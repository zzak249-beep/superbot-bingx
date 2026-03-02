"""
FUNDING RATE ARBITRAGE BOT — BingX
Estrategia Market-Neutral: LONG Spot + SHORT Futuros
Con alertas manuales detalladas en Telegram cuando falla la ejecucion automatica.

VARIABLES OBLIGATORIAS:
  BINGX_API_KEY, BINGX_API_SECRET
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

VARIABLES OPCIONALES (con defaults):
  CAPITAL_USDT      = 40       # Capital total a usar
  MAX_POSITIONS     = 3        # Max posiciones simultaneas (40/3 = $13.33 c/u)
  MIN_FUNDING       = 0.03     # Funding rate minimo para abrir (0.03%)
  SCAN_INTERVAL     = 300      # Segundos entre escaneos
  DRY_RUN           = false    # Si true, solo simula sin operar
  CLOSE_THRESHOLD   = 0.005    # Funding rate para cerrar posicion
"""

import os, time, hmac, hashlib, logging, requests

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("funding_bot")

# ── Config desde variables de entorno ────────────────────────────────────────
API_KEY        = os.environ["BINGX_API_KEY"]
API_SECRET     = os.environ["BINGX_API_SECRET"]
TG_TOKEN       = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT        = os.environ["TELEGRAM_CHAT_ID"]

CAPITAL_USDT    = float(os.getenv("CAPITAL_USDT",    "40"))
MAX_POSITIONS   = int(os.getenv("MAX_POSITIONS",      "3"))
MIN_FUNDING     = float(os.getenv("MIN_FUNDING",     "0.03"))
SCAN_INTERVAL   = int(os.getenv("SCAN_INTERVAL",     "300"))
DRY_RUN         = os.getenv("DRY_RUN", "false").lower() == "true"
CLOSE_THRESHOLD = float(os.getenv("CLOSE_THRESHOLD", "0.005"))

BASE_URL = "https://open-api.bingx.com"

# Estado en memoria
open_positions: dict = {}  # symbol -> {spot_qty, futures_qty, entry_funding, entry_time, entry_price}


# ══════════════════════════════════════════════════════════════════════════════
# UTILIDADES HTTP / FIRMA
# ══════════════════════════════════════════════════════════════════════════════

def ts_ms() -> str:
    return str(int(time.time() * 1000))

def sign(params: dict) -> str:
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def bx_get(path: str, params: dict = None) -> dict:
    params = params or {}
    params["timestamp"] = ts_ms()
    params["signature"] = sign(params)
    r = requests.get(f"{BASE_URL}{path}",
                     headers={"X-BX-APIKEY": API_KEY},
                     params=params, timeout=10)
    r.raise_for_status()
    return r.json()

def bx_post(path: str, params: dict = None) -> dict:
    params = params or {}
    params["timestamp"] = ts_ms()
    params["signature"] = sign(params)
    r = requests.post(f"{BASE_URL}{path}",
                      headers={"X-BX-APIKEY": API_KEY},
                      params=params, timeout=10)
    r.raise_for_status()
    return r.json()


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════════════════════

