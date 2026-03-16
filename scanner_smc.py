"""
scanner_smc.py — Escáner de pares BingX para SMC Bot
Standalone: obtiene pares con volumen suficiente directamente de la API.
"""
import logging
import time
import requests

log = logging.getLogger("scanner_smc")

_cache: dict = {"pares": [], "ts": 0}
_CACHE_TTL = 3600   # 1 hora


def get_pares_cached(vol_min_24h: float = 10_000_000) -> list:
    """
    Retorna lista de pares BingX Perpetual Futures con volumen >= vol_min_24h.
    Cacheado 1 hora para no saturar la API.
    """
    if time.time() - _cache["ts"] < _CACHE_TTL and _cache["pares"]:
        return _cache["pares"]

    pares = _get_pares_bingx(vol_min_24h)
    if pares:
        _cache["pares"] = pares
        _cache["ts"]    = time.time()
        log.info(f"[SCAN] {len(pares)} pares con vol≥${vol_min_24h/1e6:.0f}M")
    return pares or _fallback()


def _get_pares_bingx(vol_min: float) -> list:
    try:
        r = requests.get(
            "https://open-api.bingx.com/openApi/swap/v2/quote/ticker",
            timeout=15
        )
        if r.status_code != 200:
            return []
        data = r.json().get("data", [])
        if not data:
            return []

        pares = []
        for t in data:
            sym = t.get("symbol", "")
            if not sym.endswith("-USDT"):
                continue
            try:
                vol = float(t.get("quoteVolume", 0) or 0)
            except (ValueError, TypeError):
                vol = 0
            if vol >= vol_min:
                pares.append(sym)

        # Ordenar por volumen descendente
        vols = {}
        for t in data:
            sym = t.get("symbol","")
            try: vols[sym] = float(t.get("quoteVolume",0) or 0)
            except: pass
        pares.sort(key=lambda x: vols.get(x, 0), reverse=True)
        return pares

    except Exception as e:
        log.error(f"[SCAN] Error obteniendo pares: {e}")
        return []


def _fallback() -> list:
    """Lista fija de pares prioritarios si falla la API."""
    return [
        "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT",
        "AVAX-USDT", "DOGE-USDT", "LINK-USDT", "ARB-USDT", "OP-USDT",
        "NEAR-USDT", "APT-USDT", "SUI-USDT", "INJ-USDT", "TIA-USDT",
        "MATIC-USDT", "DOT-USDT", "ATOM-USDT", "FIL-USDT", "LTC-USDT",
    ]
