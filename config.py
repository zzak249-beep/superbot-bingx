"""config.py — Parámetros centrales del Sniper Bot V49"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── Exchange ─────────────────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET", "")
SYMBOL           = os.getenv("SYMBOL", "BTC/USDT")
TIMEFRAME        = os.getenv("TIMEFRAME", "5m")
LEVERAGE         = int(os.getenv("LEVERAGE", "1"))
MODE             = os.getenv("MODE", "paper")          # live | paper

# ─── Telegram ─────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── Gestión de riesgo ────────────────────────────────────────
RISK_PCT         = float(os.getenv("RISK_PCT", "2.0"))  # % balance por op.
ATR_MULT_TP      = 2.0
ATR_MULT_SL      = 1.2
MAX_BARS_HOLD    = 20                                    # velas máx en posición

# ─── Motor Markov ─────────────────────────────────────────────
SLOPE_MIN        = 30.0
LOOKBACK_MARKOV  = 200
PROB_THRESHOLD   = 40.0

# ─── ADX Adaptativo ───────────────────────────────────────────
ADX_LEN          = 14
ADX_TREND        = 25
ADX_RANGE        = 20

# ─── Filtros institucionales ──────────────────────────────────
PIVOT_LEN        = 4
RVOL_MIN         = 1.5
POC_LOOKBACK     = 50

# ─── Candles a cargar ─────────────────────────────────────────
CANDLES_LIMIT    = 500

# ─── Intervalo de loop (segundos) ────────────────────────────
LOOP_INTERVAL    = 60   # revisa el mercado cada 60 s
