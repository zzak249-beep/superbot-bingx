"""
risk_manager.py — Control de riesgo y sizing dinámico
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import config as C
from strategy import Signal

log = logging.getLogger(__name__)
STATE_FILE = "state.json"


class RiskManager:
    def __init__(self):
        self.daily_pnl        = 0.0
        self.daily_date       = ""
        self.cooldown_until   = 0   # timestamp unix
        self.consecutive_losses = 0
        self._load_state()

    # ── Estado persistente ─────────────────────────────────────

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    s = json.load(f)
                self.daily_pnl          = s.get("daily_pnl", 0.0)
                self.daily_date         = s.get("daily_date", "")
                self.cooldown_until     = s.get("cooldown_until", 0)
                self.consecutive_losses = s.get("consecutive_losses", 0)
                log.info(f"Estado cargado: daily_pnl={self.daily_pnl:.2f} "
                         f"cooldown_until={self.cooldown_until}")
            except Exception as e:
                log.warning(f"No se pudo cargar state.json: {e}")
        self._reset_daily_if_needed()

    def _save_state(self):
        try:
            with open(STATE_FILE, "w") as f:
                json.dump({
                    "daily_pnl":          self.daily_pnl,
                    "daily_date":         self.daily_date,
                    "cooldown_until":     self.cooldown_until,
                    "consecutive_losses": self.consecutive_losses,
                }, f)
        except Exception as e:
            log.error(f"Error guardando state: {e}")

    def _reset_daily_if_needed(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.daily_date != today:
            self.daily_pnl  = 0.0
            self.daily_date = today
            log.info(f"Reset daily PnL para {today}")
            self._save_state()

    # ── Sizing dinámico ────────────────────────────────────────

    def position_size(self, balance: float, signal: Signal,
                      price: float) -> float:
        """
        Calcula la cantidad de contratos a operar.
        - Riesgo base: RISK_PER_TRADE del capital
        - Multiplicador por convicción: 0.5× (score 5) → 1.5× (score 10)
        - Reducción tras pérdidas consecutivas
        """
        if balance <= 0 or signal.sl == 0 or price == 0:
            return 0.0

        risk_usdt = balance * C.RISK_PER_TRADE

        # Multiplicador convicción (5→0.7×, 7→1.0×, 10→1.4×)
        conv_mult = 0.4 + (signal.conviction / 10) * 1.0
        conv_mult = max(0.5, min(1.5, conv_mult))

        # Reducción por pérdidas consecutivas
        loss_mult = max(0.3, 1.0 - self.consecutive_losses * 0.2)

        risk_usdt *= conv_mult * loss_mult

        # Riesgo por contrato = diferencia entry-SL en USDT
        sl_dist = abs(price - signal.sl)
        if sl_dist == 0:
            return 0.0

        qty = (risk_usdt / sl_dist) / C.LEVERAGE
        qty = max(qty, 0.0)

        log.info(f"Sizing: balance={balance:.2f} risk_usdt={risk_usdt:.2f} "
                 f"sl_dist={sl_dist:.4f} conv_mult={conv_mult:.2f} "
                 f"loss_mult={loss_mult:.2f} qty={qty:.6f}")
        return qty

    # ── Validaciones ───────────────────────────────────────────

    def can_trade(self, balance: float, n_open_positions: int) -> tuple[bool, str]:
        self._reset_daily_if_needed()

        import time
        if time.time() < self.cooldown_until:
            remaining = int(self.cooldown_until - time.time()) // 60
            return False, f"Cooldown activo — {remaining}min restantes"

        if n_open_positions >= C.MAX_POSITIONS:
            return False, f"Máximo de posiciones alcanzado ({C.MAX_POSITIONS})"

        if balance <= 0:
            return False, "Balance cero o negativo"

        loss_pct = abs(self.daily_pnl) / max(balance, 1)
        if self.daily_pnl < 0 and loss_pct >= C.DAILY_LOSS_LIMIT:
            return False, (f"Límite diario alcanzado: "
                           f"{self.daily_pnl:.2f} USDT ({loss_pct*100:.1f}%)")

        return True, "OK"

    def can_trade_direction(self, direction: str,
                            positions: list) -> tuple[bool, str]:
        """Evita hedging — no abrir en dirección contraria si ya hay posición"""
        for p in positions:
            amt = float(p.get("positionAmt", 0))
            if direction == "LONG"  and amt < 0:
                return False, "Ya hay SHORT abierto — ciérralo primero"
            if direction == "SHORT" and amt > 0:
                return False, "Ya hay LONG abierto — ciérralo primero"
        return True, "OK"

    # ── Actualizar PnL ─────────────────────────────────────────

    def record_trade_result(self, pnl_usdt: float):
        self._reset_daily_if_needed()
        self.daily_pnl += pnl_usdt

        import time
        if pnl_usdt < 0:
            self.consecutive_losses += 1
            cooldown_secs = C.COOLDOWN_CANDLES * 3 * 60  # velas × 3min
            self.cooldown_until = time.time() + cooldown_secs
            log.warning(f"Pérdida {pnl_usdt:.2f} USDT | "
                        f"consecutivas={self.consecutive_losses} | "
                        f"cooldown={C.COOLDOWN_CANDLES} velas")
        else:
            self.consecutive_losses = 0
            self.cooldown_until = 0
            log.info(f"Ganancia {pnl_usdt:.2f} USDT")

        self._save_state()

    # ── Trailing Stop ──────────────────────────────────────────

    def trailing_sl(self, direction: str, current_price: float,
                    current_sl: float, atr: float) -> float:
        """Ajusta SL dinámicamente con ATR trailing"""
        trail = atr * C.TRAIL_ATR_MULT
        if direction == "LONG":
            new_sl = current_price - trail
            return max(new_sl, current_sl)
        else:
            new_sl = current_price + trail
            return min(new_sl, current_sl)
