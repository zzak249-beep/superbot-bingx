"""
Strategy v5 — Señales multi-timeframe con filtros avanzados
Mejoras vs v4:
  - Confirmación multi-TF: señal 15m + confirmación 1h + tendencia 4h
  - Filtro funding rate: evita operar contra el funding
  - Volume spike: requiere volumen 1.5x promedio
  - Régimen de mercado: detecta trending vs ranging
  - VWAP como filtro de entrada adicional
  - RR calculado dinámicamente con ATR
"""
import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, List

log = logging.getLogger("Strategy")


@dataclass
class Signal:
    symbol: str
    direction: str          # LONG | SHORT
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    score: float            # 0-100
    atr: float
    reason: str
    vol_ok: bool = True
    regime: str = "UNKNOWN"
    tier: str = "B"
    funding_rate: float = 0.0
    multi_tf_score: float = 0.0  # 0-3 (cuántos TF confirman)


def _ema(prices: list, period: int) -> list:
    k = 2 / (period + 1)
    result = [prices[0]]
    for p in prices[1:]:
        result.append(p * k + result[-1] * (1 - k))
    return result


def _rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-period-1:])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains) if np.mean(gains) > 0 else 1e-10
    avg_loss = np.mean(losses) if np.mean(losses) > 0 else 1e-10
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _atr(klines: list, period: int = 14) -> float:
    if len(klines) < period + 1:
        return 0.0
    trs = []
    for i in range(1, min(period + 1, len(klines))):
        h = float(klines[-i][2])
        l = float(klines[-i][3])
        pc = float(klines[-i-1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return np.mean(trs) if trs else 0.0


def _vwap(klines: list) -> float:
    """VWAP de las últimas 50 velas."""
    recent = klines[-50:]
    tp_vol = sum(
        ((float(k[2]) + float(k[3]) + float(k[4])) / 3) * float(k[5] if len(k) > 5 else 1)
        for k in recent
    )
    vol = sum(float(k[5] if len(k) > 5 else 1) for k in recent)
    return tp_vol / vol if vol > 0 else 0.0


def _detect_regime(closes: list, period: int = 20) -> str:
    """
    Detecta régimen: TRENDING_UP, TRENDING_DOWN, RANGING.
    Basado en ADX simplificado.
    """
    if len(closes) < period * 2:
        return "UNKNOWN"
    
    ema_fast = _ema(closes, 20)
    ema_slow = _ema(closes, 50)
    
    if len(ema_fast) < 2 or len(ema_slow) < 2:
        return "UNKNOWN"
    
    slope_fast = (ema_fast[-1] - ema_fast[-5]) / ema_fast[-5] * 100
    spread = (ema_fast[-1] - ema_slow[-1]) / ema_slow[-1] * 100
    
    if spread > 0.5 and slope_fast > 0.1:
        return "TRENDING_UP"
    elif spread < -0.5 and slope_fast < -0.1:
        return "TRENDING_DOWN"
    else:
        return "RANGING"


def _volume_ok(klines: list, multiplier: float = 1.5) -> bool:
    """Verifica si el volumen reciente es >= multiplier × promedio."""
    if len(klines) < 20:
        return True
    vols = [float(k[5]) if len(k) > 5 else 1.0 for k in klines[-20:]]
    avg = np.mean(vols[:-1])
    current = vols[-1]
    ok = current >= avg * multiplier
    if not ok:
        log.debug(f"Volumen bajo: {current:.0f} < {avg * multiplier:.0f}")
    return ok


def analyze_timeframe(klines: list, timeframe: str = "15m") -> Optional[dict]:
    """
    Analiza un timeframe y devuelve señal básica o None.
    Retorna: {direction, score, reason, ema_trend}
    """
    if len(klines) < 60:
        return None
    
    closes = [float(k[4]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    
    ema9  = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    ema55 = _ema(closes, 55)
    rsi   = _rsi(closes)
    
    price = closes[-1]
    
    # Tendencia EMA
    ema_bull = ema9[-1] > ema21[-1] > ema55[-1]
    ema_bear = ema9[-1] < ema21[-1] < ema55[-1]
    
    # Cruce reciente EMA 9/21
    cross_up   = ema9[-2] <= ema21[-2] and ema9[-1] > ema21[-1]
    cross_down = ema9[-2] >= ema21[-2] and ema9[-1] < ema21[-1]
    
    # RSI confirmación
    rsi_bull = 45 < rsi < 75
    rsi_bear = 25 < rsi < 55
    
    score = 0
    direction = None
    reasons = []
    
    if ema_bull:
        score += 30
        reasons.append(f"EMA_bull_{timeframe}")
        direction = "LONG"
    elif ema_bear:
        score += 30
        reasons.append(f"EMA_bear_{timeframe}")
        direction = "SHORT"
    
    if direction == "LONG" and cross_up:
        score += 20
        reasons.append(f"cross_up_{timeframe}")
    elif direction == "SHORT" and cross_down:
        score += 20
        reasons.append(f"cross_down_{timeframe}")
    
    if direction == "LONG" and rsi_bull:
        score += 15
        reasons.append(f"RSI_{rsi:.0f}_bull")
    elif direction == "SHORT" and rsi_bear:
        score += 15
        reasons.append(f"RSI_{rsi:.0f}_bear")
    
    if not direction:
        return None
    
    # Precio sobre/bajo EMA55 como confirmación adicional
    if direction == "LONG" and price > ema55[-1]:
        score += 10
    elif direction == "SHORT" and price < ema55[-1]:
        score += 10
    
    return {
        "direction": direction,
        "score": score,
        "reason": "+".join(reasons),
        "ema_trend": "bull" if ema_bull else "bear",
        "rsi": rsi,
    }


def generate_signal(
    symbol: str,
    klines_15m: list,
    klines_1h: list,
    klines_4h: list,
    funding_rate: float = 0.0,
    min_volume_usdt: float = 500_000,
) -> Optional[Signal]:
    """
    Genera señal multi-timeframe.
    
    Lógica:
    1. Señal primaria en 15m
    2. Confirmación en 1h (mismo dirección)
    3. Tendencia en 4h (idem)
    4. Filtro funding rate (no operar LONG si funding muy positivo)
    5. Filtro volumen
    6. Cálculo SL/TP con ATR
    """
    if not klines_15m or len(klines_15m) < 60:
        return None
    
    # ── Análisis multi-TF ────────────────────────────────────────────
    sig_15m = analyze_timeframe(klines_15m, "15m")
    sig_1h  = analyze_timeframe(klines_1h, "1h")  if klines_1h  else None
    sig_4h  = analyze_timeframe(klines_4h, "4h")  if klines_4h  else None
    
    if not sig_15m:
        return None
    
    direction = sig_15m["direction"]
    score     = sig_15m["score"]
    
    # Confirmaciones adicionales
    multi_tf_score = 1  # 15m siempre cuenta
    
    if sig_1h and sig_1h["direction"] == direction:
        score += 20
        multi_tf_score += 1
    elif sig_1h and sig_1h["direction"] != direction:
        score -= 15  # penalizar contradicción
    
    if sig_4h and sig_4h["direction"] == direction:
        score += 15
        multi_tf_score += 1
    elif sig_4h and sig_4h["direction"] != direction:
        score -= 10
    
    # ── Filtro funding rate ──────────────────────────────────────────
    # Funding muy positivo → mercado pagando a shorts → no ir LONG
    # Funding muy negativo → mercado pagando a longs → no ir SHORT
    funding_ok = True
    if direction == "LONG"  and funding_rate >  0.0008:  # > 0.08%/8h
        log.info(f"❌ {symbol} funding {funding_rate:.4f} muy alto para LONG")
        score -= 20
        funding_ok = False
    if direction == "SHORT" and funding_rate < -0.0008:
        log.info(f"❌ {symbol} funding {funding_rate:.4f} muy negativo para SHORT")
        score -= 20
        funding_ok = False
    
    # ── Filtro volumen ───────────────────────────────────────────────
    vol_ok = _volume_ok(klines_15m, multiplier=1.3)
    if not vol_ok:
        score -= 10
    
    # ── VWAP como filtro adicional ───────────────────────────────────
    vwap = _vwap(klines_15m)
    price = float(klines_15m[-1][4])
    vwap_ok = (direction == "LONG" and price > vwap) or (direction == "SHORT" and price < vwap)
    if vwap_ok:
        score += 10
    
    # ── Régimen de mercado ───────────────────────────────────────────
    closes_15m = [float(k[4]) for k in klines_15m]
    regime = _detect_regime(closes_15m)
    
    # En mercado lateral, reducir puntuación (más falsas señales)
    if regime == "RANGING":
        score -= 15
    
    # ── ATR y niveles SL/TP ──────────────────────────────────────────
    atr = _atr(klines_15m, 14)
    if atr <= 0:
        return None
    
    # SL: 1.5x ATR | TP1: 2x ATR | TP2: 3.5x ATR | TP3: 5x ATR
    if direction == "LONG":
        sl  = round(price - atr * 1.5, 6)
        tp1 = round(price + atr * 2.0, 6)
        tp2 = round(price + atr * 3.5, 6)
        tp3 = round(price + atr * 5.0, 6)
    else:
        sl  = round(price + atr * 1.5, 6)
        tp1 = round(price - atr * 2.0, 6)
        tp2 = round(price - atr * 3.5, 6)
        tp3 = round(price - atr * 5.0, 6)
    
    # RR
    rr = round(atr * 2.0 / (atr * 1.5), 2)  # = 1.33
    
    # Tier basado en multi_tf_score
    if multi_tf_score == 3:
        tier = "A"
        score = min(score + 10, 100)
    elif multi_tf_score == 2:
        tier = "B"
    else:
        tier = "C"
        score = max(score - 10, 0)
    
    reason_parts = [sig_15m["reason"]]
    if sig_1h:
        reason_parts.append(sig_1h["reason"])
    if sig_4h:
        reason_parts.append(sig_4h["reason"])
    reason = " | ".join(reason_parts)
    
    log.info(
        f"📡 {symbol} {direction} score={score:.0f} "
        f"tier={tier} multi_tf={multi_tf_score}/3 "
        f"regime={regime} funding={funding_rate:.4f} "
        f"rr={rr}"
    )
    
    return Signal(
        symbol=symbol,
        direction=direction,
        entry=price,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        score=float(score),
        atr=atr,
        reason=reason,
        vol_ok=vol_ok,
        regime=regime,
        tier=tier,
        funding_rate=funding_rate,
        multi_tf_score=float(multi_tf_score),
    )
