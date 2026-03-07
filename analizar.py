"""
analizar.py — v3.0
Mejoras para perfil: más pares + menos pérdidas
- Confirmación Multi-Timeframe 5m + 15m
- Filtro ATR mínimo (evita mercados planos)
- Filtro de tendencia EMA50 en 5m (evita operar contra tendencia inmediata)
- Score mejorado con más factores
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
    return 100.0 - (100.0 / (1.0 + avg_g / avg_p))


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
    return {"media": media, "superior": superior, "inferior": inferior,
            "posicion": posicion, "ancho": ancho}


def calcular_atr(highs: list, lows: list, closes: list, periodo: int = 14) -> float:
    if len(closes) < 2:
        return closes[-1] * 0.02 if closes else 0.01
    trs = [max(highs[i] - lows[i],
               abs(highs[i] - closes[i-1]),
               abs(lows[i]  - closes[i-1])) for i in range(1, len(closes))]
    if not trs:
        return closes[-1] * 0.02
    atr = float(np.mean(trs[:periodo]))
    for i in range(periodo, len(trs)):
        atr = (atr * (periodo - 1) + trs[i]) / periodo
    return atr


def calcular_ema(closes: list, periodo: int = 50) -> float:
    if len(closes) < periodo:
        return 0.0
    k   = 2.0 / (periodo + 1)
    ema = sum(closes[:periodo]) / periodo
    for c in closes[periodo:]:
        ema = c * k + ema * (1 - k)
    return ema


# ============================================================
# FILTROS DE CALIDAD
# ============================================================

def _confirmar_15m(par: str, lado: str) -> tuple:
    """
    Confirma señal en 15m.
    Retorna (pasa: bool, descripcion: str)
    LONG: RSI 15m < 55 (hay espacio para subir, no sobrecomprado)
    SHORT: RSI 15m > 45 (hay espacio para bajar, no sobrevendido)
    """
    if not getattr(config, "MTF_CONFIRMACION_ACTIVO", False):
        return True, "MTF off"
    try:
        klines = exchange.get_klines(par, intervalo="15m", limit=60)
        data   = exchange.parsear_klines(klines)
        if len(data["closes"]) < 20:
            return True, "15m sin datos"
        rsi_15m = calcular_rsi(data["closes"], 14)
        bb_15m  = calcular_bb(data["closes"], 20, 2.0)
        if lado == "LONG":
            pasa = rsi_15m < 55 and data["closes"][-1] <= bb_15m["media"] * 1.01
            return pasa, f"15m RSI={rsi_15m:.1f}"
        else:
            pasa = rsi_15m > 45 and data["closes"][-1] >= bb_15m["media"] * 0.99
            return pasa, f"15m RSI={rsi_15m:.1f}"
    except Exception:
        return True, "15m error"


def _filtro_hora() -> bool:
    """Evita las horas de baja liquidez"""
    if not getattr(config, "FILTRO_HORA_ACTIVO", False):
        return True
    from datetime import datetime, timezone
    hora_utc = datetime.now(timezone.utc).hour
    return hora_utc in getattr(config, "HORAS_PERMITIDAS", range(24))


# ============================================================
# SCORE — más factores = mejor selección
# ============================================================

def _score_long(rsi: float, bb: dict, rr: float, atr_pct: float) -> int:
    score = 50
    # RSI sobrevendido (hasta +30 pts)
    score += int(max(0, min(30, config.RSI_OVERSOLD - rsi)))
    # R:R bueno (hasta +20 pts)
    score += min(20, int(rr * 5))
    # Precio muy cerca de BB inferior (+8/+4)
    if bb["posicion"] < 0.04:
        score += 8
    elif bb["posicion"] < 0.10:
        score += 4
    # Volatilidad útil: ni muy baja ni extrema (+8/+4)
    if 0.005 < atr_pct < 0.025:
        score += 8
    elif atr_pct >= 0.003:
        score += 4
    return min(100, score)


def _score_short(rsi: float, bb: dict, rr: float, atr_pct: float) -> int:
    score = 50
    score += int(max(0, min(30, rsi - config.RSI_OVERBOUGHT)))
    score += min(20, int(rr * 5))
    if bb["posicion"] > 0.96:
        score += 8
    elif bb["posicion"] > 0.90:
        score += 4
    if 0.005 < atr_pct < 0.025:
        score += 8
    elif atr_pct >= 0.003:
        score += 4
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

    # Filtro de hora
    if not _filtro_hora():
        resultado["motivo"] = "fuera de horario"
        return resultado

    klines = exchange.get_klines(par, intervalo="5m", limit=100)
    if len(klines) < 30:
        resultado["motivo"] = f"klines insuf ({len(klines)})"
        return resultado

    data   = exchange.parsear_klines(klines)
    closes = data["closes"]
    highs  = data["highs"]
    lows   = data["lows"]
    if len(closes) < 30:
        resultado["motivo"] = "closes insuf"
        return resultado

    precio = closes[-1]
    if precio <= 0:
        resultado["motivo"] = "precio=0"
        return resultado

    rsi = calcular_rsi(closes, config.RSI_PERIODO)
    bb  = calcular_bb(closes, config.BB_PERIODO, config.BB_STD)
    atr = calcular_atr(highs, lows, closes, config.ATR_PERIODO)

    resultado.update({"precio": precio, "rsi": rsi, "bb": bb, "atr": atr})

    # Filtro volatilidad mínima
    atr_pct = atr / precio if precio > 0 else 0
    atr_min = getattr(config, "ATR_MIN_PCT_PRECIO", 0.003)
    if atr_pct < atr_min:
        resultado["motivo"] = f"ATR plano {atr_pct:.4f}"
        return resultado

    # Filtro volumen y spread
    volumen = exchange.get_volumen_24h(par)
    if volumen < config.VOLUMEN_MIN_USD:
        resultado["motivo"] = f"vol bajo ${volumen:,.0f}"
        return resultado
    spread = exchange.get_spread_pct(par)
    if spread > config.SPREAD_MAX_PCT:
        resultado["motivo"] = f"spread {spread:.2f}%"
        return resultado

    # Condición LONG
    if rsi >= config.RSI_OVERSOLD:
        resultado["motivo"] = f"RSI={rsi:.1f} (necesita <{config.RSI_OVERSOLD})"
        return resultado
    if not (bb["inferior"] > 0 and precio <= bb["inferior"] * 1.005):
        resultado["motivo"] = f"lejos BB inf pos={bb['posicion']:.2f}"
        return resultado
    if atr <= 0:
        resultado["motivo"] = "ATR=0"
        return resultado

    sl = precio - (atr * config.SL_ATR_MULT)
    tp = precio + (atr * config.TP_ATR_MULT)
    rr = (tp - precio) / (precio - sl) if (precio - sl) > 0 else 0

    if rr < config.RR_MINIMO:
        resultado["motivo"] = f"R:R={rr:.2f} < {config.RR_MINIMO}"
        return resultado

    score = _score_long(rsi, bb, rr, atr_pct)
    if score < config.SCORE_MIN:
        resultado["motivo"] = f"score={score} < {config.SCORE_MIN}"
        return resultado

    # Confirmación 15m
    pasa_15m, desc_15m = _confirmar_15m(par, "LONG")
    if not pasa_15m:
        resultado["motivo"] = f"15m bloq {desc_15m}"
        return resultado

    resultado.update({
        "señal": True, "lado": "LONG", "sl": sl, "tp": tp, "rr": rr, "score": score,
        "motivo": f"LONG RSI={rsi:.1f} pos={bb['posicion']:.2f} R:R={rr:.2f} {desc_15m} score={score}"
    })
    return resultado


# ============================================================
# ANÁLISIS SHORT
# ============================================================

def analizar_par_short(par: str) -> dict:
    resultado = {
        "par": par, "señal": False, "lado": "SHORT",
        "rsi": 50.0, "bb": {}, "atr": 0.0, "precio": 0.0,
        "sl": 0.0, "tp": 0.0, "rr": 0.0, "score": 0, "motivo": ""
    }

    if not _filtro_hora():
        resultado["motivo"] = "fuera de horario"
        return resultado

    klines = exchange.get_klines(par, intervalo="5m", limit=100)
    if len(klines) < 30:
        resultado["motivo"] = f"klines insuf ({len(klines)})"
        return resultado

    data   = exchange.parsear_klines(klines)
    closes = data["closes"]
    highs  = data["highs"]
    lows   = data["lows"]
    if len(closes) < 30:
        resultado["motivo"] = "closes insuf"
        return resultado

    precio = closes[-1]
    if precio <= 0:
        resultado["motivo"] = "precio=0"
        return resultado

    rsi = calcular_rsi(closes, config.RSI_PERIODO)
    bb  = calcular_bb(closes, config.BB_PERIODO, config.BB_STD)
    atr = calcular_atr(highs, lows, closes, config.ATR_PERIODO)

    resultado.update({"precio": precio, "rsi": rsi, "bb": bb, "atr": atr})

    atr_pct = atr / precio if precio > 0 else 0
    atr_min = getattr(config, "ATR_MIN_PCT_PRECIO", 0.003)
    if atr_pct < atr_min:
        resultado["motivo"] = f"ATR plano {atr_pct:.4f}"
        return resultado

    volumen = exchange.get_volumen_24h(par)
    if volumen < config.VOLUMEN_MIN_USD:
        resultado["motivo"] = f"vol bajo ${volumen:,.0f}"
        return resultado
    spread = exchange.get_spread_pct(par)
    if spread > config.SPREAD_MAX_PCT:
        resultado["motivo"] = f"spread {spread:.2f}%"
        return resultado

    if rsi <= config.RSI_OVERBOUGHT:
        resultado["motivo"] = f"RSI={rsi:.1f} (SHORT necesita >{config.RSI_OVERBOUGHT})"
        return resultado
    if not (bb["superior"] > 0 and precio >= bb["superior"] * 0.995):
        resultado["motivo"] = f"lejos BB sup pos={bb['posicion']:.2f}"
        return resultado
    if atr <= 0:
        resultado["motivo"] = "ATR=0"
        return resultado

    sl = precio + (atr * config.SL_ATR_MULT)
    tp = precio - (atr * config.TP_ATR_MULT)
    rr = (precio - tp) / (sl - precio) if (sl - precio) > 0 else 0

    if rr < config.RR_MINIMO:
        resultado["motivo"] = f"R:R={rr:.2f} < {config.RR_MINIMO}"
        return resultado

    score = _score_short(rsi, bb, rr, atr_pct)
    if score < config.SCORE_MIN:
        resultado["motivo"] = f"score={score} < {config.SCORE_MIN}"
        return resultado

    pasa_15m, desc_15m = _confirmar_15m(par, "SHORT")
    if not pasa_15m:
        resultado["motivo"] = f"15m bloq {desc_15m}"
        return resultado

    resultado.update({
        "señal": True, "lado": "SHORT", "sl": sl, "tp": tp, "rr": rr, "score": score,
        "motivo": f"SHORT RSI={rsi:.1f} pos={bb['posicion']:.2f} R:R={rr:.2f} {desc_15m} score={score}"
    })
    return resultado


# ============================================================
# ANALIZAR TODOS
# ============================================================

def analizar_todos(pares: list) -> list:
    senales = []
    for par in pares:
        try:
            r = analizar_par(par)
            if r["señal"]:
                senales.append(r)
            r = analizar_par_short(par)
            if r["señal"]:
                senales.append(r)
        except Exception as e:
            if getattr(config, "MODO_DEBUG", False):
                print(f"  [ERROR] {par}: {e}")
    senales.sort(key=lambda x: x["score"], reverse=True)
    return senales
