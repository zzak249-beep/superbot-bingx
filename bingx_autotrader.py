import os
import time
import hmac
import hashlib
import requests
import logging
from urllib.parse import urlencode

# Configuración de logs para ver qué pasa en la API
logger = logging.getLogger(__name__)

class BingXAutoTrader:
    def __init__(self):
        """Inicializa las credenciales y parámetros de riesgo"""
        self.api_key = os.getenv('BINGX_API_KEY', '')
        self.api_secret = os.getenv('BINGX_API_SECRET', '')
        self.base_url = "https://open-api.bingx.com"
        
        # Parámetros desde Railway o valores por defecto
        self.leverage = int(os.getenv('LEVERAGE', '5'))
        self.position_size = float(os.getenv('MAX_POSITION_SIZE', '10'))

    def _generate_signature(self, params):
        """Genera la firma HMAC SHA256 exacta que exige BingX"""
        query_string = urlencode(params)
        return hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    def _set_leverage(self, symbol):
        """Configura el apalancamiento antes de abrir la orden"""
        endpoint = "/openApi/swap/v2/trade/leverage"
        params = {
            "symbol": symbol,
            "leverage": self.leverage,
            "side": "BOTH",
            "timestamp": int(time.time() * 1000)
        }
        params["signature"] = self._generate_signature(params)
        try:
            requests.post(f"{self.base_url}{endpoint}", params=params, headers={"X-BX-APIKEY": self.api_key})
        except Exception as e:
            logger.error(f"Error ajustando apalancamiento: {e}")

    def update_stop_loss(self, symbol, direction, new_sl):
        """
        Mueve el Stop Loss (Trailing Stop). 
        En BingX V2 se usa el tipo 'STOP_LOSS' en el endpoint de order.
        """
        symbol_clean = symbol.replace("-", "")
        endpoint = "/openApi/swap/v2/trade/order"
        
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
            res = r.json()
            return res.get("code") == 0
        except Exception:
            return False

    def execute_trade(self, symbol, direction, price, sl=None):
        """
        Ejecuta una orden de mercado (MARKET).
        Limpia el símbolo para que la orden no falle.
        """
        symbol_order = symbol.replace("-", "")
        endpoint = "/openApi/swap/v2/trade/order"
        
        # 1. Ajustar apalancamiento primero
        self._set_leverage(symbol_order)

        # 2. Calcular cantidad en base al margen y apalancamiento
        # Cantidad = (Margen * Apalancamiento) / Precio
        quantity = (self.position_size * self.leverage) / price
        
        params = {
            "symbol": symbol_order,
            "side": "BUY" if direction == "LONG" else "SELL",
            "positionSide": "LONG" if direction == "LONG" else "SHORT",
            "type": "MARKET",
            "quantity": round(quantity, 4),
            "timestamp": int(time.time() * 1000)
        }
        
        # La firma debe ser el último paso antes de enviar
        params["signature"] = self._generate_signature(params)
        
        headers = {
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/json"
        }

        try:
            url = f"{self.base_url}{endpoint}"
            response = requests.post(url, params=params, headers=headers)
            res_data = response.json()
            
            if res_data.get("code") == 0:
                logger.info(f"✅ ORDEN EXITOSA en {symbol_order}: {direction}")
                return True
            else:
                logger.error(f"❌ Error de BingX: {res_data.get('msg')} (Código: {res_data.get('code')})")
                return False
        except Exception as e:
            logger.error(f"❌ Error de conexión al ejecutar trade: {e}")
            return False
