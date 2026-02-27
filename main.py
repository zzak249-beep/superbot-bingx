"""
╔══════════════════════════════════════════════════════════════════╗
║         SATY ELITE v8 - MULTI-SYMBOL CONFLUENCE ENGINE          ║
║   Python Bot para BingX Futures + Telegram                       ║
║                                                                  ║
║  Features:                                                       ║
║  - Escaneo de TODOS los pares USDT perpetuos de BingX            ║
║  - Sistema de 8 puntos de confluencia (min configurable)         ║
║  - 3 Timeframes: 5m entrada / 15m HTF / 1h macro                 ║
║  - Partial TP: 50% en TP1, 50% trailing a TP2                    ║
║  - SL basado en estructura (swing high/low)                      ║
║  - Deteccion de regimen de mercado                               ║
║  - Session filter (London + NY)                                  ║
║  - Risk dinamico por score y racha perdedora                     ║
║  - Circuit breaker + proteccion perdidas consecutivas            ║
║  - Divergencia RSI                                               ║
║  - Gestion de multiples posiciones simultaneas                   ║
║  - Notificaciones Telegram completas                             ║
╚══════════════════════════════════════════════════════════════════╝

Instalacion:
    pip install ccxt pandas numpy requests

Variables de entorno necesarias:
    BINGX_API_KEY        - API Key de BingX
    BINGX_API_SECRET     - API Secret de BingX
    TELEGRAM_BOT_TOKEN   - Token del bot de Telegram
    TELEGRAM_CHAT_ID     - Chat ID de Telegram

Variables opcionales:
    SYMBOL_FILTER        - Filtrar por moneda base, ej: "BTC,ETH,SOL" (vacío = todas)
    BLACKLIST            - Pares excluidos, ej: "LUNA/USDT:USDT,FTT/USDT:USDT"
    MAX_OPEN_TRADES      - Máximo de posiciones simultáneas (default: 3)
    MIN_SCORE            - Score mínimo para entrar (default: 5)
    BASE_RISK            - % del capital por operación (default: 2.0)
    MAX_DRAWDOWN         - % máximo drawdown antes de circuit breaker (default: 15.0)
    MIN_VOLUME_USDT      - Volumen mínimo 24h en USDT (default: 5000000)
    POLL_SECONDS         - Segundos entre cada ciclo de escaneo (default: 60)
    TIMEFRAME            - TF de entrada (default: 5m)
    HTF1                 - Primer HTF (default: 15m)
    HTF2                 - Segundo HTF / macro (default: 1h)
    TOP_N_SYMBOLS        - Número máximo de pares a escanear (0 = todos, default: 50)
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
from typing import Optional, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed

# ══════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("saty_v8_multi")

# ══════════════════════════════════════════════════════════
# CONFIG - Variables de entorno
# ══════════════════════════════════════════════════════════
API_KEY    = os.environ.get("BINGX_API_KEY",    "")
API_SECRET = os.environ.get("BINGX_API_SECRET", "")
TF         = os.environ.get("TIMEFRAME",        "5m")
HTF1       = os.environ.get("HTF1",             "15m")
HTF2       = os.environ.get("HTF2",             "1h")
POLL_SECS  = int(os.environ.get("POLL_SECONDS", "60"))
TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID",   "")

# ── Filtros de universo ──
_sym_filter  = os.environ.get("SYMBOL_FILTER", "")
SYMBOL_FILTER: List[str] = [s.strip().upper() for s in _sym_filter.split(",") if s.strip()]

_blacklist   = os.environ.get("BLACKLIST", "")
BLACKLIST: List[str] = [s.strip() for s in _blacklist.split(",") if s.strip()]

MIN_VOLUME_USDT = float(os.environ.get("MIN_VOLUME_USDT", "5000000"))  # 5M USDT/24h
TOP_N_SYMBOLS   = int(os.environ.get("TOP_N_SYMBOLS", "50"))           # 0 = todos

# ── Gestion de posiciones multiples ──
MAX_OPEN_TRADES = int(os.environ.get("MAX_OPEN_TRADES", "3"))

# ── Confluencia ──
MIN_SCORE       = int(os.environ.get("MIN_SCORE",    "5"))  # 3-8

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
SWING_LB       = 8

# ── Exits ──
TP1_MULT       = 1.0
TP2_MULT       = 2.5
SL_ATR         = 1.0
USE_SWING_SL   = True
TRAIL_MULT     = 1.0

# ── Risk dinamico ──
BASE_RISK      = float(os.environ.get("BASE_RISK",   "2.0"))  # % equity por trade
RISK_BOOST     = 1.5
RISK_CUT       = 0.5

# ── Session ──
USE_SESSION    = True
LONDON_OPEN    = 7
LONDON_CLOSE   = 16
NY_OPEN        = 13
NY_CLOSE       = 21

# ── Proteccion global ──
USE_CB          = True
CB_DD           = float(os.environ.get("MAX_DRAWDOWN", "15.0"))
MAX_CONSEC_LOSS = 3


# ══════════════════════════════════════════════════════════
# ESTADO DEL BOT
# ══════════════════════════════════════════════════════════
@dataclass
class TradeState:
    """Estado de una posicion abierta en un simbolo."""
    symbol:       str   = ""
    side:         str   = ""     # "long" | "short"
    entry_price:  float = 0.0
    tp1_price:    float = 0.0
    tp2_price:    float = 0.0
    sl_price:     float = 0.0
    tp1_hit:      bool  = False
    trail_high:   float = 0.0
    trail_low:    float = 0.0
    entry_score:  int   = 0
    entry_time:   str   = ""


@dataclass
class BotState:
    # Performance
    wins:          int   = 0
    losses:        int   = 0
    gross_profit:  float = 0.0
    gross_loss:    float = 0.0
    consec_losses: int   = 0
    peak_equity:   float = 0.0
    total_pnl:     float = 0.0

    # Posiciones activas: symbol -> TradeState
    trades: Dict[str, TradeState] = field(default_factory=dict)

    def open_count(self) -> int:
        return len(self.trades)

    def win_rate(self) -> float:
        total = self.wins + self.losses
        return (self.wins / total * 100) if total > 0 else 0.0

    def profit_factor(self) -> float:
        return (self.gross_profit / self.gross_loss) if self.gross_loss > 0 else 0.0

    def score_bar(self, score: int) -> str:
        return "█" * score + "░" * (8 - score)

    def cb_active(self) -> bool:
        if not USE_CB or self.peak_equity <= 0:
            return False
        current = self.peak_equity + self.total_pnl
        dd = (self.peak_equity - current) / self.peak_equity * 100
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


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def tg_startup(balance: float, n_symbols: int):
    filter_str = ", ".join(SYMBOL_FILTER) if SYMBOL_FILTER else "TODOS"
    tg(
        f"<b>SATY ELITE v8 MULTI - INICIADO</b>\n"
        f"══════════════════════════════\n"
        f"🌍 <b>Universo:</b> {n_symbols} pares USDT perpetuos\n"
        f"🔍 <b>Filtro base:</b> {filter_str}\n"
        f"⏱ <b>TF:</b> {TF} | <b>HTF1:</b> {HTF1} | <b>HTF2:</b> {HTF2}\n"
        f"🎯 <b>Score min:</b> {MIN_SCORE}/8 | <b>Max trades:</b> {MAX_OPEN_TRADES}\n"
        f"💰 <b>Balance:</b> ${balance:.2f} USDT\n"
        f"⚙️ <b>Risk/trade:</b> {BASE_RISK}% | <b>CB:</b> -{CB_DD}%\n"
        f"📊 <b>Vol mínimo:</b> ${MIN_VOLUME_USDT/1e6:.1f}M USDT/24h\n"
        f"⏰ {utcnow()}"
    )


def tg_signal(t: TradeState, risk_pct: float, row: pd.Series):
    emoji = "🟢" if t.side == "long" else "🔴"
    label = "LONG" if t.side == "long" else "SHORT"
    rr1   = abs(t.tp1_price - t.entry_price) / max(abs(t.sl_price - t.entry_price), 1e-9)
    rr2   = abs(t.tp2_price - t.entry_price) / max(abs(t.sl_price - t.entry_price), 1e-9)
    tg(
        f"{emoji} <b>{label} ABIERTO</b> — {t.symbol}\n"
        f"══════════════════════════════\n"
        f"🎯 <b>Score:</b> {t.entry_score}/8 {state.score_bar(t.entry_score)}\n"
        f"💵 <b>Entrada:</b> <code>{t.entry_price:.6g}</code>\n"
        f"🟡 <b>TP1 (50%):</b> <code>{t.tp1_price:.6g}</code> (R:R 1:{rr1:.1f})\n"
        f"🟢 <b>TP2 (50%):</b> <code>{t.tp2_price:.6g}</code> (R:R 1:{rr2:.1f})\n"
        f"🛑 <b>SL:</b> <code>{t.sl_price:.6g}</code>\n"
        f"══════════════════════════════\n"
        f"📊 <b>ADX:</b> {row['adx']:.1f} | <b>RSI:</b> {row['rsi']:.1f}\n"
        f"📈 <b>Vol:</b> {row['volume']/row['vol_ma']:.2f}x | <b>OSC:</b> {row['osc']:.2f}\n"
        f"💼 <b>Risk:</b> {risk_pct:.1f}% | <b>Trades abiertos:</b> {state.open_count()}/{MAX_OPEN_TRADES}\n"
        f"⏰ {utcnow()}"
    )


def tg_tp1(t: TradeState, price: float, pnl: float):
    tg(
        f"🟡 <b>TP1 TOCADO</b> — {t.symbol}\n"
        f"══════════════════════════════\n"
        f"💵 <b>Precio:</b> <code>{price:.6g}</code>\n"
        f"💰 <b>PnL parcial est.:</b> ${pnl:+.2f}\n"
        f"🔄 Trailing activo en el 50% restante\n"
        f"⏰ {utcnow()}"
    )


def tg_close(reason: str, t: TradeState, exit_p: float, pnl: float):
    emoji = "✅" if pnl > 0 else "❌"
    tg(
        f"{emoji} <b>CERRADO</b> — {t.symbol}\n"
        f"══════════════════════════════\n"
        f"📋 <b>Lado:</b> {t.side.upper()} | <b>Score:</b> {t.entry_score}/8\n"
        f"📌 <b>Razón:</b> {reason}\n"
        f"💵 <b>Entrada:</b> <code>{t.entry_price:.6g}</code>\n"
        f"💵 <b>Salida:</b>  <code>{exit_p:.6g}</code>\n"
        f"{'💰' if pnl > 0 else '💸'} <b>PnL:</b> ${pnl:+.2f}\n"
        f"══════════════════════════════\n"
        f"📊 <b>W/L:</b> {state.wins}W / {state.losses}L | "
        f"<b>WR:</b> {state.win_rate():.1f}% | "
        f"<b>PF:</b> {state.profit_factor():.2f} | "
        f"<b>PnL total:</b> ${state.total_pnl:+.2f}\n"
        f"⏰ {utcnow()}"
    )


def tg_scan_summary(signals: List[dict], scanned: int, session: bool):
    """Resumen periódico del escaneo de todos los pares."""
    cb_str   = f"⛔ ACTIVO -{CB_DD}%" if state.cb_active() else "✅ OK"
    open_str = "\n".join(
        f"  • {sym} {ts.side.upper()} score={ts.entry_score}/8"
        for sym, ts in state.trades.items()
    ) or "  (ninguna)"

    top_sigs = signals[:5]  # Top 5 señales del ciclo
    sigs_str = "\n".join(
        f"  {'🟢' if s['side']=='long' else '🔴'} {s['symbol']} "
        f"{s['score']}/8 {'↑' if s['side']=='long' else '↓'}"
        for s in top_sigs
    ) or "  (ninguna este ciclo)"

    tg(
        f"📡 <b>SCAN MULTISIMBOLO</b>\n"
        f"══════════════════════════════\n"
        f"🔍 <b>Escaneados:</b> {scanned} pares\n"
        f"⚡ <b>Señales encontradas:</b> {len(signals)}\n"
        f"🏆 <b>Top señales:</b>\n{sigs_str}\n"
        f"══════════════════════════════\n"
        f"📂 <b>Posiciones abiertas ({state.open_count()}/{MAX_OPEN_TRADES}):</b>\n{open_str}\n"
        f"══════════════════════════════\n"
        f"CB: {cb_str} | Racha perdidas: {state.consec_losses}\n"
        f"Sesión: {'ACTIVA' if session else 'CERRADA'}\n"
        f"⏰ {utcnow()}"
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
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()

def calc_rsi(s: pd.Series, n: int) -> pd.Series:
    d  = s.diff()
    g  = d.clip(lower=0).ewm(span=n, adjust=False).mean()
    lo = (-d.clip(upper=0)).ewm(span=n, adjust=False).mean()
    return 100 - (100 / (1 + g / lo.replace(0, np.nan)))

def calc_adx(df: pd.DataFrame, n: int):
    h, l  = df["high"], df["low"]
    up    = h.diff()
    dn    = -l.diff()
    pdm   = up.where((up > dn) & (up > 0), 0.0)
    mdm   = dn.where((dn > up) & (dn > 0), 0.0)
    atr   = calc_atr(df, n)
    dip   = 100 * pdm.ewm(span=n, adjust=False).mean() / atr
    dim   = 100 * mdm.ewm(span=n, adjust=False).mean() / atr
    dx    = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    return dip, dim, dx.ewm(span=n, adjust=False).mean()


def compute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l, v, o = df["close"], df["high"], df["low"], df["volume"], df["open"]

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

    df["osc_raw"]  = ((c - df["ema21"]) / (3.0 * df["atr"])) * 100
    df["osc"]      = ema(df["osc_raw"], OSC_LEN)
    df["osc_up"]   = (df["osc"] > 0) & (df["osc"].shift() <= 0)
    df["osc_dn"]   = (df["osc"] < 0) & (df["osc"].shift() >= 0)

    bb_std         = c.rolling(PIVOT_LEN).std()
    bb_up          = df["ema21"] + 2.0 * bb_std
    kc_up          = df["ema21"] + 2.0 * df["atr"]
    df["squeeze"]  = bb_up < kc_up

    bb_lo          = df["ema21"] - 2.0 * bb_std
    bb_width       = (bb_up - bb_lo) / df["ema21"]
    bb_width_ma    = sma(bb_width, 20)
    df["is_trending"] = (adx > ADX_MIN) & (bb_width > bb_width_ma * 0.9)

    rng              = (h - l).replace(0, np.nan)
    df["buy_vol"]    = v * (c - l) / rng
    df["sell_vol"]   = v * (h - c) / rng
    df["vol_ma"]     = sma(v, VOL_LEN)
    df["vol_spike"]  = v > df["vol_ma"] * 1.1
    df["vol_bull"]   = df["buy_vol"] > df["sell_vol"]
    df["vol_bear"]   = df["sell_vol"] > df["buy_vol"]

    body              = (c - o).abs()
    body_pct          = body / rng.replace(0, np.nan)
    df["bull_candle"] = (c > o) & (body_pct >= 0.4)
    df["bear_candle"] = (c < o) & (body_pct >= 0.4)

    df["swing_low"]   = l.rolling(SWING_LB).min()
    df["swing_high"]  = h.rolling(SWING_LB).max()

    df["price_ll"] = (l < l.shift())  & (l.shift()  < l.shift(2))
    df["rsi_hl"]   = (df["rsi"] > df["rsi"].shift()) & (df["rsi"].shift() > df["rsi"].shift(2))
    df["bull_div"] = df["price_ll"] & df["rsi_hl"] & (df["rsi"] < 45)

    df["price_hh"] = (h > h.shift())  & (h.shift()  > h.shift(2))
    df["rsi_lh"]   = (df["rsi"] < df["rsi"].shift()) & (df["rsi"].shift() < df["rsi"].shift(2))
    df["bear_div"] = df["price_hh"] & df["rsi_lh"] & (df["rsi"] > 55)

    return df


def htf_bias(df: pd.DataFrame) -> tuple:
    """Returns (is_bull, is_bear)."""
    df  = compute(df)
    row = df.iloc[-2]
    bull = bool(row["close"] > row["ema48"] and row["ema21"] > row["ema48"])
    bear = bool(row["close"] < row["ema48"] and row["ema21"] < row["ema48"])
    return bull, bear


def htf2_macro(df: pd.DataFrame) -> tuple:
    """1h macro: price > ema48 AND ema48 > ema200."""
    df  = compute(df)
    row = df.iloc[-2]
    bull = bool(row["close"] > row["ema48"] and row["ema48"] > row["ema200"])
    bear = bool(row["close"] < row["ema48"] and row["ema48"] < row["ema200"])
    return bull, bear


# ══════════════════════════════════════════════════════════
# CONFLUENCE SCORE
# ══════════════════════════════════════════════════════════
def confluence_score(row: pd.Series,
                     htf1_bull: bool, htf1_bear: bool,
                     htf2_bull: bool, htf2_bear: bool) -> tuple:
    """Returns (long_score, short_score) 0-8."""
    l1 = bool(row["close"] > row["ema48"] and row["ema8"] > row["ema21"])
    l2 = bool(row["osc_up"])
    l3 = htf1_bull
    l4 = htf2_bull
    l5 = bool(row["adx"] > ADX_MIN and row["dip"] > row["dim"])
    l6 = bool(45 <= row["rsi"] <= 75)
    l7 = bool(row["vol_bull"] and row["vol_spike"] and not row["squeeze"])
    l8 = bool(row["bull_candle"] and row["close"] > row["ema21"])

    s1 = bool(row["close"] < row["ema48"] and row["ema8"] < row["ema21"])
    s2 = bool(row["osc_dn"])
    s3 = htf1_bear
    s4 = htf2_bear
    s5 = bool(row["adx"] > ADX_MIN and row["dim"] > row["dip"])
    s6 = bool(25 <= row["rsi"] <= 55)
    s7 = bool(row["vol_bear"] and row["vol_spike"] and not row["squeeze"])
    s8 = bool(row["bear_candle"] and row["close"] < row["ema21"])

    return sum([l1, l2, l3, l4, l5, l6, l7, l8]), sum([s1, s2, s3, s4, s5, s6, s7, s8])


# ══════════════════════════════════════════════════════════
# SESSION CHECK
# ══════════════════════════════════════════════════════════
def in_session() -> bool:
    if not USE_SESSION:
        return True
    h = datetime.now(timezone.utc).hour
    return (LONDON_OPEN <= h < LONDON_CLOSE) or (NY_OPEN <= h < NY_CLOSE)


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


def get_all_positions(ex: ccxt.Exchange) -> Dict[str, dict]:
    """Retorna un dict symbol->position de todas las posiciones abiertas."""
    result = {}
    try:
        for p in ex.fetch_positions():
            if abs(float(p.get("contracts", 0) or 0)) > 0:
                result[p["symbol"]] = p
    except Exception as e:
        log.warning(f"fetch_positions (all) error: {e}")
    return result


def get_last_price(ex: ccxt.Exchange, symbol: str) -> float:
    return float(ex.fetch_ticker(symbol)["last"])


# ══════════════════════════════════════════════════════════
# UNIVERSE: OBTENER TODOS LOS PARES DE BINGX
# ══════════════════════════════════════════════════════════
def get_tradeable_symbols(ex: ccxt.Exchange) -> List[str]:
    """
    Retorna la lista de pares USDT perpetuos de BingX ordenados por
    volumen 24h, filtrados por MIN_VOLUME_USDT y SYMBOL_FILTER.
    """
    symbols = []
    for sym, mkt in ex.markets.items():
        # Solo perpetuos USDT
        if not (mkt.get("swap") and mkt.get("quote") == "USDT" and mkt.get("active", True)):
            continue
        # Lista negra
        if sym in BLACKLIST:
            continue
        # Filtro de base opcional
        if SYMBOL_FILTER and mkt.get("base", "") not in SYMBOL_FILTER:
            continue
        symbols.append(sym)

    if not symbols:
        log.warning("No symbols found after filters.")
        return []

    # Filtrar por volumen usando tickers
    log.info(f"Obteniendo tickers para {len(symbols)} pares...")
    try:
        tickers = ex.fetch_tickers(symbols)
    except Exception as e:
        log.warning(f"fetch_tickers error: {e} — usando lista sin filtro de volumen")
        return symbols[:TOP_N_SYMBOLS] if TOP_N_SYMBOLS > 0 else symbols

    ranked = []
    for sym in symbols:
        tk = tickers.get(sym, {})
        vol_usdt = float(tk.get("quoteVolume", 0) or 0)
        if vol_usdt >= MIN_VOLUME_USDT:
            ranked.append((sym, vol_usdt))

    ranked.sort(key=lambda x: x[1], reverse=True)
    log.info(f"Pares con volumen > ${MIN_VOLUME_USDT/1e6:.1f}M: {len(ranked)}")

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
    """Abre posicion en el simbolo dado. Retorna TradeState o None si falla."""
    try:
        balance  = get_balance(ex)
        mult     = state.risk_mult(score)
        risk_pct = BASE_RISK * mult
        usdt_qty = balance * (risk_pct / 100)

        price  = get_last_price(ex, symbol)
        amount = float(ex.amount_to_precision(symbol, usdt_qty / price))

        log.info(f"[ENTRY] {symbol} {side.upper()} score={score}/8 "
                 f"risk={risk_pct:.1f}% size={amount} @ {price:.6g}")

        order       = ex.create_order(symbol, "market", side, amount)
        entry_price = float(order.get("average") or price)

        atr = row["atr"]

        if USE_SWING_SL:
            if side == "buy":
                sl_p = min(row["swing_low"] - atr * 0.2, entry_price - atr * SL_ATR)
            else:
                sl_p = max(row["swing_high"] + atr * 0.2, entry_price + atr * SL_ATR)
        else:
            sl_p = (entry_price - atr * SL_ATR if side == "buy"
                    else entry_price + atr * SL_ATR)

        if side == "buy":
            tp1_p = entry_price + atr * TP1_MULT
            tp2_p = entry_price + atr * TP2_MULT
        else:
            tp1_p = entry_price - atr * TP1_MULT
            tp2_p = entry_price - atr * TP2_MULT

        tp1_p = float(ex.price_to_precision(symbol, tp1_p))
        tp2_p = float(ex.price_to_precision(symbol, tp2_p))
        sl_p  = float(ex.price_to_precision(symbol, sl_p))

        close_side = "sell" if side == "buy" else "buy"
        half_amt   = float(ex.amount_to_precision(symbol, amount * 0.5))

        try:
            ex.create_order(symbol, "limit", close_side, half_amt, tp1_p, {"reduceOnly": True})
        except Exception as e:
            log.warning(f"[{symbol}] TP1 order failed: {e}")

        try:
            ex.create_order(symbol, "limit", close_side, half_amt, tp2_p, {"reduceOnly": True})
        except Exception as e:
            log.warning(f"[{symbol}] TP2 order failed: {e}")

        try:
            ex.create_order(symbol, "stop_market", close_side, amount, None,
                            {"stopPrice": sl_p, "reduceOnly": True})
        except Exception as e:
            log.warning(f"[{symbol}] SL order failed: {e}")

        t = TradeState(
            symbol      = symbol,
            side        = "long" if side == "buy" else "short",
            entry_price = entry_price,
            tp1_price   = tp1_p,
            tp2_price   = tp2_p,
            sl_price    = sl_p,
            tp1_hit     = False,
            entry_score = score,
            entry_time  = utcnow(),
        )
        if side == "buy":
            t.trail_high = entry_price
        else:
            t.trail_low  = entry_price

        tg_signal(t, risk_pct, row)
        return t

    except Exception as e:
        log.error(f"[{symbol}] open_trade error: {e}")
        tg_error(f"open_trade {symbol}: {e}")
        return None


def close_trade(ex: ccxt.Exchange, symbol: str, reason: str, current_price: float):
    """Cierra la posicion en symbol y actualiza estadísticas globales."""
    if symbol not in state.trades:
        return
    t = state.trades[symbol]

    try:
        ex.cancel_all_orders(symbol)
    except Exception as e:
        log.warning(f"[{symbol}] cancel_all_orders: {e}")

    pos = get_position(ex, symbol)
    pnl = 0.0

    if pos:
        contracts  = abs(float(pos.get("contracts", 0)))
        close_side = "sell" if t.side == "long" else "buy"
        try:
            ex.create_order(symbol, "market", close_side, contracts,
                            params={"reduceOnly": True})
            if t.side == "long":
                pnl = (current_price - t.entry_price) * contracts
            else:
                pnl = (t.entry_price - current_price) * contracts
            log.info(f"[{symbol}] CLOSE {reason} pnl={pnl:+.2f}")
        except Exception as e:
            log.error(f"[{symbol}] close market order failed: {e}")
            tg_error(f"close_trade {symbol}: {e}")

    # Actualizar estadísticas
    if pnl > 0:
        state.wins         += 1
        state.gross_profit += pnl
        state.consec_losses = 0
    elif pnl < 0:
        state.losses        += 1
        state.gross_loss    += abs(pnl)
        state.consec_losses += 1

    state.total_pnl   += pnl
    state.peak_equity  = max(state.peak_equity, state.peak_equity + pnl)

    tg_close(reason, t, current_price, pnl)
    del state.trades[symbol]


# ══════════════════════════════════════════════════════════
# TRAILING STOP POST TP1
# ══════════════════════════════════════════════════════════
def manage_open_trade(ex: ccxt.Exchange, symbol: str,
                      live_price: float, atr: float,
                      long_score: int, short_score: int,
                      live_pos: Optional[dict]):
    """Gestiona TP1, trailing y flip para un trade abierto."""
    if symbol not in state.trades:
        return
    t = state.trades[symbol]

    # Posicion cerrada externamente (TP o SL del exchange)
    if live_pos is None:
        log.info(f"[{symbol}] Posicion cerrada externamente (TP/SL)")
        pnl_est = 0.0
        if t.side == "long":
            pnl_est = live_price - t.entry_price
            reason  = "TP ALCANZADO" if live_price >= t.tp2_price else "SL ALCANZADO"
        else:
            pnl_est = t.entry_price - live_price
            reason  = "TP ALCANZADO" if live_price <= t.tp2_price else "SL ALCANZADO"

        if pnl_est > 0:
            state.wins         += 1
            state.gross_profit += abs(pnl_est)
            state.consec_losses = 0
        else:
            state.losses        += 1
            state.gross_loss    += abs(pnl_est)
            state.consec_losses += 1

        state.total_pnl += pnl_est
        tg_close(reason, t, live_price, pnl_est)
        del state.trades[symbol]
        return

    # Detectar TP1 hit
    if not t.tp1_hit:
        tp1_hit = (t.side == "long"  and live_price >= t.tp1_price) or \
                  (t.side == "short" and live_price <= t.tp1_price)
        if tp1_hit:
            t.tp1_hit = True
            contracts = float(live_pos.get("contracts", 0))
            pnl_est   = abs(t.tp1_price - t.entry_price) * contracts * 0.5
            log.info(f"[{symbol}] TP1 HIT @ {live_price:.6g} — activando trailing")
            tg_tp1(t, live_price, pnl_est)

    # Trailing stop post TP1
    if t.tp1_hit:
        if t.side == "long":
            if live_price > t.trail_high:
                t.trail_high = live_price
            stop = t.trail_high - atr * TRAIL_MULT
            if live_price < stop:
                log.info(f"[{symbol}] TRAILING STOP long @ {live_price:.6g}")
                close_trade(ex, symbol, "TRAILING STOP", live_price)
                return
        else:
            if live_price < t.trail_low:
                t.trail_low = live_price
            stop = t.trail_low + atr * TRAIL_MULT
            if live_price > stop:
                log.info(f"[{symbol}] TRAILING STOP short @ {live_price:.6g}")
                close_trade(ex, symbol, "TRAILING STOP", live_price)
                return

    # Signal flip
    if symbol in state.trades:  # puede haber sido cerrado arriba
        if t.side == "long" and short_score >= MIN_SCORE:
            log.info(f"[{symbol}] FLIP: cerrando long por señal short fuerte")
            close_trade(ex, symbol, "FLIP LONG→SHORT", live_price)
        elif t.side == "short" and long_score >= MIN_SCORE:
            log.info(f"[{symbol}] FLIP: cerrando short por señal long fuerte")
            close_trade(ex, symbol, "FLIP SHORT→LONG", live_price)


# ══════════════════════════════════════════════════════════
# SCAN DE UN SIMBOLO (usado en paralelo)
# ══════════════════════════════════════════════════════════
def scan_symbol(ex: ccxt.Exchange, symbol: str) -> Optional[dict]:
    """
    Analiza un símbolo y retorna un dict con señal si la hay, o None.
    dict: {"symbol", "side", "score", "row", "atr"}
    """
    try:
        df  = fetch_df(ex, symbol, TF,   limit=350)
        df1 = fetch_df(ex, symbol, HTF1, limit=150)
        df2 = fetch_df(ex, symbol, HTF2, limit=300)

        df  = compute(df)
        row = df.iloc[-2]

        if pd.isna(row["adx"]) or pd.isna(row["rsi"]):
            return None

        htf1_bull, htf1_bear = htf_bias(df1)
        htf2_bull, htf2_bear = htf2_macro(df2)

        long_score, short_score = confluence_score(
            row, htf1_bull, htf1_bear, htf2_bull, htf2_bear
        )

        atr       = row["atr"]
        live_price = float(row["close"])

        return {
            "symbol":      symbol,
            "long_score":  long_score,
            "short_score": short_score,
            "row":         row,
            "atr":         atr,
            "live_price":  live_price,
            "is_trending": bool(row["is_trending"]),
        }
    except Exception as e:
        log.debug(f"[{symbol}] scan error: {e}")
        return None


# ══════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════
def main():
    log.info("=" * 65)
    log.info("  SATY ELITE v8 MULTI-SYMBOL — CONFLUENCE ENGINE STARTING")
    log.info("=" * 65)

    dry_run = not (API_KEY and API_SECRET)
    if dry_run:
        log.warning("DRY-RUN: sin API keys. "
                    "Configura BINGX_API_KEY y BINGX_API_SECRET.")
        while True:
            log.info("DRY-RUN mode activo. Esperando...")
            time.sleep(POLL_SECS)

    ex = build_exchange()
    log.info(f"Conectado a BingX | TF:{TF} | HTF1:{HTF1} | HTF2:{HTF2}")

    balance = get_balance(ex)
    state.peak_equity = balance
    log.info(f"Balance: ${balance:.2f} USDT")

    # Obtener universo de símbolos
    symbols = get_tradeable_symbols(ex)
    if not symbols:
        log.error("No hay símbolos válidos para escanear. Abortando.")
        return

    tg_startup(balance, len(symbols))

    scan_count   = 0
    # Refrescar universo cada N ciclos (aprox. cada hora)
    REFRESH_EVERY = max(1, int(3600 / max(POLL_SECS, 1)))

    while True:
        try:
            ts_start = time.time()
            now_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            scan_count += 1
            log.info(f"━━━ [{now_str}] SCAN #{scan_count} — {len(symbols)} pares ━━━")

            # Refrescar universo periódicamente
            if scan_count % REFRESH_EVERY == 0:
                log.info("Refrescando universo de símbolos...")
                try:
                    ex.load_markets()
                    symbols = get_tradeable_symbols(ex)
                except Exception as e:
                    log.warning(f"Error refrescando universo: {e}")

            # ── Circuit breaker global ──
            if state.cb_active():
                log.warning(f"CIRCUIT BREAKER ACTIVO — drawdown >= {CB_DD}% — sin nuevas entradas")
                time.sleep(POLL_SECS)
                continue

            # ── Obtener todas las posiciones abiertas del exchange ──
            live_positions = get_all_positions(ex)

            # ── Gestionar trades abiertos ──
            for sym in list(state.trades.keys()):
                try:
                    live_pos   = live_positions.get(sym)
                    live_price = (float(live_pos["markPrice"])
                                  if live_pos else get_last_price(ex, sym))

                    # Re-calcular scores básicos para flip detection
                    result = scan_symbol(ex, sym)
                    ls = result["long_score"]  if result else 0
                    ss = result["short_score"] if result else 0
                    atr = result["atr"]        if result else state.trades[sym].entry_price * 0.001

                    manage_open_trade(ex, sym, live_price, atr, ls, ss, live_pos)
                except Exception as e:
                    log.warning(f"[{sym}] manage_open_trade error: {e}")

            # ── Escanear nuevas señales (solo si hay cupo) ──
            new_signals: List[dict] = []

            if state.open_count() < MAX_OPEN_TRADES and in_session():
                # Escanear en paralelo para mayor velocidad
                symbols_to_scan = [s for s in symbols if s not in state.trades]

                with ThreadPoolExecutor(max_workers=5) as pool:
                    futures = {pool.submit(scan_symbol, ex, sym): sym
                               for sym in symbols_to_scan}
                    results = []
                    for fut in as_completed(futures):
                        res = fut.result()
                        if res:
                            results.append(res)

                # Ordenar por score descendente
                for res in results:
                    best_side  = None
                    best_score = 0
                    if res["long_score"] >= MIN_SCORE and res["is_trending"]:
                        if res["long_score"] > best_score:
                            best_score = res["long_score"]
                            best_side  = "long"
                    if res["short_score"] >= MIN_SCORE and res["is_trending"]:
                        if res["short_score"] > best_score:
                            best_score = res["short_score"]
                            best_side  = "short"
                    if best_side:
                        new_signals.append({
                            "symbol": res["symbol"],
                            "side":   best_side,
                            "score":  best_score,
                            "row":    res["row"],
                        })

                # Ordenar por score
                new_signals.sort(key=lambda x: x["score"], reverse=True)

                # Abrir trades para las mejores señales hasta el máximo permitido
                for sig in new_signals:
                    if state.open_count() >= MAX_OPEN_TRADES:
                        break
                    sym   = sig["symbol"]
                    side  = "buy" if sig["side"] == "long" else "sell"
                    score = sig["score"]
                    row   = sig["row"]

                    log.info(f"*** SEÑAL {sig['side'].upper()} *** {sym} score={score}/8")
                    t = open_trade(ex, sym, side, score, row)
                    if t:
                        state.trades[sym] = t

            elif not in_session():
                log.info("Fuera de sesión de trading — sin nuevas entradas")
            elif state.open_count() >= MAX_OPEN_TRADES:
                log.info(f"Máximo de trades abiertos ({MAX_OPEN_TRADES}) alcanzado")

            # ── Log resumen ──
            elapsed = time.time() - ts_start
            log.info(
                f"Ciclo completado en {elapsed:.1f}s | "
                f"Trades abiertos: {state.open_count()}/{MAX_OPEN_TRADES} | "
                f"Señales: {len(new_signals)} | "
                f"W:{state.wins} L:{state.losses} PnL:${state.total_pnl:+.2f}"
            )

            # ── Resumen Telegram cada 15 ciclos ──
            if scan_count % 15 == 0:
                tg_scan_summary(new_signals, len(symbols), in_session())

        except ccxt.NetworkError as e:
            log.warning(f"Network error: {e} — reintentando...")
        except ccxt.ExchangeError as e:
            log.error(f"Exchange error: {e}")
            tg(f"❌ <b>Exchange error:</b> <code>{str(e)[:200]}</code>")
        except KeyboardInterrupt:
            log.info("Bot detenido por el usuario.")
            tg("🛑 <b>Bot detenido manualmente.</b>")
            break
        except Exception as e:
            log.exception(f"Error inesperado: {e}")
            tg_error(str(e))

        time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()
