import os

# ── CREDENCIALES (Railway lee de variables de entorno) ──
BINGX_API_KEY    = os.environ.get("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.environ.get("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── MODO ─────────────────────────────────────────────
MODO_DEMO  = False
MODO_DEBUG = False
VERSION    = "v6.0-PRO"

# ── INDICADORES ──────────────────────────────────────
RSI_PERIODO    = 14
RSI_OVERSOLD   = 30  # Más estricto para mayor calidad
RSI_OVERBOUGHT = 70  # Más estricto
BB_PERIODO     = 20
BB_STD         = 2.0
ATR_PERIODO    = 14
EMA_PERIODO    = 200 # FILTRO CRÍTICO

# ── SL / TP (Matemática optimizada) ──────────────────
SL_ATR_MULT = 1.5
TP_ATR_MULT = 3.5  # Aumentamos el target final
RR_MINIMO   = 1.5

# ── GESTIÓN DE RIESGO ────────────────────────────────
PARTIAL_TP_PCT = 0.30 # Cerramos solo el 30% en TP1. El 70% corre al TP final.
MAX_POSICIONES = 3    # No te sobreapalancques
LEVERAGE       = 5    # 7x era arriesgado, 5x es más seguro para 15m
MARGEN_USDT    = 10.0 # Cantidad por trade
SCORE_MIN      = 72

# ── TIEMPOS ──────────────────────────────────────────
LOOP_SECONDS = 60
TIME_EXIT_H  = 8      # Cerrar si no se mueve en 8h

# ── COSTOS REALES (Para el bot y backtest) ───────────
FEE_MARKET = 0.00045  # 0.045% BingX
SLIPPAGE   = 0.00020  # 0.02% Deslizamiento estimado
