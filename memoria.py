"""
memoria.py — Persistencia, compounding y aprendizaje del bot
SMC Bot v5.0 [MetaClaw Edition]
"""
import json
import logging
import os
from datetime import datetime, timezone

import config

log = logging.getLogger("memoria")

_DATA_PATH = (
    os.path.join(config.MEMORY_DIR, "bot_memoria.json")
    if config.MEMORY_DIR
    else "bot_memoria.json"
)

# ── FIX: crear el directorio ANTES de intentar leer/escribir ──────────────
if config.MEMORY_DIR:
    try:
        import pathlib
        pathlib.Path(config.MEMORY_DIR).mkdir(parents=True, exist_ok=True)
    except Exception as _e:
        log.warning(f"[MEM] No se pudo crear directorio {config.MEMORY_DIR}: {_e}")
# ─────────────────────────────────────────────────────────────────────────

_DEFAULT: dict = {
    "trades":       [],
    "compounding":  {
        "ganancias":             0.0,
        "total_ganado":          0.0,
        "total_perdido":         0.0,
        "inversion_acumulada":   0.0,
    },
    "pares_stats":  {},   # par → {trades, wins, pnl_total, errores}
    "errores_api":  {},   # par → count  (legacy, ahora en pares_stats)
}

_data: dict = {}


def _load():
    global _data
    try:
        if os.path.exists(_DATA_PATH):
            with open(_DATA_PATH, encoding="utf-8") as f:
                loaded = json.load(f)
            _data = {**_DEFAULT, **loaded}
            # Asegurar sub-claves de compounding
            comp = _data.setdefault("compounding", {})
            for k, v in _DEFAULT["compounding"].items():
                comp.setdefault(k, v)
            return
    except Exception as e:
        log.warning(f"[MEM] Error cargando {_DATA_PATH}: {e}")
    # Reset limpio
    _data = {
        "trades":      [],
        "compounding": dict(_DEFAULT["compounding"]),
        "pares_stats": {},
        "errores_api": {},
    }


