"""
exchange.py — Conexión BingX Futures
FIXES:
  - parsear_klines soporta dicts (BingX v3) Y arrays (BingX legacy)
  - _get/_post usan sorted() para firma consistente
  - hmac.new → hmac.new corregido
"""

import hmac
import hashlib
import time
import requests
from datetime import datetime
import config

BASE_URL = "https://open-api.bingx.com"


def _sign(query_string: str) -> str:
    return hmac.new(
        config.BINGX_SECRET_KEY.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def _headers() -> dict:
    return {
        "X-BX-APIKEY": config.BINGX_API_KEY,
        "Content-Type": "application/json"
    }


def _build_qs(params: dict) -> str:
    """
    BingX requiere timestamp al final, SIN sorted().
    sorted() cambia el orden y rompe la firma HMAC.
    """
    ts = params.pop("timestamp", None)
    parts = [f"{k}={v}" for k, v in params.items()]
    if ts is not None:
        parts.append(f"timestamp={ts}")
        params["timestamp"] = ts
    return "&".join(parts)


def _get(path: str, params: dict = None, auth: bool = True) -> dict:
    params = params or {}
    if auth:
        params["timestamp"] = int(time.time() * 1000)
        query_string = _build_qs(params)
        signature    = _sign(query_string)
        url = f"{BASE_URL}{path}?{query_string}&signature={signature}"
        try:
            r    = requests.get(url, headers=_headers(), timeout=10)
            data = r.json()
            if config.MODO_DEBUG and data.get("code", 0) != 0:
                print(f"[EXCHANGE] API error {path}: code={data.get('code')} | {data.get('msg','')[:100]}")
            return data
        except Exception as e:
            print(f"[EXCHANGE] GET(auth) error {path}: {e}")
            return {"code": -1, "data": None}
    else:
        try:
            r = requests.get(BASE_URL + path, params=params,
                             headers=_headers(), timeout=10)
            return r.json()
        except Exception as e:
            print(f"[EXCHANGE] GET(pub) error {path}: {e}")
            return {"code": -1, "data": None}


def _post(path: str, params: dict) -> dict:
    params["timestamp"] = int(time.time() * 1000)
    query_string = _build_qs(params)
    signature    = _sign(query_string)
    url = f"{BASE_URL}{path}?{query_string}&signature={signature}"
    try:
        r    = requests.post(url, headers=_headers(), timeout=10)
        data = r.json()
        if config.MODO_DEBUG and data.get("code", 0) != 0:
            print(f"[EXCHANGE] POST error {path}: code={data.get('code')} | {data.get('msg','')[:100]}")
        return data
    except Exception as e:
        print(f"[EXCHANGE] POST error {path}: {e}")
        return {"code": -1}


# ============================================================
# BALANCE
# ============================================================

def _extraer_balance_usdt(resp: dict, campo: str) -> float:
    """
    Extrae el campo pedido del balance USDT.
    Maneja todos los formatos que devuelve BingX:
      - data.balance como dict         → cuenta aislada clásica
      - data.balance como lista        → multi-asset
      - data como dict directo         → margen cruzado USDT
      - data como lista de dicts       → margen cruzado multi
    """
    data = resp.get("data", {})

    # Formato 1: data es dict con subclave "balance" (dict)
    if isinstance(data, dict):
        bal = data.get("balance", None)

        if isinstance(bal, dict):
            # Intentar campo directo
            val = bal.get(campo, None)
            if val is not None:
                return float(val)
            # Fallback: availableMargin → equity, balance → equity
            for fallback in ["availableMargin", "balance", "equity", "available"]:
                v = bal.get(fallback, None)
                if v is not None:
                    return float(v)

        elif isinstance(bal, list):
            for item in bal:
                if isinstance(item, dict) and item.get("asset", "") in ("USDT", ""):
                    val = item.get(campo, item.get("availableMargin", item.get("balance", 0)))
                    return float(val)

        # Formato margen cruzado: data tiene los campos directamente
        for key in [campo, "availableMargin", "balance", "equity", "available"]:
            val = data.get(key, None)
            if val is not None:
                try:
                    return float(val)
                except:
                    continue

    # Formato 2: data es lista de cuentas
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            asset = item.get("asset", item.get("currency", ""))
            if asset in ("USDT", ""):
                val = item.get(campo, item.get("availableMargin", item.get("balance", 0)))
                return float(val)

    return 0.0


def get_balance() -> float:
    """Margen disponible para abrir posiciones nuevas"""
    if config.MODO_DEMO:
        return _demo_balance()

    resp = _get("/openApi/swap/v2/user/balance", {"currency": "USDT"}, auth=True)
    if config.MODO_DEBUG:
        print(f"[EXCHANGE] balance raw: {str(resp)[:200]}")

    val = _extraer_balance_usdt(resp, "availableMargin")
    if val > 0:
        return val

    # Segunda llamada: endpoint alternativo para margen cruzado
    resp2 = _get("/openApi/swap/v2/user/balance", {}, auth=True)
    val2 = _extraer_balance_usdt(resp2, "availableMargin")
    if val2 > 0:
        return val2

    print(f"[EXCHANGE] ⚠ balance=0 — respuesta completa: {resp}")
    return 0.0


def get_equity() -> float:
    """Balance total de la cuenta (incluyendo PnL no realizado)"""
    if config.MODO_DEMO:
        return _demo_balance()

    resp = _get("/openApi/swap/v2/user/balance", {"currency": "USDT"}, auth=True)
    val = _extraer_balance_usdt(resp, "balance")
    if val > 0:
        return val
    return _extraer_balance_usdt(resp, "equity")


# ============================================================
# PRECIO Y MERCADO (públicos)
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
        "symbol": par, "interval": intervalo, "limit": limit
    }, auth=False)
    try:
        data = resp.get("data", [])
        if isinstance(data, list):
            return data
    except Exception as e:
        print(f"[EXCHANGE] Error klines {par}: {e}")
    return []


