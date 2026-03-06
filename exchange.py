"""
exchange.py — Conexión BingX Futures
FIX BALANCE: prueba 3 métodos de firma hasta que uno devuelva balance > 0
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


def _get(path: str, params: dict = None, auth: bool = True) -> dict:
    params = params or {}
    if auth:
        params["timestamp"] = int(time.time() * 1000)
        query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
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
    query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
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


def _buscar_float(obj, campos):
    """Busca recursivamente el primer campo con valor float > 0"""
    if isinstance(obj, dict):
        for campo in campos:
            if campo in obj:
                try:
                    v = float(obj[campo])
                    if v > 0:
                        return v
                except:
                    pass
        for v in obj.values():
            r = _buscar_float(v, campos)
            if r > 0:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _buscar_float(item, campos)
            if r > 0:
                return r
    return 0.0


def get_balance() -> float:
    if config.MODO_DEMO:
        return _demo_balance()

    if not config.BINGX_API_KEY or not config.BINGX_SECRET_KEY:
        print("[BALANCE] ⚠ API_KEY o SECRET_KEY vacías en Railway Variables")
        return 0.0

    # Intento 1: v2 con currency
    ts1 = int(time.time() * 1000)
    qs1 = f"currency=USDT&timestamp={ts1}"
    url1 = f"{BASE_URL}/openApi/swap/v2/user/balance?{qs1}&signature={_sign(qs1)}"
    try:
        r1 = requests.get(url1, headers=_headers(), timeout=10).json()
        print(f"[BALANCE] intento1: code={r1.get('code')} data={str(r1.get('data',''))[:150]}")
        v1 = _buscar_float(r1, ["availableMargin", "available", "free"])
        if v1 > 0:
            print(f"[BALANCE] ✓ ${v1:.2f}")
            return v1
    except Exception as e:
        print(f"[BALANCE] intento1 error: {e}")

    # Intento 2: v2 sin currency, sorted
    ts2 = int(time.time() * 1000)
    qs2 = f"timestamp={ts2}"
    url2 = f"{BASE_URL}/openApi/swap/v2/user/balance?{qs2}&signature={_sign(qs2)}"
    try:
        r2 = requests.get(url2, headers=_headers(), timeout=10).json()
        print(f"[BALANCE] intento2: code={r2.get('code')} data={str(r2.get('data',''))[:150]}")
        v2 = _buscar_float(r2, ["availableMargin", "available", "free", "balance"])
        if v2 > 0:
            print(f"[BALANCE] ✓ ${v2:.2f}")
            return v2
    except Exception as e:
        print(f"[BALANCE] intento2 error: {e}")

    # Intento 3: v3
    ts3 = int(time.time() * 1000)
    qs3 = f"currency=USDT&timestamp={ts3}"
    url3 = f"{BASE_URL}/openApi/swap/v3/user/balance?{qs3}&signature={_sign(qs3)}"
    try:
        r3 = requests.get(url3, headers=_headers(), timeout=10).json()
        print(f"[BALANCE] intento3 v3: code={r3.get('code')} data={str(r3.get('data',''))[:150]}")
        v3 = _buscar_float(r3, ["availableMargin", "available", "free", "balance"])
        if v3 > 0:
            print(f"[BALANCE] ✓ ${v3:.2f}")
            return v3
    except Exception as e:
        print(f"[BALANCE] intento3 error: {e}")

    print(f"[BALANCE] ✗ TODOS LOS INTENTOS FALLARON. Respuesta1={r1 if 'r1' in dir() else 'N/A'}")
    return 0.0


def get_equity() -> float:
    if config.MODO_DEMO:
        return _demo_balance()
    ts = int(time.time() * 1000)
    qs = f"currency=USDT&timestamp={ts}"
    url = f"{BASE_URL}/openApi/swap/v2/user/balance?{qs}&signature={_sign(qs)}"
    try:
        r = requests.get(url, headers=_headers(), timeout=10).json()
        return _buscar_float(r, ["balance", "equity", "totalWalletBalance"]) or 0.0
    except:
        return 0.0


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
            return ((ask - bid) / ((bid + ask) / 2)) * 100
        vol = float(data.get("quoteVolume", 0))
        return 0.1 if vol > 500_000 else (0.5 if vol > 0 else 999.0)
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


def parsear_klines(klines: list) -> dict:
    opens = []; highs = []; lows = []; closes = []; vols = []
    for k in klines:
        try:
            if isinstance(k, dict):
                o = k.get("open",   k.get("o", None))
                h = k.get("high",   k.get("h", None))
                l = k.get("low",    k.get("l", None))
                c = k.get("close",  k.get("c", None))
                v = k.get("volume", k.get("v", 0))
                if None in (o, h, l, c):
                    continue
                opens.append(float(o)); highs.append(float(h))
                lows.append(float(l));  closes.append(float(c))
                vols.append(float(v))
            elif isinstance(k, (list, tuple)) and len(k) >= 6:
                opens.append(float(k[1])); highs.append(float(k[2]))
                lows.append(float(k[3]));  closes.append(float(k[4]))
                vols.append(float(k[5]))
        except (ValueError, TypeError, KeyError):
            continue
    return {"opens": opens, "highs": highs, "lows": lows, "closes": closes, "vols": vols}


def set_leverage(par: str, leverage: int) -> bool:
    if config.MODO_DEMO:
        return True
    resp = _post("/openApi/swap/v2/trade/leverage", {
        "symbol": par, "side": "LONG", "leverage": leverage
    })
    ok = resp.get("code") == 0
    if config.MODO_DEBUG:
        print(f"[EXCHANGE] Leverage {par} {leverage}x → {'ok' if ok else resp.get('msg','error')}")
    return ok


def calcular_cantidad(par: str, balance: float, precio: float) -> float:
    if balance <= 0 or precio <= 0:
        return 0.0
    capital  = balance * config.RIESGO_POR_TRADE * config.LEVERAGE
    cantidad = capital / precio
    return round(cantidad, 4) if cantidad >= 0.0001 else 0.0


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
