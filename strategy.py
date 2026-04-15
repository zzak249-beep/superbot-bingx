"""
strategy.py — Signal engine for SuperBot v4
Produces Signal objects with full TP1/TP2/TP3, score 0-100, tier.

Layers:
  1. HMA 7/9 crossover on 1m
  2. MTF confirmation 5m + 15m
  3. Market regime (ADX + BB width)
  4. Synthetic CVD (order flow proxy)
  5. Liquidity sweep detector
  6. Volume Profile / HVN proximity bonus
  7. Runner / mega-runner detection
  8. Score → tier assignment
"""

import os
import math
import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("strategy")

# ── Config from env ───────────────────────────────────────────────────────────
HMA_FAST         = int(os.environ.get("HMA_FAST", 7))
HMA_SLOW         = int(os.environ.get("HMA_SLOW", 9))
ATR_LEN          = int(os.environ.get("ATR_LEN", 14))
SL_ATR_MULT      = float(os.environ.get("SL_ATR", 1.5))
VOL_LEN          = int(os.environ.get("VOL_LEN", 20))
VOL_MULT         = float(os.environ.get("VOL_MULT", 1.2))
ADX_LEN          = int(os.environ.get("ADX_LEN", 14))
ADX_TREND        = float(os.environ.get("ADX_TREND", 20.0))
BB_LEN           = int(os.environ.get("BB_LEN", 20))
CVD_LEN          = int(os.environ.get("CVD_LEN", 20))
SWEEP_BARS       = int(os.environ.get("SWEEP_BARS", 5))
ENABLE_SHORTS    = os.environ.get("ENABLE_SHORTS", "true").lower() == "true"
MIN_ATR_PCT      = float(os.environ.get("MIN_ATR_PCT", 0.5))       # min ATR as % of price
VP_LOOKBACK      = int(os.environ.get("VP_LOOKBACK", 100))
VP_BINS          = int(os.environ.get("VP_BINS", 20))
HVN_THRESHOLD    = float(os.environ.get("HVN_THRESHOLD_PCT", 4.0)) # top 4% volume bins = HVN
HVN_PROXIMITY    = float(os.environ.get("HVN_PROXIMITY_PCT", 0.8)) # within 0.8% = near HVN
HVN_SCORE_BONUS  = int(os.environ.get("HVN_SCORE_BONUS", 20))
HVN_ENTRY_FILTER = os.environ.get("HVN_ENTRY_FILTER", "true").lower() == "true"
RUNNER_ENABLED   = os.environ.get("RUNNER_ENABLED", "true").lower() == "true"
MEGA_RUNNER_ATR  = float(os.environ.get("MEGA_RUNNER_ATR_MULT", 3.0))
TP1_RR           = float(os.environ.get("TP1_RR", 1.5))
TP2_RR           = float(os.environ.get("TP2_RR", 2.5))
TP3_RR           = float(os.environ.get("TP3_RR", 4.0))


# ── Signal dataclass ──────────────────────────────────────────────────────────

@dataclass
class Signal:
    symbol:    str
    direction: str        # "LONG" | "SHORT"
    entry:     float
    sl:        float
    tp1:       float
    tp2:       float
    tp3:       float
    atr:       float
    score:     float      # 0-100
    reason:    str
    regime:    str = "TRENDING"
    tier:      str = "B"
    vol_ok:    bool = True
    sweep:     bool = False
    cvd:       float = 0.0
    hvn_near:  bool = False
    runner:    bool = False


# ── Math helpers ──────────────────────────────────────────────────────────────

def _wma(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(arr), np.nan)
    w   = np.arange(1, period + 1, dtype=float)
    ws  = w.sum()
    for i in range(period - 1, len(arr)):
        out[i] = float(np.dot(arr[i - period + 1:i + 1], w) / ws)
    return out


