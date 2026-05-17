"""
bingx_client.py — Cliente BingX Perpetual Futures API
Maneja autenticación HMAC-SHA256, klines, órdenes y posiciones.
"""
import hmac
import hashlib
import time
import logging
import requests
import pandas as pd
from urllib.parse import urlencode

from config import BINGX_API_KEY, BINGX_SECRET_KEY, BINGX_BASE_URL, DRY_RUN

logger = logging.getLogger(__name__)


class BingXClient:
    def __init__(self):
        self.api_key    = BINGX_API_KEY
        self.secret_key = BINGX_SECRET_KEY
        self.base_url   = BINGX_BASE_URL
        self.session    = requests.Session()
        self.session.headers.update({
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/x-www-form-urlencoded",
        })

    # ──────────────────────────────────────────────────────
    # Autenticación
    # ──────────────────────────────────────────────────────
    def _timestamp(self) -> int:
        return int(time.time() * 1000)

    def _sign(self, payload: str) -> str:
        return hmac.new(
            self.secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _signed_params(self, params: dict) -> dict:
        """Agrega timestamp y firma al dict de params."""
        params["timestamp"] = self._timestamp()
        query = urlencode(sorted(params.items()))
        params["signature"] = self._sign(query)
        return params

    def _get(self, endpoint: str, params: dict = None, signed: bool = False) -> dict:
        params = params or {}
        if signed:
            params = self._signed_params(params)
        url = f"{self.base_url}{endpoint}"
        try:
            resp = self.session.get(url, params=params, timeout=10)
            return resp.json()
        except Exception as e:
            logger.error(f"GET {endpoint} error: {e}")
            return {"code": -1, "msg": str(e)}

    def _post(self, endpoint: str, params: dict) -> dict:
        params = self._signed_params(params)
        url = f"{self.base_url}{endpoint}"
        try:
            resp = self.session.post(url, data=urlencode(params), timeout=10)
            return resp.json()
        except Exception as e:
            logger.error(f"POST {endpoint} error: {e}")
            return {"code": -1, "msg": str(e)}

    # ──────────────────────────────────────────────────────
    # Datos de mercado (sin firma)
    # ──────────────────────────────────────────────────────
    def get_klines(self, symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
        """Retorna DataFrame OHLCV con columnas estándar."""
        data = self._get(
            "/openApi/swap/v2/quote/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        if data.get("code") != 0 or not data.get("data"):
            logger.warning(f"Klines vacías para {symbol}: {data.get('msg')}")
            return pd.DataFrame()

        df = pd.DataFrame(
            data["data"],
            columns=["time", "open", "high", "low", "close", "volume"],
        )
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        df.sort_values("time", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def get_24h_tickers(self) -> list:
        """Lista completa de tickers de futuros perpetuos."""
        data = self._get("/openApi/swap/v2/quote/ticker")
        if data.get("code") != 0:
            logger.error(f"Tickers error: {data.get('msg')}")
            return []
        return data.get("data", [])

    def get_symbol_price(self, symbol: str) -> float:
        data = self._get("/openApi/swap/v2/quote/price", {"symbol": symbol})
        try:
            return float(data["data"]["price"])
        except Exception:
            return 0.0

    # ──────────────────────────────────────────────────────
    # Cuenta (requieren firma)
    # ──────────────────────────────────────────────────────
    def get_balance(self) -> float:
        """Retorna margen disponible en USDT."""
        data = self._get("/openApi/swap/v2/user/balance", {"currency": "USDT"}, signed=True)
        try:
            return float(data["data"]["balance"]["availableMargin"])
        except Exception:
            logger.error(f"Balance error: {data}")
            return 0.0

    def get_open_positions(self) -> list:
        """Retorna lista de posiciones abiertas."""
        data = self._get("/openApi/swap/v2/user/positions", {}, signed=True)
        if data.get("code") != 0:
            return []
        return [p for p in data.get("data", []) if float(p.get("positionAmt", 0)) != 0]

    # ──────────────────────────────────────────────────────
    # Trading
    # ──────────────────────────────────────────────────────
    def set_leverage(self, symbol: str, leverage: int):
        for side in ("LONG", "SHORT"):
            self._post("/openApi/swap/v2/trade/leverage", {
                "symbol": symbol, "side": side, "leverage": leverage,
            })

    def place_order(
        self,
        symbol: str,
        side: str,           # "BUY" o "SELL"
        position_side: str,  # "LONG" o "SHORT"
        quantity: float,
        leverage: int = 5,
    ) -> dict:
        """Abre una posición de mercado."""
        if DRY_RUN:
            logger.info(f"[DRY_RUN] place_order {side} {symbol} qty={quantity}")
            return {"code": 0, "data": {"orderId": "DRY_RUN"}}

        self.set_leverage(symbol, leverage)
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": round(quantity, 4),
        }
        result = self._post("/openApi/swap/v2/trade/order", params)
        logger.info(f"Order result {symbol}: {result}")
        return result

    def place_stop_order(
        self,
        symbol: str,
        side: str,
        position_side: str,
        stop_price: float,
        quantity: float,
        order_type: str = "STOP_MARKET",
    ) -> dict:
        """Coloca stop-loss o take-profit."""
        if DRY_RUN:
            return {"code": 0}
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": order_type,
            "stopPrice": round(stop_price, 8),
            "quantity": round(quantity, 4),
            "workingType": "MARK_PRICE",
        }
        return self._post("/openApi/swap/v2/trade/order", params)

    def close_position(self, symbol: str, position_side: str, quantity: float) -> dict:
        """Cierra posición existente a mercado."""
        if DRY_RUN:
            logger.info(f"[DRY_RUN] close_position {symbol} {position_side} qty={quantity}")
            return {"code": 0}
        close_side = "SELL" if position_side == "LONG" else "BUY"
        params = {
            "symbol": symbol,
            "side": close_side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": round(quantity, 4),
        }
        return self._post("/openApi/swap/v2/trade/order", params)

    def cancel_all_orders(self, symbol: str) -> dict:
        """Cancela todas las órdenes abiertas de un símbolo."""
        if DRY_RUN:
            return {"code": 0}
        return self._post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})
