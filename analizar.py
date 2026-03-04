"""
analizar.py — Cálculo de señales RSI + Bollinger Bands + ATR
Entrada LONG cuando: RSI < umbral Y precio toca/cruza BB inferior
"""

import numpy as np
import config
import exchange


def calcular_rsi(closes: list, periodo: int = 14) -> float:
    if len(closes) < periodo + 1:
        return 50.0

    deltas = np.diff(closes)
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
    if len(closes) < periodo:
        return {"media": 0, "superior": 0, "inferior": 0, "posicion": 0.5}

    serie = np.array(closes[-periodo:])
    media = np.mean(serie)
    std   = np.std(serie)

    superior = media + std_mult * std
    inferior = media - std_mult * std
    ancho    = superior - inferior

    precio_actual = closes[-1]
    posicion = (precio_actual - inferior) / ancho if ancho > 0 else 0.5

    return {
        "media":    media,
        "superior": superior,
        "inferior": inferior,
        "posicion": posicion,   # 0 = en banda inferior, 1 = en banda superior
        "ancho":    ancho
    }


def calcular_atr(highs: list, lows: list, closes: list, periodo: int = 14) -> float:
    if len(closes) < periodo + 1:
        return closes[-1] * 0.02 if closes else 0.01

    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1])
        )
        trs.append(tr)

    if len(trs) < periodo:
        return np.mean(trs)

    atr = np.mean(trs[:periodo])
    for i in range(periodo, len(trs)):
        atr = (atr * (periodo - 1) + trs[i]) / periodo

    return atr


def _parsear_klines(klines: list) -> dict:
    """Extrae arrays de OHLCV desde los klines de BingX"""
    opens  = []
    highs  = []
    lows   = []
    closes = []
    vols   = []

    for k in klines:
        try:
            # BingX devuelve: [time, open, high, low, close, volume, ...]
            opens.append(float(k[1]))
            highs.append(float(k[2]))
            lows.append(float(k[3]))
            closes.append(float(k[4]))
            vols.append(float(k[5]))
        except (IndexError, ValueError, TypeError):
            continue

    return {
        "opens":  opens,
        "highs":  highs,
        "lows":   lows,
        "closes": closes,
        "vols":   vols
    }


def analizar_par(par: str) -> dict:
    """
    Analiza un par y retorna señal de entrada o None.

    Retorna dict con:
        señal: True/False
        rsi: float
        bb: dict
        atr: float
        precio: float
        sl: float
        tp: float
        rr: float
        score: int (0-100)
        motivo: str
    """
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
        resultado["motivo"] = "insuficientes klines"
        return resultado

    data = _parsear_klines(klines)
    if not data["closes"]:
        resultado["motivo"] = "error parseando klines"
        return resultado

    precio = data["closes"][-1]
    resultado["precio"] = precio

    # RSI
    rsi = calcular_rsi(data["closes"], config.RSI_PERIODO)
    resultado["rsi"] = rsi

    # Bollinger Bands
    bb = calcular_bb(data["closes"], config.BB_PERIODO, config.BB_STD)
    resultado["bb"] = bb

    # ATR
    atr = calcular_atr(data["highs"], data["lows"], data["closes"], config.ATR_PERIODO)
    resultado["atr"] = atr

    # Volumen
    volumen = exchange.get_volumen_24h(par)

    # Spread
    spread = exchange.get_spread_pct(par)

    # ============================================================
    # FILTROS DE CALIDAD
    # ============================================================
    if volumen < config.VOLUMEN_MIN_USD:
        resultado["motivo"] = f"volumen insuficiente ${volumen:,.0f}"
        return resultado

    if spread > config.SPREAD_MAX_PCT:
        resultado["motivo"] = f"spread alto {spread:.2f}%"
        return resultado

    # ============================================================
    # CONDICIONES DE ENTRADA LONG
    # ============================================================
    condicion_rsi = rsi < config.RSI_OVERSOLD
    condicion_bb  = precio <= bb["inferior"] * 1.002  # precio en/cerca banda inferior

    if not (condicion_rsi and condicion_bb):
        motivos = []
        if not condicion_rsi: motivos.append(f"RSI={rsi:.1f} (necesita <{config.RSI_OVERSOLD})")
        if not condicion_bb:  motivos.append(f"precio lejos de BB inferior ({bb['posicion']:.2f})")
        resultado["motivo"] = " | ".join(motivos)
        return resultado

    # ============================================================
    # CALCULAR SL / TP
    # ============================================================
    sl = precio - (atr * config.SL_ATR_MULT)
    tp = precio + (atr * config.TP_ATR_MULT)

    riesgo   = precio - sl
    beneficio= tp - precio
    rr = beneficio / riesgo if riesgo > 0 else 0

    resultado["sl"] = sl
    resultado["tp"] = tp
    resultado["rr"] = rr

    if rr < config.RR_MINIMO:
        resultado["motivo"] = f"R:R insuficiente {rr:.2f} (mínimo {config.RR_MINIMO})"
        return resultado

    # ============================================================
    # SCORE 0-100
    # ============================================================
    score = 50
    score += max(0, (config.RSI_OVERSOLD - rsi))   # Más puntos cuanto más bajo el RSI
    score += min(20, int(rr * 5))                  # Hasta 20 pts por R:R
    if bb["posicion"] < 0.1:                       # Precio muy cerca del fondo BB
        score += 10
    score = min(100, score)

    resultado["score"]  = score
    resultado["señal"]  = True
    resultado["motivo"] = f"RSI={rsi:.1f} | BB_pos={bb['posicion']:.2f} | R:R={rr:.2f} | Score={score}"

    return resultado


def analizar_todos(pares: list) -> list:
    """
    Analiza una lista de pares y retorna los que tienen señal,
    ordenados por score descendente.
    """
    señales = []
    for par in pares:
        try:
            resultado = analizar_par(par)
            if resultado["señal"]:
                señales.append(resultado)
                if config.MODO_DEBUG:
                    print(f"  ✓ SEÑAL {par}: {resultado['motivo']}")
            elif config.MODO_DEBUG:
                print(f"  ✗ {par}: {resultado['motivo']}")
        except Exception as e:
            print(f"  [ERROR] {par}: {e}")

    señales.sort(key=lambda x: x["score"], reverse=True)
    return señales
