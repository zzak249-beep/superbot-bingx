"""
memoria_smc.py — Sistema de memoria y compounding para SMC Bot v2.0
Standalone: no depende de ningún otro módulo del bot.
Añadido: get_compounding_info() público para evitar acceso directo a _data
"""
import json
import os
import logging
from datetime import datetime, timezone

import config_smc as cfg

log = logging.getLogger("memoria")

_FNAME = os.path.join(cfg.MEMORY_DIR, "memoria_smc.json")

_DEFAULT = {
    "compounding":     {"ganancias": 0.0, "nivel": 0},
    "trades":          [],
    "pares_stats":     {},
    "errores_api":     {},
    "pares_bloq":      [],
    "inversion_total": 0.0,
}

_data: dict = {}


def _load():
    global _data
    try:
        if os.path.exists(_FNAME):
            with open(_FNAME, encoding="utf-8") as f:
                _data = json.load(f)
            for k, v in _DEFAULT.items():
                if k not in _data:
                    _data[k] = v
        else:
            _data = dict(_DEFAULT)
    except Exception as e:
        log.error(f"[MEM] load: {e}")
        _data = dict(_DEFAULT)


def _save():
    try:
        os.makedirs(os.path.dirname(_FNAME), exist_ok=True)
        with open(_FNAME, "w", encoding="utf-8") as f:
            json.dump(_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"[MEM] save: {e}")


_load()


# ══════════════════════════════════════════════════════
# COMPOUNDING
# ══════════════════════════════════════════════════════

def get_trade_amount() -> float:
    comp  = _data["compounding"]
    nivel = comp.get("nivel", 0)
    base  = cfg.TRADE_USDT_BASE + nivel * cfg.COMPOUND_ADD_USDT
    return min(round(base, 2), cfg.TRADE_USDT_MAX)


def get_compounding_info() -> dict:
    """Acceso público al estado del compounding (evita _data directo)."""
    return dict(_data.get("compounding", {"ganancias": 0.0, "nivel": 0}))


def registrar_ganancia_compounding(pnl: float):
    if pnl <= 0:
        return
    comp = _data["compounding"]
    comp["ganancias"] = round(comp.get("ganancias", 0) + pnl, 4)
    step = cfg.COMPOUND_STEP_USDT
    if step > 0:
        nuevo_nivel = int(comp["ganancias"] / step)
        if nuevo_nivel > comp.get("nivel", 0):
            comp["nivel"] = nuevo_nivel
            log.info(f"[COMP] Nivel {nuevo_nivel} — trade=${get_trade_amount():.2f}")
    _save()


def registrar_inversion(usdt: float):
    _data["inversion_total"] = round(_data.get("inversion_total", 0) + usdt, 4)
    _save()


# ══════════════════════════════════════════════════════
# TRADES
# ══════════════════════════════════════════════════════

def registrar_resultado(par: str, pnl: float, lado: str,
                         kz: str = "", motivos: list = None):
    ganado = pnl > 0
    trade  = {
        "ts":      datetime.now(timezone.utc).isoformat(),
        "par":     par,
        "lado":    lado,
        "pnl":     round(pnl, 4),
        "ganado":  ganado,
        "kz":      kz,
        "motivos": motivos or [],
    }
    _data["trades"].append(trade)
    if len(_data["trades"]) > 500:
        _data["trades"] = _data["trades"][-500:]

    ps = _data["pares_stats"].setdefault(par, {"trades": 0, "wins": 0, "pnl": 0.0})
    ps["trades"] += 1
    ps["wins"]   += int(ganado)
    ps["pnl"]     = round(ps["pnl"] + pnl, 4)

    if ganado:
        registrar_ganancia_compounding(pnl)
    else:
        ultimos = [t for t in _data["trades"][-6:] if t["par"] == par]
        if len(ultimos) >= 3 and all(not t["ganado"] for t in ultimos[-3:]):
            if par not in _data["pares_bloq"]:
                _data["pares_bloq"].append(par)
                log.warning(f"[MEM] {par} bloqueado por 3 pérdidas seguidas")
    _save()


def ajustar_score(par: str, score: int, kz: str = "", motivos: list = None) -> int:
    ps = _data["pares_stats"].get(par, {})
    t  = ps.get("trades", 0)
    if t < 3:
        return score
    wr = ps.get("wins", 0) / t
    if wr >= 0.65: return score + 1
    if wr <= 0.30: return score - 1
    return score


def esta_bloqueado(par: str) -> bool:
    return par in _data.get("pares_bloq", [])


def get_pares_bloqueados() -> list:
    return list(_data.get("pares_bloq", []))


def get_top_pares(n: int = 10) -> list:
    ps     = _data.get("pares_stats", {})
    ranked = sorted(ps.items(), key=lambda x: x[1].get("pnl", 0), reverse=True)
    return [p for p, _ in ranked[:n]]


def registrar_error_api(par: str):
    errs = _data["errores_api"]
    errs[par] = errs.get(par, 0) + 1
    if errs[par] >= 5 and par not in _data["pares_bloq"]:
        _data["pares_bloq"].append(par)
        log.warning(f"[MEM] {par} bloqueado por 5 errores API")
    _save()


def resumen() -> str:
    trades = _data["trades"]
    total  = len(trades)
    if total == 0:
        return "📊 Sin trades registrados"
    wins = sum(1 for t in trades if t["ganado"])
    pnl  = sum(t["pnl"] for t in trades)
    wr   = wins / total * 100
    comp = get_compounding_info()
    return (
        f"📊 *Memoria SMC Bot v2.0*\n"
        f"Trades: `{total}` | WR: `{wr:.1f}%`\n"
        f"PnL total: `${pnl:+.2f}`\n"
        f"Pool compounding: `${comp['ganancias']:.2f}`\n"
        f"Trade actual: `${get_trade_amount():.2f}`"
    )
