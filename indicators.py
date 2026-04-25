"""
utils/indicators.py — Indicadores técnicos en numpy (sin pandas en el hot path).
Todos reciben arrays numpy y devuelven escalares o arrays.
Velocidad máxima: no hay copies innecesarias, operaciones vectorizadas.
"""

import numpy as np
from typing import Tuple, Optional


# ── EMA ──────────────────────────────────────────────────────────────────────

def ema(arr: np.ndarray, period: int) -> np.ndarray:
    """EMA usando el multiplicador estándar 2/(N+1). O(n), sin pandas."""
    if len(arr) < period:
        return np.full(len(arr), np.nan)
    k = 2.0 / (period + 1)
    out = np.empty(len(arr))
    out[:period - 1] = np.nan
    out[period - 1] = arr[:period].mean()
    for i in range(period, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def ema_last(arr: np.ndarray, period: int) -> float:
    """Solo el último valor de la EMA. Más rápido para el hot path."""
    result = ema(arr, period)
    return float(result[-1]) if not np.isnan(result[-1]) else float("nan")




# ── HMA (Hull Moving Average) ─────────────────────────────────────────────────

def wma(arr: np.ndarray, period: int) -> np.ndarray:
    """Weighted Moving Average — pesos lineales, más reciente pesa más."""
    n = len(arr)
    if n < period:
        return np.full(n, np.nan)
    weights = np.arange(1, period + 1, dtype=float)
    out = np.full(n, np.nan)
    for i in range(period - 1, n):
        out[i] = np.dot(arr[i - period + 1:i + 1], weights) / weights.sum()
    return out


def wma_last(arr: np.ndarray, period: int) -> float:
    result = wma(arr, period)
    return float(result[-1]) if not np.isnan(result[-1]) else float("nan")


def hma(arr: np.ndarray, period: int) -> np.ndarray:
    """
    Hull Moving Average: HMA(n) = WMA(2*WMA(n/2) − WMA(n)), length=sqrt(n).
    Reduce el lag de la EMA manteniendo suavidad.
    """
    if len(arr) < period:
        return np.full(len(arr), np.nan)
    half = max(period // 2, 2)
    sqrt_p = max(int(np.sqrt(period)), 2)
    wma_half = wma(arr, half)
    wma_full = wma(arr, period)
    diff = 2.0 * wma_half - wma_full
    return wma(diff, sqrt_p)


def hma_last(arr: np.ndarray, period: int) -> float:
    """Solo el último valor del HMA."""
    result = hma(arr, period)
    return float(result[-1]) if not np.isnan(result[-1]) else float("nan")

# ── ATR ──────────────────────────────────────────────────────────────────────

def atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
        period: int = 14) -> np.ndarray:
    """ATR usando EMA del True Range."""
    n = len(closes)
    tr = np.empty(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
    return ema(tr, period)


def atr_last(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
             period: int = 14) -> float:
    result = atr(highs, lows, closes, period)
    return float(result[-1]) if not np.isnan(result[-1]) else float("nan")


# ── Swing High / Low ─────────────────────────────────────────────────────────

def swing_high(highs: np.ndarray, lookback: int = 5) -> float:
    """Máximo más alto de las últimas `lookback` velas (excluye la actual)."""
    if len(highs) < lookback + 1:
        return float(highs.max())
    return float(highs[-(lookback + 1):-1].max())


def swing_low(lows: np.ndarray, lookback: int = 5) -> float:
    """Mínimo más bajo de las últimas `lookback` velas (excluye la actual)."""
    if len(lows) < lookback + 1:
        return float(lows.min())
    return float(lows[-(lookback + 1):-1].min())


# ── Volume SMA ────────────────────────────────────────────────────────────────

def vol_sma(volumes: np.ndarray, period: int = 20) -> float:
    """Media de volumen."""
    if len(volumes) < period:
        return float(volumes.mean())
    return float(volumes[-period:].mean())


# ── Synthetic CVD ─────────────────────────────────────────────────────────────

def cvd(opens: np.ndarray, closes: np.ndarray, volumes: np.ndarray,
        period: int = 20) -> Tuple[np.ndarray, float]:
    """
    CVD sintético: aproximación del Cumulative Volume Delta usando velas OHLCV.
    Delta por vela = volumen * signo(close - open).
    Acumulado sobre `period` velas.
    Devuelve (serie cvd, valor actual normalizado -1..1).
    """
    delta = np.where(closes >= opens, volumes, -volumes).astype(float)
    if len(delta) < period:
        cvd_series = np.cumsum(delta)
    else:
        cvd_series = np.convolve(delta, np.ones(period), mode='full')[:len(delta)]

    last_cvd = float(cvd_series[-1])
    # Normalizar respecto al volumen total del período para comparar entre activos
    total_vol = float(volumes[-min(period, len(volumes)):].sum())
    cvd_norm = last_cvd / total_vol if total_vol > 0 else 0.0
    return cvd_series, cvd_norm


def cvd_trend(opens: np.ndarray, closes: np.ndarray, volumes: np.ndarray,
              period: int = 20) -> str:
    """Retorna 'bull', 'bear' o 'neutral' según CVD normalizado."""
    _, norm = cvd(opens, closes, volumes, period)
    if norm > 0.10:
        return "bull"
    elif norm < -0.10:
        return "bear"
    return "neutral"


# ── Market Regime ─────────────────────────────────────────────────────────────

def regime(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
           atr_fast: int = 7, atr_slow: int = 50) -> str:
    """
    Detecta si el mercado está en tendencia ('trend') o rango ('range').
    Lógica: si ATR rápido > ATR lento * 0.85, hay expansión (tendencia).
    """
    if len(closes) < atr_slow + 1:
        return "unknown"
    atr_f = atr_last(highs, lows, closes, atr_fast)
    atr_s = atr_last(highs, lows, closes, atr_slow)
    if np.isnan(atr_f) or np.isnan(atr_s) or atr_s == 0:
        return "unknown"
    ratio = atr_f / atr_s
    return "trend" if ratio >= 0.85 else "range"


# ── Liquidity Sweep ───────────────────────────────────────────────────────────

def has_liquidity_sweep_bull(lows: np.ndarray, lookback: int = 10) -> bool:
    """
    ¿Se barrió liquidez bajo el mínimo anterior antes de recuperar?
    Señal: la penúltima vela hizo mínimo < swing_low, y el close actual > swing_low.
    """
    if len(lows) < lookback + 2:
        return False
    prev_low = swing_low(lows[:-2], lookback)
    swept = lows[-2] < prev_low          # penúltima vela barrió
    recovered = lows[-1] > prev_low      # actual recuperó
    return bool(swept and recovered)


def has_liquidity_sweep_bear(highs: np.ndarray, lookback: int = 10) -> bool:
    """Barrido de liquidez sobre el máximo anterior."""
    if len(highs) < lookback + 2:
        return False
    prev_high = swing_high(highs[:-2], lookback)
    swept = highs[-2] > prev_high
    recovered = highs[-1] < prev_high
    return bool(swept and recovered)


# ── Kelly Sizing ──────────────────────────────────────────────────────────────

def kelly_fraction(win_rate: float, rr_ratio: float,
                   max_fraction: float = 0.25) -> float:
    """
    Fracción de Kelly = (win_rate * rr_ratio - loss_rate) / rr_ratio.
    Limitada a max_fraction para evitar over-betting.
    """
    loss_rate = 1.0 - win_rate
    if rr_ratio <= 0:
        return 0.0
    k = (win_rate * rr_ratio - loss_rate) / rr_ratio
    return max(0.0, min(k, max_fraction))


def position_size_usdt(balance: float, risk_pct: float,
                       sl_distance_pct: float, leverage: int,
                       base_size: float) -> float:
    """
    Tamaño de posición basado en riesgo máximo en USDT.
    risk_pct: fracción del balance a arriesgar (ej. 0.015 = 1.5%)
    sl_distance_pct: distancia del SL como fracción del precio (ej. 0.005)
    """
    if sl_distance_pct <= 0:
        return base_size
    risk_usdt = balance * risk_pct
    size = (risk_usdt / sl_distance_pct) / leverage
    # Limitar entre base_size y 5x base para no sobre-escalar
    return max(base_size, min(size, base_size * 5.0))


# ── RSI ───────────────────────────────────────────────────────────────────────

def rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """RSI clásico de Wilder."""
    n = len(closes)
    out = np.full(n, np.nan)
    if n < period + 1:
        return out
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    for i in range(period, n - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 0.0
        out[i + 1] = 100.0 - (100.0 / (1.0 + rs))
    return out


def rsi_last(closes: np.ndarray, period: int = 14) -> float:
    result = rsi(closes, period)
    v = result[-1]
    return float(v) if not np.isnan(v) else float("nan")


# ── MACD ──────────────────────────────────────────────────────────────────────

def macd(closes: np.ndarray,
         fast: int = 12, slow: int = 26, signal_p: int = 9):
    """Devuelve (macd_line, signal_line, histogram) como arrays."""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal_p)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def macd_last(closes: np.ndarray,
              fast: int = 12, slow: int = 26, signal_p: int = 9):
    """Devuelve (macd, signal, hist) escalares del último bar."""
    ml, sl, hist = macd(closes, fast, slow, signal_p)
    return float(ml[-1]), float(sl[-1]), float(hist[-1])


# ── Bollinger Bands ───────────────────────────────────────────────────────────

def bollinger(closes: np.ndarray, period: int = 20,
              std_mult: float = 2.0):
    """Devuelve (upper, mid, lower) como arrays."""
    n = len(closes)
    upper = np.full(n, np.nan)
    mid   = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    for i in range(period - 1, n):
        window = closes[i - period + 1:i + 1]
        m = window.mean()
        s = window.std(ddof=0)
        mid[i]   = m
        upper[i] = m + std_mult * s
        lower[i] = m - std_mult * s
    return upper, mid, lower


def bollinger_last(closes: np.ndarray, period: int = 20,
                   std_mult: float = 2.0):
    u, m, l = bollinger(closes, period, std_mult)
    return float(u[-1]), float(m[-1]), float(l[-1])


# ── VWAP (intraday approx) ────────────────────────────────────────────────────

def vwap(highs: np.ndarray, lows: np.ndarray,
         closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    """VWAP acumulado desde el inicio del array."""
    typical = (highs + lows + closes) / 3.0
    cum_tpv = np.cumsum(typical * volumes)
    cum_vol = np.cumsum(volumes)
    with np.errstate(invalid='ignore', divide='ignore'):
        out = np.where(cum_vol > 0, cum_tpv / cum_vol, np.nan)
    return out


def vwap_last(highs, lows, closes, volumes) -> float:
    result = vwap(np.array(highs), np.array(lows),
                  np.array(closes), np.array(volumes))
    v = result[-1]
    return float(v) if not np.isnan(v) else float("nan")


# ── Stochastic ────────────────────────────────────────────────────────────────

def stochastic(highs: np.ndarray, lows: np.ndarray,
               closes: np.ndarray, k_period: int = 14,
               d_period: int = 3):
    """Devuelve (%K, %D) como arrays."""
    n = len(closes)
    k = np.full(n, np.nan)
    for i in range(k_period - 1, n):
        h = highs[i - k_period + 1:i + 1].max()
        l = lows[i - k_period + 1:i + 1].min()
        k[i] = (closes[i] - l) / (h - l) * 100.0 if h != l else 50.0
    d = ema(k, d_period)
    return k, d


# ── Donchian Channel ──────────────────────────────────────────────────────────

def donchian(highs: np.ndarray, lows: np.ndarray,
             period: int = 20):
    """Devuelve (upper, lower) canal de Donchian."""
    n = len(highs)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    for i in range(period - 1, n):
        upper[i] = highs[i - period + 1:i + 1].max()
        lower[i] = lows[i - period + 1:i + 1].min()
    return upper, lower


# ── SuperTrend ────────────────────────────────────────────────────────────────

def supertrend(highs: np.ndarray, lows: np.ndarray,
               closes: np.ndarray, period: int = 10,
               mult: float = 3.0):
    """
    SuperTrend clásico.
    Devuelve (supertrend_arr, direction_arr) donde direction: 1=bull, -1=bear.
    """
    atr_arr = atr(highs, lows, closes, period)
    hl2 = (highs + lows) / 2.0
    upper_band = hl2 + mult * atr_arr
    lower_band = hl2 - mult * atr_arr
    n = len(closes)
    st   = np.full(n, np.nan)
    dire = np.ones(n)
    for i in range(1, n):
        if np.isnan(atr_arr[i]):
            continue
        # Ajustar bandas
        if lower_band[i] > lower_band[i-1] or closes[i-1] < lower_band[i-1]:
            lower_band[i] = lower_band[i]
        else:
            lower_band[i] = lower_band[i-1]
        if upper_band[i] < upper_band[i-1] or closes[i-1] > upper_band[i-1]:
            upper_band[i] = upper_band[i]
        else:
            upper_band[i] = upper_band[i-1]
        # Dirección
        if np.isnan(st[i-1]):
            st[i] = lower_band[i]
            dire[i] = 1
        elif st[i-1] == upper_band[i-1]:
            if closes[i] <= upper_band[i]:
                st[i], dire[i] = upper_band[i], -1
            else:
                st[i], dire[i] = lower_band[i], 1
        else:
            if closes[i] >= lower_band[i]:
                st[i], dire[i] = lower_band[i], 1
            else:
                st[i], dire[i] = upper_band[i], -1
    return st, dire


def supertrend_last(highs, lows, closes, period=10, mult=3.0):
    st, dire = supertrend(np.array(highs), np.array(lows),
                          np.array(closes), period, mult)
    return float(st[-1]), int(dire[-1])

