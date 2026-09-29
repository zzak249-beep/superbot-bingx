"""Pruebas del motor: un día construido a mano con resultado conocido + una ejecución completa."""
import numpy as np
import pandas as pd

from p12 import Params, add_atr, analyze, prepare
import study


def _bars(start_ny, n, px, rng=0.1):
    idx = pd.date_range(start_ny, periods=n, freq="5min", tz="America/New_York").tz_convert("UTC")
    return pd.DataFrame(dict(open=px, high=px + rng, low=px - rng, close=px, volume=1.0), index=idx)


def build_day():
    # 3 sesiones planas de relleno (ATR) + sesión del martes 2026-09-15 construida
    frames = [_bars("2026-09-11 18:00", 288 * 3, 100.0)]  # vie 18:00 → lun 18:00 (incluye finde)
    d = _bars("2026-09-14 18:00", 288, 100.0)  # sesión con fecha martes 15
    m = np.arange(288) * 5  # minuto de sesión
    o = np.full(288, 100.0); h = o + 0.1; l = o - 0.1; c = o.copy()
    # P12: toca 101 y 99
    h[(m >= 100) & (m < 105)] = 101.0
    l[(m >= 600) & (m < 605)] = 99.0
    # lectura 06:00-09:00: todo por encima de 101 (acepta arriba)
    rd = (m >= 720) & (m < 930)
    o[rd] = 102; c[rd] = 102; h[rd] = 102.1; l[rd] = 101.9
    # apertura 09:30 en 102, vuelve al nivel a las 10:00 con vela verde
    ny = (m >= 930)
    o[ny] = 102; c[ny] = 102; h[ny] = 102.1; l[ny] = 101.9
    k = np.where(m == 960)[0][0]          # 10:00
    o[k], l[k], h[k], c[k] = 101.0, 100.9, 101.6, 101.5
    o[k + 1:] = 101.5; c[k + 1:] = 101.5; h[k + 1:] = 101.6; l[k + 1:] = 101.4
    j = np.where(m == 1020)[0][0]         # 11:00 sube a 106 → objetivo
    h[j:] = 106.0; c[j:] = 105.5; o[j:] = 105.5; l[j:] = 105.0
    d[["open", "high", "low", "close"]] = np.c_[o, h, l, c]
    tail = _bars("2026-09-15 18:00", 288, 105.5)
    df = pd.concat(frames + [d, tail])
    return df[~df.index.duplicated()]


def test_constructed_day():
    df = add_atr(prepare(build_day()), 14)
    days, trades = analyze(df, Params(), None, "TEST")
    day = [x for x in days if str(x["date"]) == "2026-09-15"][0]
    assert day["pH"] == 101.0 and day["pL"] == 99.0, day
    assert day["rd"] == 1 and day["accH"], day
    assert day["bias"] == 1 and day["coinc"], day
    assert len(trades) == 1, trades
    t = trades[0]
    assert t["dir"] == 1 and abs(t["entry"] - 101.5) < 1e-9, t
    assert t["reason"] == "TP", t
    assert 1.7 < t["r"] < 2.0, t          # 2R menos comisión
    # el fin de semana no se opera ni cuenta
    assert all(not x["traded"] for x in days if x["weekend"])
    print("día construido OK:", {k: round(v, 3) if isinstance(v, float) else v for k, v in t.items()
                                 if k in ("entry", "stop", "target", "exit", "reason", "r", "mfe")})


def synthetic(seed, n_days=200, vol=0.0012):
    rng = np.random.default_rng(seed)
    n = n_days * 288
    idx = pd.date_range("2025-09-01", periods=n, freq="5min", tz="UTC")
    # volatilidad intradía con pico a las 14-16 UTC, como BTC
    hr = idx.hour.to_numpy()
    sv = vol * (1 + 0.8 * np.exp(-((hr - 15) ** 2) / 6))
    ret = rng.standard_normal(n) * sv
    close = 100 * np.exp(np.cumsum(ret))
    op = np.r_[100, close[:-1]]
    wick = np.abs(rng.standard_normal(n)) * sv * close * 0.6
    df = pd.DataFrame(dict(open=op, close=close,
                           high=np.maximum(op, close) + wick, low=np.minimum(op, close) - wick,
                           volume=1.0), index=idx)
    return df


def test_full_run():
    frames = {f"S{i}USDT": synthetic(i) for i in range(6)}
    frames["BTCUSDT"] = synthetic(99)
    days, trades, report = study.main(["--out", "out_test_ruido"],
                                      frames_override=frames)
    assert len(days) > 1000 and len(trades) > 50
    assert "## Veredicto" in report
    # en un paseo aleatorio no debería haber edge
    E = trades["r"].mean()
    print(f"sintético: {len(trades)} ops, E {E:+.3f}R")




def planted(seed, n_days=250, vol=0.0012, mu=0.00035):
    """Paseo aleatorio + EFECTO P12 PLANTADO: si a las 09:00 NY el precio está fuera del P12,
    de 09:30 a 12:00 deriva en esa dirección. El estudio debe detectarlo."""
    rng = np.random.default_rng(seed)
    n = n_days * 288
    idx = pd.date_range("2025-09-01", periods=n, freq="5min", tz="UTC")
    meta = prepare(pd.DataFrame(dict(open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0), index=idx))
    m = meta["m"].to_numpy()
    sd = meta["sdate"].to_numpy()
    ret = rng.standard_normal(n) * vol
    close = np.empty(n)
    px, start, hi, lo, bias = 100.0, 0, -np.inf, np.inf, 0
    for i in range(n):
        if i > 0 and sd[i] != sd[i - 1]:
            hi, lo, bias = -np.inf, np.inf, 0
        r = ret[i]
        if 930 <= m[i] < 1080:
            r += bias * mu
        px *= np.exp(r)
        close[i] = px
        if m[i] < 720:
            hi, lo = max(hi, px), min(lo, px)
        if m[i] == 895:  # vela que cierra a las 09:00
            bias = 1 if px > hi else -1 if px < lo else 0
    op = np.r_[100.0, close[:-1]]
    wick = np.abs(rng.standard_normal(n)) * vol * close * 0.6
    return pd.DataFrame(dict(open=op, close=close, high=np.maximum(op, close) + wick,
                             low=np.minimum(op, close) - wick, volume=1.0), index=idx)


def test_detects_planted_edge():
    frames = {f"P{i}USDT": planted(i) for i in range(6)}
    frames["BTCUSDT"] = planted(99)
    days, trades, report = study.main(["--out", "out_test_plantado",
                                       "--no-variants"], frames_override=frames)
    E = trades["r"].mean()
    print(f"efecto plantado: {len(trades)} ops, E {E:+.3f}R")
    assert E > 0.15, "no detecta un efecto real plantado"
    assert "edge distinguible" in report, "el veredicto no reconoce el efecto"
    # el efecto depende de la POSICIÓN a las 09:00, no de aceptar con tiempo: la base
    # condicionada lo absorbe y la afirmación del hilo NO debe salir como "APORTA"
    assert "Acepta HIGH → el mínimo ya está hecho: APORTA" not in report


if __name__ == "__main__":
    test_constructed_day()
    test_full_run()
    test_detects_planted_edge()
    print("TODO OK")
