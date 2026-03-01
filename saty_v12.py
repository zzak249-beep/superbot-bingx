"""
SATY ELITE v12 — BingX Real Money
══════════════════════════════════════════════════════════════
MEJORAS vs v11:
  🐛 BUG 1: `is_trending` era filtro HARD → ahora es bonus de +1 punto
  🐛 BUG 2: BTC_FILTER bloqueaba TODOS los shorts en mercado alcista
  🐛 BUG 3: BB width require 80% de media → umbral bajado a 50%
  🐛 BUG 4: ADX_MIN=16 muy alto para crypto volátil → 14
  ✨ NUEVO: Estrategia BREAKOUT (precio rompiendo rango con volumen)
  ✨ NUEVO: Estrategia MOMENTUM BURST (aceleración de impulso)
  ✨ NUEVO: TP triple (TP1 25%, TP2 25%, TP3 50%) para maximizar ganancia
  ✨ NUEVO: Trailing ultra-ajustado cuando ganancia > 5%
  ✨ NUEVO: Score mínimo adaptativo (sube en mercado lateral)
  ✨ NUEVO: Telegram resumen con gráfico ASCII de equity
  ✨ NUEVO: Top 5 señales descartadas por filtro (debugging)
  ✨ NUEVO: Alerta cuando score alto pero bloqueado (análisis)

Variables de entorno Railway:
  BINGX_API_KEY, BINGX_API_SECRET
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  FIXED_USDT          (def: 8)
  MAX_OPEN_TRADES     (def: 12)
  MIN_SCORE           (def: 3)        ← bajado de 4
  MAX_DRAWDOWN        (def: 15)
  DAILY_LOSS_LIMIT    (def: 8)
  BTC_FILTER          (def: true)     ← ahora más permisivo
  COOLDOWN_MIN        (def: 20)
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

# ══════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("saty_v12")

# ══════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════
API_KEY    = os.environ.get("BINGX_API_KEY",    "")
API_SECRET = os.environ.get("BINGX_API_SECRET", "")
TF         = os.environ.get("TIMEFRAME",  "5m")
HTF1       = os.environ.get("HTF1",       "15m")
HTF2       = os.environ.get("HTF2",       "1h")
POLL_SECS  = int(os.environ.get("POLL_SECONDS",  "60"))

def _tg_token():   return os.environ.get("TELEGRAM_BOT_TOKEN", "") or os.environ.get("TG_TOKEN", "")
def _tg_chat_id(): return os.environ.get("TELEGRAM_CHAT_ID", "")   or os.environ.get("TG_CHAT_ID", "")

_bl = os.environ.get("BLACKLIST", "")
BLACKLIST: List[str] = [s.strip() for s in _bl.split(",") if s.strip()]

FIXED_USDT       = float(os.environ.get("FIXED_USDT",       "8.0"))
MAX_OPEN_TRADES  = int(os.environ.get("MAX_OPEN_TRADES",    "12"))
MIN_SCORE        = int(os.environ.get("MIN_SCORE",          "3"))    # ← BAJADO de 4
CB_DD            = float(os.environ.get("MAX_DRAWDOWN",     "15.0"))
DAILY_LOSS_LIMIT = float(os.environ.get("DAILY_LOSS_LIMIT", "8.0"))
COOLDOWN_MIN     = int(os.environ.get("COOLDOWN_MIN",       "20"))
MAX_SPREAD_PCT   = float(os.environ.get("MAX_SPREAD_PCT",   "1.2"))  # ← SUBIDO ligeramente
MIN_VOLUME_USDT  = float(os.environ.get("MIN_VOLUME_USDT",  "5000")) # ← BAJADO de 10000
TOP_N_SYMBOLS    = int(os.environ.get("TOP_N_SYMBOLS",      "300"))
BTC_FILTER       = os.environ.get("BTC_FILTER", "true").lower() == "true"
LEVERAGE         = float(os.environ.get("LEVERAGE", "10"))

# ── Indicadores ─────────────────────────────────────────
FAST_LEN  = 8;   PIVOT_LEN = 21; BIAS_LEN  = 48; SLOW_LEN  = 200
ADX_LEN   = 14;  ADX_MIN   = 14  # ← BAJADO de 16
RSI_LEN   = 14;  ATR_LEN   = 14
VOL_LEN   = 20;  OSC_LEN   = 3;  SWING_LB  = 10
MACD_FAST = 12;  MACD_SLOW = 26; MACD_SIG  = 9; STOCH_LEN = 14

# ── Niveles RSI extremo ──────────────────────────────────
RSI_OB_LOW  = 10; RSI_OB_HIGH = 28   # ← SUBIDO de 25 a 28 (más señales)
RSI_OS_LOW  = 72; RSI_OS_HIGH = 90   # ← BAJADO de 78 a 72 (más señales)

# ── TP/SL ────────────────────────────────────────────────
TP1_MULT  = 1.0   # ← BAJADO de 1.2 (TP1 más fácil de alcanzar)
TP2_MULT  = 2.0   # ← BAJADO de 3.0 (TP2 realista)
TP3_MULT  = 4.0   # ← NUEVO: TP3 para maximizar ganancia
SL_ATR    = 1.0
MAX_CONSEC_LOSS = 3
HEDGE_MODE: bool = False
CSV_PATH = "saty_v12_trades.csv"

# ── Equity histórico para gráfico ────────────────────────
equity_history: deque = deque(maxlen=48)   # últimas 48 muestras (~48h)

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

def clear_cache():
    _cache.clear()

# ══════════════════════════════════════════════════════════
# ESTADO
# ══════════════════════════════════════════════════════════
@dataclass
class TradeState:
    symbol:         str   = ""
    side:           str   = ""
    base:           str   = ""
    entry_price:    float = 0.0
    tp1_price:      float = 0.0
    tp2_price:      float = 0.0
    tp3_price:      float = 0.0      # ← NUEVO
    sl_price:       float = 0.0
    sl_moved_be:    bool  = False
    tp1_hit:        bool  = False
    tp2_hit:        bool  = False    # ← NUEVO
    trail_high:     float = 0.0
    trail_low:      float = 0.0
    peak_price:     float = 0.0
    stall_count:    int   = 0
    trail_phase:    str   = "normal"
    max_profit_pct: float = 0.0
    entry_score:    int   = 0
    strategy:       str   = "confluence"  # ← NUEVO: qué estrategia disparó
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
    rsi_alerts:     Dict[str, float]      = field(default_factory=dict)
    btc_bull:       bool  = True
    btc_bear:       bool  = False
    btc_rsi:        float = 50.0
    btc_adx:        float = 0.0       # ← NUEVO: fuerza de tendencia BTC
    scan_count:     int   = 0
    signals_found:  int   = 0         # ← NUEVO: total señales detectadas
    signals_blocked: int  = 0         # ← NUEVO: señales bloqueadas por filtros
    # ← NUEVO: registro de señales descartadas para debug
    last_discarded: List[dict] = field(default_factory=list)

    def open_count(self): return len(self.trades)
    def bases_open(self): return {t.base: t.side for t in self.trades.values()}
    def base_has_trade(self, base): return base in self.bases_open()
    def win_rate(self):
        t = self.wins + self.losses
        return (self.wins / t * 100) if t else 0.0
    def profit_factor(self):
        return (self.gross_profit / self.gross_loss) if self.gross_loss else 0.0
    def score_bar(self, score, mx=13):
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
            self.daily_pnl      = 0.0
            self.daily_reset_ts = now
            log.info("Daily PnL reseteado")
    def btc_strength(self):
        # ← NUEVO: BTC en tendencia fuerte (no bloquear shorts cuando btc débil)
        return self.btc_adx > 20

state = BotState()

# ══════════════════════════════════════════════════════════
# CSV LOG
# ══════════════════════════════════════════════════════════
def log_csv(action, t, price, pnl=0.0):
    try:
        exists = os.path.exists(CSV_PATH)
        with open(CSV_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["ts","action","symbol","side","strategy","score",
                            "entry","exit","pnl","contracts"])
            w.writerow([utcnow(), action, t.symbol, t.side,
                        t.strategy, t.entry_score,
                        t.entry_price, price, round(pnl, 4), t.contracts])
    except Exception as e:
        log.warning(f"CSV: {e}")

# ══════════════════════════════════════════════════════════
# UTILIDADES
# ══════════════════════════════════════════════════════════
def utcnow():
    return datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

def equity_chart():
    """Gráfico ASCII de equity para Telegram."""
    if len(equity_history) < 3:
        return ""
    vals  = list(equity_history)
    lo, hi = min(vals), max(vals)
    rng   = hi - lo
    if rng < 0.01:
        return "──── (sin variación) ────"
    rows, cols = 4, min(len(vals), 24)
    vals  = vals[-cols:]
    chart = []
    for row_i in range(rows - 1, -1, -1):
        line = ""
        threshold = lo + rng * (row_i / (rows - 1))
        for v in vals:
            line += "█" if v >= threshold else " "
        chart.append(f"|{line}|")
    chart.append(f" {vals[0]:+.2f}→{vals[-1]:+.2f} USDT")
    return "\n".join(chart)

# ══════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════
def tg(msg: str, parse_mode="HTML"):
    token   = _tg_token()
    chat_id = _tg_chat_id()
    if not token or not chat_id:
        log.debug("Telegram no configurado")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": parse_mode},
            timeout=10
        )
        if not r.ok:
            log.warning(f"TG {r.status_code}: {r.text[:100]}")
    except Exception as e:
        log.warning(f"TG: {e}")

def tg_startup(balance: float, n: int):
    btc_icon = "🟢" if state.btc_bull else "🔴" if state.btc_bear else "⚪"
    tg(
        f"🚀 <b>SATY ELITE v12 — ONLINE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: <b>${balance:.2f} USDT</b>\n"
        f"📊 Universo: <b>{n} pares</b> BingX\n"
        f"⏱ TF: {TF} · {HTF1} · {HTF2}\n"
        f"🎯 Score mín: {MIN_SCORE}/13\n"
        f"📈 Max trades: {MAX_OPEN_TRADES}  |  💵 Por trade: ${FIXED_USDT:.0f}\n"
        f"⚡ Leverage: {int(LEVERAGE)}×  |  📦 Notional: ${FIXED_USDT*LEVERAGE:.0f}\n"
        f"🛡 Circuit Breaker: -{CB_DD}%  |  📅 Límite diario: -{DAILY_LOSS_LIMIT}%\n"
        f"⏸ Cooldown: {COOLDOWN_MIN} min\n"
        f"{'🟢' if BTC_FILTER else '⚪'} Filtro BTC (adaptativo): {'✅' if BTC_FILTER else '❌'}\n"
        f"{btc_icon} BTC: {'ALCISTA' if state.btc_bull else 'BAJISTA' if state.btc_bear else 'NEUTRO'}"
        f" RSI:{state.btc_rsi:.0f} ADX:{state.btc_adx:.0f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆕 v12: 3×TP | Breakout | Momentum | Más señales\n"
        f"⏰ {utcnow()}"
    )

def tg_signal(t: TradeState, row: pd.Series):
    emoji  = "🟢" if t.side == "long" else "🔴"
    accion = "LONG ▲" if t.side == "long" else "SHORT ▼"
    sl_d   = abs(t.sl_price - t.entry_price)
    rr1    = abs(t.tp1_price - t.entry_price) / max(sl_d, 1e-9)
    rr3    = abs(t.tp3_price - t.entry_price) / max(sl_d, 1e-9)
    sl_pct = sl_d / t.entry_price * 100
    tp1_pct = abs(t.tp1_price - t.entry_price) / t.entry_price * 100
    tp2_pct = abs(t.tp2_price - t.entry_price) / t.entry_price * 100
    tp3_pct = abs(t.tp3_price - t.entry_price) / t.entry_price * 100
    btc_icon = "🟢" if state.btc_bull else "🔴" if state.btc_bear else "⚪"
    strat_icons = {"confluence": "📊", "breakout": "💥", "momentum": "🚀", "rsi_extreme": "🔥"}

    tg(
        f"{emoji} <b>{accion} — {t.symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{strat_icons.get(t.strategy,'📊')} Estrategia: <b>{t.strategy.upper()}</b>\n"
        f"🎯 Score: <b>{t.entry_score}/13</b>  {state.score_bar(t.entry_score)}\n"
        f"💵 Entrada: <code>{t.entry_price:.6g}</code>\n"
        f"🟡 TP1 (25%): <code>{t.tp1_price:.6g}</code>  +{tp1_pct:.2f}%  R:R 1:{rr1:.1f}\n"
        f"🟠 TP2 (25%): <code>{t.tp2_price:.6g}</code>  +{tp2_pct:.2f}%\n"
        f"🟢 TP3 (50%): <code>{t.tp3_price:.6g}</code>  +{tp3_pct:.2f}%  R:R 1:{rr3:.1f}\n"
        f"🛑 SL: <code>{t.sl_price:.6g}</code>  -{sl_pct:.2f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 RSI: {rsi_zone_label(float(row['rsi']))}\n"
        f"📈 ADX: {float(row['adx']):.1f} | MACD: {float(row['macd_hist']):.5f}\n"
        f"🔊 Vol: {float(row['volume'])/max(float(row['vol_ma']),1):.1f}x media\n"
        f"📦 Contratos: {t.contracts} | ATR: {t.atr_entry:.5f}\n"
        f"💰 Riesgo: ${FIXED_USDT * state.risk_mult():.1f} USDT × {int(LEVERAGE)}×\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{btc_icon} BTC: {'ALCISTA' if state.btc_bull else 'BAJISTA' if state.btc_bear else 'NEUTRO'}"
        f" RSI:{state.btc_rsi:.0f} ADX:{state.btc_adx:.0f}\n"
        f"📦 Posiciones: {state.open_count()}/{MAX_OPEN_TRADES}\n"
        f"⏰ {utcnow()}"
    )

def tg_tp_hit(t: TradeState, tp_num: int, price: float, pnl_est: float, next_target: float):
    icons = {1: "🟡", 2: "🟠", 3: "🟢"}
    descs = {1: "SL movido a entrada (break-even)", 2: "SL en TP1 — ganancia asegurada",
             3: "¡TRADE COMPLETO!"}
    pcts  = {1: "25%", 2: "25%", 3: "50%"}
    tg(
        f"{icons[tp_num]} <b>TP{tp_num} ALCANZADO — {t.symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 {t.side.upper()} | Score: {t.entry_score}/13\n"
        f"💵 Precio: <code>{price:.6g}</code>\n"
        f"💰 PnL parcial ({pcts[tp_num]}): <b>+${pnl_est:.2f}</b>\n"
        f"🛡 {descs[tp_num]}\n"
        f"{'🎯 Siguiente: TP' + str(tp_num+1) + f': <code>{next_target:.6g}</code>' if tp_num < 3 else '✅ Todo cerrado'}\n"
        f"📊 Ganancia máx hasta ahora: +{t.max_profit_pct:.2f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ {utcnow()}"
    )

def tg_close(reason: str, t: TradeState, exit_p: float, pnl: float):
    win   = pnl > 0
    emoji = "✅" if win else "❌"
    pct   = (pnl / (t.entry_price * t.contracts) * 100) if t.contracts > 0 else 0
    duracion = ""
    try:
        entry_dt = datetime.strptime(t.entry_time, "%d/%m/%Y %H:%M UTC")
        mins     = int((datetime.utcnow() - entry_dt).total_seconds() / 60)
        duracion = f"\n⏱ Duración: {mins//60}h {mins%60}m"
    except Exception:
        pass
    tg(
        f"{emoji} <b>CERRADO — {t.symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 {t.side.upper()} | {t.strategy.upper()} | Score: {t.entry_score}/13 | {reason}\n"
        f"🚪 Entrada: <code>{t.entry_price:.6g}</code>\n"
        f"🚪 Salida:  <code>{exit_p:.6g}</code>\n"
        f"{'📈' if win else '📉'} Resultado: <b>{pct:+.2f}%</b>  ${pnl:+.2f}{duracion}\n"
        f"🏔 Máx ganancia trade: +{t.max_profit_pct:.2f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Sesión: {state.wins}W/{state.losses}L"
        f" | WR: {state.win_rate():.1f}%"
        f" | PF: {state.profit_factor():.2f}\n"
        f"💹 Hoy: <b>${state.daily_pnl:+.2f}</b>"
        f" | Total: <b>${state.total_pnl:+.2f}</b>\n"
        f"📦 Posiciones: {state.open_count()}/{MAX_OPEN_TRADES}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ {utcnow()}"
    )

def tg_heartbeat(balance: float):
    btc_icon = "🟢" if state.btc_bull else "🔴" if state.btc_bear else "⚪"
    cb_icon  = "⛔" if state.cb_active() else "✅"
    dl_icon  = "⛔" if state.daily_limit_hit() else "✅"
    equity_history.append(state.total_pnl)
    chart = equity_chart()

    open_lines = "\n".join(
        f"  {'🟢' if ts.side=='long' else '🔴'} {sym} "
        f"{'LONG' if ts.side=='long' else 'SHORT'} @ {ts.entry_price:.5g}"
        f" {ts.strategy[:4]} "
        f"{'🛡' if ts.sl_moved_be else ''}{'🔒' if ts.trail_phase=='locked' else ''}"
        f" +{ts.max_profit_pct:.1f}%"
        for sym, ts in state.trades.items()
    ) or "  (ninguna)"

    tg(
        f"💓 <b>HEARTBEAT — SATY v12</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: <b>${balance:.2f} USDT</b>\n"
        f"📅 Hoy: <b>${state.daily_pnl:+.2f}</b>"
        f" | Total: <b>${state.total_pnl:+.2f}</b>\n"
        f"📊 {state.wins}W/{state.losses}L"
        f" | WR: {state.win_rate():.1f}%"
        f" | PF: {state.profit_factor():.2f}\n"
        f"🔍 Señales: {state.signals_found} detectadas / {state.signals_blocked} bloqueadas\n"
        f"🔄 Scans: {state.scan_count}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Posiciones ({state.open_count()}/{MAX_OPEN_TRADES}):\n"
        f"{open_lines}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Equity PnL:\n<code>{chart}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{btc_icon} BTC: {'ALCISTA' if state.btc_bull else 'BAJISTA' if state.btc_bear else 'NEUTRO'}"
        f" RSI:{state.btc_rsi:.0f} ADX:{state.btc_adx:.0f}\n"
        f"Circuit Breaker: {cb_icon} | Límite diario: {dl_icon}\n"
        f"⏰ {utcnow()}"
    )

def tg_summary(new_signals: list, n_scanned: int):
    # ← NUEVO: mostrar señales bloqueadas para debugging
    top_signals = "\n".join(
        f"  {'🟢' if s['side']=='long' else '🔴'} {s['symbol']} "
        f"{s['strategy'].upper()} Score:{s['score']}/13 RSI:{s['rsi']:.0f}"
        for s in new_signals[:5]
    ) or "  (ninguna)"

    discarded = "\n".join(
        f"  ⚫ {d['symbol']} score={d['score']} motivo: {d['reason']}"
        for d in state.last_discarded[:3]
    ) or "  (ninguna)"

    open_lines = "\n".join(
        f"  {'🟢' if ts.side=='long' else '🔴'} {sym} @ {ts.entry_price:.5g}"
        f" {ts.strategy[:4]} {'🛡' if ts.sl_moved_be else ''}{'🔒' if ts.trail_phase=='locked' else ''}"
        f" +{ts.max_profit_pct:.1f}%"
        for sym, ts in state.trades.items()
    ) or "  (ninguna)"

    tg(
        f"📡 <b>RESUMEN SCAN #{state.scan_count}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 Escaneados: {n_scanned} | Señales: {state.signals_found}\n"
        f"📶 Top entradas:\n{top_signals}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🚫 Bloqueadas ({state.signals_blocked}):\n{discarded}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Abiertas ({state.open_count()}/{MAX_OPEN_TRADES}):\n{open_lines}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {state.wins}W/{state.losses}L | WR:{state.win_rate():.1f}% | PF:{state.profit_factor():.2f}\n"
        f"💹 Hoy: ${state.daily_pnl:+.2f} | Total: ${state.total_pnl:+.2f}\n"
        f"⏰ {utcnow()}"
    )

def tg_circuit_breaker(dd: float):
    tg(
        f"🚨 <b>CIRCUIT BREAKER ACTIVADO</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📉 Drawdown: <b>{dd:.2f}%</b> (límite: {CB_DD}%)\n"
        f"⛔ Sin nuevas posiciones hasta reinicio\n"
        f"📦 Trades abiertos se mantienen con SL/TP\n"
        f"💹 Total PnL: ${state.total_pnl:+.2f}\n"
        f"ℹ️ Reinicia el bot para reactivar\n"
        f"⏰ {utcnow()}"
    )

def tg_daily_limit():
    tg(
        f"🚨 <b>LÍMITE DIARIO ALCANZADO</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📉 Pérdida hoy: <b>${state.daily_pnl:+.2f}</b>\n"
        f"⛔ Sin trades nuevos hoy\n"
        f"🔄 Se reanuda mañana UTC\n"
        f"⏰ {utcnow()}"
    )

def tg_error(msg: str):
    tg(f"🔥 <b>ERROR</b>\n━━━━━━━━━━\n<code>{msg[:400]}</code>\n⏰ {utcnow()}")

def tg_btc_flip(prev_bull, prev_bear):
    if prev_bull == state.btc_bull and prev_bear == state.btc_bear: return
    estado = "🟢 ALCISTA" if state.btc_bull else "🔴 BAJISTA" if state.btc_bear else "⚪ NEUTRO"
    prev   = "🟢 ALCISTA" if prev_bull  else "🔴 BAJISTA" if prev_bear  else "⚪ NEUTRO"
    tg(
        f"₿ <b>BTC CAMBIO DE TENDENCIA</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Antes: {prev}  →  Ahora: <b>{estado}</b>\n"
        f"RSI: {state.btc_rsi:.1f} | ADX: {state.btc_adx:.1f}\n"
        f"{'⚠️ Filtro BTC activo — shorts reducidos' if state.btc_bull else '⚠️ Filtro BTC activo — longs reducidos' if state.btc_bear else '⚪ Sin restricciones'}\n"
        f"⏰ {utcnow()}"
    )

def tg_rsi_alert(symbol: str, rsi_v: float, ls: int, ss: int, price: float):
    es_long = rsi_extreme_long(rsi_v)
    tg(
        f"🔔 <b>RSI EXTREMO</b> — {symbol}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {rsi_zone_label(rsi_v)}\n"
        f"💵 Precio: <code>{price:.6g}</code>\n"
        f"{'📉 POSIBLE REBOTE LONG' if es_long else '📈 POSIBLE CAÍDA SHORT'}\n"
        f"🎯 Score L:{ls}/13 {state.score_bar(ls)}  S:{ss}/13\n"
        f"ℹ️ Solo alerta — no es entrada automática\n"
        f"⏰ {utcnow()}"
    )

def tg_trail_phase(t: TradeState, phase: str, price: float, retrace: float, trail_m: float):
    icons = {"normal": "🏃", "tight": "⚡", "locked": "🔒", "ultra": "⚡⚡"}
    descs = {"normal": "Siguiendo precio", "tight": "Lateral — apretado",
             "locked": "Retroceso — bloqueando", "ultra": "Ganancia >5% — ultra ajustado"}
    tg(
        f"{icons.get(phase,'⚡')} <b>TRAILING {phase.upper()}</b> — {t.symbol}\n"
        f"💡 {descs.get(phase,'')}\n"
        f"💵 Precio: <code>{price:.6g}</code> | Peak: <code>{t.peak_price:.6g}</code>\n"
        f"📉 Retroceso: {retrace:.1f}% | Stop: {trail_m}×ATR\n"
        f"📊 Ganancia máx: +{t.max_profit_pct:.2f}%\n"
        f"⏰ {utcnow()}"
    )

# ══════════════════════════════════════════════════════════
# RSI HELPERS
# ══════════════════════════════════════════════════════════
def rsi_extreme_long(rsi):  return RSI_OB_LOW <= rsi <= RSI_OB_HIGH
def rsi_extreme_short(rsi): return RSI_OS_LOW <= rsi <= RSI_OS_HIGH

def rsi_zone_label(rsi):
    if rsi < RSI_OB_LOW:   return f"⚠️ HIPERVENTA {rsi:.1f}"
    if rsi <= RSI_OB_HIGH: return f"🔥 SOBREVENTA {rsi:.1f}"
    if rsi < 42:            return f"🟢 RSI bajo {rsi:.1f}"
    if rsi <= 58:           return f"⚪ RSI neutral {rsi:.1f}"
    if rsi < RSI_OS_LOW:   return f"🟡 RSI alto {rsi:.1f}"
    if rsi <= RSI_OS_HIGH: return f"🔥 SOBRECOMPRA {rsi:.1f}"
    return                        f"⚠️ HIPERCOMPRA {rsi:.1f}"

# ══════════════════════════════════════════════════════════
# INDICADORES
# ══════════════════════════════════════════════════════════
def ema(s, n):  return s.ewm(span=n, adjust=False).mean()
def sma(s, n):  return s.rolling(n).mean()

def calc_atr(df, n):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()

def calc_rsi(s, n):
    d  = s.diff()
    g  = d.clip(lower=0).ewm(span=n, adjust=False).mean()
    lo = (-d.clip(upper=0)).ewm(span=n, adjust=False).mean()
    return 100 - (100 / (1 + g / lo.replace(0, np.nan)))

def calc_adx(df, n):
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

def calc_stoch_rsi(s):
    r  = calc_rsi(s, RSI_LEN)
    lo = r.rolling(STOCH_LEN).min()
    hi = r.rolling(STOCH_LEN).max()
    st = 100 * (r - lo) / (hi - lo).replace(0, np.nan)
    k  = st.rolling(3).mean()
    return k, k.rolling(3).mean()

def compute(df):
    df = df.copy()
    c, h, l, v, o = df["close"], df["high"], df["low"], df["volume"], df["open"]

    df["ema8"]   = ema(c, FAST_LEN)
    df["ema21"]  = ema(c, PIVOT_LEN)
    df["ema48"]  = ema(c, BIAS_LEN)
    df["ema200"] = ema(c, SLOW_LEN)
    df["atr"]    = calc_atr(df, ATR_LEN)
    df["rsi"]    = calc_rsi(c, RSI_LEN)

    dip, dim, adx_s     = calc_adx(df, ADX_LEN)
    df["dip"] = dip; df["dim"] = dim; df["adx"] = adx_s

    macd, macd_sg, macd_h = calc_macd(c)
    df["macd_hist"]       = macd_h
    df["macd_bull"]       = (macd_h > 0) & (macd_h > macd_h.shift())
    df["macd_bear"]       = (macd_h < 0) & (macd_h < macd_h.shift())
    df["macd_cross_up"]   = (macd > macd_sg) & (macd.shift() <= macd_sg.shift())
    df["macd_cross_down"] = (macd < macd_sg) & (macd.shift() >= macd_sg.shift())

    sk, sd = calc_stoch_rsi(c)
    df["stoch_k"]    = sk; df["stoch_d"] = sd
    df["stoch_bull"] = (sk > sd) & (sk < 80) & (sk.shift() <= sd.shift())
    df["stoch_bear"] = (sk < sd) & (sk > 20) & (sk.shift() >= sd.shift())

    df["osc"]    = ema(((c - df["ema21"]) / (3.0 * df["atr"].replace(0,np.nan))) * 100, OSC_LEN)
    df["osc_up"] = (df["osc"] > 0) & (df["osc"].shift() <= 0)
    df["osc_dn"] = (df["osc"] < 0) & (df["osc"].shift() >= 0)

    bb_std        = c.rolling(PIVOT_LEN).std()
    bb_up         = df["ema21"] + 2.0 * bb_std
    bb_lo         = df["ema21"] - 2.0 * bb_std
    df["squeeze"] = bb_up < (df["ema21"] + 2.0 * df["atr"])
    bb_w          = (bb_up - bb_lo) / df["ema21"].replace(0, np.nan)
    bb_w_sma      = sma(bb_w, 20)
    # ← FIX: umbral bajado de 0.8 a 0.5 (menos restrictivo)
    df["is_trending"] = (adx_s > ADX_MIN) & (bb_w > bb_w_sma * 0.5)
    df["bb_squeeze"]  = bb_w < bb_w_sma * 0.6   # ← NUEVO: detecta squeeze (breakout inminente)

    rng            = (h - l).replace(0, np.nan)
    df["buy_vol"]  = v * (c - l) / rng
    df["sell_vol"] = v * (h - c) / rng
    df["vol_ma"]   = sma(v, VOL_LEN)
    df["vol_spike"]= v > df["vol_ma"] * 1.05
    df["vol_bull"] = df["buy_vol"] > df["sell_vol"]
    df["vol_bear"] = df["sell_vol"] > df["buy_vol"]

    # ← NUEVO: volumen institucional (spike muy grande)
    df["vol_inst"] = v > df["vol_ma"] * 2.0

    body           = (c - o).abs()
    body_pct       = body / rng.replace(0, np.nan)
    df["bull_candle"] = (c > o) & (body_pct >= 0.30)
    df["bear_candle"] = (c < o) & (body_pct >= 0.30)

    prev_body = (o.shift() - c.shift()).abs()
    df["bull_engulf"] = (c > o) & (o <= c.shift()) & (c >= o.shift()) & (body > prev_body * 0.8)
    df["bear_engulf"] = (c < o) & (o >= c.shift()) & (c <= o.shift()) & (body > prev_body * 0.8)

    df["swing_low"]  = l.rolling(SWING_LB).min()
    df["swing_high"] = h.rolling(SWING_LB).max()

    r = df["rsi"]
    df["bull_div"] = (
        (l < l.shift(1)) & (l.shift(1) < l.shift(2)) &
        (r > r.shift(1)) & (r.shift(1) > r.shift(2)) & (r < 42)
    )
    df["bear_div"] = (
        (h > h.shift(1)) & (h.shift(1) > h.shift(2)) &
        (r < r.shift(1)) & (r.shift(1) < r.shift(2)) & (r > 58)
    )

    # ── NUEVO: Breakout detector ─────────────────────────────────────────────
    lookback = 20
    df["range_high"] = h.shift(1).rolling(lookback).max()
    df["range_low"]  = l.shift(1).rolling(lookback).min()
    df["breakout_up"]   = (c > df["range_high"]) & (v > df["vol_ma"] * 1.5)
    df["breakout_down"] = (c < df["range_low"])  & (v > df["vol_ma"] * 1.5)

    # ── NUEVO: Momentum burst (3 velas consecutivas en la misma dirección) ──
    df["mom_bull_3"] = (c > c.shift(1)) & (c.shift(1) > c.shift(2)) & (c.shift(2) > c.shift(3))
    df["mom_bear_3"] = (c < c.shift(1)) & (c.shift(1) < c.shift(2)) & (c.shift(2) < c.shift(3))

    return df

def htf_bias(df):
    df  = compute(df); row = df.iloc[-2]
    return (bool(row["close"] > row["ema48"] and row["ema21"] > row["ema48"]),
            bool(row["close"] < row["ema48"] and row["ema21"] < row["ema48"]))

def htf2_macro(df):
    df  = compute(df); row = df.iloc[-2]
    return (bool(row["close"] > row["ema48"] and row["ema48"] > row["ema200"]),
            bool(row["close"] < row["ema48"] and row["ema48"] < row["ema200"]))

# ══════════════════════════════════════════════════════════
# SCORE 13 PUNTOS (v12)
# ══════════════════════════════════════════════════════════
def confluence_score(row, htf1_bull, htf1_bear, htf2_bull, htf2_bear):
    rsi_v = float(row["rsi"])
    ls = sum([
        bool(row["close"] > row["ema48"] and row["ema8"] > row["ema21"]),
        bool(row["osc_up"]),
        htf1_bull,
        htf2_bull,
        bool(row["adx"] > ADX_MIN and row["dip"] > row["dim"]),
        bool(40 <= rsi_v <= 72),        # ← AMPLIADO el rango
        bool(row["vol_bull"] and row["vol_spike"]),   # ← SIN requerir squeeze
        bool(row["bull_candle"] and row["close"] > row["ema21"]),
        bool(row["macd_bull"] or row["macd_cross_up"]),
        bool(row["stoch_bull"] or (row["stoch_k"] > row["stoch_d"] and row["stoch_k"] < 75)),
        rsi_extreme_long(rsi_v),
        bool(row["bull_engulf"] or row["bull_div"]),
        bool(row["is_trending"]),       # ← AHORA es +1 punto, no filtro hard
    ])
    ss = sum([
        bool(row["close"] < row["ema48"] and row["ema8"] < row["ema21"]),
        bool(row["osc_dn"]),
        htf1_bear,
        htf2_bear,
        bool(row["adx"] > ADX_MIN and row["dim"] > row["dip"]),
        bool(28 <= rsi_v <= 60),        # ← AMPLIADO
        bool(row["vol_bear"] and row["vol_spike"]),
        bool(row["bear_candle"] and row["close"] < row["ema21"]),
        bool(row["macd_bear"] or row["macd_cross_down"]),
        bool(row["stoch_bear"] or (row["stoch_k"] < row["stoch_d"] and row["stoch_k"] > 25)),
        rsi_extreme_short(rsi_v),
        bool(row["bear_engulf"] or row["bear_div"]),
        bool(row["is_trending"]),
    ])
    return ls, ss

def breakout_score(row) -> Tuple[int, int, str]:
    """
    ← NUEVA ESTRATEGIA: detecta breakouts de rango con volumen.
    Retorna (long_score, short_score, strategy_name)
    """
    ls, ss = 0, 0
    if bool(row["breakout_up"]):
        ls = sum([
            True,                                          # breakout base
            bool(row["vol_inst"]),                         # volumen institucional
            bool(row["adx"] > 15),
            bool(row["macd_bull"] or row["macd_cross_up"]),
            bool(row["rsi"] > 50 and row["rsi"] < 72),
        ])
    if bool(row["breakout_down"]):
        ss = sum([
            True,
            bool(row["vol_inst"]),
            bool(row["adx"] > 15),
            bool(row["macd_bear"] or row["macd_cross_down"]),
            bool(row["rsi"] < 50 and row["rsi"] > 28),
        ])
    return ls, ss

def momentum_score(row, htf1_bull, htf1_bear) -> Tuple[int, int]:
    """
    ← NUEVA ESTRATEGIA: momentum burst (impulso sostenido).
    """
    ls, ss = 0, 0
    if bool(row["mom_bull_3"]):
        ls = sum([
            True,
            htf1_bull,
            bool(row["vol_bull"] and row["volume"] > row["vol_ma"]),
            bool(row["adx"] > ADX_MIN),
            bool(row["rsi"] < 70),
            bool(row["macd_bull"]),
        ])
    if bool(row["mom_bear_3"]):
        ss = sum([
            True,
            htf1_bear,
            bool(row["vol_bear"] and row["volume"] > row["vol_ma"]),
            bool(row["adx"] > ADX_MIN),
            bool(row["rsi"] > 30),
            bool(row["macd_bear"]),
        ])
    return ls, ss

# ══════════════════════════════════════════════════════════
# BTC BIAS (mejorado)
# ══════════════════════════════════════════════════════════
def update_btc_bias(ex):
    prev_bull = state.btc_bull
    prev_bear = state.btc_bear
    try:
        df = fetch_df(ex, "BTC/USDT:USDT", "1h", limit=250)
        df = compute(df); row = df.iloc[-2]
        state.btc_bull = bool(row["close"] > row["ema48"] and row["ema48"] > row["ema200"])
        state.btc_bear = bool(row["close"] < row["ema48"] and row["ema48"] < row["ema200"])
        state.btc_rsi  = float(row["rsi"])
        state.btc_adx  = float(row["adx"])  # ← NUEVO
        log.info(f"BTC: {'BULL' if state.btc_bull else 'BEAR' if state.btc_bear else 'NEUTRAL'}"
                 f" RSI:{state.btc_rsi:.1f} ADX:{state.btc_adx:.1f}")
        tg_btc_flip(prev_bull, prev_bear)
    except Exception as e:
        log.warning(f"BTC bias: {e}")

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

def get_balance(ex):
    return float(ex.fetch_balance()["USDT"]["free"])

def get_position(ex, symbol):
    try:
        for p in ex.fetch_positions([symbol]):
            if abs(float(p.get("contracts", 0) or 0)) > 0:
                return p
    except Exception:
        pass
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
    except Exception:
        return 0.0

def get_min_amount(ex, symbol):
    try:
        mkt = ex.markets.get(symbol, {})
        return float(mkt.get("limits", {}).get("amount", {}).get("min", 0) or 0)
    except Exception:
        return 0.0

def entry_params(side):
    return {"positionSide": "LONG" if side == "buy" else "SHORT"} if HEDGE_MODE else {}

def exit_params(trade_side):
    if HEDGE_MODE:
        return {"positionSide": "LONG" if trade_side == "long" else "SHORT", "reduceOnly": True}
    return {"reduceOnly": True}

# ══════════════════════════════════════════════════════════
# UNIVERSO
# ══════════════════════════════════════════════════════════
def get_symbols(ex):
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

    ranked = []
    for sym in candidates:
        tk  = tickers.get(sym, {})
        vol = float(tk.get("quoteVolume", 0) or 0)
        if vol >= MIN_VOLUME_USDT:
            info    = ex.markets.get(sym, {}).get("info", {})
            created = info.get("onboardDate", 0) or 0
            is_new  = False
            if created:
                try:
                    is_new = (time.time() - float(created) / 1000) / 86400 < 30
                except Exception:
                    pass
            ranked.append((sym, vol, is_new))

    ranked.sort(key=lambda x: (not x[2], -x[1]))
    result = [s for s, _, _ in ranked[:TOP_N_SYMBOLS]]
    log.info(f"Universo: {len(result)} pares")
    return result

# ══════════════════════════════════════════════════════════
# APERTURA
# ══════════════════════════════════════════════════════════
def open_trade(ex, symbol, base, side, score, row, strategy="confluence"):
    try:
        spread = get_spread_pct(ex, symbol)
        if spread > MAX_SPREAD_PCT:
            log.warning(f"[{symbol}] spread {spread:.3f}% — skip")
            return None

        price    = get_last_price(ex, symbol)
        atr_v    = float(row["atr"])
        usdt     = FIXED_USDT * state.risk_mult()
        mkt      = ex.markets.get(symbol, {})
        contract_size = float(
            mkt.get("contractSize") or
            mkt.get("info", {}).get("contractSize") or
            mkt.get("info", {}).get("multiplier") or 1.0
        )

        notional = usdt * LEVERAGE
        raw_amt  = notional / (price * contract_size)
        amount   = float(ex.amount_to_precision(symbol, raw_amt))
        min_amt  = get_min_amount(ex, symbol)

        log.info(f"[SIZE] {symbol} | ${usdt:.1f}×{LEVERAGE:.0f}=${notional:.0f} "
                 f"| price={price:.6g} | cs={contract_size} | amt={amount:.6f}")

        if amount <= 0 or amount < min_amt or amount * price * contract_size < 3:
            log.warning(f"[{symbol}] amount inválido: {amount:.6f}")
            return None

        # Apalancamiento
        try:
            lev_int = int(LEVERAGE)
            if HEDGE_MODE:
                ex.set_leverage(lev_int, symbol, params={"positionSide": "LONG"})
                ex.set_leverage(lev_int, symbol, params={"positionSide": "SHORT"})
            else:
                ex.set_leverage(lev_int, symbol)
        except Exception as e:
            log.warning(f"[{symbol}] set_leverage: {e}")

        order       = ex.create_order(symbol, "market", side, amount, params=entry_params(side))
        entry_price = float(order.get("average") or price)
        trade_side  = "long" if side == "buy" else "short"

        if side == "buy":
            sl_p  = min(float(row["swing_low"])  - atr_v * 0.2, entry_price - atr_v * SL_ATR)
            tp1_p = entry_price + atr_v * TP1_MULT
            tp2_p = entry_price + atr_v * TP2_MULT
            tp3_p = entry_price + atr_v * TP3_MULT
        else:
            sl_p  = max(float(row["swing_high"]) + atr_v * 0.2, entry_price + atr_v * SL_ATR)
            tp1_p = entry_price - atr_v * TP1_MULT
            tp2_p = entry_price - atr_v * TP2_MULT
            tp3_p = entry_price - atr_v * TP3_MULT

        tp1_p = float(ex.price_to_precision(symbol, tp1_p))
        tp2_p = float(ex.price_to_precision(symbol, tp2_p))
        tp3_p = float(ex.price_to_precision(symbol, tp3_p))
        sl_p  = float(ex.price_to_precision(symbol, sl_p))
        ep    = exit_params(trade_side)
        cside = "sell" if side == "buy" else "buy"

        # ← NUEVO: 3 TPs en lugar de 2 (25% / 25% / 50%)
        q1 = float(ex.amount_to_precision(symbol, amount * 0.25))
        q2 = float(ex.amount_to_precision(symbol, amount * 0.25))
        q3 = float(ex.amount_to_precision(symbol, amount * 0.50))

        for lbl, qty, px in [("TP1", q1, tp1_p), ("TP2", q2, tp2_p), ("TP3", q3, tp3_p)]:
            try:
                ex.create_order(symbol, "limit", cside, qty, px, ep)
            except Exception as e:
                log.warning(f"[{symbol}] {lbl}: {e}")

        try:
            ex.create_order(symbol, "stop_market", cside, amount, None, {**ep, "stopPrice": sl_p})
        except Exception as e:
            log.warning(f"[{symbol}] SL: {e}")

        t = TradeState(
            symbol=symbol, base=base, side=trade_side,
            entry_price=entry_price, tp1_price=tp1_p, tp2_price=tp2_p, tp3_price=tp3_p,
            sl_price=sl_p, entry_score=score, strategy=strategy,
            entry_time=utcnow(), contracts=amount, atr_entry=atr_v,
        )
        t.trail_high = entry_price; t.trail_low = entry_price; t.peak_price = entry_price

        log_csv("OPEN", t, entry_price)
        tg_signal(t, row)
        return t

    except Exception as e:
        log.error(f"[{symbol}] open_trade: {e}")
        tg_error(f"open_trade {symbol}: {e}")
        return None

def move_sl_to(ex, symbol, new_sl: float):
    if symbol not in state.trades: return
    t = state.trades[symbol]
    try:
        ex.cancel_all_orders(symbol)
    except Exception as e:
        log.warning(f"[{symbol}] cancel SL: {e}")
    sl_px = float(ex.price_to_precision(symbol, new_sl))
    cside = "sell" if t.side == "long" else "buy"
    try:
        ex.create_order(symbol, "stop_market", cside, t.contracts, None,
                        {**exit_params(t.side), "stopPrice": sl_px})
        t.sl_price = sl_px
    except Exception as e:
        log.warning(f"[{symbol}] move_sl: {e}")

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
            log.error(f"[{symbol}] close mkt: {e}"); return

    if pnl > 0:
        state.wins += 1; state.gross_profit += pnl; state.consec_losses = 0
    elif pnl < 0:
        state.losses += 1; state.gross_loss += abs(pnl); state.consec_losses += 1

    state.total_pnl  += pnl
    state.daily_pnl  += pnl
    state.peak_equity = max(state.peak_equity, state.peak_equity + pnl)
    state.set_cooldown(symbol)
    log_csv("CLOSE", t, price, pnl)
    tg_close(reason, t, price, pnl)
    del state.trades[symbol]

# ══════════════════════════════════════════════════════════
# GESTIÓN DEL TRADE (mejorada)
# ══════════════════════════════════════════════════════════
def manage_trade(ex, symbol, live_price, atr_v, long_score, short_score, live_pos, result=None):
    if symbol not in state.trades: return
    t = state.trades[symbol]

    # Cerrado externamente (BingX ejecutó TP/SL)
    if live_pos is None:
        pnl = ((live_price - t.entry_price) if t.side == "long" else (t.entry_price - live_price)) * t.contracts
        reason = ("TP3 COMPLETO" if (t.side=="long" and live_price >= t.tp3_price) or
                                     (t.side=="short" and live_price <= t.tp3_price)
                  else "TP2 ALCANZADO" if (t.side=="long" and live_price >= t.tp2_price) or
                                          (t.side=="short" and live_price <= t.tp2_price)
                  else "SL ALCANZADO")
        if pnl > 0: state.wins += 1; state.gross_profit += pnl; state.consec_losses = 0
        else:       state.losses += 1; state.gross_loss += abs(pnl); state.consec_losses += 1
        state.total_pnl += pnl; state.daily_pnl += pnl
        state.set_cooldown(symbol)
        log_csv("CLOSE_EXT", t, live_price, pnl)
        tg_close(reason, t, live_price, pnl)
        del state.trades[symbol]; return

    # ── TP1 → Break-even ─────────────────────────────────────────────────────
    if not t.tp1_hit:
        hit = (t.side == "long" and live_price >= t.tp1_price) or \
              (t.side == "short" and live_price <= t.tp1_price)
        if hit:
            t.tp1_hit    = True
            t.sl_moved_be = True
            t.peak_price  = live_price
            contracts     = float(live_pos.get("contracts", 0))
            pnl_est       = abs(t.tp1_price - t.entry_price) * contracts * 0.25
            move_sl_to(ex, symbol, t.entry_price)
            tg_tp_hit(t, 1, live_price, pnl_est, t.tp2_price)

    # ── TP2 → SL sube a TP1 ──────────────────────────────────────────────────
    if t.tp1_hit and not t.tp2_hit:
        hit2 = (t.side == "long" and live_price >= t.tp2_price) or \
               (t.side == "short" and live_price <= t.tp2_price)
        if hit2:
            t.tp2_hit = True
            contracts  = float(live_pos.get("contracts", 0))
            pnl_est    = abs(t.tp2_price - t.entry_price) * contracts * 0.25
            move_sl_to(ex, symbol, t.tp1_price)   # SL sube a TP1 → ganancia garantizada
            tg_tp_hit(t, 2, live_price, pnl_est, t.tp3_price)

    # ── Trailing dinámico (post TP1) ──────────────────────────────────────────
    if t.tp1_hit and symbol in state.trades:
        atr_t = atr_v if atr_v > 0 else t.atr_entry

        cur_pct = ((live_price - t.entry_price) / t.entry_price * 100 if t.side == "long"
                   else (t.entry_price - live_price) / t.entry_price * 100)
        t.max_profit_pct = max(t.max_profit_pct, cur_pct)

        new_peak = (live_price > t.peak_price if t.side == "long" else live_price < t.peak_price)
        if new_peak: t.peak_price = live_price; t.stall_count = 0
        else:        t.stall_count += 1

        denom   = abs(t.peak_price - t.entry_price)
        retrace = ((t.peak_price - live_price) / max(denom, 1e-9) * 100 if t.side == "long"
                   else (live_price - t.peak_price) / max(denom, 1e-9) * 100)

        prev_phase = t.trail_phase
        # ← NUEVO: fase "ultra" cuando ganancia supera 5%
        if cur_pct > 5.0:           t.trail_phase = "ultra"
        elif retrace > 30:           t.trail_phase = "locked"
        elif t.stall_count >= 3:     t.trail_phase = "tight"
        else:                        t.trail_phase = "normal"

        trail_m = {"normal": 0.8, "tight": 0.4, "locked": 0.2, "ultra": 0.15}[t.trail_phase]

        if t.trail_phase != prev_phase:
            tg_trail_phase(t, t.trail_phase, live_price, retrace, trail_m)

        if t.side == "long":
            t.trail_high = max(t.trail_high, live_price)
            if live_price <= t.trail_high - atr_t * trail_m:
                close_trade(ex, symbol, f"TRAILING {t.trail_phase.upper()}", live_price); return
        else:
            t.trail_low = min(t.trail_low, live_price)
            if live_price >= t.trail_low + atr_t * trail_m:
                close_trade(ex, symbol, f"TRAILING {t.trail_phase.upper()}", live_price); return

    # ── Pérdida dinámica pre-TP1 ─────────────────────────────────────────────
    if not t.tp1_hit and symbol in state.trades:
        atr_now   = atr_v if atr_v > 0 else t.atr_entry
        loss_dist = (t.entry_price - live_price if t.side == "long" else live_price - t.entry_price)
        if loss_dist >= atr_now * 0.8:
            close_trade(ex, symbol, "PÉRDIDA DINÁMICA (0.8×ATR)", live_price); return

    # ── Cierre por agotamiento ───────────────────────────────────────────────
    if result and symbol in state.trades and t.tp1_hit:
        row = result["row"]
        try:
            in_profit = (t.side == "long" and live_price > t.entry_price) or \
                        (t.side == "short" and live_price < t.entry_price)
            if in_profit:
                rsi_v     = float(row["rsi"])
                adx_v     = float(row["adx"])
                vol_ratio = float(row["volume"]) / max(float(row["vol_ma"]), 1)
                if t.side == "long":
                    exh = sum([bool(row["macd_bear"]), adx_v < 15, vol_ratio < 0.6,
                               bool(row["bear_div"]), bool(row["osc_dn"]), rsi_v > 74])
                else:
                    exh = sum([bool(row["macd_bull"]), adx_v < 15, vol_ratio < 0.6,
                               bool(row["bull_div"]), bool(row["osc_up"]), rsi_v < 26])
                if exh >= 4:  # ← SUBIDO de 3 a 4 (menos cierres prematuros)
                    close_trade(ex, symbol, f"AGOTAMIENTO ({exh}/6)", live_price); return
        except Exception as e:
            log.debug(f"[{symbol}] agotamiento: {e}")

    # ── Flip de señal ────────────────────────────────────────────────────────
    if symbol in state.trades:
        if t.side == "long"  and short_score >= MIN_SCORE + 3:  # ← más conservador
            close_trade(ex, symbol, f"FLIP SHORT (score {short_score})", live_price)
        elif t.side == "short" and long_score >= MIN_SCORE + 3:
            close_trade(ex, symbol, f"FLIP LONG (score {long_score})", live_price)

# ══════════════════════════════════════════════════════════
# SCAN COMPLETO (v12: 3 estrategias)
# ══════════════════════════════════════════════════════════
def scan_symbol(ex, symbol):
    try:
        df  = fetch_df(ex, symbol, TF,   400)
        df1 = fetch_df(ex, symbol, HTF1, 200)
        df2 = fetch_df(ex, symbol, HTF2, 300)
        df  = compute(df)
        row = df.iloc[-2]

        if pd.isna(row["adx"]) or pd.isna(row["rsi"]):
            return None

        htf1_bull, htf1_bear = htf_bias(df1)
        htf2_bull, htf2_bear = htf2_macro(df2)

        # Estrategia 1: Confluencia
        ls_c, ss_c = confluence_score(row, htf1_bull, htf1_bear, htf2_bull, htf2_bear)

        # Estrategia 2: Breakout
        ls_b, ss_b = breakout_score(row)

        # Estrategia 3: Momentum
        ls_m, ss_m = momentum_score(row, htf1_bull, htf1_bear)

        # Mejor señal por dirección
        best_long  = max((ls_c,"confluence"), (ls_b,"breakout"), (ls_m,"momentum"), key=lambda x: x[0])
        best_short = max((ss_c,"confluence"), (ss_b,"breakout"), (ss_m,"momentum"), key=lambda x: x[0])
        ls, ls_strat = best_long
        ss, ss_strat = best_short

        rsi_v = float(row["rsi"])

        # RSI extremo alertas (sin importar score)
        if rsi_extreme_long(rsi_v) or rsi_extreme_short(rsi_v):
            now = time.time()
            if now - state.rsi_alerts.get(symbol, 0) > 1800:
                state.rsi_alerts[symbol] = now
                tg_rsi_alert(symbol, rsi_v, ls, ss, float(row["close"]))

        return {
            "symbol":    symbol,
            "base":      symbol.split("/")[0],
            "long_score":  ls,  "long_strategy":  ls_strat,
            "short_score": ss,  "short_strategy": ss_strat,
            "row":       row,
            "atr":       float(row["atr"]),
            "live_price":float(row["close"]),
            "is_trending":bool(row["is_trending"]),
            "rsi":       rsi_v,
        }
    except Exception as e:
        log.debug(f"[{symbol}] scan: {e}")
        return None

# ══════════════════════════════════════════════════════════
# FILTRO BTC ADAPTATIVO (← mejorado vs v11)
# ══════════════════════════════════════════════════════════
def btc_allows(direction: str, score: int) -> Tuple[bool, str]:
    """
    v12: BTC filter es ADAPTATIVO, no binario.
    - BTC alcista fuerte (ADX>20): solo longs, salvo score muy alto (>=7)
    - BTC alcista débil: permite shorts con score normal
    - BTC neutral: ambas direcciones
    - BTC bajista: lo mismo pero al revés
    """
    if not BTC_FILTER:
        return True, ""

    btc_strong = state.btc_adx > 20

    if direction == "long":
        if state.btc_bear and btc_strong and score < 7:
            return False, f"BTC bajista fuerte (ADX:{state.btc_adx:.0f})"
        return True, ""

    if direction == "short":
        if state.btc_bull and btc_strong and score < 7:
            return False, f"BTC alcista fuerte (ADX:{state.btc_adx:.0f})"
        return True, ""

    return True, ""

# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════
def main():
    global HEDGE_MODE
    log.info("=" * 65)
    log.info("  SATY ELITE v12 — BingX + 3 Estrategias + 3×TP")
    log.info("=" * 65)

    if not (API_KEY and API_SECRET):
        log.error("BINGX_API_KEY y BINGX_API_SECRET no configuradas")
        tg_error("Bot no iniciado: faltan API Keys de BingX")
        sys.exit(1)

    ex = None
    for attempt in range(10):
        try:
            ex = build_exchange(); log.info("BingX conectado ✓"); break
        except Exception as e:
            wait = min(2 ** attempt, 120)
            log.warning(f"Conexión {attempt+1}/10: {e} — retry {wait}s")
            time.sleep(wait)
    if ex is None:
        tg_error("No se pudo conectar a BingX tras 10 intentos")
        raise RuntimeError("Sin conexión BingX")

    HEDGE_MODE = detect_hedge_mode(ex)
    log.info(f"Modo: {'HEDGE' if HEDGE_MODE else 'ONE-WAY'}")

    balance = 0.0
    for i in range(10):
        try:    balance = get_balance(ex); break
        except: time.sleep(5)

    state.peak_equity    = balance
    state.daily_reset_ts = time.time()
    state.last_heartbeat = time.time()

    symbols = []
    while not symbols:
        try:
            ex.load_markets(); symbols = get_symbols(ex)
        except Exception as e:
            log.error(f"get_symbols: {e}"); time.sleep(60)

    update_btc_bias(ex)
    tg_startup(balance, len(symbols))

    scan_count    = 0
    REFRESH_EVERY = max(1, 3600 // max(POLL_SECS, 1))
    BTC_REFRESH   = max(1, 900  // max(POLL_SECS, 1))
    HB_INTERVAL   = 3600
    SUMMARY_EVERY = 20

    prev_cb = False; prev_dl = False

    while True:
        ts_start = time.time()
        try:
            scan_count += 1
            state.scan_count = scan_count
            state.reset_daily()
            clear_cache()

            log.info(f"SCAN #{scan_count} | {datetime.now(timezone.utc):%H:%M:%S} "
                     f"| {state.open_count()}/{MAX_OPEN_TRADES} trades | "
                     f"señales totales: {state.signals_found}")

            if scan_count % REFRESH_EVERY == 0:
                try: ex.load_markets(); symbols = get_symbols(ex)
                except Exception as e: log.warning(f"Refresh: {e}")
            if scan_count % BTC_REFRESH == 0:
                update_btc_bias(ex)

            if time.time() - state.last_heartbeat > HB_INTERVAL:
                try:
                    tg_heartbeat(get_balance(ex))
                    state.last_heartbeat = time.time()
                except Exception: pass

            cb_now = state.cb_active()
            if cb_now and not prev_cb:
                dd_now = (state.peak_equity - (state.peak_equity + state.total_pnl)) / state.peak_equity * 100
                tg_circuit_breaker(dd_now)
            prev_cb = cb_now

            if cb_now:
                log.warning("CIRCUIT BREAKER activo")
                time.sleep(POLL_SECS); continue

            dl_now = state.daily_limit_hit()
            if dl_now and not prev_dl:
                tg_daily_limit()
            prev_dl = dl_now

            if dl_now:
                log.warning("LÍMITE DIARIO activo")
                time.sleep(POLL_SECS); continue

            # Gestionar posiciones abiertas
            live_positions = get_all_positions(ex)
            for sym in list(state.trades.keys()):
                try:
                    lp    = live_positions.get(sym)
                    lp_   = float(lp["markPrice"]) if lp else get_last_price(ex, sym)
                    res   = scan_symbol(ex, sym)
                    ls    = res["long_score"]  if res else 0
                    ss    = res["short_score"] if res else 0
                    atr_v = res["atr"]         if res else state.trades[sym].atr_entry
                    manage_trade(ex, sym, lp_, atr_v, ls, ss, lp, res)
                except Exception as e:
                    log.warning(f"[{sym}] manage: {e}")

            # Buscar nuevas señales
            new_signals = []
            state.last_discarded = []

            if state.open_count() < MAX_OPEN_TRADES:
                bases_open = state.bases_open()
                to_scan    = [
                    s for s in symbols
                    if s not in state.trades
                    and not state.in_cooldown(s)
                    and s.split("/")[0] not in bases_open
                ]
                log.info(f"Escaneando {len(to_scan)} pares...")

                with ThreadPoolExecutor(max_workers=10) as pool:
                    futures = {pool.submit(scan_symbol, ex, s): s for s in to_scan}
                    results = [f.result() for f in as_completed(futures) if f.result()]

                for res in results:
                    base     = res["base"]
                    ls, ss   = res["long_score"], res["short_score"]
                    ls_strat = res["long_strategy"]
                    ss_strat = res["short_strategy"]

                    # Evaluar cada dirección
                    best_side     = None
                    best_score    = 0
                    best_strategy = "confluence"

                    for direction, score, strat in [("long", ls, ls_strat), ("short", ss, ss_strat)]:
                        if score < MIN_SCORE:
                            if score >= MIN_SCORE - 1:  # log señales casi-válidas
                                state.last_discarded.append({
                                    "symbol": res["symbol"], "score": score,
                                    "reason": f"score {score} < {MIN_SCORE}"
                                })
                            continue

                        allowed, block_reason = btc_allows(direction, score)
                        if not allowed:
                            state.signals_blocked += 1
                            state.last_discarded.append({
                                "symbol": res["symbol"], "score": score,
                                "reason": block_reason
                            })
                            continue

                        if state.base_has_trade(base):
                            continue

                        if score > best_score:
                            best_score    = score
                            best_side     = direction
                            best_strategy = strat

                    if best_side:
                        state.signals_found += 1
                        new_signals.append({
                            "symbol":   res["symbol"],
                            "base":     base,
                            "side":     best_side,
                            "score":    best_score,
                            "strategy": best_strategy,
                            "row":      res["row"],
                            "rsi":      res["rsi"],
                        })

                new_signals.sort(key=lambda x: x["score"], reverse=True)

                for sig in new_signals:
                    if state.open_count() >= MAX_OPEN_TRADES: break
                    sym = sig["symbol"]; base = sig["base"]
                    if sym in state.trades or state.base_has_trade(base): continue
                    if state.in_cooldown(sym): continue
                    t = open_trade(ex, sym, base,
                                   "buy" if sig["side"] == "long" else "sell",
                                   sig["score"], sig["row"], sig["strategy"])
                    if t: state.trades[sym] = t

            else:
                to_scan = []

            if scan_count % SUMMARY_EVERY == 0:
                tg_summary(new_signals, len(to_scan))

            elapsed = time.time() - ts_start
            log.info(
                f"Ciclo {elapsed:.1f}s | {state.wins}W/{state.losses}L | "
                f"señales:{state.signals_found} | hoy:${state.daily_pnl:+.2f} | "
                f"total:${state.total_pnl:+.2f}"
            )

        except ccxt.NetworkError as e:
            log.warning(f"Network: {e} — 15s"); time.sleep(15)
        except ccxt.ExchangeError as e:
            log.error(f"Exchange: {e}"); tg_error(f"Exchange: {str(e)[:200]}")
        except KeyboardInterrupt:
            tg("🛑 <b>Bot detenido manualmente.</b>"); break
        except Exception as e:
            log.exception(f"Error ciclo: {e}"); tg_error(str(e))

        elapsed = time.time() - ts_start
        time.sleep(max(0, POLL_SECS - elapsed))


if __name__ == "__main__":
    while True:
        try:
            main()
        except KeyboardInterrupt:
            log.info("Detenido."); break
        except Exception as e:
            log.exception(f"CRASH: {e}")
            try: tg_error(f"CRASH — reinicio 30s: {str(e)[:200]}")
            except Exception: pass
            time.sleep(30)