def hma(arr: np.ndarray, period: int) -> np.ndarray:
    half  = max(1, period // 2)
    sqrtn = max(1, int(round(math.sqrt(period))))
    diff  = 2 * _wma(arr, half) - _wma(arr, period)
    return _wma(diff, sqrtn)


def _atr_val(candles: list, period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = [
        max(c["high"] - c["low"],
            abs(c["high"] - candles[i - 1]["close"]),
            abs(c["low"]  - candles[i - 1]["close"]))
        for i, c in enumerate(candles)
        if i > 0
    ]
    return float(np.mean(trs[-period:]))


def _adx(candles: list, period: int = 14) -> float:
    if len(candles) < period * 2:
        return 0.0
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    closes = [c["close"] for c in candles]

    pdm, mdm, trs = [], [], []
    for i in range(1, len(candles)):
        hd = highs[i] - highs[i - 1]
        ld = lows[i - 1] - lows[i]
        pdm.append(hd if hd > ld and hd > 0 else 0.0)
        mdm.append(ld if ld > hd and ld > 0 else 0.0)
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i]  - closes[i - 1])))

    def wilder(arr, p):
        s = [sum(arr[:p])]
        for x in arr[p:]:
            s.append(s[-1] - s[-1] / p + x)
        return s

    atr_w = wilder(trs, period)
    pdi_w = wilder(pdm, period)
    mdi_w = wilder(mdm, period)

    dx = []
    for a, p, m in zip(atr_w, pdi_w, mdi_w):
        if a == 0:
            continue
        pi = 100 * p / a
        mi = 100 * m / a
        dx.append(100 * abs(pi - mi) / (pi + mi) if (pi + mi) > 0 else 0)
    if len(dx) < period:
        return 0.0
    return float(np.mean(dx[-period:]))


def _bb_width_pct(closes: np.ndarray, period: int = 20, lookback: int = 100) -> float:
    if len(closes) < period + lookback:
        return 0.5
    widths = []
    for i in range(lookback):
        idx = len(closes) - lookback + i
        if idx < period:
            continue
        s = closes[idx - period:idx]
        widths.append(float(np.std(s) * 2))
    if not widths:
        return 0.5
    cur  = widths[-1]
    return sum(1 for w in widths if w <= cur) / len(widths)


def _synthetic_cvd(candles: list, length: int = 20) -> float:
    recent = candles[-length:]
    total  = 0.0
    vol_sum = 0.0
    for c in recent:
        rng = c["high"] - c["low"]
        if rng == 0:
            continue
        pos    = (c["close"] - c["low"]) / rng
        delta  = (pos - 0.5) * 2 * c["volume"]
        total  += delta
        vol_sum += c["volume"]
    return total / vol_sum if vol_sum > 0 else 0.0


def _detect_sweep(candles: list, direction: str) -> bool:
    if len(candles) < SWEEP_BARS + 2:
        return False
    lb  = candles[-(SWEEP_BARS + 2):-1]
    cur = candles[-1]
    if direction == "LONG":
        prior_low = min(c["low"] for c in lb)
        return cur["low"] < prior_low and cur["close"] > prior_low
    if direction == "SHORT":
        prior_high = max(c["high"] for c in lb)
        return cur["high"] > prior_high and cur["close"] < prior_high
    return False


# ── Volume Profile / HVN ──────────────────────────────────────────────────────

def _hvn_near_price(candles: list, price: float) -> bool:
    """
    True if current price is within HVN_PROXIMITY% of a High Volume Node.
    Uses VP_LOOKBACK candles, VP_BINS bins, top HVN_THRESHOLD% bins are HVNs.
    """
    recent = candles[-VP_LOOKBACK:]
    if len(recent) < 20:
        return False
    lo = min(c["low"]  for c in recent)
    hi = max(c["high"] for c in recent)
    if hi <= lo:
        return False

    bins = np.zeros(VP_BINS)
    bin_size = (hi - lo) / VP_BINS
    for c in recent:
        center = (c["high"] + c["low"]) / 2
        idx    = int((center - lo) / bin_size)
        idx    = min(idx, VP_BINS - 1)
        bins[idx] += c["volume"]

    threshold = np.percentile(bins, 100 - HVN_THRESHOLD)
    hvn_idxs  = [i for i, v in enumerate(bins) if v >= threshold]
    prox_pct  = HVN_PROXIMITY / 100.0

    for idx in hvn_idxs:
        hvn_price = lo + (idx + 0.5) * bin_size
        if abs(price - hvn_price) / hvn_price <= prox_pct:
            return True
    return False


# ── Regime ────────────────────────────────────────────────────────────────────

def _get_regime(candles: list, closes: np.ndarray) -> str:
    adx_val      = _adx(candles[-60:], ADX_LEN)
    bb_pct       = _bb_width_pct(closes)
    atr_now      = _atr_val(candles, ATR_LEN)
    atr_series   = [_atr_val(candles[max(0, i - ATR_LEN):i + 1], ATR_LEN)
                    for i in range(len(candles) - 50, len(candles))]
    atr_avg      = float(np.mean([x for x in atr_series if x > 0])) if atr_series else atr_now
    atr_rel      = atr_now / atr_avg if atr_avg > 0 else 1.0

    if atr_rel > 2.5:
        return "VOLATILE"
    if adx_val < ADX_TREND and bb_pct < 0.35:
        return "RANGING"
    return "TRENDING"


