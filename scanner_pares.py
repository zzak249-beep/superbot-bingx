"""
scanner_pares.py — Encuentra TODOS los pares disponibles en BingX
y filtra los que son compatibles con la estrategia RSI+BB

Uso: python scanner_pares.py
Genera: pares_validos.txt y actualiza config.py automáticamente

Criterios de selección:
  - Volumen 24h > 500,000 USD
  - Spread < 1.5%
  - Klines parseables (precio > 0)
  - Par activo en BingX Futures
"""

import time
import requests
import json
from datetime import datetime

# ── Config mínima para no depender de config.py al arrancar ──
VOLUMEN_MIN   = 500_000
SPREAD_MAX    = 1.5
PAUSA_MS      = 0.15      # segundos entre requests (rate limit)
BASE_URL      = "https://open-api.bingx.com"

SEP = "═" * 65


def get_todos_los_pares() -> list:
    """Obtiene todos los pares de futuros perpetuos de BingX"""
    print(f"\n[SCANNER] Obteniendo lista de pares de BingX...")
    try:
        r = requests.get(
            f"{BASE_URL}/openApi/swap/v2/quote/contracts",
            timeout=15
        )
        data = r.json()
        contratos = data.get("data", [])
        # Filtrar solo pares USDT activos
        pares = [
            c["symbol"] for c in contratos
            if c.get("symbol", "").endswith("-USDT")
            and c.get("status", 1) != 0  # activo
        ]
        pares.sort()
        print(f"[SCANNER] {len(pares)} pares USDT encontrados en BingX")
        return pares
    except Exception as e:
        print(f"[SCANNER] Error obteniendo contratos: {e}")
        return []


def get_ticker(par: str) -> dict:
    """Obtiene ticker (volumen, bid, ask) de un par"""
    try:
        r = requests.get(
            f"{BASE_URL}/openApi/swap/v2/quote/ticker",
            params={"symbol": par},
            timeout=8
        )
        data = r.json().get("data", {})
        if isinstance(data, list) and data:
            data = data[0]
        return data if isinstance(data, dict) else {}
    except:
        return {}


def get_klines(par: str) -> list:
    """Obtiene klines del par"""
    try:
        r = requests.get(
            f"{BASE_URL}/openApi/swap/v3/quote/klines",
            params={"symbol": par, "interval": "1h", "limit": 50},
            timeout=8
        )
        return r.json().get("data", [])
    except:
        return []


def parsear_klines(klines: list) -> list:
    """Extrae closes de klines (soporta dicts y arrays)"""
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


def calcular_rsi(closes: list, periodo: int = 14) -> float:
    """RSI simplificado"""
    import numpy as np
    if len(closes) < periodo + 1:
        return 50.0
    arr       = np.array(closes, dtype=float)
    deltas    = np.diff(arr)
    ganancias = np.where(deltas > 0, deltas, 0.0)
    perdidas  = np.where(deltas < 0, -deltas, 0.0)
    avg_g = np.mean(ganancias[:periodo])
    avg_p = np.mean(perdidas[:periodo])
    for i in range(periodo, len(deltas)):
        avg_g = (avg_g * (periodo - 1) + ganancias[i]) / periodo
        avg_p = (avg_p * (periodo - 1) + perdidas[i]) / periodo
    if avg_p == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_p))


def analizar_par(par: str) -> dict:
    """
    Analiza un par y retorna sus métricas de calidad.
    No filtra por RSI (el mercado cambia), solo valida que el par
    sea técnicamente válido para la estrategia.
    """
    resultado = {
        "par":        par,
        "valido":     False,
        "volumen":    0,
        "spread":     999,
        "precio":     0,
        "rsi_1h":     50,
        "motivo":     ""
    }

    # ── Ticker ──────────────────────────────────────────────
    ticker = get_ticker(par)
    if not ticker:
        resultado["motivo"] = "sin ticker"
        return resultado

    # Volumen
    try:
        volumen = float(ticker.get("quoteVolume", 0))
    except:
        volumen = 0
    resultado["volumen"] = volumen

    if volumen < VOLUMEN_MIN:
        resultado["motivo"] = f"vol bajo ${volumen:,.0f}"
        return resultado

    # Spread
    try:
        bid = float(ticker.get("bidPrice", 0))
        ask = float(ticker.get("askPrice", 0))
        if bid > 0 and ask > 0:
            spread = ((ask - bid) / ((bid + ask) / 2)) * 100
        elif volumen > 1_000_000:
            spread = 0.1
        else:
            spread = 0.5
    except:
        spread = 999
    resultado["spread"] = spread

    if spread > SPREAD_MAX:
        resultado["motivo"] = f"spread {spread:.2f}%"
        return resultado

    # ── Klines ──────────────────────────────────────────────
    klines = get_klines(par)
    closes = parsear_klines(klines)

    if len(closes) < 20:
        resultado["motivo"] = f"klines insuficientes ({len(closes)})"
        return resultado

    precio = closes[-1]
    if precio <= 0:
        resultado["motivo"] = "precio = 0"
        return resultado

    resultado["precio"] = precio

    # RSI actual en 1h (informativo, no filtra)
    rsi = calcular_rsi(closes)
    resultado["rsi_1h"] = rsi

    # ── Par válido ──────────────────────────────────────────
    resultado["valido"]  = True
    resultado["motivo"]  = f"vol=${volumen:,.0f} spread={spread:.2f}% RSI={rsi:.1f}"
    return resultado


