"""
╔══════════════════════════════════════════════════════════════════╗
║         SATY ELITE v9 - MULTI-SYMBOL CONFLUENCE ENGINE          ║
║   Python Bot para BingX Futures + Telegram                       ║
║                                                                  ║
║  FIXES v9:                                                       ║
║  - FIX #1: Soporte modo Hedge (positionSide LONG/SHORT)          ║
║  - FIX #2: Validacion de monto minimo por par antes de operar    ║
║  - FIX #3: BTC dominance como filtro macro adicional             ║
║  - FIX #4: Modo 24/7 sin filtro de sesion (USE_SESSION=False)    ║
║  - FIX #5: Mas indicadores (MACD, Stoch RSI, EMA cross HTF)      ║
║  - FIX #6: MIN_SCORE reducido a 4 por defecto                    ║
║  - FIX #7: Deteccion automatica modo Hedge vs One-Way            ║
╚══════════════════════════════════════════════════════════════════╝

Instalacion:
    pip install ccxt pandas numpy requests

Variables de entorno:
    BINGX_API_KEY        API Key de BingX
    BINGX_API_SECRET     API Secret de BingX
    TELEGRAM_BOT_TOKEN   Token del bot Telegram
    TELEGRAM_CHAT_ID     Chat ID Telegram

Opcionales:
    SYMBOL_FILTER        Bases a escanear ej: "BTC,ETH,SOL" (vacio=todas)
    BLACKLIST            Pares excluidos ej: "LUNA/USDT:USDT"
    MAX_OPEN_TRADES      Max posiciones simultaneas (default: 3)
    MIN_SCORE            Score minimo 1-10 (default: 4)
    BASE_RISK            % capital por operacion (default: 2.0)
    MAX_DRAWDOWN         % drawdown maximo circuit breaker (default: 15.0)
    MIN_VOLUME_USDT      Volumen minimo 24h USDT (default: 3000000)
    POLL_SECONDS         Segundos entre ciclos (default: 60)
    TIMEFRAME            TF entrada (default: 5m)
    HTF1                 Primer HTF (default: 15m)
    HTF2                 Macro TF (default: 1h)
    TOP_N_SYMBOLS        Max pares a escanear (0=todos, default: 60)
    USE_SESSION          Filtro sesion London/NY (default: false = 24/7)
    BTC_FILTER           Filtrar segun tendencia BTC (default: true)
"""

import os
import time
import logging
import requests
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

# ══════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("saty_v9")

# ══════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════
API_KEY    = os.environ.get("BINGX_API_KEY",    "")
API_SECRET = os.environ.get("BINGX_API_SECRET", "")
TF         = os.environ.get("TIMEFRAME",        "5m")
HTF1       = os.environ.get("HTF1",             "15m")
HTF2       = os.environ.get("HTF2",             "1h")
POLL_SECS  = int(os.environ.get("POLL_SECONDS", "60"))
TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID",   "")

_sym_filter   = os.environ.get("SYMBOL_FILTER", "")
SYMBOL_FILTER : List[str] = [s.strip().upper() for s in _sym_filter.split(",") if s.strip()]
_blacklist     = os.environ.get("BLACKLIST", "")
BLACKLIST      : List[str] = [s.strip() for s in _blacklist.split(",") if s.strip()]

MIN_VOLUME_USDT = float(os.environ.get("MIN_VOLUME_USDT", "3000000"))
TOP_N_SYMBOLS   = int(os.environ.get("TOP_N_SYMBOLS",   "60"))
MAX_OPEN_TRADES = int(os.environ.get("MAX_OPEN_TRADES", "3"))
MIN_SCORE       = int(os.environ.get("MIN_SCORE",       "4"))   # reducido a 4
BASE_RISK       = float(os.environ.get("BASE_RISK",     "2.0"))
CB_DD           = float(os.environ.get("MAX_DRAWDOWN",  "15.0"))

# 24/7 por defecto (sin filtro de sesion)
USE_SESSION  = os.environ.get("USE_SESSION",  "false").lower() == "true"
BTC_FILTER   = os.environ.get("BTC_FILTER",  "true").lower()  == "true"

# Sesion (solo si USE_SESSION=true)
LONDON_OPEN  = 7;  LONDON_CLOSE = 16
NY_OPEN      = 13; NY_CLOSE     = 21

# Indicadores
FAST_LEN  = 8;   PIVOT_LEN = 21; BIAS_LEN  = 48
SLOW_LEN  = 200; ADX_LEN   = 14; ADX_MIN   = 18
RSI_LEN   = 14;  ATR_LEN   = 14; VOL_LEN   = 20
OSC_LEN   = 3;   SWING_LB  = 10; MACD_FAST = 12
MACD_SLOW = 26;  MACD_SIG  = 9;  STOCH_LEN = 14

# Exits
TP1_MULT   = 1.2
TP2_MULT   = 3.0
SL_ATR     = 1.0
TRAIL_MULT = 1.0

# Risk
RISK_BOOST    = 1.5
RISK_CUT      = 0.5
MAX_CONSEC_LOSS = 3
USE_CB        = True

# Hedge mode flag (auto-detectado al iniciar)
HEDGE_MODE: bool = False


# ══════════════════════════════════════════════════════════
# ESTADO
# ══════════════════════════════════════════════════════════
@dataclass
class TradeState:
    symbol:      str   = ""
    side:        str   = ""   # "long" | "short"
    entry_price: float = 0.0
    tp1_price:   float = 0.0
    tp2_price:   float = 0.0
    sl_price:    float = 0.0
    tp1_hit:     bool  = False
    trail_high:  float = 0.0
    trail_low:   float = 0.0
    entry_score: int   = 0
    entry_time:  str   = ""
    contracts:   float = 0.0


