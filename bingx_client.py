"""
BingXClient v5 — FIXES COMPLETOS
- Endpoint v3 para balance (fix $0.00)
- Log de wallet spot si futures=0
- Detección automática de tipo de cuenta
- Firma HMAC correcta
- Rate limiting integrado
"""
import hmac
import hashlib
import time
import logging
import requests
from typing import Optional

log = logging.getLogger("BingX")

BASE_URL = "https://open-api.bingx.com"


class BingXClient:
    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.session = requests.Session()
        self.session.headers.update({
            "X-BX-APIKEY": api_key,
            "Content-Type": "application/json",
        })
        self._last_call = 0.0
        self._min_interval = 0.12  # ~8 req/s para no superar rate limit

    def _sign(self, params: dict) -> str:
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return hmac.new(
            self.secret_key.encode("utf-8"),
            query.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()

    def _request(self, method: str, path: str, params: dict = None, data: dict = None) -> dict:
        # Rate limit suave
        elapsed = time.time() - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.time()

        params = params or {}
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(params)

        url = BASE_URL + path
        try:
            if method == "GET":
                resp = self.session.get(url, params=params, timeout=10)
            else:
                resp = self.session.post(url, params=params, json=data, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            log.error(f"HTTP error {path}: {e}")
            raise

    # ── BALANCE (FIX CRÍTICO: v3 + parseo correcto) ──────────────────
    def get_balance(self) -> float:
        """
        FIX v5: usa /swap/v3/user/balance (no v2).
        Parsea data.balance.balance correctamente.
        Si futures=0, loguea el balance spot para diagnóstico.
        """
        for attempt in range(3):
            try:
                r = self._request("GET", "/openApi/swap/v3/user/balance", {})
                data = r.get("data", {})
                # v3: data.balance es un objeto con campos
                bal_obj = data.get("balance", {})
                if isinstance(bal_obj, dict):
                    for field in ["balance", "availableMargin", "equity", "availableBalance"]:
                        val = bal_obj.get(field)
                        if val is not None:
                            fval = float(val)
                            if fval > 0:
                                log.info(f"💰 Balance futuros: ${fval:.2f} USDT (campo: {field})")
                                return fval
                # Intentar parseo alternativo (v2 fallback)
                if isinstance(data, list) and data:
                    usdt = next((x for x in data if x.get("asset") == "USDT"), None)
                    if usdt:
                        fval = float(usdt.get("balance", 0))
                        if fval > 0:
                            return fval

                log.warning(f"⚠️ Balance futuros = $0 (intento {attempt+1}/3). Raw: {data}")
                if attempt == 2:
                    self._diagnose_zero_balance()
                time.sleep(1.5)
            except Exception as e:
                log.error(f"get_balance error intento {attempt+1}: {e}")
                time.sleep(1.5)
        return 0.0

    def _diagnose_zero_balance(self):
        """Loguea wallet spot para saber si los fondos están ahí."""
        try:
            r = self._request("GET", "/openApi/spot/v1/account/balance", {})
            assets = r.get("data", {}).get("balances", [])
            usdt = next((a for a in assets if a.get("asset") == "USDT"), {})
            spot_free = float(usdt.get("free", 0))
            if spot_free > 0:
                log.error(
                    f"🔴 DIAGNÓSTICO: Futuros=$0 pero Spot tiene ${spot_free:.2f} USDT.\n"
                    f"   → Ve a BingX → Wallet → Transferir a 'Perpetual Futures'"
                )
            else:
                log.error("🔴 DIAGNÓSTICO: $0 en Futuros Y en Spot. Verifica API key y permisos.")
        except Exception as e:
            log.warning(f"Diagnóstico spot falló: {e}")

    # ── POSICIONES ────────────────────────────────────────────────────
    def get_positions(self, symbol: str = None) -> list:
        params = {}
        if symbol:
            params["symbol"] = symbol
        r = self._request("GET", "/openApi/swap/v2/user/positions", params)
        return r.get("data", []) or []

    def get_open_orders(self, symbol: str = None) -> list:
        params = {}
        if symbol:
            params["symbol"] = symbol
        r = self._request("GET", "/openApi/swap/v2/trade/openOrders", params)
        return r.get("data", {}).get("orders", []) or []

    # ── MERCADO ───────────────────────────────────────────────────────
    def get_ticker(self, symbol: str) -> dict:
        r = self._request("GET", "/openApi/swap/v2/quote/ticker", {"symbol": symbol})
        return r.get("data", {}) or {}

    def get_klines(self, symbol: str, interval: str = "15m", limit: int = 200) -> list:
        r = self._request("GET", "/openApi/swap/v3/quote/klines", {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        })
        return r.get("data", []) or []

    def get_symbol_info(self, symbol: str) -> dict:
        r = self._request("GET", "/openApi/swap/v2/quote/contracts", {})
        contracts = r.get("data", []) or []
        for c in contracts:
            if c.get("symbol") == symbol:
                return c
        return {}

    def get_symbols(self) -> list:
        r = self._request("GET", "/openApi/swap/v2/quote/contracts", {})
        return r.get("data", []) or []

    def get_funding_rate(self, symbol: str) -> float:
        try:
            r = self._request("GET", "/openApi/swap/v2/quote/fundingRate", {"symbol": symbol})
            return float(r.get("data", {}).get("lastFundingRate", 0))
        except Exception:
            return 0.0

    # ── TRADING ───────────────────────────────────────────────────────
    def set_leverage(self, symbol: str, leverage: int, side: str = "LONG"):
        try:
            self._request("POST", "/openApi/swap/v2/trade/leverage", {}, {
                "symbol": symbol,
                "side": side,
                "leverage": leverage,
            })
        except Exception as e:
            log.warning(f"set_leverage {symbol}: {e}")

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED"):
        try:
            self._request("POST", "/openApi/swap/v2/trade/marginType", {}, {
                "symbol": symbol,
                "marginType": margin_type,
            })
        except Exception as e:
            log.debug(f"set_margin_type {symbol}: {e}")

    def place_order(self, symbol: str, side: str, position_side: str,
                    order_type: str = "MARKET", quantity: float = 0,
                    price: float = None, stop_loss: float = None,
                    take_profit: float = None, client_order_id: str = None) -> dict:
        body = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": order_type,
            "quantity": quantity,
        }
        if price:
            body["price"] = price
        if stop_loss:
            body["stopLoss"] = {"type": "MARK_PRICE", "stopPrice": stop_loss, "workingType": "MARK_PRICE"}
        if take_profit:
            body["takeProfit"] = {"type": "MARK_PRICE", "stopPrice": take_profit, "workingType": "MARK_PRICE"}
        if client_order_id:
            body["clientOrderID"] = client_order_id

        return self._request("POST", "/openApi/swap/v2/trade/order", {}, body)

    def place_trailing_stop(self, symbol: str, side: str, position_side: str,
                             quantity: float, activation_price: float, price_rate: float) -> dict:
        body = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "TRAILING_STOP_MARKET",
            "quantity": quantity,
            "activationPrice": activation_price,
            "callbackRate": price_rate * 100,
        }
        return self._request("POST", "/openApi/swap/v2/trade/order", {}, body)

    def cancel_order(self, symbol: str, order_id: str):
        return self._request("DELETE", "/openApi/swap/v2/trade/order", {
            "symbol": symbol,
            "orderId": order_id,
        })

    def close_position_partial(self, symbol: str, direction: str, quantity: float) -> dict:
        side = "SELL" if direction == "LONG" else "BUY"
        return self.place_order(symbol, side, direction, "MARKET", quantity)

    def update_sl(self, symbol: str, direction: str, new_sl: float):
        """Actualiza stop-loss cancelando orden existente y colocando nueva."""
        try:
            orders = self.get_open_orders(symbol)
            for o in orders:
                if o.get("type") in ("STOP", "STOP_MARKET") and o.get("positionSide") == direction:
                    self.cancel_order(symbol, str(o.get("orderId", "")))
            # Colocar nuevo SL
            side = "SELL" if direction == "LONG" else "BUY"
            self.place_order(symbol, side, direction, "STOP_MARKET", 0,
                             stop_loss=new_sl)
        except Exception as e:
            log.warning(f"update_sl {symbol}: {e}")
