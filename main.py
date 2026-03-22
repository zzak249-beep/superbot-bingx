#!/usr/bin/env python3
"""
BOT FUSION v1.0.0 — Trend Magic + RMI Trend Sniper + EMA
══════════════════════════════════════════════════════════
ESTRATEGIA COMBINADA:
  Señal LONG: Trend Magic alcista + RMI cruza arriba de 66 + EMA9 subiendo
  Señal SHORT: Trend Magic bajista + RMI cruza abajo de 30 + EMA9 bajando

INDICADORES:
  1. Trend Magic (CCI 20 + ATR 5 x1.0) — detecta tendencia principal
  2. RMI Trend Sniper (RSI + MFI promedio) — detecta momentum
  3. EMA 9 — confirmación de dirección
  4. EMA 21/50 — filtro de tendencia adicional
  5. Bollinger Bands — detecta sobreextensión
  6. Volumen — confirma la señal

GESTIÓN DE RIESGO:
  - Balance check antes de cada trade
  - Leverage configurable por .env
  - TP/SL automáticos con hasta 3 intentos
  - Trailing stop (protege 60% de ganancia)
  - Cooldown 15min por par
  - Filtro BTC tendencia
  - Reconciliación al arrancar
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re
from datetime import datetime, timedelta
from urllib.parse import urlencode

# ============================================================================
# CONFIGURACION
# ============================================================================

def clean(key, default, typ='str'):
    v = os.getenv(key, str(default))
    v = v.strip().strip('"').strip("'").strip()
    if typ in ('int', 'float'):
        v = v.replace(',', '.')
        m = re.match(r'^-?\d+\.?\d*', v)
        v = m.group(0) if m else str(default)
    if typ == 'int':   return int(float(v))
    if typ == 'float': return float(v)
    if typ == 'bool':  return v.lower() == 'true'
    return v

BINGX_API_KEY    = os.getenv('BINGX_API_KEY',    '').strip().strip('"').strip("'")
BINGX_API_SECRET = os.getenv('BINGX_API_SECRET', '').strip().strip('"').strip("'")
TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT    = os.getenv('TELEGRAM_CHAT_ID',   '')

AUTO_TRADING     = clean('AUTO_TRADING_ENABLED',   'true',  'bool')
POSITION_SIZE    = clean('MAX_POSITION_SIZE',        '7',   'float')
MIN_TRADE        = clean('MIN_TRADE_USDT',           '5',   'float')
LEVERAGE         = clean('LEVERAGE',                 '3',   'int')
TP_PCT           = clean('TAKE_PROFIT_PCT',          '2.0', 'float')
SL_PCT           = clean('STOP_LOSS_PCT',            '1.0', 'float')
MAX_TRADES       = clean('MAX_OPEN_TRADES',          '3',   'int')
INTERVAL         = clean('CHECK_INTERVAL',          '120',  'int')
MIN_VOLUME       = clean('MIN_VOLUME_24H',       '500000',  'float')
MAX_SYMBOLS      = clean('MAX_SYMBOLS_TO_ANALYZE',   '60',  'int')
MIN_SCORE        = clean('MIN_SCORE',                '70',  'float')
TRAILING         = clean('TRAILING_STOP_ENABLED',  'true',  'bool')
USE_LIMIT_ORDERS = clean('USE_LIMIT_ORDERS',       'true',  'bool')
ENABLE_LONGS     = clean('ENABLE_LONGS',           'true',  'bool')
ENABLE_SHORTS    = clean('ENABLE_SHORTS',          'true',  'bool')
BTC_FILTER_PCT   = clean('BTC_FILTER_PCT',          '2.0',  'float')

# Parámetros Trend Magic
CCI_LEN    = clean('CCI_LENGTH',    '20',  'int')
ATR_LEN    = clean('ATR_LENGTH',     '5',  'int')
ATR_MULT   = clean('ATR_MULTIPLIER','1.0', 'float')

# Parámetros RMI
RMI_LEN    = clean('RMI_LENGTH',    '14',  'int')
RMI_POS    = clean('RMI_POSITIVE',  '66',  'float')
RMI_NEG    = clean('RMI_NEGATIVE',  '30',  'float')

LIMIT_OFFSET_PCT = 0.05
SKIP_HOURS_UTC   = {0, 1}
BASE_URL         = "https://open-api.bingx.com"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

COMISION_MAKER  = 0.0002
COMISION_TAKER  = 0.0005
COMISION_ACTUAL = COMISION_MAKER if USE_LIMIT_ORDERS else COMISION_TAKER
TP_MIN_RENTABLE = round((COMISION_ACTUAL * 2 / LEVERAGE + 0.002) * 100, 3)

# ============================================================================
# API BINGX
# ============================================================================

def bingx_request(method, endpoint, params, retries=2):
    for attempt in range(retries + 1):
        try:
            p = dict(params)
            p['timestamp'] = int(time.time() * 1000)
            qs  = urlencode(sorted(p.items()))
            sig = hmac.new(BINGX_API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
            url = f"{BASE_URL}{endpoint}?{qs}&signature={sig}"
            hdr = {'X-BX-APIKEY': BINGX_API_KEY,
                   'Content-Type': 'application/x-www-form-urlencoded'}
            r = requests.get(url, headers=hdr, timeout=12) if method == 'GET' \
                else requests.post(url, headers=hdr, timeout=12)
            return r
        except Exception as e:
            if attempt < retries:
                log.warning(f"  retry {attempt+1}: {e}"); time.sleep(1.5)
            else:
                raise

# ============================================================================
# INDICADORES TÉCNICOS
# ============================================================================

def calc_sma(prices, period):
    if not prices or len(prices) < period: return sum(prices)/len(prices) if prices else 0
    return sum(prices[-period:]) / period

def calc_ema(prices, period):
    if not prices: return 0
    if len(prices) < period: return sum(prices) / len(prices)
    k, e = 2 / (period + 1), prices[0]
    for p in prices[1:]: e = p * k + e * (1 - k)
    return e

def calc_rma(values, period):
    """RMA = Wilder's Moving Average (usado en RSI/ATR)"""
    if not values: return 0
    if len(values) < period: return sum(values) / len(values)
    alpha = 1.0 / period
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1 - alpha) * result
    return result

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < 2: return 0
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, len(closes))]
    if len(trs) < period: return sum(trs)/len(trs) if trs else 0
    return calc_rma(trs, period)

def calc_cci(highs, lows, closes, period=20):
    """CCI = (Typical Price - SMA(TP)) / (0.015 * MeanDeviation)"""
    if len(closes) < period: return 0
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    tp_slice = tp[-period:]
    sma_tp = sum(tp_slice) / period
    mean_dev = sum(abs(t - sma_tp) for t in tp_slice) / period
    if mean_dev == 0: return 0
    return (tp[-1] - sma_tp) / (0.015 * mean_dev)

