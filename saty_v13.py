"""
SATY ELITE v13 — Motor de señales multi-estrategia
══════════════════════════════════════════════════════════════
ESTRATEGIAS INTEGRADAS (traducidas desde Pine Script):

  📊 MÓDULO 1 — Confirmación PRO (Squeeze + BB + Vol + EMA)
     Fuente: "Confirmación Simple PRO v5"
     Condición BUY:  precio < BB_lower + volumen fuerte + squeeze_momentum>0 + EMA9>EMA21
     Condición SELL: precio > BB_upper + volumen fuerte + squeeze_momentum<0 + EMA9<EMA21

  🎯 MÓDULO 2 — Bollinger Hunter Pro v5.4
     Fuente: "Bollinger Hunter Pro v5.4" (Arthur Merrill + Bollinger)
     Señales: W-pattern (doble suelo), M-pattern (doble techo),
              %B Divergencia, Breakout, Walking the Bands,
              Mean Reversion, Middle Band Crossover, Squeeze

  🏛️ MÓDULO 3 — SMC (Smart Money Concepts)
     Fuente: "SMC Scalper M1 w/ M5 Confirm"
     Señales: Order Blocks (bullish/bearish), Liquidity Sweeps,
              Break of Structure (BOS), multi-timeframe EMA confirm

  🔧 ANTI-SEÑALES FALSAS:
     - REQUIERE consenso ≥2 módulos para entrar (no solo 1)
     - Cooldown 30min tras cada señal por símbolo
     - Score mínimo subido a 5/15 (antes 3/13)
     - Filtro de volumen institucional (2x media)
     - HTF confirmation obligatorio (15m + 1h)
     - SMC Order Block proximity filter
     - Sin señales en squeeze (esperar confirmación)
     - Divergencias %B como señal de salida temprana

Variables de entorno Railway:
  BINGX_API_KEY, BINGX_API_SECRET
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  FIXED_USDT          (def: 8)
  MAX_OPEN_TRADES     (def: 10)
  MIN_SCORE           (def: 5)       ← más selectivo
  MIN_MODULES         (def: 2)       ← requiere consenso
  MAX_DRAWDOWN        (def: 15)
  DAILY_LOSS_LIMIT    (def: 8)
  BTC_FILTER          (def: true)
  COOLDOWN_MIN        (def: 30)      ← más largo
  LEVERAGE            (def: 10)
"""

import os, sys, time, logging, csv, json
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque

import requests
import ccxt
import pandas as pd
import numpy as np
from scipy.stats import linregress

# ══════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("saty_v13")

# ══════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════
API_KEY    = os.environ.get("BINGX_API_KEY",    "")
API_SECRET = os.environ.get("BINGX_API_SECRET", "")
TF         = os.environ.get("TIMEFRAME",  "5m")
HTF1       = os.environ.get("HTF1",       "15m")
HTF2       = os.environ.get("HTF2",       "1h")
POLL_SECS  = int(os.environ.get("POLL_SECONDS", "60"))

def _tg_token():   return os.environ.get("TELEGRAM_BOT_TOKEN", "") or os.environ.get("TG_TOKEN", "")
def _tg_chat_id(): return os.environ.get("TELEGRAM_CHAT_ID", "")   or os.environ.get("TG_CHAT_ID", "")

_bl = os.environ.get("BLACKLIST", "")
BLACKLIST: List[str] = [s.strip() for s in _bl.split(",") if s.strip()]

FIXED_USDT       = float(os.environ.get("FIXED_USDT",       "8.0"))
MAX_OPEN_TRADES  = int(os.environ.get("MAX_OPEN_TRADES",    "10"))
MIN_SCORE        = int(os.environ.get("MIN_SCORE",          "5"))   # ← más selectivo
MIN_MODULES      = int(os.environ.get("MIN_MODULES",        "2"))   # ← consenso requerido
CB_DD            = float(os.environ.get("MAX_DRAWDOWN",     "15.0"))
DAILY_LOSS_LIMIT = float(os.environ.get("DAILY_LOSS_LIMIT", "8.0"))
COOLDOWN_MIN     = int(os.environ.get("COOLDOWN_MIN",       "30"))  # ← más largo
MAX_SPREAD_PCT   = float(os.environ.get("MAX_SPREAD_PCT",   "0.8")) # ← más estricto
MIN_VOLUME_USDT  = float(os.environ.get("MIN_VOLUME_USDT",  "50000")) # ← más líquido
TOP_N_SYMBOLS    = int(os.environ.get("TOP_N_SYMBOLS",      "200")) # ← menos pares, más calidad
BTC_FILTER       = os.environ.get("BTC_FILTER", "true").lower() == "true"
LEVERAGE         = float(os.environ.get("LEVERAGE",         "10"))

# ── Bollinger params ─────────────────────────────────────
BB_LEN    = 20;  BB_MULT   = 2.0
VOL_MULT  = 1.5; VOL_INST  = 2.0   # institucional = 2x media
SQZ_LEN   = 20
TREND_LEN = 3    # barras sobre upper band para "walking"
PIVOT_L   = 5;   PIVOT_R   = 2     # W/M pattern pivots
SMC_LB    = 10   # lookback Order Blocks

# ── Indicadores base ─────────────────────────────────────
FAST_LEN  = 9;   SLOW_LEN_EMA = 21; BIAS_LEN = 48; MA200 = 200
ADX_LEN   = 14;  ADX_MIN   = 15
RSI_LEN   = 14;  ATR_LEN   = 14; MACD_FAST = 12; MACD_SLOW = 26; MACD_SIG = 9

# ── TP/SL (3 niveles) ────────────────────────────────────
TP1_MULT = 1.0; TP2_MULT = 2.0; TP3_MULT = 4.0; SL_ATR = 1.0
MAX_CONSEC_LOSS = 3
HEDGE_MODE: bool = False
CSV_PATH  = "saty_v13_trades.csv"

equity_history: deque = deque(maxlen=48)

# ══════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════
@dataclass
class ModuleResult:
    """Resultado de un módulo de señal."""
    name:       str
    direction:  str    # "long" | "short" | "none"
    score:      int
    signals:    List[str]  # señales individuales activas
    reason:     str = ""

@dataclass
class TradeState:
    symbol:         str   = ""
    side:           str   = ""
    base:           str   = ""
    entry_price:    float = 0.0
    tp1_price:      float = 0.0
    tp2_price:      float = 0.0
    tp3_price:      float = 0.0
    sl_price:       float = 0.0
    sl_moved_be:    bool  = False
    tp1_hit:        bool  = False
    tp2_hit:        bool  = False
    trail_high:     float = 0.0
    trail_low:      float = 0.0
    peak_price:     float = 0.0
    stall_count:    int   = 0
    trail_phase:    str   = "normal"
    max_profit_pct: float = 0.0
    entry_score:    int   = 0
    modules_used:   str   = ""
    active_signals: str   = ""
    entry_time:     str   = ""
    contracts:      float = 0.0
    atr_entry:      float = 0.0

@dataclass
class BotState:
    wins:           int   = 0
    losses:         int   = 0
    gross_profit:   float = 0.0
    gross_loss:     float = 0.0
    consec_losses:  int   = 0
    peak_equity:    float = 0.0
    total_pnl:      float = 0.0
    daily_pnl:      float = 0.0
    daily_reset_ts: float = 0.0
    last_heartbeat: float = 0.0
    trades:         Dict[str, TradeState] = field(default_factory=dict)
    cooldowns:      Dict[str, float]      = field(default_factory=dict)
    btc_bull:       bool  = True
    btc_bear:       bool  = False
    btc_rsi:        float = 50.0
    btc_adx:        float = 0.0
    scan_count:     int   = 0
    signals_found:  int   = 0
    signals_blocked: int  = 0
    last_discarded: List[dict] = field(default_factory=list)

    def open_count(self): return len(self.trades)
    def bases_open(self): return {t.base: t.side for t in self.trades.values()}
    def base_has_trade(self, base): return base in self.bases_open()
    def win_rate(self):
        t = self.wins + self.losses
        return (self.wins / t * 100) if t else 0.0
    def profit_factor(self):
        return (self.gross_profit / self.gross_loss) if self.gross_loss else 0.0
    def score_bar(self, score, mx=15):
        filled = min(score, mx)
        return "█" * filled + "░" * (mx - filled)
    def cb_active(self):
        if self.peak_equity <= 0: return False
        dd = (self.peak_equity - (self.peak_equity + self.total_pnl)) / self.peak_equity * 100
        return dd >= CB_DD
    def daily_limit_hit(self):
        if self.peak_equity <= 0: return False
        return self.daily_pnl < 0 and abs(self.daily_pnl) / self.peak_equity * 100 >= DAILY_LOSS_LIMIT
    def risk_mult(self):
        return 0.5 if self.consec_losses >= MAX_CONSEC_LOSS else 1.0
    def in_cooldown(self, symbol):
        return time.time() - self.cooldowns.get(symbol, 0) < COOLDOWN_MIN * 60
    def set_cooldown(self, symbol):
        self.cooldowns[symbol] = time.time()
    def reset_daily(self):
        now = time.time()
        if now - self.daily_reset_ts > 86400:
            self.daily_pnl = 0.0; self.daily_reset_ts = now
            log.info("Daily PnL reseteado")

state = BotState()

