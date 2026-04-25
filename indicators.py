"""
strategy/indicators.py — Indicadores técnicos para ConfluenceEngine.
Todos los cálculos son puros NumPy (sin pandas en el hot path).
Funciones devuelven escalares salvo que se indique lo contrario.
"""

import numpy as np
from typing import List, Tuple, Union

# ── Constantes de régimen ────────────────────────────────────────────────────
REGIME_TRENDING  = "trending"
REGIME_RANGING   = "ranging"
REGIME_VOLATILE  = "volatile"


# ── Helpers internos ─────────────────────────────────────────────────────────

def _arr(x: Union[List, np.ndarray]) -> np.ndarray:
    return np.asarray(x, dtype=float)


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    n = len(arr)
    if n < period:
        return np.full(n, np.nan)
    k = 2.0 / (period + 1)
    out = np.empty(n)
    out[:period - 1] = np.nan
    out[period - 1] = arr[:period].mean()
    for i in range(period, n):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def _wma(arr: np.ndarray, period: int) -> np.ndarray:
    n = len(arr)
    if n < period:
        return np.full(n, np.nan)
    weights = np.arange(1, period + 1, dtype=float)
    w_sum = weights.sum()
    out = np.full(n, np.nan)
    for i in range(period - 1, n):
        out[i] = np.dot(arr[i - period + 1:i + 1], weights) / w_sum
    return out


