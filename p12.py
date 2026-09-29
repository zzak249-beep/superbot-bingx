"""
Motor P12 — la MISMA lógica que p12_hunter_v4_senales.pine, en Python.

Sesión = 18:00 → 18:00 hora de Nueva York (con horario de verano). La fecha de la
sesión es la del día de la apertura de las 09:30. Minuto de sesión m: 0 = 18:00 NY.

  Asia      m   0-510   (18:00-02:30)
  Londres   m 510-720   (02:30-06:00)      P12 = Asia + Londres
  Lectura   m 720-900   (06:00-09:00)
  Apertura  m 930       (09:30)
  Entrada   m 930-1080  (09:30-12:00)
  Cierre    m 1315      (15:55, sale en la apertura de la vela siguiente)

Diferencias conocidas con TradingView (todas conservadoras o neutras):
  · Si en la misma vela se tocan stop y objetivo, se cuenta el STOP.
  · ATR de Wilder con suavizado exponencial desde la primera vela (TV siembra con SMA).
  · Solo modo de entrada "Confirmación 5m" (el de por defecto).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

NY = "America/New_York"

# bloques del panel de horas: nombre, inicio, fin (minuto de sesión)
BLOCKS = [
    ("Asia (P12)", 0, 510),
    ("Londres (P12)", 510, 720),
    ("Lectura 06-09", 720, 900),
    ("09:00-09:30", 900, 930),
    ("NY 09:30-12", 930, 1080),
    ("NY 12-16", 1080, 1320),
    ("16-18", 1320, 1440),
]


@dataclass
class Params:
    mp_n: int = 2                    # periodos de 30 min cerrando fuera = aceptación
    night_filt: str = "excl_both"    # none | excl_both | same_aligned
    coinc_mode: str = "mid"          # mid | outside
    mismatch: str = "reduce"         # reduce | discard
    reduce_f: float = 0.5
    btc_filt: str = "off"            # off | not_against | same
    mo_filt: str = "off"             # off | favor | against
    width_filt: str = "off"          # off | excl_narrow | excl_wide | only_normal
    w_look: int = 20
    w_narrow: float = 0.75
    w_wide: float = 1.33
    atr_len: int = 14
    stop_buf: float = 0.25           # ATR más allá del mid
    rr: float = 2.0
    cost_side: float = 0.06          # % comisión por lado (con deslizamiento)
    max_cost_r: float = 0.20
    max_stop_pct: float = 2.5
    fund_pct: float = 0.01           # % por liquidación de funding (se asume que se paga)
    fund_h: int = 8
    skip_we: bool = True
    entry_end: int = 1080            # 12:00 NY
    exit_m: int = 1315               # 15:55 NY

    def to_dict(self):
        return asdict(self)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """df: índice DatetimeIndex UTC, columnas open high low close volume."""
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")].copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    ny = df.index.tz_convert(NY)
    mins = ny.hour * 60 + ny.minute
    df["m"] = (mins - 1080) % 1440
    df["sdate"] = (ny + pd.Timedelta(hours=6)).date
    df["t_ms"] = df.index.as_unit("ms").asi8  # independiente de la resolución (ns/us) de pandas
    return df


def add_atr(df: pd.DataFrame, n: int) -> pd.DataFrame:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1.0 / n, adjust=False).mean()
    return df


def btc_positions(btc: pd.DataFrame) -> dict:
    """Posición de BTC respecto a SU P12 al cierre de la última vela de lectura (08:55 NY)."""
    out = {}
    for sd, g in btc.groupby("sdate", sort=True):
        m = g["m"].to_numpy()
        p12 = m < 720
        rd = (m >= 720) & (m < 900)
        if p12.sum() < 50 or rd.sum() == 0:
            continue
        h = g["high"].to_numpy()[p12].max()
        lo = g["low"].to_numpy()[p12].min()
        c = g["close"].to_numpy()[rd][-1]
        out[sd] = 1 if c > h else -1 if c < lo else 0
    return out


def surrogate(df: pd.DataFrame, seed: int = 0) -> pd.DataFrame:
    """Serie de CONTROL con la misma volatilidad por hora del día que el símbolo, pero sin
    ninguna estructura de sesión: baraja los retornos de 5m entre días dentro de cada franja
    horaria (UTC) y reconstruye el precio. Si una afirmación del hilo acierta igual aquí,
    es geometría del rango, no información."""
    rng = np.random.default_rng(seed)
    d = df[["open", "high", "low", "close", "volume"]].copy()
    ret = np.log(d["close"]).diff().fillna(0.0).to_numpy()
    top = np.log(d["high"] / np.maximum(d["open"], d["close"])).to_numpy()
    bot = np.log(np.minimum(d["open"], d["close"]) / d["low"]).to_numpy()
    slot = (d.index.hour * 60 + d.index.minute).to_numpy()
    perm = np.arange(len(d))
    for s in np.unique(slot):
        ix = np.where(slot == s)[0]
        perm[ix] = rng.permutation(ix)
    ret, top, bot = ret[perm], top[perm], bot[perm]
    close = d["close"].iloc[0] * np.exp(np.cumsum(ret))
    op = np.r_[d["close"].iloc[0], close[:-1]]   # continua: abre donde cerró la anterior
    hi = np.maximum(op, close) * np.exp(top)
    lo = np.minimum(op, close) / np.exp(bot)
    return pd.DataFrame(dict(open=op, high=hi, low=lo, close=close, volume=d["volume"].to_numpy()), index=d.index)


def _block(m: int) -> int:
    for i, (_, s, e) in enumerate(BLOCKS):
        if s <= m < e:
            return i
    return len(BLOCKS) - 1


def analyze(df: pd.DataFrame, p: Params, btc_pos: dict | None = None, symbol: str = "") -> tuple[list, list]:
    """Devuelve (días, operaciones). df ya pasado por prepare() y add_atr()."""
    days, trades = [], []
    widths = deque(maxlen=p.w_look)
    tfm = int(round(np.median(np.diff(df["t_ms"].to_numpy()[:500])) / 60000)) if len(df) > 1 else 5
    per_sess = 1440 // max(tfm, 1)
    fund_ms = p.fund_h * 3600_000

    for sd, g in df.groupby("sdate", sort=True):
        if len(g) < per_sess * 0.85:
            continue  # sesión incompleta (hueco de datos o primer/último día)
        m = g["m"].to_numpy()
        o = g["open"].to_numpy()
        h = g["high"].to_numpy()
        lo = g["low"].to_numpy()
        c = g["close"].to_numpy()
        t = g["t_ms"].to_numpy()
        atr = g["atr"].to_numpy()

        asia = m < 510
        lon = (m >= 510) & (m < 720)
        p12 = m < 720
        rdm = (m >= 720) & (m < 900)
        if asia.sum() == 0 or lon.sum() == 0 or rdm.sum() == 0 or not (m >= 930).any():
            continue
        wd = pd.Timestamp(sd).weekday()
        weekend = wd >= 5

        # ── noche
        aO, aC = o[asia][0], c[asia][-1]
        aH, aL = h[asia].max(), lo[asia].min()
        lO, lC = o[lon][0], c[lon][-1]
        lH, lL = h[lon].max(), lo[lon].min()
        pH, pL = h[p12].max(), lo[p12].min()
        pM = (pH + pL) / 2
        aDir, lDir = int(np.sign(aC - aO)), int(np.sign(lC - lO))
        both = lH > aH and lL < aL
        scen = 3 if both else 1 if (aDir == lDir and aDir != 0) else 2
        nDir = aDir if scen == 1 else 0

        # ── ancho contra la mediana (solo laborables)
        w = pH - pL
        wr = (w / np.median(widths)) if len(widths) >= 5 and np.median(widths) > 0 else np.nan
        wB = 1 if np.isnan(wr) else 0 if wr < p.w_narrow else 2 if wr > p.w_wide else 1
        if not (p.skip_we and weekend):
            widths.append(w)

        # ── lectura: aceptación Market Profile (cierres en frontera de 30 min)
        cntH = cntL = 0
        accH = accL = annH = annL = False
        for k in np.where(rdm)[0]:
            if (m[k] + 1080 + tfm) % 30 == 0:  # la vela cierra en :00 o :30
                cntH = cntH + 1 if c[k] > pH else 0
                cntL = cntL + 1 if c[k] < pL else 0
                accH |= cntH >= p.mp_n
                accL |= cntL >= p.mp_n
            if h[k] > pH and c[k] <= pH:
                annH = True
            if lo[k] < pL and c[k] >= pL:
                annL = True
        rd = 0 if (accH and accL) else 1 if accH else -1 if accL else 0

        # ── medianoche, previos a la apertura, apertura
        mo_idx = np.where(m == 360)[0]
        moPx = o[mo_idx[0]] if len(mo_idx) else np.nan
        pre = m < 930
        preH, preL = h[pre].max(), lo[pre].min()
        oi = int(np.where(m >= 930)[0][0])
        opx = o[oi]
        btcRd = (btc_pos or {}).get(sd, 0)
        moFav = False if np.isnan(moPx) else (opx > moPx if rd == 1 else opx < moPx if rd == -1 else False)

        nightOk = True if p.night_filt == "none" else (scen != 3) if p.night_filt == "excl_both" else (scen == 1 and nDir == rd)
        btcOk = True if (p.btc_filt == "off" or btc_pos is None) else (btcRd != -rd) if p.btc_filt == "not_against" else (btcRd == rd)
        moOk = True if (p.mo_filt == "off" or np.isnan(moPx)) else moFav if p.mo_filt == "favor" else not moFav
        wOk = {"off": True, "excl_narrow": wB != 0, "excl_wide": wB != 2, "only_normal": wB == 1}[p.width_filt]
        bias = rd if (rd != 0 and nightOk and btcOk and moOk and wOk) else 0

        zEdge = zDeep = stopPx = np.nan
        coinc = False
        if bias == 1:
            zEdge, zDeep, stopPx = pH, pM, pM - p.stop_buf * atr[oi]
            coinc = opx >= pM if p.coinc_mode == "mid" else opx > pH
        elif bias == -1:
            zEdge, zDeep, stopPx = pL, pM, pM + p.stop_buf * atr[oi]
            coinc = opx <= pM if p.coinc_mode == "mid" else opx < pL
        szMult = 0.0 if bias == 0 else 1.0 if coinc else (0.0 if p.mismatch == "discard" else p.reduce_f)

        # ── estadística de NY (09:30 → 18:00) y bloque del extremo del día
        nyk = m >= 930
        nyH, nyL, nyC = h[nyk].max(), lo[nyk].min(), c[-1]
        dHiB, dLoB = _block(int(m[int(np.argmax(h))])), _block(int(m[int(np.argmin(lo))]))

        c06 = c[p12][-1]            # cierre al acabar el P12 (06:00)
        c09 = c[rdm][-1]            # cierre al acabar la lectura (09:00)
        day = dict(symbol=symbol, date=sd, weekend=weekend, scen=scen, nDir=nDir, rd=rd,
                   aboveAt9=bool(c09 > pH), belowAt9=bool(c09 < pL), upperAt6=bool(c06 > pM),
                   accH=accH, accL=accL, annH=annH, annL=annL, pH=pH, pL=pL, pM=pM,
                   width_ratio=wr, wB=wB, moPx=moPx, moFav=moFav, btcRd=btcRd, opx=opx,
                   bias=bias, coinc=coinc, szMult=szMult,
                   lowPre=bool(preL <= nyL), highPre=bool(preH >= nyH),
                   extNY=bool(nyL < preL or nyH > preH), insideClose=bool(pL <= nyC <= pH),
                   dHiB=dHiB, dLoB=dLoB, rejCost=False, rejStop=False, dead=False, traded=False)

        # ── simulación de la operación (una al día, confirmación en 5m)
        if bias != 0 and szMult > 0 and not (p.skip_we and weekend):
            k = oi
            n = len(m)
            while k < n and m[k] < p.entry_end:
                if (bias == 1 and c[k] < stopPx) or (bias == -1 and c[k] > stopPx):
                    day["dead"] = True
                    break
                sig = (bias == 1 and lo[k] <= zEdge and lo[k] > stopPx and c[k] > o[k] and c[k] > zDeep) or \
                      (bias == -1 and h[k] >= zEdge and h[k] < stopPx and c[k] < o[k] and c[k] < zDeep)
                if sig:
                    ent = c[k]
                    rU = abs(ent - stopPx)
                    if rU <= 0:
                        k += 1
                        continue
                    costR = 2 * p.cost_side / 100 * ent / rU
                    if costR > p.max_cost_r:
                        day["rejCost"] = True
                        k += 1
                        continue
                    if rU / ent * 100 > p.max_stop_pct:
                        day["rejStop"] = True
                        k += 1
                        continue
                    if k + 1 >= n:
                        break
                    tr = _run_trade(k + 1, bias, stopPx, p, m, o, h, lo, c, t, fund_ms)
                    if tr is not None:
                        tr.update(symbol=symbol, date=sd, costR_signal=costR, coinc=coinc, szMult=szMult,
                                  scen=scen, wB=wB, btc_aligned=(btcRd == bias) if btcRd != 0 else None,
                                  mo_fav=moFav if not np.isnan(moPx) else None)
                        trades.append(tr)
                        day["traded"] = True
                    break
                k += 1
        days.append(day)
    return days, trades


def _run_trade(fi, bias, stopPx, p, m, o, h, lo, c, t, fund_ms):
    """Entra a la apertura de la vela fi. Salida por stop, objetivo o cierre forzado."""
    n = len(m)
    ap = o[fi]
    rU = abs(ap - stopPx)
    if rU <= 0:
        return None
    # hueco que ya sobrepasa el stop al abrir
    if (bias == 1 and ap <= stopPx) or (bias == -1 and ap >= stopPx):
        return None
    tgt = ap + bias * p.rr * rU
    mfe = 0.0
    xp = reason = None
    xt = t[fi]
    for j in range(fi, n):
        fav = (h[j] - ap) if bias == 1 else (ap - lo[j])
        hitS = lo[j] <= stopPx if bias == 1 else h[j] >= stopPx
        hitT = h[j] >= tgt if bias == 1 else lo[j] <= tgt
        if hitS:  # conservador: si toca los dos en la misma vela, cuenta el stop
            xp, reason, xt = stopPx, "SL", t[j]
            mfe = max(mfe, 0.0 if hitT else fav / rU)
            break
        mfe = max(mfe, fav / rU)
        if hitT:
            xp, reason, xt = tgt, "TP", t[j]
            break
        if m[j] >= p.exit_m:
            if j + 1 < n:
                xp, reason, xt = o[j + 1], "tiempo", t[j + 1]
            else:
                xp, reason, xt = c[j], "tiempo", t[j]
            break
    if xp is None:
        xp, reason, xt = c[-1], "fin_datos", t[-1]
    r = bias * (xp - ap) / rU - p.cost_side / 100 * (ap + xp) / rU
    nf = int(xt // fund_ms - t[fi] // fund_ms)
    r -= nf * p.fund_pct / 100 * ap / rU
    return dict(dir=bias, entry=ap, stop=stopPx, target=tgt, exit=xp, reason=reason, r=r, mfe=mfe,
                entry_time=pd.Timestamp(t[fi], unit="ms", tz="UTC"),
                exit_time=pd.Timestamp(xt, unit="ms", tz="UTC"), fundings=nf,
                stop_pct=rU / ap * 100)