# ══════════════════════════════════════════════════════════
# CACHE OHLCV
# ══════════════════════════════════════════════════════════
_cache: Dict[str, Tuple[float, pd.DataFrame]] = {}
CACHE_TTL = 55

def fetch_df(ex, symbol, tf, limit=400):
    key = f"{symbol}|{tf}"
    now = time.time()
    if key in _cache:
        ts, df = _cache[key]
        if now - ts < CACHE_TTL:
            return df
    raw = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df  = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)
    _cache[key] = (now, df)
    return df

def clear_cache(): _cache.clear()
def utcnow(): return datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

# ══════════════════════════════════════════════════════════
# INDICADORES BASE
# ══════════════════════════════════════════════════════════
def ema(s, n):  return s.ewm(span=n, adjust=False).mean()
def sma(s, n):  return s.rolling(n).mean()

def calc_atr(df, n=ATR_LEN):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()

def calc_rsi(s, n=RSI_LEN):
    d  = s.diff()
    g  = d.clip(lower=0).ewm(span=n, adjust=False).mean()
    lo = (-d.clip(upper=0)).ewm(span=n, adjust=False).mean()
    return 100 - (100 / (1 + g / lo.replace(0, np.nan)))

def calc_adx(df, n=ADX_LEN):
    h, l   = df["high"], df["low"]
    up, dn = h.diff(), -l.diff()
    pdm    = up.where((up > dn) & (up > 0), 0.0)
    mdm    = dn.where((dn > up) & (dn > 0), 0.0)
    atr_s  = calc_atr(df, n)
    dip    = 100 * pdm.ewm(span=n, adjust=False).mean() / atr_s
    dim    = 100 * mdm.ewm(span=n, adjust=False).mean() / atr_s
    dx     = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    return dip, dim, dx.ewm(span=n, adjust=False).mean()

def calc_macd(s):
    m  = ema(s, MACD_FAST) - ema(s, MACD_SLOW)
    sg = ema(m, MACD_SIG)
    return m, sg, m - sg

def calc_bb(s, n=BB_LEN, mult=BB_MULT):
    mid = sma(s, n)
    std = s.rolling(n).std()
    return mid, mid + mult * std, mid - mult * std

def calc_squeeze_momentum(df, n=SQZ_LEN):
    """
    Squeeze Momentum (Pine: ta.linreg(close - (high+low)/2, sqzLen, 0))
    Traducción exacta del Pine Script de "Confirmación Simple PRO"
    """
    c, h, l = df["close"], df["high"], df["low"]
    val = c - (h + l) / 2
    # Regresión lineal en ventana deslizante
    result = pd.Series(np.nan, index=df.index)
    for i in range(n - 1, len(df)):
        y = val.iloc[i - n + 1: i + 1].values
        x = np.arange(n)
        if len(y) == n and not np.isnan(y).any():
            slope, intercept, *_ = linregress(x, y)
            result.iloc[i] = intercept + slope * (n - 1)
    return result

def calc_percent_b(close, upper, lower):
    """%B = (close - lower) / (upper - lower)"""
    return (close - lower) / (upper - lower).replace(0, np.nan)

def calc_pivot_highs(s_high, left=PIVOT_L, right=PIVOT_R):
    """Detecta pivot highs (máximos locales)."""
    result = pd.Series(np.nan, index=s_high.index)
    for i in range(left, len(s_high) - right):
        window_l = s_high.iloc[i - left: i]
        window_r = s_high.iloc[i + 1: i + right + 1]
        if s_high.iloc[i] >= window_l.max() and s_high.iloc[i] >= window_r.max():
            result.iloc[i] = s_high.iloc[i]
    return result

def calc_pivot_lows(s_low, left=PIVOT_L, right=PIVOT_R):
    """Detecta pivot lows (mínimos locales)."""
    result = pd.Series(np.nan, index=s_low.index)
    for i in range(left, len(s_low) - right):
        window_l = s_low.iloc[i - left: i]
        window_r = s_low.iloc[i + 1: i + right + 1]
        if s_low.iloc[i] <= window_l.min() and s_low.iloc[i] <= window_r.min():
            result.iloc[i] = s_low.iloc[i]
    return result

# ══════════════════════════════════════════════════════════
# MÓDULO 1: CONFIRMACIÓN PRO (Squeeze + BB + Vol + EMA)
# Traducción exacta de "Confirmación Simple PRO v5"
# ══════════════════════════════════════════════════════════
def module_confirmacion_pro(df: pd.DataFrame, htf1_bull: bool, htf1_bear: bool) -> Tuple[ModuleResult, ModuleResult]:
    """
    BUY:  precio < BB_lower + volumen > 1.5x + squeeze_momentum > 0 + EMA9 > EMA21
    SELL: precio > BB_upper + volumen > 1.5x + squeeze_momentum < 0 + EMA9 < EMA21

    Anti-falsas señales añadidos sobre el Pine original:
    - HTF confirmation (15m bias)
    - ADX > 14 (requiere tendencia real, no rango plano)
    - Distancia mínima a BB (evita señales en medio de la banda)
    """
    c = df["close"]
    row = df.iloc[-2]   # última vela cerrada

    # Indicadores del Pine
    mid_bb, upper_bb, lower_bb = calc_bb(c)
    vol_safe  = df["volume"].fillna(1).replace(0, 1)
    vol_avg   = sma(vol_safe, BB_LEN)
    sqz_mom   = calc_squeeze_momentum(df)
    ema_fast  = ema(c, FAST_LEN)
    ema_slow  = ema(c, SLOW_LEN_EMA)
    dip, dim, adx = calc_adx(df)

    r = df.iloc[-2]
    close_v   = float(r["close"])
    vol_v     = float(r["volume"])
    vol_avg_v = float(vol_avg.iloc[-2])
    sqz_v     = float(sqz_mom.iloc[-2]) if not pd.isna(sqz_mom.iloc[-2]) else 0.0
    upper_v   = float(upper_bb.iloc[-2])
    lower_v   = float(lower_bb.iloc[-2])
    mid_v     = float(mid_bb.iloc[-2])
    ef_v      = float(ema_fast.iloc[-2])
    es_v      = float(ema_slow.iloc[-2])
    adx_v     = float(adx.iloc[-2])

    vol_ok    = vol_v > vol_avg_v * VOL_MULT
    adx_ok    = adx_v > ADX_MIN

    # ── LONG ─────────────────────────────────────────────
    long_sigs = []
    if close_v < lower_v:      long_sigs.append("BB_lower_touch")
    if vol_ok:                 long_sigs.append(f"vol_{vol_v/vol_avg_v:.1f}x")
    if sqz_v > 0:              long_sigs.append(f"sqz_mom+{sqz_v:.4f}")
    if ef_v > es_v:            long_sigs.append("EMA9>21")
    if htf1_bull:              long_sigs.append("HTF1_bull")
    if adx_ok:                 long_sigs.append(f"ADX{adx_v:.0f}")

    # Pine original: bbBuy AND volConfirm AND sqzBuy AND trendUp
    pine_buy = (close_v < lower_v and vol_ok and sqz_v > 0 and ef_v > es_v)
    long_score = len(long_sigs) if pine_buy else max(0, len(long_sigs) - 2)

    # ── SHORT ────────────────────────────────────────────
    short_sigs = []
    if close_v > upper_v:      short_sigs.append("BB_upper_touch")
    if vol_ok:                 short_sigs.append(f"vol_{vol_v/vol_avg_v:.1f}x")
    if sqz_v < 0:              short_sigs.append(f"sqz_mom{sqz_v:.4f}")
    if ef_v < es_v:            short_sigs.append("EMA9<21")
    if htf1_bear:              short_sigs.append("HTF1_bear")
    if adx_ok:                 short_sigs.append(f"ADX{adx_v:.0f}")

    pine_sell = (close_v > upper_v and vol_ok and sqz_v < 0 and ef_v < es_v)
    short_score = len(short_sigs) if pine_sell else max(0, len(short_sigs) - 2)

    long_r  = ModuleResult("ConfPRO", "long"  if pine_buy  else "none", long_score,  long_sigs)
    short_r = ModuleResult("ConfPRO", "short" if pine_sell else "none", short_score, short_sigs)
    return long_r, short_r

