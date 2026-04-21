"""
Reward Scheme — PBR (Price-Based Reward)
Basado en el indicador TensorTrade de la imagen.

Calcula una recompensa por cada acción tomada multiplicando
el retorno de precio por la posición mantenida:
  reward = price_return * position  (LONG=+1, SHORT=-1, flat=0)

Se usa para:
  1. Loggear la calidad de cada trade cerrado
  2. Ajustar el filtro MIN_MEAN_PNL dinámicamente según histórico de recompensas
  3. Exponer métricas en el dashboard
"""

import logging
import numpy as np
from collections import deque
from typing import Optional

log = logging.getLogger("REWARD")

# Ventana de recompensas históricas para adaptar el umbral
_REWARD_WINDOW = 50


class PBR:
    """
    Price-Based Reward Scheme.

    position: +1 (LONG) / -1 (SHORT) / 0 (sin posición)
    """

    registered_name = "pbr"

    def __init__(self):
        self.position: int = -1          # empieza sin posición (−1 = flat/short neutral)
        self._price_history: list[float] = []
        self._rewards: deque[float] = deque(maxlen=_REWARD_WINDOW)

    # ------------------------------------------------------------------ #
    def on_action(self, action: int):
        """
        Registra la acción tomada.
        action: 0 → posición −1 (SHORT/flat)
                1 → posición +1 (LONG)
        """
        self.position = -1 if action == 0 else 1
        log.debug(f"PBR on_action: action={action}  position={self.position}")

    def feed_price(self, price: float):
        """Añade un nuevo precio al historial."""
        self._price_history.append(price)

    def get_reward(self) -> Optional[float]:
        """
        Calcula la recompensa de la última barra:
          reward = price_diff_pct * position
        Devuelve None si no hay suficientes datos.
        """
        if len(self._price_history) < 2:
            return None

        p_prev = self._price_history[-2]
        p_curr = self._price_history[-1]

        if p_prev == 0:
            return None

        price_return = (p_curr - p_prev) / p_prev   # retorno normalizado
        reward = price_return * self.position

        self._rewards.append(reward)
        log.debug(f"PBR reward={reward:.6f}  (ret={price_return:.4%}  pos={self.position})")
        return reward

    def reset(self):
        """Reinicia el esquema (nuevo episodio / nuevo trade)."""
        self.position = -1
        self._price_history.clear()
        log.debug("PBR reset")

    # ------------------------------------------------------------------ #
    #  Métricas agregadas
    # ------------------------------------------------------------------ #
    @property
    def mean_reward(self) -> float:
        if not self._rewards:
            return 0.0
        return float(np.mean(self._rewards))

    @property
    def cumulative_reward(self) -> float:
        return float(sum(self._rewards))

    @property
    def sharpe(self) -> float:
        """Sharpe simplificado sobre la ventana de recompensas."""
        if len(self._rewards) < 2:
            return 0.0
        arr = np.array(self._rewards)
        std = arr.std()
        return float(arr.mean() / std) if std > 0 else 0.0

    def summary(self) -> dict:
        return {
            "mean_reward":       round(self.mean_reward, 6),
            "cumulative_reward": round(self.cumulative_reward, 6),
            "sharpe":            round(self.sharpe, 4),
            "n_samples":         len(self._rewards),
            "position":          self.position,
        }
