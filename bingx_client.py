"""
Cliente BingX Perpetual Futures (API v2)
Documentación: https://bingx-api.github.io/docs/

Fixes v2:
  - Activa Hedge Mode automáticamente al iniciar
  - Cachea precisión de cantidad por símbolo (evita errores de qty)
  - Loggea la respuesta completa de cada orden para debug
  - hmac.new → hmac.new (correcto) pero verificado
"""

import hashlib
import hmac
import time
import logging
import math
from typing import Optional
from urllib.parse import urlencode

import aiohttp

log = logging.getLogger("BINGX")

BASE_URL = "https://open-api.bingx.com"


class BingXClient:
    def __init__(self, api_key: str, api_secret: str):
        self.api_key    = api_key
        self.api_secret = api_secret
        self._session: Optional[aiohttp.ClientSession] = None
        self._symbol_info: dict = {}   # cache de precisión por símbolo

    # ------------------------------------------------------------------ #
    #  HTTP helpers
    # ------------------------------------------------------------------ #
    def _sign(self, params: dict) -> str:
        query = urlencode(sorted(params.items()))
        return hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-BX-APIKEY": self.api_key}
            )
        return self._session

    async def _get(self, path: str, params: dict = None) -> dict:
        params = params or {}
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(params)
        session = await self._session_get()
        async with session.get(BASE_URL + path, params=params) as r:
            data = await r.json()
            if data.get("code", 0) != 0:
                log.error(f"GET {path} error: {data}")
            return data

    async def _post(self, path: str, params: dict = None) -> dict:
        params = params or {}
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(params)
        session = await self._session_get()
        async with session.post(BASE_URL + path, params=params) as r:
            data = await r.json()
            if data.get("code", 0) != 0:
                log.error(f"POST {path} error: {data}")
            return data

    # ------------------------------------------------------------------ #
    #  Inicialización — llamar una vez al arrancar
    # ------------------------------------------------------------------ #
    async def initialize(self):
        """Activa Hedge Mode y carga la info de símbolos."""
        await self._enable_hedge_mode()
        await self._load_symbol_info()

    async def _enable_hedge_mode(self):
        """
        BingX requiere Hedge Mode para usar positionSide LONG/SHORT.
        Intentamos activarlo; si ya está activo, la API devuelve error ignorable.
        """
        resp = await self._post(
            "/openApi/swap/v2/trade/positionSide/dual",
            {"dualSidePosition": "true"}
        )
        code = resp.get("code", -1)
        if code == 0:
            log.info("✅  Hedge Mode activado")
        elif code == 200003:
            log.info("ℹ️   Hedge Mode ya estaba activo")
        else:
            log.warning(f"Hedge Mode resp: {resp}")

    async def _load_symbol_info(self):
        """Carga precisión de cantidad y precio para todos los símbolos."""
        data = await self._get("/openApi/swap/v2/quote/contracts")
        for s in data.get("data", []):
            sym = s.get("symbol", "")
            self._symbol_info[sym] = {
                "qty_precision":   int(s.get("quantityPrecision", 3)),
                "price_precision": int(s.get("pricePrecision",    4)),
                "min_qty":         float(s.get("tradeMinQuantity", 0.001)),
                "min_notional":    float(s.get("tradeMinUSDT",    5.0)),
            }
        log.info(f"Symbol info cargada: {len(self._symbol_info)} símbolos")

    def _round_qty(self, symbol: str, qty: float) -> float:
        info = self._symbol_info.get(symbol, {})
        precision = info.get("qty_precision", 3)
        factor    = 10 ** precision
        qty_r     = math.floor(qty * factor) / factor
        min_qty   = info.get("min_qty", 0.001)
        return max(qty_r, min_qty)

    def _round_price(self, symbol: str, price: float) -> float:
        info = self._symbol_info.get(symbol, {})
        precision = info.get("price_precision", 4)
        return round(price, precision)

    # ------------------------------------------------------------------ #
    #  Market data
    # ------------------------------------------------------------------ #
    async def get_tickers(self) -> list[dict]:
        data = await self._get("/openApi/swap/v2/quote/ticker")
        return data.get("data", [])

    async def get_klines(
        self, symbol: str, interval: str = "1h", limit: int = 300
    ) -> list[dict]:
        data = await self._get(
            "/openApi/swap/v3/quote/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        return data.get("data", [])

    # ------------------------------------------------------------------ #
    #  Account
    # ------------------------------------------------------------------ #
    async def get_balance(self) -> float:
        data = await self._get("/openApi/swap/v2/user/balance")
        for asset in data.get("data", {}).get("balance", []):
            if asset.get("asset") == "USDT":
                return float(asset.get("availableMargin", 0))
        return 0.0

    async def get_positions(self) -> list[dict]:
        data = await self._get("/openApi/swap/v2/user/positions")
        return data.get("data", [])

    # ------------------------------------------------------------------ #
    #  Trading
    # ------------------------------------------------------------------ #
    async def place_order(
        self,
        symbol:     str,
        side:       str,           # BUY / SELL
        pos_side:   str,           # LONG / SHORT
        quantity:   float,
        order_type: str   = "MARKET",
        price:      float = None,
        tp_price:   float = None,
        sl_price:   float = None,
    ) -> dict:
        qty   = self._round_qty(symbol, quantity)
        params = {
            "symbol":       symbol,
            "side":         side,
            "positionSide": pos_side,
            "type":         order_type,
            "quantity":     qty,
        }
        if price:
            params["price"] = self._round_price(symbol, price)
        if tp_price:
            tp_r = self._round_price(symbol, tp_price)
            params["takeProfit"] = (
                f'{{"type":"MARK_PRICE","stopPrice":{tp_r},"workingType":"MARK_PRICE"}}'
            )
        if sl_price:
            sl_r = self._round_price(symbol, sl_price)
            params["stopLoss"] = (
                f'{{"type":"MARK_PRICE","stopPrice":{sl_r},"workingType":"MARK_PRICE"}}'
            )

        data = await self._post("/openApi/swap/v2/trade/order", params)
        # Log completo para debug
        log.info(f"ORDER {side} {pos_side} {symbol} qty={qty}  →  code={data.get('code')} msg={data.get('msg','')}")
        if data.get("code", -1) != 0:
            log.error(f"ORDER DETAIL: {data}")
        return data

    async def close_position(self, symbol: str, pos_side: str, quantity: float) -> dict:
        side = "SELL" if pos_side == "LONG" else "BUY"
        return await self.place_order(symbol, side, pos_side, quantity)

    async def set_leverage(self, symbol: str, leverage: int, side: str = "LONG") -> dict:
        resp = await self._post(
            "/openApi/swap/v2/trade/leverage",
            {"symbol": symbol, "side": side, "leverage": leverage},
        )
        if resp.get("code", -1) != 0:
            log.warning(f"set_leverage {symbol} {side}: {resp}")
        return resp

    async def cancel_all_orders(self, symbol: str) -> dict:
        return await self._post(
            "/openApi/swap/v2/trade/allOpenOrders",
            {"symbol": symbol},
        )
