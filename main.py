"""
Saty Unified Strategy v7 — High WR
Python Bot for BingX Futures
"""

import os
import time
import logging
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime

# ==========================================
# LOGGING
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ==========================================
# CONFIG — set via environment variables
# ==========================================
API_KEY    = os.environ.get("BINGX_API_KEY", "")
API_SECRET = os.environ.get("BINGX_API_SECRET", "")
SYMBOL     = os.environ.get("SYMBOL", "BTC/USDT:USDT")   # Futures perpetual
TIMEFRAME  = os.environ.get("TIMEFRAME", "5m")
HTF        = os.environ.get("HTF_TIMEFRAME", "15m")
POLL_SECS  = int(os.environ.get("POLL_SECONDS", "60"))

# --- Strategy parameters ---
FAST_LEN      = 8
PIVOT_LEN     = 21
BIAS_LEN      = 48
ADX_LEN       = 14
ADX_MIN       = 20
RSI_LEN       = 14
RSI_LONG_MIN  = 52
RSI_LONG_MAX  = 75
RSI_SHORT_MIN = 25
RSI_SHORT_MAX = 48
ATR_LEN       = 14
ATR_VOL_MIN   = 0.0005      # min ATR as fraction of price
OSC_EMA_LEN   = 3
TP_MULT       = 2.5
SL_MULT       = 1.5
SHORT_TP_MULT = 2.5
SHORT_SL_MULT = 1.5
USE_TRAILING  = True
TRAIL_MULT    = 1.0
USE_HTF       = True
EQUITY_PCT    = 15          # % of equity per trade
VOL_MA_LEN    = 10


# ==========================================
# INDICATOR HELPERS
# ==========================================

def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(window=length).mean()


