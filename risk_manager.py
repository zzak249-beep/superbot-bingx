"""
risk_manager.py — Gestión de riesgo y tamaño de posición.
Basado en % de capital con stop definido por la estructura del mercado.
"""
import logging
from config import CAPITAL_PCT, MAX_OPEN_TRADES, LEVERAGE

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self):
        self._open: dict = {}  # symbol -> trade_meta

    # ──────────────────────────────────────────────────────
    # Validaciones
    # ──────────────────────────────────────────────────────
    def can_open(self, symbol: str) -> tuple:
        """(bool, motivo)"""
        if symbol in self._open:
            return False, f"{symbol} ya tiene posición abierta"
        if len(self._open) >= MAX_OPEN_TRADES:
            return False, f"Máximo de {MAX_OPEN_TRADES} trades simultáneos alcanzado"
        return True, "OK"

    # ──────────────────────────────────────────────────────
    # Tamaño de posición
    # ──────────────────────────────────────────────────────
    def calc_quantity(self, balance: float, entry: float, sl: float) -> float:
        """
        Calcula la cantidad a operar para arriesgar exactamente CAPITAL_PCT%
        del balance disponible, dado el apalancamiento configurado.

        qty = (balance * riesgo%) * leverage / precio_entrada

        La distancia al SL define el riesgo real; aquí usamos el enfoque
        conservador de arriesgar sobre el capital nominal (más simple y seguro).
        """
        if entry <= 0 or sl <= 0:
            return 0.0

        risk_distance = abs(entry - sl) / entry  # % de pérdida si llega al SL

        if risk_distance == 0:
            return 0.0

        # Capital que queremos arriesgar en USDT
        risk_usdt = balance * (CAPITAL_PCT / 100)

        # Tamaño nocional = riesgo / distancia_sl
        notional = risk_usdt / risk_distance

        # Cantidad de contratos = nocional / precio (con apalancamiento ya incluido
        # porque BingX calcula margen = nocional / leverage)
        qty = notional / entry
        qty = round(qty, 4)

        logger.debug(
            f"Qty calc: balance={balance:.2f} risk%={CAPITAL_PCT} "
            f"risk_dist={risk_distance:.4f} notional={notional:.2f} qty={qty}"
        )
        return qty

    # ──────────────────────────────────────────────────────
    # Registro de trades
    # ──────────────────────────────────────────────────────
    def register(self, symbol: str, meta: dict):
        self._open[symbol] = meta
        logger.info(f"Trade registrado: {symbol} | Abiertos: {len(self._open)}/{MAX_OPEN_TRADES}")

    def close(self, symbol: str) -> dict | None:
        trade = self._open.pop(symbol, None)
        if trade:
            logger.info(f"Trade cerrado: {symbol} | Abiertos: {len(self._open)}/{MAX_OPEN_TRADES}")
        return trade

    def is_open(self, symbol: str) -> bool:
        return symbol in self._open

    def get_open(self) -> dict:
        return dict(self._open)

    def open_count(self) -> int:
        return len(self._open)
