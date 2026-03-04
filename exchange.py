"""
exchange.py — Conexión y órdenes en BingX Futures (perpetual swaps)
Soporta modo DEMO (sin órdenes reales) y modo REAL
"""

import hmac
import hashlib
import time
import requests
import json
from urllib.parse import urlencode
from datetime import datetime
import config

BASE_URL = "https://open-api.bingx.com"


def _sign(params: dict) -> str:
    query = urlencode(sorted(params.items()))
    return hmac.new(
        config.BINGX_SECRET_KEY.encode(),
        query.encode(),
        hashlib.sha256
    ).hexdigest()


def _headers() -> dict:
    return {
        "X-BX-APIKEY": config.BINGX_API_KEY,
        "Content-Type": "application/json"
    }


def _get(path: str, params: dict = None) -> dict:
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    try:
        r = requests.get(BASE_URL + path, params=params, headers=_headers(), timeout=10)
        return r.json()
    except Exception as e:
        print(f"[EXCHANGE] GET error {path}: {e}")
        return {}


def _post(path: str, params: dict) -> dict:
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    try:
        r = requests.post(BASE_URL + path, data=json.dumps(params), headers=_headers(), timeout=10)
        return r.json()
    except Exception as e:
        print(f"[EXCHANGE] POST error {path}: {e}")
        return {}


# ============================================================
# BALANCE
# ============================================================

def get_balance() -> float:
    """Retorna el balance disponible en USDT"""
    if config.MODO_DEMO:
        return _demo_balance()

    resp = _get("/openApi/swap/v2/user/balance")
    try:
        for asset in resp.get("data", {}).get("balance", []):
            if asset.get("asset") == "USDT":
                return float(asset.get("availableMargin", 0))
    except Exception as e:
        print(f"[EXCHANGE] Error balance: {e}")
    return 0.0


def get_equity() -> float:
    """Balance total incluyendo PnL no realizado"""
    if config.MODO_DEMO:
        return _demo_balance()

    resp = _get("/openApi/swap/v2/user/balance")
    try:
        for asset in resp.get("data", {}).get("balance", []):
            if asset.get("asset") == "USDT":
                return float(asset.get("balance", 0))
    except Exception as e:
        print(f"[EXCHANGE] Error equity: {e}")
    return 0.0


# ============================================================
# PRECIO Y MERCADO
# ============================================================

def get_precio(par: str) -> float:
    resp = _get("/openApi/swap/v2/quote/price", {"symbol": par})
    try:
        return float(resp["data"]["price"])
    except:
        return 0.0


def get_klines(par: str, intervalo: str = "5m", limit: int = 100) -> list:
    """
    Retorna lista de klines: [timestamp, open, high, low, close, volume]
    intervalo: 1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d
    """
    resp = _get("/openApi/swap/v3/quote/klines", {
        "symbol": par,
        "interval": intervalo,
        "limit": limit
    })
    try:
        return resp.get("data", [])
    except:
        return []


def get_spread_pct(par: str) -> float:
    resp = _get("/openApi/swap/v2/quote/bookTicker", {"symbol": par})
    try:
        bid = float(resp["data"]["bidPrice"])
        ask = float(resp["data"]["askPrice"])
        mid = (bid + ask) / 2
        return ((ask - bid) / mid) * 100
    except:
        return 999.0


def get_volumen_24h(par: str) -> float:
    resp = _get("/openApi/swap/v2/quote/ticker", {"symbol": par})
    try:
        return float(resp["data"]["quoteVolume"])
    except:
        return 0.0


# ============================================================
# APALANCAMIENTO
# ============================================================

def set_leverage(par: str, leverage: int) -> bool:
    if config.MODO_DEMO:
        return True

    resp = _post("/openApi/swap/v2/trade/leverage", {
        "symbol": par,
        "side": "LONG",
        "leverage": leverage
    })
    ok = resp.get("code") == 0
    if config.MODO_DEBUG:
        print(f"[EXCHANGE] Leverage {par} → {leverage}x {'✓' if ok else '✗'}")
    return ok


# ============================================================
# ÓRDENES
# ============================================================

def calcular_cantidad(par: str, balance: float, precio: float) -> float:
    """
    Calcula la cantidad a comprar basándose en:
    - balance actual (compound)
    - riesgo por trade del config
    - leverage configurado
    """
    capital_riesgo = balance * config.RIESGO_POR_TRADE * config.LEVERAGE
    cantidad = capital_riesgo / precio

    # Redondear al número de decimales que acepta BingX para este par
    # (simplificado a 4 decimales — ajustar según par)
    cantidad = round(cantidad, 4)
    return max(cantidad, 0.0001)


