"""
Trade Manager v7 — Gestión profesional de posiciones
======================================================
MEJORAS:
  - Apalancamiento dinámico según volumen y volatilidad del símbolo
  - Balance leído correctamente de futuros
  - Trailing stop profesional
  - Circuit breaker robusto
  - Cooldown inteligente por tipo de cierre
  - PnL tracking en tiempo real
  - Tamaño de posición basado en ATR (no solo pct fijo)
"""

import asyncio, logging, os, time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("TRADE_MGR")

# ── Config ──────────────────────────────────────────────────── #
DRY_RUN           = os.getenv("DRY_RUN",            "false").lower() == "true"
ACCOUNT_EQUITY    = float(os.getenv("ACCOUNT_EQUITY",   "100"))
RISK_PCT          = float(os.getenv("RISK_PCT",          "0.01"))   # 1%
MAX_OPEN          = int(os.getenv("MAX_OPEN_TRADES",     "3"))
MAX_DAILY         = int(os.getenv("MAX_DAILY_TRADES",    "6"))
MIN_TRADE_USDT    = float(os.getenv("MIN_TRADE_USDT",   "10"))
MAX_POS_USDT      = float(os.getenv("MAX_POSITION_SIZE","15"))
DAILY_LOSS_CAP    = float(os.getenv("DAILY_LOSS_CAP_PCT","8.0")) / 100
MAX_STREAK        = int(os.getenv("MAX_LOSING_STREAK",  "3"))
TP1_PCT           = float(os.getenv("TP1_PCT",          "40")) / 100  # cerrar 40% en TP1
TP2_PCT           = float(os.getenv("TP2_PCT",          "35")) / 100
TRAIL_RATE        = float(os.getenv("TRAIL_RATE_PCT",   "1.2")) / 100
TRAIL_ACT         = float(os.getenv("TRAIL_ACTIVATION", "1.0")) / 100
MAX_HOLD_H        = float(os.getenv("MAX_HOLD_HOURS",   "48"))
CB_PCT            = float(os.getenv("CIRCUIT_BREAKER_PCT","5.0")) / 100
CB_PAUSE_H        = float(os.getenv("CB_PAUSE_HOURS",   "4"))
COOLDOWN_TP_MIN   = int(os.getenv("COOLDOWN_TP_MIN",    "15"))
COOLDOWN_SL_MIN   = int(os.getenv("COOLDOWN_SL_MIN",    "240"))  # 4h tras SL

# Apalancamiento dinámico según categoría de coin
LEV_CONFIG = {
    "MAJOR":  int(os.getenv("LEV_MAJOR",  "5")),   # BTC, ETH, BNB
    "MID":    int(os.getenv("LEV_MID",    "3")),   # top-50 alts
    "SMALL":  int(os.getenv("LEV_SMALL",  "2")),   # altcoins pequeñas
}
MAJOR_COINS = {"BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT", "XRP-USDT"}
MID_COINS   = {"ADA-USDT","DOGE-USDT","AVAX-USDT","DOT-USDT","LINK-USDT",
               "MATIC-USDT","LTC-USDT","UNI-USDT","ATOM-USDT","NEAR-USDT",
               "OP-USDT","ARB-USDT","APT-USDT","SUI-USDT","INJ-USDT"}


@dataclass
class Trade:
    symbol:      str
    direction:   str        # LONG
    entry:       float
    sl:          float
    tp1:         float
    tp2:         float
    qty:         float      # cantidad en coin
    notional:    float      # qty * entry
    leverage:    int
    score:       float
    signal_type: str
    opened_at:   str        = ""
    tp1_hit:     bool       = False
    tp2_hit:     bool       = False
    trail_sl:    float      = 0.0
    trail_active:bool       = False
    pnl_usdt:    float      = 0.0
    order_id:    str        = ""
    peak_price:  float      = 0.0


