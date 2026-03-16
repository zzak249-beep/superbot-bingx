"""
analizar_smc.py — SMC Sniper Bot v2.0 [1M Fusion]
===================================================
Estrategia fusionada: Liquidez HTF + Order Flow + Supertrend + EMA + RSI

  LONG:
    1. Purga de SSL (barrido de liquidez vendedora) con MEMORIA
       - Detectada en TF_PURGA (15m) o H1/H4
       - Al menos LIQ_TOQUES_MIN toques previos en la zona
       - La purga activa memoria de LIQ_PURGA_MEM velas en 1M
    2. Supertrend alcista (precio sobre ST)
    3. EMA9 > EMA21 (modo ALINEACION: close > EMA9 > EMA21)
    4. RSI entre 30-70 y subiendo
    5. Order Flow alcista (acumulación / bid wall / whale bid)
    6. RVOL >= VOL_MULT (auto-bypass si exchange no reporta volumen)
    7. Score total >= SCORE_MINIMO (55/100 default)

  SHORT: lógica invertida (purga BSL + confirmaciones bajistas)

  FIXES vs v1:
    - RVOL bypass: si vAvg≈0 → rvol=1.0 (no bloquea)
    - Purga con memoria var persistente entre ciclos
    - Touch counter por par/nivel
    - Order Flow delta integrado al score
    - Soporte multi-TF: obtiene H1/H4 candles para niveles
"""

import logging
import os
import time
from datetime import datetime, timezone
import concurrent.futures
from typing import Optional

import config_smc as cfg
import exchange

log = logging.getLogger("analizar_smc")

# ── Estado persistente entre ciclos ──────────────────────────
# {par: {"mem_alc": int, "mem_baj": int, "t_ssl": int, "t_bsl": int,
#        "ssl": float, "bsl": float, "last_touch_ts": float}}
_estado_liq: dict = {}
_cooldown_ts: dict = {}


# ══════════════════════════════════════════════════════════════
# INDICADORES TÉCNICOS
# ══════════════════════════════════════════════════════════════

def _ema(prices: list, p: int) -> Optional[float]:
    if len(prices) < p:
        return None
    k = 2 / (p + 1)
    v = sum(prices[:p]) / p
    for x in prices[p:]:
        v = x * k + v * (1 - k)
    return v


def _sma(v: list, p: int) -> Optional[float]:
    return sum(v[-p:]) / p if len(v) >= p else None


def _rsi(prices: list, p: int = 14) -> float:
    if len(prices) < p + 1:
        return 50.0
    d = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    ag = sum(max(x, 0)      for x in d[:p]) / p
    al = sum(abs(min(x, 0)) for x in d[:p]) / p
    for x in d[p:]:
        ag = (ag*(p-1) + max(x, 0))      / p
        al = (al*(p-1) + abs(min(x, 0))) / p
    return 100.0 if al == 0 else round(100 - 100 / (1 + ag/al), 2)


def _atr(hi: list, lo: list, cl: list, p: int = 14) -> float:
    if len(hi) < p + 1:
        return 0.0
    trs = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
           for i in range(1, len(hi))]
    return sum(trs[-p:]) / p


def _supertrend(hi: list, lo: list, cl: list,
                factor: float = 3.0, p: int = 10) -> tuple:
    """
    Retorna (bull, bear, flip_bull, flip_bear, st_line)
    bull=True → precio sobre Supertrend (tendencia alcista)
    """
    if len(cl) < p + 2:
        return False, False, False, False, 0.0

    atrs = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
            for i in range(1, len(cl))]
    av = sum(atrs[:p]) / p
    atr_s = [av]
    for a in atrs[p:]:
        av = (av*(p-1) + a) / p
        atr_s.append(av)

    n = len(atr_s); off = len(cl) - n
    ub = [0.0]*n; lb = [0.0]*n; dr = [1]*n; st = [0.0]*n

    for i in range(n):
        ci = i + off
        h2 = (hi[ci] + lo[ci]) / 2
        u  = h2 + factor * atr_s[i]
        l  = h2 - factor * atr_s[i]
        ub[i] = min(u, ub[i-1]) if i > 0 and cl[ci-1] < ub[i-1] else u
        lb[i] = max(l, lb[i-1]) if i > 0 and cl[ci-1] > lb[i-1] else l
        if i == 0:
            dr[i] = 1
        elif st[i-1] == ub[i-1]:
            dr[i] = 1 if cl[ci] < ub[i] else -1
        else:
            dr[i] = -1 if cl[ci] > lb[i] else 1
        st[i] = ub[i] if dr[i] == 1 else lb[i]

    bull      = dr[-1] < 0
    bear      = dr[-1] > 0
    flip_bull = (dr[-1] < 0 and dr[-2] > 0) if len(dr) >= 2 else False
    flip_bear = (dr[-1] > 0 and dr[-2] < 0) if len(dr) >= 2 else False
    return bull, bear, flip_bull, flip_bear, st[-1]


