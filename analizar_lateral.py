"""
analizar_lateral.py — APEX Bot v7.0 [Range Trading]
=====================================================
Detecta mercado lateral y genera señales específicas para ese contexto.

Cuándo activar: RANGE_ACTIVO=true en Railway si el bot lleva
2+ días sin señales (mercado muy lateral, ADX < 22 en todos los pares).

Integración en analizar.py (ya incluida en v7):
  from analizar_lateral import detectar_rango, señal_en_rango
"""

import logging
from typing import Optional

log = logging.getLogger("lateral")


def calc_adx(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """
    Average Directional Index.
    ADX < 22  → lateral/sin tendencia → modo rango
    ADX 22-25 → zona gris
    ADX > 25  → tendencia → modo tendencia
    """
    if len(highs) < period + 2:
        return 25.0
    try:
        trs, plus_dm, minus_dm = [], [], []
        for i in range(1, len(highs)):
            tr = max(highs[i] - lows[i],
                     abs(highs[i] - closes[i-1]),
                     abs(lows[i]  - closes[i-1]))
            trs.append(tr)
            up   = highs[i] - highs[i-1]
            down = lows[i-1] - lows[i]
            plus_dm.append(max(up, 0)   if up > down   else 0)
            minus_dm.append(max(down, 0) if down > up   else 0)

        def smooth(data, p):
            if len(data) < p:
                return 1e-9
            s = sum(data[:p])
            for x in data[p:]:
                s = s - s / p + x
            return s

        n = period * 2
        atr14  = smooth(trs[-n:],      period)
        pdi14  = smooth(plus_dm[-n:],  period)
        mdi14  = smooth(minus_dm[-n:], period)

        if atr14 <= 0:
            return 25.0

        plus_di  = 100 * pdi14 / atr14
        minus_di = 100 * mdi14 / atr14

        dx_list = []
        for i in range(max(0, len(trs) - n), len(trs)):
            tr_i = trs[i]
            if tr_i <= 0:
                continue
            pdi_i = 100 * plus_dm[i]  / tr_i
            mdi_i = 100 * minus_dm[i] / tr_i
            deno  = pdi_i + mdi_i
            if deno > 0:
                dx_list.append(abs(pdi_i - mdi_i) / deno * 100)

        return sum(dx_list[-period:]) / len(dx_list[-period:]) if dx_list else 25.0

    except Exception:
        return 25.0


def detectar_rango(candles: list, lookback: int = 30) -> dict:
    """
    Detecta si el mercado está en rango lateral.

    Returns dict con:
      es_rango      : bool
      high          : float  — techo del rango
      low           : float  — suelo del rango
      mid           : float  — punto medio
      amplitud_pct  : float  — amplitud como % del precio
      adx           : float
      cerca_high    : bool   — precio cerca del techo (zona SHORT)
      cerca_low     : bool   — precio cerca del suelo (zona LONG)
      en_mitad      : bool   — precio en zona neutral (NO operar)
    """
    if len(candles) < lookback:
        return {"es_rango": False}

    recientes = candles[-lookback:]
    highs     = [c["high"]  for c in recientes]
    lows      = [c["low"]   for c in recientes]
    closes    = [c["close"] for c in recientes]
    precio    = closes[-1]

    rng_high = max(highs)
    rng_low  = min(lows)
    rng_mid  = (rng_high + rng_low) / 2
    amplitud = (rng_high - rng_low) / rng_mid * 100 if rng_mid > 0 else 0

    # ADX sobre todas las velas disponibles
    all_h = [c["high"]  for c in candles]
    all_l = [c["low"]   for c in candles]
    all_c = [c["close"] for c in candles]
    adx   = calc_adx(all_h, all_l, all_c, 14)

    # Rango válido: amplitud 2-15%, ADX < 25
    es_rango = 2.0 <= amplitud <= 15.0 and adx < 25.0

    # Tolerancia del 15% del rango para "cerca del extremo"
    tol        = (rng_high - rng_low) * 0.15
    cerca_high = precio >= rng_high - tol
    cerca_low  = precio <= rng_low  + tol
    en_mitad   = not cerca_high and not cerca_low

    return {
        "es_rango":     es_rango,
        "high":         round(rng_high, 8),
        "low":          round(rng_low,  8),
        "mid":          round(rng_mid,  8),
        "amplitud_pct": round(amplitud, 2),
        "adx":          round(adx, 1),
        "cerca_high":   cerca_high,
        "cerca_low":    cerca_low,
        "en_mitad":     en_mitad,
    }


def señal_en_rango(
    par: str,
    candles: list,
    rango: dict,
    rsi: float,
    patron_long: dict,
    patron_short: dict,
    score_base_long: int,
    score_base_short: int,
) -> Optional[dict]:
    """
    Señal específica para mercado lateral.

    Reglas:
      LONG  → cerca del suelo del rango + RSI < 50 + patrón alcista
      SHORT → cerca del techo del rango + RSI > 50 + patrón bajista
      TP    → 55% del rango (conservador, no buscar breakout)
      SL    → fuera del rango + buffer 8%
      RR    → mínimo 1.5 (más bajo que tendencia)
    """
    if not rango.get("es_rango") or rango.get("en_mitad"):
        return None

    precio       = candles[-1]["close"]
    rng_high     = rango["high"]
    rng_low      = rango["low"]
    rng_amplitud = rng_high - rng_low

    lado = score = None
    motivos = []

    # ── LONG en suelo ──────────────────────────────────────
    if rango["cerca_low"] and rsi < 55:
        score   = score_base_long + 2   # bonus zona rango
        motivos = ["RANGO_SUELO", "EQL"]
        if patron_long.get("patron"):
            score   += patron_long.get("confianza", 1)
            motivos.append(patron_long["patron"])
        if rsi < 40:
            score += 1
            motivos.append(f"RSI{rsi:.0f}")
        lado = "LONG"

    # ── SHORT en techo ─────────────────────────────────────
    elif rango["cerca_high"] and rsi > 45:
        score   = score_base_short + 2
        motivos = ["RANGO_TECHO", "EQH"]
        if patron_short.get("patron"):
            score   += patron_short.get("confianza", 1)
            motivos.append(patron_short["patron"])
        if rsi > 60:
            score += 1
            motivos.append(f"RSI{rsi:.0f}")
        lado = "SHORT"

    if lado is None or score < 6:
        return None

    # ── SL / TP específicos para rango ────────────────────
    sl_margin = rng_amplitud * 0.08  # 8% más allá del extremo

    if lado == "LONG":
        sl   = rng_low  - sl_margin
        tp   = precio + rng_amplitud * 0.55
        tp1  = precio + rng_amplitud * 0.27
        dist = precio - sl
    else:
        sl   = rng_high + sl_margin
        tp   = precio - rng_amplitud * 0.55
        tp1  = precio - rng_amplitud * 0.27
        dist = sl - precio

    if dist <= 0:
        return None

    rr = abs(tp - precio) / dist
    if rr < 1.5:
        return None

    return {
        "par":             par,
        "lado":            lado,
        "precio":          precio,
        "sl":              round(sl,  8),
        "tp":              round(tp,  8),
        "tp1":             round(tp1, 8),
        "tp2":             round(tp,  8),
        "score":           score,
        "rr":              round(rr, 2),
        "motivos":         motivos,
        "mercado_lateral": True,
        "rango_high":      round(rng_high, 8),
        "rango_low":       round(rng_low,  8),
        "rango_adx":       rango["adx"],
        "rango_amplitud":  rango["amplitud_pct"],
    }
