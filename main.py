"""
Conflux 4 Bot — main.py v5.0
Loop principal: scan → señal → trade → gestión → notificación Telegram

CAMBIOS v5.0 (optimizado para WR ≥ 79%):
  ✓ FIX CRÍTICO: calc_quantity con fallback seguro
  ✓ FIX: PnL real basado en position_usdt * leverage
  ✓ NUEVO: Filtro de sesión London/NY (horas de mayor liquidez)
  ✓ NUEVO: Filtro RSI direccional (SHORT > 48, BULL < 52) — evita entrar con momentum agotado
  ✓ NUEVO: Rechazo de SL < 1.8× ATR — evita stops prematuros por ruido
  ✓ NUEVO: Filtro de funding rate adverso para BEAR signals
  ✓ NUEVO: Sincronización de posiciones abiertas al arrancar (evita duplicados post-restart)
  ✓ NUEVO: Circuit breaker tras 3 pérdidas consecutivas — pausa configurable
  ✓ NUEVO: Umbral mínimo de calidad configurable (MIN_QUALITY, default 8)
  ✓ NUEVO: Confirmación de volumen (rechaza señales con volumen < 70% de media 20 períodos)
  ✓ NUEVO: Spread de entrada — no entra si precio actual dista > 0.15% del precio base
  ✓ MEJORADO: Log estructurado con motivo de rechazo por filtro
"""

import sys
import time
import math
import signal as _signal
import logging
from datetime import datetime, timezone
from loguru import logger

from config            import load_config, config_to_engine, config_to_risk
from bingx_client      import BingXClient
from telegram_notifier import TelegramNotifier
from risk_manager      import RiskManager
from trade_manager     import TradeManager, ActiveTrade
from symbol_scanner    import SymbolScanner
from conflux4          import Conflux4Engine as ConfluxEngine, supertrend

# ── Logging stdout (Railway) ──────────────────────────────────────────────────
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

# ── Parámetros de filtro WR (ajustables sin tocar lógica) ────────────────────
MIN_QUALITY          = 8        # Calidad mínima para aprobar señal (sobre 10)
MIN_ATR_SL_MULT      = 1.8      # SL debe ser ≥ 1.8× ATR para evitar ruido
RSI_SHORT_MIN        = 48       # Para BEAR: RSI debe ser ≥ 48 (no sobre-vendido)
RSI_BULL_MAX         = 52       # Para BULL: RSI debe ser ≤ 52 (no sobre-comprado)
FUNDING_BEAR_THRESH  = -0.0005  # Rechaza BEAR si funding < -0.05% (short squeeze riesgo)
VOLUME_RATIO_MIN     = 0.70     # Volumen barra actual ≥ 70% de media 20 velas
ENTRY_SPREAD_MAX     = 0.0015   # Máximo desvío precio actual vs precio señal (0.15%)
CIRCUIT_BREAKER_LOSS = 3        # Pausar tras N pérdidas consecutivas
CIRCUIT_BREAKER_SECS = 1800     # Pausa de 30 min tras circuit breaker

