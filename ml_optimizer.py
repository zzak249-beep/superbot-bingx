#!/usr/bin/env python3
"""
Machine Learning Module - Optimización Automática
El bot aprende de resultados pasados y optimiza parámetros
"""

import json
import numpy as np
import logging
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MLOptimizer:
    """Machine Learning para optimizar estrategia automáticamente"""
    
    def __init__(self):
        """Inicializar ML optimizer"""
        self.history_file = 'ml_history.json'
        self.optimization_file = 'ml_optimization.json'
        
        self.load_history()
        self.load_optimization()
        
        logger.info("="*80)
        logger.info("🤖 Machine Learning Optimizer")
        logger.info("✅ Aprende de resultados pasados")
        logger.info("✅ Optimiza parámetros automáticamente")
        logger.info("✅ Genera recomendaciones")
        logger.info("="*80)
    
    def load_history(self):
        """Cargar historial de trades"""
        if Path(self.history_file).exists():
            try:
                with open(self.history_file, 'r') as f:
                    self.history = json.load(f)
            except:
                self.history = []
        else:
            self.history = []
    
    def load_optimization(self):
        """Cargar parámetros optimizados"""
        if Path(self.optimization_file).exists():
            try:
                with open(self.optimization_file, 'r') as f:
                    self.optimization = json.load(f)
            except:
                self.optimization = self._create_default_optimization()
        else:
            self.optimization = self._create_default_optimization()
    
    def _create_default_optimization(self):
        """Crear parámetros por defecto"""
        return {
            'buy_threshold': 1.5,
            'sell_threshold': 1.0,
            'min_momentum': 0.5,
            'check_interval': 180,
            'max_position_size': 100,
            'leverage': 2,
            'stop_loss_pct': 1.0,
            'take_profit_pct': 4.0,
            'iterations': 0,
            'last_update': datetime.now().isoformat()
        }
    
    def save_optimization(self):
        """Guardar parámetros optimizados"""
        try:
            with open(self.optimization_file, 'w') as f:
                json.dump(self.optimization, f, indent=2)
        except Exception as e:
            logger.error(f"❌ Error guardando: {e}")
    
    def add_trade_result(self, symbol, direction, entry, exit_price, pnl, pnl_pct, duration):
        """Registrar resultado de trade"""
        trade = {
            'symbol': symbol,
            'direction': direction,
            'entry': entry,
            'exit': exit_price,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'duration_seconds': duration,
            'timestamp': datetime.now().isoformat(),
            'profitable': pnl > 0
        }
        
        self.history.append(trade)
        
        try:
            with open(self.history_file, 'w') as f:
                json.dump(self.history, f, indent=2)
        except:
            pass
    
    def analyze_performance(self):
        """Analizar desempeño histórico"""
        if len(self.history) < 10:
            logger.warning("⚠️ Necesita 10+ trades para análisis")
            return None
        
        profitable_trades = [t for t in self.history if t['profitable']]
        win_rate = len(profitable_trades) / len(self.history) * 100
        
        avg_profit = np.mean([t['pnl'] for t in self.history])
        avg_loss = np.mean([abs(t['pnl']) for t in self.history if t['pnl'] < 0])
        
        profit_factor = sum([t['pnl'] for t in profitable_trades]) / sum([abs(t['pnl']) for t in self.history if t['pnl'] < 0])
        
        long_trades = [t for t in self.history if t['direction'] == 'LONG']
        short_trades = [t for t in self.history if t['direction'] == 'SHORT']
        
        long_wr = len([t for t in long_trades if t['profitable']]) / len(long_trades) * 100 if long_trades else 0
        short_wr = len([t for t in short_trades if t['profitable']]) / len(short_trades) * 100 if short_trades else 0
        
        analysis = {
            'total_trades': len(self.history),
            'profitable_trades': len(profitable_trades),
            'win_rate': win_rate,
            'avg_profit': avg_profit,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'long_win_rate': long_wr,
            'short_win_rate': short_wr,
            'timestamp': datetime.now().isoformat()
        }
        
        return analysis
    
    def get_recommendations(self):
        """Obtener recomendaciones de optimización"""
        analysis = self.analyze_performance()
        
        if not analysis:
            return []
        
        recommendations = []
        
        # Recomendación 1: Ajustar buy_threshold
        if analysis['win_rate'] < 50:
            recommendations.append({
                'parameter': 'buy_threshold',
                'action': 'INCREASE',
                'reason': f'Win rate bajo ({analysis["win_rate"]:.1f}%)',
                'suggestion': 'Aumentar a 2.0 para más selectividad'
            })
        elif analysis['win_rate'] > 65:
            recommendations.append({
                'parameter': 'buy_threshold',
                'action': 'DECREASE',
                'reason': f'Win rate excelente ({analysis["win_rate"]:.1f}%)',
                'suggestion': 'Disminuir a 1.2 para más oportunidades'
            })
        
        # Recomendación 2: Diferenciar LONG vs SHORT
        if analysis['long_win_rate'] > analysis['short_win_rate'] + 10:
            recommendations.append({
                'parameter': 'strategy',
                'action': 'FAVOR_LONG',
                'reason': f'LONG gana más ({analysis["long_win_rate"]:.1f}% vs {analysis["short_win_rate"]:.1f}%)',
                'suggestion': 'Aumentar señales LONG, reducir SHORT'
            })
        
        elif analysis['short_win_rate'] > analysis['long_win_rate'] + 10:
            recommendations.append({
                'parameter': 'strategy',
                'action': 'FAVOR_SHORT',
                'reason': f'SHORT gana más ({analysis["short_win_rate"]:.1f}% vs {analysis["long_win_rate"]:.1f}%)',
                'suggestion': 'Aumentar señales SHORT, reducir LONG'
            })
        
        # Recomendación 3: Ajustar take_profit
        avg_trade_duration = np.mean([t['duration_seconds'] for t in self.history])
        
        if avg_trade_duration < 300:  # Menos de 5 minutos
            recommendations.append({
                'parameter': 'take_profit_pct',
                'action': 'DECREASE',
                'reason': f'Trades muy rápidos ({avg_trade_duration:.0f}s)',
                'suggestion': 'Reducir TP a 2.5% para capturar ganancias antes'
            })
        
        elif avg_trade_duration > 3600:  # Más de 1 hora
            recommendations.append({
                'parameter': 'take_profit_pct',
                'action': 'INCREASE',
                'reason': f'Trades muy lentos ({avg_trade_duration:.0f}s)',
                'suggestion': 'Aumentar TP a 5.0% para capturar movimientos mayores'
            })
        
        # Recomendación 4: Ajustar stop_loss
        if analysis['profit_factor'] < 1.2:
            recommendations.append({
                'parameter': 'stop_loss_pct',
                'action': 'DECREASE',
                'reason': f'Profit factor bajo ({analysis["profit_factor"]:.2f})',
                'suggestion': 'Reducir SL a 0.7% para proteger capital antes'
            })
        
        return recommendations
    
    def apply_optimizations(self):
        """Aplicar optimizaciones automáticamente"""
        recommendations = self.get_recommendations()
        
        if not recommendations:
            logger.info("✅ Parámetros ya optimizados")
            return False
        
        logger.info(f"\n{'='*80}")
        logger.info("🧠 RECOMENDACIONES DE MACHINE LEARNING")
        logger.info(f"{'='*80}\n")
        
        changed = False
        
        for rec in recommendations:
            logger.info(f"📊 Parámetro: {rec['parameter']}")
            logger.info(f"   Acción: {rec['action']}")
            logger.info(f"   Razón: {rec['reason']}")
            logger.info(f"   Sugerencia: {rec['suggestion']}\n")
            
            # Aplicar cambios
            if rec['parameter'] == 'buy_threshold':
                if rec['action'] == 'INCREASE':
                    self.optimization['buy_threshold'] = min(2.5, self.optimization['buy_threshold'] + 0.3)
                else:
                    self.optimization['buy_threshold'] = max(0.8, self.optimization['buy_threshold'] - 0.2)
                changed = True
            
            elif rec['parameter'] == 'take_profit_pct':
                if rec['action'] == 'DECREASE':
                    self.optimization['take_profit_pct'] = max(2.0, self.optimization['take_profit_pct'] - 0.5)
                else:
                    self.optimization['take_profit_pct'] = min(6.0, self.optimization['take_profit_pct'] + 0.5)
                changed = True
            
            elif rec['parameter'] == 'stop_loss_pct':
                if rec['action'] == 'DECREASE':
                    self.optimization['stop_loss_pct'] = max(0.5, self.optimization['stop_loss_pct'] - 0.2)
                changed = True
        
        if changed:
            self.optimization['iterations'] += 1
            self.optimization['last_update'] = datetime.now().isoformat()
            self.save_optimization()
            
            logger.info(f"✅ Parámetros actualizados - Iteración #{self.optimization['iterations']}")
            logger.info(f"   buy_threshold: {self.optimization['buy_threshold']}")
            logger.info(f"   take_profit_pct: {self.optimization['take_profit_pct']}")
            logger.info(f"   stop_loss_pct: {self.optimization['stop_loss_pct']}\n")
        
        return changed
    
    def get_optimized_parameters(self):
        """Obtener parámetros actuales optimizados"""
        return self.optimization
    
    def generate_report(self):
        """Generar reporte de optimización"""
        analysis = self.analyze_performance()
        
        if not analysis:
            return "Necesita 10+ trades para generar reporte"
        
        report = f"""
{'='*80}
📊 MACHINE LEARNING OPTIMIZATION REPORT
{'='*80}

📈 DESEMPEÑO:
   Total Trades: {analysis['total_trades']}
   Profitable: {analysis['profitable_trades']}
   Win Rate: {analysis['win_rate']:.1f}%
   Profit Factor: {analysis['profit_factor']:.2f}

📊 ANALISIS:
   Promedio Ganancia: ${analysis['avg_profit']:.2f}
   Promedio Pérdida: ${analysis['avg_loss']:.2f}
   LONG Win Rate: {analysis['long_win_rate']:.1f}%
   SHORT Win Rate: {analysis['short_win_rate']:.1f}%

🧠 PARAMETROS ACTUALES:
   buy_threshold: {self.optimization['buy_threshold']}
   sell_threshold: {self.optimization['sell_threshold']}
   take_profit_pct: {self.optimization['take_profit_pct']}
   stop_loss_pct: {self.optimization['stop_loss_pct']}
   Iteraciones: #{self.optimization['iterations']}

📌 RECOMENDACIONES:
"""
        
        recommendations = self.get_recommendations()
        if recommendations:
            for rec in recommendations:
                report += f"\n   • {rec['parameter']}: {rec['action']}"
                report += f"\n     Razón: {rec['reason']}"
        else:
            report += "\n   ✅ Todos los parámetros están optimizados"
        
        report += f"\n\n{'='*80}\n"
        
        return report


# Ejemplo
if __name__ == "__main__":
    ml = MLOptimizer()
    
    # Generar reporte
    print(ml.generate_report())
    
    # Aplicar optimizaciones
    ml.apply_optimizations()
    
    logger.info("✅ ML Optimizer listo")
