"""
analizar.py — Señales RSI + Bollinger Bands + ATR v2.0
NUEVO:
  - Señales SHORT: RSI > RSI_OVERBOUGHT + precio en BB superior
  - Filtro: solo retorna señales con score >= SCORE_MIN (75 por defecto)
  - Score 0-100 para LONG y SHORT
"""

import numpy as np
import config
import exchange


# ============================================================
# INDICADORES
# ============================================================

def calcular_rsi(closes: list, periodo: int = 14) -> float:
    if len(closes) < periodo + 1:
        return 50.0

    arr       = np.array(closes, dtype=float)
    deltas    = np.diff(arr)
    ganancias = np.where(deltas > 0, deltas, 0.0)
    perdidas  = np.where(deltas < 0, -deltas, 0.0)

    avg_g = np.mean(ganancias[:periodo])
    avg_p = np.mean(perdidas[:periodo])

    for i in range(periodo, len(deltas)):
        avg_g = (avg_g * (periodo - 1) + ganancias[i]) / periodo
        avg_p = (avg_p * (periodo - 1) + perdidas[i]) / periodo

    if avg_p == 0:
        return 100.0

    rs = avg_g / avg_p
    return 100.0 - (100.0 / (1.0 + rs))


def calcular_bb(closes: list, periodo: int = 20, std_mult: float = 2.0) -> dict:
    vacio = {"media": 0, "superior": 0, "inferior": 0, "posicion": 0.5, "ancho": 0}

    if len(closes) < periodo:
        return vacio

    serie    = np.array(closes[-periodo:], dtype=float)
    media    = float(np.mean(serie))
    std      = float(np.std(serie))
    superior = media + std_mult * std
    inferior = media - std_mult * std
    ancho    = superior - inferior
    precio   = closes[-1]
    posicion = float((precio - inferior) / ancho) if ancho > 0 else 0.5

    return {
        "media":    media,
        "superior": superior,
        "inferior": inferior,
        "posicion": posicion,   # 0 = banda inferior, 1 = banda superior
        "ancho":    ancho
    }


def calcular_atr(highs: list, lows: list, closes: list, periodo: int = 14) -> float:
    if len(closes) < 2:
        return closes[-1] * 0.02 if closes else 0.01

    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i]  - lows[i],
            abs(highs[i]  - closes[i-1]),
            abs(lows[i]   - closes[i-1])
        )
        trs.append(tr)

    if not trs:
        return closes[-1] * 0.02

    if len(trs) < periodo:
        return float(np.mean(trs))

    atr = float(np.mean(trs[:periodo]))
    for i in range(periodo, len(trs)):
        atr = (atr * (periodo - 1) + trs[i]) / periodo

    return atr


# ============================================================
# SCORE LONG (0-100)
# ============================================================

def _score_long(rsi: float, bb: dict, rr: float) -> int:
    score = 50
    # RSI más bajo = más sobrevendido = más puntos (hasta +30)
    score += int(max(0, min(30, config.RSI_OVERSOLD - rsi)))
    # R:R bueno = más puntos (hasta +15)
    score += min(15, int(rr * 4))
    # Precio muy pegado a BB inferior = +10
    if bb["posicion"] < 0.05:
        score += 10
    # ATR bajo (mercado comprimido, buen setup de reversión) = +5
    if bb["ancho"] > 0 and bb["posicion"] < 0.1:
        score += 5
    return min(100, score)


# ============================================================
# SCORE SHORT (0-100) — simétrico al LONG
# ============================================================

def _score_short(rsi: float, bb: dict, rr: float) -> int:
    score = 50
    # RSI más alto = más sobrecomprado = más puntos (hasta +30)
    score += int(max(0, min(30, rsi - config.RSI_OVERBOUGHT)))
    # R:R bueno = más puntos (hasta +15)
    score += min(15, int(rr * 4))
    # Precio muy pegado a BB superior = +10
    if bb["posicion"] > 0.95:
        score += 10
    # Precio comprimido en banda = +5
    if bb["ancho"] > 0 and bb["posicion"] > 0.9:
        score += 5
    return min(100, score)


# ============================================================
# ANÁLISIS LONG
# ============================================================

