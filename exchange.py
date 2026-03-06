"""
exchange.py — BingX Perpetual Futures
FIX CRÍTICO v4: usa modo ONE-WAY (por defecto en BingX)
  - ELIMINA positionSide de todas las órdenes (causa code=109400)
  - Leverage: solo symbol + leverage, sin side
  - LONG open:  side=BUY  + MARKET
  - SHORT open: side=SELL + MARKET
  - SL/TP usan closePosition=true en vez de reduceOnly
  - Cantidad: mínimo $5, redondeo inteligente por precio
"""

import hmac
import hashlib
import time
import requests
from datetime import datetime
import config

BASE_URL = "https://open-api.bingx.com"


# ─────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────

def _secret() -> bytes:
    return config.BINGX_SECRET_KEY.strip().encode("utf-8")


def _api_key() -> str:
    return config.BINGX_API_KEY.strip()


def _sign(query_string: str) -> str:
    return hmac.new(_secret(), query_string.encode("utf-8"), hashlib.sha256).hexdigest()


def _headers() -> dict:
    return {"X-BX-APIKEY": _api_key(), "Content-Type": "application/json"}


def _build_qs(params: dict) -> str:
    """timestamp debe ir AL FINAL para BingX signature"""
    p  = {k: v for k, v in params.items() if k != "timestamp"}
    ts = params.get("timestamp")
    parts = [f"{k}={v}" for k, v in p.items()]
    if ts is not None:
        parts.append(f"timestamp={ts}")
    return "&".join(parts)


def _get(path: str, params: dict = None, auth: bool = True) -> dict:
    params = params or {}
    if auth:
        params["timestamp"]  = int(time.time() * 1000)
        params["recvWindow"] = 10000
        qs  = _build_qs(params)
        sig = _sign(qs)
        url = f"{BASE_URL}{path}?{qs}&signature={sig}"
        try:
            r    = requests.get(url, headers=_headers(), timeout=10)
            data = r.json()
            if getattr(config, "MODO_DEBUG", False) and data.get("code", 0) != 0:
                print(f"[GET] {path}: code={data.get('code')} {str(data.get('msg',''))[:100]}")
            return data
        except Exception as e:
            print(f"[EXCHANGE] GET {path}: {e}")
            return {"code": -1, "data": None}
    else:
        try:
            r = requests.get(BASE_URL + path, params=params, headers=_headers(), timeout=10)
            return r.json()
        except Exception as e:
            print(f"[EXCHANGE] GET(pub) {path}: {e}")
            return {"code": -1, "data": None}


def _post(path: str, params: dict) -> dict:
    params["timestamp"]  = int(time.time() * 1000)
    params["recvWindow"] = 10000
    qs  = _build_qs(params)
    sig = _sign(qs)
    url = f"{BASE_URL}{path}?{qs}&signature={sig}"
    try:
        r    = requests.post(url, headers=_headers(), timeout=10)
        data = r.json()
        code = data.get("code", -1)
        if code != 0:
            print(f"[EXCHANGE] POST {path}: code={code} | {str(data.get('msg',''))[:120]}")
        return data
    except Exception as e:
        print(f"[EXCHANGE] POST {path}: {e}")
        return {"code": -1}


# ─────────────────────────────────────────────────────
# BALANCE
# ─────────────────────────────────────────────────────

def _buscar_float(obj, campos):
    if isinstance(obj, dict):
        for campo in campos:
            if campo in obj:
                try:
                    v = float(obj[campo])
                    if v > 0:
                        return v
                except Exception:
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
    if getattr(config, "MODO_DEMO", False):
        return _demo_balance()

    if not _api_key() or not config.BINGX_SECRET_KEY.strip():
        print("[BALANCE] ✗ API_KEY o SECRET_KEY vacías")
        return 0.0

    resp = _get("/openApi/swap/v2/user/balance", {"currency": "USDT"})
    if resp.get("code") == 100001:
        print("[BALANCE] ✗ Signature error — verifica BINGX_SECRET_KEY")
        return 0.0

    val = _buscar_float(resp, ["availableMargin", "available", "free", "balance"])
    if val > 0:
        print(f"[BALANCE] ✓ ${val:.2f}")
        return val

    resp2 = _get("/openApi/swap/v2/user/balance", {})
    val2  = _buscar_float(resp2, ["availableMargin", "available", "free", "balance"])
    if val2 > 0:
        print(f"[BALANCE] ✓ (fallback) ${val2:.2f}")
        return val2

    print(f"[BALANCE] ✗ balance=0 — respuesta: {str(resp)[:200]}")
    return 0.0


