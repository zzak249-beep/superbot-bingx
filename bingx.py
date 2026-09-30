"""
Cliente mínimo de BingX perpetuos (swap v2). Incorpora las lecciones de la flota:
  · Firma HMAC-SHA256 sobre urlencode(sorted(params)) y se ENVÍA ESA MISMA cadena
    (en v1.1.1 del scanner se firmaba una y se mandaba otra → todo rechazado).
  · GET/DELETE: cadena firmada en la URL. POST: en el cuerpo, form-urlencoded.
  · recvWindow siempre.
  · Hedge vs One-Way: se detecta; en Hedge nunca se manda reduceOnly.
  · DEMO = dominio VST (open-api-vst.bingx.com), mismas claves.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import time
from urllib.parse import urlencode

import requests

LIVE_URL = "https://open-api.bingx.com"
VST_URL = "https://open-api-vst.bingx.com"


class BingXError(RuntimeError):
    def __init__(self, code, msg, path=""):
        super().__init__(f"BingX {code} en {path}: {msg}")
        self.code, self.msg = code, msg


class BingX:
    def __init__(self, key="", secret="", demo=False, session=None):
        self.key, self.secret = key, secret
        self.base = VST_URL if demo else LIVE_URL
        self.public_base = LIVE_URL  # precios y velas siempre del mercado real
        self.http = session or requests.Session()
        self._contracts = None
        self._hedge = None

    # ───────── transporte ─────────
    def sign(self, params: dict) -> str:
        p = {k: v for k, v in params.items() if v is not None}
        p["timestamp"] = int(time.time() * 1000)
        p.setdefault("recvWindow", 5000)
        qs = urlencode(sorted(p.items()))
        sig = hmac.new(self.secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        return f"{qs}&signature={sig}"

    def _check(self, r, path):
        try:
            js = r.json()
        except ValueError:
            raise BingXError(r.status_code, r.text[:200], path)
        if isinstance(js, dict) and js.get("code", 0) not in (0, "0"):
            raise BingXError(js.get("code"), js.get("msg"), path)
        return js.get("data") if isinstance(js, dict) else js

    def public(self, path, params=None):
        for i in range(4):
            try:
                r = self.http.get(self.public_base + path, params=params or {}, timeout=20)
                if r.status_code >= 500 or r.status_code == 429:
                    time.sleep(2 ** i)
                    continue
                return self._check(r, path)
            except requests.RequestException:
                time.sleep(2 ** i)
        raise BingXError(-1, "sin respuesta", path)

    def private(self, method, path, params=None):
        if not (self.key and self.secret):
            raise BingXError(-1, "sin claves de API", path)
        qs = self.sign(params or {})
        headers = {"X-BX-APIKEY": self.key}
        url = self.base + path
        if method == "GET":
            r = self.http.get(f"{url}?{qs}", headers=headers, timeout=20)
        elif method == "DELETE":
            r = self.http.delete(f"{url}?{qs}", headers=headers, timeout=20)
        else:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            r = self.http.post(url, data=qs, headers=headers, timeout=20)
        return self._check(r, path)

    # ───────── mercado ─────────
    def contracts(self) -> dict:
        if self._contracts is None:
            data = self.public("/openApi/swap/v2/quote/contracts") or []
            self._contracts = {c["symbol"]: c for c in data}
        return self._contracts

    def tickers(self) -> dict:
        data = self.public("/openApi/swap/v2/quote/ticker") or []
        return {t["symbol"]: t for t in data}

    def price(self, symbol) -> float:
        d = self.public("/openApi/swap/v2/quote/ticker", dict(symbol=symbol))
        if isinstance(d, list):
            d = d[0]
        return float(d["lastPrice"])

    # ───────── cuenta ─────────
    def hedge_mode(self) -> bool:
        if self._hedge is None:
            d = self.private("GET", "/openApi/swap/v1/positionSide/dual") or {}
            self._hedge = str(d.get("dualSidePosition", "false")).lower() == "true"
        return self._hedge

    def equity(self) -> float:
        d = self.private("GET", "/openApi/swap/v2/user/balance") or {}
        bal = d.get("balance", d)
        return float(bal.get("equity") or bal.get("balance") or 0)

    def positions(self) -> list:
        return self.private("GET", "/openApi/swap/v2/user/positions") or []

    def set_isolated(self, symbol):
        try:
            self.private("POST", "/openApi/swap/v2/trade/marginType", dict(symbol=symbol, marginType="ISOLATED"))
        except BingXError as e:
            if "already" not in str(e.msg).lower() and "no need" not in str(e.msg).lower():
                raise

    def set_leverage(self, symbol, lev):
        sides = ["LONG", "SHORT"] if self.hedge_mode() else ["BOTH"]
        for sd in sides:
            self.private("POST", "/openApi/swap/v2/trade/leverage", dict(symbol=symbol, side=sd, leverage=int(lev)))

    # ───────── órdenes ─────────
    def round_qty(self, symbol, qty) -> float:
        prec = int(self.contracts().get(symbol, {}).get("quantityPrecision", 3))
        f = 10 ** prec
        return math.floor(abs(qty) * f + 1e-9) / f

    def round_price(self, symbol, px) -> float:
        prec = int(self.contracts().get(symbol, {}).get("pricePrecision", 4))
        return round(px, prec)

    def min_ok(self, symbol, qty, price) -> bool:
        c = self.contracts().get(symbol, {})
        mq = float(c.get("tradeMinQuantity", 0) or 0)
        mu = float(c.get("tradeMinUSDT", 0) or 0)
        return qty > 0 and qty >= mq and qty * price >= mu

    def order_by_client_id(self, symbol, cid):
        try:
            d = self.private("GET", "/openApi/swap/v2/trade/order", dict(symbol=symbol, clientOrderID=cid))
            return (d or {}).get("order", d) or None
        except BingXError:
            return None

    def market(self, symbol, side, qty, pos_side, reduce=False, cid=None):
        """side BUY/SELL · pos_side LONG/SHORT (se traduce a BOTH en One-Way)."""
        hedge = self.hedge_mode()
        p = dict(symbol=symbol, side=side, type="MARKET", quantity=qty,
                 positionSide=pos_side if hedge else "BOTH", clientOrderID=cid)
        if reduce and not hedge:
            p["reduceOnly"] = "true"
        return self.private("POST", "/openApi/swap/v2/trade/order", p)

    def stop_market(self, symbol, side, qty, stop_price, pos_side, cid=None):
        hedge = self.hedge_mode()
        p = dict(symbol=symbol, side=side, type="STOP_MARKET", quantity=qty, stopPrice=stop_price,
                 workingType="MARK_PRICE", positionSide=pos_side if hedge else "BOTH", clientOrderID=cid)
        if not hedge:
            p["reduceOnly"] = "true"
        return self.private("POST", "/openApi/swap/v2/trade/order", p)

    def cancel(self, symbol, order_id):
        try:
            self.private("DELETE", "/openApi/swap/v2/trade/order", dict(symbol=symbol, orderId=order_id))
        except BingXError:
            pass  # ya ejecutada o cancelada
