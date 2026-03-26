#!/usr/bin/env python3
"""
BOT SHORTS PROFESIONAL v3.3.1 — CAPITAL MÍNIMO
════════════════════════════════════════════════════════════════
NUEVO: Verificación de capital mínimo 6 USDT antes de operar

El bot NO abrirá posiciones si el balance disponible es < 6 USDT.
Esto evita trades microscópicos que no son rentables.

FIXES v3.3:
  FIX-1   POSITION_SIZE GARANTIZADO EN 8 USDT
  FIX-2   MIN_SCORE CORREGIDO
  FIX-3   LEVERAGE FIJO EN 3x
  FIX-4   TP/SL MEJORADO PARA RENTABILIDAD (RR=2.5:1)
════════════════════════════════════════════════════════════════
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

AUTO_TRADING  = clean('AUTO_TRADING_ENABLED',  'true',  'bool')

# ── Capital mínimo requerido ──────────────────────────────────
MIN_CAPITAL_REQUIRED = 6.0  # USDT — No operar si balance < 6 USDT
# ─────────────────────────────────────────────────────────────

# ── FIX-1: Tamaño garantizado ─────────────────────────────────
POSITION_SIZE  = clean('MAX_POSITION_SIZE',     '8',    'float')
FORCE_MIN_USDT = clean('FORCE_MIN_USDT',        '8',    'float')
MIN_TRADE      = clean('MIN_TRADE_USDT',         '8',   'float')
# ─────────────────────────────────────────────────────────────

LEVERAGE      = clean('LEVERAGE',                '3',   'int')
# FIX-4: TP/SL mejorado para RR 2.5:1
TP_PCT        = clean('TAKE_PROFIT_PCT',         '2.8', 'float')
SL_PCT        = clean('STOP_LOSS_PCT',           '1.1', 'float')
MAX_TRADES    = clean('MAX_OPEN_TRADES',         '2',   'int')
INTERVAL      = clean('CHECK_INTERVAL',         '300',  'int')
MIN_VOLUME    = clean('MIN_VOLUME_24H',     '10000000', 'float')
MAX_SYMBOLS   = clean('MAX_SYMBOLS_TO_ANALYZE', '40',   'int')
# FIX-2: MIN_SCORE corregido
MIN_SCORE     = clean('MIN_SCORE',              '82',   'float')
TRAILING      = clean('TRAILING_STOP_ENABLED', 'true',  'bool')
USE_LIMIT_ORDERS   = clean('USE_LIMIT_ORDERS',      'true', 'bool')
MAX_LOSS_PCT       = clean('MAX_LOSS_PCT',           '8.0',  'float')
SL_LIMIT_OFFSET    = clean('SL_LIMIT_OFFSET_PCT',   '0.05', 'float') / 100

# ── Filtro BTC multiframe ────────────────────────────────────
BTC_BULL_BLOCK_1H  = clean('BTC_BULL_BLOCK_1H',  '0.5',  'float')
BTC_BULL_BLOCK_4H  = clean('BTC_BULL_BLOCK_4H',  '1.0',  'float')
BTC_BEAR_BONUS_1H  = clean('BTC_BEAR_BONUS_1H', '-0.3',  'float')

# ── Parámetros MAE / Patrones ────────────────────────────────
MAE_PERIOD    = clean('MAE_PERIOD',     '20',  'int')
MAE_PCT       = clean('MAE_PCT',        '2.0', 'float')
MAE_EXTREME   = clean('MAE_EXTREME',    '1.8', 'float')
PATTERN_SCORE = clean('PATTERN_SCORE', 'true', 'bool')
REGIME_FILTER = clean('REGIME_FILTER', 'true', 'bool')

LIMIT_OFFSET_PCT = 0.05
SKIP_HOURS_UTC   = {0, 1}
COOLDOWN_MINS    = 30

BASE_URL = "https://open-api.bingx.com"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

COMISION_MAKER  = 0.0002
COMISION_TAKER  = 0.0005
COMISION_ACTUAL = COMISION_MAKER if USE_LIMIT_ORDERS else COMISION_TAKER
TP_MIN_RENTABLE = round((COMISION_ACTUAL / LEVERAGE + 0.002) * 100, 3)

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
# INDICADORES BASE
# ============================================================================

def calc_ema(prices, period):
    if not prices: return 0
    if len(prices) < period: return sum(prices) / len(prices)
    k, e = 2 / (period + 1), prices[0]
    for p in prices[1:]: e = p * k + e * (1 - k)
    return e

def calc_sma(prices, period):
    if len(prices) < period: return sum(prices) / len(prices)
    return sum(prices[-period:]) / period

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
# MOVING AVERAGE ENVELOPES
# ============================================================================

def calc_mae(prices, period=20, pct=2.0):
    if len(prices) < period:
        ma = sum(prices) / len(prices)
    else:
        ma = calc_sma(prices, period)
    factor = pct / 100.0
    upper = ma * (1 + factor)
    lower = ma * (1 - factor)
    price = prices[-1]
    band_width = upper - lower
    if band_width > 0:
        position = (price - lower) / band_width
    else:
        position = 0.5
    return upper, ma, lower, position

def mae_regime_score(prices, period=20, pct=2.0):
    upper, ma, lower, pos = calc_mae(prices, period, pct)
    if len(prices) >= period + 5:
        ma_old = calc_sma(prices[:-5], period)
        ma_slope_pct = (ma - ma_old) / ma_old * 100 if ma_old > 0 else 0
    else:
        ma_slope_pct = 0

    is_uptrend   = ma_slope_pct >  0.5
    is_downtrend = ma_slope_pct < -0.3
    is_ranging   = not is_uptrend and not is_downtrend

    score = 0
    desc  = ""

    if is_ranging:
        regime = "RANGO"
        if pos >= 0.95:    score = 30; desc = f"MAE_TOP_RANGO(30) pos:{pos:.2f}"
        elif pos >= 0.80:  score = 20; desc = f"MAE_ALTO_RANGO(20) pos:{pos:.2f}"
        elif pos >= 0.60:  score = 8;  desc = f"MAE_MEDIO+(8) pos:{pos:.2f}"
        elif pos <= 0.30:  score = -15; desc = f"MAE_BAJO(-15) pos:{pos:.2f}"
        else:              score = 0;   desc = f"MAE_NEUTRAL(0) pos:{pos:.2f}"
    elif is_downtrend:
        regime = "BAJISTA"
        if pos >= 0.70:    score = 25; desc = f"MAE_REBOTE_BAJISTA(25) pos:{pos:.2f}"
        elif pos >= 0.50:  score = 15; desc = f"MAE_MEDIO_BAJISTA(15) pos:{pos:.2f}"
        else:              score = 5;  desc = f"MAE_BAJISTA_OK(5) pos:{pos:.2f}"
    else:
        regime = "ALCISTA"
        if pos > 1.05:     score = -25; desc = f"MAE_IMPULSO_ALCISTA(-25) pos:{pos:.2f}"
        elif pos > 0.90:   score = -12; desc = f"MAE_ALCISTA_FUERTE(-12) pos:{pos:.2f}"
        elif pos < 0.40:   score = 10;  desc = f"MAE_RETROCESO_ALCISTA(10) pos:{pos:.2f}"
        else:              score = -5;  desc = f"MAE_ALCISTA(-5) pos:{pos:.2f}"

    return score, desc, regime, pos, upper, ma, lower

# ============================================================================
# DETECCIÓN DE PATRONES CHARTISTAS
# ============================================================================

def find_pivots(prices, window=3):
    highs, lows = [], []
    for i in range(window, len(prices) - window):
        if all(prices[i] >= prices[i-j] and prices[i] >= prices[i+j] for j in range(1, window+1)):
            highs.append((i, prices[i]))
        if all(prices[i] <= prices[i-j] and prices[i] <= prices[i+j] for j in range(1, window+1)):
            lows.append((i, prices[i]))
    return highs, lows

def detect_double_top(closes, highs_list, tolerance=0.015):
    if len(highs_list) < 2: return False, 0, ""
    h1_idx, h1_val = highs_list[-2]
    h2_idx, h2_val = highs_list[-1]
    diff = abs(h1_val - h2_val) / max(h1_val, h2_val)
    if diff > tolerance: return False, 0, ""
    valley_prices = closes[h1_idx:h2_idx]
    if not valley_prices: return False, 0, ""
    valley = min(valley_prices)
    neck_drop = (max(h1_val, h2_val) - valley) / max(h1_val, h2_val)
    if neck_drop < 0.01: return False, 0, ""
    cur = closes[-1]
    near_top = cur >= h2_val * 0.985
    at_neck  = cur <= valley * 1.005 and cur >= valley * 0.98
    if near_top: return True, 35, f"DoubleTop_TOP(35)"
    if at_neck:  return True, 40, f"DoubleTop_NECK(40)"
    return False, 0, ""

def detect_head_shoulders(closes, highs_list, tolerance=0.02):
    if len(highs_list) < 3: return False, 0, ""
    ls_idx, ls_val = highs_list[-3]
    h_idx,  h_val  = highs_list[-2]
    rs_idx, rs_val = highs_list[-1]
    if not (h_val > ls_val and h_val > rs_val): return False, 0, ""
    shoulder_diff = abs(ls_val - rs_val) / max(ls_val, rs_val)
    if shoulder_diff > tolerance: return False, 0, ""
    cur = closes[-1]
    if cur <= rs_val * 0.995: return True, 38, f"H&S_BREAK(38)"
    if cur >= rs_val * 0.99:  return True, 28, f"H&S_SHOULDER(28)"
    return False, 0, ""

def detect_rising_wedge(closes, highs_list, lows_list, min_points=3):
    if len(highs_list) < min_points or len(lows_list) < min_points:
        return False, 0, ""
    recent_highs = highs_list[-min_points:]
    recent_lows  = lows_list[-min_points:]
    def slope(points):
        if len(points) < 2: return 0
        x1, y1 = points[0]; x2, y2 = points[-1]
        return (y2 - y1) / (x2 - x1) if x2 != x1 else 0
    s_h = slope(recent_highs)
    s_l = slope(recent_lows)
    if s_h <= 0 or s_l <= 0: return False, 0, ""
    if not (s_l > s_h * 0.7): return False, 0, ""
    cur = closes[-1]
    last_high = recent_highs[-1][1]
    last_low  = recent_lows[-1][1]
    range_sz  = last_high - last_low
    if range_sz > 0:
        pos_in_wedge = (cur - last_low) / range_sz
        if pos_in_wedge >= 0.75:
            return True, 30, f"RisingWedge_TOP(30)"
    return False, 0, ""

def detect_bearish_flag(closes, volumes, highs_list):
    if len(closes) < 20: return False, 0, ""
    mast_start = -12; mast_end = -6
    mast_change = (closes[mast_end] - closes[mast_start]) / closes[mast_start] * 100
    if mast_change > -2.5: return False, 0, ""
    flag_prices = closes[-5:]
    flag_range  = (max(flag_prices) - min(flag_prices)) / min(flag_prices) * 100
    if flag_range > 2.0: return False, 0, ""
    if len(volumes) >= 10:
        vol_mast = sum(volumes[-10:-5]) / 5
        vol_flag = sum(volumes[-5:]) / 5
        vol_ok   = vol_flag < vol_mast
    else:
        vol_ok = True
    if vol_ok: return True, 32, f"BearishFlag(32)"
    return True, 18, f"BearishFlag_noVol(18)"

def detect_support_breakdown(closes, lows_list, tolerance=0.008):
    if len(lows_list) < 2 or len(closes) < 5: return False, 0, ""
    recent_lows_vals = [v for _, v in lows_list[-6:]]
    if len(recent_lows_vals) < 2: return False, 0, ""
    support_level = None
    for i, low in enumerate(recent_lows_vals):
        touches = sum(1 for l in recent_lows_vals if abs(l - low) / low < tolerance)
        if touches >= 2:
            support_level = low; break
    if not support_level: return False, 0, ""
    cur = closes[-1]
    if cur < support_level * (1 - tolerance):
        return True, 28, f"SupportBreak(28)"
    return False, 0, ""

def scan_patterns(closes, highs_raw, lows_raw, volumes):
    if not closes or len(closes) < 20:
        return 0, []
    highs_list, lows_list = find_pivots(closes, window=3)
    patterns = []; total = 0
    for fn, args in [
        (detect_double_top,       (closes, highs_list)),
        (detect_head_shoulders,   (closes, highs_list)),
        (detect_rising_wedge,     (closes, highs_list, lows_list)),
        (detect_bearish_flag,     (closes, volumes, highs_list)),
        (detect_support_breakdown,(closes, lows_list)),
    ]:
        ok, sc, dsc = fn(*args)
        if ok: patterns.append(dsc); total += sc
    return total, patterns

# ============================================================================
# DETECTOR DE RÉGIMEN DE MERCADO
# ============================================================================

def detect_market_regime(closes, period=20):
    if len(closes) < period + 5:
        return "UNKNOWN"
    ma_now  = calc_sma(closes, period)
    ma_old  = calc_sma(closes[:-5], period)
    slope_pct = (ma_now - ma_old) / ma_old * 100 if ma_old > 0 else 0
    deviations = [abs(c - ma_now) / ma_now * 100 for c in closes[-period:]]
    avg_dev = sum(deviations) / len(deviations)
    if slope_pct > 0.6 and avg_dev > 0.8:   return "TREND_UP"
    elif slope_pct < -0.4 and avg_dev > 0.6: return "TREND_DOWN"
    else:                                     return "RANGING"

# ============================================================================
# BOT
# ============================================================================

class ShortBot:

    def __init__(self):
        fee_lbl = f"LÍMITE maker {COMISION_MAKER*100:.2f}%" if USE_LIMIT_ORDERS \
                  else f"MERCADO taker {COMISION_TAKER*100:.2f}%"
        rr = round(TP_PCT / SL_PCT, 2)
        breakeven_wr = round(1 / (1 + rr) * 100, 1)
        log.info("=" * 70)
        log.info("  BOT SHORTS PROFESIONAL v3.3.1")
        log.info("  NUEVO: Capital mínimo $6 USDT requerido")
        log.info("=" * 70)
        log.info(f"  Modo:        {'AUTO' if AUTO_TRADING else 'SEÑALES'}")
        log.info(f"  Capital mín: ${MIN_CAPITAL_REQUIRED} USDT (no opera si balance < $6)")
        log.info(f"  Position:    ${POSITION_SIZE} USDT (mín absoluto: ${FORCE_MIN_USDT})")
        log.info(f"  Leverage:    {LEVERAGE}x")
        log.info(f"  TP/SL:       {TP_PCT}% / {SL_PCT}%  RR:{rr}:1")
        log.info(f"  Break-even:  WR > {breakeven_wr}% (con RR {rr}:1)")
        log.info(f"  TP mín:      {TP_MIN_RENTABLE}% (cubre comisiones)")
        log.info(f"  Órdenes:     {fee_lbl}")
        log.info(f"  Filtro BTC:  bloqueo si 1h>{BTC_BULL_BLOCK_1H}% Y 4h>{BTC_BULL_BLOCK_4H}%")
        log.info(f"  Score mín:   {MIN_SCORE} | Cooldown: {COOLDOWN_MINS}min")
        log.info(f"  Volumen:     {MIN_VOLUME/1e6:.0f}M USDT mínimo")
        log.info("=" * 70)

        self.symbols         = []
        self.open_trades     = {}
        self._contracts      = {}
        self._cooldowns      = {}
        self._last_report    = datetime.now()
        self._btc_change_1h  = 0.0
        self._btc_change_4h  = 0.0
        self._btc_blocked    = False
        self._last_balance_check = 0
        self._available_balance  = 0
        self.stats = {'exec':0,'closed':0,'wins':0,'losses':0,'pnl':0.0}

        self._verify()
        self._load_contracts()
        self._get_symbols()
        self._tg(
            f"<b>🔴 Bot SHORTS v3.3.1 iniciado</b>\n"
            f"NUEVO: Capital mín $6 USDT requerido\n"
            f"Position: ${POSITION_SIZE}(mín${FORCE_MIN_USDT}) x{LEVERAGE} | TP:{TP_PCT}% SL:{SL_PCT}%\n"
            f"RR {rr}:1 | Break-even WR>{breakeven_wr}%\n"
            f"Score≥{MIN_SCORE} | Vol≥{MIN_VOLUME/1e6:.0f}M | Cooldown:{COOLDOWN_MINS}min"
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
                data = d.get('data', {})
                eq = data.get('equity', data.get('balance','?'))
                available = float(data.get('availableMargin', eq))
                log.info(f"BingX OK | Balance: ${eq} USDT | Disponible: ${available:.2f} USDT")
                
                if available < MIN_CAPITAL_REQUIRED:
                    log.warning(f"⚠️ CAPITAL INSUFICIENTE: ${available:.2f} USDT")
                    log.warning(f"⚠️ Se requieren mínimo ${MIN_CAPITAL_REQUIRED} USDT para operar")
                    log.warning(f"⚠️ Bot en ESPERA hasta que deposites fondos suficientes")
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
                        if vol < MIN_VOLUME or price < 0.000001: continue
                        items.append({'symbol':sym,'vol':vol})
                    except: continue
                items.sort(key=lambda x: x['vol'], reverse=True)
                self.symbols = [x['symbol'] for x in items[:MAX_SYMBOLS]]
                log.info(f"Pares: {len(self.symbols)} | Excluidos no-cripto: {len(excl)}")
                return
        except Exception as e:
            log.warning(f"Error símbolos: {e}")
        self.symbols = ['BTC-USDT','ETH-USDT','SOL-USDT','BNB-USDT','XRP-USDT',
                        'DOGE-USDT','ADA-USDT','AVAX-USDT','LINK-USDT','DOT-USDT']

    # ---------------------------------------------------------------- datos

    def _klines(self, symbol, interval='5m', limit=80):
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
                return {'price': float(t.get('lastPrice',0)),
                        'change': float(t.get('priceChangePercent',0))}
        except: pass
        return None

    def _update_btc_trend(self):
        try:
            closes_1h, *_ = self._klines('BTC-USDT', '1h', 5)
            if closes_1h and len(closes_1h) >= 3:
                self._btc_change_1h = (closes_1h[-1] - closes_1h[-3]) / closes_1h[-3] * 100
        except: pass
        try:
            closes_4h, *_ = self._klines('BTC-USDT', '4h', 5)
            if closes_4h and len(closes_4h) >= 3:
                self._btc_change_4h = (closes_4h[-1] - closes_4h[-3]) / closes_4h[-3] * 100
        except: pass
        self._btc_blocked = (
            self._btc_change_1h >= BTC_BULL_BLOCK_1H and
            self._btc_change_4h >= BTC_BULL_BLOCK_4H
        )

    def _btc_regime_ok(self):
        if self._btc_blocked:
            log.info(f"  BTC alcista 1h:{self._btc_change_1h:+.2f}% "
                     f"4h:{self._btc_change_4h:+.2f}% — BLOQUEANDO todos los shorts")
            return False
        return True

    # ---------------------------------------------------------------- Balance

    def _get_available_balance(self):
        """Obtiene el balance disponible en USDT y lo cachea por 60s"""
        now = time.time()
        if now - self._last_balance_check < 60:
            return self._available_balance
        
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/balance', {}).json()
            if d.get('code') == 0:
                data = d.get('data', {})
                # Intentar obtener availableMargin primero, luego equity
                available = float(data.get('availableMargin', data.get('equity', 0)))
                self._available_balance = available
                self._last_balance_check = now
                return available
        except Exception as e:
            log.warning(f"Error obteniendo balance: {e}")
        
        return self._available_balance

    # ---------------------------------------------------------------- sizing garantizado

    def _qty_contratos(self, symbol, price, usdt_amount=None):
        """
        FIX-1: usdt_amount nunca puede ser menor que FORCE_MIN_USDT.
        Esto garantiza que cada trade usa al menos 8 USDT notional.
        """
        if usdt_amount is None:
            usdt_amount = POSITION_SIZE
        # GARANTÍA ABSOLUTA: nunca menos de FORCE_MIN_USDT
        usdt_amount = max(usdt_amount, FORCE_MIN_USDT)

        info  = self._contracts.get(symbol, {'step':1.0,'prec':2,'ctval':1.0})
        step  = max(info['step'], 0.0001)
        prec  = info['prec']
        ctval = info.get('ctval', 1.0)
        ppc   = price * ctval if ctval != 1.0 else price
        if ppc <= 0: return None, 0
        qty = round(math.ceil(usdt_amount / ppc / step) * step, prec)
        val = qty * ppc
        i = 0
        # Asegurar mínimo de FORCE_MIN_USDT (no MIN_TRADE que puede ser menor)
        min_val = max(MIN_TRADE, FORCE_MIN_USDT)
        while val < min_val and i < 500:
            qty += step; qty = round(qty, prec); val = qty * ppc; i += 1
        if val > usdt_amount * 1.3:
            qty = round(math.floor((usdt_amount * 1.3 / ppc) / step) * step, prec)
            val = qty * ppc
            # Después de recortar, re-verificar mínimo
            if val < min_val:
                qty = round(math.ceil(min_val / ppc / step) * step, prec)
                val = qty * ppc
        log.info(f"    qty_contratos: {qty} × ${ppc:.6f} = ${val:.2f} USDT (mín garantizado: ${min_val})")
        return qty, round(val, 4)

    # ---------------------------------------------------------------- cooldown / filtros

    def _cooldown_ok(self, symbol):
        ts = self._cooldowns.get(symbol)
        return not (ts and (time.time() - ts) < COOLDOWN_MINS * 60)

    def _hora_ok(self):
        return datetime.utcnow().hour not in SKIP_HOURS_UTC

    def _correlacion_ok(self):
        if not self.open_trades:
            return True
        trades_en_perdida = 0
        for sym, t in self.open_trades.items():
            tk = self._ticker(sym)
            if not tk: continue
            cur = tk['price']
            pnl_pct = (t['entry'] - cur) / t['entry'] * 100
            if pnl_pct < -0.3:
                trades_en_perdida += 1
        if trades_en_perdida >= 2:
            log.info(f"  Correlación: {trades_en_perdida} posiciones en pérdida — no abrir más shorts")
            return False
        return True

    # ---------------------------------------------------------------- análisis principal

    def analyze(self, symbol):
        if symbol in self.open_trades or not self._cooldown_ok(symbol): return None
        if not self._hora_ok(): return None
        if not self._btc_regime_ok(): return None

        closes, highs, lows, volumes, opens = self._klines(symbol, '5m', 80)
        if not closes or len(closes) < 30: return None

        ticker = self._ticker(symbol)
        if not ticker or ticker['price'] <= 0: return None

        price  = ticker['price']
        change = ticker['change']

        ema9  = calc_ema(closes, 9)
        ema21 = calc_ema(closes, 21)
        ema50 = calc_ema(closes, min(50, len(closes)))
        rsi   = calc_rsi(closes, 14)
        rsi_r = calc_rsi(closes[-20:], 10)
        ml, sg, hist = calc_macd(closes)
        bb_u, bb_m, bb_l = calc_bollinger(closes, 20)
        atr   = calc_atr(highs, lows, closes, 14)
        vs    = vol_spike(volumes)

        ema_gap  = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
        trend_5  = (closes[-1] - closes[-6])  / closes[-6]  * 100 if len(closes) >= 6  else 0
        trend_10 = (closes[-1] - closes[-11]) / closes[-11] * 100 if len(closes) >= 11 else 0
        bb_pos   = (price - bb_l) / (bb_u - bb_l) if (bb_u - bb_l) > 0 else 0.5
        near_high    = price >= max(closes[-15:]) * 0.98 if len(closes) >= 15 else False
        red_candles  = sum(1 for i in range(-4, 0) if opens and closes[i] < opens[i]) if opens else 0
        atr_pct      = (atr / price * 100) if price > 0 else 0

        mae_score, mae_desc, mae_regime, mae_pos, mae_upper, mae_ma, mae_lower = \
            mae_regime_score(closes, MAE_PERIOD, MAE_PCT)

        regime = detect_market_regime(closes, MAE_PERIOD)

        pattern_total = 0
        pattern_list  = []
        if PATTERN_SCORE:
            pattern_total, pattern_list = scan_patterns(closes, highs, lows, volumes)

        if REGIME_FILTER and regime == "TREND_UP" and not pattern_list:
            if self._btc_change_1h < 0:
                pass
            else:
                return None

        if not (ema9 < ema21 < ema50):
            if not pattern_list or pattern_total < 30:
                return None

        score_min = MIN_SCORE + (15 if self._btc_change_1h > 0.5 else 0)
        ss, sr = 0, []

        p = min(35, 28 + int(ema_gap * 4)) if ema_gap > 1.5 else min(28, 20 + int(ema_gap * 5))
        ss += p; sr.append(f"EMA({p})")

        rsi_max = max(rsi, rsi_r)
        if   rsi_max > 82: ss += 38; sr.append(f"RSI{rsi_max:.0f}(38)")
        elif rsi_max > 76: ss += 30; sr.append(f"RSI{rsi_max:.0f}(30)")
        elif rsi_max > 70: ss += 20; sr.append(f"RSI{rsi_max:.0f}(20)")
        elif rsi_max > 65: ss += 10; sr.append(f"RSI{rsi_max:.0f}(10)")
        else:              ss -= 20; sr.append(f"RSI{rsi_max:.0f}(-20)")

        if ml < sg and hist < 0:
            p = 22 if abs(hist) > abs(ml) * 0.35 else 15
            ss += p; sr.append(f"MACD-({p})")
        elif ml > 0 and hist > 0:
            ss -= 15; sr.append("MACD+(-15)")

        if   bb_pos >= 0.95: ss += 25; sr.append("BB_top(25)")
        elif bb_pos >= 0.85: ss += 17; sr.append("BB_high(17)")
        elif bb_pos >= 0.70: ss += 8;  sr.append("BB_mid+(8)")
        elif bb_pos <  0.40: ss -= 12; sr.append("BB_low(-12)")

        if vs >= 2.0 and trend_5 < -0.3:
            p = min(18, int(vs*8)); ss += p; sr.append(f"VolVenta{vs:.1f}x({p})")
        elif vs >= 1.5:
            p = min(12, int(vs*6)); ss += p; sr.append(f"Vol{vs:.1f}x({p})")
        elif vs < 1.2:
            ss -= 8; sr.append("VolBajo(-8)")

        if trend_5 < -1.5 and trend_10 < -2.5: ss += 20; sr.append("Bajada--(20)")
        elif trend_5 < -0.8:                    ss += 12; sr.append("Bajada-(12)")
        elif trend_5 > 1.0:                     ss -= 15; sr.append("Subida(-15)")

        if   change > 6.0: p = min(15, int(change*2));   ss += p; sr.append(f"24h+{change:.1f}%({p})")
        elif change > 3.0: p = min(10, int(change*1.5)); ss += p; sr.append(f"24h+{change:.1f}%({p})")
        elif change < -4.0: ss -= 12; sr.append(f"24h{change:.1f}%(-12)")

        if near_high:        ss += 12; sr.append("NearHigh(12)")
        if red_candles >= 3: ss += 10; sr.append(f"Rojas{red_candles}(10)")
        if atr_pct < 0.3:    ss -= 10; sr.append("ATRbajo(-10)")
        elif atr_pct > 1.5:  ss += 8;  sr.append(f"ATR{atr_pct:.1f}%(8)")

        if self._btc_change_1h <= BTC_BEAR_BONUS_1H:
            ss += 8; sr.append(f"BTC_bajando(+8)")

        if mae_score != 0:
            ss += mae_score; sr.append(mae_desc)

        if regime == "TREND_DOWN":
            ss += 10; sr.append("RegimeBajista(+10)")
        elif regime == "RANGING":
            ss += 5;  sr.append("RegimeRango(+5)")
        elif regime == "TREND_UP":
            ss -= 10; sr.append("RegimeAlcista(-10)")

        if pattern_total > 0:
            ss += pattern_total
            for p_name in pattern_list:
                sr.append(p_name)

        tp_dyn = max(TP_PCT, TP_MIN_RENTABLE, min(TP_PCT*2.5, atr_pct*2.0))
        if pattern_total >= 30:
            tp_dyn = max(tp_dyn, TP_PCT * 1.5)

        if ss >= score_min:
            return {
                'price':price,'change':change,'score':ss,'reasons':' | '.join(sr),
                'rsi':rsi,'vol':vs,'tp_pct':tp_dyn,'sl_pct':SL_PCT,
                'bb_pos':round(bb_pos*100,1),'atr_pct':round(atr_pct,2),
                'score_min':score_min,'regime':regime,'mae_regime':mae_regime,
                'mae_pos':round(mae_pos*100,1),'patterns':pattern_list,
            }
        return None

    # ---------------------------------------------------------------- órdenes

    def _place_short_entry(self, symbol, usdt_qty, price):
        # FIX-1: Doble garantía en la entrada
        usdt_qty = max(usdt_qty, FORCE_MIN_USDT)

        qty_c, qty_val = self._qty_contratos(symbol, price, usdt_qty)
        log.info(f"  Intentando SHORT {symbol}: ${usdt_qty:.2f} USDT → {qty_c} contratos (${qty_val:.2f})")

        if USE_LIMIT_ORDERS and qty_c:
            limit_price = round(price * (1 + LIMIT_OFFSET_PCT / 100), 8)
            d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol,'side':'SELL','positionSide':'SHORT',
                'type':'LIMIT','price':str(limit_price),
                'quantity':str(qty_c),'timeInForce':'GTC',
            }).json()
            if d.get('code') == 0:
                log.info(f"  ENTRADA LÍMITE maker OK {qty_c} cts @ ${limit_price:.6f}")
                return d.get('data',{}).get('orderId','OK'), qty_c
            if 'margin' in str(d.get('msg','')).lower():
                log.error(f"  Margen insuficiente — abortando"); return None, None
            log.warning(f"  Límite falló [{d.get('code')}] — fallback mercado")

        # Fallback 1: quoteOrderQty
        d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol':symbol,'side':'SELL','positionSide':'SHORT',
            'type':'MARKET','quoteOrderQty':str(round(usdt_qty, 2)),
        }).json()
        if d.get('code') == 0:
            return d.get('data',{}).get('orderId','OK'), qty_c

        log.warning(f"  quoteOrderQty falló [{d.get('code')}] — fallback contratos")
        if not qty_c: return None, None

        # Fallback 2: quantity en contratos
        d2 = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol':symbol,'side':'SELL','positionSide':'SHORT',
            'type':'MARKET','quantity':str(qty_c),
        }).json()
        if d2.get('code') == 0:
            return d2.get('data',{}).get('orderId','OK'), qty_c
        log.error(f"  Todos los métodos fallaron [{d2.get('code')}]: {d2.get('msg')}")
        return None, None

    def _esperar_posicion(self, symbol, timeout=60):
        log.info(f"  Esperando confirmación SHORT {symbol}...")
        for i in range(timeout):
            try:
                d = bingx_request('GET', '/openApi/swap/v2/user/positions',
                                  {'symbol': symbol}).json()
                if d.get('code') == 0:
                    for p in (d.get('data') or []):
                        amt = float(p.get('positionAmt', 0) or 0)
                        ps  = str(p.get('positionSide', '')).upper()
                        if amt < 0 or ps == 'SHORT':
                            entry_real = float(p.get('avgPrice') or p.get('entryPrice') or 0)
                            qty_real   = abs(amt)
                            if qty_real > 0:
                                log.info(f"  Confirmado: qty={qty_real:.4f} entry=${entry_real:.6f} ({i+1}s)")
                                return qty_real, entry_real
            except: pass
            time.sleep(1)
        log.warning(f"  Timeout {timeout}s — posición no confirmada")
        return None, None

    def _cancelar_ordenes(self, symbol):
        try:
            d = bingx_request('GET', '/openApi/swap/v2/trade/openOrders',
                              {'symbol': symbol}).json()
            if d.get('code') == 0:
                for o in (d.get('data', {}).get('orders') or []):
                    oid = o.get('orderId', '')
                    if oid:
                        bingx_request('DELETE', '/openApi/swap/v2/trade/order',
                                      {'symbol': symbol, 'orderId': str(oid)})
        except: pass

    def _cond_order(self, symbol, qty_c, stop_price, otype):
        if not qty_c or qty_c <= 0:
            log.error(f"  {otype} cancelado: qty_c inválido ({qty_c})")
            return False
        try:
            is_tp = "TAKE" in otype
            if is_tp:
                params = {
                    'symbol':symbol,'side':'BUY','positionSide':'SHORT',
                    'type':'TAKE_PROFIT','quantity':str(qty_c),
                    'price':str(round(stop_price, 8)),
                    'stopPrice':str(round(stop_price, 8)),'timeInForce':'GTC',
                }
                d  = bingx_request('POST', '/openApi/swap/v2/trade/order', params).json()
                ok = d.get('code') == 0
                if ok:
                    log.info(f"  TP ✅ límite maker @ ${stop_price:.6f}")
                else:
                    p2 = {'symbol':symbol,'side':'BUY','positionSide':'SHORT',
                          'type':'TAKE_PROFIT_MARKET','quantity':str(qty_c),
                          'stopPrice':str(round(stop_price, 8))}
                    d2 = bingx_request('POST', '/openApi/swap/v2/trade/order', p2).json()
                    ok = d2.get('code') == 0
                    if ok:  log.info(f"  TP ✅ market fallback @ ${stop_price:.6f}")
                    else:   log.error(f"  TP ❌ [{d2.get('code')}]: {d2.get('msg')}")
            else:
                limit_price = round(stop_price * (1 + SL_LIMIT_OFFSET), 8)
                params = {
                    'symbol':symbol,'side':'BUY','positionSide':'SHORT',
                    'type':'STOP','quantity':str(qty_c),
                    'price':str(limit_price),'stopPrice':str(round(stop_price, 8)),
                    'timeInForce':'GTC',
                }
                d  = bingx_request('POST', '/openApi/swap/v2/trade/order', params).json()
                ok = d.get('code') == 0
                if ok:
                    log.info(f"  SL ✅ límite maker trigger=${stop_price:.6f}")
                else:
                    p2 = {'symbol':symbol,'side':'BUY','positionSide':'SHORT',
                          'type':'STOP_MARKET','quantity':str(qty_c),
                          'stopPrice':str(round(stop_price, 8))}
                    d2 = bingx_request('POST', '/openApi/swap/v2/trade/order', p2).json()
                    ok = d2.get('code') == 0
                    if ok:  log.info(f"  SL ✅ STOP_MARKET fallback @ ${stop_price:.6f}")
                    else:   log.error(f"  SL ❌ [{d2.get('code')}]: {d2.get('msg')}")
            return ok
        except Exception as e:
            log.error(f"  {otype} excepción: {e}")
            return False

    def _close_short(self, symbol, t):
        qty_c = t.get('qty_c', 0)
        usdt  = t.get('usdt_qty', POSITION_SIZE)
        if qty_c and qty_c > 0:
            cur_price = t.get('entry', 0)
            try:
                tk = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker",
                                  params={'symbol': symbol}, timeout=5).json()
                if tk.get('code') == 0 and tk.get('data'):
                    cur_price = float(tk['data'].get('lastPrice', cur_price))
            except: pass
            if cur_price > 0:
                limit_price = round(cur_price * (1 - 0.0005), 8)
                d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                    'symbol':symbol,'side':'BUY','positionSide':'SHORT',
                    'type':'LIMIT','quantity':str(qty_c),
                    'price':str(limit_price),'timeInForce':'IOC','reduceOnly':'true',
                }).json()
                if d.get('code') == 0:
                    log.info(f"  Cierre límite IOC maker @ ${limit_price:.6f}")
                    return True
                log.warning(f"  Cierre límite rechazado — fallback market")
        if qty_c and qty_c > 0:
            params = {'symbol':symbol,'side':'BUY','positionSide':'SHORT',
                      'type':'MARKET','quantity':str(qty_c),'reduceOnly':'true'}
        else:
            params = {'symbol':symbol,'side':'BUY','positionSide':'SHORT',
                      'type':'MARKET','quoteOrderQty':str(round(usdt,2)),'reduceOnly':'true'}
        ok = bingx_request('POST', '/openApi/swap/v2/trade/order', params).json().get('code') == 0
        if ok: log.info(f"  Cierre MARKET taker OK")
        return ok

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

    def open_trade(self, symbol, sig):
        if not AUTO_TRADING:
            log.info(f"  [SEÑAL] SHORT {symbol} score:{sig['score']:.0f} régimen:{sig['regime']}")
            return False
        if symbol in self.open_trades: return False

        # ══ VERIFICACIÓN CAPITAL MÍNIMO 6 USDT ══
        available = self._get_available_balance()
        if available < MIN_CAPITAL_REQUIRED:
            log.warning(f"  ⚠️  FONDOS INSUFICIENTES: ${available:.2f} USDT disponibles")
            log.warning(f"  ⚠️  Se requieren mínimo ${MIN_CAPITAL_REQUIRED} USDT para operar")
            log.warning(f"  ⚠️  Bot en ESPERA hasta que deposites fondos suficientes")
            return False
        # ════════════════════════════════════════

        if not self._correlacion_ok():
            log.info(f"  {symbol} bloqueado por filtro de correlación")
            return False

        tiene, dir_bx = self._tiene_posicion(symbol)
        if tiene: log.info(f"  {symbol} ya tiene {dir_bx} — skip"); return False

        price    = sig['price']
        # FIX-1: Garantía triple — SIEMPRE mínimo FORCE_MIN_USDT
        usdt_qty = round(max(POSITION_SIZE, FORCE_MIN_USDT, MIN_TRADE), 2)

        tp_price = price * (1 - sig['tp_pct'] / 100)
        sl_price = price * (1 + sig['sl_pct'] / 100)

        patterns_str = f"Patrones: {', '.join(sig['patterns'])}" if sig['patterns'] else "Sin patrón chartista"
        rr_real = round(sig['tp_pct'] / sig['sl_pct'], 2)
        log.info(f"\n  ➤ SHORT {symbol}")
        log.info(f"  Balance disponible: ${available:.2f} USDT")
        log.info(f"  Score:{sig['score']:.0f}/{sig['score_min']:.0f} | RSI:{sig['rsi']:.0f} | "
                 f"BB:{sig['bb_pos']}% | Régimen:{sig['regime']} | BTC 1h:{self._btc_change_1h:+.2f}% 4h:{self._btc_change_4h:+.2f}%")
        log.info(f"  {patterns_str}")
        log.info(f"  {sig['reasons']}")
        log.info(f"  Entry:${price:.6f} | Capital:${usdt_qty} USDT | TP:{sig['tp_pct']:.2f}% SL:{sig['sl_pct']:.1f}% RR:{rr_real}:1")

        oid, qty_c = self._place_short_entry(symbol, usdt_qty, price)
        if not oid:
            log.error(f"  No se pudo abrir {symbol}"); return False

        qty_real, entry_real = self._esperar_posicion(symbol, timeout=60)
        if qty_real is None:
            self._cancelar_ordenes(symbol)
            time.sleep(0.5)
            d_mkt = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol,'side':'SELL','positionSide':'SHORT',
                'type':'MARKET','quantity':str(qty_c),
            }).json()
            if d_mkt.get('code') == 0:
                qty_real, entry_real = self._esperar_posicion(symbol, timeout=30)
            if qty_real is None:
                log.error(f"  CRÍTICO: No se pudo confirmar posición {symbol} — cerrando")
                self._tg(f"<b>🚨 CRÍTICO {symbol}</b>\nNo se confirmó posición. Cerrando de emergencia.")
                for _ in range(2):
                    try:
                        bingx_request('POST', '/openApi/swap/v2/trade/order', {
                            'symbol':symbol,'side':'BUY','positionSide':'SHORT',
                            'type':'MARKET','quantity':str(qty_c),'reduceOnly':'true',
                        })
                        time.sleep(1)
                    except: pass
                return False

        qty_final   = qty_real if qty_real else qty_c
        entry_final = entry_real if (entry_real and entry_real > 0) else price
        tp_price    = entry_final * (1 - sig['tp_pct'] / 100)
        sl_price    = entry_final * (1 + sig['sl_pct'] / 100)

        tp_ok = self._cond_order(symbol, qty_final, tp_price, 'TAKE_PROFIT_MARKET')
        time.sleep(0.3)
        sl_ok = self._cond_order(symbol, qty_final, sl_price, 'STOP_MARKET')

        for delay in [1, 2, 3, 5, 8, 13]:
            if tp_ok and sl_ok: break
            log.warning(f"  TP:{tp_ok} SL:{sl_ok} — reintentando en {delay}s")
            self._cancelar_ordenes(symbol)
            time.sleep(delay)
            if not tp_ok:
                tp_ok = self._cond_order(symbol, qty_final, tp_price, 'TAKE_PROFIT_MARKET')
            if not sl_ok:
                sl_ok = self._cond_order(symbol, qty_final, sl_price, 'STOP_MARKET')

        if not sl_ok:
            log.error(f"  CRÍTICO: SL fallido en {symbol} — cerrando posición")
            self._tg(f"<b>🚨 CRÍTICO SL FALLIDO — SHORT {symbol}</b>\nNo se pudo colocar SL. Cerrando.")
            time.sleep(1)
            self._close_short(symbol, {'qty_c': qty_final, 'usdt_qty': usdt_qty, 'entry': entry_final})
            return False

        self.open_trades[symbol] = {
            'entry':entry_final,'qty_c':qty_final,'usdt_qty':usdt_qty,'method':'contracts',
            'tp':tp_price,'sl':sl_price,'tp_pct':sig['tp_pct'],'sl_pct':sig['sl_pct'],
            'lowest':entry_final,'order_id':oid,'tp_ok':tp_ok,'sl_ok':sl_ok,
            'opened_at':datetime.now(),'score':sig['score'],
            'patterns':sig['patterns'],'regime':sig['regime'],
        }
        self.stats['exec'] += 1

        rr = round(sig['tp_pct'] / sig['sl_pct'], 1)
        pat_str = f"\nPatrones: {', '.join(sig['patterns'])}" if sig['patterns'] else ""
        self._tg(
            f"<b>🔴 SHORT ABIERTO v3.3.1</b>\n<b>{symbol}</b> | Score:{sig['score']:.0f}/{sig['score_min']:.0f}\n"
            f"Balance: ${available:.2f} USDT\n"
            f"Entrada: ${entry_final:.6f}\n"
            f"{'✅' if tp_ok else '❌'} TP: ${tp_price:.6f} (-{sig['tp_pct']:.2f}%)\n"
            f"{'✅' if sl_ok else '❌'} SL: ${sl_price:.6f} (+{sig['sl_pct']:.1f}%)\n"
            f"RR: {rr}:1 | Capital: ${usdt_qty} USDT x{LEVERAGE}\n"
            f"BTC 1h:{self._btc_change_1h:+.2f}% 4h:{self._btc_change_4h:+.2f}%"
            f"{pat_str}"
        )
        return True

    def close_trade(self, symbol, cur_price, reason):
        if symbol not in self.open_trades: return False
        t = self.open_trades[symbol]
        self._close_short(symbol, t)

        cambio  = (t['entry'] - cur_price) / t['entry']
        pnl     = (t['usdt_qty'] * LEVERAGE * cambio) - (t['usdt_qty'] * LEVERAGE * COMISION_ACTUAL)
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
            f"<b>{emoji} SHORT CERRADO — {reason}</b>\n<b>{symbol}</b>\n"
            f"PnL: ${pnl:+.3f} ({pnl_pct:+.1f}%) | {mins} min\n"
            f"Entry: ${t['entry']:.6f} → Exit: ${cur_price:.6f}\n"
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
                    cambio  = (t['entry'] - cur) / t['entry']
                    pnl     = (t['usdt_qty'] * LEVERAGE * cambio) - (t['usdt_qty'] * LEVERAGE * COMISION_ACTUAL)
                    pnl_pct = (pnl / t['usdt_qty']) * 100
                    self.stats['closed'] += 1; self.stats['pnl'] += pnl
                    if pnl >= 0: self.stats['wins']   += 1
                    else:        self.stats['losses'] += 1
                    total = self.stats['wins'] + self.stats['losses']
                    wr    = self.stats['wins'] / total * 100 if total else 0
                    emoji = "✅" if pnl >= 0 else "❌"
                    mins  = int((datetime.now() - t['opened_at']).total_seconds() / 60)
                    self._tg(f"<b>{emoji} SHORT cerrado BingX</b>\n<b>{sym}</b>\n"
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
                pnl_pct = (t['entry'] - cur) / t['entry'] * 100

                if TRAILING and cur < t['lowest']:
                    t['lowest'] = cur
                    if pnl_pct >= 0.6:
                        new_sl = t['entry'] - (t['entry'] - cur) * 0.60
                        if new_sl < t['sl']:
                            t['sl'] = new_sl
                            log.info(f"  Trailing SL {sym}: ${new_sl:.6f}")

                pnl_leverage = pnl_pct * LEVERAGE
                if pnl_leverage < -MAX_LOSS_PCT:
                    log.error(f"  EMERGENCIA {sym}: PnL {pnl_leverage:+.1f}% — cerrando")
                    self._tg(f"<b>🚨 EMERGENCIA MAX LOSS {sym}</b>\nPnL: {pnl_leverage:+.1f}%")
                    self.close_trade(sym, cur, "STOP LOSS EMERGENCIA")
                    continue

                if abs(pnl_pct) > 0.3:
                    log.info(f"  {sym}: {pnl_pct:+.2f}% | cur:${cur:.6f}")

                if cur <= t['tp']:   self.close_trade(sym, cur, "TAKE PROFIT")
                elif cur >= t['sl']: self.close_trade(sym, cur, "STOP LOSS")
            except Exception as e:
                log.debug(f"Monitor {sym}: {e}")

    def _reporte_horario(self):
        if datetime.now() - self._last_report < timedelta(hours=1): return
        self._last_report = datetime.now()
        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        rr = round(TP_PCT / SL_PCT, 2)
        btc_st = "⛔ BLOQUEADO" if self._btc_blocked else "✅ OK"
        available = self._get_available_balance()
        capital_st = "✅ OK" if available >= MIN_CAPITAL_REQUIRED else f"⚠️ INSUFICIENTE (mín ${MIN_CAPITAL_REQUIRED})"
        
        self._tg(
            f"<b>📊 Reporte horario v3.3.1</b>\n"
            f"Balance: ${available:.2f} USDT {capital_st}\n"
            f"PnL: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}%\n"
            f"({self.stats['wins']}W/{self.stats['losses']}L | {self.stats['closed']} trades)\n"
            f"Abiertos: {len(self.open_trades)}/{MAX_TRADES}\n"
            f"Capital/trade: ${POSITION_SIZE} USDT (mín ${FORCE_MIN_USDT})\n"
            f"RR: {rr}:1 | Break-even: WR>{round(1/(1+rr)*100,1)}%\n"
            f"BTC 1h:{self._btc_change_1h:+.2f}% 4h:{self._btc_change_4h:+.2f}% {btc_st}"
        )

    def _tg(self, msg):
        try:
            if TELEGRAM_TOKEN and TELEGRAM_CHAT:
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={'chat_id':TELEGRAM_CHAT,'text':msg,'parse_mode':'HTML'}, timeout=6)
        except: pass

    # ---------------------------------------------------------------- loop

    async def run(self):
        log.info("\n▶  Bot SHORT v3.3.1 arrancado\n")
        iteration, last_refresh = 0, 0
        while True:
            try:
                iteration += 1
                if time.time() - last_refresh > 600:
                    self._get_symbols(); last_refresh = time.time()

                self._update_btc_trend()
                available = self._get_available_balance()

                total = self.stats['wins'] + self.stats['losses']
                wr    = self.stats['wins'] / total * 100 if total else 0
                btc_st = "⛔ BLOQUEADO" if self._btc_blocked else "✅ OK"
                hora_st = "🌙 HORA BAJA" if not self._hora_ok() else "☀️"
                capital_st = "✅ OK" if available >= MIN_CAPITAL_REQUIRED else "⚠️ FONDOS INSUFICIENTES"

                log.info(f"\n{'='*70}")
                log.info(f"  #{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                         f"Balance: ${available:.2f} USDT {capital_st}")
                log.info(f"  Abiertos:{len(self.open_trades)}/{MAX_TRADES} | "
                         f"PnL:${self.stats['pnl']:+.3f} | WR:{wr:.1f}%")
                log.info(f"  BTC 1h:{self._btc_change_1h:+.2f}% 4h:{self._btc_change_4h:+.2f}% {btc_st} | {hora_st}")
                log.info(f"  Capital/trade: ${POSITION_SIZE} USDT (mín garantizado: ${FORCE_MIN_USDT})")
                log.info(f"{'='*70}\n")

                await self.monitor_trades()
                self._reporte_horario()

                if len(self.open_trades) < MAX_TRADES:
                    if available < MIN_CAPITAL_REQUIRED:
                        log.warning(f"  ⚠️  ESPERANDO FONDOS: Balance ${available:.2f} < ${MIN_CAPITAL_REQUIRED} USDT")
                        log.warning(f"  ⚠️  Deposita al menos ${MIN_CAPITAL_REQUIRED} USDT para que el bot pueda operar")
                    elif not self._correlacion_ok():
                        log.info(f"  Filtro correlación activo — esperando cierre de posiciones en pérdida")
                    elif not self._btc_regime_ok():
                        log.info(f"  Filtro BTC multiframe activo — no se analizan señales")
                    else:
                        found = 0
                        for i, sym in enumerate(self.symbols):
                            if len(self.open_trades) >= MAX_TRADES: break
                            sig = self.analyze(sym)
                            if sig:
                                found += 1
                                pat_str = f" [{','.join(sig['patterns'])}]" if sig['patterns'] else ""
                                log.info(f"  ★ {sym} score:{sig['score']:.0f} RSI:{sig['rsi']:.0f} "
                                         f"régimen:{sig['regime']}{pat_str}")
                                self.open_trade(sym, sig)
                            await asyncio.sleep(0.12)
                            if (i+1) % 20 == 0:
                                log.info(f"  ...{i+1}/{len(self.symbols)} analizados")
                        log.info(f"\n  {len(self.symbols)} pares | {found} señales")
                else:
                    log.info(f"  Max ({MAX_TRADES}) posiciones abiertas — esperando cierre")

                log.info(f"\n  Próximo ciclo en {INTERVAL}s\n")
                await asyncio.sleep(INTERVAL)

            except KeyboardInterrupt:
                log.info("Detenido"); break
            except Exception as e:
                log.error(f"Error loop #{iteration}: {e}")
                await asyncio.sleep(20)

async def main():
    try:
        await ShortBot().run()
    except Exception as e:
        log.error(f"Error fatal: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Terminado")
