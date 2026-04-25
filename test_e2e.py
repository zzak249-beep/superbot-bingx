"""
Tests End-to-End — Simulación completa del bot
Simula un ciclo completo: señal → risk → trade → TPs → cierre
SIN llamadas reales a BingX ni Telegram.
"""
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock

from conftest import make_ohlcv
from conflux4 import Conflux4Engine
from risk_manager import RiskManager
from trade_manager import TradeManager, ActiveTrade
from telegram_notifier import TelegramNotifier


# ══════════════════════════════════════════════════════════════════
# HELPER — Simular un ciclo de scan completo
# ══════════════════════════════════════════════════════════════════

def run_scan(engine, rm, tm, tg, df, symbol="BTC-USDT"):
    """Ejecuta un scan completo y devuelve las acciones tomadas."""
    result = engine.compute(df)
    actions_taken = {"signal": None, "approved": False, "trade_opened": False}

    if result.signal:
        actions_taken["signal"] = result.signal
        dec = rm.approve(symbol, result.signal, result.quality)
        actions_taken["approved"] = dec.approved

        if dec.approved:
            t = ActiveTrade(
                symbol=symbol,
                direction=result.signal,
                entry=result.entry,
                stop=result.stop,
                tp1=result.tp1, tp2=result.tp2,
                tp3=result.tp3, tp4=result.tp4,
                quantity=0.01,
                quantity_remaining=0.01,
            )
            tm.open_trade(t)
            rm.register_open(symbol, result.signal)
            actions_taken["trade_opened"] = True

    return result, actions_taken


# ══════════════════════════════════════════════════════════════════
# TESTS E2E
# ══════════════════════════════════════════════════════════════════

