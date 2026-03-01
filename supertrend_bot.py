"""
╔══════════════════════════════════════════════════════════════╗
║          SUPERTREND BOT v1 — Estrategia Simple              ║
║          Comparativa vs SATY ELITE v14                      ║
╠══════════════════════════════════════════════════════════════╣
║  Estrategia: 3×Supertrend + EMA200 + RSI + Volumen          ║
║  Objetivo:   Medir si una estrategia más simple              ║
║              supera a SATY en rentabilidad real              ║
╠══════════════════════════════════════════════════════════════╣
║  LÓGICA COMPLETA:                                           ║
║                                                              ║
║  ✅ LONG cuando:                                             ║
║     - Precio > EMA200 (tendencia alcista global)             ║
║     - ≥2 de 3 Supertrends VERDES (consenso tendencia)        ║
║     - RSI entre 40-65 (momentum sin sobrecompra)             ║
║     - Volumen > 1.5× media (confirmación institucional)      ║
║     - BTC alcista o neutral (filtro macro)                   ║
║                                                              ║
║  ✅ SHORT cuando:                                            ║
║     - Precio < EMA200 (tendencia bajista global)             ║
║     - ≥2 de 3 Supertrends ROJOS (consenso bajista)           ║
║     - RSI entre 35-60 (momentum bajista sin sobreventa)      ║
║     - Volumen > 1.5× media                                   ║
║     - BTC bajista o neutral                                  ║
║                                                              ║
║  🎯 SL:  en la línea del Supertrend más cercano              ║
║  🎯 TP1: 1× distancia al SL                                 ║
║  🎯 TP2: 2× distancia al SL                                 ║
║  🎯 TP3: 3× distancia al SL  (R:R 1:3)                     ║
║                                                              ║
║  Variables de entorno Railway (bot independiente):           ║
║  ST_BINGX_API_KEY      → misma o diferente cuenta BingX     ║
║  ST_BINGX_API_SECRET                                        ║
║  ST_TELEGRAM_BOT_TOKEN → mismo o diferente bot Telegram     ║
║  ST_TELEGRAM_CHAT_ID                                        ║
║  ST_FIXED_USDT         (def: 8)                             ║
║  ST_MAX_OPEN_TRADES    (def: 8)                             ║
║  ST_LEVERAGE           (def: 10)                            ║
║  ST_TIMEFRAME          (def: 5m)                            ║
║  ST_HTF                (def: 1h)                            ║
║  ST_POLL_SECONDS       (def: 60)                            ║
║  ST_MIN_VOLUME_USDT    (def: 50000)                         ║
║  ST_TOP_N_SYMBOLS      (def: 150)                           ║
║  ST_COOLDOWN_MIN       (def: 30)                            ║
║  ST_MAX_DRAWDOWN       (def: 15)                            ║
║  ST_DAILY_LOSS_LIMIT   (def: 8)                             ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, sys, time, logging, csv
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque

import requests
import ccxt
import pandas as pd
import numpy as np

# ══════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ST-BOT] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("supertrend_bot")

# ══════════════════════════════════════════════════════════
# CONFIG — prefijo ST_ para no colisionar con SATY v14
# ══════════════════════════════════════════════════════════
API_KEY    = os.environ.get("ST_BINGX_API_KEY",    "") or os.environ.get("BINGX_API_KEY",    "")
API_SECRET = os.environ.get("ST_BINGX_API_SECRET", "") or os.environ.get("BINGX_API_SECRET", "")

def _tg_token():   return os.environ.get("ST_TELEGRAM_BOT_TOKEN","") or os.environ.get("TELEGRAM_BOT_TOKEN","")
def _tg_chat():    return os.environ.get("ST_TELEGRAM_CHAT_ID",  "") or os.environ.get("TELEGRAM_CHAT_ID",  "")

TF           = os.environ.get("ST_TIMEFRAME",       "5m")
HTF          = os.environ.get("ST_HTF",             "1h")
POLL_SECS    = int(os.environ.get("ST_POLL_SECONDS","60"))
FIXED_USDT   = float(os.environ.get("ST_FIXED_USDT","8"))
MAX_TRADES   = int(os.environ.get("ST_MAX_OPEN_TRADES","8"))
LEVERAGE     = float(os.environ.get("ST_LEVERAGE","10"))
COOLDOWN_MIN = int(os.environ.get("ST_COOLDOWN_MIN","30"))
CB_DD        = float(os.environ.get("ST_MAX_DRAWDOWN","15"))
DAILY_LIMIT  = float(os.environ.get("ST_DAILY_LOSS_LIMIT","8"))
MIN_VOL_USDT = float(os.environ.get("ST_MIN_VOLUME_USDT","50000"))
TOP_N        = int(os.environ.get("ST_TOP_N_SYMBOLS","150"))
MAX_SPREAD   = float(os.environ.get("ST_MAX_SPREAD_PCT","0.8"))
MAX_CONSEC_L = 3

# ── Parámetros Supertrend (3 versiones para consenso) ────
# ST1: rápido (señales tempranas)
ST1_ATR = 7;  ST1_MULT = 2.0
# ST2: medio (confirmación)
ST2_ATR = 10; ST2_MULT = 3.0
# ST3: lento (tendencia de fondo)
ST3_ATR = 14; ST3_MULT = 4.0

# ── Parámetros RSI/EMA ───────────────────────────────────
RSI_LEN     = 14
EMA200_LEN  = 200
EMA50_LEN   = 50
VOL_LEN     = 20
VOL_MULT    = 1.5
ATR_LEN     = 14

# RSI válido para LONG: entre 40 y 65
# RSI válido para SHORT: entre 35 y 60
RSI_LONG_MIN  = 40; RSI_LONG_MAX  = 65
RSI_SHORT_MIN = 35; RSI_SHORT_MAX = 60

# ── TP/SL ────────────────────────────────────────────────
TP1_R = 1.0; TP2_R = 2.0; TP3_R = 3.0  # múltiplos del riesgo (distancia al SL)
CSV_PATH = "supertrend_bot_trades.csv"

HEDGE_MODE = False

# ══════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════
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
    max_profit_pct: float = 0.0
    entry_time:     str   = ""
    contracts:      float = 0.0
    atr_entry:      float = 0.0
    st_score:       int   = 0    # cuántos supertrends alineados (1-3)
    entry_signals:  str   = ""

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
    scan_count:     int   = 0
    signals_found:  int   = 0
    signals_blocked: int  = 0
    btc_bull:       bool  = True
    btc_bear:       bool  = False
    btc_adx:        float = 0.0
    last_discarded: List[dict] = field(default_factory=list)

    def open_count(self):  return len(self.trades)
    def bases_open(self):  return {t.base: t.side for t in self.trades.values()}
    def win_rate(self):
        t = self.wins + self.losses
        return (self.wins / t * 100) if t > 0 else 0.0
    def profit_factor(self):
        return (self.gross_profit / self.gross_loss) if self.gross_loss > 0 else 0.0
    def risk_mult(self):
        return 0.5 if self.consec_losses >= MAX_CONSEC_L else 1.0
    def in_cooldown(self, sym):
        return time.time() - self.cooldowns.get(sym, 0) < COOLDOWN_MIN * 60
    def set_cooldown(self, sym):
        self.cooldowns[sym] = time.time()
    def cb_active(self):
        if self.peak_equity <= 0: return False
        dd = (self.peak_equity - (self.peak_equity + self.total_pnl)) / self.peak_equity * 100
        return dd >= CB_DD
    def daily_limit_hit(self):
        if self.peak_equity <= 0: return False
        return self.daily_pnl < 0 and abs(self.daily_pnl) / self.peak_equity * 100 >= DAILY_LIMIT
    def reset_daily(self):
        if time.time() - self.daily_reset_ts > 86400:
            self.daily_pnl = 0.0; self.daily_reset_ts = time.time()

state = BotState()

# ══════════════════════════════════════════════════════════
# CACHE OHLCV
# ══════════════════════════════════════════════════════════
_cache: Dict[str, Tuple[float, pd.DataFrame]] = {}
CACHE_TTL = 55

def fetch_df(ex, symbol, tf, limit=300):
    key = f"{symbol}|{tf}"
    now = time.time()
    if key in _cache:
        ts, df = _cache[key]
        if now - ts < CACHE_TTL: return df
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
# INDICADORES
# ══════════════════════════════════════════════════════════
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def sma(s, n): return s.rolling(n).mean()

def calc_atr(df, n=ATR_LEN):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()

def calc_rsi(s, n=RSI_LEN):
    d  = s.diff()
    g  = d.clip(lower=0).ewm(span=n, adjust=False).mean()
    lo = (-d.clip(upper=0)).ewm(span=n, adjust=False).mean()
    return 100 - (100 / (1 + g / lo.replace(0, np.nan)))

def calc_adx(df, n=14):
    h, l = df["high"], df["low"]
    up, dn = h.diff(), -l.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    mdm = dn.where((dn > up) & (dn > 0), 0.0)
    atr_s = calc_atr(df, n)
    dip = 100 * pdm.ewm(span=n, adjust=False).mean() / atr_s
    dim = 100 * mdm.ewm(span=n, adjust=False).mean() / atr_s
    dx  = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    return dx.ewm(span=n, adjust=False).mean()

# ══════════════════════════════════════════════════════════
# 🌟 SUPERTREND — corazón de la estrategia
# ══════════════════════════════════════════════════════════
def calc_supertrend(df: pd.DataFrame, atr_period: int, multiplier: float) -> Tuple[pd.Series, pd.Series]:
    """
    Supertrend clásico.
    Retorna:
      - supertrend: serie con la línea del ST (soporte/resistencia dinámica)
      - direction:  1 = alcista (verde), -1 = bajista (rojo)

    Lógica:
      banda_superior = (high+low)/2 + mult × ATR
      banda_inferior = (high+low)/2 - mult × ATR
      Si precio > banda_superior anterior → tendencia alcista (dirección=1)
      Si precio < banda_inferior anterior → tendencia bajista (dirección=-1)
    """
    h, l, c = df["high"], df["low"], df["close"]
    atr     = calc_atr(df, atr_period)
    hl2     = (h + l) / 2.0

    upper_basic = hl2 + multiplier * atr
    lower_basic = hl2 - multiplier * atr

    upper = upper_basic.copy()
    lower = lower_basic.copy()
    st    = pd.Series(np.nan, index=df.index)
    direction = pd.Series(1, index=df.index)

    for i in range(1, len(df)):
        # Banda superior: no sube si precio anterior estaba por debajo
        if upper_basic.iloc[i] < upper.iloc[i-1] or c.iloc[i-1] > upper.iloc[i-1]:
            upper.iloc[i] = upper_basic.iloc[i]
        else:
            upper.iloc[i] = upper.iloc[i-1]

        # Banda inferior: no baja si precio anterior estaba por encima
        if lower_basic.iloc[i] > lower.iloc[i-1] or c.iloc[i-1] < lower.iloc[i-1]:
            lower.iloc[i] = lower_basic.iloc[i]
        else:
            lower.iloc[i] = lower.iloc[i-1]

        # Determinar dirección
        prev_dir = direction.iloc[i-1]
        if prev_dir == -1 and c.iloc[i] > upper.iloc[i-1]:
            direction.iloc[i] = 1
        elif prev_dir == 1 and c.iloc[i] < lower.iloc[i-1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = prev_dir

        # Línea del supertrend
        st.iloc[i] = lower.iloc[i] if direction.iloc[i] == 1 else upper.iloc[i]

    return st, direction

# ══════════════════════════════════════════════════════════
# SEÑAL PRINCIPAL — 3 Supertrends + EMA200 + RSI + Vol
# ══════════════════════════════════════════════════════════
def generate_signal(df: pd.DataFrame, htf_bull: bool, htf_bear: bool) -> Optional[dict]:
    """
    Genera señal de trading basada en consenso de 3 Supertrends.

    Retorna dict con: direction, score, signals, sl_price, atr
    o None si no hay señal válida.
    """
    if len(df) < EMA200_LEN + 10:
        return None

    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    # Calcular los 3 Supertrends
    st1, dir1 = calc_supertrend(df, ST1_ATR, ST1_MULT)
    st2, dir2 = calc_supertrend(df, ST2_ATR, ST2_MULT)
    st3, dir3 = calc_supertrend(df, ST3_ATR, ST3_MULT)

    # EMA200 para tendencia global
    ema200 = ema(c, EMA200_LEN)
    ema50  = ema(c, EMA50_LEN)

    # RSI
    rsi_s  = calc_rsi(c)

    # Volumen
    vol_avg = sma(v.fillna(1).replace(0, 1), VOL_LEN)
    atr_s   = calc_atr(df)

    # Usar barra -2 (última cerrada, anti look-ahead)
    i = -2
    close_v  = float(c.iloc[i])
    high_v   = float(h.iloc[i])
    low_v    = float(l.iloc[i])
    st1_v    = float(st1.iloc[i]); dir1_v = int(dir1.iloc[i])
    st2_v    = float(st2.iloc[i]); dir2_v = int(dir2.iloc[i])
    st3_v    = float(st3.iloc[i]); dir3_v = int(dir3.iloc[i])
    ema200_v = float(ema200.iloc[i])
    ema50_v  = float(ema50.iloc[i])
    rsi_v    = float(rsi_s.iloc[i]) if not pd.isna(rsi_s.iloc[i]) else 50.0
    vol_v    = float(v.iloc[i])
    vol_av   = float(vol_avg.iloc[i])
    atr_v    = float(atr_s.iloc[i])

    if pd.isna(ema200_v) or pd.isna(atr_v): return None

    vol_ok   = vol_v > vol_av * VOL_MULT
    vol_lbl  = f"vol_{vol_v/max(vol_av,1):.1f}x"

    # Contar cuántos Supertrends están alineados
    st_bulls = sum([dir1_v == 1, dir2_v == 1, dir3_v == 1])
    st_bears = sum([dir1_v == -1, dir2_v == -1, dir3_v == -1])

    # ── SEÑAL LONG ────────────────────────────────────────
    long_conds = []
    if close_v > ema200_v:    long_conds.append("precio>EMA200")
    if st_bulls >= 2:         long_conds.append(f"ST_bulls:{st_bulls}/3")
    if RSI_LONG_MIN <= rsi_v <= RSI_LONG_MAX:
                              long_conds.append(f"RSI:{rsi_v:.0f}")
    if vol_ok:                long_conds.append(vol_lbl)
    if htf_bull:              long_conds.append("HTF_bull")
    if ema50_v > ema200_v:    long_conds.append("EMA50>200")

    # Condiciones mínimas para LONG válido
    long_valid = (
        close_v > ema200_v and
        st_bulls >= 2 and
        RSI_LONG_MIN <= rsi_v <= RSI_LONG_MAX and
        vol_ok
    )

    # ── SEÑAL SHORT ───────────────────────────────────────
    short_conds = []
    if close_v < ema200_v:    short_conds.append("precio<EMA200")
    if st_bears >= 2:         short_conds.append(f"ST_bears:{st_bears}/3")
    if RSI_SHORT_MIN <= rsi_v <= RSI_SHORT_MAX:
                              short_conds.append(f"RSI:{rsi_v:.0f}")
    if vol_ok:                short_conds.append(vol_lbl)
    if htf_bear:              short_conds.append("HTF_bear")
    if ema50_v < ema200_v:    short_conds.append("EMA50<200")

    short_valid = (
        close_v < ema200_v and
        st_bears >= 2 and
        RSI_SHORT_MIN <= rsi_v <= RSI_SHORT_MAX and
        vol_ok
    )

    # ── Elegir dirección ──────────────────────────────────
    if long_valid and short_valid:
        return None   # contradicción, no operar

    if not long_valid and not short_valid:
        return None

    direction = "long" if long_valid else "short"
    signals   = long_conds if long_valid else short_conds
    score     = len(signals)

    # ── Calcular SL en la línea del Supertrend más cercano ──
    # Para LONG: SL = el Supertrend más alto (más cerca del precio) de los activos
    # Para SHORT: SL = el Supertrend más bajo (más cerca del precio)
    if direction == "long":
        active_sts = [v for v, d in [(st1_v, dir1_v),(st2_v, dir2_v),(st3_v, dir3_v)] if d == 1]
        sl_st = max(active_sts) if active_sts else close_v - atr_v * 1.5
        sl_price = min(sl_st, close_v - atr_v * 0.5)  # al menos 0.5×ATR de margen
    else:
        active_sts = [v for v, d in [(st1_v, dir1_v),(st2_v, dir2_v),(st3_v, dir3_v)] if d == -1]
        sl_st = min(active_sts) if active_sts else close_v + atr_v * 1.5
        sl_price = max(sl_st, close_v + atr_v * 0.5)

    return {
        "direction":  direction,
        "score":      score,
        "signals":    signals,
        "sl_price":   sl_price,
        "atr":        atr_v,
        "rsi":        rsi_v,
        "st_bulls":   st_bulls,
        "st_bears":   st_bears,
        "close":      close_v,
        "ema200":     ema200_v,
    }

# ══════════════════════════════════════════════════════════
# HTF BIAS (tendencia en timeframe mayor)
# ══════════════════════════════════════════════════════════
def htf_bias(df_htf: pd.DataFrame) -> Tuple[bool, bool]:
    """
    Usa 1 Supertrend en HTF como filtro de tendencia mayor.
    Mucho más limpio que cruzar EMAs.
    """
    if len(df_htf) < 50: return True, False
    _, direction = calc_supertrend(df_htf, ST2_ATR, ST2_MULT)
    dir_val = int(direction.iloc[-2])
    return (dir_val == 1), (dir_val == -1)

# ══════════════════════════════════════════════════════════
# BTC MACRO BIAS
# ══════════════════════════════════════════════════════════
def update_btc_bias(ex):
    try:
        df = fetch_df(ex, "BTC/USDT:USDT", "1h", limit=250)
        _, direction = calc_supertrend(df, ST2_ATR, ST2_MULT)
        adx = calc_adx(df)
        state.btc_bull = int(direction.iloc[-2]) == 1
        state.btc_bear = int(direction.iloc[-2]) == -1
        state.btc_adx  = float(adx.iloc[-2])
        status = "BULL" if state.btc_bull else "BEAR" if state.btc_bear else "NEUTRAL"
        log.info(f"BTC: {status} ADX:{state.btc_adx:.1f}")
    except Exception as e:
        log.warning(f"BTC bias: {e}")

# ══════════════════════════════════════════════════════════
# SCAN SÍMBOLO
# ══════════════════════════════════════════════════════════
def scan_symbol(ex, symbol) -> Optional[dict]:
    try:
        df     = fetch_df(ex, symbol, TF,  300)
        df_htf = fetch_df(ex, symbol, HTF, 150)
        if len(df) < EMA200_LEN + 20 or len(df_htf) < 50: return None

        htf_bull, htf_bear = htf_bias(df_htf)
        signal = generate_signal(df, htf_bull, htf_bear)
        if signal is None: return None

        # Filtro BTC: no ir en contra de BTC fuerte
        direction = signal["direction"]
        if state.btc_bear and direction == "long"  and state.btc_adx > 25: return None
        if state.btc_bull and direction == "short" and state.btc_adx > 25: return None

        return {
            "symbol":    symbol,
            "base":      symbol.split("/")[0],
            "direction": direction,
            "score":     signal["score"],
            "signals":   "; ".join(signal["signals"]),
            "sl_price":  signal["sl_price"],
            "atr":       signal["atr"],
            "rsi":       signal["rsi"],
            "st_bulls":  signal["st_bulls"],
            "st_bears":  signal["st_bears"],
            "close":     signal["close"],
        }
    except Exception as e:
        log.debug(f"[{symbol}] scan: {e}")
        return None

# ══════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════
def tg(msg: str):
    token = _tg_token(); chat = _tg_chat()
    if not token or not chat: return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
        if not r.ok: log.warning(f"TG {r.status_code}")
    except Exception as e: log.warning(f"TG: {e}")

def tg_startup(balance: float, n: int):
    btc_icon = "🟢" if state.btc_bull else "🔴" if state.btc_bear else "⚪"
    tg(
        f"🤖 <b>SUPERTREND BOT v1 — ONLINE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔬 <b>ESTRATEGIA COMPARATIVA vs SATY v14</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: <b>${balance:.2f} USDT</b>\n"
        f"📊 Universo: <b>{n} pares</b>\n"
        f"⏱ TF: {TF} | HTF: {HTF}\n"
        f"💵 ${FIXED_USDT:.0f}×{int(LEVERAGE)}× | Max {MAX_TRADES} trades\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌟 Estrategia 3×Supertrend:\n"
        f"  ST1: ATR{ST1_ATR} × {ST1_MULT} (rápido)\n"
        f"  ST2: ATR{ST2_ATR} × {ST2_MULT} (medio)\n"
        f"  ST3: ATR{ST3_ATR} × {ST3_MULT} (lento)\n"
        f"  + EMA200 + RSI({RSI_LEN}) + Vol{VOL_MULT}×\n"
        f"  + HTF Supertrend filter\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{btc_icon} BTC: {'ALCISTA' if state.btc_bull else 'BAJISTA' if state.btc_bear else 'NEUTRO'}"
        f" ADX:{state.btc_adx:.0f}\n"
        f"⏰ {utcnow()}"
    )

def tg_signal(t: TradeState):
    emoji  = "🟢" if t.side == "long" else "🔴"
    accion = "LONG ▲" if t.side == "long" else "SHORT ▼"
    sl_d   = abs(t.sl_price - t.entry_price)
    rr     = abs(t.tp3_price - t.entry_price) / max(sl_d, 1e-9)
    def pct(p): return abs(p - t.entry_price) / t.entry_price * 100
    tg(
        f"{emoji} <b>{accion} — {t.symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌟 ST alineados: <b>{t.st_score}/3</b>\n"
        f"📋 Señales: <code>{t.entry_signals[:100]}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Entrada: <code>{t.entry_price:.6g}</code>\n"
        f"🟡 TP1 (25%): <code>{t.tp1_price:.6g}</code>  +{pct(t.tp1_price):.2f}%\n"
        f"🟠 TP2 (25%): <code>{t.tp2_price:.6g}</code>  +{pct(t.tp2_price):.2f}%\n"
        f"🟢 TP3 (50%): <code>{t.tp3_price:.6g}</code>  +{pct(t.tp3_price):.2f}%  R:R 1:{rr:.1f}\n"
        f"🛑 SL: <code>{t.sl_price:.6g}</code>  -{pct(t.sl_price):.2f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 {state.open_count()}/{MAX_TRADES} | ⏰ {utcnow()}"
    )

def tg_close(reason: str, t: TradeState, exit_p: float, pnl: float):
    win = pnl > 0; emoji = "✅" if win else "❌"
    pct = (pnl / (t.entry_price * t.contracts) * 100) if t.contracts > 0 else 0
    tg(
        f"{emoji} <b>CERRADO — {t.symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 {t.side.upper()} ST{t.st_score}/3 | {reason}\n"
        f"🚪 {t.entry_price:.6g} → <code>{exit_p:.6g}</code>\n"
        f"{'📈' if win else '📉'} <b>{pct:+.2f}%  ${pnl:+.2f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {state.wins}W/{state.losses}L | WR:{state.win_rate():.1f}% | PF:{state.profit_factor():.2f}\n"
        f"💹 Hoy: ${state.daily_pnl:+.2f} | Total: ${state.total_pnl:+.2f}\n"
        f"⏰ {utcnow()}"
    )

def tg_heartbeat(balance: float):
    open_lines = "\n".join(
        f"  {'🟢' if ts.side=='long' else '🔴'} {sym} @ {ts.entry_price:.5g}"
        f" ST{ts.st_score}/3 {'🛡' if ts.sl_moved_be else ''}+{ts.max_profit_pct:.1f}%"
        for sym, ts in state.trades.items()
    ) or "  (ninguna)"
    tg(
        f"💓 <b>HEARTBEAT — ST BOT v1</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: <b>${balance:.2f} USDT</b>\n"
        f"📅 Hoy: <b>${state.daily_pnl:+.2f}</b> | Total: <b>${state.total_pnl:+.2f}</b>\n"
        f"📊 {state.wins}W/{state.losses}L | WR:{state.win_rate():.1f}% | PF:{state.profit_factor():.2f}\n"
        f"📦 Posiciones ({state.open_count()}/{MAX_TRADES}):\n{open_lines}\n"
        f"{'🟢' if state.btc_bull else '🔴' if state.btc_bear else '⚪'} BTC ADX:{state.btc_adx:.0f}\n"
        f"⏰ {utcnow()}"
    )

def tg_error(msg: str):
    tg(f"🔥 <b>ST BOT ERROR</b>\n<code>{msg[:400]}</code>\n⏰ {utcnow()}")

def tg_circuit_breaker(dd: float):
    tg(f"🚨 <b>ST BOT — CIRCUIT BREAKER</b> DD:{dd:.2f}%\n⛔ Sin nuevas posiciones\n⏰ {utcnow()}")

def tg_daily_limit():
    tg(f"🚨 <b>ST BOT — LÍMITE DIARIO</b> ${state.daily_pnl:+.2f}\n⛔ Sin trades hasta mañana UTC\n⏰ {utcnow()}")

# ══════════════════════════════════════════════════════════
# CSV LOG
# ══════════════════════════════════════════════════════════
def log_csv(action, t: TradeState, price, pnl=0.0):
    try:
        exists = os.path.exists(CSV_PATH)
        with open(CSV_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["ts","action","symbol","side","st_score","entry","exit","pnl","contracts"])
            w.writerow([utcnow(), action, t.symbol, t.side,
                        t.st_score, t.entry_price, price, round(pnl,4), t.contracts])
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
    except Exception: pass
    return False

def get_balance(ex):  return float(ex.fetch_balance()["USDT"]["free"])
def get_last_price(ex, symbol): return float(ex.fetch_ticker(symbol)["last"])
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
        if mkt.get("swap") and mkt.get("quote") == "USDT" and mkt.get("active", True)
    ]
    if not candidates: return []
    try: tickers = ex.fetch_tickers(candidates)
    except Exception as e:
        log.warning(f"fetch_tickers: {e}")
        return candidates[:TOP_N]
    ranked = [(s, float(tickers.get(s,{}).get("quoteVolume",0) or 0)) for s in candidates]
    ranked = [(s, v) for s, v in ranked if v >= MIN_VOL_USDT]
    ranked.sort(key=lambda x: -x[1])
    result = [s for s, _ in ranked[:TOP_N]]
    log.info(f"Universo: {len(result)} pares")
    return result

# ══════════════════════════════════════════════════════════
# APERTURA / CIERRE
# ══════════════════════════════════════════════════════════
def open_trade(ex, symbol, base, direction, score, signals, sl_price_raw, atr_v, st_score):
    try:
        spread = get_spread_pct(ex, symbol)
        if spread > MAX_SPREAD:
            log.warning(f"[{symbol}] spread {spread:.3f}% — skip")
            return None

        side  = "buy" if direction == "long" else "sell"
        price = get_last_price(ex, symbol)
        usdt  = FIXED_USDT * state.risk_mult()
        mkt   = ex.markets.get(symbol, {})
        cs    = float(mkt.get("contractSize") or mkt.get("info",{}).get("contractSize") or 1.0)
        notional = usdt * LEVERAGE
        amount   = float(ex.amount_to_precision(symbol, notional / (price * cs)))
        min_amt  = float((mkt.get("limits",{}).get("amount",{}) or {}).get("min", 0) or 0)

        if amount <= 0 or amount < min_amt or amount * price * cs < 3:
            log.warning(f"[{symbol}] amount inválido: {amount}")
            return None

        try:
            if HEDGE_MODE:
                ex.set_leverage(int(LEVERAGE), symbol, params={"positionSide": "LONG"})
                ex.set_leverage(int(LEVERAGE), symbol, params={"positionSide": "SHORT"})
            else:
                ex.set_leverage(int(LEVERAGE), symbol)
        except Exception as e: log.warning(f"[{symbol}] leverage: {e}")

        order       = ex.create_order(symbol, "market", side, amount, params=entry_params(side))
        entry_price = float(order.get("average") or price)
        trade_side  = "long" if side == "buy" else "short"

        # SL ya calculado por generate_signal (en la línea del Supertrend)
        sl_p = float(ex.price_to_precision(symbol, sl_price_raw))

        # TP basado en R:R (distancia al SL × ratio)
        risk = abs(entry_price - sl_p)
        if trade_side == "long":
            tp1_p = entry_price + risk * TP1_R
            tp2_p = entry_price + risk * TP2_R
            tp3_p = entry_price + risk * TP3_R
        else:
            tp1_p = entry_price - risk * TP1_R
            tp2_p = entry_price - risk * TP2_R
            tp3_p = entry_price - risk * TP3_R

        tp1_p = float(ex.price_to_precision(symbol, tp1_p))
        tp2_p = float(ex.price_to_precision(symbol, tp2_p))
        tp3_p = float(ex.price_to_precision(symbol, tp3_p))

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
            sl_price=sl_p, entry_time=utcnow(), contracts=amount,
            atr_entry=atr_v, st_score=st_score,
            entry_signals=signals[:120],
        )
        t.trail_high = t.trail_low = t.peak_price = entry_price
        log_csv("OPEN", t, entry_price)
        tg_signal(t)
        log.info(f"[OPEN] {symbol} {trade_side.upper()} ST{st_score}/3 SL:{sl_p:.6g}")
        return t
    except Exception as e:
        log.error(f"[{symbol}] open_trade: {e}")
        tg_error(f"open_trade {symbol}: {e}")
        return None

def move_sl_to(ex, symbol, new_sl):
    if symbol not in state.trades: return
    t = state.trades[symbol]
    try: ex.cancel_all_orders(symbol)
    except Exception: pass
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
    except Exception: pass
    pos = get_position(ex, symbol)
    pnl = 0.0
    if pos:
        contracts  = abs(float(pos.get("contracts", 0)))
        close_side = "sell" if t.side == "long" else "buy"
        try:
            ex.create_order(symbol, "market", close_side, contracts, params=exit_params(t.side))
            pnl = ((price - t.entry_price) if t.side=="long" else (t.entry_price - price)) * contracts
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
def manage_trade(ex, symbol, live_price, live_pos):
    if symbol not in state.trades: return
    t = state.trades[symbol]

    # Posición cerrada externamente (TP/SL ejecutado)
    if live_pos is None:
        pnl = ((live_price - t.entry_price) if t.side=="long"
               else (t.entry_price - live_price)) * t.contracts
        reason = "TP3" if ((t.side=="long" and live_price >= t.tp3_price) or
                            (t.side=="short" and live_price <= t.tp3_price)) else "SL"
        if pnl > 0: state.wins += 1; state.gross_profit += pnl; state.consec_losses = 0
        else:        state.losses += 1; state.gross_loss += abs(pnl); state.consec_losses += 1
        state.total_pnl += pnl; state.daily_pnl += pnl
        state.set_cooldown(symbol)
        log_csv("CLOSE_EXT", t, live_price, pnl)
        tg_close(reason, t, live_price, pnl)
        del state.trades[symbol]; return

    # TP1 → mover SL a break-even
    if not t.tp1_hit:
        tp1_hit = ((t.side=="long"  and live_price >= t.tp1_price) or
                   (t.side=="short" and live_price <= t.tp1_price))
        if tp1_hit:
            t.tp1_hit = True; t.sl_moved_be = True
            pnl_est = abs(t.tp1_price - t.entry_price) * float(live_pos.get("contracts",0)) * 0.25
            move_sl_to(ex, symbol, t.entry_price)
            tg(f"🟡 <b>TP1 + BE</b> — {t.symbol}\n"
               f"💰 +${pnl_est:.2f} | SL→entrada\n"
               f"🎯 TP2: <code>{t.tp2_price:.6g}</code>\n⏰ {utcnow()}")

    # TP2 → mover SL a TP1
    if t.tp1_hit and not t.tp2_hit:
        tp2_hit = ((t.side=="long"  and live_price >= t.tp2_price) or
                   (t.side=="short" and live_price <= t.tp2_price))
        if tp2_hit:
            t.tp2_hit = True
            pnl_est = abs(t.tp2_price - t.entry_price) * float(live_pos.get("contracts",0)) * 0.25
            move_sl_to(ex, symbol, t.tp1_price)
            tg(f"🟠 <b>TP2</b> — {t.symbol}\n"
               f"💰 +${pnl_est:.2f} | SL→TP1\n"
               f"🎯 TP3: <code>{t.tp3_price:.6g}</code>\n⏰ {utcnow()}")

    # Trailing stop después de TP1
    if t.tp1_hit and symbol in state.trades:
        atr_t   = t.atr_entry
        cur_pct = ((live_price - t.entry_price)/t.entry_price*100 if t.side=="long"
                   else (t.entry_price - live_price)/t.entry_price*100)
        t.max_profit_pct = max(t.max_profit_pct, cur_pct)

        new_peak = live_price > t.peak_price if t.side=="long" else live_price < t.peak_price
        if new_peak: t.peak_price = live_price; t.stall_count = 0
        else:        t.stall_count += 1

        trail_m = 0.4 if t.stall_count >= 3 else 0.8
        if t.side == "long":
            t.trail_high = max(t.trail_high, live_price)
            if live_price <= t.trail_high - atr_t * trail_m:
                close_trade(ex, symbol, f"TRAILING", live_price); return
        else:
            t.trail_low = min(t.trail_low, live_price)
            if live_price >= t.trail_low + atr_t * trail_m:
                close_trade(ex, symbol, f"TRAILING", live_price); return

    # SL dinámico antes de TP1 (0.8×ATR)
    if not t.tp1_hit and symbol in state.trades:
        loss_dist = (t.entry_price - live_price if t.side=="long"
                     else live_price - t.entry_price)
        if loss_dist >= t.atr_entry * 0.8:
            close_trade(ex, symbol, "PÉRDIDA DINÁMICA", live_price)

# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════
def main():
    global HEDGE_MODE
    log.info("=" * 60)
    log.info("  SUPERTREND BOT v1 — 3×ST + EMA200 + RSI + Vol")
    log.info("  Estrategia comparativa vs SATY ELITE v14")
    log.info("=" * 60)

    if not (API_KEY and API_SECRET):
        log.error("ST_BINGX_API_KEY y ST_BINGX_API_SECRET no configuradas")
        tg_error("Supertrend Bot no iniciado: faltan API Keys")
        sys.exit(1)

    ex = None
    for attempt in range(10):
        try: ex = build_exchange(); log.info("BingX conectado ✓"); break
        except Exception as e:
            wait = min(2**attempt, 120)
            log.warning(f"Conexión {attempt+1}/10: {e} → retry {wait}s")
            time.sleep(wait)
    if ex is None:
        tg_error("Sin conexión BingX"); raise RuntimeError("Sin conexión")

    HEDGE_MODE = detect_hedge_mode(ex)
    log.info(f"Modo: {'HEDGE' if HEDGE_MODE else 'ONE-WAY'}")

    balance = 0.0
    for _ in range(10):
        try: balance = get_balance(ex); break
        except: time.sleep(5)

    state.peak_equity  = balance
    state.daily_reset_ts = time.time()
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
    HEARTBEAT_S   = 3600
    SUMMARY_EVERY = 20
    prev_cb = False; prev_dl = False

    while True:
        ts_start = time.time()
        try:
            scan_count += 1; state.scan_count = scan_count
            state.reset_daily()
            clear_cache()

            log.info(f"SCAN #{scan_count} | {datetime.now(timezone.utc):%H:%M:%S} "
                     f"| {state.open_count()}/{MAX_TRADES} | "
                     f"{state.wins}W/{state.losses}L | "
                     f"BTC:{'🟢' if state.btc_bull else '🔴' if state.btc_bear else '⚪'}")

            if scan_count % REFRESH_EVERY == 0:
                try: ex.load_markets(); symbols = get_symbols(ex)
                except Exception as e: log.warning(f"Refresh: {e}")

            if scan_count % BTC_REFRESH == 0:
                update_btc_bias(ex)

            if time.time() - state.last_heartbeat > HEARTBEAT_S:
                try:
                    tg_heartbeat(get_balance(ex))
                    state.last_heartbeat = time.time()
                except Exception: pass

            # Circuit breaker
            cb_now = state.cb_active()
            if cb_now and not prev_cb:
                dd = (state.peak_equity - (state.peak_equity + state.total_pnl)) / state.peak_equity * 100
                tg_circuit_breaker(dd)
            prev_cb = cb_now
            if cb_now: time.sleep(POLL_SECS); continue

            # Límite diario
            dl_now = state.daily_limit_hit()
            if dl_now and not prev_dl: tg_daily_limit()
            prev_dl = dl_now
            if dl_now: time.sleep(POLL_SECS); continue

            # Gestionar posiciones abiertas
            live_positions = get_all_positions(ex)
            for sym in list(state.trades.keys()):
                try:
                    lp   = live_positions.get(sym)
                    lp_  = float(lp["markPrice"]) if lp else get_last_price(ex, sym)
                    manage_trade(ex, sym, lp_, lp)
                except Exception as e: log.warning(f"[{sym}] manage: {e}")

            # Buscar nuevas señales
            new_signals = []; state.last_discarded = []

            if state.open_count() < MAX_TRADES:
                bases_open = state.bases_open()
                to_scan    = [
                    s for s in symbols
                    if s not in state.trades
                    and not state.in_cooldown(s)
                    and s.split("/")[0] not in bases_open
                ]
                log.info(f"Escaneando {len(to_scan)} pares (3×Supertrend)...")

                with ThreadPoolExecutor(max_workers=8) as pool:
                    futures = {pool.submit(scan_symbol, ex, s): s for s in to_scan}
                    results = [f.result() for f in as_completed(futures) if f.result()]

                # Ordenar por ST score (más alineados primero) y luego por RSI en zona ideal
                results.sort(key=lambda x: (x["st_bulls"] + x["st_bears"], x["score"]), reverse=True)

                for res in results:
                    if state.open_count() >= MAX_TRADES: break
                    sym = res["symbol"]; base = res["base"]
                    if sym in state.trades or base in state.bases_open(): continue
                    if state.in_cooldown(sym): continue

                    st_score = res["st_bulls"] if res["direction"] == "long" else res["st_bears"]

                    t = open_trade(
                        ex, sym, base,
                        res["direction"],
                        res["score"],
                        res["signals"],
                        res["sl_price"],
                        res["atr"],
                        st_score,
                    )
                    if t:
                        state.trades[sym] = t
                        new_signals.append(res)
                        state.signals_found += 1

            # Resumen periódico
            if scan_count % SUMMARY_EVERY == 0:
                top = "\n".join(
                    f"  {'🟢' if s['direction']=='long' else '🔴'} {s['symbol']} "
                    f"ST:{s['st_bulls'] if s['direction']=='long' else s['st_bears']}/3 "
                    f"RSI:{s['rsi']:.0f}"
                    for s in new_signals[:5]
                ) or "  (ninguna)"
                tg(
                    f"📡 <b>ST BOT SCAN #{scan_count}</b>\n"
                    f"🔍 Escaneados: {len(to_scan) if 'to_scan' in dir() else 0} | Entradas: {len(new_signals)}\n"
                    f"📶 Señales:\n{top}\n"
                    f"📊 {state.wins}W/{state.losses}L | Total: ${state.total_pnl:+.2f}\n"
                    f"⏰ {utcnow()}"
                )

            elapsed = time.time() - ts_start
            log.info(f"Ciclo {elapsed:.1f}s | hoy:${state.daily_pnl:+.2f} | total:${state.total_pnl:+.2f}")

        except ccxt.NetworkError as e:
            log.warning(f"Network: {e} — 15s"); time.sleep(15)
        except ccxt.ExchangeError as e:
            log.error(f"Exchange: {e}"); tg_error(f"Exchange: {str(e)[:200]}")
        except KeyboardInterrupt:
            tg("🛑 <b>Supertrend Bot detenido.</b>"); break
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
            try: tg_error(f"ST BOT CRASH 30s: {str(e)[:200]}")
            except Exception: pass
            time.sleep(30)
