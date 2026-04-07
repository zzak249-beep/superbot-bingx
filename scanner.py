"""
Scanner v3.0 – Escanea todos los pares USDT perpetuales de BingX.
Fixes:
  - Connection pool: SCAN_THREADS reducido a 10 (era 20), pool size aumentado
  - HTF: ahora solicita 500 velas (DLO necesita DLO_MEAN_LB=200 + margen)
  - Score ajustado incluye DLO tier A/B y bounce signals
"""
import logging, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

import numpy as np

from bingx_client import BingXClient
from strategy import compute_signal, Signal, _rsi

log = logging.getLogger(__name__)

# ── Filtros ───────────────────────────────────────────────────────────
MIN_24H_VOLUME_USDT = 2_000_000    # $2M (bajado de $3M para más oportunidades)
MIN_PRICE_USDT      = 0.0001
MAX_PRICE_USDT      = 100_000
MIN_CANDLES         = 220          # DLO necesita >= 200 + margen
TOP_N_RESULTS       = 15
SCAN_THREADS        = 10           # era 20 → causaba "connection pool full"
HTF_INTERVAL        = "1h"
LTF_INTERVAL        = "15m"
LTF_LIMIT           = 500          # más velas para DLO_MEAN_LB=200

EXCLUDE_PATTERNS = [
    r"^USDC-", r"^BUSD-", r"^TUSD-", r"^USDP-", r"^DAI-",
    r"^USDT-", r"^FDUSD-", r"^PYUSD-",
    r"BULL-", r"BEAR-", r"UP-", r"DOWN-",
    r"3L-", r"3S-", r"2L-", r"2S-",
    r"^WBTC-", r"^WETH-", r"^STETH-", r"^RETH-",
]

PRIORITY_SYMBOLS = [
    "BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT", "XRP-USDT",
    "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "DOT-USDT", "MATIC-USDT",
    "LINK-USDT", "UNI-USDT", "ATOM-USDT", "LTC-USDT", "BCH-USDT",
    "NEAR-USDT", "FTM-USDT", "ALGO-USDT", "ARB-USDT", "OP-USDT",
    "SUI-USDT", "SEI-USDT", "TIA-USDT", "INJ-USDT", "WLD-USDT",
    "PEPE-USDT", "SHIB-USDT", "TON-USDT", "JUP-USDT", "APT-USDT",
]


def _is_excluded(symbol: str) -> bool:
    return any(re.search(p, symbol, re.IGNORECASE) for p in EXCLUDE_PATTERNS)


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
            closes  = np.array([c["close"] for c in candles], dtype=float)
            rsi_arr = _rsi(closes, 14)
            val     = float(rsi_arr[-1])
            return val if not np.isnan(val) else 50.0
        except Exception:
            return 50.0

    def _scan_symbol(self, symbol: str) -> Optional[ScanResult]:
        try:
            if _is_excluded(symbol):
                return None

            vol = self.client.get_24h_volume(symbol)
            if vol < MIN_24H_VOLUME_USDT:
                return None

            candles = self.client.get_klines(symbol, LTF_INTERVAL, LTF_LIMIT)
            if len(candles) < MIN_CANDLES:
                return None

            last_price = candles[-1]["close"]
            if last_price < MIN_PRICE_USDT or last_price > MAX_PRICE_USDT:
                return None

            htf_rsi = self._get_htf_rsi(symbol)
            signal  = compute_signal(candles, htf_rsi)

            if signal.direction == "NONE":
                return None

            # Score ajustado con bonificaciones por calidad de señal
            htf_bonus, htf_penalty, adx_bonus, tier_bonus = 0, 0, 0, 0

            if signal.direction == "LONG":
                if htf_rsi > 55:  htf_bonus  = 15
                if htf_rsi > 60:  htf_bonus  += 5
                if htf_rsi < 45:  htf_penalty = -20
            else:
                if htf_rsi < 45:  htf_bonus  = 15
                if htf_rsi < 40:  htf_bonus  += 5
                if htf_rsi > 55:  htf_penalty = -20

            if signal.adx > 35:   adx_bonus = 10
            if signal.adx > 45:   adx_bonus += 5

            # Bonus por Tier A (triple confirmación)
            if signal.tier == "A":  tier_bonus = 20

            # Bonus por DLO fuerte
            dlo_bonus = 0
            if signal.direction == "LONG"  and signal.dlo_value >  0.3: dlo_bonus = 15
            if signal.direction == "SHORT" and signal.dlo_value < -0.3: dlo_bonus = 15

            score_adj = (signal.score + htf_bonus + adx_bonus +
                         htf_penalty + tier_bonus + dlo_bonus)

            return ScanResult(symbol, signal, htf_rsi, vol, score_adj)

        except Exception as e:
            log.debug(f"Scan error {symbol}: {e}")
            return None

    def scan(self) -> list[ScanResult]:
        log.info("🔍 Iniciando escaneo completo del mercado (v3.0)...")

        try:
            all_symbols = self.client.get_all_symbols()
        except Exception as e:
            log.error(f"Error obteniendo símbolos: {e}")
            return []

        priority = [s for s in PRIORITY_SYMBOLS if s in set(all_symbols)]
        others   = [s for s in all_symbols if s not in set(PRIORITY_SYMBOLS)]
        symbols  = priority + others

        log.info(
            f"📊 Pares a escanear: {len(symbols)} "
            f"({len(priority)} prio + {len(others)} resto)"
        )

        results: list[ScanResult] = []
        scanned = errors = 0

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

                if scanned % 50 == 0:
                    log.info(
                        f"   Progreso: {scanned}/{len(symbols)} | "
                        f"Señales: {len(results)}"
                    )

                time.sleep(0.015)   # rate limit suave

        results.sort(key=lambda r: r.score_adj, reverse=True)
        top = results[:TOP_N_RESULTS]

        log.info(
            f"✅ Escaneo completo | {scanned} pares | "
            f"{len(results)} señales | {errors} errores"
        )

        for r in top:
            log.info(
                f"  {r.symbol:<16} {r.signal.direction:<5} "
                f"[{r.signal.tier}] score={r.score_adj:.0f} "
                f"vol=${r.volume_24h/1e6:.1f}M "
                f"HTF_RSI={r.htf_rsi:.0f} ADX={r.signal.adx:.0f} "
                f"DLO={r.signal.dlo_value:+.2f} | {r.signal.reason}"
            )

        return top
