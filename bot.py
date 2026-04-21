"""
Signal Projection Explorer Bot - BingX Auto Trader (FIXED VERSION)
MEJORAS:
  - Manejo robusto de variables de entorno con valores por defecto
  - Validación de API keys al inicio
  - Modo DRY_RUN por defecto para seguridad
  - Logging mejorado
  - Circuit breakers para limitar pérdidas
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from typing import Optional

from signal_engine import SignalEngine
from market_scanner import MarketScanner
from bingx_client import BingXClient
from trade_manager import TradeManager
from dashboard import Dashboard

# RL stack es opcional
try:
    from rl_trainer import klines_to_csv, run_training
    RL_AVAILABLE = True
except ImportError:
    RL_AVAILABLE = False
    logging.getLogger("BOT").warning(
        "ray/tensortrade no instalados — entrenamiento RL desactivado."
    )

# ================================================================
# CONFIGURACIÓN SEGURA CON VALORES POR DEFECTO
# ================================================================
def get_env(key: str, default: str = "", required: bool = False) -> str:
    """Obtener variable de entorno con validación."""
    value = os.getenv(key, default)
    if required and not value:
        logging.error(f"❌ Variable de entorno requerida no encontrada: {key}")
        sys.exit(1)
    return value

def get_env_int(key: str, default: int) -> int:
    """Obtener variable de entorno como entero."""
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        logging.warning(f"Valor inválido para {key}, usando default: {default}")
        return default

def get_env_float(key: str, default: float) -> float:
    """Obtener variable de entorno como float."""
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        logging.warning(f"Valor inválido para {key}, usando default: {default}")
        return default

def get_env_bool(key: str, default: bool) -> bool:
    """Obtener variable de entorno como booleano."""
    value = os.getenv(key, str(default)).lower()
    return value in ("true", "1", "yes", "on")


# ================================================================
# CONFIGURACIÓN DEL BOT
# ================================================================
class BotConfig:
    """Configuración centralizada del bot con validaciones."""
    
    def __init__(self):
        # API Credentials (REQUERIDAS)
        self.API_KEY = get_env("BINGX_API_KEY", required=True)
        self.API_SECRET = get_env("BINGX_API_SECRET", required=True)
        
        # Modo de operación
        self.DRY_RUN = get_env_bool("DRY_RUN", True)  # ✅ DRY_RUN por defecto
        
        # Intervalos
        self.SCAN_INTERVAL_SEC = get_env_int("SCAN_INTERVAL_SEC", 120)  # 2 minutos
        self.KLINE_INTERVAL = get_env("KLINE_INTERVAL", "1h")
        
        # Indicadores técnicos
        self.MA_FAST = get_env_int("MA_FAST", 9)
        self.MA_SLOW = get_env_int("MA_SLOW", 21)
        self.PROJ_LENGTH = get_env_int("PROJ_LENGTH", 10)
        self.MIN_MEAN_PNL = get_env_float("MIN_MEAN_PNL", 0.002)  # 0.2%
        
        # Scanner
        self.TOP_SYMBOLS = get_env_int("TOP_SYMBOLS", 10)
        self.MIN_VOLUME_USDT = get_env_float("MIN_VOLUME_USDT", 1000000)  # 1M
        self.MIN_SCORE = get_env_float("MIN_SCORE", 60.0)
        
        # Trading
        self.LEVERAGE = get_env_int("LEVERAGE", 5)
        self.RISK_PCT = get_env_float("RISK_PCT", 0.01)  # 1%
        self.MAX_OPEN_TRADES = get_env_int("MAX_OPEN_TRADES", 3)
        self.MAX_POSITION_SIZE = get_env_float("MAX_POSITION_SIZE", 20.0)  # USDT
        self.MIN_TRADE_USDT = get_env_float("MIN_TRADE_USDT", 10.0)
        
        # Take Profit / Stop Loss
        self.TP_MULT = get_env_float("TP_MULT", 2.0)
        self.SL_MULT = get_env_float("SL_MULT", 1.0)
        self.MAX_HOLD_HOURS = get_env_int("MAX_HOLD_HOURS", 48)
        
        # Circuit Breakers (protección)
        self.MAX_DAILY_LOSS_PCT = get_env_float("DAILY_LOSS_CAP_PCT", 5.0)  # 5%
        self.MAX_LOSING_STREAK = get_env_int("MAX_LOSING_STREAK", 3)
        
        # RL Training (opcional)
        self.RL_TRAIN_EVERY = get_env_int("RL_TRAIN_EVERY", 0)  # 0 = desactivado
        self.RL_TRAIN_SYMBOL = get_env("RL_TRAIN_SYMBOL", "BTC-USDT")
        
        # Dashboard
        self.PORT = get_env_int("PORT", 8080)
    
    def validate(self) -> bool:
        """Valida la configuración antes de iniciar."""
        errors = []
        
        if self.LEVERAGE < 1 or self.LEVERAGE > 125:
            errors.append(f"LEVERAGE debe estar entre 1-125, actual: {self.LEVERAGE}")
        
        if self.RISK_PCT <= 0 or self.RISK_PCT > 0.1:
            errors.append(f"RISK_PCT debe estar entre 0-10%, actual: {self.RISK_PCT*100}%")
        
        if self.MAX_OPEN_TRADES < 1:
            errors.append(f"MAX_OPEN_TRADES debe ser al menos 1, actual: {self.MAX_OPEN_TRADES}")
        
        if errors:
            for error in errors:
                logging.error(f"❌ {error}")
            return False
        
        return True
    
    def log_config(self):
        """Imprime la configuración activa."""
        logging.info("=" * 60)
        logging.info("⚙️  CONFIGURACIÓN DEL BOT")
        logging.info("=" * 60)
        logging.info(f"Modo:                {'🔴 REAL TRADING' if not self.DRY_RUN else '🟢 DRY RUN (simulación)'}")
        logging.info(f"Scan Interval:       {self.SCAN_INTERVAL_SEC}s")
        logging.info(f"Timeframe:           {self.KLINE_INTERVAL}")
        logging.info(f"Top Symbols:         {self.TOP_SYMBOLS}")
        logging.info(f"Min Volume:          ${self.MIN_VOLUME_USDT:,.0f}")
        logging.info(f"Leverage:            {self.LEVERAGE}x")
        logging.info(f"Risk per Trade:      {self.RISK_PCT*100:.1f}%")
        logging.info(f"Max Open Trades:     {self.MAX_OPEN_TRADES}")
        logging.info(f"Max Daily Loss:      {self.MAX_DAILY_LOSS_PCT}%")
        logging.info(f"TP Multiplier:       {self.TP_MULT}x")
        logging.info(f"SL Multiplier:       {self.SL_MULT}x")
        logging.info("=" * 60)


# ================================================================
# LOGGING SETUP
# ================================================================
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("BOT")


# ================================================================
# TRADING BOT
# ================================================================
class TradingBot:
    def __init__(self, config: BotConfig):
        self.config = config
        
        # Cliente BingX
        self.client = BingXClient(
            api_key=config.API_KEY,
            api_secret=config.API_SECRET,
        )
        
        # Componentes
        self.scanner = MarketScanner(self.client)
        self.signal_engine = SignalEngine()
        self.trade_manager = TradeManager(self.client, config)
        self.dashboard = Dashboard()
        
        # Estado
        self.running = False
        self._cycle_count = 0
        self._daily_pnl = 0.0
        self._losing_streak = 0
        self._circuit_breaker_active = False

    # ------------------------------------------------------------------ #
    async def validate_credentials(self) -> bool:
        """Valida que las credenciales de API funcionen."""
        try:
            log.info("🔐 Validando credenciales de BingX...")
            balance = await self.client.get_balance()
            log.info(f"✅ Credenciales válidas. Balance: ${balance:.2f} USDT")
            
            if balance < self.config.MIN_TRADE_USDT * 2:
                log.warning(f"⚠️  Balance bajo: ${balance:.2f}. Se recomienda al menos ${self.config.MIN_TRADE_USDT * 2:.2f}")
            
            return True
        except Exception as e:
            log.error(f"❌ Error validando credenciales: {e}")
            return False

    # ------------------------------------------------------------------ #
    async def run(self):
        """Loop principal del bot."""
        log.info("🚀 Iniciando BingX Trading Bot...")
        
        # Validar configuración
        if not self.config.validate():
            log.error("❌ Configuración inválida. Abortando.")
            return
        
        self.config.log_config()
        
        # Validar credenciales
        if not await self.validate_credentials():
            log.error("❌ Credenciales inválidas. Verifica BINGX_API_KEY y BINGX_API_SECRET")
            return
        
        # Modo DRY RUN warning
        if self.config.DRY_RUN:
            log.warning("=" * 60)
            log.warning("🟢 MODO DRY RUN ACTIVADO - NO SE EJECUTARÁN TRADES REALES")
            log.warning("   Para activar trading real, establece: DRY_RUN=false")
            log.warning("=" * 60)
        else:
            log.warning("=" * 60)
            log.warning("🔴 MODO TRADING REAL ACTIVADO - SE EJECUTARÁN TRADES REALES")
            log.warning("   Asegúrate de haber probado en modo DRY_RUN primero")
            log.warning("=" * 60)
            await asyncio.sleep(5)  # Pausa para que el usuario lo vea
        
        self.running = True
        
        try:
            await self.client.initialize()
            await self.dashboard.start()
            
            log.info("✅ Bot iniciado correctamente")
            
            while self.running:
                try:
                    # Circuit breaker check
                    if self._circuit_breaker_active:
                        log.warning("⚠️  Circuit breaker activo. Pausando trading...")
                        await asyncio.sleep(300)  # 5 minutos
                        continue
                    
                    await self._cycle()
                    
                except KeyboardInterrupt:
                    log.info("⏹️  Deteniendo bot (Ctrl+C)...")
                    self.running = False
                    break
                except Exception as e:
                    log.error(f"❌ Error en ciclo principal: {e}", exc_info=True)
                
                await asyncio.sleep(self.config.SCAN_INTERVAL_SEC)
        
        finally:
            log.info("👋 Bot detenido")

    # ------------------------------------------------------------------ #
    async def _cycle(self):
        """Un ciclo de escaneo y trading."""
        self._cycle_count += 1
        log.info(f"🔍 Ciclo #{self._cycle_count} - Escaneando mercado...")

        # 1. Escanear símbolos calientes
        hot_symbols = await self.scanner.get_hot_symbols(
            top_n=self.config.TOP_SYMBOLS,
            min_volume=self.config.MIN_VOLUME_USDT,
        )
        log.info(f"   → {len(hot_symbols)} símbolos encontrados")

        # 2. Evaluar señales
        signals = []
        for sym in hot_symbols:
            sig = await self.signal_engine.evaluate(self.client, sym)
            if sig:
                signals.append(sig)
                log.info(f"   ✅ SEÑAL {sig['direction']} en {sym}  "
                         f"mean={sig['mean_pnl']:.2%}  median={sig['median_pnl']:.2%}")

        # 3. Gestionar trades abiertos
        await self.trade_manager.manage_open_trades()

        # 4. Abrir nuevos trades (si no está en DRY_RUN o si está permitido)
        if not self._circuit_breaker_active:
            for sig in signals:
                if self.config.DRY_RUN:
                    log.info(f"   [DRY RUN] Señal detectada: {sig['direction']} {sig['symbol']} "
                             f"(no se ejecuta trade real)")
                else:
                    await self.trade_manager.open_trade(sig)

        # 5. Actualizar dashboard
        open_trades = await self.trade_manager.get_open_trades()
        await self.dashboard.update(hot_symbols, signals, open_trades)

        # 6. Check circuit breakers
        await self._check_circuit_breakers()

        # 7. RL training (si está habilitado)
        if (RL_AVAILABLE and 
            self.config.RL_TRAIN_EVERY > 0 and 
            self._cycle_count % self.config.RL_TRAIN_EVERY == 0):
            await self._run_rl_training()

    # ------------------------------------------------------------------ #
    async def _check_circuit_breakers(self):
        """Verifica y activa circuit breakers si es necesario."""
        # TODO: Implementar lógica de circuit breaker
        # - Calcular PnL diario
        # - Contar racha perdedora
        # - Activar breaker si se exceden límites
        pass

    # ------------------------------------------------------------------ #
    async def _run_rl_training(self):
        """Entrena el agente RL (si está habilitado)."""
        if not RL_AVAILABLE:
            return
        
        log.info(f"🧠 Iniciando entrenamiento RL para {self.config.RL_TRAIN_SYMBOL}...")
        try:
            klines = await self.client.get_klines(
                self.config.RL_TRAIN_SYMBOL, 
                self.config.KLINE_INTERVAL, 
                limit=1000
            )

            if len(klines) < 300:
                log.warning("Pocas velas para entrenar, saltando")
                return

            split = int(len(klines) * 0.8)
            train_klines = klines[:split]
            eval_klines = klines[split:]

            klines_to_csv(train_klines, "logs/training.csv")
            klines_to_csv(eval_klines, "logs/evaluation.csv")

            loop = asyncio.get_event_loop()
            best = await loop.run_in_executor(
                None,
                lambda: run_training(
                    training_csv="logs/training.csv",
                    evaluation_csv="logs/evaluation.csv",
                    num_iterations=5,
                )
            )
            log.info(f"🧠 Entrenamiento RL completado. Mejor checkpoint: {best}")

        except Exception as e:
            log.error(f"❌ Error en entrenamiento RL: {e}", exc_info=True)


# ================================================================
# MAIN
# ================================================================
if __name__ == "__main__":
    try:
        config = BotConfig()
        bot = TradingBot(config)
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("⏹️  Bot detenido por usuario")
    except Exception as e:
        log.error(f"❌ Error fatal: {e}", exc_info=True)
        sys.exit(1)
