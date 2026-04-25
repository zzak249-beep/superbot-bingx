"""
Tests de cobertura complementarios
Cubren las líneas faltantes identificadas por pytest-cov:
  - conflux4.py: HTF2 rejection paths + funding bear block
  - risk_manager.py: corrupt json load, session block, duplicate position
  - trade_manager.py: corrupt json load
  - telegram_notifier.py: signal_rejected
  - bingx_client.py: mocked API (klines, price, funding, orders)
  - config.py: load_config con variables de entorno
"""
import os
import sys
import json
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from conftest import make_ohlcv
from conflux4 import Conflux4Engine
from risk_manager import RiskManager
from trade_manager import TradeManager, ActiveTrade
from telegram_notifier import TelegramNotifier


# ══════════════════════════════════════════════════════════════════
# CONFLUX4 — HTF2 REJECTION PATHS (líneas 221-227)
# ══════════════════════════════════════════════════════════════════

class TestMTFHTF2Coverage:
    def test_htf2_blocks_bull_when_htf2_bear(self, engine_cfg):
        """HTF2 en bearish bloquea señal BULL del primario — cubre líneas 221-227."""
        engine_cfg["funding_threshold"] = 99.0   # desactivar funding filter

        df_bull  = make_ohlcv(n=350, seed=42, trend="bull")
        df_htf1  = make_ohlcv(n=100, seed=42, trend="bull")   # HTF1 alineado
        df_htf2  = make_ohlcv(n=50,  seed=99, trend="bear")   # HTF2 contrario

        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df_bull, df_htf1=df_htf1, df_htf2=df_htf2)

        # Si el motor detecta señal BULL, MTF debe haberla bloqueado
        if r.signal == "BULL":
            pytest.fail("Señal BULL no debería pasar con HTF2 bajista")
        # mtf_ok debe ser False cuando HTF2 no confirma
        assert not r.mtf_ok or r.signal is None

    def test_htf2_blocks_bear_when_htf2_bull(self, engine_cfg):
        """HTF2 en bullish bloquea señal BEAR del primario — cubre línea 226-227."""
        engine_cfg["funding_threshold"] = 99.0

        df_bear  = make_ohlcv(n=350, seed=99, trend="bear")
        df_htf1  = make_ohlcv(n=100, seed=99, trend="bear")
        df_htf2  = make_ohlcv(n=50,  seed=42, trend="bull")   # HTF2 contrario

        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df_bear, df_htf1=df_htf1, df_htf2=df_htf2)

        if r.signal == "BEAR":
            pytest.fail("Señal BEAR no debería pasar con HTF2 alcista")
        assert not r.mtf_ok or r.signal is None

    def test_htf2_passes_when_aligned(self, engine_cfg):
        """HTF2 alineado NO debe bloquear — cubre el camino positivo."""
        engine_cfg["funding_threshold"] = 99.0

        df_bull  = make_ohlcv(n=350, seed=1, trend="bull")
        df_htf1  = make_ohlcv(n=100, seed=1, trend="bull")
        df_htf2  = make_ohlcv(n=50,  seed=1, trend="bull")

        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df_bull, df_htf1=df_htf1, df_htf2=df_htf2)
        assert r.mtf_ok is True  # HTF2 alineado → MTF pasa

    def test_funding_blocks_bear_signal(self, engine_cfg):
        """Funding muy negativo bloquea señal BEAR — cubre línea 237."""
        engine_cfg["funding_threshold"] = 0.001

        df_bear = make_ohlcv(n=350, seed=99, trend="bear")
        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df_bear, funding_rate=-0.99)  # funding extremo negativo

        if r.signal == "BEAR":
            pytest.fail("Señal BEAR no debería pasar con funding=-0.99")

    def test_funding_blocks_bull_extreme_positive(self, engine_cfg):
        """Funding muy positivo bloquea señal BULL — cubre línea 235."""
        engine_cfg["funding_threshold"] = 0.001

        df_bull = make_ohlcv(n=350, seed=42, trend="bull")
        eng = Conflux4Engine(engine_cfg)
        r = eng.compute(df_bull, funding_rate=0.99)  # funding extremo positivo

        if r.signal == "BULL":
            pytest.fail("Señal BULL no debería pasar con funding=0.99")


