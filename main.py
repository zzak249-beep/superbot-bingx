"""
╔══════════════════════════════════════════════════════════╗
║          SATY ELITE v8 - CONFLUENCE ENGINE               ║
║  Python Bot para BingX Futures + Telegram               ║
║                                                          ║
║  Features:                                               ║
║  - Sistema de 8 puntos de confluencia (min 5/8)          ║
║  - 3 Timeframes: 5m entrada / 15m HTF / 1h macro        ║
║  - Partial TP: 50% en TP1, 50% trailing a TP2           ║
║  - SL basado en estructura (swing high/low)              ║
║  - Deteccion de regimen de mercado                       ║
║  - Session filter (London + NY)                          ║
║  - Risk dinamico por score y racha perdedora             ║
║  - Circuit breaker + proteccion perdidas consecutivas    ║
║  - Divergencia RSI                                       ║
║  - Notificaciones Telegram completas                     ║
╚══════════════════════════════════════════════════════════╝
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
from typing import Optional

# ══════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("saty_v8")

# ══════════════════════════════════════════════════════════
# CONFIG - Variables de entorno
# ══════════════════════════════════════════════════════════
API_KEY    = os.environ.get("BINGX_API_KEY",    "")
API_SECRET = os.environ.get("BINGX_API_SECRET", "")
SYMBOL     = os.environ.get("SYMBOL",           "BTC/USDT:USDT")
TF         = os.environ.get("TIMEFRAME",        "5m")
HTF1       = os.environ.get("HTF1",             "15m")
HTF2       = os.environ.get("HTF2",             "1h")
POLL_SECS  = int(os.environ.get("POLL_SECONDS", "60"))
TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID",   "")

# ── Confluencia ──
MIN_SCORE      = int(os.environ.get("MIN_SCORE",    "5"))   # 3-8

# ── Indicadores ──
FAST_LEN       = 8
PIVOT_LEN      = 21
BIAS_LEN       = 48
SLOW_LEN       = 200
ADX_LEN        = 14
ADX_MIN        = 20
RSI_LEN        = 14
ATR_LEN        = 14
VOL_LEN        = 20
OSC_LEN        = 3
SWING_LB       = 8     # lookback para swing SL

# ── Exits ──
TP1_MULT       = 1.0   # TP1 = 1x ATR (cierra 50%)
TP2_MULT       = 2.5   # TP2 = 2.5x ATR (cierra resto)
SL_ATR         = 1.0   # SL base ATR
USE_SWING_SL   = True  # SL basado en estructura
TRAIL_MULT     = 1.0   # Trailing post-TP1

# ── Risk dinamico ──
BASE_RISK      = float(os.environ.get("BASE_RISK",   "10.0"))  # % equity
RISK_BOOST     = 1.5   # multiplicador si score >= 7
RISK_CUT       = 0.5   # multiplicador tras perdidas consecutivas

# ── Session ──
USE_SESSION    = True
LONDON_OPEN    = 7
LONDON_CLOSE   = 16
NY_OPEN        = 13
NY_CLOSE       = 21

# ── Proteccion ──
USE_CB         = True
CB_DD          = float(os.environ.get("MAX_DRAWDOWN", "15.0"))  # % drawdown max
MAX_CONSEC_LOSS = 3


# ══════════════════════════════════════════════════════════
# ESTADO DEL BOT
# ══════════════════════════════════════════════════════════
@dataclass
class BotState:
    # Performance tracking
    wins:          int   = 0
    losses:        int   = 0
    gross_profit:  float = 0.0
    gross_loss:    float = 0.0
    consec_losses: int   = 0
    peak_equity:   float = 0.0
    total_pnl:     float = 0.0

    # Position tracking
    in_trade:       bool  = False
    trade_side:     str   = ""        # "long" | "short"
    entry_price:    float = 0.0
    tp1_hit:        bool  = False
    trail_high:     float = 0.0
    trail_low:      float = 0.0
    tp1_price:      float = 0.0
    tp2_price:      float = 0.0
    sl_price:       float = 0.0
    entry_score:    int   = 0
    entry_time:     str   = ""

    def win_rate(self) -> float:
        total = self.wins + self.losses
        return (self.wins / total * 100) if total > 0 else 0.0

    def profit_factor(self) -> float:
        return (self.gross_profit / self.gross_loss) if self.gross_loss > 0 else 0.0

    def score_bar(self, score: int) -> str:
        filled = "█" * score
        empty  = "░" * (8 - score)
        return filled + empty

    def cb_active(self) -> bool:
        if not USE_CB or self.peak_equity <= 0:
            return False
        dd = (self.peak_equity - (self.peak_equity + self.total_pnl)) / self.peak_equity * 100
        return dd >= CB_DD

    def risk_mult(self, score: int) -> float:
        if self.consec_losses >= MAX_CONSEC_LOSS:
            return RISK_CUT
        if score >= 7:
            return RISK_BOOST
        return 1.0


state = BotState()


# ══════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════
def tg(msg: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        log.warning(f"Telegram error: {e}")


def tg_startup(balance: float):
    tg(
        f"<b>SATY ELITE v8 - INICIADO</b>\n"
        f"══════════════════════\n"
        f"📊 <b>Par:</b> {SYMBOL}\n"
        f"⏱ <b>TF:</b> {TF} | <b>HTF1:</b> {HTF1} | <b>HTF2:</b> {HTF2}\n"
        f"🎯 <b>Score min:</b> {MIN_SCORE}/8\n"
        f"💰 <b>Balance:</b> ${balance:.2f} USDT\n"
        f"⚙️ <b>Risk base:</b> {BASE_RISK}% | <b>CB:</b> -{CB_DD}%\n"
        f"🕐 <b>Sesion:</b> London {LONDON_OPEN}-{LONDON_CLOSE}h UTC | NY {NY_OPEN}-{NY_CLOSE}h UTC\n"
        f"⏰ {utcnow()}"
    )


def tg_signal(side: str, score: int, price: float, tp1: float, tp2: float,
              sl: float, risk_pct: float, row: pd.Series):
    emoji  = "🟢" if side == "long" else "🔴"
    label  = "LONG" if side == "long" else "SHORT"
    rr1    = abs(tp1 - price) / max(abs(sl - price), 0.0001)
    rr2    = abs(tp2 - price) / max(abs(sl - price), 0.0001)
    tg(
        f"{emoji} <b>{label} ABIERTO</b> - {SYMBOL}\n"
        f"══════════════════════\n"
        f"🎯 <b>Score:</b> {score}/8 {state.score_bar(score)}\n"
        f"💵 <b>Entrada:</b> <code>{price:.4f}</code>\n"
        f"🟡 <b>TP1 (50%):</b> <code>{tp1:.4f}</code> (R:R 1:{rr1:.1f})\n"
        f"🟢 <b>TP2 (50%):</b> <code>{tp2:.4f}</code> (R:R 1:{rr2:.1f})\n"
        f"🛑 <b>SL:</b> <code>{sl:.4f}</code>\n"
        f"══════════════════════\n"
        f"📊 <b>ADX:</b> {row['adx']:.1f} | <b>RSI:</b> {row['rsi']:.1f}\n"
        f"📈 <b>Vol:</b> {row['volume']/row['vol_ma']:.2f}x | <b>OSC:</b> {row['osc']:.2f}\n"
        f"💼 <b>Risk:</b> {risk_pct:.1f}% equity\n"
        f"⏰ {utcnow()}"
    )


def tg_tp1(side: str, price: float, pnl: float):
    tg(
        f"🟡 <b>TP1 TOCADO</b> - 50% cerrado\n"
        f"══════════════════════\n"
        f"💵 <b>Precio:</b> <code>{price:.4f}</code>\n"
        f"💰 <b>PnL parcial:</b> ${pnl:+.2f}\n"
        f"🔄 Trailing activo en el 50% restante\n"
        f"⏰ {utcnow()}"
    )


def tg_close(reason: str, side: str, entry: float, exit_p: float, pnl: float, score: int):
    emoji = "✅" if pnl > 0 else "❌"
    tg(
        f"{emoji} <b>POSICION CERRADA</b> - {SYMBOL}\n"
        f"══════════════════════\n"
        f"📋 <b>Lado:</b> {side.upper()} | <b>Score entrada:</b> {score}/8\n"
        f"📌 <b>Razon:</b> {reason}\n"
        f"💵 <b>Entrada:</b> <code>{entry:.4f}</code>\n"
        f"💵 <b>Salida:</b>  <code>{exit_p:.4f}</code>\n"
        f"{'💰' if pnl > 0 else '💸'} <b>PnL:</b> ${pnl:+.2f}\n"
        f"══════════════════════\n"
        f"📊 <b>W/L:</b> {state.wins}W / {state.losses}L | "
        f"<b>WR:</b> {state.win_rate():.1f}% | "
        f"<b>PF:</b> {state.profit_factor():.2f}\n"
        f"⏰ {utcnow()}"
    )


def tg_scan(row: pd.Series, long_score: int, short_score: int,
            htf1_bull: bool, htf2_bull: bool, session: bool):
    regime = "TRENDING" if row["is_trending"] else "RANGING"
    cb_str = f"ACTIVO -{CB_DD}%" if state.cb_active() else "OK"
    tg(
        f"📡 <b>SCAN</b> - {SYMBOL} {TF}\n"
        f"══════════════════════\n"
        f"<b>LONG</b>  {long_score}/8  {state.score_bar(long_score)}\n"
        f"<b>SHORT</b> {short_score}/8 {state.score_bar(short_score)}\n"
        f"══════════════════════\n"
        f"ADX {row['adx']:.1f} | RSI {row['rsi']:.1f} | "
        f"Vol {row['volume']/row['vol_ma']:.2f}x\n"
        f"HTF1 {'BULL' if htf1_bull else 'BEAR'} | "
        f"HTF2 {'BULL' if htf2_bull else 'BEAR'}\n"
        f"Regimen: {regime} | Sesion: {'ACTIVA' if session else 'CERRADA'}\n"
        f"CB: {cb_str} | Racha: {state.consec_losses} perdidas\n"
        f"⏰ {utcnow()}"
    )


def tg_error(msg: str):
    tg(f"🔥 <b>ERROR:</b> <code>{msg}</code>\n⏰ {utcnow()}")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ══════════════════════════════════════════════════════════
# INDICADORES
# ══════════════════════════════════════════════════════════
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()

def calc_atr(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()

def calc_rsi(s: pd.Series, n: int) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).ewm(span=n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(span=n, adjust=False).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))

def calc_adx(df: pd.DataFrame, n: int):
    h, l = df["high"], df["low"]
    up, dn = h.diff(), -l.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    mdm = dn.where((dn > up) & (dn > 0), 0.0)
    atr = calc_atr(df, n)
    dip = 100 * pdm.ewm(span=n, adjust=False).mean() / atr
    dim = 100 * mdm.ewm(span=n, adjust=False).mean() / atr
    dx  = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    return dip, dim, dx.ewm(span=n, adjust=False).mean()


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
    df["dip"] = dip
    df["dim"] = dim
    df["adx"] = adx

    # Oscillator
    df["osc_raw"] = ((c - df["ema21"]) / (3.0 * df["atr"])) * 100
    df["osc"]     = ema(df["osc_raw"], OSC_LEN)
    df["osc_up"]  = (df["osc"] > 0) & (df["osc"].shift() <= 0)
    df["osc_dn"]  = (df["osc"] < 0) & (df["osc"].shift() >= 0)

    # Squeeze
    bb_std       = c.rolling(PIVOT_LEN).std()
    bb_up        = df["ema21"] + 2.0 * bb_std
    kc_up        = df["ema21"] + 2.0 * df["atr"]
    df["squeeze"]= bb_up < kc_up

    # Market regime
    bb_lo        = df["ema21"] - 2.0 * bb_std
    bb_width     = (bb_up - bb_lo) / df["ema21"]
    bb_width_ma  = sma(bb_width, 20)
    df["is_trending"] = (adx > ADX_MIN) & (bb_width > bb_width_ma * 0.9)

    # Volume
    rng          = (h - l).replace(0, np.nan)
    df["buy_vol"]  = v * (c - l) / rng
    df["sell_vol"] = v * (h - c) / rng
    df["vol_ma"]   = sma(v, VOL_LEN)
    df["vol_spike"]= v > df["vol_ma"] * 1.1
    df["vol_bull"] = df["buy_vol"] > df["sell_vol"]
    df["vol_bear"] = df["sell_vol"] > df["buy_vol"]

    # Candle quality
    body          = (c - o).abs()
    body_pct      = body / rng.replace(0, np.nan)
    df["bull_candle"] = (c > o) & (body_pct >= 0.4)
    df["bear_candle"] = (c < o) & (body_pct >= 0.4)

    # Swing structure for SL
    df["swing_low"]  = l.rolling(SWING_LB).min()
    df["swing_high"] = h.rolling(SWING_LB).max()

    # RSI divergence
    df["price_ll"] = (l  < l.shift())  & (l.shift()  < l.shift(2))
    df["rsi_hl"]   = (df["rsi"] > df["rsi"].shift()) & (df["rsi"].shift() > df["rsi"].shift(2))
    df["bull_div"] = df["price_ll"] & df["rsi_hl"] & (df["rsi"] < 45)

    df["price_hh"] = (h  > h.shift())  & (h.shift()  > h.shift(2))
    df["rsi_lh"]   = (df["rsi"] < df["rsi"].shift()) & (df["rsi"].shift() < df["rsi"].shift(2))
    df["bear_div"] = df["price_hh"] & df["rsi_lh"] & (df["rsi"] > 55)

    return df


def htf_bias(df: pd.DataFrame) -> tuple[bool, bool]:
    """Returns (is_bull, is_bear) based on HTF dataframe."""
    df  = compute(df)
    row = df.iloc[-2]
    bull = (row["close"] > row["ema48"]) and (row["ema21"] > row["ema48"])
    bear = (row["close"] < row["ema48"]) and (row["ema21"] < row["ema48"])
    return bull, bear


def htf2_macro(df: pd.DataFrame) -> tuple[bool, bool]:
    """1h macro bias: price > ema48 AND ema48 > ema200."""
    df  = compute(df)
    row = df.iloc[-2]
    bull = (row["close"] > row["ema48"]) and (row["ema48"] > row["ema200"])
    bear = (row["close"] < row["ema48"]) and (row["ema48"] < row["ema200"])
    return bull, bear


# ══════════════════════════════════════════════════════════
# CONFLUENCE SCORE
# ══════════════════════════════════════════════════════════
def confluence_score(row: pd.Series, htf1_bull: bool, htf1_bear: bool,
                     htf2_bull: bool, htf2_bear: bool) -> tuple[int, int]:
    """Returns (long_score, short_score) 0-8."""

    # LONG — 8 puntos
    l1 = bool(row["close"] > row["ema48"] and row["ema8"] > row["ema21"])
    l2 = bool(row["osc_up"])
    l3 = htf1_bull
    l4 = htf2_bull
    l5 = bool(row["adx"] > ADX_MIN and row["dip"] > row["dim"])
    l6 = bool(45 <= row["rsi"] <= 75)
    l7 = bool(row["vol_bull"] and row["vol_spike"] and not row["squeeze"])
    l8 = bool(row["bull_candle"] and row["close"] > row["ema21"])

    # SHORT — 8 puntos
    s1 = bool(row["close"] < row["ema48"] and row["ema8"] < row["ema21"])
    s2 = bool(row["osc_dn"])
    s3 = htf1_bear
    s4 = htf2_bear
    s5 = bool(row["adx"] > ADX_MIN and row["dim"] > row["dip"])
    s6 = bool(25 <= row["rsi"] <= 55)
    s7 = bool(row["vol_bear"] and row["vol_spike"] and not row["squeeze"])
    s8 = bool(row["bear_candle"] and row["close"] < row["ema21"])

    ls = sum([l1, l2, l3, l4, l5, l6, l7, l8])
    ss = sum([s1, s2, s3, s4, s5, s6, s7, s8])

    log.info(
        f"LONG  score {ls}/8: ribbon={l1} osc={l2} htf1={l3} htf2={l4} "
        f"adx={l5} rsi={l6} vol={l7} candle={l8}"
    )
    log.info(
        f"SHORT score {ss}/8: ribbon={s1} osc={s2} htf1={s3} htf2={s4} "
        f"adx={s5} rsi={s6} vol={s7} candle={s8}"
    )

    return ls, ss


# ══════════════════════════════════════════════════════════
# SESSION CHECK
# ══════════════════════════════════════════════════════════
def in_session() -> bool:
    if not USE_SESSION:
        return True
    h = datetime.now(timezone.utc).hour
    in_london = LONDON_OPEN <= h < LONDON_CLOSE
    in_ny     = NY_OPEN     <= h < NY_CLOSE
    return in_london or in_ny


# ══════════════════════════════════════════════════════════
# EXCHANGE
# ══════════════════════════════════════════════════════════
def build_exchange() -> ccxt.Exchange:
    ex = ccxt.bingx({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "options": {"defaultType": "swap"},
    })
    ex.load_markets()
    return ex


def fetch_df(ex: ccxt.Exchange, symbol: str, tf: str, limit: int = 300) -> pd.DataFrame:
    raw = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df  = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df.astype(float)


def get_balance(ex: ccxt.Exchange) -> float:
    return float(ex.fetch_balance()["USDT"]["free"])


def get_position(ex: ccxt.Exchange, symbol: str) -> Optional[dict]:
    for p in ex.fetch_positions([symbol]):
        if abs(float(p.get("contracts", 0) or 0)) > 0:
            return p
    return None


def get_last_price(ex: ccxt.Exchange, symbol: str) -> float:
    return float(ex.fetch_ticker(symbol)["last"])


# ══════════════════════════════════════════════════════════
# ORDER MANAGEMENT
# ══════════════════════════════════════════════════════════
def open_trade(ex: ccxt.Exchange, side: str, score: int, row: pd.Series):
    """Open market entry with TP1, TP2 (partial) and SL."""
    balance   = get_balance(ex)
    mult      = state.risk_mult(score)
    risk_pct  = BASE_RISK * mult
    usdt_qty  = balance * (risk_pct / 100)

    price  = get_last_price(ex, SYMBOL)
    amount = float(ex.amount_to_precision(SYMBOL, usdt_qty / price))

    log.info(f"[ENTRY] {side.upper()} score={score}/8 risk={risk_pct:.1f}% size={amount} @ {price:.4f}")

    order       = ex.create_order(SYMBOL, "market", side, amount)
    entry_price = float(order.get("average") or price)

    atr = row["atr"]

    # Swing-based SL
    if USE_SWING_SL:
        if side == "buy":
            sl_p = min(row["swing_low"] - atr * 0.2, entry_price - atr * SL_ATR)
        else:
            sl_p = max(row["swing_high"] + atr * 0.2, entry_price + atr * SL_ATR)
    else:
        sl_p = entry_price - atr * SL_ATR if side == "buy" else entry_price + atr * SL_ATR

    if side == "buy":
        tp1_p = entry_price + atr * TP1_MULT
        tp2_p = entry_price + atr * TP2_MULT
    else:
        tp1_p = entry_price - atr * TP1_MULT
        tp2_p = entry_price - atr * TP2_MULT

    tp1_p = float(ex.price_to_precision(SYMBOL, tp1_p))
    tp2_p = float(ex.price_to_precision(SYMBOL, tp2_p))
    sl_p  = float(ex.price_to_precision(SYMBOL, sl_p))

    close_side = "sell" if side == "buy" else "buy"
    half_amt   = float(ex.amount_to_precision(SYMBOL, amount * 0.5))

    # TP1 — 50% de la posicion
    try:
        ex.create_order(SYMBOL, "limit", close_side, half_amt, tp1_p, {"reduceOnly": True})
        log.info(f"[TP1] @ {tp1_p:.4f}")
    except Exception as e:
        log.warning(f"TP1 order failed: {e}")

    # TP2 — 50% restante (limit)
    try:
        ex.create_order(SYMBOL, "limit", close_side, half_amt, tp2_p, {"reduceOnly": True})
        log.info(f"[TP2] @ {tp2_p:.4f}")
    except Exception as e:
        log.warning(f"TP2 order failed: {e}")

    # SL
    try:
        ex.create_order(SYMBOL, "stop_market", close_side, amount, None,
                        {"stopPrice": sl_p, "reduceOnly": True})
        log.info(f"[SL]  @ {sl_p:.4f}")
    except Exception as e:
        log.warning(f"SL order failed: {e}")

    # Actualizar estado
    state.in_trade    = True
    state.trade_side  = side
    state.entry_price = entry_price
    state.tp1_price   = tp1_p
    state.tp2_price   = tp2_p
    state.sl_price    = sl_p
    state.tp1_hit     = False
    state.entry_score = score
    state.entry_time  = utcnow()

    if side == "buy":
        state.trail_high = entry_price
    else:
        state.trail_low  = entry_price

    tg_signal(side, score, entry_price, tp1_p, tp2_p, sl_p, risk_pct, row)
    return entry_price


def close_trade(ex: ccxt.Exchange, reason: str, current_price: float):
    """Force-close remaining position."""
    pos = get_position(ex, SYMBOL)
    if pos is None:
        state.in_trade = False
        return

    try:
        ex.cancel_all_orders(SYMBOL)
    except Exception as e:
        log.warning(f"Cancel orders: {e}")

    contracts  = float(pos.get("contracts", 0))
    side       = pos.get("side", "long")
    close_side = "sell" if side == "long" else "buy"
    pnl        = 0.0

    try:
        ex.create_order(SYMBOL, "market", close_side, abs(contracts),
                        params={"reduceOnly": True})

        if state.trade_side == "long":
            pnl = (current_price - state.entry_price) * abs(contracts)
        else:
            pnl = (state.entry_price - current_price) * abs(contracts)

        log.info(f"[CLOSE] {reason} pnl={pnl:+.2f}")

    except Exception as e:
        log.error(f"Close failed: {e}")
        tg_error(f"Close position failed: {e}")

    # Update stats
    if pnl > 0:
        state.wins         += 1
        state.gross_profit += pnl
        state.consec_losses = 0
    elif pnl < 0:
        state.losses       += 1
        state.gross_loss   += abs(pnl)
        state.consec_losses += 1

    state.total_pnl    += pnl
    state.peak_equity   = max(state.peak_equity, state.peak_equity + pnl)

    tg_close(reason, state.trade_side, state.entry_price, current_price,
             pnl, state.entry_score)

    state.in_trade    = False
    state.trade_side  = ""
    state.tp1_hit     = False


# ══════════════════════════════════════════════════════════
# TRAILING STOP POST TP1
# ══════════════════════════════════════════════════════════
def check_trailing(ex: ccxt.Exchange, price: float, atr: float) -> bool:
    """Returns True if trailing stop was hit and position was closed."""
    if not state.tp1_hit:
        return False

    if state.trade_side == "long":
        if price > state.trail_high:
            state.trail_high = price
        stop = state.trail_high - atr * TRAIL_MULT
        if price < stop:
            log.info(f"[TRAIL] Long trail stop hit @ {price:.4f}")
            close_trade(ex, "TRAILING STOP", price)
            return True

    elif state.trade_side == "short":
        if price < state.trail_low:
            state.trail_low = price
        stop = state.trail_low + atr * TRAIL_MULT
        if price > stop:
            log.info(f"[TRAIL] Short trail stop hit @ {price:.4f}")
            close_trade(ex, "TRAILING STOP", price)
            return True

    return False


def check_tp1_hit(price: float) -> bool:
    """Detect if TP1 has been hit (price crossed TP1 level)."""
    if state.tp1_hit or not state.in_trade:
        return False
    if state.trade_side == "long"  and price >= state.tp1_price:
        return True
    if state.trade_side == "short" and price <= state.tp1_price:
        return True
    return False


# ══════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════
def main():
    log.info("=" * 60)
    log.info(" SATY ELITE v8 - CONFLUENCE ENGINE  STARTING")
    log.info("=" * 60)

    dry_run = not (API_KEY and API_SECRET)
    if dry_run:
        log.warning("DRY-RUN: no API keys. Set BINGX_API_KEY and BINGX_API_SECRET.")
        while True:
            log.info("DRY-RUN mode active. Waiting...")
            time.sleep(POLL_SECS)

    ex = build_exchange()
    log.info(f"Connected | {SYMBOL} | {TF} | HTF1:{HTF1} | HTF2:{HTF2}")

    balance = get_balance(ex)
    state.peak_equity = balance
    log.info(f"Balance: ${balance:.2f} USDT")
    tg_startup(balance)

    scan_count = 0

    while True:
        try:
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            log.info(f"─── [{now_str}] SCAN #{scan_count + 1} ───────────────────")

            # ── Fetch data ──
            df     = fetch_df(ex, SYMBOL, TF,   limit=350)
            df1    = fetch_df(ex, SYMBOL, HTF1, limit=150)
            df2    = fetch_df(ex, SYMBOL, HTF2, limit=300)

            df  = compute(df)
            row = df.iloc[-2]  # ultima vela cerrada

            # ── HTF bias ──
            htf1_bull, htf1_bear = htf_bias(df1)
            htf2_bull, htf2_bear = htf2_macro(df2)

            atr   = row["atr"]
            price = row["close"]

            log.info(
                f"Price={price:.4f} | ADX={row['adx']:.1f} | RSI={row['rsi']:.1f} | "
                f"ATR={atr:.6f} | Vol={row['volume']/row['vol_ma']:.2f}x | "
                f"OSC={row['osc']:.2f} | SQZ={'Y' if row['squeeze'] else 'N'} | "
                f"HTF1={'B' if htf1_bull else 'b'} | HTF2={'B' if htf2_bull else 'b'} | "
                f"Trend={'Y' if row['is_trending'] else 'N'} | "
                f"Session={'Y' if in_session() else 'N'}"
            )

            # ── Score ──
            long_score, short_score = confluence_score(
                row, htf1_bull, htf1_bear, htf2_bull, htf2_bear
            )

            # ── Send periodic scan to Telegram (every 15 scans) ──
            scan_count += 1
            if scan_count % 15 == 0:
                tg_scan(row, long_score, short_score, htf1_bull, htf2_bull, in_session())

            # ── Circuit breaker ──
            if state.cb_active():
                log.warning(f"CIRCUIT BREAKER ACTIVE — drawdown >= {CB_DD}% — no new trades")
                time.sleep(POLL_SECS)
                continue

            # ── Position management ──
            live_price = get_last_price(ex, SYMBOL)
            position   = get_position(ex, SYMBOL)

            if position is not None and state.in_trade:

                # Check TP1
                if check_tp1_hit(live_price):
                    state.tp1_hit = True
                    log.info(f"[TP1 HIT] Activating trailing stop")
                    estimated_pnl = abs(state.tp1_price - state.entry_price) * float(
                        position.get("contracts", 0)) * 0.5
                    tg_tp1(state.trade_side, live_price, estimated_pnl)

                # Trailing stop (post TP1)
                if check_trailing(ex, live_price, atr):
                    position = None

                # Signal flip
                if position is not None:
                    if state.trade_side == "long" and short_score >= MIN_SCORE:
                        log.info("[FLIP] Closing long - strong short signal")
                        close_trade(ex, "FLIP LONG->SHORT", live_price)
                        position = None
                    elif state.trade_side == "short" and long_score >= MIN_SCORE:
                        log.info("[FLIP] Closing short - strong long signal")
                        close_trade(ex, "FLIP SHORT->LONG", live_price)
                        position = None

            elif position is None and state.in_trade:
                # Position was closed externally (TP or SL hit on exchange)
                log.info("[EXTERNAL CLOSE] Position closed on exchange (TP/SL)")
                pnl_est = 0.0
                reason  = "TP ALCANZADO" if live_price >= state.tp2_price else "SL ALCANZADO"
                if state.trade_side == "long":
                    pnl_est = (live_price - state.entry_price)
                else:
                    pnl_est = (state.entry_price - live_price)

                if pnl_est > 0:
                    state.wins         += 1
                    state.gross_profit += abs(pnl_est)
                    state.consec_losses = 0
                else:
                    state.losses        += 1
                    state.gross_loss    += abs(pnl_est)
                    state.consec_losses += 1

                state.total_pnl += pnl_est
                tg_close(reason, state.trade_side, state.entry_price,
                         live_price, pnl_est, state.entry_score)
                state.in_trade   = False
                state.trade_side = ""
                state.tp1_hit    = False

            # ── Entry ──
            if position is None and not state.in_trade:

                if not in_session():
                    log.info("Outside trading session — skipping")

                elif not row["is_trending"]:
                    log.info(f"Market ranging (ADX={row['adx']:.1f}) — no entry")

                elif long_score >= MIN_SCORE:
                    log.info(f"*** LONG SIGNAL *** score={long_score}/8")
                    open_trade(ex, "buy", long_score, row)

                elif short_score >= MIN_SCORE:
                    log.info(f"*** SHORT SIGNAL *** score={short_score}/8")
                    open_trade(ex, "sell", short_score, row)

                else:
                    log.info(
                        f"No signal — L:{long_score}/8 S:{short_score}/8 "
                        f"(need {MIN_SCORE})"
                    )

        except ccxt.NetworkError as e:
            log.warning(f"Network error: {e} — retrying...")
        except ccxt.ExchangeError as e:
            log.error(f"Exchange error: {e}")
            tg(f"❌ <b>Exchange error:</b> <code>{e}</code>")
        except KeyboardInterrupt:
            log.info("Bot stopped by user.")
            tg("🛑 <b>Bot detenido manualmente.</b>")
            break
        except Exception as e:
            log.exception(f"Unexpected error: {e}")
            tg_error(str(e))

        time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()
