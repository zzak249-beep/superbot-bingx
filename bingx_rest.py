"""
exchange/bingx_rest.py — Cliente REST asíncrono para BingX Perpetual Futures.
- Firma HMAC-SHA256
- Manejo de errores y reintentos
- Endpoints: klines, balance, posiciones, órdenes
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp

from config import config

log = logging.getLogger("bingx_rest")


class BingXRestError(Exception):
    def __init__(self, code: int, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"BingX API error {code}: {msg}")


class BingXRest:
    """Cliente REST asíncrono para BingX Perpetual Futures (swap v2/v3)."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=50,
                ttl_dns_cache=300,
                ssl=True,
            )
            timeout = aiohttp.ClientTimeout(total=8, connect=3)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={"X-SOURCE-KEY": "robotrading"},
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Firma ─────────────────────────────────────────────────────────────────

    def _sign(self, params: Dict[str, Any]) -> str:
        query = urlencode(sorted(params.items()))
        return hmac.new(
            config.API_SECRET.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()

    def _auth_params(self, extra: Dict[str, Any] = None) -> Dict[str, Any]:
        params = {"timestamp": int(time.time() * 1000), **(extra or {})}
        params["signature"] = self._sign(params)
        return params

    # ── Request base ──────────────────────────────────────────────────────────

    async def _request(self, method: str, path: str,
                       params: Dict = None, signed: bool = False,
                       retries: int = 3) -> Any:
        session = await self._get_session()
        url = config.BASE_URL + path
        p = dict(params or {})
        if signed:
            p = self._auth_params(p)
            p["apiKey"] = config.API_KEY

        for attempt in range(retries):
            try:
                if method == "GET":
                    async with session.get(url, params=p) as r:
                        body = await r.json(content_type=None)
                else:
                    async with session.post(url, params=p) as r:
                        body = await r.json(content_type=None)

                code = body.get("code", 0)
                if code != 0:
                    raise BingXRestError(code, body.get("msg", "unknown"))
                return body.get("data", body)

            except BingXRestError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    raise ConnectionError(f"Request failed after {retries} retries: {e}")

    # ── Klines ────────────────────────────────────────────────────────────────

    async def get_klines(self, symbol: str, interval: str,
                         limit: int = 100) -> List[Dict]:
        """
        Devuelve lista de velas: [timestamp, open, high, low, close, volume]
        interval: '1m','3m','5m','15m','1h','4h','1d'
        """
        data = await self._request("GET", "/openApi/swap/v3/quote/klines", {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        })
        # BingX devuelve lista de listas [ts, o, h, l, c, v, ...]
        return data if isinstance(data, list) else []

    # ── Balance ───────────────────────────────────────────────────────────────

    async def get_balance(self) -> float:
        """Retorna el saldo disponible en USDT."""
        data = await self._request("GET", "/openApi/swap/v2/user/balance",
                                   signed=True)
        # Puede ser dict o lista según versión de API
        if isinstance(data, dict):
            balance = data.get("balance", {})
            if isinstance(balance, dict):
                return float(balance.get("availableMargin",
                             balance.get("available", 0)))
            return float(balance)
        if isinstance(data, list):
            for item in data:
                if item.get("asset") == "USDT":
                    return float(item.get("availableMargin",
                                         item.get("free", 0)))
        return 0.0

    # ── Posiciones ────────────────────────────────────────────────────────────

    async def get_positions(self, symbol: str = "") -> List[Dict]:
        """Posiciones abiertas. Si symbol="" devuelve todas."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = await self._request("GET", "/openApi/swap/v2/user/positions",
                                   params=params, signed=True)
        return data if isinstance(data, list) else []

    async def get_open_position(self, symbol: str) -> Optional[Dict]:
        """Devuelve la posición abierta para symbol o None."""
        positions = await self.get_positions(symbol)
        for p in positions:
            if abs(float(p.get("positionAmt", 0))) > 0:
                return p
        return None

    # ── Leverage ──────────────────────────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        try:
            await self._request("POST", "/openApi/swap/v2/trade/leverage", {
                "symbol": symbol,
                "side": "LONG",
                "leverage": leverage,
            }, signed=True)
            await self._request("POST", "/openApi/swap/v2/trade/leverage", {
                "symbol": symbol,
                "side": "SHORT",
                "leverage": leverage,
            }, signed=True)
            return True
        except BingXRestError as e:
            log.warning(f"Set leverage {symbol}: {e}")
            return False

    # ── Place Order ───────────────────────────────────────────────────────────

    async def place_market_order(self, symbol: str, side: str,
                                  quantity: float,
                                  position_side: str = "BOTH") -> Dict:
        """
        Coloca una orden de mercado.
        side: 'BUY' | 'SELL'
        position_side: 'LONG' | 'SHORT' | 'BOTH' (ONE_WAY mode = BOTH)
        quantity: en unidades del activo base
        """
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": str(round(quantity, 6)),
        }
        log.info(f"ORDER {symbol} {side} {position_side} qty={quantity}")
        if config.PAPER:
            log.info("[PAPER] Orden no enviada")
            return {"orderId": "paper", "status": "FILLED"}
        return await self._request("POST", "/openApi/swap/v2/trade/order",
                                   params=params, signed=True)

    async def close_position(self, symbol: str) -> Dict:
        """Cierra toda la posición abierta en un símbolo."""
        pos = await self.get_open_position(symbol)
        if not pos:
            return {}
        amt = abs(float(pos.get("positionAmt", 0)))
        if amt == 0:
            return {}
        pos_side = pos.get("positionSide", "BOTH")
        # Si LONG → SELL; si SHORT → BUY
        close_side = "SELL" if float(pos.get("positionAmt", 0)) > 0 else "BUY"
        return await self.place_market_order(symbol, close_side, amt, pos_side)

    # ── Cancel All Orders ─────────────────────────────────────────────────────

    async def cancel_all_orders(self, symbol: str) -> bool:
        try:
            await self._request("DELETE", "/openApi/swap/v2/trade/allOpenOrders",
                                {"symbol": symbol}, signed=True)
            return True
        except BingXRestError:
            return False

    # ── Instrument Info ───────────────────────────────────────────────────────

    async def get_contracts(self) -> List[Dict]:
        data = await self._request("GET", "/openApi/swap/v2/quote/contracts")
        return data if isinstance(data, list) else []

    async def get_price(self, symbol: str) -> float:
        """Precio mark price actual."""
        data = await self._request("GET", "/openApi/swap/v2/quote/price",
                                   {"symbol": symbol})
        if isinstance(data, dict):
            return float(data.get("price", 0))
        if isinstance(data, list) and data:
            return float(data[0].get("price", 0))
        return 0.0

    async def get_tickers(self) -> List[Dict]:
        """
        Devuelve todos los tickers de futuros perpetuos con volumen 24h.
        Cada item: {symbol, lastPrice, volume, quoteVolume, priceChangePercent}
        """
        try:
            data = await self._request(
                "GET", "/openApi/swap/v2/quote/ticker",
            )
            return data if isinstance(data, list) else []
        except Exception:
            return []

    async def get_min_qty(self, symbol: str) -> float:
        """Tamaño mínimo de orden para el símbolo."""
        contracts = await self.get_contracts()
        for c in contracts:
            if c.get("symbol") == symbol:
                return float(c.get("minQty", c.get("tradeMinQuantity", 0.001)))
        return 0.001


# Instancia global (singleton)
bingx = BingXRest()
