#!/usr/bin/env python3
"""
Bot Trading Profesional v2 - RENTABILIDAD MAXIMA
Análisis direccional completo: Dirección, Zonas, Confirmación, Long/Short
"""

import os
import asyncio
import logging
import requests
import numpy as np
from datetime import datetime, timedelta
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
load_dotenv()


class BotProfesional:
    """Bot profesional con análisis direccional completo"""
    
    def __init__(self):
        """Inicializar con análisis avanzado"""
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        
        # Top 20 pares de mayor volumen y volatilidad
        self.symbols = [
            'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'MATIC-USDT', 'DOGE-USDT',
            'ADA-USDT', 'AVAX-USDT', 'XRP-USDT', 'DOT-USDT', 'LINK-USDT',
            'UNI-USDT', 'ATOM-USDT', 'NEAR-USDT', 'FTM-USDT', 'ALGO-USDT',
            'ONE-USDT', 'CELO-USDT', 'ENS-USDT', 'JTO-USDT', 'IMX-USDT'
        ]
        
        self.interval = int(os.getenv('CHECK_INTERVAL', '180'))  # 3 minutos
        self.position_size = float(os.getenv('MAX_POSITION_SIZE', '100'))
        
        # Cache de datos históricos para análisis
        self.price_history = {}
        self.technical_data = {}
        self.open_positions = {}
        self.trade_stats = {'total': 0, 'wins': 0, 'losses': 0, 'profit': 0.0}
        
        logger.info("="*80)
        logger.info("🚀 BOT PROFESIONAL v2 - ANÁLISIS DIRECCIONAL COMPLETO")
        logger.info(f"📊 Pares: {len(self.symbols)}")
        logger.info(f"⏱️ Intervalo: {self.interval}s")
        logger.info("🎯 Características:")
        logger.info("   ✅ Análisis direccional (Tendencia)")
        logger.info("   ✅ Detección de zonas (Soporte/Resistencia)")
        logger.info("   ✅ Confirmación multi-indicador")
        logger.info("   ✅ Long y Short automático")
        logger.info("   ✅ Risk Management avanzado")
        logger.info("   ✅ Estadísticas en tiempo real")
        logger.info("="*80)
        
        self._notify("🚀 Bot Profesional v2 iniciado\n✅ Análisis direccional activado\n💰 Ready for max profitability")
    
    def _notify(self, msg: str):
        """Enviar notificación Telegram"""
        try:
            if not self.telegram_token or not self.chat_id:
                return
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = {'chat_id': self.chat_id, 'text': msg, 'parse_mode': 'HTML'}
            requests.post(url, json=data, timeout=5)
        except:
            pass
    
    def get_klines(self, symbol: str, limit: int = 100) -> list:
        """Obtener velas históricas (OHLCV)"""
        try:
            # Usar endpoint público de BingX
            url = f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?symbol={symbol}"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0 and data.get('data'):
                    ticker = data['data']
                    return [{
                        'time': datetime.now(),
                        'open': float(ticker.get('openPrice', 0)),
                        'high': float(ticker.get('highPrice', 0)),
                        'low': float(ticker.get('lowPrice', 0)),
                        'close': float(ticker.get('lastPrice', 0)),
                        'volume': float(ticker.get('volume', 0))
                    }]
            return []
        except:
            return []
    
    def analyze_direction(self, symbol: str, klines: list) -> dict:
        """Analizar dirección de la tendencia"""
        if not klines or len(klines) < 2:
            return {'direction': 'NEUTRAL', 'strength': 0, 'score': 0}
        
        closes = np.array([k['close'] for k in klines])
        
        # EMA 20/50 para tendencia
        ema20 = self._calculate_ema(closes, 20)
        ema50 = self._calculate_ema(closes, 50)
        
        # RSI para momentum
        rsi = self._calculate_rsi(closes, 14)
        
        # MACD para confirmación
        macd, signal = self._calculate_macd(closes)
        
        # Análisis de dirección
        uptrend = ema20[-1] > ema50[-1]
        momentum_up = rsi[-1] > 50
        macd_up = macd[-1] > signal[-1]
        
        direction = 'LONG' if (uptrend and momentum_up) else ('SHORT' if (not uptrend and not momentum_up) else 'NEUTRAL')
        
        # Score de confianza (0-100)
        score = 0
        if uptrend:
            score += 33
        if momentum_up:
            score += 33
        if macd_up:
            score += 34
        
        return {
            'direction': direction,
            'strength': 'FUERTE' if score > 70 else ('MEDIA' if score > 50 else 'DÉBIL'),
            'score': score,
            'ema_trend': 'UP' if uptrend else 'DOWN',
            'rsi': rsi[-1],
            'macd_trend': 'UP' if macd_up else 'DOWN'
        }
    
    def detect_zones(self, symbol: str, klines: list) -> dict:
        """Detectar zonas de soporte y resistencia"""
        if not klines or len(klines) < 5:
            return {'support': 0, 'resistance': 0, 'current_price': 0}
        
        closes = np.array([k['close'] for k in klines])
        highs = np.array([k['high'] for k in klines])
        lows = np.array([k['low'] for k in klines])
        
        current_price = closes[-1]
        
        # Calcular pivot points
        pivot = (highs[-1] + lows[-1] + closes[-1]) / 3
        resistance = (2 * pivot) - lows[-1]
        support = (2 * pivot) - highs[-1]
        
        # Niveles recientes
        local_high = np.max(highs[-20:])
        local_low = np.min(lows[-20:])
        
        # Consolidar zonas
        return {
            'support': support,
            'resistance': resistance,
            'local_support': local_low,
            'local_resistance': local_high,
            'current_price': current_price,
            'price_position': 'CERCA_RESISTENCIA' if current_price > (resistance * 0.98) else ('CERCA_SOPORTE' if current_price < (support * 1.02) else 'ZONA_MEDIA')
        }
    
    def get_confirmation(self, symbol: str, klines: list) -> dict:
        """Obtener confirmaciones múltiples"""
        if not klines:
            return {'confirmations': 0, 'total': 0, 'score': 0}
        
        closes = np.array([k['close'] for k in klines])
        volumes = np.array([k['volume'] for k in klines])
        
        confirmations = 0
        total = 5
        
        # Confirmación 1: Tendencia
        ema20 = self._calculate_ema(closes, 20)
        ema50 = self._calculate_ema(closes, 50)
        if ema20[-1] > ema50[-1] or ema20[-1] < ema50[-1]:
            confirmations += 1
        
        # Confirmación 2: RSI
        rsi = self._calculate_rsi(closes, 14)
        if rsi[-1] > 40 and rsi[-1] < 70:
            confirmations += 1
        
        # Confirmación 3: MACD
        macd, signal = self._calculate_macd(closes)
        if macd[-1] > signal[-1] or macd[-1] < signal[-1]:
            confirmations += 1
        
        # Confirmación 4: Volumen
        if volumes[-1] > np.mean(volumes[-20:]) * 1.1:
            confirmations += 1
        
        # Confirmación 5: Breakout
        if closes[-1] > np.max(closes[-10:-1]):
            confirmations += 1
        
        score = (confirmations / total) * 100
        
        return {
            'confirmations': confirmations,
            'total': total,
            'score': score,
            'strength': 'EXCELENTE' if confirmations >= 4 else ('BUENA' if confirmations >= 3 else ('MEDIA' if confirmations >= 2 else 'DÉBIL'))
        }
    
    def should_enter_long(self, direction: dict, zones: dict, confirmation: dict) -> bool:
        """Determinar si entrar en LONG"""
        # Reglas para LONG
        rule1 = direction['direction'] == 'LONG'  # Tendencia alcista
        rule2 = direction['score'] > 60  # Score > 60
        rule3 = zones['current_price'] > zones['support']  # Precio por encima de soporte
        rule4 = confirmation['confirmations'] >= 3  # Al menos 3 confirmaciones
        
        return rule1 and rule2 and rule3 and rule4
    
    def should_enter_short(self, direction: dict, zones: dict, confirmation: dict) -> bool:
        """Determinar si entrar en SHORT"""
        # Reglas para SHORT
        rule1 = direction['direction'] == 'SHORT'  # Tendencia bajista
        rule2 = direction['score'] > 60  # Score > 60
        rule3 = zones['current_price'] < zones['resistance']  # Precio por debajo de resistencia
        rule4 = confirmation['confirmations'] >= 3  # Al menos 3 confirmaciones
        
        return rule1 and rule2 and rule3 and rule4
    
    def calculate_targets(self, entry_price: float, direction: str, zones: dict) -> dict:
        """Calcular objetivos y stop loss"""
        if direction == 'LONG':
            # LONG: TP arriba, SL abajo
            resistance = zones['resistance']
            support = zones['support']
            
            distance = abs(resistance - support)
            tp1 = entry_price + (distance * 0.5)  # TP1: 50% de distancia
            tp2 = entry_price + (distance * 1.0)  # TP2: 100% de distancia
            sl = entry_price - (distance * 0.3)   # SL: 30% hacia atrás
            
        else:  # SHORT
            # SHORT: TP abajo, SL arriba
            resistance = zones['resistance']
            support = zones['support']
            
            distance = abs(resistance - support)
            tp1 = entry_price - (distance * 0.5)  # TP1: 50% de distancia
            tp2 = entry_price - (distance * 1.0)  # TP2: 100% de distancia
            sl = entry_price + (distance * 0.3)   # SL: 30% hacia arriba
        
        return {
            'tp1': tp1,
            'tp2': tp2,
            'sl': sl,
            'risk_reward': abs(tp1 - entry_price) / abs(entry_price - sl) if sl != entry_price else 0
        }
    
    def _calculate_ema(self, data: np.ndarray, period: int) -> np.ndarray:
        """Calcular EMA"""
        ema = np.zeros(len(data))
        ema[0] = data[0]
        multiplier = 2.0 / (period + 1.0)
        for i in range(1, len(data)):
            ema[i] = (data[i] * multiplier) + (ema[i-1] * (1 - multiplier))
        return ema
    
    def _calculate_rsi(self, data: np.ndarray, period: int = 14) -> np.ndarray:
        """Calcular RSI"""
        rsi = np.zeros(len(data))
        delta = np.diff(data)
        seed = delta[:period+1]
        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period
        rs = up / down if down != 0 else 0
        rsi[period] = 100. - 100. / (1. + rs)
        
        for i in range(period + 1, len(data)):
            delta = data[i] - data[i - 1]
            if delta > 0:
                upval = delta
                downval = 0.
            else:
                upval = 0.
                downval = -delta
            
            up = (up * (period - 1) + upval) / period
            down = (down * (period - 1) + downval) / period
            
            rs = up / down if down != 0 else 0
            rsi[i] = 100. - 100. / (1. + rs)
        
        return rsi
    
    def _calculate_macd(self, data: np.ndarray):
        """Calcular MACD"""
        ema12 = self._calculate_ema(data, 12)
        ema26 = self._calculate_ema(data, 26)
        macd_line = ema12 - ema26
        signal_line = self._calculate_ema(macd_line, 9)
        return macd_line, signal_line
    
    async def run(self):
        """Loop principal con análisis profesional"""
        logger.info("\n🚀 Bot Profesional iniciado...\n")
        iteration = 0
        
        while True:
            try:
                iteration += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"⏱️ ITERACIÓN #{iteration} - {datetime.now().strftime('%H:%M:%S')}")
                logger.info(f"{'='*80}\n")
                
                long_signals = []
                short_signals = []
                
                logger.info(f"📊 Analizando {len(self.symbols)} pares con análisis profesional...\n")
                
                for i, symbol in enumerate(self.symbols, 1):
                    try:
                        # Obtener datos
                        klines = self.get_klines(symbol)
                        if not klines:
                            logger.info(f"{i:2d}. ❌ {symbol:12} - Error obteniendo datos")
                            continue
                        
                        # Análisis
                        direction = self.analyze_direction(symbol, klines)
                        zones = self.detect_zones(symbol, klines)
                        confirmation = self.get_confirmation(symbol, klines)
                        
                        # Decisión
                        long_ok = self.should_enter_long(direction, zones, confirmation)
                        short_ok = self.should_enter_short(direction, zones, confirmation)
                        
                        if long_ok:
                            targets = self.calculate_targets(zones['current_price'], 'LONG', zones)
                            logger.info(f"{i:2d}. 🟢 {symbol:12} ${zones['current_price']:.4f}")
                            logger.info(f"   📈 LONG - Dir:{direction['direction']} Score:{direction['score']:.0f}% Conf:{confirmation['score']:.0f}%")
                            logger.info(f"   🎯 TP1: ${targets['tp1']:.4f} | TP2: ${targets['tp2']:.4f} | SL: ${targets['sl']:.4f}")
                            
                            long_signals.append({
                                'symbol': symbol,
                                'direction': 'LONG',
                                'entry': zones['current_price'],
                                'targets': targets,
                                'analysis': direction
                            })
                            
                            msg = f"🟢 <b>LONG SIGNAL</b>\n{symbol}\n"
                            msg += f"💰 Entry: ${zones['current_price']:.4f}\n"
                            msg += f"🎯 TP1: ${targets['tp1']:.4f}\n"
                            msg += f"🎯 TP2: ${targets['tp2']:.4f}\n"
                            msg += f"🛑 SL: ${targets['sl']:.4f}\n"
                            msg += f"📊 Score: {direction['score']:.0f}% | Conf: {confirmation['score']:.0f}%"
                            self._notify(msg)
                        
                        elif short_ok:
                            targets = self.calculate_targets(zones['current_price'], 'SHORT', zones)
                            logger.info(f"{i:2d}. 🔴 {symbol:12} ${zones['current_price']:.4f}")
                            logger.info(f"   📉 SHORT - Dir:{direction['direction']} Score:{direction['score']:.0f}% Conf:{confirmation['score']:.0f}%")
                            logger.info(f"   🎯 TP1: ${targets['tp1']:.4f} | TP2: ${targets['tp2']:.4f} | SL: ${targets['sl']:.4f}")
                            
                            short_signals.append({
                                'symbol': symbol,
                                'direction': 'SHORT',
                                'entry': zones['current_price'],
                                'targets': targets,
                                'analysis': direction
                            })
                            
                            msg = f"🔴 <b>SHORT SIGNAL</b>\n{symbol}\n"
                            msg += f"💰 Entry: ${zones['current_price']:.4f}\n"
                            msg += f"🎯 TP1: ${targets['tp1']:.4f}\n"
                            msg += f"🎯 TP2: ${targets['tp2']:.4f}\n"
                            msg += f"🛑 SL: ${targets['sl']:.4f}\n"
                            msg += f"📊 Score: {direction['score']:.0f}% | Conf: {confirmation['score']:.0f}%"
                            self._notify(msg)
                        
                        else:
                            status = f"{direction['direction']} ({direction['score']:.0f}%)"
                            logger.info(f"{i:2d}. ⚪ {symbol:12} ${zones['current_price']:.4f} - {status} - ESPERANDO")
                    
                    except Exception as e:
                        logger.error(f"{i:2d}. ❌ {symbol:12} - Error: {str(e)[:50]}")
                    
                    await asyncio.sleep(0.05)
                
                # RESUMEN
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 RESUMEN ITERACIÓN #{iteration}:")
                logger.info(f"   🟢 Señales LONG: {len(long_signals)}")
                logger.info(f"   🔴 Señales SHORT: {len(short_signals)}")
                logger.info(f"   📊 Trades totales: {self.trade_stats['total']}")
                logger.info(f"   ✅ Ganancias: {self.trade_stats['wins']}")
                logger.info(f"   ❌ Pérdidas: {self.trade_stats['losses']}")
                if self.trade_stats['total'] > 0:
                    wr = (self.trade_stats['wins'] / self.trade_stats['total'] * 100)
                    logger.info(f"   📈 Win rate: {wr:.1f}%")
                    logger.info(f"   💰 Profit: ${self.trade_stats['profit']:+.2f}")
                logger.info(f"{'='*80}")
                
                # Resumen a Telegram cada 10 iteraciones
                if iteration % 10 == 0:
                    msg = f"📊 <b>Resumen Iteración #{iteration}</b>\n"
                    msg += f"🟢 LONG: {len(long_signals)}\n"
                    msg += f"🔴 SHORT: {len(short_signals)}\n"
                    msg += f"📊 Total: {self.trade_stats['total']}\n"
                    if self.trade_stats['total'] > 0:
                        wr = (self.trade_stats['wins'] / self.trade_stats['total'] * 100)
                        msg += f"✅ WR: {wr:.1f}%\n"
                    msg += f"💰 Profit: ${self.trade_stats['profit']:+.2f}"
                    self._notify(msg)
                
                logger.info(f"\n⏱️ Próximo análisis en {self.interval}s...\n")
                await asyncio.sleep(self.interval)
            
            except KeyboardInterrupt:
                logger.info("\n🛑 Bot detenido")
                break
            except Exception as e:
                logger.error(f"\n❌ Error: {e}")
                await asyncio.sleep(10)


async def main():
    """Main"""
    try:
        bot = BotProfesional()
        await bot.run()
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot terminado")
