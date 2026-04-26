"""
Conflux 4 Bot v2 — Main Loop
Orquestación completa:
  BingX (MTF + funding) → Signal Engine → Risk Manager → Trade Manager → BingX Orders → Telegram
"""

import os
import sys
import time
import fcntl
import traceback
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).parent))

from config import load_config, config_to_engine, config_to_risk
from conflux4 import Conflux4Engine
from bingx_client import BingXClient
from telegram_notifier import TelegramNotifier
from risk_manager import RiskManager
from trade_manager import TradeManager, ActiveTrade


# ── Logging ───────────────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stdout, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level:5}</level> | {message}")
logger.add("logs/bot.log", rotation="10 MB", retention="14 days", level="DEBUG")

# ── Lock de instancia única ────────────────────────────────────────────────
LOCK_FILE = "/tmp/conflux4_bot.lock"

def acquire_instance_lock():
    """
    Garantiza que solo corre UNA instancia del bot a la vez.
    Evita el error WS por múltiples instancias intentando conectar a Telegram.
    """
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        logger.info(f"Lock de instancia adquirido (PID {os.getpid()})")
        return lock_fd
    except BlockingIOError:
        logger.critical("Ya hay otra instancia corriendo. Terminando.")
        sys.exit(1)


def main():
    logger.info("═══════════════════════════════════════")
    logger.info("  Conflux 4 Bot v2 — Starting")
    logger.info("═══════════════════════════════════════")

    # ── Instancia única ────────────────────────────────────────────────────
    lock_fd = acquire_instance_lock()

    cfg = load_config()
    tg = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id)

    # ── Verificar token y eliminar webhook ─────────────────────────────────
    # El error "WS error: server rejected WebSocket connection: HTTP 200"
    # ocurre cuando Telegram tiene un webhook activo. deleteWebhook lo elimina.
    logger.info("Verificando conexión con Telegram...")
    bot_info = tg.get_bot_info()
    if not bot_info:
        logger.critical("Token de Telegram inválido. Revisa TELEGRAM_TOKEN.")
        sys.exit(1)

    logger.info("Eliminando webhook de Telegram (si existe)...")
    tg.delete_webhook()
    time.sleep(1)

    bingx = BingXClient(cfg.bingx_api_key, cfg.bingx_secret, cfg.bingx_testnet)
    engine = Conflux4Engine(config_to_engine(cfg))
    risk = RiskManager(config_to_risk(cfg), data_path="data/equity.json")
    trade_mgr = TradeManager(data_path="data/trades.json")

    # ── Balance inicial desde BingX ───────────────────────────────────────
    if cfg.bingx_api_key:
        live_balance = bingx.get_balance()
        if live_balance > 0:
            risk.state.current_balance = live_balance
            if risk.state.starting_balance == 1000.0:
                risk.state.starting_balance = live_balance
                risk.state.peak_balance = max(risk.state.peak_balance, live_balance)
            risk.save()
            logger.info(f"Balance live desde BingX: {live_balance:.2f} USDT")

    tg.startup(cfg.symbols, cfg.interval, cfg.preset, risk.state.current_balance)

    scan_count = 0
    last_results = {}     # último resultado por símbolo (para dashboard)

    while True:
        scan_count += 1
        logger.info(f"── Scan #{scan_count} ─────────────────────────────")

        # ── 1. GESTIÓN DE TRADES ACTIVOS ────────────────────────────────────
        for symbol, trade in list(trade_mgr.all_trades().items()):
            try:
                price = bingx.get_price(symbol)
                # Necesitamos el ST actual — obtenemos pocas velas
                df_quick = bingx.get_klines(symbol, cfg.interval, limit=50)
                from conflux4 import supertrend, atr as calc_atr
                st_v, st_d = supertrend(df_quick["high"], df_quick["low"],
                                        df_quick["close"], cfg.atr_len, cfg.st_mult)
                st_val_now = float(st_v.iloc[-1])
                st_bull_now = bool(st_d.iloc[-1] < 0)

                actions = trade_mgr.update(symbol, price, st_val_now, st_bull_now)

                if actions.get("events"):
                    # Calcular P&L aproximado
                    d = trade.direction
                    pnl_approx = None
                    if d == "BULL":
                        pnl_approx = (price - trade.entry) / trade.entry * risk.state.current_balance * 0.1
                    else:
                        pnl_approx = (trade.entry - price) / trade.entry * risk.state.current_balance * 0.1
                    tg.trade_update(symbol, actions["events"], price, pnl_approx)

                if actions.get("close_full") and cfg.auto_trade:
                    close_side = "SELL" if trade.direction == "BULL" else "BUY"
                    try:
                        bingx.close_partial(symbol, close_side, trade.quantity_remaining)
                    except Exception as e:
                        logger.error(f"Error cerrando {symbol}: {e}")

                if actions.get("partial_close") and cfg.auto_trade:
                    pc = actions["partial_close"]
                    close_side = "SELL" if trade.direction == "BULL" else "BUY"
                    try:
                        bingx.close_partial(symbol, close_side, pc["qty"])
                    except Exception as e:
                        logger.error(f"Error cierre parcial {symbol}: {e}")

                if actions.get("update_stop") and cfg.auto_trade:
                    open_side = "BUY" if trade.direction == "BULL" else "SELL"
                    try:
                        bingx.update_stop_loss(symbol, open_side, actions["update_stop"])
                    except Exception as e:
                        logger.warning(f"No se pudo actualizar SL {symbol}: {e}")

            except Exception as e:
                logger.error(f"Error gestionando trade activo {symbol}: {traceback.format_exc()}")

        # ── 2. SCANNER DE NUEVAS SEÑALES ────────────────────────────────────
        for symbol in cfg.symbols:
            try:
                # Datos primarios
                df = bingx.get_klines(symbol, cfg.interval, cfg.kline_limit)

                # MTF (si está activado)
                df_htf1 = df_htf2 = None
                if cfg.use_mtf:
                    try:
                        df_htf1 = bingx.get_klines(symbol, cfg.htf1, limit=100)
                    except Exception:
                        pass
                    try:
                        if cfg.htf2 != cfg.htf1:
                            df_htf2 = bingx.get_klines(symbol, cfg.htf2, limit=50)
                    except Exception:
                        pass

                # Funding rate
                funding = bingx.get_funding_rate(symbol)

                # Calcular señal
                result = engine.compute(df, df_htf1, df_htf2, funding_rate=funding)
                last_results[symbol] = result

                logger.info(
                    f"{symbol} | {result.trend:8s} | RSI {result.rsi_val:.1f} | "
                    f"ADX {result.adx_val:.1f} | Q={result.quality}/10 | "
                    f"Funding={funding*100:.4f}% | Vol%={result.volume_pct:.0f} | "
                    f"Signal={result.signal or '—'}"
                )

                # ── 3. NUEVA SEÑAL DETECTADA ─────────────────────────────
                if result.signal:
                    direction = result.signal  # 'BULL' | 'BEAR'

                    # Risk Manager decide
                    risk_dec = risk.approve(symbol, direction, result.quality, result)

                    if risk_dec.approved:
                        # Notificar señal
                        tg.signal(result, symbol, cfg.interval, cfg.preset,
                                  risk_dec, result.quality)

                        # ── Auto-trade ────────────────────────────────────
                        if cfg.auto_trade and cfg.bingx_api_key:
                            try:
                                price = result.entry
                                qty = bingx.calc_quantity(
                                    symbol, risk_dec.position_usdt, price
                                )
                                side = "BUY" if direction == "BULL" else "SELL"

                                order = bingx.place_market_order(
                                    symbol=symbol,
                                    side=side,
                                    quantity=qty,
                                    stop_loss=result.stop,
                                    take_profit=result.tp2,  # TP2 en exchange, resto manual
                                )

                                trade = ActiveTrade(
                                    symbol=symbol,
                                    direction=direction,
                                    entry=price,
                                    stop=result.stop,
                                    tp1=result.tp1,
                                    tp2=result.tp2,
                                    tp3=result.tp3,
                                    tp4=result.tp4,
                                    quantity=qty,
                                    quantity_remaining=qty,
                                )
                                trade_mgr.open_trade(trade)
                                risk.register_open(symbol, direction)

                                logger.info(f"✅ Orden {side} ejecutada: {symbol} qty={qty}")

                            except Exception as e:
                                logger.error(f"Error auto-trade {symbol}: {e}")
                                tg.error(f"Auto-trade error ({symbol}): {str(e)[:200]}")

                    else:
                        # Señal rechazada por riesgo
                        tg.signal_rejected(symbol, risk_dec.reason)
                        logger.info(f"Señal rechazada [{symbol}]: {risk_dec.reason}")

            except Exception as e:
                logger.error(f"Error procesando {symbol}: {traceback.format_exc()[:400]}")
                tg.error(f"Error [{symbol}]: {str(e)[:200]}")

        # ── 4. DASHBOARD PERIÓDICO ───────────────────────────────────────
        if scan_count % cfg.dashboard_every_n_scans == 0 and last_results:
            summary = risk.summary()
            tg.performance_dashboard(summary, last_results)

        # ── 5. ALERTA DE DRAWDOWN ────────────────────────────────────────
        dd = risk.state.drawdown_pct
        if dd > risk.cfg.get("max_drawdown_pct", 15) * 0.8:  # Aviso al 80% del límite
            tg.risk_alert(
                "Drawdown elevado",
                f"Drawdown actual: {dd:.1f}% (límite: {risk.cfg.get('max_drawdown_pct', 15)}%)\n"
                f"Balance: {risk.state.current_balance:.2f} USDT"
            )

        logger.info(f"Esperando {cfg.scan_seconds}s...")
        time.sleep(cfg.scan_seconds)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot detenido por usuario.")
    except Exception as e:
        logger.critical(f"Error fatal: {traceback.format_exc()}")
