"""
Configuration — todos los parámetros del bot
Carga desde variables de entorno (.env / Railway env vars)
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # ── BingX API ────────────────────────────────────────────────────────────
    BINGX_API_KEY:    str   = os.getenv("BINGX_API_KEY", "")
    BINGX_SECRET_KEY: str   = os.getenv("BINGX_SECRET_KEY", "")
    DEMO_MODE:        bool  = os.getenv("DEMO_MODE", "true").lower() == "true"

    # ── Trading ──────────────────────────────────────────────────────────────
    LEVERAGE:             int   = int(os.getenv("LEVERAGE", "5"))
    MAX_OPEN_POSITIONS:   int   = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
    MAX_RISK_PER_TRADE:   float = float(os.getenv("MAX_RISK_PER_TRADE", "1.5"))  # %
    MAX_DAILY_LOSS_PCT:   float = float(os.getenv("MAX_DAILY_LOSS_PCT", "5.0"))  # %
    MIN_SCORE:            float = float(os.getenv("MIN_SCORE", "55.0"))
    TRADE_INTERVAL:       str   = os.getenv("TRADE_INTERVAL", "15m")
    LOOP_SLEEP_SECONDS:   int   = int(os.getenv("LOOP_SLEEP_SECONDS", "60"))

    # ── Signal Engine ─────────────────────────────────────────────────────────
    FAST_MA:         int   = int(os.getenv("FAST_MA", "50"))
    SLOW_MA:         int   = int(os.getenv("SLOW_MA", "200"))
    PROJ_LENGTH:     int   = int(os.getenv("PROJ_LENGTH", "10"))
    ADX_LEN:         int   = int(os.getenv("ADX_LEN", "14"))
    ADX_MIN:         float = float(os.getenv("ADX_MIN", "20.0"))
    ATR_LEN:         int   = int(os.getenv("ATR_LEN", "14"))
    ATR_MA_LEN:      int   = int(os.getenv("ATR_MA_LEN", "50"))
    ATR_MULT:        float = float(os.getenv("ATR_MULT", "1.0"))
    RSI_LEN:         int   = int(os.getenv("RSI_LEN", "14"))
    RSI_LONG_MIN:    float = float(os.getenv("RSI_LONG_MIN", "52.0"))
    RSI_SHORT_MAX:   float = float(os.getenv("RSI_SHORT_MAX", "48.0"))
    EMA_TREND:       int   = int(os.getenv("EMA_TREND", "200"))

    # ── Scanner ───────────────────────────────────────────────────────────────
    MIN_VOLUME_24H:    float = float(os.getenv("MIN_VOLUME_24H", "500000"))
    MAX_COINS_SCAN:    int   = int(os.getenv("MAX_COINS_SCAN", "15"))
    VOL_SPIKE_MIN:     float = float(os.getenv("VOL_SPIKE_MIN", "2.0"))
    MIN_CHANGE_ABS:    float = float(os.getenv("MIN_CHANGE_ABS", "2.0"))
    SCAN_INTERVAL_MIN: int   = int(os.getenv("SCAN_INTERVAL_MIN", "15"))

    # ── Candles to fetch ──────────────────────────────────────────────────────
    CANDLES: int = int(os.getenv("CANDLES", "300"))

    def validate(self):
        errors = []
        if not self.BINGX_API_KEY:
            errors.append("BINGX_API_KEY no configurada")
        if not self.BINGX_SECRET_KEY:
            errors.append("BINGX_SECRET_KEY no configurada")
        if self.DEMO_MODE:
            print("⚠️  DEMO MODE activo — no se enviarán órdenes reales")
        if errors:
            raise ValueError("Errores de configuración:\n" + "\n".join(errors))


# Instancia global
cfg = Config()
