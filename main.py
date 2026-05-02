"""
Conflux 4 Bot v3.1 — Main Loop (MEJORADO)

Mejoras sobre v3:
  - Log completo de config al arranque (detecta conflictos de env vars)
  - scan_count pasado al risk manager (para cooldown post-SL)
  - Alerta Telegram si error rate > 15% en un scan
  - register_close() llamado correctamente al cerrar trades
  - sig_txt siempre visible en logs (corregido)
  - Importación de supertrend movida al top (no dentro del loop)
  - Limpieza de sl_cooldown de símbolos ya no en lista
"""

import os
import sys
import time
import fcntl
import traceback
from typing import Dict

from loguru import logger

from config import load_config, config_to_engine, config_to_risk
from conflux4 import Conflux4Engine, supertrend
from bingx_client import BingXClient
from telegram_notifier import TelegramNotifier
from risk_manager import RiskManager
from trade_manager import TradeManager, ActiveTrade
from symbol_scanner import SymbolScanner


# ── Logging ───────────────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stdout, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level:5}</level> | {message}")
logger.add("logs/bot.log", rotation="10 MB", retention="14 days", level="DEBUG")

LOCK_FILE = "/tmp/conflux4_bot.lock"


def acquire_instance_lock():
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


def scan_symbol(symbol: str, cfg, engine: Conflux4Engine, bingx: BingXClient) -> dict:
    """
    Obtiene datos y calcula señal para un símbolo.
    Devuelve {'result': SignalResult, 'error': str|None}.
    """
    try:
        df = bingx.get_klines(symbol, cfg.interval, cfg.kline_limit)

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

        funding = bingx.get_funding_rate(symbol)
        result = engine.compute(df, df_htf1, df_htf2, funding_rate=funding)
        return {"result": result, "error": None}

    except Exception as e:
        return {"result": None, "error": str(e)}


