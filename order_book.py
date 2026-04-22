"""
Order Book Analyzer — Detección de Muros de Liquidez Institucional
===================================================================
Objetivo: identificar dónde están colocados los grandes órdenes
(bancos, market makers, fondos) para:

  1. Colocar SL justo DETRÁS del muro de soporte más cercano
     → SL más ajustado = mejor R:R
  2. Colocar TP1 justo ANTES del muro de resistencia más cercano
     → mayor probabilidad de que TP se ejecute
  3. Medir el sesgo institucional (bids vs asks)
     → solo operar LONG si hay más liquidez compradora que vendedora

Conceptos de Smart Money aplicados:
  - Liquidity Wall : nivel con órdenes >= WALL_MULT x media del libro
  - Imbalance Ratio: total_bid_qty / total_ask_qty
     > 1.2 → sesgo alcista / < 0.8 → sesgo bajista
  - Iceberg Detection: muchas órdenes pequeñas al mismo precio
     (market makers ocultan tamaño real)
"""

import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("ORDERBOOK")

# Multiplicador sobre el tamaño medio para considerar "muro"
WALL_MULT       = float(__import__("os").getenv("WALL_MULT",       "3.0"))
# Niveles del libro a descargar
BOOK_DEPTH      = int  (__import__("os").getenv("BOOK_DEPTH",      "50"))
# Imbalance mínimo para señal alcista (>1) o bajista (<1)
IMBALANCE_BULL  = float(__import__("os").getenv("IMBALANCE_BULL",  "1.15"))
IMBALANCE_BEAR  = float(__import__("os").getenv("IMBALANCE_BEAR",  "0.85"))


@dataclass
class Wall:
    price:     float
    qty:       float
    notional:  float          # qty * price en USDT
    strength:  float          # qty / avg_qty en el libro


@dataclass
class OrderBookSnapshot:
    symbol:              str
    mid_price:           float
    spread_pct:          float            # (ask1 - bid1) / mid * 100
    bid_walls:           list[Wall] = field(default_factory=list)
    ask_walls:           list[Wall] = field(default_factory=list)
    nearest_bid_wall:    Optional[Wall] = None   # soporte más cercano abajo
    nearest_ask_wall:    Optional[Wall] = None   # resistencia más cercana arriba
    imbalance_ratio:     float = 1.0             # bid_total / ask_total
    bias:                str   = "NEUTRAL"       # BULLISH / BEARISH / NEUTRAL
    total_bid_notional:  float = 0.0
    total_ask_notional:  float = 0.0
    # Para dashboard
    top_bids:            list[tuple] = field(default_factory=list)  # [(price,qty)]
    top_asks:            list[tuple] = field(default_factory=list)