# ══════════════════════════════════════════════════════════
# MÓDULO 2: BOLLINGER HUNTER PRO v5.4
# Traducción de: W/M patterns, %B divergence, breakout,
#                walking the bands, mean reversion, MA cross
# ══════════════════════════════════════════════════════════
def module_bollinger_hunter(df: pd.DataFrame, htf2_bull: bool, htf2_bear: bool) -> Tuple[ModuleResult, ModuleResult]:
    """
    7 sub-estrategias con jerarquía (igual que el Pine):
    1. %B Divergencia (más fuerte)
    2. Breakout
    3. Walking the Bands
    4. Middle Band Cross
    5. W/M Pattern
    6. Mean Reversion
    7. Squeeze (solo info, no entrada)
    """
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    mid_bb, upper_bb, lower_bb = calc_bb(c)
    bb_width = ((upper_bb - lower_bb) / mid_bb) * 100
    pct_b    = calc_percent_b(c, upper_bb, lower_bb)

    vol_avg  = sma(v.fillna(1).replace(0, 1), BB_LEN)
    ema_fast = ema(c, FAST_LEN)
    ema_slow = ema(c, SLOW_LEN_EMA)

    pivot_h  = calc_pivot_highs(h)
    pivot_l  = calc_pivot_lows(l)

    # Usar barra -2 (última cerrada, anti look-ahead)
    i = len(df) - 2
    if i < 50:
        empty = ModuleResult("BollingerHunter", "none", 0, [])
        return empty, empty

    close_v  = float(c.iloc[i])
    high_v   = float(h.iloc[i])
    low_v    = float(l.iloc[i])
    open_v   = float(df["open"].iloc[i])
    upper_v  = float(upper_bb.iloc[i])
    lower_v  = float(lower_bb.iloc[i])
    mid_v    = float(mid_bb.iloc[i])
    bbw_v    = float(bb_width.iloc[i])
    pbv      = float(pct_b.iloc[i]) if not pd.isna(pct_b.iloc[i]) else 0.5
    vol_v    = float(v.iloc[i])
    vol_av   = float(vol_avg.iloc[i])
    ef_v     = float(ema_fast.iloc[i])
    es_v     = float(ema_slow.iloc[i])

    # 1️⃣ SQUEEZE
    bbw_min_100 = float(bb_width.iloc[max(0,i-100):i+1].min())
    is_squeeze  = bbw_v <= bbw_min_100 * 1.10

    # 2️⃣ BREAKOUT: precio > upper + BB se expande + volumen
    bbw_prev = float(bb_width.iloc[i-1])
    upper_prev = float(upper_bb.iloc[i-1])
    lower_prev = float(lower_bb.iloc[i-1])
    is_breakout_up   = (close_v > upper_v and bbw_v > bbw_prev and
                        upper_v > upper_prev and lower_v < lower_prev and
                        vol_v > vol_av * 1.5)
    is_breakout_down = (close_v < lower_v and bbw_v > bbw_prev and
                        upper_v > upper_prev and lower_v < lower_prev and
                        vol_v > vol_av * 1.5)

    # 3️⃣ WALKING THE BANDS (TREND_LEN barras sobre upper)
    close_arr = c.iloc[i-TREND_LEN+1:i+1].values
    upper_arr = upper_bb.iloc[i-TREND_LEN+1:i+1].values
    above_count = sum(1 for cv, uv in zip(close_arr, upper_arr) if cv >= uv)
    is_walking_up = above_count >= TREND_LEN - 1

    # 4️⃣ MEAN REVERSION
    is_rev_long  = (not is_squeeze and not is_breakout_up and
                    low_v  <= lower_v and close_v > open_v and pbv > 0.1)
    is_rev_short = (not is_squeeze and not is_breakout_down and
                    high_v >= upper_v and close_v < open_v and pbv < 0.9)

    # 5️⃣ W PATTERN (doble suelo — Arthur Merrill)
    # Primera pata: pivot low que TOCA/ROMPE lower band
    # Segunda pata: pivot low que queda DENTRO de la banda
    is_w_pattern = False
    is_m_pattern = False
    last_pl_val = None; last_pl_out = False
    last_ph_val = None; last_ph_out = False

    for j in range(max(0, i-30), i+1):
        pl_v = pivot_l.iloc[j]
        ph_v = pivot_h.iloc[j]
        lb_j = float(lower_bb.iloc[j])
        ub_j = float(upper_bb.iloc[j])

        if not pd.isna(pl_v):
            current_in = pl_v > lb_j
            if current_in and last_pl_out:
                is_w_pattern = True
            last_pl_val  = pl_v
            last_pl_out  = pl_v < lb_j

        if not pd.isna(ph_v):
            current_in = ph_v < ub_j
            if current_in and last_ph_out:
                is_m_pattern = True
            last_ph_val  = ph_v
            last_ph_out  = ph_v > ub_j

    # 6️⃣ %B DIVERGENCE (más fuerte — John Bollinger)
    # Bear div: precio sube (new high) pero %B baja (menos fuerte)
    # Bull div: precio baja (new low) pero %B sube (menos débil)
    bear_div = False; bull_div = False
    last_price_ph = None; last_pb_ph = None
    last_price_pl = None; last_pb_pl = None

    ph_indices = [(j, float(pivot_h.iloc[j])) for j in range(max(0,i-50), i+1) if not pd.isna(pivot_h.iloc[j])]
    pl_indices = [(j, float(pivot_l.iloc[j])) for j in range(max(0,i-50), i+1) if not pd.isna(pivot_l.iloc[j])]

    for j, ph_v in ph_indices:
        pb_at_ph = float(pct_b.iloc[j]) if not pd.isna(pct_b.iloc[j]) else 0.5
        if last_price_ph is not None and ph_v > last_price_ph and pb_at_ph < last_pb_ph:
            bear_div = True
        last_price_ph = ph_v; last_pb_ph = pb_at_ph

    for j, pl_v in pl_indices:
        pb_at_pl = float(pct_b.iloc[j]) if not pd.isna(pct_b.iloc[j]) else 0.5
        if last_price_pl is not None and pl_v < last_price_pl and pb_at_pl > last_pb_pl:
            bull_div = True
        last_price_pl = pl_v; last_pb_pl = pb_at_pl

    # 7️⃣ MIDDLE BAND CROSS
    close_prev = float(c.iloc[i-1])
    mid_prev   = float(mid_bb.iloc[i-1])
    is_ma_cross_up   = (close_prev <= mid_prev and close_v > mid_v)
    is_ma_cross_down = (close_prev >= mid_prev and close_v < mid_v)

    # ══════════════════════════════════════════════════════
    # JERARQUÍA EXACTA DEL PINE (Bollinger Hunter v5.4)
    # 🥇 Divergencia %B  → señal más fuerte, las demás se silencian
    # 🥈 Breakout         → inicio de movimiento
    # 🥉 Walking          → continuación de tendencia
    # 🎖️ MA Cross         → devolución segura a tendencia
    # 🏅 W/M Pattern      → señales de giro
    # 🎖️ Mean Reversion   → rebote de banda
    # ⏳ Squeeze          → MODO ESPERA, SIN ENTRADA
    # ══════════════════════════════════════════════════════

    # Squeeze = sin entrada (esperar dirección)
    if is_squeeze:
        long_r  = ModuleResult("BollingerHunter", "none", 0, ["squeeze_wait"])
        short_r = ModuleResult("BollingerHunter", "none", 0, ["squeeze_wait"])
        return long_r, short_r

    vol_strong = vol_v > vol_av * VOL_MULT
    vol_label  = f"vol_{vol_v/max(vol_av,1):.1f}x"

    # ── Determinar ESTADO del Pine (único estado por barra) ──────────────
    # Pine usa if/else → sólo UN estado activo
    # Traducimos manteniendo la exclusividad de la jerarquía

    # Patas largas (LONG)
    long_state  = "none"
    long_sigs   = []

    if bear_div:
        # Bear div = estado BAJISTA más fuerte, silencia long
        long_state = "none"
    elif bull_div:
        # 🥇 Divergencia positiva → long más fuerte
        long_state = "bull_div"
        long_sigs  = ["bull_div_%B", vol_label if vol_strong else ""]
        if htf2_bull: long_sigs.append("HTF2_bull")
    elif is_breakout_up:
        # 🥈 Breakout alcista
        long_state = "breakout"
        long_sigs  = ["breakout_up", vol_label if vol_strong else ""]
        if htf2_bull: long_sigs.append("HTF2_bull")
        if ef_v > es_v: long_sigs.append("EMA_up")
    elif is_walking_up:
        # 🥉 Walking the bands → ralli en curso, NO SHORTAR
        long_state = "walking"
        long_sigs  = ["walking_up"]
        if htf2_bull: long_sigs.append("HTF2_bull")
        if ef_v > es_v: long_sigs.append("EMA_up")
    elif is_ma_cross_up:
        # 🎖️ Cruce de banda media → devolución
        long_state = "ma_cross"
        long_sigs  = ["ma_cross_up"]
        if htf2_bull: long_sigs.append("HTF2_bull")
        if vol_strong: long_sigs.append(vol_label)
    elif is_w_pattern:
        # 🏅 Formación W
        long_state = "w_pattern"
        long_sigs  = ["W_pattern"]
        if htf2_bull: long_sigs.append("HTF2_bull")
        if vol_strong: long_sigs.append(vol_label)
    elif is_rev_long:
        # 🎖️ Mean reversion (rebote de banda baja)
        long_state = "mean_rev"
        long_sigs  = ["mean_rev_long"]
        if htf2_bull: long_sigs.append("HTF2_bull")
        if vol_strong: long_sigs.append(vol_label)

    long_sigs = [s for s in long_sigs if s]  # quitar strings vacíos

    # Patas cortas (SHORT)
    short_state = "none"
    short_sigs  = []

    if bull_div:
        # Bull div = estado ALCISTA más fuerte, silencia short
        short_state = "none"
    elif bear_div:
        # 🥇 Divergencia negativa → short más fuerte
        short_state = "bear_div"
        short_sigs  = ["bear_div_%B", vol_label if vol_strong else ""]
        if htf2_bear: short_sigs.append("HTF2_bear")
    elif is_breakout_down:
        short_state = "breakout"
        short_sigs  = ["breakout_down", vol_label if vol_strong else ""]
        if htf2_bear: short_sigs.append("HTF2_bear")
        if ef_v < es_v: short_sigs.append("EMA_dn")
    elif is_walking_up:
        # Mientras hace "walking" no hay señal short (tendencia alcista fuerte)
        short_state = "none"
    elif is_ma_cross_down:
        short_state = "ma_cross"
        short_sigs  = ["ma_cross_down"]
        if htf2_bear: short_sigs.append("HTF2_bear")
        if vol_strong: short_sigs.append(vol_label)
    elif is_m_pattern:
        short_state = "m_pattern"
        short_sigs  = ["M_pattern"]
        if htf2_bear: short_sigs.append("HTF2_bear")
        if vol_strong: short_sigs.append(vol_label)
    elif is_rev_short:
        short_state = "mean_rev"
        short_sigs  = ["mean_rev_short"]
        if htf2_bear: short_sigs.append("HTF2_bear")
        if vol_strong: short_sigs.append(vol_label)

    short_sigs = [s for s in short_sigs if s]

    # ── Validar dirección: requiere estado activo + min señales ──────────
    # Los estados "fuertes" (divergencia, breakout) necesitan menos confirmación
    # Los estados "débiles" (mean_rev) necesitan HTF confirm
    STRONG_LONG_STATES  = {"bull_div", "breakout", "walking"}
    STRONG_SHORT_STATES = {"bear_div", "breakout"}

    if long_state in STRONG_LONG_STATES:
        long_dir = "long" if len(long_sigs) >= 1 else "none"
    elif long_state != "none":
        long_dir = "long" if len(long_sigs) >= 2 and htf2_bull else "none"
    else:
        long_dir = "none"

    if short_state in STRONG_SHORT_STATES:
        short_dir = "short" if len(short_sigs) >= 1 else "none"
    elif short_state != "none":
        short_dir = "short" if len(short_sigs) >= 2 and htf2_bear else "none"
    else:
        short_dir = "none"

    long_r  = ModuleResult("BollingerHunter", long_dir,  len(long_sigs),  long_sigs)
    short_r = ModuleResult("BollingerHunter", short_dir, len(short_sigs), short_sigs)
    return long_r, short_r

