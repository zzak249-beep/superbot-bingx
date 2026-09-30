"""
Velas DIARIAS y funding de fuentes públicas (sin API key), con caché en disco.

  binance → fapi.binance.com (bloquea IPs de EE. UU.)
  vision  → data.binance.vision (ficheros públicos; sin bloqueo por región; 1 día de retraso)
  bingx   → open-api.bingx.com (lo que usa el bot en vivo: es donde opera)
  auto    → binance y, si bloquea, vision
"""
from __future__ import annotations

import io
import os
import time
import zipfile
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

DAY_MS = 86_400_000
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "trend-bot/1.0"
VISION = "https://data.binance.vision/data/futures/um"


class RegionBlocked(RuntimeError):
    pass


def base(sym: str) -> str:
    s = sym.upper().replace("-", "").replace(".P", "").replace("_", "").replace("/", "")
    return s[:-4] if s.endswith("USDT") else s


def binance_sym(sym):
    return base(sym) + "USDT"


def bingx_sym(sym):
    return base(sym) + "-USDT"


def _get(url, params=None, tries=5, ok404=False):
    for i in range(tries):
        try:
            r = SESSION.get(url, params=params, timeout=30)
            if r.status_code in (451, 403) and "fapi.binance.com" in url:
                raise RegionBlocked("Binance bloquea esta IP por ubicación")
            if r.status_code == 404 and ok404:
                return r
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** i)
                continue
            return r
        except requests.RequestException:
            time.sleep(2 ** i)
    raise RuntimeError(f"sin respuesta de {url}")


def _frame(rows):
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "quote_vol"])
    df = df.drop_duplicates("ts").sort_values("ts")
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.normalize()
    df = df[~df.index.duplicated(keep="last")]
    return df[["open", "high", "low", "close", "volume", "quote_vol"]].astype(float)


# ───────────────────────── velas diarias ─────────────────────────
def _binance_daily(sym, start_ms, end_ms):
    rows, cur = [], start_ms
    while cur < end_ms:
        r = _get("https://fapi.binance.com/fapi/v1/klines",
                 dict(symbol=sym, interval="1d", startTime=cur, endTime=end_ms, limit=1500))
        if r.status_code == 400:
            raise ValueError(f"{sym} no existe en Binance Futures")
        data = r.json()
        if not data:
            break
        rows += [[d[0], float(d[1]), float(d[2]), float(d[3]), float(d[4]), float(d[5]), float(d[7])] for d in data]
        nxt = data[-1][0] + DAY_MS
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(0.1)
    return rows


