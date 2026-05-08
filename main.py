"""
Conflux 4 Bot — main.py v4.0
Loop principal: scan → señal → trade → gestión → notificación Telegram

CAMBIOS v4.0:
  ✓ Pasa symbol= a engine.compute() para cooldown per-símbolo correcto
  ✓ Compatible con conflux4.py v4.0
"""
import sys
import time
import signal as _signal
import logging
from loguru import logger

from config           import load_config, config_to_engine, config_to_risk
from bingx_client     import BingXClient
from telegram_notifier import TelegramNotifier
from risk_manager     import RiskManager
from trade_manager    import TradeManager, ActiveTrade
from symbol_scanner   import SymbolScanner
from conflux4         import Conflux4Engine as ConfluxEngine, supertrend

# ── logging stdout (Railway) ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger.remove()
logger.add(sys.stdout, level="INFO",
           format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")

_shutdown = False

def _on_signal(signum, frame):
    global _shutdown
    logger.info(f"Señal {signum} → shutdown")
    _shutdown = True


def _scan_symbol(symbol, bingx, engine, cfg):
    """Pide klines y calcula señal. Retorna (result, df_base) o (None, None)."""
    try:
        df      = bingx.get_klines(symbol, cfg.interval, cfg.kline_limit)
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
        # ✅ FIX: pasar symbol= para cooldown per-símbolo
        result  = engine.compute(df, df_htf1, df_htf2, funding_rate=funding, symbol=symbol)
        return result, df
    except Exception as e:
        logger.warning(f"scan_symbol {symbol}: {e}")
        return None, None


def main():
    global _shutdown
    _signal.signal(_signal.SIGTERM, _on_signal)
    _signal.signal(_signal.SIGINT,  _on_signal)

    logger.info("═══════════════════════════════════════════════")
    logger.info("        Conflux 4 Bot v4.0 — Arrancando        ")
    logger.info("═══════════════════════════════════════════════")

    try:
        cfg = load_config()
    except Exception as e:
        logger.critical(f"Error de config: {e}")
        sys.exit(1)

    bingx = BingXClient(cfg.bingx_api_key, cfg.bingx_secret, testnet=cfg.bingx_testnet)
    tg    = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id)

    bot_info = tg.get_bot_info()
    if not bot_info:
        logger.warning("No se pudo verificar el bot Telegram — continúa de todos modos")
    tg.delete_webhook()

    engine    = ConfluxEngine(config_to_engine(cfg))
    risk      = RiskManager(config_to_risk(cfg))
    trade_mgr = TradeManager()
    scanner   = SymbolScanner(
        min_volume_usdt  = cfg.min_volume_usdt,
        top_n            = cfg.top_n_symbols,
        refresh_seconds  = cfg.symbol_refresh_hours * 3600,
    )

    dynamic_scan = not bool(cfg.fixed_symbols)
    if dynamic_scan:
        try:
            initial_symbols = scanner.get_symbols(bingx)
            cfg.symbols     = initial_symbols
        except Exception as e:
            logger.warning(f"Scanner inicial: {e} — usando fallback")

    if cfg.bingx_api_key:
        try:
            live_balance = bingx.get_balance()
            if live_balance > 0:
                risk.state.current_balance = live_balance
                if risk.state.starting_balance == 1000.0:
                    risk.state.starting_balance = live_balance
                    risk.state.peak_balance     = max(risk.state.peak_balance, live_balance)
                risk.save()
                logger.info(f"Balance live: ${live_balance:.2f} USDT")
        except Exception as e:
            logger.warning(f"Balance inicial: {e}")

    tg.startup(
        symbols      = cfg.symbols,
        interval     = cfg.interval,
        preset       = cfg.preset,
        balance      = risk.state.current_balance,
        dynamic_scan = dynamic_scan,
        top_n        = cfg.top_n_symbols,
    )

    if not cfg.auto_trade or not cfg.bingx_api_key:
        logger.warning("AUTO_TRADE desactivado o sin API key — modo solo señales")
        tg._send(
            "⚠️ <b>Modo SOLO SEÑALES</b>\n"
            "Activa <code>AUTO_TRADE=true</code> y configura "
            "<code>BINGX_API_KEY</code> para operar en real."
        )

    scan_count         = 0
    last_results: dict = {}

    logger.info(f"Loop iniciado — scan cada {cfg.scan_seconds}s | "
                f"{'OPERANDO' if cfg.auto_trade else 'SOLO SEÑALES'}")

    while not _shutdown:
        loop_start = time.time()
        scan_count += 1
        risk.set_scan_count(scan_count)

        # ── Refrescar símbolos ────────────────────────────────────────────────
        if dynamic_scan and scanner.needs_refresh and scan_count > 1:
            try:
                new_syms = scanner.get_symbols(bingx)
                if new_syms:
                    cfg.symbols = new_syms
                    tg.symbols_updated(new_syms, cfg.min_volume_usdt, cfg.top_n_symbols)
            except Exception as e:
                logger.warning(f"Refresh símbolos: {e}")

        # Limpiar cooldowns expirados
        for sym in list(risk.state.sl_cooldown.keys()):
            if risk.state.sl_cooldown[sym] <= scan_count:
                del risk.state.sl_cooldown[sym]

        # ── Gestión de trades abiertos ────────────────────────────────────────
        for symbol, trade in list(trade_mgr.all_trades().items()):
            try:
                price = bingx.get_price(symbol)
                if price <= 0:
                    continue

                df_quick   = bingx.get_klines(symbol, cfg.interval, limit=50)
                st_v, st_d = supertrend(df_quick["high"], df_quick["low"],
                                        df_quick["close"], cfg.atr_len, cfg.st_mult)
                st_val_now  = float(st_v.iloc[-1])
                st_bull_now = bool(st_d.iloc[-1] < 0)

                actions = trade_mgr.update(symbol, price, st_val_now, st_bull_now)
                if not actions:
                    continue

                open_side = "BUY" if trade.direction == "BULL" else "SELL"

                if actions.get("events"):
                    pnl_approx = ((price - trade.entry) / trade.entry
                                  * risk.state.current_balance * 0.1
                                  if trade.direction == "BULL"
                                  else (trade.entry - price) / trade.entry
                                  * risk.state.current_balance * 0.1)
                    tg.trade_update(symbol, actions["events"], price, pnl_approx)

                if actions.get("close_full") and cfg.auto_trade and cfg.bingx_api_key:
                    try:
                        close_side = "SELL" if trade.direction == "BULL" else "BUY"
                        bingx.close_partial(symbol, close_side, trade.quantity_remaining)
                        pnl = ((price - trade.entry) / trade.entry
                               * trade.position_usdt * cfg.leverage
                               if trade.direction == "BULL"
                               else (trade.entry - price) / trade.entry
                               * trade.position_usdt * cfg.leverage)
                        won    = pnl >= 0
                        reason = actions["events"][-1] if actions.get("events") else "Cierre"
                        risk.register_close(symbol, pnl, won)
                        tg.trade_closed(symbol, trade.direction, trade.entry, price, pnl, reason)
                    except Exception as e:
                        logger.error(f"close_full {symbol}: {e}")
                        tg.error(f"Error cerrando {symbol}: {str(e)[:150]}")

                elif actions.get("partial_close") and cfg.auto_trade and cfg.bingx_api_key:
                    try:
                        close_side = "SELL" if trade.direction == "BULL" else "BUY"
                        pc = actions["partial_close"]
                        bingx.close_partial(symbol, close_side, pc["qty"])
                    except Exception as e:
                        logger.error(f"partial_close {symbol}: {e}")

                if actions.get("update_stop") and cfg.auto_trade and cfg.bingx_api_key:
                    try:
                        bingx.update_stop_loss(symbol, open_side, actions["update_stop"])
                    except Exception as e:
                        logger.error(f"update_stop {symbol}: {e}")

            except Exception as e:
                logger.error(f"Gestión trade {symbol}: {e}")

        # ── Scan de señales nuevas ─────────────────────────────────────────────
        errors_count = 0
        total_syms   = len(cfg.symbols)

        for symbol in cfg.symbols:
            if _shutdown:
                break
            if symbol in trade_mgr.all_trades():
                continue

            result, df = _scan_symbol(symbol, bingx, engine, cfg)
            if result is None:
                errors_count += 1
                continue

            last_results[symbol] = result

            if not result.signal:
                logger.debug(
                    f"{symbol} {result.trend} | RSI={result.rsi_val:.0f} "
                    f"ADX={result.adx_val:.0f} | {' | '.join(result.reasons[:2])}"
                )
                continue

            direction = "BULL" if result.signal == "BULL" else "BEAR"
            logger.info(
                f"⚡ SEÑAL {result.signal} {symbol} | Q={result.quality} "
                f"RSI={result.rsi_val:.0f} ADX={result.adx_val:.0f} "
                f"RR={result.rr_actual:.1f}"
            )

            risk_dec = risk.approve(symbol, direction, result.quality, result)
            if not risk_dec.approved:
                logger.info(f"Risk REJECT {symbol}: {risk_dec.reason}")
                continue

            tg.signal(result, symbol, cfg.interval, cfg.preset, risk_dec, result.quality)

            if cfg.auto_trade and cfg.bingx_api_key:
                try:
                    price = bingx.get_price(symbol)
                    if price <= 0:
                        logger.warning(f"Precio inválido {symbol}")
                        continue

                    side = "BUY" if direction == "BULL" else "SELL"
                    qty  = bingx.calc_quantity(symbol, risk_dec.position_usdt, price)

                    if qty <= 0:
                        logger.warning(f"Qty inválida {symbol} pos={risk_dec.position_usdt:.0f}")
                        continue

                    order = bingx.place_market_order(
                        symbol      = symbol,
                        side        = side,
                        quantity    = qty,
                        stop_loss   = result.stop,
                        take_profit = result.tp2,
                        leverage    = cfg.leverage,
                    )

                    trade = ActiveTrade(
                        symbol             = symbol,
                        direction          = direction,
                        entry              = price,
                        stop               = result.stop,
                        tp1                = result.tp1,
                        tp2                = result.tp2,
                        tp3                = result.tp3,
                        tp4                = result.tp4,
                        quantity           = qty,
                        quantity_remaining = qty,
                        leverage           = cfg.leverage,
                        position_usdt      = risk_dec.position_usdt,
                        quality            = result.quality,
                    )
                    trade_mgr.open_trade(trade)
                    risk.register_open(symbol, direction)

                    tg.trade_opened(
                        symbol        = symbol,
                        direction     = direction,
                        entry         = price,
                        stop          = result.stop,
                        tp2           = result.tp2,
                        quantity      = qty,
                        position_usdt = risk_dec.position_usdt,
                        leverage      = cfg.leverage,
                        quality       = result.quality,
                    )

                    logger.success(
                        f"✅ TRADE ABIERTO: {symbol} {direction} @ {price:.4f} "
                        f"qty={qty} SL={result.stop:.4f} TP={result.tp2:.4f}"
                    )

                except Exception as e:
                    logger.error(f"open_order {symbol}: {e}")
                    tg.error(f"Error abriendo {symbol}: {str(e)[:200]}")

            time.sleep(0.3)

        # ── Alertas de error rate ─────────────────────────────────────────────
        if risk.should_alert_error_rate(errors_count, total_syms):
            tg.risk_alert(
                "Error rate alto",
                f"{errors_count}/{total_syms} pares con error en este scan\n"
                f"Posible problema con la API de BingX"
            )

        # ── Dashboard periódico ───────────────────────────────────────────────
        if scan_count % cfg.dashboard_every_n_scans == 0:
            summary = risk.summary()
            tg.performance_dashboard(summary, last_results)

        # ── Alertas de drawdown / racha ───────────────────────────────────────
        dd     = risk.state.drawdown_pct
        max_dd = risk.cfg.get("max_drawdown_pct", 15)
        if dd >= max_dd * 0.8:
            tg.risk_alert(
                "Drawdown alto",
                f"Drawdown actual: {dd:.1f}% (límite: {max_dd}%)\n"
                f"Balance: ${risk.state.current_balance:.2f} USDT\n"
                f"Pérdidas consecutivas: {risk.state.consecutive_losses}"
            )

        if risk.state.consecutive_losses >= 4:
            tg.risk_alert(
                "Racha negativa",
                f"{risk.state.consecutive_losses} pérdidas consecutivas\n"
                f"Sizing reducido al 40% automáticamente\n"
                f"Balance: ${risk.state.current_balance:.2f} USDT"
            )

        # ── Log de scan ───────────────────────────────────────────────────────
        elapsed     = time.time() - loop_start
        signals_cnt = sum(1 for r in last_results.values() if r.signal)
        trades_open = len(trade_mgr.all_trades())
        logger.info(
            f"Scan #{scan_count} completado | {total_syms} pares | "
            f"Señales: {signals_cnt} | WR: {risk.state.all_time_winrate*100:.0f}% | "
            f"Esperando {cfg.scan_seconds}s..."
        )

        wait = max(0, cfg.scan_seconds - elapsed)
        time.sleep(wait)

    logger.info("Shutdown limpio")
    tg._send("🛑 <b>Bot detenido.</b> Las posiciones en BingX permanecen abiertas.")


if __name__ == "__main__":
    main()
