"""
database.py — Memoria persistente del bot
Guarda trades, métricas por par, y ajustes del learner
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "bot22.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    # Tabla principal de trades
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            par           TEXT NOT NULL,
            lado          TEXT NOT NULL DEFAULT 'LONG',
            precio_entrada REAL,
            precio_salida  REAL,
            cantidad       REAL,
            pnl_usd        REAL,
            pnl_pct        REAL,
            rsi_entrada    REAL,
            bb_posicion    REAL,
            atr_entrada    REAL,
            sl_precio      REAL,
            tp_precio      REAL,
            resultado      TEXT,  -- WIN / LOSS / BE
            motivo_cierre  TEXT,  -- SL / TP / MANUAL / LEARNER
            balance_antes  REAL,
            balance_despues REAL,
            timestamp_entrada TEXT,
            timestamp_salida  TEXT,
            order_id_entrada  TEXT,
            order_id_salida   TEXT
        )
    """)

    # Métricas acumuladas por par (para el learner)
    c.execute("""
        CREATE TABLE IF NOT EXISTS metricas_par (
            par           TEXT PRIMARY KEY,
            total_trades  INTEGER DEFAULT 0,
            wins          INTEGER DEFAULT 0,
            losses        INTEGER DEFAULT 0,
            pnl_total     REAL DEFAULT 0,
            pf            REAL DEFAULT 0,
            wr            REAL DEFAULT 0,
            ultimo_trade  TEXT,
            activo        INTEGER DEFAULT 1,
            penalizado_hasta TEXT
        )
    """)

    # Log de ajustes del learner
    c.execute("""
        CREATE TABLE IF NOT EXISTS learner_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            par       TEXT,
            accion    TEXT,
            motivo    TEXT,
            valor_antes TEXT,
            valor_despues TEXT
        )
    """)

    # Estado del balance para compound
    c.execute("""
        CREATE TABLE IF NOT EXISTS balance_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            balance   REAL,
            equity    REAL,
            pnl_dia   REAL
        )
    """)

    conn.commit()
    conn.close()
    print("[DB] Base de datos inicializada ✓")


def guardar_trade(data: dict):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO trades (
            par, lado, precio_entrada, precio_salida, cantidad,
            pnl_usd, pnl_pct, rsi_entrada, bb_posicion, atr_entrada,
            sl_precio, tp_precio, resultado, motivo_cierre,
            balance_antes, balance_despues,
            timestamp_entrada, timestamp_salida,
            order_id_entrada, order_id_salida
        ) VALUES (
            :par, :lado, :precio_entrada, :precio_salida, :cantidad,
            :pnl_usd, :pnl_pct, :rsi_entrada, :bb_posicion, :atr_entrada,
            :sl_precio, :tp_precio, :resultado, :motivo_cierre,
            :balance_antes, :balance_despues,
            :timestamp_entrada, :timestamp_salida,
            :order_id_entrada, :order_id_salida
        )
    """, data)
    conn.commit()
    conn.close()
    _actualizar_metricas(data["par"])


def _actualizar_metricas(par: str):
    conn = get_conn()
    c = conn.cursor()

    rows = c.execute(
        "SELECT resultado, pnl_usd FROM trades WHERE par=? ORDER BY id DESC LIMIT 50",
        (par,)
    ).fetchall()

    if not rows:
        conn.close()
        return

    total = len(rows)
    wins  = sum(1 for r in rows if r["resultado"] == "WIN")
    losses= sum(1 for r in rows if r["resultado"] == "LOSS")
    pnl   = sum(r["pnl_usd"] for r in rows)

    ganancias = sum(r["pnl_usd"] for r in rows if r["pnl_usd"] > 0)
    perdidas  = abs(sum(r["pnl_usd"] for r in rows if r["pnl_usd"] < 0))
    pf = ganancias / perdidas if perdidas > 0 else 999.0
    wr = (wins / total * 100) if total > 0 else 0

    c.execute("""
        INSERT INTO metricas_par (par, total_trades, wins, losses, pnl_total, pf, wr, ultimo_trade)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(par) DO UPDATE SET
            total_trades=excluded.total_trades,
            wins=excluded.wins,
            losses=excluded.losses,
            pnl_total=excluded.pnl_total,
            pf=excluded.pf,
            wr=excluded.wr,
            ultimo_trade=excluded.ultimo_trade
    """, (par, total, wins, losses, pnl, pf, wr, datetime.now().isoformat()))

    conn.commit()
    conn.close()


def get_metricas_par(par: str) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM metricas_par WHERE par=?", (par,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_todos_pares_activos() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT par FROM metricas_par WHERE activo=1"
    ).fetchall()
    conn.close()
    return [r["par"] for r in rows]


def penalizar_par(par: str, hasta: str, motivo: str):
    conn = get_conn()
    conn.execute(
        "UPDATE metricas_par SET activo=0, penalizado_hasta=? WHERE par=?",
        (hasta, par)
    )
    conn.execute(
        "INSERT INTO learner_log (timestamp, par, accion, motivo) VALUES (?,?,?,?)",
        (datetime.now().isoformat(), par, "PENALIZAR", motivo)
    )
    conn.commit()
    conn.close()


def rehabilitar_par(par: str):
    conn = get_conn()
    conn.execute(
        "UPDATE metricas_par SET activo=1, penalizado_hasta=NULL WHERE par=?",
        (par,)
    )
    conn.execute(
        "INSERT INTO learner_log (timestamp, par, accion, motivo) VALUES (?,?,?,?)",
        (datetime.now().isoformat(), par, "REHABILITAR", "penalización expirada")
    )
    conn.commit()
    conn.close()


def guardar_balance(balance: float, equity: float, pnl_dia: float):
    conn = get_conn()
    conn.execute(
        "INSERT INTO balance_history (timestamp, balance, equity, pnl_dia) VALUES (?,?,?,?)",
        (datetime.now().isoformat(), balance, equity, pnl_dia)
    )
    conn.commit()
    conn.close()


def get_pnl_hoy() -> float:
    conn = get_conn()
    hoy = datetime.now().strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT SUM(pnl_usd) as total FROM trades WHERE timestamp_salida LIKE ?",
        (f"{hoy}%",)
    ).fetchone()
    conn.close()
    return row["total"] or 0.0


def get_ultimos_trades(n: int = 20) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_racha_perdidas_hoy() -> int:
    conn = get_conn()
    hoy = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT resultado FROM trades 
           WHERE timestamp_salida LIKE ? 
           ORDER BY id DESC""",
        (f"{hoy}%",)
    ).fetchall()
    conn.close()

    racha = 0
    for r in rows:
        if r["resultado"] == "LOSS":
            racha += 1
        else:
            break
    return racha


if __name__ == "__main__":
    init_db()
    print("[DB] Tablas creadas correctamente")
