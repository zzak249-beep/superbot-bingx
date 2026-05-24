"""
indicators.py — Implementación Python de todos los indicadores del QF×JP v3
Equivalentes exactos de los cálculos Pine Script v6
"""
import numpy as np
import pandas as pd


# ── Helpers ───────────────────────────────────────────────────

def f_tanh(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, -10, 10)
    e2x = np.exp(2 * x)
    return (e2x - 1) / (e2x + 1)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def stdev(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).std(ddof=0)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 10) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low  - close.shift(1)).abs()
    tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def rolling_corr(x: pd.Series, y: pd.Series, period: int) -> pd.Series:
    return x.rolling(period).corr(y)


def linreg_last(series: pd.Series, period: int) -> pd.Series:
    """Rolling linear regression — valor en el punto final de cada ventana"""
    result = pd.Series(np.nan, index=series.index)
    arr = series.values
    for i in range(period - 1, len(arr)):
        y = arr[i - period + 1 : i + 1]
        if np.any(np.isnan(y)):
            continue
        x = np.arange(period, dtype=float)
        m, b = np.polyfit(x, y, 1)
        result.iloc[i] = m * (period - 1) + b
    return result


def pivot_high(high: pd.Series, left: int, right: int) -> pd.Series:
    """Pivot highs — confirmados cuando hay `right` velas a la derecha"""
    result = pd.Series(np.nan, index=high.index)
    h = high.values
    for i in range(left, len(h) - right):
        window = h[i - left : i + right + 1]
        if h[i] == window.max() and list(window).count(h[i]) == 1:
            result.iloc[i] = h[i]
    return result


def pivot_low(low: pd.Series, left: int, right: int) -> pd.Series:
    """Pivot lows — confirmados cuando hay `right` velas a la derecha"""
    result = pd.Series(np.nan, index=low.index)
    l = low.values
    for i in range(left, len(l) - right):
        window = l[i - left : i + right + 1]
        if l[i] == window.min() and list(window).count(l[i]) == 1:
            result.iloc[i] = l[i]
    return result


def vwap_rolling(high: pd.Series, low: pd.Series, close: pd.Series,
                 volume: pd.Series, period: int = 480) -> pd.Series:
    """VWAP rolling (480 velas ≈ 24h en 3min)"""
    hlc3 = (high + low + close) / 3
    cum_vol = volume.rolling(period).sum()
    cum_pv  = (hlc3 * volume).rolling(period).sum()
    return cum_pv / cum_vol


# ── L2 · Motor de Factores ────────────────────────────────────

def factor_momentum(close: pd.Series, period: int) -> pd.Series:
    roc = (close - close.shift(period)) / close.shift(period)
    vol_n = stdev(close, period) / sma(close, period)
    return roc.where(vol_n != 0, 0) / vol_n.replace(0, np.nan).fillna(1)


def factor_mean_rev(close: pd.Series, period: int) -> pd.Series:
    basis = sma(close, period)
    std   = stdev(close, period)
    z     = -(close - basis) / std.replace(0, np.nan)
    return z.fillna(0)


def factor_volume(close: pd.Series, volume: pd.Series, period: int) -> pd.Series:
    obv_s  = obv(close, volume)
    obv_ma = ema(obv_s, period)
    obv_sd = stdev(obv_s, period)
    return ((obv_s - obv_ma) / obv_sd.replace(0, np.nan)).fillna(0)


def composite_score(close: pd.Series, volume: pd.Series,
                    mom_p: int, rev_p: int, vol_p: int,
                    w1: float, w2: float, w3: float,
                    smooth: int, decay_len: int) -> pd.Series:
    fm  = factor_momentum(close, mom_p)
    fr  = factor_mean_rev(close, rev_p)
    fv  = factor_volume(close, volume, vol_p)
    raw = w1 * fm + w2 * fr + w3 * fv
    comp= ema(raw, smooth)
    sc_std = stdev(comp, decay_len)
    norm = (comp / sc_std.replace(0, np.nan)).fillna(0)
    return pd.Series(f_tanh(norm.values), index=close.index), fm, fr, fv


# ── L3 · Decaimiento ─────────────────────────────────────────