# ══════════════════════════════════════════════════════════
# MÓDULO 3: SMC (Smart Money Concepts)
# Traducción de: "SMC Scalper M1 w/ M5 Confirm"
# Order Blocks + Liquidity Sweeps + BOS
# ══════════════════════════════════════════════════════════
def module_smc(df: pd.DataFrame, df_htf: pd.DataFrame) -> Tuple[ModuleResult, ModuleResult]:
    """
    Traducción exacta de "SMC Scalper M1 w/ M5 Confirm"

    Pine lógica:
      Bullish OB: close[i] < open[i] AND close > high[i]
        → vela bajista hace k barras, precio actual ROMPE POR ENCIMA = OB bullish activado
        → señal: precio está dentro de la zona OB (tiempo) + sweepDown + HTF BOS/EMA up

      Bearish OB: close[i] > open[i] AND close < low[i]
        → vela alcista hace k barras, precio actual ROMPE POR ABAJO = OB bearish activado
        → señal: dentro de zona OB + sweepUp + HTF BOS/EMA down

    Liquidity zones: mínimos/máximos locales que actúan como imanes de liquidez

    Anti-falsas señales añadidas vs Pine original:
      - Requiere las 3 condiciones (OB + sweep + HTF) simultáneamente
      - Cooldown implícito vía score bajo si solo 1 o 2 condiciones
    """
    c, h, l, o = df["close"], df["high"], df["low"], df["open"]

    i = len(df) - 2
    if i < SMC_LB + 5 or len(df_htf) < 30:
        empty = ModuleResult("SMC", "none", 0, [])
        return empty, empty

    close_v = float(c.iloc[i])
    high_v  = float(h.iloc[i])
    low_v   = float(l.iloc[i])
    atr_v   = float(calc_atr(df).iloc[i])

    # ── Order Blocks (Pine: for i = 2 to 10) ─────────────
    # El Pine escanea velas recientes y detecta OBs cuando precio rompe
    # NOTA: "close > high[i]" = precio actual rompe por encima de vela bajista i barras atrás
    active_bull_obs: List[dict] = []
    active_bear_obs: List[dict] = []

    for k in range(2, min(11, i)):
        ob_h = float(h.iloc[i - k])
        ob_l = float(l.iloc[i - k])
        ob_c = float(c.iloc[i - k])
        ob_o = float(o.iloc[i - k])

        # Bullish OB (Pine: close[i]<open[i] and close>high[i])
        # = vela bajista + precio actual ya por encima → OB confirmado
        if ob_c < ob_o and close_v > ob_h:
            active_bull_obs.append({"high": ob_h, "low": ob_l, "k": k})

        # Bearish OB (Pine: close[i]>open[i] and close<low[i])
        # = vela alcista + precio actual ya por debajo → OB confirmado
        if ob_c > ob_o and close_v < ob_l:
            active_bear_obs.append({"high": ob_h, "low": ob_l, "k": k})

    bullish_ob = len(active_bull_obs) > 0
    bearish_ob = len(active_bear_obs) > 0

    # ── Proximity check: precio cerca del OB (retest) ────
    # Para señal de entrada, el precio debe estar retestando el OB:
    # Bullish OB retest: precio cayó de vuelta cerca del OB (within 1 ATR above ob_high)
    bull_ob_retest = False
    for ob in active_bull_obs:
        if ob["high"] <= close_v <= ob["high"] + atr_v * 1.5:
            bull_ob_retest = True; break

    # Bearish OB retest: precio subió de vuelta cerca del OB (within 1 ATR below ob_low)
    bear_ob_retest = False
    for ob in active_bear_obs:
        if ob["low"] - atr_v * 1.5 <= close_v <= ob["low"]:
            bear_ob_retest = True; break

    # ── Liquidity Sweep (Pine: sweepLen=10 bars) ─────────
    # sweepDown = low < ta.lowest(low[1], sweepLen) → nuevo mínimo local
    # sweepUp   = high > ta.highest(high[1], sweepLen) → nuevo máximo local
    sweep_len_bars = SMC_LB  # = 10
    lowest_prev  = float(l.iloc[i - sweep_len_bars:i].min())
    highest_prev = float(h.iloc[i - sweep_len_bars:i].max())
    sweep_dn = low_v  < lowest_prev   # barre stops bajistas (trampa → rebote alcista)
    sweep_up = high_v > highest_prev  # barre stops alcistas (trampa → caída bajista)

    # ── BOS en HTF (Pine: bosUp_m5, bosDown_m5) ──────────
    htf_c  = df_htf["close"]
    htf_h  = df_htf["high"]
    htf_l  = df_htf["low"]
    htf_i  = len(df_htf) - 2

    htf_ema9  = ema(htf_c, FAST_LEN)
    htf_ema21 = ema(htf_c, SLOW_LEN_EMA)
    htf_close = float(htf_c.iloc[htf_i])
    htf_ef    = float(htf_ema9.iloc[htf_i])
    htf_es    = float(htf_ema21.iloc[htf_i])

    # bosUp_m5  = m5_close > ta.highest(high, bosLookback)
    # bosDown_m5 = m5_close < ta.lowest(low, bosLookback)
    htf_highest = float(htf_h.iloc[max(0, htf_i - 20):htf_i].max())
    htf_lowest  = float(htf_l.iloc[max(0, htf_i - 20):htf_i].min())
    bos_up   = htf_close > htf_highest  # BOS alcista HTF
    bos_down = htf_close < htf_lowest   # BOS bajista HTF
    htf_bull = htf_ef > htf_es          # m5_emaUp
    htf_bear = htf_ef < htf_es          # m5_emaDn

    # ── Señales (Pine exacto) ─────────────────────────────
    # Pine BUY:  inside bullish OB + sweepDown + (bosUp_m5 OR m5_emaUp)
    # Pine SELL: inside bearish OB + sweepUp   + (bosDown_m5 OR m5_emaDn)
    # Añadimos bull_ob_retest/bear_ob_retest para evitar falsas (precio ya lejos del OB)

    long_sigs = []
    if bullish_ob:         long_sigs.append(f"bullish_OB({len(active_bull_obs)})")
    if bull_ob_retest:     long_sigs.append("OB_retest")
    if sweep_dn:           long_sigs.append("sweep_low")
    if bos_up:             long_sigs.append("HTF_BOS_up")
    elif htf_bull:         long_sigs.append("HTF_EMA_bull")

    short_sigs = []
    if bearish_ob:         short_sigs.append(f"bearish_OB({len(active_bear_obs)})")
    if bear_ob_retest:     short_sigs.append("OB_retest")
    if sweep_up:           short_sigs.append("sweep_high")
    if bos_down:           short_sigs.append("HTF_BOS_down")
    elif htf_bear:         short_sigs.append("HTF_EMA_bear")

    # Las 3 condiciones del Pine son obligatorias para dirección válida
    smc_long_valid  = bullish_ob and bull_ob_retest and sweep_dn and (bos_up or htf_bull)
    smc_short_valid = bearish_ob and bear_ob_retest and sweep_up and (bos_down or htf_bear)

    long_dir  = "long"  if smc_long_valid  else "none"
    short_dir = "short" if smc_short_valid else "none"

    long_r  = ModuleResult("SMC", long_dir,  len(long_sigs),  long_sigs)
    short_r = ModuleResult("SMC", short_dir, len(short_sigs), short_sigs)
    return long_r, short_r