def atr(df: pd.DataFrame, length: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(span=length, adjust=False).mean()


def rsi(series: pd.Series, length: int) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(span=length, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=length, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def adx(df: pd.DataFrame, length: int):
    """Returns (DI+, DI-, ADX) as pd.Series."""
    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    up_move   = high.diff()
    down_move = -low.diff()

    plus_dm  = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    atr_vals = atr(df, length)

    di_plus  = 100 * plus_dm.ewm(span=length, adjust=False).mean()  / atr_vals
    di_minus = 100 * minus_dm.ewm(span=length, adjust=False).mean() / atr_vals

    dx       = (100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan))
    adx_vals = dx.ewm(span=length, adjust=False).mean()

    return di_plus, di_minus, adx_vals


def squeeze(df: pd.DataFrame, ema21: pd.Series, atr_vals: pd.Series) -> pd.Series:
    """True when Bollinger upper < Keltner upper (momentum squeeze)."""
    stdev    = df["close"].rolling(PIVOT_LEN).std()
    bb_upper = ema21 + 2.0 * stdev
    kc_upper = ema21 + 2.0 * atr_vals
    return bb_upper < kc_upper


def compute_indicators(df: pd.DataFrame):
    """Adds all indicator columns to df in-place."""
    df = df.copy()
    close  = df["close"]
    volume = df["volume"]
    high   = df["high"]
    low    = df["low"]

    df["ema8"]  = ema(close, FAST_LEN)
    df["ema21"] = ema(close, PIVOT_LEN)
    df["ema48"] = ema(close, BIAS_LEN)
    df["atr"]   = atr(df, ATR_LEN)

    df["raw_signal"] = ((close - df["ema21"]) / (3.0 * df["atr"])) * 100
    df["oscillator"] = ema(df["raw_signal"], OSC_EMA_LEN)

    df["osc_cross_up"]   = (df["oscillator"] > 0) & (df["oscillator"].shift() <= 0)
    df["osc_cross_down"] = (df["oscillator"] < 0) & (df["oscillator"].shift() >= 0)

    df["is_squeezing"] = squeeze(df, df["ema21"], df["atr"])

    # Volume pressure
    rng = (high - low).replace(0, np.nan)
    df["buy_vol"]          = volume * (close - low)  / rng
    df["sell_vol"]         = volume * (high - close) / rng
    df["buyers_dominant"]  = df["buy_vol"]  > df["sell_vol"]
    df["sellers_dominant"] = df["sell_vol"] > df["buy_vol"]

    # Filters
    di_plus, di_minus, adx_vals = adx(df, ADX_LEN)
    df["adx"]             = adx_vals
    df["trending_market"] = adx_vals > ADX_MIN

    df["rsi"]          = rsi(close, RSI_LEN)
    df["rsi_ok_long"]  = (df["rsi"] >= RSI_LONG_MIN)  & (df["rsi"] <= RSI_LONG_MAX)
    df["rsi_ok_short"] = (df["rsi"] >= RSI_SHORT_MIN) & (df["rsi"] <= RSI_SHORT_MAX)

    df["atr_pct"] = df["atr"] / close
    df["atr_ok"]  = df["atr_pct"] >= ATR_VOL_MIN

    df["vol_ma"]    = sma(volume, VOL_MA_LEN)
    df["vol_above"] = volume > df["vol_ma"] * 0.8

    return df


def compute_signals(df: pd.DataFrame, htf_bull: bool, htf_bear: bool) -> pd.DataFrame:
    c = df["close"]

    htf_ok_long  = (not USE_HTF) or htf_bull
    htf_ok_short = (not USE_HTF) or htf_bear

    df["base_long"] = (
        (c > df["ema48"]) &
        (df["ema8"] > df["ema21"]) &
        df["osc_cross_up"] &
        df["buyers_dominant"] &
        ~df["is_squeezing"]
    )

    df["filter_long"] = (
        df["trending_market"] &
        df["rsi_ok_long"] &
        df["atr_ok"] &
        df["vol_above"]
    )
    if htf_ok_long:
        df["long_entry"] = df["base_long"] & df["filter_long"]
    else:
        df["long_entry"] = False

    df["base_short"] = (
        (c < df["ema48"]) &
        (df["ema8"] < df["ema21"]) &
        df["osc_cross_down"] &
        df["sellers_dominant"] &
        ~df["is_squeezing"]
    )

    df["filter_short"] = (
        df["trending_market"] &
        df["rsi_ok_short"] &
        df["atr_ok"] &
        df["vol_above"]
    )
    if htf_ok_short:
        df["short_entry"] = df["base_short"] & df["filter_short"]
    else:
        df["short_entry"] = False

    return df


# ==========================================
# EXCHANGE WRAPPER
# ==========================================

def build_exchange() -> ccxt.Exchange:
    exchange = ccxt.bingx({
        "apiKey":    API_KEY,
        "secret":    API_SECRET,
        "options":   {"defaultType": "swap"},
    })
    exchange.load_markets()
    return exchange


def fetch_ohlcv(exchange: ccxt.Exchange, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df  = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df.astype(float)


def get_balance(exchange: ccxt.Exchange) -> float:
    bal = exchange.fetch_balance()
    return float(bal["USDT"]["free"])


def get_position(exchange: ccxt.Exchange, symbol: str) -> dict:
    """Returns current position dict or None."""
    positions = exchange.fetch_positions([symbol])
    for pos in positions:
        if abs(float(pos.get("contracts", 0) or 0)) > 0:
            return pos
    return None


def place_order(exchange: ccxt.Exchange, symbol: str, side: str, usdt_amount: float, atr_val: float):
    """Market entry + TP/SL bracket via stop orders."""
    price  = exchange.fetch_ticker(symbol)["last"]
    market = exchange.market(symbol)
    amount = usdt_amount / price
    amount = float(exchange.amount_to_precision(symbol, amount))

    log.info(f"[ORDER] {side.upper()} {amount} {symbol} @ ~{price:.4f}")

    # Market entry
    order = exchange.create_order(symbol, "market", side, amount)
    entry_price = float(order.get("average") or price)

    if side == "buy":
        tp_price = entry_price + atr_val * TP_MULT
        sl_price = entry_price - atr_val * SL_MULT
    else:
        tp_price = entry_price - atr_val * SHORT_TP_MULT
        sl_price = entry_price + atr_val * SHORT_SL_MULT

    tp_price = float(exchange.price_to_precision(symbol, tp_price))
    sl_price = float(exchange.price_to_precision(symbol, sl_price))

    close_side = "sell" if side == "buy" else "buy"

    # Take profit
    try:
        exchange.create_order(symbol, "limit", close_side, amount, tp_price,
                              {"reduceOnly": True})
        log.info(f"[TP] {close_side} @ {tp_price:.4f}")
    except Exception as e:
        log.warning(f"TP order failed: {e}")

    # Stop loss
    try:
        exchange.create_order(symbol, "stop_market", close_side, amount, None,
                              {"stopPrice": sl_price, "reduceOnly": True})
        log.info(f"[SL] {close_side} stop @ {sl_price:.4f}")
    except Exception as e:
        log.warning(f"SL order failed: {e}")

    return entry_price


def close_position(exchange: ccxt.Exchange, symbol: str, position: dict):
    """Market-close all open orders and flatten position."""
    try:
        exchange.cancel_all_orders(symbol)
    except Exception as e:
        log.warning(f"Cancel orders: {e}")

    contracts = float(position.get("contracts", 0))
    side      = position.get("side", "long")
    close_side = "sell" if side == "long" else "buy"
    try:
        exchange.create_order(symbol, "market", close_side, abs(contracts),
                              params={"reduceOnly": True})
        log.info(f"[CLOSE] Position closed: {side} {contracts}")
    except Exception as e:
        log.error(f"Close position failed: {e}")


# ==========================================
# MAIN LOOP
# ==========================================

def main():
    log.info("=== Saty Unified Strategy v7 — BingX Bot STARTING ===")

    if not API_KEY or not API_SECRET:
        log.warning("API keys not set — running in DRY-RUN mode (no orders placed).")
        dry_run = True
    else:
        dry_run = False

    if not dry_run:
        exchange = build_exchange()
        log.info(f"Connected to BingX | Symbol: {SYMBOL} | TF: {TIMEFRAME} | HTF: {HTF}")
    else:
        exchange = None

    open_side  = None   # "long" | "short" | None
    entry_price = None
    trail_high  = None
    trail_low   = None

    while True:
        try:
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            log.info(f"[{now}] Fetching data...")

            if not dry_run:
                df     = fetch_ohlcv(exchange, SYMBOL, TIMEFRAME, limit=250)
                df_htf = fetch_ohlcv(exchange, SYMBOL, HTF,       limit=100)
            else:
                # Simulate with random data for testing
                log.info("DRY-RUN: No live data. Set API keys to trade.")
                time.sleep(POLL_SECS)
                continue

            df     = compute_indicators(df)
            df_htf = compute_indicators(df_htf)

            # HTF bias from the last closed candle
            htf_close_price = df_htf["close"].iloc[-2]
            htf_ema48       = df_htf["ema48"].iloc[-2]
            htf_bull        = htf_close_price > htf_ema48
            htf_bear        = htf_close_price < htf_ema48

            df = compute_signals(df, htf_bull, htf_bear)

            # Use SECOND-TO-LAST candle (last closed candle)
            row = df.iloc[-2]
            atr_val = row["atr"]
            price   = row["close"]

            log.info(
                f"ADX={row['adx']:.1f} | RSI={row['rsi']:.1f} | "
                f"ATR={atr_val:.4f} | HTF={'BULL' if htf_bull else 'BEAR'} | "
                f"SQZ={'ON' if row['is_squeezing'] else 'OFF'} | "
                f"VOL={row['volume']/row['vol_ma']:.2f}x | "
                f"OSC={row['oscillator']:.2f}"
            )

            # --- Check active position ---
            if not dry_run:
                position = get_position(exchange, SYMBOL)
            else:
                position = None

            # Trailing stop management
            if position is not None:
                side      = position.get("side")
                mkt_price = exchange.fetch_ticker(SYMBOL)["last"]

                if USE_TRAILING:
                    if side == "long":
                        if trail_high is None or mkt_price > trail_high:
                            trail_high = mkt_price
                        trail_stop = trail_high - atr_val * TRAIL_MULT
                        if mkt_price < trail_stop:
                            log.info(f"[TRAIL] Long trail stop hit @ {mkt_price:.4f}")
                            close_position(exchange, SYMBOL, position)
                            open_side   = None
                            trail_high  = None
                    elif side == "short":
                        if trail_low is None or mkt_price < trail_low:
                            trail_low = mkt_price
                        trail_stop = trail_low + atr_val * TRAIL_MULT
                        if mkt_price > trail_stop:
                            log.info(f"[TRAIL] Short trail stop hit @ {mkt_price:.4f}")
                            close_position(exchange, SYMBOL, position)
                            open_side  = None
                            trail_low  = None

                # Conflict: long open but short signal (or vice versa)
                if position is not None:
                    side = position.get("side")
                    if side == "long"  and row["short_entry"]:
                        log.info("[FLIP] Closing long for short signal")
                        close_position(exchange, SYMBOL, position)
                        position   = None
                        open_side  = None
                        trail_high = None
                    elif side == "short" and row["long_entry"]:
                        log.info("[FLIP] Closing short for long signal")
                        close_position(exchange, SYMBOL, position)
                        position  = None
                        open_side = None
                        trail_low = None

            # --- Entry logic ---
            if position is None:
                balance = get_balance(exchange)
                usdt_qty = balance * (EQUITY_PCT / 100)

                if row["long_entry"]:
                    log.info("*** LONG SIGNAL ***")
                    place_order(exchange, SYMBOL, "buy", usdt_qty, atr_val)
                    open_side  = "long"
                    trail_high = price

                elif row["short_entry"]:
                    log.info("*** SHORT SIGNAL ***")
                    place_order(exchange, SYMBOL, "sell", usdt_qty, atr_val)
                    open_side = "short"
                    trail_low = price

                else:
                    log.info("No signal — waiting.")

        except ccxt.NetworkError as e:
            log.warning(f"Network error: {e} — retrying...")
        except ccxt.ExchangeError as e:
            log.error(f"Exchange error: {e}")
        except Exception as e:
            log.exception(f"Unexpected error: {e}")

        time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()
