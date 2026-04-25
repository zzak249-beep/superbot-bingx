"""
Conflux 4 v2 — Signal Engine
Mejoras vs v1:
  - Confirmación multi-timeframe (MTF): 15m necesita acuerdo en 1h y 4h
  - Filtro de funding rate (evita entrar contra el funding extremo)
  - Volume spike filter (señales en baja liquidez descartadas)
  - Score de calidad de señal 0-10
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
    """
    RSI con manejo correcto de edge cases:
    - loss=0 (solo velas alcistas) → RS=+inf → RSI=100
    - gain=0 (solo velas bajistas) → RS=0   → RSI=0
    - ambos=0 (serie plana)        → RSI=NaN (correcto)
    """
    d = close.diff()
    g = d.clip(lower=0).ewm(com=n - 1, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(com=n - 1, adjust=False).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = g / l                          # puede producir ±inf o NaN
        rs = rs.replace(-np.inf, np.inf)    # -inf (g>0, l=-0.0) → +inf → RSI=100
    return 100 - 100 / (1 + rs)


def atr(high, low, close, n: int) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=n - 1, adjust=False).mean()


def supertrend(high, low, close, n: int, mult: float):
    """
    Supertrend — replica exacta de Pine Script ta.supertrend().
    direction: -1 = bull (precio sobre ST), 1 = bear (precio bajo ST)
    """
    a = atr(high, low, close, n)
    hl2 = (high + low) / 2

    basic_upper = hl2 + mult * a
    basic_lower = hl2 - mult * a

    st = pd.Series(np.nan, index=close.index, dtype=float)
    direction = pd.Series(1, index=close.index, dtype=int)
    final_upper = basic_upper.copy().astype(float)
    final_lower = basic_lower.copy().astype(float)

    for i in range(1, len(close)):
        # Lower band sube solo si el precio anterior estaba encima
        if basic_lower.iat[i] > final_lower.iat[i - 1] or close.iat[i - 1] < final_lower.iat[i - 1]:
            final_lower.iat[i] = basic_lower.iat[i]
        else:
            final_lower.iat[i] = final_lower.iat[i - 1]

        # Upper band baja solo si el precio anterior estaba debajo
        if basic_upper.iat[i] < final_upper.iat[i - 1] or close.iat[i - 1] > final_upper.iat[i - 1]:
            final_upper.iat[i] = basic_upper.iat[i]
        else:
            final_upper.iat[i] = final_upper.iat[i - 1]

        # Flip de dirección cuando el precio cruza una banda
        if close.iat[i] > final_upper.iat[i]:
            direction.iat[i] = -1   # bull
        elif close.iat[i] < final_lower.iat[i]:
            direction.iat[i] = 1    # bear
        else:
            direction.iat[i] = direction.iat[i - 1]

        # Valor del ST: lower band si bull, upper band si bear
        st.iat[i] = final_lower.iat[i] if direction.iat[i] == -1 else final_upper.iat[i]

    return st, direction


def adx(high, low, close, n: int):
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
    """Rango percentil del volumen actual vs últimas N velas (0-100)."""
    return volume.rolling(lookback).apply(
        lambda x: (x[-1] >= x).sum() / len(x) * 100, raw=True
    )


# ──────────────────────────────────────────────────────────────────────────────
# RESULTADO DE SEÑAL
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SignalResult:
    signal: Optional[str]       # 'BULL' | 'BEAR' | None
    quality: int                # 0-10 (score de calidad)
    trend: str                  # 'BULL' | 'BEAR' | 'NEUTRAL'
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
    confluence: int             # 0-4 filtros alineados
    volume_pct: float           # percentil de volumen (0-100)
    st_val: float
    mtf_ok: bool                # confirmación multi-TF
    funding_ok: bool            # filtro funding rate pasado


# ──────────────────────────────────────────────────────────────────────────────
# MOTOR CONFLUX 4 v2
# ──────────────────────────────────────────────────────────────────────────────

class Conflux4Engine:
    def __init__(self, cfg: dict):
        self.c = cfg
        self._last_signal_bar: int = -999

    # ── Calcula señales en un único DataFrame ──────────────────────────────
    def _compute_single(self, df: pd.DataFrame) -> dict:
        c = self.c
        close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]

        vwma_v = vwma(close, vol, c["vwma_len"])
        ef = ema(close, c["ema_fast"])
        es = ema(close, c["ema_slow"])
        rsi_v = rsi(close, c["rsi_len"])
        st_v, st_dir = supertrend(high, low, close, c["atr_len"], c["st_mult"])
        _, _, adx_v = adx(high, low, close, c["adx_len"])
        atr_v = atr(high, low, close, c["atr_len"])
        vol_pct = volume_percentile(vol, 20)

        tb = (close > vwma_v) & (ef > es)
        te = (close < vwma_v) & (ef < es)
        mb = rsi_v > c["rsi_bull"]
        me = rsi_v < c["rsi_bear"]
        sb = st_dir < 0
        se = st_dir > 0
        adx_ok = (~adx_v.astype(bool) | (adx_v > c["adx_thr"])) if not c["use_adx"] else (adx_v > c["adx_thr"])

        full_bull = tb & mb & sb & adx_ok
        full_bear = te & me & se & adx_ok

        return {
            "full_bull": full_bull,
            "full_bear": full_bear,
            "trend_bull": tb, "trend_bear": te,
            "mom_bull": mb, "mom_bear": me,
            "st_bull": sb, "st_bear": se,
            "adx_ok": adx_ok,
            "close": close, "rsi": rsi_v, "adx": adx_v,
            "atr": atr_v, "st_val": st_v, "vol_pct": vol_pct,
        }

    # ── Señal de calidad con MTF ──────────────────────────────────────────
    def compute(
        self,
        df_primary: pd.DataFrame,
        df_htf1: pd.DataFrame = None,   # timeframe mayor (ej: 1h)
        df_htf2: pd.DataFrame = None,   # timeframe aún mayor (ej: 4h)
        funding_rate: float = 0.0,      # positivo = longs pagan, negativo = shorts pagan
    ) -> SignalResult:
        c = self.c
        p = self._compute_single(df_primary)
        n = len(df_primary) - 1         # índice de la última barra

        # ── Confirmación MTF ──────────────────────────────────────────────
        mtf_ok = True
        if df_htf1 is not None and len(df_htf1) > 50:
            h1 = self._compute_single(df_htf1)
            last_bull = bool(p["full_bull"].iloc[-1])
            last_bear = bool(p["full_bear"].iloc[-1])
            htf1_bull = bool(h1["full_bull"].iloc[-1]) or bool(h1["trend_bull"].iloc[-1])
            htf1_bear = bool(h1["full_bear"].iloc[-1]) or bool(h1["trend_bear"].iloc[-1])
            if last_bull and not htf1_bull:
                mtf_ok = False
            if last_bear and not htf1_bear:
                mtf_ok = False

        if df_htf2 is not None and len(df_htf2) > 50:
            h2 = self._compute_single(df_htf2)
            htf2_bull = bool(h2["trend_bull"].iloc[-1])
            htf2_bear = bool(h2["trend_bear"].iloc[-1])
            if bool(p["full_bull"].iloc[-1]) and not htf2_bull:
                mtf_ok = False
            if bool(p["full_bear"].iloc[-1]) and not htf2_bear:
                mtf_ok = False

        # ── Filtro funding rate ───────────────────────────────────────────
        # Si el funding es muy positivo (+0.05%), los longs pagan mucho → no abrir longs
        # Si el funding es muy negativo (-0.05%), los shorts pagan mucho → no abrir shorts
        funding_ok = True
        funding_threshold = c.get("funding_threshold", 0.05)
        if bool(p["full_bull"].iloc[-1]) and funding_rate > funding_threshold:
            funding_ok = False
        if bool(p["full_bear"].iloc[-1]) and funding_rate < -funding_threshold:
            funding_ok = False

        # ── Filtro de volumen mínimo ──────────────────────────────────────
        vol_pct_now = float(p["vol_pct"].iloc[-1]) if not np.isnan(float(p["vol_pct"].iloc[-1])) else 50.0
        min_vol_pct = c.get("min_volume_percentile", 30)
        vol_ok = vol_pct_now >= min_vol_pct

        # ── Señales raw con cooldown ──────────────────────────────────────
        raw_bull = bool(p["full_bull"].iloc[-1]) and not bool(p["full_bull"].iloc[-2]) if n > 0 else False
        raw_bear = bool(p["full_bear"].iloc[-1]) and not bool(p["full_bear"].iloc[-2]) if n > 0 else False

        bars_since = n - self._last_signal_bar
        cooldown_ok = bars_since >= c["cooldown"]

        sig_bull = raw_bull and cooldown_ok and mtf_ok and funding_ok and vol_ok
        sig_bear = raw_bear and cooldown_ok and mtf_ok and funding_ok and vol_ok

        if sig_bull or sig_bear:
            self._last_signal_bar = n

        # ── Cálculo stop y TPs ────────────────────────────────────────────
        close_now = float(p["close"].iloc[-1])
        st_now = float(p["st_val"].iloc[-1])
        atr_now = float(p["atr"].iloc[-1])

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

        # ── Score de calidad 0-10 ─────────────────────────────────────────
        signal = "BULL" if sig_bull else ("BEAR" if sig_bear else None)
        confluence = sum([
            bool(p["trend_bull"].iloc[-1]) or bool(p["trend_bear"].iloc[-1]),
            bool(p["mom_bull"].iloc[-1]) or bool(p["mom_bear"].iloc[-1]),
            bool(p["st_bull"].iloc[-1]) or bool(p["st_bear"].iloc[-1]),
            bool(p["adx_ok"].iloc[-1]),
        ])
        quality = 0
        if signal:
            quality += confluence * 2           # máx 8 (4 filtros × 2)
            if mtf_ok:
                quality = min(10, quality + 1)
            if vol_pct_now > 60:
                quality = min(10, quality + 1)

        trend = "BULL" if bool(p["full_bull"].iloc[-1]) else ("BEAR" if bool(p["full_bear"].iloc[-1]) else "NEUTRAL")

        return SignalResult(
            signal=signal,
            quality=quality,
            trend=trend,
            close=close_now,
            atr_val=atr_now,
            stop_dist=stop_dist,
            entry=entry,
            stop=stop_price,
            tp1=tp1, tp2=tp2, tp3=tp3, tp4=tp4,
            rsi_val=float(p["rsi"].iloc[-1]),
            adx_val=float(p["adx"].iloc[-1]),
            confluence=confluence,
            volume_pct=vol_pct_now,
            st_val=st_now,
            mtf_ok=mtf_ok,
            funding_ok=funding_ok,
        )
