"""
Market Scanner v7 — Detector de monedas explosivas
====================================================
ESTRATEGIA: Busca coins que van a EXPLOTAR antes de que exploten.
  
FACTORES DE DETECCIÓN TEMPRANA:
  1. Volumen spike anómalo (acumulación institucional)
  2. Squeeze de Bollinger (energía comprimida = explosión inminente)
  3. Momentum de precio + EMA alignment
  4. Open Interest creciente (posiciones nuevas entrando)
  5. Funding rate neutral/negativo (longs baratos)
  6. Breadth del mercado (confirmación macro)
  7. Sesión activa (London/NY overlap = máxima liquidez)
  8. Correlación con BTC (desacoplamiento = señal)
"""

import asyncio, logging, os, time
from dataclasses import dataclass, field
from typing import Optional
import statistics

log = logging.getLogger("SCANNER")

# ── Config ──────────────────────────────────────────────────── #
TOP_N           = int(os.getenv("TOP_SYMBOLS",        "40"))
MIN_VOL_24H     = float(os.getenv("MIN_VOLUME_24H",   "800000"))
MIN_MOMENTUM    = float(os.getenv("MOMENTUM_MIN",     "0.8"))
VOL_SPIKE_MIN   = float(os.getenv("VOL_SPIKE_MIN",    "1.5"))
MAX_SYMBOLS     = int(os.getenv("MAX_SYMBOLS",        "150"))
SESSION_FILTER  = os.getenv("SESSION_FILTER",         "true").lower() == "true"
BTC_BLOCK       = float(os.getenv("BTC_BLOCK_PCT",    "0.4"))
MIN_CONF        = int(os.getenv("SCANNER_MIN_CONFIDENCE", "50"))

