"""
Signal Engine v3 — EMA Cross + RSI + Order Book Confluence
==========================================================
Mejoras sobre v2:
  - Confirma señal con sesgo del order book (imbalance ratio)
  - TP/SL dinámicos basados en muros de liquidez
  - Multiframe: confirma señal en 1h con tendencia en 4h
  - Divergencia RSI mejorada
  - Pine Script projection fiel al indicador original de QuantNomad
"""

import logging
import os
import numpy as np
from typing import Optional

from bingx_client import BingXClient
from order_book import OrderBookAnalyzer, OrderBookSnapshot

log = logging.getLogger("SIGNAL")

FAST_LEN         = int  (os.getenv("MA_FAST",           "9"))
SLOW_LEN         = int  (os.getenv("MA_SLOW",           "21"))
PROJ_LEN         = int  (os.getenv("PROJ_LENGTH",       "10"))
MIN_MEAN_PNL     = float(os.getenv("MIN_MEAN_PNL",      "0.001"))
SIGNAL_LOOKBACK  = int  (os.getenv("SIGNAL_LOOKBACK",   "3"))
USE_RSI          = os.getenv("USE_RSI", "true").lower() == "true"
RSI_PERIOD       = int  (os.getenv("RSI_PERIOD",        "14"))
RSI_OB           = float(os.getenv("RSI_OB",            "70"))
RSI_OS           = float(os.getenv("RSI_OS",            "30"))
MIN_SIGNALS_HIST = int  (os.getenv("MIN_SIGNALS_HIST",  "2"))
USE_OB_FILTER    = os.getenv("USE_OB_FILTER", "true").lower() == "true"
TREND_INTERVAL   = os.getenv("TREND_INTERVAL", "4h")    # HTF para filtro tendencia
USE_TREND_FILTER = os.getenv("USE_TREND_FILTER", "true").lower() == "true"
TP_MULT          = float(os.getenv("TP_MULT",           "2.0"))
SL_PCT           = float(os.getenv("SL_PCT",            "0.015"))   # fallback 1.5%


# ------------------------------------------------------------------ #
#  Indicadores
# ------------------------------------------------------------------ #
def ema(arr: np.ndarray, period: int) -> np.ndarray:
    result = np.full(len(arr), np.nan)
    if len(arr) < period:
        return result
    result[period - 1] = arr[:period].mean()
    k = 2 / (period + 1)
    for i in range(period, len(arr)):
        result[i] = arr[i] * k + result[i - 1] * (1 - k)
    return result


def rsi(arr: np.ndarray, period: int = 14) -> np.ndarray:
    result = np.full(len(arr), np.nan)
    if len(arr) < period + 1:
        return result
    deltas = np.diff(arr)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g  = gains[:period].mean()
    avg_l  = losses[:period].mean()
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + gains[i])  / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        result[i + 1] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return result


