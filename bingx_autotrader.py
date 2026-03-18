import os
import time
import hmac
import hashlib
import requests
import logging
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

class BingXAutoTrader:
    def __init__(self):
        self.api_key = os.getenv('BINGX_API_KEY', '')
        self.api_secret = os.getenv('BINGX_API_SECRET', '')
        self.base_url = "https://open-api.bingx.com"
        self.leverage = int(os.getenv('LEVERAGE', '5'))
        self.position_size = float(os.getenv('MAX_POSITION_SIZE', '10'))

    def _generate_signature(self, params):
        query_string = urlencode(params)
        return hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    def execute_trade(self, symbol, direction, price, tp1=None, tp2=None, sl=None):
        symbol_clean = symbol.replace("-", "")
        endpoint = "/openApi/swap/v2/trade/order"
        
        params = {
            "symbol": symbol_clean,
            "side": "BUY" if direction == "LONG" else "SELL",
            "positionSide": "LONG" if direction == "LONG" else "SHORT",
            "type": "MARKET",
            "quantity": round(self.position_size / price, 4),
            "timestamp": int(time.time() * 1000)
        }
        
        params["signature"] = self._generate_signature(params)
        headers = {"X-BX-APIKEY": self.api_key}

        try:
            r = requests.post(f"{self.base_url}{endpoint}", params=params, headers=headers)
            res = r.json()
            if res.get("code") == 0:
                logger.info(f"✅ ORDEN EJECUTADA: {symbol_clean}")
                return True
            else:
                logger.error(f"❌ Error API: {res.get('msg')}")
                return False
        except Exception as e:
            logger.error(f"❌ Error Conexión: {e}")
            return False
