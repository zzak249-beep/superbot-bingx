"""
main.py — Bot BingX completo en un solo archivo.
Sin subcarpetas. Sube solo este archivo + config.py + requirements.txt.
"""

import asyncio
import gzip
import hashlib
import hmac as _hmac
import json
import logging
import os
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import urlencode

import aiohttp
import numpy as np
import websockets
from dotenv import load_dotenv
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

load_dotenv()

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════

def _list(key: str, default: str) -> List[str]:
    return [s.strip() for s in os.getenv(key, default).split(",") if s.strip()]

API_KEY    = os.getenv("BINGX_API_KEY", "")
API_SECRET = os.getenv("BINGX_API_SECRET", "")
BASE_URL   = "https://open-api.bingx.com"
WS_URL     = "wss://open-api.bingx.com/swap-market"
TG_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SYMBOLS    = _list("SYMBOLS", "BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT,XRP-USDT")
LEVERAGE   = int(os.getenv("LEVERAGE", "10"))
BASE_SIZE_USDT  = float(os.getenv("BASE_SIZE_USDT", "10"))
MAX_POSITIONS   = int(os.getenv("MAX_POSITIONS", "3"))
MAX_RISK_PCT    = float(os.getenv("MAX_RISK_PCT", "0.015"))
MIN_TRADE_USDT  = float(os.getenv("MIN_TRADE_USDT", "5.0"))
MAX_TRADE_USDT  = float(os.getenv("MAX_TRADE_USDT", "100.0"))
HTF = os.getenv("HTF", "1h")
MTF = os.getenv("MTF", "15m")
LTF = os.getenv("LTF", "5m")
EMA_SLOW       = int(os.getenv("EMA_SLOW", "50"))
EMA_FAST       = int(os.getenv("EMA_FAST", "20"))
SWING_LOOKBACK = int(os.getenv("SWING_LOOKBACK", "7"))
VOL_SMA_LEN    = int(os.getenv("VOL_SMA_LEN", "20"))
ATR_LEN        = int(os.getenv("ATR_LEN", "14"))
ATR_SL_MULT    = float(os.getenv("ATR_SL_MULT", "1.5"))
ATR_TP_MULT    = float(os.getenv("ATR_TP_MULT", "2.5"))
POSITION_MODE  = os.getenv("POSITION_MODE", "ONE_WAY")
PAPER          = os.getenv("PAPER", "true").lower() == "true"
SCAN_INTERVAL  = float(os.getenv("SCAN_INTERVAL", "2.0"))

TRAIL_STEP_ATR    = float(os.getenv("TRAIL_STEP_ATR", "0.5"))
TP1_FRACTION      = float(os.getenv("TP1_FRACTION", "0.5"))
TP1_ATR_MULT      = float(os.getenv("TP1_ATR_MULT", "1.5"))
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.03"))

MIN_QUOTE_VOLUME   = float(os.getenv("MIN_QUOTE_VOLUME", os.getenv("MIN_VOLUME_USDT", "300000")))
TOP_N_SYMBOLS      = int(os.getenv("TOP_N_SYMBOLS", "30"))
SYMBOL_REFRESH_INTERVAL = int(os.getenv("SYMBOL_REFRESH_INTERVAL", "3600"))

BLACKLIST = {"USDC-USDT","BUSD-USDT","TUSD-USDT","DAI-USDT","USDP-USDT","FDUSD-USDT"}

def validate_config():
    missing = []
    if not API_KEY:    missing.append("BINGX_API_KEY")
    if not API_SECRET: missing.append("BINGX_API_SECRET")
    if not TG_TOKEN:   missing.append("TELEGRAM_TOKEN")
    if not TG_CHAT_ID: missing.append("TELEGRAM_CHAT_ID")
    if missing:
        raise EnvironmentError(f"Variables requeridas no definidas: {missing}")

# ══════════════════════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bot")

# ══════════════════════════════════════════════════════════════════════════════
#  INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

def _arr(x):
    return np.asarray(x, dtype=float)

def _ema_arr(arr, period):
    arr = _arr(arr)
    n = len(arr)
    if n < period:
        return np.full(n, np.nan)
    k = 2.0 / (period + 1)
    out = np.empty(n)
    out[:period-1] = np.nan
    out[period-1] = arr[:period].mean()
    for i in range(period, n):
        out[i] = arr[i] * k + out[i-1] * (1-k)
    return out

def _wma_arr(arr, period):
    arr = _arr(arr)
    n = len(arr)
    if n < period:
        return np.full(n, np.nan)
    weights = np.arange(1, period+1, dtype=float)
    w_sum = weights.sum()
    out = np.full(n, np.nan)
    for i in range(period-1, n):
        out[i] = np.dot(arr[i-period+1:i+1], weights) / w_sum
    return out

