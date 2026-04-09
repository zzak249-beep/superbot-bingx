"""
Strategy v4.0 – Professional Grade Engine
Integra:
  1. BOSWaves + KhanSaab + DLO (existentes)
  2. MFI (Money Flow Index) → filtro de volumen real
  3. Turtle Trade Channels → detección de breakouts
  4. Zero Lag SMA → filtro de tendencia sin lag
  
Jerarquía mejorada:
  Tier S: todos los indicadores alineados   → máxima confianza
  Tier A: BOSWaves + KhanSaab + MFI + ZLSMA → muy bueno
  Tier B: 2+ confirmaciones + Turtle         → aceptable
  Tier C: solo 1-2 confirmaciones            → SKIP
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Literal

# ─────────────────────────────────────────────────────────────────────
#  Parámetros base (heredados)
# ─────────────────────────────────────────────────────────────────────
T3_LEN        = 28
T3_FACTOR     = 0.7
BAND_M        = [0.5, 1.0, 1.5, 2.2]

EMA_FAST      = 9
EMA_SLOW      = 21
ATR_LEN       = 14
RSI_LEN       = 14
MACD_FAST     = 12
MACD_SLOW     = 26
MACD_SIG      = 9
ADX_LEN       = 14
VOL_MA_LEN    = 20
SL_ATR_MULT   = 1.5

DLO_DI_LEN    = 14
DLO_MEAN_LB   = 200
DLO_SLOPE     = 0.18
DLO_SMOOTH    = 3
DLO_OSC_SCALE = 2.5
DLO_OSC_SMOOTH = 7

# ─────────────────────────────────────────────────────────────────────
#  NUEVOS: MFI, Turtle Channels, Zero Lag SMA
# ─────────────────────────────────────────────────────────────────────
MFI_PERIOD       = 14         # período MFI estándar
MFI_OVERSOLD     = 30         # oversold
MFI_OVERBOUGHT   = 70         # overbought
MFI_NEUTRAL_HIGH = 55         # si >55 en LONG es bueno
MFI_NEUTRAL_LOW  = 45         # si <45 en SHORT es bueno

TURTLE_LEN       = 20         # período para Turtle Channels
TURTLE_ATR_MULT  = 2.0        # para los canales

ZLSMA_LEN        = 50         # Zero Lag SMA length
ZLSMA_LAG        = 0.3        # lag reduction factor (0.3 = muy responsivo)

MIN_SCORE        = 5          # mínimo condiciones KhanSaab
MIN_ADX          = 22
CROSS_WINDOW     = 6
BW_WINDOW        = 5


@dataclass
class Signal:
    direction:  Literal["LONG", "SHORT", "NONE"]
    entry:      float
    sl:         float
    tp1:        float
    tp2:        float
    tp3:        float
    tp4:        float
    tp5:        float
    score:      float
    atr:        float
    reason:     str
    adx:        float = 0.0
    bull_pct:   float = 0.0
    bear_pct:   float = 0.0
    dlo_value:  float = 0.0
    tier:       str   = ""
    mfi_value:  float = 0.0
    zlsma_conf: float = 0.0


# ─────────────────────────────────────────────────────────────────────
#  Matemáticas base
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


def _sma(series: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(series).rolling(period, min_periods=1).mean().values


def _t3(series: np.ndarray, length: int, factor: float) -> np.ndarray:
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


def _dmi(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int):
    """Devuelve (plus_di, minus_di, adx)."""
    ph = np.roll(high, 1);  ph[0] = high[0]
    pl = np.roll(low, 1);   pl[0] = low[0]
    pc = np.roll(close, 1); pc[0] = close[0]
    tr    = np.maximum(high - low, np.maximum(np.abs(high - pc), np.abs(low - pc)))
    dm_p  = np.where((high - ph) > (pl - low), np.maximum(high - ph, 0), 0.0)
    dm_m  = np.where((pl - low) > (high - ph), np.maximum(pl - low, 0), 0.0)
    atr14 = _ema(tr, period)
    safe  = np.where(atr14 == 0, 1e-10, atr14)
    di_p  = 100 * _ema(dm_p, period) / safe
    di_m  = 100 * _ema(dm_m, period) / safe
    dsum  = np.where(di_p + di_m == 0, 1e-10, di_p + di_m)
    dx    = 100 * np.abs(di_p - di_m) / dsum
    return di_p, di_m, _ema(dx, period)


def _session_vwap(hlc3: np.ndarray, volume: np.ndarray, timestamps: np.ndarray) -> np.ndarray:
    vwap = np.full_like(hlc3, np.nan)
    cum_vol = cum_tpv = 0.0
    prev_day = -1
    for i in range(len(hlc3)):
        day = int(timestamps[i] // 86_400_000)
        if day != prev_day:
            cum_vol = cum_tpv = 0.0
            prev_day = day
        cum_vol += volume[i]
        cum_tpv += hlc3[i] * volume[i]
        vwap[i] = cum_tpv / cum_vol if cum_vol > 0 else hlc3[i]
    return vwap


def _cross_up(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    c = np.zeros(len(a), dtype=bool)
    c[1:] = (a[1:] > b[1:]) & (a[:-1] <= b[:-1])
    return c


def _cross_dn(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    c = np.zeros(len(a), dtype=bool)
    c[1:] = (a[1:] < b[1:]) & (a[:-1] >= b[:-1])
    return c


# ─────────────────────────────────────────────────────────────────────
#  NUEVO: Money Flow Index (MFI)
# ─────────────────────────────────────────────────────────────────────
def _mfi(high: np.ndarray, low: np.ndarray, close: np.ndarray, 
         volume: np.ndarray, period: int) -> np.ndarray:
    """
    Money Flow Index: oscilador de volumen
    - > 70: overbought (posible reversión bajista)
    - < 30: oversold (posible reversión alcista)
    - alineado con dirección = confirmación fuerte
    """
    tp = (high + low + close) / 3.0  # Típical Price
    mf = tp * volume  # Money Flow
    
    positive_mf = np.where(tp > np.roll(tp, 1), mf, 0)
    negative_mf = np.where(tp < np.roll(tp, 1), mf, 0)
    
    positive_mf[0] = mf[0]  # primer valor
    
    pos_mf_sum = pd.Series(positive_mf).rolling(period, min_periods=1).sum().values
    neg_mf_sum = pd.Series(negative_mf).rolling(period, min_periods=1).sum().values
    
    mfr = np.where(neg_mf_sum == 0, 100.0, pos_mf_sum / neg_mf_sum)
    mfi = 100.0 - (100.0 / (1.0 + mfr))
    
    return np.nan_to_num(mfi, nan=50.0)


# ─────────────────────────────────────────────────────────────────────
#  NUEVO: Zero Lag SMA (ZLSMA) - más responsivo que SMA
# ─────────────────────────────────────────────────────────────────────
def _zero_lag_sma(series: np.ndarray, period: int, lag: float = 0.3) -> np.ndarray:
    """
    Zero Lag SMA: elimina el retardo de SMA
    Formula: ZLSMA = SMA + lag_factor * (SMA - SMA_delayed)
    
    lag = 0.3 → bastante responsivo
    lag = 0.0 → es solo un SMA normal
    """
    sma1 = _sma(series, period)
    sma2 = _sma(sma1, period)
    zlsma = sma1 + lag * (sma1 - sma2)
    return zlsma


# ─────────────────────────────────────────────────────────────────────
#  NUEVO: Turtle Trade Channels (breakout detection)
# ─────────────────────────────────────────────────────────────────────
def _turtle_channels(high: np.ndarray, low: np.ndarray, 
                     atr: np.ndarray, period: int, atr_mult: float):
    """
    Canales de Turtle Channels:
    - High: highest(high, period) + ATR * mult
    - Low:  lowest(low, period) - ATR * mult
    
    Si price > high → breakout alcista
    Si price < low  → breakout bajista
    """
    rolling_high = pd.Series(high).rolling(period, min_periods=1).max().values
    rolling_low  = pd.Series(low).rolling(period, min_periods=1).min().values
    
    ch_high = rolling_high + atr * atr_mult
    ch_low  = rolling_low - atr * atr_mult
    
    return ch_high, ch_low


# ─────────────────────────────────────────────────────────────────────
#  DLO (heredado)
# ─────────────────────────────────────────────────────────────────────
def _logistic_prob(series: np.ndarray, mean_lb: int, slope: float, smooth: int) -> np.ndarray:
    mean     = _sma(series, mean_lb)
    z        = np.clip((series - mean) * slope, -20, 20)
    prob_raw = 1.0 / (1.0 + np.exp(-z))
    return _ema(prob_raw, smooth)


def _compute_dlo(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    di_p, di_m, adx = _dmi(high, low, close, DLO_DI_LEN)
    prob_plus  = _logistic_prob(di_p, DLO_MEAN_LB, DLO_SLOPE, DLO_SMOOTH)
    prob_minus = _logistic_prob(di_m, DLO_MEAN_LB, DLO_SLOPE, DLO_SMOOTH)
    prob_adx   = _logistic_prob(adx,  DLO_MEAN_LB, DLO_SLOPE, DLO_SMOOTH)
    net_dir        = prob_plus - prob_minus
    strength_raw   = net_dir * prob_adx * DLO_OSC_SCALE
    strength_bound = np.tanh(np.clip(strength_raw, -20, 20))
    strength       = _ema(strength_bound, DLO_SMOOTH)
    return _ema(strength, DLO_OSC_SMOOTH)


# ─────────────────────────────────────────────────────────────────────
#  Función principal v4.0
# ─────────────────────────────────────────────────────────────────────
def compute_signal(candles: list[dict], htf_rsi: float = 50.0) -> Signal:
    min_bars = max(T3_LEN * 6, DLO_MEAN_LB, MACD_SLOW + MACD_SIG, 
                   VOL_MA_LEN, MFI_PERIOD, ZLSMA_LEN, TURTLE_LEN) + 30
    if len(candles) < min_bars:
        return Signal("NONE", 0, 0, 0, 0, 0, 0, 0, 0, 0, "Not enough bars",
                     0, 0, 0, 0, "", 0, 0)

    df   = pd.DataFrame(candles)
    ts   = df["ts"].values.astype(float)
    o    = df["open"].values.astype(float)
    h    = df["high"].values.astype(float)
    l    = df["low"].values.astype(float)
    c    = df["close"].values.astype(float)
    v    = df["volume"].values.astype(float)
    hlc3 = (h + l + c) / 3.0

    # ── Indicadores base (heredados) ───────────────────────────────────
    raw_vwap = _session_vwap(hlc3, v, ts)
    t3_arr   = _t3(raw_vwap, T3_LEN, T3_FACTOR)
    atr_arr  = _atr(h, l, c, ATR_LEN)
    atr_v    = float(atr_arr[-1])
    t3_v     = float(t3_arr[-1])
    t3_p     = float(t3_arr[-2])

    band_l3  = t3_v - atr_v * BAND_M[2]
    band_l4  = t3_v - atr_v * BAND_M[3]
    band_u3  = t3_v + atr_v * BAND_M[2]
    band_u4  = t3_v + atr_v * BAND_M[3]

    bw_t3_up   = _cross_up(t3_arr, np.roll(t3_arr, 1))
    bw_t3_down = _cross_dn(t3_arr, np.roll(t3_arr, 1))
    bw_long_recent  = any(bw_t3_up[-BW_WINDOW:])
    bw_short_recent = any(bw_t3_down[-BW_WINDOW:])
    t3_bullish = t3_v > t3_p
    t3_bearish = t3_v < t3_p

    price = float(c[-1])
    bw_bounce_long  = float(l[-2]) <= band_l3 and price > band_l3 and t3_bullish
    bw_bounce_short = float(h[-2]) >= band_u3 and price < band_u3 and t3_bearish

    # ── KhanSaab ──────────────────────────────────────────────────────
    ema9  = _ema(c, EMA_FAST)
    ema21 = _ema(c, EMA_SLOW)
    rsi_a = _rsi(c, RSI_LEN)
    macd_a, sig_a = _macd(c)
    _, _, adx_a = _dmi(h, l, c, ADX_LEN)
    vol_ma = _sma(v, VOL_MA_LEN)

    ema_cross_up = _cross_up(ema9, ema21)
    ema_cross_dn = _cross_dn(ema9, ema21)

    e9    = float(ema9[-1])
    e21   = float(ema21[-1])
    rsi   = float(rsi_a[-1])
    macd  = float(macd_a[-1])
    sig   = float(sig_a[-1])
    adx   = float(adx_a[-1])
    vm    = float(vol_ma[-1]) if not np.isnan(vol_ma[-1]) else 0.0
    vwap  = float(raw_vwap[-1])
    vol   = float(v[-1])
    open_ = float(o[-1])

    ema_long_recent  = (
        any(ema_cross_up[-CROSS_WINDOW:]) or
        (e9 > e21 and (e9 - e21) > abs(e21 * 0.001))
    )
    ema_short_recent = (
        any(ema_cross_dn[-CROSS_WINDOW:]) or
        (e9 < e21 and (e21 - e9) > abs(e21 * 0.001))
    )

    bull_score = sum([
        price > vwap,
        rsi > 50,
        macd > sig,
        e9 > e21,
        adx > MIN_ADX and price > e9,
        vol > vm and open_ < price,
        htf_rsi > 50,
    ])
    bear_score = sum([
        price < vwap,
        rsi < 50,
        macd < sig,
        e9 < e21,
        adx > MIN_ADX and price < e9,
        vol > vm and open_ > price,
        htf_rsi < 50,
    ])
    bull_pct = bull_score / 7 * 100
    bear_pct = bear_score / 7 * 100

    # ── DLO ────────────────────────────────────────────────────────────
    dlo_arr = _compute_dlo(h, l, c)
    dlo_val = float(dlo_arr[-1]) if not np.isnan(dlo_arr[-1]) else 0.0
    dlo_strong_bull = dlo_val >  0.15
    dlo_strong_bear = dlo_val < -0.15
    dlo_bull        = dlo_val >  0.0
    dlo_bear        = dlo_val <  0.0

    # ── NUEVO: MFI (Money Flow Index) ──────────────────────────────────
    mfi_arr = _mfi(h, l, c, v, MFI_PERIOD)
    mfi_val = float(mfi_arr[-1])
    
    # Confirmaciones MFI
    mfi_oversold  = mfi_val < MFI_OVERSOLD        # oversold → posible reversión alcista
    mfi_overbought = mfi_val > MFI_OVERBOUGHT     # overbought → posible reversión bajista
    mfi_long_confirm = mfi_val < MFI_NEUTRAL_HIGH # para LONG, queremos MFI bajo-medio
    mfi_short_confirm = mfi_val > MFI_NEUTRAL_LOW # para SHORT, queremos MFI alto-medio

    # ── NUEVO: Zero Lag SMA ────────────────────────────────────────────
    zlsma_arr = _zero_lag_sma(c, ZLSMA_LEN, ZLSMA_LAG)
    zlsma_v = float(zlsma_arr[-1])
    zlsma_p = float(zlsma_arr[-2]) if len(zlsma_arr) > 1 else zlsma_v
    
    # Para LONG: precio > ZLSMA (uptrend)
    zlsma_long_trend = price > zlsma_v
    # Para SHORT: precio < ZLSMA (downtrend)
    zlsma_short_trend = price < zlsma_v
    
    # Fortaleza de la confirmación ZLSMA (% distancia)
    zlsma_long_strength = (price - zlsma_v) / max(zlsma_v, 0.0001) * 100
    zlsma_short_strength = (zlsma_v - price) / max(zlsma_v, 0.0001) * 100
    zlsma_conf = zlsma_long_strength if zlsma_long_trend else zlsma_short_strength

    # ── NUEVO: Turtle Channels (Breakout detection) ─────────────────────
    ch_high, ch_low = _turtle_channels(h, l, atr_arr, TURTLE_LEN, TURTLE_ATR_MULT)
    ch_high_v = float(ch_high[-1])
    ch_low_v = float(ch_low[-1])
    
    turtle_breakout_long = price > ch_high_v   # precio salió por arriba del canal
    turtle_breakout_short = price < ch_low_v   # precio salió por abajo del canal
    
    # ──────────────────────────────────────────────────────────────────────
    #  DLO reversión (heredado)
    # ──────────────────────────────────────────────────────────────────────
    if len(dlo_arr) >= 3:
        dlo_rev_up = (dlo_arr[-1] > dlo_arr[-2]) and not (dlo_arr[-2] > dlo_arr[-3])
        dlo_rev_dn = (dlo_arr[-1] < dlo_arr[-2]) and not (dlo_arr[-2] < dlo_arr[-3])
    else:
        dlo_rev_up = dlo_rev_dn = False

    # NaN guard
    if any(np.isnan(x) for x in [t3_v, t3_p, atr_v, e9, e21, adx]):
        return Signal("NONE", price, 0, 0, 0, 0, 0, 0, 0, atr_v, "NaN",
                     adx, bull_pct, bear_pct, dlo_val, "", mfi_val, zlsma_conf)

    not_over_bull = price > band_l4
    not_over_bear = price < band_u4
    risk = atr_v * SL_ATR_MULT

    # ──────────────────────────────────────────────────────────────────────
    #  Nuevas condiciones de entrada con v4.0
    # ──────────────────────────────────────────────────────────────────────
    
    # BOSWaves base
    bw_l = bw_long_recent or bw_bounce_long or t3_bullish
    bw_s = bw_short_recent or bw_bounce_short or t3_bearish
    
    # KhanSaab base
    kh_l = ema_long_recent and bull_score >= MIN_SCORE
    kh_s = ema_short_recent and bear_score >= MIN_SCORE
    
    # LONG condiciones
    # Tier S: BOSWaves + KhanSaab + MFI + ZLSMA + Turtle + DLO fuerte
    long_s = (
        bw_l and kh_l and mfi_long_confirm and zlsma_long_trend and 
        turtle_breakout_long and dlo_strong_bull and adx > MIN_ADX and not_over_bull
    )
    
    # Tier A: BOSWaves + KhanSaab + MFI + ZLSMA + DLO (sin turtle requerido)
    long_a = (
        (bw_long_recent or bw_bounce_long) and kh_l and mfi_long_confirm and 
        zlsma_long_trend and (not dlo_bear) and adx > MIN_ADX and not_over_bull and not long_s
    )
    
    # Tier B: BOSWaves + KhanSaab + ZLSMA + Turtle
    long_b = (
        bw_l and kh_l and zlsma_long_trend and turtle_breakout_long and
        adx > MIN_ADX and not_over_bull and not long_s and not long_a
    )
    
    # SHORT condiciones
    # Tier S
    short_s = (
        bw_s and kh_s and mfi_short_confirm and zlsma_short_trend and 
        turtle_breakout_short and dlo_strong_bear and adx > MIN_ADX and not_over_bear
    )
    
    # Tier A
    short_a = (
        (bw_short_recent or bw_bounce_short) and kh_s and mfi_short_confirm and 
        zlsma_short_trend and (not dlo_bull) and adx > MIN_ADX and not_over_bear and not short_s
    )
    
    # Tier B
    short_b = (
        bw_s and kh_s and zlsma_short_trend and turtle_breakout_short and
        adx > MIN_ADX and not_over_bear and not short_s and not short_a
    )

    def _long_score():
        s = bull_pct
        if dlo_strong_bull: s += 25
        elif dlo_bull:       s += 12
        if bw_bounce_long:   s += 15
        if bw_long_recent:   s += 10
        if mfi_long_confirm: s += 15  # MFI ayuda
        if zlsma_long_trend: s += 15  # ZLSMA ayuda
        if turtle_breakout_long: s += 12  # Turtle breakout
        if dlo_rev_up:       s += 8
        if adx > 35:         s += 10
        return min(s, 100.0)

    def _short_score():
        s = bear_pct
        if dlo_strong_bear:  s += 25
        elif dlo_bear:       s += 12
        if bw_bounce_short:  s += 15
        if bw_short_recent:  s += 10
        if mfi_short_confirm: s += 15
        if zlsma_short_trend: s += 15
        if turtle_breakout_short: s += 12
        if dlo_rev_dn:       s += 8
        if adx > 35:         s += 10
        return min(s, 100.0)

    if long_s or long_a or long_b:
        if long_s:
            tier = "S"
            score_mult = 1.15
        elif long_a:
            tier = "A"
            score_mult = 1.0
        else:
            tier = "B"
            score_mult = 0.85
        
        score = _long_score() * score_mult
        sl, tp1, tp2, tp3, tp4, tp5 = (
            price - risk,
            price + risk, price + risk*2, price + risk*3,
            price + risk*4, price + risk*5,
        )
        
        bw_lbl = "Bounce" if bw_bounce_long else ("T3-Cross" if bw_long_recent else "T3↑")
        reason = (
            f"[{tier}] BW-{bw_lbl} + EMA-cross + MFI({mfi_val:.0f}) + ZLSMA + Turtle | "
            f"Bull {bull_pct:.0f}% | DLO={dlo_val:+.2f} | ADX {adx:.1f}"
        )
        return Signal("LONG", price, sl, tp1, tp2, tp3, tp4, tp5,
                     score, atr_v, reason, adx, bull_pct, bear_pct, dlo_val, tier, mfi_val, zlsma_conf)

    if short_s or short_a or short_b:
        if short_s:
            tier = "S"
            score_mult = 1.15
        elif short_a:
            tier = "A"
            score_mult = 1.0
        else:
            tier = "B"
            score_mult = 0.85
        
        score = _short_score() * score_mult
        sl, tp1, tp2, tp3, tp4, tp5 = (
            price + risk,
            price - risk, price - risk*2, price - risk*3,
            price - risk*4, price - risk*5,
        )
        
        bw_lbl = "Bounce" if bw_bounce_short else ("T3-Cross" if bw_short_recent else "T3↓")
        reason = (
            f"[{tier}] BW-{bw_lbl} + EMA-cross + MFI({mfi_val:.0f}) + ZLSMA + Turtle | "
            f"Bear {bear_pct:.0f}% | DLO={dlo_val:+.2f} | ADX {adx:.1f}"
        )
        return Signal("SHORT", price, sl, tp1, tp2, tp3, tp4, tp5,
                     score, atr_v, reason, adx, bull_pct, bear_pct, dlo_val, tier, mfi_val, zlsma_conf)

    return Signal(
        "NONE", price, 0, 0, 0, 0, 0, 0,
        max(bull_pct, bear_pct), atr_v,
        f"No signal | Bull {bull_pct:.0f}% Bear {bear_pct:.0f}% | "
        f"MFI={mfi_val:.0f} ZLSMA={zlsma_conf:+.2f}% | DLO={dlo_val:+.2f} | ADX {adx:.1f}",
        adx, bull_pct, bear_pct, dlo_val, "", mfi_val, zlsma_conf,
    )
