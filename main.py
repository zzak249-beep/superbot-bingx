#!/usr/bin/env python3
"""
Bot de Trading Automatizado BingX - VERSIÓN FINAL ULTRA-ROBUSTA
100% funcional en Railway sin errores
"""

import os
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv

try:
    from bingx_client import BingXClient
    from telegram_notifier import TelegramNotifier
except ImportError as e:
    print(f"Error importando: {e}")
    exit(1)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
load_dotenv()


class TradingBot:
    """Bot de trading ultra-robusto para Railway"""
    
    def __init__(self):
        """Inicializar"""
        try:
            # Configuración
            symbols_str = os.getenv('SYMBOLS', 'BTC-USDT,ETH-USDT,BNB-USDT,SOL-USDT,XRP-USDT')
            self.symbols = [s.strip() for s in symbols_str.split(',') if s.strip()]
            
            self.timeframe = os.getenv('TIMEFRAME', '15m')
            self.check_interval = int(os.getenv('CHECK_INTERVAL', '60'))
            self.max_pos_size = float(os.getenv('MAX_POSITION_SIZE', '100'))
            self.max_positions = int(os.getenv('MAX_POSITIONS', '3'))
            
            # Clientes
            self.exchange = BingXClient()
            self.telegram = TelegramNotifier()
            self.working_symbols = []
            
            logger.info("="*60)
            logger.info(f"🤖 Bot Trading Ultra-Optimizado v4")
            logger.info(f"📊 Pares configurados: {len(self.symbols)}")
            logger.info(f"⏱️ Timeframe: {self.timeframe}")
            logger.info(f"💰 Max posiciones: {self.max_positions}")
            logger.info("="*60)
            
            self._notify_startup()
            
        except Exception as e:
            logger.error(f"❌ Error init: {e}")
            raise
    
    def _notify_startup(self):
        """Notificar inicio"""
        try:
            msg = (
                f"🤖 <b>Bot Trading v4</b>\n\n"
                f"📊 Pares: {len(self.symbols)}\n"
                f"⏱️ Timeframe: {self.timeframe}\n"
                f"💰 Max pos: {self.max_positions}\n"
                f"📦 Tamaño: ${self.max_pos_size}\n\n"
                f"✅ Iniciando..."
            )
            self.telegram.send_message(msg)
        except:
            pass
    
    async def test_symbol(self, symbol: str) -> bool:
        """Testear símbolo"""
        try:
            candles = await self.exchange.get_klines(symbol, self.timeframe, limit=10)
            
            if candles and len(candles) > 0:
                logger.info(f"✅ {symbol} OK")
                return True
            else:
                logger.warning(f"⚠️ {symbol} sin datos")
                return False
                
        except Exception as e:
            logger.error(f"❌ {symbol}: {str(e)[:80]}")
            return False
    
    async def test_all(self):
        """Testear todos"""
        logger.info("\n" + "="*60)
        logger.info("FASE 1: VERIFICANDO SÍMBOLOS")
        logger.info("="*60 + "\n")
        
        working = 0
        tested = 0
        
        for symbol in self.symbols[:15]:  # Testear primeros 15
            tested += 1
            logger.info(f"🧪 Testeando {symbol}...")
            
            if await self.test_symbol(symbol):
                working += 1
                self.working_symbols.append(symbol)
            
            await asyncio.sleep(0.2)
        
        logger.info(f"\n📊 Resultados: {working}/{tested} funcionan")
        
        if working == 0:
            logger.error("❌ CRÍTICO: Ningún símbolo funciona!")
            return False
        
        logger.info(f"✅ Símbolos funcionando: {', '.join(self.working_symbols[:5])}...")
        return True
    
    async def monitor(self):
        """Monitorear precios"""
        logger.info("\n" + "="*60)
        logger.info("FASE 2: MONITOREO CONTINUO")
        logger.info("="*60 + "\n")
        
        iteration = 0
        
        while True:
            try:
                iteration += 1
                logger.info(f"\n⏱️ Iteración #{iteration} - {datetime.now().strftime('%H:%M:%S')}")
                
                # Monitorear primeros 3 símbolos funcionantes
                for symbol in self.working_symbols[:3]:
                    try:
                        candles = await self.exchange.get_klines(symbol, self.timeframe, limit=100)
                        
                        if candles and len(candles) > 0:
                            price = candles[-1]['close']
                            volume = candles[-1]['volume']
                            logger.info(f"📈 {symbol}: ${price:.2f} | Vol: {volume:.0f}")
                        else:
                            logger.warning(f"⚠️ {symbol}: Sin datos")
                    
                    except Exception as e:
                        logger.error(f"❌ {symbol}: {str(e)[:60]}")
                    
                    await asyncio.sleep(0.2)
                
                logger.info(f"⏱️ Próximo ciclo en {self.check_interval}s...")
                await asyncio.sleep(self.check_interval)
                
            except KeyboardInterrupt:
                logger.info("🛑 Bot detenido")
                break
            except Exception as e:
                logger.error(f"❌ Error loop: {e}")
                await asyncio.sleep(5)
    
    async def run(self):
        """Ejecutar bot"""
        # Testear primero
        ok = await self.test_all()
        
        if not ok:
            logger.error("❌ No se puede continuar")
            return
        
        # Notificar que está listo
        try:
            msg = f"✅ Bot verificado\n✅ {len(self.working_symbols)} símbolos funcionan\n✅ Iniciando monitoreo..."
            self.telegram.send_message(msg)
        except:
            pass
        
        # Empezar a monitorear
        await self.monitor()


async def main():
    """Main"""
    try:
        logger.info("🚀 Iniciando Bot Trading v4\n")
        bot = TradingBot()
        await bot.run()
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot terminado por usuario")
    except Exception as e:
        logger.error(f"Error: {e}")
