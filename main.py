"""
Mean Reversion Scanner — KIBITO
Estrategia: Bollinger Bands + RSI + ADX + Funding Rate
Lateral markets | LONG + SHORT
"""
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import config
import state
from bingx_client import BingXClient
from position_manager import PositionManager
from risk_manager import RiskManager
from strategy_mr import get_signal
from telegram_client import TelegramClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
log = logging.getLogger("scanner")


# ── Health server ─────────────────────────────────────────────────────────────
def _start_health_server():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *a): pass
    try:
        srv = HTTPServer(("0.0.0.0", config.PORT), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log.info(f"Health server :{config.PORT}/health")
    except Exception as e:
        log.warning(f"Health server: {e}")


# ── Exit helper ───────────────────────────────────────────────────────────────
def _exit(pos_mgr: PositionManager, risk: RiskManager, tg: TelegramClient,
          sym: str, side: str, size: float, price: float, pnl: float, reason: str):
    if side == "LONG":
        ok = pos_mgr.close_long(sym, size, reason)
    else:
        ok = pos_mgr.close_short(sym, size, reason)
    if ok:
        risk.record_trade(pnl)
        tg.exit_trade(config.BOT_NAME, sym, side, price, reason, pnl)


# ── Position management ───────────────────────────────────────────────────────
def _manage_positions(client: BingXClient, pos_mgr: PositionManager,
                      risk: RiskManager, tg: TelegramClient):
    positions = client.get_positions()
    for pos in positions:
        sym   = pos["positionSide"]
        sym   = pos["symbol"]
        side  = pos["positionSide"]
        size  = pos["size"]
        entry = pos["entryPrice"]
        pnl   = pos["unrealizedPnl"]

        if side not in ("LONG", "SHORT"):
            continue

        try:
            mark    = client.get_mark_price(sym)
            candles = client.get_klines(sym, config.TIMEFRAME, 50)
            if len(candles) < 20:
                continue

            sig = get_signal(candles, client.get_funding_rate(sym), config)
            atr = sig["atr"]
            if not atr:
                continue

            # 1. Max hold
            if pos_mgr.is_max_hold_expired(sym, side):
                _exit(pos_mgr, risk, tg, sym, side, size, mark, pnl, "max_hold")
                continue

            # 2. BB mid exit — reversión completada
            if pos_mgr.should_bb_exit(sym, side):
                _exit(pos_mgr, risk, tg, sym, side, size, mark, pnl, "bb_mid_exit")
                continue

            # 3. ATR trail stop
            stop, hit = pos_mgr.tick_trail(sym, side, mark, atr)
            if hit:
                _exit(pos_mgr, risk, tg, sym, side, size, mark, pnl, "trail_stop")
                continue

        except Exception as e:
            log.error(f"manage {sym} {side}: {e}")


# ── Signal scanning ───────────────────────────────────────────────────────────
def _scan_and_enter(client: BingXClient, pos_mgr: PositionManager,
                    risk: RiskManager, tg: TelegramClient,
                    equity: float) -> int:
    """Scan universe for MR signals. Returns number of new trades opened."""
    opened = 0
    try:
        symbols = client.get_top_symbols(config.TOP_N_SYMBOLS, config.MIN_VOLUME_USDT)
    except Exception as e:
        log.error(f"get_top_symbols: {e}")
        return 0

    for sym in symbols:
        if sym in config.BLACKLIST:
            continue

        if pos_mgr.count_open() >= config.MAX_OPEN_TRADES:
            break

        allowed, reason = risk.can_trade(equity)
        if not allowed:
            log.warning(f"Risk block: {reason}")
            break

        # Skip if already in this symbol (either side)
        has_long  = pos_mgr.has_position(sym, "LONG")
        has_short = pos_mgr.has_position(sym, "SHORT")
        if has_long and has_short:
            continue

        try:
            candles = client.get_klines(sym, config.TIMEFRAME, 60)
            if len(candles) < 30:
                continue

            funding = client.get_funding_rate(sym)
            sig = get_signal(candles, funding, config)

            if not sig["signal"]:
                continue

            direction = sig["signal"]
            if direction == "LONG" and config.DIRECTION not in ("LONG", "BOTH"):
                continue
            if direction == "SHORT" and config.DIRECTION not in ("SHORT", "BOTH"):
                continue
            if direction == "LONG"  and has_long:
                continue
            if direction == "SHORT" and has_short:
                continue

            mark = client.get_mark_price(sym)
            atr  = sig["atr"]
            qty  = pos_mgr.calc_qty(sym, mark, atr, equity)
            if qty is None:
                continue

            log.info(
                f"MR signal {direction} {sym}  "
                f"RSI={sig['rsi']:.1f}  ADX={sig['adx']:.1f}  "
                f"BB={sig['bb_lower']:.4g}/{sig['bb_upper']:.4g}  "
                f"move%={sig.get('move_pct', 0):.3f}  "
                f"fund={funding:.4f}  atr={atr:.4g}"
            )

            if direction == "LONG":
                ok = pos_mgr.open_long(sym, qty, atr)
            else:
                ok = pos_mgr.open_short(sym, qty, atr)

            if ok:
                pos_mgr.place_tp_sl(sym, direction, qty, mark, atr)
                tg.entry(config.BOT_NAME, sym, direction, mark, qty, None, equity)
                opened += 1

        except Exception as e:
            log.error(f"scan {sym}: {e}")

    return opened


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    _start_health_server()
    log.info(f"=== {config.BOT_NAME} starting  tf={config.TIMEFRAME}  dir={config.DIRECTION} ===")

    client  = BingXClient(config.API_KEY, config.SECRET_KEY, config.BASE_URL)
    pos_mgr = PositionManager(client)
    risk    = RiskManager(config)
    tg      = TelegramClient(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT)

    equity = client.get_equity()
    risk.new_day(equity)
    log.info(f"New day — equity: {equity:.2f} USDT")

    last_scan_t = 0.0
    iteration   = 0

    while True:
        try:
            now    = time.time()
            equity = client.get_equity()

            # Manage existing positions every tick
            _manage_positions(client, pos_mgr, risk, tg)

            # Scan for new entries on interval
            if now - last_scan_t >= config.SCAN_INTERVAL:
                last_scan_t = now
                iteration  += 1

                allowed, _ = risk.can_trade(equity)
                if allowed:
                    t0 = time.time()
                    opened = _scan_and_enter(client, pos_mgr, risk, tg, equity)
                    elapsed = time.time() - t0
                    open_total = pos_mgr.count_open()
                    log.info(
                        f"scanner | Iter {iteration} | {config.TOP_N_SYMBOLS} símbolos "
                        f"| {opened} abiertos | {elapsed:.1f}s "
                        f"| posiciones={open_total}"
                    )

        except KeyboardInterrupt:
            log.info("Stopping.")
            break
        except Exception as e:
            log.error(f"Loop error: {e}")
            tg.error(config.BOT_NAME, str(e)[:400])
            time.sleep(30)

        time.sleep(config.TRAILING_CHECK_SEC)


if __name__ == "__main__":
    main()
