"""
analizar.py — Señales RSI + Bollinger Bands + ATR
FIXED: usa exchange.parsear_klines que maneja dicts Y arrays de BingX
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

    arr     = np.array(closes, dtype=float)
    deltas  = np.diff(arr)
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
        "posicion": posicion,
        "ancho":    ancho
    }


def calcular_atr(highs: list, lows: list, closes: list, periodo: int = 14) -> float:
    if len(closes) < 2:
        return closes[-1] * 0.02 if closes else 0.01

    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
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
# ANÁLISIS DE UN PAR
# ============================================================

def analizar_par(par: str) -> dict:
    resultado = {
        "par":    par,
        "señal":  False,
        "rsi":    50.0,
        "bb":     {},
        "atr":    0.0,
        "precio": 0.0,
        "sl":     0.0,
        "tp":     0.0,
        "rr":     0.0,
        "score":  0,
        "motivo": ""
    }

    # Obtener klines
    klines = exchange.get_klines(par, intervalo="5m", limit=100)
    if len(klines) < 30:
        resultado["motivo"] = f"klines insuficientes ({len(klines)})"
        return resultado

    # Parsear — ahora soporta dict Y array
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

    # Condiciones de entrada LONG
    condicion_rsi = rsi < config.RSI_OVERSOLD
    condicion_bb  = bb["inferior"] > 0 and precio <= bb["inferior"] * 1.002

    if not condicion_rsi:
        resultado["motivo"] = f"RSI={rsi:.1f} (necesita <{config.RSI_OVERSOLD})"
        return resultado

    if not condicion_bb:
        resultado["motivo"] = f"precio lejos BB inferior (pos={bb['posicion']:.2f})"
        return resultado

    # SL / TP
    if atr <= 0:
        resultado["motivo"] = "ATR = 0"
        return resultado

    sl = precio - (atr * config.SL_ATR_MULT)
    tp = precio + (atr * config.TP_ATR_MULT)

    riesgo    = precio - sl
    beneficio = tp - precio
    rr = beneficio / riesgo if riesgo > 0 else 0

    resultado["sl"] = sl
    resultado["tp"] = tp
    resultado["rr"] = rr

    if rr < config.RR_MINIMO:
        resultado["motivo"] = f"R:R={rr:.2f} < {config.RR_MINIMO}"
        return resultado

    # Score
    score = 50
    score += int(max(0, config.RSI_OVERSOLD - rsi))
    score += min(20, int(rr * 5))
    if bb["posicion"] < 0.1:
        score += 10
    score = min(100, score)

    resultado["score"]  = score
    resultado["señal"]  = True
    resultado["motivo"] = f"RSI={rsi:.1f} | BB_pos={bb['posicion']:.2f} | R:R={rr:.2f} | score={score}"

    return resultado


def analizar_todos(pares: list) -> list:
    señales = []
    for par in pares:
        try:
            r = analizar_par(par)
            if r["señal"]:
                señales.append(r)
                if config.MODO_DEBUG:
                    print(f"  ✓ SEÑAL {par}: {r['motivo']}")
            elif config.MODO_DEBUG:
                print(f"  ✗ {par}: {r['motivo']}")
        except Exception as e:
            print(f"  [ERROR] {par}: {e}")
            if config.MODO_DEBUG:
                import traceback
                traceback.print_exc()

    señales.sort(key=lambda x: x["score"], reverse=True)
    return señales
