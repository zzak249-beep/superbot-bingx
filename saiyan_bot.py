"""
╔══════════════════════════════════════════════════════════════╗
║         SAIYAN OCC BOT — BingX Perpetual Futures            ║
║         Basado en: SAIYAN OCC v6_1_23 (Pine Script)         ║
║         24/7 • Auto-aprendizaje • Sin bloqueos              ║
╚══════════════════════════════════════════════════════════════╝

ESTRATEGIA ORIGINAL:
  - Señal principal: crossover de ALMA/TEMA/HullMA en timeframe alterno
  - TF base: TIMEFRAME (def: 15m) × Multiplier (def: 8) = 120m señal
  - TP1: 1% (50%) | TP2: 1.5% (30%) | TP3: 2% (20%) | SL: 0.5%
  - Filtros: EMA144, RSI28, WaveTrend, Supply/Demand, SMC BOS

CARACTERÍSTICAS DEL BOT:
  - 3 TPs escalonados con tamaños configurables
  - Break-even automático tras TP1
  - TradingBrain: aprende de errores, ajusta parámetros
  - Circuit Breaker: pausa temporal (NO bloqueo permanente)
  - Modo defensivo: 50% tamaño si límite diario alcanzado
  - Telegram: notificaciones completas con retry y HTML escape
  - Universo multi-par: escanea top pares por volumen

VARIABLES OBLIGATORIAS (Railway/env):
  BINGX_API_KEY  BINGX_API_SECRET
  TELEGRAM_BOT_TOKEN  TELEGRAM_CHAT_ID

VARIABLES OPCIONALES:
  TIMEFRAME       def:15m    (TF base de velas)
  HTF_MULT        def:8      (multiplicador → señal en 120m)
  MA_TYPE         def:ALMA   (ALMA | TEMA | HullMA)
  MA_PERIOD       def:2      (periodo MA)
  ALMA_OFFSET     def:0.85
  ALMA_SIGMA      def:5
  TP1_PCT         def:1.0    (% take profit 1)
  TP2_PCT         def:1.5    (% take profit 2)
  TP3_PCT         def:2.0    (% take profit 3)
  SL_PCT          def:0.5    (% stop loss)
  TP1_QTY         def:50     (% del trade en TP1)
  TP2_QTY         def:30     (% del trade en TP2)
  TP3_QTY         def:20     (% del trade en TP3)
  FIXED_USDT      def:8.0
  LEVERAGE        def:10
  MAX_OPEN_TRADES def:4
  MIN_VOLUME_USDT def:1000000
  TOP_N_SYMBOLS   def:200
  POLL_SECONDS    def:60
  COOLDOWN_MIN    def:60
  MAX_SPREAD_PCT  def:0.8
  DAILY_LOSS_LIMIT def:4.0
  MAX_DRAWDOWN    def:15.0
  CB_PAUSE_MIN    def:30
  DEFENSIVE_MODE_ONLY def:true
  EMA_FILTER      def:true   (filtro EMA144)
  RSI_FILTER      def:true   (filtro RSI28)
  USE_WT_FILTER   def:false  (filtro WaveTrend extra)
"""

import os, time, logging, csv, json, collections
import html as _html
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import ccxt
import pandas as pd
import numpy as np

# ══════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("saiyan_occ")

# ══════════════════════════════════════════════════════════
# CONFIG — Variables de entorno
# ══════════════════════════════════════════════════════════
API_KEY    = os.environ.get("BINGX_API_KEY",    "")
API_SECRET = os.environ.get("BINGX_API_SECRET", "")
TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID",   "")

# Timeframes — Pine: stratRes = TF * intRes (15 * 8 = 120m)
TF_BASE    = os.environ.get("TIMEFRAME",     "15m")   # velas base
HTF_MULT   = int(os.environ.get("HTF_MULT", "8"))     # multiplicador señal
POLL_SECS  = int(os.environ.get("POLL_SECONDS", "60"))

# MA Signal (reproduce closeSeries/openSeries del Pine)
MA_TYPE    = os.environ.get("MA_TYPE",      "ALMA")
MA_PERIOD  = int(os.environ.get("MA_PERIOD",  "2"))
ALMA_OFF   = float(os.environ.get("ALMA_OFFSET", "0.85"))
ALMA_SIGMA = int(os.environ.get("ALMA_SIGMA", "5"))

# Risk Management — igual que el Pine
TP1_PCT    = float(os.environ.get("TP1_PCT", "1.0"))    # %
TP2_PCT    = float(os.environ.get("TP2_PCT", "1.5"))    # %
TP3_PCT    = float(os.environ.get("TP3_PCT", "2.0"))    # %
SL_PCT     = float(os.environ.get("SL_PCT",  "0.5"))    # %
TP1_QTY    = float(os.environ.get("TP1_QTY", "50"))     # % del tamaño
TP2_QTY    = float(os.environ.get("TP2_QTY", "30"))
TP3_QTY    = float(os.environ.get("TP3_QTY", "20"))

# Posición
FIXED_USDT       = float(os.environ.get("FIXED_USDT",        "8.0"))
LEVERAGE         = int(os.environ.get("LEVERAGE",            "10"))
MAX_OPEN_TRADES  = int(os.environ.get("MAX_OPEN_TRADES",     "4"))
COOLDOWN_MIN     = int(os.environ.get("COOLDOWN_MIN",        "60"))
MAX_SPREAD_PCT   = float(os.environ.get("MAX_SPREAD_PCT",    "0.8"))
MIN_VOLUME_USDT  = float(os.environ.get("MIN_VOLUME_USDT",   "1000000"))
TOP_N_SYMBOLS    = int(os.environ.get("TOP_N_SYMBOLS",       "200"))

# Blacklist manual
_bl = os.environ.get("BLACKLIST", "")
BLACKLIST: List[str] = [s.strip() for s in _bl.split(",") if s.strip()]

# Filtros adicionales
EMA_FILTER    = os.environ.get("EMA_FILTER",    "true").lower() == "true"
RSI_FILTER    = os.environ.get("RSI_FILTER",    "true").lower() == "true"
USE_WT_FILTER = os.environ.get("USE_WT_FILTER", "false").lower() == "true"
RSI_OB        = float(os.environ.get("RSI_OB", "65"))   # Pine: rsiOb = rsi > 65
RSI_OS        = float(os.environ.get("RSI_OS", "35"))   # Pine: rsiOs = rsi < 35
EMA_LEN       = int(os.environ.get("EMA_LEN",  "144"))  # Pine: ema(close, 144)
WT_CHAN_LEN   = int(os.environ.get("WT_CHAN_LEN", "5"))
WT_AVG_LEN    = int(os.environ.get("WT_AVG_LEN", "10"))

# Seguridad y protección
DAILY_LOSS_LIMIT    = float(os.environ.get("DAILY_LOSS_LIMIT",  "4.0"))
CB_DD               = float(os.environ.get("MAX_DRAWDOWN",      "15.0"))
CB_PAUSE_MIN        = int(os.environ.get("CB_PAUSE_MIN",        "30"))
DEFENSIVE_MODE_ONLY = os.environ.get("DEFENSIVE_MODE_ONLY", "true").lower() == "true"
MAX_CONSEC_LOSS     = int(os.environ.get("MAX_CONSEC_LOSS",  "3"))

# Paths
CSV_PATH   = "/tmp/saiyan_occ_trades.csv"
BRAIN_PATH = "/tmp/saiyan_occ_brain.json"

HEDGE_MODE: bool = False

# ══════════════════════════════════════════════════════════
# TELEGRAM — retry + html.escape
# ══════════════════════════════════════════════════════════
def esc(text) -> str:
    return _html.escape(str(text), quote=False)

def tg(msg: str, silent: bool = False):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    for attempt in range(3):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                data={"chat_id": TG_CHAT_ID, "text": msg[:4096],
                      "parse_mode": "HTML", "disable_notification": silent},
                timeout=15
            )
            if r.ok:
                return
            if r.status_code == 400:
                # fallback sin HTML
                plain = msg
                for tag in ["<b>","</b>","<code>","</code>","<i>","</i>"]:
                    plain = plain.replace(tag, "")
                requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    data={"chat_id": TG_CHAT_ID, "text": plain[:4096], "disable_notification": silent},
                    timeout=15
                )
                return
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                log.warning(f"TG error: {e}")


