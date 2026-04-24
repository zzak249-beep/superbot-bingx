"""
Order Book Analyzer v7
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

            bids = [(float(b[0]), float(b[1])) for b in bids_raw if len(b) >= 2]
            asks = [(float(a[0]), float(a[1])) for a in asks_raw if len(a) >= 2]
            if not bids or not asks:
                return None

            best_bid = bids[0][0]
            best_ask = asks[0][0]
            spread   = (best_ask - best_bid) / best_bid * 100 if best_bid > 0 else 0

            bid_vol = sum(q for _, q in bids)
            ask_vol = sum(q for _, q in asks)
            imb     = bid_vol / ask_vol if ask_vol > 0 else 1.0

            bias = "BULLISH" if imb >= 1.3 else ("BEARISH" if imb <= 0.77 else "NEUTRAL")

            # Detectar muros (concentraciones > 3x promedio)
            avg_bid = bid_vol / len(bids) if bids else 0
            avg_ask = ask_vol / len(asks) if asks else 0

            bid_walls = [Wall(p, q) for p, q in bids if q > avg_bid * 3]
            ask_walls = [Wall(p, q) for p, q in asks if q > avg_ask * 3]

            absorption = (
                imb >= 1.5 and len(bid_walls) >= 1 and
                abs(bids[-1][0] - best_bid) / best_bid < 0.01
            )

            nearest_bid = bid_walls[0].price if bid_walls else 0.0
            nearest_ask = ask_walls[0].price if ask_walls else 0.0

            return OBSnapshot(
                symbol            = symbol,
                imbalance_ratio   = round(imb, 3),
                bias              = bias,
                bid_walls         = bid_walls,
                ask_walls         = ask_walls,
                bid_delta_pct     = round((bid_vol - ask_vol) / (bid_vol + ask_vol) * 100, 2),
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
        results = await asyncio.gather(*[_one(s) for s in symbols], return_exceptions=True)
        out = {}
        for r in results:
            if isinstance(r, tuple):
                sym, snap = r
                if snap:
                    out[sym] = snap
        return out
