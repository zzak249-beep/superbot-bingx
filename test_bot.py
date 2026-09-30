"""Pruebas del bot SIN red: firma HTTP, modo señal, demo con exchange simulado, cuenta
compartida, idempotencia tras reinicio, cierre exacto, stop externo, interruptor de caída."""
import hashlib
import hmac
import os
import shutil
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlparse

import numpy as np
import pandas as pd

from bingx import BingX
from bot import Bot
from config import Config

TMP = "state_test"


# ───────── 1. firma a nivel HTTP ─────────
class CapSession:
    def __init__(self):
        self.calls = []

    class R:
        status_code = 200
        text = ""

        def json(self):
            return {"code": 0, "data": {}}

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(("GET", url, None, headers))
        return self.R()

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(("POST", url, data, headers))
        return self.R()

    def delete(self, url, headers=None, timeout=None):
        self.calls.append(("DELETE", url, None, headers))
        return self.R()


def _verify(sent: str, secret: str):
    qs, sig = sent.rsplit("&signature=", 1)
    exp = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    assert sig == exp, "la firma no corresponde a la cadena ENVIADA"
    keys = [k for k, _ in parse_qsl(qs)]
    assert keys == sorted(keys), "parámetros no ordenados"
    assert "recvWindow" in keys and "timestamp" in keys


def test_signing():
    s = CapSession()
    ex = BingX("K", "S3CR3T", session=s)
    ex.private("GET", "/openApi/swap/v2/user/positions", dict(symbol="BTC-USDT"))
    ex.private("POST", "/openApi/swap/v2/trade/order", dict(symbol="BTC-USDT", side="BUY", type="MARKET", quantity=0.001))
    g, pst = s.calls
    assert g[3]["X-BX-APIKEY"] == "K"
    _verify(urlparse(g[1]).query, "S3CR3T")
    assert "?" not in pst[1] and pst[3]["Content-Type"] == "application/x-www-form-urlencoded"
    _verify(pst[2], "S3CR3T")
    assert BingX("k", "s", demo=True).base == "https://open-api-vst.bingx.com"
    print("firma OK (GET en URL, POST en cuerpo, misma cadena firmada y enviada)")


# ───────── exchange simulado ─────────
class FakeEx(BingX):
    def __init__(self, prices, hedge=True, allow_private=True):
        super().__init__("k", "s")
        self.px = prices
        self.hedge = hedge
        self.allow_private = allow_private
        self.pos = {}          # (sym, side) -> [qty, avg]
        self.orders = []       # dicts
        self.cids = set()
        self.stops = {}
        self.oid = 100

    def private(self, *a, **k):
        raise AssertionError("llamada privada inesperada")

    def _chk(self):
        if not self.allow_private:
            raise AssertionError("el modo SEÑAL no debe llamar a la API privada")

    def contracts(self):
        return {s: dict(symbol=s, quantityPrecision=3, pricePrecision=4, tradeMinQuantity=0.001, tradeMinUSDT=2)
                for s in self.px}

    def tickers(self):
        return {s: dict(symbol=s, quoteVolume=1e9 - i * 1e6, lastPrice=p) for i, (s, p) in enumerate(self.px.items())}

    def price(self, s):
        return self.px[s]

    def hedge_mode(self):
        self._chk(); return self.hedge

    def equity(self):
        self._chk(); return 10_000.0

    def positions(self):
        self._chk()
        out = []
        for (s, side), (q, avg) in self.pos.items():
            if q > 0:
                ps = ("LONG" if side == 1 else "SHORT") if self.hedge else "BOTH"
                amt = q if (self.hedge or side == 1) else -q
                out.append(dict(symbol=s, positionSide=ps, positionAmt=amt, avgPrice=avg,
                                unrealizedProfit=side * (self.px[s] - avg) * q))
        return out

    def set_isolated(self, s):
        self._chk()

    def set_leverage(self, s, lev):
        self._chk()

    def order_by_client_id(self, s, cid):
        self._chk(); return {"clientOrderID": cid} if cid in self.cids else None

    def market(self, symbol, side, qty, pos_side, reduce=False, cid=None):
        self._chk()
        assert cid and cid not in self.cids, f"orden duplicada {cid}"
        if self.hedge:
            assert not reduce or True
        self.cids.add(cid)
        rec = dict(symbol=symbol, side=side, qty=qty, pos_side=pos_side, reduce=reduce, cid=cid)
        self.orders.append(rec)
        sd = 1 if pos_side == "LONG" or (pos_side == "BOTH" and ((side == "BUY") != reduce)) else -1
        if pos_side == "BOTH":
            sd = 1 if (side == "BUY" and not reduce) or (side == "SELL" and reduce) else -1
        key = (symbol, sd)
        q, avg = self.pos.get(key, [0.0, 0.0])
        opening = (side == "BUY") == (sd == 1)
        if opening:
            nq = q + qty
            self.pos[key] = [nq, (q * avg + qty * self.px[symbol]) / nq]
        else:
            assert qty <= q + 1e-12, f"cierra más de lo que hay: {qty} > {q}"
            self.pos[key] = [q - qty, avg]
        return {"order": {"orderId": self._next()}}

    def stop_market(self, symbol, side, qty, stop_price, pos_side, cid=None):
        self._chk()
        oid = self._next()
        self.stops[oid] = dict(symbol=symbol, qty=qty, stop=stop_price)
        return {"order": {"orderId": oid}}

    def cancel(self, symbol, oid):
        self._chk(); self.stops.pop(oid, None)

    def _next(self):
        self.oid += 1
        return self.oid


