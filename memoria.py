"""
memoria.py — APEX Bot v7.0
Persistencia, compounding, aprendizaje y estadísticas.
"""
import json, logging, os
from datetime import datetime, timezone
import config

log = logging.getLogger("memoria")

_DATA_PATH = (
    os.path.join(config.MEMORY_DIR, "apex_memoria.json")
    if config.MEMORY_DIR else "apex_memoria.json"
)

if config.MEMORY_DIR:
    try:
        import pathlib
        pathlib.Path(config.MEMORY_DIR).mkdir(parents=True, exist_ok=True)
    except Exception as _e:
        log.warning(f"[MEM] mkdir error: {_e}")

_DEFAULT = {
    "trades": [],
    "compounding": {"ganancias": 0.0, "total_ganado": 0.0, "total_perdido": 0.0, "inversion_acumulada": 0.0},
    "pares_stats": {},
    "errores_api": {},
}
_data: dict = {}


def _load():
    global _data
    try:
        if os.path.exists(_DATA_PATH):
            with open(_DATA_PATH, encoding="utf-8") as f:
                loaded = json.load(f)
            _data = {**_DEFAULT, **loaded}
            comp = _data.setdefault("compounding", {})
            for k, v in _DEFAULT["compounding"].items():
                comp.setdefault(k, v)
            return
    except Exception as e:
        log.warning(f"[MEM] load error: {e}")
    _data = {"trades": [], "compounding": dict(_DEFAULT["compounding"]),
              "pares_stats": {}, "errores_api": {}}


def _save():
    try:
        with open(_DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.warning(f"[MEM] save error: {e}")


_load()


def get_trade_amount() -> float:
    base      = float(config.TRADE_USDT_BASE)
    max_amt   = float(config.TRADE_USDT_MAX)
    step      = float(config.COMPOUND_STEP_USDT)
    add       = float(config.COMPOUND_ADD_USDT)
    ganancias = float(_data["compounding"].get("ganancias", 0.0))

    cantidad = base + int(ganancias // step) * add if step > 0 and ganancias > 0 else base

    # Reducir en racha perdedora
    rec = _data.get("trades", [])[-5:]
    if len(rec) >= 3:
        perdidas = sum(1 for t in rec if not t.get("ganado"))
        if perdidas >= 4:   cantidad = max(cantidad * 0.7, base * 0.5)
        elif perdidas >= 3: cantidad = max(cantidad * 0.85, base * 0.5)

    return min(max(cantidad, base * 0.5), max_amt)


def registrar_ganancia_compounding(pnl: float):
    comp = _data["compounding"]
    if pnl > 0:
        comp["ganancias"]    = comp.get("ganancias", 0.0) + pnl
        comp["total_ganado"] = comp.get("total_ganado", 0.0) + pnl
    else:
        comp["total_perdido"] = comp.get("total_perdido", 0.0) + abs(pnl)
    _save()


def registrar_inversion(usdt: float):
    _data["compounding"]["inversion_acumulada"] = (
        _data["compounding"].get("inversion_acumulada", 0.0) + usdt
    )
    _save()


def registrar_resultado(par: str, pnl: float, lado: str, kz: str = "", motivos: list = None):
    ganado = pnl > 0
    _data["trades"].append({
        "par": par, "pnl": round(pnl, 6), "lado": lado,
        "kz": kz, "motivos": motivos or [],
        "ts": datetime.now(timezone.utc).isoformat(), "ganado": ganado,
    })
    if len(_data["trades"]) > 500:
        _data["trades"] = _data["trades"][-500:]

    stats = _data["pares_stats"].setdefault(par, {"trades": 0, "wins": 0, "pnl_total": 0.0, "errores": 0})
    stats["trades"]    += 1
    stats["wins"]      += int(ganado)
    stats["pnl_total"] += pnl

    registrar_ganancia_compounding(pnl)
    _save()
    log.info(f"[MEM] {par} {lado} PnL={pnl:+.4f} {'✅' if ganado else '❌'}")


def registrar_error_api(par: str):
    stats = _data["pares_stats"].setdefault(par, {"trades": 0, "wins": 0, "pnl_total": 0.0, "errores": 0})
    stats["errores"] = stats.get("errores", 0) + 1
    _save()


def esta_bloqueado(par: str) -> bool:
    if par in config.PARES_BLOQUEADOS:
        return True
    stats  = _data["pares_stats"].get(par, {})
    if stats.get("errores", 0) >= 5:
        return True
    trades = stats.get("trades", 0)
    wins   = stats.get("wins", 0)
    pnl    = stats.get("pnl_total", 0.0)
    if trades >= 5 and (wins / trades) < 0.20 and pnl < -10.0:
        return True
    return False


def get_pares_bloqueados() -> list:
    return [p for p in _data["pares_stats"] if esta_bloqueado(p)]


def get_top_pares(n: int = 10) -> list:
    ranked = sorted(
        [(p, s) for p, s in _data["pares_stats"].items()
         if s.get("trades", 0) >= 3 and not esta_bloqueado(p)],
        key=lambda x: x[1].get("pnl_total", 0.0), reverse=True,
    )
    return [p for p, _ in ranked[:n]]


def ajustar_score(par: str, score: int, kz: str = "", motivos: list = None) -> int:
    stats  = _data["pares_stats"].get(par, {})
    trades = stats.get("trades", 0)
    wins   = stats.get("wins", 0)
    pnl    = stats.get("pnl_total", 0.0)
    if trades < 3:
        return score
    wr = wins / trades
    if wr >= 0.65 and pnl > 0:   ajuste = +2
    elif wr >= 0.55 and pnl > 0: ajuste = +1
    elif wr <= 0.30 or (trades >= 5 and pnl < -5.0): ajuste = -2
    elif wr <= 0.40:              ajuste = -1
    else:                         ajuste = 0
    return max(score + ajuste, 0)


def resumen() -> str:
    comp   = _data["compounding"]
    trades = _data["trades"]
    total  = len(trades)
    wins   = sum(1 for t in trades if t.get("ganado"))
    wr     = f"{wins/total*100:.1f}%" if total > 0 else "N/A"
    pool   = comp.get("ganancias", 0.0)
    tot_g  = comp.get("total_ganado", 0.0)
    bloq   = len(get_pares_bloqueados())
    return (
        f"📚 *Memoria APEX* — {total} trades | WR: {wr}\n"
        f"💹 PnL total: `${tot_g:+.2f}` | Pool: `${pool:.2f}`\n"
        f"📊 Próx trade: `${get_trade_amount():.2f} USDT`\n"
        f"🚫 Pares bloqueados: `{bloq}`"
    )
