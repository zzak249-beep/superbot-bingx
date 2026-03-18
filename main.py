#!/usr/bin/env python3
"""
Bot Trading Profesional v2.1 - CORREGIDO
Análisis + Dashboard + Auto-Trading BingX
"""

import os
import asyncio
import logging
import requests
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# IMPORTACIÓN DEL MÓDULO DE BINGX
try:
    from bingx_autotrader import BingXAutoTrader
except ImportError:
    logger.error("No se encontró bingx_autotrader.py. Asegúrate de que el archivo esté en la misma carpeta.")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
load_dotenv()

class DashboardStats:
    """Gestión de estadísticas para dashboard"""
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
        return {
            'start_date': datetime.now().isoformat(),
            'capital_inicial': float(os.getenv('INITIAL_CAPITAL', '0')),
            'capital_actual': float(os.getenv('INITIAL_CAPITAL', '0')),
            'trades': {'total': 0, 'wins': 0, 'losses': 0, 'pending': 0},
            'pnl': {'total_usdt': 0.0, 'total_percent': 0.0},
            'signals': {'long_total': 0, 'short_total': 0, 'long_today': 0, 'short_today': 0},
            'historial': []
        }
    
    def save(self):
        try:
            with open(self.data_file, 'w') as f:
                json.dump(self.stats, f, indent=2)
        except:
            pass
    
    def add_signal(self, symbol, direction, entry_price, tp1, tp2, sl):
        # LÍNEA 81 CORREGIDA: Se cerraron correctamente las comillas y paréntesis
        signal = {
            'id': len(self.stats['historial']) + 1,
            'symbol': symbol,
            'direction': direction,
            'entry': entry_price,
            'tp1': tp1,
            'tp2': tp2,
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

class BotProfesionalConDashboard:
    def __init__(self):
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        self.symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'MATIC-USDT', 'AVAX-USDT']
        self.interval = int(os.getenv('CHECK_INTERVAL', '180'))
        
        # Configuración de Trading Real
        self.auto_trading = os.getenv('AUTO_TRADING_ENABLED', 'false').lower() == 'true'
        self.trader = BingXAutoTrader() if self.auto_trading else None
        self.dashboard = DashboardStats()

        logger.info(f"✅ Bot Iniciado | Auto-Trading: {self.auto_trading}")

    def _notify(self, msg: str):
        if not self.telegram_token or not self.chat_id: return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            requests.post(url, json={'chat_id': self.chat_id, 'text': msg, 'parse_mode': 'HTML'}, timeout=5)
        except: pass

    def get_price_data(self, symbol: str):
        try:
            url = f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?symbol={symbol}"
            r = requests.get(url, timeout=10).json()
            if r.get('code') == 0:
                d = r['data']
                return {'price': float(d['lastPrice']), 'change': float(d['priceChangePercent']), 'high': float(d['highPrice']), 'low': float(d['lowPrice']), 'status': 'OK'}
            return {'status': 'ERROR'}
        except: return {'status': 'ERROR'}

    async def run(self):
        while True:
            for symbol in self.symbols:
                data = self.get_price_data(symbol)
                if data['status'] != 'OK': continue
                
                # Lógica simple de entrada (puedes mejorarla)
                direction = None
                if data['change'] > 2.0: direction = 'LONG'
                elif data['change'] < -2.0: direction = 'SHORT'
                
                if direction:
                    price = data['price']
                    # Cálculo de targets básicos
                    tp = price * 1.02 if direction == 'LONG' else price * 0.98
                    sl = price * 0.99 if direction == 'LONG' else price * 1.01
                    
                    logger.info(f"🎯 Señal detectada: {symbol} {direction}")
                    
                    # 1. Registrar en Dashboard
                    self.dashboard.add_signal(symbol, direction, price, tp, tp, sl)
                    
                    # 2. EJECUTAR EN BINGX (Si está activo)
                    if self.auto_trading and self.trader:
                        try:
                            self.trader.execute_trade(symbol, direction, price, tp, tp, sl)
                            logger.info(f"🚀 ORDEN EJECUTADA EN BINGX: {symbol}")
                        except Exception as e:
                            logger.error(f"❌ Error al ejecutar en BingX: {e}")

                    # 3. Notificar Telegram
                    self._notify(f"🚀 <b>{direction}</b> en {symbol}\nPrecio: {price}\nTP: {tp} | SL: {sl}")

            await asyncio.sleep(self.interval)

if __name__ == "__main__":
    bot = BotProfesionalConDashboard()
    asyncio.run(bot.run())
