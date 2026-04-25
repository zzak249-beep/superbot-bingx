"""
Market Scanner v8 — Detector de monedas explosivas
====================================================
FIXES vs v7:
  - BUG CRÍTICO: MIN_CONF=50 demasiado alto → bajado a 30 + scores recalibrados
  - BUG CRÍTICO: campo 'quoteVolume' incorrecto en BingX → prueba múltiples campos
  - BUG: BTC_BLOCK=0.4% bloqueaba en mercados normales → subido a 2.0%
  - BUG: OI score formula (oi/1_000_000 < 1) → score por rangos
  - BUG: datetime.utcnow() deprecated → timezone.utc
  - BUG: min 20 klines era muy restrictivo → bajado a 10
  - MEJORA: log diagnóstico detallado cuando 0 símbolos
  - MEJORA: zonas RSI y BB ampliadas para más señales
"""

import asyncio, logging, os, time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import statistics

log = logging.getLogger("SCANNER")

# ── Config ──────────────────────────────────────────────────── #
TOP_N           = int(os.getenv("TOP_SYMBOLS",           "40"))
MIN_VOL_24H     = float(os.getenv("MIN_VOLUME_24H",      "500000"))   # bajado de 800k
MIN_MOMENTUM    = float(os.getenv("MOMENTUM_MIN",        "0.5"))      # bajado de 0.8
VOL_SPIKE_MIN   = float(os.getenv("VOL_SPIKE_MIN",       "1.3"))      # bajado de 1.5
MAX_SYMBOLS     = int(os.getenv("MAX_SYMBOLS",           "200"))      # subido de 150
SESSION_FILTER  = os.getenv("SESSION_FILTER",            "true").lower() == "true"
BTC_BLOCK       = float(os.getenv("BTC_BLOCK_PCT",       "2.0"))      # CRÍTICO: subido de 0.4 a 2.0
MIN_CONF        = int(os.getenv("SCANNER_MIN_CONFIDENCE","30"))       # CRÍTICO: bajado de 50 a 30

SESSIONS = {
    "london": (7,  16),
    "ny":     (13, 21),
    "asia":   (0,  8),
}


@dataclass
class CoinScore:
    symbol:         str
    score:          float = 0.0
    volume_24h:     float = 0.0
    price_chg_pct:  float = 0.0
    vol_spike:      float = 0.0
    momentum:       float = 0.0
    oi_change:      float = 0.0
    funding:        float = 0.0
    squeeze:        bool  = False
    session:        str   = ""
    tags:           list  = field(default_factory=list)


