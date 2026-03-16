"""
analizar_smc.py — Motor de señales SMC Sniper Bot
==================================================
4 condiciones — todas deben cumplirse:

  1. LIQUIDEZ (identityKa + QML Malibu)
     — Pivot swept (SSL/BSL) + estructura QML confirmada

  2. VOLUMEN + STOP CLUSTERS (Kioseff)
     — RVOL >= VOL_MULT + presión de stops

  3. MOMENTUM (SMC Sniper v6)
     — Supertrend + EMA momentum alineado

  4. DELTA STRIKE 量能猎杀 (KodaTao)
     — Agotamiento de vendedores/compradores (proxy Footprint)
     — RVOL spike multi-periodo (corto/medio/largo)
     — RSI en zona correcta (≤40 long / ≥60 short)
     — Confirmación: vela opuesta + delta cambia dirección
"""

import logging
import os
import time
from datetime import datetime, timezone
import concurrent.futures

import config_smc as cfg
import exchange

log = logging.getLogger("analizar_smc")

_cooldown_ts:   dict = {}
_niveles_cache: dict = {}
_CACHE_TTL = 600


# ══════════════════════════════════════════════════════
# INDICADORES BASE
# ══════════════════════════════════════════════════════

def _ema(prices, period):
    if len(prices) < period: return None
    k = 2 / (period + 1)
    v = sum(prices[:period]) / period
    for p in prices[period:]: v = p*k + v*(1-k)
    return v

def _sma(prices, period):
    if len(prices) < period: return None
    return sum(prices[-period:]) / period

def _atr(hi, lo, cl, period=14):
    if len(hi) < period+1: return 0.0
    trs = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
           for i in range(1, len(hi))]
    return sum(trs[-period:]) / period

def _rsi(prices, period=14):
    if len(prices) < period+1: return 50.0
    d  = [prices[i]-prices[i-1] for i in range(1, len(prices))]
    ag = sum(max(x,0)      for x in d[:period]) / period
    al = sum(abs(min(x,0)) for x in d[:period]) / period
    for x in d[period:]:
        ag = (ag*(period-1)+max(x,0))/period
        al = (al*(period-1)+abs(min(x,0)))/period
    return 100.0 if al==0 else round(100-100/(1+ag/al), 2)

def _pivot_high(candles, length):
    """ta.pivothigh replica: máximo local centrado"""
    if len(candles) < 2*length+1: return None
    idx = len(candles) - length - 1
    window = candles[idx-length:idx+length+1]
    center = candles[idx]["high"]
    if all(c["high"] <= center for i, c in enumerate(window) if i != length):
        return center
    return None

def _pivot_low(candles, length):
    """ta.pivotlow replica: mínimo local centrado"""
    if len(candles) < 2*length+1: return None
    idx = len(candles) - length - 1
    window = candles[idx-length:idx+length+1]
    center = candles[idx]["low"]
    if all(c["low"] >= center for i, c in enumerate(window) if i != length):
        return center
    return None


# ══════════════════════════════════════════════════════
# CONDICIÓN 1A — LIQUIDITY MAGNET ZONES [identityKa]
# Detecta pivots BSL/SSL y si han sido barridos
# ══════════════════════════════════════════════════════

