#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║          SUPERBOT v5.1 — Simons Quant Edition                              ║
║          BingX Futures · USDT Perps · Multi-Signal Ensemble                ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Filosofía: "No predecimos el futuro, explotamos patrones estadísticos."   ║
║  Inspirado en Renaissance Technologies / Medallion Fund                    ║
║                                                                              ║
║  SEÑALES:  Z-Score(Vol/Price/ATR) · Momentum · Mean-Reversion · CVD       ║
║  PATRONES: VCP · Flag · Absorción · AlphaX Impulse Bands                 ║
║  FILTROS:  MTF 15m · Beta BTC · Swing-SL · Regime · BTC Guard · Sesión   ║
║  RIESGO:   Kelly · Circuit Breaker · Trailing TP1+TP2 · Runner · CSV Log  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re, json
import statistics, traceback, csv
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN 1: CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

def _env(key: str, default, typ='str'):
    v = os.getenv(key, str(default)).strip().strip('"').strip("'")
    if typ == 'int':
        m = re.match(r'^-?\d+', v.replace(',', '.'))
        return int(m.group(0)) if m else int(default)
    if typ == 'float':
        m = re.match(r'^-?\d+\.?\d*', v.replace(',', '.'))
        return float(m.group(0)) if m else float(default)
    if typ == 'bool':
        return v.lower() in ('true', '1', 'yes')
    return v

# ── API ───────────────────────────────────────────────────────────────────────
API_KEY    = _env('BINGX_API_KEY', '')
API_SECRET = _env('BINGX_API_SECRET', '')
TG_TOKEN   = _env('TELEGRAM_BOT_TOKEN', '')
TG_CHAT    = _env('TELEGRAM_CHAT_ID', '')
BASE_URL   = "https://open-api.bingx.com"

# ── Modo ──────────────────────────────────────────────────────────────────────
AUTO_TRADING = _env('AUTO_TRADING_ENABLED', 'false', 'bool')  # false = PAPER

# ── Capital ───────────────────────────────────────────────────────────────────
POSITION_SIZE  = _env('POSITION_SIZE_USD', '10', 'float')
LEVERAGE       = min(_env('LEVERAGE', '3', 'int'), 5)
MAX_POSITIONS  = _env('MAX_POSITIONS', '3', 'int')
ACCOUNT_EQUITY = _env('ACCOUNT_EQUITY', '100', 'float')
RISK_PER_TRADE = _env('RISK_PCT_PER_TRADE', '1.0', 'float')

# ── SL / TP ───────────────────────────────────────────────────────────────────
SL_ATR_MULT  = _env('SL_ATR_MULTIPLIER', '1.5', 'float')
SL_MIN_PCT   = _env('SL_MIN_PCT', '0.8', 'float')
SL_MAX_PCT   = _env('SL_MAX_PCT', '2.5', 'float')
TP1_PCT      = _env('TP1_PERCENTAGE', '40', 'float')   # % qty en TP1
TP2_PCT      = _env('TP2_PERCENTAGE', '40', 'float')   # % qty en TP2
TP1_RR       = _env('TP1_RISK_REWARD', '1.5', 'float')
TP2_RR       = _env('TP2_RISK_REWARD', '2.5', 'float')
RUNNER_TRAIL = _env('RUNNER_TRAIL_ATR', '2.0', 'float')
MIN_EDGE     = _env('MIN_EDGE_RATIO', '3.5', 'float')
MAX_HOLD_H   = _env('MAX_HOLD_HOURS', '24', 'float')

# ── Señales Simons (Quant Thresholds) ─────────────────────────────────────────
Z_VOL_THRESHOLD    = _env('Z_VOL_THRESHOLD', '2.0', 'float')    # Z-score volumen
Z_PRICE_THRESHOLD  = _env('Z_PRICE_THRESHOLD', '1.5', 'float')  # Z-score precio
ABSORPTION_RANGE   = _env('ABSORPTION_RANGE_PCT', '0.08', 'float')  # % rango para absorción
MOM_PERIOD         = _env('MOMENTUM_PERIOD', '14', 'int')
MEAN_REV_PERIOD    = _env('MEAN_REVERSION_PERIOD', '30', 'int')
MIN_QUANT_SCORE    = _env('MIN_QUANT_SCORE', '65', 'float')

# ── Filtros ───────────────────────────────────────────────────────────────────
MIN_VOLUME_24H       = _env('MIN_VOLUME_24H', '2000000', 'float')
MAX_SYMBOLS          = _env('MAX_SYMBOLS', '40', 'int')
VOLUME_BREAKOUT_MULT = _env('VOLUME_BREAKOUT_MULT', '1.8', 'float')
REGIME_ATR_MIN_PCT   = _env('REGIME_ATR_MIN_PCT', '0.4', 'float')
REGIME_ATR_MAX_PCT   = _env('REGIME_ATR_MAX_PCT', '4.0', 'float')
VCP_LOOKBACK         = _env('VCP_LOOKBACK', '20', 'int')
MAX_CORR_LONGS       = _env('MAX_CORR_LONGS', '1', 'int')

# ── Funding / OI ──────────────────────────────────────────────────────────────
FUNDING_LONG_OK    = _env('FUNDING_LONG_OK', '0.03', 'float')
FUNDING_LONG_SKIP  = _env('FUNDING_LONG_SKIP', '0.06', 'float')
OI_BREAKOUT_MIN    = _env('OI_BREAKOUT_MIN', '1.5', 'float')
OI_WEAK_THRESHOLD  = _env('OI_WEAK_THRESHOLD', '0.5', 'float')

# ── Sesión ────────────────────────────────────────────────────────────────────
SESSION_BEST = {13, 14, 15, 16, 17, 18, 19, 20, 21, 22}  # Londres+NY UTC
SESSION_OK   = {7, 8, 9, 10, 11, 12}                       # Pre-Londres UTC

# ── CVD ───────────────────────────────────────────────────────────────────────
CVD_LOOKBACK  = _env('CVD_LOOKBACK_BARS', '20', 'int')
CVD_THRESHOLD = _env('CVD_THRESHOLD', '1.2', 'float')

# ── AlphaX Impulse Bands ──────────────────────────────────────────────────────
AX_TREND_LEN     = _env('AX_TREND_LEN',     '19',   'int')
AX_IMPULSE_LEN   = _env('AX_IMPULSE_LEN',   '5',    'int')
AX_DECAY_RATE    = _env('AX_DECAY_RATE',     '0.97', 'float')
AX_MAD_LEN       = _env('AX_MAD_LEN',       '20',   'int')
AX_BAND_MIN      = _env('AX_BAND_MIN',       '1.5',  'float')
AX_BAND_MAX      = _env('AX_BAND_MAX',       '2.2',  'float')
AX_WPR_FAST      = _env('AX_WPR_FAST',       '8',    'int')
AX_WPR_MED       = _env('AX_WPR_MED',        '21',   'int')
AX_WPR_SLOW      = _env('AX_WPR_SLOW',       '55',   'int')
AX_WPR_OS        = _env('AX_WPR_OS',        '-82',   'float')
AX_WPR_OB        = _env('AX_WPR_OB',        '-18',   'float')
AX_MIN_CONF      = _env('AX_MIN_CONFIDENCE', '25',   'float')
AX_REQUIRE_WPR   = _env('AX_REQUIRE_WPR',   'true',  'bool')
AX_ENABLED       = _env('AX_ENABLED',        'true',  'bool')

# ── Multi-Timeframe Confirmation ──────────────────────────────────────────────
MTF_ENABLED      = _env('MTF_ENABLED',       'true',  'bool')  # Confirmar con 15m
MTF_REQUIRE_BULL = _env('MTF_REQUIRE_BULL',  'true',  'bool')  # Requerir momentum alcista en 15m
MTF_RSI_MIN      = _env('MTF_RSI_MIN',       '40',   'float')  # RSI mínimo en 15m (no sobrecomprado)
MTF_RSI_MAX      = _env('MTF_RSI_MAX',       '72',   'float')  # RSI máximo en 15m (no sobreextendido)

# ── Beta Filter (vs BTC) ──────────────────────────────────────────────────────
BETA_FILTER      = _env('BETA_FILTER',      'true',  'bool')   # Filtrar por beta
BETA_MIN         = _env('BETA_MIN',          '0.8',  'float')  # Beta mínima vs BTC
BETA_MAX         = _env('BETA_MAX',          '3.5',  'float')  # Beta máxima (evitar too volatile)
BETA_LOOKBACK    = _env('BETA_LOOKBACK',     '48',   'int')    # Horas de lookback para beta

# ── Structural SL (swing-low) ─────────────────────────────────────────────────
STRUCT_SL        = _env('STRUCT_SL_ENABLED', 'true',  'bool')  # SL estructural vs ATR
STRUCT_SL_BARS   = _env('STRUCT_SL_LOOKBACK','20',    'int')   # Barras atrás para swing low
STRUCT_SL_BUFFER = _env('STRUCT_SL_BUFFER',  '0.15',  'float') # % buffer debajo del swing

# ── Pattern Guard ─────────────────────────────────────────────────────────────
MIN_PATTERNS     = _env('MIN_PATTERNS',       '1',    'int')   # Mínimo patrones (VCP/Flag/Abs/AX)
TRAILING_FROM_TP1= _env('TRAILING_FROM_TP1', 'true',  'bool') # Trailing activo desde TP1 (no solo TP2)
TRAIL_TP1_MULT   = _env('TRAIL_TP1_ATR_MULT','1.0',  'float') # ATR mult para trailing desde TP1

# ── Trade Log CSV ─────────────────────────────────────────────────────────────
TRADE_LOG_CSV    = _env('TRADE_LOG_CSV', '/app/trades.csv', 'str')  # Path CSV de trades

# ── Circuit Breaker ───────────────────────────────────────────────────────────
CIRCUIT_BREAKER_PCT = _env('CIRCUIT_BREAKER_PCT', '3.0', 'float')
MAX_LOSING_STREAK   = _env('MAX_LOSING_STREAK', '3', 'int')
MAX_DAILY_TRADES    = _env('MAX_DAILY_TRADES', '10', 'int')

# ── Timing ────────────────────────────────────────────────────────────────────
SCAN_INTERVAL    = _env('SCAN_INTERVAL_SEC', '90', 'int')
MONITOR_INTERVAL = _env('MONITOR_INTERVAL_SEC', '20', 'int')
SYMBOL_COOLDOWN  = _env('SYMBOL_COOLDOWN_MIN', '60', 'int')

# ── Costes ────────────────────────────────────────────────────────────────────
FEE_TAKER  = 0.001
FEE_MAKER  = 0.0002
SLIPPAGE   = 0.0003
TOTAL_COST = FEE_TAKER + FEE_MAKER + SLIPPAGE

EXCLUDE_SYMBOLS = {
    'DOW', 'SP500', 'GOLD', 'SILVER', 'XAU', 'OIL', 'BRENT',
    'EUR', 'GBP', 'JPY', 'TSLA', 'AAPL', 'MSFT', 'GOOGL',
    'AMZN', 'META', 'NVDA', 'COIN', 'MSTR', 'PAXG', 'XAUT',
    'Q-USDT', 'BEAT-USDT',
}

# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN 2: LOGGING
# ══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/tmp/superbot_v5.log', mode='a'),
    ]
)
log = logging.getLogger('superbot_v5')

# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN 3: UTILIDADES API
# ══════════════════════════════════════════════════════════════════════════════

def safe_float(val, default: float = 0.0) -> float:
    try:
        if val is None or val == '':
            return default
        return float(val)
    except (ValueError, TypeError):
        return default

