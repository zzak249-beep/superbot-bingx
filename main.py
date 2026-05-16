"""
main.py — Sniper Bot V49 Híbrido — MODO UNIVERSO COMPLETO

Flujo:
  1. Al arrancar: obtiene TODOS los pares USDT perpetuos de BingX
  2. Filtra por volumen mínimo (top liquidez)
  3. Cada ciclo analiza todos los pares en paralelo (asyncio)
  4. Ejecuta órdenes solo en los que generan señal con score >= 55
  5. Heartbeat horario con ranking de los mejores setups del ciclo
"""
import asyncio
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

from config import Config
from bot.strategy import HybridStrategy, SignalResult
from bot.binance_client import BinanceClient
from bot.risk_manager import RiskManager, PositionState
from bot.telegram_notifier import TelegramNotifier
from bot.utils import setup_logging

setup_logging("INFO")
logger = logging.getLogger("main")

# Símbolos a excluir siempre (stablecoins, tokens de baja calidad)
BLACKLIST = {
    "USDC-USDT", "BUSD-USDT", "TUSD-USDT", "USDP-USDT",
    "DAI-USDT",  "FDUSD-USDT", "AEUR-USDT", "EURI-USDT",
}

# Concurrencia máxima al analizar (evita rate limit)
MAX_CONCURRENT = 10