@dataclass
class BotState:
    wins:          int   = 0
    losses:        int   = 0
    gross_profit:  float = 0.0
    gross_loss:    float = 0.0
    consec_losses: int   = 0
    peak_equity:   float = 0.0
    total_pnl:     float = 0.0
    trades: Dict[str, TradeState] = field(default_factory=dict)

    # BTC bias global
    btc_bull: bool = True
    btc_bear: bool = False

    def open_count(self)   -> int:   return len(self.trades)
    def win_rate(self)     -> float:
        t = self.wins + self.losses; return (self.wins / t * 100) if t else 0.0
    def profit_factor(self)-> float:
        return (self.gross_profit / self.gross_loss) if self.gross_loss else 0.0
    def score_bar(self, score: int, mx: int = 11) -> str:
        filled = min(score, mx)
        return "█" * filled + "░" * (mx - filled)
    def cb_active(self) -> bool:
        if not USE_CB or self.peak_equity <= 0: return False
        dd = (self.peak_equity - (self.peak_equity + self.total_pnl)) / self.peak_equity * 100
        return dd >= CB_DD
    def risk_mult(self, score: int) -> float:
        if self.consec_losses >= MAX_CONSEC_LOSS: return RISK_CUT
        if score >= 8: return RISK_BOOST
        return 1.0


state = BotState()


# ══════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════
def tg(msg: str):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        log.warning(f"Telegram error: {e}")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def tg_startup(balance: float, n_symbols: int):
    mode_str   = "HEDGE" if HEDGE_MODE else "ONE-WAY"
    filter_str = ", ".join(SYMBOL_FILTER) if SYMBOL_FILTER else "TODOS"
    tg(
        f"<b>🚀 SATY ELITE v9 MULTI - INICIADO</b>\n"
        f"══════════════════════════════\n"
        f"🌍 <b>Universo:</b> {n_symbols} pares USDT\n"
        f"🔍 <b>Filtro:</b> {filter_str}\n"
        f"⚙️ <b>Modo cuenta:</b> {mode_str}\n"
        f"⏱ <b>TF:</b> {TF} | HTF1:{HTF1} | HTF2:{HTF2}\n"
        f"🎯 <b>Score min:</b> {MIN_SCORE}/11 | <b>Max trades:</b> {MAX_OPEN_TRADES}\n"
        f"💰 <b>Balance:</b> ${balance:.2f} USDT\n"
        f"⚙️ <b>Risk/trade:</b> {BASE_RISK}% | CB: -{CB_DD}%\n"
        f"🕐 <b>Sesion:</b> {'London+NY' if USE_SESSION else '24/7 ACTIVO'}\n"
        f"₿  <b>Filtro BTC:</b> {'ACTIVO' if BTC_FILTER else 'DESACTIVADO'}\n"
        f"⏰ {utcnow()}"
    )


def tg_signal(t: TradeState, risk_pct: float, row: pd.Series, extra: str = ""):
    emoji = "🟢" if t.side == "long" else "🔴"
    label = "LONG" if t.side == "long" else "SHORT"
    sl_dist = abs(t.sl_price - t.entry_price)
    rr1 = abs(t.tp1_price - t.entry_price) / max(sl_dist, 1e-9)
    rr2 = abs(t.tp2_price - t.entry_price) / max(sl_dist, 1e-9)
    btc_str = "₿ BULL" if state.btc_bull else ("₿ BEAR" if state.btc_bear else "₿ NEUTRAL")
    rsi_lbl = rsi_zone_label(float(row["rsi"]))
    tg(
        f"{emoji} <b>{label} ABIERTO</b> — {t.symbol}\n"
        f"══════════════════════════════\n"
        f"🎯 <b>Score:</b> {t.entry_score}/11 {state.score_bar(t.entry_score)}\n"
        f"💵 <b>Entrada:</b> <code>{t.entry_price:.6g}</code>\n"
        f"🟡 <b>TP1 (50%):</b> <code>{t.tp1_price:.6g}</code>  R:R 1:{rr1:.1f}\n"
        f"🟢 <b>TP2 (50%):</b> <code>{t.tp2_price:.6g}</code>  R:R 1:{rr2:.1f}\n"
        f"🛑 <b>SL:</b> <code>{t.sl_price:.6g}</code>\n"
        f"══════════════════════════════\n"
        f"📊 ADX:{row['adx']:.1f} | {rsi_lbl}\n"
        f"📊 Stoch:{row['stoch_k']:.0f}/{row['stoch_d']:.0f} | "
        f"MACD:{row['macd_hist']:.4f} | Vol:{row['volume']/row['vol_ma']:.2f}x\n"
        f"{btc_str} | {extra}\n"
        f"💼 Risk:{risk_pct:.1f}% | Trades:{state.open_count()}/{MAX_OPEN_TRADES}\n"
        f"⏰ {utcnow()}"
    )


def tg_tp1(t: TradeState, price: float, pnl: float):
    tg(
        f"🟡 <b>TP1 TOCADO</b> — {t.symbol}\n"
        f"💵 Precio: <code>{price:.6g}</code>\n"
        f"💰 PnL parcial est.: ${pnl:+.2f}\n"
        f"🔄 Trailing activo en 50% restante\n"
        f"⏰ {utcnow()}"
    )


