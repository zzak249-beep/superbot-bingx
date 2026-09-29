"""
Descarga de velas 5m de endpoints PÚBLICOS (sin API key) con caché en disco.

  binance → fapi.binance.com  (perpetuos USDT-M, historial largo, el más fiable)
  bingx   → open-api.bingx.com (para monedas que solo cotizan en BingX)

La caché (data/<fuente>_<SIMBOLO>_5m.csv) se amplía de forma incremental: la segunda
ejecución solo baja lo nuevo.
"""
from __future__ import annotations

import os
import time

import pandas as pd
import requests

TF_MS = 5 * 60_000
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "p12-study/1.0"


def norm_symbol(sym: str, source: str) -> str:
    s = sym.upper().replace("-", "").replace(".P", "").replace("_", "")
    if not s.endswith("USDT"):
        s += "USDT"
    return s if source == "binance" else s[:-4] + "-USDT"


def _get(url, params, tries=5):
    for i in range(tries):
        try:
            r = SESSION.get(url, params=params, timeout=20)
            if r.status_code == 451 or r.status_code == 403:
                raise RuntimeError("Binance bloquea esta IP por ubicación (EE. UU.). Ejecuta en local desde "
                                   "España, pon la región de Railway en Europa, o usa SOURCE=bingx")
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** i)
                continue
            return r
        except requests.RequestException:
            time.sleep(2 ** i)
    raise RuntimeError(f"sin respuesta de {url}")


def _binance(sym, start_ms, end_ms):
    rows, cur = [], start_ms
    while cur < end_ms:
        r = _get("https://fapi.binance.com/fapi/v1/klines",
                 dict(symbol=sym, interval="5m", startTime=cur, endTime=end_ms, limit=1500))
        if r.status_code == 400:
            raise ValueError(f"{sym} no existe en Binance Futures ({r.text[:120]})")
        data = r.json()
        if not data:
            break
        rows += [[d[0], float(d[1]), float(d[2]), float(d[3]), float(d[4]), float(d[5])] for d in data]
        nxt = data[-1][0] + TF_MS
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(0.12)
    return rows


def _bingx(sym, start_ms, end_ms):
    rows, cur, step = [], start_ms, 1000 * TF_MS
    while cur < end_ms:
        r = _get("https://open-api.bingx.com/openApi/swap/v3/quote/klines",
                 dict(symbol=sym, interval="5m", startTime=cur, endTime=min(cur + step, end_ms), limit=1000))
        js = r.json()
        if js.get("code", 0) != 0:
            raise ValueError(f"{sym} error BingX: {js.get('msg')}")
        data = js.get("data") or []
        for d in data:
            rows.append([int(d["time"]), float(d["open"]), float(d["high"]), float(d["low"]),
                         float(d["close"]), float(d["volume"])])
        cur += step
        time.sleep(0.15)
    return rows


def load(symbol: str, days: int, source: str = "binance", cache_dir: str = "data") -> pd.DataFrame:
    os.makedirs(cache_dir, exist_ok=True)
    sym = norm_symbol(symbol, source)
    path = os.path.join(cache_dir, f"{source}_{sym}_5m.csv")
    end_ms = (int(time.time() * 1000) // TF_MS) * TF_MS
    start_ms = end_ms - days * 86_400_000

    cached = None
    if os.path.exists(path):
        cached = pd.read_csv(path)
        have_from, have_to = int(cached["ts"].min()), int(cached["ts"].max())
    fetch = []
    if cached is None:
        fetch.append((start_ms, end_ms))
    else:
        if start_ms < have_from - TF_MS:
            fetch.append((start_ms, have_from))
        if have_to + TF_MS < end_ms:
            fetch.append((have_to + TF_MS, end_ms))

    fn = _binance if source == "binance" else _bingx
    new = []
    for a, b in fetch:
        new += fn(sym, a, b)
    df = pd.DataFrame(new, columns=["ts", "open", "high", "low", "close", "volume"])
    if cached is not None:
        df = pd.concat([cached, df], ignore_index=True)
    if df.empty:
        raise ValueError(f"{sym}: sin datos")
    df = df.drop_duplicates("ts").sort_values("ts")
    df.to_csv(path, index=False)
    df = df[df["ts"] >= start_ms]
    out = df.set_index(pd.to_datetime(df["ts"], unit="ms", utc=True))[["open", "high", "low", "close", "volume"]]
    return out
