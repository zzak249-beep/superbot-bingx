"""
BingX Client v3
- OHLCV para múltiples timeframes (MTF)
- Funding rate actual
- Order book para detectar liquidez
- Retry automático con backoff exponencial
- Cálculo de cantidad por USDT de posición
- get_all_symbols(): obtiene TODOS los pares perpetuos activos de BingX
"""

import hashlib
import hmac
import time
import urllib.parse
from typing import Optional, List
import httpx
import pandas as pd
import numpy as np
from loguru import logger


BINGX_BASE = "https://open-api.bingx.com"
BINGX_TEST  = "https://open-api-vst.bingx.com"

MAX_RETRIES = 3
RETRY_DELAY = 2.0


class BingXClient:
    def __init__(self, api_key: str = "", secret_key: str = "", testnet: bool = False):
        self.api_key = api_key
        self.secret = secret_key
        self.base = BINGX_TEST if testnet else BINGX_BASE

    def _sign(self, params: dict) -> str:
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()

    def _headers(self):
        return {"X-BX-APIKEY": self.api_key}

    def _get_public(self, path: str, params: dict = None, retries: int = MAX_RETRIES) -> dict:
        url = self.base + path
        for attempt in range(retries):
            try:
                r = httpx.get(url, params=params or {}, timeout=15)
                r.raise_for_status()
                data = r.json()
                if data.get("code") not in (0, None, "0", ""):
                    raise ValueError(f"BingX error {data.get('code')}: {data.get('msg')}")
                return data
            except Exception as e:
                if attempt < retries - 1:
                    wait = RETRY_DELAY * (2 ** attempt)
                    logger.warning(f"GET {path} falló (intento {attempt+1}): {e} — reintentando en {wait}s")
                    time.sleep(wait)
                else:
                    raise

    def _get_signed(self, path: str, params: dict = None) -> dict:
        params = params or {}
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(params)
        url = self.base + path + "?" + urllib.parse.urlencode(params)
        r = httpx.get(url, headers=self._headers(), timeout=12)
        r.raise_for_status()
        return r.json()

    def _post_signed(self, path: str, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(params)
        r = httpx.post(
            self.base + path,
            headers={**self._headers(), "Content-Type": "application/x-www-form-urlencoded"},
            content=urllib.parse.urlencode(params),
            timeout=12
        )
        r.raise_for_status()
        return r.json()

    # ── TODOS LOS SÍMBOLOS ACTIVOS ────────────────────────────────────────
    def get_all_symbols(
        self,
        min_volume_usdt: float = 5_000_000,
        top_n: int = 50,
        blacklist: set = None,
    ) -> List[str]:
        """
        Obtiene todos los contratos perpetuos USDT de BingX,
        filtra por volumen 24h y devuelve el top N ordenado por volumen.

        Args:
            min_volume_usdt: Volumen mínimo 24h en USDT (default: 5M)
            top_n: Máximo de símbolos a devolver (default: 50)
            blacklist: Conjunto de símbolos a excluir
        """
        _blacklist = blacklist or {
            "USDC-USDT", "BUSD-USDT", "TUSD-USDT", "DAI-USDT",
            "USDP-USDT", "FDUSD-USDT", "USDT-USDT",
        }

        try:
            data = self._get_public("/openApi/swap/v2/quote/ticker")
            tickers = data.get("data", [])

            if not tickers:
                logger.warning("get_all_symbols: no se recibieron tickers")
                return []

            candidates = []
            for t in tickers:
                symbol = t.get("symbol", "")
                if not symbol.endswith("-USDT"):
                    continue
                if symbol in _blacklist:
                    continue

                try:
                    quote_vol = float(
                        t.get("quoteVolume") or
                        t.get("turnover") or
                        t.get("volume", 0)
                    )
                    # Si viene en unidades base, multiplicar por precio
                    if quote_vol < 1000 and t.get("lastPrice"):
                        quote_vol = float(t.get("volume", 0)) * float(t.get("lastPrice", 0))
                except (ValueError, TypeError):
                    continue

                if quote_vol < min_volume_usdt:
                    continue

                candidates.append((symbol, quote_vol))

            if not candidates:
                logger.warning(
                    f"0 símbolos superaron {min_volume_usdt:,.0f} USDT — usando fallback sin filtro"
                )
                for t in tickers:
                    s = t.get("symbol", "")
                    if s.endswith("-USDT") and s not in _blacklist:
                        try:
                            v = float(t.get("quoteVolume") or t.get("volume", 0))
                            candidates.append((s, v))
                        except Exception:
                            pass

            candidates.sort(key=lambda x: x[1], reverse=True)
            result = [s for s, _ in candidates[:top_n]]

            logger.info(
                f"📡 Símbolos activos ({len(result)}): "
                + ", ".join(result[:10])
                + (f"... +{len(result)-10} más" if len(result) > 10 else "")
            )
            return result

        except Exception as e:
            logger.error(f"Error obteniendo símbolos: {e}")
            return []

    # ── OHLCV ─────────────────────────────────────────────────────────────
    def get_klines(self, symbol: str, interval: str, limit: int = 300) -> pd.DataFrame:
        data = self._get_public(
            "/openApi/swap/v3/quote/klines",
            {"symbol": symbol, "interval": interval, "limit": limit}
        )
        rows = data.get("data") or []
        if not rows:
            raise ValueError(f"Sin datos klines para {symbol} {interval}")

        df = pd.DataFrame(rows)

        if isinstance(rows[0], list):
            n_cols = len(df.columns)
            base_cols = ["open_time", "open", "high", "low", "close", "volume"]
            extra_cols = [f"_extra_{i}" for i in range(max(0, n_cols - len(base_cols)))]
            df.columns = (base_cols + extra_cols)[:n_cols]
        else:
            rename = {
                "time": "open_time", "t": "open_time", "T": "open_time",
                "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume",
            }
            df = df.rename(columns=rename)
            if "open_time" not in df.columns:
                cols = list(df.columns)
                base_cols = ["open_time", "open", "high", "low", "close", "volume"]
                for i, c in enumerate(base_cols):
                    if i < len(cols):
                        cols[i] = c
                df.columns = cols

        df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["open_time"] = pd.to_datetime(
            pd.to_numeric(df["open_time"], errors="coerce"),
            unit="ms", errors="coerce"
        )
        df.dropna(inplace=True)
        df.set_index("open_time", inplace=True)
        return df

    def get_price(self, symbol: str) -> float:
        data = self._get_public("/openApi/swap/v2/quote/price", {"symbol": symbol})
        return float(data["data"]["price"])

    # ── Funding rate ──────────────────────────────────────────────────────
    def get_funding_rate(self, symbol: str) -> float:
        try:
            data = self._get_public("/openApi/swap/v2/quote/fundingRate", {"symbol": symbol})
            d = data.get("data", {})
            if isinstance(d, list):
                return float(d[0].get("fundingRate", 0)) if d else 0.0
            if isinstance(d, dict):
                return float(d.get("fundingRate", 0))
            return 0.0
        except Exception as e:
            logger.warning(f"No se pudo obtener funding rate para {symbol}: {e}")
            return 0.0

    def get_bid_ask_spread_pct(self, symbol: str) -> float:
        try:
            data = self._get_public("/openApi/swap/v2/quote/bookTicker", {"symbol": symbol})
            bid = float(data["data"]["bidPrice"])
            ask = float(data["data"]["askPrice"])
            mid = (bid + ask) / 2
            return (ask - bid) / mid * 100 if mid > 0 else 999.0
        except Exception as e:
            logger.warning(f"Spread no disponible para {symbol}: {e}")
            return 0.0

    def calc_quantity(self, symbol: str, position_usdt: float, price: float = None) -> float:
        if price is None:
            price = self.get_price(symbol)
        qty = position_usdt / price
        return round(qty, 3)

    def get_balance(self) -> float:
        if not self.api_key:
            return 0.0
        try:
            data = self._get_signed("/openApi/swap/v2/user/balance")
            return float(data.get("data", {}).get("balance", {}).get("availableMargin", 0))
        except Exception as e:
            logger.error(f"Error obteniendo balance: {e}")
            return 0.0

    def place_market_order(self, symbol: str, side: str, quantity: float,
                           stop_loss: float = None, take_profit: float = None) -> dict:
        if not self.api_key:
            raise ValueError("API key requerida para colocar órdenes")
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": "LONG" if side == "BUY" else "SHORT",
            "type": "MARKET",
            "quantity": quantity,
        }
        if stop_loss:
            params["stopLoss"] = f'{{"type":"MARK_PRICE","stopPrice":{stop_loss},"workingType":"MARK_PRICE"}}'
        if take_profit:
            params["takeProfit"] = f'{{"type":"MARK_PRICE","stopPrice":{take_profit},"workingType":"MARK_PRICE"}}'
        result = self._post_signed("/openApi/swap/v2/trade/order", params)
        logger.info(f"Orden ejecutada: {side} {symbol} qty={quantity} → {result}")
        return result

    def close_partial(self, symbol: str, side: str, quantity: float) -> dict:
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": "LONG" if side == "SELL" else "SHORT",
            "type": "MARKET",
            "quantity": quantity,
            "reduceOnly": "true",
        }
        return self._post_signed("/openApi/swap/v2/trade/order", params)

    def update_stop_loss(self, symbol: str, side: str, stop_price: float) -> dict:
        try:
            self._post_signed("/openApi/swap/v2/trade/allOpenOrders",
                              {"symbol": symbol, "type": "STOP_MARKET"})
        except Exception:
            pass
        params = {
            "symbol": symbol,
            "side": "SELL" if side == "BUY" else "BUY",
            "positionSide": "LONG" if side == "BUY" else "SHORT",
            "type": "STOP_MARKET",
            "stopPrice": stop_price,
            "closePosition": "true",
            "workingType": "MARK_PRICE",
        }
        return self._post_signed("/openApi/swap/v2/trade/order", params)

    def get_open_position(self, symbol: str) -> Optional[dict]:
        try:
            data = self._get_signed("/openApi/swap/v2/user/positions", {"symbol": symbol})
            positions = data.get("data", [])
            for p in positions:
                if float(p.get("positionAmt", 0)) != 0:
                    return p
        except Exception as e:
            logger.error(f"Error verificando posición {symbol}: {e}")
        return None
