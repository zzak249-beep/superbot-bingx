SuperBot v4.1 — Main Orchestrator
Fixes vs v4:
  - _env() SCAN_PERIOD bug fixed (no more empty-list call)
  - from strategy import Signal (was signals in doc comments)
  - DRY_RUN: simulates TP/SL hits from live price so journal fills
  - All Railway env vars wired correctly
  - Balance fallback logic improved for DRY mode
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
import time
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bingx_client import BingXClient
from scanner import Scanner
from risk_manager import RiskManager, TradeParams
from strategy import Signal
import notifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("BOT")


# ── Env helpers ───────────────────────────────────────────────────────────────

def _env_str(key: str, default: str = "") -> str:
    val = os.environ.get(key, default)
    return val.strip().strip('"').strip("'") if val else default

def _env_float(key: str, default: float) -> float:
    try:
        return float(_env_str(key, str(default)))
    except (ValueError, TypeError):
        return default

def _env_int(key: str, default: int) -> int:
    try:
        return int(_env_str(key, str(default)))
    except (ValueError, TypeError):
        return default

def _env_bool(key: str, default: bool = False) -> bool:
    return _env_str(key, str(default)).lower() == "true"


# ── Config ────────────────────────────────────────────────────────────────────

API_KEY    = _env_str("BINGX_API_KEY")
SECRET_KEY = _env_str("BINGX_API_SECRET") or _env_str("BINGX_SECRET_KEY")

if not API_KEY or not SECRET_KEY:
    raise EnvironmentError("BINGX_API_KEY and BINGX_API_SECRET are required")

SCAN_PERIOD         = _env_int("SCAN_PERIOD_SECONDS",  900)
DRY_RUN             = _env_bool("DRY_RUN",             False)
USE_MARKET_ENTRY    = _env_bool("USE_MARKET_ENTRY",    True)
LIMIT_ORDER_TIMEOUT = _env_int("LIMIT_ORDER_TIMEOUT",  120)

RISK_PER_TRADE      = _env_float("RISK_PER_TRADE",      0.02)
MAX_POSITIONS       = _env_int("MAX_OPEN_TRADES",        4)
LEVERAGE            = _env_int("LEVERAGE",               10)
DAILY_LOSS_LIMIT    = _env_float("DAILY_LOSS_LIMIT",     0.06)
MIN_CONFIDENCE      = _env_float("MIN_CONFIDENCE",       0.52)
SYMBOL_COOLDOWN_MIN = _env_int("SYMBOL_COOLDOWN_MIN",    45)
TRAILING_STOP_RATE  = _env_float("TRAILING_STOP_RATE",   0.025)

# DRY balance seed (used if real balance unavailable)
DRY_BALANCE         = _env_float("DRY_BALANCE",         1000.0)

STATE_FILE   = "/tmp/superbot_state.json"
JOURNAL_FILE = Path("/tmp/trade_journal.json")


# ── State / Journal ───────────────────────────────────────────────────────────

def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"open_trades": {}, "daily_date": "", "cooldowns": {}}


def save_state(state: dict):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log.error(f"Error guardando estado: {e}")


def load_journal() -> list:
    if JOURNAL_FILE.exists():
        try:
            return json.loads(JOURNAL_FILE.read_text())
        except Exception:
            pass
    return []


def save_journal(journal: list):
    JOURNAL_FILE.write_text(json.dumps(journal, indent=2))


def record_trade(journal: list, symbol: str, direction: str, entry: float,
                 exit_price: float, pnl: float, fees: float, reason: str):
    journal.append({
        "ts":        datetime.utcnow().isoformat(),
        "symbol":    symbol,
        "direction": direction,
        "entry":     entry,
        "exit":      exit_price,
        "pnl":       round(pnl,  4),
        "fees":      round(fees, 4),
        "net_pnl":   round(pnl - fees, 4),
        "win":       (pnl - fees) > 0,
        "reason":    reason,
    })
    save_journal(journal)


