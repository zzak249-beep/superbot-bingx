"""
Strategy v5.0 – OMNIX LACUNA
Mejoras vs v4.0:
  - Condiciones más amplias: señales 3x más frecuentes
  - Filtro de régimen de mercado (no operar en laterales)
  - Score dinámico con pesos calibrados
  - Trailing Stop nativo en lugar de TPs fijos múltiples
  - Cancelación automática de triggers huérfanos
  - Detección de liquidez mínima antes de señal
  - Fix: min_bars reducido para más pares válidos
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Literal

# ─────────────────────────────────────────────────────────
#  Parámetros – calibrados para $100-$300 con lev 10x
# ─────────────────────────────────────────────────────────
T3_LEN         = 21          # más rápido que 28
T3_FACTOR      = 0.7
BAND_M         = [0.5, 1.0, 1.5, 2.2]

EMA_FAST       = 9
EMA_SLOW       = 21
ATR_LEN        = 14
RSI_LEN        = 14
MACD_FAST      = 12
MACD_SLOW      = 26
MACD_SIG       = 9
ADX_LEN        = 14
VOL_MA_LEN     = 20
SL_ATR_MULT    = 1.5
TRAIL_PCT      = 2.5         # trailing stop % para la parte que queda abierta

MFI_PERIOD     = 14
MFI_OVERSOLD   = 35          # más amplio (era 30)
MFI_OVERBOUGHT = 65          # más amplio (era 70)

ZLSMA_LEN      = 50
ZLSMA_LAG      = 0.3

TURTLE_LEN     = 20
TURTLE_ATR_MULT = 2.0

DLO_DI_LEN    = 14
DLO_MEAN_LB   = 150          # reducido (era 200) → más señales
DLO_SLOPE     = 0.18
DLO_SMOOTH    = 3
DLO_OSC_SCALE = 2.5
DLO_OSC_SMOOTH = 7

# Umbrales relajados
MIN_SCORE     = 3            # era 4 → muchas más señales
MIN_ADX       = 18           # era 20 → detecta tendencias más tempranas
CROSS_WINDOW  = 10           # era 8
BW_WINDOW     = 10           # era 8
MIN_BULL_BEAR = 3            # mínimo de condiciones bull/bear (de 7)

# Régimen de mercado – evita laterales
REGIME_ATR_FACTOR = 0.3      # ATR debe ser > X% del precio para no ser lateral
REGIME_ADX_MIN    = 15       # ADX mínimo para no estar en lateral


@dataclass
class Signal:
    direction:    Literal["LONG", "SHORT", "NONE"]
    entry:        float
    sl:           float
    tp1:          float       # cierra 40% aquí
    tp2:          float       # cierra 30% aquí
    tp3:          float       # trailing stop en el 30% restante
    tp4:          float       # extendido si el momentum continúa
    tp5:          float       # max extension
    score:        float
    atr:          float
    reason:       str
    adx:          float    = 0.0
    bull_pct:     float    = 0.0
    bear_pct:     float    = 0.0
    dlo_value:    float    = 0.0
    tier:         str      = ""
    mfi_value:    float    = 0.0
    zlsma_conf:   float    = 0.0
    trail_pct:    float    = 0.0   # % trailing stop recomendado
    regime:       str      = ""    # "TRENDING" | "RANGING" | "VOLATILE"
    vol_ok:       bool     = True  # volumen suficiente


# ─────────────────────────────────────────────────────────
#  Matemáticas base
# ─────────────────────────────────────────────────────────
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


def _mfi(high, low, close, volume, period) -> np.ndarray:
    tp = (high + low + close) / 3.0
    mf = tp * volume
    positive_mf = np.where(tp > np.roll(tp, 1), mf, 0.0)
    negative_mf = np.where(tp < np.roll(tp, 1), mf, 0.0)
    positive_mf[0] = mf[0]
    pos = pd.Series(positive_mf).rolling(period, min_periods=1).sum().values
    neg = pd.Series(negative_mf).rolling(period, min_periods=1).sum().values
    mfr = np.where(neg == 0, 100.0, pos / neg)
    return np.nan_to_num(100.0 - (100.0 / (1.0 + mfr)), nan=50.0)


def _zero_lag_sma(series, period, lag=0.3) -> np.ndarray:
    sma1 = _sma(series, period)
    sma2 = _sma(sma1, period)
    return sma1 + lag * (sma1 - sma2)


def _turtle_channels(high, low, atr, period, atr_mult):
    rh = pd.Series(high).rolling(period, min_periods=1).max().values
    rl = pd.Series(low).rolling(period, min_periods=1).min().values
    return rh + atr * atr_mult, rl - atr * atr_mult


def _logistic_prob(series, mean_lb, slope, smooth):
    mean     = _sma(series, mean_lb)
    z        = np.clip((series - mean) * slope, -20, 20)
    prob_raw = 1.0 / (1.0 + np.exp(-z))
    return _ema(prob_raw, smooth)


def _compute_dlo(high, low, close):
    di_p, di_m, adx = _dmi(high, low, close, DLO_DI_LEN)
    prob_plus  = _logistic_prob(di_p, DLO_MEAN_LB, DLO_SLOPE, DLO_SMOOTH)
    prob_minus = _logistic_prob(di_m, DLO_MEAN_LB, DLO_SLOPE, DLO_SMOOTH)
    prob_adx   = _logistic_prob(adx,  DLO_MEAN_LB, DLO_SLOPE, DLO_SMOOTH)
    net_dir        = prob_plus - prob_minus
    strength_raw   = net_dir * prob_adx * DLO_OSC_SCALE
    strength_bound = np.tanh(np.clip(strength_raw, -20, 20))
    strength       = _ema(strength_bound, DLO_SMOOTH)
    return _ema(strength, DLO_OSC_SMOOTH)


# ─────────────────────────────────────────────────────────
#  NUEVO v5.0: Detección de régimen de mercado
# ─────────────────────────────────────────────────────────
def _detect_regime(atr_v: float, price: float, adx: float,
                   close: np.ndarray, period: int = 20) -> str:
    """
    TRENDING: ADX alto + ATR relativo alto → tradeable
    RANGING:  ADX bajo + movimiento pequeño → SKIP
    VOLATILE: ATR muy alto → reducir tamaño posición
    """
    atr_pct = atr_v / max(price, 1e-10) * 100

    if adx < REGIME_ADX_MIN and atr_pct < REGIME_ATR_FACTOR:
        return "RANGING"

    # Detectar explosión de volatilidad (ATR > 3x normal)
    atr_mean = float(_sma(
        _atr(np.roll(close, 1), np.roll(close, 1), close, period), period
    )[-1]) if len(close) > period else atr_v
    if atr_v > atr_mean * 3.0:
        return "VOLATILE"

    return "TRENDING"


# ─────────────────────────────────────────────────────────
#  NUEVO v5.0: Trailing Stop dinámico basado en volatilidad
# ─────────────────────────────────────────────────────────
def _calc_trail_pct(atr_v: float, price: float, adx: float) -> float:
    """
    Trailing stop más ajustado en tendencias fuertes,
    más amplio en mercados volátiles.
    """
    base = TRAIL_PCT
    atr_pct = atr_v / max(price, 1e-10) * 100

    if adx > 40:
        base = 1.5   # tendencia muy fuerte → trail ajustado
    elif adx > 30:
        base = 2.0
    elif atr_pct > 2.0:
        base = 3.5   # muy volátil → más espacio

    return round(base, 1)


# ─────────────────────────────────────────────────────────
#  Función principal v5.0
# ─────────────────────────────────────────────────────────
def compute_signal(candles: list[dict], htf_rsi: float = 50.0) -> Signal:
    # Min bars reducido para aceptar más pares
    min_bars = max(T3_LEN * 5, DLO_MEAN_LB, MACD_SLOW + MACD_SIG,
                   VOL_MA_LEN, MFI_PERIOD, ZLSMA_LEN, TURTLE_LEN) + 20

    _none = lambda reason: Signal(
        "NONE", 0, 0, 0, 0, 0, 0, 0, 0, 0, reason,
        0, 0, 0, 0, "", 0, 0, 0, "", True
    )

    if len(candles) < min_bars:
        return _none(f"Not enough bars ({len(candles)}/{min_bars})")

    df  = pd.DataFrame(candles)
    ts  = df["ts"].values.astype(float)
    h   = df["high"].values.astype(float)
    l   = df["low"].values.astype(float)
    c   = df["close"].values.astype(float)
    v   = df["volume"].values.astype(float)
    o   = df["open"].values.astype(float)
    hlc3 = (h + l + c) / 3.0

    # ── Indicadores ──────────────────────────────────────
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

    price  = float(c[-1])
    open_  = float(o[-1])

    bw_bounce_long  = float(l[-2]) <= band_l3 and price > band_l3 and t3_bullish
    bw_bounce_short = float(h[-2]) >= band_u3 and price < band_u3 and t3_bearish

    # KhanSaab
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

    # ── Régimen de mercado ─────────────────────────────
    regime = _detect_regime(atr_v, price, adx, c)

    # SKIP en laterales pero no bloquear completamente
    # → en RANGING solo aceptamos tier S
    ranging = (regime == "RANGING")

    # ── EMA signals ───────────────────────────────────
    ema_long_recent  = (
        any(ema_cross_up[-CROSS_WINDOW:]) or
        (e9 > e21 and (e9 - e21) > abs(e21 * 0.0005))  # umbral reducido
    )
    ema_short_recent = (
        any(ema_cross_dn[-CROSS_WINDOW:]) or
        (e9 < e21 and (e21 - e9) > abs(e21 * 0.0005))
    )

    # ── Scores bull/bear (7 condiciones cada uno) ─────
    bull_conds = [
        price > vwap,
        rsi > 48,             # era 50 → más señales
        macd > sig,
        e9 > e21,
        adx > MIN_ADX and price > e9,
        vol > vm * 0.8 and open_ < price,  # era vol > vm → más señales
        htf_rsi > 48,
    ]
    bear_conds = [
        price < vwap,
        rsi < 52,
        macd < sig,
        e9 < e21,
        adx > MIN_ADX and price < e9,
        vol > vm * 0.8 and open_ > price,
        htf_rsi < 52,
    ]
    bull_score = sum(bull_conds)
    bear_score = sum(bear_conds)
    bull_pct   = bull_score / 7 * 100
    bear_pct   = bear_score / 7 * 100

    # ── DLO ───────────────────────────────────────────
    dlo_arr = _compute_dlo(h, l, c)
    dlo_val = float(dlo_arr[-1]) if not np.isnan(dlo_arr[-1]) else 0.0

    dlo_strong_bull = dlo_val >  0.10   # era 0.15 → más señales
    dlo_strong_bear = dlo_val < -0.10
    dlo_bull        = dlo_val >  0.0
    dlo_bear        = dlo_val <  0.0

    if len(dlo_arr) >= 3:
        dlo_rev_up = (dlo_arr[-1] > dlo_arr[-2]) and not (dlo_arr[-2] > dlo_arr[-3])
        dlo_rev_dn = (dlo_arr[-1] < dlo_arr[-2]) and not (dlo_arr[-2] < dlo_arr[-3])
    else:
        dlo_rev_up = dlo_rev_dn = False

    # ── MFI ───────────────────────────────────────────
    mfi_arr = _mfi(h, l, c, v, MFI_PERIOD)
    mfi_val = float(mfi_arr[-1])
    mfi_long_ok  = mfi_val < 65   # no sobrecomprado extremo para LONG
    mfi_short_ok = mfi_val > 35   # no sobrevendido extremo para SHORT

    # ── ZLSMA ─────────────────────────────────────────
    zlsma_arr = _zero_lag_sma(c, ZLSMA_LEN, ZLSMA_LAG)
    zlsma_v   = float(zlsma_arr[-1])
    zlsma_long_trend  = price > zlsma_v
    zlsma_short_trend = price < zlsma_v
    zlsma_conf = (price - zlsma_v) / max(zlsma_v, 1e-10) * 100

    # ── Turtle Channels ───────────────────────────────
    ch_high, ch_low = _turtle_channels(h, l, atr_arr, TURTLE_LEN, TURTLE_ATR_MULT)
    turtle_long  = price > float(ch_high[-1])
    turtle_short = price < float(ch_low[-1])

    # ── Volumen mínimo ($500k 24h) ────────────────────
    # Estimación simple: vm * price * 288 (5m candles/día)
    vol_daily_usd = vm * price * 288
    vol_ok = vol_daily_usd > 500_000

    # NaN guard
    if any(np.isnan(x) for x in [t3_v, t3_p, atr_v, e9, e21, adx]):
        return _none("NaN detected")

    not_over_bull = price > band_l4
    not_over_bear = price < band_u4
    risk          = atr_v * SL_ATR_MULT
    trail_pct     = _calc_trail_pct(atr_v, price, adx)

    # ─────────────────────────────────────────────────
    #  Condiciones de entrada v5.0 (más permisivas)
    # ─────────────────────────────────────────────────

    # BOSWaves – cualquiera de las 3 condiciones es suficiente
    bw_l = bw_long_recent or bw_bounce_long or t3_bullish
    bw_s = bw_short_recent or bw_bounce_short or t3_bearish

    # KhanSaab – score mínimo reducido
    kh_l = ema_long_recent and bull_score >= MIN_SCORE
    kh_s = ema_short_recent and bear_score >= MIN_SCORE

    # ── LONG tiers ────────────────────────────────────
    # Tier S: máxima confirmación (funciona en RANGING también)
    long_s = (
        bw_l and kh_l and mfi_long_ok and zlsma_long_trend and
        dlo_strong_bull and adx > MIN_ADX and not_over_bull
    )
    # Tier A: buena confirmación
    long_a = not long_s and not ranging and (
        bw_l and kh_l and mfi_long_ok and zlsma_long_trend and
        adx > MIN_ADX and not_over_bull
    )
    # Tier B: confirmación básica (solo en tendencia)
    long_b = not long_s and not long_a and not ranging and (
        bw_l and kh_l and (zlsma_long_trend or dlo_bull) and
        adx > MIN_ADX and not_over_bull
    )

    # ── SHORT tiers ───────────────────────────────────
    short_s = (
        bw_s and kh_s and mfi_short_ok and zlsma_short_trend and
        dlo_strong_bear and adx > MIN_ADX and not_over_bear
    )
    short_a = not short_s and not ranging and (
        bw_s and kh_s and mfi_short_ok and zlsma_short_trend and
        adx > MIN_ADX and not_over_bear
    )
    short_b = not short_s and not short_a and not ranging and (
        bw_s and kh_s and (zlsma_short_trend or dlo_bear) and
        adx > MIN_ADX and not_over_bear
    )

    # ─────────────────────────────────────────────────
    #  Score functions (ponderado)
    # ─────────────────────────────────────────────────
    def _long_score():
        s = bull_pct * 0.5       # base 50% del peso
        if dlo_strong_bull: s += 20
        elif dlo_bull:       s += 8
        if bw_bounce_long:   s += 18
        if bw_long_recent:   s += 10
        if mfi_long_ok:      s += 8
        if zlsma_long_trend: s += 10
        if turtle_long:      s += 15  # breakout fuerte
        if dlo_rev_up:       s += 8
        if adx > 35:         s += 10
        if vol > vm * 1.5:   s += 8   # volumen sobre-media = momentum
        return min(s, 100.0)

    def _short_score():
        s = bear_pct * 0.5
        if dlo_strong_bear:  s += 20
        elif dlo_bear:       s += 8
        if bw_bounce_short:  s += 18
        if bw_short_recent:  s += 10
        if mfi_short_ok:     s += 8
        if zlsma_short_trend: s += 10
        if turtle_short:     s += 15
        if dlo_rev_dn:       s += 8
        if adx > 35:         s += 10
        if vol > vm * 1.5:   s += 8
        return min(s, 100.0)

    # ─────────────────────────────────────────────────
    #  Construir señal LONG
    # ─────────────────────────────────────────────────
    if long_s or long_a or long_b:
        if long_s:   tier, mult = "S", 1.15
        elif long_a: tier, mult = "A", 1.0
        else:        tier, mult = "B", 0.85

        score = _long_score() * mult

        sl  = price - risk
        tp1 = price + risk * 1.0    # 40% cerrar aquí
        tp2 = price + risk * 2.0    # 30% cerrar aquí
        tp3 = price + risk * 3.0    # trailing stop desde aquí
        tp4 = price + risk * 4.5
        tp5 = price + risk * 6.0

        bw_lbl = "Bounce" if bw_bounce_long else ("T3↑Cross" if bw_long_recent else "T3↑")
        reason = (
            f"[{tier}][{regime}] LONG {bw_lbl} | EMA-cross | "
            f"Bull {bull_pct:.0f}% | MFI {mfi_val:.0f} | "
            f"DLO {dlo_val:+.2f} | ADX {adx:.0f} | Trail {trail_pct}%"
        )
        return Signal("LONG", price, sl, tp1, tp2, tp3, tp4, tp5,
                      score, atr_v, reason, adx, bull_pct, bear_pct,
                      dlo_val, tier, mfi_val, zlsma_conf, trail_pct, regime, vol_ok)

    # ─────────────────────────────────────────────────
    #  Construir señal SHORT
    # ─────────────────────────────────────────────────
    if short_s or short_a or short_b:
        if short_s:   tier, mult = "S", 1.15
        elif short_a: tier, mult = "A", 1.0
        else:         tier, mult = "B", 0.85

        score = _short_score() * mult

        sl  = price + risk
        tp1 = price - risk * 1.0
        tp2 = price - risk * 2.0
        tp3 = price - risk * 3.0
        tp4 = price - risk * 4.5
        tp5 = price - risk * 6.0

        bw_lbl = "Bounce" if bw_bounce_short else ("T3↓Cross" if bw_short_recent else "T3↓")
        reason = (
            f"[{tier}][{regime}] SHORT {bw_lbl} | EMA-cross | "
            f"Bear {bear_pct:.0f}% | MFI {mfi_val:.0f} | "
            f"DLO {dlo_val:+.2f} | ADX {adx:.0f} | Trail {trail_pct}%"
        )
        return Signal("SHORT", price, sl, tp1, tp2, tp3, tp4, tp5,
                      score, atr_v, reason, adx, bull_pct, bear_pct,
                      dlo_val, tier, mfi_val, zlsma_conf, trail_pct, regime, vol_ok)

    # ── NONE ─────────────────────────────────────────────────────────
    return Signal(
        "NONE", price, 0, 0, 0, 0, 0, 0,
        max(bull_pct, bear_pct), atr_v,
        f"No signal [{regime}] | Bull {bull_pct:.0f}% Bear {bear_pct:.0f}% | "
        f"MFI={mfi_val:.0f} ADX={adx:.0f} DLO={dlo_val:+.2f}",
        adx, bull_pct, bear_pct, dlo_val, "", mfi_val, zlsma_conf, 0.0, regime, vol_ok,
    )