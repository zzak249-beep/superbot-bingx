"""
Market Scanner – Escanea TODOS los pares USDT perpetuales de BingX.
Sin límite de símbolos. Filtrado inteligente para maximizar calidad de señal.
"""
import logging, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

import numpy as np

from bingx_client import BingXClient
from strategy import compute_signal, Signal, _rsi

log = logging.getLogger(__name__)

# ── Filtros de calidad ────────────────────────────────────────────────
MIN_24H_VOLUME_USDT = 3_000_000    # $3M mínimo (ampliado para más pares)
MIN_PRICE_USDT      = 0.0001       # excluir tokens basura < $0.0001
MAX_PRICE_USDT      = 100_000      # excluir si precio irreal
MIN_CANDLES         = 150          # mínimo de velas para calcular indicadores
TOP_N_RESULTS       = 15           # mejores señales a devolver
SCAN_THREADS        = 20           # hilos paralelos
HTF_INTERVAL        = "1h"         # timeframe superior para RSI
LTF_INTERVAL        = "15m"        # timeframe de ejecución

# Pares a excluir (stablecoins, wrapped tokens, leveraged tokens, etc.)
EXCLUDE_PATTERNS = [
    r"^USDC-", r"^BUSD-", r"^TUSD-", r"^USDP-", r"^DAI-",   # stablecoins
    r"^USDT-", r"^FDUSD-", r"^PYUSD-",
    r"BULL-", r"BEAR-", r"UP-", r"DOWN-",                      # leveraged tokens
    r"3L-", r"3S-", r"2L-", r"2S-",
    r"^WBTC-", r"^WETH-", r"^STETH-", r"^RETH-",              # wrapped
]

# Prioridad: pares más líquidos primero (se escanean antes)
PRIORITY_SYMBOLS = [
    "BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT", "XRP-USDT",
    "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "DOT-USDT", "MATIC-USDT",
    "LINK-USDT", "UNI-USDT", "ATOM-USDT", "LTC-USDT", "BCH-USDT",
    "NEAR-USDT", "FTM-USDT", "ALGO-USDT", "VET-USDT", "FIL-USDT",
    "ICP-USDT", "SAND-USDT", "MANA-USDT", "APE-USDT", "OP-USDT",
    "ARB-USDT", "SUI-USDT", "SEI-USDT", "TIA-USDT", "INJ-USDT",
    "WLD-USDT", "PEPE-USDT", "SHIB-USDT", "TON-USDT", "JUP-USDT",
]


def _is_excluded(symbol: str) -> bool:
    for pat in EXCLUDE_PATTERNS:
        if re.search(pat, symbol, re.IGNORECASE):
            return True
    return False


@dataclass
class ScanResult:
    symbol:     str
    signal:     Signal
    htf_rsi:    float
    volume_24h: float
    score_adj:  float


class Scanner:
    def __init__(self, client: BingXClient):
        self.client = client

    def _get_htf_rsi(self, symbol: str) -> float:
        try:
            candles = self.client.get_klines(symbol, HTF_INTERVAL, 60)
            if len(candles) < 20:
                return 50.0
            closes = np.array([c["close"] for c in candles], dtype=float)
            rsi_arr = _rsi(closes, 14)
            val = float(rsi_arr[-1])
            return val if not np.isnan(val) else 50.0
        except Exception:
            return 50.0

    def _scan_symbol(self, symbol: str) -> Optional[ScanResult]:
        try:
            # Excluir por nombre
            if _is_excluded(symbol):
                return None

            # Filtro de volumen (llamada barata)
            vol = self.client.get_24h_volume(symbol)
            if vol < MIN_24H_VOLUME_USDT:
                return None

            # Obtener velas LTF
            candles = self.client.get_klines(symbol, LTF_INTERVAL, 300)
            if len(candles) < MIN_CANDLES:
                return None

            # Filtro de precio
            last_price = candles[-1]["close"]
            if last_price < MIN_PRICE_USDT or last_price > MAX_PRICE_USDT:
                return None

            # RSI en HTF
            htf_rsi = self._get_htf_rsi(symbol)

            # Calcular señal
            signal = compute_signal(candles, htf_rsi)
            if signal.direction == "NONE":
                return None

            # Bonus HTF: si RSI HTF alinea con dirección
            htf_bonus = 0
            if signal.direction == "LONG"  and htf_rsi > 55: htf_bonus = 15
            if signal.direction == "SHORT" and htf_rsi < 45: htf_bonus = 15
            if signal.direction == "LONG"  and htf_rsi > 60: htf_bonus = 5   # extra
            if signal.direction == "SHORT" and htf_rsi < 40: htf_bonus = 5

            # Bonus ADX fuerte
            adx_bonus = 0
            if signal.adx > 35: adx_bonus = 10
            if signal.adx > 45: adx_bonus = 5  # extra

            # Penalización si HTF va en contra
            htf_penalty = 0
            if signal.direction == "LONG"  and htf_rsi < 45: htf_penalty = -20
            if signal.direction == "SHORT" and htf_rsi > 55: htf_penalty = -20

            score_adj = signal.score + htf_bonus + adx_bonus + htf_penalty

            return ScanResult(symbol, signal, htf_rsi, vol, score_adj)

        except Exception as e:
            log.debug(f"Scan error {symbol}: {e}")
            return None

    def scan(self) -> list[ScanResult]:
        """Escanea TODOS los pares USDT disponibles en BingX."""
        log.info("🔍 Iniciando escaneo completo del mercado...")

        # Obtener todos los símbolos activos
        try:
            all_symbols = self.client.get_all_symbols()
        except Exception as e:
            log.error(f"Error obteniendo símbolos: {e}")
            return []

        # Ordenar: prioridad primero, luego el resto
        priority = [s for s in PRIORITY_SYMBOLS if s in all_symbols]
        others   = [s for s in all_symbols if s not in PRIORITY_SYMBOLS]
        symbols  = priority + others

        log.info(f"📊 Total pares a escanear: {len(symbols)} "
                 f"({len(priority)} prioritarios + {len(others)} restantes)")

        results: list[ScanResult] = []
        scanned = 0
        errors  = 0

        with ThreadPoolExecutor(max_workers=SCAN_THREADS) as ex:
            futs = {ex.submit(self._scan_symbol, sym): sym for sym in symbols}
            for fut in as_completed(futs):
                scanned += 1
                try:
                    res = fut.result()
                    if res:
                        results.append(res)
                except Exception:
                    errors += 1

                # Log de progreso cada 50 símbolos
                if scanned % 50 == 0:
                    log.info(f"   Progreso: {scanned}/{len(symbols)} | "
                             f"Señales: {len(results)}")

                time.sleep(0.01)  # rate limit suave

        # Ordenar por score ajustado (mayor primero)
        results.sort(key=lambda r: r.score_adj, reverse=True)
        top = results[:TOP_N_RESULTS]

        log.info(
            f"✅ Escaneo completo | {scanned} pares | "
            f"{len(results)} señales | {errors} errores"
        )

        for r in top:
            log.info(
                f"  {r.symbol:<16} {r.signal.direction:<5} "
                f"score={r.score_adj:.0f}  vol=${r.volume_24h/1e6:.1f}M  "
                f"HTF_RSI={r.htf_rsi:.0f}  ADX={r.signal.adx:.0f}  "
                f"| {r.signal.reason}"
            )

        return top
