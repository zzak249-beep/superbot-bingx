"""
symbol_scanner.py — Scanner dinámico de símbolos v3 (síncrono)

Obtiene TODOS los contratos perpetuos USDT de BingX,
filtra por volumen mínimo y rota la lista periódicamente.
Síncrono para integrarse con el bucle principal sin asyncio.
"""

import os
import time
from typing import List
from loguru import logger


# ── Parámetros por defecto (overridable via env vars) ─────────────────────────
MIN_VOLUME_USDT = float(os.getenv("MIN_VOLUME_USDT", "5000000"))   # 5M USDT default
TOP_N_SYMBOLS   = int(os.getenv("TOP_N_SYMBOLS", "50"))
REFRESH_SECONDS = int(os.getenv("SYMBOL_REFRESH_HOURS", "4")) * 3600

BLACKLIST = {
    "USDC-USDT", "BUSD-USDT", "TUSD-USDT", "DAI-USDT",
    "USDP-USDT", "FDUSD-USDT", "USDT-USDT",
}


class SymbolScanner:
    """Scanner síncrono que refresca la lista de símbolos periódicamente."""

    def __init__(
        self,
        min_volume_usdt: float = MIN_VOLUME_USDT,
        top_n: int = TOP_N_SYMBOLS,
        refresh_seconds: int = REFRESH_SECONDS,
        blacklist: set = None,
    ):
        self.min_volume_usdt = min_volume_usdt
        self.top_n = top_n
        self.refresh_seconds = refresh_seconds
        self.blacklist = blacklist or BLACKLIST

        self._symbols: List[str] = []
        self._last_refresh: float = 0.0

    def get_symbols(self, bingx_client) -> List[str]:
        """
        Devuelve la lista activa de símbolos.
        Refresca automáticamente si han pasado más de refresh_seconds.
        """
        if time.time() - self._last_refresh > self.refresh_seconds or not self._symbols:
            self._refresh(bingx_client)
        return list(self._symbols)

    def _refresh(self, bingx_client):
        """Descarga tickers de BingX, filtra por volumen y construye la lista."""
        try:
            new_symbols = bingx_client.get_all_symbols(
                min_volume_usdt=self.min_volume_usdt,
                top_n=self.top_n,
                blacklist=self.blacklist,
            )

            if new_symbols:
                self._symbols = new_symbols
                self._last_refresh = time.time()
                logger.info(
                    f"🔄 Lista actualizada: {len(new_symbols)} pares | "
                    f"Top 5: {', '.join(new_symbols[:5])}"
                )
            else:
                logger.warning("Scanner no obtuvo símbolos — manteniendo lista anterior")
                if not self._symbols:
                    # Fallback de emergencia
                    self._symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT",
                                     "BNB-USDT", "XRP-USDT"]
                    logger.warning(f"Usando fallback: {self._symbols}")

        except Exception as e:
            logger.error(f"SymbolScanner._refresh error: {e}")
            if not self._symbols:
                self._symbols = ["BTC-USDT", "ETH-USDT"]

    @property
    def active_symbols(self) -> List[str]:
        return list(self._symbols)

    @property
    def needs_refresh(self) -> bool:
        return time.time() - self._last_refresh > self.refresh_seconds
