import os, time, hmac, hashlib, requests, logging
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

class BingXAutoTrader:
    def __init__(self):
        self.api_key = os.getenv('BINGX_API_KEY', '')
        self.api_secret = os.getenv('BINGX_API_SECRET', '')
        self.base_url = "https://open-api.bingx.com"
        self.leverage = int(os.getenv('LEVERAGE', '5'))

    def _generate_signature(self, params):
        query_string = urlencode(params)
        return hmac.new(self.api_secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()

    def update_stop_loss(self, symbol, direction, new_sl):
        """Mueve el Stop Loss para proteger ganancias (Trailing Stop)"""
        symbol_clean = symbol.replace("-", "")
        endpoint = "/openApi/swap/v2/trade/order" # En V2 se usa el mismo endpoint para SL/TP
        
        params = {
            "symbol": symbol_clean,
            "type": "STOP_LOSS",
            "side": "SELL" if direction == "LONG" else "BUY",
            "stopPrice": round(new_sl, 4),
            "timestamp": int(time.time() * 1000)
        }
        
        params["signature"] = self._generate_signature(params)
        try:
            r = requests.post(f"{self.base_url}{endpoint}", params=params, headers={"X-BX-APIKEY": self.api_key})
            return r.json().get("code") == 0
        except:
            return False

    def execute_trade(self, symbol, direction, price, sl):
        """Ejecuta orden inicial"""
        symbol_clean = symbol.replace("-", "")
        margin = float(os.getenv('MAX_POSITION_SIZE', '10'))
        quantity = (margin * self.leverage) / price

        params = {
            "symbol": symbol_clean,
            "side": "BUY" if direction == "LONG" else "SELL",
            "positionSide": "LONG" if direction == "LONG" else "SHORT",
            "type": "MARKET",
            "quantity": round(quantity, 4),
            "timestamp": int(time.time() * 1000)
        }
        
        params["signature"] = self._generate_signature(params)
        r = requests.post(f"{self.base_url}/openApi/swap/v2/trade/order", params=params, headers={"X-BX-APIKEY": self.api_key})
        return r.json().get("code") == 0