def tg_close(reason: str, t: TradeState, exit_p: float, pnl: float):
    emoji = "✅" if pnl > 0 else "❌"
    tg(
        f"{emoji} <b>CERRADO</b> — {t.symbol}\n"
        f"📋 {t.side.upper()} | Score:{t.entry_score}/11 | {reason}\n"
        f"💵 Entrada:<code>{t.entry_price:.6g}</code> → Salida:<code>{exit_p:.6g}</code>\n"
        f"{'💰' if pnl > 0 else '💸'} PnL: ${pnl:+.2f}\n"
        f"📊 {state.wins}W/{state.losses}L | "
        f"WR:{state.win_rate():.1f}% | PF:{state.profit_factor():.2f} | "
        f"Total:${state.total_pnl:+.2f}\n"
        f"⏰ {utcnow()}"
    )


def tg_scan_summary(signals: List[dict], scanned: int):
    cb_str   = f"⛔ ACTIVO" if state.cb_active() else "✅ OK"
    btc_str  = "₿ BULL" if state.btc_bull else ("₿ BEAR" if state.btc_bear else "₿ NEUTRAL")
    open_str = "\n".join(
        f"  • {sym} {ts.side.upper()} {ts.entry_score}/11"
        for sym, ts in state.trades.items()
    ) or "  (ninguna)"
    top5 = "\n".join(
        f"  {'🟢' if s['side']=='long' else '🔴'} {s['symbol']} {s['score']}/11"
        for s in signals[:5]
    ) or "  (ninguna)"
    tg(
        f"📡 <b>SCAN #{scanned}</b> — {utcnow()}\n"
        f"══════════════════════════════\n"
        f"🔍 Escaneados: {scanned} | Señales: {len(signals)}\n"
        f"🏆 Top señales:\n{top5}\n"
        f"📂 Posiciones ({state.open_count()}/{MAX_OPEN_TRADES}):\n{open_str}\n"
        f"CB:{cb_str} | {btc_str} | Racha:{state.consec_losses}❌\n"
        f"PnL Total: ${state.total_pnl:+.2f}"
    )


def tg_error(msg: str):
    tg(f"🔥 <b>ERROR:</b> <code>{msg[:300]}</code>\n⏰ {utcnow()}")


# ══════════════════════════════════════════════════════════
# INDICADORES
# ══════════════════════════════════════════════════════════
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()

def calc_atr(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()

def calc_rsi(s: pd.Series, n: int) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).ewm(span=n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(span=n, adjust=False).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))

def calc_adx(df: pd.DataFrame, n: int):
    h, l   = df["high"], df["low"]
    up, dn = h.diff(), -l.diff()
    pdm    = up.where((up > dn) & (up > 0), 0.0)
    mdm    = dn.where((dn > up) & (dn > 0), 0.0)
    atr    = calc_atr(df, n)
    dip    = 100 * pdm.ewm(span=n, adjust=False).mean() / atr
    dim    = 100 * mdm.ewm(span=n, adjust=False).mean() / atr
    dx     = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    return dip, dim, dx.ewm(span=n, adjust=False).mean()

def calc_macd(s: pd.Series, fast=12, slow=26, sig=9):
    m    = ema(s, fast) - ema(s, slow)
    sig_ = ema(m, sig)
    return m, sig_, m - sig_

def calc_stoch_rsi(s: pd.Series, rsi_len=14, stoch_len=14, k=3, d=3):
    rsi    = calc_rsi(s, rsi_len)
    lo     = rsi.rolling(stoch_len).min()
    hi     = rsi.rolling(stoch_len).max()
    stoch  = 100 * (rsi - lo) / (hi - lo).replace(0, np.nan)
    k_line = stoch.rolling(k).mean()
    d_line = k_line.rolling(d).mean()
    return k_line, d_line


def compute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l, v, o = df["close"], df["high"], df["low"], df["volume"], df["open"]

    # EMAs
    df["ema8"]   = ema(c, FAST_LEN)
    df["ema21"]  = ema(c, PIVOT_LEN)
    df["ema48"]  = ema(c, BIAS_LEN)
    df["ema200"] = ema(c, SLOW_LEN)
    df["atr"]    = calc_atr(df, ATR_LEN)
    df["rsi"]    = calc_rsi(c, RSI_LEN)

    dip, dim, adx = calc_adx(df, ADX_LEN)
    df["dip"] = dip; df["dim"] = dim; df["adx"] = adx

    # MACD
    macd, macd_sig, macd_hist = calc_macd(c, MACD_FAST, MACD_SLOW, MACD_SIG)
    df["macd"]      = macd
    df["macd_sig"]  = macd_sig
    df["macd_hist"] = macd_hist
    df["macd_bull"] = (macd_hist > 0) & (macd_hist > macd_hist.shift())
    df["macd_bear"] = (macd_hist < 0) & (macd_hist < macd_hist.shift())

    # Stochastic RSI
    sk, sd = calc_stoch_rsi(c, RSI_LEN, STOCH_LEN)
    df["stoch_k"] = sk
    df["stoch_d"] = sd
    df["stoch_bull"] = (sk > sd) & (sk < 80) & (sk.shift() <= sd.shift())
    df["stoch_bear"] = (sk < sd) & (sk > 20) & (sk.shift() >= sd.shift())

    # Oscillator
    df["osc_raw"] = ((c - df["ema21"]) / (3.0 * df["atr"])) * 100
    df["osc"]     = ema(df["osc_raw"], OSC_LEN)
    df["osc_up"]  = (df["osc"] > 0) & (df["osc"].shift() <= 0)
    df["osc_dn"]  = (df["osc"] < 0) & (df["osc"].shift() >= 0)

    # Bollinger + Squeeze
    bb_std        = c.rolling(PIVOT_LEN).std()
    bb_up         = df["ema21"] + 2.0 * bb_std
    bb_lo         = df["ema21"] - 2.0 * bb_std
    kc_up         = df["ema21"] + 2.0 * df["atr"]
    df["squeeze"] = bb_up < kc_up
    bb_width      = (bb_up - bb_lo) / df["ema21"]
    df["is_trending"] = (adx > ADX_MIN) & (bb_width > sma(bb_width, 20) * 0.85)

    # Volume
    rng            = (h - l).replace(0, np.nan)
    df["buy_vol"]  = v * (c - l) / rng
    df["sell_vol"] = v * (h - c) / rng
    df["vol_ma"]   = sma(v, VOL_LEN)
    df["vol_spike"]= v > df["vol_ma"] * 1.05
    df["vol_bull"] = df["buy_vol"] > df["sell_vol"]
    df["vol_bear"] = df["sell_vol"] > df["buy_vol"]

    # Candles
    body              = (c - o).abs()
    body_pct          = body / rng.replace(0, np.nan)
    df["bull_candle"] = (c > o) & (body_pct >= 0.35)
    df["bear_candle"] = (c < o) & (body_pct >= 0.35)

    # Swing structure
    df["swing_low"]  = l.rolling(SWING_LB).min()
    df["swing_high"] = h.rolling(SWING_LB).max()

    # RSI divergence
    df["price_ll"] = (l < l.shift()) & (l.shift() < l.shift(2))
    df["rsi_hl"]   = (df["rsi"] > df["rsi"].shift()) & (df["rsi"].shift() > df["rsi"].shift(2))
    df["bull_div"] = df["price_ll"] & df["rsi_hl"] & (df["rsi"] < 45)

    df["price_hh"] = (h > h.shift()) & (h.shift() > h.shift(2))
    df["rsi_lh"]   = (df["rsi"] < df["rsi"].shift()) & (df["rsi"].shift() < df["rsi"].shift(2))
    df["bear_div"] = df["price_hh"] & df["rsi_lh"] & (df["rsi"] > 55)

    # EMA momentum
    df["ema_accel_bull"] = (df["ema8"] - df["ema8"].shift()) > (df["ema8"].shift() - df["ema8"].shift(2))
    df["ema_accel_bear"] = (df["ema8"] - df["ema8"].shift()) < (df["ema8"].shift() - df["ema8"].shift(2))

    return df


