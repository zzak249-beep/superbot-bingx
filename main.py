"""
main.py — Sniper Bot V35: Golden Equilibrium
Orquestador principal. Corre en Railway, opera en BingX,
notifica en Telegram, aprende de sus errores.

Ciclo:
  1. Cada 3 min  → escanea señales en los 20 mejores pares
  2. Cada 60 min → revisión de rentabilidad + re-escaneo de mercado
  3. A las 00:01 UTC → reporte diario
"""
import logging
import os
import time
from datetime import datetime, timezone

import schedule

from bingx_client import BingXClient
from config import (
    CANDLE_INTERVAL, DATA_DIR, DRY_RUN, LEVERAGE,
    SCAN_INTERVAL_MINUTES, TIME_STOP_CANDLES, TOP_N_SYMBOLS,
)
from learning_engine import LearningEngine
from risk_manager import RiskManager
from hourly_reviewer import HourlyReviewer
from scanner import MarketScanner
from strategy import StrategyV35
from telegram_notifier import TelegramNotifier

# ──────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────
os.makedirs(DATA_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"{DATA_DIR}/bot.log"),
    ],
)
logger = logging.getLogger("main")


# ──────────────────────────────────────────────────────────
class SniperBotV35:
    def __init__(self):
        logger.info("Inicializando Sniper Bot V35: Golden Equilibrium...")

        self.client   = BingXClient()
        self.strategy = StrategyV35()
        self.telegram = TelegramNotifier()
        self.scanner  = MarketScanner(self.client)
        self.risk     = RiskManager()
        self.learning = LearningEngine(telegram=self.telegram)
        self.reviewer = HourlyReviewer(self.learning, self.telegram, self.client)

        # Mapa de trades activos: symbol → {signal, qty, open_time, candle_count, ...}
        self._active: dict = {}

        # Lista de los mejores pares del día
        self._top_symbols: list = []

    # ──────────────────────────────────────────────────────
    # Startup
    # ──────────────────────────────────────────────────────
    def startup(self):
        balance = self.client.get_balance()
        self._top_symbols = self.scanner.get_top_symbols(TOP_N_SYMBOLS)

        logger.info(
            f"Balance: ${balance:.2f} USDT | "
            f"Pares: {len(self._top_symbols)} | "
            f"DRY_RUN: {DRY_RUN}"
        )
        self.telegram.notify_startup(balance, len(self._top_symbols), dry_run=DRY_RUN)
        self.telegram.notify_scan_results(self._top_symbols, self.scanner)

    # ──────────────────────────────────────────────────────
    # Tareas programadas
    # ──────────────────────────────────────────────────────
    def hourly_task(self):
        """Revisión horaria de rentabilidad + re-escaneo de mercado."""
        logger.info("Revisión horaria iniciada...")
        try:
            self.reviewer.run(self._active)
        except Exception as e:
            logger.error(f"Error en revisión horaria: {e}", exc_info=True)
        # Re-escaneo después de la revisión
        self._top_symbols = self.scanner.get_top_symbols(TOP_N_SYMBOLS)
        logger.info(f"Mercado re-escaneado. Top {len(self._top_symbols)} pares.")

    def daily_report(self):
        stats = self.learning.get_stats(today_only=True)
        self.telegram.notify_daily_report(stats)

    # ──────────────────────────────────────────────────────
    # Ciclo principal (cada 3 minutos)
    # ──────────────────────────────────────────────────────
    def tick(self):
        """Evalúa señales y gestiona trades abiertos."""
        symbols = [s["symbol"] for s in self._top_symbols]
        balance = self.client.get_balance()

        for symbol in symbols:
            try:
                df = self.client.get_klines(symbol, CANDLE_INTERVAL, limit=120)
                if df.empty or len(df) < 60:
                    continue

                if symbol in self._active:
                    self._manage_open(symbol, df)
                else:
                    self._check_new_signal(symbol, df, balance)

                time.sleep(0.25)   # evitar rate-limit

            except Exception as e:
                logger.error(f"Error en tick para {symbol}: {e}", exc_info=False)
                continue

    # ──────────────────────────────────────────────────────
    # Abrir trade
    # ──────────────────────────────────────────────────────
    def _check_new_signal(self, symbol: str, df, balance: float):
        # Filtro 1: blacklist por rendimiento histórico
        if self.learning.is_blacklisted(symbol):
            return

        # Filtro 2: cupo de posiciones abiertas
        ok, reason = self.risk.can_open(symbol)
        if not ok:
            return

        # Filtro 3: señal V35
        signal = self.strategy.get_signal(df, adx_override=self.learning.params["adx_min"])
        if signal["signal"] == "NONE":
            return

        # Filtro 4: motor de aprendizaje
        approved, reason = self.learning.should_take(signal)
        if not approved:
            logger.info(f"Señal descartada por aprendizaje: {symbol} — {reason}")
            return

        # Calcular cantidad
        qty = self.risk.calc_quantity(balance, signal["entry"], signal["sl"])
        if qty <= 0:
            logger.warning(f"Qty inválida para {symbol}: {qty}")
            return

        # Ejecutar orden
        position_side = "LONG" if signal["signal"] == "LONG" else "SHORT"
        order_side    = "BUY"  if signal["signal"] == "LONG" else "SELL"

        result = self.client.place_order(
            symbol        = symbol,
            side          = order_side,
            position_side = position_side,
            quantity      = qty,
            leverage      = LEVERAGE,
        )

        if result.get("code") != 0:
            logger.error(f"Orden rechazada {symbol}: {result}")
            return

        # Colocar SL y TP como órdenes separadas
        close_side = "SELL" if signal["signal"] == "LONG" else "BUY"
        self.client.place_stop_order(
            symbol, close_side, position_side,
            stop_price=signal["sl"], quantity=qty,
            order_type="STOP_MARKET",
        )
        self.client.place_stop_order(
            symbol, close_side, position_side,
            stop_price=signal["tp"], quantity=qty,
            order_type="TAKE_PROFIT_MARKET",
        )

        # Registrar
        meta = {
            "signal":        signal,
            "qty":           qty,
            "position_side": position_side,
            "open_time":     datetime.now(timezone.utc),
            "candle_count":  0,
            "position_usdt": qty * signal["entry"],
            "leverage":      LEVERAGE,
        }
        self._active[symbol] = meta
        self.risk.register(symbol, meta)

        self.telegram.notify_trade_open(symbol, signal, meta)
        logger.info(f"Trade abierto: {symbol} {signal['signal']} qty={qty} entry={signal['entry']}")

    # ──────────────────────────────────────────────────────
    # Gestionar trade activo
    # ──────────────────────────────────────────────────────
    def _manage_open(self, symbol: str, df):
        trade = self._active.get(symbol)
        if not trade:
            return

        trade["candle_count"] += 1
        current_price = float(df["close"].iloc[-1])
        signal        = trade["signal"]
        direction     = signal["signal"]

        # ── ¿Sigue abierta la posición en BingX? ──
        positions  = self.client.get_open_positions()
        still_open = any(
            p.get("symbol") == symbol and float(p.get("positionAmt", 0)) != 0
            for p in positions
        )

        if not still_open:
            # La cerró el SL o TP del exchange
            self._close_trade(
                symbol, trade, current_price,
                reason="TP/SL",
            )
            return

        # ── Time-Stop: 15 velas = 45 min ──
        if trade["candle_count"] >= TIME_STOP_CANDLES:
            logger.info(f"Time-Stop activado: {symbol}")
            self.client.cancel_all_orders(symbol)
            self.client.close_position(symbol, trade["position_side"], trade["qty"])
            self._close_trade(
                symbol, trade, current_price,
                reason="TIME_STOP",
            )

    def _close_trade(self, symbol: str, trade: dict, current_price: float, reason: str):
        signal    = trade["signal"]
        entry     = signal["entry"]
        direction = signal["signal"]

        # PnL estimado (sin comisiones exactas — BingX las deduce en el cierre)
        raw_pct = (current_price - entry) / entry
        if direction == "SHORT":
            raw_pct = -raw_pct
        pnl = round(raw_pct * trade["position_usdt"] * trade["leverage"], 4)

        duration = (datetime.now(timezone.utc) - trade["open_time"]).total_seconds() / 60

        outcome = {"pnl": pnl, "reason": reason, "duration_min": duration}

        self.learning.record(symbol, signal, outcome)
        self.telegram.notify_trade_close(symbol, outcome)

        del self._active[symbol]
        self.risk.close(symbol)

        logger.info(f"Trade cerrado: {symbol} pnl={pnl:.4f} reason={reason}")

    # ──────────────────────────────────────────────────────
    # Run
    # ──────────────────────────────────────────────────────
    def run(self):
        self.startup()

        # ── Scheduler ──
        schedule.every(3).minutes.do(self.tick)
        schedule.every(1).hour.do(self.hourly_task)     # revisión + rescan
        schedule.every().day.at("00:01").do(self.daily_report)

        logger.info("Scheduler iniciado. Ciclo 3min | Revisión horaria | Reporte diario 00:01 UTC")

        # Primera ejecución inmediata
        self.tick()

        while True:
            schedule.run_pending()
            time.sleep(10)


# ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot = SniperBotV35()
    bot.run()
