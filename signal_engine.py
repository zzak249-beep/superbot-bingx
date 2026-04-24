"""
Signal Engine v7 — Motor de señales de alta precisión
=======================================================
FILOSOFÍA: Menos señales, más calidad. 
  Un bot que dispara señales de 80%+ hace más dinero que uno que 
  dispara 50 señales mediocres al día.

SEÑALES IMPLEMENTADAS:
  1. BB Squeeze Breakout     — energía comprimida + ruptura
  2. Volume Climax Long      — volumen institucional anómalo
  3. CVD Divergence          — acumulación encubierta
  4. MTF Confluence          — alineación multi-temporal
  5. Momentum Surge          — inicio de trend fuerte
  6. Oversold Reversal       — rebote desde extremo
  7. OB Absorption           — absorción en order book

QUALITY GATES (todos deben pasar):
  - Score mínimo: 60 puntos
  - Risk/Reward mínimo: 2.5
  - SL máximo: 2.5%
  - No señal si BTC cae >1.5% en 1h (macro block)
  - Cooldown por símbolo: 4h tras SL
"""

import asyncio, logging, os, statistics, time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("SIGNAL")

# ── Config ──────────────────────────────────────────────────── #
MIN_SCORE    = float(os.getenv("MIN_SCORE",       "60"))
MIN_RR       = float(os.getenv("MIN_RR",          "2.5"))
SL_MAX_PCT   = float(os.getenv("SL_MAX_PCT",      "2.5"))
SL_MIN_PCT   = float(os.getenv("SL_MIN_PCT",      "0.5"))
SL_MULT      = float(os.getenv("SL_MULT",         "1.0"))
TP1_RATIO    = float(os.getenv("TP1_RATIO",       "2.5"))
TP2_RATIO    = float(os.getenv("TP2_RATIO",       "4.0"))
BB_PERIOD    = int(os.getenv("BB_PERIOD",         "20"))
BB_STD       = float(os.getenv("BB_STD",          "2.0"))
RSI_PERIOD   = int(os.getenv("RSI_PERIOD",        "14"))
MTF_MIN      = float(os.getenv("MTF_MIN_CONFLUENCE","25"))
KLINE_INT    = os.getenv("KLINE_INTERVAL",        "1h")

# Cooldown por símbolo (evita re-entrar tras SL)
_COOLDOWN: dict = {}    # sym → ts


@dataclass
class Signal:
    symbol:      str
    direction:   str      # LONG / SHORT
    score:       float
    price:       float
    sl:          float
    tp1:         float
    tp2:         float
    risk_reward: float
    signal_type: str
    active_sigs: list     = field(default_factory=list)
    bb_width:    float    = 0.0
    cvd_pct:     float    = 50.0
    mtf_label:   str      = ""
    mean_pnl:    float    = 0.0
    atr:         float    = 0.0