def main():
    logger.info("═══════════════════════════════════════")
    logger.info("  Conflux 4 Bot v3.1 — Starting")
    logger.info("  Filtros mejorados: ADX/RSI/SL/RR")
    logger.info("═══════════════════════════════════════")

    lock_fd = acquire_instance_lock()

    cfg = load_config()   # Ya loguea config completa internamente
    tg = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id)

    logger.info("Verificando conexión con Telegram...")
    bot_info = tg.get_bot_info()
    if not bot_info:
        logger.critical("Token de Telegram inválido.")
        sys.exit(1)

    logger.info("Eliminando webhook de Telegram...")
    tg.delete_webhook()
    time.sleep(1)

    bingx = BingXClient(cfg.bingx_api_key, cfg.bingx_secret, cfg.bingx_testnet)

    # ── Scanner dinámico ───────────────────────────────────────────────────
    dynamic_scan = not bool(cfg.fixed_symbols)
    scanner = SymbolScanner(
        min_volume_usdt=cfg.min_volume_usdt,
        top_n=cfg.top_n_symbols,
        refresh_seconds=cfg.symbol_refresh_hours * 3600,
    )

    if dynamic_scan:
        logger.info(f"Modo scanner dinámico: top {cfg.top_n_symbols} pares, "
                    f"vol mín {cfg.min_volume_usdt/1e6:.1f}M USDT")
        initial_symbols = scanner.get_symbols(bingx)
        if initial_symbols:
            cfg.symbols = initial_symbols
    else:
        logger.info(f"Modo lista fija: {len(cfg.symbols)} pares")

    # ── Motor de señales por símbolo ───────────────────────────────────────
    engines: Dict[str, Conflux4Engine] = {}

    def get_engine(symbol: str) -> Conflux4Engine:
        if symbol not in engines:
            engines[symbol] = Conflux4Engine(config_to_engine(cfg))
        return engines[symbol]

    risk = RiskManager(config_to_risk(cfg), data_path="data/equity.json")
    trade_mgr = TradeManager(data_path="data/trades.json")

    # ── Balance inicial ────────────────────────────────────────────────────
    if cfg.bingx_api_key:
        live_balance = bingx.get_balance()
        if live_balance > 0:
            risk.state.current_balance = live_balance
            if risk.state.starting_balance == 1000.0:
                risk.state.starting_balance = live_balance
                risk.state.peak_balance = max(risk.state.peak_balance, live_balance)
            risk.save()
            logger.info(f"Balance live desde BingX: {live_balance:.2f} USDT")

    tg.startup(
        symbols=cfg.symbols,
        interval=cfg.interval,
        preset=cfg.preset,
        balance=risk.state.current_balance,
        dynamic_scan=dynamic_scan,
        top_n=cfg.top_n_symbols,
    )

    scan_count = 0
    last_results = {}
    prev_symbol_count = len(cfg.symbols)

    while True:
        scan_count += 1
        risk.set_scan_count(scan_count)   # Para cooldown post-SL

        # ── Rotación dinámica de símbolos ──────────────────────────────────
        if dynamic_scan and scanner.needs_refresh:
            logger.info("Actualizando lista de símbolos...")
            new_symbols = scanner.get_symbols(bingx)
            if new_symbols:
                cfg.symbols = new_symbols
                if len(new_symbols) != prev_symbol_count:
                    tg.symbols_updated(new_symbols, cfg.min_volume_usdt, cfg.top_n_symbols)
                    prev_symbol_count = len(new_symbols)
                # Limpiar engines y sl_cooldowns de símbolos fuera de lista
                current_set = set(new_symbols)
                for sym in list(engines.keys()):
                    if sym not in current_set:
                        del engines[sym]
                for sym in list(risk.state.sl_cooldown.keys()):
                    if sym not in current_set:
                        del risk.state.sl_cooldown[sym]

        logger.info(
            f"── Scan #{scan_count} | {len(cfg.symbols)} pares | "
            f"{'dinámico' if dynamic_scan else 'lista fija'} ──"
        )

        # ── 1. GESTIÓN DE TRADES ACTIVOS ─────────────────────────────────
        for symbol, trade in list(trade_mgr.all_trades().items()):
            try:
                price = bingx.get_price(symbol)
                df_quick = bingx.get_klines(symbol, cfg.interval, limit=50)
                st_v, st_d = supertrend(
                    df_quick["high"], df_quick["low"], df_quick["close"],
                    cfg.atr_len, cfg.st_mult
                )
                st_val_now  = float(st_v.iloc[-1])
                st_bull_now = bool(st_d.iloc[-1] < 0)

                actions = trade_mgr.update(symbol, price, st_val_now, st_bull_now)

                if actions.get("events"):
                    d = trade.direction
                    if d == "BULL":
                        pnl_approx = (price - trade.entry) / trade.entry * risk.state.current_balance * 0.1
                    else:
                        pnl_approx = (trade.entry - price) / trade.entry * risk.state.current_balance * 0.1
                    tg.trade_update(symbol, actions["events"], price, pnl_approx)

                # Registrar cierre en risk manager con resultado real
                if actions.get("close_full"):
                    won = price > trade.entry if trade.direction == "BULL" else price < trade.entry
                    pnl = (price - trade.entry) / trade.entry * risk.state.current_balance * 0.1
                    risk.register_close(symbol, pnl, won)

                    if cfg.auto_trade:
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

            except Exception:
                logger.error(f"Error gestionando trade {symbol}: {traceback.format_exc()[:300]}")

        # ── 2. SCANNER DE NUEVAS SEÑALES ──────────────────────────────────
        signals_found = 0
        errors_count = 0

        for idx, symbol in enumerate(cfg.symbols):
            try:
                eng = get_engine(symbol)
                scan_data = scan_symbol(symbol, cfg, eng, bingx)

                if scan_data["error"]:
                    errors_count += 1
                    if errors_count <= 3:
                        logger.warning(f"Error {symbol}: {scan_data['error'][:100]}")
                    continue

                result = scan_data["result"]
                last_results[symbol] = result

                # Log siempre correcto (fix bug Signal=—)
                if result.signal:
                    sig_txt = f"⚡{result.signal}(Q{result.quality}/RR{result.rr_actual})"
                else:
                    reason_short = result.reasons[0][:40] if result.reasons else "sin señal"
                    sig_txt = f"—  [{reason_short}]"

                logger.info(
                    f"{symbol:14s} | {result.trend:7s} | "
                    f"RSI {result.rsi_val:5.1f} | ADX {result.adx_val:5.1f} | "
                    f"ATR {result.atr_pct:.2f}% | {sig_txt}"
                )

                # ── 3. NUEVA SEÑAL DETECTADA ──────────────────────────────
                if result.signal:
                    signals_found += 1
                    direction = result.signal
                    risk_dec = risk.approve(symbol, direction, result.quality, result)

                    if risk_dec.approved:
                        tg.signal(result, symbol, cfg.interval, cfg.preset,
                                  risk_dec, result.quality)

                        if cfg.auto_trade and cfg.bingx_api_key:
                            try:
                                price = result.entry
                                qty = bingx.calc_quantity(symbol, risk_dec.position_usdt, price)
                                side = "BUY" if direction == "BULL" else "SELL"
                                order = bingx.place_market_order(
                                    symbol=symbol, side=side, quantity=qty,
                                    stop_loss=result.stop, take_profit=result.tp2,
                                )
                                trade = ActiveTrade(
                                    symbol=symbol, direction=direction,
                                    entry=price, stop=result.stop,
                                    tp1=result.tp1, tp2=result.tp2,
                                    tp3=result.tp3, tp4=result.tp4,
                                    quantity=qty, quantity_remaining=qty,
                                )
                                trade_mgr.open_trade(trade)
                                risk.register_open(symbol, direction)
                                logger.info(f"✅ Orden {side} ejecutada: {symbol} qty={qty}")
                            except Exception as e:
                                logger.error(f"Error auto-trade {symbol}: {e}")
                                tg.error(f"Auto-trade error ({symbol}): {str(e)[:200]}")
                    else:
                        logger.info(f"Señal rechazada [{symbol}]: {risk_dec.reason}")

                if (idx + 1) % 10 == 0:
                    time.sleep(0.5)

            except Exception as e:
                errors_count += 1
                logger.error(f"Error procesando {symbol}: {str(e)[:200]}")

        # ── 4. ALERTA DE ERROR RATE > 15% (nuevo) ────────────────────────
        if errors_count > 0:
            total_syms = len(cfg.symbols)
            error_pct = errors_count / total_syms * 100
            logger.warning(f"Scan #{scan_count}: {errors_count}/{total_syms} errores ({error_pct:.0f}%)")
            if risk.should_alert_error_rate(errors_count, total_syms):
                tg.risk_alert(
                    "Tasa de errores elevada",
                    f"Scan #{scan_count}: {errors_count}/{total_syms} pares con error ({error_pct:.0f}%)\n"
                    "Posible problema con API de BingX o rate limit."
                )

        # ── 5. DASHBOARD PERIÓDICO ────────────────────────────────────────
        if scan_count % cfg.dashboard_every_n_scans == 0 and last_results:
            summary = risk.summary()
            tg.performance_dashboard(summary, last_results)

        # ── 6. ALERTA DE DRAWDOWN ─────────────────────────────────────────
        dd = risk.state.drawdown_pct
        max_dd = risk.cfg.get("max_drawdown_pct", 15)
        if dd > max_dd * 0.8:
            tg.risk_alert(
                "Drawdown elevado",
                f"Drawdown actual: {dd:.1f}% (límite: {max_dd}%)\n"
                f"Balance: {risk.state.current_balance:.2f} USDT\n"
                f"Pérdidas consecutivas: {risk.state.consecutive_losses}"
            )

        # ── 7. ALERTA DE RACHA MALA ───────────────────────────────────────
        if risk.state.consecutive_losses >= 4:
            tg.risk_alert(
                "Racha de pérdidas",
                f"{risk.state.consecutive_losses} pérdidas consecutivas\n"
                f"Sizing reducido al 40% automáticamente\n"
                f"Balance: {risk.state.current_balance:.2f} USDT"
            )

        logger.info(
            f"Scan #{scan_count} completado | "
            f"Señales: {signals_found} | "
            f"WR: {risk.state.all_time_winrate*100:.0f}% | "
            f"Esperando {cfg.scan_seconds}s..."
        )
        time.sleep(cfg.scan_seconds)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot detenido por usuario.")
    except Exception as e:
        logger.critical(f"Error fatal: {traceback.format_exc()}")