def abrir_long(par: str, cantidad: float, precio_entrada: float,
               sl: float, tp: float) -> dict:
    """
    Abre posición LONG con SL y TP
    Retorna dict con order_id y precio de entrada
    """
    if config.MODO_DEMO:
        return _demo_orden(par, "BUY", cantidad, precio_entrada, sl, tp)

    # Orden de entrada
    resp_entrada = _post("/openApi/swap/v2/trade/order", {
        "symbol": par,
        "side": "BUY",
        "positionSide": "LONG",
        "type": "MARKET",
        "quantity": str(cantidad),
    })

    if resp_entrada.get("code") != 0:
        print(f"[EXCHANGE] Error abriendo LONG {par}: {resp_entrada}")
        return {}

    order_id = resp_entrada.get("data", {}).get("orderId", "")

    # Stop Loss
    _post("/openApi/swap/v2/trade/order", {
        "symbol": par,
        "side": "SELL",
        "positionSide": "LONG",
        "type": "STOP_MARKET",
        "quantity": str(cantidad),
        "stopPrice": str(round(sl, 6)),
        "workingType": "MARK_PRICE"
    })

    # Take Profit
    _post("/openApi/swap/v2/trade/order", {
        "symbol": par,
        "side": "SELL",
        "positionSide": "LONG",
        "type": "TAKE_PROFIT_MARKET",
        "quantity": str(cantidad),
        "stopPrice": str(round(tp, 6)),
        "workingType": "MARK_PRICE"
    })

    if config.MODO_DEBUG:
        print(f"[EXCHANGE] LONG abierto {par} | qty:{cantidad} | SL:{sl:.4f} | TP:{tp:.4f}")

    return {
        "order_id": order_id,
        "par": par,
        "lado": "LONG",
        "cantidad": cantidad,
        "precio_entrada": precio_entrada,
        "sl": sl,
        "tp": tp,
        "timestamp": datetime.now().isoformat()
    }


def cerrar_posicion(par: str, cantidad: float) -> dict:
    """Cierra una posición LONG existente a mercado"""
    if config.MODO_DEMO:
        precio_actual = get_precio_demo(par)
        return {"order_id": f"demo_close_{int(time.time())}", "precio_salida": precio_actual}

    resp = _post("/openApi/swap/v2/trade/order", {
        "symbol": par,
        "side": "SELL",
        "positionSide": "LONG",
        "type": "MARKET",
        "quantity": str(cantidad),
    })

    if resp.get("code") != 0:
        print(f"[EXCHANGE] Error cerrando {par}: {resp}")
        return {}

    # Cancelar SL/TP pendientes
    cancelar_ordenes_abiertas(par)

    return {
        "order_id": resp.get("data", {}).get("orderId", ""),
        "precio_salida": get_precio(par)
    }


def cancelar_ordenes_abiertas(par: str):
    if config.MODO_DEMO:
        return
    _post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": par})


def get_posiciones_abiertas() -> list:
    """Retorna lista de posiciones abiertas"""
    if config.MODO_DEMO:
        return _demo_posiciones()

    resp = _get("/openApi/swap/v2/user/positions")
    try:
        posiciones = resp.get("data", [])
        return [p for p in posiciones if float(p.get("positionAmt", 0)) != 0]
    except:
        return []


def get_posicion(par: str) -> dict:
    """Retorna la posición abierta para un par específico"""
    posiciones = get_posiciones_abiertas()
    for p in posiciones:
        if p.get("symbol") == par:
            return p
    return {}


# ============================================================
# MODO DEMO — simulación interna
# ============================================================

_demo_saldo = {"balance": config.BALANCE_INICIAL}
_demo_pos   = {}

def _demo_balance() -> float:
    return _demo_saldo["balance"]

def get_precio_demo(par: str) -> float:
    # En demo, devuelve precio real de mercado (solo lectura)
    return get_precio(par) or 1.0

def _demo_orden(par, lado, cantidad, precio, sl, tp) -> dict:
    _demo_pos[par] = {
        "par": par, "lado": lado, "cantidad": cantidad,
        "precio_entrada": precio, "sl": sl, "tp": tp,
        "timestamp": datetime.now().isoformat()
    }
    oid = f"demo_{int(time.time())}"
    print(f"[DEMO] {lado} {par} | qty:{cantidad} | entrada:{precio:.4f} | SL:{sl:.4f} | TP:{tp:.4f}")
    return {"order_id": oid, "par": par, "lado": lado,
            "cantidad": cantidad, "precio_entrada": precio,
            "sl": sl, "tp": tp, "timestamp": datetime.now().isoformat()}

def _demo_posiciones() -> list:
    return list(_demo_pos.values())

def demo_actualizar_balance(pnl: float):
    _demo_saldo["balance"] += pnl
    print(f"[DEMO] Balance actualizado: ${_demo_saldo['balance']:.2f} (PnL: ${pnl:+.4f})")