def get_equity() -> float:
    if getattr(config, "MODO_DEMO", False):
        return _demo_balance()
    resp = _get("/openApi/swap/v2/user/balance", {"currency": "USDT"})
    return _buscar_float(resp, ["balance", "equity", "totalWalletBalance"])


# ─────────────────────────────────────────────────────
# PRECIO Y MERCADO
# ─────────────────────────────────────────────────────

def get_precio(par: str) -> float:
    resp = _get("/openApi/swap/v2/quote/price", {"symbol": par}, auth=False)
    try:
        data = resp.get("data", {})
        if isinstance(data, dict):
            return float(data.get("price", 0) or 0)
        if isinstance(data, list) and data:
            return float(data[0].get("price", 0) or 0)
    except Exception as e:
        print(f"[EXCHANGE] precio {par}: {e}")
    return 0.0


def get_klines(par: str, intervalo: str = "5m", limit: int = 100) -> list:
    resp = _get("/openApi/swap/v3/quote/klines", {
        "symbol": par, "interval": intervalo, "limit": limit
    }, auth=False)
    try:
        data = resp.get("data", [])
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[EXCHANGE] klines {par}: {e}")
    return []


def get_spread_pct(par: str) -> float:
    resp = _get("/openApi/swap/v2/quote/ticker", {"symbol": par}, auth=False)
    try:
        data = resp.get("data", {})
        if isinstance(data, list) and data:
            data = data[0]
        bid = float(data.get("bidPrice", 0) or 0)
        ask = float(data.get("askPrice", 0) or 0)
        if bid > 0 and ask > 0:
            return ((ask - bid) / ((bid + ask) / 2)) * 100
        vol = float(data.get("quoteVolume", 0) or 0)
        return 0.1 if vol > 500_000 else (0.5 if vol > 0 else 999.0)
    except Exception as e:
        print(f"[EXCHANGE] spread {par}: {e}")
    return 999.0


def get_volumen_24h(par: str) -> float:
    resp = _get("/openApi/swap/v2/quote/ticker", {"symbol": par}, auth=False)
    try:
        data = resp.get("data", {})
        if isinstance(data, list) and data:
            data = data[0]
        return float(data.get("quoteVolume", 0) or 0)
    except Exception as e:
        print(f"[EXCHANGE] volumen {par}: {e}")
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
            elif isinstance(k, (list, tuple)) and len(k) >= 6:
                o, h, l, c, v = k[1], k[2], k[3], k[4], k[5]
            else:
                continue
            opens.append(float(o)); highs.append(float(h))
            lows.append(float(l));  closes.append(float(c))
            vols.append(float(v))
        except (ValueError, TypeError):
            continue
    return {"opens": opens, "highs": highs, "lows": lows, "closes": closes, "vols": vols}


# ─────────────────────────────────────────────────────
# DETECCIÓN DE MODO DE POSICIÓN (ONE-WAY vs HEDGE)
# BingX: dualSidePosition=true → HEDGE, false → ONE-WAY
# ─────────────────────────────────────────────────────

import os as _os

# Modo detectado automáticamente en el primer trade
# Se puede forzar con variable de entorno POSITION_MODE=HEDGE o POSITION_MODE=ONEWAY
_POSITION_MODE_ENV = _os.environ.get("POSITION_MODE", "").upper().strip()
_POSITION_MODE = _POSITION_MODE_ENV if _POSITION_MODE_ENV in ("HEDGE", "ONEWAY") else None
print(f"[EXCHANGE] POSITION_MODE={'auto-detect' if not _POSITION_MODE else _POSITION_MODE}")


