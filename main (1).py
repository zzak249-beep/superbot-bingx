"""
main.py — 4H Range Fakeout v3.1 — Pionex
Ultra-robusto: captura errores de import y cualquier crash
"""
import sys
import logging
import time
import traceback

# ── Logging primero, antes de cualquier import ────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("main")
log.info("=== ARRANQUE DEL BOT ===")
log.info(f"Python {sys.version}")

# ── Imports con diagnóstico ───────────────────────────
try:
    from datetime import datetime, timezone
    log.info("OK: datetime")
    import requests
    log.info(f"OK: requests {requests.__version__}")
    import pandas as pd
    log.info(f"OK: pandas {pd.__version__}")
    import numpy as np
    log.info(f"OK: numpy {np.__version__}")
except ImportError as e:
    log.error(f"FALTA DEPENDENCIA: {e}")
    log.error("Asegurate de que requirements.txt tiene: requests pandas numpy")
    sys.exit(1)

try:
    import config as cfg
    log.info(f"OK: config — version={cfg.VERSION}")
except Exception as e:
    log.error(f"ERROR en config.py: {e}")
    sys.exit(1)

try:
    import fetcher
    log.info("OK: fetcher")
    import strategy
    log.info("OK: strategy")
    import trader
    log.info("OK: trader")
    import state as st
    log.info("OK: state")
    import notifier as tg
    log.info("OK: notifier")
    import symbols_loader
    log.info("OK: symbols_loader")
except Exception as e:
    log.error(f"ERROR importando módulo: {e}")
    log.error(traceback.format_exc())
    sys.exit(1)

log.info("Todos los módulos cargados correctamente")


# ─────────────────────────────────────────────────────

def manage_open_positions(state):
    for symbol, pos in list(state["positions"].items()):
        try:
            price = trader.get_price(symbol)
            if price <= 0:
                continue

            side       = pos["side"];  entry     = pos["entry"]
            sl         = pos["sl"];    tp        = pos["tp"]
            be_trigger = pos.get("be_trigger", tp)
            trail_dist = pos.get("trail_dist", 0)
            be_active  = pos.get("be_active", False)

            if not be_active:
                be_hit = (side == "long"  and price >= be_trigger) or \
                         (side == "short" and price <= be_trigger)
                if be_hit:
                    st.update_sl(state, symbol, entry, be_active=True)
                    sl = entry; be_active = True
                    tg.send_raw(
                        f"🔒 *Breakeven* — `{symbol}`\n"
                        f"SL movido a entrada `{entry:.6f}`"
                    )
                    log.info(f"BREAKEVEN {symbol} @ {price:.6f}")

            if be_active and trail_dist > 0:
                if side == "long":
                    t = price - trail_dist
                    if t > sl:
                        st.update_sl(state, symbol, t); sl = t
                else:
                    t = price + trail_dist
                    if t < sl:
                        st.update_sl(state, symbol, t); sl = t

            sl_hit = (side == "long"  and price <= sl) or \
                     (side == "short" and price >= sl)
            tp_hit = (side == "long"  and price >= tp) or \
                     (side == "short" and price <= tp)

            reason = None; exit_p = price
            if   sl_hit: exit_p = sl; reason = "SL"
            elif tp_hit: exit_p = tp; reason = "TP"

            if reason:
                result = trader.close_position(symbol, side, pos["qty"])
                pct    = (exit_p-entry)/entry if side=="long" \
                         else (entry-exit_p)/entry
                pnl    = pos["qty"] * entry * pct * cfg.LEVERAGE
                st.remove_position(state, symbol, pnl, pnl > 0)
                tg.send_close(symbol, side, entry, exit_p,
                              pnl, reason, result["ok"])
                log.info(
                    f"CLOSE {side} {symbol} @ {exit_p:.6f} "
                    f"PnL={pnl:+.4f} {reason}"
                )

        except Exception as e:
            log.error(f"manage_position {symbol}: {e}")
        time.sleep(0.3)