def htf_bias(df: pd.DataFrame) -> Tuple[bool, bool]:
    df  = compute(df)
    row = df.iloc[-2]
    bull = bool(row["close"] > row["ema48"] and row["ema21"] > row["ema48"])
    bear = bool(row["close"] < row["ema48"] and row["ema21"] < row["ema48"])
    return bull, bear


def htf2_macro(df: pd.DataFrame) -> Tuple[bool, bool]:
    df  = compute(df)
    row = df.iloc[-2]
    bull = bool(row["close"] > row["ema48"] and row["ema48"] > row["ema200"])
    bear = bool(row["close"] < row["ema48"] and row["ema48"] < row["ema200"])
    return bull, bear


# ══════════════════════════════════════════════════════════
# BTC DOMINANCE / BIAS (filtro macro)
# ══════════════════════════════════════════════════════════
def update_btc_bias(ex: ccxt.Exchange):
    """Actualiza el sesgo macro de BTC para filtrar señales."""
    try:
        df  = fetch_df(ex, "BTC/USDT:USDT", "1h", limit=100)
        df  = compute(df)
        row = df.iloc[-2]
        state.btc_bull = bool(row["close"] > row["ema48"] and row["ema48"] > row["ema200"])
        state.btc_bear = bool(row["close"] < row["ema48"] and row["ema48"] < row["ema200"])
        log.info(f"BTC bias: {'BULL' if state.btc_bull else 'BEAR' if state.btc_bear else 'NEUTRAL'}")
    except Exception as e:
        log.warning(f"BTC bias update failed: {e}")


# ══════════════════════════════════════════════════════════
# CONFLUENCE SCORE — 10 PUNTOS
# ══════════════════════════════════════════════════════════
def rsi_extreme_long(rsi: float) -> bool:
    """RSI en zona de sobreventa extrema — señal de rebote largo."""
    return 10 <= rsi <= 25

def rsi_extreme_short(rsi: float) -> bool:
    """RSI en zona de sobrecompra extrema — señal de caída corta."""
    return 78 <= rsi <= 90

def rsi_zone_label(rsi: float) -> str:
    """Etiqueta de zona RSI para Telegram."""
    if rsi < 10:        return f"⚠️ RSI HIPERVENTA {rsi:.1f}"
    if 10 <= rsi <= 25: return f"🔥 RSI SOBREVENTA {rsi:.1f} (10-25)"
    if 25 < rsi < 42:   return f"🟢 RSI bajo {rsi:.1f}"
    if 42 <= rsi <= 58: return f"⚪ RSI neutral {rsi:.1f}"
    if 58 < rsi < 78:   return f"🟡 RSI alto {rsi:.1f}"
    if 78 <= rsi <= 90: return f"🔥 RSI SOBRECOMPRA {rsi:.1f} (78-90)"
    return                     f"⚠️ RSI HIPERCOMPRA {rsi:.1f}"


