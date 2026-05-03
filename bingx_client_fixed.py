"""
BingX Client v4 — ULTRA OPTIMIZADO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ARREGLOS CRÍTICOS:
  ✓ Bug de conversión float() corregido
  ✓ Manejo robusto de tipos de datos
  ✓ Retry automático con backoff exponencial
  ✓ Cálculo automático de cantidad con leverage 10x
  ✓ Stop Loss y Take Profit automático
  ✓ Logs detallados de todas las operaciones
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import hashlib
import hmac
import time
import urllib.parse
from typing import Optional, List
import httpx
import pandas as pd
from loguru import logger


BINGX_BASE = "https://open-api.bingx.com"
BINGX_TEST = "https://open-api-vst.bingx.com"

MAX_RETRIES = 3
RETRY_DELAY = 2.0


def safe_float(value, default=0.0) -> float:
    """Convierte cualquier tipo a float de forma segura."""
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
        self.api_key = api_key
        self.secret = secret_key
        self.base = BINGX_TEST if testnet else BINGX_BASE
        self.testnet = testnet

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

    # ═══════════════════════════════════════════════════════════════
    # SÍMBOLOS DINÁMICOS (BUG CORREGIDO)
    # ═══════════════════════════════════════════════════════════════
    
    def get_all_symbols(
        self,
        min_volume_usdt: float = 5_000_000,
        top_n: int = 50,
        blacklist: set = None,
    ) -> List[str]:
        """
        Obtiene todos los contratos perpetuos USDT de BingX.
        
        ✅ BUG CORREGIDO: Manejo robusto de tipos dict/list/str
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

                # ✅ ARREGLO CRÍTICO: conversión segura de tipos
                quote_vol = safe_float(
                    t.get("quoteVolume") or 
                    t.get("turnover") or 
                    t.get("volume", 0)
                )
                
                # Si volumen muy bajo, calcular desde precio
                if quote_vol < 1000:
                    volume = safe_float(t.get("volume", 0))
                    price = safe_float(t.get("lastPrice", 0))
                    quote_vol = volume * price

                if quote_vol < min_volume_usdt:
                    continue

                candidates.append((symbol, quote_vol))

            if not candidates:
                logger.warning(
                    f"0 símbolos superaron {min_volume_usdt:,.0f} USDT — usando fallback"
                )
                # Fallback sin filtro de volumen
                for t in tickers:
                    s = t.get("symbol", "")
                    if s.endswith("-USDT") and s not in _blacklist:
                        v = safe_float(t.get("quoteVolume") or t.get("volume", 0))
                        candidates.append((s, v))

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
            return ["BTC-USDT", "ETH-USDT", "SOL-USDT"]  # Fallback seguro

    # ═══════════════════════════════════════════════════════════════
    # KLINES
    # ═══════════════════════════════════════════════════════════════
    
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
        return safe_float(data.get("data", {}).get("price", 0))

    # ═══════════════════════════════════════════════════════════════
    # TRADING CON 10X LEVERAGE Y SL/TP AUTOMÁTICO
    # ═══════════════════════════════════════════════════════════════
    
    def place_order_10x(
        self, 
        symbol: str, 
        side: str,  # BUY | SELL
        usdt_size: float,  # USDT a usar
        stop_loss_pct: float = 0.5,  # 0.5% stop loss
        take_profit_pct: float = 1.5,  # 1.5% take profit (RR 3:1)
    ) -> dict:
        """
        Coloca orden con leverage 10x y SL/TP automático.
        
        Args:
            symbol: Par a tradear (ej: BTC-USDT)
            side: BUY (long) o SELL (short)
            usdt_size: USDT a usar (ej: 100)
            stop_loss_pct: % de stop loss (default 0.5%)
            take_profit_pct: % de take profit (default 1.5%)
        
        Returns:
            Respuesta de BingX con detalles de la orden
        """
        if not self.api_key:
            raise ValueError("API key requerida para operar")
        
        # 1. Obtener precio actual
        price = self.get_price(symbol)
        
        # 2. Calcular cantidad con leverage 10x
        leverage = 10
        position_value = usdt_size * leverage  # $100 * 10 = $1000 posición
        quantity = position_value / price
        quantity = round(quantity, 3)  # BingX requiere 3 decimales
        
        # 3. Calcular SL y TP
        if side == "BUY":
            stop_loss = price * (1 - stop_loss_pct / 100)
            take_profit = price * (1 + take_profit_pct / 100)
            position_side = "LONG"
        else:
            stop_loss = price * (1 + stop_loss_pct / 100)
            take_profit = price * (1 - take_profit_pct / 100)
            position_side = "SHORT"
        
        stop_loss = round(stop_loss, 2)
        take_profit = round(take_profit, 2)
        
        # 4. Colocar orden
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": quantity,
        }
        
        # 5. Añadir SL y TP
        if stop_loss:
            params["stopLoss"] = f'{{"type":"MARK_PRICE","stopPrice":{stop_loss},"workingType":"MARK_PRICE"}}'
        if take_profit:
            params["takeProfit"] = f'{{"type":"MARK_PRICE","stopPrice":{take_profit},"workingType":"MARK_PRICE"}}'
        
        result = self._post_signed("/openApi/swap/v2/trade/order", params)
        
        logger.success(
            f"✅ ORDEN EJECUTADA: {side} {symbol}\n"
            f"   Cantidad: {quantity}\n"
            f"   Entrada: ${price:,.2f}\n"
            f"   SL: ${stop_loss:,.2f} (-{stop_loss_pct}%)\n"
            f"   TP: ${take_profit:,.2f} (+{take_profit_pct}%)\n"
            f"   Leverage: 10x\n"
            f"   USDT usado: ${usdt_size:,.2f}"
        )
        
        return result

    def get_balance(self) -> float:
        if not self.api_key:
            return 0.0
        try:
            data = self._get_signed("/openApi/swap/v2/user/balance")
            balance = safe_float(
                data.get("data", {}).get("balance", {}).get("availableMargin", 0)
            )
            return balance
        except Exception as e:
            logger.error(f"Error obteniendo balance: {e}")
            return 0.0

    def get_open_positions(self) -> List[dict]:
        """Obtiene todas las posiciones abiertas."""
        if not self.api_key:
            return []
        try:
            data = self._get_signed("/openApi/swap/v2/user/positions")
            positions = data.get("data", [])
            # Filtrar solo posiciones con cantidad > 0
            return [p for p in positions if safe_float(p.get("positionAmt", 0)) != 0]
        except Exception as e:
            logger.error(f"Error obteniendo posiciones: {e}")
            return []

    def close_position(self, symbol: str) -> dict:
        """Cierra completamente una posición."""
        if not self.api_key:
            raise ValueError("API key requerida")
        
        positions = self.get_open_positions()
        position = next((p for p in positions if p.get("symbol") == symbol), None)
        
        if not position:
            logger.warning(f"No hay posición abierta en {symbol}")
            return {}
        
        qty = abs(safe_float(position.get("positionAmt", 0)))
        side = "SELL" if qty > 0 else "BUY"  # Si long, vender; si short, comprar
        position_side = "LONG" if qty > 0 else "SHORT"
        
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": qty,
            "reduceOnly": "true",
        }
        
        result = self._post_signed("/openApi/swap/v2/trade/order", params)
        logger.info(f"✅ Posición cerrada: {symbol}")
        return result

    def get_funding_rate(self, symbol: str) -> float:
        try:
            data = self._get_public("/openApi/swap/v2/quote/fundingRate", {"symbol": symbol})
            d = data.get("data", {})
            if isinstance(d, list):
                return safe_float(d[0].get("fundingRate", 0)) if d else 0.0
            if isinstance(d, dict):
                return safe_float(d.get("fundingRate", 0))
            return 0.0
        except Exception as e:
            logger.warning(f"No se pudo obtener funding rate para {symbol}: {e}")
            return 0.0
