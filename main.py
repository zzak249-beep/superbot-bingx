"""
╔══════════════════════════════════════════════════════════════════╗
║         SATY ELITE v13 — FULL STRATEGY EDITION                  ║
║         BingX Perpetual Futures · 12 Trades · 24/7             ║
╠══════════════════════════════════════════════════════════════════╣
║  NUEVO v13 — 4 Pine Scripts integrados:                         ║
║                                                                  ║
║  1. UTBot (HPotter/Yo_adriiiiaan)                               ║
║     · ATR Trailing Stop line con Key Value configurable         ║
║     · Señal: EMA cruza ATR Trailing Stop → punto score 13       ║
║     · UTBot trailing como 2ª capa de protección                 ║
║                                                                  ║
║  2. Instrument-Z (OscillateMatrix)                              ║
║     · WaveTrend (TCI) oscillator → puntos score 14             ║
║     · Divergencias WaveTrend                                    ║
║     · TP/SL diferenciados UpTrend vs DownTrend                  ║
║     · Trade Expiration (cierre por barras máximas)              ║
║     · Mínimo profit para salidas de señal                       ║
║                                                                  ║
║  3. Bj Bot (3Commas framework)                                  ║
║     · Stops basados en Swing H/L + ATR buffer                  ║
║     · R:R ratio configurable (Risk to Reward)                   ║
║     · Trail trigger a X% del reward (rrExit)                   ║
║     · MA cross signal → punto score 15                          ║
║                                                                  ║
║  4. BB+RSI (rouxam)                                             ║
║     · Bollinger Bands oversold/overbought                       ║
║     · BB signal filtrada por RSI → punto score 16              ║
║                                                                  ║
║  Score total: 16 puntos (antes 12)                              ║
║  Score mínimo recomendado: 5 (ajustar según perfil)             ║
╚══════════════════════════════════════════════════════════════════╝

VARIABLES OBLIGATORIAS:
    BINGX_API_KEY  BINGX_API_SECRET
    TELEGRAM_BOT_TOKEN  TELEGRAM_CHAT_ID

VARIABLES OPCIONALES — GENERALES:
    MAX_OPEN_TRADES   def:12    FIXED_USDT      def:8
    MIN_SCORE         def:5     MAX_DRAWDOWN    def:15
    DAILY_LOSS_LIMIT  def:8     MIN_VOLUME_USDT def:100000
    TOP_N_SYMBOLS     def:300   POLL_SECONDS    def:60
    TIMEFRAME         def:5m    HTF1            def:15m
    HTF2              def:1h    BTC_FILTER      def:true
    COOLDOWN_MIN      def:20    MAX_SPREAD_PCT  def:1.0
    BLACKLIST

VARIABLES — SMI (Stochastic Momentum Index):
    SMI_K_LEN  def:10   SMI_D_LEN  def:3
    SMI_EMA_LEN def:10  SMI_SMOOTH def:5
    SMI_OB     def:40   SMI_OS     def:-40

VARIABLES — UTBOT (ATR Trailing Stop):
    UTBOT_KEY_VALUE   def:10   sensibilidad (+ bajo = + sensible)
    UTBOT_ATR_PERIOD  def:10   periodo ATR del trailing stop

VARIABLES — WAVETREND (Instrument-Z):
    WT_CHAN_LEN   def:9    Canal EMA
    WT_AVG_LEN    def:12   Media EMA
    WT_OB         def:60   Sobrecompra
    WT_OS         def:-60  Sobreventa

VARIABLES — BB+RSI (Bollinger Bands):
    BB_PERIOD  def:20   periodo de la BB
    BB_STD     def:2.0  desviaciones estándar
    BB_RSI_OB  def:65   RSI máximo para señal long

VARIABLES — BJ BOT (Risk Management):
    RNR        def:2.0  Risk to Reward ratio (TP = RnR × Risk)
    RISK_MULT  def:1.0  Buffer ATR para stop (detrás del swing)
    RR_EXIT    def:0.5  % del reward para activar trailing (0=inmediato)
    SWING_LB   def:10   Lookback swing high/low (redefinible)
    MIN_PROFIT_PCT def:0.0  Mínimo profit % para cerrar por señal
    TRADE_EXPIRE_BARS def:0 Barras máx por trade (0=desactivado)
"""

import os, time, logging, csv
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
log = logging.getLogger("saty_v13")

# ══════════════════════════════════════════════════════════
# CONFIG — variables de entorno
# ══════════════════════════════════════════════════════════
API_KEY    = os.environ.get("BINGX_API_KEY",    "")
API_SECRET = os.environ.get("BINGX_API_SECRET", "")
TF         = os.environ.get("TIMEFRAME",  "5m")
HTF1       = os.environ.get("HTF1",       "15m")
HTF2       = os.environ.get("HTF2",       "1h")
POLL_SECS  = int(os.environ.get("POLL_SECONDS", "60"))
TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID",   "")

_bl = os.environ.get("BLACKLIST", "")
BLACKLIST: List[str] = [s.strip() for s in _bl.split(",") if s.strip()]

# ── Capital ──
FIXED_USDT       = float(os.environ.get("FIXED_USDT",       "8.0"))
MAX_OPEN_TRADES  = int(os.environ.get("MAX_OPEN_TRADES",    "12"))
MIN_SCORE        = int(os.environ.get("MIN_SCORE",          "5"))
CB_DD            = float(os.environ.get("MAX_DRAWDOWN",     "15.0"))
DAILY_LOSS_LIMIT = float(os.environ.get("DAILY_LOSS_LIMIT", "8.0"))
COOLDOWN_MIN     = int(os.environ.get("COOLDOWN_MIN",       "20"))
MAX_SPREAD_PCT   = float(os.environ.get("MAX_SPREAD_PCT",   "1.0"))
MIN_VOLUME_USDT  = float(os.environ.get("MIN_VOLUME_USDT",  "100000"))
TOP_N_SYMBOLS    = int(os.environ.get("TOP_N_SYMBOLS",      "300"))
BTC_FILTER       = os.environ.get("BTC_FILTER", "true").lower() == "true"

# ── SMI ──
SMI_K_LEN   = int(os.environ.get("SMI_K_LEN",   "10"))
SMI_D_LEN   = int(os.environ.get("SMI_D_LEN",   "3"))
SMI_EMA_LEN = int(os.environ.get("SMI_EMA_LEN", "10"))
SMI_SMOOTH  = int(os.environ.get("SMI_SMOOTH",  "5"))
SMI_OB      = float(os.environ.get("SMI_OB",    "40.0"))
SMI_OS      = float(os.environ.get("SMI_OS",    "-40.0"))

# ── UTBot (ATR Trailing Stop) ──
UTBOT_KEY    = float(os.environ.get("UTBOT_KEY_VALUE",  "10.0"))
UTBOT_ATR    = int(os.environ.get("UTBOT_ATR_PERIOD",  "10"))

# ── WaveTrend (Instrument-Z) ──
WT_CHAN_LEN = int(os.environ.get("WT_CHAN_LEN", "9"))
WT_AVG_LEN  = int(os.environ.get("WT_AVG_LEN", "12"))
WT_OB       = float(os.environ.get("WT_OB",    "60.0"))
WT_OS       = float(os.environ.get("WT_OS",    "-60.0"))

# ── Bollinger Bands + RSI ──
BB_PERIOD  = int(os.environ.get("BB_PERIOD", "20"))
BB_STD     = float(os.environ.get("BB_STD",  "2.0"))
BB_RSI_OB  = float(os.environ.get("BB_RSI_OB", "65.0"))

# ── Bj Bot Risk Management ──
RNR              = float(os.environ.get("RNR",               "2.0"))
RISK_MULT        = float(os.environ.get("RISK_MULT",         "1.0"))
RR_EXIT          = float(os.environ.get("RR_EXIT",           "0.5"))
MIN_PROFIT_PCT   = float(os.environ.get("MIN_PROFIT_PCT",    "0.0"))
TRADE_EXPIRE_BARS= int(os.environ.get("TRADE_EXPIRE_BARS",  "0"))

# ── Indicadores clásicos ──
FAST_LEN  = 8;   PIVOT_LEN = 21; BIAS_LEN  = 48; SLOW_LEN  = 200
ADX_LEN   = 14;  ADX_MIN   = 16; RSI_LEN   = 14; ATR_LEN   = 14
VOL_LEN   = 20;  OSC_LEN   = 3;  SWING_LB  = int(os.environ.get("SWING_LB", "10"))
MACD_FAST = 12;  MACD_SLOW = 26; MACD_SIG  = 9

# ── Exits ──
TP1_ATR_MULT = 1.2
SL_ATR_MULT  = 1.0

# ── RSI extremo ──
RSI_OB_LOW = 10; RSI_OB_HIGH = 25
RSI_OS_LOW = 78; RSI_OS_HIGH = 90

