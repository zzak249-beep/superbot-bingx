# ══════════════════════════════════════════════════════
# config.py — BingX RSI+BB Bot v6.0
# Perfil: $50-150 | Más pares | Menos pérdidas | CB 20%
# ══════════════════════════════════════════════════════
import os

# ── CREDENCIALES ─────────────────────────────────────
BINGX_API_KEY    = os.environ.get("BINGX_API_KEY",    "")
BINGX_SECRET_KEY = os.environ.get("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── MODO ─────────────────────────────────────────────
MODO_DEMO       = False
MODO_DEBUG      = False
BALANCE_INICIAL = 100.0

# ── INDICADORES ──────────────────────────────────────
RSI_PERIODO    = 14
RSI_OVERSOLD   = 36    # LONG cuando RSI < 36 (era 35, +1 para más señales)
RSI_OVERBOUGHT = 64    # SHORT cuando RSI > 64 (era 65, -1 para más señales)

BB_PERIODO = 20
BB_STD     = 2.0

ATR_PERIODO = 14

# ── FILTRO DE SCORE ───────────────────────────────────
# 72 = buen equilibrio entre cantidad y calidad de señales
# (75 era demasiado restrictivo para balance pequeño)
SCORE_MIN = 72

# ── CALIDAD DE MERCADO ────────────────────────────────
VOLUMEN_MIN_USD = 400_000   # era 500k — accede a más pares sin riesgo de illiquidez
SPREAD_MAX_PCT  = 1.8       # era 1.5 — algo más permisivo

# ── SL / TP ──────────────────────────────────────────
# Clave para "menos pérdidas": SL ajustado + RR mínimo alto
# SL 1.3×ATR → pérdida por trade contenida
# TP 3.5×ATR → con RR 2.7 necesitamos ganar solo 1 de cada 3 para ser rentables
SL_ATR_MULT = 1.3    # era 1.5 → SL más ajustado, menor pérdida por trade
TP_ATR_MULT = 3.5    # era 3.0 → TP más ambicioso
RR_MINIMO   = 2.2    # era 1.5 → solo entrar con R:R >= 2.2 (filtro fuerte)

# ── RIESGO ────────────────────────────────────────────
RIESGO_POR_TRADE = 0.025   # 2.5% del balance
LEVERAGE         = 15       # igual que BingX usa en práctica
MAX_POSICIONES   = 6        # era 3 → hasta 6 simultáneas (más diversificación)

# ── MARGEN POR TRADE ──────────────────────────────────
# Con balance $100 y 6 posiciones: $8 × 6 = $48 comprometido máximo (48%)
# Con balance $50: máximo 3 posiciones abiertas de forma natural
MARGEN_POR_TRADE = 8.0

# ── CIRCUIT BREAKER ───────────────────────────────────
CB_MAX_DAILY_LOSS_PCT   = 0.20   # 20% — tu tolerancia declarada
CB_MAX_CONSECUTIVE_LOSS = 5      # pausar tras 5 pérdidas seguidas (era 4)

# ── TRAILING STOP ─────────────────────────────────────
# Activo: protege ganancias sin cortar el trade antes de tiempo
TRAILING_STOP_ACTIVO        = True
TRAILING_STOP_ACTIVAR_PCT   = 0.55  # Activa al 55% del camino hacia el TP
TRAILING_STOP_DISTANCIA_ATR = 0.9   # SL sigue a 0.9×ATR del máximo

# ── CIERRE PARCIAL ────────────────────────────────────
# Asegura ganancia parcial: importante con balance pequeño
CIERRE_PARCIAL_ACTIVO  = True
CIERRE_PARCIAL_PCT_TP  = 0.65   # Cierra 50% al llegar al 65% del TP
CIERRE_PARCIAL_QTY_PCT = 0.50   # Cierra este % de la posición

# ── OPERACIÓN ─────────────────────────────────────────
LOOP_SECONDS = 300   # escaneo cada 5 min (era 10)

# ── MULTI-TIMEFRAME ───────────────────────────────────
# Confirmación en 15m: clave para reducir falsas señales
MTF_CONFIRMACION_ACTIVO = True
MTF_INTERVALO           = "15m"

# ── VOLATILIDAD MÍNIMA ────────────────────────────────
# No operar en mercados planos (reduce whipsaws)
ATR_MIN_PCT_PRECIO = 0.003   # ATR >= 0.3% del precio

# ── FILTRO HORA ───────────────────────────────────────
# Evitar las horas de menor liquidez cripto (UTC)
# Las mejores horas son 8-12h y 14-22h UTC
FILTRO_HORA_ACTIVO = True
HORAS_PERMITIDAS   = list(range(7, 23))   # 7:00 a 22:59 UTC

VERSION = "BingX-RSI+BB-v6.0"