# ══════════════════════════════════════════════════════════════════
# RISK MANAGER — LÍNEAS FALTANTES
# ══════════════════════════════════════════════════════════════════

class TestRiskManagerCoverage:
    def test_corrupt_json_falls_back_to_default(self, risk_cfg, tmp_path):
        """JSON corrupto → fallback a estado inicial — cubre líneas 93-94."""
        bad_file = str(tmp_path / "equity.json")
        with open(bad_file, "w") as f:
            f.write("{INVALID JSON!!!}")

        rm = RiskManager(risk_cfg, bad_file)
        # Debe arrancar con valores por defecto, sin crash
        assert rm.state.starting_balance == risk_cfg["starting_balance"]
        assert rm.state.current_balance == risk_cfg["starting_balance"]

    def test_zero_balance_fallback(self, risk_cfg, tmp_path):
        """JSON válido pero balance ausente — cubre línea 72 (drawdown_pct)."""
        state_file = str(tmp_path / "equity.json")
        # Crear estado con peak=0 para forzar la rama de división por cero
        rm = RiskManager(risk_cfg, state_file)
        rm.state.peak_balance = 0.0
        rm.state.current_balance = 0.0
        dd = rm.state.drawdown_pct
        assert dd == 0.0  # no debe crashear

    def test_session_filter_blocks_trade(self, risk_cfg, tmp_path):
        """Filtro de sesión activo en hora bloqueada — cubre línea 187."""
        from unittest.mock import patch
        from datetime import datetime, timezone

        state_file = str(tmp_path / "equity.json")
        risk_cfg["use_session_filter"] = True
        risk_cfg["avoid_hours_utc"] = [3]
        rm = RiskManager(risk_cfg, state_file)

        mock_time = datetime(2024, 6, 15, 3, 30, 0, tzinfo=timezone.utc)
        with patch("risk_manager.datetime") as mock_dt:
            mock_dt.now.return_value = mock_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            dec = rm.approve("BTC-USDT", "BULL", quality=9)

        assert not dec.approved
        assert "sesión" in dec.reason.lower() or "sesi" in dec.reason.lower()

    def test_duplicate_position_same_symbol_blocked(self, risk_cfg, tmp_path):
        """Segunda posición en mismo símbolo bloqueada — cubre línea 191."""
        state_file = str(tmp_path / "equity.json")
        rm = RiskManager(risk_cfg, state_file)
        rm.state.open_positions["BTC-USDT"] = "BULL"
        rm.save()

        dec = rm.approve("BTC-USDT", "BEAR", quality=9)
        assert not dec.approved
        assert "BTC-USDT" in dec.reason or "abierta" in dec.reason.lower()


# ══════════════════════════════════════════════════════════════════
# TRADE MANAGER — JSON CORRUPTO (líneas 48-49)
# ══════════════════════════════════════════════════════════════════

class TestTradeManagerCoverage:
    def test_corrupt_trades_json_fallback(self, tmp_path):
        """JSON de trades corrupto → arranca con dict vacío — cubre líneas 48-49."""
        bad_file = str(tmp_path / "trades.json")
        with open(bad_file, "w") as f:
            f.write("{CORRUPT}")

        tm = TradeManager(bad_file)
        assert tm.trades == {}  # fallback a vacío, sin crash

    def test_partial_valid_json_fallback(self, tmp_path):
        """JSON con estructura incorrecta (no dict de trades) → vacío."""
        bad_file = str(tmp_path / "trades.json")
        with open(bad_file, "w") as f:
            json.dump(["not", "a", "dict"], f)  # lista en vez de dict

        tm = TradeManager(bad_file)
        assert tm.trades == {}


# ══════════════════════════════════════════════════════════════════
# TELEGRAM — SIGNAL REJECTED (líneas 152-153)
# ══════════════════════════════════════════════════════════════════

