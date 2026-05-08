"""
Config — Conflux 4 Bot v4.0
Cambios vs v3.2:
  - ADX mínimo 18 (era 22/20) — captura tendencias emergentes
  - RSI BULL [40-72], BEAR [28-60] — bandas más amplias, más señales
  - Cooldown reducido a 2 min (era 3-5) — no pierde el siguiente setup
  - Volumen percentil 20 (era 30) — menos restrictivo
  - MIN_QUALITY bajada a 5 para no filtrar señales buenas con MTF no confirmado
"""
import os
from dataclasses import dataclass, field
from typing import List
from loguru import logger


PRESETS = {
    "Scalp": {
        "cooldown": 2, "adx_min": 18, "adx_thr": 18,
        "stop_mode": "ATR", "sl_atr_mult": 1.2, "stop_fixed_pct": 0.2,
        "min_rr": 1.5, "rr1": 0.5, "rr2": 1.5, "rr3": 2.5, "rr4": 3.5,
        "leverage": 10, "max_risk_per_trade_pct": 0.8, "max_daily_loss_pct": 2.0,
        "min_signal_quality": 5,
        "rsi_bull_lo": 42, "rsi_bull_hi": 72, "rsi_bear_lo": 28, "rsi_bear_hi": 58,
    },
    "Daytrader": {
        "cooldown": 2, "adx_min": 18, "adx_thr": 18,
        "stop_mode": "ATR", "sl_atr_mult": 1.5, "stop_fixed_pct": 0.3,
        "min_rr": 2.0, "rr1": 0.5, "rr2": 2.0, "rr3": 3.0, "rr4": 4.5,
        "leverage": 5, "max_risk_per_trade_pct": 1.5, "max_daily_loss_pct": 3.0,
        "min_signal_quality": 5,
        "rsi_bull_lo": 40, "rsi_bull_hi": 72, "rsi_bear_lo": 28, "rsi_bear_hi": 60,
    },
    "Swing": {
        "cooldown": 5, "adx_min": 18, "adx_thr": 18,
        "stop_mode": "ATR", "sl_atr_mult": 2.0, "stop_fixed_pct": 0.5,
        "min_rr": 2.5, "rr1": 1.0, "rr2": 2.5, "rr3": 4.0, "rr4": 6.0,
        "leverage": 3, "max_risk_per_trade_pct": 2.0, "max_daily_loss_pct": 4.0,
        "min_signal_quality": 5,
        "rsi_bull_lo": 40, "rsi_bull_hi": 72, "rsi_bear_lo": 28, "rsi_bear_hi": 60,
    },
}

MTF_MAP = {
    "1m":  ("5m",  "15m"), "5m":  ("15m", "1h"),
    "15m": ("1h",  "4h"),  "30m": ("1h",  "4h"),
    "1h":  ("4h",  "1d"),  "4h":  ("1d",  None),
    "1d":  (None,  None),
}


@dataclass
class BotConfig:
    # Telegram
    telegram_token:   str  = ""
    telegram_chat_id: str  = ""

    # BingX
    bingx_api_key:  str  = ""
    bingx_secret:   str  = ""
    bingx_testnet:  bool = False
    auto_trade:     bool = True

    # Scanner
    fixed_symbols:        List[str] = field(default_factory=list)
    top_n_symbols:        int   = 50
    min_volume_usdt:      float = 5_000_000
    symbol_refresh_hours: int   = 4
    symbol_blacklist:     List[str] = field(default_factory=lambda: [
        "USDC-USDT","BUSD-USDT","TUSD-USDT","DAI-USDT","USDT-USDT","FDUSD-USDT",
    ])
    symbols: List[str] = field(default_factory=lambda: ["BTC-USDT","ETH-USDT"])

    # Timeframe
    interval:     str = "15m"
    kline_limit:  int = 350
    scan_seconds: int = 60

    # Preset
    preset: str = "Daytrader"

    # Indicadores
    vwma_len: int   = 100
    ema_fast: int   = 21
    ema_slow: int   = 50
    rsi_len:  int   = 14
    atr_len:  int   = 10
    st_mult:  float = 3.5
    adx_len:  int   = 14

    # Filtros (el preset los sobreescribe)
    adx_min:       int   = 18     # ← bajado de 22
    adx_thr:       int   = 18
    sl_atr_mult:   float = 1.5
    sl_min_pct:    float = 0.3    # ← bajado de 0.5
    min_rr:        float = 2.0
    rsi_bull_lo:   int   = 40     # ← bajado de 45
    rsi_bull_hi:   int   = 72     # ← subido de 68
    rsi_bear_lo:   int   = 28     # ← bajado de 32
    rsi_bear_hi:   int   = 60     # ← subido de 55

    # TPs
    cooldown:       int   = 2     # ← bajado de 3-5
    stop_mode:      str   = "ATR"
    stop_atr_mult:  float = 1.5
    stop_fixed_pct: float = 0.3
    rr1: float = 0.5
    rr2: float = 2.0
    rr3: float = 3.0
    rr4: float = 4.5

    # Extras
    use_mtf:               bool  = True
    min_volume_percentile: int   = 20    # ← bajado de 30
    funding_threshold:     float = 0.03

    # Riesgo
    starting_balance:      float = 1000.0
    leverage:              int   = 5
    max_risk_per_trade_pct:float = 1.5
    max_position_usdt:     float = 500.0
    max_open_trades:       int   = 3
    max_daily_loss_pct:    float = 3.0
    max_weekly_loss_pct:   float = 8.0
    max_drawdown_pct:      float = 15.0
    min_signal_quality:    int   = 5     # ← bajado de 6
    post_sl_cooldown_scans:int   = 2
    use_session_filter:    bool  = False
    avoid_hours_utc:       List[int] = field(default_factory=list)

    # Reporting
    dashboard_every_n_scans: int = 60

    # MTF
    htf1: str = "1h"
    htf2: str = "4h"


