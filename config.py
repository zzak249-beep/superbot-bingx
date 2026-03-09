# ══════════════════════════════════════════════════════
# config.py — BingX RSI+BB Bot v5.1 (Opción A — estable)
# Basado en backtest real 30 pares × 15 días
# ══════════════════════════════════════════════════════
import os

# ── CREDENCIALES ─────────────────────────────────────
BINGX_API_KEY    = os.environ.get("BINGX_API_KEY",    "")
BINGX_SECRET_KEY = os.environ.get("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── MODO ─────────────────────────────────────────────
MODO_DEMO  = False
MODO_DEBUG = False

# ── INDICADORES ──────────────────────────────────────
RSI_PERIODO    = 14
RSI_OVERSOLD   = 35
RSI_OVERBOUGHT = 65
BB_PERIODO     = 20
BB_STD         = 2.0
ATR_PERIODO    = 14

# ── SL / TP ──────────────────────────────────────────
# Backtest real: SL=1.5, TP=3.0 → PF:2.0 rentable
# No cambiar sin nuevo backtest
SL_ATR_MULT = 1.5
TP_ATR_MULT = 3.0
RR_MINIMO   = 1.5

# ── PARTIAL TP ───────────────────────────────────────
# TP1 = 1.5×ATR → cerrar 50% + SL a breakeven
# TP2 = 3.0×ATR → cerrar el 50% restante
PARTIAL_TP_ACTIVO = True
PARTIAL_TP1_MULT  = 1.5   # × ATR

# ── TRAILING STOP ────────────────────────────────────
TRAILING_ACTIVO    = True
TRAILING_ACTIVAR   = 1.5   # activar cuando gana 1.5×ATR
TRAILING_DISTANCIA = 1.0   # trailing a 1.0×ATR del precio

# ── TIME-BASED EXIT ───────────────────────────────────
# Si posición lleva > 8h sin resolver → cerrar
# Libera capital para nuevas señales
TIME_EXIT_HORAS = 8

# ── SCORE ─────────────────────────────────────────────
# Backtest: WR sube con score más alto
SCORE_MIN = 80

# ── FILTROS DE CALIDAD ────────────────────────────────
VOLUMEN_MIN_USD = 500_000
SPREAD_MAX_PCT  = 1.5

# ── RIESGO ────────────────────────────────────────────
LEVERAGE       = 7
MAX_POSICIONES = 3    # 3 × $8 margen = $24 máximo comprometido

# ── PARES BLOQUEADOS — backtest WR < 32% ─────────────
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
    "SOL-USDT",    # WR 32.3% PnL -$1.67
    "TIA-USDT",    # WR 33.3% PnL -$0.59
]

# ── PARES PRIORITARIOS — backtest WR > 36% ───────────
PARES_PRIORITARIOS = [
    "BERA-USDT",   # WR 51.7% PnL +$15.32 ⭐
    "PI-USDT",     # WR 56.2% PnL  +$8.56 ⭐
    "OP-USDT",     # WR 46.2% PnL  +$7.97 ⭐
    "NEAR-USDT",   # WR 44.0% PnL  +$7.86 ⭐
    "ARB-USDT",    # WR 39.4% PnL  +$7.84 ⭐
    "LINK-USDT",   # WR 44.8% PnL  +$5.38 ⭐
    "GRASS-USDT",  # WR 39.1% PnL  +$9.62
    "MYX-USDT",    # WR 37.5% PnL  +$5.87
    "KAITO-USDT",  # WR 39.1% PnL  +$5.14
    "ONDO-USDT",   # WR 38.5% PnL  +$2.62
    "LTC-USDT",    # WR 43.5% PnL  +$1.80
    "POPCAT-USDT", # WR 37.0% PnL  +$2.36
    "AVAX-USDT",   # WR 36.7% PnL  +$1.01
    "INJ-USDT",    # WR 37.0% PnL  +$0.46
]

# ── OPERACIÓN ─────────────────────────────────────────
LOOP_SECONDS = 600

VERSION = "BingX-RSI+BB-v5.1"
