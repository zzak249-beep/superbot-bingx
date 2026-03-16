"""
config_smc.py — SMC Sniper Bot [Liquidez + QML + Stop Clusters]
"""
import os

VERSION = os.getenv("VERSION", "SMC Sniper Bot v1.0")

# ─── API BingX ───────────────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY",    "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
MODO_DEMO        = os.getenv("MODO_DEMO", "false").lower() in ("true", "demo", "1")
MEMORY_DIR       = os.getenv("MEMORY_DIR", "/app/data")
BINGX_MODE       = os.getenv("BINGX_MODE", "hedge")

# ─── Riesgo ──────────────────────────────────────────────────
TRADE_USDT_BASE    = float(os.getenv("TRADE_USDT_BASE",    "10"))
TRADE_USDT_MAX     = float(os.getenv("TRADE_USDT_MAX",     "100"))
LEVERAGE           = int(os.getenv("LEVERAGE",             "10"))
COMPOUND_STEP_USDT = float(os.getenv("COMPOUND_STEP_USDT", "50"))
COMPOUND_ADD_USDT  = float(os.getenv("COMPOUND_ADD_USDT",  "5"))
MAX_PERDIDA_DIA    = float(os.getenv("MAX_PERDIDA_DIA",    "30.0"))
MAX_POSICIONES     = int(os.getenv("MAX_POSICIONES",       "3"))

# ─── Timeframes ──────────────────────────────────────────────
TIMEFRAME     = os.getenv("TIMEFRAME", "5m")
CANDLES_LIMIT = int(os.getenv("CANDLES_LIMIT", "200"))
HTF_H1        = "1h"
HTF_H4        = "4h"

# ─── Estrategia ──────────────────────────────────────────────
# Liquidity Magnet Zones [identityKa] — pivots
PIVOT_LEN     = int(os.getenv("PIVOT_LEN",   "15"))   # sensibilidad pivot

# QML FTB [Malibu] — Quasimodo structure
QML_ZIGZAG    = int(os.getenv("QML_ZIGZAG",  "13"))   # zigzag sensitivity
QML_MIN_ATR   = float(os.getenv("QML_MIN_ATR","1.5")) # estructura mínima en ATR

# Stop Loss Clustering [Kioseff] — stop clusters
CLUSTER_BARS  = int(os.getenv("CLUSTER_BARS", "20"))  # lookback stops

# Volumen
VOL_LOOKBACK  = int(os.getenv("VOL_LOOKBACK", "20"))
VOL_MULT      = float(os.getenv("VOL_MULT",   "1.3")) # RVOL mínimo

# ─── SL / TP ─────────────────────────────────────────────────
SL_ATR_MULT   = float(os.getenv("SL_ATR_MULT",   "1.5"))
TP_DIST_MULT  = float(os.getenv("TP_DIST_MULT",  "3.0"))
TP1_DIST_MULT = float(os.getenv("TP1_DIST_MULT", "1.5"))
MIN_RR        = float(os.getenv("MIN_RR",        "2.0"))

# ─── Gestión ─────────────────────────────────────────────────
TRAILING_ACTIVO    = os.getenv("TRAILING_ACTIVO",  "true").lower() == "true"
TRAILING_ACTIVAR   = float(os.getenv("TRAILING_ACTIVAR",   "1.5"))
TRAILING_DISTANCIA = float(os.getenv("TRAILING_DISTANCIA", "1.0"))
PARTIAL_TP_ACTIVO  = os.getenv("PARTIAL_TP_ACTIVO", "true").lower() == "true"
BE_ACTIVO          = os.getenv("BE_ACTIVO",         "true").lower() == "true"
TIME_EXIT_HORAS    = float(os.getenv("TIME_EXIT_HORAS", "8.0"))
COOLDOWN_VELAS     = int(os.getenv("COOLDOWN_VELAS",    "5"))

# ─── Pares ───────────────────────────────────────────────────
SOLO_LONG          = os.getenv("SOLO_LONG", "false").lower() == "true"
PARES_BLOQUEADOS   = [p.strip() for p in os.getenv("PARES_BLOQUEADOS","").split(",") if p.strip()]
PARES_PRIORITARIOS = ["BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT",
                      "AVAX-USDT","ARB-USDT","OP-USDT","DOGE-USDT","LINK-USDT"]
MAX_PARES_SCAN     = int(os.getenv("MAX_PARES_SCAN",   "30"))
VOLUMEN_MIN_24H    = float(os.getenv("VOLUMEN_MIN_24H","10000000"))
ANALISIS_WORKERS   = int(os.getenv("ANALISIS_WORKERS", "4"))
LOOP_SECONDS       = int(os.getenv("LOOP_SECONDS",     "90"))

# ─── Telegram ────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── Kill zones (minutos UTC) ────────────────────────────────
KZ_LONDON_START = int(os.getenv("KZ_LONDON_START", "480"))
KZ_LONDON_END   = int(os.getenv("KZ_LONDON_END",   "720"))
KZ_NY_START     = int(os.getenv("KZ_NY_START",     "780"))
KZ_NY_END       = int(os.getenv("KZ_NY_END",       "960"))


def validar() -> list:
    e = []
    if not BINGX_API_KEY:    e.append("BINGX_API_KEY no configurada")
    if not BINGX_SECRET_KEY: e.append("BINGX_SECRET_KEY no configurada")
    if TRADE_USDT_BASE <= 0: e.append("TRADE_USDT_BASE debe ser > 0")
    return e

# ─── Delta Strike 量能猎杀 [KodaTao] ─────────────────
DS_VOL_SHORT   = int(os.getenv("DS_VOL_SHORT",   "20"))
DS_VOL_MID     = int(os.getenv("DS_VOL_MID",     "60"))
DS_VOL_LONG    = int(os.getenv("DS_VOL_LONG",   "180"))
DS_RATIO_SHORT = float(os.getenv("DS_RATIO_SHORT","1.5"))
DS_RATIO_MID   = float(os.getenv("DS_RATIO_MID",  "3.0"))
DS_RATIO_LONG  = float(os.getenv("DS_RATIO_LONG", "5.0"))
DS_RSI_OB      = float(os.getenv("DS_RSI_OB",    "60"))
DS_RSI_OS      = float(os.getenv("DS_RSI_OS",    "40"))
DS_IMB_RATIO   = float(os.getenv("DS_IMB_RATIO",  "2.0"))
DS_N_CONFIRM   = int(os.getenv("DS_N_CONFIRM",    "5"))