def load_config() -> BotConfig:
    cfg = BotConfig()

    cfg.telegram_token   = os.environ.get("TELEGRAM_TOKEN", "")
    cfg.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    cfg.bingx_api_key    = os.environ.get("BINGX_API_KEY", "")
    cfg.bingx_secret     = os.environ.get("BINGX_SECRET", os.environ.get("BINGX_API_SECRET", ""))
    cfg.bingx_testnet    = os.environ.get("BINGX_TESTNET", "false").lower() == "true"
    cfg.auto_trade       = os.environ.get("AUTO_TRADE", "true").lower() == "true"

    if not cfg.telegram_token:
        raise RuntimeError("TELEGRAM_TOKEN no configurado")

    for key in ("FIXED_SYMBOLS", "SYMBOLS"):
        if key in os.environ:
            cfg.fixed_symbols = [s.strip() for s in os.environ[key].split(",") if s.strip()]
            break

    _ei(cfg, "TOP_N_SYMBOLS",        "top_n_symbols")
    _ef(cfg, "MIN_VOLUME_USDT",      "min_volume_usdt")
    _ei(cfg, "SYMBOL_REFRESH_HOURS", "symbol_refresh_hours")

    if cfg.fixed_symbols:
        cfg.symbols = cfg.fixed_symbols

    _es(cfg, "INTERVAL",            "interval")
    _es(cfg, "PRESET",              "preset")
    _ei(cfg, "SCAN_SECONDS",        "scan_seconds")
    _ef(cfg, "STARTING_BALANCE",    "starting_balance")
    _ef(cfg, "BASE_SIZE_USDT",      "starting_balance")   # compat alias
    _ef(cfg, "MAX_DAILY_LOSS_PCT",  "max_daily_loss_pct")
    _ef(cfg, "MAX_DRAWDOWN_PCT",    "max_drawdown_pct")
    _ei(cfg, "MAX_OPEN_TRADES",     "max_open_trades")
    _ei(cfg, "MAX_POSITIONS",       "max_open_trades")    # compat alias
    _ei(cfg, "MIN_QUALITY",         "min_signal_quality")
    _ei(cfg, "POST_SL_COOLDOWN",    "post_sl_cooldown_scans")
    _ei(cfg, "LEVERAGE",            "leverage")

    # Aplicar preset
    p = PRESETS.get(cfg.preset, PRESETS["Daytrader"])
    for k, v in p.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)

    # Env vars tienen prioridad final sobre preset
    _ei(cfg, "ADX_MIN",       "adx_min")
    _ef(cfg, "SL_ATR_MULT",   "sl_atr_mult")
    _ef(cfg, "MIN_RR",        "min_rr")
    _ei(cfg, "RSI_BULL_LO",   "rsi_bull_lo")
    _ei(cfg, "RSI_BULL_HI",   "rsi_bull_hi")
    _ei(cfg, "RSI_BEAR_LO",   "rsi_bear_lo")
    _ei(cfg, "RSI_BEAR_HI",   "rsi_bear_hi")
    _ef(cfg, "FUNDING_THR",   "funding_threshold")
    _ei(cfg, "MIN_VOL_PCT",   "min_volume_percentile")
    _ef(cfg, "MAX_RISK_PCT",  "max_risk_per_trade_pct")

    cfg.adx_thr = cfg.adx_min

    htf1, htf2 = MTF_MAP.get(cfg.interval, ("1h", "4h"))
    cfg.htf1 = htf1 or cfg.interval
    cfg.htf2 = htf2 or cfg.htf1

    _log_config(cfg)
    return cfg


