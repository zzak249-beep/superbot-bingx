#!/usr/bin/env python3
"""
Optimizador de Parámetros
Encuentra los mejores parámetros para tu estrategia
"""

import os
import asyncio
import json
from itertools import product
from datetime import datetime
from typing import Dict, List
from dotenv import load_dotenv
import numpy as np

from strategy import TradingStrategy
from bingx_client import BingXClient

load_dotenv()


class ParameterOptimizer:
    """Optimizador de parámetros"""
    
    def __init__(self, symbol: str = 'BTC-USDT', timeframe: str = '1h'):
        """Inicializar"""
        self.symbol = symbol
        self.timeframe = timeframe
        self.exchange = BingXClient()
        
        self.results = []
    
    async def test_parameters(self,
                             linreg_lengths: List[int],
                             linreg_mults: List[float],
                             adx_thresholds: List[int]):
        """Testear combinaciones de parámetros"""
        
        print(f"\n{'='*80}")
        print(f"🔍 OPTIMIZADOR DE PARÁMETROS - {self.symbol}")
        print(f"{'='*80}\n")
        
        # Obtener datos
        print(f"⏳ Descargando datos...")
        candles = await self.exchange.get_klines(self.symbol, self.timeframe, limit=500)
        
        if not candles:
            print(f"❌ No hay datos")
            return
        
        print(f"✅ {len(candles)} velas obtenidas\n")
        
        # Convertir a arrays
        closes = np.array([float(c['close']) for c in candles])
        highs = np.array([float(c['high']) for c in candles])
        lows = np.array([float(c['low']) for c in candles])
        
        total_combinations = len(linreg_lengths) * len(linreg_mults) * len(adx_thresholds)
        print(f"Testeando {total_combinations} combinaciones...\n")
        
        tested = 0
        
        for linreg_len in linreg_lengths:
            for linreg_m in linreg_mults:
                for adx_thr in adx_thresholds:
                    tested += 1
                    
                    # Crear estrategia con estos parámetros
                    strategy = TradingStrategy(
                        linreg_length=linreg_len,
                        linreg_mult=linreg_m,
                        adx_threshold=adx_thr
                    )
                    
                    # Contar señales
                    signal_count = 0
                    high_confidence = 0
                    avg_confidence = 0
                    
                    for i in range(linreg_len + 50, len(candles)):
                        signal = strategy.analyze(candles[:i+1])
                        
                        if signal['action'] != 'NONE':
                            signal_count += 1
                            confidence = signal.get('confidence', 0)
                            high_confidence += 1 if confidence >= 70 else 0
                            avg_confidence += confidence
                    
                    if signal_count > 0:
                        avg_confidence /= signal_count
                    
                    result = {
                        'linreg_length': linreg_len,
                        'linreg_mult': linreg_m,
                        'adx_threshold': adx_thr,
                        'signals': signal_count,
                        'high_confidence': high_confidence,
                        'avg_confidence': avg_confidence,
                        'score': signal_count * (avg_confidence / 100)  # Más señales + más confianza
                    }
                    
                    self.results.append(result)
                    
                    # Mostrar progreso
                    if tested % 10 == 0:
                        print(f"Progreso: {tested}/{total_combinations} ({tested/total_combinations*100:.0f}%)")
        
        # Ordenar por score
        self.results.sort(key=lambda x: x['score'], reverse=True)
        
        # Mostrar top 10
        print(f"\n{'='*80}")
        print(f"🏆 TOP 10 COMBINACIONES")
        print(f"{'='*80}\n")
        
        print(f"{'Rank':<5} {'LinReg L':<10} {'LinReg M':<10} {'ADX Th':<8} {'Signals':<10} {'Conf %':<10} {'Score':<10}")
        print("-" * 80)
        
        for rank, result in enumerate(self.results[:10], 1):
            print(f"{rank:<5} {result['linreg_length']:<10} {result['linreg_mult']:<10.1f} "
                  f"{result['adx_threshold']:<8} {result['signals']:<10} "
                  f"{result['avg_confidence']:<10.1f} {result['score']:<10.2f}")
        
        # Sugerir mejor combinación
        if self.results:
            best = self.results[0]
            print(f"\n{'='*80}")
            print(f"✅ RECOMENDACIÓN")
            print(f"{'='*80}\n")
            print(f"Los mejores parámetros encontrados son:")
            print(f"  LINREG_LENGTH={best['linreg_length']}")
            print(f"  LINREG_MULT={best['linreg_mult']:.1f}")
            print(f"  ADX_THRESHOLD={best['adx_threshold']}")
            print(f"\nEsta combinación genera:")
            print(f"  • {best['signals']} señales en 500 velas")
            print(f"  • Confianza promedio: {best['avg_confidence']:.1f}%")
            print(f"  • {best['high_confidence']} señales con confianza >= 70%")
            print(f"\nAñade estos valores a tu .env:\n")
            print(f"LINREG_LENGTH={best['linreg_length']}")
            print(f"LINREG_MULT={best['linreg_mult']:.1f}")
            print(f"ADX_THRESHOLD={best['adx_threshold']}\n")


async def main():
    """Función principal"""
    
    optimizer = ParameterOptimizer('BTC-USDT', '1h')
    
    # Rangos a testear
    linreg_lengths = [40, 50, 60, 80]
    linreg_mults = [1.8, 2.0, 2.2, 2.5]
    adx_thresholds = [20, 25, 30, 35]
    
    await optimizer.test_parameters(
        linreg_lengths,
        linreg_mults,
        adx_thresholds
    )
    
    print("✅ Optimización completada\n")


if __name__ == "__main__":
    asyncio.run(main())
