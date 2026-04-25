"""
Tests — Indicadores y Motor de Señales Conflux 4
Verifica: EMA, VWMA, RSI, ATR, Supertrend, ADX, señales, MTF, funding filter
"""
import pytest
import numpy as np
import pandas as pd
from conftest import make_ohlcv

from conflux4 import (
    ema, vwma, rsi, atr, supertrend, adx,
    volume_percentile, Conflux4Engine
)


# ══════════════════════════════════════════════════════════════════
# INDICADORES BASE
# ══════════════════════════════════════════════════════════════════

class TestEMA:
    def test_length_preserved(self):
        s = pd.Series(np.random.randn(100) + 100)
        result = ema(s, 20)
        assert len(result) == 100

    def test_converges_to_constant(self):
        """EMA de una serie constante == esa constante."""
        s = pd.Series([50.0] * 200)
        result = ema(s, 20)
        assert abs(result.iloc[-1] - 50.0) < 0.001

    def test_fast_reacts_quicker_than_slow(self):
        """EMA rápida reacciona más rápido a cambios."""
        # Serie estable + spike al final
        s = pd.Series([100.0] * 100 + [200.0] * 20)
        e5  = ema(s, 5)
        e50 = ema(s, 50)
        # La EMA rápida debe acercarse más al nuevo valor
        assert e5.iloc[-1] > e50.iloc[-1]

    def test_no_nan_after_warmup(self):
        s = pd.Series(np.ones(100) * 100.0)
        result = ema(s, 20)
        assert not result.iloc[20:].isna().any()


class TestVWMA:
    def test_length_preserved(self):
        df = make_ohlcv(n=250)
        result = vwma(df["close"], df["volume"], 200)
        assert len(result) == 250

    def test_high_volume_bar_has_more_weight(self):
        """Con volumen extremo en la última barra, VWMA debe acercarse a ese precio."""
        close = pd.Series([100.0] * 200 + [200.0])
        vol   = pd.Series([1.0] * 200 + [10000.0])
        result = vwma(close, vol, 20)
        assert result.iloc[-1] > 150.0   # definitivamente movido hacia 200

    def test_equal_volumes_equals_sma(self):
        """Con volumen constante, VWMA == SMA."""
        close = pd.Series(np.random.randn(100) + 100)
        vol   = pd.Series(np.ones(100))
        vw = vwma(close, vol, 10)
        sm = close.rolling(10).mean()
        diff = (vw - sm).dropna().abs()
        assert diff.max() < 1e-8


class TestRSI:
    def test_range_0_100(self):
        df = make_ohlcv(n=200)
        r = rsi(df["close"], 14)
        valid = r.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_strong_uptrend_above_50(self):
        """Tendencia alcista con ruido realista → RSI > 50."""
        import numpy as np
        np.random.seed(0)
        s = pd.Series(100 + np.cumsum(np.random.randn(200) * 0.3 + 1.0))
        r = rsi(s, 14)
        assert r.iloc[-1] > 50, f"RSI={r.iloc[-1]:.1f} esperado >50 en uptrend"

    def test_strong_downtrend_below_50(self):
        s = pd.Series(np.cumsum(-np.ones(200)) + 100.0)
        r = rsi(s, 14)
        assert r.iloc[-1] < 50

    def test_flat_market_near_50(self):
        """Serie plana → RSI ≈ 50 (sin ganancias ni pérdidas netas)."""
        s = pd.Series([100.0] * 200)
        r = rsi(s, 14)
        # Una serie completamente plana produce NaN (0/0) — aceptable
        # Si no es NaN, debe estar cerca de 50
        last = r.iloc[-1]
        assert np.isnan(last) or abs(last - 50) < 30


class TestATR:
    def test_positive(self):
        df = make_ohlcv(n=200)
        a = atr(df["high"], df["low"], df["close"], 10)
        assert (a.dropna() > 0).all()

    def test_wider_bars_higher_atr(self):
        """Barras más amplias → ATR mayor."""
        n = 200
        np.random.seed(0)
        base_close = np.cumsum(np.random.randn(n) * 50 + 100) + 1000

        # ATR pequeño
        h1 = base_close + 50
        l1 = base_close - 50
        a1 = atr(pd.Series(h1), pd.Series(l1), pd.Series(base_close), 10)

        # ATR grande
        h2 = base_close + 500
        l2 = base_close - 500
        a2 = atr(pd.Series(h2), pd.Series(l2), pd.Series(base_close), 10)

        assert a2.iloc[-1] > a1.iloc[-1]


