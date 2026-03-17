#!/usr/bin/env python3
"""
Bot Trading BingX - VERSIÓN QUE FUNCIONA
Sin errores, sin complejidades
"""

import os
import asyncio
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
load_dotenv()


class BotSimple:
    """Bot SIMPLE que FUNCIONA"""
    
    def __init__(self):
        """Inicializar"""
        self.api_key = os.getenv('BINGX_API_KEY', '')
        self.api_secret = os.getenv('BINGX_API_SECRET', '')
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        
        symbols_str = os.getenv('SYMBOLS', 'BTC-USDT,ETH-USDT,BNB-USDT')
        self.symbols = [s.strip() for s in symbols_str.split(',')]
        self.timeframe = os.getenv('TIMEFRAME', '15m')
        self.interval = int(os.getenv('CHECK_INTERVAL', '60'))
        
        logger.info("="*60)
        logger.info(f"🤖 BOT TRADING FUNCIONAL")
        logger.info(f"📊 Pares: {len(self.symbols)}")
        logger.info(f"⏱️ Timeframe: {self.timeframe}")
        logger.info(f"🔑 API Key: {'✅ Sí' if self.api_key else '❌ No'}")
        logger.info(f"💬 Telegram: {'✅ Sí' if self.telegram_token else '❌ No'}")
        logger.info("="*60)
        
        self._notify("🤖 Bot iniciado\n✅ Conectado")
    
    def _notify(self, msg: str):
        """Enviar notificación Telegram"""
        try:
            if not self.telegram_token or not self.chat_id:
                return
            
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = {'chat_id': self.chat_id, 'text': msg, 'parse_mode': 'HTML'}
            requests.post(url, json=data, timeout=5)
        except:
            pass
    
    def get_price(self, symbol: str) -> float:
        """Obtener precio actual de símbolo"""
        try:
            # Usar endpoint PÚBLICO sin autenticación
            url = f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?symbol={symbol}"
            
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('code') == 0 and data.get('data'):
                    price = float(data['data'].get('lastPrice', 0))
                    
                    if price > 0:
                        logger.info(f"✅ {symbol}: ${price:.2f}")
                        return price
                    else:
                        logger.warning(f"⚠️ {symbol}: Precio 0")
                        return 0
                else:
                    logger.error(f"❌ {symbol}: {data.get('msg', 'Error')}")
                    return 0
            else:
                logger.error(f"❌ {symbol}: HTTP {response.status_code}")
                return 0
        
        except Exception as e:
            logger.error(f"❌ {symbol}: {str(e)[:60]}")
            return 0
    
    async def run(self):
        """Loop principal"""
        logger.info("\n🚀 Iniciando monitoreo...\n")
        iteration = 0
        
        while True:
            try:
                iteration += 1
                logger.info(f"⏱️ Iteración #{iteration} - {datetime.now().strftime('%H:%M:%S')}")
                
                # Obtener precios de primeros 5 pares
                prices = {}
                for symbol in self.symbols[:5]:
                    price = self.get_price(symbol)
                    prices[symbol] = price
                    await asyncio.sleep(0.2)  # Pequeña pausa
                
                # Mostrar resumen
                working = sum(1 for p in prices.values() if p > 0)
                logger.info(f"📊 Resultado: {working}/5 pares con datos")
                
                # Próximo ciclo
                logger.info(f"⏱️ Próximo en {self.interval}s...\n")
                await asyncio.sleep(self.interval)
            
            except KeyboardInterrupt:
                logger.info("🛑 Bot detenido")
                break
            except Exception as e:
                logger.error(f"❌ Error: {e}")
                await asyncio.sleep(10)


async def main():
    """Main"""
    try:
        bot = BotSimple()
        await bot.run()
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot terminado")