def liquidity_magnet(candles: list) -> dict:
    """
    Replica identityKa Liquidity Magnet:
    - Detecta pivot highs (BSL) y pivot lows (SSL)
    - Si precio barre SSL desde abajo → LONG bias
    - Si precio barre BSL desde arriba → SHORT bias
    - is_sweeping = acaba de ocurrir este ciclo
    """
    pl = cfg.PIVOT_LEN
    result = {
        "ssl_swept": False, "bsl_swept": False,
        "last_sweep": 0,    # 1=SSL swept(bull), -1=BSL swept(bear)
        "ssl_level": 0.0,   "bsl_level": 0.0,
        "is_sweeping": False,
    }

    if len(candles) < pl*2+5:
        return result

    # Recopilar pivots en ventana reciente
    bsl_levels = []  # pivot highs activos
    ssl_levels = []  # pivot lows activos

    lb = min(100, len(candles)-pl-1)
    for i in range(lb, pl, -1):
        window = candles[-(i+pl+1):-(i-pl) if i > pl else None]
        if len(window) < 2*pl+1: continue
        center_h = window[pl]["high"]
        center_l = window[pl]["low"]
        if all(c["high"] <= center_h for j,c in enumerate(window) if j!=pl):
            bsl_levels.append(center_h)
        if all(c["low"] >= center_l for j,c in enumerate(window) if j!=pl):
            ssl_levels.append(center_l)

    if not bsl_levels and not ssl_levels:
        return result

    c   = candles[-1]
    c_p = candles[-2]

    # Sweeps en vela actual
    for lvl in ssl_levels[-3:]:  # últimos 3 pivots
        if c["low"] <= lvl:      # precio barró SSL
            result["ssl_swept"]   = True
            result["last_sweep"]  = 1   # bull bias
            result["ssl_level"]   = lvl
            result["is_sweeping"] = True
            break

    for lvl in bsl_levels[-3:]:
        if c["high"] >= lvl:     # precio barró BSL
            result["bsl_swept"]   = True
            result["last_sweep"]  = -1  # bear bias
            result["bsl_level"]   = lvl
            result["is_sweeping"] = True
            break

    # Si no hay sweep activo, usar el último bias conocido
    if not result["is_sweeping"]:
        # Verificar sweeps en velas recientes (últimas 5)
        for k in range(2, min(6, len(candles))):
            ck = candles[-k]
            for lvl in ssl_levels[-3:]:
                if ck["low"] <= lvl:
                    result["last_sweep"] = 1
                    result["ssl_level"]  = lvl
                    break
            for lvl in bsl_levels[-3:]:
                if ck["high"] >= lvl:
                    result["last_sweep"] = -1
                    result["bsl_level"]  = lvl
                    break
            if result["last_sweep"] != 0:
                break

    if bsl_levels: result["bsl_level"] = bsl_levels[-1]
    if ssl_levels: result["ssl_level"] = ssl_levels[-1]

    return result


# ══════════════════════════════════════════════════════
# CONDICIÓN 1B — QML FTB [Malibu]
# Quasimodo Structure: H2>H1<H0>H1, cierra sobre shoulder
# ══════════════════════════════════════════════════════

def qml_structure(candles: list, atr: float) -> dict:
    """
    Replica QML FTB [Malibu]:
    Bull QML: trend=-1, H2>H1 y L1>L0 y H0>H1 y close>L1
    Bear QML: trend=1,  L2<L1 y H1<H0 y L0<L1 y close<H1

    Implementación simplificada con zigzag de pivots locales.
    """
    result = {"bull": False, "bear": False, "level": 0.0, "quality": 0}

    if len(candles) < 40 or atr <= 0:
        return result

    # Encontrar swing highs y lows con zigzag básico
    zlen = cfg.QML_ZIGZAG
    highs_sw = []   # [(precio, idx)]
    lows_sw  = []

    lb = min(zlen*8, len(candles)-1)
    for i in range(lb, zlen, -zlen):
        window = candles[-i-zlen:-i+zlen+1] if i > zlen else candles[-i-zlen:]
        if len(window) < 2*zlen+1: continue
        cp = candles[-i]
        if all(c["high"] <= cp["high"] for c in window): highs_sw.append((cp["high"], -i))
        if all(c["low"]  >= cp["low"]  for c in window): lows_sw .append((cp["low"],  -i))

    if len(highs_sw) < 3 or len(lows_sw) < 3:
        return result

    # Últimos 3 highs y lows
    h = [x[0] for x in highs_sw[-3:]]   # h[0]=más viejo, h[2]=más reciente
    l = [x[0] for x in lows_sw [-3:]]

    if len(h) < 3 or len(l) < 3:
        return result

    min_struct = atr * cfg.QML_MIN_ATR

    # Bull QML: H2>H1, L1>L0 (head más bajo), H0>H1 (left shoulder menor)
    # Precio cierra sobre el shoulder izquierdo (l[2])
    c = candles[-1]
    if (h[1] > h[0] and                       # H1 > H0 (head)
        l[2] > l[1] and                       # L1 > L0 (low más alto)
        h[2] > h[1] and                       # H2 > H1 (right shoulder rompe)
        c["close"] > l[2] and                 # cierra sobre shoulder
        abs(h[1]-l[1]) >= min_struct):        # estructura mínima
        result["bull"]    = True
        result["level"]   = l[2]              # nivel de entrada = shoulder
        result["quality"] = 75

    # Bear QML
    if (l[1] < l[0] and
        h[2] < h[1] and
        l[2] < l[1] and
        c["close"] < h[2] and
        abs(h[1]-l[1]) >= min_struct):
        result["bear"]    = True
        result["level"]   = h[2]
        result["quality"] = 75

    return result


