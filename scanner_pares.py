"""
scanner_pares.py — Encuentra TODOS los pares disponibles en BingX
ACTUALIZADO: guarda TODOS los pares válidos (no solo top 50)
Excluye: NCCO (materias primas), NCFX (forex), NCSI/NCSK (índices/acciones)

Uso: python scanner_pares.py
"""

import time
import requests
import json
from datetime import datetime

VOLUMEN_MIN   = 500_000
SPREAD_MAX    = 1.5
PAUSA_MS      = 0.15
BASE_URL      = "https://open-api.bingx.com"

# No son crypto — dinámica diferente, generan falsas señales
PREFIJOS_EXCLUIR = ("NCCO", "NCFX", "NCSI", "NCSK")

SEP = "═" * 65


def get_todos_los_pares() -> list:
    print(f"\n[SCANNER] Obteniendo lista de pares de BingX...")
    try:
        r = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/contracts", timeout=15)
        contratos = r.json().get("data", [])
        pares = []
        excluidos = 0
        for c in contratos:
            sym = c.get("symbol", "")
            if not sym.endswith("-USDT"):
                continue
            if c.get("status", 1) == 0:
                continue
            base = sym.replace("-USDT", "")
            if any(base.startswith(p) for p in PREFIJOS_EXCLUIR):
                excluidos += 1
                continue
            pares.append(sym)
        pares.sort()
        print(f"[SCANNER] {len(pares)} pares crypto USDT | {excluidos} no-crypto excluidos")
        return pares
    except Exception as e:
        print(f"[SCANNER] Error: {e}")
        return []


def get_ticker(par):
    try:
        r = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker",
                         params={"symbol": par}, timeout=8)
        d = r.json().get("data", {})
        if isinstance(d, list) and d:
            d = d[0]
        return d if isinstance(d, dict) else {}
    except:
        return {}


def get_klines(par):
    try:
        r = requests.get(f"{BASE_URL}/openApi/swap/v3/quote/klines",
                         params={"symbol": par, "interval": "1h", "limit": 50}, timeout=8)
        return r.json().get("data", [])
    except:
        return []


def parsear_closes(klines):
    closes = []
    for k in klines:
        try:
            if isinstance(k, dict):
                c = k.get("close", k.get("c", None))
                if c is not None:
                    closes.append(float(c))
            elif isinstance(k, (list, tuple)) and len(k) >= 5:
                closes.append(float(k[4]))
        except:
            continue
    return closes


def calcular_rsi(closes, periodo=14):
    import numpy as np
    if len(closes) < periodo + 1:
        return 50.0
    arr = np.array(closes, dtype=float)
    d   = np.diff(arr)
    g   = np.where(d > 0, d, 0.0)
    p   = np.where(d < 0, -d, 0.0)
    ag  = np.mean(g[:periodo])
    ap  = np.mean(p[:periodo])
    for i in range(periodo, len(d)):
        ag = (ag * (periodo - 1) + g[i]) / periodo
        ap = (ap * (periodo - 1) + p[i]) / periodo
    if ap == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / ap))


def analizar_par(par):
    r = {"par": par, "valido": False, "volumen": 0, "spread": 999, "precio": 0, "rsi_1h": 50, "motivo": ""}
    t = get_ticker(par)
    if not t:
        r["motivo"] = "sin ticker"; return r

    try:
        vol = float(t.get("quoteVolume", 0))
    except:
        vol = 0
    r["volumen"] = vol
    if vol < VOLUMEN_MIN:
        r["motivo"] = f"vol bajo ${vol:,.0f}"; return r

    try:
        bid = float(t.get("bidPrice", 0))
        ask = float(t.get("askPrice", 0))
        spread = ((ask - bid) / ((bid + ask) / 2)) * 100 if bid > 0 and ask > 0 else (0.1 if vol > 1_000_000 else 0.5)
    except:
        spread = 999
    r["spread"] = spread
    if spread > SPREAD_MAX:
        r["motivo"] = f"spread {spread:.2f}%"; return r

    closes = parsear_closes(get_klines(par))
    if len(closes) < 20:
        r["motivo"] = f"klines insuf ({len(closes)})"; return r

    precio = closes[-1]
    if precio <= 0:
        r["motivo"] = "precio=0"; return r

    rsi = calcular_rsi(closes)
    r["precio"] = precio
    r["rsi_1h"] = rsi
    r["valido"]  = True
    r["motivo"]  = f"vol=${vol:,.0f} spread={spread:.2f}% RSI={rsi:.1f}"
    return r


