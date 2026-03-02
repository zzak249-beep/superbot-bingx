"""
╔══════════════════════════════════════════════════════════════════╗
║         FUNDING RATE ARBITRAGE BOT — BingX                      ║
║         Estrategia Market-Neutral: LONG Spot + SHORT Futuros    ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  CÓMO FUNCIONA:                                                  ║
║  1. Escanea TODOS los pares de BingX cada hora                  ║
║  2. Detecta funding rates > UMBRAL (ej: 0.05% cada 8h)         ║
║  3. Abre LONG en spot + SHORT en futuros (mismo tamaño)         ║
║  4. Cobra el funding cada 8 horas (00:00, 08:00, 16:00 UTC)    ║
║  5. Cierra cuando el funding cae por debajo del mínimo          ║
║                                                                  ║
║  POR QUÉ ES "CASI SIN RIESGO":                                  ║
║  · Si el precio sube → spot gana, futuros pierden (=0)          ║
║  · Si el precio baja → futuros ganan, spot pierde (=0)          ║
║  · Solo ganas o pierdes el funding rate (positivo = ganas)      ║
║                                                                  ║
║  RIESGOS REALES (importantes):                                   ║
║  · Funding puede volverse negativo → pagarías tú               ║
║  · Slippage en pares ilíquidos                                   ║
║  · Diferencia spot vs futuros al abrir/cerrar                   ║
║  · BingX puede no tener spot en todos los pares                 ║
║                                                                  ║
║  VARIABLES DE ENTORNO OBLIGATORIAS:                             ║
║      BINGX_API_KEY   BINGX_API_SECRET                           ║
║      TELEGRAM_BOT_TOKEN  TELEGRAM_CHAT_ID                       ║
║                                                                  ║
║  VARIABLES OPCIONALES:                                           ║
║      DRY_RUN          def: true                                  ║
║      CAPITAL_USDT     def: 50.0   capital total para arbitraje  ║
║      MIN_FUNDING      def: 0.05   % mínimo por intervalo        ║
║      MAX_POSITIONS    def: 3      posiciones simultáneas        ║
║      MIN_FUNDING_CLOSE def: 0.01  % para cerrar la posición     ║
║      FUNDING_INTERVAL  def: 8     horas entre funding payments  ║
║      SCAN_INTERVAL_MIN def: 30    minutos entre escaneos        ║
║      MIN_VOLUME_24H    def: 1000000  volumen mínimo 24h USD     ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import time
import hmac
import hashlib
import requests
import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List
import ccxt

# ══════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════
API_KEY     = os.environ["BINGX_API_KEY"]
API_SECRET  = os.environ["BINGX_API_SECRET"]
TG_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT     = os.environ["TELEGRAM_CHAT_ID"]
BASE_URL    = os.environ.get("BINGX_BASE_URL", "https://open-api.bingx.com")

DRY_RUN           = os.environ.get("DRY_RUN", "true").lower() == "true"
HEDGE_MODE        = os.environ.get("HEDGE_MODE", "true").lower() == "true"
CAPITAL_USDT      = float(os.environ.get("CAPITAL_USDT", "50.0"))
MIN_FUNDING       = float(os.environ.get("MIN_FUNDING", "0.05"))   # % por intervalo
MAX_POSITIONS     = int(os.environ.get("MAX_POSITIONS", "3"))
MIN_FUNDING_CLOSE = float(os.environ.get("MIN_FUNDING_CLOSE", "0.01"))  # % para cerrar
FUNDING_INTERVAL  = int(os.environ.get("FUNDING_INTERVAL", "8"))    # horas
SCAN_INTERVAL_MIN = int(os.environ.get("SCAN_INTERVAL_MIN", "30"))  # minutos
MIN_VOLUME_24H    = float(os.environ.get("MIN_VOLUME_24H", "1000000"))  # USD

# Calcular APR mínimo para referencia
MIN_APR = (MIN_FUNDING / 100) * (8760 / FUNDING_INTERVAL) * 100

# ══════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("funding_bot")

# ══════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════
def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def tg(msg: str):
    """Envía mensaje a Telegram."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        log.warning(f"Telegram error: {e}")

