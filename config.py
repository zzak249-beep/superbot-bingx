"""
config.py — APEX Bot v7.0 [INSTITUCIONAL]
==========================================
Parámetros optimizados para operar como dinero institucional.
Objetivo: WR >= 45% con R:R >= 2.0 = rentable consistentemente.
"""
import os

VERSION = "APEX-Bot v7.0 [Institucional]"


def _int(v, d):
    try: return int(os.getenv(v, str(d)).split()[0].split("(")[0].strip())
    except: return d

def _float(v, d):
    try: return float(os.getenv(v, str(d)).split()[0].split("(")[0].strip())
    except: return d

def _bool(v, d):
    raw = os.getenv(v, "true" if d else "false")
    return raw.strip().lower().split()[0] in ("true", "1", "yes")


# ── API Keys ──────────────────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY",    "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "") or os.getenv("BINGX_API_SECRET", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Control ───────────────────────────────────────────────────
MODO_DEMO    = _bool("MODO_DEMO",    False)
LOOP_SECONDS = _int("LOOP_SECONDS",  60)
BINGX_MODE   = os.getenv("BINGX_MODE", "auto").strip().lower()

# ── MetaClaw ──────────────────────────────────────────────────
METACLAW_ACTIVO        = _bool("METACLAW_ACTIVO",        True)
METACLAW_VETO_MINIMO   = _int("METACLAW_VETO_MINIMO",    5)
METACLAW_CONFIANZA_MIN = _int("METACLAW_CONFIANZA_MIN",  4)

# ── Capital ───────────────────────────────────────────────────
LEVERAGE           = _int("LEVERAGE",           10)
TRADE_USDT_BASE    = _float("TRADE_USDT_BASE",  10.0)
TRADE_USDT_MAX     = _float("TRADE_USDT_MAX",   50.0)
MAX_POSICIONES     = _int("MAX_POSICIONES",       3)
COMPOUND_STEP_USDT = _float("COMPOUND_STEP_USDT", 50.0)
COMPOUND_ADD_USDT  = _float("COMPOUND_ADD_USDT",   1.0)

# ── SL / TP ───────────────────────────────────────────────────
TP_ATR_MULT       = _float("TP_ATR_MULT",      2.0)
SL_ATR_MULT       = _float("SL_ATR_MULT",      1.2)
PARTIAL_TP1_MULT  = _float("PARTIAL_TP1_MULT", 0.9)
PARTIAL_TP_ACTIVO = _bool("PARTIAL_TP_ACTIVO",  True)
MIN_RR            = _float("MIN_RR",            2.0)

# ── Trailing ──────────────────────────────────────────────────
TRAILING_ACTIVO    = _bool("TRAILING_ACTIVO",    True)
TRAILING_ACTIVAR   = _float("TRAILING_ACTIVAR",  1.2)
TRAILING_DISTANCIA = _float("TRAILING_DISTANCIA", 1.0)

# ── Límites ───────────────────────────────────────────────────
TIME_EXIT_HORAS = _float("TIME_EXIT_HORAS", 12.0)
MAX_PERDIDA_DIA = _float("MAX_PERDIDA_DIA", 20.0)

# ── Scoring ───────────────────────────────────────────────────
SCORE_MIN    = _int("SCORE_MIN",       9)   # v7 sube a 9 — más calidad
FVG_MIN_PIPS = _float("FVG_MIN_PIPS",  0.0)
EQ_LOOKBACK  = _int("EQ_LOOKBACK",    50)
EQ_THRESHOLD = _float("EQ_THRESHOLD",  0.1)
EQ_PIVOT_LEN = _int("EQ_PIVOT_LEN",    3)   # pivotes más sensibles

# ── Indicadores ───────────────────────────────────────────────
EMA_FAST       = _int("EMA_FAST",      21)
EMA_SLOW       = _int("EMA_SLOW",      50)
EMA_LOCAL_FAST = _int("EMA_LOCAL_FAST",  9)
EMA_LOCAL_SLOW = _int("EMA_LOCAL_SLOW", 21)
RSI_PERIOD     = _int("RSI_PERIOD",    14)
RSI_BUY_MAX    = _float("RSI_BUY_MAX",  60.0)
RSI_SELL_MIN   = _float("RSI_SELL_MIN", 40.0)
ATR_PERIOD     = _int("ATR_PERIOD",    14)
ATR_FAST       = _int("ATR_FAST",       7)
PIVOT_NEAR_PCT = _float("PIVOT_NEAR_PCT", 1.5)

# ── Patrones ──────────────────────────────────────────────────
PINBAR_RATIO    = _float("PINBAR_RATIO",   0.50)
ENGULF_ACTIVO   = _bool("ENGULF_ACTIVO",   True)
VWAP_ACTIVO     = _bool("VWAP_ACTIVO",     True)
VWAP_PCT        = _float("VWAP_PCT",       0.30)
COOLDOWN_VELAS  = _int("COOLDOWN_VELAS",   10)
MOMENTUM_ACTIVO = _bool("MOMENTUM_ACTIVO", True)

