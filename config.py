# ══════════════════════════════════════════════════════
# config.py — BingX RSI+BB Bot v5.2
# Optimizado para balance pequeño ($30-$100)
# Margen dinámico — escala con el balance automáticamente
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
TP_ATR_MULT = 3.0
RR_MINIMO   = 1.5

# ── PARTIAL TP ────────────────────────────────────────
# TP1 = 1.5×ATR → cerrar 50% + SL a breakeven
# TP2 = 3.0×ATR → cerrar el 50% restante
PARTIAL_TP_ACTIVO = True
PARTIAL_TP1_MULT  = 1.5

# ── TRAILING STOP ────────────────────────────────────
TRAILING_ACTIVO    = True
TRAILING_ACTIVAR   = 1.5   # activar al ganar 1.5×ATR
TRAILING_DISTANCIA = 1.0   # trailing a 1.0×ATR del precio

# ── TIME-BASED EXIT ──────────────────────────────────
TIME_EXIT_HORAS = 8

# ── SCORE ────────────────────────────────────────────
# 80 era demasiado alto → 0 señales en mercado bajista
# 70 permite más entradas manteniendo calidad
SCORE_MIN = int(os.environ.get("SCORE_MIN", 70))

# ── FILTROS DE CALIDAD ────────────────────────────────
VOLUMEN_MIN_USD = 300_000   # bajado de 500k — más pares pasan
SPREAD_MAX_PCT  = 1.5

# ── RIESGO ────────────────────────────────────────────
LEVERAGE       = int(os.environ.get("LEVERAGE", 7))
# Con $34 y MAX_POSICIONES=3, ponía $24 en riesgo (70% del balance)
# Reducido a 2 → máximo $12-14 en riesgo (~36%)
MAX_POSICIONES = int(os.environ.get("MAX_POSICIONES", 2))

# ── MARGEN DINÁMICO ───────────────────────────────────
# En lugar de $8 fijo, usa un % del balance disponible
# Escala automáticamente:
#   $34 → $6.10/trade   $50 → $9/trade   $80+ → $12/trade (cap)
# Esto protege el capital pequeño y crece con la cuenta
MARGEN_PCT  = 0.18   # 18% del balance por trade
MARGEN_MIN  = 5.0    # mínimo absoluto ($5 = mínimo BingX)
MARGEN_MAX  = 12.0   # máximo absoluto (no arriesgar más de $12)

# ── CIRCUIT BREAKER ──────────────────────────────────
CB_MAX_DAILY_LOSS_PCT   = 0.08   # 8% diario → pausar (más permisivo)
CB_MAX_CONSECUTIVE_LOSS = 3

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

# ── OPERACIÓN ────────────────────────────────────────
LOOP_SECONDS = 600

VERSION = "BingX-RSI+BB-v5.2"
