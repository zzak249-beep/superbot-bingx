import os, asyncio, logging, requests
from bingx_autotrader import BingXAutoTrader
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)
load_dotenv()

class BotProRentable:
    def __init__(self):
        self.symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT']
        self.trader = BingXAutoTrader()
        self.active_trades = {} # Diccionario para rastrear trades y sus SL

    async def manage_trailing_stop(self, symbol, current_price):
        """Lógica de protección de ganancias"""
        if symbol not in self.active_trades: return

        trade = self.active_trades[symbol]
        entry = trade['entry']
        direction = trade['direction']
        last_sl = trade['sl']

        # Si es LONG y el precio subió 0.6% desde la entrada
        if direction == "LONG":
            if current_price > entry * 1.006 and last_sl < entry:
                # Mover SL al precio de entrada + pequeña comisión (Break-even)
                new_sl = entry * 1.001 
                if self.trader.update_stop_loss(symbol, direction, new_sl):
                    logger.info(f"🛡️ BREAK-EVEN ACTIVADO para {symbol}")
                    self.active_trades[symbol]['sl'] = new_sl

        # Si es SHORT y el precio bajó 0.6%
        elif direction == "SHORT":
            if current_price < entry * 0.994 and last_sl > entry:
                new_sl = entry * 0.999
                if self.trader.update_stop_loss(symbol, direction, new_sl):
                    logger.info(f"🛡️ BREAK-EVEN ACTIVADO para {symbol}")
                    self.active_trades[symbol]['sl'] = new_sl

    async def run(self):
        logger.info("🚀 Bot v4.0 con Trailing Stop activado")
        while True:
            for symbol in self.symbols:
                # 1. Obtener precio actual
                price_url = f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?symbol={symbol.replace('-','')}"
                try:
                    current_price = float(requests.get(price_url).json()['data']['lastPrice'])
                    
                    # 2. Gestionar Trailing Stop de posiciones existentes
                    await self.manage_trailing_stop(symbol, current_price)

                    # 3. Buscar nuevas entradas (Solo si no hay trade activo en ese par)
                    if symbol not in self.active_trades:
                        # ... aquí iría tu lógica de análisis (EMA, RSI, etc) ...
                        # Ejemplo: Si hay señal:
                        # success = self.trader.execute_trade(symbol, direction, current_price, sl_inicial)
                        # if success: self.active_trades[symbol] = {'entry': current_price, 'direction': direction, 'sl': sl_inicial}
                        pass

                except Exception as e:
                    logger.error(f"Error en ciclo: {e}")
            
            await asyncio.sleep(30) # Revisar cada 30 seg para ser más reactivo

if __name__ == "__main__":
    asyncio.run(BotProRentable().run())
