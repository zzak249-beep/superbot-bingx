"""
Risk Manager v3.0
Sin cambios de lógica respecto a v2 — solo limpieza de código.
"""
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

RISK_PER_TRADE   = 0.01
MAX_POSITIONS    = 5
LEVERAGE         = 5
DAILY_LOSS_LIMIT = 0.05
MIN_NOTIONAL     = 5.0
PARTIAL_TP_PCT   = 0.5


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
    notional:    float
    risk_usdt:   float


class RiskManager:
    def __init__(self):
        self._daily_start_balance: Optional[float] = None
        self._daily_loss:  float = 0.0
        self._trade_count: int   = 0

    def reset_daily(self, balance: float):
        self._daily_start_balance = balance
        self._daily_loss  = 0.0
        self._trade_count = 0
        log.info(f"Daily reset. Balance: {balance:.2f} USDT")

    def record_pnl(self, pnl_usdt: float):
        if pnl_usdt < 0:
            self._daily_loss += abs(pnl_usdt)
        self._trade_count += 1

    def is_kill_switch(self, balance: float) -> bool:
        if self._daily_start_balance is None:
            return False
        pct = self._daily_loss / max(self._daily_start_balance, 1)
        if pct >= DAILY_LOSS_LIMIT:
            log.warning(f"⛔ KILL SWITCH: {pct*100:.1f}% ≥ {DAILY_LOSS_LIMIT*100:.0f}%")
            return True
        return False

    def can_open_trade(self, open_positions: int, balance: float) -> bool:
        if open_positions >= MAX_POSITIONS:
            log.info(f"Max posiciones ({MAX_POSITIONS}) alcanzado.")
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
        risk_usdt = balance * RISK_PER_TRADE
        sl_dist   = abs(entry - sl)
        if sl_dist == 0:
            log.warning("SL = entry price, skip")
            return None

        qty_raw  = risk_usdt / sl_dist
        qty      = round(qty_raw, qty_precision)
        notional = qty * entry

        if notional < MIN_NOTIONAL:
            qty      = round(MIN_NOTIONAL / entry, qty_precision)
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
        return round(qty * PARTIAL_TP_PCT, qty_precision)
