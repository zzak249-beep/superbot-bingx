"""
BingX API Client - Perpetual Futures (Swap)
Optimized for low fees using maker orders
"""
import hmac, hashlib, time, requests, json, os
from urllib.parse import urlencode
from typing import Optional

BASE_URL = "https://open-api.bingx.com"

class BingXClient:
    def __init__(self, api_key: str, secret_key: str):
        self.api_key    = api_key
        self.secret_key = secret_key
        self.session    = requests.Session()
        self.session.headers.update({"X-BX-APIKEY": self.api_key})

    # ── Auth ──────────────────────────────────────────────────────────────
    def _sign(self, params: dict) -> str:
        payload = urlencode(sorted(params.items()))
        return hmac.new(
            self.secret_key.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()

    def _get(self, path: str, params: dict = None) -> dict:
        params = params or {}
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(params)
        r = self.session.get(BASE_URL + path, params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, params: dict = None) -> dict:
        params = params or {}
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(params)
        r = self.session.post(BASE_URL + path, params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    # ── Market Data ───────────────────────────────────────────────────────
    def get_all_symbols(self) -> list[str]:
        """Return all active USDT perpetual symbols."""
        data = self._get("/openApi/swap/v2/quote/contracts")
        return [
            c["symbol"] for c in data.get("data", [])
            if c.get("currency") == "USDT" and c.get("status") == 1
        ]

    def get_klines(self, symbol: str, interval: str = "15m", limit: int = 200) -> list:
        """OHLCV candles. interval: 1m 5m 15m 30m 1h 4h 1d"""
        data = self._get("/openApi/swap/v3/quote/klines", {
            "symbol": symbol, "interval": interval, "limit": limit
        })
        raw = data.get("data", [])
        # [open_time, open, high, low, close, volume, close_time]
        candles = []
        for k in raw:
            candles.append({
                "ts":     int(k[0]),
                "open":   float(k[1]),
                "high":   float(k[2]),
                "low":    float(k[3]),
                "close":  float(k[4]),
                "volume": float(k[5]),
            })
        return candles

    def get_ticker(self, symbol: str) -> dict:
        data = self._get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
        return data.get("data", {})

    def get_24h_volume(self, symbol: str) -> float:
        t = self.get_ticker(symbol)
        return float(t.get("quoteVolume", 0))

    # ── Account ───────────────────────────────────────────────────────────
    def get_balance(self) -> float:
        """Available USDT balance."""
        data = self._get("/openApi/swap/v2/user/balance")
        for a in data.get("data", {}).get("balance", []):
            if a.get("asset") == "USDT":
                return float(a.get("availableMargin", 0))
        return 0.0

    def get_positions(self) -> list:
        data = self._get("/openApi/swap/v2/user/positions")
        return [p for p in data.get("data", []) if float(p.get("positionAmt", 0)) != 0]

    def get_open_orders(self, symbol: str) -> list:
        data = self._get("/openApi/swap/v2/trade/openOrders", {"symbol": symbol})
        return data.get("data", {}).get("orders", [])

    # ── Trading ───────────────────────────────────────────────────────────
    def set_leverage(self, symbol: str, leverage: int, side: str = "LONG") -> dict:
        return self._post("/openApi/swap/v2/trade/leverage", {
            "symbol": symbol, "side": side, "leverage": leverage
        })

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> dict:
        """ISOLATED or CROSSED"""
        return self._post("/openApi/swap/v2/trade/marginType", {
            "symbol": symbol, "marginType": margin_type
        })

    def place_order(
        self,
        symbol: str,
        side: str,         # BUY or SELL
        position_side: str, # LONG or SHORT
        order_type: str,   # LIMIT or MARKET
        quantity: float,
        price: float = None,
        stop_loss: float = None,
        take_profit: float = None,
        reduce_only: bool = False,
    ) -> dict:
        params = {
            "symbol":       symbol,
            "side":         side,
            "positionSide": position_side,
            "type":         order_type,
            "quantity":     quantity,
        }
        if order_type == "LIMIT" and price:
            params["price"]    = price
            params["timeInForce"] = "GTC"  # Maker = lower fees
        if stop_loss:
            params["stopLoss"]   = json.dumps({"type": "STOP_MARKET", "stopPrice": stop_loss, "workingType": "MARK_PRICE"})
        if take_profit:
            params["takeProfit"] = json.dumps({"type": "TAKE_PROFIT_MARKET", "stopPrice": take_profit, "workingType": "MARK_PRICE"})
        if reduce_only:
            params["reduceOnly"] = "true"
        return self._post("/openApi/swap/v2/trade/order", params)

    def cancel_all_orders(self, symbol: str) -> dict:
        return self._post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})

    def close_position(self, symbol: str, position_side: str, quantity: float) -> dict:
        side = "SELL" if position_side == "LONG" else "BUY"
        return self.place_order(
            symbol, side, position_side, "MARKET", quantity, reduce_only=True
        )

    def get_symbol_info(self, symbol: str) -> dict:
        data = self._get("/openApi/swap/v2/quote/contracts")
        for c in data.get("data", []):
            if c["symbol"] == symbol:
                return c
        return {}