# ── SMC ───────────────────────────────────────────────────────
PREMIUM_DISCOUNT_ACTIVO = _bool("PREMIUM_DISCOUNT_ACTIVO", True)
PREMIUM_DISCOUNT_LB     = _int("PREMIUM_DISCOUNT_LB",      50)
DISPLACEMENT_ACTIVO     = _bool("DISPLACEMENT_ACTIVO",     True)
IDM_ACTIVO              = _bool("IDM_ACTIVO",               True)
MTF_4H_ACTIVO           = _bool("MTF_4H_ACTIVO",            True)

# ── Timeframes ────────────────────────────────────────────────
TIMEFRAME     = os.getenv("TIMEFRAME",     "5m").strip()
CANDLES_LIMIT = _int("CANDLES_LIMIT",     200)
MTF_ACTIVO    = _bool("MTF_ACTIVO",        True)
MTF_TIMEFRAME = os.getenv("MTF_TIMEFRAME", "1h").strip()
MTF_CANDLES   = _int("MTF_CANDLES",        60)

# ── SMC Básico ────────────────────────────────────────────────
OB_ACTIVO         = _bool("OB_ACTIVO",         True)
OB_LOOKBACK       = _int("OB_LOOKBACK",         30)
BOS_ACTIVO        = _bool("BOS_ACTIVO",         True)
ASIA_RANGE_ACTIVO = _bool("ASIA_RANGE_ACTIVO",  True)
VELA_CONFIRMACION = _bool("VELA_CONFIRMACION",  True)
CORRELACION_ACTIVO= _bool("CORRELACION_ACTIVO", True)
MACD_ACTIVO       = _bool("MACD_ACTIVO",        True)
SWEEP_ACTIVO      = _bool("SWEEP_ACTIVO",       True)
SWEEP_LOOKBACK    = _int("SWEEP_LOOKBACK",      20)

# ── Volumen ───────────────────────────────────────────────────
VOLUMEN_MIN_24H     = _float("VOLUMEN_MIN_24H",    5_000_000.0)
VOLUMEN_MIN_LOW_VOL = _float("VOLUMEN_MIN_LOW_VOL",  500_000.0)
SCORE_MIN_LOW_VOL   = _int("SCORE_MIN_LOW_VOL",         10)
LOW_VOL_ACTIVO      = _bool("LOW_VOL_ACTIVO",          False)

# ── Scanner ───────────────────────────────────────────────────
MAX_PARES_SCAN   = _int("MAX_PARES_SCAN",    0)
ANALISIS_WORKERS = _int("ANALISIS_WORKERS",  6)
SOLO_LONG        = _bool("SOLO_LONG",        False)

# ── Range ─────────────────────────────────────────────────────
RANGE_ACTIVO    = _bool("RANGE_ACTIVO",    False)
RANGE_ADX_MAX   = _float("RANGE_ADX_MAX", 22.0)
RANGE_SCORE_MIN = _int("RANGE_SCORE_MIN",   7)

# ── Killzones UTC ─────────────────────────────────────────────
KZ_ASIA_START   = _int("KZ_ASIA_START",    0)
KZ_ASIA_END     = _int("KZ_ASIA_END",    240)
KZ_LONDON_START = _int("KZ_LONDON_START",420)
KZ_LONDON_END   = _int("KZ_LONDON_END",  600)
KZ_NY_START     = _int("KZ_NY_START",    780)
KZ_NY_END       = _int("KZ_NY_END",      960)
KZ_REQUERIDA    = _bool("KZ_REQUERIDA",  False)

# ── Pares ─────────────────────────────────────────────────────
_bloq_default = (
    "RESOLV-USDT,KAVA-USDT,AXS-USDT,GRASS-USDT,NTRN-USDT,AWE-USDT,"
    "DUSK-USDT,ME-USDT,2Z-USDT,BROCCOLIF3B-USDT,PAXG-USDT,XAUT-USDT,"
    "APT-USDT,AVAX-USDT,XRP-USDT,DOGE-USDT"
)
PARES_BLOQUEADOS   = [p.strip() for p in os.getenv("PARES_BLOQUEADOS", _bloq_default).split(",") if p.strip()]
PARES_PRIORITARIOS = [p.strip() for p in os.getenv("PARES_PRIORITARIOS", "BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT").split(",") if p.strip()]

MEMORY_DIR = os.getenv("MEMORY_DIR", "")


def validar():
    e = []
    if not MODO_DEMO:
        if not BINGX_API_KEY:    e.append("BINGX_API_KEY vacía")
        if not BINGX_SECRET_KEY: e.append("BINGX_SECRET_KEY vacía")
    if not TELEGRAM_TOKEN:       e.append("TELEGRAM_TOKEN vacía")
    if not ANTHROPIC_API_KEY and METACLAW_ACTIVO:
        e.append("ANTHROPIC_API_KEY vacía — MetaClaw inactivo")
    if not (1 <= LEVERAGE <= 125): e.append(f"LEVERAGE={LEVERAGE} fuera de rango")
    if TRADE_USDT_BASE < 1:        e.append(f"TRADE_USDT_BASE={TRADE_USDT_BASE} muy bajo")
    if not (1 <= SCORE_MIN <= 20): e.append(f"SCORE_MIN={SCORE_MIN} fuera de rango 1-20")
    if MIN_RR < 1.0:               e.append(f"MIN_RR={MIN_RR} peligroso")
    return e
