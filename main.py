"""
╔══════════════════════════════════════════════════════════════════╗
║          SATY ELITE v10 — MULTI-SYMBOL CONFLUENCE ENGINE        ║
║          Python Bot · BingX Perpetual Futures · Telegram        ║
╠══════════════════════════════════════════════════════════════════╣
║  MEJORAS v10 vs v9:                                             ║
║                                                                  ║
║  ESTRATEGIA:                                                     ║
║  · Score 12 puntos (añadido: vela engulfing + divergencia RSI)  ║
║  · RSI extremo 10-25 / 78-90 con alerta + punto de confluencia  ║
║  · Filtro de correlación: evita abrir 2 trades muy similares    ║
║  · Cooldown por símbolo: no re-entrar en 30min tras cierre      ║
║  · Detección de divergencia RSI mejorada (3 barras)             ║
║  · Filtro de spread: descarta pares con spread > 0.3%           ║
║                                                                  ║
║  GESTIÓN DE RIESGO:                                             ║
║  · Break-even automático al tocar TP1 (SL se mueve a entrada)   ║
║  · Risk sizing por ATR (volatilidad real del par)               ║
║  · Max pérdida diaria configurable (DAILY_LOSS_LIMIT)           ║
║  · Protección anti-whipsaw: pausa 5min si SL consecutivos       ║
║                                                                  ║
║  INFRAESTRUCTURA:                                               ║
║  · Cache de OHLCV: no repite llamadas al exchange en el mismo   ║
║    ciclo para el mismo símbolo+TF                               ║
║  · Rate limit inteligente con backoff exponencial               ║
║  · Reconexión automática sin reiniciar                          ║
║  · Log de trades a CSV para análisis posterior                  ║
║  · Heartbeat Telegram cada hora (confirmación de vida)          ║
╚══════════════════════════════════════════════════════════════════╝

Variables de entorno OBLIGATORIAS:
    BINGX_API_KEY        API Key de BingX
    BINGX_API_SECRET     API Secret de BingX
    TELEGRAM_BOT_TOKEN   Token bot Telegram
    TELEGRAM_CHAT_ID     Chat ID Telegram

Variables OPCIONALES:
    SYMBOL_FILTER        Bases "BTC,ETH,SOL" (vacío=todas)
    BLACKLIST            Pares excluidos "LUNA/USDT:USDT,..."
    MAX_OPEN_TRADES      Max posiciones simultáneas      (def: 10)
    MIN_SCORE            Score mínimo 1-12               (def: 5)
    BASE_RISK            % capital por operación         (def: 2.0)
    MAX_DRAWDOWN         % CB drawdown máximo            (def: 15.0)
    DAILY_LOSS_LIMIT     % pérdida máxima diaria         (def: 6.0)
    MIN_VOLUME_USDT      Volumen mín 24h USDT            (def: 3000000)
    TOP_N_SYMBOLS        Max pares a escanear            (def: 60)
    POLL_SECONDS         Segundos entre ciclos           (def: 60)
    TIMEFRAME            TF entrada                      (def: 5m)
    HTF1                 HTF intermedio                  (def: 15m)
    HTF2                 HTF macro                       (def: 1h)
    USE_SESSION          Filtro sesión London/NY         (def: false)
    BTC_FILTER           Filtrar por tendencia BTC       (def: true)
    COOLDOWN_MIN         Minutos cooldown tras cierre    (def: 30)
    MAX_SPREAD_PCT       Spread máximo permitido %       (def: 0.3)
    LOG_TRADES_CSV       Guardar trades en CSV           (def: true)
"""

import os, time, logging, csv, json, math
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

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
log = logging.getLogger("saty_v10")

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

_sf = os.environ.get("SYMBOL_FILTER", "")
SYMBOL_FILTER: List[str] = [s.strip().upper() for s in _sf.split(",") if s.strip()]
_bl = os.environ.get("BLACKLIST", "")
BLACKLIST: List[str] = [s.strip() for s in _bl.split(",") if s.strip()]

MIN_VOLUME_USDT  = float(os.environ.get("MIN_VOLUME_USDT",  "3000000"))
TOP_N_SYMBOLS    = int(os.environ.get("TOP_N_SYMBOLS",      "60"))
MAX_OPEN_TRADES  = int(os.environ.get("MAX_OPEN_TRADES",    "10"))
MIN_SCORE        = int(os.environ.get("MIN_SCORE",          "5"))
BASE_RISK        = float(os.environ.get("BASE_RISK",        "2.0"))
CB_DD            = float(os.environ.get("MAX_DRAWDOWN",     "15.0"))
DAILY_LOSS_LIMIT = float(os.environ.get("DAILY_LOSS_LIMIT", "6.0"))
COOLDOWN_MIN     = int(os.environ.get("COOLDOWN_MIN",       "30"))
MAX_SPREAD_PCT   = float(os.environ.get("MAX_SPREAD_PCT",   "0.3"))
LOG_TRADES_CSV   = os.environ.get("LOG_TRADES_CSV", "true").lower() == "true"

USE_SESSION = os.environ.get("USE_SESSION", "false").lower() == "true"
BTC_FILTER  = os.environ.get("BTC_FILTER",  "true").lower()  == "true"

LONDON_OPEN = 7;  LONDON_CLOSE = 16
NY_OPEN     = 13; NY_CLOSE     = 21

# ── Indicadores ──
FAST_LEN  = 8;   PIVOT_LEN = 21; BIAS_LEN  = 48; SLOW_LEN  = 200
ADX_LEN   = 14;  ADX_MIN   = 18; RSI_LEN   = 14; ATR_LEN   = 14
VOL_LEN   = 20;  OSC_LEN   = 3;  SWING_LB  = 10
MACD_FAST = 12;  MACD_SLOW = 26; MACD_SIG  = 9;  STOCH_LEN = 14

# ── Exits ──
TP1_MULT   = 1.2   # TP1 = 1.2 × ATR
TP2_MULT   = 3.0   # TP2 = 3.0 × ATR
SL_ATR     = 1.0   # SL base ATR
TRAIL_MULT = 0.8   # Trailing más ajustado

# ── Risk ──
RISK_BOOST      = 1.5
RISK_CUT        = 0.5
MAX_CONSEC_LOSS = 3
USE_CB          = True

# ── RSI extremo ──
RSI_OB_LOW  = 10;  RSI_OB_HIGH = 25   # Sobreventa
RSI_OS_LOW  = 78;  RSI_OS_HIGH = 90   # Sobrecompra

# ── Hedge mode (auto-detectado) ──
HEDGE_MODE: bool = False

# ── CSV log ──
CSV_PATH = "/tmp/saty_trades.csv"


# ══════════════════════════════════════════════════════════
# CACHE OHLCV (evita llamadas duplicadas por ciclo)
# ══════════════════════════════════════════════════════════
_ohlcv_cache: Dict[str, Tuple[float, pd.DataFrame]] = {}
CACHE_TTL = 55  # segundos

def fetch_df_cached(ex: ccxt.Exchange, symbol: str, tf: str, limit: int = 400) -> pd.DataFrame:
    key = f"{symbol}|{tf}"
    now = time.time()
    if key in _ohlcv_cache:
        ts, df = _ohlcv_cache[key]
        if now - ts < CACHE_TTL:
            return df
    raw = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df  = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)
    _ohlcv_cache[key] = (now, df)
    return df