def analizar_par(par: str) -> dict:
    resultado = {
        "par": par, "señal": False, "lado": "LONG",
        "rsi": 50.0, "bb": {}, "atr": 0.0, "precio": 0.0,
        "sl": 0.0, "tp": 0.0, "rr": 0.0, "score": 0, "motivo": ""
    }

    klines = exchange.get_klines(par, intervalo="5m", limit=100)
    if len(klines) < 30:
        resultado["motivo"] = f"klines insuficientes ({len(klines)})"
        return resultado

    data = exchange.parsear_klines(klines)
    if len(data["closes"]) < 30:
        resultado["motivo"] = f"closes insuficientes ({len(data['closes'])})"
        return resultado

    closes = data["closes"]
    highs  = data["highs"]
    lows   = data["lows"]
    precio = closes[-1]

    if precio <= 0:
        resultado["motivo"] = "precio = 0"
        return resultado

    resultado["precio"] = precio

    rsi = calcular_rsi(closes, config.RSI_PERIODO)
    bb  = calcular_bb(closes, config.BB_PERIODO, config.BB_STD)
    atr = calcular_atr(highs, lows, closes, config.ATR_PERIODO)

    resultado["rsi"] = rsi
    resultado["bb"]  = bb
    resultado["atr"] = atr

    volumen = exchange.get_volumen_24h(par)
    if volumen < config.VOLUMEN_MIN_USD:
        resultado["motivo"] = f"vol bajo ${volumen:,.0f}"
        return resultado

    spread = exchange.get_spread_pct(par)
    if spread > config.SPREAD_MAX_PCT:
        resultado["motivo"] = f"spread {spread:.2f}%"
        return resultado

    # Condiciones LONG: RSI sobrevendido + precio en/bajo BB inferior
    if rsi >= config.RSI_OVERSOLD:
        resultado["motivo"] = f"RSI={rsi:.1f} (necesita <{config.RSI_OVERSOLD})"
        return resultado

    if not (bb["inferior"] > 0 and precio <= bb["inferior"] * 1.005):
        resultado["motivo"] = f"precio lejos BB inf (pos={bb['posicion']:.2f})"
        return resultado

    if atr <= 0:
        resultado["motivo"] = "ATR=0"
        return resultado

    sl = precio - (atr * config.SL_ATR_MULT)
    tp = precio + (atr * config.TP_ATR_MULT)

    riesgo    = precio - sl
    beneficio = tp - precio
    rr        = beneficio / riesgo if riesgo > 0 else 0

    if rr < config.RR_MINIMO:
        resultado["motivo"] = f"R:R={rr:.2f} < {config.RR_MINIMO}"
        return resultado

    score = _score_long(rsi, bb, rr)

    # ── Filtro de score mínimo ──
    score_min = getattr(config, "SCORE_MIN", 75)
    if score < score_min:
        resultado["motivo"] = f"score={score} < {score_min} (LONG RSI={rsi:.1f})"
        return resultado

    resultado.update({
        "señal": True, "lado": "LONG",
        "sl": sl, "tp": tp, "rr": rr, "score": score,
        "motivo": f"LONG RSI={rsi:.1f} BB_pos={bb['posicion']:.2f} R:R={rr:.2f} score={score}"
    })
    return resultado


# ============================================================
# ANÁLISIS SHORT — NUEVO
# ============================================================

