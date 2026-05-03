"""
BACKTESTING ENGINE — Análisis de Rentabilidad
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Simula el bot en datos históricos para determinar:
  ✓ Win rate real
  ✓ Profit factor
  ✓ Max drawdown
  ✓ Sharpe ratio
  ✓ ROI mensual esperado
  ✓ Mejor configuración de parámetros

OBLIGATORIO ejecutar ANTES de operar con dinero real.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import json
from loguru import logger

from bingx_client import BingXClient
from conflux4 import Conflux4Engine
from config_production import ProductionConfig, config_to_engine


@dataclass
class Trade:
    """Registro de un trade simulado."""
    symbol: str
    direction: str  # BULL | BEAR
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    stop_loss: float = 0
    take_profit: float = 0
    size_usdt: float = 0
    pnl_usdt: float = 0
    pnl_pct: float = 0
    exit_reason: str = ""  # TP1, TP2, TP3, TP4, SL, Trend_Flip
    quality: int = 0
    
    @property
    def is_winner(self) -> bool:
        return self.pnl_usdt > 0
    
    @property
    def duration_hours(self) -> float:
        if self.exit_time:
            return (self.exit_time - self.entry_time).total_seconds() / 3600
        return 0


@dataclass
class BacktestResults:
    """Resultados completos del backtest."""
    # Equity
    starting_balance: float = 1000.0
    ending_balance: float = 1000.0
    peak_balance: float = 1000.0
    
    # Trades
    trades: List[Trade] = field(default_factory=list)
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    
    # Performance
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_rr: float = 0.0
    
    # Returns
    total_return_pct: float = 0.0
    monthly_return_pct: float = 0.0
    
    # Time
    backtest_days: int = 0
    avg_trade_duration_hours: float = 0
    
    def calculate_metrics(self):
        """Calcula todas las métricas del backtest."""
        if not self.trades:
            return
        
        self.total_trades = len(self.trades)
        self.winning_trades = sum(1 for t in self.trades if t.is_winner)
        self.losing_trades = self.total_trades - self.winning_trades
        
        # Win rate
        self.win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
        
        # Profit factor
        total_wins = sum(t.pnl_usdt for t in self.trades if t.is_winner)
        total_losses = abs(sum(t.pnl_usdt for t in self.trades if not t.is_winner))
        self.profit_factor = (total_wins / total_losses) if total_losses > 0 else 0
        
        # Returns
        self.total_return_pct = ((self.ending_balance - self.starting_balance) / 
                                 self.starting_balance * 100)
        
        if self.backtest_days > 0:
            self.monthly_return_pct = (self.total_return_pct / self.backtest_days) * 30
        
        # Average win/loss
        wins = [t.pnl_pct for t in self.trades if t.is_winner]
        losses = [t.pnl_pct for t in self.trades if not t.is_winner]
        self.avg_win_pct = np.mean(wins) if wins else 0
        self.avg_loss_pct = np.mean(losses) if losses else 0
        
        # Average R:R
        if self.avg_loss_pct != 0:
            self.avg_rr = abs(self.avg_win_pct / self.avg_loss_pct)
        
        # Max drawdown
        equity_curve = []
        balance = self.starting_balance
        for t in self.trades:
            balance += t.pnl_usdt
            equity_curve.append(balance)
        
        if equity_curve:
            peak = self.starting_balance
            max_dd = 0
            for equity in equity_curve:
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak * 100
                if dd > max_dd:
                    max_dd = dd
            self.max_drawdown_pct = max_dd
            self.peak_balance = peak
        
        # Sharpe ratio (simplified)
        if self.trades:
            returns = [t.pnl_pct for t in self.trades]
            mean_return = np.mean(returns)
            std_return = np.std(returns)
            self.sharpe_ratio = (mean_return / std_return * np.sqrt(252)) if std_return > 0 else 0
        
        # Avg duration
        durations = [t.duration_hours for t in self.trades if t.duration_hours > 0]
        self.avg_trade_duration_hours = np.mean(durations) if durations else 0
    
    def print_summary(self):
        """Imprime resumen del backtest."""
        print("\n" + "="*70)
        print("📊 RESULTADOS DEL BACKTEST")
        print("="*70)
        
        print(f"\n💰 EQUITY:")
        print(f"   Balance inicial:  ${self.starting_balance:,.2f}")
        print(f"   Balance final:    ${self.ending_balance:,.2f}")
        print(f"   Peak balance:     ${self.peak_balance:,.2f}")
        print(f"   Return total:     {self.total_return_pct:+.2f}%")
        print(f"   Return mensual:   {self.monthly_return_pct:+.2f}%")
        
        print(f"\n📈 TRADES:")
        print(f"   Total:            {self.total_trades}")
        print(f"   Winners:          {self.winning_trades}")
        print(f"   Losers:           {self.losing_trades}")
        print(f"   Win rate:         {self.win_rate:.1f}%")
        
        print(f"\n📊 PERFORMANCE:")
        print(f"   Profit factor:    {self.profit_factor:.2f}")
        print(f"   Sharpe ratio:     {self.sharpe_ratio:.2f}")
        print(f"   Max drawdown:     {self.max_drawdown_pct:.2f}%")
        print(f"   Avg win:          {self.avg_win_pct:+.2f}%")
        print(f"   Avg loss:         {self.avg_loss_pct:+.2f}%")
        print(f"   Avg R:R:          {self.avg_rr:.2f}")
        print(f"   Avg duration:     {self.avg_trade_duration_hours:.1f}h")
        
        print(f"\n⏱️  PERIODO:")
        print(f"   Días simulados:   {self.backtest_days}")
        
        # Veredicto
        print(f"\n{'='*70}")
        print("🎯 VEREDICTO:")
        
        if self.win_rate >= 55 and self.profit_factor >= 1.5 and self.max_drawdown_pct <= 15:
            print("   ✅ BOT RENTABLE - Apto para dinero real")
        elif self.win_rate >= 50 and self.profit_factor >= 1.3:
            print("   ⚠️  BOT MARGINAL - Optimizar antes de usar dinero real")
        else:
            print("   ❌ BOT NO RENTABLE - NO usar con dinero real")
        
        print("="*70 + "\n")


class BacktestEngine:
    """Motor de backtesting."""
    
    def __init__(self, config: ProductionConfig, bingx: BingXClient):
        self.config = config
        self.bingx = bingx
        self.engine = Conflux4Engine(config_to_engine(config))
        
    def run(self, symbols: List[str], days: int = 30, 
            starting_balance: float = 1000.0) -> BacktestResults:
        """
        Ejecuta backtest sobre datos históricos.
        
        Args:
            symbols: Lista de símbolos a testear
            days: Días históricos a simular
            starting_balance: Capital inicial
        
        Returns:
            BacktestResults con todos los metrics
        """
        logger.info(f"Iniciando backtest: {len(symbols)} símbolos, {days} días")
        
        results = BacktestResults(
            starting_balance=starting_balance,
            ending_balance=starting_balance,
            backtest_days=days
        )
        
        balance = starting_balance
        peak_balance = starting_balance
        
        # Obtener datos históricos
        for symbol in symbols:
            try:
                logger.info(f"Backtesting {symbol}...")
                
                # Obtener klines (1 día = ~96 velas de 15m)
                limit = min(days * 100, 1000)
                df = self.bingx.get_klines(symbol, self.config.interval, limit)
                
                if len(df) < 100:
                    logger.warning(f"Datos insuficientes para {symbol}")
                    continue
                
                # Simular trading en cada vela
                for i in range(100, len(df)):
                    # Datos hasta esta vela
                    df_subset = df.iloc[:i+1]
                    
                    # Calcular señal
                    try:
                        result = self.engine.compute(df_subset, None, None, funding_rate=0.0)
                    except Exception as e:
                        continue
                    
                    # Si hay señal y pasa filtros
                    if result.signal and result.quality >= self.config.min_signal_quality:
                        # Crear trade
                        trade = Trade(
                            symbol=symbol,
                            direction=result.signal,
                            entry_time=df_subset.index[-1],
                            entry_price=result.entry,
                            stop_loss=result.stop,
                            take_profit=result.tp2,
                            quality=result.quality,
                        )
                        
                        # Calcular tamaño de posición
                        risk_pct = self.config.max_risk_per_trade_pct / 100
                        sl_dist = abs(result.entry - result.stop) / result.entry
                        if sl_dist > 0:
                            position_size = (balance * risk_pct) / sl_dist
                            position_size = min(position_size, 
                                              balance * self.config.leverage / result.entry)
                            trade.size_usdt = position_size
                        else:
                            continue
                        
                        # Simular salida del trade
                        exit_found = False
                        for j in range(i+1, min(i+100, len(df))):  # Máximo 100 velas futuras
                            candle = df.iloc[j]
                            price_high = candle['high']
                            price_low = candle['low']
                            
                            # Check stop loss
                            if result.signal == "BULL":
                                if price_low <= trade.stop_loss:
                                    trade.exit_price = trade.stop_loss
                                    trade.exit_time = candle.name
                                    trade.exit_reason = "SL"
                                    exit_found = True
                                # Check TPs
                                elif price_high >= result.tp4:
                                    trade.exit_price = result.tp4
                                    trade.exit_time = candle.name
                                    trade.exit_reason = "TP4"
                                    exit_found = True
                                elif price_high >= result.tp3:
                                    trade.exit_price = result.tp3
                                    trade.exit_time = candle.name
                                    trade.exit_reason = "TP3"
                                    exit_found = True
                                elif price_high >= result.tp2:
                                    trade.exit_price = result.tp2
                                    trade.exit_time = candle.name
                                    trade.exit_reason = "TP2"
                                    exit_found = True
                                elif price_high >= result.tp1:
                                    trade.exit_price = result.tp1
                                    trade.exit_time = candle.name
                                    trade.exit_reason = "TP1"
                                    exit_found = True
                            else:  # BEAR
                                if price_high >= trade.stop_loss:
                                    trade.exit_price = trade.stop_loss
                                    trade.exit_time = candle.name
                                    trade.exit_reason = "SL"
                                    exit_found = True
                                elif price_low <= result.tp4:
                                    trade.exit_price = result.tp4
                                    trade.exit_time = candle.name
                                    trade.exit_reason = "TP4"
                                    exit_found = True
                                elif price_low <= result.tp3:
                                    trade.exit_price = result.tp3
                                    trade.exit_time = candle.name
                                    trade.exit_reason = "TP3"
                                    exit_found = True
                                elif price_low <= result.tp2:
                                    trade.exit_price = result.tp2
                                    trade.exit_time = candle.name
                                    trade.exit_reason = "TP2"
                                    exit_found = True
                                elif price_low <= result.tp1:
                                    trade.exit_price = result.tp1
                                    trade.exit_time = candle.name
                                    trade.exit_reason = "TP1"
                                    exit_found = True
                            
                            if exit_found:
                                break
                        
                        # Si se cerró el trade, calcular P&L
                        if exit_found and trade.exit_price:
                            if result.signal == "BULL":
                                trade.pnl_pct = ((trade.exit_price - trade.entry_price) / 
                                                trade.entry_price * 100)
                            else:
                                trade.pnl_pct = ((trade.entry_price - trade.exit_price) / 
                                                trade.entry_price * 100)
                            
                            trade.pnl_usdt = (trade.size_usdt * trade.pnl_pct / 100)
                            balance += trade.pnl_usdt
                            
                            if balance > peak_balance:
                                peak_balance = balance
                            
                            results.trades.append(trade)
                            
                            # Cooldown
                            i = j + self.config.cooldown
            
            except Exception as e:
                logger.error(f"Error en backtest {symbol}: {e}")
                continue
        
        # Calcular métricas finales
        results.ending_balance = balance
        results.peak_balance = peak_balance
        results.calculate_metrics()
        
        return results


def quick_backtest(symbols: List[str] = None, days: int = 30):
    """Ejecuta backtest rápido con configuración por defecto."""
    from config_production import load_production_config
    
    cfg = load_production_config()
    bingx = BingXClient(cfg.bingx_api_key, cfg.bingx_secret, testnet=True)
    
    if symbols is None:
        symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT"]
    
    engine = BacktestEngine(cfg, bingx)
    results = engine.run(symbols, days=days, starting_balance=cfg.starting_balance)
    
    results.print_summary()
    
    # Guardar resultados
    with open("data/backtest_results.json", "w") as f:
        json.dump({
            "config": cfg.preset,
            "symbols": symbols,
            "days": days,
            "win_rate": results.win_rate,
            "profit_factor": results.profit_factor,
            "total_return_pct": results.total_return_pct,
            "monthly_return_pct": results.monthly_return_pct,
            "max_drawdown_pct": results.max_drawdown_pct,
            "total_trades": results.total_trades,
            "sharpe_ratio": results.sharpe_ratio,
        }, f, indent=2)
    
    return results


if __name__ == "__main__":
    # Ejecutar backtest
    results = quick_backtest(days=30)