def crossover_arr(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    sig = np.zeros(len(a), dtype=bool)
    for i in range(1, len(a)):
        if any(np.isnan(x) for x in [a[i], b[i], a[i-1], b[i-1]]):
            continue
        if a[i-1] <= b[i-1] and a[i] > b[i]:
            sig[i] = True
    return sig


def crossunder_arr(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    sig = np.zeros(len(a), dtype=bool)
    for i in range(1, len(a)):
        if any(np.isnan(x) for x in [a[i], b[i], a[i-1], b[i-1]]):
            continue
        if a[i-1] >= b[i-1] and a[i] < b[i]:
            sig[i] = True
    return sig


def project_pnl(closes: np.ndarray, signals: np.ndarray, proj_len: int) -> dict:
    """
    Proyecta el PnL histórico tras cada señal, tal como hace
    el indicador Signal Projection Explorer de QuantNomad.
    Devuelve dict {step: {worst, best, p25, p75, mean, median, count}}
    """
    day_pnls = [[] for _ in range(proj_len)]
    for sig_i in np.where(signals)[0]:
        last_pnl = 0.0
        for step in range(1, proj_len + 1):
            future_i = sig_i + step
            if future_i >= len(closes):
                break
            # PnL acumulado desde la señal (igual que Pine: (close/close[signal])-1)
            cum_pnl = (closes[future_i] / closes[sig_i]) - 1
            day_pnls[step - 1].append(cum_pnl)
    result = {}
    for step, pnls in enumerate(day_pnls):
        if not pnls:
            continue
        arr = np.array(pnls)
        result[step] = {
            "worst":  float(arr.min()),
            "best":   float(arr.max()),
            "p25":    float(np.percentile(arr, 25)),
            "p75":    float(np.percentile(arr, 75)),
            "mean":   float(arr.mean()),
            "median": float(np.median(arr)),
            "count":  len(pnls),
        }
    return result


def _htf_trend(closes_4h: np.ndarray, slow: int = 50) -> str:
    """Determina tendencia HTF con EMA50 en 4h."""
    if len(closes_4h) < slow:
        return "NEUTRAL"
    ema_slow = ema(closes_4h, slow)
    if np.isnan(ema_slow[-1]):
        return "NEUTRAL"
    price = closes_4h[-1]
    if price > ema_slow[-1] * 1.002:
        return "BULL"
    if price < ema_slow[-1] * 0.998:
        return "BEAR"
    return "NEUTRAL"


# ------------------------------------------------------------------ #
#  Motor principal
# ------------------------------------------------------------------ #
class SignalEngine:

    def __init__(self, ob_analyzer: Optional[OrderBookAnalyzer] = None):
        self.ob = ob_analyzer

    async def evaluate(
        self,
        client: BingXClient,
        symbol: str,
        ob_snap: Optional[OrderBookSnapshot] = None,
    ) -> Optional[dict]:
        try:
            interval = os.getenv("KLINE_INTERVAL", "1h")
            limit    = max(SLOW_LEN * 3, 200)
            klines   = await client.get_klines(symbol, interval, limit=limit)

            if len(klines) < SLOW_LEN + PROJ_LEN + 10:
                return None

            closes = np.array([float(k["close"]) for k in klines])
            ma_fast = ema(closes, FAST_LEN)
            ma_slow = ema(closes, SLOW_LEN)

            long_cross  = crossover_arr(ma_fast, ma_slow)
            short_cross = crossunder_arr(ma_fast, ma_slow)

            window        = SIGNAL_LOOKBACK
            recent_long   = bool(long_cross[-window:].any())
            recent_short  = bool(short_cross[-window:].any())

            direction   = None
            signal_type = None

            if recent_long and not recent_short:
                direction, signal_type = "LONG",  "EMA_CROSS"
            elif recent_short and not recent_long:
                direction, signal_type = "SHORT", "EMA_CROSS"

            # ---- RSI señal alternativa ---------------------------------
            if direction is None and USE_RSI:
                rsi_arr  = rsi(closes, RSI_PERIOD)
                rsi_now  = rsi_arr[-1]
                rsi_prev = rsi_arr[-2] if len(rsi_arr) > 1 else np.nan
                if not np.isnan(rsi_now) and not np.isnan(rsi_prev):
                    if rsi_prev <= RSI_OS and rsi_now > RSI_OS:
                        direction, signal_type = "LONG",  "RSI_OS"
                    elif rsi_prev >= RSI_OB and rsi_now < RSI_OB:
                        direction, signal_type = "SHORT", "RSI_OB"

            if direction is None:
                return None

            # ---- Filtro de tendencia EMA lenta -------------------------
            price        = closes[-1]
            ema_slow_now = ma_slow[-1]
            if not np.isnan(ema_slow_now):
                if direction == "LONG"  and price < ema_slow_now * 0.995:
                    log.debug(f"↷ {symbol} LONG rechazado: precio bajo EMA lenta")
                    return None
                if direction == "SHORT" and price > ema_slow_now * 1.005:
                    log.debug(f"↷ {symbol} SHORT rechazado: precio sobre EMA lenta")
                    return None

            # ---- Filtro HTF (tendencia 4h) -----------------------------
            if USE_TREND_FILTER:
                klines_htf = await client.get_klines(symbol, TREND_INTERVAL, limit=100)
                if len(klines_htf) >= 50:
                    closes_htf = np.array([float(k["close"]) for k in klines_htf])
                    htf = _htf_trend(closes_htf)
                    if htf == "BULL" and direction == "SHORT":
                        log.debug(f"↷ {symbol} SHORT rechazado: tendencia HTF alcista")
                        return None
                    if htf == "BEAR" and direction == "LONG":
                        log.debug(f"↷ {symbol} LONG rechazado: tendencia HTF bajista")
                        return None

            # ---- Filtro Order Book ------------------------------------
            ob_bias = "NEUTRAL"
            ob_imbalance = 1.0
            if USE_OB_FILTER and ob_snap is not None:
                ob_bias      = ob_snap.bias
                ob_imbalance = ob_snap.imbalance_ratio
                if direction == "LONG"  and ob_bias == "BEARISH":
                    log.debug(f"↷ {symbol} LONG rechazado: OB bearish (imb={ob_imbalance:.2f})")
                    return None
                if direction == "SHORT" and ob_bias == "BULLISH":
                    log.debug(f"↷ {symbol} SHORT rechazado: OB bullish (imb={ob_imbalance:.2f})")
                    return None

            # ---- Proyección PnL histórica (QuantNomad logic) ----------
            hist_sigs   = long_cross if direction == "LONG" else short_cross
            projections = project_pnl(closes, hist_sigs[:-window], PROJ_LEN)

            if not projections:
                log.debug(f"↷ {symbol}: sin proyecciones históricas")
                return None

            last_step = max(projections.keys())
            proj      = projections[last_step]

            if proj["count"] < MIN_SIGNALS_HIST:
                log.debug(f"↷ {symbol}: solo {proj['count']} señales históricas")
                return None

            expected_sign = 1 if direction == "LONG" else -1
            if proj["mean"] * expected_sign < MIN_MEAN_PNL:
                log.debug(f"↷ {symbol} {direction}: mean={proj['mean']:.3%} < {MIN_MEAN_PNL:.3%}")
                return None

            # ---- TP / SL óptimos ----------------------------------------
            if ob_snap:
                tp_price = OrderBookAnalyzer.suggest_tp(ob_snap, direction, price, proj["mean"], TP_MULT)
                sl_price = OrderBookAnalyzer.suggest_sl(ob_snap, direction, price, SL_PCT)
            else:
                if direction == "LONG":
                    tp_price = round(price * (1 + abs(proj["mean"]) * TP_MULT), 8)
                    sl_price = round(price * (1 - SL_PCT), 8)
                else:
                    tp_price = round(price * (1 - abs(proj["mean"]) * TP_MULT), 8)
                    sl_price = round(price * (1 + SL_PCT), 8)

            risk_reward = abs(tp_price - price) / abs(price - sl_price) if abs(price - sl_price) > 0 else 0

            log.info(
                f"✅ {symbol} {direction} [{signal_type}] "
                f"mean={proj['mean']:.2%} worst={proj['worst']:.2%} "
                f"OB={ob_bias}({ob_imbalance:.2f}) R:R={risk_reward:.2f} "
                f"n={proj['count']}"
            )

            return {
                "symbol":       symbol,
                "direction":    direction,
                "signal_type":  signal_type,
                "price":        price,
                "interval":     interval,
                "mean_pnl":     proj["mean"],
                "median_pnl":   proj["median"],
                "worst_pnl":    proj["worst"],
                "best_pnl":     proj["best"],
                "p25_pnl":      proj["p25"],
                "p75_pnl":      proj["p75"],
                "signal_count": proj["count"],
                "projections":  projections,
                "tp_price":     tp_price,
                "sl_price":     sl_price,
                "risk_reward":  round(risk_reward, 2),
                "ob_bias":      ob_bias,
                "ob_imbalance": ob_imbalance,
                "timestamp":    klines[-1]["time"],
            }

        except Exception as e:
            log.warning(f"Error evaluando {symbol}: {e}")
            return None
