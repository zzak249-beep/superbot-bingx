"""
Tests — Risk Manager
Cubre: Kelly sizing, circuit breakers, correlaciones, sesión, drawdown
"""
import pytest
import json
import time
from risk_manager import RiskManager, DayStats, EquityState


# ══════════════════════════════════════════════════════════════════
# KELLY Y TAMAÑO DE POSICIÓN
# ══════════════════════════════════════════════════════════════════

class TestKellySizing:
    def test_approve_basic(self, risk_cfg, tmp_equity):
        rm = RiskManager(risk_cfg, tmp_equity)
        dec = rm.approve("BTC-USDT", "BULL", quality=8)
        assert dec.approved
        assert dec.position_usdt > 0
        assert dec.risk_pct > 0
        assert dec.Kelly_fraction > 0

    def test_position_never_exceeds_max(self, risk_cfg, tmp_equity):
        risk_cfg["max_position_usdt"] = 50.0
        rm = RiskManager(risk_cfg, tmp_equity)
        dec = rm.approve("BTC-USDT", "BULL", quality=10)
        assert dec.position_usdt <= 50.0

    def test_higher_quality_larger_position(self, risk_cfg, tmp_equity):
        rm_low  = RiskManager(risk_cfg, tmp_equity)
        rm_high = RiskManager(risk_cfg, tmp_equity)
        dec_low  = rm_low.approve("BTC-USDT",  "BULL", quality=4)
        dec_high = rm_high.approve("ETH-USDT", "BULL", quality=9)
        assert dec_high.position_usdt >= dec_low.position_usdt

    def test_kelly_fraction_within_max_risk(self, risk_cfg, tmp_equity):
        risk_cfg["max_risk_per_trade_pct"] = 1.5
        rm = RiskManager(risk_cfg, tmp_equity)
        dec = rm.approve("BTC-USDT", "BULL", quality=10)
        assert dec.risk_pct <= 1.5 + 0.001  # pequeño margen float

    def test_risk_pct_positive(self, risk_cfg, tmp_equity):
        rm = RiskManager(risk_cfg, tmp_equity)
        dec = rm.approve("BTC-USDT", "BULL", quality=7)
        assert dec.risk_pct > 0


# ══════════════════════════════════════════════════════════════════
# CIRCUIT BREAKERS
# ══════════════════════════════════════════════════════════════════

class TestCircuitBreakers:
    def test_daily_loss_blocks_trading(self, risk_cfg, tmp_equity):
        """Si la pérdida diaria supera el límite, no se aprueban más trades."""
        risk_cfg["max_daily_loss_pct"] = 2.0
        rm = RiskManager(risk_cfg, tmp_equity)
        # Simular pérdida diaria del 5%
        rm.state.today.pnl_usdt = -50.0  # -50 sobre 1000 = -5%
        rm.save()

        dec = rm.approve("BTC-USDT", "BULL", quality=9)
        assert not dec.approved
        assert "daily" in dec.reason.lower() or "Daily" in dec.reason

    def test_drawdown_blocks_trading(self, risk_cfg, tmp_equity):
        """Si el drawdown supera el límite, no se aprueban trades."""
        risk_cfg["max_drawdown_pct"] = 10.0
        rm = RiskManager(risk_cfg, tmp_equity)
        rm.state.peak_balance = 1000.0
        rm.state.current_balance = 850.0  # -15% drawdown
        rm.save()

        dec = rm.approve("BTC-USDT", "BULL", quality=9)
        assert not dec.approved
        assert "drawdown" in dec.reason.lower() or "Drawdown" in dec.reason

    def test_weekly_loss_blocks_trading(self, risk_cfg, tmp_equity):
        """Pérdida semanal excesiva bloquea trades."""
        risk_cfg["max_weekly_loss_pct"] = 5.0
        rm = RiskManager(risk_cfg, tmp_equity)
        rm.state.week_pnl = -80.0  # -8% sobre 1000
        rm.save()

        dec = rm.approve("BTC-USDT", "BULL", quality=9)
        assert not dec.approved

    def test_max_open_trades_blocks_new(self, risk_cfg, tmp_equity):
        """No se pueden abrir más trades del máximo configurado."""
        risk_cfg["max_open_trades"] = 2
        rm = RiskManager(risk_cfg, tmp_equity)
        rm.state.open_positions = {"BTC-USDT": "BULL", "ETH-USDT": "BULL"}
        rm.save()

        dec = rm.approve("SOL-USDT", "BULL", quality=9)
        assert not dec.approved
        assert "simultáneos" in dec.reason or "máx" in dec.reason.lower() or "max" in dec.reason.lower()

    def test_minimum_quality_blocks_low_quality(self, risk_cfg, tmp_equity):
        """Señales por debajo del umbral de calidad son rechazadas."""
        risk_cfg["min_signal_quality"] = 6
        rm = RiskManager(risk_cfg, tmp_equity)
        dec = rm.approve("BTC-USDT", "BULL", quality=3)
        assert not dec.approved
        assert "calidad" in dec.reason.lower() or "quality" in dec.reason.lower() or "Calidad" in dec.reason

    def test_duplicate_symbol_blocked(self, risk_cfg, tmp_equity):
        """No se puede abrir una segunda posición en el mismo par."""
        rm = RiskManager(risk_cfg, tmp_equity)
        rm.state.open_positions = {"BTC-USDT": "BULL"}
        rm.save()

        dec = rm.approve("BTC-USDT", "BULL", quality=9)
        assert not dec.approved


