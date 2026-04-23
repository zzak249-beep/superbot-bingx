"""
BingXClient v6 — WebSocket + REST con latencia mínima
=====================================================
Mejoras vs v5:
  - WebSocket para price feed en tiempo real (10-50ms vs 500ms+)
  - Conexión HTTP persistente con pool de conexiones optimizado
  - Cache inteligente por símbolo con TTL
  - Timestamp sincronizado con el servidor para evitar rechazos
  - Batch ticker fetch paralelo
  - Retry automático con backoff en errores de red
"""

import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import time
from collections import defaultdict
from typing import Optional
from urllib.parse import urlencode

import aiohttp

log = logging.getLogger("BINGX")

BASE_URL   = "https://open-api.bingx.com"
WS_URL     = "wss://open-api.bingx.com/market"

# Ajuste de reloj servidor (se calibra en initialize)
_SERVER_TIME_OFFSET_MS: int = 0


class BingXClient:
    def __init__(self, api_key: str, api_secret: str):
        self.api_key    = api_key
        self.api_secret = api_secret

        # HTTP session con pool grande y timeouts cortos
        self._session: Optional[aiohttp.ClientSession] = None
        self._connector: Optional[aiohttp.TCPConnector] = None

        # Cache de datos de mercado con TTL
        self._ticker_cache:  dict = {}          # sym → {price, ts}
        self._kline_cache:   dict = {}          # (sym,iv,limit) → {data, ts}
        self._depth_cache:   dict = {}          # sym → {data, ts}
        self._symbol_info:   dict = {}          # sym → precision info

        # WebSocket state
        self._ws_prices:     dict = {}          # sym → float (precio mid WS)
        self._ws_task:       Optional[asyncio.Task] = None
        self._ws_subscribed: set  = set()

        # Stats de latencia
        self._latency_ms:    float = 0.0

    # ------------------------------------------------------------------ #
    #  Sesión HTTP optimizada
    # ------------------------------------------------------------------ #
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._connector = aiohttp.TCPConnector(
                limit           = 100,   # pool grande
                ttl_dns_cache   = 300,
                use_dns_cache   = True,
                keepalive_timeout = 30,
                enable_cleanup_closed = True,
            )
            timeout = aiohttp.ClientTimeout(
                total   = 8,
                connect = 3,
                sock_read = 5,
            )
            self._session = aiohttp.ClientSession(
                connector = self._connector,
                timeout   = timeout,
                headers   = {
                    "X-BX-APIKEY": self.api_key,
                    "Content-Type": "application/json",
                },
            )
        return self._session

    # ------------------------------------------------------------------ #
    #  Firma HMAC
    # ------------------------------------------------------------------ #
    def _sign(self, params: dict) -> str:
        query = urlencode(sorted(params.items()))
        return hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()

    def _ts(self) -> int:
        return int(time.time() * 1000) + _SERVER_TIME_OFFSET_MS

    # ------------------------------------------------------------------ #
    #  HTTP helpers con retry
    # ------------------------------------------------------------------ #
    async def _get(self, path: str, params: dict = None, cache_ttl: float = 0) -> dict:
        params = params or {}

        # Cache rápida
        if cache_ttl > 0:
            key = path + str(sorted(params.items()))
            cached = self._kline_cache.get(key)
            if cached and (time.time() - cached["ts"]) < cache_ttl:
                return cached["data"]

        params["timestamp"] = self._ts()
        params["signature"] = self._sign(params)

        session = await self._get_session()
        for attempt in range(3):
            try:
                t0 = time.time()
                async with session.get(BASE_URL + path, params=params) as r:
                    self._latency_ms = (time.time() - t0) * 1000
                    data = await r.json(content_type=None)
                    if data.get("code", 0) != 0:
                        log.debug(f"GET {path} code={data.get('code')} msg={data.get('msg','')}")
                    if cache_ttl > 0:
                        self._kline_cache[key] = {"data": data, "ts": time.time()}
                    return data
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == 2:
                    log.error(f"GET {path} falló tras 3 intentos: {e}")
                    return {}
                await asyncio.sleep(0.3 * (attempt + 1))
        return {}

    async def _post(self, path: str, params: dict = None) -> dict:
        params = params or {}
        params["timestamp"] = self._ts()
        params["signature"] = self._sign(params)

        session = await self._get_session()
        for attempt in range(3):
            try:
                async with session.post(BASE_URL + path, params=params) as r:
                    data = await r.json(content_type=None)
                    if data.get("code", 0) != 0:
                        log.error(f"POST {path} error: {data}")
                    return data
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == 2:
                    log.error(f"POST {path} falló: {e}")
                    return {}
                await asyncio.sleep(0.3 * (attempt + 1))
        return {}

    # ------------------------------------------------------------------ #
    #  Inicialización
    # ------------------------------------------------------------------ #
    async def initialize(self):
        await self._sync_server_time()
        await self._enable_hedge_mode()
        await self._load_symbol_info()
        log.info(f"✅ BingX Client v6 — latencia REST: {self._latency_ms:.0f}ms")

    async def _sync_server_time(self):
        """Sincroniza el reloj local con el servidor para evitar rechazos."""
        global _SERVER_TIME_OFFSET_MS
        try:
            t0 = time.time()
            data = await self._get("/openApi/swap/v2/server/time", cache_ttl=0)
            rtt_ms = (time.time() - t0) * 1000
            server_ts = data.get("data", {}).get("serverTime", 0)
            if server_ts:
                local_ts = int(time.time() * 1000)
                _SERVER_TIME_OFFSET_MS = int(server_ts - local_ts + rtt_ms / 2)
                log.info(f"Reloj sincronizado: offset={_SERVER_TIME_OFFSET_MS}ms  RTT={rtt_ms:.0f}ms")
        except Exception as e:
            log.warning(f"Time sync: {e}")

    async def _enable_hedge_mode(self):
        resp = await self._post(
            "/openApi/swap/v2/trade/positionSide/dual",
            {"dualSidePosition": "true"}
        )
        code = resp.get("code", -1)
        if code == 0:
            log.info("✅ Hedge Mode activado")
        elif code == 200003:
            log.info("ℹ️  Hedge Mode ya activo")
        else:
            log.warning(f"Hedge Mode: {resp}")

    async def _load_symbol_info(self):
        data = await self._get("/openApi/swap/v2/quote/contracts", cache_ttl=3600)
        for s in data.get("data", []):
            sym = s.get("symbol", "")
            self._symbol_info[sym] = {
                "qty_precision":   int(s.get("quantityPrecision", 3)),
                "price_precision": int(s.get("pricePrecision",    4)),
                "min_qty":         float(s.get("tradeMinQuantity", 0.001)),
                "min_notional":    float(s.get("tradeMinUSDT",    5.0)),
            }
        log.info(f"Symbol info: {len(self._symbol_info)} símbolos")

    # ------------------------------------------------------------------ #
    #  WebSocket — precio en tiempo real
    # ------------------------------------------------------------------ #
    async def start_ws_price_feed(self, symbols: list[str]):
        """
        Inicia WebSocket para recibir precios en tiempo real.
        Actualiza self._ws_prices con latencia ~10-50ms.
        """
        new_syms = [s for s in symbols if s not in self._ws_subscribed]
        if not new_syms:
            return

        self._ws_subscribed.update(new_syms)

        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()

        self._ws_task = asyncio.create_task(
            self._ws_loop(list(self._ws_subscribed))
        )

    async def _ws_loop(self, symbols: list[str]):
        """Loop principal del WebSocket con reconexión automática."""
        while True:
            try:
                session = await self._get_session()
                async with session.ws_connect(
                    WS_URL,
                    heartbeat    = 20,
                    timeout      = aiohttp.ClientWSTimeout(ws_close=10),
                    max_msg_size = 0,
                ) as ws:
                    # Suscribir a ticker de todos los símbolos
                    for sym in symbols:
                        await ws.send_json({
                            "id":     sym,
                            "reqType": "sub",
                            "dataType": f"{sym}@ticker",
                        })
                    log.info(f"WS: suscrito a {len(symbols)} símbolos")

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            sym = data.get("s", "") or data.get("dataType", "").split("@")[0]
                            if sym and "c" in data:
                                self._ws_prices[sym] = float(data["c"])
                        elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                         aiohttp.WSMsgType.ERROR):
                            break

            except asyncio.CancelledError:
                return
            except Exception as e:
                log.debug(f"WS reconectando: {e}")
                await asyncio.sleep(2)

    def get_ws_price(self, symbol: str) -> Optional[float]:
        """Precio del WebSocket (latencia mínima). None si no disponible."""
        return self._ws_prices.get(symbol)

    # ------------------------------------------------------------------ #
    #  Market data — con cache inteligente
    # ------------------------------------------------------------------ #
    async def get_tickers(self) -> list[dict]:
        """Tickers de todos los pares — cacheado 10 segundos."""
        data = await self._get("/openApi/swap/v2/quote/ticker", cache_ttl=10)
        return data.get("data", [])

    async def get_tickers_parallel(self, symbols: list[str]) -> dict[str, dict]:
        """
        Obtiene tickers de múltiples símbolos en paralelo.
        Devuelve dict sym → ticker.
        """
        # Un solo call al endpoint general es más eficiente
        tickers = await self.get_tickers()
        return {t["symbol"]: t for t in tickers if t.get("symbol") in symbols}

    async def get_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 300,
    ) -> list[dict]:
        """
        Klines con cache de 30 segundos para mismos parámetros.
        Si el precio WS está disponible, lo inyecta en la última vela.
        """
        data = await self._get(
            "/openApi/swap/v3/quote/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
            cache_ttl = 30,
        )
        klines = data.get("data", [])

        # Inyectar precio WS en la última vela (más preciso)
        ws_price = self.get_ws_price(symbol)
        if ws_price and klines:
            klines[-1]["close"] = str(ws_price)

        return klines

    async def get_klines_multi(
        self,
        symbols: list[str],
        interval: str = "1h",
        limit: int = 150,
    ) -> dict[str, list[dict]]:
        """Descarga klines de múltiples símbolos en paralelo."""
        tasks = [
            self.get_klines(sym, interval, limit)
            for sym in symbols
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out = {}
        for sym, res in zip(symbols, results):
            if isinstance(res, list):
                out[sym] = res
        return out

    async def get_depth(self, symbol: str, limit: int = 20) -> dict:
        """Order book con cache de 2 segundos."""
        data = await self._get(
            "/openApi/swap/v2/quote/depth",
            {"symbol": symbol, "limit": limit},
            cache_ttl = 2,
        )
        return data.get("data", {})

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
    #  Rounding helpers
    # ------------------------------------------------------------------ #
    def _round_qty(self, symbol: str, qty: float) -> float:
        info      = self._symbol_info.get(symbol, {})
        precision = info.get("qty_precision", 3)
        factor    = 10 ** precision
        qty_r     = math.floor(qty * factor) / factor
        min_qty   = info.get("min_qty", 0.001)
        return max(qty_r, min_qty)

    def _round_price(self, symbol: str, price: float) -> float:
        info      = self._symbol_info.get(symbol, {})
        precision = info.get("price_precision", 4)
        return round(price, precision)

    # ------------------------------------------------------------------ #
    #  Trading — ejecución optimizada para mínima latencia
    # ------------------------------------------------------------------ #
    async def place_order(
        self,
        symbol:     str,
        side:       str,
        pos_side:   str,
        quantity:   float,
        order_type: str   = "MARKET",
        price:      float = None,
        tp_price:   float = None,
        sl_price:   float = None,
    ) -> dict:
        qty = self._round_qty(symbol, quantity)

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
                f'{{"type":"MARK_PRICE","stopPrice":{tp_r},'
                f'"workingType":"MARK_PRICE","priceProtect":"true"}}'
            )
        if sl_price:
            sl_r = self._round_price(symbol, sl_price)
            params["stopLoss"] = (
                f'{{"type":"MARK_PRICE","stopPrice":{sl_r},'
                f'"workingType":"MARK_PRICE","priceProtect":"true"}}'
            )

        # Usar precio WS para mejor timing si es MARKET
        if order_type == "MARKET":
            ws_p = self.get_ws_price(symbol)
            if ws_p:
                log.debug(f"Usando precio WS: {ws_p} para {symbol}")

        data = await self._post("/openApi/swap/v2/trade/order", params)
        log.info(
            f"ORDER {side} {pos_side} {symbol} qty={qty} "
            f"→ code={data.get('code')} msg={data.get('msg','')}"
        )
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

    # ------------------------------------------------------------------ #
    #  Telegram
    # ------------------------------------------------------------------ #
    async def notify(self, message: str):
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id   = os.getenv("TELEGRAM_CHAT_ID")

        if not bot_token or not chat_id:
            log.debug(f"Telegram (no configurado): {message[:80]}")
            return

        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            session = await self._get_session()
            async with session.post(url, json={
                "chat_id":    chat_id,
                "text":       message,
                "parse_mode": "HTML",
            }) as resp:
                if resp.status != 200:
                    log.warning(f"Telegram status: {resp.status}")
        except Exception as e:
            log.warning(f"Telegram: {e}")

    # ------------------------------------------------------------------ #
    #  Cierre
    # ------------------------------------------------------------------ #
    async def close(self):
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
        if self._session and not self._session.closed:
            await self._session.close()
        if self._connector and not self._connector.closed:
            await self._connector.close()