# ══════════════════════════════════════════════════════
# CONDICIÓN 2 — VOLUMEN + STOP CLUSTERS [Kioseff]
# Replica: volumen > media × mult + detección de stops
# ══════════════════════════════════════════════════════

def stop_cluster_pressure(candles: list, lado: str) -> dict:
    """
    Replica simplificada del Stop Loss Clustering [Kioseff]:
    - RVOL: volumen actual vs media
    - Presión de stops: suma de volumen de velas que barren niveles
      en la dirección correcta (proxy de stops activados)
    - Si precio barre un mínimo/máximo con alto volumen → stops activados
    """
    vols = [c["volume"] for c in candles]
    lb   = cfg.VOL_LOOKBACK

    if len(vols) < lb+2:
        return {"ok": False, "rvol": 0.0, "stop_pressure": 0.0}

    avg_vol = _sma(vols[:-1], lb) or 1
    rvol    = vols[-1] / avg_vol

    # Detectar "stop clusters": velas que rompen swings locales con volumen alto
    # Proxy: en las últimas 20 velas, contar cuántas rompieron un mínimo/máximo
    # local con volumen > media
    stop_pressure = 0.0
    c_lb = min(cfg.CLUSTER_BARS, len(candles)-1)

    for i in range(1, c_lb):
        ci      = candles[-i]
        ci_prev = candles[-i-1]
        v_ratio = ci["volume"] / (avg_vol or 1)

        if lado == "LONG":
            # Stops de longs: velas que rompieron mínimos con vol alto (ya activados)
            if ci["low"] < ci_prev["low"] and v_ratio > 1.2:
                stop_pressure += v_ratio
        else:
            # Stops de shorts: velas que rompieron máximos con vol alto
            if ci["high"] > ci_prev["high"] and v_ratio > 1.2:
                stop_pressure += v_ratio

    return {
        "ok":            rvol >= cfg.VOL_MULT,
        "rvol":          round(rvol, 2),
        "stop_pressure": round(stop_pressure, 2),
    }


# ══════════════════════════════════════════════════════
# CONDICIÓN 3 — MOMENTUM [SMC Sniper v6]
# Supertrend(2.5, 10) + EMA momentum
# ══════════════════════════════════════════════════════

def momentum_check(hi, lo, cl, lado: str) -> dict:
    """
    Replica SMC Sniper v6:
    - Supertrend(factor=2.5, atr=10): dirección principal
    - EMA50: filtro de tendencia
    - ADX proxy: rango vs ATR para verificar trending
    """
    # Supertrend
    factor = 2.5; period = 10
    st_bull = st_bear = False

    if len(cl) >= period+2:
        atrs = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
                for i in range(1, len(cl))]
        av = sum(atrs[:period]) / period
        atr_s = [av]
        for a in atrs[period:]:
            av = (av*(period-1)+a)/period; atr_s.append(av)
        n = len(atr_s); off = len(cl)-n
        ub=[0.]*n; lb_=[0.]*n; dr=[1]*n; st=[0.]*n
        for i in range(n):
            ci=i+off; h2=(hi[ci]+lo[ci])/2
            u=h2+factor*atr_s[i]; l=h2-factor*atr_s[i]
            ub[i]=min(u,ub[i-1]) if i>0 and cl[ci-1]<ub[i-1] else u
            lb_[i]=max(l,lb_[i-1]) if i>0 and cl[ci-1]>lb_[i-1] else l
            if i==0: dr[i]=1
            elif st[i-1]==ub[i-1]: dr[i]=1 if cl[ci]<ub[i] else -1
            else: dr[i]=-1 if cl[ci]>lb_[i] else 1
            st[i]=ub[i] if dr[i]==1 else lb_[i]
        st_bull = dr[-1] < 0
        st_bear = dr[-1] > 0

    # EMA50
    ema50 = _ema(cl, 50)
    ema_bull = ema50 and cl[-1] > ema50
    ema_bear = ema50 and cl[-1] < ema50

    # Trending check (ADX proxy: rango reciente vs ATR)
    atr14  = _atr(hi, lo, cl, 14)
    rng20  = (max(hi[-20:]) - min(lo[-20:])) if len(hi) >= 20 else atr14
    trending = rng20 > atr14 * 3

    if lado == "LONG":
        ok = (st_bull and ema_bull) or (st_bull and trending) or (ema_bull and trending)
    else:
        ok = (st_bear and ema_bear) or (st_bear and trending) or (ema_bear and trending)

    return {
        "ok":       ok,
        "st_bull":  st_bull,
        "st_bear":  st_bear,
        "ema_bull": ema_bull,
        "ema_bear": ema_bear,
        "trending": trending,
    }


