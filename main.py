import os, asyncio, logging, requests
from bingx_autotrader import BingXAutoTrader
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)
load_dotenv()

class BotRentable:
    def __init__(self):
        self.symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'AVAX-USDT', 'LINK-USDT']
        self.trader = BingXAutoTrader()
        self.interval = 60 # Revisar cada minuto

    def get_market_analysis(self, symbol):
        """Filtro de tendencia e impulso"""
        try:
            # Pedimos K-lines (velas) para calcular una media móvil simple
            url = f"https://open-api.bingx.com/openApi/swap/v2/quote/klines?symbol={symbol.replace('-','')}&interval=1h&limit=20"
            data = requests.get(url).json()['data']
            closes = [float(candle['close']) for candle in data]
            
            current_price = closes[-1]
            sma = sum(closes) / len(closes) # Media simple de 20 periodos
            
            # LÓGICA RENTABLE:
            # Solo LONG si el precio > media y hay fuerza alcista
            # Solo SHORT si el precio < media y hay fuerza bajista
            change_24h = ((closes[-1] - closes[0]) / closes[0]) * 100

            if current_price > sma and change_24h > 1.5:
                return "LONG", current_price
            elif current_price < sma and change_24h < -1.5:
                return "SHORT", current_price
            
            return None, current_price
        except:
            return None, 0

    async def run(self):
        logger.info("🚀 Bot Rentable v3.0 Iniciado con Filtro de Tendencia")
        while True:
            for symbol in self.symbols:
                direction, price = self.get_market_analysis(symbol)
                
                if direction:
                    # Definimos un SL técnico (0.8% de distancia)
                    sl = price * 0.992 if direction == "LONG" else price * 1.008
                    
                    logger.info(f"⚡ Señal Confirmada: {symbol} {direction} a {price}")
                    success = self.trader.execute_trade(symbol, direction, price, sl)
                    
                    if success:
                        # Esperar para no duplicar trades en el mismo par
                        await asyncio.sleep(5) 
                
            await asyncio.sleep(self.interval)

if __name__ == "__main__":
    asyncio.run(BotRentable().run())