def _atr_arr(highs: np.ndarray, lows: np.ndarray,
             closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    tr = np.empty(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    return _ema(tr, period)


# ── HMA ───────────────────────────────────────────────────────────────────────

def hma(closes: Union[List, np.ndarray], period: int) -> np.ndarray:
    """Hull Moving Average: WMA(2*WMA(n/2) − WMA(n)), length=sqrt(n)."""
    c = _arr(closes)
    if len(c) < period:
        return np.full(len(c), np.nan)
    half   = max(period // 2, 2)
    sqrt_p = max(int(np.sqrt(period)), 2)
    diff   = 2.0 * _wma(c, half) - _wma(c, period)
    return _wma(diff, sqrt_p)


def hma_direction(closes: Union[List, np.ndarray], period: int) -> int:
    """
    Dirección del HMA: +1 subiendo, -1 bajando, 0 plano.
    Compara los dos últimos valores válidos del HMA.
    """
    h = hma(closes, period)
    valid = h[~np.isnan(h)]
    if len(valid) < 2:
        return 0
    diff = valid[-1] - valid[-2]
    if diff > 0:
        return 1
    elif diff < 0:
        return -1
    return 0


def hma_crossover(fast_src: Union[List, np.ndarray],
                  slow_src: Union[List, np.ndarray],
                  fast_period: int, slow_period: int) -> int:
    """
    Detecta cruce del HMA rápido sobre el lento en la última vela.
    +1 = cruce alcista, -1 = cruce bajista, 0 = sin cruce.
    (fast_src y slow_src son normalmente el mismo array de closes.)
    """
    hf = hma(fast_src, fast_period)
    hs = hma(slow_src, slow_period)
    # Necesitamos al menos las dos últimas posiciones
    min_len = min(len(hf), len(hs))
    if min_len < 2:
        return 0
    hf2 = hf[-min_len:]
    hs2 = hs[-min_len:]
    prev_above = hf2[-2] > hs2[-2]
    curr_above = hf2[-1] > hs2[-1]
    if not prev_above and curr_above:
        return 1   # cruce alcista
    if prev_above and not curr_above:
        return -1  # cruce bajista
    return 0


# ── ATR scalars ───────────────────────────────────────────────────────────────

def atr_now(highs: Union[List, np.ndarray],
            lows:  Union[List, np.ndarray],
            closes: Union[List, np.ndarray],
            period: int = 14) -> float:
    """Último valor del ATR."""
    arr = _atr_arr(_arr(highs), _arr(lows), _arr(closes), period)
    v = arr[-1]
    return float(v) if not np.isnan(v) else 0.0


def atr_pct_now(highs: Union[List, np.ndarray],
                lows:  Union[List, np.ndarray],
                closes: Union[List, np.ndarray],
                period: int = 14) -> float:
    """ATR como porcentaje del precio actual."""
    c = _arr(closes)
    a = atr_now(highs, lows, closes, period)
    price = float(c[-1])
    return (a / price) if price > 0 else 0.0


def atr_percentile(highs: Union[List, np.ndarray],
                   lows:  Union[List, np.ndarray],
                   closes: Union[List, np.ndarray],
                   period: int = 14,
                   lookback: int = 100) -> float:
    """
    Percentil (0-100) del ATR actual respecto a los últimos `lookback` valores.
    Útil para saber si la volatilidad actual es alta o baja históricamente.
    """
    arr = _atr_arr(_arr(highs), _arr(lows), _arr(closes), period)
    valid = arr[~np.isnan(arr)]
    if len(valid) < 2:
        return 50.0
    window = valid[-lookback:]
    current = valid[-1]
    return float(np.sum(window <= current) / len(window) * 100)


# ── Bollinger Bands ───────────────────────────────────────────────────────────

def bollinger(closes: Union[List, np.ndarray],
              period: int = 20,
              std_mult: float = 2.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Devuelve (upper, mid, lower) como arrays numpy."""
    c = _arr(closes)
    n = len(c)
    upper = np.full(n, np.nan)
    mid   = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    for i in range(period - 1, n):
        w = c[i - period + 1:i + 1]
        m = w.mean()
        s = w.std(ddof=0)
        mid[i]   = m
        upper[i] = m + std_mult * s
        lower[i] = m - std_mult * s
    return upper, mid, lower


def bb_pct_b(closes: Union[List, np.ndarray],
             period: int = 20,
             std_mult: float = 2.0) -> float:
    """
    %B de Bollinger: posición del precio dentro de las bandas (0=lower, 1=upper).
    Devuelve el valor del último cierre.
    """
    c = _arr(closes)
    u, m, l = bollinger(c, period, std_mult)
    if np.isnan(u[-1]) or (u[-1] - l[-1]) == 0:
        return 0.5
    return float((c[-1] - l[-1]) / (u[-1] - l[-1]))


# ── RSI ───────────────────────────────────────────────────────────────────────

def rsi_now(closes: Union[List, np.ndarray], period: int = 14) -> float:
    """Último valor del RSI de Wilder."""
    c = _arr(closes)
    n = len(c)
    if n < period + 1:
        return 50.0
    deltas = np.diff(c)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = gains[:period].mean()
    avg_l = losses[:period].mean()
    for i in range(period, n - 1):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    rs = avg_g / avg_l if avg_l > 0 else 0.0
    return float(100.0 - 100.0 / (1.0 + rs))


def stoch_rsi(closes: Union[List, np.ndarray],
              rsi_period: int = 14,
              stoch_period: int = 14,
              k_smooth: int = 3,
              d_smooth: int = 3) -> Tuple[float, float]:
    """
    Stochastic RSI. Devuelve (%K, %D) escalares del último bar (0-100).
    """
    c = _arr(closes)
    n = len(c)
    if n < rsi_period + stoch_period + 2:
        return 50.0, 50.0

    # Calcular serie RSI completa
    rsi_arr = np.full(n, np.nan)
    deltas = np.diff(c)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = gains[:rsi_period].mean()
    avg_l = losses[:rsi_period].mean()
    rs = avg_g / avg_l if avg_l > 0 else 0.0
    rsi_arr[rsi_period] = 100.0 - 100.0 / (1.0 + rs)
    for i in range(rsi_period, n - 1):
        avg_g = (avg_g * (rsi_period - 1) + gains[i]) / rsi_period
        avg_l = (avg_l * (rsi_period - 1) + losses[i]) / rsi_period
        rs = avg_g / avg_l if avg_l > 0 else 0.0
        rsi_arr[i + 1] = 100.0 - 100.0 / (1.0 + rs)

    # Stochastic sobre la serie RSI
    stoch_k = np.full(n, np.nan)
    for i in range(rsi_period + stoch_period - 1, n):
        window = rsi_arr[i - stoch_period + 1:i + 1]
        lo = np.nanmin(window)
        hi = np.nanmax(window)
        if hi != lo:
            stoch_k[i] = (rsi_arr[i] - lo) / (hi - lo) * 100.0
        else:
            stoch_k[i] = 50.0

    # Suavizado
    valid = ~np.isnan(stoch_k)
    stoch_k_filled = np.where(valid, stoch_k, 0.0)
    k_smooth_arr = _ema(stoch_k_filled, k_smooth)
    d_smooth_arr = _ema(k_smooth_arr, d_smooth)

    k_val = k_smooth_arr[-1]
    d_val = d_smooth_arr[-1]
    k_out = float(k_val) if not np.isnan(k_val) else 50.0
    d_out = float(d_val) if not np.isnan(d_val) else 50.0
    return k_out, d_out


def macd_vals(closes: Union[List, np.ndarray],
              fast: int = 12, slow: int = 26,
              signal_p: int = 9) -> Tuple[float, float, float]:
    """Devuelve (macd_line, signal_line, histogram) escalares del último bar."""
    c = _arr(closes)
    ema_f = _ema(c, fast)
    ema_s = _ema(c, slow)
    line  = ema_f - ema_s
    sig   = _ema(line, signal_p)
    hist  = line - sig
    return float(line[-1]), float(sig[-1]), float(hist[-1])


# ── ADX ───────────────────────────────────────────────────────────────────────

def adx_vals(highs: Union[List, np.ndarray],
             lows:  Union[List, np.ndarray],
             closes: Union[List, np.ndarray],
             period: int = 14) -> Tuple[float, float, float]:
    """
    Devuelve (ADX, +DI, -DI) escalares del último bar.
    ADX: 0-100. Por encima de 25 suele indicar tendencia.
    """
    h = _arr(highs)
    l = _arr(lows)
    c = _arr(closes)
    n = len(c)
    if n < period * 2:
        return 0.0, 0.0, 0.0

    tr   = np.empty(n)
    pdm  = np.empty(n)
    ndm  = np.empty(n)
    tr[0] = h[0] - l[0]
    pdm[0] = 0.0
    ndm[0] = 0.0

    for i in range(1, n):
        hl   = h[i] - l[i]
        hpc  = abs(h[i] - c[i - 1])
        lpc  = abs(l[i] - c[i - 1])
        tr[i] = max(hl, hpc, lpc)

        up   = h[i] - h[i - 1]
        down = l[i - 1] - l[i]
        pdm[i] = up   if (up > down and up > 0)   else 0.0
        ndm[i] = down if (down > up and down > 0) else 0.0

    # Smoothed using Wilder's method
    def wilder_smooth(arr: np.ndarray, p: int) -> np.ndarray:
        out = np.empty(n)
        out[:p] = np.nan
        out[p]  = arr[1:p + 1].sum()
        for i in range(p + 1, n):
            out[i] = out[i - 1] - out[i - 1] / p + arr[i]
        return out

    tr_s  = wilder_smooth(tr,  period)
    pdm_s = wilder_smooth(pdm, period)
    ndm_s = wilder_smooth(ndm, period)

    with np.errstate(divide='ignore', invalid='ignore'):
        pdi = np.where(tr_s > 0, 100.0 * pdm_s / tr_s, 0.0)
        ndi = np.where(tr_s > 0, 100.0 * ndm_s / tr_s, 0.0)
        dx  = np.where((pdi + ndi) > 0, 100.0 * np.abs(pdi - ndi) / (pdi + ndi), 0.0)

    adx_arr = wilder_smooth(dx, period)

    adx_v = adx_arr[-1]
    pdi_v = pdi[-1]
    ndi_v = ndi[-1]
    return (
        float(adx_v) if not np.isnan(adx_v) else 0.0,
        float(pdi_v) if not np.isnan(pdi_v) else 0.0,
        float(ndi_v) if not np.isnan(ndi_v) else 0.0,
    )


# ── Efficiency Ratio ─────────────────────────────────────────────────────────

def efficiency_ratio_now(closes: Union[List, np.ndarray],
                          period: int = 10) -> float:
    """
    Kaufman Efficiency Ratio: |net change| / sum(|bar changes|).
    1.0 = movimiento perfectamente direccional, 0.0 = ruido puro.
    """
    c = _arr(closes)
    if len(c) < period + 1:
        return 0.0
    window = c[-period - 1:]
    net    = abs(window[-1] - window[0])
    noise  = np.sum(np.abs(np.diff(window)))
    return float(net / noise) if noise > 0 else 0.0


# ── Market Regime ─────────────────────────────────────────────────────────────

def market_regime(highs: Union[List, np.ndarray],
                  lows:  Union[List, np.ndarray],
                  closes: Union[List, np.ndarray],
                  atr_fast: int = 7,
                  atr_slow: int = 50,
                  vol_threshold: float = 1.8) -> str:
    """
    Clasifica el mercado como REGIME_TRENDING, REGIME_RANGING o REGIME_VOLATILE.

    Lógica:
      - Si ATR rápido > ATR lento × vol_threshold → VOLATILE (expansión extrema)
      - Si ATR rápido / ATR lento >= 0.85          → TRENDING (expansión normal)
      - Else                                        → RANGING
    """
    h = _arr(highs)
    l = _arr(lows)
    c = _arr(closes)

    if len(c) < atr_slow + 1:
        return REGIME_RANGING

    atr_f_arr = _atr_arr(h, l, c, atr_fast)
    atr_s_arr = _atr_arr(h, l, c, atr_slow)
    atr_f = float(atr_f_arr[-1])
    atr_s = float(atr_s_arr[-1])

    if np.isnan(atr_f) or np.isnan(atr_s) or atr_s == 0:
        return REGIME_RANGING

    ratio = atr_f / atr_s
    if ratio >= vol_threshold:
        return REGIME_VOLATILE
    elif ratio >= 0.85:
        return REGIME_TRENDING
    return REGIME_RANGING


# ── Synthetic CVD ─────────────────────────────────────────────────────────────

def synthetic_cvd(opens:  Union[List, np.ndarray],
                  highs:  Union[List, np.ndarray],
                  lows:   Union[List, np.ndarray],
                  closes: Union[List, np.ndarray],
                  volumes: Union[List, np.ndarray],
                  period: int = 20) -> float:
    """
    CVD sintético normalizado [-1, 1].
    Delta por vela = vol × sign(close − open).
    """
    o = _arr(opens)
    c = _arr(closes)
    v = _arr(volumes)
    delta = np.where(c >= o, v, -v).astype(float)
    window_delta = delta[-period:]
    window_vol   = v[-period:]
    total_vol = float(window_vol.sum())
    if total_vol == 0:
        return 0.0
    return float(window_delta.sum() / total_vol)


def cvd_slope(opens:  Union[List, np.ndarray],
              highs:  Union[List, np.ndarray],
              lows:   Union[List, np.ndarray],
              closes: Union[List, np.ndarray],
              volumes: Union[List, np.ndarray],
              period: int = 20,
              slope_bars: int = 5) -> float:
    """
    Pendiente del CVD acumulado en las últimas `slope_bars` velas.
    Positivo = CVD subiendo (presión compradora), negativo = vendedora.
    """
    o = _arr(opens)
    c = _arr(closes)
    v = _arr(volumes)
    delta = np.where(c >= o, v, -v).astype(float)
    cvd_series = np.cumsum(delta)
    if len(cvd_series) < slope_bars + 1:
        return 0.0
    window = cvd_series[-(slope_bars + 1):]
    # Normalizar por volumen medio para hacerlo comparable entre activos
    avg_vol = float(v[-slope_bars:].mean()) if float(v[-slope_bars:].mean()) > 0 else 1.0
    slope = (window[-1] - window[0]) / (slope_bars * avg_vol)
    return float(slope)


def cvd_divergence(opens:  Union[List, np.ndarray],
                   highs:  Union[List, np.ndarray],
                   lows:   Union[List, np.ndarray],
                   closes: Union[List, np.ndarray],
                   volumes: Union[List, np.ndarray],
                   lookback: int = 10) -> int:
    """
    Divergencia CVD/precio:
      +1 = bullish divergence (precio baja pero CVD sube)
      -1 = bearish divergence (precio sube pero CVD baja)
       0 = sin divergencia
    """
    o = _arr(opens)
    c = _arr(closes)
    v = _arr(volumes)
    if len(c) < lookback + 1:
        return 0
    delta = np.where(c >= o, v, -v).astype(float)
    cvd_series = np.cumsum(delta)
    price_change = c[-1] - c[-lookback]
    cvd_change   = cvd_series[-1] - cvd_series[-lookback]
    if price_change < 0 and cvd_change > 0:
        return 1
    if price_change > 0 and cvd_change < 0:
        return -1
    return 0


# ── Order Flow Imbalance ──────────────────────────────────────────────────────

def orderflow_imbalance(opens:  Union[List, np.ndarray],
                         highs:  Union[List, np.ndarray],
                         lows:   Union[List, np.ndarray],
                         closes: Union[List, np.ndarray],
                         period: int = 20) -> float:
    """
    OFI sintético usando la proporción de velas alcistas vs bajistas ponderadas
    por su cuerpo relativo. Devuelve un valor 0-1.
    0.5 = equilibrio; >0.5 = presión compradora; <0.5 = vendedora.
    """
    o = _arr(opens)
    h = _arr(highs)
    l = _arr(lows)
    c = _arr(closes)
    n = min(period, len(c))
    o_w = o[-n:]
    h_w = h[-n:]
    l_w = l[-n:]
    c_w = c[-n:]
    ranges = h_w - l_w
    ranges = np.where(ranges == 0, 1e-10, ranges)
    body_pct = np.abs(c_w - o_w) / ranges  # 0-1, qué % del rango es cuerpo
    bull_pressure = np.where(c_w >= o_w, body_pct, 0.0).sum()
    bear_pressure = np.where(c_w < o_w,  body_pct, 0.0).sum()
    total = bull_pressure + bear_pressure
    if total == 0:
        return 0.5
    return float(bull_pressure / total)


# ── Volume ────────────────────────────────────────────────────────────────────

def volume_ratio_now(volumes: Union[List, np.ndarray],
                     period: int = 20) -> float:
    """
    Ratio volumen actual vs media de `period` velas anteriores.
    >1.0 = volumen sobre la media.
    """
    v = _arr(volumes)
    if len(v) < period + 1:
        return 1.0
    current  = float(v[-1])
    avg      = float(v[-period - 1:-1].mean())
    return (current / avg) if avg > 0 else 1.0


def volume_trend(volumes: Union[List, np.ndarray],
                 period: int = 5) -> int:
    """
    +1 = volumen creciente, -1 = decreciente, 0 = plano.
    Compara la media del último `period` con el anterior.
    """
    v = _arr(volumes)
    if len(v) < period * 2:
        return 0
    recent = float(v[-period:].mean())
    prev   = float(v[-period * 2:-period].mean())
    if recent > prev * 1.05:
        return 1
    elif recent < prev * 0.95:
        return -1
    return 0


# ── SMC — Liquidity Sweep ─────────────────────────────────────────────────────

def liquidity_sweep(highs:  Union[List, np.ndarray],
                    lows:   Union[List, np.ndarray],
                    closes: Union[List, np.ndarray],
                    lookback: int = 10) -> int:
    """
    Detecta barrido de liquidez en la última vela.
    +1 = bullish sweep (mínimos barridos y precio recuperado)
    -1 = bearish sweep (máximos barridos y precio revertido)
     0 = sin sweep
    """
    h = _arr(highs)
    l = _arr(lows)
    c = _arr(closes)
    if len(l) < lookback + 2:
        return 0

    # Bullish: penúltima vela perforó swing low, precio actual cerró por encima
    prev_low = float(l[-(lookback + 2):-2].min()) if lookback >= 1 else float(l[-3])
    if l[-2] < prev_low and c[-1] > prev_low:
        return 1

    # Bearish: penúltima vela perforó swing high, precio actual cerró por debajo
    prev_high = float(h[-(lookback + 2):-2].max()) if lookback >= 1 else float(h[-3])
    if h[-2] > prev_high and c[-1] < prev_high:
        return -1

    return 0


# ── VWAP ─────────────────────────────────────────────────────────────────────

def vwap_now(highs:   Union[List, np.ndarray],
             lows:    Union[List, np.ndarray],
             closes:  Union[List, np.ndarray],
             volumes: Union[List, np.ndarray]) -> float:
    """VWAP acumulado desde el inicio del array. Devuelve escalar."""
    h = _arr(highs)
    l = _arr(lows)
    c = _arr(closes)
    v = _arr(volumes)
    typical  = (h + l + c) / 3.0
    cum_tpv  = np.cumsum(typical * v)
    cum_vol  = np.cumsum(v)
    with np.errstate(invalid='ignore', divide='ignore'):
        vwap_arr = np.where(cum_vol > 0, cum_tpv / cum_vol, np.nan)
    val = vwap_arr[-1]
    return float(val) if not np.isnan(val) else float(c[-1])


# ── Pivot Points ──────────────────────────────────────────────────────────────

def pivot_points(highs:  Union[List, np.ndarray],
                 lows:   Union[List, np.ndarray],
                 closes: Union[List, np.ndarray]) -> dict:
    """
    Pivotes clásicos basados en la última vela completa (penúltima del array).
    Devuelve dict con claves: P, R1, R2, R3, S1, S2, S3.
    """
    h = _arr(highs)
    l = _arr(lows)
    c = _arr(closes)
    if len(c) < 2:
        price = float(c[-1]) if len(c) else 0.0
        return {k: price for k in ("P", "R1", "R2", "R3", "S1", "S2", "S3")}
    prev_h = float(h[-2])
    prev_l = float(l[-2])
    prev_c = float(c[-2])
    P  = (prev_h + prev_l + prev_c) / 3.0
    R1 = 2 * P - prev_l
    S1 = 2 * P - prev_h
    R2 = P + (prev_h - prev_l)
    S2 = P - (prev_h - prev_l)
    R3 = prev_h + 2 * (P - prev_l)
    S3 = prev_l - 2 * (prev_h - P)
    return {"P": P, "R1": R1, "R2": R2, "R3": R3, "S1": S1, "S2": S2, "S3": S3}


# ── Position Sizing ───────────────────────────────────────────────────────────

def risk_adjusted_size(balance: float,
                       risk_pct: float,
                       entry: float,
                       sl: float,
                       leverage: int = 10,
                       min_usdt: float = 5.0,
                       max_usdt: float = 100.0) -> float:
    """
    Tamaño de posición en USDT basado en riesgo fijo.
    risk_pct: fracción del balance a arriesgar (ej. 0.015 = 1.5%)
    El riesgo real = (entry - sl) / entry × size × leverage
    → size = (balance × risk_pct × leverage) / (|entry - sl| / entry × leverage)
           = balance × risk_pct / (|entry - sl| / entry)
    """
    if entry <= 0 or sl <= 0:
        return min_usdt
    sl_pct = abs(entry - sl) / entry
    if sl_pct == 0:
        return min_usdt
    size = (balance * risk_pct) / sl_pct
    return float(max(min_usdt, min(size, max_usdt)))


def vol_adjusted_size(base_size: float,
                      atr_pct: float,
                      target_vol_pct: float = 0.02) -> float:
    """
    Ajusta el tamaño por volatilidad.
    Si ATR% es el doble del objetivo, reduce el tamaño a la mitad.
    """
    if atr_pct <= 0:
        return base_size
    multiplier = target_vol_pct / atr_pct
    # Limitar entre 0.3× y 2× del tamaño base
    multiplier = max(0.3, min(multiplier, 2.0))
    return float(base_size * multiplier)


def confidence_size_multiplier(score: float,
                                min_score: float = 65.0,
                                max_score: float = 100.0) -> float:
    """
    Multiplica el tamaño según la confianza de la señal.
    score=65 → 0.7×; score=100 → 1.3×
    Lineal entre los extremos, con límites en [0.5, 1.5].
    """
    if score <= min_score:
        return 0.7
    if score >= max_score:
        return 1.3
    normalized = (score - min_score) / (max_score - min_score)  # 0-1
    mult = 0.7 + normalized * 0.6  # 0.7 → 1.3
    return float(max(0.5, min(mult, 1.5)))
