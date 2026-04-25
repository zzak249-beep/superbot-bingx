"""
strategy/engine.py — Motor de señales multi-timeframe con confluence scoring.

Sistema de puntuación 0-100:
  ┌─────────────────────────────────────────────┬────────┐
  │ Componente                                  │ Pts    │
  ├─────────────────────────────────────────────┼────────┤
  │ [1]  HTF  HMA dirección alineada            │  20    │
  │ [2]  HTF  Régimen de mercado (trending)     │  15    │
  │ [3]  MTF  HMA cruce / dirección             │  15    │
  │ [4]  MTF  ADX fuerza de tendencia           │  10    │
  │ [5]  LTF  Trigger (HMA cruce + vela)        │  15    │
  │ [6]  CVD  Divergencia / slope alineado      │  10    │
  │ [7]  OFI  Orderflow imbalance confirmado    │   8    │
  │ [8]  VOL  Volumen sobre media               │   7    │
  │ [BONUS] Liquidity sweep alineado            │  +5    │
  │ [BONUS] RSI zona favorable                  │  +3    │
  │ [BONUS] Precio sobre/bajo VWAP              │  +2    │
  └─────────────────────────────────────────────┴────────┘

  Mínimo para operar: 65 puntos
  ATR SL: 1.5 × ATR (LTF)
  TP1: 1.5 R   (50% posición)
  TP2: 2.5 R   (50% restante + trailing)
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from config import config
from strategy.indicators import (
    # Moving averages
    hma, hma_direction, hma_crossover,
    # Volatility
    atr_now, atr_pct_now, atr_percentile, bollinger, bb_pct_b,
    # Momentum
    rsi_now, stoch_rsi, macd_vals,
    # Trend
    adx_vals, efficiency_ratio_now, market_regime,
    REGIME_TRENDING, REGIME_RANGING, REGIME_VOLATILE,
    # Volume / OFI
    synthetic_cvd, cvd_slope, cvd_divergence,
    orderflow_imbalance, volume_ratio_now, volume_trend,
    # SMC
    liquidity_sweep, vwap_now, pivot_points,
    # Sizing
    risk_adjusted_size, vol_adjusted_size, confidence_size_multiplier,
)

log = logging.getLogger("engine")


@dataclass
class SignalResult:
    symbol:      str
    direction:   str        # "LONG" | "SHORT"
    score:       float      # 0-100+
    is_valid:    bool
    entry_price: float
    sl_price:    float
    tp1_price:   float
    tp2_price:   float
    size_usdt:   float
    atr:         float
    regime:      str
    breakdown:   dict = field(default_factory=dict)

    @classmethod
    def invalid(cls, symbol: str, reason: str = "") -> "SignalResult":
        return cls(symbol=symbol, direction="", score=0.0,
                   is_valid=False, entry_price=0.0, sl_price=0.0,
                   tp1_price=0.0, tp2_price=0.0, size_usdt=0.0,
                   atr=0.0, regime="", breakdown={"reason": reason})

    def summary(self) -> str:
        bd = self.breakdown
        lines = [
            f"🎯 <b>{self.symbol} {self.direction}</b>  score={self.score:.0f}/100",
            f"  Entry: <code>{self.entry_price:.4f}</code>  "
            f"SL: <code>{self.sl_price:.4f}</code>",
            f"  TP1: <code>{self.tp1_price:.4f}</code>  "
            f"TP2: <code>{self.tp2_price:.4f}</code>",
            f"  Size: <code>${self.size_usdt:.2f}</code>  "
            f"ATR: <code>{self.atr:.4f}</code>  Régimen: <b>{self.regime}</b>",
            f"  CVD slope={bd.get('cvd_slope', 0):.3f}  "
            f"OFI={bd.get('ofi', 0):.2f}  "
            f"vol_ratio={bd.get('vol_ratio', 0):.2f}",
        ]
        if bd.get("sweep"):
            lines.append(f"  ⚡ Liquidity sweep detectado")
        return "\n".join(lines)


class ConfluenceEngine:
    """
    Evalúa 3 timeframes (HTF, MTF, LTF) y devuelve una SignalResult
    con el score de confluencia y todos los niveles calculados.
    """

    # ── Parámetros de HMA ──────────────────────────────────────────────────
    HTF_HMA_FAST  = 9
    HTF_HMA_SLOW  = 21
    MTF_HMA_FAST  = 9
    MTF_HMA_SLOW  = 21
    LTF_HMA_FAST  = 9
    LTF_HMA_SLOW  = 21

    # ── Umbrales de scoring ────────────────────────────────────────────────
    SCORE_THRESHOLD  = 65.0   # mínimo para abrir posición
    ADX_STRONG       = 22.0   # ADX mínimo para "tendencia"
    RSI_OB           = 70.0   # RSI sobrecomprado (evitar LONG)
    RSI_OS           = 30.0   # RSI sobrevendido   (evitar SHORT)
    RSI_LONG_ZONE    = (40.0, 65.0)   # zona ideal para LONG
    RSI_SHORT_ZONE   = (35.0, 60.0)   # zona ideal para SHORT
    VOL_RATIO_MIN    = 1.2    # volumen mínimo para confirmación
    OFI_BULL_MIN     = 0.55   # OFI mínimo para LONG
    OFI_BEAR_MAX     = 0.45   # OFI máximo para SHORT

    # ── Risk Management ────────────────────────────────────────────────────
    SL_ATR_MULT    = 1.6   # ATR × mult = distancia SL
    TP1_R          = 1.5   # TP1 en múltiplos de R (riesgo)
    TP2_R          = 2.5   # TP2 en múltiplos de R
    RISK_PCT       = 0.015 # riesgo por operación = 1.5% del balance

    def evaluate(
        self,
        symbol: str,
        # HTF arrays
        ho: List, hh: List, hl: List, hc: List, hv: List,
        # MTF arrays
        mo: List, mh: List, ml: List, mc: List, mv: List,
        # LTF arrays
        lo: List, lh: List, ll: List, lc: List, lv: List,
        # Balance actual
        balance: float = 100.0,
    ) -> SignalResult:

        # ── [1] HTF: dirección y régimen ────────────────────────────────
        htf_dir    = hma_direction(hc, self.HTF_HMA_SLOW)
        htf_regime = market_regime(hh, hl, hc)

        # ── [2] MTF: cruce HMA ──────────────────────────────────────────
        mtf_cross  = hma_crossover(mc, mc, self.MTF_HMA_FAST, self.MTF_HMA_SLOW)
        mtf_dir    = hma_direction(mc, self.MTF_HMA_SLOW)
        mtf_adx, mtf_dip, mtf_dim = adx_vals(mh, ml, mc)

        # ── [3] LTF: trigger ────────────────────────────────────────────
        ltf_cross  = hma_crossover(lc, lc, self.LTF_HMA_FAST, self.LTF_HMA_SLOW)
        ltf_dir    = hma_direction(lc, self.LTF_HMA_FAST)

        # ── [4] Volume / OFI ────────────────────────────────────────────
        vol_rat    = volume_ratio_now(lv)
        ofi        = orderflow_imbalance(lo, lh, ll, lc)

        # ── [5] CVD ─────────────────────────────────────────────────────
        cvd_sl     = cvd_slope(lo, lh, ll, lc, lv)
        cvd_div    = cvd_divergence(lo, lh, ll, lc, lv)

        # ── [6] Extras ──────────────────────────────────────────────────
        ltf_rsi    = rsi_now(lc)
        ltf_vwap   = vwap_now(lh, ll, lc, lv)
        ltf_price  = float(lc[-1])
        sweep      = liquidity_sweep(lh, ll, lc)

        # ── [7] ATR LTF para niveles ────────────────────────────────────
        ltf_atr    = atr_now(lh, ll, lc)
        ltf_atr_pct = atr_pct_now(lh, ll, lc)

        # ── Decidir dirección candidata ─────────────────────────────────
        # Exigimos alineación mínima HTF + MTF para evaluar
        if htf_dir == 1 and mtf_dir >= 0:
            candidate = "LONG"
        elif htf_dir == -1 and mtf_dir <= 0:
            candidate = "SHORT"
        else:
            return SignalResult.invalid(symbol, "HTF/MTF desalineados")

        # ── Scoring ─────────────────────────────────────────────────────
        score = 0.0
        bd    = {}

        # [1] HTF HMA dirección — 20 pts
        if (candidate == "LONG" and htf_dir == 1) or \
           (candidate == "SHORT" and htf_dir == -1):
            score += 20
            bd["htf_hma"] = 20
        else:
            bd["htf_hma"] = 0

        # [2] HTF Régimen — 15 pts
        if htf_regime == REGIME_TRENDING:
            score += 15
            bd["regime"] = 15
        elif htf_regime == REGIME_VOLATILE:
            score += 5   # volatile no es ideal pero no anula
            bd["regime"] = 5
        else:
            bd["regime"] = 0

        # [3] MTF HMA cruce o dirección alineada — 15 pts
        mtf_pts = 0
        if (candidate == "LONG"  and mtf_cross == 1) or \
           (candidate == "SHORT" and mtf_cross == -1):
            mtf_pts = 15  # cruce activo = máxima puntuación
        elif (candidate == "LONG"  and mtf_dir == 1) or \
             (candidate == "SHORT" and mtf_dir == -1):
            mtf_pts = 8   # solo dirección, sin cruce
        score += mtf_pts
        bd["mtf_hma"] = mtf_pts

        # [4] MTF ADX fuerza — 10 pts
        adx_pts = 0
        if mtf_adx >= self.ADX_STRONG:
            adx_pts = 10
        elif mtf_adx >= 18:
            adx_pts = 5
        # DI dirección confirmada
        if candidate == "LONG"  and mtf_dip > mtf_dim:
            adx_pts = min(adx_pts + 2, 10)
        if candidate == "SHORT" and mtf_dim > mtf_dip:
            adx_pts = min(adx_pts + 2, 10)
        score += adx_pts
        bd["adx"] = adx_pts

        # [5] LTF Trigger — 15 pts
        ltf_pts = 0
        if (candidate == "LONG"  and ltf_cross == 1) or \
           (candidate == "SHORT" and ltf_cross == -1):
            ltf_pts = 15
        elif (candidate == "LONG"  and ltf_dir == 1) or \
             (candidate == "SHORT" and ltf_dir == -1):
            ltf_pts = 7
        score += ltf_pts
        bd["ltf_trigger"] = ltf_pts

        # [6] CVD slope + divergencia — 10 pts
        cvd_pts = 0
        if candidate == "LONG":
            if cvd_sl > 0:
                cvd_pts += 5
            if cvd_div == 1:
                cvd_pts += 5
        else:
            if cvd_sl < 0:
                cvd_pts += 5
            if cvd_div == -1:
                cvd_pts += 5
        score += cvd_pts
        bd["cvd"] = cvd_pts
        bd["cvd_slope"] = round(cvd_sl, 4)

        # [7] OFI — 8 pts
        ofi_pts = 0
        if candidate == "LONG"  and ofi >= self.OFI_BULL_MIN:
            ofi_pts = 8
        elif candidate == "SHORT" and ofi <= self.OFI_BEAR_MAX:
            ofi_pts = 8
        elif 0.48 < ofi < 0.52:
            ofi_pts = 0   # neutro
        else:
            ofi_pts = 4   # parcial
        score += ofi_pts
        bd["ofi_pts"] = ofi_pts
        bd["ofi"] = round(ofi, 3)

        # [8] Volumen — 7 pts
        vol_pts = 0
        if vol_rat >= self.VOL_RATIO_MIN:
            vol_pts = 7
        elif vol_rat >= 1.0:
            vol_pts = 3
        score += vol_pts
        bd["vol_pts"] = vol_pts
        bd["vol_ratio"] = round(vol_rat, 2)

        # ── BONUS: Liquidity Sweep ─────────────────────────────────────
        sweep_pts = 0
        sweep_aligned = (candidate == "LONG" and sweep == 1) or \
                        (candidate == "SHORT" and sweep == -1)
        if sweep_aligned:
            sweep_pts = 5
            score += sweep_pts
        bd["sweep"] = sweep_aligned
        bd["sweep_pts"] = sweep_pts

        # ── BONUS: RSI zona favorable ─────────────────────────────────
        rsi_pts = 0
        if candidate == "LONG":
            lo_r, hi_r = self.RSI_LONG_ZONE
            if lo_r <= ltf_rsi <= hi_r:
                rsi_pts = 3
            elif ltf_rsi > self.RSI_OB:
                score -= 5  # penalización sobrecompra
        else:
            lo_r, hi_r = self.RSI_SHORT_ZONE
            if lo_r <= ltf_rsi <= hi_r:
                rsi_pts = 3
            elif ltf_rsi < self.RSI_OS:
                score -= 5  # penalización sobrevendido
        score += rsi_pts
        bd["rsi_pts"] = rsi_pts
        bd["rsi"] = round(ltf_rsi, 1)

        # ── BONUS: VWAP ───────────────────────────────────────────────
        vwap_pts = 0
        if candidate == "LONG"  and ltf_price > ltf_vwap:
            vwap_pts = 2
        elif candidate == "SHORT" and ltf_price < ltf_vwap:
            vwap_pts = 2
        score += vwap_pts
        bd["vwap_pts"] = vwap_pts

        # ── Régimen volatile: penalización de size, no de señal ───────
        if htf_regime == REGIME_VOLATILE:
            bd["vol_regime_penalty"] = True

        # ── Decisión ──────────────────────────────────────────────────
        score = max(0.0, score)
        bd["total"] = round(score, 1)

        if score < self.SCORE_THRESHOLD:
            return SignalResult.invalid(
                symbol, f"Score insuficiente: {score:.0f}/{self.SCORE_THRESHOLD:.0f}"
            )

        # ── Niveles de precio ─────────────────────────────────────────
        if ltf_atr == 0:
            return SignalResult.invalid(symbol, "ATR=0, datos insuficientes")

        sl_dist = ltf_atr * self.SL_ATR_MULT
        r       = sl_dist  # 1R = distancia SL

        if candidate == "LONG":
            sl  = ltf_price - sl_dist
            tp1 = ltf_price + r * self.TP1_R
            tp2 = ltf_price + r * self.TP2_R
        else:
            sl  = ltf_price + sl_dist
            tp1 = ltf_price - r * self.TP1_R
            tp2 = ltf_price - r * self.TP2_R

        # ── Position sizing ───────────────────────────────────────────
        base_size = risk_adjusted_size(
            balance      = balance,
            risk_pct     = self.RISK_PCT,
            entry        = ltf_price,
            sl           = sl,
            leverage     = config.LEVERAGE,
            min_usdt     = float(getattr(config, "MIN_TRADE_USDT", 5.0)),
            max_usdt     = float(getattr(config, "MAX_TRADE_USDT", 50.0)),
        )

        # Ajuste por volatilidad
        adj_size = vol_adjusted_size(base_size, ltf_atr_pct)

        # Ajuste por confianza de señal
        conf_mult = confidence_size_multiplier(score)
        final_size = adj_size * conf_mult

        # Reducir en régimen volátil
        if htf_regime == REGIME_VOLATILE:
            final_size *= 0.6

        final_size = max(float(getattr(config, "MIN_TRADE_USDT", 5.0)), final_size)

        log.info(
            f"[{symbol}] {candidate} score={score:.0f} "
            f"| sl={sl:.4f} tp1={tp1:.4f} tp2={tp2:.4f} "
            f"| size=${final_size:.2f} | regime={htf_regime}"
        )

        return SignalResult(
            symbol      = symbol,
            direction   = candidate,
            score       = round(score, 1),
            is_valid    = True,
            entry_price = ltf_price,
            sl_price    = round(sl, 6),
            tp1_price   = round(tp1, 6),
            tp2_price   = round(tp2, 6),
            size_usdt   = round(final_size, 2),
            atr         = round(ltf_atr, 6),
            regime      = htf_regime,
            breakdown   = bd,
        )


# ── Instancia global ──────────────────────────────────────────────────────────
engine = ConfluenceEngine()
