#!/usr/bin/env python3
"""
Backtesting de Estrategia
Valida la estrategia con datos históricos
"""

import os
import asyncio
import json
from datetime import datetime, timedelta
from typing import List, Dict
from dotenv import load_dotenv

from strategy import TradingStrategy
from bingx_client import BingXClient

load_dotenv()


class BacktestEngine:
    """Engine de backtesting"""
    
    def __init__(self, symbol: str, timeframe: str = '1h'):
        """Inicializar"""
        self.symbol = symbol
        self.timeframe = timeframe
        self.exchange = BingXClient()
        self.strategy = TradingStrategy()
        
        self.trades = []
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = 0.0
    
    async def run(self, candles_count: int = 500):
        """Ejecutar backtest"""
        
        print(f"\n{'='*80}")
        print(f"🔄 BACKTESTING: {self.symbol} ({self.timeframe})")
        print(f"{'='*80}\n")
        
        print(f"⏳ Descargando {candles_count} velas...")
        candles = await self.exchange.get_klines(self.symbol, self.timeframe, limit=candles_count)
        
        if not candles:
            print(f"❌ No hay datos disponibles")
            return
        
        print(f"✅ {len(candles)} velas obtenidas")
        print(f"📅 Período: {self._format_date(candles[0]['timestamp'])} a {self._format_date(candles[-1]['timestamp'])}\n")
        
        # Simulación simple
        position_open = False
        entry_price = 0
        entry_idx = 0
        entry_signal = {}
        
        print(f"{'Idx':<5} {'Fecha':<20} {'Precio':<12} {'Señal':<12} {'Conf':<6} {'Action':<15}")
        print("-" * 80)
        
        for i in range(100, len(candles)):  # Usar últimas 400 velas
            # Analizar hasta aquí
            analysis_candles = candles[:i+1]
            signal = self.strategy.analyze(analysis_candles)
            
            confidence = signal.get('confidence', 0)
            
            # Mostrar cada 10 velas
            if i % 10 == 0:
                signal_emoji = {
                    'LONG': '🟢',
                    'SHORT': '🔴',
                    'NONE': '⚪'
                }.get(signal['action'], '⚪')
                
                price = candles[i]['close']
                print(f"{i:<5} {self._format_date(candles[i]['timestamp']):<20} ${price:<11,.2f} {signal_emoji} {signal['action']:<10} {confidence:<6} ", end='')
            
            # LÓGICA DE TRADING
            if not position_open and signal['action'] in ['LONG', 'SHORT']:
                # Abrir posición
                if confidence >= 50:
                    position_open = True
                    entry_price = signal['price']
                    entry_idx = i
                    entry_signal = signal
                    
                    if i % 10 == 0:
                        print("ENTER")
                    else:
                        print(f"{i:<5} {self._format_date(candles[i]['timestamp']):<20} ${entry_price:<11,.2f} ENTRY")
            
            elif position_open:
                current_price = candles[i]['close']
                
                # Verificar SL
                sl = entry_signal.get('stop_loss', 0)
                tp = entry_signal.get('take_profit', 0)
                
                should_close = False
                pnl = 0
                
                if entry_signal['action'] == 'LONG':
                    if current_price <= sl:
                        # Hit SL
                        should_close = True
                        pnl = current_price - entry_price
                    elif current_price >= tp:
                        # Hit TP
                        should_close = True
                        pnl = current_price - entry_price
                
                elif entry_signal['action'] == 'SHORT':
                    if current_price >= sl:
                        # Hit SL
                        should_close = True
                        pnl = entry_price - current_price
                    elif current_price <= tp:
                        # Hit TP
                        should_close = True
                        pnl = entry_price - current_price
                
                # Cerrar si alcanzamos SL/TP
                if should_close:
                    position_open = False
                    bars_held = i - entry_idx
                    
                    trade = {
                        'entry_price': entry_price,
                        'exit_price': current_price,
                        'pnl': pnl,
                        'pnl_pct': (pnl / entry_price * 100) if entry_price > 0 else 0,
                        'bars': bars_held,
                        'type': entry_signal['action']
                    }
                    
                    self.trades.append(trade)
                    self.total_pnl += pnl
                    
                    if pnl > 0:
                        self.winning_trades += 1
                        status = "✅ WIN"
                    else:
                        self.losing_trades += 1
                        status = "❌ LOSS"
                    
                    print(f"{i:<5} {self._format_date(candles[i]['timestamp']):<20} ${current_price:<11,.2f} {status:<15} PnL: {pnl:+.2f}")
            else:
                if i % 10 == 0:
                    print("")
        
        # RESUMEN
        print(f"\n{'='*80}")
        print(f"📊 RESULTADOS DEL BACKTEST")
        print(f"{'='*80}\n")
        
        if self.trades:
            print(f"Total trades: {len(self.trades)}")
            print(f"Winning trades: {self.winning_trades}")
            print(f"Losing trades: {self.losing_trades}")
            
            win_rate = (self.winning_trades / len(self.trades) * 100) if self.trades else 0
            print(f"Win rate: {win_rate:.1f}%")
            
            print(f"\nPnL total: ${self.total_pnl:+,.2f}")
            
            if self.trades:
                avg_win = sum(t['pnl'] for t in self.trades if t['pnl'] > 0) / max(self.winning_trades, 1)
                avg_loss = sum(t['pnl'] for t in self.trades if t['pnl'] < 0) / max(self.losing_trades, 1)
                
                print(f"Ganancia promedio: ${avg_win:+,.2f}")
                print(f"Pérdida promedio: ${avg_loss:+,.2f}")
                
                if avg_loss != 0:
                    rr_ratio = abs(avg_win / avg_loss)
                    print(f"Ratio R:R: 1:{rr_ratio:.2f}")
        else:
            print(f"❌ Sin trades generados (mercado sin señales claras)")
        
        print(f"\n{'='*80}\n")
    
    def _format_date(self, timestamp: int) -> str:
        """Formatear timestamp"""
        dt = datetime.fromtimestamp(timestamp / 1000)
        return dt.strftime('%Y-%m-%d %H:%M')


async def main():
    """Función principal"""
    
    # Símbolos a testear
    symbols = [
        'BTC-USDT',
        'ETH-USDT',
        'SOL-USDT',
    ]
    
    for symbol in symbols:
        backtest = BacktestEngine(symbol, timeframe='1h')
        await backtest.run(candles_count=500)
    
    print(f"\n✅ Backtesting completado\n")


if __name__ == "__main__":
    asyncio.run(main())
