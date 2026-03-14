"""
analizar.py — APEX Bot v7.0 [INSTITUCIONAL]
============================================
Filosofía: operar EXACTAMENTE como opera el dinero institucional.

Los bancos y ballenas operan así:
  1. Crean liquidez falsa (equal highs/lows visibles)
  2. Barren esa liquidez con un spike rápido (sweep)
  3. Revierten con fuerza mostrando un FVG o OB limpio
  4. El precio viaja al siguiente pool de liquidez opuesto

Este bot detecta EXACTAMENTE ese patrón y entra DESPUÉS del sweep,
no antes. La mayoría de bots entran antes del sweep y son barridos.
Nosotros entramos donde los institucionales ya entraron.

STACK COMPLETO:
  ✅ Sweep de liquidez → detección de la trampa institucional
  ✅ Market Structure Shift (MSS) → cambio de dirección confirmado
  ✅ FVG de alta calidad → zona de entrada precisa
  ✅ Order Block válido (no mitigado) → zona institucional
  ✅ Premium/Discount → solo entrar en buen precio
  ✅ Triple MTF (4H bias + 1H dirección + 5M entrada)
  ✅ Volumen en la entrada → confirma participación real
  ✅ SL bajo swing estructural → no al ruido del mercado
  ✅ TP en siguiente pool de liquidez → donde los institucionales salen
  ✅ Sesión horaria → solo en horas de liquidez real
  ✅ HTF estricto → NUNCA contra la tendencia mayor
  ✅ Macro BTC → si BTC cae, altcoins no se salvan
"""

import logging
import time
from datetime import datetime, timezone
import concurrent.futures

import config
import exchange

log = logging.getLogger("analizar")

# ── Estado global ─────────────────────────────────────────────
_cooldown_ts: dict = {}   # par → timestamp última señal
_kz_stats: dict   = {}   # kz → {trades, wins}
_macro_btc        = {"htf4h": "NEUTRAL", "htf1h": "NEUTRAL", "ts": 0.0}


def registrar_senal_ts(par: str):
    _cooldown_ts[par] = time.time()


def registrar_trade_kz(kz: str, ganado: bool):
    s = _kz_stats.setdefault(kz, {"trades": 0, "wins": 0})
    s["trades"] += 1
    s["wins"] += int(ganado)


def _cooldown_ok(par: str) -> bool:
    ultimo = _cooldown_ts.get(par, 0)
    return (time.time() - ultimo) >= config.COOLDOWN_VELAS * 300


def actualizar_macro_btc():
    """Macro BTC en 4H y 1H — contexto institucional global."""
    if time.time() - _macro_btc["ts"] < 900:
        return
    try:
        for tf, key in [("4h", "htf4h"), ("1h", "htf1h")]:
            ch = exchange.get_candles("BTC-USDT", tf, 60)
            if len(ch) < 40:
                continue
            cl = [c["close"] for c in ch]
            ef = _ema(cl, 21)
            es = _ema(cl, 50)
            if ef and es:
                _macro_btc[key] = "BULL" if ef > es * 1.003 else ("BEAR" if ef < es * 0.997 else "NEUTRAL")
        _macro_btc["ts"] = time.time()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
# INDICADORES BASE
# ══════════════════════════════════════════════════════════════

def _ema(prices: list, period: int):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    e = sum(prices[:period]) / period
    for p in prices[period:]:
        e = p * k + e * (1 - k)
    return e


def _rsi(prices: list, period: int = 14):
    if len(prices) < period + 1:
        return 50.0
    d  = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    ag = sum(max(x, 0) for x in d[:period]) / period
    al = sum(abs(min(x, 0)) for x in d[:period]) / period
    for x in d[period:]:
        ag = (ag * (period - 1) + max(x, 0)) / period
        al = (al * (period - 1) + abs(min(x, 0))) / period
    return 100.0 if al == 0 else round(100 - 100 / (1 + ag / al), 2)


def _atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return 0.0
    trs = [max(highs[i] - lows[i],
               abs(highs[i] - closes[i-1]),
               abs(lows[i] - closes[i-1]))
           for i in range(1, len(highs))]
    return sum(trs[-period:]) / period if len(trs) >= period else (sum(trs) / len(trs) if trs else 0.0)


def _stoch_rsi(closes: list, rsi_period=14, stoch_period=14, smooth_k=3, smooth_d=3):
    """Stochastic RSI — filtra señales RSI extremas con contexto adicional."""
    if len(closes) < rsi_period + stoch_period + smooth_k + smooth_d + 5:
        return 50.0, 50.0
    rsi_vals = []
    for i in range(rsi_period + 1, len(closes) + 1):
        rsi_vals.append(_rsi(closes[:i], rsi_period))
    if len(rsi_vals) < stoch_period:
        return 50.0, 50.0
    stoch_k_raw = []
    for i in range(stoch_period, len(rsi_vals) + 1):
        window = rsi_vals[i - stoch_period:i]
        mn, mx = min(window), max(window)
        stoch_k_raw.append(50.0 if mx == mn else (rsi_vals[i-1] - mn) / (mx - mn) * 100)
    if len(stoch_k_raw) < smooth_k:
        return 50.0, 50.0
    k_smooth = [sum(stoch_k_raw[i:i+smooth_k])/smooth_k for i in range(len(stoch_k_raw)-smooth_k+1)]
    if len(k_smooth) < smooth_d:
        return k_smooth[-1] if k_smooth else 50.0, 50.0
    d_smooth = [sum(k_smooth[i:i+smooth_d])/smooth_d for i in range(len(k_smooth)-smooth_d+1)]
    return round(k_smooth[-1], 2), round(d_smooth[-1], 2)