# ══════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════
def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def tf_to_minutes(tf: str) -> int:
    """Convierte '15m' -> 15, '1h' -> 60, '4h' -> 240"""
    tf = tf.lower().strip()
    if tf.endswith("m"):  return int(tf[:-1])
    if tf.endswith("h"):  return int(tf[:-1]) * 60
    if tf.endswith("d"):  return int(tf[:-1]) * 1440
    return int(tf)

def signal_tf() -> str:
    """Calcula el TF de señal = TF_BASE_minutos * HTF_MULT"""
    base_min  = tf_to_minutes(TF_BASE)
    signal_min = base_min * HTF_MULT
    if signal_min < 60:   return f"{signal_min}m"
    if signal_min % 60 == 0: return f"{signal_min // 60}h"
    return f"{signal_min}m"

SIGNAL_TF = signal_tf()
log.info(f"TF base: {TF_BASE} | Señal: {SIGNAL_TF} ({tf_to_minutes(TF_BASE)} × {HTF_MULT})")


# ══════════════════════════════════════════════════════════
# CACHE OHLCV
# ══════════════════════════════════════════════════════════
_cache: Dict[str, Tuple[float, pd.DataFrame]] = {}
CACHE_TTL = 55

def fetch_df(ex: ccxt.Exchange, symbol: str, tf: str, limit: int = 500) -> pd.DataFrame:
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

def clear_cache():
    _cache.clear()


# ══════════════════════════════════════════════════════════
# INDICADORES — replica Pine Script
# ══════════════════════════════════════════════════════════
def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()

def _wma(s: pd.Series, n: int) -> pd.Series:
    weights = np.arange(1, n + 1, dtype=float)
    return s.rolling(n).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)

def calc_tema(s: pd.Series, n: int) -> pd.Series:
    e1 = _ema(s, n)
    e2 = _ema(e1, n)
    e3 = _ema(e2, n)
    return 3 * e1 - 3 * e2 + e3