def tg(msg: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        log.warning(f"Telegram error: {e}")

def tg_alert_manual(symbol: str, action: str, spot_qty: float,
                    futures_qty: float, price: float, funding: float, reason: str):
    emoji = "🟢" if action == "OPEN" else "🔴"
    msg = (
        f"{emoji} <b>ACCION MANUAL REQUERIDA</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 Bot fallo — ejecutar manualmente:\n\n"
        f"<b>Par:</b> {symbol}\n"
        f"<b>Accion:</b> {action}\n"
        f"<b>Motivo fallo:</b> {reason}\n\n"
        f"📋 <b>PASOS EN BINGX:</b>\n"
    )
    if action == "OPEN":
        base = symbol.split("-")[0]
        msg += (
            f"1️⃣ <b>SPOT</b> → Comprar {base}\n"
            f"   Cantidad: <code>{spot_qty}</code> {base}\n"
            f"   Precio ref: ${price:.4f}\n\n"
            f"2️⃣ <b>FUTUROS</b> → Short {symbol}\n"
            f"   Cantidad: <code>{futures_qty}</code> contratos\n"
            f"   Tipo: Market, Sell/Short\n\n"
            f"📊 Funding rate: <b>{funding:.4f}%</b>\n"
            f"💰 Capital aprox: ${spot_qty * price:.2f} USDT"
        )
    else:
        base = symbol.split("-")[0]
        msg += (
            f"1️⃣ <b>SPOT</b> → Vender {base}\n"
            f"   Cantidad: <code>{spot_qty}</code> {base}\n\n"
            f"2️⃣ <b>FUTUROS</b> → Cerrar Short {symbol}\n"
            f"   Cantidad: <code>{futures_qty}</code> contratos\n"
            f"   Tipo: Market, Buy/Long (cerrar)\n\n"
            f"📊 Funding actual: <b>{funding:.4f}%</b>"
        )
    tg(msg)


# ══════════════════════════════════════════════════════════════════════════════
# WALLETS Y TRANSFERENCIAS
# ══════════════════════════════════════════════════════════════════════════════

def get_spot_balance() -> float:
    try:
        data = bx_get("/openApi/spot/v1/account/balance")
        for asset in data.get("data", {}).get("balances", []):
            if asset["asset"] == "USDT":
                return float(asset["free"])
    except Exception as e:
        log.warning(f"Error balance spot: {e}")
    return 0.0

def get_futures_balance() -> float:
    try:
        data = bx_get("/openApi/swap/v2/user/balance")
        return float(data.get("data", {}).get("balance", {}).get("availableMargin", 0))
    except Exception as e:
        log.warning(f"Error balance futuros: {e}")
    return 0.0

def transfer_futures_to_spot(amount: float) -> bool:
    """Transfiere USDT de Futuros Perpetuos → Spot. BingX type=3."""
    try:
        params = {
            "asset":  "USDT",
            "amount": str(round(amount, 2)),
            "type":   "3",   # 3 = Futuros Perpetuos → Spot
        }
        result = bx_post("/openApi/api/v3/asset/transfer", params)
        if result.get("code") == 0:
            log.info(f"✅ Transferido ${amount:.2f} Futuros→Spot")
            return True
        log.warning(f"Transfer fallida: {result}")
        return False
    except Exception as e:
        log.warning(f"Transfer excepcion: {e}")
        return False

def ensure_spot_balance(needed_usdt: float) -> bool:
    """Verifica balance Spot; si falta, transfiere desde Futuros."""
    spot = get_spot_balance()
    log.info(f"Spot: ${spot:.2f} | Necesario: ${needed_usdt:.2f}")
    if spot >= needed_usdt:
        return True

    gap = needed_usdt - spot + 0.50   # 0.50 USDT colchon
    fut = get_futures_balance()
    log.info(f"Futuros: ${fut:.2f} | A transferir: ${gap:.2f}")

    if fut < gap:
        log.warning(f"Fondos insuficientes. Futuros: ${fut:.2f}, necesito ${gap:.2f}")
        return False

    ok = transfer_futures_to_spot(gap)
    if ok:
        time.sleep(2)   # dar tiempo a BingX para procesar
    return ok


# ══════════════════════════════════════════════════════════════════════════════
# FUNDING RATES
# ══════════════════════════════════════════════════════════════════════════════

def get_top_funding_symbols(top_n: int = 20) -> list:
    try:
        data = bx_get("/openApi/swap/v2/quote/premiumIndex")
        result = []
        for item in data.get("data", []):
            try:
                fr = float(item.get("lastFundingRate", 0)) * 100
                if fr >= MIN_FUNDING:
                    result.append({
                        "symbol":       item["symbol"],
                        "funding_rate": fr,
                        "price":        float(item.get("markPrice", 0))
                    })
            except Exception:
                continue
        result.sort(key=lambda x: x["funding_rate"], reverse=True)
        return result[:top_n]
    except Exception as e:
        log.error(f"Error funding rates: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# ORDENES
# ══════════════════════════════════════════════════════════════════════════════

def get_symbol_info(symbol: str) -> dict:
    try:
        data = bx_get("/openApi/spot/v1/common/symbols", {"symbol": symbol})
        info = data.get("data", {}).get("symbols", [{}])[0]
        return {
            "step_size":    float(info.get("stepSize",    0.0001)),
            "min_qty":      float(info.get("minQty",      0.0001)),
            "min_notional": float(info.get("minNotional", 5.0))
        }
    except Exception:
        return {"step_size": 0.0001, "min_qty": 0.0001, "min_notional": 5.0}

def round_qty(qty: float, step_size: float) -> float:
    if step_size <= 0:
        return qty
    factor = 1 / step_size
    return int(qty * factor) / factor

def place_spot_buy(symbol: str, qty: float) -> dict | None:
    try:
        result = bx_post("/openApi/spot/v1/trade/order", {
            "symbol":   symbol,
            "side":     "BUY",
            "type":     "MARKET",
            "quantity": str(qty),
        })
        if result.get("code") == 0:
            log.info(f"✅ Spot BUY {qty} {symbol}")
            return result["data"]
        log.error(f"Spot BUY error: {result}")
        return None
    except Exception as e:
        log.error(f"Spot BUY excepcion: {e}")
        return None

def place_futures_short(symbol: str, qty: float) -> dict | None:
    try:
        result = bx_post("/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         "SELL",
            "positionSide": "SHORT",
            "type":         "MARKET",
            "quantity":     str(qty),
        })
        if result.get("code") == 0:
            log.info(f"✅ Futures SHORT {qty} {symbol}")
            return result["data"]
        log.error(f"Futures SHORT error: {result}")
        return None
    except Exception as e:
        log.error(f"Futures SHORT excepcion: {e}")
        return None

def close_spot_sell(symbol: str, qty: float) -> dict | None:
    try:
        result = bx_post("/openApi/spot/v1/trade/order", {
            "symbol":   symbol,
            "side":     "SELL",
            "type":     "MARKET",
            "quantity": str(qty),
        })
        if result.get("code") == 0:
            log.info(f"✅ Spot SELL {qty} {symbol}")
            return result["data"]
        log.error(f"Spot SELL error: {result}")
        return None
    except Exception as e:
        log.error(f"Spot SELL excepcion: {e}")
        return None

def close_futures_short(symbol: str, qty: float) -> dict | None:
    try:
        result = bx_post("/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         "BUY",
            "positionSide": "SHORT",
            "type":         "MARKET",
            "quantity":     str(qty),
            "reduceOnly":   "true",
        })
        if result.get("code") == 0:
            log.info(f"✅ Futures CLOSE SHORT {qty} {symbol}")
            return result["data"]
        log.error(f"Futures CLOSE error: {result}")
        return None
    except Exception as e:
        log.error(f"Futures CLOSE excepcion: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# LOGICA PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def open_arb_position(symbol: str, funding_rate: float, price: float):
    usdt_per_pos = CAPITAL_USDT / MAX_POSITIONS
    info = get_symbol_info(symbol)

    qty      = round_qty(usdt_per_pos / price, info["step_size"])
    notional = qty * price

    log.info(f"[OPEN] {symbol} | Funding: {funding_rate:.4f}% | "
             f"Qty: {qty} | ~${notional:.2f}")

    if qty < info["min_qty"] or notional < info["min_notional"]:
        log.warning(f"[SKIP] {symbol}: bajo minimo. qty={qty} notional=${notional:.2f}")
        return

    if DRY_RUN:
        open_positions[symbol] = {
            "spot_qty": qty, "futures_qty": qty,
            "entry_funding": funding_rate, "entry_time": time.time(),
            "entry_price": price, "dry_run": True
        }
        tg(f"🧪 [DRY RUN] OPEN {symbol}\nFunding: {funding_rate:.4f}% | Qty: {qty} | ~${notional:.2f}")
        return

    # ── FIX CRITICO: garantizar fondos en Spot antes de operar ───────────────
    needed = notional * 1.02   # +2% para cubrir fees
    if not ensure_spot_balance(needed):
        reason = f"Sin fondos suficientes en Spot (necesita ${needed:.2f})"
        log.error(f"[OPEN FAIL] {symbol}: {reason}")
        tg_alert_manual(symbol, "OPEN", qty, qty, price, funding_rate, reason)
        return

    # PASO 1 — Comprar Spot
    spot_result = place_spot_buy(symbol, qty)
    if not spot_result:
        tg_alert_manual(symbol, "OPEN", qty, qty, price, funding_rate,
                        "Error al colocar orden Spot BUY")
        return

    # PASO 2 — Short Futuros
    fut_result = place_futures_short(symbol, qty)
    if not fut_result:
        log.warning(f"[REVERT] Spot OK pero Futuros fallo. Revirtiendo spot...")
        close_spot_sell(symbol, qty)
        tg_alert_manual(symbol, "OPEN", qty, qty, price, funding_rate,
                        "Spot OK pero fallo SHORT en Futuros (spot revertido)")
        return

    open_positions[symbol] = {
        "spot_qty": qty, "futures_qty": qty,
        "entry_funding": funding_rate, "entry_time": time.time(),
        "entry_price": price, "dry_run": False
    }
    tg(f"✅ POSICION ABIERTA\nPar: {symbol}\n"
       f"Funding: {funding_rate:.4f}% | Qty: {qty} | ~${notional:.2f}\n"
       f"LONG Spot ✅ | SHORT Futuros ✅")


def close_arb_position(symbol: str, current_funding: float):
    pos = open_positions.get(symbol)
    if not pos:
        return

    qty   = pos["spot_qty"]
    price = pos.get("entry_price", 0)

    if DRY_RUN or pos.get("dry_run"):
        del open_positions[symbol]
        tg(f"🧪 [DRY RUN] CLOSE {symbol}\nFunding actual: {current_funding:.4f}%")
        return

    spot_result = close_spot_sell(symbol, qty)
    fut_result  = close_futures_short(symbol, qty)

    if not spot_result or not fut_result:
        reason = (f"Spot close: {'OK' if spot_result else 'FAIL'} | "
                  f"Futures close: {'OK' if fut_result else 'FAIL'}")
        tg_alert_manual(symbol, "CLOSE", qty, qty, price, current_funding, reason)
    else:
        held_h = (time.time() - pos["entry_time"]) / 3600
        tg(f"🔴 POSICION CERRADA\nPar: {symbol}\n"
           f"Funding entrada: {pos['entry_funding']:.4f}%\n"
           f"Funding salida:  {current_funding:.4f}%\n"
           f"Tiempo: {held_h:.1f}h\n"
           f"SELL Spot ✅ | CLOSE Short ✅")

    del open_positions[symbol]


def heartbeat():
    spot_bal = get_spot_balance()
    fut_bal  = get_futures_balance()
    log.info(f"[♥] Spot: ${spot_bal:.2f} | Futuros: ${fut_bal:.2f} | "
             f"Total: ${spot_bal + fut_bal:.2f} | "
             f"Posiciones: {len(open_positions)}/{MAX_POSITIONS} | "
             f"DRY_RUN: {DRY_RUN}")


def scan_and_trade():
    heartbeat()

    # Cerrar posiciones con funding caido
    if open_positions:
        try:
            data   = bx_get("/openApi/swap/v2/quote/premiumIndex")
            fr_map = {
                item["symbol"]: float(item.get("lastFundingRate", 0)) * 100
                for item in data.get("data", [])
            }
            for sym in list(open_positions.keys()):
                fr = fr_map.get(sym, 0)
                if fr < CLOSE_THRESHOLD:
                    log.info(f"[CLOSE TRIGGER] {sym} funding {fr:.4f}% < {CLOSE_THRESHOLD}%")
                    close_arb_position(sym, fr)
        except Exception as e:
            log.error(f"Error verificando cierres: {e}")

    # Abrir nuevas posiciones
    slots = MAX_POSITIONS - len(open_positions)
    if slots <= 0:
        return

    candidates = get_top_funding_symbols(top_n=20)
    log.info(f"[SCAN] {len(candidates)} candidatos con funding >= {MIN_FUNDING}%")

    opened = 0
    for c in candidates:
        if opened >= slots:
            break
        if c["symbol"] in open_positions:
            continue
        open_arb_position(c["symbol"], c["funding_rate"], c["price"])
        opened += 1
        time.sleep(1)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    log.info("=" * 60)
    log.info("🚀 Funding Rate Arbitrage Bot — BingX")
    log.info(f"   Capital: ${CAPITAL_USDT} | Max pos: {MAX_POSITIONS} | "
             f"Min funding: {MIN_FUNDING}% | DRY_RUN: {DRY_RUN}")
    log.info("=" * 60)

    tg(f"🤖 <b>Bot iniciado</b>\n"
       f"Capital: ${CAPITAL_USDT} USDT\n"
       f"Max posiciones: {MAX_POSITIONS}\n"
       f"Min funding: {MIN_FUNDING}%\n"
       f"Modo: {'🧪 DRY RUN' if DRY_RUN else '🔴 LIVE'}")

    while True:
        try:
            scan_and_trade()
        except KeyboardInterrupt:
            log.info("Bot detenido.")
            tg("🛑 Bot detenido manualmente.")
            break
        except Exception as e:
            log.error(f"Error en ciclo principal: {e}")
            tg(f"⚠️ Error en bot: {e}")
        log.info(f"[SLEEP] Proximo scan en {SCAN_INTERVAL}s")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
