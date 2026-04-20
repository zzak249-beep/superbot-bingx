"""
Multi-Timeframe (MTF) Confluence Analyzer
──────────────────────────────────────────
El error más común de los bots amateurs: operar contra el sesgo
de timeframes superiores. Un crossover alcista en 15m con tendencia
bajista en 4h = setup de baja calidad.

Esta clase verifica:
  • Tendencia de 1h y 4h mediante EMA200 y estructura de máximos/mínimos
  • Momentum relativo mediante RSI
  • Alineación de ADX en múltiples timeframes
  • Devuelve un "confluence score" de -100 a +100
    → positivo = sesgo alcista multi-TF
    → negativo = sesgo bajista multi-TF
    → cerca de 0 = sin sesgo claro (no operar)
"""

import numpy as np
from dataclasses import dataclass
from loguru import logger

from signal_engine import calc_rsi, calc_adx, _ema, _sma


@dataclass
class MTFResult:
    symbol:           str
    # 1h analysis
    trend_1h:         str      # "up" / "down" / "flat"
    rsi_1h:           float
    adx_1h:           float
    # 4h analysis
    trend_4h:         str
    rsi_4h:           float
    adx_4h:           float
    # confluence
    confluence_score: float    # -100 to +100
    long_confirmed:   bool
    short_confirmed:  bool
    reason:           str


# Interval map for BingX API
INTERVAL_MAP = {
    "1m":  "1m",
    "5m":  "5m",
    "15m": "15m",
    "30m": "30m",
    "1h":  "1h",
    "2h":  "2h",
    "4h":  "4h",
    "1d":  "1d",
}


