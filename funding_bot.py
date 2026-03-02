"""
FUNDING RATE ARBITRAGE BOT — BingX
Estrategia Market-Neutral: LONG Spot + SHORT Futuros

VARIABLES OBLIGATORIAS:
  BINGX_API_KEY, BINGX_API_SECRET
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

VARIABLES OPCIONALES (con defaults):
  CAPITAL_USDT      = 40
  MAX_POSITIONS     = 3
  MIN_FUNDING       = 0.03
  SCAN_INTERVAL     = 300
  DRY_RUN           = false
  CLOSE_THRESHOLD   = 0.01
"""

import os, time, hmac, hashlib, logging, requests

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("funding_bot")

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY         = os.environ["BINGX_API_KEY"]
API_SECRET      = os.environ["BINGX_API_SECRET"]
TG_TOKEN        = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT         = os.environ["TELEGRAM_CHAT_ID"]

CAPITAL_USDT    = float(os.getenv("CAPITAL_USDT",    "40"))
MAX_POSITIONS   = int(os.getenv("MAX_POSITIONS",      "3"))
MIN_FUNDING     = float(os.getenv("MIN_FUNDING",     "0.03"))
SCAN_INTERVAL   = int(os.getenv("SCAN_INTERVAL",     "300"))
DRY_RUN         = os.getenv("DRY_RUN", "false").lower() == "true"
CLOSE_THRESHOLD = float(os.getenv("CLOSE_THRESHOLD", "0.01"))

BASE_URL = "https://open-api.bingx.com"

open_positions: dict = {}


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
    r = requests.get(
        f"{BASE_URL}{path}",
        headers={"X-BX-APIKEY": API_KEY},
        params=params,
        timeout=10
    )
    r.raise_for_status()
    return r.json()

def bx_post_futures(path: str, params: dict = None) -> dict:
    """POST Futuros — firma en QUERY STRING."""
    params = params or {}
    params["timestamp"] = ts_ms()
    params["signature"] = sign(params)
    r = requests.post(
        f"{BASE_URL}{path}",
        headers={"X-BX-APIKEY": API_KEY},
        params=params,
        timeout=10
    )
    r.raise_for_status()
    return r.json()

