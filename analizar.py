import numpy as np
import pandas as pd
import config
import exchange

def calcular_rsi(closes, periodo=14):
    if len(closes) < periodo + 1: return 50.0
    series = pd.Series(closes)
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=periodo).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=periodo).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs)).iloc[-1]

def calcular_bb(closes, periodo=20, std=2.0):
    series = pd.Series(closes)
    ma = series.rolling(window=periodo).mean()
    sd = series.rolling(window=periodo).std()
    upper = ma + (std * sd)
    lower = ma - (std * sd)
    precio = closes[-1]
    posicion = (precio - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1]) if (upper.iloc[-1] - lower.iloc[-1]) != 0 else 0.5
    return {"sup": upper.iloc[-1], "inf": lower.iloc[-1], "posicion": posicion}

def calcular_atr(highs, lows, closes, periodo=14):
    if len(closes) < periodo + 1: return 0
    tr = []
    for i in range(1, len(closes)):
        tr.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    return pd.Series(tr).rolling(window=periodo).mean().iloc[-1]

def calcular_ema(closes, periodo=200):
    if len(closes) < periodo: return None
    return pd.Series(closes).ewm(span=periodo, adjust=False).mean().iloc[-1]

def analizar_par(par: str) -> dict:
    resultado = {"par": par, "señal": False, "lado": None, "motivo": "", "score": 0}
    klines = exchange.get_klines(par, "15m", 300) # Más velas para la EMA200
    
    if not klines or len(klines) < 200:
        resultado["motivo"] = "Pocos datos"
        return resultado

    closes = [k["close"] for k in klines]
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    precio = closes[-1]

    rsi = calcular_rsi(closes, config.RSI_PERIODO)
    bb = calcular_bb(closes, config.BB_PERIODO, config.BB_STD)
    atr = calcular_atr(highs, lows, closes, config.ATR_PERIODO)
    ema = calcular_ema(closes, config.EMA_PERIODO)

    sl_dist = atr * config.SL_ATR_MULT
    tp_dist = atr * config.TP_ATR_MULT
    tp1_dist = sl_dist # TP1 a 1:1 de riesgo

    # FILTRO DE TENDENCIA
    tendencia_alcista = precio > ema
    tendencia_bajista = precio < ema

    # LÓGICA LONG (Solo si tendencia es alcista)
    if tendencia_alcista and rsi < config.RSI_OVERSOLD and bb["posicion"] <= 0.1:
        score = (config.RSI_OVERSOLD - rsi) * 2 + (0.1 - bb["posicion"]) * 100
        resultado.update({
            "señal": True, "lado": "LONG", "sl": precio - sl_dist,
            "tp": precio + tp_dist, "tp1": precio + tp1_dist,
            "score": score, "motivo": f"LONG EMA_OK RSI={rsi:.1f}"
        })
        return resultado

    # LÓGICA SHORT (Solo si tendencia es bajista)
    if tendencia_bajista and rsi > config.RSI_OVERBOUGHT and bb["posicion"] >= 0.9:
        score = (rsi - config.RSI_OVERBOUGHT) * 2 + (bb["posicion"] - 0.9) * 100
        resultado.update({
            "señal": True, "lado": "SHORT", "sl": precio + sl_dist,
            "tp": precio - tp_dist, "tp1": precio - tp1_dist,
            "score": score, "motivo": f"SHORT EMA_OK RSI={rsi:.1f}"
        })
        return resultado

    resultado["motivo"] = "Sin confluencia o contra tendencia"
    return resultado
