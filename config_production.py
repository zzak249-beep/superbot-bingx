"""
CONFIGURACIÓN PARA DINERO REAL — CONFLUX 4 PRO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️  PARÁMETROS OPTIMIZADOS PARA RENTABILIDAD REAL
⚠️  RIESGO CONTROLADO - ANTI-RUINA
⚠️  BACKTESTING OBLIGATORIO ANTES DE ACTIVAR

MEJORAS vs CONFIG ORIGINAL:
  ✓ Leverage reducido 10x → 3-5x (más seguro)
  ✓ Calidad mínima 4 → 7 (solo señales excelentes)
  ✓ Stop loss obligatorio en TODAS las posiciones
  ✓ Riesgo por trade 1.5% → 1% (más conservador)
  ✓ Max drawdown 15% → 10% (circuit breaker más estricto)
  ✓ Integración Fear & Greed de Taleb
  ✓ Backtesting obligatorio
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
from dataclasses import dataclass, field
from typing import List

# ═══════════════════════════════════════════════════════════════
# PRESETS OPTIMIZADOS PARA DINERO REAL
# ═══════════════════════════════════════════════════════════════

PRODUCTION_PRESETS = {
    "Conservative": {
        # Para principiantes o capital <$5k
        "cooldown": 8,
        "use_adx": True, "adx_thr": 20,
        "stop_mode": "Supertrend",
        "stop_atr_mult": 2.0, "stop_fixed_pct": 0.5,
        "rr1": 0.75, "rr2": 1.5, "rr3": 2.5, "rr4": 4.0,
        "leverage": 3,
        "max_risk_per_trade_pct": 0.5,  # 0.5% por trade
        "max_daily_loss_pct": 1.5,
        "max_drawdown_pct": 8.0,
        "min_signal_quality": 8,  # CRÍTICO: solo señales 8+
        "rsi_bull": 55, "rsi_bear": 45,
    },
    
    "Balanced": {
        # Para capital $5k-$20k
        "cooldown": 6,
        "use_adx": True, "adx_thr": 18,
        "stop_mode": "Supertrend",
        "stop_atr_mult": 1.8, "stop_fixed_pct": 0.4,
        "rr1": 0.5, "rr2": 1.5, "rr3": 2.5, "rr4": 3.5,
        "leverage": 4,
        "max_risk_per_trade_pct": 1.0,  # 1% por trade
        "max_daily_loss_pct": 2.5,
        "max_drawdown_pct": 10.0,
        "min_signal_quality": 7,
        "rsi_bull": 53, "rsi_bear": 47,
    },
    
    "Aggressive": {
        # Para traders experimentados con capital >$20k
        "cooldown": 5,
        "use_adx": True, "adx_thr": 16,
        "stop_mode": "Supertrend",
        "stop_atr_mult": 1.5, "stop_fixed_pct": 0.3,
        "rr1": 0.5, "rr2": 1.0, "rr3": 2.0, "rr4": 3.0,
        "leverage": 5,
        "max_risk_per_trade_pct": 1.5,  # 1.5% por trade
        "max_daily_loss_pct": 3.0,
        "max_drawdown_pct": 12.0,
        "min_signal_quality": 7,
        "rsi_bull": 52, "rsi_bear": 48,
    },
}


@dataclass
class ProductionConfig:
    """Configuración para dinero real con salvaguardas."""
    
    # ═══════════════════════════════════════════════════════════
    # CONEXIONES
    # ═══════════════════════════════════════════════════════════
    telegram_token: str = ""
    telegram_chat_id: str = ""
    bingx_api_key: str = ""
    bingx_secret: str = ""
    bingx_testnet: bool = True  # ¡SIEMPRE empezar en testnet!
    
    # ═══════════════════════════════════════════════════════════
    # MODO DE OPERACIÓN
    # ═══════════════════════════════════════════════════════════
    auto_trade: bool = False  # ¡SIEMPRE false hasta backtest OK!
    dry_run: bool = True      # ¡SIEMPRE true hasta backtest OK!
    backtest_required: bool = True  # Obligatorio antes de activar
    min_backtest_winrate: float = 55.0  # Min 55% WR en backtest
    min_backtest_profit_factor: float = 1.3  # Min PF 1.3
    
    # ═══════════════════════════════════════════════════════════
    # SCANNER DINÁMICO
    # ═══════════════════════════════════════════════════════════
    fixed_symbols: List[str] = field(default_factory=list)
    top_n_symbols: int = 30  # Reducido de 50 → 30 para mejor calidad
    min_volume_usdt: float = 10_000_000  # 10M min (más líquido)
    symbol_refresh_hours: int = 6  # Refresh cada 6h
    
    # ═══════════════════════════════════════════════════════════
    # TIMEFRAME
    # ═══════════════════════════════════════════════════════════
    interval: str = "15m"  # 15m es óptimo para daytrading
    kline_limit: int = 500  # Más datos históricos
    scan_seconds: int = 45  # Scan cada 45s (balance velocidad/API)
    
    # ═══════════════════════════════════════════════════════════
    # PRESET
    # ═══════════════════════════════════════════════════════════
    preset: str = "Balanced"  # Default: Balanced
    
    # ═══════════════════════════════════════════════════════════
    # INDICADORES
    # ═══════════════════════════════════════════════════════════
    vwma_len: int = 100
    ema_fast: int = 21
    ema_slow: int = 50
    rsi_len: int = 14
    rsi_bull: int = 53
    rsi_bear: int = 47
    atr_len: int = 14  # ATR más suavizado
    st_mult: float = 3.0  # Supertrend más conservador
    use_adx: bool = True  # ¡SIEMPRE usar ADX en producción!
    adx_len: int = 14
    adx_thr: int = 18
    
    # ═══════════════════════════════════════════════════════════
    # SEÑAL
    # ═══════════════════════════════════════════════════════════
    cooldown: int = 6
    stop_mode: str = "Supertrend"
    stop_atr_mult: float = 1.8
    stop_fixed_pct: float = 0.4
    rr1: float = 0.5
    rr2: float = 1.5
    rr3: float = 2.5
    rr4: float = 3.5
    
    # ═══════════════════════════════════════════════════════════
    # FILTROS
    # ═══════════════════════════════════════════════════════════
    use_mtf: bool = True
    min_volume_percentile: int = 40  # Más estricto (antes 20)
    funding_threshold: float = 0.03  # Más estricto (antes 0.05)
    
    # ═══════════════════════════════════════════════════════════
    # GESTIÓN DE RIESGO — LO MÁS IMPORTANTE
    # ═══════════════════════════════════════════════════════════
    starting_balance: float = 1000.0
    leverage: int = 4  # MAX 5x para dinero real
    max_risk_per_trade_pct: float = 1.0  # 1% máximo
    max_position_usdt: float = 500.0
    max_open_trades: int = 2  # Más conservador (antes 3)
    max_daily_loss_pct: float = 2.5  # Circuit breaker diario
    max_weekly_loss_pct: float = 6.0  # Circuit breaker semanal
    max_drawdown_pct: float = 10.0  # STOP TOTAL si DD > 10%
    min_signal_quality: int = 7  # ¡CRÍTICO! Solo señales 7+
    
    # ═══════════════════════════════════════════════════════════
    # HORARIOS
    # ═══════════════════════════════════════════════════════════
    use_session_filter: bool = True
    avoid_hours_utc: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 22, 23])
    
    # ═══════════════════════════════════════════════════════════
    # FEAR & GREED (TALEB)
    # ═══════════════════════════════════════════════════════════
    enable_fear_strategy: bool = True
    min_panic_score_buy: int = 65  # Comprar si pánico > 65
    max_greed_score_sell: int = -65  # Vender si euforia < -65
    fear_max_risk_pct: float = 1.0  # Max 1% en trades anti-pánico
    panic_cooldown_hours: int = 8  # 8h entre panic trades
    
    # ═══════════════════════════════════════════════════════════
    # REPORTING
    # ═══════════════════════════════════════════════════════════
    dashboard_every_n_scans: int = 40  # Dashboard cada 30 min
    
    # MTF (calculado automáticamente)
    htf1: str = "1h"
    htf2: str = "4h"
    symbols: List[str] = field(default_factory=lambda: ["BTC-USDT", "ETH-USDT"])


def load_production_config() -> ProductionConfig:
    """Carga configuración desde variables de entorno."""
    cfg = ProductionConfig()
    
    # Conexiones (OBLIGATORIAS)
    cfg.telegram_token = os.environ.get("TELEGRAM_TOKEN", "")
    cfg.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    cfg.bingx_api_key = os.environ.get("BINGX_API_KEY", "")
    cfg.bingx_secret = os.environ.get("BINGX_SECRET", "")
    
    if not cfg.telegram_token or not cfg.telegram_chat_id:
        raise ValueError("❌ TELEGRAM_TOKEN y TELEGRAM_CHAT_ID son obligatorios")
    
    # Modo de operación
    cfg.bingx_testnet = os.environ.get("BINGX_TESTNET", "true").lower() == "true"
    cfg.auto_trade = os.environ.get("AUTO_TRADE", "false").lower() == "true"
    cfg.dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
    
    # ⚠️  SALVAGUARDA CRÍTICA
    if cfg.auto_trade and cfg.dry_run:
        raise ValueError("❌ AUTO_TRADE=true requiere DRY_RUN=false")
    
    if cfg.auto_trade and not cfg.bingx_api_key:
        raise ValueError("❌ AUTO_TRADE=true requiere BINGX_API_KEY")
    
    # Scanner
    if "SYMBOLS" in os.environ:
        cfg.fixed_symbols = [s.strip() for s in os.environ["SYMBOLS"].split(",") if s.strip()]
        cfg.symbols = cfg.fixed_symbols
    if "TOP_N_SYMBOLS" in os.environ:
        cfg.top_n_symbols = int(os.environ["TOP_N_SYMBOLS"])
    if "MIN_VOLUME_USDT" in os.environ:
        cfg.min_volume_usdt = float(os.environ["MIN_VOLUME_USDT"])
    
    # Timeframe
    if "INTERVAL" in os.environ:
        cfg.interval = os.environ["INTERVAL"]
    if "SCAN_SECONDS" in os.environ:
        cfg.scan_seconds = int(os.environ["SCAN_SECONDS"])
    
    # Preset
    if "PRESET" in os.environ:
        cfg.preset = os.environ["PRESET"]
    
    # Aplicar preset
    if cfg.preset in PRODUCTION_PRESETS:
        preset = PRODUCTION_PRESETS[cfg.preset]
        for k, v in preset.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    
    # Risk (variables de entorno tienen prioridad)
    if "STARTING_BALANCE" in os.environ:
        cfg.starting_balance = float(os.environ["STARTING_BALANCE"])
    if "LEVERAGE" in os.environ:
        leverage = int(os.environ["LEVERAGE"])
        if leverage > 5:
            raise ValueError(f"❌ LEVERAGE={leverage} es PELIGROSO. Max permitido: 5x")
        cfg.leverage = leverage
    if "MAX_RISK_PER_TRADE_PCT" in os.environ:
        cfg.max_risk_per_trade_pct = float(os.environ["MAX_RISK_PER_TRADE_PCT"])
    if "MAX_DRAWDOWN_PCT" in os.environ:
        cfg.max_drawdown_pct = float(os.environ["MAX_DRAWDOWN_PCT"])
    if "MIN_SIGNAL_QUALITY" in os.environ:
        quality = int(os.environ["MIN_SIGNAL_QUALITY"])
        if quality < 6:
            raise ValueError(f"❌ MIN_SIGNAL_QUALITY={quality} muy bajo. Mínimo: 6")
        cfg.min_signal_quality = quality
    
    # MTF automático
    mtf_map = {
        "1m": ("5m", "15m"),
        "5m": ("15m", "1h"),
        "15m": ("1h", "4h"),
        "30m": ("1h", "4h"),
        "1h": ("4h", "1d"),
        "4h": ("1d", None),
        "1d": (None, None),
    }
    htf1, htf2 = mtf_map.get(cfg.interval, ("1h", "4h"))
    cfg.htf1 = htf1 or cfg.interval
    cfg.htf2 = htf2 or cfg.htf1
    
    return cfg


def config_to_engine(cfg: ProductionConfig) -> dict:
    """Convierte config a dict para Conflux4Engine."""
    return {
        "vwma_len": cfg.vwma_len,
        "ema_fast": cfg.ema_fast,
        "ema_slow": cfg.ema_slow,
        "rsi_len": cfg.rsi_len,
        "rsi_bull": cfg.rsi_bull,
        "rsi_bear": cfg.rsi_bear,
        "atr_len": cfg.atr_len,
        "st_mult": cfg.st_mult,
        "use_adx": cfg.use_adx,
        "adx_len": cfg.adx_len,
        "adx_thr": cfg.adx_thr,
        "cooldown": cfg.cooldown,
        "stop_mode": cfg.stop_mode,
        "stop_atr_mult": cfg.stop_atr_mult,
        "stop_fixed_pct": cfg.stop_fixed_pct,
        "rr1": cfg.rr1,
        "rr2": cfg.rr2,
        "rr3": cfg.rr3,
        "rr4": cfg.rr4,
        "min_volume_percentile": cfg.min_volume_percentile,
        "funding_threshold": cfg.funding_threshold,
    }


def config_to_risk(cfg: ProductionConfig) -> dict:
    """Convierte config a dict para RiskManager."""
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


def config_to_fear(cfg: ProductionConfig) -> dict:
    """Convierte config a dict para TalebFearEngine."""
    return {
        "enable_fear_strategy": cfg.enable_fear_strategy,
        "min_panic_score_buy": cfg.min_panic_score_buy,
        "max_greed_score_sell": cfg.max_greed_score_sell,
        "fear_max_risk_pct": cfg.fear_max_risk_pct,
        "panic_cooldown_hours": cfg.panic_cooldown_hours,
    }


def validate_production_config(cfg: ProductionConfig):
    """Valida que la configuración sea segura para dinero real."""
    errors = []
    warnings = []
    
    # Errores críticos (bloquean ejecución)
    if cfg.leverage > 5:
        errors.append(f"❌ Leverage {cfg.leverage}x es PELIGROSO. Máximo: 5x")
    
    if cfg.min_signal_quality < 6:
        errors.append(f"❌ Calidad mínima {cfg.min_signal_quality} muy baja. Mínimo: 6")
    
    if cfg.max_risk_per_trade_pct > 2.0:
        errors.append(f"❌ Riesgo {cfg.max_risk_per_trade_pct}% muy alto. Máximo: 2%")
    
    if cfg.max_drawdown_pct > 15:
        errors.append(f"❌ Max drawdown {cfg.max_drawdown_pct}% muy alto. Máximo: 15%")
    
    if cfg.auto_trade and cfg.backtest_required:
        errors.append("❌ AUTO_TRADE requiere backtesting completo primero")
    
    # Warnings (no bloquean pero alertan)
    if cfg.leverage > 3 and cfg.starting_balance < 5000:
        warnings.append(f"⚠️  Leverage {cfg.leverage}x con ${cfg.starting_balance} es arriesgado")
    
    if cfg.max_open_trades > 3:
        warnings.append(f"⚠️  {cfg.max_open_trades} posiciones simultáneas aumenta exposición")
    
    if not cfg.use_adx:
        warnings.append("⚠️  ADX desactivado - menor filtrado de señales")
    
    if cfg.min_volume_percentile < 30:
        warnings.append(f"⚠️  Vol percentile {cfg.min_volume_percentile} bajo - posible baja liquidez")
    
    # Mostrar resultados
    if errors:
        print("\n" + "="*60)
        print("❌ ERRORES CRÍTICOS - CONFIGURACIÓN NO SEGURA")
        print("="*60)
        for e in errors:
            print(e)
        raise ValueError("Configuración no válida para dinero real")
    
    if warnings:
        print("\n" + "="*60)
        print("⚠️  ADVERTENCIAS")
        print("="*60)
        for w in warnings:
            print(w)
        print("="*60 + "\n")
    
    print("✅ Configuración validada - SEGURA para dinero real\n")


if __name__ == "__main__":
    # Test
    cfg = load_production_config()
    validate_production_config(cfg)
    
    print("Configuración cargada:")
    print(f"  Preset: {cfg.preset}")
    print(f"  Leverage: {cfg.leverage}x")
    print(f"  Riesgo por trade: {cfg.max_risk_per_trade_pct}%")
    print(f"  Calidad mínima: {cfg.min_signal_quality}/10")
    print(f"  Max drawdown: {cfg.max_drawdown_pct}%")
    print(f"  Auto-trade: {cfg.auto_trade}")
    print(f"  Testnet: {cfg.bingx_testnet}")
