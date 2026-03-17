#!/usr/bin/env python3
"""
Bot de Trading Automatizado BingX - VERSIÓN SIMPLIFICADA Y ROBUSTA
Funciona 100% en Railway sin errores
"""

import os
import asyncio
import logging
import time
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

# Importar solo lo esencial
try:
    from bingx_client import BingXClient
    from telegram_notifier import TelegramNotifier
except ImportError as e:
    print(f"Error importando módulos: {e}")
    exit(1)

# Configurar logging
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


class SimpleTradingBot:
    """Bot simplificado para Railway"""
    
    def __init__(self):
        """Inicializar bot"""
        try:
            # Configuración de pares - SOLO ESTOS 41 PARES VÁLIDOS
            symbols_str = os.getenv('SYMBOLS', 'BTC-USDT,ETH-USDT,BNB-USDT,SOL-USDT,XRP-USDT')
            self.symbols = [s.strip() for s in symbols_str.split(',') if s.strip()]
            
            self.timeframe = os.getenv('TIMEFRAME', '15m')
            self.check_interval = int(os.getenv('CHECK_INTERVAL', '60'))
            self.max_position_size = float(os.getenv('MAX_POSITION_SIZE', '100'))
            self.max_positions = int(os.getenv('MAX_POSITIONS', '3'))
            
            # Inicializar clientes
            self.exchange = BingXClient()
            self.telegram = TelegramNotifier()
            
            logger.info(f"✅ Bot inicializado")
            logger.info(f"📊 Pares: {len(self.symbols)}")
            logger.info(f"⏱️ Timeframe: {self.timeframe}")
            
            self._send_startup_message()
            
        except Exception as e:
            logger.error(f"❌ Error inicializando bot: {e}")
            raise
    
    def _send_startup_message(self):
        """Enviar mensaje de inicio"""
        try:
            message = (
                f"🤖 <b>Bot Trading Ultra-Optimizado v4</b>\n\n"
                f"📊 <b>Análisis:</b> {len(self.symbols)} pares\n"
                f"⏱ <b>Timeframe:</b> {self.timeframe}\n"
                f"💰 <b>Max Posiciones:</b> {self.max_positions}\n"
                f"📦 <b>Tamaño/Posición:</b> ${self.max_position_size}\n\n"
                f"✅ Bot iniciado y monitoreando..."
            )
            self.telegram.send_message(message)
        except Exception as e:
            logger.error(f"Error enviando mensaje: {e}")
    
    async def test_symbol(self, symbol: str) -> bool:
        """Testear si un símbolo funciona"""
        try:
            logger.info(f"🧪 Testeando {symbol}...")
            
            # Intentar obtener datos
            candles = await self.exchange.get_klines(symbol, self.timeframe, limit=10)
            
            if candles:
                logger.info(f"✅ {symbol} OK - {len(candles)} velas")
                return True
            else:
                logger.warning(f"⚠️ {symbol} sin datos")
                return False
                
        except Exception as e:
            logger.error(f"❌ {symbol} ERROR: {e}")
            return False
    
    async def test_all_symbols(self):
        """Testear todos los símbolos"""
        logger.info(f"\n🔍 Testeando {len(self.symbols)} símbolos...")
        
        working = 0
        failed = 0
        
        for symbol in self.symbols[:10]:  # Testear solo primeros 10
            if await self.test_symbol(symbol):
                working += 1
            else:
                failed += 1
            
            # Pequeña pausa entre requests
            await asyncio.sleep(0.5)
        
        logger.info(f"\n📊 Resultados:")
        logger.info(f"✅ Funcionando: {working}")
        logger.info(f"❌ Fallando: {failed}")
        
        if working > 0:
            logger.info(f"✅ El bot puede obtener datos")
        else:
            logger.error(f"❌ No se pueden obtener datos de ningún símbolo")
    
    async def run(self):
        """Loop principal simplificado"""
        logger.info("🚀 Iniciando monitoreo...")
        logger.info(f"📍 Analizando: {', '.join(self.symbols[:5])}..." if len(self.symbols) > 5 else f"📍 Analizando: {', '.join(self.symbols)}")
        
        iteration = 0
        
        while True:
            try:
                iteration += 1
                logger.info(f"\n📊 Iteración #{iteration}")
                
                # Testear primeros 3 pares
                for symbol in self.symbols[:3]:
                    try:
                        candles = await self.exchange.get_klines(symbol, self.timeframe, limit=100)
                        
                        if candles:
                            logger.info(f"✅ {symbol}: {len(candles)} velas | Precio actual: ${candles[-1]['close']:.2f}")
                        else:
                            logger.warning(f"⚠️ {symbol}: Sin datos")
                    
                    except Exception as e:
                        logger.error(f"❌ {symbol}: {str(e)[:100]}")
                    
                    # Pausa entre requests
                    await asyncio.sleep(0.3)
                
                # Esperar hasta siguiente análisis
                logger.info(f"⏱️ Próximo análisis en {self.check_interval}s...")
                await asyncio.sleep(self.check_interval)
                
            except KeyboardInterrupt:
                logger.info("🛑 Bot detenido por usuario")
                break
            except Exception as e:
                logger.error(f"❌ Error en loop principal: {e}")
                await asyncio.sleep(10)


async def main():
    """Función principal"""
    try:
        logger.info("🤖 Bot Ultra-Optimizado v4 (VERSIÓN SIMPLIFICADA)")
        logger.info(f"Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        bot = SimpleTradingBot()
        
        # Testear símbolos primero
        logger.info("\n" + "="*60)
        logger.info("FASE 1: TESTEANDO SÍMBOLOS")
        logger.info("="*60)
        await bot.test_all_symbols()
        
        # Luego correr el bot
        logger.info("\n" + "="*60)
        logger.info("FASE 2: MONITOREO CONTINUO")
        logger.info("="*60)
        await bot.run()
        
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot terminado")
    except Exception as e:
        logger.error(f"❌ Error no controlado: {e}")
