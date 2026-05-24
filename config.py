import os
from dotenv import load_dotenv

load_dotenv()

# ── BingX ─────────────────────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
BINGX_BASE_URL   = "https://open-api.bingx.com"

# ── Telegram ──────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Operativa ─────────────────────────────────────────────────
SYMBOL           = os.getenv("SYMBOL", "BTC-USDT")
LEVERAGE         = int(os.getenv("LEVERAGE", "10"))
RISK_PER_TRADE   = float(os.getenv("RISK_PER_TRADE", "0.015"))   # 1.5% por operación
MAX_POSITIONS    = int(os.getenv("MAX_POSITIONS", "2"))
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "0.06"))  # 6% máximo diario

# ── Temporalidad ──────────────────────────────────────────────
TIMEFRAME        = "3m"
HTF_TIMEFRAME    = "15m"
LOOKBACK         = 250   # velas históricas a cargar

# ── SL / TP ───────────────────────────────────────────────────
SL_ATR_MULT      = float(os.getenv("SL_ATR_MULT",  "1.5"))
TP_RR_STD        = float(os.getenv("TP_RR_STD",    "2.0"))  # RR para señal estándar
TP_RR_FUEL       = float(os.getenv("TP_RR_FUEL",   "2.5"))  # RR para fuel
TP_RR_SUP        = float(os.getenv("TP_RR_SUP",    "3.0"))  # RR para suprema
TRAIL_ATR_MULT   = float(os.getenv("TRAIL_ATR_MULT","1.0"))  # trailing stop

# ── Filtros de riesgo ─────────────────────────────────────────
MIN_CONVICTION   = int(os.getenv("MIN_CONVICTION", "5"))     # score mínimo 0-10
MAX_FUNDING_LONG = float(os.getenv("MAX_FUNDING_LONG",  "0.0010"))  # evitar longs con funding alto
MIN_FUNDING_SHORT= float(os.getenv("MIN_FUNDING_SHORT", "-0.0010")) # evitar shorts con funding negativo
COOLDOWN_CANDLES = int(os.getenv("COOLDOWN_CANDLES", "3"))   # velas de espera tras pérdida

# ── Multi-par ─────────────────────────────────────────────────
MULTI_PAIR       = os.getenv("MULTI_PAIR", "false").lower() == "true"
TOP_PAIRS        = int(os.getenv("TOP_PAIRS", "30"))
MIN_VOLUME_USDT  = float(os.getenv("MIN_VOLUME_USDT", "5000000"))  # 5M USDT volumen mínimo

# ── Railway ───────────────────────────────────────────────────
PORT             = int(os.getenv("PORT", "8080"))

# ── Parámetros de estrategia QF×JP ───────────────────────────
# L2 Factores
MOM_LOOKBACK     = 20
REV_LOOKBACK     = 8
VOL_LOOKBACK     = 14
ATR_PERIOD       = 10
W_MOM            = 0.40
W_REV            = 0.30
W_VOL            = 0.30
SIGNAL_SMOOTH    = 3

# L3 Decaimiento
DECAY_LEN        = 40
DECAY_THR        = 0.50

# L4 Dark Pool
DP_VOL_MULT      = 2.5
DP_BASELINE      = 20
SPREAD_LEN       = 5

# L5 Ejecución
EXEC_BASELINE    = 12
BP_THRESHOLD     = 0.18

# L6 Asimetría
ASYM_LEN         = 10
ASYM_BULL_RATIO  = 1.40
ASYM_BEAR_RATIO  = 1.40

# L7 Trendline
TL_LOOKBACK      = 30
TL_LEFT          = 5
TL_RIGHT         = 3
TL_BUFFER        = 0.15

# L8 Swing
SWING_LOW_LEFT   = 5
SWING_LOW_RIGHT  = 3
SWING_HIGH_LEFT  = 5
SWING_HIGH_RIGHT = 3
HL_COUNT_MIN     = 2
LH_COUNT_MIN     = 2
SWING_WINDOW     = 40

# L9 FVG
FVG_MIN_ATR      = 0.3
FVG_MAX_BARS     = 40

# L10 Order Blocks
OB_IMPULSE_ATR   = 1.5
OB_MAX_BARS      = 50

# L11 CVD
CVD_EMA_LEN      = 20
CVD_DIV_LEN      = 5

# L12 Squeeze
SQ_LEN           = 20
SQ_BB_MULT       = 2.0
SQ_KC_MULT       = 1.5

# HTF
HTF_FAST         = 9
HTF_SLOW         = 21