class TestTelegramCoverage:
    def test_signal_rejected_sends_message(self):
        """signal_rejected envía mensaje — cubre líneas 152-153."""
        tg = TelegramNotifier("fake", "fake")
        with patch.object(tg, "send", return_value=True) as mock_send:
            tg.signal_rejected("BTC-USDT", "Daily loss limit reached")
            msg = mock_send.call_args[0][0]

        assert "BTC-USDT" in msg
        assert "rechazada" in msg.lower() or "Rechazada" in msg
        assert "Daily loss" in msg

    def test_signal_rejected_different_symbols(self):
        """signal_rejected funciona con distintos símbolos y razones."""
        tg = TelegramNotifier("fake", "fake")
        test_cases = [
            ("ETH-USDT", "Drawdown limit"),
            ("SOL-USDT", "Correlation block"),
            ("BNB-USDT", "Quality too low"),
        ]
        for symbol, reason in test_cases:
            with patch.object(tg, "send", return_value=True) as mock_send:
                tg.signal_rejected(symbol, reason)
                msg = mock_send.call_args[0][0]
            assert symbol in msg
            assert reason in msg


# ══════════════════════════════════════════════════════════════════
# BINGX CLIENT — MOCK COMPLETO
# ══════════════════════════════════════════════════════════════════

def make_mock_klines_response(n=50, base_price=67000.0):
    """Genera respuesta mock de la API de klines de BingX."""
    import time
    now = int(time.time() * 1000)
    rows = []
    for i in range(n):
        ts = now - (n - i) * 60000  # velas de 1 minuto
        price = base_price + i * 10
        rows.append({
            "t": ts,
            "o": str(price),
            "h": str(price + 50),
            "l": str(price - 50),
            "c": str(price + 5),
            "v": str(500.0 + i),
        })
    return {"code": 0, "data": rows}


