"""
EXAMEN del bot de tendencia. Responde tres preguntas antes de arriesgar dinero:

  1. ¿Gana después de comisiones, deslizamiento y FUNDING?
  2. ¿La señal de tendencia aporta algo sobre lo mismo pero SIEMPRE COMPRADO con el mismo
     escalado de volatilidad? (una réplica encontró que casi todo el mérito es del escalado)
  3. ¿Supera al RUIDO? (la misma estrategia sobre retornos barajados, sin tendencias reales)

  python backtest.py                         # 40 candidatas, ~5,5 años
  python backtest.py --days 1500 --n 10

Salida: consola + out/examen.md, out/equity.csv, out/variantes.csv (+ Telegram si hay token).
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from dataclasses import replace

import numpy as np
import pandas as pd
import requests

import data
from engine import Params, bh_weights, build_panel, target_weights

# candidatas: líquidas en perpetuos hoy + algunas deslistadas para reducir el sesgo de supervivencia
CANDIDATES = ("BTC,ETH,SOL,XRP,BNB,DOGE,ADA,AVAX,LINK,DOT,LTC,BCH,TRX,MATIC,POL,ATOM,NEAR,APT,ARB,OP,"
              "SUI,SEI,INJ,TIA,FIL,ETC,UNI,AAVE,MKR,LDO,RUNE,FTM,SAND,AXS,GALA,WIF,PEPE,TAO,ENA,"
              "LUNA,FTT,SRM,WAVES,EOS,XLM,HBAR,ICP,TON")

VARIANTS = [
    ("BASE (bot por defecto)", {}),
    ("Largos y cortos", dict(allow_short=True)),
    ("Vol objetivo 15%", dict(vol_target=0.15)),
    ("Vol objetivo 40%", dict(vol_target=0.40)),
    ("Solo lookbacks rápidos (5-60)", dict(lookbacks=(5, 10, 20, 30, 60))),
    ("Solo lookbacks lentos (90-360)", dict(lookbacks=(90, 150, 250, 360))),
    ("Cesta de 5", dict(n_coins=5)),
    ("Cesta de 20", dict(n_coins=20)),
    ("Sin banda de reajuste", dict(rebal_band=0.0)),
    ("Banda 5%", dict(rebal_band=0.05)),
    ("Costes ×2", dict(cost_side=0.0016)),
]


# ───────────────────────── simulación ─────────────────────────
def simulate(panel: dict, w: pd.DataFrame, fund: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Cartera día a día. Pesos decididos al cierre t se aplican al retorno t→t+1.
    Los pesos derivan con los precios y solo se reajustan si se salen de la banda."""
    close = panel["close"].to_numpy()
    W = w.to_numpy()
    F = fund.to_numpy()
    T, N = close.shape
    ret = np.zeros_like(close)
    ret[1:] = close[1:] / close[:-1] - 1
    ret = np.nan_to_num(ret, nan=0.0, posinf=0.0, neginf=0.0)
    h = np.zeros(N)
    out = np.zeros((T, 5))  # neto, bruto, coste, funding, exposición bruta
    for t in range(T):
        if t > 0:
            gross_r = float(h @ ret[t])
            fcost = float(np.sum(h * F[t]))          # largos pagan funding positivo
            port = gross_r - fcost
            if 1 + port > 0:
                h = h * (1 + ret[t]) / (1 + port)
            else:
                h = np.zeros(N)
        else:
            gross_r = fcost = port = 0.0
        tgt = np.nan_to_num(W[t])
        diff = tgt - h
        trade = (np.abs(diff) > p.rebal_band) | ((tgt == 0) & (h != 0)) | ((h == 0) & (np.abs(tgt) > 0) & (np.abs(diff) > p.rebal_band / 2))
        traded = np.abs(diff[trade]).sum()
        cost = traded * p.cost_side
        h = np.where(trade, tgt, h)
        net = (1 + port) * (1 - cost) - 1
        out[t] = [net, gross_r, cost, fcost, np.abs(h).sum()]
    return pd.DataFrame(out, index=panel["index"], columns=["net", "gross", "cost", "funding", "exposure"])


