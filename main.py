"""
main.py — Orquestador principal del bot.

Mejoras v2:
  [1] TRAILING STOP   — el SL sube (LONG) o baja (SHORT) a medida que
                         el precio se aleja. Se activa al superar TP1.
  [2] DOBLE TP        — TP1 en 1.5R cierra el 50 % de la posición y
                         activa el trailing. TP2 en 2.5R cierra el resto.
  [3] CIRCUIT BREAKER — si la pérdida diaria supera MAX_DAILY_LOSS_PCT
                         (default 3 %), el bot congela nuevas entradas
                         hasta la medianoche UTC siguiente.

Flujo de ejecución:
1. Startup: configura leverage, sincroniza posiciones, siembra buffers
2. WebSocket: klines en tiempo real
3. Por cada cierre de vela LTF → evalúa señal → ejecuta si válida
4. Monitor (cada 5s): trailing stop, TP1/TP2 parciales, circuit breaker
5. Telegram: notifica todo el ciclo de vida
"""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Set

from dotenv import load_dotenv
load_dotenv()

from config import config
from exchange.bingx_rest import bingx, BingXRestError
from exchange.bingx_ws import ws_client
from notifications.telegram_notifier import telegram
from strategy.engine import engine, SignalResult
from utils.symbol_scanner import symbol_scanner

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("main")

# ── Parámetros de las mejoras ─────────────────────────────────────────────────
# [1] Trailing: el SL se mueve cada vez que el precio gana X ATRs extras
TRAIL_STEP_ATR   = float(os.getenv("TRAIL_STEP_ATR", "0.5"))
# [2] TP parcial: fracción del tamaño que se cierra en TP1
TP1_FRACTION     = float(os.getenv("TP1_FRACTION", "0.5"))
# [2] TP1 en R (múltiplo de ATR_SL), TP2 = ATR_TP_MULT del config
TP1_ATR_MULT     = float(os.getenv("TP1_ATR_MULT", "1.5"))
# [3] Pérdida diaria máxima como fracción del balance inicial del día
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.03"))  # 3 %


# ── Estado global ─────────────────────────────────────────────────────────────
class BotState:
    def __init__(self):
        # symbol → {direction, entry, sl, tp1, tp2, size, size_remaining,
        #            tp1_hit, trailing_active, atr, order_id, synced}
        self.open_positions: Dict[str, dict] = {}
        self.locked_symbols: Set[str] = set()
        self.balance: float = 0.0

        # [3] Circuit breaker
        self.day_start_balance: float = 0.0   # balance al inicio del día
        self.daily_pnl: float = 0.0           # PnL acumulado hoy
        self.circuit_open: bool = False        # True = no abrir nuevas posiciones
        self._cb_day: int = -1                 # día UTC en que se calculó

    def record_pnl(self, pnl_usdt: float):
        """Actualiza el PnL diario y evalúa el circuit breaker."""
        self.daily_pnl += pnl_usdt
        if self.day_start_balance > 0:
            loss_pct = -self.daily_pnl / self.day_start_balance
            if loss_pct >= MAX_DAILY_LOSS_PCT and not self.circuit_open:
                self.circuit_open = True
                log.warning(
                    f"🛑 CIRCUIT BREAKER activado: pérdida diaria "
                    f"{loss_pct*100:.1f}% ≥ {MAX_DAILY_LOSS_PCT*100:.1f}%"
                )
                asyncio.create_task(telegram.notify_error(
                    f"🛑 Circuit breaker activado\n"
                    f"Pérdida del día: {self.daily_pnl:.2f} USDT "
                    f"({loss_pct*100:.1f}%)\n"
                    f"No se abrirán nuevas posiciones hasta medianoche UTC."
                ))

    def reset_daily_if_needed(self):
        """Reinicia contadores al inicio de un nuevo día UTC."""
        today = datetime.now(timezone.utc).timetuple().tm_yday
        if today != self._cb_day:
            self._cb_day = today
            self.daily_pnl = 0.0
            if self.circuit_open:
                self.circuit_open = False
                log.info("✅ Circuit breaker reseteado — nuevo día UTC")
                asyncio.create_task(telegram.send(
                    "✅ <b>Circuit breaker reseteado</b> — nuevo día UTC"
                ))
            if self.balance > 0:
                self.day_start_balance = self.balance