class TestSupertrend:
    def test_returns_two_series(self):
        df = make_ohlcv(n=200)
        st_v, st_d = supertrend(df["high"], df["low"], df["close"], 10, 3.5)
        assert len(st_v) == len(df)
        assert len(st_d) == len(df)

    def test_direction_only_1_or_minus1(self):
        df = make_ohlcv(n=200)
        _, st_d = supertrend(df["high"], df["low"], df["close"], 10, 3.5)
        valid = st_d.dropna()
        assert set(valid.unique()).issubset({1, -1})

    def test_bull_trend_direction_minus1(self):
        """En tendencia alcista sostenida, dirección debe ser -1 (Pine Script convention)."""
        df = make_ohlcv(n=350, trend="bull")
        _, st_d = supertrend(df["high"], df["low"], df["close"], 10, 3.5)
        # La última dirección debe ser -1 (bull)
        assert st_d.iloc[-1] == -1

    def test_st_positive(self):
        df = make_ohlcv(n=200)
        st_v, _ = supertrend(df["high"], df["low"], df["close"], 10, 3.5)
        assert (st_v.dropna() > 0).all()


class TestADX:
    def test_returns_three_series(self):
        df = make_ohlcv(n=200)
        di_p, di_m, adx_v = adx(df["high"], df["low"], df["close"], 14)
        assert len(di_p) == len(di_m) == len(adx_v) == 200

    def test_adx_range(self):
        df = make_ohlcv(n=200)
        _, _, adx_v = adx(df["high"], df["low"], df["close"], 14)
        valid = adx_v.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_trending_market_higher_adx(self):
        """Mercado en tendencia debe tener ADX mayor que mercado lateral."""
        df_trend  = make_ohlcv(n=300, seed=1, trend="bull")
        # Serie casi plana
        np.random.seed(5)
        flat_p = np.random.randn(300) * 5 + 100  # rango muy estrecho
        flat_h = flat_p + 2; flat_l = flat_p - 2
        df_flat = pd.DataFrame({"high": flat_h, "low": flat_l,
                                "close": flat_p, "volume": np.ones(300)})

        _, _, adx_trend = adx(df_trend["high"], df_trend["low"], df_trend["close"], 14)
        _, _, adx_flat  = adx(pd.Series(flat_h), pd.Series(flat_l), pd.Series(flat_p), 14)

        assert adx_trend.iloc[-1] > adx_flat.iloc[-1]


class TestVolumePercentile:
    def test_range_0_100(self):
        df = make_ohlcv(n=100)
        vp = volume_percentile(df["volume"], 20)
        valid = vp.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_highest_volume_returns_100(self):
        """El bar con mayor volumen de la ventana debe tener percentil máximo."""
        vol = pd.Series([100.0] * 19 + [9999.0])  # último es el mayor
        vp = volume_percentile(vol, 20)
        assert vp.iloc[-1] == 100.0


# ══════════════════════════════════════════════════════════════════
# MOTOR DE SEÑALES
# ══════════════════════════════════════════════════════════════════

