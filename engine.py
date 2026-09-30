"""
Motor de TENDENCIA — el mismo código lo usan el backtest y el bot (se opera lo que se mide).

Reglas (Zarattini, Pagani y Barbon 2025, "Catching Crypto Trends", SSRN 5209907):
  · Velas DIARIAS (cierre 00:00 UTC).
  · Para cada lookback L de {5,10,20,30,60,90,150,250,360}:
      ENTRA si el cierre de hoy supera el MÁXIMO de los cierres de los L días anteriores.
      Stop = máx(stop anterior, punto medio del canal de cierres de los últimos L días).
      SALE si el cierre cae por debajo del stop.
  · Señal de la moneda = fracción de lookbacks en posición (0 … 1).
  · Tamaño = señal × min(tope, VOL_OBJETIVO / vol realizada 90 días anualizada).
  · Cartera = N monedas más líquidas, cada una con 1/N del capital asignado.
Cortos: opcionales (espejo exacto). Por defecto APAGADOS: en cripto los cortos apenas
aportan y sufren estrujamientos (una moneda +1.400% en una semana).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

LOOKBACKS = (5, 10, 20, 30, 60, 90, 150, 250, 360)


@dataclass
class Params:
    lookbacks: tuple = LOOKBACKS
    allow_short: bool = False
    vol_target: float = 0.25        # anual, por moneda
    vol_lookback: int = 90
    coin_cap: float = 1.0           # exposición máx. por moneda sobre su hueco (1 = sin apalancar)
    gross_cap: float = 1.0          # exposición bruta máx. de la cartera
    n_coins: int = 10               # tamaño de la cesta
    min_history: int = 365          # días de historial mínimos para entrar en la cesta
    liq_window: int = 30            # días para medir liquidez (volumen en USDT)
    rebal_band: float = 0.02        # no operar si el cambio de peso es menor (fracción del capital)
    cost_side: float = 0.0008       # comisión taker 0.05% + deslizamiento 0.03%, por lado
    fund_default: float = 0.0001    # funding por periodo de 8 h si no hay histórico (0.01%)

    def to_dict(self):
        d = asdict(self)
        d["lookbacks"] = list(self.lookbacks)
        return d


# ───────────────────────── señal de una moneda ─────────────────────────
def donchian_state(close: np.ndarray, lookbacks=LOOKBACKS, allow_short=False):
    """Devuelve (señal[t] en [-1,1], detalle) calculada con datos HASTA el cierre t inclusive.
    La posición decidida al cierre t se aplica al retorno t+1 (sin mirar el futuro)."""
    n = len(close)
    sig = np.zeros(n)
    per_lb = {}
    for L in lookbacks:
        pos = np.zeros(n)
        stop = np.nan
        p = 0
        if n > L:
            s = pd.Series(close)
            prev_max = s.shift(1).rolling(L).max().to_numpy()   # máx de los L cierres ANTERIORES
            prev_min = s.shift(1).rolling(L).min().to_numpy()
            win_max = s.rolling(L).max().to_numpy()             # canal que incluye hoy (para el stop)
            win_min = s.rolling(L).min().to_numpy()
            mid = (win_max + win_min) / 2
            for t in range(L, n):
                c = close[t]
                if p == 1:
                    stop = max(stop, mid[t])
                    if c < stop:
                        p = 0
                elif p == -1:
                    stop = min(stop, mid[t])
                    if c > stop:
                        p = 0
                if p == 0:
                    if c > prev_max[t]:
                        p, stop = 1, mid[t]
                    elif allow_short and c < prev_min[t]:
                        p, stop = -1, mid[t]
                pos[t] = p
        per_lb[L] = pos
        sig += pos
    sig /= len(lookbacks)
    return sig, per_lb


def realized_vol(close: np.ndarray, lookback=90) -> np.ndarray:
    r = pd.Series(np.log(close)).diff()
    return (r.rolling(lookback, min_periods=max(20, lookback // 3)).std() * np.sqrt(365)).to_numpy()


def coin_weight(sig: np.ndarray, vol: np.ndarray, p: Params) -> np.ndarray:
    """Exposición de la moneda sobre SU hueco (1 = hueco entero)."""
    scale = np.where(vol > 0, np.minimum(p.coin_cap, p.vol_target / np.where(vol > 0, vol, np.nan)), 0.0)
    w = sig * np.nan_to_num(scale)
    return np.clip(w, -p.coin_cap, p.coin_cap)


# ───────────────────────── panel de varias monedas ─────────────────────────
def build_panel(frames: dict) -> dict:
    """frames: {sym: DataFrame diario con open high low close quote_vol, índice fecha UTC}.
    Devuelve matrices alineadas por fecha (NaN donde no cotiza)."""
    idx = sorted(set().union(*[f.index for f in frames.values()]))
    idx = pd.DatetimeIndex(idx)
    close = pd.DataFrame({s: f["close"] for s, f in frames.items()}).reindex(idx)
    qvol = pd.DataFrame({s: f["quote_vol"] for s, f in frames.items()}).reindex(idx)
    return dict(index=idx, close=close, qvol=qvol)


def universe_mask(panel: dict, p: Params) -> pd.DataFrame:
    """Cesta punto-en-el-tiempo: el primer día de cada mes se eligen las N monedas con más
    volumen medio en los últimos liq_window días entre las que tienen min_history días."""
    close, qvol = panel["close"], panel["qvol"]
    hist = close.notna().cumsum()
    liq = qvol.rolling(p.liq_window, min_periods=p.liq_window // 2).mean()
    mask = pd.DataFrame(False, index=close.index, columns=close.columns)
    cur = []
    for i, d in enumerate(close.index):
        if i == 0 or d.month != close.index[i - 1].month:
            elig = (hist.iloc[i] >= p.min_history) & liq.iloc[i].notna() & close.iloc[i].notna()
            cand = liq.iloc[i][elig].sort_values(ascending=False)
            cur = list(cand.index[: p.n_coins])
        if cur:
            mask.loc[d, cur] = True
    # una moneda que deja de cotizar sale de la cesta
    return mask & close.notna()


def target_weights(panel: dict, p: Params) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pesos objetivo de CARTERA (fracción del capital) al cierre de cada día + señales."""
    close = panel["close"]
    sig_df = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    w_df = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for s in close.columns:
        c = close[s]
        valid = c.notna().to_numpy()
        if valid.sum() < 30:
            continue
        cv = c[valid].to_numpy()
        sig, _ = donchian_state(cv, p.lookbacks, p.allow_short)
        vol = realized_vol(cv, p.vol_lookback)
        sig_df.loc[valid, s] = sig
        w_df.loc[valid, s] = coin_weight(sig, vol, p)
    mask = universe_mask(panel, p)
    w = (w_df * mask) / p.n_coins
    gross = w.abs().sum(axis=1)
    scale = np.where(gross > p.gross_cap, p.gross_cap / gross, 1.0)
    return w.mul(scale, axis=0), sig_df


def bh_weights(panel: dict, p: Params) -> pd.DataFrame:
    """REFERENCIA: misma cesta y mismo escalado por volatilidad pero SIEMPRE comprado.
    Si la tendencia no mejora esto, lo que funciona es el escalado, no la señal."""
    close = panel["close"]
    w_df = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for s in close.columns:
        c = close[s]
        valid = c.notna().to_numpy()
        if valid.sum() < 30:
            continue
        vol = realized_vol(c[valid].to_numpy(), p.vol_lookback)
        w_df.loc[valid, s] = coin_weight(np.ones(valid.sum()), vol, p)
    w = (w_df * universe_mask(panel, p)) / p.n_coins
    gross = w.abs().sum(axis=1)
    return w.mul(np.where(gross > p.gross_cap, p.gross_cap / gross, 1.0), axis=0)


def latest_targets(frames: dict, p: Params) -> tuple[pd.Series, pd.Series, pd.Timestamp]:
    """Para el bot: pesos objetivo y señales al ÚLTIMO cierre diario completo."""
    panel = build_panel(frames)
    w, sig = target_weights(panel, p)
    return w.iloc[-1], sig.iloc[-1], w.index[-1]
