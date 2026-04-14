"""
BingX Perpetual Futures API Client — bingx_client.py
Class-based wrapper for bot.py / SuperBot v4.

Methods expected by bot.py:
  get_balance()
  get_positions()
  get_symbol_info(symbol)
  get_ticker(symbol)
  get_open_orders()
  set_margin_type(symbol, margin_type)
  set_leverage(symbol, leverage)
  place_order(symbol, side, position_side, order_type, quantity,
              stop_loss, take_profit, client_order_id)
  place_trailing_stop(symbol, side, position_side, quantity,
                      activation_price, price_rate)
  close_position_partial(symbol, direction, qty)
  update_sl(symbol, direction, new_sl)
  cancel_order(symbol, order_id)
"""

import time
import hmac
import hashlib
import logging
import requests
from urllib.parse import urlencode

log = logging.getLogger("BingXClient")

BASE_URL = "https://open-api.bingx.com"


class BingXClient:
    def __init__(self, api_key: str, api_secret: str,
                 mode: str = "hedge", recv_window: int = 5000):
        """
        mode: "hedge" (LONG/SHORT positionSide) or "oneway" (BOTH)
        """
        self.api_key    = api_key
        self.api_secret = api_secret
        self.mode       = mode.lower().strip()
        self.recv_window = recv_window
        self._session   = requests.Session()
        self._session.headers.update({"X-BX-APIKEY": self.api_key})

    # ── Auth helpers ──────────────────────────────────────────────────────────

    def _sign(self, params: dict) -> str:
        query = urlencode(sorted(params.items()))
        return hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()

    def _get(self, path: str, params: dict = None) -> dict:
        params = dict(params or {})
        params["timestamp"]  = int(time.time() * 1000)
        params["recvWindow"] = self.recv_window
        params["signature"]  = self._sign(params)
        try:
            r = self._session.get(BASE_URL + path, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                # Some BingX endpoints wrap in list by mistake — unwrap
                log.warning(f"GET {path} returned list, expected dict: {data}")
                return {}
            return data
        except Exception as e:
            log.error(f"GET {path} error: {e}")
            raise

    def _post(self, path: str, payload: dict = None) -> dict:
        payload = dict(payload or {})
        payload["timestamp"]  = int(time.time() * 1000)
        payload["recvWindow"] = self.recv_window
        payload["signature"]  = self._sign(payload)
        try:
            r = self._session.post(BASE_URL + path, data=payload, timeout=10)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                log.warning(f"POST {path} returned list, expected dict: {data}")
                return {}
            return data
        except Exception as e:
            log.error(f"POST {path} error: {e}")
            raise

    def _pos_side(self, direction: str) -> str:
        """Return positionSide based on account mode."""
        if self.mode == "oneway":
            return "BOTH"
        return direction  # "LONG" or "SHORT"

    # ── Account ───────────────────────────────────────────────────────────────

    def get_balance(self) -> float:
        """Returns available margin (USDT) for perpetual futures."""
        res = self._get("/openApi/swap/v2/user/balance")
        try:
            # Typical structure: res["data"]["balance"]["availableMargin"]
            data = res.get("data", {})
            # BingX sometimes nests differently depending on account type
            if isinstance(data, dict):
                bal = data.get("balance", data)
                if isinstance(bal, dict):
                    for key in ("availableMargin", "available", "availableBalance", "equity"):
                        val = bal.get(key)
                        if val is not None:
                            return float(val)
                # Flat: data = {"availableMargin": "123.45", ...}
                for key in ("availableMargin", "available", "availableBalance", "equity"):
                    val = data.get(key)
                    if val is not None:
                        return float(val)
            log.warning(f"get_balance: unexpected structure: {res}")
            return 0.0
        except Exception as e:
            log.error(f"get_balance parse error: {e} | raw={res}")
            return 0.0

    def get_positions(self) -> list:
        """Returns all open perpetual positions."""
        res = self._get("/openApi/swap/v2/user/positions")
        try:
            data = res.get("data", [])
            if isinstance(data, list):
                return data
            return []
        except Exception as e:
            log.error(f"get_positions error: {e}")
            return []

    def get_symbol_info(self, symbol: str) -> dict:
        """Returns contract metadata (quantityPrecision, pricePrecision, etc.)"""
        res = self._get("/openApi/swap/v2/quote/contracts")
        try:
            for c in res.get("data", []):
                if c.get("symbol") == symbol:
                    return c
        except Exception as e:
            log.error(f"get_symbol_info error: {e}")
        return {}

    def get_ticker(self, symbol: str) -> dict:
        """Returns latest ticker (lastPrice, etc.)"""
        res = self._get(
            "/openApi/swap/v2/quote/price", {"symbol": symbol}
        )
        try:
            data = res.get("data", {})
            if isinstance(data, dict):
                return data
            return {}
        except Exception as e:
            log.error(f"get_ticker error: {e}")
            return {}

    def get_open_orders(self) -> list:
        """Returns all open orders across all symbols."""
        res = self._get("/openApi/swap/v2/trade/openOrders")
        try:
            data = res.get("data", {})
            if isinstance(data, list):
                return data
            # Sometimes nested under "orders"
            orders = data.get("orders", data.get("list", []))
            return orders if isinstance(orders, list) else []
        except Exception as e:
            log.error(f"get_open_orders error: {e}")
            return []

    # ── Trade setup ───────────────────────────────────────────────────────────

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED"):
        """Set margin type: ISOLATED or CROSSED."""
        try:
            self._post("/openApi/swap/v2/trade/marginType", {
                "symbol":     symbol,
                "marginType": margin_type,
            })
        except Exception as e:
            log.debug(f"set_margin_type {symbol}: {e} (non-critical)")

    def set_leverage(self, symbol: str, leverage: int):
        """Set leverage for both sides."""
        for side in ("LONG", "SHORT"):
            try:
                self._post("/openApi/swap/v2/trade/leverage", {
                    "symbol":   symbol,
                    "side":     side,
                    "leverage": leverage,
                })
            except Exception as e:
                log.debug(f"set_leverage {symbol} {side}: {e}")

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: str,
        position_side: str,
        order_type: str = "MARKET",
        quantity: float = 0.0,
        price: float = None,
        stop_loss: float = None,
        take_profit: float = None,
        client_order_id: str = None,
    ) -> dict:
        """
        Place entry order. Returns raw API response dict.
        order_type: MARKET | LIMIT
        """
        payload = {
            "symbol":       symbol,
            "side":         side,
            "positionSide": self._pos_side(position_side),
            "type":         order_type,
            "quantity":     quantity,
        }
        if price and order_type == "LIMIT":
            payload["price"] = price
            payload["timeInForce"] = "GTC"
        if stop_loss:
            payload["stopLoss"]   = str(stop_loss)
        if take_profit:
            payload["takeProfit"] = str(take_profit)
        if client_order_id:
            payload["clientOrderID"] = client_order_id

        res = self._post("/openApi/swap/v2/trade/order", payload)
        log.debug(f"place_order {symbol}: {res}")
        return res

    def place_trailing_stop(
        self,
        symbol: str,
        side: str,
        position_side: str,
        quantity: float,
        activation_price: float,
        price_rate: float,
    ) -> dict:
        """
        Place a trailing stop order.
        price_rate: callback rate as decimal (e.g. 0.025 = 2.5%)
        """
        payload = {
            "symbol":          symbol,
            "side":            side,
            "positionSide":    self._pos_side(position_side),
            "type":            "TRAILING_STOP_MARKET",
            "quantity":        quantity,
            "activationPrice": activation_price,
            "callbackRate":    round(price_rate * 100, 4),  # BingX expects % value
            "reduceOnly":      "true",
        }
        res = self._post("/openApi/swap/v2/trade/order", payload)
        log.debug(f"place_trailing_stop {symbol}: {res}")
        return res

    def close_position_partial(
        self, symbol: str, direction: str, qty: float
    ) -> dict:
        """Close (reduce) part of a position at market price."""
        close_side = "SELL" if direction == "LONG" else "BUY"
        payload = {
            "symbol":       symbol,
            "side":         close_side,
            "positionSide": self._pos_side(direction),
            "type":         "MARKET",
            "quantity":     qty,
            "reduceOnly":   "true",
        }
        res = self._post("/openApi/swap/v2/trade/order", payload)
        log.debug(f"close_position_partial {symbol} {qty}: {res}")
        return res

    def update_sl(self, symbol: str, direction: str, new_sl: float) -> dict:
        """
        Update stop-loss: cancel all open SL orders and place a new STOP_MARKET.
        """
        # Cancel existing conditional orders for symbol
        try:
            self._post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})
        except Exception as e:
            log.debug(f"cancel orders before update_sl {symbol}: {e}")

        close_side = "SELL" if direction == "LONG" else "BUY"
        payload = {
            "symbol":       symbol,
            "side":         close_side,
            "positionSide": self._pos_side(direction),
            "type":         "STOP_MARKET",
            "stopPrice":    new_sl,
            "reduceOnly":   "true",
        }
        res = self._post("/openApi/swap/v2/trade/order", payload)
        log.debug(f"update_sl {symbol} → {new_sl}: {res}")
        return res

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        """Cancel a specific order by orderId."""
        payload = {
            "symbol":  symbol,
            "orderId": order_id,
        }
        res = self._post("/openApi/swap/v2/trade/cancelOrder", payload)
        log.debug(f"cancel_order {symbol} {order_id}: {res}")
        return res
