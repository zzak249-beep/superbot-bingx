"""
learner.py — Aprendizaje adaptativo
Evalúa el rendimiento por par y ajusta qué pares usa el bot.
Se ejecuta cada LEARNER_CICLO_H horas.
"""

import json
import os
from datetime import datetime, timedelta
import config
import database
import notifier


ESTADO_FILE = "learner_estado.json"


def _cargar_estado() -> dict:
    if os.path.exists(ESTADO_FILE):
        try:
            with open(ESTADO_FILE) as f:
                return json.load(f)
        except:
            pass
    return {"ultima_evaluacion": None, "ajustes": []}


def _guardar_estado(estado: dict):
    with open(ESTADO_FILE, "w") as f:
        json.dump(estado, f, indent=2)


def necesita_evaluacion() -> bool:
    estado = _cargar_estado()
    ultima = estado.get("ultima_evaluacion")
    if not ultima:
        return True
    dt_ultima = datetime.fromisoformat(ultima)
    return datetime.now() - dt_ultima > timedelta(hours=config.LEARNER_CICLO_H)


def evaluar_y_ajustar(pares_config: list) -> list:
    """
    Evalúa todos los pares y:
    - Penaliza los que tienen mal rendimiento
    - Rehabilita los que cumplieron su penalización
    Retorna la lista de pares activos después del ajuste
    """
    print(f"\n[LEARNER] Evaluando {len(pares_config)} pares...")
    ahora = datetime.now()

    # 1. Rehabilitar pares cuya penalización expiró
    _rehabilitar_expirados(ahora)

    # 2. Evaluar rendimiento de cada par
    penalizados = []
    activos     = []

    for par in pares_config:
        metricas = database.get_metricas_par(par)

        if not metricas:
            activos.append(par)
            continue

        # Par penalizado actualmente
        if not metricas.get("activo", 1):
            hasta = metricas.get("penalizado_hasta", "")
            print(f"  ⏸ {par} penalizado hasta {hasta}")
            continue

        total = metricas.get("total_trades", 0)

        # No hay suficientes datos todavía
        if total < config.LEARNER_MIN_TRADES:
            activos.append(par)
            print(f"  📊 {par}: {total} trades (esperando {config.LEARNER_MIN_TRADES})")
            continue

        wr = metricas.get("wr", 0)
        pf = metricas.get("pf", 0)

        # Criterios de penalización
        motivo = None
        if wr < config.LEARNER_MIN_WR and pf < config.LEARNER_MIN_PF:
            motivo = f"WR={wr:.1f}% y PF={pf:.2f} por debajo del mínimo"
        elif wr < (config.LEARNER_MIN_WR - 10):
            motivo = f"WR crítico={wr:.1f}%"
        elif pf < 0.8:
            motivo = f"PF crítico={pf:.2f}"

        if motivo:
            hasta = (ahora + timedelta(hours=config.LEARNER_PENALIZACION_H)).isoformat()
            database.penalizar_par(par, hasta, motivo)
            notifier.learner_ajuste(par, "PENALIZAR", motivo)
            penalizados.append(par)
            print(f"  ❌ PENALIZANDO {par}: {motivo}")
        else:
            activos.append(par)
            print(f"  ✓ {par}: WR={wr:.1f}% PF={pf:.2f} — OK")

    # 3. Guardar estado
    estado = {
        "ultima_evaluacion": ahora.isoformat(),
        "activos": activos,
        "penalizados": penalizados,
        "ajustes": [{"par": p, "accion": "PENALIZAR", "ts": ahora.isoformat()} for p in penalizados]
    }
    _guardar_estado(estado)

    print(f"[LEARNER] Activos: {len(activos)} | Penalizados: {len(penalizados)}")
    return activos


def _rehabilitar_expirados(ahora: datetime):
    """Revisa la BD y rehabilita pares cuya penalización ya venció"""
    import sqlite3
    try:
        conn = database.get_conn()
        rows = conn.execute(
            "SELECT par, penalizado_hasta FROM metricas_par WHERE activo=0 AND penalizado_hasta IS NOT NULL"
        ).fetchall()
        conn.close()

        for row in rows:
            par   = row["par"]
            hasta = row["penalizado_hasta"]
            try:
                dt_hasta = datetime.fromisoformat(hasta)
                if ahora >= dt_hasta:
                    database.rehabilitar_par(par)
                    notifier.learner_ajuste(par, "REHABILITAR", f"penalización expiró ({hasta})")
                    print(f"  ♻️ REHABILITADO {par}")
            except:
                pass
    except Exception as e:
        print(f"[LEARNER] Error rehabilitando: {e}")


def ajustar_parametros_globales():
    """
    Analiza el rendimiento global y sugiere ajustes de parámetros.
    Por ahora solo loguea las sugerencias; no modifica config automáticamente.
    """
    trades = database.get_ultimos_trades(50)
    if len(trades) < 10:
        return

    wins   = [t for t in trades if t.get("resultado") == "WIN"]
    losses = [t for t in trades if t.get("resultado") == "LOSS"]

    wr_global = len(wins) / len(trades) * 100 if trades else 0
    pnl_total = sum(t.get("pnl_usd", 0) for t in trades)

    sugerencias = []

    if wr_global < 40:
        sugerencias.append("WR global < 40% — considera bajar RSI_OVERSOLD a 25")
    if wr_global > 70:
        sugerencias.append("WR global > 70% — podrías subir RSI_OVERSOLD a 35")

    # Analizar si las pérdidas son muy grandes comparado con ganancias
    avg_win  = sum(t.get("pnl_usd", 0) for t in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(t.get("pnl_usd", 0) for t in losses) / len(losses)) if losses else 0

    if avg_loss > avg_win * 2:
        sugerencias.append("Pérdidas promedio 2x ganancias — considera bajar SL_ATR_MULT")

    if sugerencias:
        print(f"\n[LEARNER] Sugerencias de ajuste:")
        for s in sugerencias:
            print(f"  💡 {s}")

    return {
        "wr_global": wr_global,
        "pnl_total": pnl_total,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "sugerencias": sugerencias
    }
