#!/usr/bin/env python3
import os
import asyncio
import logging
import requests
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# IMPORTACIÓN DEL MÓDULO BINGX
try:
    from bingx_autotrader import BingXAutoTrader
except ImportError:
    print("❌ ERROR: No se encontró bingx_autotrader.py. Asegúrate de que el archivo existe.")

# Configuración de Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.FileHandler('bot_ejecucion.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
load_dotenv()

class MasterBot:
    def __init__(self):
        # Símbolos con guion (necesario para la API de consulta de precio de BingX)
        self.symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'AVAX-USDT', 'LINK-USDT']
        
        # Cargar configuración desde Railway
        self.auto_trading = os.getenv('AUTO_TRADING_ENABLED', 'false').lower() == 'true'
        self.interval = int(os.getenv('CHECK_INTERVAL', '60'))
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        
        # Inicializar Trader y Stats
        self.trader = BingXAutoTrader() if self.auto_trading else None
        self.active_trades = {} # Diccionario para rastrear trades y aplicar Trailing Stop
        
        logger.info(f"🚀 MASTER BOT INICIADO | Auto-Trading: {self.auto_trading}")

    def _notify(self, msg):
        """Envía notificaciones a Telegram si está configurado"""
        if not self.telegram_token or not self.chat_id: return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            requests.post(url, json={'chat_id': self.chat_id, 'text': msg, 'parse_mode': 'HTML'}, timeout=5)
        except Exception as e:
            logger.error(f"Error Telegram: {e}")

    def get_market_data(self, symbol):
        """Obtiene precio y cambio porcentual usando el formato con guion"""
        try:
            url = f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?symbol={symbol}"
            r = requests.get(url, timeout=10).json()
            if r.get('code') == 0:
                data = r['data']
                # Si es lista tomamos el primer elemento, si no el objeto directo
                d = data[0] if isinstance(data, list) else data
                return float(d['lastPrice']), float(d['priceChangePercent'])
            return None, None
        except:
            return None, None

    async def manage_trailing_stop(self, symbol, current_price):
        """Mueve el Stop Loss a Break-even cuando ganamos un 0.6%"""
        if symbol not in self.active_trades: return

        trade = self.active_trades[symbol]
        if trade.get('protected', False): return # Ya está protegido

        entry = trade['entry']
        direction = trade['direction']

        # Si el precio se mueve a nuestro favor un 0.6%, protegemos entrada
        move_to_be = False
        if direction == "LONG" and current_price > entry * 1.006:
            move_to_be = True
        elif direction == "SHORT" and current_price < entry * 0.994:
            move_to_be = True

        if move_to_be and self.auto_trading:
            # New SL es el precio de entrada (Break Even)
            new_sl = entry * 1.001 if direction == "LONG" else entry * 0.999
            success = self.trader.update_stop_loss(symbol, direction, new_sl)
            if success:
                self.active_trades[symbol]['protected'] = True
                logger.info(f"🛡️ {symbol}: Stop Loss movido a BREAK-EVEN")
                self._notify(f"🛡️ <b>{symbol} PROTEGIDO</b>\nStop Loss movido al precio de entrada.")

    async def run(self):
        iteration = 0
        while True:
            iteration += 1
            logger.info(f"--- Ciclo de Análisis #{iteration} ---")
            
            for symbol in self.symbols:
                price, change = self.get_market_data(symbol)
                
                if price is None:
                    logger.warning(f"⚠️ No se pudo obtener datos de {symbol}")
                    continue

                # 1. Gestionar trades abiertos (Trailing Stop)
                await self.manage_trailing_stop(symbol, price)

                # 2. Lógica de entrada si no hay trade activo
                if symbol not in self.active_trades:
                    direction = None
                    # Estrategia: Ruptura de tendencia 1.8%
                    if change > 1.8: direction = "LONG"
                    elif change < -1.8: direction = "SHORT"

                    if direction:
                        logger.info(f"🎯 SEÑAL DETECTADA: {symbol} {direction} a {price}")
                        
                        if self.auto_trading:
                            if self.trader.execute_trade(symbol, direction, price):
                                self.active_trades[symbol] = {
                                    'entry': price, 
                                    'direction': direction,
                                    'protected': False
                                }
                                emoji = "🟢" if direction == "LONG" else "🔴"
                                self._notify(f"{emoji} <b>ORDEN EJECUTADA</b>\n<b>Par:</b> {symbol}\n<b>Tipo:</b> {direction}\n<b>Precio:</b> {price}")

                logger.info(f"📊 {symbol}: ${price} ({change:+.2f}%)")
                await asyncio.sleep(1) # Evitar rate limit

            await asyncio.sleep(self.interval)

if __name__ == "__main__":
    bot = MasterBot()
    asyncio.run(bot.run())
