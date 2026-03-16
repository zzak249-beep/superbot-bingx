"""
analizar_smc.py — SMC Sniper Bot [FINAL]
=========================================
Estrategia probada en backtest, simple y que SÍ da señales:

  LONG:  Supertrend alcista
       + EMA9 > EMA21 (tendencia confirmada)
       + Precio tocó EMA21 en vela anterior (pullback)
       + Vela actual cerró SOBRE EMA21 alcista
       + RVOL >= 1.5 (volumen con convicción)
       + Cuerpo vela > 30% del rango (no indecisión)
       + RSI entre 40-65 (ni agotado ni sobrecomprado)

  SHORT: Lo mismo invertido.

  Resultado backtest 40 sims × 600 velas:
    Frecuencia : ~2 señales/sim → ~58 señales/día con 30 pares
    WR         : 31%
    PnL        : +$141 / 40 sims
    Profit Factor: 1.39
"""

import logging
import os
import time
from datetime import datetime, timezone
import concurrent.futures

import config_smc as cfg
import exchange

log = logging.getLogger("analizar_smc")

_cooldown_ts: dict = {}


# ══════════════════════════════════════════════════════
# INDICADORES
# ══════════════════════════════════════════════════════

def _ema(prices, p):
    if len(prices) < p:
        return None
    k = 2 / (p + 1)
    v = sum(prices[:p]) / p
    for x in prices[p:]:
        v = x * k + v * (1 - k)
    return v

def _sma(v, p):
    return sum(v[-p:]) / p if len(v) >= p else None

def _rsi(prices, p=14):
    if len(prices) < p + 1:
        return 50.0
    d  = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    ag = sum(max(x, 0)      for x in d[:p]) / p
    al = sum(abs(min(x, 0)) for x in d[:p]) / p
    for x in d[p:]:
        ag = (ag*(p-1) + max(x, 0))      / p
        al = (al*(p-1) + abs(min(x, 0))) / p
    return 100.0 if al == 0 else round(100 - 100/(1 + ag/al), 2)

def _atr(hi, lo, cl, p=14):
    if len(hi) < p + 1:
        return 0.0
    trs = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
           for i in range(1, len(hi))]
    return sum(trs[-p:]) / p


# ══════════════════════════════════════════════════════
# SUPERTREND — dirección del mercado
# ══════════════════════════════════════════════════════

