"""
Signal Engine v5 — Scoring Gradual con BB Squeeze, MTF, CVD
============================================================
Mejoras vs v1:
  - Sistema de scoring 0-100 (no binario)
  - BB Squeeze detection
  - Multi-timeframe confluence (MTF)
  - Order book integration (CVD, imbalance)
  - Señales graduales (múltiples indicadores contribuyen al score)
"""

import logging
import os
import numpy as np
from typing import Optional
from datetime import datetime

from bingx_client import BingXClient
from order_book import OrderBookSnapshot

log = logging.getLogger("SIGNAL")

# Configuración
FAST_LEN = int(os.getenv("MA_FAST", "9"))
SLOW_LEN = int(os.getenv("MA_SLOW", "21"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
BB_PERIOD = int(os.getenv("BB_PERIOD", "20"))
BB_STD = float(os.getenv("BB_STD", "2.0"))
MIN_SIGNAL_SCORE = float(os.getenv("MIN_SIGNAL_SCORE", "40"))
PROJ_LEN = int(os.getenv("PROJ_LENGTH", "10"))


# ------------------------------------------------------------------ #
#  Indicadores
# ------------------------------------------------------------------ #
def ema(arr: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average."""
    result = np.full(len(arr), np.nan)
    if len(arr) < period:
        return result
    result[period - 1] = arr[:period].mean()
    k = 2 / (period + 1)
    for i in range(period, len(arr)):
        result[i] = arr[i] * k + result[i - 1] * (1 - k)
    return result


def rsi(arr: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index."""
    result = np.full(len(arr), np.nan)
    if len(arr) < period + 1:
        return result
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100 - 100 / (1 + rs)
    return result


def bollinger_bands(arr: np.ndarray, period: int = 20, std: float = 2.0):
    """
    Bollinger Bands.
    Returns: (middle, upper, lower, width_pct, squeeze_pct)
    """
    if len(arr) < period:
        return None, None, None, 0.0, 0.0
    
    middle = np.full(len(arr), np.nan)
    upper = np.full(len(arr), np.nan)
    lower = np.full(len(arr), np.nan)
    
    for i in range(period - 1, len(arr)):
        window = arr[i - period + 1:i + 1]
        m = window.mean()
        s = window.std()
        middle[i] = m
        upper[i] = m + std * s
        lower[i] = m - std * s
    
    # BB width (ancho de las bandas como % del precio)
    last_middle = middle[-1]
    last_upper = upper[-1]
    last_lower = lower[-1]
    
    if not np.isnan(last_middle) and last_middle > 0:
        width_pct = ((last_upper - last_lower) / last_middle) * 100
    else:
        width_pct = 0.0
    
    # Squeeze detection (ancho histórico)
    valid_widths = []
    for i in range(max(0, len(arr) - 50), len(arr)):
        if not np.isnan(middle[i]) and middle[i] > 0:
            w = ((upper[i] - lower[i]) / middle[i]) * 100
            valid_widths.append(w)
    
    if valid_widths:
        avg_width = np.mean(valid_widths)
        squeeze_pct = (width_pct / avg_width) * 100 if avg_width > 0 else 100
    else:
        squeeze_pct = 100
    
    return middle, upper, lower, width_pct, squeeze_pct


# ------------------------------------------------------------------ #
#  Signal Engine
# ------------------------------------------------------------------ #
class SignalEngine:
    def __init__(self, ob_analyzer=None):
        self.ob_analyzer = ob_analyzer
    
    async def evaluate(
        self, 
        client: BingXClient, 
        symbol: str,
        ob_snap: Optional[OrderBookSnapshot] = None
    ) -> Optional[dict]:
        """
        Evalúa señal usando scoring gradual.
        Devuelve None si score < MIN_SIGNAL_SCORE.
        """
        try:
            # Obtener klines
            interval = os.getenv("KLINE_INTERVAL", "1h")
            limit = max(SLOW_LEN * 3, 150)
            klines = await client.get_klines(symbol, interval, limit=limit)
            
            if len(klines) < SLOW_LEN + PROJ_LEN + 10:
                return None
            
            closes = np.array([float(k["close"]) for k in klines])
            highs = np.array([float(k["high"]) for k in klines])
            lows = np.array([float(k["low"]) for k in klines])
            volumes = np.array([float(k["volume"]) for k in klines])
            
            # Indicadores
            ma_fast = ema(closes, FAST_LEN)
            ma_slow = ema(closes, SLOW_LEN)
            rsi_arr = rsi(closes, RSI_PERIOD)
            bb_mid, bb_up, bb_low, bb_width, bb_squeeze = bollinger_bands(
                closes, BB_PERIOD, BB_STD
            )
            
            # Precio actual
            price = closes[-1]
            
            # ============================================
            # SCORING SYSTEM (0-100 points)
            # ============================================
            score = 0.0
            direction = None
            active_signals = []
            
            # 1. EMA Crossover (20 points)
            if not np.isnan(ma_fast[-1]) and not np.isnan(ma_slow[-1]):
                # LONG: fast > slow
                if ma_fast[-1] > ma_slow[-1]:
                    # Crossover reciente (últimas 3 velas)
                    for i in range(1, 4):
                        if (ma_fast[-i-1] <= ma_slow[-i-1] and 
                            ma_fast[-i] > ma_slow[-i]):
                            score += 20
                            active_signals.append("EMA Cross ↑")
                            direction = "LONG"
                            break
                    # Si no hay cross reciente pero está arriba, 10pts
                    if direction != "LONG" and ma_fast[-1] > ma_slow[-1]:
                        score += 10
                        active_signals.append("EMA ↑")
                        direction = "LONG"
                
                # SHORT: fast < slow
                elif ma_fast[-1] < ma_slow[-1]:
                    for i in range(1, 4):
                        if (ma_fast[-i-1] >= ma_slow[-i-1] and 
                            ma_fast[-i] < ma_slow[-i]):
                            score += 20
                            active_signals.append("EMA Cross ↓")
                            direction = "SHORT"
                            break
                    if direction != "SHORT" and ma_fast[-1] < ma_slow[-1]:
                        score += 10
                        active_signals.append("EMA ↓")
                        direction = "SHORT"
            
            # Si no hay dirección clara, salir
            if direction is None:
                return None
            
            # 2. RSI (15 points)
            rsi_now = rsi_arr[-1]
            if not np.isnan(rsi_now):
                if direction == "LONG":
                    # RSI oversold = buena oportunidad de compra
                    if rsi_now < 30:
                        score += 15
                        active_signals.append("RSI Oversold")
                    elif rsi_now < 45:
                        score += 10
                    elif rsi_now < 50:
                        score += 5
                else:  # SHORT
                    if rsi_now > 70:
                        score += 15
                        active_signals.append("RSI Overbought")
                    elif rsi_now > 55:
                        score += 10
                    elif rsi_now > 50:
                        score += 5
            
            # 3. Bollinger Bands Squeeze (20 points)
            if bb_squeeze < 70:  # Squeeze (bandas comprimidas)
                score += 15
                active_signals.append(f"BB Squeeze {bb_width:.1f}%")
                
                # Si el precio está cerca de la banda en la dirección correcta
                if direction == "LONG" and price <= bb_low[-1] * 1.02:
                    score += 5
                    active_signals.append("BB Lower Touch")
                elif direction == "SHORT" and price >= bb_up[-1] * 0.98:
                    score += 5
                    active_signals.append("BB Upper Touch")
            
            # BB expansion (explosión después de squeeze)
            if bb_squeeze > 130:
                score += 5
                active_signals.append("BB Expansion")
            
            # 4. Volume Spike (15 points)
            vol_ma = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
            if vol_ma > 0:
                vol_ratio = volumes[-1] / vol_ma
                if vol_ratio > 3:
                    score += 15
                    active_signals.append(f"Vol acelerado {vol_ratio:.1f}x")
                elif vol_ratio > 2:
                    score += 10
                elif vol_ratio > 1.5:
                    score += 5
            
            # 5. Order Book (CVD + Imbalance) (20 points)
            if ob_snap:
                # CVD
                cvd_pct = ob_snap.cvd_pct
                if direction == "LONG":
                    # CVD alto = más presión compradora
                    if cvd_pct > 70:
                        score += 10
                        active_signals.append(f"CVD {cvd_pct:.0f}% bull")
                    elif cvd_pct > 55:
                        score += 5
                else:  # SHORT
                    if cvd_pct < 30:
                        score += 10
                        active_signals.append(f"CVD {cvd_pct:.0f}% bear")
                    elif cvd_pct < 45:
                        score += 5
                
                # Imbalance
                if direction == "LONG" and ob_snap.bias == "BULLISH":
                    score += 10
                    active_signals.append("OB Bullish")
                elif direction == "SHORT" and ob_snap.bias == "BEARISH":
                    score += 10
                    active_signals.append("OB Bearish")
                
                # Absorption signal
                if ob_snap.absorption_signal:
                    if (direction == "LONG" and ob_snap.absorption_side == "BID") or \
                       (direction == "SHORT" and ob_snap.absorption_side == "ASK"):
                        score += 5
                        active_signals.append("Absorption")
            
            # 6. Multi-timeframe (10 points)
            # Verificar timeframe superior (simplificado)
            mtf_score, mtf_label = await self._check_mtf(
                client, symbol, direction, closes
            )
            score += mtf_score
            if mtf_score > 0:
                active_signals.append(f"MTF {mtf_label}")
            
            # ============================================
            # FILTRO FINAL
            # ============================================
            if score < MIN_SIGNAL_SCORE:
                return None
            
            # Proyección histórica (para TP/SL)
            mean_pnl, median_pnl, worst_pnl, best_pnl = self._project_pnl(
                closes, ma_fast, ma_slow, direction
            )
            
            # Risk:Reward
            if worst_pnl != 0:
                risk_reward = abs(mean_pnl) / abs(worst_pnl)
            else:
                risk_reward = 0
            
            # Compilar señal
            signal = {
                "symbol": symbol,
                "direction": direction,
                "score": score,
                "price": price,
                "interval": interval,
                "signal_type": "MULTI_FACTOR",
                "active_sigs": active_signals,
                "mean_pnl": mean_pnl,
                "median_pnl": median_pnl,
                "worst_pnl": worst_pnl,
                "best_pnl": best_pnl,
                "risk_reward": risk_reward,
                "bb_width": bb_width,
                "bb_squeeze": bb_squeeze,
                "bb_expansion": bb_squeeze > 130,
                "cvd_pct": ob_snap.cvd_pct if ob_snap else 50,
                "ob_bias": ob_snap.bias if ob_snap else "NEUTRAL",
                "mtf_label": mtf_label,
                "timestamp": klines[-1]["time"],
            }
            
            return signal
            
        except Exception as e:
            log.warning(f"Error evaluando {symbol}: {e}")
            return None
    
    # ------------------------------------------------------------------ #
    async def _check_mtf(self, client, symbol, direction, closes_1h):
        """
        Multi-timeframe check simplificado.
        Verifica si el timeframe superior (4h) confirma la dirección.
        """
        try:
            # Obtener velas 4h
            klines_4h = await client.get_klines(symbol, "4h", limit=50)
            if len(klines_4h) < 20:
                return 0, "0/1"
            
            closes_4h = np.array([float(k["close"]) for k in klines_4h])
            ma_fast_4h = ema(closes_4h, FAST_LEN)
            ma_slow_4h = ema(closes_4h, SLOW_LEN)
            
            # Verificar tendencia en 4h
            if not np.isnan(ma_fast_4h[-1]) and not np.isnan(ma_slow_4h[-1]):
                if direction == "LONG" and ma_fast_4h[-1] > ma_slow_4h[-1]:
                    return 10, "2/2"  # 1h y 4h alineados
                elif direction == "SHORT" and ma_fast_4h[-1] < ma_slow_4h[-1]:
                    return 10, "2/2"
            
            return 0, "1/2"  # Solo 1h confirma
            
        except Exception:
            return 0, "1/1"
    
    # ------------------------------------------------------------------ #
    def _project_pnl(self, closes, ma_fast, ma_slow, direction):
        """Proyección histórica de PnL."""
        # Detectar cruces históricos
        crosses = []
        for i in range(1, len(ma_fast) - PROJ_LEN):
            if np.isnan(ma_fast[i]) or np.isnan(ma_slow[i]):
                continue
            if direction == "LONG":
                if ma_fast[i-1] <= ma_slow[i-1] and ma_fast[i] > ma_slow[i]:
                    crosses.append(i)
            else:
                if ma_fast[i-1] >= ma_slow[i-1] and ma_fast[i] < ma_slow[i]:
                    crosses.append(i)
        
        if not crosses:
            return 0.01, 0.01, -0.01, 0.02
        
        # Calcular PnL para cada cruce
        pnls = []
        for cross_i in crosses:
            entry = closes[cross_i]
            # Proyectar PROJ_LEN velas adelante
            future_i = cross_i + PROJ_LEN
            if future_i >= len(closes):
                continue
            exit_price = closes[future_i]
            pnl = (exit_price / entry - 1) if direction == "LONG" else (entry / exit_price - 1)
            pnls.append(pnl)
        
        if not pnls:
            return 0.01, 0.01, -0.01, 0.02
        
        arr = np.array(pnls)
        return (
            float(arr.mean()),
            float(np.median(arr)),
            float(arr.min()),
            float(arr.max())
        )