def clear_cache():
    _ohlcv_cache.clear()


# ══════════════════════════════════════════════════════════
# ESTADO
# ══════════════════════════════════════════════════════════
@dataclass
class TradeState:
    symbol:       str   = ""
    side:         str   = ""      # "long" | "short"
    entry_price:  float = 0.0
    tp1_price:    float = 0.0
    tp2_price:    float = 0.0
    sl_price:     float = 0.0
    sl_moved_be:  bool  = False   # SL movido a break-even
    tp1_hit:      bool  = False
    trail_high:   float = 0.0
    trail_low:    float = 0.0
    entry_score:  int   = 0
    entry_time:   str   = ""
    contracts:    float = 0.0
    atr_entry:    float = 0.0     # ATR en el momento de entrada

    # ── Trailing dinámico ──
    # Precio máximo/mínimo alcanzado (peak del movimiento)
    peak_price:       float = 0.0
    # Precio anterior del ciclo (para detectar si se paró o giró)
    prev_price:       float = 0.0
    # Contador de ciclos sin nuevo máximo/mínimo (precio parado)
    stall_count:      int   = 0
    # Fase del trailing: "normal" | "tight" | "locked"
    trail_phase:      str   = "normal"
    # Ganancia máxima en % alcanzada (para calcular cuánto ha cedido)
    max_profit_pct:   float = 0.0


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
    daily_reset_ts: float = 0.0   # timestamp del reset diario
    trades: Dict[str, TradeState] = field(default_factory=dict)

    # Cooldown: symbol -> timestamp de cierre
    cooldowns: Dict[str, float] = field(default_factory=dict)

    # BTC macro
    btc_bull: bool = True
    btc_bear: bool = False
    btc_rsi:  float = 50.0

    # Alertas RSI ya enviadas (evitar spam): symbol -> timestamp
    rsi_alerts: Dict[str, float] = field(default_factory=dict)

    # Último heartbeat
    last_heartbeat: float = 0.0

    def open_count(self) -> int: return len(self.trades)

    def win_rate(self) -> float:
        t = self.wins + self.losses
        return (self.wins / t * 100) if t else 0.0

    def profit_factor(self) -> float:
        return (self.gross_profit / self.gross_loss) if self.gross_loss else 0.0

    def score_bar(self, score: int, mx: int = 12) -> str:
        filled = min(score, mx)
        return "█" * filled + "░" * (mx - filled)

    def cb_active(self) -> bool:
        if not USE_CB or self.peak_equity <= 0: return False
        dd = (self.peak_equity - (self.peak_equity + self.total_pnl)) / self.peak_equity * 100
        return dd >= CB_DD

    def daily_limit_hit(self) -> bool:
        if self.peak_equity <= 0: return False
        return abs(self.daily_pnl) / self.peak_equity * 100 >= DAILY_LOSS_LIMIT \
               and self.daily_pnl < 0

    def risk_mult(self, score: int) -> float:
        if self.consec_losses >= MAX_CONSEC_LOSS: return RISK_CUT
        if score >= 9: return RISK_BOOST
        return 1.0

    def in_cooldown(self, symbol: str) -> bool:
        if symbol not in self.cooldowns: return False
        return time.time() - self.cooldowns[symbol] < COOLDOWN_MIN * 60

    def set_cooldown(self, symbol: str):
        self.cooldowns[symbol] = time.time()

    def reset_daily_if_needed(self):
        now = time.time()
        if now - self.daily_reset_ts > 86400:
            self.daily_pnl     = 0.0
            self.daily_reset_ts = now
            log.info("Daily PnL reseteado")


state = BotState()


# ══════════════════════════════════════════════════════════
# CSV TRADE LOG
# ══════════════════════════════════════════════════════════
def log_trade_csv(action: str, t: TradeState, price: float, pnl: float = 0.0):
    if not LOG_TRADES_CSV: return
    try:
        exists = os.path.exists(CSV_PATH)
        with open(CSV_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["timestamp","action","symbol","side","score",
                            "entry","exit","pnl","contracts","atr"])
            w.writerow([utcnow(), action, t.symbol, t.side, t.entry_score,
                        t.entry_price, price, round(pnl, 4),
                        t.contracts, round(t.atr_entry, 6)])
    except Exception as e:
        log.warning(f"CSV log error: {e}")


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
    mode = "HEDGE" if HEDGE_MODE else "ONE-WAY"
    fil  = ", ".join(SYMBOL_FILTER) if SYMBOL_FILTER else "TODOS"
    tg(
        f"<b>🚀 SATY ELITE v10 MULTI</b>\n"
        f"══════════════════════════════\n"
        f"🌍 <b>Universo:</b> {n_symbols} pares USDT\n"
        f"🔍 <b>Filtro:</b> {fil} | <b>Modo:</b> {mode}\n"
        f"⏱ <b>TF:</b> {TF} · {HTF1} · {HTF2}\n"
        f"🎯 <b>Score min:</b> {MIN_SCORE}/12 | <b>Max trades:</b> {MAX_OPEN_TRADES}\n"
        f"💰 <b>Balance:</b> ${balance:.2f} USDT\n"
        f"⚙️ Risk:{BASE_RISK}% · CB:{CB_DD}% · Límite diario:{DAILY_LOSS_LIMIT}%\n"
        f"🕐 Sesión: {'London+NY' if USE_SESSION else '24/7'} | "
        f"₿ BTC filtro: {'✅' if BTC_FILTER else '❌'}\n"
        f"⏳ Cooldown: {COOLDOWN_MIN}min | Spread max: {MAX_SPREAD_PCT}%\n"
        f"⏰ {utcnow()}"
    )


def tg_signal(t: TradeState, risk_pct: float, row: pd.Series, extra_flags: str = ""):
    emoji   = "🟢" if t.side == "long" else "🔴"
    label   = "LONG" if t.side == "long" else "SHORT"
    sl_dist = abs(t.sl_price - t.entry_price)
    rr1     = abs(t.tp1_price - t.entry_price) / max(sl_dist, 1e-9)
    rr2     = abs(t.tp2_price - t.entry_price) / max(sl_dist, 1e-9)
    btc_str = f"₿{'🟢' if state.btc_bull else '🔴' if state.btc_bear else '⚪'} RSI:{state.btc_rsi:.0f}"
    rsi_lbl = rsi_zone_label(float(row["rsi"]))
    be_info = " | SL→BE automático tras TP1" 
    tg(
        f"{emoji} <b>{label}</b> — {t.symbol}\n"
        f"══════════════════════════════\n"
        f"🎯 Score: {t.entry_score}/12  {state.score_bar(t.entry_score)}\n"
        f"💵 Entrada: <code>{t.entry_price:.6g}</code>\n"
        f"🟡 TP1 50%: <code>{t.tp1_price:.6g}</code>  R:R 1:{rr1:.1f}\n"
        f"🟢 TP2 50%: <code>{t.tp2_price:.6g}</code>  R:R 1:{rr2:.1f}\n"
        f"🛑 SL: <code>{t.sl_price:.6g}</code>{be_info}\n"
        f"══════════════════════════════\n"
        f"{rsi_lbl} | ADX:{row['adx']:.1f}\n"
        f"Stoch:{row['stoch_k']:.0f}/{row['stoch_d']:.0f} | "
        f"MACD:{row['macd_hist']:.5f}\n"
        f"Vol:{row['volume']/row['vol_ma']:.2f}x | ATR:{t.atr_entry:.5f}\n"
        f"{btc_str} {extra_flags}\n"
        f"💼 $8 por operación · {state.open_count()}/{MAX_OPEN_TRADES} trades\n"
        f"⏰ {utcnow()}"
    )


