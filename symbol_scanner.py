"""
Symbol Scanner — Conflux 4 Bot
Obtiene y refresca la lista de pares con mayor volumen
"""
import time
from loguru import logger


class SymbolScanner:
    def __init__(self, min_volume_usdt: float = 5_000_000,
                 top_n: int = 50, refresh_seconds: int = 14400):
        self.min_volume_usdt = min_volume_usdt
        self.top_n           = top_n
        self.refresh_seconds = refresh_seconds
        self._last_refresh   = 0.0
        self._symbols        = []

    @property
    def needs_refresh(self) -> bool:
        return (time.time() - self._last_refresh) >= self.refresh_seconds

    def get_symbols(self, bingx) -> list:
        try:
            symbols = bingx.get_all_symbols(
                min_volume_usdt=self.min_volume_usdt,
                top_n=self.top_n,
            )
            if symbols:
                self._symbols      = symbols
                self._last_refresh = time.time()
                logger.info(f"Scanner: {len(symbols)} pares actualizados")
            return self._symbols
        except Exception as e:
            logger.error(f"SymbolScanner: {e}")
            return self._symbols or ["BTC-USDT","ETH-USDT","SOL-USDT"]