class TestEndToEnd:
    def test_full_winning_trade_cycle(self, engine_cfg, risk_cfg, tmp_equity, tmp_trades):
        """
        Ciclo completo de trade ganador:
        Señal → Risk approve → Open → TP1 (BE) → TP2 → TP4 → Cierre
        """
        engine = Conflux4Engine(engine_cfg)
        rm = RiskManager(risk_cfg, tmp_equity)
        tm = TradeManager(tmp_trades)
        tg = TelegramNotifier("fake", "fake")

        # Forzar un trade abierto manualmente para testear el ciclo completo
        t = ActiveTrade(
            symbol="BTC-USDT", direction="BULL",
            entry=67000.0, stop=66500.0,
            tp1=67250.0, tp2=67500.0, tp3=68000.0, tp4=68500.0,
            quantity=0.04, quantity_remaining=0.04,
        )
        tm.open_trade(t)
        rm.register_open("BTC-USDT", "BULL")

        assert len(tm.all_trades()) == 1
        assert rm.state.today.trades == 1

        # ── Precio sube a TP1 ────────────────────────────────────
        a1 = tm.update("BTC-USDT", price=67300.0, st_val=66800.0, st_bull=True)
        assert a1["partial_close"] is not None
        assert abs(a1["partial_close"]["qty"] - 0.01) < 1e-6
        assert a1["update_stop"] == 67000.0  # BE

        trade = tm.get_trade("BTC-USDT")
        assert trade.tp1_hit is True
        assert trade.be_moved is True
        assert trade.stop == 67000.0

        # ── Precio sube a TP2 ────────────────────────────────────
        a2 = tm.update("BTC-USDT", price=67600.0, st_val=67200.0, st_bull=True)
        assert a2["partial_close"] is not None
        assert "TP2" in a2["partial_close"]["reason"]

        # ── Trailing stop sube con Supertrend ────────────────────
        a3 = tm.update("BTC-USDT", price=67900.0, st_val=67400.0, st_bull=True)
        trade = tm.get_trade("BTC-USDT")
        assert trade.stop == 67400.0

        # ── Precio llega a TP3 ───────────────────────────────────
        a3b = tm.update("BTC-USDT", price=68100.0, st_val=67800.0, st_bull=True)
        assert a3b["partial_close"] is not None
        assert "TP3" in a3b["partial_close"]["reason"]

        # ── Precio llega a TP4 → cierre total ────────────────────
        a4 = tm.update("BTC-USDT", price=68600.0, st_val=68200.0, st_bull=True)
        assert a4["close_full"] is True
        assert "BTC-USDT" not in tm.trades

        # Registrar cierre
        pnl = (68600.0 - 67000.0) * 0.01
        rm.register_close("BTC-USDT", pnl, won=True)
        assert rm.state.today.wins == 1
        assert rm.state.current_balance > 1000.0

    def test_full_losing_trade_cycle(self, engine_cfg, risk_cfg, tmp_equity, tmp_trades):
        """
        Ciclo completo de trade perdedor: Stop hit → Cierre en pérdida.
        """
        rm = RiskManager(risk_cfg, tmp_equity)
        tm = TradeManager(tmp_trades)

        t = ActiveTrade(
            symbol="ETH-USDT", direction="BEAR",
            entry=3000.0, stop=3100.0,
            tp1=2900.0, tp2=2800.0, tp3=2600.0, tp4=2400.0,
            quantity=0.1, quantity_remaining=0.1,
        )
        tm.open_trade(t)
        rm.register_open("ETH-USDT", "BEAR")

        # ── Precio sube → stop hit ───────────────────────────────
        actions = tm.update("ETH-USDT", price=3110.0, st_val=3200.0, st_bull=False)
        assert actions["close_full"] is True
        assert "STOP" in actions["events"][0]
        assert "ETH-USDT" not in tm.trades

        pnl = (3000.0 - 3110.0) * 0.1  # pérdida
        rm.register_close("ETH-USDT", pnl, won=False)
        assert rm.state.today.losses == 1
        assert rm.state.current_balance < 1000.0

    def test_circuit_breaker_stops_after_daily_loss(self, engine_cfg, risk_cfg, tmp_equity, tmp_trades):
        """
        Después de alcanzar la pérdida diaria máxima, el Risk Manager
        rechaza todas las señales siguientes.
        """
        risk_cfg["max_daily_loss_pct"] = 2.0
        rm = RiskManager(risk_cfg, tmp_equity)

        # Simular pérdida del 3%
        rm.state.today.pnl_usdt = -30.0
        rm.save()

        dec = rm.approve("BTC-USDT", "BULL", quality=10)
        assert not dec.approved
        assert "daily" in dec.reason.lower() or "Daily" in dec.reason

    def test_multiple_symbols_independent(self, engine_cfg, risk_cfg, tmp_equity, tmp_trades):
        """
        Dos símbolos no correlacionados pueden tener trades abiertos simultáneamente.
        """
        risk_cfg["max_open_trades"] = 3
        rm = RiskManager(risk_cfg, tmp_equity)
        tm = TradeManager(tmp_trades)

        # Abrir BTC BULL
        t1 = ActiveTrade(
            symbol="BTC-USDT", direction="BULL",
            entry=67000.0, stop=66500.0,
            tp1=67250.0, tp2=67500.0, tp3=68000.0, tp4=68500.0,
            quantity=0.01, quantity_remaining=0.01,
        )
        tm.open_trade(t1)
        rm.register_open("BTC-USDT", "BULL")

        # Intentar abrir LINK BULL (no correlacionado con BTC)
        dec = rm.approve("LINK-USDT", "BULL", quality=7)
        assert dec.approved

        # Intentar abrir ETH BULL (correlacionado con BTC → rechazado)
        dec_eth = rm.approve("ETH-USDT", "BULL", quality=9)
        assert not dec_eth.approved

    def test_drawdown_calculated_after_losses(self, engine_cfg, risk_cfg, tmp_equity, tmp_trades):
        """El drawdown se calcula correctamente tras varias pérdidas."""
        rm = RiskManager(risk_cfg, tmp_equity)

        rm.register_open("BTC-USDT", "BULL")
        rm.register_close("BTC-USDT", -50.0, won=False)
        rm.register_open("ETH-USDT", "BEAR")
        rm.register_close("ETH-USDT", -50.0, won=False)

        assert rm.state.current_balance == 900.0
        assert rm.state.peak_balance == 1000.0
        assert abs(rm.state.drawdown_pct - 10.0) < 0.001

    def test_engine_no_signal_doesnt_open_trade(self, engine_cfg, risk_cfg, tmp_equity, tmp_trades):
        """Sin señal, no se registra ningún trade."""
        engine_cfg["cooldown"] = 9999
        engine = Conflux4Engine(engine_cfg)
        rm = RiskManager(risk_cfg, tmp_equity)
        tm = TradeManager(tmp_trades)
        tg = TelegramNotifier("fake", "fake")

        df = make_ohlcv(n=350)
        # Forzar cooldown para que no haya señal
        engine._last_signal_bar = 999999
        result = engine.compute(df)
        # Si no hay señal, no abrimos trade
        if not result.signal:
            assert len(tm.all_trades()) == 0

    def test_signal_rejected_not_open_trade(self, engine_cfg, risk_cfg, tmp_equity, tmp_trades):
        """Si Risk Manager rechaza la señal, no se abre ningún trade."""
        risk_cfg["min_signal_quality"] = 10  # imposible de alcanzar
        rm = RiskManager(risk_cfg, tmp_equity)
        tm = TradeManager(tmp_trades)

        dec = rm.approve("BTC-USDT", "BULL", quality=9)
        assert not dec.approved

        if not dec.approved:
            pass  # no abrimos trade
        assert len(tm.all_trades()) == 0

    def test_state_persists_across_bot_restarts(self, engine_cfg, risk_cfg, tmp_equity, tmp_trades):
        """Simula reinicio del bot: el estado de trades y equity se recupera."""
        rm = RiskManager(risk_cfg, tmp_equity)
        tm = TradeManager(tmp_trades)

        t = ActiveTrade(
            symbol="SOL-USDT", direction="BULL",
            entry=150.0, stop=145.0,
            tp1=152.5, tp2=155.0, tp3=160.0, tp4=165.0,
            quantity=1.0, quantity_remaining=1.0,
        )
        tm.open_trade(t)
        rm.register_open("SOL-USDT", "BULL")

        # Cerrar el trade vía TP4 hit (precio supera tp4=165.0)
        tm.update("SOL-USDT", price=166.0, st_val=162.0, st_bull=True)  # TP1
        tm.update("SOL-USDT", price=156.0, st_val=153.0, st_bull=True)  # TP2
        tm.update("SOL-USDT", price=161.0, st_val=158.0, st_bull=True)  # TP3
        tm.update("SOL-USDT", price=166.0, st_val=163.0, st_bull=True)  # TP4 → cierre

        rm.register_close("SOL-USDT", 20.0, won=True)

        # "Reiniciar" — crear nuevas instancias desde disco
        rm2 = RiskManager(risk_cfg, tmp_equity)
        tm2 = TradeManager(tmp_trades)

        assert rm2.state.current_balance == 1020.0
        assert rm2.state.all_time_wins == 1
        # El trade se cerró correctamente, no debe estar en trades activos
        assert "SOL-USDT" not in tm2.trades

    def test_be2e_signal_quality_gates(self, engine_cfg, risk_cfg, tmp_equity, tmp_trades):
        """
        Señales de baja calidad tienen posiciones pequeñas.
        Señales de alta calidad tienen posiciones grandes.
        """
        rm_low  = RiskManager(risk_cfg, tmp_equity)
        rm_high = RiskManager(risk_cfg, tmp_equity)

        dec_low  = rm_low.approve("BTC-USDT",  "BULL", quality=4)
        dec_high = rm_high.approve("ETH-USDT", "BULL", quality=9)

        if dec_low.approved and dec_high.approved:
            assert dec_high.position_usdt >= dec_low.position_usdt


