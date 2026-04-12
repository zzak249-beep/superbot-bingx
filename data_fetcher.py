"""
data_fetcher.py — Pulls OHLCV candles from BingX and returns pandas DataFrame
"""
import logging
from typing import Optional

import pandas as pd

from bingx_client import BingXClient

logger = logging.getLogger("DataFetcher")


def fetch_ohlcv(bingx: BingXClient, symbol: str, interval: str,
                limit: int = 500) -> Optional[pd.DataFrame]:
    """
    Returns DataFrame with columns: open, high, low, close, volume
    Indexed by datetime UTC. Returns None on failure.
    """
    try:
        raw = bingx.get_klines(symbol, interval, limit)
        if not raw:
            logger.warning(f"Empty klines for {symbol}")
            return None

        df = pd.DataFrame(raw)
        # BingX returns: time, open, high, low, close, volume (as strings/numbers)
        rename = {}
        for col in df.columns:
            lc = col.lower()
            if lc in ("time", "t", "timestamp"):
                rename[col] = "time"
            elif lc in ("open", "o"):
                rename[col] = "open"
            elif lc in ("high", "h"):
                rename[col] = "high"
            elif lc in ("low", "l"):
                rename[col] = "low"
            elif lc in ("close", "c"):
                rename[col] = "close"
            elif lc in ("volume", "v", "vol"):
                rename[col] = "volume"
        df.rename(columns=rename, inplace=True)

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
            df.set_index("time", inplace=True)

        df.sort_index(inplace=True)
        df.dropna(subset=["open", "high", "low", "close"], inplace=True)
        return df

    except Exception as e:
        logger.error(f"fetch_ohlcv {symbol} {interval}: {e}", exc_info=True)
        return None
