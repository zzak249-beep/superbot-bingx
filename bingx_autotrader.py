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
        """Firma HMAC SHA256 obligatoria"""
        query_string = urlencode(params)
        return hmac.new(self.api_secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()

    def execute_trade(self, symbol, direction, price):
        """Ejecuta orden de mercado con limpieza de símbolo"""
        symbol_clean = symbol.replace("-", "") # Convierte BTC-USDT a BTCUSDT
        endpoint = "/openApi/swap/v2/trade/order"
        
        # 1. Configurar apalancamiento primero (Evita errores de margen)
        self._set_leverage(symbol_clean)

        # 2. Parámetros de la orden
        params = {
            "symbol": symbol_clean,
            "side": "BUY" if direction == "LONG" else "SELL",
            "positionSide": "LONG" if direction == "LONG" else "SHORT",
            "type": "MARKET",
            "quantity": float(os.getenv('MAX_POSITION_SIZE', '10')) / price,
            "timestamp": int(time.time() * 1000)
        }
        
        params["signature"] = self._generate_signature(params)
        
        try:
            r = requests.post(f"{self.base_url}{endpoint}", params=params, headers={"X-BX-APIKEY": self.api_key})
            res = r.json()
            if res.get("code") == 0:
                logger.info(f"✅ ORDEN EXITOSA: {symbol_clean} {direction}")
                return True
            logger.error(f"❌ Error BingX: {res.get('msg')}")
            return False
        except Exception as e:
            logger.error(f"❌ Error de red: {e}")
            return False

    def _set_leverage(self, symbol):
        params = {"symbol": symbol, "leverage": self.leverage, "timestamp": int(time.time() * 1000)}
        params["signature"] = self._generate_signature(params)
        requests.post(f"{self.base_url}/openApi/swap/v2/trade/leverage", params=params, headers={"X-BX-APIKEY": self.api_key})