class SniperBot:

    def __init__(self):
        self.cfg      = Config()
        self.client   = BinanceClient(
            self.cfg.BINANCE_API_KEY,
            self.cfg.BINANCE_SECRET_KEY,
            testnet=self.cfg.TESTNET
        )
        self.strategy = HybridStrategy(self.cfg)
        self.risk     = RiskManager(self.cfg)
        self.notifier = TelegramNotifier(
            self.cfg.TELEGRAM_TOKEN, self.cfg.TELEGRAM_CHAT_ID
        )
        self._pos_state: dict[str, PositionState]  = {}
        self._last_signals: dict[str, SignalResult] = {}
        self._last_heartbeat = datetime.min.replace(tzinfo=timezone.utc)
        self._current_bar    = 0
        self._daily_pnl      = 0.0
        self._universe: list[str] = []      # todos los pares activos

    # ─────────────────────────────────────────
    # ARRANQUE
    # ─────────────────────────────────────────

    async def start(self) -> None:
        logger.info(f"Iniciando Sniper Bot — {self.cfg}")
        await self.client.connect()

        # Obtener universo completo de BingX
        self._universe = await self._fetch_universe()
        logger.info(f"Universo: {len(self._universe)} pares activos")

        # Configurar leverage en todos
        await self._setup_all_symbols()

        # Notificar arranque
        self.cfg.SYMBOLS = self._universe   # sobreescribir lista inicial
        await self.notifier.send_startup(self.cfg)
        await self.notifier.send_universe(self._universe)

        await self._main_loop()

    # ─────────────────────────────────────────
    # DESCUBRIMIENTO DE UNIVERSO
    # ─────────────────────────────────────────

    async def _fetch_universe(self) -> list[str]:
        """
        Obtiene todos los contratos perpetuos USDT de BingX,
        filtra por volumen mínimo y excluye stablecoins.
        """
        symbols = await self.client.get_all_symbols()
        if not symbols:
            # Fallback a lista base si falla la API
            logger.warning("Fallback a lista base de símbolos")
            return [
                "BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT",
                "XRP-USDT", "DOGE-USDT", "ADA-USDT", "AVAX-USDT",
                "LINK-USDT", "DOT-USDT", "MATIC-USDT", "LTC-USDT",
            ]

        # Filtrar blacklist
        filtered = [s for s in symbols if s not in BLACKLIST]
        logger.info(f"Pares disponibles en BingX: {len(filtered)}")
        return filtered

    async def _setup_all_symbols(self) -> None:
        """Configura leverage en todos los pares en lotes de 10."""
        sem = asyncio.Semaphore(5)
        async def _setup(sym):
            async with sem:
                await self.client.setup_symbol(sym, self.cfg.LEVERAGE)
                await asyncio.sleep(0.1)
        await asyncio.gather(*[_setup(s) for s in self._universe],
                             return_exceptions=True)

    # ─────────────────────────────────────────
    # BUCLE PRINCIPAL
    # ─────────────────────────────────────────

    async def _main_loop(self) -> None:
        # Refrescar universo cada 24 h (nuevos listings)
        _last_universe_refresh = datetime.now(timezone.utc)

        while True:
            try:
                self._current_bar += 1
                balance = await self.client.get_balance()

                # Refrescar universo diariamente
                now = datetime.now(timezone.utc)
                if (now - _last_universe_refresh).total_seconds() > 86400:
                    self._universe = await self._fetch_universe()
                    await self._setup_all_symbols()
                    _last_universe_refresh = now
                    logger.info(f"Universo refrescado: {len(self._universe)} pares")

                # Analizar todos en paralelo
                top_signals = await self._scan_universe(balance)

                # Ejecutar los mejores setups
                for signal in top_signals:
                    await self._try_enter(signal.symbol, signal, balance)

                await self._maybe_heartbeat(balance)
                await asyncio.sleep(self.cfg.LOOP_INTERVAL)

            except KeyboardInterrupt:
                logger.info("Apagado por usuario")
                await self.notifier.send_paused("Apagado manual")
                break
            except Exception as e:
                logger.exception(f"Error en bucle principal: {e}")
                await self.notifier.send_error(str(e))
                await asyncio.sleep(30)

    # ─────────────────────────────────────────
    # SCAN PARALELO DEL UNIVERSO
    # ─────────────────────────────────────────

    async def _scan_universe(self, balance: float) -> list[SignalResult]:
        """
        Analiza todos los pares en paralelo con semáforo para
        no saturar el rate limit de BingX (300 req/min).
        Devuelve lista de señales activas ordenadas por score.
        """
        sem = asyncio.Semaphore(MAX_CONCURRENT)

        async def _analyze(symbol: str) -> Optional[SignalResult]:
            async with sem:
                try:
                    # Gestionar posiciones abiertas primero
                    position = await self.client.get_position(symbol)
                    if position and abs(position["size"]) > 0:
                        df = await self.client.get_klines(
                            symbol, self.cfg.TIMEFRAME, limit=300
                        )
                        if df is not None and len(df) >= 150:
                            signal = self.strategy.analyze(df, symbol)
                            await self._manage_open_position(
                                symbol, position, signal, balance, df
                            )
                        return None

                    # Analizar solo si podemos operar
                    if not self.risk.can_trade(symbol):
                        return None

                    df = await self.client.get_klines(
                        symbol, self.cfg.TIMEFRAME, limit=300
                    )
                    if df is None or len(df) < 150:
                        return None

                    signal = self.strategy.analyze(df, symbol)
                    self._last_signals[symbol] = signal

                    if signal.long or signal.short:
                        return signal
                    return None

                except Exception as e:
                    logger.debug(f"_analyze {symbol}: {e}")
                    return None

        results = await asyncio.gather(
            *[_analyze(s) for s in self._universe],
            return_exceptions=False
        )

        # Filtrar None y ordenar por score descendente
        active = [r for r in results if r is not None]
        active.sort(key=lambda x: x.score, reverse=True)

        if active:
            logger.info(
                f"Señales activas: {len(active)} | "
                f"Top: {active[0].symbol} score={active[0].score:.0f}"
            )
        return active

    # ─────────────────────────────────────────
    # INTENTAR ENTRADA
    # ─────────────────────────────────────────

    async def _try_enter(self, symbol: str, signal: SignalResult,
                         balance: float) -> None:
        if not self.risk.can_trade(symbol):
            return
        if balance <= 0:
            return
        if signal.long:
            await self._enter_long(symbol, signal, balance)
        elif signal.short:
            await self._enter_short(symbol, signal, balance)

    # ─────────────────────────────────────────
    # GESTIÓN DE POSICIÓN ABIERTA
    # ─────────────────────────────────────────

    async def _manage_open_position(self, symbol: str, position: dict,
                                    signal: SignalResult, balance: float,
                                    df) -> None:
        state = self._pos_state.get(symbol)
        if state is None:
            tp, sl = self.risk.compute_barriers(
                position["entry_price"], signal.atr14, position["side"]
            )
            self._pos_state[symbol] = PositionState(
                symbol=symbol, side=position["side"],
                entry_price=position["entry_price"],
                quantity=abs(position["size"]),
                tp_price=tp, sl_price=sl,
                entry_bar=self._current_bar
            )
            return

        if self.risk.check_time_exit(state, self._current_bar):
            logger.info(f"{symbol}: barrera de tiempo — cerrando")
            result = await self.client.close_position(symbol, position)
            if result:
                pnl     = await self.client.get_last_trade_pnl(symbol)
                pnl_pct = (pnl / balance * 100) if balance > 0 else 0.0
                self._daily_pnl += pnl
                self.risk.register_close(symbol, pnl_pct)
                del self._pos_state[symbol]
                await self.notifier.send_exit(
                    symbol, "TIME", pnl, pnl_pct, balance
                )

    # ─────────────────────────────────────────
    # ENTRADAS
    # ─────────────────────────────────────────

    async def _enter_long(self, symbol: str, signal: SignalResult,
                          balance: float) -> None:
        qty    = self.risk.calculate_position_size(signal, balance)
        tp, sl = self.risk.compute_barriers(
            signal.entry_price, signal.atr14, "LONG"
        )
        order  = await self.client.open_long(symbol, qty, tp, sl)
        if order:
            self.risk.register_open(symbol)
            self._pos_state[symbol] = PositionState(
                symbol=symbol, side="LONG",
                entry_price=signal.entry_price, quantity=qty,
                tp_price=tp, sl_price=sl,
                entry_bar=self._current_bar
            )
            await self.notifier.send_entry(symbol, "LONG", order, signal, balance)

    async def _enter_short(self, symbol: str, signal: SignalResult,
                           balance: float) -> None:
        qty    = self.risk.calculate_position_size(signal, balance)
        tp, sl = self.risk.compute_barriers(
            signal.entry_price, signal.atr14, "SHORT"
        )
        order  = await self.client.open_short(symbol, qty, tp, sl)
        if order:
            self.risk.register_open(symbol)
            self._pos_state[symbol] = PositionState(
                symbol=symbol, side="SHORT",
                entry_price=signal.entry_price, quantity=qty,
                tp_price=tp, sl_price=sl,
                entry_bar=self._current_bar
            )
            await self.notifier.send_entry(symbol, "SHORT", order, signal, balance)

    # ─────────────────────────────────────────
    # HEARTBEAT
    # ─────────────────────────────────────────

    async def _maybe_heartbeat(self, balance: float) -> None:
        now  = datetime.now(timezone.utc)
        diff = (now - self._last_heartbeat).total_seconds()
        if diff >= 3600:
            self._last_heartbeat = now

            # Top 5 setups del último ciclo
            top5 = sorted(
                self._last_signals.values(),
                key=lambda x: x.score, reverse=True
            )[:5]

            await self.notifier.send_heartbeat(
                balance        = balance,
                daily_pnl      = self._daily_pnl,
                open_pos       = self.risk.open_positions,
                daily_loss_pct = self.risk.daily_loss_pct,
                symbols_status = {s.symbol: s for s in top5},
                total_scanned  = len(self._universe)
            )


# ──────────────────────────────────────────────
if __name__ == "__main__":
    bot = SniperBot()
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("Bot detenido")
        sys.exit(0)
