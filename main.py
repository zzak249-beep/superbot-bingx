import os, asyncio, logging, requests
from bingx_autotrader import BingXAutoTrader
from dotenv import load_dotenv

# Configuración de logs limpia
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)
load_dotenv()

class BotMaestro:
    def __init__(self):
        # Aseguramos que los símbolos no tengan guiones para la API de BingX
        self.symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'AVAXUSDT']
        self.trader = BingXAutoTrader()
        self.active_trades = {}
        self.interval = int(os.getenv('CHECK_INTERVAL', '60'))

    def get_price_safe(self, symbol):
        """Obtiene el precio de forma segura manejando errores de clave"""
        try:
            url = f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?symbol={symbol}"
            response = requests.get(url, timeout=10)
            data = response.json()

            # Verificamos si la API respondió con éxito
            if data.get('code') == 0 and 'data' in data:
                # El campo correcto es 'lastPrice' (siempre en inglés)
                ticker_data = data['data']
                
                # Manejo de si la API devuelve una lista o un diccionario
                if isinstance(ticker_data, list):
                    return float(ticker_data[0]['lastPrice']), float(ticker_data[0]['priceChangePercent'])
                else:
                    return float(ticker_data['lastPrice']), float(ticker_data['priceChangePercent'])
            else:
                logger.error(f"❌ Error API en {symbol}: {data.get('msg', 'Sin mensaje')}")
                return None, None
        except Exception as e:
            logger.error(f"❌ Error de conexión en {symbol}: {str(e)}")
            return None, None

    async def run(self):
        logger.info("🤖 Master Bot v5.1 - Iniciado (Modo Robusto)")
        while True:
            for symbol in self.symbols:
                price, change = self.get_price_safe(symbol)
                
                if price is None:
                    continue

                logger.info(f"📊 {symbol}: ${price} ({change}%)")

                # Lógica de entrada
                direction = None
                if change > 2.0: direction = "LONG"
                elif change < -2.0: direction = "SHORT"

                if direction and symbol not in self.active_trades:
                    # Llamada al trader (asegúrate de que execute_trade acepte estos params)
                    if self.trader.execute_trade(symbol, direction, price):
                        self.active_trades[symbol] = {'entry': price, 'direction': direction}
                        logger.info(f"🚀 Posición abierta: {symbol} {direction}")

            await asyncio.sleep(self.interval)

if __name__ == "__main__":
    asyncio.run(BotMaestro().run())
