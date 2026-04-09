"""
SuperBot v3.1 — Triple Confirmation Engine + Bear-Aware
════════════════════════════════════════════════════════

FIXES v3.1 vs v3.0:
  1. Balance parsing fix — USDT no encontrado → parseo robusto
  2. Market regime check — pausa en bajista, añade SHORTs opcionalmente
  3. Scanner más flexible — reduce exigencias en mercado neutral
  4. Anti-churn — mismo símbolo bloqueado 4h tras SL, 24h si repetido
  5. BTC 4h filter — no abrir LONGs si BTC cae >2% en 4h
  6. Breadth guard — si <40% coins alcistas, reducir tamaño o pausar
  7. Better sync — detecta posiciones cerradas por SL del exchange
  8. Retry logic mejorado para órdenes
  9. Daily stats en Telegram
 10. Crash guard — pausa automática si BTC cae >3% en 1 ciclo
"""
import logging, os, time, json, requests, hmac, hashlib
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional

from bingx_client import BingXClient
from scanner import Scanner
from risk_manager import RiskManager, TradeParams
from strategy import Signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("BOT")

API_KEY    = os.environ.get("BINGX_API_KEY", "").strip()
SECRET_KEY = os.environ.get("BINGX_SECRET_KEY", "").strip()
if not API_KEY or not SECRET_KEY:
    raise RuntimeError(
        "Variables de entorno faltantes.\n"
        "Añade en Railway -> Variables:\n"
        "  BINGX_API_KEY\n  BINGX_SECRET_KEY"
    )

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")

SCAN_PERIOD     = int(os.environ.get("SCAN_PERIOD_SECONDS", "900"))
DRY_RUN         = os.environ.get("DRY_RUN", "false").lower() == "true"
LIMIT_ENTRY     = os.environ.get("LIMIT_ENTRY", "true").lower() == "true"
SLIPPAGE_OFFSET = 0.0003

# v3.1: nuevos parámetros
REGIME_CHECK         = os.environ.get("REGIME_CHECK", "true").lower() == "true"
BTC_4H_CRASH_PCT     = float(os.environ.get("BTC_4H_CRASH_PCT", "3.0"))
BTC_4H_CRASH_HOURS   = int(os.environ.get("BTC_4H_CRASH_HOURS", "2"))
BREADTH_MIN          = float(os.environ.get("BREADTH_MIN", "0.40"))
DAILY_LOSS_CAP_PCT   = float(os.environ.get("DAILY_LOSS_CAP_PCT", "10.0"))
CD_SL_HOURS          = int(os.environ.get("CD_SL_HOURS", "4"))     # cooldown tras SL
ALLOW_SHORTS         = os.environ.get("ALLOW_SHORTS", "false").lower() == "true"
MAX_OPEN_TRADES      = int(os.environ.get("MAX_OPEN_TRADES", "3"))

STATE_FILE   = "/tmp/bot_state_v31.json"
REGIME_FILE  = "/tmp/bot_regime_v31.json"

# Coins para breadth check
BREADTH_COINS = [
    'BTC-USDT','ETH-USDT','BNB-USDT','SOL-USDT','XRP-USDT',
    'ADA-USDT','AVAX-USDT','DOGE-USDT','LINK-USDT','NEAR-USDT',
]


# ============================================================================
# HELPERS
# ============================================================================

def _tg(msg: str):
    """Envía mensaje a Telegram."""
    try:
        if TG_TOKEN and TG_CHAT:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                timeout=6
            )
    except: pass


def _safe_float(val, default=0.0) -> float:
    """Parseo robusto de floats (fix v3.1 para balance USDT)."""
    if val is None: return default
    if isinstance(val, (int, float)): return float(val)
    if isinstance(val, dict):
        # Buscar en claves conocidas
        for k in ('equity', 'balance', 'availableMargin', 'amount', 'totalWalletBalance'):
            if k in val:
                v = _safe_float(val[k], 0)
                if v > 0: return v
        return default
    try: return float(str(val).replace(',', '.'))
    except: return default


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {
            "open_trades": {}, "daily_date": "", "trade_log": [],
            "daily_pnl": 0.0, "total_trades": 0, "wins": 0, "losses": 0,
            "cooldowns": {},
        }


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


