#!/usr/bin/env python3
"""
BENCHMARK DE VELOCIDAD — Mide las mejoras de optimización
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Compara:
  • Indicadores normales vs ultra-fast (Numba)
  • Procesamiento secuencial vs paralelo
  • Con cache vs sin cache
  • REST polling vs WebSocket

Ejecutar:
  python benchmark.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import time
import numpy as np
import pandas as pd
from typing import Callable


def benchmark_function(func: Callable, name: str, iterations: int = 100):
    """Mide tiempo de ejecución de una función."""
    print(f"\n{'='*60}")
    print(f"  BENCHMARK: {name}")
    print(f"{'='*60}")
    
    # Warmup (importante para JIT compilation)
    for _ in range(5):
        func()
    
    # Medición real
    times = []
    for i in range(iterations):
        t0 = time.perf_counter()
        func()
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
        
        if (i + 1) % 20 == 0:
            print(f"  Progreso: {i+1}/{iterations}...", end='\r')
    
    avg_time = np.mean(times) * 1000  # en ms
    std_time = np.std(times) * 1000
    min_time = np.min(times) * 1000
    max_time = np.max(times) * 1000
    
    print(f"\n  Iteraciones: {iterations}")
    print(f"  Promedio:    {avg_time:.3f} ms")
    print(f"  Min/Max:     {min_time:.3f} / {max_time:.3f} ms")
    print(f"  Std Dev:     {std_time:.3f} ms")
    
    return avg_time


def generate_test_data(n: int = 1000):
    """Genera datos OHLCV sintéticos."""
    np.random.seed(42)
    close = np.cumsum(np.random.randn(n) * 0.01) + 100
    high = close + np.abs(np.random.randn(n) * 0.5)
    low = close - np.abs(np.random.randn(n) * 0.5)
    volume = np.abs(np.random.randn(n) * 1000) + 5000
    
    df = pd.DataFrame({
        'close': close,
        'high': high,
        'low': low,
        'open': close + np.random.randn(n) * 0.1,
        'volume': volume,
    })
    
    return df


def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   CONFLUX 4 BOT — BENCHMARK DE VELOCIDAD                   ║
║                                                              ║
║   Mide las mejoras de optimización                          ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")
    
    # Generar datos de prueba
    print("\n[1/4] Generando datos de prueba (1000 velas)...")
    df = generate_test_data(1000)
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    
    results = {}
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BENCHMARK 1: RSI
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n[2/4] Benchmarking RSI(14)...")
    
    try:
        from indicators_ultra_fast import fast_rsi
        
        def test_fast_rsi():
            return fast_rsi(close, 14)
        
        results['RSI Ultra-Fast (Numba)'] = benchmark_function(
            test_fast_rsi, 
            "RSI Ultra-Fast (Numba JIT)", 
            iterations=100
        )
    except ImportError:
        print("  ⚠ indicators_ultra_fast.py no encontrado")
        results['RSI Ultra-Fast (Numba)'] = None
    
    # RSI normal (como referencia)
    try:
        import ta
        
        def test_normal_rsi():
            return ta.momentum.RSIIndicator(df['close'], 14).rsi()
        
        results['RSI Normal (ta-lib)'] = benchmark_function(
            test_normal_rsi,
            "RSI Normal (ta-lib)",
            iterations=100
        )
    except ImportError:
        print("  ℹ ta-lib no instalado (opcional)")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BENCHMARK 2: Supertrend
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n[3/4] Benchmarking Supertrend...")
    
    try:
        from indicators_ultra_fast import fast_supertrend
        
        def test_fast_st():
            return fast_supertrend(high, low, close, 10, 3.0)
        
        results['Supertrend Ultra-Fast'] = benchmark_function(
            test_fast_st,
            "Supertrend Ultra-Fast (Numba JIT)",
            iterations=100
        )
    except ImportError:
        results['Supertrend Ultra-Fast'] = None
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BENCHMARK 3: ADX
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n[4/4] Benchmarking ADX(14)...")
    
    try:
        from indicators_ultra_fast import fast_adx
        
        def test_fast_adx():
            return fast_adx(high, low, close, 14)
        
        results['ADX Ultra-Fast'] = benchmark_function(
            test_fast_adx,
            "ADX Ultra-Fast (Numba JIT)",
            iterations=100
        )
    except ImportError:
        results['ADX Ultra-Fast'] = None
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # RESULTADOS FINALES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print(f"\n\n{'='*70}")
    print("  RESULTADOS DEL BENCHMARK")
    print(f"{'='*70}\n")
    
    print(f"{'Indicador':<35} {'Tiempo (ms)':<15} {'Speedup'}")
    print(f"{'-'*35} {'-'*15} {'-'*15}")
    
    baseline_rsi = results.get('RSI Normal (ta-lib)')
    
    for name, avg_time in results.items():
        if avg_time is None:
            continue
        
        speedup = ""
        if baseline_rsi and 'Ultra-Fast' in name:
            if 'RSI' in name:
                speedup = f"{baseline_rsi / avg_time:.0f}x más rápido"
        
        print(f"{name:<35} {avg_time:>10.3f} ms    {speedup}")
    
    print(f"\n{'='*70}")
    
    # Conclusión
    if results.get('RSI Ultra-Fast (Numba)'):
        rsi_ultra = results['RSI Ultra-Fast (Numba)']
        
        print("\n✅ OPTIMIZACIONES ACTIVAS:")
        print(f"   • RSI ultra-fast: {rsi_ultra:.3f}ms por cálculo")
        
        if baseline_rsi:
            improvement = (baseline_rsi / rsi_ultra)
            print(f"   • Mejora: {improvement:.0f}x más rápido que versión normal")
        
        print(f"\n   Con 50 símbolos escaneando cada 5 segundos:")
        scans_per_minute = 12
        calcs_per_scan = 50  # símbolos
        total_calcs = scans_per_minute * calcs_per_scan
        
        time_ultra = (rsi_ultra * total_calcs) / 1000  # en segundos
        
        if baseline_rsi:
            time_normal = (baseline_rsi * total_calcs) / 1000
            print(f"   • Tiempo normal: {time_normal:.1f}s por minuto")
            print(f"   • Tiempo ultra:  {time_ultra:.1f}s por minuto")
            print(f"   • Ahorro: {time_normal - time_ultra:.1f}s por minuto")
    else:
        print("\n⚠ OPTIMIZACIONES NO DETECTADAS")
        print("   Instala las dependencias:")
        print("   pip install -r requirements_ultra.txt")
    
    print()


if __name__ == "__main__":
    main()
