"""
diagnostico.py — Ejecutar para ver exactamente por qué no llegan señales
Uso: python diagnostico.py

Muestra para cada par:
  - Si los klines se parsean correctamente (closes > 0)
  - RSI actual
  - Posición del precio respecto a BB
  - Volumen 24h
  - Spread
  - Cuánto falta para cumplir cada condición
"""

import sys
import os

# Permite ejecutar desde cualquier directorio
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import exchange
from analizar import calcular_rsi, calcular_bb, calcular_atr

SEP = "─" * 70


def diagnosticar_par(par: str):
    print(f"\n{'═'*70}")
    print(f"  {par}")
    print(SEP)

    # 1. Klines
    klines = exchange.get_klines(par, intervalo="5m", limit=100)
    print(f"  Klines recibidos  : {len(klines)}")
    if len(klines) < 30:
        print(f"  ✗ FALLO: klines insuficientes")
        return

    data = exchange.parsear_klines(klines)
    closes = data["closes"]
    print(f"  Closes parseados  : {len(closes)}")

    if len(closes) < 30:
        print(f"  ✗ FALLO: closes insuficientes — BingX devuelve formato inesperado")
        print(f"  Ejemplo kline[0]  : {klines[0]}")
        return

    precio = closes[-1]
    print(f"  Precio actual     : {precio:.8f}")

    if precio <= 0:
        print(f"  ✗ FALLO: precio = 0 — error de parseo")
        return

    # 2. Indicadores
    rsi = calcular_rsi(closes, config.RSI_PERIODO)
    bb  = calcular_bb(closes, config.BB_PERIODO, config.BB_STD)
    atr = calcular_atr(data["highs"], data["lows"], closes, config.ATR_PERIODO)

    print(f"  RSI ({config.RSI_PERIODO})          : {rsi:.2f}  {'✓' if rsi < config.RSI_OVERSOLD else f'✗ (necesita <{config.RSI_OVERSOLD}, faltan {rsi-config.RSI_OVERSOLD:.1f} puntos)'}")
    print(f"  BB inferior       : {bb['inferior']:.8f}")
    print(f"  BB posición       : {bb['posicion']:.3f}  (0=fondo, 1=techo)")
    umbral_bb = bb["inferior"] * 1.005
    print(f"  Precio vs BB*1.005: {precio:.8f} vs {umbral_bb:.8f}  {'✓' if precio <= umbral_bb else f'✗ (precio {((precio/umbral_bb-1)*100):+.2f}% por encima)'}")
    print(f"  ATR               : {atr:.8f}")

    # 3. Filtros calidad
    volumen = exchange.get_volumen_24h(par)
    spread  = exchange.get_spread_pct(par)
    print(f"  Volumen 24h       : ${volumen:,.0f}  {'✓' if volumen >= config.VOLUMEN_MIN_USD else f'✗ (min ${config.VOLUMEN_MIN_USD:,.0f})'}")
    print(f"  Spread            : {spread:.3f}%  {'✓' if spread <= config.SPREAD_MAX_PCT else f'✗ (max {config.SPREAD_MAX_PCT}%)'}")

    # 4. SL/TP/RR
    sl = precio - (atr * config.SL_ATR_MULT)
    tp = precio + (atr * config.TP_ATR_MULT)
    riesgo    = precio - sl
    beneficio = tp - precio
    rr        = beneficio / riesgo if riesgo > 0 else 0
    print(f"  R:R proyectado    : {rr:.2f}  {'✓' if rr >= config.RR_MINIMO else f'✗ (min {config.RR_MINIMO})'}")

    # 5. Resumen
    pasa_rsi    = rsi < config.RSI_OVERSOLD
    pasa_bb     = precio <= bb["inferior"] * 1.005
    pasa_vol    = volumen >= config.VOLUMEN_MIN_USD
    pasa_spread = spread <= config.SPREAD_MAX_PCT
    pasa_rr     = rr >= config.RR_MINIMO

    total_pasa = sum([pasa_rsi, pasa_bb, pasa_vol, pasa_spread, pasa_rr])
    print(f"\n  Filtros OK: {total_pasa}/5  {'→ SEÑAL ✅' if total_pasa == 5 else '→ sin señal'}")
    if total_pasa < 5:
        faltantes = []
        if not pasa_rsi:    faltantes.append(f"RSI ({rsi:.1f} ≥ {config.RSI_OVERSOLD})")
        if not pasa_bb:     faltantes.append(f"BB (precio {((precio/umbral_bb-1)*100):+.1f}% sobre inf)")
        if not pasa_vol:    faltantes.append(f"Volumen (${volumen:,.0f})")
        if not pasa_spread: faltantes.append(f"Spread ({spread:.2f}%)")
        if not pasa_rr:     faltantes.append(f"R:R ({rr:.2f})")
        print(f"  Falla en: {' | '.join(faltantes)}")


def main():
    print(f"\n{'═'*70}")
    print(f"  DIAGNÓSTICO BOT — {len(config.PARES)} pares")
    print(f"  RSI_OVERSOLD={config.RSI_OVERSOLD} | BB_STD={config.BB_STD} | VOL_MIN=${config.VOLUMEN_MIN_USD:,.0f}")
    print(f"{'═'*70}")

    señales = 0
    for par in config.PARES:
        try:
            diagnosticar_par(par)
            # Contar si pasa todos los filtros
        except Exception as e:
            print(f"\n  [ERROR] {par}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'═'*70}")
    print(f"  Diagnóstico completado. Revisa los ✗ para ver qué filtros fallan.")
    print(f"  Si todos los cierres son RSI: el mercado no está en sobreventa ahora.")
    print(f"  Si closes=0: hay un problema de parseo de klines con BingX.")
    print(f"{'═'*70}\n")


if __name__ == "__main__":
    main()