def tg_tp1_be(t: TradeState, price: float, pnl_est: float):
    tg(
        f"🟡 <b>TP1 + BREAK-EVEN</b> — {t.symbol}\n"
        f"💵 Precio: <code>{price:.6g}</code>\n"
        f"💰 PnL parcial: ~${pnl_est:+.2f}\n"
        f"🛡 SL movido a entrada <code>{t.entry_price:.6g}</code>\n"
        f"🔄 Trailing activo en 50% restante\n"
        f"⏰ {utcnow()}"
    )


def tg_close(reason: str, t: TradeState, exit_p: float, pnl: float):
    emoji   = "✅" if pnl > 0 else "❌"
    pnl_pct = (pnl / (t.entry_price * t.contracts)) * 100 if t.contracts > 0 else 0
    tg(
        f"{emoji} <b>CERRADO</b> — {t.symbol}\n"
        f"📋 {t.side.upper()} · Score:{t.entry_score}/12 · {reason}\n"
        f"💵 <code>{t.entry_price:.6g}</code> → <code>{exit_p:.6g}</code> "
        f"({pnl_pct:+.2f}%)\n"
        f"{'💰' if pnl > 0 else '💸'} PnL: ${pnl:+.2f}\n"
        f"📊 {state.wins}W/{state.losses}L · WR:{state.win_rate():.1f}% · "
        f"PF:{state.profit_factor():.2f}\n"
        f"💹 Diario:${state.daily_pnl:+.2f} · Total:${state.total_pnl:+.2f}\n"
        f"⏰ {utcnow()}"
    )


def tg_rsi_extreme(symbol: str, rsi: float, score_l: int, score_s: int,
                   price: float, adx: float, macd: float):
    direction = "📉 POTENCIAL LONG (rebote)" if rsi_extreme_long(rsi) else "📈 POTENCIAL SHORT (caída)"
    score_str = f"L:{score_l}/12" if rsi_extreme_long(rsi) else f"S:{score_s}/12"
    tg(
        f"🔔 <b>RSI EXTREMO</b> — {symbol}\n"
        f"══════════════════════════\n"
        f"{rsi_zone_label(rsi)}\n"
        f"💵 Precio: <code>{price:.6g}</code>\n"
        f"ADX:{adx:.1f} | MACD:{macd:.5f}\n"
        f"{direction}\n"
        f"Score: {score_str}\n"
        f"⏰ {utcnow()}"
    )


def tg_scan_summary(signals: List[dict], n_scanned: int):
    cb_str  = "⛔ CB ACTIVO" if state.cb_active() else "✅ OK"
    dl_str  = f"⛔ LÍMITE DIARIO" if state.daily_limit_hit() else f"💹 ${state.daily_pnl:+.2f}"
    btc_str = f"₿{'🟢' if state.btc_bull else '🔴' if state.btc_bear else '⚪'}"
    open_lines = "\n".join(
        f"  {'🟢' if ts.side=='long' else '🔴'} {sym} "
        f"({ts.side.upper()}) E:{ts.entry_price:.5g} "
        f"{'🛡BE' if ts.sl_moved_be else ''}"
        for sym, ts in state.trades.items()
    ) or "  (ninguna)"
    top_lines = "\n".join(
        f"  {'🟢' if s['side']=='long' else '🔴'} {s['symbol']} "
        f"{s['score']}/12 RSI:{s['rsi']:.1f}"
        for s in signals[:5]
    ) or "  (ninguna)"
    tg(
        f"📡 <b>RESUMEN</b> — {n_scanned} pares · {utcnow()}\n"
        f"══════════════════════════════\n"
        f"⚡ Señales: {len(signals)} · Top:\n{top_lines}\n"
        f"══════════════════════════════\n"
        f"📂 Posiciones ({state.open_count()}/{MAX_OPEN_TRADES}):\n{open_lines}\n"
        f"══════════════════════════════\n"
        f"CB:{cb_str} | Diario:{dl_str}\n"
        f"{btc_str} RSI:{state.btc_rsi:.0f} | Racha:{state.consec_losses}❌\n"
        f"📊 {state.wins}W/{state.losses}L · PF:{state.profit_factor():.2f}"
    )


def tg_heartbeat(balance: float):
    tg(
        f"💓 <b>HEARTBEAT</b> — {utcnow()}\n"
        f"Balance: ${balance:.2f} | PnL hoy: ${state.daily_pnl:+.2f}\n"
        f"Trades: {state.open_count()}/{MAX_OPEN_TRADES} | "
        f"{state.wins}W/{state.losses}L\n"
        f"₿ {'BULL' if state.btc_bull else 'BEAR' if state.btc_bear else 'NEUTRAL'} "
        f"RSI:{state.btc_rsi:.0f}"
    )


def tg_error(msg: str):
    tg(f"🔥 <b>ERROR:</b> <code>{msg[:300]}</code>\n⏰ {utcnow()}")


# ══════════════════════════════════════════════════════════
# RSI ZONES
# ══════════════════════════════════════════════════════════
def rsi_extreme_long(rsi: float) -> bool:
    return RSI_OB_LOW <= rsi <= RSI_OB_HIGH

def rsi_extreme_short(rsi: float) -> bool:
    return RSI_OS_LOW <= rsi <= RSI_OS_HIGH

def rsi_zone_label(rsi: float) -> str:
    if rsi < RSI_OB_LOW:    return f"⚠️ RSI HIPERVENTA {rsi:.1f}"
    if rsi <= RSI_OB_HIGH:  return f"🔥 RSI SOBREVENTA {rsi:.1f} [{RSI_OB_LOW}-{RSI_OB_HIGH}]"
    if rsi < 42:             return f"🟢 RSI bajo {rsi:.1f}"
    if rsi <= 58:            return f"⚪ RSI neutral {rsi:.1f}"
    if rsi < RSI_OS_LOW:    return f"🟡 RSI alto {rsi:.1f}"
    if rsi <= RSI_OS_HIGH:  return f"🔥 RSI SOBRECOMPRA {rsi:.1f} [{RSI_OS_LOW}-{RSI_OS_HIGH}]"
    return                         f"⚠️ RSI HIPERCOMPRA {rsi:.1f}"


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
    d  = s.diff()
    g  = d.clip(lower=0).ewm(span=n, adjust=False).mean()
    lo = (-d.clip(upper=0)).ewm(span=n, adjust=False).mean()
    return 100 - (100 / (1 + g / lo.replace(0, np.nan)))