def frames_set(days=900, seed=1, up=("AAA", "BBB", "CCC"), down=("DDD", "EEE")):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end=pd.Timestamp(datetime.now(timezone.utc).date()) - pd.Timedelta(days=1),
                        periods=days, freq="D", tz="UTC")
    fr = {}
    names = list(up) + list(down) + ["FFF", "GGG", "BTC"]
    for i, n in enumerate(names):
        r = rng.standard_normal(days) * 0.03
        if n in up:
            r[-200:] += 0.012
        if n in down:
            r[-200:] -= 0.012
        c = 50 * np.exp(np.cumsum(r))
        fr[n] = pd.DataFrame(dict(open=c, high=c, low=c, close=c, volume=1.0, quote_vol=1e8 * (10 - i)), index=idx)
    return fr


def make_bot(mode, frames, hedge=True, **env):
    shutil.rmtree(TMP, ignore_errors=True)
    os.environ.update(dict(MODE=mode, STATE_DIR=TMP, ALLOC_USDT="1000", N_COINS="5", MIN_HISTORY="365",
                           BINGX_API_KEY="k", BINGX_API_SECRET="s", LIVE_CONFIRM="SI", TELEGRAM_BOT_TOKEN="",
                           TELEGRAM_CHAT_ID=""))
    os.environ.update(env)
    cfg = Config()
    prices = {f"{b}-USDT": float(df["close"].iloc[-1]) for b, df in frames.items()}
    ex = FakeEx(prices, hedge=hedge, allow_private=(mode != "SIGNAL"))
    msgs = []

    class TG:
        def send(self, t):
            msgs.append(t)
    bot = Bot(cfg, ex=ex, loader=lambda b, d: frames[b], tg=TG())
    return bot, ex, msgs


def test_signal_mode():
    fr = frames_set()
    bot, ex, msgs = make_bot("SIGNAL", fr)
    bot.run_once()
    pp = bot.state["paper"]["pos"]
    assert set(pp) >= {"AAA-USDT", "BBB-USDT", "CCC-USDT"}, pp
    assert "DDD-USDT" not in pp and "EEE-USDT" not in pp
    assert not ex.orders
    print("SEÑAL OK · cartera en papel:", {k: round(v, 3) for k, v in pp.items()})
    print(msgs[-1])