def stats(r: pd.Series) -> dict:
    r = r.dropna()
    if len(r) < 30:
        return dict(cagr=np.nan, vol=np.nan, sharpe=np.nan, maxdd=np.nan, t=np.nan, n=len(r))
    eq = (1 + r).cumprod()
    yrs = len(r) / 365
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if eq.iloc[-1] > 0 else -1
    vol = r.std() * math.sqrt(365)
    sharpe = r.mean() / r.std() * math.sqrt(365) if r.std() > 0 else np.nan
    dd = (eq / eq.cummax() - 1).min()
    t = r.mean() / (r.std() / math.sqrt(len(r))) if r.std() > 0 else np.nan
    return dict(cagr=cagr, vol=vol, sharpe=sharpe, maxdd=dd, t=t, n=len(r))


def alpha_vs(r: pd.Series, b: pd.Series) -> tuple[float, float, float]:
    """Regresión diaria r = a + beta·b. Devuelve (alfa anual, t de alfa, beta)."""
    df = pd.concat([r, b], axis=1).dropna()
    if len(df) < 60:
        return np.nan, np.nan, np.nan
    y, x = df.iloc[:, 0].to_numpy(), df.iloc[:, 1].to_numpy()
    X = np.c_[np.ones(len(x)), x]
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X @ coef
    s2 = res @ res / (len(y) - 2)
    cov = s2 * np.linalg.inv(X.T @ X)
    return coef[0] * 365, coef[0] / math.sqrt(cov[0, 0]), coef[1]


def shuffle_panel(panel: dict, seed: int) -> dict:
    """Control: baraja los retornos diarios de cada moneda (mismas magnitudes, mismo
    periodo de cotización, CERO tendencias persistentes)."""
    rng = np.random.default_rng(seed)
    close = panel["close"].copy()
    for s in close.columns:
        c = close[s]
        v = c.notna().to_numpy()
        if v.sum() < 3:
            continue
        lr = np.diff(np.log(c[v].to_numpy()))
        lr = rng.permutation(lr)
        close.loc[v, s] = c[v].iloc[0] * np.exp(np.r_[0, np.cumsum(lr)])
    return dict(index=panel["index"], close=close, qvol=panel["qvol"])


def fmt(x, f="{:+.2f}"):
    return "—" if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))) else f.format(x)


def md(rows, head):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"] +
                     ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


def row(name, s):
    return [name, fmt(s["cagr"] * 100, "{:+.1f}%"), fmt(s["vol"] * 100, "{:.0f}%"), fmt(s["sharpe"]),
            fmt(s["maxdd"] * 100, "{:.0f}%"), fmt(s["t"])]


def telegram(text, files=()):
    tok = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    try:
        for i in range(0, len(text), 3800):
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data=dict(chat_id=chat, text=text[i:i + 3800]), timeout=15)
        for fp in files:
            if os.path.exists(fp):
                with open(fp, "rb") as f:
                    requests.post(f"https://api.telegram.org/bot{tok}/sendDocument",
                                  data=dict(chat_id=chat), files=dict(document=f), timeout=60)
    except requests.RequestException as e:
        print(f"Telegram: {e}", file=sys.stderr)


# ───────────────────────── principal ─────────────────────────
def run_all(panel, fund, p):
    w, _ = target_weights(panel, p)
    return simulate(panel, w, fund, p)


