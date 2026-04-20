"""
Signal Engine — Signal Projection Explorer mejorado
Implementa: proyección estadística + ADX + ATR + RSI + régimen de mercado
Equivalente Python del indicador Pine Script de TradingView
"""

import numpy as np
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from loguru import logger


class MarketRegime(Enum):
    TRENDING_UP   = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING       = "ranging"
    VOLATILE      = "volatile"


class SignalType(Enum):
    LONG  = "LONG"
    SHORT = "SHORT"
    NONE  = "NONE"


@dataclass
class SignalResult:
    signal:       SignalType     = SignalType.NONE
    regime:       MarketRegime   = MarketRegime.RANGING
    score:        float          = 0.0          # 0–100
    adx:          float          = 0.0
    atr:          float          = 0.0
    rsi:          float          = 50.0
    plus_di:      float          = 0.0
    minus_di:     float          = 0.0
    projected_tp: float          = 0.0
    projected_sl: float          = 0.0
    projection:   dict           = field(default_factory=dict)  # percentiles


# ─── Technical Indicators ─────────────────────────────────────────────────────

def _sma(arr: np.ndarray, n: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    for i in range(n - 1, len(arr)):
        out[i] = arr[i - n + 1:i + 1].mean()
    return out


def _ema(arr: np.ndarray, n: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    k = 2.0 / (n + 1)
    start = np.argmax(~np.isnan(arr))
    out[start + n - 1] = arr[start:start + n].mean()
    for i in range(start + n, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def calc_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(closes, prepend=closes[0])
    gain  = np.where(delta > 0, delta, 0.0)
    loss  = np.where(delta < 0, -delta, 0.0)
    avg_g = _ema(gain, period)
    avg_l = _ema(loss, period)
    rs    = np.where(avg_l == 0, 100, avg_g / (avg_l + 1e-10))
    return 100 - 100 / (1 + rs)


def calc_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    tr = np.maximum(
        highs - lows,
        np.maximum(
            np.abs(highs - np.roll(closes, 1)),
            np.abs(lows  - np.roll(closes, 1)),
        ),
    )
    tr[0] = highs[0] - lows[0]
    return _ema(tr, period)


def calc_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> tuple:
    """Returns (adx, plus_di, minus_di)"""
    n = len(closes)
    atr = calc_atr(highs, lows, closes, period)

    up_move   = highs - np.roll(highs, 1)
    down_move = np.roll(lows, 1) - lows
    up_move[0]   = 0
    down_move[0] = 0

    plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_di  = 100 * _ema(plus_dm, period)  / (atr + 1e-10)
    minus_di = 100 * _ema(minus_dm, period) / (atr + 1e-10)

    dx  = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    adx = _ema(dx, period)
    return adx, plus_di, minus_di


def calc_bbands(closes: np.ndarray, period: int = 20, mult: float = 2.0):
    mid = _sma(closes, period)
    std = np.array([
        closes[max(0, i - period + 1):i + 1].std() if i >= period - 1 else np.nan
        for i in range(len(closes))
    ])
    return mid + mult * std, mid, mid - mult * std


# ─── Signal Projection Explorer ───────────────────────────────────────────────

class SignalProjectionExplorer:
    """
    Core engine. Detects signals and computes projection statistics
    from historical occurrences (idéntico al indicador de QuantNomad
    pero con filtros ADX/ATR/RSI/régimen añadidos).
    """

    def __init__(
        self,
        fast_len:       int   = 50,
        slow_len:       int   = 200,
        proj_length:    int   = 10,
        adx_len:        int   = 14,
        adx_min:        float = 20.0,
        atr_len:        int   = 14,
        atr_ma_len:     int   = 50,
        atr_mult:       float = 1.0,
        rsi_len:        int   = 14,
        rsi_long_min:   float = 52.0,
        rsi_short_max:  float = 48.0,
        ema_trend_len:  int   = 200,
        adx_volatile:   float = 35.0,
    ):
        self.fast_len      = fast_len
        self.slow_len      = slow_len
        self.proj_length   = proj_length
        self.adx_len       = adx_len
        self.adx_min       = adx_min
        self.atr_len       = atr_len
        self.atr_ma_len    = atr_ma_len
        self.atr_mult      = atr_mult
        self.rsi_len       = rsi_len
        self.rsi_long_min  = rsi_long_min
        self.rsi_short_max = rsi_short_max
        self.ema_trend_len = ema_trend_len
        self.adx_volatile  = adx_volatile

    def _detect_regime(
        self, adx: float, plus_di: float, minus_di: float,
        atr: float, atr_ma: float, close: float, trend_ema: float
    ) -> MarketRegime:
        if atr > atr_ma * 1.8:
            return MarketRegime.VOLATILE
        if adx >= self.adx_min:
            if close > trend_ema and plus_di > minus_di:
                return MarketRegime.TRENDING_UP
            if close < trend_ema and minus_di > plus_di:
                return MarketRegime.TRENDING_DOWN
        return MarketRegime.RANGING

    def _compute_score(
        self, signal: SignalType, adx: float, plus_di: float, minus_di: float,
        atr: float, atr_ma: float, rsi: float, close: float, trend_ema: float
    ) -> float:
        score = 0.0

        # ADX strength (30 pts)
        if adx >= 30:
            score += 30
        elif adx >= 25:
            score += 22
        elif adx >= self.adx_min:
            score += 15

        # ATR expansion (20 pts)
        if atr >= atr_ma * 1.3:
            score += 20
        elif atr >= atr_ma * self.atr_mult:
            score += 12

        # RSI momentum (20 pts)
        if signal == SignalType.LONG:
            if rsi >= 60:
                score += 20
            elif rsi >= self.rsi_long_min:
                score += 12
        else:
            if rsi <= 40:
                score += 20
            elif rsi <= self.rsi_short_max:
                score += 12

        # DMI alignment (20 pts)
        if signal == SignalType.LONG and plus_di > minus_di:
            score += 20 * min(1, (plus_di - minus_di) / 10)
        elif signal == SignalType.SHORT and minus_di > plus_di:
            score += 20 * min(1, (minus_di - plus_di) / 10)

        # Trend EMA (10 pts)
        if signal == SignalType.LONG and close > trend_ema:
            score += 10
        elif signal == SignalType.SHORT and close < trend_ema:
            score += 10

        return round(min(score, 100), 1)

    def _compute_projections(self, closes: np.ndarray, signal_indices: list) -> dict:
        """Collect post-signal returns across history (replica del Pine Script)"""
        if not signal_indices or len(signal_indices) < 3:
            return {}

        pnl_by_bar: list[list] = [[] for _ in range(self.proj_length)]
        n = len(closes)

        for sig_i in signal_indices:
            for offset in range(1, self.proj_length + 1):
                future_i = sig_i + offset
                if future_i >= n:
                    break
                pnl = closes[future_i] / closes[sig_i] - 1
                pnl_by_bar[offset - 1].append(pnl)

        if not pnl_by_bar[0]:
            return {}

        result = {}
        for bar_idx, pnls in enumerate(pnl_by_bar):
            if not pnls:
                continue
            arr = np.array(pnls)
            result[bar_idx + 1] = {
                "worst":  float(arr.min()),
                "best":   float(arr.max()),
                "p25":    float(np.percentile(arr, 25)),
                "p75":    float(np.percentile(arr, 75)),
                "mean":   float(arr.mean()),
                "median": float(np.median(arr)),
                "count":  len(pnls),
            }
        return result

    def analyze(
        self,
        opens:   np.ndarray,
        highs:   np.ndarray,
        lows:    np.ndarray,
        closes:  np.ndarray,
        volumes: np.ndarray,
    ) -> SignalResult:
        """
        Full analysis on OHLCV arrays. Returns SignalResult with
        current signal, regime, score, and projection stats.
        """
        n = len(closes)
        min_bars = max(self.slow_len, self.ema_trend_len) + self.adx_len + 20
        if n < min_bars:
            return SignalResult()

        # ── Indicators ──────────────────────────────────────────────────────
        ma_fast   = _sma(closes, self.fast_len)
        ma_slow   = _sma(closes, self.slow_len)
        adx_arr, plus_di_arr, minus_di_arr = calc_adx(highs, lows, closes, self.adx_len)
        atr_arr   = calc_atr(highs, lows, closes, self.atr_len)
        atr_ma    = _sma(atr_arr, self.atr_ma_len)
        rsi_arr   = calc_rsi(closes, self.rsi_len)
        trend_ema = _ema(closes, self.ema_trend_len)

        # ── Current values ───────────────────────────────────────────────────
        cur_adx      = float(adx_arr[-1])
        cur_plus_di  = float(plus_di_arr[-1])
        cur_minus_di = float(minus_di_arr[-1])
        cur_atr      = float(atr_arr[-1])
        cur_atr_ma   = float(atr_ma[-1])
        cur_rsi      = float(rsi_arr[-1])
        cur_close    = float(closes[-1])
        cur_trend    = float(trend_ema[-1])

        # ── SMA crossover signal (same as Pine Script) ───────────────────────
        crossover_up   = (ma_fast[-2] <= ma_slow[-2]) and (ma_fast[-1] > ma_slow[-1])
        crossover_down = (ma_fast[-2] >= ma_slow[-2]) and (ma_fast[-1] < ma_slow[-1])

        # Collect historical signal indices for projection
        long_signal_indices  = []
        short_signal_indices = []
        for i in range(1, n):
            if np.isnan(ma_fast[i]) or np.isnan(ma_slow[i]):
                continue
            if ma_fast[i - 1] <= ma_slow[i - 1] and ma_fast[i] > ma_slow[i]:
                long_signal_indices.append(i)
            elif ma_fast[i - 1] >= ma_slow[i - 1] and ma_fast[i] < ma_slow[i]:
                short_signal_indices.append(i)

        # ── Filters ──────────────────────────────────────────────────────────
        adx_ok   = cur_adx >= self.adx_min
        atr_ok   = cur_atr >= cur_atr_ma * self.atr_mult
        not_wild = cur_atr < cur_atr_ma * 2.5  # bloquear volatilidad extrema

        long_ok  = (crossover_up   and adx_ok and atr_ok and not_wild
                    and cur_rsi >= self.rsi_long_min
                    and cur_plus_di > cur_minus_di)
        short_ok = (crossover_down and adx_ok and atr_ok and not_wild
                    and cur_rsi <= self.rsi_short_max
                    and cur_minus_di > cur_plus_di)

        # ── Regime ───────────────────────────────────────────────────────────
        regime = self._detect_regime(
            cur_adx, cur_plus_di, cur_minus_di,
            cur_atr, cur_atr_ma, cur_close, cur_trend
        )

        # ── Signal ───────────────────────────────────────────────────────────
        if long_ok:
            signal = SignalType.LONG
            projection = self._compute_projections(closes, long_signal_indices)
        elif short_ok:
            signal = SignalType.SHORT
            projection = self._compute_projections(closes, short_signal_indices)
        else:
            signal = SignalType.NONE
            projection = {}

        # ── Score ─────────────────────────────────────────────────────────
        if signal != SignalType.NONE:
            score = self._compute_score(
                signal, cur_adx, cur_plus_di, cur_minus_di,
                cur_atr, cur_atr_ma, cur_rsi, cur_close, cur_trend
            )
        else:
            score = 0.0

        # ── TP/SL from ATR ────────────────────────────────────────────────
        atr_mult_tp = 2.5
        atr_mult_sl = 1.2
        if signal == SignalType.LONG:
            tp = cur_close + cur_atr * atr_mult_tp
            sl = cur_close - cur_atr * atr_mult_sl
        elif signal == SignalType.SHORT:
            tp = cur_close - cur_atr * atr_mult_tp
            sl = cur_close + cur_atr * atr_mult_sl
        else:
            tp = sl = 0.0

        return SignalResult(
            signal=signal,
            regime=regime,
            score=score,
            adx=cur_adx,
            atr=cur_atr,
            rsi=cur_rsi,
            plus_di=cur_plus_di,
            minus_di=cur_minus_di,
            projected_tp=round(tp, 8),
            projected_sl=round(sl, 8),
            projection=projection,
        )