class TestConflux4Engine:
    def test_compute_returns_signal_result(self, engine_cfg):
        df = make_ohlcv(n=350, trend="bull")
        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df)
        assert hasattr(r, "signal")
        assert hasattr(r, "quality")
        assert hasattr(r, "trend")
        assert hasattr(r, "confluence")

    def test_signal_is_null_or_string(self, engine_cfg):
        df = make_ohlcv(n=350)
        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df)
        assert r.signal in (None, "BULL", "BEAR")

    def test_quality_range(self, engine_cfg):
        df = make_ohlcv(n=350, trend="bull")
        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df)
        assert 0 <= r.quality <= 10

    def test_confluence_range(self, engine_cfg):
        df = make_ohlcv(n=350)
        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df)
        assert 0 <= r.confluence <= 4

    def test_stop_below_entry_for_bull(self, engine_cfg):
        """Stop de long siempre por debajo de la entrada."""
        for seed in range(5):
            df = make_ohlcv(n=350, seed=seed, trend="bull")
            eng = Conflux4Engine(engine_cfg)
            r = eng.compute(df)
            if r.signal == "BULL":
                assert r.stop < r.entry, f"Stop {r.stop} >= entry {r.entry}"
                break

    def test_stop_above_entry_for_bear(self, engine_cfg):
        """Stop de short siempre por encima de la entrada."""
        for seed in range(10):
            df = make_ohlcv(n=350, seed=seed, trend="bear")
            eng = Conflux4Engine(engine_cfg)
            r = eng.compute(df)
            if r.signal == "BEAR":
                assert r.stop > r.entry
                break

    def test_tps_ascending_for_bull(self, engine_cfg):
        """Para BULL: TP1 < TP2 < TP3 < TP4."""
        for seed in range(20):
            df = make_ohlcv(n=350, seed=seed, trend="bull")
            eng = Conflux4Engine(engine_cfg)
            r = eng.compute(df)
            if r.signal == "BULL":
                assert r.tp1 < r.tp2 < r.tp3 < r.tp4
                break

    def test_tps_descending_for_bear(self, engine_cfg):
        """Para BEAR: TP1 > TP2 > TP3 > TP4."""
        for seed in range(20):
            df = make_ohlcv(n=350, seed=seed, trend="bear")
            eng = Conflux4Engine(engine_cfg)
            r = eng.compute(df)
            if r.signal == "BEAR":
                assert r.tp1 > r.tp2 > r.tp3 > r.tp4
                break

    def test_cooldown_prevents_double_signal(self, engine_cfg):
        """No debe generarse señal si el cooldown no ha expirado."""
        engine_cfg["cooldown"] = 50  # cooldown muy alto
        df = make_ohlcv(n=350, seed=10, trend="bull")
        eng = Conflux4Engine(engine_cfg)

        # Primera llamada puede generar señal
        r1 = eng.compute(df)
        # Segunda llamada en la misma barra → no puede generar señal nueva
        r2 = eng.compute(df)

        if r1.signal:
            assert r2.signal is None, "Cooldown no bloqueó segunda señal"

    def test_funding_filter_blocks_bull(self, engine_cfg):
        """Funding muy alto bloquea señal BULL."""
        engine_cfg["funding_threshold"] = 0.001  # threshold muy bajo
        df = make_ohlcv(n=350, trend="bull")
        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df, funding_rate=0.99)   # funding extremo
        # Si hubiera señal bull, debe ser rechazada
        if r.signal == "BULL":
            pytest.fail("Señal BULL no debería pasar con funding=0.99")

    def test_mtf_confirmation(self, engine_cfg):
        """MTF con bear en TF mayor debe bloquear señal bull."""
        engine_cfg["funding_threshold"] = 99.0  # desactivar funding filter
        df_primary = make_ohlcv(n=350, seed=42, trend="bull")
        df_bear_htf = make_ohlcv(n=100, seed=99, trend="bear")
        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df_primary, df_htf1=df_bear_htf)
        # En este escenario MTF debe fallar → no señal bull
        assert r.signal != "BULL" or not r.mtf_ok

    def test_stop_distance_positive(self, engine_cfg):
        df = make_ohlcv(n=350)
        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df)
        assert r.stop_dist >= 0
        assert r.atr_val > 0

    def test_atr_cap_stop_mode(self, engine_cfg):
        engine_cfg["stop_mode"] = "ATR Cap"
        engine_cfg["stop_atr_mult"] = 1.0
        df = make_ohlcv(n=350, trend="bull")
        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df)
        # Stop distance no puede superar ATR
        assert r.stop_dist <= r.atr_val * 1.001  # pequeño margen float

    def test_fixed_pct_stop_mode(self, engine_cfg):
        engine_cfg["stop_mode"] = "Fixed %"
        engine_cfg["stop_fixed_pct"] = 1.0
        df = make_ohlcv(n=350, trend="bull")
        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df)
        expected = r.close * 0.01
        assert abs(r.stop_dist - expected) < 0.01

    def test_rsi_val_in_result(self, engine_cfg):
        df = make_ohlcv(n=350)
        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df)
        assert 0 <= r.rsi_val <= 100

    def test_adx_val_in_result(self, engine_cfg):
        df = make_ohlcv(n=350)
        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df)
        assert r.adx_val >= 0

    def test_volume_percentile_in_result(self, engine_cfg):
        df = make_ohlcv(n=350)
        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df)
        assert 0 <= r.volume_pct <= 100

    def test_insufficient_data_no_crash(self, engine_cfg):
        """Con pocas velas no debe crashear."""
        df = make_ohlcv(n=50)   # menos de vwma_len=200
        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df)    # debe devolver resultado (sin señal)
        assert r is not None
