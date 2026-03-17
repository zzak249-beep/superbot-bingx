#!/usr/bin/env python3
"""
Bot Trading BingX - ANALIZA TODOS LOS PARES
Sin límite de 5, procesa cada símbolo configurado
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


class BotTodosPares:
    """Bot que ANALIZA TODOS LOS PARES"""
    
    def __init__(self):
        """Inicializar"""
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        
        # TODOS los pares - SIN LÍMITE DE 5
        symbols_str = os.getenv('SYMBOLS', 'BTC-USDT,ETH-USDT,BNB-USDT')
        self.symbols = [s.strip() for s in symbols_str.split(',')]
        
        self.timeframe = os.getenv('TIMEFRAME', '15m')
        self.interval = int(os.getenv('CHECK_INTERVAL', '60'))
        
        logger.info("="*60)
        logger.info(f"🤖 BOT TRADING - ANALIZA TODOS LOS PARES")
        logger.info(f"📊 TOTAL DE PARES: {len(self.symbols)}")
        logger.info(f"⏱️ Timeframe: {self.timeframe}")
        logger.info(f"💬 Telegram: {'✅ Sí' if self.telegram_token else '❌ No'}")
        logger.info("="*60)
        logger.info(f"\n📋 Pares a analizar:")
        for i, symbol in enumerate(self.symbols, 1):
            logger.info(f"   {i}. {symbol}")
        logger.info("\n")
        
        self._notify(f"🤖 Bot iniciado\n📊 Analizando {len(self.symbols)} pares\n✅ Conectado")
    
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
    
    def get_price(self, symbol: str) -> dict:
        """Obtener precio y datos del símbolo"""
        try:
            # Endpoint público
            url = f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?symbol={symbol}"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('code') == 0 and data.get('data'):
                    ticker = data['data']
                    price = float(ticker.get('lastPrice', 0))
                    
                    if price > 0:
                        return {
                            'symbol': symbol,
                            'price': price,
                            'high': float(ticker.get('highPrice', 0)),
                            'low': float(ticker.get('lowPrice', 0)),
                            'volume': float(ticker.get('volume', 0)),
                            'change': float(ticker.get('priceChangePercent', 0)),
                            'status': 'OK'
                        }
                    else:
                        return {'symbol': symbol, 'price': 0, 'status': 'ERROR - Precio 0'}
                else:
                    return {'symbol': symbol, 'price': 0, 'status': f"ERROR - {data.get('msg', 'API error')}"}
            else:
                return {'symbol': symbol, 'price': 0, 'status': f'ERROR - HTTP {response.status_code}'}
        
        except Exception as e:
            return {'symbol': symbol, 'price': 0, 'status': f'ERROR - {str(e)[:40]}'}
    
    async def run(self):
        """Loop principal"""
        logger.info("🚀 Iniciando monitoreo de TODOS los pares...\n")
        iteration = 0
        
        while True:
            try:
                iteration += 1
                logger.info(f"\n{'='*60}")
                logger.info(f"⏱️ ITERACIÓN #{iteration} - {datetime.now().strftime('%H:%M:%S')}")
                logger.info(f"{'='*60}")
                
                # ANALIZAR TODOS LOS PARES
                results = []
                working = 0
                failed = 0
                
                logger.info(f"\n📊 Analizando {len(self.symbols)} pares...\n")
                
                for i, symbol in enumerate(self.symbols, 1):
                    result = self.get_price(symbol)
                    results.append(result)
                    
                    if result['status'] == 'OK':
                        logger.info(f"{i:2d}. ✅ {symbol:12} ${result['price']:12,.2f} | "
                                  f"24h: {result['change']:+6.2f}% | Vol: {result['volume']:.0f}")
                        working += 1
                    else:
                        logger.info(f"{i:2d}. ❌ {symbol:12} {result['status']}")
                        failed += 1
                    
                    # Pequeña pausa entre requests
                    await asyncio.sleep(0.1)
                
                # RESUMEN
                logger.info(f"\n{'='*60}")
                logger.info(f"📊 RESUMEN:")
                logger.info(f"   ✅ Pares funcionando: {working}/{len(self.symbols)}")
                logger.info(f"   ❌ Pares con error: {failed}/{len(self.symbols)}")
                logger.info(f"   📈 Tasa de éxito: {working*100//len(self.symbols)}%")
                logger.info(f"{'='*60}")
                
                # Enviar resumen a Telegram cada 10 iteraciones
                if iteration % 10 == 0 and working > 0:
                    top_gainers = sorted([r for r in results if r['status'] == 'OK'], 
                                        key=lambda x: x['change'], reverse=True)[:3]
                    msg = f"📈 <b>Top 3 Gainers (Iteración #{iteration})</b>\n\n"
                    for r in top_gainers:
                        msg += f"{r['symbol']}: <b>{r['change']:+.2f}%</b> (${r['price']:.2f})\n"
                    self._notify(msg)
                
                # Próximo ciclo
                logger.info(f"\n⏱️ Próximo análisis en {self.interval}s...")
                await asyncio.sleep(self.interval)
            
            except KeyboardInterrupt:
                logger.info("\n🛑 Bot detenido por usuario")
                break
            except Exception as e:
                logger.error(f"\n❌ Error en loop: {e}")
                await asyncio.sleep(10)


async def main():
    """Main"""
    try:
        bot = BotTodosPares()
        await bot.run()
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot terminado")
