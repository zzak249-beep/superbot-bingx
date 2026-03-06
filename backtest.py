"""
backtest.py — Backtesting RSI + Bollinger Bands sobre datos de BingX
Genera ranking_pares.txt y config_recomendado.py automáticamente
"""

import time
import json
from datetime import datetime
import numpy as np

import exchange
import config
from analizar import calcular_rsi, calcular_bb, calcular_atr

# ============================================================
# PARÁMETROS DEL BACKTEST
# ============================================================
BT_INTERVALO      = "1h"      # Temporalidad para backtest
BT_VELAS          = 200       # Velas históricas
BT_RSI_OVERSOLD   = 30
BT_BB_STD         = 2.0
BT_SL_ATR_MULT    = 1.5
BT_TP_ATR_MULT    = 2.5
BT_MIN_TRADES     = 3         # Mínimo trades para considerar un par
BT_MIN_WR         = 50.0
BT_MIN_PF         = 1.2
BT_CAPITAL        = 100.0     # Capital inicial de simulación


def backtest_par(par: str) -> dict:
    """
    Simula la estrategia RSI+BB sobre datos históricos de un par.
    Retorna métricas de rendimiento.
    """
    klines = exchange.get_klines(par, intervalo=BT_INTERVALO, limit=BT_VELAS)
    if len(klines) < 50:
        return {"par": par, "status": "insuficiente", "trades": 0}

    data = exchange.parsear_klines(klines)
    closes = data["closes"]
    highs  = data["highs"]
    lows   = data["lows"]

    if len(closes) < 30:
        return {"par": par, "status": "insuficiente", "trades": 0}

    trades      = []
    en_posicion = False
    entrada     = 0.0
    sl          = 0.0
    tp          = 0.0

    for i in range(30, len(closes)):
        precio = closes[i]

        if not en_posicion:
            # Calcular indicadores sobre velas anteriores
            rsi = calcular_rsi(closes[:i], 14)
            bb  = calcular_bb(closes[:i], 20, BT_BB_STD)
            atr = calcular_atr(highs[:i], lows[:i], closes[:i], 14)

            # Condición de entrada
            if rsi < BT_RSI_OVERSOLD and precio <= bb["inferior"] * 1.002:
                entrada     = precio
                sl          = precio - (atr * BT_SL_ATR_MULT)
                tp          = precio + (atr * BT_TP_ATR_MULT)
                en_posicion = True

        else:
            # Verificar salida
            if precio <= sl:
                pnl = (sl - entrada) / entrada
                trades.append({"resultado": "LOSS", "pnl": pnl, "entrada": entrada, "salida": sl})
                en_posicion = False
            elif precio >= tp:
                pnl = (tp - entrada) / entrada
                trades.append({"resultado": "WIN", "pnl": pnl, "entrada": entrada, "salida": tp})
                en_posicion = False

    if not trades:
        return {"par": par, "status": "sin_trades", "trades": 0}

    wins      = [t for t in trades if t["resultado"] == "WIN"]
    losses    = [t for t in trades if t["resultado"] == "LOSS"]
    total     = len(trades)
    wr        = len(wins) / total * 100

    ganancias = sum(t["pnl"] for t in wins)
    perdidas  = abs(sum(t["pnl"] for t in losses))
    pf        = ganancias / perdidas if perdidas > 0 else 999.0

    # PnL simulado con capital inicial
    pnl_usd   = sum(t["pnl"] for t in trades) * BT_CAPITAL

    avg_win   = (ganancias / len(wins)) * BT_CAPITAL if wins else 0
    avg_loss  = (perdidas / len(losses)) * BT_CAPITAL if losses else 0

    return {
        "par":      par,
        "status":   "ok",
        "trades":   total,
        "wins":     len(wins),
        "losses":   len(losses),
        "wr":       wr,
        "pf":       pf,
        "pnl_usd":  pnl_usd,
        "avg_win":  avg_win,
        "avg_loss": avg_loss,
        "rentable": wr >= BT_MIN_WR and pf >= BT_MIN_PF
    }


def escanear_todos(pares: list = None, max_pares: int = None) -> list:
    """
    Escanea todos los pares y retorna resultados ordenados.
    """
    if pares is None:
        pares = config.PARES

    if max_pares:
        pares = pares[:max_pares]

    print("=" * 65)
    print(f"  SCANNER BINGX — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  RSI<{BT_RSI_OVERSOLD} | R:R>={BT_TP_ATR_MULT/BT_SL_ATR_MULT:.1f} | Solo LONG")
    print(f"  Total pares a analizar: {len(pares)}")
    print("=" * 65)

    resultados = []
    total = len(pares)

    for i, par in enumerate(pares):
        pct = int((i + 1) / total * 100)
        resultado = backtest_par(par)

        if resultado["trades"] == 0:
            status = f"  [{i+1:4}/{total}]  {pct:3}%  {par:<25} 0tr (insuficiente)"
        elif resultado["trades"] < BT_MIN_TRADES:
            status = f"  [{i+1:4}/{total}]  {pct:3}%  {par:<25} {resultado['trades']}tr (insuficiente)"
        else:
            marca  = "✓" if resultado.get("rentable") else " "
            status = (
                f"  [{i+1:4}/{total}]  {pct:3}%  {par:<25}"
                f"{marca}  {resultado['trades']}tr  "
                f"WR:{resultado['wr']:5.0f}%  "
                f"PF:{min(resultado['pf'], 999):.2f}  "
                f"${resultado['pnl_usd']:+.4f}"
            )

        print(status)
        resultados.append(resultado)
        time.sleep(0.1)  # Rate limit

    return resultados


