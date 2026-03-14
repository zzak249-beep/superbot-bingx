"""
PARCHE_analizar.py — APEX Bot v7.0
====================================
Instrucciones para integrar analizar_lateral.py en analizar.py v7.

En v7, analizar.py YA TIENE la lógica necesaria.
Este archivo documenta cómo funciona la integración y
dónde tocar si quieres ajustar el modo rango.

═══════════════════════════════════════════════════════════════
CÓMO ACTIVAR EL MODO RANGO
═══════════════════════════════════════════════════════════════

1. En Railway añade o cambia:
     RANGE_ACTIVO=true
     RANGE_ADX_MAX=22
     RANGE_SCORE_MIN=7

2. El bot activará range trading automáticamente cuando:
   - ADX < RANGE_ADX_MAX (mercado sin tendencia)
   - La señal tendencial falla (lado is None)
   - RANGE_ACTIVO=true

═══════════════════════════════════════════════════════════════
INTEGRACIÓN EN analizar.py (ya incluida en v7)
═══════════════════════════════════════════════════════════════

El código de integración en analizar_par() está al final,
justo antes del return None por falta de señal:

    # ── MODO RANGO (fallback si no hay señal tendencial) ──
    if lado is None and getattr(config, 'RANGE_ACTIVO', False):
        try:
            from analizar_lateral import detectar_rango, señal_en_rango
            rango = detectar_rango(candles, lookback=30)
            if rango.get('es_rango'):
                log.info(f'[RANGO] {par} ADX={rango["adx"]:.1f} amp={rango["amplitud_pct"]:.1f}%')
                pat_long  = detectar_patron_vela(candles) if ... else {}
                pat_short = detectar_patron_vela(candles) if ... else {}
                señal_r = señal_en_rango(
                    par, candles, rango, rsi,
                    pat_long, pat_short,
                    sl, ss,  # scores base
                )
                score_min_rango = getattr(config, 'RANGE_SCORE_MIN', 7)
                if señal_r and señal_r['score'] >= score_min_rango:
                    # Completar campos necesarios
                    señal_r.update({
                        'kz': kz['nombre'], 'htf': htf_1h, 'htf_4h': htf_4h,
                        'vwap': vwap, 'sobre_vwap': sobre_vwap,
                        'macro_btc_4h': macro_btc_4h,
                        # ... resto de campos
                    })
                    registrar_senal_ts(par)
                    return señal_r
        except ImportError:
            pass

═══════════════════════════════════════════════════════════════
CUÁNDO USAR MODO RANGO
═══════════════════════════════════════════════════════════════

✅ Activar cuando:
   - Bot lleva 2+ días sin señales
   - ADX global del mercado < 20
   - BTC en consolidación lateral (p.ej. entre $80k-$85k sin romper)

❌ No activar cuando:
   - Hay tendencia clara en BTC (ADX > 30)
   - En días de alta volatilidad (noticias macro, FOMC, halving)
   - Con pares de bajo volumen

═══════════════════════════════════════════════════════════════
DIFERENCIAS VS SEÑAL TENDENCIAL
═══════════════════════════════════════════════════════════════

Modo rango:
  TP = 55% del rango (conservador)
  SL = fuera del rango + 8% buffer
  RR mínimo = 1.5x (más bajo que tendencia 2.0x)
  Score mínimo = 7 (RANGE_SCORE_MIN)
  mercado_lateral = True en la señal

═══════════════════════════════════════════════════════════════
VARIABLES RAILWAY PARA MODO RANGO
═══════════════════════════════════════════════════════════════

RANGE_ACTIVO=true          # activar
RANGE_ADX_MAX=22           # ADX máximo para considerar lateral
RANGE_SCORE_MIN=7          # score mínimo para señal rango
"""

# Este archivo es solo documentación — no contiene código ejecutable.
# Ver analizar_lateral.py para la implementación.
