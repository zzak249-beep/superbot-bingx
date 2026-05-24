"""
strategy.py — Motor de señales QF×JP v3
Genera señal, score de convicción 0-10, SL y TP
"""
import numpy as np
import pandas as pd
import logging
from dataclasses import dataclass, field
from typing import Optional

import config as C
import indicators as ind

log = logging.getLogger(__name__)


@dataclass
class Signal:
    direction:   str    = "NONE"   # LONG | SHORT | NONE
    level:       str    = "NONE"   # STD | FUEL | SUP
    conviction:  int    = 0        # 0-10
    entry:       float  = 0.0
    sl:          float  = 0.0
    tp:          float  = 0.0
    atr:         float  = 0.0
    sl_source:   float  = 0.0      # último swing para referencia
    reasons:     list   = field(default_factory=list)
    # Indicadores individuales para Telegram
    score:       float  = 0.0
    f_mom:       float  = 0.0
    f_rev:       float  = 0.0
    f_vol:       float  = 0.0
    decay_pct:   float  = 0.0
    bp_drain:    float  = 0.0
    cvd_rising:  bool   = False
    sq_on:       bool   = False
    htf_bull:    bool   = False
    htf_bear:    bool   = False
    ses_label:   str    = "OFF"
    funding:     float  = 0.0


