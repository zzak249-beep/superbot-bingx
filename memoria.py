"""
memoria.py — Sistema de aprendizaje del bot
Aprende de errores y resultados por par:
  - Blacklist temporal para pares con errores API repetidos
  - Penalización de score para pares con pérdidas consecutivas
  - Bonus de score para pares con historial ganador
  - Persistencia en disco (sobrevive reinicios)
"""

import json
import os
from datetime import datetime, date

MEMORIA_FILE = "/app/bot_memoria.json"

# ─────────────────────────────────────────────────────
# ESTRUCTURA DE MEMORIA
# ─────────────────────────────────────────────────────
# {
#   "par": {
#     "wins": int,
#     "losses": int,
#     "errores_api": int,        # code=109400 u otros errores
#     "pnl_total": float,
#     "perdidas_cons": int,      # pérdidas consecutivas actuales
#     "ultimo_error": str,       # fecha ISO del último error
#     "blacklist_hasta": str,    # fecha ISO hasta la que está bloqueado
#     "ultimo_trade": str,
#   }
# }

_memoria: dict = {}


def _cargar():
    global _memoria
    try:
        if os.path.exists(MEMORIA_FILE):
            with open(MEMORIA_FILE, "r") as f:
                _memoria = json.load(f)
            print(f"[MEMORIA] Cargados {len(_memoria)} pares del historial")
        else:
            _memoria = {}
            print("[MEMORIA] Sin historial previo — comenzando desde cero")
    except Exception as e:
        print(f"[MEMORIA] Error cargando: {e} — reiniciando")
        _memoria = {}


def _guardar():
    try:
        with open(MEMORIA_FILE, "w") as f:
            json.dump(_memoria, f, indent=2)
    except Exception as e:
        print(f"[MEMORIA] Error guardando: {e}")


def _get(par: str) -> dict:
    if par not in _memoria:
        _memoria[par] = {
            "wins": 0, "losses": 0, "errores_api": 0,
            "pnl_total": 0.0, "perdidas_cons": 0,
            "ultimo_error": None, "blacklist_hasta": None,
            "ultimo_trade": None,
        }
    return _memoria[par]


# ─────────────────────────────────────────────────────
# API PÚBLICA
# ─────────────────────────────────────────────────────

def inicializar():
    """Llamar al arrancar el bot"""
    _cargar()


def registrar_error_api(par: str, codigo_error: int):
    """Registra un fallo de API (ej: code=109400)"""
    m = _get(par)
    m["errores_api"] += 1
    m["ultimo_error"] = datetime.now().isoformat()

    # Tras 3 errores API seguidos → blacklist 2 horas
    if m["errores_api"] >= 3:
        from datetime import timedelta
        hasta = (datetime.now() + timedelta(hours=2)).isoformat()
        m["blacklist_hasta"] = hasta
        print(f"[MEMORIA] ⛔ {par} bloqueado 2h por {m['errores_api']} errores API (code={codigo_error})")

    _guardar()


def registrar_resultado(par: str, pnl: float, lado: str):
    """Registra el resultado de un trade cerrado"""
    m = _get(par)
    m["pnl_total"] = round(m["pnl_total"] + pnl, 4)
    m["ultimo_trade"] = datetime.now().isoformat()
    m["errores_api"] = 0  # reset errores al tener un trade exitoso

    if pnl > 0:
        m["wins"] += 1
        m["perdidas_cons"] = 0
        print(f"[MEMORIA] ✅ {par} WIN #{m['wins']} PnL={pnl:+.4f} (total={m['pnl_total']:+.4f})")
    else:
        m["losses"] += 1
        m["perdidas_cons"] += 1
        print(f"[MEMORIA] ❌ {par} LOSS #{m['losses']} PnL={pnl:+.4f} perdidas_cons={m['perdidas_cons']}")

        # Tras 3 pérdidas consecutivas → blacklist 4 horas
        if m["perdidas_cons"] >= 3:
            from datetime import timedelta
            hasta = (datetime.now() + timedelta(hours=4)).isoformat()
            m["blacklist_hasta"] = hasta
            print(f"[MEMORIA] ⛔ {par} bloqueado 4h por {m['perdidas_cons']} pérdidas seguidas")

    _guardar()


def esta_bloqueado(par: str) -> bool:
    """Devuelve True si el par está en blacklist activa"""
    m = _get(par)
    bl = m.get("blacklist_hasta")
    if not bl:
        return False
    if datetime.now().isoformat() < bl:
        return True
    # Expiró → limpiar blacklist
    m["blacklist_hasta"] = None
    m["errores_api"] = 0
    m["perdidas_cons"] = 0
    _guardar()
    print(f"[MEMORIA] 🔓 {par} desbloqueado")
    return False


def ajustar_score(par: str, score: int) -> int:
    """
    Ajusta el score de una señal según historial del par:
      - Pérdidas consecutivas: -5 pts cada una
      - Win rate alto (>60%): +5 pts
      - PnL total negativo: -5 pts
      - Muchos errores API: -10 pts
    """
    m = _get(par)
    total = m["wins"] + m["losses"]
    ajuste = 0

    # Penalizar pérdidas consecutivas
    if m["perdidas_cons"] >= 1:
        ajuste -= m["perdidas_cons"] * 5

    # Bonus por buen win rate (mínimo 5 trades)
    if total >= 5:
        wr = m["wins"] / total
        if wr >= 0.6:
            ajuste += 5
        elif wr < 0.35:
            ajuste -= 5

    # Penalizar PnL negativo acumulado
    if m["pnl_total"] < -2.0:
        ajuste -= 5

    # Penalizar errores API
    if m["errores_api"] >= 2:
        ajuste -= 10

    score_final = max(0, min(100, score + ajuste))

    if ajuste != 0:
        print(f"[MEMORIA] {par} score {score} → {score_final} (ajuste={ajuste:+d}, "
              f"W/L={m['wins']}/{m['losses']}, perdidas_cons={m['perdidas_cons']})")

    return score_final


def resumen() -> str:
    """Devuelve un resumen del historial para Telegram"""
    if not _memoria:
        return "Sin historial aún."

    bloqueados = [p for p in _memoria if esta_bloqueado(p)]
    mejores = sorted(
        [(p, d) for p, d in _memoria.items() if d["wins"] + d["losses"] >= 3],
        key=lambda x: x[1]["pnl_total"], reverse=True
    )[:3]
    peores = sorted(
        [(p, d) for p, d in _memoria.items() if d["wins"] + d["losses"] >= 3],
        key=lambda x: x[1]["pnl_total"]
    )[:3]

    txt = f"🧠 *Memoria del bot* ({len(_memoria)} pares)\n"
    txt += f"⛔ Bloqueados: {len(bloqueados)}\n"

    if mejores:
        txt += "\n🏆 *Mejores pares:*\n"
        for p, d in mejores:
            wr = d['wins'] / (d['wins'] + d['losses']) * 100
            txt += f"  `{p}` W{d['wins']}/L{d['losses']} WR:{wr:.0f}% PnL:{d['pnl_total']:+.2f}\n"

    if peores:
        txt += "\n💀 *Peores pares:*\n"
        for p, d in peores:
            wr = d['wins'] / (d['wins'] + d['losses']) * 100
            txt += f"  `{p}` W{d['wins']}/L{d['losses']} WR:{wr:.0f}% PnL:{d['pnl_total']:+.2f}\n"

    if bloqueados:
        txt += f"\n⛔ *Bloqueados ahora:*\n"
        for p in bloqueados:
            txt += f"  `{p}`\n"

    return txt


# Cargar al importar
_cargar()