def _enviar_orden_con_fallback(params_oneway: dict, params_hedge: dict, desc: str) -> dict:
    """
    Intenta la orden en ONE-WAY. Si falla con 109400 prueba HEDGE.
    Guarda el modo que funciona para no repetir el intento.
    """
    global _POSITION_MODE

    # Si ya sabemos el modo, usarlo directamente
    if _POSITION_MODE == "ONEWAY":
        resp = _post("/openApi/swap/v2/trade/order", params_oneway)
        if resp.get("code") != 0:
            print(f"[EXCHANGE] ✗ {desc} ONEWAY: code={resp.get('code')} {str(resp.get('msg',''))[:80]}")
        return resp

    if _POSITION_MODE == "HEDGE":
        resp = _post("/openApi/swap/v2/trade/order", params_hedge)
        if resp.get("code") != 0:
            print(f"[EXCHANGE] ✗ {desc} HEDGE: code={resp.get('code')} {str(resp.get('msg',''))[:80]}")
        return resp

    # Auto-detect: prueba ONEWAY primero
    resp = _post("/openApi/swap/v2/trade/order", params_oneway)
    if resp.get("code") == 0:
        _POSITION_MODE = "ONEWAY"
        print(f"[EXCHANGE] ✓ Modo detectado: ONEWAY")
        return resp

    if resp.get("code") == 109400:
        # Falla con parámetros inválidos → probar HEDGE
        print(f"[EXCHANGE] ONEWAY falló 109400 → probando HEDGE...")
        resp2 = _post("/openApi/swap/v2/trade/order", params_hedge)
        if resp2.get("code") == 0:
            _POSITION_MODE = "HEDGE"
            print(f"[EXCHANGE] ✓ Modo detectado: HEDGE")
            return resp2
        print(f"[EXCHANGE] ✗ HEDGE también falló: code={resp2.get('code')} {str(resp2.get('msg',''))[:80]}")
        return resp2

    return resp


def set_leverage(par: str, leverage: int) -> bool:
    if getattr(config, "MODO_DEMO", False):
        return True

    if _POSITION_MODE == "HEDGE":
        for side in ("LONG", "SHORT"):
            _post("/openApi/swap/v2/trade/leverage", {
                "symbol":   par,
                "side":     side,
                "leverage": str(leverage),
            })
    else:
        # ONE-WAY: BingX también requiere el campo side
        _post("/openApi/swap/v2/trade/leverage", {
            "symbol":   par,
            "side":     "LONG",
            "leverage": str(leverage),
        })
    return True


# ─────────────────────────────────────────────────────
# FORMATEO DE CANTIDAD Y PRECIO
# ─────────────────────────────────────────────────────

def _format_qty(cantidad: float, precio: float = 0) -> str:
    """Redondeo inteligente según precio del activo"""
    if precio > 10000:   # BTC, ETH
        qty = round(cantidad, 3)
    elif precio > 100:   # SOL, BNB
        qty = round(cantidad, 2)
    elif precio > 1:     # mayoría altcoins
        qty = round(cantidad, 1)
    elif precio > 0.01:  # XRP, DOGE
        qty = round(cantidad, 0)
        qty = max(qty, 1)
    else:                # micro-caps
        qty = round(cantidad, 0)
        qty = max(qty, 1)

    if qty == int(qty):
        return str(int(qty))
    return str(qty)


def _format_price(precio: float) -> str:
    if precio >= 10000:
        return f"{precio:.1f}"
    elif precio >= 100:
        return f"{precio:.2f}"
    elif precio >= 1:
        return f"{precio:.4f}"
    elif precio >= 0.001:
        return f"{precio:.6f}"
    else:
        return f"{precio:.8f}"


def calcular_cantidad(par: str, balance: float, precio: float) -> float:
    if balance <= 0 or precio <= 0:
        return 0.0

    capital  = balance * getattr(config, "RIESGO_POR_TRADE", 0.02) * getattr(config, "LEVERAGE", 2)
    cantidad = capital / precio

    qty_str = _format_qty(cantidad, precio)
    try:
        cantidad = float(qty_str)
    except Exception:
        return 0.0

    # BingX requiere mínimo $5 por orden
    if cantidad * precio < 5.0:
        cantidad_min = 5.2 / precio
        cantidad = float(_format_qty(cantidad_min, precio))
        if cantidad * precio > balance * getattr(config, "LEVERAGE", 2) * 1.1:
            return 0.0

    return cantidad


# ─────────────────────────────────────────────────────
# ÓRDENES — AUTO MODO (detecta ONE-WAY o HEDGE)
# ONE-WAY: sin positionSide, SL/TP con closePosition=true
# HEDGE:   con positionSide, SL/TP con quantity
# ─────────────────────────────────────────────────────

