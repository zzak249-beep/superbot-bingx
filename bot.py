"""
SuperBot Main Orchestrator – v2.0
Loop: scan TODOS los pares → filtrar → abrir trades → gestionar posiciones → repetir

Mejoras:
  - Variables de entorno defensivas (no crashea sin ellas)
  - Sincronización de posiciones al arranque (anti-hedge bug)
  - Gestión de TP1/TP2/TP3
  - LIMIT entry para comisiones maker (0.02%)
  - Kill switch diario
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

# ── Variables de entorno (defensivo) ─────────────────────────────────
API_KEY    = os.environ.get("BINGX_API_KEY", "").strip()
SECRET_KEY = os.environ.get("BINGX_SECRET_KEY", "").strip()

if not API_KEY or not SECRET_KEY:
    raise RuntimeError(
        "Variables de entorno faltantes.\n"
        "Añade en Railway -> Variables:\n"
        "  BINGX_API_KEY\n"
        "  BINGX_SECRET_KEY"
    )

SCAN_PERIOD     = int(os.environ.get("SCAN_PERIOD_SECONDS", "900"))
DRY_RUN         = os.environ.get("DRY_RUN", "false").lower() == "true"
LIMIT_ENTRY     = os.environ.get("LIMIT_ENTRY", "true").lower() == "true"
SLIPPAGE_OFFSET = 0.0003


# ── Estado persistente ────────────────────────────────────────────────
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
        self._sync_positions_from_exchange()

    # ── Sincronización al arranque (anti-hedge bug) ───────────────────
    def _sync_positions_from_exchange(self):
        if DRY_RUN:
            return
        try:
            live = {p["symbol"]: p for p in self.client.get_positions()}

            # Eliminar del estado lo que ya no existe en BingX
            for s in list(self.state["open_trades"]):
                if s not in live:
                    log.info(f"Sync: eliminando {s} del estado (cerrado en BingX)")
                    del self.state["open_trades"][s]

            # Añadir posiciones reales que faltan en estado
            for sym, pos in live.items():
                if sym not in self.state["open_trades"]:
                    amt   = float(pos.get("positionAmt", 0))
                    side  = "LONG" if amt > 0 else "SHORT"
                    entry = float(pos.get("avgPrice", 0))
                    log.info(f"Sync: añadiendo {sym} {side} @ {entry}")
                    self.state["open_trades"][sym] = {
                        "direction": side, "entry": entry,
                        "sl": 0, "tp1": 0, "tp2": 0, "tp3": 0,
                        "qty": abs(amt), "qty_p": 3,
                        "tp1_hit": False, "tp2_hit": False,
                        "opened_at": datetime.utcnow().isoformat(),
                        "synced": True,
                    }
            save_state(self.state)
            log.info(f"Sync OK: {len(self.state['open_trades'])} posiciones activas")
        except Exception as e:
            log.error(f"Error sync: {e}")

    # ── Reset diario ──────────────────────────────────────────────────
    def _init_daily(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state["daily_date"] != today:
            balance = self.client.get_balance()
            self.risk.reset_daily(balance)
            self.state["daily_date"] = today
            save_state(self.state)
            log.info(f"Nuevo dia: {today} | Balance: {balance:.2f} USDT")

    def _get_precision(self, symbol: str) -> tuple[int, int]:
        info    = self.client.get_symbol_info(symbol)
        qty_p   = int(info.get("quantityPrecision", 3))
        price_p = int(info.get("pricePrecision", 4))
        return qty_p, price_p

    # ── Abrir trade ───────────────────────────────────────────────────
    def _open_trade(self, symbol: str, signal: Signal):
        if symbol in self.state["open_trades"]:
            return

        balance    = self.client.get_balance()
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

        if DRY_RUN:
            log.info(
                f"[DRY RUN] {symbol} {params.direction} x{params.quantity} "
                f"@ {params.entry_price} SL={params.sl_price} TP1={params.tp1_price}"
            )
            self.state["open_trades"][symbol] = {
                "direction": params.direction, "entry": params.entry_price,
                "sl": params.sl_price, "tp1": params.tp1_price,
                "tp2": params.tp2_price, "tp3": params.tp3_price,
                "qty": params.quantity, "qty_p": qty_p,
                "tp1_hit": False, "tp2_hit": False,
                "opened_at": datetime.utcnow().isoformat(),
            }
            save_state(self.state)
            return

        try:
            self.client.set_margin_type(symbol, "ISOLATED")
            self.client.set_leverage(symbol, params.leverage, params.direction)

            side     = "BUY"  if params.direction == "LONG" else "SELL"
            pos_side = params.direction

            if LIMIT_ENTRY:
                offset     = SLIPPAGE_OFFSET * params.entry_price
                limit_px   = (params.entry_price - offset if side == "BUY"
                              else params.entry_price + offset)
                limit_px   = round(limit_px, price_p)
                order_type = "LIMIT"
                price_arg  = limit_px
            else:
                order_type = "MARKET"
                price_arg  = None

            result   = self.client.place_order(
                symbol, side, pos_side, order_type, params.quantity,
                price=price_arg, stop_loss=params.sl_price,
            )
            order_id = result.get("data", {}).get("orderId", "?")
            log.info(
                f"Abierto {symbol} {params.direction} qty={params.quantity} "
                f"@ {price_arg or 'MARKET'} SL={params.sl_price} orderId={order_id}"
            )

            self.state["open_trades"][symbol] = {
                "direction": params.direction, "entry": params.entry_price,
                "sl": params.sl_price, "tp1": params.tp1_price,
                "tp2": params.tp2_price, "tp3": params.tp3_price,
                "qty": params.quantity, "qty_p": qty_p,
                "tp1_hit": False, "tp2_hit": False,
                "order_id": order_id,
                "opened_at": datetime.utcnow().isoformat(),
            }
            save_state(self.state)

        except Exception as e:
            log.error(f"Error abriendo {symbol}: {e}")

    # ── Gestionar posiciones ──────────────────────────────────────────
    def _manage_positions(self):
        try:
            positions = {p["symbol"]: p for p in self.client.get_positions()}
        except Exception as e:
            log.error(f"Error obteniendo posiciones: {e}")
            return

        for symbol, trade in list(self.state["open_trades"].items()):
            direction = trade["direction"]
            qty       = float(trade["qty"])
            tp1       = float(trade.get("tp1", 0))
            tp2       = float(trade.get("tp2", 0))
            tp3       = float(trade.get("tp3", 0))
            tp1_hit   = trade.get("tp1_hit", False)
            tp2_hit   = trade.get("tp2_hit", False)
            qty_p     = int(trade.get("qty_p", 3))
            synced    = trade.get("synced", False)

            # Posición cerrada externamente
            if not DRY_RUN and symbol not in positions:
                log.info(f"Posicion cerrada externamente: {symbol}")
                self.risk.record_pnl(0.0)
                del self.state["open_trades"][symbol]
                save_state(self.state)
                continue

            # Precio actual
            try:
                ticker = self.client.get_ticker(symbol)
                price  = float(ticker.get("lastPrice", trade["entry"]))
            except Exception:
                continue

            # Posiciones sincronizadas sin TPs: solo monitorear
            if synced and tp1 == 0:
                continue

            # TP1: 50% parcial
            if not tp1_hit and tp1 > 0:
                tp1_ok = (direction == "LONG" and price >= tp1) or \
                         (direction == "SHORT" and price <= tp1)
                if tp1_ok:
                    pqty = self.risk.partial_close_qty(qty, qty_p)
                    log.info(f"TP1 {symbol} | Cerrando {pqty}/{qty}")
                    if not DRY_RUN:
                        try:
                            self.client.close_position(symbol, direction, pqty)
                        except Exception as e:
                            log.error(f"Error TP1 {symbol}: {e}")
                    trade["tp1_hit"] = True
                    trade["qty"]     = round(qty - pqty, qty_p)
                    qty              = trade["qty"]
                    save_state(self.state)

            # TP2: 50% del restante
            if tp1_hit and not tp2_hit and tp2 > 0:
                tp2_ok = (direction == "LONG" and price >= tp2) or \
                         (direction == "SHORT" and price <= tp2)
                if tp2_ok:
                    pqty = self.risk.partial_close_qty(qty, qty_p)
                    log.info(f"TP2 {symbol} | Cerrando {pqty}/{qty}")
                    if not DRY_RUN:
                        try:
                            self.client.close_position(symbol, direction, pqty)
                        except Exception as e:
                            log.error(f"Error TP2 {symbol}: {e}")
                    trade["tp2_hit"] = True
                    trade["qty"]     = round(qty - pqty, qty_p)
                    qty              = trade["qty"]
                    save_state(self.state)

            # TP3: cierre total
            if tp2_hit and tp3 > 0:
                tp3_ok = (direction == "LONG" and price >= tp3) or \
                         (direction == "SHORT" and price <= tp3)
                if tp3_ok:
                    remaining = trade["qty"]
                    log.info(f"TP3 {symbol} | Cerrando restante {remaining}")
                    if not DRY_RUN:
                        try:
                            self.client.close_position(symbol, direction, remaining)
                            entry_p = float(trade["entry"])
                            pnl     = abs(price - entry_p) * remaining
                            self.risk.record_pnl(pnl)
                        except Exception as e:
                            log.error(f"Error TP3 {symbol}: {e}")
                    del self.state["open_trades"][symbol]
                    save_state(self.state)

    # ── Loop principal ────────────────────────────────────────────────
    def run(self):
        log.info(
            f"SuperBot v2.0 | DRY_RUN={DRY_RUN} | "
            f"SCAN_PERIOD={SCAN_PERIOD}s | LIMIT_ENTRY={LIMIT_ENTRY}"
        )
        while True:
            try:
                self._init_daily()
                self._manage_positions()

                balance    = self.client.get_balance()
                open_count = len(self.state["open_trades"])

                if self.risk.can_open_trade(open_count, balance):
                    results = self.scanner.scan()
                    opened  = 0
                    for result in results:
                        if not self.risk.can_open_trade(
                            len(self.state["open_trades"]), balance
                        ):
                            break
                        if result.symbol not in self.state["open_trades"]:
                            self._open_trade(result.symbol, result.signal)
                            opened += 1
                            time.sleep(0.5)
                    log.info(f"Trades abiertos: {opened}")
                else:
                    log.info(
                        f"Posiciones: {open_count} | Balance: {balance:.2f} | Sin capacidad"
                    )

                log.info(
                    f"Durmiendo {SCAN_PERIOD}s | "
                    f"Abiertos: {list(self.state['open_trades'].keys())}"
                )

            except Exception as e:
                log.error(f"Error loop: {e}", exc_info=True)

            time.sleep(SCAN_PERIOD)
