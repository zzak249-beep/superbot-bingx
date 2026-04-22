"""
Trade Manager v5 — TP Parcial, Performance Tracking, Circuit Breakers
======================================================================
Mejoras vs v1:
  - Take Profit parcial (TP1 40%, TP2 35%, trailing 25%)
  - Performance tracking (win rate, profit factor, por tipo de señal)
  - Daily stats y circuit breakers
  - Risk management mejorado
"""

import asyncio
import logging
import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from collections import defaultdict

from bingx_client import BingXClient
from reward_scheme import PBR

log = logging.getLogger("TRADE_MGR")

STATE_FILE = Path("logs/open_trades.json")
HISTORY_FILE = Path("logs/trade_history.json")

# Config
LEVERAGE = int(os.getenv("LEVERAGE", "5"))
RISK_PCT = float(os.getenv("RISK_PCT", "0.01"))
TP1_RATIO = float(os.getenv("TP1_RATIO", "2.5"))
TP2_RATIO = float(os.getenv("TP2_RATIO", "4.0"))
TP1_PCT = float(os.getenv("TP1_PCT", "40"))  # % de posición a cerrar en TP1
TP2_PCT = float(os.getenv("TP2_PCT", "35"))  # % en TP2
MAX_TRADES = int(os.getenv("MAX_OPEN_TRADES", "3"))
MAX_HOLD_H = int(os.getenv("MAX_HOLD_HOURS", "48"))
MIN_NOTIONAL = float(os.getenv("MIN_TRADE_USDT", "10"))
SL_MULT = float(os.getenv("SL_MULT", "1.0"))
MIN_RR = float(os.getenv("MIN_RR", "2.0"))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# Circuit Breakers
MAX_DAILY_LOSS_PCT = float(os.getenv("DAILY_LOSS_CAP_PCT", "5.0"))
MAX_LOSING_STREAK = int(os.getenv("MAX_LOSING_STREAK", "3"))


