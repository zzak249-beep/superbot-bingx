#!/usr/bin/env python3
"""
Bot con Telegram CORREGIDO - Envía CADA SEÑAL a Telegram
"""

import os
import asyncio
import logging
import requests
import json
from datetime import datetime
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
load_dotenv()


class BotConTelegram:
    """Bot que REALMENTE envía signals a Telegram"""
    
    def __init__(self):
        """Inicializar"""
        self.symbols = [
            'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'MATIC-USDT', 'AVAX-USDT',
            'DOGE-USDT', 'ADA-USDT', 'XRP-USDT', 'DOT-USDT', 'LINK-USDT'
        ]
        
        # TELEGRAM - MUY IMPORTANTE
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        
        logger.info(f"Telegram Token: {'✅ Configurado' if self.telegram_token else '❌ NO CONFIGURADO'}")
        logger.info(f"Telegram Chat ID: {'✅ Configurado' if self.chat_id else '❌ NO CONFIGURADO'}")
        
        self.interval = int(os.getenv('CHECK_INTERVAL', '60'))
        self.buy_threshold = 0.8
        
        self.stats = {
            'total_signals': 0,
            'long_signals': 0,
            'short_signals': 0,
            'telegram_sent': 0,
            'telegram_failed': 0
        }
        
        logger.info("="*80)
        logger.info("🚀 BOT CON TELEGRAM CORREGIDO")
        logger.info("📱 Envía CADA señal a Telegram")
        logger.info(f"📊 Pares: {len(self.symbols)}")
        logger.info(f"⏱️ Intervalo: {self.interval}s")
        logger.info(f"🎯 Buy Threshold: {self.buy_threshold}%")
        logger.info("="*80)
        
        # VERIFICAR TELEGRAM
        self._test_telegram()
    
    def _test_telegram(self):
        """Probar conexión Telegram"""
        if not self.telegram_token or not self.chat_id:
            logger.error("❌ ERROR: Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
            logger.error("   Agrega en Railway → Settings → Environment:")
            logger.error("   TELEGRAM_BOT_TOKEN = tu_token")
            logger.error("   TELEGRAM_CHAT_ID = tu_id")
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = {
                'chat_id': self.chat_id,
                'text': '✅ Bot con Telegram CORREGIDO\n📱 Listo para enviar signals'
            }
            response = requests.post(url, json=data, timeout=5)
            
            if response.status_code == 200:
                logger.info("✅ Telegram CONECTADO CORRECTAMENTE")
                self.stats['telegram_sent'] += 1
                return True
            else:
                logger.error(f"❌ Error Telegram: {response.status_code}")
                logger.error(f"   Respuesta: {response.text}")
                return False
        except Exception as e:
            logger.error(f"❌ Error conectando Telegram: {e}")
            return False
    
    def _send_to_telegram(self, msg: str, retry=True) -> bool:
        """Enviar mensaje a Telegram - CON RETRY"""
        if not self.telegram_token or not self.chat_id:
            logger.warning("⚠️ Telegram no configurado")
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = {
                'chat_id': self.chat_id,
                'text': msg,
                'parse_mode': 'HTML'
            }
            
            response = requests.post(url, json=data, timeout=10)
            
            if response.status_code == 200:
                self.stats['telegram_sent'] += 1
                logger.info("✅ Enviado a Telegram")
                return True
            else:
                logger.error(f"❌ Error Telegram: {response.status_code}")
                self.stats['telegram_failed'] += 1
                
                if retry:
                    logger.info("   Reintentando...")
                    asyncio.sleep(1)
                    return self._send_to_telegram(msg, retry=False)
                
                return False
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            self.stats['telegram_failed'] += 1
            return False
    
    def get_price_data(self, symbol: str) -> dict:
        """Obtener datos de precio"""
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
            
            return {'symbol': symbol, 'status': 'ERROR'}
        except:
            return {'symbol': symbol, 'status': 'ERROR'}
    
    def analyze_signal(self, symbol: str, data: dict) -> dict:
        """Analizar y generar señal"""
        try:
            change = data.get('change', 0)
            price = data.get('price', 0)
            
            if change >= self.buy_threshold:
                score = min(95, 50 + (change * 10))
                return {
                    'direction': 'LONG',
                    'score': score,
                    'change': change,
                    'price': price
                }
            
            elif change <= -self.buy_threshold:
                score = min(95, 50 + (abs(change) * 10))
                return {
                    'direction': 'SHORT',
                    'score': score,
                    'change': change,
                    'price': price
                }
            
            else:
                return {
                    'direction': 'NEUTRAL',
                    'score': 0,
                    'change': change,
                    'price': price
                }
        except:
            return {'direction': 'NEUTRAL', 'score': 0, 'change': 0, 'price': 0}
    
    def calculate_targets(self, entry_price: float, direction: str) -> dict:
        """Calcular TP y SL"""
        try:
            if direction == 'LONG':
                tp1 = entry_price * 1.015
                tp2 = entry_price * 1.03
                sl = entry_price * 0.985
            else:
                tp1 = entry_price * 0.985
                tp2 = entry_price * 0.97
                sl = entry_price * 1.015
            
            return {'tp1': tp1, 'tp2': tp2, 'sl': sl}
        except:
            return {'tp1': 0, 'tp2': 0, 'sl': 0}
    
    async def run(self):
        """Loop principal"""
        logger.info("\n🚀 Bot iniciado...\n")
        iteration = 0
        
        while True:
            try:
                iteration += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"⏱️ ITERACIÓN #{iteration}")
                logger.info(f"{'='*80}\n")
                
                long_count = 0
                short_count = 0
                analyzed = 0
                
                for i, symbol in enumerate(self.symbols, 1):
                    try:
                        data = self.get_price_data(symbol)
                        
                        if data['status'] != 'OK':
                            continue
                        
                        analyzed += 1
                        signal = self.analyze_signal(symbol, data)
                        
                        # LONG
                        if signal['direction'] == 'LONG':
                            long_count += 1
                            targets = self.calculate_targets(data['price'], 'LONG')
                            
                            logger.info(f"🟢 {symbol}: ${data['price']:.4f} | {signal['change']:+.2f}% | LONG")
                            self.stats['long_signals'] += 1
                            
                            # ENVIAR A TELEGRAM - INMEDIATAMENTE
                            msg = f"""🟢 <b>LONG SIGNAL</b>
<b>{symbol}</b>

💰 <b>Entry:</b> ${data['price']:.4f}
📈 <b>Cambio:</b> {signal['change']:+.2f}%
🎯 <b>Score:</b> {signal['score']:.0f}%

<b>Objetivos:</b>
• TP1: ${targets['tp1']:.4f} (+1.5%)
• TP2: ${targets['tp2']:.4f} (+3.0%)
🛑 <b>SL:</b> ${targets['sl']:.4f} (-1.5%)

⏰ {datetime.now().strftime('%H:%M:%S')}"""
                            
                            self._send_to_telegram(msg)
                        
                        # SHORT
                        elif signal['direction'] == 'SHORT':
                            short_count += 1
                            targets = self.calculate_targets(data['price'], 'SHORT')
                            
                            logger.info(f"🔴 {symbol}: ${data['price']:.4f} | {signal['change']:+.2f}% | SHORT")
                            self.stats['short_signals'] += 1
                            
                            # ENVIAR A TELEGRAM - INMEDIATAMENTE
                            msg = f"""🔴 <b>SHORT SIGNAL</b>
<b>{symbol}</b>

💰 <b>Entry:</b> ${data['price']:.4f}
📉 <b>Cambio:</b> {signal['change']:+.2f}%
🎯 <b>Score:</b> {signal['score']:.0f}%

<b>Objetivos:</b>
• TP1: ${targets['tp1']:.4f} (-1.5%)
• TP2: ${targets['tp2']:.4f} (-3.0%)
🛑 <b>SL:</b> ${targets['sl']:.4f} (+1.5%)

⏰ {datetime.now().strftime('%H:%M:%S')}"""
                            
                            self._send_to_telegram(msg)
                        
                        else:
                            logger.info(f"⚪ {symbol}: ${data['price']:.4f} | {signal['change']:+.2f}% | NEUTRAL")
                    
                    except Exception as e:
                        logger.debug(f"Error {symbol}: {str(e)[:40]}")
                    
                    await asyncio.sleep(0.05)
                
                self.stats['total_signals'] = long_count + short_count
                
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 RESUMEN:")
                logger.info(f"   ✅ Analizados: {analyzed}/10")
                logger.info(f"   🟢 LONG: {long_count}")
                logger.info(f"   🔴 SHORT: {short_count}")
                logger.info(f"   📱 Telegram: ✅ {self.stats['telegram_sent']} enviados | ❌ {self.stats['telegram_failed']} fallidos")
                logger.info(f"{'='*80}\n")
                
                # Resumen cada 10 iteraciones
                if iteration % 10 == 0:
                    msg = f"""📊 <b>RESUMEN ITERACIÓN #{iteration}</b>

🟢 <b>LONG:</b> {self.stats['long_signals']} total
🔴 <b>SHORT:</b> {self.stats['short_signals']} total
📈 <b>Total Signals:</b> {self.stats['total_signals']} esta ronda

📱 <b>Telegram:</b>
✅ Enviados: {self.stats['telegram_sent']}
❌ Fallidos: {self.stats['telegram_failed']}"""
                    
                    self._send_to_telegram(msg)
                
                logger.info(f"⏱️ Próximo en {self.interval}s...\n")
                await asyncio.sleep(self.interval)
            
            except KeyboardInterrupt:
                logger.info("\n🛑 Bot detenido")
                break
            except Exception as e:
                logger.error(f"\n❌ Error: {e}")
                await asyncio.sleep(10)


async def main():
    try:
        bot = BotConTelegram()
        await bot.run()
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot terminado")