class MarketScanner:
    def __init__(self, client, ob_analyzer=None):
        self.client         = client
        self.ob             = ob_analyzer
        self._cache: dict   = {}
        self._cache_ts: float = 0.0
        self._btc_chg: float  = 0.0

    async def get_hot_symbols(self, top_n: int = TOP_N,
                               min_volume: float = MIN_VOL_24H) -> list[str]:
        now = time.time()
        if now - self._cache_ts < 25 and self._cache:
            hot    = sorted(self._cache.values(), key=lambda x: x.score, reverse=True)
            result = [c.symbol for c in hot if c.score >= MIN_CONF][:top_n]
            top    = hot[0] if hot else None
            log.info(f"Scanner cache: {len(result)} símbolos "
                     f"(top: {top.symbol if top else '—'} "
                     f"score={top.score:.0f if top else 0})")
            return result

        scores         = await self._full_scan(min_volume)
        self._cache    = {s.symbol: s for s in scores}
        self._cache_ts = now

        hot     = sorted(scores, key=lambda x: x.score, reverse=True)
        passing = [c for c in hot if c.score >= MIN_CONF]
        result  = [c.symbol for c in passing][:top_n]

        for i, c in enumerate(hot[:10], 1):
            log.info(f"  #{i:2d} {c.symbol:<20} score={c.score:.0f} "
                     f"vol={c.vol_spike:.1f}x chg={c.price_chg_pct:+.1f}% "
                     f"{'SQUEEZE ' if c.squeeze else ''}{' '.join(c.tags[:3])}")

        log.info(f"Scanner: {len(result)}/{len(scores)} símbolos "
                 f"(min_conf={MIN_CONF})")

        if not result and hot:
            log.warning(
                f"  ⚠️ 0 símbolos calientes. Top score={hot[0].score:.0f} "
                f"({hot[0].symbol}). "
                f"Considera bajar SCANNER_MIN_CONFIDENCE o MIN_VOLUME_24H."
            )
        return result

    def get_coin_score(self, symbol: str) -> Optional[CoinScore]:
        return self._cache.get(symbol)

    async def _full_scan(self, min_volume: float) -> list[CoinScore]:
        tickers = await self.client.get_all_tickers()
        if not tickers:
            log.warning("No tickers recibidos de BingX")
            return []

        log.info(f"  Tickers recibidos: {len(tickers)}")

        # BTC macro reference
        for t in tickers:
            if t.get("symbol") in ("BTC-USDT", "BTCUSDT"):
                try:
                    self._btc_chg = float(t.get("priceChangePercent", 0))
                except Exception:
                    pass
                break

        log.info(f"  BTC 24h: {self._btc_chg:+.2f}%")

        candidates   = []
        rejected_vol = 0

        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("-USDT"):
                continue
            if sym == "USDT-USDT":
                continue
            try:
                # FIX: BingX usa diferentes campos según endpoint
                vol = float(
                    t.get("quoteVolume") or
                    t.get("volume")      or
                    t.get("turnover")    or
                    t.get("vol")         or 0
                )
                chg = float(t.get("priceChangePercent", 0))
                lp  = float(t.get("lastPrice", 0) or t.get("price", 0))
            except (ValueError, TypeError):
                continue
            if lp <= 0:
                continue
            if vol < min_volume:
                rejected_vol += 1
                continue
            candidates.append({"symbol": sym, "vol": vol, "chg": chg,
                                "price": lp, "ticker": t})

        log.info(f"  Candidatos: {len(candidates)} "
                 f"(rechazados por vol<{min_volume:,.0f}: {rejected_vol})")

        if not candidates:
            log.warning(
                f"  ⚠️ 0 candidatos. "
                f"Prueba: MIN_VOLUME_24H=100000 en las variables de Railway"
            )
            return []

        candidates.sort(key=lambda x: x["vol"], reverse=True)
        candidates = candidates[:MAX_SYMBOLS]

        sem = asyncio.Semaphore(15)

        async def _score_one(c: dict) -> Optional[CoinScore]:
            async with sem:
                try:
                    return await self._score_symbol(c)
                except Exception as e:
                    log.debug(f"score {c['symbol']}: {e}")
                    return None

        results = await asyncio.gather(*[_score_one(c) for c in candidates],
                                        return_exceptions=True)
        scored = [r for r in results if isinstance(r, CoinScore)]
        log.info(f"  Scored: {len(scored)}/{len(candidates)}")
        return scored

    async def _score_symbol(self, c: dict) -> Optional[CoinScore]:
        sym   = c["symbol"]
        vol   = c["vol"]
        chg   = c["chg"]
        price = c["price"]
        score = 0.0
        tags  = []

        klines = await self.client.get_klines(sym, "1h", limit=48)
        if not klines or len(klines) < 10:  # FIX: bajado de 20
            return None

        try:
            closes  = [float(k[4]) for k in klines]
            volumes = [float(k[5]) for k in klines]
        except (IndexError, ValueError):
            return None

        if not closes or closes[-1] <= 0:
            return None

        # 1. Volumen spike
        ref_vols  = volumes[:-1]
        avg_vol   = (statistics.mean(ref_vols[-20:]) if len(ref_vols) >= 5
                     else statistics.mean(ref_vols) if ref_vols else 1)
        cur_vol   = volumes[-1]
        vol_spike = cur_vol / avg_vol if avg_vol > 0 else 1.0

        if vol_spike >= 3.0:
            score += 25; tags.append("MEGA_VOL")
        elif vol_spike >= 2.0:
            score += 18; tags.append("HIGH_VOL")
        elif vol_spike >= VOL_SPIKE_MIN:
            score += 10; tags.append("VOL_SPIKE")
        else:
            score += 3  # base positivo para no excluir buenos activos

        # 2. Bollinger Squeeze
        squeeze  = False
        bb_width = 0.0
        if len(closes) >= 20:
            bb_score, squeeze, bb_width = self._bollinger(closes)
            score += bb_score
            if squeeze:
                tags.append("BB_SQUEEZE")

        # 3. EMA momentum
        ema9  = self._ema(closes, 9)
        ema21 = self._ema(closes, 21)
        ema55 = self._ema(closes, 55) if len(closes) >= 55 else None

        momentum = 0.0
        if ema9 and ema21:
            if ema9[-1] > ema21[-1]:
                momentum = (ema9[-1] - ema21[-1]) / ema21[-1] * 100
                if momentum >= 0.5:
                    score += 15; tags.append("EMA_BULL")
                elif momentum >= 0.1:
                    score += 8
            else:
                score -= 5

        if ema55 and closes[-1] > ema55[-1]:
            score += 8; tags.append("EMA55_OK")

        # 4. Precio momentum 4h
        chg_4h = ((closes[-1] - closes[-5]) / closes[-5] * 100
                  if len(closes) >= 5 else chg)
        if chg_4h >= 3.0:
            score += 15; tags.append("PUMP_4H")
        elif chg_4h >= 1.5:
            score += 8
        elif chg_4h >= 0.3:
            score += 3
        elif chg_4h < -3.0:
            score -= 8

        # 5. RSI (zona ampliada)
        rsi = self._rsi(closes, 14)
        if rsi:
            r = rsi[-1]
            if 40 <= r <= 70:           # FIX: ampliado de 45-65
                score += 10; tags.append("RSI_OK")
            elif 30 <= r < 40:
                score += 6
            elif r > 80:
                score -= 10             # FIX: reducido de -15
            elif r < 25:
                score -= 3

        # 6. Patrones vela
        candle_score = self._candle_pattern(klines[-3:])
        score       += candle_score
        if candle_score >= 8:
            tags.append("CANDLE_BULL")

        # 7. Volumen 24h absoluto
        if vol >= 5_000_000:
            score += 10
        elif vol >= 2_000_000:
            score += 7
        elif vol >= 1_000_000:
            score += 4
        elif vol >= 500_000:
            score += 2

        # 8. Sesión activa
        hour    = datetime.now(timezone.utc).hour  # FIX: deprecated utcnow()
        session = "off"
        if SESSIONS["london"][0] <= hour < SESSIONS["london"][1]:
            session = "london"; score += 5
        elif SESSIONS["ny"][0] <= hour < SESSIONS["ny"][1]:
            session = "ny"; score += 5
        elif SESSIONS["asia"][0] <= hour < SESSIONS["asia"][1]:
            session = "asia"; score += 3

        # 9. BTC decoupling
        btc_delta = chg - self._btc_chg
        if btc_delta >= 5 and chg > 0:
            score += 12; tags.append("BTC_DECOUPLED")
        # FIX CRÍTICO: solo penalizar si BTC cae más de 2% (no 0.4%)
        if self._btc_chg < -BTC_BLOCK:
            score -= 8; tags.append("BTC_WARN")

        # 10. Open Interest
        oi_change = 0.0
        try:
            oi = await self.client.get_open_interest(sym)
            if oi > 0:
                oi_change = oi
                # FIX: score por rangos, no fórmula rota
                if oi > 10_000_000:
                    score += 8; tags.append("HIGH_OI")
                elif oi > 1_000_000:
                    score += 4
                else:
                    score += 2
        except Exception:
            pass

        # 11. Funding rate
        funding = 0.0
        try:
            funding = await self.client.get_funding_rate(sym)
            fr_abs  = abs(funding)
            if fr_abs < 0.0001:
                score += 5
            elif funding < -0.0002:
                score += 8; tags.append("NEG_FUNDING")
            elif funding > 0.0005:
                score -= 5
        except Exception:
            pass

        return CoinScore(
            symbol        = sym,
            score         = max(0.0, score),
            volume_24h    = vol,
            price_chg_pct = chg,
            vol_spike     = vol_spike,
            momentum      = momentum,
            oi_change     = oi_change,
            funding       = funding,
            squeeze       = squeeze,
            session       = session,
            tags          = tags,
        )

    # ── Indicadores ──────────────────────────────────────────── #

    def _ema(self, data: list, period: int) -> Optional[list]:
        if len(data) < period:
            return None
        k      = 2 / (period + 1)
        result = [sum(data[:period]) / period]
        for v in data[period:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    def _rsi(self, closes: list, period: int = 14) -> Optional[list]:
        if len(closes) < period + 1:
            return None
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains  = [max(d, 0)       for d in deltas]
        losses = [abs(min(d, 0))  for d in deltas]
        avg_g  = sum(gains[:period])  / period
        avg_l  = sum(losses[:period]) / period
        rsis   = []
        for i in range(period, len(deltas)):
            avg_g = (avg_g * (period - 1) + gains[i])  / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
            rs    = avg_g / avg_l if avg_l > 0 else 100
            rsis.append(100 - 100 / (1 + rs))
        return rsis if rsis else None

    def _bollinger(self, closes: list, period: int = 20, std: float = 2.0):
        if len(closes) < period:
            return 0, False, 0.0
        window   = closes[-period:]
        mean     = statistics.mean(window)
        dev      = statistics.stdev(window) if len(window) > 1 else 0
        upper    = mean + std * dev
        lower    = mean - std * dev
        bb_width = (upper - lower) / mean * 100 if mean > 0 else 0
        squeeze  = bb_width < 3.5          # FIX: subido de 3.0
        score    = 0
        if squeeze:
            score = 20
        elif bb_width < 5.0:
            score = 12
        elif bb_width < 8.0:
            score = 6
        elif bb_width < 12.0:
            score = 2
        cur = closes[-1]
        if cur <= lower * 1.01:            # FIX: ampliado de 1.005
            score += 8
        return score, squeeze, bb_width

    def _candle_pattern(self, klines: list) -> float:
        if len(klines) < 1:
            return 0
        try:
            score  = 0
            bodies = []
            for k in klines:
                o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
                bodies.append((o, h, l, c))
            last_o, last_h, last_l, last_c = bodies[-1]
            body  = abs(last_c - last_o)
            wick  = last_h - max(last_c, last_o)
            lower = min(last_c, last_o) - last_l
            total = last_h - last_l or 1
            if last_c > last_o and body / total > 0.6:
                score += 10
            if last_c > last_o and body / total > 0.4:
                score += 4
            if lower > body * 2 and wick < body * 0.5:
                score += 8
            if len(bodies) >= 2:
                prev_o, _, _, prev_c = bodies[-2]
                if (prev_c < prev_o and last_c > last_o
                        and last_c > prev_o and last_o < prev_c):
                    score += 12
            return score
        except Exception:
            return 0
