"""
SuperBot Main Orchestrator
Loop: scan → filter → open trades → manage positions → repeat
Commission saving strategy:
  - LIMIT entry orders (maker fee 0.02% vs taker 0.05%)
  - Partial close at TP1 to lock profit
  - Trailing via band4 crossover
"""
import logging, os, time, json
from datetime import datetime, timezone
from typing import Optional

from bingx_client import BingXClient
from scanner import Scanner
from risk_manager import RiskManager, TradeParams
from strategy import Signal

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("BOT")

# ── Config from ENV ───────────────────────────────────────────────────
API_KEY     = os.environ["BINGX_API_KEY"]
SECRET_KEY  = os.environ["BINGX_SECRET_KEY"]
SCAN_PERIOD = int(os.environ.get("SCAN_PERIOD_SECONDS", "900"))   # 15 min
DRY_RUN     = os.environ.get("DRY_RUN", "false").lower() == "true"
LIMIT_ENTRY = os.environ.get("LIMIT_ENTRY", "true").lower() == "true"  # maker orders
SLIPPAGE_OFFSET = 0.0003   # 0.03% offset for limit order vs market price

# ── Tracked trades ────────────────────────────────────────────────────
STATE_FILE = "/tmp/bot_state.json"

def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"open_trades": {}, "daily_date": "", "trade_log": []}

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