# ══════════════════════════════════════════════════════════════════
# CORRELACIONES
# ══════════════════════════════════════════════════════════════════

class TestCorrelationBlock:
    def test_correlated_same_direction_blocked(self, risk_cfg, tmp_equity):
        """BTC y ETH en el mismo lado no pueden coexistir."""
        rm = RiskManager(risk_cfg, tmp_equity)
        rm.state.open_positions = {"BTC-USDT": "BULL"}
        rm.save()

        dec = rm.approve("ETH-USDT", "BULL", quality=9)
        assert not dec.approved
        assert "correlación" in dec.reason.lower() or "Correlación" in dec.reason

    def test_correlated_opposite_direction_allowed(self, risk_cfg, tmp_equity):
        """BTC long y ETH short pueden coexistir (cobertura)."""
        rm = RiskManager(risk_cfg, tmp_equity)
        rm.state.open_positions = {"BTC-USDT": "BULL"}
        rm.save()

        dec = rm.approve("ETH-USDT", "BEAR", quality=7)
        assert dec.approved

    def test_uncorrelated_pairs_allowed(self, risk_cfg, tmp_equity):
        """Pares no correlacionados pueden abrirse simultáneamente."""
        rm = RiskManager(risk_cfg, tmp_equity)
        rm.state.open_positions = {"BTC-USDT": "BULL"}
        rm.save()

        # LINK no está en la lista de correlaciones con BTC
        dec = rm.approve("LINK-USDT", "BULL", quality=7)
        assert dec.approved


# ══════════════════════════════════════════════════════════════════
# REGISTRO DE TRADES Y PERSISTENCIA
# ══════════════════════════════════════════════════════════════════

