"""
Risk Manager
- Position sizing: fixed % of balance per trade
- Max open positions cap
- Daily loss limit (kill switch)
- Leverage optimizer (lower = less liquidation risk)
- Commission saving: always use LIMIT orders (maker fee ~0.02%)
"""
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────
RISK_PER_TRADE   = 0.01    # 1% balance per trade
MAX_POSITIONS    = 5       # concurrent open trades
LEVERAGE         = 5       # conservative; lower = safer
DAILY_LOSS_LIMIT = 0.05    # 5% daily drawdown → pause bot
MIN_NOTIONAL     = 5.0     # BingX min order $5 USDT
PARTIAL_TP_PCT   = 0.5     # close 50% at TP1

@dataclass
class TradeParams:
    symbol:         str
    direction:      str    # LONG / SHORT
    entry_price:    float
    sl_price:       float
    tp1_price:      float
    tp2_price:      float
    tp3_price:      float
    quantity:       float
    leverage:       int
    notional:       float
    risk_usdt:      float


class RiskManager:
    def __init__(self):
        self._daily_start_balance: Optional[float] = None
        self._daily_loss: float = 0.0
        self._trade_count: int  = 0

    def reset_daily(self, balance: float):
        self._daily_start_balance = balance
        self._daily_loss = 0.0
        self._trade_count = 0
        log.info(f"Daily reset. Balance: {balance:.2f} USDT")

    def record_pnl(self, pnl_usdt: float):
        if pnl_usdt < 0:
            self._daily_loss += abs(pnl_usdt)
        self._trade_count += 1

    def is_kill_switch(self, balance: float) -> bool:
        if self._daily_start_balance is None:
            return False
        daily_loss_pct = self._daily_loss / max(self._daily_start_balance, 1)
        if daily_loss_pct >= DAILY_LOSS_LIMIT:
            log.warning(f"⛔ KILL SWITCH: Daily loss {daily_loss_pct*100:.1f}% ≥ {DAILY_LOSS_LIMIT*100:.0f}%")
            return True
        return False

    def can_open_trade(self, open_positions: int, balance: float) -> bool:
        if open_positions >= MAX_POSITIONS:
            log.info(f"Max positions ({MAX_POSITIONS}) reached.")
            return False
        if self.is_kill_switch(balance):
            return False
        return True

    def size_position(
        self,
        symbol: str,
        direction: str,
        entry: float,
        sl: float,
        tp1: float,
        tp2: float,
        tp3: float,
        balance: float,
        qty_precision: int = 3,
        price_precision: int = 4,
    ) -> Optional[TradeParams]:
        """
        Size using fixed-risk: risk_usdt = balance * RISK_PER_TRADE
        qty = risk_usdt / (|entry - sl| * leverage)
        """
        risk_usdt  = balance * RISK_PER_TRADE
        sl_dist    = abs(entry - sl)
        if sl_dist == 0:
            log.warning("SL = entry price, skipping")
            return None

        # Qty in coins (with leverage)
        qty_raw  = risk_usdt / sl_dist
        qty      = round(qty_raw, qty_precision)

        notional = qty * entry
        if notional < MIN_NOTIONAL:
            # scale up to minimum
            qty = round(MIN_NOTIONAL / entry, qty_precision)
            notional = qty * entry

        ep  = round(entry, price_precision)
        slp = round(sl,    price_precision)
        t1  = round(tp1,   price_precision)
        t2  = round(tp2,   price_precision)
        t3  = round(tp3,   price_precision)

        log.info(
            f"Size [{symbol}] {direction} qty={qty} entry={ep} "
            f"SL={slp} TP1={t1} notional={notional:.2f} risk={risk_usdt:.2f}"
        )
        return TradeParams(
            symbol=symbol, direction=direction,
            entry_price=ep, sl_price=slp,
            tp1_price=t1, tp2_price=t2, tp3_price=t3,
            quantity=qty, leverage=LEVERAGE,
            notional=notional, risk_usdt=risk_usdt,
        )

    def partial_close_qty(self, qty: float, qty_precision: int = 3) -> float:
        """Qty to close at TP1 (50%)."""
        return round(qty * PARTIAL_TP_PCT, qty_precision)