class TradeManager:
    def __init__(self, client: BingXClient):
        self.client = client
        self.trades: dict[str, dict] = self._load_state()
        self.history: list[dict] = self._load_history()
        self.pbr: dict[str, PBR] = {}
        
        # Risk management state
        self._losing_streak = 0
        self._risk_paused = False
        self._pause_until: Optional[datetime] = None
        self._daily_start_balance = None
        self._last_reset_date = None
    
    # ================================================================== #
    #  State Persistence
    # ================================================================== #
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
    
    def _load_history(self) -> list:
        if HISTORY_FILE.exists():
            try:
                return json.loads(HISTORY_FILE.read_text())
            except Exception:
                pass
        return []
    
    def _save_history(self):
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Guardar solo últimos 500 trades
        HISTORY_FILE.write_text(
            json.dumps(self.history[-500:], indent=2, default=str)
        )
    
    # ================================================================== #
    #  Public API
    # ================================================================== #
    async def open_trade(self, signal: dict) -> bool:
        """Abre un nuevo trade basado en la señal."""
        sym = signal["symbol"]
        
        # Checks
        if sym in self.trades:
            log.debug(f"Trade ya abierto en {sym}")
            return False
        
        if len(self.trades) >= MAX_TRADES:
            log.info(f"Máximo de trades ({MAX_TRADES}) alcanzado")
            return False
        
        # Circuit breaker
        if self._risk_paused:
            if self._pause_until and datetime.utcnow() < self._pause_until:
                log.warning(f"Risk paused hasta {self._pause_until}")
                return False
            else:
                self._risk_paused = False
                self._pause_until = None
        
        # Risk:Reward check
        if signal.get("risk_reward", 0) < MIN_RR:
            log.debug(f"{sym}: R:R {signal.get('risk_reward',0):.2f} < {MIN_RR}")
            return False
        
        direction = signal["direction"]
        price = signal["price"]
        mean_pnl = signal["mean_pnl"]
        worst_pnl = signal["worst_pnl"]
        
        # Sizing
        balance = await self.client.get_balance()
        if balance <= 0:
            log.error("Balance 0")
            return False
        
        risk_usdt = balance * RISK_PCT * LEVERAGE
        quantity = round(risk_usdt / price, 6)
        notional = quantity * price
        
        if notional < MIN_NOTIONAL:
            log.info(f"{sym}: notional {notional:.2f} < {MIN_NOTIONAL}")
            return False
        
        # TP/SL
        if direction == "LONG":
            tp1 = round(price * (1 + abs(mean_pnl) * TP1_RATIO), 6)
            tp2 = round(price * (1 + abs(mean_pnl) * TP2_RATIO), 6)
            sl = round(price * (1 - abs(worst_pnl) * SL_MULT), 6)
            side, pos_side = "BUY", "LONG"
        else:
            tp1 = round(price * (1 - abs(mean_pnl) * TP1_RATIO), 6)
            tp2 = round(price * (1 - abs(mean_pnl) * TP2_RATIO), 6)
            sl = round(price * (1 + abs(worst_pnl) * SL_MULT), 6)
            side, pos_side = "SELL", "SHORT"
        
        prefix = "[DRY RUN] " if DRY_RUN else ""
        log.info(f"{prefix}Abriendo {direction} {sym}  qty={quantity}  "
                 f"entry={price}  TP1={tp1}  TP2={tp2}  SL={sl}")
        
        if DRY_RUN:
            order_id = f"DRY_{int(datetime.utcnow().timestamp())}"
        else:
            try:
                await self.client.set_leverage(sym, LEVERAGE, pos_side)
                await asyncio.sleep(0.3)
                
                resp = await self.client.place_order(
                    symbol=sym, side=side, pos_side=pos_side,
                    quantity=quantity, sl_price=sl
                )
                
                if resp.get("code", -1) != 0:
                    log.error(f"Error: {resp}")
                    return False
                
                order_id = resp.get("data", {}).get("order", {}).get("orderId", "?")
            except Exception as e:
                log.error(f"Exception: {e}", exc_info=True)
                return False
        
        # Guardar trade
        self.trades[sym] = {
            "symbol": sym,
            "direction": direction,
            "entry": price,
            "quantity": quantity,
            "tp1": tp1,
            "tp2": tp2,
            "sl": sl,
            "tp1_hit": False,
            "tp2_hit": False,
            "remaining_qty": quantity,
            "order_id": order_id,
            "opened_at": datetime.utcnow().isoformat(),
            "signal": {k: v for k, v in signal.items() if k != "projections"},
            "dry_run": DRY_RUN,
        }
        self._save_state()
        
        # PBR
        pbr = PBR()
        pbr.on_action(1 if direction == "LONG" else 0)
        pbr.feed_price(price)
        self.pbr[sym] = pbr
        
        log.info(f"✅ Trade abierto {sym} #{order_id}")
        return True
    
    # ------------------------------------------------------------------ #
    async def manage_open_trades(self):
        """Gestiona trades abiertos (TP parcial, SL, timeout)."""
        if not self.trades:
            return
        
        if DRY_RUN:
            # En DRY RUN solo simulamos timeout
            to_close = []
            for sym, t in list(self.trades.items()):
                age_h = (datetime.utcnow() - 
                        datetime.fromisoformat(t["opened_at"])).total_seconds() / 3600
                if age_h >= MAX_HOLD_H:
                    log.info(f"[DRY RUN] {sym} timeout")
                    to_close.append(sym)
            
            for sym in to_close:
                self._close_trade(sym, "TIMEOUT", 0)
            return
        
        # REAL MODE
        positions = await self.client.get_positions()
        pos_map = {p["symbol"]: p for p in positions 
                   if float(p.get("positionAmt", 0)) != 0}
        
        to_close = []
        
        for sym, t in list(self.trades.items()):
            # Posición cerrada en exchange
            if sym not in pos_map:
                log.info(f"{sym}: cerrada en exchange")
                self._close_trade(sym, "SL/TP", 0)
                continue
            
            pos = pos_map[sym]
            current_price = float(pos.get("markPrice", t["entry"]))
            unrealized_pnl = float(pos.get("unrealizedProfit", 0))
            
            # Update PBR
            if sym in self.pbr:
                self.pbr[sym].feed_price(current_price)
                self.pbr[sym].get_reward()
            
            # Age check
            age_h = (datetime.utcnow() - 
                    datetime.fromisoformat(t["opened_at"])).total_seconds() / 3600
            
            # TP parcial check
            direction = t["direction"]
            tp1 = t["tp1"]
            tp2 = t["tp2"]
            
            # TP1
            if not t["tp1_hit"]:
                tp1_hit = (direction == "LONG" and current_price >= tp1) or \
                         (direction == "SHORT" and current_price <= tp1)
                if tp1_hit:
                    close_qty = t["quantity"] * (TP1_PCT / 100)
                    await self._close_partial(sym, close_qty, "TP1")
                    t["tp1_hit"] = True
                    t["remaining_qty"] -= close_qty
                    self._save_state()
            
            # TP2
            if not t["tp2_hit"]:
                tp2_hit = (direction == "LONG" and current_price >= tp2) or \
                         (direction == "SHORT" and current_price <= tp2)
                if tp2_hit:
                    close_qty = t["quantity"] * (TP2_PCT / 100)
                    await self._close_partial(sym, close_qty, "TP2")
                    t["tp2_hit"] = True
                    t["remaining_qty"] -= close_qty
                    self._save_state()
            
            # Timeout
            if age_h >= MAX_HOLD_H:
                log.info(f"{sym}: timeout {age_h:.1f}h")
                to_close.append(sym)
        
        for sym in to_close:
            await self._close_full(sym, "TIMEOUT")
    
    # ------------------------------------------------------------------ #
    async def _close_partial(self, sym: str, qty: float, reason: str):
        """Cierra parte de la posición."""
        t = self.trades[sym]
        side = "SELL" if t["direction"] == "LONG" else "BUY"
        
        log.info(f"Closing {qty} of {sym} ({reason})")
        
        try:
            await self.client.close_position(sym, t["direction"], qty)
        except Exception as e:
            log.error(f"Error closing partial {sym}: {e}")
    
    async def _close_full(self, sym: str, reason: str):
        """Cierra la posición completamente y guarda en historial."""
        t = self.trades[sym]
        
        try:
            await self.client.close_position(
                sym, t["direction"], t["remaining_qty"]
            )
        except Exception as e:
            log.error(f"Error closing {sym}: {e}")
        
        # Calcular PnL (simulado en DRY RUN)
        pnl = 0  # TODO: calcular PnL real desde exchange
        
        self._close_trade(sym, reason, pnl)
    
    def _close_trade(self, sym: str, reason: str, pnl: float):
        """Mueve trade a historial y actualiza stats."""
        t = self.trades.pop(sym, None)
        if not t:
            return
        
        # PBR final
        if sym in self.pbr:
            pbr_summary = self.pbr[sym].summary()
            self.pbr[sym].reset()
            del self.pbr[sym]
        else:
            pbr_summary = {}
        
        # Historial
        t["closed_at"] = datetime.utcnow().isoformat()
        t["close_reason"] = reason
        t["pnl"] = pnl
        t["pbr_final"] = pbr_summary
        self.history.append(t)
        
        # Update stats
        if pnl < 0:
            self._losing_streak += 1
        else:
            self._losing_streak = 0
        
        # Circuit breaker check
        if self._losing_streak >= MAX_LOSING_STREAK:
            self._risk_paused = True
            self._pause_until = datetime.utcnow() + timedelta(hours=4)
            log.warning(f"⚠️ Circuit breaker: {self._losing_streak} pérdidas. "
                       f"Paused hasta {self._pause_until}")
        
        self._save_state()
        self._save_history()
        
        log.info(f"🏁 {sym} cerrado ({reason})  PnL: {pnl:.2f}  "
                 f"PBR: {pbr_summary.get('cumulative_reward',0):.5f}")
    
    # ================================================================== #
    #  Stats / Performance
    # ================================================================== #
    async def get_open_trades(self) -> list[dict]:
        """Devuelve trades abiertos con PBR."""
        result = []
        for sym, t in self.trades.items():
            t_copy = dict(t)
            if sym in self.pbr:
                t_copy["pbr"] = self.pbr[sym].summary()
            result.append(t_copy)
        return result
    
    def get_daily_stats(self) -> dict:
        """Stats diarias para circuit breaker."""
        today = datetime.utcnow().date()
        
        # Reset diario
        if self._last_reset_date != today:
            self._last_reset_date = today
            self._daily_start_balance = None  # Se actualizará en próximo check
        
        # Filtrar trades de hoy
        today_trades = [
            t for t in self.history
            if datetime.fromisoformat(t["closed_at"]).date() == today
        ]
        
        daily_pnl = sum(t.get("pnl", 0) for t in today_trades)
        
        return {
            "date": str(today),
            "trades_count": len(today_trades),
            "daily_pnl": daily_pnl,
            "losing_streak": self._losing_streak,
            "risk_paused": self._risk_paused,
            "pause_until": str(self._pause_until) if self._pause_until else None,
            "pause_reason": f"{self._losing_streak} pérdidas consecutivas" 
                           if self._risk_paused else None,
        }
    
    def get_performance(self) -> dict:
        """Performance general."""
        if not self.history:
            return {
                "total_trades": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "total_pnl": 0,
                "by_signal_type": {},
            }
        
        wins = [t for t in self.history if t.get("pnl", 0) > 0]
        losses = [t for t in self.history if t.get("pnl", 0) < 0]
        
        win_rate = (len(wins) / len(self.history)) * 100 if self.history else 0
        
        total_win = sum(t["pnl"] for t in wins)
        total_loss = abs(sum(t["pnl"] for t in losses))
        profit_factor = total_win / total_loss if total_loss > 0 else 0
        
        # Por tipo de señal
        by_type = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0})
        for t in self.history:
            sig_type = t.get("signal", {}).get("signal_type", "UNKNOWN")
            by_type[sig_type]["count"] += 1
            if t.get("pnl", 0) > 0:
                by_type[sig_type]["wins"] += 1
            by_type[sig_type]["pnl"] += t.get("pnl", 0)
        
        by_type_dict = {}
        for st, v in by_type.items():
            by_type_dict[st] = {
                "count": v["count"],
                "win_rate": (v["wins"] / v["count"]) * 100 if v["count"] > 0 else 0,
                "pnl": v["pnl"],
            }
        
        return {
            "total_trades": len(self.history),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "total_pnl": sum(t.get("pnl", 0) for t in self.history),
            "by_signal_type": by_type_dict,
        }
