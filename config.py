# ══════════════════════════════════════════════════════
# config.py — BingX RSI+BB Bot v4.0
# NUEVO: SCORE_MIN=75, RSI_OVERBOUGHT para SHORT
# ══════════════════════════════════════════════════════
import os

# ── CREDENCIALES ─────────────────────────────────────
BINGX_API_KEY    = os.environ.get("BINGX_API_KEY",    "")
BINGX_SECRET_KEY = os.environ.get("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── MODO ─────────────────────────────────────────────
MODO_DEMO        = False    # True = paper trading
MODO_DEBUG       = False    # True = logs detallados
BALANCE_INICIAL  = 100.0    # solo usado en MODO_DEMO

# ── INDICADORES ──────────────────────────────────────
RSI_PERIODO   = 14
RSI_OVERSOLD  = 35    # LONG cuando RSI < 35
RSI_OVERBOUGHT= 65    # SHORT cuando RSI > 65

BB_PERIODO    = 20
BB_STD        = 2.0

ATR_PERIODO   = 14

# ── FILTRO DE SCORE ───────────────────────────────────
# Solo notifica y ejecuta señales con score >= SCORE_MIN
# 0  = todas las señales
# 75 = solo alta convicción (recomendado)
# 90 = solo señales extremas
SCORE_MIN     = 75

# ── CALIDAD DE MERCADO ────────────────────────────────
VOLUMEN_MIN_USD = 500_000    # volumen mínimo 24h en USDT
SPREAD_MAX_PCT  = 1.5        # spread máximo %

# ── SL / TP ──────────────────────────────────────────
SL_ATR_MULT   = 1.5    # SL = precio ± 1.5 × ATR
TP_ATR_MULT   = 3.0    # TP = precio ± 3.0 × ATR  → R:R ≈ 2.0
RR_MINIMO     = 1.5    # descartar si R:R < 1.5

# ── RIESGO ────────────────────────────────────────────
RIESGO_POR_TRADE = 0.02   # 2% del balance por trade
LEVERAGE         = 2       # apalancamiento
MAX_POSICIONES   = 3       # máx posiciones simultáneas

# ── CIRCUIT BREAKER ───────────────────────────────────
CB_MAX_DAILY_LOSS_PCT   = 0.05   # parar si pierde 5% del balance en el día
CB_MAX_CONSECUTIVE_LOSS = 4      # parar tras 4 pérdidas seguidas

# ── OPERACIÓN ─────────────────────────────────────────
LOOP_SECONDS  = 600   # escanear cada 10 minutos

VERSION = "BingX-RSI+BB-v4.0"
