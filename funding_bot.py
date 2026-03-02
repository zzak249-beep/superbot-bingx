"""
FUNDING RATE ARBITRAGE BOT — BingX
Estrategia Market-Neutral: LONG Spot + SHORT Futuros
Con alertas manuales detalladas en Telegram cuando falla la ejecucion automatica.

VARIABLES OBLIGATORIAS:
    BINGX_API_KEY, BINGX_API_SECRET
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

VARIABLES OPCIONALES:
    DRY_RUN            def: true
    HEDGE_MODE         def: true
    CAPITAL_USDT       def: 50.0
    MIN_FUNDING        def: 0.05
    MAX_POSITIONS      def: 3
    MIN_FUNDING_CLOSE  def: 0.01
    FUNDING_INTERVAL   def: 8
    SCAN_INTERVAL_MIN  def: 30
    MIN_VOLUME_24H     def: 1000000
"""

import os
import time
import hmac
import hashlib
import requests
import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional, Dict, List
import ccxt

API_KEY     = os.environ["BINGX_API_KEY"]
API_SECRET  = os.environ["BINGX_API_SECRET"]
TG_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT     = os.environ["TELEGRAM_CHAT_ID"]
BASE_URL    = os.environ.get("BINGX_BASE_URL", "https://open-api.bingx.com")

DRY_RUN           = os.environ.get("DRY_RUN", "true").lower() == "true"
HEDGE_MODE        = os.environ.get("HEDGE_MODE", "true").lower() == "true"
CAPITAL_USDT      = float(os.environ.get("CAPITAL_USDT", "50.0"))
MIN_FUNDING       = float(os.environ.get("MIN_FUNDING", "0.05"))
MAX_POSITIONS     = int(os.environ.get("MAX_POSITIONS", "3"))
MIN_FUNDING_CLOSE = float(os.environ.get("MIN_FUNDING_CLOSE", "0.01"))
FUNDING_INTERVAL  = int(os.environ.get("FUNDING_INTERVAL", "8"))
SCAN_INTERVAL_MIN = int(os.environ.get("SCAN_INTERVAL_MIN", "30"))
MIN_VOLUME_24H    = float(os.environ.get("MIN_VOLUME_24H", "1000000"))
MIN_APR           = (MIN_FUNDING / 100) * (8760 / FUNDING_INTERVAL) * 100

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("funding_bot")

def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def tg(msg: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"},
            timeout=10)
    except Exception as e:
        log.warning(f"Telegram: {e}")

def tg_error(msg: str):
    tg(f"🔥 <b>ERROR FUNDING BOT:</b> {msg}\n⏰ {utcnow()}")

def tg_manual_open(base, spot_sym, futures_sym, price, qty, usdt,
                   funding_pct, apr, payments_per_day, daily_est, reason=""):
    payment_est   = qty * price * (funding_pct / 100) if price > 0 and qty > 0 else 0
    position_side = "SHORT" if HEDGE_MODE else "One-Way SELL"
    tg(
        f"⚡ <b>EJECUTAR MANUALMENTE — ARB FUNDING</b>\n"
        f"══════════════════════════════\n"
        f"📌 Par: <b>{base}/USDT</b>\n"
        f"🔄 Funding: <b>{funding_pct:.4f}%</b> cada {FUNDING_INTERVAL}h\n"
        f"📊 APR estimado: <b>{apr:.1f}%</b>\n"
        f"💰 Capital: <b>${usdt:.2f} USDT</b>\n"
        f"══════════════════════════════\n"
        f"✅ <b>PASO 1 — COMPRAR EN SPOT</b>\n"
        f"  🏦 Ve a: Spot Trading\n"
        f"  📌 Par: <code>{spot_sym}</code>\n"
        f"  🔄 Tipo: Market BUY\n"
        f"  📦 Cantidad: <b>{qty} {base}</b>\n"
        f"  💲 Precio aprox: <code>{price:.6g}</code>\n"
        f"  💵 Valor: ~${usdt:.2f} USDT\n"
        f"══════════════════════════════\n"
        f"✅ <b>PASO 2 — SHORT EN FUTUROS</b>\n"
        f"  🏦 Ve a: Perpetual Futures\n"
        f"  📌 Par: <code>{futures_sym}</code>\n"
        f"  🔄 Tipo: Market SELL (abrir SHORT)\n"
        f"  📦 Cantidad: <b>{qty} {base}</b>\n"
        f"  ⚙️ Leverage: <b>1x</b>\n"
        f"  ⚙️ Position Side: <b>{position_side}</b>\n"
        f"  ⚠️ NO pongas SL ni TP\n"
        f"══════════════════════════════\n"
        f"💸 Cobro por pago (8h): <b>${payment_est:.5f}</b>\n"
        f"💸 Cobro por dia: <b>${daily_est:.5f}</b>\n"
        f"══════════════════════════════\n"
        f"🔒 CUANDO CERRAR:\n"
        f"  Cuando funding baje de {MIN_FUNDING_CLOSE:.3f}%\n"
        f"  o se vuelva negativo.\n"
        f"  Cierra SHORT futuros primero, luego vende spot.\n"
        f"══════════════════════════════\n"
        f"{'⚠️ ' + reason[:100] if reason else '🤖 Ejecucion automatica fallida'}\n"
        f"⏰ {utcnow()}"
    )

