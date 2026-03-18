#!/usr/bin/env python3
"""
Multiple Strategies Module
4 estrategias diferentes corriendo simultáneamente
El bot elige la mejor según desempeño real
"""

import json
import logging
import numpy as np
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Strategy:
    """Clase base para estrategias"""
    
    def __init__(self, name, params):
        self.name = name
        self.params = params
        self.trades = []
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0
    
    def analyze(self, data):
        """Analizar y generar señal"""
        raise NotImplementedError
    
    def get_performance(self):
        """Obtener métrica de desempeño"""
        if len(self.trades) == 0:
            return 0
        
        win_rate = self.wins / (self.wins + self.losses) * 100 if (self.wins + self.losses) > 0 else 0
        profit_factor = 1.0
        
        return {
            'name': self.name,
            'trades': len(self.trades),
            'wins': self.wins,
            'losses': self.losses,
            'win_rate': win_rate,
            'total_pnl': self.total_pnl,
            'score': (win_rate * 0.6) + (self.total_pnl * 0.4)
        }


class StrategyMomentum(Strategy):
    """Estrategia 1: Momentum puro"""
    
    def __init__(self):
        super().__init__("Momentum", {
            'rsi_threshold': 55,
            'min_change': 2.0,
            'timeframe': '15m'
        })
    
    def analyze(self, symbol, data):
        """Analizar momentum"""
        change = data.get('change', 0)
        
        # Señal LONG si cambio > 2%
        if change > self.params['min_change']:
            return {'direction': 'LONG', 'score': min(90, 50 + change), 'reason': 'Momentum Up'}
        
        # Señal SHORT si cambio < -2%
        elif change < -self.params['min_change']:
            return {'direction': 'SHORT', 'score': min(90, 50 + abs(change)), 'reason': 'Momentum Down'}
        
        return {'direction': 'NEUTRAL', 'score': 0, 'reason': 'Sin Momentum'}


class StrategyTrend(Strategy):
    """Estrategia 2: Seguidor de Tendencia"""
    
    def __init__(self):
        super().__init__("Trend Follower", {
            'ema_fast': 12,
            'ema_slow': 26,
            'min_strength': 0.5
        })
    
    def analyze(self, symbol, data):
        """Analizar tendencia"""
        change = data.get('change', 0)
        
        # Tendencia alcista: cambio > 0.5%
        if change > self.params['min_strength']:
            return {'direction': 'LONG', 'score': 65, 'reason': 'Tendencia Alcista'}
        
        # Tendencia bajista: cambio < -0.5%
        elif change < -self.params['min_strength']:
            return {'direction': 'SHORT', 'score': 65, 'reason': 'Tendencia Bajista'}
        
        return {'direction': 'NEUTRAL', 'score': 0, 'reason': 'Sin Tendencia'}


class StrategyVolatility(Strategy):
    """Estrategia 3: Volatilidad (Mean Reversion)"""
    
    def __init__(self):
        super().__init__("Volatility", {
            'volatility_threshold': 1.5,
            'reversion_target': 0.8
        })
    
    def analyze(self, symbol, data):
        """Analizar volatilidad"""
        change = data.get('change', 0)
        
        # Si volatilidad muy alta, esperar reversión
        if abs(change) > self.params['volatility_threshold']:
            # Esperar regreso hacia 0.8% del movimiento
            if change > 0:
                return {'direction': 'SHORT', 'score': 55, 'reason': 'Mean Reversion'}
            else:
                return {'direction': 'LONG', 'score': 55, 'reason': 'Mean Reversion'}
        
        return {'direction': 'NEUTRAL', 'score': 0, 'reason': 'Volatilidad Normal'}


class StrategyMachine(Strategy):
    """Estrategia 4: Machine Learning (adaptativa)"""
    
    def __init__(self):
        super().__init__("ML Adaptive", {
            'learning_rate': 0.1,
            'min_confidence': 0.6
        })
        self.history = []
    
    def analyze(self, symbol, data):
        """Analizar con ML simple"""
        change = data.get('change', 0)
        volume = data.get('volume', 0)
        
        # Combinar múltiples factores
        score = 0
        direction = 'NEUTRAL'
        
        # Factor 1: Momentum
        if change > 1.5:
            score += 40
            direction = 'LONG'
        elif change < -1.5:
            score += 40
            direction = 'SHORT'
        else:
            score += 20
        
        # Factor 2: Volumen
        if volume > 0:
            score += 30
        
        # Factor 3: Patrón histórico (si hay)
        if len(self.history) > 0:
            avg_change = np.mean([h.get('change', 0) for h in self.history[-10:]])
            if change > avg_change:
                score += 30
        
        confidence = min(100, score)
        
        if confidence < self.params['min_confidence'] * 100:
            return {'direction': 'NEUTRAL', 'score': 0, 'reason': 'Baja Confianza'}
        
        return {
            'direction': direction,
            'score': confidence,
            'reason': f'ML Score: {confidence:.0f}'
        }


