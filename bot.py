"""
InstitutionalBot v6.0 — Multi-Asset Trading Bot para BingX
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MEJORAS v6.0 vs v5.0:
  • LONG + SHORT signals (el doble de oportunidades)
  • Trailing Stop NATIVO de BingX (priceRate) — exchange gestiona el trail
  • 150 símbolos priorizados por volumen real 24h
  • 12 indicadores: HTF trend, EMA stack, EMA200, RSI, MACD,
    ADX (fuerza tendencia), BOS, CHoCH, FVG, HVN, Donchian breakout, BB
  • Filtro volatilidad mínima (evita monedas planas)
  • Gestión riesgo diaria: pausa automática si pierde > MAX_DAILY_LOSS_PCT
  • SL+TP integrados en la orden de apertura (API oficial BingX)
  • Mega Runner: después de TP2, trail se amplía con TRAILING_STOP_MARKET nativo
  • Heartbeat cada 100 scans
  • XAUT-USDT (oro tokenizado Tether Gold, 24/7 por API)

NOTA: BingX Commodity Futures (GOLD-USDT nativo) NO soporta API trading
aún. Usamos XAUT-USDT como proxy del oro, que sí opera 24/7 por API.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import httpx
import numpy as np
import pandas as pd

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("BotV6")


# ─────────────────────────────────────────────
#  CONFIG — todas las variables en Railway
# ─────────────────────────────────────────────
class Config:
    # BingX
    API_KEY    = os.getenv("BINGX_API_KEY", "")
    API_SECRET = os.getenv("BINGX_API_SECRET", "")
    BASE_URL   = "https://open-api.bingx.com"

    # Telegram
    TG_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # Sizing
    LEVERAGE        = int(os.getenv("LEVERAGE", "10"))
    RISK_PCT        = float(os.getenv("RISK_PCT", "1.0"))
    MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "6"))

    # Scan
    SCAN_INTERVAL_SEC = int(os.getenv("SCAN_INTERVAL_SEC", "30"))
    MAX_SYMBOLS       = int(os.getenv("MAX_SYMBOLS", "150"))

    # Volume Profile
    VP_BINS           = int(os.getenv("VP_BINS", "20"))
    HVN_THRESHOLD_PCT = float(os.getenv("HVN_THRESHOLD_PCT", "4.0"))
    VP_LOOKBACK       = int(os.getenv("VP_LOOKBACK", "150"))

    # Señales
    MIN_SCORE_LONG  = float(os.getenv("MIN_SCORE_LONG", "3.0"))
    MIN_SCORE_SHORT = float(os.getenv("MIN_SCORE_SHORT", "3.0"))
    ENABLE_SHORTS   = os.getenv("ENABLE_SHORTS", "true").lower() == "true"

    # TP / SL
    SL_ATR_MULT  = float(os.getenv("SL_ATR_MULT", "1.5"))
    TP1_ATR_MULT = float(os.getenv("TP1_ATR_MULT", "2.0"))
    TP2_ATR_MULT = float(os.getenv("TP2_ATR_MULT", "4.0"))
    TP3_ATR_MULT = float(os.getenv("TP3_ATR_MULT", "7.0"))

    # Trailing Stop nativo BingX (% precio)
    TRAIL_PCT      = float(os.getenv("TRAIL_PCT", "1.5"))
    MEGA_TRAIL_PCT = float(os.getenv("MEGA_TRAIL_PCT", "3.0"))

    # Mega Runner
    RUNNER_ENABLED      = os.getenv("RUNNER_ENABLED", "true").lower() == "true"
    MEGA_RUNNER_TRIGGER = float(os.getenv("MEGA_RUNNER_TRIGGER", "2.0"))

    # Filtros calidad
    MIN_VOLUME_USDT = float(os.getenv("MIN_VOLUME_USDT", "500000"))
    MIN_ATR_PCT     = float(os.getenv("MIN_ATR_PCT", "0.5"))

    # Riesgo diario
    MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "5.0"))

    # Timeframes
    PRIMARY_TF = os.getenv("PRIMARY_TF", "15m")
    HTF        = os.getenv("HTF", "1h")

    # Prioridades (siempre incluidos, primeros en lista)
    PRIORITY_SYMBOLS = [
        "XAUT-USDT",  # Tether Gold — proxy oro 24/7 por API
        "BTC-USDT",
        "ETH-USDT",
        "SOL-USDT",
        "BNB-USDT",
        "XRP-USDT",
    ]


# ─────────────────────────────────────────────
#  BINGX HTTP CLIENT
# ─────────────────────────────────────────────
class BingXClient:
    def __init__(self):
        limits = httpx.Limits(max_connections=50, max_keepalive_connections=20)
        self._c = httpx.AsyncClient(timeout=15, limits=limits)

    def _sign(self, params: dict) -> str:
        qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return hmac.new(Config.API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()

    def _h(self) -> dict:
        return {"X-BX-APIKEY": Config.API_KEY}

    def _ts(self) -> int:
        return int(time.time() * 1000)

    async def _get(self, path: str, params: dict = None, signed: bool = False):
        params = params or {}
        if signed:
            params["timestamp"] = self._ts()
            params["signature"] = self._sign(params)
        r = await self._c.get(Config.BASE_URL + path, params=params, headers=self._h())
        r.raise_for_status()
        d = r.json()
        if d.get("code", 0) != 0:
            raise ValueError(f"BingX {d.get('code')}: {d.get('msg')}")
        return d

    async def _post(self, path: str, params: dict = None):
        params = params or {}
        params["timestamp"] = self._ts()
        params["signature"] = self._sign(params)
        r = await self._c.post(Config.BASE_URL + path, params=params, headers=self._h())
        r.raise_for_status()
        d = r.json()
        if d.get("code", 0) != 0:
            raise ValueError(f"BingX {d.get('code')}: {d.get('msg')}")
        return d

    # ─── Mercado ──────────────────────────────
    async def get_all_tickers(self) -> list[dict]:
        d = await self._get("/openApi/swap/v2/quote/ticker")
        return d.get("data", [])

    async def get_klines(self, symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
        d = await self._get("/openApi/swap/v3/quote/klines", {
            "symbol": symbol, "interval": interval, "limit": limit
        })
        rows = d.get("data", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["time","open","high","low","close","volume","_"])
        df = df[["time","open","high","low","close","volume"]].astype(float)
        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        return df.sort_values("time").reset_index(drop=True)

    # ─── Cuenta ───────────────────────────────
    async def get_balance(self) -> float:
        d = await self._get("/openApi/swap/v3/user/balance", signed=True)
        data = d.get("data", [])
        
        # Handle both response structures: dict with "balance" key or direct list
        if isinstance(data, dict):
            balance_list = data.get("balance", [])
        else:
            balance_list = data if isinstance(data, list) else []
        
        for item in balance_list:
            if item.get("asset") == "USDT":
                return float(item.get("availableMargin", 0))
        return 0.0

    async def get_positions(self) -> list[dict]:
        d = await self._get("/openApi/swap/v2/user/positions", signed=True)
        return [p for p in d.get("data", []) if float(p.get("positionAmt", 0)) != 0]

    # ─── Órdenes ──────────────────────────────
    async def set_leverage(self, symbol: str, lev: int, pos_side: str = "LONG"):
        try:
            await self._post("/openApi/swap/v2/trade/leverage", {
                "symbol": symbol, "side": pos_side, "leverage": lev
            })
        except Exception as e:
            log.debug(f"set_leverage: {e}")

    async def open_position(self, symbol: str, side: str, pos_side: str,
                            qty: float, sl: float, tp: float) -> dict:
        """
        Abre posición con SL y TP integrados (API oficial BingX v2).
        side: BUY (long) / SELL (short)
        """
        params = {
            "symbol"      : symbol,
            "side"        : side,
            "positionSide": pos_side,
            "type"        : "MARKET",
            "quantity"    : qty,
            "takeProfit"  : json.dumps({
                "type"       : "TAKE_PROFIT_MARKET",
                "stopPrice"  : round(tp, 8),
                "workingType": "MARK_PRICE",
            }),
            "stopLoss"    : json.dumps({
                "type"       : "STOP_MARKET",
                "stopPrice"  : round(sl, 8),
                "workingType": "MARK_PRICE",
            }),
        }
        d = await self._post("/openApi/swap/v2/trade/order", params)
        return d.get("data", {})

    async def close_partial(self, symbol: str, pos_side: str, qty: float) -> dict:
        """Cierre parcial a mercado."""
        side = "SELL" if pos_side == "LONG" else "BUY"
        params = {
            "symbol"      : symbol,
            "side"        : side,
            "positionSide": pos_side,
            "type"        : "MARKET",
            "quantity"    : qty,
            "reduceOnly"  : "true",
        }
        d = await self._post("/openApi/swap/v2/trade/order", params)
        return d.get("data", {})

    async def place_trailing_stop(self, symbol: str, pos_side: str,
                                  qty: float, activation_price: float,
                                  price_rate: float) -> dict:
        """
        Trailing Stop NATIVO BingX.
        priceRate = % trailing (ej: 1.5 = 1.5%)
        Se activa cuando precio toca activationPrice.
        """
        side = "SELL" if pos_side == "LONG" else "BUY"
        params = {
            "symbol"         : symbol,
            "side"           : side,
            "positionSide"   : pos_side,
            "type"           : "TRAILING_STOP_MARKET",
            "quantity"       : qty,
            "priceRate"      : price_rate,
            "activationPrice": round(activation_price, 8),
            "workingType"    : "MARK_PRICE",
        }
        d = await self._post("/openApi/swap/v2/trade/order", params)
        return d.get("data", {})

    async def cancel_all_orders(self, symbol: str):
        try:
            await self._post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})
        except Exception as e:
            log.debug(f"cancel_all: {e}")

    async def update_tp(self, symbol: str, pos_side: str, qty: float, new_tp: float):
        """Recoloca TP (cancela y crea nuevo TAKE_PROFIT_MARKET)."""
        await self.cancel_all_orders(symbol)
        side = "SELL" if pos_side == "LONG" else "BUY"
        try:
            await self._post("/openApi/swap/v2/trade/order", {
                "symbol"      : symbol,
                "side"        : side,
                "positionSide": pos_side,
                "type"        : "TAKE_PROFIT_MARKET",
                "quantity"    : qty,
                "stopPrice"   : round(new_tp, 8),
                "workingType" : "MARK_PRICE",
                "reduceOnly"  : "true",
            })
        except Exception as e:
            log.warning(f"update_tp {symbol}: {e}")


# ─────────────────────────────────────────────
#  INDICADORES
# ─────────────────────────────────────────────
def _atr(df: pd.DataFrame, p: int = 14) -> pd.Series:
    h, l, cp = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - cp).abs(), (l - cp).abs()], axis=1).max(axis=1)
    return tr.ewm(span=p, adjust=False).mean()

def _ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=p, adjust=False).mean()

def _rsi(s: pd.Series, p: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).ewm(span=p, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(span=p, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def _macd(s: pd.Series):
    line = _ema(s, 12) - _ema(s, 26)
    return line, _ema(line, 9)

def _bb(s: pd.Series, p: int = 20, std: float = 2.0):
    m = s.rolling(p).mean()
    d = s.rolling(p).std()
    return m - std * d, m, m + std * d

def _adx(df: pd.DataFrame, p: int = 14) -> float:
    h, l, cp = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - cp).abs(), (l - cp).abs()], axis=1).max(axis=1)
    dp = (h - h.shift(1)).clip(lower=0)
    dm = (l.shift(1) - l).clip(lower=0)
    dp[dp < dm] = 0
    dm[dm < dp] = 0
    atr_ = tr.ewm(span=p, adjust=False).mean()
    dip  = 100 * dp.ewm(span=p, adjust=False).mean() / atr_.replace(0, np.nan)
    dim  = 100 * dm.ewm(span=p, adjust=False).mean() / atr_.replace(0, np.nan)
    dx   = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    return float(dx.ewm(span=p, adjust=False).mean().iloc[-1])

def _donchian(df: pd.DataFrame, p: int = 20):
    hmax = df["high"].rolling(p).max().iloc[-2]
    lmin = df["low"].rolling(p).min().iloc[-2]
    c    = df["close"].iloc[-1]
    return c > hmax, c < lmin  # (long_bo, short_bo)

def _volume_profile(df: pd.DataFrame) -> dict:
    if len(df) < 10:
        return {"poc": 0.0, "hvn": [], "val": 0.0, "vah": 0.0}
    bins = Config.VP_BINS
    lo, hi = df["low"].min(), df["high"].max()
    if hi == lo:
        return {"poc": 0.0, "hvn": [], "val": 0.0, "vah": 0.0}
    edges   = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    vols    = np.zeros(bins)
    for _, row in df.iterrows():
        rl, rh, rv = row["low"], row["high"], row["volume"]
        for i in range(bins):
            ov = min(rh, edges[i+1]) - max(rl, edges[i])
            if ov > 0:
                frac = ov / (rh - rl) if rh != rl else 1.0
                vols[i] += rv * frac
    total = vols.sum()
    if total == 0:
        return {"poc": 0.0, "hvn": [], "val": 0.0, "vah": 0.0}
    pi  = int(np.argmax(vols))
    poc = centers[pi]
    thr = total * (Config.HVN_THRESHOLD_PCT / 100)
    hvn = [centers[i] for i in range(bins) if vols[i] >= thr]
    target = total * 0.70
    lo_i, hi_i, cov = pi, pi, vols[pi]
    while cov < target and (lo_i > 0 or hi_i < bins - 1):
        la = vols[lo_i - 1] if lo_i > 0 else 0
        ha = vols[hi_i + 1] if hi_i < bins - 1 else 0
        if la >= ha and lo_i > 0:
            lo_i -= 1; cov += la
        elif hi_i < bins - 1:
            hi_i += 1; cov += ha
        else:
            lo_i -= 1; cov += la
    return {"poc": poc, "hvn": hvn, "val": centers[lo_i], "vah": centers[hi_i]}

def _structure(df: pd.DataFrame):
    if len(df) < 20:
        return None, None
    hs = df["high"].rolling(5).max()
    ls = df["low"].rolling(5).min()
    c  = df["close"].iloc[-1]
    bos = "bullish" if c > hs.iloc[-2] else ("bearish" if c < ls.iloc[-2] else None)
    choch = None
    if len(df) >= 40:
        if bos == "bullish" and hs.iloc[-20] > hs.iloc[-2]:
            choch = "bullish_reversal"
        elif bos == "bearish" and ls.iloc[-20] < ls.iloc[-2]:
            choch = "bearish_reversal"
    return bos, choch

def _fvg(df: pd.DataFrame) -> Optional[dict]:
    for i in range(len(df) - 3, max(len(df) - 13, 1), -1):
        c1, c3 = df.iloc[i], df.iloc[i + 2]
        if c1["high"] < c3["low"]:
            return {"type": "bullish", "low": c1["high"], "high": c3["low"]}
        if c1["low"] > c3["high"]:
            return {"type": "bearish", "low": c3["high"], "high": c1["low"]}
    return None


# ─────────────────────────────────────────────
#  ANALIZADOR DE SEÑALES
# ─────────────────────────────────────────────
async def analyze(bx: BingXClient, symbol: str) -> Optional[dict]:
    try:
        df   = await bx.get_klines(symbol, Config.PRIMARY_TF, Config.VP_LOOKBACK)
        df1h = await bx.get_klines(symbol, Config.HTF, 100)
        if df.empty or df1h.empty or len(df) < 60:
            return None

        close = df["close"]
        price = float(close.iloc[-1])

        # Filtro volatilidad
        atr_v   = float(_atr(df).iloc[-1])
        atr_pct = (atr_v / price) * 100
        if atr_pct < Config.MIN_ATR_PCT:
            return None

        # Indicadores
        rsi_v    = float(_rsi(close).iloc[-1])
        ml, ms   = _macd(close)
        hist     = float((ml - ms).iloc[-1])
        blo, _, bhi = _bb(close)
        bmid     = float(_.iloc[-1])
        blo_v    = float(blo.iloc[-1])
        bhi_v    = float(bhi.iloc[-1])
        ema20    = float(_ema(close, 20).iloc[-1])
        ema50    = float(_ema(close, 50).iloc[-1])
        ema200   = float(_ema(close, 200).iloc[-1]) if len(close) >= 200 else ema50
        adx_v    = _adx(df)
        don_l, don_s = _donchian(df)

        htf_c   = df1h["close"]
        htf_e50 = float(_ema(htf_c, 50).iloc[-1])
        htf_rsi = float(_rsi(htf_c).iloc[-1])
        htf_bull = float(htf_c.iloc[-1]) > htf_e50

        vol_avg  = float(df["volume"].rolling(20).mean().iloc[-1])
        vol_last = float(df["volume"].iloc[-1])
        vol_sp   = vol_last > vol_avg * 1.5

        vp       = _volume_profile(df.tail(Config.VP_LOOKBACK))
        bos, choch = _structure(df)
        fvg_     = _fvg(df)

        # ── LONG score ───────────────────────────────────────────
        ls, lr = 0.0, []
        if htf_bull:                                     ls += 1.0; lr.append("HTF↑")
        if price > ema20 > ema50:                        ls += 1.0; lr.append("EMA↑")
        if price > ema200:                               ls += 0.5; lr.append("EMA200↑")
        if 40 < rsi_v < 65:                              ls += 0.5; lr.append(f"RSI{rsi_v:.0f}")
        if rsi_v < 35:                                   ls += 1.0; lr.append(f"RSI_OS{rsi_v:.0f}")
        if hist > 0:                                     ls += 1.0; lr.append("MACD+")
        if adx_v > 25:                                   ls += 0.5; lr.append(f"ADX{adx_v:.0f}")
        if bos == "bullish":                             ls += 1.0; lr.append("BOS↑")
        if choch == "bullish_reversal":                  ls += 0.5; lr.append("CHoCH↑")
        if fvg_ and fvg_["type"] == "bullish" and fvg_["low"] <= price <= fvg_["high"]:
                                                         ls += 1.0; lr.append("FVG↑")
        if vp["hvn"] and any(abs(price-h)/price < 0.006 for h in vp["hvn"]):
                                                         ls += 1.0; lr.append("HVN")
        if vol_sp:                                       ls += 0.5; lr.append("VolSpike")
        if don_l:                                        ls += 1.0; lr.append("Donchian↑")
        if vp["val"] < price < vp["vah"]:               ls += 0.5; lr.append("VA")
        if blo_v < price < bmid:                         ls += 0.5; lr.append("BB_long")
        if htf_rsi < 45:                                 ls += 0.5; lr.append(f"HTF_RSI{htf_rsi:.0f}")

        # ── SHORT score ──────────────────────────────────────────
        ss, sr = 0.0, []
        if not htf_bull:                                 ss += 1.0; sr.append("HTF↓")
        if price < ema20 < ema50:                        ss += 1.0; sr.append("EMA↓")
        if price < ema200:                               ss += 0.5; sr.append("EMA200↓")
        if 35 < rsi_v < 60:                              ss += 0.5; sr.append(f"RSI{rsi_v:.0f}")
        if rsi_v > 65:                                   ss += 1.0; sr.append(f"RSI_OB{rsi_v:.0f}")
        if hist < 0:                                     ss += 1.0; sr.append("MACD-")
        if adx_v > 25:                                   ss += 0.5; sr.append(f"ADX{adx_v:.0f}")
        if bos == "bearish":                             ss += 1.0; sr.append("BOS↓")
        if choch == "bearish_reversal":                  ss += 0.5; sr.append("CHoCH↓")
        if fvg_ and fvg_["type"] == "bearish" and fvg_["low"] <= price <= fvg_["high"]:
                                                         ss += 1.0; sr.append("FVG↓")
        if vp["hvn"] and any(abs(price-h)/price < 0.006 for h in vp["hvn"]):
                                                         ss += 1.0; sr.append("HVN_R")
        if vol_sp:                                       ss += 0.5; sr.append("VolSpike")
        if don_s:                                        ss += 1.0; sr.append("Donchian↓")
        if bmid < price < bhi_v:                         ss += 0.5; sr.append("BB_short")
        if htf_rsi > 55:                                 ss += 0.5; sr.append(f"HTF_RSI{htf_rsi:.0f}")

        # ── Dirección ────────────────────────────────────────────
        direction, score, reasons = None, 0.0, []
        if ls >= Config.MIN_SCORE_LONG and ls >= ss:
            direction, score, reasons = "LONG", ls, lr
        elif Config.ENABLE_SHORTS and ss >= Config.MIN_SCORE_SHORT:
            direction, score, reasons = "SHORT", ss, sr
        if direction is None:
            return None

        # ── SL / TP ──────────────────────────────────────────────
        if direction == "LONG":
            sl  = price - Config.SL_ATR_MULT  * atr_v
            tp1 = price + Config.TP1_ATR_MULT * atr_v
            tp2 = price + Config.TP2_ATR_MULT * atr_v
            tp3 = price + Config.TP3_ATR_MULT * atr_v
        else:
            sl  = price + Config.SL_ATR_MULT  * atr_v
            tp1 = price - Config.TP1_ATR_MULT * atr_v
            tp2 = price - Config.TP2_ATR_MULT * atr_v
            tp3 = price - Config.TP3_ATR_MULT * atr_v

        return {
            "symbol": symbol, "direction": direction, "price": price,
            "atr": atr_v, "atr_pct": atr_pct,
            "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "score": score, "reasons": reasons,
            "rsi": rsi_v, "adx": adx_v,
            "poc": vp["poc"], "hvn": vp["hvn"],
            "val": vp["val"], "vah": vp["vah"],
        }
    except Exception as e:
        log.debug(f"analyze {symbol}: {e}")
        return None


# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
class Telegram:
    def __init__(self):
        self._c = httpx.AsyncClient(timeout=10)

    async def send(self, text: str):
        if not Config.TG_TOKEN or not Config.TG_CHAT_ID:
            return
        try:
            await self._c.post(
                f"https://api.telegram.org/bot{Config.TG_TOKEN}/sendMessage",
                json={"chat_id": Config.TG_CHAT_ID, "text": text[:4000], "parse_mode": "HTML"},
            )
        except Exception as e:
            log.error(f"Telegram: {e}")


# ─────────────────────────────────────────────
#  BOT PRINCIPAL
# ─────────────────────────────────────────────
class InstitutionalBot:
    def __init__(self):
        self.bx     = BingXClient()
        self.tg     = Telegram()
        self.symbols: list[str] = []
        self.trades : dict[str, dict] = {}
        self.stats  = defaultdict(int)
        # Riesgo diario
        self.day_start_bal: float = 0.0
        self.day_date: str = ""
        self.paused: bool = False

    # ─── Control riesgo ───────────────────────
    async def _daily_risk_ok(self) -> bool:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.day_date:
            bal = await self.bx.get_balance()
            self.day_start_bal = bal
            self.day_date      = today
            self.paused        = False
            log.info(f"[DIA NUEVO] Balance: ${bal:.2f}")
        if self.day_start_bal <= 0:
            return True
        bal     = await self.bx.get_balance()
        loss_p  = (self.day_start_bal - bal) / self.day_start_bal * 100
        if loss_p >= Config.MAX_DAILY_LOSS_PCT and not self.paused:
            self.paused = True
            await self.tg.send(
                f"⛔ <b>PAUSA DIARIA ACTIVADA</b>\n"
                f"Pérdida: {loss_p:.1f}% ≥ límite {Config.MAX_DAILY_LOSS_PCT}%\n"
                f"Reanuda mañana UTC."
            )
        return not self.paused

    # ─── Símbolos ─────────────────────────────
    async def _refresh_symbols(self):
        try:
            tickers = await self.bx.get_all_tickers()
            # Filtrar por volumen mínimo y ordenar desc
            valid = []
            for t in tickers:
                sym = t.get("symbol", "")
                if not sym.endswith("-USDT"):
                    continue
                try:
                    vol = float(t.get("quoteVolume") or t.get("volume") or 0)
                except Exception:
                    vol = 0
                if vol >= Config.MIN_VOLUME_USDT:
                    valid.append((sym, vol))
            valid.sort(key=lambda x: x[1], reverse=True)

            # Prioridades primero
            selected = list(Config.PRIORITY_SYMBOLS)
            for sym, _ in valid:
                if sym not in selected:
                    selected.append(sym)
                if len(selected) >= Config.MAX_SYMBOLS:
                    break

            self.symbols = selected
            top5 = [s for s, _ in valid[:5]]
            log.info(f"Símbolos: {len(self.symbols)} | Top5 vol: {top5}")
        except Exception as e:
            log.error(f"_refresh_symbols: {e}")

    # ─── Abrir posición ───────────────────────
    async def open_position(self, sig: dict):
        symbol = sig["symbol"]
        if symbol in self.trades or len(self.trades) >= Config.MAX_OPEN_TRADES:
            return
        try:
            bal       = await self.bx.get_balance()
            risk_usdt = bal * (Config.RISK_PCT / 100)
            sl_dist   = abs(sig["price"] - sig["sl"])
            if sl_dist <= 0:
                return
            qty = round((risk_usdt * Config.LEVERAGE) / sl_dist, 3)
            if qty <= 0:
                return

            pos_side = "LONG" if sig["direction"] == "LONG" else "SHORT"
            side     = "BUY"  if sig["direction"] == "LONG" else "SELL"

            await self.bx.set_leverage(symbol, Config.LEVERAGE, pos_side)
            order = await self.bx.open_position(
                symbol, side, pos_side, qty, sig["sl"], sig["tp1"]
            )
            if not order:
                return

            self.trades[symbol] = {
                "direction"    : sig["direction"],
                "pos_side"     : pos_side,
                "entry"        : sig["price"],
                "sl"           : sig["sl"],
                "tp1"          : sig["tp1"],
                "tp2"          : sig["tp2"],
                "tp3"          : sig["tp3"],
                "atr"          : sig["atr"],
                "qty"          : qty,
                "qty_left"     : qty,
                "tp1_hit"      : False,
                "tp2_hit"      : False,
                "mega"         : False,
                "trail_active" : False,
                "opened_at"    : time.time(),
            }
            self.stats["opened"] += 1

            hvn_str = ", ".join(f"${h:.6g}" for h in sig["hvn"][:3]) or "—"
            emoji   = "🟢" if sig["direction"] == "LONG" else "🔴"
            arrow   = "↑ LONG" if sig["direction"] == "LONG" else "↓ SHORT"

            await self.tg.send(
                f"{emoji} <b>{arrow} OPENED v6.0</b>\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"📌 <b>{symbol}</b>\n"
                f"💰 Entry : <b>${sig['price']:.6g}</b>\n"
                f"🛑 SL    : ${sig['sl']:.6g}\n"
                f"🎯 TP1   : ${sig['tp1']:.6g}\n"
                f"🎯 TP2   : ${sig['tp2']:.6g}\n"
                f"🎯 TP3   : ${sig['tp3']:.6g}\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"📊 POC: ${sig['poc']:.6g} | VAL: ${sig['val']:.6g} | VAH: ${sig['vah']:.6g}\n"
                f"🔷 HVN: {hvn_str}\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"⚡ Score: {sig['score']:.1f} | RSI: {sig['rsi']:.0f} | ADX: {sig['adx']:.0f}\n"
                f"✅ {' | '.join(sig['reasons'])}\n"
                f"📦 Qty: {qty} | Lev: {Config.LEVERAGE}x | ATR%: {sig['atr_pct']:.2f}%\n"
                f"💼 Risk: ${risk_usdt:.2f}"
            )
            log.info(f"OPENED {sig['direction']} {symbol} @ {sig['price']:.6g} score={sig['score']:.1f}")

        except Exception as e:
            log.error(f"open_position {symbol}: {e}")

    # ─── Monitorear trades ────────────────────
    async def monitor(self):
        if not self.trades:
            return
        try:
            positions = await self.bx.get_positions()
        except Exception as e:
            log.warning(f"get_positions: {e}")
            return

        live = {p["symbol"] for p in positions}

        for symbol, t in list(self.trades.items()):
            # Cerrada por SL/TP nativo o manualmente
            if symbol not in live:
                self.stats["closed"] += 1
                await self.tg.send(f"📤 <b>Posición cerrada</b>: {symbol}\n(SL/TP nativo o manual)")
                del self.trades[symbol]
                continue

            # Precio actual desde position
            try:
                pos   = next(p for p in positions if p["symbol"] == symbol)
                price = float(pos.get("markPrice") or pos.get("avgPrice") or 0)
            except Exception:
                continue
            if price == 0:
                continue

            is_long  = t["direction"] == "LONG"
            pnl_pct  = ((price - t["entry"]) / t["entry"]) * 100 * (1 if is_long else -1)
            qty_left = t["qty_left"]

            # ── TP1 ─────────────────────────────────
            if not t["tp1_hit"]:
                hit = (is_long and price >= t["tp1"]) or (not is_long and price <= t["tp1"])
                if hit:
                    t["tp1_hit"] = True
                    close_q = round(qty_left * 0.33, 3)
                    t["qty_left"] = round(qty_left - close_q, 3)
                    try:
                        await self.bx.close_partial(symbol, t["pos_side"], close_q)
                        # Recoloca TP en TP2 y mantiene SL en breakeven
                        await self.bx.cancel_all_orders(symbol)
                        side = "SELL" if is_long else "BUY"
                        await self.bx._post("/openApi/swap/v2/trade/order", {
                            "symbol": symbol, "side": side, "positionSide": t["pos_side"],
                            "type": "STOP_MARKET", "quantity": t["qty_left"],
                            "stopPrice": round(t["entry"], 8),
                            "workingType": "MARK_PRICE", "reduceOnly": "true",
                        })
                        await self.bx._post("/openApi/swap/v2/trade/order", {
                            "symbol": symbol, "side": side, "positionSide": t["pos_side"],
                            "type": "TAKE_PROFIT_MARKET", "quantity": t["qty_left"],
                            "stopPrice": round(t["tp2"], 8),
                            "workingType": "MARK_PRICE", "reduceOnly": "true",
                        })
                    except Exception as e:
                        log.warning(f"TP1 reorder {symbol}: {e}")
                    await self.tg.send(
                        f"🎯 <b>TP1 HIT</b> {symbol}\n"
                        f"Price: ${price:.6g} | PnL: +{pnl_pct:.1f}%\n"
                        f"✂️ 33% cerrado | SL → breakeven\n"
                        f"🎯 Siguiente: ${t['tp2']:.6g}"
                    )
                    continue

            # ── TP2 ─────────────────────────────────
            if t["tp1_hit"] and not t["tp2_hit"]:
                hit = (is_long and price >= t["tp2"]) or (not is_long and price <= t["tp2"])
                if hit:
                    t["tp2_hit"] = True
                    close_q = round(qty_left * 0.50, 3)
                    t["qty_left"] = round(qty_left - close_q, 3)
                    try:
                        await self.bx.close_partial(symbol, t["pos_side"], close_q)
                        await self.bx.cancel_all_orders(symbol)
                        # Trailing Stop NATIVO activado
                        await self.bx.place_trailing_stop(
                            symbol, t["pos_side"], t["qty_left"],
                            activation_price=price,
                            price_rate=Config.TRAIL_PCT,
                        )
                        t["trail_active"] = True
                    except Exception as e:
                        log.warning(f"TP2 trail {symbol}: {e}")
                    await self.tg.send(
                        f"🎯 <b>TP2 HIT</b> {symbol}\n"
                        f"Price: ${price:.6g} | PnL: +{pnl_pct:.1f}%\n"
                        f"✂️ Otro 33% cerrado\n"
                        f"🔄 Trailing Stop NATIVO {Config.TRAIL_PCT}% activado\n"
                        f"🎯 TP3: ${t['tp3']:.6g}"
                    )
                    continue

            # ── Mega Runner ──────────────────────────
            if t["tp2_hit"] and Config.RUNNER_ENABLED and not t["mega"]:
                tp2_dist = abs(t["tp2"] - t["entry"])
                mega_trigger = (t["tp2"] + tp2_dist * Config.MEGA_RUNNER_TRIGGER if is_long
                                else t["tp2"] - tp2_dist * Config.MEGA_RUNNER_TRIGGER)
                hit = (is_long and price >= mega_trigger) or (not is_long and price <= mega_trigger)
                if hit:
                    t["mega"] = True
                    try:
                        await self.bx.cancel_all_orders(symbol)
                        await self.bx.place_trailing_stop(
                            symbol, t["pos_side"], t["qty_left"],
                            activation_price=price,
                            price_rate=Config.MEGA_TRAIL_PCT,
                        )
                    except Exception as e:
                        log.warning(f"Mega Runner {symbol}: {e}")
                    await self.tg.send(
                        f"🚀 <b>MEGA RUNNER!</b> {symbol}\n"
                        f"Price: ${price:.6g} | PnL: +{pnl_pct:.1f}%\n"
                        f"🔥 Trail ampliado a {Config.MEGA_TRAIL_PCT}% — ¡dejamos correr!"
                    )

    # ─── Scan señales ─────────────────────────
    async def scan(self):
        slots = Config.MAX_OPEN_TRADES - len(self.trades)
        if slots <= 0:
            return

        sem = asyncio.Semaphore(15)

        async def safe(sym):
            async with sem:
                return await analyze(self.bx, sym)

        tasks   = [safe(s) for s in self.symbols if s not in self.trades]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        signals = [r for r in results if isinstance(r, dict)]

        if not signals:
            log.info("Sin señales")
            return

        signals.sort(key=lambda x: x["score"], reverse=True)
        top = signals[0]
        log.info(f"Señales: {len(signals)} | Top: {top['symbol']} {top['direction']} "
                 f"score={top['score']:.1f} razones={top['reasons']}")

        for sig in signals[:slots]:
            await self.open_position(sig)
            await asyncio.sleep(0.3)

    # ─── Loop principal ───────────────────────
    async def run(self):
        log.info("=" * 56)
        log.info("  InstitutionalBot v6.0 INICIADO")
        log.info(f"  Lev:{Config.LEVERAGE}x Risk:{Config.RISK_PCT}% MaxTrades:{Config.MAX_OPEN_TRADES}")
        log.info(f"  Shorts:{'ON' if Config.ENABLE_SHORTS else 'OFF'} "
                 f"Scan:{Config.SCAN_INTERVAL_SEC}s Syms:{Config.MAX_SYMBOLS}")
        log.info(f"  Trail:{Config.TRAIL_PCT}% Mega:{Config.MEGA_TRAIL_PCT}% "
                 f"Runner:{'ON' if Config.RUNNER_ENABLED else 'OFF'}")
        log.info("=" * 56)

        await self.tg.send(
            "🤖 <b>InstitutionalBot v6.0 ONLINE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚙️  Lev: {Config.LEVERAGE}x | Risk: {Config.RISK_PCT}%/trade\n"
            f"📉 SHORTS: {'✅' if Config.ENABLE_SHORTS else '❌'}\n"
            f"🔄 Trailing NATIVO: TP1={Config.TRAIL_PCT}% | Mega={Config.MEGA_TRAIL_PCT}%\n"
            f"🚀 Mega Runner: {'✅' if Config.RUNNER_ENABLED else '❌'}\n"
            f"📊 VP: {Config.VP_BINS} bins | ADX+Donchian activo\n"
            f"🔍 {Config.MAX_SYMBOLS} síms c/{Config.SCAN_INTERVAL_SEC}s | "
            f"Vol mín: ${Config.MIN_VOLUME_USDT/1e6:.1f}M\n"
            f"⛔ Pausa diaria si pérdida ≥ {Config.MAX_DAILY_LOSS_PCT}%\n"
            f"🥇 XAUT-USDT (oro 24/7) siempre incluido"
        )

        await self._refresh_symbols()
        scan_n = 0

        while True:
            try:
                scan_n += 1
                if scan_n % 30 == 0:
                    await self._refresh_symbols()

                if not await self._daily_risk_ok():
                    await asyncio.sleep(300)
                    continue

                log.info(f"── Scan #{scan_n} trades={len(self.trades)}/{Config.MAX_OPEN_TRADES} ──")
                await self.monitor()
                await self.scan()

                # Heartbeat cada 100 scans
                if scan_n % 100 == 0:
                    bal = await self.bx.get_balance()
                    await self.tg.send(
                        f"💓 <b>Heartbeat #{scan_n}</b>\n"
                        f"Balance: ${bal:.2f} | Trades: {len(self.trades)}\n"
                        f"Abiertos total: {self.stats['opened']} | Cerrados: {self.stats['closed']}"
                    )

                await asyncio.sleep(Config.SCAN_INTERVAL_SEC)

            except Exception as e:
                log.error(f"Loop error: {e}", exc_info=True)
                await asyncio.sleep(30)