def main():
    log.info("=" * 60)
    log.info(f"4H RANGE FAKEOUT {cfg.VERSION} — PIONEX FUTURES")
    log.info(f"Rango London: {cfg.RANGE_4H_OPEN_UTC}:00 UTC")
    log.info(f"Sesión NY: {cfg.NY_SESSION_START}:00-{cfg.NY_SESSION_END}:00 UTC")
    log.info(f"MAX_BARS={cfg.MAX_BARS_OUTSIDE} ATR={cfg.MAX_SL_ATR}x "
             f"EMA200={cfg.USE_EMA200} DRY={cfg.DRY_RUN}")

    try:
        SYMBOLS = symbols_loader.get_all_symbols()
        log.info(f"Pares cargados: {len(SYMBOLS)}")
    except Exception as e:
        log.error(f"Error cargando símbolos: {e}")
        SYMBOLS = symbols_loader._fallback()
        log.info(f"Usando fallback: {len(SYMBOLS)} pares")

    log.info("=" * 60)

    state       = st.load()
    balance     = trader.get_balance()
    ranges      = {}
    last_status = time.time()
    last_reload = time.time()
    cycle       = 0

    try:
        tg.send_startup(f"{len(SYMBOLS)} pares USDT Pionex Futures")
    except Exception as e:
        log.error(f"send_startup: {e}")

    while True:
        try:
            cycle  += 1
            now_utc = datetime.now(timezone.utc)
            state   = st.load()

            try:
                balance = trader.get_balance()
            except Exception as e:
                log.warning(f"get_balance: {e}")

            # Recargar pares cada hora
            if time.time() - last_reload > 3600:
                try:
                    SYMBOLS     = symbols_loader.get_all_symbols()
                    last_reload = time.time()
                    log.info(f"Pares recargados: {len(SYMBOLS)}")
                except Exception as e:
                    log.warning(f"reload: {e}")

            # Circuit breaker
            try:
                if state.get("cb_active") or st.check_cb(state, balance, cfg):
                    if cycle % 20 == 1:
                        log.warning("CIRCUIT BREAKER activo")
                        tg.send_cb(state["stats"])
                    time.sleep(cfg.LOOP_SECONDS * 20)
                    continue
            except Exception as e:
                log.error(f"circuit breaker check: {e}")

            # Gestionar posiciones abiertas
            if state.get("positions"):
                manage_open_positions(state)
                state = st.load()

            open_count = len(state.get("positions", {}))

            # Log periódico
            if cycle % 10 == 1:
                in_s = strategy.in_session()
                log.info(
                    f"Ciclo {cycle} | "
                    f"{'SESION NY' if in_s else 'FUERA SESION'} | "
                    f"{now_utc.strftime('%H:%M UTC')} | "
                    f"Pos:{open_count} | "
                    f"Bal:${balance:.2f}"
                )

            # Buscar señales solo en sesión
            if strategy.in_session() and open_count < cfg.MAX_POSITIONS:
                for i, symbol in enumerate(SYMBOLS):
                    if symbol in state.get("positions", {}):
                        continue
                    if len(state.get("positions", {})) >= cfg.MAX_POSITIONS:
                        break
                    if i > 0 and i % 10 == 0:
                        time.sleep(1)

                    try:
                        # Refrescar rango
                        cached    = ranges.get(symbol)
                        today_str = str(now_utc.date())
                        need_ref  = (
                            cached is None or
                            cached.get("date") != today_str or
                            (strategy.is_weekend() and
                             time.time() - cached.get("fetched_at", 0) > 3600)
                        )
                        if need_ref:
                            rng = strategy.get_london_range(symbol)
                            if rng:
                                rng["fetched_at"] = time.time()
                                ranges[symbol]    = rng
                                log.info(
                                    f"Rango {symbol} "
                                    f"{'[FDS]' if rng.get('weekend') else '[LDN]'}: "
                                    f"H={rng['high']:.4f} L={rng['low']:.4f}"
                                )
                            time.sleep(0.4)
                            continue

                        traded_h, traded_l = st.traded(state, symbol)
                        if traded_h and traded_l:
                            continue

                        sig = strategy.detect_signal(
                            symbol, ranges[symbol],
                            traded_high=traded_h,
                            traded_low=traded_l,
                        )
                        if sig is None:
                            time.sleep(0.2)
                            continue

                        # Ejecutar señal
                        balance  = trader.get_balance()
                        executed = False

                        if balance < 10.0 and not cfg.DRY_RUN:
                            tg.send_no_funds(symbol, sig, balance)
                            time.sleep(0.3)
                            continue

                        qty = trader.calc_qty(
                            symbol, sig["entry"], balance, sig["sl"]
                        )
                        if qty > 0:
                            if cfg.DRY_RUN:
                                executed = True
                            else:
                                res = trader.open_position(
                                    symbol, sig["side"],
                                    qty, sig["sl"], sig["tp"]
                                )
                                executed = res.get("ok", False)
                            if executed:
                                st.add_position(
                                    state, symbol, sig["side"],
                                    sig["entry"], qty,
                                    sig["sl"], sig["tp"],
                                    sig["be_trigger"], sig["trail_dist"],
                                    sig["level"],
                                )

                        tg.send_signal(symbol, sig, balance, executed)
                        log.info(
                            f"SIGNAL {sig['side'].upper()} {symbol} "
                            f"e={sig['entry']:.6f} rr={sig['rr']} "
                            f"exec={executed}"
                        )
                        time.sleep(1)

                    except Exception as e:
                        log.error(f"scan {symbol}: {e}")
                        time.sleep(0.5)

            # Reporte horario
            if time.time() - last_status >= 3600:
                try:
                    plist = [
                        {**{"symbol": s}, **p}
                        for s, p in state.get("positions", {}).items()
                    ]
                    for p in plist:
                        p["current"] = (trader.get_price(p["symbol"])
                                        or p["entry"])
                    tg.send_status(plist, balance, state["stats"])
                except Exception as e:
                    log.error(f"send_status: {e}")
                last_status = time.time()

        except KeyboardInterrupt:
            log.info("Detenido manualmente")
            tg.send_raw("🛑 *Bot detenido.*")
            break
        except Exception as e:
            log.error(f"ERROR CICLO: {e}")
            log.error(traceback.format_exc())
            try:
                tg.send_error(str(e)[:200])
            except Exception:
                pass

        time.sleep(cfg.LOOP_SECONDS)


if __name__ == "__main__":
    main()
