"""
Signal Engine v5 — CVD + MTF + Filtros Graduales
=================================================
PROBLEMA v4: 5 filtros duros encadenados → cero señales.
SOLUCIÓN v5: 1 filtro duro + todo lo demás suma/resta score.

Señales detectadas:
  1. EMA Cross (base)
  2. BB Squeeze + expansión
  3. RSI OS/OB
  4. CVD bullish/bearish (Cumulative Volume Delta)
  5. 3-candle pattern
  MTF, ADX, OB, Funding → ajustan score, no bloquean
"""

import logging
import os
import numpy as np
from typing import Optional

from bingx_client import BingXClient
from order_book   import OrderBookAnalyzer, OrderBookSnapshot

log = logging.getLogger("SIGNAL")

FAST_LEN        = int  (os.getenv("MA_FAST",           "9"))
SLOW_LEN        = int  (os.getenv("MA_SLOW",           "21"))
PROJ_LEN        = int  (os.getenv("PROJ_LENGTH",       "10"))
MIN_MEAN_PNL    = float(os.getenv("MIN_MEAN_PNL",      "0.0005"))
SIGNAL_LOOKBACK = int  (os.getenv("SIGNAL_LOOKBACK",   "3"))
RSI_PERIOD      = int  (os.getenv("RSI_PERIOD",        "14"))
RSI_OB          = float(os.getenv("RSI_OB",            "70"))
RSI_OS          = float(os.getenv("RSI_OS",            "30"))
MIN_SIGNALS_HIST= int  (os.getenv("MIN_SIGNALS_HIST",  "1"))
MIN_SCORE       = float(os.getenv("MIN_SIGNAL_SCORE",  "40"))
ADX_PERIOD      = int  (os.getenv("ADX_PERIOD",        "14"))
BB_PERIOD       = int  (os.getenv("BB_PERIOD",         "20"))
BB_MULT         = float(os.getenv("BB_MULT",           "2.0"))
TP_RR           = float(os.getenv("TP_RR",             "2.0"))
USE_HTF         = os.getenv("USE_TREND_FILTER",  "true").lower() == "true"
USE_OB          = os.getenv("USE_OB_FILTER",     "true").lower() == "true"
TREND_INTERVAL  = os.getenv("TREND_INTERVAL",    "4h")
SL_ATR_MULT     = float(os.getenv("SL_ATR_MULT",      "1.5"))
KLINE_INTERVAL  = os.getenv("KLINE_INTERVAL",    "1h")


# ================================================================== #
def ema(arr, period):
    out = np.full(len(arr), np.nan)
    if len(arr) < period: return out
    out[period-1] = arr[:period].mean()
    k = 2/(period+1)
    for i in range(period, len(arr)):
        out[i] = arr[i]*k + out[i-1]*(1-k)
    return out

def rsi(arr, period=14):
    out = np.full(len(arr), np.nan)
    if len(arr) < period+1: return out
    d = np.diff(arr)
    g = np.where(d>0, d, 0.); l = np.where(d<0, -d, 0.)
    ag, al = g[:period].mean(), l[:period].mean()
    for i in range(period, len(d)):
        ag = (ag*(period-1)+g[i])/period
        al = (al*(period-1)+l[i])/period
        out[i+1] = 100. if al==0 else 100-100/(1+ag/al)
    return out

def bollinger_squeeze(closes, period=20, mult=2.0):
    n = len(closes)
    bw = np.full(n, np.nan)
    upper = np.full(n, np.nan); lower = np.full(n, np.nan)
    for i in range(period-1, n):
        sl = closes[i-period+1:i+1]
        m = sl.mean(); s = sl.std()
        upper[i] = m+mult*s; lower[i] = m-mult*s
        bw[i] = (upper[i]-lower[i])/m*100 if m>0 else 0
    squeeze = False; exp_pct = 0.0
    lookback = min(50, n-1)
    if not np.isnan(bw[-1]) and lookback > 5:
        hist = bw[-lookback-1:-1]; hist = hist[~np.isnan(hist)]
        if len(hist) > 5:
            squeeze = bw[-1] < hist.min()*1.15
            if len(bw)>=4 and not np.isnan(bw[-4]) and bw[-4]>0:
                exp_pct = (bw[-1]-bw[-4])/bw[-4]*100
    return upper, lower, bw, squeeze, exp_pct