def confluence_score(row: pd.Series,
                     htf1_bull: bool, htf1_bear: bool,
                     htf2_bull: bool, htf2_bear: bool) -> Tuple[int, int]:
    """
    11 puntos por direccion:
    1.  Ribbon EMA (precio vs EMA48 + EMA8 vs EMA21)
    2.  Oscillator crossover
    3.  HTF1 bias (15m)
    4.  HTF2 macro (1h)
    5.  ADX + DI direccional
    6.  RSI zona normal (42-78 long | 22-58 short)
    7.  Volumen (buy/sell pressure + spike)
    8.  Calidad de vela
    9.  MACD histograma direccional
    10. Stochastic RSI crossover
    11. RSI EXTREMO — bonus si RSI en 10-25 (long) o 78-90 (short)
    """
    rsi = float(row["rsi"])

    # LONG
    l1  = bool(row["close"] > row["ema48"] and row["ema8"] > row["ema21"])
    l2  = bool(row["osc_up"])
    l3  = htf1_bull
    l4  = htf2_bull
    l5  = bool(row["adx"] > ADX_MIN and row["dip"] > row["dim"])
    l6  = bool(42 <= rsi <= 78)          # zona normal long
    l7  = bool(row["vol_bull"] and row["vol_spike"] and not row["squeeze"])
    l8  = bool(row["bull_candle"] and row["close"] > row["ema21"])
    l9  = bool(row["macd_bull"])
    l10 = bool(row["stoch_bull"] or (row["stoch_k"] > row["stoch_d"] and row["stoch_k"] < 75))
    l11 = rsi_extreme_long(rsi)          # BONUS: RSI 10-25 sobreventa extrema

    # SHORT
    s1  = bool(row["close"] < row["ema48"] and row["ema8"] < row["ema21"])
    s2  = bool(row["osc_dn"])
    s3  = htf1_bear
    s4  = htf2_bear
    s5  = bool(row["adx"] > ADX_MIN and row["dim"] > row["dip"])
    s6  = bool(22 <= rsi <= 58)          # zona normal short
    s7  = bool(row["vol_bear"] and row["vol_spike"] and not row["squeeze"])
    s8  = bool(row["bear_candle"] and row["close"] < row["ema21"])
    s9  = bool(row["macd_bear"])
    s10 = bool(row["stoch_bear"] or (row["stoch_k"] < row["stoch_d"] and row["stoch_k"] > 25))
    s11 = rsi_extreme_short(rsi)         # BONUS: RSI 78-90 sobrecompra extrema

    ls = sum([l1, l2, l3, l4, l5, l6, l7, l8, l9, l10, l11])
    ss = sum([s1, s2, s3, s4, s5, s6, s7, s8, s9, s10, s11])
    return ls, ss


# ══════════════════════════════════════════════════════════
# SESSION
# ══════════════════════════════════════════════════════════
def in_session() -> bool:
    if not USE_SESSION: return True
    h = datetime.now(timezone.utc).hour
    return (LONDON_OPEN <= h < LONDON_CLOSE) or (NY_OPEN <= h < NY_CLOSE)


# ══════════════════════════════════════════════════════════
# EXCHANGE
# ══════════════════════════════════════════════════════════
def build_exchange() -> ccxt.Exchange:
    ex = ccxt.bingx({
        "apiKey":  API_KEY,
        "secret":  API_SECRET,
        "options": {"defaultType": "swap"},
    })
    ex.load_markets()
    return ex


def detect_hedge_mode(ex: ccxt.Exchange) -> bool:
    """
    Detecta si la cuenta esta en modo Hedge (bidireccional).
    BingX devuelve dualSidePosition=true cuando esta en hedge mode.
    """
    try:
        resp = ex.fapiPrivateGetPositionSideDual()  # endpoint estandar
        return bool(resp.get("dualSidePosition", False))
    except Exception:
        pass
    # Fallback: intentar abrir una orden tiny y ver si exige positionSide
    # Si no podemos detectarlo, asumimos hedge si el error original fue 109400
    try:
        # BingX especifico: consultar configuracion
        resp = ex.fetch_positions()
        # Si la respuesta incluye campos LONG/SHORT separados, es hedge
        for p in resp[:3]:
            if p.get("side", "") in ("long", "short") and p.get("info", {}).get("positionSide", "") in ("LONG", "SHORT"):
                return True
    except Exception:
        pass
    return False


def fetch_df(ex: ccxt.Exchange, symbol: str, tf: str, limit: int = 300) -> pd.DataFrame:
    raw = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df  = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df.astype(float)


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


def get_all_positions(ex: ccxt.Exchange) -> Dict[str, dict]:
    result = {}
    try:
        for p in ex.fetch_positions():
            if abs(float(p.get("contracts", 0) or 0)) > 0:
                sym = p["symbol"]
                result[sym] = p
    except Exception as e:
        log.warning(f"fetch_positions error: {e}")
    return result


def get_last_price(ex: ccxt.Exchange, symbol: str) -> float:
    return float(ex.fetch_ticker(symbol)["last"])


def get_min_amount(ex: ccxt.Exchange, symbol: str) -> float:
    """Retorna el mínimo de contratos/coins que acepta BingX para este par."""
    try:
        mkt = ex.markets.get(symbol, {})
        limits = mkt.get("limits", {})
        amt_min = limits.get("amount", {}).get("min", 0) or 0
        return float(amt_min)
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════
# ORDER PARAMS — soporte Hedge mode
# ══════════════════════════════════════════════════════════
def entry_params(side: str) -> dict:
    """Parametros extra para orden de entrada segun modo de cuenta."""
    if HEDGE_MODE:
        return {"positionSide": "LONG" if side == "buy" else "SHORT"}
    return {}


def exit_params(trade_side: str) -> dict:
    """Parametros extra para orden de cierre segun modo de cuenta."""
    if HEDGE_MODE:
        return {
            "positionSide": "LONG" if trade_side == "long" else "SHORT",
            "reduceOnly": True
        }
    return {"reduceOnly": True}