# ══════════════════════════════════════════════════════════════
# ORDER FLOW DELTA
# Calcula acumulación/distribución a partir de la posición del close
# ══════════════════════════════════════════════════════════════

def _order_flow(candles: list) -> dict:
    """
    Calcula delta de volumen, acumulación, distribución e iceberg/whale.
    Retorna dict con claves: bull, bear, flow_bull, flow_bear, delta, rvol, vol_valido
    """
    batch   = cfg.OF_FLOW_LEN
    vols    = [c["volume"] for c in candles]
    cls     = [c["close"]  for c in candles]
    opns    = [c["open"]   for c in candles]
    his     = [c["high"]   for c in candles]
    los     = [c["low"]    for c in candles]

    # RVOL con bypass si volumen inválido
    vol_sma_p = min(cfg.VOL_LOOKBACK, len(vols) - 1)
    avg_vol   = _sma(vols[:-1], vol_sma_p) or 0.0
    vol_valido = avg_vol > 0.001
    cur_vol   = vols[-1]
    rvol      = (cur_vol / avg_vol) if vol_valido else 1.0  # bypass si 0

    # Calcular buy/sell vol por vela (posición del close en el rango)
    buy_vols  = []
    sell_vols = []
    for i in range(len(candles)):
        rng = max(his[i] - los[i], 1e-9)
        pos = (cls[i] - los[i]) / rng
        buy_vols.append(vols[i] * pos)
        sell_vols.append(vols[i] * (1 - pos))

    # Batch acumulación (últimas N velas)
    n = min(batch, len(candles))
    cb = sum(buy_vols[-n:])
    cs = sum(sell_vols[-n:])
    ab = cb / n if n > 0 else 0
    as_ = cs / n if n > 0 else 0

    flow_bull = ab > (as_ * cfg.OF_FLOW_RATIO) and cb > cs
    flow_bear = as_ > (ab  * cfg.OF_FLOW_RATIO) and cs > cb
    delta     = cb - cs

    # Iceberg / Whale detection
    smart_ice = max(cfg.OF_ICE_MIN_VOL, avg_vol * cfg.OF_ICE_MUL)
    prev_vol  = vols[-2] if len(vols) >= 2 else 0
    vol_diff  = abs(prev_vol - cur_vol)
    ice_grow  = (cur_vol > prev_vol and cur_vol >= smart_ice
                 and vol_diff >= smart_ice * 0.2 and vol_valido)
    ice_bid   = ice_grow and cls[-1] > opns[-1]
    ice_ask   = ice_grow and cls[-1] <= opns[-1]

    # Spoof detection
    prev_avg  = _sma(vols[:-2], cfg.VOL_LOOKBACK) or 1
    prev_rvol = (prev_vol / prev_avg) if prev_avg > 0.001 else 1.0
    spoof     = (vol_valido and cur_vol < prev_vol * cfg.OF_SPOOF_PULL
                 and abs(vol_diff) > avg_vol * 1.5
                 and prev_rvol >= 1.5)
    spoof_bid = spoof and cls[-1] > opns[-1] and cls[-1] > cls[-2]
    spoof_ask = spoof and cls[-1] <= opns[-1] and cls[-1] < cls[-2]

    # Whale (nuevo nivel de precio con alto RVOL)
    price_move = abs(cls[-1] - cls[-2]) if len(cls) >= 2 else 0
    atr_approx = _atr(his[-20:], los[-20:], cls[-20:]) if len(cls) >= 20 else 0
    whale_entry = rvol >= cfg.OF_ICE_MUL * 0.8 and price_move > atr_approx * 0.5 and vol_valido
    whale_bid   = whale_entry and cls[-1] > opns[-1]
    whale_ask   = whale_entry and cls[-1] <= opns[-1]

    of_bull_raw = flow_bull or ice_bid or spoof_ask or whale_bid
    of_bear_raw = flow_bear or ice_ask or spoof_bid or whale_ask
    of_bull = of_bull_raw and (not of_bear_raw or delta >= 0)
    of_bear = of_bear_raw and (not of_bull_raw or delta <  0)

    # Tipo de evento para el log
    if of_bull:
        tipo = "ACUM" if flow_bull else ("BWL" if ice_bid else ("APL" if spoof_ask else "WBD"))
    elif of_bear:
        tipo = "DIST" if flow_bear else ("AWL" if ice_ask else ("BPL" if spoof_bid else "WAK"))
    else:
        tipo = "NEUTRAL"

    return {
        "of_bull":    of_bull,
        "of_bear":    of_bear,
        "flow_bull":  flow_bull,
        "flow_bear":  flow_bear,
        "delta":      delta,
        "rvol":       round(rvol, 3),
        "vol_valido": vol_valido,
        "tipo_of":    tipo,
    }