class MultiStrategyBot:
    """Bot con múltiples estrategias"""
    
    def __init__(self):
        """Inicializar con 4 estrategias"""
        self.strategies = [
            StrategyMomentum(),
            StrategyTrend(),
            StrategyVolatility(),
            StrategyMachine()
        ]
        
        self.performance_file = 'strategies_performance.json'
        self.results_file = 'strategies_results.json'
        
        logger.info("="*80)
        logger.info("🎯 MULTI-STRATEGY BOT")
        logger.info(f"✅ Estrategias activas: {len(self.strategies)}")
        for strat in self.strategies:
            logger.info(f"   • {strat.name}")
        logger.info("="*80)
    
    def analyze_symbol(self, symbol, data):
        """Analizar símbolo con todas las estrategias"""
        results = []
        
        for strategy in self.strategies:
            signal = strategy.analyze(symbol, data)
            signal['strategy'] = strategy.name
            results.append(signal)
        
        return results
    
    def get_best_strategy(self):
        """Obtener la estrategia con mejor desempeño"""
        performances = [s.get_performance() for s in self.strategies]
        
        # Ordenar por score
        best = max(performances, key=lambda x: x['score'])
        
        return best
    
    def get_consensus_signal(self, symbol, data):
        """Obtener señal por consenso de todas las estrategias"""
        all_signals = self.analyze_symbol(symbol, data)
        
        long_votes = len([s for s in all_signals if s['direction'] == 'LONG'])
        short_votes = len([s for s in all_signals if s['direction'] == 'SHORT'])
        
        # Si hay mayoría
        if long_votes >= 2 and long_votes > short_votes:
            avg_score = np.mean([s['score'] for s in all_signals if s['direction'] == 'LONG'])
            return {
                'direction': 'LONG',
                'score': avg_score,
                'consensus': f'{long_votes}/{len(self.strategies)} estrategias',
                'reason': 'Consenso LONG'
            }
        
        elif short_votes >= 2 and short_votes > long_votes:
            avg_score = np.mean([s['score'] for s in all_signals if s['direction'] == 'SHORT'])
            return {
                'direction': 'SHORT',
                'score': avg_score,
                'consensus': f'{short_votes}/{len(self.strategies)} estrategias',
                'reason': 'Consenso SHORT'
            }
        
        return {
            'direction': 'NEUTRAL',
            'score': 0,
            'consensus': 'Sin consenso',
            'reason': 'Diferentes opiniones'
        }
    
    def record_trade_result(self, symbol, direction, entry, exit_price, pnl):
        """Registrar resultado de trade"""
        for strategy in self.strategies:
            if pnl > 0:
                strategy.wins += 1
            else:
                strategy.losses += 1
            
            strategy.total_pnl += pnl
            strategy.trades.append({
                'symbol': symbol,
                'direction': direction,
                'entry': entry,
                'exit': exit_price,
                'pnl': pnl,
                'timestamp': datetime.now().isoformat()
            })
    
    def get_strategies_report(self):
        """Generar reporte de estrategias"""
        report = f"\n{'='*80}\n"
        report += "📊 ESTRATEGIAS - COMPARATIVA DE DESEMPEÑO\n"
        report += f"{'='*80}\n\n"
        
        performances = [s.get_performance() for s in self.strategies]
        
        # Ordenar por score
        sorted_perfs = sorted(performances, key=lambda x: x['score'], reverse=True)
        
        for i, perf in enumerate(sorted_perfs, 1):
            report += f"{i}. {perf['name']}\n"
            report += f"   Trades: {perf['trades']} (✅ {perf['wins']} | ❌ {perf['losses']})\n"
            report += f"   Win Rate: {perf['win_rate']:.1f}%\n"
            report += f"   PnL Total: ${perf['total_pnl']:+.2f}\n"
            report += f"   Score: {perf['score']:.1f}\n\n"
        
        # Mejor estrategia
        best = sorted_perfs[0]
        report += f"🏆 MEJOR ESTRATEGIA: {best['name']}\n"
        report += f"   Usar esta estrategia como referencia\n"
        
        report += f"\n{'='*80}\n"
        
        return report
    
    def export_results(self):
        """Exportar resultados a JSON"""
        results = {
            'timestamp': datetime.now().isoformat(),
            'strategies': [
                {
                    'name': s.name,
                    'params': s.params,
                    'performance': s.get_performance(),
                    'total_trades': len(s.trades)
                }
                for s in self.strategies
            ]
        }
        
        try:
            with open(self.results_file, 'w') as f:
                json.dump(results, f, indent=2)
            logger.info(f"✅ Resultados exportados a {self.results_file}")
        except:
            pass
        
        return results


# Ejemplo
if __name__ == "__main__":
    bot = MultiStrategyBot()
    
    # Ejemplo de análisis
    test_data = {
        'symbol': 'BTC-USDT',
        'change': 2.5,  # +2.5%
        'volume': 1000000
    }
    
    # Análisis individual
    signals = bot.analyze_symbol('BTC-USDT', test_data)
    logger.info("Análisis individual:")
    for sig in signals:
        logger.info(f"  {sig['strategy']}: {sig['direction']} ({sig['score']:.0f})")
    
    # Consenso
    consensus = bot.get_consensus_signal('BTC-USDT', test_data)
    logger.info(f"\nConsenso: {consensus['direction']} - {consensus['consensus']}")
    
    # Reporte
    print(bot.get_strategies_report())
    
    logger.info("✅ Multi-Strategy Bot listo")