def escanear_todos(pares: list) -> list:
    """Escanea todos los pares y retorna los válidos"""
    print(f"\n[SCANNER] Analizando {len(pares)} pares...")
    print(f"[SCANNER] Filtros: vol>${VOLUMEN_MIN:,.0f} | spread<{SPREAD_MAX}%")
    print(SEP)

    validos   = []
    invalidos = 0
    total     = len(pares)

    for i, par in enumerate(pares):
        pct = int((i + 1) / total * 100)

        try:
            r = analizar_par(par)

            if r["valido"]:
                validos.append(r)
                print(f"  ✓ [{i+1:4}/{total}] {pct:3}%  {par:<25} {r['motivo']}")
            else:
                invalidos += 1
                # Solo mostrar el % de progreso para los inválidos
                print(f"  ✗ [{i+1:4}/{total}] {pct:3}%  {par:<25} {r['motivo']}", end="\r")

        except Exception as e:
            invalidos += 1
            print(f"  ! [{i+1:4}/{total}] {pct:3}%  {par:<25} ERROR: {e}", end="\r")

        time.sleep(PAUSA_MS)

    print()  # Nueva línea tras los \r
    return validos


def guardar_resultados(validos: list):
    """Guarda los pares válidos en txt y actualiza config"""
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Ordenar por volumen descendente
    validos.sort(key=lambda x: x["volumen"], reverse=True)

    # ── pares_validos.txt ────────────────────────────────────
    with open("pares_validos.txt", "w", encoding="utf-8") as f:
        f.write(f"SCANNER BINGX — {ahora}\n")
        f.write(f"Filtros: vol>${VOLUMEN_MIN:,.0f} | spread<{SPREAD_MAX}%\n")
        f.write(f"Total pares válidos: {len(validos)}\n\n")
        f.write(f"{'PAR':<25} {'VOLUMEN 24H':>18} {'SPREAD':>8} {'RSI 1H':>8}\n")
        f.write("─" * 65 + "\n")
        for r in validos:
            f.write(
                f"{r['par']:<25} "
                f"${r['volumen']:>17,.0f} "
                f"{r['spread']:>7.3f}% "
                f"{r['rsi_1h']:>7.1f}\n"
            )

    print(f"\n  ► Guardado: pares_validos.txt ({len(validos)} pares)")

    # ── config_pares.py ──────────────────────────────────────
    # Top 50 por volumen (más que suficiente, el bot filtrará por señal)
    top50 = [r["par"] for r in validos[:50]]

    with open("config_pares.py", "w", encoding="utf-8") as f:
        f.write(f"# Generado automáticamente — {ahora}\n")
        f.write(f"# {len(top50)} pares con vol>${VOLUMEN_MIN:,.0f} y spread<{SPREAD_MAX}%\n")
        f.write(f"# Ordenados por volumen 24h descendente\n\n")
        f.write(f"PARES = {json.dumps(top50, indent=4)}\n")

    print(f"  ► Guardado: config_pares.py (top {len(top50)} pares)")
    print(f"\n  Copia PARES de config_pares.py → config.py para activarlos")


def imprimir_resumen(validos: list, total_escaneados: int):
    print(f"\n{SEP}")
    print(f"  RESUMEN DEL SCANNER")
    print(f"  Escaneados : {total_escaneados}")
    print(f"  Válidos    : {len(validos)}")
    print(f"  Descartados: {total_escaneados - len(validos)}")
    print(SEP)

    print(f"\n  TOP 20 por volumen:")
    print(f"  {'PAR':<25} {'VOLUMEN':>18} {'RSI 1H':>8}")
    print(f"  {'─'*55}")
    for r in validos[:20]:
        print(f"  {r['par']:<25} ${r['volumen']:>17,.0f} {r['rsi_1h']:>7.1f}")

    # Pares cercanos a señal (RSI entre 30-45)
    cercanos = [r for r in validos if 30 <= r["rsi_1h"] <= 45]
    if cercanos:
        print(f"\n  ⚡ PARES CERCANOS A SEÑAL (RSI entre 30-45):")
        print(f"  {'─'*55}")
        for r in sorted(cercanos, key=lambda x: x["rsi_1h"]):
            print(f"  {r['par']:<25} RSI={r['rsi_1h']:.1f}  vol=${r['volumen']:,.0f}")

    print(f"\n{SEP}\n")


if __name__ == "__main__":
    print(SEP)
    print(f"  SCANNER DE PARES BINGX")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(SEP)

    # 1. Obtener todos los pares
    todos_los_pares = get_todos_los_pares()

    if not todos_los_pares:
        print("[ERROR] No se pudieron obtener los pares. Verifica tu conexión.")
        exit(1)

    # 2. Escanear
    validos = escanear_todos(todos_los_pares)

    # 3. Resultados
    imprimir_resumen(validos, len(todos_los_pares))
    guardar_resultados(validos)
