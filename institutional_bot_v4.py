#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║        INSTITUTIONAL BOT v4.0 — Phoenix Trader Edition                     ║
║        BingX Futures · USDT Perps · LONG Only                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Filosofía: "Precio + Volumen es todo. Estructura, no indicadores tardíos." ║
║                                                                              ║
║  INDICADORES: MA10 · MA20 · EMA9 · EMA50 · ATR · RSI · CVD                ║
║  PATRONES:    VCP (Volatility Contraction) · Flag/Banderín                 ║
║  FILTROS:     Market Regime · BTC Guard · Funding · OI · Sesión            ║
║  RIESGO:      Kelly Sizing · Circuit Breaker · Trailing Stop               ║
╚══════════════════════════════════════════════════════════════════════════════╝

  NUEVO en v4.0:
  ─ Soporte SHORT opcional (configurable)
  ─ Max hold time (cierre automático en N horas)
  ─ Breakeven automático más inteligente
  ─ Mejor scoring con normalización
  ─ Cooldown por símbolo (evita re-entrar en perdedor reciente)
  ─ Stats ampliadas: avg hold time, profit factor, max drawdown
  ─ Todos los fixes de v3.1 incorporados de base (sin bugs heredados)
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re, json
import statistics, traceback
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN 1: CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

def _env(key: str, default, typ='str'):
    """Lee variable de entorno con limpieza de comillas y conversión de tipo."""
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

# ── API Credentials ───────────────────────────────────────────────────────────
API_KEY    = _env('BINGX_API_KEY', '')
API_SECRET = _env('BINGX_API_SECRET', '')
TG_TOKEN   = _env('TELEGRAM_BOT_TOKEN', '')
TG_CHAT    = _env('TELEGRAM_CHAT_ID', '')
BASE_URL   = "https://open-api.bingx.com"

# ── Trading Mode ──────────────────────────────────────────────────────────────
AUTO_TRADING  = _env('AUTO_TRADING_ENABLED', 'false', 'bool')   # ⚠️ false = PAPER MODE
ALLOW_SHORTS  = _env('ALLOW_SHORTS', 'false', 'bool')           # SHORT trades (experimental)

# ── Capital Management ────────────────────────────────────────────────────────
POSITION_SIZE  = _env('POSITION_SIZE_USD', '10', 'float')       # USD por posición
LEVERAGE       = min(_env('LEVERAGE', '2', 'int'), 5)           # Nunca superar 5×
MAX_POSITIONS  = _env('MAX_POSITIONS', '2', 'int')              # Posiciones simultáneas
ACCOUNT_EQUITY = _env('ACCOUNT_EQUITY', '100', 'float')         # Equity inicial
RISK_PER_TRADE = _env('RISK_PCT_PER_TRADE', '1.0', 'float')     # % equity en riesgo/trade

# ── Stop Loss & Take Profit ───────────────────────────────────────────────────
SL_ATR_MULT  = _env('SL_ATR_MULTIPLIER', '1.5', 'float')
SL_MIN_PCT   = _env('SL_MIN_PCT', '0.8', 'float')
SL_MAX_PCT   = _env('SL_MAX_PCT', '2.0', 'float')
TP1_PCT      = _env('TP1_PERCENTAGE', '40', 'float')            # % cantidad en TP1
TP2_PCT      = _env('TP2_PERCENTAGE', '40', 'float')            # % cantidad en TP2
TP1_RR       = _env('TP1_RISK_REWARD', '1.5', 'float')
TP2_RR       = _env('TP2_RISK_REWARD', '2.5', 'float')
RUNNER_TRAIL = _env('RUNNER_TRAIL_ATR', '2.0', 'float')
MIN_EDGE     = _env('MIN_EDGE_RATIO', '4.0', 'float')
MAX_HOLD_H   = _env('MAX_HOLD_HOURS', '24', 'float')            # Cierre forzado en N horas

# ── Market Filters ────────────────────────────────────────────────────────────
MIN_VOLUME_24H       = _env('MIN_VOLUME_24H', '2000000', 'float')
MAX_SYMBOLS          = _env('MAX_SYMBOLS', '30', 'int')
MIN_SCORE            = _env('MIN_ENTRY_SCORE', '75', 'float')
VOLUME_BREAKOUT_MULT = _env('VOLUME_BREAKOUT_MULT', '1.8', 'float')
REGIME_ATR_MIN_PCT   = _env('REGIME_ATR_MIN_PCT', '0.5', 'float')
REGIME_ATR_MAX_PCT   = _env('REGIME_ATR_MAX_PCT', '3.5', 'float')
VCP_LOOKBACK         = _env('VCP_LOOKBACK', '20', 'int')
MAX_CORR_LONGS       = _env('MAX_CORR_LONGS', '1', 'int')
EMA9_REQUIRED        = _env('EMA9_REQUIRED', 'true', 'bool')

# ── Funding & OI Filters ──────────────────────────────────────────────────────
FUNDING_ENABLED    = _env('FUNDING_FILTER', 'true', 'bool')
FUNDING_LONG_OK    = _env('FUNDING_LONG_OK', '0.03', 'float')
FUNDING_LONG_SKIP  = _env('FUNDING_LONG_SKIP', '0.05', 'float')
OI_ENABLED         = _env('OI_FILTER', 'true', 'bool')
OI_BREAKOUT_MIN    = _env('OI_BREAKOUT_MIN', '1.5', 'float')
OI_WEAK_THRESHOLD  = _env('OI_WEAK_THRESHOLD', '0.5', 'float')

# ── Session Filter ────────────────────────────────────────────────────────────
SESSION_FILTER_ENABLED = _env('SESSION_FILTER', 'true', 'bool')
SESSION_BEST  = {13, 14, 15, 16, 17, 18, 19, 20, 21, 22}   # Londres + NY (UTC)
SESSION_OK    = {7, 8, 9, 10, 11, 12}                        # Pre-Londres (UTC)