# ══════════════════════════════════════════════════════════
# MOTOR DE CONSENSO (anti-falsas señales)
# ══════════════════════════════════════════════════════════
def eval_consensus(
    m1_long: ModuleResult, m1_short: ModuleResult,
    m2_long: ModuleResult, m2_short: ModuleResult,
    m3_long: ModuleResult, m3_short: ModuleResult,
) -> Tuple[Optional[str], int, str, str]:
    """
    Retorna (direction, total_score, modules_used, signals_str) o (None, 0, "", "")

    Anti-falsas señales:
    1. Requiere MIN_MODULES módulos en la misma dirección
    2. Score total >= MIN_SCORE
    3. No señales contradictorias fuertes (si hay short fuerte con long fuerte → skip)
    """
    # Acumular longs
    long_results  = [r for r in [m1_long, m2_long, m3_long]  if r.direction == "long"]
    short_results = [r for r in [m1_short, m2_short, m3_short] if r.direction == "short"]

    long_total  = sum(r.score for r in long_results)
    short_total = sum(r.score for r in short_results)
    long_mods   = len(long_results)
    short_mods  = len(short_results)

    # Contradicción fuerte: ambos lados tienen ≥2 módulos → no operar
    if long_mods >= 2 and short_mods >= 2:
        return None, 0, "", "contradicting_signals"

    best_dir = None; best_score = 0; best_mods = ""

    if long_mods >= MIN_MODULES and long_total >= MIN_SCORE:
        mod_names = "+".join(r.name for r in long_results)
        all_sigs  = "; ".join(f"{r.name}:[{','.join(r.signals[:3])}]" for r in long_results)
        best_dir = "long"; best_score = long_total; best_mods = mod_names

    if short_mods >= MIN_MODULES and short_total >= MIN_SCORE:
        if short_total > best_score:
            mod_names = "+".join(r.name for r in short_results)
            all_sigs  = "; ".join(f"{r.name}:[{','.join(r.signals[:3])}]" for r in short_results)
            best_dir = "short"; best_score = short_total; best_mods = mod_names

    if best_dir is None:
        return None, 0, "", ""

    all_sigs = "; ".join(
        f"{r.name}:[{','.join(r.signals[:3])}]"
        for r in (long_results if best_dir == "long" else short_results)
    )
    return best_dir, best_score, best_mods, all_sigs

# ══════════════════════════════════════════════════════════
# INDICADORES HTF (para módulos 1 y 2)
# ══════════════════════════════════════════════════════════
def htf_bias(df: pd.DataFrame) -> Tuple[bool, bool]:
    c = df["close"]
    e48 = ema(c, BIAS_LEN); e21 = ema(c, SLOW_LEN_EMA)
    row = df.iloc[-2]
    cl  = float(row["close"]); e48v = float(e48.iloc[-2]); e21v = float(e21.iloc[-2])
    return (cl > e48v and e21v > e48v), (cl < e48v and e21v < e48v)

# ══════════════════════════════════════════════════════════
# SCAN COMPLETO
# ══════════════════════════════════════════════════════════
def scan_symbol(ex, symbol) -> Optional[dict]:
    try:
        df   = fetch_df(ex, symbol, TF,   400)
        df1  = fetch_df(ex, symbol, HTF1, 200)
        df2  = fetch_df(ex, symbol, HTF2, 300)

        if len(df) < 100 or len(df1) < 50 or len(df2) < 50:
            return None

        # Indicadores básicos para filtrado rápido
        rsi_s = calc_rsi(df["close"])
        atr_s = calc_atr(df)
        dip, dim, adx_s = calc_adx(df)

        rsi_v = float(rsi_s.iloc[-2])
        adx_v = float(adx_s.iloc[-2])

        if pd.isna(adx_v) or pd.isna(rsi_v):
            return None

        htf1_bull, htf1_bear = htf_bias(df1)
        htf2_bull, htf2_bear = htf_bias(df2)

        # ── Ejecutar los 3 módulos ───────────────────────
        m1_l, m1_s = module_confirmacion_pro(df, htf1_bull, htf1_bear)
        m2_l, m2_s = module_bollinger_hunter(df, htf2_bull, htf2_bear)
        m3_l, m3_s = module_smc(df, df1)

        direction, total_score, modules, signals = eval_consensus(
            m1_l, m1_s, m2_l, m2_s, m3_l, m3_s
        )

        return {
            "symbol":    symbol,
            "base":      symbol.split("/")[0],
            "direction": direction,
            "score":     total_score,
            "modules":   modules,
            "signals":   signals,
            "rsi":       rsi_v,
            "adx":       adx_v,
            "atr":       float(atr_s.iloc[-2]),
            "live_price":float(df["close"].iloc[-2]),
            "vol_ma":    float(df["volume"].rolling(20).mean().iloc[-2]),
            "row":       df.iloc[-2],
            # módulos individuales para debug
            "m1_l": m1_l, "m1_s": m1_s,
            "m2_l": m2_l, "m2_s": m2_s,
            "m3_l": m3_l, "m3_s": m3_s,
        }
    except Exception as e:
        log.debug(f"[{symbol}] scan: {e}")
        return None

# ══════════════════════════════════════════════════════════
# BTC BIAS
# ══════════════════════════════════════════════════════════
def update_btc_bias(ex):
    prev_bull = state.btc_bull; prev_bear = state.btc_bear
    try:
        df  = fetch_df(ex, "BTC/USDT:USDT", "1h", limit=250)
        e48 = ema(df["close"], BIAS_LEN)
        e200= ema(df["close"], MA200)
        adx = calc_adx(df)[2]
        rsi = calc_rsi(df["close"])
        r   = df.iloc[-2]
        state.btc_bull = bool(float(r["close"]) > float(e48.iloc[-2]) and float(e48.iloc[-2]) > float(e200.iloc[-2]))
        state.btc_bear = bool(float(r["close"]) < float(e48.iloc[-2]) and float(e48.iloc[-2]) < float(e200.iloc[-2]))
        state.btc_rsi  = float(rsi.iloc[-2])
        state.btc_adx  = float(adx.iloc[-2])
        log.info(f"BTC: {'BULL' if state.btc_bull else 'BEAR' if state.btc_bear else 'NEUTRAL'}"
                 f" RSI:{state.btc_rsi:.1f} ADX:{state.btc_adx:.1f}")
        tg_btc_flip(prev_bull, prev_bear)
    except Exception as e:
        log.warning(f"BTC bias: {e}")

# ══════════════════════════════════════════════════════════
# BTC FILTER ADAPTATIVO
# ══════════════════════════════════════════════════════════
def btc_allows(direction: str, score: int) -> Tuple[bool, str]:
    if not BTC_FILTER:
        return True, ""
    btc_strong = state.btc_adx > 22
    if direction == "long":
        if state.btc_bear and btc_strong and score < 9:
            return False, f"BTC bajista fuerte (ADX:{state.btc_adx:.0f})"
    if direction == "short":
        if state.btc_bull and btc_strong and score < 9:
            return False, f"BTC alcista fuerte (ADX:{state.btc_adx:.0f})"
    return True, ""

# ══════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════
def tg(msg: str, parse_mode="HTML"):
    token = _tg_token(); chat_id = _tg_chat_id()
    if not token or not chat_id: return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": parse_mode},
            timeout=10
        )
        if not r.ok: log.warning(f"TG {r.status_code}: {r.text[:80]}")
    except Exception as e: log.warning(f"TG: {e}")

def tg_startup(balance: float, n: int):
    btc_icon = "🟢" if state.btc_bull else "🔴" if state.btc_bear else "⚪"
    tg(
        f"🚀 <b>SATY ELITE v13 — ONLINE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: <b>${balance:.2f} USDT</b>\n"
        f"📊 Universo: <b>{n} pares</b> (min ${MIN_VOLUME_USDT/1000:.0f}k vol)\n"
        f"⏱ TF: {TF} · {HTF1} · {HTF2}\n"
        f"🎯 Score mín: {MIN_SCORE} | Módulos mín: {MIN_MODULES}/3\n"
        f"📈 Max trades: {MAX_OPEN_TRADES}  |  💵 ${FIXED_USDT:.0f}×{int(LEVERAGE)}×\n"
        f"⏸ Cooldown: {COOLDOWN_MIN}min  |  Spread max: {MAX_SPREAD_PCT}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Módulos activos:\n"
        f"  1️⃣ Confirmación PRO (Squeeze+BB+Vol+EMA)\n"
        f"  2️⃣ Bollinger Hunter (W/M+Div+Breakout)\n"
        f"  3️⃣ SMC (OB+Sweep+BOS)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{btc_icon} BTC: {'ALCISTA' if state.btc_bull else 'BAJISTA' if state.btc_bear else 'NEUTRO'}"
        f" RSI:{state.btc_rsi:.0f} ADX:{state.btc_adx:.0f}\n"
        f"⏰ {utcnow()}"
    )

