"""
analizar.py — Señales RSI + Bollinger Bands + ATR v5.1
Estrategia confirmada por backtest: PF:2.0 rentable en 15 días
- LONG:  RSI < 35 + precio en BB inferior
- SHORT: RSI > 65 + precio en BB superior
- Sin EMA200 (backtest: destruye rentabilidad 811→30 trades)
"""

import numpy as np
import config
import exchange


# ═══════════════════════════════════════════════════════
# INDICADORES
# ═══════════════════════════════════════════════════════

def calcular_rsi(closes, periodo=14):
    if len(closes) < periodo + 1:
        return 50.0
    arr  = np.array(closes, dtype=float)
    d    = np.diff(arr)
    g    = np.where(d > 0, d, 0.0)
    lo   = np.where(d < 0, -d, 0.0)
    ag   = np.mean(g[:periodo])
    al   = np.mean(lo[:periodo])
    for i in range(periodo, len(d)):
        ag = (ag * (periodo - 1) + g[i])  / periodo
        al = (al * (periodo - 1) + lo[i]) / periodo
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))


def calcular_bb(closes, periodo=20, std_mult=2.0):
    vacio = {"media": 0, "superior": 0, "inferior": 0, "posicion": 0.5, "ancho": 0}
    if len(closes) < periodo:
        return vacio
    s    = np.array(closes[-periodo:], dtype=float)
    med  = float(np.mean(s))
    std  = float(np.std(s))
    sup  = med + std_mult * std
    inf  = med - std_mult * std
    anc  = sup - inf
    pos  = float((closes[-1] - inf) / anc) if anc > 0 else 0.5
    return {"media": med, "superior": sup, "inferior": inf,
            "posicion": pos, "ancho": anc}


def calcular_atr(highs, lows, closes, periodo=14):
    if len(closes) < 2:
        return closes[-1] * 0.02 if closes else 0.01
    trs = [
        max(highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1]))
        for i in range(1, len(closes))
    ]
    if not trs:
        return closes[-1] * 0.02
    atr = float(np.mean(trs[:periodo]))
    for i in range(periodo, len(trs)):
        atr = (atr * (periodo - 1) + trs[i]) / periodo
    return atr


# ═══════════════════════════════════════════════════════
# ANÁLISIS
# ═══════════════════════════════════════════════════════

def analizar_par(par):
    resultado = {
        "par": par, "señal": False, "lado": None,
        "rsi": 50.0, "bb": {}, "atr": 0.0,
        "precio": 0.0, "sl": 0.0, "tp": 0.0,
        "tp1": 0.0, "rr": 0.0, "score": 0, "motivo": ""
    }

    klines = exchange.get_klines(par, intervalo="15m", limit=120)
    if len(klines) < 50:
        resultado["motivo"] = f"klines insuficientes ({len(klines)})"
        return resultado

    data   = exchange.parsear_klines(klines)
    closes = data["closes"]
    highs  = data["highs"]
    lows   = data["lows"]

    if len(closes) < 50:
        resultado["motivo"] = f"datos insuficientes ({len(closes)})"
        return resultado

    precio = closes[-1]
    if precio <= 0:
        resultado["motivo"] = "precio = 0"
        return resultado

    resultado["precio"] = precio

    # Indicadores
    rsi = calcular_rsi(closes, config.RSI_PERIODO)
    bb  = calcular_bb(closes, config.BB_PERIODO, config.BB_STD)
    atr = calcular_atr(highs, lows, closes, config.ATR_PERIODO)

    resultado["rsi"] = rsi
    resultado["bb"]  = bb
    resultado["atr"] = atr

    # Filtros de calidad
    volumen = exchange.get_volumen_24h(par)
    if volumen < config.VOLUMEN_MIN_USD:
        resultado["motivo"] = f"vol bajo ${volumen:,.0f}"
        return resultado

    spread = exchange.get_spread_pct(par)
    if spread > config.SPREAD_MAX_PCT:
        resultado["motivo"] = f"spread {spread:.2f}%"
        return resultado

    if atr <= 0 or bb["ancho"] <= 0:
        resultado["motivo"] = "ATR o BB = 0"
        return resultado

    sl_dist  = atr * config.SL_ATR_MULT
    tp_dist  = atr * config.TP_ATR_MULT
    tp1_dist = atr * getattr(config, "PARTIAL_TP1_MULT", 1.5)
    rr       = tp_dist / sl_dist if sl_dist > 0 else 0

    if rr < config.RR_MINIMO:
        resultado["motivo"] = f"R:R={rr:.2f} < {config.RR_MINIMO}"
        return resultado

    # ── LONG ──────────────────────────────────────────
    if rsi < config.RSI_OVERSOLD and precio <= bb["inferior"] * 1.002:
        score = 50
        score += int(max(0, config.RSI_OVERSOLD - rsi))  # más sobrevendido → +score
        score += min(20, int(rr * 5))                     # mejor R:R → +score
        if bb["posicion"] < 0.10: score += 10             # en borde BB
        if rsi < 25:              score += 10             # RSI extremo
        score = min(100, score)

        if score < config.SCORE_MIN:
            resultado["motivo"] = f"LONG score {score} < {config.SCORE_MIN}"
            return resultado

        resultado.update({
            "señal": True, "lado": "LONG",
            "sl":    precio - sl_dist,
            "tp":    precio + tp_dist,
            "tp1":   precio + tp1_dist,
            "rr":    rr, "score": score,
            "motivo": f"LONG RSI={rsi:.1f} BB={bb['posicion']:.2f} R:R={rr:.2f} score={score}"
        })
        return resultado

    # ── SHORT ─────────────────────────────────────────
    if rsi > config.RSI_OVERBOUGHT and precio >= bb["superior"] * 0.998:
        score = 50
        score += int(max(0, rsi - config.RSI_OVERBOUGHT))
        score += min(20, int(rr * 5))
        if bb["posicion"] > 0.90: score += 10
        if rsi > 75:              score += 10
        score = min(100, score)

        if score < config.SCORE_MIN:
            resultado["motivo"] = f"SHORT score {score} < {config.SCORE_MIN}"
            return resultado

        resultado.update({
            "señal": True, "lado": "SHORT",
            "sl":    precio + sl_dist,
            "tp":    precio - tp_dist,
            "tp1":   precio - tp1_dist,
            "rr":    rr, "score": score,
            "motivo": f"SHORT RSI={rsi:.1f} BB={bb['posicion']:.2f} R:R={rr:.2f} score={score}"
        })
        return resultado

    # Sin señal
    if rsi < config.RSI_OVERSOLD:
        resultado["motivo"] = f"RSI={rsi:.1f} OK pero precio lejos BB inf (pos={bb['posicion']:.2f})"
    elif rsi > config.RSI_OVERBOUGHT:
        resultado["motivo"] = f"RSI={rsi:.1f} OK pero precio lejos BB sup (pos={bb['posicion']:.2f})"
    else:
        resultado["motivo"] = f"RSI={rsi:.1f} neutro"
    return resultado


def analizar_todos(pares):
    senales = []
    for par in pares:
        try:
            r = analizar_par(par)
            if r["señal"]:
                senales.append(r)
                if config.MODO_DEBUG:
                    print(f"  ✓ {r['lado']:5s} {par}: {r['motivo']}")
            elif config.MODO_DEBUG:
                print(f"  ✗ {par}: {r['motivo']}")
        except Exception as e:
            print(f"  [ERROR] {par}: {e}")
            if config.MODO_DEBUG:
                import traceback; traceback.print_exc()
    senales.sort(key=lambda x: x["score"], reverse=True)
    return senales
