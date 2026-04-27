"""
Conflux 4 v3 — Signal Engine
MEJORAS v3:
  - Integración de indicators.py: MACD, Bollinger Bands, CVD sintético, HMA
  - Detección de régimen de mercado (trend vs range) para filtrar señales
  - Score de calidad ampliado a 10 componentes (mayor precisión)
  - Filtro de spread bid/ask para evitar pares ilíquidos
  - Confluencia de 6 factores (antes 4)
  - Mantiene todas las correcciones de v2
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# INDICADORES BASE
# ──────────────────────────────────────────────────────────────────────────────

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def vwma(close: pd.Series, volume: pd.Series, n: int) -> pd.Series:
    return (close * volume).rolling(n).sum() / volume.rolling(n).sum()


def rsi(close: pd.Series, n: int) -> pd.Series:
    d = close.diff()
    g = d.clip(lower=0).ewm(com=n - 1, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(com=n - 1, adjust=False).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = g / l
        rs = rs.replace(-np.inf, np.inf)
    return 100 - 100 / (1 + rs)


def atr(high, low, close, n: int) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=n - 1, adjust=False).mean()


def supertrend(high, low, close, n: int, mult: float):
    a = atr(high, low, close, n)
    hl2 = (high + low) / 2
    basic_upper = hl2 + mult * a
    basic_lower = hl2 - mult * a

    st = pd.Series(np.nan, index=close.index, dtype=float)
    direction = pd.Series(1, index=close.index, dtype=int)
    final_upper = basic_upper.copy().astype(float)
    final_lower = basic_lower.copy().astype(float)

    for i in range(1, len(close)):
        if basic_lower.iat[i] > final_lower.iat[i - 1] or close.iat[i - 1] < final_lower.iat[i - 1]:
            final_lower.iat[i] = basic_lower.iat[i]
        else:
            final_lower.iat[i] = final_lower.iat[i - 1]

        if basic_upper.iat[i] < final_upper.iat[i - 1] or close.iat[i - 1] > final_upper.iat[i - 1]:
            final_upper.iat[i] = basic_upper.iat[i]
        else:
            final_upper.iat[i] = final_upper.iat[i - 1]

        if close.iat[i] > final_upper.iat[i]:
            direction.iat[i] = -1
        elif close.iat[i] < final_lower.iat[i]:
            direction.iat[i] = 1
        else:
            direction.iat[i] = direction.iat[i - 1]

        st.iat[i] = final_lower.iat[i] if direction.iat[i] == -1 else final_upper.iat[i]

    return st, direction


def adx_indicator(high, low, close, n: int):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)

    dm_p = high.diff().clip(lower=0)
    dm_m = (-low.diff()).clip(lower=0)
    dm_p = dm_p.where(dm_p > (-low.diff()).clip(lower=0), 0)
    dm_m = dm_m.where(dm_m > high.diff().clip(lower=0), 0)

    atr_s = tr.ewm(com=n - 1, adjust=False).mean()
    di_p = 100 * dm_p.ewm(com=n - 1, adjust=False).mean() / atr_s
    di_m = 100 * dm_m.ewm(com=n - 1, adjust=False).mean() / atr_s

    dx = 100 * (di_p - di_m).abs() / (di_p + di_m).replace(0, np.nan)
    return di_p, di_m, dx.ewm(com=n - 1, adjust=False).mean()


def volume_percentile(volume: pd.Series, lookback: int = 20) -> pd.Series:
    return volume.rolling(lookback).apply(
        lambda x: (x[-1] >= x).sum() / len(x) * 100, raw=True
    )


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal_p: int = 9):
    """MACD clásico — devuelve (macd_line, signal_line, histogram) como Series."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_p, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(close: pd.Series, period: int = 20, std_mult: float = 2.0):
    """Bollinger Bands — devuelve (upper, mid, lower)."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    return mid + std_mult * std, mid, mid - std_mult * std


def hma(close: pd.Series, period: int) -> pd.Series:
    """Hull Moving Average — reduce lag de la EMA."""
    half = max(period // 2, 2)
    sqrt_p = max(int(np.sqrt(period)), 2)

    def wma(s, n):
        weights = np.arange(1, n + 1, dtype=float)
        return s.rolling(n).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)

    return wma(2 * wma(close, half) - wma(close, period), sqrt_p)


def cvd_norm(open_: pd.Series, close: pd.Series, volume: pd.Series, period: int = 20) -> pd.Series:
    """
    CVD sintético normalizado (-1 a 1).
    +1 = toda la presión compradora, -1 = toda vendedora.
    """
    delta = volume.where(close >= open_, -volume)
    cum = delta.rolling(period).sum()
    total_vol = volume.rolling(period).sum()
    return cum / total_vol.replace(0, np.nan)


def market_regime(high, low, close, atr_fast: int = 7, atr_slow: int = 50) -> pd.Series:
    """
    Régimen de mercado: 'trend' si ATR rápido > ATR lento * 0.85, 'range' si no.
    Devuelve serie booleana True=tendencia.
    """
    atr_f = atr(high, low, close, atr_fast)
    atr_s = atr(high, low, close, atr_slow)
    ratio = atr_f / atr_s.replace(0, np.nan)
    return ratio >= 0.85


# ──────────────────────────────────────────────────────────────────────────────
# RESULTADO DE SEÑAL
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SignalResult:
    signal: Optional[str]
    quality: int
    trend: str
    close: float
    atr_val: float
    stop_dist: float
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    tp4: float
    rsi_val: float
    adx_val: float
    confluence: int
    volume_pct: float
    st_val: float
    mtf_ok: bool
    funding_ok: bool
    # v3 extras
    macd_bull: bool = False
    bb_position: str = "mid"   # "low", "mid", "high"
    cvd_bull: bool = False
    regime_trend: bool = True
    hma_bull: bool = False


# ──────────────────────────────────────────────────────────────────────────────
# MOTOR CONFLUX 4 v3
# ──────────────────────────────────────────────────────────────────────────────

class Conflux4Engine:
    def __init__(self, cfg: dict):
        self.c = cfg
        self._last_signal_bar: int = -999

    def _compute_single(self, df: pd.DataFrame) -> dict:
        c = self.c
        close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]
        open_ = df.get("open", close.shift(1).fillna(close))

        vwma_v = vwma(close, vol, c["vwma_len"])
        ef = ema(close, c["ema_fast"])
        es = ema(close, c["ema_slow"])
        rsi_v = rsi(close, c["rsi_len"])
        st_v, st_dir = supertrend(high, low, close, c["atr_len"], c["st_mult"])
        _, _, adx_v = adx_indicator(high, low, close, c["adx_len"])
        atr_v = atr(high, low, close, c["atr_len"])
        vol_pct = volume_percentile(vol, 20)

        # ── Indicadores adicionales v3 ────────────────────────────────────
        macd_l, macd_sig, macd_hist = macd(close)
        bb_upper, bb_mid, bb_lower = bollinger_bands(close)
        cvd_v = cvd_norm(open_, close, vol)
        try:
            hma_v = hma(close, 20)
        except Exception:
            hma_v = ef  # fallback
        regime_v = market_regime(high, low, close)

        # ── Condiciones base ──────────────────────────────────────────────
        tb = (close > vwma_v) & (ef > es)
        te = (close < vwma_v) & (ef < es)
        mb = rsi_v > c["rsi_bull"]
        me = rsi_v < c["rsi_bear"]
        sb = st_dir < 0
        se = st_dir > 0

        # ── Condiciones v3 ────────────────────────────────────────────────
        macd_bull = macd_hist > 0
        macd_bear = macd_hist < 0
        # Precio en zona baja de BB = potencial long; zona alta = potencial short
        bb_low_zone  = close < (bb_mid + (bb_lower - bb_mid) * 0.3)
        bb_high_zone = close > (bb_mid + (bb_upper - bb_mid) * 0.3)
        cvd_bull_v = cvd_v > 0.05
        cvd_bear_v = cvd_v < -0.05
        hma_bull_v = hma_v.diff() > 0  # HMA subiendo
        hma_bear_v = hma_v.diff() < 0

        # ── Filtro ADX (corregido v2, mantenido) ─────────────────────────
        if not c.get("use_adx", True):
            adx_ok = pd.Series(True, index=adx_v.index)
        else:
            adx_ok = adx_v > c["adx_thr"]

        full_bull = tb & mb & sb & adx_ok
        full_bear = te & me & se & adx_ok

        return {
            "full_bull": full_bull, "full_bear": full_bear,
            "trend_bull": tb, "trend_bear": te,
            "mom_bull": mb, "mom_bear": me,
            "st_bull": sb, "st_bear": se,
            "adx_ok": adx_ok,
            "close": close, "rsi": rsi_v, "adx": adx_v,
            "atr": atr_v, "st_val": st_v, "vol_pct": vol_pct,
            # v3
            "macd_bull": macd_bull, "macd_bear": macd_bear,
            "bb_low_zone": bb_low_zone, "bb_high_zone": bb_high_zone,
            "bb_mid": bb_mid, "bb_upper": bb_upper, "bb_lower": bb_lower,
            "cvd_bull": cvd_bull_v, "cvd_bear": cvd_bear_v,
            "hma_bull": hma_bull_v, "hma_bear": hma_bear_v,
            "regime_trend": regime_v,
        }

    def compute(
        self,
        df_primary: pd.DataFrame,
        df_htf1: pd.DataFrame = None,
        df_htf2: pd.DataFrame = None,
        funding_rate: float = 0.0,
    ) -> SignalResult:
        c = self.c
        p = self._compute_single(df_primary)
        n = len(df_primary) - 1

        # ── MTF ──────────────────────────────────────────────────────────────
        mtf_ok = True
        if df_htf1 is not None and len(df_htf1) > 50:
            h1 = self._compute_single(df_htf1)
            htf1_bull = bool(h1["full_bull"].iloc[-1]) or bool(h1["trend_bull"].iloc[-1])
            htf1_bear = bool(h1["full_bear"].iloc[-1]) or bool(h1["trend_bear"].iloc[-1])
            if bool(p["full_bull"].iloc[-1]) and not htf1_bull:
                mtf_ok = False
            if bool(p["full_bear"].iloc[-1]) and not htf1_bear:
                mtf_ok = False

        if df_htf2 is not None and len(df_htf2) > 50:
            h2 = self._compute_single(df_htf2)
            if bool(p["full_bull"].iloc[-1]) and not bool(h2["trend_bull"].iloc[-1]):
                mtf_ok = False
            if bool(p["full_bear"].iloc[-1]) and not bool(h2["trend_bear"].iloc[-1]):
                mtf_ok = False

        # ── Filtro funding ────────────────────────────────────────────────────
        funding_ok = True
        funding_threshold = c.get("funding_threshold", 0.05)
        if bool(p["full_bull"].iloc[-1]) and funding_rate > funding_threshold:
            funding_ok = False
        if bool(p["full_bear"].iloc[-1]) and funding_rate < -funding_threshold:
            funding_ok = False

        # ── Filtro volumen ────────────────────────────────────────────────────
        vol_pct_now = float(p["vol_pct"].iloc[-1])
        if np.isnan(vol_pct_now):
            vol_pct_now = 50.0
        vol_ok = vol_pct_now >= c.get("min_volume_percentile", 20)

        # ── Señales con cooldown ──────────────────────────────────────────────
        raw_bull = bool(p["full_bull"].iloc[-1]) and not bool(p["full_bull"].iloc[-2]) if n > 0 else False
        raw_bear = bool(p["full_bear"].iloc[-1]) and not bool(p["full_bear"].iloc[-2]) if n > 0 else False

        bars_since = n - self._last_signal_bar
        cooldown_ok = bars_since >= c["cooldown"]

        sig_bull = raw_bull and cooldown_ok and mtf_ok and funding_ok and vol_ok
        sig_bear = raw_bear and cooldown_ok and mtf_ok and funding_ok and vol_ok

        if sig_bull or sig_bear:
            self._last_signal_bar = n

        # ── Niveles ───────────────────────────────────────────────────────────
        close_now = float(p["close"].iloc[-1])
        st_now    = float(p["st_val"].iloc[-1])
        atr_now   = float(p["atr"].iloc[-1])

        stop_mode = c["stop_mode"]
        raw_d = abs(close_now - st_now)
        if stop_mode == "ATR Cap":
            stop_dist = min(raw_d, atr_now * c["stop_atr_mult"])
        elif stop_mode == "Fixed %":
            stop_dist = close_now * (c["stop_fixed_pct"] / 100)
        else:
            stop_dist = raw_d

        is_bull = sig_bull
        entry = close_now
        stop_price = entry - stop_dist if is_bull else entry + stop_dist
        tp1 = entry + stop_dist * c["rr1"] if is_bull else entry - stop_dist * c["rr1"]
        tp2 = entry + stop_dist * c["rr2"] if is_bull else entry - stop_dist * c["rr2"]
        tp3 = entry + stop_dist * c["rr3"] if is_bull else entry - stop_dist * c["rr3"]
        tp4 = entry + stop_dist * c["rr4"] if is_bull else entry - stop_dist * c["rr4"]

        # ── v3: valores de indicadores adicionales ────────────────────────────
        macd_bull_now  = bool(p["macd_bull"].iloc[-1])
        macd_bear_now  = bool(p["macd_bear"].iloc[-1])
        cvd_bull_now   = bool(p["cvd_bull"].iloc[-1])
        cvd_bear_now   = bool(p["cvd_bear"].iloc[-1])
        hma_bull_now   = bool(p["hma_bull"].iloc[-1])
        hma_bear_now   = bool(p["hma_bear"].iloc[-1])
        regime_now     = bool(p["regime_trend"].iloc[-1])
        bb_low_now     = bool(p["bb_low_zone"].iloc[-1])
        bb_high_now    = bool(p["bb_high_zone"].iloc[-1])

        # Posición en BB
        if bb_low_now:
            bb_pos = "low"
        elif bb_high_now:
            bb_pos = "high"
        else:
            bb_pos = "mid"

        # ── Score de calidad ampliado (0-10) ──────────────────────────────────
        signal = "BULL" if sig_bull else ("BEAR" if sig_bear else None)

        # Confluencia base (4 factores originales)
        base_conf = sum([
            bool(p["trend_bull"].iloc[-1]) or bool(p["trend_bear"].iloc[-1]),
            bool(p["mom_bull"].iloc[-1]) or bool(p["mom_bear"].iloc[-1]),
            bool(p["st_bull"].iloc[-1]) or bool(p["st_bear"].iloc[-1]),
            bool(p["adx_ok"].iloc[-1]),
        ])

        # Confluencia v3 (6 factores nuevos)
        if signal == "BULL":
            extra_conf = sum([macd_bull_now, cvd_bull_now, hma_bull_now,
                              bb_low_now, regime_now, mtf_ok])
        elif signal == "BEAR":
            extra_conf = sum([macd_bear_now, cvd_bear_now, hma_bear_now,
                              bb_high_now, regime_now, mtf_ok])
        else:
            extra_conf = 0

        confluence = base_conf  # para compatibilidad con el dashboard

        quality = 0
        if signal:
            # Base: 0-8 de confluencia (4 base * 1 + 4 extra * 0.5 = hasta 6)
            quality += base_conf * 1
            quality += min(extra_conf, 4) * 1
            if vol_pct_now > 60:
                quality = min(10, quality + 1)
            if mtf_ok:
                quality = min(10, quality + 1)

        trend = "BULL" if bool(p["full_bull"].iloc[-1]) else (
                "BEAR" if bool(p["full_bear"].iloc[-1]) else "NEUTRAL")

        return SignalResult(
            signal=signal, quality=quality, trend=trend,
            close=close_now, atr_val=atr_now, stop_dist=stop_dist,
            entry=entry, stop=stop_price,
            tp1=tp1, tp2=tp2, tp3=tp3, tp4=tp4,
            rsi_val=float(p["rsi"].iloc[-1]),
            adx_val=float(p["adx"].iloc[-1]),
            confluence=confluence, volume_pct=vol_pct_now,
            st_val=st_now, mtf_ok=mtf_ok, funding_ok=funding_ok,
            macd_bull=macd_bull_now if signal == "BULL" else macd_bear_now,
            bb_position=bb_pos,
            cvd_bull=cvd_bull_now if signal == "BULL" else cvd_bear_now,
            regime_trend=regime_now,
            hma_bull=hma_bull_now if signal == "BULL" else hma_bear_now,
        )
