"""
Configuración Conflux 4 Bot v3.1 (MEJORADO)

Cambios sobre v3:
  - adx_min=22: filtro ADX siempre activo (era use_adx=False en presets)
  - R/R mínimo 2.0 (era 1.0 en Daytrader)
  - sl_atr_mult=1.5: SL basado en ATR real
  - RSI por zonas (rsi_bull_lo/hi, rsi_bear_lo/hi)
  - funding_threshold=0.03 (más conservador, era 0.05)
  - min_volume_percentile=30 (era 20)
  - Log completo de config al arranque
  - Nuevas env vars: ADX_MIN, SL_ATR_MULT, MIN_RR, RSI_BULL_LO/HI, RSI_BEAR_LO/HI
"""

import os
from dataclasses import dataclass, field
from typing import List
from loguru import logger


PRESETS = {
    "Scalp": {
        "cooldown": 3,
        "adx_min": 22,           # ADX mínimo siempre activo
        "adx_thr": 22,
        "stop_mode": "ATR",
        "sl_atr_mult": 1.2,
        "stop_fixed_pct": 0.2,
        "min_rr": 1.5,           # Scalp puede tener RR menor
        "rr1": 0.5, "rr2": 1.5, "rr3": 2.5, "rr4": 3.5,
        "leverage": 10, "max_risk_per_trade_pct": 0.8,
        "max_daily_loss_pct": 2.0, "min_signal_quality": 5,
        "rsi_bull_lo": 47, "rsi_bull_hi": 65,
        "rsi_bear_lo": 35, "rsi_bear_hi": 53,
    },
    "Daytrader": {
        "cooldown": 5,
        "adx_min": 22,
        "adx_thr": 22,
        "stop_mode": "ATR",
        "sl_atr_mult": 1.5,
        "stop_fixed_pct": 0.3,
        "min_rr": 2.0,           # R/R mínimo 2.0 (MEJORA CLAVE)
        "rr1": 0.5, "rr2": 2.0, "rr3": 3.0, "rr4": 4.5,
        "leverage": 5, "max_risk_per_trade_pct": 1.5,
        "max_daily_loss_pct": 3.0, "min_signal_quality": 5,
        "rsi_bull_lo": 45, "rsi_bull_hi": 68,
        "rsi_bear_lo": 32, "rsi_bear_hi": 55,
    },
    "Swing": {
        "cooldown": 10,
        "adx_min": 20,           # Swing permite tendencias algo más débiles
        "adx_thr": 20,
        "stop_mode": "ATR",
        "sl_atr_mult": 2.0,      # SL más amplio para swing
        "stop_fixed_pct": 0.5,
        "min_rr": 2.5,
        "rr1": 1.0, "rr2": 2.5, "rr3": 4.0, "rr4": 6.0,
        "leverage": 3, "max_risk_per_trade_pct": 2.0,
        "max_daily_loss_pct": 4.0, "min_signal_quality": 6,
        "rsi_bull_lo": 45, "rsi_bull_hi": 65,
        "rsi_bear_lo": 35, "rsi_bear_hi": 55,
    },
}

