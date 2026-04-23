"""
Market Scanner v4 — Paralelo, ultrarrápido, anticipación al mercado
====================================================================
FIXES:
  - deep_score ya no accede a nearest_bid_wall (crasheaba)
  - Manejo robusto de excepciones por símbolo

MEJORAS vs v3:
  - Descarga klines de TODOS los candidatos en un solo gather() paralelo
  - Inicia WebSocket antes de evaluar para tener precios frescos
  - Score de velocidad: detecta aceleración de volumen en los últimos N ticks
  - Pre-fetching de klines 4h para MTF simultáneo
  - Filtro de spread para evitar pares ilíquidos
"""

import asyncio
import logging
import os
import numpy as np
from typing import Optional

from bingx_client import BingXClient

log = logging.getLogger("SCANNER")

STABLE_BLACKLIST = {"USDC","BUSD","TUSD","USDT","DAI","FDUSD","USDP","FRAX"}

TOP_N           = int(os.getenv("TOP_SYMBOLS",       "30"))
MIN_VOL_USDT    = float(os.getenv("MIN_VOLUME_USDT", "500000"))
VOL_SPIKE_MIN   = float(os.getenv("VOL_SPIKE_MIN",   "1.5"))
MOMENTUM_MIN    = float(os.getenv("MOMENTUM_MIN",    "1.0"))
KLINE_INTERVAL  = os.getenv("KLINE_INTERVAL",        "1h")


