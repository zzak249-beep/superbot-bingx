#!/usr/bin/env python3
"""
BOT FUSION v2.0.0 — Trend Magic + RMI Trend Sniper + EMA
══════════════════════════════════════════════════════════
MEJORAS v2 respecto al original:

  COMISIONES (igual que FLOOP v3):
    - TP:  TAKE_PROFIT límite → maker 0.02% | fallback market si rechazado
    - SL:  STOP límite con offset → maker 0.02% | fallback STOP_MARKET
    - Cierre manual: LIMIT IOC → maker 0.02% | fallback market
    - COMISION_ACTUAL fija a maker (0.02%) para calculo PnL correcto

  FILTROS DE MERCADO (igual que FLOOP v3):
    - _update_btc_trend: ventana real 4x15m (~1h) y 4x1h (~4h)
    - _update_market_bias: si 3+ del top-5 caen >2% => no longs
    - _filtro_btc_ok: requiere btc_1h > -1.5% Y btc_4h > -2.5% para LONGS

  LOGICA:
    - Rate limiter 0.12s entre llamadas API (evita 429)
    - Cache de klines por ciclo (evita descargas duplicadas)
    - _hora_ok sin datetime.utcnow() deprecated
    - Cooldown diferenciado: TP=15min, SL=30min
    - Trailing stop porcentaje configurable (TRAILING_LOCK_PCT)
    - log.info en _qty_contratos solo cuando hay trade real
    - Score normalizado: se loguea sobre el maximo posible (~140)
    - Aviso Telegram cuando se reconcilia leverage diferente

  TP DINAMICO:
    - Usa ATR real * TP_ATR_MULT (igual que FLOOP)
    - Limites min/max configurables
    - RR minimo garantizado 1.3:1
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re
from datetime import datetime, timedelta, timezone
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
MAX_TRADES       = clean('MAX_OPEN_TRADES',          '3',   'int')
INTERVAL         = clean('CHECK_INTERVAL',          '120',  'int')
MIN_VOLUME       = clean('MIN_VOLUME_24H',       '500000',  'float')
MAX_SYMBOLS      = clean('MAX_SYMBOLS_TO_ANALYZE',   '60',  'int')
MIN_SCORE        = clean('MIN_SCORE',                '70',  'float')
TRAILING         = clean('TRAILING_STOP_ENABLED',  'true',  'bool')
TRAILING_LOCK    = clean('TRAILING_LOCK_PCT',       '60',   'float')  # % ganancia a proteger
USE_LIMIT_ORDERS = clean('USE_LIMIT_ORDERS',       'true',  'bool')
ENABLE_LONGS     = clean('ENABLE_LONGS',           'true',  'bool')
ENABLE_SHORTS    = clean('ENABLE_SHORTS',          'true',  'bool')

# Filtro BTC con doble ventana (fix del bug de 1 sola vela)
BTC_FILTER_1H    = clean('BTC_FILTER_1H_PCT',       '1.5',  'float')
BTC_FILTER_4H    = clean('BTC_FILTER_4H_PCT',       '2.5',  'float')

# Filtro mercado amplio
MARKET_FILTER_ON = clean('MARKET_FILTER_ENABLED',  'true',  'bool')
MARKET_FILTER_PCT= clean('MARKET_FILTER_PCT',       '2.0',  'float')
MARKET_FILTER_N  = clean('MARKET_FILTER_MIN_BAD',   '3',    'int')

# TP/SL dinamico con ATR
TP_ATR_MULT      = clean('TP_ATR_MULTIPLIER',       '2.5',  'float')
SL_PCT           = clean('STOP_LOSS_PCT',           '1.0',  'float')
TP_MIN_PCT       = clean('TP_MIN_PCT',              '1.2',  'float')
TP_MAX_PCT       = clean('TP_MAX_PCT',              '5.0',  'float')
SL_MIN_PCT       = clean('SL_MIN_PCT',              '0.8',  'float')
SL_MAX_PCT       = clean('SL_MAX_PCT',              '2.5',  'float')

# Cooldown diferenciado
COOLDOWN_AFTER_TP = clean('COOLDOWN_AFTER_TP_MIN',  '15',   'int')
COOLDOWN_AFTER_SL = clean('COOLDOWN_AFTER_SL_MIN',  '30',   'int')

# Parametros indicadores
CCI_LEN   = clean('CCI_LENGTH',    '20',  'int')
ATR_LEN   = clean('ATR_LENGTH',     '5',  'int')
ATR_MULT  = clean('ATR_MULTIPLIER','1.0', 'float')
RMI_LEN   = clean('RMI_LENGTH',    '14',  'int')
RMI_POS   = clean('RMI_POSITIVE',  '66',  'float')
RMI_NEG   = clean('RMI_NEGATIVE',  '30',  'float')

LIMIT_OFFSET_PCT = 0.05
SL_LIMIT_OFFSET  = clean('SL_LIMIT_OFFSET_PCT', '0.05', 'float') / 100
SKIP_HOURS_UTC   = {0, 1}
BASE_URL         = "https://open-api.bingx.com"
MARKET_REF_PAIRS = ['BTC-USDT','ETH-USDT','SOL-USDT','BNB-USDT','XRP-USDT']
API_RATE_LIMIT   = 0.12  # segundos entre llamadas

# Comisiones: con ordenes limite en entrada+TP+SL pagamos maker en los 3 lados
COMISION_MAKER   = 0.0002
COMISION_TAKER   = 0.0005
COMISION_ACTUAL  = COMISION_MAKER  # maker en todos los lados con la nueva logica

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ============================================================================
# RATE LIMITER
# ============================================================================

_last_api_call = 0.0

def _rate_limit():
    global _last_api_call
    wait = API_RATE_LIMIT - (time.time() - _last_api_call)
    if wait > 0: time.sleep(wait)
    _last_api_call = time.time()

# ============================================================================
# API BINGX
# ============================================================================

def bingx_request(method, endpoint, params, retries=2):
    for attempt in range(retries + 1):
        try:
            _rate_limit()
            p = dict(params)
            p['timestamp'] = int(time.time() * 1000)
            qs  = urlencode(sorted(p.items()))
            sig = hmac.new(BINGX_API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
            url = f"{BASE_URL}{endpoint}?{qs}&signature={sig}"
            hdr = {'X-BX-APIKEY': BINGX_API_KEY,
                   'Content-Type': 'application/x-www-form-urlencoded'}
            r = (requests.get(url, headers=hdr, timeout=12)
                 if method == 'GET'
                 else requests.post(url, headers=hdr, timeout=12))
            if r.status_code == 429:
                wait = int(r.headers.get('Retry-After', 5))
                log.warning(f"  Rate limit 429 — esperando {wait}s")
                time.sleep(wait); continue
            return r
        except Exception as e:
            if attempt < retries:
                log.warning(f"  API retry {attempt+1}: {e}")
                time.sleep(2 ** attempt)
            else:
                raise

# ============================================================================
# INDICADORES
# ============================================================================

def calc_sma(prices, period):
    if not prices: return 0.0
    w = prices[-period:] if len(prices) >= period else prices
    return sum(w) / len(w)

def calc_ema(prices, period):
    if not prices: return 0.0
    period = min(period, len(prices))
    k, e = 2.0 / (period + 1), prices[0]
    for p in prices[1:]: e = p * k + e * (1 - k)
    return e

def calc_rma(values, period):
    if not values: return 0.0
    period = min(period, len(values))
    result = sum(values[:period]) / period
    alpha  = 1.0 / period
    for v in values[period:]: result = alpha * v + (1 - alpha) * result
    return result

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < 2: return 0.0
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, len(closes))]
    return calc_rma(trs, period) if trs else 0.0

def calc_trend_magic(highs, lows, closes, cci_len=20, atr_len=5, mult=1.0):
    """
    Trend Magic: CCI + ATR trailing stop.
    Traduccion fiel del Pine Script:
      - ATR suavizado con SMA (ta.sma(ta.tr, atr_len))
      - CCI determina direccion
      - Trailing buffers up/down siguen el precio
    Retorna (trend_val, direction): direction 1=bull, -1=bear
    """
    n = len(closes)
    if n < cci_len + atr_len + 5:
        return closes[-1] if closes else 0.0, 0

    # ATR con SMA (como en Pine: ta.sma(ta.tr, atr_len))
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, n)]
    atr_sma = calc_sma(trs, atr_len)

    # CCI actual
    def _cci_at(idx):
        start = max(0, idx - cci_len + 1)
        h_s = highs[start:idx+1]; l_s = lows[start:idx+1]; c_s = closes[start:idx+1]
        tp  = [(h_s[i]+l_s[i]+c_s[i])/3 for i in range(len(c_s))]
        sma = sum(tp) / len(tp)
        md  = sum(abs(t - sma) for t in tp) / len(tp)
        return (tp[-1] - sma) / (0.015 * md) if md > 0 else 0.0

    cci_now  = _cci_at(n - 1)
    cci_prev = _cci_at(n - 2) if n > cci_len else 0.0

    # Trailing buffers (simulamos los ultimos 2 pasos del Pine)
    buf_up_prev  = lows[-2]  - mult * atr_sma
    buf_dn_prev  = highs[-2] + mult * atr_sma
    buf_up_now   = lows[-1]  - mult * atr_sma
    buf_dn_now   = highs[-1] + mult * atr_sma

    # Trailing: el buffer solo sube o solo baja
    if closes[-2] > buf_up_prev: buf_up_now = max(buf_up_now, buf_up_prev)
    if closes[-2] < buf_dn_prev: buf_dn_now = min(buf_dn_now, buf_dn_prev)

    # Direccion segun CCI
    if cci_now >= 0:
        direction = 1
        trend_val = buf_up_now
    else:
        direction = -1
        trend_val = buf_dn_now

    return trend_val, direction

def calc_rmi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains   = [max(0, c) for c in changes]
    losses  = [max(0, -c) for c in changes]
    up   = calc_rma(gains, period)
    down = calc_rma(losses, period)
    if down == 0: return 100.0
    if up == 0:   return 0.0
    return 100 - (100 / (1 + up / down))

def calc_mfi(highs, lows, closes, volumes, period=14):
    if len(closes) < period + 1: return 50.0
    hlc3 = [(highs[i]+lows[i]+closes[i])/3 for i in range(len(closes))]
    pos_flow, neg_flow = [], []
    for i in range(1, len(hlc3)):
        mf = hlc3[i] * volumes[i]
        if   hlc3[i] > hlc3[i-1]: pos_flow.append(mf); neg_flow.append(0)
        elif hlc3[i] < hlc3[i-1]: pos_flow.append(0);  neg_flow.append(mf)
        else:                       pos_flow.append(0);  neg_flow.append(0)
    pf = sum(pos_flow[-period:])
    nf = sum(neg_flow[-period:])
    if nf == 0: return 100.0
    if pf == 0: return 0.0
    return 100 - (100 / (1 + pf / nf))

def calc_rmi_sniper(highs, lows, closes, volumes, period=14, pos_thresh=66, neg_thresh=30):
    if len(closes) < period + 5: return 50.0, 50.0, None
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
    if rmi_prev < pos_thresh and rmi_now > pos_thresh and rmi_now > neg_thresh and ema5_rising:
        signal = 'BUY'
    elif rmi_now < neg_thresh and not ema5_rising:
        signal = 'SELL'
    return rmi_now, rmi_prev, signal

def calc_bollinger(prices, period=20):
    if len(prices) < period:
        m = sum(prices)/len(prices); return m, m, m
    w   = prices[-period:]
    mid = sum(w) / period
    std = (sum((p-mid)**2 for p in w) / period) ** 0.5
    return mid + 2*std, mid, mid - 2*std

def vol_spike(volumes):
    if len(volumes) < 5: return 1.0
    avg = sum(volumes[:-1]) / len(volumes[:-1])
    return volumes[-1] / avg if avg > 0 else 1.0

def calc_tp_sl_atr(price, direction, atr, tp_mult, sl_pct_fixed,
                   tp_min, tp_max, sl_min, sl_max):
    """
    TP dinamico basado en ATR real, SL fijo con limites.
    RR minimo garantizado 1.3:1.
    """
    atr_pct = atr / price * 100 if price > 0 else 1.0
    tp_pct  = max(tp_min, min(tp_max, atr_pct * tp_mult))
    sl_pct  = max(sl_min, min(sl_max, sl_pct_fixed))
    if tp_pct < sl_pct * 1.3:
        tp_pct = sl_pct * 1.3
    if direction == 'LONG':
        return price*(1+tp_pct/100), price*(1-sl_pct/100), round(tp_pct,3), round(sl_pct,3)
    return price*(1-tp_pct/100), price*(1+sl_pct/100), round(tp_pct,3), round(sl_pct,3)

# ============================================================================
# BOT FUSION v2
# ============================================================================

class FusionBot:

    def __init__(self):
        dirs = (['LONGS'] if ENABLE_LONGS else []) + (['SHORTS'] if ENABLE_SHORTS else [])

        log.info("=" * 65)
        log.info("  BOT FUSION v2.0.0")
        log.info("  Trend Magic + RMI Sniper + EMA + BB + Volume")
        log.info("=" * 65)
        log.info(f"  Modo:        {'AUTO' if AUTO_TRADING else 'SENALES'}")
        log.info(f"  Capital:     ${POSITION_SIZE} USDT x{LEVERAGE}")
        log.info(f"  TP/SL ATR:   TP={TP_ATR_MULT}xATR ({TP_MIN_PCT}%-{TP_MAX_PCT}%) | SL={SL_PCT}%")
        log.info(f"  Comisiones:  maker 0.02% entrada+TP+SL (limite)")
        log.info(f"  Filtro BTC:  1h>{-BTC_FILTER_1H:.1f}% 4h>{-BTC_FILTER_4H:.1f}%")
        log.info(f"  Filtro mkt:  {'ON' if MARKET_FILTER_ON else 'OFF'} "
                 f"({MARKET_FILTER_N}+ pares caen >{MARKET_FILTER_PCT}% = no longs)")
        log.info(f"  Score min:   {MIN_SCORE}")
        log.info(f"  Cooldown:    TP={COOLDOWN_AFTER_TP}min SL={COOLDOWN_AFTER_SL}min")
        log.info(f"  Dirs:        {' + '.join(dirs)}")
        log.info("=" * 65)

        self.symbols        = []
        self.open_trades    = {}
        self._contracts     = {}
        self._cooldowns     = {}   # {symbol: (resume_ts, reason)}
        self._klines_cache  = {}   # limpiado cada ciclo
        self._last_report   = datetime.now()
        self._btc_1h        = 0.0
        self._btc_4h        = 0.0
        self._market_bias   = 'neutral'
        self._balance       = 0.0
        self.stats = {'exec':0,'closed':0,'wins':0,'losses':0,'pnl':0.0,
                      'max_dd':0.0,'peak_pnl':0.0}

        self._verify()
        self._load_contracts()
        self._get_symbols()
        self._reconciliar_posiciones()
        self._tg(
            f"<b>Bot FUSION v2.0.0 iniciado</b>\n"
            f"Trend Magic + RMI Sniper + EMA\n"
            f"TP={TP_ATR_MULT}xATR | SL={SL_PCT}% | Score>={MIN_SCORE}\n"
            f"Filtro BTC: 1h>{-BTC_FILTER_1H:.1f}% 4h>{-BTC_FILTER_4H:.1f}%\n"
            f"Comisiones maker en entrada+TP+SL\n"
            f"Capital: ${POSITION_SIZE} x{LEVERAGE} | Balance: ${self._balance:.2f}"
        )

    # ---------------------------------------------------------------- setup

    def _extraer_balance(self, d):
        try:
            data = d.get('data', {})
            if isinstance(data, list): data = data[0] if data else {}
            bal = data.get('balance', None)
            if isinstance(bal, dict):
                for k in ['equity','balance','availableMargin','availableBalance']:
                    v = bal.get(k)
                    if v is not None:
                        try: return float(str(v) or 0)
                        except: continue
            for k in ['equity','balance','availableMargin','availableBalance','walletBalance']:
                v = data.get(k)
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
                if isinstance(obj, list) and obj: return buscar(obj[0], depth+1)
                return None
            return buscar(data) or 0.0
        except Exception as e:
            log.error(f"  Error balance: {e}"); return 0.0

    def _verify(self):
        global AUTO_TRADING
        if not AUTO_TRADING: return
        if not BINGX_API_KEY or not BINGX_API_SECRET:
            log.error("  API keys vacias"); AUTO_TRADING = False; return
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/balance', {}).json()
            if d.get('code') == 0:
                self._balance = self._extraer_balance(d)
                log.info(f"BingX OK | Balance: ${self._balance:.2f} USDT")
            else:
                log.error(f"BingX [{d.get('code')}]: {d.get('msg')}"); AUTO_TRADING = False
        except Exception as e:
            log.error(f"Error API: {e}"); AUTO_TRADING = False

    def _update_balance(self):
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/balance', {}).json()
            if d.get('code') == 0: self._balance = self._extraer_balance(d)
        except: pass
        return self._balance

    def _balance_suficiente(self):
        bal = self._update_balance()
        needed = POSITION_SIZE / LEVERAGE
        if bal < needed:
            log.warning(f"  Balance ${bal:.2f} < margen ${needed:.2f} -- skip"); return False
        return True

    def _set_leverage(self, symbol, direction):
        try:
            bingx_request('POST', '/openApi/swap/v2/trade/leverage',
                          {'symbol':symbol,'side':direction,'leverage':str(LEVERAGE)})
        except: pass

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
        except Exception as e: log.warning(f"Error contratos: {e}")

    def _get_symbols(self):
        NO = ['DOW','JONES','SP500','SPX','SPY','QQQ','NASDAQ','RUSSELL','DAX','FTSE',
              'CAC','NIKKEI','HANG','BOVESPA','IBEX','US30','NAS100','US500','DJI','INDEX',
              'GOLD','SILVER','XAU','XAG','PAXG','XAUT','OIL','BRENT','WTI','CRUDE',
              'GAS','GASOLINE','PLATINUM','PALLADIUM','COPPER','NICKEL','ZINC','IRON',
              'TSLA','AAPL','MSFT','GOOGL','AMZN','META','NVDA','COIN','MSTR',
              'EUR','GBP','JPY','CHF','AUD','CAD','NZD',
              'WHEAT','CORN','SUGAR','COFFEE','COTTON','LUMBER','SOYBEAN']
        try:
            d = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker", timeout=15).json()
            if d.get('code') == 0:
                items, excl = [], []
                for t in d.get('data', []):
                    sym = t.get('symbol','')
                    if not sym.endswith('-USDT'): continue
                    base = sym.replace('-USDT','').upper()
                    if any(kw in base for kw in NO): excl.append(base); continue
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
        except Exception as e: log.warning(f"Error simbolos: {e}")
        self.symbols = ['BTC-USDT','ETH-USDT','SOL-USDT','BNB-USDT','XRP-USDT']

    def _reconciliar_posiciones(self):
        if not AUTO_TRADING: return
        log.info("  Reconciliando posiciones...")
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
                    msg = f"  {sym} ignorado — leverage {lev}x != {LEVERAGE}x"
                    log.info(msg)
                    self._tg(f"Reconciliacion: {msg.strip()}")
                    continue
                try: entry = float(p.get('avgPrice') or p.get('entryPrice') or 0)
                except: entry = 0
                if entry <= 0:
                    tk = self._ticker(sym); entry = tk['price'] if tk else 0
                if entry <= 0: continue
                direction = 'LONG' if amt > 0 else 'SHORT'
                qty_c     = abs(amt)
                tp_p      = entry*(1+TP_MIN_PCT/100) if direction=='LONG' else entry*(1-TP_MIN_PCT/100)
                sl_p      = entry*(1-SL_MIN_PCT/100) if direction=='LONG' else entry*(1+SL_MIN_PCT/100)
                tp_ok = self._cond_order(sym, direction, qty_c, tp_p, 'TAKE_PROFIT')
                time.sleep(0.3)
                sl_ok = self._cond_order(sym, direction, qty_c, sl_p, 'STOP')
                self.open_trades[sym] = {
                    'direction':direction,'entry':entry,'qty_c':qty_c,'usdt_qty':POSITION_SIZE,
                    'tp':tp_p,'sl':sl_p,'tp_pct':TP_MIN_PCT,'sl_pct':SL_MIN_PCT,
                    'highest':entry,'lowest':entry,'order_id':'RECONCILIADO',
                    'tp_ok':tp_ok,'sl_ok':sl_ok,'opened_at':datetime.now(),'score':0,'atr':0,
                }
                recuperadas += 1
                log.info(f"  {direction} {sym} reconciliado @ ${entry:.6f}")
            log.info(f"  Reconciliacion: {recuperadas} posiciones")
        except Exception as e: log.error(f"  Error reconciliacion: {e}")

    # ---------------------------------------------------------------- datos con cache

    def _klines(self, symbol, interval='15m', limit=100):
        key = (symbol, interval)
        if key in self._klines_cache:
            return self._klines_cache[key]
        try:
            _rate_limit()
            d = requests.get(f"{BASE_URL}/openApi/swap/v3/quote/klines",
                params={'symbol':symbol,'interval':interval,'limit':limit}, timeout=12).json()
            if d.get('code') == 0 and d.get('data'):
                k = d['data']
                result = ([float(x['close'])  for x in k],
                          [float(x['high'])   for x in k],
                          [float(x['low'])    for x in k],
                          [float(x['volume']) for x in k],
                          [float(x['open'])   for x in k])
                self._klines_cache[key] = result
                return result
        except: pass
        return None, None, None, None, None

    def _clear_klines_cache(self):
        self._klines_cache.clear()

    def _ticker(self, symbol):
        try:
            _rate_limit()
            d = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker",
                             params={'symbol':symbol}, timeout=8).json()
            if d.get('code') == 0 and d.get('data'):
                t = d['data']
                return {'price':  float(t.get('lastPrice',0)),
                        'change': float(t.get('priceChangePercent',0))}
        except: pass
        return None

    def _update_btc_trend(self):
        """Ventanas reales: 4x15m = ~1h, 4x1h = ~4h."""
        try:
            c15, *_ = self._klines('BTC-USDT', '15m', 8)
            if c15 and len(c15) >= 5:
                self._btc_1h = (c15[-1] - c15[-5]) / c15[-5] * 100
            c1h, *_ = self._klines('BTC-USDT', '1h', 8)
            if c1h and len(c1h) >= 5:
                self._btc_4h = (c1h[-1] - c1h[-5]) / c1h[-5] * 100
        except: pass

    def _update_market_bias(self):
        """Comprueba cuantos del top-5 estan bajando o subiendo fuerte."""
        if not MARKET_FILTER_ON:
            self._market_bias = 'neutral'; return
        bull, bear = 0, 0
        for sym in MARKET_REF_PAIRS:
            try:
                c15, *_ = self._klines(sym, '15m', 6)
                if c15 and len(c15) >= 5:
                    chg = (c15[-1] - c15[-5]) / c15[-5] * 100
                    if chg >  MARKET_FILTER_PCT: bull += 1
                    if chg < -MARKET_FILTER_PCT: bear += 1
            except: pass
        if   bear >= MARKET_FILTER_N: self._market_bias = 'bear'
        elif bull >= MARKET_FILTER_N: self._market_bias = 'bull'
        else:                          self._market_bias = 'neutral'
        log.info(f"  Mercado: {self._market_bias} (bull={bull} bear={bear}) | "
                 f"BTC 1h:{self._btc_1h:+.2f}% 4h:{self._btc_4h:+.2f}%")

    def _filtro_btc_ok(self, direction):
        if direction == 'LONG':
            return (self._btc_1h > -BTC_FILTER_1H and
                    self._btc_4h > -BTC_FILTER_4H and
                    self._market_bias != 'bear')
        else:
            return (self._btc_1h < BTC_FILTER_1H and
                    self._btc_4h < BTC_FILTER_4H and
                    self._market_bias != 'bull')

    # ---------------------------------------------------------------- sizing y cooldown

    def _qty_contratos(self, symbol, price, usdt_amount=None, verbose=True):
        if usdt_amount is None: usdt_amount = POSITION_SIZE
        info  = self._contracts.get(symbol, {'step':1.0,'prec':2,'ctval':1.0})
        step  = max(info['step'], 0.0001)
        prec  = info['prec']
        ppc   = price * info.get('ctval',1.0) if info.get('ctval',1.0) != 1.0 else price
        if ppc <= 0: return None, 0
        qty = round(math.ceil(usdt_amount / ppc / step) * step, prec)
        val = qty * ppc
        i   = 0
        while val < MIN_TRADE and i < 500:
            qty += step; qty = round(qty, prec); val = qty * ppc; i += 1
        if val > usdt_amount * 1.3:
            qty = round(math.floor((usdt_amount*1.3/ppc)/step)*step, prec); val = qty*ppc
        if verbose:
            log.info(f"    qty: {qty} x ${ppc:.6f} = ${val:.2f} USDT")
        return qty, round(val, 4)

    def _cooldown_ok(self, symbol):
        cd = self._cooldowns.get(symbol)
        if not cd: return True
        resume_ts, reason = cd
        if time.time() >= resume_ts:
            del self._cooldowns[symbol]; return True
        return False

    def _set_cooldown(self, symbol, reason='TP'):
        mins = COOLDOWN_AFTER_TP if reason == 'TP' else COOLDOWN_AFTER_SL
        self._cooldowns[symbol] = (time.time() + mins * 60, reason)

    def _hora_ok(self):
        return datetime.now(timezone.utc).hour not in SKIP_HOURS_UTC

    # ---------------------------------------------------------------- ANALISIS FUSION

    def analyze(self, symbol):
        if symbol in self.open_trades: return None
        if not self._cooldown_ok(symbol): return None
        if not self._hora_ok(): return None

        closes, highs, lows, volumes, opens = self._klines(symbol, '15m', 100)
        if not closes or len(closes) < 30: return None

        ticker = self._ticker(symbol)
        if not ticker or ticker['price'] <= 0: return None
        price  = ticker['price']
        change = ticker['change']

        # ── Indicadores ──────────────────────────────────────────────
        tm_val, tm_dir = calc_trend_magic(highs, lows, closes, CCI_LEN, ATR_LEN, ATR_MULT)
        trend_bullish  = tm_dir > 0
        trend_bearish  = tm_dir < 0

        rmi_now, rmi_prev, rmi_signal = calc_rmi_sniper(
            highs, lows, closes, volumes, RMI_LEN, RMI_POS, RMI_NEG)

        ema9       = calc_ema(closes, 9)
        ema21      = calc_ema(closes, 21)
        ema50      = calc_ema(closes, min(50, len(closes)))
        ema9_prev  = calc_ema(closes[:-1], 9)
        ema9_rising  = ema9 > ema9_prev
        ema9_falling = ema9 < ema9_prev
        ema_bull = ema9 > ema21 > ema50
        ema_bear = ema9 < ema21 < ema50

        bb_u, bb_m, bb_l = calc_bollinger(closes, 20)
        bb_rng = bb_u - bb_l
        bb_pos = (price - bb_l) / bb_rng if bb_rng > 0 else 0.5

        vs = vol_spike(volumes)

        atr     = calc_atr(highs, lows, closes, 14)
        atr_pct = atr / price * 100 if price > 0 else 0.0

        trend_5  = (closes[-1]-closes[-6])  / closes[-6]  * 100 if len(closes) >= 6  else 0.0
        trend_10 = (closes[-1]-closes[-11]) / closes[-11] * 100 if len(closes) >= 11 else 0.0

        # ── Scoring LONG ─────────────────────────────────────────────
        long_score, long_reasons = 0, []

        if ENABLE_LONGS and self._filtro_btc_ok('LONG'):

            if trend_bullish:
                long_score += 35; long_reasons.append("TM_BULL(35)")
            else:
                long_score -= 20; long_reasons.append("TM_BEAR(-20)")

            if rmi_signal == 'BUY':
                long_score += 40; long_reasons.append(f"RMI_BUY({rmi_now:.0f})(40)")
            elif rmi_now > RMI_POS:
                long_score += 15; long_reasons.append(f"RMI_alto({rmi_now:.0f})(15)")
            elif rmi_now < RMI_NEG:
                long_score -= 15; long_reasons.append("RMI_bajo(-15)")

            if ema_bull and ema9_rising:
                long_score += 20; long_reasons.append("EMA_BULL+(20)")
            elif ema_bull:
                long_score += 10; long_reasons.append("EMA_BULL(10)")
            elif ema_bear:
                long_score -= 15; long_reasons.append("EMA_BEAR(-15)")

            if   bb_pos <= 0.10: long_score += 15; long_reasons.append("BB_bot(15)")
            elif bb_pos <= 0.25: long_score += 8;  long_reasons.append("BB_low(8)")
            elif bb_pos >= 0.85: long_score -= 10; long_reasons.append("BB_high(-10)")

            if vs >= 1.8:
                p = min(15, int(vs*7)); long_score += p; long_reasons.append(f"Vol{vs:.1f}x({p})")
            elif vs < 1.2:
                long_score -= 8; long_reasons.append("VolBajo(-8)")

            if trend_5 > 1.0:  long_score += 10; long_reasons.append("Impulso+(10)")
            if trend_5 < -1.5: long_score -= 10; long_reasons.append("Impulso-(-10)")

            if atr_pct > 0.5:  long_score += 8; long_reasons.append(f"ATR{atr_pct:.1f}%(8)")
            elif atr_pct < 0.2: long_score -= 8; long_reasons.append("ATRbajo(-8)")

        # ── Scoring SHORT ─────────────────────────────────────────────
        short_score, short_reasons = 0, []

        if ENABLE_SHORTS and self._filtro_btc_ok('SHORT'):

            if trend_bearish:
                short_score += 35; short_reasons.append("TM_BEAR(35)")
            else:
                short_score -= 20; short_reasons.append("TM_BULL(-20)")

            if rmi_signal == 'SELL':
                short_score += 40; short_reasons.append(f"RMI_SELL({rmi_now:.0f})(40)")
            elif rmi_now < RMI_NEG:
                short_score += 15; short_reasons.append(f"RMI_bajo({rmi_now:.0f})(15)")
            elif rmi_now > RMI_POS:
                short_score -= 15; short_reasons.append("RMI_alto(-15)")

            if ema_bear and ema9_falling:
                short_score += 20; short_reasons.append("EMA_BEAR+(20)")
            elif ema_bear:
                short_score += 10; short_reasons.append("EMA_BEAR(10)")
            elif ema_bull:
                short_score -= 15; short_reasons.append("EMA_BULL(-15)")

            if   bb_pos >= 0.90: short_score += 15; short_reasons.append("BB_top(15)")
            elif bb_pos >= 0.75: short_score += 8;  short_reasons.append("BB_high(8)")
            elif bb_pos <= 0.15: short_score -= 10; short_reasons.append("BB_low(-10)")

            if vs >= 1.8:
                p = min(15, int(vs*7)); short_score += p; short_reasons.append(f"Vol{vs:.1f}x({p})")
            elif vs < 1.2:
                short_score -= 8; short_reasons.append("VolBajo(-8)")

            if trend_5 < -1.0: short_score += 10; short_reasons.append("Impulso-(10)")
            if trend_5 >  1.5: short_score -= 10; short_reasons.append("Impulso+(-10)")

            if atr_pct > 0.5:  short_score += 8; short_reasons.append(f"ATR{atr_pct:.1f}%(8)")
            elif atr_pct < 0.2: short_score -= 8; short_reasons.append("ATRbajo(-8)")

        # ── Seleccionar senal ─────────────────────────────────────────
        base = {
            'price':price, 'change':change, 'rmi':rmi_now,
            'tm_dir':tm_dir, 'bb_pos':round(bb_pos*100,1),
            'atr_pct':round(atr_pct,2), 'atr_val':atr,
        }

        if long_score >= MIN_SCORE and long_score > short_score:
            tp_p, sl_p, tp_pct, sl_pct = calc_tp_sl_atr(
                price, 'LONG', atr, TP_ATR_MULT, SL_PCT,
                TP_MIN_PCT, TP_MAX_PCT, SL_MIN_PCT, SL_MAX_PCT)
            return {**base, 'signal':'LONG', 'score':long_score,
                    'reasons':' | '.join(long_reasons),
                    'tp_price':tp_p, 'sl_price':sl_p, 'tp_pct':tp_pct, 'sl_pct':sl_pct}

        if short_score >= MIN_SCORE and short_score > long_score:
            tp_p, sl_p, tp_pct, sl_pct = calc_tp_sl_atr(
                price, 'SHORT', atr, TP_ATR_MULT, SL_PCT,
                TP_MIN_PCT, TP_MAX_PCT, SL_MIN_PCT, SL_MAX_PCT)
            return {**base, 'signal':'SHORT', 'score':short_score,
                    'reasons':' | '.join(short_reasons),
                    'tp_price':tp_p, 'sl_price':sl_p, 'tp_pct':tp_pct, 'sl_pct':sl_pct}

        return None

    # ---------------------------------------------------------------- ordenes maker-first

    def _place_entry(self, symbol, direction, usdt_qty, price):
        qty_c, val = self._qty_contratos(symbol, price, usdt_qty, verbose=True)
        if not qty_c: return None, None
        side = 'BUY' if direction == 'LONG' else 'SELL'
        log.info(f"  Abriendo {direction} {symbol}: {qty_c} cts = ${val:.2f}")

        if USE_LIMIT_ORDERS:
            offset = (1-LIMIT_OFFSET_PCT/100) if direction=='LONG' else (1+LIMIT_OFFSET_PCT/100)
            lp = round(price*offset, 8)
            d  = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol,'side':side,'positionSide':direction,
                'type':'LIMIT','price':str(lp),'quantity':str(qty_c),'timeInForce':'GTC',
            }).json()
            if d.get('code') == 0:
                log.info(f"  Entrada maker OK @ ${lp:.6f} (0.02%)")
                return d.get('data',{}).get('orderId','OK'), qty_c
            if 'margin' in str(d.get('msg','')).lower(): return None, None
            log.warning("  Limite entrada fallo -- fallback market")

        d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol':symbol,'side':side,'positionSide':direction,
            'type':'MARKET','quantity':str(qty_c),
        }).json()
        if d.get('code') == 0:
            log.info(f"  Entrada taker market OK (0.05%)")
            return d.get('data',{}).get('orderId','OK'), qty_c
        log.error(f"  Entrada fallida: {d.get('msg')}"); return None, None

    def _cond_order(self, symbol, direction, qty_c, stop_price, otype):
        """
        Ordenes condicionales maker-first.
        TP  → TAKE_PROFIT limite (maker) | fallback TAKE_PROFIT_MARKET
        SL  → STOP limite con offset (maker) | fallback STOP_MARKET
        """
        if not qty_c or qty_c <= 0: return False
        try:
            is_tp      = "TAKE" in otype or otype == 'TAKE_PROFIT'
            close_side = 'SELL' if direction=='LONG' else 'BUY'

            if is_tp:
                params = {
                    'symbol':symbol,'side':close_side,'positionSide':direction,
                    'type':'TAKE_PROFIT','quantity':str(qty_c),
                    'price':str(round(stop_price,8)),
                    'stopPrice':str(round(stop_price,8)),'timeInForce':'GTC',
                }
                d  = bingx_request('POST', '/openApi/swap/v2/trade/order', params).json()
                ok = d.get('code') == 0
                if ok:
                    log.info(f"  TP maker OK @ ${stop_price:.6f} (0.02%)")
                else:
                    log.warning(f"  TP limite rechazado — fallback market")
                    p2 = {'symbol':symbol,'side':close_side,'positionSide':direction,
                          'type':'TAKE_PROFIT_MARKET','quantity':str(qty_c),
                          'stopPrice':str(round(stop_price,8))}
                    d2 = bingx_request('POST', '/openApi/swap/v2/trade/order', p2).json()
                    ok = d2.get('code') == 0
                    if ok:  log.info(f"  TP taker fallback OK (0.05%)")
                    else:   log.error(f"  TP FALLO: {d2.get('msg')}")

            else:
                # SL: STOP limite con offset para ejecutar como maker
                if direction == 'LONG':
                    limit_price = round(stop_price * (1 + SL_LIMIT_OFFSET), 8)
                else:
                    limit_price = round(stop_price * (1 - SL_LIMIT_OFFSET), 8)

                params = {
                    'symbol':symbol,'side':close_side,'positionSide':direction,
                    'type':'STOP','quantity':str(qty_c),
                    'price':str(limit_price),
                    'stopPrice':str(round(stop_price,8)),'timeInForce':'GTC',
                }
                d  = bingx_request('POST', '/openApi/swap/v2/trade/order', params).json()
                ok = d.get('code') == 0
                if ok:
                    log.info(f"  SL maker OK trigger=${stop_price:.6f} limit=${limit_price:.6f} (0.02%)")
                else:
                    log.warning(f"  SL limite rechazado — fallback STOP_MARKET")
                    p2 = {'symbol':symbol,'side':close_side,'positionSide':direction,
                          'type':'STOP_MARKET','quantity':str(qty_c),
                          'stopPrice':str(round(stop_price,8))}
                    d2 = bingx_request('POST', '/openApi/swap/v2/trade/order', p2).json()
                    ok = d2.get('code') == 0
                    if ok:  log.info(f"  SL taker fallback OK (0.05%)")
                    else:   log.error(f"  SL FALLO: {d2.get('msg')}")

            return ok
        except Exception as e:
            log.error(f"  {otype}: {e}"); return False

    def _close_position(self, symbol, direction, t):
        """Cierre maker-first con LIMIT IOC, fallback MARKET."""
        qty_c      = t.get('qty_c', 0)
        close_side = 'SELL' if direction=='LONG' else 'BUY'

        cur_price = t.get('entry', 0)
        try:
            tk = self._ticker(symbol)
            if tk and tk['price'] > 0: cur_price = tk['price']
        except: pass

        if qty_c and qty_c > 0 and cur_price > 0:
            CLOSE_OFFSET = 0.0005
            limit_price  = (round(cur_price*(1+CLOSE_OFFSET), 8) if direction=='LONG'
                            else round(cur_price*(1-CLOSE_OFFSET), 8))
            params_lim = {
                'symbol':symbol,'side':close_side,'positionSide':direction,
                'type':'LIMIT','quantity':str(qty_c),
                'price':str(limit_price),'timeInForce':'IOC','reduceOnly':'true',
            }
            d = bingx_request('POST', '/openApi/swap/v2/trade/order', params_lim).json()
            if d.get('code') == 0:
                log.info(f"  Cierre maker IOC @ ${limit_price:.6f} (0.02%)")
                return True
            log.warning("  Cierre limite rechazado -- fallback market")

        params_mkt = ({'symbol':symbol,'side':close_side,'positionSide':direction,
                       'type':'MARKET','quantity':str(qty_c),'reduceOnly':'true'}
                      if qty_c else
                      {'symbol':symbol,'side':close_side,'positionSide':direction,
                       'type':'MARKET',
                       'quoteOrderQty':str(round(t.get('usdt_qty',POSITION_SIZE),2)),
                       'reduceOnly':'true'})
        ok = bingx_request('POST', '/openApi/swap/v2/trade/order', params_mkt).json().get('code') == 0
        if ok: log.info(f"  Cierre taker market OK (0.05%)")
        return ok

    def _tiene_posicion(self, symbol):
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/positions', {'symbol':symbol}).json()
            if d.get('code') == 0:
                for p in (d.get('data') or []):
                    amt = float(p.get('positionAmt',0) or 0)
                    if abs(amt) > 0: return True, 'LONG' if amt>0 else 'SHORT'
        except: pass
        return False, None

    def _esperar_posicion(self, symbol, direction, timeout=45):
        log.info(f"  Esperando {direction} {symbol}...")
        for i in range(timeout):
            try:
                d = bingx_request('GET', '/openApi/swap/v2/user/positions', {'symbol':symbol}).json()
                if d.get('code') == 0:
                    data = d.get('data') or []
                    if i < 3: log.info(f"  [debug {i+1}s] {str(data)[:150]}")
                    for p in data:
                        try: amt = float(p.get('positionAmt',0) or 0)
                        except: continue
                        ps  = str(p.get('positionSide','')).upper()
                        ok  = (amt>0 or ps=='LONG') if direction=='LONG' else (amt<0 or ps=='SHORT')
                        if ok and abs(amt) > 0:
                            entry_real = float(p.get('avgPrice') or p.get('entryPrice') or 0)
                            log.info(f"  OK: qty={abs(amt):.4f} entry=${entry_real:.6f} ({i+1}s)")
                            return abs(amt), entry_real
            except: pass
            time.sleep(1)
        log.warning(f"  Timeout {timeout}s"); return None, None

    def _cancelar_ordenes(self, symbol):
        try:
            d = bingx_request('GET', '/openApi/swap/v2/trade/openOrders', {'symbol':symbol}).json()
            if d.get('code') == 0:
                for o in (d.get('data',{}).get('orders') or []):
                    oid = o.get('orderId','')
                    if oid: bingx_request('DELETE', '/openApi/swap/v2/trade/order',
                                          {'symbol':symbol,'orderId':str(oid)})
        except: pass

    # ---------------------------------------------------------------- lifecycle

    def _pnl_contable(self, t, cur_price):
        direction = t['direction']
        cambio    = ((cur_price-t['entry'])/t['entry']
                     if direction=='LONG'
                     else (t['entry']-cur_price)/t['entry'])
        pnl = (t['usdt_qty']*LEVERAGE*cambio) - (t['usdt_qty']*LEVERAGE*COMISION_ACTUAL*2)
        return pnl, pnl/t['usdt_qty']*100

    def _actualizar_stats(self, pnl):
        self.stats['closed'] += 1; self.stats['pnl'] += pnl
        if pnl > 0: self.stats['wins']   += 1
        else:        self.stats['losses'] += 1
        if self.stats['pnl'] > self.stats['peak_pnl']:
            self.stats['peak_pnl'] = self.stats['pnl']
        dd = self.stats['peak_pnl'] - self.stats['pnl']
        if dd > self.stats['max_dd']: self.stats['max_dd'] = dd

    def open_trade(self, symbol, sig):
        if not AUTO_TRADING:
            log.info(f"  [SENAL] {sig['signal']} {symbol} score:{sig['score']:.0f}")
            return False
        if symbol in self.open_trades: return False
        if not self._balance_suficiente(): return False
        tiene, dir_bx = self._tiene_posicion(symbol)
        if tiene: log.info(f"  {symbol} ya tiene {dir_bx} -- skip"); return False

        direction = sig['signal']
        price     = sig['price']
        usdt_qty  = round(max(POSITION_SIZE, MIN_TRADE), 2)
        tp_price  = sig['tp_price']
        sl_price  = sig['sl_price']
        tp_pct    = sig['tp_pct']
        sl_pct    = sig['sl_pct']

        self._set_leverage(symbol, direction)
        log.info(f"\n  > {direction} {symbol} [FUSION score:{sig['score']:.0f}]")
        log.info(f"  RMI:{sig['rmi']:.0f} TM:{sig['tm_dir']} BB:{sig['bb_pos']}% ATR:{sig['atr_pct']:.2f}%")
        log.info(f"  TP:{tp_pct:.2f}% SL:{sl_pct:.2f}% RR:{tp_pct/sl_pct:.1f}:1")
        log.info(f"  BTC 1h:{self._btc_1h:+.2f}% 4h:{self._btc_4h:+.2f}% Mkt:{self._market_bias}")
        log.info(f"  {sig['reasons']}")

        oid, qty_c = self._place_entry(symbol, direction, usdt_qty, price)
        if not oid: return False

        qty_real, entry_real = self._esperar_posicion(symbol, direction, timeout=45)
        if qty_real is None:
            self._cancelar_ordenes(symbol); time.sleep(0.5)
            side  = 'BUY' if direction=='LONG' else 'SELL'
            d_mkt = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol,'side':side,'positionSide':direction,
                'type':'MARKET','quantity':str(qty_c),
            }).json()
            if d_mkt.get('code') == 0:
                qty_real, entry_real = self._esperar_posicion(symbol, direction, timeout=20)
            if qty_real is None:
                self._tg(f"AVISO {direction} {symbol} SIN TP/SL -- fijar manual")
                self.open_trades[symbol] = {
                    'direction':direction,'entry':price,'qty_c':qty_c,'usdt_qty':usdt_qty,
                    'tp':tp_price,'sl':sl_price,'tp_pct':tp_pct,'sl_pct':sl_pct,
                    'highest':price,'lowest':price,'order_id':oid,
                    'tp_ok':False,'sl_ok':False,'opened_at':datetime.now(),
                    'score':sig['score'],'atr':sig['atr_val'],
                }
                return True

        if entry_real and entry_real > 0:
            tp_price, sl_price, tp_pct, sl_pct = calc_tp_sl_atr(
                entry_real, direction, sig['atr_val'], TP_ATR_MULT, SL_PCT,
                TP_MIN_PCT, TP_MAX_PCT, SL_MIN_PCT, SL_MAX_PCT)
            log.info(f"  Entry real: ${entry_real:.6f} | TP:${tp_price:.6f} SL:${sl_price:.6f}")

        qty_final   = qty_real if qty_real else qty_c
        entry_final = entry_real if (entry_real and entry_real > 0) else price

        tp_ok = self._cond_order(symbol, direction, qty_final, tp_price, 'TAKE_PROFIT')
        time.sleep(0.3)
        sl_ok = self._cond_order(symbol, direction, qty_final, sl_price, 'STOP')
        for delay in [3, 5]:
            if tp_ok and sl_ok: break
            time.sleep(delay)
            if not tp_ok: tp_ok = self._cond_order(symbol, direction, qty_final, tp_price, 'TAKE_PROFIT')
            if not sl_ok: sl_ok = self._cond_order(symbol, direction, qty_final, sl_price, 'STOP')

        self.open_trades[symbol] = {
            'direction':direction,'entry':entry_final,'qty_c':qty_final,'usdt_qty':usdt_qty,
            'tp':tp_price,'sl':sl_price,'tp_pct':tp_pct,'sl_pct':sl_pct,
            'highest':entry_final,'lowest':entry_final,
            'order_id':oid,'tp_ok':tp_ok,'sl_ok':sl_ok,
            'opened_at':datetime.now(),'score':sig['score'],'atr':sig['atr_val'],
        }
        self.stats['exec'] += 1

        self._tg(
            f"<b>{'LONG' if direction=='LONG' else 'SHORT'} ABIERTO — FUSION</b>\n"
            f"<b>{symbol}</b> | Score:{sig['score']:.0f}\n"
            f"Entrada: ${entry_final:.6f}\n"
            f"{'OK' if tp_ok else 'FIJAR MANUAL'} TP: ${tp_price:.6f} (+{tp_pct:.2f}%)\n"
            f"{'OK' if sl_ok else 'FIJAR MANUAL'} SL: ${sl_price:.6f} (-{sl_pct:.2f}%)\n"
            f"RR: {tp_pct/sl_pct:.1f}:1 | ATR: {sig['atr_pct']:.2f}%\n"
            f"RMI:{sig['rmi']:.0f} TM:{sig['tm_dir']} BB:{sig['bb_pos']}%\n"
            f"BTC 1h:{self._btc_1h:+.2f}% 4h:{self._btc_4h:+.2f}% | Mkt:{self._market_bias}\n"
            f"Capital: ${usdt_qty} x{LEVERAGE} | Balance: ${self._balance:.2f}\n"
            f"{sig['reasons']}"
        )
        return True

    def close_trade(self, symbol, cur_price, reason):
        if symbol not in self.open_trades: return False
        t = self.open_trades[symbol]; direction = t['direction']
        self._close_position(symbol, direction, t)
        pnl, pnl_pct = self._pnl_contable(t, cur_price)
        self._actualizar_stats(pnl)
        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        mins  = int((datetime.now()-t['opened_at']).total_seconds()/60)
        close_reason = 'TP' if 'PROFIT' in reason else 'SL'
        self._set_cooldown(symbol, close_reason)
        log.info(f"  {'OK' if pnl>0 else 'MAL'} {reason} {symbol} "
                 f"PnL:${pnl:+.3f}({pnl_pct:+.1f}%) {mins}min")
        self._tg(
            f"<b>{'OK' if pnl>0 else 'MAL'} {direction} CERRADO — {reason}</b>\n"
            f"<b>{symbol}</b>\n"
            f"PnL: ${pnl:+.3f} ({pnl_pct:+.1f}%)\n"
            f"Entry: ${t['entry']:.6f} -> Exit: ${cur_price:.6f} | {mins}min\n"
            f"Cooldown: {COOLDOWN_AFTER_TP if close_reason=='TP' else COOLDOWN_AFTER_SL}min\n"
            f"<b>Total: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}% "
            f"({self.stats['wins']}W/{self.stats['losses']}L) | "
            f"MaxDD:${self.stats['max_dd']:.2f}</b>"
        )
        del self.open_trades[symbol]; return True

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
                    tk  = self._ticker(sym)
                    cur = tk['price'] if tk else t['entry']
                    pnl, pnl_pct = self._pnl_contable(t, cur)
                    self._actualizar_stats(pnl)
                    total = self.stats['wins']+self.stats['losses']
                    wr    = self.stats['wins']/total*100 if total else 0
                    mins  = int((datetime.now()-t['opened_at']).total_seconds()/60)
                    close_reason = 'TP' if pnl >= 0 else 'SL'
                    self._set_cooldown(sym, close_reason)
                    self._tg(
                        f"<b>{'OK' if pnl>=0 else 'MAL'} {t['direction']} cerrado BingX</b>\n"
                        f"<b>{sym}</b>\n"
                        f"PnL: ${pnl:+.3f} ({pnl_pct:+.1f}%) | {mins}min\n"
                        f"Total: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}% | MaxDD:${self.stats['max_dd']:.2f}"
                    )
                    del self.open_trades[sym]
        except Exception as e: log.debug(f"sync: {e}")

    async def monitor_trades(self):
        await self._sync_bingx()
        for sym in list(self.open_trades.keys()):
            try:
                t  = self.open_trades[sym]
                tk = self._ticker(sym)
                if not tk: continue
                cur = tk['price']; dir = t['direction']

                if dir == 'LONG':
                    pnl_pct = (cur-t['entry'])/t['entry']*100
                    if TRAILING and cur > t['highest']:
                        t['highest'] = cur
                        if pnl_pct >= 0.6:
                            new_sl = t['entry'] + (cur-t['entry']) * (TRAILING_LOCK/100)
                            if new_sl > t['sl']:
                                t['sl'] = new_sl
                                log.info(f"  Trailing {sym}: SL=${new_sl:.6f}")
                    hit_tp = cur >= t['tp']; hit_sl = cur <= t['sl']
                else:
                    pnl_pct = (t['entry']-cur)/t['entry']*100
                    if TRAILING and cur < t['lowest']:
                        t['lowest'] = cur
                        if pnl_pct >= 0.6:
                            new_sl = t['entry'] - (t['entry']-cur) * (TRAILING_LOCK/100)
                            if new_sl < t['sl']:
                                t['sl'] = new_sl
                                log.info(f"  Trailing {sym}: SL=${new_sl:.6f}")
                    hit_tp = cur <= t['tp']; hit_sl = cur >= t['sl']

                if abs(pnl_pct) > 0.3:
                    log.info(f"  {sym} {dir}: {pnl_pct:+.2f}% | TP:${t['tp']:.6f} SL:${t['sl']:.6f}")

                if hit_tp:   self.close_trade(sym, cur, "TAKE PROFIT")
                elif hit_sl: self.close_trade(sym, cur, "STOP LOSS")
            except Exception as e: log.debug(f"Monitor {sym}: {e}")

    def _reporte_horario(self):
        if datetime.now() - self._last_report < timedelta(hours=1): return
        self._last_report = datetime.now()
        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        pos_txt = ""
        for sym, t in self.open_trades.items():
            tk = self._ticker(sym)
            if tk:
                cur = tk['price']; dir = t['direction']
                pnl_pct = ((cur-t['entry'])/t['entry']*100
                           if dir=='LONG' else (t['entry']-cur)/t['entry']*100)
                pos_txt += f"  {sym} {dir}: {pnl_pct:+.2f}%\n"
        self._tg(
            f"<b>Reporte horario — FUSION v2</b>\n"
            f"PnL: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}% | MaxDD:${self.stats['max_dd']:.2f}\n"
            f"({self.stats['wins']}W/{self.stats['losses']}L | {self.stats['closed']} trades)\n"
            f"Abiertos: {len(self.open_trades)}/{MAX_TRADES}\n"
            f"Balance: ${self._balance:.2f} USDT\n"
            f"BTC 1h:{self._btc_1h:+.2f}% 4h:{self._btc_4h:+.2f}% | Mkt:{self._market_bias}\n"
            + (pos_txt or "  sin posiciones\n")
        )

    def _tg(self, msg):
        try:
            if TELEGRAM_TOKEN and TELEGRAM_CHAT:
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={'chat_id':TELEGRAM_CHAT,'text':msg,'parse_mode':'HTML'}, timeout=6)
        except: pass

    # ---------------------------------------------------------------- loop

    async def run(self):
        log.info("\n> Bot FUSION v2.0.0 arrancado\n")
        iteration, last_refresh = 0, 0
        while True:
            try:
                iteration += 1
                if time.time() - last_refresh > 600:
                    self._get_symbols(); last_refresh = time.time()

                self._clear_klines_cache()
                self._update_btc_trend()
                self._update_market_bias()
                self._update_balance()

                total   = self.stats['wins'] + self.stats['losses']
                wr      = self.stats['wins'] / total * 100 if total else 0
                hora_st = "BAJA" if not self._hora_ok() else "OK"

                log.info(f"\n{'='*65}")
                log.info(f"  #{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                         f"Abiertos:{len(self.open_trades)}/{MAX_TRADES} | "
                         f"PnL:${self.stats['pnl']:+.3f} | WR:{wr:.1f}%")
                log.info(f"  Balance:${self._balance:.2f} | "
                         f"BTC 1h:{self._btc_1h:+.2f}% 4h:{self._btc_4h:+.2f}% | "
                         f"Mkt:{self._market_bias} | {hora_st}")
                log.info(f"{'='*65}\n")

                await self.monitor_trades()
                self._reporte_horario()

                if len(self.open_trades) < MAX_TRADES and self._hora_ok():
                    found = 0
                    for i, sym in enumerate(self.symbols):
                        if len(self.open_trades) >= MAX_TRADES: break
                        sig = self.analyze(sym)
                        if sig:
                            found += 1
                            log.info(f"  * {sig['signal']} {sym} score:{sig['score']:.0f} "
                                     f"RMI:{sig['rmi']:.0f} TM:{sig['tm_dir']}")
                            self.open_trade(sym, sig)
                        await asyncio.sleep(0.15)
                        if (i+1) % 20 == 0:
                            log.info(f"  ...{i+1}/{len(self.symbols)} analizados")
                    log.info(f"\n  {len(self.symbols)} pares | {found} senales")
                elif not self._hora_ok():
                    log.info("  Hora de baja liquidez -- esperando")
                else:
                    log.info(f"  Max ({MAX_TRADES}) trades abiertos")

                log.info(f"\n  Proximo ciclo en {INTERVAL}s\n")
                await asyncio.sleep(INTERVAL)

            except KeyboardInterrupt:
                log.info("Detenido"); break
            except Exception as e:
                log.error(f"Error loop #{iteration}: {e}")
                await asyncio.sleep(20)

async def main():
    try: await FusionBot().run()
    except Exception as e: log.error(f"Error fatal: {e}")

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: log.info("Terminado")