# ══════════════════════════════════════════════════════════
# UNIVERSE
# ══════════════════════════════════════════════════════════
def get_tradeable_symbols(ex: ccxt.Exchange) -> List[str]:
    symbols = []
    for sym, mkt in ex.markets.items():
        if not (mkt.get("swap") and mkt.get("quote") == "USDT" and mkt.get("active", True)):
            continue
        if sym in BLACKLIST: continue
        if SYMBOL_FILTER and mkt.get("base", "") not in SYMBOL_FILTER: continue
        symbols.append(sym)

    if not symbols:
        log.warning("No symbols found after filters.")
        return []

    log.info(f"Obteniendo tickers para {len(symbols)} pares...")
    try:
        tickers = ex.fetch_tickers(symbols)
    except Exception as e:
        log.warning(f"fetch_tickers error: {e}")
        return symbols[:TOP_N_SYMBOLS] if TOP_N_SYMBOLS > 0 else symbols

    ranked = []
    for sym in symbols:
        tk       = tickers.get(sym, {})
        vol_usdt = float(tk.get("quoteVolume", 0) or 0)
        if vol_usdt >= MIN_VOLUME_USDT:
            ranked.append((sym, vol_usdt))

    ranked.sort(key=lambda x: x[1], reverse=True)
    log.info(f"Pares con vol >${MIN_VOLUME_USDT/1e6:.1f}M: {len(ranked)}")

    result = [sym for sym, _ in ranked]
    if TOP_N_SYMBOLS > 0:
        result = result[:TOP_N_SYMBOLS]

    log.info(f"Universo final: {len(result)} pares")
    return result


# ══════════════════════════════════════════════════════════
# ORDER MANAGEMENT
# ══════════════════════════════════════════════════════════
def open_trade(ex: ccxt.Exchange, symbol: str, side: str,
               score: int, row: pd.Series) -> Optional[TradeState]:
    try:
        balance  = get_balance(ex)
        mult     = state.risk_mult(score)
        risk_pct = BASE_RISK * mult
        usdt_qty = balance * (risk_pct / 100)

        price    = get_last_price(ex, symbol)
        raw_amt  = usdt_qty / price
        amount   = float(ex.amount_to_precision(symbol, raw_amt))

        # ── FIX #2: Validar monto mínimo ──
        min_amt = get_min_amount(ex, symbol)
        if amount < min_amt:
            log.warning(f"[{symbol}] amount {amount} < min {min_amt} — saltando")
            return None
        if amount <= 0:
            log.warning(f"[{symbol}] amount calculado es 0 — saltando")
            return None

        log.info(f"[ENTRY] {symbol} {side.upper()} score={score}/10 "
                 f"risk={risk_pct:.1f}% size={amount} @ {price:.6g} "
                 f"{'[HEDGE]' if HEDGE_MODE else '[ONE-WAY]'}")

        # ── FIX #1: Enviar positionSide en modo Hedge ──
        order       = ex.create_order(symbol, "market", side, amount,
                                      params=entry_params(side))
        entry_price = float(order.get("average") or price)

        atr  = row["atr"]
        trade_side = "long" if side == "buy" else "short"

        if side == "buy":
            sl_p  = min(float(row["swing_low"]) - atr * 0.2,
                        entry_price - atr * SL_ATR)
            tp1_p = entry_price + atr * TP1_MULT
            tp2_p = entry_price + atr * TP2_MULT
        else:
            sl_p  = max(float(row["swing_high"]) + atr * 0.2,
                        entry_price + atr * SL_ATR)
            tp1_p = entry_price - atr * TP1_MULT
            tp2_p = entry_price - atr * TP2_MULT

        tp1_p = float(ex.price_to_precision(symbol, tp1_p))
        tp2_p = float(ex.price_to_precision(symbol, tp2_p))
        sl_p  = float(ex.price_to_precision(symbol, sl_p))

        close_side = "sell" if side == "buy" else "buy"
        half_amt   = float(ex.amount_to_precision(symbol, amount * 0.5))

        ep = exit_params(trade_side)

        try:
            ex.create_order(symbol, "limit", close_side, half_amt, tp1_p, ep)
            log.info(f"[{symbol}] TP1 @ {tp1_p:.6g}")
        except Exception as e:
            log.warning(f"[{symbol}] TP1 failed: {e}")

        try:
            ex.create_order(symbol, "limit", close_side, half_amt, tp2_p, ep)
            log.info(f"[{symbol}] TP2 @ {tp2_p:.6g}")
        except Exception as e:
            log.warning(f"[{symbol}] TP2 failed: {e}")

        try:
            sl_ep = {**ep, "stopPrice": sl_p}
            sl_ep.pop("reduceOnly", None)
            sl_ep["reduceOnly"] = True
            ex.create_order(symbol, "stop_market", close_side, amount, None, sl_ep)
            log.info(f"[{symbol}] SL  @ {sl_p:.6g}")
        except Exception as e:
            log.warning(f"[{symbol}] SL failed: {e}")

        t = TradeState(
            symbol=symbol, side=trade_side,
            entry_price=entry_price, tp1_price=tp1_p,
            tp2_price=tp2_p, sl_price=sl_p,
            entry_score=score, entry_time=utcnow(),
            contracts=amount,
        )
        if side == "buy": t.trail_high = entry_price
        else:             t.trail_low  = entry_price

        tg_signal(t, risk_pct, row,
                  extra=f"{'HEDGE' if HEDGE_MODE else 'ONE-WAY'}")
        return t

    except Exception as e:
        log.error(f"[{symbol}] open_trade error: {e}")
        tg_error(f"open_trade {symbol}: {e}")
        return None