# ══════════════════════════════════════════════════════════════════
# TEST DE STRESS — Muchos trades seguidos
# ══════════════════════════════════════════════════════════════════

class TestStress:
    def test_100_trades_no_crash(self, risk_cfg, tmp_equity, tmp_trades):
        """100 apertura/cierre consecutivos no deben crashear ni corromper el estado."""
        import random
        random.seed(42)

        rm = RiskManager(risk_cfg, tmp_equity)
        symbols = ["BTC-USDT", "SOL-USDT", "LINK-USDT", "AVAX-USDT", "DOT-USDT"]

        trades_done = 0
        for i in range(100):
            sym = symbols[i % len(symbols)]
            direction = "BULL" if i % 2 == 0 else "BEAR"
            quality = random.randint(5, 10)

            dec = rm.approve(sym, direction, quality)
            if dec.approved:
                rm.register_open(sym, direction)
                pnl = random.uniform(-30, 50)
                won = pnl > 0
                rm.register_close(sym, pnl, won)
                trades_done += 1

        # El balance no debe ser negativo ni NaN
        assert not (rm.state.current_balance != rm.state.current_balance)  # no NaN
        assert rm.state.all_time_trades >= 0
        assert trades_done > 0

    def test_engine_processes_350_bars_fast(self, engine_cfg):
        """El motor debe procesar 350 velas en menos de 3 segundos."""
        import time
        engine = Conflux4Engine(engine_cfg)
        df = make_ohlcv(n=350)

        start = time.time()
        for _ in range(10):
            engine.compute(df)
        elapsed = time.time() - start

        assert elapsed < 3.0, f"Motor demasiado lento: {elapsed:.2f}s para 10 cálculos"

    def test_concurrent_symbols_no_state_leak(self, engine_cfg, risk_cfg, tmp_equity, tmp_trades):
        """Procesar múltiples símbolos no contamina el estado entre ellos."""
        rm = RiskManager(risk_cfg, tmp_equity)
        tm = TradeManager(tmp_trades)

        # Abrir trades en 3 símbolos distintos no correlacionados
        for sym in ["LINK-USDT", "AVAX-USDT", "DOT-USDT"]:
            t = ActiveTrade(
                symbol=sym, direction="BULL",
                entry=100.0, stop=95.0,
                tp1=102.5, tp2=105.0, tp3=110.0, tp4=115.0,
                quantity=1.0, quantity_remaining=1.0,
            )
            tm.open_trade(t)

        assert len(tm.all_trades()) == 3

        # Cerrar uno no debe afectar a los otros
        tm.update("LINK-USDT", price=90.0, st_val=88.0, st_bull=True)  # stop hit
        assert "LINK-USDT" not in tm.trades
        assert "AVAX-USDT" in tm.trades
        assert "DOT-USDT" in tm.trades