def escanear_todos(pares):
    print(f"\n[SCANNER] Analizando {len(pares)} pares...")
    print(SEP)
    validos = []
    total   = len(pares)
    for i, par in enumerate(pares):
        pct = int((i + 1) / total * 100)
        try:
            r = analizar_par(par)
            if r["valido"]:
                validos.append(r)
                print(f"  ✓ [{i+1:4}/{total}] {pct:3}%  {par:<30} {r['motivo']}")
            else:
                print(f"  ✗ [{i+1:4}/{total}] {pct:3}%  {par:<30} {r['motivo']}", end="\r")
        except Exception as e:
            print(f"  ! error {par}: {e}", end="\r")
        time.sleep(PAUSA_MS)
    print()
    return validos


def guardar_resultados(validos):
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")
    validos.sort(key=lambda x: x["volumen"], reverse=True)

    # pares_validos.txt
    with open("pares_validos.txt", "w", encoding="utf-8") as f:
        f.write(f"SCANNER BINGX — {ahora} — {len(validos)} pares válidos\n\n")
        f.write(f"{'PAR':<30} {'VOLUMEN 24H':>18} {'SPREAD':>8} {'RSI 1H':>8}\n")
        f.write("─" * 70 + "\n")
        for r in validos:
            f.write(f"{r['par']:<30} ${r['volumen']:>17,.0f} {r['spread']:>7.3f}% {r['rsi_1h']:>7.1f}\n")
    print(f"  ► pares_validos.txt ({len(validos)} pares)")

    # config_pares.py — TODOS los pares
    todos = [r["par"] for r in validos]
    with open("config_pares.py", "w", encoding="utf-8") as f:
        f.write(f"# Generado — {ahora}\n")
        f.write(f"# {len(todos)} pares crypto USDT con vol>${VOLUMEN_MIN:,.0f} y spread<{SPREAD_MAX}%\n")
        f.write(f"# Excluidos: NCCO/NCFX/NCSI/NCSK (no crypto)\n\n")
        f.write(f"PARES = {json.dumps(todos, indent=4)}\n")
    print(f"  ► config_pares.py ({len(todos)} pares — TODOS activos en el bot)")

    # señales_ahora.txt
    cercanos = sorted([r for r in validos if r["rsi_1h"] <= 40], key=lambda x: x["rsi_1h"])
    if cercanos:
        with open("señales_ahora.txt", "w", encoding="utf-8") as f:
            f.write(f"PARES CON RSI <= 40 — {ahora}\n\n")
            for r in cercanos:
                f.write(f"{r['par']:<30} RSI={r['rsi_1h']:.1f}  ${r['volumen']:,.0f}\n")
        print(f"  ► señales_ahora.txt ({len(cercanos)} pares cercanos a señal)")


def imprimir_resumen(validos, total):
    print(f"\n{SEP}")
    print(f"  Escaneados: {total} | Válidos: {len(validos)} | Descartados: {total-len(validos)}")
    print(SEP)
    print(f"\n  TOP 10 POR VOLUMEN:")
    for r in validos[:10]:
        print(f"  {r['par']:<30} ${r['volumen']:>18,.0f}  RSI={r['rsi_1h']:.1f}")
    cercanos = sorted([r for r in validos if r["rsi_1h"] <= 35], key=lambda x: x["rsi_1h"])
    if cercanos:
        print(f"\n  ⚡ {len(cercanos)} PARES CON RSI<=35 AHORA:")
        for r in cercanos[:10]:
            print(f"  {r['par']:<30} RSI={r['rsi_1h']:.1f}  vol=${r['volumen']:,.0f}")
    print(SEP)


if __name__ == "__main__":
    print(SEP)
    print(f"  SCANNER BINGX — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(SEP)
    pares = get_todos_los_pares()
    if not pares:
        print("[ERROR] No se pudieron obtener los pares.")
        exit(1)
    validos = escanear_todos(pares)
    imprimir_resumen(validos, len(pares))
    guardar_resultados(validos)
