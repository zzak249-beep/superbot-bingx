"""
config.py — Configuración central del bot
Los pares se cargan automáticamente desde config_pares.py
Para regenerar: python scanner_pares.py
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# CREDENCIALES
# ============================================================
BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# PARES — cargados desde config_pares.py (generado por scanner)
# ============================================================
_PARES_RESPALDO = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "XRP-USDT",
    "ADA-USDT", "AVAX-USDT", "LINK-USDT", "DOT-USDT", "SUI-USDT",
]

try:
    from config_pares import PARES
    print(f"[CONFIG] {len(PARES)} pares cargados desde config_pares.py")
except ImportError:
    PARES = _PARES_RESPALDO
    print(f"[CONFIG] Usando {len(PARES)} pares de respaldo. Ejecuta scanner_pares.py")

# ============================================================
# SEÑALES DE ENTRADA
# ============================================================
RSI_PERIODO      = 14
RSI_OVERSOLD     = 45          # Señal cuando RSI < este valor
BB_PERIODO       = 20
BB_STD           = 1.8
ATR_PERIODO      = 14
VOLUMEN_MIN_USD  = 500_000
SPREAD_MAX_PCT   = 1.5

# ============================================================
# GESTIÓN DE RIESGO
# ============================================================
LEVERAGE         = 2
SL_ATR_MULT      = 1.5
TP_ATR_MULT      = 2.5         # R:R = 1.67
RIESGO_POR_TRADE = 0.02        # 2% del balance por trade
MAX_POSICIONES   = 5
RR_MINIMO        = 1.5

# ============================================================
# COMPOUND
# ============================================================
COMPOUND         = True
BALANCE_INICIAL  = 100.0

# ============================================================
# CIRCUIT BREAKER
# ============================================================
MAX_PNL_NEGATIVO_DIA  = -0.05
MAX_PERDIDAS_SEGUIDAS = 4

# ============================================================
# OPERACIÓN
# ============================================================
# Con 400+ pares el ciclo tarda ~10min en analizar todos.
# CICLO_SEGUNDOS = 600 (10 min) es razonable para no saturar la API.
CICLO_SEGUNDOS   = 600
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
