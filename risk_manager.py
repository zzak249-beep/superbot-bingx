"""
Risk Manager — Conflux 4 v2
El módulo más importante para ser rentable.

Reglas implementadas:
  1. Tamaño de posición dinámico (fracción de Kelly simplificada)
  2. Pérdida diaria máxima → circuit breaker (para el bot ese día)
  3. Pérdida semanal máxima → modo conservador
  4. Drawdown máximo desde equity peak → detiene el bot
  5. Bloqueo de correlación (no abrir 2 posiciones muy correlacionadas)
  6. Filtro de sesión (evitar horas de baja liquidez)
  7. Calidad mínima de señal requerida
  8. Máximo de trades simultáneos
"""

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Dict, Optional
from loguru import logger


# ── Correlaciones conocidas (BTC mueve el mercado crypto) ─────────────────
CORRELATED_PAIRS = [
    {"BTC-USDT", "ETH-USDT"},
    {"BTC-USDT", "SOL-USDT"},
    {"BTC-USDT", "BNB-USDT"},
    {"ETH-USDT", "SOL-USDT"},
]


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    position_usdt: float        # USDT a usar en esta operación
    risk_pct: float             # % del capital en riesgo
    Kelly_fraction: float       # fracción Kelly calculada


@dataclass
class DayStats:
    date: str = ""
    pnl_usdt: float = 0.0
    trades: int = 0
    wins: int = 0
    losses: int = 0

    @property
    def winrate(self) -> float:
        t = self.wins + self.losses
        return self.wins / t if t > 0 else 0.5


@dataclass
class EquityState:
    starting_balance: float = 1000.0
    current_balance: float = 1000.0
    peak_balance: float = 1000.0
    total_pnl: float = 0.0
    today: DayStats = field(default_factory=DayStats)
    week_pnl: float = 0.0
    all_time_trades: int = 0
    all_time_wins: int = 0
    open_positions: Dict[str, str] = field(default_factory=dict)  # symbol → direction

    @property
    def drawdown_pct(self) -> float:
        if self.peak_balance == 0:
            return 0.0
        return (self.peak_balance - self.current_balance) / self.peak_balance * 100


