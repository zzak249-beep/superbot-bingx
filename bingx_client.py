"""
BingX Client — Conflux 4 Bot
Métodos completos: klines, precio, balance, órdenes, SL/TP
"""
import hashlib
import hmac
import time
import urllib.parse
from typing import List, Optional
import httpx
import pandas as pd
from loguru import logger

BINGX_LIVE = "https://open-api.bingx.com"
BINGX_TEST = "https://open-api-vst.bingx.com"
MAX_RETRIES = 3


def safe_float(value, default=0.0) -> float:
    try:
        if isinstance(value, dict):
            value = value.get("value", default)
        elif isinstance(value, list):
            value = value[0] if value else default
        return float(str(value))
    except (ValueError, TypeError):
        return default


class BingXClient:
    def __init__(self, api_key: str = "", secret_key: str = "", testnet: bool = False):
        self.api_key  = api_key
        self.secret   = secret_key
        self.base     = BINGX_TEST if testnet else BINGX_LIVE
        self.testnet  = testnet
        self._client  = httpx.Client(timeout=15)

    # ── Firma ─────────────────────────────────────────────────────────────────

    def _sign(self, params: dict) -> str:
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()

    def _headers(self):
        return {"X-BX-APIKEY": self.api_key}

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get_public(self, path: str, params: dict = None) -> dict:
        url = self.base + path
        for attempt in range(MAX_RETRIES):
            try:
                r = self._client.get(url, params=params or {})
                r.raise_for_status()
                data = r.json()
                if data.get("code") not in (0, None, "0", ""):
                    raise ValueError(f"BingX {data.get('code')}: {data.get('msg')}")
                return data
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    wait = 1.5 ** attempt
                    logger.warning(f"GET {path} intento {attempt+1}: {e} — espera {wait:.1f}s")
                    time.sleep(wait)
                else:
                    raise

    def _get_signed(self, path: str, params: dict = None) -> dict:
        p = dict(params or {})
        p["timestamp"] = int(time.time() * 1000)
        p["signature"] = self._sign(p)
        r = self._client.get(self.base + path, params=p, headers=self._headers())
        r.raise_for_status()
        return r.json()

    def _post_signed(self, path: str, params: dict) -> dict:
        p = dict(params)
        p["timestamp"] = int(time.time() * 1000)
        p["signature"] = self._sign(p)
        r = self._client.post(
            self.base + path,
            headers={**self._headers(), "Content-Type": "application/x-www-form-urlencoded"},
            content=urllib.parse.urlencode(p),
        )
        r.raise_for_status()
        return r.json()

    def _delete_signed(self, path: str, params: dict = None) -> dict:
        p = dict(params or {})
        p["timestamp"] = int(time.time() * 1000)
        p["signature"] = self._sign(p)
        r = self._client.delete(self.base + path, params=p, headers=self._headers())
        r.raise_for_status()
        return r.json()

    # ── Market data ───────────────────────────────────────────────────────────

    def get_klines(self, symbol: str, interval: str, limit: int = 300) -> pd.DataFrame:
        data = self._get_public("/openApi/swap/v3/quote/klines",
                                {"symbol": symbol, "interval": interval, "limit": limit})
        rows = data.get("data") or []
        if not rows:
            raise ValueError(f"Sin klines: {symbol} {interval}")

        df = pd.DataFrame(rows)
        if isinstance(rows[0], list):
            cols = ["open_time","open","high","low","close","volume"]
            df.columns = cols[:len(df.columns)]
        else:
            df = df.rename(columns={
                "time":"open_time","t":"open_time","T":"open_time",
                "o":"open","h":"high","l":"low","c":"close","v":"volume",
            })
            if "open_time" not in df.columns and len(df.columns) >= 6:
                df.columns = ["open_time","open","high","low","close","volume"] + list(df.columns[6:])

        df = df[["open_time","open","high","low","close","volume"]].copy()
        for col in ["open","high","low","close","volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["open_time"] = pd.to_datetime(
            pd.to_numeric(df["open_time"], errors="coerce"), unit="ms", errors="coerce"
        )
        df.dropna(inplace=True)
        df.set_index("open_time", inplace=True)
        return df

    def get_price(self, symbol: str) -> float:
        try:
            data = self._get_public("/openApi/swap/v2/quote/price", {"symbol": symbol})
            return safe_float(data.get("data", {}).get("price", 0))
        except Exception:
            data = self._get_public("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
            tickers = data.get("data", [])
            if isinstance(tickers, list) and tickers:
                return safe_float(tickers[0].get("lastPrice", 0))
            return safe_float(tickers.get("lastPrice", 0))

    def get_funding_rate(self, symbol: str) -> float:
        try:
            data = self._get_public("/openApi/swap/v2/quote/fundingRate", {"symbol": symbol})
            d = data.get("data", {})
            if isinstance(d, list):
                return safe_float(d[0].get("fundingRate", 0)) if d else 0.0
            return safe_float(d.get("fundingRate", 0))
        except Exception:
            return 0.0

    def get_all_symbols(self, min_volume_usdt: float = 5_000_000,
                        top_n: int = 50, blacklist: set = None) -> List[str]:
        _bl = blacklist or {"USDC-USDT","BUSD-USDT","TUSD-USDT","DAI-USDT","USDT-USDT"}
        try:
            data = self._get_public("/openApi/swap/v2/quote/ticker")
            tickers = data.get("data", [])
            candidates = []
            for t in tickers:
                sym = t.get("symbol", "")
                if not sym.endswith("-USDT") or sym in _bl:
                    continue
                vol = safe_float(t.get("quoteVolume") or t.get("volume", 0))
                if vol < min_volume_usdt:
                    continue
                candidates.append((sym, vol))
            candidates.sort(key=lambda x: x[1], reverse=True)
            result = [s for s, _ in candidates[:top_n]]
            logger.info(f"Símbolos: {len(result)} pares activos")
            return result
        except Exception as e:
            logger.error(f"get_all_symbols: {e}")
            return ["BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT"]

    # ── Account ───────────────────────────────────────────────────────────────

    def get_balance(self) -> float:
        if not self.api_key:
            return 0.0
        try:
            data = self._get_signed("/openApi/swap/v2/user/balance")
            d = data.get("data", {})
            if isinstance(d, dict):
                bal = d.get("balance", {})
                if isinstance(bal, dict):
                    return safe_float(bal.get("availableMargin", bal.get("balance", 0)))
                return safe_float(d.get("availableMargin", d.get("equity", 0)))
        except Exception as e:
            logger.error(f"get_balance: {e}")
        return 0.0

    # ── Trading ───────────────────────────────────────────────────────────────

    def _set_leverage(self, symbol: str, leverage: int):
        for side in ("LONG", "SHORT"):
            try:
                self._post_signed("/openApi/swap/v2/trade/leverage",
                                  {"symbol": symbol, "side": side, "leverage": str(leverage)})
            except Exception as e:
                logger.warning(f"set_leverage {symbol}/{side}: {e}")

    def calc_quantity(self, symbol: str, position_usdt: float, price: float) -> float:
        """Cantidad en moneda base para abrir posición."""
        if price <= 0:
            return 0.0
        qty = position_usdt / price
        if price >= 10_000:
            return round(qty, 4)
        elif price >= 100:
            return round(qty, 3)
        elif price >= 1:
            return round(qty, 2)
        else:
            return round(qty, 1)

    def place_market_order(self, symbol: str, side: str, quantity: float,
                           stop_loss: float, take_profit: float,
                           leverage: int = 5) -> dict:
        """
        Abre posición MARKET con SL y TP automáticos.
        side: 'BUY' (long) | 'SELL' (short)
        """
        if not self.api_key:
            raise ValueError("API key no configurada")

        self._set_leverage(symbol, leverage)
        position_side = "LONG" if side == "BUY" else "SHORT"

        # Orden principal
        params = {
            "symbol":       symbol,
            "side":         side,
            "positionSide": position_side,
            "type":         "MARKET",
            "quantity":     str(quantity),
        }

        resp = self._post_signed("/openApi/swap/v2/trade/order", params)
        code = resp.get("code", -1)
        if code not in (0, 200, None):
            raise RuntimeError(f"Orden fallida [{symbol}] code={code} msg={resp.get('msg','')}")

        order_id = resp.get("data", {}).get("order", {}).get("orderId", "")
        logger.info(f"Orden abierta {symbol} {side} qty={quantity} id={order_id}")

        # SL
        try:
            close_side = "SELL" if side == "BUY" else "BUY"
            self._post_signed("/openApi/swap/v2/trade/order", {
                "symbol":       symbol,
                "side":         close_side,
                "positionSide": position_side,
                "type":         "STOP_MARKET",
                "quantity":     str(quantity),
                "stopPrice":    str(round(stop_loss, 6)),
                "workingType":  "MARK_PRICE",
                "reduceOnly":   "true",
            })
            logger.info(f"SL colocado {symbol} @ {stop_loss:.6f}")
        except Exception as e:
            logger.error(f"SL fallido {symbol}: {e}")

        # TP
        try:
            close_side = "SELL" if side == "BUY" else "BUY"
            self._post_signed("/openApi/swap/v2/trade/order", {
                "symbol":       symbol,
                "side":         close_side,
                "positionSide": position_side,
                "type":         "TAKE_PROFIT_MARKET",
                "quantity":     str(quantity),
                "stopPrice":    str(round(take_profit, 6)),
                "workingType":  "MARK_PRICE",
                "reduceOnly":   "true",
            })
            logger.info(f"TP colocado {symbol} @ {take_profit:.6f}")
        except Exception as e:
            logger.error(f"TP fallido {symbol}: {e}")

        return resp

    def close_partial(self, symbol: str, side: str, quantity: float) -> dict:
        """Cierre parcial de posición."""
        position_side = "SHORT" if side == "SELL" else "LONG"
        try:
            resp = self._post_signed("/openApi/swap/v2/trade/order", {
                "symbol":       symbol,
                "side":         side,
                "positionSide": position_side,
                "type":         "MARKET",
                "quantity":     str(quantity),
                "reduceOnly":   "true",
            })
            logger.info(f"Cierre parcial {symbol} {side} qty={quantity}")
            return resp
        except Exception as e:
            logger.error(f"close_partial {symbol}: {e}")
            return {}

    def update_stop_loss(self, symbol: str, side: str, new_sl: float) -> dict:
        """Cancela órdenes SL actuales y coloca nuevo SL."""
        try:
            self._delete_signed("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})
        except Exception as e:
            logger.warning(f"cancel_orders {symbol}: {e}")
        position_side = "LONG" if side == "BUY" else "SHORT"
        close_side    = "SELL" if side == "BUY" else "BUY"
        try:
            resp = self._post_signed("/openApi/swap/v2/trade/order", {
                "symbol":       symbol,
                "side":         close_side,
                "positionSide": position_side,
                "type":         "STOP_MARKET",
                "stopPrice":    str(round(new_sl, 6)),
                "workingType":  "MARK_PRICE",
                "reduceOnly":   "true",
                "closePosition":"true",
            })
            logger.info(f"SL actualizado {symbol} @ {new_sl:.6f}")
            return resp
        except Exception as e:
            logger.error(f"update_stop_loss {symbol}: {e}")
            return {}
