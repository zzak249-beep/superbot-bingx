"""
exchange.py — BingX Perpetual Futures API v2.0
FIX#POST: def _post() restaurada | Multi-TF | SL/TP verificado
"""
import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import requests
import config_smc as config

log = logging.getLogger("exchange")

BASE_URL = "https://open-api.bingx.com"
_CONTRATOS_FUTURES: set = set()
_blocked_pairs: set     = set()
_time_offset: int       = 0
_QTY_PRECISION: dict    = {}
_MIN_QTY: dict          = {}
_MAX_NOTIONAL: dict     = {}

_TF_MAP = {
    "1m":"1m","3m":"3m","5m":"5m","15m":"15m","30m":"30m",
    "1h":"1h","2h":"2h","4h":"4h","6h":"6h","12h":"12h","1d":"1d","1w":"1w",
    "1":"1m","3":"3m","5":"5m","15":"15m","30":"30m",
    "60":"1h","120":"2h","240":"4h","D":"1d","W":"1w",
}

def _ts() -> int:
    return int(time.time() * 1000) + _time_offset

def _praseParam(params: dict) -> str:
    parts = []
    for k, v in params.items():
        if isinstance(v, dict):
            parts.append(f"{k}={json.dumps(v, separators=(',', ':'))}")
        else:
            parts.append(f"{k}={v}")
    return "&".join(parts)