# ══════════════════════════════════════════════════════════════
# NIVELES DE LIQUIDEZ HTF
# Obtiene BSL/SSL de H1 y H4 usando candles reales de BingX
# ══════════════════════════════════════════════════════════════

def _get_niveles_htf(par: str) -> dict:
    """
    Obtiene los niveles de liquidez (máximo/mínimo) de H1 y H4.
    Retorna dict con bsl_h1, ssl_h1, bsl_h4, ssl_h4.
    """
    niveles = {"bsl_h1": 0.0, "ssl_h1": 0.0, "bsl_h4": 0.0, "ssl_h4": 0.0}
    lkb = cfg.LIQ_LOOKBACK

    try:
        if cfg.LIQ_USAR_H1:
            c1h = exchange.get_candles(par, cfg.TF_H1, lkb)
            if len(c1h) >= 5:
                niveles["bsl_h1"] = max(c["high"]  for c in c1h[-lkb:])
                niveles["ssl_h1"] = min(c["low"]   for c in c1h[-lkb:])
    except Exception as e:
        log.debug(f"[HTF] H1 {par}: {e}")

    try:
        if cfg.LIQ_USAR_H4:
            c4h = exchange.get_candles(par, cfg.TF_H4, lkb)
            if len(c4h) >= 5:
                niveles["bsl_h4"] = max(c["high"]  for c in c4h[-lkb:])
                niveles["ssl_h4"] = min(c["low"]   for c in c4h[-lkb:])
    except Exception as e:
        log.debug(f"[HTF] H4 {par}: {e}")

    return niveles


# ══════════════════════════════════════════════════════════════
# GESTIÓN DE PURGA Y TOQUES (estado persistente por par)
# FIX PRINCIPAL: memoria entre ciclos usando dict global
# ══════════════════════════════════════════════════════════════

def _init_estado_liq(par: str, niveles: dict):
    """Inicializa o actualiza el estado de liquidez de un par."""
    if par not in _estado_liq:
        _estado_liq[par] = {
            "mem_alc":  0,
            "mem_baj":  0,
            "t_ssl":    0,
            "t_bsl":    0,
            "ssl":      niveles.get("ssl_h1") or niveles.get("ssl_h4") or 0,
            "bsl":      niveles.get("bsl_h1") or niveles.get("bsl_h4") or 0,
            "last_upd": 0.0,
        }
    st = _estado_liq[par]
    # Actualizar niveles si han cambiado significativamente
    nuevo_ssl = niveles.get("ssl_h1") or niveles.get("ssl_h4") or 0
    nuevo_bsl = niveles.get("bsl_h1") or niveles.get("bsl_h4") or 0
    if nuevo_ssl > 0:
        st["ssl"] = nuevo_ssl
    if nuevo_bsl > 0:
        st["bsl"] = nuevo_bsl
    return st


