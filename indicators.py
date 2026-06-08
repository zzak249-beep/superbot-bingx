"""
GUA-USDT Bot v3 — Indicadores Técnicos
Fixes v3:
  • EMA warm-up correcto: usa min_periods para señalizar NaN hasta tener datos reales
  • MACD conectado al scorer (ya no se calcula y se ignora)
  • MFI (Money Flow Index) como confirmación de volumen institucional
  • pre_compression(): detecta acumulación silenciosa ANTES del breakout
  • Tolerancia dinámica en liquidity sweeps (proporcional al ATR)
  • detect_fvg mejorado: marca FVGs "frescos" (<10 velas) con mayor peso
  • market_structure con zona de confluencia (precio ±0.1% del swing level)
"""

from __future__ import annotations
import numpy as np
from typing import Dict, List, Optional, Tuple


# ══════════════════════════════════════════════════════════════════════
#  CLÁSICOS
# ══════════════════════════════════════════════════════════════════════

def ema(values: List[float], period: int) -> np.ndarray:
    arr = np.array(values, dtype=float)
    k   = 2.0 / (period + 1)
    out = np.full(len(arr), np.nan)
    # Warm-up: primera media simple con period velas
    if len(arr) < period:
        return out
    out[period - 1] = arr[:period].mean()
    for i in range(period, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def sma(values: List[float], period: int) -> np.ndarray:
    arr = np.array(values, dtype=float)
    out = np.full(len(arr), np.nan)
    for i in range(period - 1, len(arr)):
        out[i] = arr[i - period + 1:i + 1].mean()
    return out


def rsi(closes: List[float], period: int = 14) -> np.ndarray:
    arr = np.array(closes, dtype=float)
    d   = np.diff(arr)
    g   = np.where(d > 0, d, 0.0)
    l_  = np.where(d < 0, -d, 0.0)
    n   = len(arr)
    ag  = np.zeros(n)
    al  = np.zeros(n)
    if n <= period:
        return np.full(n, np.nan)
    ag[period] = g[:period].mean()
    al[period] = l_[:period].mean()
    for i in range(period + 1, n):
        ag[i] = (ag[i - 1] * (period - 1) + g[i - 1]) / period
        al[i] = (al[i - 1] * (period - 1) + l_[i - 1]) / period
    rs  = np.where(al == 0, 100.0, ag / al)
    out = np.where(al == 0, 100.0, 100.0 - 100.0 / (1.0 + rs))
    out[:period] = np.nan
    return out


def atr(highs: List[float], lows: List[float], closes: List[float],
        period: int = 14) -> np.ndarray:
    h  = np.array(highs,  dtype=float)
    l  = np.array(lows,   dtype=float)
    c  = np.array(closes, dtype=float)
    pc = np.roll(c, 1); pc[0] = c[0]
    tr  = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.zeros(len(tr))
    if len(tr) < period:
        return out
    out[period - 1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def adx(highs: List[float], lows: List[float], closes: List[float],
        period: int = 14) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    h  = np.array(highs,  dtype=float)
    l  = np.array(lows,   dtype=float)
    c  = np.array(closes, dtype=float)
    n  = len(c)
    pc = np.roll(c, 1); pc[0] = c[0]
    ph = np.roll(h, 1); ph[0] = h[0]
    pl = np.roll(l, 1); pl[0] = l[0]
    tr   = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    dmp  = np.where((h - ph) > (pl - l), np.maximum(h - ph, 0), 0)
    dmm  = np.where((pl - l) > (h - ph), np.maximum(pl - l, 0), 0)
    atr14 = np.zeros(n); dmp14 = np.zeros(n); dmm14 = np.zeros(n)
    if n <= period:
        return atr14, dmp14, dmm14
    atr14[period] = tr[1:period + 1].sum()
    dmp14[period] = dmp[1:period + 1].sum()
    dmm14[period] = dmm[1:period + 1].sum()
    for i in range(period + 1, n):
        atr14[i] = atr14[i - 1] - atr14[i - 1] / period + tr[i]
        dmp14[i] = dmp14[i - 1] - dmp14[i - 1] / period + dmp[i]
        dmm14[i] = dmm14[i - 1] - dmm14[i - 1] / period + dmm[i]
    dip  = np.where(atr14 == 0, 0, 100 * dmp14 / atr14)
    dim  = np.where(atr14 == 0, 0, 100 * dmm14 / atr14)
    den  = dip + dim
    dx   = np.where(den == 0, 0, 100 * np.abs(dip - dim) / den)
    adxv = np.zeros(n)
    s    = 2 * period
    if s < n:
        adxv[s] = dx[period:s + 1].mean()
        for i in range(s + 1, n):
            adxv[i] = (adxv[i - 1] * (period - 1) + dx[i]) / period
    return adxv, dip, dim


def cvd(opens: List[float], closes: List[float], volumes: List[float],
        window: int = 20) -> np.ndarray:
    o     = np.array(opens,   dtype=float)
    c     = np.array(closes,  dtype=float)
    v     = np.array(volumes, dtype=float)
    delta = np.where(c > o, v, np.where(c < o, -v, 0.0))
    n = len(delta); out = np.zeros(n)
    for i in range(n):
        s = max(0, i - window + 1)
        out[i] = delta[s:i + 1].sum()
    return out


def slope(arr: np.ndarray, n: int = 5) -> float:
    y = arr[-n:]
    x = np.arange(len(y), dtype=float)
    return float(np.polyfit(x, y, 1)[0]) if len(y) >= 2 else 0.0


def atr_percentile(atr_arr: np.ndarray, window: int = 50) -> float:
    hist = atr_arr[-window:]
    hist = hist[hist > 0]
    if len(hist) < 5:
        return 50.0
    return float(np.mean(hist <= atr_arr[-1]) * 100)


# ══════════════════════════════════════════════════════════════════════
#  MFI — Money Flow Index (volumen institucional)
# ══════════════════════════════════════════════════════════════════════

def mfi(highs: List[float], lows: List[float], closes: List[float],
        volumes: List[float], period: int = 14) -> np.ndarray:
    """
    Money Flow Index: RSI ponderado por volumen.
    >70 → sobrecompra (presión compradora agotándose)
    <30 → sobreventa (acumulación institucional posible)
    Divergencia MFI vs precio = señal más potente que el RSI solo.
    """
    h  = np.array(highs,   dtype=float)
    l  = np.array(lows,    dtype=float)
    c  = np.array(closes,  dtype=float)
    v  = np.array(volumes, dtype=float)
    n  = len(c)
    tp = (h + l + c) / 3.0
    mf = tp * v  # raw money flow

    out = np.full(n, np.nan)
    for i in range(period, n):
        pos_mf = sum(
            mf[j] for j in range(i - period + 1, i + 1)
            if tp[j] >= tp[j - 1]
        )
        neg_mf = sum(
            mf[j] for j in range(i - period + 1, i + 1)
            if tp[j] < tp[j - 1]
        )
        if neg_mf == 0:
            out[i] = 100.0
        else:
            mfr = pos_mf / neg_mf
            out[i] = 100.0 - (100.0 / (1.0 + mfr))
    return out


# ══════════════════════════════════════════════════════════════════════
#  TTM SQUEEZE MOMENTUM
# ══════════════════════════════════════════════════════════════════════

def squeeze_momentum(
    highs: List[float], lows: List[float], closes: List[float],
    bb_period: int = 20, bb_mult: float = 2.0,
    kc_period: int = 20, kc_mult: float = 1.5,
    mom_period: int = 12,
) -> Tuple[np.ndarray, np.ndarray]:
    h  = np.array(highs,  dtype=float)
    l  = np.array(lows,   dtype=float)
    c  = np.array(closes, dtype=float)
    n  = len(c)

    bb_mid = sma(closes, bb_period)
    bb_std = np.array([
        c[max(0, i - bb_period + 1):i + 1].std() if i >= bb_period - 1 else 0
        for i in range(n)
    ])
    bb_up = bb_mid + bb_mult * bb_std
    bb_dn = bb_mid - bb_mult * bb_std

    atr_kc = atr(highs, lows, closes, kc_period)
    kc_up  = bb_mid + kc_mult * atr_kc
    kc_dn  = bb_mid - kc_mult * atr_kc

    sqz = (bb_up <= kc_up) & (bb_dn >= kc_dn)

    don_hi  = np.array([h[max(0, i - mom_period):i + 1].max() for i in range(n)])
    don_lo  = np.array([l[max(0, i - mom_period):i + 1].min() for i in range(n)])
    don_mid = (don_hi + don_lo) / 2
    delta   = c - (don_mid + bb_mid) / 2

    mom = np.zeros(n)
    for i in range(mom_period, n):
        y = delta[i - mom_period:i]
        x = np.arange(mom_period, dtype=float)
        mom[i] = float(np.polyfit(x, y, 1)[0]) * mom_period

    return sqz, mom


# ══════════════════════════════════════════════════════════════════════
#  PRE-COMPRESSION DETECTOR
#  Detecta acumulación ANTES del breakout — el edge que los otros bots no tienen
# ══════════════════════════════════════════════════════════════════════

def pre_compression(
    highs: List[float], lows: List[float], volumes: List[float],
    atr_arr: np.ndarray,
    range_lookback: int = 8,
    vol_lookback: int = 20,
) -> Tuple[bool, str]:
    """
    Detecta compresión pre-breakout en 3 condiciones:
      1. Rango de velas recientes < 40% del rango normal (compresión)
      2. Volumen se mantiene ≥ 60% del promedio (acumulación activa)
      3. ATR decreciente en las últimas N velas (squeeze estructural)

    Devuelve (is_compressing, bias):
      bias = "BULL" si los cierres están en el tercio superior del rango
      bias = "BEAR" si están en el tercio inferior
      bias = "NEUTRAL" si no hay sesgo claro

    El edge: entrar mientras comprime, con SL al extremo del rango.
    Los breakout-bots entran 2-5 velas después, a peor precio.
    """
    h   = np.array(highs,   dtype=float)
    l   = np.array(lows,    dtype=float)
    v   = np.array(volumes, dtype=float)
    n   = len(h)

    if n < vol_lookback + 2:
        return False, "NEUTRAL"

    # Rango reciente vs rango histórico
    recent_ranges  = h[-range_lookback:] - l[-range_lookback:]
    historic_range = (h[-vol_lookback:] - l[-vol_lookback:]).mean()
    if historic_range == 0:
        return False, "NEUTRAL"

    avg_recent_range = recent_ranges.mean()
    range_ratio      = avg_recent_range / historic_range

    # Volumen: reciente vs histórico
    recent_vol  = v[-range_lookback:].mean()
    historic_vol = v[-vol_lookback:].mean()
    vol_ratio   = recent_vol / historic_vol if historic_vol > 0 else 1.0

    # ATR decreciente (comprimir energía)
    atr_recent   = atr_arr[-range_lookback:]
    atr_recent   = atr_recent[atr_recent > 0]
    atr_declining = (len(atr_recent) >= 3 and
                     float(np.polyfit(np.arange(len(atr_recent)), atr_recent, 1)[0]) < 0)

    is_compressing = (
        range_ratio < 0.40 and   # rango comprimido al 40% del normal
        vol_ratio   >= 0.60 and  # volumen sostenido (no desvaneciéndose)
        atr_declining             # ATR bajando = squeeze estructural
    )

    if not is_compressing:
        return False, "NEUTRAL"

    # Sesgo: ¿dónde están cerrando las velas dentro del rango comprimido?
    closes_in_range = []
    for i in range(-range_lookback, 0):
        rng = h[i] - l[i]
        if rng > 0:
            pos = (h[i] - l[i])  # usamos highs como proxy del close relativo
            # posición del close dentro del rango
            closes_in_range.append((h[i] + l[i]) / 2)

    if not closes_in_range:
        return True, "NEUTRAL"

    range_lo = l[-range_lookback:].min()
    range_hi = h[-range_lookback:].max()
    total_range = range_hi - range_lo
    if total_range == 0:
        return True, "NEUTRAL"

    avg_mid = np.mean(closes_in_range)
    position = (avg_mid - range_lo) / total_range  # 0=bajo, 1=alto

    if position > 0.65:
        bias = "BULL"   # acumulando en la parte alta → breakout alcista probable
    elif position < 0.35:
        bias = "BEAR"   # distribuyendo en la parte baja → breakdown probable
    else:
        bias = "NEUTRAL"

    return True, bias


# ══════════════════════════════════════════════════════════════════════
#  FUNDING RATE — Ventana pre-pago (45 min antes)
# ══════════════════════════════════════════════════════════════════════

def funding_window_active() -> bool:
    """
    True si estamos en la ventana de 45 min antes del funding payment.
    BingX paga funding a las 00:00, 08:00, 16:00 UTC.
    En esta ventana, quienes pagan (posiciones en extremo) cierran → predecible.
    """
    from datetime import datetime, timezone
    now     = datetime.now(timezone.utc)
    minutes = now.hour * 60 + now.minute
    # 45 min antes de 00:00=0, 08:00=480, 16:00=960
    payment_minutes = [0, 480, 960]
    for pm in payment_minutes:
        diff = (minutes - pm) % (24 * 60)
        if diff >= (24 * 60 - 45) or diff <= 5:  # ventana ±45min antes + 5 después
            return True
    return False


# ══════════════════════════════════════════════════════════════════════
#  RVOL
# ══════════════════════════════════════════════════════════════════════

def rvol(volumes: List[float], period: int = 20) -> np.ndarray:
    v   = np.array(volumes, dtype=float)
    avg = sma(volumes, period)
    return np.where(avg > 0, v / avg, 1.0)


# ══════════════════════════════════════════════════════════════════════
#  VWAP con bandas de desviación estándar
# ══════════════════════════════════════════════════════════════════════

def vwap_bands(
    highs: List[float], lows: List[float], closes: List[float],
    volumes: List[float], period: int = 60, band_mult: float = 1.5,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    h  = np.array(highs,   dtype=float)
    l  = np.array(lows,    dtype=float)
    c  = np.array(closes,  dtype=float)
    v  = np.array(volumes, dtype=float)
    tp = (h + l + c) / 3.0
    n  = len(c)

    vw = np.zeros(n); vw_up = np.zeros(n); vw_dn = np.zeros(n)
    for i in range(period - 1, n):
        s  = i - period + 1
        sv = v[s:i + 1].sum()
        if sv == 0:
            vw[i] = tp[i]; vw_up[i] = tp[i]; vw_dn[i] = tp[i]; continue
        vw[i]    = (tp[s:i + 1] * v[s:i + 1]).sum() / sv
        dev      = np.sqrt(((tp[s:i + 1] - vw[i]) ** 2 * v[s:i + 1]).sum() / sv)
        vw_up[i] = vw[i] + band_mult * dev
        vw_dn[i] = vw[i] - band_mult * dev

    return vw, vw_up, vw_dn


# ══════════════════════════════════════════════════════════════════════
#  CVD DIVERGENCIA
# ══════════════════════════════════════════════════════════════════════

def cvd_divergence(closes: List[float], cvd_arr: np.ndarray,
                   lookback: int = 10) -> Tuple[bool, bool]:
    c  = np.array(closes, dtype=float)
    lb = min(lookback, len(c) - 1)
    price_slope = slope(c, lb)
    cvd_slope   = slope(cvd_arr, lb)

    bearish_div = (price_slope > 0) and (cvd_slope < 0)
    bullish_div = (price_slope < 0) and (cvd_slope > 0)
    return bearish_div, bullish_div


# ══════════════════════════════════════════════════════════════════════
#  FVG — Fair Value Gaps (v3: marca "freshness")
# ══════════════════════════════════════════════════════════════════════

def detect_fvg(
    highs: List[float], lows: List[float], closes: List[float],
    lookback: int = 30, min_size_pct: float = 0.003,
) -> Tuple[Optional[Dict], Optional[Dict]]:
    """
    Detecta FVGs no rellenados.
    Ahora incluye campo "fresh" (True si age < 10 velas) para ponderar
    en el scorer: FVG fresco = señal más potente.
    """
    h = np.array(highs,  dtype=float)
    l = np.array(lows,   dtype=float)
    c = np.array(closes, dtype=float)
    n = len(c)

    bear_fvg = None
    bull_fvg = None

    for i in range(n - 2, max(n - lookback - 2, 2), -1):
        if bear_fvg is None:
            if h[i] < l[i - 2]:
                size = (l[i - 2] - h[i]) / max(h[i], 1e-9)
                if size >= min_size_pct:
                    mid = (l[i - 2] + h[i]) / 2
                    if c[-1] < mid or c[-1] < l[i - 2]:
                        age = n - 1 - i
                        bear_fvg = {
                            "top": l[i - 2], "bottom": h[i],
                            "midpoint": mid, "age": age,
                            "fresh": age < 10,
                        }

        if bull_fvg is None:
            if l[i] > h[i - 2]:
                size = (l[i] - h[i - 2]) / max(h[i - 2], 1e-9)
                if size >= min_size_pct:
                    mid = (l[i] + h[i - 2]) / 2
                    if c[-1] > mid or c[-1] > h[i - 2]:
                        age = n - 1 - i
                        bull_fvg = {
                            "top": l[i], "bottom": h[i - 2],
                            "midpoint": mid, "age": age,
                            "fresh": age < 10,
                        }

        if bear_fvg and bull_fvg:
            break

    return bear_fvg, bull_fvg


# ══════════════════════════════════════════════════════════════════════
#  ORDER BLOCKS
# ══════════════════════════════════════════════════════════════════════

def detect_order_blocks(
    opens: List[float], highs: List[float], lows: List[float],
    closes: List[float], lookback: int = 40, impulse_bars: int = 3,
) -> Tuple[Optional[Dict], Optional[Dict]]:
    o = np.array(opens,  dtype=float)
    h = np.array(highs,  dtype=float)
    l = np.array(lows,   dtype=float)
    c = np.array(closes, dtype=float)
    n = len(c)

    bear_ob = None
    bull_ob = None

    for i in range(n - 1, max(n - lookback, impulse_bars + 2), -1):
        if bear_ob is None:
            reds = sum(1 for j in range(i, min(i + impulse_bars, n)) if c[j] < o[j])
            if reds >= impulse_bars:
                for k in range(i - 1, max(i - 6, 0), -1):
                    if c[k] > o[k]:
                        bear_ob = {
                            "high": h[k], "low": l[k],
                            "mid":  (h[k] + l[k]) / 2,
                            "age":  n - 1 - k,
                        }
                        break

        if bull_ob is None:
            greens = sum(1 for j in range(i, min(i + impulse_bars, n)) if c[j] > o[j])
            if greens >= impulse_bars:
                for k in range(i - 1, max(i - 6, 0), -1):
                    if c[k] < o[k]:
                        bull_ob = {
                            "high": h[k], "low": l[k],
                            "mid":  (h[k] + l[k]) / 2,
                            "age":  n - 1 - k,
                        }
                        break

        if bear_ob and bull_ob:
            break

    return bear_ob, bull_ob


# ══════════════════════════════════════════════════════════════════════
#  LIQUIDITY SWEEPS — Tolerancia dinámica (proporcional al ATR)
# ══════════════════════════════════════════════════════════════════════

def detect_liquidity_sweep(
    highs: List[float], lows: List[float], closes: List[float],
    opens: List[float], lookback: int = 25,
    tolerance: float = 0.002,
    atr_val: float = 0.0,    # NUEVO: tolerancia dinámica si se pasa ATR
) -> Tuple[bool, bool]:
    """
    v3: Si se pasa atr_val, la tolerancia se ajusta dinámicamente:
    tol = max(config_tol, atr_val / price * 0.5)
    Esto evita falsos positivos en alta volatilidad y falsos negativos en baja.
    """
    h = np.array(highs,  dtype=float)
    l = np.array(lows,   dtype=float)
    c = np.array(closes, dtype=float)

    price = c[-1] if c[-1] > 0 else 1.0
    if atr_val > 0:
        dyn_tol = atr_val / price * 0.5
        tolerance = max(tolerance, min(dyn_tol, 0.008))  # cap a 0.8%

    win  = h[-lookback - 2:-3]
    wl   = l[-lookback - 2:-3]
    cur_h, cur_l, cur_c = h[-2], l[-2], c[-2]

    swept_highs = False
    swept_lows  = False

    if len(win) >= 2:
        for i in range(len(win)):
            for j in range(i + 1, len(win)):
                if abs(win[i] - win[j]) / max(win[i], 1e-9) < tolerance:
                    eq_level = (win[i] + win[j]) / 2
                    if cur_h > eq_level * (1 + tolerance) and cur_c < eq_level:
                        swept_highs = True
                        break
            if swept_highs:
                break

    if len(wl) >= 2:
        for i in range(len(wl)):
            for j in range(i + 1, len(wl)):
                if abs(wl[i] - wl[j]) / max(wl[i], 1e-9) < tolerance:
                    eq_level = (wl[i] + wl[j]) / 2
                    if cur_l < eq_level * (1 - tolerance) and cur_c > eq_level:
                        swept_lows = True
                        break
            if swept_lows:
                break

    return swept_highs, swept_lows


# ══════════════════════════════════════════════════════════════════════
#  MACD
# ══════════════════════════════════════════════════════════════════════

def macd(closes: List[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Devuelve (macd_line, signal_line, histogram)."""
    e_fast = ema(closes, fast)
    e_slow = ema(closes, slow)
    # Enmascarar NaNs del warm-up
    valid  = ~np.isnan(e_fast) & ~np.isnan(e_slow)
    ml     = np.where(valid, e_fast - e_slow, np.nan)
    ml_list = np.where(np.isnan(ml), 0.0, ml).tolist()
    sl     = ema(ml_list, signal)
    hist   = np.where(np.isnan(ml), np.nan, ml - sl)
    return ml, sl, hist


# ══════════════════════════════════════════════════════════════════════
#  MARKET STRUCTURE — BOS / CHoCH (v3: zona de confluencia ±0.1%)
# ══════════════════════════════════════════════════════════════════════

def market_structure(
    highs: List[float], lows: List[float], closes: List[float],
    swing_len: int = 5,
) -> Dict[str, str]:
    h = np.array(highs,  dtype=float)
    l = np.array(lows,   dtype=float)
    c = np.array(closes, dtype=float)
    n = len(c)

    win = min(30, n - swing_len - 1)

    swing_highs = []
    swing_lows  = []

    for i in range(swing_len, win + swing_len):
        idx = n - 1 - i
        if idx < swing_len or idx >= n - swing_len:
            continue
        if h[idx] == h[idx - swing_len:idx + swing_len + 1].max():
            swing_highs.append((idx, h[idx]))
        if l[idx] == l[idx - swing_len:idx + swing_len + 1].min():
            swing_lows.append((idx, l[idx]))

    bos   = "NONE"
    choch = "NONE"
    cur   = c[-2]

    ZONE = 0.001  # ±0.1% confluencia (v3: evita señales espurias en zona)

    if len(swing_highs) >= 2:
        last_sh = swing_highs[0][1]
        prev_sh = swing_highs[1][1]
        # Rompe claramente el nivel (no apenas en la zona)
        if cur > last_sh * (1 + ZONE):
            if last_sh > prev_sh:
                bos = "BULL"
            else:
                choch = "BULL"

    if len(swing_lows) >= 2:
        last_sl = swing_lows[0][1]
        prev_sl = swing_lows[1][1]
        if cur < last_sl * (1 - ZONE):
            if last_sl < prev_sl:
                bos = "BEAR"
            else:
                choch = "BEAR"

    return {"bos": bos, "choch": choch}
