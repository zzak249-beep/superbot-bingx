"""
GUA-USDT Bot v3 — Configuración
v3 changes:
  • LOOKBACK 150→250 (fix EMA200 warm-up en 3m, aunque EMA200 ya no se usa en scorer 3m)
  • LOOKBACK_MACRO 72→210 (3.5 días de 1h — EMA200 necesita 200 velas de warm-up)
  • MFI_PERIOD añadido
  • COMP_RANGE_LB, COMP_VOL_LB para pre_compression
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── BingX API ──────────────────────────────────────────────────────────────────
BINGX_API_KEY  = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET   = os.getenv("BINGX_SECRET", "")
BASE_URL       = "https://open-api.bingx.com"

# ── Símbolo y temporalidades ───────────────────────────────────────────────────
SYMBOL           = os.getenv("SYMBOL", "GUA-USDT")
INTERVAL         = os.getenv("INTERVAL", "3m")
INTERVAL_TREND   = os.getenv("INTERVAL_TREND", "15m")
INTERVAL_MACRO   = os.getenv("INTERVAL_MACRO", "1h")

# v3: LOOKBACK aumentado
# 3m: 250 velas (aunque EMA200 no se usa en scorer, el warm-up extra mejora ATR/ADX)
# 15m: 120 velas (2 sesiones completas de London+NY)
# 1h: 210 velas — CRÍTICO para EMA200 en macro_bias (200 velas mínimo de warm-up)
LOOKBACK         = 250
LOOKBACK_TREND   = 120
LOOKBACK_MACRO   = 210

# ── Telegram ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Modo ───────────────────────────────────────────────────────────────────────
MODE             = os.getenv("MODE", "SIGNAL")

# ── Capital ────────────────────────────────────────────────────────────────────
LEVERAGE         = int(os.getenv("LEVERAGE",        "5"))
RISK_PCT         = float(os.getenv("RISK_PCT",      "0.02"))
MAX_OPEN_TRADES  = int(os.getenv("MAX_OPEN_TRADES", "1"))

# ── ATR dinámico ──────────────────────────────────────────────────────────────
ATR_SL_MULT      = float(os.getenv("ATR_SL_MULT",    "1.5"))
ATR_TP1_MULT     = float(os.getenv("ATR_TP1_MULT",   "2.0"))
ATR_TP2_MULT     = float(os.getenv("ATR_TP2_MULT",   "4.0"))
ATR_TRAIL_MULT   = float(os.getenv("ATR_TRAIL_MULT",  "1.0"))
ATR_HIGHVOL_MULT = float(os.getenv("ATR_HIGHVOL_MULT", "2.0"))

# ── Indicadores clásicos ───────────────────────────────────────────────────────
RSI_PERIOD       = 14
RSI_OB           = float(os.getenv("RSI_OB",  "63"))
RSI_OS           = float(os.getenv("RSI_OS",  "37"))
EMA_FAST         = 9
EMA_SLOW         = 21
EMA_TREND        = 50
EMA_MACRO        = 200   # Solo en 1h (candles_macro), NO en 3m scorer
ADX_PERIOD       = 14
ADX_MIN          = float(os.getenv("ADX_MIN", "18"))

# ── MFI (v3) ──────────────────────────────────────────────────────────────────
MFI_PERIOD       = 14
MFI_OB           = float(os.getenv("MFI_OB", "70"))
MFI_OS           = float(os.getenv("MFI_OS", "30"))

# ── TTM Squeeze ────────────────────────────────────────────────────────────────
BB_PERIOD        = 20
BB_MULT          = 2.0
KC_PERIOD        = 20
KC_MULT          = 1.5
MOM_PERIOD       = 12

# ── VWAP ──────────────────────────────────────────────────────────────────────
VWAP_PERIOD      = 60
VWAP_BAND_MULT   = 1.5

# ── RVOL ──────────────────────────────────────────────────────────────────────
RVOL_PERIOD      = 20
RVOL_MIN         = float(os.getenv("RVOL_MIN", "1.3"))

# ── CVD ────────────────────────────────────────────────────────────────────────
CVD_LB           = 20
CVD_DIV_LB       = 10

# ── FVG ────────────────────────────────────────────────────────────────────────
FVG_LOOKBACK     = 30
FVG_MIN_SIZE     = float(os.getenv("FVG_MIN_SIZE", "0.003"))

# ── Order Blocks ───────────────────────────────────────────────────────────────
OB_LOOKBACK      = 40
OB_IMPULSE_BARS  = 3

# ── Liquidity Sweeps ───────────────────────────────────────────────────────────
LIQ_LOOKBACK     = 25
LIQ_TOLERANCE    = float(os.getenv("LIQ_TOLERANCE", "0.002"))

# ── ATR Percentil ─────────────────────────────────────────────────────────────
ATR_PERCENTILE_LB = 50

# ── Pre-Compression (v3) ──────────────────────────────────────────────────────
COMP_RANGE_LB    = int(os.getenv("COMP_RANGE_LB", "8"))   # velas recientes para rango
COMP_VOL_LB      = int(os.getenv("COMP_VOL_LB",  "20"))   # velas históricas para vol

# ── Funding ────────────────────────────────────────────────────────────────────
FUNDING_EXTREME_LONG  = float(os.getenv("FUNDING_EXTREME_LONG",  "0.0003"))
FUNDING_EXTREME_SHORT = float(os.getenv("FUNDING_EXTREME_SHORT", "-0.0003"))

# ── OI ─────────────────────────────────────────────────────────────────────────
OI_HISTORY_LEN   = 5

# ── Señal ──────────────────────────────────────────────────────────────────────
SCORE_THR        = float(os.getenv("SCORE_THR", "0.58"))

# ── Cooldown ───────────────────────────────────────────────────────────────────
COOLDOWN_MIN     = int(os.getenv("COOLDOWN_MIN", "15"))

# ── Sesiones (UTC) ─────────────────────────────────────────────────────────────
SESSION_FILTER   = os.getenv("SESSION_FILTER", "true").lower() == "true"
SESSION_HOURS    = [(7, 12), (13, 18)]

# ── Health server ──────────────────────────────────────────────────────────────
PORT             = int(os.getenv("PORT", "8080"))