def signal_decay(norm_score: pd.Series, close: pd.Series,
                 decay_len: int, smooth: int, threshold: float):
    fwd_ret  = close.pct_change()
    ic_raw   = rolling_corr(norm_score.shift(1), fwd_ret, decay_len)
    ic_roll  = ema(ic_raw.abs(), smooth)
    ic_peak  = ic_roll.rolling(decay_len).max()
    decay_r  = (ic_roll / ic_peak.replace(0, np.nan)).fillna(0.5)
    sig_alive= decay_r >= threshold
    return sig_alive, decay_r


# ── L4 · Dark Pool ────────────────────────────────────────────

def dark_pool(high: pd.Series, low: pd.Series, close: pd.Series,
              open_: pd.Series, volume: pd.Series,
              atr_s: pd.Series, vol_mult: float, baseline: int):
    vol_base   = sma(volume, baseline)
    vol_spike  = volume > vol_base * vol_mult
    rng_narrow = (high - low) < atr_s * 0.6
    dp_buy     = vol_spike & rng_narrow & (close > open_)
    dp_sell    = vol_spike & rng_narrow & (close < open_)
    vac_up     = ((high - low) > atr_s * 1.8) & (volume < vol_base * 0.6) & (close > open_)
    vac_dn     = ((high - low) > atr_s * 1.8) & (volume < vol_base * 0.6) & (close < open_)
    return dp_buy, dp_sell, vac_up, vac_dn


# ── L5 · Coste de Ejecución ──────────────────────────────────

def exec_cost(high: pd.Series, low: pd.Series, close: pd.Series,
              spread_len: int, bp_thr: float):
    hi_lo_r   = np.log(high / low)
    spread_e  = sma(hi_lo_r, spread_len) * close
    bp_drain  = (spread_e / close) * 100
    exec_ok   = bp_drain < bp_thr
    return exec_ok, bp_drain


# ── L6 · Asimetría Momentum ──────────────────────────────────

def momentum_asymmetry(high: pd.Series, low: pd.Series,
                       close: pd.Series, open_: pd.Series,
                       period: int, bull_thr: float, bear_thr: float):
    up_rng = (high - low).where(close > open_, 0.0)
    dn_rng = (high - low).where(close < open_, 0.0)
    avg_up = sma(up_rng, period)
    avg_dn = sma(dn_rng, period)
    rr_bull = (avg_up / avg_dn.replace(0, np.nan)).fillna(1)
    rr_bear = (avg_dn / avg_up.replace(0, np.nan)).fillna(1)
    asym_bull = rr_bull >= bull_thr
    asym_bear = rr_bear >= bear_thr
    return asym_bull, asym_bear, rr_bull, rr_bear


# ── L7 · Ruptura Trendline ───────────────────────────────────

def trendline_break(high: pd.Series, low: pd.Series, close: pd.Series,
                    atr_s: pd.Series, ph: pd.Series, pl: pd.Series,
                    tl_lookback: int, tl_left: int, tl_right: int, tl_buf: float):
    """Devuelve dos Series bool: tl_break_long, tl_break_short"""
    n = len(close)
    tl_break_long  = pd.Series(False, index=close.index)
    tl_break_short = pd.Series(False, index=close.index)

    # Recopilar pivots confirmados con su índice real
    ph_vals = [(i, ph.iloc[i]) for i in range(len(ph)) if not np.isnan(ph.iloc[i])]
    pl_vals = [(i, pl.iloc[i]) for i in range(len(pl)) if not np.isnan(pl.iloc[i])]

    for i in range(tl_right + 2, n):
        atr_i = atr_s.iloc[i]
        c_now  = close.iloc[i]
        c_prev = close.iloc[i - 1]

        # TL bajista (pivot highs decrecientes)
        ph_recent = [(idx, v) for idx, v in ph_vals
                     if idx <= i - tl_right and (i - idx) <= tl_lookback]
        if len(ph_recent) >= 2:
            ph2_idx, ph2_v = ph_recent[-2]
            ph1_idx, ph1_v = ph_recent[-1]
            if ph2_v > ph1_v:
                dx = max(ph1_idx - ph2_idx, 1)
                slope = (ph1_v - ph2_v) / dx
                tl_now  = ph1_v + slope * (i - ph1_idx)
                tl_prev = ph1_v + slope * (i - 1 - ph1_idx)
                buf = atr_i * tl_buf
                if c_now > tl_now + buf and c_prev <= tl_prev + buf:
                    tl_break_long.iloc[i] = True

        # TL alcista (pivot lows crecientes)
        pl_recent = [(idx, v) for idx, v in pl_vals
                     if idx <= i - tl_right and (i - idx) <= tl_lookback]
        if len(pl_recent) >= 2:
            pl2_idx, pl2_v = pl_recent[-2]
            pl1_idx, pl1_v = pl_recent[-1]
            if pl2_v < pl1_v:
                dx = max(pl1_idx - pl2_idx, 1)
                slope = (pl1_v - pl2_v) / dx
                tl_now  = pl1_v + slope * (i - pl1_idx)
                tl_prev = pl1_v + slope * (i - 1 - pl1_idx)
                buf = atr_i * tl_buf
                if c_now < tl_now - buf and c_prev >= tl_prev - buf:
                    tl_break_short.iloc[i] = True

    return tl_break_long, tl_break_short


