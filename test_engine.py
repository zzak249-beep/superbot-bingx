"""Pruebas del motor y del examen: sin lookahead, ruido sin ventaja, tendencia plantada detectada."""
import numpy as np
import pandas as pd

import backtest
from engine import donchian_state

OUT = "out_test"


def synth(n_coins=24, days=1600, trend=False, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=days, freq="D", tz="UTC")
    frames = {}
    for i in range(n_coins):
        vol = rng.uniform(0.03, 0.06)
        eps = rng.standard_normal(days) * vol
        if trend:  # deriva persistente (regímenes de tendencia) que el Donchian debería capturar
            mu = np.zeros(days)
            for t in range(1, days):
                mu[t] = 0.985 * mu[t - 1] + rng.standard_normal() * vol * 0.03
            eps = eps + mu
        c = 10 * np.exp(np.cumsum(eps))
        start = rng.integers(0, 500)
        df = pd.DataFrame(dict(open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1.0,
                               quote_vol=rng.lognormal(16 + i * 0.05, 0.3, days)), index=idx).iloc[start:]
        frames[f"C{i}USDT" if i else "BTCUSDT"] = df
    return frames


def test_no_lookahead():
    c = np.r_[np.full(10, 100.0), np.linspace(100, 130, 31)[1:], np.linspace(130, 100, 31)[1:]]
    s1, _ = donchian_state(c, (5, 10))
    c2 = c.copy(); c2[50:] = 500
    s2, _ = donchian_state(c2, (5, 10))
    assert np.allclose(s1[:50], s2[:50])


def test_noise():
    r = backtest.main(["--out", OUT + "_ruido", "--seeds", "3", "--no-variants"], frames_override=synth(trend=False))
    print("RUIDO: Sharpe", round(r["stats"]["sharpe"], 2), "alfa t", round(r["alpha_t"], 2), "veredicto", r["verdict"])
    assert r["verdict"] <= 2, "encuentra ventaja en ruido puro"


def test_planted_trend():
    r = backtest.main(["--out", OUT + "_tendencia", "--seeds", "3", "--no-variants"], frames_override=synth(trend=True, seed=7))
    print("TENDENCIA: Sharpe", round(r["stats"]["sharpe"], 2), "vs comprado", round(r["stats_bh"]["sharpe"], 2),
          "alfa t", round(r["alpha_t"], 2), "ruido", np.round(r["noise"], 2), "veredicto", r["verdict"])
    assert r["alpha_t"] >= 2 and r["stats"]["sharpe"] > np.nanmax(r["noise"]), "no detecta tendencia real"


if __name__ == "__main__":
    test_no_lookahead()
    test_noise()
    test_planted_trend()
    print("MOTOR OK")