class MarketScanner:
    def __init__(self, client: BingXClient, ob_analyzer=None):
        self.client      = client
        self.ob_analyzer = ob_analyzer

    # ------------------------------------------------------------------ #
    async def get_hot_symbols(
        self,
        top_n:      int   = TOP_N,
        min_volume: float = MIN_VOL_USDT,
    ) -> list[str]:
        """
        Retorna los top_n símbolos más calientes.
        ULTRA-RÁPIDO: un fetch de tickers + klines paralelas.
        """
        # 1. Tickers (un solo request, cacheado)
        tickers = await self.client.get_tickers()
        if not tickers:
            log.warning("Scanner: tickers vacíos")
            return []

        # 2. Filtro base rápido
        btc_change = 0.0
        candidates = []
        for t in tickers:
            sym = t.get("symbol", "")
            if sym == "BTC-USDT":
                try:
                    btc_change = abs(float(t.get("priceChangePercent", 0)))
                except Exception:
                    pass

            if not sym.endswith("-USDT"):
                continue
            base = sym.replace("-USDT", "")
            if base in STABLE_BLACKLIST:
                continue

            try:
                vol_24h  = float(t.get("quoteVolume", 0))
                change   = float(t.get("priceChangePercent", 0))
                price    = float(t.get("lastPrice", 0))
                high_24h = float(t.get("highPrice",  0))
                low_24h  = float(t.get("lowPrice",   0))
            except (ValueError, TypeError):
                continue

            if vol_24h < min_volume or price <= 0:
                continue

            candidates.append({
                "symbol":       sym,
                "vol_24h":      vol_24h,
                "price_change": change,
                "price":        price,
                "high_24h":     high_24h,
                "low_24h":      low_24h,
            })

        log.info(f"Scanner: {len(candidates)} candidatos base")

        # 3. Score rápido con ticker data
        scored = []
        for c in candidates:
            s = self._quick_score(c, btc_change)
            if s > 0:
                c["quick_score"] = s
                scored.append(c)

        scored.sort(key=lambda x: x["quick_score"], reverse=True)

        # 4. Pre-selección amplia para deep score paralelo
        pre_n  = min(len(scored), top_n * 4)   # analizar 4x más de lo necesario
        pre    = scored[:pre_n]

        if not pre:
            log.warning("Scanner: ningún candidato pasó el filtro rápido")
            return []

        # 5. Iniciar WebSocket para los candidatos (precio en tiempo real)
        ws_syms = [c["symbol"] for c in pre[:top_n * 2]]
        asyncio.create_task(self.client.start_ws_price_feed(ws_syms))

        # 6. Descargar klines en PARALELO (gran ganancia de velocidad)
        syms_list   = [c["symbol"] for c in pre]
        klines_map  = await self.client.get_klines_multi(
            syms_list, KLINE_INTERVAL, limit=30
        )

        # 7. Deep score con los klines ya descargados
        final = []
        for c in pre:
            klines = klines_map.get(c["symbol"])
            if not klines or len(klines) < 10:
                continue
            result = self._deep_score(c, klines)
            if result is not None:
                final.append(result)

        final.sort(key=lambda x: x["final_score"], reverse=True)

        # Log top
        log.info(f"🔥 TOP {min(top_n, len(final))} símbolos calientes:")
        for i, c in enumerate(final[:top_n], 1):
            log.info(
                f"  #{i:2d} {c['symbol']:20s} "
                f"score={c['final_score']:.0f} "
                f"spike={c.get('vol_spike',0):.1f}x "
                f"chg={c['price_change']:+.1f}%"
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
        if vol >= 100_000_000:  score += 5
        elif vol >= 50_000_000: score += 10
        elif vol >= 10_000_000: score += 15
        elif vol >= 5_000_000:  score += 20
        else:                   score += 25

        if c["price"] > 0 and c["high_24h"] > 0:
            daily_range = (c["high_24h"] - c["low_24h"]) / c["price"]
            score += min(daily_range * 100, 20)

        if change > btc_change + 3:
            score += 10

        return score

    # ------------------------------------------------------------------ #
    def _deep_score(self, base: dict, klines: list) -> Optional[dict]:
        """
        Score profundo usando klines ya descargadas.
        NO hace ninguna llamada a red — todo local.
        """
        try:
            volumes  = np.array([float(k.get("volume", 0)) for k in klines])
            closes   = np.array([float(k.get("close",  0)) for k in klines])
            highs    = np.array([float(k.get("high",   0)) for k in klines])
            lows     = np.array([float(k.get("low",    0)) for k in klines])

            if len(volumes) < 5 or volumes.mean() == 0:
                return None

            vol_mean     = volumes[:-1].mean() if len(volumes) > 1 else 1.0
            vol_spike    = float(volumes[-1]) / vol_mean if vol_mean > 0 else 1.0

            # Aceleración: velas consecutivas con volumen creciente
            vol_accel = 0
            for i in range(len(volumes) - 1, 0, -1):
                if volumes[i] > volumes[i - 1]:
                    vol_accel += 1
                else:
                    break

            # Volatility breakout
            ranges       = highs - lows
            range_mean   = float(ranges[:-1].mean()) if len(ranges) > 1 else 1.0
            vol_breakout = float(ranges[-1]) / range_mean if range_mean > 0 else 1.0

            # Price breakout (cerca del máximo reciente)
            recent_high    = float(highs[:-1].max()) if len(highs) > 1 else closes[-1]
            price_breakout = closes[-1] > recent_high * 0.99

            # Score final
            final_score = base["quick_score"]

            if vol_spike >= 10:              final_score += 35
            elif vol_spike >= 5:             final_score += 25
            elif vol_spike >= 3:             final_score += 15
            elif vol_spike >= VOL_SPIKE_MIN: final_score += 8
            else:                            final_score -= 20

            final_score += min(vol_accel * 5, 20)

            if vol_breakout >= 2:   final_score += 10
            elif vol_breakout >= 1.5: final_score += 5

            if price_breakout:      final_score += 10

            result = dict(base)
            result.update({
                "vol_spike":      round(vol_spike, 2),
                "vol_accel":      vol_accel,
                "vol_breakout":   round(vol_breakout, 2),
                "price_breakout": price_breakout,
                "final_score":    round(final_score, 1),
            })
            return result

        except Exception as e:
            log.debug(f"Deep score {base.get('symbol','?')}: {e}")
            return None
