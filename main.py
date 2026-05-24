"""
main.py — QF×JP Bot v3
Ejecuta ciclos cada 3 minutos alineados al cierre de vela
"""
import logging
import time
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config as C
from bingx_client import BingXClient
from strategy import QFJPStrategy
from order_manager import OrderManager
from risk_manager import RiskManager
from telegram_notifier import TelegramNotifier
from market_data import fetch_pair_data, validate_df
from health_server import start_health_server, update_status

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("main")

# ── Instancias globales ───────────────────────────────────────
client   = BingXClient()
risk     = RiskManager()
tg       = TelegramNotifier()
strategy = QFJPStrategy()
orders   = OrderManager(client, risk, tg)


# ─────────────────────────────────────────────────────────────
def run_single(symbol: str):
    """Ciclo completo para un único par"""
    log.info(f"── Ciclo {symbol} ──")

    df_3m, df_15m, funding, ob_imb = fetch_pair_data(client, symbol)

    if not validate_df(df_3m):
        log.warning(f"Datos insuficientes para {symbol}")
        update_status(error=True)
        return

    price   = float(df_3m["close"].iloc[-1])
    atr_now = float(df_3m["high"].iloc[-10:].mean() - df_3m["low"].iloc[-10:].mean()) / 2

    # Gestionar posición abierta
    orders.manage_positions(symbol, price, atr_now)

    # Generar señal
    sig = strategy.compute(df_3m, df_15m, funding, ob_imb)

    if sig.direction != "NONE":
        balance = client.get_balance()
        ok, reason = risk.can_trade(balance, len(client.get_positions(symbol)))
        if ok:
            orders.execute_signal(symbol, sig, balance)
        else:
            log.info(f"Trade bloqueado: {reason}")

    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    update_status(last_cycle=f"{ts} {symbol} {sig.direction}")


def run_multi():
    """Ciclo completo para top N pares"""
    log.info("── Ciclo multi-par ──")
    balance = client.get_balance()
    pairs   = client.get_top_pairs(C.TOP_PAIRS, C.MIN_VOLUME_USDT)

    best_signals = []

    for symbol in pairs:
        try:
            df_3m, df_15m, funding, ob_imb = fetch_pair_data(client, symbol)
            if not validate_df(df_3m, min_rows=80):
                continue
            sig = strategy.compute(df_3m, df_15m, funding, ob_imb)
            if sig.direction != "NONE":
                best_signals.append({
                    "symbol":     symbol,
                    "signal":     sig,
                    "direction":  sig.direction,
                    "level":      sig.level,
                    "conviction": sig.conviction,
                    "df_3m":      df_3m,
                })
        except Exception as e:
            log.error(f"Error en {symbol}: {e}")
        time.sleep(0.3)  # evitar rate-limit

    if not best_signals:
        log.info("Sin señales este ciclo")
        update_status(last_cycle=datetime.now(timezone.utc).strftime("%H:%M:%S"))
        return

    # Ordenar por convicción descendente
    best_signals.sort(key=lambda x: x["conviction"], reverse=True)

    # Notificar resumen
    tg.send_scan_result(best_signals)

    # Ejecutar la mejor señal (si hay margen)
    for item in best_signals:
        sym = item["symbol"]
        sig = item["signal"]
        df  = item["df_3m"]

        price   = float(df["close"].iloc[-1])
        atr_now = float((df["high"] - df["low"]).iloc[-10:].mean())
        orders.manage_positions(sym, price, atr_now)

        ok, reason = risk.can_trade(balance, len(client.get_positions(sym)))
        if ok:
            orders.execute_signal(sym, sig, balance)
            break  # una entrada por ciclo en multi-par
        else:
            log.info(f"{sym} bloqueado: {reason}")

    update_status(last_cycle=datetime.now(timezone.utc).strftime("%H:%M:%S"))


# ─────────────────────────────────────────────────────────────
def cycle():
    """Función llamada por el scheduler"""
    try:
        if C.MULTI_PAIR:
            run_multi()
        else:
            run_single(C.SYMBOL)
    except Exception as e:
        log.exception(f"Error en ciclo: {e}")
        tg.send_error(f"Error ciclo: {e}")
        update_status(error=True)


# ─────────────────────────────────────────────────────────────
def main():
    log.info("══════════════════════════════════")
    log.info("  QF×JP Bot v3 — Iniciando        ")
    log.info("══════════════════════════════════")

    # Health check server (Railway)
    start_health_server()

    # Validar credenciales
    if not C.BINGX_API_KEY or not C.BINGX_SECRET_KEY:
        log.error("BINGX_API_KEY / BINGX_SECRET_KEY no configuradas")
        sys.exit(1)

    # Balance inicial
    balance = client.get_balance()
    log.info(f"Balance: {balance:.2f} USDT")

    # Configurar símbolo y recuperar posiciones
    if not C.MULTI_PAIR:
        orders.setup_symbol(C.SYMBOL)
        orders.sync_positions(C.SYMBOL)

    # Notificar arranque
    tg.send_startup(C.SYMBOL, balance, C.MULTI_PAIR)

    # Ejecutar ciclo inmediato al arrancar
    cycle()

    # Scheduler: cada 3 minutos, 10 segundos después del cierre de vela
    # Cierre de velas 3min: 00:00, 03:00, 06:00 ... → disparo en :10s
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        cycle,
        CronTrigger(minute="0,3,6,9,12,15,18,21,24,27,30,33,36,39,42,45,48,51,54,57",
                    second=10),
        max_instances=1,
        misfire_grace_time=30,
    )

    log.info("Scheduler iniciado — ciclos cada 3min")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        log.info("Bot detenido manualmente")


if __name__ == "__main__":
    main()
