#!/usr/bin/env python3
"""
Master Bot OPTIMIZADO - Genera Señales Constantemente
Threshold más bajo = Más señales
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


class MasterBotOptimizado:
    """Master Bot Optimizado - Genera señales"""
    
    def __init__(self):
        """Inicializar"""
        self.symbols = [
            'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'MATIC-USDT', 'AVAX-USDT',
            'DOGE-USDT', 'ADA-USDT', 'XRP-USDT', 'DOT-USDT', 'LINK-USDT'
        ]
        
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        self.interval = int(os.getenv('CHECK_INTERVAL', '60'))
        
        # THRESHOLDS OPTIMIZADOS - MAS BAJOS = MAS SIGNALS
        self.buy_threshold = 0.8      # ← REDUCIDO (era 1.5)
        self.sell_threshold = 0.5     # ← REDUCIDO
        
        self.stats = {
            'total_signals': 0,
            'long_signals': 0,
            'short_signals': 0,
        }
        
        logger.info("="*80)
        logger.info("🚀 MASTER BOT OPTIMIZADO")
        logger.info("📊 Generador de Señales Constantemente")
        logger.info(f"📊 Pares: {len(self.symbols)}")
        logger.info(f"⏱️ Intervalo: {self.interval}s")
        logger.info(f"🎯 Buy Threshold: {self.buy_threshold}%")
        logger.info(f"🎯 Sell Threshold: {self.sell_threshold}%")
        logger.info("="*80)
        
        self._notify("🚀 Master Bot Optimizado\n✅ Generador de Señales\n📊 Thresholds optimizados")
    
    def _notify(self, msg: str):
        """Enviar Telegram"""
        try:
            if not self.telegram_token or not self.chat_id:
                return
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = {'chat_id': self.chat_id, 'text': msg, 'parse_mode': 'HTML'}
            requests.post(url, json=data, timeout=5)
        except:
            pass
    
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
                            'high': float(ticker.get('highPrice', 0)),
                            'low': float(ticker.get('lowPrice', 0)),
                            'change': float(ticker.get('priceChangePercent', 0)),
                            'volume': float(ticker.get('volume', 0)),
                            'status': 'OK'
                        }
            
            return {'symbol': symbol, 'status': 'ERROR'}
        except Exception as e:
            logger.debug(f"Error {symbol}: {str(e)[:40]}")
            return {'symbol': symbol, 'status': 'ERROR'}
    
    def analyze_signal(self, symbol: str, data: dict) -> dict:
        """Analizar y generar señal"""
        try:
            change = data.get('change', 0)
            price = data.get('price', 0)
            
            # LOGICA SIMPLIFICADA - MAS SENSIBLE
            
            # LONG si cambio positivo
            if change >= self.buy_threshold:
                score = min(95, 50 + (change * 10))  # Score proporcional al cambio
                return {
                    'direction': 'LONG',
                    'score': score,
                    'change': change,
                    'price': price,
                    'strength': 'FUERTE' if score > 70 else ('MEDIA' if score > 50 else 'DÉBIL')
                }
            
            # SHORT si cambio negativo
            elif change <= -self.buy_threshold:
                score = min(95, 50 + (abs(change) * 10))
                return {
                    'direction': 'SHORT',
                    'score': score,
                    'change': change,
                    'price': price,
                    'strength': 'FUERTE' if score > 70 else ('MEDIA' if score > 50 else 'DÉBIL')
                }
            
            # NEUTRAL en el medio
            else:
                return {
                    'direction': 'NEUTRAL',
                    'score': 0,
                    'change': change,
                    'price': price,
                    'strength': 'DÉBIL'
                }
        except Exception as e:
            logger.debug(f"Error analizando {symbol}: {str(e)[:40]}")
            return {'direction': 'NEUTRAL', 'score': 0, 'change': 0, 'price': 0, 'strength': 'ERROR'}
    
    def calculate_targets(self, entry_price: float, direction: str) -> dict:
        """Calcular TP y SL"""
        try:
            if direction == 'LONG':
                tp1 = entry_price * 1.015  # +1.5%
                tp2 = entry_price * 1.03   # +3%
                sl = entry_price * 0.985   # -1.5%
            else:  # SHORT
                tp1 = entry_price * 0.985  # -1.5%
                tp2 = entry_price * 0.97   # -3%
                sl = entry_price * 1.015   # +1.5%
            
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
                logger.info(f"⏱️ ITERACIÓN #{iteration} - {datetime.now().strftime('%H:%M:%S')}")
                logger.info(f"{'='*80}\n")
                
                long_count = 0
                short_count = 0
                neutral_count = 0
                analyzed = 0
                
                logger.info(f"📊 Analizando {len(self.symbols)} pares...\n")
                
                for i, symbol in enumerate(self.symbols, 1):
                    try:
                        # Obtener datos
                        data = self.get_price_data(symbol)
                        
                        if data['status'] != 'OK':
                            logger.info(f"{i:2d}. ❌ {symbol:12} - Error obteniendo datos")
                            continue
                        
                        analyzed += 1
                        
                        # Analizar
                        signal = self.analyze_signal(symbol, data)
                        
                        # LONG
                        if signal['direction'] == 'LONG':
                            long_count += 1
                            targets = self.calculate_targets(data['price'], 'LONG')
                            
                            logger.info(f"{i:2d}. 🟢 {symbol:12} ${data['price']:.4f} | {signal['change']:+6.2f}% | LONG ({signal['strength']})")
                            logger.info(f"     Score: {signal['score']:.0f}% | TP1: ${targets['tp1']:.4f} | TP2: ${targets['tp2']:.4f} | SL: ${targets['sl']:.4f}")
                            
                            self.stats['long_signals'] += 1
                            
                            msg = f"🟢 <b>LONG</b>\n{symbol}\n💰 ${data['price']:.4f}\n"
                            msg += f"📈 Cambio: {signal['change']:+.2f}%\n"
                            msg += f"🎯 TP1: ${targets['tp1']:.4f} | TP2: ${targets['tp2']:.4f}\n"
                            msg += f"🛑 SL: ${targets['sl']:.4f}"
                            self._notify(msg)
                        
                        # SHORT
                        elif signal['direction'] == 'SHORT':
                            short_count += 1
                            targets = self.calculate_targets(data['price'], 'SHORT')
                            
                            logger.info(f"{i:2d}. 🔴 {symbol:12} ${data['price']:.4f} | {signal['change']:+6.2f}% | SHORT ({signal['strength']})")
                            logger.info(f"     Score: {signal['score']:.0f}% | TP1: ${targets['tp1']:.4f} | TP2: ${targets['tp2']:.4f} | SL: ${targets['sl']:.4f}")
                            
                            self.stats['short_signals'] += 1
                            
                            msg = f"🔴 <b>SHORT</b>\n{symbol}\n💰 ${data['price']:.4f}\n"
                            msg += f"📉 Cambio: {signal['change']:+.2f}%\n"
                            msg += f"🎯 TP1: ${targets['tp1']:.4f} | TP2: ${targets['tp2']:.4f}\n"
                            msg += f"🛑 SL: ${targets['sl']:.4f}"
                            self._notify(msg)
                        
                        # NEUTRAL
                        else:
                            neutral_count += 1
                            logger.info(f"{i:2d}. ⚪ {symbol:12} ${data['price']:.4f} | {signal['change']:+6.2f}% | NEUTRAL")
                    
                    except Exception as e:
                        logger.debug(f"{i:2d}. ❌ {symbol:12} - Error: {str(e)[:40]}")
                    
                    await asyncio.sleep(0.05)
                
                # RESUMEN
                self.stats['total_signals'] = long_count + short_count
                
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 RESUMEN ITERACIÓN #{iteration}:")
                logger.info(f"   ✅ Analizados: {analyzed}/{len(self.symbols)}")
                logger.info(f"   🟢 LONG: {long_count}")
                logger.info(f"   🔴 SHORT: {short_count}")
                logger.info(f"   ⚪ NEUTRAL: {neutral_count}")
                logger.info(f"   📈 Total Signals: {self.stats['total_signals']}")
                logger.info(f"   📊 Total Histórico: {self.stats['long_signals'] + self.stats['short_signals']}")
                logger.info(f"{'='*80}")
                
                # Resumen cada 10 iteraciones
                if iteration % 10 == 0:
                    msg = f"📊 <b>RESUMEN #{iteration}</b>\n"
                    msg += f"🟢 LONG: {self.stats['long_signals']} total\n"
                    msg += f"🔴 SHORT: {self.stats['short_signals']} total\n"
                    msg += f"📈 Señales esta ronda: {self.stats['total_signals']}"
                    self._notify(msg)
                
                logger.info(f"\n⏱️ Próximo análisis en {self.interval}s...\n")
                await asyncio.sleep(self.interval)
            
            except KeyboardInterrupt:
                logger.info("\n🛑 Bot detenido")
                break
            except Exception as e:
                logger.error(f"\n❌ Error: {e}")
                await asyncio.sleep(10)


async def main():
    try:
        bot = MasterBotOptimizado()
        await bot.run()
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot terminado")
