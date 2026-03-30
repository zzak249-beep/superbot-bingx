#!/usr/bin/env python3
"""
BOT SHORTS PROFESIONAL v3.6 — main_shorts.py para Railway/GitHub
════════════════════════════════════════════════════════════════
PROTECCIONES HARDCODEADAS (igual que main.py de longs):
  ▸ LEVERAGE máximo: 3x  (aunque .env diga 18x o más)
  ▸ MAX_TRADES máximo: 3  (aunque .env diga más)
  ▸ FORCE_MIN_USDT: 8 USDT mínimo por trade
  ▸ Sin quoteOrderQty — siempre quantity en contratos
  ▸ _set_leverage() llamado antes de cada apertura

FIXES v3.6 (nuevos respecto a v3.5):
  ▸ Hard cap LEVERAGE ≤ 3x en código (no sobreescribible por .env)
  ▸ Hard cap MAX_TRADES ≤ 3 en código
  ▸ _set_leverage() fuerza 3x en BingX antes de cada SHORT
  ▸ Lock _abriendo — solo un trade a la vez
  ▸ Conteo posiciones reales en BingX antes de abrir
  ▸ Pausa 3s tras cada apertura exitosa
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
POSITION_SIZE = clean('MAX_POSITION_SIZE',      '10',   'float')
MIN_TRADE     = clean('MIN_TRADE_USDT',          '8',   'float')
TP_PCT        = clean('TAKE_PROFIT_PCT',         '2.5', 'float')
SL_PCT        = clean('STOP_LOSS_PCT',           '1.5', 'float')
INTERVAL      = clean('CHECK_INTERVAL',          '60',  'int')
MIN_VOLUME    = clean('MIN_VOLUME_24H',      '500000',  'float')
MAX_SYMBOLS   = clean('MAX_SYMBOLS_TO_ANALYZE',  '50',  'int')
MIN_SCORE     = clean('MIN_SCORE',               '82',  'float')
TRAILING      = clean('TRAILING_STOP_ENABLED', 'true',  'bool')
TRAILING_START= clean('TRAILING_START_PCT',     '1.0',  'float')
TRAILING_LOCK = clean('TRAILING_LOCK_PCT',       '60',  'float')
USE_LIMIT_ORDERS   = clean('USE_LIMIT_ORDERS',      'true', 'bool')
BTC_BULL_BLOCK_PCT = clean('BTC_BULL_BLOCK_PCT',    '1.5',  'float')
MAX_LOSS_PCT       = clean('MAX_LOSS_PCT',           '5.0',  'float')
SL_LIMIT_OFFSET    = clean('SL_LIMIT_OFFSET_PCT',   '0.05', 'float') / 100
FORCE_MIN_USDT     = clean('FORCE_MIN_USDT',         '8.0',  'float')
COOLDOWN_MIN_TP    = clean('COOLDOWN_AFTER_TP_MIN',  '15',   'int')
COOLDOWN_MIN_SL    = clean('COOLDOWN_AFTER_SL_MIN',  '45',   'int')

# ── NUEVOS parámetros v3.0 ───────────────────────────────────────
MAE_PERIOD    = clean('MAE_PERIOD',     '20',  'int')
MAE_PCT       = clean('MAE_PCT',        '2.0', 'float')
MAE_EXTREME   = clean('MAE_EXTREME',    '1.8', 'float')
PATTERN_SCORE = clean('PATTERN_SCORE', 'true', 'bool')
REGIME_FILTER = clean('REGIME_FILTER', 'true', 'bool')

# ══════════════════════════════════════════════════════════════════
# HARD CAPS — NO MODIFICAR — protegen contra .env mal configurado
# ══════════════════════════════════════════════════════════════════
_lev_env    = clean('LEVERAGE',        '3', 'int')
_trades_env = clean('MAX_OPEN_TRADES', '2', 'int')
_min_env    = clean('FORCE_MIN_USDT',  '8.0', 'float')

LEVERAGE       = min(_lev_env,    3)   # NUNCA más de 3x aunque .env diga 18x
MAX_TRADES     = min(_trades_env, 3)   # NUNCA más de 3 trades simultáneos
FORCE_MIN_USDT = max(_min_env,    8.0) # SIEMPRE mínimo 8 USDT por trade
# ══════════════════════════════════════════════════════════════════

LIMIT_OFFSET_PCT = 0.05
SKIP_HOURS_UTC   = {0, 1}

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
# MOVING AVERAGE ENVELOPES — nuevo módulo v3.0
# ============================================================================

def calc_mae(prices, period=20, pct=2.0):
    """
    Moving Average Envelopes.
    Retorna: (upper_band, ma, lower_band, position)
    position: >1.0 = sobre banda superior (extremo alcista)
              <-1.0 = bajo banda inferior (extremo bajista)
              0..1 = dentro del rango, lado superior
              -1..0 = dentro del rango, lado inferior
    """
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
        # normalizado: 0 = en lower, 1 = en upper, >1 = encima upper, <0 = debajo lower
        position = (price - lower) / band_width
    else:
        position = 0.5
    
    return upper, ma, lower, position

def mae_regime_score(prices, period=20, pct=2.0):
    """
    Evalúa si el precio está en zona de SHORT según MAE.
    
    Filosofía (imagen 2):
    - Mercado RANGO: precio cierra POR DEBAJO de banda +2% → SHORT (reversión a media)
    - Mercado TENDENCIA: precio por encima de banda = impulso, NO luchar
    
    Retorna: (puntos, descripción, régimen)
    """
    upper, ma, lower, pos = calc_mae(prices, period, pct)
    price = prices[-1]
    
    # Detectar régimen: pendiente de la MA
    if len(prices) >= period + 5:
        ma_old = calc_sma(prices[:-5], period)
        ma_slope_pct = (ma - ma_old) / ma_old * 100 if ma_old > 0 else 0
    else:
        ma_slope_pct = 0
    
    is_uptrend   = ma_slope_pct >  0.5   # MA sube > 0.5% en 5 velas
    is_downtrend = ma_slope_pct < -0.3   # MA baja > 0.3% en 5 velas
    is_ranging   = not is_uptrend and not is_downtrend
    
    score = 0
    desc  = ""
    
    if is_ranging:
        regime = "RANGO"
        if pos >= 0.95:          # precio tocando o sobre banda superior → extremo, reversión
            score = 30; desc = f"MAE_TOP_RANGO(30) pos:{pos:.2f}"
        elif pos >= 0.80:
            score = 20; desc = f"MAE_ALTO_RANGO(20) pos:{pos:.2f}"
        elif pos >= 0.60:
            score = 8;  desc = f"MAE_MEDIO+(8) pos:{pos:.2f}"
        elif pos <= 0.30:
            score = -15; desc = f"MAE_BAJO(-15) pos:{pos:.2f}"
        else:
            score = 0;   desc = f"MAE_NEUTRAL(0) pos:{pos:.2f}"
    
    elif is_downtrend:
        regime = "BAJISTA"
        # En downtrend cualquier rebote a la banda media/superior es buena entrada short
        if pos >= 0.70:
            score = 25; desc = f"MAE_REBOTE_BAJISTA(25) pos:{pos:.2f}"
        elif pos >= 0.50:
            score = 15; desc = f"MAE_MEDIO_BAJISTA(15) pos:{pos:.2f}"
        else:
            score = 5;  desc = f"MAE_BAJISTA_OK(5) pos:{pos:.2f}"
    
    else:  # UPTREND
        regime = "ALCISTA"
        # En uptrend fuerte NO entrar short (precio sobre banda = impulso)
        if pos > 1.05:
            score = -25; desc = f"MAE_IMPULSO_ALCISTA(-25) pos:{pos:.2f}"
        elif pos > 0.90:
            score = -12; desc = f"MAE_ALCISTA_FUERTE(-12) pos:{pos:.2f}"
        elif pos < 0.40:
            # Retroceso en uptrend (precio vuelve a MA o below) → posible short de corta duración
            score = 10; desc = f"MAE_RETROCESO_ALCISTA(10) pos:{pos:.2f}"
        else:
            score = -5; desc = f"MAE_ALCISTA(-5) pos:{pos:.2f}"
    
    return score, desc, regime, pos, upper, ma, lower

# ============================================================================
# DETECCIÓN DE PATRONES CHARTISTAS — nuevo módulo v3.0
# ============================================================================

def find_pivots(prices, window=3):
    """Detecta máximos y mínimos locales con ventana configurable."""
    highs, lows = [], []
    for i in range(window, len(prices) - window):
        if all(prices[i] >= prices[i-j] and prices[i] >= prices[i+j] for j in range(1, window+1)):
            highs.append((i, prices[i]))
        if all(prices[i] <= prices[i-j] and prices[i] <= prices[i+j] for j in range(1, window+1)):
            lows.append((i, prices[i]))
    return highs, lows

def detect_double_top(closes, highs_list, tolerance=0.015):
    """
    Double Top: dos máximos similares con valle en medio.
    Señal SHORT: precio rompe el cuello (valley).
    """
    if len(highs_list) < 2: return False, 0, ""
    
    h1_idx, h1_val = highs_list[-2]
    h2_idx, h2_val = highs_list[-1]
    
    # Los dos máximos deben ser similares (dentro del tolerance)
    diff = abs(h1_val - h2_val) / max(h1_val, h2_val)
    if diff > tolerance: return False, 0, ""
    
    # Debe haber un valle significativo entre ellos
    valley_prices = closes[h1_idx:h2_idx]
    if not valley_prices: return False, 0, ""
    valley = min(valley_prices)
    
    # El valle debe ser al menos 1% menor que los máximos
    neck_drop = (max(h1_val, h2_val) - valley) / max(h1_val, h2_val)
    if neck_drop < 0.01: return False, 0, ""
    
    # Precio actual cerca del segundo máximo o justo rompiendo el cuello
    cur = closes[-1]
    near_top  = cur >= h2_val * 0.985
    at_neck   = cur <= valley * 1.005 and cur >= valley * 0.98
    
    if near_top:
        score = 35
        desc  = f"DoubleTop_TOP({score})"
        return True, score, desc
    if at_neck:
        score = 40
        desc  = f"DoubleTop_NECK({score})"
        return True, score, desc
    
    return False, 0, ""

def detect_head_shoulders(closes, highs_list, tolerance=0.02):
    """
    Head & Shoulders: hombro izq < cabeza > hombro der, precio en neckline.
    Señal SHORT: clásica inversión.
    """
    if len(highs_list) < 3: return False, 0, ""
    
    ls_idx, ls_val = highs_list[-3]
    h_idx,  h_val  = highs_list[-2]
    rs_idx, rs_val = highs_list[-1]
    
    # Cabeza debe ser el mayor de los tres
    if not (h_val > ls_val and h_val > rs_val): return False, 0, ""
    
    # Hombros deben ser similares en altura (±2%)
    shoulder_diff = abs(ls_val - rs_val) / max(ls_val, rs_val)
    if shoulder_diff > tolerance: return False, 0, ""
    
    # Precio actual por debajo del hombro derecho → breakout del neckline
    cur = closes[-1]
    if cur <= rs_val * 0.995:
        score = 38
        desc  = f"H&S_BREAK({score})"
        return True, score, desc
    
    # Precio cerca del hombro derecho → entrada anticipada
    if cur >= rs_val * 0.99:
        score = 28
        desc  = f"H&S_SHOULDER({score})"
        return True, score, desc
    
    return False, 0, ""

def detect_rising_wedge(closes, highs_list, lows_list, min_points=3):
    """
    Rising Wedge: máximos y mínimos subiendo pero convergiendo.
    Señal SHORT: ruptura bajista inminente.
    """
    if len(highs_list) < min_points or len(lows_list) < min_points:
        return False, 0, ""
    
    recent_highs = highs_list[-min_points:]
    recent_lows  = lows_list[-min_points:]
    
    # Pendiente de máximos y mínimos
    def slope(points):
        if len(points) < 2: return 0
        x1, y1 = points[0]
        x2, y2 = points[-1]
        return (y2 - y1) / (x2 - x1) if x2 != x1 else 0
    
    s_h = slope(recent_highs)
    s_l = slope(recent_lows)
    
    # Ambas pendientes positivas (precio subiendo)
    if s_h <= 0 or s_l <= 0: return False, 0, ""
    
    # Mínimos suben más rápido que máximos → convergencia (cuña)
    if not (s_l > s_h * 0.7): return False, 0, ""
    
    # Precio cerca del vértice de la cuña (extremo superior)
    cur = closes[-1]
    last_high = recent_highs[-1][1]
    last_low  = recent_lows[-1][1]
    range_sz  = last_high - last_low
    
    if range_sz > 0:
        pos_in_wedge = (cur - last_low) / range_sz
        if pos_in_wedge >= 0.75:  # precio en la parte alta de la cuña
            score = 30
            desc  = f"RisingWedge_TOP({score})"
            return True, score, desc
    
    return False, 0, ""

def detect_bearish_flag(closes, volumes, highs_list):
    """
    Bearish Flag: caída fuerte (mástil) seguida de consolidación lateral/ligero alza.
    Señal SHORT: continuación bajista.
    """
    if len(closes) < 20: return False, 0, ""
    
    # Detectar mástil: caída > 3% en las últimas 5-8 velas
    mast_start = -12
    mast_end   = -6
    
    mast_change = (closes[mast_end] - closes[mast_start]) / closes[mast_start] * 100
    if mast_change > -2.5: return False, 0, ""  # no hay mástil bajista
    
    # Consolidación: las últimas 5 velas en rango estrecho (<1.5%)
    flag_prices = closes[-5:]
    flag_range  = (max(flag_prices) - min(flag_prices)) / min(flag_prices) * 100
    if flag_range > 2.0: return False, 0, ""  # no es consolidación
    
    # Volumen: debe haber bajado durante la bandera vs el mástil
    if len(volumes) >= 10:
        vol_mast = sum(volumes[-10:-5]) / 5
        vol_flag = sum(volumes[-5:]) / 5
        vol_ok   = vol_flag < vol_mast  # volumen decrece en flag
    else:
        vol_ok = True
    
    if vol_ok:
        score = 32
        desc  = f"BearishFlag({score})"
        return True, score, desc
    
    # Sin confirmación de volumen, señal más débil
    score = 18
    desc  = f"BearishFlag_noVol({score})"
    return True, score, desc

def detect_support_breakdown(closes, lows_list, tolerance=0.008):
    """
    Ruptura de soporte: precio rompe nivel horizontal de soporte con fuerza.
    Señal SHORT: continuación bajista tras ruptura.
    """
    if len(lows_list) < 2 or len(closes) < 5: return False, 0, ""
    
    # Soporte: mínimo reciente que fue testeado al menos 2 veces
    recent_lows_vals = [v for _, v in lows_list[-6:]]
    if len(recent_lows_vals) < 2: return False, 0, ""
    
    # Agrupar mínimos similares (±0.8%) para encontrar zona de soporte
    support_level = None
    for i, low in enumerate(recent_lows_vals):
        touches = sum(1 for l in recent_lows_vals if abs(l - low) / low < tolerance)
        if touches >= 2:
            support_level = low; break
    
    if not support_level: return False, 0, ""
    
    cur = closes[-1]
    # Precio rompió el soporte (>0.5% por debajo)
    if cur < support_level * (1 - tolerance):
        score = 28
        desc  = f"SupportBreak({score})"
        return True, score, desc
    
    return False, 0, ""

def scan_patterns(closes, highs_raw, lows_raw, volumes):
    """
    Ejecuta todos los detectores de patrones y suma scores.
    Retorna: (total_score, lista_patrones_detectados)
    """
    if not closes or len(closes) < 20:
        return 0, []
    
    highs_list, lows_list = find_pivots(closes, window=3)
    
    patterns = []
    total    = 0
    
    # Double Top
    ok, sc, dsc = detect_double_top(closes, highs_list)
    if ok: patterns.append(dsc); total += sc
    
    # Head & Shoulders
    ok, sc, dsc = detect_head_shoulders(closes, highs_list)
    if ok: patterns.append(dsc); total += sc
    
    # Rising Wedge
    ok, sc, dsc = detect_rising_wedge(closes, highs_list, lows_list)
    if ok: patterns.append(dsc); total += sc
    
    # Bearish Flag
    ok, sc, dsc = detect_bearish_flag(closes, volumes, highs_list)
    if ok: patterns.append(dsc); total += sc
    
    # Support Breakdown
    ok, sc, dsc = detect_support_breakdown(closes, lows_list)
    if ok: patterns.append(dsc); total += sc
    
    return total, patterns

# ============================================================================
# DETECTOR DE RÉGIMEN DE MERCADO
# ============================================================================

def detect_market_regime(closes, period=20):
    """
    Clasifica el mercado en: TREND_UP, TREND_DOWN, RANGING
    Basado en pendiente MA + ADX simplificado (ratio ATR/precio).
    """
    if len(closes) < period + 5:
        return "UNKNOWN"
    
    ma_now  = calc_sma(closes, period)
    ma_old  = calc_sma(closes[:-5], period)
    
    slope_pct = (ma_now - ma_old) / ma_old * 100 if ma_old > 0 else 0
    
    # Calcular "trending strength" como desviación de precio respecto a MA
    deviations = [abs(c - ma_now) / ma_now * 100 for c in closes[-period:]]
    avg_dev = sum(deviations) / len(deviations)
    
    # Alto avg_dev + slope = tendencia; bajo avg_dev = rango
    if slope_pct > 0.6 and avg_dev > 0.8:
        return "TREND_UP"
    elif slope_pct < -0.4 and avg_dev > 0.6:
        return "TREND_DOWN"
    else:
        return "RANGING"

# ============================================================================
# BOT
# ============================================================================

class ShortBot:

    def __init__(self):
        fee_lbl = f"LÍMITE maker {COMISION_MAKER*100:.2f}%" if USE_LIMIT_ORDERS \
                  else f"MERCADO taker {COMISION_TAKER*100:.2f}%"
        log.info("=" * 70)
        log.info("  BOT SHORTS PROFESIONAL v3.6 — main_shorts.py")
        log.info("  HARD CAPS: LEVERAGE≤3x | MAX_TRADES≤3 | MIN_USDT≥8")
        log.info("=" * 70)
        log.info(f"  Modo:      {'AUTO' if AUTO_TRADING else 'SEÑALES'}")
        log.info(f"  Capital:   ${POSITION_SIZE} USDT | Leverage: {LEVERAGE}x (cap 3x)")
        log.info(f"  TP/SL:     {TP_PCT}% / {SL_PCT}%  RR:{TP_PCT/SL_PCT:.1f}:1")
        log.info(f"  TP mín:    {TP_MIN_RENTABLE}% (cubre comisiones)")
        log.info(f"  Órdenes:   {fee_lbl}")
        log.info(f"  MAE:       MA{MAE_PERIOD} ±{MAE_PCT}% (filtro contexto)")
        log.info(f"  Patrones:  {'ON' if PATTERN_SCORE else 'OFF'} | Régimen: {'ON' if REGIME_FILTER else 'OFF'}")
        log.info(f"  BTC filtro:{BTC_BULL_BLOCK_PCT}% | MAX trades:{MAX_TRADES} (cap 3)")
        log.info("=" * 70)
        if _lev_env > 3:
            log.warning(f"  ⚠️  LEVERAGE={_lev_env}x en .env → forzado a {LEVERAGE}x por hard cap")
        if _trades_env > 3:
            log.warning(f"  ⚠️  MAX_OPEN_TRADES={_trades_env} en .env → forzado a {MAX_TRADES} por hard cap")

        self.symbols         = []
        self.open_trades     = {}
        self._contracts      = {}
        self._cooldowns      = {}
        self._last_report    = datetime.now()
        self._btc_change_1h  = 0.0
        self.stats = {'exec':0,'closed':0,'wins':0,'losses':0,'pnl':0.0}

        self._verify()
        self._load_contracts()
        self._get_symbols()
        self._tg(
            f"<b>🔴 Bot SHORTS v3.6 iniciado</b>\n"
            f"<b>HARD CAPS: LEV≤3x | MAX≤3 trades | MIN≥8 USDT</b>\n"
            f"TP:{TP_PCT}% SL:{SL_PCT}% RR≥1.7 LEV:{LEVERAGE}x\n"
            f"Score≥{MIN_SCORE} | Capital: ${POSITION_SIZE}\n"
            f"{'⚠️ LEVERAGE estaba en '+str(_lev_env)+'x → cap a 3x' if _lev_env > 3 else ''}"
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
        """Aumentado a 80 velas para dar más datos a los detectores de patrones."""
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
            closes, *_ = self._klines('BTC-USDT', '1h', 3)
            if closes and len(closes) >= 2:
                self._btc_change_1h = (closes[-1] - closes[-2]) / closes[-2] * 100
        except: pass

    # ---------------------------------------------------------------- sizing

    def _qty_contratos(self, symbol, price, usdt_amount=None):
        """
        v3.5 FIX DEFINITIVO: calcula qty en contratos para que el NOTIONAL sea
        exactamente usdt_amount USDT, independientemente del modo "Por costo/valor" de BingX.

        BingX `quantity` = número de contratos.
        Notional = qty × price × contractSize
        Por tanto: qty = usdt_amount / (price × contractSize)

        NUNCA se usa quoteOrderQty — es ambiguo según el modo de la cuenta.
        """
        if usdt_amount is None: usdt_amount = POSITION_SIZE
        usdt_amount = max(usdt_amount, FORCE_MIN_USDT, MIN_TRADE)

        info  = self._contracts.get(symbol, {'step': 1.0, 'prec': 2, 'ctval': 1.0})
        step  = max(info.get('step', 1.0), 0.0001)
        prec  = info.get('prec', 2)
        ctval = max(info.get('ctval', 1.0), 0.000000001)  # tamaño del contrato

        # Precio por contrato = precio del activo × tamaño del contrato
        ppc = price * ctval
        if ppc <= 0:
            log.error(f"  [QTY] {symbol}: precio por contrato inválido (price={price} ctval={ctval})")
            return None, 0

        # Cantidad de contratos para cubrir usdt_amount de NOTIONAL
        # (no de margen — el margen depende del leverage que fije el usuario en BingX)
        qty_raw = usdt_amount / ppc
        qty = round(math.ceil(qty_raw / step) * step, prec)
        val = qty * ppc  # notional en USDT

        min_val = max(MIN_TRADE, FORCE_MIN_USDT)
        i = 0
        while val < min_val and i < 200:
            qty += step
            qty  = round(qty, prec)
            val  = qty * ppc
            i   += 1

        if val < min_val:
            log.error(f"  [QTY] {symbol}: notional ${val:.4f} < mínimo ${min_val} (step={step}, ppc={ppc:.8f})")
            return None, 0

        # Cap: no exceder 130% del capital objetivo
        if val > usdt_amount * 1.3:
            qty = round(math.floor((usdt_amount * 1.3 / ppc) / step) * step, prec)
            val = qty * ppc
            if val < min_val:  # si el recorte rompió el mínimo, usar mínimo
                qty = round(math.ceil(min_val / ppc / step) * step, prec)
                val = qty * ppc

        log.info(f"  [QTY] {symbol}: {qty} cts × ${ppc:.6f}/ct = ${val:.2f} USDT notional "
                 f"(ctval={ctval}, step={step})")
        return qty, round(val, 4)

    def _notional_ok(self, symbol, qty_c, price):
        """Valida que qty_c contratos = al menos FORCE_MIN_USDT de notional."""
        info  = self._contracts.get(symbol, {'ctval': 1.0})
        ctval = max(info.get('ctval', 1.0), 0.000000001)
        val   = qty_c * price * ctval
        ok    = val >= FORCE_MIN_USDT
        log.info(f"  [VAL] {symbol}: {qty_c} cts × ${price:.6f} × {ctval} = ${val:.2f} USDT {'✅' if ok else '❌'}")
        return ok, round(val, 2)

    # ---------------------------------------------------------------- análisis principal

    def _cooldown_ok(self, symbol):
        ts = self._cooldowns.get(symbol)
        if not ts: return True
        resume_ts, reason = ts if isinstance(ts, tuple) else (ts + COOLDOWN_MIN_TP * 60, 'TP')
        if time.time() >= resume_ts:
            del self._cooldowns[symbol]; return True
        mins_left = int((resume_ts - time.time()) / 60)
        log.debug(f"  {symbol} cooldown {reason} ({mins_left}min restantes)")
        return False

    def _set_cooldown(self, symbol, reason='TP'):
        mins = COOLDOWN_MIN_TP if reason == 'TP' else COOLDOWN_MIN_SL
        self._cooldowns[symbol] = (time.time() + mins * 60, reason)
        log.info(f"  Cooldown {symbol}: {mins}min ({reason})")

    def _hora_ok(self):
        return datetime.utcnow().hour not in SKIP_HOURS_UTC

    def analyze(self, symbol):
        if symbol in self.open_trades or not self._cooldown_ok(symbol): return None
        if not self._hora_ok(): return None
        if self._btc_change_1h >= BTC_BULL_BLOCK_PCT: return None

        closes, highs, lows, volumes, opens = self._klines(symbol, '5m', 80)
        if not closes or len(closes) < 30: return None

        ticker = self._ticker(symbol)
        if not ticker or ticker['price'] <= 0: return None

        price  = ticker['price']
        change = ticker['change']

        # ── Indicadores base ────────────────────────────────────────
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

        # ── Moving Average Envelopes ─────────────────────────────────
        mae_score, mae_desc, mae_regime, mae_pos, mae_upper, mae_ma, mae_lower = \
            mae_regime_score(closes, MAE_PERIOD, MAE_PCT)

        # ── Régimen de mercado ───────────────────────────────────────
        regime = detect_market_regime(closes, MAE_PERIOD)

        # ── Patrones chartistas ──────────────────────────────────────
        pattern_total = 0
        pattern_list  = []
        if PATTERN_SCORE:
            pattern_total, pattern_list = scan_patterns(closes, highs, lows, volumes)

        # ── Filtro de régimen ────────────────────────────────────────
        # En mercado alcista sin patrones de reversión → skip
        if REGIME_FILTER and regime == "TREND_UP" and not pattern_list:
            if self._btc_change_1h < 0:
                pass
            else:
                return None

        # ── FILTROS OBLIGATORIOS v3.3 — deben cumplirse TODOS ────────
        # 1. RSI mínimo: no entrar si el activo no está sobrecomprado
        rsi_max = max(rsi, calc_rsi(closes[-20:], 10))
        if rsi_max < 65:
            return None   # RSI demasiado bajo — no hay sobrecompra

        # 2. Volumen mínimo: el spike debe ser real
        if vs < 1.2:
            return None   # volumen plano — señal sin convicción

        # 3. ATR mínimo: necesitamos movimiento para llegar al TP
        if atr_pct < 0.4:
            return None   # mercado dormido — el TP nunca se alcanzará

        # 4. BTC pánico bajista: si BTC cae >3% en 1h hay riesgo de reversal
        btc_panic_down = self._btc_change_1h < -3.0
        if btc_panic_down:
            return None   # pánico = rebote inminente = shorts en trampa

        # 5. Confirmación de cierre: al menos 2 velas rojas recientes
        if opens:
            recent_red = sum(1 for i in range(-3, 0) if closes[i] < opens[i])
            if recent_red < 1:
                return None  # sin presión vendedora confirmada
        if not (ema9 < ema21 < ema50):
            # v3.3: solo patrón muy fuerte (≥35) puede saltarse EMA
            # pero penalización -15 se aplica igualmente en el scoring
            if not pattern_list or pattern_total < 35:
                return None

        # ── Scoring v3.3 ─────────────────────────────────────────────
        score_min = MIN_SCORE + (10 if self._btc_change_1h > 0.5 else 0)
        ss, sr = 0, []

        # EMA: penaliza explícitamente si no alineado (v3.3)
        if ema9 < ema21 < ema50:
            p = min(35, 28 + int(ema_gap * 4)) if ema_gap > 1.5 else min(28, 20 + int(ema_gap * 5))
            ss += p; sr.append(f"EMA_OK({p})")
        else:
            ss -= 15; sr.append("EMA_ROTA(-15)")   # v3.3: penaliza siempre

        # RSI: umbrales subidos — solo suma si realmente sobrecomprado (v3.3)
        if   rsi_max > 82: ss += 38; sr.append(f"RSI{rsi_max:.0f}(38)")
        elif rsi_max > 76: ss += 28; sr.append(f"RSI{rsi_max:.0f}(28)")
        elif rsi_max > 70: ss += 18; sr.append(f"RSI{rsi_max:.0f}(18)")
        elif rsi_max > 65: ss += 8;  sr.append(f"RSI{rsi_max:.0f}(8)")
        # < 65 ya fue bloqueado por filtro obligatorio

        # MACD
        if ml < sg and hist < 0:
            p = 22 if abs(hist) > abs(ml) * 0.35 else 15
            ss += p; sr.append(f"MACD-({p})")
        elif ml > 0 and hist > 0:
            ss -= 15; sr.append("MACD+(-15)")

        # Bollinger Bands
        if   bb_pos >= 0.95: ss += 25; sr.append("BB_top(25)")
        elif bb_pos >= 0.85: ss += 17; sr.append("BB_high(17)")
        elif bb_pos >= 0.70: ss += 8;  sr.append("BB_mid+(8)")
        elif bb_pos <  0.40: ss -= 12; sr.append("BB_low(-12)")

        # Volumen — ya filtrado: vs>=1.2 garantizado (v3.3)
        if vs >= 2.0 and trend_5 < -0.3:
            p = min(18, int(vs*8)); ss += p; sr.append(f"VolVenta{vs:.1f}x({p})")
        elif vs >= 1.5:
            p = min(12, int(vs*6)); ss += p; sr.append(f"Vol{vs:.1f}x({p})")
        elif vs >= 1.2:
            ss += 4; sr.append(f"Vol{vs:.1f}x(4)")
        # < 1.2 ya fue bloqueado

        # Tendencia corta
        if trend_5 < -1.5 and trend_10 < -2.5: ss += 20; sr.append("Bajada--(20)")
        elif trend_5 < -0.8:                    ss += 12; sr.append("Bajada-(12)")
        elif trend_5 > 1.0:                     ss -= 15; sr.append("Subida(-15)")

        # Cambio 24h
        if   change > 6.0: p = min(15, int(change*2));   ss += p; sr.append(f"24h+{change:.1f}%({p})")
        elif change > 3.0: p = min(10, int(change*1.5)); ss += p; sr.append(f"24h+{change:.1f}%({p})")
        elif change < -4.0: ss -= 12; sr.append(f"24h{change:.1f}%(-12)")

        # Otros
        if near_high:        ss += 12; sr.append("NearHigh(12)")
        if red_candles >= 3: ss += 10; sr.append(f"Rojas{red_candles}(10)")
        elif red_candles >= 2: ss += 5; sr.append(f"Rojas{red_candles}(5)")
        # ATR ya filtrado >= 0.4%
        if atr_pct > 1.5:  ss += 8;  sr.append(f"ATR{atr_pct:.1f}%(8)")
        elif atr_pct > 0.8: ss += 4; sr.append(f"ATR{atr_pct:.1f}%(4)")

        # ── MAE score (nuevo v3.0) ───────────────────────────────────
        if mae_score != 0:
            ss += mae_score; sr.append(mae_desc)

        # ── Régimen bonus/penalización (nuevo v3.0) ──────────────────
        if regime == "TREND_DOWN":
            ss += 10; sr.append("RegimeBajista(+10)")
        elif regime == "RANGING":
            ss += 5;  sr.append("RegimeRango(+5)")
        elif regime == "TREND_UP":
            ss -= 10; sr.append("RegimeAlcista(-10)")

        # ── Patrones chartistas (nuevo v3.0) ─────────────────────────
        if pattern_total > 0:
            ss += pattern_total
            for p_name in pattern_list:
                sr.append(p_name)

        # ── TP/SL dinámicos — v3.3: RR mínimo garantizado 1.7:1 ────────
        # SL mínimo = max(SL_PCT, ATR*0.8) para no saltar por ruido
        sl_dyn = max(SL_PCT, atr_pct * 0.8) if atr_pct > 0 else SL_PCT
        sl_dyn = round(min(sl_dyn, SL_PCT * 2.0), 3)   # cap: no más del doble del SL configurado

        # TP mínimo = max(TP_PCT, SL*1.7, ATR*2) — RR≥1.7 garantizado
        tp_dyn = max(TP_PCT, sl_dyn * 1.7, atr_pct * 2.0, TP_MIN_RENTABLE)
        tp_dyn = round(min(tp_dyn, TP_PCT * 2.5), 3)   # cap: no más de 2.5x el TP configurado

        # Con patrón fuerte, TP más ambicioso (RR≥2)
        if pattern_total >= 30:
            tp_dyn = max(tp_dyn, sl_dyn * 2.0)

        if ss >= score_min:
            return {
                'price':price,'change':change,'score':ss,'reasons':' | '.join(sr),
                'rsi':rsi,'vol':vs,'tp_pct':tp_dyn,'sl_pct':sl_dyn,
                'bb_pos':round(bb_pos*100,1),'atr_pct':round(atr_pct,2),
                'score_min':score_min,'regime':regime,'mae_regime':mae_regime,
                'mae_pos':round(mae_pos*100,1),'patterns':pattern_list,
                'rr':round(tp_dyn/sl_dyn,2),
            }
        return None

    # ---------------------------------------------------------------- órdenes

    def _place_short_entry(self, symbol, usdt_qty, price):
        """
        v3.5 FIX DEFINITIVO: NUNCA usa quoteOrderQty.
        quoteOrderQty es ambiguo en BingX — según el modo de cuenta ("Por costo" vs
        "Por valor") puede ser el margen o el notional, lo que causa trades de 1-2 USDT.
        Solución: siempre quantity en contratos calculado por _qty_contratos().
        """
        usdt_qty = max(usdt_qty, FORCE_MIN_USDT, MIN_TRADE)
        qty_c, qty_val = self._qty_contratos(symbol, price, usdt_qty)

        if not qty_c or qty_c <= 0:
            log.error(f"  ENTRADA ABORTADA {symbol}: _qty_contratos devolvió 0")
            return None, None

        # Validar notional antes de enviar
        notional_ok, notional_val = self._notional_ok(symbol, qty_c, price)
        if not notional_ok:
            log.error(f"  ENTRADA ABORTADA {symbol}: notional ${notional_val:.2f} < ${FORCE_MIN_USDT}")
            return None, None

        log.info(f"  SHORT {symbol}: {qty_c} contratos = ${notional_val:.2f} USDT notional")

        # ── Método 1: LIMIT (maker 0.02%) ─────────────────────────────
        if USE_LIMIT_ORDERS:
            limit_price = round(price * (1 + LIMIT_OFFSET_PCT / 100), 8)
            d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':      symbol,
                'side':        'SELL',
                'positionSide':'SHORT',
                'type':        'LIMIT',
                'price':       str(limit_price),
                'quantity':    str(qty_c),   # siempre contratos
                'timeInForce': 'GTC',
            }).json()
            if d.get('code') == 0:
                log.info(f"  ✅ LIMIT maker {qty_c} cts @ ${limit_price:.6f} (${notional_val:.2f})")
                return d.get('data', {}).get('orderId', 'OK'), qty_c
            if 'margin' in str(d.get('msg', '')).lower():
                log.error(f"  Margen insuficiente"); return None, None
            log.warning(f"  LIMIT falló [{d.get('code')}] {d.get('msg','')} — MARKET")

        # ── Método 2: MARKET quantity (taker 0.05%) ────────────────────
        d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol':      symbol,
            'side':        'SELL',
            'positionSide':'SHORT',
            'type':        'MARKET',
            'quantity':    str(qty_c),   # siempre contratos, nunca quoteOrderQty
        }).json()
        if d.get('code') == 0:
            log.info(f"  ✅ MARKET {qty_c} cts (${notional_val:.2f})")
            return d.get('data', {}).get('orderId', 'OK'), qty_c

        log.error(f"  ❌ TODOS FALLARON [{d.get('code')}]: {d.get('msg')}")
        return None, None

    def _esperar_posicion(self, symbol, timeout=60):
        """
        FIX-4: Espera confirmación real de BingX antes de poner TP/SL.
        Retorna (qty_real, entry_real) o (None, None) si timeout.
        """
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
        """Cancela todas las órdenes abiertas de un símbolo."""
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
        """
        FIX-1 (FLOOP v4): órdenes condicionales maker-first.

        TP → TAKE_PROFIT límite (price + stopPrice) → maker 0.02%
             Fallback: TAKE_PROFIT_MARKET → taker 0.05%

        SL → STOP límite con offset SL_LIMIT_OFFSET hacia arriba
             (para SHORT: trigger es stopPrice, límite queda ligeramente POR ENCIMA
             → el precio sube hasta el trigger, dispara, el límite se llena maker)
             Fallback: STOP_MARKET → taker 0.05%

        Ahorro vs todo-taker: ~0.06% por trade (×3 lev = 0.18% del notional).
        """
        if not qty_c or qty_c <= 0:
            log.error(f"  {otype} cancelado: qty_c inválido ({qty_c})")
            return False
        try:
            is_tp = "TAKE" in otype
            lbl   = "TP" if is_tp else "SL"

            if is_tp:
                # TP límite: price + stopPrice = orden límite condicionada (maker)
                params = {
                    'symbol':      symbol,
                    'side':        'BUY',
                    'positionSide':'SHORT',
                    'type':        'TAKE_PROFIT',
                    'quantity':    str(qty_c),
                    'price':       str(round(stop_price, 8)),
                    'stopPrice':   str(round(stop_price, 8)),
                    'timeInForce': 'GTC',
                }
                d  = bingx_request('POST', '/openApi/swap/v2/trade/order', params).json()
                ok = d.get('code') == 0
                if ok:
                    log.info(f"  TP ✅ límite maker @ ${stop_price:.6f} (qty={qty_c}, 0.02%)")
                else:
                    log.warning(f"  TP límite rechazado [{d.get('code')}] {d.get('msg','')[:40]} — fallback market")
                    p2 = {
                        'symbol':symbol,'side':'BUY','positionSide':'SHORT',
                        'type':'TAKE_PROFIT_MARKET','quantity':str(qty_c),
                        'stopPrice':str(round(stop_price, 8)),
                    }
                    d2 = bingx_request('POST', '/openApi/swap/v2/trade/order', p2).json()
                    ok = d2.get('code') == 0
                    if ok:  log.info(f"  TP ✅ market fallback @ ${stop_price:.6f} (0.05%)")
                    else:   log.error(f"  TP ❌ [{d2.get('code')}]: {d2.get('msg')}")

            else:
                # SL límite: para SHORT trigger sube hasta stopPrice, límite queda encima → maker
                limit_price = round(stop_price * (1 + SL_LIMIT_OFFSET), 8)
                params = {
                    'symbol':      symbol,
                    'side':        'BUY',
                    'positionSide':'SHORT',
                    'type':        'STOP',
                    'quantity':    str(qty_c),
                    'price':       str(limit_price),
                    'stopPrice':   str(round(stop_price, 8)),
                    'timeInForce': 'GTC',
                }
                d  = bingx_request('POST', '/openApi/swap/v2/trade/order', params).json()
                ok = d.get('code') == 0
                if ok:
                    log.info(f"  SL ✅ límite maker trigger=${stop_price:.6f} límite=${limit_price:.6f} (0.02%)")
                else:
                    log.warning(f"  SL límite rechazado [{d.get('code')}] {d.get('msg','')[:40]} — fallback STOP_MARKET")
                    p2 = {
                        'symbol':symbol,'side':'BUY','positionSide':'SHORT',
                        'type':'STOP_MARKET','quantity':str(qty_c),
                        'stopPrice':str(round(stop_price, 8)),
                    }
                    d2 = bingx_request('POST', '/openApi/swap/v2/trade/order', p2).json()
                    ok = d2.get('code') == 0
                    if ok:  log.info(f"  SL ✅ STOP_MARKET fallback @ ${stop_price:.6f} (0.05%)")
                    else:   log.error(f"  SL ❌ [{d2.get('code')}]: {d2.get('msg')}")

            return ok
        except Exception as e:
            log.error(f"  {otype} excepción: {e}")
            return False

    def _close_short(self, symbol, t):
        """v3.5: cierre siempre con quantity en contratos. Sin quoteOrderQty."""
        qty_c = t.get('qty_c', 0)
        if not qty_c or qty_c <= 0:
            log.error(f"  Cierre {symbol}: sin qty_c — no se puede cerrar"); return False

        # Intentar límite IOC (maker 0.02%)
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
                log.info(f"  Cierre LIMIT IOC maker {qty_c} cts @ ${limit_price:.6f}")
                return True

        # Fallback MARKET con quantity (nunca quoteOrderQty)
        d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol':symbol,'side':'BUY','positionSide':'SHORT',
            'type':'MARKET','quantity':str(qty_c),'reduceOnly':'true',
        }).json()
        ok = d.get('code') == 0
        if ok: log.info(f"  Cierre MARKET {qty_c} cts OK")
        else:  log.error(f"  Cierre MARKET falló [{d.get('code')}]: {d.get('msg')}")
        return ok

    def _contar_posiciones_reales(self):
        """v3.5: cuenta posiciones SHORT reales en BingX antes de abrir."""
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/positions', {}).json()
            if d.get('code') == 0:
                shorts = sum(
                    1 for p in (d.get('data') or [])
                    if float(p.get('positionAmt', 0) or 0) < 0
                )
                log.info(f"  [REAL] Posiciones SHORT en BingX: {shorts}/{MAX_TRADES}")
                return shorts
        except Exception as e:
            log.warning(f"  [REAL] Error contando posiciones: {e}")
        return len(self.open_trades)

    def _set_leverage(self, symbol):
        """v3.6: fuerza leverage en BingX antes de abrir — hard cap a LEVERAGE (max 3x)."""
        try:
            for side in ['LONG', 'SHORT']:
                bingx_request('POST', '/openApi/swap/v2/trade/leverage', {
                    'symbol':   symbol,
                    'side':     side,
                    'leverage': str(LEVERAGE),  # ya capeado a 3 por hard cap
                })
            log.info(f"  Leverage {symbol} → {LEVERAGE}x (ambos lados)")
        except Exception as e:
            log.warning(f"  _set_leverage {symbol}: {e}")

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

    # v3.6: lock para evitar aperturas simultáneas
    _abriendo = False

    def open_trade(self, symbol, sig):
        if not AUTO_TRADING:
            log.info(f"  [SEÑAL] SHORT {symbol} score:{sig['score']:.0f} régimen:{sig['regime']}")
            if sig['patterns']:
                log.info(f"  Patrones: {', '.join(sig['patterns'])}")
            return False
        if symbol in self.open_trades: return False

        # v3.6: bloquear si ya se está abriendo otro trade
        if ShortBot._abriendo:
            log.info(f"  {symbol} — skip: ya abriendo otro trade"); return False

        # v3.6: verificar posiciones SHORT reales en BingX
        pos_reales = self._contar_posiciones_reales()
        if pos_reales >= MAX_TRADES:
            log.info(f"  Max trades en BingX: {pos_reales}/{MAX_TRADES} — skip"); return False

        tiene, dir_bx = self._tiene_posicion(symbol)
        if tiene: log.info(f"  {symbol} ya tiene {dir_bx} — skip"); return False

        ShortBot._abriendo = True
        try:
            return self._open_trade_inner(symbol, sig)
        finally:
            ShortBot._abriendo = False

    def _open_trade_inner(self, symbol, sig):
        """Lógica real de apertura — llamada solo cuando el lock está activo."""
        price    = sig['price']
        usdt_qty = round(max(POSITION_SIZE, FORCE_MIN_USDT, MIN_TRADE), 2)

        # pre-validación antes de enviar orden
        test_qty, test_val = self._qty_contratos(symbol, price, usdt_qty)
        if not test_qty or test_val < FORCE_MIN_USDT:
            log.warning(f"  {symbol} rechazado: valor USDT estimado ${test_val:.2f} < ${FORCE_MIN_USDT}")
            return False

        tp_price = price * (1 - sig['tp_pct'] / 100)
        sl_price = price * (1 + sig['sl_pct'] / 100)

        patterns_str = f"Patrones: {', '.join(sig['patterns'])}" if sig['patterns'] else "Sin patrón chartista"
        log.info(f"\n  ➤ SHORT {symbol}")
        log.info(f"  Score:{sig['score']:.0f}/{sig['score_min']:.0f} | RSI:{sig['rsi']:.0f} | "
                 f"BB:{sig['bb_pos']}% | Régimen:{sig['regime']} MAE:{sig['mae_regime']}")
        log.info(f"  MAE pos:{sig['mae_pos']}% | {patterns_str}")
        log.info(f"  {sig['reasons']}")
        log.info(f"  Entry:${price:.6f} | Capital:${usdt_qty} | TP:{sig['tp_pct']:.2f}% SL:{sig['sl_pct']:.2f}% RR:{sig.get('rr',0):.2f}:1")

        # v3.6: forzar leverage en BingX antes de abrir
        self._set_leverage(symbol)

        oid, qty_c = self._place_short_entry(symbol, usdt_qty, price)
        if not oid:
            log.error(f"  No se pudo abrir {symbol}"); return False

        # FIX-4: esperar confirmación real de BingX antes de poner TP/SL
        qty_real, entry_real = self._esperar_posicion(symbol, timeout=60)
        if qty_real is None:
            # No se confirmó — intentar con market directo
            self._cancelar_ordenes(symbol)
            time.sleep(0.5)
            d_mkt = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol,'side':'SELL','positionSide':'SHORT',
                'type':'MARKET','quantity':str(qty_c),
            }).json()
            if d_mkt.get('code') == 0:
                qty_real, entry_real = self._esperar_posicion(symbol, timeout=30)
            if qty_real is None:
                # FIX crítico: no dejar posición sin TP/SL — cerrar de emergencia
                log.error(f"  CRÍTICO: No se pudo confirmar posición {symbol} — cerrando")
                self._tg(f"<b>🚨 CRÍTICO {symbol}</b>\nNo se confirmó posición.\nCerrando de emergencia para evitar liquidación.")
                for _ in range(2):
                    try:
                        bingx_request('POST', '/openApi/swap/v2/trade/order', {
                            'symbol':symbol,'side':'BUY','positionSide':'SHORT',
                            'type':'MARKET','quantity':str(qty_c),'reduceOnly':'true',
                        })
                        time.sleep(1)
                    except: pass
                return False

        # Usar qty y precio reales de BingX para TP/SL
        qty_final   = qty_real if qty_real else qty_c
        entry_final = entry_real if (entry_real and entry_real > 0) else price
        tp_price    = entry_final * (1 - sig['tp_pct'] / 100)
        sl_price    = entry_final * (1 + sig['sl_pct'] / 100)

        # FIX-3: 6 reintentos con backoff (1s,2s,3s,5s,8s,13s)
        # Cancela órdenes colgadas antes de cada reintento
        tp_ok = self._cond_order(symbol, qty_final, tp_price, 'TAKE_PROFIT_MARKET')
        time.sleep(0.3)
        sl_ok = self._cond_order(symbol, qty_final, sl_price, 'STOP_MARKET')

        for delay in [1, 2, 3, 5, 8, 13]:
            if tp_ok and sl_ok: break
            log.warning(f"  TP:{tp_ok} SL:{sl_ok} — cancelando órdenes y reintentando en {delay}s")
            self._cancelar_ordenes(symbol)
            time.sleep(delay)
            if not tp_ok:
                tp_ok = self._cond_order(symbol, qty_final, tp_price, 'TAKE_PROFIT_MARKET')
            if not sl_ok:
                sl_ok = self._cond_order(symbol, qty_final, sl_price, 'STOP_MARKET')

        # FIX crítico: si el SL no se pudo poner tras 6 intentos → cerrar inmediatamente
        if not sl_ok:
            log.error(f"  CRÍTICO: SL fallido en {symbol} tras 6 intentos — cerrando posición")
            self._tg(
                f"<b>🚨 CRÍTICO SL FALLIDO — SHORT {symbol}</b>\n"
                f"No se pudo colocar SL tras 6 intentos.\n"
                f"Cerrando posición para evitar liquidación.\n"
                f"Entrada fue: ${entry_final:.6f}"
            )
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

        status_tp = "✅" if tp_ok else "❌ FIJAR MANUAL"
        status_sl = "✅" if sl_ok else "❌ FIJAR MANUAL"
        pat_str   = f"\nPatrones: {', '.join(sig['patterns'])}" if sig['patterns'] else ""
        self._tg(
            f"<b>🔴 SHORT ABIERTO</b>\n<b>{symbol}</b> | Score:{sig['score']:.0f}/100\n"
            f"Entrada: ${entry_final:.6f}\n"
            f"{status_tp} TP: ${tp_price:.6f} (-{sig['tp_pct']:.2f}%)\n"
            f"{status_sl} SL: ${sl_price:.6f} (+{sig['sl_pct']:.1f}%)\n"
            f"Capital: ${usdt_qty} x{LEVERAGE} = ${usdt_qty*LEVERAGE:.1f} USDT\n"
            f"Contratos: {qty_final} | RSI:{sig['rsi']:.0f} BB:{sig['bb_pos']}%\n"
            f"Régimen: {sig['regime']} | MAE: {sig['mae_regime']} pos:{sig['mae_pos']}%"
            f"{pat_str}\n"
            f"{sig['reasons']}"
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
        reason_cd = 'TP' if 'PROFIT' in reason else 'SL'  # v3.2: cooldown diferenciado

        log.info(f"  {emoji} {reason} {symbol} PnL:${pnl:+.3f}({pnl_pct:+.1f}%) {mins}min")
        self._tg(
            f"<b>{emoji} SHORT CERRADO — {reason}</b>\n<b>{symbol}</b>\n"
            f"PnL: ${pnl:+.3f} ({pnl_pct:+.1f}%)\n"
            f"Entry: ${t['entry']:.6f} → Exit: ${cur_price:.6f}\n"
            f"Duración: {mins} min\n"
            f"Cooldown: {COOLDOWN_MIN_TP if reason_cd=='TP' else COOLDOWN_MIN_SL}min\n"
            f"<b>Total: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}% ({self.stats['wins']}W/{self.stats['losses']}L)</b>"
        )
        self._set_cooldown(symbol, reason_cd)  # v3.2
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
                    reason_cd = 'TP' if pnl >= 0 else 'SL'  # v3.2
                    self._tg(f"<b>{emoji} SHORT cerrado BingX</b>\n<b>{sym}</b>\n"
                             f"PnL: ${pnl:+.3f} ({pnl_pct:+.1f}%) | {mins}min\n"
                             f"Total: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}%")
                    self._set_cooldown(sym, reason_cd)  # v3.2
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
                    if pnl_pct >= TRAILING_START:   # v3.2: usa variable TRAILING_START
                        new_sl = t['entry'] - (t['entry'] - cur) * (TRAILING_LOCK / 100)
                        if new_sl < t['sl']:
                            t['sl'] = new_sl
                            log.info(f"  Trailing SL {sym}: ${new_sl:.6f} (lock {TRAILING_LOCK:.0f}%)")

                # FIX-5: seguro final contra liquidación si el SL no se ejecutó
                pnl_leverage = pnl_pct * LEVERAGE
                if pnl_leverage < -MAX_LOSS_PCT:
                    log.error(f"  EMERGENCIA {sym}: PnL {pnl_leverage:+.1f}% < -{MAX_LOSS_PCT}% — cerrando")
                    self._tg(f"<b>🚨 EMERGENCIA MAX LOSS {sym}</b>\nPnL: {pnl_leverage:+.1f}% supera límite -{MAX_LOSS_PCT}%\nCerrando para evitar liquidación.")
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
        pos_txt = "".join(
            f"  {sym}: {(t['entry']-(self._ticker(sym) or {'price':t['entry']})['price'])/t['entry']*100:+.2f}%\n"
            for sym, t in self.open_trades.items()
        )
        self._tg(
            f"<b>📊 Reporte horario</b>\n"
            f"PnL: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}%\n"
            f"({self.stats['wins']}W/{self.stats['losses']}L | {self.stats['closed']} trades)\n"
            f"Abiertos: {len(self.open_trades)}/{MAX_TRADES} | BTC 1h:{self._btc_change_1h:+.2f}%\n"
            + (pos_txt if pos_txt else "  sin posiciones\n")
        )

    def _tg(self, msg):
        try:
            if TELEGRAM_TOKEN and TELEGRAM_CHAT:
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={'chat_id':TELEGRAM_CHAT,'text':msg,'parse_mode':'HTML'}, timeout=6)
        except: pass

    # ---------------------------------------------------------------- loop

    async def run(self):
        log.info("\n▶  Bot SHORT v3.5 arrancado\n")
        iteration, last_refresh = 0, 0
        while True:
            try:
                iteration += 1
                if time.time() - last_refresh > 600:
                    self._get_symbols(); last_refresh = time.time()

                self._update_btc_trend()

                total = self.stats['wins'] + self.stats['losses']
                wr    = self.stats['wins'] / total * 100 if total else 0
                btc_st = "⚠️ BLOQUEADO" if self._btc_change_1h >= BTC_BULL_BLOCK_PCT else "OK"
                hora_st = "🌙 HORA BAJA" if not self._hora_ok() else "☀️"

                log.info(f"\n{'='*70}")
                log.info(f"  #{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                         f"Abiertos:{len(self.open_trades)}/{MAX_TRADES} | "
                         f"PnL:${self.stats['pnl']:+.3f} | WR:{wr:.1f}%")
                log.info(f"  BTC 1h:{self._btc_change_1h:+.2f}% {btc_st} | {hora_st}")
                log.info(f"{'='*70}\n")

                await self.monitor_trades()
                self._reporte_horario()

                # v3.5 FIX: usar posiciones reales de BingX para evitar exceder MAX_TRADES
                pos_reales = self._contar_posiciones_reales()
                slots_libres = MAX_TRADES - max(pos_reales, len(self.open_trades))
                log.info(f"  Slots libres: {slots_libres} (BingX={pos_reales} local={len(self.open_trades)})")

                if slots_libres > 0:
                    found = 0
                    for i, sym in enumerate(self.symbols):
                        if max(self._contar_posiciones_reales(), len(self.open_trades)) >= MAX_TRADES:
                            break
                        sig = self.analyze(sym)
                        if sig:
                            found += 1
                            pat_str = f" [{','.join(sig['patterns'])}]" if sig['patterns'] else ""
                            log.info(f"  ★ {sym} score:{sig['score']:.0f} RSI:{sig['rsi']:.0f} "
                                     f"régimen:{sig['regime']}{pat_str}")
                            abierto = self.open_trade(sym, sig)
                            if abierto:
                                await asyncio.sleep(3)  # pausa tras apertura
                        await asyncio.sleep(0.15)
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
        await ShortBot().run()
    except Exception as e:
        log.error(f"Error fatal: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Terminado")
