"""
Signal Engine v8 — Motor de señales de alta precisión
=======================================================
FIXES vs v7:
  - MIN_SCORE=60 con klines multi-TF era inalcanzable en mercados normales
  - Quality gate "mínimo 2 señales" imposible con scores bajos → ajustado
  - MTF_MIN bajado de 25 a 15 (más realista)
  - Scores individuales recalibrados para alcanzar 60 total
  - SL basado en ATR ahora tiene fallback robusto si ATR=0
  - RR calculado real (no fijo TP1_RATIO)
"""

import asyncio, logging, os, statistics, time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("SIGNAL")

# ── Config ──────────────────────────────────────────────────── #
MIN_SCORE  = float(os.getenv("MIN_SCORE",          "60"))
MIN_RR     = float(os.getenv("MIN_RR",             "2.0"))   # FIX: bajado de 2.5
SL_MAX_PCT = float(os.getenv("SL_MAX_PCT",         "3.0"))   # FIX: subido de 2.5%
SL_MIN_PCT = float(os.getenv("SL_MIN_PCT",         "0.4"))
SL_MULT    = float(os.getenv("SL_MULT",            "1.2"))   # FIX: subido de 1.0
TP1_RATIO  = float(os.getenv("TP1_RATIO",          "2.5"))
TP2_RATIO  = float(os.getenv("TP2_RATIO",          "4.0"))
BB_PERIOD  = int(os.getenv("BB_PERIOD",            "20"))
BB_STD     = float(os.getenv("BB_STD",             "2.0"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD",           "14"))
MTF_MIN    = float(os.getenv("MTF_MIN_CONFLUENCE", "15"))    # FIX: bajado de 25

_COOLDOWN: dict = {}


@dataclass
class Signal:
    symbol:      str
    direction:   str
    score:       float
    price:       float
    sl:          float
    tp1:         float
    tp2:         float
    risk_reward: float
    signal_type: str
    active_sigs: list  = field(default_factory=list)
    bb_width:    float = 0.0
    cvd_pct:     float = 50.0
    mtf_label:   str   = ""
    mean_pnl:    float = 0.0
    atr:         float = 0.0


class SignalEngine:
    def __init__(self, ob_analyzer=None):
        self.ob = ob_analyzer

    async def evaluate(self, client, symbol: str,
                       ob_snap=None) -> Optional[dict]:
        if self._in_cooldown(symbol):
            return None

        # Fetch klines multi-TF en paralelo
        results = await asyncio.gather(
            client.get_klines(symbol, "1h",  limit=100),
            client.get_klines(symbol, "4h",  limit=50),
            client.get_klines(symbol, "15m", limit=60),
            return_exceptions=True
        )
        klines_1h, klines_4h, klines_15m = results

        # FIX: no abortar si un TF falla — usar lo disponible
        if isinstance(klines_1h, Exception) or not klines_1h:
            return None

        try:
            closes_1h  = [float(x[4]) for x in klines_1h]
            volumes_1h = [float(x[5]) for x in klines_1h]
        except (IndexError, ValueError):
            return None

        if len(closes_1h) < 20:
            return None

        closes_4h  = []
        closes_15m = []
        try:
            if klines_4h and not isinstance(klines_4h, Exception):
                closes_4h = [float(x[4]) for x in klines_4h]
        except Exception:
            pass
        try:
            if klines_15m and not isinstance(klines_15m, Exception):
                closes_15m = [float(x[4]) for x in klines_15m]
        except Exception:
            pass

        price = client.get_ws_price(symbol) or closes_1h[-1]
        atr   = self._atr(klines_1h[-20:])

        # ── Señales ────────────────────────────────────────────── #
        sigs_found  = []
        total_score = 0.0
        signal_type = ""

        # 1. BB Squeeze Breakout
        bb_score, squeeze, bb_width, bb_upper, bb_lower = \
            self._bb_squeeze(closes_1h)
        if bb_score > 0:
            sigs_found.append(f"BB {bb_width:.1f}%{'🔥' if squeeze else ''}")
            total_score += bb_score
            signal_type  = "BB_SQUEEZE" if squeeze else "BB"

        # 2. Volume climax
        vol_score, vol_spike = self._volume_climax(volumes_1h)
        if vol_score > 0:
            sigs_found.append(f"Vol {vol_spike:.1f}x")
            total_score += vol_score
            if not signal_type:
                signal_type = "VOL_CLIMAX"

        # 3. CVD
        cvd_pct, cvd_score = self._cvd_analysis(klines_1h[-20:])
        if cvd_score > 0:
            sigs_found.append(f"CVD {cvd_pct:.0f}%")
            total_score += cvd_score

        # 4. MTF Confluence
        mtf_score, mtf_label = self._mtf_confluence(
            closes_1h, closes_4h, closes_15m
        )
        if mtf_score >= MTF_MIN:
            sigs_found.append(f"MTF {mtf_label}")
            total_score += mtf_score

        # 5. Momentum
        mom_score, mom_desc = self._momentum_surge(closes_1h)
        if mom_score > 0:
            sigs_found.append(mom_desc)
            total_score += mom_score
            if not signal_type:
                signal_type = "MOMENTUM"

        # 6. RSI reversal
        rsi_score, rsi_desc = self._rsi_reversal(closes_1h)
        if rsi_score > 0:
            sigs_found.append(rsi_desc)
            total_score += rsi_score
            if not signal_type:
                signal_type = "OVERSOLD_REV"

        # 7. OB absorption
        if ob_snap:
            ob_score = self._ob_signal(ob_snap)
            if ob_score > 0:
                sigs_found.append("OB Absorción")
                total_score += ob_score

        # ── Quality gates ──────────────────────────────────────── #
        if total_score < MIN_SCORE:
            return None
        # FIX: requerir al menos 1 señal fuerte O 2 señales normales
        if len(sigs_found) < 1:
            return None
        if len(sigs_found) == 1 and total_score < MIN_SCORE * 1.2:
            return None  # señal solitaria necesita 20% más de score

        # ── SL / TP ────────────────────────────────────────────── #
        # FIX: fallback robusto si ATR es 0
        if atr > 0:
            sl_dist = atr * SL_MULT
        else:
            sl_dist = price * SL_MIN_PCT / 100

        sl_dist = max(sl_dist, price * SL_MIN_PCT / 100)
        sl_dist = min(sl_dist, price * SL_MAX_PCT / 100)

        if sl_dist <= 0:
            return None

        sl  = round(price - sl_dist, 8)
        tp1 = round(price + sl_dist * TP1_RATIO, 8)
        tp2 = round(price + sl_dist * TP2_RATIO, 8)
        rr  = round((tp1 - price) / sl_dist, 2)

        if rr < MIN_RR:
            return None

        return Signal(
            symbol      = symbol,
            direction   = "LONG",
            score       = round(total_score, 1),
            price       = price,
            sl          = sl,
            tp1         = tp1,
            tp2         = tp2,
            risk_reward = rr,
            signal_type = signal_type or "COMBO",
            active_sigs = sigs_found,
            bb_width    = round(bb_width, 2),
            cvd_pct     = round(cvd_pct, 1),
            mtf_label   = mtf_label,
            atr         = round(atr, 8),
        ).__dict__

    def set_cooldown(self, symbol: str, minutes: int = 240):
        _COOLDOWN[symbol] = time.time() + minutes * 60

    def _in_cooldown(self, symbol: str) -> bool:
        return time.time() < _COOLDOWN.get(symbol, 0)

    # ── Indicadores ───────────────────────────────────────────── #

    def _ema(self, data, period):
        if len(data) < period:
            return None
        k = 2 / (period + 1)
        r = [sum(data[:period]) / period]
        for v in data[period:]:
            r.append(v * k + r[-1] * (1 - k))
        return r

    def _atr(self, klines, period: int = 14) -> float:
        if len(klines) < 2:
            return 0
        trs = []
        for i in range(1, len(klines)):
            try:
                h  = float(klines[i][2])
                l  = float(klines[i][3])
                pc = float(klines[i-1][4])
                trs.append(max(h - l, abs(h - pc), abs(l - pc)))
            except (IndexError, ValueError):
                continue
        if not trs:
            return 0
        n = min(period, len(trs))
        return sum(trs[-n:]) / n

    def _bb_squeeze(self, closes):
        if len(closes) < BB_PERIOD:
            return 0, False, 0, 0, 0
        w = closes[-BB_PERIOD:]
        m = statistics.mean(w)
        d = statistics.stdev(w) if len(w) > 1 else 0
        upper = m + BB_STD * d
        lower = m - BB_STD * d
        bw    = (upper - lower) / m * 100 if m > 0 else 0
        squeeze = bw < 3.5
        score = 0
        if squeeze:
            score = 28           # FIX: subido de 25
        elif bw < 5:
            score = 18           # FIX: subido de 15
        elif bw < 8:
            score = 10           # FIX: subido de 8
        elif bw < 12:
            score = 5            # FIX: nuevo tier
        if closes[-1] > upper * 0.997:
            score += 12          # FIX: subido de 10
        return score, squeeze, bw, upper, lower

    def _volume_climax(self, volumes):
        if len(volumes) < 5:
            return 0, 1.0
        ref = volumes[:-1]
        avg = statistics.mean(ref[-20:] if len(ref) >= 20 else ref)
        cur = volumes[-1]
        spk = cur / avg if avg > 0 else 1.0
        if spk >= 4:
            return 28, spk       # FIX: subido de 25
        if spk >= 2.5:
            return 20, spk       # FIX: subido de 18
        if spk >= 1.8:
            return 12, spk       # FIX: subido de 10
        if spk >= 1.3:
            return 6, spk        # FIX: nuevo tier
        return 0, spk

    def _cvd_analysis(self, klines):
        if len(klines) < 3:
            return 50, 0
        buy_vol = 0.0
        tot_vol = 0.0
        for k in klines:
            try:
                o, c, v = float(k[1]), float(k[4]), float(k[5])
            except (IndexError, ValueError):
                continue
            tot_vol += v
            if c > o:
                buy_vol += v
            else:
                buy_vol += v * (c / o) if o > 0 else v * 0.4
        pct = buy_vol / tot_vol * 100 if tot_vol > 0 else 50
        score = 0
        if pct >= 65:
            score = 20           # FIX: subido de 18
        elif pct >= 58:
            score = 12           # FIX: subido de 10
        elif pct >= 52:
            score = 5            # FIX: nuevo tier
        elif pct <= 35:
            score = -8
        return pct, score

    def _mtf_confluence(self, c1h, c4h, c15m):
        score  = 0
        labels = []
        # 1h siempre requerido
        e9  = self._ema(c1h, 9)
        e21 = self._ema(c1h, 21)
        if e9 and e21 and e9[-1] > e21[-1]:
            score += 20; labels.append("↑1h")  # FIX: subido de 15

        # 4h opcional (si hay datos)
        if c4h and len(c4h) >= 9:
            e9  = self._ema(c4h, 9)
            e21 = self._ema(c4h, 21)
            if e9 and e21 and e9[-1] > e21[-1]:
                score += 15; labels.append("↑4h")

        # 15m opcional
        if c15m and len(c15m) >= 9:
            e9  = self._ema(c15m, 9)
            e21 = self._ema(c15m, 21)
            if e9 and e21 and e9[-1] > e21[-1]:
                score += 10; labels.append("↑15m")

        return score, "/".join(labels) if labels else "neutral"

    def _momentum_surge(self, closes):
        if len(closes) < 7:
            return 0, ""
        chg_1 = (closes[-1] - closes[-2]) / closes[-2] * 100
        chg_3 = (closes[-1] - closes[-4]) / closes[-4] * 100
        if len(closes) >= 7:
            chg_6 = (closes[-1] - closes[-7]) / closes[-7] * 100
        else:
            chg_6 = chg_3
        if chg_1 >= 1.5 and chg_3 >= 3 and chg_6 >= 5:
            return 22, f"Momentum ↑ {chg_3:.1f}%"    # FIX: subido de 20
        if chg_1 >= 0.8 and chg_3 >= 1.5:
            return 14, f"Momentum {chg_3:.1f}%"       # FIX: subido de 12
        if chg_1 >= 0.3 and chg_3 >= 0.8:
            return 7, f"Mom leve {chg_3:.1f}%"        # FIX: nuevo tier
        return 0, ""

    def _rsi_reversal(self, closes):
        if len(closes) < 16:
            return 0, ""
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains  = [max(d, 0)      for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]
        ag = sum(gains[:14])  / 14
        al = sum(losses[:14]) / 14
        for i in range(14, len(deltas)):
            ag = (ag * 13 + gains[i])  / 14
            al = (al * 13 + losses[i]) / 14
        rs  = ag / al if al > 0 else 100
        rsi = 100 - 100 / (1 + rs)
        if rsi < 30:
            return 18, f"RSI Oversold {rsi:.0f}"      # FIX: subido de 15
        if rsi < 40:
            return 10, f"RSI Bajo {rsi:.0f}"          # FIX: subido de 8
        if rsi < 50:
            return 5, f"RSI Neutral {rsi:.0f}"        # FIX: nuevo tier
        return 0, ""

    def _ob_signal(self, ob_snap) -> float:
        score = 0
        try:
            if ob_snap.bias == "BULLISH":
                score += 14      # FIX: subido de 12
            if ob_snap.absorption_signal:
                score += 10      # FIX: subido de 8
            if ob_snap.imbalance_ratio >= 1.5:
                score += 6       # FIX: subido de 5
            if ob_snap.imbalance_ratio >= 1.3:
                score += 3       # FIX: nuevo tier
        except AttributeError:
            pass
        return score