MTF_MAP = {
    "1m":  ("5m",  "15m"),
    "5m":  ("15m", "1h"),
    "15m": ("1h",  "4h"),
    "30m": ("1h",  "4h"),
    "1h":  ("4h",  "1d"),
    "4h":  ("1d",  None),
    "1d":  (None,  None),
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

    # ── Scanner dinámico ──────────────────────────────────────────────────
    fixed_symbols: List[str] = field(default_factory=list)
    top_n_symbols: int = 50
    min_volume_usdt: float = 5_000_000
    symbol_refresh_hours: int = 4
    symbol_blacklist: List[str] = field(default_factory=lambda: [
        "USDC-USDT", "BUSD-USDT", "TUSD-USDT", "DAI-USDT",
        "USDP-USDT", "FDUSD-USDT", "USDT-USDT",
    ])
    symbols: List[str] = field(default_factory=lambda: ["BTC-USDT", "ETH-USDT"])

    # ── Timeframe ─────────────────────────────────────────────────────────
    interval: str = "15m"
    kline_limit: int = 350
    scan_seconds: int = 60

    # ── Preset ────────────────────────────────────────────────────────────
    preset: str = "Daytrader"

    # ── Indicadores ───────────────────────────────────────────────────────
    vwma_len: int = 100
    ema_fast: int = 21
    ema_slow: int = 50
    rsi_len: int = 14
    atr_len: int = 10
    st_mult: float = 3.5
    adx_len: int = 14

    # ── Filtros de señal mejorados ────────────────────────────────────────
    adx_min: int = 22                # ADX mínimo para emitir señal
    adx_thr: int = 22                # Alias compatible con v2
    sl_atr_mult: float = 1.5         # SL = ATR × este multiplicador
    sl_min_pct: float = 0.5          # SL mínimo como % del precio
    min_rr: float = 2.0              # R/R mínimo garantizado

    # Zonas RSI válidas por dirección
    rsi_bull_lo: int = 45
    rsi_bull_hi: int = 68
    rsi_bear_lo: int = 32
    rsi_bear_hi: int = 55

    # ── TPs ───────────────────────────────────────────────────────────────
    cooldown: int = 5
    stop_mode: str = "ATR"
    stop_atr_mult: float = 1.5
    stop_fixed_pct: float = 0.3
    rr1: float = 0.5
    rr2: float = 2.0                 # Era 1.0, subido a 2.0
    rr3: float = 3.0
    rr4: float = 4.5

    # ── Filtros extra ─────────────────────────────────────────────────────
    use_mtf: bool = True
    min_volume_percentile: int = 30   # Era 20, subido a 30
    funding_threshold: float = 0.03   # Era 0.05, bajado a 0.03

    # ── Riesgo ────────────────────────────────────────────────────────────
    starting_balance: float = 1000.0
    leverage: int = 5
    max_risk_per_trade_pct: float = 1.5
    max_position_usdt: float = 500.0
    max_open_trades: int = 3
    max_daily_loss_pct: float = 3.0
    max_weekly_loss_pct: float = 8.0
    max_drawdown_pct: float = 15.0
    min_signal_quality: int = 5       # Era 4, subido a 5

    # Cooldown post-SL: no re-entrar N scans en el mismo par tras un stop
    post_sl_cooldown_scans: int = 2

    use_session_filter: bool = True
    avoid_hours_utc: List[int] = field(default_factory=lambda: [0, 1, 2, 3])

    # ── Reporting ─────────────────────────────────────────────────────────
    dashboard_every_n_scans: int = 60

    # ── MTF ───────────────────────────────────────────────────────────────
    htf1: str = "1h"
    htf2: str = "4h"


def load_config() -> BotConfig:
    cfg = BotConfig()

    cfg.telegram_token   = os.environ["TELEGRAM_TOKEN"]
    cfg.telegram_chat_id = os.environ["TELEGRAM_CHAT_ID"]
    cfg.bingx_api_key    = os.environ.get("BINGX_API_KEY", "")
    cfg.bingx_secret     = os.environ.get("BINGX_SECRET", "")
    cfg.bingx_testnet    = os.environ.get("BINGX_TESTNET", "false").lower() == "true"
    cfg.auto_trade       = os.environ.get("AUTO_TRADE", "false").lower() == "true"

    # ── Scanner dinámico ──────────────────────────────────────────────────
    if "FIXED_SYMBOLS" in os.environ:
        cfg.fixed_symbols = [s.strip() for s in os.environ["FIXED_SYMBOLS"].split(",") if s.strip()]
    elif "SYMBOLS" in os.environ:
        cfg.fixed_symbols = [s.strip() for s in os.environ["SYMBOLS"].split(",") if s.strip()]

    _env_int(cfg, "TOP_N_SYMBOLS",       "top_n_symbols")
    _env_float(cfg, "MIN_VOLUME_USDT",   "min_volume_usdt")
    _env_int(cfg, "SYMBOL_REFRESH_HOURS","symbol_refresh_hours")

    if cfg.fixed_symbols:
        cfg.symbols = cfg.fixed_symbols

    _env_str(cfg,   "INTERVAL",           "interval")
    _env_str(cfg,   "PRESET",             "preset")
    _env_int(cfg,   "SCAN_SECONDS",       "scan_seconds")
    _env_float(cfg, "STARTING_BALANCE",   "starting_balance")
    _env_float(cfg, "MAX_DAILY_LOSS_PCT", "max_daily_loss_pct")
    _env_float(cfg, "MAX_DRAWDOWN_PCT",   "max_drawdown_pct")
    _env_int(cfg,   "VWMA_LEN",           "vwma_len")
    _env_int(cfg,   "MIN_VOL_PCT",        "min_volume_percentile")
    _env_int(cfg,   "MIN_QUALITY",        "min_signal_quality")
    _env_int(cfg,   "MAX_OPEN_TRADES",    "max_open_trades")
    _env_int(cfg,   "POST_SL_COOLDOWN",   "post_sl_cooldown_scans")

    # Aplicar preset
    p = PRESETS.get(cfg.preset, PRESETS["Daytrader"])
    for k, v in p.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)

    # Env vars tienen prioridad FINAL sobre preset
    _env_int(cfg,   "ADX_MIN",        "adx_min")
    _env_int(cfg,   "ADX_THR",        "adx_thr")
    _env_float(cfg, "SL_ATR_MULT",    "sl_atr_mult")
    _env_float(cfg, "SL_MIN_PCT",     "sl_min_pct")
    _env_float(cfg, "MIN_RR",         "min_rr")
    _env_int(cfg,   "RSI_BULL_LO",    "rsi_bull_lo")
    _env_int(cfg,   "RSI_BULL_HI",    "rsi_bull_hi")
    _env_int(cfg,   "RSI_BEAR_LO",    "rsi_bear_lo")
    _env_int(cfg,   "RSI_BEAR_HI",    "rsi_bear_hi")
    _env_float(cfg, "FUNDING_THR",    "funding_threshold")

    # adx_thr sincronizado con adx_min (compatibilidad)
    cfg.adx_thr = cfg.adx_min

    # MTF automático
    htf1, htf2 = MTF_MAP.get(cfg.interval, ("1h", "4h"))
    cfg.htf1 = htf1 or cfg.interval
    cfg.htf2 = htf2 or cfg.htf1

    _log_config(cfg)
    return cfg