def get_spread_pct(par: str) -> float:
    resp = _get("/openApi/swap/v2/quote/ticker", {"symbol": par}, auth=False)
    try:
        data = resp.get("data", {})
        if isinstance(data, list) and data:
            data = data[0]
        bid = float(data.get("bidPrice", 0))
        ask = float(data.get("askPrice", 0))
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
            return ((ask - bid) / mid) * 100
        vol = float(data.get("quoteVolume", 0))
        if vol > 500_000:
            return 0.1
        if vol > 0:
            return 0.5
    except Exception as e:
        print(f"[EXCHANGE] Error spread {par}: {e}")
    return 999.0


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
# PARSEAR KLINES — soporta dict Y array (BingX v2/v3)
# ============================================================

def parsear_klines(klines: list) -> dict:
    """
    BingX v3 devuelve dicts: {"open":..., "high":..., "low":..., "close":..., "volume":...}
    BingX v2/legacy devuelve arrays: [time, open, high, low, close, volume, ...]
    Esta función maneja ambos formatos.
    """
    opens  = []
    highs  = []
    lows   = []
    closes = []
    vols   = []

    for k in klines:
        try:
            if isinstance(k, dict):
                # BingX v3: claves en inglés o abreviadas
                o = k.get("open",   k.get("o", None))
                h = k.get("high",   k.get("h", None))
                l = k.get("low",    k.get("l", None))
                c = k.get("close",  k.get("c", None))
                v = k.get("volume", k.get("v", 0))
                if None in (o, h, l, c):
                    continue
                opens.append(float(o))
                highs.append(float(h))
                lows.append(float(l))
                closes.append(float(c))
                vols.append(float(v))
            elif isinstance(k, (list, tuple)) and len(k) >= 6:
                # Legacy array: [time, open, high, low, close, volume]
                opens.append(float(k[1]))
                highs.append(float(k[2]))
                lows.append(float(k[3]))
                closes.append(float(k[4]))
                vols.append(float(k[5]))
        except (ValueError, TypeError, KeyError):
            continue

    return {
        "opens":  opens,
        "highs":  highs,
        "lows":   lows,
        "closes": closes,
        "vols":   vols
    }


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
        estado = "ok" if ok else resp.get("msg", "error")
        print(f"[EXCHANGE] Leverage {par} {leverage}x → {estado}")
    return ok


# ============================================================
# CALCULAR CANTIDAD (COMPOUND)
# ============================================================

def calcular_cantidad(par: str, balance: float, precio: float) -> float:
    if balance <= 0 or precio <= 0:
        return 0.0
    capital  = balance * config.RIESGO_POR_TRADE * config.LEVERAGE
    cantidad = capital / precio
    return round(cantidad, 4) if cantidad >= 0.0001 else 0.0


# ============================================================
# ÓRDENES
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
        print(f"[EXCHANGE] LONG {par} qty:{cantidad} SL:{sl:.6f} TP:{tp:.6f}")

    return {
        "order_id": order_id, "par": par, "lado": "LONG",
        "cantidad": cantidad, "precio_entrada": precio_entrada,
        "sl": sl, "tp": tp, "timestamp": datetime.now().isoformat()
    }


def cerrar_posicion(par: str, cantidad: float) -> dict:
    if config.MODO_DEMO:
        return {
            "order_id": f"demo_close_{int(time.time())}",
            "precio_salida": get_precio(par)
        }

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
