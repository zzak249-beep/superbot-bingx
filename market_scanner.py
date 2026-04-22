"""
Market Scanner v4 — Detector de Explosiones + Sesgo Institucional
=================================================================
Mejoras sobre v3:
  - Integra order book para filtrar solo monedas con liquidez sana
  - Bonus de puntuación cuando el OB muestra acumulación institucional
  - Detección de "volumen oculto" (muchas órdenes pequeñas = iceberg)
  - Foco mejorado en small/micro caps (los +100% del día salen aquí)
  - Filtro de spread excesivo (monedas con spread > 0.5% = trampa)
"""

import logging
import os
import asyncio
import numpy as np
from typing import Optional

from bingx_client import BingXClient
from order_book import OrderBookAnalyzer

log = logging.getLogger("SCANNER")

STABLE_BLACKLIST = {"USDC","BUSD","TUSD","USDT","DAI","FDUSD","USDP","FRAX"}

TOP_EXPLOSIONS = int  (os.getenv("TOP_SYMBOLS",       "10"))
MIN_VOL_USDT   = float(os.getenv("MIN_VOLUME_USDT",   "500000"))
VOL_SPIKE_MIN  = float(os.getenv("VOL_SPIKE_MIN",     "2.0"))
MOMENTUM_MIN   = float(os.getenv("MOMENTUM_MIN",      "1.5"))
KLINE_INTERVAL = os.getenv("KLINE_INTERVAL",          "1h")
MAX_SPREAD_PCT = float(os.getenv("MAX_SPREAD_PCT",    "0.5"))    # descarta spreads > 0.5%
USE_OB_SCAN    = os.getenv("USE_OB_SCAN", "true").lower() == "true"


