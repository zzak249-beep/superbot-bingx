"""
Cascade Bot — config.py
════════════════════════════════════════════════════════════════
RENOMBRAR ESTE ARCHIVO A config.py EN EL REPO DEL CASCADE BOT.

Variables marcadas con ← son las más importantes de ajustar.
Las demás se pueden dejar con el valor por defecto inicial.
════════════════════════════════════════════════════════════════
"""
import os

def _float(key, default):
    try:
        v = os.getenv(key, "")
        return float(v.split("#")[0].strip()) if v else default
    except Exception:
        return default

def _int(key, default):
    try:
        v = os.getenv(key, "")
        return int(float(v.split("#")[0].strip())) if v else default
    except Exception:
        return default

def _bool(key, default):
    v = os.getenv(key, "").strip().lower().split("#")[0].strip()
    if not v:
        return default
    return v in ("true", "1", "yes")

def _str(key, default):
    v = os.getenv(key, "").strip()
    return v if v else default


# ── BingX API (cuenta de renewed-love / independiente) ───────────────────────
BINGX_API_KEY    = _str("BINGX_API_KEY", "")
BINGX_SECRET_KEY = _str("BINGX_SECRET_KEY", "")
BINGX_BASE_URL   = "https://open-api.bingx.com"

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = _str("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = _str("TELEGRAM_CHAT_ID", "")

# ── Modo ──────────────────────────────────────────────────────────────────────
MODE = _str("MODE", "SIGNAL")   # ← SIGNAL para testear, LIVE cuando tengas confianza

# ── Capital y riesgo ──────────────────────────────────────────────────────────
CAPITAL          = _float("CAPITAL", 400.0)    # ← Balance real de la cuenta
LEVERAGE         = _int("LEVERAGE", 5)         # 5X — conservador para cascades
RISK_PCT         = _float("RISK_PCT", 1.0)     # ← 1% de riesgo por trade (máx 4 USDT)
MAX_NOTIONAL_USDT = _float("MAX_NOTIONAL_USDT", 40.0)  # máx 40 USDT por posición
MIN_NOTIONAL_USDT = _float("MIN_NOTIONAL_USDT", 10.0)  # mín 10 USDT

# ── Posiciones ────────────────────────────────────────────────────────────────
MAX_OPEN_TRADES  = _int("MAX_OPEN_TRADES", 3)     # ← Máx 3 cascades simultáneas
MAX_DAILY_TRADES = _int("MAX_DAILY_TRADES", 10)   # Máx 10 por día

# ── Parámetros de cascade ─────────────────────────────────────────────────────
CASCADE_MIN_SCORE    = _float("CASCADE_MIN_SCORE", 60.0)   # ← Score mínimo OI+FR
CASCADE_MIN_RR       = _float("CASCADE_MIN_RR", 1.5)       # ← RR mínimo requerido
CASCADE_SL_ATR       = _float("CASCADE_SL_ATR", 1.5)       # ATR buffer para SL
CASCADE_SCAN_INTERVAL = _int("CASCADE_SCAN_INTERVAL", 60)  # 60s entre iteraciones
CASCADE_UNIVERSE     = _int("CASCADE_UNIVERSE", 100)       # Nº símbolos a escanear

# ── Risk management ───────────────────────────────────────────────────────────
DAILY_LOSS_PCT   = _float("DAILY_LOSS_PCT", 5.0)   # ← Parar día si pierde >5%
FIXED_NOTIONAL_USDT = _float("FIXED_NOTIONAL_USDT", 0.0)

# ── Trailing stop ─────────────────────────────────────────────────────────────
BREAKEVEN_ATR_MULT  = _float("BREAKEVEN_ATR_MULT", 2.0)  # Activar trail a 2×ATR
TRAIL_DISTANCE_ATR  = _float("TRAIL_DISTANCE_ATR", 1.5)  # Distancia del trail
MAX_HOLD_MINUTES    = _int("MAX_HOLD_MINUTES", 480)       # 8H máx (cascade se resuelve)

# ── EMA exit ──────────────────────────────────────────────────────────────────
EMA_EXIT_ENABLED = _bool("EMA_EXIT_ENABLED", True)   # Salida por EMA9 activa
EMA_EXIT_PERIOD  = _int("EMA_EXIT_PERIOD", 9)

# ── Timeframes (cascade usa 1H como primario) ─────────────────────────────────
TIMEFRAME     = _str("TIMEFRAME", "1h")     # ← 1H para cascade (no 3m)
HTF_TIMEFRAME = _str("HTF_TIMEFRAME", "4h")
HTF2_TIMEFRAME = _str("HTF2_TIMEFRAME", "1d")
HTF5_TIMEFRAME = _str("HTF5_TIMEFRAME", "1w")

# ── Blacklist ──────────────────────────────────────────────────────────────────
# Excluir tokens sin volumen real y non-crypto que no tienen OI real
_BL_RAW = _str("BLACKLIST", "ESPORTS,STABLEUSDT,EURUSD,SILVER,SILVERXAG,OILWTI,OILBRENT,PAXG,CUSDT,SYN,GOLD,GASOLINE")
BLACKLIST = set(s.strip().upper() for s in _BL_RAW.split(",") if s.strip())

# ── Puerto Railway ────────────────────────────────────────────────────────────
PORT = _int("PORT", 8080)

# ── Tier (para RiskManager) ───────────────────────────────────────────────────
MIN_TIER  = _str("MIN_TIER", "STD")
SL_ATR_MULT = _float("SL_ATR_MULT", CASCADE_SL_ATR)

# ── Kelly sizing ──────────────────────────────────────────────────────────────
KELLY_FRACTION  = _float("KELLY_FRACTION", 0.25)
KELLY_MAX_PCT   = _float("KELLY_MAX_PCT", 0.05)

# ── Reconciliación ────────────────────────────────────────────────────────────
RECONCILE_ON_STARTUP = _bool("RECONCILE_ON_STARTUP", False)
POSITION_CHECK_INTERVAL = _int("POSITION_CHECK_INTERVAL", 30)

# ── Momentum exit (desactivado por defecto en cascade) ───────────────────────
MOMENTUM_EXIT_ENABLED = _bool("MOMENTUM_EXIT_ENABLED", False)

# ── Complement (no aplica a este bot) ────────────────────────────────────────
COMPLEMENT_MODE = "DISABLED"
MASTER_URL = ""

# ── Diagnóstico ───────────────────────────────────────────────────────────────
SCAN_INTERVAL = CASCADE_SCAN_INTERVAL
