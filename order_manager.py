"""
order_manager.py — Ejecución de trades y gestión de posiciones abiertas
"""
import logging
import time
from typing import Optional

import config as C
from bingx_client import BingXClient
from risk_manager import RiskManager
from strategy import Signal
from telegram_notifier import TelegramNotifier

log = logging.getLogger(__name__)


class OrderManager:
    def __init__(self, client: BingXClient, risk: RiskManager,
                 tg: TelegramNotifier):
        self.client   = client
        self.risk     = risk
        self.tg       = tg
        self._active  = {}  # symbol → {direction, sl, tp, qty, entry, atr}

    # ── Setup inicial ──────────────────────────────────────────

    def setup_symbol(self, symbol: str):
        """Configura apalancamiento y margen al iniciar"""
        try:
            self.client.set_leverage(symbol, C.LEVERAGE)
            self.client.set_margin_type(symbol, "ISOLATED")
        except Exception as e:
            log.warning(f"Setup {symbol}: {e}")

    def sync_positions(self, symbol: str):
        """Sincroniza posiciones abiertas desde BingX al arrancar"""
        positions = self.client.get_positions(symbol)
        for p in positions:
            amt = float(p.get("positionAmt", 0))
            if amt == 0:
                continue
            direction = "LONG" if amt > 0 else "SHORT"
            entry = float(p.get("avgPrice", 0))
            atr   = entry * 0.005  # estimado si no tenemos ATR
            sl    = (entry - atr * C.SL_ATR_MULT if direction == "LONG"
                     else entry + atr * C.SL_ATR_MULT)
            self._active[symbol] = {
                "direction": direction,
                "sl":        sl,
                "tp":        0.0,
                "qty":       abs(amt),
                "entry":     entry,
                "atr":       atr,
                "open_time": time.time(),
            }
            log.info(f"Posición recuperada: {direction} {abs(amt)} {symbol} @ {entry}")

    # ── Nueva entrada ─────────────────────────────────────────

    def execute_signal(self, symbol: str, signal: Signal,
                       balance: float) -> bool:
        if signal.direction == "NONE":
            return False

        positions = self.client.get_positions(symbol)
        n_open = len(positions)

        # ¿Hay posición contraria? → cerrar primero
        for p in positions:
            amt = float(p.get("positionAmt", 0))
            if (signal.direction == "LONG"  and amt < 0) or \
               (signal.direction == "SHORT" and amt > 0):
                log.info(f"Cerrando posición contraria antes de invertir")
                self._close_position(symbol, p)
                time.sleep(1)
                n_open -= 1

        # Validaciones riesgo
        ok, reason = self.risk.can_trade(balance, n_open)
        if not ok:
            log.info(f"Trade bloqueado: {reason}")
            return False

        ok2, reason2 = self.risk.can_trade_direction(signal.direction, positions)
        if not ok2:
            log.info(f"Dirección bloqueada: {reason2}")
            return False

        # Sizing
        qty = self.risk.position_size(balance, signal, signal.entry)
        if qty <= 0:
            log.warning("Cantidad calculada = 0, abortando")
            return False

        # Ejecutar orden de mercado
        side = "BUY" if signal.direction == "LONG" else "SELL"
        result = self.client.place_market_order(symbol, side, qty)

        if not result or result.get("code", -1) != 0:
            log.error(f"Error en orden: {result}")
            self.tg.send_error(f"❌ Error orden {side} {symbol}: {result}")
            return False

        # Guardar estado local
        self._active[symbol] = {
            "direction": signal.direction,
            "sl":        signal.sl,
            "tp":        signal.tp,
            "qty":       qty,
            "entry":     signal.entry,
            "atr":       signal.atr,
            "open_time": time.time(),
        }

        self.tg.send_entry(symbol, signal, qty, balance)
        log.info(f"✅ Posición abierta: {signal.direction} {qty:.6f} {symbol}")
        return True

    # ── Gestión de posiciones abiertas ────────────────────────

    def manage_positions(self, symbol: str, current_price: float,
                         current_atr: float):
        """
        Ejecutar en cada ciclo de 3min.
        Comprueba SL, TP y trailing stop.
        """
        state = self._active.get(symbol)
        if not state:
            return

        positions = self.client.get_positions(symbol)
        if not positions:
            # Posición cerrada externamente (TP/SL en exchange)
            log.info(f"Posición {symbol} cerrada externamente")
            self._on_closed(symbol, current_price, "EXCHANGE")
            return

        direction = state["direction"]
        sl        = state["sl"]
        tp        = state["tp"]
        entry     = state["entry"]
        qty       = state["qty"]

        # ── Trailing Stop ────────────────────────────────────
        new_sl = self.risk.trailing_sl(direction, current_price, sl, current_atr)
        if new_sl != sl:
            log.info(f"Trailing SL: {sl:.4f} → {new_sl:.4f}")
            state["sl"] = new_sl

        # ── Verificar SL manual ──────────────────────────────
        sl_hit = (direction == "LONG"  and current_price <= state["sl"]) or \
                 (direction == "SHORT" and current_price >= state["sl"])

        tp_hit = tp > 0 and (
            (direction == "LONG"  and current_price >= tp) or
            (direction == "SHORT" and current_price <= tp)
        )

        if sl_hit or tp_hit:
            reason = "TP" if tp_hit else "SL"
            log.info(f"{reason} alcanzado @ {current_price:.4f} | {symbol}")
            pos = positions[0]
            self._close_position(symbol, pos)
            self._on_closed(symbol, current_price, reason)

    def _close_position(self, symbol: str, position: dict):
        self.client.cancel_all_orders(symbol)
        self.client.close_position(symbol, position)

    def _on_closed(self, symbol: str, close_price: float, reason: str):
        state = self._active.pop(symbol, None)
        if not state:
            return
        entry     = state["entry"]
        direction = state["direction"]
        qty       = state["qty"]

        pnl = (close_price - entry) * qty * C.LEVERAGE
        if direction == "SHORT":
            pnl = -pnl

        self.risk.record_trade_result(pnl)
        self.tg.send_close(symbol, direction, entry, close_price,
                           pnl, reason, qty)

    def has_position(self, symbol: str) -> bool:
        return symbol in self._active

    def get_active_state(self, symbol: str) -> Optional[dict]:
        return self._active.get(symbol)