# ── MTF helper ────────────────────────────────────────────────────────────────

def _mtf_bias(candles_tf: list, direction: str) -> bool:
    """True if higher-TF HMA agrees with direction."""
    if not candles_tf or len(candles_tf) < HMA_SLOW * 3:
        return True  # no data → don't block
    closes = np.array([c["close"] for c in candles_tf])
    h      = hma(closes, HMA_SLOW)
    if math.isnan(h[-1]) or math.isnan(h[-2]):
        return True
    if direction == "LONG":
        return h[-1] > h[-2]
    return h[-1] < h[-2]


# ── Runner detection ──────────────────────────────────────────────────────────

def _is_runner(candles: list, direction: str, atr: float) -> bool:
    """True if last N candles show sustained momentum without deep pullback."""
    if not RUNNER_ENABLED or len(candles) < 10 or atr == 0:
        return False
    last = candles[-8:]
    if direction == "LONG":
        return all(c["close"] > c["open"] for c in last[-4:])
    return all(c["close"] < c["open"] for c in last[-4:])


# ── Score engine ──────────────────────────────────────────────────────────────

def _score(
    regime: str, vol_ok: bool, sweep: bool,
    cvd: float, direction: str,
    adx: float, mtf5_ok: bool, mtf15_ok: bool,
    hvn_near: bool, runner: bool,
    atr_rel: float,
) -> float:
    score = 50.0

    # Regime
    if regime == "TRENDING":
        score += 10
    elif regime == "RANGING":
        score -= 25
    elif regime == "VOLATILE":
        score -= 10

    # ADX strength
    if adx > 30:
        score += 10
    elif adx > ADX_TREND:
        score += 5

    # Volume
    if vol_ok:
        score += 8

    # CVD alignment
    if direction == "LONG" and cvd > 0.1:
        score += min(10, cvd * 30)
    elif direction == "SHORT" and cvd < -0.1:
        score += min(10, abs(cvd) * 30)

    # Sweep (high-quality entry)
    if sweep:
        score += 12

    # MTF confirmation
    if mtf5_ok:
        score += 8
    if mtf15_ok:
        score += 7

    # HVN proximity
    if hvn_near:
        score += HVN_SCORE_BONUS

    # Runner
    if runner:
        score += 8

    # ATR not too extreme
    if 0.5 < atr_rel < 1.8:
        score += 5

    return round(min(100.0, max(0.0, score)), 1)