state = BotState()


# ── Seed buffers ──────────────────────────────────────────────────────────────

async def seed_buffers():
    log.info("Sembrando buffers con datos históricos...")
    tasks = []
    for symbol in config.SYMBOLS:
        for tf in [config.HTF, config.MTF, config.LTF]:
            buf = ws_client.get_buffer(symbol, tf)
            if buf:
                tasks.append(_seed_one(symbol, tf, buf))
    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("Buffers sembrados.")


async def seed_buffers_dynamic(symbols: list):
    """Versión de seed_buffers que acepta una lista explícita de símbolos."""
    log.info(f"Sembrando buffers para {len(symbols)} símbolos...")
    tasks = []
    for symbol in symbols:
        for tf in [config.HTF, config.MTF, config.LTF]:
            buf = ws_client.get_buffer(symbol, tf)
            if buf:
                tasks.append(_seed_one(symbol, tf, buf))
    # Batch de 20 para no saturar la API
    batch_size = 20
    for i in range(0, len(tasks), batch_size):
        await asyncio.gather(*tasks[i:i+batch_size], return_exceptions=True)
        if i + batch_size < len(tasks):
            await asyncio.sleep(0.5)
    log.info("Buffers sembrados.")


async def _seed_one(symbol: str, interval: str, buf):
    try:
        klines = await bingx.get_klines(symbol, interval, limit=200)
        buf.seed(klines)
        log.debug(f"  {symbol} {interval}: {len(klines)} velas cargadas")
    except Exception as e:
        log.warning(f"  Error seed {symbol} {interval}: {e}")


# ── Sync posiciones ────────────────────────────────────────────────────────────

async def sync_positions():
    log.info("Sincronizando posiciones abiertas...")
    try:
        all_positions = await bingx.get_positions()
        for pos in all_positions:
            amt = float(pos.get("positionAmt", 0))
            if abs(amt) > 0:
                sym = pos.get("symbol", "")
                direction = "LONG" if amt > 0 else "SHORT"
                entry = float(pos.get("avgPrice", pos.get("entryPrice", 0)))
                state.open_positions[sym] = {
                    "direction": direction,
                    "entry": entry,
                    "sl": 0.0,
                    "tp1": 0.0,
                    "tp2": 0.0,
                    "size": abs(amt),
                    "size_remaining": abs(amt),
                    "tp1_hit": False,
                    "trailing_active": False,
                    "atr": 0.0,
                    "synced": True,
                }
                log.info(f"  Posición existente: {sym} {direction} entry={entry}")
    except Exception as e:
        log.warning(f"Sync posiciones: {e}")


# ── Setup ─────────────────────────────────────────────────────────────────────

async def setup_leverage(symbols: list = None):
    syms = symbols or config.SYMBOLS
    log.info(f"Configurando leverage para {len(syms)} símbolos...")
    tasks = [bingx.set_leverage(s, config.LEVERAGE) for s in syms]
    await asyncio.gather(*tasks, return_exceptions=True)


async def refresh_balance():
    try:
        state.balance = await bingx.get_balance()
        log.debug(f"Balance: ${state.balance:.2f} USDT")
    except Exception as e:
        log.warning(f"Balance error: {e}")


# ── Ejecutar trade ─────────────────────────────────────────────────────────────

