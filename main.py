import os, asyncio, logging, requests
from bingx_autotrader import BingXAutoTrader
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)
load_dotenv()

class BotMaestro:
    def __init__(self):
        self.symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'AVAX-USDT']
        self.trader = BingXAutoTrader()
        self.active_trades = {} # Seguimiento para Trailing Stop

    async def check_trailing_stop(self, symbol, current_price):
        """Si ganamos > 0.6%, protegemos la entrada"""
        if symbol in self.active_trades:
            trade = self.active_trades[symbol]
            entry = trade['entry']
            
            # Lógica simple de protección
            if trade['direction'] == "LONG" and current_price > entry * 1.006:
                logger.info(f"🛡️ {symbol}: Protegiendo ganancias (Precio subió 0.6%)")
                # Aquí podrías llamar a una función de cierre o mover SL
                # Por ahora, marcamos como protegido
                self.active_trades[symbol]['protected'] = True

    async def run(self):
        logger.info("🤖 Master Bot v5.0 Iniciado")
        while True:
            for symbol in self.symbols:
                try:
                    # 1. Obtener datos reales
                    url = f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?symbol={symbol.replace('-','')}"
                    data = requests.get(url).json()['data']
                    price = float(data['lastPrice'])
                    change = float(data['priceChangePercent'])

                    # 2. Gestionar trades activos
                    await self.check_trailing_stop(symbol, price)

                    # 3. Lógica de Entrada (Filtro de Tendencia)
                    direction = None
                    if change > 2.0: direction = "LONG"
                    elif change < -2.0: direction = "SHORT"

                    if direction and symbol not in self.active_trades:
                        if self.trader.execute_trade(symbol, direction, price):
                            self.active_trades[symbol] = {'entry': price, 'direction': direction, 'protected': False}
                            
                except Exception as e:
                    logger.error(f"Error en {symbol}: {e}")
                
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(BotMaestro().run())