# ══════════════════════════════════════════════════════
# CONDICIÓN 4 — DELTA STRIKE [KodaTao 量能猎杀]
#
# Replica la lógica del indicador sin Footprint API:
#
# BASE SETUP BOTTOM (LONG):
#   - RVOL spike en al menos 1 periodo (short/mid/long)
#   - Delta negativo (vendedores dominan) = close < open
#   - Cierre en zona alta de la vela (> 20% del rango)
#   - RSI <= RSI_OS (sobreventa)
#
# FOOTPRINT EXHAUSTION proxy (sin API):
#   - Wick inferior > cuerpo × ratio (vendedores rechazados)
#   - Volumen de la vela > avg_vol × lose_balance_ratio
#
# CONFIRMACIÓN (vela siguiente):
#   - Cierra alcista + por encima del cierre de la vela base
#   - Delta cambia a positivo (close >= open en vela actual)
#
# Lo mismo inverso para tops (SHORT).
# ══════════════════════════════════════════════════════

def delta_strike(candles: list) -> dict:
    """
    Replica Delta Strike 量能猎杀指标 [KodaTao]
    sin acceso a Footprint API (usa proxy de wicks + volumen).

    Retorna:
      bottom_setup:   bool — setup de agotamiento bajista detectado
      top_setup:      bool — setup de agotamiento alcista detectado
      confirm_bottom: bool — confirmación LONG (vela siguiente alcista + delta +)
      confirm_top:    bool — confirmación SHORT (vela siguiente bajista + delta -)
      st_buy:         bool — Supertrend flip alcista + delta positivo
      st_sell:        bool — Supertrend flip bajista + delta negativo
      spike_score:    int  — 0-9 (cuántos RVOL superan los 3 umbrales × 3 periodos)
      rsi:            float
    """
    # Parámetros (configurables en Railway)
    vol_s  = int(os.getenv("DS_VOL_SHORT",  "20"))
    vol_m  = int(os.getenv("DS_VOL_MID",    "60"))
    vol_l  = int(os.getenv("DS_VOL_LONG",  "180"))
    r_s    = float(os.getenv("DS_RATIO_SHORT", "1.5"))
    r_m    = float(os.getenv("DS_RATIO_MID",   "3.0"))
    r_l    = float(os.getenv("DS_RATIO_LONG",  "5.0"))
    rsi_ob = float(os.getenv("DS_RSI_OB", "60"))
    rsi_os = float(os.getenv("DS_RSI_OS", "40"))
    imb_r  = float(os.getenv("DS_IMB_RATIO", "2.0"))  # lose_balance_ratio proxy
    n_conf = int(os.getenv("DS_N_CONFIRM",   "5"))    # velas de espera máx

    base = {
        "bottom_setup": False, "top_setup": False,
        "confirm_bottom": False, "confirm_top": False,
        "st_buy": False, "st_sell": False,
        "spike_score": 0, "rsi": 50.0,
        "setup_offset_b": 0, "setup_offset_t": 0,
    }

    need = max(vol_l, 200) + n_conf + 2
    if len(candles) < need:
        return base

    vols  = [c["volume"] for c in candles]
    cl    = [c["close"]  for c in candles]
    op    = [c["open"]   for c in candles]
    hi    = [c["high"]   for c in candles]
    lo    = [c["low"]    for c in candles]

    # ── RVOL series ────────────────────────────────────
    def rvol_at(i, period):
        avg = _sma(vols[max(0,i-period):i], period)
        return vols[i] / avg if avg and avg > 0 else 0.0

    # ── Delta proxy: close >= open → alcista, else bajista ─
    def delta_at(i):
        return 1 if cl[i] >= op[i] else -1

    # ── RSI ────────────────────────────────────────────
    rsi_now = _rsi(cl[-50:])
    base["rsi"] = rsi_now

    # ── Supertrend ─────────────────────────────────────
    st_bull_now, st_bear_now = False, False
    if len(cl) >= 14:
        factor = 3.0; period = 14
        atrs_s = [max(hi[i]-lo[i],abs(hi[i]-cl[i-1]),abs(lo[i]-cl[i-1]))
                  for i in range(1, len(cl))]
        av = sum(atrs_s[:period]) / period
        atr_s2 = [av]
        for a in atrs_s[period:]: av=(av*(period-1)+a)/period; atr_s2.append(av)
        n2=len(atr_s2); off2=len(cl)-n2
        ub2=[0.]*n2; lb2=[0.]*n2; dr2=[1]*n2; st2=[0.]*n2
        for i in range(n2):
            ci=i+off2; h2=(hi[ci]+lo[ci])/2
            u=h2+factor*atr_s2[i]; l=h2-factor*atr_s2[i]
            ub2[i]=min(u,ub2[i-1]) if i>0 and cl[ci-1]<ub2[i-1] else u
            lb2[i]=max(l,lb2[i-1]) if i>0 and cl[ci-1]>lb2[i-1] else l
            if i==0: dr2[i]=1
            elif st2[i-1]==ub2[i-1]: dr2[i]=1 if cl[ci]<ub2[i] else -1
            else: dr2[i]=-1 if cl[ci]>lb2[i] else 1
            st2[i]=ub2[i] if dr2[i]==1 else lb2[i]

        # Supertrend flip
        st_flip_bull = (dr2[-1] < 0 and dr2[-2] > 0) if len(dr2) >= 2 else False
        st_flip_bear = (dr2[-1] > 0 and dr2[-2] < 0) if len(dr2) >= 2 else False

        # valid_st_buy: flip alcista + delta positivo esta vela
        base["st_buy"]  = st_flip_bull and delta_at(-1) > 0
        base["st_sell"] = st_flip_bear and delta_at(-1) < 0

    # ── Buscar bottom/top setups en historial ──────────
    last_i = len(candles) - 1

    def is_vol_spike(i):
        rs = rvol_at(i, vol_s); rm = rvol_at(i, vol_m); rl = rvol_at(i, vol_l)
        return (rs >= r_s or rm >= r_s or rl >= r_s), rs, rm, rl

    def spike_score(rs, rm, rl):
        s = 0
        for ratio, thresh in [(rs,r_s),(rm,r_s),(rl,r_s),(rs,r_m),(rm,r_m),(rl,r_m),(rs,r_l),(rm,r_l),(rl,r_l)]:
            if ratio >= thresh: s += 1
        return s

    def footprint_exhaustion_bottom(i):
        """
        Proxy para fp_bottom_exhaustion:
        - Wick inferior > cuerpo × imb_r  (vendedores rechazados = absorción)
        - Volumen > avg × imb_r
        """
        body   = abs(cl[i] - op[i])
        lw     = min(cl[i], op[i]) - lo[i]   # wick inferior
        avg_v  = _sma(vols[max(0,i-20):i], 20) or 1
        return lw > body * imb_r and vols[i] > avg_v * imb_r

    def footprint_exhaustion_top(i):
        body   = abs(cl[i] - op[i])
        uw     = hi[i] - max(cl[i], op[i])   # wick superior
        avg_v  = _sma(vols[max(0,i-20):i], 20) or 1
        return uw > body * imb_r and vols[i] > avg_v * imb_r

    # Escanear las últimas n_conf+1 velas para setups
    for j in range(1, min(n_conf + 1, last_i)):
        idx = last_i - j    # índice de la vela "base"

        spike, rs, rm, rl = is_vol_spike(idx)
        if not spike:
            continue

        # BOTTOM SETUP
        rng = hi[idx] - lo[idx]
        if (delta_at(idx) < 0 and                          # delta negativo
            rng > 0 and
            cl[idx] > lo[idx] + rng * 0.2 and             # cierre en zona alta
            _rsi(cl[max(0,idx-14):idx+1]) <= rsi_os and   # RSI sobreventa
            footprint_exhaustion_bottom(idx)):              # absorción

            base["bottom_setup"]   = True
            base["setup_offset_b"] = j
            base["spike_score"]    = spike_score(rs, rm, rl)

            # CONFIRMACIÓN: vela actual alcista + cierre sobre base + delta +
            if (cl[-1] > op[-1] and
                cl[-1] > cl[idx] and
                delta_at(-1) > 0):
                base["confirm_bottom"] = True
            break

        # TOP SETUP
        if (delta_at(idx) > 0 and                          # delta positivo
            rng > 0 and
            cl[idx] < hi[idx] - rng * 0.2 and             # cierre en zona baja
            _rsi(cl[max(0,idx-14):idx+1]) >= rsi_ob and   # RSI sobrecompra
            footprint_exhaustion_top(idx)):                 # absorción

            base["top_setup"]      = True
            base["setup_offset_t"] = j
            base["spike_score"]    = spike_score(rs, rm, rl)

            # CONFIRMACIÓN: vela actual bajista + cierre bajo base + delta -
            if (cl[-1] < op[-1] and
                cl[-1] < cl[idx] and
                delta_at(-1) < 0):
                base["confirm_top"] = True
            break

    return base