# ── Risk ──
MAX_CONSEC_LOSS = 3
USE_CB          = True
HEDGE_MODE: bool = False
CSV_PATH = "/tmp/saty_v13_trades.csv"


# ══════════════════════════════════════════════════════════
# CACHE OHLCV
# ══════════════════════════════════════════════════════════
_cache: Dict[str, Tuple[float, pd.DataFrame]] = {}
CACHE_TTL = 55

def fetch_df(ex: ccxt.Exchange, symbol: str, tf: str, limit: int = 400) -> pd.DataFrame:
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
# ESTADO
# ══════════════════════════════════════════════════════════
@dataclass
class TradeState:
    symbol:           str   = ""
    side:             str   = ""
    base:             str   = ""
    entry_price:      float = 0.0
    tp1_price:        float = 0.0
    tp2_price:        float = 0.0
    sl_price:         float = 0.0
    sl_moved_be:      bool  = False
    tp1_hit:          bool  = False
    trail_high:       float = 0.0
    trail_low:        float = 0.0
    peak_price:       float = 0.0
    prev_price:       float = 0.0
    stall_count:      int   = 0
    trail_phase:      str   = "normal"
    max_profit_pct:   float = 0.0
    entry_score:      int   = 0
    entry_time:       str   = ""
    contracts:        float = 0.0
    atr_entry:        float = 0.0
    smi_entry:        float = 0.0
    wt_entry:         float = 0.0
    utbot_stop:       float = 0.0   # UTBot ATR trailing stop at entry
    bar_count:        int   = 0     # barras desde entrada (trade expiry)
    uptrend_entry:    bool  = True  # era uptrend en la entrada
    rr_trail_active:  bool  = False # R:R trail trigger activado (Bj Bot)
    rr_trail_stop:    float = 0.0   # nivel del trailing Bj Bot


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
    trades:    Dict[str, TradeState] = field(default_factory=dict)
    cooldowns: Dict[str, float]      = field(default_factory=dict)
    rsi_alerts:Dict[str, float]      = field(default_factory=dict)
    btc_bull: bool  = True
    btc_bear: bool  = False
    btc_rsi:  float = 50.0

    def open_count(self) -> int: return len(self.trades)
    def bases_open(self) -> Dict[str, str]:
        return {t.base: t.side for t in self.trades.values()}
    def base_has_trade(self, base: str) -> bool:
        return base in self.bases_open()
    def win_rate(self) -> float:
        t = self.wins + self.losses
        return (self.wins / t * 100) if t else 0.0
    def profit_factor(self) -> float:
        return (self.gross_profit / self.gross_loss) if self.gross_loss else 0.0
    def score_bar(self, score: int, mx: int = 16) -> str:
        return "█" * min(score, mx) + "░" * (mx - min(score, mx))
    def cb_active(self) -> bool:
        if not USE_CB or self.peak_equity <= 0: return False
        dd = (self.peak_equity - (self.peak_equity + self.total_pnl)) / self.peak_equity * 100
        return dd >= CB_DD
    def daily_limit_hit(self) -> bool:
        if self.peak_equity <= 0: return False
        return self.daily_pnl < 0 and abs(self.daily_pnl) / self.peak_equity * 100 >= DAILY_LOSS_LIMIT
    def risk_mult(self) -> float:
        return 0.5 if self.consec_losses >= MAX_CONSEC_LOSS else 1.0
    def in_cooldown(self, symbol: str) -> bool:
        return time.time() - self.cooldowns.get(symbol, 0) < COOLDOWN_MIN * 60
    def set_cooldown(self, symbol: str):
        self.cooldowns[symbol] = time.time()
    def reset_daily(self):
        now = time.time()
        if now - self.daily_reset_ts > 86400:
            self.daily_pnl = 0.0; self.daily_reset_ts = now
            log.info("Daily PnL reseteado")


state = BotState()


# ══════════════════════════════════════════════════════════
# CSV LOG
# ══════════════════════════════════════════════════════════
def log_csv(action: str, t: TradeState, price: float, pnl: float = 0.0):
    try:
        exists = os.path.exists(CSV_PATH)
        with open(CSV_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["ts","action","symbol","base","side","score",
                            "smi","wt","entry","exit","pnl","contracts","bars"])
            w.writerow([utcnow(), action, t.symbol, t.base, t.side,
                        t.entry_score, round(t.smi_entry,2), round(t.wt_entry,2),
                        t.entry_price, price, round(pnl,4), t.contracts, t.bar_count])
    except Exception as e:
        log.warning(f"CSV: {e}")


# ══════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════
def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def smi_label(smi: float) -> str:
    if smi >= SMI_OB:  return f"🔴 SMI OB {smi:.1f}"
    if smi <= SMI_OS:  return f"🟢 SMI OS {smi:.1f}"
    if smi > 0:        return f"⚪ SMI {smi:.1f}↑"
    return                    f"⚪ SMI {smi:.1f}↓"

def wt_label(wt: float) -> str:
    if wt >= WT_OB:  return f"🔴 WT OB {wt:.1f}"
    if wt <= WT_OS:  return f"🟢 WT OS {wt:.1f}"
    if wt > 0:       return f"⚪ WT {wt:.1f}↑"
    return                  f"⚪ WT {wt:.1f}↓"

def rsi_extreme_long(rsi: float) -> bool:
    return RSI_OB_LOW <= rsi <= RSI_OB_HIGH

def rsi_extreme_short(rsi: float) -> bool:
    return RSI_OS_LOW <= rsi <= RSI_OS_HIGH

def rsi_zone_label(rsi: float) -> str:
    if rsi < RSI_OB_LOW:   return f"⚠️ RSI HIPERVENTA {rsi:.1f}"
    if rsi <= RSI_OB_HIGH: return f"🔥 RSI SOBREVENTA {rsi:.1f}"
    if rsi < 42:            return f"🟢 RSI bajo {rsi:.1f}"
    if rsi <= 58:           return f"⚪ RSI neutral {rsi:.1f}"
    if rsi < RSI_OS_LOW:   return f"🟡 RSI alto {rsi:.1f}"
    if rsi <= RSI_OS_HIGH: return f"🔥 RSI SOBRECOMPRA {rsi:.1f}"
    return                        f"⚠️ RSI HIPERCOMPRA {rsi:.1f}"


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
        log.warning(f"TG: {e}")

def tg_startup(balance: float, n: int):
    tg(
        f"<b>🚀 SATY ELITE v13 — FULL STRATEGY EDITION</b>\n"
        f"══════════════════════════════\n"
        f"🌍 Universo: {n} pares | Vol≥${MIN_VOLUME_USDT/1000:.0f}K\n"
        f"⚙️ Modo: {'HEDGE' if HEDGE_MODE else 'ONE-WAY'} | 24/7\n"
        f"⏱ {TF} · {HTF1} · {HTF2}\n"
        f"🎯 Score min: {MIN_SCORE}/16 | Max trades: {MAX_OPEN_TRADES}\n"
        f"💰 Balance: ${balance:.2f} | ${FIXED_USDT:.0f}/trade\n"
        f"🛡 CB: -{CB_DD}% | Límite diario: -{DAILY_LOSS_LIMIT}%\n"
        f"══════════════════════════════\n"
        f"📊 SMI({SMI_K_LEN},{SMI_D_LEN},{SMI_EMA_LEN}) OB:{SMI_OB:+.0f}/{SMI_OS:+.0f}\n"
        f"🌊 WaveTrend({WT_CHAN_LEN},{WT_AVG_LEN}) OB:{WT_OB}/{WT_OS}\n"
        f"🤖 UTBot KeyVal:{UTBOT_KEY} ATR:{UTBOT_ATR}\n"
        f"📈 BB({BB_PERIOD},{BB_STD}) | R:R={RNR} | RiskMult={RISK_MULT}\n"
        f"{'⏳ Expire:' + str(TRADE_EXPIRE_BARS) + 'bars' if TRADE_EXPIRE_BARS > 0 else '⏳ Expire: OFF'}\n"
        f"₿ Filtro BTC: {'✅' if BTC_FILTER else '❌'}\n"
        f"⏰ {utcnow()}"
    )

