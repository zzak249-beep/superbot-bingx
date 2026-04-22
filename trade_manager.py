"""
Trade Manager v3 — Multi-TP, SL Dinámico, Límite Diario, DRY_RUN
=================================================================
Mejoras sobre v2:
  - TP1 cierra 60% de la posición (asegurar beneficio)
  - TP2 objetivo completo (proyección media)
  - SL dinámico basado en muros de order book
  - Trailing stop activado tras TP1
  - Máximo de trades diarios (MAX_DAILY_TRADES)
  - Modo DRY_RUN completo
  - Notificaciones Telegram en cada evento de trade
  - Kelly fraccional para sizing óptimo
"""

import asyncio
import logging
import os
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

from bingx_client import BingXClient
from reward_scheme import PBR

log = logging.getLogger("TRADE_MGR")

STATE_FILE    = Path("logs/open_trades.json")
DAILY_FILE    = Path("logs/daily_stats.json")

LEVERAGE        = int  (os.getenv("LEVERAGE",          "5"))
RISK_PCT        = float(os.getenv("RISK_PCT",           "0.01"))
TP_MULT         = float(os.getenv("TP_MULT",            "2.0"))
SL_MULT         = float(os.getenv("SL_MULT",            "1.0"))
MAX_TRADES      = int  (os.getenv("MAX_OPEN_TRADES",    "5"))
MAX_HOLD_H      = int  (os.getenv("MAX_HOLD_HOURS",     "48"))
MIN_NOTIONAL    = float(os.getenv("MIN_NOTIONAL",       "10"))
MAX_POSITION    = float(os.getenv("MAX_POSITION_SIZE",  "100"))  # USDT máximo por trade
MAX_DAILY_TRADES= int  (os.getenv("MAX_DAILY_TRADES",   "10"))
TP1_PCT         = float(os.getenv("TP1_PCT",            "0.60")) # % posición cerrada en TP1
MIN_RR          = float(os.getenv("MIN_RR",             "1.5"))  # R:R mínimo para abrir
TRAILING_AFTER_TP1 = os.getenv("TRAILING_AFTER_TP1", "true").lower() == "true"
DRY_RUN         = os.getenv("DRY_RUN", "false").lower() == "true"