def api_request(method: str, endpoint: str, params: dict = None, retries: int = 3) -> dict:
    params = params or {}
    last_error = None
    for attempt in range(retries + 1):
        try:
            p = {**{k: str(v) for k, v in params.items()},
                 'timestamp': str(int(time.time() * 1000))}
            query = urlencode(sorted(p.items()))
            sig   = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
            url   = f"{BASE_URL}{endpoint}?{query}&signature={sig}"
            hdrs  = {'X-BX-APIKEY': API_KEY, 'Content-Type': 'application/x-www-form-urlencoded'}
            resp  = getattr(requests, method.lower())(url, headers=hdrs, timeout=15)
            data  = resp.json()
            if data.get('code') not in (0, None):
                log.warning(f"API [{endpoint}] code={data.get('code')} msg={data.get('msg')}")
            return data
        except requests.exceptions.Timeout as e:
            last_error = f"Timeout: {e}"
        except Exception as e:
            last_error = f"Exception: {e}"
            log.error(f"API [{endpoint}] attempt {attempt}: {e}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    log.error(f"API [{endpoint}] FAILED after {retries+1} attempts: {last_error}")
    return {'code': -1, 'msg': last_error}

def public_request(path: str, params: dict = None) -> dict:
    try:
        resp = requests.get(f"{BASE_URL}{path}", params=params or {}, timeout=10)
        return resp.json()
    except Exception as e:
        log.error(f"Public [{path}] error: {e}")
        return {'code': -1, 'msg': str(e)}

def extract_equity(data: dict) -> float:
    if data.get('code') != 0:
        return 0.0
    raw = data.get('data', {})
    if isinstance(raw, dict):
        inner = raw.get('balance', raw)
        if isinstance(inner, dict):
            return safe_float(inner.get('equity') or inner.get('availableMargin') or 0)
        return safe_float(raw.get('equity') or raw.get('balance') or 0)
    return 0.0

# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN 4: MOTOR CUANTITATIVO — ESTRATEGIA SIMONS
# ══════════════════════════════════════════════════════════════════════════════

class QuantEngine:
    """
    Motor de señales inspirado en Renaissance Technologies.
    Principio: explotar anomalías estadísticas repetibles y predecibles.
    No predicciones — probabilidades y exploración de patrones.
    """

    # ── Z-Score de Volumen (La Huella de la Ballena) ──────────────────────────
    @staticmethod
    def z_score_volume(volumes: List[float], period: int = 30) -> float:
        """Z-score del volumen actual vs ventana rodante. >2.0 = evento inusual."""
        if len(volumes) < period + 1:
            return 0.0
        window = volumes[-period-1:-1]
        mean   = sum(window) / len(window)
        var    = sum((v - mean) ** 2 for v in window) / len(window)
        std    = math.sqrt(var) if var > 0 else 1e-10
        return (volumes[-1] - mean) / std

    # ── Z-Score de Precio (Momentum Estadístico) ──────────────────────────────
    @staticmethod
    def z_score_price(closes: List[float], period: int = 20) -> float:
        """Z-score del precio. Positivo=momentum alcista, negativo=presión bajista."""
        if len(closes) < period + 1:
            return 0.0
        window = closes[-period-1:-1]
        mean   = sum(window) / len(window)
        var    = sum((c - mean) ** 2 for c in window) / len(window)
        std    = math.sqrt(var) if var > 0 else 1e-10
        return (closes[-1] - mean) / std

    # ── Absorción Institucional ───────────────────────────────────────────────
    @staticmethod
    def detect_absorption(volumes: List[float], highs: List[float],
                          lows: List[float], opens: List[float],
                          z_vol: float,
                          closes: List[float] = None) -> Tuple[bool, str]:
        """
        Absorción: volumen anómalo + rango estrecho = manos fuertes acumulando.
        Señal clásica de Wyckoff / Simons Order-Flow.
        """
        if len(volumes) < 5:
            return False, "insufficient"
        if opens[-1] <= 0:
            return False, "invalid_open"
        last_range_pct = (highs[-1] - lows[-1]) / opens[-1]
        body_pct = (abs(closes[-1] - opens[-1]) / opens[-1]
                    if closes is not None and len(closes) > 0
                    else last_range_pct)
        # Absorción: Z >2.5, rango estrecho Y cuerpo pequeño (doji/spinning top)
        # ABSORPTION_RANGE está en fracción (ej: 0.08 = 0.08%), convertir correctamente
        threshold = ABSORPTION_RANGE / 100  # 0.08 / 100 = 0.0008
        if z_vol > 2.5 and last_range_pct < threshold and body_pct < threshold * 1.5:
            return True, f"absorption_z{z_vol:.1f}"
        return False, "no_absorption"

    # ── Momentum Multi-Período ────────────────────────────────────────────────
    @staticmethod
    def momentum_signal(closes: List[float]) -> Tuple[float, str]:
        """
        Momentum en 3 períodos: corto (5), medio (14), largo (21).
        Retorna score normalizado [-1, 1] y etiqueta.
        """
        if len(closes) < 22:
            return 0.0, "insufficient"
        m5  = (closes[-1] - closes[-6])  / closes[-6]  if closes[-6]  > 0 else 0
        m14 = (closes[-1] - closes[-15]) / closes[-15] if closes[-15] > 0 else 0
        m21 = (closes[-1] - closes[-22]) / closes[-22] if closes[-22] > 0 else 0
        # Alineación de momentum: todos positivos = señal fuerte
        score = (m5 * 0.5 + m14 * 0.3 + m21 * 0.2)
        label = "mom_strong" if score > 0.01 else ("mom_weak" if score > 0 else "mom_negative")
        return score, label

    # ── Mean Reversion Score ──────────────────────────────────────────────────
    @staticmethod
    def mean_reversion_score(closes: List[float], period: int = 30) -> Tuple[float, str]:
        """
        ¿Cuántas desviaciones estándar está el precio de su media?
        Simons usaba este principio: precio bajo vs media histórica = rebote probable.
        """
        if len(closes) < period:
            return 0.0, "insufficient"
        window = closes[-period:]
        mean   = sum(window) / period
        std    = statistics.stdev(window) if period > 1 else 1e-10
        z      = (closes[-1] - mean) / std
        # Para LONG: queremos que el precio sea positivo pero no extremo
        # z entre -0.5 y +1.5 = zona de rebote / continuación
        if -0.5 <= z <= 1.5:
            return 1.0 - abs(z) / 2, "mean_rev_zone"
        elif z > 1.5:
            return 0.0, "overextended"
        else:
            return 0.0, "below_mean"

    # ── ATR Normalizado (Volatility Regime) ───────────────────────────────────
    @staticmethod
    def volatility_regime(highs: List[float], lows: List[float],
                          closes: List[float]) -> Tuple[str, float]:
        """Clasifica el régimen de volatilidad para ajustar señales."""
        if len(closes) < 15:
            return "unknown", 0.0
        atr_v = QuantEngine.atr(highs, lows, closes, 14)
        atr_p = (atr_v / closes[-1] * 100) if closes[-1] > 0 else 0
        if atr_p > REGIME_ATR_MAX_PCT:    return "volatile_extreme",  atr_p
        if atr_p < REGIME_ATR_MIN_PCT:    return "ranging_quiet",     atr_p
        ma10  = sum(closes[-10:]) / 10
        ma20  = sum(closes[-20:]) / 20 if len(closes) >= 20 else ma10
        ma20p = sum(closes[-25:-5]) / 20 if len(closes) >= 25 else ma20
        rising = ma20 > ma20p
        cur    = closes[-1]
        if cur > ma10 and cur > ma20 and ma10 > ma20 and rising:
            return "trending_bullish",  atr_p
        if cur > ma20 and rising:
            return "trending_moderate", atr_p
        if not rising and cur < ma20:
            return "bearish",           atr_p
        return "ranging", atr_p

    # ── Indicadores Clásicos ──────────────────────────────────────────────────
    @staticmethod
    def ema(prices: List[float], period: int) -> float:
        if not prices:
            return 0.0
        k = 2 / (period + 1)
        val = prices[0]
        for p in prices[1:]:
            val = p * k + val * (1 - k)
        return val

    @staticmethod
    def atr(highs: List[float], lows: List[float], closes: List[float],
            period: int = 14) -> float:
        if len(closes) < 2:
            return 0.0
        trs = []
        for i in range(1, min(len(closes), period + 1)):
            trs.append(max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            ))
        return sum(trs) / len(trs) if trs else 0.0

    @staticmethod
    def rsi(prices: List[float], period: int = 14) -> float:
        if len(prices) < period + 1:
            return 50.0
        gains  = [max(prices[i] - prices[i-1], 0) for i in range(1, len(prices))]
        losses = [max(prices[i-1] - prices[i], 0) for i in range(1, len(prices))]
        ag = sum(gains[-period:]) / period
        al = sum(losses[-period:]) / period
        return 100 - (100 / (1 + ag / al)) if al > 0 else 100.0

    @staticmethod
    def cvd(volumes: List[float], closes: List[float],
            opens: List[float]) -> Tuple[float, str]:
        """Cumulative Volume Delta: presión compradora vs vendedora."""
        if len(volumes) < CVD_LOOKBACK:
            return 0.0, "insufficient"
        rv = volumes[-CVD_LOOKBACK:]
        rc = closes[-CVD_LOOKBACK:]
        ro = opens[-CVD_LOOKBACK:]
        raw = sum(rv[i] * (1 if rc[i] > ro[i] else -1 if rc[i] < ro[i] else 0)
                  for i in range(len(rv)))
        total = sum(rv) or 1
        cvd_n = raw / total
        bars  = [rv[i] * (1 if rc[i] > ro[i] else -1) for i in range(len(rv))]
        try:
            std = statistics.stdev(bars) if len(bars) > 1 else 1
            if abs(cvd_n) * total > CVD_THRESHOLD * std:
                return cvd_n, "bullish_cvd" if cvd_n > 0 else "bearish_cvd"
        except Exception:
            pass
        return cvd_n, "cvd_neutral"

    # ── VCP (Volatility Contraction Pattern) ──────────────────────────────────
    @staticmethod
    def detect_vcp(closes: List[float], volumes: List[float],
                   lookback: int = 20) -> Tuple[bool, str]:
        if len(closes) < lookback:
            return False, "insufficient"
        rc = closes[-lookback:]
        rv = volumes[-lookback:]
        contractions = []
        for i in range(2, len(rc) - 2):
            if rc[i] < rc[i-1] and rc[i] < rc[i+1]:
                depth = (rc[i-1] - rc[i]) / rc[i-1] * 100
                contractions.append((depth, rv[i]))
        if len(contractions) < 2:
            return False, "no_contractions"
        depths = [c[0] for c in contractions[-3:]]
        contracting = all(depths[j] < depths[j-1] * 1.15 for j in range(1, len(depths)))
        near_high   = rc[-1] >= max(rc) * 0.90
        return (True, f"vcp_{len(contractions)}c") if contracting and near_high \
               else (False, "no_vcp")

    # ── Bull Flag ─────────────────────────────────────────────────────────────
    @staticmethod
    def detect_flag(closes: List[float], volumes: List[float],
                    highs: List[float], lows: List[float]) -> Tuple[bool, str]:
        if len(closes) < 15:
            return False, "insufficient"
        pole_w, flag_w = 7, 5
        pc = closes[-(pole_w + flag_w):-flag_w]
        fc = closes[-flag_w:]
        fv = volumes[-flag_w:]
        pv = volumes[-(pole_w + flag_w):-flag_w]
        pole_move  = (pc[-1] - pc[0]) / pc[0] * 100
        flag_range = (max(fc) - min(fc)) / min(fc) * 100
        vol_ratio  = (sum(fv)/len(fv)) / (sum(pv)/len(pv)) if pv else 1
        retrace    = (pc[-1] - min(fc)) / (pc[-1] - pc[0] + 1e-10) * 100
        return (True, f"flag_{pole_move:.1f}pct") \
               if pole_move > 3.5 and flag_range < 4.0 and vol_ratio < 0.85 and retrace < 55 \
               else (False, "no_flag")

    # ── Ensemble Score (núcleo Simons) ────────────────────────────────────────
    @staticmethod
    def ensemble_score(z_vol: float, z_price: float, mom_score: float,
                       mr_score: float, cvd_val: float) -> float:
        """
        Combina señales independientes con pesos calibrados.
        Principio Simons: ninguna señal sola; el ensemble decide.
        Retorna score 0-100.
        """
        # Normalizar cada señal a [0, 1]
        z_vol_n   = min(max(z_vol / 4.0, 0), 1)        # Z-vol: 0..4 → 0..1
        z_price_n = min(max(z_price / 3.0, 0), 1)      # Z-price: 0..3 → 0..1
        mom_n     = min(max(mom_score * 50, 0), 1)      # mom: 0..0.02 → 0..1
        mr_n      = min(max(mr_score, 0), 1)            # ya está en 0..1
        cvd_n     = min(max(cvd_val * 5, 0), 1) if cvd_val > 0 else 0  # CVD positivo

        # Pesos del ensemble (suman 1.0)
        weights = {
            'z_vol':   0.25,   # Volumen inusual (la huella)
            'z_price': 0.20,   # Momentum de precio
            'mom':     0.20,   # Momentum multi-período
            'mr':      0.15,   # Zona de mean-reversion
            'cvd':     0.20,   # Presión compradora
        }
        raw = (z_vol_n   * weights['z_vol']   +
               z_price_n * weights['z_price'] +
               mom_n     * weights['mom']     +
               mr_n      * weights['mr']      +
               cvd_n     * weights['cvd'])
        return round(raw * 100, 1)

    # ── Swing-Low Structural SL ───────────────────────────────────────────────
    @staticmethod
    def swing_low_sl(lows: List[float], closes: List[float],
                     lookback: int = 20, buffer_pct: float = 0.15) -> Optional[float]:
        """
        Encuentra el swing-low más reciente en los últimos `lookback` barras.
        Coloca el SL `buffer_pct`% debajo de ese mínimo.
        Más resistente a stop-hunting que el SL basado en ATR.
        Retorna None si no hay swing válido.
        """
        if len(lows) < lookback + 2:
            return None
        window = lows[-(lookback + 1):-1]   # excluir la vela actual
        sl_raw = min(window)
        sl     = sl_raw * (1 - buffer_pct / 100)
        # Guard: el SL no puede estar a más del SL_MAX_PCT del cierre actual
        if closes[-1] > 0 and (closes[-1] - sl) / closes[-1] * 100 > SL_MAX_PCT:
            return None   # swing demasiado lejano → usar ATR
        if closes[-1] > 0 and (closes[-1] - sl) / closes[-1] * 100 < SL_MIN_PCT:
            return None   # swing demasiado cercano → usar ATR
        return sl

    # ── Beta vs BTC ───────────────────────────────────────────────────────────
    @staticmethod
    def beta_vs_btc(sym_closes: List[float], btc_closes: List[float],
                    lookback: int = 48) -> float:
        """
        Calcula la beta del símbolo respecto a BTC usando retornos logarítmicos.
        Beta > 1 = se mueve más que BTC (amplificador de tendencia).
        """
        if len(sym_closes) < lookback + 1 or len(btc_closes) < lookback + 1:
            return 1.0
        sc  = sym_closes[-(lookback + 1):]
        bc  = btc_closes[-(lookback + 1):]
        sym_ret = [math.log(sc[i] / sc[i-1]) for i in range(1, len(sc)) if sc[i-1] > 0]
        btc_ret = [math.log(bc[i] / bc[i-1]) for i in range(1, len(bc)) if bc[i-1] > 0]
        n = min(len(sym_ret), len(btc_ret))
        if n < 10:
            return 1.0
        sym_ret, btc_ret = sym_ret[-n:], btc_ret[-n:]
        mean_b = sum(btc_ret) / n
        mean_s = sum(sym_ret) / n
        cov    = sum((btc_ret[i] - mean_b) * (sym_ret[i] - mean_s) for i in range(n)) / n
        var_b  = sum((r - mean_b) ** 2 for r in btc_ret) / n
        return cov / var_b if var_b > 1e-12 else 1.0

    # ── MTF (Multi-TimeFrame) Confirmation ────────────────────────────────────
    @staticmethod
    def mtf_confirm(closes_15m: List[float], highs_15m: List[float],
                    lows_15m:  List[float]) -> Tuple[bool, str]:
        """
        Confirmación de tendencia en 15m para señales generadas en 5m.
        Requiere: precio > EMA20(15m) + RSI entre 40-72 + momentum positivo.
        Retorna (ok, reason).
        """
        if len(closes_15m) < 22:
            return True, "insufficient_15m"   # sin datos → no bloquear
        ema20 = QuantEngine.ema(closes_15m, 20)
        ema9  = QuantEngine.ema(closes_15m, 9)
        rsi   = QuantEngine.rsi(closes_15m, 14)
        mom   = (closes_15m[-1] - closes_15m[-6]) / closes_15m[-6] * 100 if closes_15m[-6] > 0 else 0
        price = closes_15m[-1]

        if rsi > MTF_RSI_MAX:
            return False, f"15m_RSI_overbought({rsi:.0f})"
        if rsi < MTF_RSI_MIN:
            return False, f"15m_RSI_weak({rsi:.0f})"
        if MTF_REQUIRE_BULL:
            if price < ema20:
                return False, f"15m_below_EMA20"
            if mom < -0.3:
                return False, f"15m_mom_neg({mom:.2f}%)"
        return True, f"15m_ok(RSI={rsi:.0f},mom={mom:.2f}%)"

    # ── Kelly Sizing ──────────────────────────────────────────────────────────
    @staticmethod
    def kelly_size(win_rate: float, avg_win: float, avg_loss: float,
                   equity: float) -> float:
        if avg_loss <= 0 or win_rate <= 0:
            return POSITION_SIZE
        R      = avg_win / avg_loss
        kelly  = win_rate - (1 - win_rate) / R
        half   = max(0.005, min(0.02, kelly / 2))
        return min(equity * half, POSITION_SIZE)


