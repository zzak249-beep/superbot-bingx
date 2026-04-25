"""
Tests — Telegram Notifier
Verifica formato de mensajes sin hacer llamadas reales a la API.
"""
import pytest
from unittest.mock import patch, MagicMock
from telegram_notifier import TelegramNotifier
from conflux4 import SignalResult


def make_signal_result(signal="BULL", quality=8, trend="BULL",
                        close=67245.0, stop=66890.0,
                        tp1=67422.0, tp2=67600.0, tp3=67954.0, tp4=68309.0,
                        rsi_val=61.3, adx_val=28.7, confluence=4,
                        volume_pct=65.0, st_val=66800.0,
                        mtf_ok=True, funding_ok=True,
                        atr_val=355.0, stop_dist=355.0):
    return SignalResult(
        signal=signal, quality=quality, trend=trend, close=close,
        atr_val=atr_val, stop_dist=stop_dist,
        entry=close, stop=stop, tp1=tp1, tp2=tp2, tp3=tp3, tp4=tp4,
        rsi_val=rsi_val, adx_val=adx_val, confluence=confluence,
        volume_pct=volume_pct, st_val=st_val,
        mtf_ok=mtf_ok, funding_ok=funding_ok,
    )


@pytest.fixture
def tg():
    return TelegramNotifier("fake_token", "fake_chat_id")


# ══════════════════════════════════════════════════════════════════
# FORMATO DE MENSAJES
# ══════════════════════════════════════════════════════════════════