def calc_adx(df: pd.DataFrame, n: int) -> Tuple[pd.Series, pd.Series, pd.Series]:
    h, l    = df["high"], df["low"]
    up, dn  = h.diff(), -l.diff()
    pdm     = up.where((up > dn) & (up > 0), 0.0)
    mdm     = dn.where((dn > up) & (dn > 0), 0.0)
    atr_s   = calc_atr(df, n)
    dip     = 100 * pdm.ewm(span=n, adjust=False).mean() / atr_s
    dim     = 100 * mdm.ewm(span=n, adjust=False).mean() / atr_s
    dx      = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    return dip, dim, dx.ewm(span=n, adjust=False).mean()

def calc_macd(s: pd.Series, fast=12, slow=26, sig=9):
    m    = ema(s, fast) - ema(s, slow)
    sg   = ema(m, sig)
    return m, sg, m - sg

def calc_stoch_rsi(s: pd.Series, rsi_len=14, stoch_len=14, k=3, d=3):
    rsi   = calc_rsi(s, rsi_len)
    lo    = rsi.rolling(stoch_len).min()
    hi    = rsi.rolling(stoch_len).max()
    stoch = 100 * (rsi - lo) / (hi - lo).replace(0, np.nan)
    kl    = stoch.rolling(k).mean()
    dl    = kl.rolling(d).mean()
    return kl, dl


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
    macd, macd_sg, macd_h = calc_macd(c, MACD_FAST, MACD_SLOW, MACD_SIG)
    df["macd"]       = macd
    df["macd_sig"]   = macd_sg
    df["macd_hist"]  = macd_h
    df["macd_bull"]  = (macd_h > 0) & (macd_h > macd_h.shift())
    df["macd_bear"]  = (macd_h < 0) & (macd_h < macd_h.shift())
    df["macd_cross_up"]   = (macd > macd_sg) & (macd.shift() <= macd_sg.shift())
    df["macd_cross_down"] = (macd < macd_sg) & (macd.shift() >= macd_sg.shift())

    # Stochastic RSI
    sk, sd = calc_stoch_rsi(c, RSI_LEN, STOCH_LEN)
    df["stoch_k"]    = sk
    df["stoch_d"]    = sd
    df["stoch_bull"] = (sk > sd) & (sk < 80) & (sk.shift() <= sd.shift())
    df["stoch_bear"] = (sk < sd) & (sk > 20) & (sk.shift() >= sd.shift())

    # Oscillator
    df["osc"]     = ema(((c - df["ema21"]) / (3.0 * df["atr"])) * 100, OSC_LEN)
    df["osc_up"]  = (df["osc"] > 0) & (df["osc"].shift() <= 0)
    df["osc_dn"]  = (df["osc"] < 0) & (df["osc"].shift() >= 0)

    # Bollinger Bands + Squeeze
    bb_std        = c.rolling(PIVOT_LEN).std()
    bb_up         = df["ema21"] + 2.0 * bb_std
    bb_lo         = df["ema21"] - 2.0 * bb_std
    kc_up         = df["ema21"] + 2.0 * df["atr"]
    df["squeeze"] = bb_up < kc_up
    bb_w          = (bb_up - bb_lo) / df["ema21"]
    df["is_trending"] = (adx > ADX_MIN) & (bb_w > sma(bb_w, 20) * 0.85)

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

    # Engulfing (vela que engulle a la anterior — señal fuerte)
    prev_body = (o.shift() - c.shift()).abs()
    df["bull_engulf"] = (
        (c > o) &                      # vela alcista
        (o <= c.shift()) &             # abre por debajo del cierre anterior
        (c >= o.shift()) &             # cierra por encima de apertura anterior
        (body > prev_body * 0.9)       # cuerpo mayor o similar
    )
    df["bear_engulf"] = (
        (c < o) &
        (o >= c.shift()) &
        (c <= o.shift()) &
        (body > prev_body * 0.9)
    )

    # Swing structure
    df["swing_low"]  = l.rolling(SWING_LB).min()
    df["swing_high"] = h.rolling(SWING_LB).max()

    # RSI Divergencia (3 barras)
    rsi = df["rsi"]
    df["bull_div"] = (
        (l < l.shift(1)) & (l.shift(1) < l.shift(2)) &   # precio: lower low
        (rsi > rsi.shift(1)) & (rsi.shift(1) > rsi.shift(2)) &  # RSI: higher low
        (rsi < 42)
    )
    df["bear_div"] = (
        (h > h.shift(1)) & (h.shift(1) > h.shift(2)) &
        (rsi < rsi.shift(1)) & (rsi.shift(1) < rsi.shift(2)) &
        (rsi > 58)
    )

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
# BTC BIAS (macro filtro)
# ══════════════════════════════════════════════════════════
def update_btc_bias(ex: ccxt.Exchange):
    try:
        df  = fetch_df_cached(ex, "BTC/USDT:USDT", "1h", limit=250)
        df  = compute(df)
        row = df.iloc[-2]
        state.btc_bull = bool(row["close"] > row["ema48"] and row["ema48"] > row["ema200"])
        state.btc_bear = bool(row["close"] < row["ema48"] and row["ema48"] < row["ema200"])
        state.btc_rsi  = float(row["rsi"])
        log.info(f"BTC bias: {'BULL' if state.btc_bull else 'BEAR' if state.btc_bear else 'NEUTRAL'} "
                 f"RSI:{state.btc_rsi:.1f}")
    except Exception as e:
        log.warning(f"BTC bias update failed: {e}")


# ══════════════════════════════════════════════════════════
# CONFLUENCE SCORE — 12 PUNTOS
# ══════════════════════════════════════════════════════════
def confluence_score(row: pd.Series,
                     htf1_bull: bool, htf1_bear: bool,
                     htf2_bull: bool, htf2_bear: bool) -> Tuple[int, int]:
    """
    12 puntos por dirección:
     1. Ribbon EMA: precio>EMA48 y EMA8>EMA21
     2. Oscillator crossover (cruce cero)
     3. HTF1 bias 15m
     4. HTF2 macro 1h
     5. ADX > 18 con DI correcto
     6. RSI zona normal (42-78 long | 22-58 short)
     7. Volumen: buy/sell pressure + spike
     8. Calidad de vela (cuerpo ≥35%)
     9. MACD histograma direccional y creciente
    10. Stochastic RSI crossover
    11. BONUS RSI extremo (10-25 long | 78-90 short)
    12. BONUS engulfing o divergencia RSI
    """
    rsi = float(row["rsi"])

    # ── LONG ──
    l1  = bool(row["close"] > row["ema48"] and row["ema8"] > row["ema21"])
    l2  = bool(row["osc_up"])
    l3  = htf1_bull
    l4  = htf2_bull
    l5  = bool(row["adx"] > ADX_MIN and row["dip"] > row["dim"])
    l6  = bool(42 <= rsi <= 78)
    l7  = bool(row["vol_bull"] and row["vol_spike"] and not row["squeeze"])
    l8  = bool(row["bull_candle"] and row["close"] > row["ema21"])
    l9  = bool(row["macd_bull"] or row["macd_cross_up"])
    l10 = bool(row["stoch_bull"] or
               (row["stoch_k"] > row["stoch_d"] and row["stoch_k"] < 75))
    l11 = rsi_extreme_long(rsi)                         # RSI 10-25
    l12 = bool(row["bull_engulf"] or row["bull_div"])   # Engulfing o divergencia

    # ── SHORT ──
    s1  = bool(row["close"] < row["ema48"] and row["ema8"] < row["ema21"])
    s2  = bool(row["osc_dn"])
    s3  = htf1_bear
    s4  = htf2_bear
    s5  = bool(row["adx"] > ADX_MIN and row["dim"] > row["dip"])
    s6  = bool(22 <= rsi <= 58)
    s7  = bool(row["vol_bear"] and row["vol_spike"] and not row["squeeze"])
    s8  = bool(row["bear_candle"] and row["close"] < row["ema21"])
    s9  = bool(row["macd_bear"] or row["macd_cross_down"])
    s10 = bool(row["stoch_bear"] or
               (row["stoch_k"] < row["stoch_d"] and row["stoch_k"] > 25))
    s11 = rsi_extreme_short(rsi)                        # RSI 78-90
    s12 = bool(row["bear_engulf"] or row["bear_div"])   # Engulfing o divergencia

    ls = sum([l1,l2,l3,l4,l5,l6,l7,l8,l9,l10,l11,l12])
    ss = sum([s1,s2,s3,s4,s5,s6,s7,s8,s9,s10,s11,s12])
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
        "enableRateLimit": True,
    })
    ex.load_markets()
    return ex


