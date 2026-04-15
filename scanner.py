"""
scanner.py — Scanner class for SuperBot v4

Wraps BingXClient + strategy.generate_signal.
Returns list of ScanResult(symbol, signal) sorted by score desc.

Env vars used:
  MAX_SYMBOLS       int   100   max pairs to scan
  MIN_VOLUME_USDT   float 500000 24h min volume filter
  SCAN_THREADS      int   12    parallel workers
  FUNDING_LIMIT     float 0.001 skip if abs(funding) > this
  INTERVAL          str   15m   primary candle interval
  ENABLE_SHORTS     bool  true
"""

import os
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional

from strategy import Signal, generate_signal

log = logging.getLogger("Scanner")

MAX_SYMBOLS     = int(os.environ.get("MAX_SYMBOLS",      100))
MIN_VOL_USDT    = float(os.environ.get("MIN_VOLUME_USDT", 500_000))
MAX_WORKERS     = int(os.environ.get("SCAN_THREADS",      12))
FUNDING_LIMIT   = float(os.environ.get("FUNDING_LIMIT",   0.001))
INTERVAL        = os.environ.get("INTERVAL", "15m")
INTERVAL_MTF5   = "5m"
INTERVAL_MTF15  = "15m"
CANDLE_LIMIT    = 220   # enough for all indicators
MIN_SCORE       = float(os.environ.get("MIN_CONFIDENCE", 0.52)) * 100  # convert 0-1 → 0-100

# Symbols to always exclude (stablecoins, leveraged tokens, indices)
EXCLUDE = {
    "USDC-USDT", "BUSD-USDT", "TUSD-USDT", "USDP-USDT", "FDUSD-USDT",
    "DOW-USDT",  "SP500-USDT","GOLD-USDT", "SILVER-USDT",
    "BTCUP-USDT","BTCDOWN-USDT","ETHUP-USDT","ETHDOWN-USDT",
    "Q-USDT", "BEAT-USDT",
}


@dataclass
class ScanResult:
    symbol: str
    signal: Signal
    score:  float


class Scanner:
    def __init__(self, client):
        """client: BingXClient instance"""
        self.client  = client
        self._sym_cache: List[str] = []
        self._cache_ts: float = 0.0

    def _get_symbols(self) -> List[str]:
        now = time.time()
        if self._sym_cache and (now - self._cache_ts) < 300:
            return self._sym_cache
        try:
            syms = self.client.get_symbols()
            syms = [s for s in syms if s not in EXCLUDE][:MAX_SYMBOLS]
            if syms:
                self._sym_cache = syms
                self._cache_ts  = now
                log.info(f"📋 Symbol cache refreshed: {len(syms)} pairs")
        except Exception as e:
            log.warning(f"get_symbols failed: {e}")
        return self._sym_cache

    def _scan_one(self, symbol: str) -> Optional[ScanResult]:
        try:
            # Primary timeframe candles
            raw_1m = self.client.get_klines(symbol, INTERVAL, CANDLE_LIMIT)
            if not raw_1m or len(raw_1m) < 120:
                return None

            candles_1m  = _parse_klines(raw_1m)
            if not candles_1m:
                return None

            # Volume filter (sum last 50 bars × close price)
            vol_usdt = sum(c["close"] * c["volume"] for c in candles_1m[-50:])
            if vol_usdt < MIN_VOL_USDT:
                return None

            # Funding rate gate
            funding = self.client.get_funding_rate(symbol)
            # (funding filter applied after direction known)

            # MTF candles (best-effort)
            raw_5m  = self.client.get_klines(symbol, INTERVAL_MTF5,  80)
            raw_15m = self.client.get_klines(symbol, INTERVAL_MTF15, 60)
            candles_5m  = _parse_klines(raw_5m)  if raw_5m  else []
            candles_15m = _parse_klines(raw_15m) if raw_15m else []

            sig = generate_signal(candles_1m, candles_5m, candles_15m, symbol)
            if sig is None:
                return None

            # Score gate
            if sig.score < MIN_SCORE:
                log.debug(f"  {symbol} score={sig.score:.0f} < {MIN_SCORE:.0f} → skip")
                return None

            # Funding gate
            if sig.direction == "LONG"  and funding >  FUNDING_LIMIT:
                log.debug(f"  {symbol} LONG skipped: funding={funding:.4f}")
                return None
            if sig.direction == "SHORT" and funding < -FUNDING_LIMIT:
                log.debug(f"  {symbol} SHORT skipped: funding={funding:.4f}")
                return None

            log.info(
                f"  ✨ {symbol} {sig.direction} score={sig.score:.0f} "
                f"[{sig.tier}] RR={abs(sig.tp1-sig.entry)/max(abs(sig.sl-sig.entry),1e-9):.1f}x"
            )
            return ScanResult(symbol=symbol, signal=sig, score=sig.score)

        except Exception as e:
            log.debug(f"  scan_one {symbol}: {e}")
            return None

    def scan(self) -> List[ScanResult]:
        symbols = self._get_symbols()
        if not symbols:
            log.warning("No symbols to scan")
            return []

        log.info(f"🔍 Scanning {len(symbols)} symbols (workers={MAX_WORKERS})...")
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(self._scan_one, s): s for s in symbols}
            for fut in as_completed(futures):
                r = fut.result()
                if r is not None:
                    results.append(r)

        # Sort: S-tier first, then by score descending
        results.sort(key=lambda r: (-{"S": 4, "A": 3, "B": 2, "C": 1}.get(r.signal.tier, 0),
                                    -r.score))
        log.info(f"🎯 Signals found: {len(results)} / {len(symbols)}")
        return results


# ── Kline parser ──────────────────────────────────────────────────────────────

def _parse_klines(raw: list) -> list:
    """
    BingX kline formats:
      v3: list of dicts  {"open","high","low","close","volume",...}
      v2: list of lists  [ts, open, high, low, close, volume]
    Returns list of dicts with float values.
    """
    out = []
    for k in raw:
        try:
            if isinstance(k, dict):
                c = {
                    "open":   float(k.get("open",   k.get("o", 0))),
                    "high":   float(k.get("high",   k.get("h", 0))),
                    "low":    float(k.get("low",    k.get("l", 0))),
                    "close":  float(k.get("close",  k.get("c", 0))),
                    "volume": float(k.get("volume", k.get("v", 0))),
                }
            elif isinstance(k, (list, tuple)) and len(k) >= 6:
                c = {
                    "open":   float(k[1]),
                    "high":   float(k[2]),
                    "low":    float(k[3]),
                    "close":  float(k[4]),
                    "volume": float(k[5]),
                }
            else:
                continue
            if c["high"] > 0 and c["close"] > 0:
                out.append(c)
        except (ValueError, TypeError, IndexError):
            continue
    return out