def cvd(closes, opens, highs, lows, volumes):
    """Cumulative Volume Delta — presión compradora vs vendedora."""
    hl = np.where(highs-lows == 0, 1e-10, highs-lows)
    bull_vol = volumes*(closes-lows)/hl
    bear_vol = volumes*(highs-closes)/hl
    delta = bull_vol-bear_vol
    cum   = np.cumsum(delta)
    window = min(20, len(volumes))
    tv = volumes[-window:].sum()
    bv = bull_vol[-window:].sum()
    pct = bv/tv*100 if tv>0 else 50.
    trend_up = cum[-5:].mean() > cum[-10:-5].mean() if len(cum)>=10 else True
    return cum, round(pct, 1), trend_up

def atr(highs, lows, closes, period=14):
    n = len(closes); tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
    out = np.full(n, np.nan)
    if n >= period:
        out[period-1] = tr[1:period].mean()
        for i in range(period, n):
            out[i] = (out[i-1]*(period-1)+tr[i])/period
    return out

def adx_last(highs, lows, closes, period=14):
    n = len(closes)
    if n < period*2+5: return 0.
    tr=np.zeros(n); pdm=np.zeros(n); ndm=np.zeros(n)
    for i in range(1,n):
        hd=highs[i]-highs[i-1]; ld=lows[i-1]-lows[i]
        tr[i]=max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1]))
        pdm[i]=hd if hd>ld and hd>0 else 0
        ndm[i]=ld if ld>hd and ld>0 else 0
    def _s(a):
        out=np.zeros(n)
        if n>period:
            out[period]=a[1:period+1].sum()
            for i in range(period+1,n): out[i]=out[i-1]-out[i-1]/period+a[i]
        return out
    a=_s(tr)
    with np.errstate(divide="ignore",invalid="ignore"):
        pdi=np.where(a>0,_s(pdm)/a*100,0)
        ndi=np.where(a>0,_s(ndm)/a*100,0)
        dx =np.where(pdi+ndi>0,np.abs(pdi-ndi)/(pdi+ndi)*100,0)
    adx_out=np.zeros(n)
    if n>period*2:
        adx_out[period*2]=dx[period:period*2].mean()
        for i in range(period*2+1,n): adx_out[i]=(adx_out[i-1]*(period-1)+dx[i])/period
    return float(adx_out[-1])

def three_candles(closes, opens):
    if len(closes)<4: return None
    c=closes[-4:]; o=opens[-4:]
    bull=all(c[i]>o[i] and c[i]>c[i-1] for i in range(1,4))
    bear=all(c[i]<o[i] and c[i]<c[i-1] for i in range(1,4))
    return "bull" if bull else ("bear" if bear else None)

def crossover(a, b):
    sig=np.zeros(len(a),dtype=bool)
    for i in range(1,len(a)):
        if any(np.isnan(x) for x in [a[i],b[i],a[i-1],b[i-1]]): continue
        if a[i-1]<=b[i-1] and a[i]>b[i]: sig[i]=True
    return sig

def crossunder(a, b):
    sig=np.zeros(len(a),dtype=bool)
    for i in range(1,len(a)):
        if any(np.isnan(x) for x in [a[i],b[i],a[i-1],b[i-1]]): continue
        if a[i-1]>=b[i-1] and a[i]<b[i]: sig[i]=True
    return sig