def bx_post_spot(path: str, params: dict = None) -> dict:
    """POST Spot — firma en BODY como form data. CRITICO."""
    params = params or {}
    params["timestamp"] = ts_ms()
    params["signature"] = sign(params)
    r = requests.post(
        f"{BASE_URL}{path}",
        headers={"X-BX-APIKEY": API_KEY},
        data=params,   # <-- data= NO params=
        timeout=10
    )
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
    base  = symbol.split("-")[0]
    msg = (
        f"{emoji} <b>ACCION MANUAL REQUERIDA</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 Bot fallo — ejecutar manualmente:\n\n"
        f"<b>Par:</b> {symbol}\n"
        f"<b>Accion:</b> {action}\n"
        f"<b>Motivo:</b> {reason}\n\n"
        f"📋 <b>PASOS EN BINGX:</b>\n"
    )
    if action == "OPEN":
        msg += (
            f"1️⃣ <b>SPOT</b> → Comprar {base}\n"
            f"   Cantidad: <code>{spot_qty}</code> {base}\n"
            f"   Precio ref: ${price:.6f}\n\n"
            f"2️⃣ <b>FUTUROS</b> → Short {symbol}\n"
            f"   Cantidad: <code>{futures_qty}</code> contratos\n"
            f"   Tipo: Market, Sell/Short\n\n"
            f"📊 Funding: <b>{funding:.4f}%</b>\n"
            f"💰 Aprox: ${spot_qty * price:.2f} USDT"
        )
    else:
        msg += (
            f"1️⃣ <b>FUTUROS</b> → Cerrar Short {symbol}\n"
            f"   Cantidad: <code>{futures_qty}</code> contratos\n"
            f"   Tipo: Market, Buy/Long (cerrar)\n\n"
            f"2️⃣ <b>SPOT</b> → Vender {base}\n"
            f"   Cantidad: <code>{spot_qty}</code> {base}\n\n"
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
    """
    Transfiere USDT de Futuros Perpetuos → Spot.
    BingX transfer types:
      1 = Spot      → USD-M Futures
      2 = USD-M Futures → Spot          ← ESTE
      3 = Spot      → Coin-M Futures
      4 = Coin-M Futures → Spot
    Para Perpetual Futures (USDT-M) usar type=2
    """
    try:
        params = {
            "asset":  "USDT",
            "amount": str(round(amount, 2)),
            "type":   "2",   # ← CORREGIDO: Futuros USDT-M → Spot
        }
        result = bx_post_futures("/openApi/api/v3/asset/transfer", params)
        log.info(f"Transfer result: {result}")
        if result.get("code") == 0:
            log.info(f"✅ Transferido ${amount:.2f} Futuros→Spot")
            return True
        log.warning(f"Transfer fallida code={result.get('code')}: {result.get('msg')}")
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

    gap = needed_usdt - spot + 0.50
    fut = get_futures_balance()
    log.info(f"Futuros disponible: ${fut:.2f} | A transferir: ${gap:.2f}")

    if fut < gap:
        log.warning(f"Fondos insuficientes. Futuros=${fut:.2f}, necesito=${gap:.2f}")
        tg(f"⚠️ <b>Sin fondos suficientes</b>\n"
           f"Spot: ${spot:.2f} | Futuros: ${fut:.2f}\n"
           f"Necesario: ${needed_usdt:.2f}\n"
           f"Transfiere USDT manualmente en BingX: Activos → Transferir")
        return False

    ok = transfer_futures_to_spot(gap)
    if ok:
        time.sleep(3)  # esperar que se procese la transferencia
        new_spot = get_spot_balance()
        log.info(f"Spot post-transfer: ${new_spot:.2f}")
        return new_spot >= needed_usdt
    return False


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
    """Obtiene step_size y min_qty del par en Spot."""
    try:
        spot_sym = symbol.replace("-USDT", "-USDT")  # ya esta en formato correcto
        data = bx_get("/openApi/spot/v1/common/symbols", {"symbol": spot_sym})
        info = data.get("data", {}).get("symbols", [{}])[0]
        return {
            "step_size":    float(info.get("stepSize",    0.0001)),
            "min_qty":      float(info.get("minQty",      0.0001)),
            "min_notional": float(info.get("minNotional", 5.0))
        }
    except Exception as e:
        log.warning(f"Symbol info error {symbol}: {e}")
        return {"step_size": 0.0001, "min_qty": 0.0001, "min_notional": 5.0}

def round_qty(qty: float, step_size: float) -> float:
    if step_size <= 0:
        return round(qty, 4)
    factor = 1 / step_size
    return int(qty * factor) / factor

def set_leverage(symbol: str, leverage: int = 1):
    """Pone leverage 1x en futuros para minimo riesgo."""
    try:
        result = bx_post_futures("/openApi/swap/v2/trade/leverage", {
            "symbol":   symbol,
            "side":     "SHORT",
            "leverage": str(leverage),
        })
        log.info(f"Leverage {leverage}x set: {result.get('code')}")
    except Exception as e:
        log.warning(f"Set leverage error: {e}")

def place_spot_buy(symbol: str, qty: float) -> dict | None:
    """Compra spot a mercado — body form data."""
    try:
        result = bx_post_spot("/openApi/spot/v1/trade/order", {
            "symbol":   symbol,
            "side":     "BUY",
            "type":     "MARKET",
            "quantity": str(qty),
        })
        log.info(f"Spot BUY result: code={result.get('code')} msg={result.get('msg')}")
        if result.get("code") == 0:
            log.info(f"✅ Spot BUY {qty} {symbol}")
            return result.get("data", {})
        log.error(f"Spot BUY error code={result.get('code')}: {result.get('msg')}")
        return None
    except Exception as e:
        log.error(f"Spot BUY excepcion: {e}")
        return None

def place_futures_short(symbol: str, qty: float) -> dict | None:
    """Abre short futuros — query string."""
    try:
        # Poner leverage 1x primero
        set_leverage(symbol, 1)
        time.sleep(0.5)

        result = bx_post_futures("/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         "SELL",
            "positionSide": "SHORT",
            "type":         "MARKET",
            "quantity":     str(qty),
        })
        log.info(f"Futures SHORT result: code={result.get('code')} msg={result.get('msg')}")
        if result.get("code") == 0:
            log.info(f"✅ Futures SHORT {qty} {symbol}")
            return result.get("data", {})
        log.error(f"Futures SHORT error code={result.get('code')}: {result.get('msg')}")
        return None
    except Exception as e:
        log.error(f"Futures SHORT excepcion: {e}")
        return None