class RiskManager:
    def __init__(self, cfg: dict, data_path: str = "data/equity.json"):
        self.cfg = cfg
        self.data_path = Path(data_path)
        self.state = self._load_state()
        self._ensure_today()

    # ── Persistencia ───────────────────────────────────────────────────────
    def _load_state(self) -> EquityState:
        if self.data_path.exists():
            try:
                with open(self.data_path) as f:
                    d = json.load(f)
                state = EquityState(**{k: v for k, v in d.items() if k != "today"})
                if "today" in d:
                    state.today = DayStats(**d["today"])
                return state
            except Exception as e:
                logger.warning(f"No se pudo cargar equity state: {e}")
        bal = self.cfg.get("starting_balance", 1000.0)
        return EquityState(
            starting_balance=bal,
            current_balance=bal,
            peak_balance=bal,
        )

    def save(self):
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        d = asdict(self.state)
        with open(self.data_path, "w") as f:
            json.dump(d, f, indent=2)

    def _ensure_today(self):
        today_str = date.today().isoformat()
        if self.state.today.date != today_str:
            # Nuevo día: reset stats diarias
            self.state.today = DayStats(date=today_str)
            self.save()

    # ── Cálculo de Kelly simplificado ─────────────────────────────────────
    def _kelly_fraction(self, quality: int) -> float:
        """
        Kelly fraccionado basado en historial y calidad de señal.
        Usamos Half-Kelly (más conservador) para reducir varianza.
        """
        wr = self.state.today.winrate if self.state.all_time_trades > 20 else 0.5
        avg_rr = self.cfg.get("rr2", 1.0)  # RR objetivo (TP2 como referencia)

        # Kelly = (WR * (RR + 1) - 1) / RR
        kelly = (wr * (avg_rr + 1) - 1) / avg_rr
        kelly = max(0.01, min(kelly, 0.25))  # clamp 1%-25%

        # Half-Kelly
        half_kelly = kelly * 0.5

        # Ajustar por calidad de señal (0-10)
        quality_mult = 0.5 + (quality / 10) * 0.5   # 0.5 - 1.0
        adjusted = half_kelly * quality_mult

        max_risk = self.cfg.get("max_risk_per_trade_pct", 2.0) / 100
        return min(adjusted, max_risk)

    # ── Verificación principal ─────────────────────────────────────────────
    def approve(self, symbol: str, direction: str, quality: int, signal_result=None) -> RiskDecision:
        self._ensure_today()
        c = self.cfg
        state = self.state
        bal = state.current_balance

        def reject(reason: str) -> RiskDecision:
            logger.warning(f"Risk REJECT [{symbol}]: {reason}")
            return RiskDecision(approved=False, reason=reason,
                                position_usdt=0, risk_pct=0, Kelly_fraction=0)

        # 1. Calidad mínima
        min_quality = c.get("min_signal_quality", 5)
        if quality < min_quality:
            return reject(f"Calidad señal {quality} < mínimo {min_quality}")

        # 2. Circuit breaker diario
        max_daily_loss = c.get("max_daily_loss_pct", 3.0)
        daily_loss_pct = -state.today.pnl_usdt / bal * 100 if bal > 0 else 0
        if daily_loss_pct >= max_daily_loss:
            return reject(f"Daily loss {daily_loss_pct:.1f}% ≥ límite {max_daily_loss}%")

        # 3. Pérdida semanal
        max_weekly_loss = c.get("max_weekly_loss_pct", 8.0)
        weekly_loss_pct = -state.week_pnl / bal * 100 if bal > 0 else 0
        if weekly_loss_pct >= max_weekly_loss:
            return reject(f"Weekly loss {weekly_loss_pct:.1f}% ≥ límite {max_weekly_loss}%")

        # 4. Drawdown máximo desde equity peak
        max_drawdown = c.get("max_drawdown_pct", 15.0)
        if state.drawdown_pct >= max_drawdown:
            return reject(f"Drawdown {state.drawdown_pct:.1f}% ≥ límite {max_drawdown}%")

        # 5. Máximo de trades simultáneos
        max_open = c.get("max_open_trades", 3)
        if len(state.open_positions) >= max_open:
            return reject(f"Máx trades simultáneos ({max_open}) alcanzado")

        # 6. Bloqueo de correlación — no abrir mismo lado en pares correlacionados
        for corr_set in CORRELATED_PAIRS:
            if symbol in corr_set:
                for other_sym, other_dir in state.open_positions.items():
                    if other_sym in corr_set and other_dir == direction:
                        return reject(f"Correlación: {other_sym} ya abierto en {direction}")

        # 7. Filtro de sesión (horario UTC)
        session_ok = self._session_filter()
        if not session_ok:
            return reject("Fuera de sesión de trading (baja liquidez)")

        # 8. No entrar si ya hay posición abierta en ese símbolo
        if symbol in state.open_positions:
            return reject(f"Ya hay posición abierta en {symbol}")

        # ── Calcular tamaño ────────────────────────────────────────────────
        kelly_frac = self._kelly_fraction(quality)
        risk_usdt = bal * kelly_frac
        leverage = c.get("leverage", 5)
        position_usdt = risk_usdt * leverage  # USDT de posición nocional

        # Clamp al máximo absoluto configurado
        max_pos = c.get("max_position_usdt", 500.0)
        position_usdt = min(position_usdt, max_pos)

        risk_pct = kelly_frac * 100

        logger.info(
            f"Risk APPROVE [{symbol} {direction}] "
            f"Quality={quality} Kelly={kelly_frac*100:.2f}% "
            f"Pos={position_usdt:.1f} USDT Risk={risk_pct:.2f}%"
        )

        return RiskDecision(
            approved=True,
            reason="OK",
            position_usdt=position_usdt,
            risk_pct=risk_pct,
            Kelly_fraction=kelly_frac,
        )

    # ── Filtro de sesión ───────────────────────────────────────────────────
    def _session_filter(self) -> bool:
        c = self.cfg
        if not c.get("use_session_filter", True):
            return True
        hour = datetime.now(timezone.utc).hour
        # Por defecto: evitar 00:00-04:00 UTC (baja liquidez crypto)
        avoid_hours = c.get("avoid_hours_utc", [0, 1, 2, 3])
        return hour not in avoid_hours

    # ── Registrar apertura de posición ────────────────────────────────────
    def register_open(self, symbol: str, direction: str):
        self.state.open_positions[symbol] = direction
        self.state.today.trades += 1
        self.state.all_time_trades += 1
        self.save()
        logger.info(f"Posición abierta: {symbol} {direction} | Open: {len(self.state.open_positions)}")

    # ── Registrar cierre de posición ──────────────────────────────────────
    def register_close(self, symbol: str, pnl_usdt: float, won: bool):
        self.state.open_positions.pop(symbol, None)
        self.state.today.pnl_usdt += pnl_usdt
        self.state.week_pnl += pnl_usdt
        self.state.total_pnl += pnl_usdt
        self.state.current_balance += pnl_usdt

        if won:
            self.state.today.wins += 1
            self.state.all_time_wins += 1
        else:
            self.state.today.losses += 1

        if self.state.current_balance > self.state.peak_balance:
            self.state.peak_balance = self.state.current_balance

        self.save()
        emoji = "✅" if won else "❌"
        logger.info(
            f"{emoji} Posición cerrada: {symbol} PnL={pnl_usdt:+.2f} USDT "
            f"Balance={self.state.current_balance:.2f} USDT"
        )

    # ── Resumen para dashboard ─────────────────────────────────────────────
    def summary(self) -> dict:
        s = self.state
        wr = s.all_time_wins / s.all_time_trades if s.all_time_trades > 0 else 0
        return {
            "balance": s.current_balance,
            "peak": s.peak_balance,
            "drawdown_pct": s.drawdown_pct,
            "total_pnl": s.total_pnl,
            "today_pnl": s.today.pnl_usdt,
            "week_pnl": s.week_pnl,
            "today_trades": s.today.trades,
            "all_trades": s.all_time_trades,
            "winrate": wr,
            "open_positions": len(s.open_positions),
        }