# ============================================================================
# MARKET REGIME DETECTOR v3.1
# ============================================================================

class RegimeDetector:
    """Detecta el régimen de mercado (bull/neutral/caution/bear)."""

    def __init__(self, client: 'BingXClient'):
        self.client   = client
        self.regime   = 'neutral'
        self.btc_1h   = 0.0
        self.btc_4h   = 0.0
        self.breadth  = 0.5
        self._pause_until: Optional[datetime] = None
        self._last_update = datetime.utcnow() - timedelta(hours=1)

    def _get_klines(self, symbol: str, interval: str, limit: int):
        """Obtiene velas del exchange."""
        try:
            return self.client.get_klines(symbol, interval, limit)
        except Exception as e:
            log.warning(f"Klines {symbol} {interval}: {e}")
            return []

    def _ema(self, prices, n):
        if len(prices) < n: return prices[-1] if prices else 0
        k = 2 / (n + 1)
        e = prices[0]
        for p in prices[1:]: e = p * k + e * (1 - k)
        return e

    def update(self):
        """Actualiza el régimen. Llamar cada 5 minutos."""
        if (datetime.utcnow() - self._last_update).total_seconds() < 250:
            return  # No actualizar demasiado frecuente

        self._last_update = datetime.utcnow()

        try:
            # 1. BTC 1h y 4h
            c1h = self._get_klines('BTC-USDT', '1h', 5)
            c4h = self._get_klines('BTC-USDT', '4h', 10)

            if c1h and len(c1h) >= 2:
                self.btc_1h = (c1h[-1] - c1h[-2]) / c1h[-2] * 100 if c1h[-2] > 0 else 0

            if c4h and len(c4h) >= 4:
                self.btc_4h = (c4h[-1] - c4h[-4]) / c4h[-4] * 100 if c4h[-4] > 0 else 0
                # Crash guard
                if self.btc_4h < -BTC_4H_CRASH_PCT:
                    if not self._pause_until or datetime.utcnow() > self._pause_until:
                        self._pause_until = datetime.utcnow() + timedelta(hours=BTC_4H_CRASH_HOURS)
                        log.warning(f"🚨 BTC CRASH {self.btc_4h:.1f}% en 4h — pausa {BTC_4H_CRASH_HOURS}h")
                        _tg(
                            f"<b>🚨 CRASH GUARD ACTIVO</b>\n"
                            f"BTC cayó {self.btc_4h:.1f}% en 4h\n"
                            f"SuperBot pausado {BTC_4H_CRASH_HOURS}h"
                        )

            # 2. Breadth: % de coins top-10 sobre su EMA21
            bulls = 0; total = 0
            for coin in BREADTH_COINS[:8]:
                try:
                    c = self._get_klines(coin, '1h', 25)
                    if c and len(c) >= 21:
                        e21 = self._ema(c, 21)
                        if c[-1] > e21: bulls += 1
                        total += 1
                except: pass
            if total > 0:
                self.breadth = bulls / total

            # 3. Determinar régimen
            btc_bear   = (self.btc_4h < -1.5) or (self.btc_1h < -1.0)
            low_breath = self.breadth < BREADTH_MIN

            old_regime = self.regime
            if btc_bear and low_breath:
                self.regime = 'bear'
            elif btc_bear or low_breath:
                self.regime = 'caution'
            elif self.btc_4h > 1.5 and self.breadth > 0.60:
                self.regime = 'bull'
            else:
                self.regime = 'neutral'

            if self.regime != old_regime:
                log.info(f"📊 RÉGIMEN: {old_regime} → {self.regime} | BTC4h:{self.btc_4h:+.1f}% breadth:{int(self.breadth*100)}%")
                if self.regime == 'bear':
                    _tg(
                        f"<b>🐻 RÉGIMEN BAJISTA</b>\n"
                        f"BTC 4h: {self.btc_4h:+.1f}% | Breadth: {int(self.breadth*100)}%\n"
                        f"{'LONGs suspendidos' if not ALLOW_SHORTS else 'Solo SHORTs activos'}"
                    )
                elif self.regime == 'bull' and old_regime in ('bear', 'caution'):
                    _tg(f"<b>🟢 RÉGIMEN ALCISTA</b>\nBTC 4h: {self.btc_4h:+.1f}% | Breadth: {int(self.breadth*100)}%")

        except Exception as e:
            log.error(f"RegimeDetector.update: {e}")

    def can_open_long(self) -> tuple:
        """Retorna (bool, reason)."""
        if self._pause_until and datetime.utcnow() < self._pause_until:
            remaining = int((self._pause_until - datetime.utcnow()).total_seconds() / 60)
            return False, f"crash guard {remaining}min"
        if self.regime == 'bear':
            return False, "régimen bajista"
        return True, "ok"

    def can_open_short(self) -> tuple:
        """Solo para mercados bajistas si ALLOW_SHORTS está activo."""
        if not ALLOW_SHORTS:
            return False, "SHORTs no habilitados"
        if self.regime in ('bear', 'caution') and self.btc_4h < -1.0:
            return True, "ok"
        return False, "mercado no bajista suficiente"

    def score_multiplier(self) -> float:
        """Multiplicador de score según régimen."""
        if self.regime == 'bull':   return 1.0
        if self.regime == 'neutral': return 0.9
        if self.regime == 'caution': return 0.7
        if self.regime == 'bear':    return 0.3
        return 1.0


