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
MODE = _str("MODE", "SIGNAL").upper()   # FIX: .upper() — "live" → "LIVE"

# ── Capital y riesgo ──────────────────────────────────────────────────────────
CAPITAL           = _float("CAPITAL", 400.0)
LEVERAGE          = _int("LEVERAGE", 5)
RISK_PCT          = _float("RISK_PCT", 1.0)
MAX_NOTIONAL_USDT = _float("MAX_NOTIONAL_USDT", 40.0)
MIN_NOTIONAL_USDT = _float("MIN_NOTIONAL_USDT", 10.0)

# ── Posiciones ────────────────────────────────────────────────────────────────
MAX_OPEN_TRADES  = _int("MAX_OPEN_TRADES", 3)
MAX_DAILY_TRADES = _int("MAX_DAILY_TRADES", 10)

# ── Scanner ───────────────────────────────────────────────────────────────────
MIN_VOLUME_USDT      = _float("MIN_VOLUME_USDT", 500_000.0)  # FIX: faltaba esta línea
CASCADE_UNIVERSE     = _int("CASCADE_UNIVERSE", 100)
CASCADE_SCAN_INTERVAL = _int("CASCADE_SCAN_INTERVAL", 60)

# ── Parámetros de cascade ─────────────────────────────────────────────────────
CASCADE_MIN_SCORE = _float("CASCADE_MIN_SCORE", 60.0)
CASCADE_MIN_RR    = _float("CASCADE_MIN_RR", 1.5)
CASCADE_SL_ATR    = _float("CASCADE_SL_ATR", 1.5)

# ── Risk management ───────────────────────────────────────────────────────────
DAILY_LOSS_PCT      = _float("DAILY_LOSS_PCT", 5.0)
FIXED_NOTIONAL_USDT = _float("FIXED_NOTIONAL_USDT", 0.0)

# ── Trailing stop ─────────────────────────────────────────────────────────────
BREAKEVEN_ATR_MULT = _float("BREAKEVEN_ATR_MULT", 2.0)
TRAIL_DISTANCE_ATR = _float("TRAIL_DISTANCE_ATR", 1.5)
MAX_HOLD_MINUTES   = _int("MAX_HOLD_MINUTES", 480)

# ── EMA exit ──────────────────────────────────────────────────────────────────
EMA_EXIT_ENABLED = _bool("EMA_EXIT_ENABLED", True)
EMA_EXIT_PERIOD  = _int("EMA_EXIT_PERIOD", 9)

# ── Timeframes ────────────────────────────────────────────────────────────────
TIMEFRAME      = _str("TIMEFRAME", "1h")
HTF_TIMEFRAME  = _str("HTF_TIMEFRAME", "4h")
HTF2_TIMEFRAME = _str("HTF2_TIMEFRAME", "1d")
HTF5_TIMEFRAME = _str("HTF5_TIMEFRAME", "1w")

# ── Blacklist ─────────────────────────────────────────────────────────────────
_BL_RAW = _str("BLACKLIST", "ESPORTS,STABLEUSDT,EURUSD,SILVER,SILVERXAG,OILWTI,OILBRENT,PAXG,CUSDT,SYN,GOLD,GASOLINE")
BLACKLIST = set(s.strip().upper() for s in _BL_RAW.split(",") if s.strip())

# ── Puerto Railway ────────────────────────────────────────────────────────────
PORT = _int("PORT", 8080)

# ── Tier / SL ────────────────────────────────────────────────────────────────
MIN_TIER    = _str("MIN_TIER", "STD")
SL_ATR_MULT = _float("SL_ATR_MULT", CASCADE_SL_ATR)

# ── Kelly sizing ──────────────────────────────────────────────────────────────
KELLY_FRACTION = _float("KELLY_FRACTION", 0.25)
KELLY_MAX_PCT  = _float("KELLY_MAX_PCT", 0.05)

# ── Reconciliación ────────────────────────────────────────────────────────────
RECONCILE_ON_STARTUP    = _bool("RECONCILE_ON_STARTUP", False)
POSITION_CHECK_INTERVAL = _int("POSITION_CHECK_INTERVAL", 30)

# ── Momentum exit ────────────────────────────────────────────────────────────
MOMENTUM_EXIT_ENABLED = _bool("MOMENTUM_EXIT_ENABLED", False)

# ── Complement (no aplica) ────────────────────────────────────────────────────
COMPLEMENT_MODE = "DISABLED"
MASTER_URL      = ""

# ── Diagnóstico ───────────────────────────────────────────────────────────────
SCAN_INTERVAL = CASCADE_SCAN_INTERVAL
