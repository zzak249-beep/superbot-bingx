"""
Order Book Analyzer v8
FIXES vs v7:
  - Maneja bids/asks como lista de listas o lista de dicts
  - Wall detection: umbral dinámico (no fijo 3x)
  - absorption_signal: lógica más robusta
"""
import asyncio, logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("OB")


@dataclass
class Wall:
    price: float
    qty:   float


@dataclass
class OBSnapshot:
    symbol:            str
    imbalance_ratio:   float = 1.0
    bias:              str   = "NEUTRAL"
    bid_walls:         list  = field(default_factory=list)
    ask_walls:         list  = field(default_factory=list)
    bid_delta_pct:     float = 0.0
    ask_delta_pct:     float = 0.0
    absorption_signal: bool  = False
    spread_pct:        float = 0.0
    nearest_bid_wall:  float = 0.0
    nearest_ask_wall:  float = 0.0


class OrderBookAnalyzer:
    def __init__(self, client):
        self.client = client

    async def analyze(self, symbol: str) -> Optional[OBSnapshot]:
        try:
            ob = await self.client.get_orderbook(symbol, depth=20)
            if not ob:
                return None

            bids_raw = ob.get("bids", [])
            asks_raw = ob.get("asks", [])
            if not bids_raw or not asks_raw:
                return None

            def parse_level(level):
                """Parsea [price, qty] o {"price": x, "qty": y}."""
                if isinstance(level, (list, tuple)) and len(level) >= 2:
                    return float(level[0]), float(level[1])
                if isinstance(level, dict):
                    p = float(level.get("price", 0) or level.get("p", 0))
                    q = float(level.get("qty",   0) or level.get("q", 0)
                              or level.get("quantity", 0))
                    return p, q
                return None

            bids, asks = [], []
            for b in bids_raw:
                parsed = parse_level(b)
                if parsed and parsed[0] > 0 and parsed[1] > 0:
                    bids.append(parsed)
            for a in asks_raw:
                parsed = parse_level(a)
                if parsed and parsed[0] > 0 and parsed[1] > 0:
                    asks.append(parsed)

            if not bids or not asks:
                return None

            best_bid = bids[0][0]
            best_ask = asks[0][0]
            spread   = (best_ask - best_bid) / best_bid * 100 if best_bid > 0 else 0

            bid_vol = sum(q for _, q in bids)
            ask_vol = sum(q for _, q in asks)
            imb     = bid_vol / ask_vol if ask_vol > 0 else 1.0

            bias = ("BULLISH" if imb >= 1.3
                    else "BEARISH" if imb <= 0.77
                    else "NEUTRAL")

            # Muros: concentraciones > 2.5x promedio (FIX: bajado de 3x)
            avg_bid = bid_vol / len(bids) if bids else 0
            avg_ask = ask_vol / len(asks) if asks else 0
            thresh  = 2.5

            bid_walls = [Wall(p, q) for p, q in bids if q > avg_bid * thresh]
            ask_walls = [Wall(p, q) for p, q in asks if q > avg_ask * thresh]

            # Absorption: gran muro bid Y precio cerca de él
            absorption = False
            if bid_walls and imb >= 1.3:
                nearest_wall = bid_walls[0].price
                if abs(best_bid - nearest_wall) / best_bid < 0.015:
                    absorption = True

            nearest_bid = bid_walls[0].price if bid_walls else 0.0
            nearest_ask = ask_walls[0].price if ask_walls else 0.0

            delta_pct = ((bid_vol - ask_vol) / (bid_vol + ask_vol) * 100
                         if (bid_vol + ask_vol) > 0 else 0)

            return OBSnapshot(
                symbol            = symbol,
                imbalance_ratio   = round(imb, 3),
                bias              = bias,
                bid_walls         = bid_walls,
                ask_walls         = ask_walls,
                bid_delta_pct     = round(delta_pct, 2),
                ask_delta_pct     = 0.0,
                absorption_signal = absorption,
                spread_pct        = round(spread, 4),
                nearest_bid_wall  = nearest_bid,
                nearest_ask_wall  = nearest_ask,
            )
        except Exception as e:
            log.debug(f"OB {symbol}: {e}")
            return None

    async def analyze_batch(self, symbols: list) -> dict:
        sem = asyncio.Semaphore(10)

        async def _one(sym):
            async with sem:
                return sym, await self.analyze(sym)

        results = await asyncio.gather(
            *[_one(s) for s in symbols], return_exceptions=True
        )
        out = {}
        for r in results:
            if isinstance(r, tuple):
                sym, snap = r
                if snap:
                    out[sym] = snap
        return out