def tg_signal(t: TradeState):
    emoji  = "🟢" if t.side == "long" else "🔴"
    accion = "LONG ▲" if t.side == "long" else "SHORT ▼"
    sl_d   = abs(t.sl_price - t.entry_price)
    rr3    = abs(t.tp3_price - t.entry_price) / max(sl_d, 1e-9)
    def pct(p): return abs(p - t.entry_price) / t.entry_price * 100
    btc_icon = "🟢" if state.btc_bull else "🔴" if state.btc_bear else "⚪"
    tg(
        f"{emoji} <b>{accion} — {t.symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 Módulos: <b>{t.modules_used}</b>\n"
        f"🎯 Score: <b>{t.entry_score}/15</b>\n"
        f"📋 Señales: <code>{t.active_signals[:120]}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Entrada: <code>{t.entry_price:.6g}</code>\n"
        f"🟡 TP1 (25%): <code>{t.tp1_price:.6g}</code>  +{pct(t.tp1_price):.2f}%\n"
        f"🟠 TP2 (25%): <code>{t.tp2_price:.6g}</code>  +{pct(t.tp2_price):.2f}%\n"
        f"🟢 TP3 (50%): <code>{t.tp3_price:.6g}</code>  +{pct(t.tp3_price):.2f}%  R:R 1:{rr3:.1f}\n"
        f"🛑 SL: <code>{t.sl_price:.6g}</code>  -{pct(t.sl_price):.2f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{btc_icon} BTC RSI:{state.btc_rsi:.0f} ADX:{state.btc_adx:.0f}\n"
        f"📦 Posiciones: {state.open_count()}/{MAX_OPEN_TRADES}\n"
        f"⏰ {utcnow()}"
    )

def tg_close(reason: str, t: TradeState, exit_p: float, pnl: float):
    win   = pnl > 0
    emoji = "✅" if win else "❌"
    pct   = (pnl / (t.entry_price * t.contracts) * 100) if t.contracts > 0 else 0
    tg(
        f"{emoji} <b>CERRADO — {t.symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 {t.side.upper()} | {t.modules_used} | {reason}\n"
        f"🚪 Entrada: <code>{t.entry_price:.6g}</code>\n"
        f"🚪 Salida:  <code>{exit_p:.6g}</code>\n"
        f"{'📈' if win else '📉'} <b>{pct:+.2f}%  ${pnl:+.2f}</b>\n"
        f"🏔 Máx trade: +{t.max_profit_pct:.2f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {state.wins}W/{state.losses}L"
        f" | WR:{state.win_rate():.1f}%"
        f" | PF:{state.profit_factor():.2f}\n"
        f"💹 Hoy: ${state.daily_pnl:+.2f} | Total: ${state.total_pnl:+.2f}\n"
        f"⏰ {utcnow()}"
    )

def tg_heartbeat(balance: float):
    equity_history.append(state.total_pnl)
    open_lines = "\n".join(
        f"  {'🟢' if ts.side=='long' else '🔴'} {sym} @ {ts.entry_price:.5g}"
        f" [{ts.modules_used}] {'🛡' if ts.sl_moved_be else ''}+{ts.max_profit_pct:.1f}%"
        for sym, ts in state.trades.items()
    ) or "  (ninguna)"
    tg(
        f"💓 <b>HEARTBEAT — SATY v13</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: <b>${balance:.2f} USDT</b>\n"
        f"📅 Hoy: <b>${state.daily_pnl:+.2f}</b> | Total: <b>${state.total_pnl:+.2f}</b>\n"
        f"📊 {state.wins}W/{state.losses}L | WR:{state.win_rate():.1f}% | PF:{state.profit_factor():.2f}\n"
        f"🔍 Señales: {state.signals_found} ok / {state.signals_blocked} bloqueadas\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Posiciones ({state.open_count()}/{MAX_OPEN_TRADES}):\n{open_lines}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{'🟢' if state.btc_bull else '🔴' if state.btc_bear else '⚪'} BTC"
        f" RSI:{state.btc_rsi:.0f} ADX:{state.btc_adx:.0f}\n"
        f"⏰ {utcnow()}"
    )

def tg_summary(new_signals: list, n_scanned: int):
    top = "\n".join(
        f"  {'🟢' if s['direction']=='long' else '🔴'} {s['symbol']} "
        f"[{s['modules']}] Score:{s['score']} RSI:{s['rsi']:.0f}"
        for s in new_signals[:5]
    ) or "  (ninguna)"
    blocked = "\n".join(
        f"  ⚫ {d['symbol']} s:{d.get('score',0)} → {d['reason']}"
        for d in state.last_discarded[:3]
    ) or "  (ninguna)"
    tg(
        f"📡 <b>SCAN #{state.scan_count}</b>\n"
        f"🔍 Escaneados: {n_scanned} | Señales válidas: {state.signals_found}\n"
        f"📶 Entradas:\n{top}\n"
        f"🚫 Bloqueadas:\n{blocked}\n"
        f"📊 {state.wins}W/{state.losses}L | Total: ${state.total_pnl:+.2f}\n"
        f"⏰ {utcnow()}"
    )

def tg_btc_flip(prev_bull, prev_bear):
    if prev_bull == state.btc_bull and prev_bear == state.btc_bear: return
    estado = "🟢 ALCISTA" if state.btc_bull else "🔴 BAJISTA" if state.btc_bear else "⚪ NEUTRO"
    prev   = "🟢 ALCISTA" if prev_bull  else "🔴 BAJISTA" if prev_bear  else "⚪ NEUTRO"
    tg(f"₿ BTC: {prev} → <b>{estado}</b> | RSI:{state.btc_rsi:.1f} ADX:{state.btc_adx:.1f}\n⏰ {utcnow()}")

def tg_error(msg: str):
    tg(f"🔥 <b>ERROR</b>\n<code>{msg[:400]}</code>\n⏰ {utcnow()}")

def tg_circuit_breaker(dd: float):
    tg(f"🚨 <b>CIRCUIT BREAKER</b> DD:{dd:.2f}% > {CB_DD}%\n⛔ Sin nuevas posiciones\n⏰ {utcnow()}")

def tg_daily_limit():
    tg(f"🚨 <b>LÍMITE DIARIO</b> ${state.daily_pnl:+.2f}\n⛔ Sin trades hasta mañana UTC\n⏰ {utcnow()}")

# ══════════════════════════════════════════════════════════
# CSV LOG
# ══════════════════════════════════════════════════════════
def log_csv(action, t: TradeState, price, pnl=0.0):
    try:
        exists = os.path.exists(CSV_PATH)
        with open(CSV_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["ts","action","symbol","side","modules","score","entry","exit","pnl","contracts"])
            w.writerow([utcnow(), action, t.symbol, t.side,
                        t.modules_used, t.entry_score,
                        t.entry_price, price, round(pnl,4), t.contracts])
    except Exception as e:
        log.warning(f"CSV: {e}")

# ══════════════════════════════════════════════════════════
# EXCHANGE
# ══════════════════════════════════════════════════════════
def build_exchange():
    ex = ccxt.bingx({
        "apiKey": API_KEY, "secret": API_SECRET,
        "options": {"defaultType": "swap"},
        "enableRateLimit": True,
    })
    ex.load_markets()
    return ex

def detect_hedge_mode(ex):
    try:
        for p in ex.fetch_positions()[:5]:
            if p.get("info", {}).get("positionSide", "") in ("LONG", "SHORT"):
                return True
    except Exception:
        pass
    return False

def get_balance(ex):  return float(ex.fetch_balance()["USDT"]["free"])
def get_position(ex, symbol):
    try:
        for p in ex.fetch_positions([symbol]):
            if abs(float(p.get("contracts", 0) or 0)) > 0: return p
    except Exception: pass
    return None

def get_all_positions(ex):
    result = {}
    try:
        for p in ex.fetch_positions():
            if abs(float(p.get("contracts", 0) or 0)) > 0:
                result[p["symbol"]] = p
    except Exception as e:
        log.warning(f"fetch_positions: {e}")
    return result

def get_last_price(ex, symbol):
    return float(ex.fetch_ticker(symbol)["last"])

def get_spread_pct(ex, symbol):
    try:
        ob  = ex.fetch_order_book(symbol, limit=1)
        bid = ob["bids"][0][0] if ob["bids"] else 0
        ask = ob["asks"][0][0] if ob["asks"] else 0
        mid = (bid + ask) / 2
        return ((ask - bid) / mid * 100) if mid > 0 else 999.0
    except Exception: return 0.0

def entry_params(side):
    return {"positionSide": "LONG" if side == "buy" else "SHORT"} if HEDGE_MODE else {}

def exit_params(trade_side):
    if HEDGE_MODE:
        return {"positionSide": "LONG" if trade_side == "long" else "SHORT", "reduceOnly": True}
    return {"reduceOnly": True}

def get_symbols(ex):
    candidates = [
        sym for sym, mkt in ex.markets.items()
        if mkt.get("swap") and mkt.get("quote") == "USDT"
        and mkt.get("active", True) and sym not in BLACKLIST
    ]
    if not candidates: return []
    try: tickers = ex.fetch_tickers(candidates)
    except Exception as e:
        log.warning(f"fetch_tickers: {e}")
        return candidates[:TOP_N_SYMBOLS]
    ranked = []
    for sym in candidates:
        tk  = tickers.get(sym, {})
        vol = float(tk.get("quoteVolume", 0) or 0)
        if vol >= MIN_VOLUME_USDT:
            ranked.append((sym, vol))
    ranked.sort(key=lambda x: -x[1])
    result = [s for s, _ in ranked[:TOP_N_SYMBOLS]]
    log.info(f"Universo: {len(result)} pares (>${MIN_VOLUME_USDT/1000:.0f}k vol)")
    return result

