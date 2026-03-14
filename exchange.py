"""
exchange.py — BingX Perpetual Futures API
SMC Bot v5.0 — FIX balance + debug logging

FIXES v5.5:
  FIX#SL — Verificar SL/TP real en BingX tras abrir posición
           Si falló silenciosamente, colocar manualmente antes de registrar
  FIX#RETRY — El retry sin SL/TP ahora siempre coloca SL/TP separados
"""
import hashlib
import hmac
import json
import logging
import time
from typing import Optional
from urllib.parse import urlencode

import requests

import config

log = logging.getLogger("exchange")

BASE_URL = "https://open-api.bingx.com"
_CONTRATOS_FUTURES: set = set()
_blocked_pairs: set = set()
_time_offset: int = 0
_QTY_PRECISION: dict = {}
_MIN_QTY: dict = {}


# ═══════════════════════════════════════════════════════
# FIRMA
# ═══════════════════════════════════════════════════════

def _ts() -> int:
    return int(time.time() * 1000) + _time_offset


def _praseParam(params: dict) -> str:
    """
    Construye el query string para firmar — patrón oficial BingX.
    stopLoss/takeProfit se serializan como JSON string dentro de la query.
    """
    parts = []
    for k, v in params.items():
        if isinstance(v, dict):
            parts.append(f"{k}={json.dumps(v, separators=(',', ':'))}")
        else:
            parts.append(f"{k}={v}")
    return "&".join(parts)


