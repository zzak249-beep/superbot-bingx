"""
Live Trade Manager — Conflux 4 v2
Gestiona trades activos en tiempo real:
  - Mover stop a breakeven cuando se alcanza TP1
  - Salida parcial en cada TP (25% en TP1, 25% TP2, 25% TP3, 25% TP4)
  - Trailing stop dinámico basado en Supertrend
  - Cancela posición si el trend se invierte completamente
"""

import json
import numpy as np  # ← CORRECCIÓN: import al inicio, no al final
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Optional
from loguru import logger


@dataclass
class ActiveTrade:
    symbol: str
    direction: str              # 'BULL' | 'BEAR'
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    tp4: float
    quantity: float             # cantidad total
    quantity_remaining: float   # cantidad pendiente
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    tp4_hit: bool = False
    be_moved: bool = False      # stop movido a breakeven
    partial_usdt_locked: float = 0.0   # beneficio ya asegurado


class TradeManager:
    def __init__(self, data_path: str = "data/trades.json"):
        self.data_path = Path(data_path)
        self.trades: Dict[str, ActiveTrade] = self._load()

    def _load(self) -> Dict[str, ActiveTrade]:
        if self.data_path.exists():
            try:
                with open(self.data_path) as f:
                    raw = json.load(f)
                return {k: ActiveTrade(**v) for k, v in raw.items()}
            except Exception:
                pass
        return {}

    def save(self):
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.data_path, "w") as f:
            json.dump({k: asdict(v) for k, v in self.trades.items()}, f, indent=2)

    # ── Registrar nuevo trade ──────────────────────────────────────────────
    def open_trade(self, trade: ActiveTrade):
        self.trades[trade.symbol] = trade
        self.save()
        logger.info(f"Trade abierto: {trade.symbol} {trade.direction} @ {trade.entry}")

    # ── Actualizar con precio actual y Supertrend ─────────────────────────
    def update(self, symbol: str, price: float, st_val: float, st_bull: bool) -> dict:
        """
        Devuelve dict con acciones a tomar:
          - close_full: cerrar toda la posición
          - partial_close: {qty: float, reason: str}
          - update_stop: nuevo nivel de stop
          - events: lista de strings para logging/Telegram
        """
        if symbol not in self.trades:
            return {}

        t = self.trades[symbol]
        actions = {"close_full": False, "partial_close": None,
                   "update_stop": None, "events": []}

        is_long = t.direction == "BULL"

        # ── Chequeo de stop hit ───────────────────────────────────────────
        stop_hit = (is_long and price <= t.stop) or (not is_long and price >= t.stop)
        if stop_hit:
            actions["close_full"] = True
            actions["events"].append(f"🛑 STOP HIT @ {price:.4f}")
            self._close(symbol)
            return actions

        # ── Cancelación por inversión de trend (Supertrend flip) ─────────
        trend_flip = (is_long and not st_bull) or (not is_long and st_bull)
        if trend_flip and not t.tp1_hit:
            actions["close_full"] = True
            actions["events"].append(f"⚡ Trend flip — salida sin TP @ {price:.4f}")
            self._close(symbol)
            return actions

        # ── Hits de TP y salidas parciales (escalonado 25/25/25/25) ───────
        partial_qty = 0.0
        partial_reason = ""

        if not t.tp1_hit and ((is_long and price >= t.tp1) or (not is_long and price <= t.tp1)):
            t.tp1_hit = True
            partial_qty = t.quantity * 0.25
            partial_reason = f"TP1 @ {t.tp1:.4f}"
            if not t.be_moved:
                t.stop = t.entry
                t.be_moved = True
                actions["update_stop"] = t.entry
                actions["events"].append(f"🔒 Stop movido a BE @ {t.entry:.4f}")
            actions["events"].append(f"🎯 {partial_reason} — salida 25%")

        elif not t.tp2_hit and ((is_long and price >= t.tp2) or (not is_long and price <= t.tp2)):
            t.tp2_hit = True
            partial_qty = t.quantity * 0.25
            partial_reason = f"TP2 @ {t.tp2:.4f}"
            actions["events"].append(f"🎯 {partial_reason} — salida 25%")

        elif not t.tp3_hit and ((is_long and price >= t.tp3) or (not is_long and price <= t.tp3)):
            t.tp3_hit = True
            partial_qty = t.quantity * 0.25
            partial_reason = f"TP3 @ {t.tp3:.4f}"
            actions["events"].append(f"🎯 {partial_reason} — salida 25%")

        elif not t.tp4_hit and ((is_long and price >= t.tp4) or (not is_long and price <= t.tp4)):
            t.tp4_hit = True
            partial_qty = t.quantity_remaining
            partial_reason = f"TP4 @ {t.tp4:.4f}"
            actions["events"].append(f"🏆 {partial_reason} — salida 100% restante")
            actions["close_full"] = True
            self._close(symbol)
            self.save()
            return actions

        if partial_qty > 0:
            t.quantity_remaining = max(0, t.quantity_remaining - partial_qty)
            actions["partial_close"] = {"qty": partial_qty, "reason": partial_reason}

        # ── Trailing stop con Supertrend (solo después de TP1) ────────────
        # CORRECCIÓN: numpy ya está importado al inicio, condición simplificada
        if t.tp1_hit and not np.isnan(float(st_val)):
            if is_long and st_bull and st_val > t.stop:
                t.stop = st_val
                actions["update_stop"] = st_val
            elif not is_long and not st_bull and st_val < t.stop:
                t.stop = st_val
                actions["update_stop"] = st_val

        self.save()
        return actions

    def _close(self, symbol: str):
        self.trades.pop(symbol, None)
        self.save()

    def get_trade(self, symbol: str) -> Optional[ActiveTrade]:
        return self.trades.get(symbol)

    def all_trades(self) -> Dict[str, ActiveTrade]:
        return self.trades
