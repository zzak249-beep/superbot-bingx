"""
Tests — Trade Manager
Cubre: apertura/cierre, salidas parciales, BE move, trailing stop, persistencia
"""
import pytest
import json
from trade_manager import TradeManager, ActiveTrade


def make_trade(symbol="BTC-USDT", direction="BULL", entry=67000.0, stop=66500.0,
               tp1=67250.0, tp2=67500.0, tp3=68000.0, tp4=68500.0, qty=0.01):
    return ActiveTrade(
        symbol=symbol, direction=direction,
        entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, tp4=tp4,
        quantity=qty, quantity_remaining=qty,
    )


def make_bear_trade(symbol="ETH-USDT", entry=3000.0, stop=3100.0,
                    tp1=2900.0, tp2=2800.0, tp3=2600.0, tp4=2400.0, qty=0.1):
    return ActiveTrade(
        symbol=symbol, direction="BEAR",
        entry=entry, stop=stop,
        tp1=tp1, tp2=tp2, tp3=tp3, tp4=tp4,
        quantity=qty, quantity_remaining=qty,
    )


# ══════════════════════════════════════════════════════════════════
# APERTURA Y CIERRE
# ══════════════════════════════════════════════════════════════════

class TestOpenClose:
    def test_open_trade_stored(self, tmp_trades):
        tm = TradeManager(tmp_trades)
        t = make_trade()
        tm.open_trade(t)
        assert "BTC-USDT" in tm.trades

    def test_get_trade(self, tmp_trades):
        tm = TradeManager(tmp_trades)
        t = make_trade()
        tm.open_trade(t)
        fetched = tm.get_trade("BTC-USDT")
        assert fetched is not None
        assert fetched.entry == 67000.0

    def test_get_nonexistent_returns_none(self, tmp_trades):
        tm = TradeManager(tmp_trades)
        assert tm.get_trade("XYZ-USDT") is None

    def test_multiple_symbols(self, tmp_trades):
        tm = TradeManager(tmp_trades)
        tm.open_trade(make_trade("BTC-USDT"))
        tm.open_trade(make_bear_trade("ETH-USDT"))
        assert len(tm.all_trades()) == 2

    def test_persistence_survives_reload(self, tmp_trades):
        tm = TradeManager(tmp_trades)
        tm.open_trade(make_trade())
        tm2 = TradeManager(tmp_trades)
        assert "BTC-USDT" in tm2.trades
        assert tm2.trades["BTC-USDT"].entry == 67000.0


# ══════════════════════════════════════════════════════════════════
# STOP LOSS HIT
# ══════════════════════════════════════════════════════════════════

class TestStopLoss:
    def test_stop_hit_long(self, tmp_trades):
        """Precio por debajo del stop → close_full=True."""
        tm = TradeManager(tmp_trades)
        tm.open_trade(make_trade(entry=67000.0, stop=66500.0))
        actions = tm.update("BTC-USDT", price=66400.0, st_val=66000.0, st_bull=True)
        assert actions["close_full"] is True
        assert any("STOP" in e for e in actions["events"])
        assert "BTC-USDT" not in tm.trades

    def test_stop_not_hit_above(self, tmp_trades):
        """Precio por encima del stop → no cierra."""
        tm = TradeManager(tmp_trades)
        tm.open_trade(make_trade(entry=67000.0, stop=66500.0))
        actions = tm.update("BTC-USDT", price=67100.0, st_val=66400.0, st_bull=True)
        assert not actions["close_full"]

    def test_stop_hit_short(self, tmp_trades):
        """Precio por encima del stop → close para SHORT."""
        tm = TradeManager(tmp_trades)
        tm.open_trade(make_bear_trade(entry=3000.0, stop=3100.0))
        actions = tm.update("ETH-USDT", price=3150.0, st_val=3200.0, st_bull=False)
        assert actions["close_full"] is True

    def test_trend_flip_exits_before_tp1(self, tmp_trades):
        """Inversión de trend antes de TP1 → salida de emergencia."""
        tm = TradeManager(tmp_trades)
        tm.open_trade(make_trade())
        # st_bull=False = supertrend giró a bajista
        actions = tm.update("BTC-USDT", price=67050.0, st_val=67200.0, st_bull=False)
        assert actions["close_full"] is True
        assert any("flip" in e.lower() or "trend" in e.lower() for e in actions["events"])

    def test_trend_flip_after_tp1_doesnt_exit(self, tmp_trades):
        """Después de TP1 con trailing, el trend flip no cierra inmediatamente."""
        tm = TradeManager(tmp_trades)
        t = make_trade()
        t.tp1_hit = True  # ya pasó TP1
        t.be_moved = True
        t.stop = t.entry  # stop en breakeven
        tm.open_trade(t)
        # st gira pero precio sigue por encima del stop
        actions = tm.update("BTC-USDT", price=67100.0, st_val=67200.0, st_bull=False)
        # No debe cerrar por trend flip si ya hay TP1 hit — solo se mueve el stop
        assert not actions["close_full"]