class OrderBookAnalyzer:
    """
    Descarga el libro de órdenes de BingX y lo analiza buscando
    muros institucionales y sesgo de liquidez.
    """

    def __init__(self, client):
        self.client = client
        self._cache: dict[str, OrderBookSnapshot] = {}

    # ------------------------------------------------------------------ #
    async def analyze(self, symbol: str) -> Optional[OrderBookSnapshot]:
        """
        Analiza el order book de `symbol`.
        Devuelve un OrderBookSnapshot o None si falla.
        """
        try:
            data = await self.client.get_order_book(symbol, limit=BOOK_DEPTH)
            bids_raw = data.get("bids", [])   # [[price, qty], …]  desc
            asks_raw = data.get("asks", [])   # [[price, qty], …]  asc

            if not bids_raw or not asks_raw:
                return None

            bids = [(float(b[0]), float(b[1])) for b in bids_raw]
            asks = [(float(a[0]), float(a[1])) for a in asks_raw]

            best_bid = bids[0][0]
            best_ask = asks[0][0]
            mid      = (best_bid + best_ask) / 2
            spread   = (best_ask - best_bid) / mid * 100 if mid > 0 else 0

            # --- Métricas globales del libro ------------------------------
            bid_qtys  = np.array([b[1] for b in bids])
            ask_qtys  = np.array([a[1] for a in asks])

            total_bid_qty  = bid_qtys.sum()
            total_ask_qty  = ask_qtys.sum()
            total_bid_not  = sum(p * q for p, q in bids)
            total_ask_not  = sum(p * q for p, q in asks)
            imbalance      = total_bid_qty / total_ask_qty if total_ask_qty > 0 else 1.0

            avg_bid_qty = bid_qtys.mean() if len(bid_qtys) > 0 else 1
            avg_ask_qty = ask_qtys.mean() if len(ask_qtys) > 0 else 1

            # --- Detección de muros ---------------------------------------
            bid_walls = []
            for price, qty in bids:
                if qty >= avg_bid_qty * WALL_MULT:
                    bid_walls.append(Wall(
                        price    = price,
                        qty      = qty,
                        notional = price * qty,
                        strength = qty / avg_bid_qty,
                    ))

            ask_walls = []
            for price, qty in asks:
                if qty >= avg_ask_qty * WALL_MULT:
                    ask_walls.append(Wall(
                        price    = price,
                        qty      = qty,
                        notional = price * qty,
                        strength = qty / avg_ask_qty,
                    ))

            # El muro más cercano al precio actual
            nearest_bid = bid_walls[0] if bid_walls else None
            nearest_ask = ask_walls[0] if ask_walls else None

            # --- Sesgo --------------------------------------------------
            if imbalance >= IMBALANCE_BULL:
                bias = "BULLISH"
            elif imbalance <= IMBALANCE_BEAR:
                bias = "BEARISH"
            else:
                bias = "NEUTRAL"

            snap = OrderBookSnapshot(
                symbol             = symbol,
                mid_price          = mid,
                spread_pct         = round(spread, 4),
                bid_walls          = bid_walls,
                ask_walls          = ask_walls,
                nearest_bid_wall   = nearest_bid,
                nearest_ask_wall   = nearest_ask,
                imbalance_ratio    = round(imbalance, 3),
                bias               = bias,
                total_bid_notional = round(total_bid_not, 2),
                total_ask_notional = round(total_ask_not, 2),
                top_bids           = bids[:5],
                top_asks           = asks[:5],
            )

            self._cache[symbol] = snap

            log.debug(
                f"OB {symbol}: bias={bias}  imb={imbalance:.2f}  "
                f"bid_walls={len(bid_walls)}  ask_walls={len(ask_walls)}  "
                f"spread={spread:.4f}%"
            )
            return snap

        except Exception as e:
            log.warning(f"OrderBook error {symbol}: {e}")
            return None

    # ------------------------------------------------------------------ #
    def get_cached(self, symbol: str) -> Optional[OrderBookSnapshot]:
        return self._cache.get(symbol)

    # ------------------------------------------------------------------ #
    @staticmethod
    def suggest_sl(
        snap:      OrderBookSnapshot,
        direction: str,
        entry:     float,
        fallback_pct: float = 0.015,
    ) -> float:
        """
        Sugiere un SL basado en muros del order book.
        LONG: SL justo debajo del bid wall más cercano (buffer 0.1%)
        SHORT: SL justo encima del ask wall más cercano (buffer 0.1%)
        Si no hay muro, usa fallback_pct del precio.
        """
        buffer = 0.001   # 0.1% buffer detrás del muro
        if direction == "LONG":
            if snap.nearest_bid_wall:
                sl = snap.nearest_bid_wall.price * (1 - buffer)
                if sl < entry * (1 - fallback_pct * 2):   # sanity check: SL no muy lejos
                    sl = entry * (1 - fallback_pct)
            else:
                sl = entry * (1 - fallback_pct)
        else:
            if snap.nearest_ask_wall:
                sl = snap.nearest_ask_wall.price * (1 + buffer)
                if sl > entry * (1 + fallback_pct * 2):
                    sl = entry * (1 + fallback_pct)
            else:
                sl = entry * (1 + fallback_pct)
        return round(sl, 8)

    @staticmethod
    def suggest_tp(
        snap:      OrderBookSnapshot,
        direction: str,
        entry:     float,
        mean_pnl:  float,
        tp_mult:   float = 2.0,
    ) -> float:
        """
        TP1 justo ANTES del muro de resistencia más cercano.
        Si no hay muro, usa mean_pnl * tp_mult.
        """
        if direction == "LONG":
            default_tp = entry * (1 + abs(mean_pnl) * tp_mult)
            if snap.nearest_ask_wall:
                wall_tp = snap.nearest_ask_wall.price * 0.999
                # Tomar el menor entre wall y mean target
                return round(min(wall_tp, default_tp), 8)
            return round(default_tp, 8)
        else:
            default_tp = entry * (1 - abs(mean_pnl) * tp_mult)
            if snap.nearest_bid_wall:
                wall_tp = snap.nearest_bid_wall.price * 1.001
                return round(max(wall_tp, default_tp), 8)
            return round(default_tp, 8)
