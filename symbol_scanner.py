"""
utils/symbol_scanner.py — Scanner dinámico de símbolos.

En vez de una lista fija, el bot:
  1. Consulta todos los contratos perpetuos de BingX (~200 pares)
  2. Filtra por volumen mínimo en USDT (liquidity gate)
  3. Ordena por volumen descendente y toma el top N
  4. Rota la lista cada REFRESH_INTERVAL segundos

Esto garantiza que el bot siempre escanea los mercados más activos
sin necesidad de mantener una lista manual.
"""

import asyncio
import logging
import time
from typing import List

log = logging.getLogger("symbol_scanner")

# ── Parámetros ────────────────────────────────────────────────────────────────
import os

# Volumen mínimo en USDT en las últimas 24h para considerar el par
# Soporta tanto MIN_QUOTE_VOLUME como MIN_VOLUME_USDT (Railway legacy)
MIN_QUOTE_VOLUME = float(
    os.getenv("MIN_QUOTE_VOLUME",
    os.getenv("MIN_VOLUME_USDT", "300000"))   # 300K USDT default
)

# Top N pares por volumen que el bot monitoreará activamente
TOP_N_SYMBOLS = int(os.getenv("TOP_N_SYMBOLS", "30"))

# Cada cuántos segundos se refresca la lista de símbolos activos
REFRESH_INTERVAL = int(os.getenv("SYMBOL_REFRESH_INTERVAL", "3600"))  # 1h

# Excluir stablecoins y pares de baja utilidad
BLACKLIST = {
    "USDC-USDT", "BUSD-USDT", "TUSD-USDT", "DAI-USDT",
    "USDP-USDT", "FDUSD-USDT",
}


class SymbolScanner:
    def __init__(self):
        self._symbols: List[str] = []
        self._last_refresh: float = 0.0
        self._lock = asyncio.Lock()

    async def get_symbols(self, rest_client) -> List[str]:
        """
        Devuelve la lista activa de símbolos.
        Refresca automáticamente cada REFRESH_INTERVAL segundos.
        """
        async with self._lock:
            if time.time() - self._last_refresh > REFRESH_INTERVAL or not self._symbols:
                await self._refresh(rest_client)
        return list(self._symbols)

    async def _refresh(self, rest_client):
        """Descarga tickers, filtra y ordena."""
        try:
            tickers = await rest_client.get_tickers()
            if not tickers:
                log.warning("get_tickers devolvió vacío — manteniendo lista anterior")
                return

            candidates = []
            for t in tickers:
                symbol = t.get("symbol", "")

                # Solo pares USDT perpetuos
                if not symbol.endswith("-USDT"):
                    continue
                if symbol in BLACKLIST:
                    continue

                try:
                    quote_vol = float(t.get("quoteVolume",
                                  t.get("volume",
                                  t.get("turnover", 0))))
                except (ValueError, TypeError):
                    continue

                if quote_vol < MIN_QUOTE_VOLUME:
                    continue

                candidates.append((symbol, quote_vol))

            if not candidates:
                log.warning(
                    f"0 símbolos superaron el filtro MIN_QUOTE_VOLUME={MIN_QUOTE_VOLUME:,.0f}. "
                    f"Bajando umbral al 10% del mínimo actual."
                )
                # Fallback: top N sin filtro de volumen
                fallback = []
                for t in tickers:
                    s = t.get("symbol", "")
                    if s.endswith("-USDT") and s not in BLACKLIST:
                        try:
                            v = float(t.get("quoteVolume", t.get("volume", 0)))
                            fallback.append((s, v))
                        except Exception:
                            pass
                candidates = fallback

            # Ordenar por volumen descendente, tomar top N
            candidates.sort(key=lambda x: x[1], reverse=True)
            new_symbols = [s for s, _ in candidates[:TOP_N_SYMBOLS]]

            if new_symbols:
                self._symbols = new_symbols
                self._last_refresh = time.time()
                log.info(
                    f"🔍 Símbolos activos ({len(new_symbols)}): "
                    f"{', '.join(new_symbols[:10])}"
                    + (f"... +{len(new_symbols)-10} más" if len(new_symbols) > 10 else "")
                )
            else:
                log.error("No se pudo obtener ningún símbolo válido de BingX")

        except Exception as e:
            log.error(f"SymbolScanner._refresh error: {e}")

    @property
    def active_symbols(self) -> List[str]:
        return list(self._symbols)


# Instancia global
symbol_scanner = SymbolScanner()
