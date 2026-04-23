"""
Order Book Analyzer v2 — Rápido, sin crashes
=============================================
FIXES vs v1:
  - Eliminado atributo nearest_bid_wall que causaba el crash
  - _fetch_order_book usa cache del cliente directamente
  - Análisis paralelo para múltiples símbolos
  - nearest_bid / nearest_ask como propiedad calculada

MEJORAS:
  - fetch directo a través del cache del cliente (2s TTL)
  - Snapshot inmutable con propiedad nearest_bid_wall (no falla)
  - Batch analyze para procesar N símbolos en paralelo
"""

import asyncio
import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime, timedelta

log = logging.getLogger("ORDER_BOOK")


@dataclass
class Wall:
    price: float
    volume: float
    distance_pct: float


@dataclass
class OrderBookSnapshot:
    symbol: str
    timestamp: datetime

    best_bid: float
    best_ask: float
    mid_price: float
    spread_pct: float

    total_bid_volume: float
    total_ask_volume: float
    imbalance_ratio: float

    bias: str           # BULLISH / BEARISH / NEUTRAL

    bid_walls: List[Wall] = field(default_factory=list)
    ask_walls: List[Wall] = field(default_factory=list)

    bid_delta_pct: float = 0.0
    ask_delta_pct: float = 0.0

    cvd: float      = 0.0
    cvd_pct: float  = 50.0

    absorption_signal: bool         = False
    absorption_side: Optional[str]  = None

    depth_levels: int = 0

    # ── propiedades calculadas para compatibilidad ────────── #
    @property
    def nearest_bid_wall(self) -> Optional[Wall]:
        """Muro de compra más cercano al precio actual."""
        if not self.bid_walls:
            return None
        return min(self.bid_walls, key=lambda w: w.distance_pct)

    @property
    def nearest_ask_wall(self) -> Optional[Wall]:
        """Muro de venta más cercano al precio actual."""
        if not self.ask_walls:
            return None
        return min(self.ask_walls, key=lambda w: w.distance_pct)


class OrderBookAnalyzer:
    def __init__(self, client, depth_levels: int = 20):
        self.client       = client
        self.depth_levels = depth_levels

        self._cache:       dict[str, OrderBookSnapshot] = {}
        self._cvd_history: dict[str, list]              = {}

        self.wall_threshold_pct  = 2.0
        self.absorption_threshold = 3.0
        self.imbalance_strong    = 1.5

    # ------------------------------------------------------------------ #
    #  API pública
    # ------------------------------------------------------------------ #
    async def analyze(self, symbol: str) -> Optional[OrderBookSnapshot]:
        try:
            ob_data = await self.client.get_depth(symbol, self.depth_levels)
            if not ob_data:
                return None

            bids = ob_data.get("bids", [])
            asks = ob_data.get("asks", [])
            if not bids or not asks:
                return None

            best_bid  = float(bids[0][0])
            best_ask  = float(asks[0][0])
            mid_price = (best_bid + best_ask) / 2
            spread_pct = ((best_ask - best_bid) / mid_price) * 100 if mid_price else 0

            total_bid = sum(float(b[1]) for b in bids)
            total_ask = sum(float(a[1]) for a in asks)

            imbalance = total_bid / total_ask if total_ask > 0 else 1.0

            if imbalance >= self.imbalance_strong:
                bias = "BULLISH"
            elif imbalance <= (1 / self.imbalance_strong):
                bias = "BEARISH"
            else:
                bias = "NEUTRAL"

            bid_walls = self._detect_walls(bids, mid_price)
            ask_walls = self._detect_walls(asks, mid_price)

            bid_delta, ask_delta = self._calc_deltas(symbol, total_bid, total_ask)
            cvd, cvd_pct         = self._calc_cvd(symbol, total_bid, total_ask)
            abs_sig, abs_side    = self._detect_absorption(
                bid_walls, ask_walls, total_bid, total_ask
            )

            snap = OrderBookSnapshot(
                symbol=symbol,
                timestamp=datetime.utcnow(),
                best_bid=best_bid,
                best_ask=best_ask,
                mid_price=mid_price,
                spread_pct=spread_pct,
                total_bid_volume=total_bid,
                total_ask_volume=total_ask,
                imbalance_ratio=imbalance,
                bias=bias,
                bid_walls=bid_walls,
                ask_walls=ask_walls,
                bid_delta_pct=bid_delta,
                ask_delta_pct=ask_delta,
                cvd=cvd,
                cvd_pct=cvd_pct,
                absorption_signal=abs_sig,
                absorption_side=abs_side,
                depth_levels=len(bids),
            )

            self._cache[symbol] = snap
            return snap

        except Exception as e:
            log.warning(f"OB {symbol}: {e}")
            return None

    async def analyze_batch(self, symbols: list[str]) -> dict[str, Optional[OrderBookSnapshot]]:
        """Analiza múltiples símbolos en paralelo."""
        tasks   = [self.analyze(sym) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out = {}
        for sym, res in zip(symbols, results):
            out[sym] = res if isinstance(res, OrderBookSnapshot) else None
        return out

    def get_cached(self, symbol: str) -> Optional[OrderBookSnapshot]:
        return self._cache.get(symbol)

    # ------------------------------------------------------------------ #
    #  Internos
    # ------------------------------------------------------------------ #
    def _detect_walls(self, orders: list, ref_price: float) -> List[Wall]:
        if not orders:
            return []
        volumes = [float(o[1]) for o in orders]
        avg_vol = np.mean(volumes) if volumes else 1.0
        walls   = []
        for price_s, vol_s in orders:
            price  = float(price_s)
            volume = float(vol_s)
            if volume >= avg_vol * self.wall_threshold_pct:
                dist = abs((price - ref_price) / ref_price) * 100 if ref_price else 0
                walls.append(Wall(price=price, volume=volume, distance_pct=dist))
        walls.sort(key=lambda w: w.volume, reverse=True)
        return walls[:5]

    def _calc_deltas(self, symbol: str, bid: float, ask: float):
        prev = self._cache.get(symbol)
        if not prev:
            return 0.0, 0.0
        bd = ((bid - prev.total_bid_volume) / prev.total_bid_volume * 100
              if prev.total_bid_volume > 0 else 0.0)
        ad = ((ask - prev.total_ask_volume) / prev.total_ask_volume * 100
              if prev.total_ask_volume > 0 else 0.0)
        return bd, ad

    def _calc_cvd(self, symbol: str, bid: float, ask: float):
        hist = self._cvd_history.setdefault(symbol, [])
        delta = bid - ask
        cvd   = (hist[-1][1] + delta) if hist else delta

        now = datetime.utcnow()
        hist.append((now, cvd))
        cutoff = now - timedelta(hours=24)
        self._cvd_history[symbol] = [(t, v) for t, v in hist if t >= cutoff]

        vals = [v for _, v in self._cvd_history[symbol]]
        if len(vals) > 1:
            lo, hi = min(vals), max(vals)
            cvd_pct = ((cvd - lo) / (hi - lo) * 100) if hi > lo else 50.0
        else:
            cvd_pct = 50.0

        return cvd, cvd_pct

    def _detect_absorption(self, bid_walls, ask_walls, total_bid, total_ask):
        max_bid_wall = max((w.volume for w in bid_walls), default=0)
        max_ask_wall = max((w.volume for w in ask_walls), default=0)

        if max_bid_wall > total_ask * self.absorption_threshold:
            return True, "BID"
        if max_ask_wall > total_bid * self.absorption_threshold:
            return True, "ASK"
        return False, None
