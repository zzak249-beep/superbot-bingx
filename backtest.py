"""
backtest/backtest.py — Backtester vectorizado para la estrategia.
Simula la misma lógica del motor de señales sobre datos históricos.
Calcula: Win Rate, R:R, Expectancy, Sharpe, Max Drawdown.

Uso:
    python backtest/backtest.py --symbol BTC-USDT --interval 5m --days 90
"""

import asyncio
import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from dotenv import load_dotenv
load_dotenv()

from exchange.bingx_rest import bingx
from utils.indicators import (
    ema, atr, swing_high, swing_low, vol_sma,
    cvd_trend, regime as calc_regime
)
from config import config


async def fetch_all(symbol: str, interval: str, limit: int = 1000):
    klines = await bingx.get_klines(symbol, interval, limit=limit)
    if not klines:
        return [], [], [], [], []
    o = [float(k[1]) for k in klines]
    h = [float(k[2]) for k in klines]
    l = [float(k[3]) for k in klines]
    c = [float(k[4]) for k in klines]
    v = [float(k[5]) for k in klines]
    return o, h, l, c, v


def run_backtest(
    symbol: str,
    htf_c, htf_h, htf_l, htf_o, htf_v,
    mtf_c, mtf_h, mtf_l, mtf_o, mtf_v,
    ltf_c, ltf_h, ltf_l, ltf_o, ltf_v,
    atr_sl: float = 1.5,
    atr_tp: float = 2.5,
    swing_lb: int = 7,
    ema_slow: int = 50,
    ema_fast: int = 20,
    vol_len: int = 20,
    atr_len: int = 14,
) -> dict:
    """
    Backtest vectorizado. Itera sobre velas LTF evaluando señales.
    """
    htf_c_arr = np.array(htf_c)
    htf_h_arr = np.array(htf_h)
    htf_l_arr = np.array(htf_l)
    htf_o_arr = np.array(htf_o)
    htf_v_arr = np.array(htf_v)

    mtf_c_arr = np.array(mtf_c)
    mtf_h_arr = np.array(mtf_h)
    mtf_l_arr = np.array(mtf_l)
    mtf_o_arr = np.array(mtf_o)
    mtf_v_arr = np.array(mtf_v)

    ltf_c_arr = np.array(ltf_c)
    ltf_h_arr = np.array(ltf_h)
    ltf_l_arr = np.array(ltf_l)
    ltf_o_arr = np.array(ltf_o)
    ltf_v_arr = np.array(ltf_v)

    # Pre-computar EMAs HTF
    htf_ema_slow_arr = ema(htf_c_arr, ema_slow)
    htf_ema_fast_arr = ema(htf_c_arr, ema_fast)

    # ATR LTF
    from utils.indicators import atr as calc_atr
    ltf_atr_arr = calc_atr(ltf_h_arr, ltf_l_arr, ltf_c_arr, atr_len)

    trades = []
    start_i = max(ema_slow + 5, 60)
    in_trade = False
    trade_dir = None
    trade_entry = 0.0
    trade_sl = 0.0
    trade_tp = 0.0

    for i in range(start_i, len(ltf_c_arr)):
        if in_trade:
            price = ltf_c_arr[i]
            hit_tp = (trade_dir == "LONG" and price >= trade_tp) or \
                     (trade_dir == "SHORT" and price <= trade_tp)
            hit_sl = (trade_dir == "LONG" and price <= trade_sl) or \
                     (trade_dir == "SHORT" and price >= trade_sl)

            if hit_tp or hit_sl:
                if hit_tp:
                    pnl_r = atr_tp / atr_sl
                else:
                    pnl_r = -1.0
                trades.append({"dir": trade_dir, "pnl_r": pnl_r, "win": hit_tp})
                in_trade = False
            continue

        # ── Evaluar señal ──────────────────────────────────────────────────
        # HTF bias
        htf_ema_s = htf_ema_slow_arr[min(i // 12, len(htf_ema_slow_arr) - 1)]
        htf_ema_f = htf_ema_fast_arr[min(i // 12, len(htf_ema_fast_arr) - 1)]
        htf_price = htf_c_arr[min(i // 12, len(htf_c_arr) - 1)]

        if np.isnan(htf_ema_s) or np.isnan(htf_ema_f):
            continue

        if htf_price > htf_ema_s and htf_ema_f > htf_ema_s:
            htf_bias = "bull"
        elif htf_price < htf_ema_s and htf_ema_f < htf_ema_s:
            htf_bias = "bear"
        else:
            continue

        # MTF swing
        mtf_i = min(i // 3, len(mtf_c_arr) - 1)
        if mtf_i < swing_lb + 2:
            continue
        mtf_h_win = mtf_h_arr[max(0, mtf_i - swing_lb - 1):mtf_i]
        mtf_l_win = mtf_l_arr[max(0, mtf_i - swing_lb - 1):mtf_i]
        if len(mtf_h_win) == 0:
            continue
        sh = float(mtf_h_win.max())
        sl_level = float(mtf_l_win.min())
        mtf_close = mtf_c_arr[mtf_i]
        mtf_open  = mtf_o_arr[mtf_i]

        if mtf_close > sh and mtf_close > mtf_open:
            mtf_break = "up"
        elif mtf_close < sl_level and mtf_close < mtf_open:
            mtf_break = "down"
        else:
            continue

        if htf_bias == "bull" and mtf_break != "up":
            continue
        if htf_bias == "bear" and mtf_break != "down":
            continue

        # Volumen
        vol_win = mtf_v_arr[max(0, mtf_i - vol_len):mtf_i + 1]
        if len(vol_win) < 5:
            continue
        vol_ok = float(vol_win[-1]) > float(vol_win[:-1].mean()) * 1.1

        # ATR
        atr_val = float(ltf_atr_arr[i]) if not np.isnan(ltf_atr_arr[i]) else 0
        if atr_val == 0:
            continue

        entry = float(ltf_c_arr[i])
        direction = "LONG" if htf_bias == "bull" else "SHORT"

        if direction == "LONG":
            sl_price = entry - atr_val * atr_sl
            tp_price = entry + atr_val * atr_tp
        else:
            sl_price = entry + atr_val * atr_sl
            tp_price = entry - atr_val * atr_tp

        in_trade = True
        trade_dir = direction
        trade_entry = entry
        trade_sl = sl_price
        trade_tp = tp_price

    if not trades:
        return {"trades": 0, "win_rate": 0, "expectancy_r": 0,
                "sharpe": 0, "max_dd": 0}

    wins = [t for t in trades if t["win"]]
    win_rate = len(wins) / len(trades)
    pnls = [t["pnl_r"] for t in trades]
    expectancy = float(np.mean(pnls))

    # Equity curve
    equity = np.cumsum([0.0] + pnls)
    rolling_max = np.maximum.accumulate(equity)
    drawdowns = equity - rolling_max
    max_dd = float(drawdowns.min())

    # Sharpe (assume 0 risk-free, daily returns approximation)
    sharpe = float(np.mean(pnls) / np.std(pnls)) * np.sqrt(252) if np.std(pnls) > 0 else 0

    return {
        "symbol": symbol,
        "trades": len(trades),
        "wins": len(wins),
        "win_rate": round(win_rate * 100, 1),
        "expectancy_r": round(expectancy, 3),
        "sharpe": round(sharpe, 2),
        "max_dd_r": round(max_dd, 2),
        "longs": sum(1 for t in trades if t["dir"] == "LONG"),
        "shorts": sum(1 for t in trades if t["dir"] == "SHORT"),
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC-USDT")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    symbol = args.symbol
    print(f"\n📊 BACKTEST: {symbol}")
    print("─" * 50)

    print("Descargando datos...")
    htf_o, htf_h, htf_l, htf_c, htf_v = await fetch_all(symbol, config.HTF, args.limit)
    mtf_o, mtf_h, mtf_l, mtf_c, mtf_v = await fetch_all(symbol, config.MTF, args.limit)
    ltf_o, ltf_h, ltf_l, ltf_c, ltf_v = await fetch_all(symbol, config.LTF, args.limit)

    if not ltf_c:
        print("❌ Sin datos")
        return

    print(f"HTF: {len(htf_c)} velas | MTF: {len(mtf_c)} | LTF: {len(ltf_c)}")

    result = run_backtest(
        symbol,
        htf_c, htf_h, htf_l, htf_o, htf_v,
        mtf_c, mtf_h, mtf_l, mtf_o, mtf_v,
        ltf_c, ltf_h, ltf_l, ltf_o, ltf_v,
    )

    print(f"\n{'='*50}")
    print(f"  RESULTADO BACKTEST — {symbol}")
    print(f"{'='*50}")
    print(f"  Trades totales : {result['trades']}")
    print(f"  Ganados        : {result.get('wins', 0)}")
    print(f"  Win Rate       : {result['win_rate']}%")
    print(f"  Expectancy     : {result['expectancy_r']:+.3f}R por trade")
    print(f"  Sharpe         : {result['sharpe']}")
    print(f"  Max Drawdown   : {result['max_dd_r']}R")
    print(f"  Longs / Shorts : {result.get('longs',0)} / {result.get('shorts',0)}")
    print(f"{'='*50}")

    ev = result['expectancy_r']
    if ev > 0:
        print(f"\n  ✅ Ventaja matemática POSITIVA: +{ev:.3f}R por trade")
        print(f"     Con {result['trades']} trades, EV total: {ev * result['trades']:.2f}R")
    else:
        print(f"\n  ⚠️  Sin ventaja en este período.")

    await bingx.close()


if __name__ == "__main__":
    asyncio.run(main())