def tg_manual_step2(base, futures_sym, qty, error=""):
    tg(
        f"⚡ <b>PASO 2 MANUAL — SHORT FUTUROS {base}</b>\n"
        f"══════════════════════════════\n"
        f"ℹ️ El SPOT ya esta comprado. Solo falta el SHORT.\n"
        f"══════════════════════════════\n"
        f"  🏦 Ve a: Perpetual Futures\n"
        f"  📌 Par: <code>{futures_sym}</code>\n"
        f"  🔄 Tipo: Market SELL (abrir SHORT)\n"
        f"  📦 Cantidad: <b>{qty} {base}</b>\n"
        f"  ⚙️ Leverage: <b>1x</b>\n"
        f"  ⚙️ Position Side: <b>{'SHORT' if HEDGE_MODE else 'One-Way'}</b>\n"
        f"  ⚠️ Sin SL ni TP\n"
        f"══════════════════════════════\n"
        f"{'⚠️ Error: ' + error[:100] if error else ''}\n"
        f"⏰ {utcnow()}"
    )

def tg_manual_close(base, spot_sym, futures_sym, qty, reason=""):
    tg(
        f"🔒 <b>CERRAR MANUALMENTE — ARB {base}</b>\n"
        f"══════════════════════════════\n"
        f"📋 Razon: {reason}\n"
        f"══════════════════════════════\n"
        f"✅ <b>PASO 1 — CERRAR SHORT FUTUROS</b>\n"
        f"  🏦 Ve a: Perpetual Futures\n"
        f"  📌 Par: <code>{futures_sym}</code>\n"
        f"  🔄 Tipo: Market BUY (cerrar SHORT)\n"
        f"  📦 Cantidad: <b>{qty} {base}</b>\n"
        f"  ⚙️ Marcar: Close Position / Reduce Only\n"
        f"══════════════════════════════\n"
        f"✅ <b>PASO 2 — VENDER SPOT</b>\n"
        f"  🏦 Ve a: Spot Trading\n"
        f"  📌 Par: <code>{spot_sym}</code>\n"
        f"  🔄 Tipo: Market SELL\n"
        f"  📦 Cantidad: <b>{qty} {base}</b>\n"
        f"══════════════════════════════\n"
        f"⚠️ Cierra PRIMERO el short de futuros,\n"
        f"   despues el spot. Asi evitas riesgo.\n"
        f"⏰ {utcnow()}"
    )

def tg_urgent_sell_spot(base, spot_sym, qty, error=""):
    tg(
        f"🚨 <b>URGENTE — VENDER SPOT {base} MANUALMENTE</b>\n"
        f"══════════════════════════════\n"
        f"El short fallo y no se pudo cerrar el spot automaticamente.\n"
        f"  🏦 Ve a: Spot Trading\n"
        f"  📌 Par: <code>{spot_sym}</code>\n"
        f"  🔄 Tipo: Market SELL\n"
        f"  📦 Cantidad: <b>{qty} {base}</b>\n"
        f"══════════════════════════════\n"
        f"{'Error: ' + error[:80] if error else ''}\n"
        f"⏰ {utcnow()}"
    )

@dataclass
class ArbPosition:
    symbol:             str
    spot_symbol:        str
    futures_symbol:     str
    side:               str   = "long_spot_short_futures"
    usdt_allocated:     float = 0.0
    spot_qty:           float = 0.0
    futures_qty:        float = 0.0
    spot_entry:         float = 0.0
    futures_entry:      float = 0.0
    funding_rate_entry: float = 0.0
    funding_collected:  float = 0.0
    funding_payments:   int   = 0
    open_time:          str   = ""
    annual_rate_entry:  float = 0.0

    @property
    def funding_per_payment(self) -> float:
        return self.futures_qty * self.futures_entry * (self.funding_rate_entry / 100)

    @property
    def total_pnl(self) -> float:
        return self.funding_collected

