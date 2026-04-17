import os
import asyncio
import ccxt.pro as ccxt
import pandas as pd
import numpy as np
import logging
from sklearn.ensemble import RandomForestRegressor
from datetime import datetime
from dotenv import load_dotenv

# Cargar variables de entorno si existen (local)
load_dotenv()

# Configuración de Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("SimonsAI")

class UltimateQuantBot:
    def __init__(self):
        # Conexión a BingX (Dinero Real)
        self.exchange = ccxt.bingx({
            'apiKey': os.getenv('BINGX_API_KEY'),
            'secret': os.getenv('BINGX_SECRET_KEY'),
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'} # Para Futuros Perpetuos
        })
        
        self.symbols = ['BTC/USDT:USDT', 'ETH/USDT:USDT']
        self.order_size = float(os.getenv('ORDER_SIZE', 0.001))
        
        # Parámetros de Riesgo
        self.tp_pct = 0.015  # 1.5% Take Profit
        self.sl_pct = 0.008  # 0.8% Stop Loss
        self.trailing_pct = 0.004
        
        # IA y Memoria
        self.model = RandomForestRegressor(n_estimators=100)
        self.memory = []
        self.is_trained = False
        self.default_z_threshold = 2.5
        
        # Gestión de Posiciones
        self.positions = {s: {'in_trade': False, 'entry': 0, 'max_seen': 0} for s in self.symbols}

    async def analyze_market(self, symbol):
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, '1m', limit=50)
            df = pd.DataFrame(ohlcv, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
            
            # Z-Score de Volumen (La Huella de la Ballena)
            v_mean = df['v'].rolling(30).mean().iloc[-1]
            v_std = df['v'].rolling(30).std().iloc[-1]
            z_vol = (df['v'].iloc[-1] - v_mean) / v_std if v_std != 0 else 0
            
            # Absorción: Volumen alto, rango de precio pequeño
            range_pct = (df['h'].iloc[-1] - df['l'].iloc[-1]) / df['o'].iloc[-1]
            abs_detected = z_vol > 3.0 and range_pct < 0.001
            
            return z_vol, abs_detected, df['c'].iloc[-1]
        except Exception as e:
            logger.error(f"Error analizando {symbol}: {e}")
            return 0, False, 0

    async def execute_smart_order(self, symbol, side, size):
        """ Ejecución Maker-First para ahorrar comisiones """
        try:
            ob = await self.exchange.fetch_order_book(symbol, limit=5)
            target_price = ob['bids'][0][0] if side == 'buy' else ob['asks'][0][0]
            
            logger.info(f"🛡️ Colocando LIMIT {side} en {target_price}")
            order = await self.exchange.create_limit_order(symbol, side, size, target_price)
            
            await asyncio.sleep(8) # Esperamos a que el mercado nos toque
            
            check = await self.exchange.fetch_order(order['id'], symbol)
            if check['status'] == 'closed':
                return target_price
            else:
                await self.exchange.cancel_order(order['id'], symbol)
                logger.warning("🏃 Precio escapado. Entrando a MARKET.")
                m_order = await self.exchange.create_market_order(symbol, side, size)
                return m_order.get('average', m_order.get('price'))
        except Exception as e:
            logger.error(f"Fallo en ejecución: {e}")
            return None

    async def run(self):
        logger.info("🚀 SISTEMA LIVE - OPERANDO DINERO REAL EN BINGX")
        while True:
            for symbol in self.symbols:
                z, abs_on, price = await self.analyze_market(symbol)
                
                # IA ajustando el umbral
                hour = datetime.now().hour
                threshold = self.default_z_threshold # Aquí podrías meter self.model.predict si está entrenado
                
                if not self.positions[symbol]['in_trade']:
                    if z > threshold or abs_on:
                        logger.info(f"🔥 SEÑAL: {symbol} Z:{z:.2f}")
                        entry = await self.execute_smart_order(symbol, 'buy', self.order_size)
                        if entry:
                            self.positions[symbol] = {'in_trade': True, 'entry': entry, 'max_seen': entry}
                
                else:
                    # Gestión de salida
                    pos = self.positions[symbol]
                    if price > pos['max_seen']: self.positions[symbol]['max_seen'] = price
                    
                    if price <= pos['entry'] * (1 - self.sl_pct) or price >= pos['entry'] * (1 + self.tp_pct):
                        logger.info(f"💰 CERRANDO POSICIÓN EN {symbol}")
                        await self.execute_smart_order(symbol, 'sell', self.order_size)
                        self.positions[symbol]['in_trade'] = False

            await asyncio.sleep(10)

if __name__ == "__main__":
    bot = UltimateQuantBot()
    asyncio.run(bot.run())
