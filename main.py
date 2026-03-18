import os, asyncio, logging, requests
from bingx_autotrader import BingXAutoTrader
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)
load_dotenv()

class BotMaestro:
    def __init__(self):
        # USAMOS EL FORMATO QUE LA API PIDE: Con guion para las consultas
        self.symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'AVAX-USDT']
        self.trader = BingXAutoTrader()
        self.active_trades = {}
        self.interval = int(os.getenv('CHECK_INTERVAL', '60'))

    def get_price_safe(self, symbol):
        """Obtiene el precio usando el formato con guion exigido por BingX"""
        try:
            # URL para obtener precio (Ticker)
            url = f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?symbol={symbol}"
            response = requests.get(url, timeout=10)
            data = response.json()

            if data.get('code') == 0 and 'data' in data:
                ticker = data['data']
                # BingX a veces devuelve una lista, a veces un objeto
                d = ticker[0] if isinstance(ticker, list) else ticker
                return float(d['lastPrice']), float(d['priceChangePercent'])
            else:
                # Si hay error, lo reportamos pero no detenemos el bot
                logger.error(f"❌ Error API en {symbol}: {data.get('msg')}")
                return None, None
        except Exception as e:
            logger.error(f"❌ Error de conexión en {symbol}: {e}")
            return None, None

    async def run(self):
        logger.info("🤖 Master Bot v5.3 - Símbolos con Guion Activados")
        while True:
            for symbol in self.symbols:
                price, change = self.get_price_safe(symbol)
                
                if price is None: continue

                # Log de monitoreo
                logger.info(f"📊 {symbol}: ${price} ({change}%)")

                # Lógica de decisión
                direction = None
                if change > 1.5: direction = "LONG"
                elif change < -1.5: direction = "SHORT"

                if direction and symbol not in self.active_trades:
                    # Le pasamos el símbolo original, el TRADER se encarga de limpiarlo si hace falta
                    if self.trader.execute_trade(symbol, direction, price):
                        self.active_trades[symbol] = {'entry': price, 'direction': direction}
                        logger.info(f"✅ OPERACIÓN REGISTRADA: {symbol} {direction}")
            
            await asyncio.sleep(self.interval)

if __name__ == "__main__":
    asyncio.run(BotMaestro().run())