# ══════════════════════════════════════════════════════════════════
# SALIDAS PARCIALES EN TPs
# ══════════════════════════════════════════════════════════════════

class TestPartialExits:
    def test_tp1_triggers_partial_25pct(self, tmp_trades):
        """Al alcanzar TP1 se cierra el 25% de la posición."""
        tm = TradeManager(tmp_trades)
        t = make_trade(qty=0.04)
        tm.open_trade(t)
        actions = tm.update("BTC-USDT", price=67300.0, st_val=66000.0, st_bull=True)
        assert actions["partial_close"] is not None
        assert abs(actions["partial_close"]["qty"] - 0.01) < 1e-6  # 25% de 0.04
        assert "TP1" in actions["partial_close"]["reason"]

    def test_tp1_moves_stop_to_be(self, tmp_trades):
        """TP1 mueve el stop a breakeven."""
        tm = TradeManager(tmp_trades)
        t = make_trade(entry=67000.0, stop=66500.0, tp1=67250.0, qty=0.04)
        tm.open_trade(t)
        tm.update("BTC-USDT", price=67300.0, st_val=66000.0, st_bull=True)
        trade = tm.get_trade("BTC-USDT")
        assert trade is not None
        assert trade.be_moved is True
        assert trade.stop == 67000.0  # breakeven = entry

    def test_tp1_stop_be_update_action(self, tmp_trades):
        """La acción update_stop se emite al mover a BE."""
        tm = TradeManager(tmp_trades)
        t = make_trade(entry=67000.0, stop=66500.0, tp1=67250.0)
        tm.open_trade(t)
        actions = tm.update("BTC-USDT", price=67300.0, st_val=66000.0, st_bull=True)
        assert actions["update_stop"] == 67000.0

    def test_tp2_triggers_second_partial(self, tmp_trades):
        """TP2 cierra otro 25%."""
        tm = TradeManager(tmp_trades)
        t = make_trade(qty=0.04)
        t.tp1_hit = True
        t.be_moved = True
        t.quantity_remaining = 0.03  # ya se cerró 1 parcial
        tm.open_trade(t)
        actions = tm.update("BTC-USDT", price=67600.0, st_val=66000.0, st_bull=True)
        assert actions["partial_close"] is not None
        assert "TP2" in actions["partial_close"]["reason"]

    def test_tp4_closes_full(self, tmp_trades):
        """TP4 cierra el 100% restante."""
        tm = TradeManager(tmp_trades)
        t = make_trade(qty=0.04, tp4=68400.0)
        t.tp1_hit = True
        t.tp2_hit = True
        t.tp3_hit = True
        t.be_moved = True
        t.quantity_remaining = 0.01
        t.stop = 67000.0  # BE
        tm.open_trade(t)
        actions = tm.update("BTC-USDT", price=68500.0, st_val=67500.0, st_bull=True)
        assert actions["close_full"] is True
        assert any("TP4" in e for e in actions["events"])
        assert "BTC-USDT" not in tm.trades

    def test_no_double_tp1(self, tmp_trades):
        """TP1 no se dispara dos veces en la misma posición."""
        tm = TradeManager(tmp_trades)
        t = make_trade(tp1=67250.0)
        tm.open_trade(t)
        a1 = tm.update("BTC-USDT", price=67300.0, st_val=66000.0, st_bull=True)
        a2 = tm.update("BTC-USDT", price=67350.0, st_val=66000.0, st_bull=True)
        # Segunda actualización no debe emitir TP1 de nuevo
        if a2["partial_close"]:
            assert "TP1" not in a2["partial_close"]["reason"]


