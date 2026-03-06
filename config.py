"""
config.py — Configuración central del bot
FIXES:
  - RSI_OVERSOLD subido a 35 (más señales, sigue siendo sobreventa)
  - VOLUMEN_MIN_USD bajado a 500k (pares pequeños como PROVE, BLESS, BMT)
  - BB_STD bajado a 1.8 (bandas más estrechas = más toques)
  - Eliminados pares de bajo volumen dudoso (AKE, PROVE, BLESS, BMT)
  - Añadidos pares de alto volumen para compensar
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
# PARES ACTIVOS
# Reemplazados pares de volumen dudoso por pares líquidos
# ============================================================
PARES = [
    "SOL-USDT",
    "LINK-USDT",
    "SUSHI-USDT",
    "ZEC-USDT",
    "CRO-USDT",
    "RSR-USDT",
    "ZEN-USDT",
    "SQD-USDT",
    "BOME-USDT",
    "DEEP-USDT",
    "VANRY-USDT",
    # Nuevos pares líquidos de reemplazo
    "DOGE-USDT",
    "XRP-USDT",
    "ADA-USDT",
    "MATIC-USDT",
    "DOT-USDT",
    "AVAX-USDT",
    "LTC-USDT",
    "FIL-USDT",
    "ATOM-USDT",
]

# ============================================================
# SEÑALES DE ENTRADA
# ============================================================
RSI_PERIODO      = 14
RSI_OVERSOLD     = 35          # FIX: era 30, subido a 35 para más señales
BB_PERIODO       = 20
BB_STD           = 1.8         # FIX: era 2.0, bajado a 1.8 para bandas más estrechas
ATR_PERIODO      = 14
VOLUMEN_MIN_USD  = 500_000     # FIX: era 1_000_000, bajado a 500k
SPREAD_MAX_PCT   = 1.5

# ============================================================
# GESTIÓN DE RIESGO
# ============================================================
LEVERAGE         = 2
SL_ATR_MULT      = 1.5         # SL = entrada - (ATR × 1.5)
TP_ATR_MULT      = 2.5         # TP = entrada + (ATR × 2.5) → R:R 1.67
RIESGO_POR_TRADE = 0.02        # 2% del balance por trade
MAX_POSICIONES   = 5
RR_MINIMO        = 1.5

# ============================================================
# COMPOUND — REINVERSIÓN AUTOMÁTICA
# ============================================================
COMPOUND         = True
BALANCE_INICIAL  = 100.0

# ============================================================
# CIRCUIT BREAKER
# ============================================================
MAX_PNL_NEGATIVO_DIA  = -0.05   # -5% del balance → parar el día
MAX_PERDIDAS_SEGUIDAS = 4        # 4 pérdidas seguidas → pausa 1 hora

# ============================================================
# OPERACIÓN
# ============================================================
CICLO_SEGUNDOS   = 300          # 5 minutos
MODO_DEMO        = False
MODO_DEBUG       = True

# ============================================================
# LEARNER
# ============================================================
LEARNER_MIN_TRADES     = 5
LEARNER_MIN_WR         = 40.0
LEARNER_MIN_PF         = 1.0
LEARNER_PENALIZACION_H = 24
LEARNER_CICLO_H        = 6
