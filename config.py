"""Configuración por variables de entorno. Todas se limpian de comillas y espacios: una
comilla de más en Railway ya volteó un DRY_RUN a False en otro bot de la flota."""
from __future__ import annotations

import os

CODE_VERSION = "trend-bot 1.0.0"


def _raw(name, default=""):
    v = os.getenv(name, default)
    return str(v).strip().strip('"').strip("'").strip()


def s(name, default=""):
    return _raw(name, default)


def b(name, default=False):
    return _raw(name, "true" if default else "false").lower() in ("1", "true", "si", "sí", "yes", "on")


def f(name, default):
    try:
        return float(_raw(name, str(default)).replace(",", "."))
    except ValueError:
        return float(default)


def i(name, default):
    return int(f(name, default))


def lst(name, default=""):
    return [x.strip().upper() for x in _raw(name, default).split(",") if x.strip()]


class Config:
    def __init__(self):
        self.MODE = s("MODE", "SIGNAL").upper()                  # SIGNAL | DEMO | LIVE
        self.LIVE_CONFIRM = s("LIVE_CONFIRM", "").upper()        # segundo cerrojo: SI
        self.API_KEY = s("BINGX_API_KEY") or s("BINGX_KEY")
        self.API_SECRET = s("BINGX_API_SECRET") or s("BINGX_SECRET")
        self.ALLOC_USDT = f("ALLOC_USDT", 100)                   # capital asignado a ESTE bot
        self.N_COINS = i("N_COINS", 10)
        self.UNIVERSE_POOL = i("UNIVERSE_POOL", 40)              # candidatas por volumen 24h
        self.EXCLUDE = lst("EXCLUDE", "USDC,FDUSD,TUSD,DAI,USDE,PAXG,XAUT")
        self.VOL_TARGET = f("VOL_TARGET", 0.25)
        self.ALLOW_SHORT = b("ALLOW_SHORT", False)
        self.LEVERAGE = i("LEVERAGE", 3)                         # solo afecta al margen, no al riesgo
        self.REBAL_BAND = f("REBAL_BAND", 0.02)
        self.MIN_ORDER_USDT = f("MIN_ORDER_USDT", 5)
        self.MIN_HISTORY = i("MIN_HISTORY", 365)
        self.MAX_DRAWDOWN_PCT = f("MAX_DRAWDOWN_PCT", 30)        # interruptor: deja de abrir
        self.DISASTER_STOP = b("DISASTER_STOP", True)            # stop en el exchange contra crash
        self.DISASTER_VOL_MULT = f("DISASTER_VOL_MULT", 4.0)     # nº de volatilidades diarias
        self.DISASTER_MIN_PCT = f("DISASTER_MIN_PCT", 15)
        self.RUN_AT_UTC = s("RUN_AT_UTC", "00:10")
        self.STATE_DIR = s("STATE_DIR", "/data" if os.path.isdir("/data") else "state")
        self.TG_TOKEN = s("TELEGRAM_BOT_TOKEN") or s("TELEGRAM_TOKEN")
        self.TG_CHAT = s("TELEGRAM_CHAT_ID")
        self.RUN_NOW = b("RUN_NOW", False)                       # ejecutar al arrancar aunque ya se hiciera hoy

    @property
    def trading(self):
        return self.MODE in ("DEMO", "LIVE")

    def problems(self):
        p = []
        if self.MODE not in ("SIGNAL", "DEMO", "LIVE"):
            p.append(f"MODE={self.MODE} no válido (SIGNAL, DEMO o LIVE)")
        if self.trading and not (self.API_KEY and self.API_SECRET):
            p.append(f"MODE={self.MODE} necesita BINGX_API_KEY y BINGX_API_SECRET")
        if self.MODE == "LIVE" and self.LIVE_CONFIRM != "SI":
            p.append("MODE=LIVE necesita también LIVE_CONFIRM=SI (segundo cerrojo)")
        if self.ALLOC_USDT <= 0:
            p.append("ALLOC_USDT debe ser > 0")
        return p