# ── L8 · Swing Lows/Highs ────────────────────────────────────

def swing_analysis(pl: pd.Series, ph: pd.Series,
                   window: int, hl_min: int, lh_min: int):
    n = len(pl)
    sell_exhausted = pd.Series(False, index=pl.index)
    buy_exhausted  = pd.Series(False, index=pl.index)
    last_sl        = pd.Series(np.nan, index=pl.index)
    last_sh        = pd.Series(np.nan, index=ph.index)

    for i in range(window, n):
        # Higher Lows en ventana
        pls = [(j, pl.iloc[j]) for j in range(i - window, i + 1)
               if not np.isnan(pl.iloc[j])]
        hl_count = sum(1 for k in range(1, len(pls)) if pls[k][1] > pls[k-1][1])
        sell_exhausted.iloc[i] = hl_count >= hl_min

        # Lower Highs en ventana
        phs = [(j, ph.iloc[j]) for j in range(i - window, i + 1)
               if not np.isnan(ph.iloc[j])]
        lh_count = sum(1 for k in range(1, len(phs)) if phs[k][1] < phs[k-1][1])
        buy_exhausted.iloc[i] = lh_count >= lh_min

        if pls:
            last_sl.iloc[i] = pls[-1][1]
        if phs:
            last_sh.iloc[i] = phs[-1][1]

    return sell_exhausted, buy_exhausted, last_sl, last_sh


# ── L9 · Fair Value Gaps ─────────────────────────────────────

def fair_value_gaps(high: pd.Series, low: pd.Series,
                    atr_s: pd.Series, min_atr: float, max_bars: int):
    n = len(high)
    bull_fvg = pd.Series(False, index=high.index)
    bear_fvg = pd.Series(False, index=high.index)
    in_bull  = pd.Series(False, index=high.index)
    in_bear  = pd.Series(False, index=high.index)

    bfvg_top = bfvg_bot = np.nan
    sfvg_top = sfvg_bot = np.nan
    bfvg_age = sfvg_age = 0

    for i in range(2, n):
        c_close = (high.iloc[i] + low.iloc[i]) / 2  # proxy
        atr_i   = atr_s.iloc[i]

        # Detectar FVG alcista: low[0] > high[2]
        if low.iloc[i] > high.iloc[i - 2] and (low.iloc[i] - high.iloc[i - 2]) > atr_i * min_atr:
            bull_fvg.iloc[i] = True
            bfvg_top = low.iloc[i]
            bfvg_bot = high.iloc[i - 2]
            bfvg_age = 0
        else:
            bfvg_age += 1
            if bfvg_age > max_bars:
                bfvg_top = bfvg_bot = np.nan

        # Detectar FVG bajista: high[0] < low[2]
        if high.iloc[i] < low.iloc[i - 2] and (low.iloc[i - 2] - high.iloc[i]) > atr_i * min_atr:
            bear_fvg.iloc[i] = True
            sfvg_top = low.iloc[i - 2]
            sfvg_bot = high.iloc[i]
            sfvg_age = 0
        else:
            sfvg_age += 1
            if sfvg_age > max_bars:
                sfvg_top = sfvg_bot = np.nan

        # ¿Precio en zona FVG?
        c = (high.iloc[i] + low.iloc[i]) / 2
        if not np.isnan(bfvg_top) and bfvg_bot <= c <= bfvg_top:
            in_bull.iloc[i] = True
        if not np.isnan(sfvg_top) and sfvg_bot <= c <= sfvg_top:
            in_bear.iloc[i] = True

    return bull_fvg, bear_fvg, in_bull, in_bear


