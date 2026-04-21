"""
Market Scanner v3 — Detector de Explosiones Diarias
====================================================
Objetivo: encontrar las 3-5 monedas que van a explotar HOY.

Criterios combinados (puntuación 0-100):
  1. VOLUME SPIKE       — volumen actual vs media histórica (x3, x5, x10...)
  2. PRICE MOMENTUM     — cambio % en las últimas 1h, 4h, 24h
  3. VOLATILITY BREAKOUT— rango (High-Low)/Close supera media reciente
  4. ACCELERATION       — el volumen lleva N velas consecutivas creciendo
  5. RELATIVE STRENGTH  — rendimiento vs BTC en las últimas 4h

Separación grande/pequeña para no perderse gems:
  - Large cap  (vol 24h > 50M USDT)  : candidatos explosivos conocidos
  - Mid cap    (vol 24h 5M-50M)      : zona intermedia
  - Small cap  (vol 24h 1M-5M)       : aquí suelen estar los +100%

Output: lista ordenada por score, máximo TOP_EXPLOSIONS símbolos.
"""

import logging
import os
import numpy as np
from typing import Optional

from bingx_client import BingXClient

log = logging.getLogger("SCANNER")

STABLE_BLACKLIST = {"USDC", "BUSD", "TUSD", "USDT", "DAI", "FDUSD", "USDP", "FRAX"}

# Variables de entorno
TOP_EXPLOSIONS  = int(os.getenv("TOP_SYMBOLS",      "10"))   # máximo símbolos a evaluar señal
MIN_VOL_USDT    = float(os.getenv("MIN_VOLUME_USDT","1000000"))  # 1M mínimo
VOL_SPIKE_MIN   = float(os.getenv("VOL_SPIKE_MIN",  "2.0"))  # volumen actual >= 2x la media
MOMENTUM_MIN    = float(os.getenv("MOMENTUM_MIN",   "2.0"))  # cambio 24h mínimo 2%
KLINE_INTERVAL  = os.getenv("KLINE_INTERVAL",       "1h")