def _save():
    try:
        with open(_DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.warning(f"[MEM] Error guardando: {e}")


_load()


# ═══════════════════════════════════════════════════════
# COMPOUNDING
# ═══════════════════════════════════════════════════════

def get_trade_amount() -> float:
    """
    Calcula el tamaño del próximo trade con compounding real:
    - Sube: cada COMPOUND_STEP_USDT ganados → +COMPOUND_ADD_USDT
    - Baja: racha perdedora reduce el tamaño (máx -30% del base)
    - Nunca supera TRADE_USDT_MAX ni baja de TRADE_USDT_BASE * 0.5
    """
    base      = float(config.TRADE_USDT_BASE)
    max_amt   = float(config.TRADE_USDT_MAX)
    step      = float(config.COMPOUND_STEP_USDT)
    add       = float(config.COMPOUND_ADD_USDT)
    ganancias = float(_data["compounding"].get("ganancias", 0.0))
    perdidas  = float(_data["compounding"].get("total_perdido", 0.0))

    # Subir según ganancias acumuladas
    if step > 0 and ganancias > 0:
        niveles  = int(ganancias // step)
        cantidad = base + niveles * add
    else:
        cantidad = base

    # Bajar si hay racha perdedora reciente (últimos 5 trades)
    trades_recientes = _data.get("trades", [])[-5:]
    if len(trades_recientes) >= 3:
        perdidas_recientes = sum(1 for t in trades_recientes if not t.get("ganado"))
        if perdidas_recientes >= 4:          # 4+ perdidos de los últimos 5
            cantidad = max(cantidad * 0.7, base * 0.5)
        elif perdidas_recientes >= 3:        # 3 perdidos de los últimos 5
            cantidad = max(cantidad * 0.85, base * 0.5)

    return min(max(cantidad, base * 0.5), max_amt)


def registrar_ganancia_compounding(pnl: float):
    comp = _data["compounding"]
    if pnl > 0:
        comp["ganancias"]    = comp.get("ganancias", 0.0) + pnl
        comp["total_ganado"] = comp.get("total_ganado", 0.0) + pnl
    else:
        comp["total_perdido"] = comp.get("total_perdido", 0.0) + abs(pnl)
        # Las pérdidas NO reducen el pool de compounding (reinversión solo de ganancias)
    _save()


def registrar_inversion(usdt: float):
    _data["compounding"]["inversion_acumulada"] = (
        _data["compounding"].get("inversion_acumulada", 0.0) + usdt
    )
    _save()


# ═══════════════════════════════════════════════════════
# TRADES
# ═══════════════════════════════════════════════════════

def registrar_resultado(par: str, pnl: float, lado: str,
                        kz: str = "", motivos: list = None):
    ganado = pnl > 0
    trade  = {
        "par":     par,
        "pnl":     round(pnl, 6),
        "lado":    lado,
        "kz":      kz,
        "motivos": motivos or [],
        "ts":      datetime.now(timezone.utc).isoformat(),
        "ganado":  ganado,
    }
    _data["trades"].append(trade)
    # Mantener máx 500 trades en disco
    if len(_data["trades"]) > 500:
        _data["trades"] = _data["trades"][-500:]

    stats = _data["pares_stats"].setdefault(par, {
        "trades": 0, "wins": 0, "pnl_total": 0.0, "errores": 0,
    })
    stats["trades"]    = stats.get("trades", 0) + 1
    stats["wins"]      = stats.get("wins", 0) + (1 if ganado else 0)
    stats["pnl_total"] = stats.get("pnl_total", 0.0) + pnl

    registrar_ganancia_compounding(pnl)
    _save()
    log.info(f"[MEM] {par} {lado} PnL={pnl:+.4f} {'✅' if ganado else '❌'}")


def registrar_error_api(par: str):
    stats = _data["pares_stats"].setdefault(par, {
        "trades": 0, "wins": 0, "pnl_total": 0.0, "errores": 0,
    })
    stats["errores"] = stats.get("errores", 0) + 1
    # Guardar timestamp del último error para poder expirarlos
    stats["ultimo_error_ts"] = datetime.now(timezone.utc).isoformat()
    _save()


def resetear_errores(par: str):
    """Limpia el contador de errores de un par (llamar al desbloquear manualmente)."""
    stats = _data["pares_stats"].get(par, {})
    if stats:
        stats["errores"] = 0
        stats["ultimo_error_ts"] = ""
        _save()
        log.info(f"[MEM] {par} errores API reseteados")


# ═══════════════════════════════════════════════════════
# BLOQUEOS
# ═══════════════════════════════════════════════════════

def esta_bloqueado(par: str) -> bool:
    """Par bloqueado si muchos errores API reales o tasa de éxito muy baja."""
    if par in config.PARES_BLOQUEADOS:
        return True
    stats = _data["pares_stats"].get(par, {})

    # ── Errores API: umbral subido a 10 y con expiración de 24h ──────────
    errores = stats.get("errores", 0)
    if errores >= 10:
        # Si el último error fue hace más de 24h, resetear y no bloquear
        ultimo_ts = stats.get("ultimo_error_ts", "")
        if ultimo_ts:
            try:
                ts = datetime.fromisoformat(ultimo_ts)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                horas = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
                if horas >= 24:
                    stats["errores"] = 0
                    stats["ultimo_error_ts"] = ""
                    log.info(f"[MEM] {par} errores expirados (>24h) — desbloqueado")
                    _save()
                    # No bloquear — los errores eran viejos
                else:
                    return True  # Errores recientes y abundantes → bloquear
            except Exception:
                return True
        else:
            return True

    trades = stats.get("trades", 0)
    wins   = stats.get("wins", 0)
    pnl    = stats.get("pnl_total", 0.0)
    # Bloquear solo si ≥8 trades con WR < 20% y PnL muy negativo
    if trades >= 8 and (wins / trades) < 0.20 and pnl < -15.0:
        return True
    return False


def get_pares_bloqueados() -> list:
    return [p for p in _data["pares_stats"] if esta_bloqueado(p)]


# ═══════════════════════════════════════════════════════
# TOP PARES Y SCORE
# ═══════════════════════════════════════════════════════

def get_top_pares(n: int = 10) -> list:
    """Retorna los N mejores pares por PnL acumulado."""
    stats = _data["pares_stats"]
    ranked = sorted(
        [
            (p, s) for p, s in stats.items()
            if s.get("trades", 0) >= 3 and not esta_bloqueado(p)
        ],
        key=lambda x: x[1].get("pnl_total", 0.0),
        reverse=True,
    )
    return [p for p, _ in ranked[:n]]


def ajustar_score(par: str, score: int, kz: str = "", motivos: list = None) -> int:
    """Ajusta score ±2 según historial real del par (WR + PnL)."""
    stats = _data["pares_stats"].get(par, {})
    trades = stats.get("trades", 0)
    wins   = stats.get("wins", 0)
    pnl    = stats.get("pnl_total", 0.0)
    if trades < 3:
        return score
    wr = wins / trades
    ajuste = 0
    # WR alto y PnL positivo = par ganador → boost
    if wr >= 0.65 and pnl > 0:
        ajuste = +2
    elif wr >= 0.55 and pnl > 0:
        ajuste = +1
    # WR bajo o PnL negativo = par perdedor → penalizar
    elif wr <= 0.30 or (trades >= 5 and pnl < -5.0):
        ajuste = -2
    elif wr <= 0.40:
        ajuste = -1
    return max(score + ajuste, 0)


# ═══════════════════════════════════════════════════════
# RESUMEN
# ═══════════════════════════════════════════════════════

def resumen() -> str:
    comp   = _data["compounding"]
    trades = _data["trades"]
    total  = len(trades)
    wins   = sum(1 for t in trades if t.get("ganado"))
    wr     = f"{wins/total*100:.1f}%" if total > 0 else "N/A"
    bloq   = len(get_pares_bloqueados())
    pool   = comp.get("ganancias", 0.0)
    tot_g  = comp.get("total_ganado", 0.0)
    return (
        f"📚 *Memoria* — {total} trades | WR: {wr}\n"
        f"💹 PnL total: `${tot_g:+.2f}` | Pool: `${pool:.2f}`\n"
        f"📊 Próx trade: `${get_trade_amount():.2f} USDT`\n"
        f"🚫 Pares bloqueados: `{bloq}`"
    )