async def execute_trade(sig: SignalResult):
    symbol = sig.symbol

    # [3] Circuit breaker
    if state.circuit_open:
        log.info(f"🛑 Circuit breaker activo — señal {symbol} ignorada")
        return

    if symbol in state.locked_symbols:
        log.debug(f"{symbol} bloqueado, ignorando señal")
        return

    if symbol in state.open_positions:
        log.debug(f"{symbol} ya tiene posición abierta")
        return

    if len(state.open_positions) >= config.MAX_POSITIONS:
        log.info(f"Máximo de posiciones ({config.MAX_POSITIONS}) alcanzado")
        return

    if state.balance <= 0:
        await refresh_balance()
    if state.balance < sig.size_usdt:
        log.warning(f"Balance insuficiente: ${state.balance:.2f} < ${sig.size_usdt:.2f}")
        return

    state.locked_symbols.add(symbol)
    try:
        price   = sig.entry_price
        qty_raw = (sig.size_usdt * config.LEVERAGE) / price
        min_qty = await bingx.get_min_qty(symbol)
        qty     = round(max(qty_raw, min_qty), 6)

        side     = "BUY" if sig.direction == "LONG" else "SELL"
        pos_side = "BOTH" if config.POSITION_MODE == "ONE_WAY" else sig.direction

        order    = await bingx.place_market_order(symbol, side, qty, pos_side)
        order_id = str(order.get("orderId", "?"))

        # [2] Calcular TP1 y TP2 separados
        atr = sig.atr
        if sig.direction == "LONG":
            tp1 = price + atr * TP1_ATR_MULT
            tp2 = price + atr * config.ATR_TP_MULT
            sl  = sig.sl_price
        else:
            tp1 = price - atr * TP1_ATR_MULT
            tp2 = price - atr * config.ATR_TP_MULT
            sl  = sig.sl_price

        state.open_positions[symbol] = {
            "direction":       sig.direction,
            "entry":           price,
            "sl":              sl,
            "tp1":             tp1,
            "tp2":             tp2,
            "size":            qty,
            "size_remaining":  qty,
            "tp1_hit":         False,
            "trailing_active": False,
            "atr":             atr,
            "order_id":        order_id,
        }

        log.info(f"✅ {symbol} {sig.direction} qty={qty} "
                 f"SL={sl:.4f} TP1={tp1:.4f} TP2={tp2:.4f}")

        await telegram.notify_order(
            symbol, sig.direction, price, sl, tp2, sig.size_usdt, order_id
        )
        await telegram.send(
            f"📐 <b>Niveles</b> {symbol}\n"
            f"TP1 (50%): <code>{tp1:.4f}</code>\n"
            f"TP2 (50%): <code>{tp2:.4f}</code>\n"
            f"Trailing activa tras TP1"
        )

    except BingXRestError as e:
        log.error(f"BingX order error {symbol}: {e}")
        await telegram.notify_error(f"{symbol}: {e}")
    except Exception as e:
        log.error(f"Execute trade error {symbol}: {e}")
    finally:
        state.locked_symbols.discard(symbol)


# ── Monitor de posiciones ─────────────────────────────────────────────────────