# ══════════════════════════════════════════════════════════════════
# TRAILING STOP
# ══════════════════════════════════════════════════════════════════

class TestTrailingStop:
    def test_trailing_raises_stop_after_tp1(self, tmp_trades):
        """Después de TP1, el trailing stop sube con el Supertrend."""
        tm = TradeManager(tmp_trades)
        t = make_trade(entry=67000.0, stop=66500.0)
        t.tp1_hit = True
        t.be_moved = True
        t.stop = 67000.0  # BE
        tm.open_trade(t)

        # Supertrend sube a 67300 (por encima del stop actual 67000)
        actions = tm.update("BTC-USDT", price=67800.0, st_val=67300.0, st_bull=True)
        trade = tm.get_trade("BTC-USDT")
        assert trade.stop == 67300.0
        assert actions["update_stop"] == 67300.0

    def test_trailing_never_lowers_stop(self, tmp_trades):
        """El stop nunca baja aunque el Supertrend baje."""
        tm = TradeManager(tmp_trades)
        t = make_trade(entry=67000.0, stop=67200.0)  # stop ya en 67200
        t.tp1_hit = True
        t.be_moved = True
        tm.open_trade(t)

        # Supertrend cae a 66800 (por debajo del stop actual 67200)
        actions = tm.update("BTC-USDT", price=67500.0, st_val=66800.0, st_bull=True)
        trade = tm.get_trade("BTC-USDT")
        assert trade.stop == 67200.0  # sin cambio
        assert actions["update_stop"] is None

    def test_no_trailing_before_tp1(self, tmp_trades):
        """Sin TP1 hit, el trailing no modifica el stop."""
        tm = TradeManager(tmp_trades)
        t = make_trade(entry=67000.0, stop=66500.0)
        t.tp1_hit = False
        tm.open_trade(t)

        actions = tm.update("BTC-USDT", price=67100.0, st_val=66700.0, st_bull=True)
        trade = tm.get_trade("BTC-USDT")
        assert trade.stop == 66500.0  # sin cambio
        assert actions.get("update_stop") is None

    def test_trailing_short_lowers_stop(self, tmp_trades):
        """Para SHORT, el trailing baja el stop con el Supertrend."""
        tm = TradeManager(tmp_trades)
        t = make_bear_trade(entry=3000.0, stop=3100.0,
                            tp1=2900.0, tp2=2800.0, tp3=2600.0, tp4=2400.0)
        t.tp1_hit = True
        t.be_moved = True
        t.stop = 3000.0  # BE
        tm.open_trade(t)

        # Supertrend bajista en 2800 (por debajo del stop actual 3000)
        actions = tm.update("ETH-USDT", price=2700.0, st_val=2800.0, st_bull=False)
        trade = tm.get_trade("ETH-USDT")
        assert trade.stop == 2800.0

    def test_unknown_symbol_returns_empty(self, tmp_trades):
        """Update en símbolo sin trade activo devuelve dict vacío."""
        tm = TradeManager(tmp_trades)
        result = tm.update("UNKNOWN-USDT", price=100.0, st_val=99.0, st_bull=True)
        assert result == {}
