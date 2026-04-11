#!/usr/bin/env python3
"""
🏆 INSTITUTIONAL BOT v4.0 — Volume Profile Edition
═══════════════════════════════════════════════════════════════════════

FILOSOFÍA (@pheonix_trader + @Tradewrite):
  "Los indicadores van con retraso. Necesito ESTRUCTURA, no confirmación."
  "Entra dentro de un HVN. Apunta al siguiente HVN. Esa es la estructura completa."
  "Ingresa en la dirección de la nube, apunta al siguiente HVN, stop en el HVN anterior."

MEJORAS v4.0 sobre v3.0:
├─ Volume Profile — calcula HVN/LVN sobre las últimas N velas
├─ HVN Entry Filter — solo entrar cuando precio está EN o cerca de un HVN
├─ HVN Target — TP apunta al siguiente HVN superior
├─ HVN Stop — SL se coloca en el HVN inferior anterior
├─ LVN Avoidance — evitar entrar en zonas de bajo volumen (precio pasa rápido)
├─ POC Detection — Point of Control = precio con más volumen (imán de precio)
├─ Value Area — zona 70% del volumen (VA High / VA Low)
└─ Todo lo de v3.0: VCP, Flag, MA10/MA20, Regime, BTC Guard, Kelly...

ESTRATEGIA HVN (@Tradewrite):
  1. Precio en HVN → zona de soporte/resistencia con volumen institucional
  2. TP al siguiente HVN arriba (precio tiende a moverse entre HVNs)
  3. SL en el HVN anterior abajo (si rompe ese nivel, la tesis falla)
  4. En LVN el precio se mueve rápido → no entrar, solo usar como TP acelerado

REGLA DE ORO: Estructura > Indicadores. Volumen confirma precio.
Win Rate objetivo: 68-75% | RR: 2.0-3.5× | Señales/día: 3-5
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re, json
import statistics
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

# ════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════════════

def clean_env(key: str, default, typ='str'):
    v = os.getenv(key, str(default)).strip()
    if v.startswith('"') and v.endswith('"'): v = v[1:-1]
    elif v.startswith("'") and v.endswith("'"): v = v[1:-1]
    if typ in ('int', 'float'):
        v = v.replace(',', '.')
        m = re.match(r'^-?\d+\.?\d*', v)
        v = m.group(0) if m else str(default)
    if typ == 'int':   return int(float(v))
    if typ == 'float': return float(v)
    if typ == 'bool':  return v.lower() == 'true'
    return v

# API
API_KEY    = clean_env('BINGX_API_KEY', '')
API_SECRET = clean_env('BINGX_API_SECRET', '')
TG_TOKEN   = clean_env('TELEGRAM_BOT_TOKEN', '')
TG_CHAT    = clean_env('TELEGRAM_CHAT_ID', '')

# CAPITAL
AUTO_TRADING   = clean_env('AUTO_TRADING_ENABLED', 'true', 'bool')
POSITION_SIZE  = clean_env('POSITION_SIZE_USD', '15', 'float')
LEVERAGE       = min(clean_env('LEVERAGE', '3', 'int'), 5)
MAX_POSITIONS  = clean_env('MAX_POSITIONS', '4', 'int')
ACCOUNT_EQUITY = clean_env('ACCOUNT_EQUITY', '100', 'float')
RISK_PER_TRADE = clean_env('RISK_PCT_PER_TRADE', '1.5', 'float')

# FUNDING RATE
FUNDING_LONG_OK   = clean_env('FUNDING_LONG_OK', '0.03', 'float')
FUNDING_LONG_SKIP = clean_env('FUNDING_LONG_SKIP', '0.05', 'float')
FUNDING_ENABLED   = clean_env('FUNDING_FILTER', 'true', 'bool')

# OPEN INTEREST
OI_BREAKOUT_MIN   = clean_env('OI_BREAKOUT_MIN', '1.5', 'float')
OI_WEAK_THRESHOLD = clean_env('OI_WEAK_THRESHOLD', '0.5', 'float')
OI_ENABLED        = clean_env('OI_FILTER', 'true', 'bool')

# SESSION (US/London)
SESSION_BEST  = {13, 14, 15, 16, 17, 18, 19, 20, 21, 22}
SESSION_OK    = {7, 8, 9, 10, 11, 12}
SESSION_AVOID = {22, 23, 0, 1, 2, 3, 4, 5, 6}
SESSION_FILTER_ENABLED = clean_env('SESSION_FILTER', 'true', 'bool')

# CVD
CVD_LOOKBACK  = clean_env('CVD_LOOKBACK_BARS', '20', 'int')
CVD_THRESHOLD = clean_env('CVD_THRESHOLD', '1.5', 'float')

# ── v4.0 VOLUME PROFILE / HVN ────────────────────────────────────
# Cuántas velas usar para construir el Volume Profile
VP_LOOKBACK      = clean_env('VP_LOOKBACK', '100', 'int')
# Número de bins (filas) del perfil — Tradewrite: "cambia el tamaño de fila a 200"
VP_BINS          = clean_env('VP_BINS', '200', 'int')
# Un nodo es "High Volume" si tiene >X% del volumen total
HVN_THRESHOLD    = clean_env('HVN_THRESHOLD_PCT', '6.0', 'float')
# Un nodo es "Low Volume" si tiene <X% del volumen total  
LVN_THRESHOLD    = clean_env('LVN_THRESHOLD_PCT', '2.0', 'float')
# Tolerancia para considerar que el precio "está en" un HVN (% del precio)
HVN_PROXIMITY    = clean_env('HVN_PROXIMITY_PCT', '0.8', 'float')
# Requerir que el precio esté cerca de un HVN para entrar
HVN_ENTRY_FILTER = clean_env('HVN_ENTRY_FILTER', 'true', 'bool')
# Bonus de score por estar en HVN
HVN_SCORE_BONUS  = clean_env('HVN_SCORE_BONUS', '20', 'float')

# STOP LOSS & TP
SL_ATR_MULT  = clean_env('SL_ATR_MULTIPLIER', '1.2', 'float')
SL_MIN_PCT   = clean_env('SL_MIN_PCT', '0.6', 'float')
SL_MAX_PCT   = clean_env('SL_MAX_PCT', '2.5', 'float')
TP1_PCT      = clean_env('TP1_PERCENTAGE', '35', 'float')
TP2_PCT      = clean_env('TP2_PERCENTAGE', '35', 'float')
TP1_RR       = clean_env('TP1_RISK_REWARD', '1.2', 'float')
TP2_RR       = clean_env('TP2_RISK_REWARD', '2.2', 'float')
RUNNER_TRAIL = clean_env('RUNNER_TRAIL_ATR', '1.5', 'float')
MIN_EDGE     = clean_env('MIN_EDGE_RATIO', '3.0', 'float')

# ── v3.0 NEW FILTERS ──────────────────────────────────────────────
# Volume breakout (Phoenix trader: "grandes movimientos empiezan con grandes compras")
VOLUME_BREAKOUT_MULT = clean_env('VOLUME_BREAKOUT_MULT', '1.5', 'float')  # >1.5× avg vol

# Market regime (no operar en laterales)
REGIME_ATR_MIN_PCT = clean_env('REGIME_ATR_MIN_PCT', '0.4', 'float')  # ATR >0.4% = hay movimiento
REGIME_ATR_MAX_PCT = clean_env('REGIME_ATR_MAX_PCT', '4.0', 'float')  # ATR <4.0% = no caos

# VCP pattern settings
VCP_LOOKBACK = clean_env('VCP_LOOKBACK', '20', 'int')  # Barras para detectar VCP

# Correlation guard
MAX_CORR_LONGS = clean_env('MAX_CORR_LONGS', '2', 'int')  # Max altcoins correlacionadas

# 9 EMA confirmation (ORB strategy)
EMA9_REQUIRED = clean_env('EMA9_REQUIRED', 'true', 'bool')  # Precio sobre EMA9

# MARKET FILTERS
MIN_VOLUME_24H = clean_env('MIN_VOLUME_24H', '1000000', 'float')
MAX_SYMBOLS    = clean_env('MAX_SYMBOLS', '50', 'int')
MIN_SCORE      = clean_env('MIN_ENTRY_SCORE', '72', 'float')  # Subido de 70 a 72

# CIRCUIT BREAKER
CIRCUIT_BREAKER_PCT = clean_env('CIRCUIT_BREAKER_PCT', '6.0', 'float')
MAX_LOSING_STREAK   = clean_env('MAX_LOSING_STREAK', '4', 'int')

# TIMING
SCAN_INTERVAL    = clean_env('SCAN_INTERVAL_SEC', '60', 'int')
MONITOR_INTERVAL = clean_env('MONITOR_INTERVAL_SEC', '15', 'int')

# CONSTANTS
BASE_URL   = "https://open-api.bingx.com"
FEE_TAKER  = 0.001
FEE_MAKER  = 0.0002
SLIPPAGE   = 0.0002
TOTAL_COST = FEE_TAKER + FEE_MAKER + SLIPPAGE

EXCLUDE_SYMBOLS = {
    'DOW', 'SP500', 'GOLD', 'SILVER', 'XAU', 'OIL', 'BRENT',
    'EUR', 'GBP', 'JPY', 'TSLA', 'AAPL', 'MSFT', 'GOOGL',
    'AMZN', 'META', 'NVDA', 'COIN', 'MSTR', 'PAXG', 'XAUT'
}

# ════════════════════════════════════════════════════════════════════
# LOGGING
# ════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════
# API FUNCTIONS
# ════════════════════════════════════════════════════════════════════

def api_request(method: str, endpoint: str, params: dict = None, retries: int = 3) -> dict:
    params = params or {}
    for attempt in range(retries + 1):
        try:
            p = {**{k: str(v) for k, v in params.items()},
                 'timestamp': str(int(time.time() * 1000))}
            query = urlencode(sorted(p.items()))
            signature = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
            url = f"{BASE_URL}{endpoint}?{query}&signature={signature}"
            headers = {'X-BX-APIKEY': API_KEY, 'Content-Type': 'application/x-www-form-urlencoded'}
            response = getattr(requests, method.lower())(url, headers=headers, timeout=15)
            return response.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
            else:
                log.error(f"API {endpoint} failed: {e}")
                return {}

def public_request(path: str, params: dict = None) -> dict:
    try:
        response = requests.get(f"{BASE_URL}{path}", params=params or {}, timeout=10)
        return response.json()
    except:
        return {}

def safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except:
        return default

def extract_equity(data: dict) -> float:
    """Extrae equity de respuesta BingX — maneja estructura anidada data.balance.equity"""
    if data.get('code') != 0:
        return 0.0
    raw = data.get('data', {})
    if isinstance(raw, dict) and 'balance' in raw:
        inner = raw['balance']
        if isinstance(inner, dict):
            return safe_float(inner.get('equity', inner.get('availableMargin', 0)))
    if isinstance(raw, dict):
        return safe_float(raw.get('equity', raw.get('balance', 0)))
    return 0.0

# ════════════════════════════════════════════════════════════════════
# TECHNICAL INDICATORS — Minimalistas como @pheonix_trader
# ════════════════════════════════════════════════════════════════════

def ema(prices: List[float], period: int) -> float:
    if not prices or len(prices) < period:
        return sum(prices) / len(prices) if prices else 0
    k = 2 / (period + 1)
    val = prices[0]
    for p in prices[1:]:
        val = p * k + val * (1 - k)
    return val

def sma(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0
    return sum(prices[-period:]) / period

def atr_calc(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if len(closes) < 2:
        return 0
    trs = []
    for i in range(1, min(len(closes), period + 1)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0

def rsi_calc(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    gains = [max(prices[i] - prices[i-1], 0) for i in range(1, len(prices))]
    losses = [max(prices[i-1] - prices[i], 0) for i in range(1, len(prices))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    return 100 - (100 / (1 + ag/al)) if al > 0 else 100.0

def volume_avg(volumes: List[float], period: int = 20) -> float:
    recent = volumes[-period:] if len(volumes) >= period else volumes
    return sum(recent) / len(recent) if recent else 0

def cvd_calc(volumes: List[float], closes: List[float], opens: List[float]) -> float:
    if len(volumes) < 2:
        return 0
    cvd = 0
    for i in range(len(volumes)):
        delta = 1 if closes[i] > opens[i] else -1 if closes[i] < opens[i] else 0
        cvd += volumes[i] * delta
    return cvd

# ════════════════════════════════════════════════════════════════════
# v4.0 VOLUME PROFILE — HVN / LVN / POC / Value Area
# ════════════════════════════════════════════════════════════════════

def build_volume_profile(closes: List[float], highs: List[float],
                          lows: List[float], volumes: List[float],
                          n_bins: int = 20) -> Dict:
    """
    Construye el Volume Profile de las últimas N velas.
    
    Divide el rango de precio en bins y acumula el volumen de cada vela
    en el bin correspondiente al precio típico (HLC/3).
    
    Returns:
        {
          'bins': [(precio_centro, volumen_acumulado), ...],
          'poc': float,          # Point of Control = precio con más volumen
          'va_high': float,      # Value Area High (70% del volumen arriba del POC)
          'va_low': float,       # Value Area Low (70% del volumen abajo del POC)
          'hvn_list': [float],   # High Volume Nodes (zonas de soporte/resistencia)
          'lvn_list': [float],   # Low Volume Nodes (el precio pasa rápido aquí)
          'total_volume': float,
        }
    """
    if len(closes) < 10:
        return {'bins': [], 'poc': 0, 'va_high': 0, 'va_low': 0,
                'hvn_list': [], 'lvn_list': [], 'total_volume': 0}

    price_min = min(lows)
    price_max = max(highs)
    if price_max <= price_min:
        return {'bins': [], 'poc': closes[-1], 'va_high': closes[-1],
                'va_low': closes[-1], 'hvn_list': [closes[-1]], 'lvn_list': [],
                'total_volume': sum(volumes)}

    bin_size = (price_max - price_min) / n_bins
    bins = [0.0] * n_bins

    for i in range(len(closes)):
        typical_price = (highs[i] + lows[i] + closes[i]) / 3
        bin_idx = int((typical_price - price_min) / bin_size)
        bin_idx = max(0, min(n_bins - 1, bin_idx))
        bins[bin_idx] += volumes[i]

    total_vol = sum(bins)
    if total_vol <= 0:
        return {'bins': [], 'poc': closes[-1], 'va_high': closes[-1],
                'va_low': closes[-1], 'hvn_list': [], 'lvn_list': [],
                'total_volume': 0}

    # Precio centro de cada bin
    bin_prices = [price_min + (i + 0.5) * bin_size for i in range(n_bins)]

    # POC = bin con mayor volumen
    poc_idx = bins.index(max(bins))
    poc = bin_prices[poc_idx]

    # Value Area: 70% del volumen alrededor del POC
    target_vol = total_vol * 0.70
    va_vol = bins[poc_idx]
    lo_idx, hi_idx = poc_idx, poc_idx

    while va_vol < target_vol and (lo_idx > 0 or hi_idx < n_bins - 1):
        add_hi = bins[hi_idx + 1] if hi_idx < n_bins - 1 else 0
        add_lo = bins[lo_idx - 1] if lo_idx > 0 else 0
        if add_hi >= add_lo:
            hi_idx = min(hi_idx + 1, n_bins - 1)
            va_vol += bins[hi_idx]
        else:
            lo_idx = max(lo_idx - 1, 0)
            va_vol += bins[lo_idx]

    va_high = bin_prices[hi_idx]
    va_low  = bin_prices[lo_idx]

    # HVN = bins con volumen > HVN_THRESHOLD% del total
    hvn_list = []
    lvn_list = []
    for i, (bp, bv) in enumerate(zip(bin_prices, bins)):
        pct = bv / total_vol * 100
        if pct >= HVN_THRESHOLD:
            hvn_list.append(bp)
        elif pct <= LVN_THRESHOLD and bv > 0:
            lvn_list.append(bp)

    # Construir lista de bins para referencia
    bins_data = [(bp, bv) for bp, bv in zip(bin_prices, bins)]

    return {
        'bins': bins_data,
        'poc': poc,
        'va_high': va_high,
        'va_low': va_low,
        'hvn_list': sorted(hvn_list),
        'lvn_list': sorted(lvn_list),
        'total_volume': total_vol,
        'bin_size': bin_size,
    }


def analyze_hvn_context(price: float, vp: Dict) -> Dict:
    """
    Analiza la posición del precio respecto al Volume Profile.
    
    Implementa la estrategia de @Tradewrite:
    - ¿Estamos en un HVN? → buena zona de entrada
    - ¿Cuál es el siguiente HVN arriba? → ese es nuestro TP
    - ¿Cuál es el HVN anterior abajo? → ese es nuestro SL
    - ¿Estamos en un LVN? → el precio se moverá rápido, esperar
    
    Returns:
        {
          'in_hvn': bool,           # Precio está dentro/cerca de un HVN
          'hvn_current': float,     # HVN más cercano al precio actual
          'hvn_next_up': float,     # Siguiente HVN arriba → TARGET
          'hvn_prev_down': float,   # HVN anterior abajo → STOP
          'in_lvn': bool,           # Precio en zona de bajo volumen
          'above_poc': bool,        # Precio sobre el POC (tendencia alcista)
          'in_value_area': bool,    # Precio dentro del Value Area
          'hvn_rr': float,          # RR natural HVN→HVN
          'context': str,           # Descripción del contexto
        }
    """
    if not vp or not vp.get('hvn_list'):
        return {
            'in_hvn': False, 'hvn_current': 0, 'hvn_next_up': 0,
            'hvn_prev_down': 0, 'in_lvn': False, 'above_poc': False,
            'in_value_area': False, 'hvn_rr': 0, 'context': 'no_vp_data'
        }

    poc      = vp['poc']
    va_high  = vp['va_high']
    va_low   = vp['va_low']
    hvn_list = vp['hvn_list']
    lvn_list = vp['lvn_list']
    bin_size = vp.get('bin_size', price * 0.005)

    proximity = price * (HVN_PROXIMITY / 100)

    # ¿Está el precio cerca de algún HVN?
    in_hvn = False
    hvn_current = 0
    min_dist = float('inf')
    for hvn in hvn_list:
        dist = abs(price - hvn)
        if dist < min_dist:
            min_dist = dist
            hvn_current = hvn
        if dist <= proximity:
            in_hvn = True

    # Siguiente HVN arriba del precio → TARGET natural
    hvn_next_up = 0
    for hvn in sorted(hvn_list):
        if hvn > price * 1.002:  # Al menos 0.2% arriba
            hvn_next_up = hvn
            break

    # HVN anterior abajo → STOP natural
    hvn_prev_down = 0
    for hvn in sorted(hvn_list, reverse=True):
        if hvn < price * 0.998:  # Al menos 0.2% abajo
            hvn_prev_down = hvn
            break

    # ¿En zona de bajo volumen?
    in_lvn = any(abs(price - lvn) <= proximity for lvn in lvn_list)

    # ¿Sobre el POC?
    above_poc = price > poc

    # ¿Dentro del Value Area?
    in_value_area = va_low <= price <= va_high

    # RR natural HVN-to-HVN
    hvn_rr = 0.0
    if hvn_next_up > price and hvn_prev_down > 0 and hvn_prev_down < price:
        potential_gain = hvn_next_up - price
        potential_loss = price - hvn_prev_down
        hvn_rr = potential_gain / potential_loss if potential_loss > 0 else 0

    # Contexto descriptivo
    if in_lvn:
        context = "lvn_fast_move"
    elif in_hvn and above_poc:
        context = "hvn_bullish_entry"
    elif in_hvn and not above_poc:
        context = "hvn_below_poc"
    elif in_value_area and above_poc:
        context = "value_area_bullish"
    elif price > va_high:
        context = "above_value_area"
    elif price < va_low:
        context = "below_value_area"
    else:
        context = "neutral"

    return {
        'in_hvn': in_hvn,
        'hvn_current': hvn_current,
        'hvn_next_up': hvn_next_up,
        'hvn_prev_down': hvn_prev_down,
        'in_lvn': in_lvn,
        'above_poc': above_poc,
        'in_value_area': in_value_area,
        'in_va': in_value_area,
        'poc': poc,
        'va_high': va_high,
        'va_low': va_low,
        'hvn_rr': hvn_rr,
        'context': context,
        'hvn_count': len(hvn_list),
    }

# ════════════════════════════════════════════════════════════════════
# v3.0 PATTERN DETECTION — La clave de @pheonix_trader
# ════════════════════════════════════════════════════════════════════

def detect_vcp(closes: List[float], volumes: List[float], lookback: int = 20) -> Tuple[bool, str]:
    """
    VCP — Volatility Contraction Pattern (Mark Minervini)
    El patrón más poderoso de momentum: contracciones de rango decrecientes
    con volumen decreciente → señal de acumulación institucional.
    
    Estructura: Cada corrección es más pequeña que la anterior.
    C1 > C2 > C3 (contracciones decrecientes)
    V1 > V2 > V3 (volumen decreciente en correcciones)
    """
    if len(closes) < lookback:
        return False, "insufficient_data"
    
    recent = closes[-lookback:]
    recent_vols = volumes[-lookback:]
    
    # Calcular swings (correcciones)
    contractions = []
    for i in range(2, len(recent) - 2):
        if recent[i] < recent[i-1] and recent[i] < recent[i+1]:
            # Es un mínimo local (corrección)
            depth = (recent[i-1] - recent[i]) / recent[i-1] * 100
            vol_at_dip = recent_vols[i]
            contractions.append((depth, vol_at_dip))
    
    if len(contractions) < 2:
        return False, "no_vcp_contractions"
    
    # Verificar que contracciones son decrecientes (VCP clásico)
    depths = [c[0] for c in contractions[-3:]]  # Últimas 3
    vols = [c[1] for c in contractions[-3:]]
    
    # Cada contracción < anterior (volatilidad contrayéndose)
    depth_contracting = all(depths[i] < depths[i-1] * 1.1 for i in range(1, len(depths)))
    
    # Precio cerca del máximo reciente (tight base)
    recent_high = max(recent)
    current = recent[-1]
    near_high = current >= recent_high * 0.92  # Dentro del 8% del máximo
    
    if depth_contracting and near_high and len(contractions) >= 2:
        return True, f"vcp_{len(contractions)}contractions"
    
    return False, "no_vcp"

def detect_flag(closes: List[float], volumes: List[float], highs: List[float], 
                lows: List[float]) -> Tuple[bool, str]:
    """
    FLAG Pattern — Banderín/Bandera
    Fuerte impulso (pole) → consolidación en rango estrecho (flag) → breakout
    
    Estructura:
    1. Pole: subida >5% en pocos días con volumen alto
    2. Flag: consolidación estrecha <3% de rango, volumen bajo
    3. Entry: breakout del flag con volumen >1.5× media
    """
    if len(closes) < 15:
        return False, "insufficient_data"
    
    # Detectar el "pole" (impulso previo)
    pole_window = 7  # Últimas 7 velas para el polo
    flag_window = 5  # Últimas 5 para el flag
    
    if len(closes) < pole_window + flag_window:
        return False, "insufficient_data"
    
    pole_closes = closes[-(pole_window + flag_window):-flag_window]
    flag_closes = closes[-flag_window:]
    flag_vols = volumes[-flag_window:]
    pole_vols = volumes[-(pole_window + flag_window):-flag_window]
    
    # 1. El pole debe ser un movimiento fuerte (>4%)
    pole_move = (pole_closes[-1] - pole_closes[0]) / pole_closes[0] * 100
    
    # 2. El flag debe ser una consolidación estrecha (<3.5%)
    flag_range = (max(flag_closes) - min(flag_closes)) / min(flag_closes) * 100
    
    # 3. Volumen en flag debe ser menor que en pole
    avg_pole_vol = sum(pole_vols) / len(pole_vols)
    avg_flag_vol = sum(flag_vols) / len(flag_vols)
    vol_declining = avg_flag_vol < avg_pole_vol * 0.8
    
    # 4. Flag no debe retroceder más del 50% del pole
    flag_retrace = (pole_closes[-1] - min(flag_closes)) / (pole_closes[-1] - pole_closes[0]) * 100
    
    if (pole_move > 4.0 and 
        flag_range < 3.5 and 
        vol_declining and 
        flag_retrace < 50 and
        len(flag_closes) >= 3):
        return True, f"flag_pole{pole_move:.1f}pct_range{flag_range:.1f}pct"
    
    return False, "no_flag"

def detect_market_regime(closes: List[float], highs: List[float], 
                          lows: List[float], volumes: List[float]) -> Tuple[str, float]:
    """
    Market Regime Detection — Solo opera en tendencia, no en laterales
    
    TRENDING:    precio > MA20, MA20 subiendo, ATR normal → OPERAR
    RANGING:     precio oscila alrededor de MA20 → EVITAR  
    VOLATILE:    ATR muy alto → EVITAR (caos)
    BEARISH:     precio < MA20 bajando → EVITAR
    """
    if len(closes) < 30:
        return "unknown", 0.0
    
    current = closes[-1]
    ma10 = sma(closes, 10)
    ma20 = sma(closes, 20)
    
    # ATR para medir volatilidad del régimen
    atr_val = atr_calc(highs, lows, closes, 14)
    atr_pct = (atr_val / current * 100) if current > 0 else 0
    
    # MA20 trend direction
    ma20_prev = sma(closes[:-5], 20) if len(closes) > 25 else ma20
    ma20_rising = ma20 > ma20_prev
    
    # Precio relativo a MAs
    above_ma10 = current > ma10
    above_ma20 = current > ma20
    
    # Volatilidad extrema → no operar
    if atr_pct > REGIME_ATR_MAX_PCT:
        return "volatile_extreme", atr_pct
    
    # Muy poca volatilidad → lateral, no hay edge
    if atr_pct < REGIME_ATR_MIN_PCT:
        return "ranging_quiet", atr_pct
    
    # Tendencia alcista clara (lo que busca @pheonix_trader)
    if above_ma10 and above_ma20 and ma20_rising:
        return "trending_bullish", atr_pct
    
    # Tendencia alcista moderada
    if above_ma20 and ma20_rising:
        return "trending_moderate", atr_pct
    
    # Bajista
    if not above_ma20 and not ma20_rising:
        return "bearish", atr_pct
    
    return "ranging", atr_pct

def check_volume_breakout(volumes: List[float], current_vol: float) -> Tuple[bool, float]:
    """
    Volume Breakout Filter — @pheonix_trader: "grandes movimientos empiezan con grandes compras"
    
    Breakout real: volumen actual > 1.5× volumen medio
    Breakout débil: volumen actual < media → ignorar señal
    """
    if len(volumes) < 10:
        return True, 1.0
    
    avg_vol = volume_avg(volumes[:-1], 20)  # Excluir vela actual
    if avg_vol <= 0:
        return True, 1.0
    
    vol_ratio = current_vol / avg_vol
    is_breakout = vol_ratio >= VOLUME_BREAKOUT_MULT
    return is_breakout, vol_ratio

def check_9ema_setup(closes: List[float], current_price: float) -> Tuple[bool, str]:
    """
    9 EMA Scalp Setup — ThiccTeddy ORB Strategy
    
    Si precio está sobre EMA9 → momentum alcista a corto plazo
    Si precio acaba de cruzar EMA9 al alza → entry point óptimo
    """
    ema9 = ema(closes, 9)
    ema9_prev = ema(closes[:-1], 9) if len(closes) > 1 else ema9
    
    above_ema9 = current_price > ema9
    
    # Cruce reciente al alza (más potente)
    prev_price = closes[-2] if len(closes) > 1 else current_price
    fresh_cross = prev_price <= ema9_prev and current_price > ema9
    
    if fresh_cross:
        return True, "ema9_fresh_cross"
    elif above_ema9:
        return True, "above_ema9"
    else:
        return False, "below_ema9"

def kelly_position_size(win_rate: float, avg_win: float, avg_loss: float, 
                         equity: float) -> float:
    """
    Half-Kelly Position Sizing — Maximiza crecimiento sin arruinar la cuenta
    
    Kelly% = W - (1-W)/R donde W=win_rate, R=win/loss ratio
    Usamos Half-Kelly (más conservador) para reducir drawdowns
    """
    if avg_loss <= 0 or win_rate <= 0:
        return POSITION_SIZE
    
    R = avg_win / avg_loss
    kelly_pct = win_rate - (1 - win_rate) / R
    half_kelly = kelly_pct / 2  # Half-Kelly
    
    # Limitar entre 0.5% y 3% del equity
    half_kelly = max(0.005, min(0.03, half_kelly))
    
    kelly_size = equity * half_kelly
    
    # No superar POSITION_SIZE máximo configurado
    return min(kelly_size, POSITION_SIZE)

# ════════════════════════════════════════════════════════════════════
# INSTITUTIONAL FILTERS v3.0
# ════════════════════════════════════════════════════════════════════

class InstitutionalFilters:
    def __init__(self):
        self.funding_cache = {}
        self.oi_cache = {}
        self.last_update = {}
        self.btc_price_history = deque(maxlen=10)
        self.btc_dominance_cache = 0.0
        self.btc_dom_last_update = 0

    def check_funding_rate(self, symbol: str) -> Tuple[bool, str, float]:
        if not FUNDING_ENABLED:
            return True, "funding_disabled", 0
        cache_key = f"{symbol}_funding"
        if cache_key in self.last_update and time.time() - self.last_update[cache_key] < 300:
            rate = self.funding_cache.get(cache_key, 0)
            return (rate < FUNDING_LONG_OK), ("funding_ok" if rate < FUNDING_LONG_OK else "funding_high"), rate
        try:
            data = public_request('/openApi/swap/v2/quote/premiumIndex', {'symbol': symbol})
            if data.get('code') == 0 and data.get('data'):
                rate = safe_float(data['data'].get('lastFundingRate', 0)) * 100
                self.funding_cache[cache_key] = rate
                self.last_update[cache_key] = time.time()
                if rate > FUNDING_LONG_SKIP:
                    return False, "funding_overheated", rate
                return rate < FUNDING_LONG_OK, "funding_ok" if rate < FUNDING_LONG_OK else "funding_neutral", rate
        except:
            pass
        return True, "funding_unknown", 0

    def check_open_interest(self, symbol: str, price_change_pct: float) -> Tuple[bool, str, float]:
        if not OI_ENABLED:
            return True, "oi_disabled", 0
        try:
            data = public_request('/openApi/swap/v2/quote/openInterest', {'symbol': symbol})
            if data.get('code') == 0 and data.get('data'):
                current_oi = safe_float(data['data'].get('openInterest', 0))
                cache_key = f"{symbol}_oi"
                if cache_key in self.oi_cache:
                    prev_oi = self.oi_cache[cache_key]
                    oi_change = ((current_oi - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0
                    self.oi_cache[cache_key] = current_oi
                    if price_change_pct > 1.0 and oi_change > OI_BREAKOUT_MIN:
                        return True, "oi_breakout_confirmed", oi_change
                    elif price_change_pct > 1.0 and oi_change < OI_WEAK_THRESHOLD:
                        return False, "oi_divergence_weak", oi_change
                    return True, "oi_neutral", oi_change
                else:
                    self.oi_cache[cache_key] = current_oi
                    return True, "oi_first_check", 0
        except:
            pass
        return True, "oi_unknown", 0

    def check_session_quality(self) -> Tuple[bool, str]:
        if not SESSION_FILTER_ENABLED:
            return True, "session_disabled"
        hour = datetime.utcnow().hour
        if hour in SESSION_BEST:
            return True, "us_session"
        elif hour in SESSION_OK:
            return True, "london_session"
        return False, "asia_session_avoid"

    def calculate_volume_quality(self, volumes, closes, opens) -> Tuple[float, str]:
        if len(volumes) < CVD_LOOKBACK:
            return 0, "cvd_insufficient_data"
        rv = volumes[-CVD_LOOKBACK:]
        rc = closes[-CVD_LOOKBACK:]
        ro = opens[-CVD_LOOKBACK:]
        cvd = cvd_calc(rv, rc, ro)
        total = sum(rv)
        cvd_n = cvd / total if total > 0 else 0
        cvd_vals = [rv[i] * (1 if rc[i] > ro[i] else -1) for i in range(len(rv))]
        try:
            std = statistics.stdev(cvd_vals) if len(cvd_vals) > 1 else 0
            if abs(cvd_n) > CVD_THRESHOLD * std:
                return cvd_n, "bullish_cvd" if cvd_n > 0 else "bearish_cvd"
        except:
            pass
        return cvd_n, "cvd_neutral"

    def check_btc_market_health(self) -> Tuple[bool, str]:
        """
        BTC Guard — No abrir altcoins si BTC está en caída libre
        Si BTC cae >2% en la última hora, evitar nuevas entradas en altcoins
        """
        try:
            data = public_request('/openApi/swap/v2/quote/ticker', {'symbol': 'BTC-USDT'})
            if data.get('code') == 0 and data.get('data'):
                ticker = data['data']
                btc_change = safe_float(ticker.get('priceChangePercent', 0))
                btc_price = safe_float(ticker.get('lastPrice', 0))
                
                self.btc_price_history.append(btc_price)
                
                # BTC cayendo fuerte → altcoins también caerán
                if btc_change < -2.0:
                    return False, f"btc_falling_{btc_change:.1f}pct"
                
                # BTC en tendencia positiva → bueno para altcoins
                if btc_change > 0:
                    return True, "btc_positive"
                
                return True, "btc_neutral"
        except:
            pass
        return True, "btc_unknown"

# ════════════════════════════════════════════════════════════════════
# LIQUIDITY ZONES
# ════════════════════════════════════════════════════════════════════

def find_liquidity_zones(highs: List[float], lows: List[float], lookback: int = 100) -> Dict:
    if len(highs) < lookback:
        return {'resistance_zones': [], 'support_zones': []}
    rh = highs[-lookback:]
    rl = lows[-lookback:]
    swing_highs = [rh[i] for i in range(2, len(rh)-2)
                   if rh[i] > rh[i-1] and rh[i] > rh[i-2] and rh[i] > rh[i+1] and rh[i] > rh[i+2]]
    swing_lows = [rl[i] for i in range(2, len(rl)-2)
                  if rl[i] < rl[i-1] and rl[i] < rl[i-2] and rl[i] < rl[i+1] and rl[i] < rl[i+2]]
    return {
        'resistance_zones': sorted(swing_highs, reverse=True)[:5],
        'support_zones': sorted(swing_lows)[:5]
    }

# ════════════════════════════════════════════════════════════════════
# INSTITUTIONAL BOT v3.0
# ════════════════════════════════════════════════════════════════════

class InstitutionalBot:
    def __init__(self):
        self.symbols = []
        self.positions = {}
        self.contracts_info = {}
        self.filters = InstitutionalFilters()
        self.equity = ACCOUNT_EQUITY
        self.daily_pnl = 0.0
        self.daily_date = datetime.utcnow().date()
        self.circuit_breaker_active = False
        self.circuit_breaker_until = None
        self.stats = {'total_trades': 0, 'wins': 0, 'losses': 0,
                      'total_pnl': 0.0, 'total_fees': 0.0,
                      'win_amounts': [], 'loss_amounts': []}
        self.corr_long_count = 0  # Correlation guard counter

        log.info("=" * 80)
        log.info("🏆 INSTITUTIONAL BOT v4.0 — Volume Profile Edition")
        log.info("=" * 80)
        log.info(f"Capital: ${POSITION_SIZE} × {MAX_POSITIONS} | Leverage: {LEVERAGE}×")
        log.info(f"Filtros: Funding={'✓' if FUNDING_ENABLED else '✗'} | OI={'✓' if OI_ENABLED else '✗'} | Session={'✓' if SESSION_FILTER_ENABLED else '✗'}")
        log.info(f"v4.0 NEW: Volume Profile ✓ | HVN Entry ✓ | HVN Target ✓ | HVN Stop ✓")
        log.info(f"v4.0 NEW: POC Detection ✓ | Value Area ✓ | LVN Avoidance ✓")
        log.info(f"v3.0: VCP Pattern ✓ | Flag Pattern ✓ | Volume Breakout ✓ | Market Regime ✓")
        log.info(f"TPs: {int(TP1_PCT)}%@{TP1_RR}RR | {int(TP2_PCT)}%@{TP2_RR}RR | {int(100-TP1_PCT-TP2_PCT)}%@trail")
        log.info(f"Min Score: {MIN_SCORE} | Min Edge: {MIN_EDGE}× | Circuit Breaker: {CIRCUIT_BREAKER_PCT}%")
        log.info("=" * 80)

        if not self._connect():
            log.error("❌ No se pudo conectar a BingX")
            sys.exit(1)

        self._load_contracts()
        self._refresh_symbols()
        self._recover_positions()

        self._send_telegram(
            f"<b>🏆 INSTITUTIONAL BOT v4.0 STARTED</b>\n\n"
            f"📊 Volume Profile Edition\n"
            f"💰 Capital: ${POSITION_SIZE} × {MAX_POSITIONS}\n"
            f"📈 Volume Profile: HVN/LVN/POC activo\n"
            f"🎯 Strategy: Entra HVN → TP siguiente HVN → SL HVN anterior\n"
            f"⚡ Patterns: VCP + Flag + Volume Breakout\n"
            f"🛡️ Guards: BTC + Correlation + Kelly\n\n"
            f"Modo: {'REAL MONEY 💸' if AUTO_TRADING else 'PAPER TRADING 📝'}"
        )

    def _connect(self) -> bool:
        global AUTO_TRADING
        if not AUTO_TRADING:
            return True
        if not API_KEY or not API_SECRET:
            log.error("API keys no configuradas")
            AUTO_TRADING = False
            return False
        data = api_request('GET', '/openApi/swap/v2/user/balance')
        if data.get('code') == 0:
            equity = extract_equity(data)
            if equity > 0:
                self.equity = equity
                log.info(f"✓ BingX conectado | Equity: ${equity:.2f}")
                return True
            else:
                log.warning("⚠️ Equity=0 en respuesta. Usando default.")
                self.equity = ACCOUNT_EQUITY
                return True
        log.error(f"Error conectando: {data}")
        AUTO_TRADING = False
        return False

    def _load_contracts(self):
        data = public_request('/openApi/swap/v2/quote/contracts')
        if data.get('code') == 0:
            for c in data.get('data', []):
                s = c.get('symbol', '')
                if s:
                    self.contracts_info[s] = {
                        'min_qty': safe_float(c.get('tradeMinQuantity', 1)),
                        'qty_precision': int(c.get('quantityPrecision', 2)),
                        'contract_size': safe_float(c.get('contractSize', 1))
                    }
            log.info(f"✓ Contratos cargados: {len(self.contracts_info)}")

    def _refresh_symbols(self):
        data = public_request('/openApi/swap/v2/quote/ticker')
        if data.get('code') != 0:
            self.symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT']
            return
        candidates = []
        for t in data.get('data', []):
            s = t.get('symbol', '')
            if not s.endswith('-USDT'):
                continue
            base = s.replace('-USDT', '').upper()
            if any(ex in base for ex in EXCLUDE_SYMBOLS):
                continue
            try:
                price = safe_float(t.get('lastPrice', 0))
                vol = safe_float(t.get('volume', 0)) * price
                if vol >= MIN_VOLUME_24H and price > 0:
                    candidates.append({'symbol': s, 'volume': vol})
            except:
                continue
        candidates.sort(key=lambda x: x['volume'], reverse=True)
        self.symbols = [c['symbol'] for c in candidates[:MAX_SYMBOLS]]
        log.info(f"✓ Símbolos activos: {len(self.symbols)}")

    def _recover_positions(self):
        if not AUTO_TRADING:
            return
        data = api_request('GET', '/openApi/swap/v2/user/positions')
        recovered = 0
        for pos in data.get('data', []):
            try:
                symbol = pos.get('symbol', '')
                amt = safe_float(pos.get('positionAmt', 0))
                side = str(pos.get('positionSide', '')).upper()
                if (side == 'LONG' or (side == 'BOTH' and amt > 0)) and abs(amt) > 0:
                    entry = safe_float(pos.get('avgPrice') or pos.get('entryPrice', 0))
                    if entry > 0:
                        self.positions[symbol] = {
                            'entry': entry, 'qty': abs(amt), 'side': 'LONG',
                            'tp1_hit': False, 'tp2_hit': False, 'recovered': True,
                            'highest': entry, 'opened_at': datetime.now(),
                            'pnl_realized': 0.0,
                            'signal': {'atr': 0},
                            'tp1_price': entry * 1.012,
                            'tp2_price': entry * 1.022,
                            'sl_price': entry * 0.988,
                            'qty_tp1': abs(amt) * TP1_PCT / 100,
                            'qty_tp2': abs(amt) * TP2_PCT / 100,
                        }
                        recovered += 1
                        log.info(f"♻️ Posición recuperada: {symbol} @ ${entry:.6f}")
            except:
                continue
        if recovered > 0:
            log.info(f"✓ Posiciones recuperadas: {recovered}")

    def _get_klines(self, symbol: str, interval: str = '5m', limit: int = 150):
        data = public_request('/openApi/swap/v3/quote/klines', {
            'symbol': symbol, 'interval': interval, 'limit': limit
        })
        if data.get('code') == 0 and data.get('data'):
            klines = data['data']
            return (
                [safe_float(k['close']) for k in klines],
                [safe_float(k['high']) for k in klines],
                [safe_float(k['low']) for k in klines],
                [safe_float(k['volume']) for k in klines],
                [safe_float(k['open']) for k in klines]
            )
        return None, None, None, None, None

    def _get_ticker(self, symbol: str):
        data = public_request('/openApi/swap/v2/quote/ticker', {'symbol': symbol})
        if data.get('code') == 0 and data.get('data'):
            t = data['data']
            return {
                'price': safe_float(t.get('lastPrice', 0)),
                'change_pct': safe_float(t.get('priceChangePercent', 0)),
                'volume': safe_float(t.get('volume', 0))
            }
        return None

    def analyze_symbol(self, symbol: str) -> Optional[Dict]:
        """
        Análisis v3.0 — Filosofía Phoenix Trader:
        ESTRUCTURA + VOLUMEN + MAs simples
        """
        if symbol in self.positions:
            return None

        closes, highs, lows, volumes, opens = self._get_klines(symbol, '5m', 150)
        if not closes or len(closes) < 50:
            return None

        ticker = self._get_ticker(symbol)
        if not ticker or ticker['price'] <= 0:
            return None

        price = ticker['price']
        change_24h = ticker['change_pct']
        current_vol = ticker['volume']

        # ═══ FILTRO 0: MARKET REGIME ═══════════════════════════════
        # "Sistema sobre ruido — solo operar cuando hay estructura"
        regime, atr_pct = detect_market_regime(closes, highs, lows, volumes)
        if regime in ("volatile_extreme", "ranging_quiet", "bearish"):
            log.debug(f"{symbol}: ❌ Régimen={regime} ({atr_pct:.2f}%)")
            return None

        # ═══ FILTRO 1: BTC HEALTH GUARD ════════════════════════════
        if symbol != 'BTC-USDT':
            btc_ok, btc_reason = self.filters.check_btc_market_health()
            if not btc_ok:
                log.debug(f"{symbol}: ❌ {btc_reason}")
                return None

        # ═══ FILTRO 2: CORRELATION GUARD ═══════════════════════════
        # Max 2 altcoins correlacionadas long simultáneas
        if symbol != 'BTC-USDT' and symbol != 'ETH-USDT':
            corr_longs = sum(1 for s in self.positions if s not in ('BTC-USDT', 'ETH-USDT'))
            if corr_longs >= MAX_CORR_LONGS:
                log.debug(f"{symbol}: ❌ Correlation guard ({corr_longs} altcoins ya abiertas)")
                return None

        # ═══ FILTRO 3: FUNDING RATE ═════════════════════════════════
        funding_ok, funding_reason, funding_rate = self.filters.check_funding_rate(symbol)
        if not funding_ok:
            return None

        # ═══ FILTRO 4: OPEN INTEREST ════════════════════════════════
        oi_ok, oi_reason, oi_change = self.filters.check_open_interest(symbol, change_24h)
        if not oi_ok:
            return None

        # ═══ FILTRO 5: SESSION ══════════════════════════════════════
        session_ok, session_name = self.filters.check_session_quality()
        if not session_ok:
            return None

        # ═══ FILTRO 6: VOLUME BREAKOUT ══════════════════════════════
        # "Los grandes movimientos empiezan con grandes compras"
        vol_breakout, vol_ratio = check_volume_breakout(volumes, current_vol)

        # ═══ TECHNICAL ANALYSIS — MA10/MA20 (Phoenix trader) ═══════
        ma10  = sma(closes, 10)
        ma20  = sma(closes, 20)
        ema9  = ema(closes, 9)
        ema50 = ema(closes, 50)

        # ═══ v4.0: EMA CLOUD DIRECTION — @Tradewrite ════════════════
        # "Inclinada hacia arriba = compradores en control → entrar"
        # "Plana = quédate fuera"
        # "Inclinada hacia abajo = vendedores → no entrar"
        ema20_now  = ema(closes, 20)
        ema50_now  = ema(closes, 50)
        ema20_prev = ema(closes[:-3], 20) if len(closes) > 23 else ema20_now
        ema50_prev = ema(closes[:-3], 50) if len(closes) > 53 else ema50_now

        cloud_slope_20 = (ema20_now - ema20_prev) / ema20_prev * 100 if ema20_prev > 0 else 0
        cloud_slope_50 = (ema50_now - ema50_prev) / ema50_prev * 100 if ema50_prev > 0 else 0

        # Nube subiendo = ambas EMAs con slope positivo
        cloud_up   = cloud_slope_20 > 0.01 and cloud_slope_50 > 0.005
        cloud_flat = abs(cloud_slope_20) < 0.01  # Plana → no operar
        cloud_down = cloud_slope_20 < -0.01 and cloud_slope_50 < -0.005

        # "Nube plana o bajando → quédate fuera" — @Tradewrite
        if cloud_flat or cloud_down:
            log.debug(f"{symbol}: ❌ EMA Cloud plana/bajando (slope20={cloud_slope_20:.3f}%)")
            return None

        atr_val = atr_calc(highs, lows, closes, 14)
        rsi_val = rsi_calc(closes, 14)
        cvd_val, cvd_signal = self.filters.calculate_volume_quality(volumes, closes, opens)
        liq_zones = find_liquidity_zones(highs, lows, 100)

        # ═══ v4.0: VOLUME PROFILE / HVN ════════════════════════════
        # "Entra en un HVN. Apunta al siguiente HVN." — @Tradewrite
        vp = build_volume_profile(
            closes[-VP_LOOKBACK:], highs[-VP_LOOKBACK:],
            lows[-VP_LOOKBACK:], volumes[-VP_LOOKBACK:],
            n_bins=VP_BINS
        )
        hvn_ctx = analyze_hvn_context(price, vp)

        # Bloquear entradas en LVN — el precio pasa rápido, sin soporte
        if hvn_ctx['in_lvn'] and HVN_ENTRY_FILTER:
            log.debug(f"{symbol}: ❌ En LVN — precio sin soporte volumétrico")
            return None

        # MA conditions
        above_ma10 = price > ma10
        above_ma20 = price > ma20
        ma10_above_ma20 = ma10 > ma20  # Tendencia alcista
        ma20_prev = sma(closes[:-5], 20) if len(closes) > 25 else ma20
        ma20_rising = ma20 > ma20_prev

        # ═══ PATTERN DETECTION (la clave de v3.0) ══════════════════
        vcp_detected, vcp_reason = detect_vcp(closes, volumes, VCP_LOOKBACK)
        flag_detected, flag_reason = detect_flag(closes, volumes, highs, lows)
        ema9_ok, ema9_reason = check_9ema_setup(closes, price)

        # ═══ SCORING — Estructura sobre indicadores ══════════════════
        score = 0
        reasons = []

        # MARKET REGIME (15 pts) — fundamental en v3.0
        if regime == "trending_bullish":
            score += 15
            reasons.append("Regime_Bullish(15)")
        elif regime == "trending_moderate":
            score += 8
            reasons.append("Regime_Moderate(8)")

        # ═══ v4.0: EMA CLOUD (15 pts) — @Tradewrite ═════════════════
        # "Inclinada hacia arriba = compradores en control"
        if cloud_up and cloud_slope_20 > 0.05:
            score += 15
            reasons.append(f"Cloud_StrongUp(slope={cloud_slope_20:.2f}%)(15)")
        elif cloud_up:
            score += 8
            reasons.append(f"Cloud_Up(slope={cloud_slope_20:.2f}%)(8)")

        # MA10/MA20 SETUP — @pheonix_trader core (25 pts)
        if above_ma10 and above_ma20 and ma10_above_ma20 and ma20_rising:
            score += 25
            reasons.append("MA10>MA20_Rising(25)")
        elif above_ma20 and ma20_rising:
            score += 15
            reasons.append("Above_MA20_Rising(15)")
        elif above_ma20:
            score += 8
            reasons.append("Above_MA20(8)")

        # VCP PATTERN (20 pts) — el patrón más poderoso de momentum
        if vcp_detected:
            score += 20
            reasons.append(f"VCP({vcp_reason})(20)")

        # FLAG PATTERN (15 pts)
        if flag_detected:
            score += 15
            reasons.append(f"Flag({flag_reason})(15)")

        # VOLUME BREAKOUT (15 pts) — "grandes compras = grandes movimientos"
        if vol_breakout:
            score += 15
            reasons.append(f"VolumeBreakout({vol_ratio:.1f}x)(15)")
        elif vol_ratio > 1.2:
            score += 7
            reasons.append(f"VolumeAboveAvg({vol_ratio:.1f}x)(7)")

        # ═══ v4.0: HVN SCORING (hasta 25 pts) ══════════════════════
        # "Ingresa al trade en la dirección de la nube, apunta al siguiente HVN"
        hvn_score = 0
        if hvn_ctx['in_hvn']:
            hvn_score += int(HVN_SCORE_BONUS)
            reasons.append(f"HVN_Entry({hvn_ctx['context']})(+{int(HVN_SCORE_BONUS)})")
        elif hvn_ctx['above_poc'] and hvn_ctx['in_value_area']:
            hvn_score += int(HVN_SCORE_BONUS * 0.5)
            reasons.append(f"ValueArea_Bullish(+{int(HVN_SCORE_BONUS*0.5)})")

        if hvn_ctx['hvn_next_up'] > price:
            # Hay un HVN arriba → existe un target claro
            hvn_score += 5
            reasons.append(f"HVN_Target@{hvn_ctx['hvn_next_up']:.4f}(+5)")

        if hvn_ctx['hvn_rr'] >= 1.5:
            # El RR natural HVN→HVN es bueno
            hvn_score += 5
            reasons.append(f"HVN_RR{hvn_ctx['hvn_rr']:.1f}x(+5)")
        elif hvn_ctx['hvn_rr'] < 0.8 and hvn_ctx['hvn_rr'] > 0:
            # RR pobre HVN→HVN → penalizar
            hvn_score -= 5
            reasons.append(f"HVN_RR_Low{hvn_ctx['hvn_rr']:.1f}x(-5)")

        if hvn_ctx['above_poc']:
            hvn_score += 3
            reasons.append("Above_POC(+3)")

        score += hvn_score

        # 9 EMA CONFIRMATION (10 pts) — ORB strategy
        if ema9_ok:
            score += 10 if ema9_reason == "ema9_fresh_cross" else 5
            reasons.append(f"{ema9_reason}({'10' if ema9_reason == 'ema9_fresh_cross' else '5'})")

        # CVD — Volume quality (10 pts)
        if cvd_signal == "bullish_cvd":
            score += 10
            reasons.append(f"CVD_Bullish(10)")
        elif cvd_signal == "cvd_neutral":
            score += 4
            reasons.append("CVD_Neutral(4)")

        # FUNDING (5 pts)
        if funding_rate < 0:
            score += 5
            reasons.append(f"Funding_Neg(5)")
        elif funding_rate < 0.02:
            score += 3
            reasons.append(f"Funding_Low(3)")

        # OI (5 pts)
        if oi_reason == "oi_breakout_confirmed":
            score += 5
            reasons.append(f"OI_Breakout(5)")

        # SESSION (5 pts)
        if session_name == "us_session":
            score += 5
            reasons.append("US_Session(5)")
        elif session_name == "london_session":
            score += 3
            reasons.append("London_Session(3)")

        # RSI dulce spot (5 pts)
        if 35 < rsi_val < 55:
            score += 5
            reasons.append(f"RSI_Sweet({int(rsi_val)})(5)")

        # ═══ STOP LOSS inteligente v4.0 — usa HVN anterior como SL ════
        sl_atr = price - (atr_val * SL_ATR_MULT)
        support_zones = liq_zones.get('support_zones', [])
        sl_support = next((s * 0.998 for s in support_zones if s < price), None)

        # v4.0: si hay HVN abajo → usarlo como SL natural (más preciso)
        # "Coloca tu stop en el HVN anterior" — @Tradewrite
        sl_hvn = None
        if hvn_ctx['hvn_prev_down'] > 0:
            sl_hvn = hvn_ctx['hvn_prev_down'] * 0.997  # Ligeramente debajo del HVN

        # Elegir el SL más conservador (más cercano al precio)
        candidates = [c for c in [sl_atr, sl_support, sl_hvn] if c and c > 0]
        sl_price = max(candidates) if candidates else sl_atr

        sl_pct = (price - sl_price) / price * 100
        sl_pct = max(SL_MIN_PCT, min(SL_MAX_PCT, sl_pct))
        sl_price = price * (1 - sl_pct / 100)

        # ═══ TAKE PROFITS v4.0 — usa siguiente HVN como TP1 ══════════
        # "Apunta al siguiente HVN" — @Tradewrite
        tp1_hvn = hvn_ctx.get('hvn_next_up', 0)
        tp1_standard = price * (1 + sl_pct * TP1_RR / 100)

        # Si hay HVN arriba y está en rango razonable → usarlo como TP1
        if tp1_hvn > price * 1.003 and tp1_hvn < price * 1.08:
            tp1_price = tp1_hvn
            reasons.append(f"TP1=HVN@{tp1_hvn:.4f}")
        else:
            tp1_price = tp1_standard

        tp2_price = price * (1 + sl_pct * TP2_RR / 100)

        # ═══ EDGE CALCULATION ════════════════════════════════════════
        potential_profit = (tp1_price - price) / price * 100
        edge_ratio = potential_profit / (TOTAL_COST * 100)

        if edge_ratio < MIN_EDGE:
            log.debug(f"{symbol}: Edge {edge_ratio:.1f}× < {MIN_EDGE}×")
            return None

        if score < MIN_SCORE:
            return None

        return {
            'symbol': symbol,
            'price': price,
            'score': score,
            'reasons': ' | '.join(reasons),
            'sl_price': sl_price,
            'sl_pct': sl_pct,
            'tp1_price': tp1_price,
            'tp2_price': tp2_price,
            'edge_ratio': edge_ratio,
            'atr': atr_val,
            'atr_pct': atr_pct,
            'ma10': ma10,
            'ma20': ma20,
            'ema9': ema9,
            'rsi': rsi_val,
            'regime': regime,
            'vcp': vcp_detected,
            'flag': flag_detected,
            'vol_ratio': vol_ratio,
            'cvd': cvd_val,
            'cvd_signal': cvd_signal,
            'funding_rate': funding_rate,
            'oi_change': oi_change,
            # v4.0 HVN data
            'hvn_in': hvn_ctx['in_hvn'],
            'hvn_context': hvn_ctx['context'],
            'hvn_next_up': hvn_ctx['hvn_next_up'],
            'hvn_prev_down': hvn_ctx['hvn_prev_down'],
            'hvn_rr': hvn_ctx['hvn_rr'],
            'poc': hvn_ctx.get('poc', 0),
            'va_high': hvn_ctx.get('va_high', 0),
            'va_low': hvn_ctx.get('va_low', 0),
            'above_poc': hvn_ctx['above_poc'],
            'session': session_name,
            'liq_zones': liq_zones
        }

    def open_position(self, signal: Dict) -> bool:
        if not AUTO_TRADING:
            return False

        symbol = signal['symbol']
        price = signal['price']
        sl_price = signal['sl_price']

        log.info(f"\n{'='*80}")
        log.info(f"🎯 OPENING LONG v4.0: {symbol}")
        log.info(f"Score: {int(signal['score'])} | Edge: {signal['edge_ratio']:.1f}× | Regime: {signal['regime']}")
        log.info(f"Patterns: VCP={'✓' if signal['vcp'] else '✗'} | Flag={'✓' if signal['flag'] else '✗'} | Vol:{signal['vol_ratio']:.1f}×")
        log.info(f"HVN: In={signal.get('hvn_in','?')} | Ctx={signal.get('hvn_context','?')} | NextUp={signal.get('hvn_next_up',0):.4f} | RR={signal.get('hvn_rr',0):.1f}×")
        log.info(f"POC={signal.get('poc',0):.4f} | AbovePOC={signal.get('above_poc','?')} | VA[{signal.get('va_low',0):.4f}-{signal.get('va_high',0):.4f}]")
        log.info(f"Entry: ${price:.6f} | SL: ${sl_price:.6f} (-{signal['sl_pct']:.2f}%)")
        log.info(f"TP1: ${signal['tp1_price']:.6f} | TP2: ${signal['tp2_price']:.6f}")
        log.info(f"MA10: ${signal['ma10']:.4f} | MA20: ${signal['ma20']:.4f} | EMA9: ${signal['ema9']:.4f}")
        log.info(f"{'='*80}\n")

        # Kelly position sizing dinámico
        total = self.stats['wins'] + self.stats['losses']
        if total >= 10 and self.stats['win_amounts'] and self.stats['loss_amounts']:
            wr = self.stats['wins'] / total
            avg_win = sum(self.stats['win_amounts'][-20:]) / len(self.stats['win_amounts'][-20:])
            avg_loss = abs(sum(self.stats['loss_amounts'][-20:]) / len(self.stats['loss_amounts'][-20:]))
            pos_size = kelly_position_size(wr, avg_win, avg_loss, self.equity)
            log.info(f"Kelly sizing: ${pos_size:.2f} (WR:{wr:.0%} Avg W:${avg_win:.2f} L:${avg_loss:.2f})")
        else:
            pos_size = POSITION_SIZE

        qty = self._calculate_quantity(symbol, price, sl_price, pos_size)
        if not qty:
            log.error(f"No se pudo calcular cantidad para {symbol}")
            return False

        self._set_leverage(symbol, LEVERAGE)
        time.sleep(0.3)

        order_data = api_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol': symbol, 'side': 'BUY', 'type': 'MARKET',
            'quantity': str(qty), 'positionSide': 'LONG'
        })

        if order_data.get('code') != 0:
            log.error(f"Error abriendo posición: {order_data.get('msg')}")
            return False

        time.sleep(1)
        fill_qty, fill_price = self._confirm_position(symbol)
        if not fill_qty:
            log.error("Posición no confirmada")
            return False

        real_sl_pct = (fill_price - sl_price) / fill_price * 100
        tp1_price = fill_price * (1 + real_sl_pct * TP1_RR / 100)
        tp2_price = fill_price * (1 + real_sl_pct * TP2_RR / 100)

        # Stop Loss STOP_MARKET
        sl_params = {
            'symbol': symbol, 'side': 'SELL', 'type': 'STOP_MARKET',
            'quantity': str(fill_qty), 'stopPrice': str(round(sl_price, 8)),
            'positionSide': 'LONG'
        }
        sl_result = api_request('POST', '/openApi/swap/v2/trade/order', sl_params)
        if sl_result.get('code') != 0:
            sl_params['type'] = 'STOP'
            sl_params['price'] = str(round(sl_price * 0.999, 8))
            sl_result = api_request('POST', '/openApi/swap/v2/trade/order', sl_params)
        sl_ok = sl_result.get('code') == 0

        self.positions[symbol] = {
            'entry': fill_price, 'qty': fill_qty,
            'qty_tp1': round(fill_qty * TP1_PCT / 100, 6),
            'qty_tp2': round(fill_qty * TP2_PCT / 100, 6),
            'side': 'LONG', 'sl_price': sl_price, 'sl_pct': real_sl_pct,
            'tp1_price': tp1_price, 'tp2_price': tp2_price,
            'tp1_hit': False, 'tp2_hit': False, 'highest': fill_price,
            'opened_at': datetime.now(), 'score': signal['score'],
            'signal': signal, 'pnl_realized': 0.0,
            'pos_size': pos_size
        }
        self.stats['total_trades'] += 1

        patterns = []
        if signal['vcp']: patterns.append("VCP✓")
        if signal['flag']: patterns.append("Flag✓")
        patterns_str = " ".join(patterns) if patterns else "No pattern"

        self._send_telegram(
            f"<b>🟢 LONG OPENED v3.0</b>\n\n"
            f"<b>{symbol}</b>\n"
            f"Score: {int(signal['score'])} | Edge: {signal['edge_ratio']:.1f}×\n"
            f"Regime: {signal['regime']}\n"
            f"Patterns: {patterns_str}\n"
            f"Volume: {signal['vol_ratio']:.1f}× avg\n\n"
            f"📍 Entry: ${fill_price:.6f}\n"
            f"🎯 TP1: ${tp1_price:.6f} {'← HVN' if signal.get('hvn_next_up') and abs(tp1_price - signal.get('hvn_next_up', 0)) < tp1_price * 0.002 else ''}\n"
            f"🎯 TP2: ${tp2_price:.6f}\n"
            f"🛑 SL: ${sl_price:.6f} (-{real_sl_pct:.2f}%) {'← HVN' if signal.get('hvn_prev_down') and abs(sl_price - signal.get('hvn_prev_down', 0)) < sl_price * 0.005 else ''}\n\n"
            f"📊 Volume Profile:\n"
            f"  POC: ${signal.get('poc', 0):.4f} {'✅ AbovePOC' if signal.get('above_poc') else '⚠️ BelowPOC'}\n"
            f"  HVN: {'✅ En HVN' if signal.get('hvn_in') else '⚪ Cerca HVN'} | Ctx: {signal.get('hvn_context','?')}\n"
            f"  HVN→HVN RR: {signal.get('hvn_rr', 0):.1f}×\n"
            f"📊 MA10: ${signal['ma10']:.4f} | MA20: ${signal['ma20']:.4f}\n"
            f"📊 Funding: {signal['funding_rate']:.3f}%\n"
            f"🕐 Session: {signal['session']}\n\n"
            f"{'✅ SL Placed' if sl_ok else '⚠️ SL Manual Required'}"
        )

        log.info(f"✓ Posición abierta: {symbol} @ ${fill_price:.6f}")
        return True

    def _calculate_quantity(self, symbol: str, price: float, sl_price: float,
                             pos_size: float = None) -> Optional[float]:
        if pos_size is None:
            pos_size = POSITION_SIZE
        contract = self.contracts_info.get(symbol, {})
        min_qty = contract.get('min_qty', 1)
        precision = contract.get('qty_precision', 2)
        contract_size = contract.get('contract_size', 1)
        price_per_contract = price * contract_size
        if price_per_contract <= 0:
            return None
        risk_pct = (price - sl_price) / price * 100
        risk_amount = self.equity * (RISK_PER_TRADE / 100)
        notional = min(risk_amount / (risk_pct / 100) if risk_pct > 0 else 0,
                       pos_size * LEVERAGE)
        qty = notional / price_per_contract
        qty = math.ceil(qty / min_qty) * min_qty
        qty = round(qty, precision)
        return qty if qty >= min_qty else None

    def _set_leverage(self, symbol: str, leverage: int):
        for side in ['LONG', 'SHORT']:
            try:
                api_request('POST', '/openApi/swap/v2/trade/leverage',
                            {'symbol': symbol, 'side': side, 'leverage': str(leverage)})
            except:
                pass

    def _confirm_position(self, symbol: str, timeout: int = 15) -> Tuple[Optional[float], Optional[float]]:
        for _ in range(timeout):
            data = api_request('GET', '/openApi/swap/v2/user/positions', {'symbol': symbol})
            for pos in data.get('data', []):
                amt = safe_float(pos.get('positionAmt', 0))
                side = str(pos.get('positionSide', '')).upper()
                if (side == 'LONG' or (side == 'BOTH' and amt > 0)) and abs(amt) > 0:
                    entry = safe_float(pos.get('avgPrice') or pos.get('entryPrice', 0))
                    return abs(amt), entry
            time.sleep(1)
        return None, None

    async def monitor_positions(self):
        for symbol in list(self.positions.keys()):
            try:
                pos = self.positions[symbol]
                ticker = self._get_ticker(symbol)
                if not ticker:
                    continue

                current_price = ticker['price']
                if current_price > pos['highest']:
                    pos['highest'] = current_price

                # TP1
                if not pos['tp1_hit'] and current_price >= pos.get('tp1_price', float('inf')):
                    self._close_partial(symbol, pos['qty_tp1'], current_price, "TP1")
                    pos['tp1_hit'] = True
                    pos['sl_price'] = pos['entry'] * 1.001
                    log.info(f"🔒 {symbol} SL → Breakeven")
                    continue

                # TP2
                if pos['tp1_hit'] and not pos['tp2_hit'] and current_price >= pos.get('tp2_price', float('inf')):
                    self._close_partial(symbol, pos['qty_tp2'], current_price, "TP2")
                    pos['tp2_hit'] = True
                    trail_dist = pos['signal'].get('atr', 0) * RUNNER_TRAIL
                    if trail_dist > 0:
                        pos['sl_price'] = max(pos['sl_price'], current_price - trail_dist)
                    log.info(f"🔒 {symbol} SL → Trail @ ${pos['sl_price']:.6f}")
                    continue

                # Runner trailing
                if pos['tp2_hit']:
                    trail_dist = pos['signal'].get('atr', 0) * RUNNER_TRAIL
                    if trail_dist > 0:
                        new_sl = current_price - trail_dist
                        if new_sl > pos['sl_price']:
                            pos['sl_price'] = new_sl

                # SL check
                if current_price <= pos['sl_price']:
                    self._close_position(symbol, current_price, "STOP_LOSS")

            except Exception as e:
                log.error(f"Error monitoring {symbol}: {e}")

    def _close_partial(self, symbol: str, qty: float, price: float, reason: str):
        if qty <= 0:
            return
        result = api_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol': symbol, 'side': 'SELL', 'type': 'MARKET',
            'quantity': str(qty), 'positionSide': 'LONG'
        })
        if result.get('code') != 0:
            log.error(f"Error cerrando parcial {symbol}: {result.get('msg')}")
            return
        pos = self.positions[symbol]
        pnl = self._calculate_pnl(pos['entry'], price, qty, symbol)
        pos['pnl_realized'] += pnl
        pos['qty'] -= qty
        self.stats['total_pnl'] += pnl
        self.daily_pnl += pnl
        log.info(f"💰 {reason} {symbol}: ${pnl:+.4f}")
        self._send_telegram(f"<b>💰 {reason}</b>\n\n{symbol}\nExit: ${price:.6f}\nPnL: ${pnl:+.4f}")

    def _close_position(self, symbol: str, price: float, reason: str):
        if symbol not in self.positions:
            return
        pos = self.positions[symbol]
        qty = pos['qty']
        if qty > 0:
            api_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol': symbol, 'side': 'SELL', 'type': 'MARKET',
                'quantity': str(qty), 'positionSide': 'LONG'
            })
        pnl_final = self._calculate_pnl(pos['entry'], price, qty, symbol)
        total_pnl = pos['pnl_realized'] + pnl_final
        win = total_pnl > 0
        if win:
            self.stats['wins'] += 1
            self.stats['win_amounts'].append(total_pnl)
        else:
            self.stats['losses'] += 1
            self.stats['loss_amounts'].append(total_pnl)
        self.stats['total_pnl'] += pnl_final
        self.daily_pnl += pnl_final
        total_trades = self.stats['wins'] + self.stats['losses']
        wr = (self.stats['wins'] / total_trades * 100) if total_trades > 0 else 0
        duration_min = int((datetime.now() - pos['opened_at']).total_seconds() / 60)
        log.info(f"{'✅' if win else '❌'} {reason} {symbol} | ${total_pnl:+.4f} | {duration_min}min | WR:{wr:.0f}%")
        self._send_telegram(
            f"<b>{'✅' if win else '❌'} CLOSED — {reason}</b>\n\n"
            f"<b>{symbol}</b>\n"
            f"Entry: ${pos['entry']:.6f} → Exit: ${price:.6f}\n"
            f"Duration: {duration_min}min\n\n"
            f"<b>PnL: ${total_pnl:+.4f}</b>\n"
            f"Win Rate: {wr:.0f}% ({self.stats['wins']}/{total_trades})"
        )
        del self.positions[symbol]

    def _calculate_pnl(self, entry: float, exit_price: float, qty: float, symbol: str = '') -> float:
        contract = self.contracts_info.get(symbol, {})
        contract_size = contract.get('contract_size', 1)
        notional = qty * entry * contract_size
        pnl_gross = (exit_price - entry) / entry * notional * LEVERAGE
        fees = notional * (FEE_TAKER + FEE_MAKER)
        return pnl_gross - fees

    def _check_circuit_breaker(self) -> bool:
        today = datetime.utcnow().date()
        if today != self.daily_date:
            self.daily_pnl = 0
            self.daily_date = today
            if self.circuit_breaker_active:
                self.circuit_breaker_active = False
                self.circuit_breaker_until = None
                log.info("🔓 Circuit Breaker RESET")
        if self.circuit_breaker_active:
            if self.circuit_breaker_until and datetime.utcnow() > self.circuit_breaker_until:
                self.circuit_breaker_active = False
                self.daily_pnl = 0
                log.info("🔓 Circuit Breaker OFF")
                return False
            return True
        threshold = self.equity * (CIRCUIT_BREAKER_PCT / 100)
        if self.daily_pnl < -threshold:
            self.circuit_breaker_active = True
            self.circuit_breaker_until = datetime.utcnow() + timedelta(hours=4)
            log.warning(f"🔒 CIRCUIT BREAKER | Loss: ${self.daily_pnl:.2f}")
            self._send_telegram(
                f"<b>🔒 CIRCUIT BREAKER</b>\nDaily Loss: ${self.daily_pnl:.2f}\nPaused: 4h"
            )
            return True
        return False

    def _send_telegram(self, message: str):
        if not TG_TOKEN or not TG_CHAT:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={'chat_id': TG_CHAT, 'text': message, 'parse_mode': 'HTML'},
                timeout=5
            )
        except:
            pass

    async def run(self):
        log.info("\n🚀 Institutional Bot v3.0 RUNNING — Phoenix Trader Edition\n")
        iteration = 0
        last_symbol_refresh = 0
        last_equity_update = 0

        while True:
            try:
                iteration += 1

                if time.time() - last_symbol_refresh > 600:
                    self._refresh_symbols()
                    last_symbol_refresh = time.time()

                if time.time() - last_equity_update > 1800:
                    if AUTO_TRADING:
                        data = api_request('GET', '/openApi/swap/v2/user/balance')
                        if data.get('code') == 0:
                            eq = extract_equity(data)
                            if eq > 0:
                                self.equity = eq
                    last_equity_update = time.time()

                if self._check_circuit_breaker():
                    await asyncio.sleep(SCAN_INTERVAL)
                    continue

                total_trades = self.stats['wins'] + self.stats['losses']
                wr = (self.stats['wins'] / total_trades * 100) if total_trades > 0 else 0

                log.info(f"\n{'='*80}")
                log.info(f"#{iteration} {datetime.now().strftime('%H:%M:%S')} UTC | Positions: {len(self.positions)}/{MAX_POSITIONS}")
                log.info(f"PnL: ${self.stats['total_pnl']:+.4f} | Today: ${self.daily_pnl:+.4f} | WR: {wr:.0f}% ({total_trades} trades)")
                log.info(f"{'='*80}\n")

                await self.monitor_positions()

                if len(self.positions) < MAX_POSITIONS:
                    log.info(f"Scanning {len(self.symbols)} symbols...")
                    signals_found = 0

                    for symbol in self.symbols:
                        if len(self.positions) >= MAX_POSITIONS:
                            break
                        signal = self.analyze_symbol(symbol)
                        if signal:
                            signals_found += 1
                            patterns = []
                            if signal['vcp']: patterns.append("VCP")
                            if signal['flag']: patterns.append("Flag")
                            pattern_str = "+".join(patterns) if patterns else "Momentum"
                            log.info(f"💡 {symbol} | Score:{int(signal['score'])} | Edge:{signal['edge_ratio']:.1f}× | {pattern_str} | Vol:{signal['vol_ratio']:.1f}× | {signal['regime']} | HVN:{signal.get('hvn_context','?')} RR:{signal.get('hvn_rr',0):.1f}×")
                            if self.open_position(signal):
                                await asyncio.sleep(2)

                    log.info(f"✓ Scan complete | Signals: {signals_found}")

                await asyncio.sleep(SCAN_INTERVAL)

            except KeyboardInterrupt:
                log.info("⏹️ Bot stopped")
                break
            except Exception as e:
                log.error(f"Error in main loop: {e}", exc_info=True)
                await asyncio.sleep(20)

# ════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════════════

async def main():
    bot = InstitutionalBot()
    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("👋 Bot v3.0 terminated")