class MTFAnalyzer:
    """
    Analiza el sesgo de tendencia en 1h y 4h para filtrar señales del TF base.
    Sólo deja pasar señales que están alineadas con el sesgo multi-TF.
    """

    def __init__(
        self,
        ema_len:          int   = 200,
        rsi_len:          int   = 14,
        adx_len:          int   = 14,
        min_confluence:   float = 25.0,   # mínimo score para aprobar
        candles_fetch:    int   = 250,
        check_1h:         bool  = True,
        check_4h:         bool  = True,
    ):
        self.ema_len        = ema_len
        self.rsi_len        = rsi_len
        self.adx_len        = adx_len
        self.min_confluence = min_confluence
        self.candles_fetch  = candles_fetch
        self.check_1h       = check_1h
        self.check_4h       = check_4h

    def _analyze_tf(self, closes: np.ndarray, highs: np.ndarray, lows: np.ndarray) -> dict:
        """Analiza un array de velas OHLCV y devuelve dict con métricas"""
        if len(closes) < self.ema_len:
            return {"trend": "flat", "rsi": 50.0, "adx": 0.0}

        ema    = _ema(closes, self.ema_len)
        rsi    = calc_rsi(closes, self.rsi_len)
        adx_a, plus_di, minus_di = calc_adx(highs, lows, closes, self.adx_len)

        cur_close = closes[-1]
        cur_ema   = ema[-1]
        cur_rsi   = float(rsi[-1])
        cur_adx   = float(adx_a[-1])

        # Estructura de HH/HL vs LH/LL (últimas 3 pivots)
        n = len(closes)
        lookback = min(30, n - 1)
        recent_highs = highs[-lookback:]
        recent_lows  = lows[-lookback:]

        # Simple swing detection
        swing_up   = recent_highs[-1] > recent_highs[lookback // 2]
        swing_low  = recent_lows[-1]  > recent_lows[lookback // 2]
        swing_down = recent_highs[-1] < recent_highs[lookback // 2]
        swing_fall = recent_lows[-1]  < recent_lows[lookback // 2]

        # Combine EMA position + swing structure
        ema_bullish = cur_close > cur_ema
        ema_bearish = cur_close < cur_ema

        if ema_bullish and swing_up and swing_low:
            trend = "up"
        elif ema_bearish and swing_down and swing_fall:
            trend = "down"
        else:
            trend = "flat"

        return {
            "trend":    trend,
            "rsi":      cur_rsi,
            "adx":      cur_adx,
            "plus_di":  float(plus_di[-1]),
            "minus_di": float(minus_di[-1]),
        }

    def _compute_confluence(self, tf1: dict, tf4: dict, signal_side: str) -> float:
        """
        Calcula score de confluencia -100 a +100.
        signal_side: "LONG" o "SHORT"
        """
        score = 0.0
        is_long = signal_side == "LONG"

        # ── 4h trend (peso 40%) ────────────────────────────────────────────
        if tf4["trend"] == ("up" if is_long else "down"):
            score += 40
        elif tf4["trend"] == ("down" if is_long else "up"):
            score -= 40
        # flat = 0

        # ── 1h trend (peso 30%) ───────────────────────────────────────────
        if tf1["trend"] == ("up" if is_long else "down"):
            score += 30
        elif tf1["trend"] == ("down" if is_long else "up"):
            score -= 30

        # ── RSI alignment (peso 20%) ──────────────────────────────────────
        # Long: 4h RSI > 50 y 1h RSI > 50
        rsi_ok_long  = tf4["rsi"] > 52 and tf1["rsi"] > 52
        rsi_ok_short = tf4["rsi"] < 48 and tf1["rsi"] < 48
        if (is_long and rsi_ok_long) or (not is_long and rsi_ok_short):
            score += 20
        elif (is_long and rsi_ok_short) or (not is_long and rsi_ok_long):
            score -= 20

        # ── ADX confirms trend (peso 10%) ─────────────────────────────────
        if tf4["adx"] > 20 and tf1["adx"] > 20:
            score += 10

        return score

    async def analyze(
        self, client, symbol: str, signal_side: str, base_closes: np.ndarray = None
    ) -> MTFResult:
        """
        Descarga velas 1h y 4h, analiza confluencia con la señal del TF base.
        """
        tf1_data = {"trend": "flat", "rsi": 50.0, "adx": 0.0, "plus_di": 20.0, "minus_di": 20.0}
        tf4_data = {"trend": "flat", "rsi": 50.0, "adx": 0.0, "plus_di": 20.0, "minus_di": 20.0}

        try:
            if self.check_1h:
                klines_1h = await client.get_klines(symbol, "1h", self.candles_fetch)
                if klines_1h and len(klines_1h) >= self.ema_len:
                    c = np.array([float(k[4]) for k in klines_1h])
                    h = np.array([float(k[2]) for k in klines_1h])
                    l = np.array([float(k[3]) for k in klines_1h])
                    tf1_data = self._analyze_tf(c, h, l)
        except Exception as e:
            logger.debug(f"MTF 1h error {symbol}: {e}")

        try:
            if self.check_4h:
                klines_4h = await client.get_klines(symbol, "4h", self.candles_fetch)
                if klines_4h and len(klines_4h) >= self.ema_len:
                    c = np.array([float(k[4]) for k in klines_4h])
                    h = np.array([float(k[2]) for k in klines_4h])
                    l = np.array([float(k[3]) for k in klines_4h])
                    tf4_data = self._analyze_tf(c, h, l)
        except Exception as e:
            logger.debug(f"MTF 4h error {symbol}: {e}")

        # Compute confluence
        confluence = self._compute_confluence(tf1_data, tf4_data, signal_side)
        long_ok  = confluence >= self.min_confluence
        short_ok = confluence <= -self.min_confluence

        reasons = []
        reasons.append(f"1h: {tf1_data['trend']} (ADX {tf1_data['adx']:.1f} | RSI {tf1_data['rsi']:.1f})")
        reasons.append(f"4h: {tf4_data['trend']} (ADX {tf4_data['adx']:.1f} | RSI {tf4_data['rsi']:.1f})")
        reasons.append(f"Confluence: {confluence:+.0f}/100")

        confirmed = long_ok if signal_side == "LONG" else short_ok
        status = "✅ MTF alineado" if confirmed else "❌ MTF en contra"

        logger.info(f"[MTF] {symbol} {signal_side}: {status} | {' | '.join(reasons)}")

        return MTFResult(
            symbol=symbol,
            trend_1h=tf1_data["trend"],
            rsi_1h=tf1_data["rsi"],
            adx_1h=tf1_data["adx"],
            trend_4h=tf4_data["trend"],
            rsi_4h=tf4_data["rsi"],
            adx_4h=tf4_data["adx"],
            confluence_score=confluence,
            long_confirmed=long_ok,
            short_confirmed=short_ok,
            reason=" | ".join(reasons),
        )