# Sesiones de trading UTC (mayor liquidez = mayor follow-through)
SESSION_WINDOWS_UTC = [
    (7, 17),   # Londres: 07:00–17:00 UTC
    (13, 22),  # Nueva York: 13:00–22:00 UTC
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_trading_session() -> bool:
    """Retorna True si la hora UTC actual está en ventana London o NY."""
    hour = datetime.now(timezone.utc).hour
    return any(start <= hour < end for start, end in SESSION_WINDOWS_UTC)


def _safe_calc_quantity(bingx: BingXClient, symbol: str,
                        position_usdt: float, price: float) -> float:
    """
    Calcula cantidad de contratos con fallback si calc_quantity no está
    implementado en BingXClient. Aplica stepSize desde exchangeInfo si disponible.
    """
    # Intentar método nativo primero
    if hasattr(bingx, "calc_quantity"):
        return bingx.calc_quantity(symbol, position_usdt, price)

    # Fallback: calcular manualmente
    if price <= 0:
        return 0.0

    raw_qty = position_usdt / price

    # Intentar obtener stepSize desde exchangeInfo
    step = 0.001  # default conservador
    try:
        if hasattr(bingx, "get_symbol_info"):
            info = bingx.get_symbol_info(symbol)
            step = float(info.get("tradeMinQty", info.get("stepSize", 0.001)))
        elif hasattr(bingx, "_get_symbol_info"):
            info = bingx._get_symbol_info(symbol)
            step = float(info.get("tradeMinQty", info.get("stepSize", 0.001)))
    except Exception:
        pass

    if step <= 0:
        step = 0.001

    qty = math.floor(raw_qty / step) * step
    return round(qty, 8) if qty > 0 else 0.0


def _calc_pnl(trade: ActiveTrade, price: float) -> float:
    """PnL real basado en posición y apalancamiento."""
    direction_mult = 1.0 if trade.direction == "BULL" else -1.0
    price_change   = (price - trade.entry) / trade.entry
    return direction_mult * price_change * trade.position_usdt * trade.leverage


def _sync_open_positions(bingx: BingXClient,
                         trade_mgr: TradeManager,
                         tg: TelegramNotifier) -> int:
    """
    Al arrancar, lee posiciones abiertas en BingX y las registra en trade_mgr
    para evitar duplicados. Retorna número de posiciones restauradas.
    """
    if not hasattr(bingx, "get_open_positions"):
        logger.warning("get_open_positions no implementado — omitiendo sync")
        return 0

    try:
        positions = bingx.get_open_positions()
        restored  = 0
        for pos in positions:
            sym = pos.get("symbol", "")
            if not sym or sym in trade_mgr.all_trades():
                continue

            size     = float(pos.get("positionAmt", 0))
            entry    = float(pos.get("avgPrice",     0))
            side_raw = pos.get("positionSide", "LONG")

            if abs(size) < 1e-9 or entry <= 0:
                continue

            direction = "BULL" if (size > 0 or side_raw == "LONG") else "BEAR"

            trade = ActiveTrade(
                symbol             = sym,
                direction          = direction,
                entry              = entry,
                stop               = entry * (0.975 if direction == "BULL" else 1.025),
                tp1                = entry * (1.01  if direction == "BULL" else 0.99),
                tp2                = entry * (1.02  if direction == "BULL" else 0.98),
                tp3                = entry * (1.03  if direction == "BULL" else 0.97),
                tp4                = entry * (1.04  if direction == "BULL" else 0.96),
                quantity           = abs(size),
                quantity_remaining = abs(size),
                leverage           = int(pos.get("leverage", 3)),
                position_usdt      = abs(size) * entry,
                quality            = 7,  # calidad desconocida en restore
            )
            trade_mgr.open_trade(trade)
            restored += 1
            logger.info(f"Posición restaurada: {sym} {direction} @ {entry:.4f} qty={abs(size)}")

        if restored > 0:
            tg._send(
                f"🔄 <b>Sync startup:</b> {restored} posición(es) restaurada(s) desde BingX.\n"
                f"SL/TP aproximados — revisar manualmente si es necesario."
            )
        return restored

    except Exception as e:
        logger.warning(f"sync_open_positions: {e}")
        return 0


def _validate_signal_filters(result, direction: str,
                              df_base, cfg) -> tuple[bool, str]:
    """
    Filtros adicionales de WR sobre el resultado del engine.
    Retorna (pass: bool, motivo_rechazo: str).

    Filtros aplicados:
    1. Calidad mínima
    2. RSI direccional (no entrar con momentum agotado)
    3. SL mínimo 1.8× ATR
    4. Volumen mínimo 70% de media
    5. Funding rate adverso para BEAR
    6. Sesión de trading activa
    """

    # ── 1. Calidad mínima ─────────────────────────────────────────────────────
    if result.quality < MIN_QUALITY:
        return False, f"Calidad {result.quality} < {MIN_QUALITY}"

    # ── 2. Filtro RSI direccional ─────────────────────────────────────────────
    rsi = result.rsi_val
    if direction == "BEAR" and rsi < RSI_SHORT_MIN:
        return False, f"SHORT con RSI={rsi:.0f} < {RSI_SHORT_MIN} (sobre-vendido)"
    if direction == "BULL" and rsi > RSI_BULL_MAX:
        return False, f"LONG con RSI={rsi:.0f} > {RSI_BULL_MAX} (sobre-comprado)"

    # ── 3. SL mínimo 1.8× ATR ────────────────────────────────────────────────
    try:
        entry        = float(df_base["close"].iloc[-1])
        sl_dist_pct  = abs(result.stop - entry) / entry   # fracción
        atr_pct      = result.atr_val / entry if hasattr(result, "atr_val") else None

        if atr_pct is not None and atr_pct > 0:
            if sl_dist_pct < MIN_ATR_SL_MULT * atr_pct:
                return False, (
                    f"SL {sl_dist_pct*100:.2f}% < {MIN_ATR_SL_MULT}×ATR "
                    f"({MIN_ATR_SL_MULT * atr_pct * 100:.2f}%) — stop prematuro"
                )
    except Exception:
        pass

    # ── 4. Confirmación de volumen ────────────────────────────────────────────
    try:
        vol_series  = df_base["volume"]
        vol_now     = float(vol_series.iloc[-1])
        vol_avg_20  = float(vol_series.iloc[-21:-1].mean())
        if vol_avg_20 > 0 and (vol_now / vol_avg_20) < VOLUME_RATIO_MIN:
            return False, (
                f"Volumen bajo: {vol_now/vol_avg_20*100:.0f}% de media "
                f"(mínimo {VOLUME_RATIO_MIN*100:.0f}%)"
            )
    except Exception:
        pass

    # ── 5. Funding rate adverso para BEAR ────────────────────────────────────
    if direction == "BEAR":
        try:
            fr = getattr(result, "funding_rate", None)
            if fr is not None and fr < FUNDING_BEAR_THRESH:
                return False, (
                    f"Funding {fr*100:.4f}% muy negativo — riesgo short squeeze"
                )
        except Exception:
            pass

    # ── 6. Sesión de trading ──────────────────────────────────────────────────
    if not _is_trading_session():
        return False, "Fuera de sesión London/NY (baja liquidez)"

    return True, ""


# ── Scan de símbolo ───────────────────────────────────────────────────────────

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
        result  = engine.compute(df, df_htf1, df_htf2, funding_rate=funding, symbol=symbol)
        return result, df
    except Exception as e:
        logger.warning(f"scan_symbol {symbol}: {e}")
        return None, None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global _shutdown
    _signal.signal(_signal.SIGTERM, _on_signal)
    _signal.signal(_signal.SIGINT,  _on_signal)

    logger.info("═══════════════════════════════════════════════")
    logger.info("     Conflux 4 Bot v5.0 — WR Target 79%       ")
    logger.info("═══════════════════════════════════════════════")
    logger.info(
        f"Filtros activos: MIN_Q={MIN_QUALITY} | "
        f"SL≥{MIN_ATR_SL_MULT}×ATR | "
        f"RSI BULL≤{RSI_BULL_MAX}/BEAR≥{RSI_SHORT_MIN} | "
        f"VOL≥{VOLUME_RATIO_MIN*100:.0f}% | "
        f"Sesión London/NY"
    )

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
        min_volume_usdt = cfg.min_volume_usdt,
        top_n           = cfg.top_n_symbols,
        refresh_seconds = cfg.symbol_refresh_hours * 3600,
    )

    dynamic_scan = not bool(cfg.fixed_symbols)
    if dynamic_scan:
        try:
            initial_symbols = scanner.get_symbols(bingx)
            cfg.symbols     = initial_symbols
        except Exception as e:
            logger.warning(f"Scanner inicial: {e} — usando fallback")

    # ── Balance inicial ───────────────────────────────────────────────────────
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

    # ── Sync posiciones abiertas (evita duplicados post-restart) ──────────────
    if cfg.bingx_api_key and cfg.auto_trade:
        _sync_open_positions(bingx, trade_mgr, tg)

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

    scan_count          = 0
    last_results: dict  = {}
    circuit_breaker_end = 0.0   # timestamp hasta el que está en pausa

    logger.info(
        f"Loop iniciado — scan cada {cfg.scan_seconds}s | "
        f"{'OPERANDO' if cfg.auto_trade else 'SOLO SEÑALES'}"
    )

    while not _shutdown:
        loop_start = time.time()
        scan_count += 1
        risk.set_scan_count(scan_count)

        # ── Circuit breaker ───────────────────────────────────────────────────
        if time.time() < circuit_breaker_end:
            remaining = int(circuit_breaker_end - time.time())
            logger.warning(f"⛔ Circuit breaker activo — reanuda en {remaining}s")
            time.sleep(min(30, remaining))
            continue

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

                df_quick    = bingx.get_klines(symbol, cfg.interval, limit=50)
                st_v, st_d  = supertrend(df_quick["high"], df_quick["low"],
                                         df_quick["close"], cfg.atr_len, cfg.st_mult)
                st_val_now  = float(st_v.iloc[-1])
                st_bull_now = bool(st_d.iloc[-1] < 0)

                actions = trade_mgr.update(symbol, price, st_val_now, st_bull_now)
                if not actions:
                    continue

                open_side = "BUY" if trade.direction == "BULL" else "SELL"

                # PnL real (fix v5.0 — antes usaba balance * 0.1, incorrecto)
                pnl_approx = _calc_pnl(trade, price)

                if actions.get("events"):
                    tg.trade_update(symbol, actions["events"], price, pnl_approx)

                if actions.get("close_full") and cfg.auto_trade and cfg.bingx_api_key:
                    try:
                        close_side = "SELL" if trade.direction == "BULL" else "BUY"
                        bingx.close_partial(symbol, close_side, trade.quantity_remaining)

                        pnl    = _calc_pnl(trade, price)
                        won    = pnl >= 0
                        reason = actions["events"][-1] if actions.get("events") else "Cierre"
                        risk.register_close(symbol, pnl, won)
                        tg.trade_closed(symbol, trade.direction, trade.entry, price, pnl, reason)

                        # ── Activar circuit breaker si procede ────────────────
                        if risk.state.consecutive_losses >= CIRCUIT_BREAKER_LOSS:
                            circuit_breaker_end = time.time() + CIRCUIT_BREAKER_SECS
                            logger.warning(
                                f"⛔ Circuit breaker activado: "
                                f"{risk.state.consecutive_losses} pérdidas consecutivas — "
                                f"pausa de {CIRCUIT_BREAKER_SECS//60} min"
                            )
                            tg.risk_alert(
                                "Circuit Breaker",
                                f"{risk.state.consecutive_losses} pérdidas consecutivas.\n"
                                f"Bot en pausa {CIRCUIT_BREAKER_SECS//60} minutos.\n"
                                f"Balance: ${risk.state.current_balance:.2f} USDT"
                            )

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
        errors_count    = 0
        filtered_count  = 0
        total_syms      = len(cfg.symbols)

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

            # ── Filtros WR v5.0 ───────────────────────────────────────────────
            passed, reject_reason = _validate_signal_filters(result, direction, df, cfg)
            if not passed:
                filtered_count += 1
                logger.info(f"🚫 FILTRO {symbol} {direction}: {reject_reason}")
                continue

            logger.info(
                f"⚡ SEÑAL {result.signal} {symbol} | Q={result.quality} "
                f"RSI={result.rsi_val:.0f} ADX={result.adx_val:.0f} "
                f"RR={result.rr_actual:.1f}"
            )

            risk_dec = risk.approve(symbol, direction, result.quality, result)
            if not risk_dec.approved:
                logger.info(f"Risk REJECT {symbol}: {risk_dec.reason}")
                continue

            # ── Verificar spread de entrada ───────────────────────────────────
            if cfg.auto_trade and cfg.bingx_api_key:
                try:
                    live_price = bingx.get_price(symbol)
                    signal_price = float(df["close"].iloc[-1])
                    spread = abs(live_price - signal_price) / signal_price
                    if spread > ENTRY_SPREAD_MAX:
                        logger.info(
                            f"🚫 SPREAD {symbol}: precio movido {spread*100:.2f}% "
                            f"desde señal — omitiendo"
                        )
                        continue
                except Exception:
                    pass

            tg.signal(result, symbol, cfg.interval, cfg.preset, risk_dec, result.quality)

            if cfg.auto_trade and cfg.bingx_api_key:
                try:
                    price = bingx.get_price(symbol)
                    if price <= 0:
                        logger.warning(f"Precio inválido {symbol}")
                        continue

                    side = "BUY" if direction == "BULL" else "SELL"

                    # ── calc_quantity con fallback seguro (FIX v5.0) ──────────
                    qty = _safe_calc_quantity(bingx, symbol, risk_dec.position_usdt, price)

                    if qty <= 0:
                        logger.warning(
                            f"Qty inválida {symbol} "
                            f"pos={risk_dec.position_usdt:.0f} price={price}"
                        )
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
            f"Scan #{scan_count} | {total_syms} pares | "
            f"Señales: {signals_cnt} | Filtradas: {filtered_count} | "
            f"Trades: {trades_open} | WR: {risk.state.all_time_winrate*100:.0f}% | "
            f"Errores: {errors_count} | Esperando {cfg.scan_seconds}s..."
        )

        wait = max(0, cfg.scan_seconds - elapsed)
        time.sleep(wait)

    logger.info("Shutdown limpio")
    tg._send("🛑 <b>Bot detenido.</b> Las posiciones en BingX permanecen abiertas.")


def _on_signal(signum, frame):
    global _shutdown
    logger.info(f"Señal {signum} → shutdown")
    _shutdown = True


if __name__ == "__main__":
    main()
