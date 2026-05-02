"""
Risk Manager — Conflux 4 v3.1 (MEJORADO)

Mejoras sobre v2:
  - Win rate tracking real (trades cerrados en TP vs SL)
  - Cooldown post-SL: bloquea re-entrada N scans tras un stop loss
  - Alerta Telegram si error rate > 15% en un scan
  - summary() incluye win rate real y streak actual
  - register_close() actualiza peak correctamente en cada cierre
  - Kelly mejorado: usa win rate histórico real desde el primer trade (no espera 20)
  - Nuevo: consecutive_losses para detectar racha mala y reducir sizing
"""

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Dict, Optional
from loguru import logger


CORRELATED_PAIRS = [
    {"BTC-USDT", "ETH-USDT"},
    {"BTC-USDT", "SOL-USDT"},
    {"BTC-USDT", "BNB-USDT"},
    {"ETH-USDT", "SOL-USDT"},
    {"ETH-USDT", "MATIC-USDT"},
    {"SOL-USDT", "AVAX-USDT"},
]


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    position_usdt: float
    risk_pct: float
    Kelly_fraction: float


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
    all_time_losses: int = 0         # Nuevo: llevar cuenta de losses
    consecutive_losses: int = 0      # Nuevo: racha de pérdidas consecutivas
    open_positions: Dict[str, str] = field(default_factory=dict)

    # Cooldown post-SL: símbolo → scan_count cuando se puede volver a entrar
    sl_cooldown: Dict[str, int] = field(default_factory=dict)

    @property
    def drawdown_pct(self) -> float:
        if self.peak_balance == 0:
            return 0.0
        return (self.peak_balance - self.current_balance) / self.peak_balance * 100

    @property
    def all_time_winrate(self) -> float:
        total = self.all_time_wins + self.all_time_losses
        return self.all_time_wins / total if total > 0 else 0.5