def close_trade(ex: ccxt.Exchange, symbol: str, reason: str, current_price: float):
    if symbol not in state.trades: return
    t = state.trades[symbol]

    try: ex.cancel_all_orders(symbol)
    except Exception as e: log.warning(f"[{symbol}] cancel_all: {e}")

    pos = get_position(ex, symbol)
    pnl = 0.0

    if pos:
        contracts  = abs(float(pos.get("contracts", 0)))
        close_side = "sell" if t.side == "long" else "buy"
        ep         = exit_params(t.side)
        try:
            ex.create_order(symbol, "market", close_side, contracts, params=ep)
            pnl = ((current_price - t.entry_price) if t.side == "long"
                   else (t.entry_price - current_price)) * contracts
            log.info(f"[{symbol}] CLOSE {reason} pnl={pnl:+.2f}")
        except Exception as e:
            log.error(f"[{symbol}] close failed: {e}")
            tg_error(f"close_trade {symbol}: {e}")

    if pnl > 0:
        state.wins += 1; state.gross_profit += pnl; state.consec_losses = 0
    elif pnl < 0:
        state.losses += 1; state.gross_loss += abs(pnl); state.consec_losses += 1

    state.total_pnl  += pnl
    state.peak_equity = max(state.peak_equity, state.peak_equity + pnl)
    tg_close(reason, t, current_price, pnl)
    del state.trades[symbol]


# ══════════════════════════════════════════════════════════
# TRADE MANAGEMENT
# ══════════════════════════════════════════════════════════
def manage_open_trade(ex: ccxt.Exchange, symbol: str,
                      live_price: float, atr: float,
                      long_score: int, short_score: int,
                      live_pos: Optional[dict]):
    if symbol not in state.trades: return
    t = state.trades[symbol]

    # Cerrado externamente
    if live_pos is None:
        if t.side == "long":
            pnl_est = live_price - t.entry_price
            reason  = "TP ALCANZADO" if live_price >= t.tp2_price else "SL ALCANZADO"
        else:
            pnl_est = t.entry_price - live_price
            reason  = "TP ALCANZADO" if live_price <= t.tp2_price else "SL ALCANZADO"

        if pnl_est > 0:
            state.wins += 1; state.gross_profit += abs(pnl_est); state.consec_losses = 0
        else:
            state.losses += 1; state.gross_loss += abs(pnl_est); state.consec_losses += 1

        state.total_pnl += pnl_est
        tg_close(reason, t, live_price, pnl_est)
        del state.trades[symbol]
        return

    # TP1 hit
    if not t.tp1_hit:
        tp1_hit = ((t.side == "long"  and live_price >= t.tp1_price) or
                   (t.side == "short" and live_price <= t.tp1_price))
        if tp1_hit:
            t.tp1_hit = True
            contracts = float(live_pos.get("contracts", 0))
            pnl_est   = abs(t.tp1_price - t.entry_price) * contracts * 0.5
            log.info(f"[{symbol}] TP1 HIT @ {live_price:.6g}")
            tg_tp1(t, live_price, pnl_est)

    # Trailing
    if t.tp1_hit:
        if t.side == "long":
            t.trail_high = max(t.trail_high, live_price)
            if live_price < t.trail_high - atr * TRAIL_MULT:
                close_trade(ex, symbol, "TRAILING STOP", live_price); return
        else:
            t.trail_low = min(t.trail_low, live_price)
            if live_price > t.trail_low + atr * TRAIL_MULT:
                close_trade(ex, symbol, "TRAILING STOP", live_price); return

    # Flip
    if symbol in state.trades:
        if t.side == "long"  and short_score >= MIN_SCORE:
            close_trade(ex, symbol, "FLIP LONG→SHORT", live_price)
        elif t.side == "short" and long_score >= MIN_SCORE:
            close_trade(ex, symbol, "FLIP SHORT→LONG", live_price)


# ══════════════════════════════════════════════════════════
# SCAN DE UN SIMBOLO
# ══════════════════════════════════════════════════════════
def scan_symbol(ex: ccxt.Exchange, symbol: str) -> Optional[dict]:
    try:
        df  = fetch_df(ex, symbol, TF,   limit=400)
        df1 = fetch_df(ex, symbol, HTF1, limit=150)
        df2 = fetch_df(ex, symbol, HTF2, limit=300)

        df  = compute(df)
        row = df.iloc[-2]

        if pd.isna(row["adx"]) or pd.isna(row["rsi"]) or pd.isna(row["macd_hist"]):
            return None

        htf1_bull, htf1_bear = htf_bias(df1)
        htf2_bull, htf2_bear = htf2_macro(df2)

        ls, ss = confluence_score(row, htf1_bull, htf1_bear, htf2_bull, htf2_bear)

        rsi_val  = float(row["rsi"])
        rsi_ext_long  = rsi_extreme_long(rsi_val)
        rsi_ext_short = rsi_extreme_short(rsi_val)

        # Alerta independiente de RSI extremo (aunque no haya señal completa)
        if rsi_ext_long or rsi_ext_short:
            direction = "📉 POTENCIAL LONG (rebote)" if rsi_ext_long else "📈 POTENCIAL SHORT (caida)"
            tg(
                f"🔔 <b>RSI EXTREMO</b> — {symbol}\n"
                f"══════════════════════════\n"
                f"{rsi_zone_label(rsi_val)}\n"
                f"💵 Precio: <code>{float(row['close']):.6g}</code>\n"
                f"📊 ADX:{float(row['adx']):.1f} | "
                f"MACD:{float(row['macd_hist']):.4f}\n"
                f"{direction}\n"
                f"🎯 Score actual: {'L' if rsi_ext_long else 'S'} {ls if rsi_ext_long else ss}/11\n"
                f"⏰ {utcnow()}"
            )

        return {
            "symbol":        symbol,
            "long_score":    ls,
            "short_score":   ss,
            "row":           row,
            "atr":           float(row["atr"]),
            "live_price":    float(row["close"]),
            "is_trending":   bool(row["is_trending"]),
            "rsi_ext_long":  rsi_ext_long,
            "rsi_ext_short": rsi_ext_short,
        }
    except Exception as e:
        log.debug(f"[{symbol}] scan error: {e}")
        return None