def _ei(cfg, env: str, attr: str):
    if env in os.environ:
        try: setattr(cfg, attr, int(os.environ[env]))
        except ValueError: pass

def _ef(cfg, env: str, attr: str):
    if env in os.environ:
        try: setattr(cfg, attr, float(os.environ[env]))
        except ValueError: pass

def _es(cfg, env: str, attr: str):
    if env in os.environ:
        setattr(cfg, attr, os.environ[env])


def _log_config(cfg: BotConfig):
    logger.info("══════════════════ CONFIG ACTIVA ══════════════════")
    logger.info(f"  Preset:        {cfg.preset}")
    logger.info(f"  Interval:      {cfg.interval} | HTF1={cfg.htf1} HTF2={cfg.htf2}")
    logger.info(f"  AUTO_TRADE:    {'✅ SÍ — operando en real' if cfg.auto_trade else '❌ NO — solo señales'}")
    logger.info(f"  Testnet:       {cfg.bingx_testnet}")
    logger.info(f"  ADX mínimo:    {cfg.adx_min}")
    logger.info(f"  RSI BULL:      [{cfg.rsi_bull_lo}-{cfg.rsi_bull_hi}]")
    logger.info(f"  RSI BEAR:      [{cfg.rsi_bear_lo}-{cfg.rsi_bear_hi}]")
    logger.info(f"  Cooldown:      {cfg.cooldown} min")
    logger.info(f"  Vol percentil: {cfg.min_volume_percentile}%")
    logger.info(f"  SL mult ATR:   {cfg.sl_atr_mult}x")
    logger.info(f"  R/R mínimo:    {cfg.min_rr}")
    logger.info(f"  Leverage:      {cfg.leverage}x")
    logger.info(f"  Riesgo/trade:  {cfg.max_risk_per_trade_pct}%")
    logger.info(f"  Max trades:    {cfg.max_open_trades}")
    logger.info(f"  Calidad mín:   {cfg.min_signal_quality}/10")
    logger.info(f"  Daily loss:    {cfg.max_daily_loss_pct}%")
    logger.info("═══════════════════════════════════════════════════")


def config_to_engine(cfg: BotConfig) -> dict:
    return {
        "vwma_len": cfg.vwma_len, "ema_fast": cfg.ema_fast, "ema_slow": cfg.ema_slow,
        "rsi_len": cfg.rsi_len, "atr_len": cfg.atr_len, "st_mult": cfg.st_mult,
        "adx_len": cfg.adx_len, "adx_min": cfg.adx_min,
        "sl_atr_mult": cfg.sl_atr_mult, "sl_min_pct": cfg.sl_min_pct,
        "min_rr": cfg.min_rr,
        "rsi_bull_lo": cfg.rsi_bull_lo, "rsi_bull_hi": cfg.rsi_bull_hi,
        "rsi_bear_lo": cfg.rsi_bear_lo, "rsi_bear_hi": cfg.rsi_bear_hi,
        "cooldown": cfg.cooldown, "stop_mode": cfg.stop_mode,
        "stop_atr_mult": cfg.sl_atr_mult, "stop_fixed_pct": cfg.stop_fixed_pct,
        "rr1": cfg.rr1, "rr2": cfg.rr2, "rr3": cfg.rr3, "rr4": cfg.rr4,
        "min_volume_percentile": cfg.min_volume_percentile,
        "funding_threshold": cfg.funding_threshold,
    }


def config_to_risk(cfg: BotConfig) -> dict:
    return {
        "starting_balance": cfg.starting_balance, "leverage": cfg.leverage,
        "max_risk_per_trade_pct": cfg.max_risk_per_trade_pct,
        "max_position_usdt": cfg.max_position_usdt,
        "max_open_trades": cfg.max_open_trades,
        "max_daily_loss_pct": cfg.max_daily_loss_pct,
        "max_weekly_loss_pct": cfg.max_weekly_loss_pct,
        "max_drawdown_pct": cfg.max_drawdown_pct,
        "min_signal_quality": cfg.min_signal_quality,
        "use_session_filter": cfg.use_session_filter,
        "avoid_hours_utc": cfg.avoid_hours_utc,
        "rr2": cfg.rr2, "post_sl_cooldown_scans": cfg.post_sl_cooldown_scans,
    }