def _detectar_toques_y_purga(par: str, candles: list, niveles: dict) -> dict:
    """
    Analiza las últimas velas para:
    1. Contar toques en zona de liquidez
    2. Detectar purga y actualizar memoria
    3. Decrementar memoria si no hay purga nueva
    """
    st  = _init_estado_liq(par, niveles)
    atr = _atr([c["high"] for c in candles],
               [c["low"]  for c in candles],
               [c["close"] for c in candles])
    zona = atr * 0.3

    margen = candles[-1]["close"] * cfg.LIQ_MARGEN_PCT / 100

    # Niveles activos (combinar H1, H4 y TF_PURGA)
    ssls = [v for k, v in niveles.items() if "ssl" in k and v > 0]
    bsls = [v for k, v in niveles.items() if "bsl" in k and v > 0]

    if not ssls and not bsls:
        return st

    ssl_ref = min(ssls) if ssls else 0  # SSL más bajo = soporte más relevante
    bsl_ref = max(bsls) if bsls else 0  # BSL más alto = resistencia más relevante

    win = cfg.LIQ_TOQUES_WIN
    candles_win = candles[-win:]

    # Contar toques SSL (precio baja a esa zona pero no purga todavía)
    t_ssl = sum(
        1 for c in candles_win
        if ssl_ref > 0 and c["low"] <= ssl_ref + zona and c["low"] >= ssl_ref - zona * 2
    )
    # Contar toques BSL
    t_bsl = sum(
        1 for c in candles_win
        if bsl_ref > 0 and c["high"] >= bsl_ref - zona and c["high"] <= bsl_ref + zona * 2
    )

    st["t_ssl"] = t_ssl
    st["t_bsl"] = t_bsl
    st["ssl"]   = ssl_ref
    st["bsl"]   = bsl_ref

    c_last = candles[-1]

    # Detectar purga alcista (precio barre SSL y cierra encima)
    purga_alc = (ssl_ref > 0
                 and c_last["low"] <= ssl_ref * (1 + margen)
                 and c_last["close"] > ssl_ref)

    # Detectar purga bajista (precio barre BSL y cierra debajo)
    purga_baj = (bsl_ref > 0
                 and c_last["high"] >= bsl_ref * (1 - margen)
                 and c_last["close"] < bsl_ref)

    # Actualizar memoria (FIX: persiste entre ciclos gracias al dict global)
    if purga_alc and t_ssl >= cfg.LIQ_TOQUES_MIN:
        st["mem_alc"] = cfg.LIQ_PURGA_MEM
        log.info(f"[LIQ] PURGA ALC {par} SSL={ssl_ref:.6f} t={t_ssl} → mem={cfg.LIQ_PURGA_MEM}")
    elif st["mem_alc"] > 0:
        st["mem_alc"] -= 1

    if purga_baj and t_bsl >= cfg.LIQ_TOQUES_MIN:
        st["mem_baj"] = cfg.LIQ_PURGA_MEM
        log.info(f"[LIQ] PURGA BAJ {par} BSL={bsl_ref:.6f} t={t_bsl} → mem={cfg.LIQ_PURGA_MEM}")
    elif st["mem_baj"] > 0:
        st["mem_baj"] -= 1

    return st


# ══════════════════════════════════════════════════════════════
# SCORE DE CONFLUENCIA
# ══════════════════════════════════════════════════════════════

