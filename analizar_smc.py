"""
analizar_smc.py — SMC Sniper Bot [OPTIMIZADO]
=============================================
Estrategia con mayor rentabilidad según backtest:

  SEÑAL = Supertrend alineado  +  RVOL ≥ 1.3×  +  UNA de estas:
    A) Sweep de swing (precio barró máx/mín reciente y cerró al otro lado)
    B) Wick de absorción (wick grande + RSI en zona) 
    C) Supertrend flip (cambio de dirección)

  Parámetros optimizados: TP=3x, SL=1.5x, WR~34%, PF~1.74
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
_CACHE_TTL = 600


# ══════════════════════════════════════════════════════
# INDICADORES
# ══════════════════════════════════════════════════════

def _sma(v, p):
    return sum(v[-p:]) / p if len(v) >= p else None

def _ema(prices, p):
    if len(prices) < p: return None
    k = 2 / (p + 1); v = sum(prices[:p]) / p
    for x in prices[p:]: v = x*k + v*(1-k)
    return v

def _rsi(prices, p=14):
    if len(prices) < p+1: return 50.0
    d  = [prices[i]-prices[i-1] for i in range(1, len(prices))]
    ag = sum(max(x,0)      for x in d[:p]) / p
    al = sum(abs(min(x,0)) for x in d[:p]) / p
    for x in d[p:]:
        ag = (ag*(p-1)+max(x,0)) / p
        al = (al*(p-1)+abs(min(x,0))) / p
    return 100.0 if al == 0 else round(100 - 100/(1+ag/al), 2)

def _atr(hi, lo, cl, p=14):
    if len(hi) < p+1: return 0.0
    trs = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
           for i in range(1, len(hi))]
    return sum(trs[-p:]) / p


# ══════════════════════════════════════════════════════
# SUPERTREND — dirección principal del mercado
# ══════════════════════════════════════════════════════

def _supertrend(hi, lo, cl, factor=3.0, p=10):
    """
    Retorna (st_bull, st_bear, flip_bull, flip_bear)
    st_bull=True → tendencia alcista
    flip_bull=True → acaba de cambiar a alcista esta vela
    """
    if len(cl) < p+2:
        return False, False, False, False

    atrs = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
            for i in range(1, len(cl))]
    av = sum(atrs[:p]) / p
    atr_s = [av]
    for a in atrs[p:]:
        av = (av*(p-1)+a) / p; atr_s.append(av)

    n = len(atr_s); off = len(cl) - n
    ub = [0.]*n; lb = [0.]*n; dr = [1]*n; st = [0.]*n

    for i in range(n):
        ci = i+off; h2 = (hi[ci]+lo[ci])/2
        u = h2+factor*atr_s[i]; l = h2-factor*atr_s[i]
        ub[i] = min(u, ub[i-1]) if i>0 and cl[ci-1]<ub[i-1] else u
        lb[i] = max(l, lb[i-1]) if i>0 and cl[ci-1]>lb[i-1] else l
        if i == 0: dr[i] = 1
        elif st[i-1] == ub[i-1]: dr[i] = 1 if cl[ci]<ub[i] else -1
        else: dr[i] = -1 if cl[ci]>lb[i] else 1
        st[i] = ub[i] if dr[i]==1 else lb[i]

    st_bull    = dr[-1] < 0
    st_bear    = dr[-1] > 0
    flip_bull  = (dr[-1]<0 and dr[-2]>0) if len(dr)>=2 else False
    flip_bear  = (dr[-1]>0 and dr[-2]<0) if len(dr)>=2 else False

    return st_bull, st_bear, flip_bull, flip_bear


# ══════════════════════════════════════════════════════
# CONDICIÓN A — SWEEP DE SWING
# Precio barró el máximo/mínimo de las últimas N velas
# y cerró al lado correcto (la vela confirmadora)
# ══════════════════════════════════════════════════════

def sweep_signal(candles: list) -> str | None:
    """
    Retorna 'LONG', 'SHORT' o None.
    Swing lookback = 40 velas (parámetro óptimo del backtest).
    """
    lb = int(os.getenv("SWING_LB", "40"))
    if len(candles) < lb + 4:
        return None

    ref    = candles[-(lb+2):-2]       # ventana sin lookahead
    cp     = candles[-2]               # vela que barró
    c      = candles[-1]               # vela de confirmación

    if not ref:
        return None

    swing_hi = max(x["high"] for x in ref)
    swing_lo = min(x["low"]  for x in ref)

    # Sweep SSL: vela anterior bajó bajo swing_lo, actual cerró arriba
    if cp["low"] <= swing_lo and c["close"] > swing_lo:
        return "LONG"

    # Sweep BSL: vela anterior subió sobre swing_hi, actual cerró abajo
    if cp["high"] >= swing_hi and c["close"] < swing_hi:
        return "SHORT"

    return None


# ══════════════════════════════════════════════════════
# CONDICIÓN B — WICK DE ABSORCIÓN  (Delta Strike proxy)
# Wick grande + RSI en zona + cierre confirmado
# ══════════════════════════════════════════════════════

def wick_absorption(candles: list) -> str | None:
    """
    Retorna 'LONG', 'SHORT' o None.
    Parámetros óptimos: wick_mult=1.5, RSI_os=48, RSI_ob=52
    """
    wick_mult = float(os.getenv("WICK_MULT",  "1.5"))
    rsi_os    = float(os.getenv("DS_RSI_OS",  "48"))
    rsi_ob    = float(os.getenv("DS_RSI_OB",  "52"))

    if len(candles) < 30:
        return None

    c   = candles[-1]
    op  = c["open"]; hi = c["high"]; lo = c["low"]; cl = c["close"]
    body = abs(cl - op)
    rng  = hi - lo if hi > lo else 1e-9
    lw   = min(cl, op) - lo       # wick inferior
    uw   = hi - max(cl, op)       # wick superior

    closes = [x["close"] for x in candles]
    rv     = _rsi(closes[-30:])

    # Absorción alcista: wick inferior grande + RSI bajo + vela alcista
    if (lw > body * wick_mult and
            lw / rng > 0.28 and
            rv < rsi_os and
            cl > op):
        return "LONG"

    # Absorción bajista: wick superior grande + RSI alto + vela bajista
    if (uw > body * wick_mult and
            uw / rng > 0.28 and
            rv > rsi_ob and
            cl < op):
        return "SHORT"

    return None


# ══════════════════════════════════════════════════════
# CONDICIÓN C — SUPERTREND FLIP (señal más limpia)
# ══════════════════════════════════════════════════════

def st_flip_signal(flip_bull: bool, flip_bear: bool) -> str | None:
    if flip_bull: return "LONG"
    if flip_bear: return "SHORT"
    return None


# ══════════════════════════════════════════════════════
# VOLUMEN — filtro base obligatorio
# ══════════════════════════════════════════════════════

def check_vol(candles: list) -> dict:
    rvol_min = float(os.getenv("VOL_MULT", "1.3"))
    vols     = [c["volume"] for c in candles]
    avg      = _sma(vols[-21:-1], 20) or 1
    ratio    = vols[-1] / avg
    return {"ok": ratio >= rvol_min, "ratio": round(ratio, 2)}


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
def actualizar_macro_btc(): pass
def invalidar_niveles(par): pass


# ══════════════════════════════════════════════════════
# SL INTELIGENTE
# ══════════════════════════════════════════════════════

def _calcular_sl(candles, lado, atr, precio):
    rec = candles[-20:-1]
    buf = atr * 0.25

    if lado == "LONG":
        swing = min(c["low"] for c in rec) if rec else precio
        sl    = swing - buf
        if precio - sl > 3 * atr:
            sl = precio - atr * cfg.SL_ATR_MULT
    else:
        swing = max(c["high"] for c in rec) if rec else precio
        sl    = swing + buf
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
        if len(candles) < 80:
            return None

        cl     = [c["close"] for c in candles]
        hi     = [c["high"]  for c in candles]
        lo     = [c["low"]   for c in candles]
        precio = cl[-1]
        if precio <= 0:
            return None

        atr = _atr(hi, lo, cl)
        if atr <= 0:
            return None

        # ── Supertrend (filtro de dirección) ─────────────────
        st_bull, st_bear, flip_bull, flip_bear = _supertrend(hi, lo, cl)

        # ── Volumen (filtro obligatorio) ──────────────────────
        vol = check_vol(candles)
        if not vol["ok"]:
            return None

        # ── Señal: UNA de las 3 condiciones ──────────────────
        sweep = sweep_signal(candles)
        wick  = wick_absorption(candles)
        flip  = st_flip_signal(flip_bull, flip_bear)

        lado = None
        tipo = ""

        # Prioridad: flip > sweep > wick
        for cond_lado, cond_tipo in [(flip, "ST_FLIP"), (sweep, "SWEEP"), (wick, "WICK")]:
            if cond_lado:
                # Verificar que ST esté alineado
                if cond_lado == "LONG"  and st_bull:
                    lado = "LONG";  tipo = cond_tipo; break
                if cond_lado == "SHORT" and st_bear:
                    lado = "SHORT"; tipo = cond_tipo; break

        if not lado:
            return None

        if lado == "SHORT" and cfg.SOLO_LONG:
            return None

        # ── SL / TP ──────────────────────────────────────────
        sl   = _calcular_sl(candles, lado, atr, precio)
        dist = abs(precio - sl)
        if dist <= 0:
            return None

        tp   = (precio + dist * cfg.TP_DIST_MULT)  if lado == "LONG" else (precio - dist * cfg.TP_DIST_MULT)
        tp1  = (precio + dist * cfg.TP1_DIST_MULT) if lado == "LONG" else (precio - dist * cfg.TP1_DIST_MULT)
        rr   = abs(tp - precio) / dist
        if rr < cfg.MIN_RR:
            return None

        # ── Score ─────────────────────────────────────────────
        kz    = en_killzone()
        score = 3
        if tipo == "ST_FLIP": score += 2   # flip es la señal más limpia
        if tipo == "SWEEP":   score += 1
        if vol["ratio"] >= 2.0: score += 1
        if vol["ratio"] >= 3.0: score += 1
        if flip_bull and lado == "LONG":  score += 1
        if flip_bear and lado == "SHORT": score += 1
        if kz["in_kz"]: score += 1

        # Bonus si sweep + wick coinciden
        if sweep and wick and sweep == lado:
            score += 2

        rsi_val = _rsi(cl[-30:])
        motivos = [tipo, f"RVOL×{vol['ratio']:.1f}", f"RSI{rsi_val:.0f}"]
        if flip_bull or flip_bear: motivos.append("ST_FLIP")
        if sweep == lado: motivos.append("SWEEP")
        if wick  == lado: motivos.append("WICK")
        if kz["in_kz"]: motivos.append(f"KZ_{kz['nombre']}")

        registrar_senal_ts(par)

        log.info(
            f"[SEÑAL] {lado:5s} {par:15s} [{tipo}] "
            f"RVOL×{vol['ratio']:.1f} RSI={rsi_val:.0f} "
            f"score={score} SL={sl:.6f} TP={tp:.6f} RR={rr:.2f}"
        )

        return {
            "par": par, "lado": lado, "precio": precio,
            "sl":  round(sl,  8), "tp":  round(tp,  8),
            "tp1": round(tp1, 8), "tp2": round(tp,  8),
            "atr": round(atr, 8), "dist_sl": round(dist, 8),
            "score": score, "rsi": rsi_val, "rr": round(rr, 2),
            "motivos": motivos, "kz": kz["nombre"],
            "htf": "NEUTRAL", "htf_4h": "NEUTRAL",
            "purga_nivel": tipo, "purga_peso": score,
            "vol_ratio": vol["ratio"],
            "bsl_h1": 0.0, "ssl_h1": 0.0, "bsl_h4": 0.0,
            "ssl_h4": 0.0, "bsl_d": 0.0,  "ssl_d": 0.0,
            "ema_r": 0.0,  "ema_l": 0.0,  "vwap": 0.0,
            "sobre_vwap": False, "fvg_top": 0, "fvg_bottom": 0,
            "fvg_rellenado": True, "ob_bull": False, "ob_bear": False,
            "ob_fvg_bull": False, "ob_fvg_bear": False, "ob_mitigado": True,
            "bos_bull": lado=="LONG", "bos_bear": lado=="SHORT",
            "choch_bull": flip_bull, "choch_bear": flip_bear,
            "sweep_bull": sweep=="LONG", "sweep_bear": sweep=="SHORT",
            "patron": None, "vela_conf": True,
            "premium": False, "discount": False,
            "displacement": False, "macd_hist": 0,
            "asia_valido": True, "adx": 25.0, "inducement": False,
            "liq_bull": wick=="LONG", "liq_bear": wick=="SHORT",
            "liq_z_up": vol["ratio"], "liq_z_dn": vol["ratio"],
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