def _orden_params_long_open(qty_str: str, par: str) -> dict:
    if _POSITION_MODE == "HEDGE":
        return {"symbol": par, "side": "BUY", "positionSide": "LONG",
                "type": "MARKET", "quantity": qty_str}
    return {"symbol": par, "side": "BUY", "type": "MARKET", "quantity": qty_str}


def _orden_params_short_open(qty_str: str, par: str) -> dict:
    if _POSITION_MODE == "HEDGE":
        return {"symbol": par, "side": "SELL", "positionSide": "SHORT",
                "type": "MARKET", "quantity": qty_str}
    return {"symbol": par, "side": "SELL", "type": "MARKET", "quantity": qty_str}


def _orden_sl_long(par: str, sl_price: str, qty_str: str) -> dict:
    if _POSITION_MODE == "HEDGE":
        return {"symbol": par, "side": "SELL", "positionSide": "LONG",
                "type": "STOP_MARKET", "stopPrice": sl_price,
                "quantity": qty_str, "workingType": "MARK_PRICE"}
    return {"symbol": par, "side": "SELL", "type": "STOP_MARKET",
            "stopPrice": sl_price, "closePosition": "true", "workingType": "MARK_PRICE"}


def _orden_tp_long(par: str, tp_price: str, qty_str: str) -> dict:
    if _POSITION_MODE == "HEDGE":
        return {"symbol": par, "side": "SELL", "positionSide": "LONG",
                "type": "TAKE_PROFIT_MARKET", "stopPrice": tp_price,
                "quantity": qty_str, "workingType": "MARK_PRICE"}
    return {"symbol": par, "side": "SELL", "type": "TAKE_PROFIT_MARKET",
            "stopPrice": tp_price, "closePosition": "true", "workingType": "MARK_PRICE"}


def _orden_sl_short(par: str, sl_price: str, qty_str: str) -> dict:
    if _POSITION_MODE == "HEDGE":
        return {"symbol": par, "side": "BUY", "positionSide": "SHORT",
                "type": "STOP_MARKET", "stopPrice": sl_price,
                "quantity": qty_str, "workingType": "MARK_PRICE"}
    return {"symbol": par, "side": "BUY", "type": "STOP_MARKET",
            "stopPrice": sl_price, "closePosition": "true", "workingType": "MARK_PRICE"}


def _orden_tp_short(par: str, tp_price: str, qty_str: str) -> dict:
    if _POSITION_MODE == "HEDGE":
        return {"symbol": par, "side": "BUY", "positionSide": "SHORT",
                "type": "TAKE_PROFIT_MARKET", "stopPrice": tp_price,
                "quantity": qty_str, "workingType": "MARK_PRICE"}
    return {"symbol": par, "side": "BUY", "type": "TAKE_PROFIT_MARKET",
            "stopPrice": tp_price, "closePosition": "true", "workingType": "MARK_PRICE"}


def abrir_long(par: str, cantidad: float, precio_entrada: float,
               sl: float, tp: float) -> dict:
    if getattr(config, "MODO_DEMO", False):
        return _demo_orden(par, "LONG", cantidad, precio_entrada, sl, tp)

    set_leverage(par, getattr(config, "LEVERAGE", 2))
    qty_str = _format_qty(cantidad, precio_entrada)

    params_ow = {"symbol": par, "side": "BUY", "type": "MARKET", "quantity": qty_str}
    params_hg = {"symbol": par, "side": "BUY", "positionSide": "LONG", "type": "MARKET", "quantity": qty_str}

    resp = _enviar_orden_con_fallback(params_ow, params_hg, f"LONG {par}")

    if resp.get("code") != 0:
        err_msg = f"code={resp.get('code')} | {str(resp.get('msg', resp.get('message', 'sin mensaje')))[:120]}"
        print(f"[EXCHANGE] ✗ Error LONG {par}: {err_msg}")
        return {"error": err_msg}

    order_id = str(resp.get("data", {}).get("orderId", ""))
    print(f"[EXCHANGE] ✓ LONG {par} qty:{qty_str} modo:{_POSITION_MODE}")

    # SL y TP según modo detectado
    _post("/openApi/swap/v2/trade/order", _orden_sl_long(par, _format_price(sl), qty_str))
    _post("/openApi/swap/v2/trade/order", _orden_tp_long(par, _format_price(tp), qty_str))

    return {
        "order_id": order_id, "par": par, "lado": "LONG",
        "cantidad": cantidad, "precio_entrada": precio_entrada,
        "sl": sl, "tp": tp, "timestamp": datetime.now().isoformat()
    }


