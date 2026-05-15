"""indicators.py — Motor de indicadores Sniper Bot V49
Porta fielmente la lógica Pine Script del V49 Definitivo.
"""
import numpy as np
import pandas as pd
from config import (
    SLOPE_MIN, LOOKBACK_MARKOV, PROB_THRESHOLD,
    ADX_LEN, ADX_TREND, ADX_RANGE,
    PIVOT_LEN, RVOL_MIN, POC_LOOKBACK,
    ATR_MULT_TP, ATR_MULT_SL,
)


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def _atr(df: pd.DataFrame, length: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(span=length, adjust=False).mean()


def _rma(series: pd.Series, length: int) -> pd.Series:
    """RMA (Wilder's MA) — usado en ADX."""
    alpha = 1 / length
    return series.ewm(alpha=alpha, adjust=False).mean()


def _adx(df: pd.DataFrame, length: int) -> pd.DataFrame:
    h, l, c = df["high"], df["low"], df["close"]
    up   = h - h.shift(1)
    down = l.shift(1) - l
    plus_dm  = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    atr14 = _rma(pd.Series(
        pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1),
        index=df.index), length)
    plus_di  = 100 * _rma(pd.Series(plus_dm,  index=df.index), length) / atr14
    minus_di = 100 * _rma(pd.Series(minus_dm, index=df.index), length) / atr14
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val  = _rma(dx.fillna(0), length)
    return pd.DataFrame({"plus_di": plus_di, "minus_di": minus_di, "adx": adx_val}, index=df.index)


def _stoch(series: pd.Series, length: int) -> pd.Series:
    lowest  = series.rolling(length).min()
    highest = series.rolling(length).max()
    denom   = (highest - lowest).replace(0, np.nan)
    return 100 * (series - lowest) / denom


def _stc(close: pd.Series, stoch_len=10, fast=23, slow=50) -> pd.Series:
    """Schaff Trend Cycle."""
    macd = _ema(close, fast) - _ema(close, slow)
    st   = _stoch(macd, stoch_len)
    return _ema(st.fillna(50), 3)


def _vwap(df: pd.DataFrame) -> pd.Series:
    tp  = (df["high"] + df["low"] + df["close"]) / 3
    cum_v  = (tp * df["volume"]).cumsum()
    cum_tv = df["volume"].cumsum()
    return cum_v / cum_tv.replace(0, np.nan)


def _pivot_high(df: pd.DataFrame, length: int) -> pd.Series:
    highs = df["high"]
    result = pd.Series(np.nan, index=df.index)
    for i in range(length, len(df) - length):
        window = highs.iloc[i - length: i + length + 1]
        if highs.iloc[i] == window.max():
            result.iloc[i] = highs.iloc[i]
    return result


def _pivot_low(df: pd.DataFrame, length: int) -> pd.Series:
    lows = df["low"]
    result = pd.Series(np.nan, index=df.index)
    for i in range(length, len(df) - length):
        window = lows.iloc[i - length: i + length + 1]
        if lows.iloc[i] == window.min():
            result.iloc[i] = lows.iloc[i]
    return result


def _poc(df: pd.DataFrame, lookback: int) -> float:
    """Point of Control: precio con mayor volumen en ventana."""
    sub = df.tail(lookback)
    if sub.empty:
        return float("nan")
    return float(sub.loc[sub["volume"].idxmax(), "close"])


# ══════════════════════════════════════════════════════════════
#  MOTOR MARKOV
# ══════════════════════════════════════════════════════════════

class MarkovEngine:
    """Cadena de Markov de orden-1 con ventana deslizante."""

    def __init__(self, lookback: int = LOOKBACK_MARKOV):
        self.lookback = lookback
        self.matrix   = np.zeros(9, dtype=float)   # 3×3 aplanada

    def _state(self, slope: float, threshold: float) -> int:
        if slope > threshold:
            return 0   # bull
        if slope < -threshold:
            return 1   # bear
        return 2       # neutral

    def update(self, slopes: pd.Series, adaptive_threshold: float) -> tuple[float, float]:
        """
        Recalcula la matriz completa sobre la ventana de slopes.
        Devuelve (prob_bull, prob_bear) del estado actual.
        """
        self.matrix[:] = 0.0
        window = slopes.dropna().values[-self.lookback:]
        if len(window) < 2:
            return 0.0, 0.0

        states = np.array([self._state(s, adaptive_threshold) for s in window])
        for i in range(1, len(states)):
            idx = states[i - 1] * 3 + states[i]
            self.matrix[idx] += 1.0

        curr_s   = states[-1]
        base     = curr_s * 3
        total    = self.matrix[base] + self.matrix[base + 1] + self.matrix[base + 2]
        if total == 0:
            return 0.0, 0.0
        return (self.matrix[base] / total) * 100, (self.matrix[base + 1] / total) * 100