def get_winrate(journal: list, last_n: int = 30) -> float:
    recent = journal[-last_n:] if len(journal) >= last_n else journal
    if not recent:
        return 0.5
    return sum(1 for t in recent if t.get("win")) / len(recent)


# ── Helpers ───────────────────────────────────────────────────────────────────

def calc_rr(signal: Signal) -> float:
    try:
        sl_dist  = abs(signal.entry - signal.sl)
        tp1_dist = abs(signal.tp1   - signal.entry)
        if sl_dist < 1e-10:
            return 0.0
        return round(tp1_dist / sl_dist, 2)
    except Exception:
        return 0.0


def dynamic_min_confidence(journal: list) -> float:
    base = MIN_CONFIDENCE * 100  # score is 0-100
    wr   = get_winrate(journal, 20)
    if len(journal) < 10:
        return base
    if wr < 0.40:
        return min(base + 10, 78)
    if wr > 0.65:
        return max(base - 8,  45)
    return base


# ══════════════════════════════════════════════════════════════════════════════
class SuperBot:

    def __init__(self):
        self.client  = BingXClient(API_KEY, SECRET_KEY)
        self.scanner = Scanner(self.client)
        self.risk    = RiskManager(
            risk_pct         = RISK_PER_TRADE,
            max_pos          = MAX_POSITIONS,
            leverage         = LEVERAGE,
            daily_loss_limit = DAILY_LOSS_LIMIT,
        )
        self.state   = load_state()
        self.journal = load_journal()
        self._sym_info_cache: dict = {}
        self._dry_balance: float   = DRY_BALANCE   # tracks simulated balance

        log.info(
            f"🤖 SuperBot v4.1 | DRY={DRY_RUN} | "
            f"Risk={RISK_PER_TRADE*100:.1f}% | Lev={LEVERAGE}x | "
            f"MaxPos={MAX_POSITIONS} | MinConf={MIN_CONFIDENCE:.2f} | "
            f"Journal={len(self.journal)}"
        )
        self._sync_positions_on_start()
        self._init_daily()

    # ── Init ──────────────────────────────────────────────────────────────────

    def _sync_positions_on_start(self):
        if DRY_RUN:
            return
        try:
            live  = {p["symbol"] for p in self.client.get_positions()
                     if abs(float(p.get("positionAmt", 0) or 0)) > 0}
            stale = [s for s in list(self.state["open_trades"]) if s not in live]
            for s in stale:
                log.info(f"🔄 Sync: eliminando posición huérfana {s}")
                del self.state["open_trades"][s]
            if stale:
                save_state(self.state)
            log.info(f"🔄 Sync: {len(live)} vivas, {len(stale)} huérfanas limpiadas")
        except Exception as e:
            log.warning(f"Sync positions error (no crítico): {e}")

    def _init_daily(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state.get("daily_date") != today:
            balance = self._safe_get_balance()
            self.risk.reset_daily(balance)
            self.state["daily_date"] = today
            self.state.setdefault("cooldowns", {})
            save_state(self.state)
            notifier.notify_startup(balance, DRY_RUN)

    def _safe_get_balance(self) -> float:
        if DRY_RUN:
            return self._dry_balance
        for attempt in range(3):
            try:
                bal = self.client.get_balance()
                if bal > 0:
                    return bal
                log.warning(f"Balance $0 intento {attempt+1}/3")
                time.sleep(1)
            except Exception as e:
                log.error(f"get_balance error intento {attempt+1}: {e}")
                time.sleep(1)
        log.error("❌ Balance $0 tras 3 intentos → verifica API keys y permisos en BingX")
        return 0.0

    def _get_precision(self, symbol: str) -> tuple:
        if symbol not in self._sym_info_cache:
            try:
                info = self.client.get_symbol_info(symbol)
                self._sym_info_cache[symbol] = (
                    int(info.get("quantityPrecision", 3)),
                    int(info.get("pricePrecision",   4)),
                )
            except Exception:
                self._sym_info_cache[symbol] = (3, 4)
        return self._sym_info_cache[symbol]

    def _is_in_cooldown(self, symbol: str) -> bool:
        last_ts = self.state.get("cooldowns", {}).get(symbol)
        if not last_ts:
            return False
        elapsed = (time.time() - last_ts) / 60
        if elapsed < SYMBOL_COOLDOWN_MIN:
            log.info(f"⏳ {symbol} cooldown ({elapsed:.0f}/{SYMBOL_COOLDOWN_MIN}min)")
            return True
        return False

    def _set_cooldown(self, symbol: str):
        self.state.setdefault("cooldowns", {})[symbol] = time.time()
        save_state(self.state)

    # ── Open trade ────────────────────────────────────────────────────────────

    def _open_trade(self, symbol: str, signal: Signal):
        if symbol in self.state["open_trades"]:
            return
        if self._is_in_cooldown(symbol):
            return

        rr         = calc_rr(signal)
        confidence = signal.score   # already 0-100

        if rr < 1.2:
            log.info(f"❌ [{symbol}] RR={rr:.1f} < 1.2 → skip")
            return
        if hasattr(signal, "vol_ok") and not signal.vol_ok:
            log.info(f"❌ [{symbol}] Volumen insuficiente → skip")
            return

        min_conf = dynamic_min_confidence(self.journal)
        if confidence < min_conf:
            log.info(f"❌ [{symbol}] score={confidence:.0f} < {min_conf:.0f} RR={rr:.1f}x")
            return

        balance    = self._safe_get_balance()
        open_count = len(self.state["open_trades"])

        if not self.risk.can_open_trade(open_count, balance):
            return

        qty_p, price_p = self._get_precision(symbol)
        params = self.risk.size_position(
            symbol, signal.direction,
            signal.entry, signal.sl,
            signal.tp1, signal.tp2, signal.tp3,
            balance, qty_p, price_p,
        )
        if not params:
            return

        trade_id   = str(uuid.uuid4())[:8]
        trade_data = {
            "trade_id":     trade_id,
            "direction":    signal.direction,
            "entry":        params.entry_price,
            "sl":           params.sl_price,
            "sl_original":  params.sl_price,
            "tp1":          params.tp1_price,
            "tp2":          params.tp2_price,
            "tp3":          params.tp3_price,
            "qty":          params.quantity,
            "qty_full":     params.quantity,
            "qty_p":        qty_p,
            "price_p":      price_p,
            "atr":          signal.atr,
            "tp1_hit":      False,
            "tp2_hit":      False,
            "breakeven":    False,
            "trail_active": False,
            "opened_at":    datetime.utcnow().isoformat(),
            "est_fee":      params.est_fee,
            "rr":           rr,
            "regime":       getattr(signal, "regime", "UNKNOWN"),
            "tier":         getattr(signal, "tier",   "?"),
        }

        # ── DRY RUN ──────────────────────────────────────────────────────────
        if DRY_RUN:
            log.info(
                f"🔵 [DRY] {symbol} {signal.direction} x{params.quantity} "
                f"@ {params.entry_price} SL={params.sl_price} "
                f"TP1={params.tp1_price} TP2={params.tp2_price} "
                f"score={confidence:.0f} RR={rr:.1f}x [{signal.tier}]"
            )
            trade_data["order_id"] = "DRY-" + trade_id
            trade_data["status"]   = "FILLED"
            self.state["open_trades"][symbol] = trade_data
            save_state(self.state)
            notifier.notify_trade_opened(
                symbol, signal.direction, params.quantity,
                params.entry_price, params.sl_price,
                params.tp1_price, params.tp2_price,
                params.notional, dry_run=True,
            )
            return

        # ── LIVE ──────────────────────────────────────────────────────────────
        try:
            try:
                self.client.set_margin_type(symbol, "ISOLATED")
            except Exception:
                pass
            self.client.set_leverage(symbol, params.leverage)

            side     = "BUY"  if signal.direction == "LONG" else "SELL"
            pos_side = signal.direction

            result = self.client.place_order(
                symbol           = symbol,
                side             = side,
                position_side    = pos_side,
                order_type       = "MARKET",
                quantity         = params.quantity,
                stop_loss        = params.sl_price,
                take_profit      = params.tp1_price,
                client_order_id  = f"sb_{trade_id}",
            )

            order_id = result.get("data", {}).get("orderId", "")
            if not order_id:
                log.error(f"❌ No orderId {symbol}: {result}")
                return

            # Trailing stop for remaining 60%
            try:
                self.client.place_trailing_stop(
                    symbol           = symbol,
                    side             = "SELL" if signal.direction == "LONG" else "BUY",
                    position_side    = pos_side,
                    quantity         = round(params.quantity * 0.60, qty_p),
                    activation_price = params.tp1_price,
                    price_rate       = TRAILING_STOP_RATE,
                )
                log.info(f"📈 Trailing stop {symbol}: rate={TRAILING_STOP_RATE*100:.1f}%")
            except Exception as e:
                log.warning(f"Trailing stop no colocado {symbol}: {e}")

            log.info(
                f"✅ MARKET {symbol} {signal.direction} qty={params.quantity} "
                f"SL={params.sl_price} TP1={params.tp1_price} id={order_id} "
                f"score={confidence:.0f}"
            )

            trade_data["order_id"] = str(order_id)
            trade_data["status"]   = "FILLED"
            self.state["open_trades"][symbol] = trade_data
            save_state(self.state)
            notifier.notify_trade_opened(
                symbol, signal.direction, params.quantity,
                params.entry_price, params.sl_price,
                params.tp1_price, params.tp2_price,
                params.notional, dry_run=False,
            )

        except Exception as e:
            log.error(f"❌ Error abriendo {symbol}: {e}", exc_info=True)

    # ── Manage positions ──────────────────────────────────────────────────────

    def _manage_positions(self):
        active = {s: t for s, t in self.state["open_trades"].items()
                  if t.get("status") == "FILLED"}
        if not active:
            return

        exchange_positions = {}
        if not DRY_RUN:
            try:
                for p in self.client.get_positions():
                    if abs(float(p.get("positionAmt", 0) or 0)) > 0:
                        exchange_positions[p["symbol"]] = p
            except Exception as e:
                log.error(f"Error obteniendo posiciones: {e}")
                return

        for symbol, trade in list(active.items()):
            direction = trade["direction"]
            qty       = trade["qty"]
            qty_full  = trade.get("qty_full", qty)
            tp1       = trade["tp1"]
            tp2       = trade["tp2"]
            entry     = trade.get("entry", 0)
            sl        = trade.get("sl",    0)
            atr_v     = trade.get("atr",   0)
            qty_p     = trade.get("qty_p", 3)
            price_p   = trade.get("price_p", 4)
            tp1_hit   = trade.get("tp1_hit",  False)
            breakeven = trade.get("breakeven", False)
            est_fee   = trade.get("est_fee",   0)

            # Closed externally (live only)
            if not DRY_RUN and symbol not in exchange_positions:
                log.info(f"📤 Cerrada externamente: {symbol}")
                loss = abs(entry - sl) * qty_full * 0.5 if entry and sl else 0
                self.risk.record_pnl(-loss, est_fee)
                notifier.notify_sl_hit(symbol, sl, loss)
                record_trade(self.journal, symbol, direction, entry, sl,
                             -loss, est_fee, "SL_HIT")
                self._set_cooldown(symbol)
                del self.state["open_trades"][symbol]
                save_state(self.state)
                continue

            # Current price
            try:
                ticker = self.client.get_ticker(symbol)
                price  = float(ticker.get("lastPrice", 0) or
                               ticker.get("price", 0) or 0)
            except Exception:
                continue
            if price <= 0:
                continue

            # ── Breakeven when 70% to TP1 ─────────────────────────────────────
            if not breakeven and not tp1_hit and atr_v > 0:
                progress = (
                    (price - entry) / max(tp1 - entry, 1e-10) if direction == "LONG"
                    else (entry - price) / max(entry - tp1, 1e-10)
                )
                if progress >= 0.70:
                    buffer = atr_v * 0.15
                    new_sl = round(
                        entry + buffer if direction == "LONG" else entry - buffer,
                        price_p
                    )
                    if not DRY_RUN:
                        try:
                            self.client.update_sl(symbol, direction, new_sl)
                        except Exception as e:
                            log.warning(f"Update SL {symbol}: {e}")
                    self.state["open_trades"][symbol]["sl"]        = new_sl
                    self.state["open_trades"][symbol]["breakeven"] = True
                    save_state(self.state)
                    log.info(f"🔒 Breakeven {symbol} SL→{new_sl} ({progress:.0%})")

            # ── SL hit check (DRY: simulate from live price) ──────────────────
            if not DRY_RUN:
                sl_hit = (direction == "LONG"  and price <= sl) or \
                         (direction == "SHORT" and price >= sl)
                if sl_hit:
                    loss = abs(entry - sl) * qty_full
                    self.risk.record_pnl(-loss, est_fee)
                    notifier.notify_sl_hit(symbol, sl, loss)
                    record_trade(self.journal, symbol, direction, entry, sl,
                                 -loss, est_fee, "SL_HIT")
                    self._set_cooldown(symbol)
                    del self.state["open_trades"][symbol]
                    save_state(self.state)
                    if DRY_RUN:
                        self._dry_balance -= loss + est_fee
                    continue

            # DRY: check SL hit
            if DRY_RUN:
                sl_hit = (direction == "LONG"  and price <= sl) or \
                         (direction == "SHORT" and price >= sl)
                if sl_hit:
                    loss = abs(entry - sl) * qty_full
                    self.risk.record_pnl(-loss, est_fee)
                    notifier.notify_sl_hit(symbol, sl, loss)
                    record_trade(self.journal, symbol, direction, entry, sl,
                                 -loss, est_fee, "SL_HIT_DRY")
                    self._dry_balance -= loss + est_fee
                    self._set_cooldown(symbol)
                    del self.state["open_trades"][symbol]
                    save_state(self.state)
                    log.info(f"🔴 [DRY] SL hit {symbol} @ {price} | loss=${loss:.2f} | "
                             f"balance=${self._dry_balance:.2f}")
                    continue

            # ── TP1: partial close 40% ─────────────────────────────────────────
            if not tp1_hit:
                tp1_hit_now = (direction == "LONG"  and price >= tp1) or \
                              (direction == "SHORT" and price <= tp1)
                if tp1_hit_now:
                    partial = round(qty * 0.40, qty_p)
                    if not DRY_RUN:
                        try:
                            self.client.close_position_partial(symbol, direction, partial)
                        except Exception as e:
                            log.error(f"Error cierre parcial TP1 {symbol}: {e}")
                    est_pnl = abs(price - entry) * partial
                    fee_tp1 = partial * price * 0.0005
                    net_pnl = est_pnl - fee_tp1
                    self.risk.record_pnl(net_pnl, fee_tp1)
                    if DRY_RUN:
                        self._dry_balance += net_pnl
                    notifier.notify_tp_hit(symbol, 1, price, partial, net_pnl)
                    remaining = round(qty - partial, qty_p)
                    self.state["open_trades"][symbol]["tp1_hit"] = True
                    self.state["open_trades"][symbol]["qty"]     = remaining
                    save_state(self.state)
                    log.info(f"💰 TP1 {symbol} @ {price} pnl=${net_pnl:.2f} rest={remaining}"
                             + (f" | DRY balance=${self._dry_balance:.2f}" if DRY_RUN else ""))

            # ── TP2: close rest ────────────────────────────────────────────────
            elif not trade.get("tp2_hit", False):
                tp2_hit_now = (direction == "LONG"  and price >= tp2) or \
                              (direction == "SHORT" and price <= tp2)
                if tp2_hit_now:
                    remaining = self.state["open_trades"][symbol]["qty"]
                    if not DRY_RUN:
                        try:
                            self.client.close_position_partial(symbol, direction, remaining)
                        except Exception as e:
                            log.error(f"Error cierre TP2 {symbol}: {e}")
                    est_pnl = abs(price - entry) * remaining
                    fee_tp2 = remaining * price * 0.0005
                    net_pnl = est_pnl - fee_tp2
                    self.risk.record_pnl(net_pnl, fee_tp2)
                    if DRY_RUN:
                        self._dry_balance += net_pnl
                    notifier.notify_tp_hit(symbol, 2, price, remaining, net_pnl)
                    record_trade(self.journal, symbol, direction, entry, price,
                                 est_pnl, est_fee + fee_tp2, "TP2")
                    self._set_cooldown(symbol)
                    del self.state["open_trades"][symbol]
                    save_state(self.state)
                    log.info(f"🎯 TP2 {symbol} @ {price} pnl=${net_pnl:.2f}"
                             + (f" | DRY balance=${self._dry_balance:.2f}" if DRY_RUN else ""))

    # ── Cleanup stale triggers ────────────────────────────────────────────────

    def _cleanup_stale_triggers(self):
        if DRY_RUN:
            return
        try:
            open_orders = self.client.get_open_orders()
            now = time.time() * 1000
            for o in open_orders:
                created = float(o.get("time", now))
                age_h   = (now - created) / 3_600_000
                otype   = o.get("type", "")
                if otype in ("TRIGGER", "TRIGGER_LIMIT") and age_h > 2:
                    sym = o.get("symbol", "")
                    oid = o.get("orderId", "")
                    log.info(f"🧹 Cancelando trigger huérfano {sym} id={oid} ({age_h:.1f}h)")
                    try:
                        self.client.cancel_order(sym, oid)
                    except Exception:
                        pass
        except Exception as e:
            log.debug(f"cleanup triggers: {e}")

    # ── Status log ────────────────────────────────────────────────────────────

    def _log_status(self):
        balance = self._safe_get_balance()
        syms    = list(self.state["open_trades"].keys())
        stats   = self.risk.get_stats()
        wr      = get_winrate(self.journal, 20)
        log.info(
            f"📊 Status | Balance: ${balance:.2f} | "
            f"Open: {syms} | "
            f"DailyPnL: {stats['daily_pnl']:+.2f} | "
            f"Fees: ${stats['total_fees']:.3f} | "
            f"WinRate(20): {wr*100:.0f}% | "
            f"Trades: {stats['trades_today']}"
        )

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        log.info(
            f"🚀 SuperBot v4.1 | DRY={DRY_RUN} | "
            f"SCAN={SCAN_PERIOD}s | Lev={LEVERAGE}x | "
            f"Risk={RISK_PER_TRADE*100:.1f}%"
        )
        cycle = 0
        while True:
            try:
                cycle += 1
                log.info(f"{'='*44} CICLO {cycle} {'='*44}")
                self._init_daily()
                self._cleanup_stale_triggers()
                self._manage_positions()

                balance    = self._safe_get_balance()
                open_count = len(self.state["open_trades"])

                if self.risk.can_open_trade(open_count, balance):
                    results    = self.scanner.scan()
                    slots      = MAX_POSITIONS - open_count
                    opened     = 0
                    for result in results:
                        if opened >= slots:
                            break
                        sym = result.symbol
                        if sym not in self.state["open_trades"]:
                            self._open_trade(sym, result.signal)
                            opened += 1
                            time.sleep(1.5)
                else:
                    log.info(f"📊 Sin slots ({open_count}/{MAX_POSITIONS}) o balance bajo")

                self._log_status()
                log.info(f"😴 Esperando {SCAN_PERIOD}s...")

            except KeyboardInterrupt:
                log.info("Bot detenido por usuario.")
                break
            except Exception as e:
                log.error(f"💥 Error en loop: {e}", exc_info=True)

            time.sleep(SCAN_PERIOD)


if __name__ == "__main__":
    SuperBot().run()
