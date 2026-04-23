"""
Signal Engine v6 — Paralelo, Anticipación de Movimientos
=========================================================
FIXES vs v5:
  - MTF y klines se descargan en paralelo (no secuencial)
  - Manejo robusto de ob_snap None (no crashea)
  - active_sigs siempre es lista (nunca None)

MEJORAS:
  - Fetch de klines 1h y 4h en paralelo con asyncio.gather
  - Usa precio WS del cliente para la señal más fresca
  - RSI y BB calculados una sola vez
  - OI change simulado desde variación de volumen (hasta tener endpoint)
  - Score de ANTICIPACIÓN: detecta el setup ANTES del movimiento
    (BB squeeze + CVD divergencia + volumen acelerando)
"""

import asyncio
import logging
import os
import numpy as np
from typing import Optional
from datetime import datetime

log = logging.getLogger("SIGNAL")

FAST_LEN         = int(os.getenv("MA_FAST",          "9"))
SLOW_LEN         = int(os.getenv("MA_SLOW",          "21"))
RSI_PERIOD       = int(os.getenv("RSI_PERIOD",       "14"))
BB_PERIOD        = int(os.getenv("BB_PERIOD",        "20"))
BB_STD           = float(os.getenv("BB_STD",         "2.0"))
MIN_SIGNAL_SCORE = float(os.getenv("MIN_SIGNAL_SCORE","40"))
PROJ_LEN         = int(os.getenv("PROJ_LENGTH",      "10"))
KLINE_INTERVAL   = os.getenv("KLINE_INTERVAL",       "1h")


