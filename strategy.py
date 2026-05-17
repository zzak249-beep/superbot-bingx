"""
strategy.py — V35 Golden Equilibrium
Indicadores implementados con numpy/pandas puro.
Sin pandas-ta, sin ta-lib. Funciona en Railway con pandas 2.x.
"""
import logging
import numpy as np
import pandas as pd

from config import (
    EMA_FAST, EMA_MID, EMA_SLOW,
    PIVOT_LEN, VOL_MULT, ADX_MIN, ATR_SL_MULT,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# Indicadores (numpy/pandas puro)
# ──────────────────────────────────────────────────────────

def _ema(series: pd.Series, n: int) -> pd.Series:
    """EMA — idéntico a ta.ema() de Pine Script."""
    return series.ewm(span=n, adjust=False).mean()


def _sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """ADX de Wilder — replica ta.dmi() de Pine Script."""
    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    up_move   = high - prev_high
    down_move = prev_low - low

    plus_dm  = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=high.index, dtype=float)
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index, dtype=float)

    atr_s      = tr.ewm(span=n, adjust=False).mean()
    plus_dm_s  = plus_dm.ewm(span=n, adjust=False).mean()
    minus_dm_s = minus_dm.ewm(span=n, adjust=False).mean()

    plus_di  = 100 * plus_dm_s  / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm_s / atr_s.replace(0, np.nan)

    dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(span=n, adjust=False).mean()


def _pivot_high(high: pd.Series, n: int) -> pd.Series:
    """ta.pivothigh(high, n, n) de Pine Script."""
    result = pd.Series(np.nan, index=high.index, dtype=float)
    arr = high.to_numpy()
    for i in range(n, len(arr) - n):
        window = arr[i - n: i + n + 1]
        if arr[i] == window.max() and list(window).count(arr[i]) == 1:
            result.iloc[i] = arr[i]
    return result


def _pivot_low(low: pd.Series, n: int) -> pd.Series:
    """ta.pivotlow(low, n, n) de Pine Script."""
    result = pd.Series(np.nan, index=low.index, dtype=float)
    arr = low.to_numpy()
    for i in range(n, len(arr) - n):
        window = arr[i - n: i + n + 1]
        if arr[i] == window.min() and list(window).count(arr[i]) == 1:
            result.iloc[i] = arr[i]
    return result


# ──────────────────────────────────────────────────────────
# Estrategia V35
# ──────────────────────────────────────────────────────────

class StrategyV35:
    """
    Port exacto del Pine Script V35: Golden Equilibrium.

    LONG:  crossover(EMA7, EMA17) AND low < valley AND vol > 1.5x AND ADX > 20
    SHORT: crossunder(EMA7, EMA17) AND high > peak  AND vol > 1.5x AND ADX > 20
    SL  :  valley − ATR×0.5 (long)  |  peak + ATR×0.5 (short)
    TP  :  EMA 21
    """

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["ema7"]  = _ema(df["close"], EMA_FAST)
        df["ema17"] = _ema(df["close"], EMA_MID)
        df["ema21"] = _ema(df["close"], EMA_SLOW)
        df["vol_ma"]      = _sma(df["volume"], 20)
        df["is_inst_vol"] = df["volume"] > (df["vol_ma"] * VOL_MULT)
        df["adx"] = _adx(df["high"], df["low"], df["close"], 14)
        df["atr"] = _atr(df["high"], df["low"], df["close"], 14)
        df["peak"]   = _pivot_high(df["high"], PIVOT_LEN).ffill()
        df["valley"] = _pivot_low( df["low"],  PIVOT_LEN).ffill()
        return df

    def get_signal(self, df: pd.DataFrame, adx_override: float = None) -> dict:
        NONE = {"signal": "NONE"}
        if len(df) < 60:
            return NONE

        df   = self._add_indicators(df)
        last = df.iloc[-1]
        prev = df.iloc[-2]

        for col in ["ema7", "ema17", "ema21", "adx", "atr", "peak", "valley", "vol_ma"]:
            if pd.isna(last[col]) or pd.isna(prev[col]):
                return NONE

        adx_min = adx_override if adx_override else ADX_MIN
        if not bool(last["is_inst_vol"]):
            return NONE
        if float(last["adx"]) <= adx_min:
            return NONE

        cross_up   = float(prev["ema7"]) <= float(prev["ema17"]) and \
                     float(last["ema7"])  >  float(last["ema17"])
        cross_down = float(prev["ema7"]) >= float(prev["ema17"]) and \
                     float(last["ema7"])  <  float(last["ema17"])

        if cross_up   and float(last["low"])  < float(last["valley"]):
            signal = "LONG"
        elif cross_down and float(last["high"]) > float(last["peak"]):
            signal = "SHORT"
        else:
            return NONE

        entry = float(last["close"])
        atr   = float(last["atr"])
        sl    = float(last["valley"]) - atr * ATR_SL_MULT if signal == "LONG" \
                else float(last["peak"]) + atr * ATR_SL_MULT
        tp    = float(last["ema21"])

        if signal == "LONG"  and (sl >= entry or tp <= entry): return NONE
        if signal == "SHORT" and (sl <= entry or tp >= entry): return NONE

        vol_ratio = float(last["volume"]) / float(last["vol_ma"]) \
                    if float(last["vol_ma"]) > 0 else 0.0

        return {
            "signal":    signal,
            "entry":     round(entry, 8),
            "sl":        round(sl, 8),
            "tp":        round(tp, 8),
            "atr":       round(atr, 8),
            "adx":       round(float(last["adx"]), 2),
            "strength":  self._strength(last),
            "peak":      round(float(last["peak"]),   8),
            "valley":    round(float(last["valley"]), 8),
            "vol_ratio": round(vol_ratio, 2),
        }

    @staticmethod
    def _strength(row) -> float:
        score = 0.0
        score += min(40.0, (float(row["adx"]) / 50.0) * 40.0)
        if float(row["vol_ma"]) > 0:
            score += min(30.0, (float(row["volume"]) / float(row["vol_ma"]) / 3.0) * 30.0)
        e7, e17, e21 = float(row["ema7"]), float(row["ema17"]), float(row["ema21"])
        if (e7 > e17 > e21) or (e7 < e17 < e21):
            score += 30.0
        elif e7 != e17:
            score += 15.0
        return round(score, 1)
