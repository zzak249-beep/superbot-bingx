"""
Market Scanner
Scans all USDT perpetual pairs on BingX and ranks them by signal quality.
Filters by volume, ADX, spread. Returns top-N candidates.
"""
import logging, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

from bingx_client import BingXClient
from strategy import compute_signal, Signal

log = logging.getLogger(__name__)

# ── Filter thresholds ──────────────────────────────────────────────────
MIN_24H_VOLUME_USDT = 5_000_000     # $5M daily volume minimum
MAX_SYMBOLS_TO_SCAN = 120           # limit API calls
TOP_N_RESULTS       = 10            # return best N signals
SCAN_THREADS        = 8             # parallel requests
HTF_INTERVAL        = "1h"          # higher timeframe for RSI context
LTF_INTERVAL        = "15m"         # execution timeframe


@dataclass
class ScanResult:
    symbol:    str
    signal:    Signal
    htf_rsi:   float
    volume_24h: float
    score_adj: float   # adjusted score considering HTF alignment


class Scanner:
    def __init__(self, client: BingXClient):
        self.client = client
        self._symbol_meta: dict = {}   # cache symbol info

    def _get_htf_rsi(self, symbol: str) -> float:
        try:
            candles = self.client.get_klines(symbol, HTF_INTERVAL, 50)
            if len(candles) < 20:
                return 50.0
            import numpy as np
            from strategy import _rsi
            closes = np.array([c["close"] for c in candles], dtype=float)
            rsi = _rsi(closes, 14)
            return float(rsi[-1]) if not np.isnan(rsi[-1]) else 50.0
        except Exception:
            return 50.0

    def _scan_symbol(self, symbol: str) -> Optional[ScanResult]:
        try:
            # Volume filter first (cheap check)
            vol = self.client.get_24h_volume(symbol)
            if vol < MIN_24H_VOLUME_USDT:
                return None

            # Get candles for strategy
            candles = self.client.get_klines(symbol, LTF_INTERVAL, 250)
            if len(candles) < 150:
                return None

            htf_rsi = self._get_htf_rsi(symbol)
            signal  = compute_signal(candles, htf_rsi)

            if signal.direction == "NONE":
                return None

            # HTF alignment bonus
            htf_bonus = 0
            if signal.direction == "LONG"  and htf_rsi > 55: htf_bonus = 10
            if signal.direction == "SHORT" and htf_rsi < 45: htf_bonus = 10
            score_adj = signal.score + htf_bonus

            return ScanResult(symbol, signal, htf_rsi, vol, score_adj)

        except Exception as e:
            log.debug(f"Scan error {symbol}: {e}")
            return None

    def scan(self) -> list[ScanResult]:
        """Full market scan. Returns ranked list of actionable signals."""
        log.info("🔍 Starting market scan...")
        all_symbols = self.client.get_all_symbols()

        # Sort by last known volume (heuristic: prefer known liquid pairs first)
        priority = ["BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT", "XRP-USDT",
                    "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "DOT-USDT", "MATIC-USDT"]
        other = [s for s in all_symbols if s not in priority]
        symbols = (priority + other)[:MAX_SYMBOLS_TO_SCAN]

        results: list[ScanResult] = []
        with ThreadPoolExecutor(max_workers=SCAN_THREADS) as ex:
            futs = {ex.submit(self._scan_symbol, sym): sym for sym in symbols}
            for fut in as_completed(futs):
                res = fut.result()
                if res:
                    results.append(res)
                time.sleep(0.02)  # gentle rate limit

        # Rank by adjusted score
        results.sort(key=lambda r: r.score_adj, reverse=True)
        top = results[:TOP_N_RESULTS]

        log.info(f"✅ Scan complete. {len(results)} signals found, top {len(top)} selected.")
        for r in top:
            log.info(
                f"  {r.symbol:<16} {r.signal.direction:<5} "
                f"score={r.score_adj:.0f} vol=${r.volume_24h/1e6:.1f}M "
                f"reason: {r.signal.reason}"
            )
        return top
