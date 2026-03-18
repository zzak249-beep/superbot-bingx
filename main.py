import os, asyncio, logging, requests
from bingx_autotrader import BingXAutoTrader
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)
load_dotenv()

class BotMaestro:
    def __init__(self):
        # Para la consulta de PRECIO usamos el formato con guion
        self.symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'AVAX-USDT']
        self.trader = BingXAutoTrader()
        self.active_trades = {}
        self.interval = int(os.getenv('CHECK_INTERVAL', '60'))

    def get_price_safe(self, symbol):
        """Obtiene el precio usando el formato que BingX exige (-USDT)"""
        try:
            # Endpoint de V2 Quote Ticker
            url = f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?symbol={symbol}"
            response = requests.get(url, timeout=10)
            data = response.json()

            if data.get('code') == 0 and 'data' in data:
                ticker = data['data']
                # Si viene en lista, agarramos el primero; si no, el objeto
                d = ticker[0] if isinstance(ticker, list) else ticker
                return float(d['lastPrice']), float(d['priceChangePercent'])
            else:
                logger.error(f"❌ Error API en {symbol}: {data.get('msg')}")
                return None, None
        except Exception as e:
            logger.error(f"❌ Error de conexión en {symbol}: {e}")
            return None, None

    async def run(self):
        logger.info("🤖 Master Bot v5.2 - Formato de Símbolos Corregido")
        while True:
            for symbol in self.symbols:
                price, change = self.get_price_safe(symbol)
                
                if price is None: continue

                logger.info(f"📊 {symbol}: ${price} ({change}%)")

                direction = None
                # Estrategia: Umbral de 1.8% para ser más sensible
                if change > 1.8: direction = "LONG"
                elif change < -1.8: direction = "SHORT"

                if direction and symbol not in self.active_trades:
                    # El trader se encarga de limpiar el símbolo para la orden
                    if self.trader.execute_trade(symbol, direction, price):
                        self.active_trades[symbol] = {'entry': price, 'direction': direction}
            
            await asyncio.sleep(self.interval)

if __name__ == "__main__":
    asyncio.run(BotMaestro().run())