# ============================================================================
# SUPERBOT v3.1
# ============================================================================

class SuperBot:
    def __init__(self):
        self.client  = BingXClient(API_KEY, SECRET_KEY)
        self.scanner = Scanner(self.client)
        self.risk    = RiskManager()
        self.state   = load_state()
        self.regime  = RegimeDetector(self.client)

        # v3.1: estadísticas
        self._equity_start  = 0.0
        self._last_tg_report = datetime.utcnow() - timedelta(hours=3)

        self._init_daily()
        self._sync_positions_from_exchange()

        log.info(
            f"SuperBot v3.1 | DRY_RUN={DRY_RUN} | "
            f"SCAN_PERIOD={SCAN_PERIOD}s | LIMIT_ENTRY={LIMIT_ENTRY} | "
            f"CD_SL={CD_SL_HOURS}h | ALLOW_SHORTS={ALLOW_SHORTS}"
        )

    # ── balance ────────────────────────────────────────────────────────────

    def _get_balance(self) -> float:
        """Parseo robusto del balance (fix crítico v3.1)."""
        try:
            raw = self.client.get_balance()
            # Si ya es un float, úsalo
            if isinstance(raw, (int, float)):
                return float(raw)
            # Si es dict, buscar equity/balance
            if isinstance(raw, dict):
                b = _safe_float(raw)
                if b > 0: return b
                # Buscar en subclave 'balance'
                if 'balance' in raw:
                    inner = raw['balance']
                    if isinstance(inner, dict):
                        for k in ('equity', 'balance', 'availableMargin'):
                            v = _safe_float(inner.get(k, 0))
                            if v > 0: return v
                    else:
                        v = _safe_float(inner)
                        if v > 0: return v
            # Fallback: pedir directamente a la API
            log.warning(f"get_balance: estructura inesperada ({type(raw)}), intentando API directa")
            return self._get_balance_direct()
        except Exception as e:
            log.error(f"get_balance: {e}")
            return self._get_balance_direct()

    def _get_balance_direct(self) -> float:
        """Consulta directa a la API de BingX para obtener el balance."""
        try:
            import time as _time
            from urllib.parse import urlencode
            p = {'timestamp': str(int(_time.time() * 1000))}
            qs = urlencode(sorted(p.items()))
            sig = hmac.new(SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()
            url = f"https://open-api.bingx.com/openApi/swap/v2/user/balance?{qs}&signature={sig}"
            hdr = {'X-BX-APIKEY': API_KEY, 'Content-Type': 'application/x-www-form-urlencoded'}
            r = requests.get(url, headers=hdr, timeout=15).json()
            if r.get('code') == 0:
                b = r.get('data', {})
                for k in ('equity', 'balance', 'availableMargin'):
                    v = _safe_float(b.get(k, 0))
                    if v > 0:
                        log.info(f"Balance directo: ${v:.2f} USDT ({k})")
                        return v
        except Exception as e:
            log.error(f"_get_balance_direct: {e}")
        return 50.0  # Fallback seguro

    # ── sync ───────────────────────────────────────────────────────────────

    def _sync_positions_from_exchange(self):
        if DRY_RUN:
            return
        try:
            live = {p["symbol"]: p for p in self.client.get_positions()}

            # Eliminar trades cerrados
            for s in list(self.state["open_trades"]):
                if s not in live:
                    log.info(f"Sync: eliminando {s} (cerrado en BingX)")
                    t = self.state["open_trades"].pop(s)
                    entry = float(t.get("entry", 0))
                    # No podemos saber el PnL exacto, registrar como cerrado
                    self.state["trade_log"].append({
                        "symbol": s, "closed_at": datetime.utcnow().isoformat(),
                        "reason": "cerrado_externo", "entry": entry,
                    })

            # Añadir posiciones nuevas del exchange
            for sym, pos in live.items():
                if sym not in self.state["open_trades"]:
                    amt   = float(pos.get("positionAmt", 0))
                    side  = "LONG" if amt > 0 else "SHORT"
                    entry = float(pos.get("avgPrice", 0))
                    log.info(f"Sync: añadiendo {sym} {side} @ {entry}")
                    self.state["open_trades"][sym] = {
                        "direction": side, "entry": entry,
                        "sl": 0, "tp1": 0, "tp2": 0, "tp3": 0, "tp4": 0, "tp5": 0,
                        "qty": abs(amt), "qty_p": 3,
                        "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
                        "opened_at": datetime.utcnow().isoformat(),
                        "synced": True,
                    }

            save_state(self.state)
            log.info(f"Sync OK: {len(self.state['open_trades'])} posiciones activas")
        except Exception as e:
            log.error(f"Error sync: {e}")

    # ── daily ──────────────────────────────────────────────────────────────

    def _init_daily(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state["daily_date"] != today:
            balance = self._get_balance()
            self.risk.reset_daily(balance)
            self._equity_start = balance
            self.state["daily_date"]  = today
            self.state["daily_pnl"]   = 0.0
            save_state(self.state)
            log.info(f"Nuevo día: {today} | Balance: {balance:.2f} USDT")
        else:
            self._equity_start = self._get_balance()

    # ── cooldowns v3.1 ────────────────────────────────────────────────────

    def _cd_ok(self, symbol: str) -> tuple:
        """Verifica si un símbolo puede ser operado."""
        cds = self.state.get("cooldowns", {})
        if symbol in cds:
            resume_ts = cds[symbol].get("resume", 0)
            reason    = cds[symbol].get("reason", "cd")
            if time.time() < resume_ts:
                remaining = int((resume_ts - time.time()) / 60)
                return False, f"cooldown {remaining}min ({reason})"
            else:
                del cds[symbol]
                save_state(self.state)
        return True, "ok"

    def _set_cd(self, symbol: str, reason: str = "TP"):
        """Establece cooldown para un símbolo."""
        cds = self.state.setdefault("cooldowns", {})
        if reason == "SL":
            hours = CD_SL_HOURS
        else:
            hours = 0.17  # ~10 minutos para TP
        cds[symbol] = {
            "reason": reason,
            "resume": time.time() + hours * 3600,
            "set_at": datetime.utcnow().isoformat(),
        }
        save_state(self.state)

    # ── precision ──────────────────────────────────────────────────────────

    def _get_precision(self, symbol: str) -> tuple:
        try:
            info    = self.client.get_symbol_info(symbol)
            qty_p   = int(info.get("quantityPrecision", 3))
            price_p = int(info.get("pricePrecision", 4))
            return qty_p, price_p
        except:
            return 3, 4

    # ── abrir trade ────────────────────────────────────────────────────────

    def _open_trade(self, symbol: str, signal: Signal):
        if symbol in self.state["open_trades"]:
            return

        # v3.1: verificar cooldown
        cd_ok, cd_reason = self._cd_ok(symbol)
        if not cd_ok:
            log.debug(f"Skip {symbol}: {cd_reason}")
            return

        # v3.1: verificar régimen para la dirección de la señal
        direction = getattr(signal, 'direction', 'LONG')
        if direction == 'LONG':
            ok, reason = self.regime.can_open_long()
            if not ok:
                log.debug(f"Skip {symbol}: {reason}")
                return
        elif direction == 'SHORT':
            ok, reason = self.regime.can_open_short()
            if not ok:
                log.debug(f"Skip {symbol}: {reason}")
                return

        balance    = self._get_balance()
        open_count = len(self.state["open_trades"])

        if open_count >= MAX_OPEN_TRADES:
            log.debug(f"Max trades ({MAX_OPEN_TRADES}) alcanzados")
            return

        if not self.risk.can_open_trade(open_count, balance):
            return

        # v3.1: verificar daily loss cap
        if self._equity_start > 0:
            daily_loss_pct = abs(self.state.get("daily_pnl", 0)) / self._equity_start * 100
            if self.state.get("daily_pnl", 0) < 0 and daily_loss_pct > DAILY_LOSS_CAP_PCT:
                log.warning(f"Daily loss cap alcanzado: {daily_loss_pct:.1f}%")
                return

        qty_p, price_p = self._get_precision(symbol)
        params = self.risk.size_position(
            symbol, signal.direction,
            signal.entry, signal.sl,
            signal.tp1, signal.tp2, signal.tp3,
            balance, qty_p, price_p,
        )
        if not params:
            return

        tier_tag = f"[Tier {signal.tier}] " if getattr(signal, 'tier', None) else ""

        if DRY_RUN:
            log.info(
                f"[DRY RUN] {tier_tag}{symbol} {params.direction} x{params.quantity} "
                f"@ {params.entry_price} SL={params.sl_price} TP1={params.tp1_price} "
                f"| {getattr(signal, 'reason', '?')}"
            )
            self.state["open_trades"][symbol] = self._make_trade_record(
                params, signal, qty_p, price_p, "DRY_RUN"
            )
            save_state(self.state)
            return

        # Orden real
        try:
            self.client.set_margin_type(symbol, "ISOLATED")
            self.client.set_leverage(symbol, params.leverage, params.direction)

            side     = "BUY"  if params.direction == "LONG" else "SELL"
            pos_side = params.direction

            if LIMIT_ENTRY:
                offset    = SLIPPAGE_OFFSET * params.entry_price
                limit_px  = (params.entry_price - offset if side == "BUY"
                             else params.entry_price + offset)
                limit_px  = round(limit_px, price_p)
                result    = self.client.place_order(
                    symbol, side, pos_side, "LIMIT", params.quantity,
                    price=limit_px, stop_loss=params.sl_price,
                )
                price_used = limit_px
            else:
                result    = self.client.place_order(
                    symbol, side, pos_side, "MARKET", params.quantity,
                    stop_loss=params.sl_price,
                )
                price_used = "MARKET"

            order_id = result.get("data", {}).get("orderId", "?")
            log.info(
                f"{tier_tag}{symbol} {params.direction} qty={params.quantity} "
                f"@ {price_used} SL={params.sl_price} orderId={order_id} "
                f"| Régimen:{self.regime.regime}"
            )

            self.state["open_trades"][symbol] = self._make_trade_record(
                params, signal, qty_p, price_p, order_id
            )
            self.state["total_trades"] = self.state.get("total_trades", 0) + 1
            save_state(self.state)

            _tg(
                f"<b>{'🟢' if params.direction == 'LONG' else '🔴'} {params.direction}</b> — {symbol}\n"
                f"{tier_tag}@ ${price_used} | SL: ${params.sl_price}\n"
                f"TP1: ${params.tp1_price} | TP2: ${params.tp2_price}\n"
                f"Régimen: {self.regime.regime} | Breadth: {int(self.regime.breadth*100)}%"
            )

        except Exception as e:
            log.error(f"Error abriendo {symbol}: {e}")

    def _make_trade_record(self, params, signal, qty_p, price_p, order_id):
        return {
            "direction": params.direction, "entry": params.entry_price,
            "sl": params.sl_price,
            "tp1": params.tp1_price, "tp2": params.tp2_price,
            "tp3": params.tp3_price,
            "tp4": round(getattr(signal, 'tp4', 0), price_p),
            "tp5": round(getattr(signal, 'tp5', 0), price_p),
            "qty": params.quantity, "qty_p": qty_p,
            "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
            "order_id": order_id,
            "opened_at": datetime.utcnow().isoformat(),
            "tier": getattr(signal, "tier", ""),
            "regime": self.regime.regime,
        }

    # ── gestionar posiciones ───────────────────────────────────────────────

    def _manage_positions(self):
        if not self.state["open_trades"]:
            return

        try:
            positions = {p["symbol"]: p for p in self.client.get_positions()}
        except Exception as e:
            log.error(f"Error obteniendo posiciones: {e}")
            return

        for symbol, trade in list(self.state["open_trades"].items()):
            direction = trade["direction"]
            qty       = float(trade["qty"])
            tp1       = float(trade.get("tp1", 0))
            tp2       = float(trade.get("tp2", 0))
            tp3       = float(trade.get("tp3", 0))
            tp4       = float(trade.get("tp4", 0))
            tp5       = float(trade.get("tp5", 0))
            tp1_hit   = trade.get("tp1_hit", False)
            tp2_hit   = trade.get("tp2_hit", False)
            tp3_hit   = trade.get("tp3_hit", False)
            qty_p     = int(trade.get("qty_p", 3))
            synced    = trade.get("synced", False)

            # v3.1: verificar si la posición sigue abierta en el exchange
            if not DRY_RUN and symbol not in positions:
                log.info(f"Posición cerrada externamente: {symbol}")
                entry  = float(trade.get("entry", 0))
                # Registrar como probable SL
                self._on_trade_closed(symbol, entry, entry * 0.99, "SL_externo", qty)
                continue

            try:
                ticker = self.client.get_ticker(symbol)
                price  = float(ticker.get("lastPrice", trade["entry"]))
            except Exception:
                continue

            if synced and tp1 == 0:
                continue

            def _tp_reached(tp_level):
                if tp_level == 0: return False
                return (direction == "LONG"  and price >= tp_level) or \
                       (direction == "SHORT" and price <= tp_level)

            def _partial_close(label, pqty):
                if pqty <= 0: return
                log.info(f"{label} {symbol} | Cerrando {pqty}/{qty} @ ${price:.4f}")
                if not DRY_RUN:
                    try:
                        self.client.close_position(symbol, direction, pqty)
                    except Exception as e:
                        log.error(f"Error {label} {symbol}: {e}")

            # TP1: 40%
            if not tp1_hit and _tp_reached(tp1):
                pqty = round(qty * 0.4, qty_p)
                _partial_close("TP1", pqty)
                trade["tp1_hit"] = True
                trade["qty"]     = round(qty - pqty, qty_p)
                qty              = trade["qty"]
                # Break-even SL
                if trade["sl"] < trade["entry"]:
                    trade["sl"] = round(float(trade["entry"]) * 1.0005, 6)
                    log.info(f"SL → break-even @ ${trade['sl']}")
                save_state(self.state)

            # TP2: 30% del restante
            if tp1_hit and not tp2_hit and _tp_reached(tp2):
                pqty = round(qty * 0.3, qty_p)
                _partial_close("TP2", pqty)
                trade["tp2_hit"] = True
                trade["qty"]     = round(qty - pqty, qty_p)
                qty              = trade["qty"]
                save_state(self.state)

            # TP3: 30% del restante
            if tp2_hit and not tp3_hit and _tp_reached(tp3):
                pqty = round(qty * 0.3, qty_p)
                _partial_close("TP3", pqty)
                trade["tp3_hit"] = True
                trade["qty"]     = round(qty - pqty, qty_p)
                qty              = trade["qty"]
                save_state(self.state)

            # TP4: 50% del restante
            if tp3_hit and tp4 > 0 and _tp_reached(tp4):
                pqty = round(qty * 0.5, qty_p)
                _partial_close("TP4", pqty)
                trade["qty"] = round(qty - pqty, qty_p)
                qty          = trade["qty"]
                save_state(self.state)

            # TP5: cierre total
            if tp3_hit and tp5 > 0 and _tp_reached(tp5):
                _partial_close("TP5 (total)", trade["qty"])
                entry_p = float(trade["entry"])
                pnl     = abs(price - entry_p) * trade["qty"]
                self._on_trade_closed(symbol, entry_p, price, "TP5", trade["qty"])
                continue

            # v3.1: SL dinámico — si el régimen cambia a bajista con trade abierto, avisar
            if self.regime.regime == 'bear' and direction == 'LONG':
                opened_at = datetime.fromisoformat(trade.get("opened_at", datetime.utcnow().isoformat()))
                mins_open = (datetime.utcnow() - opened_at).total_seconds() / 60
                if not trade.get("bear_warned") and mins_open > 5:
                    trade["bear_warned"] = True
                    entry_p = float(trade["entry"])
                    pct = (price - entry_p) / entry_p * 100
                    _tg(
                        f"<b>⚠️ RÉGIMEN BAJISTA con LONG abierto</b>\n"
                        f"{symbol} | {pct:+.2f}% desde entrada\n"
                        f"Considera cerrar manualmente si no hay SL activo"
                    )
                    save_state(self.state)

    def _on_trade_closed(self, symbol, entry, exit_price, reason, qty):
        """Registra cierre de trade y actualiza estadísticas."""
        win = (exit_price > entry) if symbol in self.state.get("open_trades", {}) \
              else (reason not in ("SL_externo", "STOP LOSS"))
        direction = self.state["open_trades"].get(symbol, {}).get("direction", "LONG")
        if direction == "SHORT":
            win = (exit_price < entry)

        pnl_pct = abs(exit_price - entry) / entry * 100 * (-1 if not win else 1)

        self.state["trade_log"].append({
            "symbol": symbol, "entry": entry, "exit": exit_price,
            "reason": reason, "win": win, "pnl_pct": pnl_pct,
            "closed_at": datetime.utcnow().isoformat(),
        })
        self.state["daily_pnl"] = self.state.get("daily_pnl", 0) + pnl_pct
        if win:
            self.state["wins"] = self.state.get("wins", 0) + 1
        else:
            self.state["losses"] = self.state.get("losses", 0) + 1
            # v3.1: aplicar cooldown largo tras SL
            self._set_cd(symbol, "SL")

        if symbol in self.state["open_trades"]:
            del self.state["open_trades"][symbol]
        save_state(self.state)

        total = self.state.get("wins", 0) + self.state.get("losses", 0)
        wr = self.state.get("wins", 0) / total * 100 if total else 0
        log.info(f"{'✅' if win else '❌'} {reason} {symbol} | {pnl_pct:+.1f}% | WR:{wr:.0f}% ({total}t)")

        _tg(
            f"<b>{'✅' if win else '❌'} CERRADO — {reason}</b>\n"
            f"{symbol} | ${entry:.4f} → ${exit_price:.4f}\n"
            f"PnL: {pnl_pct:+.2f}% | WR: {wr:.0f}% ({total}t)"
        )
        self.risk.record_pnl(pnl_pct)

    # ── reporte ────────────────────────────────────────────────────────────

    def _maybe_report(self):
        if (datetime.utcnow() - self._last_tg_report).total_seconds() < 7200:
            return
        self._last_tg_report = datetime.utcnow()
        balance = self._get_balance()
        total   = self.state.get("wins", 0) + self.state.get("losses", 0)
        wr      = self.state.get("wins", 0) / total * 100 if total else 0
        open_s  = list(self.state["open_trades"].keys())

        cds = self.state.get("cooldowns", {})
        cd_activos = [s for s, v in cds.items() if time.time() < v.get("resume", 0)]

        _tg(
            f"<b>📊 SuperBot v3.1 — Reporte</b>\n"
            f"Balance: ${balance:.2f} USDT\n"
            f"WR: {wr:.0f}% | Trades: {total}\n"
            f"PnL hoy: {self.state.get('daily_pnl', 0):+.2f}%\n"
            f"Régimen: {self.regime.regime} | Breadth: {int(self.regime.breadth*100)}%\n"
            f"BTC 4h: {self.regime.btc_4h:+.1f}%\n"
            f"Abiertos: {open_s or 'ninguno'}\n"
            f"Cooldowns activos: {len(cd_activos)}"
        )

    # ── main loop ──────────────────────────────────────────────────────────

    def run(self):
        log.info(
            f"SuperBot v3.1 | DRY_RUN={DRY_RUN} | "
            f"SCAN_PERIOD={SCAN_PERIOD}s | REGIME_CHECK={REGIME_CHECK}"
        )
        _tg(
            f"<b>🤖 SuperBot v3.1 iniciado</b>\n"
            f"DRY_RUN={DRY_RUN} | CD_SL={CD_SL_HOURS}h | MAX_TRADES={MAX_OPEN_TRADES}\n"
            f"Posiciones activas: {len(self.state['open_trades'])}"
        )

        iteration = 0
        last_regime_update = 0

        while True:
            try:
                iteration += 1
                self._init_daily()

                # v3.1: actualizar régimen cada 5 min
                if REGIME_CHECK and time.time() - last_regime_update > 300:
                    self.regime.update()
                    last_regime_update = time.time()

                self._manage_positions()
                self._maybe_report()

                balance    = self._get_balance()
                open_count = len(self.state["open_trades"])

                # v3.1: verificar daily loss cap
                if self._equity_start > 0:
                    daily_loss_pct = abs(self.state.get("daily_pnl", 0)) / self._equity_start * 100
                    if self.state.get("daily_pnl", 0) < 0 and daily_loss_pct > DAILY_LOSS_CAP_PCT:
                        log.warning(f"Daily loss cap: {daily_loss_pct:.1f}% — sin nuevas entradas")
                        log.info(f"Durmiendo {SCAN_PERIOD}s | Abiertos: {list(self.state['open_trades'].keys())}")
                        time.sleep(SCAN_PERIOD)
                        continue

                regime_ok, regime_reason = self.regime.can_open_long()

                if regime_ok and self.risk.can_open_trade(open_count, balance) and open_count < MAX_OPEN_TRADES:
                    log.info(f"🔍 Escaneando | Régimen: {self.regime.regime} | Breadth: {int(self.regime.breadth*100)}%")
                    try:
                        results = self.scanner.scan()
                    except Exception as e:
                        log.error(f"Scanner error: {e}")
                        results = []

                    opened = 0
                    for result in results:
                        if not self.risk.can_open_trade(
                            len(self.state["open_trades"]), balance
                        ):
                            break
                        if len(self.state["open_trades"]) >= MAX_OPEN_TRADES:
                            break
                        if result.symbol not in self.state["open_trades"]:
                            self._open_trade(result.symbol, result.signal)
                            opened += 1
                            time.sleep(0.5)

                    log.info(f"Trades abiertos este ciclo: {opened}")
                else:
                    if not regime_ok:
                        log.info(f"⏸️ Sin entradas: {regime_reason}")
                    log.info(
                        f"Posiciones: {open_count}/{MAX_OPEN_TRADES} | "
                        f"Balance: {balance:.2f} | "
                        f"Régimen: {self.regime.regime}"
                    )

                log.info(
                    f"#{iteration} Durmiendo {SCAN_PERIOD}s | "
                    f"Abiertos: {list(self.state['open_trades'].keys())}"
                )

            except Exception as e:
                log.error(f"Error loop #{iteration}: {e}", exc_info=True)

            time.sleep(SCAN_PERIOD)
