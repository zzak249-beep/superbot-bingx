#!/usr/bin/env python3
"""
Bot Trading BingX - CON TRADES AUTOMÁTICOS
Ejecuta compras/ventas automáticas según condiciones
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


class BotAutoTrade:
    """Bot que EJECUTA TRADES AUTOMÁTICOS"""
    
    def __init__(self):
        """Inicializar"""
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        
        # TODOS los pares
        symbols_str = os.getenv('SYMBOLS', 'BTC-USDT,ETH-USDT,BNB-USDT')
        self.symbols = [s.strip() for s in symbols_str.split(',')]
        
        self.timeframe = os.getenv('TIMEFRAME', '15m')
        self.interval = int(os.getenv('CHECK_INTERVAL', '60'))
        self.position_size = float(os.getenv('MAX_POSITION_SIZE', '100'))
        
        # Umbrales para trading automático
        self.buy_threshold = 1.0  # Compra si cambio > +1%
        self.sell_threshold = 0.5  # Vende si cambio > +0.5%
        
        # Tracking de posiciones abiertas
        self.open_positions = {}
        
        logger.info("="*60)
        logger.info(f"🤖 BOT TRADING AUTOMÁTICO")
        logger.info(f"📊 TOTAL DE PARES: {len(self.symbols)}")
        logger.info(f"📦 Tamaño posición: ${self.position_size}")
        logger.info(f"🟢 Umbral BUY: >{self.buy_threshold}%")
        logger.info(f"🔴 Umbral SELL: >{self.sell_threshold}%")
        logger.info("="*60)
        
        self._notify(f"🤖 Bot Trading Automático\n📊 {len(self.symbols)} pares\n📦 ${self.position_size}/posición\n✅ Listo")
    
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
        """Obtener precio del símbolo"""
        try:
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
                            'change': float(ticker.get('priceChangePercent', 0)),
                            'status': 'OK'
                        }
            
            return {'symbol': symbol, 'price': 0, 'change': 0, 'status': 'ERROR'}
        
        except Exception as e:
            return {'symbol': symbol, 'price': 0, 'change': 0, 'status': f'ERROR - {str(e)[:30]}'}
    
    def buy(self, symbol: str, price: float, change: float):
        """Ejecutar compra"""
        logger.info(f"🟢 COMPRANDO {symbol} a ${price:.2f} (Cambio: {change:+.2f}%)")
        logger.info(f"   Monto: ${self.position_size:.2f}")
        logger.info(f"   Cantidad: {self.position_size/price:.6f}")
        
        # Registrar posición abierta
        self.open_positions[symbol] = {
            'entry_price': price,
            'entry_change': change,
            'size': self.position_size / price,
            'timestamp': datetime.now()
        }
        
        # Notificar
        self._notify(f"🟢 <b>COMPRA ABIERTA</b>\n{symbol}\n💰 Precio: ${price:.2f}\n📈 Cambio: {change:+.2f}%\n📦 Cantidad: {self.position_size/price:.6f}")
    
    def sell(self, symbol: str, price: float, change: float):
        """Ejecutar venta"""
        if symbol not in self.open_positions:
            logger.warning(f"⚠️ No hay posición abierta en {symbol}")
            return
        
        pos = self.open_positions[symbol]
        pnl = (price - pos['entry_price']) * pos['size']
        pnl_pct = ((price - pos['entry_price']) / pos['entry_price']) * 100
        
        logger.info(f"🔴 VENDIENDO {symbol} a ${price:.2f} (Cambio: {change:+.2f}%)")
        logger.info(f"   PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
        logger.info(f"   Entrada: ${pos['entry_price']:.2f}")
        
        # Notificar
        status = "✅ GANANCIA" if pnl > 0 else "❌ PÉRDIDA"
        self._notify(f"🔴 <b>VENTA CERRADA</b>\n{symbol}\n💰 Precio: ${price:.2f}\n📊 Entrada: ${pos['entry_price']:.2f}\n{status}: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
        
        # Cerrar posición
        del self.open_positions[symbol]
    
    async def run(self):
        """Loop principal con trading automático"""
        logger.info("\n🚀 Iniciando trading automático...\n")
        iteration = 0
        
        while True:
            try:
                iteration += 1
                logger.info(f"\n{'='*60}")
                logger.info(f"⏱️ ITERACIÓN #{iteration} - {datetime.now().strftime('%H:%M:%S')}")
                logger.info(f"{'='*60}\n")
                
                results = []
                buy_signals = []
                sell_signals = []
                
                logger.info(f"📊 Analizando {len(self.symbols)} pares...\n")
                
                for i, symbol in enumerate(self.symbols, 1):
                    result = self.get_price(symbol)
                    results.append(result)
                    
                    if result['status'] == 'OK':
                        # Lógica de trading automático
                        
                        # COMPRA: Si cambio > +1% y no hay posición abierta
                        if result['change'] > self.buy_threshold and symbol not in self.open_positions:
                            logger.info(f"{i:2d}. 🟢 {symbol:12} ${result['price']:12,.2f} | "
                                      f"Cambio: {result['change']:+6.2f}% | 🟢 SEÑAL BUY")
                            buy_signals.append(symbol)
                            self.buy(symbol, result['price'], result['change'])
                        
                        # VENTA: Si cambio > +0.5% y hay posición abierta
                        elif result['change'] > self.sell_threshold and symbol in self.open_positions:
                            logger.info(f"{i:2d}. 🔴 {symbol:12} ${result['price']:12,.2f} | "
                                      f"Cambio: {result['change']:+6.2f}% | 🔴 SEÑAL SELL")
                            sell_signals.append(symbol)
                            self.sell(symbol, result['price'], result['change'])
                        
                        # HOLD: Monitoreando
                        elif symbol in self.open_positions:
                            logger.info(f"{i:2d}. 📊 {symbol:12} ${result['price']:12,.2f} | "
                                      f"Cambio: {result['change']:+6.2f}% | 📊 POSICIÓN ABIERTA")
                        
                        else:
                            logger.info(f"{i:2d}. ⚪ {symbol:12} ${result['price']:12,.2f} | "
                                      f"Cambio: {result['change']:+6.2f}% | ⚪ ESPERANDO")
                    
                    else:
                        logger.info(f"{i:2d}. ❌ {symbol:12} {result['status']}")
                    
                    await asyncio.sleep(0.1)
                
                # RESUMEN
                logger.info(f"\n{'='*60}")
                logger.info(f"📊 RESUMEN ITERACIÓN #{iteration}:")
                logger.info(f"   🟢 Señales BUY: {len(buy_signals)}")
                logger.info(f"   🔴 Señales SELL: {len(sell_signals)}")
                logger.info(f"   📊 Posiciones ABIERTAS: {len(self.open_positions)}")
                
                if self.open_positions:
                    logger.info(f"\n   Posiciones activas:")
                    for symbol, pos in self.open_positions.items():
                        logger.info(f"      - {symbol}: Entrada ${pos['entry_price']:.2f}")
                
                logger.info(f"{'='*60}")
                
                # Enviar resumen a Telegram cada 10 iteraciones
                if iteration % 10 == 0:
                    msg = f"📊 <b>Resumen Iteración #{iteration}</b>\n"
                    msg += f"🟢 Compras: {len(buy_signals)}\n"
                    msg += f"🔴 Ventas: {len(sell_signals)}\n"
                    msg += f"📊 Abiertas: {len(self.open_positions)}"
                    self._notify(msg)
                
                # Próximo ciclo
                logger.info(f"\n⏱️ Próximo análisis en {self.interval}s...")
                await asyncio.sleep(self.interval)
            
            except KeyboardInterrupt:
                logger.info("\n🛑 Bot detenido")
                break
            except Exception as e:
                logger.error(f"\n❌ Error: {e}")
                await asyncio.sleep(10)


async def main():
    """Main"""
    try:
        bot = BotAutoTrade()
        await bot.run()
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot terminado")
