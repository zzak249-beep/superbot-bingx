"""
optimizador.py — Agente de auto-optimización en segundo plano
SMC Bot v5.0 [MetaClaw Edition]

Corre en un hilo separado. Cada 24h analiza el historial real de trades
y ajusta automáticamente los parámetros del bot via Railway API.

Qué optimiza automáticamente:
  ✅ SCORE_MIN          — sube/baja según WR global
  ✅ MIN_RR             — ajusta según R:R medio de trades ganadores
  ✅ RSI_BUY_MAX        — estrecha/amplía según falsos positivos RSI
  ✅ TP_ATR_MULT        — ajusta según cuánto se mueve el precio antes de revertir
  ✅ SL_ATR_MULT        — ajusta según frecuencia de SL hits
  ✅ TIME_EXIT_HORAS    — ajusta según duración media de trades ganadores
  ✅ PARES_BLOQUEADOS   — añade pares con WR < 20% y PnL negativo
  ✅ PARES_PRIORITARIOS — añade pares con WR > 65% y PnL positivo
  ✅ MAX_POSICIONES     — ajusta según drawdown diario y volatilidad
  ✅ KZ_REQUERIDA       — activa/desactiva según diferencia WR dentro/fuera KZ

Requiere:
  RAILWAY_TOKEN       — API token de Railway (en variables de entorno)
  RAILWAY_SERVICE_ID  — ID del servicio en Railway
  RAILWAY_PROJECT_ID  — ID del proyecto en Railway (opcional, se detecta solo)

Integración en main.py (añadir al final del bloque de imports y al inicio de main()):
  import optimizador
  # al inicio de main():
  optimizador.iniciar()
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

log = logging.getLogger("optimizador")

# ══════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════

INTERVALO_HORAS    = float(os.getenv("OPT_INTERVALO_HORAS", "24"))
MIN_TRADES_ANALISIS = int(os.getenv("OPT_MIN_TRADES", "10"))   # mínimo para analizar
DRY_RUN            = os.getenv("OPT_DRY_RUN", "false").lower() in ("true", "1", "yes")

RAILWAY_TOKEN      = os.getenv("RAILWAY_TOKEN", "")
RAILWAY_SERVICE_ID = os.getenv("RAILWAY_SERVICE_ID", "")

# Límites de seguridad — el optimizador NUNCA saldrá de estos rangos
LIMITES = {
    "SCORE_MIN":       (4, 12),
    "MIN_RR":          (1.5, 4.0),
    "RSI_BUY_MAX":     (60.0, 78.0),
    "RSI_SELL_MIN":    (22.0, 40.0),
    "TP_ATR_MULT":     (1.8, 5.0),
    "SL_ATR_MULT":     (0.6, 2.0),
    "TIME_EXIT_HORAS": (3.0, 16.0),
    "MAX_POSICIONES":  (2, 8),
    "LEVERAGE":        (5, 20),
}

_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


# ══════════════════════════════════════════════════════════════
# RAILWAY API
# ══════════════════════════════════════════════════════════════

_RAILWAY_GQL = "https://backboard.railway.app/graphql/v2"


def _railway_headers() -> dict:
    return {
        "Authorization": f"Bearer {RAILWAY_TOKEN}",
        "Content-Type":  "application/json",
    }


def _railway_get_variables() -> dict:
    """Obtiene las variables actuales del servicio en Railway."""
    if not RAILWAY_TOKEN or not RAILWAY_SERVICE_ID:
        return {}
    query = """
    query GetServiceVariables($serviceId: String!) {
      serviceInstance(id: $serviceId) {
        serviceVariables {
          edges { node { name value } }
        }
      }
    }
    """
    try:
        resp = requests.post(
            _RAILWAY_GQL,
            headers=_railway_headers(),
            json={"query": query, "variables": {"serviceId": RAILWAY_SERVICE_ID}},
            timeout=15,
        )
        data = resp.json()
        edges = (
            data.get("data", {})
            .get("serviceInstance", {})
            .get("serviceVariables", {})
            .get("edges", [])
        )
        return {e["node"]["name"]: e["node"]["value"] for e in edges}
    except Exception as e:
        log.warning(f"[OPT] Railway get_variables error: {e}")
        return {}


def _railway_upsert_variables(variables: dict) -> bool:
    """Actualiza variables en Railway. Devuelve True si tuvo éxito."""
    if not RAILWAY_TOKEN or not RAILWAY_SERVICE_ID:
        log.warning("[OPT] RAILWAY_TOKEN o RAILWAY_SERVICE_ID no configurados — usando DRY_RUN")
        return False

    if DRY_RUN:
        log.info(f"[OPT][DRY_RUN] Hubiera actualizado: {variables}")
        return True

    # Railway v2 API usa upsertVariables
    mutation = """
    mutation UpsertVariables($input: VariableCollectionUpsertInput!) {
      variableCollectionUpsert(input: $input)
    }
    """
    variables_list = [{"name": k, "value": str(v)} for k, v in variables.items()]
    try:
        resp = requests.post(
            _RAILWAY_GQL,
            headers=_railway_headers(),
            json={
                "query": mutation,
                "variables": {
                    "input": {
                        "serviceId": RAILWAY_SERVICE_ID,
                        "variables":  variables_list,
                    }
                },
            },
            timeout=20,
        )
        result = resp.json()
        if result.get("errors"):
            log.warning(f"[OPT] Railway upsert error: {result['errors']}")
            return False
        log.info(f"[OPT] ✅ Railway actualizado: {list(variables.keys())}")
        return True
    except Exception as e:
        log.warning(f"[OPT] Railway upsert excepción: {e}")
        return False


# ══════════════════════════════════════════════════════════════
# ANÁLISIS DE HISTORIAL
# ══════════════════════════════════════════════════════════════

def _cargar_trades_recientes(dias: int = 7) -> list:
    """Carga trades de memoria.py de los últimos N días."""
    try:
        import memoria
        trades = memoria._data.get("trades", [])
        if not trades:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(days=dias)
        recientes = []
        for t in trades:
            try:
                ts = datetime.fromisoformat(t["ts"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    recientes.append(t)
            except Exception:
                recientes.append(t)  # si no hay ts, incluir igualmente
        return recientes
    except Exception as e:
        log.warning(f"[OPT] Error cargando trades: {e}")
        return []


def _analizar_historial(trades: list) -> dict:
    """
    Analiza el historial y devuelve métricas clave para la optimización.
    """
    if not trades:
        return {}

    total  = len(trades)
    wins   = [t for t in trades if t.get("ganado")]
    losses = [t for t in trades if not t.get("ganado")]
    wr     = len(wins) / total if total > 0 else 0

    pnl_wins   = [t.get("pnl", 0) for t in wins]
    pnl_losses = [t.get("pnl", 0) for t in losses]

    avg_win  = sum(pnl_wins)  / len(pnl_wins)  if pnl_wins  else 0
    avg_loss = sum(pnl_losses) / len(pnl_losses) if pnl_losses else 0
    rr_real  = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    # Trades por killzone
    kz_stats = {}
    for t in trades:
        kz = t.get("kz", "FUERA")
        if kz not in kz_stats:
            kz_stats[kz] = {"total": 0, "wins": 0}
        kz_stats[kz]["total"] += 1
        if t.get("ganado"):
            kz_stats[kz]["wins"] += 1

    wr_en_kz   = 0
    wr_fuera_kz = 0
    for kz, st in kz_stats.items():
        wr_kz = st["wins"] / st["total"] if st["total"] > 0 else 0
        if kz in ("LONDON", "NY", "ASIA"):
            wr_en_kz = max(wr_en_kz, wr_kz)
        else:
            wr_fuera_kz = max(wr_fuera_kz, wr_kz)

    # Stats por par
    par_stats = {}
    for t in trades:
        par = t.get("par", "?")
        if par not in par_stats:
            par_stats[par] = {"total": 0, "wins": 0, "pnl": 0.0}
        par_stats[par]["total"] += 1
        par_stats[par]["pnl"]   += t.get("pnl", 0)
        if t.get("ganado"):
            par_stats[par]["wins"] += 1

    pares_buenos  = []
    pares_malos   = []
    for par, st in par_stats.items():
        if st["total"] < 3:
            continue
        wr_par = st["wins"] / st["total"]
        if wr_par >= 0.65 and st["pnl"] > 0:
            pares_buenos.append(par)
        elif wr_par < 0.20 and st["pnl"] < 0:
            pares_malos.append(par)

    return {
        "total":          total,
        "wr":             wr,
        "rr_real":        rr_real,
        "avg_win":        avg_win,
        "avg_loss":       avg_loss,
        "wr_en_kz":       wr_en_kz,
        "wr_fuera_kz":    wr_fuera_kz,
        "pares_buenos":   pares_buenos,
        "pares_malos":    pares_malos,
        "kz_stats":       kz_stats,
    }


# ══════════════════════════════════════════════════════════════
# LÓGICA DE OPTIMIZACIÓN
# ══════════════════════════════════════════════════════════════

def _clamp(valor, nombre: str):
    """Asegura que el valor esté dentro de los límites de seguridad."""
    if nombre not in LIMITES:
        return valor
    lo, hi = LIMITES[nombre]
    return max(lo, min(hi, valor))


def calcular_ajustes(metricas: dict, vars_actuales: dict) -> dict:
    """
    Calcula los nuevos valores de variables basándose en las métricas.
    Solo propone cambios cuando hay evidencia clara.
    Devuelve dict con las variables a cambiar y sus nuevos valores.
    """
    cambios = {}
    wr      = metricas.get("wr", 0.5)
    rr      = metricas.get("rr_real", 2.0)
    total   = metricas.get("total", 0)

    if total < MIN_TRADES_ANALISIS:
        log.info(f"[OPT] Solo {total} trades — necesito {MIN_TRADES_ANALISIS} para optimizar")
        return {}

    # ── SCORE_MIN ─────────────────────────────────────────────
    # WR muy bajo (<40%) → subir SCORE_MIN para ser más selectivo
    # WR muy alto (>70%) → bajar SCORE_MIN para capturar más setups
    score_actual = int(vars_actuales.get("SCORE_MIN", 6))
    if wr < 0.40 and score_actual < 10:
        nuevo = _clamp(score_actual + 1, "SCORE_MIN")
        cambios["SCORE_MIN"] = str(nuevo)
        log.info(f"[OPT] SCORE_MIN {score_actual}→{nuevo} (WR bajo: {wr:.0%})")
    elif wr > 0.68 and score_actual > 5:
        nuevo = _clamp(score_actual - 1, "SCORE_MIN")
        cambios["SCORE_MIN"] = str(nuevo)
        log.info(f"[OPT] SCORE_MIN {score_actual}→{nuevo} (WR alto: {wr:.0%})")

    # ── MIN_RR ────────────────────────────────────────────────
    # Si el R:R real promedio es muy inferior al mínimo → subir MIN_RR
    # Si el R:R real es consistentemente superior → podemos bajar MIN_RR
    rr_actual = float(vars_actuales.get("MIN_RR", 2.0))
    if rr > 0 and rr < rr_actual * 0.75:
        # El R:R real es mucho peor que el mínimo → ajustar objetivo
        nuevo = _clamp(round(rr_actual - 0.25, 2), "MIN_RR")
        cambios["MIN_RR"] = str(nuevo)
        log.info(f"[OPT] MIN_RR {rr_actual}→{nuevo} (R:R real={rr:.2f})")
    elif rr > rr_actual * 1.5 and wr > 0.55:
        nuevo = _clamp(round(rr_actual + 0.25, 2), "MIN_RR")
        cambios["MIN_RR"] = str(nuevo)
        log.info(f"[OPT] MIN_RR {rr_actual}→{nuevo} (R:R real excelente={rr:.2f})")

    # ── TP_ATR_MULT ───────────────────────────────────────────
    # Si WR es alto pero avg_win es bajo → el TP se toca pero es pequeño
    # → Subir TP_ATR_MULT para capturar más del movimiento
    tp_actual = float(vars_actuales.get("TP_ATR_MULT", 2.5))
    avg_win   = metricas.get("avg_win", 0)
    avg_loss  = abs(metricas.get("avg_loss", 1))
    if wr > 0.60 and avg_win > 0 and avg_win < avg_loss * 1.5:
        nuevo = _clamp(round(tp_actual + 0.25, 2), "TP_ATR_MULT")
        cambios["TP_ATR_MULT"] = str(nuevo)
        log.info(f"[OPT] TP_ATR_MULT {tp_actual}→{nuevo} (WR alto pero wins pequeñas)")

    # ── SL_ATR_MULT ───────────────────────────────────────────
    # Si el ratio losses/total es muy alto → SL demasiado ajustado
    # Subir SL_ATR_MULT da más margen
    sl_actual = float(vars_actuales.get("SL_ATR_MULT", 1.0))
    if wr < 0.38 and sl_actual < 1.5:
        nuevo = _clamp(round(sl_actual + 0.1, 2), "SL_ATR_MULT")
        cambios["SL_ATR_MULT"] = str(nuevo)
        log.info(f"[OPT] SL_ATR_MULT {sl_actual}→{nuevo} (muchas pérdidas, SL muy ajustado)")
    elif wr > 0.65 and sl_actual > 0.8:
        nuevo = _clamp(round(sl_actual - 0.1, 2), "SL_ATR_MULT")
        cambios["SL_ATR_MULT"] = str(nuevo)
        log.info(f"[OPT] SL_ATR_MULT {sl_actual}→{nuevo} (WR alto, podemos ajustar SL)")

    # ── KZ_REQUERIDA ─────────────────────────────────────────
    # Si WR en killzone es significativamente mejor que fuera → activar
    wr_kz    = metricas.get("wr_en_kz",    0)
    wr_fuera = metricas.get("wr_fuera_kz", 0)
    kz_req   = vars_actuales.get("KZ_REQUERIDA", "false").lower()
    if wr_kz > wr_fuera + 0.15 and kz_req == "false":
        cambios["KZ_REQUERIDA"] = "true"
        log.info(f"[OPT] KZ_REQUERIDA→true (WR en KZ={wr_kz:.0%} vs fuera={wr_fuera:.0%})")
    elif wr_kz < wr_fuera + 0.05 and kz_req == "true":
        cambios["KZ_REQUERIDA"] = "false"
        log.info(f"[OPT] KZ_REQUERIDA→false (KZ no mejora significativamente el WR)")

    # ── PARES_PRIORITARIOS ────────────────────────────────────
    buenos = metricas.get("pares_buenos", [])
    if buenos:
        actuales_str  = vars_actuales.get("PARES_PRIORITARIOS", "")
        actuales_set  = set(p.strip() for p in actuales_str.split(",") if p.strip())
        nuevos        = actuales_set | set(buenos)
        if nuevos != actuales_set:
            cambios["PARES_PRIORITARIOS"] = ",".join(sorted(nuevos))
            log.info(f"[OPT] PARES_PRIORITARIOS añadidos: {buenos}")

    # ── PARES_BLOQUEADOS ──────────────────────────────────────
    malos = metricas.get("pares_malos", [])
    if malos:
        actuales_str = vars_actuales.get("PARES_BLOQUEADOS", "RESOLV-USDT")
        actuales_set = set(p.strip() for p in actuales_str.split(",") if p.strip())
        nuevos       = actuales_set | set(malos)
        if nuevos != actuales_set:
            cambios["PARES_BLOQUEADOS"] = ",".join(sorted(nuevos))
            log.info(f"[OPT] PARES_BLOQUEADOS añadidos: {malos}")

    # ── MAX_POSICIONES ────────────────────────────────────────
    # Si WR es muy bajo → reducir exposición
    max_pos_actual = int(vars_actuales.get("MAX_POSICIONES", 5))
    if wr < 0.35 and max_pos_actual > 2:
        nuevo = _clamp(max_pos_actual - 1, "MAX_POSICIONES")
        cambios["MAX_POSICIONES"] = str(nuevo)
        log.info(f"[OPT] MAX_POSICIONES {max_pos_actual}→{nuevo} (WR muy bajo: {wr:.0%})")
    elif wr > 0.65 and max_pos_actual < 6:
        nuevo = _clamp(max_pos_actual + 1, "MAX_POSICIONES")
        cambios["MAX_POSICIONES"] = str(nuevo)
        log.info(f"[OPT] MAX_POSICIONES {max_pos_actual}→{nuevo} (WR alto: {wr:.0%})")

    return cambios


# ══════════════════════════════════════════════════════════════
# NOTIFICACIÓN TELEGRAM
# ══════════════════════════════════════════════════════════════

def _notif_telegram(msg: str):
    try:
        tok = os.getenv("TELEGRAM_TOKEN", "").strip()
        cid = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not tok or not cid:
            return
        requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": cid, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception:
        pass


def _notif_optimizacion(metricas: dict, cambios: dict, aplicados: bool):
    """Envía resumen de optimización por Telegram."""
    if not cambios:
        return

    wr    = metricas.get("wr", 0)
    total = metricas.get("total", 0)
    rr    = metricas.get("rr_real", 0)

    estado = "✅ *Aplicados*" if aplicados else "📋 *DRY RUN* (no aplicados)"
    cambios_txt = "\n".join(f"  • `{k}` → `{v}`" for k, v in cambios.items())

    _notif_telegram(
        f"🤖 *Optimizador MetaClaw* — Ciclo diario\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Trades analizados: `{total}`\n"
        f"🎯 WR: `{wr:.0%}` | R:R real: `{rr:.2f}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔧 Cambios ({estado}):\n{cambios_txt}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ Próxima optimización en {INTERVALO_HORAS:.0f}h"
    )


# ══════════════════════════════════════════════════════════════
# CICLO PRINCIPAL
# ══════════════════════════════════════════════════════════════

def _guardar_log_optimizacion(metricas: dict, cambios: dict, aplicados: bool):
    """Guarda un registro de cada optimización en disco."""
    try:
        import config as cfg
        log_path = os.path.join(cfg.MEMORY_DIR or "", "optimizador_log.json")
        registros = []
        if os.path.exists(log_path):
            with open(log_path, encoding="utf-8") as f:
                registros = json.load(f)

        registros.append({
            "ts":       datetime.now(timezone.utc).isoformat(),
            "metricas": {k: round(v, 4) if isinstance(v, float) else v
                         for k, v in metricas.items()
                         if k not in ("pares_buenos", "pares_malos", "kz_stats")},
            "cambios":  cambios,
            "aplicados": aplicados,
        })
        # Mantener últimas 90 entradas
        registros = registros[-90:]
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(registros, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.warning(f"[OPT] Error guardando log: {e}")


def ciclo_optimizacion():
    """Un ciclo completo de análisis y optimización."""
    log.info("[OPT] ── Iniciando ciclo de optimización ──")

    # 1. Cargar historial
    trades = _cargar_trades_recientes(dias=7)
    log.info(f"[OPT] {len(trades)} trades de los últimos 7 días")

    if len(trades) < MIN_TRADES_ANALISIS:
        log.info(f"[OPT] Insuficientes trades ({len(trades)} < {MIN_TRADES_ANALISIS}) — saltando")
        return

    # 2. Analizar
    metricas = _analizar_historial(trades)
    log.info(
        f"[OPT] WR={metricas['wr']:.0%} | R:R={metricas['rr_real']:.2f} | "
        f"buenos={metricas['pares_buenos']} | malos={metricas['pares_malos']}"
    )

    # 3. Obtener variables actuales de Railway
    vars_actuales = _railway_get_variables()
    if not vars_actuales:
        # Fallback: leer desde config
        try:
            import config as cfg
            vars_actuales = {
                "SCORE_MIN":       str(cfg.SCORE_MIN),
                "MIN_RR":          str(cfg.MIN_RR),
                "TP_ATR_MULT":     str(cfg.TP_ATR_MULT),
                "SL_ATR_MULT":     str(cfg.SL_ATR_MULT),
                "KZ_REQUERIDA":    str(cfg.KZ_REQUERIDA).lower(),
                "MAX_POSICIONES":  str(cfg.MAX_POSICIONES),
                "PARES_PRIORITARIOS": ",".join(cfg.PARES_PRIORITARIOS),
                "PARES_BLOQUEADOS":   ",".join(cfg.PARES_BLOQUEADOS),
            }
        except Exception:
            vars_actuales = {}

    # 4. Calcular ajustes
    cambios = calcular_ajustes(metricas, vars_actuales)

    if not cambios:
        log.info("[OPT] No hay cambios necesarios — bot optimizado ✓")
        _guardar_log_optimizacion(metricas, {}, False)
        return

    log.info(f"[OPT] Cambios propuestos: {cambios}")

    # 5. Aplicar en Railway
    aplicados = _railway_upsert_variables(cambios)

    # 6. Guardar log
    _guardar_log_optimizacion(metricas, cambios, aplicados)

    # 7. Notificar
    _notif_optimizacion(metricas, cambios, aplicados)

    log.info(f"[OPT] Ciclo completado — {len(cambios)} variables {'actualizadas' if aplicados else 'en DRY_RUN'}")


def _loop():
    """Hilo en segundo plano — ejecuta ciclos cada INTERVALO_HORAS."""
    # Primera ejecución: esperar 10 minutos para que el bot arranque
    log.info(f"[OPT] Agente iniciado — primera optimización en 10 minutos, luego cada {INTERVALO_HORAS:.0f}h")
    _stop_event.wait(600)  # 10 minutos
    if _stop_event.is_set():
        return

    while not _stop_event.is_set():
        try:
            ciclo_optimizacion()
        except Exception as e:
            log.error(f"[OPT] Error en ciclo: {e}", exc_info=True)

        # Esperar hasta el próximo ciclo
        intervalo_seg = int(INTERVALO_HORAS * 3600)
        log.info(f"[OPT] Próximo ciclo en {INTERVALO_HORAS:.0f}h")
        _stop_event.wait(intervalo_seg)


# ══════════════════════════════════════════════════════════════
# API PÚBLICA
# ══════════════════════════════════════════════════════════════

def iniciar():
    """Inicia el agente de optimización en segundo plano. Llamar desde main()."""
    global _thread

    if not RAILWAY_TOKEN:
        log.warning(
            "[OPT] RAILWAY_TOKEN no configurado — las optimizaciones se guardarán "
            "en log pero NO se aplicarán en Railway. "
            "Añade RAILWAY_TOKEN a las variables de entorno para activar auto-optimización."
        )
    if not RAILWAY_SERVICE_ID:
        log.warning("[OPT] RAILWAY_SERVICE_ID no configurado")

    if DRY_RUN:
        log.info("[OPT] Modo DRY_RUN activado — calculará ajustes pero no los aplicará")

    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="optimizador", daemon=True)
    _thread.start()
    log.info("[OPT] ✅ Agente de auto-optimización iniciado en segundo plano")


def detener():
    """Detiene el agente limpiamente."""
    _stop_event.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=5)
    log.info("[OPT] Agente detenido")


def forzar_ciclo():
    """Ejecuta un ciclo de optimización manualmente (útil para testing)."""
    log.info("[OPT] Ciclo forzado manualmente")
    ciclo_optimizacion()


def get_ultimo_log() -> Optional[dict]:
    """Devuelve la última entrada del log de optimización."""
    try:
        import config as cfg
        log_path = os.path.join(cfg.MEMORY_DIR or "", "optimizador_log.json")
        if not os.path.exists(log_path):
            return None
        with open(log_path, encoding="utf-8") as f:
            registros = json.load(f)
        return registros[-1] if registros else None
    except Exception:
        return None
