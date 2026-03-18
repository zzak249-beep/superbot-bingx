#!/usr/bin/env python3
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

# Intentar importar el trader de BingX
try:
    from bingx_autotrader import BingXAutoTrader
except ImportError:
    logger.error("❌ No se encontró bingx_autotrader.py")

class DashboardStats:
    def __init__(self):
        self.data_file = 'bot_stats.json'
        self.stats = self.load_stats()
    
    def load_stats(self):
        if Path(self.data_file).exists():
            try:
                with open(self.data_file, 'r') as f:
                    return json.load(f)
            except:
                return self.create_empty()
        return self.create_empty()
    
    def create_empty(self):
        cap = float(os.getenv('INITIAL_CAPITAL', '100'))
        return {
            'start_date': datetime.now().isoformat(),
            'capital_inicial': cap,
            'capital_actual': cap,
            'trades': {'total': 0, 'wins': 0, 'losses': 0, 'pending': 0},
            'pnl': {'total_usdt': 0.0, 'total_percent': 0.0},
            'signals': {'long_total': 0, 'short_total': 0, 'long_today': 0, 'short_today': 0},
            'historial': []
        }
    
    def add_signal(self, symbol, direction, entry, tp, sl):
        signal = {
            'id': len(self.stats['historial']) + 1,
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'tp': tp,
            'sl': sl,
            'timestamp': datetime.now().isoformat(),
            'status': 'OPEN'
        }
        self.stats['historial'].append(signal)
        self.stats['trades']['total'] += 1
        if direction == 'LONG': self.stats['signals']['long_total'] += 1
        else: self.stats['signals']['short_total'] += 1
        
        with open(self.data_file, 'w') as f:
            json.dump(self.stats, f, indent=2)

class BotMaestro:
    def __init__(self):
        self.symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'MATIC-USDT', 'AVAX-USDT', 'LINK-USDT']
        self.interval = int(os.getenv('CHECK_INTERVAL', '120'))
        self.auto_trading = os.getenv('AUTO_TRADING_ENABLED', 'false').lower() == 'true'
        
        # Verificar que las llaves existan
        key = os.getenv('BINGX_API_KEY')
        secret = os.getenv('BINGX_API_SECRET')
        
        if self.auto_trading and key and secret:
            self.trader = BingXAutoTrader()
            logger.info("🚀 MODO AUTO-TRADING: ACTIVADO ✅")
        else:
            self.trader = None
            logger.warning("⚠️ MODO AUTO-TRADING: DESACTIVADO o faltan APIs")
            
        self.dashboard = DashboardStats()

    async def run(self):
        while True:
            for symbol in self.symbols:
                try:
                    # Simulación de obtención de datos para el ejemplo
                    url = f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?symbol={symbol}"
                    r = requests.get(url, timeout=10).json()
                    if r.get('code') != 0: continue
                    
                    price = float(r['data']['lastPrice'])
                    change = float(r['data']['priceChangePercent'])
                    
                    direction = None
                    if change > 1.8: direction = 'LONG'
                    elif change < -1.8: direction = 'SHORT'
                    
                    if direction:
                        tp = price * 1.02 if direction == 'LONG' else price * 0.98
                        sl = price * 0.99 if direction == 'LONG' else price * 1.01
                        
                        logger.info(f"🎯 OPORTUNIDAD: {symbol} | {direction} | Precio: {price}")
                        
                        # 1. Registrar señal
                        self.dashboard.add_signal(symbol, direction, price, tp, sl)
                        
                        # 2. Ejecutar en BingX
                        if self.auto_trading and self.trader:
                            # Intentamos ejecutar y capturamos el resultado real
                            success = self.trader.execute_trade(symbol, direction, price, tp, tp, sl)
                            if success:
                                logger.info(f"✅ ORDEN CONFIRMADA EN BINGX: {symbol}")
                            else:
                                logger.error(f"❌ BINGX RECHAZÓ LA ORDEN: {symbol} (Verifica API Keys)")

                except Exception as e:
                    logger.error(f"Error en {symbol}: {e}")
                await asyncio.sleep(1)
            await asyncio.sleep(self.interval)

if __name__ == "__main__":
    bot = BotMaestro()
    asyncio.run(bot.run())
