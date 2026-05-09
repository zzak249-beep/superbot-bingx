"""
bingx_additions.py — Métodos que faltan en BingXClient
=======================================================
INSTRUCCIONES:
Copia estos métodos dentro de tu clase BingXClient en bingx_client.py.
No reemplaces el archivo — añade los métodos a la clase existente.

Requerimientos adicionales en imports de bingx_client.py:
  import math
"""

import math


# ────────────────────────────────────────────────────────────────────────────
# Pegar dentro de class BingXClient:
# ────────────────────────────────────────────────────────────────────────────

def get_symbol_info(self, symbol: str) -> dict:
    """
    Obtiene metadatos del contrato (stepSize, tickSize, minQty, etc.)
    desde el endpoint de exchangeInfo de BingX perpetuals.
    Cachea el resultado para evitar llamadas repetidas.
    """
    if not hasattr(self, "_symbol_info_cache"):
        self._symbol_info_cache = {}

    if symbol in self._symbol_info_cache:
        return self._symbol_info_cache[symbol]

    try:
        # BingX endpoint: GET /openApi/swap/v2/quote/contracts
        url    = "/openApi/swap/v2/quote/contracts"
        params = {}
        resp   = self._request("GET", url, params, signed=False)

        # La respuesta contiene una lista de contratos
        contracts = resp.get("data", [])
        for c in contracts:
            sym = c.get("symbol", "")
            self._symbol_info_cache[sym] = c

        return self._symbol_info_cache.get(symbol, {})

    except Exception as e:
        # Fallback si el endpoint falla
        return {
            "tradeMinQty": "0.001",
            "tradeMinNotional": "5",
        }


def calc_quantity(self, symbol: str, position_usdt: float, price: float) -> float:
    """
    Calcula la cantidad de contratos dado un tamaño en USDT y precio actual.

    Aplica stepSize (tradeMinQty) del contrato para garantizar que
    la orden no sea rechazada por BingX por precisión incorrecta.

    Ejemplo:
        position_usdt = 30.0 (10 USDT base × leverage 3)
        price         = 0.2574 (ADA)
        → raw_qty = 116.55
        → step    = 1.0 (ADA tiene stepSize entero)
        → qty     = 116.0
    """
    if price <= 0 or position_usdt <= 0:
        return 0.0

    raw_qty = position_usdt / price

    # Obtener stepSize del contrato
    step = 0.001  # default conservador
    try:
        info    = self.get_symbol_info(symbol)
        step_s  = info.get("tradeMinQty", info.get("stepSize", "0.001"))
        step    = float(step_s)
        if step <= 0:
            step = 0.001
    except Exception:
        pass

    # Floor al múltiplo de step más cercano (no round — siempre hacia abajo)
    qty = math.floor(raw_qty / step) * step
    qty = round(qty, 8)

    # Verificar notional mínimo
    try:
        min_notional = float(
            self.get_symbol_info(symbol).get("tradeMinNotional", "5")
        )
        if qty * price < min_notional:
            return 0.0
    except Exception:
        pass

    return qty if qty > 0 else 0.0


def get_open_positions(self) -> list:
    """
    Devuelve lista de posiciones abiertas en BingX perpetuals.
    Usado por _sync_open_positions() en main.py al arrancar.

    Retorna lista de dicts con keys:
        symbol, positionAmt, avgPrice, positionSide, leverage, unrealizedProfit
    """
    try:
        url    = "/openApi/swap/v2/user/positions"
        params = {"timestamp": self._ts()}
        resp   = self._request("GET", url, params, signed=True)
        data   = resp.get("data", [])

        # Filtrar solo posiciones con tamaño real
        return [
            p for p in data
            if abs(float(p.get("positionAmt", 0))) > 1e-9
        ]
    except Exception as e:
        return []


# ────────────────────────────────────────────────────────────────────────────
# Helper privado (si no tienes _ts() en tu cliente, añade esto también)
# ────────────────────────────────────────────────────────────────────────────

def _ts(self) -> int:
    """Timestamp en milisegundos para firmar requests."""
    import time
    return int(time.time() * 1000)


# ────────────────────────────────────────────────────────────────────────────
# NOTAS DE INTEGRACIÓN
# ────────────────────────────────────────────────────────────────────────────
#
# 1. El método _request(method, url, params, signed) debe existir en tu
#    BingXClient. Si usas una firma diferente, adapta la llamada.
#
# 2. El endpoint /openApi/swap/v2/quote/contracts devuelve todos los
#    contratos disponibles. Cachear evita rate limiting.
#
# 3. Para ADA-USDT el stepSize suele ser 1 (cantidades enteras).
#    Para BTC-USDT puede ser 0.001. calc_quantity lo gestiona automáticamente.
#
# 4. Si tu BingXClient ya tiene un método _get_symbol_info() interno,
#    adapta get_symbol_info() para llamarlo en lugar de hacer la request.
#
# 5. Verificación rápida en Railway logs:
#    Deberías ver "✅ TRADE ABIERTO" en lugar de
#    "'BingXClient' object has no attribute 'calc_quantity'"
