"""
Estrategia Ultra-Optimizada v4 de Trading
Combina indicadores probados de TradingView con filtros de calidad

Indicadores:
- Linear Regression Channel (tendencia principal)
- Volatility Stop (tendencia dinámico basado en ATR)
- PDH/PDL (niveles de liquidez)
- ADX (fuerza de tendencia)
- EMAs (régimen de mercado)
- RSI (sobrecompra/sobreventa)
- MACD (momentum)
- ATR (volatilidad)
- Bollinger Bands (extensión)
- Volumen (confirmación)
"""

import numpy as np
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


class TradingStrategy:
    """Estrategia ultra-optimizada basada en indicadores comprobados"""
    
    def __init__(self,
                 linreg_length: int = 50,
                 linreg_mult: float = 2.2,
                 ema_fast: int = 20,
                 ema_slow: int = 50,
                 ema_trend: int = 200,
                 adx_threshold: int = 25,
                 atr_length: int = 14,
                 atr_mult: float = 2.0,
                 rsi_period: int = 14,
                 risk_reward: float = 2.5):
        """Inicializar con parámetros optimizados"""
        self.linreg_length = linreg_length
        self.linreg_mult = linreg_mult
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.ema_trend = ema_trend
        self.adx_threshold = adx_threshold
        self.atr_length = atr_length
        self.atr_mult = atr_mult
        self.rsi_period = rsi_period
        self.risk_reward = risk_reward
    
    def analyze(self, candles: List[dict]) -> Dict:
        """Análisis ultra-filtrado con múltiples confirmaciones"""
        
        if len(candles) < max(self.linreg_length, self.ema_trend) + 20:
            return self._no_signal("Datos insuficientes")
        
        # Extraer OHLCV
        closes = np.array([float(c['close']) for c in candles])
        highs = np.array([float(c['high']) for c in candles])
        lows = np.array([float(c['low']) for c in candles])
        volumes = np.array([float(c['volume']) for c in candles])
        
        current_price = closes[-1]
        current_high = highs[-1]
        current_low = lows[-1]
        
        # ========== INDICADORES BÁSICOS ==========
        
        # 1. Linear Regression Channel
        linreg_basis, linreg_upper, linreg_lower, slope = self._calculate_linreg(closes)
        
        # 2. Volatility Stop (ATR basado)
        vstop_long, vstop_short, is_uptrend = self._calculate_volatility_stop(closes, highs, lows)
        
        # 3. EMAs para régimen
        ema_f = self._ema(closes, self.ema_fast)
        ema_s = self._ema(closes, self.ema_slow)
        ema_t = self._ema(closes, self.ema_trend)
        
        current_ema_f = ema_f[-1]
        current_ema_s = ema_s[-1]
        current_ema_t = ema_t[-1]
        
        # 4. Tendencias por EMA
        ema_bullish = current_ema_f > current_ema_s
        price_above_trend = current_price > current_ema_t
        bullish_regime = ema_bullish and price_above_trend
        
        ema_bearish = current_ema_f < current_ema_s
        price_below_trend = current_price < current_ema_t
        bearish_regime = ema_bearish and price_below_trend
        
        # 5. ADX (fuerza de tendencia)
        adx = self._calculate_adx(highs, lows, closes)
        strong_trend = adx > self.adx_threshold
        
        # 6. ATR (volatilidad)
        atr = self._calculate_atr(highs, lows, closes, self.atr_length)
        atr_sma = np.mean([self._calculate_atr(highs[max(0, i-14):i], 
                                               lows[max(0, i-14):i], 
                                               closes[max(0, i-14):i]) 
                          for i in range(max(1, len(closes)-28), len(closes))])
        high_volatility = atr > atr_sma * 1.3
        
        # 7. RSI
        rsi = self._calculate_rsi(closes, self.rsi_period)
        rsi_bullish = rsi > 40 and rsi < 70
        rsi_bearish = rsi > 30 and rsi < 60
        rsi_neutral = rsi > 35 and rsi < 65
        
        # 8. Volumen
        vol_sma = np.mean(volumes[-20:])
        high_volume = volumes[-1] > vol_sma * 1.5
        
        # 9. MACD
        macd_line, signal_line, hist = self._calculate_macd(closes)
        macd_bullish = macd_line > signal_line and hist > 0
        macd_bearish = macd_line < signal_line and hist < 0
        
        # 10. Bollinger Bands
        bb_middle, bb_upper, bb_lower = self._calculate_bollinger(closes, 20, 2)
        not_overextended_up = current_price < bb_upper * 0.98
        not_overextended_down = current_price > bb_lower * 1.02
        
        # ========== FILTROS DE CALIDAD ==========
        
        # Distancia desde EMA
        distance_from_ema = abs((current_price - current_ema_s) / current_price) * 100
        not_too_far = distance_from_ema < 3.0
        
        # PDH/PDL
        pdh, pdl = self._calculate_pdh_pdl(candles)
        prev_close = closes[-2]
        
        crosses_above_pdh = prev_close <= pdh and current_high >= pdh
        crosses_below_pdl = prev_close >= pdl and current_low <= pdl
        
        # ========== GENERACIÓN DE SEÑALES ==========
        
        reasons = []
        
        # ===== SEÑAL LONG (múltiples confirmaciones) =====
        if (crosses_above_pdh and bullish_regime and strong_trend and
            macd_bullish and rsi_bullish and high_volume and
            not_overextended_up and not_too_far and
            is_uptrend):  # Confirmar con Volatility Stop
            
            reasons.append("✅ Cruce PDH (resistencia rota)")
            reasons.append(f"✅ Bullish EMA (F:{current_ema_f:.0f} > S:{current_ema_s:.0f})")
            reasons.append(f"✅ Precio > Tendencia ({current_price:.0f} > {current_ema_t:.0f})")
            reasons.append(f"✅ ADX fuerte: {adx:.1f}")
            reasons.append(f"✅ MACD bullish")
            reasons.append(f"✅ RSI: {rsi:.1f} (alcista)")
            reasons.append(f"✅ Volumen alto ({volumes[-1]:.0f})")
            reasons.append(f"✅ V-Stop alcista ({vstop_long:.0f})")
            
            # SL en band inferior
            stop_loss = max(linreg_lower, vstop_long)
            risk = current_price - stop_loss
            take_profit = current_price + (risk * self.risk_reward)
            
            confidence = min(100, int(
                (adx / 50) * 25 +
                (rsi / 100) * 15 +
                35 +
                (20 if high_volume else 0 + 5 if is_uptrend else 0)
            ))
            
            return {
                'action': 'LONG',
                'price': current_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'reasons': ' | '.join(reasons),
                'confidence': confidence,
                'adx': adx,
                'rsi': rsi,
                'vstop': vstop_long,
                'atr': atr
            }
        
        # ===== SEÑAL SHORT (múltiples confirmaciones) =====
        elif (crosses_below_pdl and bearish_regime and strong_trend and
              macd_bearish and rsi_bearish and high_volume and
              not_overextended_down and not_too_far and
              not is_uptrend):  # Confirmar con Volatility Stop
            
            reasons.append("✅ Cruce PDL (soporte roto)")
            reasons.append(f"✅ Bearish EMA (F:{current_ema_f:.0f} < S:{current_ema_s:.0f})")
            reasons.append(f"✅ Precio < Tendencia ({current_price:.0f} < {current_ema_t:.0f})")
            reasons.append(f"✅ ADX fuerte: {adx:.1f}")
            reasons.append(f"✅ MACD bearish")
            reasons.append(f"✅ RSI: {rsi:.1f} (bajista)")
            reasons.append(f"✅ Volumen alto ({volumes[-1]:.0f})")
            reasons.append(f"✅ V-Stop bajista ({vstop_short:.0f})")
            
            # SL en banda superior
            stop_loss = min(linreg_upper, vstop_short)
            risk = stop_loss - current_price
            take_profit = current_price - (risk * self.risk_reward)
            
            confidence = min(100, int(
                (adx / 50) * 25 +
                ((100 - rsi) / 100) * 15 +
                35 +
                (20 if high_volume else 0 + 5 if not is_uptrend else 0)
            ))
            
            return {
                'action': 'SHORT',
                'price': current_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'reasons': ' | '.join(reasons),
                'confidence': confidence,
                'adx': adx,
                'rsi': rsi,
                'vstop': vstop_short,
                'atr': atr
            }
        
        return self._no_signal("No hay confirmación de múltiples indicadores")
    
    # ========== INDICADORES TÉCNICOS ==========
    
    def _calculate_linreg(self, closes: np.ndarray) -> tuple:
        """Linear Regression Channel"""
        data = closes[-self.linreg_length:]
        x = np.arange(len(data))
        
        coeffs = np.polyfit(x, data, 1)
        linreg_line = np.polyval(coeffs, x)
        linreg_basis = linreg_line[-1]
        slope = coeffs[0]
        
        std_dev = np.std(data)
        linreg_upper = linreg_basis + (std_dev * self.linreg_mult)
        linreg_lower = linreg_basis - (std_dev * self.linreg_mult)
        
        return linreg_basis, linreg_upper, linreg_lower, slope
    
    def _calculate_volatility_stop(self, closes: np.ndarray, highs: np.ndarray, lows: np.ndarray) -> tuple:
        """Volatility Stop basado en ATR (como el indicador de TradingView)"""
        atr = self._calculate_atr(highs, lows, closes, self.atr_length)
        atr_mult = atr * self.atr_mult
        
        # Calcular máximos y mínimos
        max_price = np.max(closes[-self.atr_length:])
        min_price = np.min(closes[-self.atr_length:])
        
        # Volatility Stop
        vstop_long = max_price - atr_mult
        vstop_short = min_price + atr_mult
        
        # Determinar tendencia (si el precio está arriba del vstop_short)
        is_uptrend = closes[-1] > vstop_short
        
        return vstop_long, vstop_short, is_uptrend
    
    def _ema(self, data: np.ndarray, period: int) -> np.ndarray:
        """Exponential Moving Average"""
        ema = np.zeros(len(data))
        sma = np.mean(data[:period])
        ema[period - 1] = sma
        
        multiplier = 2 / (period + 1)
        
        for i in range(period, len(data)):
            ema[i] = (data[i] - ema[i - 1]) * multiplier + ema[i - 1]
        
        return ema
    
    def _calculate_adx(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
        """Average Directional Index"""
        plus_dm = np.zeros(len(closes))
        minus_dm = np.zeros(len(closes))
        tr = np.zeros(len(closes))
        
        for i in range(1, len(closes)):
            # True Range
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
            
            # Directional Movement
            up_move = highs[i] - highs[i - 1]
            down_move = lows[i - 1] - lows[i]
            
            if up_move > down_move and up_move > 0:
                plus_dm[i] = up_move
            if down_move > up_move and down_move > 0:
                minus_dm[i] = down_move
        
        # Smooth
        atr = self._calculate_atr(highs, lows, closes, period)
        
        plus_di = 100 * np.sum(plus_dm[-period:]) / (atr * period) if atr > 0 else 0
        minus_di = 100 * np.sum(minus_dm[-period:]) / (atr * period) if atr > 0 else 0
        
        di_diff = abs(plus_di - minus_di)
        di_sum = plus_di + minus_di
        
        adx = 100 * di_diff / di_sum if di_sum > 0 else 0
        
        return adx
    
    def _calculate_atr(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
        """Average True Range"""
        tr = np.zeros(len(closes))
        
        for i in range(1, len(closes)):
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
        
        atr_val = np.mean(tr[-period:])
        return atr_val if atr_val > 0 else np.mean(highs[-period:] - lows[-period:])
    
    def _calculate_rsi(self, closes: np.ndarray, period: int = 14) -> float:
        """Relative Strength Index"""
        deltas = np.diff(closes[-period-1:])
        seed = deltas[:period]
        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period
        
        rs = up / down if down != 0 else 0
        rsi = 100 - (100 / (1 + rs))
        
        for d in deltas[period:]:
            if d >= 0:
                up = (up * (period - 1) + d) / period
                down = (down * (period - 1)) / period
            else:
                up = (up * (period - 1)) / period
                down = (down * (period - 1) - d) / period
            
            rs = up / down if down != 0 else 0
            rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def _calculate_macd(self, closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple:
        """MACD Indicator"""
        ema_fast = self._ema(closes, fast)[-1]
        ema_slow = self._ema(closes, slow)[-1]
        macd_line = ema_fast - ema_slow
        
        macd_series = self._ema(closes, fast) - self._ema(closes, slow)
        signal_line = self._ema(macd_series, signal)[-1]
        
        hist = macd_line - signal_line
        
        return macd_line, signal_line, hist
    
    def _calculate_bollinger(self, closes: np.ndarray, period: int = 20, mult: float = 2) -> tuple:
        """Bollinger Bands"""
        sma = np.mean(closes[-period:])
        std = np.std(closes[-period:])
        
        upper = sma + (std * mult)
        lower = sma - (std * mult)
        
        return sma, upper, lower
    
    def _calculate_pdh_pdl(self, candles: List[dict]) -> tuple:
        """Previous Day High/Low"""
        daily_data = {}
        
        for candle in candles:
            timestamp = int(candle['timestamp'])
            day = timestamp // (24 * 60 * 60 * 1000)
            
            if day not in daily_data:
                daily_data[day] = {
                    'high': float(candle['high']),
                    'low': float(candle['low'])
                }
            else:
                daily_data[day]['high'] = max(daily_data[day]['high'], float(candle['high']))
                daily_data[day]['low'] = min(daily_data[day]['low'], float(candle['low']))
        
        days = sorted(daily_data.keys())
        
        if len(days) < 2:
            closes = [float(c['close']) for c in candles]
            return max(closes), min(closes)
        
        previous_day = days[-2]
        pdh = daily_data[previous_day]['high']
        pdl = daily_data[previous_day]['low']
        
        return pdh, pdl
    
    def _no_signal(self, reason: str = "") -> Dict:
        """Sin señal"""
        return {
            'action': 'NONE',
            'price': 0,
            'stop_loss': 0,
            'take_profit': 0,
            'reasons': reason,
            'confidence': 0,
            'adx': 0,
            'rsi': 0,
            'vstop': 0,
            'atr': 0
        }
