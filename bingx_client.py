"""
BingX Client v7 — Conexión robusta con futuros perpetuos
=========================================================
FIXES:
  - Balance leído de cuenta FUTURES (no spot)
  - Verificación de permisos API al iniciar
  - Reconexión automática WebSocket con backoff exponencial
  - Latencia real medida en cada request
"""

import asyncio, hashlib, hmac, json, logging, os, time, urllib.parse
from typing import Optional
import aiohttp

log = logging.getLogger("BINGX")

BINGX_BASE   = "https://open-api.bingx.com"
BINGX_WS     = "wss://open-api.bingx.com/market"
MAX_RETRIES  = 3
RETRY_DELAY  = 1.0


class BingXClient:
    def __init__(self, api_key: str, api_secret: str):
        self._key    = api_key
        self._secret = api_secret
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_prices: dict  = {}        # sym → {"price": float, "ts": float}
        self._ws_task: Optional[asyncio.Task] = None
        self._latency_ms: float = 0.0
        self._tg_token  = os.getenv("TELEGRAM_TOKEN", "")
        self._tg_chat   = os.getenv("TELEGRAM_CHAT_ID", "")

    # ── Lifecycle ─────────────────────────────────────────────── #

    async def initialize(self):
        timeout = aiohttp.ClientTimeout(total=15)
        conn    = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
        self._session = aiohttp.ClientSession(timeout=timeout, connector=conn)

        # Verificar cuenta FUTURES — FIX principal
        balance = await self._get_futures_balance()
        if balance is None:
            raise RuntimeError(
                "No se pudo leer balance de FUTUROS. "
                "Verifica: 1) Fondos en cuenta perpetuos BingX "
                "2) API key con permiso 'Trade' activo "
                "3) IP whitelisted si aplica"
            )
        log.info(f"Balance futuros USDT: ${balance:.2f}")

        # Iniciar WebSocket
        self._ws_task = asyncio.create_task(self._ws_loop())

    async def close(self):
        if self._ws_task:
            self._ws_task.cancel()
        if self._session:
            await self._session.close()

    # ── Firma BingX ───────────────────────────────────────────── #

    def _sign(self, params: dict) -> str:
        # BingX requiere parámetros ordenados alfabéticamente por clave
        # NO usar urlencode - concatenar directamente key=value
        sorted_params = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        return hmac.new(self._secret.encode(), sorted_params.encode(), hashlib.sha256).hexdigest()

    def _headers(self) -> dict:
        return {"X-BX-APIKEY": self._key, "Content-Type": "application/json"}

    def _ts(self) -> int:
        return int(time.time() * 1000)

    # ── HTTP helpers ──────────────────────────────────────────── #

    async def _get(self, path: str, params: dict = None, signed: bool = False) -> Optional[dict]:
        p = dict(params or {})
        if signed:
            p["timestamp"] = self._ts()
            p["signature"] = self._sign(p)
        url = BINGX_BASE + path
        t0  = time.monotonic()
        for attempt in range(MAX_RETRIES):
            try:
                async with self._session.get(url, params=p, headers=self._headers()) as r:
                    self._latency_ms = (time.monotonic() - t0) * 1000
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        if data.get("code") == 0:
                            return data.get("data")
                        log.warning(f"GET {path} code={data.get('code')} msg={data.get('msg')}")
                        return None
                    log.warning(f"GET {path} HTTP {r.status}")
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY * (2 ** attempt))
                else:
                    log.error(f"GET {path} failed: {e}")
        return None

    async def _post(self, path: str, params: dict) -> Optional[dict]:
        p = dict(params)
        p["timestamp"] = self._ts()
        p["signature"] = self._sign(p)
        url = BINGX_BASE + path
        for attempt in range(MAX_RETRIES):
            try:
                async with self._session.post(url, params=p, headers=self._headers()) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        if data.get("code") == 0:
                            return data.get("data")
                        log.error(f"POST {path} code={data.get('code')} msg={data.get('msg')}")
                        return None
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY * (2 ** attempt))
                else:
                    log.error(f"POST {path} failed: {e}")
        return None

    # ── Balance FUTURES (FIX crítico) ─────────────────────────── #

    async def _get_futures_balance(self) -> Optional[float]:
        """Lee el balance USDT de la cuenta de FUTUROS PERPETUOS."""
        data = await self._get("/openApi/swap/v2/user/balance", {}, signed=True)
        if not data:
            return None
        # BingX devuelve {"balance": {"asset": "USDT", "balance": "xxx", ...}}
        bal = data.get("balance", {})
        if isinstance(bal, list):
            for b in bal:
                if b.get("asset") == "USDT":
                    return float(b.get("balance", 0))
        elif isinstance(bal, dict):
            return float(bal.get("balance", 0))
        return None

    async def get_balance(self) -> float:
        """Retorna balance USDT disponible en futuros."""
        val = await self._get_futures_balance()
        return val if val is not None else 0.0

    # ── Market data ───────────────────────────────────────────── #

    async def get_symbols(self) -> list[str]:
        data = await self._get("/openApi/swap/v2/quote/contracts")
        if not data:
            return []
        return [c["symbol"] for c in data if c.get("status") == 1 and "USDT" in c.get("symbol", "")]

    async def get_ticker_24h(self, symbol: str) -> Optional[dict]:
        return await self._get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})

    async def get_all_tickers(self) -> list[dict]:
        data = await self._get("/openApi/swap/v2/quote/ticker")
        return data if isinstance(data, list) else []

    async def get_klines(self, symbol: str, interval: str = "1h", limit: int = 200) -> list:
        data = await self._get("/openApi/swap/v2/quote/klines", {
            "symbol": symbol, "interval": interval, "limit": limit
        })
        return data if data else []

    async def get_orderbook(self, symbol: str, depth: int = 20) -> Optional[dict]:
        return await self._get("/openApi/swap/v2/quote/depth", {
            "symbol": symbol, "limit": depth
        })

    async def get_funding_rate(self, symbol: str) -> float:
        data = await self._get("/openApi/swap/v2/quote/fundingRate", {"symbol": symbol})
        if data:
            return float(data.get("fundingRate", 0))
        return 0.0

    async def get_open_interest(self, symbol: str) -> float:
        data = await self._get("/openApi/swap/v2/quote/openInterest", {"symbol": symbol})
        if data:
            return float(data.get("openInterest", 0))
        return 0.0

    def get_ws_price(self, symbol: str) -> Optional[float]:
        entry = self._ws_prices.get(symbol)
        if entry and (time.time() - entry["ts"]) < 5:
            return entry["price"]
        return None

    # ── Órdenes ───────────────────────────────────────────────── #

    async def set_leverage(self, symbol: str, leverage: int, side: str = "LONG") -> bool:
        data = await self._post("/openApi/swap/v2/trade/leverage", {
            "symbol": symbol, "side": side, "leverage": str(leverage)
        })
        return data is not None

    async def place_order(self, symbol: str, side: str, qty: float,
                          order_type: str = "MARKET", price: float = None,
                          sl: float = None, tp: float = None) -> Optional[dict]:
        params = {
            "symbol":       symbol,
            "side":         side,            # BUY / SELL
            "positionSide": "LONG" if side == "BUY" else "SHORT",
            "type":         order_type,
            "quantity":     str(round(qty, 4)),
        }
        if price:
            params["price"] = str(round(price, 6))
        if sl:
            params["stopLoss"]   = json.dumps({"type": "STOP_MARKET", "stopPrice": round(sl, 6), "workingType": "MARK_PRICE"})
        if tp:
            params["takeProfit"] = json.dumps({"type": "TAKE_PROFIT_MARKET", "stopPrice": round(tp, 6), "workingType": "MARK_PRICE"})
        return await self._post("/openApi/swap/v2/trade/order", params)

    async def close_position(self, symbol: str, side: str, qty: float) -> Optional[dict]:
        close_side = "SELL" if side == "LONG" else "BUY"
        return await self._post("/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         close_side,
            "positionSide": side,
            "type":         "MARKET",
            "quantity":     str(round(qty, 4)),
        })

    async def get_positions(self) -> list[dict]:
        data = await self._get("/openApi/swap/v2/user/positions", {}, signed=True)
        if isinstance(data, list):
            return [p for p in data if float(p.get("positionAmt", 0)) != 0]
        return []

    async def cancel_all_orders(self, symbol: str):
        await self._post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})

    # ── WebSocket precios en tiempo real ──────────────────────── #

    async def _ws_loop(self):
        backoff = 1
        while True:
            try:
                async with self._session.ws_connect(
                    f"{BINGX_WS}/stream?listenKey=latest",
                    heartbeat=20
                ) as ws:
                    backoff = 1
                    # Suscribirse a mini-tickers de todos los símbolos
                    await ws.send_json({"id": "price-sub", "reqType": "sub",
                                       "dataType": "!miniTicker@arr"})
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                d = json.loads(msg.data)
                                if isinstance(d, list):
                                    for item in d:
                                        sym = item.get("s") or item.get("symbol")
                                        p   = item.get("c") or item.get("lastPrice")
                                        if sym and p:
                                            self._ws_prices[sym] = {
                                                "price": float(p), "ts": time.time()
                                            }
                            except Exception:
                                pass
            except Exception as e:
                log.warning(f"WS desconectado: {e} — reconectando en {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # ── Telegram ──────────────────────────────────────────────── #

    async def notify(self, text: str):
        if not self._tg_token or not self._tg_chat:
            return
        try:
            url = f"https://api.telegram.org/bot{self._tg_token}/sendMessage"
            await self._session.post(url, json={
                "chat_id":    self._tg_chat,
                "text":       text,
                "parse_mode": "HTML",
            })
        except Exception as e:
            log.debug(f"Telegram: {e}")