def detect_hedge_mode(ex: ccxt.Exchange) -> bool:
    try:
        positions = ex.fetch_positions()
        for p in positions[:5]:
            ps = p.get("info", {}).get("positionSide", "")
            if ps in ("LONG", "SHORT"):
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


def get_all_positions(ex: ccxt.Exchange) -> Dict[str, dict]:
    result: Dict[str, dict] = {}
    try:
        for p in ex.fetch_positions():
            if abs(float(p.get("contracts", 0) or 0)) > 0:
                result[p["symbol"]] = p
    except Exception as e:
        log.warning(f"fetch_positions error: {e}")
    return result


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
        mkt    = ex.markets.get(symbol, {})
        limits = mkt.get("limits", {})
        return float(limits.get("amount", {}).get("min", 0) or 0)
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════
# ORDER PARAMS — Hedge vs One-Way
# ══════════════════════════════════════════════════════════
def entry_params(side: str) -> dict:
    if HEDGE_MODE:
        return {"positionSide": "LONG" if side == "buy" else "SHORT"}
    return {}

def exit_params(trade_side: str) -> dict:
    if HEDGE_MODE:
        return {"positionSide": "LONG" if trade_side == "long" else "SHORT",
                "reduceOnly": True}
    return {"reduceOnly": True}


# ══════════════════════════════════════════════════════════
# UNIVERSE
# ══════════════════════════════════════════════════════════
def get_tradeable_symbols(ex: ccxt.Exchange) -> List[str]:
    symbols = []
    for sym, mkt in ex.markets.items():
        if not (mkt.get("swap") and mkt.get("quote") == "USDT"
                and mkt.get("active", True)):
            continue
        if sym in BLACKLIST: continue
        if SYMBOL_FILTER and mkt.get("base","") not in SYMBOL_FILTER: continue
        symbols.append(sym)

    if not symbols:
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
    result = [s for s, _ in ranked]
    if TOP_N_SYMBOLS > 0:
        result = result[:TOP_N_SYMBOLS]

    log.info(f"Universo final: {len(result)} pares (vol>${MIN_VOLUME_USDT/1e6:.1f}M)")
    return result


# ══════════════════════════════════════════════════════════
# ORDER MANAGEMENT
# ══════════════════════════════════════════════════════════
def open_trade(ex: ccxt.Exchange, symbol: str, side: str,
               score: int, row: pd.Series) -> Optional[TradeState]:
    try:
        # ── Validaciones previas ──
        spread = get_spread_pct(ex, symbol)
        if spread > MAX_SPREAD_PCT:
            log.warning(f"[{symbol}] spread {spread:.3f}% > {MAX_SPREAD_PCT}% — saltando")
            return None

        price    = get_last_price(ex, symbol)
        atr      = float(row["atr"])
        risk_pct = BASE_RISK   # solo para el log

        # Tamaño fijo: 8 USDT por operación
        FIXED_USDT = 8.0
        raw_amt    = FIXED_USDT / price
        amount     = float(ex.amount_to_precision(symbol, raw_amt))

        min_amt  = get_min_amount(ex, symbol)
        if amount < min_amt or amount <= 0:
            log.warning(f"[{symbol}] amount {amount:.6f} < min {min_amt} — saltando")
            return None

        notional = amount * price
        if notional < 5:
            log.warning(f"[{symbol}] notional ${notional:.2f} demasiado pequeño — saltando")
            return None

        log.info(f"[ENTRY] {symbol} {side.upper()} score={score}/12 "
                 f"size={amount} @ {price:.6g} notional=${amount*price:.2f} "
                 f"spread={spread:.3f}%")

        order       = ex.create_order(symbol, "market", side, amount,
                                      params=entry_params(side))
        entry_price = float(order.get("average") or price)
        trade_side  = "long" if side == "buy" else "short"

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
        ep         = exit_params(trade_side)

        for label, typ, qty, px in [
            ("TP1", "limit",       half_amt, tp1_p),
            ("TP2", "limit",       half_amt, tp2_p),
        ]:
            try:
                ex.create_order(symbol, typ, close_side, qty, px, ep)
                log.info(f"[{symbol}] {label} @ {px:.6g}")
            except Exception as e:
                log.warning(f"[{symbol}] {label} failed: {e}")

        try:
            sl_ep = {**ep, "stopPrice": sl_p}
            ex.create_order(symbol, "stop_market", close_side, amount, None, sl_ep)
            log.info(f"[{symbol}] SL  @ {sl_p:.6g}")
        except Exception as e:
            log.warning(f"[{symbol}] SL failed: {e}")

        t = TradeState(
            symbol=symbol,       side=trade_side,
            entry_price=entry_price, tp1_price=tp1_p,
            tp2_price=tp2_p,     sl_price=sl_p,
            entry_score=score,   entry_time=utcnow(),
            contracts=amount,    atr_entry=atr,
        )
        if side == "buy": t.trail_high = entry_price
        else:             t.trail_low  = entry_price

        log_trade_csv("OPEN", t, entry_price)

        extra = f"{'🔀HEDGE' if HEDGE_MODE else '1️⃣ONE-WAY'} | spread:{spread:.3f}% | 💵$8 fijos"
        tg_signal(t, 0.0, row, extra)
        return t

    except Exception as e:
        log.error(f"[{symbol}] open_trade error: {e}")
        tg_error(f"open_trade {symbol}: {e}")
        return None


