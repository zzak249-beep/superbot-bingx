"""
Conflux 4 Engine — v3.1 (MEJORADO)

Mejoras sobre v3:
  - Filtro ADX obligatorio: señal solo si ADX >= adx_min (default 22)
  - SL basado en ATR × sl_atr_mult (default 1.5) — nunca menor que ruido
  - R/R mínimo configurable (default 2.0) — TP recalculado si no se cumple
  - RSI en zona: BULL solo si RSI en [rsi_bull_lo, rsi_bull_hi] (45-65)
  - BEAR solo si RSI en [rsi_bear_lo, rsi_bear_hi] (35-55)
  - Cooldown por símbolo independiente (ya estaba, reforzado)
  - Log de señal siempre visible (corregido bug Signal=—)
  - Calidad de señal mejorada: hasta 10 puntos con criterios claros
  - Nuevo: confirmación de volumen relativo (vol > percentil configurable)
  - Nuevo: filtro de funding rate bidireccional
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger


# ──────────────────────────────────────────────────────────────────────────────
# Indicadores técnicos
# ──────────────────────────────────────────────────────────────────────────────

def supertrend(high: pd.Series, low: pd.Series, close: pd.Series,
               period: int = 10, mult: float = 3.0):
    """Supertrend: devuelve (banda, dirección). dir < 0 = alcista."""
    hl2 = (high + low) / 2
    atr = _atr(high, low, close, period)
    upper = hl2 + mult * atr
    lower = hl2 - mult * atr

    st = pd.Series(np.nan, index=close.index)
    direction = pd.Series(1, index=close.index)

    for i in range(1, len(close)):
        prev_upper = upper.iloc[i - 1] if not np.isnan(st.iloc[i - 1]) else upper.iloc[i]
        prev_lower = lower.iloc[i - 1] if not np.isnan(st.iloc[i - 1]) else lower.iloc[i]
        prev_st = st.iloc[i - 1] if not np.isnan(st.iloc[i - 1]) else close.iloc[i]
        prev_dir = direction.iloc[i - 1]

        upper.iloc[i] = min(upper.iloc[i], prev_upper) if close.iloc[i - 1] <= prev_upper else upper.iloc[i]
        lower.iloc[i] = max(lower.iloc[i], prev_lower) if close.iloc[i - 1] >= prev_lower else lower.iloc[i]

        if prev_dir == 1 and close.iloc[i] > prev_st:
            direction.iloc[i] = -1
        elif prev_dir == -1 and close.iloc[i] < prev_st:
            direction.iloc[i] = 1
        else:
            direction.iloc[i] = prev_dir

        st.iloc[i] = lower.iloc[i] if direction.iloc[i] == -1 else upper.iloc[i]

    return st, direction


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _vwma(close: pd.Series, volume: pd.Series, period: int) -> pd.Series:
    cv = close * volume
    return cv.rolling(period).sum() / volume.rolling(period).sum()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """ADX clásico de Wilder."""
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    plus_dm = high - prev_high
    minus_dm = prev_low - low
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    atr = _atr(high, low, close, period)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    return dx.ewm(alpha=1 / period, adjust=False).mean()


# ──────────────────────────────────────────────────────────────────────────────
# Resultado de señal
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SignalResult:
    signal: Optional[str]       # "BULL" | "BEAR" | None
    trend: str                  # "BULL" | "BEAR" | "NEUTRAL"
    quality: int                # 0-10
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    tp4: float
    rsi_val: float
    adx_val: float
    atr_val: float
    atr_pct: float              # ATR como % del precio
    rr_actual: float            # R/R real calculado
    volume_ok: bool
    funding_ok: bool
    reasons: list = field(default_factory=list)   # por qué se aprobó/rechazó


# ──────────────────────────────────────────────────────────────────────────────
# Motor principal
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_CFG = {
    # Indicadores
    "vwma_len": 100,
    "ema_fast": 21,
    "ema_slow": 50,
    "rsi_len": 14,
    "atr_len": 10,
    "st_mult": 3.5,
    "adx_len": 14,

    # ── FILTROS MEJORADOS ────────────────────────────────────────────────
    # ADX mínimo para emitir señal (mercado con tendencia real)
    "adx_min": 22,              # <22 = lateral = señal bloqueada

    # RSI: zonas válidas para BULL y BEAR
    "rsi_bull_lo": 45,          # RSI mínimo para BULL (no sobrevendido extremo)
    "rsi_bull_hi": 68,          # RSI máximo para BULL (no sobrecomprado)
    "rsi_bear_lo": 32,          # RSI mínimo para BEAR (no sobrevendido extremo)
    "rsi_bear_hi": 55,          # RSI máximo para BEAR (no sobrecomprado)

    # SL basado en ATR
    "sl_atr_mult": 1.5,         # SL = entrada ± ATR × sl_atr_mult
    "sl_min_pct": 0.5,          # SL nunca menor al 0.5% (protección contra ruido)

    # R/R mínimo — si el TP natural no lo cumple, se amplía
    "min_rr": 2.0,

    # TPs basados en R/R desde el SL real
    "rr1": 0.5,
    "rr2": 2.0,                 # TP2 = referencia (era 1.0, subido a 2.0)
    "rr3": 3.0,
    "rr4": 4.5,

    # Cooldown y otros
    "cooldown": 5,
    "stop_mode": "ATR",         # "ATR" | "Supertrend" | "Fixed %"
    "stop_atr_mult": 1.5,
    "stop_fixed_pct": 0.3,

    # Volumen
    "min_volume_percentile": 30,  # Subido de 20 a 30

    # Funding rate
    "funding_threshold": 0.03,    # Bajado de 0.05 a 0.03 (más conservador)
}


class Conflux4Engine:
    def __init__(self, cfg: dict | None = None):
        self.cfg = {**DEFAULT_CFG, **(cfg or {})}

        # Cooldown: timestamp de última señal emitida por símbolo
        # Se controla externamente por símbolo, aquí guardamos el estado
        self._last_signal_ts: float = 0.0
        self._signals_emitted: int = 0

    def compute(
        self,
        df: pd.DataFrame,
        df_htf1: pd.DataFrame | None = None,
        df_htf2: pd.DataFrame | None = None,
        funding_rate: float = 0.0,
    ) -> SignalResult:
        """
        Calcula señal para un símbolo. Devuelve SignalResult.
        signal=None si no hay señal válida.
        """
        c = self.cfg

        # ── Indicadores base ──────────────────────────────────────────────
        close = df["close"]
        high  = df["high"]
        low   = df["low"]
        vol   = df["volume"]

        ema_fast = _ema(close, c["ema_fast"])
        ema_slow = _ema(close, c["ema_slow"])
        vwma     = _vwma(close, vol, c["vwma_len"])
        rsi      = _rsi(close, c["rsi_len"])
        atr      = _atr(high, low, close, c["atr_len"])
        adx      = _adx(high, low, close, c["adx_len"])
        st_val, st_dir = supertrend(high, low, close, c["atr_len"], c["st_mult"])

        # Valores actuales
        price      = float(close.iloc[-1])
        rsi_now    = float(rsi.iloc[-1])
        adx_now    = float(adx.iloc[-1])
        atr_now    = float(atr.iloc[-1])
        atr_pct    = atr_now / price * 100
        ema_f_now  = float(ema_fast.iloc[-1])
        ema_s_now  = float(ema_slow.iloc[-1])
        vwma_now   = float(vwma.iloc[-1])
        st_val_now = float(st_val.iloc[-1])
        st_bull    = bool(st_dir.iloc[-1] < 0)   # True = alcista

        # ── Tendencia base ────────────────────────────────────────────────
        bull_trend = (
            st_bull
            and ema_f_now > ema_s_now
            and price > vwma_now
        )
        bear_trend = (
            not st_bull
            and ema_f_now < ema_s_now
            and price < vwma_now
        )
        trend_str = "BULL" if bull_trend else ("BEAR" if bear_trend else "NEUTRAL")

        # ── Filtro de volumen ─────────────────────────────────────────────
        vol_pct = c.get("min_volume_percentile", 30)
        vol_threshold = float(vol.quantile(vol_pct / 100))
        volume_ok = float(vol.iloc[-1]) >= vol_threshold

        # ── Filtro de funding rate ────────────────────────────────────────
        ft = c.get("funding_threshold", 0.03)
        # Funding muy positivo = mercado muy largo = evitar BULL
        # Funding muy negativo = mercado muy corto = evitar BEAR
        funding_ok_bull = funding_rate <= ft
        funding_ok_bear = funding_rate >= -ft
        funding_ok = True  # se aplica por dirección más abajo

        # ── MTF confirmación ──────────────────────────────────────────────
        mtf_bull = mtf_bear = True  # por defecto ok si no hay HTF
        if df_htf1 is not None and len(df_htf1) > 10:
            htf1_close = df_htf1["close"]
            htf1_st_v, htf1_st_d = supertrend(
                df_htf1["high"], df_htf1["low"], htf1_close,
                c["atr_len"], c["st_mult"]
            )
            htf1_bull = bool(htf1_st_d.iloc[-1] < 0)
            mtf_bull = htf1_bull
            mtf_bear = not htf1_bull

        if df_htf2 is not None and len(df_htf2) > 10:
            htf2_close = df_htf2["close"]
            htf2_ema_f = _ema(htf2_close, c["ema_fast"])
            htf2_ema_s = _ema(htf2_close, c["ema_slow"])
            htf2_bull_ema = float(htf2_ema_f.iloc[-1]) > float(htf2_ema_s.iloc[-1])
            mtf_bull = mtf_bull and htf2_bull_ema
            mtf_bear = mtf_bear and not htf2_bull_ema

        # ── Cooldown ──────────────────────────────────────────────────────
        cooldown_bars = c.get("cooldown", 5)
        in_cooldown = (time.time() - self._last_signal_ts) < (cooldown_bars * 60)

        # ════════════════════════════════════════════════════════════════
        # EVALUACIÓN DE SEÑAL CON TODOS LOS FILTROS
        # ════════════════════════════════════════════════════════════════

        signal = None
        reasons = []

        # ── FILTRO 1: ADX mínimo (nuevo — más importante) ─────────────────
        adx_min = c.get("adx_min", 22)
        if adx_now < adx_min:
            reasons.append(f"ADX {adx_now:.1f} < {adx_min} (mercado lateral)")
            return self._no_signal(trend_str, rsi_now, adx_now, atr_now, atr_pct,
                                   volume_ok, funding_ok, reasons, price)

        # ── FILTRO 2: Tendencia clara ──────────────────────────────────────
        if trend_str == "NEUTRAL":
            reasons.append("Sin tendencia clara (ST/EMA/VWMA no alineados)")
            return self._no_signal(trend_str, rsi_now, adx_now, atr_now, atr_pct,
                                   volume_ok, funding_ok, reasons, price)

        # ── FILTRO 3: Volumen ─────────────────────────────────────────────
        if not volume_ok:
            reasons.append(f"Volumen bajo (< percentil {vol_pct}%)")
            return self._no_signal(trend_str, rsi_now, adx_now, atr_now, atr_pct,
                                   volume_ok, funding_ok, reasons, price)

        # ── FILTRO 4: Cooldown ────────────────────────────────────────────
        if in_cooldown:
            reasons.append("Cooldown activo")
            return self._no_signal(trend_str, rsi_now, adx_now, atr_now, atr_pct,
                                   volume_ok, funding_ok, reasons, price)

        # ── SEÑAL BULL ────────────────────────────────────────────────────
        if bull_trend:
            rsi_lo = c.get("rsi_bull_lo", 45)
            rsi_hi = c.get("rsi_bull_hi", 68)

            if not (rsi_lo <= rsi_now <= rsi_hi):
                reasons.append(f"RSI {rsi_now:.1f} fuera de zona BULL [{rsi_lo}-{rsi_hi}]")
                return self._no_signal(trend_str, rsi_now, adx_now, atr_now, atr_pct,
                                       volume_ok, funding_ok, reasons, price)

            if not mtf_bull:
                reasons.append("MTF no confirma alcista")
                return self._no_signal(trend_str, rsi_now, adx_now, atr_now, atr_pct,
                                       volume_ok, funding_ok, reasons, price)

            if not funding_ok_bull:
                reasons.append(f"Funding {funding_rate:.4f} muy positivo — evitar BULL")
                funding_ok = False
                return self._no_signal(trend_str, rsi_now, adx_now, atr_now, atr_pct,
                                       volume_ok, funding_ok, reasons, price)

            signal = "BULL"
            reasons.append(f"RSI {rsi_now:.1f} en zona alcista")
            reasons.append(f"ADX {adx_now:.1f} confirma tendencia")
            if mtf_bull:
                reasons.append("MTF confirma alcista")

        # ── SEÑAL BEAR ────────────────────────────────────────────────────
        elif bear_trend:
            rsi_lo = c.get("rsi_bear_lo", 32)
            rsi_hi = c.get("rsi_bear_hi", 55)

            if not (rsi_lo <= rsi_now <= rsi_hi):
                reasons.append(f"RSI {rsi_now:.1f} fuera de zona BEAR [{rsi_lo}-{rsi_hi}]")
                return self._no_signal(trend_str, rsi_now, adx_now, atr_now, atr_pct,
                                       volume_ok, funding_ok, reasons, price)

            if not mtf_bear:
                reasons.append("MTF no confirma bajista")
                return self._no_signal(trend_str, rsi_now, adx_now, atr_now, atr_pct,
                                       volume_ok, funding_ok, reasons, price)

            if not funding_ok_bear:
                reasons.append(f"Funding {funding_rate:.4f} muy negativo — evitar BEAR")
                funding_ok = False
                return self._no_signal(trend_str, rsi_now, adx_now, atr_now, atr_pct,
                                       volume_ok, funding_ok, reasons, price)

            signal = "BEAR"
            reasons.append(f"RSI {rsi_now:.1f} en zona bajista")
            reasons.append(f"ADX {adx_now:.1f} confirma tendencia")

        else:
            reasons.append("Tendencia no confirmada por todos los filtros")
            return self._no_signal(trend_str, rsi_now, adx_now, atr_now, atr_pct,
                                   volume_ok, funding_ok, reasons, price)

        # ════════════════════════════════════════════════════════════════
        # SEÑAL VÁLIDA — calcular niveles
        # ════════════════════════════════════════════════════════════════

        # ── SL basado en ATR (mejorado) ───────────────────────────────────
        sl_atr_mult = c.get("sl_atr_mult", 1.5)
        sl_min_pct  = c.get("sl_min_pct", 0.5) / 100
        sl_dist_atr = atr_now * sl_atr_mult
        sl_dist_min = price * sl_min_pct
        sl_dist     = max(sl_dist_atr, sl_dist_min)  # nunca menor al ruido mínimo

        if signal == "BULL":
            # Para modo Supertrend: usar el valor del ST como referencia adicional
            if c.get("stop_mode") == "Supertrend" and not np.isnan(st_val_now):
                sl_st = price - st_val_now
                sl_dist = max(sl_dist, sl_st)  # el mayor de los dos
            stop = price - sl_dist
        else:
            if c.get("stop_mode") == "Supertrend" and not np.isnan(st_val_now):
                sl_st = st_val_now - price
                sl_dist = max(sl_dist, sl_st)
            stop = price + sl_dist

        # ── TPs con R/R mínimo garantizado ───────────────────────────────
        min_rr = c.get("min_rr", 2.0)
        rr1 = c.get("rr1", 0.5)
        rr2 = max(c.get("rr2", 2.0), min_rr)   # TP2 nunca menor al R/R mínimo
        rr3 = c.get("rr3", 3.0)
        rr4 = c.get("rr4", 4.5)

        risk = abs(price - stop)

        if signal == "BULL":
            tp1 = price + risk * rr1
            tp2 = price + risk * rr2
            tp3 = price + risk * rr3
            tp4 = price + risk * rr4
        else:
            tp1 = price - risk * rr1
            tp2 = price - risk * rr2
            tp3 = price - risk * rr3
            tp4 = price - risk * rr4

        rr_actual = rr2

        # ── Calidad de señal (0-10) ───────────────────────────────────────
        quality = self._calc_quality(
            adx_now, rsi_now, atr_pct, volume_ok, mtf_bull if signal == "BULL" else mtf_bear,
            funding_rate, signal, rr_actual
        )

        # ── Registrar señal ────────────────────────────────────────────────
        self._last_signal_ts = time.time()
        self._signals_emitted += 1

        logger.debug(
            f"Señal {signal} | ADX={adx_now:.1f} RSI={rsi_now:.1f} "
            f"ATR%={atr_pct:.2f} Q={quality} RR={rr_actual:.1f} | "
            f"SL={stop:.6f} TP2={tp2:.6f}"
        )

        return SignalResult(
            signal=signal,
            trend=trend_str,
            quality=quality,
            entry=price,
            stop=round(stop, 8),
            tp1=round(tp1, 8),
            tp2=round(tp2, 8),
            tp3=round(tp3, 8),
            tp4=round(tp4, 8),
            rsi_val=round(rsi_now, 2),
            adx_val=round(adx_now, 2),
            atr_val=round(atr_now, 8),
            atr_pct=round(atr_pct, 3),
            rr_actual=round(rr_actual, 2),
            volume_ok=volume_ok,
            funding_ok=funding_ok,
            reasons=reasons,
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _no_signal(self, trend, rsi, adx, atr, atr_pct, vol_ok, fund_ok, reasons, price) -> SignalResult:
        return SignalResult(
            signal=None, trend=trend, quality=0,
            entry=price, stop=0, tp1=0, tp2=0, tp3=0, tp4=0,
            rsi_val=round(rsi, 2), adx_val=round(adx, 2),
            atr_val=round(atr, 8), atr_pct=round(atr_pct, 3),
            rr_actual=0, volume_ok=vol_ok, funding_ok=fund_ok,
            reasons=reasons,
        )

    def _calc_quality(self, adx, rsi, atr_pct, vol_ok, mtf_ok,
                      funding, signal, rr) -> int:
        """
        Calidad 0-10 basada en criterios objetivos.
        Cada criterio suma puntos. Se redondea al entero.
        """
        score = 0.0

        # ADX (0-3 pts): más fuerte la tendencia, más puntos
        if adx >= 30:
            score += 3.0
        elif adx >= 25:
            score += 2.0
        elif adx >= 22:
            score += 1.0

        # RSI (0-2 pts): en zona ideal (50-60 BULL, 40-50 BEAR)
        if signal == "BULL":
            if 50 <= rsi <= 62:
                score += 2.0
            elif 45 <= rsi < 50 or 62 < rsi <= 68:
                score += 1.0
        else:
            if 38 <= rsi <= 50:
                score += 2.0
            elif 32 <= rsi < 38 or 50 < rsi <= 55:
                score += 1.0

        # MTF (0-2 pts)
        if mtf_ok:
            score += 2.0

        # Volumen (0-1 pt)
        if vol_ok:
            score += 1.0

        # Funding rate (0-1 pt): neutral es mejor
        if abs(funding) < 0.01:
            score += 1.0

        # ATR% (0-1 pt): volatilidad razonable (0.3%-1.5%)
        if 0.3 <= atr_pct <= 1.5:
            score += 1.0

        return min(10, int(round(score)))
