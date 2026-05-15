"""main.py — Sniper Bot V49 Definitivo
Loop principal: fetch → indicadores → señal → orden → Telegram
"""
import time
import logging
import colorlog
from datetime import datetime, date
from config import (
    SYMBOL, TIMEFRAME, LOOP_INTERVAL, MODE,
    ATR_MULT_TP, ATR_MULT_SL, MAX_BARS_HOLD,
)
from indicators import compute, MarkovEngine
from exchange  import (
    build_exchange, fetch_ohlcv, get_balance,
    calc_qty, open_long, open_short,
    close_position, get_position,
)
import telegram_bot as tg


# ── Logger con colores ────────────────────────────────────────
def setup_logger() -> logging.Logger:
    handler = colorlog.StreamHandler()
    handler.setFormatter(colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s [%(levelname)s]%(reset)s %(message)s",
        datefmt="%H:%M:%S",
        log_colors={"DEBUG": "cyan", "INFO": "green",
                    "WARNING": "yellow", "ERROR": "red"},
    ))
    fh = logging.FileHandler("logs/bot.log")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.addHandler(fh)
    return logger


log = setup_logger()


# ── Estado del bot ────────────────────────────────────────────
class BotState:
    def __init__(self):
        self.position      = None    # "long" | "short" | None
        self.entry_price   = 0.0
        self.entry_qty     = 0.0
        self.entry_bar     = 0
        self.bar_count     = 0
        self.trades_today  = 0
        self.last_hb_date  = None    # fecha último heartbeat diario
        self.last_signal   = None    # evita señales duplicadas


state = BotState()
markov = MarkovEngine()


# ── Loop principal ────────────────────────────────────────────
def tick(ex):
    global state, markov
    state.bar_count += 1

    # 1. Datos
    df = fetch_ohlcv(ex, SYMBOL, TIMEFRAME)
    if df.empty or len(df) < 210:
        log.warning("No hay suficientes velas. Esperando...")
        return

    # 2. Indicadores V49
    ind = compute(df, markov)
    log.info(
        f"[{SYMBOL}] close={ind['close']:.4f} | "
        f"slope={ind['slope']:.1f} adx={ind['adx']:.1f} | "
        f"bull={ind['prob_bull']:.1f}% bear={ind['prob_bear']:.1f}% | "
        f"rvol={ind['rvol']:.2f}x stc={ind['stc']:.1f} | "
        f"dense={'✓' if ind['is_dense'] else '✗'}"
    )

    balance = get_balance(ex)

    # 3. Heartbeat diario a las 08:00 UTC
    now = datetime.utcnow()
    if now.hour == 8 and state.last_hb_date != date.today():
        tg.send(tg.msg_heartbeat(ind, balance, state.trades_today))
        state.last_hb_date = date.today()

    # 4. Gestión de posición abierta
    if state.position:
        bars_held   = state.bar_count - state.entry_bar
        price_now   = ind["close"]
        atr_now     = ind["atr14"]
        tp_price    = (state.entry_price + atr_now * ATR_MULT_TP
                       if state.position == "long"
                       else state.entry_price - atr_now * ATR_MULT_TP)
        sl_price    = (state.entry_price - atr_now * ATR_MULT_SL
                       if state.position == "long"
                       else state.entry_price + atr_now * ATR_MULT_SL)

        hit_tp = (price_now >= tp_price if state.position == "long" else price_now <= tp_price)
        hit_sl = (price_now <= sl_price if state.position == "long" else price_now >= sl_price)
        hit_time = bars_held >= MAX_BARS_HOLD

        reason = None
        if hit_tp:   reason = "TP alcanzado"
        elif hit_sl: reason = "SL alcanzado"
        elif hit_time: reason = f"Tiempo máximo ({MAX_BARS_HOLD} velas)"

        if reason:
            close_position(ex, SYMBOL)
            tg.send(tg.msg_close(
                state.position, state.entry_price, price_now,
                state.entry_qty, reason, get_balance(ex)
            ))
            state.position  = None
            state.trades_today += 1
            log.info(f"Posición cerrada: {reason} @ {price_now:.4f}")
        return   # no busca nuevas señales con posición abierta

    # 5. Señales de entrada
    if ind["long"] and state.last_signal != "long":
        qty = calc_qty(balance, ind["close"], ind["atr14"])
        if qty <= 0:
            log.warning("Cantidad calculada = 0. Revisa el balance.")
            return
        tp  = round(ind["close"] + ind["atr14"] * ATR_MULT_TP, 4)
        sl  = round(ind["close"] - ind["atr14"] * ATR_MULT_SL, 4)

        order = open_long(ex, SYMBOL, qty, ind["close"], ind["atr14"])
        if order:
            state.position    = "long"
            state.entry_price = ind["close"]
            state.entry_qty   = qty
            state.entry_bar   = state.bar_count
            state.last_signal = "long"
            tg.send(tg.msg_signal("long", ind, ind["close"], qty, tp, sl, balance))
            log.info(f"LONG abierto @ {ind['close']:.4f} | TP {tp} | SL {sl}")

    elif ind["short"] and state.last_signal != "short":
        qty = calc_qty(balance, ind["close"], ind["atr14"])
        if qty <= 0:
            log.warning("Cantidad calculada = 0. Revisa el balance.")
            return
        tp  = round(ind["close"] - ind["atr14"] * ATR_MULT_TP, 4)
        sl  = round(ind["close"] + ind["atr14"] * ATR_MULT_SL, 4)

        order = open_short(ex, SYMBOL, qty, ind["close"], ind["atr14"])
        if order:
            state.position    = "short"
            state.entry_price = ind["close"]
            state.entry_qty   = qty
            state.entry_bar   = state.bar_count
            state.last_signal = "short"
            tg.send(tg.msg_signal("short", ind, ind["close"], qty, tp, sl, balance))
            log.info(f"SHORT abierto @ {ind['close']:.4f} | TP {tp} | SL {sl}")

    else:
        state.last_signal = None   # reset para que la próxima señal válida entre


def main():
    log.info("═" * 50)
    log.info("  Sniper Bot V49 Definitivo — Arrancando...")
    log.info("═" * 50)

    ex = build_exchange()
    log.info(f"Exchange: BingX | Par: {SYMBOL} | TF: {TIMEFRAME} | Modo: {MODE.upper()}")

    bal = get_balance(ex)
    log.info(f"Balance inicial: ${bal:.2f} USDT")
    tg.send(tg.msg_start(SYMBOL, TIMEFRAME, MODE))

    while True:
        try:
            tick(ex)
        except KeyboardInterrupt:
            log.info("Bot detenido manualmente.")
            tg.send("🔴 <b>Bot detenido manualmente.</b>")
            break
        except Exception as e:
            log.error(f"Error en tick: {e}")
            tg.send(tg.msg_error("tick principal", str(e)))
        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    main()