def close_spot_sell(symbol: str, qty: float) -> dict | None:
    try:
        result = bx_post_spot("/openApi/spot/v1/trade/order", {
            "symbol":   symbol,
            "side":     "SELL",
            "type":     "MARKET",
            "quantity": str(qty),
        })
        if result.get("code") == 0:
            log.info(f"✅ Spot SELL {qty} {symbol}")
            return result.get("data", {})
        log.error(f"Spot SELL error code={result.get('code')}: {result.get('msg')}")
        return None
    except Exception as e:
        log.error(f"Spot SELL excepcion: {e}")
        return None

def close_futures_short(symbol: str, qty: float) -> dict | None:
    try:
        result = bx_post_futures("/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         "BUY",
            "positionSide": "SHORT",
            "type":         "MARKET",
            "quantity":     str(qty),
            "reduceOnly":   "true",
        })
        if result.get("code") == 0:
            log.info(f"✅ Futures CLOSE SHORT {qty} {symbol}")
            return result.get("data", {})
        log.error(f"Futures CLOSE error code={result.get('code')}: {result.get('msg')}")
        return None
    except Exception as e:
        log.error(f"Futures CLOSE excepcion: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# LOGICA PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def open_arb_position(symbol: str, funding_rate: float, price: float):
    usdt_per_pos = CAPITAL_USDT / MAX_POSITIONS
    info         = get_symbol_info(symbol)
    qty          = round_qty(usdt_per_pos / price, info["step_size"])
    notional     = qty * price

    log.info(f"[OPEN] {symbol} | Funding: {funding_rate:.4f}% | "
             f"Qty: {qty} | ~${notional:.2f} | DRY_RUN={DRY_RUN}")

    if qty < info["min_qty"] or notional < info["min_notional"]:
        log.warning(f"[SKIP] {symbol}: bajo minimo. qty={qty} notional=${notional:.2f}")
        return

    if DRY_RUN:
        open_positions[symbol] = {
            "spot_qty": qty, "futures_qty": qty,
            "entry_funding": funding_rate, "entry_time": time.time(),
            "entry_price": price, "dry_run": True
        }
        tg(f"🧪 [DRY RUN] OPEN {symbol}\n"
           f"Funding: {funding_rate:.4f}% | Qty: {qty} | ~${notional:.2f}")
        return

    # ── Garantizar fondos en Spot ─────────────────────────────────────────────
    needed = notional * 1.03  # 3% extra para fees
    if not ensure_spot_balance(needed):
        reason = f"Sin fondos en Spot (necesita ${needed:.2f})"
        log.error(f"[OPEN FAIL] {symbol}: {reason}")
        tg_alert_manual(symbol, "OPEN", qty, qty, price, funding_rate, reason)
        return

    # ── PASO 1: Comprar Spot ──────────────────────────────────────────────────
    spot_result = place_spot_buy(symbol, qty)
    if not spot_result:
        tg_alert_manual(symbol, "OPEN", qty, qty, price, funding_rate,
                        "Fallo orden Spot BUY")
        return

    # ── PASO 2: Short Futuros ─────────────────────────────────────────────────
    fut_result = place_futures_short(symbol, qty)
    if not fut_result:
        log.warning(f"[REVERT] Futuros fallo — revirtiendo spot {symbol}...")
        close_spot_sell(symbol, qty)
        tg_alert_manual(symbol, "OPEN", qty, qty, price, funding_rate,
                        "Spot OK pero fallo SHORT Futuros (spot revertido)")
        return

    open_positions[symbol] = {
        "spot_qty": qty, "futures_qty": qty,
        "entry_funding": funding_rate, "entry_time": time.time(),
        "entry_price": price, "dry_run": False
    }
    tg(f"✅ <b>POSICION ABIERTA</b>\n"
       f"Par: {symbol}\n"
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

    # Cerrar futuros primero (reduce riesgo)
    fut_result  = close_futures_short(symbol, qty)
    spot_result = close_spot_sell(symbol, qty)

    if not spot_result or not fut_result:
        reason = (f"Spot: {'OK' if spot_result else 'FAIL'} | "
                  f"Futuros: {'OK' if fut_result else 'FAIL'}")
        tg_alert_manual(symbol, "CLOSE", qty, qty, price, current_funding, reason)
    else:
        held_h = (time.time() - pos["entry_time"]) / 3600
        tg(f"🔴 <b>POSICION CERRADA</b>\n"
           f"Par: {symbol}\n"
           f"Funding entrada: {pos['entry_funding']:.4f}%\n"
           f"Funding salida:  {current_funding:.4f}%\n"
           f"Tiempo: {held_h:.1f}h\n"
           f"SELL Spot ✅ | CLOSE Short ✅")

    del open_positions[symbol]


def heartbeat():
    spot_bal = get_spot_balance()
    fut_bal  = get_futures_balance()
    pos_info = ""
    for sym, pos in open_positions.items():
        pos_info += f"\n  {sym}: funding={pos['entry_funding']:.4f}%"
    log.info(f"[♥] Spot: ${spot_bal:.2f} | Futuros: ${fut_bal:.2f} | "
             f"Total: ${spot_bal + fut_bal:.2f} | "
             f"Posiciones: {len(open_positions)}/{MAX_POSITIONS} | "
             f"DRY_RUN: {DRY_RUN}")
    tg(f"💗 <b>HEARTBEAT</b>\n"
       f"💵 Spot: ${spot_bal:.2f} | Futuros: ${fut_bal:.2f}\n"
       f"📊 Posiciones: {len(open_positions)}/{MAX_POSITIONS}"
       f"{pos_info}\n"
       f"Modo: {'🧪 DRY RUN' if DRY_RUN else '🔴 LIVE'}")


def scan_and_trade():
    heartbeat()

    # ── Cerrar posiciones con funding caido ───────────────────────────────────
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
                    log.info(f"[CLOSE TRIGGER] {sym} funding={fr:.4f}% < {CLOSE_THRESHOLD}%")
                    close_arb_position(sym, fr)
        except Exception as e:
            log.error(f"Error verificando cierres: {e}")

    # ── Abrir nuevas posiciones ───────────────────────────────────────────────
    slots = MAX_POSITIONS - len(open_positions)
    if slots <= 0:
        log.info(f"[SCAN] Posiciones llenas {MAX_POSITIONS}/{MAX_POSITIONS}")
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
    log.info("🚀 Funding Rate Arbitrage Bot — BingX INICIANDO")
    log.info(f"   Capital: ${CAPITAL_USDT} | Max pos: {MAX_POSITIONS} | "
             f"Min funding: {MIN_FUNDING}% | DRY_RUN: {DRY_RUN}")
    log.info("=" * 60)

    spot_bal = get_spot_balance()
    fut_bal  = get_futures_balance()

    tg(f"🤖 <b>Bot iniciado</b>\n"
       f"Capital total: ${CAPITAL_USDT} USDT\n"
       f"Max posiciones: {MAX_POSITIONS} (${CAPITAL_USDT/MAX_POSITIONS:.2f} c/u)\n"
       f"Min funding: {MIN_FUNDING}%\n"
       f"Modo: {'🧪 DRY RUN' if DRY_RUN else '🔴 LIVE'}\n\n"
       f"💰 Balance Spot: ${spot_bal:.2f}\n"
       f"💰 Balance Futuros: ${fut_bal:.2f}")

    while True:
        try:
            scan_and_trade()
        except KeyboardInterrupt:
            log.info("Bot detenido.")
            tg("🛑 Bot detenido manualmente.")
            break
        except Exception as e:
            log.error(f"Error en ciclo principal: {e}", exc_info=True)
            tg(f"⚠️ Error en bot: {str(e)[:200]}")
        log.info(f"[SLEEP] Proximo scan en {SCAN_INTERVAL}s")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