# ══════════════════════════════════════════════════════════════
#  PIPELINE PRINCIPAL
# ══════════════════════════════════════════════════════════════

def compute(df: pd.DataFrame, markov: MarkovEngine) -> dict:
    """
    Recibe un DataFrame OHLCV y devuelve un dict con todos
    los indicadores y las señales long/short del V49.
    """
    df = df.copy()

    # ── Base ──────────────────────────────────────────────────
    ema7        = _ema(df["close"], 7)
    atr7        = _atr(df, 7)
    atr14       = _atr(df, 14)
    magic_slope = ((ema7 - ema7.shift(1)) / atr7.clip(lower=1e-9)) * 100

    # ── ADX adaptativo ────────────────────────────────────────
    adx_df      = _adx(df, ADX_LEN)
    adx_val     = adx_df["adx"].iloc[-1]
    is_trending = adx_val > ADX_TREND
    is_ranging  = adx_val < ADX_RANGE
    adaptive_slope = (
        SLOPE_MIN * 1.30 if is_ranging else
        SLOPE_MIN * 0.85 if is_trending else
        SLOPE_MIN
    )

    # ── Markov ────────────────────────────────────────────────
    prob_bull, prob_bear = markov.update(magic_slope, adaptive_slope)

    # ── Filtros institucionales ───────────────────────────────
    vwap_val    = _vwap(df)
    vol_sma     = df["volume"].rolling(50).mean()
    rvol        = (df["volume"] / vol_sma.replace(0, np.nan)).fillna(0)
    is_dense    = rvol.iloc[-1] >= RVOL_MIN

    poc_level   = _poc(df, POC_LOOKBACK)
    stc_val     = _stc(df["close"]).iloc[-1]

    ph_series   = _pivot_high(df, PIVOT_LEN)
    pl_series   = _pivot_low(df, PIVOT_LEN)
    peak        = ph_series.dropna().iloc[-1] if ph_series.dropna().shape[0] > 0 else float("nan")
    valley      = pl_series.dropna().iloc[-1] if pl_series.dropna().shape[0] > 0 else float("nan")

    # ── Última vela ───────────────────────────────────────────
    last        = df.iloc[-1]
    slope_now   = float(magic_slope.iloc[-1])
    vwap_now    = float(vwap_val.iloc[-1])
    rvol_now    = float(rvol.iloc[-1])
    atr14_now   = float(atr14.iloc[-1])

    # ── Umbral dinámico ───────────────────────────────────────
    threshold   = PROB_THRESHOLD - 5.0 if is_dense else PROB_THRESHOLD

    # ── Señales ───────────────────────────────────────────────
    long_sig = (
        not np.isnan(valley)           and
        float(last["low"])  < valley   and
        float(last["close"]) < vwap_now and
        slope_now > adaptive_slope      and
        is_dense                        and
        prob_bull > threshold           and
        stc_val < 75
    )
    short_sig = (
        not np.isnan(peak)             and
        float(last["high"]) > peak     and
        float(last["close"]) > vwap_now and
        slope_now < -adaptive_slope     and
        is_dense                        and
        prob_bear > threshold           and
        stc_val > 25
    )

    return {
        # señales
        "long":          long_sig,
        "short":         short_sig,
        # precios
        "close":         float(last["close"]),
        "atr14":         atr14_now,
        "vwap":          vwap_now,
        "poc":           poc_level,
        "peak":          peak,
        "valley":        valley,
        # indicadores
        "slope":         slope_now,
        "adaptive_slope": adaptive_slope,
        "adx":           adx_val,
        "is_trending":   is_trending,
        "is_ranging":    is_ranging,
        "prob_bull":     prob_bull,
        "prob_bear":     prob_bear,
        "rvol":          rvol_now,
        "is_dense":      is_dense,
        "stc":           stc_val,
        "threshold":     threshold,
    }
