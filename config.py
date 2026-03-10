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
RSI_OVERSOLD   = 35   # ← subido de 30 → más señales LONG
RSI_OVERBOUGHT = 65   # ← bajado de 70 → más señales SHORT
BB_PERIODO     = 20
BB_STD         = 2.0
ATR_PERIODO    = 14
EMA_PERIODO    = 200  # FILTRO CRÍTICO
# ── SL / TP (Matemática optimizada) ──────────────────
SL_ATR_MULT = 1.5
TP_ATR_MULT = 3.5
RR_MINIMO   = 1.5
# ── GESTIÓN DE RIESGO ────────────────────────────────
PARTIAL_TP_ACTIVO  = True
PARTIAL_TP_PCT     = 0.30        # 30% en TP1, 70% corre al TP final
PARTIAL_TP1_MULT   = 1.5         # ATR × 1.5 para TP1
TRAILING_ACTIVO    = True
TRAILING_ACTIVAR   = 1.5
TRAILING_DISTANCIA = 1.0
MAX_POSICIONES     = 3
LEVERAGE           = 5
MARGEN_USDT        = 10.0
SCORE_MIN          = 65          # ← bajado de 72 → más señales entran
# ── TIEMPOS ──────────────────────────────────────────
LOOP_SECONDS   = 60
TIME_EXIT_H    = 8
TIME_EXIT_HORAS = 8              # ← añadido alias (main.py usa este nombre)
# ── COSTOS REALES (Para el bot y backtest) ───────────
FEE_MARKET = 0.00045  # 0.045% BingX
SLIPPAGE   = 0.00020  # 0.02% Deslizamiento estimado
# ── PARES ────────────────────────────────────────────
PARES_PRIORITARIOS = [
    "FET-USDT", "SUI-USDT", "NEAR-USDT", "ARB-USDT",
    "LINK-USDT", "AVAX-USDT", "INJ-USDT", "OP-USDT",
]
PARES_BLOQUEADOS = []