def project_pnl(closes, signals, proj_len):
    day_pnls=[[] for _ in range(proj_len)]
    for si in np.where(signals)[0]:
        for step in range(1,proj_len+1):
            fi=si+step
            if fi>=len(closes): break
            day_pnls[step-1].append(closes[fi]/closes[si]-1)
    result={}
    for step,pnls in enumerate(day_pnls):
        if not pnls: continue
        arr=np.array(pnls)
        result[step]={"worst":float(arr.min()),"best":float(arr.max()),
                      "p25":float(np.percentile(arr,25)),"p75":float(np.percentile(arr,75)),
                      "mean":float(arr.mean()),"median":float(np.median(arr)),"count":len(pnls)}
    return result


# ================================================================== #
class SignalEngine:
    def __init__(self, ob_analyzer=None):
        self.ob = ob_analyzer

    async def evaluate(self, client, symbol, ob_snap=None):
        try:
            klines = await client.get_klines(symbol, KLINE_INTERVAL,
                                             limit=max(SLOW_LEN*4, 250))
            if len(klines) < SLOW_LEN+PROJ_LEN+20: return None

            closes  = np.array([float(k["close"])  for k in klines])
            highs   = np.array([float(k["high"])   for k in klines])
            lows    = np.array([float(k["low"])    for k in klines])
            opens   = np.array([float(k["open"])   for k in klines])
            volumes = np.array([float(k["volume"]) for k in klines])

            ma_fast = ema(closes, FAST_LEN)
            ma_slow = ema(closes, SLOW_LEN)
            rsi_arr = rsi(closes, RSI_PERIOD)
            atr_arr = atr(highs, lows, closes, 14)
            bb_u, bb_l, bb_bw, is_squeeze, bb_exp = bollinger_squeeze(closes, BB_PERIOD, BB_MULT)
            _, cvd_pct, cvd_up = cvd(closes, opens, highs, lows, volumes)
            adx_val = adx_last(highs, lows, closes, ADX_PERIOD)
            pat = three_candles(closes, opens)

            lc  = crossover(ma_fast, ma_slow)
            sc_ = crossunder(ma_fast, ma_slow)
            w   = SIGNAL_LOOKBACK

            price       = closes[-1]
            ema_slow_v  = float(ma_slow[-1]) if not np.isnan(ma_slow[-1]) else price
            current_atr = float(atr_arr[-1]) if not np.isnan(atr_arr[-1]) else price*0.01
            current_rsi = float(rsi_arr[-1]) if not np.isnan(rsi_arr[-1]) else 50.
            bw_now      = float(bb_bw[-1])   if not np.isnan(bb_bw[-1])   else 0.

            direction   = None
            signal_type = None
            active_sigs = []

            # ── Detección de señales ──────────────────────────────────
            if bool(lc[-w:].any()) and not bool(sc_[-w:].any()):
                direction, signal_type = "LONG",  "EMA_CROSS"
                active_sigs.append("EMA Cross ↑")
            elif bool(sc_[-w:].any()) and not bool(lc[-w:].any()):
                direction, signal_type = "SHORT", "EMA_CROSS"
                active_sigs.append("EMA Cross ↓")

            if is_squeeze and bb_exp > 3:
                above_mid = price > (float(bb_u[-1])+float(bb_l[-1]))/2
                if direction is None:
                    direction   = "LONG"  if above_mid else "SHORT"
                    signal_type = "BB_SQUEEZE"
                active_sigs.append(f"BB Squeeze {bw_now:.1f}%")

            rn = float(rsi_arr[-1]) if not np.isnan(rsi_arr[-1]) else 50.
            rp = float(rsi_arr[-2]) if len(rsi_arr)>1 and not np.isnan(rsi_arr[-2]) else 50.
            if rp<=RSI_OS and rn>RSI_OS:
                if direction is None: direction, signal_type = "LONG",  "RSI_OS"
                active_sigs.append(f"RSI OS {rn:.0f}")
            elif rp>=RSI_OB and rn<RSI_OB:
                if direction is None: direction, signal_type = "SHORT", "RSI_OB"
                active_sigs.append(f"RSI OB {rn:.0f}")

            if cvd_pct >= 60 and cvd_up:
                if direction is None: direction, signal_type = "LONG",  "CVD_BULL"
                active_sigs.append(f"CVD {cvd_pct:.0f}% bull")
            elif cvd_pct <= 40 and not cvd_up:
                if direction is None: direction, signal_type = "SHORT", "CVD_BEAR"
                active_sigs.append(f"CVD {cvd_pct:.0f}% bear")

            if pat == "bull":
                if direction is None: direction, signal_type = "LONG",  "3C_BULL"
                active_sigs.append("3 velas bull")
            elif pat == "bear":
                if direction is None: direction, signal_type = "SHORT", "3C_BEAR"
                active_sigs.append("3 velas bear")

            if direction is None:
                return None

            # ── Scoring aditivo ────────────────────────────────────────
            score = 0.; sd = {}

            sig_pts  = min(25, len(active_sigs)*8)
            score   += sig_pts;  sd["signals"] = sig_pts

            cvd_pts  = min(20,(cvd_pct-50)/2) if direction=="LONG" and cvd_pct>50 else \
                       (min(20,(50-cvd_pct)/2) if direction=="SHORT" and cvd_pct<50 else 0.)
            score   += cvd_pts;  sd["cvd"] = round(cvd_pts,1)

            bb_pts   = min(15, 5+bb_exp/3) if is_squeeze else 0.
            score   += bb_pts;  sd["bb"] = round(bb_pts,1)

            rsi_pts  = (10 if 45<=current_rsi<=65 else 7 if 30<=current_rsi<45 else 5) \
                       if direction=="LONG" else \
                       (10 if 35<=current_rsi<=55 else 7 if 55<current_rsi<=70 else 5)
            score   += rsi_pts;  sd["rsi"] = rsi_pts

            adx_pts  = min(10, max(0,(adx_val-15)/2))
            score   += adx_pts;  sd["adx"] = round(adx_pts,1)

            ob_bias="NEUTRAL"; ob_imb=1.; ob_delta=0.; ob_absorb=False; ob_pts=0.
            if USE_OB and ob_snap:
                ob_bias=ob_snap.bias; ob_imb=ob_snap.imbalance_ratio
                ob_delta=ob_snap.bid_delta_pct; ob_absorb=ob_snap.absorption_signal
                if direction=="LONG":
                    if ob_bias=="BULLISH":   ob_pts+=8
                    elif ob_bias=="BEARISH": ob_pts-=5
                    if ob_delta>3:           ob_pts+=4
                    if ob_absorb:            ob_pts+=5
                else:
                    if ob_bias=="BEARISH":   ob_pts+=8
                    elif ob_bias=="BULLISH": ob_pts-=5
                    if ob_snap.ask_delta_pct>3: ob_pts+=4
                    if ob_absorb:               ob_pts+=5
                ob_pts = max(-10, ob_pts)
            score += ob_pts;  sd["ob"] = round(ob_pts,1)

            # MTF 4h
            mtf_label="?/3"; mtf_score=0; mtf_checks=0
            try:
                if USE_HTF:
                    kl4h = await client.get_klines(symbol, TREND_INTERVAL, limit=50)
                    if len(kl4h) >= 30:
                        c4h=np.array([float(k["close"])  for k in kl4h])
                        h4h=np.array([float(k["high"])   for k in kl4h])
                        l4h=np.array([float(k["low"])    for k in kl4h])
                        v4h=np.array([float(k["volume"]) for k in kl4h])
                        o4h=np.array([float(k["open"])   for k in kl4h])
                        es4=ema(c4h,21); rs4=rsi(c4h,14); _,cp4,cu4=cvd(c4h,o4h,h4h,l4h,v4h)
                        if not np.isnan(es4[-1]):
                            if direction=="LONG"  and c4h[-1]>es4[-1]: mtf_checks+=1
                            elif direction=="SHORT" and c4h[-1]<es4[-1]: mtf_checks+=1
                        rv=float(rs4[-1]) if not np.isnan(rs4[-1]) else 50.
                        if direction=="LONG" and rv<65: mtf_checks+=1
                        elif direction=="SHORT" and rv>35: mtf_checks+=1
                        if direction=="LONG" and cu4: mtf_checks+=1
                        elif direction=="SHORT" and not cu4: mtf_checks+=1
                        mtf_label=f"{mtf_checks}/3"
                        mtf_score=mtf_checks*3
            except Exception: mtf_label="?/3"
            score += mtf_score;  sd["mtf"] = mtf_label

            funding=0.
            try:
                funding=await client.get_funding_rate(symbol)
                fp = 5. if (direction=="LONG" and -0.001<funding<0.002) or \
                           (direction=="SHORT" and funding>0.002) else \
                     (3. if direction=="LONG" and funding<-0.001 else 0.)
                score+=fp;  sd["fund"]=round(fp,1)
            except Exception: pass

            score = round(score, 1)
            log.debug(f"{symbol} {direction} score={score} detail={sd}")

            if score < MIN_SCORE:
                log.debug(f"↷ {symbol}: score={score} < {MIN_SCORE}")
                return None

            # Proyección PnL
            hist = lc if direction=="LONG" else sc_
            proj_map = project_pnl(closes, hist[:-w], PROJ_LEN)
            if not proj_map:
                if score < 55: return None
                proj = {"worst":-0.01,"best":0.03,"p25":0,"p75":0.02,
                        "mean":0.015,"median":0.01,"count":0}
            else:
                proj = proj_map[max(proj_map.keys())]
                exp_sign = 1 if direction=="LONG" else -1
                if proj["count"] < MIN_SIGNALS_HIST and score < 55: return None
                if proj["mean"]*exp_sign < MIN_MEAN_PNL and score < 55: return None

            # TP / SL
            if ob_snap:
                from order_book import OrderBookAnalyzer as OBA
                tp_price = OBA.suggest_tp(ob_snap, direction, price, current_atr, TP_RR)
                sl_price = OBA.suggest_sl(ob_snap, direction, price, current_atr)
            else:
                if direction == "LONG":
                    tp_price = round(price+current_atr*TP_RR*1.5, 8)
                    sl_price = round(price-current_atr*SL_ATR_MULT, 8)
                else:
                    tp_price = round(price-current_atr*TP_RR*1.5, 8)
                    sl_price = round(price+current_atr*SL_ATR_MULT, 8)

            risk=abs(price-sl_price); reward=abs(tp_price-price)
            rr  = round(reward/risk,2) if risk>0 else 0

            log.info(
                f"SENAL [{score:.0f}] {symbol} {direction} "
                f"CVD={cvd_pct:.0f}% BB_sq={is_squeeze} MTF={mtf_label} "
                f"ADX={adx_val:.0f} OB={ob_bias} R:R={rr} n={len(active_sigs)}"
            )

            return {
                "symbol":symbol,"direction":direction,"signal_type":signal_type,
                "price":price,"interval":KLINE_INTERVAL,"score":score,"score_detail":sd,
                "active_sigs":active_sigs,
                "mean_pnl":proj.get("mean",0.01),"median_pnl":proj.get("median",0.01),
                "worst_pnl":proj.get("worst",-0.01),"best_pnl":proj.get("best",0.03),
                "p25_pnl":proj.get("p25",0),"p75_pnl":proj.get("p75",0),
                "signal_count":proj.get("count",0),"projections":proj_map,
                "tp_price":tp_price,"sl_price":sl_price,"risk_reward":rr,
                "atr":round(current_atr,8),"adx":round(adx_val,1),"rsi":round(current_rsi,1),
                "cvd_pct":cvd_pct,"cvd_up":cvd_up,"bb_squeeze":is_squeeze,
                "bb_width":round(bw_now,2),"bb_expansion":round(bb_exp,1),
                "mtf_label":mtf_label,"ob_bias":ob_bias,"ob_imbalance":ob_imb,
                "ob_delta":ob_delta,"ob_absorption":ob_absorb,
                "funding":round(funding*100,5),"timestamp":klines[-1]["time"],
            }
        except Exception as e:
            log.warning(f"Error {symbol}: {e}")
            return None
