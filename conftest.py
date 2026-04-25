"""
conftest.py — Datos compartidos para todos los tests
"""
import sys
import os
import pytest
import numpy as np
import pandas as pd
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# ── Generadores de datos sintéticos ────────────────────────────────────────

def make_ohlcv(n=350, seed=42, trend="bull", base=67000.0, vol_base=500.0):
    """Genera un DataFrame OHLCV sintético con tendencia configurable."""
    np.random.seed(seed)
    drift = 15.0 if trend == "bull" else -15.0
    price = base + np.cumsum(np.random.randn(n) * 60 + drift)
    price = np.maximum(price, 100.0)   # nunca negativo
    noise_h = np.abs(np.random.randn(n) * 120) + 20
    noise_l = np.abs(np.random.randn(n) * 120) + 20
    vol = np.abs(np.random.randn(n) * vol_base) + vol_base * 0.5

    df = pd.DataFrame({
        "open":   price,
        "high":   price + noise_h,
        "low":    price - noise_l,
        "close":  price,
        "volume": vol,
    })
    df.index = pd.date_range("2024-01-01", periods=n, freq="15min")
    return df


def make_bear_ohlcv(n=350, seed=99):
    return make_ohlcv(n=n, seed=seed, trend="bear")


def make_ranging_ohlcv(n=350, seed=7):
    return make_ohlcv(n=n, seed=seed, trend="bull", base=67000.0)


@pytest.fixture
def df_bull():
    return make_ohlcv(trend="bull")


@pytest.fixture
def df_bear():
    return make_ohlcv(trend="bear")


@pytest.fixture
def df_htf1():
    """Timeframe mayor simulado (submuestreo)."""
    df = make_ohlcv(n=350, trend="bull")
    return df.iloc[::4].reset_index(drop=True)


@pytest.fixture
def df_htf2():
    """Timeframe aún mayor."""
    df = make_ohlcv(n=350, trend="bull")
    return df.iloc[::16].reset_index(drop=True)


@pytest.fixture
def engine_cfg():
    return {
        "vwma_len": 200, "ema_fast": 21, "ema_slow": 50,
        "rsi_len": 14, "rsi_bull": 55, "rsi_bear": 45,
        "atr_len": 10, "st_mult": 3.5,
        "use_adx": True, "adx_len": 14, "adx_thr": 25,
        "cooldown": 3,
        "stop_mode": "Supertrend", "stop_atr_mult": 1.5, "stop_fixed_pct": 0.3,
        "rr1": 0.5, "rr2": 1.0, "rr3": 2.0, "rr4": 3.0,
        "min_volume_percentile": 20,
        "funding_threshold": 0.05,
    }


@pytest.fixture
def risk_cfg():
    return {
        "starting_balance": 1000.0,
        "leverage": 5,
        "max_risk_per_trade_pct": 2.0,
        "max_position_usdt": 500.0,
        "max_open_trades": 3,
        "max_daily_loss_pct": 3.0,
        "max_weekly_loss_pct": 8.0,
        "max_drawdown_pct": 15.0,
        "min_signal_quality": 4,
        "use_session_filter": False,   # desactivado para tests
        "rr2": 1.0,
    }


@pytest.fixture
def tmp_equity(tmp_path):
    return str(tmp_path / "equity.json")


@pytest.fixture
def tmp_trades(tmp_path):
    return str(tmp_path / "trades.json")