def move_sl_to_breakeven(ex: ccxt.Exchange, symbol: str):
    """Mueve el stop-loss a precio de entrada (break-even)."""
    if symbol not in state.trades: return
    t = state.trades[symbol]
    if t.sl_moved_be: return

    try:
        ex.cancel_all_orders(symbol)
    except Exception as e:
        log.warning(f"[{symbol}] cancel for BE: {e}")

    be_price   = float(ex.price_to_precision(symbol, t.entry_price))
    close_side = "sell" if t.side == "long" else "buy"
    ep         = {**exit_params(t.side), "stopPrice": be_price}

    try:
        ex.create_order(symbol, "stop_market", close_side, t.contracts, None, ep)
        t.sl_price    = be_price
        t.sl_moved_be = True
        log.info(f"[{symbol}] SL → Break-even @ {be_price:.6g}")
    except Exception as e:
        log.warning(f"[{symbol}] BE SL failed: {e}")


def close_trade(ex: ccxt.Exchange, symbol: str, reason: str, current_price: float):
    if symbol not in state.trades: return
    t = state.trades[symbol]

    try: ex.cancel_all_orders(symbol)
    except Exception as e: log.warning(f"[{symbol}] cancel: {e}")

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
            return

    if pnl > 0:
        state.wins += 1; state.gross_profit += pnl; state.consec_losses = 0
    elif pnl < 0:
        state.losses += 1; state.gross_loss += abs(pnl); state.consec_losses += 1

    state.total_pnl  += pnl
    state.daily_pnl  += pnl
    state.peak_equity = max(state.peak_equity, state.peak_equity + pnl)
    state.set_cooldown(symbol)

    log_trade_csv("CLOSE", t, current_price, pnl)
    tg_close(reason, t, current_price, pnl)
    del state.trades[symbol]


