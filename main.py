#!/usr/bin/env python3
"""
Bot de Trading Automatizado BingX - Ultra Optimizado v4
Combina Linear Regression + Volatility Stop + Múltiples Filtros

Features:
✅ Análisis multi-par en paralelo (hasta 50+ pares)
✅ Filtros ultra-precisos (ADX, RSI, MACD, Volumen)
✅ Volatility Stop dinámico basado en ATR
✅ Gestión de riesgo automática
✅ Notificaciones Telegram en tiempo real
✅ Compatible con Railway (hosting gratis)
"""

import os
import asyncio
import logging
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

from strategy import TradingStrategy
from bingx_client import BingXClient
from telegram_notifier import TelegramNotifier

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()


class TradingBot:
    """Bot multi-par con estrategia ultra-optimizada"""
    
    def __init__(self):
        """Inicializar bot"""
        # Configuración de pares
        symbols_str = os.getenv('SYMBOLS', 'BTC-USDT,ETH-USDT,SOL-USDT')
        self.symbols = [s.strip() for s in symbols_str.split(',')]
        
        self.timeframe = os.getenv('TIMEFRAME', '15m')
        self.check_interval = int(os.getenv('CHECK_INTERVAL', '60'))
        self.max_position_size = float(os.getenv('MAX_POSITION_SIZE', '100'))
        self.max_positions = int(os.getenv('MAX_POSITIONS', '3'))
        
        # Parámetros de estrategia
        self.linreg_length = int(os.getenv('LINREG_LENGTH', '50'))
        self.linreg_mult = float(os.getenv('LINREG_MULT', '2.2'))
        self.adx_threshold = int(os.getenv('ADX_THRESHOLD', '25'))
        self.risk_reward = float(os.getenv('RISK_REWARD', '2.5'))
        
        # Inicializar componentes
        self.exchange = BingXClient()
        self.strategy = TradingStrategy(
            linreg_length=self.linreg_length,
            linreg_mult=self.linreg_mult,
            adx_threshold=self.adx_threshold,
            risk_reward=self.risk_reward
        )
        self.telegram = TelegramNotifier()
        
        # Rastreo
        self.active_positions = {}
        self.signal_cache = {}
        self.last_analysis = {}
        
        logger.info(f"🤖 Bot Ultra-Optimizado v4 Inicializado")
        logger.info(f"📊 Pares: {len(self.symbols)} | Timeframe: {self.timeframe}")
        logger.info(f"⚙️ LinReg: {self.linreg_length}p x{self.linreg_mult} | ADX: >{self.adx_threshold}")
        
        self._send_startup_message()
    
    def _send_startup_message(self):
        """Enviar mensaje de inicio"""
        symbols_list = '\n'.join([f"  • {s}" for s in self.symbols])
        message = (
            f"🤖 <b>Bot Multi-Par Ultra-Optimizado v4</b>\n\n"
            f"📊 <b>Análisis:</b> {len(self.symbols)} pares\n{symbols_list}\n\n"
            f"⏱ <b>Timeframe:</b> {self.timeframe}\n"
            f"💰 <b>Max Posiciones:</b> {self.max_positions}\n"
            f"📦 <b>Tamaño/Posición:</b> ${self.max_position_size}\n\n"
            f"🎯 <b>Parámetros:</b>\n"
            f"  • LinReg: {self.linreg_length} periodos x{self.linreg_mult}\n"
            f"  • ADX Threshold: {self.adx_threshold}\n"
            f"  • Ratio R:R: 1:{self.risk_reward}\n\n"
            f"✅ Bot iniciado y monitoreando..."
        )
        self.telegram.send_message(message)
    
    async def run(self):
        """Loop principal"""
        logger.info("🚀 Iniciando loop de trading...")
        
        while True:
            try:
                await self.trading_cycle()
                await asyncio.sleep(self.check_interval)
                
            except KeyboardInterrupt:
                logger.info("⏹ Bot detenido por usuario")
                self.telegram.send_message("⏹ Bot detenido manualmente")
                break
                
            except Exception as e:
                logger.error(f"❌ Error: {e}", exc_info=True)
                self.telegram.send_message(f"⚠️ Error: {str(e)}")
                await asyncio.sleep(60)
    
    async def trading_cycle(self):
        """Ciclo de trading"""
        
        # Analizar todos los pares en paralelo
        tasks = [self.analyze_symbol(symbol) for symbol in self.symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Procesar resultados
        signals = []
        for symbol, result in zip(self.symbols, results):
            if isinstance(result, Exception):
                logger.error(f"Error en {symbol}: {result}")
                continue
            if result and result['action'] != 'NONE':
                # Filtrar señales por confianza mínima
                if result.get('confidence', 0) >= 50:
                    signals.append((symbol, result))
        
        # Ordenar por confianza/fuerza
        signals.sort(key=lambda x: x[1].get('confidence', 0), reverse=True)
        
        # Ejecutar mejores señales
        for symbol, signal in signals:
            if len(self.active_positions) >= self.max_positions:
                logger.info(f"⏸️ Límite de posiciones ({self.max_positions})")
                break
            
            await self.process_signal(symbol, signal)
    
    async def analyze_symbol(self, symbol: str) -> Optional[dict]:
        """Analizar símbolo"""
        try:
            candles = await self.exchange.get_klines(symbol, self.timeframe, limit=150)
            if not candles:
                return {'action': 'NONE'}
            
            signal = self.strategy.analyze(candles)
            signal['symbol'] = symbol
            
            self.last_analysis[symbol] = {
                'timestamp': datetime.now(),
                'signal': signal
            }
            
            return signal
            
        except Exception as e:
            logger.error(f"Error analizando {symbol}: {e}")
            return {'action': 'NONE'}
    
    async def process_signal(self, symbol: str, signal: dict):
        """Procesar señal"""
        
        current_position = await self.exchange.get_position(symbol)
        
        if signal['action'] == 'LONG' and not current_position and symbol not in self.active_positions:
            await self.open_long(symbol, signal)
            
        elif signal['action'] == 'SHORT' and not current_position and symbol not in self.active_positions:
            await self.open_short(symbol, signal)
        
        if current_position:
            await self.manage_position(symbol, current_position, signal)
    
    async def open_long(self, symbol: str, signal: dict):
        """Abrir LONG"""
        try:
            price = signal['price']
            stop_loss = signal['stop_loss']
            take_profit = signal['take_profit']
            confidence = signal.get('confidence', 0)
            
            quantity = self.calculate_position_size(price, stop_loss)
            
            logger.info(f"📈 [{symbol}] LONG - Conf: {confidence}% | P:{price:.2f} | SL:{stop_loss:.2f} | TP:{take_profit:.2f}")
            
            order = await self.exchange.place_order(
                symbol=symbol,
                side='BUY',
                quantity=quantity,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit
            )
            
            if order:
                self.active_positions[symbol] = {
                    'side': 'LONG',
                    'entry_price': price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'quantity': quantity,
                    'confidence': confidence,
                    'opened_at': datetime.now()
                }
                
                risk = price - stop_loss
                reward = take_profit - price
                rr_ratio = reward / risk if risk > 0 else 0
                
                message = (
                    f"🟢 <b>LONG ABIERTO</b> - {symbol}\n"
                    f"💯 <b>Confianza:</b> {confidence}%\n"
                    f"📊 <b>Precio:</b> {price:.4f}\n"
                    f"🛑 <b>Stop Loss:</b> {stop_loss:.4f}\n"
                    f"🎯 <b>Take Profit:</b> {take_profit:.4f}\n"
                    f"📦 <b>Cantidad:</b> {quantity:.6f}\n"
                    f"📈 <b>R:R:</b> 1:{rr_ratio:.2f}\n"
                    f"⚡ {signal['reasons']}\n"
                    f"💼 Posiciones: {len(self.active_positions)}/{self.max_positions}"
                )
                self.telegram.send_message(message)
                logger.info(f"✅ [{symbol}] LONG abierto")
                
        except Exception as e:
            logger.error(f"❌ Error abriendo LONG en {symbol}: {e}")
            self.telegram.send_message(f"❌ Error LONG {symbol}: {str(e)}")
    
    async def open_short(self, symbol: str, signal: dict):
        """Abrir SHORT"""
        try:
            price = signal['price']
            stop_loss = signal['stop_loss']
            take_profit = signal['take_profit']
            confidence = signal.get('confidence', 0)
            
            quantity = self.calculate_position_size(price, stop_loss)
            
            logger.info(f"📉 [{symbol}] SHORT - Conf: {confidence}% | P:{price:.2f} | SL:{stop_loss:.2f} | TP:{take_profit:.2f}")
            
            order = await self.exchange.place_order(
                symbol=symbol,
                side='SELL',
                quantity=quantity,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit
            )
            
            if order:
                self.active_positions[symbol] = {
                    'side': 'SHORT',
                    'entry_price': price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'quantity': quantity,
                    'confidence': confidence,
                    'opened_at': datetime.now()
                }
                
                risk = stop_loss - price
                reward = price - take_profit
                rr_ratio = reward / risk if risk > 0 else 0
                
                message = (
                    f"🔴 <b>SHORT ABIERTO</b> - {symbol}\n"
                    f"💯 <b>Confianza:</b> {confidence}%\n"
                    f"📊 <b>Precio:</b> {price:.4f}\n"
                    f"🛑 <b>Stop Loss:</b> {stop_loss:.4f}\n"
                    f"🎯 <b>Take Profit:</b> {take_profit:.4f}\n"
                    f"📦 <b>Cantidad:</b> {quantity:.6f}\n"
                    f"📉 <b>R:R:</b> 1:{rr_ratio:.2f}\n"
                    f"⚡ {signal['reasons']}\n"
                    f"💼 Posiciones: {len(self.active_positions)}/{self.max_positions}"
                )
                self.telegram.send_message(message)
                logger.info(f"✅ [{symbol}] SHORT abierto")
                
        except Exception as e:
            logger.error(f"❌ Error abriendo SHORT en {symbol}: {e}")
            self.telegram.send_message(f"❌ Error SHORT {symbol}: {str(e)}")
    
    async def manage_position(self, symbol: str, position: dict, signal: dict):
        """Gestionar posición abierta"""
        pass
    
    def calculate_position_size(self, entry_price: float, stop_loss: float) -> float:
        """Calcular tamaño basado en riesgo 2%"""
        risk_per_trade = self.max_position_size * 0.02
        price_diff = abs(entry_price - stop_loss)
        
        if price_diff == 0:
            return 0
        
        quantity = risk_per_trade / price_diff
        return round(quantity, 8)


async def main():
    """Función principal"""
    bot = TradingBot()
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
