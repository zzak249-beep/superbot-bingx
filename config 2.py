# ══════════════════════════════════════════════════════
# config.py — BingX RSI+BB Bot v5.4
# $100 USDT | Leverage 10x | TP optimizado
# TP1:1.5× asegura | TP2:4× deja correr | Trail:1.5×
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

# ── SL / TP — confirmados por backtest PF:2.0 ────────
SL_ATR_MULT = 1.5
TP_ATR_MULT = 4.0
RR_MINIMO   = 1.5

# ── PARTIAL TP ────────────────────────────────────────
PARTIAL_TP_ACTIVO = True
PARTIAL_TP1_MULT  = 1.5   # TP1 = 1.5×ATR → cerrar 50% + SL a breakeven

# ── TRAILING STOP ─────────────────────────────────────
TRAILING_ACTIVO    = True
TRAILING_ACTIVAR   = 1.5
TRAILING_DISTANCIA = 1.5

# ── TIME-BASED EXIT ───────────────────────────────────
TIME_EXIT_HORAS = 12

# ── SCORE ─────────────────────────────────────────────
# 65 = más señales manteniendo calidad mínima
# Sube a 70+ si hay demasiadas pérdidas seguidas
SCORE_MIN = int(os.environ.get("SCORE_MIN", 65))

# ── FILTROS ───────────────────────────────────────────
VOLUMEN_MIN_USD = 300_000
SPREAD_MAX_PCT  = 1.5

# ══════════════════════════════════════════════════════
# RIESGO AGRESIVO — $100 USDT
# ══════════════════════════════════════════════════════

# Leverage 10x — doble que antes (7x)
# BingX permite hasta 125x, 10x es agresivo pero controlado
LEVERAGE = int(os.environ.get("LEVERAGE", 10))

# 3 posiciones simultáneas máximo
# $20 × 3 = $60 comprometido = 60% del balance
MAX_POSICIONES = int(os.environ.get("MAX_POSICIONES", 3))

# Margen dinámico — 20% del balance por trade
# $100 → $20/trade  |  $150 → $25/trade  |  $200+ → $25/trade (cap)
# Con 10x leverage: $20 margen = $200 notional
MARGEN_PCT  = 0.20   # 20% del balance por trade
MARGEN_MIN  = 5.0    # mínimo $5
MARGEN_MAX  = 25.0   # máximo $25 por trade

# ── CIRCUIT BREAKER ───────────────────────────────────
# Relajado para modo agresivo — no pausar demasiado rápido
CB_MAX_DAILY_LOSS_PCT   = 0.12   # 12% pérdida diaria → pausar
CB_MAX_CONSECUTIVE_LOSS = 5      # 5 pérdidas seguidas → pausar
MAX_DAILY_LOSS_PCT      = CB_MAX_DAILY_LOSS_PCT
MAX_DRAWDOWN_PCT        = 0.25   # 25% drawdown máximo (agresivo)
CIRCUIT_BREAKER_LOSS    = CB_MAX_CONSECUTIVE_LOSS

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

# ── LEARNER (requerido por memoria.py) ───────────────
LEARNER_PERSISTIR      = False
LEARNER_CICLO_H        = 4
LEARNER_MIN_TRADES     = 8
LEARNER_MIN_WR         = 38.0
LEARNER_MIN_PF         = 0.9
LEARNER_PENALIZACION_H = 12

# ── PARES — importar lista completa (184 pares) ───────
try:
    from config_pares import PARES, PARES_PRIORITARIOS, PARES_BLOQUEADOS
except ImportError:
    PARES = PARES_PRIORITARIOS

# ── OPERACIÓN ────────────────────────────────────────
LOOP_SECONDS = 600

VERSION = "BingX-RSI+BB-v5.4"
