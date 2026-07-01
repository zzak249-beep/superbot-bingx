"""
Mean Reversion Strategy — Bollinger + RSI + ADX + Funding Rate
Mercados LATERALES con sesgo SHORT (funding positivo = cobras por aguantar el short)
"""
import math, logging
log = logging.getLogger("strategy_mr")

def _rsi(closes, p=14):
    if len(closes) < p + 1: return 50.0
    gains = [max(closes[i]-closes[i-1],0) for i in range(1,len(closes))]
    losses= [max(closes[i-1]-closes[i],0) for i in range(1,len(closes))]
    ag = sum(gains[-p:])/p; al = sum(losses[-p:])/p
    return 100.0 if al==0 else 100 - 100/(1+ag/al)

def _atr(candles, p=14):
    trs = [max(c["high"]-c["low"],abs(c["high"]-candles[i-1]["close"]),abs(c["low"]-candles[i-1]["close"]))
           for i,c in enumerate(candles) if i>0]
    if not trs: return 0.0
    a = trs[0]
    for t in trs[1:]: a = t/p + a*(1-1/p)
    return a

def _bollinger(closes, p=20, s=2.0):
    if len(closes)<p: return None,None,None
    sub = closes[-p:]; ma = sum(sub)/p
    std = math.sqrt(sum((c-ma)**2 for c in sub)/p)
    return ma+s*std, ma, ma-s*std

def _adx(candles, p=14):
    if len(candles)<p+2: return 50.0
    pdm,mdm,trl=[],[],[]
    for i in range(1,len(candles)):
        h,l=candles[i]["high"],candles[i]["low"]
        ph,pl,pc=candles[i-1]["high"],candles[i-1]["low"],candles[i-1]["close"]
        up,dn=h-ph,pl-l
        pdm.append(up if up>dn and up>0 else 0)
        mdm.append(dn if dn>up and dn>0 else 0)
        trl.append(max(h-l,abs(h-pc),abs(l-pc)))
    def sm(lst):
        s=sum(lst[:p])
        out=[s]
        for v in lst[p:]: s=s-s/p+v; out.append(s)
        return out
    st,sp,sm2=sm(trl),sm(pdm),sm(mdm)
    dx=[]
    for s,pp,mm in zip(st,sp,sm2):
        if s==0: continue
        pdi,mdi=100*pp/s,100*mm/s
        d=pdi+mdi
        if d: dx.append(100*abs(pdi-mdi)/d)
    return sum(dx[-p:])/min(len(dx),p) if dx else 50.0

def get_signal(candles: list, funding_rate: float, config) -> dict:
    result={"signal":None,"rsi":50,"adx":50,"bb_upper":0,"bb_mid":0,"bb_lower":0,"atr":0,"funding":funding_rate}
    if len(candles)<30: return result
    closes=[c["close"] for c in candles]
    rsi=_rsi(closes,14); adx=_adx(candles,14); atr=_atr(candles,14)
    bbu,bbm,bbl=_bollinger(closes,20,2.0)
    result.update({"rsi":rsi,"adx":adx,"atr":atr,"bb_upper":bbu or 0,"bb_mid":bbm or 0,"bb_lower":bbl or 0})
    if bbu is None or atr==0: return result
    is_lateral = adx < getattr(config,"MR_ADX_MAX",25)
    cur_h,cur_l,cur_c = candles[-1]["high"],candles[-1]["low"],closes[-1]
    short_ok = (is_lateral and cur_h>=bbu and cur_c<bbu
                and rsi>=getattr(config,"MR_RSI_SHORT",68)
                and funding_rate>=getattr(config,"MR_FUNDING_MIN_SHORT",0.0))
    long_ok  = (is_lateral and cur_l<=bbl and cur_c>bbl
                and rsi<=getattr(config,"MR_RSI_LONG",32))
    if short_ok:   result["signal"]="SHORT"
    elif long_ok:  result["signal"]="LONG"
    return result

def check_bb_mid_exit(candles: list, side: str) -> bool:
    if len(candles)<22: return False
    closes=[c["close"] for c in candles]
    _,bbm,_=_bollinger(closes,20,2.0)
    if bbm is None: return False
    last=closes[-2]
    return (side=="SHORT" and last>=bbm) or (side=="LONG" and last<=bbm)
