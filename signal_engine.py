"""
Signal Engine v2 — más señales, misma base Pine Script
Mejoras respecto a v1:
  - Crossover detectado en las últimas SIGNAL_LOOKBACK barras (no solo la última)
  - RSI como señal alternativa cuando no hay crossover reciente
  - EMA en vez de SMA (más reactiva)
  - Filtro de tendencia: solo LONG si precio > EMA lenta, SHORT si precio < EMA lenta
  - Logging detallado para depuración
"""

import logging
import os
import numpy as np
from typing import Optional

from bingx_client import BingXClient

log = logging.getLogger("SIGNAL")

FAST_LEN        = int(os.getenv("MA_FAST",          "9"))
SLOW_LEN        = int(os.getenv("MA_SLOW",          "21"))
PROJ_LEN        = int(os.getenv("PROJ_LENGTH",      "10"))
MIN_MEAN_PNL    = float(os.getenv("MIN_MEAN_PNL",   "0.001"))   # 0.1% — más permisivo
SIGNAL_LOOKBACK = int(os.getenv("SIGNAL_LOOKBACK",  "3"))       # barras hacia atrás
USE_RSI         = os.getenv("USE_RSI", "true").lower() == "true"
RSI_PERIOD      = int(os.getenv("RSI_PERIOD",       "14"))
RSI_OB          = float(os.getenv("RSI_OB",         "70"))      # sobrecompra
RSI_OS          = float(os.getenv("RSI_OS",         "30"))      # sobreventa
MIN_SIGNALS_HIST = int(os.getenv("MIN_SIGNALS_HIST","2"))       # mínimo histórico


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
    day_pnls = [[] for _ in range(proj_len)]
    for sig_i in np.where(signals)[0]:
        for step in range(1, proj_len + 1):
            future_i = sig_i + step
            if future_i >= len(closes):
                break
            cum_pnl = closes[future_i] / closes[sig_i] - 1
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


# ------------------------------------------------------------------ #
#  Motor principal
# ------------------------------------------------------------------ #
class SignalEngine:

    async def evaluate(self, client: BingXClient, symbol: str) -> Optional[dict]:
        try:
            interval = os.getenv("KLINE_INTERVAL", "1h")
            limit    = max(SLOW_LEN * 3, 150)
            klines   = await client.get_klines(symbol, interval, limit=limit)

            if len(klines) < SLOW_LEN + PROJ_LEN + 10:
                return None

            closes = np.array([float(k["close"]) for k in klines])

            ma_fast = ema(closes, FAST_LEN)
            ma_slow = ema(closes, SLOW_LEN)

            long_cross  = crossover_arr(ma_fast, ma_slow)
            short_cross = crossunder_arr(ma_fast, ma_slow)

            # ---- Detectar crossover en las últimas SIGNAL_LOOKBACK barras ----
            window      = SIGNAL_LOOKBACK
            recent_long  = bool(long_cross[-window:].any())
            recent_short = bool(short_cross[-window:].any())

            direction   = None
            signal_type = None

            if recent_long and not recent_short:
                direction   = "LONG"
                signal_type = "EMA_CROSS"
            elif recent_short and not recent_long:
                direction   = "SHORT"
                signal_type = "EMA_CROSS"

            # ---- RSI como señal alternativa ----
            if direction is None and USE_RSI:
                rsi_arr = rsi(closes, RSI_PERIOD)
                rsi_now = rsi_arr[-1]
                rsi_prev = rsi_arr[-2] if len(rsi_arr) > 1 else np.nan

                if not np.isnan(rsi_now) and not np.isnan(rsi_prev):
                    # Salida de sobreventa → LONG
                    if rsi_prev <= RSI_OS and rsi_now > RSI_OS:
                        direction   = "LONG"
                        signal_type = "RSI_OS"
                    # Salida de sobrecompra → SHORT
                    elif rsi_prev >= RSI_OB and rsi_now < RSI_OB:
                        direction   = "SHORT"
                        signal_type = "RSI_OB"

            if direction is None:
                return None

            # ---- Filtro de tendencia: confirmar con EMA lenta ----
            price = closes[-1]
            ema_slow_now = ma_slow[-1]
            if not np.isnan(ema_slow_now):
                if direction == "LONG"  and price < ema_slow_now * 0.995:
                    log.debug(f"  ↷  {symbol} LONG rechazado: precio bajo EMA lenta")
                    return None
                if direction == "SHORT" and price > ema_slow_now * 1.005:
                    log.debug(f"  ↷  {symbol} SHORT rechazado: precio sobre EMA lenta")
                    return None

            # ---- Proyección histórica de PnL ----
            hist_sigs = long_cross if direction == "LONG" else short_cross
            # Excluir las últimas `window` barras (la señal actual)
            projections = project_pnl(closes, hist_sigs[:-window], PROJ_LEN)

            if not projections:
                log.debug(f"  ↷  {symbol}: sin proyecciones históricas")
                return None

            last_step = max(projections.keys())
            proj      = projections[last_step]

            if proj["count"] < MIN_SIGNALS_HIST:
                log.debug(f"  ↷  {symbol}: solo {proj['count']} señales históricas")
                return None

            expected_sign = 1 if direction == "LONG" else -1
            if proj["mean"] * expected_sign < MIN_MEAN_PNL:
                log.debug(f"  ↷  {symbol} {direction}: mean={proj['mean']:.3%} < {MIN_MEAN_PNL:.3%}")
                return None

            log.info(f"  ✅  {symbol} {direction} [{signal_type}]  "
                     f"mean={proj['mean']:.2%}  worst={proj['worst']:.2%}  n={proj['count']}")

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
                "timestamp":    klines[-1]["time"],
            }

        except Exception as e:
            log.warning(f"Error evaluando {symbol}: {e}")
            return None