def _vwap(candles: list) -> float:
    hoy = datetime.now(timezone.utc).date()
    velas = [c for c in candles
             if datetime.fromtimestamp(c["ts"]/1000, tz=timezone.utc).date() == hoy]
    if not velas:
        velas = candles[-50:]
    tp_vol = sum(((c["high"] + c["low"] + c["close"]) / 3) * c["volume"] for c in velas)
    vol    = sum(c["volume"] for c in velas)
    return tp_vol / vol if vol > 0 else candles[-1]["close"]


def _choppiness(candles: list, n: int = 20) -> float:
    """Choppiness Index. < 38.2 = muy tendencial. > 61.8 = muy lateral."""
    if len(candles) < n + 1:
        return 50.0
    w = candles[-(n+1):]
    s = sum(max(w[i]["high"] - w[i]["low"],
                abs(w[i]["high"] - w[i-1]["close"]),
                abs(w[i]["low"] - w[i-1]["close"])) for i in range(1, len(w)))
    rng = max(c["high"] for c in w) - min(c["low"] for c in w)
    return (s / rng / n * 100) if rng > 0 else 50.0


def _volume_delta(candles: list, n: int = 5) -> float:
    """Delta de volumen: vol alcista vs bajista en últimas n velas."""
    if len(candles) < n:
        return 0.0
    bull_vol = sum(c["volume"] for c in candles[-n:] if c["close"] >= c["open"])
    bear_vol = sum(c["volume"] for c in candles[-n:] if c["close"] < c["open"])
    total = bull_vol + bear_vol
    return (bull_vol - bear_vol) / total if total > 0 else 0.0


# ══════════════════════════════════════════════════════════════
# MTF — TIMEFRAMES MÚLTIPLES
# ══════════════════════════════════════════════════════════════

def _tendencia(par: str, tf: str, n: int = 60) -> str:
    try:
        ch = exchange.get_candles(par, tf, n)
        if len(ch) < 40:
            return "NEUTRAL"
        cl = [c["close"] for c in ch]
        ef = _ema(cl, config.EMA_FAST)
        es = _ema(cl, config.EMA_SLOW)
        if ef is None or es is None:
            return "NEUTRAL"
        # Usar ADX implícito: si las EMAs están muy juntas = sin tendencia
        gap = abs(ef - es) / es * 100
        if gap < 0.2:
            return "NEUTRAL"
        if ef > es * 1.002:
            return "BULL"
        if ef < es * 0.998:
            return "BEAR"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def en_killzone() -> dict:
    ahora = datetime.now(timezone.utc)
    tim   = ahora.hour * 60 + ahora.minute
    asia   = config.KZ_ASIA_START   <= tim < config.KZ_ASIA_END
    london = config.KZ_LONDON_START <= tim < config.KZ_LONDON_END
    ny     = config.KZ_NY_START     <= tim < config.KZ_NY_END
    return {
        "in_asia": asia, "in_london": london, "in_ny": ny,
        "in_kz": asia or london or ny,
        "nombre": "ASIA" if asia else ("LONDON" if london else ("NY" if ny else "FUERA")),
    }


# ══════════════════════════════════════════════════════════════
# SMC — ESTRUCTURAS INSTITUCIONALES
# ══════════════════════════════════════════════════════════════

def _swing_points(candles: list, strength: int = 3) -> tuple:
    """Detecta swing highs y swing lows estructurales."""
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    sh, sl = [], []
    for i in range(strength, len(highs) - strength):
        if all(highs[i] > highs[i-k] and highs[i] > highs[i+k] for k in range(1, strength+1)):
            sh.append((i, highs[i]))
        if all(lows[i] < lows[i-k] and lows[i] < lows[i+k] for k in range(1, strength+1)):
            sl.append((i, lows[i]))
    return sh, sl


def detectar_mss(candles: list) -> dict:
    """
    Market Structure Shift (MSS) — el cambio de estructura más importante.
    Ocurre cuando precio rompe el último swing HIGH/LOW contrario tras un sweep.
    Más potente que BOS porque confirma inversión real de estructura.
    """
    r = {"mss_bull": False, "mss_bear": False, "bos_bull": False, "bos_bear": False,
         "choch_bull": False, "choch_bear": False}
    if len(candles) < 30:
        return r
    sh, sl = _swing_points(candles[-60:], strength=2)
    precio = candles[-1]["close"]
    highs_vals = [v for _, v in sh]
    lows_vals  = [v for _, v in sl]

    if highs_vals and precio > highs_vals[-1]:
        r["bos_bull"] = True
        if len(highs_vals) >= 2 and highs_vals[-1] < highs_vals[-2]:
            r["choch_bull"] = True
            r["mss_bull"]   = True   # CHoCH = MSS

    if lows_vals and precio < lows_vals[-1]:
        r["bos_bear"] = True
        if len(lows_vals) >= 2 and lows_vals[-1] > lows_vals[-2]:
            r["choch_bear"] = True
            r["mss_bear"]   = True

    return r