class QFJPStrategy:
    def __init__(self):
        self._last_signals: dict = {}

    # ─────────────────────────────────────────────────────────
    def compute(self,
                df: pd.DataFrame,
                df_htf: pd.DataFrame,
                funding_rate: float = 0.0,
                ob_imbalance: float = 0.5) -> Signal:
        """
        df     : OHLCV 3min  (columnas: open, high, low, close, volume)
        df_htf : OHLCV 15min
        funding_rate : tasa de financiamiento actual
        ob_imbalance : ratio bids/(bids+asks) del libro de órdenes 0-1
        """
        if len(df) < C.LOOKBACK // 2:
            return Signal()

        o = df["open"]
        h = df["high"]
        l = df["low"]
        c = df["close"]
        v = df["volume"]

        # ── L1 · ATR ─────────────────────────────────────────
        atr_s = ind.atr(h, l, c, C.ATR_PERIOD)

        # ── L2 · Score compuesto ──────────────────────────────
        norm_score, fm, fr, fv = ind.composite_score(
            c, v,
            C.MOM_LOOKBACK, C.REV_LOOKBACK, C.VOL_LOOKBACK,
            C.W_MOM, C.W_REV, C.W_VOL,
            C.SIGNAL_SMOOTH, C.DECAY_LEN
        )

        # ── L3 · Decaimiento ─────────────────────────────────
        sig_alive, decay_r = ind.signal_decay(
            norm_score, c, C.DECAY_LEN, C.SIGNAL_SMOOTH, C.DECAY_THR
        )

        # ── L4 · Dark Pool ───────────────────────────────────
        dp_buy, dp_sell, vac_up, vac_dn = ind.dark_pool(
            h, l, c, o, v, atr_s, C.DP_VOL_MULT, C.DP_BASELINE
        )

        # ── L5 · Ejecución ───────────────────────────────────
        exec_ok, bp_drain = ind.exec_cost(h, l, c, C.SPREAD_LEN, C.BP_THRESHOLD)

        # ── L6 · Asimetría ───────────────────────────────────
        asym_bull, asym_bear, rr_bull, rr_bear = ind.momentum_asymmetry(
            h, l, c, o, C.ASYM_LEN, C.ASYM_BULL_RATIO, C.ASYM_BEAR_RATIO
        )

        # ── L7 · Pivotes y TL ────────────────────────────────
        ph = ind.pivot_high(h, C.TL_LEFT, C.TL_RIGHT)
        pl = ind.pivot_low(l, C.SWING_LOW_LEFT, C.SWING_LOW_RIGHT)

        tl_break_long, tl_break_short = ind.trendline_break(
            h, l, c, atr_s, ph, pl,
            C.TL_LOOKBACK, C.TL_LEFT, C.TL_RIGHT, C.TL_BUFFER
        )

        # ── L8 · Swing ───────────────────────────────────────
        sell_ex, buy_ex, last_sl_s, last_sh_s = ind.swing_analysis(
            pl, ph, C.SWING_WINDOW, C.HL_COUNT_MIN, C.LH_COUNT_MIN
        )

        # ── L9 · FVG ─────────────────────────────────────────
        _, _, in_bull_fvg, in_bear_fvg = ind.fair_value_gaps(
            h, l, atr_s, C.FVG_MIN_ATR, C.FVG_MAX_BARS
        )

        # ── L10 · Order Blocks ───────────────────────────────
        _, _, in_bull_ob, in_bear_ob = ind.order_blocks(
            h, l, c, o, atr_s, C.OB_IMPULSE_ATR, C.OB_MAX_BARS
        )

        # ── L11 · CVD ────────────────────────────────────────
        cvd_rising, cvd_bull_div, cvd_bear_div = ind.cvd_delta(
            h, l, c, v, C.CVD_EMA_LEN, C.CVD_DIV_LEN
        )

        # ── L12 · Squeeze ────────────────────────────────────
        sq_on, sq_bull, sq_bear = ind.squeeze_momentum(
            h, l, c, atr_s, C.SQ_LEN, C.SQ_BB_MULT, C.SQ_KC_MULT
        )

        # ── HTF Régimen ──────────────────────────────────────
        htf_bull, htf_bear = self._htf_regime(df_htf)

        # ── VWAP ─────────────────────────────────────────────
        vwap = ind.vwap_rolling(h, l, c, v, 480)
        above_vwap = c > vwap

        # ── Sesión UTC ───────────────────────────────────────
        ses_label = self._session_label(df.index[-1])

        # ── Valores en la última vela ─────────────────────────
        i = -1  # última vela completa

        def b(s):
            try:
                return bool(s.iloc[i])
            except Exception:
                return False

        def f(s):
            try:
                v2 = float(s.iloc[i])
                return 0.0 if np.isnan(v2) else v2
            except Exception:
                return 0.0

        ns   = f(norm_score)
        alive= b(sig_alive)
        eok  = b(exec_ok)
        hb   = htf_bull
        he   = htf_bear
        ab   = b(asym_bull)
        ae   = b(asym_bear)
        se   = b(sell_ex)
        be   = b(buy_ex)
        tlbl = b(tl_break_long)
        tbsh = b(tl_break_short)
        dpb  = b(dp_buy)
        dps  = b(dp_sell)
        ibfvg= b(in_bull_fvg)
        iefvg= b(in_bear_fvg)
        ibob = b(in_bull_ob)
        ieob = b(in_bear_ob)
        cvdr = b(cvd_rising)
        cbdiv= b(cvd_bull_div)
        csdiv= b(cvd_bear_div)
        sqb  = b(sq_bull)
        sqbe = b(sq_bear)
        sqon = b(sq_on)
        avwap= b(above_vwap)

        atr_v = f(atr_s)
        price  = f(c)
        sl_lng = f(last_sl_s)
        sl_sht = f(last_sh_s)
        decay  = f(decay_r)
        bp     = f(bp_drain)
        fm_v   = f(fm)
        fr_v   = f(fr)
        fv_v   = f(fv)

        # ── Filtro funding ────────────────────────────────────
        funding_ok_long  = funding_rate <= C.MAX_FUNDING_LONG
        funding_ok_short = funding_rate >= C.MIN_FUNDING_SHORT

        # ── Filtro orderbook ──────────────────────────────────
        # ob_imbalance > 0.55 = presión compradora
        ob_bull = ob_imbalance >= 0.55
        ob_bear = ob_imbalance <= 0.45

        # ── SEÑALES ───────────────────────────────────────────
        long_std = (ns > 0.15 and alive and eok and hb and ab and se
                    and funding_ok_long)
        long_fuel= long_std and (tlbl or sqb or ((ibfvg or ibob) and cvdr))
        long_sup = long_fuel and (dpb or cbdiv or (ob_bull and avwap))

        short_std = (ns < -0.15 and alive and eok and he and ae and be
                     and funding_ok_short)
        short_fuel= short_std and (tbsh or sqbe or ((iefvg or ieob) and not cvdr))
        short_sup = short_fuel and (dps or csdiv or (ob_bear and not avwap))

        # ── Score convicción 0-10 ─────────────────────────────
        long_conv = sum([
            ns > 0.15, alive, eok, hb, ab, se,
            tlbl, dpb, cvdr,
            (sqb or ibfvg or ibob),
        ])
        short_conv = sum([
            ns < -0.15, alive, eok, he, ae, be,
            tbsh, dps, not cvdr,
            (sqbe or iefvg or ieob),
        ])

        # ── Construir señal ───────────────────────────────────
        sig = Signal()
        sig.atr       = atr_v
        sig.score     = ns
        sig.f_mom     = fm_v
        sig.f_rev     = fr_v
        sig.f_vol     = fv_v
        sig.decay_pct = decay * 100
        sig.bp_drain  = bp
        sig.cvd_rising= cvdr
        sig.sq_on     = sqon
        sig.htf_bull  = hb
        sig.htf_bear  = he
        sig.ses_label = ses_label
        sig.funding   = funding_rate

        if long_std and long_conv >= C.MIN_CONVICTION:
            sig.direction = "LONG"
            sig.level     = "SUP" if long_sup else "FUEL" if long_fuel else "STD"
            sig.conviction= long_conv
            sig.entry     = price
            sl_ref        = sl_lng if not np.isnan(sl_lng) else price - atr_v * C.SL_ATR_MULT
            sig.sl        = min(sl_ref, price - atr_v * C.SL_ATR_MULT)
            sig.sl_source = sl_lng
            rr            = C.TP_RR_SUP if long_sup else C.TP_RR_FUEL if long_fuel else C.TP_RR_STD
            risk          = price - sig.sl
            sig.tp        = price + risk * rr
            sig.reasons   = self._reasons_long(
                ns, alive, eok, hb, ab, se, tlbl, sqb, dpb,
                ibfvg, ibob, cvdr, cbdiv, ob_bull, avwap, funding_rate
            )

        elif short_std and short_conv >= C.MIN_CONVICTION:
            sig.direction = "SHORT"
            sig.level     = "SUP" if short_sup else "FUEL" if short_fuel else "STD"
            sig.conviction= short_conv
            sig.entry     = price
            sl_ref        = sl_sht if not np.isnan(sl_sht) else price + atr_v * C.SL_ATR_MULT
            sig.sl        = max(sl_ref, price + atr_v * C.SL_ATR_MULT)
            sig.sl_source = sl_sht
            rr            = C.TP_RR_SUP if short_sup else C.TP_RR_FUEL if short_fuel else C.TP_RR_STD
            risk          = sig.sl - price
            sig.tp        = price - risk * rr
            sig.reasons   = self._reasons_short(
                ns, alive, eok, he, ae, be, tbsh, sqbe, dps,
                iefvg, ieob, not cvdr, csdiv, ob_bear, not avwap, funding_rate
            )

        if sig.direction != "NONE":
            log.info(f"SEÑAL {sig.direction} {sig.level} | conv={sig.conviction}/10 "
                     f"| entry={sig.entry:.4f} sl={sig.sl:.4f} tp={sig.tp:.4f}")

        return sig

    # ─────────────────────────────────────────────────────────
    def _htf_regime(self, df_htf: pd.DataFrame):
        if df_htf is None or len(df_htf) < C.HTF_SLOW + 2:
            return False, False
        c = df_htf["close"]
        fast = c.ewm(span=C.HTF_FAST, adjust=False).mean()
        slow = c.ewm(span=C.HTF_SLOW, adjust=False).mean()
        htf_bull = bool(fast.iloc[-1] > slow.iloc[-1])
        htf_bear = bool(fast.iloc[-1] < slow.iloc[-1])
        return htf_bull, htf_bear

    def _session_label(self, ts) -> str:
        try:
            h = pd.Timestamp(ts, tz="UTC").hour
            in_asia   = 0 <= h < 8
            in_london = 7 <= h < 16
            in_ny     = 13 <= h < 22
            if in_ny:     return "NY 🟢"
            if in_london: return "LDN 🔴"
            if in_asia:   return "ASIA 🔵"
            return "OFF"
        except Exception:
            return "—"

    def _reasons_long(self, ns, alive, eok, hb, ab, se,
                      tlbl, sqb, dpb, ibfvg, ibob, cvdr, cbdiv,
                      ob_bull, avwap, funding):
        r = []
        if ns > 0.15:  r.append(f"✅ Score {ns:+.2f}")
        if alive:       r.append("✅ Señal viva")
        if eok:         r.append("✅ Spread ok")
        if hb:          r.append("✅ HTF alcista")
        if ab:          r.append("✅ Asimetría bull")
        if se:          r.append("✅ Vendedores agotados")
        if tlbl:        r.append("🔥 Ruptura TL bajista")
        if sqb:         r.append("💥 Squeeze liberado ↑")
        if dpb:         r.append("🏦 Dark Pool compra")
        if ibfvg:       r.append("📦 En FVG alcista")
        if ibob:        r.append("🟩 Retest OB alcista")
        if cvdr:        r.append("📈 CVD rising")
        if cbdiv:       r.append("🔍 Divergencia CVD acum")
        if ob_bull:     r.append("📊 Orderbook presión ↑")
        if avwap:       r.append("📍 Sobre VWAP")
        if funding < 0: r.append(f"💰 Funding negativo {funding*100:.3f}%")
        return r

    def _reasons_short(self, ns, alive, eok, he, ae, be,
                       tbsh, sqbe, dps, iefvg, ieob, cvdf, csdiv,
                       ob_bear, bvwap, funding):
        r = []
        if ns < -0.15: r.append(f"✅ Score {ns:+.2f}")
        if alive:       r.append("✅ Señal viva")
        if eok:         r.append("✅ Spread ok")
        if he:          r.append("✅ HTF bajista")
        if ae:          r.append("✅ Asimetría bear")
        if be:          r.append("✅ Compradores agotados")
        if tbsh:        r.append("🔥 Ruptura TL alcista")
        if sqbe:        r.append("💥 Squeeze liberado ↓")
        if dps:         r.append("🏦 Dark Pool venta")
        if iefvg:       r.append("📦 En FVG bajista")
        if ieob:        r.append("🟥 Retest OB bajista")
        if cvdf:        r.append("📉 CVD falling")
        if csdiv:       r.append("🔍 Divergencia CVD dist")
        if ob_bear:     r.append("📊 Orderbook presión ↓")
        if bvwap:       r.append("📍 Bajo VWAP")
        if funding > 0: r.append(f"💰 Funding positivo {funding*100:.3f}%")
        return r