def calc_trend_magic(highs, lows, closes, cci_len=20, atr_len=5, mult=1.0):
    """
    Trend Magic: usa CCI + ATR para crear un trailing stop dinámico.
    Retorna: (trend_value, direction) donde direction=1 bullish, -1 bearish
    """
    if len(closes) < max(cci_len, atr_len) + 5:
        return closes[-1] if closes else 0, 0

    # Calcular ATR suavizado
    n = len(closes)
    atr_vals = []
    for i in range(1, n):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        atr_vals.append(tr)
    
    # SMA del ATR (como en Pine: ta.sma(ta.tr, atr_len))
    atr_sma = calc_sma(atr_vals, atr_len)
    
    # Calcular CCI para cada barra de los últimos cci_len+5 puntos
    window = min(cci_len + 10, n)
    cci_series = []
    for i in range(n - window, n):
        start = max(0, i - cci_len + 1)
        h_sl = highs[start:i+1]
        l_sl = lows[start:i+1]
        c_sl = closes[start:i+1]
        tp = [(h_sl[j]+l_sl[j]+c_sl[j])/3 for j in range(len(c_sl))]
        sma_tp = sum(tp)/len(tp)
        md = sum(abs(t-sma_tp) for t in tp)/len(tp)
        cci = (tp[-1]-sma_tp)/(0.015*md) if md > 0 else 0
        cci_series.append(cci)

    # Simular buffers de Trend Magic
    buf_up = lows[-1] - mult * atr_sma
    buf_dn = highs[-1] + mult * atr_sma

    # Determinar dirección según CCI actual vs anterior
    cci_now  = cci_series[-1] if cci_series else 0
    cci_prev = cci_series[-2] if len(cci_series) > 1 else 0

    if cci_now >= 0:
        direction = 1   # bullish
        trend_val = buf_up
    else:
        direction = -1  # bearish
        trend_val = buf_dn

    return trend_val, direction

def calc_rmi(closes, period=14):
    """
    RMI usando RMA de gains/losses (similar al RSI de Pine con RMA).
    """
    if len(closes) < period + 1: return 50.0
    changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(0, c) for c in changes]
    losses = [max(0, -c) for c in changes]
    up   = calc_rma(gains, period)
    down = calc_rma(losses, period)
    if down == 0: return 100.0
    if up == 0:   return 0.0
    return 100 - (100 / (1 + up / down))

def calc_mfi(highs, lows, closes, volumes, period=14):
    """Money Flow Index"""
    if len(closes) < period + 1: return 50.0
    hlc3 = [(highs[i]+lows[i]+closes[i])/3 for i in range(len(closes))]
    pos_flow, neg_flow = [], []
    for i in range(1, len(hlc3)):
        mf = hlc3[i] * volumes[i]
        if hlc3[i] > hlc3[i-1]:
            pos_flow.append(mf); neg_flow.append(0)
        elif hlc3[i] < hlc3[i-1]:
            pos_flow.append(0);  neg_flow.append(mf)
        else:
            pos_flow.append(0);  neg_flow.append(0)
    pf = sum(pos_flow[-period:])
    nf = sum(neg_flow[-period:])
    if nf == 0: return 100.0
    if pf == 0: return 0.0
    return 100 - (100 / (1 + pf / nf))

def calc_rmi_sniper(highs, lows, closes, volumes, period=14, pos_thresh=66, neg_thresh=30):
    """
    RMI Trend Sniper: combina RSI + MFI y detecta cruces.
    Retorna: (rmi_now, rmi_prev, signal)
    signal: 'BUY', 'SELL' o None
    """
    if len(closes) < period + 5:
        return 50.0, 50.0, None

    rsi_now  = calc_rmi(closes, period)
    rsi_prev = calc_rmi(closes[:-1], period)
    mfi_now  = calc_mfi(highs, lows, closes, volumes, period)
    mfi_prev = calc_mfi(highs[:-1], lows[:-1], closes[:-1], volumes[:-1], period)

    rmi_now  = (rsi_now  + mfi_now)  / 2
    rmi_prev = (rsi_prev + mfi_prev) / 2

    ema5_now  = calc_ema(closes, 5)
    ema5_prev = calc_ema(closes[:-1], 5)
    ema5_rising = ema5_now > ema5_prev

    signal = None
    # BUY: rmi cruza arriba de pos_thresh y EMA5 subiendo
    if rmi_prev < pos_thresh and rmi_now > pos_thresh and rmi_now > neg_thresh and ema5_rising:
        signal = 'BUY'
    # SELL: rmi cae debajo de neg_thresh y EMA5 bajando
    elif rmi_now < neg_thresh and not ema5_rising:
        signal = 'SELL'

    return rmi_now, rmi_prev, signal

def calc_bollinger(prices, period=20):
    if len(prices) < period:
        m = sum(prices)/len(prices); return m, m, m
    w = prices[-period:]
    mid = sum(w)/period
    std = (sum((p-mid)**2 for p in w)/period)**0.5
    return mid+2*std, mid, mid-2*std

def vol_spike(volumes):
    if len(volumes) < 5: return 1.0
    avg = sum(volumes[:-1])/len(volumes[:-1])
    return (volumes[-1]/avg) if avg > 0 else 1.0

# ============================================================================
# BOT FUSION
# ============================================================================

