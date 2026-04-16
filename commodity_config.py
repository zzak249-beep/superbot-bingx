"""
commodity_config.py — SuperBot v5.0 Phase 1
Detecta la clase de activo de un símbolo y devuelve parámetros específicos.

Clases soportadas:
  - crypto:    Bitcoin, altcoins (comportamiento normal)
  - commodity: Oro, Plata, Petróleo (macro-driven, menos volátiles en crypto terms)

BingX Perpetual Futures — Symbols verificados:
  XAUUSDT  → Oro (Gold)
  XAGUSD   → Plata (Silver)  [nota: algunos brokers usan XAGUSDT]
  USOILUSDT → Petróleo WTI

Si BingX usa nombres distintos en tu cuenta, ajusta COMMODITY_SYMBOLS abajo
o sobreescribe con la variable de entorno FORCE_SYMBOLS=XAUUSDT,USOILUSDT
"""

from typing import Dict, Any

# ── Símbolos de commodities en BingX ─────────────────────────────────────────
# Ajusta estos si BingX muestra nombres distintos en tu cuenta
COMMODITY_SYMBOLS = [
    "XAUUSDT",    # Oro / Gold
    "XAGUSD",     # Plata / Silver  (sin T final en algunos pares BingX)
    "USOILUSDT",  # Petróleo WTI
]

# Prefijos/sufijos que identifican commodities automáticamente
_COMMODITY_PREFIXES = ("XAU", "XAG", "XPT", "XPD")   # metales spot
_COMMODITY_KEYWORDS = ("OIL", "CRUDE", "BRENT", "GAS", "GOLD", "SILVER")

# ── Parámetros por clase de activo ────────────────────────────────────────────

ASSET_PARAMS: Dict[str, Dict[str, Any]] = {

    "crypto": {
        # Parámetros heredados del bot original
        "min_rr":       1.2,
        "min_score":    52.0,
        "leverage":     10,
        "trail_rate":   0.025,   # 2.5%
        "sl_atr_mult":  1.2,
        "tp_atr_mult":  2.5,
        "vol_min_24h":  500_000,
        "notes":        "Alta volatilidad, sigue a BTC, indicadores estándar",
    },

    "commodity": {
        # Commodities: más lentos, movimientos macro, spreads más altos
        "min_rr":       1.5,     # exigimos más RR (movimientos más predecibles)
        "min_score":    60.0,    # más selectivos (menos ruido intradía)
        "leverage":     5,       # menos leverage (gap risk en sesiones Asia/Europa)
        "trail_rate":   0.015,   # 1.5% (movimientos más lentos)
        "sl_atr_mult":  1.5,     # SL más holgado (spreads mayores)
        "tp_atr_mult":  3.0,     # TP más amplio (tendencias más sostenidas)
        "vol_min_24h":  100_000, # volumen mínimo menor (commodities menos líquidos)
        "notes":        "Macro-driven, sesiones NY/London, ATR más estable",
    },
}

# ── Funciones públicas ────────────────────────────────────────────────────────

def get_asset_class(symbol: str) -> str:
    """
    Detecta si un símbolo es 'commodity' o 'crypto'.

    Ejemplos:
        get_asset_class("XAUUSDT")    → "commodity"
        get_asset_class("USOILUSDT")  → "commodity"
        get_asset_class("BTCUSDT")    → "crypto"
        get_asset_class("ETHUSDT")    → "crypto"
    """
    sym = symbol.upper()

    # Check lista explícita primero
    if sym in COMMODITY_SYMBOLS:
        return "commodity"

    # Check prefijos de metales preciosos (XAU, XAG, XPT, XPD)
    for prefix in _COMMODITY_PREFIXES:
        if sym.startswith(prefix):
            return "commodity"

    # Check keywords
    for kw in _COMMODITY_KEYWORDS:
        if kw in sym:
            return "commodity"

    return "crypto"


def get_asset_params(asset_class: str) -> Dict[str, Any]:
    """
    Devuelve los parámetros de trading para una clase de activo.
    Fallback a 'crypto' si la clase no existe.
    """
    return ASSET_PARAMS.get(asset_class, ASSET_PARAMS["crypto"])


def get_symbol_params(symbol: str) -> Dict[str, Any]:
    """Shortcut: detecta clase y devuelve parámetros en un paso."""
    return get_asset_params(get_asset_class(symbol))


def list_commodities() -> list:
    """Devuelve la lista de símbolos de commodities configurados."""
    return list(COMMODITY_SYMBOLS)


# ── Validación al importar ────────────────────────────────────────────────────
if __name__ == "__main__":
    test_symbols = [
        "XAUUSDT", "XAGUSD", "USOILUSDT",
        "BTCUSDT", "ETHUSDT", "SOLUSDT",
        "BLUR-USDT", "TRB-USDT",
    ]
    print("Symbol Classification Test:")
    print("-" * 40)
    for s in test_symbols:
        cls    = get_asset_class(s)
        params = get_asset_params(cls)
        print(f"  {s:<15} → {cls:<10} | "
              f"RR≥{params['min_rr']} | "
              f"Score≥{params['min_score']} | "
              f"Lev={params['leverage']}x | "
              f"Trail={params['trail_rate']*100:.1f}%")