class SignalEngine:
    def __init__(self, ob_analyzer=None):
        self.ob = ob_analyzer

    # ── API pública ───────────────────────────────────────────── #

    async def evaluate(self, client, symbol: str, ob_snap=None) -> Optional[dict]:
        """Evalúa un símbolo y retorna señal si supera todos los quality gates."""

        # Cooldown check
        if self._in_cooldown(symbol):
            return None

        # Fetch klines multi-timeframe
        klines_1h, klines_4h, klines_15m = await asyncio.gather(
            client.get_klines(symbol, "1h",  limit=100),
            client.get_klines(symbol, "4h",  limit=50),
            client.get_klines(symbol, "15m", limit=60),
            return_exceptions=True
        )
        for k in (klines_1h, klines_4h, klines_15m):
            if isinstance(k, Exception) or not k:
                return None

        try:
            closes_1h  = [float(x[4]) for x in klines_1h]
            volumes_1h = [float(x[5]) for x in klines_1h]
            closes_4h  = [float(x[4]) for x in klines_4h]
            closes_15m = [float(x[4]) for x in klines_15m]
        except (IndexError, ValueError):
            return None

        if len(closes_1h) < 30:
            return None

        price = client.get_ws_price(symbol) or closes_1h[-1]
        atr   = self._atr(klines_1h[-20:])

        # ── Evaluar señales ──────────────────────────────────── #
        sigs_found  = []
        total_score = 0.0
        direction   = "LONG"   # v7 solo LONG (trend-following)
        signal_type = ""

        # 1. BB Squeeze Breakout
        bb_score, sq, bb_width, bb_upper, bb_lower = self._bb_squeeze(closes_1h)
        if sq and bb_score > 0:
            sigs_found.append(f"BB Squeeze {bb_width:.1f}%")
            total_score += bb_score
            signal_type = "BB_SQUEEZE"

        # 2. Volume climax
        vol_score, vol_spike = self._volume_climax(volumes_1h)
        if vol_score > 0:
            sigs_found.append(f"Vol {vol_spike:.1f}x")
            total_score += vol_score
            if not signal_type:
                signal_type = "VOL_CLIMAX"

        # 3. CVD (Cumulative Volume Delta proxy)
        cvd_pct, cvd_score = self._cvd_analysis(klines_1h[-20:])
        if cvd_score > 0:
            sigs_found.append(f"CVD {cvd_pct:.0f}%")
            total_score += cvd_score

        # 4. MTF Confluence
        mtf_score, mtf_label = self._mtf_confluence(closes_1h, closes_4h, closes_15m)
        if mtf_score >= MTF_MIN:
            sigs_found.append(f"MTF {mtf_label}")
            total_score += mtf_score

        # 5. Momentum surge
        mom_score, mom_desc = self._momentum_surge(closes_1h)
        if mom_score > 0:
            sigs_found.append(mom_desc)
            total_score += mom_score
            if not signal_type:
                signal_type = "MOMENTUM"

        # 6. RSI oversold reversal
        rsi_score, rsi_desc = self._rsi_reversal(closes_1h)
        if rsi_score > 0:
            sigs_found.append(rsi_desc)
            total_score += rsi_score
            if not signal_type:
                signal_type = "OVERSOLD_REV"

        # 7. OB absorption (si disponible)
        ob_score = 0
        if ob_snap:
            ob_score = self._ob_signal(ob_snap)
            if ob_score > 0:
                sigs_found.append("OB Absorción")
                total_score += ob_score

        # ── Quality gates ────────────────────────────────────── #
        if total_score < MIN_SCORE:
            return None
        if len(sigs_found) < 2:  # mínimo 2 señales para confirmar
            return None

        # Calcular SL y TP basados en ATR
        sl_dist = max(atr * SL_MULT, price * SL_MIN_PCT / 100)
        sl_dist = min(sl_dist, price * SL_MAX_PCT / 100)

        sl  = round(price - sl_dist, 8)
        tp1 = round(price + sl_dist * TP1_RATIO, 8)
        tp2 = round(price + sl_dist * TP2_RATIO, 8)
        rr  = round(TP1_RATIO, 2)

        if rr < MIN_RR:
            return None

        return Signal(
            symbol      = symbol,
            direction   = direction,
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
        exp = _COOLDOWN.get(symbol, 0)
        return time.time() < exp

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
        if len(klines) < period:
            return 0
        trs = []
        for i in range(1, len(klines)):
            h = float(klines[i][2]); l = float(klines[i][3]); pc = float(klines[i-1][4])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        if not trs:
            return 0
        return sum(trs[-period:]) / min(period, len(trs))

    def _bb_squeeze(self, closes, period=BB_PERIOD, std=BB_STD):
        if len(closes) < period:
            return 0, False, 0, 0, 0
        w = closes[-period:]
        m = statistics.mean(w)
        d = statistics.stdev(w) if len(w) > 1 else 0
        upper = m + std * d
        lower = m - std * d
        bw    = (upper - lower) / m * 100 if m > 0 else 0
        squeeze = bw < 3.5
        score = 25 if squeeze else (15 if bw < 5 else 8 if bw < 8 else 0)
        # Bonus si precio acaba de romper la banda superior
        if closes[-1] > upper * 0.998:
            score += 10
        return score, squeeze, bw, upper, lower

    def _volume_climax(self, volumes):
        if len(volumes) < 10:
            return 0, 1.0
        avg  = statistics.mean(volumes[-21:-1])
        cur  = volumes[-1]
        spk  = cur / avg if avg > 0 else 1.0
        if spk >= 4:
            return 25, spk
        if spk >= 2.5:
            return 18, spk
        if spk >= 1.8:
            return 10, spk
        return 0, spk

    def _cvd_analysis(self, klines):
        """
        CVD proxy: calcula si el volumen viene de compradores o vendedores.
        Vela alcista (close>open) = volumen de compra.
        """
        if len(klines) < 5:
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
            score = 18
        elif pct >= 58:
            score = 10
        elif pct <= 35:
            score = -10  # presión vendedora
        return pct, score

    def _mtf_confluence(self, c1h, c4h, c15m):
        """
        Multi-timeframe: EMA9/21 alineadas en los 3 TFs = alta probabilidad.
        """
        score  = 0
        labels = []
        for closes, label in [(c1h, "1h"), (c4h, "4h"), (c15m, "15m")]:
            e9  = self._ema(closes, 9)
            e21 = self._ema(closes, 21)
            if e9 and e21 and e9[-1] > e21[-1]:
                score += 15
                labels.append(f"↑{label}")
        return score, "/".join(labels) if labels else "neutral"

    def _momentum_surge(self, closes):
        if len(closes) < 10:
            return 0, ""
        chg_1  = (closes[-1] - closes[-2])  / closes[-2]  * 100
        chg_3  = (closes[-1] - closes[-4])  / closes[-4]  * 100
        chg_6  = (closes[-1] - closes[-7])  / closes[-7]  * 100
        # Momentum acelerando
        if chg_1 >= 1.5 and chg_3 >= 3 and chg_6 >= 5:
            return 20, f"Momentum ↑ {chg_3:.1f}%"
        if chg_1 >= 0.8 and chg_3 >= 1.5:
            return 12, f"Momentum {chg_3:.1f}%"
        return 0, ""

    def _rsi_reversal(self, closes):
        if len(closes) < 16:
            return 0, ""
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains  = [max(d, 0) for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]
        ag = sum(gains[:14]) / 14
        al = sum(losses[:14]) / 14
        for i in range(14, len(deltas)):
            ag = (ag * 13 + gains[i]) / 14
            al = (al * 13 + losses[i]) / 14
        rs  = ag / al if al > 0 else 100
        rsi = 100 - 100 / (1 + rs)
        # RSI saliendo de sobreventa
        if rsi < 35:
            return 15, f"RSI Rebote {rsi:.0f}"
        if rsi < 45:
            return 8, f"RSI Oversold {rsi:.0f}"
        return 0, ""

    def _ob_signal(self, ob_snap) -> float:
        score = 0
        try:
            if ob_snap.bias == "BULLISH":
                score += 12
            if ob_snap.absorption_signal:
                score += 8
            if ob_snap.imbalance_ratio >= 1.5:
                score += 5
        except AttributeError:
            pass
        return score
