"""
Risk Manager — Cascade Bot (standalone, sin dependencias complejas)
FIX: añadidos métodos async que position_manager.py v7.8 requiere:
  - update_open_count(count)       → reconcilia open_count con BingX real
  - on_trade_opened(symbol, dir)   → callback al abrir trade
  - on_trade_closed(pnl, symbol)   → ahora async (era sync)
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field

import config as C

log = logging.getLogger("risk")

@dataclass
class RiskManager:
    _open_count:    int   = 0
    _daily_count:   int   = 0
    _daily_pnl:     float = 0.0
    _day_start:     float = field(default_factory=time.time)
    _reserved:      int   = 0
    _dir_counts:    dict  = field(default_factory=dict)
    _dir_tokens:    dict  = field(default_factory=dict)
    _token_counter: int   = 0

    def __post_init__(self):
        self._dir_counts = {"LONG": 0, "SHORT": 0}
        self._dir_tokens = {}

    def _reset_day_if_needed(self):
        if time.time() - self._day_start > 86400:
            self._daily_count = 0
            self._daily_pnl   = 0.0
            self._day_start   = time.time()

    async def can_trade(self, unrealized_pnl: float = 0.0) -> tuple:
        self._reset_day_if_needed()
        max_open       = getattr(C, 'MAX_OPEN_TRADES', 3)
        max_daily      = getattr(C, 'MAX_DAILY_TRADES', 10)
        daily_loss_pct = getattr(C, 'DAILY_LOSS_PCT', 5.0)
        capital        = getattr(C, 'CAPITAL', 400.0)

        if self._open_count + self._reserved >= max_open:
            return False, f"max_open_trades({self._open_count + self._reserved}/{max_open})"
        if self._daily_count >= max_daily:
            return False, f"max_daily_trades({self._daily_count}/{max_daily})"
        total_pnl = self._daily_pnl + unrealized_pnl
        max_loss  = capital * daily_loss_pct / 100
        if total_pnl <= -max_loss:
            return False, f"daily_loss_limit({total_pnl:.2f}/{-max_loss:.2f})"

        self._reserved   += 1
        self._open_count += 1
        self._daily_count += 1
        log.info("Trade confirmado — open=%d reserved=%d daily=%d",
                 self._open_count, self._reserved, self._daily_count)
        return True, "ok"

    async def release_reservation(self):
        if self._reserved > 0:
            self._reserved -= 1
        if self._open_count > 0:
            self._open_count -= 1

    # ── FIX: métodos que position_manager v7.8 requiere ──────────────────────

    async def update_open_count(self, count: int):
        """
        Reconcilia el contador interno con el real de BingX.
        Llamado desde _check_all_positions() en cada ciclo del monitor.
        """
        if self._open_count != count:
            log.debug("update_open_count: %d → %d (BingX real)", self._open_count, count)
            self._open_count = count

    async def on_trade_opened(self, symbol: str = "", direction: str = ""):
        """Callback cuando un trade se registra exitosamente."""
        log.info("on_trade_opened: %s %s | open=%d daily=%d",
                 symbol, direction, self._open_count, self._daily_count)

    async def on_trade_closed(self, pnl: float = 0.0, symbol: str = ""):
        """
        Callback cuando un trade se cierra.
        FIX: ahora async (position_manager lo llama con await).
        """
        self._daily_pnl += pnl
        if self._open_count > 0:
            self._open_count -= 1
        log.info("on_trade_closed: %s pnl=%.4f | open=%d daily_pnl=%.4f",
                 symbol, pnl, self._open_count, self._daily_pnl)

    # ─────────────────────────────────────────────────────────────────────────

    def symbol_allowed(self, symbol: str) -> tuple:
        bl = getattr(C, 'BLACKLIST', set())
        for b in bl:
            if b and b in symbol.upper():
                return False, f"blacklist({b})"
        return True, "ok"

    def direction_allowed(self, direction: str) -> tuple:
        self._token_counter += 1
        token = self._token_counter
        self._dir_counts[direction] = self._dir_counts.get(direction, 0) + 1
        self._dir_tokens[token] = direction
        return True, "ok", token

    def release_direction_reservation(self, direction: str, token):
        if token in self._dir_tokens:
            d = self._dir_tokens.pop(token)
            self._dir_counts[d] = max(0, self._dir_counts.get(d, 1) - 1)

    def tier_ok(self, tier: str) -> bool:
        return True

    def kelly_position_size(self, balance: float, entry: float, sl: float,
                             score: float = 60.0, tier: str = "STD",
                             symbol: str = "") -> float:
        risk_pct = getattr(C, 'RISK_PCT', 1.0) / 100
        capital  = getattr(C, 'CAPITAL', balance)
        max_not  = getattr(C, 'MAX_NOTIONAL_USDT', 40.0)
        min_not  = getattr(C, 'MIN_NOTIONAL_USDT', 10.0)

        if entry <= 0 or sl <= 0:
            return 0.0
        sl_dist = abs(entry - sl) / entry
        if sl_dist < 1e-6:
            return 0.0

        risk_usdt = capital * risk_pct
        notional  = min(risk_usdt / sl_dist, max_not)
        notional  = max(notional, min_not)
        qty       = notional / entry

        try:
            from bingx_client import BingXClient
            qty = BingXClient._round_qty_static(symbol, qty)
        except Exception:
            pass

        log.info("[sizing] %s score=%.0f risk=%.4f USDT qty=%.6f notional=%.2f USDT",
                 tier, score, risk_usdt, qty, qty * entry)
        return qty

    def status(self, unrealized_pnl: float = 0.0) -> dict:
        self._reset_day_if_needed()
        return {
            "open_trades":  self._open_count,
            "daily_trades": self._daily_count,
            "daily_pnl":    round(self._daily_pnl + unrealized_pnl, 4),
            "max_open":     getattr(C, 'MAX_OPEN_TRADES', 3),
            "max_daily":    getattr(C, 'MAX_DAILY_TRADES', 10),
        }