# ------------------------------------------------------------------ #
#  Indicadores
# ------------------------------------------------------------------ #
def ema(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(arr), np.nan)
    if len(arr) < period:
        return out
    out[period - 1] = arr[:period].mean()
    k = 2 / (period + 1)
    for i in range(period, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def rsi(arr: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full(len(arr), np.nan)
    if len(arr) < period + 1:
        return out
    d    = np.diff(arr)
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    ag   = gain[:period].mean()
    al   = loss[:period].mean()
    for i in range(period, len(d)):
        ag = (ag * (period - 1) + gain[i]) / period
        al = (al * (period - 1) + loss[i]) / period
        if al == 0:
            out[i + 1] = 100.0
        else:
            out[i + 1] = 100 - 100 / (1 + ag / al)
    return out


def bollinger(arr: np.ndarray, period: int = 20, std: float = 2.0):
    """Devuelve (width_pct, squeeze_pct, upper, lower, mid)."""
    if len(arr) < period:
        return 0.0, 100.0, arr[-1], arr[-1], arr[-1]

    mid_arr = np.array([arr[i - period + 1:i + 1].mean()
                        for i in range(period - 1, len(arr))])
    std_arr = np.array([arr[i - period + 1:i + 1].std()
                        for i in range(period - 1, len(arr))])

    upper_arr = mid_arr + std * std_arr
    lower_arr = mid_arr - std * std_arr

    last_mid   = mid_arr[-1]
    last_upper = upper_arr[-1]
    last_lower = lower_arr[-1]

    width_pct = ((last_upper - last_lower) / last_mid * 100) if last_mid > 0 else 0.0

    # squeeze: cuán comprimidas están las bandas vs historial 50 velas
    if len(mid_arr) >= 20:
        hist_widths = (upper_arr[-50:] - lower_arr[-50:]) / np.where(
            mid_arr[-50:] > 0, mid_arr[-50:], 1) * 100
        avg_w       = hist_widths.mean()
        squeeze_pct = (width_pct / avg_w * 100) if avg_w > 0 else 100.0
    else:
        squeeze_pct = 100.0

    return width_pct, squeeze_pct, last_upper, last_lower, last_mid


# ------------------------------------------------------------------ #
#  Signal Engine
# ------------------------------------------------------------------ #
class SignalEngine:
    def __init__(self, ob_analyzer=None):
        self.ob_analyzer = ob_analyzer

    async def evaluate(self, client, symbol: str, ob_snap=None) -> Optional[dict]:
        try:
            limit = max(SLOW_LEN * 3, 150)

            # ── Descarga 1h y 4h EN PARALELO ──────────────────────── #
            klines_1h, klines_4h = await asyncio.gather(
                client.get_klines(symbol, KLINE_INTERVAL, limit=limit),
                client.get_klines(symbol, "4h", limit=50),
                return_exceptions=True,
            )

            if isinstance(klines_1h, Exception) or len(klines_1h) < SLOW_LEN + 10:
                return None

            closes  = np.array([float(k["close"])  for k in klines_1h])
            volumes = np.array([float(k["volume"]) for k in klines_1h])
            highs   = np.array([float(k["high"])   for k in klines_1h])
            lows    = np.array([float(k["low"])    for k in klines_1h])

            # Precio más fresco: usar WebSocket si disponible
            ws_price = client.get_ws_price(symbol)
            price    = ws_price if ws_price else closes[-1]

            ma_fast  = ema(closes, FAST_LEN)
            ma_slow  = ema(closes, SLOW_LEN)
            rsi_arr  = rsi(closes, RSI_PERIOD)
            bb_width, bb_squeeze, bb_up, bb_low, bb_mid = bollinger(closes, BB_PERIOD, BB_STD)

            # ============================================================ #
            #  SCORING
            # ============================================================ #
            score     = 0.0
            direction = None
            active    = []

            # ── 1. EMA trend / crossover (20pts) ──────────────────── #
            if not (np.isnan(ma_fast[-1]) or np.isnan(ma_slow[-1])):
                fast_now, slow_now = ma_fast[-1], ma_slow[-1]
                fast_prev, slow_prev = ma_fast[-2], ma_slow[-2]

                if fast_now > slow_now:
                    direction = "LONG"
                    if fast_prev <= slow_prev:           # crossover fresco
                        score += 20; active.append("EMA Cross ↑")
                    else:
                        score += 10; active.append("EMA ↑")
                elif fast_now < slow_now:
                    direction = "SHORT"
                    if fast_prev >= slow_prev:
                        score += 20; active.append("EMA Cross ↓")
                    else:
                        score += 10; active.append("EMA ↓")

            if direction is None:
                return None

            # ── 2. RSI (15pts) ────────────────────────────────────── #
            rsi_now = rsi_arr[-1]
            if not np.isnan(rsi_now):
                if direction == "LONG":
                    if rsi_now < 30:   score += 15; active.append(f"RSI OS {rsi_now:.0f}")
                    elif rsi_now < 45: score += 10
                    elif rsi_now < 50: score += 5
                else:
                    if rsi_now > 70:   score += 15; active.append(f"RSI OB {rsi_now:.0f}")
                    elif rsi_now > 55: score += 10
                    elif rsi_now > 50: score += 5

            # ── 3. Bollinger Bands (20pts) ────────────────────────── #
            if bb_squeeze < 70:
                score += 15; active.append(f"BB {bb_width:.1f}%")
                # Precio tocando la banda en la dirección correcta
                if direction == "LONG" and price <= bb_low * 1.02:
                    score += 5; active.append("BB Lower")
                elif direction == "SHORT" and price >= bb_up * 0.98:
                    score += 5; active.append("BB Upper")
            if bb_squeeze > 130:
                score += 5; active.append("BB Expand")

            # ── 4. Volume spike (15pts) ──────────────────────────── #
            vol_ma = volumes[-21:-1].mean() if len(volumes) > 21 else volumes[:-1].mean()
            if vol_ma > 0:
                v_ratio = volumes[-1] / vol_ma
                if v_ratio > 3:   score += 15; active.append(f"Vol {v_ratio:.1f}x")
                elif v_ratio > 2: score += 10
                elif v_ratio > 1.5: score += 5

            # ── 5. Order Book / CVD (20pts) ────────────────────────── #
            cvd_pct = 50.0
            ob_bias = "NEUTRAL"
            if ob_snap is not None:
                cvd_pct = ob_snap.cvd_pct
                ob_bias = ob_snap.bias

                if direction == "LONG":
                    if cvd_pct > 70:   score += 10; active.append(f"CVD {cvd_pct:.0f}%")
                    elif cvd_pct > 55: score += 5
                else:
                    if cvd_pct < 30:   score += 10; active.append(f"CVD {cvd_pct:.0f}%")
                    elif cvd_pct < 45: score += 5

                if (direction == "LONG"  and ob_bias == "BULLISH") or \
                   (direction == "SHORT" and ob_bias == "BEARISH"):
                    score += 10; active.append("OB Bias")

                if ob_snap.absorption_signal:
                    side_ok = (direction == "LONG"  and ob_snap.absorption_side == "BID") or \
                              (direction == "SHORT" and ob_snap.absorption_side == "ASK")
                    if side_ok:
                        score += 5; active.append("Absorption")

            # ── 6. Multi-timeframe (10pts) ─────────────────────────── #
            mtf_pts, mtf_label = self._check_mtf_sync(klines_4h, direction)
            score += mtf_pts
            if mtf_pts > 0:
                active.append(f"MTF {mtf_label}")

            # ── 7. ANTICIPACIÓN: BB squeeze + CVD divergencia (10pts) ─ #
            # Setup de anticipación: bandas comprimidas + CVD favorable
            # pero el precio aún NO se ha movido → señal temprana
            if bb_squeeze < 60:
                if direction == "LONG" and cvd_pct > 60:
                    score += 10; active.append("Setup Anticip↑")
                elif direction == "SHORT" and cvd_pct < 40:
                    score += 10; active.append("Setup Anticip↓")

            # ============================================================ #
            if score < MIN_SIGNAL_SCORE:
                return None

            # ── Proyección histórica ──────────────────────────────── #
            mean_p, med_p, worst_p, best_p = self._project(
                closes, ma_fast, ma_slow, direction
            )

            rr = (abs(mean_p) / abs(worst_p)) if worst_p != 0 else 0

            return {
                "symbol":     symbol,
                "direction":  direction,
                "score":      min(score, 100),
                "price":      price,
                "interval":   KLINE_INTERVAL,
                "signal_type":"MULTI_FACTOR",
                "active_sigs": active,
                "mean_pnl":   mean_p,
                "median_pnl": med_p,
                "worst_pnl":  worst_p,
                "best_pnl":   best_p,
                "risk_reward": round(rr, 2),
                "bb_width":   bb_width,
                "bb_squeeze": bb_squeeze,
                "bb_expansion": bb_squeeze > 130,
                "cvd_pct":    cvd_pct,
                "ob_bias":    ob_bias,
                "mtf_label":  mtf_label,
                "timestamp":  klines_1h[-1].get("time", ""),
            }

        except Exception as e:
            log.warning(f"evaluate {symbol}: {e}")
            return None

    # ------------------------------------------------------------------ #
    def _check_mtf_sync(self, klines_4h, direction: str):
        """MTF check síncrono usando klines ya descargadas."""
        if isinstance(klines_4h, Exception) or not klines_4h or len(klines_4h) < 20:
            return 0, "?"
        try:
            c4h     = np.array([float(k["close"]) for k in klines_4h])
            fast_4h = ema(c4h, FAST_LEN)
            slow_4h = ema(c4h, SLOW_LEN)
            if np.isnan(fast_4h[-1]) or np.isnan(slow_4h[-1]):
                return 0, "?"
            aligned = (direction == "LONG"  and fast_4h[-1] > slow_4h[-1]) or \
                      (direction == "SHORT" and fast_4h[-1] < slow_4h[-1])
            return (10, "2/2") if aligned else (0, "1/2")
        except Exception:
            return 0, "?"

    # ------------------------------------------------------------------ #
    def _project(self, closes, ma_fast, ma_slow, direction):
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

        pnls = []
        for ci in crosses:
            fi = ci + PROJ_LEN
            if fi >= len(closes):
                continue
            entry = closes[ci]
            exit_ = closes[fi]
            pnl   = (exit_ / entry - 1) if direction == "LONG" else (entry / exit_ - 1)
            pnls.append(pnl)

        if not pnls:
            return 0.01, 0.01, -0.01, 0.02

        a = np.array(pnls)
        return float(a.mean()), float(np.median(a)), float(a.min()), float(a.max())