def tg_signal(t: TradeState, row: pd.Series):
    e      = "🟢" if t.side == "long" else "🔴"
    sl_d   = abs(t.sl_price - t.entry_price)
    rr1    = abs(t.tp1_price - t.entry_price) / max(sl_d, 1e-9)
    rr2    = abs(t.tp2_price - t.entry_price) / max(sl_d, 1e-9)
    smi_v  = float(row.get("smi", 0.0))
    wt_v   = float(row.get("wt1", 0.0))
    ut_stop= float(row.get("utbot_stop", 0.0))
    trend  = "📈 UpTrend" if t.uptrend_entry else "📉 DownTrend"
    tg(
        f"{e} <b>{'LONG' if t.side=='long' else 'SHORT'}</b> — {t.symbol}\n"
        f"══════════════════════════════\n"
        f"🎯 Score: {t.entry_score}/16  {state.score_bar(t.entry_score)}\n"
        f"📊 {trend}\n"
        f"💵 Entrada: <code>{t.entry_price:.6g}</code>\n"
        f"🟡 TP1: <code>{t.tp1_price:.6g}</code> R:R 1:{rr1:.1f}\n"
        f"🟢 TP2: <code>{t.tp2_price:.6g}</code> R:R 1:{rr2:.1f}\n"
        f"🛑 SL: <code>{t.sl_price:.6g}</code> → BE tras TP1\n"
        f"🤖 UTBot Stop: <code>{ut_stop:.6g}</code>\n"
        f"══════════════════════════════\n"
        f"{smi_label(smi_v)} | {wt_label(wt_v)}\n"
        f"{rsi_zone_label(float(row['rsi']))} | ADX:{row['adx']:.1f}\n"
        f"MACD:{row['macd_hist']:.5f} | Vol:{row['volume']/row['vol_ma']:.2f}x\n"
        f"ATR:{t.atr_entry:.5f} | ${FIXED_USDT:.0f} fijos\n"
        f"₿{'🟢' if state.btc_bull else '🔴' if state.btc_bear else '⚪'} "
        f"RSI:{state.btc_rsi:.0f}\n"
        f"📊 {state.open_count()}/{MAX_OPEN_TRADES} trades\n"
        f"⏰ {utcnow()}"
    )

def tg_tp1_be(t: TradeState, price: float, pnl: float):
    tg(
        f"🟡 <b>TP1 + BREAK-EVEN</b> — {t.symbol}\n"
        f"💵 <code>{price:.6g}</code> | PnL parcial: ~${pnl:+.2f}\n"
        f"SMI:{t.smi_entry:.1f} | WT:{t.wt_entry:.1f}\n"
        f"🛡 SL → entrada <code>{t.entry_price:.6g}</code>\n"
        f"⏰ {utcnow()}"
    )

def tg_trail_phase(t: TradeState, phase: str, price: float,
                   retrace: float, trail_m: float):
    icons = {"normal": "🏃", "tight": "⚡", "locked": "🔒", "utbot": "🤖", "rr": "📐"}
    tg(
        f"{icons.get(phase,'⚡')} <b>TRAILING {phase.upper()}</b> — {t.symbol}\n"
        f"Precio: <code>{price:.6g}</code> | Peak: <code>{t.peak_price:.6g}</code>\n"
        f"Retroceso: {retrace:.1f}% | Mult: {trail_m}\n"
        f"Ganancia max: {t.max_profit_pct:.2f}%\n"
        f"⏰ {utcnow()}"
    )

def tg_close(reason: str, t: TradeState, exit_p: float, pnl: float):
    e   = "✅" if pnl > 0 else "❌"
    pct = (pnl / (t.entry_price * t.contracts) * 100) if t.contracts > 0 else 0
    tg(
        f"{e} <b>CERRADO</b> — {t.symbol}\n"
        f"📋 {t.side.upper()} · {t.entry_score}/16 · {reason}\n"
        f"💵 <code>{t.entry_price:.6g}</code> → <code>{exit_p:.6g}</code> ({pct:+.2f}%)\n"
        f"{'💰' if pnl>0 else '💸'} PnL: ${pnl:+.2f} | Barras: {t.bar_count}\n"
        f"📊 {state.wins}W/{state.losses}L · WR:{state.win_rate():.1f}% · PF:{state.profit_factor():.2f}\n"
        f"💹 Hoy:${state.daily_pnl:+.2f} · Total:${state.total_pnl:+.2f}\n"
        f"⏰ {utcnow()}"
    )

def tg_rsi_alert(symbol: str, rsi: float, smi: float, wt: float,
                 ls: int, ss: int, price: float):
    direction = "📉 LONG rebote" if rsi_extreme_long(rsi) else "📈 SHORT caída"
    tg(
        f"🔔 <b>RSI EXTREMO</b> — {symbol}\n"
        f"{rsi_zone_label(rsi)}\n"
        f"{smi_label(smi)} | {wt_label(wt)}\n"
        f"💵 <code>{price:.6g}</code> | {direction}\n"
        f"Score: L:{ls}/16 S:{ss}/16\n"
        f"⏰ {utcnow()}"
    )

def tg_summary(signals: List[dict], n_scanned: int):
    open_lines = "\n".join(
        f"  {'🟢' if ts.side=='long' else '🔴'} {sym} E:{ts.entry_price:.5g} "
        f"WT:{ts.wt_entry:.1f} {'🛡' if ts.sl_moved_be else ''}"
        for sym, ts in state.trades.items()
    ) or "  (ninguna)"
    top = "\n".join(
        f"  {'🟢' if s['side']=='long' else '🔴'} {s['symbol']} "
        f"{s['score']}/16 {wt_label(s['wt'])}"
        for s in signals[:5]
    ) or "  (ninguna)"
    tg(
        f"📡 <b>RESUMEN</b> — {n_scanned} pares · {utcnow()}\n"
        f"Top señales:\n{top}\n"
        f"══════════════════════════════\n"
        f"Posiciones ({state.open_count()}/{MAX_OPEN_TRADES}):\n{open_lines}\n"
        f"══════════════════════════════\n"
        f"CB:{'⛔' if state.cb_active() else '✅'} Hoy:${state.daily_pnl:+.2f}\n"
        f"₿{'🟢' if state.btc_bull else '🔴'} {state.wins}W/{state.losses}L PF:{state.profit_factor():.2f}"
    )

def tg_heartbeat(balance: float):
    bases    = state.bases_open()
    open_str = ", ".join(f"{b}({'L' if s=='long' else 'S'})"
                         for b, s in bases.items()) or "ninguna"
    tg(
        f"💓 <b>HEARTBEAT</b> — {utcnow()}\n"
        f"Balance: ${balance:.2f} | Hoy: ${state.daily_pnl:+.2f}\n"
        f"Trades: {state.open_count()}/{MAX_OPEN_TRADES} | {open_str}\n"
        f"₿ {'BULL' if state.btc_bull else 'BEAR' if state.btc_bear else 'NEUTRAL'} "
        f"RSI:{state.btc_rsi:.0f}"
    )

def tg_error(msg: str):
    tg(f"🔥 <b>ERROR:</b> <code>{msg[:300]}</code>\n⏰ {utcnow()}")


# ══════════════════════════════════════════════════════════
# INDICADORES BASE
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
    h, l   = df["high"], df["low"]
    up, dn = h.diff(), -l.diff()
    pdm    = up.where((up > dn) & (up > 0), 0.0)
    mdm    = dn.where((dn > up) & (dn > 0), 0.0)
    atr_s  = calc_atr(df, n)
    dip    = 100 * pdm.ewm(span=n, adjust=False).mean() / atr_s
    dim    = 100 * mdm.ewm(span=n, adjust=False).mean() / atr_s
    dx     = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    return dip, dim, dx.ewm(span=n, adjust=False).mean()

def calc_macd(s: pd.Series):
    m  = ema(s, MACD_FAST) - ema(s, MACD_SLOW)
    sg = ema(m, MACD_SIG)
    return m, sg, m - sg