class TestBingXClientMocked:
    def test_get_klines_returns_dataframe(self):
        """get_klines procesa la respuesta de la API y devuelve DataFrame."""
        from bingx_client import BingXClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = make_mock_klines_response(50)
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            client = BingXClient()
            df = client.get_klines("BTC-USDT", "1m", limit=50)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 50
        assert all(col in df.columns for col in ["open", "high", "low", "close", "volume"])
        assert (df["close"] > 0).all()
        assert (df["high"] >= df["close"]).all()
        assert (df["low"] <= df["close"]).all()

    def test_get_price_returns_float(self):
        """get_price parsea el precio correctamente."""
        from bingx_client import BingXClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"price": "67432.50"}}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            client = BingXClient()
            price = client.get_price("BTC-USDT")

        assert isinstance(price, float)
        assert abs(price - 67432.50) < 0.01

    def test_get_funding_rate_returns_float(self):
        """get_funding_rate parsea correctamente."""
        from bingx_client import BingXClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0, "data": {"fundingRate": "0.0001"}}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            client = BingXClient()
            fr = client.get_funding_rate("BTC-USDT")

        assert isinstance(fr, float)
        assert abs(fr - 0.0001) < 1e-8

    def test_get_funding_rate_fallback_on_error(self):
        """Error de red en funding rate → devuelve 0.0 sin crash."""
        from bingx_client import BingXClient
        import httpx

        with patch("httpx.get", side_effect=httpx.ConnectError("timeout")):
            client = BingXClient()
            fr = client.get_funding_rate("BTC-USDT")

        assert fr == 0.0

    def test_calc_quantity_correct(self):
        """calc_quantity calcula la cantidad de activo correctamente."""
        from bingx_client import BingXClient

        client = BingXClient()
        # 500 USDT de posición a 67000 → 0.00746... BTC → redondeado a 0.007
        qty = client.calc_quantity("BTC-USDT", position_usdt=500.0, price=67000.0)
        expected = round(500.0 / 67000.0, 3)
        assert abs(qty - expected) < 1e-4

    def test_calc_quantity_small_position(self):
        """calc_quantity con posición pequeña."""
        from bingx_client import BingXClient
        client = BingXClient()
        qty = client.calc_quantity("ETH-USDT", position_usdt=50.0, price=3000.0)
        assert qty > 0
        assert qty < 1.0  # menos de 1 ETH

    def test_place_market_order_without_api_key_raises(self):
        """Sin API key, place_market_order lanza ValueError."""
        from bingx_client import BingXClient
        client = BingXClient(api_key="", secret_key="")
        with pytest.raises(ValueError, match="API key"):
            client.place_market_order("BTC-USDT", "BUY", 0.001)

    def test_get_balance_without_api_key_returns_zero(self):
        """Sin API key, get_balance devuelve 0 sin crash."""
        from bingx_client import BingXClient
        client = BingXClient(api_key="", secret_key="")
        bal = client.get_balance()
        assert bal == 0.0

    def test_get_balance_with_mocked_api(self):
        """get_balance con API key mockeada devuelve el balance."""
        from bingx_client import BingXClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {"balance": {"availableMargin": "850.25"}}
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            client = BingXClient(api_key="fake_key", secret_key="fake_secret")
            bal = client.get_balance()

        assert abs(bal - 850.25) < 0.01

    def test_retry_on_network_error(self):
        """La lógica de retry intenta hasta MAX_RETRIES veces."""
        from bingx_client import BingXClient
        import httpx

        call_count = [0]

        def flaky_get(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise httpx.ConnectError("timeout")
            mock = MagicMock()
            mock.json.return_value = make_mock_klines_response(10)
            mock.raise_for_status = MagicMock()
            return mock

        with patch("httpx.get", side_effect=flaky_get):
            with patch("time.sleep"):  # no esperar en tests
                client = BingXClient()
                df = client.get_klines("BTC-USDT", "1m", limit=10)

        assert call_count[0] == 3  # falló 2 veces, éxito a la 3ª
        assert len(df) == 10

    def test_klines_numeric_columns(self):
        """Todas las columnas OHLCV son numéricas (no strings)."""
        from bingx_client import BingXClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = make_mock_klines_response(20)
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            client = BingXClient()
            df = client.get_klines("BTC-USDT", "15m", limit=20)

        for col in ["open", "high", "low", "close", "volume"]:
            assert df[col].dtype in [np.float64, np.float32, float], \
                f"Columna {col} no es numérica: {df[col].dtype}"

    def test_klines_index_is_datetime(self):
        """El índice del DataFrame es DatetimeIndex."""
        from bingx_client import BingXClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = make_mock_klines_response(20)
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            client = BingXClient()
            df = client.get_klines("BTC-USDT", "15m", limit=20)

        assert isinstance(df.index, pd.DatetimeIndex)

    def test_get_bid_ask_spread_returns_float(self):
        """get_bid_ask_spread_pct devuelve porcentaje positivo."""
        from bingx_client import BingXClient

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {"bidPrice": "67000.0", "askPrice": "67010.0"}
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            client = BingXClient()
            spread = client.get_bid_ask_spread_pct("BTC-USDT")

        assert isinstance(spread, float)
        assert spread > 0
        assert spread < 1.0  # spread de ~0.015% es realista

    def test_get_bid_ask_fallback_on_error(self):
        """Error en bid/ask → devuelve 0.0 sin crash."""
        from bingx_client import BingXClient
        import httpx

        with patch("httpx.get", side_effect=Exception("API error")):
            client = BingXClient()
            spread = client.get_bid_ask_spread_pct("BTC-USDT")

        assert spread == 0.0


# ══════════════════════════════════════════════════════════════════
# CONFIG — CARGA DE VARIABLES DE ENTORNO
# ══════════════════════════════════════════════════════════════════

class TestConfigLoading:
    def test_load_config_daytrader_preset(self):
        """Preset Daytrader carga correctamente."""
        from config import load_config, PRESETS

        env = {
            "TELEGRAM_TOKEN": "test_token",
            "TELEGRAM_CHAT_ID": "-100123",
            "PRESET": "Daytrader",
            "INTERVAL": "15m",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()

        assert cfg.preset == "Daytrader"
        assert cfg.cooldown == PRESETS["Daytrader"]["cooldown"]
        assert cfg.stop_mode == PRESETS["Daytrader"]["stop_mode"]

    def test_load_config_scalp_preset(self):
        """Preset Scalp aplica sus valores específicos."""
        from config import load_config, PRESETS

        env = {
            "TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "-1",
            "PRESET": "Scalp",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()

        assert cfg.cooldown == PRESETS["Scalp"]["cooldown"]
        assert cfg.leverage == PRESETS["Scalp"]["leverage"]

    def test_load_config_swing_preset(self):
        """Preset Swing aplica sus valores específicos."""
        from config import load_config, PRESETS

        env = {
            "TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "-1",
            "PRESET": "Swing",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()

        assert cfg.cooldown == PRESETS["Swing"]["cooldown"]
        assert cfg.rr4 == PRESETS["Swing"]["rr4"]

    def test_symbols_parsed_from_env(self):
        """SYMBOLS se parsea correctamente de variable de entorno."""
        from config import load_config

        env = {
            "TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "-1",
            "SYMBOLS": "BTC-USDT,ETH-USDT,SOL-USDT",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()

        assert cfg.symbols == ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

    def test_auto_trade_false_by_default(self):
        """AUTO_TRADE es False por defecto."""
        from config import load_config

        env = {"TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "-1"}
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()

        assert cfg.auto_trade is False

    def test_auto_trade_true_from_env(self):
        """AUTO_TRADE=true activa el auto-trade."""
        from config import load_config

        env = {
            "TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "-1",
            "AUTO_TRADE": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()

        assert cfg.auto_trade is True

    def test_mtf_map_correct_for_15m(self):
        """Para TF=15m, HTF1=1h y HTF2=4h."""
        from config import load_config

        env = {
            "TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "-1",
            "INTERVAL": "15m",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()

        assert cfg.htf1 == "1h"
        assert cfg.htf2 == "4h"

    def test_mtf_map_correct_for_1h(self):
        """Para TF=1h, HTF1=4h y HTF2=1d."""
        from config import load_config

        env = {
            "TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "-1",
            "INTERVAL": "1h",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()

        assert cfg.htf1 == "4h"
        assert cfg.htf2 == "1d"

    def test_config_to_engine_has_all_keys(self):
        """config_to_engine devuelve todas las claves que espera el motor."""
        from config import load_config, config_to_engine

        env = {"TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "-1"}
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()
            params = config_to_engine(cfg)

        required = ["vwma_len", "ema_fast", "ema_slow", "rsi_len", "rsi_bull", "rsi_bear",
                    "atr_len", "st_mult", "use_adx", "adx_len", "adx_thr",
                    "cooldown", "stop_mode", "rr1", "rr2", "rr3", "rr4"]
        for k in required:
            assert k in params, f"Clave faltante en config_to_engine: {k}"

    def test_config_to_risk_has_all_keys(self):
        """config_to_risk devuelve todas las claves que espera el risk manager."""
        from config import load_config, config_to_risk

        env = {"TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "-1"}
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()
            params = config_to_risk(cfg)

        required = ["starting_balance", "leverage", "max_risk_per_trade_pct",
                    "max_open_trades", "max_daily_loss_pct", "max_drawdown_pct",
                    "min_signal_quality", "use_session_filter"]
        for k in required:
            assert k in params, f"Clave faltante en config_to_risk: {k}"

    def test_missing_telegram_token_raises(self):
        """Sin TELEGRAM_TOKEN, load_config lanza KeyError."""
        from config import load_config

        env_without_token = {k: v for k, v in os.environ.items()
                             if k not in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")}
        with patch.dict(os.environ, env_without_token, clear=True):
            with pytest.raises(KeyError):
                load_config()

    def test_scan_seconds_from_env(self):
        """SCAN_SECONDS se lee correctamente."""
        from config import load_config

        env = {
            "TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "-1",
            "SCAN_SECONDS": "30",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()

        assert cfg.scan_seconds == 30

    def test_starting_balance_from_env(self):
        """STARTING_BALANCE se lee correctamente."""
        from config import load_config

        env = {
            "TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "-1",
            "STARTING_BALANCE": "5000",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()

        assert cfg.starting_balance == 5000.0

    def test_testnet_flag(self):
        """BINGX_TESTNET=true activa testnet."""
        from config import load_config

        env = {
            "TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "-1",
            "BINGX_TESTNET": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()

        assert cfg.bingx_testnet is True