# Sesiones horarias UTC
SESSIONS = {
    "london": (7, 12),
    "ny":     (13, 17),
    "asia":   (0,  6),
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
        self.client      = client
        self.ob          = ob_analyzer
        self._cache: dict      = {}        # symbol → CoinScore
        self._cache_ts: float  = 0.0
        self._btc_chg:  float  = 0.0
        self._vol_baseline: dict = {}      # sym → avg_vol (histórico 7d)

    # ── API pública ───────────────────────────────────────────── #

    async def get_hot_symbols(self, top_n: int = TOP_N,
                               min_volume: float = MIN_VOL_24H) -> list[str]:
        """
        Retorna los N símbolos con mayor probabilidad de movimiento explosivo.
        Scored por múltiples factores, no solo volumen.
        """
        now = time.time()

        # Cache de 25s para no saturar la API
        if now - self._cache_ts < 25 and self._cache:
            hot = sorted(self._cache.values(), key=lambda x: x.score, reverse=True)
            result = [c.symbol for c in hot if c.score >= MIN_CONF][:top_n]
            log.info(f"Scanner cache: {len(result)} símbolos (top score: {hot[0].score:.0f} {hot[0].symbol if hot else ''})")
            return result

        scores = await self._full_scan(min_volume)
        self._cache    = {s.symbol: s for s in scores}
        self._cache_ts = now

        hot = sorted(scores, key=lambda x: x.score, reverse=True)

        # Log top-10
        for i, c in enumerate(hot[:10], 1):
            log.info(f"  Scanner #{i:2d} {c.symbol:<20} score={c.score:.0f} "
                     f"vol={c.vol_spike:.1f}x chg={c.price_chg_pct:+.1f}% "
                     f"{'SQUEEZE ' if c.squeeze else ''}{' '.join(c.tags[:2])}")

        result = [c.symbol for c in hot if c.score >= MIN_CONF][:top_n]
        log.info(f"Scanner: {len(result)}/{len(scores)} símbolos calientes (min_conf={MIN_CONF})")
        return result

    def get_coin_score(self, symbol: str) -> Optional[CoinScore]:
        return self._cache.get(symbol)

    # ── Escaneo completo ──────────────────────────────────────── #

    async def _full_scan(self, min_volume: float) -> list[CoinScore]:
        # 1. Tickers de todos los símbolos en 1 sola llamada
        tickers = await self.client.get_all_tickers()
        if not tickers:
            log.warning("No tickers recibidos de BingX")
            return []

        # 2. BTC change como referencia macro
        for t in tickers:
            if t.get("symbol") == "BTC-USDT":
                try:
                    self._btc_chg = float(t.get("priceChangePercent", 0))
                except Exception:
                    pass
                break

        # 3. Filtro inicial — volumen y símbolo válido
        candidates = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("-USDT"):
                continue
            if sym in ("USDT-USDT",):
                continue
            try:
                vol = float(t.get("quoteVolume", 0) or t.get("volume", 0))
                chg = float(t.get("priceChangePercent", 0))
                lp  = float(t.get("lastPrice", 0))
            except (ValueError, TypeError):
                continue
            if vol < min_volume or lp <= 0:
                continue
            candidates.append({"symbol": sym, "vol": vol, "chg": chg, "price": lp, "ticker": t})

        if not candidates:
            log.warning(f"0 candidatos tras filtro vol>={min_volume:,.0f}")
            return []

        # Limitar para no saturar klines
        candidates = candidates[:MAX_SYMBOLS]

        # 4. Score en paralelo (con semáforo)
        sem = asyncio.Semaphore(12)

        async def _score_one(c: dict) -> Optional[CoinScore]:
            async with sem:
                return await self._score_symbol(c)

        results = await asyncio.gather(*[_score_one(c) for c in candidates], return_exceptions=True)
        return [r for r in results if isinstance(r, CoinScore)]

    async def _score_symbol(self, c: dict) -> Optional[CoinScore]:
        sym   = c["symbol"]
        vol   = c["vol"]
        chg   = c["chg"]
        price = c["price"]
        score = 0.0
        tags  = []

        # ── Klines 1h (últimas 48 velas) ────────────────────── #
        klines = await self.client.get_klines(sym, "1h", limit=48)
        if not klines or len(klines) < 20:
            return None

        try:
            closes  = [float(k[4]) for k in klines]
            volumes = [float(k[5]) for k in klines]
            highs   = [float(k[2]) for k in klines]
            lows    = [float(k[3]) for k in klines]
        except (IndexError, ValueError):
            return None

        if not closes or closes[-1] <= 0:
            return None

        # ── 1. Volumen spike ────────────────────────────────── #
        avg_vol_20 = statistics.mean(volumes[-21:-1]) if len(volumes) > 21 else statistics.mean(volumes[:-1])
        cur_vol    = volumes[-1]
        vol_spike  = cur_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

        if vol_spike >= 3.0:
            score += 25; tags.append("MEGA_VOL")
        elif vol_spike >= 2.0:
            score += 18; tags.append("HIGH_VOL")
        elif vol_spike >= VOL_SPIKE_MIN:
            score += 10; tags.append("VOL_SPIKE")

        # ── 2. Bollinger Squeeze ─────────────────────────────── #
        bb_score, squeeze, bb_width = self._bollinger(closes)
        score += bb_score
        if squeeze:
            tags.append("BB_SQUEEZE")

        # ── 3. EMA momentum ─────────────────────────────────── #
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

        # EMA55 confirmación macro
        if ema55 and closes[-1] > ema55[-1]:
            score += 8; tags.append("EMA55_OK")

        # ── 4. Precio momentum (cambio 4h) ───────────────────── #
        chg_4h = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0
        if chg_4h >= 3.0:
            score += 15; tags.append("PUMP_4H")
        elif chg_4h >= 1.5:
            score += 8
        elif chg_4h < -3.0:
            score -= 10  # momentum negativo penaliza

        # ── 5. RSI (evitar sobrecompra extrema) ──────────────── #
        rsi = self._rsi(closes, 14)
        if rsi:
            r = rsi[-1]
            if 45 <= r <= 65:
                score += 10; tags.append("RSI_OK")
            elif 35 <= r < 45:
                score += 5   # oversold recovery
            elif r > 80:
                score -= 15  # sobrecompra peligrosa
            elif r < 25:
                score -= 5

        # ── 6. Patrón de velas (últimas 3) ───────────────────── #
        candle_score = self._candle_pattern(klines[-3:])
        score += candle_score
        if candle_score >= 8:
            tags.append("CANDLE_BULL")

        # ── 7. Volumen 24h total ─────────────────────────────── #
        if vol >= 5_000_000:
            score += 8
        elif vol >= 2_000_000:
            score += 5
        elif vol >= 1_000_000:
            score += 3

        # ── 8. Sesión activa ─────────────────────────────────── #
        import datetime
        hour = datetime.datetime.utcnow().hour
        session = "off"
        if SESSIONS["london"][0] <= hour < SESSIONS["london"][1]:
            session = "london"; score += 5
        elif SESSIONS["ny"][0] <= hour < SESSIONS["ny"][1]:
            session = "ny"; score += 5
        elif SESSIONS["asia"][0] <= hour < SESSIONS["asia"][1]:
            session = "asia"; score += 2

        # ── 9. Desacoplamiento BTC (potencial altseason) ─────── #
        btc_delta = abs(chg - self._btc_chg)
        if btc_delta >= 5 and chg > 0:
            score += 12; tags.append("BTC_DECOUPLED")
        elif abs(self._btc_chg) > BTC_BLOCK and self._btc_chg < -1.5:
            score -= 8   # BTC cayendo fuerte → penalizar longs

        # ── 10. OI change (si disponible) ────────────────────── #
        oi_change = 0.0
        try:
            oi = await self.client.get_open_interest(sym)
            if oi > 0:
                oi_change = oi   # guardamos para referencia
                score += min(8, oi / 1_000_000)  # más OI = más interés
        except Exception:
            pass

        # ── 11. Funding rate ─────────────────────────────────── #
        funding = 0.0
        try:
            funding = await self.client.get_funding_rate(sym)
            fr_abs  = abs(funding)
            if fr_abs < 0.0001:
                score += 5   # funding neutro = no apalancado
            elif funding < -0.0002:
                score += 8; tags.append("NEG_FUNDING")  # shorts pagando = bullish
            elif funding > 0.0005:
                score -= 8   # longs sobrepagando = dangerous
        except Exception:
            pass

        cs = CoinScore(
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
        return cs

    # ── Indicadores técnicos ──────────────────────────────────── #

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
        gains  = [max(d, 0) for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]

        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period
        rsis  = []
        for i in range(period, len(deltas)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
            rs    = avg_g / avg_l if avg_l > 0 else 100
            rsis.append(100 - 100 / (1 + rs))
        return rsis if rsis else None

    def _bollinger(self, closes: list, period: int = 20, std: float = 2.0):
        """Devuelve (score, squeeze, bb_width_pct)"""
        if len(closes) < period:
            return 0, False, 0.0
        window   = closes[-period:]
        mean     = statistics.mean(window)
        dev      = statistics.stdev(window)
        upper    = mean + std * dev
        lower    = mean - std * dev
        bb_width = (upper - lower) / mean * 100 if mean > 0 else 0

        squeeze = bb_width < 3.0
        score   = 0
        if squeeze:
            score = 20   # energía comprimida → explosión inminente
        elif bb_width < 5.0:
            score = 12
        elif bb_width < 8.0:
            score = 6

        # Precio tocando banda inferior (potencial rebote)
        cur = closes[-1]
        if cur <= lower * 1.005:
            score += 8
        return score, squeeze, bb_width

    def _candle_pattern(self, klines: list) -> float:
        """Detecta patrones alcistas en últimas 3 velas."""
        if len(klines) < 1:
            return 0
        try:
            score = 0
            bodies = []
            for k in klines:
                o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
                bodies.append((o, h, l, c))

            last_o, last_h, last_l, last_c = bodies[-1]
            body  = abs(last_c - last_o)
            wick  = last_h - max(last_c, last_o)
            lower = min(last_c, last_o) - last_l
            total = last_h - last_l or 1

            # Vela alcista fuerte
            if last_c > last_o and body / total > 0.6:
                score += 10
            # Hammer / pin bar
            if lower > body * 2 and wick < body * 0.5:
                score += 8
            # Engulfing alcista
            if len(bodies) >= 2:
                prev_o, _, _, prev_c = bodies[-2]
                if prev_c < prev_o and last_c > last_o and last_c > prev_o and last_o < prev_c:
                    score += 12

            return score
        except Exception:
            return 0