def abrir_short(par: str, cantidad: float, precio_entrada: float,
                sl: float, tp: float) -> dict:
    """Abre posición SHORT — auto detecta ONE-WAY o HEDGE"""
    if getattr(config, "MODO_DEMO", False):
        return _demo_orden(par, "SHORT", cantidad, precio_entrada, sl, tp)

    set_leverage(par, getattr(config, "LEVERAGE", 2))
    qty_str = _format_qty(cantidad, precio_entrada)

    params_ow = {"symbol": par, "side": "SELL", "type": "MARKET", "quantity": qty_str}
    params_hg = {"symbol": par, "side": "SELL", "positionSide": "SHORT", "type": "MARKET", "quantity": qty_str}

    resp = _enviar_orden_con_fallback(params_ow, params_hg, f"SHORT {par}")

    if resp.get("code") != 0:
        err_msg = f"code={resp.get('code')} | {str(resp.get('msg', resp.get('message', 'sin mensaje')))[:120]}"
        print(f"[EXCHANGE] ✗ Error SHORT {par}: {err_msg}")
        return {"error": err_msg}

    order_id = str(resp.get("data", {}).get("orderId", ""))
    print(f"[EXCHANGE] ✓ SHORT {par} qty:{qty_str} modo:{_POSITION_MODE}")

    _post("/openApi/swap/v2/trade/order",
          _orden_sl_short(par, _format_price(sl), qty_str))
    _post("/openApi/swap/v2/trade/order",
          _orden_tp_short(par, _format_price(tp), qty_str))

    return {
        "order_id": order_id, "par": par, "lado": "SHORT",
        "cantidad": cantidad, "precio_entrada": precio_entrada,
        "sl": sl, "tp": tp, "timestamp": datetime.now().isoformat()
    }


def cerrar_posicion(par: str, cantidad: float, lado: str = "LONG") -> dict:
    if getattr(config, "MODO_DEMO", False):
        return {"order_id": f"demo_close_{int(time.time())}", "precio_salida": get_precio(par)}

    qty_str = _format_qty(cantidad, get_precio(par))

    if _POSITION_MODE == "HEDGE":
        close_side    = "SELL" if lado == "LONG" else "BUY"
        position_side = lado
        resp = _post("/openApi/swap/v2/trade/order", {
            "symbol":       par,
            "side":         close_side,
            "positionSide": position_side,
            "type":         "MARKET",
            "quantity":     qty_str,
        })
    else:
        close_side = "SELL" if lado == "LONG" else "BUY"
        resp = _post("/openApi/swap/v2/trade/order", {
            "symbol":        par,
            "side":          close_side,
            "type":          "MARKET",
            "closePosition": "true",
        })

    if resp.get("code") != 0:
        print(f"[EXCHANGE] ✗ cerrar {par}: code={resp.get('code')}")
        return {}

    cancelar_ordenes_abiertas(par)
    return {
        "order_id":      str(resp.get("data", {}).get("orderId", "")),
        "precio_salida": get_precio(par)
    }


def cancelar_ordenes_abiertas(par: str):
    if getattr(config, "MODO_DEMO", False):
        return
    _post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": par})


def get_posiciones_abiertas() -> list:
    if getattr(config, "MODO_DEMO", False):
        return list(_demo_pos.values())
    resp = _get("/openApi/swap/v2/user/positions", {}, auth=True)
    try:
        posiciones = resp.get("data", []) or []
        return [p for p in posiciones if abs(float(p.get("positionAmt", 0))) > 0]
    except Exception:
        return []


def get_posicion(par: str) -> dict:
    for p in get_posiciones_abiertas():
        if p.get("symbol") == par:
            return p
    return {}


# ─────────────────────────────────────────────────────
# MODO DEMO
# ─────────────────────────────────────────────────────

_demo_state = {"balance": None}
_demo_pos   = {}


def _demo_balance() -> float:
    if _demo_state["balance"] is None:
        _demo_state["balance"] = getattr(config, "BALANCE_INICIAL", 100.0)
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
    print(f"[DEMO] {lado} {par} qty:{cantidad} e:{precio:.6f} SL:{sl:.6f} TP:{tp:.6f}")
    return _demo_pos[par].copy()