# ══════════════════════════════════════════════════════

def en_killzone() -> dict:
    m    = datetime.now(timezone.utc)
    mins = m.hour * 60 + m.minute
    london = cfg.KZ_LONDON_START <= mins < cfg.KZ_LONDON_END
    ny     = cfg.KZ_NY_START     <= mins < cfg.KZ_NY_END
    return {
        "in_kz":  london or ny,
        "nombre": "LONDON" if london else ("NY" if ny else "FUERA"),
    }

def _cooldown_ok(par): return (time.time() - _cooldown_ts.get(par, 0)) >= cfg.COOLDOWN_VELAS * 300
def registrar_senal_ts(par): _cooldown_ts[par] = time.time()
def registrar_trade_kz(kz, ganado): pass
def actualizar_macro_btc(): pass
def invalidar_niveles(par): _niveles_cache.pop(par, None)


# ══════════════════════════════════════════════════════
# SL / TP INTELIGENTE
# ══════════════════════════════════════════════════════

def calcular_sl(candles, lado, atr, precio, liq_level=0):
    rec = candles[-16:-1]
    buf = atr * 0.25

    if lado == "LONG":
        # SL bajo el nivel de liquidez barrido (SSL) o swing low
        sl_liq = liq_level - buf if liq_level > 0 and liq_level < precio else 0
        sl_sw  = min(c["low"] for c in rec) - buf if rec else 0
        sl_atr = precio - atr * cfg.SL_ATR_MULT
        opts   = [x for x in [sl_liq, sl_sw] if 0 < x < precio]
        sl     = max(opts) if opts else sl_atr
        if precio - sl > 3 * atr: sl = sl_atr
    else:
        sl_liq = liq_level + buf if liq_level > precio else 0
        sl_sw  = max(c["high"] for c in rec) + buf if rec else 0
        sl_atr = precio + atr * cfg.SL_ATR_MULT
        opts   = [x for x in [sl_liq, sl_sw] if x > precio]
        sl     = min(opts) if opts else sl_atr
        if sl - precio > 3 * atr: sl = sl_atr

    return sl


