"""
Trade Manager — Conflux 4 Bot
Gestiona posiciones abiertas: TP1 parcial, trailing stop, cierre total
"""
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional
from loguru import logger


@dataclass
class ActiveTrade:
    symbol:             str
    direction:          str    # BULL | BEAR
    entry:              float
    stop:               float
    tp1:                float
    tp2:                float
    tp3:                float
    tp4:                float
    quantity:           float
    quantity_remaining: float
    leverage:           int   = 5
    position_usdt:      float = 0.0
    quality:            int   = 0
    tp1_done:           bool  = False
    be_done:            bool  = False


class TradeManager:
    def __init__(self, data_path: str = "data/trades.json"):
        self._path   = Path(data_path)
        self._trades: Dict[str, ActiveTrade] = {}
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                with open(self._path) as f:
                    raw = json.load(f)
                for sym, d in raw.items():
                    self._trades[sym] = ActiveTrade(**{
                        k: v for k, v in d.items()
                        if k in ActiveTrade.__dataclass_fields__
                    })
                logger.info(f"Trades cargados: {len(self._trades)}")
            except Exception as e:
                logger.warning(f"No se pudieron cargar trades: {e}")

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump({s: asdict(t) for s, t in self._trades.items()}, f, indent=2)

    def open_trade(self, trade: ActiveTrade):
        self._trades[trade.symbol] = trade
        self._save()
        logger.info(f"Trade registrado: {trade.symbol} {trade.direction} @ {trade.entry:.4f}")

    def remove(self, symbol: str):
        self._trades.pop(symbol, None)
        self._save()

    def all_trades(self) -> Dict[str, ActiveTrade]:
        return dict(self._trades)

    def update(self, symbol: str, current_price: float,
               st_val: float, st_bull: bool) -> dict:
        """
        Evalúa la posición y devuelve acciones a ejecutar.
        Returns dict con keys: events, close_full, partial_close, update_stop
        """
        trade = self._trades.get(symbol)
        if not trade:
            return {}

        actions  = {"events": [], "close_full": False,
                    "partial_close": None, "update_stop": None}
        price    = current_price
        is_bull  = trade.direction == "BULL"

        # ── Check TP1 (cierre parcial 50%) ────────────────────────────────
        if not trade.tp1_done:
            hit_tp1 = (price >= trade.tp1) if is_bull else (price <= trade.tp1)
            if hit_tp1:
                qty_close = round(trade.quantity * 0.5, 6)
                trade.quantity_remaining -= qty_close
                trade.tp1_done = True
                actions["events"].append("TP1 parcial 50%")
                actions["partial_close"] = {"qty": qty_close}
                logger.info(f"TP1 alcanzado: {symbol} cierre parcial {qty_close}")

        # ── Breakeven tras TP1 ────────────────────────────────────────────
        if trade.tp1_done and not trade.be_done:
            trade.stop    = trade.entry
            trade.be_done = True
            actions["events"].append("SL → Breakeven")
            actions["update_stop"] = trade.entry
            logger.info(f"Breakeven: {symbol} SL → {trade.entry:.4f}")

        # ── Trailing stop: Supertrend flip ────────────────────────────────
        if trade.be_done:
            if is_bull and not st_bull:
                actions["events"].append("Trailing SELL (Supertrend flip)")
                actions["close_full"] = True
                logger.info(f"Trail BULL→BEAR: {symbol} @ {price:.4f}")
            elif not is_bull and st_bull:
                actions["events"].append("Trailing BUY (Supertrend flip)")
                actions["close_full"] = True
                logger.info(f"Trail BEAR→BULL: {symbol} @ {price:.4f}")

        # ── TP2 definitivo ────────────────────────────────────────────────
        if not actions["close_full"]:
            hit_tp2 = (price >= trade.tp2) if is_bull else (price <= trade.tp2)
            if hit_tp2:
                actions["events"].append("TP2 alcanzado")
                actions["close_full"] = True
                logger.info(f"TP2 alcanzado: {symbol} @ {price:.4f}")

        # Si se cierra, eliminar del registro
        if actions["close_full"]:
            self.remove(symbol)
        elif actions["events"]:
            self._save()

        return actions
