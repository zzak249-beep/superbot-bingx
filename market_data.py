"""
market_data.py — Fetching y preparación de datos OHLCV desde BingX
"""
import logging
from typing import Optional, Tuple

import pandas as pd
import numpy as np

from bingx_client import BingXClient
import config as C

log = logging.getLogger(__name__)


def fetch_ohlcv(client: BingXClient, symbol: str,
                interval: str, limit: int = 250) -> Optional[pd.DataFrame]:
    """
    Descarga klines y devuelve DataFrame con columnas:
    open, high, low, close, volume — indexado por timestamp UTC
    """
    raw = client.get_klines(symbol, interval, limit)
    if not raw:
        log.error(f"Sin datos para {symbol} {interval}")
        return None

    rows = []
    for k in raw:
        try:
            # BingX devuelve [time, open, high, low, close, volume, ...]
            if isinstance(k, list):
                ts  = pd.Timestamp(int(k[0]), unit="ms", tz="UTC")
                o, h, l, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
            elif isinstance(k, dict):
                ts  = pd.Timestamp(int(k.get("time", k.get("t", 0))), unit="ms", tz="UTC")
                o   = float(k.get("open",   k.get("o", 0)))
                h   = float(k.get("high",   k.get("h", 0)))
                l   = float(k.get("low",    k.get("l", 0)))
                c   = float(k.get("close",  k.get("c", 0)))
                v   = float(k.get("volume", k.get("v", 0)))
            else:
                continue
            rows.append({"time": ts, "open": o, "high": h,
                         "low": l, "close": c, "volume": v})
        except Exception as e:
            log.warning(f"Kline parse error: {e} | data={k}")
            continue

    if not rows:
        return None

    df = pd.DataFrame(rows).set_index("time").sort_index()
    df = df[~df.index.duplicated(keep="last")]

    # Eliminar la última vela si está incompleta (menos de 10 segundos de margen)
    # En producción confiamos en que ya cerró (el scheduler espera 10s)
    return df


def fetch_pair_data(client: BingXClient,
                    symbol: str) -> Tuple[Optional[pd.DataFrame],
                                          Optional[pd.DataFrame],
                                          float, float]:
    """
    Devuelve: df_3m, df_15m, funding_rate, ob_imbalance
    """
    df_3m  = fetch_ohlcv(client, symbol, C.TIMEFRAME,     C.LOOKBACK)
    df_15m = fetch_ohlcv(client, symbol, C.HTF_TIMEFRAME, 100)
    funding= client.get_funding_rate(symbol)
    ob_imb = client.orderbook_imbalance(symbol, 15)
    return df_3m, df_15m, funding, ob_imb


def validate_df(df: pd.DataFrame, min_rows: int = 100) -> bool:
    if df is None or len(df) < min_rows:
        return False
    if df["close"].isna().sum() > 5:
        return False
    return True
