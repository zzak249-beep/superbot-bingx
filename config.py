"""
config.py — Sniper Bot V35: Golden Equilibrium
Todas las variables de entorno centralizadas aquí.
Sincronizadas con el Pine Script de TradingView.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── BingX ────────────────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
BINGX_BASE_URL   = "https://open-api.bingx.com"

# ─── Telegram ─────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── Estrategia V35 (sincronizado con Pine Script) ────────
EMA_FAST          = 7
EMA_MID           = 17
EMA_SLOW          = 21
PIVOT_LEN         = 5
VOL_MULT          = float(os.getenv("VOL_MULT", "1.5"))    # is_inst_vol threshold
ADX_MIN           = float(os.getenv("ADX_MIN", "20"))       # Filtro tendencia
ATR_SL_MULT       = 0.5                                     # SL = valley ± (ATR * 0.5)
CANDLE_INTERVAL   = os.getenv("CANDLE_INTERVAL", "3m")      # Velas de 3 minutos
TIME_STOP_CANDLES = 15                                       # 15 x 3min = 45 min

# ─── Gestión de riesgo ────────────────────────────────────
CAPITAL_PCT    = float(os.getenv("CAPITAL_PCT", "2"))       # % del balance por trade
MAX_OPEN_TRADES = int(os.getenv("MAX_TRADES", "3"))         # Máximo simultáneos
LEVERAGE       = int(os.getenv("LEVERAGE", "5"))            # Apalancamiento

# ─── Scanner ──────────────────────────────────────────────
TOP_N_SYMBOLS          = 20
SCAN_INTERVAL_MINUTES  = 60

# ─── Aprendizaje ──────────────────────────────────────────
DATA_DIR              = "data"
LEARNING_FILE         = "data/trades.json"
MIN_TRADES_TO_LEARN   = 10
SYMBOL_BLACKLIST_WR   = 30   # Bloquear símbolos con WR% < este valor tras 5+ trades

# ─── Modo ─────────────────────────────────────────────────
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
