#!/usr/bin/env python3
"""
BOT TRADING MAESTRO v2.5 - CORREGIDO Y COMPLETO
Dashboard + Auto-Trading Real + Notificaciones
"""

import os
import asyncio
import logging
import requests
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Configuración de Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
load_dotenv()

# Importación segura del ejecutor de BingX
try:
    from bingx_autotrader import BingXAutoTrader
except ImportError:
    logger.error("❌ ERROR: No se encontró bingx_autotrader.py")

class DashboardStats:
    def __init__(self):
        self.data_file = 'bot_stats.json'
        self.load_stats()
    
    def load_stats(self):
        if Path(self.data_file).exists():
            try:
                with open(self.data_file, 'r') as f:
                    self.stats = json.load(f)
            except:
                self.stats = self.create_empty()
        else:
            self.stats = self.create_empty()
    
    def create_empty(self):
        cap = float(os.getenv('INITIAL_CAPITAL', '0'))
        return {
            'start_date': datetime.now().isoformat(),
            'capital_inicial': cap,
            'capital_actual': cap,
            'trades': {'total': 0, 'wins': 0, 'losses': 0, 'pending': 0},
            'pnl': {'total_usdt': 0.0, 'total_percent': 0.0},
            'signals': {'long_total': 0, 'short_total': 0, 'long_today': 0, 'short_today': 0},
            'historial': []
        }
    
    def save(self):
        try:
            with open(self.data_file, 'w') as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            logger.error(f"Error guardando stats: {e}")

    def add_signal(self, symbol, direction, entry_price, tp, sl):
        # CORRECCIÓN DE SINTAXIS AQUÍ (Línea 81 corregida)
        signal = {
            'id': len(self.stats['historial']) + 1,
            'symbol': symbol,
            'direction': direction,
            'entry': entry_price,
            'tp1': tp,
            'tp2': tp,
            'sl': sl,
            'timestamp': datetime.now().isoformat(),
            'status': 'OPEN'
        }
        self.stats['historial'].append(signal)
        self.stats['trades']['total'] += 1
        if direction == 'LONG':
            self.stats['signals']['long_total'] += 1
        else:
            self.stats['signals']['short_total'] += 1
        self.save()
        return signal

class BotMaestro:
    def __init__(self):
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        self.symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'MATIC-USDT', 'AVAX-USDT', 'LINK-USDT']
        self.interval = int(os.getenv('CHECK_INTERVAL', '120'))
        
        # Activar Auto-Trading Real
        self.auto_trading = os.getenv('AUTO_TRADING_ENABLED', 'false').lower() == 'true'
        if self.auto_trading:
            self.trader = BingXAutoTrader()
            logger.info("🚀 MODO AUTO-TRADING: ACTIVADO ✅")
        else:
            self.trader = None
            logger.info("⚠️ MODO AUTO-TRADING: DESACTIVADO (Solo señales)")
            
        self.dashboard = DashboardStats()

    def _notify(self, msg: str):
        if not self.telegram_token or not self.chat_id: return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            requests.post(url, json={'chat_id': self.chat_id, 'text': msg, 'parse_mode': 'HTML'}, timeout=5)
        except Exception as e:
            logger.error(f"Error Telegram: {e}")

    def get_price_data(self, symbol: str):
        try:
            url = f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?symbol={symbol}"
            r = requests.get(url, timeout=10).json()
            if r.get('code') == 0:
                d = r['data']
                return {
                    'price': float(d['lastPrice']), 
                    'change': float(d['priceChangePercent']),
                    'status': 'OK'
                }
            return {'status': 'ERROR'}
        except:
            return {'status': 'ERROR'}

    async def run(self):
        logger.info("🤖 Bot en ejecución...")
        while True:
            for symbol in self.symbols:
                try:
                    data = self.get_price_data(symbol)
                    if data['status'] != 'OK': continue
                    
                    price = data['price']
                    change = data['change']
                    direction = None

                    # Estrategia: Cambio rápido mayor al 1.5%
                    if change > 1.5: direction = 'LONG'
                    elif change < -1.5: direction = 'SHORT'
                    
                    if direction:
                        # Cálculo de targets
                        tp = price * 1.015 if direction == 'LONG' else price * 0.985
                        sl = price * 0.992 if direction == 'LONG' else price * 1.008
                        
                        logger.info(f"🎯 OPORTUNIDAD: {symbol} | {direction} | Precio: {price}")
                        
                        # 1. Dashboard
                        self.dashboard.add_signal(symbol, direction, price, tp, sl)
                        
                        # 2. Ejecutar Trade Real en BingX
                        if self.auto_trading and self.trader:
                            try:
                                # Usamos la función execute_trade del archivo bingx_autotrader.py
                                self.trader.execute_trade(symbol, direction, price, tp, tp, sl)
                                logger.info(f"✅ ORDEN ENVIADA A BINGX: {symbol}")
                            except Exception as e:
                                logger.error(f"❌ FALLÓ EJECUCIÓN BINGX: {e}")

                        # 3. Notificar
                        emoji = "🟢" if direction == "LONG" else "🔴"
                        msg = f"{emoji} <b>NUEVA OPERACIÓN</b>\n<b>Par:</b> {symbol}\n<b>Dirección:</b> {direction}\n<b>Precio:</b> {price}\n<b>TP:</b> {tp:.4f}\n<b>SL:</b> {sl:.4f}"
                        self._notify(msg)

                except Exception as e:
                    logger.error(f"Error procesando {symbol}: {e}")
                
                await asyncio.sleep(1) # Pequeña pausa entre monedas

            await asyncio.sleep(self.interval)

if __name__ == "__main__":
    bot = BotMaestro()
    asyncio.run(bot.run())