# ══════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════
def main():
    global HEDGE_MODE

    log.info("=" * 65)
    log.info("   SATY ELITE v9 MULTI-SYMBOL — STARTING")
    log.info("=" * 65)

    dry_run = not (API_KEY and API_SECRET)
    if dry_run:
        log.warning("DRY-RUN: sin API keys.")
        while True:
            log.info("DRY-RUN activo..."); time.sleep(POLL_SECS)

    ex = build_exchange()

    # ── Detectar modo Hedge ──
    HEDGE_MODE = detect_hedge_mode(ex)
    log.info(f"Modo cuenta: {'HEDGE (positionSide requerido)' if HEDGE_MODE else 'ONE-WAY'}")

    balance = get_balance(ex)
    state.peak_equity = balance
    log.info(f"Balance: ${balance:.2f} USDT")

    symbols = get_tradeable_symbols(ex)
    if not symbols:
        log.error("Sin simbolos validos. Abortando.")
        return

    update_btc_bias(ex)
    tg_startup(balance, len(symbols))

    scan_count    = 0
    REFRESH_EVERY = max(1, int(3600 / max(POLL_SECS, 1)))
    BTC_REFRESH   = max(1, int(900  / max(POLL_SECS, 1)))  # cada 15 min

    while True:
        try:
            ts_start  = time.time()
            scan_count += 1
            now_str   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            log.info(f"━━━ [{now_str}] SCAN #{scan_count} — {len(symbols)} pares ━━━")

            # Refrescar universo ~1h
            if scan_count % REFRESH_EVERY == 0:
                try:
                    ex.load_markets()
                    symbols = get_tradeable_symbols(ex)
                except Exception as e:
                    log.warning(f"Refresh universo: {e}")

            # Refrescar BTC bias ~15min
            if scan_count % BTC_REFRESH == 0:
                update_btc_bias(ex)

            if state.cb_active():
                log.warning(f"CIRCUIT BREAKER — drawdown >= {CB_DD}%")
                time.sleep(POLL_SECS); continue

            # ── Gestionar posiciones abiertas ──
            live_positions = get_all_positions(ex)

            for sym in list(state.trades.keys()):
                try:
                    live_pos   = live_positions.get(sym)
                    live_price = (float(live_pos["markPrice"])
                                  if live_pos else get_last_price(ex, sym))
                    result = scan_symbol(ex, sym)
                    ls  = result["long_score"]  if result else 0
                    ss  = result["short_score"] if result else 0
                    atr = result["atr"]         if result else state.trades[sym].entry_price * 0.001
                    manage_open_trade(ex, sym, live_price, atr, ls, ss, live_pos)
                except Exception as e:
                    log.warning(f"[{sym}] manage error: {e}")

            # ── Escanear nuevas señales ──
            new_signals: List[dict] = []

            if state.open_count() < MAX_OPEN_TRADES and in_session():
                syms_to_scan = [s for s in symbols if s not in state.trades]

                with ThreadPoolExecutor(max_workers=5) as pool:
                    futures = {pool.submit(scan_symbol, ex, s): s for s in syms_to_scan}
                    results = [f.result() for f in as_completed(futures)
                               if f.result() is not None]

                for res in results:
                    best_side, best_score = None, 0

                    can_long  = res["long_score"]  >= MIN_SCORE and res["is_trending"]
                    can_short = res["short_score"] >= MIN_SCORE and res["is_trending"]

                    # Filtro BTC dominance
                    if BTC_FILTER:
                        if state.btc_bear: can_long  = False
                        if state.btc_bull: can_short = False

                    if can_long and res["long_score"] > best_score:
                        best_score = res["long_score"];  best_side = "long"
                    if can_short and res["short_score"] > best_score:
                        best_score = res["short_score"]; best_side = "short"

                    if best_side:
                        new_signals.append({
                            "symbol": res["symbol"], "side": best_side,
                            "score":  best_score,    "row":  res["row"],
                        })

                new_signals.sort(key=lambda x: x["score"], reverse=True)

                for sig in new_signals:
                    if state.open_count() >= MAX_OPEN_TRADES: break
                    sym   = sig["symbol"]
                    if sym in state.trades: continue
                    side  = "buy" if sig["side"] == "long" else "sell"
                    t     = open_trade(ex, sym, side, sig["score"], sig["row"])
                    if t:
                        state.trades[sym] = t

            elif not in_session():
                log.info("Fuera de sesion")
            else:
                log.info(f"Max trades ({MAX_OPEN_TRADES}) alcanzado")

            elapsed = time.time() - ts_start
            log.info(
                f"Ciclo {elapsed:.1f}s | "
                f"Trades:{state.open_count()}/{MAX_OPEN_TRADES} | "
                f"Señales:{len(new_signals)} | "
                f"W:{state.wins} L:{state.losses} PnL:${state.total_pnl:+.2f}"
            )

            if scan_count % 15 == 0:
                tg_scan_summary(new_signals, len(symbols))

        except ccxt.NetworkError as e:
            log.warning(f"Network: {e}")
        except ccxt.ExchangeError as e:
            log.error(f"Exchange: {e}")
            tg(f"❌ <b>Exchange error:</b> <code>{str(e)[:200]}</code>")
        except KeyboardInterrupt:
            log.info("Bot detenido.")
            tg("🛑 <b>Bot detenido manualmente.</b>")
            break
        except Exception as e:
            log.exception(f"Error: {e}")
            tg_error(str(e))

        time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()