def tg_error(msg: str):
    tg(f"🔥 <b>ERROR FUNDING BOT:</b> {msg}\n⏰ {utcnow()}")

# ══════════════════════════════════════════════════════════
# ESTRUCTURA DE POSICIÓN ARBITRAJE
# ══════════════════════════════════════════════════════════
@dataclass
class ArbPosition:
    symbol:           str
    spot_symbol:      str
    futures_symbol:   str
    side:             str = "long_spot_short_futures"
    usdt_allocated:   float = 0.0
    spot_qty:         float = 0.0
    futures_qty:      float = 0.0
    spot_entry:       float = 0.0
    futures_entry:    float = 0.0
    funding_rate_entry: float = 0.0  # % por intervalo
    funding_collected: float = 0.0   # USDT cobrado hasta ahora
    funding_payments:  int = 0       # número de cobros
    open_time:        str = ""
    annual_rate_entry: float = 0.0   # APR al entrar

    @property
    def funding_per_payment(self) -> float:
        """Cuánto cobra cada 8h en USDT."""
        notional = self.futures_qty * self.futures_entry
        return notional * (self.funding_rate_entry / 100)

    @property
    def total_pnl(self) -> float:
        return self.funding_collected


# ══════════════════════════════════════════════════════════
# ESTADO GLOBAL
# ══════════════════════════════════════════════════════════
class BotState:
    def __init__(self):
        self.positions: Dict[str, ArbPosition] = {}
        self.total_funding_earned: float = 0.0
        self.total_positions_opened: int = 0
        self._load()

    def _path(self) -> str:
        return "/tmp/funding_bot_state.json"

    def _load(self):
        try:
            with open(self._path()) as f:
                data = json.load(f)
                self.total_funding_earned  = data.get("total_funding_earned", 0.0)
                self.total_positions_opened = data.get("total_positions_opened", 0)
                for sym, p in data.get("positions", {}).items():
                    self.positions[sym] = ArbPosition(**p)
        except Exception:
            pass

    def save(self):
        try:
            data = {
                "total_funding_earned": self.total_funding_earned,
                "total_positions_opened": self.total_positions_opened,
                "positions": {k: asdict(v) for k, v in self.positions.items()}
            }
            with open(self._path(), "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.warning(f"State save error: {e}")

state = BotState()

# ══════════════════════════════════════════════════════════
# API BINGX — FUNDING RATES
# ══════════════════════════════════════════════════════════
def bingx_get(endpoint: str, params: dict = {}) -> Optional[dict]:
    """Llamada GET autenticada a la API de BingX."""
    try:
        ts = str(int(time.time() * 1000))
        params_with_ts = {**params, "timestamp": ts}
        query = "&".join(f"{k}={v}" for k, v in sorted(params_with_ts.items()))
        sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
        url = f"{BASE_URL}{endpoint}?{query}&signature={sig}"
        resp = requests.get(url, headers={"X-BX-APIKEY": API_KEY}, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            return data.get("data")
        else:
            log.warning(f"API error {endpoint}: {data.get('msg')}")
            return None
    except Exception as e:
        log.warning(f"bingx_get {endpoint}: {e}")
        return None


def get_all_funding_rates() -> List[dict]:
    """
    Obtiene funding rates actuales de todos los pares.
    Endpoint: GET /openApi/swap/v2/quote/premiumIndex
    Sin symbol = todos los pares.
    Returns lista de: {symbol, markPrice, indexPrice, lastFundingRate, nextFundingTime}
    """
    data = bingx_get("/openApi/swap/v2/quote/premiumIndex")
    if data is None:
        # Fallback: usar ccxt fetch_funding_rates
        return []

    if isinstance(data, list):
        return data
    elif isinstance(data, dict):
        return [data]
    return []


def get_funding_rate_ccxt(ex: ccxt.Exchange) -> Dict[str, float]:
    """
    Obtiene funding rates via ccxt como fallback.
    Retorna dict: {symbol: funding_rate_decimal}
    """
    rates = {}
    try:
        fr = ex.fetch_funding_rates()
        for sym, info in fr.items():
            rate = info.get("fundingRate")
            if rate is not None:
                rates[sym] = float(rate) * 100  # convertir a %
    except Exception as e:
        log.warning(f"ccxt funding rates: {e}")
    return rates


def get_top_opportunities(ex: ccxt.Exchange) -> List[dict]:
    """
    Escanea todos los pares y devuelve los mejores para arbitraje.
    Criterios:
    1. Funding rate > MIN_FUNDING
    2. Volumen 24h > MIN_VOLUME_24H
    3. Par no está ya en posición abierta
    4. BingX tiene mercado spot para ese par
    """
    log.info("Escaneando funding rates...")

    # Obtener funding rates via ccxt
    funding_rates = get_funding_rate_ccxt(ex)
    if not funding_rates:
        log.warning("No se pudieron obtener funding rates")
        return []

    # Obtener tickers para volumen
    tickers = {}
    try:
        all_tickers = ex.fetch_tickers()
        for sym, tk in all_tickers.items():
            vol = float(tk.get("quoteVolume") or 0)
            tickers[sym] = vol
    except Exception as e:
        log.warning(f"fetch_tickers: {e}")

    # Mercados spot disponibles en BingX
    spot_markets = set()
    try:
        spot_ex = ccxt.bingx({
            "apiKey": API_KEY,
            "secret": API_SECRET,
            "options": {"defaultType": "spot"},
            "enableRateLimit": True,
        })
        spot_ex.load_markets()
        spot_markets = set(spot_ex.markets.keys())
    except Exception as e:
        log.warning(f"Spot markets: {e}")

    opportunities = []

    for futures_sym, funding_pct in funding_rates.items():
        # Solo USDT-M perps
        if not futures_sym.endswith("/USDT:USDT"):
            continue

        # Filtro mínimo de funding
        if funding_pct < MIN_FUNDING:
            continue

        # Filtro volumen
        vol = tickers.get(futures_sym, 0)
        if vol < MIN_VOLUME_24H:
            continue

        # ¿Ya tenemos posición?
        base = futures_sym.split("/")[0]
        if base in state.positions:
            continue

        # ¿Hay mercado spot?
        spot_sym = f"{base}/USDT"
        if spot_markets and spot_sym not in spot_markets:
            continue

        # Calcular APR
        payments_per_year = 8760 / FUNDING_INTERVAL
        apr = funding_pct * payments_per_year

        opportunities.append({
            "base": base,
            "futures_symbol": futures_sym,
            "spot_symbol": spot_sym,
            "funding_pct": funding_pct,
            "apr": apr,
            "volume_24h": vol,
            "payment_8h_per_100usdt": funding_pct,  # % de la posición notional
        })

    # Ordenar por funding rate descendente
    opportunities.sort(key=lambda x: x["funding_pct"], reverse=True)

    log.info(f"Oportunidades encontradas: {len(opportunities)} "
             f"(funding > {MIN_FUNDING}%)")

    return opportunities


# ══════════════════════════════════════════════════════════
# ABRIR POSICIÓN DE ARBITRAJE
# ══════════════════════════════════════════════════════════
def open_arb_position(ex: ccxt.Exchange,
                      opp: dict,
                      usdt_per_position: float) -> Optional[ArbPosition]:
    """
    Abre posición de arbitraje:
    1. Compra spot
    2. Abre short en futuros del mismo tamaño
    """
    base           = opp["base"]
    futures_sym    = opp["futures_symbol"]
    spot_sym       = opp["spot_symbol"]
    funding_pct    = opp["funding_pct"]
    apr            = opp["apr"]

    try:
        # Precio actual
        ticker = ex.fetch_ticker(futures_sym)
        price  = float(ticker["last"])
        if price <= 0:
            return None

        # Calcular cantidad
        qty = usdt_per_position / price
        mkt = ex.markets.get(futures_sym, {})
        min_qty = float(mkt.get("limits", {}).get("amount", {}).get("min", 0) or 0)
        if qty < min_qty:
            qty = min_qty

        qty = float(ex.amount_to_precision(futures_sym, qty))

        log.info(
            f"[ARB OPEN] {base} | Funding: {funding_pct:.4f}%/8h "
            f"APR: {apr:.1f}% | ${usdt_per_position:.2f} | "
            f"qty={qty} @ {price:.4g} | {'DRY-RUN' if DRY_RUN else 'LIVE'}"
        )

        spot_entry    = price
        futures_entry = price

        if not DRY_RUN:
            # Paso 1: Comprar spot
            try:
                spot_ex = ccxt.bingx({
                    "apiKey": API_KEY,
                    "secret": API_SECRET,
                    "options": {"defaultType": "spot"},
                    "enableRateLimit": True,
                })
                spot_ex.load_markets()
                spot_order = spot_ex.create_order(spot_sym, "market", "buy", qty)
                spot_entry = float(spot_order.get("average") or price)
                log.info(f"[ARB] Spot LONG abierto @ {spot_entry}")
            except Exception as e:
                log.error(f"[ARB] Spot open falló: {e}")
                tg_error(f"ARB spot open {base}: {str(e)[:100]}")
                return None

            # Paso 2: Abrir short en futuros
            try:
                # Set leverage 1x (no necesitamos más para estar cubiertos)
                try:
                    lv_p = {"hedged": True} if HEDGE_MODE else {}
                    ex.set_leverage(1, futures_sym, params=lv_p)
                except Exception:
                    pass

                short_params = {}
                if HEDGE_MODE:
                    short_params["positionSide"] = "SHORT"

                fut_order = ex.create_order(
                    futures_sym, "market", "sell", qty,
                    params=short_params
                )
                futures_entry = float(fut_order.get("average") or price)
                log.info(f"[ARB] Futures SHORT abierto @ {futures_entry}")
            except Exception as e:
                log.error(f"[ARB] Futures short falló: {e}")
                tg_error(f"ARB futures short {base}: {str(e)[:100]}")
                # CRÍTICO: cerrar el spot si el short falla
                try:
                    spot_ex.create_order(spot_sym, "market", "sell", qty)
                    log.info(f"[ARB] Spot cerrado tras fallo del short")
                except Exception as e2:
                    log.error(f"[ARB] No se pudo cerrar el spot: {e2}")
                return None

        pos = ArbPosition(
            symbol=base,
            spot_symbol=spot_sym,
            futures_symbol=futures_sym,
            usdt_allocated=usdt_per_position,
            spot_qty=qty,
            futures_qty=qty,
            spot_entry=spot_entry,
            futures_entry=futures_entry,
            funding_rate_entry=funding_pct,
            annual_rate_entry=apr,
            open_time=utcnow(),
        )

        state.positions[base] = pos
        state.total_positions_opened += 1
        state.save()

        # Notificación
        payments_per_day = 24 / FUNDING_INTERVAL
        daily_est = pos.funding_per_payment * payments_per_day

        tg(
            f"💰 <b>ARB ABIERTO — {base}</b>\n"
            f"══════════════════════════════\n"
            f"📈 Spot LONG @ <code>{spot_entry:.6g}</code>\n"
            f"📉 Futures SHORT @ <code>{futures_entry:.6g}</code>\n"
            f"══════════════════════════════\n"
            f"💵 Capital: ${usdt_per_position:.2f} USDT\n"
            f"📦 Cantidad: {qty} {base}\n"
            f"══════════════════════════════\n"
            f"🔄 Funding rate: <b>{funding_pct:.4f}%</b> cada {FUNDING_INTERVAL}h\n"
            f"📊 APR estimado: <b>{apr:.1f}%</b>\n"
            f"💸 Est. por pago: <b>${pos.funding_per_payment:.4f}</b>\n"
            f"💸 Est. por día:  <b>${daily_est:.4f}</b>\n"
            f"══════════════════════════════\n"
            f"{'🔵 DRY-RUN' if DRY_RUN else '🟢 LIVE'} | ⏰ {utcnow()}"
        )

        return pos

    except Exception as e:
        log.error(f"open_arb_position {base}: {e}")
        tg_error(f"ARB open {base}: {str(e)[:100]}")
        return None


# ══════════════════════════════════════════════════════════
# CERRAR POSICIÓN DE ARBITRAJE
# ══════════════════════════════════════════════════════════
def close_arb_position(ex: ccxt.Exchange,
                       pos: ArbPosition,
                       reason: str):
    """
    Cierra posición de arbitraje:
    1. Cierra short en futuros
    2. Vende spot
    """
    base = pos.symbol

    log.info(
        f"[ARB CLOSE] {base} | Razón: {reason} | "
        f"Funding cobrado: ${pos.funding_collected:.4f} | "
        f"{'DRY-RUN' if DRY_RUN else 'LIVE'}"
    )

    if not DRY_RUN:
        # Cerrar short en futuros
        try:
            close_params = {"reduceOnly": True}
            if HEDGE_MODE:
                close_params["positionSide"] = "SHORT"

            ex.create_order(
                pos.futures_symbol, "market", "buy",
                pos.futures_qty, params=close_params
            )
            log.info(f"[ARB] Futures SHORT cerrado")
        except Exception as e:
            log.error(f"[ARB] Futures close error: {e}")
            tg_error(f"ARB futures close {base}: {str(e)[:100]}")

        # Cerrar spot
        try:
            spot_ex = ccxt.bingx({
                "apiKey": API_KEY,
                "secret": API_SECRET,
                "options": {"defaultType": "spot"},
                "enableRateLimit": True,
            })
            spot_ex.load_markets()
            spot_ex.create_order(
                pos.spot_symbol, "market", "sell",
                pos.spot_qty
            )
            log.info(f"[ARB] Spot vendido")
        except Exception as e:
            log.error(f"[ARB] Spot close error: {e}")
            tg_error(f"ARB spot close {base}: {str(e)[:100]}")

    state.total_funding_earned += pos.funding_collected
    del state.positions[base]
    state.save()

    tg(
        f"🔒 <b>ARB CERRADO — {base}</b>\n"
        f"══════════════════════════════\n"
        f"📋 Razón: {reason}\n"
        f"💰 Funding cobrado: <b>${pos.funding_collected:.4f}</b>\n"
        f"🔄 Pagos recibidos: {pos.funding_payments}\n"
        f"📊 APR entrada: {pos.annual_rate_entry:.1f}%\n"
        f"══════════════════════════════\n"
        f"🏦 Total acumulado: ${state.total_funding_earned:.4f}\n"
        f"{'🔵 DRY-RUN' if DRY_RUN else '🟢 LIVE'} | ⏰ {utcnow()}"
    )


# ══════════════════════════════════════════════════════════
# REGISTRAR COBRO DE FUNDING
# ══════════════════════════════════════════════════════════
def record_funding_payment(ex: ccxt.Exchange):
    """
    Llamar cerca de 00:00, 08:00, 16:00 UTC.
    En DRY-RUN, calcula el importe teórico.
    En LIVE, consulta el historial de income de BingX.
    """
    if not state.positions:
        return

    log.info("Registrando pagos de funding...")

    for base, pos in state.positions.items():
        if DRY_RUN:
            # Simulación: calcular con el rate de entrada
            payment = pos.funding_per_payment
            pos.funding_collected  += payment
            pos.funding_payments   += 1
            log.info(
                f"[ARB DRY] {base} funding: +${payment:.5f} "
                f"(total: ${pos.funding_collected:.5f})"
            )
        else:
            # LIVE: intentar leer del historial de income
            try:
                income = bingx_get(
                    "/openApi/swap/v2/user/income",
                    {"symbol": pos.futures_symbol,
                     "incomeType": "FUNDING_FEE",
                     "limit": "5"}
                )
                if income:
                    for entry in income:
                        amount = float(entry.get("income", 0))
                        if amount > 0:
                            pos.funding_collected += amount
                            pos.funding_payments  += 1
                            log.info(f"[ARB LIVE] {base} funding: +${amount:.5f}")
                            break
            except Exception as e:
                log.warning(f"Income check {base}: {e}")

    state.save()

    # Resumen por Telegram cada cobro
    if state.positions:
        lines = ["💸 <b>FUNDING COBRADO</b>\n══════════════════════════════"]
        total_payment = 0.0
        for base, pos in state.positions.items():
            payment = pos.funding_per_payment
            total_payment += payment
            lines.append(
                f"  {base}: ${payment:.5f} "
                f"(total: ${pos.funding_collected:.4f})"
            )
        lines.append(f"══════════════════════════════")
        lines.append(f"💰 Este pago: ${total_payment:.5f}")
        lines.append(f"🏦 Acumulado total: ${state.total_funding_earned:.4f}")
        lines.append(f"⏰ {utcnow()}")
        tg("\n".join(lines))


# ══════════════════════════════════════════════════════════
# GESTIONAR POSICIONES EXISTENTES
# ══════════════════════════════════════════════════════════
def manage_positions(ex: ccxt.Exchange):
    """
    Revisa las posiciones abiertas:
    1. Actualiza el funding rate actual
    2. Cierra si el funding bajó demasiado (ya no es rentable)
    3. Cierra si el funding se volvió negativo
    """
    if not state.positions:
        return

    funding_rates = get_funding_rate_ccxt(ex)

    for base in list(state.positions.keys()):
        pos = state.positions[base]

        current_rate = funding_rates.get(pos.futures_symbol)
        if current_rate is None:
            continue

        log.info(
            f"[ARB MANAGE] {base} | "
            f"Rate entrada: {pos.funding_rate_entry:.4f}% | "
            f"Rate actual: {current_rate:.4f}%"
        )

        # Cerrar si el funding bajó por debajo del mínimo rentable
        if current_rate < MIN_FUNDING_CLOSE:
            reason = (
                f"Funding actual {current_rate:.4f}% "
                f"< mínimo {MIN_FUNDING_CLOSE:.4f}%"
            )
            if current_rate < 0:
                reason = f"⚠️ Funding NEGATIVO: {current_rate:.4f}%"
            close_arb_position(ex, pos, reason)


# ══════════════════════════════════════════════════════════
# HEARTBEAT
# ══════════════════════════════════════════════════════════
def send_heartbeat(ex: ccxt.Exchange):
    try:
        bal = float(ex.fetch_balance()["USDT"]["free"])
    except Exception:
        bal = 0.0

    if not state.positions:
        pos_text = "(ninguna)"
        daily_est = 0.0
    else:
        lines = []
        daily_est = 0.0
        payments_per_day = 24 / FUNDING_INTERVAL
        for base, pos in state.positions.items():
            payment_day = pos.funding_per_payment * payments_per_day
            daily_est += payment_day
            lines.append(
                f"  {base}: {pos.funding_rate_entry:.4f}%/8h "
                f"→ ${payment_day:.4f}/día "
                f"(cobrado: ${pos.funding_collected:.4f})"
            )
        pos_text = "\n".join(lines)

    tg(
        f"💗 <b>HEARTBEAT — FUNDING BOT</b>\n"
        f"══════════════════════════════\n"
        f"💵 Balance: ${bal:.2f}\n"
        f"📊 Posiciones: {len(state.positions)}/{MAX_POSITIONS}\n"
        f"{pos_text}\n"
        f"══════════════════════════════\n"
        f"💰 Est. día: ${daily_est:.4f}\n"
        f"🏦 Acumulado: ${state.total_funding_earned:.4f}\n"
        f"📈 Total trades: {state.total_positions_opened}\n"
        f"{'🔵 DRY-RUN' if DRY_RUN else '🟢 LIVE'} | ⏰ {utcnow()}"
    )


# ══════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════
def main():
    log.info("=== FUNDING RATE BOT INICIANDO ===")

    # Construir exchange (futuros)
    ex = ccxt.bingx({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "options": {"defaultType": "swap"},
        "enableRateLimit": True,
    })
    ex.load_markets()

    balance = 0.0
    try:
        balance = float(ex.fetch_balance()["USDT"]["free"])
    except Exception as e:
        log.warning(f"Balance: {e}")

    daily_est_all = 0.0
    payments_per_day = 24 / FUNDING_INTERVAL
    for pos in state.positions.values():
        daily_est_all += pos.funding_per_payment * payments_per_day

    # Startup message
    mode = "🔵 DRY-RUN" if DRY_RUN else "🟢 LIVE"
    tg(
        f"🚀 <b>FUNDING RATE BOT INICIADO</b>\n"
        f"══════════════════════════════\n"
        f"⚙️ Modo: {mode}\n"
        f"💵 Capital asignado: ${CAPITAL_USDT:.2f} USDT\n"
        f"══════════════════════════════\n"
        f"📊 Configuración:\n"
        f"  🔑 Funding mínimo: {MIN_FUNDING:.4f}%/8h\n"
        f"  📈 APR mínimo: ~{MIN_APR:.1f}%\n"
        f"  🔒 Cerrar si rate < {MIN_FUNDING_CLOSE:.4f}%\n"
        f"  📦 Max posiciones: {MAX_POSITIONS}\n"
        f"  🔄 Scan cada: {SCAN_INTERVAL_MIN} minutos\n"
        f"══════════════════════════════\n"
        f"💰 Balance: ${balance:.2f}\n"
        f"📊 Posiciones activas: {len(state.positions)}\n"
        f"💸 Est. día actual: ${daily_est_all:.4f}\n"
        f"⏰ {utcnow()}"
    )

    last_scan   = 0
    last_hb     = 0
    last_funding = 0

    # Funding times: 00:00, 08:00, 16:00 UTC (en segundos desde medianoche)
    funding_hours = [0, 8, 16]

    log.info(f"Loop iniciado. DRY_RUN={DRY_RUN} | "
             f"Capital=${CAPITAL_USDT} | MinFunding={MIN_FUNDING}%")

    while True:
        now = time.time()
        dt  = datetime.now(timezone.utc)

        # ── REGISTRAR FUNDING (cada 8h tras el pago) ──────────
        # Revisar si estamos 5 minutos después de un pago de funding
        minutes_in_day = dt.hour * 60 + dt.minute
        for fh in funding_hours:
            funding_minute = fh * 60
            if (funding_minute + 1 <= minutes_in_day <= funding_minute + 6
                    and now - last_funding > 3600):
                record_funding_payment(ex)
                last_funding = now
                break

        # ── GESTIONAR POSICIONES EXISTENTES ───────────────────
        manage_positions(ex)

        # ── BUSCAR NUEVAS OPORTUNIDADES ───────────────────────
        if (now - last_scan > SCAN_INTERVAL_MIN * 60
                and len(state.positions) < MAX_POSITIONS):

            opportunities = get_top_opportunities(ex)
            usdt_per_pos  = CAPITAL_USDT / MAX_POSITIONS

            for opp in opportunities:
                if len(state.positions) >= MAX_POSITIONS:
                    break

                log.info(
                    f"[OPP] {opp['base']} | "
                    f"Funding: {opp['funding_pct']:.4f}%/8h | "
                    f"APR: {opp['apr']:.1f}% | "
                    f"Vol24h: ${opp['volume_24h']/1e6:.1f}M"
                )
                open_arb_position(ex, opp, usdt_per_pos)

            # Si no hay oportunidades abiertas, avisar
            if not opportunities and not state.positions:
                log.info("No hay funding rates por encima del umbral actualmente")

            last_scan = now

        # ── HEARTBEAT cada 1 hora ─────────────────────────────
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
        tg(f"💀 <b>FUNDING BOT CAÍDO:</b> {str(e)[:200]}\n⏰ {utcnow()}")
        raise
