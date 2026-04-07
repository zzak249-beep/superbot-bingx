"""
Strategy: VWAP Volatility Bands + Sniper Entry
Exact Python replication of both Pine Script indicators:
  1. VWAP Volatility Bands [BOSWaves]  → trend direction + band context
  2. Sniper Entry/Exit [KhanSaab V.02]  → entry trigger + score filter
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Literal

# ─────────────────────────────────────────────
#  Params (mirrors Pine Script inputs)
# ─────────────────────────────────────────────
T3_LEN       = 28
T3_FACTOR    = 0.7
ATR_LEN      = 14
EMA_FAST     = 9
EMA_SLOW     = 21
RSI_LEN      = 14
MACD_FAST    = 12
MACD_SLOW    = 26
MACD_SIG     = 9
ADX_LEN      = 14
VOL_MA_LEN   = 20
SL_ATR_MULT  = 1.5
BAND_M       = [0.5, 1.0, 1.5, 2.2]   # band multipliers

MIN_SCORE    = 5          # out of 7 to open trade
MIN_ADX      = 25
MIN_VOLUME_X = 1.0        # volume must be >= X * vol_avg


@dataclass
class Signal:
    direction: Literal["LONG", "SHORT", "NONE"]
    entry:     float
    sl:        float
    tp1:       float
    tp2:       float
    tp3:       float
    tp4:       float
    tp5:       float
    score:     float       # 0-100
    atr:       float
    reason:    str


# ─────────────────────────────────────────────
#  Math helpers
# ─────────────────────────────────────────────
def _ema(series: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(series, np.nan)
    k = 2.0 / (period + 1)
    for i in range(len(series)):
        if np.isnan(series[i]):
            continue
        if np.isnan(out[i - 1]):
            out[i] = series[i]
        else:
            out[i] = series[i] * k + out[i - 1] * (1 - k)
    return out


def _t3(series: np.ndarray, length: int, factor: float) -> np.ndarray:
    a  = factor
    c1 = -a**3
    c2 = 3*a**2 + 3*a**3
    c3 = -6*a**2 - 3*a - 3*a**3
    c4 = 1 + 3*a + a**3 + 3*a**2
    e1 = _ema(series, length)
    e2 = _ema(e1, length)
    e3 = _ema(e2, length)
    e4 = _ema(e3, length)
    e5 = _ema(e4, length)
    e6 = _ema(e5, length)
    return c1*e6 + c2*e5 + c3*e4 + c4*e3


def _rsi(close: np.ndarray, period: int) -> np.ndarray:
    delta  = np.diff(close, prepend=np.nan)
    gain   = np.where(delta > 0, delta, 0.0)
    loss   = np.where(delta < 0, -delta, 0.0)
    avg_g  = _ema(gain, period)
    avg_l  = _ema(loss, period)
    rs     = np.where(avg_l == 0, 100, avg_g / avg_l)
    return 100 - (100 / (1 + rs))


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low,
         np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    return _ema(tr, period)


def _macd(close: np.ndarray):
    fast = _ema(close, MACD_FAST)
    slow = _ema(close, MACD_SLOW)
    m    = fast - slow
    sig  = _ema(m, MACD_SIG)
    return m, sig


def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    prev_h = np.roll(high, 1); prev_h[0] = high[0]
    prev_l = np.roll(low, 1);  prev_l[0] = low[0]
    prev_c = np.roll(close, 1); prev_c[0] = close[0]
    tr  = np.maximum(high - low,
          np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
    dm_p = np.where((high - prev_h) > (prev_l - low), np.maximum(high - prev_h, 0), 0)
    dm_m = np.where((prev_l - low) > (high - prev_h), np.maximum(prev_l - low, 0), 0)
    atr14 = _ema(tr, period)
    di_p  = 100 * _ema(dm_p, period) / np.where(atr14 == 0, 1e-10, atr14)
    di_m  = 100 * _ema(dm_m, period) / np.where(atr14 == 0, 1e-10, atr14)
    dx    = 100 * np.abs(di_p - di_m) / np.where(di_p + di_m == 0, 1e-10, di_p + di_m)
    return _ema(dx, period)


def _vwap_session(hlc3: np.ndarray, volume: np.ndarray) -> np.ndarray:
    """Session VWAP (simple cumulative for single-session data)."""
    cum_vol  = np.cumsum(volume)
    cum_tpv  = np.cumsum(hlc3 * volume)
    return cum_tpv / np.where(cum_vol == 0, 1e-10, cum_vol)


# ─────────────────────────────────────────────
#  Main strategy function
# ─────────────────────────────────────────────
def compute_signal(candles: list[dict], htf_rsi: float = 50.0) -> Signal:
    """
    candles: list of {ts, open, high, low, close, volume}  (oldest → newest)
    htf_rsi: RSI from higher timeframe (5m RSI if on 1m, etc.)
    Returns Signal with direction, entry, sl, tp levels.
    """
    if len(candles) < max(T3_LEN * 6, ATR_LEN, ADX_LEN, MACD_SLOW + MACD_SIG) + 10:
        return Signal("NONE", 0, 0, 0, 0, 0, 0, 0, 0, 0, "Not enough bars")

    df = pd.DataFrame(candles)
    o  = df["open"].values.astype(float)
    h  = df["high"].values.astype(float)
    l  = df["low"].values.astype(float)
    c  = df["close"].values.astype(float)
    v  = df["volume"].values.astype(float)
    hlc3 = (h + l + c) / 3.0

    # ── Indicators ──────────────────────────────────────────
    raw_vwap  = _vwap_session(hlc3, v)
    t3        = _t3(raw_vwap, T3_LEN, T3_FACTOR)
    atr_arr   = _atr(h, l, c, ATR_LEN)
    ema9_arr  = _ema(c, EMA_FAST)
    ema21_arr = _ema(c, EMA_SLOW)
    rsi_arr   = _rsi(c, RSI_LEN)
    macd_arr, sig_arr = _macd(c)
    adx_arr   = _adx(h, l, c, ADX_LEN)
    vol_ma    = pd.Series(v).rolling(VOL_MA_LEN).mean().values

    # ── Last bar values ──────────────────────────────────────
    i    = -1
    t3_v = t3[i];    t3_p = t3[i-1]
    atr  = atr_arr[i]
    e9   = ema9_arr[i];   e9p = ema9_arr[i-1]
    e21  = ema21_arr[i];  e21p = ema21_arr[i-1]
    rsi  = rsi_arr[i]
    macd = macd_arr[i];  sig = sig_arr[i]
    adx  = adx_arr[i]
    vm   = vol_ma[i]
    vwap = raw_vwap[i]
    price = c[i]

    # ── Trend direction (BOSWaves score) ──────────────────────
    t3_bullish  = t3_v > t3_p    # T3 rising
    t3_bearish  = t3_v < t3_p

    # ── EMA crossover (Sniper trigger) ────────────────────────
    ema_cross_up   = (e9 > e21) and (e9p <= e21p)
    ema_cross_down = (e9 < e21) and (e9p >= e21p)

    # ── Bull score (Sniper, 7 conditions) ────────────────────
    bull_score = sum([
        price > vwap,
        rsi > 50,
        macd > sig,
        e9 > e21,
        adx > MIN_ADX and price > e9,
        v[i] > vm and c[i] > o[i],
        htf_rsi > 50,
    ])
    bear_score = sum([
        price < vwap,
        rsi < 50,
        macd < sig,
        e9 < e21,
        adx > MIN_ADX and price < e9,
        v[i] > vm and c[i] < o[i],
        htf_rsi < 50,
    ])

    bull_pct = (bull_score / 7) * 100
    bear_pct = (bear_score / 7) * 100

    # ── VWAP band context ─────────────────────────────────────
    band_l4 = t3_v - atr * BAND_M[3]
    band_u4 = t3_v + atr * BAND_M[3]
    band_l3 = t3_v - atr * BAND_M[2]
    band_u3 = t3_v + atr * BAND_M[2]

    # Price not extended beyond band4 (overextended = bad entry)
    not_overextended_bull = price > band_l4
    not_overextended_bear = price < band_u4

    # ── Entry conditions ──────────────────────────────────────
    risk  = atr * SL_ATR_MULT

    long_ok  = (
        ema_cross_up and
        t3_bullish and
        bull_score >= MIN_SCORE and
        not_overextended_bull and
        adx > MIN_ADX
    )
    short_ok = (
        ema_cross_down and
        t3_bearish and
        bear_score >= MIN_SCORE and
        not_overextended_bear and
        adx > MIN_ADX
    )

    if long_ok:
        sl   = price - risk
        tp1  = price + risk * 1
        tp2  = price + risk * 2
        tp3  = price + risk * 3
        tp4  = price + risk * 4
        tp5  = price + risk * 5
        reason = f"EMA cross UP | T3↑ | Bull {bull_pct:.0f}% | ADX {adx:.1f}"
        return Signal("LONG",  price, sl, tp1, tp2, tp3, tp4, tp5, bull_pct, atr, reason)

    if short_ok:
        sl   = price + risk
        tp1  = price - risk * 1
        tp2  = price - risk * 2
        tp3  = price - risk * 3
        tp4  = price - risk * 4
        tp5  = price - risk * 5
        reason = f"EMA cross DOWN | T3↓ | Bear {bear_pct:.0f}% | ADX {adx:.1f}"
        return Signal("SHORT", price, sl, tp1, tp2, tp3, tp4, tp5, bear_pct, atr, reason)

    return Signal("NONE", price, 0, 0, 0, 0, 0, 0,
                  max(bull_pct, bear_pct), atr,
                  f"No trigger | Bull {bull_pct:.0f}% Bear {bear_pct:.0f}% ADX {adx:.1f}")
