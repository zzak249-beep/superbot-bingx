"""
EXAMEN del bot de tendencia. Responde tres preguntas antes de arriesgar dinero:

  1. ¿Gana después de comisiones, deslizamiento y FUNDING?
  2. ¿La señal de tendencia aporta algo sobre lo mismo pero SIEMPRE COMPRADO con el mismo
     escalado de volatilidad? (una réplica encontró que casi todo el mérito es del escalado)
  3. ¿Supera al RUIDO? (la misma estrategia sobre retornos barajados, sin tendencias reales)

  python backtest.py                         # 40 candidatas, ~5,5 años
  python backtest.py --days 1500 --n 10

CAMBIOS DE ESTA VERSIÓN (ver notas al final del archivo):
  · --seeds por defecto 5 → 200: con 5 semillas el percentil 95 del ruido es un
    dato casi tan inestable como el que intenta medir (visto en dos tiradas
    reales con datos casi idénticos: p95 pasó de 1.08 a 1.23).
  · El ruido ahora se recalcula POR VARIANTE (antes solo existía para el bot
    base; comparar "Cesta de 20" contra el p95 de una cesta de 10 no es una
    comparación válida, porque el ruido de una cartera de 20 activos no es
    el mismo que el de una de 10).
  · --shuffle-mode {iid,block}: 'iid' es el barajado original (cada moneda por
    su cuenta) — destruye también la correlación real entre monedas, no solo
    la tendencia. 'block' baraja BLOQUES de días completos IGUALES para todas
    las monedas a la vez, así que un bloque real de 30 días donde BTC y ETH se
    movieron juntos se sigue moviendo junto en el barajado, solo cambia de
    sitio en el calendario. Esto es un tira y afloja real, no un blindaje:
    con bloques pequeños, algo de tendencia real de corto plazo sobrevive
    DENTRO del bloque (favorece a los lookbacks cortos); con bloques grandes
    apenas se baraja nada (pocas combinaciones posibles). No hay un tamaño de
    bloque "correcto" universal — se deja como parámetro para que compares
    'iid' contra 'block' con un par de tamaños y veas tú mismo cuánto cambia
    el p95. Si cambia mucho, es una señal de que el resultado depende de qué
    null se use, no solo de la estrategia.

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


def shuffle_panel_iid(panel: dict, seed: int) -> dict:
    """Barajado ORIGINAL: cada moneda con su propia semilla, de forma independiente.
    Mismas magnitudes y mismo periodo de cotización por moneda, CERO tendencia — pero
    TAMBIÉN cero correlación real entre monedas (en cripto, alta). Cada moneda 'inventa'
    su suerte por su cuenta; en la realidad, cuando el mercado se mueve, se mueve junto."""
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


def shuffle_panel_block(panel: dict, seed: int, block: int = 30) -> dict:
    """Alternativa: baraja BLOQUES de `block` días completos, aplicando el MISMO
    reordenamiento a todas las monedas a la vez. Si BTC y ETH subieron juntos en un
    bloque real de 30 días, ese bloque se mueve entero a otra fecha, pero siguen
    subiendo juntos ahí — conserva la correlación cruzada real. Lo que se destruye es
    la persistencia de tendencia MÁS LARGA que el bloque, no toda tendencia: con
    block=30, un lookback de 5-20 días puede seguir 'viendo' tendencia real dentro
    de un bloque, aunque esté en un sitio distinto del calendario. Por eso no hay un
    tamaño de bloque único correcto: compara un par de tamaños y mira cuánto cambia
    el resultado antes de fiarte de uno solo."""
    rng = np.random.default_rng(seed)
    close = panel["close"]
    T = len(close)
    logret = np.log(close).diff()
    logret.iloc[0] = 0.0
    starts = list(range(0, T, block))
    order = rng.permutation(len(starts))
    row_map = np.concatenate([np.arange(starts[i], min(starts[i] + block, T)) for i in order])
    row_map = row_map[:T]
    logret_shuf = pd.DataFrame(logret.to_numpy()[row_map], index=logret.index, columns=logret.columns)
    new_close = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    for s in close.columns:
        v = close[s].notna().to_numpy()
        if v.sum() < 3:
            continue
        lr = logret_shuf[s].to_numpy()[v]
        lr = lr.copy()
        lr[0] = 0.0
        new_close.loc[v, s] = close[s][v].iloc[0] * np.exp(np.cumsum(lr))
    return dict(index=panel["index"], close=new_close, qvol=panel["qvol"])


def shuffle_panel(panel: dict, seed: int, mode: str = "iid", block: int = 30) -> dict:
    if mode == "block":
        return shuffle_panel_block(panel, seed, block)
    return shuffle_panel_iid(panel, seed)


def noise_sharpe(panel: dict, fund: pd.DataFrame, p: Params, start, seeds: int, mode: str, block: int) -> np.ndarray:
    """Sharpe de la ESTRATEGIA (no de una cartera pasiva) sobre `seeds` barajados,
    con los parámetros `p` exactos que se están evaluando (misma cesta, mismos
    lookbacks...). Se recalcula para cada variante: el ruido de una cesta de 20
    monedas no es el mismo que el de una de 10."""
    out = np.full(seeds, np.nan)
    for sd in range(seeds):
        pn = shuffle_panel(panel, sd, mode, block)
        rn = run_all(pn, fund, p).loc[start:]
        out[sd] = stats(rn["net"])["sharpe"]
    return out


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
    ap.add_argument("--seeds", type=int, default=200,
                    help="Semillas del control de ruido. Con 5, el p95 es casi tan ruidoso como lo que mide.")
    ap.add_argument("--shuffle-mode", choices=["iid", "block"], default="iid",
                    help="iid = cada moneda por separado (original). block = bloques de días compartidos entre monedas (conserva correlación real, deja algo de tendencia corta dentro del bloque).")
    ap.add_argument("--block-size", type=int, default=30, help="Tamaño de bloque en días, solo para --shuffle-mode block.")
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

    # ── control de ruido (del bot BASE, con sus propios n_coins)
    noise = noise_sharpe(panel, fund, p, start, a.seeds, a.shuffle_mode, a.block_size)
    noise_p95 = np.nanpercentile(noise, 95) if len(noise) else np.nan

    # ── por año
    yrows = []
    for y, g in res["net"].groupby(res.index.year):
        gb = bh["net"].loc[g.index]
        yrows.append([y, fmt(((1 + g).prod() - 1) * 100, "{:+.1f}%"), fmt(((1 + gb).prod() - 1) * 100, "{:+.1f}%"),
                      fmt(((1 + btc.loc[g.index].fillna(0)).prod() - 1) * 100, "{:+.0f}%") if btc is not None else "—",
                      fmt(res["exposure"].loc[g.index].mean() * 100, "{:.0f}%")])

    # ── variantes (ahora con SU PROPIO ruido, no el del bot base)
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
            nz_ = noise if not kw else noise_sharpe(panel, fund, pv, start, a.seeds, a.shuffle_mode, a.block_size)
            nz_p95 = np.nanpercentile(nz_, 95) if len(nz_) else np.nan
            beats_noise = (not np.isnan(sv["sharpe"])) and (not np.isnan(nz_p95)) and sv["sharpe"] > nz_p95
            vrows.append([name, fmt(sv["cagr"] * 100, "{:+.1f}%"), fmt(sv["sharpe"]), fmt(sv["maxdd"] * 100, "{:.0f}%"),
                          fmt(av * 100, "{:+.1f}%"), fmt(avt), fmt(st_["sharpe"]), fmt(se_["sharpe"]),
                          fmt(nz_p95), "✅" if beats_noise else "❌"])
            vdata.append(dict(variante=name, **sv, alpha=av, alpha_t=avt, sharpe_train=st_["sharpe"],
                               sharpe_test=se_["sharpe"], noise_p95=nz_p95, beats_noise=beats_noise))
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
    ver.append(f"• ¿Supera al RUIDO? Sharpe {fmt(s_str['sharpe'])} vs percentil 95 del ruido {fmt(noise_p95)} "
               f"({a.seeds} semillas, modo {a.shuffle_mode}) → " + ("✅ sí" if ok_noise else "❌ no"))
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
              f"\n\nRuido ({a.shuffle_mode}, {a.seeds} semillas" + (f", bloque {a.block_size}d" if a.shuffle_mode == "block" else "") +
              f"): Sharpe medio {fmt(np.nanmean(noise))}, p95 {fmt(noise_p95)}" +
              "\n\n## 2. Por año\n\n" + md(yrows, ["Año", "Tendencia", "Siempre comprado", "BTC", "Exposición media"]) +
              ("\n\n## 3. Variantes\n\n" + md(vrows, ["Variante", "CAGR", "Sharpe", "caída", "alfa vs comprado", "t alfa",
                                                    "Sharpe entren.", "Sharpe prueba", "ruido p95 (propio)", "¿bate su ruido?"]) +
               f"\n\n*{len(VARIANTS)} variantes: alguna saldrá mejor por azar. Solo cambies la configuración si mejora en "
               f"entrenamiento Y en prueba Y bate su PROPIO ruido (cada variante tiene un p95 distinto, no el del bot base).*"
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

# ═══════════════════════════════════════════════════════════════════════
# NOTAS DE LA REVISIÓN
# ═══════════════════════════════════════════════════════════════════════
# 1. --seeds 5 → 200. Con 5 semillas, el p95 (que ya de por sí es un estimador
#    de cola, más ruidoso que una media) se mueve por azar casi tanto como el
#    resultado que se mide. En dos tiradas reales con datos casi idénticos
#    (un día de diferencia), el p95 pasó de 1.08 a 1.23 — un salto del 14%
#    que no puede venir de un día más de datos. 200 semillas no lo arregla
#    del todo pero lo estabiliza mucho; tiene un coste: 200× más tiempo en
#    esa parte. Si 200 es demasiado lento en Railway, prueba 50 como mínimo
#    razonable y compara un par de veces con el mismo `--out` para ver cuánto
#    todavía se mueve el p95 solo por azar.
#
# 2. El ruido ahora se recalcula para CADA variante, con sus propios n_coins/
#    lookbacks/etc. Antes, la tabla de variantes no comparaba nada contra
#    ruido; si alguna vez comparaste a ojo el Sharpe de "Cesta de 20" contra
#    el p95 del bot base (cesta de 10), no era una comparación válida — el
#    ruido de una cesta de 20 activos no es el mismo que el de una de 10.
#    Esto es más lento (recalcula `seeds` barajados por cada una de las 11
#    variantes), así que con 200 semillas y 11 variantes el examen tardará
#    bastante más que antes. Si hace falta, usa --no-variants para la
#    exploración rápida y actívalas solo en la tirada final.
#
# 3. --shuffle-mode {iid,block}: el barajado original baraja cada moneda por
#    su cuenta. Eso destruye la tendencia (correcto) pero TAMBIÉN destruye la
#    correlación real entre monedas (en cripto, alta) — en el ruido, las 10
#    monedas ya no se mueven juntas nunca, cuando en la realidad sí. No sé
#    decirte con certeza en qué dirección sesga el p95 sin probarlo: podría
#    hacer el ruido más fácil de batir (si la falta de diversificación real
#    en el mercado hace que la estrategia sufra más simultáneamente de lo que
#    el barajado independiente refleja) o más difícil (si el barajado
#    independiente infla el Sharpe del ruido al diversificar más de la cuenta
#    entre 10 sucesos de suerte no correlacionados). Por eso añado 'block'
#    como alternativa que SÍ conserva la correlación real dentro de cada
#    bloque de N días, en vez de asegurar una respuesta que no puedo
#    verificar sin tus datos reales. Es un tira y afloja, no una solución
#    perfecta: con bloques pequeños (ej. 20-30 días) algo de tendencia real
#    de corto plazo sobrevive dentro del bloque, favoreciendo a los
#    lookbacks cortos (5-30) incluso en el "ruido". Con bloques grandes se
#    baraja tan poco que hay pocas combinaciones distintas posibles.
#    RECOMENDACIÓN: corre el examen con --shuffle-mode iid y otra vez con
#    --shuffle-mode block --block-size 30 (y quizá 90), y compara los p95.
#    Si el veredicto cambia según el modo, es una señal real de que el
#    resultado es sensible a una decisión metodológica, no un hecho sólido
#    — y eso en sí mismo es información valiosa antes de arriesgar dinero.
# ═══════════════════════════════════════════════════════════════════════