def _calcular_score(lado: str, st_liq: dict, of: dict,
                    st_bull: bool, st_bear: bool,
                    ema_ok_c: bool, ema_ok_v: bool,
                    rsi_ok_c: bool, rsi_ok_v: bool,
                    flip: bool, kz: bool) -> int:
    if lado == "LONG":
        s = 0
        s += 25 if st_liq["mem_alc"] > 0 else 0       # purga activa
        s += 10 if st_liq["t_ssl"] >= cfg.LIQ_TOQUES_MIN else 0  # toques suficientes
        s += 20 if of["of_bull"]  else 0                # order flow alcista
        s += 10 if of["flow_bull"] else 0               # acumulación pura
        s += 10 if st_bull        else 0                # supertrend
        s += 10 if ema_ok_c       else 0                # ema alineada
        s +=  5 if rsi_ok_c       else 0                # rsi ok
        s +=  5 if of["rvol"] >= cfg.VOL_MULT else 0   # volumen ok
        s +=  3 if flip           else 0                # flip ST
        s +=  2 if kz             else 0                # kill zone
        return s
    else:
        s = 0
        s += 25 if st_liq["mem_baj"] > 0 else 0
        s += 10 if st_liq["t_bsl"] >= cfg.LIQ_TOQUES_MIN else 0
        s += 20 if of["of_bear"]  else 0
        s += 10 if of["flow_bear"] else 0
        s += 10 if st_bear        else 0
        s += 10 if ema_ok_v       else 0
        s +=  5 if rsi_ok_v       else 0
        s +=  5 if of["rvol"] >= cfg.VOL_MULT else 0
        s +=  3 if flip           else 0
        s +=  2 if kz             else 0
        return s


# ══════════════════════════════════════════════════════════════
# SL INTELIGENTE
# ══════════════════════════════════════════════════════════════

def _calcular_sl(candles: list, lado: str, atr: float,
                 precio: float, e21: float) -> float:
    rec = candles[-15:-1]
    buf = atr * 0.2

    if lado == "LONG":
        sl_ema = e21 - buf
        sl_sw  = min(c["low"]  for c in rec) - buf if rec else 0
        opts   = [x for x in [sl_ema, sl_sw] if 0 < x < precio]
        sl     = max(opts) if opts else precio - atr * cfg.SL_ATR_MULT
        if precio - sl > 3 * atr:
            sl = precio - atr * cfg.SL_ATR_MULT
    else:
        sl_ema = e21 + buf
        sl_sw  = max(c["high"] for c in rec) + buf if rec else 0
        opts   = [x for x in [sl_ema, sl_sw] if x > precio]
        sl     = min(opts) if opts else precio + atr * cfg.SL_ATR_MULT
        if sl - precio > 3 * atr:
            sl = precio + atr * cfg.SL_ATR_MULT

    return sl


# ══════════════════════════════════════════════════════════════
# KILL ZONES
# ══════════════════════════════════════════════════════════════

def en_killzone() -> dict:
    m    = datetime.now(timezone.utc)
    mins = m.hour * 60 + m.minute
    london = cfg.KZ_LONDON_START <= mins < cfg.KZ_LONDON_END
    ny     = cfg.KZ_NY_START     <= mins < cfg.KZ_NY_END
    return {
        "in_kz":  london or ny,
        "nombre": "LONDON" if london else ("NY" if ny else "FUERA"),
    }


# ══════════════════════════════════════════════════════════════
# COOLDOWN
# ══════════════════════════════════════════════════════════════

def _cooldown_ok(par: str) -> bool:
    # En 1M, cooldown en segundos (COOLDOWN_VELAS × 60s)
    return (time.time() - _cooldown_ts.get(par, 0)) >= cfg.COOLDOWN_VELAS * 60


def registrar_senal_ts(par: str):
    _cooldown_ts[par] = time.time()


# ── Stubs compatibilidad ──
def registrar_trade_kz(kz, ganado): pass
def actualizar_macro_btc():         pass
def invalidar_niveles(par):
    if par in _estado_liq:
        del _estado_liq[par]


# ══════════════════════════════════════════════════════════════
# ANÁLISIS PRINCIPAL POR PAR
# ══════════════════════════════════════════════════════════════