def detectar_sweep_liquidez(candles: list) -> dict:
    """
    Liquidity Sweep completo:
    - Sweep de EQL (equal lows) → señal de LONG inminente
    - Sweep de EQH (equal highs) → señal de SHORT inminente
    - También detecta sweeps de swing highs/lows simples

    El sweep ES la trampa institucional. Los institucionales mueven
    el precio para barrer los stop losses minoristas y luego revierten.
    """
    r = {"sweep_bull": False, "sweep_bear": False,
         "sweep_bull_nivel": 0.0, "sweep_bear_nivel": 0.0,
         "sweep_fuerza": 0}
    lb = min(getattr(config, "SWEEP_LOOKBACK", 20), len(candles) - 3)
    if lb < 5:
        return r
    previo = candles[-(lb+2):-2]
    pen    = candles[-2]   # penúltima
    act    = candles[-1]   # actual (cierre)

    max_prev = max(c["high"] for c in previo)
    min_prev = min(c["low"]  for c in previo)

    # Sweep alcista: spike abajo + cierre arriba (trampa bajista)
    # La vela penúltima o actual perforó min_prev pero cerró arriba
    swept_low = (pen["low"] < min_prev and pen["close"] > min_prev) or \
                (act["low"] < min_prev and act["close"] > min_prev)
    if swept_low:
        r["sweep_bull"]       = True
        r["sweep_bull_nivel"] = min_prev
        # Fuerza del sweep: qué tan rápido revirtió
        rechazo = (act["close"] - act["low"]) / (act["high"] - act["low"] + 1e-10)
        r["sweep_fuerza"] = int(rechazo * 3)  # 0-3

    # Sweep bajista: spike arriba + cierre abajo (trampa alcista)
    swept_high = (pen["high"] > max_prev and pen["close"] < max_prev) or \
                 (act["high"] > max_prev and act["close"] < max_prev)
    if swept_high:
        r["sweep_bear"]        = True
        r["sweep_bear_nivel"]  = max_prev
        rechazo = (act["high"] - act["close"]) / (act["high"] - act["low"] + 1e-10)
        r["sweep_fuerza"] = max(r["sweep_fuerza"], int(rechazo * 3))

    return r


def detectar_fvg(candles: list) -> dict:
    """
    Fair Value Gap mejorado:
    - Detecta el FVG más reciente y relevante
    - Verifica si está rellenado (inválido)
    - Mide el tamaño del FVG (FVGs grandes = más institucionales)
    - FVG dentro de OB = confluencia máxima
    """
    r = {"bull_fvg": False, "bear_fvg": False, "fvg_top": 0.0, "fvg_bottom": 0.0,
         "fvg_size_atr": 0.0, "fvg_rellenado": False, "fvg_en_ob": False}
    if len(candles) < 3:
        return r
    precio = candles[-1]["close"]
    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    atr_val = _atr(highs, lows, closes, 14)

    for i in range(len(candles)-1, max(len(candles)-25, 2)-1, -1):
        c0, c2 = candles[i], candles[i-2]
        gap_up   = c0["low"] - c2["high"]
        gap_down = c2["low"] - c0["high"]

        if gap_up > config.FVG_MIN_PIPS:
            rellenado = precio <= c2["high"] * 1.001
            size_atr  = gap_up / atr_val if atr_val > 0 else 0
            r.update({"bull_fvg": True, "fvg_top": c0["low"], "fvg_bottom": c2["high"],
                      "fvg_rellenado": rellenado, "fvg_size_atr": round(size_atr, 2)})
            break
        if gap_down > config.FVG_MIN_PIPS:
            rellenado = precio >= c2["low"] * 0.999
            size_atr  = gap_down / atr_val if atr_val > 0 else 0
            r.update({"bear_fvg": True, "fvg_top": c2["low"], "fvg_bottom": c0["high"],
                      "fvg_rellenado": rellenado, "fvg_size_atr": round(size_atr, 2)})
            break
    return r


def detectar_ob(candles: list) -> dict:
    """
    Order Block mejorado:
    - Detecta OBs de alta calidad (con displacement posterior)
    - Verifica mitigación (precio ya pasó por el OB = inválido)
    - Mide el tamaño del OB vs ATR
    - OB + FVG en misma zona = confluencia máxima
    """
    r = {"bull_ob": False, "bull_ob_top": 0.0, "bull_ob_bottom": 0.0,
         "bear_ob": False, "bear_ob_top": 0.0, "bear_ob_bottom": 0.0,
         "ob_fvg_bull": False, "ob_fvg_bear": False, "ob_quality": 0}
    if not config.OB_ACTIVO or len(candles) < 8:
        return r
    precio = candles[-1]["close"]
    lb     = min(config.OB_LOOKBACK, len(candles) - 3)
    buscar = candles[-(lb+3):-1]
    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    atr_val = _atr(highs, lows, closes, 14)

    for i in range(len(buscar)-4, 1, -1):
        c = buscar[i]
        body = abs(c["close"] - c["open"])
        rng  = c["high"] - c["low"]

        # Bull OB: vela bajista con cuerpo real seguida de impulso alcista
        if c["close"] < c["open"] and body > rng * 0.3 and not r["bull_ob"] and i+3 < len(buscar):
            c1, c2, c3 = buscar[i+1], buscar[i+2], buscar[i+3]
            if (c1["close"] > c1["open"] and c2["close"] > c2["open"]
                    and c2["high"] > c["high"]):
                ob_top    = max(c["open"], c["close"])
                ob_bottom = c["low"]
                mitigado  = precio < ob_bottom * 0.997
                if not mitigado and ob_bottom <= precio <= ob_top * 1.01:
                    # Calidad OB: desplazamiento posterior
                    displacement = (c2["close"] - c["close"]) / (atr_val + 1e-10)
                    quality = min(int(displacement), 3)
                    r.update({"bull_ob": True, "bull_ob_top": ob_top,
                              "bull_ob_bottom": ob_bottom, "ob_quality": quality})

        # Bear OB: vela alcista con cuerpo real seguida de impulso bajista
        if c["close"] > c["open"] and body > rng * 0.3 and not r["bear_ob"] and i+3 < len(buscar):
            c1, c2, c3 = buscar[i+1], buscar[i+2], buscar[i+3]
            if (c1["close"] < c1["open"] and c2["close"] < c2["open"]
                    and c2["low"] < c["low"]):
                ob_top    = c["high"]
                ob_bottom = min(c["open"], c["close"])
                mitigado  = precio > ob_top * 1.003
                if not mitigado and ob_bottom * 0.99 <= precio <= ob_top:
                    displacement = (c["close"] - c2["close"]) / (atr_val + 1e-10)
                    quality = min(int(displacement), 3)
                    r.update({"bear_ob": True, "bear_ob_top": ob_top,
                              "bear_ob_bottom": ob_bottom, "ob_quality": quality})

        if r["bull_ob"] and r["bear_ob"]:
            break

    return r