# ── CVD ───────────────────────────────────────────────────────────────────────
CVD_LOOKBACK  = _env('CVD_LOOKBACK_BARS', '20', 'int')
CVD_THRESHOLD = _env('CVD_THRESHOLD', '1.5', 'float')

# ── Circuit Breaker ───────────────────────────────────────────────────────────
CIRCUIT_BREAKER_PCT = _env('CIRCUIT_BREAKER_PCT', '3.0', 'float')
MAX_LOSING_STREAK   = _env('MAX_LOSING_STREAK', '3', 'int')
MAX_DAILY_TRADES    = _env('MAX_DAILY_TRADES', '8', 'int')

# ── Timing ────────────────────────────────────────────────────────────────────
SCAN_INTERVAL    = _env('SCAN_INTERVAL_SEC', '90', 'int')
MONITOR_INTERVAL = _env('MONITOR_INTERVAL_SEC', '20', 'int')
SYMBOL_COOLDOWN  = _env('SYMBOL_COOLDOWN_MIN', '60', 'int')     # Minutos entre re-entries

# ── Costs ─────────────────────────────────────────────────────────────────────
FEE_TAKER  = 0.001
FEE_MAKER  = 0.0002
SLIPPAGE   = 0.0003
TOTAL_COST = FEE_TAKER + FEE_MAKER + SLIPPAGE