def calc_hullma(s: pd.Series, n: int) -> pd.Series:
    half = max(n // 2, 1)
    sqrtn = max(int(round(np.sqrt(n))), 1)
    return _wma(2 * _wma(s, half) - _wma(s, n), sqrtn)

def calc_alma(s: pd.Series, n: int, offset: float = 0.85, sigma: int = 5) -> pd.Series:
    """ALMA: Arnaud Legoux Moving Average — replica exacta del Pine"""
    def alma_window(x):
        m = offset * (n - 1)
        s_ = n / sigma
        w  = np.exp(-((np.arange(n) - m) ** 2) / (2 * s_ * s_))
        w /= w.sum()
        return np.dot(x, w)
    return s.rolling(n).apply(alma_window, raw=True)

def calc_ma(s: pd.Series, ma_type: str, period: int,
            offset: float = 0.85, sigma: int = 5) -> pd.Series:
    """Replica la función variant() del Pine Script"""
    t = ma_type.upper()
    if t == "ALMA":   return calc_alma(s, period, offset, sigma)
    if t == "TEMA":   return calc_tema(s, period)
    if t == "HULLMA": return calc_hullma(s, period)
    if t == "EMA":    return _ema(s, period)
    if t == "SMA":    return _sma(s, period)
    return calc_alma(s, period, offset, sigma)  # default ALMA

def calc_rsi(s: pd.Series, n: int = 28) -> pd.Series:
    d  = s.diff()
    g  = d.clip(lower=0).ewm(span=n, adjust=False).mean()
    lo = (-d.clip(upper=0)).ewm(span=n, adjust=False).mean()
    return 100 - (100 / (1 + g / lo.replace(0, np.nan)))

def calc_atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()

def calc_wavetrend(df: pd.DataFrame, chan_len: int = 5, avg_len: int = 10) -> Tuple[pd.Series, pd.Series]:
    """WaveTrend — replica Pine wavetrend()"""
    ap  = (df["high"] + df["low"] + df["close"]) / 3
    esa = ap.ewm(span=chan_len, adjust=False).mean()
    d   = (ap - esa).abs().ewm(span=chan_len, adjust=False).mean()
    ci  = (ap - esa) / (0.015 * d.replace(0, np.nan))
    wt1 = ci.ewm(span=avg_len, adjust=False).mean()
    wt2 = wt1.rolling(3).mean()
    return wt1, wt2

def calc_pivot_high(df: pd.DataFrame, lb: int = 10, rb: int = 10) -> pd.Series:
    """ta.pivothigh equivalente"""
    h = df["high"]
    result = pd.Series(np.nan, index=df.index)
    for i in range(lb, len(df) - rb):
        val = h.iloc[i]
        if val == h.iloc[i-lb:i+rb+1].max():
            result.iloc[i] = val
    return result

def calc_pivot_low(df: pd.DataFrame, lb: int = 10, rb: int = 10) -> pd.Series:
    """ta.pivotlow equivalente"""
    l = df["low"]
    result = pd.Series(np.nan, index=df.index)
    for i in range(lb, len(df) - rb):
        val = l.iloc[i]
        if val == l.iloc[i-lb:i+rb+1].min():
            result.iloc[i] = val
    return result


# ══════════════════════════════════════════════════════════
# SEÑAL PRINCIPAL SAIYAN OCC
# ══════════════════════════════════════════════════════════
def compute_saiyan_signal(df_signal: pd.DataFrame) -> pd.DataFrame:
    """
    Replica el núcleo del Pine Script:
      closeSeries = variant(basisType, close, basisLen) en TF de señal
      openSeries  = variant(basisType, open,  basisLen) en TF de señal
      leTrigger   = crossover (closeSeries, openSeries)  → LONG
      seTrigger   = crossunder(closeSeries, openSeries)  → SHORT
    """
    df = df_signal.copy()
    c  = df["close"]
    o  = df["open"]

    # Calcula MA sobre close y open (replica closeSeries/openSeries)
    close_ma = calc_ma(c, MA_TYPE, MA_PERIOD, ALMA_OFF, ALMA_SIGMA)
    open_ma  = calc_ma(o, MA_TYPE, MA_PERIOD, ALMA_OFF, ALMA_SIGMA)

    df["close_ma"] = close_ma
    df["open_ma"]  = open_ma

    # Crossover/Crossunder (leTrigger/seTrigger)
    df["le_trigger"] = (close_ma > open_ma) & (close_ma.shift(1) <= open_ma.shift(1))
    df["se_trigger"] = (close_ma < open_ma) & (close_ma.shift(1) >= open_ma.shift(1))

    # Dirección actual del MA (para filtro de tendencia)
    df["ma_bull"] = close_ma > open_ma
    df["ma_bear"] = close_ma < open_ma

    return df

def compute_base_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula indicadores de filtro en TF base"""
    df = df.copy()
    c = df["close"]
    h = df["high"]
    l = df["low"]

    # EMA144 — Pine: ema = ta.ema(close, 144); emaBull = close > ema
    df["ema144"]   = _ema(c, EMA_LEN)
    df["ema_bull"] = c > df["ema144"]
    df["ema_bear"] = c < df["ema144"]

    # RSI28 — Pine: rsi = ta.rsi(close, 28)
    df["rsi"]    = calc_rsi(c, 28)
    df["rsi_ob"] = df["rsi"] > RSI_OB  # > 65
    df["rsi_os"] = df["rsi"] < RSI_OS  # < 35

    # ATR
    df["atr"] = calc_atr(df, 14)

    # WaveTrend (filtro opcional)
    if USE_WT_FILTER:
        wt1, wt2 = calc_wavetrend(df, WT_CHAN_LEN, WT_AVG_LEN)
        df["wt1"] = wt1
        df["wt2"] = wt2
        df["wt_bull"] = wt1 > wt2
        df["wt_bear"] = wt1 < wt2

    # Volume
    df["vol_ma"]   = _sma(df["volume"], 20)
    df["vol_spike"]= df["volume"] > df["vol_ma"] * 1.2

    return df

def check_filters(row_base: pd.Series, signal_bull: bool) -> Tuple[bool, str]:
    """
    Aplica filtros adicionales al Pine Script:
    - EMA144: close > ema para long, < ema para short
    - RSI: no entrar en sobrecompra/sobreventa extrema
    - WaveTrend: confirmación de dirección
    Retorna (ok, reason)
    """
    # Filtro EMA144
    if EMA_FILTER:
        if signal_bull and not bool(row_base.get("ema_bull", True)):
            return False, "EMA144 bajista (no long)"
        if not signal_bull and not bool(row_base.get("ema_bear", True)):
            return False, "EMA144 alcista (no short)"

    # Filtro RSI — no comprar en OB, no vender en OS
    rsi = float(row_base.get("rsi", 50))
    if signal_bull and rsi > RSI_OB:
        return False, f"RSI OB {round(rsi,1)} (evitar long)"
    if not signal_bull and rsi < RSI_OS:
        return False, f"RSI OS {round(rsi,1)} (evitar short)"

    # Filtro WaveTrend
    if USE_WT_FILTER:
        wt_bull = bool(row_base.get("wt_bull", True))
        wt_bear = bool(row_base.get("wt_bear", True))
        if signal_bull and wt_bear and not wt_bull:
            return False, "WaveTrend bajista"
        if not signal_bull and wt_bull and not wt_bear:
            return False, "WaveTrend alcista"

    return True, "OK"


# ══════════════════════════════════════════════════════════
# TRADING BRAIN — Auto-aprendizaje
# ══════════════════════════════════════════════════════════
class TradingBrain:
    def __init__(self):
        self.trades:          List[dict]         = []
        self.pair_stats:      Dict[str, dict]    = {}
        self.tp_stats:        Dict[str, dict]    = {}   # stats por TP alcanzado
        self.hour_stats:      Dict[int, dict]    = {}
        self.blacklist:       Dict[str, float]   = {}
        self.total_trades:    int                = 0
        self.total_wins:      int                = 0
        self.adaptive_tp1:    float              = TP1_PCT
        self.adaptive_tp2:    float              = TP2_PCT
        self.adaptive_tp3:    float              = TP3_PCT
        self.adaptive_sl:     float              = SL_PCT
        self.load()

    def load(self):
        try:
            if os.path.exists(BRAIN_PATH):
                with open(BRAIN_PATH) as f:
                    data = json.load(f)
                self.trades        = data.get("trades", [])[-500:]
                self.pair_stats    = data.get("pair_stats", {})
                self.tp_stats      = data.get("tp_stats", {})
                self.hour_stats    = {int(k): v for k, v in data.get("hour_stats", {}).items()}
                self.blacklist     = data.get("blacklist", {})
                self.total_trades  = data.get("total_trades", 0)
                self.total_wins    = data.get("total_wins", 0)
                self.adaptive_tp1  = data.get("adaptive_tp1", TP1_PCT)
                self.adaptive_tp2  = data.get("adaptive_tp2", TP2_PCT)
                self.adaptive_tp3  = data.get("adaptive_tp3", TP3_PCT)
                self.adaptive_sl   = data.get("adaptive_sl",  SL_PCT)
                log.info(f"Brain cargado: {self.total_trades} trades, "
                         f"TP1:{self.adaptive_tp1}% TP2:{self.adaptive_tp2}% SL:{self.adaptive_sl}%")
        except Exception as e:
            log.warning(f"Brain load error: {e}")

    def save(self):
        try:
            with open(BRAIN_PATH, "w") as f:
                json.dump({
                    "trades":        self.trades[-500:],
                    "pair_stats":    self.pair_stats,
                    "tp_stats":      self.tp_stats,
                    "hour_stats":    self.hour_stats,
                    "blacklist":     self.blacklist,
                    "total_trades":  self.total_trades,
                    "total_wins":    self.total_wins,
                    "adaptive_tp1":  self.adaptive_tp1,
                    "adaptive_tp2":  self.adaptive_tp2,
                    "adaptive_tp3":  self.adaptive_tp3,
                    "adaptive_sl":   self.adaptive_sl,
                }, f, indent=2)
        except Exception as e:
            log.warning(f"Brain save: {e}")

    def is_blacklisted(self, symbol: str) -> bool:
        expiry = self.blacklist.get(symbol, 0)
        if time.time() < expiry:
            return True
        if symbol in self.blacklist:
            del self.blacklist[symbol]
        return False

    def record_trade(self, symbol: str, side: str, pnl: float,
                     reason: str, rsi: float, ema_bull: bool, hour: int):
        win  = 1 if pnl > 0 else 0
        self.total_trades += 1
        if win: self.total_wins += 1

        record = {
            "ts": time.time(), "symbol": symbol, "side": side,
            "pnl": pnl, "win": win, "reason": reason,
            "rsi": round(rsi, 1), "ema_bull": ema_bull, "hour": hour,
        }
        self.trades.append(record)

        # Stats por par
        if symbol not in self.pair_stats:
            self.pair_stats[symbol] = {"wins": 0, "losses": 0, "pnl": 0.0, "streak": 0}
        ps = self.pair_stats[symbol]
        ps["pnl"] += pnl
        if win:
            ps["wins"] += 1
            ps["streak"] = max(0, ps["streak"]) + 1
        else:
            ps["losses"] += 1
            ps["streak"] = min(0, ps["streak"]) - 1
            if ps["streak"] <= -3:
                self.blacklist[symbol] = time.time() + 6 * 3600
                tg(f"<b>BRAIN BLACKLIST</b> — <code>{esc(symbol)}</code>\n"
                   f"3 pérdidas seguidas — bloqueado 6h\n{utcnow()}")
                log.warning(f"Brain blacklist: {symbol} 6h")

        # Stats por razón (TP/SL)
        cat = reason.split()[0]  # "TP1", "TP2", "TP3", "SL", etc.
        if cat not in self.tp_stats:
            self.tp_stats[cat] = {"wins": 0, "losses": 0, "total_pnl": 0.0}
        ts_ = self.tp_stats[cat]
        if win: ts_["wins"] += 1
        else:   ts_["losses"] += 1
        ts_["total_pnl"] += pnl

        # Stats por hora
        hr = self.hour_stats.setdefault(hour, {"wins": 0, "losses": 0})
        if win: hr["wins"] += 1
        else:   hr["losses"] += 1

        # Ajuste cada 25 trades
        if self.total_trades % 25 == 0:
            self._adapt_levels()

        self.save()

    def _adapt_levels(self):
        """Ajusta TP/SL basado en estadísticas recientes"""
        global TP1_PCT, TP2_PCT, TP3_PCT, SL_PCT
        if len(self.trades) < 20:
            return
        recent  = self.trades[-25:]
        wr      = sum(t["win"] for t in recent) / len(recent)
        avg_pnl = sum(t["pnl"] for t in recent) / len(recent)

        changes = []

        # Si win rate > 65%: ampliar TPs ligeramente
        if wr > 0.65:
            new_tp1 = min(self.adaptive_tp1 * 1.05, TP1_PCT * 2.0)
            new_tp2 = min(self.adaptive_tp2 * 1.05, TP2_PCT * 2.0)
            new_tp3 = min(self.adaptive_tp3 * 1.05, TP3_PCT * 2.0)
            if new_tp1 != self.adaptive_tp1:
                changes.append(f"TP1 {round(self.adaptive_tp1,2)}% → {round(new_tp1,2)}%")
                self.adaptive_tp1 = round(new_tp1, 3)
                self.adaptive_tp2 = round(new_tp2, 3)
                self.adaptive_tp3 = round(new_tp3, 3)
                TP1_PCT = self.adaptive_tp1
                TP2_PCT = self.adaptive_tp2
                TP3_PCT = self.adaptive_tp3

        # Si win rate < 40%: reducir TPs, apretar SL
        elif wr < 0.40:
            new_tp1 = max(self.adaptive_tp1 * 0.95, TP1_PCT * 0.5)
            new_sl  = max(self.adaptive_sl  * 0.95, SL_PCT  * 0.5)
            if new_tp1 != self.adaptive_tp1:
                changes.append(f"TP1 {round(self.adaptive_tp1,2)}% → {round(new_tp1,2)}%")
                changes.append(f"SL {round(self.adaptive_sl,2)}% → {round(new_sl,2)}%")
                self.adaptive_tp1 = round(new_tp1, 3)
                self.adaptive_sl  = round(new_sl, 3)
                TP1_PCT = self.adaptive_tp1
                SL_PCT  = self.adaptive_sl

        if changes:
            msg = (f"<b>BRAIN AJUSTE</b>\n"
                   f"WR últimos 25: {round(wr*100)}% | PnL med: ${round(avg_pnl,3)}\n"
                   + "\n".join(esc(c) for c in changes)
                   + f"\n{utcnow()}")
            tg(msg)
            log.info("Brain: " + " | ".join(changes))

    def get_report(self) -> str:
        if self.total_trades < 3:
            return f"Brain: {self.total_trades} trades — acumulando datos..."
        recent  = self.trades[-30:]
        wins    = sum(t["win"] for t in recent)
        wr      = wins / len(recent) * 100 if recent else 0
        avg_pnl = sum(t["pnl"] for t in recent) / len(recent) if recent else 0
        bl_act  = sum(1 for exp in self.blacklist.values() if time.time() < exp)

        tp_lines = []
        for cat in ["TP1","TP2","TP3","SL"]:
            d = self.tp_stats.get(cat)
            if d:
                tot = d["wins"] + d["losses"]
                tp_lines.append(f"  {cat}: {d['wins']}W/{d['losses']}L "
                                f"${round(d['total_pnl'],2)}")

        lines = [
            f"<b>BRAIN SAIYAN</b> | {self.total_trades} total",
            f"Últimos 30: {wins}W/{len(recent)-wins}L = {round(wr)}% | Avg: ${round(avg_pnl,3)}",
            f"TPs adaptativos: TP1={self.adaptive_tp1}% TP2={self.adaptive_tp2}% TP3={self.adaptive_tp3}% SL={self.adaptive_sl}%",
            f"Pares bloqueados: {bl_act}",
        ]
        if tp_lines:
            lines.append("Por razón:\n" + "\n".join(tp_lines[:4]))
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════
# ESTADO DEL BOT
# ══════════════════════════════════════════════════════════
@dataclass
class TradeState:
    symbol:      str   = ""
    base:        str   = ""
    side:        str   = ""   # "long" | "short"
    entry_price: float = 0.0
    tp1_price:   float = 0.0
    tp2_price:   float = 0.0
    tp3_price:   float = 0.0
    sl_price:    float = 0.0
    # Cantidades por tramo
    tp1_qty:     float = 0.0  # contratos para TP1
    tp2_qty:     float = 0.0
    tp3_qty:     float = 0.0
    total_qty:   float = 0.0
    # Estado del trade
    tp1_hit:     bool  = False
    tp2_hit:     bool  = False
    sl_moved_be: bool  = False
    entry_time:  str   = ""
    bar_count:   int   = 0
    atr_entry:   float = 0.0
    rsi_entry:   float = 0.0
    ema_bull:    bool  = True
    # PnL parcial
    partial_pnl: float = 0.0


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
    missed_signals: int = 0
    last_signal_ts: Dict[str, float] = field(default_factory=dict)

    def open_count(self)  -> int:  return len(self.trades)
    def bases_open(self)  -> Dict[str, str]:
        return {t.base: t.side for t in self.trades.values()}
    def base_has_trade(self, base: str) -> bool:
        return base in self.bases_open()
    def win_rate(self)    -> float:
        t = self.wins + self.losses
        return (self.wins / t * 100) if t else 0.0
    def profit_factor(self) -> float:
        return (self.gross_profit / self.gross_loss) if self.gross_loss else 0.0

    def cb_active(self) -> bool:
        if self.peak_equity <= 0: return False
        dd = (self.peak_equity - (self.peak_equity + self.total_pnl)) / self.peak_equity * 100
        return dd >= CB_DD

    def daily_defensive(self) -> bool:
        if self.peak_equity <= 0: return False
        return self.daily_pnl < 0 and abs(self.daily_pnl) / self.peak_equity * 100 >= DAILY_LOSS_LIMIT

    def daily_limit_hit(self) -> bool:
        if DEFENSIVE_MODE_ONLY: return False
        return self.daily_defensive()

    def risk_mult(self) -> float:
        if self.daily_defensive():                    return 0.5
        if self.consec_losses >= MAX_CONSEC_LOSS:     return 0.5
        return 1.0

    def in_cooldown(self, symbol: str) -> bool:
        return time.time() - self.cooldowns.get(symbol, 0) < COOLDOWN_MIN * 60

    def set_cooldown(self, symbol: str):
        self.cooldowns[symbol] = time.time()

    def reset_daily(self):
        now = time.time()
        if now - self.daily_reset_ts > 86400:
            was_def = self.daily_defensive()
            self.daily_pnl = 0.0
            self.daily_reset_ts = now
            log.info("Daily PnL reseteado")
            if was_def:
                tg(f"Nuevo día — modo defensivo terminado. Operando al 100%\n{utcnow()}")


state = BotState()
brain = TradingBrain()
_defensive_active = False
_cb_last_ts       = 0.0


# ══════════════════════════════════════════════════════════
# CSV LOG
# ══════════════════════════════════════════════════════════
def log_csv(action: str, t: TradeState, price: float, pnl: float = 0.0, reason: str = ""):
    try:
        exists = os.path.exists(CSV_PATH)
        with open(CSV_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["ts","action","symbol","side","entry","exit","pnl",
                            "qty","rsi","ema_bull","atr","bars","reason"])
            w.writerow([utcnow(), action, t.symbol, t.side,
                        round(t.entry_price,6), round(price,6), round(pnl,4),
                        t.total_qty, round(t.rsi_entry,1), t.ema_bull,
                        round(t.atr_entry,6), t.bar_count, reason])
    except Exception as e:
        log.warning(f"CSV: {e}")


# ══════════════════════════════════════════════════════════
# MENSAJES TELEGRAM
# ══════════════════════════════════════════════════════════
def tg_startup(balance: float, n: int):
    def_str = "DEFENSIVO 50%" if DEFENSIVE_MODE_ONLY else "BLOQUEO"
    tg(
        f"<b>SAIYAN OCC BOT — INICIADO</b>\n"
        f"{'='*30}\n"
        f"Estrategia: ALMA crossover {TF_BASE}×{HTF_MULT}={SIGNAL_TF}\n"
        f"MA: {MA_TYPE}({MA_PERIOD}) | Off:{ALMA_OFF} σ:{ALMA_SIGMA}\n"
        f"TP1:{TP1_PCT}% TP2:{TP2_PCT}% TP3:{TP3_PCT}% SL:{SL_PCT}%\n"
        f"Qty: {TP1_QTY}%/{TP2_QTY}%/{TP3_QTY}%\n"
        f"{'='*30}\n"
        f"Universo: {n} pares | Max trades: {MAX_OPEN_TRADES}\n"
        f"Balance: ${round(balance,2)} | ${FIXED_USDT}×{LEVERAGE}x\n"
        f"EMA filter: {'ON' if EMA_FILTER else 'OFF'} | RSI filter: {'ON' if RSI_FILTER else 'OFF'}\n"
        f"Límite diario: -{DAILY_LOSS_LIMIT}% modo {esc(def_str)}\n"
        f"CB: -{CB_DD}% pausa {CB_PAUSE_MIN}min\n"
        f"{'='*30}\n"
        f"{utcnow()}"
    )

def tg_signal(t: TradeState):
    side_icon = "🟢 LONG" if t.side == "long" else "🔴 SHORT"
    def_note  = " [DEFENSIVO 50%]" if state.daily_defensive() else ""
    tg(
        f"<b>{side_icon}</b> — <code>{esc(t.symbol)}</code>{esc(def_note)}\n"
        f"{'='*28}\n"
        f"MA {esc(MA_TYPE)}({MA_PERIOD}) crossover {esc(SIGNAL_TF)}\n"
        f"Entrada: <code>{esc(str(round(t.entry_price,6)))}</code>\n"
        f"TP1: <code>{esc(str(round(t.tp1_price,6)))}</code> ({TP1_PCT}%)\n"
        f"TP2: <code>{esc(str(round(t.tp2_price,6)))}</code> ({TP2_PCT}%)\n"
        f"TP3: <code>{esc(str(round(t.tp3_price,6)))}</code> ({TP3_PCT}%)\n"
        f"SL:  <code>{esc(str(round(t.sl_price,6)))}</code>  (-{SL_PCT}%)\n"
        f"{'='*28}\n"
        f"RSI: {round(t.rsi_entry,1)} | EMA: {'BULL' if t.ema_bull else 'BEAR'}\n"
        f"ATR: {round(t.atr_entry,6)} | ${FIXED_USDT}×{LEVERAGE}x\n"
        f"Trades: {state.open_count()}/{MAX_OPEN_TRADES}\n"
        f"{utcnow()}"
    )

def tg_tp(level: int, t: TradeState, price: float, pnl: float):
    tg(
        f"<b>TP{level} ALCANZADO</b> — <code>{esc(t.symbol)}</code>\n"
        f"Precio: <code>{esc(str(round(price,6)))}</code>\n"
        f"PnL parcial: ~${round(pnl,3)}\n"
        + (f"SL movido a BE <code>{esc(str(round(t.entry_price,6)))}</code>\n" if level == 1 else "")
        + f"{utcnow()}"
    )

def tg_close(reason: str, t: TradeState, exit_p: float, pnl: float):
    icon = "WIN" if pnl > 0 else "LOSS"
    pnl_pct = (pnl / (t.entry_price * t.total_qty) * 100) if t.total_qty > 0 else 0
    total_pnl_est = pnl + t.partial_pnl
    tg(
        f"<b>{icon}</b> — <code>{esc(t.symbol)}</code>\n"
        f"{esc(t.side.upper())} | {esc(reason)}\n"
        f"<code>{esc(str(round(t.entry_price,6)))}</code> → <code>{esc(str(round(exit_p,6)))}</code>"
        f" ({round(pnl_pct,2)}%)\n"
        f"PnL final: ${round(pnl,3)} | Total trade: ~${round(total_pnl_est,3)}\n"
        f"Barras: {t.bar_count}\n"
        f"{'='*25}\n"
        f"{state.wins}W/{state.losses}L WR:{round(state.win_rate(),1)}%\n"
        f"Hoy:${round(state.daily_pnl,2)} | Total:${round(state.total_pnl,2)}\n"
        f"{utcnow()}"
    )

def tg_heartbeat(balance: float):
    open_str = ", ".join(
        f"{esc(b)}({'L' if s=='long' else 'S'})"
        for b, s in state.bases_open().items()
    ) or "ninguna"
    def_str = " [DEFENSIVO 50%]" if state.daily_defensive() else ""
    tg(
        f"<b>HEARTBEAT SAIYAN OCC</b>\n"
        f"{'='*25}\n"
        f"Balance: ${round(balance,2)} | Hoy: ${round(state.daily_pnl,2)}{esc(def_str)}\n"
        f"Trades: {state.open_count()}/{MAX_OPEN_TRADES} | {open_str}\n"
        f"{state.wins}W/{state.losses}L | WR:{round(state.win_rate(),1)}% | PF:{round(state.profit_factor(),2)}\n"
        f"{'='*25}\n"
        + brain.get_report()
    )

def tg_manual_signal(symbol: str, side: str, entry: float,
                     tp1: float, tp2: float, tp3: float, sl: float, reason: str):
    state.missed_signals += 1
    tg(
        f"<b>⚡ SEÑAL MANUAL — {esc(side.upper())}</b>\n"
        f"<code>{esc(symbol)}</code> | {esc(reason)}\n"
        f"{'='*28}\n"
        f"Entrada: <code>{esc(str(round(entry,6)))}</code>\n"
        f"TP1: <code>{esc(str(round(tp1,6)))}</code> ({TP1_PCT}%)\n"
        f"TP2: <code>{esc(str(round(tp2,6)))}</code> ({TP2_PCT}%)\n"
        f"TP3: <code>{esc(str(round(tp3,6)))}</code> ({TP3_PCT}%)\n"
        f"SL:  <code>{esc(str(round(sl,6)))}</code>  (-{SL_PCT}%)\n"
        f"Señales perdidas: {state.missed_signals}\n"
        f"{utcnow()}"
    )

def tg_defensive():
    tg(
        f"<b>MODO DEFENSIVO ACTIVADO</b>\n"
        f"Límite diario -{DAILY_LOSS_LIMIT}% alcanzado.\n"
        f"Bot SIGUE OPERANDO al 50% de tamaño.\n"
        f"Hoy: ${round(state.daily_pnl,2)}\n"
        f"Se restaura el día siguiente.\n{utcnow()}"
    )

def tg_cb(dd: float):
    tg(
        f"<b>CIRCUIT BREAKER — PAUSA {CB_PAUSE_MIN}min</b>\n"
        f"Drawdown: {round(dd,1)}% (límite {CB_DD}%)\n"
        f"El bot pausa y reinicia automáticamente.\n"
        f"Total PnL: ${round(state.total_pnl,2)}\n{utcnow()}"
    )

def tg_summary(signals: List[dict], n_scanned: int):
    top = "\n".join(
        f"  {'L' if s['side']=='long' else 'S'} {esc(s['symbol'])} | RSI:{round(s['rsi'],1)}"
        for s in signals[:5]
    ) or "  (ninguna)"
    open_lines = "\n".join(
        f"  {'L' if ts.side=='long' else 'S'} {esc(sym)} E:{round(ts.entry_price,5)}"
        + (" BE" if ts.sl_moved_be else "")
        for sym, ts in state.trades.items()
    ) or "  (ninguna)"
    def_str = " [DEF]" if state.daily_defensive() else ""
    tg(
        f"<b>RESUMEN</b> — {n_scanned} pares | {utcnow()}\n"
        f"Señales:\n{top}\n"
        f"{'='*28}\n"
        f"Posiciones ({state.open_count()}/{MAX_OPEN_TRADES}):\n{open_lines}\n"
        f"{'='*28}\n"
        f"Hoy:${round(state.daily_pnl,2)}{esc(def_str)}\n"
        f"{state.wins}W/{state.losses}L PF:{round(state.profit_factor(),2)}"
    )


# ══════════════════════════════════════════════════════════
# EXCHANGE
# ══════════════════════════════════════════════════════════
def build_exchange() -> ccxt.Exchange:
    ex = ccxt.bingx({
        "apiKey": API_KEY, "secret": API_SECRET,
        "options": {"defaultType": "swap"},
        "enableRateLimit": True
    })
    ex.load_markets()
    return ex

def detect_hedge_mode(ex: ccxt.Exchange) -> bool:
    try:
        for p in ex.fetch_positions()[:5]:
            if p.get("info", {}).get("positionSide", "") in ("LONG","SHORT"):
                return True
    except Exception:
        pass
    return False

def get_balance(ex: ccxt.Exchange) -> float:
    return float(ex.fetch_balance()["USDT"]["free"])

def get_position(ex: ccxt.Exchange, symbol: str) -> Optional[dict]:
    try:
        for p in ex.fetch_positions([symbol]):
            if abs(float(p.get("contracts", 0) or 0)) > 0:
                return p
    except Exception:
        pass
    return None

def get_last_price(ex: ccxt.Exchange, symbol: str) -> float:
    return float(ex.fetch_ticker(symbol)["last"])

def get_spread_pct(ex: ccxt.Exchange, symbol: str) -> float:
    try:
        ob  = ex.fetch_order_book(symbol, limit=1)
        bid = ob["bids"][0][0] if ob["bids"] else 0
        ask = ob["asks"][0][0] if ob["asks"] else 0
        mid = (bid + ask) / 2
        return ((ask - bid) / mid * 100) if mid > 0 else 999.0
    except Exception:
        return 0.0

def get_min_amount(ex: ccxt.Exchange, symbol: str) -> float:
    try:
        mkt = ex.markets.get(symbol, {})
        return float(mkt.get("limits", {}).get("amount", {}).get("min", 0) or 0)
    except Exception:
        return 0.0

def entry_params(side: str) -> dict:
    if HEDGE_MODE:
        return {"positionSide": "LONG" if side == "buy" else "SHORT"}
    return {}

def exit_params(trade_side: str) -> dict:
    if HEDGE_MODE:
        return {
            "positionSide": "LONG" if trade_side == "long" else "SHORT",
            "reduceOnly": True
        }
    return {"reduceOnly": True}


# ══════════════════════════════════════════════════════════
# UNIVERSO DE PARES
# ══════════════════════════════════════════════════════════
def get_symbols(ex: ccxt.Exchange) -> List[str]:
    candidates = [
        sym for sym, mkt in ex.markets.items()
        if mkt.get("swap") and mkt.get("quote") == "USDT"
        and mkt.get("active", True) and sym not in BLACKLIST
    ]
    if not candidates:
        return []
    try:
        tickers = ex.fetch_tickers(candidates)
    except Exception as e:
        log.warning(f"fetch_tickers: {e}")
        return candidates[:TOP_N_SYMBOLS]
    ranked = [
        (sym, float(tickers.get(sym, {}).get("quoteVolume", 0) or 0))
        for sym in candidates
        if float(tickers.get(sym, {}).get("quoteVolume", 0) or 0) >= MIN_VOLUME_USDT
    ]
    ranked.sort(key=lambda x: -x[1])
    result = [s for s, _ in ranked[:TOP_N_SYMBOLS]]
    log.info(f"Universo: {len(result)} pares")
    return result


# ══════════════════════════════════════════════════════════
# CALCULAR PRECIOS TP/SL (replica Pine Risk Management)
# ══════════════════════════════════════════════════════════
def calc_levels(entry: float, side: str) -> Tuple[float, float, float, float]:
    """
    Replica el Pine Script:
      topLvl  = src + src * (tp_pct / 100)  → para long
      botLvl  = src - src * (tp_pct / 100)  → para short
      slBotLvl= src - src * (sl_pct / 100)
      slTopLvl= src + src * (sl_pct / 100)
    """
    if side == "long":
        tp1 = entry * (1 + TP1_PCT / 100)
        tp2 = entry * (1 + TP2_PCT / 100)
        tp3 = entry * (1 + TP3_PCT / 100)
        sl  = entry * (1 - SL_PCT  / 100)
    else:
        tp1 = entry * (1 - TP1_PCT / 100)
        tp2 = entry * (1 - TP2_PCT / 100)
        tp3 = entry * (1 - TP3_PCT / 100)
        sl  = entry * (1 + SL_PCT  / 100)
    return tp1, tp2, tp3, sl


# ══════════════════════════════════════════════════════════
# ABRIR TRADE
# ══════════════════════════════════════════════════════════
def open_trade(ex: ccxt.Exchange, symbol: str, base: str, side: str,
               row_base: pd.Series) -> Optional[TradeState]:
    try:
        spread = get_spread_pct(ex, symbol)
        if spread > MAX_SPREAD_PCT:
            log.warning(f"{symbol} spread {round(spread,3)}% > max")
            return None

        try:
            ex.set_leverage(LEVERAGE, symbol,
                           params={"hedged": True} if HEDGE_MODE else {})
        except Exception as lv_err:
            log.warning(f"{symbol} set_leverage: {lv_err}")

        price   = get_last_price(ex, symbol)
        rsi_v   = float(row_base.get("rsi",    50))
        ema_bull= bool(row_base.get("ema_bull", True))
        atr_v   = float(row_base.get("atr",    0))

        # Tamaño de posición con factor de riesgo adaptativo
        usdt    = FIXED_USDT * state.risk_mult()
        raw_amt = (usdt * LEVERAGE) / price
        min_amt = get_min_amount(ex, symbol)
        raw_amt = max(raw_amt, min_amt) if min_amt > 0 else raw_amt
        amount  = float(ex.amount_to_precision(symbol, raw_amt))

        if amount <= 0 or amount * price < 3:
            log.warning(f"{symbol} notional demasiado pequeño")
            return None

        order_side = "buy" if side == "long" else "sell"
        log.info(f"OPEN {symbol} {side.upper()} ${round(usdt,1)} @ {round(price,6)}")

        order       = ex.create_order(symbol, "market", order_side, amount,
                                      params=entry_params(order_side))
        entry_price = float(order.get("average") or price)

        # Calcular niveles TP/SL
        tp1_p, tp2_p, tp3_p, sl_p = calc_levels(entry_price, side)
        tp1_p = float(ex.price_to_precision(symbol, tp1_p))
        tp2_p = float(ex.price_to_precision(symbol, tp2_p))
        tp3_p = float(ex.price_to_precision(symbol, tp3_p))
        sl_p  = float(ex.price_to_precision(symbol, sl_p))

        # Cantidades por tramo (replica Pine TP1_QTY/TP2_QTY/TP3_QTY)
        tp1_qty = float(ex.amount_to_precision(symbol, amount * TP1_QTY / 100))
        tp2_qty = float(ex.amount_to_precision(symbol, amount * TP2_QTY / 100))
        tp3_qty = max(0.0, float(ex.amount_to_precision(symbol,
                  amount - tp1_qty - tp2_qty)))

        close_side = "sell" if side == "long" else "buy"
        ep = exit_params(side)

        # Colocar órdenes de salida
        for lbl, qty, px in [("TP1", tp1_qty, tp1_p),
                              ("TP2", tp2_qty, tp2_p),
                              ("TP3", tp3_qty, tp3_p)]:
            if qty > 0:
                try:
                    ex.create_order(symbol, "limit", close_side, qty, px, ep)
                    log.info(f"{symbol} {lbl} @ {round(px,6)}")
                except Exception as e:
                    log.warning(f"{symbol} {lbl}: {e}")

        # Stop Loss
        try:
            ex.create_order(symbol, "stop_market", close_side, amount, None,
                           {**ep, "stopPrice": sl_p})
            log.info(f"{symbol} SL @ {round(sl_p,6)}")
        except Exception as e:
            log.warning(f"{symbol} SL: {e}")

        t = TradeState(
            symbol=symbol, base=base, side=side,
            entry_price=entry_price,
            tp1_price=tp1_p, tp2_price=tp2_p, tp3_price=tp3_p,
            sl_price=sl_p,
            tp1_qty=tp1_qty, tp2_qty=tp2_qty, tp3_qty=tp3_qty,
            total_qty=amount,
            entry_time=utcnow(),
            atr_entry=atr_v, rsi_entry=rsi_v, ema_bull=ema_bull,
        )
        log_csv("OPEN", t, entry_price)
        tg_signal(t)
        return t

    except Exception as e:
        err_str = str(e).lower()
        log.error(f"{symbol} open_trade: {e}")
        if any(k in err_str for k in ["insufficient","margin","balance","not enough"]):
            try:
                p  = get_last_price(ex, symbol)
                tp1, tp2, tp3, sl = calc_levels(p, side)
                tg_manual_signal(symbol, side, p, tp1, tp2, tp3, sl, str(e)[:120])
            except Exception as me:
                log.warning(f"{symbol} manual signal: {me}")
        else:
            tg(f"<b>ERROR</b> open_trade {esc(symbol)}: <code>{esc(str(e)[:200])}</code>")
        return None


# ══════════════════════════════════════════════════════════
# MOVER SL A BREAK-EVEN
# ══════════════════════════════════════════════════════════
def move_be(ex: ccxt.Exchange, symbol: str):
    if symbol not in state.trades:
        return
    t = state.trades[symbol]
    if t.sl_moved_be:
        return
    try:
        ex.cancel_all_orders(symbol)
    except Exception:
        pass
    be = float(ex.price_to_precision(symbol, t.entry_price))
    remaining = t.tp2_qty + t.tp3_qty
    close_side = "sell" if t.side == "long" else "buy"
    ep = exit_params(t.side)
    # Recolocar TP2 y TP3
    if remaining > 0:
        try:
            ex.create_order(symbol, "limit", close_side, t.tp2_qty, t.tp2_price, ep)
        except Exception as e:
            log.warning(f"{symbol} BE recolocar TP2: {e}")
        try:
            ex.create_order(symbol, "limit", close_side, t.tp3_qty, t.tp3_price, ep)
        except Exception as e:
            log.warning(f"{symbol} BE recolocar TP3: {e}")
    # Stop en BE
    try:
        ex.create_order(symbol, "stop_market", close_side, remaining, None,
                       {**ep, "stopPrice": be})
        t.sl_price = be
        t.sl_moved_be = True
        log.info(f"{symbol} BE @ {round(be,6)}")
    except Exception as e:
        log.warning(f"{symbol} BE: {e}")


# ══════════════════════════════════════════════════════════
# CERRAR TRADE
# ══════════════════════════════════════════════════════════
def close_trade(ex: ccxt.Exchange, symbol: str, reason: str, price: float):
    if symbol not in state.trades:
        return
    t = state.trades[symbol]
    try:
        ex.cancel_all_orders(symbol)
    except Exception:
        pass
    pos = get_position(ex, symbol)
    pnl = 0.0
    if pos:
        contracts  = abs(float(pos.get("contracts", 0)))
        close_side = "sell" if t.side == "long" else "buy"
        try:
            ex.create_order(symbol, "market", close_side, contracts,
                           params=exit_params(t.side))
            pnl = ((price - t.entry_price) if t.side == "long"
                   else (t.entry_price - price)) * contracts
        except Exception as e:
            log.error(f"{symbol} close: {e}")
            tg(f"<b>ERROR</b> close {esc(symbol)}: <code>{esc(str(e)[:200])}</code>")
            return

    if pnl > 0:
        state.wins += 1
        state.gross_profit += pnl
        state.consec_losses = 0
    elif pnl < 0:
        state.losses += 1
        state.gross_loss += abs(pnl)
        state.consec_losses += 1

    state.total_pnl   += pnl
    state.daily_pnl   += pnl
    state.peak_equity  = max(state.peak_equity, state.peak_equity + pnl)
    state.set_cooldown(symbol)

    log_csv("CLOSE", t, price, pnl, reason)
    tg_close(reason, t, price, pnl)
    brain.record_trade(
        symbol=symbol, side=t.side, pnl=pnl, reason=reason,
        rsi=t.rsi_entry, ema_bull=t.ema_bull,
        hour=datetime.now(timezone.utc).hour
    )
    del state.trades[symbol]


# ══════════════════════════════════════════════════════════
# GESTIONAR TRADE ABIERTO
# ══════════════════════════════════════════════════════════
def manage_trade(ex: ccxt.Exchange, symbol: str, live_price: float,
                 live_pos: Optional[dict],
                 sig_ma_bull: Optional[bool] = None):
    if symbol not in state.trades:
        return
    t = state.trades[symbol]
    t.bar_count += 1

    # Comprobar si la posición cerró externamente (por TP/SL de exchange)
    if live_pos is None:
        pnl = ((live_price - t.entry_price) if t.side == "long"
               else (t.entry_price - live_price)) * t.total_qty
        reason = "CERRADO EXTERNAMENTE (TP/SL exchange)"
        if live_price >= t.tp3_price if t.side == "long" else live_price <= t.tp3_price:
            reason = "TP3 EXCHANGE"
        elif live_price >= t.tp2_price if t.side == "long" else live_price <= t.tp2_price:
            reason = "TP2 EXCHANGE"
        elif live_price >= t.tp1_price if t.side == "long" else live_price <= t.tp1_price:
            reason = "TP1 EXCHANGE"
        if pnl > 0:
            state.wins += 1; state.gross_profit += pnl; state.consec_losses = 0
        else:
            state.losses += 1; state.gross_loss += abs(pnl); state.consec_losses += 1
        state.total_pnl  += pnl
        state.daily_pnl  += pnl
        state.set_cooldown(symbol)
        log_csv("CLOSE_EXT", t, live_price, pnl, reason)
        tg_close(reason, t, live_price, pnl)
        brain.record_trade(symbol, t.side, pnl, reason, t.rsi_entry, t.ema_bull,
                           datetime.now(timezone.utc).hour)
        del state.trades[symbol]
        return

    # Señal de reversión — cierre anticipado (como Pine: seTrigger cierra long)
    if sig_ma_bull is not None:
        if t.side == "long"  and sig_ma_bull == False:
            close_trade(ex, symbol, "SEÑAL INVERSIÓN (MA bear cross)", live_price)
            return
        if t.side == "short" and sig_ma_bull == True:
            close_trade(ex, symbol, "SEÑAL INVERSIÓN (MA bull cross)", live_price)
            return

    # TP1 manual (si el exchange no lo ejecutó)
    if not t.tp1_hit:
        tp1_hit = ((t.side == "long"  and live_price >= t.tp1_price) or
                   (t.side == "short" and live_price <= t.tp1_price))
        if tp1_hit:
            t.tp1_hit  = True
            pnl_tp1    = abs(t.tp1_price - t.entry_price) * t.tp1_qty
            t.partial_pnl += pnl_tp1
            move_be(ex, symbol)
            tg_tp(1, t, live_price, pnl_tp1)

    # SL dinámico (si por algún motivo el exchange no lo ejecuta)
    sl_hit = ((t.side == "long"  and live_price <= t.sl_price) or
              (t.side == "short" and live_price >= t.sl_price))
    if sl_hit:
        close_trade(ex, symbol, "SL DINAMICO", live_price)


# ══════════════════════════════════════════════════════════
# ESCANEAR UN SÍMBOLO
# ══════════════════════════════════════════════════════════
def scan_symbol(ex: ccxt.Exchange, symbol: str) -> Optional[dict]:
    try:
        # TF base para filtros
        df_base = fetch_df(ex, symbol, TF_BASE, 300)
        df_base = compute_base_indicators(df_base)
        row_base = df_base.iloc[-2]

        # TF de señal para el MA crossover
        df_sig = fetch_df(ex, symbol, SIGNAL_TF, 300)
        df_sig = compute_saiyan_signal(df_sig)

        row_sig  = df_sig.iloc[-2]   # última vela cerrada
        row_sig1 = df_sig.iloc[-3]   # penúltima

        if pd.isna(row_sig.get("close_ma", np.nan)):
            return None

        # Señal actual
        le_trigger = bool(row_sig.get("le_trigger", False))
        se_trigger = bool(row_sig.get("se_trigger", False))
        ma_bull    = bool(row_sig.get("ma_bull", True))
        ma_bear    = bool(row_sig.get("ma_bear", False))

        # Filtros
        side = None
        if le_trigger:
            ok, reason = check_filters(row_base, True)
            if ok: side = "long"
            else:  log.debug(f"{symbol} LONG filtrado: {reason}")
        elif se_trigger:
            ok, reason = check_filters(row_base, False)
            if ok: side = "short"
            else:  log.debug(f"{symbol} SHORT filtrado: {reason}")

        return {
            "symbol":    symbol,
            "base":      symbol.split("/")[0],
            "side":      side,
            "le":        le_trigger,
            "se":        se_trigger,
            "ma_bull":   ma_bull,
            "ma_bear":   ma_bear,
            "row_base":  row_base,
            "rsi":       float(row_base.get("rsi", 50)),
            "ema_bull":  bool(row_base.get("ema_bull", True)),
            "atr":       float(row_base.get("atr", 0)),
            "live_price":float(df_base["close"].iloc[-1]),
        }

    except Exception as e:
        log.debug(f"{symbol} scan: {e}")
        return None


# ══════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════
def main():
    global HEDGE_MODE, _defensive_active, _cb_last_ts

    log.info("=" * 65)
    log.info("  SAIYAN OCC BOT — 24/7 NONSTOP")
    log.info(f"  Señal: {MA_TYPE}({MA_PERIOD}) en {SIGNAL_TF}")
    log.info(f"  TP1:{TP1_PCT}% TP2:{TP2_PCT}% TP3:{TP3_PCT}% SL:{SL_PCT}%")
    log.info(f"  MAX_OPEN={MAX_OPEN_TRADES} | ${FIXED_USDT}×{LEVERAGE}x")
    log.info("=" * 65)

    if not (API_KEY and API_SECRET):
        log.warning("DRY-RUN: sin claves API")
        while True:
            log.info("DRY-RUN activo...")
            time.sleep(POLL_SECS)

    # Conectar exchange
    ex = None
    for attempt in range(10):
        try:
            ex = build_exchange()
            log.info("Exchange conectado")
            break
        except Exception as e:
            wait = min(2 ** attempt, 120)
            log.warning(f"Conexión {attempt+1}/10: {e} — retry {wait}s")
            time.sleep(wait)
    if ex is None:
        raise RuntimeError("No se pudo conectar al exchange")

    HEDGE_MODE = detect_hedge_mode(ex)
    log.info(f"Modo: {'HEDGE' if HEDGE_MODE else 'ONE-WAY'}")

    balance = 0.0
    for i in range(10):
        try:
            balance = get_balance(ex)
            break
        except Exception as e:
            log.warning(f"get_balance {i+1}/10: {e}")
            time.sleep(5)

    state.peak_equity   = balance
    state.daily_reset_ts = time.time()
    log.info(f"Balance: ${round(balance,2)} USDT")

    symbols: List[str] = []
    while not symbols:
        try:
            ex.load_markets()
            symbols = get_symbols(ex)
        except Exception as e:
            log.error(f"get_symbols: {e} — reintento 60s")
            time.sleep(60)

    tg_startup(balance, len(symbols))

    scan_count    = 0
    REFRESH_EVERY = max(1, 3600 // max(POLL_SECS, 1))
    HB_INTERVAL   = 3600
    SUMMARY_EVERY = 20

    while True:
        ts_start = time.time()
        try:
            scan_count += 1
            state.reset_daily()
            clear_cache()

            log.info(
                f"SCAN #{scan_count} {datetime.now(timezone.utc).strftime('%H:%M:%S')}"
                f" | {len(symbols)} pares | {state.open_count()}/{MAX_OPEN_TRADES} trades"
            )

            # Refresh universo periódico
            if scan_count % REFRESH_EVERY == 0:
                try:
                    ex.load_markets()
                    symbols = get_symbols(ex)
                except Exception as e:
                    log.warning(f"Refresh: {e}")

            # Heartbeat
            if time.time() - state.last_heartbeat > HB_INTERVAL:
                try:
                    tg_heartbeat(get_balance(ex))
                    state.last_heartbeat = time.time()
                except Exception:
                    pass

            # ── CIRCUIT BREAKER (pausa, no bloqueo) ─────────────────────
            if state.cb_active():
                now_ts = time.time()
                if now_ts - _cb_last_ts > CB_PAUSE_MIN * 60:
                    _cb_last_ts = now_ts
                    dd = (state.peak_equity - (state.peak_equity + state.total_pnl)) \
                         / state.peak_equity * 100
                    tg_cb(dd)
                    log.warning(f"CIRCUIT BREAKER — pausa {CB_PAUSE_MIN}min")
                    time.sleep(CB_PAUSE_MIN * 60)
                else:
                    time.sleep(POLL_SECS)
                continue

            # ── MODO DEFENSIVO ────────────────────────────────────────────
            was_def = _defensive_active
            _defensive_active = state.daily_defensive()
            if _defensive_active and not was_def:
                tg_defensive()
                log.warning("MODO DEFENSIVO: 50% tamaño")

            # ── GESTIONAR POSICIONES ABIERTAS ─────────────────────────────
            for sym in list(state.trades.keys()):
                try:
                    live_pos = get_position(ex, sym)
                    live_p   = (float(live_pos["markPrice"])
                                if live_pos else get_last_price(ex, sym))
                    # Re-escanear para obtener señal de inversión
                    res = scan_symbol(ex, sym)
                    ma_bull = res["ma_bull"] if res else None
                    manage_trade(ex, sym, live_p, live_pos, ma_bull)
                except Exception as e:
                    log.warning(f"{sym} manage: {e}")

            # ── BUSCAR NUEVAS SEÑALES ─────────────────────────────────────
            new_signals: List[dict] = []

            if state.open_count() < MAX_OPEN_TRADES:
                bases_open = state.bases_open()
                to_scan = [
                    s for s in symbols
                    if s not in state.trades
                    and not state.in_cooldown(s)
                    and not brain.is_blacklisted(s)
                    and s.split("/")[0] not in bases_open
                ]
                log.info(f"Escaneando {len(to_scan)} pares")

                with ThreadPoolExecutor(max_workers=10) as pool:
                    futures = {pool.submit(scan_symbol, ex, s): s for s in to_scan}
                    results = [f.result() for f in as_completed(futures)
                               if f.result() is not None]

                for res in results:
                    if res["side"] is None:
                        continue
                    sym  = res["symbol"]
                    base = res["base"]
                    if sym in state.trades or state.base_has_trade(base):
                        continue
                    if state.in_cooldown(sym):
                        continue
                    new_signals.append(res)

                # Ordenar por RSI más extremo (señal más fuerte)
                def signal_strength(r):
                    rsi = r["rsi"]
                    return abs(rsi - 50)  # más lejos del centro = más fuerte

                new_signals.sort(key=signal_strength, reverse=True)

                for sig in new_signals:
                    if state.open_count() >= MAX_OPEN_TRADES:
                        break
                    sym  = sig["symbol"]
                    base = sig["base"]
                    if sym in state.trades or state.base_has_trade(base):
                        continue
                    t = open_trade(ex, sym, base, sig["side"], sig["row_base"])
                    if t:
                        state.trades[sym] = t

            else:
                log.info(f"Max trades alcanzado ({MAX_OPEN_TRADES})")

            elapsed = time.time() - ts_start
            log.info(
                f"OK {round(elapsed,1)}s | señales:{len(new_signals)}"
                f" | {state.wins}W/{state.losses}L"
                f" | hoy:${round(state.daily_pnl,2)}"
                f" | total:${round(state.total_pnl,2)}"
                + (" [DEF]" if _defensive_active else "")
            )

            if scan_count % SUMMARY_EVERY == 0:
                tg_summary(new_signals, len(symbols))

        except ccxt.NetworkError as e:
            log.warning(f"Network: {e} — 10s")
            time.sleep(10)
        except ccxt.ExchangeError as e:
            log.error(f"Exchange: {e}")
            tg(f"Exchange error: <code>{esc(str(e)[:200])}</code>")
        except KeyboardInterrupt:
            log.info("Detenido.")
            tg("SAIYAN OCC Bot detenido.")
            break
        except Exception as e:
            log.exception(f"Error: {e}")
            tg(f"<b>ERROR:</b> <code>{esc(str(e)[:200])}</code>")

        elapsed = time.time() - ts_start
        time.sleep(max(0, POLL_SECS - elapsed))


if __name__ == "__main__":
    while True:
        try:
            main()
        except KeyboardInterrupt:
            log.info("Detenido por usuario.")
            break
        except Exception as e:
            log.exception(f"CRASH: {e}")
            try:
                tg(f"<b>CRASH</b> — reinicio en 30s: <code>{esc(str(e)[:200])}</code>")
            except Exception:
                pass
            log.info("Reiniciando en 30s...")
            time.sleep(30)
