"""
config_smc.py — SMC Sniper Bot v2.0 [1M Fusion Strategy]
Liquidez HTF + Order Flow + Supertrend + EMA/RSI
"""
import os

VERSION = os.getenv("VERSION", "SMC Sniper Bot v2.0 [1M Fusion]")

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
TIMEFRAME     = os.getenv("TIMEFRAME",     "1m")   # 1M para entradas
TF_PURGA      = os.getenv("TF_PURGA",      "15m")  # TF donde se detecta purga
TF_H1         = "1h"
TF_H4         = "4h"
CANDLES_LIMIT = int(os.getenv("CANDLES_LIMIT", "200"))
CANDLES_HTF   = int(os.getenv("CANDLES_HTF",   "60"))  # candles para H1/H4

# ─── Estrategia: Liquidez + Purga ────────────────────────────
LIQ_LOOKBACK    = int(os.getenv("LIQ_LOOKBACK",    "50"))   # velas HTF para buscar max/min
LIQ_MARGEN_PCT  = float(os.getenv("LIQ_MARGEN_PCT","0.002")) # % tolerancia zona
LIQ_PURGA_MEM   = int(os.getenv("LIQ_PURGA_MEM",   "12"))   # velas 1M que dura memoria purga
LIQ_TOQUES_MIN  = int(os.getenv("LIQ_TOQUES_MIN",  "2"))    # toques mínimos en zona antes de señal
LIQ_TOQUES_WIN  = int(os.getenv("LIQ_TOQUES_WIN",  "30"))   # ventana para contar toques (velas 1M)
LIQ_USAR_H1     = os.getenv("LIQ_USAR_H1",  "true").lower()  == "true"
LIQ_USAR_H4     = os.getenv("LIQ_USAR_H4",  "true").lower()  == "true"
LIQ_USAR_D      = os.getenv("LIQ_USAR_D",   "false").lower() == "true"

# ─── Estrategia: Order Flow ───────────────────────────────────
OF_FLOW_LEN     = int(os.getenv("OF_FLOW_LEN",    "21"))   # batch length acumulación
OF_FLOW_RATIO   = float(os.getenv("OF_FLOW_RATIO", "1.8")) # ratio acumulación/distribución
OF_ICE_MUL      = float(os.getenv("OF_ICE_MUL",   "2.0")) # multiplicador iceberg
OF_ICE_MIN_VOL  = float(os.getenv("OF_ICE_MIN_VOL","200")) # vol mínimo absoluto iceberg
OF_SPOOF_PULL   = float(os.getenv("OF_SPOOF_PULL", "0.4")) # caída % vol para spoof

# ─── Estrategia: Confirmaciones ──────────────────────────────
EMA_RAPIDA      = int(os.getenv("EMA_RAPIDA",    "9"))
EMA_LENTA       = int(os.getenv("EMA_LENTA",     "21"))
EMA_MODO        = os.getenv("EMA_MODO", "ALINEACION")  # CRUCE | ALINEACION | CUALQUIERA
RSI_LEN         = int(os.getenv("RSI_LEN",       "14"))
RSI_OB          = float(os.getenv("RSI_OB",      "70"))
RSI_OS          = float(os.getenv("RSI_OS",      "30"))
ST_FACTOR       = float(os.getenv("ST_FACTOR",   "3.0"))
ST_PERIOD       = int(os.getenv("ST_PERIOD",     "10"))
VOL_LOOKBACK    = int(os.getenv("VOL_LOOKBACK",  "20"))
VOL_MULT        = float(os.getenv("VOL_MULT",    "1.2"))  # RVOL mínimo (1.0 = bypass)
SCORE_MINIMO    = int(os.getenv("SCORE_MINIMO",  "55"))   # score 0-100 para señal

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
TIME_EXIT_HORAS    = float(os.getenv("TIME_EXIT_HORAS", "4.0"))  # 4h en 1M
COOLDOWN_VELAS     = int(os.getenv("COOLDOWN_VELAS",    "5"))    # cooldown entre señales

# ─── Pares ───────────────────────────────────────────────────
SOLO_LONG          = os.getenv("SOLO_LONG", "false").lower() == "true"
PARES_BLOQUEADOS   = [p.strip() for p in os.getenv("PARES_BLOQUEADOS","").split(",") if p.strip()]
PARES_PRIORITARIOS = ["BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT",
                      "AVAX-USDT","ARB-USDT","OP-USDT","DOGE-USDT","LINK-USDT"]
MAX_PARES_SCAN     = int(os.getenv("MAX_PARES_SCAN",    "20"))
VOLUMEN_MIN_24H    = float(os.getenv("VOLUMEN_MIN_24H", "5000000"))
ANALISIS_WORKERS   = int(os.getenv("ANALISIS_WORKERS",  "4"))
LOOP_SECONDS       = int(os.getenv("LOOP_SECONDS",      "60"))  # 60s en 1M

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
    if TIMEFRAME not in ("1m","3m","5m","15m"):
        e.append(f"TIMEFRAME={TIMEFRAME} — para 1M usar '1m'")
    return e