class FusionBot:

    def __init__(self):
        dirs = []
        if ENABLE_LONGS:  dirs.append("LONGS")
        if ENABLE_SHORTS: dirs.append("SHORTS")
        fee_lbl = f"LÍMITE maker {COMISION_MAKER*100:.2f}%" if USE_LIMIT_ORDERS \
                  else f"MERCADO taker {COMISION_TAKER*100:.2f}%"

        log.info("=" * 65)
        log.info("  BOT FUSION v1.0.0")
        log.info("  Trend Magic + RMI Sniper + EMA + BB + Volume")
        log.info("=" * 65)
        log.info(f"  Modo:      {'AUTO' if AUTO_TRADING else 'SEÑALES'}")
        log.info(f"  Capital:   ${POSITION_SIZE} USDT | Leverage: {LEVERAGE}x")
        log.info(f"  TP/SL:     {TP_PCT}% / {SL_PCT}%  RR:{TP_PCT/SL_PCT:.1f}:1")
        log.info(f"  TP mín:    {TP_MIN_RENTABLE}%")
        log.info(f"  Fee:       {fee_lbl}")
        log.info(f"  Dirs:      {' + '.join(dirs)}")
        log.info(f"  Score mín: {MIN_SCORE}")
        log.info(f"  CCI:{CCI_LEN} ATR:{ATR_LEN}x{ATR_MULT} | RMI:{RMI_LEN} +{RMI_POS}/-{RMI_NEG}")
        log.info("=" * 65)

        self.symbols        = []
        self.open_trades    = {}
        self._contracts     = {}
        self._cooldowns     = {}
        self._last_report   = datetime.now()
        self._btc_change_1h = 0.0
        self._balance       = 0.0
        self.stats = {'exec':0,'closed':0,'wins':0,'losses':0,'pnl':0.0}

        self._verify()
        self._load_contracts()
        self._get_symbols()
        self._reconciliar_posiciones()

        self._tg(
            f"<b>🤖 Bot FUSION v1.0.0 iniciado</b>\n"
            f"Trend Magic + RMI Sniper + EMA\n"
            f"Capital: ${POSITION_SIZE} x{LEVERAGE} | TP:{TP_PCT}% SL:{SL_PCT}%\n"
            f"Fee: {fee_lbl} | Score≥{MIN_SCORE}\n"
            f"Dirs: {' + '.join(dirs)} | Balance: ${self._balance:.2f} USDT"
        )

    # ---------------------------------------------------------------- setup

    def _extraer_balance(self, d):
        try:
            data = d.get('data', {})
            if isinstance(data, list):
                data = data[0] if data else {}
            bal = data.get('balance', None)
            if isinstance(bal, dict):
                for key in ['equity','balance','availableMargin','availableBalance','walletBalance']:
                    v = bal.get(key, None)
                    if v is not None:
                        return float(str(v) or 0)
            for key in ['equity','balance','availableMargin','availableBalance','walletBalance']:
                v = data.get(key, None)
                if v is not None and not isinstance(v, dict):
                    try: return float(str(v) or 0)
                    except: continue
            def buscar(obj, depth=0):
                if depth > 3: return None
                if isinstance(obj, (int, float)): return float(obj)
                if isinstance(obj, str):
                    try: return float(obj)
                    except: return None
                if isinstance(obj, dict):
                    for k in ['equity','balance','availableMargin','availableBalance']:
                        if k in obj:
                            r = buscar(obj[k], depth+1)
                            if r is not None and r > 0: return r
                if isinstance(obj, list) and obj:
                    return buscar(obj[0], depth+1)
                return None
            result = buscar(data)
            return result if result is not None else 0.0
        except Exception as e:
            log.error(f"  Error balance: {e}"); return 0.0

    def _verify(self):
        global AUTO_TRADING
        if not AUTO_TRADING: return
        if not BINGX_API_KEY or not BINGX_API_SECRET:
            log.error("  API keys vacías"); AUTO_TRADING = False; return
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/balance', {}).json()
            if d.get('code') == 0:
                eq = self._extraer_balance(d)
                self._balance = eq
                log.info(f"BingX OK | Balance: ${eq:.2f} USDT")
                if eq < MIN_TRADE:
                    log.warning(f"  Balance bajo ${eq:.2f} — continuando")
            else:
                log.error(f"BingX [{d.get('code')}]: {d.get('msg')}"); AUTO_TRADING = False
        except Exception as e:
            log.error(f"Error API: {e}"); AUTO_TRADING = False

    def _update_balance(self):
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/balance', {}).json()
            if d.get('code') == 0:
                eq = self._extraer_balance(d)
                self._balance = eq
                return eq
        except: pass
        return self._balance

    def _balance_suficiente(self):
        bal = self._update_balance()
        needed = POSITION_SIZE / LEVERAGE
        if bal < needed:
            log.warning(f"  Balance ${bal:.2f} < margen ${needed:.2f} — skip")
            return False
        return True

    def _set_leverage(self, symbol, direction):
        try:
            for side in (['LONG'] if direction == 'LONG' else ['SHORT']):
                bingx_request('POST', '/openApi/swap/v2/trade/leverage', {
                    'symbol': symbol, 'side': side, 'leverage': str(LEVERAGE),
                })
            log.info(f"  Leverage {LEVERAGE}x configurado para {symbol} {direction}")
        except Exception as e:
            log.debug(f"  _set_leverage: {e}")

    def _load_contracts(self):
        try:
            d = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/contracts", timeout=15).json()
            if d.get('code') == 0:
                for c in d.get('data', []):
                    self._contracts[c.get('symbol','')] = {
                        'step':  float(c.get('tradeMinQuantity', 1)),
                        'prec':  int(c.get('quantityPrecision', 2)),
                        'ctval': float(c.get('contractSize', 1)),
                    }
                log.info(f"Contratos: {len(self._contracts)}")
        except Exception as e:
            log.warning(f"Error contratos: {e}")

    def _get_symbols(self):
        NO_CRIPTO = [
            'DOW','JONES','SP500','SPX','SPY','QQQ','NASDAQ','RUSSELL',
            'DAX','FTSE','CAC','NIKKEI','HANG','BOVESPA','IBEX',
            'US30','NAS100','US500','DJI','INDEX',
            'GOLD','SILVER','XAU','XAG','PAXG','XAUT',
            'OIL','BRENT','WTI','CRUDE','GAS','GASOLINE',
            'PLATINUM','PALLADIUM','COPPER','NICKEL','ZINC','IRON',
            'TSLA','AAPL','MSFT','GOOGL','AMZN','META','NVDA','COIN','MSTR',
            'EUR','GBP','JPY','CHF','AUD','CAD','NZD',
            'WHEAT','CORN','SUGAR','COFFEE','COTTON','LUMBER','SOYBEAN',
        ]
        try:
            d = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker", timeout=15).json()
            if d.get('code') == 0:
                items, excl = [], []
                for t in d.get('data', []):
                    sym = t.get('symbol','')
                    if not sym.endswith('-USDT'): continue
                    base = sym.replace('-USDT','').upper()
                    if any(kw in base for kw in NO_CRIPTO): excl.append(base); continue
                    try:
                        price = float(t.get('lastPrice',0))
                        vol   = float(t.get('volume',0)) * price
                        if vol < MIN_VOLUME or price < 0.000001: continue
                        items.append({'symbol':sym,'vol':vol})
                    except: continue
                items.sort(key=lambda x: x['vol'], reverse=True)
                self.symbols = [x['symbol'] for x in items[:MAX_SYMBOLS]]
                log.info(f"Pares: {len(self.symbols)} | Excluidos: {len(excl)}")
                return
        except Exception as e:
            log.warning(f"Error símbolos: {e}")
        self.symbols = ['BTC-USDT','ETH-USDT','SOL-USDT','BNB-USDT','XRP-USDT']

    def _reconciliar_posiciones(self):
        if not AUTO_TRADING: return
        log.info("  🔍 Reconciliando posiciones en BingX...")
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/positions', {}).json()
            if d.get('code') != 0: return
            recuperadas = 0
            for p in (d.get('data') or []):
                try: amt = float(p.get('positionAmt', 0) or 0)
                except: continue
                if abs(amt) == 0: continue
                sym = p.get('symbol', '')
                if not sym: continue
                try: lev = int(float(p.get('leverage', 0) or 0))
                except: lev = 0
                if lev != 0 and lev != LEVERAGE:
                    log.info(f"  ⏭ {sym} ignorado — leverage {lev}x ≠ {LEVERAGE}x"); continue
                try:
                    entry = float(p.get('avgPrice') or p.get('entryPrice') or 0)
                except: entry = 0
                if entry <= 0:
                    tk = self._ticker(sym); entry = tk['price'] if tk else 0
                if entry <= 0: continue
                direction = 'LONG' if amt > 0 else 'SHORT'
                qty_c = abs(amt)
                tp_price = entry*(1+TP_PCT/100) if direction=='LONG' else entry*(1-TP_PCT/100)
                sl_price = entry*(1-SL_PCT/100) if direction=='LONG' else entry*(1+SL_PCT/100)
                tp_ok = self._cond_order(sym, direction, qty_c, tp_price, 'TAKE_PROFIT_MARKET')
                time.sleep(0.3)
                sl_ok = self._cond_order(sym, direction, qty_c, sl_price, 'STOP_MARKET')
                self.open_trades[sym] = {
                    'direction':direction,'entry':entry,'qty_c':qty_c,'usdt_qty':POSITION_SIZE,
                    'tp':tp_price,'sl':sl_price,'tp_pct':TP_PCT,'sl_pct':SL_PCT,
                    'highest':entry,'lowest':entry,'order_id':'RECONCILIADO',
                    'tp_ok':tp_ok,'sl_ok':sl_ok,'opened_at':datetime.now(),'score':0,
                }
                recuperadas += 1
                emoji = "📈" if direction == 'LONG' else "📉"
                log.info(f"  {emoji} {sym} {direction} reconciliado | entry=${entry:.6f} qty={qty_c}")
            log.info(f"  ✅ Reconciliación: {recuperadas} posiciones")
        except Exception as e:
            log.error(f"  Error reconciliación: {e}")

    # ---------------------------------------------------------------- datos

    def _klines(self, symbol, interval='15m', limit=100):
        try:
            d = requests.get(f"{BASE_URL}/openApi/swap/v3/quote/klines",
                params={'symbol':symbol,'interval':interval,'limit':limit}, timeout=12).json()
            if d.get('code') == 0 and d.get('data'):
                k = d['data']
                return ([float(x['close'])  for x in k],
                        [float(x['high'])   for x in k],
                        [float(x['low'])    for x in k],
                        [float(x['volume']) for x in k],
                        [float(x['open'])   for x in k])
        except: pass
        return None, None, None, None, None

    def _ticker(self, symbol):
        try:
            d = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker",
                             params={'symbol':symbol}, timeout=8).json()
            if d.get('code') == 0 and d.get('data'):
                t = d['data']
                return {'price': float(t.get('lastPrice',0)),
                        'change': float(t.get('priceChangePercent',0))}
        except: pass
        return None

    def _update_btc_trend(self):
        try:
            closes, *_ = self._klines('BTC-USDT', '1h', 3)
            if closes and len(closes) >= 2:
                self._btc_change_1h = (closes[-1]-closes[-2])/closes[-2]*100
        except: pass

    # ---------------------------------------------------------------- sizing

    def _qty_contratos(self, symbol, price, usdt_amount=None):
        if usdt_amount is None: usdt_amount = POSITION_SIZE
        info  = self._contracts.get(symbol, {'step':1.0,'prec':2,'ctval':1.0})
        step  = max(info['step'], 0.0001)
        prec  = info['prec']
        ctval = info.get('ctval', 1.0)
        ppc   = price * ctval if ctval != 1.0 else price
        if ppc <= 0: return None, 0
        qty = round(math.ceil(usdt_amount / ppc / step) * step, prec)
        val = qty * ppc
        i = 0
        while val < MIN_TRADE and i < 500:
            qty += step; qty = round(qty, prec); val = qty * ppc; i += 1
        if val > usdt_amount * 1.3:
            qty = round(math.floor((usdt_amount * 1.3 / ppc) / step) * step, prec)
            val = qty * ppc
        log.info(f"    qty: {qty} × ${ppc:.6f} = ${val:.2f} USDT")
        return qty, round(val, 4)

    # ---------------------------------------------------------------- filtros

    def _cooldown_ok(self, symbol):
        ts = self._cooldowns.get(symbol)
        return not (ts and (time.time() - ts) < 15 * 60)

    def _hora_ok(self):
        return datetime.utcnow().hour not in SKIP_HOURS_UTC

    # ---------------------------------------------------------------- ANÁLISIS FUSION

    def analyze(self, symbol):
        if symbol in self.open_trades: return None
        if not self._cooldown_ok(symbol): return None
        if not self._hora_ok(): return None

        # Usar velas de 15m para mejor señal con Trend Magic
        closes, highs, lows, volumes, opens = self._klines(symbol, '15m', 100)
        if not closes or len(closes) < 30: return None

        ticker = self._ticker(symbol)
        if not ticker or ticker['price'] <= 0: return None

        price  = ticker['price']
        change = ticker['change']

        # ── INDICADOR 1: Trend Magic ──────────────────────────────────
        tm_val, tm_dir = calc_trend_magic(highs, lows, closes, CCI_LEN, ATR_LEN, ATR_MULT)
        trend_bullish = tm_dir > 0
        trend_bearish = tm_dir < 0

        # ── INDICADOR 2: RMI Trend Sniper ─────────────────────────────
        rmi_now, rmi_prev, rmi_signal = calc_rmi_sniper(
            highs, lows, closes, volumes, RMI_LEN, RMI_POS, RMI_NEG)

        # ── INDICADOR 3: EMAs ─────────────────────────────────────────
        ema9  = calc_ema(closes, 9)
        ema21 = calc_ema(closes, 21)
        ema50 = calc_ema(closes, min(50, len(closes)))
        ema9_prev = calc_ema(closes[:-1], 9)
        ema9_rising  = ema9 > ema9_prev
        ema9_falling = ema9 < ema9_prev
        ema_bull = ema9 > ema21 > ema50
        ema_bear = ema9 < ema21 < ema50

        # ── INDICADOR 4: Bollinger Bands ──────────────────────────────
        bb_u, bb_m, bb_l = calc_bollinger(closes, 20)
        bb_pos = (price - bb_l) / (bb_u - bb_l) if (bb_u - bb_l) > 0 else 0.5

        # ── INDICADOR 5: Volumen ──────────────────────────────────────
        vs = vol_spike(volumes)

        # ── ATR para TP dinámico ──────────────────────────────────────
        atr     = calc_atr(highs, lows, closes, 14)
        atr_pct = (atr / price * 100) if price > 0 else 0

        # ── Tendencia corta ───────────────────────────────────────────
        trend_5  = (closes[-1]-closes[-6])/closes[-6]*100  if len(closes) >= 6  else 0
        trend_10 = (closes[-1]-closes[-11])/closes[-11]*100 if len(closes) >= 11 else 0

        # ── TP dinámico ───────────────────────────────────────────────
        tp_dyn = max(TP_PCT, TP_MIN_RENTABLE, min(TP_PCT*2.5, atr_pct*2.5))

        # ====================================================================
        # SEÑAL LONG
        # ====================================================================
        long_score, long_reasons = 0, []

        if ENABLE_LONGS and self._btc_change_1h > -BTC_FILTER_PCT:

            # Trend Magic bullish — condición principal
            if trend_bullish:
                long_score += 35; long_reasons.append("TrendMagic_BULL(35)")
            else:
                long_score -= 20; long_reasons.append("TrendMagic_BEAR(-20)")

            # RMI Sniper señal BUY
            if rmi_signal == 'BUY':
                long_score += 40; long_reasons.append(f"RMI_BUY({rmi_now:.0f})(40)")
            elif rmi_now > RMI_POS:
                long_score += 15; long_reasons.append(f"RMI_alto({rmi_now:.0f})(15)")
            elif rmi_now < RMI_NEG:
                long_score -= 15; long_reasons.append(f"RMI_bajo(-15)")

            # EMA confirmación
            if ema_bull and ema9_rising:
                long_score += 20; long_reasons.append("EMA_BULL+(20)")
            elif ema_bull:
                long_score += 10; long_reasons.append("EMA_BULL(10)")
            elif ema_bear:
                long_score -= 15; long_reasons.append("EMA_BEAR(-15)")

            # Bollinger
            if bb_pos <= 0.10:
                long_score += 15; long_reasons.append("BB_bot(15)")
            elif bb_pos <= 0.25:
                long_score += 8;  long_reasons.append("BB_low(8)")
            elif bb_pos >= 0.85:
                long_score -= 10; long_reasons.append("BB_high(-10)")

            # Volumen
            if vs >= 1.8:
                p = min(15, int(vs*7)); long_score += p; long_reasons.append(f"Vol{vs:.1f}x({p})")
            elif vs < 1.2:
                long_score -= 8; long_reasons.append("VolBajo(-8)")

            # Tendencia corta
            if trend_5 > 1.0:  long_score += 10; long_reasons.append("Impulso+(10)")
            if trend_5 < -1.5: long_score -= 10; long_reasons.append("Impulso-(-10)")

            # ATR suficiente
            if atr_pct > 0.5:  long_score += 8;  long_reasons.append(f"ATR{atr_pct:.1f}%(8)")
            elif atr_pct < 0.2: long_score -= 8; long_reasons.append("ATRbajo(-8)")

        # ====================================================================
        # SEÑAL SHORT
        # ====================================================================
        short_score, short_reasons = 0, []

        if ENABLE_SHORTS and self._btc_change_1h < BTC_FILTER_PCT:

            # Trend Magic bearish — condición principal
            if trend_bearish:
                short_score += 35; short_reasons.append("TrendMagic_BEAR(35)")
            else:
                short_score -= 20; short_reasons.append("TrendMagic_BULL(-20)")

            # RMI Sniper señal SELL
            if rmi_signal == 'SELL':
                short_score += 40; short_reasons.append(f"RMI_SELL({rmi_now:.0f})(40)")
            elif rmi_now < RMI_NEG:
                short_score += 15; short_reasons.append(f"RMI_bajo({rmi_now:.0f})(15)")
            elif rmi_now > RMI_POS:
                short_score -= 15; short_reasons.append(f"RMI_alto(-15)")

            # EMA confirmación
            if ema_bear and ema9_falling:
                short_score += 20; short_reasons.append("EMA_BEAR+(20)")
            elif ema_bear:
                short_score += 10; short_reasons.append("EMA_BEAR(10)")
            elif ema_bull:
                short_score -= 15; short_reasons.append("EMA_BULL(-15)")

            # Bollinger
            if bb_pos >= 0.90:
                short_score += 15; short_reasons.append("BB_top(15)")
            elif bb_pos >= 0.75:
                short_score += 8;  short_reasons.append("BB_high(8)")
            elif bb_pos <= 0.15:
                short_score -= 10; short_reasons.append("BB_low(-10)")

            # Volumen
            if vs >= 1.8:
                p = min(15, int(vs*7)); short_score += p; short_reasons.append(f"Vol{vs:.1f}x({p})")
            elif vs < 1.2:
                short_score -= 8; short_reasons.append("VolBajo(-8)")

            # Tendencia corta
            if trend_5 < -1.0:  short_score += 10; short_reasons.append("Impulso-(10)")
            if trend_5 > 1.5:   short_score -= 10; short_reasons.append("Impulso+(-10)")

            # ATR suficiente
            if atr_pct > 0.5:  short_score += 8;  short_reasons.append(f"ATR{atr_pct:.1f}%(8)")
            elif atr_pct < 0.2: short_score -= 8; short_reasons.append("ATRbajo(-8)")

        # ── Decidir señal ganadora ────────────────────────────────────
        base = {
            'price':price, 'change':change, 'rmi':rmi_now,
            'tp_pct':tp_dyn, 'sl_pct':SL_PCT,
            'tm_dir':tm_dir, 'bb_pos':round(bb_pos*100,1), 'atr_pct':round(atr_pct,2),
        }

        if long_score >= MIN_SCORE and long_score > short_score:
            return {**base, 'signal':'LONG', 'score':long_score, 'reasons':' | '.join(long_reasons)}

        if short_score >= MIN_SCORE and short_score > long_score:
            return {**base, 'signal':'SHORT', 'score':short_score, 'reasons':' | '.join(short_reasons)}

        return None

    # ---------------------------------------------------------------- órdenes

    def _place_entry(self, symbol, direction, usdt_qty, price):
        qty_c, val = self._qty_contratos(symbol, price, usdt_qty)
        if not qty_c:
            log.error(f"  No se pudo calcular qty_c para {symbol}"); return None, None

        side = 'BUY' if direction == 'LONG' else 'SELL'
        log.info(f"  Abriendo {direction} {symbol}: {qty_c} contratos = ${val:.2f} USDT")

        if USE_LIMIT_ORDERS:
            offset = (1 - LIMIT_OFFSET_PCT/100) if direction == 'LONG' else (1 + LIMIT_OFFSET_PCT/100)
            limit_price = round(price * offset, 8)
            d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol, 'side':side, 'positionSide':direction,
                'type':'LIMIT', 'price':str(limit_price),
                'quantity':str(qty_c), 'timeInForce':'GTC',
            }).json()
            if d.get('code') == 0:
                log.info(f"  ENTRADA LÍMITE OK {qty_c} contratos @ ${limit_price:.6f} maker")
                return d.get('data',{}).get('orderId','OK'), qty_c
            if 'margin' in str(d.get('msg','')).lower():
                log.error(f"  Insufficient margin — abortando"); return None, None
            log.warning(f"  Límite falló [{d.get('code')}] — fallback mercado")

        # Fallback MARKET — siempre quantity en contratos
        d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol':symbol, 'side':side, 'positionSide':direction,
            'type':'MARKET', 'quantity':str(qty_c),
        }).json()
        if d.get('code') == 0:
            log.info(f"  ENTRADA MERCADO OK {qty_c} contratos taker")
            return d.get('data',{}).get('orderId','OK'), qty_c
        if 'margin' in str(d.get('msg','')).lower():
            log.error(f"  Insufficient margin — abortando"); return None, None
        log.error(f"  Entrada fallida [{d.get('code')}]: {d.get('msg')}")
        return None, None

    def _cond_order(self, symbol, direction, qty_c, stop_price, otype):
        if not qty_c or qty_c <= 0: return False
        try:
            is_tp = "TAKE" in otype
            lbl   = "TP" if is_tp else "SL"
            close_side = 'SELL' if direction == 'LONG' else 'BUY'

            if is_tp:
                params = {
                    'symbol':symbol, 'side':close_side, 'positionSide':direction,
                    'type':'TAKE_PROFIT', 'quantity':str(qty_c),
                    'price':str(round(stop_price,8)),
                    'stopPrice':str(round(stop_price,8)), 'timeInForce':'GTC',
                }
            else:
                params = {
                    'symbol':symbol, 'side':close_side, 'positionSide':direction,
                    'type':'STOP_MARKET', 'quantity':str(qty_c),
                    'stopPrice':str(round(stop_price,8)),
                }
            d  = bingx_request('POST', '/openApi/swap/v2/trade/order', params).json()
            ok = d.get('code') == 0
            if ok:
                log.info(f"  {lbl} ✅ @ ${stop_price:.6f} qty={qty_c}")
            else:
                if is_tp:
                    log.warning(f"  TP límite rechazado — fallback mercado")
                    p2 = {'symbol':symbol,'side':close_side,'positionSide':direction,
                          'type':'TAKE_PROFIT_MARKET','quantity':str(qty_c),
                          'stopPrice':str(round(stop_price,8))}
                    d2 = bingx_request('POST', '/openApi/swap/v2/trade/order', p2).json()
                    ok = d2.get('code') == 0
                    if ok:  log.info(f"  TP ✅ fallback @ ${stop_price:.6f}")
                    else:   log.error(f"  TP ❌ [{d2.get('code')}]: {d2.get('msg')}")
                else:
                    log.error(f"  {lbl} ❌ [{d.get('code')}]: {d.get('msg')}")
            return ok
        except Exception as e:
            log.error(f"  {otype} excepción: {e}"); return False

    def _close_position(self, symbol, direction, t):
        qty_c = t.get('qty_c', 0)
        close_side = 'SELL' if direction == 'LONG' else 'BUY'
        if qty_c and qty_c > 0:
            params = {'symbol':symbol,'side':close_side,'positionSide':direction,
                      'type':'MARKET','quantity':str(qty_c),'reduceOnly':'true'}
        else:
            usdt = t.get('usdt_qty', POSITION_SIZE)
            params = {'symbol':symbol,'side':close_side,'positionSide':direction,
                      'type':'MARKET','quoteOrderQty':str(round(usdt,2)),'reduceOnly':'true'}
        return bingx_request('POST', '/openApi/swap/v2/trade/order', params).json().get('code') == 0

    def _tiene_posicion(self, symbol):
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/positions', {'symbol':symbol}).json()
            if d.get('code') == 0:
                for p in (d.get('data') or []):
                    amt = float(p.get('positionAmt',0) or 0)
                    if abs(amt) > 0:
                        return True, 'LONG' if amt > 0 else 'SHORT'
        except: pass
        return False, None

    def _esperar_posicion(self, symbol, direction, timeout=45):
        log.info(f"  Esperando posición {direction} {symbol} (max {timeout}s)...")
        for i in range(timeout):
            try:
                d = bingx_request('GET', '/openApi/swap/v2/user/positions',
                                  {'symbol': symbol}).json()
                if d.get('code') == 0:
                    data = d.get('data') or []
                    if i < 3: log.info(f"  [debug {i+1}s] {str(data)[:150]}")
                    for p in data:
                        try: amt = float(p.get('positionAmt', 0) or 0)
                        except: amt = 0
                        pos_side = str(p.get('positionSide','')).upper()
                        if direction == 'LONG':
                            ok = (amt > 0) or (pos_side == 'LONG' and abs(amt) > 0)
                        else:
                            ok = (amt < 0) or (pos_side == 'SHORT' and abs(amt) > 0)
                        if ok:
                            try:
                                entry_real = float(p.get('avgPrice') or
                                                   p.get('entryPrice') or
                                                   p.get('averagePrice') or 0)
                            except: entry_real = 0
                            qty_real = abs(amt)
                            log.info(f"  ✅ {direction} confirmado: qty={qty_real} entry=${entry_real:.6f} ({i+1}s)")
                            return qty_real, entry_real
            except Exception as e:
                log.debug(f"  _esperar: {e}")
            time.sleep(1)
        log.warning(f"  ⏱ Timeout {timeout}s")
        return None, None

    def _cancelar_ordenes(self, symbol):
        try:
            d = bingx_request('GET', '/openApi/swap/v2/trade/openOrders',
                              {'symbol': symbol}).json()
            if d.get('code') == 0:
                for o in (d.get('data', {}).get('orders') or []):
                    oid = o.get('orderId','')
                    if oid:
                        bingx_request('DELETE', '/openApi/swap/v2/trade/order',
                                      {'symbol': symbol, 'orderId': str(oid)})
                        log.info(f"  Orden {oid} cancelada")
        except Exception as e:
            log.debug(f"  _cancelar: {e}")

    # ---------------------------------------------------------------- lifecycle

    def open_trade(self, symbol, sig):
        if not AUTO_TRADING:
            log.info(f"  [SEÑAL] {sig['signal']} {symbol} score:{sig['score']:.0f}"); return False
        if symbol in self.open_trades: return False
        if not self._balance_suficiente(): return False

        tiene, dir_bx = self._tiene_posicion(symbol)
        if tiene: log.info(f"  {symbol} ya tiene {dir_bx} — skip"); return False

        direction = sig['signal']
        price     = sig['price']
        usdt_qty  = round(max(POSITION_SIZE, MIN_TRADE), 2)

        tp_price = price*(1+sig['tp_pct']/100) if direction=='LONG' else price*(1-sig['tp_pct']/100)
        sl_price = price*(1-sig['sl_pct']/100) if direction=='LONG' else price*(1+sig['sl_pct']/100)

        emoji = "📈" if direction == 'LONG' else "📉"

        # FIX: configurar leverage ANTES de abrir
        self._set_leverage(symbol, direction)

        log.info(f"\n  ➤ {direction} {symbol}")
        log.info(f"  Score:{sig['score']:.0f} | RMI:{sig['rmi']:.0f} | TM:{sig['tm_dir']} | Balance:${self._balance:.2f}")
        log.info(f"  {sig['reasons']}")
        log.info(f"  Entry:${price:.6f} | Capital:${usdt_qty} | TP:{sig['tp_pct']:.2f}% SL:{sig['sl_pct']:.1f}%")

        oid, qty_c = self._place_entry(symbol, direction, usdt_qty, price)
        if not oid: log.error(f"  No se pudo abrir {symbol}"); return False

        qty_real, entry_real = self._esperar_posicion(symbol, direction, timeout=45)

        if qty_real is None:
            log.warning(f"  LIMIT no ejecutada → cancelando + MARKET")
            self._cancelar_ordenes(symbol)
            time.sleep(0.5)
            side = 'BUY' if direction == 'LONG' else 'SELL'
            d_mkt = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol, 'side':side, 'positionSide':direction,
                'type':'MARKET', 'quantity':str(qty_c),
            }).json()
            if d_mkt.get('code') == 0:
                qty_real, entry_real = self._esperar_posicion(symbol, direction, timeout=20)
            if qty_real is None:
                self._tg(f"⚠️ {direction} {symbol} SIN TP/SL — no confirmado. Fijar manual.")
                self.open_trades[symbol] = {
                    'direction':direction,'entry':price,'qty_c':qty_c,'usdt_qty':usdt_qty,
                    'tp':tp_price,'sl':sl_price,'tp_pct':sig['tp_pct'],'sl_pct':sig['sl_pct'],
                    'highest':price,'lowest':price,'order_id':oid,'tp_ok':False,'sl_ok':False,
                    'opened_at':datetime.now(),'score':sig['score'],
                }
                return True

        if entry_real and entry_real > 0:
            tp_price = entry_real*(1+sig['tp_pct']/100) if direction=='LONG' else entry_real*(1-sig['tp_pct']/100)
            sl_price = entry_real*(1-sig['sl_pct']/100) if direction=='LONG' else entry_real*(1+sig['sl_pct']/100)
            log.info(f"  Entry real: ${entry_real:.6f} | TP:${tp_price:.6f} SL:${sl_price:.6f}")

        qty_final   = qty_real if qty_real else qty_c
        entry_final = entry_real if (entry_real and entry_real > 0) else price

        tp_ok = self._cond_order(symbol, direction, qty_final, tp_price, 'TAKE_PROFIT_MARKET')
        time.sleep(0.3)
        sl_ok = self._cond_order(symbol, direction, qty_final, sl_price, 'STOP_MARKET')

        for delay in [3, 5]:
            if tp_ok and sl_ok: break
            log.warning(f"  TP:{tp_ok} SL:{sl_ok} — reintentando en {delay}s")
            time.sleep(delay)
            if not tp_ok: tp_ok = self._cond_order(symbol, direction, qty_final, tp_price, 'TAKE_PROFIT_MARKET')
            if not sl_ok: sl_ok = self._cond_order(symbol, direction, qty_final, sl_price, 'STOP_MARKET')

        self.open_trades[symbol] = {
            'direction':direction,'entry':entry_final,'qty_c':qty_final,'usdt_qty':usdt_qty,
            'tp':tp_price,'sl':sl_price,'tp_pct':sig['tp_pct'],'sl_pct':sig['sl_pct'],
            'highest':entry_final,'lowest':entry_final,
            'order_id':oid,'tp_ok':tp_ok,'sl_ok':sl_ok,
            'opened_at':datetime.now(),'score':sig['score'],
        }
        self.stats['exec'] += 1

        stp = "✅" if tp_ok else "❌ FIJAR MANUAL"
        ssl = "✅" if sl_ok else "❌ FIJAR MANUAL"
        self._tg(
            f"<b>{emoji} {direction} ABIERTO — FUSION</b>\n<b>{symbol}</b> | Score:{sig['score']:.0f}\n"
            f"Entrada: ${entry_final:.6f}\n"
            f"{stp} TP: ${tp_price:.6f} ({sig['tp_pct']:.2f}%)\n"
            f"{ssl} SL: ${sl_price:.6f} ({sig['sl_pct']:.1f}%)\n"
            f"Capital: ${usdt_qty} x{LEVERAGE} | Balance: ${self._balance:.2f}\n"
            f"RMI:{sig['rmi']:.0f} | TM_dir:{sig['tm_dir']} | BB:{sig['bb_pos']}%\n"
            f"BTC 1h: {self._btc_change_1h:+.2f}%\n"
            f"{sig['reasons']}"
        )
        return True

    def close_trade(self, symbol, cur_price, reason):
        if symbol not in self.open_trades: return False
        t = self.open_trades[symbol]
        direction = t['direction']
        self._close_position(symbol, direction, t)

        if direction == 'LONG':
            cambio = (cur_price - t['entry']) / t['entry']
        else:
            cambio = (t['entry'] - cur_price) / t['entry']

        pnl     = (t['usdt_qty']*LEVERAGE*cambio) - (t['usdt_qty']*LEVERAGE*COMISION_ACTUAL*2)
        pnl_pct = (pnl / t['usdt_qty']) * 100

        self.stats['closed'] += 1
        self.stats['pnl']    += pnl
        if pnl > 0: self.stats['wins']   += 1
        else:        self.stats['losses'] += 1

        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        mins  = int((datetime.now() - t['opened_at']).total_seconds() / 60)
        emoji = "✅" if pnl > 0 else "❌"

        log.info(f"  {emoji} {reason} {symbol} {direction} PnL:${pnl:+.3f}({pnl_pct:+.1f}%) {mins}min")
        self._tg(
            f"<b>{emoji} {direction} CERRADO — {reason}</b>\n<b>{symbol}</b>\n"
            f"PnL: ${pnl:+.3f} ({pnl_pct:+.1f}%)\n"
            f"Entry: ${t['entry']:.6f} → Exit: ${cur_price:.6f}\n"
            f"Duración: {mins} min\n"
            f"<b>Total: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}% "
            f"({self.stats['wins']}W/{self.stats['losses']}L)</b>"
        )
        self._cooldowns[symbol] = time.time()
        del self.open_trades[symbol]
        return True

    # ---------------------------------------------------------------- monitor

    async def _sync_bingx(self):
        if not self.open_trades or not AUTO_TRADING: return
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/positions', {}).json()
            if d.get('code') != 0: return
            pos = {p.get('symbol'): float(p.get('positionAmt',0) or 0)
                   for p in (d.get('data') or [])
                   if abs(float(p.get('positionAmt',0) or 0)) > 0}
            for sym in list(self.open_trades.keys()):
                if sym not in pos:
                    t   = self.open_trades[sym]
                    dir = t['direction']
                    tk  = self._ticker(sym)
                    cur = tk['price'] if tk else t['entry']
                    cambio = (cur-t['entry'])/t['entry'] if dir=='LONG' else (t['entry']-cur)/t['entry']
                    pnl    = (t['usdt_qty']*LEVERAGE*cambio) - (t['usdt_qty']*LEVERAGE*COMISION_ACTUAL*2)
                    pnl_pct = (pnl/t['usdt_qty'])*100
                    self.stats['closed'] += 1; self.stats['pnl'] += pnl
                    if pnl >= 0: self.stats['wins'] += 1
                    else:        self.stats['losses'] += 1
                    total = self.stats['wins']+self.stats['losses']
                    wr = self.stats['wins']/total*100 if total else 0
                    emoji = "✅" if pnl >= 0 else "❌"
                    mins = int((datetime.now()-t['opened_at']).total_seconds()/60)
                    self._tg(f"<b>{emoji} {dir} cerrado BingX</b>\n<b>{sym}</b>\n"
                             f"PnL: ${pnl:+.3f} ({pnl_pct:+.1f}%) | {mins}min\n"
                             f"Total: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}%")
                    self._cooldowns[sym] = time.time()
                    del self.open_trades[sym]
        except Exception as e:
            log.debug(f"sync: {e}")

    async def monitor_trades(self):
        await self._sync_bingx()
        for sym in list(self.open_trades.keys()):
            try:
                t   = self.open_trades[sym]
                tk  = self._ticker(sym)
                if not tk: continue
                cur = tk['price']
                dir = t['direction']

                if dir == 'LONG':
                    pnl_pct = (cur - t['entry']) / t['entry'] * 100
                    if TRAILING and cur > t['highest']:
                        t['highest'] = cur
                        if pnl_pct >= 0.6:
                            new_sl = t['entry'] + (cur-t['entry'])*0.60
                            if new_sl > t['sl']:
                                t['sl'] = new_sl
                                log.info(f"  Trailing SL {sym}: ${new_sl:.6f}")
                    hit_tp = cur >= t['tp']
                    hit_sl = cur <= t['sl']
                else:
                    pnl_pct = (t['entry'] - cur) / t['entry'] * 100
                    if TRAILING and cur < t['lowest']:
                        t['lowest'] = cur
                        if pnl_pct >= 0.6:
                            new_sl = t['entry'] - (t['entry']-cur)*0.60
                            if new_sl < t['sl']:
                                t['sl'] = new_sl
                                log.info(f"  Trailing SL {sym}: ${new_sl:.6f}")
                    hit_tp = cur <= t['tp']
                    hit_sl = cur >= t['sl']

                if abs(pnl_pct) > 0.3:
                    log.info(f"  {sym} {dir}: {pnl_pct:+.2f}% | ${cur:.6f}")

                if hit_tp:   self.close_trade(sym, cur, "TAKE PROFIT")
                elif hit_sl: self.close_trade(sym, cur, "STOP LOSS")
            except Exception as e:
                log.debug(f"Monitor {sym}: {e}")

    def _reporte_horario(self):
        if datetime.now() - self._last_report < timedelta(hours=1): return
        self._last_report = datetime.now()
        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        self._tg(
            f"<b>📊 Reporte horario — FUSION</b>\n"
            f"PnL: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}%\n"
            f"({self.stats['wins']}W/{self.stats['losses']}L | {self.stats['closed']} trades)\n"
            f"Abiertos: {len(self.open_trades)}/{MAX_TRADES}\n"
            f"Balance: ${self._balance:.2f} USDT | BTC 1h:{self._btc_change_1h:+.2f}%"
        )

    def _tg(self, msg):
        try:
            if TELEGRAM_TOKEN and TELEGRAM_CHAT:
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={'chat_id':TELEGRAM_CHAT,'text':msg,'parse_mode':'HTML'}, timeout=6)
        except: pass

    # ---------------------------------------------------------------- loop

    async def run(self):
        log.info("\n▶  Bot FUSION v1.0.0 arrancado\n")
        iteration, last_refresh = 0, 0
        while True:
            try:
                iteration += 1
                if time.time() - last_refresh > 600:
                    self._get_symbols(); last_refresh = time.time()

                self._update_btc_trend()
                self._update_balance()

                total = self.stats['wins'] + self.stats['losses']
                wr    = self.stats['wins'] / total * 100 if total else 0
                hora_st = "🌙 BAJA" if not self._hora_ok() else "☀️"
                dirs_act = []
                if ENABLE_LONGS and self._btc_change_1h > -BTC_FILTER_PCT:  dirs_act.append("LONG✅")
                elif ENABLE_LONGS:  dirs_act.append("LONG⚠️BTC")
                if ENABLE_SHORTS and self._btc_change_1h < BTC_FILTER_PCT:  dirs_act.append("SHORT✅")
                elif ENABLE_SHORTS: dirs_act.append("SHORT⚠️BTC")

                log.info(f"\n{'='*65}")
                log.info(f"  #{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                         f"Abiertos:{len(self.open_trades)}/{MAX_TRADES} | "
                         f"PnL:${self.stats['pnl']:+.3f} | WR:{wr:.1f}%")
                log.info(f"  Balance:${self._balance:.2f} | BTC:{self._btc_change_1h:+.2f}% | "
                         f"{' '.join(dirs_act)} | {hora_st}")
                log.info(f"{'='*65}\n")

                await self.monitor_trades()
                self._reporte_horario()

                if len(self.open_trades) < MAX_TRADES:
                    found = 0
                    for i, sym in enumerate(self.symbols):
                        if len(self.open_trades) >= MAX_TRADES: break
                        sig = self.analyze(sym)
                        if sig:
                            found += 1
                            emoji = "📈" if sig['signal']=='LONG' else "📉"
                            log.info(f"  ★ {emoji} {sig['signal']} {sym} score:{sig['score']:.0f} RMI:{sig['rmi']:.0f}")
                            self.open_trade(sym, sig)
                        await asyncio.sleep(0.15)
                        if (i+1) % 20 == 0:
                            log.info(f"  ...{i+1}/{len(self.symbols)} analizados")
                    log.info(f"\n  {len(self.symbols)} pares | {found} señales FUSION")
                else:
                    log.info(f"  Max ({MAX_TRADES}) — esperando cierre")

                log.info(f"\n  Próximo ciclo en {INTERVAL}s\n")
                await asyncio.sleep(INTERVAL)

            except KeyboardInterrupt:
                log.info("Detenido"); break
            except Exception as e:
                log.error(f"Error loop #{iteration}: {e}")
                await asyncio.sleep(20)

async def main():
    try:
        await FusionBot().run()
    except Exception as e:
        log.error(f"Error fatal: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Terminado")