# ── Helpers de env vars ───────────────────────────────────────────────────────

def _env_int(cfg, env_key: str, attr: str):
    if env_key in os.environ:
        try:
            setattr(cfg, attr, int(os.environ[env_key]))
        except ValueError:
            logger.warning(f"Env var {env_key} inválida: {os.environ[env_key]}")

def _env_float(cfg, env_key: str, attr: str):
    if env_key in os.environ:
        try:
            setattr(cfg, attr, float(os.environ[env_key]))
        except ValueError:
            logger.warning(f"Env var {env_key} inválida: {os.environ[env_key]}")

def _env_str(cfg, env_key: str, attr: str):
    if env_key in os.environ:
        setattr(cfg, attr, os.environ[env_key])

def _env_bool(cfg, env_key: str, attr: str):
    if env_key in os.environ:
        setattr(cfg, attr, os.environ[env_key].lower() == "true")


def _log_config(cfg: BotConfig):
    """Log completo de config al arranque — detecta conflictos entre env y defaults."""
    logger.info("══════════════════ CONFIG ACTIVA ══════════════════")
    logger.info(f"  Preset:          {cfg.preset}")
    logger.info(f"  Interval:        {cfg.interval} | HTF1={cfg.htf1} HTF2={cfg.htf2}")
    logger.info(f"  Pares:           {'dinámico top-' + str(cfg.top_n_symbols) if not cfg.fixed_symbols else str(len(cfg.symbols)) + ' fijos'}")
    logger.info(f"  ADX mínimo:      {cfg.adx_min}  (filtro tendencia)")
    logger.info(f"  RSI BULL:        [{cfg.rsi_bull_lo}-{cfg.rsi_bull_hi}]")
    logger.info(f"  RSI BEAR:        [{cfg.rsi_bear_lo}-{cfg.rsi_bear_hi}]")
    logger.info(f"  SL ATR mult:     {cfg.sl_atr_mult}x  (mín {cfg.sl_min_pct}%)")
    logger.info(f"  R/R mínimo:      {cfg.min_rr}  | TPs: {cfg.rr1}/{cfg.rr2}/{cfg.rr3}/{cfg.rr4}")
    logger.info(f"  Leverage:        {cfg.leverage}x")
    logger.info(f"  Riesgo/trade:    {cfg.max_risk_per_trade_pct}%")
    logger.info(f"  Max posición:    {cfg.max_position_usdt} USDT")
    logger.info(f"  Max trades:      {cfg.max_open_trades}")
    logger.info(f"  Calidad mínima:  {cfg.min_signal_quality}/10")
    logger.info(f"  Auto-trade:      {'SÍ ⚡' if cfg.auto_trade else 'NO (solo señales)'}")
    logger.info(f"  Testnet:         {cfg.bingx_testnet}")
    logger.info(f"  Daily loss lím:  {cfg.max_daily_loss_pct}%")
    logger.info(f"  Drawdown lím:    {cfg.max_drawdown_pct}%")
    logger.info(f"  Funding umbral:  {cfg.funding_threshold}")
    logger.info(f"  Vol percentil:   {cfg.min_volume_percentile}%")
    logger.info("═══════════════════════════════════════════════════")


def config_to_engine(cfg: BotConfig) -> dict:
    return {
        "vwma_len": cfg.vwma_len,
        "ema_fast": cfg.ema_fast,
        "ema_slow": cfg.ema_slow,
        "rsi_len": cfg.rsi_len,
        "atr_len": cfg.atr_len,
        "st_mult": cfg.st_mult,
        "adx_len": cfg.adx_len,
        # Filtros mejorados
        "adx_min": cfg.adx_min,
        "sl_atr_mult": cfg.sl_atr_mult,
        "sl_min_pct": cfg.sl_min_pct,
        "min_rr": cfg.min_rr,
        "rsi_bull_lo": cfg.rsi_bull_lo,
        "rsi_bull_hi": cfg.rsi_bull_hi,
        "rsi_bear_lo": cfg.rsi_bear_lo,
        "rsi_bear_hi": cfg.rsi_bear_hi,
        "cooldown": cfg.cooldown,
        "stop_mode": cfg.stop_mode,
        "stop_atr_mult": cfg.sl_atr_mult,
        "stop_fixed_pct": cfg.stop_fixed_pct,
        "rr1": cfg.rr1,
        "rr2": cfg.rr2,
        "rr3": cfg.rr3,
        "rr4": cfg.rr4,
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
        "post_sl_cooldown_scans": cfg.post_sl_cooldown_scans,
    }