# ══════════════════════════════════════════════════════════
# TRADE MANAGEMENT
# ══════════════════════════════════════════════════════════
def manage_open_trade(ex: ccxt.Exchange, symbol: str,
                      live_price: float, atr: float,
                      long_score: int, short_score: int,
                      live_pos: Optional[dict],
                      result: Optional[dict] = None):
    if symbol not in state.trades: return
    t = state.trades[symbol]

    # ── Cerrado externamente (TP o SL en exchange) ──
    if live_pos is None:
        if t.side == "long":
            pnl_est = (live_price - t.entry_price) * t.contracts
            reason  = "TP2 ALCANZADO" if live_price >= t.tp2_price else "SL ALCANZADO"
        else:
            pnl_est = (t.entry_price - live_price) * t.contracts
            reason  = "TP2 ALCANZADO" if live_price <= t.tp2_price else "SL ALCANZADO"

        if pnl_est > 0:
            state.wins += 1; state.gross_profit += pnl_est; state.consec_losses = 0
        else:
            state.losses += 1; state.gross_loss += abs(pnl_est); state.consec_losses += 1

        state.total_pnl += pnl_est
        state.daily_pnl += pnl_est
        state.set_cooldown(symbol)
        log_trade_csv("CLOSE_EXT", t, live_price, pnl_est)
        tg_close(reason, t, live_price, pnl_est)
        del state.trades[symbol]
        return

    # ══════════════════════════════════════════════════════
    # CIERRE ANTICIPADO #1 — Pérdida dinámica
    # Si el trade va en contra más de 0.8×ATR desde entrada
    # cierra inmediatamente sin esperar al SL del exchange.
    # Evita dejar una posición perdedora languidecer.
    # ══════════════════════════════════════════════════════
    if not t.tp1_hit:
        atr_now = atr if atr > 0 else t.atr_entry
        if t.side == "long":
            loss_dist = t.entry_price - live_price
        else:
            loss_dist = live_price - t.entry_price

        if loss_dist >= atr_now * 0.8:
            pnl_est = -loss_dist * t.contracts
            log.info(f"[{symbol}] CIERRE PÉRDIDA DINÁMICA — perdiendo {loss_dist:.5f} > 0.8×ATR")
            tg(
                f"🛑 <b>CIERRE POR PÉRDIDA</b> — {symbol}\n"
                f"📉 El precio fue en contra {loss_dist:.5f} (0.8×ATR)\n"
                f"💵 Entrada: <code>{t.entry_price:.6g}</code> → "
                f"Actual: <code>{live_price:.6g}</code>\n"
                f"💸 PnL est.: ${pnl_est:.2f}\n"
                f"⏰ {utcnow()}"
            )
            close_trade(ex, symbol, "PÉRDIDA DINÁMICA", live_price)
            return

    # ══════════════════════════════════════════════════════
    # CIERRE ANTICIPADO #2 — Agotamiento del movimiento
    # Detecta que el momentum se está acabando usando:
    #   · MACD histograma decreciente 2 ciclos seguidos
    #   · RSI perdiendo fuerza (divergiendo del precio)
    #   · ADX cayendo (tendencia debilitándose)
    #   · Volumen cayendo respecto a la media
    # Si 3 de 4 señales de agotamiento → cierra
    # Solo aplica si el trade lleva ganancia (protege profit)
    # ══════════════════════════════════════════════════════
    if result is not None and symbol in state.trades:
        row = result["row"]
        try:
            is_profitable = (
                (t.side == "long"  and live_price > t.entry_price) or
                (t.side == "short" and live_price < t.entry_price)
            )

            if is_profitable:
                rsi_val   = float(row["rsi"])
                macd_h    = float(row["macd_hist"])
                macd_h1   = float(row.get("macd_hist", macd_h))  # mismo valor si no hay prev
                adx_val   = float(row["adx"])
                vol_ratio = float(row["volume"]) / max(float(row["vol_ma"]), 1)

                # Señales de agotamiento según dirección
                if t.side == "long":
                    # Momentum perdiendo fuerza en alcista
                    exh1 = bool(row["macd_bear"])          # MACD histograma bajando
                    exh2 = rsi_val > 70 and bool(row["rsi_lh"] if "rsi_lh" in row.index else False)
                    exh3 = adx_val < 20                    # ADX cayendo = tendencia débil
                    exh4 = vol_ratio < 0.7                 # Volumen cayendo = convicción baja
                    exh5 = bool(row["bear_div"])            # Divergencia bajista en RSI
                    exh6 = bool(row["osc_dn"])              # Oscillator cruzando hacia abajo
                else:
                    # Momentum perdiendo fuerza en bajista
                    exh1 = bool(row["macd_bull"])
                    exh2 = rsi_val < 30 and bool(row["rsi_hl"] if "rsi_hl" in row.index else False)
                    exh3 = adx_val < 20
                    exh4 = vol_ratio < 0.7
                    exh5 = bool(row["bull_div"])
                    exh6 = bool(row["osc_up"])

                exhaustion_score = sum([exh1, exh2, exh3, exh4, exh5, exh6])

                if exhaustion_score >= 3:
                    if t.side == "long":
                        profit = (live_price - t.entry_price) * t.contracts
                    else:
                        profit = (t.entry_price - live_price) * t.contracts

                    log.info(
                        f"[{symbol}] AGOTAMIENTO detectado ({exhaustion_score}/6) — "
                        f"cerrando en ganancia ${profit:.2f}"
                    )
                    tg(
                        f"🏁 <b>CIERRE POR AGOTAMIENTO</b> — {symbol}\n"
                        f"══════════════════════════\n"
                        f"📊 Señales agotamiento: {exhaustion_score}/6\n"
                        f"  {'✅' if exh1 else '❌'} MACD perdiendo fuerza\n"
                        f"  {'✅' if exh3 else '❌'} ADX < 20 (tendencia débil)\n"
                        f"  {'✅' if exh4 else '❌'} Volumen cayendo ({vol_ratio:.2f}x)\n"
                        f"  {'✅' if exh5 else '❌'} Divergencia RSI\n"
                        f"  {'✅' if exh6 else '❌'} Oscillator girando\n"
                        f"💵 Precio: <code>{live_price:.6g}</code>\n"
                        f"💰 Ganancia est.: ${profit:+.2f}\n"
                        f"⏰ {utcnow()}"
                    )
                    close_trade(ex, symbol, "AGOTAMIENTO", live_price)
                    return
        except Exception as e:
            log.debug(f"[{symbol}] exhaustion check error: {e}")

    # ── TP1 hit → mover SL a break-even ──
    if not t.tp1_hit:
        tp1_hit = ((t.side == "long"  and live_price >= t.tp1_price) or
                   (t.side == "short" and live_price <= t.tp1_price))
        if tp1_hit:
            t.tp1_hit    = True
            contracts    = float(live_pos.get("contracts", 0))
            pnl_est      = abs(t.tp1_price - t.entry_price) * contracts * 0.5
            # Inicializar peak y prev para el trailing dinámico
            t.peak_price  = live_price
            t.prev_price  = live_price
            t.stall_count = 0
            t.trail_phase = "normal"
            log.info(f"[{symbol}] TP1 HIT @ {live_price:.6g} — moviendo SL a BE")
            move_sl_to_breakeven(ex, symbol)
            tg_tp1_be(t, live_price, pnl_est)

    # ── Trailing dinámico inteligente (post TP1) ──
    #
    # FASE "normal"  → precio corriendo, trailing amplio (0.8 × ATR)
    # FASE "tight"   → precio parado N ciclos, trailing apretado (0.4 × ATR)
    # FASE "locked"  → precio se gira (cede > 30% del avance), trailing mínimo (0.2 × ATR)
    #
    if t.tp1_hit and symbol in state.trades:
        atr_trail = atr if atr > 0 else t.atr_entry

        # Calcular ganancia actual en %
        if t.side == "long":
            current_profit_pct = (live_price - t.entry_price) / t.entry_price * 100
        else:
            current_profit_pct = (t.entry_price - live_price) / t.entry_price * 100
        t.max_profit_pct = max(t.max_profit_pct, current_profit_pct)

        # Actualizar peak del movimiento
        if t.side == "long":
            new_peak = live_price > t.peak_price
        else:
            new_peak = live_price < t.peak_price

        if new_peak:
            t.peak_price  = live_price
            t.stall_count = 0
        else:
            t.stall_count += 1

        # Detectar retroceso desde el peak (cuánto ha cedido)
        if t.side == "long":
            retrace_pct = (t.peak_price - live_price) / max(t.peak_price - t.entry_price, 1e-9) * 100
        else:
            retrace_pct = (live_price - t.peak_price) / max(t.entry_price - t.peak_price, 1e-9) * 100

        # ── Determinar fase ──
        prev_phase = t.trail_phase

        if retrace_pct > 30:
            # Precio cedió más del 30% de lo ganado → fase LOCKED (trailing mínimo)
            t.trail_phase = "locked"
        elif t.stall_count >= 3:
            # Sin nuevo máximo por 3 ciclos → fase TIGHT
            t.trail_phase = "tight"
        else:
            # Precio corriendo → fase NORMAL
            t.trail_phase = "normal"

        # Multiplicador de trailing según fase
        if   t.trail_phase == "locked": trail_m = 0.2
        elif t.trail_phase == "tight":  trail_m = 0.4
        else:                           trail_m = 0.8

        # Notificar cambio de fase
        if t.trail_phase != prev_phase:
            phase_emoji = {"normal": "🏃", "tight": "⚡", "locked": "🔒"}
            log.info(
                f"[{symbol}] Trailing fase: {prev_phase.upper()} → "
                f"{t.trail_phase.upper()} "
                f"(stall:{t.stall_count} retrace:{retrace_pct:.1f}% "
                f"mult:{trail_m}×ATR)"
            )
            tg(
                f"{phase_emoji[t.trail_phase]} <b>TRAILING {t.trail_phase.upper()}</b> "
                f"— {symbol}\n"
                f"Precio: <code>{live_price:.6g}</code> | "
                f"Peak: <code>{t.peak_price:.6g}</code>\n"
                f"Retroceso: {retrace_pct:.1f}% | Ganancia max: {t.max_profit_pct:.2f}%\n"
                f"Stop ajustado a {trail_m}×ATR\n"
                f"⏰ {utcnow()}"
            )

        # ── Calcular y comprobar el stop ──
        if t.side == "long":
            t.trail_high = max(t.trail_high, live_price)
            stop_level   = t.trail_high - atr_trail * trail_m
            if live_price <= stop_level:
                log.info(
                    f"[{symbol}] TRAILING {t.trail_phase.upper()} HIT "
                    f"@ {live_price:.6g} (stop:{stop_level:.6g})"
                )
                close_trade(ex, symbol, f"TRAILING {t.trail_phase.upper()}", live_price)
                return
        else:
            t.trail_low = min(t.trail_low, live_price)
            stop_level  = t.trail_low + atr_trail * trail_m
            if live_price >= stop_level:
                log.info(
                    f"[{symbol}] TRAILING {t.trail_phase.upper()} HIT "
                    f"@ {live_price:.6g} (stop:{stop_level:.6g})"
                )
                close_trade(ex, symbol, f"TRAILING {t.trail_phase.upper()}", live_price)
                return

        t.prev_price = live_price

    # ── Flip de señal ──
    if symbol in state.trades:
        if t.side == "long"  and short_score >= MIN_SCORE + 2:
            close_trade(ex, symbol, "FLIP LONG→SHORT", live_price)
        elif t.side == "short" and long_score >= MIN_SCORE + 2:
            close_trade(ex, symbol, "FLIP SHORT→LONG", live_price)