# ══════════════════════════════════════════════════════════
# SMI — Stochastic Momentum Index (Pine Script original)
# ══════════════════════════════════════════════════════════
def calc_smi(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    h, l, c = df["high"], df["low"], df["close"]
    ll      = l.rolling(SMI_K_LEN).min()
    hh      = h.rolling(SMI_K_LEN).max()
    diff    = hh - ll
    rdiff   = c - (hh + ll) / 2
    avgrel  = rdiff.ewm(span=SMI_D_LEN,  adjust=False).mean()
    avgdiff = diff.ewm(span=SMI_D_LEN,   adjust=False).mean()
    smi_raw = pd.Series(
        np.where(avgdiff.abs() > 1e-10, (avgrel / (avgdiff / 2)) * 100, 0.0),
        index=df.index
    )
    smoothed = smi_raw.rolling(SMI_SMOOTH).mean()
    signal   = smoothed.ewm(span=SMI_EMA_LEN, adjust=False).mean()
    return smoothed, signal


# ══════════════════════════════════════════════════════════
# UTBOT — ATR Trailing Stop (HPotter / Yo_adriiiiaan)
# Traducción exacta del Pine Script v2
# ══════════════════════════════════════════════════════════
def calc_utbot(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    xATR  = atr(ATR_PERIOD)
    nLoss = KEY_VALUE * xATR
    xATRTrailingStop logic (iff cascade):
      if close > prev_stop AND close[1] > prev_stop: max(prev_stop, close-nLoss)
      elif close < prev_stop AND close[1] < prev_stop: min(prev_stop, close+nLoss)
      elif close > prev_stop: close - nLoss
      else: close + nLoss
    buy  = close > stop AND ema(close,1) crosses above stop
    sell = close < stop AND ema(close,1) crosses below stop
    """
    atr_vals = calc_atr(df, UTBOT_ATR)
    n_loss   = UTBOT_KEY * atr_vals
    c        = df["close"]

    stop = pd.Series(0.0, index=df.index)
    c_arr    = c.values
    nl_arr   = n_loss.values
    st_arr   = stop.values

    for i in range(1, len(df)):
        prev = st_arr[i - 1]
        curr = c_arr[i]
        prev_c = c_arr[i - 1]
        loss   = nl_arr[i]
        if curr > prev and prev_c > prev:
            st_arr[i] = max(prev, curr - loss)
        elif curr < prev and prev_c < prev:
            st_arr[i] = min(prev, curr + loss)
        elif curr > prev:
            st_arr[i] = curr - loss
        else:
            st_arr[i] = curr + loss

    stop     = pd.Series(st_arr, index=df.index)
    ema1     = c.ewm(span=1, adjust=False).mean()
    buy_sig  = (c > stop) & (ema1 > stop) & (ema1.shift() <= stop.shift())
    sell_sig = (c < stop) & (ema1 < stop) & (ema1.shift() >= stop.shift())
    return stop, buy_sig, sell_sig


# ══════════════════════════════════════════════════════════
# WAVETREND — TCI (Instrument-Z / OscillateMatrix)
# ══════════════════════════════════════════════════════════
def calc_wavetrend(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """
    ap   = hlc3
    esa  = ema(ap, CHAN_LEN)
    d    = ema(abs(ap - esa), CHAN_LEN)
    ci   = (ap - esa) / (0.015 * d)
    tci  = ema(ci, AVG_LEN)
    wt1  = tci
    wt2  = sma(wt1, 4)
    cross_up: wt1 > wt2 AND wt1[1] <= wt2[1] AND wt1 < 0  (cross from below zero)
    cross_dn: wt1 < wt2 AND wt1[1] >= wt2[1] AND wt1 > 0  (cross from above zero)
    """
    ap  = (df["high"] + df["low"] + df["close"]) / 3
    esa = ap.ewm(span=WT_CHAN_LEN, adjust=False).mean()
    d   = (ap - esa).abs().ewm(span=WT_CHAN_LEN, adjust=False).mean()
    ci  = (ap - esa) / (0.015 * d.replace(0, np.nan))
    tci = ci.ewm(span=WT_AVG_LEN, adjust=False).mean()
    wt1 = tci
    wt2 = wt1.rolling(4).mean()

    cross_up = (wt1 > wt2) & (wt1.shift() <= wt2.shift()) & (wt1 < 0)
    cross_dn = (wt1 < wt2) & (wt1.shift() >= wt2.shift()) & (wt1 > 0)
    return wt1, wt2, cross_up, cross_dn


# ══════════════════════════════════════════════════════════
# BOLLINGER BANDS — BB+RSI (rouxam / 3commas DCA)
# ══════════════════════════════════════════════════════════
def calc_bb(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    basis = sma(close, BB_PERIOD)
    upper = basis + BB_STD * stdev(close, BB_PERIOD)
    lower = basis - BB_STD * stdev(close, BB_PERIOD)
    buy  = close < lower AND rsi < BB_RSI_OB   (oversold at lower band)
    sell = close > upper AND rsi > (100-BB_RSI_OB)  (overbought at upper band)
    """
    c     = df["close"]
    basis = c.rolling(BB_PERIOD).mean()
    dev   = c.rolling(BB_PERIOD).std()
    upper = basis + BB_STD * dev
    lower = basis - BB_STD * dev
    return upper, lower, basis


# ══════════════════════════════════════════════════════════
# BJ BOT — R:R Targets (3Commas framework)
# ══════════════════════════════════════════════════════════
def calc_rr_targets(entry: float, side: str,
                    swing_low: float, swing_high: float,
                    atr: float) -> Tuple[float, float, float]:
    """
    Traducción directa de Bj Bot:
      longStop  = lowestLow  - atr * RiskM
      shortStop = highestHigh + atr * RiskM
      longRisk  = entry - longStop
      longlimit = entry + RnR * longRisk     ← TP2 basado en R:R
      TP1       = entry + (longlimit - entry) * 0.5  ← 50% del camino a TP2
    """
    if side == "long":
        stop   = min(swing_low  - atr * RISK_MULT, entry - atr * SL_ATR_MULT)
        risk   = entry - stop
        tp2    = entry + RNR * risk
        tp1    = entry + (tp2 - entry) * 0.5
    else:
        stop   = max(swing_high + atr * RISK_MULT, entry + atr * SL_ATR_MULT)
        risk   = stop - entry
        tp2    = entry - RNR * risk
        tp1    = entry - (entry - tp2) * 0.5
    return tp1, tp2, stop


# ══════════════════════════════════════════════════════════
# COMPUTE — todos los indicadores
# ══════════════════════════════════════════════════════════
def compute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l, v, o = df["close"], df["high"], df["low"], df["volume"], df["open"]

    # ── EMAs ──
    df["ema8"]   = ema(c, FAST_LEN)
    df["ema21"]  = ema(c, PIVOT_LEN)
    df["ema48"]  = ema(c, BIAS_LEN)
    df["ema200"] = ema(c, SLOW_LEN)
    df["atr"]    = calc_atr(df, ATR_LEN)
    df["rsi"]    = calc_rsi(c, RSI_LEN)

    # ── ADX ──
    dip, dim, adx = calc_adx(df, ADX_LEN)
    df["dip"] = dip; df["dim"] = dim; df["adx"] = adx

    # ── MACD ──
    macd, macd_sg, macd_h = calc_macd(c)
    df["macd_hist"]       = macd_h
    df["macd_bull"]       = (macd_h > 0) & (macd_h > macd_h.shift())
    df["macd_bear"]       = (macd_h < 0) & (macd_h < macd_h.shift())
    df["macd_cross_up"]   = (macd > macd_sg) & (macd.shift() <= macd_sg.shift())
    df["macd_cross_down"] = (macd < macd_sg) & (macd.shift() >= macd_sg.shift())

    # ── SMI ──
    smi_s, smi_sig = calc_smi(df)
    df["smi"]          = smi_s
    df["smi_signal"]   = smi_sig
    df["smi_cross_up"]   = (smi_s > smi_sig) & (smi_s.shift() <= smi_sig.shift())
    df["smi_cross_down"] = (smi_s < smi_sig) & (smi_s.shift() >= smi_sig.shift())
    df["smi_bull"]     = (smi_s > smi_sig) & (smi_s < SMI_OB)
    df["smi_bear"]     = (smi_s < smi_sig) & (smi_s > SMI_OS)
    df["smi_ob"]       = smi_s >= SMI_OB
    df["smi_os"]       = smi_s <= SMI_OS
    df["smi_exit_ob"]  = (smi_s < SMI_OB) & (smi_s.shift() >= SMI_OB)
    df["smi_exit_os"]  = (smi_s > SMI_OS) & (smi_s.shift() <= SMI_OS)

    # ── UTBot ──
    ut_stop, ut_buy, ut_sell = calc_utbot(df)
    df["utbot_stop"] = ut_stop
    df["utbot_buy"]  = ut_buy
    df["utbot_sell"] = ut_sell

    # ── WaveTrend ──
    wt1, wt2, wt_cross_up, wt_cross_dn = calc_wavetrend(df)
    df["wt1"]          = wt1
    df["wt2"]          = wt2
    df["wt_cross_up"]  = wt_cross_up
    df["wt_cross_dn"]  = wt_cross_dn
    df["wt_bull"]      = (wt1 > wt2) & (wt1 < WT_OB)
    df["wt_bear"]      = (wt1 < wt2) & (wt1 > WT_OS)
    df["wt_ob"]        = wt1 >= WT_OB
    df["wt_os"]        = wt1 <= WT_OS

    # ── Bollinger Bands ──
    bb_up, bb_lo, bb_basis = calc_bb(df)
    df["bb_upper"] = bb_up
    df["bb_lower"] = bb_lo
    df["bb_basis"] = bb_basis
    # BB signal: precio toca banda inferior con RSI no sobrecomprado
    df["bb_buy"]  = (c < bb_lo) & (df["rsi"] < BB_RSI_OB)
    df["bb_sell"] = (c > bb_up) & (df["rsi"] > (100 - BB_RSI_OB))
    # Squeeze: BB dentro de Keltner
    kc_up         = df["ema21"] + 2.0 * df["atr"]
    df["squeeze"] = bb_up < kc_up
    bb_w          = (bb_up - bb_lo) / df["ema21"].replace(0, np.nan)
    df["bb_width"]    = bb_w

    # ── MA cross (Bj Bot) — usa ema8 vs ema21 ──
    df["ma_cross_up"]  = (df["ema8"] > df["ema21"]) & (df["ema8"].shift() <= df["ema21"].shift())
    df["ma_cross_down"]= (df["ema8"] < df["ema21"]) & (df["ema8"].shift() >= df["ema21"].shift())

    # ── Oscilador ──
    df["osc"]    = ema(((c - df["ema21"]) / (3.0 * df["atr"].replace(0,np.nan))) * 100, OSC_LEN)
    df["osc_up"] = (df["osc"] > 0) & (df["osc"].shift() <= 0)
    df["osc_dn"] = (df["osc"] < 0) & (df["osc"].shift() >= 0)

    # ── Tendencia ──
    df["is_trending"] = (adx > ADX_MIN) & (bb_w > sma(bb_w, 20) * 0.8)

    # ── Volumen ──
    rng            = (h - l).replace(0, np.nan)
    df["buy_vol"]  = v * (c - l) / rng
    df["sell_vol"] = v * (h - c) / rng
    df["vol_ma"]   = sma(v, VOL_LEN)
    df["vol_spike"]= v > df["vol_ma"] * 1.05
    df["vol_bull"] = df["buy_vol"] > df["sell_vol"]
    df["vol_bear"] = df["sell_vol"] > df["buy_vol"]

    # ── Velas ──
    body              = (c - o).abs()
    body_pct          = body / rng.replace(0, np.nan)
    df["bull_candle"] = (c > o) & (body_pct >= 0.30)
    df["bear_candle"] = (c < o) & (body_pct >= 0.30)
    prev_body = (o.shift() - c.shift()).abs()
    df["bull_engulf"] = (c > o) & (o <= c.shift()) & (c >= o.shift()) & (body > prev_body * 0.8)
    df["bear_engulf"] = (c < o) & (o >= c.shift()) & (c <= o.shift()) & (body > prev_body * 0.8)

    # ── Swing H/L ──
    df["swing_low"]  = l.rolling(SWING_LB).min()
    df["swing_high"] = h.rolling(SWING_LB).max()

    # ── Divergencias RSI ──
    rsi = df["rsi"]
    df["bull_div"] = (
        (l < l.shift(1)) & (l.shift(1) < l.shift(2)) &
        (rsi > rsi.shift(1)) & (rsi.shift(1) > rsi.shift(2)) & (rsi < 42)
    )
    df["bear_div"] = (
        (h > h.shift(1)) & (h.shift(1) > h.shift(2)) &
        (rsi < rsi.shift(1)) & (rsi.shift(1) < rsi.shift(2)) & (rsi > 58)
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
# SCORE 16 PUNTOS — v13 integración completa
# ══════════════════════════════════════════════════════════
def confluence_score(row: pd.Series,
                     htf1_bull: bool, htf1_bear: bool,
                     htf2_bull: bool, htf2_bear: bool,
                     uptrend: bool) -> Tuple[int, int]:
    """
    16 puntos por dirección:
    
    LONG:
     1. EMA trend alcista
     2. Oscilador cruza al alza
     3. HTF1 bias alcista
     4. HTF2 macro alcista
     5. ADX con DI+ > DI-
     6. RSI en zona sana
     7. Volumen comprador + spike
     8. Vela alcista + close > ema21
     9. MACD alcista o cruce
    10. SMI cross up / bull
    11. SMI en OS o saliendo
    12. Bull engulf / div RSI
    13. UTBot BUY signal       ← nuevo (HPotter)
    14. WaveTrend cross up / OS ← nuevo (Instrument-Z)
    15. MA cross alcista        ← nuevo (Bj Bot)
    16. BB buy signal           ← nuevo (rouxam BB+RSI)

    SHORT: lógica espejada
    """
    rsi = float(row["rsi"])

    # ─── LONG ─────────────────────────────────────────────
    l1  = bool(row["close"] > row["ema48"] and row["ema8"] > row["ema21"])
    l2  = bool(row["osc_up"])
    l3  = htf1_bull
    l4  = htf2_bull
    l5  = bool(row["adx"] > ADX_MIN and row["dip"] > row["dim"])
    l6  = bool(42 <= rsi <= 78)
    l7  = bool(row["vol_bull"] and row["vol_spike"] and not row["squeeze"])
    l8  = bool(row["bull_candle"] and row["close"] > row["ema21"])
    l9  = bool(row["macd_bull"] or row["macd_cross_up"])
    l10 = bool(row.get("smi_cross_up") or row.get("smi_bull"))
    l11 = bool(row.get("smi_os")       or row.get("smi_exit_os"))
    l12 = bool(row["bull_engulf"]      or row["bull_div"])
    l13 = bool(row.get("utbot_buy"))                                      # UTBot
    l14 = bool(row.get("wt_cross_up")  or row.get("wt_os") or            # WaveTrend
               (row.get("wt_bull") and not row.get("wt_ob")))
    l15 = bool(row.get("ma_cross_up"))                                    # Bj Bot MA cross
    l16 = bool(row.get("bb_buy") and not row.get("squeeze"))              # BB + RSI

    # ─── SHORT ────────────────────────────────────────────
    s1  = bool(row["close"] < row["ema48"] and row["ema8"] < row["ema21"])
    s2  = bool(row["osc_dn"])
    s3  = htf1_bear
    s4  = htf2_bear
    s5  = bool(row["adx"] > ADX_MIN and row["dim"] > row["dip"])
    s6  = bool(22 <= rsi <= 58)
    s7  = bool(row["vol_bear"] and row["vol_spike"] and not row["squeeze"])
    s8  = bool(row["bear_candle"] and row["close"] < row["ema21"])
    s9  = bool(row["macd_bear"]   or row["macd_cross_down"])
    s10 = bool(row.get("smi_cross_down") or row.get("smi_bear"))
    s11 = bool(row.get("smi_ob")         or row.get("smi_exit_ob"))
    s12 = bool(row["bear_engulf"]        or row["bear_div"])
    s13 = bool(row.get("utbot_sell"))                                     # UTBot
    s14 = bool(row.get("wt_cross_dn")    or row.get("wt_ob") or          # WaveTrend
               (row.get("wt_bear") and not row.get("wt_os")))
    s15 = bool(row.get("ma_cross_down"))                                  # Bj Bot MA cross
    s16 = bool(row.get("bb_sell") and not row.get("squeeze"))             # BB + RSI

    return (sum([l1,l2,l3,l4,l5,l6,l7,l8,l9,l10,l11,l12,l13,l14,l15,l16]),
            sum([s1,s2,s3,s4,s5,s6,s7,s8,s9,s10,s11,s12,s13,s14,s15,s16]))


# ══════════════════════════════════════════════════════════
# BTC BIAS
# ══════════════════════════════════════════════════════════
def update_btc_bias(ex: ccxt.Exchange):
    try:
        df  = fetch_df(ex, "BTC/USDT:USDT", "1h", limit=250)
        df  = compute(df)
        row = df.iloc[-2]
        state.btc_bull = bool(row["close"] > row["ema48"] and row["ema48"] > row["ema200"])
        state.btc_bear = bool(row["close"] < row["ema48"] and row["ema48"] < row["ema200"])
        state.btc_rsi  = float(row["rsi"])
        log.info(
            f"BTC: {'BULL' if state.btc_bull else 'BEAR' if state.btc_bear else 'NEUTRAL'} "
            f"RSI:{state.btc_rsi:.1f} "
            f"SMI:{float(row.get('smi',0)):.1f} "
            f"WT:{float(row.get('wt1',0)):.1f} "
            f"UTBot:{'BUY' if row.get('utbot_buy') else 'SELL' if row.get('utbot_sell') else '-'}"
        )
    except Exception as e:
        log.warning(f"BTC bias: {e}")


# ══════════════════════════════════════════════════════════
# EXCHANGE
# ══════════════════════════════════════════════════════════
def build_exchange() -> ccxt.Exchange:
    ex = ccxt.bingx({
        "apiKey": API_KEY, "secret": API_SECRET,
        "options": {"defaultType": "swap"},
        "enableRateLimit": True,
    })
    ex.load_markets()
    return ex

def detect_hedge_mode(ex: ccxt.Exchange) -> bool:
    try:
        for p in ex.fetch_positions()[:5]:
            if p.get("info", {}).get("positionSide", "") in ("LONG", "SHORT"):
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
        log.warning(f"fetch_positions: {e}")
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
        return {"positionSide": "LONG" if trade_side == "long" else "SHORT",
                "reduceOnly": True}
    return {"reduceOnly": True}


# ══════════════════════════════════════════════════════════
# UNIVERSO
# ══════════════════════════════════════════════════════════
def get_symbols(ex: ccxt.Exchange) -> List[str]:
    candidates = []
    for sym, mkt in ex.markets.items():
        if not (mkt.get("swap") and mkt.get("quote") == "USDT"
                and mkt.get("active", True)):
            continue
        if sym in BLACKLIST: continue
        candidates.append(sym)

    if not candidates:
        log.warning("Sin candidatos de mercado")
        return []

    log.info(f"Obteniendo tickers para {len(candidates)} pares...")
    try:
        tickers = ex.fetch_tickers(candidates)
    except Exception as e:
        log.warning(f"fetch_tickers: {e}")
        return candidates[:TOP_N_SYMBOLS]

    ranked = []
    for sym in candidates:
        tk  = tickers.get(sym, {})
        vol = float(tk.get("quoteVolume", 0) or 0)
        if vol >= MIN_VOLUME_USDT:
            info    = ex.markets.get(sym, {}).get("info", {})
            created = info.get("onboardDate", 0) or info.get("deliveryDate", 0)
            is_new  = False
            if created:
                try:
                    age_days = (time.time() - float(created) / 1000) / 86400
                    is_new   = age_days < 30
                except Exception:
                    pass
            ranked.append((sym, vol, is_new))

    ranked.sort(key=lambda x: (not x[2], -x[1]))
    result = [s for s, _, _ in ranked]
    if TOP_N_SYMBOLS > 0:
        result = result[:TOP_N_SYMBOLS]

    new_count = sum(1 for _, _, n in ranked[:len(result)] if n)
    log.info(f"Universo: {len(result)} pares "
             f"(vol≥${MIN_VOLUME_USDT/1000:.0f}K, {new_count} nuevos primero)")
    return result


# ══════════════════════════════════════════════════════════
# APERTURA DE POSICIÓN — con targets Bj Bot (R:R)
# ══════════════════════════════════════════════════════════
def open_trade(ex: ccxt.Exchange, symbol: str, base: str,
               side: str, score: int, row: pd.Series,
               uptrend: bool) -> Optional[TradeState]:
    try:
        spread = get_spread_pct(ex, symbol)
        if spread > MAX_SPREAD_PCT:
            log.warning(f"[{symbol}] spread {spread:.3f}% > {MAX_SPREAD_PCT}% — skip")
            return None

        price   = get_last_price(ex, symbol)
        atr     = float(row["atr"])
        smi_v   = float(row.get("smi", 0.0))
        wt_v    = float(row.get("wt1", 0.0))
        ut_stop = float(row.get("utbot_stop", 0.0))
        usdt    = FIXED_USDT * state.risk_mult()
        raw_amt = usdt / price
        amount  = float(ex.amount_to_precision(symbol, raw_amt))

        min_amt = get_min_amount(ex, symbol)
        if amount <= 0 or amount < min_amt:
            log.warning(f"[{symbol}] amount {amount:.6f} < min {min_amt}")
            return None
        if amount * price < 3:
            log.warning(f"[{symbol}] notional ${amount*price:.2f} < $3")
            return None

        log.info(f"[OPEN] {symbol} {side.upper()} score={score}/16 "
                 f"SMI={smi_v:.1f} WT={wt_v:.1f} ${usdt:.1f} @ {price:.6g}")

        order       = ex.create_order(symbol, "market", side, amount,
                                      params=entry_params(side))
        entry_price = float(order.get("average") or price)
        trade_side  = "long" if side == "buy" else "short"

        # ── Targets Bj Bot (R:R) ──
        tp1_p, tp2_p, sl_p = calc_rr_targets(
            entry_price, trade_side,
            float(row["swing_low"]), float(row["swing_high"]), atr
        )

        tp1_p = float(ex.price_to_precision(symbol, tp1_p))
        tp2_p = float(ex.price_to_precision(symbol, tp2_p))
        sl_p  = float(ex.price_to_precision(symbol, sl_p))

        # R:R trail trigger (Bj Bot rrExit)
        if trade_side == "long":
            rr_trigger = entry_price + (tp2_p - entry_price) * RR_EXIT
        else:
            rr_trigger = entry_price - (entry_price - tp2_p) * RR_EXIT

        close_side = "sell" if side == "buy" else "buy"
        half       = float(ex.amount_to_precision(symbol, amount * 0.5))
        ep         = exit_params(trade_side)

        for lbl, qty, px in [("TP1", half, tp1_p), ("TP2", half, tp2_p)]:
            try:
                ex.create_order(symbol, "limit", close_side, qty, px, ep)
                log.info(f"[{symbol}] {lbl} @ {px:.6g}")
            except Exception as e:
                log.warning(f"[{symbol}] {lbl}: {e}")

        try:
            sl_ep = {**ep, "stopPrice": sl_p}
            ex.create_order(symbol, "stop_market", close_side, amount, None, sl_ep)
            log.info(f"[{symbol}] SL @ {sl_p:.6g}")
        except Exception as e:
            log.warning(f"[{symbol}] SL: {e}")

        t = TradeState(
            symbol=symbol,       base=base,        side=trade_side,
            entry_price=entry_price,               tp1_price=tp1_p,
            tp2_price=tp2_p,     sl_price=sl_p,
            entry_score=score,   entry_time=utcnow(),
            contracts=amount,    atr_entry=atr,
            smi_entry=smi_v,     wt_entry=wt_v,
            utbot_stop=ut_stop,
            uptrend_entry=uptrend,
            rr_trail_stop=rr_trigger,
        )
        if side == "buy":
            t.trail_high = entry_price
            t.rr_trail_stop = rr_trigger
        else:
            t.trail_low  = entry_price
            t.rr_trail_stop = rr_trigger

        log_csv("OPEN", t, entry_price)
        tg_signal(t, row)
        return t

    except Exception as e:
        log.error(f"[{symbol}] open_trade: {e}")
        tg_error(f"open_trade {symbol}: {e}")
        return None


def move_be(ex: ccxt.Exchange, symbol: str):
    if symbol not in state.trades: return
    t = state.trades[symbol]
    if t.sl_moved_be: return
    try:
        ex.cancel_all_orders(symbol)
    except Exception as e:
        log.warning(f"[{symbol}] cancel for BE: {e}")
    be    = float(ex.price_to_precision(symbol, t.entry_price))
    ep    = {**exit_params(t.side), "stopPrice": be}
    cside = "sell" if t.side == "long" else "buy"
    try:
        ex.create_order(symbol, "stop_market", cside, t.contracts, None, ep)
        t.sl_price    = be
        t.sl_moved_be = True
        log.info(f"[{symbol}] BE @ {be:.6g}")
    except Exception as e:
        log.warning(f"[{symbol}] BE failed: {e}")


def close_trade(ex: ccxt.Exchange, symbol: str, reason: str, price: float):
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
            ex.create_order(symbol, "market", close_side, contracts,
                            params=exit_params(t.side))
            pnl = ((price - t.entry_price) if t.side == "long"
                   else (t.entry_price - price)) * contracts
        except Exception as e:
            log.error(f"[{symbol}] close: {e}")
            tg_error(f"close {symbol}: {e}")
            return

    if pnl > 0:
        state.wins += 1; state.gross_profit += pnl; state.consec_losses = 0
    elif pnl < 0:
        state.losses += 1; state.gross_loss += abs(pnl); state.consec_losses += 1

    state.total_pnl   += pnl
    state.daily_pnl   += pnl
    state.peak_equity  = max(state.peak_equity, state.peak_equity + pnl)
    state.set_cooldown(symbol)

    log_csv("CLOSE", t, price, pnl)
    tg_close(reason, t, price, pnl)
    del state.trades[symbol]


# ══════════════════════════════════════════════════════════
# GESTIÓN DEL TRADE — v13 con todas las capas de salida
# ══════════════════════════════════════════════════════════
def manage_trade(ex: ccxt.Exchange, symbol: str,
                 live_price: float, atr: float,
                 long_score: int, short_score: int,
                 live_pos: Optional[dict],
                 result: Optional[dict] = None):

    if symbol not in state.trades: return
    t = state.trades[symbol]
    t.bar_count += 1

    # ── Posición cerrada externamente (SL/TP ejecutado) ──
    if live_pos is None:
        pnl = ((live_price - t.entry_price) if t.side == "long"
               else (t.entry_price - live_price)) * t.contracts
        reason = ("TP2 ALCANZADO"
                  if (t.side=="long"  and live_price >= t.tp2_price) or
                     (t.side=="short" and live_price <= t.tp2_price)
                  else "SL ALCANZADO")
        if pnl > 0:
            state.wins += 1; state.gross_profit += pnl; state.consec_losses = 0
        else:
            state.losses += 1; state.gross_loss += abs(pnl); state.consec_losses += 1
        state.total_pnl += pnl; state.daily_pnl += pnl
        state.set_cooldown(symbol)
        log_csv("CLOSE_EXT", t, live_price, pnl)
        tg_close(reason, t, live_price, pnl)
        del state.trades[symbol]
        return

    # ── Trade Expiration (Instrument-Z) ──
    if TRADE_EXPIRE_BARS > 0 and t.bar_count >= TRADE_EXPIRE_BARS:
        close_trade(ex, symbol, f"EXPIRADO ({t.bar_count} barras)", live_price)
        return

    # ── UTBot trailing stop como 2ª línea de defensa ──
    # Si el precio cruza la línea UTBot en dirección contraria → cierre
    if result is not None and symbol in state.trades:
        row = result.get("row")
        if row is not None:
            ut_stop_now = float(row.get("utbot_stop", 0.0))
            if t.side == "long" and ut_stop_now > 0:
                # UTBot sell signal activo Y precio bajo el stop
                if bool(row.get("utbot_sell")) and live_price < ut_stop_now:
                    if t.tp1_hit:  # solo si ya está en profit
                        close_trade(ex, symbol, "UTBOT TRAILING STOP", live_price)
                        return
            elif t.side == "short" and ut_stop_now > 0:
                if bool(row.get("utbot_buy")) and live_price > ut_stop_now:
                    if t.tp1_hit:
                        close_trade(ex, symbol, "UTBOT TRAILING STOP", live_price)
                        return

    # ── Cierre por pérdida dinámica (antes de TP1) ──
    if not t.tp1_hit:
        atr_now   = atr if atr > 0 else t.atr_entry
        loss_dist = (t.entry_price - live_price if t.side == "long"
                     else live_price - t.entry_price)
        if loss_dist >= atr_now * 0.8:
            close_trade(ex, symbol, "PÉRDIDA DINÁMICA", live_price)
            return

    # ── Agotamiento (7 señales incluyendo SMI, WT, UTBot) ──
    if result is not None and symbol in state.trades:
        row = result.get("row")
        if row is not None:
            try:
                in_profit = ((t.side == "long"  and live_price > t.entry_price) or
                             (t.side == "short" and live_price < t.entry_price))
                if in_profit:
                    rsi_v     = float(row["rsi"])
                    adx_v     = float(row["adx"])
                    vol_ratio = float(row["volume"]) / max(float(row["vol_ma"]), 1)
                    smi_now   = float(row.get("smi", 0.0))
                    wt_now    = float(row.get("wt1", 0.0))
                    if t.side == "long":
                        e1 = bool(row["macd_bear"])
                        e2 = adx_v < 20
                        e3 = vol_ratio < 0.7
                        e4 = bool(row["bear_div"])
                        e5 = bool(row["osc_dn"])
                        e6 = rsi_v > 72
                        e7 = bool(row.get("smi_ob") or row.get("smi_cross_down"))
                        e8 = bool(row.get("wt_ob") or row.get("wt_cross_dn"))
                        e9 = bool(row.get("utbot_sell"))
                    else:
                        e1 = bool(row["macd_bull"])
                        e2 = adx_v < 20
                        e3 = vol_ratio < 0.7
                        e4 = bool(row["bull_div"])
                        e5 = bool(row["osc_up"])
                        e6 = rsi_v < 28
                        e7 = bool(row.get("smi_os") or row.get("smi_cross_up"))
                        e8 = bool(row.get("wt_os") or row.get("wt_cross_up"))
                        e9 = bool(row.get("utbot_buy"))
                    exh = sum([e1,e2,e3,e4,e5,e6,e7,e8,e9])
                    if exh >= 5:
                        profit = ((live_price - t.entry_price) if t.side == "long"
                                  else (t.entry_price - live_price)) * t.contracts

                        # Minimum profit check (Instrument-Z)
                        min_profit_usdt = t.entry_price * t.contracts * MIN_PROFIT_PCT
                        if profit < min_profit_usdt:
                            pass  # no cerrar si no alcanza mínimo
                        else:
                            tg(
                                f"🏁 <b>AGOTAMIENTO</b> — {symbol}\n"
                                f"Señales: {exh}/9 | ${profit:+.2f}\n"
                                f"{'✅' if e1 else '❌'} MACD  {'✅' if e2 else '❌'} ADX\n"
                                f"{'✅' if e3 else '❌'} Vol↓  {'✅' if e4 else '❌'} DivRSI\n"
                                f"{'✅' if e5 else '❌'} OSC   {'✅' if e6 else '❌'} RSIext\n"
                                f"{'✅' if e7 else '❌'} SMI{smi_now:.1f} "
                                f"{'✅' if e8 else '❌'} WT{wt_now:.1f} "
                                f"{'✅' if e9 else '❌'} UTBot\n"
                                f"⏰ {utcnow()}"
                            )
                            close_trade(ex, symbol, "AGOTAMIENTO", live_price)
                            return
            except Exception as e:
                log.debug(f"[{symbol}] agotamiento: {e}")

    # ── TP1 → Break-Even ──
    if not t.tp1_hit:
        hit = ((t.side == "long"  and live_price >= t.tp1_price) or
               (t.side == "short" and live_price <= t.tp1_price))
        if hit:
            t.tp1_hit    = True
            t.peak_price = live_price
            t.prev_price = live_price
            contracts    = float(live_pos.get("contracts", 0))
            pnl_est      = abs(t.tp1_price - t.entry_price) * contracts * 0.5
            move_be(ex, symbol)
            tg_tp1_be(t, live_price, pnl_est)

    # ── R:R Trail trigger (Bj Bot rrExit) ──
    if t.tp1_hit and symbol in state.trades and not t.rr_trail_active:
        triggered = ((t.side == "long"  and live_price >= t.rr_trail_stop) or
                     (t.side == "short" and live_price <= t.rr_trail_stop))
        if triggered:
            t.rr_trail_active = True
            tg(
                f"📐 <b>R:R TRAIL ACTIVADO</b> — {symbol}\n"
                f"Precio trigger: <code>{t.rr_trail_stop:.6g}</code>\n"
                f"R:R={RNR} | {RR_EXIT*100:.0f}% del camino al TP2\n"
                f"⏰ {utcnow()}"
            )

    # ── Trailing stop (3 fases + R:R) ──
    if t.tp1_hit and symbol in state.trades:
        atr_t = atr if atr > 0 else t.atr_entry

        if t.side == "long":
            cur_pct = (live_price - t.entry_price) / t.entry_price * 100
        else:
            cur_pct = (t.entry_price - live_price) / t.entry_price * 100
        t.max_profit_pct = max(t.max_profit_pct, cur_pct)

        new_peak = (live_price > t.peak_price if t.side == "long"
                    else live_price < t.peak_price)
        if new_peak:
            t.peak_price  = live_price
            t.stall_count = 0
        else:
            t.stall_count += 1

        denom = abs(t.peak_price - t.entry_price)
        if t.side == "long":
            retrace = (t.peak_price - live_price) / max(denom, 1e-9) * 100
        else:
            retrace = (live_price - t.peak_price) / max(denom, 1e-9) * 100

        prev_phase = t.trail_phase
        # R:R trail activo → fase más agresiva
        if t.rr_trail_active and retrace > 15:
            t.trail_phase = "locked"
        elif retrace > 30:
            t.trail_phase = "locked"
        elif t.stall_count >= 3:
            t.trail_phase = "tight"
        else:
            t.trail_phase = "normal"

        trail_m = {"normal": 0.8, "tight": 0.4, "locked": 0.2}[t.trail_phase]

        if t.trail_phase != prev_phase:
            tg_trail_phase(t, t.trail_phase, live_price, retrace, trail_m)

        if t.side == "long":
            t.trail_high = max(t.trail_high, live_price)
            if live_price <= t.trail_high - atr_t * trail_m:
                close_trade(ex, symbol, f"TRAILING {t.trail_phase.upper()}", live_price)
                return
        else:
            t.trail_low = min(t.trail_low, live_price)
            if live_price >= t.trail_low + atr_t * trail_m:
                close_trade(ex, symbol, f"TRAILING {t.trail_phase.upper()}", live_price)
                return

        t.prev_price = live_price

    # ── Flip de dirección ──
    if symbol in state.trades:
        if t.side == "long"  and short_score >= MIN_SCORE + 2:
            close_trade(ex, symbol, "FLIP LONG→SHORT", live_price)
        elif t.side == "short" and long_score >= MIN_SCORE + 2:
            close_trade(ex, symbol, "FLIP SHORT→LONG", live_price)


# ══════════════════════════════════════════════════════════
# SCAN DE UN SÍMBOLO
# ══════════════════════════════════════════════════════════
def scan_symbol(ex: ccxt.Exchange, symbol: str) -> Optional[dict]:
    try:
        df  = fetch_df(ex, symbol, TF,   400)
        df1 = fetch_df(ex, symbol, HTF1, 200)
        df2 = fetch_df(ex, symbol, HTF2, 300)

        df  = compute(df)
        row = df.iloc[-2]

        # Validar que los indicadores están disponibles
        for col in ["adx", "rsi", "macd_hist", "smi", "wt1", "utbot_stop"]:
            if pd.isna(row.get(col, np.nan)):
                return None

        htf1_bull, htf1_bear = htf_bias(df1)
        htf2_bull, htf2_bear = htf2_macro(df2)

        # UpTrend: precio sobre EMA200
        uptrend = bool(row["close"] > row["ema200"])

        ls, ss = confluence_score(row, htf1_bull, htf1_bear, htf2_bull, htf2_bear, uptrend)

        rsi_v = float(row["rsi"])
        smi_v = float(row.get("smi", 0.0))
        wt_v  = float(row.get("wt1", 0.0))

        if rsi_extreme_long(rsi_v) or rsi_extreme_short(rsi_v):
            now  = time.time()
            last = state.rsi_alerts.get(symbol, 0)
            if now - last > 1800:
                state.rsi_alerts[symbol] = now
                tg_rsi_alert(symbol, rsi_v, smi_v, wt_v, ls, ss, float(row["close"]))

        return {
            "symbol":      symbol,
            "base":        symbol.split("/")[0],
            "long_score":  ls,
            "short_score": ss,
            "row":         row,
            "atr":         float(row["atr"]),
            "live_price":  float(row["close"]),
            "is_trending": bool(row["is_trending"]),
            "rsi":         rsi_v,
            "smi":         smi_v,
            "wt":          wt_v,
            "uptrend":     uptrend,
        }
    except Exception as e:
        log.debug(f"[{symbol}] scan: {e}")
        return None


# ══════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════
def main():
    global HEDGE_MODE

    log.info("=" * 65)
    log.info("  SATY ELITE v13 — FULL STRATEGY EDITION · 24/7")
    log.info("  UTBot + WaveTrend + Bj Bot R:R + BB+RSI + SMI")
    log.info("=" * 65)

    if not (API_KEY and API_SECRET):
        log.warning("DRY-RUN: sin claves API")
        while True: log.info("DRY-RUN..."); time.sleep(POLL_SECS)

    ex = None
    for attempt in range(10):
        try:
            ex = build_exchange()
            log.info("Exchange conectado ✓")
            break
        except Exception as e:
            wait = min(2 ** attempt, 120)
            log.warning(f"Conexión {attempt+1}/10: {e} — retry {wait}s")
            time.sleep(wait)

    if ex is None:
        raise RuntimeError("No se pudo conectar al exchange")

    HEDGE_MODE = detect_hedge_mode(ex)
    log.info(f"Modo cuenta: {'HEDGE' if HEDGE_MODE else 'ONE-WAY'}")

    balance = 0.0
    for i in range(10):
        try:
            balance = get_balance(ex)
            break
        except Exception as e:
            log.warning(f"get_balance {i+1}/10: {e}")
            time.sleep(5)

    state.peak_equity    = balance
    state.daily_reset_ts = time.time()
    log.info(f"Balance: ${balance:.2f} USDT")

    symbols: List[str] = []
    while not symbols:
        try:
            ex.load_markets()
            symbols = get_symbols(ex)
        except Exception as e:
            log.error(f"get_symbols: {e} — reintento 60s")
            time.sleep(60)

    update_btc_bias(ex)
    tg_startup(balance, len(symbols))

    scan_count    = 0
    REFRESH_EVERY = max(1, 3600 // max(POLL_SECS, 1))
    BTC_REFRESH   = max(1, 900  // max(POLL_SECS, 1))
    HB_INTERVAL   = 3600

    while True:
        ts_start = time.time()
        try:
            scan_count += 1
            state.reset_daily()
            clear_cache()

            log.info(
                f"━━━ SCAN #{scan_count} "
                f"{datetime.now(timezone.utc):%H:%M:%S} "
                f"| {len(symbols)} pares "
                f"| {state.open_count()}/{MAX_OPEN_TRADES} trades "
                f"| bases: {list(state.bases_open().keys())} ━━━"
            )

            if scan_count % REFRESH_EVERY == 0:
                try:
                    ex.load_markets(); symbols = get_symbols(ex)
                except Exception as e:
                    log.warning(f"Refresh: {e}")

            if scan_count % BTC_REFRESH == 0:
                update_btc_bias(ex)

            if time.time() - state.last_heartbeat > HB_INTERVAL:
                try:
                    tg_heartbeat(get_balance(ex))
                    state.last_heartbeat = time.time()
                except Exception:
                    pass

            if state.cb_active():
                log.warning(f"CIRCUIT BREAKER >= {CB_DD}%")
                time.sleep(POLL_SECS); continue

            if state.daily_limit_hit():
                log.warning(f"LÍMITE DIARIO >= {DAILY_LOSS_LIMIT}%")
                time.sleep(POLL_SECS); continue

            live_positions = get_all_positions(ex)

            # ── Gestionar trades abiertos ──
            for sym in list(state.trades.keys()):
                try:
                    lp  = live_positions.get(sym)
                    lp_ = float(lp["markPrice"]) if lp else get_last_price(ex, sym)
                    res = scan_symbol(ex, sym)
                    ls  = res["long_score"]  if res else 0
                    ss  = res["short_score"] if res else 0
                    atr = res["atr"]         if res else state.trades[sym].atr_entry
                    manage_trade(ex, sym, lp_, atr, ls, ss, lp, res)
                except Exception as e:
                    log.warning(f"[{sym}] manage: {e}")

            # ── Buscar nuevas entradas ──
            new_signals: List[dict] = []

            if state.open_count() < MAX_OPEN_TRADES:
                bases_open = state.bases_open()
                to_scan    = [
                    s for s in symbols
                    if s not in state.trades
                    and not state.in_cooldown(s)
                    and s.split("/")[0] not in bases_open
                ]

                log.info(f"Escaneando {len(to_scan)} pares "
                         f"(excluidas bases: {list(bases_open.keys())})")

                with ThreadPoolExecutor(max_workers=8) as pool:
                    futures = {pool.submit(scan_symbol, ex, s): s for s in to_scan}
                    results = [f.result() for f in as_completed(futures)
                               if f.result() is not None]

                for res in results:
                    base       = res["base"]
                    best_side  = None
                    best_score = 0
                    uptrend    = res["uptrend"]

                    can_long  = (res["long_score"]  >= MIN_SCORE and res["is_trending"])
                    can_short = (res["short_score"] >= MIN_SCORE and res["is_trending"])

                    if BTC_FILTER:
                        if state.btc_bear: can_long  = False
                        if state.btc_bull: can_short = False

                    if state.base_has_trade(base):
                        continue

                    if can_long  and res["long_score"]  > best_score:
                        best_score = res["long_score"];  best_side = "long"
                    if can_short and res["short_score"] > best_score:
                        best_score = res["short_score"]; best_side = "short"

                    if best_side:
                        new_signals.append({
                            "symbol":  res["symbol"],
                            "base":    base,
                            "side":    best_side,
                            "score":   best_score,
                            "row":     res["row"],
                            "rsi":     res["rsi"],
                            "smi":     res["smi"],
                            "wt":      res["wt"],
                            "uptrend": uptrend,
                        })

                new_signals.sort(key=lambda x: x["score"], reverse=True)

                for sig in new_signals:
                    if state.open_count() >= MAX_OPEN_TRADES: break
                    sym  = sig["symbol"]
                    base = sig["base"]
                    if sym in state.trades:        continue
                    if state.base_has_trade(base): continue
                    if state.in_cooldown(sym):      continue

                    order_side = "buy" if sig["side"] == "long" else "sell"
                    t = open_trade(ex, sym, base, order_side,
                                   sig["score"], sig["row"], sig["uptrend"])
                    if t:
                        state.trades[sym] = t

            else:
                log.info(f"Max trades alcanzado ({MAX_OPEN_TRADES})")

            elapsed = time.time() - ts_start
            log.info(
                f"✓ {elapsed:.1f}s | señales:{len(new_signals)} | "
                f"{state.wins}W/{state.losses}L | "
                f"hoy:${state.daily_pnl:+.2f} | total:${state.total_pnl:+.2f}"
            )

            if scan_count % 20 == 0:
                tg_summary(new_signals, len(symbols))

        except ccxt.NetworkError as e:
            log.warning(f"Network: {e} — 10s")
            time.sleep(10)
        except ccxt.ExchangeError as e:
            log.error(f"Exchange: {e}")
            tg(f"❌ Exchange: <code>{str(e)[:200]}</code>")
        except KeyboardInterrupt:
            log.info("Detenido.")
            tg("🛑 <b>Bot detenido.</b>")
            break
        except Exception as e:
            log.exception(f"Error: {e}")
            tg_error(str(e))

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
            try: tg_error(f"CRASH — reinicio en 30s:\n{e}")
            except Exception: pass
            log.info("Reiniciando en 30s...")
            time.sleep(30)
