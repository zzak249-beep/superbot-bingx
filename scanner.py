"""
Market Scanner — detecta monedas explosivas en BingX
Criterios: volumen spike, momentum de precio, ATR expansión, RSI extremos
Escanea grande y pequeñas cap automáticamente
"""

import asyncio
import numpy as np
from dataclasses import dataclass
from typing import Optional
from loguru import logger

from bingx_client import BingXClient


@dataclass
class CoinScore:
    symbol:         str
    price:          float
    change_24h:     float   # %
    volume_24h:     float   # USD
    volume_spike:   float   # ratio vs avg
    momentum_score: float   # 0–100
    atr_pct:        float   # ATR/price %
    rsi:            float
    total_score:    float
    category:       str     # "large" / "small" / "micro"


class MarketScanner:
    """
    Escanea todos los pares perpetuos de BingX y rankea
    los que tienen mayor probabilidad de movimiento explosivo.
    """

    def __init__(
        self,
        client: BingXClient,
        min_volume_24h:   float = 500_000,   # USD mínimo
        max_coins:        int   = 15,
        vol_spike_min:    float = 2.0,        # 2x volumen vs media
        min_change_abs:   float = 2.0,        # mínimo 2% de movimiento 24h
        interval:         str   = "15m",
        candles:          int   = 100,
        concurrency:      int   = 8,
    ):
        self.client         = client
        self.min_volume_24h = min_volume_24h
        self.max_coins      = max_coins
        self.vol_spike_min  = vol_spike_min
        self.min_change_abs = min_change_abs
        self.interval       = interval
        self.candles        = candles
        self.concurrency    = concurrency

    def _classify(self, vol_usd: float) -> str:
        if vol_usd > 100_000_000:
            return "large"
        if vol_usd > 5_000_000:
            return "small"
        return "micro"

    async def _analyze_coin(self, symbol: str, semaphore: asyncio.Semaphore) -> Optional[CoinScore]:
        async with semaphore:
            try:
                klines = await self.client.get_klines(symbol, self.interval, self.candles)
                if not klines or len(klines) < 50:
                    return None

                closes  = np.array([float(k[4]) for k in klines])
                highs   = np.array([float(k[2]) for k in klines])
                lows    = np.array([float(k[3]) for k in klines])
                volumes = np.array([float(k[5]) for k in klines])

                # ── Volume spike ──────────────────────────────────────────
                vol_avg   = volumes[:-5].mean() if len(volumes) > 5 else volumes.mean()
                vol_spike = volumes[-1] / (vol_avg + 1e-10)

                # ── ATR% ──────────────────────────────────────────────────
                tr = np.maximum(
                    highs - lows,
                    np.maximum(
                        np.abs(highs - np.roll(closes, 1)),
                        np.abs(lows  - np.roll(closes, 1)),
                    ),
                )
                atr = tr[-14:].mean()
                atr_pct = atr / closes[-1] * 100

                # ── RSI ───────────────────────────────────────────────────
                delta  = np.diff(closes)
                gain   = np.where(delta > 0, delta, 0).mean()
                loss   = np.where(delta < 0, -delta, 0).mean()
                rsi    = 100 - 100 / (1 + gain / (loss + 1e-10)) if loss > 0 else 100

                # ── Momentum (price change last 5 bars) ───────────────────
                mom5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0

                # ── Score ─────────────────────────────────────────────────
                score = 0.0
                score += min(vol_spike / 5 * 40, 40)      # vol spike (40 pts)
                score += min(abs(mom5) / 5 * 30, 30)      # momentum (30 pts)
                score += min(atr_pct / 3 * 20, 20)        # volatilidad (20 pts)
                if rsi > 65 or rsi < 35:
                    score += 10                            # RSI extremo (10 pts)

                return CoinScore(
                    symbol        = symbol,
                    price         = closes[-1],
                    change_24h    = mom5,
                    volume_24h    = float(volumes[-1]) * closes[-1],
                    volume_spike  = round(vol_spike, 2),
                    momentum_score= round(score, 1),
                    atr_pct       = round(atr_pct, 3),
                    rsi           = round(rsi, 1),
                    total_score   = round(score, 1),
                    category      = self._classify(float(volumes[-1]) * closes[-1]),
                )
            except Exception as e:
                logger.debug(f"Scanner skip {symbol}: {e}")
                return None

    async def scan(self) -> list[CoinScore]:
        """
        Escanea todos los pares, filtra y rankea.
        Devuelve lista ordenada por score descendente.
        """
        logger.info("🔍 Iniciando escaneo de mercado...")
        tickers = await self.client.get_all_tickers()

        # Filtrar por volumen mínimo y par USDT perpetuo
        candidates = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("-USDT"):
                continue
            vol = float(t.get("quoteVolume", 0))
            change = abs(float(t.get("priceChangePercent", 0)))
            if vol >= self.min_volume_24h and change >= self.min_change_abs:
                candidates.append(sym)

        logger.info(f"  → {len(candidates)} candidatos con volumen suficiente")

        sem = asyncio.Semaphore(self.concurrency)
        tasks = [self._analyze_coin(sym, sem) for sym in candidates]
        results = await asyncio.gather(*tasks)

        # Filtrar y ordenar
        scored = [r for r in results if r is not None and r.volume_spike >= self.vol_spike_min]
        scored.sort(key=lambda x: x.total_score, reverse=True)

        top = scored[:self.max_coins]

        logger.info(f"✅ Top {len(top)} monedas explosivas encontradas:")
        for i, coin in enumerate(top[:5], 1):
            logger.info(
                f"  {i}. {coin.symbol} | Score: {coin.total_score} | "
                f"VolSpike: {coin.volume_spike}x | Mom: {coin.change_24h:.2f}% | "
                f"ATR: {coin.atr_pct:.2f}% | RSI: {coin.rsi} | [{coin.category}]"
            )

        return top
