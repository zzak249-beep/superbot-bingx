"""
Risk Manager — gestión de riesgo por posición y portafolio
- Tamaño de posición por % del balance
- Stop loss por ATR
- Límite de posiciones simultáneas
- Protección de drawdown diario
- Anti-revenge trading
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional
from loguru import logger


@dataclass
class PositionInfo:
    symbol:        str
    side:          str          # LONG / SHORT
    entry_price:   float
    quantity:      float
    stop_loss:     float
    take_profit:   float
    score:         float
    opened_at:     datetime = field(default_factory=datetime.now)
    pnl_pct:       float = 0.0
    trailing_sl:   Optional[float] = None


class RiskManager:
    def __init__(
        self,
        max_risk_per_trade: float = 1.5,    # % balance por operación
        max_open_positions: int   = 5,
        max_daily_loss_pct: float = 5.0,    # cierre total si pierde X%
        min_score:          float = 55.0,   # score mínimo para operar
        leverage:           int   = 5,
        trailing_stop_pct:  float = 1.5,    # trailing stop ATR-based
        max_pos_per_side:   int   = 3,
    ):
        self.max_risk_per_trade = max_risk_per_trade
        self.max_open_positions = max_open_positions
        self.max_daily_loss_pct = max_daily_loss_pct
        self.min_score          = min_score
        self.leverage           = leverage
        self.trailing_stop_pct  = trailing_stop_pct
        self.max_pos_per_side   = max_pos_per_side

        self.open_positions: dict[str, PositionInfo] = {}
        self.daily_pnl_pct:  float = 0.0
        self._daily_date:    date  = date.today()
        self._start_balance: float = 0.0
        self.total_trades:   int   = 0
        self.winning_trades: int   = 0

    def _reset_daily_if_needed(self):
        today = date.today()
        if today != self._daily_date:
            logger.info(f"📅 Nuevo día — PnL diario reset. Ayer: {self.daily_pnl_pct:.2f}%")
            self.daily_pnl_pct = 0.0
            self._daily_date   = today

    def can_open_trade(self, symbol: str, side: str, score: float) -> tuple[bool, str]:
        """Verifica si se puede abrir la posición"""
        self._reset_daily_if_needed()

        if symbol in self.open_positions:
            return False, f"{symbol} ya tiene posición abierta"

        if len(self.open_positions) >= self.max_open_positions:
            return False, f"Límite de {self.max_open_positions} posiciones simultáneas"

        same_side = sum(1 for p in self.open_positions.values() if p.side == side)
        if same_side >= self.max_pos_per_side:
            return False, f"Límite de {self.max_pos_per_side} posiciones por lado ({side})"

        if score < self.min_score:
            return False, f"Score {score} < mínimo {self.min_score}"

        if self.daily_pnl_pct <= -self.max_daily_loss_pct:
            return False, f"Límite diario de pérdida alcanzado ({self.daily_pnl_pct:.2f}%)"

        return True, "OK"

    def calc_position_size(
        self,
        balance_usdt: float,
        entry_price:  float,
        stop_loss:    float,
        symbol:       str,
    ) -> float:
        """
        Calcula el tamaño de posición basado en el riesgo máximo.
        risk_usdt = balance * max_risk_per_trade%
        qty = risk_usdt / |entry - stop_loss|
        """
        if balance_usdt <= 0 or entry_price <= 0:
            return 0.0

        risk_usdt  = balance_usdt * (self.max_risk_per_trade / 100)
        sl_dist    = abs(entry_price - stop_loss)

        if sl_dist <= 0:
            logger.warning(f"SL distance = 0 para {symbol}")
            return 0.0

        qty = risk_usdt / sl_dist
        # Límite: no más del 20% del balance en una posición
        max_notional = balance_usdt * 0.20 * self.leverage
        max_qty      = max_notional / entry_price
        qty = min(qty, max_qty)

        # Redondear a 3 decimales
        return round(qty, 3)

    def register_position(self, pos: PositionInfo):
        self.open_positions[pos.symbol] = pos
        self.total_trades += 1
        logger.info(
            f"📋 Posición registrada: {pos.symbol} {pos.side} "
            f"qty={pos.quantity} entry={pos.entry_price:.6f} "
            f"SL={pos.stop_loss:.6f} TP={pos.take_profit:.6f}"
        )

    def close_position(self, symbol: str, exit_price: float) -> Optional[PositionInfo]:
        pos = self.open_positions.pop(symbol, None)
        if not pos:
            return None

        if pos.side == "LONG":
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
        else:
            pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100

        pnl_pct_lev = pnl_pct * self.leverage
        self.daily_pnl_pct += pnl_pct_lev
        if pnl_pct_lev > 0:
            self.winning_trades += 1

        logger.info(
            f"{'✅' if pnl_pct_lev > 0 else '❌'} Cerrada {symbol} {pos.side} | "
            f"PnL: {pnl_pct_lev:+.2f}% | Daily: {self.daily_pnl_pct:+.2f}%"
        )
        return pos

    def update_trailing_stop(self, symbol: str, current_price: float, atr: float) -> Optional[float]:
        """Actualiza trailing stop. Devuelve nuevo SL si fue actualizado."""
        pos = self.open_positions.get(symbol)
        if not pos:
            return None

        new_sl = None
        trail_dist = atr * 1.5

        if pos.side == "LONG":
            candidate = current_price - trail_dist
            if pos.trailing_sl is None or candidate > pos.trailing_sl:
                # Solo mover hacia arriba
                if candidate > pos.stop_loss:
                    pos.trailing_sl = candidate
                    new_sl = candidate
        else:
            candidate = current_price + trail_dist
            if pos.trailing_sl is None or candidate < pos.trailing_sl:
                if candidate < pos.stop_loss:
                    pos.trailing_sl = candidate
                    new_sl = candidate

        return new_sl

    def should_close_position(self, symbol: str, current_price: float) -> tuple[bool, str]:
        """Verifica si una posición activa debe cerrarse"""
        pos = self.open_positions.get(symbol)
        if not pos:
            return False, ""

        sl = pos.trailing_sl or pos.stop_loss
        tp = pos.take_profit

        if pos.side == "LONG":
            if current_price <= sl:
                return True, "STOP_LOSS"
            if current_price >= tp:
                return True, "TAKE_PROFIT"
        else:
            if current_price >= sl:
                return True, "STOP_LOSS"
            if current_price <= tp:
                return True, "TAKE_PROFIT"

        return False, ""

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades * 100

    def summary(self) -> str:
        return (
            f"Posiciones: {len(self.open_positions)}/{self.max_open_positions} | "
            f"PnL hoy: {self.daily_pnl_pct:+.2f}% | "
            f"Win rate: {self.win_rate:.1f}% ({self.winning_trades}/{self.total_trades})"
        )
