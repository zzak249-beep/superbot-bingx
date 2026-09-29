"""
Estudio P12 — ¿funciona de verdad? Mide el hilo de @p12_hunter en muchos símbolos y
muchos meses con la MISMA lógica que el Pine v4.1.

  python study.py                                   # 20 símbolos, 365 días, Binance
  python study.py --symbols BTC,ETH,TAO --days 540
  python study.py --source bingx --symbols AMP,TRUST   # BingX: solo ~45 días de 5m

Salida: informe en consola + out/informe.md, out/dias.csv, out/operaciones.csv,
out/variantes.csv. Si TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID existen, manda el veredicto.
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
from p12 import BLOCKS, Params, add_atr, analyze, btc_positions, prepare, surrogate

DEFAULT_SYMBOLS = ("BTC,ETH,SOL,XRP,DOGE,BNB,ADA,AVAX,LINK,SUI,TAO,LTC,AAVE,NEAR,"
                   "TRUMP,ENA,WIF,ARB,ZEC,LDO")

VARIANTS = [
    ("BASE (Pine por defecto)", {}),
    ("Noche: sin filtro", dict(night_filt="none")),
    ("Noche: solo mismo lado alineado", dict(night_filt="same_aligned")),
    ("Apertura no coincide: descartar", dict(mismatch="discard")),
    ("BTC no en contra", dict(btc_filt="not_against")),
    ("BTC igual", dict(btc_filt="same")),
    ("Midnight Open a favor", dict(mo_filt="favor")),
    ("Ancho: excluir estrecho", dict(width_filt="excl_narrow")),
    ("Ancho: excluir ancho", dict(width_filt="excl_wide")),
    ("Ancho: solo normal", dict(width_filt="only_normal")),
    ("Aceptación 1×30m", dict(mp_n=1)),
    ("Aceptación 3×30m", dict(mp_n=3)),
    ("Objetivo 1R", dict(rr=1.0)),
    ("Objetivo 1.5R", dict(rr=1.5)),
    ("Objetivo 3R", dict(rr=3.0)),
    ("Stop 0.5 ATR tras el mid", dict(stop_buf=0.5)),
]


# ───────────────────────── estadística ─────────────────────────
def tstats(r: np.ndarray) -> dict:
    n = len(r)
    if n == 0:
        return dict(n=0, wr=np.nan, E=np.nan, t=np.nan, pf=np.nan, sum=0.0, maxdd=0.0)
    E = r.mean()
    sd = r.std(ddof=1) if n > 1 else np.nan
    t = E / (sd / math.sqrt(n)) if n > 1 and sd > 0 else np.nan
    gp, gl = r[r > 0].sum(), -r[r < 0].sum()
    eq = np.cumsum(r)
    dd = (np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq).max()
    return dict(n=n, wr=(r > 0).mean() * 100, E=E, t=t, pf=gp / gl if gl > 0 else np.inf, sum=r.sum(), maxdd=dd)


def split_tt(tr: pd.DataFrame, frac=0.7):
    if tr.empty:
        return tr, tr
    tr = tr.sort_values("entry_time")
    k = int(len(tr) * frac)
    return tr.iloc[:k], tr.iloc[k:]


def ztest2(h1, n1, h2, n2):
    """z de diferencia de proporciones (las dos muestras tienen error)."""
    if n1 == 0 or n2 == 0:
        return np.nan
    pp = (h1 + h2) / (n1 + n2)
    if pp <= 0 or pp >= 1:
        return np.nan
    return (h1 / n1 - h2 / n2) / math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))


def ztest(h, n, p0):
    if n == 0 or not (0 < p0 < 1):
        return np.nan
    return (h / n - p0) / math.sqrt(p0 * (1 - p0) / n)


def fmt(x, f="{:+.2f}"):
    return "—" if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))) else f.format(x)


def md_table(rows, head):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


# ───────────────────────── bloques del informe ─────────────────────────
def claim_rows(d: pd.DataFrame) -> list:
    """Cada afirmación con su base CONDICIONADA a la posición del precio.

    La base ingenua (% de todos los días) está sesgada: si el precio aceptó encima del
    high, el mínimo del día queda lejos y es mecánicamente probable que ya esté hecho,
    incluso en un paseo aleatorio. La pregunta del hilo es si el TIEMPO (aceptar) aporta
    sobre la mera POSICIÓN (anunciar), así que se compara contra días con el precio en el
    mismo sitio que NO cumplen la condición."""
    d = d[~d["weekend"]]
    s1u = d[(d["scen"] == 1) & (d["nDir"] == 1)]
    s1d = d[(d["scen"] == 1) & (d["nDir"] == -1)]
    bu = d[(d["scen"] != 1) & d["upperAt6"]]["lowPre"]
    bd = d[(d["scen"] != 1) & ~d["upperAt6"]]["highPre"]
    s1_hits = pd.concat([s1u["lowPre"], s1d["highPre"]])
    s1_base = pd.concat([bu, bd])
    return [
        ("Acepta HIGH → el mínimo ya está hecho", d[d["rd"] == 1]["lowPre"],
         d[d["aboveAt9"] & (d["rd"] != 1)]["lowPre"], "precio sobre el high a las 09:00 SIN aceptar"),
        ("Acepta LOW → el máximo ya está hecho", d[d["rd"] == -1]["highPre"],
         d[d["belowAt9"] & (d["rd"] != -1)]["highPre"], "precio bajo el low a las 09:00 SIN aceptar"),
        ("Sin aceptación → el extremo llega en NY", d[d["rd"] == 0]["extNY"], d["extNY"], "todos los días"),
        ("Mismo lado → el extremo opuesto ya está hecho", s1_hits, s1_base, "misma mitad del P12 a las 06:00, otra noche"),
        ("Se contradicen → el extremo llega en NY", d[d["scen"] == 2]["extNY"], d["extNY"], "todos los días"),
        ("Ambos lados → NY cierra dentro del P12", d[d["scen"] == 3]["insideClose"], d["insideClose"], "todos los días"),
    ]


def claims_section(d: pd.DataFrame, dnull: pd.DataFrame | None) -> tuple[str, list]:
    dd = d[~d["weekend"]]
    N = len(dd)
    if N == 0:
        return "Sin días.", []
    real = claim_rows(d)
    null = claim_rows(dnull) if dnull is not None and not dnull.empty else [None] * len(real)
    rows, verdicts = [], []
    for (name, ser, base, bdesc), nl in zip(real, null):
        n, hc = len(ser), int(ser.sum())
        rate = hc / n if n else np.nan
        nb, hb = len(base), int(base.sum())
        brate = hb / nb if nb else np.nan
        nn = len(nl[1]) if nl is not None else 0
        hn = int(nl[1].sum()) if nn else 0
        nrate = hn / nn if nn else np.nan
        # referencia = la más exigente de las dos (la de mayor acierto)
        cands = [(r_, h_, n_) for r_, h_, n_ in ((brate, hb, nb), (nrate, hn, nn)) if n_ > 0]
        rr_, hr_, nr_ = max(cands, key=lambda x: x[0]) if cands else (np.nan, 0, 0)
        z = ztest2(hc, n, hr_, nr_)
        delta = (rate - rr_) * 100 if n and nr_ else np.nan
        v = ("muestra corta" if n < 30 or nb < 20 else "APORTA" if (z >= 3 and delta >= 5) else
             "EN CONTRA" if z <= -3 else "no aporta")
        rows.append([name, n, fmt(rate * 100 if n else np.nan, "{:.0f}%"),
                     fmt(brate * 100, "{:.0f}%") + f" (n {nb})", fmt(nrate * 100, "{:.0f}%"),
                     fmt(delta, "{:+.0f}pp"), fmt(z, "{:+.1f}"), v])
        verdicts.append((name, v, delta, n))
    acc = (dd["rd"] != 0).mean() * 100
    txt = (f"Días laborables medidos: **{N}** · aceptan: **{acc:.0f}%**\n\n" +
           md_table(rows, ["Afirmación", "n", "acierto", "base condicionada", "en ruido", "Δ vs mejor ref.", "z", "veredicto"]) +
           "\n\n*base condicionada = mismos días con el precio en el mismo sitio sin la condición del hilo. "
           "en ruido = la misma medida sobre una serie de control con la volatilidad horaria del símbolo pero sin "
           "estructura de sesión. Δ se mide contra la MAYOR de las dos: si el hilo no supera al ruido, lo que "
           "describe es geometría del rango.*")
    return txt, verdicts


def hours_section(d: pd.DataFrame, dnull: pd.DataFrame | None) -> tuple[str, float, float, float]:
    """Dónde cae el máximo/mínimo del día. Se compara contra el RUIDO, no contra la duración:
    en un paseo aleatorio los extremos caen más a menudo al principio y al final de la
    ventana (ley del arcoseno), así que "por duración" no es una base válida."""
    d = d[~d["weekend"]]
    dn = dnull[~dnull["weekend"]] if dnull is not None and not dnull.empty else None
    rows = []
    for i, (name, s_, e_) in enumerate(BLOCKS):
        uni = (e_ - s_) / 1440 * 100
        hi = (d["dHiB"] == i).mean() * 100
        lo = (d["dLoB"] == i).mean() * 100
        nz = (((dn["dHiB"] == i).mean() + (dn["dLoB"] == i).mean()) / 2 * 100) if dn is not None else np.nan
        rows.append([name, f"{hi:.0f}%", f"{lo:.0f}%", f"{uni:.0f}%", fmt(nz, "{:.0f}%"),
                     fmt((hi + lo) / 2 / nz if nz else np.nan, "{:.2f}×")])
    pre_hi = (d["dHiB"] <= 3).mean() * 100
    pre_lo = (d["dLoB"] <= 3).mean() * 100
    uni_pre = 930 / 1440 * 100
    nz_pre = (((dn["dHiB"] <= 3).mean() + (dn["dLoB"] <= 3).mean()) / 2 * 100) if dn is not None else np.nan
    rows.append(["**Antes de las 09:30**", f"**{pre_hi:.0f}%**", f"**{pre_lo:.0f}%**", f"{uni_pre:.0f}%",
                 fmt(nz_pre, "{:.0f}%"), fmt((pre_hi + pre_lo) / 2 / nz_pre if nz_pre else np.nan, "{:.2f}×")])
    txt = md_table(rows, ["Bloque (hora NY)", "máx del día", "mín del día", "por duración", "en ruido", "× ruido"])
    txt += "\n\n*× ruido > 1 = ese bloque concentra extremos más de lo que daría el azar con la misma volatilidad horaria.*"
    return txt, (pre_hi + pre_lo) / 2, uni_pre, nz_pre


def trades_section(tr: pd.DataFrame) -> str:
    if tr.empty:
        return "Ninguna operación."
    r = tr["r"].to_numpy()
    s = tstats(r)
    w = tr["szMult"].to_numpy()
    Ew = (r * w).sum() / w.sum()
    a, b = split_tt(tr)
    sa, sb = tstats(a["r"].to_numpy()), tstats(b["r"].to_numpy())
    reasons = tr["reason"].value_counts(normalize=True).mul(100).round(0).to_dict()
    out = [md_table([[s["n"], f"{s['wr']:.0f}%", fmt(s["E"]) + "R", fmt(Ew) + "R", fmt(s["t"]), fmt(s["pf"], "{:.2f}"),
                      fmt(s["sum"], "{:+.1f}") + "R", fmt(s["maxdd"], "{:.1f}") + "R"]],
                    ["ops", "acierto", "E", "E ponderada", "t", "PF", "Σ", "máx DD"])]
    out.append(f"\nSalidas: " + " · ".join(f"{k} {v:.0f}%" for k, v in reasons.items()))
    out.append(f"MFE ≥1R {(tr['mfe'] >= 1).mean()*100:.0f}% · ≥2R {(tr['mfe'] >= 2).mean()*100:.0f}% · "
               f"≥3R {(tr['mfe'] >= 3).mean()*100:.0f}% · coste medio {tr['costR_signal'].mean():.2f}R · "
               f"stop medio {tr['stop_pct'].mean():.2f}%")
    over = sa["E"] > 0 and sb["E"] <= 0
    out.append(f"\n**Entrenamiento / prueba (70/30 cronológico):** {sa['n']} ops {fmt(sa['E'])}R · "
               f"{sb['n']} ops {fmt(sb['E'])}R" + ("  ⚠ gana en el pasado y pierde en lo reciente" if over else ""))

    def grp(col, labels):
        rows = []
        for key, lab in labels:
            sub = tr[tr[col] == key]["r"].to_numpy() if key is not None else tr[tr[col].isna()]["r"].to_numpy()
            st = tstats(sub)
            rows.append([lab, st["n"], fmt(st["E"]) + "R", fmt(st["t"])])
        return rows
    rows = []
    rows += grp("dir", [(1, "Largos"), (-1, "Cortos")])
    rows += grp("coinc", [(True, "Apertura coincide"), (False, "Apertura NO coincide")])
    rows += grp("btc_aligned", [(True, "BTC alineado"), (False, "BTC en contra")])
    rows += grp("mo_fav", [(True, "Midnight Open a favor"), (False, "Midnight Open en contra")])
    rows += grp("wB", [(0, "P12 estrecho"), (1, "P12 normal"), (2, "P12 ancho")])
    rows += grp("scen", [(1, "Noche: mismo lado"), (2, "Noche: se contradicen"), (3, "Noche: ambos lados")])
    out.append("\n**Desgloses**\n\n" + md_table(rows, ["Grupo", "ops", "E", "t"]))

    tr = tr.copy()
    tr["mes"] = tr["entry_time"].dt.strftime("%Y-%m")
    mrows = []
    for mes, g in tr.groupby("mes"):
        st = tstats(g["r"].to_numpy())
        mrows.append([mes, st["n"], fmt(st["E"]) + "R", fmt(st["sum"], "{:+.1f}") + "R"])
    out.append("\n**Por mes** (si solo gana en 1-2 meses, depende del régimen)\n\n" + md_table(mrows, ["Mes", "ops", "E", "Σ"]))

    srows = []
    for sym, g in tr.groupby("symbol"):
        st = tstats(g["r"].to_numpy())
        srows.append([sym, st["n"], fmt(st["E"]) + "R", fmt(st["sum"], "{:+.1f}") + "R"])
    srows.sort(key=lambda x: -float(x[3].replace("R", "").replace("—", "0")))
    out.append("\n**Por símbolo** (no elijas los mejores: eso es data snooping, manda el agregado)\n\n" +
               md_table(srows, ["Símbolo", "ops", "E", "Σ"]))
    return "\n".join(out)


# ───────────────────────── ejecución ─────────────────────────
def run(frames: dict, btc_pos: dict | None, p: Params):
    days, trades = [], []
    for sym, df in frames.items():
        d, t = analyze(df, p, btc_pos if not sym.startswith("BTC") else None, sym)
        days += d
        trades += t
    return pd.DataFrame(days), pd.DataFrame(trades)


def telegram_doc(path: str, caption: str = ""):
    """Manda el informe completo como archivo (en Railway el disco se borra al terminar)."""
    tok = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat or not os.path.exists(path):
        return
    try:
        with open(path, "rb") as f:
            requests.post(f"https://api.telegram.org/bot{tok}/sendDocument",
                          data=dict(chat_id=chat, caption=caption[:1000]), files=dict(document=f), timeout=60)
    except requests.RequestException as e:
        print(f"Telegram: {e}", file=sys.stderr)


def telegram(text: str):
    tok = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    for i in range(0, len(text), 3800):
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data=dict(chat_id=chat, text=text[i:i + 3800]), timeout=15)
        except requests.RequestException as e:
            print(f"Telegram: {e}", file=sys.stderr)


def main(argv=None, frames_override=None):
    ap = argparse.ArgumentParser(description="Estudio P12")
    ap.add_argument("--symbols", default=os.getenv("SYMBOLS", DEFAULT_SYMBOLS))
    ap.add_argument("--days", type=int, default=int(os.getenv("DAYS", "365")))
    ap.add_argument("--source", default=os.getenv("SOURCE", "auto").lower(), choices=["auto", "binance", "vision", "bingx"])
    ap.add_argument("--out", default=os.getenv("OUT_DIR", "out"))
    ap.add_argument("--no-variants", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    base = Params()
    t0 = time.time()

    # ── datos
    frames, failed, short = {}, [], []
    if frames_override is not None:
        frames = frames_override
    else:
        syms = [s.strip() for s in a.symbols.split(",") if s.strip()]
        if not any(data.norm_symbol(s, "binance").startswith("BTCUSDT") for s in syms):
            syms.append("BTC")  # referencia para el filtro/desglose BTC
        for s in syms:
            try:
                df, src = data.load(s, a.days, a.source)
                frames[data.norm_symbol(s, "binance")] = df
                span = (df.index.max() - df.index.min()).days if len(df) else 0
                warn = ""
                if span < a.days * 0.8:
                    warn = f"  ⚠ solo {span} días de {a.days} (listada hace poco o la fuente no da más)"
                    short.append(f"{s} {span}d")
                print(f"  {s}: {len(df):,} velas · {span} días · {src}{warn}", flush=True)
            except Exception as e:
                failed.append(f"{s} ({e})")
                print(f"  {s}: FALLO {e}", file=sys.stderr)
    for k in list(frames):
        frames[k] = add_atr(prepare(frames[k]), base.atr_len)
    btc_key = next((k for k in frames if k.startswith("BTCUSDT")), None)
    btc_pos = btc_positions(frames[btc_key]) if btc_key else None

    # ── configuración base
    days, trades = run(frames, btc_pos, base)
    days.to_csv(os.path.join(a.out, "dias.csv"), index=False)
    trades.to_csv(os.path.join(a.out, "operaciones.csv"), index=False)
    # control: mismas cuentas sobre series barajadas (misma volatilidad horaria, sin sesiones)
    nd, nt = [], []
    for sd_ in range(3):  # 3 semillas agrupadas: el control también tiene error de muestreo
        null_frames = {k: add_atr(prepare(surrogate(v[["open", "high", "low", "close", "volume"]], seed=100 * sd_ + i)), base.atr_len)
                       for i, (k, v) in enumerate(frames.items())}
        d_, t_ = run(null_frames, None, base)
        nd.append(d_)
        nt.append(t_)
    null_days, null_trades = pd.concat(nd, ignore_index=True), pd.concat(nt, ignore_index=True)
    claims_txt, verdicts = claims_section(days, null_days)
    hours_txt, pre_share, pre_uni, pre_null = hours_section(days, null_days)
    trades_txt = trades_section(trades)
    sn = tstats(null_trades["r"].to_numpy()) if not null_trades.empty else tstats(np.array([]))
    trades_txt += (f"\n\n**Control (ruido con la misma volatilidad horaria):** {sn['n']} ops · E {fmt(sn['E'])}R · "
                   f"t {fmt(sn['t'])}. Lo que la estrategia gane por ENCIMA de esto es lo único atribuible al método.")

    # ── variantes
    vrows, vdata = [], []
    if not a.no_variants:
        for name, kw in VARIANTS:
            p = replace(base, **kw)
            _, tr = (days, trades) if not kw else run(frames, btc_pos, p)
            s = tstats(tr["r"].to_numpy()) if not tr.empty else tstats(np.array([]))
            tra, tes = split_tt(tr) if not tr.empty else (tr, tr)
            sa = tstats(tra["r"].to_numpy()) if not tr.empty else tstats(np.array([]))
            sb = tstats(tes["r"].to_numpy()) if not tr.empty else tstats(np.array([]))
            cand = s["n"] >= 100 and s["t"] >= 3 and sa["E"] > 0 and sb["E"] > 0
            vrows.append([name, s["n"], fmt(s["E"]) + "R", fmt(s["t"]), fmt(s["pf"], "{:.2f}"),
                          fmt(sa["E"]) + "R", fmt(sb["E"]) + "R", "✅ candidato" if cand else ""])
            vdata.append(dict(variante=name, **{k: s[k] for k in ("n", "wr", "E", "t", "pf", "sum", "maxdd")},
                              E_train=sa["E"], E_test=sb["E"], candidato=cand))
        pd.DataFrame(vdata).to_csv(os.path.join(a.out, "variantes.csv"), index=False)

    # ── veredicto
    sb_all = tstats(trades["r"].to_numpy()) if not trades.empty else tstats(np.array([]))
    ver = []
    if sb_all["n"] < 100:
        ver.append(f"• Operaciones: {sb_all['n']} — muestra insuficiente (<100). Sube --days o añade símbolos.")
    elif sb_all["t"] >= 3:
        ver.append(f"• Operaciones: E {fmt(sb_all['E'])}R con t {fmt(sb_all['t'])} en {sb_all['n']} ops → edge distinguible de cero.")
    elif sb_all["t"] >= 2:
        ver.append(f"• Operaciones: E {fmt(sb_all['E'])}R, t {fmt(sb_all['t'])} → prometedor pero no concluyente (se pide t ≥ 3).")
    else:
        ver.append(f"• Operaciones: E {fmt(sb_all['E'])}R, t {fmt(sb_all['t'])} en {sb_all['n']} ops → NO distinguible de cero.")
    for name, v, delta, n in verdicts:
        ver.append(f"• {name}: {v} ({fmt(delta, '{:+.0f}pp')}, n {n})")
    ver.append(f"• Extremo del día antes de las 09:30: {pre_share:.0f}% vs {pre_null:.0f}% en ruido → " +
               ("la noche SÍ concentra extremos más que el azar" if pre_share > pre_null + 4 else
                "la noche concentra MENOS extremos que el azar: la premisa del P12 no se cumple" if pre_share < pre_null - 4 else
                "igual que el azar: la noche no tiene nada especial"))
    cands = [r[0] for r in vrows if r[-1]]
    ver.append("• Variantes candidatas: " + (", ".join(cands) if cands else "ninguna pasa (n≥100, t≥3, gana en entrenamiento Y en prueba)"))

    dates = pd.to_datetime(days["date"]) if not days.empty else pd.Series(dtype="datetime64[ns]")
    head = (f"# Estudio P12 — {len(frames)} símbolos · "
            f"{dates.min():%Y-%m-%d} → {dates.max():%Y-%m-%d}\n\n" if not days.empty else "# Estudio P12\n\n")
    if failed:
        head += "Símbolos sin datos: " + ", ".join(failed) + "\n\n"
    if short:
        head += "Historial más corto de lo pedido: " + ", ".join(short) + "\n\n"
    report = (head + "## Veredicto\n\n" + "\n".join(ver) +
              "\n\n## 1. Afirmaciones del hilo contra la tasa base\n\n" + claims_txt +
              "\n\n## 2. ¿Cuándo se forma el extremo del día?\n\n" + hours_txt +
              "\n\n## 3. Operaciones (configuración por defecto del Pine)\n\n" + trades_txt +
              ("\n\n## 4. Variantes (qué filtro activar en el Pine)\n\n" +
               md_table(vrows, ["Variante", "ops", "E", "t", "PF", "E entren.", "E prueba", ""]) +
               f"\n\n*{len(VARIANTS)} variantes probadas: con tantas pruebas alguna sale bien por azar. "
               "Solo cuenta si gana en entrenamiento Y en prueba con t ≥ 3.*" if vrows else "") +
              f"\n\n---\nParámetros base: {base.to_dict()}\nTiempo: {time.time() - t0:.0f}s\n")
    with open(os.path.join(a.out, "informe.md"), "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    telegram("📊 ESTUDIO P12\n" + head.replace("# ", "").strip() + "\n\n" + "\n".join(ver))
    for fn in ("informe.md", "operaciones.csv", "variantes.csv"):
        telegram_doc(os.path.join(a.out, fn), "P12 · " + fn)
    return days, trades, report


if __name__ == "__main__":
    main()
