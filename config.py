"""
config.py — Configuración central del bot
Generado con los TOP 15 pares del scanner BingX 2026-03-04
Edita este archivo o usa backtest.py para regenerarlo automáticamente
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# CREDENCIALES (se leen del .env)
# ============================================================
BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# PARES ACTIVOS — TOP 15 del último backtest
# ============================================================
PARES = [
    "RSR-USDT",
    "LINK-USDT",
    "ZEC-USDT",
    "AKE-USDT",
    "BOME-USDT",
    "DEEP-USDT",
    "BLESS-USDT",
    "VANRY-USDT",
    "PROVE-USDT",
    "BMT-USDT",
    "ZEN-USDT",
    "SUSHI-USDT",
    "SQD-USDT",
    "CRO-USDT",
    "SOL-USDT",
]

# ============================================================
# SEÑALES DE ENTRADA
# ============================================================
RSI_PERIODO      = 14
RSI_OVERSOLD     = 30          # Entrada LONG cuando RSI < este valor
BB_PERIODO       = 20
BB_STD           = 2.0         # Bandas de Bollinger σ
ATR_PERIODO      = 14
VOLUMEN_MIN_USD  = 1_000_000   # Volumen mínimo 24h en USD
SPREAD_MAX_PCT   = 1.5         # Spread máximo permitido %

# ============================================================
# GESTIÓN DE RIESGO
# ============================================================
LEVERAGE         = 2           # Apalancamiento (conservador)
SL_ATR_MULT      = 1.5         # Stop Loss = entrada - (ATR × mult)
TP_ATR_MULT      = 2.5         # Take Profit = entrada + (ATR × mult)  → R:R 1.67
RIESGO_POR_TRADE = 0.02        # 2% del balance por trade
MAX_POSICIONES   = 5           # Máximo posiciones simultáneas
RR_MINIMO        = 1.5         # Risk/Reward mínimo para entrar

# ============================================================
# COMPOUND — REINVERSIÓN AUTOMÁTICA
# ============================================================
COMPOUND         = True        # True = tamaño dinámico según balance real
BALANCE_INICIAL  = 100.0       # Solo para referencia inicial

# ============================================================
# CIRCUIT BREAKER
# ============================================================
MAX_PNL_NEGATIVO_DIA = -0.05   # -5% del balance → parar el día
MAX_PERDIDAS_SEGUIDAS = 4      # 4 pérdidas seguidas → pausa 1 hora

# ============================================================
# OPERACIÓN
# ============================================================
CICLO_SEGUNDOS   = 300         # Intervalo del loop principal (5 min)
MODO_DEMO        = False       # True = simular sin órdenes reales
MODO_DEBUG       = True        # True = logs detallados

# ============================================================
# LEARNER — Umbrales para penalizar/rehabilitar pares
# ============================================================
LEARNER_MIN_TRADES     = 5     # Mínimo trades para evaluar un par
LEARNER_MIN_WR         = 40.0  # WR mínimo para seguir usando el par
LEARNER_MIN_PF         = 1.0   # PF mínimo
LEARNER_PENALIZACION_H = 24    # Horas de penalización
LEARNER_CICLO_H        = 6     # Cada cuántas horas evalúa el learner
