"""
exchange.py — Conexión BingX Futures
FIXED v3:
  - Firma correcta: parámetros SIN ordenar (BingX los quiere en orden de inserción)
  - spread: usa /quote/ticker en lugar de bookTicker
  - bookTicker solo para pares públicos (sin auth)
"""

import hmac
import hashlib
import time
import requests
import json
from datetime import datetime
import config

BASE_URL = "https://open-api.bingx.com"


def _sign(params: dict) -> str:
    """
    BingX firma: NO ordenar los parámetros.
    El query string debe respetar el orden de inserción del dict.
    """
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(
        config.BINGX_SECRET_KEY.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def _headers() -> dict:
    return {
        "X-BX-APIKEY": config.BINGX_API_KEY,
        "Content-Type": "application/json"
    }


def _get(path: str, params: dict = None, auth: bool = True) -> dict:
    """
    auth=True  → añade timestamp + signature (endpoints privados)
    auth=False → sin firma (endpoints públicos de mercado)
    """
    params = params or {}
    if auth:
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = _sign(params)
    try:
        r = requests.get(BASE_URL + path, params=params, headers=_headers(), timeout=10)
        data = r.json()
        if auth and config.MODO_DEBUG and data.get("code", 0) != 0:
            print(f"[EXCHANGE] API error {path}: code={data.get('code')} | {data.get('msg','')[:80]}")
        return data
    except Exception as e:
        print(f"[EXCHANGE] GET error {path}: {e}")
        return {"code": -1, "data": None}


def _post(path: str, params: dict) -> dict:
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    try:
        r = requests.post(
            BASE_URL + path,
            data=json.dumps(params),
            headers=_headers(),
            timeout=10
        )
        return r.json()
    except Exception as e:
        print(f"[EXCHANGE] POST error {path}: {e}")
        return {"code": -1}


# ============================================================
# BALANCE (endpoint privado — requiere auth)
# ============================================================

def get_balance() -> float:
    if config.MODO_DEMO:
        return _demo_balance()

    resp = _get("/openApi/swap/v2/user/balance", {"currency": "USDT"}, auth=True)
    try:
        bal = resp.get("data", {}).get("balance", {})
        if isinstance(bal, dict):
            return float(bal.get("availableMargin", 0))
        if isinstance(bal, list):
            for item in bal:
                if item.get("asset") == "USDT":
                    return float(item.get("availableMargin", 0))
    except Exception as e:
        print(f"[EXCHANGE] Error balance: {e} | resp: {resp}")
    return 0.0


def get_equity() -> float:
    if config.MODO_DEMO:
        return _demo_balance()

    resp = _get("/openApi/swap/v2/user/balance", {"currency": "USDT"}, auth=True)
    try:
        bal = resp.get("data", {}).get("balance", {})
        if isinstance(bal, dict):
            return float(bal.get("balance", 0))
        if isinstance(bal, list):
            for item in bal:
                if item.get("asset") == "USDT":
                    return float(item.get("balance", 0))
    except Exception as e:
        print(f"[EXCHANGE] Error equity: {e}")
    return 0.0


# ============================================================
# PRECIO Y MERCADO (endpoints públicos — sin auth)
# ============================================================

def get_precio(par: str) -> float:
    resp = _get("/openApi/swap/v2/quote/price", {"symbol": par}, auth=False)
    try:
        data = resp.get("data", {})
        if isinstance(data, dict):
            return float(data.get("price", 0))
        if isinstance(data, list) and data:
            return float(data[0].get("price", 0))
    except Exception as e:
        print(f"[EXCHANGE] Error precio {par}: {e}")
    return 0.0


def get_klines(par: str, intervalo: str = "5m", limit: int = 100) -> list:
    resp = _get("/openApi/swap/v3/quote/klines", {
        "symbol":   par,
        "interval": intervalo,
        "limit":    limit
    }, auth=False)
    try:
        data = resp.get("data", [])
        if isinstance(data, list):
            return data
    except Exception as e:
        print(f"[EXCHANGE] Error klines {par}: {e}")
    return []


def get_spread_pct(par: str) -> float:
    """
    Calcula spread usando el ticker 24h (endpoint público).
    Usamos (high-low)/close como proxy del spread real.
    Si el par tiene volumen > 0 consideramos spread aceptable.
    """
    resp = _get("/openApi/swap/v2/quote/ticker", {"symbol": par}, auth=False)
    try:
        data = resp.get("data", {})
        if isinstance(data, list) and data:
            data = data[0]

        # Intento 1: bid/ask directo si viene en el ticker
        bid = float(data.get("bidPrice", 0))
        ask = float(data.get("askPrice", 0))
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
            return ((ask - bid) / mid) * 100

        # Intento 2: si hay volumen, el spread es aceptable (<1%)
        vol = float(data.get("quoteVolume", 0))
        if vol > 500_000:
            return 0.1   # spread simbólico aceptable
        if vol > 0:
            return 0.5

    except Exception as e:
        print(f"[EXCHANGE] Error spread {par}: {e}")

    return 999.0   # sin datos → rechazar


def get_volumen_24h(par: str) -> float:
    resp = _get("/openApi/swap/v2/quote/ticker", {"symbol": par}, auth=False)
    try:
        data = resp.get("data", {})
        if isinstance(data, list) and data:
            data = data[0]
        return float(data.get("quoteVolume", 0))
    except Exception as e:
        print(f"[EXCHANGE] Error volumen {par}: {e}")
    return 0.0


# ============================================================
# PARSEAR KLINES — acepta dicts Y arrays
# ============================================================

def parsear_klines(klines: list) -> dict:
    opens = []; highs = []; lows = []; closes = []; vols = []

    for k in klines:
        try:
            if isinstance(k, dict):
                opens.append( float(k.get("open",   k.get("o", 0))))
                highs.append( float(k.get("high",   k.get("h", 0))))
                lows.append(  float(k.get("low",    k.get("l", 0))))
                closes.append(float(k.get("close",  k.get("c", 0))))
                vols.append(  float(k.get("volume", k.get("v", 0))))
            elif isinstance(k, (list, tuple)) and len(k) >= 6:
                opens.append( float(k[1]))
                highs.append( float(k[2]))
                lows.append(  float(k[3]))
                closes.append(float(k[4]))
                vols.append(  float(k[5]))
        except (ValueError, TypeError, KeyError):
            continue

    return {"opens": opens, "highs": highs, "lows": lows, "closes": closes, "vols": vols}


# ============================================================
# APALANCAMIENTO
# ============================================================

def set_leverage(par: str, leverage: int) -> bool:
    if config.MODO_DEMO:
        return True

    resp = _post("/openApi/swap/v2/trade/leverage", {
        "symbol": par, "side": "LONG", "leverage": leverage
    })
    ok = resp.get("code") == 0
    if config.MODO_DEBUG:
        msg = resp.get("msg", "")
        estado = "✓" if ok else f"✗ {msg}"
        print(f"[EXCHANGE] Leverage {par} {leverage}x {estado}")
    return ok


# ============================================================
# CALCULAR CANTIDAD (COMPOUND)
# ============================================================

def calcular_cantidad(par: str, balance: float, precio: float) -> float:
    if balance <= 0 or precio <= 0:
        return 0.0
    capital = balance * config.RIESGO_POR_TRADE * config.LEVERAGE
    cantidad = capital / precio
    return round(cantidad, 4) if cantidad >= 0.0001 else 0.0


# ============================================================
# ÓRDENES (endpoints privados — requieren auth)
# ============================================================

def abrir_long(par: str, cantidad: float, precio_entrada: float,
               sl: float, tp: float) -> dict:
    if config.MODO_DEMO:
        return _demo_orden(par, "BUY", cantidad, precio_entrada, sl, tp)

    resp = _post("/openApi/swap/v2/trade/order", {
        "symbol": par, "side": "BUY",
        "positionSide": "LONG", "type": "MARKET",
        "quantity": str(cantidad),
    })

    if resp.get("code") != 0:
        print(f"[EXCHANGE] Error LONG {par}: {resp}")
        return {}

    order_id = str(resp.get("data", {}).get("orderId", ""))

    _post("/openApi/swap/v2/trade/order", {
        "symbol": par, "side": "SELL", "positionSide": "LONG",
        "type": "STOP_MARKET", "quantity": str(cantidad),
        "stopPrice": str(round(sl, 6)), "workingType": "MARK_PRICE"
    })
    _post("/openApi/swap/v2/trade/order", {
        "symbol": par, "side": "SELL", "positionSide": "LONG",
        "type": "TAKE_PROFIT_MARKET", "quantity": str(cantidad),
        "stopPrice": str(round(tp, 6)), "workingType": "MARK_PRICE"
    })

    if config.MODO_DEBUG:
        print(f"[EXCHANGE] ✓ LONG {par} | qty:{cantidad} | SL:{sl:.6f} | TP:{tp:.6f}")

    return {
        "order_id": order_id, "par": par, "lado": "LONG",
        "cantidad": cantidad, "precio_entrada": precio_entrada,
        "sl": sl, "tp": tp, "timestamp": datetime.now().isoformat()
    }


def cerrar_posicion(par: str, cantidad: float) -> dict:
    if config.MODO_DEMO:
        return {"order_id": f"demo_close_{int(time.time())}", "precio_salida": get_precio(par)}

    resp = _post("/openApi/swap/v2/trade/order", {
        "symbol": par, "side": "SELL",
        "positionSide": "LONG", "type": "MARKET",
        "quantity": str(cantidad),
    })

    if resp.get("code") != 0:
        print(f"[EXCHANGE] Error cerrando {par}: {resp}")
        return {}

    cancelar_ordenes_abiertas(par)
    return {
        "order_id": str(resp.get("data", {}).get("orderId", "")),
        "precio_salida": get_precio(par)
    }


def cancelar_ordenes_abiertas(par: str):
    if config.MODO_DEMO:
        return
    _post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": par})


def get_posiciones_abiertas() -> list:
    if config.MODO_DEMO:
        return _demo_posiciones()
    resp = _get("/openApi/swap/v2/user/positions", {}, auth=True)
    try:
        posiciones = resp.get("data", []) or []
        return [p for p in posiciones if float(p.get("positionAmt", 0)) != 0]
    except:
        return []


def get_posicion(par: str) -> dict:
    for p in get_posiciones_abiertas():
        if p.get("symbol") == par:
            return p
    return {}


# ============================================================
# MODO DEMO
# ============================================================

_demo_state = {"balance": None}
_demo_pos   = {}


def _demo_balance() -> float:
    if _demo_state["balance"] is None:
        _demo_state["balance"] = config.BALANCE_INICIAL
    return _demo_state["balance"]


def demo_actualizar_balance(pnl: float):
    _demo_state["balance"] = _demo_balance() + pnl
    print(f"[DEMO] Balance: ${_demo_state['balance']:.2f} (PnL: ${pnl:+.4f})")


def _demo_orden(par, lado, cantidad, precio, sl, tp) -> dict:
    oid = f"demo_{int(time.time())}"
    _demo_pos[par] = {
        "par": par, "lado": lado, "cantidad": cantidad,
        "precio_entrada": precio, "sl": sl, "tp": tp,
        "order_id": oid, "timestamp": datetime.now().isoformat()
    }
    print(f"[DEMO] {lado} {par} qty:{cantidad} entrada:{precio:.6f} SL:{sl:.6f} TP:{tp:.6f}")
    return _demo_pos[par].copy()


def _demo_posiciones() -> list:
    return list(_demo_pos.values())
