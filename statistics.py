#!/usr/bin/env python3
"""
Estadísticas del Bot
Analiza los logs y genera reportes
"""

import os
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List


class TradeStatistics:
    """Análisis de estadísticas de trades"""
    
    def __init__(self, log_file: str = 'trading_bot.log'):
        """Inicializar"""
        self.log_file = log_file
        self.trades = []
        self.errors = []
    
    def parse_logs(self):
        """Parsear archivo de logs"""
        
        if not os.path.exists(self.log_file):
            print(f"❌ Archivo de logs no encontrado: {self.log_file}")
            return
        
        print(f"\n📖 Analizando logs desde: {self.log_file}\n")
        
        with open(self.log_file, 'r') as f:
            for line in f:
                # Detectar trades LONG
                if 'LONG ABIERTO' in line and '📈' in line:
                    match = re.search(r'\[(\w+-\w+)\].*LONG.*P:([\d.]+).*SL:([\d.]+).*TP:([\d.]+)', line)
                    if match:
                        symbol = match.group(1)
                        entry = float(match.group(2))
                        sl = float(match.group(3))
                        tp = float(match.group(4))
                        
                        self.trades.append({
                            'type': 'LONG',
                            'symbol': symbol,
                            'entry': entry,
                            'sl': sl,
                            'tp': tp,
                            'timestamp': self._extract_timestamp(line)
                        })
                
                # Detectar trades SHORT
                elif 'SHORT ABIERTO' in line and '📉' in line:
                    match = re.search(r'\[(\w+-\w+)\].*SHORT.*P:([\d.]+).*SL:([\d.]+).*TP:([\d.]+)', line)
                    if match:
                        symbol = match.group(1)
                        entry = float(match.group(2))
                        sl = float(match.group(3))
                        tp = float(match.group(4))
                        
                        self.trades.append({
                            'type': 'SHORT',
                            'symbol': symbol,
                            'entry': entry,
                            'sl': sl,
                            'tp': tp,
                            'timestamp': self._extract_timestamp(line)
                        })
                
                # Detectar cierres
                elif 'POSICIÓN CERRADA' in line and 'PnL' in line:
                    match = re.search(r'PnL:\s*([\d.+-]+)', line)
                    if match:
                        pnl = float(match.group(1))
                        # Buscar trade correspondiente (último sin cierre)
                        for trade in reversed(self.trades):
                            if 'exit' not in trade:
                                trade['exit'] = pnl
                                break
                
                # Detectar errores
                elif 'ERROR' in line or '❌' in line:
                    self.errors.append(line.strip())
    
    def _extract_timestamp(self, line: str) -> str:
        """Extraer timestamp de la línea"""
        match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        return match.group(1) if match else ""
    
    def print_statistics(self):
        """Imprimir estadísticas"""
        
        print("="*80)
        print("📊 ESTADÍSTICAS DEL BOT")
        print("="*80 + "\n")
        
        if not self.trades:
            print("❌ No hay trades registrados\n")
            return
        
        # Estadísticas básicas
        total_trades = len(self.trades)
        closed_trades = sum(1 for t in self.trades if 'exit' in t)
        open_trades = total_trades - closed_trades
        
        print(f"📈 TRADES")
        print(f"  Total: {total_trades}")
        print(f"  Cerrados: {closed_trades}")
        print(f"  Abiertos: {open_trades}\n")
        
        # Análisis de cerrados
        if closed_trades > 0:
            pnls = [t['exit'] for t in self.trades if 'exit' in t]
            winning = sum(1 for p in pnls if p > 0)
            losing = sum(1 for p in pnls if p < 0)
            breakeven = sum(1 for p in pnls if p == 0)
            
            win_rate = (winning / closed_trades * 100) if closed_trades > 0 else 0
            
            print(f"💰 RESULTADOS CERRADOS")
            print(f"  Ganancias: {winning} ({winning/closed_trades*100:.1f}%)")
            print(f"  Pérdidas: {losing} ({losing/closed_trades*100:.1f}%)")
            print(f"  Break-even: {breakeven}")
            print(f"  Win rate: {win_rate:.1f}%\n")
            
            total_pnl = sum(pnls)
            avg_pnl = total_pnl / closed_trades
            avg_win = sum(p for p in pnls if p > 0) / max(winning, 1) if winning > 0 else 0
            avg_loss = sum(p for p in pnls if p < 0) / max(losing, 1) if losing > 0 else 0
            
            print(f"💵 PnL")
            print(f"  Total: ${total_pnl:+,.2f}")
            print(f"  Promedio: ${avg_pnl:+,.2f}")
            print(f"  Ganancia promedio: ${avg_win:+,.2f}")
            print(f"  Pérdida promedio: ${avg_loss:+,.2f}")
            
            if avg_loss != 0:
                rr = abs(avg_win / avg_loss)
                print(f"  Ratio R:R: 1:{rr:.2f}\n")
        
        # Por símbolo
        symbols = set(t['symbol'] for t in self.trades)
        print(f"🎯 POR SÍMBOLO")
        for symbol in sorted(symbols):
            symbol_trades = [t for t in self.trades if t['symbol'] == symbol]
            symbol_closed = [t for t in symbol_trades if 'exit' in t]
            
            if symbol_closed:
                symbol_pnl = sum(t['exit'] for t in symbol_closed)
                symbol_wr = sum(1 for t in symbol_closed if t['exit'] > 0) / len(symbol_closed) * 100
                print(f"  {symbol}: {len(symbol_trades)} trades, "
                      f"${symbol_pnl:+,.2f}, WR {symbol_wr:.0f}%")
            else:
                print(f"  {symbol}: {len(symbol_trades)} trades (abiertos)")
        
        print()
        
        # Errores
        if self.errors:
            print(f"⚠️  ERRORES REGISTRADOS: {len(self.errors)}")
            print(f"  (Últimos 5)")
            for error in self.errors[-5:]:
                print(f"    • {error[:70]}...")
            print()
    
    def export_to_json(self, filename: str = 'bot_statistics.json'):
        """Exportar estadísticas a JSON"""
        
        stats = {
            'timestamp': datetime.now().isoformat(),
            'total_trades': len(self.trades),
            'trades': self.trades,
            'errors': self.errors
        }
        
        with open(filename, 'w') as f:
            json.dump(stats, f, indent=2)
        
        print(f"✅ Estadísticas exportadas a: {filename}\n")
    
    def export_to_csv(self, filename: str = 'bot_trades.csv'):
        """Exportar trades a CSV"""
        
        import csv
        
        with open(filename, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['type', 'symbol', 'entry', 'sl', 'tp', 'exit', 'timestamp'])
            writer.writeheader()
            writer.writerows(self.trades)
        
        print(f"✅ Trades exportados a: {filename}\n")


def main():
    """Función principal"""
    
    stats = TradeStatistics()
    stats.parse_logs()
    stats.print_statistics()
    
    # Exportar
    stats.export_to_json()
    stats.export_to_csv()


if __name__ == "__main__":
    main()
