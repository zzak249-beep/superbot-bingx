"""
BingX Perpetual Futures API Client — bingx_client.py
SuperBot v4 — fixed signature + Scanner compatibility

FIXES:
  - Signature: NO sorted() — BingX signs params in insertion order
  - POST: params go in query string (not form body) per BingX v2 spec
  - Added get_symbols() for Scanner compatibility
  - Robust get_balance() handling multiple response shapes
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
    def __init__(self, api_key: str, api_secret: str, mode: str = "hedge"):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.mode       = mode.lower().strip()
        self._session   = requests.Session()
        self._session.headers.update({"X-BX-APIKEY": self.api_key})

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _sign(self, params: dict) -> str:
        # CRITICAL: do NOT sort — BingX signs params in insertion order
        query = urlencode(params)
        return hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()

    def _get(self, path: str, params: dict = None) -> dict:
        p = dict(params or {})
        p["timestamp"] = int(time.time() * 1000)
        p["signature"] = self._sign(p)
        try:
            r = self._session.get(BASE_URL + path, params=p, timeout=10)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                log.warning(f"GET {path} returned list: {str(data)[:120]}")
                return {}
            return data
        except Exception as e:
            log.error(f"GET {path} error: {e}")
            raise

    def _post(self, path: str, payload: dict = None) -> dict:
        # BingX v2: ALL params (including signature) go in query string
        p = dict(payload or {})
        p["timestamp"] = int(time.time() * 1000)
        p["signature"] = self._sign(p)
        try:
            r = self._session.post(BASE_URL + path, params=p, timeout=10)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                log.warning(f"POST {path} returned list: {str(data)[:120]}")
                return {}
            return data
        except Exception as e:
            log.error(f"POST {path} error: {e}")
            raise

    def _pos_side(self, direction: str) -> str:
        return "BOTH" if self.mode == "oneway" else direction

    # ── Market data (no auth needed) ──────────────────────────────────────────

    def get_symbols(self) -> list:
        """All active USDT perpetual symbols. Called by Scanner."""
        try:
            res = requests.get(
                BASE_URL + "/openApi/swap/v2/quote/contracts", timeout=10
            ).json()
            if res.get("code") != 0:
                return []
            return [
                c["symbol"] for c in res.get("data", [])
                if c.get("symbol", "").endswith("-USDT")
                and c.get("status", 0) == 1
            ]
        except Exception as e:
            log.error(f"get_symbols error: {e}")
            return []

    def get_klines(self, symbol: str, interval: str = "15m",
                   limit: int = 200) -> list:
        try:
            res = requests.get(
                BASE_URL + "/openApi/swap/v3/quote/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
                timeout=10,
            ).json()
            return res.get("data", []) if res.get("code") == 0 else []
        except Exception as e:
            log.error(f"get_klines {symbol}: {e}")
            return []

    def get_ticker(self, symbol: str) -> dict:
        try:
            res = requests.get(
                BASE_URL + "/openApi/swap/v2/quote/price",
                params={"symbol": symbol}, timeout=5,
            ).json()
            if res.get("code") == 0:
                d = res.get("data", {})
                if "price" in d and "lastPrice" not in d:
                    d["lastPrice"] = d["price"]
                return d
        except Exception as e:
            log.error(f"get_ticker {symbol}: {e}")
        return {}

    def get_funding_rate(self, symbol: str) -> float:
        try:
            res = requests.get(
                BASE_URL + "/openApi/swap/v2/quote/premiumIndex",
                params={"symbol": symbol}, timeout=5,
            ).json()
            if res.get("code") == 0:
                return float(res["data"].get("lastFundingRate", 0))
        except Exception:
            pass
        return 0.0

    def get_symbol_info(self, symbol: str) -> dict:
        try:
            res = requests.get(
                BASE_URL + "/openApi/swap/v2/quote/contracts", timeout=10
            ).json()
            for c in res.get("data", []):
                if c.get("symbol") == symbol:
                    return c
        except Exception as e:
            log.error(f"get_symbol_info {symbol}: {e}")
        return {}

    # ── Account ───────────────────────────────────────────────────────────────

    def get_balance(self) -> float:
        res = self._get("/openApi/swap/v2/user/balance")
        try:
            data = res.get("data", {})
            if isinstance(data, dict):
                bal = data.get("balance", data)
                if isinstance(bal, dict):
                    for key in ("availableMargin", "available",
                                "availableBalance", "equity"):
                        v = bal.get(key)
                        if v is not None:
                            return float(v)
                for key in ("availableMargin", "available",
                            "availableBalance", "equity"):
                    v = data.get(key)
                    if v is not None:
                        return float(v)
            log.warning(f"get_balance unexpected: {res}")
            return 0.0
        except Exception as e:
            log.error(f"get_balance parse error: {e} | raw={res}")
            return 0.0

    def get_positions(self) -> list:
        res = self._get("/openApi/swap/v2/user/positions")
        try:
            data = res.get("data", [])
            return data if isinstance(data, list) else []
        except Exception as e:
            log.error(f"get_positions error: {e}")
            return []

    def get_open_orders(self) -> list:
        res = self._get("/openApi/swap/v2/trade/openOrders", {})
        try:
            data = res.get("data", {})
            if isinstance(data, list):
                return data
            for key in ("orders", "list"):
                v = data.get(key)
                if isinstance(v, list):
                    return v
            return []
        except Exception as e:
            log.error(f"get_open_orders error: {e}")
            return []

    # ── Trade setup ───────────────────────────────────────────────────────────

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED"):
        try:
            self._post("/openApi/swap/v2/trade/marginType",
                       {"symbol": symbol, "marginType": margin_type})
        except Exception as e:
            log.debug(f"set_margin_type {symbol}: {e}")

    def set_leverage(self, symbol: str, leverage: int):
        for side in ("LONG", "SHORT"):
            try:
                self._post("/openApi/swap/v2/trade/leverage",
                           {"symbol": symbol, "side": side, "leverage": leverage})
            except Exception as e:
                log.debug(f"set_leverage {symbol} {side}: {e}")

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(self, symbol, side, position_side, order_type="MARKET",
                    quantity=0.0, price=None, stop_loss=None,
                    take_profit=None, client_order_id=None) -> dict:
        payload = {
            "symbol":       symbol,
            "side":         side,
            "positionSide": self._pos_side(position_side),
            "type":         order_type,
            "quantity":     quantity,
        }
        if price and order_type == "LIMIT":
            payload["price"]       = price
            payload["timeInForce"] = "GTC"
        if stop_loss:
            payload["stopLoss"]   = str(stop_loss)
        if take_profit:
            payload["takeProfit"] = str(take_profit)
        if client_order_id:
            payload["clientOrderID"] = client_order_id
        return self._post("/openApi/swap/v2/trade/order", payload)

    def place_trailing_stop(self, symbol, side, position_side,
                            quantity, activation_price, price_rate) -> dict:
        payload = {
            "symbol":          symbol,
            "side":            side,
            "positionSide":    self._pos_side(position_side),
            "type":            "TRAILING_STOP_MARKET",
            "quantity":        quantity,
            "activationPrice": activation_price,
            "callbackRate":    round(price_rate * 100, 4),
            "reduceOnly":      "true",
        }
        return self._post("/openApi/swap/v2/trade/order", payload)

    def close_position_partial(self, symbol: str, direction: str,
                               qty: float) -> dict:
        return self._post("/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         "SELL" if direction == "LONG" else "BUY",
            "positionSide": self._pos_side(direction),
            "type":         "MARKET",
            "quantity":     qty,
            "reduceOnly":   "true",
        })

    def update_sl(self, symbol: str, direction: str, new_sl: float) -> dict:
        try:
            self._post("/openApi/swap/v2/trade/allOpenOrders",
                       {"symbol": symbol})
        except Exception:
            pass
        return self._post("/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         "SELL" if direction == "LONG" else "BUY",
            "positionSide": self._pos_side(direction),
            "type":         "STOP_MARKET",
            "stopPrice":    new_sl,
            "reduceOnly":   "true",
        })

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        return self._post("/openApi/swap/v2/trade/cancelOrder",
                          {"symbol": symbol, "orderId": order_id})