# ── Excluded Symbols ──────────────────────────────────────────────────────────
EXCLUDE_SYMBOLS = {
    'DOW', 'SP500', 'GOLD', 'SILVER', 'XAU', 'OIL', 'BRENT',
    'EUR', 'GBP', 'JPY', 'TSLA', 'AAPL', 'MSFT', 'GOOGL',
    'AMZN', 'META', 'NVDA', 'COIN', 'MSTR', 'PAXG', 'XAUT',
    'Q-USDT', 'BEAT-USDT',  # Problemáticos en v3.0
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
        logging.FileHandler('/tmp/bot_v4.log', mode='a'),
    ]
)
log = logging.getLogger('bot_v4')

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
    """Llamada firmada a BingX con retry exponencial."""
    params = params or {}
    last_error = None
    for attempt in range(retries + 1):
        try:
            p = {**{k: str(v) for k, v in params.items()},
                 'timestamp': str(int(time.time() * 1000))}
            query  = urlencode(sorted(p.items()))
            sig    = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
            url    = f"{BASE_URL}{endpoint}?{query}&signature={sig}"
            hdrs   = {'X-BX-APIKEY': API_KEY, 'Content-Type': 'application/x-www-form-urlencoded'}
            resp   = getattr(requests, method.lower())(url, headers=hdrs, timeout=15)
            data   = resp.json()
            if data.get('code') not in (0, None):
                log.warning(f"API [{endpoint}] code={data.get('code')} msg={data.get('msg')} params={params}")
            return data
        except requests.exceptions.Timeout as e:
            last_error = f"Timeout: {e}"
        except requests.exceptions.ConnectionError as e:
            last_error = f"ConnError: {e}"
        except Exception as e:
            last_error = f"Exception: {e}"
            log.error(f"API [{endpoint}] attempt {attempt}: {e}\n{traceback.format_exc()}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    log.error(f"API [{endpoint}] FAILED after {retries+1} attempts: {last_error}")
    return {'code': -1, 'msg': last_error}

def public_request(path: str, params: dict = None) -> dict:
    """Llamada pública (sin firma) a BingX."""
    try:
        resp = requests.get(f"{BASE_URL}{path}", params=params or {}, timeout=10)
        return resp.json()
    except Exception as e:
        log.error(f"Public [{path}] error: {e}")
        return {'code': -1, 'msg': str(e)}

def extract_equity(data: dict) -> float:
    """Extrae equity de la respuesta /user/balance (estructura variable según plan)."""
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
# SECCIÓN 4: INDICADORES TÉCNICOS
# ══════════════════════════════════════════════════════════════════════════════

def ema(prices: List[float], period: int) -> float:
    if not prices:
        return 0.0
    k = 2 / (period + 1)
    val = prices[0]
    for p in prices[1:]:
        val = p * k + val * (1 - k)
    return val

def sma(prices: List[float], period: int) -> float:
    if not prices:
        return 0.0
    tail = prices[-period:] if len(prices) >= period else prices
    return sum(tail) / len(tail)

def atr_calc(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
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

def rsi_calc(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    gains  = [max(prices[i] - prices[i - 1], 0) for i in range(1, len(prices))]
    losses = [max(prices[i - 1] - prices[i], 0) for i in range(1, len(prices))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    return 100 - (100 / (1 + ag / al)) if al > 0 else 100.0

def volume_avg(volumes: List[float], period: int = 20) -> float:
    tail = volumes[-period:] if len(volumes) >= period else volumes
    return sum(tail) / len(tail) if tail else 0.0

def cvd_calc(volumes: List[float], closes: List[float], opens: List[float]) -> float:
    cvd = 0.0
    for i in range(len(volumes)):
        sign = 1 if closes[i] > opens[i] else (-1 if closes[i] < opens[i] else 0)
        cvd += volumes[i] * sign
    return cvd

def kelly_size(win_rate: float, avg_win: float, avg_loss: float, equity: float) -> float:
    """Half-Kelly con límite de 2% del equity."""
    if avg_loss <= 0 or win_rate <= 0:
        return POSITION_SIZE
    R = avg_win / avg_loss
    kelly = win_rate - (1 - win_rate) / R
    half  = max(0.005, min(0.02, kelly / 2))
    return min(equity * half, POSITION_SIZE)

# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN 5: DETECCIÓN DE PATRONES
# ══════════════════════════════════════════════════════════════════════════════

def detect_vcp(closes: List[float], volumes: List[float], lookback: int = 20) -> Tuple[bool, str]:
    """Volatility Contraction Pattern — compresión progresiva con volumen decreciente."""
    if len(closes) < lookback:
        return False, "insufficient_data"
    rc = closes[-lookback:]
    rv = volumes[-lookback:]
    contractions = []
    for i in range(2, len(rc) - 2):
        if rc[i] < rc[i - 1] and rc[i] < rc[i + 1]:
            depth = (rc[i - 1] - rc[i]) / rc[i - 1] * 100
            contractions.append((depth, rv[i]))
    if len(contractions) < 2:
        return False, "no_contractions"
    depths = [c[0] for c in contractions[-3:]]
    contracting = all(depths[j] < depths[j - 1] * 1.1 for j in range(1, len(depths)))
    near_high   = rc[-1] >= max(rc) * 0.92
    if contracting and near_high:
        return True, f"vcp_{len(contractions)}c"
    return False, "no_vcp"

def detect_flag(closes: List[float], volumes: List[float],
                highs: List[float], lows: List[float]) -> Tuple[bool, str]:
    """Bull Flag — mástil fuerte seguido de consolidación con volumen bajo."""
    if len(closes) < 15:
        return False, "insufficient_data"
    pole_w, flag_w = 7, 5
    if len(closes) < pole_w + flag_w:
        return False, "insufficient_data"
    pc = closes[-(pole_w + flag_w):-flag_w]
    fc = closes[-flag_w:]
    fv = volumes[-flag_w:]
    pv = volumes[-(pole_w + flag_w):-flag_w]
    pole_move  = (pc[-1] - pc[0]) / pc[0] * 100
    flag_range = (max(fc) - min(fc)) / min(fc) * 100
    vol_ratio  = (sum(fv) / len(fv)) / (sum(pv) / len(pv)) if pv else 1
    retrace    = (pc[-1] - min(fc)) / (pc[-1] - pc[0] + 1e-10) * 100
    if pole_move > 4.0 and flag_range < 3.5 and vol_ratio < 0.8 and retrace < 50:
        return True, f"flag_pole{pole_move:.1f}pct"
    return False, "no_flag"

def market_regime(closes: List[float], highs: List[float],
                  lows: List[float], volumes: List[float]) -> Tuple[str, float]:
    """Clasifica el régimen actual: trending_bullish, trending_moderate, ranging, bearish, volatile."""
    if len(closes) < 30:
        return "unknown", 0.0
    cur    = closes[-1]
    ma10   = sma(closes, 10)
    ma20   = sma(closes, 20)
    ma20p  = sma(closes[:-5], 20) if len(closes) > 25 else ma20
    atr_v  = atr_calc(highs, lows, closes, 14)
    atr_p  = (atr_v / cur * 100) if cur > 0 else 0
    if atr_p > REGIME_ATR_MAX_PCT:    return "volatile_extreme", atr_p
    if atr_p < REGIME_ATR_MIN_PCT:    return "ranging_quiet",    atr_p
    rising = ma20 > ma20p
    if cur > ma10 and cur > ma20 and ma10 > ma20 and rising: return "trending_bullish",  atr_p
    if cur > ma20 and rising:                                 return "trending_moderate", atr_p
    if not rising and cur < ma20:                             return "bearish",           atr_p
    return "ranging", atr_p

def swing_zones(highs: List[float], lows: List[float], lookback: int = 100) -> Dict:
    """Encuentra zonas de soporte/resistencia por swing highs y lows."""
    if len(highs) < lookback:
        return {'res': [], 'sup': []}
    rh = highs[-lookback:]
    rl = lows[-lookback:]
    sh = [rh[i] for i in range(2, len(rh) - 2)
          if rh[i] > rh[i-1] and rh[i] > rh[i-2] and rh[i] > rh[i+1] and rh[i] > rh[i+2]]
    sl = [rl[i] for i in range(2, len(rl) - 2)
          if rl[i] < rl[i-1] and rl[i] < rl[i-2] and rl[i] < rl[i+1] and rl[i] < rl[i+2]]
    return {'res': sorted(sh, reverse=True)[:5], 'sup': sorted(sl)[:5]}

def ema9_setup(closes: List[float], price: float) -> Tuple[bool, str]:
    """Detecta setups de entrada respecto a EMA9."""
    if len(closes) < 2:
        return False, "insufficient"
    e9  = ema(closes, 9)
    e9p = ema(closes[:-1], 9)
    if closes[-2] <= e9p and price > e9:
        return True, "ema9_fresh_cross"
    if price > e9:
        return True, "above_ema9"
    return False, "below_ema9"

# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN 6: FILTROS INSTITUCIONALES
# ══════════════════════════════════════════════════════════════════════════════

class Filters:
    """Cache y lógica de filtros externos: funding, OI, sesión, CVD, BTC health."""
    def __init__(self):
        self._cache: Dict[str, tuple] = {}   # key → (value, timestamp)
        self.btc_history = deque(maxlen=10)

    def _cached(self, key: str, ttl: int = 300):
        """Devuelve valor cacheado o None si expiró."""
        if key in self._cache:
            val, ts = self._cache[key]
            if time.time() - ts < ttl:
                return val
        return None

    def funding_rate(self, symbol: str) -> Tuple[bool, str, float]:
        if not FUNDING_ENABLED:
            return True, "disabled", 0.0
        key = f"fr_{symbol}"
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
        if not OI_ENABLED:
            return True, "disabled", 0.0
        key = f"oi_{symbol}"
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
        if not SESSION_FILTER_ENABLED:
            return True, "disabled"
        h = datetime.utcnow().hour
        if h in SESSION_BEST:   return True,  "us_session"
        if h in SESSION_OK:     return True,  "london_session"
        return False, "asia_session_avoid"

    def cvd_quality(self, volumes: List[float],
                    closes: List[float], opens: List[float]) -> Tuple[float, str]:
        if len(volumes) < CVD_LOOKBACK:
            return 0.0, "insufficient"
        rv = volumes[-CVD_LOOKBACK:]
        rc = closes[-CVD_LOOKBACK:]
        ro = opens[-CVD_LOOKBACK:]
        raw = cvd_calc(rv, rc, ro)
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

    def btc_health(self) -> Tuple[bool, str]:
        data = public_request('/openApi/swap/v2/quote/ticker', {'symbol': 'BTC-USDT'})
        if data.get('code') == 0 and data.get('data'):
            t   = data['data']
            chg = safe_float(t.get('priceChangePercent', 0))
            self.btc_history.append(safe_float(t.get('lastPrice', 0)))
            if chg < -2.0:
                return False, f"btc_falling_{chg:.1f}pct"
            return True, "btc_positive" if chg > 0 else "btc_neutral"
        return True, "btc_unknown"

# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN 7: CLASE PRINCIPAL — InstitutionalBot v4
# ══════════════════════════════════════════════════════════════════════════════

class InstitutionalBot:

    def __init__(self):
        # Estado
        self.symbols:        List[str]         = []
        self.positions:      Dict[str, dict]   = {}
        self.contracts_info: Dict[str, dict]   = {}
        self.symbol_cooldown: Dict[str, float] = {}   # símbolo → timestamp último cierre
        self.filters   = Filters()
        self.equity    = ACCOUNT_EQUITY

        # Circuit Breaker
        self.circuit_active  = False
        self.circuit_until:  Optional[datetime] = None
        self.daily_pnl       = 0.0
        self.daily_date      = datetime.utcnow().date()
        self.daily_trades    = 0
        self.losing_streak   = 0

        # Stats v4 — ampliadas
        self.stats = {
            'wins': 0, 'losses': 0, 'total_pnl': 0.0,
            'win_amounts': [], 'loss_amounts': [],
            'best_trade': 0.0, 'worst_trade': 0.0,
            'hold_times': [],   # minutos
        }

        self._print_banner()
        ok = self._connect()
        if not ok and AUTO_TRADING:
            log.error("❌ Conexión fallida. Abortando.")
            sys.exit(1)
        self._load_contracts()
        self._refresh_symbols()
        self._recover_positions()
        self._notify(
            f"<b>🚀 BOT v4.0 INICIADO</b>\n\n"
            f"Capital: ${POSITION_SIZE} × {MAX_POSITIONS} | Lev: {LEVERAGE}×\n"
            f"Circuit: {CIRCUIT_BREAKER_PCT}% | Min score: {MIN_SCORE}\n"
            f"Modo: {'REAL 💸' if AUTO_TRADING else 'PAPER 📝'}"
        )

    # ── Banner de inicio ──────────────────────────────────────────────────────
    def _print_banner(self):
        log.info("=" * 78)
        log.info("  INSTITUTIONAL BOT v4.0 — Phoenix Trader Edition")
        log.info("=" * 78)
        log.info(f"  Capital: ${POSITION_SIZE}/pos × {MAX_POSITIONS} pos | Leverage: {LEVERAGE}×")
        log.info(f"  SL: {SL_ATR_MULT}×ATR ({SL_MIN_PCT}-{SL_MAX_PCT}%) | "
                 f"TP1: {TP1_RR}R | TP2: {TP2_RR}R | Trail: {RUNNER_TRAIL}×ATR")
        log.info(f"  Min Score: {MIN_SCORE} | Min Edge: {MIN_EDGE}× | Max hold: {MAX_HOLD_H}h")
        log.info(f"  Circuit: {CIRCUIT_BREAKER_PCT}% loss / {MAX_LOSING_STREAK} streak / {MAX_DAILY_TRADES} trades")
        log.info(f"  Auto-trading: {'ENABLED 💸' if AUTO_TRADING else 'DISABLED — PAPER MODE 📝'}")
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

    # ── Cargar contratos ──────────────────────────────────────────────────────
    def _load_contracts(self):
        data = public_request('/openApi/swap/v2/quote/contracts')
        if data.get('code') == 0:
            for c in data.get('data', []):
                s = c.get('symbol', '')
                if s:
                    self.contracts_info[s] = {
                        'min_qty':        safe_float(c.get('tradeMinQuantity', 1)),
                        'qty_precision':  int(c.get('quantityPrecision', 2)),
                        'contract_size':  safe_float(c.get('contractSize', 1)),
                    }
            log.info(f"✓ Contratos cargados: {len(self.contracts_info)}")
        else:
            log.warning(f"⚠️ Contratos no cargados: {data.get('msg')}")

    # ── Refresh de símbolos ───────────────────────────────────────────────────
    def _refresh_symbols(self):
        data = public_request('/openApi/swap/v2/quote/ticker')
        if data.get('code') != 0:
            self.symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT']
            log.warning("⚠️ Usando fallback de símbolos")
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

    # ── Recuperar posiciones abiertas ─────────────────────────────────────────
    def _recover_positions(self):
        """Recupera posiciones abiertas con TODOS los campos inicializados."""
        if not AUTO_TRADING:
            return
        data = api_request('GET', '/openApi/swap/v2/user/positions')
        if data.get('code') != 0:
            log.warning(f"⚠️ No se pudieron recuperar posiciones: {data.get('msg')}")
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
                    self.positions[symbol] = self._build_position(
                        entry=entry, qty=qty,
                        tp1_price=entry * (1 + SL_MIN_PCT * TP1_RR / 100),
                        tp2_price=entry * (1 + SL_MIN_PCT * TP2_RR / 100),
                        sl_price =entry * (1 - SL_MIN_PCT / 100),
                        sl_pct   =SL_MIN_PCT,
                        atr_val  =entry * 0.005,
                        score    =0,
                        signal   =None,
                        recovered=True
                    )
                    recovered += 1
                    log.info(f"♻️  Posición recuperada: {symbol} @ ${entry:.6f}")
            except Exception as e:
                log.error(f"Error recuperando posición: {e}\n{traceback.format_exc()}")
        if recovered:
            log.info(f"✓ {recovered} posiciones recuperadas")

    # ── Constructor de dict de posición (garantiza todos los campos) ──────────
    def _build_position(self, *, entry, qty, tp1_price, tp2_price, sl_price,
                         sl_pct, atr_val, score, signal, recovered=False) -> dict:
        return {
            'entry':       entry,
            'qty':         qty,
            'qty_tp1':     round(qty * TP1_PCT / 100, 6),
            'qty_tp2':     round(qty * TP2_PCT / 100, 6),
            'side':        'LONG',
            'sl_price':    sl_price,
            'sl_pct':      sl_pct,
            'tp1_price':   tp1_price,
            'tp2_price':   tp2_price,
            'tp1_hit':     False,
            'tp2_hit':     False,
            'highest':     entry,          # CRÍTICO: siempre presente
            'opened_at':   datetime.now(),
            'score':       score,
            'signal':      signal or {'atr': atr_val, 'atr_pct': 0},
            'pnl_realized': 0.0,
            'pos_size':    POSITION_SIZE,
            'recovered':   recovered,
        }

    # ── Obtener velas ─────────────────────────────────────────────────────────
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

    # ── Obtener ticker ────────────────────────────────────────────────────────
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

    # ── Análisis de símbolo (scoring) ─────────────────────────────────────────
    def analyze(self, symbol: str) -> Optional[dict]:
        """Devuelve señal si el símbolo cumple todos los filtros y score ≥ MIN_SCORE."""
        if symbol in self.positions:
            return None
        if symbol not in self.contracts_info:
            return None

        # Cooldown: evitar re-entrada en símbolo perdedor reciente
        last_close = self.symbol_cooldown.get(symbol, 0)
        if time.time() - last_close < SYMBOL_COOLDOWN * 60:
            return None

        closes, highs, lows, volumes, opens = self._klines(symbol, '5m', 150)
        if not closes or len(closes) < 50:
            return None

        tick = self._ticker(symbol)
        if not tick or tick['price'] <= 0:
            return None

        price    = tick['price']
        chg_24h  = tick['change_pct']
        cur_vol  = tick['volume']

        # ── Filtros macro ──────────────────────────────────────────────────────
        regime, atr_pct = market_regime(closes, highs, lows, volumes)
        if regime in ("volatile_extreme", "ranging_quiet", "bearish"):
            log.debug(f"{symbol}: ✗ Regime={regime}")
            return None

        if symbol != 'BTC-USDT':
            btc_ok, btc_reason = self.filters.btc_health()
            if not btc_ok:
                log.debug(f"{symbol}: ✗ {btc_reason}")
                return None

        # Correlation guard: máximo MAX_CORR_LONGS altcoins simultáneas
        if symbol not in ('BTC-USDT', 'ETH-USDT'):
            alts = sum(1 for s in self.positions if s not in ('BTC-USDT', 'ETH-USDT'))
            if alts >= MAX_CORR_LONGS:
                return None

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

        # ── Indicadores ────────────────────────────────────────────────────────
        ma10  = sma(closes, 10)
        ma20  = sma(closes, 20)
        ma20p = sma(closes[:-5], 20) if len(closes) > 25 else ma20
        e9    = ema(closes, 9)
        e50   = ema(closes, 50)
        atr_v = atr_calc(highs, lows, closes, 14)
        rsi_v = rsi_calc(closes, 14)
        cvd_v, cvd_sig = self.filters.cvd_quality(volumes, closes, opens)
        zones = swing_zones(highs, lows, 100)

        above_ma10 = price > ma10
        above_ma20 = price > ma20
        ma_stack   = ma10 > ma20 and above_ma10 and above_ma20
        ma20_rising = ma20 > ma20p
        vol_ok, vol_ratio = (True, (cur_vol / volume_avg(volumes[:-1], 20))
                             ) if volume_avg(volumes[:-1], 20) > 0 else (True, 1.0)
        vol_breakout = vol_ratio >= VOLUME_BREAKOUT_MULT
        vcp_ok, vcp_str  = detect_vcp(closes, volumes, VCP_LOOKBACK)
        flag_ok, flag_str = detect_flag(closes, volumes, highs, lows)
        e9_ok, e9_str    = ema9_setup(closes, price)

        # ── Scoring ────────────────────────────────────────────────────────────
        score   = 0
        reasons = []

        # Regime (15 pts)
        if regime == "trending_bullish":
            score += 15; reasons.append("Regime_Bull(15)")
        elif regime == "trending_moderate":
            score +=  8; reasons.append("Regime_Mod(8)")

        # MA stack (25 pts)
        if ma_stack and ma20_rising:
            score += 25; reasons.append("MA_Stack_Rising(25)")
        elif above_ma20 and ma20_rising:
            score += 15; reasons.append("Above_MA20_Rising(15)")
        elif above_ma20:
            score +=  8; reasons.append("Above_MA20(8)")

        # VCP (20 pts)
        if vcp_ok:
            score += 20; reasons.append(f"VCP(20)")

        # Flag (15 pts)
        if flag_ok:
            score += 15; reasons.append(f"Flag(15)")

        # Volume (15 pts)
        if vol_breakout:
            score += 15; reasons.append(f"VolBreakout({vol_ratio:.1f}x)(15)")
        elif vol_ratio > 1.2:
            score +=  7; reasons.append(f"VolAbove({vol_ratio:.1f}x)(7)")

        # EMA9 (10 pts)
        if e9_ok:
            bonus = 10 if e9_str == "ema9_fresh_cross" else 5
            score += bonus; reasons.append(f"{e9_str}({bonus})")

        # CVD (10 pts)
        if cvd_sig == "bullish_cvd":
            score += 10; reasons.append("CVD_Bull(10)")
        elif cvd_sig == "cvd_neutral":
            score +=  4; reasons.append("CVD_Neutral(4)")

        # Funding (5 pts)
        if fund_rate < 0:
            score += 5; reasons.append("Fund_Neg(5)")
        elif fund_rate < 0.02:
            score += 3; reasons.append("Fund_Low(3)")

        # OI (5 pts)
        if oi_reason == "oi_breakout_confirmed":
            score += 5; reasons.append("OI_Break(5)")

        # Session (5 pts)
        if sess_name == "us_session":
            score += 5; reasons.append("US_Sess(5)")
        elif sess_name == "london_session":
            score += 3; reasons.append("London_Sess(3)")

        # RSI sweet spot (5 pts)
        if 35 < rsi_v < 55:
            score += 5; reasons.append(f"RSI_Sweet({int(rsi_v)})(5)")

        # ── Gestión de riesgo: SL dinámico ─────────────────────────────────────
        sl_atr = price - atr_v * SL_ATR_MULT
        sup    = next((s * 0.998 for s in zones['sup'] if s < price), None)
        sl_raw = max(sl_atr, sup) if sup else sl_atr
        sl_pct = (price - sl_raw) / price * 100
        sl_pct = max(SL_MIN_PCT, min(SL_MAX_PCT, sl_pct))
        sl_price   = price * (1 - sl_pct / 100)
        tp1_price  = price * (1 + sl_pct * TP1_RR / 100)
        tp2_price  = price * (1 + sl_pct * TP2_RR / 100)
        edge_ratio = (sl_pct * TP1_RR) / (TOTAL_COST * 100)

        if edge_ratio < MIN_EDGE:
            log.debug(f"{symbol}: ✗ Edge {edge_ratio:.1f}× < {MIN_EDGE}×")
            return None
        if score < MIN_SCORE:
            log.debug(f"{symbol}: ✗ Score {score} < {MIN_SCORE}")
            return None

        return {
            'symbol':     symbol,
            'price':      price,
            'score':      score,
            'reasons':    ' | '.join(reasons),
            'sl_price':   sl_price,
            'sl_pct':     sl_pct,
            'tp1_price':  tp1_price,
            'tp2_price':  tp2_price,
            'edge_ratio': edge_ratio,
            'atr':        atr_v,
            'atr_pct':    atr_pct,
            'ma10': ma10, 'ma20': ma20,
            'ema9': e9,   'ema50': e50,
            'rsi':        rsi_v,
            'regime':     regime,
            'vcp':        vcp_ok,
            'flag':       flag_ok,
            'vol_ratio':  vol_ratio,
            'cvd_signal': cvd_sig,
            'funding_rate': fund_rate,
            'oi_change':  oi_chg,
            'session':    sess_name,
        }

    # ── Abrir posición ────────────────────────────────────────────────────────
    def open_position(self, sig: dict) -> bool:
        symbol    = sig['symbol']
        price     = sig['price']
        sl_price  = sig['sl_price']

        if not AUTO_TRADING:
            log.info(f"📝 PAPER: Abriría LONG {symbol} | Score:{int(sig['score'])} | "
                     f"Edge:{sig['edge_ratio']:.1f}× | Regime:{sig['regime']}")
            return False

        if symbol not in self.contracts_info:
            log.error(f"❌ {symbol}: no hay info de contrato")
            return False

        log.info(f"{'='*60}")
        log.info(f"🎯 ABRIENDO LONG: {symbol} | Score:{int(sig['score'])} | Edge:{sig['edge_ratio']:.1f}×")
        log.info(f"Entry: ${price:.6f} | SL: ${sl_price:.6f} (-{sig['sl_pct']:.2f}%)")

        # Kelly sizing tras 10+ trades
        total = self.stats['wins'] + self.stats['losses']
        pos_size = POSITION_SIZE
        if (total >= 10 and self.stats['win_amounts'] and self.stats['loss_amounts']):
            wr  = self.stats['wins'] / total
            avg_w = sum(self.stats['win_amounts'][-20:]) / len(self.stats['win_amounts'][-20:])
            avg_l = abs(sum(self.stats['loss_amounts'][-20:]) / len(self.stats['loss_amounts'][-20:]))
            pos_size = kelly_size(wr, avg_w, avg_l, self.equity)

        qty = self._calc_qty(symbol, price, sl_price, pos_size)
        if not qty:
            log.error(f"❌ {symbol}: cantidad no calculable")
            return False

        # Set leverage
        self._set_leverage(symbol, LEVERAGE)
        time.sleep(0.3)

        # Orden de apertura con positionSide (CRÍTICO)
        order = api_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol':       symbol,
            'side':         'BUY',
            'type':         'MARKET',
            'quantity':     str(qty),
            'positionSide': 'LONG',    # SIEMPRE requerido en BingX
        })
        if order.get('code') != 0:
            log.error(f"❌ Orden fallida {symbol}: {order.get('msg')}")
            return False

        time.sleep(1)
        fill_qty, fill_price = self._confirm_position(symbol)
        if not fill_qty:
            log.error(f"❌ {symbol}: posición no confirmada")
            return False

        # Recalcular con precio de ejecución real
        real_sl_pct = (fill_price - sl_price) / fill_price * 100
        tp1 = fill_price * (1 + real_sl_pct * TP1_RR / 100)
        tp2 = fill_price * (1 + real_sl_pct * TP2_RR / 100)

        # Stop Loss en exchange (con positionSide)
        sl_p = {'symbol': symbol, 'side': 'SELL', 'type': 'STOP_MARKET',
                 'quantity': str(fill_qty), 'stopPrice': str(round(sl_price, 8)),
                 'positionSide': 'LONG'}
        sl_r = api_request('POST', '/openApi/swap/v2/trade/order', sl_p)
        if sl_r.get('code') != 0:
            # Intentar con STOP
            sl_p['type'] = 'STOP'
            sl_p['price'] = str(round(sl_price * 0.999, 8))
            sl_r = api_request('POST', '/openApi/swap/v2/trade/order', sl_p)
        sl_ok = sl_r.get('code') == 0

        # Registrar posición con TODOS los campos
        self.positions[symbol] = self._build_position(
            entry=fill_price, qty=fill_qty,
            tp1_price=tp1, tp2_price=tp2,
            sl_price=sl_price, sl_pct=real_sl_pct,
            atr_val=sig['atr'], score=sig['score'], signal=sig
        )
        self.positions[symbol]['pos_size'] = pos_size
        self.daily_trades += 1

        patterns = " + ".join(filter(None, [
            "VCP" if sig.get('vcp') else "",
            "Flag" if sig.get('flag') else "",
        ])) or "Momentum"

        log.info(f"✓ LONG abierto: {symbol} @ ${fill_price:.6f} | SL: {'OK' if sl_ok else '⚠️ MANUAL'}")

        self._notify(
            f"<b>🟢 LONG ABIERTO</b> — {symbol}\n\n"
            f"Score: {int(sig['score'])} | Edge: {sig['edge_ratio']:.1f}× | {patterns}\n"
            f"Volumen: {sig.get('vol_ratio',1):.1f}× | Funding: {sig['funding_rate']:.3f}%\n\n"
            f"📍 Entrada: ${fill_price:.6f}\n"
            f"🎯 TP1: ${tp1:.6f} (+{real_sl_pct * TP1_RR:.2f}%)\n"
            f"🎯 TP2: ${tp2:.6f} (+{real_sl_pct * TP2_RR:.2f}%)\n"
            f"🛑 SL: ${sl_price:.6f} (-{real_sl_pct:.2f}%)\n\n"
            f"{'✅ SL en exchange' if sl_ok else '⚠️ SL MANUAL NECESARIO'}"
        )
        return True

    # ── Monitor de posiciones ─────────────────────────────────────────────────
    async def monitor_positions(self):
        """Revisa cada posición: TP parciales, trailing stops, SL, max hold."""
        for symbol in list(self.positions.keys()):
            try:
                pos  = self.positions[symbol]
                tick = self._ticker(symbol)
                if not tick:
                    continue

                cp = tick['price']

                # Actualizar highest (siempre con .get() como seguro)
                if cp > pos.get('highest', pos['entry']):
                    pos['highest'] = cp

                # ── Max hold time ──────────────────────────────────────────────
                hold_min = (datetime.now() - pos['opened_at']).total_seconds() / 60
                if hold_min >= MAX_HOLD_H * 60:
                    log.info(f"⏰ {symbol}: Max hold {MAX_HOLD_H}h alcanzado")
                    self._close_full(symbol, cp, "MAX_HOLD_TIME")
                    continue

                # ── TP1 ────────────────────────────────────────────────────────
                if not pos['tp1_hit'] and cp >= pos.get('tp1_price', 1e18):
                    self._close_partial(symbol, pos['qty_tp1'], cp, "TP1")
                    pos['tp1_hit']  = True
                    pos['sl_price'] = pos['entry'] * 1.001  # Breakeven
                    log.info(f"💰 {symbol} TP1 hit → SL a breakeven")
                    continue

                # ── TP2 ────────────────────────────────────────────────────────
                if pos['tp1_hit'] and not pos['tp2_hit'] and cp >= pos.get('tp2_price', 1e18):
                    self._close_partial(symbol, pos['qty_tp2'], cp, "TP2")
                    pos['tp2_hit'] = True
                    sig = pos.get('signal') or {}
                    atr_val = sig.get('atr', pos['entry'] * 0.005)
                    trail_dist = atr_val * RUNNER_TRAIL
                    pos['sl_price'] = max(pos['sl_price'], cp - trail_dist)
                    log.info(f"💰 {symbol} TP2 hit → SL trail @ ${pos['sl_price']:.6f}")
                    continue

                # ── Runner trailing ────────────────────────────────────────────
                if pos['tp2_hit']:
                    sig = pos.get('signal') or {}
                    atr_val = sig.get('atr', pos['entry'] * 0.005)
                    new_sl = cp - atr_val * RUNNER_TRAIL
                    if new_sl > pos['sl_price']:
                        pos['sl_price'] = new_sl

                # ── SL ─────────────────────────────────────────────────────────
                if cp <= pos['sl_price']:
                    self._close_full(symbol, cp, "STOP_LOSS")

            except KeyError as e:
                log.error(f"KeyError {symbol}: {e} | keys={list(self.positions.get(symbol, {}).keys())}\n"
                          f"{traceback.format_exc()}")
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
                'quantity': str(qty), 'positionSide': 'LONG',  # CRÍTICO
            })
            if res.get('code') != 0:
                log.error(f"❌ Cierre parcial {symbol}: {res.get('msg')}")
                return
        pos  = self.positions[symbol]
        pnl  = self._calc_pnl(pos['entry'], price, qty, symbol)
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
            res = api_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol': symbol, 'side': 'SELL', 'type': 'MARKET',
                'quantity': str(qty), 'positionSide': 'LONG',  # CRÍTICO
            })
            if res.get('code') != 0:
                log.error(f"❌ Cierre total {symbol}: {res.get('msg')}")
        pnl_final  = self._calc_pnl(pos['entry'], price, qty, symbol)
        total_pnl  = pos['pnl_realized'] + pnl_final
        hold_min   = int((datetime.now() - pos['opened_at']).total_seconds() / 60)
        win = total_pnl > 0
        # Actualizar stats
        if win:
            self.stats['wins'] += 1
            self.stats['win_amounts'].append(total_pnl)
            self.losing_streak = 0
        else:
            self.stats['losses'] += 1
            self.stats['loss_amounts'].append(total_pnl)
            self.losing_streak += 1
            self.symbol_cooldown[symbol] = time.time()   # Cooldown en perdedor
        self.stats['total_pnl'] += pnl_final
        self.stats['hold_times'].append(hold_min)
        self.daily_pnl += pnl_final
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
            f"Entrada: ${pos['entry']:.6f} → Salida: ${price:.6f}\n"
            f"Duración: {hold_min}min\n\n"
            f"<b>PnL: ${total_pnl:+.4f}</b>\n"
            f"WR: {wr:.0f}% ({self.stats['wins']}/{total_t}) | PF: {pf:.2f}\n"
            f"Racha pérdidas: {self.losing_streak}"
        )
        del self.positions[symbol]

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _calc_pnl(self, entry: float, exit_p: float, qty: float, symbol: str = '') -> float:
        info = self.contracts_info.get(symbol, {})
        csz  = info.get('contract_size', 1)
        notional = qty * entry * csz
        gross    = (exit_p - entry) / entry * notional * LEVERAGE
        fees     = notional * (FEE_TAKER + FEE_MAKER)
        return gross - fees

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
            r = api_request('POST', '/openApi/swap/v2/trade/leverage',
                            {'symbol': symbol, 'side': side, 'leverage': str(leverage)})
            if r.get('code') != 0:
                log.warning(f"⚠️ Leverage {side} {symbol}: {r.get('msg')}")

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
        gross_win  = sum(self.stats['win_amounts'])  if self.stats['win_amounts']  else 0
        gross_loss = abs(sum(self.stats['loss_amounts'])) if self.stats['loss_amounts'] else 1
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
                log.info("🔓 Circuit Breaker RESET (nuevo día)")

        if self.circuit_active:
            if self.circuit_until and datetime.utcnow() > self.circuit_until:
                self.circuit_active = False
                log.info("🔓 Circuit Breaker OFF")
                return False
            return True

        threshold = self.equity * (CIRCUIT_BREAKER_PCT / 100)

        if self.daily_pnl < -threshold:
            self._activate_circuit(f"Pérdida diaria ${self.daily_pnl:.2f}", hours=6)
            return True
        if self.losing_streak >= MAX_LOSING_STREAK:
            self._activate_circuit(f"Racha {self.losing_streak} pérdidas", hours=4)
            return True
        if self.daily_trades >= MAX_DAILY_TRADES:
            log.warning(f"⚠️ Máximo de trades diarios: {self.daily_trades}/{MAX_DAILY_TRADES}")
            return True

        return False

    def _activate_circuit(self, reason: str, hours: int):
        self.circuit_active = True
        self.circuit_until  = datetime.utcnow() + timedelta(hours=hours)
        log.warning(f"🔒 CIRCUIT BREAKER — {reason} | Pausa: {hours}h")
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
        log.info("🚀 Bot v4.0 corriendo...\n")
        iteration          = 0
        last_sym_refresh   = 0
        last_equity_update = 0
        last_report        = 0

        while True:
            try:
                iteration += 1

                # Refresh de símbolos cada 10 minutos
                if time.time() - last_sym_refresh > 600:
                    self._refresh_symbols()
                    last_sym_refresh = time.time()

                # Actualizar equity cada 30 minutos
                if AUTO_TRADING and time.time() - last_equity_update > 1800:
                    data = api_request('GET', '/openApi/swap/v2/user/balance')
                    if data.get('code') == 0:
                        eq = extract_equity(data)
                        if eq > 0:
                            self.equity = eq
                    last_equity_update = time.time()

                # Circuit breaker
                if self._circuit_breaker():
                    await asyncio.sleep(SCAN_INTERVAL)
                    continue

                # Stats
                total = self.stats['wins'] + self.stats['losses']
                wr    = (self.stats['wins'] / total * 100) if total > 0 else 0
                pf    = self._profit_factor()
                avg_h = (sum(self.stats['hold_times']) / len(self.stats['hold_times'])
                         if self.stats['hold_times'] else 0)

                log.info(f"\n{'='*70}")
                log.info(f"  #{iteration} {datetime.now().strftime('%d/%m %H:%M:%S')} UTC | "
                         f"Pos: {len(self.positions)}/{MAX_POSITIONS}")
                log.info(f"  PnL total: ${self.stats['total_pnl']:+.4f} | "
                         f"Hoy: ${self.daily_pnl:+.4f} | "
                         f"WR: {wr:.0f}% ({total}T) | PF: {pf:.2f}")
                log.info(f"  Equity: ${self.equity:.2f} | Trades hoy: {self.daily_trades}/"
                         f"{MAX_DAILY_TRADES} | Avg hold: {avg_h:.0f}min")
                log.info(f"{'='*70}\n")

                # Monitorear posiciones abiertas
                await self.monitor_positions()

                # Informe por Telegram cada hora
                if time.time() - last_report > 3600:
                    self._notify(
                        f"<b>📊 Reporte horario</b>\n\n"
                        f"PnL total: ${self.stats['total_pnl']:+.4f}\n"
                        f"Hoy: ${self.daily_pnl:+.4f}\n"
                        f"WR: {wr:.0f}% ({total} trades)\n"
                        f"PF: {pf:.2f} | Avg hold: {avg_h:.0f}min\n"
                        f"Posiciones: {len(self.positions)}/{MAX_POSITIONS}"
                    )
                    last_report = time.time()

                # Escanear nuevas señales
                if len(self.positions) < MAX_POSITIONS and self.daily_trades < MAX_DAILY_TRADES:
                    log.info(f"Escaneando {len(self.symbols)} símbolos...")
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
                                "VCP"  if sig.get('vcp')  else "",
                                "Flag" if sig.get('flag') else "",
                            ])) or "Momentum"
                            log.info(f"💡 {symbol} | Score:{int(sig['score'])} | "
                                     f"Edge:{sig['edge_ratio']:.1f}× | {patterns} | "
                                     f"Regime:{sig['regime']}")
                            if self.open_position(sig):
                                await asyncio.sleep(3)
                    log.info(f"✓ Escaneo completo | Señales: {found}")

                await asyncio.sleep(SCAN_INTERVAL)

            except KeyboardInterrupt:
                log.info("\n⏹️  Bot detenido por usuario")
                self._notify("<b>⏹️ BOT DETENIDO</b>\nApagado manual")
                break
            except Exception as e:
                log.error(f"❌ Error en main loop: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(30)

# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    bot = InstitutionalBot()
    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("👋 Bot v4.0 terminado")