class TradeManager:
    def __init__(self, client: BingXClient):
        self.client         = client
        self.trades: dict   = self._load_state()
        self.pbr:    dict   = {}
        self._daily         = self._load_daily()
        self._tp1_hit: set  = set()   # símbolos con TP1 ya ejecutado

    # ------------------------------------------------------------------ #
    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text())
            except Exception:
                pass
        return {}

    def _save_state(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.trades, indent=2, default=str))

    def _load_daily(self) -> dict:
        today = str(date.today())
        if DAILY_FILE.exists():
            try:
                data = json.loads(DAILY_FILE.read_text())
                if data.get("date") == today:
                    return data
            except Exception:
                pass
        return {"date": today, "trades_opened": 0, "total_pnl": 0.0, "wins": 0, "losses": 0}

    def _save_daily(self):
        DAILY_FILE.parent.mkdir(parents=True, exist_ok=True)
        DAILY_FILE.write_text(json.dumps(self._daily, indent=2, default=str))

    def _check_daily_reset(self):
        today = str(date.today())
        if self._daily.get("date") != today:
            self._daily = {"date": today, "trades_opened": 0, "total_pnl": 0.0, "wins": 0, "losses": 0}

    # ------------------------------------------------------------------ #
    async def open_trade(self, signal: dict) -> bool:
        self._check_daily_reset()

        sym = signal["symbol"]
        if sym in self.trades:
            log.debug(f"  Ya hay trade abierto en {sym}")
            return False

        if len(self.trades) >= MAX_TRADES:
            log.info(f"  Máximo de trades ({MAX_TRADES}) alcanzado")
            return False

        if self._daily["trades_opened"] >= MAX_DAILY_TRADES:
            log.info(f"  Máximo diario ({MAX_DAILY_TRADES}) alcanzado")
            return False

        # Filtro R:R mínimo
        rr = signal.get("risk_reward", 0)
        if rr < MIN_RR:
            log.info(f"  {sym}: R:R={rr:.2f} < mínimo {MIN_RR}, saltando")
            return False

        direction = signal["direction"]
        price     = signal["price"]
        mean_pnl  = signal["mean_pnl"]
        worst_pnl = signal["worst_pnl"]

        # TP/SL de la señal (ya calculados con OB si disponible)
        tp_price = signal.get("tp_price")
        sl_price = signal.get("sl_price")

        # Fallback si no vienen de la señal
        if not tp_price or not sl_price:
            if direction == "LONG":
                tp_price = round(price * (1 + abs(mean_pnl) * TP_MULT), 8)
                sl_price = round(price * (1 - abs(worst_pnl) * SL_MULT), 8)
            else:
                tp_price = round(price * (1 - abs(mean_pnl) * TP_MULT), 8)
                sl_price = round(price * (1 + abs(worst_pnl) * SL_MULT), 8)

        # --- Sizing con Kelly fraccional ---
        balance   = await self.client.get_balance() if not DRY_RUN else 10000.0
        if balance <= 0:
            log.error("Balance 0, no se puede operar")
            return False

        # Kelly fraccional: f = (p*b - q) / b
        # p = probabilidad win (proxy: signal_count con mean>0 / total_count)
        # b = mean_pnl / |worst_pnl|  (reward/risk)
        signal_count = signal.get("signal_count", 1)
        p_win  = max(0.3, min(0.8, 0.5 + signal.get("mean_pnl", 0) * 10))
        b_ratio = abs(mean_pnl) / max(abs(worst_pnl), 0.001)
        kelly  = (p_win * b_ratio - (1 - p_win)) / b_ratio
        kelly  = max(0.01, min(kelly, 0.25))   # clamped 1%-25%

        risk_usdt = min(balance * kelly * RISK_PCT * LEVERAGE, MAX_POSITION)
        quantity  = round(risk_usdt / price, 6)
        notional  = quantity * price

        if notional < MIN_NOTIONAL:
            log.info(f"  {sym}: notional {notional:.2f} USDT < {MIN_NOTIONAL}")
            return False

        # TP1: nivel intermedio al 50% del recorrido hacia TP2
        tp1_price = round(
            price + (tp_price - price) * 0.50,   # mitad del camino a TP2
            8
        )
        qty_tp1   = round(quantity * TP1_PCT, 6)  # cerrar 60% en TP1
        qty_tp2   = round(quantity - qty_tp1, 6)  # dejar 40% para TP2

        side, pos_side = ("BUY", "LONG") if direction == "LONG" else ("SELL", "SHORT")

        mode_str = "[DRY_RUN] " if DRY_RUN else ""
        log.info(
            f"{mode_str}📈 {direction} {sym} qty={quantity:.4f} (TP1:{qty_tp1:.4f}+TP2:{qty_tp2:.4f})  "
            f"entry={price}  TP1={tp1_price}  TP2={tp_price}  SL={sl_price}  "
            f"kelly={kelly:.2%}  R:R={rr:.2f}  notional={notional:.2f}$"
        )

        try:
            if not DRY_RUN:
                await self.client.set_leverage(sym, LEVERAGE, pos_side)
                await self.client.set_leverage(sym, LEVERAGE, "SHORT" if pos_side == "LONG" else "LONG")
                await asyncio.sleep(0.3)

            resp = await self.client.place_order(
                symbol   = sym,
                side     = side,
                pos_side = pos_side,
                quantity = quantity,
                tp_price = tp_price,
                sl_price = sl_price,
            )

            if resp.get("code", -1) != 0 and not DRY_RUN:
                log.error(f"  ❌ Error abriendo {sym}: {resp}")
                return False

            order_id = resp.get("data", {}).get("order", {}).get("orderId", "DRY")

            self.trades[sym] = {
                "symbol":     sym,
                "direction":  direction,
                "entry":      price,
                "quantity":   quantity,
                "qty_tp1":    qty_tp1,
                "qty_tp2":    qty_tp2,
                "tp1":        tp1_price,
                "tp":         tp_price,
                "sl":         sl_price,
                "order_id":   order_id,
                "opened_at":  datetime.utcnow().isoformat(),
                "tp1_hit":    False,
                "signal":     {k: v for k, v in signal.items() if k != "projections"},
            }
            self._save_state()

            self._daily["trades_opened"] += 1
            self._save_daily()

            pbr = PBR()
            pbr.on_action(1 if direction == "LONG" else 0)
            pbr.feed_price(price)
            self.pbr[sym] = pbr

            # Telegram alert
            emoji = "🟢" if direction == "LONG" else "🔴"
            await self.client.notify(
                f"{emoji} <b>TRADE ABIERTO</b> {mode_str}\n"
                f"💎 {sym} <b>{direction}</b>\n"
                f"📥 Entry: {price}\n"
                f"🎯 TP1: {tp1_price}  |  TP2: {tp_price}\n"
                f"🛑 SL: {sl_price}\n"
                f"⚖️ Kelly: {kelly:.2%}  |  R:R: {rr:.2f}\n"
                f"📊 Señal: {signal.get('signal_type','?')}  OB: {signal.get('ob_bias','?')}"
            )

            log.info(f"  ✅ Trade abierto {sym} orderId={order_id}")
            return True

        except Exception as e:
            log.error(f"  ❌ Excepción abriendo {sym}: {e}", exc_info=True)
            return False

    # ------------------------------------------------------------------ #
    async def manage_open_trades(self):
        if not self.trades:
            return

        positions = await self.client.get_positions() if not DRY_RUN else []
        pos_map   = {p["symbol"]: p for p in positions if float(p.get("positionAmt", 0)) != 0}

        to_close = []

        for sym, trade in list(self.trades.items()):
            opened_at = datetime.fromisoformat(trade["opened_at"])
            age_h     = (datetime.utcnow() - opened_at).total_seconds() / 3600
            direction = trade["direction"]

            # ¿Posición ya cerrada por TP/SL en exchange?
            if not DRY_RUN and sym not in pos_map:
                log.info(f"  🏁 {sym}: cerrada en exchange (TP/SL hit)")
                to_close.append(sym)
                continue

            pos         = pos_map.get(sym, {})
            pnl         = float(pos.get("unrealizedProfit", 0))
            mark_price  = float(pos.get("markPrice",  trade["entry"]))
            qty_left    = abs(float(pos.get("positionAmt", trade["quantity"])))

            if sym in self.pbr:
                self.pbr[sym].feed_price(mark_price)
                reward = self.pbr[sym].get_reward()
                rs     = self.pbr[sym].summary()
                log.info(
                    f"  📊 {sym} {direction}  price={mark_price}  PnL={pnl:.4f}$  "
                    f"age={age_h:.1f}h  PBR={rs['cumulative_reward']:.5f}  "
                    f"sharpe={rs['sharpe']:.3f}"
                )

            # ---- TP1 parcial -----------------------------------------
            if not trade.get("tp1_hit", False):
                tp1 = trade["tp1"]
                tp1_hit = (
                    (direction == "LONG"  and mark_price >= tp1) or
                    (direction == "SHORT" and mark_price <= tp1)
                )
                if tp1_hit:
                    log.info(f"  🎯 {sym}: TP1 alcanzado en {mark_price}  cerrando {trade['qty_tp1']:.4f}")
                    if not DRY_RUN:
                        await self.client.close_position(sym, direction, trade["qty_tp1"])
                    trade["tp1_hit"] = True
                    self._save_state()
                    self._tp1_hit.add(sym)
                    await self.client.notify(
                        f"🎯 <b>TP1 ALCANZADO</b>\n"
                        f"💎 {sym} {direction}\n"
                        f"💰 PnL parcial: +{pnl:.4f} USDT"
                    )

            # ---- Trailing stop tras TP1 --------------------------------
            if TRAILING_AFTER_TP1 and trade.get("tp1_hit") and not DRY_RUN:
                # Ajustar SL al precio de entrada (break-even mínimo)
                entry = trade["entry"]
                if direction == "LONG"  and trade["sl"] < entry:
                    trade["sl"] = entry * 1.001
                    self._save_state()
                elif direction == "SHORT" and trade["sl"] > entry:
                    trade["sl"] = entry * 0.999
                    self._save_state()

            # ---- Cierre por tiempo ----------------------------------------
            if age_h >= MAX_HOLD_H:
                log.info(f"  ⏰ {sym}: tiempo máximo, cerrando")
                if not DRY_RUN:
                    await self.client.close_position(sym, direction, qty_left)
                to_close.append(sym)
                await self.client.notify(
                    f"⏰ <b>CERRADO POR TIEMPO</b>\n"
                    f"💎 {sym}  PnL: {pnl:+.4f} USDT"
                )

        for sym in to_close:
            if sym in self.pbr:
                rs = self.pbr[sym].summary()
                log.info(
                    f"  🏆 {sym} PBR final: "
                    f"cum={rs['cumulative_reward']:.6f}  sharpe={rs['sharpe']:.3f}"
                )
                self.pbr[sym].reset()
                del self.pbr[sym]
            self._tp1_hit.discard(sym)
            self.trades.pop(sym, None)
        if to_close:
            self._save_state()

    # ------------------------------------------------------------------ #
    async def get_open_trades(self) -> list[dict]:
        result = []
        for sym, trade in self.trades.items():
            t = dict(trade)
            if sym in self.pbr:
                t["pbr"] = self.pbr[sym].summary()
            result.append(t)
        return result

    def get_daily_stats(self) -> dict:
        self._check_daily_reset()
        return self._daily
