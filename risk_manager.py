"""
risk_manager.py — Position sizing & risk controls for SuperBot v4

RiskManager:
  - size_position()     → TradeParams with quantity, entry, sl, tp1/2/3, notional, fee
  - can_open_trade()    → bool
  - reset_daily()       → resets daily P&L tracking
  - record_pnl()        → track daily P&L and fees
  - get_stats()         → dict summary

TradeParams: dataclass with all order params
"""

import os
import math
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("RiskManager")

MIN_NOTIONAL = float(os.environ.get("MIN_NOTIONAL_USDT", 5.0))   # BingX minimum order
MAX_NOTIONAL = float(os.environ.get("MAX_NOTIONAL_USDT", 500.0)) # cap per trade


@dataclass
class TradeParams:
    symbol:      str
    direction:   str
    entry_price: float
    sl_price:    float
    tp1_price:   float
    tp2_price:   float
    tp3_price:   float
    quantity:    float
    leverage:    int
    notional:    float   # quantity × entry (pre-leverage)
    est_fee:     float   # estimated maker+taker fee


class RiskManager:
    def __init__(
        self,
        risk_pct:         float = 0.02,
        max_pos:          int   = 4,
        leverage:         int   = 10,
        daily_loss_limit: float = 0.06,
    ):
        self.risk_pct         = risk_pct          # fraction of balance per trade
        self.max_pos          = max_pos
        self.leverage         = leverage
        self.daily_loss_limit = daily_loss_limit

        self._daily_start_balance: float = 0.0
        self._daily_pnl:           float = 0.0
        self._total_fees:          float = 0.0
        self._trades_today:        int   = 0

    # ── Daily reset ───────────────────────────────────────────────────────────

    def reset_daily(self, balance: float):
        self._daily_start_balance = balance
        self._daily_pnl           = 0.0
        self._trades_today        = 0
        log.info(f"📅 Daily reset | Balance=${balance:.2f} | Limit={self.daily_loss_limit*100:.1f}%")

    # ── Guards ────────────────────────────────────────────────────────────────

    def can_open_trade(self, open_count: int, balance: float) -> bool:
        if open_count >= self.max_pos:
            log.info(f"⛔ Max positions reached ({open_count}/{self.max_pos})")
            return False
        if balance <= 0:
            log.warning("⛔ Balance $0 — cannot open trades")
            return False
        # Daily loss circuit breaker
        if self._daily_start_balance > 0:
            loss_pct = -self._daily_pnl / self._daily_start_balance
            if loss_pct >= self.daily_loss_limit:
                log.warning(
                    f"⛔ Daily loss limit hit: {loss_pct*100:.1f}% "
                    f">= {self.daily_loss_limit*100:.1f}%"
                )
                return False
        return True

    # ── Sizing ────────────────────────────────────────────────────────────────

    def size_position(
        self,
        symbol:    str,
        direction: str,
        entry:     float,
        sl:        float,
        tp1:       float,
        tp2:       float,
        tp3:       float,
        balance:   float,
        qty_precision:   int = 3,
        price_precision: int = 4,
    ) -> Optional[TradeParams]:
        """
        Kelly-inspired fixed-fractional sizing:
          risk_amount = balance × risk_pct
          sl_distance = |entry - sl|
          quantity    = (risk_amount × leverage) / sl_distance
          notional    = quantity × entry
        """
        if entry <= 0 or sl <= 0:
            log.warning(f"size_position: invalid entry={entry} sl={sl}")
            return None

        sl_dist = abs(entry - sl)
        if sl_dist < 1e-10:
            log.warning(f"size_position: SL distance too small for {symbol}")
            return None

        risk_amount = balance * self.risk_pct
        # raw quantity = risk_amount × leverage / sl_distance
        qty_raw     = (risk_amount * self.leverage) / sl_dist
        notional    = qty_raw * entry

        # Clamp notional
        if notional < MIN_NOTIONAL:
            log.info(f"size_position: notional ${notional:.2f} < min ${MIN_NOTIONAL} for {symbol}")
            return None
        if notional > MAX_NOTIONAL:
            qty_raw  = MAX_NOTIONAL / entry
            notional = MAX_NOTIONAL

        qty = round(qty_raw, qty_precision)
        if qty <= 0:
            return None

        # Re-compute notional with rounded qty
        notional = round(qty * entry, 2)

        # Round prices
        ep  = round(entry, price_precision)
        slp = round(sl,    price_precision)
        t1  = round(tp1,   price_precision)
        t2  = round(tp2,   price_precision)
        t3  = round(tp3,   price_precision)

        # Estimated fee: 0.05% maker entry + 0.05% taker exit (typical BingX)
        est_fee = round(notional * 0.001, 4)

        log.info(
            f"📐 Size {symbol} {direction} | qty={qty} entry={ep} "
            f"SL={slp} TP1={t1} | notional=${notional:.2f} lev={self.leverage}x "
            f"risk=${risk_amount:.2f} fee~${est_fee:.3f}"
        )

        return TradeParams(
            symbol      = symbol,
            direction   = direction,
            entry_price = ep,
            sl_price    = slp,
            tp1_price   = t1,
            tp2_price   = t2,
            tp3_price   = t3,
            quantity    = qty,
            leverage    = self.leverage,
            notional    = notional,
            est_fee     = est_fee,
        )

    # ── P&L tracking ──────────────────────────────────────────────────────────

    def record_pnl(self, pnl: float, fee: float = 0.0):
        self._daily_pnl   += pnl
        self._total_fees  += fee
        self._trades_today += 1
        log.info(
            f"💹 PnL recorded: {pnl:+.4f} | fee={fee:.4f} | "
            f"daily={self._daily_pnl:+.4f} | trades={self._trades_today}"
        )

    def get_stats(self) -> dict:
        return {
            "daily_pnl":      round(self._daily_pnl, 4),
            "total_fees":     round(self._total_fees, 4),
            "trades_today":   self._trades_today,
            "start_balance":  self._daily_start_balance,
        }