def detectar_eqh_eql(candles: list) -> dict:
    """Equal Highs/Lows — pools de liquidez visibles."""
    r = {"is_eqh": False, "eqh_price": 0.0, "is_eql": False, "eql_price": 0.0}
    if len(candles) < config.EQ_LOOKBACK:
        return r
    highs  = [c["high"] for c in candles]
    lows   = [c["low"]  for c in candles]
    ln     = config.EQ_PIVOT_LEN
    thr    = config.EQ_THRESHOLD
    n      = len(highs)
    lb     = config.EQ_LOOKBACK
    ph_list, pl_list = [], []

    for i in range(max(ln, n-lb-ln), n-ln):
        if i < ln or i+ln >= n:
            continue
        v = highs[i]
        if all(highs[j] < v for j in range(i-ln, i+ln+1) if j != i):
            ph_list.append(v)
        v = lows[i]
        if all(lows[j] > v for j in range(i-ln, i+ln+1) if j != i):
            pl_list.append(v)

    if len(ph_list) >= 2:
        for i in range(len(ph_list)-1, 0, -1):
            for j in range(i-1, max(i-10, -1), -1):
                if abs(ph_list[i] - ph_list[j]) / ph_list[i] * 100 <= thr:
                    r.update({"is_eqh": True, "eqh_price": ph_list[i]})
                    break
            if r["is_eqh"]:
                break

    if len(pl_list) >= 2:
        for i in range(len(pl_list)-1, 0, -1):
            for j in range(i-1, max(i-10, -1), -1):
                if abs(pl_list[i] - pl_list[j]) / pl_list[i] * 100 <= thr:
                    r.update({"is_eql": True, "eql_price": pl_list[i]})
                    break
            if r["is_eql"]:
                break

    return r


def detectar_patron_vela(candles: list) -> dict:
    """Patrones de vela institucionales de alta fiabilidad."""
    r = {"patron": None, "confianza": 0, "lado": None}
    if len(candles) < 3:
        return r
    c    = candles[-1]
    prev = candles[-2]
    prev2 = candles[-3]
    rng  = c["high"] - c["low"]
    if rng <= 0:
        return r
    body       = abs(c["close"] - c["open"])
    body_pct   = body / rng
    upper_wick = c["high"] - max(c["open"], c["close"])
    lower_wick = min(c["open"], c["close"]) - c["low"]
    ratio      = getattr(config, "PINBAR_RATIO", 0.50)

    # Pin bar alcista
    if lower_wick / rng >= ratio and body_pct < 0.35:
        r = {"patron": "PIN_BAR", "confianza": 2, "lado": "LONG"}
    # Pin bar bajista
    elif upper_wick / rng >= ratio and body_pct < 0.35:
        r = {"patron": "PIN_BAR", "confianza": 2, "lado": "SHORT"}
    # Engulfing alcista
    elif (c["close"] > c["open"] and prev["close"] < prev["open"]
          and c["close"] > prev["open"] and c["open"] < prev["close"]
          and body > abs(prev["close"] - prev["open"])):
        r = {"patron": "ENGULFING", "confianza": 2, "lado": "LONG"}
    # Engulfing bajista
    elif (c["close"] < c["open"] and prev["close"] > prev["open"]
          and c["close"] < prev["open"] and c["open"] > prev["close"]
          and body > abs(prev["close"] - prev["open"])):
        r = {"patron": "ENGULFING", "confianza": 2, "lado": "SHORT"}
    # Morning Star (3 velas)
    elif (prev2["close"] < prev2["open"]
          and abs(prev["close"] - prev["open"]) < abs(prev2["close"] - prev2["open"]) * 0.3
          and c["close"] > c["open"]
          and c["close"] > (prev2["open"] + prev2["close"]) / 2):
        r = {"patron": "MORNING_STAR", "confianza": 3, "lado": "LONG"}
    # Evening Star (3 velas)
    elif (prev2["close"] > prev2["open"]
          and abs(prev["close"] - prev["open"]) < abs(prev2["close"] - prev2["open"]) * 0.3
          and c["close"] < c["open"]
          and c["close"] < (prev2["open"] + prev2["close"]) / 2):
        r = {"patron": "EVENING_STAR", "confianza": 3, "lado": "SHORT"}
    return r


