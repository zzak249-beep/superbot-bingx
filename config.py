# ══════════════════════════════════════════════════════
# config.py — BingX RSI+BB Bot v7.0
# AJUSTADO: backtest real + investigación 2025
# ══════════════════════════════════════════════════════
import os

# ── CREDENCIALES ─────────────────────────────────────
BINGX_API_KEY    = os.environ.get("BINGX_API_KEY",    "")
BINGX_SECRET_KEY = os.environ.get("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── MODO ─────────────────────────────────────────────
MODO_DEMO    = False
MODO_DEBUG   = False

# ── INDICADORES BASE ─────────────────────────────────
RSI_PERIODO    = 14
RSI_OVERSOLD   = 35
RSI_OVERBOUGHT = 65
BB_PERIODO     = 20
BB_STD         = 2.0
ATR_PERIODO    = 14

# ── STOCHRSI (NUEVO v7) ───────────────────────────────
# Confirma que el momentum RSI está en su punto más extremo
# K < 20 = oversold extremo → confirma LONG (+12 score)
# K > 80 = overbought extremo → confirma SHORT (+12 score)
# Cruce K sobre D en zona extrema → +6 score adicional
STOCHRSI_PERIODO  = 14
STOCHRSI_K        = 3      # suavizado K
STOCHRSI_OS       = 20     # oversold
STOCHRSI_OB       = 80     # overbought

# ── ADX ───────────────────────────────────────────────
ADX_PERIODO = 14
ADX_MAX     = 25

# ── EMA200 — DESACTIVADO (backtest confirma) ─────────
# Sin EMA200: 811t +$14.19 PF:2.0 ✅
# Con EMA200:  30t  -$2.20 PF:1.74 ❌ (96% bloqueadas)
EMA200_ACTIVO = False

# ── MULTI-TIMEFRAME ───────────────────────────────────
MTF_ACTIVO    = True
MTF_INTERVALO = "15m"

# ── TRAILING STOP ────────────────────────────────────
TRAILING_ACTIVO    = True
TRAILING_ACTIVAR   = 1.5
TRAILING_DISTANCIA = 1.0

# ── PARTIAL TP (NUEVO v7) ─────────────────────────────
# TP1 = 1.5×ATR → cerrar 50%, mover SL a breakeven
# TP2 = 3.0×ATR → cerrar el 50% restante (con trailing)
# Beneficio: más wins "garantizados" + ride completo en movimientos grandes
PARTIAL_TP_ACTIVO   = True
PARTIAL_TP1_MULT    = 1.5   # × ATR para TP1 (50%)
PARTIAL_TP2_MULT    = 3.0   # × ATR para TP2 (50% restante)

# ── TIME-BASED EXIT (NUEVO v7) ────────────────────────
# Si posición lleva X horas sin resolver → cerrar
# Libera capital para nuevas oportunidades
# En 15m timeframe: si no rebota en 8h = señal falló
TIME_EXIT_HORAS = 8

# ── CONFIRMACIÓN DE VOLUMEN ──────────────────────────
VOLUMEN_VELA_MULT = 1.2

# ── SCORE ─────────────────────────────────────────────
# v7 tiene scoring más granular con StochRSI+Divergencia
# Score máximo teórico ~115 pero limitado a 100
# Score 80 = señal de calidad alta en el nuevo sistema
SCORE_MIN = 80

# ── CALIDAD DE MERCADO ────────────────────────────────
VOLUMEN_MIN_USD = 500_000
SPREAD_MAX_PCT  = 1.5

# ── SL / TP ──────────────────────────────────────────
SL_ATR_MULT  = 1.5
TP_ATR_MULT  = 3.0   # TP2 (TP final)
RR_MINIMO    = 1.5

# ── RIESGO ────────────────────────────────────────────
LEVERAGE          = 7
RIESGO_MARGEN_PCT = 0.08    # 8% por trade
MAX_POSICIONES    = 3       # 3 × 8% = 24% capital en margen

# ── CIRCUIT BREAKER ───────────────────────────────────
CB_MAX_DAILY_LOSS_PCT   = 0.06
CB_MAX_CONSECUTIVE_LOSS = 3

# ── FILTRO DE SESIÓN ─────────────────────────────────
SESION_ACTIVO      = True
SESION_HORAS_MALAS = [2, 3, 4, 5]

# ── PARES BLOQUEADOS — backtest WR < 30% ─────────────
PARES_BLOQUEADOS = [
    "BTC-USDT",    # WR 20.8% PnL -$6.10
    "ETH-USDT",    # WR 23.1% PnL -$5.49
    "DOGE-USDT",   # WR 22.2% PnL -$6.12
    "ADA-USDT",    # WR 24.1% PnL -$6.48
    "HYPE-USDT",   # WR 20.8% PnL -$9.35
    "WIF-USDT",    # WR 25.0% PnL -$8.13
    "BNB-USDT",    # WR 29.6% PnL -$3.66
    "XRP-USDT",    # WR 27.6% PnL -$2.06
    "RUNE-USDT",   # WR 31.2% PnL -$4.51
    "SEI-USDT",    # WR 29.0% PnL -$3.65
    "JUP-USDT",    # WR 29.6% PnL -$4.15
    "SUI-USDT",    # WR 32.0% PnL -$1.71
    "ATOM-USDT",   # WR 27.8% PnL -$2.11
]

# ── PARES PRIORITARIOS — backtest WR > 36% ───────────
PARES_PRIORITARIOS = [
    "BERA-USDT",   # WR 51.7% PnL +$15.32 ⭐
    "PI-USDT",     # WR 56.2% PnL  +$8.56 ⭐
    "OP-USDT",     # WR 46.2% PnL  +$7.97 ⭐
    "NEAR-USDT",   # WR 44.0% PnL  +$7.86 ⭐
    "ARB-USDT",    # WR 39.4% PnL  +$7.84 ⭐
    "GRASS-USDT",  # WR 39.1% PnL  +$9.62 ⭐
    "KAITO-USDT",  # WR 39.1% PnL  +$5.14 ⭐
    "MYX-USDT",    # WR 37.5% PnL  +$5.87 ⭐
    "LINK-USDT",   # WR 44.8% PnL  +$5.38 ⭐
    "ONDO-USDT",   # WR 38.5% PnL  +$2.62
    "POPCAT-USDT", # WR 37.0% PnL  +$2.36
    "INJ-USDT",    # WR 37.0% PnL  +$0.46
    "AVAX-USDT",   # WR 36.7% PnL  +$1.01
    "LTC-USDT",    # WR 43.5% PnL  +$1.80
]

# ── OPERACIÓN ─────────────────────────────────────────
LOOP_SECONDS = 600

VERSION = "BingX-RSI+BB-v7.0"