class MarketScanner:
    def __init__(self, client: BingXClient):
        self.client = client

    # ------------------------------------------------------------------ #
    async def get_hot_symbols(
        self,
        top_n:      int   = TOP_EXPLOSIONS,
        min_volume: float = MIN_VOL_USDT,
    ) -> list[str]:
        """
        Devuelve los símbolos con mayor probabilidad de explosión hoy.
        """
        tickers = await self.client.get_tickers()

        # ---- Filtro base ------------------------------------------------
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

        # ---- Score rápido con datos de ticker ---------------------------
        scored = []
        for c in candidates:
            score = self._quick_score(c, btc_change)
            if score > 0:
                c["quick_score"] = score
                scored.append(c)

        scored.sort(key=lambda x: x["quick_score"], reverse=True)

        # ---- Pre-selección por caps para diversificar -------------------
        large = [c for c in scored if c["vol_24h"] >= 50_000_000]
        mid   = [c for c in scored if 5_000_000 <= c["vol_24h"] < 50_000_000]
        small = [c for c in scored if c["vol_24h"] < 5_000_000]

        # Distribución: 20% large, 40% mid, 40% small
        n_large = max(1, top_n // 5)
        n_mid   = max(1, top_n * 2 // 5)
        n_small = top_n - n_large - n_mid

        pre_selected = (
            large[:n_large] +
            mid[:n_mid]     +
            small[:n_small]
        )

        log.info(f"Pre-selección: {len(large[:n_large])} large + "
                 f"{len(mid[:n_mid])} mid + {len(small[:n_small])} small")

        # ---- Score profundo con klines (volumen spike + aceleración) ---
        final = []
        for c in pre_selected:
            deep = await self._deep_score(c["symbol"], c)
            if deep is not None:
                final.append(deep)

        final.sort(key=lambda x: x["final_score"], reverse=True)

        # Log top candidatos
        log.info(f"🔥  TOP EXPLOSIONES del día:")
        for i, c in enumerate(final[:top_n], 1):
            log.info(
                f"  #{i:2d}  {c['symbol']:20s}  "
                f"score={c['final_score']:.1f}  "
                f"vol_spike={c.get('vol_spike', 0):.1f}x  "
                f"chg24h={c['price_change']:+.1f}%  "
                f"accel={c.get('vol_accel', 0)}v  "
                f"cap={'L' if c['vol_24h']>=50e6 else 'M' if c['vol_24h']>=5e6 else 'S'}"
            )

        return [c["symbol"] for c in final[:top_n]]

    # ------------------------------------------------------------------ #
    def _quick_score(self, c: dict, btc_change: float) -> float:
        """Score rápido basado solo en datos del ticker (sin klines)."""
        score = 0.0

        change = abs(c["price_change"])

        # Momentum mínimo requerido
        if change < MOMENTUM_MIN:
            return 0.0

        # Puntos por momentum
        score += min(change * 2, 40)   # hasta 40pts por cambio de precio

        # Puntos por volumen (relativo a cap)
        vol = c["vol_24h"]
        if vol >= 100_000_000:   score += 5
        elif vol >= 50_000_000:  score += 10
        elif vol >= 10_000_000:  score += 15
        elif vol >= 5_000_000:   score += 20
        else:                    score += 25   # small cap = más potencial

        # Rango diario (High-Low)/Last — cuánto se ha movido
        if c["price"] > 0 and c["high_24h"] > 0:
            daily_range = (c["high_24h"] - c["low_24h"]) / c["price"]
            score += min(daily_range * 100, 20)   # hasta 20pts

        # Bonus si se mueve más que BTC
        if change > btc_change + 3:
            score += 10

        return score

    # ------------------------------------------------------------------ #
    async def _deep_score(self, symbol: str, base: dict) -> Optional[dict]:
        """
        Score profundo usando las últimas velas:
        - Volume spike vs media 20 períodos
        - Volumen acelerando N barras consecutivas
        - Volatility breakout
        """
        try:
            klines = await self.client.get_klines(symbol, KLINE_INTERVAL, limit=30)
            if len(klines) < 10:
                return None

            volumes = np.array([float(k["volume"]) for k in klines])
            closes  = np.array([float(k["close"])  for k in klines])
            highs   = np.array([float(k["high"])   for k in klines])
            lows    = np.array([float(k["low"])    for k in klines])

            # Volume spike: última vela vs media de las anteriores 20
            vol_mean     = volumes[:-1].mean() if len(volumes) > 1 else 1
            vol_spike    = volumes[-1] / vol_mean if vol_mean > 0 else 1

            # Aceleración de volumen: cuántas velas consecutivas el vol va subiendo
            vol_accel = 0
            for i in range(len(volumes) - 1, 0, -1):
                if volumes[i] > volumes[i - 1]:
                    vol_accel += 1
                else:
                    break

            # Volatility breakout: rango actual vs media de rangos recientes
            ranges      = highs - lows
            range_mean  = ranges[:-1].mean() if len(ranges) > 1 else 1
            vol_breakout = ranges[-1] / range_mean if range_mean > 0 else 1

            # Precio rompiendo máximo reciente (breakout)
            recent_high = highs[:-1].max() if len(highs) > 1 else closes[-1]
            price_breakout = closes[-1] > recent_high * 0.99   # dentro del 1% del máximo

            # ---- Score final ----------------------------------------
            final_score = base["quick_score"]

            # Volume spike (el más importante)
            if vol_spike >= 10:   final_score += 35
            elif vol_spike >= 5:  final_score += 25
            elif vol_spike >= 3:  final_score += 15
            elif vol_spike >= VOL_SPIKE_MIN:
                                  final_score += 8
            else:
                # Sin spike de volumen significativo → penalizar fuerte
                final_score -= 20

            # Aceleración
            final_score += min(vol_accel * 5, 20)   # hasta 20pts por aceleración

            # Volatility breakout
            if vol_breakout >= 2: final_score += 10
            elif vol_breakout >= 1.5: final_score += 5

            # Price breakout
            if price_breakout:    final_score += 10

            result = dict(base)
            result.update({
                "vol_spike":    round(vol_spike, 2),
                "vol_accel":    vol_accel,
                "vol_breakout": round(vol_breakout, 2),
                "price_breakout": price_breakout,
                "final_score":  round(final_score, 1),
            })
            return result

        except Exception as e:
            log.warning(f"Deep score error {symbol}: {e}")
            return None
