#!/usr/bin/env python3
"""
🚀 BOT DE TRADING PROFESIONAL CON ML/IA
==================================================
✅ Análisis de TODAS las monedas disponibles
✅ Machine Learning para predicción de señales
✅ Análisis técnico avanzado (RSI, MACD, BB)
✅ Gestión de riesgo robusta
✅ Estadísticas y rentabilidad completa
✅ Trailing stop loss
✅ Base de datos para histórico
==================================================
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List
import sys

from config import Config
from bingx_client import BingXClient
from technical_analysis import TechnicalAnalysis
from ml_predictor import MLPredictor
from risk_manager import RiskManager
from statistics import StatisticsTracker
import requests

# Configurar logging
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(Config.LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class TradingBotPro:
    """Bot de Trading Profesional con ML/IA"""
    
    def __init__(self):
        """Inicializar bot"""
        logger.info("\n" + "="*80)
        logger.info("🚀 INICIANDO BOT DE TRADING PROFESIONAL")
        logger.info("="*80)
        
        # Validar configuración
        errors = Config.validate()
        if errors:
            logger.error("❌ Errores de configuración:")
            for error in errors:
                logger.error(f"   - {error}")
            if Config.AUTO_TRADING_ENABLED:
                logger.error("⚠️ AUTO-TRADING DESACTIVADO por errores")
                Config.AUTO_TRADING_ENABLED = False
        
        # Imprimir configuración
        print(Config.get_summary())
        
        # Inicializar componentes
        self.client = BingXClient()
        self.risk_manager = RiskManager()
        self.ml_predictor = MLPredictor()
        self.stats_tracker = StatisticsTracker()
        
        # Trading symbols
        self.active_symbols = []
        self.open_trades = {}
        
        # Trade tracking
        self.trade_db_ids = {}  # symbol -> db_id mapping
        
        # Inicializar ML con datos sintéticos si es necesario
        if Config.ML_ENABLED:
            self.ml_predictor.simulate_initial_training()
        
        # Notificar inicio
        self._send_telegram(
            "🤖 <b>BOT PROFESIONAL INICIADO</b>\n\n"
            f"{'✅ AUTO-TRADING: ON' if Config.AUTO_TRADING_ENABLED else '⏹️ AUTO-TRADING: OFF'}\n"
            f"{'🤖 ML/IA: ACTIVADO' if Config.ML_ENABLED else '⏹️ ML: OFF'}\n"
            f"💰 Position Size: ${Config.MAX_POSITION_SIZE}\n"
            f"⚡ Leverage: {Config.LEVERAGE}x\n"
            f"🎯 TP: {Config.TAKE_PROFIT_PCT}% | SL: {Config.STOP_LOSS_PCT}%"
        )
        
        logger.info("✅ Bot inicializado correctamente\n")
    
    async def update_symbol_list(self):
        """Actualizar lista de símbolos activos"""
        try:
            logger.info("📊 Actualizando lista de símbolos...")
            
            # Obtener top símbolos por volumen
            top_symbols = self.client.get_top_symbols_by_volume(
                limit=Config.MAX_SYMBOLS_TO_TRADE
            )
            
            if top_symbols:
                self.active_symbols = top_symbols
                logger.info(f"✅ {len(self.active_symbols)} símbolos activos")
            else:
                # Fallback a lista estática
                self.active_symbols = [
                    'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT', 'XRP-USDT',
                    'DOGE-USDT', 'ADA-USDT', 'AVAX-USDT', 'DOT-USDT', 'MATIC-USDT',
                    'LINK-USDT', 'UNI-USDT', 'ATOM-USDT', 'LTC-USDT', 'BCH-USDT'
                ]
                logger.warning("⚠️ Usando lista estática de símbolos")
        
        except Exception as e:
            logger.error(f"❌ Error actualizando símbolos: {e}")
    
    async def analyze_symbol(self, symbol: str) -> Dict:
        """
        Análisis completo de un símbolo
        
        Returns:
            {'signal': 'LONG'|'SHORT'|'NEUTRAL', 'confidence': float, ...}
        """
        try:
            # Obtener datos históricos
            klines = self.client.get_klines(symbol, Config.PRIMARY_TIMEFRAME, 100)
            
            if len(klines) < 50:
                return {'signal': 'NEUTRAL', 'confidence': 0}
            
            # Generar features técnicas
            features = TechnicalAnalysis.generate_features(klines)
            
            if not features:
                return {'signal': 'NEUTRAL', 'confidence': 0}
            
            # Análisis técnico tradicional
            tech_signal = TechnicalAnalysis.get_signal_strength(features)
            
            # Predicción ML
            ml_prediction = None
            if Config.ML_ENABLED:
                ml_prediction = self.ml_predictor.predict(features)
            
            # Combinar señales
            final_signal = self._combine_signals(tech_signal, ml_prediction)
            
            return {
                'symbol': symbol,
                'signal': final_signal['direction'],
                'confidence': final_signal['confidence'],
                'technical_score': tech_signal['strength'],
                'ml_confidence': ml_prediction['confidence'] if ml_prediction else None,
                'price': features['current_price'],
                'features': features
            }
        
        except Exception as e:
            logger.debug(f"Error analizando {symbol}: {e}")
            return {'signal': 'NEUTRAL', 'confidence': 0}
    
    def _combine_signals(self, tech_signal: Dict, ml_prediction: Dict = None) -> Dict:
        """Combinar señal técnica y ML"""
        
        # Solo técnico
        if not ml_prediction or not Config.ML_ENABLED:
            return {
                'direction': tech_signal['direction'],
                'confidence': tech_signal['strength'] / 100
            }
        
        # Ambas señales deben estar de acuerdo
        if tech_signal['direction'] == ml_prediction['direction']:
            # Promedio ponderado (60% ML, 40% técnico)
            combined_confidence = (
                ml_prediction['confidence'] * 0.6 + 
                (tech_signal['strength'] / 100) * 0.4
            )
            
            return {
                'direction': tech_signal['direction'],
                'confidence': combined_confidence
            }
        
        # Señales en conflicto - NEUTRAL
        return {
            'direction': 'NEUTRAL',
            'confidence': 0
        }
    
    async def execute_trade(self, analysis: Dict):
        """Ejecutar trade basado en análisis"""
        symbol = analysis['symbol']
        signal = analysis['signal']
        confidence = analysis['confidence']
        price = analysis['price']
        
        # Verificaciones
        if signal == 'NEUTRAL':
            return
        
        if confidence < Config.ML_CONFIDENCE_THRESHOLD:
            logger.debug(f"   {symbol}: Confianza baja ({confidence:.2f})")
            return
        
        if symbol in self.open_trades:
            return
        
        if len(self.open_trades) >= Config.MAX_OPEN_TRADES:
            logger.debug(f"   Max trades alcanzado ({Config.MAX_OPEN_TRADES})")
            return
        
        # Verificar riesgo
        balance = self.client.get_balance()
        can_trade, reason = self.risk_manager.can_open_trade(balance['total'], reason=symbol)
        
        if not can_trade:
            logger.warning(f"⚠️ Trade bloqueado: {reason}")
            return
        
        # Calcular tamaño de posición
        volatility = analysis['features'].get('bb_bandwidth', None)
        quantity = self.risk_manager.calculate_position_size(
            symbol, price, volatility, balance['available']
        )
        
        # Ejecutar
        if Config.AUTO_TRADING_ENABLED:
            result = self.client.open_position(
                symbol, signal, quantity, Config.LEVERAGE
            )
            
            if result:
                # Calcular TP/SL
                tp_price = self.risk_manager.calculate_take_profit(price, signal)
                sl_price = self.risk_manager.calculate_stop_loss(price, signal)
                
                # Registrar
                db_id = self.stats_tracker.record_trade_open(
                    symbol, signal, price, quantity, Config.LEVERAGE,
                    analysis.get('ml_confidence'), analysis.get('technical_score')
                )
                
                self.open_trades[symbol] = {
                    'direction': signal,
                    'entry_price': price,
                    'quantity': quantity,
                    'tp_price': tp_price,
                    'sl_price': sl_price,
                    'entry_time': datetime.now(),
                    'db_id': db_id
                }
                
                self.trade_db_ids[symbol] = db_id
                
                logger.info(f"✅ TRADE ABIERTO: {signal} {symbol} @ ${price:.4f}")
                logger.info(f"   Cantidad: {quantity:.6f}")
                logger.info(f"   🎯 TP: ${tp_price:.4f} | 🛑 SL: ${sl_price:.4f}")
                logger.info(f"   Confianza: {confidence:.2%}")
                
                self._send_telegram(
                    f"✅ <b>TRADE ABIERTO</b>\n\n"
                    f"{signal} {symbol}\n"
                    f"💰 Entry: ${price:.4f}\n"
                    f"🎯 TP: ${tp_price:.4f} (+{Config.TAKE_PROFIT_PCT}%)\n"
                    f"🛑 SL: ${sl_price:.4f} (-{Config.STOP_LOSS_PCT}%)\n"
                    f"📊 Cantidad: {quantity:.6f}\n"
                    f"🎲 Confianza: {confidence:.0%}"
                )
                
                # Añadir muestra de entrenamiento ML
                if Config.ML_ENABLED:
                    self.ml_predictor.add_training_sample(analysis['features'], signal)
        
        else:
            # Solo señal
            logger.info(f"📊 SEÑAL: {signal} {symbol} @ ${price:.4f} (Confianza: {confidence:.0%})")
            
            self.stats_tracker.record_signal(
                symbol, signal, price,
                analysis.get('technical_score'),
                analysis.get('ml_confidence')
            )
    
    async def monitor_open_trades(self):
        """Monitorear trades abiertos"""
        if not self.open_trades:
            return
        
        for symbol in list(self.open_trades.keys()):
            try:
                trade = self.open_trades[symbol]
                
                # Obtener precio actual
                current_price = self.client._get_current_price(symbol)
                if not current_price:
                    continue
                
                # Actualizar trailing stop
                if Config.TRAILING_STOP_ENABLED:
                    new_sl = self.risk_manager.update_trailing_stop(trade, current_price)
                    if new_sl:
                        trade['sl_price'] = new_sl
                
                # Verificar TP/SL
                should_close = False
                close_reason = ""
                
                if trade['direction'] == 'LONG':
                    if current_price >= trade['tp_price']:
                        should_close = True
                        close_reason = "TAKE PROFIT"
                    elif current_price <= trade['sl_price']:
                        should_close = True
                        close_reason = "STOP LOSS"
                
                else:  # SHORT
                    if current_price <= trade['tp_price']:
                        should_close = True
                        close_reason = "TAKE PROFIT"
                    elif current_price >= trade['sl_price']:
                        should_close = True
                        close_reason = "STOP LOSS"
                
                # Cerrar si es necesario
                if should_close:
                    await self.close_trade(symbol, current_price, close_reason)
            
            except Exception as e:
                logger.debug(f"Error monitoreando {symbol}: {e}")
    
    async def close_trade(self, symbol: str, exit_price: float, reason: str):
        """Cerrar un trade"""
        if symbol not in self.open_trades:
            return
        
        trade = self.open_trades[symbol]
        
        # Cerrar en exchange
        if Config.AUTO_TRADING_ENABLED:
            success = self.client.close_position(
                symbol, trade['direction'], trade['quantity']
            )
            
            if not success:
                logger.error(f"❌ Error cerrando {symbol}")
                return
        
        # Calcular PnL
        if trade['direction'] == 'LONG':
            pnl = (exit_price - trade['entry_price']) * trade['quantity']
        else:
            pnl = (trade['entry_price'] - exit_price) * trade['quantity']
        
        pnl_pct = (pnl / (trade['entry_price'] * trade['quantity'])) * 100
        
        # Registrar cierre
        db_id = trade.get('db_id')
        if db_id:
            self.stats_tracker.record_trade_close(db_id, exit_price, reason)
        
        # Actualizar risk manager
        self.risk_manager.record_trade({
            'symbol': symbol,
            'direction': trade['direction'],
            'entry_price': trade['entry_price'],
            'exit_price': exit_price,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'reason': reason
        })
        
        # Logging
        logger.info(f"✅ TRADE CERRADO - {reason}")
        logger.info(f"   {symbol}: ${trade['entry_price']:.4f} → ${exit_price:.4f}")
        logger.info(f"   PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
        
        self._send_telegram(
            f"✅ <b>TRADE CERRADO - {reason}</b>\n\n"
            f"{symbol}\n"
            f"💰 Entry: ${trade['entry_price']:.4f}\n"
            f"💰 Exit: ${exit_price:.4f}\n"
            f"📊 PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%)"
        )
        
        # Remover de trades abiertos
        del self.open_trades[symbol]
    
    def _send_telegram(self, message: str):
        """Enviar notificación a Telegram"""
        try:
            if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
                return
            
            url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {
                'chat_id': Config.TELEGRAM_CHAT_ID,
                'text': message,
                'parse_mode': 'HTML'
            }
            requests.post(url, json=data, timeout=5)
        except:
            pass
    
    async def print_status(self, iteration: int):
        """Imprimir estado actual"""
        balance = self.client.get_balance()
        risk_metrics = self.risk_manager.get_risk_metrics()
        
        logger.info("\n" + "="*80)
        logger.info(f"📊 STATUS - Iteración #{iteration} | {datetime.now().strftime('%H:%M:%S')}")
        logger.info("="*80)
        logger.info(f"💰 Balance: ${balance['total']:.2f} | Disponible: ${balance['available']:.2f}")
        logger.info(f"📈 Trades abiertos: {len(self.open_trades)}/{Config.MAX_OPEN_TRADES}")
        logger.info(f"📊 PnL Diario: ${risk_metrics['daily_pnl']:+.2f}")
        logger.info(f"📉 Drawdown: {risk_metrics['current_drawdown']:.2f}%")
        logger.info(f"🎯 Win Rate (7d): {risk_metrics['win_rate']:.1f}%")
        
        if Config.ML_ENABLED:
            ml_stats = self.ml_predictor.get_stats()
            logger.info(f"🤖 ML Muestras: {ml_stats['samples']} | Trained: {'✅' if ml_stats['trained'] else '❌'}")
        
        logger.info("="*80)
    
    async def run(self):
        """Loop principal"""
        logger.info("\n🚀 INICIANDO LOOP PRINCIPAL\n")
        
        iteration = 0
        last_symbol_update = 0
        last_ml_train = 0
        
        while True:
            try:
                iteration += 1
                current_time = datetime.now().timestamp()
                
                # Actualizar símbolos periódicamente
                if current_time - last_symbol_update > Config.MARKET_SCAN_INTERVAL:
                    await self.update_symbol_list()
                    last_symbol_update = current_time
                
                # Status
                await self.print_status(iteration)
                
                # Monitorear trades abiertos
                await self.monitor_open_trades()
                
                # Analizar símbolos y buscar oportunidades
                logger.info(f"\n🔍 Analizando {len(self.active_symbols)} símbolos...\n")
                
                signals_found = 0
                
                for symbol in self.active_symbols:
                    analysis = await self.analyze_symbol(symbol)
                    
                    if analysis['signal'] != 'NEUTRAL':
                        signals_found += 1
                        logger.info(
                            f"   📊 {symbol}: {analysis['signal']} "
                            f"(Confianza: {analysis['confidence']:.0%})"
                        )
                        
                        await self.execute_trade(analysis)
                    
                    await asyncio.sleep(0.1)
                
                logger.info(f"\n✅ Análisis completado: {signals_found} señales encontradas")
                
                # Re-entrenar ML periódicamente
                if Config.ML_ENABLED and self.ml_predictor.should_retrain():
                    if current_time - last_ml_train > Config.ML_RETRAIN_INTERVAL:
                        logger.info("\n🤖 Re-entrenando modelo ML...")
                        self.ml_predictor.train(min_samples=100)
                        last_ml_train = current_time
                
                # Actualizar balance diario
                balance = self.client.get_balance()
                self.stats_tracker.update_daily_balance(
                    balance['total'],
                    self.risk_manager.daily_pnl
                )
                
                # Esperar próxima iteración
                logger.info(f"\n⏱️ Próxima iteración en {Config.CHECK_INTERVAL}s...\n")
                await asyncio.sleep(Config.CHECK_INTERVAL)
            
            except KeyboardInterrupt:
                logger.info("\n🛑 Bot detenido por usuario")
                break
            
            except Exception as e:
                logger.error(f"\n❌ Error en loop principal: {e}", exc_info=True)
                await asyncio.sleep(10)
        
        # Cleanup
        logger.info("\n🧹 Limpiando recursos...")
        self.stats_tracker.close()
        logger.info("✅ Bot terminado")


async def main():
    """Punto de entrada"""
    try:
        bot = TradingBotPro()
        await bot.run()
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}", exc_info=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n👋 Bot terminado")