class TradeManager:
    def __init__(self, client):
        self.client        = client
        self._trades: list[Trade] = []
        self._daily_pnl:   float  = 0.0
        self._daily_trades:int    = 0
        self._losing_streak:int   = 0
        self._paused_until:float  = 0.0
        self._pause_reason:str    = ""
        self._equity:      float  = ACCOUNT_EQUITY
        self._perf: dict          = {
            "total_trades": 0, "wins": 0, "losses": 0,
            "gross_profit": 0.0, "gross_loss": 0.0,
            "by_signal_type": {}
        }

    # ── Abrir trade ───────────────────────────────────────────── #

    async def open_trade(self, sig: dict) -> bool:
        sym = sig["symbol"]

        # Guards
        if self._is_paused():
            log.info(f"  Skip {sym}: circuit breaker activo")
            return False
        if len(self._trades) >= MAX_OPEN:
            log.debug(f"  Skip {sym}: max trades abiertos ({MAX_OPEN})")
            return False
        if self._daily_trades >= MAX_DAILY:
            log.info(f"  Skip {sym}: max trades diarios alcanzado")
            return False
        if any(t.symbol == sym for t in self._trades):
            log.debug(f"  Skip {sym}: ya hay posición abierta")
            return False

        # Balance real
        balance = await self.client.get_balance()
        if balance <= 0:
            log.error(f"TRADE_MGR - Balance {balance:.2f} USDT insuficiente. "
                      f"Verifica fondos en cuenta de FUTUROS BingX.")
            return False

        self._equity = max(balance, self._equity)

        # Apalancamiento dinámico
        leverage = self._dynamic_leverage(sym, sig.get("atr", 0), sig.get("price", 1))

        # Tamaño de posición basado en riesgo
        price  = sig["price"]
        sl     = sig["sl"]
        risk_u = self._equity * RISK_PCT              # $ en riesgo
        sl_pct = abs(price - sl) / price
        if sl_pct <= 0:
            return False

        pos_usdt = min(risk_u / sl_pct, MAX_POS_USDT, balance * 0.95)
        pos_usdt = max(pos_usdt, MIN_TRADE_USDT)

        if pos_usdt > balance * 0.95:
            log.warning(f"  {sym}: posición reducida a ${balance*0.95:.2f} (balance insuficiente)")
            pos_usdt = balance * 0.95

        qty = pos_usdt / price

        log.info(f"  {'[DRY]' if DRY_RUN else '[LIVE]'} OPEN {sym} {sig['direction']} "
                 f"entry={price:.6g} sl={sl:.6g} tp1={sig['tp1']:.6g} "
                 f"qty={qty:.4f} notional=${pos_usdt:.2f} lev={leverage}x "
                 f"score={sig['score']:.0f}")

        order_id = ""
        if not DRY_RUN:
            # Configurar apalancamiento
            await self.client.set_leverage(sym, leverage, "LONG")

            # Ejecutar orden
            result = await self.client.place_order(
                symbol    = sym,
                side      = "BUY",
                qty       = qty,
                order_type= "MARKET",
                sl        = sl,
                tp        = sig["tp1"],
            )
            if not result:
                log.error(f"  {sym}: orden fallida")
                return False
            order_id = str(result.get("orderId", ""))

        trade = Trade(
            symbol      = sym,
            direction   = "LONG",
            entry       = price,
            sl          = sl,
            tp1         = sig["tp1"],
            tp2         = sig["tp2"],
            qty         = qty,
            notional    = pos_usdt,
            leverage    = leverage,
            score       = sig["score"],
            signal_type = sig.get("signal_type", "COMBO"),
            opened_at   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            trail_sl    = sl,
            peak_price  = price,
            order_id    = order_id,
        )
        self._trades.append(trade)
        self._daily_trades += 1

        await self.client.notify(
            f"🟢 <b>TRADE ABIERTO</b> — {sym}\n"
            f"{'🔵 DRY RUN' if DRY_RUN else '🔴 LIVE'} | {leverage}x\n"
            f"Entry: ${price:.6g} | SL: ${sl:.6g}\n"
            f"TP1: ${sig['tp1']:.6g} | TP2: ${sig['tp2']:.6g}\n"
            f"Posición: ${pos_usdt:.2f} | Riesgo: ${risk_u:.2f}\n"
            f"Score: {sig['score']:.0f} | {', '.join(sig.get('active_sigs',[])[:3])}"
        )
        return True

    # ── Gestión de trades abiertos ────────────────────────────── #

    async def manage_open_trades(self):
        to_close = []
        for trade in self._trades:
            price = self.client.get_ws_price(trade.symbol) or 0
            if price <= 0:
                continue

            # PnL actual
            trade.pnl_usdt = (price - trade.entry) / trade.entry * trade.notional
            trade.peak_price = max(trade.peak_price, price)

            # Actualizar trailing stop
            if not trade.trail_active and price >= trade.entry * (1 + TRAIL_ACT):
                trade.trail_active = True
                log.info(f"  {trade.symbol}: Trailing stop activado en ${price:.6g}")

            if trade.trail_active:
                new_sl = price * (1 - TRAIL_RATE)
                if new_sl > trade.trail_sl:
                    trade.trail_sl = new_sl

            effective_sl = max(trade.sl, trade.trail_sl)

            # TP1 parcial
            if not trade.tp1_hit and price >= trade.tp1:
                trade.tp1_hit = True
                log.info(f"  {trade.symbol}: TP1 alcanzado ${price:.6g}")
                if not DRY_RUN:
                    await self.client.place_order(trade.symbol, "SELL",
                                                   trade.qty * TP1_PCT, "MARKET")
                await self.client.notify(
                    f"🎯 <b>TP1</b> {trade.symbol} @ ${price:.6g}\n"
                    f"PnL parcial: +${trade.pnl_usdt * TP1_PCT:.2f}"
                )

            # TP2 — cerrar resto
            if trade.tp1_hit and price >= trade.tp2:
                to_close.append((trade, "TP2", price))
                continue

            # Stop loss
            if price <= effective_sl:
                to_close.append((trade, "SL", price))
                continue

            # Tiempo máximo
            try:
                opened_dt = datetime.strptime(trade.opened_at, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                hours_held = (datetime.now(timezone.utc) - opened_dt).seconds / 3600
                if hours_held >= MAX_HOLD_H:
                    to_close.append((trade, "TIMEOUT", price))
            except Exception:
                pass

        for trade, reason, price in to_close:
            await self._close_trade(trade, reason, price)

    async def _close_trade(self, trade: Trade, reason: str, price: float):
        pnl = (price - trade.entry) / trade.entry * trade.notional

        # Si TP1 ya cobró parcial
        if trade.tp1_hit:
            remaining = 1 - TP1_PCT
            pnl = pnl * remaining + trade.notional * TP1_PCT * (trade.tp1 - trade.entry) / trade.entry
        
        win = pnl > 0

        log.info(f"  CLOSE {trade.symbol} [{reason}] "
                 f"entry={trade.entry:.6g} exit={price:.6g} "
                 f"pnl=${pnl:+.2f} ({'WIN' if win else 'LOSS'})")

        if not DRY_RUN:
            remaining_qty = trade.qty * (1 - TP1_PCT) if trade.tp1_hit else trade.qty
            await self.client.close_position(trade.symbol, "LONG", remaining_qty)

        self._trades.remove(trade)
        self._daily_pnl += pnl
        self._update_perf(trade, pnl, win)

        # Circuit breaker
        if win:
            self._losing_streak = 0
        else:
            self._losing_streak += 1
            if self._losing_streak >= MAX_STREAK:
                self._pause(f"{self._losing_streak} pérdidas consecutivas", CB_PAUSE_H)
            if self._daily_pnl <= -self._equity * DAILY_LOSS_CAP:
                self._pause(f"Daily loss cap: {self._daily_pnl:.2f}$", CB_PAUSE_H * 2)

        # Cooldown por símbolo
        from signal_engine import SignalEngine
        cool_min = COOLDOWN_TP_MIN if win else COOLDOWN_SL_MIN
        _DUMMY_ENGINE = SignalEngine()
        _DUMMY_ENGINE.set_cooldown(trade.symbol, cool_min)

        emoji = "✅" if win else "❌"
        await self.client.notify(
            f"{emoji} <b>TRADE CERRADO</b> [{reason}] — {trade.symbol}\n"
            f"Entry: ${trade.entry:.6g} → Exit: ${price:.6g}\n"
            f"PnL: ${pnl:+.2f} | Daily: ${self._daily_pnl:+.2f}\n"
            f"Racha pérdidas: {self._losing_streak}"
        )

    def _update_perf(self, trade: Trade, pnl: float, win: bool):
        p = self._perf
        p["total_trades"] += 1
        if win:
            p["wins"] += 1
            p["gross_profit"] += pnl
        else:
            p["losses"] += 1
            p["gross_loss"] += abs(pnl)

        st = trade.signal_type
        if st not in p["by_signal_type"]:
            p["by_signal_type"][st] = {"count": 0, "wins": 0, "pnl": 0.0}
        p["by_signal_type"][st]["count"] += 1
        if win:
            p["by_signal_type"][st]["wins"] += 1
        p["by_signal_type"][st]["pnl"] += pnl

    # ── Stats ─────────────────────────────────────────────────── #

    async def get_open_trades(self) -> list[dict]:
        result = []
        for t in self._trades:
            d = asdict(t)
            result.append(d)
        return result

    def get_daily_stats(self) -> dict:
        return {
            "daily_pnl":      round(self._daily_pnl, 2),
            "daily_trades":   self._daily_trades,
            "losing_streak":  self._losing_streak,
            "risk_paused":    self._is_paused(),
            "pause_reason":   self._pause_reason,
            "equity":         round(self._equity, 2),
        }

    def get_performance(self) -> dict:
        p   = self._perf
        tot = p["total_trades"]
        wr  = p["wins"] / tot * 100 if tot > 0 else 0
        pf  = p["gross_profit"] / p["gross_loss"] if p["gross_loss"] > 0 else 0
        bst = {}
        for st, v in p["by_signal_type"].items():
            bst[st] = {
                "count":    v["count"],
                "win_rate": v["wins"] / v["count"] * 100 if v["count"] > 0 else 0,
                "pnl":      round(v["pnl"], 2),
            }
        return {
            "total_trades":    tot,
            "win_rate":        round(wr, 1),
            "profit_factor":   round(pf, 2),
            "net_pnl":         round(p["gross_profit"] - p["gross_loss"], 2),
            "by_signal_type":  bst,
        }

    def reset_daily(self):
        self._daily_pnl    = 0.0
        self._daily_trades = 0

    # ── Helpers ───────────────────────────────────────────────── #

    def _dynamic_leverage(self, symbol: str, atr: float, price: float) -> int:
        """
        Apalancamiento dinámico:
        - BTC/ETH/BNB/SOL/XRP → hasta 5x
        - Top-50 altcoins      → hasta 3x
        - Resto (micro-caps)   → 2x máximo
        - Si ATR > 3% del precio → reducir un nivel más
        """
        if symbol in MAJOR_COINS:
            lev = LEV_CONFIG["MAJOR"]
        elif symbol in MID_COINS:
            lev = LEV_CONFIG["MID"]
        else:
            lev = LEV_CONFIG["SMALL"]

        # Reducir si ATR alto (alta volatilidad)
        if atr > 0 and price > 0:
            atr_pct = atr / price
            if atr_pct > 0.04:    # >4% ATR
                lev = max(1, lev - 2)
            elif atr_pct > 0.025:  # >2.5% ATR
                lev = max(1, lev - 1)

        return lev

    def _is_paused(self) -> bool:
        return time.time() < self._paused_until

    def _pause(self, reason: str, hours: float):
        self._paused_until = time.time() + hours * 3600
        self._pause_reason = reason
        log.warning(f"🛑 Circuit breaker: {reason} — pausa {hours}h")
