"""
GUA-USDT Bot v3 — Motor de Estrategia

Mejoras v3 vs v2:
  • EMA200 eliminada del scorer 3m (LOOKBACK=150 < 200 → valores basura)
    → EMA200 sólo se usa en 1h (candles_macro) donde hay suficiente warm-up
  • MACD histogram conectado al scorer (ya no se calcula y se descarta)
  • MFI añadido: divergencia MFI vs precio = confirmación institucional
  • pre_compression() integrado: entrada anticipada antes del breakout
  • Funding window: bonus score 45 min antes del pago de funding
  • Tolerancia dinámica en liquidity sweeps (proporcional ATR)
  • FVG fresco (<10 velas) puntúa más que FVG viejo
  • _score_long rebalanceado: RSI sobreventa ya no es REQUERIDO hard-gate
    (permite entradas momentum en sobreventa relativa con suficientes confluencias)
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

import config
import indicators as ind

log = logging.getLogger("strategy")


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class Signal:
    direction:   str    # "LONG" | "SHORT"
    score:       float  # 0.0 – 1.0
    price:       float
    atr:         float
    atr_pct:     float
    sl:          float
    tp1:         float
    tp2:         float
    rsi:         float
    adx:         float
    funding:     float
    squeeze:     bool
    rvol:        float
    reason:      str
    # SMC
    fvg_hit:     bool = False
    ob_hit:      bool = False
    liq_sweep:   bool = False
    bos:         str  = "NONE"
    choch:       str  = "NONE"
    # v3 extras
    pre_comp:    bool = False   # compresión pre-breakout detectada
    comp_bias:   str  = "NEUTRAL"
    mfi:         float = 50.0
    macd_align:  bool = False
    funding_win: bool = False   # en ventana pre-pago funding


@dataclass
class StrategyState:
    last_candle_time: int = 0
    oi_history: List[float] = field(default_factory=list)


_state = StrategyState()


# ── Función principal ──────────────────────────────────────────────────────────

def analyze(
    candles:       List[Dict],
    candles_trend: List[Dict],
    candles_macro: List[Dict],
    funding_rate:  float = 0.0,
    open_interest: float = 0.0,
) -> Optional[Signal]:

    if len(candles) < 80:
        log.warning("Pocas velas: %d", len(candles))
        return None

    # Antiduplicado
    last_time = candles[-1]["time"]
    if last_time == _state.last_candle_time:
        return None
    _state.last_candle_time = last_time

    # Filtro de sesión
    if config.SESSION_FILTER and not _in_session():
        log.info("Fuera de sesión London/NY — skip")
        return None

    # Arrays 3m
    opens   = [c["open"]   for c in candles]
    highs   = [c["high"]   for c in candles]
    lows    = [c["low"]    for c in candles]
    closes  = [c["close"]  for c in candles]
    volumes = [c["volume"] for c in candles]

    # ── Indicadores clásicos ───────────────────────────────────────────────────
    ema9   = ind.ema(closes, config.EMA_FAST)    # 9
    ema21  = ind.ema(closes, config.EMA_SLOW)    # 21
    ema50  = ind.ema(closes, config.EMA_TREND)   # 50
    # EMA200 en 3m NO se usa en scorer (insuficientes velas para warm-up correcto)
    # Se usa sólo en candles_macro (1h) donde sí tenemos suficientes datos
    rsi14  = ind.rsi(closes, config.RSI_PERIOD)
    atr14  = ind.atr(highs, lows, closes, 14)
    adx14, di_p, di_m = ind.adx(highs, lows, closes, config.ADX_PERIOD)
    cvd20  = ind.cvd(opens, closes, volumes, config.CVD_LB)
    mfi14  = ind.mfi(highs, lows, closes, volumes, 14)

    # MACD — v3: se usa en scorer
    ml, sl_line, macd_hist = ind.macd(closes)

    i = -2  # última vela completa
    price   = closes[i]
    e9      = float(ema9[i])  if not np.isnan(ema9[i])  else float(ema9[~np.isnan(ema9)][-1])
    e21     = float(ema21[i]) if not np.isnan(ema21[i]) else float(ema21[~np.isnan(ema21)][-1])
    e50     = float(ema50[i]) if not np.isnan(ema50[i]) else float(ema50[~np.isnan(ema50)][-1])
    rsi_v   = float(rsi14[i]) if not np.isnan(rsi14[i]) else 50.0
    atr_v   = float(atr14[i])
    adx_v   = float(adx14[i])
    cvd_v   = float(cvd20[i])
    mfi_v   = float(mfi14[i]) if not np.isnan(mfi14[i]) else 50.0

    # MACD histogram: alineado = histogram mismo signo que en i-1 y creciendo
    macd_h      = float(macd_hist[i])     if not np.isnan(macd_hist[i])     else 0.0
    macd_h_prev = float(macd_hist[i - 1]) if not np.isnan(macd_hist[i - 1]) else 0.0
    macd_bull   = macd_h > 0 and macd_h > macd_h_prev    # histograma positivo y creciendo
    macd_bear   = macd_h < 0 and macd_h < macd_h_prev    # histograma negativo y cayendo

    # ATR percentil
    atr_pct  = ind.atr_percentile(atr14, config.ATR_PERCENTILE_LB)
    high_vol = atr_pct >= 75
    low_vol  = atr_pct <= 25

    # TTM Squeeze
    sqz_arr, mom_arr = ind.squeeze_momentum(
        highs, lows, closes,
        config.BB_PERIOD, config.BB_MULT,
        config.KC_PERIOD, config.KC_MULT,
        config.MOM_PERIOD,
    )
    in_squeeze      = bool(sqz_arr[i])
    prev_squeeze    = bool(sqz_arr[i - 1])
    squeeze_release = prev_squeeze and not in_squeeze
    mom_v           = float(mom_arr[i])
    mom_prev        = float(mom_arr[i - 1])
    mom_bearish     = squeeze_release and mom_v < 0 and mom_v < mom_prev
    mom_bullish     = squeeze_release and mom_v > 0 and mom_v > mom_prev

    # RVOL
    rvol_arr = ind.rvol(volumes, config.RVOL_PERIOD)
    rvol_v   = float(rvol_arr[i])

    # VWAP
    vwap_arr, vwap_up, vwap_dn = ind.vwap_bands(
        highs, lows, closes, volumes,
        config.VWAP_PERIOD, config.VWAP_BAND_MULT,
    )
    vwap_v        = float(vwap_arr[i])
    above_vwap    = price > vwap_v
    below_vwap    = price < vwap_v
    extended_up   = price > float(vwap_up[i])
    extended_down = price < float(vwap_dn[i])

    # CVD divergencia
    cvd_bear_div, cvd_bull_div = ind.cvd_divergence(closes, cvd20, config.CVD_DIV_LB)

    # FVG (v3: incluye "fresh")
    bear_fvg, bull_fvg = ind.detect_fvg(
        highs, lows, closes, config.FVG_LOOKBACK, config.FVG_MIN_SIZE
    )
    price_in_bear_fvg = (
        bear_fvg is not None and
        bear_fvg["bottom"] <= price <= bear_fvg["top"]
    )
    price_in_bull_fvg = (
        bull_fvg is not None and
        bull_fvg["bottom"] <= price <= bull_fvg["top"]
    )
    bear_fvg_fresh = bear_fvg is not None and bear_fvg.get("fresh", False)
    bull_fvg_fresh = bull_fvg is not None and bull_fvg.get("fresh", False)

    # Order Blocks
    bear_ob, bull_ob = ind.detect_order_blocks(
        opens, highs, lows, closes, config.OB_LOOKBACK, config.OB_IMPULSE_BARS
    )
    price_in_bear_ob = (
        bear_ob is not None and
        bear_ob["low"] <= price <= bear_ob["high"]
    )
    price_in_bull_ob = (
        bull_ob is not None and
        bull_ob["low"] <= price <= bull_ob["high"]
    )

    # Liquidity sweep (v3: tolerancia dinámica con ATR)
    swept_highs, swept_lows = ind.detect_liquidity_sweep(
        highs, lows, closes, opens,
        config.LIQ_LOOKBACK, config.LIQ_TOLERANCE,
        atr_val=atr_v,
    )

    # Market structure
    ms = ind.market_structure(highs, lows, closes)

    # Pre-compression (v3: NUEVO)
    is_compressing, comp_bias = ind.pre_compression(
        highs, lows, volumes, atr14,
        range_lookback=8, vol_lookback=20,
    )

    # Funding window (v3: NUEVO)
    funding_win = ind.funding_window_active()

    # OI delta
    _state.oi_history.append(open_interest)
    if len(_state.oi_history) > config.OI_HISTORY_LEN:
        _state.oi_history.pop(0)
    oi_delta = _oi_delta()

    # Tendencia 15m (EMA9/21/50 — sin EMA200, no hay suficiente warm-up en 15m con 100 velas)
    trend_bias = "NEUTRAL"
    if len(candles_trend) >= 55:
        tc   = [c["close"] for c in candles_trend]
        te9  = ind.ema(tc, config.EMA_FAST)
        te21 = ind.ema(tc, config.EMA_SLOW)
        te50 = ind.ema(tc, config.EMA_TREND)
        # Usar último valor válido
        _te9  = te9[~np.isnan(te9)][-1]   if (~np.isnan(te9)).any()  else tc[-1]
        _te21 = te21[~np.isnan(te21)][-1] if (~np.isnan(te21)).any() else tc[-1]
        _te50 = te50[~np.isnan(te50)][-1] if (~np.isnan(te50)).any() else tc[-1]
        trend_bias = (
            "DOWN" if _te9 < _te21 and _te21 < _te50 else
            "UP"   if _te9 > _te21 and _te21 > _te50 else
            "NEUTRAL"
        )

    # Estructura macro 1h (aquí SÍ usamos EMA200 — 72 velas × 1h = 3 días de warm-up)
    macro_bias = "NEUTRAL"
    if len(candles_macro) >= 55:
        mc    = [c["close"] for c in candles_macro]
        me50  = ind.ema(mc, config.EMA_TREND)
        me200 = ind.ema(mc, config.EMA_MACRO)
        _me50  = me50[~np.isnan(me50)][-1]   if (~np.isnan(me50)).any()  else mc[-1]
        _me200 = me200[~np.isnan(me200)][-1] if (~np.isnan(me200)).any() else mc[-1]
        last_c = mc[-1]
        macro_bias = (
            "DOWN" if last_c < _me50 and _me50 < _me200 else
            "UP"   if last_c > _me50 and _me50 > _me200 else
            "NEUTRAL"
        )

    log.info(
        "price=%.5f rsi=%.1f mfi=%.1f adx=%.1f atrPct=%.0f sqz=%s "
        "rvol=%.2fx macdBull=%s macdBear=%s comp=%s(%s) fwin=%s bias15m=%s macro=%s",
        price, rsi_v, mfi_v, adx_v, atr_pct, in_squeeze,
        rvol_v, macd_bull, macd_bear,
        is_compressing, comp_bias, funding_win, trend_bias, macro_bias,
    )

    # Evitar baja volatilidad sin ADX
    if low_vol and adx_v < config.ADX_MIN:
        log.info("Baja volatilidad + ADX bajo — skip")
        return None

    # Scorers
    short_score, short_parts = _score_short(
        price, e9, e21, e50,
        rsi_v, mfi_v, adx_v, atr_pct,
        mom_bearish, in_squeeze, macd_bear,
        rvol_v, above_vwap, extended_up,
        cvd_bear_div,
        swept_highs,
        price_in_bear_fvg, bear_fvg_fresh,
        price_in_bear_ob,
        ms, oi_delta, funding_rate,
        trend_bias, macro_bias,
        is_compressing, comp_bias,
        funding_win,
    )

    long_score, long_parts = _score_long(
        price, e9, e21, e50,
        rsi_v, mfi_v, adx_v, atr_pct,
        mom_bullish, in_squeeze, macd_bull,
        rvol_v, below_vwap, extended_down,
        cvd_bull_div,
        swept_lows,
        price_in_bull_fvg, bull_fvg_fresh,
        price_in_bull_ob,
        ms, oi_delta, funding_rate,
        trend_bias, macro_bias,
        is_compressing, comp_bias,
        funding_win,
    )

    direction = None
    score     = 0.0
    parts     = ""
    if short_score > long_score and short_score >= config.SCORE_THR:
        direction, score, parts = "SHORT", short_score, short_parts
    elif long_score >= config.SCORE_THR:
        direction, score, parts = "LONG", long_score, long_parts

    if direction is None:
        return None

    # SL/TP dinámico
    sl_mult = config.ATR_HIGHVOL_MULT if high_vol else config.ATR_SL_MULT
    if direction == "SHORT":
        sl  = price + atr_v * sl_mult
        tp1 = price - atr_v * config.ATR_TP1_MULT
        tp2 = price - atr_v * config.ATR_TP2_MULT
    else:
        sl  = price - atr_v * sl_mult
        tp1 = price + atr_v * config.ATR_TP1_MULT
        tp2 = price + atr_v * config.ATR_TP2_MULT

    return Signal(
        direction   = direction,
        score       = round(score, 3),
        price       = round(price, 6),
        atr         = round(atr_v, 6),
        atr_pct     = round(atr_pct, 1),
        sl          = round(sl,  6),
        tp1         = round(tp1, 6),
        tp2         = round(tp2, 6),
        rsi         = round(rsi_v, 1),
        adx         = round(adx_v, 1),
        funding     = round(funding_rate, 6),
        squeeze     = in_squeeze,
        rvol        = round(rvol_v, 2),
        reason      = parts,
        fvg_hit     = price_in_bear_fvg if direction == "SHORT" else price_in_bull_fvg,
        ob_hit      = price_in_bear_ob  if direction == "SHORT" else price_in_bull_ob,
        liq_sweep   = swept_highs       if direction == "SHORT" else swept_lows,
        bos         = ms["bos"],
        choch       = ms["choch"],
        pre_comp    = is_compressing,
        comp_bias   = comp_bias,
        mfi         = round(mfi_v, 1),
        macd_align  = macd_bear if direction == "SHORT" else macd_bull,
        funding_win = funding_win,
    )


# ══════════════════════════════════════════════════════════════════════
#  SCORERS v3
# ══════════════════════════════════════════════════════════════════════

def _score_short(
    price, e9, e21, e50,
    rsi, mfi_v, adx, atr_pct,
    mom_bearish, in_squeeze, macd_bear,
    rvol, above_vwap, extended_up,
    cvd_div,
    swept_highs,
    in_bear_fvg, fvg_fresh,
    in_bear_ob,
    ms, oi_delta, funding,
    trend_15m, macro_1h,
    is_compressing, comp_bias,
    funding_win,
) -> Tuple[float, str]:

    parts = []
    score = 0.0

    # ── [REQUERIDO] EMA estructura bajista ────────────────────────────────────
    if not (e9 < e21):
        return 0.0, ""

    score += 0.16; parts.append("EMA9<EMA21 ✅")
    if e21 < e50:
        score += 0.06; parts.append("EMA21<EMA50 ✅")
    # EMA200 eliminada del scorer 3m — ver nota en docstring

    # ── RSI zona de carga ─────────────────────────────────────────────────────
    rsi_ok = (config.RSI_OS + 15) <= rsi <= config.RSI_OB
    if rsi_ok:
        score += 0.10; parts.append(f"RSI={rsi:.0f} zona cargada ✅")
    elif rsi > config.RSI_OB:
        score += 0.07; parts.append(f"RSI={rsi:.0f} sobrecompra ⚠️")

    # ── MFI (v3: NEW) ─────────────────────────────────────────────────────────
    if mfi_v > 70:
        score += 0.08; parts.append(f"MFI={mfi_v:.0f} sobrecompra vol ✅")
    elif mfi_v > 60:
        score += 0.04; parts.append(f"MFI={mfi_v:.0f} cargado ⚠️")

    # ── MACD histogram bajista (v3: conectado) ────────────────────────────────
    if macd_bear:
        score += 0.07; parts.append("MACD hist bajista creciendo ✅")

    # ── SMC: Liquidity Sweep equal highs ──────────────────────────────────────
    if swept_highs:
        score += 0.14; parts.append("🎣 Barrido liquidez equal highs ✅")

    # ── SMC: FVG bajista (fresco puntúa más) ──────────────────────────────────
    if in_bear_fvg:
        bonus = 0.12 if fvg_fresh else 0.08
        score += bonus
        parts.append(f"📦 FVG bajista {'fresco' if fvg_fresh else 'activo'} ✅")

    # ── SMC: Order Block bajista ───────────────────────────────────────────────
    if in_bear_ob:
        score += 0.08; parts.append("🧱 OB bajista ✅")

    # ── SMC: BOS/CHoCH bajista ────────────────────────────────────────────────
    if ms["bos"] == "BEAR":
        score += 0.07; parts.append("⚡ BOS bajista ✅")
    elif ms["choch"] == "BEAR":
        score += 0.05; parts.append("🔄 CHoCH bajista ✅")

    # ── Pre-compression bearish (v3: NEW) ─────────────────────────────────────
    if is_compressing and comp_bias == "BEAR":
        score += 0.09; parts.append("🗜 Pre-compresión bajista ✅")
    elif is_compressing and comp_bias == "NEUTRAL":
        score += 0.04; parts.append("🗜 Compresión neutral ⏳")

    # ── Squeeze liberando bajista ─────────────────────────────────────────────
    if mom_bearish:
        score += 0.09; parts.append("💥 Squeeze liberando bajista ✅")
    elif in_squeeze:
        score += 0.03; parts.append("🌀 Squeeze activo ⏳")

    # ── CVD divergencia bajista ───────────────────────────────────────────────
    if cvd_div:
        score += 0.07; parts.append("📊 CVD div bajista ✅")

    # ── VWAP ─────────────────────────────────────────────────────────────────
    if extended_up:
        score += 0.06; parts.append("📈 Sobre banda VWAP sup ✅")
    elif above_vwap:
        score += 0.02; parts.append("Sobre VWAP ⚠️")

    # ── RVOL ─────────────────────────────────────────────────────────────────
    if rvol >= config.RVOL_MIN:
        score += 0.05; parts.append(f"📣 RVOL={rvol:.1f}x ✅")

    # ── ADX ──────────────────────────────────────────────────────────────────
    if adx >= config.ADX_MIN:
        score += 0.04; parts.append(f"ADX={adx:.1f} ✅")
    else:
        score -= 0.05; parts.append(f"ADX={adx:.1f} bajo ❌")

    # ── OI Delta ─────────────────────────────────────────────────────────────
    if oi_delta > 0:
        score += 0.03; parts.append("OI↑ dinero nuevo ✅")

    # ── Funding ──────────────────────────────────────────────────────────────
    if funding >= config.FUNDING_EXTREME_LONG:
        score += 0.06; parts.append(f"💰 Funding extremo +{funding:.4%} ✅")
    elif funding > 0:
        score += 0.02

    # ── Funding window (v3: NEW) ──────────────────────────────────────────────
    if funding_win and funding >= config.FUNDING_EXTREME_LONG:
        score += 0.05; parts.append("⏰ Ventana pre-pago funding ✅")

    # ── Bias 15m ─────────────────────────────────────────────────────────────
    if trend_15m == "DOWN":
        score += 0.08; parts.append("📉 Bias 15m bajista ✅")
    elif trend_15m == "UP":
        score -= 0.12; parts.append("📈 Bias 15m alcista PENALIZA ❌")

    # ── Macro 1h ─────────────────────────────────────────────────────────────
    if macro_1h == "DOWN":
        score += 0.06; parts.append("🏔 Macro 1h bajista ✅")
    elif macro_1h == "UP":
        score -= 0.08; parts.append("🏔 Macro 1h alcista PENALIZA ❌")

    # ── Régimen ATR ──────────────────────────────────────────────────────────
    if 50 <= atr_pct <= 85:
        score += 0.03; parts.append(f"ATR pct={atr_pct:.0f} óptimo ✅")
    elif atr_pct > 90:
        score -= 0.05; parts.append(f"ATR pct={atr_pct:.0f} extremo ⚠️")

    return min(max(score, 0.0), 1.0), " | ".join(parts)


def _score_long(
    price, e9, e21, e50,
    rsi, mfi_v, adx, atr_pct,
    mom_bullish, in_squeeze, macd_bull,
    rvol, below_vwap, extended_down,
    cvd_div,
    swept_lows,
    in_bull_fvg, fvg_fresh,
    in_bull_ob,
    ms, oi_delta, funding,
    trend_15m, macro_1h,
    is_compressing, comp_bias,
    funding_win,
) -> Tuple[float, str]:

    parts = []
    score = 0.0

    # ── v3: RSI ya NO es hard-gate absoluto ──────────────────────────────────
    # En v2, rsi > RSI_OS bloqueaba TODO el scorer LONG.
    # Problema: muchos setups momentum válidos (CHoCH en tendencia fuerte)
    # tienen RSI 40-50 pero son señales LONG legítimas.
    # Solución v3: RSI sobreventa da bonus grande, pero no bloquea si hay
    # suficientes confluencias SMC.

    # Requiero mínimo: precio bajo EMA21 (discrecional) O alguna confluencia SMC
    has_smc = swept_lows or in_bull_fvg or in_bull_ob or ms["choch"] == "BULL"
    ema_long_ok = e9 > e21 or price < e21  # estructura o precio bajo EMA21

    if not has_smc and not ema_long_ok:
        return 0.0, ""  # Sin ninguna confluencia → descartar

    # ── RSI (bonus escalonado) ────────────────────────────────────────────────
    if rsi <= config.RSI_OS:
        score += 0.18; parts.append(f"RSI={rsi:.0f} sobreventa ✅")
    elif rsi <= config.RSI_OS + 10:
        score += 0.10; parts.append(f"RSI={rsi:.0f} cerca sobreventa ⚠️")
    elif rsi <= 50:
        score += 0.04; parts.append(f"RSI={rsi:.0f} neutral-bajo ⚠️")
    else:
        score -= 0.05; parts.append(f"RSI={rsi:.0f} alto PENALIZA ❌")

    # ── MFI (v3: NEW) ─────────────────────────────────────────────────────────
    if mfi_v < 30:
        score += 0.08; parts.append(f"MFI={mfi_v:.0f} sobreventa vol ✅")
    elif mfi_v < 40:
        score += 0.04; parts.append(f"MFI={mfi_v:.0f} bajo ⚠️")

    # ── MACD histogram alcista (v3: conectado) ────────────────────────────────
    if macd_bull:
        score += 0.07; parts.append("MACD hist alcista creciendo ✅")

    # ── EMA estructura alcista ────────────────────────────────────────────────
    if e9 > e21:
        score += 0.10; parts.append("EMA9>EMA21 ✅")
        if e21 > e50:
            score += 0.05; parts.append("EMA21>EMA50 ✅")

    # ── SMC: Liquidity sweep equal lows ───────────────────────────────────────
    if swept_lows:
        score += 0.15; parts.append("🎣 Barrido liquidez equal lows ✅")

    # ── SMC: FVG alcista (fresco puntúa más) ──────────────────────────────────
    if in_bull_fvg:
        bonus = 0.12 if fvg_fresh else 0.08
        score += bonus
        parts.append(f"📦 FVG alcista {'fresco' if fvg_fresh else 'activo'} ✅")

    # ── SMC: Order Block alcista ──────────────────────────────────────────────
    if in_bull_ob:
        score += 0.08; parts.append("🧱 OB alcista ✅")

    # ── SMC: CHoCH alcista ────────────────────────────────────────────────────
    if ms["choch"] == "BULL":
        score += 0.09; parts.append("🔄 CHoCH alcista ✅")
    elif ms["bos"] == "BULL":
        score += 0.05; parts.append("⚡ BOS alcista ✅")

    # ── Pre-compression bullish (v3: NEW) ─────────────────────────────────────
    if is_compressing and comp_bias == "BULL":
        score += 0.09; parts.append("🗜 Pre-compresión alcista ✅")
    elif is_compressing and comp_bias == "NEUTRAL":
        score += 0.04; parts.append("🗜 Compresión neutral ⏳")

    # ── Squeeze liberando alcista ─────────────────────────────────────────────
    if mom_bullish:
        score += 0.09; parts.append("💥 Squeeze liberando alcista ✅")

    # ── CVD divergencia alcista ───────────────────────────────────────────────
    if cvd_div:
        score += 0.07; parts.append("📊 CVD div alcista ✅")

    # ── VWAP ─────────────────────────────────────────────────────────────────
    if extended_down:
        score += 0.05; parts.append("📉 Bajo banda VWAP inf ✅")

    # ── RVOL ─────────────────────────────────────────────────────────────────
    if rvol >= config.RVOL_MIN:
        score += 0.05; parts.append(f"📣 RVOL={rvol:.1f}x ✅")

    # ── OI Delta ─────────────────────────────────────────────────────────────
    if oi_delta < 0:
        score += 0.04; parts.append("OI↓ short covering posible ✅")

    # ── Funding ──────────────────────────────────────────────────────────────
    if funding <= config.FUNDING_EXTREME_SHORT:
        score += 0.07; parts.append(f"💰 Funding extremo {funding:.4%} ✅")

    # ── Funding window (v3: NEW) ──────────────────────────────────────────────
    if funding_win and funding <= config.FUNDING_EXTREME_SHORT:
        score += 0.05; parts.append("⏰ Ventana pre-pago funding ✅")

    # ── ADX ──────────────────────────────────────────────────────────────────
    if adx < 25:
        score += 0.04; parts.append(f"ADX={adx:.1f} counter-trend ✅")
    elif adx > 35:
        score -= 0.08; parts.append(f"ADX={adx:.1f} tendencia fuerte PENALIZA ❌")

    # ── Bias 15m ─────────────────────────────────────────────────────────────
    if trend_15m == "UP":
        score += 0.07; parts.append("📈 Bias 15m alcista ✅")
    elif trend_15m == "DOWN":
        score -= 0.12; parts.append("📉 Bias 15m bajista PENALIZA ❌")

    # ── Macro 1h ─────────────────────────────────────────────────────────────
    if macro_1h == "DOWN":
        score -= 0.10; parts.append("🏔 Macro 1h bajista PENALIZA ❌")
    elif macro_1h == "UP":
        score += 0.05; parts.append("🏔 Macro 1h alcista ✅")

    return min(max(score, 0.0), 1.0), " | ".join(parts)


# ══════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════

def _in_session() -> bool:
    hour = datetime.now(timezone.utc).hour
    return any(start <= hour < end for start, end in config.SESSION_HOURS)


def _oi_delta() -> float:
    h = _state.oi_history
    if len(h) < 2:
        return 0.0
    return h[-1] - h[0]
