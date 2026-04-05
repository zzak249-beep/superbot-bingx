#!/usr/bin/env python3
"""
WYCKOFF SMC BOT v1.2 — OPTIMIZADO PARA RENTABILIDAD
════════════════════════════════════════════════════════════════
CAMBIOS v1.2 (CRÍTICOS PARA RENTABILIDAD):
  ✅ MIN_SCORE subido a 85 (era 60, generaba señales basura)
  ✅ TP/SL ratio mejorado: TP=3.5% / SL=1.2% → RR=2.9:1
  ✅ LEVERAGE reducido a 2x (era 3x, amplificaba pérdidas)
  ✅ MAX_LOSS_PCT reducido a 4% (era 8%, muy permisivo)
  ✅ CIRCUIT_PCT reducido a 6% (era 12%, activación más rápida)
  ✅ Filtro adicional: bloquear trades con señales contradictorias
  ✅ Filtro BTC: no operar si BTC cae >2% en 1h
  ✅ Cooldown extendido después de SL: 45min (era 25min)
════════════════════════════════════════════════════════════════

MATEMÁTICA DE RENTABILIDAD:
- Con WR 40% y RR 2.9:1 → 0.40×2.9 - 0.60×1 = +0.56% por trade
- Objetivo: subir WR a 45%+ con filtros más estrictos
════════════════════════════════════════════════════════════════
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re
from datetime import datetime, timedelta
from urllib.parse import urlencode
from collections import deque

# ============================================================================
# CONFIGURACIÓN OPTIMIZADA
# ============================================================================

def clean(key, default, typ='str'):
    v = os.getenv(key, str(default)).strip().strip('"').strip("'").strip()
    if typ in ('int', 'float'):
        v = v.replace(',', '.')
        m = re.match(r'^-?\d+\.?\d*', v)
        v = m.group(0) if m else str(default)
    if typ == 'int':   return int(float(v))
    if typ == 'float': return float(v))
    if typ == 'bool':  return v.lower() == 'true'
    return v

BINGX_API_KEY    = os.getenv('BINGX_API_KEY',    '').strip().strip('"').strip("'")
BINGX_API_SECRET = os.getenv('BINGX_API_SECRET', '').strip().strip('"').strip("'")
TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT    = os.getenv('TELEGRAM_CHAT_ID',   '')

AUTO_TRADING  = clean('AUTO_TRADING_ENABLED',   'true',  'bool')
POSITION_SIZE = clean('WYK_POSITION_SIZE',       '10',   'float')

# ═══════════════════════════════════════════════════════════════
# OPTIMIZACIÓN CRÍTICA #1: TP/SL ratio mejorado
# ═══════════════════════════════════════════════════════════════
TP_PCT        = clean('WYK_TAKE_PROFIT_PCT',     '3.5',  'float')  # Era 2.5%
SL_PCT        = clean('WYK_STOP_LOSS_PCT',       '1.2',  'float')  # Era 1.5%
# RR = 3.5/1.2 = 2.9:1 (con 40% WR → +0.56% esperado por trade)

INTERVAL      = clean('WYK_CHECK_INTERVAL',      '60',   'int')
MIN_VOLUME    = clean('WYK_MIN_VOLUME_24H',  '800000',   'float')
MAX_SYMBOLS   = clean('WYK_MAX_SYMBOLS',         '40',   'int')

# ═══════════════════════════════════════════════════════════════
# OPTIMIZACIÓN CRÍTICA #2: MIN_SCORE más estricto
# ═══════════════════════════════════════════════════════════════
MIN_SCORE     = clean('WYK_MIN_SCORE',           '85',   'float')  # Era 60

USE_LIMIT     = clean('WYK_USE_LIMIT_ORDERS',  'true',   'bool')
TRAILING      = clean('WYK_TRAILING_ENABLED',  'true',   'bool')
TRAILING_START= clean('WYK_TRAILING_START',     '1.2',   'float')  # Era 0.8%
TRAILING_LOCK = clean('WYK_TRAILING_LOCK',       '60',   'float')  # Era 55%
COOLDOWN_TP   = clean('WYK_COOLDOWN_TP_MIN',     '15',   'int')    # Era 10min

# ═══════════════════════════════════════════════════════════════
# OPTIMIZACIÓN CRÍTICA #3: Cooldown SL extendido
# ═══════════════════════════════════════════════════════════════
COOLDOWN_SL   = clean('WYK_COOLDOWN_SL_MIN',     '45',   'int')    # Era 25min

# ═══════════════════════════════════════════════════════════════
# OPTIMIZACIÓN CRÍTICA #4: Stop loss de emergencia más agresivo
# ═══════════════════════════════════════════════════════════════
MAX_LOSS_PCT  = clean('WYK_MAX_LOSS_PCT',        '4.0',  'float')  # Era 8%

# ═══════════════════════════════════════════════════════════════
# OPTIMIZACIÓN CRÍTICA #5: Circuit breaker más sensible
# ═══════════════════════════════════════════════════════════════
CIRCUIT_PCT   = clean('WYK_CIRCUIT_BREAKER_PCT', '6.0',  'float')  # Era 12%

ALLOW_SHORT   = clean('WYK_ALLOW_SHORT',        'true',  'bool')

_lev_env    = clean('WYK_LEVERAGE',        '2', 'int')  # REDUCIDO DE 3 A 2
_trades_env = clean('WYK_MAX_OPEN_TRADES', '3', 'int')
_min_env    = clean('WYK_FORCE_MIN_USDT',  '8.0', 'float')

# ═══════════════════════════════════════════════════════════════
# OPTIMIZACIÓN CRÍTICA #6: Leverage reducido
# ═══════════════════════════════════════════════════════════════
LEVERAGE       = min(_lev_env,    2)  # Era 3x
MAX_TRADES     = min(_trades_env, 3)
FORCE_MIN_USDT = max(_min_env,    8.0)
MIN_TRADE      = 8.0

BASE_URL       = "https://open-api.bingx.com"
COMISION       = 0.0002 if USE_LIMIT else 0.0005
SL_OFFSET      = 0.0005

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

# ============================================================================
# API (sin cambios)
# ============================================================================

def bingx(method, endpoint, params, retries=2):
    for attempt in range(retries + 1):
        try:
            p = dict(params)
            p['timestamp'] = int(time.time() * 1000)
            qs  = urlencode(sorted(p.items()))
            sig = hmac.new(BINGX_API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
            url = f"{BASE_URL}{endpoint}?{qs}&signature={sig}"
            hdr = {'X-BX-APIKEY': BINGX_API_KEY, 'Content-Type': 'application/x-www-form-urlencoded'}
            r = requests.get(url, headers=hdr, timeout=12) if method == 'GET' \
                else requests.post(url, headers=hdr, timeout=12)
            return r
        except Exception as e:
            if attempt < retries: time.sleep(1.5)
            else: raise

def klines(symbol, interval='15m', limit=100):
    try:
        d = requests.get(f"{BASE_URL}/openApi/swap/v3/quote/klines",
            params={'symbol': symbol, 'interval': interval, 'limit': limit}, timeout=10).json()
        if d.get('code') == 0 and d.get('data'):
            k = d['data']
            return {
                'close':  [float(x['close'])  for x in k],
                'high':   [float(x['high'])   for x in k],
                'low':    [float(x['low'])     for x in k],
                'open':   [float(x['open'])    for x in k],
                'volume': [float(x['volume'])  for x in k],
            }
    except: pass
    return None

def ticker(symbol):
    try:
        d = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker",
                         params={'symbol': symbol}, timeout=8).json()
        if d.get('code') == 0 and d.get('data'):
            t = d['data']
            return {'price': float(t.get('lastPrice', 0)),
                    'change': float(t.get('priceChangePercent', 0))}
    except: pass
    return None

# ============================================================================
# INDICADORES BASE (sin cambios)
# ============================================================================

def ema(prices, period):
    if not prices or len(prices) < 1: return 0
    if len(prices) < period: return sum(prices) / len(prices)
    k, e = 2 / (period + 1), prices[0]
    for p in prices[1:]: e = p * k + e * (1 - k)
    return e

def sma(prices, period):
    if len(prices) < period: return sum(prices) / len(prices)
    return sum(prices[-period:]) / period

def rsi(prices, period=14):
    if len(prices) < period + 1: return 50.0
    gains  = [max(0,  prices[i] - prices[i-1]) for i in range(1, len(prices))]
    losses = [max(0, prices[i-1] - prices[i])  for i in range(1, len(prices))]
    ag = sum(gains[-period:])  / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100.0
    return 100 - (100 / (1 + ag / al))

def atr(highs, lows, closes, period=14):
    if len(closes) < 2: return 0
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, min(len(closes), period+1))]
    return sum(trs) / len(trs) if trs else 0

def vol_spike(volumes):
    if len(volumes) < 5: return 1.0
    avg = sum(volumes[:-1]) / len(volumes[:-1])
    return volumes[-1] / avg if avg > 0 else 1.0

# ============================================================================
# MÓDULO 1 — ZERO LAG EMA (sin cambios en lógica, solo ajustes de pesos)
# ============================================================================

def zlema(prices, period=21):
    if len(prices) < period: return ema(prices, period)
    lag = (period - 1) // 2
    adjusted = []
    for i in range(len(prices)):
        if i >= lag:
            adjusted.append(2 * prices[i] - prices[i - lag])
        else:
            adjusted.append(prices[i])
    return ema(adjusted, period)

def zlema_signal(closes, fast=8, slow=21, signal=9):
    if len(closes) < slow + 5:
        return 'NEUTRAL', 0, "ZLEMA_insuf"

    zl_fast = zlema(closes, fast)
    zl_slow = zlema(closes, slow)
    zl_fast_prev = zlema(closes[:-1], fast)
    zl_slow_prev = zlema(closes[:-1], slow)

    gap     = (zl_fast - zl_slow) / zl_slow * 100 if zl_slow > 0 else 0
    gap_prev= (zl_fast_prev - zl_slow_prev) / zl_slow_prev * 100 if zl_slow_prev > 0 else 0

    crossed_up   = gap > 0 and gap_prev <= 0
    crossed_down = gap < 0 and gap_prev >= 0

    if gap > 0:
        direction = 'BULL'
        strength  = min(40, int(abs(gap) * 15) + (15 if crossed_up else 0))
        desc = f"ZLEMA_BULL({strength}){'+CROSS' if crossed_up else ''} gap:{gap:.3f}%"
    elif gap < 0:
        direction = 'BEAR'
        strength  = min(40, int(abs(gap) * 15) + (15 if crossed_down else 0))
        desc = f"ZLEMA_BEAR({strength}){'+CROSS' if crossed_down else ''} gap:{gap:.3f}%"
    else:
        direction, strength, desc = 'NEUTRAL', 0, "ZLEMA_FLAT"

    return direction, strength, desc

# ============================================================================
# MÓDULO 2 — TREND REVERSAL PROBABILITY (sin cambios)
# ============================================================================

def trend_reversal_probability(closes, highs, lows, period=50):
    if len(closes) < period:
        return 50.0

    ma = sma(closes, period)
    price = closes[-1]

    recent_range = max(highs[-period:]) - min(lows[-period:])
    if recent_range <= 0: return 50.0
    dist_from_ma = abs(price - ma) / recent_range * 100

    mom_short = (closes[-1] - closes[-5])  / closes[-5]  * 100 if len(closes) >= 5  else 0
    mom_long  = (closes[-1] - closes[-period]) / closes[-period] * 100 if len(closes) >= period else 0

    momentum_ratio = abs(mom_short) / (abs(mom_long) + 0.001)
    if abs(mom_long) > 0 and abs(mom_short) < abs(mom_long) * 0.3:
        momentum_fading = 30
    else:
        momentum_fading = 0

    rsi_val = rsi(closes, 14)
    if rsi_val > 75:   rsi_exhaustion = 25
    elif rsi_val > 65: rsi_exhaustion = 10
    elif rsi_val < 25: rsi_exhaustion = 25
    elif rsi_val < 35: rsi_exhaustion = 10
    else:              rsi_exhaustion = 0

    trp = min(100, dist_from_ma * 0.5 + momentum_fading + rsi_exhaustion)
    return round(trp, 1)

def trp_signal(trp_value, direction):
    if direction == 'LONG':
        if trp_value < 20:   return 20, f"TRP_SANO({trp_value:.0f}%)"
        elif trp_value < 40: return 12, f"TRP_OK({trp_value:.0f}%)"
        elif trp_value < 60: return 0,  f"TRP_NEUTRO({trp_value:.0f}%)"
        elif trp_value < 84: return -10, f"TRP_CANSADO({trp_value:.0f}%)"
        else:                return -20, f"TRP_AGOTADO({trp_value:.0f}%) ⚠️"
    else:
        if trp_value > 84:   return 20, f"TRP_REVER({trp_value:.0f}%) ✅"
        elif trp_value > 65: return 12, f"TRP_ALTO({trp_value:.0f}%)"
        elif trp_value > 50: return 5,  f"TRP_MED({trp_value:.0f}%)"
        else:                return -10, f"TRP_BAJO({trp_value:.0f}%)"

# ============================================================================
# MÓDULO 3 — WYCKOFF PHASE DETECTION (sin cambios)
# ============================================================================

class WyckoffAnalyzer:

    def detect_climax(self, closes, highs, lows, volumes, direction='sell'):
        if len(closes) < 10: return False, 0, ""
        candle_range = highs[-1] - lows[-1]
        avg_range    = sum(highs[i] - lows[i] for i in range(-10, -1)) / 9
        vol_now = volumes[-1]
        avg_vol = sum(volumes[-10:-1]) / 9
        vol_ratio = vol_now / avg_vol if avg_vol > 0 else 1.0

        if direction == 'sell':
            is_bearish = closes[-1] < closes[-2]
            trend_down = closes[-5] > closes[-1]
            if is_bearish and candle_range > avg_range * 1.5 and vol_ratio > 2.0 and trend_down:
                score = min(35, int(vol_ratio * 8 + candle_range / avg_range * 5))
                return True, score, f"SC_Climax({score}) vol:{vol_ratio:.1f}x"
        else:
            is_bullish = closes[-1] > closes[-2]
            trend_up   = closes[-5] < closes[-1]
            if is_bullish and candle_range > avg_range * 1.5 and vol_ratio > 2.0 and trend_up:
                score = min(35, int(vol_ratio * 8 + candle_range / avg_range * 5))
                return True, score, f"BC_Climax({score}) vol:{vol_ratio:.1f}x"

        return False, 0, ""

    def detect_spring(self, closes, lows, volumes):
        if len(closes) < 20: return False, 0, ""
        support = min(lows[-20:-3])
        recent_low = min(lows[-3:])
        current    = closes[-1]
        broke_support  = recent_low < support * 0.998
        recovered      = current > support * 1.001
        vol_on_spring  = volumes[-2] > sum(volumes[-6:-2]) / 4 * 1.3

        if broke_support and recovered:
            strength = 40 if vol_on_spring else 28
            return True, strength, f"Spring({'vol' if vol_on_spring else 'noVol'})({strength})"
        return False, 0, ""

    def detect_upthrust(self, closes, highs, volumes):
        if len(closes) < 20: return False, 0, ""
        resistance = max(highs[-20:-3])
        recent_high = max(highs[-3:])
        current     = closes[-1]
        broke_resistance = recent_high > resistance * 1.002
        rejected         = current < resistance * 0.999
        vol_on_ut        = volumes[-2] > sum(volumes[-6:-2]) / 4 * 1.3

        if broke_resistance and rejected:
            strength = 40 if vol_on_ut else 28
            return True, strength, f"Upthrust({'vol' if vol_on_ut else 'noVol'})({strength})"
        return False, 0, ""

    def detect_sos(self, closes, highs, volumes):
        if len(closes) < 15: return False, 0, ""
        resistance = max(highs[-15:-3])
        current    = closes[-1]
        broke = current > resistance * 1.005
        vol   = volumes[-1] > sum(volumes[-5:-1]) / 4 * 1.5

        if broke and vol:
            pct = (current - resistance) / resistance * 100
            score = min(38, int(pct * 20 + 20))
            return True, score, f"SOS({score}) +{pct:.2f}%"
        return False, 0, ""

    def detect_sow(self, closes, lows, volumes):
        if len(closes) < 15: return False, 0, ""
        support = min(lows[-15:-3])
        current = closes[-1]
        broke = current < support * 0.995
        vol   = volumes[-1] > sum(volumes[-5:-1]) / 4 * 1.5

        if broke and vol:
            pct = (support - current) / support * 100
            score = min(38, int(pct * 20 + 20))
            return True, score, f"SOW({score}) -{pct:.2f}%"
        return False, 0, ""

    def detect_lps(self, closes, lows, volumes):
        if len(closes) < 20: return False, 0, ""
        support  = min(lows[-20:-5])
        recent_low = min(lows[-5:])
        current    = closes[-1]
        above_support = recent_low > support * 0.997
        recovering    = current > recent_low * 1.003
        low_vol       = volumes[-1] < sum(volumes[-6:-1]) / 5 * 0.8

        if above_support and recovering and low_vol:
            return True, 32, f"LPS(32) sop:${support:.6f}"
        return False, 0, ""

    def detect_lpsy(self, closes, highs, volumes):
        if len(closes) < 20: return False, 0, ""
        resistance  = max(highs[-20:-5])
        recent_high = max(highs[-5:])
        current     = closes[-1]
        below_resist = recent_high < resistance * 1.003
        rejecting    = current < recent_high * 0.997
        low_vol      = volumes[-1] < sum(volumes[-6:-1]) / 5 * 0.8

        if below_resist and rejecting and low_vol:
            return True, 32, f"LPSY(32) res:${resistance:.6f}"
        return False, 0, ""

    def detect_accumulation_phase(self, closes, highs, lows, volumes):
        if len(closes) < 30: return 'UNKNOWN', 0, ""
        range_high = max(highs[-30:])
        range_low  = min(lows[-30:])
        range_pct  = (range_high - range_low) / range_low * 100

        if not (2.0 <= range_pct <= 18.0): return 'OTHER', 0, ""

        current = closes[-1]
        pos_in_range = (current - range_low) / (range_high - range_low)
        vol_early = sum(volumes[-30:-15]) / 15
        vol_late  = sum(volumes[-15:])    / 15
        vol_decreasing = vol_late < vol_early * 0.85
        price_low = pos_in_range < 0.35

        if vol_decreasing and price_low:
            score = 25 + int((1 - pos_in_range) * 15)
            return 'ACCUMULATION_C_D', score, f"WYK_ACCUM({score}) rng:{range_pct:.1f}% pos:{pos_in_range:.2f}"
        elif price_low:
            return 'ACCUMULATION_B', 15, f"WYK_ACCUM_B(15) rng:{range_pct:.1f}%"
        elif pos_in_range > 0.65 and not vol_decreasing:
            return 'MARKUP', 10, f"WYK_MARKUP(10) pos:{pos_in_range:.2f}"

        return 'RANGING', 5, f"WYK_RANGING(5)"

    def detect_distribution_phase(self, closes, highs, lows, volumes):
        if len(closes) < 30: return 'UNKNOWN', 0, ""
        range_high = max(highs[-30:])
        range_low  = min(lows[-30:])
        range_pct  = (range_high - range_low) / range_low * 100

        if not (2.0 <= range_pct <= 18.0): return 'OTHER', 0, ""

        current = closes[-1]
        pos_in_range = (current - range_low) / (range_high - range_low)
        vol_early = sum(volumes[-30:-15]) / 15
        vol_late  = sum(volumes[-15:])    / 15
        vol_decreasing = vol_late < vol_early * 0.85
        price_high = pos_in_range > 0.65

        if vol_decreasing and price_high:
            score = 25 + int(pos_in_range * 15)
            return 'DISTRIBUTION_C_D', score, f"WYK_DIST({score}) rng:{range_pct:.1f}% pos:{pos_in_range:.2f}"
        elif price_high:
            return 'DISTRIBUTION_B', 15, f"WYK_DIST_B(15) rng:{range_pct:.1f}%"
        elif pos_in_range < 0.35 and not vol_decreasing:
            return 'MARKDOWN', 10, f"WYK_MARKDOWN(10)"

        return 'RANGING', 0, ""

# ============================================================================
# MÓDULO 4 — SMART MONEY CONCEPTS (sin cambios)
# ============================================================================

class SMCAnalyzer:

    def detect_order_block(self, closes, opens, highs, lows, volumes, direction='bull'):
        if len(closes) < 10: return False, 0, 0, 0, ""
        impulse_threshold = 1.5

        if direction == 'bull':
            for i in range(-2, -8, -1):
                if abs(i) >= len(closes): break
                if opens[i] > closes[i]:
                    if len(closes) > abs(i):
                        impulse = (closes[-1] - closes[i]) / closes[i] * 100
                        if impulse > impulse_threshold:
                            ob_low  = lows[i]
                            ob_high = highs[i]
                            price   = closes[-1]
                            if ob_low <= price <= ob_high * 1.005:
                                vol_ratio = volumes[i] / (sum(volumes[i-3:i]) / 3) if i > -len(volumes)+3 else 1
                                score = min(35, 20 + int(vol_ratio * 5))
                                return True, score, ob_low, ob_high, f"BullOB({score}) ${ob_low:.6f}-${ob_high:.6f}"
        else:
            for i in range(-2, -8, -1):
                if abs(i) >= len(closes): break
                if closes[i] > opens[i]:
                    if len(closes) > abs(i):
                        impulse = (closes[i] - closes[-1]) / closes[i] * 100
                        if impulse > impulse_threshold:
                            ob_low  = lows[i]
                            ob_high = highs[i]
                            price   = closes[-1]
                            if ob_low * 0.995 <= price <= ob_high:
                                vol_ratio = volumes[i] / (sum(volumes[i-3:i]) / 3) if i > -len(volumes)+3 else 1
                                score = min(35, 20 + int(vol_ratio * 5))
                                return True, score, ob_low, ob_high, f"BearOB({score}) ${ob_low:.6f}-${ob_high:.6f}"

        return False, 0, 0, 0, ""

    def detect_fvg(self, closes, highs, lows):
        if len(closes) < 5: return False, 0, 0, 0, ""

        for i in range(-1, -6, -1):
            idx = len(closes) + i
            if idx < 2: break
            price = closes[-1]

            if highs[idx-2] < lows[idx]:
                fvg_low  = highs[idx-2]
                fvg_high = lows[idx]
                fvg_size = (fvg_high - fvg_low) / fvg_low * 100
                if fvg_size > 0.1 and fvg_low <= price <= fvg_high:
                    score = min(30, int(fvg_size * 15 + 15))
                    return True, score, fvg_low, fvg_high, f"BullFVG({score}) {fvg_size:.2f}%"

            if lows[idx-2] > highs[idx]:
                fvg_low  = highs[idx]
                fvg_high = lows[idx-2]
                fvg_size = (fvg_high - fvg_low) / fvg_low * 100
                if fvg_size > 0.1 and fvg_low <= price <= fvg_high:
                    score = min(30, int(fvg_size * 15 + 15))
                    return True, score, fvg_low, fvg_high, f"BearFVG({score}) {fvg_size:.2f}%"

        return False, 0, 0, 0, ""

    def detect_bos_choch(self, closes, highs, lows):
        if len(closes) < 20: return 'NONE', 0, ""

        price = closes[-1]
        last_high = max(highs[-20:-2])
        last_low  = min(lows[-20:-2])

        if price > last_high * 1.003:
            pct = (price - last_high) / last_high * 100
            score = min(30, int(pct * 15 + 15))
            return 'BOS_BULL', score, f"BOS_BULL({score}) +{pct:.2f}%"

        if price < last_low * 0.997:
            pct = (last_low - price) / last_low * 100
            score = min(30, int(pct * 15 + 15))
            return 'BOS_BEAR', score, f"BOS_BEAR({score}) -{pct:.2f}%"

        if len(highs) >= 10 and highs[-1] > highs[-5] and closes[-1] > closes[-5]:
            return 'CHOCH_BULL', 12, "CHoCH_BULL(12)"

        if len(lows) >= 10 and lows[-1] < lows[-5] and closes[-1] < closes[-5]:
            return 'CHOCH_BEAR', 12, "CHoCH_BEAR(12)"

        return 'NONE', 0, ""

    def detect_liquidity_sweep(self, closes, highs, lows, volumes):
        if len(closes) < 15: return 'NONE', 0, ""

        recent_highs = highs[-15:-2]
        recent_lows  = lows[-15:-2]
        max_high = max(recent_highs)
        min_low  = min(recent_lows)
        price    = closes[-1]

        if min(lows[-3:]) < min_low * 0.998 and price > min_low * 1.001:
            vol_ok = volumes[-1] > sum(volumes[-5:-1]) / 4
            score  = 35 if vol_ok else 22
            return 'SWEEP_LOW', score, f"LiqSweepLow({score})"

        if max(highs[-3:]) > max_high * 1.002 and price < max_high * 0.999:
            vol_ok = volumes[-1] > sum(volumes[-5:-1]) / 4
            score  = 35 if vol_ok else 22
            return 'SWEEP_HIGH', score, f"LiqSweepHigh({score})"

        return 'NONE', 0, ""

    def premium_discount(self, closes, highs, lows, period=50):
        if len(closes) < period: period = len(closes)
        range_high = max(highs[-period:])
        range_low  = min(lows[-period:])
        price      = closes[-1]

        if range_high == range_low: return 50.0
        pos = (price - range_low) / (range_high - range_low) * 100
        return round(pos, 1)

# ============================================================================
# ANÁLISIS COMPLETO CON FILTROS MEJORADOS
# ============================================================================

wyckoff = WyckoffAnalyzer()
smc     = SMCAnalyzer()

def full_analysis(symbol, direction='LONG'):
    d15 = klines(symbol, '15m', 100)
    d1h = klines(symbol, '1h',  60)
    d4h = klines(symbol, '4h',  40)

    if not d15: return 0, []

    c15, h15, l15, v15, o15 = d15['close'], d15['high'], d15['low'], d15['volume'], d15['open']
    price = c15[-1]

    c1h = d1h['close'] if d1h else c15
    h1h = d1h['high']  if d1h else h15
    l1h = d1h['low']   if d1h else l15
    v1h = d1h['volume'] if d1h else v15

    c4h = d4h['close'] if d4h else c1h
    h4h = d4h['high']  if d4h else h1h
    l4h = d4h['low']   if d4h else l1h
    v4h = d4h['volume'] if d4h else v1h

    score, reasons = 0, []

    # ═══════════════════════════════════════════════════════════════════════
    # FILTRO NUEVO #1: Detectar señales contradictorias
    # ═══════════════════════════════════════════════════════════════════════
    contradictions = 0

    # ── SCALPING: ZLEMA + TRP ─────────────────────────────────────────────
    zlema_dir, zlema_score, zlema_desc = zlema_signal(c15, fast=8, slow=21)
    trp_val = trend_reversal_probability(c15, h15, l15, period=50)
    trp_score, trp_desc = trp_signal(trp_val, direction)

    if direction == 'LONG':
        if zlema_dir == 'BULL':
            score += zlema_score; reasons.append(zlema_desc)
        elif zlema_dir == 'BEAR':
            score -= zlema_score * 0.5
            reasons.append(f"ZLEMA_CONTRA(-{int(zlema_score*0.5)})")
            contradictions += 1  # SEÑAL CONTRADICTORIA
    else:
        if zlema_dir == 'BEAR':
            score += zlema_score; reasons.append(zlema_desc)
        elif zlema_dir == 'BULL':
            score -= zlema_score * 0.5
            reasons.append(f"ZLEMA_CONTRA(-{int(zlema_score*0.5)})")
            contradictions += 1  # SEÑAL CONTRADICTORIA

    score += trp_score; reasons.append(trp_desc)

    # Penalización por TRP contradictorio
    if direction == 'LONG' and trp_score < -5:
        contradictions += 1
    elif direction == 'SHORT' and trp_score < -5:
        contradictions += 1

    # ── WYCKOFF (1h) ──────────────────────────────────────────────────────
    if direction == 'LONG':
        wyk_phase, wyk_score, wyk_desc = wyckoff.detect_accumulation_phase(c1h, h1h, l1h, v1h)
        if wyk_score > 0: score += wyk_score; reasons.append(wyk_desc)

        ok, s, d = wyckoff.detect_spring(c1h, l1h, v1h)
        if ok: score += s; reasons.append(d)

        ok, s, d = wyckoff.detect_climax(c1h, h1h, l1h, v1h, 'sell')
        if ok: score += s; reasons.append(d)

        ok, s, d = wyckoff.detect_sos(c1h, h1h, v1h)
        if ok: score += s; reasons.append(d)

        ok, s, d = wyckoff.detect_lps(c1h, l1h, v1h)
        if ok: score += s; reasons.append(d)

        wyk4_phase, wyk4_score, wyk4_desc = wyckoff.detect_accumulation_phase(c4h, h4h, l4h, v4h)
        if wyk4_score > 0:
            bonus = int(wyk4_score * 0.6)
            score += bonus; reasons.append(f"4H_{wyk4_desc}")

    else:
        wyk_phase, wyk_score, wyk_desc = wyckoff.detect_distribution_phase(c1h, h1h, l1h, v1h)
        if wyk_score > 0: score += wyk_score; reasons.append(wyk_desc)

        ok, s, d = wyckoff.detect_upthrust(c1h, h1h, v1h)
        if ok: score += s; reasons.append(d)

        ok, s, d = wyckoff.detect_climax(c1h, h1h, l1h, v1h, 'buy')
        if ok: score += s; reasons.append(d)

        ok, s, d = wyckoff.detect_sow(c1h, l1h, v1h)
        if ok: score += s; reasons.append(d)

        ok, s, d = wyckoff.detect_lpsy(c1h, h1h, v1h)
        if ok: score += s; reasons.append(d)

        wyk4_phase, wyk4_score, wyk4_desc = wyckoff.detect_distribution_phase(c4h, h4h, l4h, v4h)
        if wyk4_score > 0:
            bonus = int(wyk4_score * 0.6)
            score += bonus; reasons.append(f"4H_{wyk4_desc}")

    # ── SMC ───────────────────────────────────────────────────────────────
    ob_ok, ob_score, ob_low, ob_high, ob_desc = smc.detect_order_block(
        c15, o15, h15, l15, v15,
        direction='bull' if direction == 'LONG' else 'bear'
    )
    if ob_ok: score += ob_score; reasons.append(ob_desc)

    fvg_ok, fvg_score, _, _, fvg_desc = smc.detect_fvg(c15, h15, l15)
    if fvg_ok: score += fvg_score; reasons.append(fvg_desc)

    bos_type, bos_score, bos_desc = smc.detect_bos_choch(c15, h15, l15)
    if direction == 'LONG' and 'BULL' in bos_type:
        score += bos_score; reasons.append(bos_desc)
    elif direction == 'SHORT' and 'BEAR' in bos_type:
        score += bos_score; reasons.append(bos_desc)
    elif bos_type != 'NONE':
        score -= int(bos_score * 0.4)
        contradictions += 1  # SEÑAL CONTRADICTORIA

    sweep_type, sweep_score, sweep_desc = smc.detect_liquidity_sweep(c15, h15, l15, v15)
    if direction == 'LONG' and sweep_type == 'SWEEP_LOW':
        score += sweep_score; reasons.append(sweep_desc)
    elif direction == 'SHORT' and sweep_type == 'SWEEP_HIGH':
        score += sweep_score; reasons.append(sweep_desc)

    pd_pos = smc.premium_discount(c15, h15, l15, period=50)
    if direction == 'LONG':
        if pd_pos < 30:   s = 18; reasons.append(f"Discount({pd_pos:.0f}%)(+18)")
        elif pd_pos < 45: s = 8;  reasons.append(f"Discount_med({pd_pos:.0f}%)(+8)")
        elif pd_pos > 70: s = -12; reasons.append(f"Premium({pd_pos:.0f}%)(-12)")
        else:             s = 0
        score += s
    else:
        if pd_pos > 70:   s = 18; reasons.append(f"Premium({pd_pos:.0f}%)(+18)")
        elif pd_pos > 55: s = 8;  reasons.append(f"Premium_med({pd_pos:.0f}%)(+8)")
        elif pd_pos < 30: s = -12; reasons.append(f"Discount({pd_pos:.0f}%)(-12)")
        else:             s = 0
        score += s

    # ── Indicadores clásicos ──────────────────────────────────────────────
    rsi_val  = rsi(c15, 14)
    atr_val  = atr(h15, l15, c15, 14)
    atr_pct  = atr_val / price * 100 if price > 0 else 0
    vs       = vol_spike(v15)
    ema9_val  = ema(c15, 9)
    ema21_val = ema(c15, 21)

    if direction == 'LONG':
        if rsi_val < 30:   score += 15; reasons.append(f"RSI_oversold({rsi_val:.0f})")
        elif rsi_val < 45: score += 8;  reasons.append(f"RSI_low({rsi_val:.0f})")
        elif rsi_val > 75: score -= 12; reasons.append(f"RSI_overbought({rsi_val:.0f})")
        if ema9_val > ema21_val: score += 8; reasons.append("EMA_bull(8)")
    else:
        if rsi_val > 70:   score += 15; reasons.append(f"RSI_overbought({rsi_val:.0f})")
        elif rsi_val > 55: score += 8;  reasons.append(f"RSI_high({rsi_val:.0f})")
        elif rsi_val < 25: score -= 12; reasons.append(f"RSI_oversold({rsi_val:.0f})")
        if ema9_val < ema21_val: score += 8; reasons.append("EMA_bear(8)")

    if vs >= 2.0: score += 10; reasons.append(f"Vol{vs:.1f}x(10)")
    elif vs >= 1.5: score += 5; reasons.append(f"Vol{vs:.1f}x(5)")

    if atr_pct < 0.15: return 0, []

    # ═══════════════════════════════════════════════════════════════════════
    # FILTRO CRÍTICO: Rechazar trades con 2+ señales contradictorias
    # ═══════════════════════════════════════════════════════════════════════
    if contradictions >= 2:
        log.debug(f"  [FILTRO] {symbol} rechazado: {contradictions} contradicciones")
        return 0, []

    return round(score, 1), reasons

# ============================================================================
# BOT PRINCIPAL CON FILTROS ADICIONALES
# ============================================================================

class WyckoffBot:

    def __init__(self):
        log.info("=" * 70)
        log.info("  WYCKOFF SMC BOT v1.2 OPTIMIZADO")
        log.info("  TP:3.5% SL:1.2% RR:2.9:1 | LEV:2x | MIN_SCORE:85")
        log.info("=" * 70)
        log.info(f"  Capital: ${POSITION_SIZE} | LEV:{LEVERAGE}x | MAX:{MAX_TRADES}")
        log.info(f"  TP:{TP_PCT}% SL:{SL_PCT}% | Short:{'ON' if ALLOW_SHORT else 'OFF'}")
        log.info(f"  Min score: {MIN_SCORE} | Circuit: {CIRCUIT_PCT}%")
        log.info("=" * 70)

        self.symbols     = []
        self.open_trades = {}
        self._contracts  = {}
        self._cooldowns  = {}
        self._last_report= datetime.now()
        self._btc_trend  = 0.0
        self._daily_pnl  = 0.0
        self._daily_reset= datetime.utcnow().date()
        self._circuit    = False
        self._circuit_until = None
        self._real_count_cache = 0
        self._real_count_ts    = 0
        self.stats = {'exec':0,'closed':0,'wins':0,'losses':0,'pnl':0.0}

        self._verify()
        self._load_contracts()
        self._get_symbols()
        self._recover()
        self._cleanup_excess()

        self._tg(
            f"<b>🔵 Wyckoff SMC Bot v1.2 OPTIMIZADO</b>\n"
            f"TP:{TP_PCT}% SL:{SL_PCT}% RR:{TP_PCT/SL_PCT:.2f}:1\n"
            f"LEV:{LEVERAGE}x | MinScore:{MIN_SCORE} | Circuit:{CIRCUIT_PCT}%\n"
            f"Posiciones recuperadas: {len(self.open_trades)}/{MAX_TRADES}"
        )

    # ---------------------------------------------------------------- setup (sin cambios)

    def _verify(self):
        global AUTO_TRADING
        if not BINGX_API_KEY or not BINGX_API_SECRET:
            AUTO_TRADING = False; return
        try:
            d = bingx('GET', '/openApi/swap/v2/user/balance', {}).json()
            if d.get('code') == 0:
                eq = d.get('data',{}).get('equity', '?')
                log.info(f"BingX OK | Balance: ${eq} USDT")
            else:
                log.error(f"BingX error: {d.get('msg')}"); AUTO_TRADING = False
        except Exception as e:
            log.error(f"API error: {e}"); AUTO_TRADING = False

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
            log.warning(f"Contratos error: {e}")

    def _get_symbols(self):
        NO = ['DOW','SP500','GOLD','SILVER','XAU','OIL','BRENT','EUR','GBP','JPY',
              'TSLA','AAPL','MSFT','NVDA','COIN','MSTR','WHEAT','CORN']
        try:
            d = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker", timeout=15).json()
            if d.get('code') == 0:
                items = []
                for t in d.get('data', []):
                    sym = t.get('symbol','')
                    if not sym.endswith('-USDT'): continue
                    base = sym.replace('-USDT','').upper()
                    if any(kw in base for kw in NO): continue
                    try:
                        price = float(t.get('lastPrice',0))
                        vol   = float(t.get('volume',0)) * price
                        if vol < MIN_VOLUME or price < 0.000001: continue
                        items.append({'symbol':sym,'vol':vol})
                    except: continue
                items.sort(key=lambda x: x['vol'], reverse=True)
                self.symbols = [x['symbol'] for x in items[:MAX_SYMBOLS]]
                log.info(f"Símbolos: {len(self.symbols)}")
        except Exception as e:
            log.warning(f"Símbolos error: {e}")
            self.symbols = ['BTC-USDT','ETH-USDT','SOL-USDT','BNB-USDT','XRP-USDT']

    def _recover(self):
        if not AUTO_TRADING: return
        try:
            d = bingx('GET', '/openApi/swap/v2/user/positions', {}).json()
            if d.get('code') != 0: return
            rec = 0
            for p in (d.get('data') or []):
                try: amt = float(p.get('positionAmt', 0) or 0)
                except: continue
                if amt == 0: continue
                sym = p.get('symbol', '')
                if not sym or sym in self.open_trades: continue
                direction = 'LONG' if amt > 0 else 'SHORT'
                try: entry = float(p.get('avgPrice') or p.get('entryPrice') or 0)
                except: entry = 0
                if entry <= 0:
                    tk = ticker(sym)
                    entry = tk['price'] if tk else 0
                if entry <= 0: continue
                tp = entry * (1 + TP_PCT/100) if direction == 'LONG' else entry * (1 - TP_PCT/100)
                sl = entry * (1 - SL_PCT/100) if direction == 'LONG' else entry * (1 + SL_PCT/100)
                self.open_trades[sym] = {
                    'direction': direction, 'entry': entry,
                    'qty_c': abs(amt), 'usdt_qty': POSITION_SIZE,
                    'tp': tp, 'sl': sl, 'tp_pct': TP_PCT, 'sl_pct': SL_PCT,
                    'highest': entry, 'lowest': entry,
                    'opened_at': datetime.now(), 'score': 0,
                }
                rec += 1
                log.info(f"  Recuperado {direction} {sym}: entry=${entry:.6f}")
            log.info(f"  Recovery: {rec} posiciones")
        except Exception as e:
            log.error(f"Recovery error: {e}")

    def _cleanup_excess(self):
        if not AUTO_TRADING: return
        try:
            d = bingx('GET', '/openApi/swap/v2/user/positions', {}).json()
            if d.get('code') != 0: return
            positions = []
            for p in (d.get('data') or []):
                try: amt = float(p.get('positionAmt', 0) or 0)
                except: continue
                if amt == 0: continue
                positions.append({
                    'symbol': p.get('symbol',''),
                    'amt': amt,
                    'direction': 'LONG' if amt > 0 else 'SHORT',
                    'pnl': float(p.get('unrealizedProfit', 0) or 0)
                })
            excess = len(positions) - MAX_TRADES
            if excess <= 0: return
            log.warning(f"  [CLEANUP] {len(positions)} pos > {MAX_TRADES} — cerrando {excess}")
            positions.sort(key=lambda x: x['pnl'])
            for pos in positions[:excess]:
                sym = pos['symbol']
                qty = abs(pos['amt'])
                side = 'SELL' if pos['direction'] == 'LONG' else 'BUY'
                ps   = pos['direction']
                d2 = bingx('POST', '/openApi/swap/v2/trade/order', {
                    'symbol': sym, 'side': side, 'positionSide': ps,
                    'type': 'MARKET', 'quantity': str(qty), 'reduceOnly': 'true',
                }).json()
                if d2.get('code') == 0:
                    log.info(f"  [CLEANUP] ✅ {sym} {ps} cerrado")
                    if sym in self.open_trades: del self.open_trades[sym]
                time.sleep(0.5)
        except Exception as e:
            log.error(f"Cleanup error: {e}")

    # ---------------------------------------------------------------- sizing (sin cambios significativos)

    def _qty(self, symbol, price, usdt_amount=None):
        if usdt_amount is None: usdt_amount = POSITION_SIZE
        notional = max(usdt_amount * LEVERAGE, FORCE_MIN_USDT * LEVERAGE)
        info  = self._contracts.get(symbol, {'step': 1.0, 'prec': 2, 'ctval': 1.0})
        step  = max(info.get('step', 1.0), 0.0001)
        prec  = info.get('prec', 2)
        ctval = max(info.get('ctval', 1.0), 1e-9)
        ppc   = price * ctval
        if ppc <= 0: return None, 0
        qty = round(math.ceil(notional / ppc / step) * step, prec)
        val = qty * ppc
        i = 0
        while val < FORCE_MIN_USDT and i < 200:
            qty += step; qty = round(qty, prec); val = qty * ppc; i += 1
        if val < FORCE_MIN_USDT: return None, 0
        log.info(f"  [QTY] {symbol}: {qty}cts ${val:.2f} notional")
        return qty, round(val, 4)

    # ---------------------------------------------------------------- cooldown

    def _cd_ok(self, symbol, direction):
        key = f"{symbol}_{direction}"
        ts = self._cooldowns.get(key)
        if not ts: return True
        if time.time() >= ts:
            del self._cooldowns[key]; return True
        return False

    def _set_cd(self, symbol, direction, reason='TP'):
        key  = f"{symbol}_{direction}"
        mins = COOLDOWN_TP if reason == 'TP' else COOLDOWN_SL
        self._cooldowns[key] = time.time() + mins * 60

    # ---------------------------------------------------------------- circuit breaker (corregido)

    def _check_circuit(self):
        today = datetime.utcnow().date()
        if today != self._daily_reset:
            self._daily_pnl  = 0.0
            self._daily_reset= today
            self._circuit    = False
            self._circuit_until = None
            log.info("  [CIRCUIT] Reset diario — bot reanudado")

        if self._circuit:
            if self._circuit_until and datetime.utcnow() > self._circuit_until:
                self._circuit = False
                self._circuit_until = None
                self._daily_pnl = 0.0  # RESET CRÍTICO
                log.info("  [CIRCUIT] 2h cumplidas — bot reanudado (PnL reseteado)")
                self._tg("<b>🟢 Circuit Breaker desactivado [v1.2]</b> — reanudando")
            else:
                remaining = ""
                if self._circuit_until:
                    mins = int((self._circuit_until - datetime.utcnow()).total_seconds() / 60)
                    remaining = f" ({mins}min restantes)"
                log.warning(f"  [CIRCUIT] Bot pausado{remaining}")
                return True

        loss_pct = abs(min(self._daily_pnl, 0)) / max(POSITION_SIZE, 1) * 100
        if loss_pct >= CIRCUIT_PCT:
            self._circuit       = True
            self._circuit_until = datetime.utcnow() + timedelta(hours=2)
            log.warning(f"  [CIRCUIT] 🔴 Pérdida {loss_pct:.1f}% — pausado 2h")
            self._tg(
                f"<b>🔴 Circuit Breaker [v1.2]</b>\n"
                f"Pérdida: ${self._daily_pnl:.3f} ({loss_pct:.1f}%)\n"
                f"Reanuda: {self._circuit_until.strftime('%H:%M')} UTC"
            )
        return self._circuit

    # ---------------------------------------------------------------- órdenes (sin cambios)

    def _set_leverage(self, symbol):
        try:
            for side in ['LONG', 'SHORT']:
                bingx('POST', '/openApi/swap/v2/trade/leverage',
                      {'symbol': symbol, 'side': side, 'leverage': str(LEVERAGE)})
        except: pass

    def _place_entry(self, symbol, direction, price, qty_c):
        side = 'BUY' if direction == 'LONG' else 'SELL'
        ps   = direction

        if USE_LIMIT:
            offset = 1 - 0.0005 if direction == 'LONG' else 1 + 0.0005
            lp = round(price * offset, 8)
            d = bingx('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol,'side':side,'positionSide':ps,
                'type':'LIMIT','price':str(lp),'quantity':str(qty_c),'timeInForce':'GTC',
            }).json()
            if d.get('code') == 0:
                log.info(f"  ✅ LIMIT {direction} {qty_c}cts @ ${lp:.6f}")
                return d.get('data',{}).get('orderId','OK')
            log.warning(f"  LIMIT falló [{d.get('msg')}] — intentando MARKET")

        d = bingx('POST', '/openApi/swap/v2/trade/order', {
            'symbol':symbol,'side':side,'positionSide':ps,
            'type':'MARKET','quantity':str(qty_c),
        }).json()
        if d.get('code') == 0:
            log.info(f"  ✅ MARKET {direction} {qty_c}cts")
            return d.get('data',{}).get('orderId','OK')
        log.error(f"  ❌ Entrada fallida [{d.get('code')}]: {d.get('msg')}")
        return None

    def _place_tp_sl(self, symbol, direction, qty_c, tp_price, sl_price):
        side = 'SELL' if direction == 'LONG' else 'BUY'
        ps   = direction

        d = bingx('POST', '/openApi/swap/v2/trade/order', {
            'symbol':symbol,'side':side,'positionSide':ps,
            'type':'TAKE_PROFIT_MARKET','quantity':str(qty_c),
            'stopPrice':str(round(tp_price, 8)),
        }).json()
        tp_ok = d.get('code') == 0
        if tp_ok: log.info(f"  TP ✅ @ ${tp_price:.6f}")
        else: log.warning(f"  TP ❌: {d.get('msg')}")

        time.sleep(0.3)

        sl_limit = round(sl_price * (1 + SL_OFFSET) if direction == 'SHORT' else sl_price * (1 - SL_OFFSET), 8)
        d2 = bingx('POST', '/openApi/swap/v2/trade/order', {
            'symbol':symbol,'side':side,'positionSide':ps,
            'type':'STOP','quantity':str(qty_c),
            'price':str(sl_limit),'stopPrice':str(round(sl_price, 8)),'timeInForce':'GTC',
        }).json()
        sl_ok = d2.get('code') == 0
        if sl_ok: log.info(f"  SL ✅ @ ${sl_price:.6f}")
        else:
            d3 = bingx('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol,'side':side,'positionSide':ps,
                'type':'STOP_MARKET','quantity':str(qty_c),
                'stopPrice':str(round(sl_price, 8)),
            }).json()
            sl_ok = d3.get('code') == 0
            if sl_ok: log.info(f"  SL ✅ MARKET @ ${sl_price:.6f}")
            else: log.error(f"  SL ❌: {d3.get('msg')}")

        return tp_ok, sl_ok

    def _wait_position(self, symbol, direction, timeout=30):
        for i in range(timeout):
            try:
                d = bingx('GET', '/openApi/swap/v2/user/positions', {'symbol': symbol}).json()
                if d.get('code') == 0:
                    for p in (d.get('data') or []):
                        amt = float(p.get('positionAmt', 0) or 0)
                        if (direction == 'LONG' and amt > 0) or (direction == 'SHORT' and amt < 0):
                            entry = float(p.get('avgPrice') or p.get('entryPrice') or 0)
                            qty   = abs(amt)
                            if qty > 0:
                                log.info(f"  Confirmado {direction} {symbol}: qty={qty:.4f} entry=${entry:.6f}")
                                return qty, entry
            except: pass
            time.sleep(1)
        return None, None

    def _close_position(self, symbol, direction, qty_c):
        side = 'SELL' if direction == 'LONG' else 'BUY'
        ps   = direction
        tk   = ticker(symbol)
        price = tk['price'] if tk else 0

        if price > 0:
            offset = 1.0005 if direction == 'LONG' else 0.9995
            lp = round(price * offset, 8)
            d = bingx('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol,'side':side,'positionSide':ps,
                'type':'LIMIT','quantity':str(qty_c),
                'price':str(lp),'timeInForce':'IOC','reduceOnly':'true',
            }).json()
            if d.get('code') == 0:
                log.info(f"  Cierre LIMIT IOC {direction} {symbol}"); return True

        d = bingx('POST', '/openApi/swap/v2/trade/order', {
            'symbol':symbol,'side':side,'positionSide':ps,
            'type':'MARKET','quantity':str(qty_c),'reduceOnly':'true',
        }).json()
        ok = d.get('code') == 0
        if ok: log.info(f"  Cierre MARKET {direction} {symbol}")
        else:  log.error(f"  Cierre fallido: {d.get('msg')}")
        return ok

    def _cancel_orders(self, symbol):
        try:
            d = bingx('GET', '/openApi/swap/v2/trade/openOrders', {'symbol': symbol}).json()
            if d.get('code') == 0:
                for o in (d.get('data', {}).get('orders') or []):
                    oid = o.get('orderId', '')
                    if oid:
                        bingx('DELETE', '/openApi/swap/v2/trade/order',
                              {'symbol': symbol, 'orderId': str(oid)})
        except: pass

    def _count_real(self, force=False):
        now = time.time()
        if not force and (now - self._real_count_ts) < 30:
            return self._real_count_cache
        try:
            d = bingx('GET', '/openApi/swap/v2/user/positions', {}).json()
            if d.get('code') == 0:
                n = sum(1 for p in (d.get('data') or [])
                        if float(p.get('positionAmt', 0) or 0) != 0)
                log.info(f"  [REAL] Posiciones en BingX: {n}/{MAX_TRADES}")
                self._real_count_cache = n
                self._real_count_ts    = now
                return n
        except: pass
        return len(self.open_trades)

    # ═══════════════════════════════════════════════════════════════════════
    # FILTRO ADICIONAL #2: Verificar tendencia BTC antes de abrir
    # ═══════════════════════════════════════════════════════════════════════

    def analyze_symbol(self, symbol):
        if symbol in self.open_trades: return None

        # ═══════════════════════════════════════════════════════════════════
        # FILTRO BTC: No operar si BTC cae >2% en 1h
        # ═══════════════════════════════════════════════════════════════════
        if symbol != 'BTC-USDT' and self._btc_trend < -2.0:
            log.debug(f"  [FILTRO BTC] {symbol} rechazado: BTC {self._btc_trend:+.2f}%")
            return None

        tk = ticker(symbol)
        if not tk or tk['price'] <= 0: return None
        price  = tk['price']
        change = tk['change']

        best = None
        for direction in (['LONG', 'SHORT'] if ALLOW_SHORT else ['LONG']):
            if not self._cd_ok(symbol, direction): continue

            score, reasons = full_analysis(symbol, direction)
            if score >= MIN_SCORE:
                d15 = klines(symbol, '15m', 30)
                if d15:
                    atr_val = atr(d15['high'], d15['low'], d15['close'], 14)
                    atr_pct = atr_val / price * 100 if price > 0 else SL_PCT
                else:
                    atr_pct = SL_PCT

                sl_dyn = max(SL_PCT, atr_pct * 0.8)
                sl_dyn = round(min(sl_dyn, SL_PCT * 2.0), 3)
                tp_dyn = max(TP_PCT, sl_dyn * 2.0, atr_pct * 2.5)  # RR mínimo 2:1
                tp_dyn = round(min(tp_dyn, TP_PCT * 3.0), 3)
                rr     = round(tp_dyn / sl_dyn, 2)

                if best is None or score > best['score']:
                    best = {
                        'symbol': symbol, 'direction': direction,
                        'price': price, 'change': change,
                        'score': score, 'reasons': ' | '.join(reasons),
                        'tp_pct': tp_dyn, 'sl_pct': sl_dyn, 'rr': rr,
                    }
        return best

    # ---------------------------------------------------------------- open/close

    _abriendo = False

    def open_trade(self, sig):
        if not AUTO_TRADING: return False
        symbol    = sig['symbol']
        direction = sig['direction']
        price     = sig['price']

        if symbol in self.open_trades: return False
        if WyckoffBot._abriendo: return False
        if self._count_real(force=True) >= MAX_TRADES: return False

        WyckoffBot._abriendo = True
        try:
            usdt_qty = max(POSITION_SIZE, FORCE_MIN_USDT)
            qty_c, qty_val = self._qty(symbol, price, usdt_qty)
            if not qty_c: return False

            tp_price = price * (1 + sig['tp_pct']/100) if direction == 'LONG' \
                  else price * (1 - sig['tp_pct']/100)
            sl_price = price * (1 - sig['sl_pct']/100) if direction == 'LONG' \
                  else price * (1 + sig['sl_pct']/100)

            emoji = "🟢" if direction == 'LONG' else "🔴"
            log.info(f"\n  ➤ {direction} {symbol}")
            log.info(f"  Score:{sig['score']:.0f} | TP:{sig['tp_pct']:.2f}% SL:{sig['sl_pct']:.2f}% RR:{sig['rr']:.2f}")
            log.info(f"  {sig['reasons']}")

            self._set_leverage(symbol)
            oid = self._place_entry(symbol, direction, price, qty_c)
            if not oid: return False

            qty_real, entry_real = self._wait_position(symbol, direction, timeout=30)
            if qty_real is None:
                self._cancel_orders(symbol)
                time.sleep(0.5)
                side = 'BUY' if direction == 'LONG' else 'SELL'
                bingx('POST', '/openApi/swap/v2/trade/order', {
                    'symbol':symbol,'side':side,'positionSide':direction,
                    'type':'MARKET','quantity':str(qty_c),
                }).json()
                qty_real, entry_real = self._wait_position(symbol, direction, timeout=15)
                if qty_real is None:
                    log.error(f"  CRÍTICO: No confirmada posición {symbol}")
                    for _ in range(2):
                        self._close_position(symbol, direction, qty_c)
                        time.sleep(1)
                    return False

            qty_f   = qty_real or qty_c
            entry_f = entry_real or price
            tp_f = entry_f * (1 + sig['tp_pct']/100) if direction == 'LONG' \
                else entry_f * (1 - sig['tp_pct']/100)
            sl_f = entry_f * (1 - sig['sl_pct']/100) if direction == 'LONG' \
                else entry_f * (1 + sig['sl_pct']/100)

            tp_ok, sl_ok = self._place_tp_sl(symbol, direction, qty_f, tp_f, sl_f)

            if not sl_ok:
                log.error(f"  CRÍTICO: SL fallido {symbol} — cerrando")
                self._close_position(symbol, direction, qty_f)
                return False

            self.open_trades[symbol] = {
                'direction': direction, 'entry': entry_f,
                'qty_c': qty_f, 'usdt_qty': usdt_qty,
                'tp': tp_f, 'sl': sl_f,
                'tp_pct': sig['tp_pct'], 'sl_pct': sig['sl_pct'],
                'highest': entry_f, 'lowest': entry_f,
                'opened_at': datetime.now(), 'score': sig['score'],
            }
            self._real_count_ts = 0
            self.stats['exec'] += 1

            self._tg(
                f"<b>{emoji} {direction} ABIERTO [v1.2]</b>\n"
                f"<b>{symbol}</b> | Score:{sig['score']:.0f}\n"
                f"Entrada: ${entry_f:.6f}\n"
                f"TP: ${tp_f:.6f} (+{sig['tp_pct']:.2f}%)\n"
                f"SL: ${sl_f:.6f} (-{sig['sl_pct']:.2f}%)\n"
                f"RR: {sig['rr']:.2f}:1 | ${usdt_qty} x{LEVERAGE}\n"
                f"{sig['reasons']}"
            )
            return True
        finally:
            WyckoffBot._abriendo = False

    def close_trade(self, symbol, cur_price, reason):
        if symbol not in self.open_trades: return False
        t = self.open_trades[symbol]
        direction = t['direction']
        self._close_position(symbol, direction, t['qty_c'])

        if direction == 'LONG':
            cambio = (cur_price - t['entry']) / t['entry']
        else:
            cambio = (t['entry'] - cur_price) / t['entry']

        pnl     = (t['usdt_qty'] * LEVERAGE * cambio) - (t['usdt_qty'] * LEVERAGE * COMISION)
        pnl_pct = (pnl / t['usdt_qty']) * 100

        self.stats['closed'] += 1; self.stats['pnl'] += pnl
        self._daily_pnl += pnl
        if pnl > 0: self.stats['wins'] += 1
        else:        self.stats['losses'] += 1

        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        mins  = int((datetime.now() - t['opened_at']).total_seconds() / 60)
        emoji = "✅" if pnl > 0 else "❌"
        cd    = 'TP' if 'PROFIT' in reason else 'SL'

        log.info(f"  {emoji} {reason} {direction} {symbol} PnL:${pnl:+.3f} {mins}min")
        self._tg(
            f"<b>{emoji} {direction} CERRADO — {reason} [v1.2]</b>\n"
            f"<b>{symbol}</b>\nPnL: ${pnl:+.3f} ({pnl_pct:+.1f}%)\n"
            f"Entry: ${t['entry']:.6f} → Exit: ${cur_price:.6f} | {mins}min\n"
            f"<b>Total: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}%</b>\nDía: ${self._daily_pnl:+.3f}"
        )
        self._set_cd(symbol, direction, cd)
        del self.open_trades[symbol]
        self._real_count_ts = 0
        return True

    # ---------------------------------------------------------------- monitor

    async def _sync_bingx(self):
        if not self.open_trades or not AUTO_TRADING: return
        try:
            d = bingx('GET', '/openApi/swap/v2/user/positions', {}).json()
            if d.get('code') != 0: return
            alive = set()
            for p in (d.get('data') or []):
                amt = float(p.get('positionAmt', 0) or 0)
                if amt != 0: alive.add(p.get('symbol',''))
            for sym in list(self.open_trades.keys()):
                if sym not in alive:
                    t   = self.open_trades[sym]
                    tk  = ticker(sym)
                    cur = tk['price'] if tk else t['entry']
                    direction = t['direction']
                    cambio = (cur - t['entry']) / t['entry'] if direction == 'LONG' \
                        else (t['entry'] - cur) / t['entry']
                    pnl = (t['usdt_qty'] * LEVERAGE * cambio) - (t['usdt_qty'] * LEVERAGE * COMISION)
                    self.stats['closed'] += 1; self.stats['pnl'] += pnl
                    self._daily_pnl += pnl
                    if pnl >= 0: self.stats['wins'] += 1
                    else:         self.stats['losses'] += 1
                    total = self.stats['wins'] + self.stats['losses']
                    wr    = self.stats['wins'] / total * 100 if total else 0
                    emoji = "✅" if pnl >= 0 else "❌"
                    self._tg(f"<b>{emoji} {direction} cerrado BingX [v1.2]</b>\n"
                             f"<b>{sym}</b> PnL:${pnl:+.3f} | WR:{wr:.1f}%")
                    self._set_cd(sym, direction, 'TP' if pnl >= 0 else 'SL')
                    del self.open_trades[sym]
        except Exception as e:
            log.debug(f"sync: {e}")

    async def monitor(self):
        await self._sync_bingx()
        for sym in list(self.open_trades.keys()):
            try:
                t  = self.open_trades[sym]
                tk = ticker(sym)
                if not tk: continue
                cur       = tk['price']
                direction = t['direction']

                if direction == 'LONG':
                    pnl_pct = (cur - t['entry']) / t['entry'] * 100
                    if TRAILING and cur > t['highest']:
                        t['highest'] = cur
                        if pnl_pct >= TRAILING_START:
                            new_sl = t['entry'] + (cur - t['entry']) * (TRAILING_LOCK / 100)
                            if new_sl > t['sl']:
                                t['sl'] = new_sl
                                log.info(f"  Trailing LONG {sym}: SL=${new_sl:.6f}")
                    pnl_lev = pnl_pct * LEVERAGE
                    if pnl_lev < -MAX_LOSS_PCT:
                        self._tg(f"<b>🚨 EMERGENCIA LONG {sym}</b> {pnl_lev:+.1f}%")
                        self.close_trade(sym, cur, "STOP LOSS EMERGENCIA"); continue
                    if cur >= t['tp']:   self.close_trade(sym, cur, "TAKE PROFIT")
                    elif cur <= t['sl']: self.close_trade(sym, cur, "STOP LOSS")

                else:
                    pnl_pct = (t['entry'] - cur) / t['entry'] * 100
                    if TRAILING and cur < t.get('lowest', t['entry']):
                        t['lowest'] = cur
                        if pnl_pct >= TRAILING_START:
                            new_sl = t['entry'] - (t['entry'] - cur) * (TRAILING_LOCK / 100)
                            if new_sl < t['sl']:
                                t['sl'] = new_sl
                                log.info(f"  Trailing SHORT {sym}: SL=${new_sl:.6f}")
                    pnl_lev = pnl_pct * LEVERAGE
                    if pnl_lev < -MAX_LOSS_PCT:
                        self._tg(f"<b>🚨 EMERGENCIA SHORT {sym}</b> {pnl_lev:+.1f}%")
                        self.close_trade(sym, cur, "STOP LOSS EMERGENCIA"); continue
                    if cur <= t['tp']:   self.close_trade(sym, cur, "TAKE PROFIT")
                    elif cur >= t['sl']: self.close_trade(sym, cur, "STOP LOSS")

                if abs(pnl_pct) > 0.3:
                    log.info(f"  {direction} {sym}: {pnl_pct:+.2f}%")

            except Exception as e:
                log.debug(f"Monitor {sym}: {e}")

    def _reporte(self):
        if datetime.now() - self._last_report < timedelta(hours=1): return
        self._last_report = datetime.now()
        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        pos_txt = ""
        for sym, t in self.open_trades.items():
            tk = ticker(sym)
            cur = tk['price'] if tk else t['entry']
            d   = t['direction']
            pct = (cur - t['entry'])/t['entry']*100 if d=='LONG' else (t['entry']-cur)/t['entry']*100
            pos_txt += f"  {d} {sym}: {pct:+.2f}%\n"
        self._tg(
            f"<b>📊 Reporte Wyckoff SMC v1.2</b>\n"
            f"PnL: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}%\n"
            f"Hoy: ${self._daily_pnl:+.3f}\n"
            f"({self.stats['wins']}W/{self.stats['losses']}L | {self.stats['closed']} trades)\n"
            f"Abiertos: {len(self.open_trades)}/{MAX_TRADES}\n"
            + (pos_txt or "  sin posiciones\n")
        )

    def _tg(self, msg):
        try:
            if TELEGRAM_TOKEN and TELEGRAM_CHAT:
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={'chat_id':TELEGRAM_CHAT,'text':msg,'parse_mode':'HTML'}, timeout=6)
        except: pass

    def _update_btc(self):
        try:
            d = klines('BTC-USDT', '1h', 3)
            if d and len(d['close']) >= 2:
                self._btc_trend = (d['close'][-1] - d['close'][-2]) / d['close'][-2] * 100
        except: pass

    # ---------------------------------------------------------------- loop

    async def run(self):
        log.info("\n▶  Wyckoff SMC Bot v1.2 OPTIMIZADO arrancado\n")
        iteration, last_refresh = 0, 0
        while True:
            try:
                iteration += 1
                if time.time() - last_refresh > 600:
                    self._get_symbols(); last_refresh = time.time()

                self._update_btc()
                if self._check_circuit():
                    await asyncio.sleep(INTERVAL); continue

                total = self.stats['wins'] + self.stats['losses']
                wr    = self.stats['wins'] / total * 100 if total else 0
                log.info(f"\n{'='*70}")
                log.info(f"  [v1.2] #{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                         f"Abiertos:{len(self.open_trades)}/{MAX_TRADES} | "
                         f"PnL:${self.stats['pnl']:+.3f} | WR:{wr:.1f}%")
                log.info(f"  BTC 1h:{self._btc_trend:+.2f}% | Día:${self._daily_pnl:+.3f}")
                log.info(f"{'='*70}\n")

                await self.monitor()
                self._reporte()

                pos_reales = self._count_real(force=True)
                slots      = MAX_TRADES - max(pos_reales, len(self.open_trades))
                log.info(f"  [v1.2] Slots libres: {slots} (BingX={pos_reales})")

                if slots > 0:
                    found = 0
                    for i, sym in enumerate(self.symbols):
                        if max(self._count_real(), len(self.open_trades)) >= MAX_TRADES:
                            break
                        sig = self.analyze_symbol(sym)
                        if sig:
                            found += 1
                            log.info(f"  ★ [v1.2] {sig['direction']} {sym} score:{sig['score']:.0f} RR:{sig['rr']:.2f}")
                            if self.open_trade(sig):
                                await asyncio.sleep(3)
                        await asyncio.sleep(0.3)
                        if (i+1) % 10 == 0:
                            log.info(f"  [v1.2] ...{i+1}/{len(self.symbols)} analizados")
                    log.info(f"\n  [v1.2] {len(self.symbols)} pares | {found} señales encontradas")
                else:
                    log.info(f"  [v1.2] Max trades ({MAX_TRADES}) alcanzado — esperando")

                log.info(f"\n  [v1.2] Próximo ciclo en {INTERVAL}s\n")
                await asyncio.sleep(INTERVAL)

            except KeyboardInterrupt:
                log.info("Wyckoff Bot v1.2 detenido"); break
            except Exception as e:
                log.error(f"[v1.2] Error loop #{iteration}: {e}")
                await asyncio.sleep(20)

async def main():
    try: await WyckoffBot().run()
    except Exception as e: log.error(f"Error fatal v1.2: {e}")

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: log.info("Terminado")