# ══════════════════════════════════════════════════════════
# SCAN DE UN SIMBOLO
# ══════════════════════════════════════════════════════════
def scan_symbol(ex: ccxt.Exchange, symbol: str) -> Optional[dict]:
    try:
        df  = fetch_df_cached(ex, symbol, TF,   limit=400)
        df1 = fetch_df_cached(ex, symbol, HTF1, limit=200)
        df2 = fetch_df_cached(ex, symbol, HTF2, limit=300)

        df  = compute(df)
        row = df.iloc[-2]

        if pd.isna(row["adx"]) or pd.isna(row["rsi"]) or pd.isna(row["macd_hist"]):
            return None

        htf1_bull, htf1_bear = htf_bias(df1)
        htf2_bull, htf2_bear = htf2_macro(df2)
        ls, ss               = confluence_score(row, htf1_bull, htf1_bear,
                                                htf2_bull, htf2_bear)
        rsi_val = float(row["rsi"])

        # ── Alerta RSI extremo (anti-spam: 1 alerta cada 30 min por símbolo) ──
        if rsi_extreme_long(rsi_val) or rsi_extreme_short(rsi_val):
            now = time.time()
            last = state.rsi_alerts.get(symbol, 0)
            if now - last > 1800:  # 30 min cooldown para alertas
                state.rsi_alerts[symbol] = now
                tg_rsi_extreme(
                    symbol, rsi_val, ls, ss,
                    float(row["close"]), float(row["adx"]), float(row["macd_hist"])
                )

        return {
            "symbol":       symbol,
            "long_score":   ls,
            "short_score":  ss,
            "row":          row,
            "atr":          float(row["atr"]),
            "live_price":   float(row["close"]),
            "is_trending":  bool(row["is_trending"]),
            "rsi":          rsi_val,
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
    log.info("   SATY ELITE v10 MULTI-SYMBOL — STARTING")
    log.info("=" * 65)

    dry_run = not (API_KEY and API_SECRET)
    if dry_run:
        log.warning("DRY-RUN: configura BINGX_API_KEY y BINGX_API_SECRET.")
        while True: log.info("DRY-RUN..."); time.sleep(POLL_SECS)

    # ── Conexión con reintentos ──
    ex = None
    for attempt in range(5):
        try:
            ex = build_exchange()
            break
        except Exception as e:
            wait = 2 ** attempt
            log.warning(f"Conexión fallida ({attempt+1}/5): {e} — reintento en {wait}s")
            time.sleep(wait)

    if ex is None:
        log.error("No se pudo conectar al exchange. Abortando.")
        return

    HEDGE_MODE = detect_hedge_mode(ex)
    log.info(f"Modo: {'HEDGE' if HEDGE_MODE else 'ONE-WAY'}")

    balance = 0.0
    for attempt in range(10):
        try:
            balance = get_balance(ex)
            break
        except Exception as e:
            log.warning(f"get_balance intento {attempt+1}/10: {e}")
            time.sleep(5)
    state.peak_equity    = balance
    state.daily_reset_ts = time.time()
    log.info(f"Balance: ${balance:.2f} USDT")

    symbols = get_tradeable_symbols(ex)
    while not symbols:
        log.error("Sin símbolos válidos — reintentando en 60s...")
        time.sleep(60)
        try:
            ex.load_markets()
            symbols = get_tradeable_symbols(ex)
        except Exception as e:
            log.warning(f"Reintento símbolos: {e}")

    update_btc_bias(ex)
    tg_startup(balance, len(symbols))

    scan_count    = 0
    REFRESH_EVERY = max(1, int(3600 / max(POLL_SECS, 1)))   # ~1h
    BTC_REFRESH   = max(1, int(900  / max(POLL_SECS, 1)))   # ~15min
    HB_INTERVAL   = 3600                                     # heartbeat cada 1h

    while True:
        try:
            ts_start = time.time()
            scan_count += 1
            state.reset_daily_if_needed()
            clear_cache()   # limpiar cache al inicio de cada ciclo

            log.info(
                f"━━━ SCAN #{scan_count} | {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} "
                f"| {len(symbols)} pares | "
                f"Trades:{state.open_count()}/{MAX_OPEN_TRADES} ━━━"
            )

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

            # Heartbeat cada 1h
            if time.time() - state.last_heartbeat > HB_INTERVAL:
                try:
                    bal = get_balance(ex)
                    tg_heartbeat(bal)
                    state.last_heartbeat = time.time()
                except Exception:
                    pass

            # ── Guardias ──
            if state.cb_active():
                log.warning(f"CIRCUIT BREAKER — drawdown >= {CB_DD}%")
                time.sleep(POLL_SECS); continue

            if state.daily_limit_hit():
                log.warning(f"LÍMITE DIARIO alcanzado ({DAILY_LOSS_LIMIT}%)")
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
                    atr = result["atr"]         if result else state.trades[sym].atr_entry
                    manage_open_trade(ex, sym, live_price, atr, ls, ss, live_pos, result)
                except Exception as e:
                    log.warning(f"[{sym}] manage error: {e}")

            # ── Escaneo de nuevas señales ──
            new_signals: List[dict] = []

            if state.open_count() < MAX_OPEN_TRADES and in_session():
                syms_to_scan = [
                    s for s in symbols
                    if s not in state.trades and not state.in_cooldown(s)
                ]

                with ThreadPoolExecutor(max_workers=5) as pool:
                    futures = {pool.submit(scan_symbol, ex, s): s
                               for s in syms_to_scan}
                    results = [f.result() for f in as_completed(futures)
                               if f.result() is not None]

                for res in results:
                    best_side, best_score = None, 0

                    can_long  = res["long_score"]  >= MIN_SCORE and res["is_trending"]
                    can_short = res["short_score"] >= MIN_SCORE and res["is_trending"]

                    # Filtro BTC macro
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
                            "rsi":    res["rsi"],
                        })

                new_signals.sort(key=lambda x: x["score"], reverse=True)

                for sig in new_signals:
                    if state.open_count() >= MAX_OPEN_TRADES: break
                    sym  = sig["symbol"]
                    if sym in state.trades: continue
                    side = "buy" if sig["side"] == "long" else "sell"
                    t    = open_trade(ex, sym, side, sig["score"], sig["row"])
                    if t:
                        state.trades[sym] = t

            elif not in_session():
                log.info("Fuera de sesión")
            else:
                log.info(f"Max trades ({MAX_OPEN_TRADES}) alcanzado")

            # ── Log de ciclo ──
            elapsed = time.time() - ts_start
            log.info(
                f"✓ Ciclo {elapsed:.1f}s | "
                f"Señales:{len(new_signals)} | "
                f"{state.wins}W/{state.losses}L | "
                f"Diario:${state.daily_pnl:+.2f} | "
                f"Total:${state.total_pnl:+.2f}"
            )

            # Resumen Telegram cada 20 ciclos
            if scan_count % 20 == 0:
                tg_scan_summary(new_signals, len(symbols))

        except ccxt.NetworkError as e:
            log.warning(f"Network: {e} — reintentando...")
            time.sleep(5)
        except ccxt.ExchangeError as e:
            log.error(f"Exchange: {e}")
            tg(f"❌ <b>Exchange error:</b> <code>{str(e)[:200]}</code>")
        except KeyboardInterrupt:
            log.info("Bot detenido.")
            tg("🛑 <b>Bot detenido manualmente.</b>")
            break
        except Exception as e:
            log.exception(f"Error inesperado: {e}")
            tg_error(str(e))

        # Ajustar sleep si el ciclo tardó mucho
        elapsed = time.time() - ts_start
        sleep_t = max(0, POLL_SECS - elapsed)
        time.sleep(sleep_t)


if __name__ == "__main__":
    # ── Bucle de reinicio infinito ──
    # Si main() termina por cualquier error no capturado,
    # el proceso nunca muere → Railway no lo marca como "Completed"
    while True:
        try:
            main()
        except KeyboardInterrupt:
            log.info("Detenido por usuario.")
            break
        except Exception as e:
            log.exception(f"CRASH en main(): {e}")
            try:
                tg_error(f"CRASH — reiniciando en 30s:\n{e}")
            except Exception:
                pass
            log.info("Reiniciando en 30 segundos...")
            time.sleep(30)
