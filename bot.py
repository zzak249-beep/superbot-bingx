"""
Signal Projection Explorer Bot - BingX Auto Trader
Basado en el indicador Pine Script de QuantNomad
Detecta crossover MA rápida/lenta + proyecta PnL histórico
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

from signal_engine import SignalEngine
from market_scanner import MarketScanner
from bingx_client import BingXClient
from trade_manager import TradeManager
from dashboard import Dashboard

# RL stack es opcional — solo disponible si ray+tensortrade están instalados
try:
    from rl_trainer import klines_to_csv, run_training
    RL_AVAILABLE = True
except ImportError:
    RL_AVAILABLE = False
    logging.getLogger("BOT").warning(
        "ray/tensortrade no instalados — entrenamiento RL desactivado. "
        "Instala requirements-rl.txt para habilitarlo."
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("BOT")


class TradingBot:
    def __init__(self):
        self.client        = BingXClient(
            api_key    = os.environ["BINGX_API_KEY"],
            api_secret = os.environ["BINGX_API_SECRET"],
        )
        self.scanner       = MarketScanner(self.client)
        self.signal_engine = SignalEngine()
        self.trade_manager = TradeManager(self.client)
        self.dashboard     = Dashboard()
        self.running       = False
        self._cycle_count  = 0
        # Cada cuántos ciclos re-entrenar el agente RL (0 = desactivado)
        self._rl_train_every = int(os.getenv("RL_TRAIN_EVERY", "0"))
        self._rl_symbol      = os.getenv("RL_TRAIN_SYMBOL", "BTC-USDT")

    # ------------------------------------------------------------------ #
    async def run(self):
        log.info("🚀  Bot iniciado")
        self.running = True

        await self.client.initialize()        # Hedge Mode + symbol info
        await self.dashboard.start()          # dashboard web en :8080

        while self.running:
            try:
                await self._cycle()
            except Exception as e:
                log.error(f"Error en ciclo principal: {e}", exc_info=True)

            await asyncio.sleep(int(os.getenv("SCAN_INTERVAL_SEC", "60")))

    # ------------------------------------------------------------------ #
    async def _cycle(self):
        log.info("🔍  Escaneando mercado …")

        # 1. Obtener monedas «en explosión» (volumen + momentum)
        hot_symbols = await self.scanner.get_hot_symbols(
            top_n        = int(os.getenv("TOP_SYMBOLS", "30")),
            min_volume   = float(os.getenv("MIN_VOLUME_USDT", "500000")),
        )
        log.info(f"   → {len(hot_symbols)} símbolos calientes")

        # 2. Calcular señales para cada símbolo
        signals = []
        for sym in hot_symbols:
            sig = await self.signal_engine.evaluate(self.client, sym)
            if sig:
                signals.append(sig)
                log.info(f"   ✅  SEÑAL {sig['direction']} en {sym}  "
                         f"mean_pnl={sig['mean_pnl']:.2%}  "
                         f"median_pnl={sig['median_pnl']:.2%}")

        # 3. Gestionar trades abiertos (take-profit / stop-loss)
        await self.trade_manager.manage_open_trades()

        # 4. Abrir nuevas posiciones para las mejores señales
        for sig in signals:
            await self.trade_manager.open_trade(sig)

        # 5. Actualizar dashboard
        open_trades = await self.trade_manager.get_open_trades()
        await self.dashboard.update(hot_symbols, signals, open_trades)

        # 6. Re-entrenar agente RL periódicamente (si está habilitado)
        self._cycle_count += 1
        if RL_AVAILABLE and self._rl_train_every > 0 and self._cycle_count % self._rl_train_every == 0:
            await self._run_rl_training()


    # ------------------------------------------------------------------ #
    async def _run_rl_training(self):
        """
        Descarga velas del símbolo RL, exporta CSVs de train/eval
        y lanza Ray Tune en un hilo separado para no bloquear el bot.
        """
        import asyncio
        log.info(f"🧠  Iniciando entrenamiento RL para {self._rl_symbol} …")
        try:
            interval = os.getenv("KLINE_INTERVAL", "1h")
            klines   = await self.client.get_klines(self._rl_symbol, interval, limit=1000)

            if len(klines) < 300:
                log.warning("Pocas velas para entrenar, saltando")
                return

            split        = int(len(klines) * 0.8)
            train_klines = klines[:split]
            eval_klines  = klines[split:]

            klines_to_csv(train_klines, "logs/training.csv")
            klines_to_csv(eval_klines,  "logs/evaluation.csv")

            loop = asyncio.get_event_loop()
            best = await loop.run_in_executor(
                None,
                lambda: run_training(
                    training_csv   = "logs/training.csv",
                    evaluation_csv = "logs/evaluation.csv",
                    num_iterations = int(os.getenv("RL_ITERATIONS", "5")),
                )
            )
            log.info(f"🧠  Entrenamiento RL completado. Mejor checkpoint: {best}")

        except Exception as e:
            log.error(f"Error en entrenamiento RL: {e}", exc_info=True)


# ------------------------------------------------------------------ #
if __name__ == "__main__":
    bot = TradingBot()
    asyncio.run(bot.run())
