"""
learning_engine.py — Motor de aprendizaje adaptativo.
Registra cada trade, analiza patrones y ajusta automáticamente
los umbrales de ADX y fuerza de señal para maximizar el winrate.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Tuple

from config import (
    LEARNING_FILE, MIN_TRADES_TO_LEARN,
    ADX_MIN, VOL_MULT, SYMBOL_BLACKLIST_WR, DATA_DIR,
)

logger = logging.getLogger(__name__)


class LearningEngine:
    def __init__(self, telegram=None):
        self.telegram = telegram
        os.makedirs(DATA_DIR, exist_ok=True)

        self.trades: list = self._load()

        # Parámetros dinámicos — parten de los valores del config
        self.params = {
            "adx_min":     ADX_MIN,
            "min_strength": 35.0,
            "vol_mult":    VOL_MULT,
        }

        # Resumen de aprendizaje acumulado
        self.adjustments: list = []

    # ──────────────────────────────────────────────────────
    # Persistencia
    # ──────────────────────────────────────────────────────
    def _load(self) -> list:
        try:
            if os.path.exists(LEARNING_FILE):
                with open(LEARNING_FILE, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error cargando trades: {e}")
        return []

    def _save(self):
        try:
            with open(LEARNING_FILE, "w") as f:
                json.dump(self.trades, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Error guardando trades: {e}")

    # ──────────────────────────────────────────────────────
    # Registro
    # ──────────────────────────────────────────────────────
    def record(self, symbol: str, signal: dict, outcome: dict):
        """Graba un trade cerrado y dispara el aprendizaje."""
        trade = {
            "id":           len(self.trades) + 1,
            "ts":           datetime.now(timezone.utc).isoformat(),
            "symbol":       symbol,
            "direction":    signal.get("signal"),
            "entry":        signal.get("entry"),
            "sl":           signal.get("sl"),
            "tp":           signal.get("tp"),
            "adx":          signal.get("adx"),
            "strength":     signal.get("strength"),
            "vol_ratio":    signal.get("vol_ratio"),
            "pnl":          outcome.get("pnl", 0.0),
            "reason":       outcome.get("reason", "unknown"),
            "duration_min": outcome.get("duration_min", 0),
            "won":          outcome.get("pnl", 0.0) > 0,
        }
        self.trades.append(trade)
        self._save()

        logger.info(
            f"Trade #{trade['id']} {symbol} {trade['direction']} "
            f"pnl={trade['pnl']:.4f} reason={trade['reason']}"
        )

        # Aprende cada 5 trades
        if len(self.trades) % 5 == 0:
            self._learn()

    # ──────────────────────────────────────────────────────
    # Motor de aprendizaje
    # ──────────────────────────────────────────────────────
    def _learn(self):
        if len(self.trades) < MIN_TRADES_TO_LEARN:
            return

        recent  = self.trades[-20:]   # ventana deslizante de 20 trades
        wins    = [t for t in recent if t["won"]]
        losses  = [t for t in recent if not t["won"]]
        winrate = len(wins) / len(recent) * 100

        logger.info(f"Learning: WR={winrate:.1f}% ({len(wins)}/{len(recent)}) | params={self.params}")

        old_params = dict(self.params)
        reason     = None

        # ── Winrate bajo → endurecer filtros ──
        if winrate < 40 and len(recent) >= 10:
            self.params["adx_min"]     = min(35.0, self.params["adx_min"] + 2.0)
            self.params["min_strength"] = min(65.0, self.params["min_strength"] + 5.0)
            reason = f"WR bajo ({winrate:.0f}%) → filtros más estrictos"

        # ── Winrate alto → relajar levemente para capturar más señales ──
        elif winrate > 65 and len(recent) >= 10:
            self.params["adx_min"]     = max(float(ADX_MIN), self.params["adx_min"] - 1.0)
            self.params["min_strength"] = max(30.0, self.params["min_strength"] - 2.0)
            reason = f"WR alto ({winrate:.0f}%) → relajando filtros"

        # ── Análisis de pérdidas: ADX bajo correlacionado ──
        if losses and len(losses) >= 3:
            avg_adx_loss = sum(t.get("adx", 0) or 0 for t in losses) / len(losses)
            if avg_adx_loss < self.params["adx_min"] + 2 and not reason:
                self.params["adx_min"] = min(30.0, self.params["adx_min"] + 1.0)
                reason = f"Pérdidas correlacionadas con ADX bajo (media={avg_adx_loss:.1f})"

        if reason and old_params != self.params:
            self.adjustments.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
                "old": old_params,
                "new": dict(self.params),
            })
            logger.info(f"Params actualizados: {old_params} → {self.params} | {reason}")
            if self.telegram:
                try:
                    self.telegram.notify_learning_update(old_params, self.params, reason)
                except Exception:
                    pass

    # ──────────────────────────────────────────────────────
    # Filtro de señales (consultado antes de cada trade)
    # ──────────────────────────────────────────────────────
    def should_take(self, signal: dict) -> Tuple[bool, str]:
        """Aplica los umbrales aprendidos a una señal nueva."""
        adx      = signal.get("adx", 0) or 0
        strength = signal.get("strength", 0) or 0

        if adx < self.params["adx_min"]:
            return False, f"ADX {adx:.1f} < umbral aprendido {self.params['adx_min']}"
        if strength < self.params["min_strength"]:
            return False, f"Fuerza {strength:.1f} < umbral aprendido {self.params['min_strength']}"
        return True, "OK"

    # ──────────────────────────────────────────────────────
    # Listas negras por símbolo
    # ──────────────────────────────────────────────────────
    def is_blacklisted(self, symbol: str) -> bool:
        sym_trades = [t for t in self.trades if t["symbol"] == symbol]
        if len(sym_trades) < 5:
            return False
        wins = sum(1 for t in sym_trades if t["won"])
        wr   = wins / len(sym_trades) * 100
        if wr < SYMBOL_BLACKLIST_WR:
            logger.info(f"Blacklist: {symbol} WR={wr:.0f}% en {len(sym_trades)} trades")
            if self.telegram:
                try:
                    self.telegram.notify_blacklist(symbol, wr, len(sym_trades))
                except Exception:
                    pass
            return True
        return False

    # ──────────────────────────────────────────────────────
    # Estadísticas
    # ──────────────────────────────────────────────────────
    def get_stats(self, today_only: bool = True) -> dict:
        if today_only:
            today = datetime.now(timezone.utc).date().isoformat()
            trades = [t for t in self.trades if t["ts"].startswith(today)]
        else:
            trades = self.trades

        if not trades:
            return {
                "total": 0, "wins": 0, "losses": 0,
                "winrate": 0.0, "total_pnl": 0.0,
                "learning_notes": "Sin trades aún",
            }

        wins   = [t for t in trades if t["won"]]
        losses = [t for t in trades if not t["won"]]
        pnl    = sum(t.get("pnl", 0) for t in trades)
        wr     = len(wins) / len(trades) * 100

        return {
            "total":      len(trades),
            "wins":       len(wins),
            "losses":     len(losses),
            "winrate":    round(wr, 1),
            "total_pnl":  round(pnl, 4),
            "learning_notes": (
                f"ADX≥{self.params['adx_min']:.0f}  "
                f"Fuerza≥{self.params['min_strength']:.0f}%  "
                f"({len(self.adjustments)} ajustes realizados)"
            ),
        }

    def total_trades(self) -> int:
        return len(self.trades)