class TestMessageFormat:
    def test_bull_signal_contains_long(self, tg):
        r = make_signal_result(signal="BULL")
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.signal(r, "BTC-USDT", "15m", "Daytrader")
            msg = mock_send.call_args[0][0]
        assert "LONG" in msg
        assert "BULL" in msg

    def test_bear_signal_contains_short(self, tg):
        r = make_signal_result(signal="BEAR", trend="BEAR",
                               close=3000.0, stop=3100.0,
                               tp1=2900.0, tp2=2800.0, tp3=2600.0, tp4=2400.0)
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.signal(r, "ETH-USDT", "15m", "Daytrader")
            msg = mock_send.call_args[0][0]
        assert "SHORT" in msg
        assert "BEAR" in msg

    def test_signal_contains_all_tps(self, tg):
        r = make_signal_result()
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.signal(r, "BTC-USDT", "15m", "Daytrader")
            msg = mock_send.call_args[0][0]
        assert "TP1" in msg
        assert "TP2" in msg
        assert "TP3" in msg
        assert "TP4" in msg

    def test_signal_contains_entry_and_stop(self, tg):
        r = make_signal_result()
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.signal(r, "BTC-USDT", "15m", "Daytrader")
            msg = mock_send.call_args[0][0]
        assert "Entrada" in msg or "ntrada" in msg
        assert "Stop" in msg

    def test_signal_contains_symbol(self, tg):
        r = make_signal_result()
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.signal(r, "SOL-USDT", "1h", "Swing")
            msg = mock_send.call_args[0][0]
        assert "SOL-USDT" in msg

    def test_signal_contains_rsi_adx(self, tg):
        r = make_signal_result(rsi_val=63.5, adx_val=31.2)
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.signal(r, "BTC-USDT", "15m", "Daytrader")
            msg = mock_send.call_args[0][0]
        assert "63.5" in msg or "63" in msg
        assert "31.2" in msg or "31" in msg

    def test_signal_with_risk_decision(self, tg):
        from risk_manager import RiskDecision
        r = make_signal_result()
        rd = RiskDecision(approved=True, reason="OK",
                          position_usdt=150.0, risk_pct=1.5, Kelly_fraction=0.015)
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.signal(r, "BTC-USDT", "15m", "Daytrader", risk_dec=rd)
            msg = mock_send.call_args[0][0]
        assert "150" in msg or "posici" in msg.lower()

    def test_signal_contains_quality_stars(self, tg):
        r = make_signal_result(quality=7)
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.signal(r, "BTC-USDT", "15m", "Daytrader", quality=7)
            msg = mock_send.call_args[0][0]
        assert "⭐" in msg
        assert "7" in msg

    def test_signal_mtf_ok_emoji(self, tg):
        r = make_signal_result(mtf_ok=True)
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.signal(r, "BTC-USDT", "15m", "Daytrader")
            msg = mock_send.call_args[0][0]
        assert "✅" in msg

    def test_signal_mtf_fail_emoji(self, tg):
        r = make_signal_result(mtf_ok=False)
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.signal(r, "BTC-USDT", "15m", "Daytrader")
            msg = mock_send.call_args[0][0]
        assert "❌" in msg

    def test_trade_update_message(self, tg):
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.trade_update("BTC-USDT", ["🎯 TP1 @ 67422", "🔒 Stop → BE"], 67500.0, pnl_approx=12.5)
            msg = mock_send.call_args[0][0]
        assert "BTC-USDT" in msg
        assert "TP1" in msg
        assert "12.5" in msg or "12" in msg

    def test_trade_update_no_events_doesnt_send(self, tg):
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.trade_update("BTC-USDT", [], 67000.0)
        mock_send.assert_not_called()

    def test_trade_closed_win(self, tg):
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.trade_closed("BTC-USDT", "BULL", 67000.0, 67500.0, 25.0, "TP2")
            msg = mock_send.call_args[0][0]
        assert "✅" in msg
        assert "BTC-USDT" in msg
        assert "25" in msg

    def test_trade_closed_loss(self, tg):
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.trade_closed("BTC-USDT", "BULL", 67000.0, 66500.0, -18.0, "STOP")
            msg = mock_send.call_args[0][0]
        assert "❌" in msg

    def test_performance_dashboard_contains_balance(self, tg):
        summary = {
            "balance": 1075.5, "peak": 1100.0, "drawdown_pct": 2.2,
            "total_pnl": 75.5, "today_pnl": 20.0, "week_pnl": 50.0,
            "winrate": 0.65, "all_trades": 10, "open_positions": 1,
        }
        from conflux4 import SignalResult
        r = make_signal_result(signal=None, trend="BULL")
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.performance_dashboard(summary, {"BTC-USDT": r})
            msg = mock_send.call_args[0][0]
        assert "1075" in msg
        assert "65" in msg or "winrate" in msg.lower() or "Winrate" in msg

    def test_startup_message(self, tg):
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.startup(["BTC-USDT", "ETH-USDT"], "15m", "Daytrader", 1000.0)
            msg = mock_send.call_args[0][0]
        assert "BTC-USDT" in msg
        assert "ETH-USDT" in msg
        assert "1000" in msg

    def test_error_message(self, tg):
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.error("Algo salió mal")
            msg = mock_send.call_args[0][0]
        assert "ERROR" in msg
        assert "Algo salió mal" in msg

    def test_risk_alert(self, tg):
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.risk_alert("Drawdown elevado", "DD actual: 12%")
            msg = mock_send.call_args[0][0]
        assert "ALERTA" in msg or "alerta" in msg.lower()
        assert "12%" in msg


# ══════════════════════════════════════════════════════════════════
# SEND — MANEJO DE ERRORES DE RED
# ══════════════════════════════════════════════════════════════════

class TestSendResilience:
    def test_send_returns_false_on_http_error(self, tg):
        import httpx
        with patch("httpx.post", side_effect=httpx.ConnectError("timeout")):
            result = tg.send("test message")
        assert result is False

    def test_send_returns_true_on_success(self, tg):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        with patch("httpx.post", return_value=mock_response):
            result = tg.send("test message")
        assert result is True

    def test_signal_doesnt_crash_on_send_failure(self, tg):
        """Un error de red no crashea el bot."""
        import httpx
        r = make_signal_result()
        with patch("httpx.post", side_effect=Exception("network error")):
            # No debe lanzar excepción
            tg.signal(r, "BTC-USDT", "15m", "Daytrader")