class TestTradeRegistration:
    def test_register_open_increments_count(self, risk_cfg, tmp_equity):
        rm = RiskManager(risk_cfg, tmp_equity)
        rm.register_open("BTC-USDT", "BULL")
        assert "BTC-USDT" in rm.state.open_positions
        assert rm.state.today.trades == 1
        assert rm.state.all_time_trades == 1

    def test_register_close_win_updates_balance(self, risk_cfg, tmp_equity):
        rm = RiskManager(risk_cfg, tmp_equity)
        rm.register_open("BTC-USDT", "BULL")
        rm.register_close("BTC-USDT", pnl_usdt=50.0, won=True)

        assert rm.state.current_balance == 1050.0
        assert rm.state.today.wins == 1
        assert rm.state.today.losses == 0
        assert "BTC-USDT" not in rm.state.open_positions

    def test_register_close_loss_updates_balance(self, risk_cfg, tmp_equity):
        rm = RiskManager(risk_cfg, tmp_equity)
        rm.register_open("BTC-USDT", "BULL")
        rm.register_close("BTC-USDT", pnl_usdt=-30.0, won=False)

        assert rm.state.current_balance == 970.0
        assert rm.state.today.losses == 1

    def test_peak_balance_updates_on_profit(self, risk_cfg, tmp_equity):
        rm = RiskManager(risk_cfg, tmp_equity)
        rm.register_open("BTC-USDT", "BULL")
        rm.register_close("BTC-USDT", pnl_usdt=200.0, won=True)
        assert rm.state.peak_balance == 1200.0

    def test_peak_not_updated_on_loss(self, risk_cfg, tmp_equity):
        rm = RiskManager(risk_cfg, tmp_equity)
        rm.register_open("BTC-USDT", "BULL")
        rm.register_close("BTC-USDT", pnl_usdt=-100.0, won=False)
        assert rm.state.peak_balance == 1000.0  # sin cambio

    def test_drawdown_calculated_correctly(self, risk_cfg, tmp_equity):
        rm = RiskManager(risk_cfg, tmp_equity)
        rm.state.peak_balance = 1000.0
        rm.state.current_balance = 800.0
        assert abs(rm.state.drawdown_pct - 20.0) < 0.001

    def test_winrate_calculation(self, risk_cfg, tmp_equity):
        rm = RiskManager(risk_cfg, tmp_equity)
        rm.register_open("BTC-USDT", "BULL")
        rm.register_close("BTC-USDT", 50.0, won=True)
        rm.register_open("ETH-USDT", "BULL")
        rm.register_close("ETH-USDT", -20.0, won=False)
        rm.register_open("SOL-USDT", "BULL")
        rm.register_close("SOL-USDT", 30.0, won=True)

        assert abs(rm.state.today.winrate - 2/3) < 0.001

    def test_persistence_survives_reload(self, risk_cfg, tmp_equity):
        """El estado persiste al recargar desde disco."""
        rm = RiskManager(risk_cfg, tmp_equity)
        rm.register_open("BTC-USDT", "BULL")
        rm.register_close("BTC-USDT", 75.0, won=True)

        # Recargar desde disco
        rm2 = RiskManager(risk_cfg, tmp_equity)
        assert rm2.state.current_balance == 1075.0
        assert rm2.state.all_time_wins == 1

    def test_summary_keys_present(self, risk_cfg, tmp_equity):
        rm = RiskManager(risk_cfg, tmp_equity)
        s = rm.summary()
        for key in ["balance", "peak", "drawdown_pct", "total_pnl",
                    "today_pnl", "week_pnl", "winrate", "all_trades", "open_positions"]:
            assert key in s, f"Clave faltante en summary: {key}"

    def test_new_day_resets_daily_stats(self, risk_cfg, tmp_equity):
        """Al cambiar de día, las estadísticas diarias se reinician."""
        rm = RiskManager(risk_cfg, tmp_equity)
        rm.state.today.pnl_usdt = -999.0
        rm.state.today.date = "2000-01-01"  # fecha pasada
        rm.save()

        rm2 = RiskManager(risk_cfg, tmp_equity)
        rm2._ensure_today()
        assert rm2.state.today.pnl_usdt == 0.0


# ══════════════════════════════════════════════════════════════════
# FILTRO DE SESIÓN
# ══════════════════════════════════════════════════════════════════

class TestSessionFilter:
    def test_session_disabled_always_passes(self, risk_cfg, tmp_equity):
        risk_cfg["use_session_filter"] = False
        rm = RiskManager(risk_cfg, tmp_equity)
        # Debe aprobar independientemente de la hora
        result = rm._session_filter()
        assert result is True

    def test_avoid_hours_blocked(self, risk_cfg, tmp_equity):
        """Horas configuradas como evitadas deben bloquear."""
        from unittest.mock import patch
        from datetime import datetime, timezone

        risk_cfg["use_session_filter"] = True
        risk_cfg["avoid_hours_utc"] = [2, 3, 4]
        rm = RiskManager(risk_cfg, tmp_equity)

        # Simular hora 3:00 UTC
        mock_time = datetime(2024, 1, 15, 3, 0, 0, tzinfo=timezone.utc)
        with patch("risk_manager.datetime") as mock_dt:
            mock_dt.now.return_value = mock_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = rm._session_filter()

        assert result is False

    def test_trading_hours_pass(self, risk_cfg, tmp_equity):
        """Horas fuera de la lista de evitadas deben pasar."""
        from unittest.mock import patch
        from datetime import datetime, timezone

        risk_cfg["use_session_filter"] = True
        risk_cfg["avoid_hours_utc"] = [0, 1, 2, 3]
        rm = RiskManager(risk_cfg, tmp_equity)

        mock_time = datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)  # 14:00 UTC
        with patch("risk_manager.datetime") as mock_dt:
            mock_dt.now.return_value = mock_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = rm._session_filter()

        assert result is True
