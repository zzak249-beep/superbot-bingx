"""
Strategy: VWAP Volatility Bands [BOSWaves] + Sniper Entry [KhanSaab V.02]
Fiel replicación de ambos Pine Scripts con confirmación dual obligatoria.

Lógica:
  BOSWaves  → T3-VWAP direction change = señal principal
  KhanSaab  → EMA9/21 crossover + 7-condition score = filtro de calidad
  Ambos deben alinearse para abrir trade.
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Literal

# ─────────────────────────────────────────────────────────────────────
#  Parámetros (idénticos a Pine Script)
# ─────────────────────────────────────────────────────────────────────
T3_LEN        = 28
T3_FACTOR     = 0.7
ATR_LEN       = 14
EMA_FAST      = 9
EMA_SLOW      = 21
RSI_LEN       = 14
MACD_FAST     = 12
MACD_SLOW     = 26
MACD_SIG      = 9
ADX_LEN       = 14
VOL_MA_LEN    = 20
SL_ATR_MULT   = 1.5     # KhanSaab default
BAND_M        = [0.5, 1.0, 1.5, 2.2]

MIN_SCORE     = 5        # mínimo de 7 condiciones Sniper
MIN_ADX       = 25
MIN_VOL_RATIO = 1.0      # volumen >= media


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
    adx:       float = 0.0
    bull_pct:  float = 0.0
    bear_pct:  float = 0.0


# ─────────────────────────────────────────────────────────────────────
#  Funciones matemáticas
# ─────────────────────────────────────────────────────────────────────
def _ema(series: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(series, np.nan, dtype=float)
    k = 2.0 / (period + 1)
    for i in range(len(series)):
        if np.isnan(series[i]):
            continue
        if i == 0 or np.isnan(out[i - 1]):
            out[i] = series[i]
        else:
            out[i] = series[i] * k + out[i - 1] * (1 - k)
    return out


def _t3(series: np.ndarray, length: int, factor: float) -> np.ndarray:
    """T3 de Tillson – exactamente como Pine Script f_t3()"""
    a  = factor
    c1 = -(a ** 3)
    c2 = 3 * a**2 + 3 * a**3
    c3 = -6 * a**2 - 3 * a - 3 * a**3
    c4 = 1 + 3 * a + a**3 + 3 * a**2
    e1 = _ema(series, length)
    e2 = _ema(e1, length)
    e3 = _ema(e2, length)
    e4 = _ema(e3, length)
    e5 = _ema(e4, length)
    e6 = _ema(e5, length)
    return c1*e6 + c2*e5 + c3*e4 + c4*e3


def _rsi(close: np.ndarray, period: int) -> np.ndarray:
    delta = np.diff(close, prepend=np.nan)
    gain  = np.where(delta > 0, delta, 0.0)
    loss  = np.where(delta < 0, -delta, 0.0)
    ag    = _ema(gain, period)
    al    = _ema(loss, period)
    rs    = np.where(al == 0, 100.0, ag / al)
    return 100 - (100 / (1 + rs))


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    pc = np.roll(close, 1); pc[0] = close[0]
    tr = np.maximum(high - low,
         np.maximum(np.abs(high - pc), np.abs(low - pc)))
    return _ema(tr, period)


def _macd(close: np.ndarray):
    fast = _ema(close, MACD_FAST)
    slow = _ema(close, MACD_SLOW)
    m    = fast - slow
    sig  = _ema(m, MACD_SIG)
    return m, sig


def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    ph = np.roll(high, 1);  ph[0] = high[0]
    pl = np.roll(low, 1);   pl[0] = low[0]
    pc = np.roll(close, 1); pc[0] = close[0]
    tr    = np.maximum(high - low, np.maximum(np.abs(high - pc), np.abs(low - pc)))
    dm_p  = np.where((high - ph) > (pl - low), np.maximum(high - ph, 0), 0.0)
    dm_m  = np.where((pl - low)  > (high - ph), np.maximum(pl - low, 0), 0.0)
    atr14 = _ema(tr, period)
    safe  = np.where(atr14 == 0, 1e-10, atr14)
    di_p  = 100 * _ema(dm_p, period) / safe
    di_m  = 100 * _ema(dm_m, period) / safe
    dsum  = np.where(di_p + di_m == 0, 1e-10, di_p + di_m)
    dx    = 100 * np.abs(di_p - di_m) / dsum
    return _ema(dx, period)


def _session_vwap(hlc3: np.ndarray, volume: np.ndarray,
                   timestamps: np.ndarray) -> np.ndarray:
    """
    VWAP con reset de sesión (diario) – replica Pine Script BOSWaves.
    Detecta cambio de día en timestamps (ms epoch).
    """
    vwap = np.full_like(hlc3, np.nan)
    cum_vol = 0.0
    cum_tpv = 0.0
    prev_day = -1

    for i in range(len(hlc3)):
        day = int(timestamps[i] // 86_400_000)   # ms → día
        if day != prev_day:                        # nuevo día = reset
            cum_vol = 0.0
            cum_tpv = 0.0
            prev_day = day
        cum_vol += volume[i]
        cum_tpv += hlc3[i] * volume[i]
        vwap[i] = cum_tpv / cum_vol if cum_vol > 0 else hlc3[i]
    return vwap


def _crossover(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """True en posición i si a cruza sobre b (a[i]>b[i] y a[i-1]<=b[i-1])"""
    cross = np.zeros(len(a), dtype=bool)
    cross[1:] = (a[1:] > b[1:]) & (a[:-1] <= b[:-1])
    return cross


def _crossunder(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    cross = np.zeros(len(a), dtype=bool)
    cross[1:] = (a[1:] < b[1:]) & (a[:-1] >= b[:-1])
    return cross


# ─────────────────────────────────────────────────────────────────────
#  Función principal
# ─────────────────────────────────────────────────────────────────────
def compute_signal(candles: list[dict], htf_rsi: float = 50.0) -> Signal:
    """
    candles: lista de {ts, open, high, low, close, volume}  (oldest → newest)
    htf_rsi: RSI de timeframe superior (1h cuando operamos en 15m)
    """
    min_bars = max(T3_LEN * 6, ATR_LEN, ADX_LEN, MACD_SLOW + MACD_SIG, VOL_MA_LEN) + 20
    if len(candles) < min_bars:
        return Signal("NONE", 0, 0, 0, 0, 0, 0, 0, 0, 0, "Not enough bars")

    df   = pd.DataFrame(candles)
    ts   = df["ts"].values.astype(float)
    o    = df["open"].values.astype(float)
    h    = df["high"].values.astype(float)
    l    = df["low"].values.astype(float)
    c    = df["close"].values.astype(float)
    v    = df["volume"].values.astype(float)
    hlc3 = (h + l + c) / 3.0

    # ── BOSWaves: VWAP con reset diario → T3 ──────────────────────────
    raw_vwap = _session_vwap(hlc3, v, ts)
    t3_arr   = _t3(raw_vwap, T3_LEN, T3_FACTOR)

    # ── Indicadores KhanSaab ──────────────────────────────────────────
    atr_arr   = _atr(h, l, c, ATR_LEN)
    ema9_arr  = _ema(c, EMA_FAST)
    ema21_arr = _ema(c, EMA_SLOW)
    rsi_arr   = _rsi(c, RSI_LEN)
    macd_arr, sig_arr = _macd(c)
    adx_arr   = _adx(h, l, c, ADX_LEN)
    vol_ma    = pd.Series(v).rolling(VOL_MA_LEN).mean().values

    # ── Crossovers (arrays) ───────────────────────────────────────────
    # BOSWaves: señal primaria = T3 cambia dirección
    t3_cross_up   = _crossover(t3_arr, np.roll(t3_arr, 1))
    t3_cross_down = _crossunder(t3_arr, np.roll(t3_arr, 1))

    # KhanSaab: trigger = EMA9/21 crossover
    ema_cross_up   = _crossover(ema9_arr, ema21_arr)
    ema_cross_down = _crossunder(ema9_arr, ema21_arr)

    # ── Últimos valores ───────────────────────────────────────────────
    i = -1
    t3_v  = t3_arr[i];   t3_p  = t3_arr[i-1]
    atr   = float(atr_arr[i])
    e9    = float(ema9_arr[i]);   e9p  = float(ema9_arr[i-1])
    e21   = float(ema21_arr[i]);  e21p = float(ema21_arr[i-1])
    rsi   = float(rsi_arr[i])
    macd  = float(macd_arr[i]);   sig  = float(sig_arr[i])
    adx   = float(adx_arr[i])
    vm    = float(vol_ma[i]) if not np.isnan(vol_ma[i]) else 0.0
    vwap  = float(raw_vwap[i])
    price = float(c[i])
    vol   = float(v[i])
    open_ = float(o[i])

    # Comprobamos NaN
    if any(np.isnan(x) for x in [t3_v, t3_p, atr, e9, e21, adx]):
        return Signal("NONE", price, 0, 0, 0, 0, 0, 0, 0, atr, "NaN in indicators")

    # ── BOSWaves Score (dirección T3) ─────────────────────────────────
    t3_bullish = t3_v > t3_p
    t3_bearish = t3_v < t3_p

    # BOSWaves: señal en las últimas 3 velas (crossover reciente)
    bw_long_recent  = any(t3_cross_up[-3:])
    bw_short_recent = any(t3_cross_down[-3:])

    # EMA crossover reciente (últimas 3 velas)
    ema_long_recent  = any(ema_cross_up[-3:])
    ema_short_recent = any(ema_cross_down[-3:])

    # ── KhanSaab: 7-condition Bull/Bear score ─────────────────────────
    bull_score = sum([
        price > vwap,                            # Price/VWAP
        rsi > 50,                                # RSI
        macd > sig,                              # MACD
        e9 > e21,                                # EMA alignment
        adx > MIN_ADX and price > e9,            # ADX Power + price above EMA9
        vol > vm and open_ < price,              # Volume + green candle
        htf_rsi > 50,                            # Higher TF RSI (5m/1h)
    ])
    bear_score = sum([
        price < vwap,
        rsi < 50,
        macd < sig,
        e9 < e21,
        adx > MIN_ADX and price < e9,
        vol > vm and open_ > price,              # red candle
        htf_rsi < 50,
    ])

    bull_pct = (bull_score / 7) * 100
    bear_pct = (bear_score / 7) * 100

    # ── BOSWaves: banda 4 (no entrar si precio sobreextendido) ────────
    band_l4 = t3_v - atr * BAND_M[3]
    band_u4 = t3_v + atr * BAND_M[3]
    not_over_bull = price > band_l4
    not_over_bear = price < band_u4

    # ── Riesgo (KhanSaab usa ATR × 1.5) ──────────────────────────────
    risk = atr * SL_ATR_MULT

    # ── Condiciones de entrada (DUAL CONFIRMATION) ────────────────────
    # LONG: BOSWaves T3 sube + KhanSaab EMA cross up + score + ADX
    long_ok = (
        (bw_long_recent or t3_bullish) and   # BOSWaves confirma
        ema_long_recent and                   # KhanSaab trigger
        bull_score >= MIN_SCORE and           # score mínimo
        not_over_bull and                     # precio no sobreextendido
        adx > MIN_ADX                         # tendencia fuerte
    )

    # SHORT: BOSWaves T3 baja + KhanSaab EMA cross down + score + ADX
    short_ok = (
        (bw_short_recent or t3_bearish) and
        ema_short_recent and
        bear_score >= MIN_SCORE and
        not_over_bear and
        adx > MIN_ADX
    )

    if long_ok:
        sl  = price - risk
        tp1 = price + risk * 1
        tp2 = price + risk * 2
        tp3 = price + risk * 3
        tp4 = price + risk * 4
        tp5 = price + risk * 5
        reason = (f"BOSWaves T3↑ + EMA cross UP | "
                  f"Bull {bull_pct:.0f}% ({bull_score}/7) | ADX {adx:.1f} | "
                  f"HTF RSI {htf_rsi:.1f}")
        return Signal("LONG", price, sl, tp1, tp2, tp3, tp4, tp5,
                      bull_pct, atr, reason, adx, bull_pct, bear_pct)

    if short_ok:
        sl  = price + risk
        tp1 = price - risk * 1
        tp2 = price - risk * 2
        tp3 = price - risk * 3
        tp4 = price - risk * 4
        tp5 = price - risk * 5
        reason = (f"BOSWaves T3↓ + EMA cross DOWN | "
                  f"Bear {bear_pct:.0f}% ({bear_score}/7) | ADX {adx:.1f} | "
                  f"HTF RSI {htf_rsi:.1f}")
        return Signal("SHORT", price, sl, tp1, tp2, tp3, tp4, tp5,
                      bear_pct, atr, reason, adx, bull_pct, bear_pct)

    dominant = max(bull_pct, bear_pct)
    return Signal(
        "NONE", price, 0, 0, 0, 0, 0, 0, dominant, atr,
        f"No trigger | Bull {bull_pct:.0f}% Bear {bear_pct:.0f}% | ADX {adx:.1f}",
        adx, bull_pct, bear_pct,
    )