class SuperBot:
    def __init__(self):
        self.client  = BingXClient(API_KEY, SECRET_KEY)
        self.scanner = Scanner(self.client)
        self.risk    = RiskManager()
        self.state   = load_state()
        self._init_daily()

    def _init_daily(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state["daily_date"] != today:
            balance = self.client.get_balance()
            self.risk.reset_daily(balance)
            self.state["daily_date"] = today
            save_state(self.state)
            log.info(f"📅 New trading day: {today} | Balance: {balance:.2f} USDT")

    # ── Symbol meta (precision) ───────────────────────────────────────
    def _get_precision(self, symbol: str) -> tuple[int, int]:
        """Returns (qty_precision, price_precision)."""
        info = self.client.get_symbol_info(symbol)
        qty_p   = int(info.get("quantityPrecision", 3))
        price_p = int(info.get("pricePrecision", 4))
        return qty_p, price_p

    # ── Open new trade ────────────────────────────────────────────────
    def _open_trade(self, symbol: str, signal: Signal):
        if symbol in self.state["open_trades"]:
            log.info(f"Already in trade for {symbol}, skip.")
            return

        balance = self.client.get_balance()
        open_count = len(self.state["open_trades"])
        if not self.risk.can_open_trade(open_count, balance):
            return

        qty_p, price_p = self._get_precision(symbol)
        params = self.risk.size_position(
            symbol, signal.direction,
            signal.entry, signal.sl, signal.tp1, signal.tp2, signal.tp3,
            balance, qty_p, price_p,
        )
        if not params:
            return

        if DRY_RUN:
            log.info(f"🔵 [DRY RUN] Would open {symbol} {params.direction} x{params.quantity} @ {params.entry_price}")
            self.state["open_trades"][symbol] = {
                "direction": params.direction, "entry": params.entry_price,
                "sl": params.sl_price, "tp1": params.tp1_price,
                "tp2": params.tp2_price, "tp3": params.tp3_price,
                "qty": params.quantity, "qty_p": qty_p, "tp1_hit": False,
                "opened_at": datetime.utcnow().isoformat(),
            }
            save_state(self.state)
            return

        try:
            # Set leverage + isolated margin
            self.client.set_margin_type(symbol, "ISOLATED")
            self.client.set_leverage(symbol, params.leverage, params.direction)

            side     = "BUY"  if params.direction == "LONG" else "SELL"
            pos_side = params.direction

            # Commission saving: use LIMIT order (maker fee)
            if LIMIT_ENTRY:
                offset = SLIPPAGE_OFFSET * params.entry_price
                limit_px = params.entry_price - offset if side == "BUY" else params.entry_price + offset
                limit_px = round(limit_px, price_p)
                order_type = "LIMIT"
                price_arg  = limit_px
            else:
                order_type = "MARKET"
                price_arg  = None

            result = self.client.place_order(
                symbol, side, pos_side, order_type, params.quantity,
                price=price_arg,
                stop_loss=params.sl_price,
            )
            order_id = result.get("data", {}).get("orderId", "?")
            log.info(
                f"✅ Opened {symbol} {params.direction} qty={params.quantity} "
                f"@ {price_arg or 'MARKET'} SL={params.sl_price} | orderId={order_id}"
            )

            self.state["open_trades"][symbol] = {
                "direction": params.direction, "entry": params.entry_price,
                "sl": params.sl_price, "tp1": params.tp1_price,
                "tp2": params.tp2_price, "tp3": params.tp3_price,
                "qty": params.quantity, "qty_p": qty_p, "tp1_hit": False,
                "order_id": order_id,
                "opened_at": datetime.utcnow().isoformat(),
            }
            save_state(self.state)

        except Exception as e:
            log.error(f"❌ Failed to open {symbol}: {e}")

    # ── Manage existing trades ────────────────────────────────────────
    def _manage_positions(self):
        positions = {p["symbol"]: p for p in self.client.get_positions()}

        for symbol, trade in list(self.state["open_trades"].items()):
            direction = trade["direction"]
            qty       = trade["qty"]
            tp1       = trade["tp1"]
            tp2       = trade["tp2"]
            tp1_hit   = trade.get("tp1_hit", False)
            qty_p     = trade.get("qty_p", 3)

            # Check if position still exists
            if not DRY_RUN and symbol not in positions:
                pnl_usdt = 0.0  # unknown, was stopped out or TP hit
                log.info(f"📤 Position closed externally: {symbol}")
                self.risk.record_pnl(pnl_usdt)
                del self.state["open_trades"][symbol]
                save_state(self.state)
                continue

            # Get current price
            try:
                ticker = self.client.get_ticker(symbol)
                price  = float(ticker.get("lastPrice", trade["entry"]))
            except Exception:
                continue

            # TP1 partial close (50%)
            if not tp1_hit:
                tp1_reached = (direction == "LONG" and price >= tp1) or \
                              (direction == "SHORT" and price <= tp1)
                if tp1_reached:
                    partial_qty = self.risk.partial_close_qty(qty, qty_p)
                    log.info(f"💰 TP1 hit {symbol}! Closing {partial_qty} of {qty}")
                    if not DRY_RUN:
                        try:
                            self.client.close_position(symbol, direction, partial_qty)
                        except Exception as e:
                            log.error(f"Partial close error {symbol}: {e}")
                    self.state["open_trades"][symbol]["tp1_hit"] = True
                    self.state["open_trades"][symbol]["qty"] = round(qty - partial_qty, qty_p)
                    save_state(self.state)

            # TP2 full close
            tp2_reached = (direction == "LONG" and price >= tp2) or \
                          (direction == "SHORT" and price <= tp2)
            if tp2_reached and tp1_hit:
                remaining = self.state["open_trades"][symbol]["qty"]
                log.info(f"🎯 TP2 hit {symbol}! Closing remaining {remaining}")
                if not DRY_RUN:
                    try:
                        self.client.close_position(symbol, direction, remaining)
                        pnl = abs(price - trade["entry"]) * (qty + remaining)
                        self.risk.record_pnl(pnl)
                    except Exception as e:
                        log.error(f"TP2 close error {symbol}: {e}")
                del self.state["open_trades"][symbol]
                save_state(self.state)

    # ── Main loop ─────────────────────────────────────────────────────
    def run(self):
        log.info(f"🤖 SuperBot starting | DRY_RUN={DRY_RUN} | SCAN_PERIOD={SCAN_PERIOD}s")
        while True:
            try:
                self._init_daily()

                # Manage existing first
                self._manage_positions()

                # Scan only if we have capacity
                balance     = self.client.get_balance()
                open_count  = len(self.state["open_trades"])
                if self.risk.can_open_trade(open_count, balance):
                    results = self.scanner.scan()
                    for result in results:
                        if len(self.state["open_trades"]) >= 5:
                            break
                        if result.symbol not in self.state["open_trades"]:
                            self._open_trade(result.symbol, result.signal)
                            time.sleep(0.5)
                else:
                    log.info(f"📊 Holding {open_count} positions, no new entries.")

                log.info(f"😴 Sleeping {SCAN_PERIOD}s | Open: {list(self.state['open_trades'].keys())}")

            except Exception as e:
                log.error(f"Loop error: {e}", exc_info=True)

            time.sleep(SCAN_PERIOD)