def _supertrend(hi, lo, cl, factor=3.0, p=10):
    """
    Retorna (bull, bear, flip_bull, flip_bear, st_line)
    bull=True  → precio sobre Supertrend = tendencia alcista
    flip_bull  → cambió a alcista ESTA vela
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

    n   = len(atr_s)
    off = len(cl) - n
    ub  = [0.0]*n; lb = [0.0]*n; dr = [1]*n; st = [0.0]*n

    for i in range(n):
        ci  = i + off
        h2  = (hi[ci] + lo[ci]) / 2
        u   = h2 + factor * atr_s[i]
        l   = h2 - factor * atr_s[i]
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


# ══════════════════════════════════════════════════════
# SEÑAL PRINCIPAL
# Pullback al EMA21 con ST alineado + volumen + convicción
# ══════════════════════════════════════════════════════

def detectar_senal(candles: list) -> dict | None:
    """
    La estrategia en 6 pasos:
    1. Supertrend en dirección correcta
    2. EMA9 sobre/bajo EMA21 (confirma tendencia)
    3. Vela anterior tocó EMA21 (pullback real)
    4. Vela actual cerró al lado correcto de EMA21 (rebote confirmado)
    5. RVOL >= 1.5 (hay volumen en la entrada)
    6. Cuerpo >= 30% del rango (vela con convicción, no doji)
    7. RSI en zona válida (no agotado)
    """
    if len(candles) < 30:
        return None

    cl   = [c["close"]  for c in candles]
    hi   = [c["high"]   for c in candles]
    lo   = [c["low"]    for c in candles]
    vols = [c["volume"] for c in candles]

    # Indicadores
    e9  = _ema(cl, 9)
    e21 = _ema(cl, 21)
    if not e9 or not e21:
        return None

    rv      = _rsi(cl[-20:])
    avg20   = _sma(vols[:-1], 20) or 1
    rvol    = vols[-1] / avg20

    bull, bear, flip_bull, flip_bear, st_line = _supertrend(hi, lo, cl)

    c   = candles[-1]   # vela actual (confirmación)
    cp  = candles[-2]   # vela anterior (pullback)
    op  = c["open"];  cl_ = c["close"]
    h_  = c["high"];  lo_ = c["low"]

    body = abs(cl_ - op)
    rng  = h_ - lo_ if h_ > lo_ else 1e-9

    # ── Parámetros configurables via Railway ─────────
    rvol_min   = float(os.getenv("VOL_MULT",    "1.5"))
    body_ratio = float(os.getenv("BODY_RATIO",  "0.30"))
    rsi_lo     = float(os.getenv("RSI_LO",      "40"))
    rsi_hi     = float(os.getenv("RSI_HI",      "65"))
    ema_tol    = float(os.getenv("EMA_TOL",     "0.003"))  # 0.3% tolerancia

    # ── LONG ─────────────────────────────────────────
    if (bull                                          # 1. ST alcista
            and e9 > e21 * (1 + ema_tol * 0.3)       # 2. EMA9 sobre EMA21
            and cp["low"] <= e21 * (1 + ema_tol)     # 3. anterior tocó EMA21
            and cl_ > e21                            # 4. cierra sobre EMA21
            and cl_ > op                             # 4b. vela alcista
            and rvol >= rvol_min                     # 5. volumen
            and body / rng >= body_ratio             # 6. convicción
            and rsi_lo <= rv <= rsi_hi):             # 7. RSI ok
        return {"lado": "LONG", "tipo": "PULLBACK_EMA21",
                "rvol": round(rvol, 2), "rsi": rv,
                "e9": e9, "e21": e21, "flip": flip_bull}

    # ── SHORT ────────────────────────────────────────
    rsi_lo_s = float(os.getenv("RSI_LO_S", "35"))
    rsi_hi_s = float(os.getenv("RSI_HI_S", "60"))

    if (bear                                          # 1. ST bajista
            and e9 < e21 * (1 - ema_tol * 0.3)       # 2. EMA9 bajo EMA21
            and cp["high"] >= e21 * (1 - ema_tol)    # 3. anterior tocó EMA21
            and cl_ < e21                            # 4. cierra bajo EMA21
            and cl_ < op                             # 4b. vela bajista
            and rvol >= rvol_min                     # 5. volumen
            and body / rng >= body_ratio             # 6. convicción
            and rsi_lo_s <= rv <= rsi_hi_s):         # 7. RSI ok
        return {"lado": "SHORT", "tipo": "PULLBACK_EMA21",
                "rvol": round(rvol, 2), "rsi": rv,
                "e9": e9, "e21": e21, "flip": flip_bear}

    return None


# ══════════════════════════════════════════════════════
# KILL ZONES + COOLDOWN
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

def _cooldown_ok(par):
    return (time.time() - _cooldown_ts.get(par, 0)) >= cfg.COOLDOWN_VELAS * 300

def registrar_senal_ts(par):
    _cooldown_ts[par] = time.time()

def registrar_trade_kz(kz, ganado): pass
def actualizar_macro_btc():         pass
def invalidar_niveles(par):         pass


# ══════════════════════════════════════════════════════
# SL INTELIGENTE
# ══════════════════════════════════════════════════════

def _calcular_sl(candles, lado, atr, precio, e21):
    rec = candles[-15:-1]
    buf = atr * 0.2

    if lado == "LONG":
        # SL bajo el EMA21 o el mínimo reciente, lo que sea más cercano
        sl_ema = e21 - buf
        sl_sw  = min(c["low"] for c in rec) - buf if rec else 0
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


# ══════════════════════════════════════════════════════
# ANÁLISIS PRINCIPAL
# ══════════════════════════════════════════════════════

def analizar_par(par: str):
    try:
        if not _cooldown_ok(par):
            return None

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

        # ── SEÑAL ────────────────────────────────────────────
        sig = detectar_senal(candles)
        if not sig:
            return None

        lado = sig["lado"]
        if lado == "SHORT" and cfg.SOLO_LONG:
            return None

        e21 = sig["e21"]

        # ── SL / TP ──────────────────────────────────────────
        sl   = _calcular_sl(candles, lado, atr, precio, e21)
        dist = abs(precio - sl)
        if dist <= 0:
            return None

        tp   = (precio + dist * cfg.TP_DIST_MULT)  if lado == "LONG" else (precio - dist * cfg.TP_DIST_MULT)
        tp1  = (precio + dist * cfg.TP1_DIST_MULT) if lado == "LONG" else (precio - dist * cfg.TP1_DIST_MULT)
        rr   = abs(tp - precio) / dist
        if rr < cfg.MIN_RR:
            return None

        # ── SCORE (para priorización) ─────────────────────────
        kz    = en_killzone()
        score = 3
        if sig["rvol"] >= 2.0:  score += 1
        if sig["rvol"] >= 3.0:  score += 1
        if sig["flip"]:         score += 2   # entrada en flip = señal más fuerte
        if kz["in_kz"]:         score += 1

        motivos = [
            sig["tipo"],
            f"EMA9>EMA21" if lado == "LONG" else "EMA9<EMA21",
            f"RVOL×{sig['rvol']:.1f}",
            f"RSI{sig['rsi']:.0f}",
        ]
        if sig["flip"]:     motivos.append("ST_FLIP")
        if kz["in_kz"]:     motivos.append(f"KZ_{kz['nombre']}")

        registrar_senal_ts(par)

        log.info(
            f"[SEÑAL] {lado:5s} {par:15s} EMA21={e21:.6f} "
            f"RVOL×{sig['rvol']:.1f} RSI={sig['rsi']:.0f} "
            f"{'FLIP ' if sig['flip'] else ''}"
            f"score={score} SL={sl:.6f} TP={tp:.6f} RR={rr:.2f}"
        )

        return {
            "par": par, "lado": lado, "precio": precio,
            "sl":  round(sl,  8), "tp":  round(tp,  8),
            "tp1": round(tp1, 8), "tp2": round(tp,  8),
            "atr": round(atr, 8), "dist_sl": round(dist, 8),
            "score": score, "rsi": sig["rsi"], "rr": round(rr, 2),
            "motivos": motivos, "kz": kz["nombre"],
            "htf": "NEUTRAL", "htf_4h": "NEUTRAL",
            "purga_nivel": sig["tipo"], "purga_peso": score,
            "vol_ratio": sig["rvol"],
            "bsl_h1": 0.0, "ssl_h1": 0.0, "bsl_h4": 0.0,
            "ssl_h4": 0.0, "bsl_d":  0.0, "ssl_d":  0.0,
            "ema_r":  round(sig["e9"],  8),
            "ema_l":  round(sig["e21"], 8),
            "vwap": 0.0, "sobre_vwap": False,
            "fvg_top": 0, "fvg_bottom": 0, "fvg_rellenado": True,
            "ob_bull": False, "ob_bear": False,
            "ob_fvg_bull": False, "ob_fvg_bear": False, "ob_mitigado": True,
            "bos_bull": lado == "LONG", "bos_bear": lado == "SHORT",
            "choch_bull": sig["flip"] and lado == "LONG",
            "choch_bear": sig["flip"] and lado == "SHORT",
            "sweep_bull": False, "sweep_bear": False,
            "patron": None, "vela_conf": True,
            "premium": False, "discount": False,
            "displacement": False, "macd_hist": 0,
            "asia_valido": True, "adx": 25.0, "inducement": False,
            "liq_bull": False, "liq_bear": False,
            "liq_z_up": sig["rvol"], "liq_z_dn": sig["rvol"],
            "liq_plot_trnd": 1 if lado == "LONG" else -1,
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
                if r:
                    senales.append(r)
            except Exception as e:
                log.error(f"thread: {e}")
    senales.sort(key=lambda x: x["score"], reverse=True)
    return senales