def _sign(query_string: str) -> str:
    """Firma HMAC-SHA256 sobre el query string completo."""
    return hmac.new(
        config.BINGX_SECRET_KEY.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _headers() -> dict:
    return {
        "X-BX-APIKEY": config.BINGX_API_KEY,
    }


def _get(path: str, params: Optional[dict] = None) -> dict:
    p = dict(params or {})
    p["timestamp"] = _ts()
    qs = _praseParam(p)
    sig = _sign(qs)
    url = f"{BASE_URL}{path}?{qs}&signature={sig}"
    try:
        r = requests.get(url, headers=_headers(), timeout=12)
        return r.json()
    except Exception as e:
        log.error(f"GET {path}: {e}")
        return {}


def _post(path: str, params: Optional[dict] = None) -> dict:
    p = dict(params or {})
    p["timestamp"] = _ts()
    qs = _praseParam(p)
    sig = _sign(qs)
    url = f"{BASE_URL}{path}?{qs}&signature={sig}"
    try:
        r = requests.post(url, headers=_headers(), timeout=15)
        return r.json()
    except Exception as e:
        log.error(f"POST {path}: {e}")
        return {}


# ═══════════════════════════════════════════════════════
# TIME SYNC
# ═══════════════════════════════════════════════════════

def sync_server_time():
    global _time_offset
    try:
        r    = requests.get(f"{BASE_URL}/openApi/swap/v2/server/time", timeout=8)
        srv  = r.json().get("data", {}).get("serverTime", 0)
        local = int(time.time() * 1000)
        _time_offset = int(srv) - local if srv else 0
        log.info(f"[TIME] offset={_time_offset}ms")
    except Exception as e:
        log.warning(f"[TIME] sync falló: {e}")


# ═══════════════════════════════════════════════════════
# CONTRATOS
# ═══════════════════════════════════════════════════════

def _cargar_contratos():
    global _QTY_PRECISION, _MIN_QTY
    try:
        data  = _get("/openApi/swap/v2/quote/contracts")
        items = data.get("data", []) or []
        for item in items:
            sym  = item.get("symbol", "")
            if not sym:
                continue
            _CONTRATOS_FUTURES.add(sym)
            prec = int(item.get("quantityPrecision", 4) or 4)
            mq   = float(item.get("minQty", 0) or 0)
            _QTY_PRECISION[sym] = prec
            _MIN_QTY[sym]       = mq
        log.info(f"[CONTRATOS] {len(_CONTRATOS_FUTURES)} futuros cargados")
    except Exception as e:
        log.warning(f"[CONTRATOS] {e}")


def par_es_soportado(par: str) -> bool:
    if par in _blocked_pairs:
        return False
    if _CONTRATOS_FUTURES and par not in _CONTRATOS_FUTURES:
        return False
    return True


def bloquear_par(par: str):
    _blocked_pairs.add(par)
    log.info(f"[BLOCKED] {par} bloqueado permanentemente")


# ═══════════════════════════════════════════════════════
# PRECIO
# ═══════════════════════════════════════════════════════

def get_precio(par: str) -> float:
    try:
        data = _get("/openApi/swap/v2/quote/price", {"symbol": par})
        p    = data.get("data", {}).get("price", 0)
        return float(p) if p else 0.0
    except Exception as e:
        log.error(f"get_precio {par}: {e}")
        return 0.0


# ═══════════════════════════════════════════════════════
# VELAS
# ═══════════════════════════════════════════════════════

def get_candles(par: str, tf: str = "5m", limit: int = 200) -> list:
    try:
        data   = _get("/openApi/swap/v3/quote/klines",
                      {"symbol": par, "interval": tf, "limit": limit})
        raw    = data.get("data", []) or []
        candles = []
        for c in raw:
            try:
                candles.append({
                    "ts":     int(c[0]),
                    "open":   float(c[1]),
                    "high":   float(c[2]),
                    "low":    float(c[3]),
                    "close":  float(c[4]),
                    "volume": float(c[5]),
                })
            except Exception:
                continue
        return candles
    except Exception as e:
        log.error(f"get_candles {par} {tf}: {e}")
        return []


# ═══════════════════════════════════════════════════════
# BALANCE
# ═══════════════════════════════════════════════════════

def _try_balance_endpoint(ep: str) -> tuple:
    try:
        data  = _get(ep)
        bal   = data.get("data", {})
        if isinstance(bal, list) and bal:
            bal = bal[0]
        if not isinstance(bal, dict):
            return -1, -1
        total = float(bal.get("balance",    bal.get("totalWalletBalance", -1)) or -1)
        avail = float(bal.get("availableMargin",
                              bal.get("availableBalance",
                              bal.get("available", total))) or total)
        if total >= 0:
            log.info(f"[BAL] {ep} total={total:.2f} disponible={avail:.2f}")
        return total, avail
    except Exception as e:
        log.debug(f"[BAL] {ep} error: {e}")
        return -1, -1


def get_balance() -> float:
    if config.MODO_DEMO:
        return 200.0

    endpoints = [
        "/openApi/swap/v2/user/balance",
        "/openApi/swap/v3/user/balance",
        "/openApi/account/v1/balance",
    ]

    for ep in endpoints:
        total, avail = _try_balance_endpoint(ep)
        if total >= 0:
            log.info(f"[BAL] ✅ Balance total: ${total:.2f} | Disponible: ${avail:.2f} desde {ep}")
            return total
    return 0.0


def get_available_margin() -> float:
    if config.MODO_DEMO:
        return 200.0

    endpoints = [
        "/openApi/swap/v2/user/balance",
        "/openApi/swap/v3/user/balance",
        "/openApi/account/v1/balance",
    ]

    for ep in endpoints:
        total, avail = _try_balance_endpoint(ep)
        if total >= 0:
            return avail if avail >= 0 else total
    return 0.0


# ═══════════════════════════════════════════════════════
# POSICIONES ABIERTAS
# ═══════════════════════════════════════════════════════

def get_posiciones_abiertas() -> list:
    if config.MODO_DEMO:
        return []
    try:
        data = _get("/openApi/swap/v2/trade/allOpenPositions")
        pos = data.get("data", []) or []
        return pos if isinstance(pos, list) else []
    except Exception as e:
        log.error(f"get_posiciones_abiertas: {e}")
        return []


# ═══════════════════════════════════════════════════════
# ÓRDENES ABIERTAS — para verificar SL/TP
# ═══════════════════════════════════════════════════════

def get_ordenes_abiertas(par: str) -> list:
    """Retorna las órdenes pendientes (SL/TP) de un par."""
    try:
        data = _get("/openApi/swap/v2/trade/openOrders", {"symbol": par})
        orders = data.get("data", {})
        if isinstance(orders, dict):
            orders = orders.get("orders", []) or []
        return orders if isinstance(orders, list) else []
    except Exception as e:
        log.debug(f"get_ordenes_abiertas {par}: {e}")
        return []


def verificar_sl_tp_presentes(par: str) -> tuple:
    """
    Verifica si hay órdenes SL y TP activas en BingX para el par.
    Retorna (tiene_sl, tiene_tp).
    """
    ordenes = get_ordenes_abiertas(par)
    tiene_sl = any(
        o.get("type", "") in ("STOP_MARKET", "STOP") for o in ordenes
    )
    tiene_tp = any(
        o.get("type", "") in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT") for o in ordenes
    )
    return tiene_sl, tiene_tp


# ═══════════════════════════════════════════════════════
# CANTIDAD Y LEVERAGE
# ═══════════════════════════════════════════════════════

_MAX_NOTIONAL: dict = {}


def calcular_cantidad(par: str, usdt: float, precio: float) -> float:
    if precio <= 0:
        return 0.0
    try:
        raw_qty = (usdt * config.LEVERAGE) / precio
        prec    = _QTY_PRECISION.get(par, 4)
        factor  = 10 ** prec
        qty     = float(int(raw_qty * factor)) / factor
        min_q   = _MIN_QTY.get(par, 0.0001)
        if qty < min_q:
            return 0.0

        max_notional = _MAX_NOTIONAL.get(par, 0)
        if max_notional > 0:
            notional = qty * precio
            if notional > max_notional * 0.90:
                qty_limitada = float(int((max_notional * 0.88 / precio) * factor)) / factor
                if qty_limitada >= min_q:
                    log.info(f"[QTY] {par} notional limitado a ${max_notional:.0f} → qty={qty_limitada}")
                    return qty_limitada
                return 0.0

        return qty
    except Exception as e:
        log.error(f"calcular_cantidad {par}: {e}")
        return 0.0


def registrar_max_notional(par: str, max_usdt: float):
    _MAX_NOTIONAL[par] = max_usdt
    log.info(f"[QTY] {par} límite notional aprendido: ${max_usdt:.0f}")


def _detectar_limite_notional(par: str, err_msg: str):
    import re
    m = re.search(r'maximum position value for this leverage is (\d+(?:\.\d+)?)\s*USDT', err_msg, re.I)
    if m:
        limite = float(m.group(1))
        registrar_max_notional(par, limite)


def _set_leverage(par: str, lado: str):
    try:
        res = _post("/openApi/swap/v2/trade/leverage", {
            "symbol":   par,
            "side":     "LONG" if lado == "LONG" else "SHORT",
            "leverage": config.LEVERAGE,
        })
        if res.get("code", -1) != 0:
            log.debug(f"leverage {par}: {res.get('msg')}")
    except Exception as e:
        log.debug(f"_set_leverage {par}: {e}")


# ═══════════════════════════════════════════════════════
# ABRIR LONG — FIX SL/TP CRÍTICO
# ═══════════════════════════════════════════════════════

def abrir_long(par: str, qty: float, precio: float, sl: float, tp: float) -> Optional[dict]:
    if config.MODO_DEMO:
        log.info(f"[DEMO] LONG {par} qty={qty:.6f} @ {precio:.6f}")
        return {"fill_price": precio, "executedQty": qty}
    try:
        _set_leverage(par, "LONG")
        params: dict = {
            "symbol": par, "side": "BUY",
            "positionSide": "LONG", "type": "MARKET", "quantity": qty,
        }
        if sl > 0:
            params["stopLoss"] = {
                "type": "STOP_MARKET", "stopPrice": round(sl, 8),
                "price": round(sl, 8), "workingType": "MARK_PRICE",
            }
        if tp > 0:
            params["takeProfit"] = {
                "type": "TAKE_PROFIT_MARKET", "stopPrice": round(tp, 8),
                "price": round(tp, 8), "workingType": "MARK_PRICE",
            }

        data = _post("/openApi/swap/v2/trade/order", params)
        sl_tp_incluidos = True

        if data.get("code", -1) != 0:
            err = data.get("msg", str(data))
            log.warning(f"abrir_long {par}: {err}")
            _detectar_limite_notional(par, err)
            # Retry SIN SL/TP — pero marcamos que hay que colocarlos después
            params.pop("stopLoss", None)
            params.pop("takeProfit", None)
            sl_tp_incluidos = False
            data = _post("/openApi/swap/v2/trade/order", params)
            if data.get("code", -1) != 0:
                err2 = data.get("msg", str(data))
                _detectar_limite_notional(par, err2)
                log.error(f"[API-ERR] {par}: {err2}")
                return {"error": err2}

        order = data.get("data", {}).get("order", {})
        fill  = float(order.get("avgPrice", 0) or order.get("price", 0) or precio)
        qty_r = float(order.get("executedQty", qty) or qty)
        log.info(f"✅ LONG {par} fill={fill:.6f} qty={qty_r}")

        # FIX CRÍTICO: Si SL/TP no se incluyeron en la orden, colocarlos ahora
        if not sl_tp_incluidos:
            log.warning(f"[SL-FIX] {par} LONG — colocando SL/TP separados (fill={fill:.6f})")
            time.sleep(1)  # Esperar a que la posición se registre en BingX
            _colocar_sl_tp_separados(par, sl, tp, "LONG", qty_r or qty)

        # FIX CRÍTICO: Verificar que SL está presente en BingX
        time.sleep(2)
        tiene_sl, tiene_tp = verificar_sl_tp_presentes(par)
        if not tiene_sl and sl > 0:
            log.warning(f"[SL-CHECK] {par} LONG — SL ausente en BingX, reintentando...")
            _colocar_sl_tp_separados(par, sl, tp, "LONG", qty_r or qty)
        elif tiene_sl:
            log.info(f"[SL-CHECK] {par} ✅ SL confirmado en BingX")

        return {"fill_price": fill or precio, "executedQty": qty_r or qty}
    except Exception as e:
        log.error(f"abrir_long {par}: {e}")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════
# ABRIR SHORT — FIX SL/TP CRÍTICO
# ═══════════════════════════════════════════════════════

def abrir_short(par: str, qty: float, precio: float, sl: float, tp: float) -> Optional[dict]:
    if config.MODO_DEMO:
        log.info(f"[DEMO] SHORT {par} qty={qty:.6f} @ {precio:.6f}")
        return {"fill_price": precio, "executedQty": qty}
    try:
        _set_leverage(par, "SHORT")
        params: dict = {
            "symbol": par, "side": "SELL",
            "positionSide": "SHORT", "type": "MARKET", "quantity": qty,
        }
        if sl > 0:
            params["stopLoss"] = {
                "type": "STOP_MARKET", "stopPrice": round(sl, 8),
                "price": round(sl, 8), "workingType": "MARK_PRICE",
            }
        if tp > 0:
            params["takeProfit"] = {
                "type": "TAKE_PROFIT_MARKET", "stopPrice": round(tp, 8),
                "price": round(tp, 8), "workingType": "MARK_PRICE",
            }

        data = _post("/openApi/swap/v2/trade/order", params)
        sl_tp_incluidos = True

        if data.get("code", -1) != 0:
            err = data.get("msg", str(data))
            log.warning(f"abrir_short {par}: {err}")
            _detectar_limite_notional(par, err)
            params.pop("stopLoss", None)
            params.pop("takeProfit", None)
            sl_tp_incluidos = False
            data = _post("/openApi/swap/v2/trade/order", params)
            if data.get("code", -1) != 0:
                return {"error": data.get("msg", str(data))}

        order = data.get("data", {}).get("order", {})
        fill  = float(order.get("avgPrice", 0) or order.get("price", 0) or precio)
        qty_r = float(order.get("executedQty", qty) or qty)
        log.info(f"✅ SHORT {par} fill={fill:.6f} qty={qty_r}")

        # FIX CRÍTICO: Si SL/TP no se incluyeron en la orden, colocarlos ahora
        if not sl_tp_incluidos:
            log.warning(f"[SL-FIX] {par} SHORT — colocando SL/TP separados (fill={fill:.6f})")
            time.sleep(1)
            _colocar_sl_tp_separados(par, sl, tp, "SHORT", qty_r or qty)

        # FIX CRÍTICO: Verificar que SL está presente en BingX
        time.sleep(2)
        tiene_sl, tiene_tp = verificar_sl_tp_presentes(par)
        if not tiene_sl and sl > 0:
            log.warning(f"[SL-CHECK] {par} SHORT — SL ausente en BingX, reintentando...")
            _colocar_sl_tp_separados(par, sl, tp, "SHORT", qty_r or qty)
        elif tiene_sl:
            log.info(f"[SL-CHECK] {par} ✅ SL confirmado en BingX")

        return {"fill_price": fill or precio, "executedQty": qty_r or qty}
    except Exception as e:
        log.error(f"abrir_short {par}: {e}")
        return {"error": str(e)}


def _colocar_sl_tp_separados(par: str, sl: float, tp: float, lado: str, qty: float):
    try:
        pos_side = "LONG" if lado == "LONG" else "SHORT"
        cl_side  = "SELL" if lado == "LONG" else "BUY"
        if sl > 0:
            res = _post("/openApi/swap/v2/trade/order", {
                "symbol": par, "side": cl_side, "positionSide": pos_side,
                "type": "STOP_MARKET", "quantity": qty,
                "stopPrice": round(sl, 8), "workingType": "MARK_PRICE", "reduceOnly": "true",
            })
            if res.get("code", -1) == 0:
                log.info(f"[SL-SEP] {par} SL={sl:.8f} colocado ✅")
            else:
                log.warning(f"[SL-SEP] {par} SL falló: {res.get('msg')}")
        if tp > 0:
            res = _post("/openApi/swap/v2/trade/order", {
                "symbol": par, "side": cl_side, "positionSide": pos_side,
                "type": "TAKE_PROFIT_MARKET", "quantity": qty,
                "stopPrice": round(tp, 8), "workingType": "MARK_PRICE", "reduceOnly": "true",
            })
            if res.get("code", -1) == 0:
                log.info(f"[TP-SEP] {par} TP={tp:.8f} colocado ✅")
            else:
                log.warning(f"[TP-SEP] {par} TP falló: {res.get('msg')}")
    except Exception as e:
        log.debug(f"_colocar_sl_tp_separados {par}: {e}")


# ═══════════════════════════════════════════════════════
# TRAILING STOP REAL
# ═══════════════════════════════════════════════════════

def actualizar_sl_bingx(par: str, nuevo_sl: float, lado: str) -> bool:
    if config.MODO_DEMO or nuevo_sl <= 0:
        return True
    try:
        pos_side = "LONG" if lado == "LONG" else "SHORT"
        params = {
            "symbol":       par,
            "positionSide": pos_side,
            "stopLoss": {
                "type":        "STOP_MARKET",
                "stopPrice":   round(nuevo_sl, 8),
                "price":       round(nuevo_sl, 8),
                "workingType": "MARK_PRICE",
            },
        }
        data = _post("/openApi/swap/v2/trade/profitloss", params)
        ok = data.get("code", -1) == 0
        if ok:
            log.debug(f"[TRAIL-BINGX] ✅ {par} {lado} SL → {nuevo_sl:.6f}")
        else:
            log.debug(f"[TRAIL-BINGX] profitloss falló ({data.get('msg')}) — cancel+replace")
            _actualizar_sl_cancel_replace(par, nuevo_sl, lado)
        return ok
    except Exception as e:
        log.debug(f"actualizar_sl_bingx {par}: {e}")
        return False


def _actualizar_sl_cancel_replace(par: str, nuevo_sl: float, lado: str):
    try:
        cl_side  = "SELL" if lado == "LONG" else "BUY"
        pos_side = "LONG" if lado == "LONG" else "SHORT"
        _post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": par})
        _post("/openApi/swap/v2/trade/order", {
            "symbol":        par,
            "side":          cl_side,
            "positionSide":  pos_side,
            "type":          "STOP_MARKET",
            "stopPrice":     round(nuevo_sl, 8),
            "workingType":   "MARK_PRICE",
            "reduceOnly":    "true",
            "closePosition": "true",
        })
    except Exception as e:
        log.debug(f"_actualizar_sl_cancel_replace {par}: {e}")


# ═══════════════════════════════════════════════════════
# CERRAR POSICIÓN
# ═══════════════════════════════════════════════════════

def cerrar_posicion(par: str, qty: float, lado: str) -> Optional[dict]:
    precio_actual = get_precio(par)
    if config.MODO_DEMO:
        return {"precio_salida": precio_actual}
    try:
        cl_side  = "SELL" if lado == "LONG" else "BUY"
        pos_side = "LONG" if lado == "LONG" else "SHORT"
        data = _post("/openApi/swap/v2/trade/order", {
            "symbol": par, "side": cl_side, "positionSide": pos_side,
            "type": "MARKET", "quantity": qty, "reduceOnly": "true",
        })
        if data.get("code", -1) != 0:
            log.warning(f"cerrar {par}: {data.get('msg')} — intentando closePosition")
            data2 = _post("/openApi/swap/v2/trade/closePosition", {
                "symbol": par, "positionSide": pos_side,
            })
            if data2.get("code", -1) == 0:
                return {"precio_salida": precio_actual}
            return {"precio_salida": precio_actual, "error": data.get("msg")}

        order = data.get("data", {}).get("order", {})
        fill  = float(order.get("avgPrice", 0) or order.get("price", 0) or precio_actual)
        return {"precio_salida": fill or precio_actual}
    except Exception as e:
        log.error(f"cerrar_posicion {par}: {e}")
        return {"precio_salida": precio_actual}


# ═══════════════════════════════════════════════════════
# DIAGNÓSTICO DE BALANCE AL ARRANQUE
# ═══════════════════════════════════════════════════════

def diagnostico_balance():
    import json as _json
    log.info("=" * 60)
    log.info("[DIAG-BAL] Iniciando diagnóstico de balance BingX...")
    endpoints = [
        "/openApi/swap/v2/user/balance",
        "/openApi/swap/v3/user/balance",
        "/openApi/account/v1/balance",
        "/openApi/swap/v2/user/margin",
    ]
    for ep in endpoints:
        try:
            data = _get(ep)
            log.info(f"[DIAG-BAL] {ep} → {_json.dumps(data)[:500]}")
        except Exception as e:
            log.info(f"[DIAG-BAL] {ep} → ERROR: {e}")
    log.info("=" * 60)