def _tier(score: float) -> str:
    if score >= 82:
        return "S"
    if score >= 70:
        return "A"
    if score >= 58:
        return "B"
    return "C"


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_signal(
    candles_1m: list,
    candles_5m: list  = None,
    candles_15m: list = None,
    symbol: str       = "UNKNOWN",
) -> Optional[Signal]:
    """
    Returns a Signal object or None if no setup found.
    candles_*: list of dicts with keys open/high/low/close/volume
    """
    MIN_CANDLES = max(HMA_SLOW * 4, VOL_LEN + 2, ATR_LEN + 2, ADX_LEN * 3, BB_LEN + 100)
    if len(candles_1m) < MIN_CANDLES:
        return None

    closes  = np.array([c["close"]  for c in candles_1m], dtype=float)
    volumes = np.array([c["volume"] for c in candles_1m], dtype=float)

    # HMA
    hf = hma(closes, HMA_FAST)
    hs = hma(closes, HMA_SLOW)
    for v in [hf[-1], hf[-2], hs[-1], hs[-2]]:
        if math.isnan(v):
            return None

    # Regime
    regime = _get_regime(candles_1m, closes)
    if regime == "RANGING":
        return None

    # ATR
    atr = _atr_val(candles_1m, ATR_LEN)
    if atr == 0:
        return None
    price_now = closes[-1]
    atr_pct   = (atr / price_now) * 100
    if atr_pct < MIN_ATR_PCT:
        return None  # too quiet, spread will eat profit

    # ATR relative to recent average
    atr_recent = [_atr_val(candles_1m[max(0, i - ATR_LEN):i + 1], ATR_LEN)
                  for i in range(len(candles_1m) - 50, len(candles_1m))]
    atr_avg  = float(np.mean([x for x in atr_recent if x > 0])) or atr
    atr_rel  = atr / atr_avg

    # Volume
    vol_now   = volumes[-1]
    vol_avg   = float(np.mean(volumes[-VOL_LEN - 1:-1])) or 1.0
    vol_ok    = vol_now >= VOL_MULT * vol_avg

    # HMA states
    hf_rising  = hf[-1] > hf[-2]
    hs_rising  = hs[-1] > hs[-2]
    hf_falling = hf[-1] < hf[-2]
    hs_falling = hs[-1] < hs[-2]

    # Price cross
    long_cross  = closes[-2] <= hf[-2] and closes[-1] > hf[-1] and closes[-1] > hs[-1]
    short_cross = closes[-2] >= hf[-2] and closes[-1] < hf[-1] and closes[-1] < hs[-1]

    if not long_cross and not short_cross:
        return None

    direction = "LONG" if long_cross else "SHORT"
    if direction == "SHORT" and not ENABLE_SHORTS:
        return None

    # CVD
    cvd = _synthetic_cvd(candles_1m, CVD_LEN)
    if direction == "LONG"  and cvd < -0.3:
        return None   # strong selling pressure
    if direction == "SHORT" and cvd >  0.3:
        return None   # strong buying pressure

    # HMA alignment
    if direction == "LONG"  and not (hf_rising  and hs_rising):
        return None
    if direction == "SHORT" and not (hf_falling and hs_falling):
        return None

    # MTF
    mtf5_ok  = _mtf_bias(candles_5m,  direction)
    mtf15_ok = _mtf_bias(candles_15m, direction)

    # Sweep
    sweep = _detect_sweep(candles_1m, direction)

    # HVN
    hvn_near = _hvn_near_price(candles_1m, price_now)
    if HVN_ENTRY_FILTER and not hvn_near and not sweep:
        # Require either HVN proximity OR sweep for quality entries
        pass  # still allow if score is high enough — scored below

    # Runner
    runner = _is_runner(candles_1m, direction, atr)

    # ADX
    adx_val = _adx(candles_1m[-60:], ADX_LEN)

    # Score
    score = _score(
        regime, vol_ok, sweep, cvd, direction,
        adx_val, mtf5_ok, mtf15_ok, hvn_near, runner, atr_rel,
    )
    tier = _tier(score)

    # SL / TPs
    sl_dist = SL_ATR_MULT * atr
    if direction == "LONG":
        sl  = price_now - sl_dist
        tp1 = price_now + TP1_RR * sl_dist
        tp2 = price_now + TP2_RR * sl_dist
        tp3 = price_now + TP3_RR * sl_dist
    else:
        sl  = price_now + sl_dist
        tp1 = price_now - TP1_RR * sl_dist
        tp2 = price_now - TP2_RR * sl_dist
        tp3 = price_now - TP3_RR * sl_dist

    rr  = round(TP1_RR, 2)
    reason_parts = [
        f"{direction} | score={score:.0f} [{tier}]",
        f"vol={vol_now/vol_avg:.1f}x",
        f"CVD={cvd:+.2f}",
        f"ADX={adx_val:.0f}",
        f"regime={regime}",
    ]
    if sweep:    reason_parts.append("SWEEP✓")
    if hvn_near: reason_parts.append("HVN✓")
    if runner:   reason_parts.append("RUNNER✓")
    if not mtf5_ok:  reason_parts.append("MTF5✗")
    if not mtf15_ok: reason_parts.append("MTF15✗")

    return Signal(
        symbol    = symbol,
        direction = direction,
        entry     = round(price_now, 8),
        sl        = round(sl, 8),
        tp1       = round(tp1, 8),
        tp2       = round(tp2, 8),
        tp3       = round(tp3, 8),
        atr       = round(atr, 8),
        score     = score,
        reason    = " | ".join(reason_parts),
        regime    = regime,
        tier      = tier,
        vol_ok    = vol_ok,
        sweep     = sweep,
        cvd       = round(cvd, 3),
        hvn_near  = hvn_near,
        runner    = runner,
    )


# ── Backward-compat dict wrapper (for old scanner.py code) ────────────────────

def check_signal(candles_1m: list, candles_5m: list = None) -> dict:
    sig = generate_signal(candles_1m, candles_5m)
    if sig is None:
        return {"signal": None, "reason": "no signal"}
    return {
        "signal":  sig.direction,
        "entry":   sig.entry,
        "sl":      sig.sl,
        "tp":      sig.tp1,
        "sl_atr":  sig.atr,
        "regime":  sig.regime,
        "cvd":     sig.cvd,
        "sweep":   sig.sweep,
        "reason":  sig.reason,
        "_signal_obj": sig,
    }