class RiskManager:
    def __init__(self, cfg: dict, data_path: str = "data/equity.json"):
        self.cfg = cfg
        self.data_path = Path(data_path)
        self.state = self._load_state()
        self._ensure_today()
        self._current_scan = 0   # Se actualiza desde main.py

    # ── Persistencia ──────────────────────────────────────────────────────

    def _load_state(self) -> EquityState:
        if self.data_path.exists():
            try:
                with open(self.data_path) as f:
                    d = json.load(f)
                today_data = d.pop("today", {})
                # Compatibilidad: ignorar campos desconocidos
                valid_keys = {f.name for f in EquityState.__dataclass_fields__.values()}
                filtered = {k: v for k, v in d.items() if k in valid_keys}
                state = EquityState(**filtered)
                if today_data:
                    state.today = DayStats(**today_data)
                return state
            except Exception as e:
                logger.warning(f"No se pudo cargar equity state: {e} — iniciando desde cero")
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
            self.state.today = DayStats(date=today_str)
            self.save()

    def set_scan_count(self, n: int):
        """Llamado desde main.py para que el risk manager sepa el scan actual."""
        self._current_scan = n

    # ── Kelly mejorado ─────────────────────────────────────────────────────

    def _kelly_fraction(self, quality: int) -> float:
        """
        Half-Kelly con win rate real desde el primer trade,
        ajustado por calidad y reducido en rachas malas.
        """
        # Win rate: usar histórico real si hay suficientes trades
        total_closed = self.state.all_time_wins + self.state.all_time_losses
        if total_closed >= 5:
            wr = self.state.all_time_winrate
        else:
            wr = 0.48   # Conservador hasta tener historial

        avg_rr = self.cfg.get("rr2", 2.0)
        kelly_raw = (wr * (avg_rr + 1) - 1) / avg_rr
        kelly_raw = max(0.01, min(kelly_raw, 0.25))

        # Half-Kelly para reducir varianza
        half_kelly = kelly_raw * 0.5

        # Ajuste por calidad (5-10 → mult 0.6-1.0)
        quality_mult = 0.5 + (max(quality, 0) / 10) * 0.5
        adjusted = half_kelly * quality_mult

        # Reducción por racha de pérdidas consecutivas
        consec = self.state.consecutive_losses
        if consec >= 4:
            adjusted *= 0.4    # -60% tras 4+ pérdidas seguidas
            logger.warning(f"Sizing reducido al 40% — {consec} pérdidas consecutivas")
        elif consec >= 2:
            adjusted *= 0.65   # -35% tras 2-3 pérdidas seguidas

        max_risk = self.cfg.get("max_risk_per_trade_pct", 1.5) / 100
        return min(adjusted, max_risk)

    # ── Verificación principal ─────────────────────────────────────────────

    def approve(self, symbol: str, direction: str, quality: int,
                signal_result=None) -> RiskDecision:
        self._ensure_today()
        c = self.cfg
        state = self.state
        bal = state.current_balance

        def reject(reason: str) -> RiskDecision:
            logger.info(f"Risk REJECT [{symbol}]: {reason}")
            return RiskDecision(approved=False, reason=reason,
                                position_usdt=0, risk_pct=0, Kelly_fraction=0)

        # 1. Calidad mínima
        min_quality = c.get("min_signal_quality", 5)
        if quality < min_quality:
            return reject(f"Calidad {quality} < mínimo {min_quality}")

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

        # 4. Drawdown máximo
        max_drawdown = c.get("max_drawdown_pct", 15.0)
        if state.drawdown_pct >= max_drawdown:
            return reject(f"Drawdown {state.drawdown_pct:.1f}% ≥ límite {max_drawdown}%")

        # 5. Máximo de trades simultáneos
        max_open = c.get("max_open_trades", 3)
        if len(state.open_positions) >= max_open:
            return reject(f"Máx trades simultáneos ({max_open}) alcanzado")

        # 6. Ya hay posición abierta en ese símbolo
        if symbol in state.open_positions:
            return reject(f"Ya hay posición abierta en {symbol}")

        # 7. Cooldown post-SL (nuevo)
        sl_cd = state.sl_cooldown.get(symbol, 0)
        if sl_cd > self._current_scan:
            scans_left = sl_cd - self._current_scan
            return reject(f"Cooldown post-SL activo — {scans_left} scans restantes")

        # 8. Correlación — no abrir mismo lado en pares correlacionados
        for corr_set in CORRELATED_PAIRS:
            if symbol in corr_set:
                for other_sym, other_dir in state.open_positions.items():
                    if other_sym in corr_set and other_dir == direction:
                        return reject(f"Correlación: {other_sym} ya abierto {direction}")

        # 9. Filtro de sesión
        if not self._session_filter():
            return reject("Fuera de sesión de trading (baja liquidez UTC)")

        # ── Calcular tamaño ────────────────────────────────────────────────
        kelly_frac = self._kelly_fraction(quality)
        risk_usdt = bal * kelly_frac
        leverage = c.get("leverage", 5)
        position_usdt = risk_usdt * leverage
        max_pos = c.get("max_position_usdt", 500.0)
        position_usdt = min(position_usdt, max_pos)
        risk_pct = kelly_frac * 100

        logger.info(
            f"Risk APPROVE [{symbol} {direction}] "
            f"Q={quality} WR={self.state.all_time_winrate*100:.0f}% "
            f"Kelly={kelly_frac*100:.2f}% Pos={position_usdt:.1f} USDT"
        )

        return RiskDecision(
            approved=True, reason="OK",
            position_usdt=position_usdt,
            risk_pct=risk_pct,
            Kelly_fraction=kelly_frac,
        )

    # ── Filtro de sesión ───────────────────────────────────────────────────

    def _session_filter(self) -> bool:
        if not self.cfg.get("use_session_filter", True):
            return True
        hour = datetime.now(timezone.utc).hour
        avoid_hours = self.cfg.get("avoid_hours_utc", [0, 1, 2, 3])
        return hour not in avoid_hours

    # ── Registro de trades ─────────────────────────────────────────────────

    def register_open(self, symbol: str, direction: str):
        self.state.open_positions[symbol] = direction
        self.state.today.trades += 1
        self.state.all_time_trades += 1
        self.save()
        logger.info(
            f"Posición abierta: {symbol} {direction} | "
            f"Abiertas: {len(self.state.open_positions)}"
        )

    def register_close(self, symbol: str, pnl_usdt: float, won: bool):
        """
        Registra el cierre de un trade.
        won=True si cerró en TP, won=False si cerró en SL.
        """
        self.state.open_positions.pop(symbol, None)
        self.state.today.pnl_usdt += pnl_usdt
        self.state.week_pnl += pnl_usdt
        self.state.total_pnl += pnl_usdt
        self.state.current_balance += pnl_usdt

        if won:
            self.state.today.wins += 1
            self.state.all_time_wins += 1
            self.state.consecutive_losses = 0   # Reset racha de pérdidas
        else:
            self.state.today.losses += 1
            self.state.all_time_losses += 1
            self.state.consecutive_losses += 1  # Incrementar racha

            # Cooldown post-SL: bloquear re-entrada N scans
            cd_scans = self.cfg.get("post_sl_cooldown_scans", 2)
            self.state.sl_cooldown[symbol] = self._current_scan + cd_scans
            logger.info(f"Cooldown post-SL: {symbol} bloqueado {cd_scans} scans")

        # Actualizar equity peak
        if self.state.current_balance > self.state.peak_balance:
            self.state.peak_balance = self.state.current_balance

        self.save()

        total_closed = self.state.all_time_wins + self.state.all_time_losses
        wr_pct = self.state.all_time_winrate * 100

        emoji = "✅" if won else "❌"
        logger.info(
            f"{emoji} Cierre: {symbol} PnL={pnl_usdt:+.2f} USDT | "
            f"Balance={self.state.current_balance:.2f} | "
            f"WR={wr_pct:.0f}% ({self.state.all_time_wins}/{total_closed}) | "
            f"Racha pérdidas: {self.state.consecutive_losses}"
        )

    # ── Resumen para dashboard ─────────────────────────────────────────────

    def summary(self) -> dict:
        s = self.state
        total_closed = s.all_time_wins + s.all_time_losses
        return {
            "balance": s.current_balance,
            "peak": s.peak_balance,
            "drawdown_pct": s.drawdown_pct,
            "total_pnl": s.total_pnl,
            "today_pnl": s.today.pnl_usdt,
            "week_pnl": s.week_pnl,
            "today_trades": s.today.trades,
            "today_wins": s.today.wins,
            "today_losses": s.today.losses,
            "all_trades": s.all_time_trades,
            "all_wins": s.all_time_wins,
            "all_losses": s.all_time_losses,
            "winrate": s.all_time_winrate,
            "winrate_pct": round(s.all_time_winrate * 100, 1),
            "consecutive_losses": s.consecutive_losses,
            "open_positions": len(s.open_positions),
            "total_closed": total_closed,
        }

    # ── Alerta de tasa de errores ──────────────────────────────────────────

    def should_alert_error_rate(self, errors: int, total: int) -> bool:
        """Devuelve True si la tasa de errores supera el 15%."""
        if total == 0:
            return False
        return (errors / total) > 0.15
