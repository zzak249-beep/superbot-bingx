#!/usr/bin/env python3
"""
Monitor de Análisis Multi-Par en Tiempo Real
Analiza todos los pares y muestra señales activas
"""

import os
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv

from strategy import TradingStrategy
from bingx_client import BingXClient

logging.basicConfig(level=logging.WARNING)

load_dotenv()


class MultiPairMonitor:
    """Monitor de análisis multi-par"""
    
    def __init__(self):
        """Inicializar monitor"""
        symbols_str = os.getenv('SYMBOLS', 'BTC-USDT,ETH-USDT,SOL-USDT')
        self.symbols = [s.strip() for s in symbols_str.split(',')]
        self.timeframe = os.getenv('TIMEFRAME', '15m')
        
        self.exchange = BingXClient()
        self.strategy = TradingStrategy()
        
        print(f"\n{'='*80}")
        print(f"🔍 MONITOR MULTI-PAR - ULTRA OPTIMIZADO v4")
        print(f"{'='*80}")
        print(f"📊 Analizando {len(self.symbols)} pares")
        print(f"⏱️ Timeframe: {self.timeframe}")
        print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*80}\n")
    
    async def analyze_all(self):
        """Analizar todos los pares"""
        print("⏳ Analizando pares...\n")
        
        tasks = [self.analyze_pair(symbol) for symbol in self.symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Procesar
        analysis_data = []
        signals_found = []
        
        for symbol, result in zip(self.symbols, results):
            if isinstance(result, Exception):
                analysis_data.append([symbol, '❌ ERROR', '-', '-', '-', '-', '-', '-'])
                continue
            
            if result['action'] != 'NONE':
                signals_found.append((symbol, result))
            
            action_emoji = {'LONG': '🟢', 'SHORT': '🔴', 'NONE': '⚪'}.get(result['action'], '⚪')
            confidence = result.get('confidence', 0)
            price = result.get('price', 0)
            sl = result.get('stop_loss', 0)
            tp = result.get('take_profit', 0)
            
            if result['action'] != 'NONE' and sl and tp:
                risk = abs(price - sl)
                reward = abs(tp - price)
                rr_ratio = f"1:{reward/risk:.2f}" if risk > 0 else '-'
                risk_pct = f"{(risk/price)*100:.2f}%"
            else:
                rr_ratio = '-'
                risk_pct = '-'
            
            analysis_data.append([
                symbol,
                f"{action_emoji} {result['action']}",
                f"{confidence}%",
                f"{price:.2f}" if price else '-',
                f"{sl:.2f}" if sl else '-',
                f"{tp:.2f}" if tp else '-',
                risk_pct,
                rr_ratio
            ])
        
        # Mostrar tabla
        print(f"{'PAR':<12} {'SEÑAL':<15} {'CONF':<8} {'PRECIO':<12} {'SL':<12} {'TP':<12} {'RIESGO':<10} {'R:R':<10}")
        print("-" * 100)
        
        for row in analysis_data:
            print(f"{row[0]:<12} {row[1]:<15} {row[2]:<8} {row[3]:<12} {row[4]:<12} {row[5]:<12} {row[6]:<10} {row[7]:<10}")
        
        print(f"\n{'='*80}")
        print(f"📊 RESUMEN")
        print(f"{'='*80}")
        
        long_count = sum(1 for _, r in zip(self.symbols, results) if not isinstance(r, Exception) and r['action'] == 'LONG')
        short_count = sum(1 for _, r in zip(self.symbols, results) if not isinstance(r, Exception) and r['action'] == 'SHORT')
        
        print(f"✅ Pares analizados: {len(self.symbols)}")
        print(f"🟢 Señales LONG: {long_count}")
        print(f"🔴 Señales SHORT: {short_count}")
        print(f"⚪ Sin señal: {len(self.symbols) - long_count - short_count}")
        print(f"{'='*80}\n")
        
        # Señales detalladas
        if signals_found:
            print(f"{'='*80}")
            print(f"🎯 SEÑALES ACTIVAS ({len(signals_found)})")
            print(f"{'='*80}\n")
            
            for symbol, signal in signals_found:
                action_emoji = '🟢' if signal['action'] == 'LONG' else '🔴'
                confidence = signal.get('confidence', 0)
                adx = signal.get('adx', 0)
                rsi = signal.get('rsi', 0)
                
                print(f"{action_emoji} <b>{signal['action']}</b> - {symbol} (Conf: {confidence}%)")
                print(f"   💰 Precio: {signal['price']:.4f}")
                print(f"   🛑 Stop Loss: {signal['stop_loss']:.4f}")
                print(f"   🎯 Take Profit: {signal['take_profit']:.4f}")
                print(f"   📊 ADX: {adx:.1f} | RSI: {rsi:.1f}")
                print(f"   ⚡ {signal['reasons']}\n")
    
    async def analyze_pair(self, symbol: str) -> dict:
        """Analizar par"""
        try:
            candles = await self.exchange.get_klines(symbol, self.timeframe, limit=150)
            
            if not candles:
                return {'action': 'NONE'}
            
            signal = self.strategy.analyze(candles)
            signal['symbol'] = symbol
            
            return signal
            
        except Exception as e:
            logger.error(f"Error: {e}")
            raise


async def main():
    """Principal"""
    monitor = MultiPairMonitor()
    await monitor.analyze_all()
    print("✅ Análisis completado\n")


if __name__ == "__main__":
    asyncio.run(main())