def _sign(qs: str) -> str:
    return hmac.new(config.BINGX_SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()

def _headers() -> dict:
    return {"X-BX-APIKEY": config.BINGX_API_KEY}

def _get(path: str, params: Optional[dict] = None) -> dict:
    p = dict(params or {}); p["timestamp"] = _ts()
    qs = _praseParam(p); sig = _sign(qs)
    try:
        r = requests.get(f"{BASE_URL}{path}?{qs}&signature={sig}", headers=_headers(), timeout=12)
        return r.json()
    except Exception as e:
        log.error(f"GET {path}: {e}"); return {}

def _delete(path: str, params: Optional[dict] = None) -> dict:
    p = dict(params or {}); p["timestamp"] = _ts()
    qs = _praseParam(p); sig = _sign(qs)
    try:
        r = requests.delete(f"{BASE_URL}{path}?{qs}&signature={sig}", headers=_headers(), timeout=12)
        return r.json()
    except Exception as e:
        log.error(f"DELETE {path}: {e}"); return {}

def _post(path: str, params: Optional[dict] = None) -> dict:
    p = dict(params or {}); p["timestamp"] = _ts()
    qs = _praseParam(p); sig = _sign(qs)
    try:
        r = requests.post(f"{BASE_URL}{path}?{qs}&signature={sig}", headers=_headers(), timeout=15)
        return r.json()
    except Exception as e:
        log.error(f"POST {path}: {e}"); return {}

def sync_server_time():
    global _time_offset
    try:
        r = requests.get(f"{BASE_URL}/openApi/swap/v2/server/time", timeout=8)
        srv = r.json().get("data", {}).get("serverTime", 0)
        _time_offset = int(srv) - int(time.time() * 1000) if srv else 0
        log.info(f"[TIME] offset={_time_offset}ms")
    except Exception as e:
        log.warning(f"[TIME] sync falló: {e}")

def _cargar_contratos():
    global _QTY_PRECISION, _MIN_QTY
    try:
        items = _get("/openApi/swap/v2/quote/contracts").get("data", []) or []
        for item in items:
            sym = item.get("symbol", "")
            if not sym: continue
            _CONTRATOS_FUTURES.add(sym)
            _QTY_PRECISION[sym] = int(item.get("quantityPrecision", 4) or 4)
            _MIN_QTY[sym]       = float(item.get("minQty", 0) or 0)
        log.info(f"[CONTRATOS] {len(_CONTRATOS_FUTURES)} cargados")
    except Exception as e:
        log.warning(f"[CONTRATOS] {e}")

def par_es_soportado(par: str) -> bool:
    if par in _blocked_pairs: return False
    if _CONTRATOS_FUTURES and par not in _CONTRATOS_FUTURES: return False
    return True

def bloquear_par(par: str):
    _blocked_pairs.add(par)

def get_precio(par: str) -> float:
    try:
        p = _get("/openApi/swap/v2/quote/price", {"symbol": par}).get("data", {}).get("price", 0)
        return float(p) if p else 0.0
    except Exception as e:
        log.error(f"get_precio {par}: {e}"); return 0.0

def get_candles(par: str, tf: str = "1m", limit: int = 200) -> list:
    tf_b = _TF_MAP.get(tf, tf)
    for i in range(2):
        try:
            raw = _get("/openApi/swap/v3/quote/klines",
                       {"symbol": par, "interval": tf_b, "limit": limit}).get("data", []) or []
            candles = []
            for c in raw:
                try:
                    candles.append({"ts": int(c[0]), "open": float(c[1]),
                                    "high": float(c[2]), "low": float(c[3]),
                                    "close": float(c[4]), "volume": float(c[5])})
                except Exception: continue
            if candles: return candles
            if i == 0: time.sleep(0.5)
        except Exception as e:
            log.error(f"get_candles {par} {tf_b}: {e}")
            if i == 0: time.sleep(0.5)
    return []

def _try_balance_endpoint(ep: str) -> tuple:
    try:
        bal = _get(ep).get("data", {})
        if isinstance(bal, list) and bal: bal = bal[0]
        if not isinstance(bal, dict): return -1, -1
        total = float(bal.get("balance", bal.get("totalWalletBalance", -1)) or -1)
        avail = float(bal.get("availableMargin", bal.get("availableBalance", bal.get("available", total))) or total)
        if total >= 0: log.info(f"[BAL] {ep} total={total:.2f} disp={avail:.2f}")
        return total, avail
    except Exception as e:
        log.debug(f"[BAL] {ep}: {e}"); return -1, -1

def get_balance() -> float:
    if config.MODO_DEMO: return 200.0
    for ep in ["/openApi/swap/v2/user/balance","/openApi/swap/v3/user/balance","/openApi/account/v1/balance"]:
        total, _ = _try_balance_endpoint(ep)
        if total >= 0: return total
    return 0.0

def get_available_margin() -> float:
    if config.MODO_DEMO: return 200.0
    for ep in ["/openApi/swap/v2/user/balance","/openApi/swap/v3/user/balance","/openApi/account/v1/balance"]:
        total, avail = _try_balance_endpoint(ep)
        if total >= 0: return avail if avail >= 0 else total
    return 0.0

def get_posiciones_abiertas() -> list:
    if config.MODO_DEMO: return []
    try:
        pos = _get("/openApi/swap/v2/trade/allOpenPositions").get("data", []) or []
        return pos if isinstance(pos, list) else []
    except Exception as e:
        log.error(f"get_posiciones_abiertas: {e}"); return []

def get_ordenes_abiertas(par: str) -> list:
    try:
        orders = _get("/openApi/swap/v2/trade/openOrders", {"symbol": par}).get("data", {})
        if isinstance(orders, dict): orders = orders.get("orders", []) or []
        return orders if isinstance(orders, list) else []
    except Exception as e:
        log.debug(f"get_ordenes_abiertas {par}: {e}"); return []

def verificar_sl_tp_presentes(par: str) -> tuple:
    ords = get_ordenes_abiertas(par)
    tiene_sl = any(o.get("type","") in ("STOP_MARKET","STOP") for o in ords)
    tiene_tp = any(o.get("type","") in ("TAKE_PROFIT_MARKET","TAKE_PROFIT") for o in ords)
    return tiene_sl, tiene_tp

def calcular_cantidad(par: str, usdt: float, precio: float) -> float:
    if precio <= 0: return 0.0
    try:
        raw  = (usdt * config.LEVERAGE) / precio
        prec = _QTY_PRECISION.get(par, 4)
        f    = 10 ** prec
        qty  = float(int(raw * f)) / f
        if qty < _MIN_QTY.get(par, 0.0001): return 0.0
        mn   = _MAX_NOTIONAL.get(par, 0)
        if mn > 0 and qty * precio > mn * 0.90:
            qty = float(int((mn * 0.90 / precio) * f)) / f
        return qty
    except Exception as e:
        log.error(f"calcular_cantidad {par}: {e}"); return 0.0

def _detectar_limite_notional(par: str, msg: str):
    if "notional" in msg.lower() or "nominal" in msg.lower():
        try:
            p = get_precio(par)
            if p > 0: _MAX_NOTIONAL[par] = p * 10
        except Exception: pass

def _set_leverage(par: str, side: str = "LONG"):
    try:
        _post("/openApi/swap/v2/trade/leverage",
              {"symbol": par, "side": "LONG" if side=="LONG" else "SHORT",
               "leverage": config.LEVERAGE})
    except Exception as e:
        log.debug(f"_set_leverage {par}: {e}")

def _colocar_sl_tp_separados(par: str, sl: float, tp: float, lado: str, qty: float):
    try:
        ps = "LONG" if lado=="LONG" else "SHORT"
        cs = "SELL" if lado=="LONG" else "BUY"
        if sl > 0:
            res = _post("/openApi/swap/v2/trade/order", {
                "symbol": par, "side": cs, "positionSide": ps,
                "type": "STOP_MARKET", "quantity": qty,
                "stopPrice": round(sl,8), "workingType": "MARK_PRICE", "reduceOnly": "true"})
            log.info(f"[SL-SEP] {par} SL={'OK' if res.get('code')==0 else 'ERR'}")
        if tp > 0:
            res = _post("/openApi/swap/v2/trade/order", {
                "symbol": par, "side": cs, "positionSide": ps,
                "type": "TAKE_PROFIT_MARKET", "quantity": qty,
                "stopPrice": round(tp,8), "workingType": "MARK_PRICE", "reduceOnly": "true"})
            log.info(f"[TP-SEP] {par} TP={'OK' if res.get('code')==0 else 'ERR'}")
    except Exception as e:
        log.debug(f"_colocar_sl_tp_sep {par}: {e}")

def abrir_long(par: str, qty: float, precio: float, sl: float, tp: float) -> Optional[dict]:
    if config.MODO_DEMO:
        return {"fill_price": precio, "executedQty": qty}
    try:
        _set_leverage(par, "LONG")
        params = {"symbol": par, "side": "BUY", "positionSide": "LONG",
                  "type": "MARKET", "quantity": qty}
        if sl > 0: params["stopLoss"]   = {"type":"STOP_MARKET","stopPrice":round(sl,8),"price":round(sl,8),"workingType":"MARK_PRICE"}
        if tp > 0: params["takeProfit"] = {"type":"TAKE_PROFIT_MARKET","stopPrice":round(tp,8),"price":round(tp,8),"workingType":"MARK_PRICE"}
        data = _post("/openApi/swap/v2/trade/order", params); ok = True
        if data.get("code",-1) != 0:
            err = data.get("msg",""); log.warning(f"abrir_long {par}: {err}")
            _detectar_limite_notional(par, err)
            params.pop("stopLoss",None); params.pop("takeProfit",None); ok = False
            data = _post("/openApi/swap/v2/trade/order", params)
            if data.get("code",-1) != 0: return {"error": data.get("msg",str(data))}
        order = data.get("data",{}).get("order",{})
        fill  = float(order.get("avgPrice",0) or order.get("price",0) or precio)
        qty_r = float(order.get("executedQty", qty) or qty)
        log.info(f"✅ LONG {par} fill={fill:.6f} qty={qty_r}")
        if not ok:
            time.sleep(1); _colocar_sl_tp_separados(par, sl, tp, "LONG", qty_r)
        time.sleep(2)
        tiene_sl, _ = verificar_sl_tp_presentes(par)
        if not tiene_sl and sl > 0:
            _colocar_sl_tp_separados(par, sl, tp, "LONG", qty_r)
        return {"fill_price": fill or precio, "executedQty": qty_r}
    except Exception as e:
        log.error(f"abrir_long {par}: {e}"); return {"error": str(e)}

def abrir_short(par: str, qty: float, precio: float, sl: float, tp: float) -> Optional[dict]:
    if config.MODO_DEMO:
        return {"fill_price": precio, "executedQty": qty}
    try:
        _set_leverage(par, "SHORT")
        params = {"symbol": par, "side": "SELL", "positionSide": "SHORT",
                  "type": "MARKET", "quantity": qty}
        if sl > 0: params["stopLoss"]   = {"type":"STOP_MARKET","stopPrice":round(sl,8),"price":round(sl,8),"workingType":"MARK_PRICE"}
        if tp > 0: params["takeProfit"] = {"type":"TAKE_PROFIT_MARKET","stopPrice":round(tp,8),"price":round(tp,8),"workingType":"MARK_PRICE"}
        data = _post("/openApi/swap/v2/trade/order", params); ok = True
        if data.get("code",-1) != 0:
            err = data.get("msg",""); log.warning(f"abrir_short {par}: {err}")
            _detectar_limite_notional(par, err)
            params.pop("stopLoss",None); params.pop("takeProfit",None); ok = False
            data = _post("/openApi/swap/v2/trade/order", params)
            if data.get("code",-1) != 0: return {"error": data.get("msg",str(data))}
        order = data.get("data",{}).get("order",{})
        fill  = float(order.get("avgPrice",0) or order.get("price",0) or precio)
        qty_r = float(order.get("executedQty", qty) or qty)
        log.info(f"✅ SHORT {par} fill={fill:.6f} qty={qty_r}")
        if not ok:
            time.sleep(1); _colocar_sl_tp_separados(par, sl, tp, "SHORT", qty_r)
        time.sleep(2)
        tiene_sl, _ = verificar_sl_tp_presentes(par)
        if not tiene_sl and sl > 0:
            _colocar_sl_tp_separados(par, sl, tp, "SHORT", qty_r)
        return {"fill_price": fill or precio, "executedQty": qty_r}
    except Exception as e:
        log.error(f"abrir_short {par}: {e}"); return {"error": str(e)}

def actualizar_sl_bingx(par: str, nuevo_sl: float, lado: str) -> bool:
    if config.MODO_DEMO or nuevo_sl <= 0: return True
    try:
        ps   = "LONG" if lado=="LONG" else "SHORT"
        data = _post("/openApi/swap/v2/trade/profitloss", {
            "symbol": par, "positionSide": ps,
            "stopLoss": {"type":"STOP_MARKET","stopPrice":round(nuevo_sl,8),
                         "price":round(nuevo_sl,8),"workingType":"MARK_PRICE"}})
        ok = data.get("code",-1) == 0
        if not ok:
            cs = "SELL" if lado=="LONG" else "BUY"
            _delete("/openApi/swap/v2/trade/allOpenOrders", {"symbol": par})
            time.sleep(0.5)
            _post("/openApi/swap/v2/trade/order", {
                "symbol": par, "side": cs, "positionSide": ps,
                "type": "STOP_MARKET", "stopPrice": round(nuevo_sl,8),
                "workingType": "MARK_PRICE", "reduceOnly": "true", "closePosition": "true"})
        return ok
    except Exception as e:
        log.debug(f"actualizar_sl_bingx {par}: {e}"); return False

def cerrar_posicion(par: str, qty: float, lado: str) -> Optional[dict]:
    precio_actual = get_precio(par)
    if config.MODO_DEMO: return {"precio_salida": precio_actual}
    try:
        cs = "SELL" if lado=="LONG" else "BUY"
        ps = "LONG" if lado=="LONG" else "SHORT"
        data = _post("/openApi/swap/v2/trade/order", {
            "symbol": par, "side": cs, "positionSide": ps,
            "type": "MARKET", "quantity": qty, "reduceOnly": "true"})
        if data.get("code",-1) != 0:
            data2 = _post("/openApi/swap/v2/trade/closePosition", {"symbol": par, "positionSide": ps})
            if data2.get("code",-1) == 0: return {"precio_salida": precio_actual}
            return {"precio_salida": precio_actual, "error": data.get("msg")}
        order = data.get("data",{}).get("order",{})
        fill  = float(order.get("avgPrice",0) or order.get("price",0) or precio_actual)
        return {"precio_salida": fill or precio_actual}
    except Exception as e:
        log.error(f"cerrar_posicion {par}: {e}"); return {"precio_salida": precio_actual}

def diagnostico_balance():
    import json as _j
    log.info("=" * 60)
    for ep in ["/openApi/swap/v2/user/balance","/openApi/swap/v3/user/balance",
               "/openApi/account/v1/balance","/openApi/swap/v2/user/margin"]:
        try: log.info(f"[DIAG] {ep} → {_j.dumps(_get(ep))[:400]}")
        except Exception as e: log.info(f"[DIAG] {ep} ERROR: {e}")
    log.info("=" * 60)