# ══════════════════════════════════════════════════════
# ANÁLISIS PRINCIPAL
# ══════════════════════════════════════════════════════

def analizar_par(par: str):
    try:
        if not _cooldown_ok(par):
            return None

        candles = exchange.get_candles(par, cfg.TIMEFRAME, cfg.CANDLES_LIMIT)
        if len(candles) < 60:
            return None

        cl     = [c["close"] for c in candles]
        hi     = [c["high"]  for c in candles]
        lo     = [c["low"]   for c in candles]
        precio = cl[-1]
        if precio <= 0: return None

        atr = _atr(hi, lo, cl)
        if atr <= 0: return None

        # ── C1: LIQUIDEZ (identityKa + QML) ─────────────────
        liq  = liquidity_magnet(candles)
        qml  = qml_structure(candles, atr)

        # Determinar lado desde liquidez
        lado = None
        liq_level = 0.0

        if liq["is_sweeping"]:
            if liq["ssl_swept"] and liq["last_sweep"] == 1:
                lado = "LONG"; liq_level = liq["ssl_level"]
            elif liq["bsl_swept"] and liq["last_sweep"] == -1:
                lado = "SHORT"; liq_level = liq["bsl_level"]
        elif liq["last_sweep"] != 0:
            # Sweep reciente + QML confirma
            if liq["last_sweep"] == 1 and qml["bull"]:
                lado = "LONG"; liq_level = liq["ssl_level"]
            elif liq["last_sweep"] == -1 and qml["bear"]:
                lado = "SHORT"; liq_level = liq["bsl_level"]

        if not lado:
            return None

        if lado == "SHORT" and cfg.SOLO_LONG:
            return None

        # ── C2: VOLUMEN + STOP CLUSTERS ─────────────────────
        vol = stop_cluster_pressure(candles, lado)
        if not vol["ok"]:
            log.info(f"[SKIP-VOL] {par} {lado} RVOL={vol['rvol']:.2f}")
            return None

        # ── C3: MOMENTUM (Supertrend + EMA) ─────────────────
        mom = momentum_check(hi, lo, cl, lado)
        if not mom["ok"]:
            log.info(
                f"[SKIP-MOM] {par} {lado} "
                f"ST_bull={mom['st_bull']} ST_bear={mom['st_bear']} "
                f"ema_bull={mom['ema_bull']} ema_bear={mom['ema_bear']}"
            )
            return None

        # ── C4: DELTA STRIKE (量能猎杀) ──────────────────────
        ds = delta_strike(candles)

        # Delta Strike puede CONFIRMAR (máxima fuerza) o PERMITIR (neutral)
        # — Si hay confirmación Delta Strike en la dirección correcta: +3 score
        # — Si hay ST flip Delta Strike: señal independiente válida también
        # — Si Delta Strike dice lo CONTRARIO: BLOQUEAR la señal
        ds_long_ok  = ds["confirm_bottom"] or ds["st_buy"]  or (not ds["confirm_top"]  and not ds["top_setup"])
        ds_short_ok = ds["confirm_top"]    or ds["st_sell"] or (not ds["confirm_bottom"] and not ds["bottom_setup"])

        if lado == "LONG"  and not ds_long_ok:
            log.info(f"[SKIP-DS] {par} LONG bloqueado por Delta Strike (top setup activo)")
            return None
        if lado == "SHORT" and not ds_short_ok:
            log.info(f"[SKIP-DS] {par} SHORT bloqueado por Delta Strike (bottom setup activo)")
            return None

        # ── SL / TP ──────────────────────────────────────────
        sl   = calcular_sl(candles, lado, atr, precio, liq_level)
        dist = abs(precio - sl)
        if dist <= 0: return None

        tp   = (precio + dist * cfg.TP_DIST_MULT)  if lado=="LONG" else (precio - dist * cfg.TP_DIST_MULT)
        tp1  = (precio + dist * cfg.TP1_DIST_MULT) if lado=="LONG" else (precio - dist * cfg.TP1_DIST_MULT)
        rr   = abs(tp - precio) / dist
        if rr < cfg.MIN_RR: return None

        # ── SCORE ────────────────────────────────────────────
        kz    = en_killzone()
        score = 3   # base: las 3 condiciones (+ DS no bloquea)
        if liq["is_sweeping"]:         score += 2  # sweep activo
        if qml["bull"] or qml["bear"]: score += 2  # QML confirma
        if vol["rvol"] >= 2.0:         score += 1
        if vol["stop_pressure"] > 3:   score += 1
        if mom["st_bull"] and lado=="LONG":  score += 1
        if mom["st_bear"] and lado=="SHORT": score += 1
        # Delta Strike bonus
        if ds["confirm_bottom"] and lado=="LONG":  score += 3  # máxima confirmación
        if ds["confirm_top"]    and lado=="SHORT": score += 3
        if ds["st_buy"]  and lado=="LONG":  score += 2  # ST flip + delta
        if ds["st_sell"] and lado=="SHORT": score += 2
        if ds["spike_score"] >= 6:     score += 1   # spike muy fuerte
        if kz["in_kz"]:                score += 1

        tipo = "SWEEP" if liq["is_sweeping"] else "QML"
        motivos = [
            f"{tipo}_{'SSL' if lado=='LONG' else 'BSL'}",
            f"RVOL×{vol['rvol']:.1f}",
            f"ST_{'BULL' if mom['st_bull'] else 'BEAR'}",
        ]
        if ds["confirm_bottom"] and lado=="LONG":  motivos.append(f"DS_底部吸收({ds['spike_score']}/9)")
        if ds["confirm_top"]    and lado=="SHORT": motivos.append(f"DS_顶部吸收({ds['spike_score']}/9)")
        if ds["st_buy"]  and lado=="LONG":  motivos.append("DS_ST突破")
        if ds["st_sell"] and lado=="SHORT": motivos.append("DS_ST跌破")
        if qml["bull"] or qml["bear"]: motivos.append("QML")
        if kz["in_kz"]: motivos.append(f"KZ_{kz['nombre']}")

        registrar_senal_ts(par)

        ds_tag = ""
        if ds["confirm_bottom"] and lado=="LONG":  ds_tag = f" DS底部({ds['spike_score']}/9)"
        if ds["confirm_top"]    and lado=="SHORT": ds_tag = f" DS顶部({ds['spike_score']}/9)"
        if ds["st_buy"]  and lado=="LONG":  ds_tag = " DS_ST↑"
        if ds["st_sell"] and lado=="SHORT": ds_tag = " DS_ST↓"

        log.info(
            f"[SEÑAL] {lado:5s} {par:15s} {tipo:6s} "
            f"LIQ={liq_level:.6f} RVOL×{vol['rvol']:.1f} "
            f"ST={'B' if mom['st_bull'] else ('S' if mom['st_bear'] else '-')}"
            f"{ds_tag} score={score} SL={sl:.6f} TP={tp:.6f} RR={rr:.2f}"
        )

        return {
            "par": par, "lado": lado, "precio": precio,
            "sl": round(sl, 8), "tp": round(tp, 8),
            "tp1": round(tp1, 8), "tp2": round(tp, 8),
            "atr": round(atr, 8), "dist_sl": round(dist, 8),
            "score": score, "rsi": _rsi(cl[-20:]), "rr": round(rr, 2),
            "motivos": motivos, "kz": kz["nombre"],
            "htf": "NEUTRAL", "htf_4h": "NEUTRAL",
            "purga_nivel": f"{tipo}_{'SSL' if lado=='LONG' else 'BSL'}",
            "purga_peso": score,
            "vol_ratio": vol["rvol"],
            "bsl_h1": liq["bsl_level"], "ssl_h1": liq["ssl_level"],
            "bsl_h4": 0.0, "ssl_h4": 0.0, "bsl_d": 0.0, "ssl_d": 0.0,
            "ema_r": 0.0, "ema_l": 0.0, "vwap": 0.0, "sobre_vwap": False,
            "fvg_top": 0, "fvg_bottom": 0, "fvg_rellenado": True,
            "ob_bull": False, "ob_bear": False,
            "ob_fvg_bull": False, "ob_fvg_bear": False, "ob_mitigado": True,
            "bos_bull": lado=="LONG",  "bos_bear": lado=="SHORT",
            "choch_bull": qml["bull"], "choch_bear": qml["bear"],
            "sweep_bull": liq["ssl_swept"], "sweep_bear": liq["bsl_swept"],
            "patron": None, "vela_conf": True,
            "premium": False, "discount": False,
            "displacement": False, "macd_hist": 0,
            "asia_valido": True, "adx": 25.0, "inducement": False,
            "liq_bull": liq["ssl_swept"], "liq_bear": liq["bsl_swept"],
            "liq_z_up": vol["stop_pressure"], "liq_z_dn": vol["stop_pressure"],
            "liq_plot_trnd": 1 if lado=="LONG" else -1,
        }

    except Exception as e:
        log.error(f"analizar_par {par}: {e}")
        return None


def analizar_todos(pares: list, workers: int = 4) -> list:
    senales = []
    w = min(workers, len(pares), 8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=w) as ex:
        futuros = {ex.submit(analizar_par, p): p for p in pares}
        for fut in concurrent.futures.as_completed(futuros):
            try:
                r = fut.result()
                if r: senales.append(r)
            except Exception as e:
                log.error(f"thread: {e}")
    senales.sort(key=lambda x: x["score"], reverse=True)
    return senales