class BotState:
    def __init__(self):
        self.positions: Dict[str, ArbPosition] = {}
        self.total_funding_earned:   float = 0.0
        self.total_positions_opened: int   = 0
        self._load()

    def _path(self):
        return "/tmp/funding_bot_state.json"

    def _load(self):
        try:
            with open(self._path()) as f:
                data = json.load(f)
                self.total_funding_earned   = data.get("total_funding_earned", 0.0)
                self.total_positions_opened = data.get("total_positions_opened", 0)
                for sym, p in data.get("positions", {}).items():
                    self.positions[sym] = ArbPosition(**p)
        except Exception:
            pass

    def save(self):
        try:
            data = {
                "total_funding_earned":   self.total_funding_earned,
                "total_positions_opened": self.total_positions_opened,
                "positions": {k: asdict(v) for k, v in self.positions.items()}
            }
            with open(self._path(), "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.warning(f"State save: {e}")

state = BotState()

def bingx_get(endpoint: str, params: dict = {}) -> Optional[dict]:
    try:
        ts    = str(int(time.time() * 1000))
        p     = {**params, "timestamp": ts}
        query = "&".join(f"{k}={v}" for k, v in sorted(p.items()))
        sig   = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
        url   = f"{BASE_URL}{endpoint}?{query}&signature={sig}"
        resp  = requests.get(url, headers={"X-BX-APIKEY": API_KEY}, timeout=10)
        data  = resp.json()
        return data.get("data") if data.get("code") == 0 else None
    except Exception as e:
        log.warning(f"bingx_get {endpoint}: {e}")
        return None

def get_funding_rate_ccxt(ex: ccxt.Exchange) -> Dict[str, float]:
    rates = {}
    try:
        fr = ex.fetch_funding_rates()
        for sym, info in fr.items():
            rate = info.get("fundingRate")
            if rate is not None:
                rates[sym] = float(rate) * 100
    except Exception as e:
        log.warning(f"funding rates: {e}")
    return rates

def get_top_opportunities(ex: ccxt.Exchange) -> List[dict]:
    log.info("Escaneando funding rates...")
    funding_rates = get_funding_rate_ccxt(ex)
    if not funding_rates:
        return []

    tickers = {}
    try:
        for sym, tk in ex.fetch_tickers().items():
            tickers[sym] = float(tk.get("quoteVolume") or 0)
    except Exception as e:
        log.warning(f"tickers: {e}")

    spot_markets = set()
    try:
        sx = ccxt.bingx({"apiKey": API_KEY, "secret": API_SECRET,
                         "options": {"defaultType": "spot"}, "enableRateLimit": True})
        sx.load_markets()
        spot_markets = set(sx.markets.keys())
    except Exception as e:
        log.warning(f"spot markets: {e}")

    opps = []
    for fsym, fpct in funding_rates.items():
        if not fsym.endswith("/USDT:USDT"):
            continue
        if fpct < MIN_FUNDING:
            continue
        if tickers.get(fsym, 0) < MIN_VOLUME_24H:
            continue
        base = fsym.split("/")[0]
        if base in state.positions:
            continue
        ssym = f"{base}/USDT"
        if spot_markets and ssym not in spot_markets:
            continue
        apr = fpct * (8760 / FUNDING_INTERVAL)
        opps.append({"base": base, "futures_symbol": fsym, "spot_symbol": ssym,
                     "funding_pct": fpct, "apr": apr,
                     "volume_24h": tickers.get(fsym, 0)})

    opps.sort(key=lambda x: x["funding_pct"], reverse=True)
    log.info(f"Oportunidades: {len(opps)} (funding>{MIN_FUNDING}%)")
    return opps

def open_arb_position(ex: ccxt.Exchange, opp: dict,
                      usdt_per_position: float) -> Optional[ArbPosition]:
    base        = opp["base"]
    futures_sym = opp["futures_symbol"]
    spot_sym    = opp["spot_symbol"]
    funding_pct = opp["funding_pct"]
    apr         = opp["apr"]
    ppd         = 24 / FUNDING_INTERVAL
    price       = 0.0
    qty         = 0.0

    try:
        price = float(ex.fetch_ticker(futures_sym)["last"])
        if price <= 0:
            return None

        qty = usdt_per_position / price
        mkt = ex.markets.get(futures_sym, {})
        min_qty = float(mkt.get("limits", {}).get("amount", {}).get("min", 0) or 0)
        if qty < min_qty:
            qty = min_qty
        qty = float(ex.amount_to_precision(futures_sym, qty))

        daily_est = qty * price * (funding_pct / 100) * ppd

        log.info(f"[ARB OPEN] {base} {funding_pct:.4f}%/8h APR:{apr:.1f}% "
                 f"${usdt_per_position:.2f} qty={qty}@{price:.4g} "
                 f"{'DRY-RUN' if DRY_RUN else 'LIVE'}")

        spot_entry    = price
        futures_entry = price

        if not DRY_RUN:
            spot_ex = None

            # PASO 1 — Comprar spot
            try:
                spot_ex = ccxt.bingx({"apiKey": API_KEY, "secret": API_SECRET,
                                      "options": {"defaultType": "spot"},
                                      "enableRateLimit": True})
                spot_ex.load_markets()
                order      = spot_ex.create_order(spot_sym, "market", "buy", qty)
                spot_entry = float(order.get("average") or price)
                log.info(f"[ARB] Spot LONG @ {spot_entry}")
            except Exception as e:
                log.error(f"[ARB] Spot open fallo: {e}")
                tg_manual_open(base, spot_sym, futures_sym, price, qty,
                               usdt_per_position, funding_pct, apr, ppd, daily_est,
                               reason=f"Spot open fallo: {str(e)[:80]}")
                return None

            # PASO 2 — Short futuros
            try:
                try:
                    lv_p = {"hedged": True} if HEDGE_MODE else {}
                    ex.set_leverage(1, futures_sym, params=lv_p)
                except Exception:
                    pass
                sp    = {"positionSide": "SHORT"} if HEDGE_MODE else {}
                order = ex.create_order(futures_sym, "market", "sell", qty, params=sp)
                futures_entry = float(order.get("average") or price)
                log.info(f"[ARB] Futures SHORT @ {futures_entry}")
            except Exception as e:
                log.error(f"[ARB] Futures short fallo: {e}")
                tg_manual_step2(base, futures_sym, qty, error=str(e)[:100])
                try:
                    spot_ex.create_order(spot_sym, "market", "sell", qty)
                    tg(f"ℹ️ Spot {base} cerrado automaticamente (short fallo).")
                except Exception as e2:
                    tg_urgent_sell_spot(base, spot_sym, qty, error=str(e2)[:80])
                return None

        pos = ArbPosition(
            symbol=base, spot_symbol=spot_sym, futures_symbol=futures_sym,
            usdt_allocated=usdt_per_position, spot_qty=qty, futures_qty=qty,
            spot_entry=spot_entry, futures_entry=futures_entry,
            funding_rate_entry=funding_pct, annual_rate_entry=apr,
            open_time=utcnow(),
        )
        state.positions[base] = pos
        state.total_positions_opened += 1
        state.save()

        daily_real = pos.funding_per_payment * ppd
        tg(
            f"💰 <b>ARB ABIERTO — {base}</b>\n"
            f"══════════════════════════════\n"
            f"📈 Spot LONG @ <code>{spot_entry:.6g}</code>\n"
            f"📉 Futures SHORT @ <code>{futures_entry:.6g}</code>\n"
            f"══════════════════════════════\n"
            f"💵 Capital: ${usdt_per_position:.2f} | 📦 Qty: {qty} {base}\n"
            f"🔄 Funding: <b>{funding_pct:.4f}%</b>/8h | APR: <b>{apr:.1f}%</b>\n"
            f"💸 Por pago (8h): <b>${pos.funding_per_payment:.5f}</b>\n"
            f"💸 Por dia: <b>${daily_real:.5f}</b>\n"
            f"══════════════════════════════\n"
            f"{'🔵 DRY-RUN' if DRY_RUN else '🟢 LIVE'} | ⏰ {utcnow()}"
        )
        return pos

    except Exception as e:
        log.error(f"open_arb_position {base}: {e}")
        try:
            daily_est = qty * price * (funding_pct / 100) * (24 / FUNDING_INTERVAL)
            tg_manual_open(base, opp["spot_symbol"], opp["futures_symbol"],
                           price, qty, usdt_per_position, funding_pct, apr,
                           24 / FUNDING_INTERVAL, daily_est, reason=str(e)[:100])
        except Exception:
            tg_error(f"ARB open {base}: {str(e)[:100]}")
        return None

def close_arb_position(ex: ccxt.Exchange, pos: ArbPosition, reason: str):
    base = pos.symbol
    log.info(f"[ARB CLOSE] {base} | {reason} | "
             f"Cobrado: ${pos.funding_collected:.5f} | "
             f"{'DRY-RUN' if DRY_RUN else 'LIVE'}")

    if not DRY_RUN:
        try:
            cp = {"reduceOnly": True}
            if HEDGE_MODE:
                cp["positionSide"] = "SHORT"
            ex.create_order(pos.futures_symbol, "market", "buy",
                            pos.futures_qty, params=cp)
            log.info(f"[ARB] Futures SHORT cerrado")
        except Exception as e:
            log.error(f"[ARB] Futures close error: {e}")
            tg_manual_close(base, pos.spot_symbol, pos.futures_symbol,
                            pos.futures_qty,
                            reason=f"Cierre automatico fallo: {str(e)[:80]}")

        try:
            sx = ccxt.bingx({"apiKey": API_KEY, "secret": API_SECRET,
                              "options": {"defaultType": "spot"}, "enableRateLimit": True})
            sx.load_markets()
            sx.create_order(pos.spot_symbol, "market", "sell", pos.spot_qty)
            log.info(f"[ARB] Spot vendido")
        except Exception as e:
            log.error(f"[ARB] Spot close error: {e}")
            tg_urgent_sell_spot(base, pos.spot_symbol, pos.spot_qty, error=str(e)[:80])

    state.total_funding_earned += pos.funding_collected
    del state.positions[base]
    state.save()

    tg(
        f"🔒 <b>ARB CERRADO — {base}</b>\n"
        f"══════════════════════════════\n"
        f"📋 Razon: {reason}\n"
        f"💰 Funding cobrado: <b>${pos.funding_collected:.5f}</b>\n"
        f"🔄 Pagos: {pos.funding_payments} | APR: {pos.annual_rate_entry:.1f}%\n"
        f"🏦 Total acumulado: ${state.total_funding_earned:.5f}\n"
        f"{'🔵 DRY-RUN' if DRY_RUN else '🟢 LIVE'} | ⏰ {utcnow()}"
    )

def record_funding_payment(ex: ccxt.Exchange):
    if not state.positions:
        return
    log.info("Registrando pagos de funding...")
    for base, pos in state.positions.items():
        if DRY_RUN:
            payment = pos.funding_per_payment
            pos.funding_collected += payment
            pos.funding_payments  += 1
            log.info(f"[DRY] {base} +${payment:.5f} total=${pos.funding_collected:.5f}")
        else:
            try:
                income = bingx_get("/openApi/swap/v2/user/income",
                                   {"symbol": pos.futures_symbol,
                                    "incomeType": "FUNDING_FEE", "limit": "5"})
                if income:
                    for entry in income:
                        amount = float(entry.get("income", 0))
                        if amount > 0:
                            pos.funding_collected += amount
                            pos.funding_payments  += 1
                            log.info(f"[LIVE] {base} +${amount:.5f}")
                            break
            except Exception as e:
                log.warning(f"Income {base}: {e}")
    state.save()

    if state.positions:
        lines = ["💸 <b>FUNDING COBRADO</b>\n══════════════════════════════"]
        total = 0.0
        for base, pos in state.positions.items():
            p = pos.funding_per_payment
            total += p
            lines.append(f"  {base}: ${p:.5f} (acum: ${pos.funding_collected:.5f})")
        lines += ["══════════════════════════════",
                  f"💰 Este pago: ${total:.5f}",
                  f"🏦 Total acumulado: ${state.total_funding_earned:.5f}",
                  f"⏰ {utcnow()}"]
        tg("\n".join(lines))

def manage_positions(ex: ccxt.Exchange):
    if not state.positions:
        return
    rates = get_funding_rate_ccxt(ex)
    for base in list(state.positions.keys()):
        pos  = state.positions[base]
        rate = rates.get(pos.futures_symbol)
        if rate is None:
            continue
        log.info(f"[MANAGE] {base} entrada:{pos.funding_rate_entry:.4f}% actual:{rate:.4f}%")
        if rate < MIN_FUNDING_CLOSE:
            reason = (f"Funding NEGATIVO: {rate:.4f}%" if rate < 0
                      else f"Funding {rate:.4f}% < minimo {MIN_FUNDING_CLOSE:.4f}%")
            close_arb_position(ex, pos, reason)

def send_heartbeat(ex: ccxt.Exchange):
    try:
        bal = float(ex.fetch_balance()["USDT"]["free"])
    except Exception:
        bal = 0.0
    ppd = 24 / FUNDING_INTERVAL
    if not state.positions:
        pos_text  = "(ninguna)"
        daily_est = 0.0
    else:
        lines = []
        daily_est = 0.0
        for base, pos in state.positions.items():
            d = pos.funding_per_payment * ppd
            daily_est += d
            lines.append(f"  {base}: {pos.funding_rate_entry:.4f}%/8h "
                         f"→ ${d:.5f}/dia (cobrado: ${pos.funding_collected:.5f})")
        pos_text = "\n".join(lines)
    tg(
        f"💗 <b>HEARTBEAT — FUNDING BOT</b>\n"
        f"══════════════════════════════\n"
        f"💵 Balance: ${bal:.2f}\n"
        f"📊 Posiciones: {len(state.positions)}/{MAX_POSITIONS}\n"
        f"{pos_text}\n"
        f"══════════════════════════════\n"
        f"💰 Est. dia: ${daily_est:.5f}\n"
        f"🏦 Acumulado: ${state.total_funding_earned:.5f}\n"
        f"📈 Total abiertos: {state.total_positions_opened}\n"
        f"{'🔵 DRY-RUN' if DRY_RUN else '🟢 LIVE'} | ⏰ {utcnow()}"
    )

def main():
    log.info("=== FUNDING RATE BOT INICIANDO ===")
    ex = ccxt.bingx({"apiKey": API_KEY, "secret": API_SECRET,
                     "options": {"defaultType": "swap"}, "enableRateLimit": True})
    ex.load_markets()

    try:
        balance = float(ex.fetch_balance()["USDT"]["free"])
    except Exception:
        balance = 0.0

    ppd           = 24 / FUNDING_INTERVAL
    daily_est_all = sum(p.funding_per_payment * ppd for p in state.positions.values())

    tg(
        f"🚀 <b>FUNDING RATE BOT INICIADO</b>\n"
        f"══════════════════════════════\n"
        f"⚙️ Modo: {'🔵 DRY-RUN' if DRY_RUN else '🟢 LIVE'}\n"
        f"💵 Capital: ${CAPITAL_USDT:.2f} USDT\n"
        f"══════════════════════════════\n"
        f"  🔑 Funding minimo: {MIN_FUNDING:.4f}%/8h\n"
        f"  📈 APR minimo: ~{MIN_APR:.1f}%\n"
        f"  🔒 Cerrar si rate < {MIN_FUNDING_CLOSE:.4f}%\n"
        f"  📦 Max posiciones: {MAX_POSITIONS}\n"
        f"  🔄 Scan cada: {SCAN_INTERVAL_MIN} min\n"
        f"══════════════════════════════\n"
        f"💰 Balance: ${balance:.2f}\n"
        f"📊 Posiciones activas: {len(state.positions)}\n"
        f"💸 Est. dia: ${daily_est_all:.5f}\n"
        f"⏰ {utcnow()}"
    )

    last_scan    = 0
    last_hb      = 0
    last_funding = 0

    while True:
        now = time.time()
        dt  = datetime.now(timezone.utc)
        mid = dt.hour * 60 + dt.minute

        for fh in [0, 8, 16]:
            fm = fh * 60
            if fm + 1 <= mid <= fm + 6 and now - last_funding > 3600:
                record_funding_payment(ex)
                last_funding = now
                break

        manage_positions(ex)

        if now - last_scan > SCAN_INTERVAL_MIN * 60 and len(state.positions) < MAX_POSITIONS:
            opps         = get_top_opportunities(ex)
            usdt_per_pos = CAPITAL_USDT / MAX_POSITIONS
            for opp in opps:
                if len(state.positions) >= MAX_POSITIONS:
                    break
                log.info(f"[OPP] {opp['base']} {opp['funding_pct']:.4f}%/8h "
                         f"APR:{opp['apr']:.1f}% Vol:${opp['volume_24h']/1e6:.1f}M")
                open_arb_position(ex, opp, usdt_per_pos)
            if not opps and not state.positions:
                log.info("Sin oportunidades sobre el umbral")
            last_scan = now

        if now - last_hb > 3600:
            send_heartbeat(ex)
            last_hb = now

        time.sleep(60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Bot detenido.")
    except Exception as e:
        log.critical(f"Error fatal: {e}", exc_info=True)
        tg(f"💀 <b>FUNDING BOT CAIDO:</b> {str(e)[:200]}\n⏰ {utcnow()}")
        raise