async def monitor_positions():
    """
    Revisa cada posición abierta:
      • TP1 → cierra 50 %, activa trailing
      • Trailing → mueve SL step a step
      • TP2 / SL → cierra el resto
    """
    if not state.open_positions:
        return

    for symbol in list(state.open_positions.keys()):
        if symbol not in state.open_positions:
            continue
        pos = state.open_positions[symbol]

        if pos.get("sl", 0) == 0 and pos.get("tp2", 0) == 0:
            continue  # posición sincronizada sin datos — skip

        try:
            price = await bingx.get_price(symbol)
            if price == 0:
                continue

            direction = pos["direction"]
            sl        = pos["sl"]
            tp1       = pos["tp1"]
            tp2       = pos["tp2"]
            atr       = pos.get("atr", 0)
            tp1_hit   = pos["tp1_hit"]
            size_rem  = pos["size_remaining"]
            entry     = pos["entry"]

            # ── [2] TP1: cierra la mitad ──────────────────────────────────
            if not tp1_hit:
                hit_tp1 = (direction == "LONG"  and price >= tp1) or \
                          (direction == "SHORT" and price <= tp1)
                if hit_tp1:
                    qty_close = round(size_rem * TP1_FRACTION, 6)
                    close_side = "SELL" if direction == "LONG" else "BUY"
                    pos_side   = "BOTH" if config.POSITION_MODE == "ONE_WAY" else direction

                    await bingx.place_market_order(symbol, close_side, qty_close, pos_side)
                    pnl = _estimate_pnl(pos, price) * TP1_FRACTION

                    pos["tp1_hit"]         = True
                    pos["trailing_active"] = True
                    pos["size_remaining"]  = round(size_rem - qty_close, 6)
                    # Mover SL a break-even al activar trailing
                    pos["sl"]              = entry
                    state.record_pnl(pnl)

                    log.info(f"🎯 TP1 {symbol} price={price:.4f} "
                             f"cerrado {qty_close} pnl≈{pnl:.2f}")
                    await telegram.send(
                        f"🎯 <b>TP1 alcanzado</b> {symbol}\n"
                        f"Cerrado 50% a <code>{price:.4f}</code>\n"
                        f"PnL parcial: <code>+{pnl:.2f} USDT</code>\n"
                        f"SL movido a break-even <code>{entry:.4f}</code>\n"
                        f"Trailing activado → resto corre hasta TP2"
                    )
                    continue  # procesamos trailing en el siguiente ciclo

            # ── [1] Trailing Stop ─────────────────────────────────────────
            if pos["trailing_active"] and atr > 0:
                if direction == "LONG":
                    new_sl = price - atr * TRAIL_STEP_ATR
                    if new_sl > pos["sl"]:
                        pos["sl"] = new_sl
                        log.debug(f"  Trail {symbol} sl→{new_sl:.4f}")
                else:  # SHORT
                    new_sl = price + atr * TRAIL_STEP_ATR
                    if new_sl < pos["sl"]:
                        pos["sl"] = new_sl
                        log.debug(f"  Trail {symbol} sl→{new_sl:.4f}")

            # ── TP2 o SL: cierra el resto ─────────────────────────────────
            hit_tp2 = (direction == "LONG"  and price >= tp2) or \
                      (direction == "SHORT" and price <= tp2)
            hit_sl  = (direction == "LONG"  and price <= pos["sl"]) or \
                      (direction == "SHORT" and price >= pos["sl"])

            if hit_tp2 or hit_sl:
                reason = "TP2 alcanzado" if hit_tp2 else "SL alcanzado"
                await bingx.close_position(symbol)
                pnl = _estimate_pnl(pos, price, remaining_only=True)
                state.record_pnl(pnl)

                del state.open_positions[symbol]
                await telegram.notify_close(symbol, pnl, reason)
                log.info(f"{reason} {symbol} price={price:.4f} pnl≈{pnl:.2f}")

        except Exception as e:
            log.error(f"Monitor error {symbol}: {e}")


def _estimate_pnl(pos: dict, exit_price: float,
                   remaining_only: bool = False) -> float:
    entry     = pos.get("entry", 0)
    size      = pos.get("size_remaining", pos.get("size", 0)) if remaining_only \
                else pos.get("size", 0)
    direction = pos.get("direction", "LONG")
    if entry == 0:
        return 0.0
    raw = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
    return raw * size * config.LEVERAGE


# ── Callback cierre de vela ───────────────────────────────────────────────────

async def on_candle_close(symbol: str, interval: str, buf):
    if interval != config.LTF:
        return

    try:
        htf_buf = ws_client.get_buffer(symbol, config.HTF)
        mtf_buf = ws_client.get_buffer(symbol, config.MTF)
        if not htf_buf or not mtf_buf:
            return
        if (not htf_buf.ready(55) or not mtf_buf.ready(20) or not buf.ready(55)):
            return

        ho, hh, hl, hc, hv = htf_buf.to_arrays()
        mo, mh, ml, mc, mv = mtf_buf.to_arrays()
        lo, lh, ll, lc, lv = buf.to_arrays()

        sig = engine.evaluate(
            symbol,
            ho, hh, hl, hc, hv,
            mo, mh, ml, mc, mv,
            lo, lh, ll, lc, lv,
            balance=state.balance,
        )

        if sig.is_valid:
            log.info(f"🎯 Señal válida: {sig.summary()}")
            await telegram.notify_signal(sig.summary())
            await execute_trade(sig)

    except Exception as e:
        log.error(f"on_candle_close error {symbol} {interval}: {e}", exc_info=True)


