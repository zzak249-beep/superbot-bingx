"""
RiskManager v5 — Gestión de riesgo profesional
Mejoras:
  - Kelly Criterion para sizing dinámico
  - Circuit breaker: para el bot si pérdida diaria > límite
  - Drawdown tracking en tiempo real
  - Ajuste automático de riesgo según winrate reciente
"""
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("Risk")


@dataclass
class TradeParams:
    symbol: str
    direction: str
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    tp3_price: float
    quantity: float
    notional: float
    leverage: int
    est_fee: float


class RiskManager:
    def __init__(
        self,
        risk_pct: float = 0.02,
        max_pos: int = 4,
        leverage: int = 10,
        daily_loss_limit: float = 0.06,
    ):
        self.base_risk_pct = risk_pct
        self.max_pos = max_pos
        self.leverage = leverage
        self.daily_loss_limit = daily_loss_limit
        
        # Estado diario
        self.daily_pnl = 0.0
        self.daily_start_balance = 0.0
        self.total_fees = 0.0
        self.trades_today = 0
        self.wins_today = 0
        self.circuit_open = False  # True = bot pausado
    
    def reset_daily(self, balance: float):
        self.daily_pnl = 0.0
        self.daily_start_balance = balance
        self.total_fees = 0.0
        self.trades_today = 0
        self.wins_today = 0
        self.circuit_open = False
        log.info(f"📅 Reset diario | Balance: ${balance:.2f}")
    
    def _dynamic_risk_pct(self, winrate: float) -> float:
        """
        Kelly fraccionario (25% del Kelly completo para seguridad).
        Kelly = (p*(b+1) - 1) / b  donde p=winrate, b=RR promedio
        """
        if winrate <= 0 or winrate >= 1:
            return self.base_risk_pct
        
        rr = 1.5  # RR promedio asumido (conservador)
        kelly_full = (winrate * (rr + 1) - 1) / rr
        kelly_frac = kelly_full * 0.25  # 25% del Kelly
        
        # Clamp entre 0.5% y 3%
        return max(0.005, min(0.03, kelly_frac))
    
    def can_open_trade(self, open_count: int, balance: float, winrate: float = 0.5) -> bool:
        if self.circuit_open:
            log.warning("⚡ Circuit breaker activo — trading pausado")
            return False
        if open_count >= self.max_pos:
            return False
        if balance <= 0:
            log.error("❌ Balance = $0 — verifica fondos en cuenta Perpetual Futures")
            return False
        
        # Check daily loss limit
        if self.daily_start_balance > 0:
            daily_loss_pct = -self.daily_pnl / self.daily_start_balance
            if daily_loss_pct >= self.daily_loss_limit:
                log.warning(f"🛑 Límite diario alcanzado: {daily_loss_pct:.1%}")
                self.circuit_open = True
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
        winrate: float = 0.5,
    ) -> Optional[TradeParams]:
        if balance <= 0 or entry <= 0 or sl <= 0:
            return None
        
        sl_dist = abs(entry - sl)
        if sl_dist < 1e-10:
            return None
        
        # Riesgo dinámico (Kelly fraccionario)
        risk_pct = self._dynamic_risk_pct(winrate)
        risk_amount = balance * risk_pct
        
        # Cantidad basada en distancia al SL
        # risk_amount = qty * sl_dist (sin apalancamiento en el riesgo real)
        qty_raw = risk_amount / sl_dist
        
        # Redondear a precisión del símbolo
        qty = round(qty_raw, qty_precision)
        
        # Mínimo razonable
        notional = qty * entry
        if notional < 5.0:  # mínimo $5 de notional
            log.debug(f"Notional muy pequeño: ${notional:.2f}")
            return None
        
        # El margen requerido (con apalancamiento)
        margin_required = notional / self.leverage
        if margin_required > balance * 0.4:  # max 40% del balance en un trade
            # Reducir qty
            max_qty = (balance * 0.4 * self.leverage) / entry
            qty = round(max_qty, qty_precision)
            notional = qty * entry
        
        if qty <= 0:
            return None
        
        # Fee estimado (0.05% maker + 0.05% taker = 0.1% round trip)
        est_fee = notional * 0.001
        
        log.info(
            f"💰 Sizing {symbol} {direction}: qty={qty} "
            f"notional=${notional:.2f} margin=${notional/self.leverage:.2f} "
            f"risk=${risk_amount:.2f} ({risk_pct*100:.1f}%) "
            f"sl_dist=${sl_dist:.4f}"
        )
        
        return TradeParams(
            symbol=symbol,
            direction=direction,
            entry_price=round(entry, price_precision),
            sl_price=round(sl, price_precision),
            tp1_price=round(tp1, price_precision),
            tp2_price=round(tp2, price_precision),
            tp3_price=round(tp3, price_precision),
            quantity=qty,
            notional=notional,
            leverage=self.leverage,
            est_fee=est_fee,
        )
    
    def record_pnl(self, pnl: float, fee: float = 0.0):
        self.daily_pnl += pnl
        self.total_fees += fee
        self.trades_today += 1
        if pnl > 0:
            self.wins_today += 1
        
        # Circuit breaker intraday
        if self.daily_start_balance > 0:
            loss_pct = -self.daily_pnl / self.daily_start_balance
            if loss_pct >= self.daily_loss_limit:
                log.warning(f"⚡ CIRCUIT BREAKER: pérdida diaria {loss_pct:.1%} ≥ {self.daily_loss_limit:.1%}")
                self.circuit_open = True
    
    def get_winrate(self) -> float:
        if self.trades_today == 0:
            return 0.5
        return self.wins_today / self.trades_today
    
    def get_stats(self) -> dict:
        return {
            "daily_pnl": self.daily_pnl,
            "total_fees": self.total_fees,
            "trades_today": self.trades_today,
            "wins_today": self.wins_today,
            "winrate": self.get_winrate(),
            "circuit_open": self.circuit_open,
        }
