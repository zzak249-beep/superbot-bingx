"""
scanner.py — Escanea las 20 mejores monedas del día.
Scoring = volumen USDT × (1 + |cambio%|) para capturar
los pares con más movimiento institucional.
"""
import logging
from typing import List, Dict
from bingx_client import BingXClient
from config import TOP_N_SYMBOLS

logger = logging.getLogger(__name__)

# Símbolos problemáticos con spread alto o liquidez baja
SYMBOL_BLOCKLIST = {
    "LUNA-USDT", "LUNC-USDT", "SHIB-USDT",  # alta volatilidad extrema
}


class MarketScanner:
    def __init__(self, client: BingXClient):
        self.client  = client
        self._cached: List[Dict] = []

    def get_top_symbols(self, n: int = TOP_N_SYMBOLS) -> List[Dict]:
        """
        Retorna los N mejores pares ordenados por score.
        Cada elemento: {symbol, price, change_pct, volume_usdt, score}
        """
        tickers = self.client.get_24h_tickers()
        if not tickers:
            logger.warning("No se obtuvieron tickers — usando caché anterior")
            return self._cached or []

        scored = []
        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol.endswith("-USDT"):
                continue
            if symbol in SYMBOL_BLOCKLIST:
                continue
            try:
                price   = float(t.get("lastPrice", 0) or 0)
                vol     = float(t.get("quoteVolume", 0) or 0)
                change  = float(t.get("priceChangePercent", 0) or 0)

                if price < 0.000001 or vol < 500_000:   # filtro polvo y escasa liquidez
                    continue

                score = vol * (1 + abs(change) / 100)
                scored.append({
                    "symbol":      symbol,
                    "price":       price,
                    "change_pct":  round(change, 2),
                    "volume_usdt": round(vol, 0),
                    "score":       score,
                })
            except Exception:
                continue

        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:n]

        self._cached = top
        logger.info(
            f"Scanner: top {len(top)} pares | "
            f"1º {top[0]['symbol']} score={top[0]['score']:.0f}"
            if top else "Scanner: sin resultados"
        )
        return top

    def get_symbol_list(self) -> List[str]:
        return [s["symbol"] for s in self._cached]

    def summary_text(self, top: List[Dict], n: int = 5) -> str:
        lines = []
        for i, s in enumerate(top[:n], 1):
            arrow = "🟢" if s["change_pct"] >= 0 else "🔴"
            lines.append(
                f"  {i}. {arrow} <b>{s['symbol']}</b> "
                f"{s['change_pct']:+.2f}%  "
                f"Vol ${s['volume_usdt']/1e6:.1f}M"
            )
        return "\n".join(lines)
