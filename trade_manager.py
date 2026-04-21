"""
Trade Manager — gestiona el ciclo completo de un trade:
  - Sizing basado en Kelly fracción / riesgo fijo
  - Apertura con TP y SL automáticos
  - Monitorización de posiciones abiertas
  - Cierre por TP, SL o tiempo máximo
"""

import asyncio
import logging
import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from bingx_client import BingXClient
from reward_scheme import PBR

log = logging.getLogger("TRADE_MGR")

STATE_FILE = Path("logs/open_trades.json")


class TradeManager:
    def __init__(self, client: BingXClient, config=None):
        self.client = client
        self.config = config  # BotConfig instance (optional for backwards compatibility)
        self.trades: dict[str, dict] = self._load_state()
        self.pbr:   dict[str, PBR]  = {}   # un PBR por símbolo abierto
        
        # Usar valores de config si está disponible, sino usar env vars
        self._leverage = config.LEVERAGE if config else int(os.getenv("LEVERAGE", "5"))
        self._risk_pct = config.RISK_PCT if config else float(os.getenv("RISK_PCT", "0.01"))
        self._tp_mult = config.TP_MULT if config else float(os.getenv("TP_MULT", "2.0"))
        self._sl_mult = config.SL_MULT if config else float(os.getenv("SL_MULT", "1.0"))
        self._max_trades = config.MAX_OPEN_TRADES if config else int(os.getenv("MAX_OPEN_TRADES", "5"))
        self._max_hold_h = config.MAX_HOLD_HOURS if config else int(os.getenv("MAX_HOLD_HOURS", "48"))
        self._min_notional = config.MIN_TRADE_USDT if config else float(os.getenv("MIN_NOTIONAL", "10"))
        self._dry_run = config.DRY_RUN if config else os.getenv("DRY_RUN", "true").lower() == "true"

    # ------------------------------------------------------------------ #
    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text())
            except Exception:
                pass
        return {}

    def _save_state(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.trades, indent=2, default=str))

    # ------------------------------------------------------------------ #
    async def open_trade(self, signal: dict) -> bool:
        sym = signal["symbol"]

        if sym in self.trades:
            log.debug(f"  Ya hay trade abierto en {sym}, saltando")
            return False

        if len(self.trades) >= self._max_trades:
            log.info(f"  Máximo de trades abiertos ({self._max_trades}) alcanzado")
            return False

        direction = signal["direction"]
        price     = signal["price"]
        mean_pnl  = signal["mean_pnl"]
        worst_pnl = signal["worst_pnl"]

        # --- Sizing ---
        balance = await self.client.get_balance()
        if balance <= 0:
            log.error("Balance 0, no se puede operar")
            return False

        risk_usdt = balance * self._risk_pct * self._leverage
        quantity  = round(risk_usdt / price, 6)
        notional  = quantity * price

        if notional < self._min_notional:
            log.info(f"  {sym}: notional {notional:.2f} USDT < mínimo {self._min_notional}, saltando")
            return False

        # --- TP / SL ---
        if direction == "LONG":
            tp_price = round(price * (1 + abs(mean_pnl) * self._tp_mult), 6)
            sl_price = round(price * (1 - abs(worst_pnl) * self._sl_mult), 6)
            side, pos_side = "BUY", "LONG"
        else:
            tp_price = round(price * (1 - abs(mean_pnl) * self._tp_mult), 6)
            sl_price = round(price * (1 + abs(worst_pnl) * self._sl_mult), 6)
            side, pos_side = "SELL", "SHORT"

        log.info(f"  📈  {'[DRY RUN] ' if self._dry_run else ''}Abriendo {direction} {sym}  qty={quantity}  "
                 f"entry={price}  TP={tp_price}  SL={sl_price}  notional={notional:.2f}$")

        # Si está en DRY_RUN, simular el trade sin ejecutarlo
        if self._dry_run:
            log.info(f"  ✅  [DRY RUN] Trade simulado (no ejecutado en exchange)")
            self.trades[sym] = {
                "symbol":     sym,
                "direction":  direction,
                "entry":      price,
                "quantity":   quantity,
                "tp":         tp_price,
                "sl":         sl_price,
                "order_id":   "DRY_RUN_" + str(int(datetime.utcnow().timestamp())),
                "opened_at":  datetime.utcnow().isoformat(),
                "signal":     {k: v for k, v in signal.items() if k != "projections"},
                "dry_run":    True,
            }
            self._save_state()
            
            # Inicializar PBR
            pbr = PBR()
            pbr.on_action(1 if direction == "LONG" else 0)
            pbr.feed_price(price)
            self.pbr[sym] = pbr
            
            return True

        try:
            # Fijar apalancamiento
            await self.client.set_leverage(sym, self._leverage, pos_side)
            await asyncio.sleep(0.3)

            resp = await self.client.place_order(
                symbol    = sym,
                side      = side,
                pos_side  = pos_side,
                quantity  = quantity,
                tp_price  = tp_price,
                sl_price  = sl_price,
            )

            if resp.get("code", -1) != 0:
                log.error(f"  ❌  Error abriendo {sym}: {resp}")
                return False

            order_id = resp.get("data", {}).get("order", {}).get("orderId", "unknown")

            self.trades[sym] = {
                "symbol":     sym,
                "direction":  direction,
                "entry":      price,
                "quantity":   quantity,
                "tp":         tp_price,
                "sl":         sl_price,
                "order_id":   order_id,
                "opened_at":  datetime.utcnow().isoformat(),
                "signal":     {k: v for k, v in signal.items() if k != "projections"},
                "dry_run":    False,
            }
            self._save_state()

            # Inicializar PBR para este símbolo
            pbr = PBR()
            pbr.on_action(1 if direction == "LONG" else 0)
            pbr.feed_price(price)
            self.pbr[sym] = pbr

            log.info(f"  ✅  Trade abierto {sym} orderId={order_id}")
            return True

        except Exception as e:
            log.error(f"  ❌  Excepción abriendo {sym}: {e}", exc_info=True)
            return False

    # ------------------------------------------------------------------ #
    async def manage_open_trades(self):
        """Revisa trades abiertos y cierra los que cumplen condición."""
        if not self.trades:
            return

        # En modo DRY_RUN, simular monitoreo sin consultar exchange
        if self._dry_run:
            to_close = []
            for sym, trade in list(self.trades.items()):
                opened_at = datetime.fromisoformat(trade["opened_at"])
                age_h     = (datetime.utcnow() - opened_at).total_seconds() / 3600
                
                # Cerrar por tiempo máximo en DRY_RUN
                if age_h >= self._max_hold_h:
                    log.info(f"  ⏰  [DRY RUN] {sym}: tiempo máximo alcanzado")
                    to_close.append(sym)
            
            for sym in to_close:
                if sym in self.pbr:
                    summary = self.pbr[sym].summary()
                    log.info(f"  🏆  [DRY RUN] {sym} PBR final: cumulative={summary['cumulative_reward']:.6f}")
                    self.pbr[sym].reset()
                    del self.pbr[sym]
                self.trades.pop(sym, None)
            
            if to_close:
                self._save_state()
            return

        # Modo REAL - consultar exchange
        positions = await self.client.get_positions()
        pos_map   = {p["symbol"]: p for p in positions if float(p.get("positionAmt", 0)) != 0}

        to_close = []

        for sym, trade in list(self.trades.items()):
            opened_at = datetime.fromisoformat(trade["opened_at"])
            age_h     = (datetime.utcnow() - opened_at).total_seconds() / 3600

            # ¿La posición ya fue cerrada por TP/SL en BingX?
            if sym not in pos_map:
                log.info(f"  🏁  {sym}: posición ya cerrada en exchange (TP/SL hit)")
                to_close.append(sym)
                continue

            pos = pos_map[sym]
            pnl = float(pos.get("unrealizedProfit", 0))

            # Actualizar PBR con precio actual
            current_price = float(pos.get("markPrice", trade["entry"]))
            if sym in self.pbr:
                self.pbr[sym].feed_price(current_price)
                reward = self.pbr[sym].get_reward()
                reward_summary = self.pbr[sym].summary()
                log.info(f"  📊  {sym}  unrealizedPnL={pnl:.4f} USDT  age={age_h:.1f}h  "
                         f"PBR reward={reward:.6f}  sharpe={reward_summary['sharpe']:.3f}")
            else:
                log.info(f"  📊  {sym}  unrealizedPnL={pnl:.4f} USDT  age={age_h:.1f}h")

            # Cierre por tiempo máximo
            if age_h >= self._max_hold_h:
                log.info(f"  ⏰  {sym}: tiempo máximo alcanzado, cerrando")
                qty = abs(float(pos.get("positionAmt", trade["quantity"])))
                await self.client.close_position(sym, trade["direction"], qty)
                to_close.append(sym)

        for sym in to_close:
            if sym in self.pbr:
                summary = self.pbr[sym].summary()
                log.info(f"  🏆  {sym} PBR final: cumulative={summary['cumulative_reward']:.6f}  "
                         f"sharpe={summary['sharpe']:.3f}  samples={summary['n_samples']}")
                self.pbr[sym].reset()
                del self.pbr[sym]
            self.trades.pop(sym, None)
        if to_close:
            self._save_state()

    # ------------------------------------------------------------------ #
    async def get_open_trades(self) -> list[dict]:
        result = []
        for sym, trade in self.trades.items():
            t = dict(trade)
            if sym in self.pbr:
                t["pbr"] = self.pbr[sym].summary()
            result.append(t)
        return result