# ── L10 · Order Blocks ───────────────────────────────────────

def order_blocks(high: pd.Series, low: pd.Series,
                 close: pd.Series, open_: pd.Series,
                 atr_s: pd.Series, impulse_atr: float, max_bars: int):
    n = len(close)
    bull_ob_raw = pd.Series(False, index=close.index)
    bear_ob_raw = pd.Series(False, index=close.index)
    in_bull_ob  = pd.Series(False, index=close.index)
    in_bear_ob  = pd.Series(False, index=close.index)

    bob_hi = bob_lo = np.nan
    sob_hi = sob_lo = np.nan
    bob_age = sob_age = 0

    for i in range(1, n):
        atr_i = atr_s.iloc[i]
        c_now = close.iloc[i]

        strong_up = (close.iloc[i] - open_.iloc[i]) > atr_i * impulse_atr and c_now > close.iloc[i-1]
        strong_dn = (open_.iloc[i] - close.iloc[i]) > atr_i * impulse_atr and c_now < close.iloc[i-1]

        if strong_up and close.iloc[i-1] < open_.iloc[i-1]:
            bull_ob_raw.iloc[i] = True
            bob_hi  = open_.iloc[i-1]
            bob_lo  = close.iloc[i-1]
            bob_age = 0
        else:
            bob_age += 1
            if bob_age > max_bars or (not np.isnan(bob_lo) and c_now < bob_lo):
                bob_hi = bob_lo = np.nan

        if strong_dn and close.iloc[i-1] > open_.iloc[i-1]:
            bear_ob_raw.iloc[i] = True
            sob_hi  = close.iloc[i-1]
            sob_lo  = open_.iloc[i-1]
            sob_age = 0
        else:
            sob_age += 1
            if sob_age > max_bars or (not np.isnan(sob_hi) and c_now > sob_hi):
                sob_hi = sob_lo = np.nan

        if not np.isnan(bob_hi) and bob_lo <= c_now <= bob_hi:
            in_bull_ob.iloc[i] = True
        if not np.isnan(sob_hi) and sob_lo <= c_now <= sob_hi:
            in_bear_ob.iloc[i] = True

    return bull_ob_raw, bear_ob_raw, in_bull_ob, in_bear_ob


# ── L11 · CVD Delta ──────────────────────────────────────────

def cvd_delta(high: pd.Series, low: pd.Series,
              close: pd.Series, volume: pd.Series,
              ema_len: int, div_len: int):
    hl = (high - low).replace(0, np.nan)
    bvol = ((close - low) / hl * volume).fillna(volume * 0.5)
    svol = ((high - close) / hl * volume).fillna(volume * 0.5)
    delta = (bvol - svol).cumsum()
    delta_ema   = ema(delta, ema_len)
    cvd_rising  = delta > delta_ema
    bull_div    = (close < close.shift(div_len)) & (delta > delta.shift(div_len))
    bear_div    = (close > close.shift(div_len)) & (delta < delta.shift(div_len))
    return cvd_rising, bull_div, bear_div


# ── L12 · Squeeze Momentum ───────────────────────────────────

def squeeze_momentum(high: pd.Series, low: pd.Series,
                     close: pd.Series, atr_s: pd.Series,
                     length: int, bb_mult: float, kc_mult: float):
    basis   = sma(close, length)
    dev     = stdev(close, length)
    bb_hi   = basis + bb_mult * dev
    bb_lo   = basis - bb_mult * dev
    kc_mid  = ema(close, length)
    kc_hi   = kc_mid + kc_mult * atr_s
    kc_lo   = kc_mid - kc_mult * atr_s

    sq_on   = (bb_hi < kc_hi) & (bb_lo > kc_lo)
    sq_fire = ~sq_on & sq_on.shift(1).fillna(False)

    hh = high.rolling(length).max()
    ll = low.rolling(length).min()
    mid_val = (hh + ll) / 2
    delta_s = close - (mid_val + basis) / 2
    sq_val  = linreg_last(delta_s, length)

    sq_bull = sq_fire & (sq_val > 0)
    sq_bear = sq_fire & (sq_val < 0)
    return sq_on, sq_bull, sq_bear
