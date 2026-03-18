#!/usr/bin/env python3
"""
Bot Trading Profesional v2 CORREGIDO
Análisis direccional con manejo de errores robusto
"""

import os
import asyncio
import logging
import requests
import numpy as np
from datetime import datetime
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
load_dotenv()


class BotProfesionalCorregido:
    """Bot profesional con manejo robusto de errores"""
    
    def __init__(self):
        """Inicializar con análisis avanzado"""
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        
        # Pares principales (solo los más confiables)
        self.symbols = [
            'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'MATIC-USDT', 'AVAX-USDT',
            'DOGE-USDT', 'ADA-USDT', 'XRP-USDT', 'DOT-USDT', 'LINK-USDT'
        ]
        
        self.interval = int(os.getenv('CHECK_INTERVAL', '180'))
        self.position_size = float(os.getenv('MAX_POSITION_SIZE', '100'))
        
        self.trade_stats = {'total': 0, 'wins': 0, 'losses': 0, 'profit': 0.0}
        
        logger.info("="*80)
        logger.info("🚀 BOT PROFESIONAL v2 CORREGIDO")
        logger.info(f"📊 Pares: {len(self.symbols)}")
        logger.info(f"⏱️ Intervalo: {self.interval}s")
        logger.info("🎯 Características:")
        logger.info("   ✅ Análisis direccional (Tendencia)")
        logger.info("   ✅ Detección de zonas (Soporte/Resistencia)")
        logger.info("   ✅ Confirmación multi-indicador")
        logger.info("   ✅ Long y Short automático")
        logger.info("   ✅ Manejo robusto de errores")
        logger.info("="*80)
        
        self._notify("🚀 Bot Profesional v2 (CORREGIDO)\n✅ Listo\n💰 Analizando pares...")
    
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
    
    def get_price_data(self, symbol: str) -> dict:
        """Obtener datos de precio con manejo de errores"""
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
            logger.debug(f"Error obteniendo datos de {symbol}: {str(e)[:50]}")
            return {'symbol': symbol, 'status': 'ERROR'}
    
    def analyze_direction(self, symbol: str, data: dict) -> dict:
        """Análisis simplificado de dirección (solo con datos actuales)"""
        try:
            change = data.get('change', 0)
            price = data.get('price', 0)
            
            # Lógica simple pero efectiva
            # Si el cambio 24h es positivo = tendencia alcista
            # Si es negativo = tendencia bajista
            
            if change > 2:
                direction = 'LONG'
                score = min(90, 50 + abs(change))
            elif change < -2:
                direction = 'SHORT'
                score = min(90, 50 + abs(change))
            else:
                direction = 'NEUTRAL'
                score = 50 + abs(change)
            
            return {
                'direction': direction,
                'score': score,
                'change': change,
                'strength': 'FUERTE' if score > 70 else ('MEDIA' if score > 50 else 'DÉBIL')
            }
        except Exception as e:
            logger.debug(f"Error analizando dirección de {symbol}: {str(e)[:50]}")
            return {'direction': 'NEUTRAL', 'score': 0, 'change': 0, 'strength': 'ERROR'}
    
    def detect_zones(self, symbol: str, data: dict) -> dict:
        """Detectar zonas basadas en movimiento 24h"""
        try:
            price = data.get('price', 0)
            high = data.get('high', 0)
            low = data.get('low', 0)
            
            if price <= 0:
                return {
                    'support': 0,
                    'resistance': 0,
                    'current_price': 0,
                    'status': 'ERROR'
                }
            
            # Calcular zonas simples
            range_price = high - low
            support = price - (range_price * 0.5)
            resistance = price + (range_price * 0.5)
            
            # Asegurar que support < price < resistance
            support = min(support, price * 0.95)
            resistance = max(resistance, price * 1.05)
            
            return {
                'support': support,
                'resistance': resistance,
                'current_price': price,
                'high': high,
                'low': low,
                'status': 'OK'
            }
        except Exception as e:
            logger.debug(f"Error detectando zonas de {symbol}: {str(e)[:50]}")
            return {'support': 0, 'resistance': 0, 'current_price': 0, 'status': 'ERROR'}
    
    def get_confirmation(self, data: dict) -> dict:
        """Confirmación simple basada en cambio de precio"""
        try:
            change = data.get('change', 0)
            volume = data.get('volume', 0)
            
            confirmations = 0
            
            # Confirmación 1: Cambio significativo
            if abs(change) > 0.5:
                confirmations += 1
            
            # Confirmación 2: Volumen presente
            if volume > 0:
                confirmations += 1
            
            # Confirmación 3: Dirección clara
            if change > 1 or change < -1:
                confirmations += 1
            
            score = (confirmations / 3) * 100
            
            return {
                'confirmations': confirmations,
                'total': 3,
                'score': score,
                'strength': 'EXCELENTE' if score > 70 else ('BUENA' if score > 50 else 'DÉBIL')
            }
        except Exception as e:
            logger.debug(f"Error en confirmación: {str(e)[:50]}")
            return {'confirmations': 0, 'total': 3, 'score': 0, 'strength': 'ERROR'}
    
    def should_enter_long(self, direction: dict, zones: dict, confirmation: dict) -> bool:
        """Determinar si entrar en LONG"""
        try:
            # Reglas para LONG
            rule1 = direction['direction'] == 'LONG'
            rule2 = direction['score'] > 60
            rule3 = zones['current_price'] > zones['support']
            rule4 = confirmation['confirmations'] >= 2
            
            return rule1 and rule2 and rule3 and rule4
        except:
            return False
    
    def should_enter_short(self, direction: dict, zones: dict, confirmation: dict) -> bool:
        """Determinar si entrar en SHORT"""
        try:
            # Reglas para SHORT
            rule1 = direction['direction'] == 'SHORT'
            rule2 = direction['score'] > 60
            rule3 = zones['current_price'] < zones['resistance']
            rule4 = confirmation['confirmations'] >= 2
            
            return rule1 and rule2 and rule3 and rule4
        except:
            return False
    
    def calculate_targets(self, entry_price: float, direction: str, zones: dict) -> dict:
        """Calcular objetivos y stop loss"""
        try:
            if direction == 'LONG':
                resistance = zones['resistance']
                support = zones['support']
                distance = abs(resistance - support)
                
                tp1 = entry_price + (distance * 0.3)
                tp2 = entry_price + (distance * 0.6)
                sl = entry_price - (distance * 0.2)
                
            else:  # SHORT
                resistance = zones['resistance']
                support = zones['support']
                distance = abs(resistance - support)
                
                tp1 = entry_price - (distance * 0.3)
                tp2 = entry_price - (distance * 0.6)
                sl = entry_price + (distance * 0.2)
            
            return {
                'tp1': tp1,
                'tp2': tp2,
                'sl': sl,
                'distance': abs(tp1 - entry_price)
            }
        except:
            return {'tp1': 0, 'tp2': 0, 'sl': 0, 'distance': 0}
    
    async def run(self):
        """Loop principal con análisis profesional"""
        logger.info("\n🚀 Bot iniciado...\n")
        iteration = 0
        
        while True:
            try:
                iteration += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"⏱️ ITERACIÓN #{iteration} - {datetime.now().strftime('%H:%M:%S')}")
                logger.info(f"{'='*80}\n")
                
                long_signals = []
                short_signals = []
                analyzed = 0
                errors = 0
                
                logger.info(f"📊 Analizando {len(self.symbols)} pares...\n")
                
                for i, symbol in enumerate(self.symbols, 1):
                    try:
                        # Obtener datos
                        data = self.get_price_data(symbol)
                        
                        if data['status'] != 'OK':
                            logger.info(f"{i:2d}. ❌ {symbol:12} - Error obteniendo datos")
                            errors += 1
                            continue
                        
                        analyzed += 1
                        
                        # Análisis
                        direction = self.analyze_direction(symbol, data)
                        zones = self.detect_zones(symbol, data)
                        confirmation = self.get_confirmation(data)
                        
                        # Validar que zonas sean válidas
                        if zones['status'] != 'OK':
                            logger.info(f"{i:2d}. ❌ {symbol:12} - Error en zonas")
                            continue
                        
                        # Decisión
                        long_ok = self.should_enter_long(direction, zones, confirmation)
                        short_ok = self.should_enter_short(direction, zones, confirmation)
                        
                        if long_ok:
                            targets = self.calculate_targets(zones['current_price'], 'LONG', zones)
                            logger.info(f"{i:2d}. 🟢 {symbol:12} ${zones['current_price']:.4f} LONG")
                            logger.info(f"   Score:{direction['score']:.0f}% | Conf:{confirmation['score']:.0f}%")
                            logger.info(f"   TP1: ${targets['tp1']:.4f} | TP2: ${targets['tp2']:.4f} | SL: ${targets['sl']:.4f}")
                            
                            long_signals.append(symbol)
                            
                            msg = f"🟢 <b>LONG</b>\n{symbol}\n"
                            msg += f"💰 Entry: ${zones['current_price']:.4f}\n"
                            msg += f"🎯 TP1: ${targets['tp1']:.4f} | TP2: ${targets['tp2']:.4f}\n"
                            msg += f"🛑 SL: ${targets['sl']:.4f}\n"
                            msg += f"📊 Score: {direction['score']:.0f}%"
                            self._notify(msg)
                        
                        elif short_ok:
                            targets = self.calculate_targets(zones['current_price'], 'SHORT', zones)
                            logger.info(f"{i:2d}. 🔴 {symbol:12} ${zones['current_price']:.4f} SHORT")
                            logger.info(f"   Score:{direction['score']:.0f}% | Conf:{confirmation['score']:.0f}%")
                            logger.info(f"   TP1: ${targets['tp1']:.4f} | TP2: ${targets['tp2']:.4f} | SL: ${targets['sl']:.4f}")
                            
                            short_signals.append(symbol)
                            
                            msg = f"🔴 <b>SHORT</b>\n{symbol}\n"
                            msg += f"💰 Entry: ${zones['current_price']:.4f}\n"
                            msg += f"🎯 TP1: ${targets['tp1']:.4f} | TP2: ${targets['tp2']:.4f}\n"
                            msg += f"🛑 SL: ${targets['sl']:.4f}\n"
                            msg += f"📊 Score: {direction['score']:.0f}%"
                            self._notify(msg)
                        
                        else:
                            logger.info(f"{i:2d}. ⚪ {symbol:12} ${zones['current_price']:.4f} - {direction['direction']} ({direction['score']:.0f}%)")
                    
                    except Exception as e:
                        logger.debug(f"{i:2d}. ❌ {symbol:12} - Error: {str(e)[:40]}")
                        errors += 1
                    
                    await asyncio.sleep(0.05)
                
                # RESUMEN
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 RESUMEN ITERACIÓN #{iteration}:")
                logger.info(f"   ✅ Analizados: {analyzed}/{len(self.symbols)}")
                logger.info(f"   ❌ Errores: {errors}")
                logger.info(f"   🟢 Señales LONG: {len(long_signals)}")
                logger.info(f"   🔴 Señales SHORT: {len(short_signals)}")
                logger.info(f"   📊 Total trades: {self.trade_stats['total']}")
                logger.info(f"{'='*80}")
                
                # Resumen a Telegram cada 10 iteraciones
                if iteration % 10 == 0:
                    msg = f"📊 <b>Resumen Iteración #{iteration}</b>\n"
                    msg += f"✅ Analizados: {analyzed}/{len(self.symbols)}\n"
                    msg += f"🟢 LONG: {len(long_signals)} | 🔴 SHORT: {len(short_signals)}\n"
                    msg += f"📊 Total: {self.trade_stats['total']}"
                    self._notify(msg)
                
                logger.info(f"\n⏱️ Próximo análisis en {self.interval}s...\n")
                await asyncio.sleep(self.interval)
            
            except KeyboardInterrupt:
                logger.info("\n🛑 Bot detenido")
                break
            except Exception as e:
                logger.error(f"\n❌ Error principal: {e}")
                await asyncio.sleep(10)


async def main():
    """Main"""
    try:
        bot = BotProfesionalCorregido()
        await bot.run()
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot terminado")
