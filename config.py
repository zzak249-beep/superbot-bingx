"""
Configuración Conflux 4 Bot v2
Todos los parámetros con valores por defecto conservadores.
"""

import os
from dataclasses import dataclass, field
from typing import List


PRESETS = {
    "Scalp": {
        "cooldown": 3, "adx_thr": 20, "stop_mode": "ATR Cap",
        "stop_atr_mult": 1.0, "stop_fixed_pct": 0.2,
        "rr1": 0.5, "rr2": 1.0, "rr3": 1.5, "rr4": 2.0,
        "leverage": 10, "max_risk_per_trade_pct": 1.0,
        "max_daily_loss_pct": 2.0, "min_signal_quality": 6,
    },
    "Daytrader": {
        "cooldown": 5, "adx_thr": 25, "stop_mode": "Supertrend",
        "stop_atr_mult": 1.5, "stop_fixed_pct": 0.3,
        "rr1": 0.5, "rr2": 1.0, "rr3": 2.0, "rr4": 3.0,
        "leverage": 5, "max_risk_per_trade_pct": 1.5,
        "max_daily_loss_pct": 3.0, "min_signal_quality": 5,
    },
    "Swing": {
        "cooldown": 10, "adx_thr": 20, "stop_mode": "Fixed %",
        "stop_atr_mult": 1.5, "stop_fixed_pct": 0.5,
        "rr1": 1.0, "rr2": 1.5, "rr3": 2.5, "rr4": 3.5,
        "leverage": 3, "max_risk_per_trade_pct": 2.0,
        "max_daily_loss_pct": 4.0, "min_signal_quality": 4,
    },
}

# Timeframe mayor para MTF (según TF primario)
MTF_MAP = {
    "1m": ("5m", "15m"),
    "5m": ("15m", "1h"),
    "15m": ("1h", "4h"),
    "30m": ("1h", "4h"),
    "1h": ("4h", "1d"),
    "4h": ("1d", None),
    "1d": (None, None),
}


@dataclass
class BotConfig:
    # ── Telegram ──────────────────────────────────────────────────────────
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # ── BingX ─────────────────────────────────────────────────────────────
    bingx_api_key: str = ""
    bingx_secret: str = ""
    bingx_testnet: bool = False
    auto_trade: bool = False

    # ── Pares y timeframe ─────────────────────────────────────────────────
    symbols: List[str] = field(default_factory=lambda: ["BTC-USDT", "ETH-USDT"])
    interval: str = "15m"
    kline_limit: int = 350
    scan_seconds: int = 60

    # ── Preset ────────────────────────────────────────────────────────────
    preset: str = "Daytrader"

    # ── Indicadores ───────────────────────────────────────────────────────
    vwma_len: int = 200
    ema_fast: int = 21
    ema_slow: int = 50
    rsi_len: int = 14
    rsi_bull: int = 55
    rsi_bear: int = 45
    atr_len: int = 10
    st_mult: float = 3.5
    use_adx: bool = True
    adx_len: int = 14
    adx_thr: int = 25

    # ── Señal ─────────────────────────────────────────────────────────────
    cooldown: int = 5
    stop_mode: str = "Supertrend"
    stop_atr_mult: float = 1.5
    stop_fixed_pct: float = 0.3
    rr1: float = 0.5
    rr2: float = 1.0
    rr3: float = 2.0
    rr4: float = 3.0

    # ── Filtros extra ─────────────────────────────────────────────────────
    use_mtf: bool = True
    min_volume_percentile: int = 30
    funding_threshold: float = 0.05   # % funding que rechaza la señal

    # ── Riesgo ────────────────────────────────────────────────────────────
    starting_balance: float = 1000.0
    leverage: int = 5
    max_risk_per_trade_pct: float = 1.5   # % capital en riesgo por trade
    max_position_usdt: float = 500.0
    max_open_trades: int = 3
    max_daily_loss_pct: float = 3.0
    max_weekly_loss_pct: float = 8.0
    max_drawdown_pct: float = 15.0
    min_signal_quality: int = 5
    use_session_filter: bool = True
    avoid_hours_utc: List[int] = field(default_factory=lambda: [0, 1, 2, 3])

    # ── Reporting ─────────────────────────────────────────────────────────
    dashboard_every_n_scans: int = 60

    # ── MTF (calculado automáticamente) ───────────────────────────────────
    htf1: str = "1h"
    htf2: str = "4h"