def analizar_par(par: str) -> Optional[dict]:
    try:
        if not _cooldown_ok(par):
            return None

        # ── Candles 1M ────────────────────────────────────────
        candles = exchange.get_candles(par, cfg.TIMEFRAME, cfg.CANDLES_LIMIT)
        if len(candles) < 50:
            return None

        cl  = [c["close"] for c in candles]
        hi  = [c["high"]  for c in candles]
        lo  = [c["low"]   for c in candles]
        precio = cl[-1]
        if precio <= 0:
            return None

        atr = _atr(hi, lo, cl)
        if atr <= 0:
            return None

        # ── Indicadores técnicos 1M ───────────────────────────
        e9  = _ema(cl, cfg.EMA_RAPIDA)
        e21 = _ema(cl, cfg.EMA_LENTA)
        if not e9 or not e21:
            return None

        rsi_v = _rsi(cl[-30:])
        rsi_sub = rsi_v >= _rsi(cl[-31:-1]) if len(cl) >= 31 else True

        bull_st, bear_st, flip_bull, flip_bear, st_line = _supertrend(
            hi, lo, cl, cfg.ST_FACTOR, cfg.ST_PERIOD
        )

        # EMA confirmación
        modo = cfg.EMA_MODO
        if modo == "CRUCE":
            ema_ok_c = e9 > e21 and _ema(cl[:-1], cfg.EMA_RAPIDA) <= _ema(cl[:-1], cfg.EMA_LENTA)
            ema_ok_v = e9 < e21 and _ema(cl[:-1], cfg.EMA_RAPIDA) >= _ema(cl[:-1], cfg.EMA_LENTA)
        elif modo == "ALINEACION":
            ema_ok_c = e9 > e21 and precio > e9
            ema_ok_v = e9 < e21 and precio < e9
        else:  # CUALQUIERA
            ema_ok_c = e9 > e21
            ema_ok_v = e9 < e21

        rsi_ok_c = cfg.RSI_OS < rsi_v < cfg.RSI_OB and rsi_sub
        rsi_ok_v = cfg.RSI_OS < rsi_v < cfg.RSI_OB and not rsi_sub

        # ── Order Flow ────────────────────────────────────────
        of = _order_flow(candles)

        # RVOL gate (con bypass automático si vol_valido=False)
        rvol_ok = not of["vol_valido"] or of["rvol"] >= cfg.VOL_MULT

        # ── Niveles HTF y purga ───────────────────────────────
        niveles = _get_niveles_htf(par)
        st_liq  = _detectar_toques_y_purga(par, candles, niveles)

        kz = en_killzone()

        # ── Determinar lado con score ─────────────────────────
        score_long  = _calcular_score("LONG",  st_liq, of,
                                      bull_st, bear_st,
                                      ema_ok_c, ema_ok_v,
                                      rsi_ok_c, rsi_ok_v,
                                      flip_bull, kz["in_kz"])
        score_short = _calcular_score("SHORT", st_liq, of,
                                      bull_st, bear_st,
                                      ema_ok_c, ema_ok_v,
                                      rsi_ok_c, rsi_ok_v,
                                      flip_bear, kz["in_kz"])

        # Elegir el lado con mayor score si supera mínimo
        if score_long >= cfg.SCORE_MINIMO and score_long > score_short:
            lado  = "LONG"
            score = score_long
            flip  = flip_bull
        elif score_short >= cfg.SCORE_MINIMO and not cfg.SOLO_LONG:
            lado  = "SHORT"
            score = score_short
            flip  = flip_bear
        else:
            return None

        # ── Verificar purga activa para el lado ──────────────
        if lado == "LONG"  and st_liq["mem_alc"] == 0:
            return None
        if lado == "SHORT" and st_liq["mem_baj"] == 0:
            return None

        # Requiere al menos que rvol_ok o que sea una señal premium
        if not rvol_ok and score < cfg.SCORE_MINIMO + 10:
            return None

        # ── SL / TP ───────────────────────────────────────────
        sl   = _calcular_sl(candles, lado, atr, precio, e21)
        dist = abs(precio - sl)
        if dist <= 0:
            return None

        tp  = (precio + dist * cfg.TP_DIST_MULT)  if lado == "LONG" else (precio - dist * cfg.TP_DIST_MULT)
        tp1 = (precio + dist * cfg.TP1_DIST_MULT) if lado == "LONG" else (precio - dist * cfg.TP1_DIST_MULT)
        rr  = abs(tp - precio) / dist
        if rr < cfg.MIN_RR:
            return None

        # ── Motivos para log/telegram ─────────────────────────
        motivos = [
            of["tipo_of"],
            f"EMA{'▲' if lado=='LONG' else '▼'}",
            f"ST_{'BULL' if bull_st else 'BEAR'}",
            f"RSI{rsi_v:.0f}",
            f"SCORE{score}",
        ]
        if flip:        motivos.append("ST_FLIP")
        if kz["in_kz"]: motivos.append(f"KZ_{kz['nombre']}")
        if of["of_bull"] or of["of_bear"]: motivos.append(of["tipo_of"])

        registrar_senal_ts(par)

        log.info(
            f"[SEÑAL] {lado:5s} {par:15s} "
            f"score={score} RVOL×{of['rvol']:.1f} RSI={rsi_v:.0f} "
            f"ST={'BULL' if bull_st else 'BEAR'} OF={of['tipo_of']} "
            f"{'FLIP ' if flip else ''}"
            f"t_ssl={st_liq['t_ssl']} t_bsl={st_liq['t_bsl']} "
            f"mem_alc={st_liq['mem_alc']} mem_baj={st_liq['mem_baj']} "
            f"SL={sl:.6f} TP={tp:.6f} RR={rr:.2f}"
        )

        return {
            "par": par, "lado": lado, "precio": precio,
            "sl":  round(sl,  8), "tp":  round(tp,  8),
            "tp1": round(tp1, 8), "tp2": round(tp,  8),
            "atr": round(atr, 8), "dist_sl": round(dist, 8),
            "score": score, "rsi": rsi_v, "rr": round(rr, 2),
            "motivos": motivos, "kz": kz["nombre"],
            # Campos compatibilidad main_smc.py
            "htf": "BULL" if bull_st else "BEAR", "htf_4h": "NEUTRAL",
            "purga_nivel": f"SSL{st_liq['t_ssl']}" if lado=="LONG" else f"BSL{st_liq['t_bsl']}",
            "purga_peso": score,
            "vol_ratio": of["rvol"],
            "bsl_h1": niveles.get("bsl_h1", 0),
            "ssl_h1": niveles.get("ssl_h1", 0),
            "bsl_h4": niveles.get("bsl_h4", 0),
            "ssl_h4": niveles.get("ssl_h4", 0),
            "bsl_d": 0.0, "ssl_d": 0.0,
            "ema_r": round(e9,  8),
            "ema_l": round(e21, 8),
            "vwap": 0.0, "sobre_vwap": precio > e21,
            "fvg_top": 0, "fvg_bottom": 0, "fvg_rellenado": True,
            "ob_bull": lado == "LONG", "ob_bear": lado == "SHORT",
            "ob_fvg_bull": False, "ob_fvg_bear": False, "ob_mitigado": False,
            "bos_bull": lado == "LONG", "bos_bear": lado == "SHORT",
            "choch_bull": flip and lado == "LONG",
            "choch_bear": flip and lado == "SHORT",
            "sweep_bull": st_liq["mem_alc"] > 0,
            "sweep_bear": st_liq["mem_baj"] > 0,
            "patron": of["tipo_of"], "vela_conf": True,
            "premium": lado == "SHORT", "discount": lado == "LONG",
            "displacement": of["of_bull"] or of["of_bear"],
            "macd_hist": of["delta"],
            "asia_valido": True, "adx": 25.0, "inducement": False,
            "liq_bull": st_liq["mem_alc"] > 0,
            "liq_bear": st_liq["mem_baj"] > 0,
            "liq_z_up": st_liq["t_bsl"], "liq_z_dn": st_liq["t_ssl"],
            "liq_plot_trnd": 1 if lado == "LONG" else -1,
        }

    except Exception as e:
        log.error(f"analizar_par {par}: {e}", exc_info=True)
        return None


def analizar_todos(pares: list, workers: int = 4) -> list:
    senales = []
    w = min(workers, len(pares), 8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=w) as ex:
        futuros = {ex.submit(analizar_par, p): p for p in pares}
        for fut in concurrent.futures.as_completed(futuros):
            try:
                r = fut.result()
                if r:
                    senales.append(r)
            except Exception as e:
                log.error(f"thread {e}")
    senales.sort(key=lambda x: x["score"], reverse=True)
    return senales
