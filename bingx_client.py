"""
BingX Perpetual Futures API Client
Handles authentication, orders, market data
"""

import hashlib
import hmac
import time
import urllib.parse
import httpx
import asyncio
from typing import Optional
from loguru import logger


class BingXClient:
    BASE_URL = "https://open-api.bingx.com"

    def __init__(self, api_key: str, secret_key: str, demo: bool = False):
        self.api_key = api_key
        self.secret_key = secret_key
        self.demo = demo
        if demo:
            self.BASE_URL = "https://open-api-vst.bingx.com"
        self.client = httpx.AsyncClient(timeout=15)

    def _sign(self, params: dict) -> str:
        params["timestamp"] = int(time.time() * 1000)
        query = urllib.parse.urlencode(sorted(params.items()))
        signature = hmac.new(
            self.secret_key.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        return query + "&signature=" + signature

    async def _get(self, path: str, params: dict = {}) -> dict:
        signed = self._sign(params)
        url = f"{self.BASE_URL}{path}?{signed}"
        headers = {"X-BX-APIKEY": self.api_key}
        resp = await self.client.get(url, headers=headers)
        return resp.json()

    async def _post(self, path: str, params: dict = {}) -> dict:
        signed = self._sign(params)
        url = f"{self.BASE_URL}{path}"
        headers = {
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        resp = await self.client.post(url, headers=headers, content=signed)
        return resp.json()

    async def _delete(self, path: str, params: dict = {}) -> dict:
        signed = self._sign(params)
        url = f"{self.BASE_URL}{path}?{signed}"
        headers = {"X-BX-APIKEY": self.api_key}
        resp = await self.client.delete(url, headers=headers)
        return resp.json()

    # ─── Market Data ───────────────────────────────────────────────────────────

    async def get_all_tickers(self) -> list[dict]:
        """All perpetual futures tickers with 24h stats"""
        data = await self._get("/openApi/swap/v2/quote/ticker")
        return data.get("data", [])

    async def get_klines(
        self, symbol: str, interval: str = "15m", limit: int = 300
    ) -> list:
        """OHLCV candles. interval: 1m,3m,5m,15m,30m,1h,2h,4h,6h,12h,1d"""
        data = await self._get(
            "/openApi/swap/v3/quote/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        return data.get("data", [])

    async def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        data = await self._get(
            "/openApi/swap/v2/quote/depth", {"symbol": symbol, "limit": limit}
        )
        return data.get("data", {})

    async def get_mark_price(self, symbol: str) -> float:
        data = await self._get(
            "/openApi/swap/v2/quote/premiumIndex", {"symbol": symbol}
        )
        return float(data.get("data", {}).get("markPrice", 0))

    # ─── Account ───────────────────────────────────────────────────────────────

    async def get_balance(self) -> dict:
        data = await self._get("/openApi/swap/v2/user/balance")
        return data.get("data", {}).get("balance", {})

    async def get_positions(self, symbol: str = "") -> list:
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = await self._get("/openApi/swap/v2/user/positions", params)
        return data.get("data", [])

    async def get_open_orders(self, symbol: str) -> list:
        data = await self._get(
            "/openApi/swap/v2/trade/openOrders", {"symbol": symbol}
        )
        return data.get("data", {}).get("orders", [])

    # ─── Trading ───────────────────────────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        side: str,            # BUY / SELL
        position_side: str,   # LONG / SHORT
        order_type: str,      # MARKET / LIMIT
        quantity: float,
        price: float = 0,
        stop_loss: float = 0,
        take_profit: float = 0,
        reduce_only: bool = False,
    ) -> dict:
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": order_type,
            "quantity": quantity,
        }
        if order_type == "LIMIT":
            params["price"] = price
            params["timeInForce"] = "GTC"
        if stop_loss:
            params["stopLoss"] = f'{{"type":"STOP_MARKET","stopPrice":{stop_loss},"price":{stop_loss},"workingType":"MARK_PRICE"}}'
        if take_profit:
            params["takeProfit"] = f'{{"type":"TAKE_PROFIT_MARKET","stopPrice":{take_profit},"price":{take_profit},"workingType":"MARK_PRICE"}}'
        if reduce_only:
            params["reduceOnly"] = "true"

        logger.info(f"[ORDER] {side} {position_side} {quantity} {symbol} @ {order_type}")
        if self.demo:
            logger.warning("[DEMO MODE] Order not sent to exchange")
            return {"code": 0, "data": {"orderId": "DEMO_" + str(int(time.time()))}}

        return await self._post("/openApi/swap/v2/trade/order", params)

    async def close_position(self, symbol: str, position_side: str, quantity: float) -> dict:
        """Market close a position"""
        side = "SELL" if position_side == "LONG" else "BUY"
        return await self.place_order(
            symbol=symbol,
            side=side,
            position_side=position_side,
            order_type="MARKET",
            quantity=quantity,
            reduce_only=True,
        )

    async def cancel_order(self, symbol: str, order_id: str) -> dict:
        return await self._delete(
            "/openApi/swap/v2/trade/order",
            {"symbol": symbol, "orderId": order_id},
        )

    async def cancel_all_orders(self, symbol: str) -> dict:
        return await self._delete(
            "/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol}
        )

    async def set_leverage(self, symbol: str, leverage: int, side: str = "LONG") -> dict:
        return await self._post(
            "/openApi/swap/v2/trade/leverage",
            {"symbol": symbol, "side": side, "leverage": leverage},
        )

    async def close(self):
        await self.client.aclose()
