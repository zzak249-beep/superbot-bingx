#!/usr/bin/env python3
"""
BOT LONGS PROFESIONAL v3.2.0
════════════════════════════════════════════════
FIXES CRÍTICOS respecto a v3.0.2:

  1. cap val>10 ELIMINADO — _qty() respeta POSITION_SIZE del .env
  2. COMISION corregida: maker 0.02%, taker 0.05% (BingX real)
  3. _qty() usa contractSize (ctval) — contratos correctamente calculados
  4. clean() mejorado: maneja comillas/espacios/comas Railway
  5. bingx_request() con retry automático (x2)
  6. TP como TAKE_PROFIT (límite/maker) con fallback a mercado
  7. Entrada LIMIT opcional (maker 0.02%) con fallback MARKET
  8. Cooldown 15 min por par tras cierre
  9. Filtro hora baja liquidez UTC 0-1h
 10. TP_MIN_RENTABLE calculado: cubre comisiones reales
 11. Filtro BTC tendencia bajista (bloquea LONG si BTC cae >1.5% en 1h)
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
TP_PCT           = clean('TAKE_PROFIT_PCT',          '3.5', 'float')
SL_PCT           = clean('STOP_LOSS_PCT',            '1.0', 'float')
MAX_TRADES       = clean('MAX_OPEN_TRADES',          '2',   'int')
INTERVAL         = clean('CHECK_INTERVAL',           '60',  'int')
MIN_VOLUME       = clean('MIN_VOLUME_24H',       '500000',  'float')
MAX_SYMBOLS      = clean('MAX_SYMBOLS_TO_ANALYZE',   '80',  'int')
MIN_SCORE        = clean('MIN_SCORE',                '75',  'float')
TRAILING         = clean('TRAILING_STOP_ENABLED',  'true',  'bool')
USE_LIMIT_ORDERS = clean('USE_LIMIT_ORDERS',       'true',  'bool')
BTC_BEAR_BLOCK   = clean('BTC_BEAR_BLOCK_PCT',      '1.5',  'float')

LIMIT_OFFSET_PCT = 0.05   # % por debajo del mercado para entrada LONG (maker)
SKIP_HOURS_UTC   = {0, 1} # horas de baja liquidez

BASE_URL = "https://open-api.bingx.com"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# FIX v3.2: fees reales BingX
COMISION_MAKER  = 0.0002   # 0.02%
COMISION_TAKER  = 0.0005   # 0.05%
COMISION_ACTUAL = COMISION_MAKER if USE_LIMIT_ORDERS else COMISION_TAKER
# TP mínimo para cubrir 2 comisiones (entrada + salida) + margen mínimo
TP_MIN_RENTABLE = round((COMISION_ACTUAL * 2 / LEVERAGE + 0.002) * 100, 3)

# ============================================================================
# API BINGX — con retry
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
# INDICADORES
# ============================================================================

def calc_ema(prices, period):
    if not prices: return 0
    if len(prices) < period: return sum(prices) / len(prices)
    k, e = 2 / (period + 1), prices[0]
    for p in prices[1:]: e = p * k + e * (1 - k)
    return e

def calc_rsi(prices, period=14):
    if len(prices) < period + 1: return 50.0
    gains  = [max(0,  prices[i] - prices[i-1]) for i in range(1, len(prices))]
    losses = [max(0, prices[i-1] - prices[i])  for i in range(1, len(prices))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100.0
    return 100 - (100 / (1 + ag / al))

def calc_macd(prices):
    if len(prices) < 26: return 0, 0, 0
    ml = calc_ema(prices, 12) - calc_ema(prices, 26)
    return ml, ml * 0.9, ml * 0.1

def calc_bollinger(prices, period=20):
    if len(prices) < period:
        m = sum(prices) / len(prices); return m, m, m
    w = prices[-period:]
    mid = sum(w) / period
    std = (sum((p - mid)**2 for p in w) / period) ** 0.5
    return mid + 2*std, mid, mid - 2*std

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < 2: return 0
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, min(len(closes), period+1))]
    return sum(trs) / len(trs) if trs else 0

def vol_spike(volumes):
    if len(volumes) < 5: return 1.0
    avg = sum(volumes[:-1]) / len(volumes[:-1])
    return (volumes[-1] / avg) if avg > 0 else 1.0

# ============================================================================
# BOT
# ============================================================================

class LongBot:

    def __init__(self):
        fee_lbl = f"LÍMITE maker {COMISION_MAKER*100:.2f}%" if USE_LIMIT_ORDERS \
                  else f"MERCADO taker {COMISION_TAKER*100:.2f}%"
        log.info("=" * 65)
        log.info("  BOT LONGS PROFESIONAL v3.2.0")
        log.info("  SOLO LONGS | Fees reales | Limit orders | Cooldown | Filtros")
        log.info("=" * 65)
        log.info(f"  Modo:        {'AUTO' if AUTO_TRADING else 'SEÑALES'}")
        log.info(f"  Capital:     ${POSITION_SIZE} USDT | Leverage: {LEVERAGE}x")
        log.info(f"  TP/SL:       {TP_PCT}% / {SL_PCT}%  RR:{TP_PCT/SL_PCT:.1f}:1")
        log.info(f"  TP mín:      {TP_MIN_RENTABLE}% (cubre comisiones)")
        log.info(f"  Órdenes:     {fee_lbl}")
        log.info(f"  BTC filtro:  cae >{BTC_BEAR_BLOCK}% en 1h → bloquea LONG")
        log.info(f"  Cooldown:    15 min por par tras cierre")
        log.info("=" * 65)

        self.symbols        = []
        self.open_trades    = {}
        self._contracts     = {}
        self._cooldowns     = {}
        self._last_report   = datetime.now()
        self._btc_change_1h = 0.0
        self.stats = {'exec':0,'closed':0,'wins':0,'losses':0,'pnl':0.0}

        self._verify()
        self._load_contracts()
        self._get_symbols()

        self._tg(
            f"<b>📈 Bot LONGS v3.2.0 iniciado</b>\n"
            f"Capital: ${POSITION_SIZE} x{LEVERAGE} | TP:{TP_PCT}% SL:{SL_PCT}%\n"
            f"Fee: {fee_lbl} | Score≥{MIN_SCORE} | TP mín:{TP_MIN_RENTABLE}%\n"
            f"Filtros: BTC bear, cooldown 15min, hora liquidez"
        )

    # ---------------------------------------------------------------- setup

    def _verify(self):
        global AUTO_TRADING
        if not AUTO_TRADING: return
        if not BINGX_API_KEY or not BINGX_API_SECRET:
            AUTO_TRADING = False; return
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/balance', {}).json()
            if d.get('code') == 0:
                eq = d.get('data',{}).get('equity', d.get('data',{}).get('balance','?'))
                log.info(f"BingX OK | Balance: ${eq} USDT")
            else:
                log.error(f"BingX [{d.get('code')}]: {d.get('msg')}"); AUTO_TRADING = False
        except Exception as e:
            log.error(f"Error API: {e}"); AUTO_TRADING = False

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
            'OIL','BRENT','WTI','CRUDE','PETROLEUM',
            'GAS','GASOLINE','NATURAL','PETROL','DIESEL',
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
                        if vol < MIN_VOLUME or price < 0.0001: continue
                        items.append({'symbol':sym,'vol':vol})
                    except: continue
                items.sort(key=lambda x: x['vol'], reverse=True)
                self.symbols = [x['symbol'] for x in items[:MAX_SYMBOLS]]
                log.info(f"Pares: {len(self.symbols)} | Excluidos: {len(excl)}")
                return
        except Exception as e:
            log.warning(f"Error símbolos: {e}")
        self.symbols = ['BTC-USDT','ETH-USDT','SOL-USDT','BNB-USDT','XRP-USDT',
                        'DOGE-USDT','ADA-USDT','AVAX-USDT','LINK-USDT','DOT-USDT']

    # ---------------------------------------------------------------- datos

    def _klines(self, symbol, interval='5m', limit=60):
        try:
            d = requests.get(f"{BASE_URL}/openApi/swap/v3/quote/klines",
                params={'symbol':symbol,'interval':interval,'limit':limit}, timeout=10).json()
            if d.get('code') == 0 and d.get('data'):
                k = d['data']
                return ([float(x['close']) for x in k], [float(x['high']) for x in k],
                        [float(x['low']) for x in k],   [float(x['volume']) for x in k],
                        [float(x['open']) for x in k])
        except: pass
        return None, None, None, None, None

    def _ticker(self, symbol):
        try:
            d = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker",
                             params={'symbol':symbol}, timeout=8).json()
            if d.get('code') == 0 and d.get('data'):
                t = d['data']
                return {'price':  float(t.get('lastPrice',0)),
                        'change': float(t.get('priceChangePercent',0))}
        except: pass
        return None

    def _update_btc_trend(self):
        try:
            closes, *_ = self._klines('BTC-USDT', '1h', 3)
            if closes and len(closes) >= 2:
                self._btc_change_1h = (closes[-1] - closes[-2]) / closes[-2] * 100
        except: pass

    # ---------------------------------------------------------------- sizing
    # FIX v3.2: usa usdt_amount, usa ctval, sin cap hardcodeado de 10 USDT

    def _qty_contratos(self, symbol, price, usdt_amount=None):
        if usdt_amount is None:
            usdt_amount = POSITION_SIZE

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

        # Cap suave: nunca más de usdt_amount * 1.3
        if val > usdt_amount * 1.3:
            qty = round(math.floor((usdt_amount * 1.3 / ppc) / step) * step, prec)
            val = qty * ppc

        log.info(f"    qty_contratos: {qty} × ${ppc:.6f} = ${val:.2f} USDT")
        return qty, round(val, 4)

    # ---------------------------------------------------------------- filtros

    def _cooldown_ok(self, symbol):
        ts = self._cooldowns.get(symbol)
        return not (ts and (time.time() - ts) < 15 * 60)

    def _hora_ok(self):
        return datetime.utcnow().hour not in SKIP_HOURS_UTC

    # ---------------------------------------------------------------- análisis

    def analyze(self, symbol):
        if symbol in self.open_trades: return None
        if not self._cooldown_ok(symbol): return None
        if not self._hora_ok(): return None
        # FIX v3.2: bloquear LONG si BTC está cayendo fuerte
        if self._btc_change_1h <= -BTC_BEAR_BLOCK: return None

        closes, highs, lows, volumes, opens = self._klines(symbol, '5m', 60)
        if not closes or len(closes) < 26: return None

        ticker = self._ticker(symbol)
        if not ticker or ticker['price'] <= 0: return None

        price  = ticker['price']
        change = ticker['change']

        ema9  = calc_ema(closes, 9)
        ema21 = calc_ema(closes, 21)
        ema50 = calc_ema(closes, min(50, len(closes)))
        rsi   = calc_rsi(closes, 14)
        rsi_r = calc_rsi(closes[-20:], 10)   # RSI reciente más sensible
        ml, sg, hist = calc_macd(closes)
        bb_u, bb_m, bb_l = calc_bollinger(closes, 20)
        atr   = calc_atr(highs, lows, closes, 14)
        vs    = vol_spike(volumes)

        ema_bull = ema9 > ema21 > ema50
        ema_gap  = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
        trend_5  = (closes[-1] - closes[-6])  / closes[-6]  * 100 if len(closes) >= 6  else 0
        trend_10 = (closes[-1] - closes[-11]) / closes[-11] * 100 if len(closes) >= 11 else 0
        bb_pos   = (price - bb_l) / (bb_u - bb_l) if (bb_u - bb_l) > 0 else 0.5
        near_low     = price <= min(closes[-15:]) * 1.02 if len(closes) >= 15 else False
        green_candles = sum(1 for i in range(-4, 0) if opens and closes[i] > opens[i]) if opens else 0
        atr_pct      = (atr / price * 100) if price > 0 else 0

        rsi_min = min(rsi, rsi_r)

        ls, lr = 0, []

        # EMA alineación alcista
        if ema_bull:
            p = min(35, 28 + int(ema_gap * 4)) if ema_gap > 1.5 else min(28, 20 + int(ema_gap * 5))
            ls += p; lr.append(f"EMA+({p})")
        else:
            ls -= 10; lr.append("SinEMA(-10)")

        # RSI oversold (señal de compra fuerte)
        if   rsi_min < 25: ls += 38; lr.append(f"RSI{rsi_min:.0f}(38)")
        elif rsi_min < 35: ls += 28; lr.append(f"RSI{rsi_min:.0f}(28)")
        elif rsi_min < 45: ls += 18; lr.append(f"RSI{rsi_min:.0f}(18)")
        elif rsi_min < 55: ls += 8;  lr.append(f"RSI{rsi_min:.0f}(8)")
        elif rsi_min > 70: ls -= 20; lr.append(f"RSI{rsi_min:.0f}(-20)")  # sobrecomprado

        # MACD confirmación alcista
        if ml > sg and hist > 0:
            p = 22 if abs(hist) > abs(ml) * 0.35 else 15
            ls += p; lr.append(f"MACD+({p})")
        elif ml < 0 and hist < 0:
            ls -= 15; lr.append("MACD-(-15)")

        # Bollinger Bands (precio bajo = oportunidad de entrada)
        if   bb_pos <= 0.05: ls += 25; lr.append("BB_bot(25)")
        elif bb_pos <= 0.15: ls += 17; lr.append("BB_low(17)")
        elif bb_pos <= 0.30: ls += 8;  lr.append("BB_mid-(8)")
        elif bb_pos >= 0.80: ls -= 12; lr.append("BB_high(-12)")  # sobrecomprado

        # Volumen spike alcista
        if vs >= 2.0 and trend_5 > 0.3:
            p = min(18, int(vs*8)); ls += p; lr.append(f"VolCompra{vs:.1f}x({p})")
        elif vs >= 1.5:
            p = min(12, int(vs*6)); ls += p; lr.append(f"Vol{vs:.1f}x({p})")
        elif vs < 1.2:
            ls -= 8; lr.append("VolBajo(-8)")

        # Tendencia corto plazo
        if trend_5 > 1.5 and trend_10 > 2.5:   ls += 20; lr.append("Subida++(20)")
        elif trend_5 > 0.8:                      ls += 12; lr.append("Subida+(12)")
        elif trend_5 < -1.0:                     ls -= 15; lr.append("Caida(-15)")

        # Cambio 24h
        if   change > 6.0: p = min(15, int(change*2));   ls += p; lr.append(f"24h+{change:.1f}%({p})")
        elif change > 3.0: p = min(10, int(change*1.5)); ls += p; lr.append(f"24h+{change:.1f}%({p})")
        elif change < -4.0: ls -= 12; lr.append(f"24h{change:.1f}%(-12)")

        # Extras
        if near_low:          ls += 12; lr.append("NearLow(12)")
        if green_candles >= 3: ls += 10; lr.append(f"Verdes{green_candles}(10)")
        if atr_pct < 0.3:     ls -= 10; lr.append("ATRbajo(-10)")
        elif atr_pct > 1.5:   ls += 8;  lr.append(f"ATR{atr_pct:.1f}%(8)")

        # TP dinámico: al menos TP_PCT, al menos TP_MIN_RENTABLE
        tp_dyn = max(TP_PCT, TP_MIN_RENTABLE, min(TP_PCT * 2.5, atr_pct * 2.0))

        if ls >= MIN_SCORE and rsi_min <= 70:
            return {'price':price,'change':change,'score':ls,'reasons':' | '.join(lr),
                    'rsi':rsi,'vol':vs,'tp_pct':tp_dyn,'sl_pct':SL_PCT,
                    'bb_pos':round(bb_pos*100,1),'atr_pct':round(atr_pct,2)}
        return None

    # ---------------------------------------------------------------- órdenes

    def _place_long_entry(self, symbol, usdt_qty, price):
        """
        Entrada LONG (BUY):
        - LIMIT: precio por DEBAJO del mercado → espera en libro → maker 0.02%
        - Fallback MARKET con quantity (contratos)
        FIX v3.2: LIMIT usa quantity (contratos), NO quoteOrderQty
        """
        qty_c, _ = self._qty_contratos(symbol, price, usdt_qty)

        if USE_LIMIT_ORDERS and qty_c:
            # LONG=BUY: límite por debajo del mercado → espera → maker
            limit_price = round(price * (1 - LIMIT_OFFSET_PCT / 100), 8)
            d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol,'side':'BUY','positionSide':'LONG',
                'type':'LIMIT','price':str(limit_price),
                'quantity':str(qty_c),'timeInForce':'GTC',
            }).json()
            if d.get('code') == 0:
                log.info(f"  ENTRADA LÍMITE OK {qty_c} contratos @ ${limit_price:.6f} (~${usdt_qty}) maker")
                return d.get('data',{}).get('orderId','OK'), qty_c
            log.warning(f"  Límite falló [{d.get('code')}] — fallback mercado")

        # Fallback MARKET con quantity
        if not qty_c:
            qty_c, _ = self._qty_contratos(symbol, price, usdt_qty)
        if not qty_c:
            log.error(f"  No se pudo calcular qty_c"); return None, None

        d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol':symbol,'side':'BUY','positionSide':'LONG',
            'type':'MARKET','quantity':str(qty_c),
        }).json()
        if d.get('code') == 0:
            log.info(f"  ENTRADA MERCADO OK {qty_c} contratos (~${usdt_qty}) taker")
            return d.get('data',{}).get('orderId','OK'), qty_c

        log.error(f"  Entrada fallida [{d.get('code')}]: {d.get('msg')}")
        return None, None

    def _cond_order(self, symbol, qty_c, stop_price, otype):
        """
        FIX v3.2: TP como TAKE_PROFIT (límite/maker 0.02%) con fallback a mercado.
                  SL como STOP_MARKET (taker, garantiza ejecución).
        """
        if not qty_c or qty_c <= 0:
            log.error(f"  {otype} cancelado: qty_c inválido ({qty_c})")
            return False
        try:
            is_tp = "TAKE" in otype
            lbl   = "TP" if is_tp else "SL"

            if is_tp:
                params = {
                    'symbol':symbol,'side':'SELL','positionSide':'LONG',
                    'type':'TAKE_PROFIT',
                    'quantity':str(qty_c),
                    'price':str(round(stop_price, 8)),
                    'stopPrice':str(round(stop_price, 8)),
                    'timeInForce':'GTC',
                }
            else:
                params = {
                    'symbol':symbol,'side':'SELL','positionSide':'LONG',
                    'type':'STOP_MARKET',
                    'quantity':str(qty_c),
                    'stopPrice':str(round(stop_price, 8)),
                }

            d  = bingx_request('POST', '/openApi/swap/v2/trade/order', params).json()
            ok = d.get('code') == 0
            fee_lbl = "maker" if is_tp else "taker"
            if ok:
                log.info(f"  {lbl} ✅ fijado @ ${stop_price:.6f} (qty={qty_c}, {fee_lbl})")
            else:
                if is_tp:
                    log.warning(f"  TP límite rechazado [{d.get('code')}] — fallback TAKE_PROFIT_MARKET")
                    params2 = {
                        'symbol':symbol,'side':'SELL','positionSide':'LONG',
                        'type':'TAKE_PROFIT_MARKET','quantity':str(qty_c),
                        'stopPrice':str(round(stop_price, 8)),
                    }
                    d2 = bingx_request('POST', '/openApi/swap/v2/trade/order', params2).json()
                    ok = d2.get('code') == 0
                    if ok:  log.info(f"  TP ✅ (fallback mercado) @ ${stop_price:.6f}")
                    else:   log.error(f"  TP ❌ [{d2.get('code')}]: {d2.get('msg')}")
                else:
                    log.error(f"  {lbl} ❌ [{d.get('code')}]: {d.get('msg')}")
            return ok
        except Exception as e:
            log.error(f"  {otype} excepción: {e}"); return False

    def _close_long(self, symbol, t):
        qty_c = t.get('qty_c', 0)
        if qty_c and qty_c > 0:
            params = {'symbol':symbol,'side':'SELL','positionSide':'LONG',
                      'type':'MARKET','quantity':str(qty_c),'reduceOnly':'true'}
        else:
            usdt = t.get('usdt_qty', POSITION_SIZE)
            params = {'symbol':symbol,'side':'SELL','positionSide':'LONG',
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

    # ---------------------------------------------------------------- lifecycle

    def _esperar_posicion(self, symbol, timeout=30):
        """
        FIX v3.2: Espera hasta que BingX confirme la posición abierta.
        Devuelve (qty_real, entry_real) o (None, None) si timeout.
        Sin esto, el TP/SL se coloca antes de que exista la posición → BingX lo rechaza.
        """
        log.info(f"  Esperando confirmación de posición {symbol} (max {timeout}s)...")
        for i in range(timeout):
            try:
                d = bingx_request('GET', '/openApi/swap/v2/user/positions',
                                  {'symbol': symbol}).json()
                if d.get('code') == 0:
                    for p in (d.get('data') or []):
                        amt = float(p.get('positionAmt', 0) or 0)
                        if amt > 0:  # LONG = positivo
                            entry_real = float(p.get('avgPrice', 0) or
                                               p.get('entryPrice', 0) or 0)
                            qty_real   = abs(amt)
                            log.info(f"  ✅ Posición confirmada: qty={qty_real} entry=${entry_real:.6f} ({i+1}s)")
                            return qty_real, entry_real
            except Exception as e:
                log.debug(f"  _esperar_posicion {symbol}: {e}")
            time.sleep(1)
        log.warning(f"  ⏱ Timeout {timeout}s — posición no apareció en BingX")
        return None, None

    def _cancelar_ordenes_abiertas(self, symbol):
        """Cancela todas las órdenes pendientes de un símbolo (ej: LIMIT no ejecutada)."""
        try:
            d = bingx_request('GET', '/openApi/swap/v2/trade/openOrders',
                              {'symbol': symbol}).json()
            if d.get('code') == 0:
                for o in (d.get('data', {}).get('orders') or []):
                    oid = o.get('orderId', '')
                    if oid:
                        bingx_request('DELETE', '/openApi/swap/v2/trade/order',
                                      {'symbol': symbol, 'orderId': str(oid)})
                        log.info(f"  🗑 Orden {oid} cancelada")
        except Exception as e:
            log.debug(f"  _cancelar_ordenes {symbol}: {e}")

    def open_trade(self, symbol, sig):
        if not AUTO_TRADING:
            log.info(f"  [SEÑAL] LONG {symbol} score:{sig['score']:.0f}"); return False
        if symbol in self.open_trades: return False

        tiene, dir_bx = self._tiene_posicion(symbol)
        if tiene:
            log.info(f"  {symbol} ya tiene {dir_bx} en BingX — skip"); return False

        price    = sig['price']
        usdt_qty = round(max(POSITION_SIZE, MIN_TRADE), 2)
        tp_price = price * (1 + sig['tp_pct'] / 100)
        sl_price = price * (1 - sig['sl_pct'] / 100)

        log.info(f"\n  ➤ LONG {symbol}")
        log.info(f"  Score:{sig['score']:.0f} | RSI:{sig['rsi']:.0f} | BB:{sig['bb_pos']}%")
        log.info(f"  {sig['reasons']}")
        log.info(f"  Entry:${price:.6f} | Capital:${usdt_qty} | TP:{sig['tp_pct']:.2f}% SL:{sig['sl_pct']:.1f}%")

        oid, qty_c = self._place_long_entry(symbol, usdt_qty, price)
        if not oid:
            log.error(f"  No se pudo abrir {symbol}"); return False

        if not qty_c:
            qty_c, _ = self._qty_contratos(symbol, price, usdt_qty)

        if not qty_c:
            log.error(f"  No se pudo calcular qty_c para TP/SL de {symbol}")
            self.open_trades[symbol] = {
                'entry':price,'qty_c':0,'usdt_qty':usdt_qty,
                'tp':tp_price,'sl':sl_price,'tp_pct':sig['tp_pct'],'sl_pct':sig['sl_pct'],
                'highest':price,'order_id':oid,'tp_ok':False,'sl_ok':False,
                'opened_at':datetime.now(),'score':sig['score'],
            }
            self._tg(f"⚠️ LONG {symbol} abierto SIN TP/SL — qty_c=0. Fijar manual.")
            return True

        # FIX v3.2: esperar confirmación de posición antes de colocar TP/SL
        # Con LIMIT la posición no es inmediata — BingX rechaza TP/SL sin posición
        qty_real, entry_real = self._esperar_posicion(symbol, timeout=30)

        if qty_real is None:
            # LIMIT no se ejecutó en 30s → cancelar y entrar a MARKET
            log.warning(f"  LIMIT no ejecutada en 30s → cancelando, entrando a MERCADO")
            self._cancelar_ordenes_abiertas(symbol)
            time.sleep(0.5)
            d_mkt = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol,'side':'BUY','positionSide':'LONG',
                'type':'MARKET','quantity':str(qty_c),
            }).json()
            if d_mkt.get('code') == 0:
                log.info(f"  Fallback MERCADO OK")
                qty_real, entry_real = self._esperar_posicion(symbol, timeout=15)
            if qty_real is None:
                log.error(f"  No se pudo confirmar posición {symbol} — abortando TP/SL")
                self._tg(f"⚠️ LONG {symbol} abierto SIN TP/SL — posición no confirmada. Fijar manual.")
                self.open_trades[symbol] = {
                    'entry':price,'qty_c':qty_c,'usdt_qty':usdt_qty,
                    'tp':tp_price,'sl':sl_price,'tp_pct':sig['tp_pct'],'sl_pct':sig['sl_pct'],
                    'highest':price,'order_id':oid,'tp_ok':False,'sl_ok':False,
                    'opened_at':datetime.now(),'score':sig['score'],
                }
                return True

        # Recalcular TP/SL con precio de entrada REAL de BingX
        if entry_real and entry_real > 0:
            tp_price = entry_real * (1 + sig['tp_pct'] / 100)
            sl_price = entry_real * (1 - sig['sl_pct'] / 100)
            log.info(f"  Entry real BingX: ${entry_real:.6f} | TP:${tp_price:.6f} SL:${sl_price:.6f}")

        qty_para_tpsl = qty_real if qty_real else qty_c

        tp_ok = self._cond_order(symbol, qty_para_tpsl, tp_price, 'TAKE_PROFIT_MARKET')
        time.sleep(0.3)
        sl_ok = self._cond_order(symbol, qty_para_tpsl, sl_price, 'STOP_MARKET')

        if not tp_ok or not sl_ok:
            log.warning(f"  TP:{tp_ok} SL:{sl_ok} — reintentando en 3s")
            time.sleep(3)
            if not tp_ok: tp_ok = self._cond_order(symbol, qty_para_tpsl, tp_price, 'TAKE_PROFIT_MARKET')
            if not sl_ok: sl_ok = self._cond_order(symbol, qty_para_tpsl, sl_price, 'STOP_MARKET')
            if not tp_ok or not sl_ok:
                log.warning(f"  TP:{tp_ok} SL:{sl_ok} — tercer intento en 5s")
                time.sleep(5)
                if not tp_ok: tp_ok = self._cond_order(symbol, qty_para_tpsl, tp_price, 'TAKE_PROFIT_MARKET')
                if not sl_ok: sl_ok = self._cond_order(symbol, qty_para_tpsl, sl_price, 'STOP_MARKET')

        entry_final = entry_real if (entry_real and entry_real > 0) else price
        qty_final   = qty_real   if qty_real  else qty_c
        self.open_trades[symbol] = {
            'entry':entry_final,'qty_c':qty_final,'usdt_qty':usdt_qty,
            'tp':tp_price,'sl':sl_price,'tp_pct':sig['tp_pct'],'sl_pct':sig['sl_pct'],
            'highest':entry_final,'order_id':oid,'tp_ok':tp_ok,'sl_ok':sl_ok,
            'opened_at':datetime.now(),'score':sig['score'],
        }
        self.stats['exec'] += 1

        status_tp = "✅" if tp_ok else "❌ FIJARLO MANUAL"
        status_sl = "✅" if sl_ok else "❌ FIJARLO MANUAL"
        self._tg(
            f"<b>📈 LONG ABIERTO</b>\n<b>{symbol}</b> | Score:{sig['score']:.0f}/100\n"
            f"Entrada: ${price:.6f}\n"
            f"{status_tp} TP: ${tp_price:.6f} (+{sig['tp_pct']:.2f}%)\n"
            f"{status_sl} SL: ${sl_price:.6f} (-{sig['sl_pct']:.1f}%)\n"
            f"Capital: ${usdt_qty} x{LEVERAGE} = ${usdt_qty*LEVERAGE:.1f} USDT\n"
            f"Contratos: {qty_c} | RSI:{sig['rsi']:.0f} BB:{sig['bb_pos']}%\n"
            f"BTC 1h: {self._btc_change_1h:+.2f}%\n"
            f"{sig['reasons']}"
        )
        return True

    def close_trade(self, symbol, cur_price, reason):
        if symbol not in self.open_trades: return False
        t = self.open_trades[symbol]
        self._close_long(symbol, t)

        cambio  = (cur_price - t['entry']) / t['entry']
        pnl     = (t['usdt_qty'] * LEVERAGE * cambio) - (t['usdt_qty'] * LEVERAGE * COMISION_ACTUAL * 2)
        pnl_pct = (pnl / t['usdt_qty']) * 100

        self.stats['closed'] += 1
        self.stats['pnl']    += pnl
        if pnl > 0: self.stats['wins']   += 1
        else:        self.stats['losses'] += 1

        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        mins  = int((datetime.now() - t['opened_at']).total_seconds() / 60)
        emoji = "✅" if pnl > 0 else "❌"

        log.info(f"  {emoji} {reason} {symbol} PnL:${pnl:+.3f}({pnl_pct:+.1f}%) {mins}min")
        self._tg(
            f"<b>{emoji} LONG CERRADO — {reason}</b>\n<b>{symbol}</b>\n"
            f"PnL: ${pnl:+.3f} ({pnl_pct:+.1f}%)\n"
            f"Entry: ${t['entry']:.6f} → Exit: ${cur_price:.6f}\n"
            f"Duración: {mins} min\n"
            f"<b>Total: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}% ({self.stats['wins']}W/{self.stats['losses']}L)</b>"
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
                    tk  = self._ticker(sym)
                    cur = tk['price'] if tk else t['entry']
                    cambio  = (cur - t['entry']) / t['entry']
                    pnl     = (t['usdt_qty'] * LEVERAGE * cambio) - (t['usdt_qty'] * LEVERAGE * COMISION_ACTUAL * 2)
                    pnl_pct = (pnl / t['usdt_qty']) * 100
                    self.stats['closed'] += 1; self.stats['pnl'] += pnl
                    if pnl >= 0: self.stats['wins']   += 1
                    else:        self.stats['losses'] += 1
                    total = self.stats['wins'] + self.stats['losses']
                    wr    = self.stats['wins'] / total * 100 if total else 0
                    emoji = "✅" if pnl >= 0 else "❌"
                    mins  = int((datetime.now() - t['opened_at']).total_seconds() / 60)
                    self._tg(f"<b>{emoji} LONG cerrado BingX</b>\n<b>{sym}</b>\n"
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
                cur     = tk['price']
                pnl_pct = (cur - t['entry']) / t['entry'] * 100

                # Trailing stop: activa con +0.5%, protege 60% de ganancia
                if TRAILING and cur > t['highest']:
                    t['highest'] = cur
                    if pnl_pct >= 0.5:
                        profit = cur - t['entry']
                        new_sl = t['entry'] + profit * 0.60
                        if new_sl > t['sl']:
                            t['sl'] = new_sl
                            log.info(f"  Trailing SL {sym}: ${new_sl:.6f} (protege {pnl_pct*0.6:.1f}%)")

                if abs(pnl_pct) > 0.3:
                    log.info(f"  {sym}: {pnl_pct:+.2f}% | cur:${cur:.6f}")

                if cur >= t['tp']:  self.close_trade(sym, cur, "TAKE PROFIT")
                elif cur <= t['sl']: self.close_trade(sym, cur, "STOP LOSS")
            except Exception as e:
                log.debug(f"Monitor {sym}: {e}")

    def _reporte_horario(self):
        if datetime.now() - self._last_report < timedelta(hours=1): return
        self._last_report = datetime.now()
        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        self._tg(
            f"<b>📊 Reporte horario</b>\n"
            f"PnL: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}%\n"
            f"({self.stats['wins']}W/{self.stats['losses']}L | {self.stats['closed']} trades)\n"
            f"Abiertos: {len(self.open_trades)}/{MAX_TRADES} | BTC 1h:{self._btc_change_1h:+.2f}%"
        )

    def _tg(self, msg):
        try:
            if TELEGRAM_TOKEN and TELEGRAM_CHAT:
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={'chat_id':TELEGRAM_CHAT,'text':msg,'parse_mode':'HTML'}, timeout=6)
        except: pass

    # ---------------------------------------------------------------- loop

    async def run(self):
        log.info("\n▶  Bot LONGS v3.2.0 arrancado\n")
        iteration, last_refresh = 0, 0
        while True:
            try:
                iteration += 1
                if time.time() - last_refresh > 600:
                    self._get_symbols(); last_refresh = time.time()

                self._update_btc_trend()

                total = self.stats['wins'] + self.stats['losses']
                wr    = self.stats['wins'] / total * 100 if total else 0
                btc_st = "⚠️ BLOQUEADO" if self._btc_change_1h <= -BTC_BEAR_BLOCK else "OK"
                hora_st = "🌙 HORA BAJA" if not self._hora_ok() else "☀️"

                log.info(f"\n{'='*65}")
                log.info(f"  #{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                         f"Abiertos:{len(self.open_trades)}/{MAX_TRADES} | "
                         f"PnL:${self.stats['pnl']:+.3f} | WR:{wr:.1f}%")
                log.info(f"  BTC 1h:{self._btc_change_1h:+.2f}% {btc_st} | {hora_st}")
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
                            log.info(f"  ★ {sym} score:{sig['score']:.0f} RSI:{sig['rsi']:.0f}")
                            self.open_trade(sym, sig)
                        await asyncio.sleep(0.12)
                        if (i+1) % 25 == 0:
                            log.info(f"  ...{i+1}/{len(self.symbols)} analizados")
                    log.info(f"\n  {len(self.symbols)} pares | {found} señales")
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
        await LongBot().run()
    except Exception as e:
        log.error(f"Error fatal: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Terminado")