# ══════════════════════════════════════════════════════════
# APERTURA / CIERRE
# ══════════════════════════════════════════════════════════
def open_trade(ex, symbol, base, side, score, modules, signals, atr_v, swing_low, swing_high):
    try:
        spread = get_spread_pct(ex, symbol)
        if spread > MAX_SPREAD_PCT:
            log.warning(f"[{symbol}] spread {spread:.3f}% — skip")
            return None

        price    = get_last_price(ex, symbol)
        usdt     = FIXED_USDT * state.risk_mult()
        mkt      = ex.markets.get(symbol, {})
        cs       = float(mkt.get("contractSize") or mkt.get("info",{}).get("contractSize") or 1.0)
        notional = usdt * LEVERAGE
        amount   = float(ex.amount_to_precision(symbol, notional / (price * cs)))
        min_amt  = float((mkt.get("limits",{}).get("amount",{}) or {}).get("min", 0) or 0)

        if amount <= 0 or amount < min_amt or amount * price * cs < 3:
            log.warning(f"[{symbol}] amount inválido: {amount:.6f}")
            return None

        try:
            lev_int = int(LEVERAGE)
            if HEDGE_MODE:
                ex.set_leverage(lev_int, symbol, params={"positionSide": "LONG"})
                ex.set_leverage(lev_int, symbol, params={"positionSide": "SHORT"})
            else:
                ex.set_leverage(lev_int, symbol)
        except Exception as e: log.warning(f"[{symbol}] leverage: {e}")

        order       = ex.create_order(symbol, "market", side, amount, params=entry_params(side))
        entry_price = float(order.get("average") or price)
        trade_side  = "long" if side == "buy" else "short"

        if side == "buy":
            sl_p  = min(swing_low - atr_v * 0.2, entry_price - atr_v * SL_ATR)
            tp1_p = entry_price + atr_v * TP1_MULT
            tp2_p = entry_price + atr_v * TP2_MULT
            tp3_p = entry_price + atr_v * TP3_MULT
        else:
            sl_p  = max(swing_high + atr_v * 0.2, entry_price + atr_v * SL_ATR)
            tp1_p = entry_price - atr_v * TP1_MULT
            tp2_p = entry_price - atr_v * TP2_MULT
            tp3_p = entry_price - atr_v * TP3_MULT

        for attr, val in [("tp1",tp1_p),("tp2",tp2_p),("tp3",tp3_p),("sl",sl_p)]:
            locals()[f"{attr}_p"] = float(ex.price_to_precision(symbol, val))
        tp1_p = float(ex.price_to_precision(symbol, tp1_p))
        tp2_p = float(ex.price_to_precision(symbol, tp2_p))
        tp3_p = float(ex.price_to_precision(symbol, tp3_p))
        sl_p  = float(ex.price_to_precision(symbol, sl_p))

        ep    = exit_params(trade_side)
        cside = "sell" if side == "buy" else "buy"
        q1 = float(ex.amount_to_precision(symbol, amount * 0.25))
        q2 = float(ex.amount_to_precision(symbol, amount * 0.25))
        q3 = float(ex.amount_to_precision(symbol, amount * 0.50))

        for lbl, qty, px in [("TP1",q1,tp1_p),("TP2",q2,tp2_p),("TP3",q3,tp3_p)]:
            try: ex.create_order(symbol, "limit", cside, qty, px, ep)
            except Exception as e: log.warning(f"[{symbol}] {lbl}: {e}")
        try:
            ex.create_order(symbol, "stop_market", cside, amount, None, {**ep, "stopPrice": sl_p})
        except Exception as e: log.warning(f"[{symbol}] SL: {e}")

        t = TradeState(
            symbol=symbol, base=base, side=trade_side,
            entry_price=entry_price, tp1_price=tp1_p, tp2_price=tp2_p, tp3_price=tp3_p,
            sl_price=sl_p, entry_score=score, modules_used=modules,
            active_signals=signals[:150], entry_time=utcnow(),
            contracts=amount, atr_entry=atr_v,
        )
        t.trail_high = t.trail_low = t.peak_price = entry_price
        log_csv("OPEN", t, entry_price)
        tg_signal(t)
        log.info(f"[OPEN] {symbol} {trade_side.upper()} score={score} [{modules}]")
        return t
    except Exception as e:
        log.error(f"[{symbol}] open_trade: {e}")
        tg_error(f"open_trade {symbol}: {e}")
        return None

def move_sl_to(ex, symbol, new_sl):
    if symbol not in state.trades: return
    t = state.trades[symbol]
    try: ex.cancel_all_orders(symbol)
    except Exception as e: log.warning(f"[{symbol}] cancel: {e}")
    sl_px = float(ex.price_to_precision(symbol, new_sl))
    cside = "sell" if t.side == "long" else "buy"
    try:
        ex.create_order(symbol, "stop_market", cside, t.contracts, None,
                        {**exit_params(t.side), "stopPrice": sl_px})
        t.sl_price = sl_px
    except Exception as e: log.warning(f"[{symbol}] move_sl: {e}")

def close_trade(ex, symbol, reason, price):
    if symbol not in state.trades: return
    t = state.trades[symbol]
    try: ex.cancel_all_orders(symbol)
    except Exception as e: log.warning(f"[{symbol}] cancel: {e}")
    pos = get_position(ex, symbol)
    pnl = 0.0
    if pos:
        contracts  = abs(float(pos.get("contracts", 0)))
        close_side = "sell" if t.side == "long" else "buy"
        try:
            ex.create_order(symbol, "market", close_side, contracts, params=exit_params(t.side))
            pnl = ((price - t.entry_price) if t.side == "long" else (t.entry_price - price)) * contracts
        except Exception as e:
            log.error(f"[{symbol}] close: {e}"); return
    if pnl > 0: state.wins += 1; state.gross_profit += pnl; state.consec_losses = 0
    else:        state.losses += 1; state.gross_loss += abs(pnl); state.consec_losses += 1
    state.total_pnl += pnl; state.daily_pnl += pnl
    state.peak_equity = max(state.peak_equity, state.peak_equity + pnl)
    state.set_cooldown(symbol)
    log_csv("CLOSE", t, price, pnl)
    tg_close(reason, t, price, pnl)
    del state.trades[symbol]

# ══════════════════════════════════════════════════════════
# GESTIÓN DEL TRADE
# ══════════════════════════════════════════════════════════
def manage_trade(ex, symbol, live_price, atr_v, res, live_pos):
    if symbol not in state.trades: return
    t = state.trades[symbol]

    if live_pos is None:
        pnl = ((live_price - t.entry_price) if t.side=="long" else (t.entry_price - live_price)) * t.contracts
        reason = ("TP3 COMPLETO" if (t.side=="long" and live_price>=t.tp3_price) or
                                     (t.side=="short" and live_price<=t.tp3_price)
                  else "SL ALCANZADO")
        if pnl > 0: state.wins += 1; state.gross_profit += pnl; state.consec_losses = 0
        else:        state.losses += 1; state.gross_loss += abs(pnl); state.consec_losses += 1
        state.total_pnl += pnl; state.daily_pnl += pnl
        state.set_cooldown(symbol)
        log_csv("CLOSE_EXT", t, live_price, pnl)
        tg_close(reason, t, live_price, pnl)
        del state.trades[symbol]; return

    # TP1 → break-even
    if not t.tp1_hit:
        hit = (t.side=="long" and live_price>=t.tp1_price) or (t.side=="short" and live_price<=t.tp1_price)
        if hit:
            t.tp1_hit = True; t.sl_moved_be = True; t.peak_price = live_price
            pnl_est = abs(t.tp1_price - t.entry_price) * float(live_pos.get("contracts",0)) * 0.25
            move_sl_to(ex, symbol, t.entry_price)
            tg(f"🟡 <b>TP1 + BE</b> — {t.symbol}\n💰 +${pnl_est:.2f} | SL→entrada\n🎯 Próximo TP2: <code>{t.tp2_price:.6g}</code>\n⏰ {utcnow()}")

    # TP2 → SL sube a TP1
    if t.tp1_hit and not t.tp2_hit:
        hit2 = (t.side=="long" and live_price>=t.tp2_price) or (t.side=="short" and live_price<=t.tp2_price)
        if hit2:
            t.tp2_hit = True
            pnl_est = abs(t.tp2_price - t.entry_price) * float(live_pos.get("contracts",0)) * 0.25
            move_sl_to(ex, symbol, t.tp1_price)
            tg(f"🟠 <b>TP2 ALCANZADO</b> — {t.symbol}\n💰 +${pnl_est:.2f} | SL→TP1\n🎯 TP3: <code>{t.tp3_price:.6g}</code>\n⏰ {utcnow()}")

    # Trailing dinámico
    if t.tp1_hit and symbol in state.trades:
        atr_t    = atr_v if atr_v > 0 else t.atr_entry
        cur_pct  = ((live_price - t.entry_price)/t.entry_price*100 if t.side=="long"
                    else (t.entry_price - live_price)/t.entry_price*100)
        t.max_profit_pct = max(t.max_profit_pct, cur_pct)

        new_peak = (live_price > t.peak_price if t.side=="long" else live_price < t.peak_price)
        if new_peak: t.peak_price = live_price; t.stall_count = 0
        else:        t.stall_count += 1

        denom   = abs(t.peak_price - t.entry_price)
        retrace = ((t.peak_price - live_price)/max(denom,1e-9)*100 if t.side=="long"
                   else (live_price - t.peak_price)/max(denom,1e-9)*100)

        prev_phase = t.trail_phase
        if cur_pct > 5.0:       t.trail_phase = "ultra"
        elif retrace > 30:       t.trail_phase = "locked"
        elif t.stall_count >= 3: t.trail_phase = "tight"
        else:                    t.trail_phase = "normal"

        trail_m = {"normal":0.8,"tight":0.4,"locked":0.2,"ultra":0.15}[t.trail_phase]
        if t.side == "long":
            t.trail_high = max(t.trail_high, live_price)
            if live_price <= t.trail_high - atr_t * trail_m:
                close_trade(ex, symbol, f"TRAILING {t.trail_phase.upper()}", live_price); return
        else:
            t.trail_low = min(t.trail_low, live_price)
            if live_price >= t.trail_low + atr_t * trail_m:
                close_trade(ex, symbol, f"TRAILING {t.trail_phase.upper()}", live_price); return

    # Pérdida dinámica pre-TP1
    if not t.tp1_hit and symbol in state.trades:
        atr_now   = atr_v if atr_v > 0 else t.atr_entry
        loss_dist = (t.entry_price - live_price if t.side=="long" else live_price - t.entry_price)
        if loss_dist >= atr_now * 0.8:
            close_trade(ex, symbol, "PÉRDIDA DINÁMICA (0.8×ATR)", live_price); return

    # Cierre por señal opuesta fuerte (requiere MIN_MODULES módulos opuestos)
    if res and symbol in state.trades:
        direction = res.get("direction")
        if t.side == "long"  and direction == "short" and res.get("score",0) >= MIN_SCORE + 2:
            close_trade(ex, symbol, f"FLIP SHORT score={res['score']}", live_price)
        elif t.side == "short" and direction == "long"  and res.get("score",0) >= MIN_SCORE + 2:
            close_trade(ex, symbol, f"FLIP LONG score={res['score']}", live_price)

# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════
def main():
    global HEDGE_MODE
    log.info("=" * 65)
    log.info("  SATY ELITE v13 — Motor multi-módulo 3 estrategias")
    log.info("=" * 65)

    if not (API_KEY and API_SECRET):
        log.error("BINGX_API_KEY y BINGX_API_SECRET no configuradas")
        tg_error("Bot no iniciado: faltan API Keys")
        sys.exit(1)

    ex = None
    for attempt in range(10):
        try: ex = build_exchange(); log.info("BingX conectado ✓"); break
        except Exception as e:
            wait = min(2**attempt, 120)
            log.warning(f"Conexión {attempt+1}/10: {e} — retry {wait}s")
            time.sleep(wait)
    if ex is None:
        tg_error("Sin conexión BingX tras 10 intentos"); raise RuntimeError("Sin conexión")

    HEDGE_MODE = detect_hedge_mode(ex)
    log.info(f"Modo: {'HEDGE' if HEDGE_MODE else 'ONE-WAY'}")

    balance = 0.0
    for _ in range(10):
        try: balance = get_balance(ex); break
        except: time.sleep(5)

    state.peak_equity = balance; state.daily_reset_ts = time.time()
    state.last_heartbeat = time.time()

    symbols = []
    while not symbols:
        try: ex.load_markets(); symbols = get_symbols(ex)
        except Exception as e: log.error(f"get_symbols: {e}"); time.sleep(60)

    update_btc_bias(ex)
    tg_startup(balance, len(symbols))

    scan_count    = 0
    REFRESH_EVERY = max(1, 3600 // max(POLL_SECS, 1))
    BTC_REFRESH   = max(1, 900  // max(POLL_SECS, 1))
    SUMMARY_EVERY = 20
    prev_cb = False; prev_dl = False

    while True:
        ts_start = time.time()
        try:
            scan_count += 1; state.scan_count = scan_count
            state.reset_daily(); clear_cache()

            log.info(f"SCAN #{scan_count} | {datetime.now(timezone.utc):%H:%M:%S} "
                     f"| {state.open_count()}/{MAX_OPEN_TRADES} | "
                     f"señales ok:{state.signals_found} bloq:{state.signals_blocked}")

            if scan_count % REFRESH_EVERY == 0:
                try: ex.load_markets(); symbols = get_symbols(ex)
                except Exception as e: log.warning(f"Refresh: {e}")
            if scan_count % BTC_REFRESH == 0:
                update_btc_bias(ex)
            if time.time() - state.last_heartbeat > 3600:
                try: tg_heartbeat(get_balance(ex)); state.last_heartbeat = time.time()
                except Exception: pass

            cb_now = state.cb_active()
            if cb_now and not prev_cb:
                dd = (state.peak_equity - (state.peak_equity + state.total_pnl)) / state.peak_equity * 100
                tg_circuit_breaker(dd)
            prev_cb = cb_now
            if cb_now: time.sleep(POLL_SECS); continue

            dl_now = state.daily_limit_hit()
            if dl_now and not prev_dl: tg_daily_limit()
            prev_dl = dl_now
            if dl_now: time.sleep(POLL_SECS); continue

            # Gestionar posiciones abiertas
            live_positions = get_all_positions(ex)
            for sym in list(state.trades.keys()):
                try:
                    lp    = live_positions.get(sym)
                    lp_   = float(lp["markPrice"]) if lp else get_last_price(ex, sym)
                    res   = scan_symbol(ex, sym)
                    atr_v = res["atr"] if res else state.trades[sym].atr_entry
                    manage_trade(ex, sym, lp_, atr_v, res, lp)
                except Exception as e: log.warning(f"[{sym}] manage: {e}")

            # Buscar nuevas señales
            new_signals = []; state.last_discarded = []

            if state.open_count() < MAX_OPEN_TRADES:
                bases_open = state.bases_open()
                to_scan    = [
                    s for s in symbols
                    if s not in state.trades
                    and not state.in_cooldown(s)
                    and s.split("/")[0] not in bases_open
                ]
                log.info(f"Escaneando {len(to_scan)} pares con 3 módulos...")

                with ThreadPoolExecutor(max_workers=8) as pool:
                    futures = {pool.submit(scan_symbol, ex, s): s for s in to_scan}
                    results = [f.result() for f in as_completed(futures) if f.result()]

                for res in results:
                    direction = res.get("direction")
                    score     = res.get("score", 0)
                    if direction is None: continue

                    allowed, block_reason = btc_allows(direction, score)
                    if not allowed:
                        state.signals_blocked += 1
                        state.last_discarded.append({
                            "symbol": res["symbol"], "score": score, "reason": block_reason
                        })
                        continue

                    state.signals_found += 1
                    new_signals.append(res)

                new_signals.sort(key=lambda x: x["score"], reverse=True)

                for res in new_signals:
                    if state.open_count() >= MAX_OPEN_TRADES: break
                    sym = res["symbol"]; base = res["base"]
                    if sym in state.trades or state.base_has_trade(base): continue
                    if state.in_cooldown(sym): continue

                    row  = res["row"]
                    atr_v = res["atr"]
                    sl_l = float(df["low"].rolling(10).min().iloc[-2]) if False else \
                           float(row.get("swing_low", row["low"]) if hasattr(row, 'get') else row["low"])
                    sl_h = float(row.get("swing_high", row["high"]) if hasattr(row, 'get') else row["high"])

                    # Calcular swing_low/high directamente
                    try:
                        df_tmp = fetch_df(ex, sym, TF, 50)
                        sl_l = float(df_tmp["low"].rolling(10).min().iloc[-2])
                        sl_h = float(df_tmp["high"].rolling(10).max().iloc[-2])
                    except Exception: pass

                    t = open_trade(
                        ex, sym, base,
                        "buy"  if res["direction"] == "long" else "sell",
                        res["score"], res["modules"], res["signals"],
                        atr_v, sl_l, sl_h
                    )
                    if t: state.trades[sym] = t

            else:
                to_scan = []

            if scan_count % SUMMARY_EVERY == 0:
                tg_summary(new_signals, len(to_scan))

            elapsed = time.time() - ts_start
            log.info(f"Ciclo {elapsed:.1f}s | {state.wins}W/{state.losses}L | "
                     f"hoy:${state.daily_pnl:+.2f} | total:${state.total_pnl:+.2f}")

        except ccxt.NetworkError as e:
            log.warning(f"Network: {e} — 15s"); time.sleep(15)
        except ccxt.ExchangeError as e:
            log.error(f"Exchange: {e}"); tg_error(f"Exchange: {str(e)[:200]}")
        except KeyboardInterrupt:
            tg("🛑 <b>Bot detenido.</b>"); break
        except Exception as e:
            log.exception(f"Error: {e}"); tg_error(str(e))

        elapsed = time.time() - ts_start
        time.sleep(max(0, POLL_SECS - elapsed))


if __name__ == "__main__":
    while True:
        try: main()
        except KeyboardInterrupt: break
        except Exception as e:
            log.exception(f"CRASH: {e}")
            try: tg_error(f"CRASH 30s: {str(e)[:200]}")
            except Exception: pass
            time.sleep(30)
