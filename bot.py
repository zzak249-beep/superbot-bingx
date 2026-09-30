"""
BOT DE TENDENCIA (diario) para perpetuos de BingX.

Una vez al día, tras el cierre de las 00:00 UTC:
  1. Elige la cesta: las N monedas más líquidas con al menos MIN_HISTORY días de historial.
  2. Calcula la señal de tendencia (9 canales Donchian) y el tamaño por volatilidad.
     Es EL MISMO código (engine.py) que mide backtest.py.
  3. Compara con lo que tiene y solo opera lo que se sale de la banda de reajuste.
  4. Pone un stop de emergencia en el exchange por posición (el sistema sale al cierre
     diario; el stop solo existe para un desplome entre dos ejecuciones).

Modos:  SIGNAL (por defecto: cartera en papel, no necesita claves)
        DEMO   (órdenes reales en la cuenta demo VST de BingX)
        LIVE   (dinero real; exige además LIVE_CONFIRM=SI)

Seguridad en cuenta compartida: el bot SOLO toca las posiciones que abrió él. Si una
moneda de la cesta ya tiene posición de otro bot o manual, la salta y avisa.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

import data
from bingx import BingX, BingXError
from config import CODE_VERSION, Config
from engine import Params, latest_targets, realized_vol


def log(msg):
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {msg}", flush=True)


class Telegram:
    def __init__(self, token, chat):
        self.token, self.chat = token, chat

    def send(self, text):
        log("TG: " + text.replace("\n", " | ")[:300])
        if not (self.token and self.chat):
            return
        for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)]:
            for k in range(3):
                try:
                    requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                                  data=dict(chat_id=self.chat, text=chunk), timeout=15)
                    break
                except requests.RequestException:
                    time.sleep(2 ** k)


class Bot:
    def __init__(self, cfg: Config, ex: BingX | None = None, loader=None, tg=None):
        self.cfg = cfg
        self.ex = ex or BingX(cfg.API_KEY, cfg.API_SECRET, demo=(cfg.MODE == "DEMO"))
        self.loader = loader or self._load_history
        self.tg = tg or Telegram(cfg.TG_TOKEN, cfg.TG_CHAT)
        os.makedirs(cfg.STATE_DIR, exist_ok=True)
        self.state_path = os.path.join(cfg.STATE_DIR, f"trend_state_{cfg.MODE.lower()}.json")
        self.journal_path = os.path.join(cfg.STATE_DIR, f"trend_journal_{cfg.MODE.lower()}.csv")
        self.state = self._load_state()

    # ───────── estado ─────────
    def _load_state(self):
        base = dict(last_run=None, owned={}, realized=0.0, peak=None, halted=False, lev_set=[],
                    paper=dict(cash=self.cfg.ALLOC_USDT, pos={}), runs=0)
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path) as f:
                    base.update(json.load(f))
            except (ValueError, OSError) as e:
                log(f"estado ilegible ({e}); empiezo de cero")
        return base

    def save(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1, default=str)
        os.replace(tmp, self.state_path)

    def journal(self, **row):
        new = not os.path.exists(self.journal_path)
        with open(self.journal_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["time", "day", "mode", "symbol", "action", "qty", "price",
                                              "notional", "target_w", "signal", "note"])
            if new:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in w.fieldnames})

    # ───────── datos ─────────
    def _load_history(self, base_sym, days):
        df, src = data.load_daily(base_sym, days, "bingx", cache_dir=os.path.join(self.cfg.STATE_DIR, "cache"))
        if len(df) < self.cfg.MIN_HISTORY:
            try:  # BingX corto de historial: completa con Binance (mismo precio) y BingX manda en lo reciente
                old, _ = data.load_daily(base_sym, days, "auto", cache_dir=os.path.join(self.cfg.STATE_DIR, "cache"))
                df = df.combine_first(old)
            except Exception:
                pass
        return df

    def universe(self):
        cons = self.ex.contracts()
        tick = self.ex.tickers()
        rows = []
        for sym, c in cons.items():
            if not sym.endswith("-USDT"):
                continue
            b_ = data.base(sym)
            if b_ in self.cfg.EXCLUDE:
                continue
            t = tick.get(sym) or {}
            qv = float(t.get("quoteVolume") or 0)
            if qv > 0:  # sin volumen = no cotiza (suspendida o deslistándose)
                rows.append((sym, qv))
        rows.sort(key=lambda x: -x[1])
        return [s for s, _ in rows[: self.cfg.UNIVERSE_POOL]]

    # ───────── cuenta ─────────
    def exchange_positions(self):
        """{symbol: {'side': 1/-1, 'qty': float, 'avg': float, 'upnl': float}} de toda la cuenta."""
        out = {}
        for p in self.ex.positions():
            amt = float(p.get("positionAmt") or 0)
            if amt == 0:
                continue
            side_txt = str(p.get("positionSide", "BOTH")).upper()
            side = -1 if side_txt == "SHORT" or (side_txt == "BOTH" and amt < 0) else 1
            out.setdefault(p["symbol"], []).append(dict(side=side, qty=abs(amt), avg=float(p.get("avgPrice") or 0),
                                                         upnl=float(p.get("unrealizedProfit") or 0)))
        return out

    # ───────── ejecución diaria ─────────
    def run_once(self, now=None):
        cfg = self.cfg
        now = now or datetime.now(timezone.utc)
        today = now.date().isoformat()
        p = Params(n_coins=cfg.N_COINS, vol_target=cfg.VOL_TARGET, allow_short=cfg.ALLOW_SHORT,
                   min_history=cfg.MIN_HISTORY, rebal_band=cfg.REBAL_BAND)
        notes, orders, errors = [], [], []

        # 1. cesta y datos
        pool = self.universe()
        frames = {}
        for sym in pool:
            try:
                df = self.loader(data.base(sym), max(cfg.MIN_HISTORY + 420, 800))
                if len(df) >= 60:
                    frames[sym] = df
            except Exception as e:
                errors.append(f"{sym}: datos ({str(e)[:60]})")
        if not frames:
            self.tg.send("⚠️ TREND: sin datos de ninguna moneda; no opero hoy")
            return
        weights, sigs, day = latest_targets(frames, p)
        expected = (now - timedelta(days=1)).date()
        if day.date() < expected - timedelta(days=1):
            notes.append(f"⚠ últimos datos del {day.date()} (esperaba {expected}): datos con retraso")
        vols = {s: realized_vol(frames[s]["close"].to_numpy(), p.vol_lookback)[-1] for s in frames}
        last_close = {s: float(frames[s]["close"].iloc[-1]) for s in frames}

        # 2. posiciones actuales y capital del bot
        owned = self.state["owned"]
        if cfg.trading:
            pos = self.exchange_positions()
            # posiciones del bot que ya no están (stop de emergencia ejecutado o cierre manual)
            for sym in list(owned):
                live = [x for x in pos.get(sym, []) if x["side"] == owned[sym]["side"]]
                if not live:
                    px = self._px(sym, last_close)
                    pnl = owned[sym]["side"] * (px - owned[sym]["avg"]) * owned[sym]["qty"]
                    self.state["realized"] += pnl
                    notes.append(f"🛑 {sym}: la posición ya no está en el exchange (stop o cierre manual) ≈ {pnl:+.2f} USDT")
                    self.journal(time=now.isoformat(), day=today, mode=cfg.MODE, symbol=sym, action="EXTERNAL_CLOSE",
                                 qty=owned[sym]["qty"], price=px, note="no encontrada en exchange")
                    del owned[sym]
            upnl = sum(x["upnl"] for s in owned for x in pos.get(s, []) if x["side"] == owned[s]["side"])
            equity = cfg.ALLOC_USDT + self.state["realized"] + upnl
            try:
                acct = self.ex.equity()
                if acct < equity:
                    notes.append(f"⚠ la cuenta tiene {acct:.2f} USDT, menos que el capital asignado; dimensiono con eso")
                    equity = acct
            except BingXError as e:
                errors.append(f"saldo: {e}")
        else:
            pos = {}
            pp = self.state["paper"]
            equity = pp["cash"] + sum(q * last_close.get(s, 0) for s, q in pp["pos"].items())

        # 3. interruptor de caída
        self.state["peak"] = max(self.state["peak"] or equity, equity)
        dd = 1 - equity / self.state["peak"] if self.state["peak"] else 0
        if dd * 100 >= cfg.MAX_DRAWDOWN_PCT and not self.state["halted"]:
            self.state["halted"] = True
            self.tg.send(f"🚨 TREND: caída del {dd*100:.1f}% desde máximos (límite {cfg.MAX_DRAWDOWN_PCT}%). "
                         "Dejo de ABRIR posiciones; solo reduzco. Para reactivar: borra 'halted' del estado o sube MAX_DRAWDOWN_PCT.")
        halted = self.state["halted"]

        # 4. órdenes
        symbols = sorted(set(weights[weights != 0].index) | set(owned) |
                         (set(self.state["paper"]["pos"]) if not cfg.trading else set()))
        for sym in symbols:
            try:
                w = float(weights.get(sym, 0.0))
                if sym not in frames:  # salió del pool de datos: cerrar lo nuestro
                    w = 0.0
                px = self._px(sym, last_close)
                if cfg.trading:
                    others = [x for x in pos.get(sym, []) if sym not in owned or x["side"] != owned[sym]["side"]]
                    if others and w != 0:
                        notes.append(f"⛔ {sym}: ya hay una posición que no es de este bot (manual u otro bot); la salto")
                        continue
                    cur_qty = owned.get(sym, {}).get("qty", 0.0) * owned.get(sym, {}).get("side", 0)
                    live = [x for x in pos.get(sym, []) if sym in owned and x["side"] == owned[sym]["side"]]
                    if live:
                        cur_qty = live[0]["qty"] * live[0]["side"]  # la verdad es el exchange
                else:
                    cur_qty = self.state["paper"]["pos"].get(sym, 0.0)
                target = w * equity
                cur = cur_qty * px
                if halted and (abs(target) > abs(cur) or np.sign(target) * np.sign(cur) < 0):
                    target = cur if np.sign(target) == np.sign(cur) else 0.0
                diff = target - cur
                full_close = target == 0 and cur_qty != 0
                if not full_close and abs(diff) < max(cfg.MIN_ORDER_USDT, cfg.REBAL_BAND * equity):
                    continue
                steps = []
                if cur_qty != 0 and (target == 0 or np.sign(target) != np.sign(cur_qty)):
                    steps.append(("close", -cur_qty))
                    if target != 0:
                        steps.append(("open", target / px))
                else:
                    steps.append(("adjust", diff / px))
                for k, (kind, dq) in enumerate(steps):
                    self._execute(sym, kind, dq, cur_qty, px, w, float(sigs.get(sym, 0)), today, k, orders, notes)
                    if kind == "close":
                        cur_qty = 0.0
            except Exception as e:
                errors.append(f"{sym}: {str(e)[:120]}")
                log(traceback.format_exc())

        # 5. stops de emergencia y estado final
        if cfg.trading:
            try:
                self._refresh_owned(vols, last_close, today, notes)
            except Exception as e:
                errors.append(f"stops/estado: {str(e)[:120]}")
        self.state["last_run"] = today
        self.state["runs"] += 1
        self.save()
        self._report(today, day, equity, dd, weights, sigs, orders, notes, errors, last_close)

    def _px(self, sym, last_close):
        if self.cfg.trading:
            try:
                return self.ex.price(sym)
            except Exception:
                pass
        return last_close.get(sym) or self.ex.price(sym)

    def _execute(self, sym, kind, dq, cur_qty, px, w, sig, today, k, orders, notes):
        cfg = self.cfg
        side_long = (cur_qty > 0) if kind == "close" else (dq > 0 if kind == "open" else (cur_qty > 0 or (cur_qty == 0 and dq > 0)))
        pos_side = "LONG" if side_long else "SHORT"
        if not cfg.trading:
            qty = abs(dq)
            if kind != "close" and qty * px < cfg.MIN_ORDER_USDT:
                return
            pp = self.state["paper"]
            fee = abs(dq) * px * 0.0008
            pp["cash"] -= dq * px + fee
            newq = pp["pos"].get(sym, 0.0) + dq
            if abs(newq) * px < 1e-6:
                pp["pos"].pop(sym, None)
            else:
                pp["pos"][sym] = newq
            orders.append(f"{'🟢' if dq > 0 else '🔴'} {sym} {kind} {dq:+.6g} @ {px:.6g} ({dq*px:+.1f} USDT)")
            self.journal(time=datetime.now(timezone.utc).isoformat(), day=today, mode=cfg.MODE, symbol=sym,
                         action=kind, qty=dq, price=px, notional=dq * px, target_w=w, signal=sig)
            self.save()
            return
        # ── real / demo ──
        if kind == "close":
            qty = abs(cur_qty)  # cantidad EXACTA del exchange: nada de restos
        else:
            qty = self.ex.round_qty(sym, dq)
        if qty <= 0:
            return
        if kind != "close" and not self.ex.min_ok(sym, qty, px):
            notes.append(f"· {sym}: {qty*px:.1f} USDT por debajo del mínimo de BingX; no opero")
            return
        buy = dq > 0
        reduce = kind == "close" or (kind == "adjust" and ((cur_qty > 0 and not buy) or (cur_qty < 0 and buy)))
        cid = f"tb{today.replace('-', '')[2:]}{data.base(sym)[:10]}{kind[0]}{k}"[:40]
        if self.ex.order_by_client_id(sym, cid):
            notes.append(f"· {sym}: la orden {cid} ya existía (reinicio); no la repito")
            return
        if not reduce and sym not in self.state["lev_set"]:
            self.ex.set_isolated(sym)
            self.ex.set_leverage(sym, cfg.LEVERAGE)
            self.state["lev_set"].append(sym)
        self.ex.market(sym, "BUY" if buy else "SELL", qty, pos_side, reduce=reduce, cid=cid)
        own = self.state["owned"].get(sym)
        if reduce and own:
            self.state["realized"] += own["side"] * (px - own["avg"]) * qty - qty * px * 0.0005
        if kind == "close":
            # fuera del registro: si luego se abre al otro lado, entra limpio con su lado
            old = self.state["owned"].pop(sym, None)
            if old and old.get("stop_oid"):
                self.ex.cancel(sym, old["stop_oid"])
        orders.append(f"{'🟢' if buy else '🔴'} {sym} {kind} {'+' if buy else '-'}{qty:g} @ ~{px:.6g} ({qty*px:.1f} USDT)")
        self.journal(time=datetime.now(timezone.utc).isoformat(), day=today, mode=cfg.MODE, symbol=sym,
                     action=kind, qty=qty if buy else -qty, price=px, notional=(qty if buy else -qty) * px,
                     target_w=w, signal=sig, note=cid)
        if not reduce:
            self.state["owned"].setdefault(sym, dict(side=1 if pos_side == "LONG" else -1, qty=0.0, avg=px, stop=None, stop_oid=None))
        # guardar YA: si el proceso muere antes de acabar, al reiniciar la posición sigue
        # siendo del bot (si no, la trataría como ajena y no la gestionaría nunca)
        self.save()

    def _refresh_owned(self, vols, last_close, today, notes):
        """Cantidades reales del exchange + stop de emergencia que solo sube (largos)."""
        cfg = self.cfg
        pos = self.exchange_positions()
        for sym in list(self.state["owned"]):
            o = self.state["owned"][sym]
            live = [x for x in pos.get(sym, []) if x["side"] == o["side"]]
            if o.get("stop_oid"):
                self.ex.cancel(sym, o["stop_oid"])
                o["stop_oid"] = None
            if not live:
                del self.state["owned"][sym]
                continue
            o["qty"], o["avg"] = live[0]["qty"], live[0]["avg"] or o["avg"]
            if not cfg.DISASTER_STOP:
                continue
            px = self._px(sym, last_close)
            dvol = (vols.get(sym) or 0.8) / math.sqrt(365)
            dist = max(cfg.DISASTER_MIN_PCT / 100, cfg.DISASTER_VOL_MULT * dvol)
            raw = px * (1 - dist) if o["side"] == 1 else px * (1 + dist)
            if o.get("stop"):
                raw = max(raw, o["stop"]) if o["side"] == 1 else min(raw, o["stop"])
            sp = self.ex.round_price(sym, raw)
            try:
                r = self.ex.stop_market(sym, "SELL" if o["side"] == 1 else "BUY", o["qty"], sp,
                                        "LONG" if o["side"] == 1 else "SHORT",
                                        cid=f"tbs{today.replace('-', '')[2:]}{data.base(sym)[:10]}{self.state['runs']}"[:40])
                oid = (r or {}).get("order", r or {}).get("orderId")
                o["stop"], o["stop_oid"] = sp, oid
            except BingXError as e:
                notes.append(f"⚠ {sym}: no pude poner el stop de emergencia ({e.msg})")

    # ───────── informe ─────────
    def _report(self, today, day, equity, dd, weights, sigs, orders, notes, errors, last_close):
        cfg = self.cfg
        held = weights[weights != 0].sort_values(ascending=False)
        lines = [f"📈 TREND · {cfg.MODE} · cierre {day:%Y-%m-%d}",
                 f"Capital del bot: {equity:.2f} USDT · caída desde máx {dd*100:.1f}%" + (" · 🚨 SOLO REDUCE" if self.state['halted'] else ""),
                 f"Exposición objetivo: {held.abs().sum()*100:.0f}% en {len(held)} monedas"]
        for s, w in held.items():
            bars = "▮" * int(round(abs(sigs.get(s, 0)) * 9))
            lines.append(f"  {s:<12} {w*100:5.1f}%  señal {bars:<9} {sigs.get(s,0):.2f}")
        if orders:
            lines += ["", "Órdenes:"] + ["  " + o for o in orders]
        else:
            lines.append("Sin cambios: nada se sale de la banda de reajuste.")
        if notes:
            lines += [""] + notes
        if errors:
            lines += ["", "Errores:"] + ["  " + e for e in errors[:15]]
        if cfg.MODE == "SIGNAL":
            lines.append("\n(modo SEÑAL: cartera en papel, no se envía nada a BingX)")
        self.tg.send("\n".join(lines))


# ───────── bucle principal ─────────
def next_run_due(state, now, run_at):
    hh, mm = (int(x) for x in run_at.split(":"))
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return state.get("last_run") != now.date().isoformat() and now >= due


def main():
    cfg = Config()
    tg = Telegram(cfg.TG_TOKEN, cfg.TG_CHAT)
    log(f"{CODE_VERSION} · MODE={cfg.MODE} · ALLOC={cfg.ALLOC_USDT} · N={cfg.N_COINS} · VOL={cfg.VOL_TARGET} · "
        f"SHORT={cfg.ALLOW_SHORT} · estado en {cfg.STATE_DIR}")
    probs = cfg.problems()
    if probs:
        msg = "⛔ TREND no arranca:\n" + "\n".join("• " + x for x in probs)
        tg.send(msg)
        while True:  # sin bucle de reinicios: espera a que corrijas las variables
            time.sleep(3600)
    bot = Bot(cfg, tg=tg)
    tg.send(f"✅ {CODE_VERSION} arrancado · modo {cfg.MODE} · capital asignado {cfg.ALLOC_USDT} USDT · "
            f"cesta {cfg.N_COINS} · ejecución diaria {cfg.RUN_AT_UTC} UTC · última: {bot.state.get('last_run')}")
    first = True
    last_beat = 0
    while True:
        now = datetime.now(timezone.utc)
        try:
            if (first and cfg.RUN_NOW) or next_run_due(bot.state, now, cfg.RUN_AT_UTC):
                log("ejecución diaria")
                bot.run_once(now)
        except Exception as e:
            log(traceback.format_exc())
            tg.send(f"❌ TREND error en la ejecución diaria: {str(e)[:300]}\nReintento en 15 min.")
            time.sleep(900)
        first = False
        if time.time() - last_beat > 3600:
            log(f"latido · última ejecución {bot.state.get('last_run')}")
            last_beat = time.time()
        time.sleep(60)


if __name__ == "__main__":
    main()