def _parse_kline_zip(content):
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        raw = pd.read_csv(z.open(z.namelist()[0]), header=None, usecols=[0, 1, 2, 3, 4, 5, 7], dtype=str)
    raw = raw[pd.to_numeric(raw[0], errors="coerce").notna()].astype(float)
    ts = raw[0].astype("int64")
    ts = ts.where(ts < 10**14, ts // 1000)
    return [[int(a), b, c, d, e, f, g] for a, b, c, d, e, f, g in
            zip(ts, raw[1], raw[2], raw[3], raw[4], raw[5], raw[7])]


def _months(start_ms, end_ms):
    s = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    e = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
    m = datetime(s.year, s.month, 1, tzinfo=timezone.utc)
    while m <= e:
        yield m
        m = datetime(m.year + (m.month == 12), m.month % 12 + 1, 1, tzinfo=timezone.utc)


def _vision_daily(sym, start_ms, end_ms):
    rows, found = [], False
    now = datetime.now(timezone.utc)
    for m in _months(start_ms, end_ms):
        r = _get(f"{VISION}/monthly/klines/{sym}/1d/{sym}-1d-{m:%Y-%m}.zip", ok404=True)
        if r.status_code == 200:
            rows += _parse_kline_zip(r.content)
            found = True
        elif m.year == now.year and m.month == now.month:
            d = m
            while d.date() < now.date():
                rd = _get(f"{VISION}/daily/klines/{sym}/1d/{sym}-1d-{d:%Y-%m-%d}.zip", ok404=True)
                if rd.status_code == 200:
                    rows += _parse_kline_zip(rd.content)
                    found = True
                d += timedelta(days=1)
        time.sleep(0.03)
    if not found:
        raise ValueError(f"{sym} no está en data.binance.vision")
    return rows


def _bingx_daily(sym, start_ms, end_ms):
    """Pagina hacia atrás desde end_ms hasta start_ms o hasta que no haya más historial."""
    rows, end = [], end_ms
    for _ in range(40):
        r = _get("https://open-api.bingx.com/openApi/swap/v3/quote/klines",
                 dict(symbol=sym, interval="1d", startTime=max(start_ms, end - 1000 * DAY_MS), endTime=end, limit=1000))
        js = r.json()
        if js.get("code", 0) != 0:
            raise ValueError(f"{sym} error BingX: {js.get('msg')}")
        data = js.get("data") or []
        if not data:
            break
        batch = [[int(d["time"]), float(d["open"]), float(d["high"]), float(d["low"]), float(d["close"]),
                  float(d["volume"]), float(d.get("quoteVolume", 0) or 0) or float(d["volume"]) * float(d["close"])]
                 for d in data]
        rows += batch
        first = min(b[0] for b in batch)
        if first <= start_ms or first >= end:
            break
        end = first - 1
        time.sleep(0.15)
    return rows


FETCH = {"binance": _binance_daily, "vision": _vision_daily, "bingx": _bingx_daily}
_blocked = False


def load_daily(symbol: str, days: int, source: str = "auto", cache_dir: str = "data",
               drop_today: bool = True) -> tuple[pd.DataFrame, str]:
    """Velas diarias COMPLETAS (sin la del día en curso). Devuelve (df, fuente)."""
    global _blocked
    os.makedirs(cache_dir, exist_ok=True)
    now_ms = int(time.time() * 1000)
    end_ms = now_ms // DAY_MS * DAY_MS          # 00:00 UTC de hoy
    start_ms = end_ms - days * DAY_MS
    order = {"auto": (["vision"] if _blocked else ["binance", "vision"])}.get(source, [source])
    err = None
    for src in order:
        try:
            sym = bingx_sym(symbol) if src == "bingx" else binance_sym(symbol)
            path = os.path.join(cache_dir, f"{src}_{sym}_1d.csv")
            rows = []
            if os.path.exists(path) and src != "bingx":
                cached = pd.read_csv(path)
                rows = cached.values.tolist()
                have_to = int(cached["ts"].max()) if len(cached) else start_ms
                have_from = int(cached["ts"].min()) if len(cached) else end_ms
                if have_from > start_ms + DAY_MS:
                    rows += FETCH[src](sym, start_ms, have_from)
                if have_to + DAY_MS < end_ms:
                    rows += FETCH[src](sym, have_to + DAY_MS, end_ms)
            else:
                rows = FETCH[src](sym, start_ms, end_ms)
            if not rows:
                raise ValueError(f"{sym}: sin datos")
            raw = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "quote_vol"])
            raw.drop_duplicates("ts").sort_values("ts").to_csv(path, index=False)
            df = _frame(rows)
            if drop_today:
                df = df[df.index < pd.Timestamp(end_ms, unit="ms", tz="UTC")]
            return df[df.index >= pd.Timestamp(start_ms, unit="ms", tz="UTC")], src
        except RegionBlocked as e:
            _blocked, err = True, e
    raise RuntimeError(f"{err}. Pon la región en Europa o usa SOURCE=vision")


# ───────────────────────── funding histórico ─────────────────────────
def load_funding(symbol: str, days: int, source: str = "auto") -> pd.Series | None:
    """Suma diaria del funding (fracción). None si no se consigue: el backtest usa entonces
    un valor constante conservador."""
    sym = binance_sym(symbol)
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * DAY_MS
    rows = []
    try:
        if source in ("auto", "binance") and not _blocked:
            cur = start_ms
            while cur < now_ms:
                r = _get("https://fapi.binance.com/fapi/v1/fundingRate",
                         dict(symbol=sym, startTime=cur, endTime=now_ms, limit=1000))
                data = r.json() if r.status_code == 200 else []
                if not data:
                    break
                rows += [(int(d["fundingTime"]), float(d["fundingRate"])) for d in data]
                nxt = data[-1]["fundingTime"] + 1
                if nxt <= cur:
                    break
                cur = nxt
                time.sleep(0.1)
    except RegionBlocked:
        rows = []
    if not rows:
        for m in _months(start_ms, now_ms):
            try:
                r = _get(f"{VISION}/monthly/fundingRate/{sym}/{sym}-fundingRate-{m:%Y-%m}.zip", ok404=True)
            except RuntimeError:
                continue
            if r.status_code != 200:
                continue
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                raw = pd.read_csv(z.open(z.namelist()[0]), header=None, dtype=str)
            raw = raw[pd.to_numeric(raw[0], errors="coerce").notna()]
            rate_col = raw.columns[-1]
            rows += [(int(float(t)), float(v)) for t, v in zip(raw[0], raw[rate_col])]
    if not rows:
        return None
    s = pd.Series({pd.Timestamp(t, unit="ms", tz="UTC"): v for t, v in rows}).sort_index()
    return s.groupby(s.index.normalize()).sum()
