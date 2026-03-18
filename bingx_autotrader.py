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
        """Genera la firma HMAC SHA256 requerida por BingX"""
        query_string = urlencode(params)
        return hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    def execute_trade(self, symbol, direction, price, tp1, tp2, sl):
        """Ejecuta una orden de mercado en Futuros Perpetuos"""
        endpoint = "/openApi/swap/v2/trade/order"
        
        # Preparar parámetros básicos
        # BingX requiere timestamp en milisegundos exactos
        params = {
            "symbol": symbol.replace("-", ""), # Convierte BTC-USDT a BTCUSDT
            "side": "BUY" if direction == "LONG" else "SELL",
            "positionSide": "LONG" if direction == "LONG" else "SHORT",
            "type": "MARKET",
            "quantity": self.position_size / price, # Cantidad en la moneda base
            "timestamp": int(time.time() * 1000)
        }
        
        # Generar firma e incluirla en los parámetros
        params["signature"] = self._generate_signature(params)
        
        headers = {
            "X-BX-APIKEY": self.api_key
        }

        try:
            url = f"{self.base_url}{endpoint}"
            response = requests.post(url, params=params, headers=headers)
            res_data = response.json()
            
            if res_data.get("code") == 0:
                logger.info(f"✅ Orden exitosa en BingX para {symbol}")
                return True
            else:
                logger.error(f"❌ Error de BingX: {res_data.get('msg')} (Código: {res_data.get('code')})")
                return False
        except Exception as e:
            logger.error(f"❌ Error de conexión: {e}")
            return False