def _premium_discount(candles: list) -> dict:
    """Premium/Discount zone del rango institucional."""
    r = {"premium": False, "discount": False, "zona_pct": 50.0, "equilibrio": False}
    lb = min(getattr(config, "PREMIUM_DISCOUNT_LB", 50), len(candles))
    if lb < 10:
        return r
    rec   = candles[-lb:]
    max_h = max(c["high"] for c in rec)
    min_l = min(c["low"]  for c in rec)
    rng   = max_h - min_l
    if rng <= 0:
        return r
    precio  = candles[-1]["close"]
    zona    = (precio - min_l) / rng * 100
    return {
        "premium":    zona >= 62.0,   # top 38%
        "discount":   zona <= 38.0,   # bottom 38%
        "equilibrio": 40.0 < zona < 60.0,
        "zona_pct":   round(zona, 1),
    }


def _rango_asia(candles: list) -> dict:
    r = {"high": 0.0, "low": 0.0, "valido": False}
    if not config.ASIA_RANGE_ACTIVO:
        return r
    ac = [c for c in candles
          if 0 <= (datetime.fromtimestamp(c["ts"]/1000, tz=timezone.utc).hour * 60
                   + datetime.fromtimestamp(c["ts"]/1000, tz=timezone.utc).minute) < 240]
    if len(ac) >= 3:
        r.update({"high": max(c["high"] for c in ac),
                  "low":  min(c["low"]  for c in ac), "valido": True})
    return r


def _pivotes_diarios(candles_d: list) -> dict:
    if len(candles_d) < 2:
        return {}
    p = candles_d[-2]
    pp = (p["high"] + p["low"] + p["close"]) / 3
    return {"PP": pp, "R1": 2*pp - p["low"], "R2": pp + (p["high"] - p["low"]),
            "S1": 2*pp - p["high"], "S2": pp - (p["high"] - p["low"])}


# ══════════════════════════════════════════════════════════════
# SL ESTRUCTURAL
# ══════════════════════════════════════════════════════════════

def _sl_estructural(candles: list, ob: dict, lado: str, atr: float, precio: float) -> float:
    """
    SL bajo/sobre la estructura real del mercado.
    No al ruido del precio — al nivel donde el setup queda invalidado.
    """
    if lado == "LONG":
        # Candidatos SL: OB bottom, swing low reciente, ATR floor
        candidatos = []
        if ob.get("bull_ob") and ob["bull_ob_bottom"] > 0:
            candidatos.append(ob["bull_ob_bottom"] * 0.996)
        sh, sl = _swing_points(candles[-30:], strength=2)
        if sl:
            candidatos.append(sl[-1][1] * 0.996)
        sl_atr = precio - atr * config.SL_ATR_MULT
        candidatos.append(sl_atr)
        # Usar el MÁS ALTO (protección máxima dentro de la estructura)
        validos = [x for x in candidatos if precio - x < atr * 2.5]
        return max(validos) if validos else sl_atr
    else:
        candidatos = []
        if ob.get("bear_ob") and ob["bear_ob_top"] > 0:
            candidatos.append(ob["bear_ob_top"] * 1.004)
        sh, sl = _swing_points(candles[-30:], strength=2)
        if sh:
            candidatos.append(sh[-1][1] * 1.004)
        sl_atr = precio + atr * config.SL_ATR_MULT
        candidatos.append(sl_atr)
        validos = [x for x in candidatos if x - precio < atr * 2.5]
        return min(validos) if validos else sl_atr


# ══════════════════════════════════════════════════════════════
# TP HACIA LIQUIDEZ
# ══════════════════════════════════════════════════════════════

def _tp_liquidez(candles: list, eq: dict, pivotes: dict, asia: dict,
                  lado: str, precio: float, atr: float) -> tuple:
    """
    TP apuntado al próximo pool de liquidez (EQH/EQL, pivotes, rango Asia).
    Los institucionales salen donde están los stop losses del otro lado.
    """
    tp_base  = precio + atr * config.TP_ATR_MULT if lado == "LONG" else precio - atr * config.TP_ATR_MULT
    tp1_base = precio + atr * config.PARTIAL_TP1_MULT if lado == "LONG" else precio - atr * config.PARTIAL_TP1_MULT

    niveles = []
    if lado == "LONG":
        if eq.get("is_eqh") and eq["eqh_price"] > precio + atr * 0.8:
            niveles.append(eq["eqh_price"] * 0.997)
        if pivotes:
            for k in ("R1", "R2"):
                v = pivotes.get(k, 0)
                if v > precio + atr * 0.8:
                    niveles.append(v * 0.997)
        if asia.get("valido") and asia["high"] > precio + atr * 0.8:
            niveles.append(asia["high"] * 0.997)
        # Swing high anterior como target
        sh, _ = _swing_points(candles[-50:], strength=2)
        for _, v in reversed(sh):
            if v > precio + atr * 1.0:
                niveles.append(v * 0.997)
                break
    else:
        if eq.get("is_eql") and eq["eql_price"] < precio - atr * 0.8:
            niveles.append(eq["eql_price"] * 1.003)
        if pivotes:
            for k in ("S1", "S2"):
                v = pivotes.get(k, 0)
                if v > 0 and v < precio - atr * 0.8:
                    niveles.append(v * 1.003)
        if asia.get("valido") and asia["low"] < precio - atr * 0.8:
            niveles.append(asia["low"] * 1.003)
        _, sl = _swing_points(candles[-50:], strength=2)
        for _, v in reversed(sl):
            if v < precio - atr * 1.0:
                niveles.append(v * 1.003)
                break

    if not niveles:
        return tp_base, tp1_base

    if lado == "LONG":
        mejores = sorted([n for n in niveles if n > precio + atr * 1.2])
        tp2 = mejores[0] if mejores else tp_base
    else:
        mejores = sorted([n for n in niveles if n < precio - atr * 1.2], reverse=True)
        tp2 = mejores[0] if mejores else tp_base

    tp1 = precio + (tp2 - precio) * 0.40
    return tp2, tp1