def _atr_arr(h, l, c, period=14):
    h, l, c = _arr(h), _arr(l), _arr(c)
    n = len(c)
    tr = np.empty(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
    return _ema_arr(tr, period)

def ind_hma(closes, period):
    c = _arr(closes)
    if len(c) < period:
        return np.full(len(c), np.nan)
    half = max(period//2, 2)
    sq   = max(int(np.sqrt(period)), 2)
    diff = 2.0 * _wma_arr(c, half) - _wma_arr(c, period)
    return _wma_arr(diff, sq)

def ind_hma_direction(closes, period):
    h = ind_hma(closes, period)
    v = h[~np.isnan(h)]
    if len(v) < 2: return 0
    d = v[-1] - v[-2]
    return 1 if d > 0 else (-1 if d < 0 else 0)

def ind_hma_crossover(closes, fast, slow):
    hf = ind_hma(closes, fast)
    hs = ind_hma(closes, slow)
    m = min(len(hf), len(hs))
    if m < 2: return 0
    hf2, hs2 = hf[-m:], hs[-m:]
    prev = hf2[-2] > hs2[-2]
    curr = hf2[-1] > hs2[-1]
    if not prev and curr: return 1
    if prev and not curr: return -1
    return 0

def ind_atr_now(h, l, c, period=14):
    a = _atr_arr(h, l, c, period)
    v = a[-1]
    return float(v) if not np.isnan(v) else 0.0

def ind_atr_pct(h, l, c, period=14):
    a = ind_atr_now(h, l, c, period)
    price = float(_arr(c)[-1])
    return (a/price) if price > 0 else 0.0

def ind_rsi(closes, period=14):
    c = _arr(closes)
    n = len(c)
    if n < period+1: return 50.0
    d = np.diff(c)
    g = np.where(d>0, d, 0.0)
    ls = np.where(d<0, -d, 0.0)
    ag = g[:period].mean()
    al = ls[:period].mean()
    for i in range(period, n-1):
        ag = (ag*(period-1)+g[i])/period
        al = (al*(period-1)+ls[i])/period
    rs = ag/al if al>0 else 0.0
    return float(100.0 - 100.0/(1.0+rs))

def ind_adx(h, l, c, period=14):
    h, l, c = _arr(h), _arr(l), _arr(c)
    n = len(c)
    if n < period*2: return 0.0, 0.0, 0.0
    tr = np.empty(n); pdm = np.empty(n); ndm = np.empty(n)
    tr[0] = h[0]-l[0]; pdm[0] = 0.0; ndm[0] = 0.0
    for i in range(1, n):
        tr[i]  = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
        up   = h[i]-h[i-1]; dn = l[i-1]-l[i]
        pdm[i] = up if (up>dn and up>0) else 0.0
        ndm[i] = dn if (dn>up and dn>0) else 0.0
    def ws(arr, p):
        out = np.empty(n); out[:p] = np.nan
        out[p] = arr[1:p+1].sum()
        for i in range(p+1, n): out[i] = out[i-1] - out[i-1]/p + arr[i]
        return out
    trs = ws(tr, period); pds = ws(pdm, period); nds = ws(ndm, period)
    with np.errstate(divide='ignore', invalid='ignore'):
        pdi = np.where(trs>0, 100.0*pds/trs, 0.0)
        ndi = np.where(trs>0, 100.0*nds/trs, 0.0)
        dx  = np.where((pdi+ndi)>0, 100.0*np.abs(pdi-ndi)/(pdi+ndi), 0.0)
    adx_a = ws(dx, period)
    av = adx_a[-1]; pv = pdi[-1]; nv = ndi[-1]
    return (float(av) if not np.isnan(av) else 0.0,
            float(pv) if not np.isnan(pv) else 0.0,
            float(nv) if not np.isnan(nv) else 0.0)

REGIME_TRENDING = "trending"
REGIME_RANGING  = "ranging"
REGIME_VOLATILE = "volatile"

def ind_market_regime(h, l, c, atr_fast=7, atr_slow=50, vol_thresh=1.8):
    h, l, c = _arr(h), _arr(l), _arr(c)
    if len(c) < atr_slow+1: return REGIME_RANGING
    af = _atr_arr(h, l, c, atr_fast)[-1]
    as_ = _atr_arr(h, l, c, atr_slow)[-1]
    if np.isnan(af) or np.isnan(as_) or as_==0: return REGIME_RANGING
    r = af/as_
    if r >= vol_thresh: return REGIME_VOLATILE
    if r >= 0.85: return REGIME_TRENDING
    return REGIME_RANGING

def ind_cvd_slope(o, h, l, c, v, period=20, slope_bars=5):
    o, c, v = _arr(o), _arr(c), _arr(v)
    delta = np.where(c>=o, v, -v).astype(float)
    cs = np.cumsum(delta)
    if len(cs) < slope_bars+1: return 0.0
    w = cs[-(slope_bars+1):]
    avg_v = float(v[-slope_bars:].mean()) or 1.0
    return float((w[-1]-w[0])/(slope_bars*avg_v))

def ind_cvd_divergence(o, h, l, c, v, lookback=10):
    o, c, v = _arr(o), _arr(c), _arr(v)
    if len(c) < lookback+1: return 0
    delta = np.where(c>=o, v, -v).astype(float)
    cs = np.cumsum(delta)
    pc = c[-1]-c[-lookback]; cc = cs[-1]-cs[-lookback]
    if pc<0 and cc>0: return 1
    if pc>0 and cc<0: return -1
    return 0

def ind_ofi(o, h, l, c, period=20):
    o, h, l, c = _arr(o), _arr(h), _arr(l), _arr(c)
    n = min(period, len(c))
    ow, hw, lw, cw = o[-n:], h[-n:], l[-n:], c[-n:]
    ranges = hw-lw; ranges = np.where(ranges==0, 1e-10, ranges)
    bp = np.abs(cw-ow)/ranges
    bull = np.where(cw>=ow, bp, 0.0).sum()
    bear = np.where(cw<ow,  bp, 0.0).sum()
    total = bull+bear
    return float(bull/total) if total>0 else 0.5

def ind_vol_ratio(volumes, period=20):
    v = _arr(volumes)
    if len(v) < period+1: return 1.0
    cur = float(v[-1]); avg = float(v[-period-1:-1].mean())
    return (cur/avg) if avg>0 else 1.0

def ind_liquidity_sweep(h, l, c, lookback=10):
    h, l, c = _arr(h), _arr(l), _arr(c)
    if len(l) < lookback+2: return 0
    prev_low  = float(l[-(lookback+2):-2].min())
    prev_high = float(h[-(lookback+2):-2].max())
    if l[-2] < prev_low  and c[-1] > prev_low:  return 1
    if h[-2] > prev_high and c[-1] < prev_high: return -1
    return 0

def ind_vwap(h, l, c, v):
    h, l, c, v = _arr(h), _arr(l), _arr(c), _arr(v)
    tp = (h+l+c)/3.0
    cv = np.cumsum(v)
    with np.errstate(invalid='ignore', divide='ignore'):
        vw = np.where(cv>0, np.cumsum(tp*v)/cv, np.nan)
    val = vw[-1]
    return float(val) if not np.isnan(val) else float(c[-1])

def ind_risk_size(balance, risk_pct, entry, sl, leverage=10,
                   min_u=5.0, max_u=100.0):
    if entry<=0 or sl<=0: return min_u
    sl_pct = abs(entry-sl)/entry
    if sl_pct==0: return min_u
    size = (balance*risk_pct)/sl_pct
    return float(max(min_u, min(size, max_u)))

def ind_vol_adj_size(base, atr_pct, target=0.02):
    if atr_pct<=0: return base
    m = max(0.3, min(target/atr_pct, 2.0))
    return float(base*m)

def ind_conf_mult(score, min_s=65.0, max_s=100.0):
    if score<=min_s: return 0.7
    if score>=max_s: return 1.3
    return float(0.7 + (score-min_s)/(max_s-min_s)*0.6)

# ══════════════════════════════════════════════════════════════════════════════
#  SIGNAL ENGINE
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SignalResult:
    symbol:      str
    direction:   str
    score:       float
    is_valid:    bool
    entry_price: float
    sl_price:    float
    tp1_price:   float
    tp2_price:   float
    size_usdt:   float
    atr:         float
    regime:      str
    breakdown:   dict = field(default_factory=dict)

    @classmethod
    def invalid(cls, symbol, reason=""):
        return cls(symbol=symbol, direction="", score=0.0, is_valid=False,
                   entry_price=0.0, sl_price=0.0, tp1_price=0.0, tp2_price=0.0,
                   size_usdt=0.0, atr=0.0, regime="", breakdown={"reason": reason})

    def summary(self):
        bd = self.breakdown
        return (
            f"🎯 <b>{self.symbol} {self.direction}</b>  score={self.score:.0f}/100\n"
            f"  Entry: <code>{self.entry_price:.4f}</code>  SL: <code>{self.sl_price:.4f}</code>\n"
            f"  TP1: <code>{self.tp1_price:.4f}</code>  TP2: <code>{self.tp2_price:.4f}</code>\n"
            f"  Size: <code>${self.size_usdt:.2f}</code>  ATR: <code>{self.atr:.4f}</code>\n"
            f"  CVD={bd.get('cvd_slope',0):.3f}  OFI={bd.get('ofi',0):.2f}  "
            f"vol={bd.get('vol_ratio',0):.2f}  regime={self.regime}"
        )


def evaluate_signal(symbol, ho, hh, hl, hc, hv,
                    mo, mh, ml, mc, mv,
                    lo, lh, ll, lc, lv, balance=100.0):
    HTF_SLOW=21; MTF_FAST=9; MTF_SLOW=21; LTF_FAST=9; LTF_SLOW=21
    SCORE_MIN=65.0; ADX_STRONG=22.0; VOL_MIN=1.2
    OFI_BULL=0.55; OFI_BEAR=0.45; SL_MULT=1.6; TP1_R=1.5; TP2_R=2.5

    htf_dir    = ind_hma_direction(hc, HTF_SLOW)
    htf_regime = ind_market_regime(hh, hl, hc)
    mtf_cross  = ind_hma_crossover(mc, MTF_FAST, MTF_SLOW)
    mtf_dir    = ind_hma_direction(mc, MTF_SLOW)
    mtf_adx, mtf_dip, mtf_dim = ind_adx(mh, ml, mc)
    ltf_cross  = ind_hma_crossover(lc, LTF_FAST, LTF_SLOW)
    ltf_dir    = ind_hma_direction(lc, LTF_FAST)
    vol_rat    = ind_vol_ratio(lv)
    ofi        = ind_ofi(lo, lh, ll, lc)
    cvd_sl     = ind_cvd_slope(lo, lh, ll, lc, lv)
    cvd_div    = ind_cvd_divergence(lo, lh, ll, lc, lv)
    ltf_rsi    = ind_rsi(lc)
    ltf_vwap   = ind_vwap(lh, ll, lc, lv)
    ltf_price  = float(_arr(lc)[-1])
    sweep      = ind_liquidity_sweep(lh, ll, lc)
    ltf_atr    = ind_atr_now(lh, ll, lc)
    ltf_atr_pct = ind_atr_pct(lh, ll, lc)

    if htf_dir==1 and mtf_dir>=0:   candidate="LONG"
    elif htf_dir==-1 and mtf_dir<=0: candidate="SHORT"
    else: return SignalResult.invalid(symbol, "HTF/MTF desalineados")

    score=0.0; bd={}

    # [1] HTF HMA — 20pts
    if (candidate=="LONG" and htf_dir==1) or (candidate=="SHORT" and htf_dir==-1):
        score+=20; bd["htf_hma"]=20
    else: bd["htf_hma"]=0

    # [2] Régimen — 15pts
    if htf_regime==REGIME_TRENDING: score+=15; bd["regime"]=15
    elif htf_regime==REGIME_VOLATILE: score+=5; bd["regime"]=5
    else: bd["regime"]=0

    # [3] MTF HMA — 15pts
    mp=0
    if (candidate=="LONG" and mtf_cross==1) or (candidate=="SHORT" and mtf_cross==-1): mp=15
    elif (candidate=="LONG" and mtf_dir==1) or (candidate=="SHORT" and mtf_dir==-1): mp=8
    score+=mp; bd["mtf_hma"]=mp

    # [4] ADX — 10pts
    ap=10 if mtf_adx>=ADX_STRONG else (5 if mtf_adx>=18 else 0)
    if candidate=="LONG"  and mtf_dip>mtf_dim: ap=min(ap+2, 10)
    if candidate=="SHORT" and mtf_dim>mtf_dip: ap=min(ap+2, 10)
    score+=ap; bd["adx"]=ap

    # [5] LTF trigger — 15pts
    lp=0
    if (candidate=="LONG" and ltf_cross==1) or (candidate=="SHORT" and ltf_cross==-1): lp=15
    elif (candidate=="LONG" and ltf_dir==1) or (candidate=="SHORT" and ltf_dir==-1): lp=7
    score+=lp; bd["ltf_trigger"]=lp

    # [6] CVD — 10pts
    cp=0
    if candidate=="LONG":
        if cvd_sl>0: cp+=5
        if cvd_div==1: cp+=5
    else:
        if cvd_sl<0: cp+=5
        if cvd_div==-1: cp+=5
    score+=cp; bd["cvd"]=cp; bd["cvd_slope"]=round(cvd_sl,4)

    # [7] OFI — 8pts
    if candidate=="LONG" and ofi>=OFI_BULL: op=8
    elif candidate=="SHORT" and ofi<=OFI_BEAR: op=8
    elif 0.48<ofi<0.52: op=0
    else: op=4
    score+=op; bd["ofi_pts"]=op; bd["ofi"]=round(ofi,3)

    # [8] Volume — 7pts
    vp=7 if vol_rat>=VOL_MIN else (3 if vol_rat>=1.0 else 0)
    score+=vp; bd["vol_pts"]=vp; bd["vol_ratio"]=round(vol_rat,2)

    # BONUS sweep +5
    sw_ok=(candidate=="LONG" and sweep==1) or (candidate=="SHORT" and sweep==-1)
    if sw_ok: score+=5
    bd["sweep"]=sw_ok

    # BONUS RSI +3 / -5
    if candidate=="LONG":
        if 40<=ltf_rsi<=65: score+=3
        elif ltf_rsi>70: score-=5
    else:
        if 35<=ltf_rsi<=60: score+=3
        elif ltf_rsi<30: score-=5
    bd["rsi"]=round(ltf_rsi,1)

    # BONUS VWAP +2
    if (candidate=="LONG" and ltf_price>ltf_vwap) or \
       (candidate=="SHORT" and ltf_price<ltf_vwap): score+=2

    score=max(0.0, score); bd["total"]=round(score,1)
    if score<SCORE_MIN:
        return SignalResult.invalid(symbol, f"Score {score:.0f}<{SCORE_MIN:.0f}")
    if ltf_atr==0:
        return SignalResult.invalid(symbol, "ATR=0")

    sl_d=ltf_atr*SL_MULT; r=sl_d
    if candidate=="LONG":
        sl=ltf_price-sl_d; tp1=ltf_price+r*TP1_R; tp2=ltf_price+r*TP2_R
    else:
        sl=ltf_price+sl_d; tp1=ltf_price-r*TP1_R; tp2=ltf_price-r*TP2_R

    base = ind_risk_size(balance, MAX_RISK_PCT, ltf_price, sl,
                          LEVERAGE, MIN_TRADE_USDT, MAX_TRADE_USDT)
    adj  = ind_vol_adj_size(base, ltf_atr_pct)
    final = adj * ind_conf_mult(score)
    if htf_regime==REGIME_VOLATILE: final*=0.6
    final = max(MIN_TRADE_USDT, final)

    return SignalResult(
        symbol=symbol, direction=candidate, score=round(score,1),
        is_valid=True, entry_price=ltf_price,
        sl_price=round(sl,6), tp1_price=round(tp1,6), tp2_price=round(tp2,6),
        size_usdt=round(final,2), atr=round(ltf_atr,6),
        regime=htf_regime, breakdown=bd,
    )

# ══════════════════════════════════════════════════════════════════════════════
#  BINGX REST
# ══════════════════════════════════════════════════════════════════════════════

class BingXRestError(Exception):
    def __init__(self, code, msg):
        self.code=code; self.msg=msg
        super().__init__(f"BingX API {code}: {msg}")

class BingXRest:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def _sess(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=50, ssl=True),
                timeout=aiohttp.ClientTimeout(total=8, connect=3),
                headers={"X-SOURCE-KEY": "robotrading"},
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _sign(self, params):
        q = urlencode(sorted(params.items()))
        return _hmac.new(API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()

    def _auth(self, extra=None):
        p = {"timestamp": int(time.time()*1000), **(extra or {})}
        p["signature"] = self._sign(p)
        return p

    async def _req(self, method, path, params=None, signed=False, retries=3):
        sess = await self._sess()
        url  = BASE_URL + path
        p = dict(params or {})
        if signed:
            p = self._auth(p); p["apiKey"] = API_KEY
        for attempt in range(retries):
            try:
                if method=="GET":
                    async with sess.get(url, params=p) as r:
                        body = await r.json(content_type=None)
                else:
                    async with sess.post(url, params=p) as r:
                        body = await r.json(content_type=None)
                code = body.get("code", 0)
                if code!=0: raise BingXRestError(code, body.get("msg","unknown"))
                return body.get("data", body)
            except BingXRestError: raise
            except Exception as e:
                if attempt<retries-1: await asyncio.sleep(0.5*(attempt+1))
                else: raise ConnectionError(f"Request failed: {e}")

    async def get_klines(self, symbol, interval, limit=100):
        data = await self._req("GET", "/openApi/swap/v3/quote/klines",
                               {"symbol":symbol,"interval":interval,"limit":limit})
        return data if isinstance(data, list) else []

    async def get_balance(self):
        data = await self._req("GET", "/openApi/swap/v2/user/balance", signed=True)
        if isinstance(data, dict):
            b = data.get("balance", {})
            if isinstance(b, dict): return float(b.get("availableMargin", b.get("available", 0)))
            return float(b)
        if isinstance(data, list):
            for item in data:
                if item.get("asset")=="USDT":
                    return float(item.get("availableMargin", item.get("free", 0)))
        return 0.0

    async def get_positions(self, symbol=""):
        params = {}
        if symbol: params["symbol"] = symbol
        data = await self._req("GET", "/openApi/swap/v2/user/positions", params=params, signed=True)
        return data if isinstance(data, list) else []

    async def get_open_position(self, symbol):
        for p in await self.get_positions(symbol):
            if abs(float(p.get("positionAmt",0)))>0: return p
        return None

    async def set_leverage(self, symbol, leverage):
        try:
            for side in ["LONG","SHORT"]:
                await self._req("POST", "/openApi/swap/v2/trade/leverage",
                                {"symbol":symbol,"side":side,"leverage":leverage}, signed=True)
            return True
        except: return False

    async def place_market_order(self, symbol, side, quantity, position_side="BOTH"):
        params = {"symbol":symbol,"side":side,"positionSide":position_side,
                  "type":"MARKET","quantity":str(round(quantity,6))}
        log.info(f"ORDER {symbol} {side} {position_side} qty={quantity}")
        if PAPER:
            log.info("[PAPER] Orden no enviada"); return {"orderId":"paper","status":"FILLED"}
        return await self._req("POST", "/openApi/swap/v2/trade/order", params=params, signed=True)

    async def close_position(self, symbol):
        pos = await self.get_open_position(symbol)
        if not pos: return {}
        amt = abs(float(pos.get("positionAmt",0)))
        if amt==0: return {}
        ps   = pos.get("positionSide","BOTH")
        side = "SELL" if float(pos.get("positionAmt",0))>0 else "BUY"
        return await self.place_market_order(symbol, side, amt, ps)

    async def get_price(self, symbol):
        data = await self._req("GET", "/openApi/swap/v2/quote/price", {"symbol":symbol})
        if isinstance(data, dict): return float(data.get("price",0))
        if isinstance(data, list) and data: return float(data[0].get("price",0))
        return 0.0

    async def get_tickers(self):
        try:
            data = await self._req("GET", "/openApi/swap/v2/quote/ticker")
            return data if isinstance(data, list) else []
        except: return []

    async def get_min_qty(self, symbol):
        try:
            data = await self._req("GET", "/openApi/swap/v2/quote/contracts")
            contracts = data if isinstance(data, list) else []
            for c in contracts:
                if c.get("symbol")==symbol:
                    return float(c.get("minQty", c.get("tradeMinQuantity",0.001)))
        except: pass
        return 0.001

bingx = BingXRest()

# ══════════════════════════════════════════════════════════════════════════════
#  BINGX WEBSOCKET
# ══════════════════════════════════════════════════════════════════════════════

_HB_INTERVAL = 20
_HB_TIMEOUT  = 10

class KlineBuffer:
    def __init__(self, symbol, interval, maxlen=200):
        self.symbol=symbol; self.interval=interval
        self._data: deque = deque(maxlen=maxlen)
        self._current=None; self.last_close_ts=0

    def seed(self, klines):
        self._data.clear()
        for k in klines:
            self._data.append({"ts":int(k[0]),"o":float(k[1]),"h":float(k[2]),
                                "l":float(k[3]),"c":float(k[4]),"v":float(k[5]),"closed":True})
        if self._data: self.last_close_ts = self._data[-1]["ts"]

    def update_tick(self, tick):
        if tick["closed"]:
            self._data.append({**tick,"closed":True})
            self.last_close_ts=tick["ts"]; self._current=None
        else: self._current=tick

    def to_arrays(self):
        candles=list(self._data)
        if self._current: candles.append(self._current)
        if not candles: return [],[],[],[],[]
        return ([c["o"] for c in candles],[c["h"] for c in candles],
                [c["l"] for c in candles],[c["c"] for c in candles],
                [c["v"] for c in candles])

    def latest_close(self):
        if self._current: return self._current["c"]
        if self._data: return self._data[-1]["c"]
        return 0.0

    def ready(self, min_candles=60): return len(self._data)>=min_candles


class BingXWebSocket:
    def __init__(self):
        self.buffers: Dict[str,KlineBuffer]={}
        self._callbacks: List[Callable]=[]
        self._subs: Set[str]=set()
        self._ws=None; self._running=False
        self._pong=asyncio.Event()

    def add_buffer(self, symbol, interval, maxlen=200):
        key=f"{symbol}@kline_{interval}"
        if key not in self.buffers: self.buffers[key]=KlineBuffer(symbol,interval,maxlen)
        return self.buffers[key]

    def get_buffer(self, symbol, interval):
        return self.buffers.get(f"{symbol}@kline_{interval}")

    def on_candle_close(self, cb): self._callbacks.append(cb)

    async def _send(self, msg):
        if self._ws:
            try: await self._ws.send(json.dumps(msg) if isinstance(msg,dict) else msg)
            except: pass

    async def _subscribe_all(self):
        for key in self.buffers:
            await self._send({"id":str(int(time.time()*1000)),"reqType":"sub","dataType":key})
            await asyncio.sleep(0.05)

    async def _heartbeat(self):
        await asyncio.sleep(_HB_INTERVAL)
        while self._running and self._ws:
            ts=int(time.time()*1000); self._pong.clear()
            await self._send({"ping":ts})
            try:
                await asyncio.wait_for(self._pong.wait(), timeout=_HB_TIMEOUT)
            except asyncio.TimeoutError:
                log.warning("WS pong timeout — reconnecting")
                try: await self._ws.close()
                except: pass
                break
            await asyncio.sleep(_HB_INTERVAL)

    def _parse_tick(self, data):
        if not data: return None
        k=data
        if isinstance(data,dict) and "c" in data and isinstance(data["c"],dict): k=data["c"]
        try:
            return {"ts":int(k.get("t",k.get("T",k.get("startTime",0)))),
                    "o":float(k.get("o",k.get("open",0))),
                    "h":float(k.get("h",k.get("high",0))),
                    "l":float(k.get("l",k.get("low",0))),
                    "c":float(k.get("c",k.get("close",0))),
                    "v":float(k.get("v",k.get("volume",0))),
                    "closed":bool(k.get("x",k.get("X",k.get("confirm",False))))}
        except: return None

    async def _handle(self, raw):
        try:
            try: text=gzip.decompress(raw).decode("utf-8")
            except: text=raw.decode("utf-8") if isinstance(raw,bytes) else raw
            msg=json.loads(text)
            if msg=="Ping": await self._send("Pong"); return
            if isinstance(msg,dict):
                if "pong" in msg or msg.get("e")=="pong": self._pong.set(); return
                if "ping" in msg: await self._send({"pong":msg["ping"]}); return
            dt=msg.get("dataType",msg.get("e",""))
            if "@kline_" not in str(dt): return
            buf=self.buffers.get(dt)
            if not buf: return
            tick=self._parse_tick(msg.get("data",msg.get("k",{})))
            if tick is None: return
            buf.update_tick(tick)
            if tick["closed"]:
                parts=dt.split("@kline_"); sym,iv=parts[0],parts[1]
                for cb in self._callbacks: asyncio.create_task(cb(sym,iv,buf))
        except Exception as e: log.error(f"WS handle error: {e}")

    async def run(self):
        self._running=True; backoff=1
        while self._running:
            hbt=None
            try:
                async with websockets.connect(WS_URL, ping_interval=None,
                                               open_timeout=15, max_size=2**20) as ws:
                    self._ws=ws; backoff=1
                    await self._subscribe_all()
                    log.info("✅ WebSocket conectado")
                    hbt=asyncio.create_task(self._heartbeat())
                    async for msg in ws: await self._handle(msg)
            except (ConnectionClosedOK,ConnectionClosedError) as e:
                log.warning(f"WS closed: {e}")
            except Exception as e:
                log.error(f"WS error: {e}")
            finally:
                if hbt and not hbt.done(): hbt.cancel()
                self._ws=None
            if self._running: await asyncio.sleep(backoff); backoff=min(backoff*2,60)

    async def stop(self):
        self._running=False
        if self._ws: await self._ws.close()

ws_client = BingXWebSocket()

# ══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════════════════════

class TelegramNotifier:
    def __init__(self):
        self._session=None; self._queue=asyncio.Queue(); self._task=None

    def start(self):
        if self._task is None or self._task.done():
            self._task=asyncio.create_task(self._worker())

    async def close(self):
        if self._task and not self._task.done(): self._task.cancel()
        if self._session and not self._session.closed: await self._session.close()

    async def _sess(self):
        if self._session is None or self._session.closed:
            self._session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        return self._session

    async def send(self, text): await self._queue.put(text)

    async def _send_now(self, text):
        if not TG_TOKEN or not TG_CHAT_ID: log.debug(f"[TG] {text[:80]}"); return
        url=f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        try:
            sess=await self._sess()
            async with sess.post(url, json={"chat_id":TG_CHAT_ID,"text":text,
                                             "parse_mode":"HTML","disable_web_page_preview":True}) as r:
                if r.status!=200: log.warning(f"TG error {r.status}")
        except Exception as e: log.warning(f"TG send failed: {e}")

    async def _worker(self):
        while True:
            try:
                text=await self._queue.get()
                await self._send_now(text); self._queue.task_done()
                await asyncio.sleep(0.3)
            except asyncio.CancelledError: break
            except Exception as e: log.error(f"TG worker: {e}")

    async def notify_start(self, symbols):
        mode="📄 PAPER" if PAPER else "💰 REAL"
        s=", ".join(symbols[:10])+("..." if len(symbols)>10 else "")
        await self.send(f"🤖 <b>Bot iniciado</b> — {mode}\n{s}\nHTF={HTF} MTF={MTF} LTF={LTF}")

    async def notify_signal(self, summary): await self.send(f"🔔 <b>Señal</b>\n{summary}")

    async def notify_order(self, symbol, direction, entry, sl, tp, size, order_id):
        e="🟢" if direction=="LONG" else "🔴"
        await self.send(f"{e} <b>{direction}</b> {symbol}\nEntry <code>{entry:.4f}</code> "
                        f"SL <code>{sl:.4f}</code> TP <code>{tp:.4f}</code> ${size:.2f}")

    async def notify_close(self, symbol, pnl, reason):
        e="✅" if pnl>=0 else "❌"
        await self.send(f"{e} <b>CERRADO</b> {symbol} — {reason}\nPnL <code>{pnl:+.2f} USDT</code>")

    async def notify_error(self, msg): await self.send(f"⚠️ <b>ERROR</b>\n<code>{msg[:400]}</code>")

telegram = TelegramNotifier()

# ══════════════════════════════════════════════════════════════════════════════
#  SYMBOL SCANNER
# ══════════════════════════════════════════════════════════════════════════════

class SymbolScanner:
    def __init__(self):
        self._symbols=[]; self._last=0.0; self._lock=asyncio.Lock()

    async def get_symbols(self, rest_client):
        async with self._lock:
            if time.time()-self._last>SYMBOL_REFRESH_INTERVAL or not self._symbols:
                await self._refresh(rest_client)
        return list(self._symbols)

    async def _refresh(self, rest_client):
        try:
            tickers=await rest_client.get_tickers()
            if not tickers: return
            candidates=[]
            for t in tickers:
                s=t.get("symbol","")
                if not s.endswith("-USDT") or s in BLACKLIST: continue
                try: v=float(t.get("quoteVolume",t.get("volume",t.get("turnover",0))))
                except: continue
                if v>=MIN_QUOTE_VOLUME: candidates.append((s,v))
            if not candidates:
                candidates=[(t.get("symbol",""),float(t.get("quoteVolume",0)))
                             for t in tickers if t.get("symbol","").endswith("-USDT")
                             and t.get("symbol","") not in BLACKLIST]
            candidates.sort(key=lambda x:x[1], reverse=True)
            new=[s for s,_ in candidates[:TOP_N_SYMBOLS]]
            if new:
                self._symbols=new; self._last=time.time()
                log.info(f"🔍 {len(new)} símbolos: {', '.join(new[:8])}...")
        except Exception as e: log.error(f"SymbolScanner: {e}")

symbol_scanner = SymbolScanner()

# ══════════════════════════════════════════════════════════════════════════════
#  BOT STATE & MAIN LOOP
# ══════════════════════════════════════════════════════════════════════════════

class BotState:
    def __init__(self):
        self.open_positions: Dict[str,dict]={}
        self.locked: Set[str]=set()
        self.balance=0.0
        self.day_start_balance=0.0
        self.daily_pnl=0.0
        self.circuit_open=False
        self._cb_day=-1

    def record_pnl(self, pnl):
        self.daily_pnl+=pnl
        if self.day_start_balance>0:
            loss=(-self.daily_pnl/self.day_start_balance)
            if loss>=MAX_DAILY_LOSS_PCT and not self.circuit_open:
                self.circuit_open=True
                log.warning(f"🛑 Circuit breaker: {loss*100:.1f}%")
                asyncio.create_task(telegram.notify_error(
                    f"🛑 Circuit breaker activado\nPérdida: {self.daily_pnl:.2f} USDT ({loss*100:.1f}%)"))

    def reset_daily(self):
        today=datetime.now(timezone.utc).timetuple().tm_yday
        if today!=self._cb_day:
            self._cb_day=today; self.daily_pnl=0.0
            if self.circuit_open:
                self.circuit_open=False
                log.info("✅ Circuit breaker reseteado")
                asyncio.create_task(telegram.send("✅ Circuit breaker reseteado — nuevo día"))
            if self.balance>0: self.day_start_balance=self.balance

state = BotState()


async def seed_one(symbol, interval, buf):
    try:
        klines=await bingx.get_klines(symbol, interval, limit=200)
        buf.seed(klines)
    except Exception as e: log.warning(f"seed {symbol} {interval}: {e}")


async def seed_all(symbols):
    tasks=[]
    for sym in symbols:
        for tf in [HTF,MTF,LTF]:
            buf=ws_client.get_buffer(sym,tf)
            if buf: tasks.append(seed_one(sym,tf,buf))
    for i in range(0, len(tasks), 20):
        await asyncio.gather(*tasks[i:i+20], return_exceptions=True)
        if i+20<len(tasks): await asyncio.sleep(0.5)


async def sync_positions():
    try:
        for pos in await bingx.get_positions():
            amt=float(pos.get("positionAmt",0))
            if abs(amt)>0:
                sym=pos.get("symbol","")
                state.open_positions[sym]={
                    "direction":"LONG" if amt>0 else "SHORT",
                    "entry":float(pos.get("avgPrice",pos.get("entryPrice",0))),
                    "sl":0.0,"tp1":0.0,"tp2":0.0,
                    "size":abs(amt),"size_remaining":abs(amt),
                    "tp1_hit":False,"trailing_active":False,"atr":0.0,"synced":True}
                log.info(f"Posición existente: {sym}")
    except Exception as e: log.warning(f"sync_positions: {e}")


async def execute_trade(sig: SignalResult):
    if state.circuit_open or sig.symbol in state.locked or sig.symbol in state.open_positions:
        return
    if len(state.open_positions)>=MAX_POSITIONS: return
    if state.balance<sig.size_usdt: return

    state.locked.add(sig.symbol)
    try:
        price=sig.entry_price
        qty=round(max((sig.size_usdt*LEVERAGE)/price, await bingx.get_min_qty(sig.symbol)), 6)
        side="BUY" if sig.direction=="LONG" else "SELL"
        ps="BOTH" if POSITION_MODE=="ONE_WAY" else sig.direction
        order=await bingx.place_market_order(sig.symbol, side, qty, ps)
        oid=str(order.get("orderId","?"))
        atr=sig.atr
        if sig.direction=="LONG":
            tp1=price+atr*TP1_ATR_MULT; tp2=sig.tp2_price; sl=sig.sl_price
        else:
            tp1=price-atr*TP1_ATR_MULT; tp2=sig.tp2_price; sl=sig.sl_price
        state.open_positions[sig.symbol]={
            "direction":sig.direction,"entry":price,"sl":sl,"tp1":tp1,"tp2":tp2,
            "size":qty,"size_remaining":qty,"tp1_hit":False,"trailing_active":False,
            "atr":atr,"order_id":oid}
        log.info(f"✅ {sig.symbol} {sig.direction} qty={qty} SL={sl:.4f} TP1={tp1:.4f} TP2={tp2:.4f}")
        await telegram.notify_order(sig.symbol,sig.direction,price,sl,tp2,sig.size_usdt,oid)
    except Exception as e:
        log.error(f"execute_trade {sig.symbol}: {e}")
        await telegram.notify_error(f"{sig.symbol}: {e}")
    finally: state.locked.discard(sig.symbol)


async def monitor_positions():
    for symbol in list(state.open_positions):
        pos=state.open_positions.get(symbol)
        if not pos or (pos.get("sl",0)==0 and pos.get("tp2",0)==0): continue
        try:
            price=await bingx.get_price(symbol)
            if price==0: continue
            d=pos["direction"]; sl=pos["sl"]; tp1=pos["tp1"]; tp2=pos["tp2"]
            atr=pos.get("atr",0); rem=pos["size_remaining"]; entry=pos["entry"]

            if not pos["tp1_hit"]:
                hit=((d=="LONG" and price>=tp1) or (d=="SHORT" and price<=tp1))
                if hit:
                    qty=round(rem*TP1_FRACTION, 6)
                    cs="SELL" if d=="LONG" else "BUY"
                    ps="BOTH" if POSITION_MODE=="ONE_WAY" else d
                    await bingx.place_market_order(symbol, cs, qty, ps)
                    pnl=((price-entry) if d=="LONG" else (entry-price))*qty*LEVERAGE
                    pos["tp1_hit"]=True; pos["trailing_active"]=True
                    pos["size_remaining"]=round(rem-qty,6); pos["sl"]=entry
                    state.record_pnl(pnl)
                    log.info(f"🎯 TP1 {symbol} pnl≈{pnl:.2f}")
                    await telegram.send(f"🎯 <b>TP1</b> {symbol} +{pnl:.2f} USDT | SL→BE")
                    continue

            if pos["trailing_active"] and atr>0:
                if d=="LONG":
                    ns=price-atr*TRAIL_STEP_ATR
                    if ns>pos["sl"]: pos["sl"]=ns
                else:
                    ns=price+atr*TRAIL_STEP_ATR
                    if ns<pos["sl"]: pos["sl"]=ns

            hit_tp2=((d=="LONG" and price>=tp2) or (d=="SHORT" and price<=tp2))
            hit_sl =((d=="LONG" and price<=pos["sl"]) or (d=="SHORT" and price>=pos["sl"]))
            if hit_tp2 or hit_sl:
                reason="TP2" if hit_tp2 else "SL"
                await bingx.close_position(symbol)
                r=pos["size_remaining"]; e=pos["entry"]
                pnl=((price-e) if d=="LONG" else (e-price))*r*LEVERAGE
                state.record_pnl(pnl)
                del state.open_positions[symbol]
                await telegram.notify_close(symbol, pnl, reason)
        except Exception as e: log.error(f"monitor {symbol}: {e}")


async def on_candle_close(symbol, interval, buf):
    if interval!=LTF: return
    try:
        hb=ws_client.get_buffer(symbol,HTF); mb=ws_client.get_buffer(symbol,MTF)
        if not hb or not mb: return
        if not (hb.ready(55) and mb.ready(20) and buf.ready(55)): return
        ho,hh,hl,hc,hv=hb.to_arrays(); mo,mh,ml,mc,mv=mb.to_arrays()
        lo,lh,ll,lc,lv=buf.to_arrays()
        sig=evaluate_signal(symbol,ho,hh,hl,hc,hv,mo,mh,ml,mc,mv,lo,lh,ll,lc,lv,state.balance)
        if sig.is_valid:
            log.info(f"🎯 {sig.summary()[:80]}")
            await telegram.notify_signal(sig.summary())
            await execute_trade(sig)
    except Exception as e: log.error(f"on_candle_close {symbol}: {e}", exc_info=True)


async def main_loop():
    last_balance=last_monitor=0.0; last_rotation=time.time()
    while True:
        now=time.time(); state.reset_daily()
        if now-last_balance>60:
            try: state.balance=await bingx.get_balance()
            except: pass
            if state.day_start_balance==0 and state.balance>0:
                state.day_start_balance=state.balance
            last_balance=now
        if now-last_monitor>5:
            await monitor_positions(); last_monitor=now
        if now-last_rotation>3600:
            new_syms=await symbol_scanner.get_symbols(bingx)
            for sym in new_syms:
                for tf in [HTF,MTF,LTF]:
                    if not ws_client.get_buffer(sym,tf):
                        ws_client.add_buffer(sym,tf,maxlen=250)
                        await seed_one(sym,tf,ws_client.get_buffer(sym,tf))
            last_rotation=now
        await asyncio.sleep(1.0)


async def run():
    log.info("="*60)
    log.info("   🤖 BINGX BOT v2 — archivo único")
    log.info("="*60)
    validate_config()
    telegram.start()
    await telegram.notify_start(SYMBOLS)
    try: state.balance=await bingx.get_balance()
    except: pass
    state.day_start_balance=state.balance

    if SYMBOLS==["BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT"]:
        active=await symbol_scanner.get_symbols(bingx)
    else:
        active=SYMBOLS
    if not active:
        log.critical("Sin símbolos"); sys.exit(1)

    await asyncio.gather(*[bingx.set_leverage(s, LEVERAGE) for s in active],
                          return_exceptions=True)
    for sym in active:
        for tf in [HTF,MTF,LTF]: ws_client.add_buffer(sym,tf,maxlen=250)
    await seed_all(active)
    await sync_positions()
    ws_client.on_candle_close(on_candle_close)

    log.info(f"Símbolos: {len(active)} | Balance: ${state.balance:.2f} | Paper: {PAPER}")
    await asyncio.gather(ws_client.run(), main_loop())


if __name__=="__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Bot detenido.")
    finally:
        loop=asyncio.new_event_loop()
        for coro in (bingx.close(), telegram.close()):
            try: loop.run_until_complete(coro)
            except: pass
        loop.close()