# Alias de conveniencia
Q = QuantEngine


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN 4b: ALPHAX IMPULSE BANDS — Adaptive Trend Engine
# Portado de Pine Script a Python. Lógica 1:1 con el indicador original.
# ══════════════════════════════════════════════════════════════════════════════

class AlphaXEngine:
    """
    Motor AlphaX Impulse Bands.
    Combina bandas adaptativas MAD + impulso ATR-normalizado + WPR multi-período.
    Genera un confidence score 0-100 para señales BULL / BEAR.
    """

    # ── Helpers estadísticos ─────────────────────────────────────────────────
    @staticmethod
    def _ema(prices: List[float], period: int) -> List[float]:
        """EMA completa sobre una serie, devuelve lista del mismo tamaño."""
        if not prices or period <= 0:
            return prices
        k   = 2 / (period + 1)
        out = [prices[0]]
        for p in prices[1:]:
            out.append(p * k + out[-1] * (1 - k))
        return out

    @staticmethod
    def _sma(prices: List[float], period: int) -> List[float]:
        """SMA rolling completa."""
        out = []
        for i in range(len(prices)):
            if i < period - 1:
                out.append(sum(prices[:i+1]) / (i + 1))
            else:
                out.append(sum(prices[i-period+1:i+1]) / period)
        return out

    @staticmethod
    def _atr_series(highs: List[float], lows: List[float],
                    closes: List[float], period: int = 14) -> List[float]:
        """ATR rolling completo."""
        trs  = [highs[0] - lows[0]]
        for i in range(1, len(closes)):
            trs.append(max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i]  - closes[i-1]),
            ))
        # Wilder smoothing (EMA con k=1/period)
        k   = 1 / period
        atr = [trs[0]]
        for tr in trs[1:]:
            atr.append(tr * k + atr[-1] * (1 - k))
        return atr

    @staticmethod
    def _wpr(highs: List[float], lows: List[float],
             closes: List[float], period: int) -> List[float]:
        """Williams %R rolling completo. Rango [-100, 0]."""
        out = []
        for i in range(len(closes)):
            start = max(0, i - period + 1)
            h = max(highs[start:i+1])
            l = min(lows[start:i+1])
            if h == l:
                out.append(-50.0)
            else:
                out.append((closes[i] - h) / (h - l) * 100)
        return out

    # ── Cálculo principal ─────────────────────────────────────────────────────
    @classmethod
    def compute(cls,
                closes:  List[float],
                highs:   List[float],
                lows:    List[float],
                opens:   List[float],
                volumes: List[float]) -> dict:
        """
        Calcula todas las señales AlphaX Impulse Bands sobre las velas dadas.
        Devuelve dict con el estado en la última barra.
        """
        n = len(closes)
        if n < max(AX_TREND_LEN, AX_MAD_LEN, AX_WPR_SLOW, AX_IMPULSE_LEN) + 5:
            return {'valid': False}

        # ── Basis EMAs ────────────────────────────────────────────────────────
        basis_c = cls._ema(closes, AX_TREND_LEN)
        atr_ser = cls._atr_series(highs, lows, closes, 14)

        # ── MAD (Mean Absolute Deviation) ─────────────────────────────────────
        mean_c = cls._sma(closes, AX_MAD_LEN)
        abs_dev = [abs(closes[i] - mean_c[i]) for i in range(n)]
        mad    = cls._sma(abs_dev, AX_MAD_LEN)

        # ── Impulso normalizado por ATR (stateful decay) ───────────────────────
        impulse     = 0.0
        impulse_dir = 0
        freshness_s = []
        imp_thresh  = 1.0
        for i in range(n):
            if i < AX_IMPULSE_LEN:
                freshness_s.append(0.0)
                continue
            raw = (atr_ser[i] > 0
                   and (closes[i] - closes[i - AX_IMPULSE_LEN]) / atr_ser[i]
                   or 0.0)
            if abs(raw) > imp_thresh:
                impulse     = abs(raw)
                impulse_dir = 1 if raw > 0 else -1
            else:
                impulse *= AX_DECAY_RATE
            freshness_s.append(min(impulse / 2.5, 1.0))

        # Values at last bar
        freshness    = freshness_s[-1]
        # Recompute impulse_dir at last bar (stateful — use last 5 bars)
        last_raw = (atr_ser[-1] > 0
                    and (closes[-1] - closes[-1 - AX_IMPULSE_LEN]) / atr_ser[-1]
                    or 0.0)
        impulse_dir  = 1 if last_raw > imp_thresh else (-1 if last_raw < -imp_thresh else impulse_dir)

        # impulse momentum (ROC of freshness)
        imp_momentum = (freshness_s[-1] - freshness_s[-4]) if n >= 4 else 0.0

        # ── Adaptive bands ────────────────────────────────────────────────────
        band_mult  = AX_BAND_MAX - (AX_BAND_MAX - AX_BAND_MIN) * freshness
        band_upper = basis_c[-1] + mad[-1] * band_mult
        band_lower = basis_c[-1] - mad[-1] * band_mult

        # ── Trend direction (crossover logic) ─────────────────────────────────
        trend_dir = 0
        for i in range(1, n):
            bu = basis_c[i] + mad[i] * (AX_BAND_MAX - (AX_BAND_MAX - AX_BAND_MIN) * freshness_s[i])
            bl = basis_c[i] - mad[i] * (AX_BAND_MAX - (AX_BAND_MAX - AX_BAND_MIN) * freshness_s[i])
            if closes[i] > bu:
                trend_dir = 1
            elif closes[i] < bl:
                trend_dir = -1
        # Flip detection: compare last two
        prev_trend = 0
        for i in range(1, n - 1):
            bu = basis_c[i] + mad[i] * AX_BAND_MAX
            bl = basis_c[i] - mad[i] * AX_BAND_MAX
            if closes[i] > bu:
                prev_trend = 1
            elif closes[i] < bl:
                prev_trend = -1
        trend_flip_up   = trend_dir == 1  and prev_trend == -1
        trend_flip_down = trend_dir == -1 and prev_trend == 1

        # ── WPR multi-período ─────────────────────────────────────────────────
        wpr_fast_s = cls._wpr(highs, lows, closes, AX_WPR_FAST)
        wpr_med_s  = cls._wpr(highs, lows, closes, AX_WPR_MED)
        wpr_slow_s = cls._wpr(highs, lows, closes, AX_WPR_SLOW)

        wf = wpr_fast_s[-1];  wf1 = wpr_fast_s[-2] if n >= 2 else wf
        wm = wpr_med_s[-1]
        ws = wpr_slow_s[-1]

        wpr_fast_os      = wf < AX_WPR_OS
        wpr_fast_ob      = wf > AX_WPR_OB
        wpr_med_os       = wm < AX_WPR_OS
        wpr_med_ob       = wm > AX_WPR_OB
        wpr_fast_cross_up   = wf  > AX_WPR_OS and wf1 <= AX_WPR_OS
        wpr_fast_cross_down = wf  < AX_WPR_OB and wf1 >= AX_WPR_OB
        wpr_fast_vel        = wf  - wf1
        wpr_fast_accel      = wpr_fast_vel - (wpr_fast_s[-2] - wpr_fast_s[-3]
                                               if n >= 3 else 0.0)
        wpr_bull_align      = ws > -65 and wm > -60
        wpr_bear_align      = ws < -35 and wm < -40
        wpr_bull_recovery   = (wf > wpr_fast_s[-2] and wf > wpr_fast_s[-3]
                                if n >= 3 else False)
        wpr_bear_recovery   = (wf < wpr_fast_s[-2] and wf < wpr_fast_s[-3]
                                if n >= 3 else False)
        wpr_triple_os       = wf < AX_WPR_OS and wm < -70 and ws < -60
        wpr_triple_ob       = wf > AX_WPR_OB and wm > -30 and ws > -40

        # WPR confirmation gate
        wpr_bull_ok = (not AX_REQUIRE_WPR or
                       wpr_fast_os or wpr_fast_cross_up or
                       wpr_bull_recovery or wpr_bull_align)
        wpr_bear_ok = (not AX_REQUIRE_WPR or
                       wpr_fast_ob or wpr_fast_cross_down or
                       wpr_bear_recovery or wpr_bear_align)

        # ── Volume & candle context ───────────────────────────────────────────
        vol_sma   = cls._sma(volumes, 20)
        vol_ratio = volumes[-1] / vol_sma[-1] if vol_sma[-1] > 0 else 1.0
        candle_body  = abs(closes[-1] - opens[-1])
        candle_range = highs[-1] - lows[-1]
        body_ratio   = candle_body / candle_range if candle_range > 0 else 0.0

        # ── Confidence scoring ────────────────────────────────────────────────
        def bull_confidence() -> float:
            c = 0.0
            c += 25.0 if trend_flip_up else 0.0
            c += (20.0 if freshness > 0.8 else 15.0 if freshness > 0.6 else
                  10.0 if freshness > 0.4 else 5.0  if freshness > 0.2 else 0.0)
            c += 10.0 if impulse_dir == 1 else 0.0
            c += (15.0 if wf < -95 else 12.0 if wf < -90 else
                  9.0  if wf < -85 else 5.0  if wf < -80  else 0.0)
            c += (10.0 if wpr_fast_cross_up   else
                  7.0  if wpr_fast_vel > 3    else
                  4.0  if wpr_bull_recovery   else 0.0)
            c += 8.0 if wpr_triple_os else 5.0 if wpr_med_os else 0.0
            c += 5.0 if wpr_bull_align else 2.0 if ws > -55 else 0.0
            c += (5.0 if closes[-1] > opens[-1] and body_ratio > 0.5
                  else 2.0 if closes[-1] > opens[-1] else 0.0)
            c += 5.0 if vol_ratio > 1.5 else 2.0 if vol_ratio > 1.0 else 0.0
            c += 5.0 if imp_momentum > 0.5 else 2.0 if imp_momentum > 0.2 else 0.0
            # Penalties
            c -= 8.0 if ws > -30 else 0.0
            c -= 5.0 if wf > -20 else 0.0
            c -= 10.0 if freshness < 0.1 else 0.0
            c -= 5.0 if abs(wpr_fast_vel) > 30 else 0.0
            return max(0.0, min(100.0, c))

        def bear_confidence() -> float:
            c = 0.0
            c += 25.0 if trend_flip_down else 0.0
            c += (20.0 if freshness > 0.8 else 15.0 if freshness > 0.6 else
                  10.0 if freshness > 0.4 else 5.0  if freshness > 0.2 else 0.0)
            c += 10.0 if impulse_dir == -1 else 0.0
            c += (15.0 if wf > -5  else 12.0 if wf > -10 else
                  9.0  if wf > -15 else 5.0  if wf > -20  else 0.0)
            c += (10.0 if wpr_fast_cross_down  else
                  7.0  if wpr_fast_vel < -3    else
                  4.0  if wpr_bear_recovery    else 0.0)
            c += 8.0 if wpr_triple_ob else 5.0 if wpr_med_ob else 0.0
            c += 5.0 if wpr_bear_align else 2.0 if ws < -45 else 0.0
            c += (5.0 if closes[-1] < opens[-1] and body_ratio > 0.5
                  else 2.0 if closes[-1] < opens[-1] else 0.0)
            c += 5.0 if vol_ratio > 1.5 else 2.0 if vol_ratio > 1.0 else 0.0
            c += 5.0 if imp_momentum > 0.5 else 2.0 if imp_momentum > 0.2 else 0.0
            c -= 8.0 if ws < -70 else 0.0
            c -= 5.0 if wf < -80 else 0.0
            c -= 10.0 if freshness < 0.1 else 0.0
            c -= 5.0 if abs(wpr_fast_vel) > 30 else 0.0
            return max(0.0, min(100.0, c))

        bull_conf = bull_confidence()
        bear_conf = bear_confidence()

        # ── Tier classification ────────────────────────────────────────────────
        bull_tier = "S" if bull_conf >= 70 else "A" if bull_conf >= 55 else "B"
        bear_tier = "S" if bear_conf >= 70 else "A" if bear_conf >= 55 else "B"

        # ── Band width squeeze indicator ───────────────────────────────────────
        band_width_pct = (band_upper - band_lower) / closes[-1] * 100 if closes[-1] > 0 else 0.0

        return {
            'valid':          True,
            # Impulse
            'freshness':      freshness,
            'impulse_dir':    impulse_dir,
            'imp_momentum':   imp_momentum,
            # Bands
            'band_upper':     band_upper,
            'band_lower':     band_lower,
            'band_width_pct': band_width_pct,
            'basis_ema':      basis_c[-1],
            'band_mult':      band_mult,
            # Trend
            'trend_dir':      trend_dir,
            'trend_flip_up':  trend_flip_up,
            'trend_flip_down': trend_flip_down,
            # WPR
            'wpr_fast':       wf,
            'wpr_med':        wm,
            'wpr_slow':       ws,
            'wpr_fast_vel':   wpr_fast_vel,
            'wpr_bull_align': wpr_bull_align,
            'wpr_bear_align': wpr_bear_align,
            'wpr_triple_os':  wpr_triple_os,
            'wpr_triple_ob':  wpr_triple_ob,
            'wpr_bull_ok':    wpr_bull_ok,
            'wpr_bear_ok':    wpr_bear_ok,
            # Volume
            'vol_ratio':      vol_ratio,
            # Confidence
            'bull_conf':      bull_conf,
            'bear_conf':      bear_conf,
            'bull_tier':      bull_tier,
            'bear_tier':      bear_tier,
        }

    @classmethod
    def bull_signal(cls, ax: dict) -> bool:
        """True si AlphaX da señal alcista válida."""
        if not ax.get('valid'):
            return True  # si no hay datos suficientes, no bloquear
        return (ax['bull_conf'] >= AX_MIN_CONF and
                ax['wpr_bull_ok'] and
                ax['trend_dir'] >= 0 and          # no en tendencia bajista
                ax['freshness'] > 0.1 and          # no completamente estale
                ax['impulse_dir'] >= 0)            # impulso no activamente bajista

    @classmethod
    def squeeze_warning(cls, ax: dict) -> bool:
        """True si hay squeeze de bandas (potencial breakout inminente)."""
        return ax.get('valid') and ax.get('band_width_pct', 99) < 2.0



