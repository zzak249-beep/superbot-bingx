"""
bot/bingx_client.py  (nombrado binance_client.py para no cambiar imports)
Cliente asíncrono para BingX Perpetual Futures (USDT-M).

API base: https://open-api.bingx.com
Docs:     https://bingx-api.github.io/docs/

Endpoints usados:
  GET  /openApi/swap/v2/quote/klines          — velas OHLCV
  GET  /openApi/swap/v2/user/balance          — saldo USDT
  GET  /openApi/swap/v2/user/positions        — posiciones abiertas
  POST /openApi/swap/v2/trade/order           — abrir orden
  POST /openApi/swap/v2/trade/closePosition   — cerrar posición
  DELETE /openApi/swap/v2/trade/allOpenOrders — cancelar órdenes
  POST /openApi/swap/v2/trade/leverage        — configurar leverage
  GET  /openApi/swap/v2/trade/allFillOrders   — historial trades
"""
import asyncio
import hashlib
import hmac
import logging
import math
import time
from typing import Optional
from urllib.parse import urlencode

import aiohttp
import pandas as pd

logger = logging.getLogger(__name__)

BASE_URL    = "https://open-api.bingx.com"
TESTNET_URL = "https://open-api-vst.bingx.com"   # BingX VST (paper trading)


class BinanceClient:
    """
    Nombre mantenido como BinanceClient para no cambiar imports en main.py.
    Internamente opera contra la API de BingX.
    """

    def __init__(self, api_key: str, secret_key: str, testnet: bool = False):
        self.api_key    = api_key
        self.secret_key = secret_key
        self.base_url   = TESTNET_URL if testnet else BASE_URL
        self._session: Optional[aiohttp.ClientSession] = None
        self._sym_info: dict = {}   # precisiones por símbolo

    # ─────────────────────────────────────────
    # CONEXIÓN
    # ─────────────────────────────────────────

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        logger.info(f"BingX client conectado → {self.base_url}")
        await self._load_symbol_info()

    async def disconnect(self) -> None:
        if self._session:
            await self._session.close()

    # ─────────────────────────────────────────
    # FIRMA HMAC-SHA256
    # ─────────────────────────────────────────

    def _sign(self, params: dict) -> str:
        query = urlencode(sorted(params.items()))
        return hmac.new(
            self.secret_key.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _headers(self) -> dict:
        return {
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/json"
        }

    def _ts(self) -> int:
        return int(time.time() * 1000)

    # ─────────────────────────────────────────
    # HTTP HELPERS
    # ─────────────────────────────────────────

    async def _get(self, path: str, params: dict = None) -> Optional[dict]:
        params = params or {}
        params["timestamp"] = self._ts()
        params["signature"] = self._sign(params)
        url = self.base_url + path
        try:
            async with self._session.get(url, params=params,
                                          headers=self._headers()) as r:
                data = await r.json()
                if data.get("code", 0) != 0:
                    logger.error(f"GET {path} error: {data}")
                    return None
                return data
        except Exception as e:
            logger.error(f"GET {path}: {e}")
            return None

    async def _post(self, path: str, params: dict = None) -> Optional[dict]:
        params = params or {}
        params["timestamp"] = self._ts()
        params["signature"] = self._sign(params)
        url = self.base_url + path
        try:
            async with self._session.post(url, params=params,
                                           headers=self._headers()) as r:
                data = await r.json()
                if data.get("code", 0) != 0:
                    logger.error(f"POST {path} error: {data}")
                    return None
                return data
        except Exception as e:
            logger.error(f"POST {path}: {e}")
            return None

    async def _delete(self, path: str, params: dict = None) -> Optional[dict]:
        params = params or {}
        params["timestamp"] = self._ts()
        params["signature"] = self._sign(params)
        url = self.base_url + path
        try:
            async with self._session.delete(url, params=params,
                                             headers=self._headers()) as r:
                data = await r.json()
                return data
        except Exception as e:
            logger.error(f"DELETE {path}: {e}")
            return None

    # ─────────────────────────────────────────
    # INFO DE SÍMBOLO (precisión)
    # ─────────────────────────────────────────

    async def _load_symbol_info(self) -> None:
        """Carga info de contratos para precisión de precio/qty."""
        path = "/openApi/swap/v2/quote/contracts"
        params = {"timestamp": self._ts()}
        params["signature"] = self._sign(params)
        url = self.base_url + path
        try:
            async with self._session.get(url, params=params,
                                          headers=self._headers()) as r:
                data = await r.json()
            if data.get("code") == 0:
                for c in data.get("data", []):
                    sym = c.get("symbol", "")
                    self._sym_info[sym] = {
                        "price_precision": int(c.get("pricePrecision", 2)),
                        "qty_precision":   int(c.get("quantityPrecision", 3)),
                    }
                logger.info(f"BingX: {len(self._sym_info)} contratos cargados")
        except Exception as e:
            logger.warning(f"No se pudo cargar info de contratos: {e}")

    def _fmt_symbol(self, symbol: str) -> str:
        """BTCUSDT → BTC-USDT para BingX."""
        if "-" not in symbol and symbol.endswith("USDT"):
            return symbol[:-4] + "-USDT"
        return symbol

    def _round_price(self, symbol: str, price: float) -> float:
        prec = self._sym_info.get(self._fmt_symbol(symbol), {}).get("price_precision", 2)
        return round(price, prec)

    def _round_qty(self, symbol: str, qty: float) -> float:
        prec = self._sym_info.get(self._fmt_symbol(symbol), {}).get("qty_precision", 3)
        return round(qty, prec)

    # ─────────────────────────────────────────
    # DATOS DE MERCADO
    # ─────────────────────────────────────────

    async def get_klines(self, symbol: str, interval: str,
                         limit: int = 300) -> Optional[pd.DataFrame]:
        """
        Devuelve DataFrame con columnas: open, high, low, close, volume
        Intervalo BingX: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
        """
        sym = self._fmt_symbol(symbol)
        data = await self._get("/openApi/swap/v2/quote/klines", {
            "symbol":   sym,
            "interval": interval,
            "limit":    limit
        })
        if not data:
            return None
        rows = data.get("data", [])
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=[
            "open_time", "open", "high", "low", "close", "volume"
        ])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        df.set_index("open_time", inplace=True)
        df.sort_index(inplace=True)
        return df

    # ─────────────────────────────────────────
    # CUENTA
    # ─────────────────────────────────────────

    async def get_balance(self) -> float:
        data = await self._get("/openApi/swap/v2/user/balance", {
            "currency": "USDT"
        })
        if not data:
            return 0.0
        bal = data.get("data", {}).get("balance", {})
        return float(bal.get("availableMargin", 0))

    async def get_position(self, symbol: str) -> Optional[dict]:
        sym  = self._fmt_symbol(symbol)
        data = await self._get("/openApi/swap/v2/user/positions", {
            "symbol": sym
        })
        if not data:
            return None
        positions = data.get("data", [])
        for p in positions:
            size = float(p.get("positionAmt", 0))
            if abs(size) > 0:
                return {
                    "symbol":      symbol,
                    "size":        size,
                    "side":        "LONG" if size > 0 else "SHORT",
                    "entry_price": float(p.get("avgPrice", 0)),
                    "unrealized":  float(p.get("unrealizedProfit", 0)),
                    "leverage":    int(p.get("leverage", 1)),
                }
        # Sin posición
        return {"symbol": symbol, "size": 0, "side": "FLAT",
                "entry_price": 0, "unrealized": 0, "leverage": 1}

    # ─────────────────────────────────────────
    # CONFIGURACIÓN DE SÍMBOLO
    # ─────────────────────────────────────────

    async def setup_symbol(self, symbol: str, leverage: int) -> None:
        sym  = self._fmt_symbol(symbol)
        data = await self._post("/openApi/swap/v2/trade/leverage", {
            "symbol":     sym,
            "side":       "LONG",
            "leverage":   leverage
        })
        await self._post("/openApi/swap/v2/trade/leverage", {
            "symbol":     sym,
            "side":       "SHORT",
            "leverage":   leverage
        })
        logger.info(f"{symbol}: leverage {leverage}x configurado en BingX")

    # ─────────────────────────────────────────
    # ÓRDENES
    # ─────────────────────────────────────────

    async def open_long(self, symbol: str, qty: float,
                        tp_price: float, sl_price: float) -> Optional[dict]:
        sym = self._fmt_symbol(symbol)
        qty = self._round_qty(symbol, qty)
        tp  = self._round_price(symbol, tp_price)
        sl  = self._round_price(symbol, sl_price)

        if qty <= 0:
            logger.warning(f"{symbol}: cantidad inválida {qty}")
            return None

        # Orden de entrada MARKET LONG
        entry = await self._post("/openApi/swap/v2/trade/order", {
            "symbol":     sym,
            "side":       "BUY",
            "positionSide": "LONG",
            "type":       "MARKET",
            "quantity":   qty
        })
        if not entry:
            return None
        logger.info(f"{symbol} LONG abierto qty={qty}")

        # TP
        await self._post("/openApi/swap/v2/trade/order", {
            "symbol":       sym,
            "side":         "SELL",
            "positionSide": "LONG",
            "type":         "TAKE_PROFIT_MARKET",
            "stopPrice":    tp,
            "closePosition": "true",
            "workingType":  "MARK_PRICE"
        })
        # SL
        await self._post("/openApi/swap/v2/trade/order", {
            "symbol":       sym,
            "side":         "SELL",
            "positionSide": "LONG",
            "type":         "STOP_MARKET",
            "stopPrice":    sl,
            "closePosition": "true",
            "workingType":  "MARK_PRICE"
        })
        return {"order": entry, "tp": tp, "sl": sl, "qty": qty, "side": "LONG"}

    async def open_short(self, symbol: str, qty: float,
                         tp_price: float, sl_price: float) -> Optional[dict]:
        sym = self._fmt_symbol(symbol)
        qty = self._round_qty(symbol, qty)
        tp  = self._round_price(symbol, tp_price)
        sl  = self._round_price(symbol, sl_price)

        if qty <= 0:
            return None

        entry = await self._post("/openApi/swap/v2/trade/order", {
            "symbol":       sym,
            "side":         "SELL",
            "positionSide": "SHORT",
            "type":         "MARKET",
            "quantity":     qty
        })
        if not entry:
            return None
        logger.info(f"{symbol} SHORT abierto qty={qty}")

        await self._post("/openApi/swap/v2/trade/order", {
            "symbol":       sym,
            "side":         "BUY",
            "positionSide": "SHORT",
            "type":         "TAKE_PROFIT_MARKET",
            "stopPrice":    tp,
            "closePosition": "true",
            "workingType":  "MARK_PRICE"
        })
        await self._post("/openApi/swap/v2/trade/order", {
            "symbol":       sym,
            "side":         "BUY",
            "positionSide": "SHORT",
            "type":         "STOP_MARKET",
            "stopPrice":    sl,
            "closePosition": "true",
            "workingType":  "MARK_PRICE"
        })
        return {"order": entry, "tp": tp, "sl": sl, "qty": qty, "side": "SHORT"}

    async def close_position(self, symbol: str, position: dict) -> Optional[dict]:
        """Cierre de mercado para barrera de tiempo."""
        sym  = self._fmt_symbol(symbol)
        size = abs(position["size"])
        if size == 0:
            return None
        qty  = self._round_qty(symbol, size)
        side_pos = position["side"]  # LONG | SHORT

        # Cancelar TP/SL pendientes primero
        await self._delete("/openApi/swap/v2/trade/allOpenOrders", {
            "symbol": sym
        })

        close_side = "SELL" if side_pos == "LONG" else "BUY"
        result = await self._post("/openApi/swap/v2/trade/order", {
            "symbol":       sym,
            "side":         close_side,
            "positionSide": side_pos,
            "type":         "MARKET",
            "quantity":     qty,
            "reduceOnly":   "true"
        })
        logger.info(f"{symbol} posición cerrada por barrera de tiempo")
        return result

    async def get_last_trade_pnl(self, symbol: str) -> float:
        sym  = self._fmt_symbol(symbol)
        data = await self._get("/openApi/swap/v2/trade/allFillOrders", {
            "symbol": sym,
            "limit":  5
        })
        if not data:
            return 0.0
        trades = data.get("data", {}).get("fill_orders", [])
        if trades:
            return float(trades[-1].get("realizedPnl", 0))
        return 0.0