def load_config() -> BotConfig:
    cfg = BotConfig()

    cfg.telegram_token = os.environ["TELEGRAM_TOKEN"]
    cfg.telegram_chat_id = os.environ["TELEGRAM_CHAT_ID"]
    cfg.bingx_api_key = os.environ.get("BINGX_API_KEY", "")
    cfg.bingx_secret = os.environ.get("BINGX_SECRET", "")
    cfg.bingx_testnet = os.environ.get("BINGX_TESTNET", "false").lower() == "true"
    cfg.auto_trade = os.environ.get("AUTO_TRADE", "false").lower() == "true"

    if "SYMBOLS" in os.environ:
        cfg.symbols = [s.strip() for s in os.environ["SYMBOLS"].split(",")]
    if "INTERVAL" in os.environ:
        cfg.interval = os.environ["INTERVAL"]
    if "PRESET" in os.environ:
        cfg.preset = os.environ["PRESET"]
    if "SCAN_SECONDS" in os.environ:
        cfg.scan_seconds = int(os.environ["SCAN_SECONDS"])
    if "STARTING_BALANCE" in os.environ:
        cfg.starting_balance = float(os.environ["STARTING_BALANCE"])
    if "MAX_DAILY_LOSS_PCT" in os.environ:
        cfg.max_daily_loss_pct = float(os.environ["MAX_DAILY_LOSS_PCT"])
    if "MAX_DRAWDOWN_PCT" in os.environ:
        cfg.max_drawdown_pct = float(os.environ["MAX_DRAWDOWN_PCT"])

    # Aplicar preset
    p = PRESETS.get(cfg.preset, PRESETS["Daytrader"])
    for k, v in p.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)

    # MTF automático
    htf1, htf2 = MTF_MAP.get(cfg.interval, ("1h", "4h"))
    cfg.htf1 = htf1 or cfg.interval
    cfg.htf2 = htf2 or cfg.htf1

    return cfg


def config_to_engine(cfg: BotConfig) -> dict:
    return {
        "vwma_len": cfg.vwma_len, "ema_fast": cfg.ema_fast, "ema_slow": cfg.ema_slow,
        "rsi_len": cfg.rsi_len, "rsi_bull": cfg.rsi_bull, "rsi_bear": cfg.rsi_bear,
        "atr_len": cfg.atr_len, "st_mult": cfg.st_mult,
        "use_adx": cfg.use_adx, "adx_len": cfg.adx_len, "adx_thr": cfg.adx_thr,
        "cooldown": cfg.cooldown, "stop_mode": cfg.stop_mode,
        "stop_atr_mult": cfg.stop_atr_mult, "stop_fixed_pct": cfg.stop_fixed_pct,
        "rr1": cfg.rr1, "rr2": cfg.rr2, "rr3": cfg.rr3, "rr4": cfg.rr4,
        "min_volume_percentile": cfg.min_volume_percentile,
        "funding_threshold": cfg.funding_threshold,
    }


def config_to_risk(cfg: BotConfig) -> dict:
    return {
        "starting_balance": cfg.starting_balance,
        "leverage": cfg.leverage,
        "max_risk_per_trade_pct": cfg.max_risk_per_trade_pct,
        "max_position_usdt": cfg.max_position_usdt,
        "max_open_trades": cfg.max_open_trades,
        "max_daily_loss_pct": cfg.max_daily_loss_pct,
        "max_weekly_loss_pct": cfg.max_weekly_loss_pct,
        "max_drawdown_pct": cfg.max_drawdown_pct,
        "min_signal_quality": cfg.min_signal_quality,
        "use_session_filter": cfg.use_session_filter,
        "avoid_hours_utc": cfg.avoid_hours_utc,
        "rr2": cfg.rr2,
    }