def main(argv=None, frames_override=None, fund_override=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=os.getenv("BT_SYMBOLS", CANDIDATES))
    ap.add_argument("--days", type=int, default=int(os.getenv("BT_DAYS", "2000")))
    ap.add_argument("--source", default=os.getenv("SOURCE", "auto").lower())
    ap.add_argument("--n", type=int, default=int(os.getenv("N_COINS", "10")))
    ap.add_argument("--out", default=os.getenv("OUT_DIR", "out"))
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--no-variants", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    p = replace(Params(), n_coins=a.n)

    frames, funds, failed = {}, {}, []
    if frames_override is not None:
        frames, funds = frames_override, (fund_override or {})
    else:
        for s in [x.strip() for x in a.symbols.split(",") if x.strip()]:
            try:
                df, src = data.load_daily(s, a.days, a.source)
                if len(df) < 60:
                    raise ValueError("menos de 60 días")
                frames[s] = df
                f = data.load_funding(s, a.days, a.source)
                if f is not None:
                    funds[s] = f
                print(f"  {s}: {len(df)} días · {src} · funding {'real' if f is not None else 'estimado'}", flush=True)
            except Exception as e:
                failed.append(s)
                print(f"  {s}: sin datos ({str(e)[:80]})", flush=True)
    if not frames:
        print("Sin datos.")
        return None
    panel = build_panel(frames)
    idx = panel["index"]
    fund = pd.DataFrame({s: funds[s] for s in funds}).reindex(idx) if funds else pd.DataFrame(index=idx)
    fund = fund.reindex(columns=panel["close"].columns)
    real_fund_share = fund.notna().to_numpy().mean() if fund.size else 0.0
    fund = fund.fillna(p.fund_default * 3)

    # ── estrategia, referencia siempre-comprado, BTC
    res = run_all(panel, fund, p)
    bh = simulate(panel, bh_weights(panel, p), fund, p)
    first = res.index[(res["exposure"] > 0) | (bh["exposure"] > 0)]
    start = first[0] if len(first) else res.index[0]
    res, bh = res.loc[start:], bh.loc[start:]
    btc_col = next((c for c in panel["close"].columns if data.base(c) == "BTC"), None)
    btc = panel["close"][btc_col].pct_change().loc[start:] if btc_col else None

    s_str, s_bh = stats(res["net"]), stats(bh["net"])
    a_ann, a_t, beta = alpha_vs(res["net"], bh["net"])
    k = int(len(res) * 0.7)
    s_tr, s_te = stats(res["net"].iloc[:k]), stats(res["net"].iloc[k:])
    bh_tr, bh_te = stats(bh["net"].iloc[:k]), stats(bh["net"].iloc[k:])

    # ── control de ruido
    noise = []
    for sd in range(a.seeds):
        pn = shuffle_panel(panel, sd)
        rn = run_all(pn, fund, p).loc[start:]
        noise.append(stats(rn["net"])["sharpe"])
    noise = np.array(noise)
    noise_p95 = np.nanpercentile(noise, 95) if len(noise) else np.nan

    # ── por año
    yrows = []
    for y, g in res["net"].groupby(res.index.year):
        gb = bh["net"].loc[g.index]
        yrows.append([y, fmt(((1 + g).prod() - 1) * 100, "{:+.1f}%"), fmt(((1 + gb).prod() - 1) * 100, "{:+.1f}%"),
                      fmt(((1 + btc.loc[g.index].fillna(0)).prod() - 1) * 100, "{:+.0f}%") if btc is not None else "—",
                      fmt(res["exposure"].loc[g.index].mean() * 100, "{:.0f}%")])

    # ── variantes
    vrows, vdata = [], []
    if not a.no_variants:
        for name, kw in VARIANTS:
            pv = replace(p, **kw)
            rv = (res if not kw else run_all(panel, fund, pv).loc[start:])
            sv = stats(rv["net"])
            bv = simulate(panel, bh_weights(panel, pv), fund, pv).loc[start:]
            av, avt, _ = alpha_vs(rv["net"], bv["net"])
            kk = int(len(rv) * 0.7)
            st_, se_ = stats(rv["net"].iloc[:kk]), stats(rv["net"].iloc[kk:])
            vrows.append([name, fmt(sv["cagr"] * 100, "{:+.1f}%"), fmt(sv["sharpe"]), fmt(sv["maxdd"] * 100, "{:.0f}%"),
                          fmt(av * 100, "{:+.1f}%"), fmt(avt), fmt(st_["sharpe"]), fmt(se_["sharpe"])])
            vdata.append(dict(variante=name, **sv, alpha=av, alpha_t=avt, sharpe_train=st_["sharpe"], sharpe_test=se_["sharpe"]))
        pd.DataFrame(vdata).to_csv(os.path.join(a.out, "variantes.csv"), index=False)

    # ── veredicto
    ver = []
    ok_net = s_str["sharpe"] > 0.5 and s_str["t"] >= 2
    ok_trend = a_t >= 2 and s_str["sharpe"] > s_bh["sharpe"]
    ok_noise = s_str["sharpe"] > noise_p95
    ok_test = s_te["sharpe"] > 0
    ver.append(f"• Neto (comisión, deslizamiento y funding): Sharpe {fmt(s_str['sharpe'])}, CAGR {fmt(s_str['cagr']*100,'{:+.1f}%')}, "
               f"caída máx {fmt(s_str['maxdd']*100,'{:.0f}%')} → " + ("✅ gana" if ok_net else "❌ no gana con claridad"))
    ver.append(f"• ¿Aporta la TENDENCIA sobre siempre-comprado con el mismo escalado? alfa {fmt(a_ann*100,'{:+.1f}%')}/año, "
               f"t {fmt(a_t)} (Sharpe {fmt(s_str['sharpe'])} vs {fmt(s_bh['sharpe'])}) → " + ("✅ sí" if ok_trend else "❌ no demostrado"))
    ver.append(f"• ¿Supera al RUIDO? Sharpe {fmt(s_str['sharpe'])} vs percentil 95 del ruido {fmt(noise_p95)} → " + ("✅ sí" if ok_noise else "❌ no"))
    ver.append(f"• ¿Aguanta en el tramo reciente (30% final)? Sharpe {fmt(s_te['sharpe'])} (siempre-comprado {fmt(bh_te['sharpe'])}) → " + ("✅" if ok_test else "❌"))
    n_ok = sum([ok_net, ok_trend, ok_noise, ok_test])
    final = ("🟢 PASA: se puede pasar a DEMO y después a REAL con tamaño pequeño" if n_ok == 4 else
             "🟡 A MEDIAS: opera en DEMO, no con dinero real" if n_ok >= 2 else
             "🔴 NO PASA: no operar")
    ver.append(f"\n**{final}** ({n_ok}/4)")

    head = (f"# Examen del bot de tendencia — {panel['close'].shape[1]} monedas candidatas · cesta {p.n_coins} · "
            f"{res.index[0]:%Y-%m-%d} → {res.index[-1]:%Y-%m-%d}\n\n"
            f"Funding real en el {real_fund_share*100:.0f}% de los datos (el resto: 0.01%/8h, conservador)."
            + (f" Sin datos: {', '.join(failed)}." if failed else "") + "\n\n")
    report = (head + "## Veredicto\n\n" + "\n".join(ver) +
              "\n\n## 1. Resultado neto\n\n" +
              md([row("Tendencia (el bot)", s_str), row("Siempre comprado, mismo escalado", s_bh),
                  row("Entrenamiento 70%", s_tr), row("Prueba 30%", s_te)],
                 ["", "CAGR", "vol", "Sharpe", "caída máx", "t"]) +
              f"\n\nCostes pagados: comisiones {res['cost'].sum()*100:.1f}% del capital · funding {res['funding'].sum()*100:.1f}% · "
              f"exposición media {res['exposure'].mean()*100:.0f}% · beta frente a siempre-comprado {fmt(beta)}" +
              f"\n\nRuido (retornos barajados, {a.seeds} semillas): Sharpe medio {fmt(np.nanmean(noise))}, p95 {fmt(noise_p95)}" +
              "\n\n## 2. Por año\n\n" + md(yrows, ["Año", "Tendencia", "Siempre comprado", "BTC", "Exposición media"]) +
              ("\n\n## 3. Variantes\n\n" + md(vrows, ["Variante", "CAGR", "Sharpe", "caída", "alfa vs comprado", "t alfa",
                                                    "Sharpe entren.", "Sharpe prueba"]) +
               f"\n\n*{len(VARIANTS)} variantes: alguna saldrá mejor por azar. Solo cambies la configuración si mejora en entrenamiento Y en prueba.*"
               if vrows else "") +
              f"\n\n---\nParámetros: {p.to_dict()}\nTiempo: {time.time()-t0:.0f}s\n")
    res.assign(benchmark=bh["net"]).to_csv(os.path.join(a.out, "equity.csv"))
    with open(os.path.join(a.out, "examen.md"), "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    telegram("🧪 EXAMEN BOT TENDENCIA\n" + head.replace("# ", "").strip() + "\n\n" + "\n".join(ver),
             [os.path.join(a.out, "examen.md"), os.path.join(a.out, "variantes.csv")])
    return dict(res=res, bh=bh, stats=s_str, stats_bh=s_bh, alpha_t=a_t, noise=noise, verdict=n_ok, report=report)


if __name__ == "__main__":
    main()