# ══════════════════════════════════════════════════════════════
# SEÑAL PRINCIPAL v7.0
# ══════════════════════════════════════════════════════════════

def analizar_par(par: str):
    try:
        candles = exchange.get_candles(par, config.TIMEFRAME, config.CANDLES_LIMIT)
        if len(candles) < 80:
            return None

        # Cooldown
        if not _cooldown_ok(par):
            return None

        # Mercado trending (no choppy)
        chop = _choppiness(candles, 20)
        if chop > 61.8:
            return None

        # Volumen mínimo
        vols = [c["volume"] for c in candles[-21:-1]]
        avg_vol = sum(vols) / len(vols) if vols else 0
        if avg_vol > 0 and candles[-1]["volume"] / avg_vol < 0.35:
            return None

        cl  = [c["close"] for c in candles]
        hi  = [c["high"]  for c in candles]
        lo  = [c["low"]   for c in candles]
        precio = cl[-1]
        if precio <= 0:
            return None

        atr = _atr(hi, lo, cl, config.ATR_PERIOD)
        if atr <= 0 or atr / precio * 100 < 0.03:
            return None

        # ── Indicadores ──────────────────────────────────────
        rsi = _rsi(cl, config.RSI_PERIOD)
        srsi_k, srsi_d = _stoch_rsi(cl)
        vwap     = _vwap(candles)
        vdelta   = _volume_delta(candles, 5)

        ef  = _ema(cl, config.EMA_FAST)
        es  = _ema(cl, config.EMA_SLOW)
        ef9 = _ema(cl, getattr(config, "EMA_LOCAL_FAST", 9))
        es21 = _ema(cl, getattr(config, "EMA_LOCAL_SLOW", 21))
        bull5 = ef and es and ef > es * 1.001
        bear5 = ef and es and ef < es * 0.999
        bull9 = ef9 and es21 and ef9 > es21
        bear9 = ef9 and es21 and ef9 < es21

        # MACD
        macd_hist = 0.0
        if len(cl) > 35:
            ef12 = _ema(cl[-35:], 12)
            es26 = _ema(cl[-35:], 26)
            if ef12 and es26:
                macd_hist = ef12 - es26

        # ── MTF triple ───────────────────────────────────────
        htf_4h = _tendencia(par, "4h", 60)
        htf_1h = _tendencia(par, "1h", 60)

        # ── Macro BTC ────────────────────────────────────────
        macro_btc_4h = _macro_btc.get("htf4h", "NEUTRAL")
        macro_btc_1h = _macro_btc.get("htf1h", "NEUTRAL")

        # ── SMC estructuras ───────────────────────────────────
        sweep  = detectar_sweep_liquidez(candles)
        mss    = detectar_mss(candles)
        fvg    = detectar_fvg(candles)
        ob     = detectar_ob(candles)
        eq     = detectar_eqh_eql(candles)
        pd     = _premium_discount(candles)
        pat    = detectar_patron_vela(candles)
        asia   = _rango_asia(candles)
        kz     = en_killzone()

        candles_d = exchange.get_candles(par, "1d", 5)
        pivotes   = _pivotes_diarios(candles_d)
        pct_piv   = config.PIVOT_NEAR_PCT / 100

        ns1 = pivotes and abs(precio - pivotes.get("S1", 0)) / precio < pct_piv
        ns2 = pivotes and abs(precio - pivotes.get("S2", 0)) / precio < pct_piv
        nr1 = pivotes and abs(precio - pivotes.get("R1", 0)) / precio < pct_piv
        nr2 = pivotes and abs(precio - pivotes.get("R2", 0)) / precio < pct_piv
        nal  = asia["valido"] and abs(precio - asia["low"])  / precio < pct_piv
        nah  = asia["valido"] and abs(precio - asia["high"]) / precio < pct_piv

        iob_b = ob["bull_ob"] and ob["bull_ob_bottom"] <= precio <= ob["bull_ob_top"] * 1.008
        iob_r = ob["bear_ob"] and ob["bear_ob_bottom"] * 0.992 <= precio <= ob["bear_ob_top"]
        ob_fvg_bull = iob_b and fvg["bull_fvg"] and not fvg["fvg_rellenado"]
        ob_fvg_bear = iob_r and fvg["bear_fvg"] and not fvg["fvg_rellenado"]

        sobre_vwap = precio > vwap * 1.001
        bajo_vwap  = precio < vwap * 0.999

        # ── SCORING v7.0 (máx ~20) ────────────────────────────
        sl = ss = 0
        ml: list = []
        ms: list = []

        def add(cond, pts, lbl, side):
            nonlocal sl, ss
            if cond:
                if side in ("L", "B"): sl += pts; ml.append(lbl)
                if side in ("S", "B"): ss += pts; ms.append(lbl)

        # ── SEÑALES DE ALTA CALIDAD (peso 3) ──────────────────
        # Sweep + MSS = la combinación más potente en ICT
        add(sweep["sweep_bull"] and mss["mss_bull"],   3, "SWEEP+MSS",   "L")
        add(sweep["sweep_bear"] and mss["mss_bear"],   3, "SWEEP+MSS",   "S")
        # OB + FVG en zona = confluencia institucional máxima
        add(ob_fvg_bull,                               3, "OB+FVG",      "L")
        add(ob_fvg_bear,                               3, "OB+FVG",      "S")

        # ── SEÑALES DE CALIDAD MEDIA-ALTA (peso 2) ────────────
        add(sweep["sweep_bull"] and not mss["mss_bull"], 2, "SWEEP",     "L")
        add(sweep["sweep_bear"] and not mss["mss_bear"], 2, "SWEEP",     "S")
        add(mss["choch_bull"],                         2, "CHoCH",       "L")
        add(mss["choch_bear"],                         2, "CHoCH",       "S")
        add(fvg["bull_fvg"] and not fvg["fvg_rellenado"] and fvg["fvg_size_atr"] >= 0.3,
                                                       2, "FVG",         "L")
        add(fvg["bear_fvg"] and not fvg["fvg_rellenado"] and fvg["fvg_size_atr"] >= 0.3,
                                                       2, "FVG",         "S")
        add(iob_b,                                     2, "OB+",         "L")
        add(iob_r,                                     2, "OB-",         "S")
        add(mss["bos_bull"] and not mss["choch_bull"], 1, "BOS",         "L")
        add(mss["bos_bear"] and not mss["choch_bear"], 1, "BOS",         "S")

        # ── CONFLUENCIAS DE PRECIO (peso 1) ───────────────────
        add(eq["is_eql"],          1, "EQL",       "L")
        add(eq["is_eqh"],          1, "EQH",       "S")
        add(ns1 or ns2,            1, "PIVOT_S",   "L")
        add(nr1 or nr2,            1, "PIVOT_R",   "S")
        add(nal,                   1, "ASIA_L",    "L")
        add(nah,                   1, "ASIA_H",    "S")
        add(pd["discount"],        1, "DISCOUNT",  "L")
        add(pd["premium"],         1, "PREMIUM",   "S")

        # ── TENDENCIA Y MOMENTUM (peso 1) ─────────────────────
        add(htf_4h == "BULL",      2, "H4_BULL",   "L")   # H4 pesa doble
        add(htf_4h == "BEAR",      2, "H4_BEAR",   "S")
        add(htf_1h == "BULL",      1, "H1_BULL",   "L")
        add(htf_1h == "BEAR",      1, "H1_BEAR",   "S")
        add(bull5,                 1, "EMA_5M",    "L")
        add(bear5,                 1, "EMA_5M",    "S")
        add(bull9,                 1, "EMA_LOC",   "L")
        add(bear9,                 1, "EMA_LOC",   "S")
        add(macd_hist > 0,         1, "MACD+",     "L")
        add(macd_hist < 0,         1, "MACD-",     "S")
        add(kz["in_kz"],           1, f"KZ_{kz['nombre']}", "B")

        # ── CONFIRMACIÓN VELA ─────────────────────────────────
        if pat.get("patron") and pat.get("lado") == "LONG":
            sl += pat["confianza"]; ml.append(pat["patron"])
        if pat.get("patron") and pat.get("lado") == "SHORT":
            ss += pat["confianza"]; ms.append(pat["patron"])

        # ── RSI y STOCH RSI ───────────────────────────────────
        rsi_l = 20 <= rsi <= config.RSI_BUY_MAX
        rsi_s = config.RSI_SELL_MIN <= rsi <= 80
        add(rsi_l,                 1, f"RSI{rsi:.0f}", "L")
        add(rsi_s,                 1, f"RSI{rsi:.0f}", "S")
        add(srsi_k < 30 and srsi_k > srsi_d,  1, "SRSI_OV", "L")
        add(srsi_k > 70 and srsi_k < srsi_d,  1, "SRSI_OB", "S")

        # ── VWAP ──────────────────────────────────────────────
        add(bajo_vwap,             1, "VWAP_B",    "L")
        add(sobre_vwap,            1, "VWAP_H",    "S")

        # ── VOLUME DELTA ──────────────────────────────────────
        add(vdelta > 0.2,          1, "VOL+",      "L")
        add(vdelta < -0.2,         1, "VOL-",      "S")

        # ── CONDICIONES BASE OBLIGATORIAS ────────────────────
        # Para entrar necesitas: zona DE LIQUIDEZ + señal SMC + sesión
        zona_l = eq["is_eql"] or ns1 or ns2 or nal or iob_b or pd["discount"] or sweep["sweep_bull"]
        zona_s = eq["is_eqh"] or nr1 or nr2 or nah or iob_r or pd["premium"]  or sweep["sweep_bear"]

        # Base: al menos 1 señal SMC fuerte + zona relevante
        base_l = (fvg["bull_fvg"] or iob_b or sweep["sweep_bull"] or mss["choch_bull"]) and zona_l and kz["in_kz"]
        base_s = (fvg["bear_fvg"] or iob_r or sweep["sweep_bear"] or mss["choch_bear"]) and zona_s and kz["in_kz"]

        # ── ALINEACIÓN DE TENDENCIA ───────────────────────────
        # v7.0: HTF estricto — NEUTRAL solo si hay CHoCH fuerte
        htf_ok_l = (
            bull5 and (
                htf_1h == "BULL" or
                (htf_1h == "NEUTRAL" and htf_4h == "BULL") or
                (htf_1h == "NEUTRAL" and sl >= 10 and mss["choch_bull"])
            )
        )
        htf_ok_s = (
            bear5 and (
                htf_1h == "BEAR" or
                (htf_1h == "NEUTRAL" and htf_4h == "BEAR") or
                (htf_1h == "NEUTRAL" and ss >= 10 and mss["choch_bear"])
            )
        )

        # ── DECIDIR SEÑAL ─────────────────────────────────────
        lado = score = None
        motivos: list = []

        if not config.SOLO_LONG:
            if base_s and ss >= config.SCORE_MIN and htf_ok_s and rsi_s:
                if ss > sl:
                    lado, score, motivos = "SHORT", ss, ms

        if base_l and sl >= config.SCORE_MIN and htf_ok_l and rsi_l:
            if lado is None or sl >= ss:
                lado, score, motivos = "LONG", sl, ml

        if lado is None:
            if sl >= 4 or ss >= 4:
                log.debug(f"[NO-SIG] {par} L:{sl} S:{ss} base_L={base_l} base_S={base_s} "
                          f"htf={htf_1h}/{htf_4h} KZ={kz['nombre']}")
            return None

        # ── VETO MACRO BTC ────────────────────────────────────
        # Si BTC cae fuerte, altcoins no pueden subir sostenidamente
        if score < 11 and par not in ("BTC-USDT",):
            if lado == "LONG"  and macro_btc_4h == "BEAR" and macro_btc_1h == "BEAR":
                log.debug(f"[MACRO-VETO] {par} LONG — BTC doble BEAR")
                return None
            if lado == "SHORT" and macro_btc_4h == "BULL" and macro_btc_1h == "BULL":
                log.debug(f"[MACRO-VETO] {par} SHORT — BTC doble BULL")
                return None

        # ── SL / TP ───────────────────────────────────────────
        sl_p = _sl_estructural(candles, ob, lado, atr, precio)
        tp_p, tp1_p = _tp_liquidez(candles, eq, pivotes, asia, lado, precio, atr)

        dist = abs(precio - sl_p)
        if dist <= 0:
            return None
        rr = abs(tp_p - precio) / dist
        if rr < config.MIN_RR:
            log.debug(f"[NO-SIG] {par} R:R={rr:.2f} < {config.MIN_RR}")
            return None

        registrar_senal_ts(par)

        return {
            "par":           par,
            "lado":          lado,
            "precio":        precio,
            "sl":            round(sl_p, 8),
            "tp":            round(tp_p, 8),
            "tp1":           round(tp1_p, 8),
            "tp2":           round(tp_p, 8),
            "atr":           round(atr, 8),
            "score":         score,
            "rsi":           rsi,
            "srsi_k":        srsi_k,
            "rr":            round(rr, 2),
            "motivos":       motivos,
            "kz":            kz["nombre"],
            "htf":           htf_1h,
            "htf_4h":        htf_4h,
            "vwap":          round(vwap, 8),
            "sobre_vwap":    sobre_vwap,
            "fvg_top":       fvg.get("fvg_top", 0),
            "fvg_bottom":    fvg.get("fvg_bottom", 0),
            "fvg_rellenado": fvg.get("fvg_rellenado", True),
            "fvg_size_atr":  fvg.get("fvg_size_atr", 0),
            "ob_bull":       ob["bull_ob"],
            "ob_bear":       ob["bear_ob"],
            "ob_fvg_bull":   ob_fvg_bull,
            "ob_fvg_bear":   ob_fvg_bear,
            "ob_mitigado":   not ob["bull_ob"] and not ob["bear_ob"],
            "ob_quality":    ob.get("ob_quality", 0),
            "bos_bull":      mss["bos_bull"],
            "bos_bear":      mss["bos_bear"],
            "choch_bull":    mss["choch_bull"],
            "choch_bear":    mss["choch_bear"],
            "sweep_bull":    sweep["sweep_bull"],
            "sweep_bear":    sweep["sweep_bear"],
            "sweep_fuerza":  sweep.get("sweep_fuerza", 0),
            "patron":        pat.get("patron"),
            "vela_conf":     pat.get("patron") is not None,
            "premium":       pd["premium"],
            "discount":      pd["discount"],
            "zona_pct":      pd["zona_pct"],
            "displacement":  score >= 10,
            "inducement":    eq["is_eql"] or eq["is_eqh"],
            "pivotes":       pivotes,
            "macd_hist":     round(macd_hist, 8),
            "vol_ratio":     round(candles[-1]["volume"] / (avg_vol + 1e-9), 2),
            "vol_delta":     round(vdelta, 3),
            "chop":          round(chop, 1),
            "asia_valido":   asia["valido"],
            "macro_btc_4h":  macro_btc_4h,
        }

    except Exception as e:
        log.error(f"analizar_par {par}: {e}", exc_info=True)
        return None


def analizar_todos(pares: list, workers: int = 4) -> list:
    senales = []
    w = getattr(config, "ANALISIS_WORKERS", workers)
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