# ── Loop principal ─────────────────────────────────────────────────────────────

async def main_loop():
    balance_interval = 60
    monitor_interval = 5
    rotation_interval = 3600   # rotar símbolos cada hora
    last_balance     = 0.0
    last_monitor     = 0.0
    last_rotation    = time.time()

    while True:
        now = time.time()

        # [3] Comprobar reset diario
        state.reset_daily_if_needed()

        if now - last_balance > balance_interval:
            await refresh_balance()
            # Registrar balance del día si aún no está fijado
            if state.day_start_balance == 0 and state.balance > 0:
                state.day_start_balance = state.balance
            last_balance = now

        if now - last_monitor > monitor_interval:
            await monitor_positions()
            last_monitor = now

        # Rotar símbolos cada hora — añade nuevos buffers para pares emergentes
        if now - last_rotation > rotation_interval:
            new_syms = await symbol_scanner.get_symbols(bingx)
            for sym in new_syms:
                for tf in [config.HTF, config.MTF, config.LTF]:
                    if not ws_client.get_buffer(sym, tf):
                        ws_client.add_buffer(sym, tf, maxlen=250)
                        await _seed_one(sym, tf, ws_client.get_buffer(sym, tf))
            last_rotation = now

        await asyncio.sleep(1.0)


# ── Startup ───────────────────────────────────────────────────────────────────

async def run():
    log.info("=" * 60)
    log.info("   🤖 BINGX BOT v2 — Trailing · Partial TP · Circuit Breaker")
    log.info("=" * 60)

    try:
        config.validate()
    except EnvironmentError as e:
        log.critical(f"Configuración inválida: {e}")
        sys.exit(1)

    telegram.start()
    await telegram.notify_start(config.SYMBOLS)

    await refresh_balance()

    state.day_start_balance = state.balance

    # ── Cargar lista dinámica de símbolos ─────────────────────────────────
    # Si SYMBOLS está definido en .env, úsalo; si no, descarga el top dinámico
    if len(config.SYMBOLS) <= 5 and config.SYMBOLS == ["BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT"]:
        log.info("SYMBOLS no configurado → cargando top dinámico de BingX...")
        active_symbols = await symbol_scanner.get_symbols(bingx)
    else:
        active_symbols = config.SYMBOLS
        log.info(f"Usando SYMBOLS del entorno: {active_symbols}")

    if not active_symbols:
        log.critical("No se obtuvieron símbolos. Verifica la conexión a BingX.")
        sys.exit(1)

    await setup_leverage(active_symbols)

    for symbol in active_symbols:
        for tf in [config.HTF, config.MTF, config.LTF]:
            ws_client.add_buffer(symbol, tf, maxlen=250)

    await seed_buffers_dynamic(active_symbols)
    await sync_positions()
    ws_client.on_candle_close(on_candle_close)

    log.info(f"Símbolos     : {len(active_symbols)} activos")
    log.info(f"Timeframes   : HTF={config.HTF} MTF={config.MTF} LTF={config.LTF}")
    log.info(f"Balance      : ${state.balance:.2f} USDT | Paper: {config.PAPER}")
    log.info(f"Trailing step: {TRAIL_STEP_ATR} ATR | TP1: {TP1_ATR_MULT}R "
             f"({TP1_FRACTION*100:.0f}%) | CB: {MAX_DAILY_LOSS_PCT*100:.1f}%/día")

    await asyncio.gather(ws_client.run(), main_loop())


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Bot detenido por el usuario.")
    finally:
        # Limpiar recursos: ejecutar cada coroutine por separado
        # (asyncio.gather falla si un coroutine ya fue garbage-collected)
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        for _coro in (bingx.close(), telegram.close()):
            try:
                _loop.run_until_complete(_coro)
            except Exception:
                pass
        try:
            _loop.close()
        except Exception:
            pass
