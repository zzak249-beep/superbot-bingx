"""
bingx_client.py — Cliente BingX Perpetual Futures
Maneja firma HMAC, endpoints públicos y autenticados
"""
import hmac
import hashlib
import time
import logging
import urllib.parse
from typing import Optional

import requests

import config as C

log = logging.getLogger(__name__)

BASE = C.BINGX_BASE_URL


class BingXClient:
    def __init__(self):
        self.api_key = C.BINGX_API_KEY
        self.secret  = C.BINGX_SECRET_KEY
        self.session = requests.Session()
        self.session.headers.update({
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/json",
        })

    # ── Firma ──────────────────────────────────────────────────

    def _sign(self, params: dict) -> dict:
        params["timestamp"]  = int(time.time() * 1000)
        params["recvWindow"] = 5000
        sorted_params = sorted(params.items())
        query_str = urllib.parse.urlencode(sorted_params)
        sig = hmac.new(
            self.secret.encode("utf-8"),
            query_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = sig
        return params

    def _get_pub(self, path: str, params: dict = None) -> dict:
        """GET público — sin firma"""
        url = BASE + path
        try:
            r = self.session.get(url, params=params or {}, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error(f"GET {path} error: {e}")
            return {}

    def _get_auth(self, path: str, params: dict = None) -> dict:
        """GET autenticado — con firma"""
        p = self._sign(params or {})
        url = BASE + path
        try:
            r = self.session.get(url, params=p, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error(f"GET AUTH {path} error: {e}")
            return {}

    def _post_auth(self, path: str, params: dict = None) -> dict:
        """POST autenticado — con firma en query string"""
        p = self._sign(params or {})
        url = BASE + path + "?" + urllib.parse.urlencode(sorted(p.items()))
        try:
            r = self.session.post(url, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error(f"POST AUTH {path} error: {e}")
            return {}

    def _delete_auth(self, path: str, params: dict = None) -> dict:
        p = self._sign(params or {})
        url = BASE + path
        try:
            r = self.session.delete(url, params=p, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error(f"DELETE AUTH {path} error: {e}")
            return {}

    # ── Market Data ────────────────────────────────────────────

    def get_klines(self, symbol: str, interval: str, limit: int = 250):
        """OHLCV histórico"""
        data = self._get_pub("/openApi/swap/v2/quote/klines", {
            "symbol":   symbol,
            "interval": interval,
            "limit":    limit,
        })
        return data.get("data", [])

    def get_orderbook(self, symbol: str, limit: int = 20) -> dict:
        """Libro de órdenes"""
        return self._get_pub("/openApi/swap/v2/quote/depth", {
            "symbol": symbol,
            "limit":  limit,
        }).get("data", {})

    def get_funding_rate(self, symbol: str) -> float:
        """Tasa de financiamiento actual"""
        data = self._get_pub("/openApi/swap/v2/quote/premiumIndex", {
            "symbol": symbol,
        })
        try:
            return float(data["data"]["lastFundingRate"])
        except Exception:
            return 0.0

    def get_all_contracts(self) -> list:
        """Todos los pares perpetuos disponibles"""
        data = self._get_pub("/openApi/swap/v2/quote/contracts")
        return data.get("data", [])

    def get_ticker_24h(self, symbol: str = None) -> list:
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = self._get_pub("/openApi/swap/v2/quote/ticker", params)
        d = data.get("data", [])
        return d if isinstance(d, list) else [d]

    def get_mark_price(self, symbol: str) -> float:
        data = self._get_pub("/openApi/swap/v2/quote/premiumIndex", {"symbol": symbol})
        try:
            return float(data["data"]["markPrice"])
        except Exception:
            return 0.0

    # ── Cuenta ─────────────────────────────────────────────────

    def get_balance(self) -> float:
        """Saldo disponible USDT"""
        data = self._get_auth("/openApi/swap/v2/user/balance")
        try:
            result = data.get("data", {})
            if isinstance(result, dict):
                bal = result.get("balance", {})
                if isinstance(bal, dict):
                    return float(bal.get("availableMargin", 0))
                return float(result.get("availableMargin", 0))
            if isinstance(result, list):
                for item in result:
                    if item.get("asset") in ("USDT", "USD"):
                        return float(item.get("availableMargin", 0))
        except Exception as e:
            log.error(f"Balance parse error: {e} | raw: {data}")
        return 0.0

    def get_positions(self, symbol: str = None) -> list:
        """Posiciones abiertas"""
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = self._get_auth("/openApi/swap/v2/trade/openPositions", params)
        positions = data.get("data", []) or []
        return [p for p in positions if float(p.get("positionAmt", 0)) != 0]

    def get_open_orders(self, symbol: str) -> list:
        data = self._get_auth("/openApi/swap/v2/trade/openOrders", {"symbol": symbol})
        return data.get("data", {}).get("orders", []) or []

    # ── Configuración ──────────────────────────────────────────

    def set_leverage(self, symbol: str, leverage: int):
        for side in ("LONG", "SHORT"):
            self._post_auth("/openApi/swap/v2/trade/leverage", {
                "symbol":       symbol,
                "side":         side,
                "leverage":     leverage,
            })
        log.info(f"Apalancamiento {leverage}x en {symbol}")

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED"):
        self._post_auth("/openApi/swap/v2/trade/marginType", {
            "symbol":     symbol,
            "marginType": margin_type,
        })

    # ── Órdenes ────────────────────────────────────────────────

    def place_market_order(self, symbol: str, side: str,
                           quantity: float) -> dict:
        """
        side: BUY | SELL
        One-Way mode: sin positionSide
        """
        params = {
            "symbol":     symbol,
            "side":       side,
            "type":       "MARKET",
            "quantity":   round(quantity, 6),
        }
        data = self._post_auth("/openApi/swap/v2/trade/order", params)
        log.info(f"Market order {side} {quantity} {symbol} → {data}")
        return data

    def place_limit_order(self, symbol: str, side: str,
                          quantity: float, price: float) -> dict:
        params = {
            "symbol":     symbol,
            "side":       side,
            "type":       "LIMIT",
            "quantity":   round(quantity, 6),
            "price":      round(price, 6),
            "timeInForce":"GTC",
        }
        data = self._post_auth("/openApi/swap/v2/trade/order", params)
        log.info(f"Limit order {side} {quantity} @ {price} {symbol} → {data}")
        return data

    def place_stop_order(self, symbol: str, side: str,
                         quantity: float, stop_price: float,
                         order_type: str = "STOP_MARKET") -> dict:
        params = {
            "symbol":     symbol,
            "side":       side,
            "type":       order_type,
            "quantity":   round(quantity, 6),
            "stopPrice":  round(stop_price, 6),
        }
        data = self._post_auth("/openApi/swap/v2/trade/order", params)
        log.info(f"Stop order {side} {quantity} @ stop={stop_price} → {data}")
        return data

    def cancel_all_orders(self, symbol: str) -> dict:
        return self._delete_auth("/openApi/swap/v2/trade/allOpenOrders",
                                 {"symbol": symbol})

    def close_position(self, symbol: str, position: dict) -> dict:
        """Cierra posición existente con market order inversa"""
        amt = float(position.get("positionAmt", 0))
        if amt == 0:
            return {}
        side = "SELL" if amt > 0 else "BUY"
        qty  = abs(amt)
        return self.place_market_order(symbol, side, qty)

    # ── Orderbook imbalance ────────────────────────────────────

    def orderbook_imbalance(self, symbol: str, levels: int = 10) -> float:
        """
        Ratio de presión compradora/vendedora en el libro.
        > 0.55 = presión compradora, < 0.45 = presión vendedora
        """
        ob = self.get_orderbook(symbol, levels)
        try:
            bids = ob.get("bids", [])
            asks = ob.get("asks", [])
            bid_vol = sum(float(b[1]) for b in bids[:levels])
            ask_vol = sum(float(a[1]) for a in asks[:levels])
            total = bid_vol + ask_vol
            return bid_vol / total if total > 0 else 0.5
        except Exception:
            return 0.5

    # ── Top pares por volumen ──────────────────────────────────

    def get_top_pairs(self, n: int = 30, min_vol: float = 5_000_000) -> list:
        """Pares con mayor volumen 24h en USDT"""
        tickers = self.get_ticker_24h()
        pairs = []
        for t in tickers:
            try:
                sym = t.get("symbol", "")
                if not sym.endswith("-USDT"):
                    continue
                vol = float(t.get("quoteVolume", 0))
                if vol >= min_vol:
                    pairs.append((sym, vol))
            except Exception:
                continue
        pairs.sort(key=lambda x: x[1], reverse=True)
        return [p[0] for p in pairs[:n]]
