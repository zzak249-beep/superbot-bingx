"""
config.py — Configuración centralizada del bot vía variables de entorno.
"""

import os
from dataclasses import dataclass, field
from typing import List


def _list(key: str, default: str) -> List[str]:
    return [s.strip() for s in os.getenv(key, default).split(",") if s.strip()]


@dataclass
class Config:
    # ── BingX ────────────────────────────────────────────────────────────────
    API_KEY:    str = field(default_factory=lambda: os.getenv("BINGX_API_KEY", ""))
    API_SECRET: str = field(default_factory=lambda: os.getenv("BINGX_API_SECRET", ""))
    BASE_URL:   str = "https://open-api.bingx.com"
    # URL correcta para WebSocket de klines de swap/perpetuos
    WS_URL:     str = "wss://open-api.bingx.com/swap-market"

    # ── Telegram ─────────────────────────────────────────────────────────────
    TG_TOKEN:   str = field(default_factory=lambda: os.getenv("TELEGRAM_TOKEN", ""))
    TG_CHAT_ID: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # ── Pares a operar ────────────────────────────────────────────────────────
    SYMBOLS: List[str] = field(
        default_factory=lambda: _list(
            "SYMBOLS",
            "BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT,XRP-USDT"
        )
    )

    # ── Gestión de riesgo ─────────────────────────────────────────────────────
    LEVERAGE:        int   = field(default_factory=lambda: int(os.getenv("LEVERAGE", "10")))
    BASE_SIZE_USDT:  float = field(default_factory=lambda: float(os.getenv("BASE_SIZE_USDT", "10")))
    MAX_POSITIONS:   int   = field(default_factory=lambda: int(os.getenv("MAX_POSITIONS", "3")))
    MAX_RISK_PCT:    float = field(default_factory=lambda: float(os.getenv("MAX_RISK_PCT", "0.015")))
    # Límites de tamaño de trade (usados por engine.py)
    MIN_TRADE_USDT:  float = field(default_factory=lambda: float(os.getenv("MIN_TRADE_USDT", "5.0")))
    MAX_TRADE_USDT:  float = field(default_factory=lambda: float(os.getenv("MAX_TRADE_USDT", "100.0")))

    # ── Timeframes ────────────────────────────────────────────────────────────
    HTF: str = field(default_factory=lambda: os.getenv("HTF", "1h"))
    MTF: str = field(default_factory=lambda: os.getenv("MTF", "15m"))
    LTF: str = field(default_factory=lambda: os.getenv("LTF", "5m"))

    # ── Parámetros de estrategia ──────────────────────────────────────────────
    EMA_SLOW:       int   = field(default_factory=lambda: int(os.getenv("EMA_SLOW", "50")))
    EMA_FAST:       int   = field(default_factory=lambda: int(os.getenv("EMA_FAST", "20")))
    SWING_LOOKBACK: int   = field(default_factory=lambda: int(os.getenv("SWING_LOOKBACK", "7")))
    VOL_SMA_LEN:    int   = field(default_factory=lambda: int(os.getenv("VOL_SMA_LEN", "20")))
    ATR_LEN:        int   = field(default_factory=lambda: int(os.getenv("ATR_LEN", "14")))
    ATR_SL_MULT:    float = field(default_factory=lambda: float(os.getenv("ATR_SL_MULT", "1.5")))
    ATR_TP_MULT:    float = field(default_factory=lambda: float(os.getenv("ATR_TP_MULT", "2.5")))
    CVD_LEN:        int   = field(default_factory=lambda: int(os.getenv("CVD_LEN", "20")))
    REGIME_ATR_FAST:int   = field(default_factory=lambda: int(os.getenv("REGIME_ATR_FAST", "7")))
    REGIME_ATR_SLOW:int   = field(default_factory=lambda: int(os.getenv("REGIME_ATR_SLOW", "50")))

    # ── Modo de posición ──────────────────────────────────────────────────────
    POSITION_MODE: str = field(default_factory=lambda: os.getenv("POSITION_MODE", "ONE_WAY"))

    # ── Scan ──────────────────────────────────────────────────────────────────
    SCAN_INTERVAL: float = field(default_factory=lambda: float(os.getenv("SCAN_INTERVAL", "2.0")))

    # ── Paper trading ─────────────────────────────────────────────────────────
    PAPER: bool = field(default_factory=lambda: os.getenv("PAPER", "true").lower() == "true")

    def validate(self):
        missing = []
        if not self.API_KEY:    missing.append("BINGX_API_KEY")
        if not self.API_SECRET: missing.append("BINGX_API_SECRET")
        if not self.TG_TOKEN:   missing.append("TELEGRAM_TOKEN")
        if not self.TG_CHAT_ID: missing.append("TELEGRAM_CHAT_ID")
        if missing:
            raise EnvironmentError(
                f"Variables de entorno requeridas no definidas: {missing}"
            )
        return self


# Instancia global
config = Config()
