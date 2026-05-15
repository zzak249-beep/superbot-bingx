"""exchange.py — Cliente BingX (CCXT) para Sniper Bot V49"""
import logging
import ccxt
import pandas as pd
from config import (
    BINGX_API_KEY, BINGX_API_SECRET,
    SYMBOL, TIMEFRAME, CANDLES_LIMIT,
    LEVERAGE, MODE, RISK_PCT,
    ATR_MULT_TP, ATR_MULT_SL,
)

log = logging.getLogger("exchange")


def build_exchange() -> ccxt.bingx:
    ex = ccxt.bingx({
        "apiKey":  BINGX_API_KEY,
        "secret":  BINGX_API_SECRET,
        "options": {"defaultType": "swap"},   # perpetual futures
    })
    ex.load_markets()
    if LEVERAGE > 1:
        try:
            ex.set_leverage(LEVERAGE, SYMBOL)
        except Exception as e:
            log.warning(f"No se pudo setear apalancamiento: {e}")
    return ex


def fetch_ohlcv(ex: ccxt.bingx, symbol: str = SYMBOL,
                timeframe: str = TIMEFRAME,
                limit: int = CANDLES_LIMIT) -> pd.DataFrame:
    raw = ex.fetch_ohlcv(symbol, timeframe, limit=limit)
    df  = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df.astype(float)


def get_balance(ex: ccxt.bingx) -> float:
    """Devuelve el balance disponible en USDT."""
    try:
        bal = ex.fetch_balance()
        return float(bal["USDT"]["free"])
    except Exception as e:
        log.error(f"Error obteniendo balance: {e}")
        return 0.0


def calc_qty(balance: float, price: float, atr: float) -> float:
    """
    Calidad de posición usando el riesgo fijo en % del balance.
    SL = ATR_MULT_SL × ATR  →  qty = (balance × RISK_PCT%) / SL_en_USDT
    """
    risk_usdt = balance * (RISK_PCT / 100)
    sl_dist   = atr * ATR_MULT_SL
    if sl_dist <= 0 or price <= 0:
        return 0.0
    qty = risk_usdt / sl_dist
    return round(qty, 4)


def open_long(ex: ccxt.bingx, symbol: str, qty: float,
              price: float, atr: float) -> dict | None:
    if MODE == "paper":
        log.info(f"[PAPER] LONG {qty} {symbol} @ {price:.4f}")
        return {"id": "paper_long", "price": price, "qty": qty}
    try:
        tp = round(price + atr * ATR_MULT_TP, 4)
        sl = round(price - atr * ATR_MULT_SL, 4)
        order = ex.create_order(
            symbol, "market", "buy", qty,
            params={"takeProfit": {"triggerPrice": tp},
                    "stopLoss":   {"triggerPrice": sl}})
        log.info(f"LONG abierto: {order['id']} | TP {tp} | SL {sl}")
        return order
    except Exception as e:
        log.error(f"Error abriendo LONG: {e}")
        return None


def open_short(ex: ccxt.bingx, symbol: str, qty: float,
               price: float, atr: float) -> dict | None:
    if MODE == "paper":
        log.info(f"[PAPER] SHORT {qty} {symbol} @ {price:.4f}")
        return {"id": "paper_short", "price": price, "qty": qty}
    try:
        tp = round(price - atr * ATR_MULT_TP, 4)
        sl = round(price + atr * ATR_MULT_SL, 4)
        order = ex.create_order(
            symbol, "market", "sell", qty,
            params={"takeProfit": {"triggerPrice": tp},
                    "stopLoss":   {"triggerPrice": sl}})
        log.info(f"SHORT abierto: {order['id']} | TP {tp} | SL {sl}")
        return order
    except Exception as e:
        log.error(f"Error abriendo SHORT: {e}")
        return None


def close_position(ex: ccxt.bingx, symbol: str) -> bool:
    if MODE == "paper":
        log.info(f"[PAPER] CIERRE posición {symbol}")
        return True
    try:
        pos = ex.fetch_positions([symbol])
        for p in pos:
            if p["contracts"] and float(p["contracts"]) > 0:
                side  = "sell" if p["side"] == "long" else "buy"
                ex.create_order(symbol, "market", side, abs(float(p["contracts"])),
                                params={"reduceOnly": True})
        return True
    except Exception as e:
        log.error(f"Error cerrando posición: {e}")
        return False


def get_position(ex: ccxt.bingx, symbol: str) -> dict | None:
    """Devuelve la posición abierta o None."""
    if MODE == "paper":
        return None
    try:
        positions = ex.fetch_positions([symbol])
        for p in positions:
            if p["contracts"] and float(p["contracts"]) > 0:
                return p
        return None
    except Exception as e:
        log.error(f"Error obteniendo posición: {e}")
        return None