def analizar_par_short(par: str) -> dict:
    """
    SHORT: RSI > RSI_OVERBOUGHT + precio en/sobre BB superior
    SL sobre BB superior, TP bajo BB media
    """
    resultado = {
        "par": par, "señal": False, "lado": "SHORT",
        "rsi": 50.0, "bb": {}, "atr": 0.0, "precio": 0.0,
        "sl": 0.0, "tp": 0.0, "rr": 0.0, "score": 0, "motivo": ""
    }

    klines = exchange.get_klines(par, intervalo="5m", limit=100)
    if len(klines) < 30:
        resultado["motivo"] = f"klines insuficientes ({len(klines)})"
        return resultado

    data = exchange.parsear_klines(klines)
    if len(data["closes"]) < 30:
        resultado["motivo"] = f"closes insuficientes ({len(data['closes'])})"
        return resultado

    closes = data["closes"]
    highs  = data["highs"]
    lows   = data["lows"]
    precio = closes[-1]

    if precio <= 0:
        resultado["motivo"] = "precio = 0"
        return resultado

    resultado["precio"] = precio

    rsi = calcular_rsi(closes, config.RSI_PERIODO)
    bb  = calcular_bb(closes, config.BB_PERIODO, config.BB_STD)
    atr = calcular_atr(highs, lows, closes, config.ATR_PERIODO)

    resultado["rsi"] = rsi
    resultado["bb"]  = bb
    resultado["atr"] = atr

    volumen = exchange.get_volumen_24h(par)
    if volumen < config.VOLUMEN_MIN_USD:
        resultado["motivo"] = f"vol bajo ${volumen:,.0f}"
        return resultado

    spread = exchange.get_spread_pct(par)
    if spread > config.SPREAD_MAX_PCT:
        resultado["motivo"] = f"spread {spread:.2f}%"
        return resultado

    # Condiciones SHORT: RSI sobrecomprado + precio en/sobre BB superior
    rsi_overbought = getattr(config, "RSI_OVERBOUGHT", 70)
    if rsi <= rsi_overbought:
        resultado["motivo"] = f"RSI={rsi:.1f} (SHORT necesita >{rsi_overbought})"
        return resultado

    if not (bb["superior"] > 0 and precio >= bb["superior"] * 0.995):
        resultado["motivo"] = f"precio lejos BB sup (pos={bb['posicion']:.2f})"
        return resultado

    if atr <= 0:
        resultado["motivo"] = "ATR=0"
        return resultado

    # SHORT: SL sobre entrada, TP bajo entrada
    sl = precio + (atr * config.SL_ATR_MULT)   # SL arriba del precio
    tp = precio - (atr * config.TP_ATR_MULT)   # TP abajo del precio

    riesgo    = sl - precio
    beneficio = precio - tp
    rr        = beneficio / riesgo if riesgo > 0 else 0

    if rr < config.RR_MINIMO:
        resultado["motivo"] = f"R:R={rr:.2f} < {config.RR_MINIMO}"
        return resultado

    score = _score_short(rsi, bb, rr)

    # ── Filtro de score mínimo ──
    score_min = getattr(config, "SCORE_MIN", 75)
    if score < score_min:
        resultado["motivo"] = f"score={score} < {score_min} (SHORT RSI={rsi:.1f})"
        return resultado

    resultado.update({
        "señal": True, "lado": "SHORT",
        "sl": sl, "tp": tp, "rr": rr, "score": score,
        "motivo": f"SHORT RSI={rsi:.1f} BB_pos={bb['posicion']:.2f} R:R={rr:.2f} score={score}"
    })
    return resultado


# ============================================================
# ANALIZAR TODOS — LONG Y SHORT
# ============================================================

def analizar_todos(pares: list) -> list:
    """
    Analiza lista de pares para LONG y SHORT.
    Solo retorna señales con score >= SCORE_MIN.
    Ordena por score descendente.
    """
    señales = []
    for par in pares:
        try:
            # LONG
            r_long = analizar_par(par)
            if r_long["señal"]:
                señales.append(r_long)
                if getattr(config, "MODO_DEBUG", False):
                    print(f"  ✓ LONG {par} score={r_long['score']}: {r_long['motivo']}")
            elif getattr(config, "MODO_DEBUG", False):
                print(f"  ✗ LONG  {par}: {r_long['motivo']}")

            # SHORT
            r_short = analizar_par_short(par)
            if r_short["señal"]:
                señales.append(r_short)
                if getattr(config, "MODO_DEBUG", False):
                    print(f"  ✓ SHORT {par} score={r_short['score']}: {r_short['motivo']}")
            elif getattr(config, "MODO_DEBUG", False):
                print(f"  ✗ SHORT {par}: {r_short['motivo']}")

        except Exception as e:
            print(f"  [ERROR] {par}: {e}")

    señales.sort(key=lambda x: x["score"], reverse=True)
    return señales