def generar_ranking(resultados: list) -> list:
    """Filtra y ordena los resultados por PF y WR"""
    con_datos = [r for r in resultados if r.get("trades", 0) >= BT_MIN_TRADES]
    rentables = [r for r in con_datos if r.get("rentable")]
    rentables.sort(key=lambda x: (x["pf"] * x["wr"]), reverse=True)
    return rentables


def guardar_resultados(resultados: list, rentables: list):
    total_con_datos = len([r for r in resultados if r.get("trades", 0) >= BT_MIN_TRADES])

    # Ranking en texto
    with open("ranking_pares.txt", "w", encoding="utf-8") as f:
        f.write(f"SCANNER BINGX — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"RSI<{BT_RSI_OVERSOLD} | R:R>={BT_TP_ATR_MULT/BT_SL_ATR_MULT:.1f} | Solo LONG\n")
        f.write(f"Total analizados: {len(resultados)} | Con datos: {total_con_datos} | Rentables: {len(rentables)}\n\n")

        f.write(f"{'PAR':<28} {'TR':>4} {'WR':>6} {'PF':>6} {'PnL':>10} {'AW':>8} {'AL':>8}\n")
        f.write("-" * 75 + "\n")

        for i, r in enumerate(rentables[:20], 1):
            marca = "★" if r["wr"] >= BT_MIN_WR else " "
            f.write(
                f"{marca}{r['par']:<27} "
                f"{r['trades']:>3}tr "
                f"WR:{r['wr']:5.0f}% "
                f"PF:{min(r['pf'], 999):6.2f} "
                f"${r['pnl_usd']:+8.4f} "
                f"↑${r['avg_win']:6.4f} "
                f"↓${r['avg_loss']:6.4f}\n"
            )

    print(f"\n  Guardado en: ranking_pares.txt")

    # Config recomendado
    top15 = [r["par"] for r in rentables[:15]]

    with open("config_recomendado.py", "w", encoding="utf-8") as f:
        f.write(f'# Generado automáticamente — {datetime.now().strftime("%Y-%m-%d %H:%M")}\n')
        f.write(f'# TOP {len(top15)} pares rentables del último backtest\n\n')
        f.write(f'PARES = {json.dumps(top15, indent=4)}\n\n')
        f.write(f'# Parámetros recomendados basados en backtest:\n')
        f.write(f'RSI_OVERSOLD = {BT_RSI_OVERSOLD}\n')
        f.write(f'BB_STD       = {BT_BB_STD}\n')
        f.write(f'SL_ATR_MULT  = {BT_SL_ATR_MULT}\n')
        f.write(f'TP_ATR_MULT  = {BT_TP_ATR_MULT}\n')

    print(f"  Guardado en: config_recomendado.py")
    print(f"\n  ► Copia config_recomendado.py → config.py para usar los mejores pares")


def imprimir_resumen(rentables: list, total_resultados: int):
    total_con_datos = total_resultados
    print("\n" + "=" * 65)
    print(f"  RESULTADOS — {total_con_datos} pares con datos suficientes")
    print(f"  Criterio rentable: WR>={BT_MIN_WR}% y PF>={BT_MIN_PF}")
    print("=" * 65)

    print(f"\n  TOP {min(15, len(rentables))} RENTABLES (WR>={BT_MIN_WR}% y PF>={BT_MIN_PF}):")
    print("  " + "-" * 60)
    for i, r in enumerate(rentables[:15], 1):
        print(
            f"  {i:2}. {r['par']:<25} "
            f"WR:{r['wr']:.0f}%  "
            f"PF:{min(r['pf'], 999):.2f}  "
            f"${r['pnl_usd']:+.4f}"
        )
    print("=" * 65)


if __name__ == "__main__":
    import sys

    # Uso: python backtest.py [max_pares]
    max_p = int(sys.argv[1]) if len(sys.argv) > 1 else None

    resultados = escanear_todos(pares=config.PARES, max_pares=max_p)
    rentables  = generar_ranking(resultados)
    imprimir_resumen(rentables, len([r for r in resultados if r.get("trades", 0) >= BT_MIN_TRADES]))
    guardar_resultados(resultados, rentables)