class MarketScanner:
    def __init__(self, client: BingXClient, ob_analyzer: Optional[OrderBookAnalyzer] = None):
        self.client = client
        self.ob     = ob_analyzer

    # ------------------------------------------------------------------ #
    async def get_hot_symbols(
        self,
        top_n:      int   = TOP_EXPLOSIONS,
        min_volume: float = MIN_VOL_USDT,
    ) -> list[str]:
        tickers = await self.client.get_tickers()

        candidates = []
        btc_change = 0.0

        for t in tickers:
            sym = t.get("symbol", "")
            if sym == "BTC-USDT":
                btc_change = abs(float(t.get("priceChangePercent", 0)))
            if not sym.endswith("-USDT"):
                continue
            base = sym.replace("-USDT", "")
            if base in STABLE_BLACKLIST:
                continue
            try:
                vol_24h      = float(t.get("quoteVolume",        0))
                price_change = float(t.get("priceChangePercent", 0))
                last_price   = float(t.get("lastPrice",          0))
                high_24h     = float(t.get("highPrice",          0))
                low_24h      = float(t.get("lowPrice",           0))
            except (ValueError, TypeError):
                continue
            if vol_24h < min_volume or last_price <= 0:
                continue
            candidates.append({
                "symbol":       sym,
                "vol_24h":      vol_24h,
                "price_change": price_change,
                "price":        last_price,
                "high_24h":     high_24h,
                "low_24h":      low_24h,
            })

        log.info(f"Scanner: {len(candidates)} pares USDT con vol > {min_volume:,.0f}")

        # Score rápido
        scored = []
        for c in candidates:
            score = self._quick_score(c, btc_change)
            if score > 0:
                c["quick_score"] = score
                scored.append(c)
        scored.sort(key=lambda x: x["quick_score"], reverse=True)

        # Distribución por caps: más peso en small caps
        large = [c for c in scored if c["vol_24h"] >= 50_000_000]
        mid   = [c for c in scored if 5_000_000 <= c["vol_24h"] < 50_000_000]
        small = [c for c in scored if 1_000_000 <= c["vol_24h"] < 5_000_000]
        micro = [c for c in scored if c["vol_24h"] < 1_000_000]

        # 10% large / 30% mid / 40% small / 20% micro
        n_large = max(1, top_n // 10)
        n_mid   = max(1, top_n * 3 // 10)
        n_small = max(1, top_n * 4 // 10)
        n_micro = top_n - n_large - n_mid - n_small

        pre_selected = (
            large[:n_large] +
            mid[:n_mid]     +
            small[:n_small] +
            micro[:n_micro]
        )

        log.info(
            f"Pre-selección: {len(large[:n_large])} large + "
            f"{len(mid[:n_mid])} mid + {len(small[:n_small])} small + "
            f"{len(micro[:n_micro])} micro"
        )

        # Score profundo con klines + order book
        tasks  = [self._deep_score(c["symbol"], c) for c in pre_selected]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        final = []
        for r in results:
            if isinstance(r, dict) and r is not None:
                final.append(r)

        final.sort(key=lambda x: x["final_score"], reverse=True)

        log.info("🔥  TOP EXPLOSIONES:")
        for i, c in enumerate(final[:top_n], 1):
            cap = "L" if c["vol_24h"] >= 50e6 else "M" if c["vol_24h"] >= 5e6 else "S" if c["vol_24h"] >= 1e6 else "µ"
            ob_str = f"  OB={c.get('ob_bias','?')}({c.get('ob_imb',0):.2f})" if c.get("ob_bias") else ""
            log.info(
                f"  #{i:2d}  {c['symbol']:20s}  score={c['final_score']:.1f}  "
                f"vol_spike={c.get('vol_spike',0):.1f}x  "
                f"chg24h={c['price_change']:+.1f}%  cap={cap}{ob_str}"
            )

        return [c["symbol"] for c in final[:top_n]]

    # ------------------------------------------------------------------ #
    def _quick_score(self, c: dict, btc_change: float) -> float:
        score  = 0.0
        change = abs(c["price_change"])
        if change < MOMENTUM_MIN:
            return 0.0

        score += min(change * 2, 40)

        vol = c["vol_24h"]
        if   vol >= 100_000_000: score += 5
        elif vol >= 50_000_000:  score += 10
        elif vol >= 10_000_000:  score += 15
        elif vol >= 5_000_000:   score += 20
        elif vol >= 1_000_000:   score += 25
        else:                    score += 30   # micro caps = máximo potencial

        if c["price"] > 0 and c["high_24h"] > 0:
            daily_range = (c["high_24h"] - c["low_24h"]) / c["price"]
            score += min(daily_range * 100, 20)

        if change > btc_change + 3:
            score += 10

        return score

    # ------------------------------------------------------------------ #
    async def _deep_score(self, symbol: str, base: dict) -> Optional[dict]:
        try:
            klines = await self.client.get_klines(symbol, KLINE_INTERVAL, limit=30)
            if len(klines) < 10:
                return None

            volumes = np.array([float(k["volume"]) for k in klines])
            closes  = np.array([float(k["close"])  for k in klines])
            highs   = np.array([float(k["high"])   for k in klines])
            lows    = np.array([float(k["low"])    for k in klines])

            vol_mean     = volumes[:-1].mean() if len(volumes) > 1 else 1
            vol_spike    = volumes[-1] / vol_mean if vol_mean > 0 else 1

            vol_accel = 0
            for i in range(len(volumes) - 1, 0, -1):
                if volumes[i] > volumes[i - 1]:
                    vol_accel += 1
                else:
                    break

            ranges       = highs - lows
            range_mean   = ranges[:-1].mean() if len(ranges) > 1 else 1
            vol_breakout = ranges[-1] / range_mean if range_mean > 0 else 1
            recent_high  = highs[:-1].max() if len(highs) > 1 else closes[-1]
            price_brkout = closes[-1] > recent_high * 0.99

            final_score = base["quick_score"]

            if   vol_spike >= 10: final_score += 35
            elif vol_spike >= 5:  final_score += 25
            elif vol_spike >= 3:  final_score += 15
            elif vol_spike >= VOL_SPIKE_MIN:
                                  final_score += 8
            else:                 final_score -= 20

            final_score += min(vol_accel * 5, 20)
            if vol_breakout >= 2:   final_score += 10
            elif vol_breakout >= 1.5: final_score += 5
            if price_brkout:        final_score += 10

            # ---- Order book quality check --------------------------------
            ob_bias    = None
            ob_imb     = 1.0
            spread_pct = 0.0
            if USE_OB_SCAN and self.ob:
                snap = await self.ob.analyze(symbol)
                if snap:
                    ob_bias    = snap.bias
                    ob_imb     = snap.imbalance_ratio
                    spread_pct = snap.spread_pct

                    # Penalizar spreads altos (ilíquido o manipulado)
                    if spread_pct > MAX_SPREAD_PCT:
                        log.debug(f"  {symbol}: spread {spread_pct:.3f}% > máx, penalizando")
                        final_score -= 30

                    # Bonus si el OB muestra acumulación
                    if ob_bias == "BULLISH" and abs(base["price_change"]) == base["price_change"]:
                        final_score += 15   # precio subiendo + libro alcista
                    elif ob_bias == "BEARISH" and base["price_change"] < 0:
                        final_score += 10   # precio bajando + libro bajista (short candidato)

                    # Muros de bid grandes cerca = soporte institucional → mejor para LONG
                    if snap.nearest_bid_wall and snap.nearest_bid_wall.strength >= 5:
                        final_score += 8

            result = dict(base)
            result.update({
                "vol_spike":    round(vol_spike,    2),
                "vol_accel":    vol_accel,
                "vol_breakout": round(vol_breakout, 2),
                "price_breakout": price_brkout,
                "final_score":  round(final_score,  1),
                "ob_bias":      ob_bias,
                "ob_imb":       round(ob_imb, 3),
                "spread_pct":   round(spread_pct, 4),
            })
            return result

        except Exception as e:
            log.warning(f"Deep score error {symbol}: {e}")
            return None