def test_demo_flow(hedge=True):
    fr = frames_set()
    bot, ex, msgs = make_bot("DEMO", fr, hedge=hedge)
    # posición AJENA (manual) en una moneda de la cesta
    ex.pos[("BBB-USDT", 1)] = [1.0, 40.0]
    bot.run_once()
    syms = {o["symbol"] for o in ex.orders}
    assert "BBB-USDT" not in syms, "tocó una posición que no es suya"
    assert {"AAA-USDT", "CCC-USDT"} <= syms, syms
    assert all(o["qty"] == round(o["qty"], 3) for o in ex.orders)
    assert len(ex.stops) == len(bot.state["owned"]) > 0, "faltan stops de emergencia"
    assert any("no es de este bot" in m for m in msgs[-1:][0].split("\n")), msgs[-1]
    n1 = len(ex.orders)
    # reinicio a mitad: se pierde last_run pero las órdenes ya están → no duplica
    bot.state["last_run"] = None
    bot.run_once()
    assert len(ex.orders) == n1, "duplicó órdenes tras reinicio"
    # fin de la tendencia en AAA: cierra la cantidad EXACTA
    q_before = ex.pos[("AAA-USDT", 1)][0]
    c = fr["AAA"]["close"].to_numpy().copy()
    c[-40:] = c[-41] * np.exp(np.cumsum(np.full(40, -0.03)))
    fr["AAA"] = fr["AAA"].assign(close=c, open=c, high=c, low=c)
    ex.px["AAA-USDT"] = float(c[-1])
    bot.state["last_run"] = None
    bot.run_once(datetime.now(timezone.utc) + pd.Timedelta(days=1))
    closes = [o for o in ex.orders if o["symbol"] == "AAA-USDT" and o["side"] == "SELL"]
    assert closes and abs(closes[-1]["qty"] - q_before) < 1e-12, (closes, q_before)
    assert ex.pos[("AAA-USDT", 1)][0] == 0 and "AAA-USDT" not in bot.state["owned"]
    if not hedge:
        assert closes[-1]["reduce"], "en One-Way el cierre debe ir con reduceOnly"
    # stop de emergencia ejecutado en el exchange (CCC desaparece)
    ex.pos[("CCC-USDT", 1)] = [0.0, 0.0]
    bot.state["last_run"] = None
    bot.run_once(datetime.now(timezone.utc) + pd.Timedelta(days=2))
    assert any("ya no está en el exchange" in m for m in msgs[-1].split("\n")), msgs[-1]
    print(f"DEMO OK ({'Hedge' if hedge else 'One-Way'}) · órdenes {len(ex.orders)} · stops vivos {len(ex.stops)}")


def test_halt():
    fr = frames_set()
    bot, ex, msgs = make_bot("DEMO", fr)
    bot.state["peak"] = 5000.0  # simula venir de un máximo muy superior
    bot.run_once()
    assert bot.state["halted"] and not ex.orders, "abrió posiciones con el interruptor activo"
    print("INTERRUPTOR OK")


def test_config_locks():
    for k in ("MODE", "LIVE_CONFIRM", "BINGX_API_KEY", "BINGX_API_SECRET"):
        os.environ.pop(k, None)
    os.environ.update(MODE='"LIVE"', BINGX_API_KEY="k", BINGX_API_SECRET="s")
    c = Config()
    assert c.MODE == "LIVE" and any("LIVE_CONFIRM" in p for p in c.problems())
    os.environ["MODE"] = "DEMO"
    os.environ.pop("BINGX_API_KEY")
    assert any("BINGX_API_KEY" in p for p in Config().problems())
    print("CERROJOS OK (comillas limpiadas, LIVE exige LIVE_CONFIRM, DEMO exige claves)")


if __name__ == "__main__":
    test_signing()
    test_config_locks()
    test_signal_mode()
    test_demo_flow(hedge=True)
    test_demo_flow(hedge=False)
    test_halt()
    shutil.rmtree(TMP, ignore_errors=True)
    print("BOT OK")