class Filters:
    def __init__(self):
        self._cache: Dict[str, tuple] = {}
        self.btc_history = deque(maxlen=10)

    def _cached(self, key: str, ttl: int = 300):
        if key in self._cache:
            val, ts = self._cache[key]
            if time.time() - ts < ttl:
                return val
        return None

    def funding_rate(self, symbol: str) -> Tuple[bool, str, float]:
        key    = f"fr_{symbol}"
        cached = self._cached(key)
        if cached is not None:
            rate = cached
        else:
            data = public_request('/openApi/swap/v2/quote/premiumIndex', {'symbol': symbol})
            rate = 0.0
            if data.get('code') == 0 and data.get('data'):
                rate = safe_float(data['data'].get('lastFundingRate', 0)) * 100
            self._cache[key] = (rate, time.time())
        if rate > FUNDING_LONG_SKIP:
            return False, "funding_overheated", rate
        return rate < FUNDING_LONG_OK, "funding_ok" if rate < FUNDING_LONG_OK else "funding_neutral", rate

    def open_interest(self, symbol: str, price_chg: float) -> Tuple[bool, str, float]:
        key  = f"oi_{symbol}"
        data = public_request('/openApi/swap/v2/quote/openInterest', {'symbol': symbol})
        if data.get('code') != 0 or not data.get('data'):
            return True, "oi_unknown", 0.0
        oi = safe_float(data['data'].get('openInterest', 0))
        cached = self._cached(key, ttl=60)
        self._cache[key] = (oi, time.time())
        if cached is None:
            return True, "oi_first", 0.0
        change = (oi - cached) / cached * 100 if cached > 0 else 0
        if price_chg > 1.0 and change > OI_BREAKOUT_MIN:
            return True, "oi_breakout_confirmed", change
        if price_chg > 1.0 and change < OI_WEAK_THRESHOLD:
            return False, "oi_divergence_weak", change
        return True, "oi_neutral", change

    def session(self) -> Tuple[bool, str]:
        h = datetime.utcnow().hour
        if h in SESSION_BEST:  return True,  "us_session"
        if h in SESSION_OK:    return True,  "london_session"
        return False, "asia_session_avoid"

    def btc_health(self) -> Tuple[bool, str]:
        data = public_request('/openApi/swap/v2/quote/ticker', {'symbol': 'BTC-USDT'})
        if data.get('code') == 0 and data.get('data'):
            t   = data['data']
            chg = safe_float(t.get('priceChangePercent', 0))
            self.btc_history.append(safe_float(t.get('lastPrice', 0)))
            if chg < -2.5:
                return False, f"btc_falling_{chg:.1f}pct"
            return True, "btc_positive" if chg > 0 else "btc_neutral"
        return True, "btc_unknown"


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN 6: SUPERBOT — CLASE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class SuperBot:
    """
    SuperBot v5.0 — Simons Quant Edition.
    Nombre de clase expuesto para compatibilidad con main.py:
        from bot import SuperBot
    """

    def __init__(self):
        self.symbols:         List[str]         = []
        self.positions:       Dict[str, dict]   = {}
        self.contracts_info:  Dict[str, dict]   = {}
        self.symbol_cooldown: Dict[str, float]  = {}
        self.filters  = Filters()
        self.q        = Q()
        self.equity   = ACCOUNT_EQUITY

        # Circuit Breaker
        self.circuit_active = False
        self.circuit_until: Optional[datetime] = None
        self.daily_pnl    = 0.0
        self.daily_date   = datetime.utcnow().date()
        self.daily_trades = 0
        self.losing_streak = 0

        # Stats
        self.stats = {
            'wins': 0, 'losses': 0, 'total_pnl': 0.0,
            'win_amounts': [], 'loss_amounts': [],
            'best_trade': 0.0, 'worst_trade': 0.0,
            'hold_times': [],
            # Métricas Simons
            'z_vol_avg': [],    # Z-vol promedio en entradas
            'ensemble_avg': [], # Score ensemble promedio
            # Métricas por patrón (para feedback loop)
            'pattern_pnl': defaultdict(list),   # {pattern: [pnl1, pnl2...]}
            'tier_pnl':    defaultdict(list),   # {ax_tier: [pnl1...]}
            'sl_type_pnl': defaultdict(list),   # {'atr': [...], 'structural': [...]}
        }
        self._beta_cache: Dict[str, Tuple[float, float]] = {}  # {sym: (beta, ts)}

        self._banner()
        ok = self._connect()
        if not ok and AUTO_TRADING:
            log.error("❌ Conexión fallida. Abortando.")
            sys.exit(1)
        self._load_contracts()
        self._refresh_symbols()
        self._recover_positions()
        self._notify(
            f"<b>🚀 SUPERBOT v5.1 — SIMONS QUANT</b>\n\n"
            f"Capital: ${POSITION_SIZE} × {MAX_POSITIONS} | Lev: {LEVERAGE}×\n"
            f"Circuit: {CIRCUIT_BREAKER_PCT}% | Min score: {MIN_QUANT_SCORE}\n"
            f"Z-Vol: {Z_VOL_THRESHOLD} | Patterns≥{MIN_PATTERNS}\n"
            f"MTF 15m: {'✓' if MTF_ENABLED else '✗'} | Beta: {'✓' if BETA_FILTER else '✗'} [{BETA_MIN}-{BETA_MAX}]\n"
            f"SL: {'Estructural' if STRUCT_SL else 'ATR'} | Trail desde TP1: {'✓' if TRAILING_FROM_TP1 else '✗'}\n"
            f"AlphaX: {'✓' if AX_ENABLED else '✗'} | CSV log: ✓\n"
            f"Modo: {'REAL 💸' if AUTO_TRADING else 'PAPER 📝'}"
        )

    # ── Banner ────────────────────────────────────────────────────────────────
    def _banner(self):
        log.info("=" * 78)
        log.info("  SUPERBOT v5.1 — Simons Quant Edition")
        log.info("  'No predecimos el futuro, explotamos patrones estadísticos.'")
        log.info("=" * 78)
        log.info(f"  Capital: ${POSITION_SIZE}/pos × {MAX_POSITIONS} | Leverage: {LEVERAGE}×")
        log.info(f"  SL: {'Estructural+ATR' if STRUCT_SL else 'ATR'} | TP1: {TP1_RR}R | TP2: {TP2_RR}R | Trail: {RUNNER_TRAIL}×ATR")
        log.info(f"  Trailing desde TP1: {'✓' if TRAILING_FROM_TP1 else '✗'} ({TRAIL_TP1_MULT}×ATR)")
        log.info(f"  MTF 15m: {'✓' if MTF_ENABLED else '✗'} | Beta filter: {'✓' if BETA_FILTER else '✗'} [{BETA_MIN}-{BETA_MAX}]")
        log.info(f"  Min patterns: {MIN_PATTERNS} | AlphaX: {'✓' if AX_ENABLED else '✗'}")
        log.info(f"  Z-Vol: {Z_VOL_THRESHOLD} | Min Score: {MIN_QUANT_SCORE} | CSV: {TRADE_LOG_CSV}")
        log.info(f"  Auto-trading: {'ENABLED 💸' if AUTO_TRADING else 'PAPER MODE 📝'}")
        log.info("=" * 78)

    # ── Conexión ──────────────────────────────────────────────────────────────
    def _connect(self) -> bool:
        global AUTO_TRADING
        if not AUTO_TRADING:
            log.info("✓ Paper trading mode activo")
            return True
        if not API_KEY or not API_SECRET:
            log.error("❌ API keys no configuradas")
            AUTO_TRADING = False
            return False
        data = api_request('GET', '/openApi/swap/v2/user/balance')
        if data.get('code') == 0:
            eq = extract_equity(data)
            self.equity = eq if eq > 0 else ACCOUNT_EQUITY
            log.info(f"✓ BingX conectado | Equity: ${self.equity:.2f}")
            return True
        log.error(f"❌ Conexión fallida: {data.get('msg')}")
        AUTO_TRADING = False
        return False

    # ── Contratos ─────────────────────────────────────────────────────────────
    def _load_contracts(self):
        data = public_request('/openApi/swap/v2/quote/contracts')
        if data.get('code') == 0:
            for c in data.get('data', []):
                s = c.get('symbol', '')
                if s:
                    self.contracts_info[s] = {
                        'min_qty':       safe_float(c.get('tradeMinQuantity', 1)),
                        'qty_precision': int(c.get('quantityPrecision', 2)),
                        'contract_size': safe_float(c.get('contractSize', 1)),
                    }
            log.info(f"✓ Contratos: {len(self.contracts_info)}")

    # ── Refresh símbolos ──────────────────────────────────────────────────────
    def _refresh_symbols(self):
        data = public_request('/openApi/swap/v2/quote/ticker')
        if data.get('code') != 0:
            self.symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT']
            log.warning("⚠️ Usando fallback")
            return
        candidates = []
        for t in data.get('data', []):
            s = t.get('symbol', '')
            if not s.endswith('-USDT'):
                continue
            base = s.replace('-USDT', '').upper()
            if any(ex in base for ex in EXCLUDE_SYMBOLS) or s in EXCLUDE_SYMBOLS:
                continue
            if s not in self.contracts_info:
                continue
            price = safe_float(t.get('lastPrice', 0))
            vol   = safe_float(t.get('volume', 0)) * price
            if vol >= MIN_VOLUME_24H and price > 0:
                candidates.append({'symbol': s, 'volume': vol})
        candidates.sort(key=lambda x: x['volume'], reverse=True)
        self.symbols = [c['symbol'] for c in candidates[:MAX_SYMBOLS]]
        log.info(f"✓ Símbolos activos: {len(self.symbols)}")

    # ── Recuperar posiciones ──────────────────────────────────────────────────
    def _recover_positions(self):
        if not AUTO_TRADING:
            return
        data = api_request('GET', '/openApi/swap/v2/user/positions')
        if data.get('code') != 0:
            return
        recovered = 0
        for pos in data.get('data', []):
            try:
                symbol   = pos.get('symbol', '')
                amt      = safe_float(pos.get('positionAmt', 0))
                side_str = str(pos.get('positionSide', '')).upper()
                if (side_str == 'LONG' or (side_str == 'BOTH' and amt > 0)) and abs(amt) > 0:
                    entry = safe_float(pos.get('avgPrice') or pos.get('entryPrice', 0))
                    if entry <= 0:
                        continue
                    qty = abs(amt)
                    self.positions[symbol] = self._build_pos(
                        entry=entry, qty=qty,
                        tp1=entry * (1 + SL_MIN_PCT * TP1_RR / 100),
                        tp2=entry * (1 + SL_MIN_PCT * TP2_RR / 100),
                        sl=entry * (1 - SL_MIN_PCT / 100),
                        sl_pct=SL_MIN_PCT, atr=entry * 0.005,
                        score=0, sig=None, recovered=True
                    )
                    recovered += 1
                    log.info(f"♻️  Recuperada: {symbol} @ ${entry:.6f}")
            except Exception as e:
                log.error(f"Recover: {e}")
        if recovered:
            log.info(f"✓ {recovered} posiciones recuperadas")

    # ── Constructor de posición ───────────────────────────────────────────────
    def _build_pos(self, *, entry, qty, tp1, tp2, sl, sl_pct,
                   atr, score, sig, recovered=False) -> dict:
        return {
            'entry':        entry,
            'qty':          qty,
            'qty_tp1':      round(qty * TP1_PCT / 100, 6),
            'qty_tp2':      round(qty * TP2_PCT / 100, 6),
            'side':         'LONG',
            'sl_price':     sl,
            'sl_pct':       sl_pct,
            'tp1_price':    tp1,
            'tp2_price':    tp2,
            'tp1_hit':      False,
            'tp2_hit':      False,
            'highest':      entry,
            'opened_at':    datetime.now(),
            'score':        score,
            'signal':       sig or {'atr': atr},
            'pnl_realized': 0.0,
            'pos_size':     POSITION_SIZE,
            'recovered':    recovered,
        }

    # ── Velas ─────────────────────────────────────────────────────────────────
    def _klines(self, symbol: str, interval: str = '5m',
                limit: int = 150) -> Tuple[List, List, List, List, List]:
        try:
            data = public_request('/openApi/swap/v3/quote/klines',
                                  {'symbol': symbol, 'interval': interval, 'limit': limit})
            if data.get('code') == 0 and data.get('data'):
                ks = data['data']
                return (
                    [safe_float(k['close'])  for k in ks],
                    [safe_float(k['high'])   for k in ks],
                    [safe_float(k['low'])    for k in ks],
                    [safe_float(k['volume']) for k in ks],
                    [safe_float(k['open'])   for k in ks],
                )
        except Exception as e:
            log.error(f"Klines [{symbol}]: {e}")
        return None, None, None, None, None

    # ── Ticker ────────────────────────────────────────────────────────────────
    def _ticker(self, symbol: str) -> Optional[dict]:
        try:
            data = public_request('/openApi/swap/v2/quote/ticker', {'symbol': symbol})
            if data.get('code') == 0 and data.get('data'):
                t = data['data']
                return {
                    'price':      safe_float(t.get('lastPrice', 0)),
                    'change_pct': safe_float(t.get('priceChangePercent', 0)),
                    'volume':     safe_float(t.get('volume', 0)),
                }
        except Exception as e:
            log.error(f"Ticker [{symbol}]: {e}")
        return None

    # ══════════════════════════════════════════════════════════════════════════
    # NÚCLEO: ANÁLISIS SIMONS — ENSEMBLE DE SEÑALES
    # ══════════════════════════════════════════════════════════════════════════
    def analyze(self, symbol: str) -> Optional[dict]:
        """
        Motor de análisis cuantitativo multi-señal.
        Retorna dict de señal si score ≥ MIN_QUANT_SCORE, else None.
        """
        if symbol in self.positions:
            return None
        if symbol not in self.contracts_info:
            return None
        # Cooldown post-pérdida
        if time.time() - self.symbol_cooldown.get(symbol, 0) < SYMBOL_COOLDOWN * 60:
            return None

        closes, highs, lows, volumes, opens = self._klines(symbol, '5m', 150)
        if not closes or len(closes) < 50:
            return None

        # ── Multi-Timeframe: fetch 15m candles ────────────────────────────────
        closes_15m, highs_15m, lows_15m = [], [], []
        if MTF_ENABLED:
            c15, h15, l15, _, _ = self._klines(symbol, '15m', 60)
            if c15 and len(c15) >= 22:
                closes_15m, highs_15m, lows_15m = c15, h15, l15

        tick = self._ticker(symbol)
        if not tick or tick['price'] <= 0:
            return None

        price   = tick['price']
        chg_24h = tick['change_pct']

        # ── 1. Régimen de mercado ──────────────────────────────────────────────
        regime, atr_pct = Q.volatility_regime(highs, lows, closes)
        if regime in ("volatile_extreme", "ranging_quiet", "bearish"):
            log.debug(f"{symbol}: ✗ Regime={regime}")
            return None

        # ── 2. BTC Health ─────────────────────────────────────────────────────
        if symbol != 'BTC-USDT':
            btc_ok, btc_reason = self.filters.btc_health()
            if not btc_ok:
                log.debug(f"{symbol}: ✗ {btc_reason}")
                return None

        # ── 3. Correlation guard ──────────────────────────────────────────────
        if symbol not in ('BTC-USDT', 'ETH-USDT'):
            alts = sum(1 for s in self.positions if s not in ('BTC-USDT', 'ETH-USDT'))
            if alts >= MAX_CORR_LONGS:
                return None

        # ── 4. Filtros institucionales ─────────────────────────────────────────
        fund_ok, fund_reason, fund_rate = self.filters.funding_rate(symbol)
        if not fund_ok:
            log.debug(f"{symbol}: ✗ {fund_reason}")
            return None

        oi_ok, oi_reason, oi_chg = self.filters.open_interest(symbol, chg_24h)
        if not oi_ok:
            log.debug(f"{symbol}: ✗ {oi_reason}")
            return None

        sess_ok, sess_name = self.filters.session()
        if not sess_ok:
            log.debug(f"{symbol}: ✗ {sess_name}")
            return None

        # ── 5. Beta vs BTC filter ─────────────────────────────────────────────
        if BETA_FILTER and symbol != 'BTC-USDT':
            now_ts = time.time()
            cached  = self._beta_cache.get(symbol)
            if cached and now_ts - cached[1] < 1800:   # cache 30 min
                beta = cached[0]
            else:
                btc_c, _, _, _, _ = self._klines('BTC-USDT', '1h', BETA_LOOKBACK + 2)
                sym_c, _, _, _, _ = self._klines(symbol,     '1h', BETA_LOOKBACK + 2)
                beta = Q.beta_vs_btc(sym_c, btc_c, BETA_LOOKBACK) if btc_c and sym_c else 1.0
                self._beta_cache[symbol] = (beta, now_ts)
            if beta < BETA_MIN or beta > BETA_MAX:
                log.debug(f"{symbol}: ✗ beta={beta:.2f} fuera de [{BETA_MIN},{BETA_MAX}]")
                return None

        # ── 6. Multi-Timeframe confirmation ───────────────────────────────────
        if MTF_ENABLED and closes_15m:
            mtf_ok, mtf_reason = Q.mtf_confirm(closes_15m, highs_15m, lows_15m)
            if not mtf_ok:
                log.debug(f"{symbol}: ✗ MTF {mtf_reason}")
                return None

        # ══════════════════════════════════════════════════════════════════════
        # SEÑALES CUANTITATIVAS — NÚCLEO SIMONS
        # ══════════════════════════════════════════════════════════════════════

        # Z-Scores
        z_vol   = Q.z_score_volume(volumes, 30)
        z_price = Q.z_score_price(closes, 20)

        # Absorción institucional
        abs_ok, abs_str = Q.detect_absorption(volumes, highs, lows, opens, z_vol, closes)

        # Momentum multi-período
        mom_score, mom_str = Q.momentum_signal(closes)

        # Mean reversion zone
        mr_score, mr_str = Q.mean_reversion_score(closes, MEAN_REV_PERIOD)

        # CVD
        cvd_val, cvd_str = Q.cvd(volumes, closes, opens)

        # ── AlphaX Impulse Bands ───────────────────────────────────────────────
        ax = AlphaXEngine.compute(closes, highs, lows, opens, volumes)
        if AX_ENABLED and ax.get('valid'):
            if not AlphaXEngine.bull_signal(ax):
                log.debug(f"{symbol}: ✗ AlphaX bull_conf={ax['bull_conf']:.0f} "
                          f"trend={ax['trend_dir']} fresh={ax['freshness']:.2f}")
                return None

        # Patrones clásicos
        vcp_ok,  vcp_str  = Q.detect_vcp(closes, volumes, VCP_LOOKBACK)
        flag_ok, flag_str = Q.detect_flag(closes, volumes, highs, lows)

        # Indicadores de soporte
        atr_v = Q.atr(highs, lows, closes, 14)
        rsi_v = Q.rsi(closes, 14)
        e9    = Q.ema(closes, 9)
        e50   = Q.ema(closes, 50)
        ma10  = sum(closes[-10:]) / 10
        ma20  = sum(closes[-20:]) / 20 if len(closes) >= 20 else ma10
        vol_avg  = sum(volumes[-20:-1]) / 19 if len(volumes) >= 20 else volumes[-1]
        vol_ratio = volumes[-1] / vol_avg if vol_avg > 0 else 1.0

        # ── Score Ensemble (Simons) ────────────────────────────────────────────
        ensemble = Q.ensemble_score(z_vol, z_price, mom_score, mr_score, cvd_val)

        # ── Score Total (Ensemble + Patrones + Filtros + AlphaX) ─────────────
        score   = ensemble   # base: ensemble cuantitativo
        reasons = [f"Ensemble({ensemble:.0f})"]

        # Patrones visuales (+25 cada uno, hasta 50)
        if vcp_ok:
            score += 12; reasons.append(f"VCP(+12)")
        if flag_ok:
            score += 10; reasons.append(f"Flag(+10)")
        if abs_ok:
            score += 15; reasons.append(f"Absorcion(+15)")

        # Volumen inusual confirma
        if z_vol > Z_VOL_THRESHOLD:
            bonus = min(int((z_vol - Z_VOL_THRESHOLD) * 5), 10)
            score += bonus; reasons.append(f"ZVol{z_vol:.1f}(+{bonus})")
        if vol_ratio >= VOLUME_BREAKOUT_MULT:
            score += 8;  reasons.append(f"VolBreak{vol_ratio:.1f}x(+8)")

        # Momentum confirmado
        if mom_str == "mom_strong" and mom_score > 0.008:
            score += 8; reasons.append(f"MomStrong(+8)")

        # Estructura de MAs
        if price > ma10 and ma10 > ma20 and price > e50:
            score += 10; reasons.append("MAStack(+10)")
        elif price > ma20:
            score += 5;  reasons.append("AboveMA20(+5)")

        # EMA9 pullback / cross
        if price > e9 and closes[-2] <= Q.ema(closes[:-1], 9):
            score += 8; reasons.append("EMA9Cross(+8)")
        elif price > e9:
            score += 4; reasons.append("AboveEMA9(+4)")

        # RSI sweet spot
        if 40 < rsi_v < 60:
            score += 5; reasons.append(f"RSI_Sweet({rsi_v:.0f})(+5)")
        elif 30 < rsi_v <= 40:
            score += 3; reasons.append(f"RSI_Oversold({rsi_v:.0f})(+3)")

        # Regime bonus
        if regime == "trending_bullish":
            score += 8; reasons.append("Trending_Bull(+8)")

        # Sesión
        if sess_name == "us_session":
            score += 4; reasons.append("US_Sess(+4)")

        # Funding negativo = longs no saturados
        if fund_rate < 0:
            score += 4; reasons.append("Fund_Neg(+4)")

        # OI confirma breakout
        if oi_reason == "oi_breakout_confirmed":
            score += 5; reasons.append("OI_Break(+5)")

        # ── AlphaX Impulse bonus ───────────────────────────────────────────────
        if ax.get('valid'):
            bc = ax['bull_conf']
            # S/A/B tier bonuses
            if bc >= 70:
                score += 18; reasons.append(f"AX_S({bc:.0f}%)(+18)")
            elif bc >= 55:
                score += 12; reasons.append(f"AX_A({bc:.0f}%)(+12)")
            elif bc >= AX_MIN_CONF:
                score += 6;  reasons.append(f"AX_B({bc:.0f}%)(+6)")
            # Sub-señales adicionales
            if ax['wpr_triple_os']:
                score += 8; reasons.append("AX_TripleOS(+8)")
            elif ax['wpr_med'] < AX_WPR_OS:
                score += 4; reasons.append("AX_MedOS(+4)")
            if ax['trend_flip_up']:
                score += 10; reasons.append("AX_TrendFlip(+10)")
            if ax['freshness'] > 0.7:
                score += 5; reasons.append(f"AX_Fresh({ax['freshness']:.0%})(+5)")
            if AlphaXEngine.squeeze_warning(ax):
                score += 6; reasons.append(f"AX_Squeeze(bw={ax['band_width_pct']:.1f}%)(+6)")
            if ax['wpr_bull_align']:
                score += 4; reasons.append("AX_WPRAlign(+4)")

        # ── Gestión de riesgo dinámica ─────────────────────────────────────────
        # Structural SL: swing-low debajo del mínimo reciente (menos cazable)
        struct_sl = None
        if STRUCT_SL:
            struct_sl = Q.swing_low_sl(lows, closes, STRUCT_SL_BARS, STRUCT_SL_BUFFER)

        if struct_sl:
            sl_price = struct_sl
            sl_pct   = (price - sl_price) / price * 100
            sl_pct   = max(SL_MIN_PCT, min(SL_MAX_PCT, sl_pct))
            sl_price = price * (1 - sl_pct / 100)
        else:
            # Fallback: ATR-based SL
            sl_atr   = price - atr_v * SL_ATR_MULT
            sl_pct   = (price - sl_atr) / price * 100
            sl_pct   = max(SL_MIN_PCT, min(SL_MAX_PCT, sl_pct))
            sl_price = price * (1 - sl_pct / 100)

        tp1_price  = price * (1 + sl_pct * TP1_RR / 100)
        tp2_price  = price * (1 + sl_pct * TP2_RR / 100)
        edge_ratio = (sl_pct * TP1_RR) / (TOTAL_COST * 100)

        # ── Pattern guard: ≥ MIN_PATTERNS confirmados ─────────────────────────
        pattern_count = sum([
            bool(vcp_ok),
            bool(flag_ok),
            bool(abs_ok),
            bool(ax.get('valid') and ax.get('trend_flip_up')),
            bool(ax.get('valid') and ax.get('wpr_triple_os')),
        ])
        if pattern_count < MIN_PATTERNS:
            log.debug(f"{symbol}: ✗ Patterns={pattern_count} < {MIN_PATTERNS}")
            return None

        if edge_ratio < MIN_EDGE:
            log.debug(f"{symbol}: ✗ Edge {edge_ratio:.1f}×")
            return None
        if score < MIN_QUANT_SCORE:
            log.debug(f"{symbol}: ✗ Score {score:.0f}")
            return None

        return {
            'symbol':      symbol,
            'price':       price,
            'score':       score,
            'ensemble':    ensemble,
            'reasons':     ' | '.join(reasons),
            'sl_price':    sl_price,
            'sl_pct':      sl_pct,
            'tp1_price':   tp1_price,
            'tp2_price':   tp2_price,
            'edge_ratio':  edge_ratio,
            'atr':         atr_v,
            'atr_pct':     atr_pct,
            'z_vol':       z_vol,
            'z_price':     z_price,
            'mom_score':   mom_score,
            'mr_score':    mr_score,
            'cvd_val':     cvd_val,
            'cvd_signal':  cvd_str,
            'absorption':  abs_ok,
            'absorption_str': abs_str,
            'vcp':         vcp_ok,
            'vcp_str':     vcp_str,
            'flag':        flag_ok,
            'flag_str':    flag_str,
            'vol_ratio':   vol_ratio,
            'rsi':         rsi_v,
            'regime':      regime,
            'session':     sess_name,
            'funding_rate': fund_rate,
            'oi_change':   oi_chg,
            'ma10': ma10, 'ma20': ma20, 'ema9': e9, 'ema50': e50,
            # AlphaX Impulse Bands
            'ax_valid':       ax.get('valid', False),
            'ax_bull_conf':   ax.get('bull_conf', 0.0),
            'ax_bull_tier':   ax.get('bull_tier', '?'),
            'ax_freshness':   ax.get('freshness', 0.0),
            'ax_trend_dir':   ax.get('trend_dir', 0),
            'ax_trend_flip':  ax.get('trend_flip_up', False),
            'ax_wpr_fast':    ax.get('wpr_fast', -50.0),
            'ax_wpr_slow':    ax.get('wpr_slow', -50.0),
            'ax_triple_os':   ax.get('wpr_triple_os', False),
            'ax_squeeze':     AlphaXEngine.squeeze_warning(ax),
            'ax_band_width':  ax.get('band_width_pct', 0.0),
            'ax_impulse_dir': ax.get('impulse_dir', 0),
            # Signal meta
            'pattern_count':  pattern_count,
            'sl_type':        'structural' if struct_sl else 'atr',
        }

    # ══════════════════════════════════════════════════════════════════════════
    # EJECUCIÓN
    # ══════════════════════════════════════════════════════════════════════════

    def open_position(self, sig: dict) -> bool:
        symbol   = sig['symbol']
        price    = sig['price']
        sl_price = sig['sl_price']

        if not AUTO_TRADING:
            log.info(
                f"📝 PAPER LONG {symbol} | "
                f"Score:{sig['score']:.0f} | Ensemble:{sig['ensemble']:.0f} | "
                f"Z-Vol:{sig['z_vol']:.2f} | Edge:{sig['edge_ratio']:.1f}× | "
                f"Regime:{sig['regime']}"
            )
            # Track paper position so monitor_positions can simulate exits
            sl_pct = sig['sl_pct']
            tp1 = sig['price'] * (1 + sl_pct * TP1_RR / 100)
            tp2 = sig['price'] * (1 + sl_pct * TP2_RR / 100)
            qty_paper = (POSITION_SIZE * LEVERAGE) / sig['price']
            self.positions[symbol] = self._build_pos(
                entry=sig['price'], qty=qty_paper,
                tp1=tp1, tp2=tp2,
                sl=sig['sl_price'], sl_pct=sl_pct,
                atr=sig['atr'], score=sig['score'], sig=sig
            )
            self.daily_trades += 1
            self.stats['z_vol_avg'].append(sig['z_vol'])
            self.stats['ensemble_avg'].append(sig['ensemble'])
            return True

        if symbol not in self.contracts_info:
            log.error(f"❌ {symbol}: sin info de contrato")
            return False

        log.info(f"{'='*60}")
        log.info(f"🎯 LONG {symbol} | Score:{sig['score']:.0f} | Ensemble:{sig['ensemble']:.0f}")
        log.info(f"Entry: ${price:.6f} | SL: ${sl_price:.6f} (-{sig['sl_pct']:.2f}%)")

        # Kelly sizing
        total = self.stats['wins'] + self.stats['losses']
        pos_size = POSITION_SIZE
        if (total >= 10 and self.stats['win_amounts'] and self.stats['loss_amounts']):
            wr    = self.stats['wins'] / total
            avg_w = sum(self.stats['win_amounts'][-20:]) / len(self.stats['win_amounts'][-20:])
            avg_l = abs(sum(self.stats['loss_amounts'][-20:]) / len(self.stats['loss_amounts'][-20:]))
            pos_size = Q.kelly_size(wr, avg_w, avg_l, self.equity)

        qty = self._calc_qty(symbol, price, sl_price, pos_size)
        if not qty:
            log.error(f"❌ {symbol}: qty incalculable")
            return False

        self._set_leverage(symbol, LEVERAGE)
        time.sleep(0.3)

        order = api_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol':       symbol,
            'side':         'BUY',
            'type':         'MARKET',
            'quantity':     str(qty),
            'positionSide': 'LONG',
        })
        if order.get('code') != 0:
            log.error(f"❌ Orden {symbol}: {order.get('msg')}")
            return False

        time.sleep(1)
        fill_qty, fill_price = self._confirm_position(symbol)
        if not fill_qty:
            log.error(f"❌ {symbol}: posición no confirmada")
            return False

        real_sl_pct = (fill_price - sl_price) / fill_price * 100
        tp1 = fill_price * (1 + real_sl_pct * TP1_RR / 100)
        tp2 = fill_price * (1 + real_sl_pct * TP2_RR / 100)

        # Stop Loss en exchange
        sl_params = {
            'symbol': symbol, 'side': 'SELL', 'type': 'STOP_MARKET',
            'quantity': str(fill_qty), 'stopPrice': str(round(sl_price, 8)),
            'positionSide': 'LONG'
        }
        sl_r = api_request('POST', '/openApi/swap/v2/trade/order', sl_params)
        if sl_r.get('code') != 0:
            sl_params['type']  = 'STOP'
            sl_params['price'] = str(round(sl_price * 0.999, 8))
            sl_r = api_request('POST', '/openApi/swap/v2/trade/order', sl_params)
        sl_ok = sl_r.get('code') == 0

        self.positions[symbol] = self._build_pos(
            entry=fill_price, qty=fill_qty,
            tp1=tp1, tp2=tp2,
            sl=sl_price, sl_pct=real_sl_pct,
            atr=sig['atr'], score=sig['score'], sig=sig
        )
        self.positions[symbol]['pos_size'] = pos_size
        self.daily_trades += 1

        # Guardar métricas Simons
        self.stats['z_vol_avg'].append(sig['z_vol'])
        self.stats['ensemble_avg'].append(sig['ensemble'])

        patterns = "+".join(filter(None, [
            "Absorción" if sig.get('absorption') else "",
            "VCP"       if sig.get('vcp')        else "",
            "Flag"      if sig.get('flag')        else "",
        ])) or "Momentum_Quant"

        ax_line = ""
        if sig.get('ax_valid'):
            ax_line = (f"\n🌊 AlphaX: {sig['ax_bull_tier']}-Tier "
                       f"({sig['ax_bull_conf']:.0f}%) | "
                       f"Fresh:{sig['ax_freshness']:.0%} | "
                       f"WPR:{sig['ax_wpr_fast']:.0f}/{sig['ax_wpr_slow']:.0f}"
                       + (" | TripleOS🔥" if sig.get('ax_triple_os') else "")
                       + (" | SQUEEZE⚡" if sig.get('ax_squeeze') else ""))

        log.info(f"✓ LONG {symbol} @ ${fill_price:.6f} | SL:{'OK' if sl_ok else '⚠️'}")
        self._notify(
            f"<b>🟢 LONG ABIERTO — SIMONS QUANT</b>\n\n"
            f"<b>{symbol}</b> | {patterns}\n\n"
            f"🔬 Ensemble: {sig['ensemble']:.0f} | Score: {sig['score']:.0f}\n"
            f"📊 Z-Vol: {sig['z_vol']:.2f} | Z-Price: {sig['z_price']:.2f}\n"
            f"⚡ Momentum: {sig['mom_score']*100:.2f}% | CVD: {sig['cvd_signal']}"
            f"{ax_line}\n"
            f"💱 Funding: {sig['funding_rate']:.3f}% | OI: {sig['oi_change']:+.1f}%\n\n"
            f"📍 Entrada: ${fill_price:.6f}\n"
            f"🎯 TP1: ${tp1:.6f} (+{real_sl_pct * TP1_RR:.2f}%)\n"
            f"🎯 TP2: ${tp2:.6f} (+{real_sl_pct * TP2_RR:.2f}%)\n"
            f"🛑 SL: ${sl_price:.6f} (-{real_sl_pct:.2f}%)\n"
            f"Edge: {sig['edge_ratio']:.1f}× | Regime: {sig['regime']}\n\n"
            f"{'✅ SL en exchange' if sl_ok else '⚠️ SL MANUAL'}"
        )
        return True

    # ── Monitor de posiciones ─────────────────────────────────────────────────
    async def monitor_positions(self):
        for symbol in list(self.positions.keys()):
            try:
                pos  = self.positions[symbol]
                tick = self._ticker(symbol)
                if not tick:
                    continue
                cp = tick['price']
                if cp > pos.get('highest', pos['entry']):
                    pos['highest'] = cp

                # Max hold
                hold_min = (datetime.now() - pos['opened_at']).total_seconds() / 60
                if hold_min >= MAX_HOLD_H * 60:
                    log.info(f"⏰ {symbol}: max hold")
                    self._close_full(symbol, cp, "MAX_HOLD")
                    continue

                # TP1
                if not pos['tp1_hit'] and cp >= pos.get('tp1_price', 1e18):
                    self._close_partial(symbol, pos['qty_tp1'], cp, "TP1")
                    pos['tp1_hit']  = True
                    pos['sl_price'] = pos['entry'] * 1.001   # move SL to breakeven+
                    continue

                # Trailing desde TP1 (NUEVO): protege ganancias antes de TP2
                if pos['tp1_hit'] and not pos['tp2_hit'] and TRAILING_FROM_TP1:
                    sig_ref  = pos.get('signal') or {}
                    atr_val  = sig_ref.get('atr', pos['entry'] * 0.005)
                    trail_sl = cp - atr_val * TRAIL_TP1_MULT
                    be_floor = pos['entry'] * 1.001   # nunca bajar debajo de breakeven
                    new_sl   = max(trail_sl, be_floor)
                    if new_sl > pos['sl_price']:
                        pos['sl_price'] = new_sl
                        log.debug(f"⬆ Trail[TP1] {symbol}: SL → ${new_sl:.6f}")

                # TP2
                if pos['tp1_hit'] and not pos['tp2_hit'] and cp >= pos.get('tp2_price', 1e18):
                    self._close_partial(symbol, pos['qty_tp2'], cp, "TP2")
                    pos['tp2_hit'] = True
                    sig_ref  = pos.get('signal') or {}
                    atr_val  = sig_ref.get('atr', pos['entry'] * 0.005)
                    pos['sl_price'] = max(pos['sl_price'], cp - atr_val * RUNNER_TRAIL)
                    continue

                # Runner trailing (post-TP2): ATR trail más agresivo
                if pos['tp2_hit']:
                    sig_ref  = pos.get('signal') or {}
                    atr_val  = sig_ref.get('atr', pos['entry'] * 0.005)
                    new_sl   = cp - atr_val * RUNNER_TRAIL
                    if new_sl > pos['sl_price']:
                        pos['sl_price'] = new_sl

                # SL
                if cp <= pos['sl_price']:
                    self._close_full(symbol, cp, "STOP_LOSS")

            except KeyError as e:
                log.error(f"KeyError {symbol}: {e}")
                self.positions.pop(symbol, None)
            except Exception as e:
                log.error(f"Monitor [{symbol}]: {e}\n{traceback.format_exc()}")

    # ── Cierre parcial ────────────────────────────────────────────────────────
    def _close_partial(self, symbol: str, qty: float, price: float, reason: str):
        if qty <= 0:
            return
        if AUTO_TRADING:
            res = api_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol': symbol, 'side': 'SELL', 'type': 'MARKET',
                'quantity': str(qty), 'positionSide': 'LONG',
            })
            if res.get('code') != 0:
                log.error(f"❌ Cierre parcial {symbol}: {res.get('msg')}")
                return
        pos = self.positions[symbol]
        pnl = self._calc_pnl(pos['entry'], price, qty, symbol)
        pos['pnl_realized'] += pnl
        pos['qty']          -= qty
        self._update_stats_partial(pnl)
        log.info(f"💰 {reason} {symbol}: qty={qty} @ ${price:.6f} | PnL: ${pnl:+.4f}")
        self._notify(f"<b>💰 {reason}</b> — {symbol}\nExit: ${price:.6f} | PnL: ${pnl:+.4f}")

    # ── Cierre total ──────────────────────────────────────────────────────────
    def _close_full(self, symbol: str, price: float, reason: str):
        if symbol not in self.positions:
            return
        pos = self.positions[symbol]
        qty = pos['qty']
        if qty > 0 and AUTO_TRADING:
            api_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol': symbol, 'side': 'SELL', 'type': 'MARKET',
                'quantity': str(qty), 'positionSide': 'LONG',
            })
            # Cancel open SL/TP orders to avoid orphan orders on exchange
            try:
                open_orders = api_request('GET', '/openApi/swap/v2/trade/openOrders', {'symbol': symbol})
                for o in open_orders.get('data', {}).get('orders', []):
                    oid = o.get('orderId')
                    if oid:
                        api_request('DELETE', '/openApi/swap/v2/trade/order',
                                    {'symbol': symbol, 'orderId': str(oid)})
            except Exception as e:
                log.warning(f"Cancel orphan orders [{symbol}]: {e}")
        pnl_final = self._calc_pnl(pos['entry'], price, qty, symbol)
        total_pnl = pos['pnl_realized'] + pnl_final
        hold_min  = int((datetime.now() - pos['opened_at']).total_seconds() / 60)
        win = total_pnl > 0
        if win:
            self.stats['wins']        += 1
            self.stats['win_amounts'].append(total_pnl)
            self.losing_streak = 0
        else:
            self.stats['losses']       += 1
            self.stats['loss_amounts'].append(total_pnl)
            self.losing_streak        += 1
            self.symbol_cooldown[symbol] = time.time()
        self.stats['total_pnl'] += pnl_final
        self.stats['hold_times'].append(hold_min)
        self.daily_pnl         += pnl_final
        if total_pnl > self.stats['best_trade']:  self.stats['best_trade']  = total_pnl
        if total_pnl < self.stats['worst_trade']: self.stats['worst_trade'] = total_pnl
        total_t = self.stats['wins'] + self.stats['losses']
        wr = (self.stats['wins'] / total_t * 100) if total_t > 0 else 0
        pf = self._profit_factor()
        log.info(f"{'✅' if win else '❌'} {reason} {symbol} | "
                 f"PnL: ${total_pnl:+.4f} | {hold_min}min | WR:{wr:.0f}% | PF:{pf:.2f}")
        self._notify(
            f"<b>{'✅ WIN' if win else '❌ LOSS'}</b> — {reason}\n\n"
            f"<b>{symbol}</b>\n"
            f"${pos['entry']:.6f} → ${price:.6f} | {hold_min}min\n\n"
            f"<b>PnL: ${total_pnl:+.4f}</b>\n"
            f"WR: {wr:.0f}% ({self.stats['wins']}/{total_t}) | PF: {pf:.2f}\n"
            f"Racha pérdidas: {self.losing_streak}"
        )
        del self.positions[symbol]
        # ── CSV Trade Log ─────────────────────────────────────────────────────
        self._log_trade(symbol, pos, price, total_pnl, hold_min, reason, win)
        # ── Feedback loop stats ────────────────────────────────────────────────
        sig = pos.get('signal', {})
        for pat in ('vcp', 'flag', 'absorption'):
            if sig.get(pat):
                self.stats['pattern_pnl'][pat].append(total_pnl)
        tier = sig.get('ax_bull_tier', '?')
        self.stats['tier_pnl'][tier].append(total_pnl)
        sl_t = sig.get('sl_type', 'atr')
        self.stats['sl_type_pnl'][sl_t].append(total_pnl)
        # Log pattern stats cada 10 trades
        total_t = self.stats['wins'] + self.stats['losses']
        if total_t > 0 and total_t % 10 == 0:
            self._log_pattern_stats()

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _calc_pnl(self, entry: float, exit_p: float, qty: float, symbol: str = '') -> float:
        info     = self.contracts_info.get(symbol, {})
        csz      = info.get('contract_size', 1)
        notional = qty * entry * csz
        gross    = (exit_p - entry) / entry * notional * LEVERAGE
        fees     = notional * (FEE_TAKER + FEE_MAKER)
        return gross - fees

    def _log_trade(self, symbol: str, pos: dict, exit_price: float,
                   pnl: float, hold_min: int, reason: str, win: bool):
        """
        Guarda cada trade cerrado en CSV para análisis de feedback loop.
        Columnas: todas las métricas de señal + resultado real.
        """
        sig = pos.get('signal', {})
        row = {
            'timestamp':    datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            'symbol':       symbol,
            'entry':        pos.get('entry', 0),
            'exit':         exit_price,
            'qty':          pos.get('qty', 0),
            'pnl_usd':      round(pnl, 4),
            'hold_min':     hold_min,
            'reason':       reason,
            'win':          int(win),
            # Score metrics
            'score':        sig.get('score', pos.get('score', 0)),
            'ensemble':     sig.get('ensemble', 0),
            'z_vol':        round(sig.get('z_vol', 0), 3),
            'z_price':      round(sig.get('z_price', 0), 3),
            'rsi':          round(sig.get('rsi', 0), 1),
            'vol_ratio':    round(sig.get('vol_ratio', 0), 2),
            'edge_ratio':   round(sig.get('edge_ratio', 0), 2),
            'atr_pct':      round(sig.get('atr_pct', 0), 4),
            # Patterns
            'vcp':          int(bool(sig.get('vcp'))),
            'flag':         int(bool(sig.get('flag'))),
            'absorption':   int(bool(sig.get('absorption'))),
            'pattern_count':sig.get('pattern_count', 0),
            'sl_type':      sig.get('sl_type', 'atr'),
            # AlphaX
            'ax_tier':      sig.get('ax_bull_tier', '?'),
            'ax_conf':      round(sig.get('ax_bull_conf', 0), 1),
            'ax_freshness': round(sig.get('ax_freshness', 0), 2),
            'ax_triple_os': int(bool(sig.get('ax_triple_os'))),
            'ax_squeeze':   int(bool(sig.get('ax_squeeze'))),
            'ax_wpr_fast':  round(sig.get('ax_wpr_fast', 0), 1),
            # Context
            'regime':       sig.get('regime', ''),
            'session':      sig.get('session', ''),
            'funding_rate': round(sig.get('funding_rate', 0), 4),
            'oi_change':    round(sig.get('oi_change', 0), 2),
            'sl_pct':       round(sig.get('sl_pct', 0), 3),
            'tp1_hit':      int(bool(pos.get('tp1_hit'))),
            'tp2_hit':      int(bool(pos.get('tp2_hit'))),
            'mode':         'REAL' if AUTO_TRADING else 'PAPER',
        }
        try:
            path    = TRADE_LOG_CSV
            is_new  = not os.path.exists(path)
            with open(path, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=row.keys())
                if is_new:
                    writer.writeheader()
                writer.writerow(row)
        except Exception as e:
            log.warning(f"CSV log error: {e}")

    def _log_pattern_stats(self):
        """Imprime análisis de rendimiento por patrón/tier cada 10 trades."""
        lines = ["📊 FEEDBACK LOOP — Rendimiento por patrón:"]
        for pat, pnls in self.stats['pattern_pnl'].items():
            if not pnls: continue
            wr  = sum(1 for p in pnls if p > 0) / len(pnls) * 100
            avg = sum(pnls) / len(pnls)
            lines.append(f"  {pat.upper():12s}: WR={wr:.0f}% | avg=${avg:+.4f} | n={len(pnls)}")
        for tier, pnls in self.stats['tier_pnl'].items():
            if not pnls: continue
            wr  = sum(1 for p in pnls if p > 0) / len(pnls) * 100
            avg = sum(pnls) / len(pnls)
            lines.append(f"  AX-{tier}-Tier   : WR={wr:.0f}% | avg=${avg:+.4f} | n={len(pnls)}")
        for slt, pnls in self.stats['sl_type_pnl'].items():
            if not pnls: continue
            wr  = sum(1 for p in pnls if p > 0) / len(pnls) * 100
            avg = sum(pnls) / len(pnls)
            lines.append(f"  SL-{slt:10s}: WR={wr:.0f}% | avg=${avg:+.4f} | n={len(pnls)}")
        msg = "\n".join(lines)
        log.info(msg)
        self._notify(f"<b>📊 Feedback Loop</b>\n<pre>{msg}</pre>")

    def _calc_qty(self, symbol: str, price: float, sl_price: float,
                  pos_size: float = None) -> Optional[float]:
        if pos_size is None:
            pos_size = POSITION_SIZE
        info      = self.contracts_info.get(symbol, {})
        min_qty   = info.get('min_qty', 1)
        precision = info.get('qty_precision', 2)
        csz       = info.get('contract_size', 1)
        ppc       = price * csz
        if ppc <= 0:
            return None
        risk_pct    = max((price - sl_price) / price * 100, 0.1)
        risk_amount = self.equity * (RISK_PER_TRADE / 100)
        notional    = min(risk_amount / (risk_pct / 100), pos_size * LEVERAGE)
        qty = math.ceil((notional / ppc) / min_qty) * min_qty
        qty = round(qty, precision)
        return qty if qty >= min_qty else None

    def _set_leverage(self, symbol: str, leverage: int):
        for side in ('LONG', 'SHORT'):
            api_request('POST', '/openApi/swap/v2/trade/leverage',
                        {'symbol': symbol, 'side': side, 'leverage': str(leverage)})

    def _confirm_position(self, symbol: str, timeout: int = 15) -> Tuple[Optional[float], Optional[float]]:
        for _ in range(timeout):
            data = api_request('GET', '/openApi/swap/v2/user/positions', {'symbol': symbol})
            for pos in data.get('data', []):
                amt  = safe_float(pos.get('positionAmt', 0))
                side = str(pos.get('positionSide', '')).upper()
                if (side == 'LONG' or (side == 'BOTH' and amt > 0)) and abs(amt) > 0:
                    entry = safe_float(pos.get('avgPrice') or pos.get('entryPrice', 0))
                    return abs(amt), entry
            time.sleep(1)
        return None, None

    def _update_stats_partial(self, pnl: float):
        self.stats['total_pnl'] += pnl
        self.daily_pnl          += pnl
        if pnl > self.stats['best_trade']:  self.stats['best_trade']  = pnl
        if pnl < self.stats['worst_trade']: self.stats['worst_trade'] = pnl

    def _profit_factor(self) -> float:
        gross_win  = sum(self.stats['win_amounts'])            if self.stats['win_amounts']  else 0
        gross_loss = abs(sum(self.stats['loss_amounts']))      if self.stats['loss_amounts'] else 1
        return round(gross_win / gross_loss, 2)

    # ── Circuit Breaker ───────────────────────────────────────────────────────
    def _circuit_breaker(self) -> bool:
        today = datetime.utcnow().date()
        if today != self.daily_date:
            self.daily_pnl    = 0.0
            self.daily_date   = today
            self.daily_trades = 0
            if self.circuit_active:
                self.circuit_active = False
                self.circuit_until  = None
                log.info("🔓 Circuit Breaker RESET")
        if self.circuit_active:
            if self.circuit_until and datetime.utcnow() > self.circuit_until:
                self.circuit_active = False
                log.info("🔓 Circuit Breaker OFF")
                return False
            return True
        threshold = self.equity * (CIRCUIT_BREAKER_PCT / 100)
        if self.daily_pnl < -threshold:
            self._activate_circuit(f"Pérdida ${self.daily_pnl:.2f}", hours=6)
            return True
        if self.losing_streak >= MAX_LOSING_STREAK:
            self._activate_circuit(f"Racha {self.losing_streak} pérdidas", hours=4)
            return True
        if self.daily_trades >= MAX_DAILY_TRADES:
            log.warning(f"⚠️ Max trades: {self.daily_trades}/{MAX_DAILY_TRADES}")
            return True
        return False

    def _activate_circuit(self, reason: str, hours: int):
        self.circuit_active = True
        self.circuit_until  = datetime.utcnow() + timedelta(hours=hours)
        log.warning(f"🔒 CIRCUIT BREAKER — {reason} | {hours}h")
        self._notify(f"<b>🔒 CIRCUIT BREAKER</b>\n{reason}\nPausa: {hours}h")

    # ── Telegram ──────────────────────────────────────────────────────────────
    def _notify(self, msg: str):
        if not TG_TOKEN or not TG_CHAT:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                timeout=5
            )
        except Exception as e:
            log.error(f"Telegram: {e}")

    # ── Loop principal ────────────────────────────────────────────────────────
    async def run(self):
        log.info("🚀 SuperBot v5.0 Simons Quant — Corriendo...\n")
        iteration          = 0
        last_sym_refresh   = 0
        last_equity_update = 0
        last_report        = 0

        while True:
            try:
                iteration += 1

                if time.time() - last_sym_refresh > 600:
                    self._refresh_symbols()
                    last_sym_refresh = time.time()

                if AUTO_TRADING and time.time() - last_equity_update > 1800:
                    data = api_request('GET', '/openApi/swap/v2/user/balance')
                    if data.get('code') == 0:
                        eq = extract_equity(data)
                        if eq > 0:
                            self.equity = eq
                    last_equity_update = time.time()

                if self._circuit_breaker():
                    await asyncio.sleep(SCAN_INTERVAL)
                    continue

                total = self.stats['wins'] + self.stats['losses']
                wr    = (self.stats['wins'] / total * 100) if total > 0 else 0
                pf    = self._profit_factor()
                avg_h = (sum(self.stats['hold_times']) / len(self.stats['hold_times'])
                         if self.stats['hold_times'] else 0)
                avg_z = (sum(self.stats['z_vol_avg'][-20:]) / len(self.stats['z_vol_avg'][-20:])
                         if self.stats['z_vol_avg'] else 0)
                avg_e = (sum(self.stats['ensemble_avg'][-20:]) / len(self.stats['ensemble_avg'][-20:])
                         if self.stats['ensemble_avg'] else 0)

                log.info(f"\n{'='*70}")
                log.info(f"  #{iteration} {datetime.now().strftime('%d/%m %H:%M:%S')} | "
                         f"Pos: {len(self.positions)}/{MAX_POSITIONS}")
                log.info(f"  PnL: ${self.stats['total_pnl']:+.4f} | Hoy: ${self.daily_pnl:+.4f} | "
                         f"WR: {wr:.0f}% ({total}T) | PF: {pf:.2f}")
                log.info(f"  Equity: ${self.equity:.2f} | Avg-Hold: {avg_h:.0f}min | "
                         f"Avg-ZVol: {avg_z:.2f} | Avg-Ensemble: {avg_e:.0f}")
                log.info(f"{'='*70}\n")

                await self.monitor_positions()

                if time.time() - last_report > 3600:
                    self._notify(
                        f"<b>📊 Reporte Horario — SuperBot v5</b>\n\n"
                        f"PnL: ${self.stats['total_pnl']:+.4f} | Hoy: ${self.daily_pnl:+.4f}\n"
                        f"WR: {wr:.0f}% ({total}T) | PF: {pf:.2f}\n"
                        f"Avg Hold: {avg_h:.0f}min | Avg Z-Vol: {avg_z:.2f}\n"
                        f"Pos: {len(self.positions)}/{MAX_POSITIONS}"
                    )
                    last_report = time.time()

                if len(self.positions) < MAX_POSITIONS and self.daily_trades < MAX_DAILY_TRADES:
                    log.info(f"🔬 Escaneando {len(self.symbols)} símbolos [Simons Quant]...")
                    found = 0
                    for symbol in self.symbols:
                        if len(self.positions) >= MAX_POSITIONS:
                            break
                        if self.daily_trades >= MAX_DAILY_TRADES:
                            break
                        sig = self.analyze(symbol)
                        if sig:
                            found += 1
                            patterns = "+".join(filter(None, [
                                "Absorción" if sig.get('absorption') else "",
                                "VCP"       if sig.get('vcp')        else "",
                                "Flag"      if sig.get('flag')        else "",
                            ])) or "Quant"
                            ax_info = ""
                            if sig.get('ax_valid'):
                                ax_info = (f" | AX:{sig['ax_bull_tier']}"
                                           f"({sig['ax_bull_conf']:.0f}%)"
                                           f" Fresh:{sig['ax_freshness']:.0%}"
                                           f" WPR:{sig['ax_wpr_fast']:.0f}"
                                           + (" 🌊TOS" if sig.get('ax_triple_os') else "")
                                           + (" 🔧SQZ" if sig.get('ax_squeeze') else ""))
                            log.info(
                                f"💡 {symbol} | "
                                f"Score:{sig['score']:.0f} | Ensemble:{sig['ensemble']:.0f} | "
                                f"ZVol:{sig['z_vol']:.2f} | {patterns} | {sig['regime']}"
                                f"{ax_info}"
                            )
                            if self.open_position(sig):
                                await asyncio.sleep(3)
                    log.info(f"✓ Escaneo completo | Señales: {found}")

                await asyncio.sleep(SCAN_INTERVAL)

            except KeyboardInterrupt:
                log.info("\n⏹️  Bot detenido")
                self._notify("<b>⏹️ SUPERBOT DETENIDO</b>")
                break
            except Exception as e:
                log.error(f"❌ Main loop: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(30)
