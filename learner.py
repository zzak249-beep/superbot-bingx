"""
learner.py — Aprendizaje adaptativo v2
Aprende de cada trade: qué RSI, qué BB, qué hora funcionó mejor.
Ajusta parámetros automáticamente y penaliza pares malos.
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
    return {
        "ultima_evaluacion": None,
        "rsi_optimo": config.RSI_OVERSOLD,
        "sl_optimo": config.SL_ATR_MULT,
        "tp_optimo": config.TP_ATR_MULT,
        "horas_malas": [],
        "pares_penalizados": {},
        "historial_ajustes": []
    }


def _guardar_estado(estado: dict):
    with open(ESTADO_FILE, "w") as f:
        json.dump(estado, f, indent=2)


def necesita_evaluacion() -> bool:
    estado = _cargar_estado()
    ultima = estado.get("ultima_evaluacion")
    if not ultima:
        return True
    dt = datetime.fromisoformat(ultima)
    return datetime.now() - dt > timedelta(hours=config.LEARNER_CICLO_H)


# ============================================================
# ANÁLISIS DE ERRORES
# ============================================================

def _analizar_trades(trades: list) -> dict:
    """Extrae patrones de wins vs losses"""
    if not trades:
        return {}

    wins   = [t for t in trades if t.get("resultado") == "WIN"]
    losses = [t for t in trades if t.get("resultado") == "LOSS"]

    analisis = {
        "total":  len(trades),
        "wins":   len(wins),
        "losses": len(losses),
        "wr":     len(wins) / len(trades) * 100 if trades else 0,
    }

    # RSI promedio en wins vs losses
    if wins:
        analisis["rsi_wins"]  = sum(t.get("rsi_entrada", 30) for t in wins) / len(wins)
    if losses:
        analisis["rsi_losses"]= sum(t.get("rsi_entrada", 30) for t in losses) / len(losses)

    # BB posición promedio en wins vs losses
    if wins:
        analisis["bb_wins"]   = sum(t.get("bb_posicion", 0) for t in wins) / len(wins)
    if losses:
        analisis["bb_losses"] = sum(t.get("bb_posicion", 0) for t in losses) / len(losses)

    # Horas del día con más losses
    horas_loss = {}
    for t in losses:
        ts = t.get("timestamp_entrada", "")
        try:
            hora = int(ts[11:13])
            horas_loss[hora] = horas_loss.get(hora, 0) + 1
        except:
            pass
    analisis["horas_loss"] = horas_loss

    # PnL promedio
    if wins:
        analisis["avg_win"]  = sum(t.get("pnl_usd", 0) for t in wins) / len(wins)
    if losses:
        analisis["avg_loss"] = abs(sum(t.get("pnl_usd", 0) for t in losses) / len(losses))

    return analisis


def _ajustar_rsi(estado: dict, analisis: dict) -> dict:
    """
    Si los wins ocurren con RSI más bajo que los losses,
    bajar el umbral RSI para ser más selectivo.
    Si hay muy pocas señales, subir un poco.
    """
    ajustes = []
    rsi_actual = estado.get("rsi_optimo", config.RSI_OVERSOLD)

    rsi_wins   = analisis.get("rsi_wins", None)
    rsi_losses = analisis.get("rsi_losses", None)

    if rsi_wins and rsi_losses:
        if rsi_wins < rsi_losses - 2:
            # Los wins ocurren con RSI más bajo → bajar umbral (más estricto)
            nuevo_rsi = max(20, rsi_actual - 1)
            if nuevo_rsi != rsi_actual:
                ajustes.append(f"RSI {rsi_actual} → {nuevo_rsi} (wins con RSI más bajo)")
                estado["rsi_optimo"] = nuevo_rsi

        elif rsi_losses < rsi_wins - 2:
            # Los losses ocurren con RSI más bajo → subir umbral (más permisivo)
            nuevo_rsi = min(40, rsi_actual + 1)
            if nuevo_rsi != rsi_actual:
                ajustes.append(f"RSI {rsi_actual} → {nuevo_rsi} (losses con RSI muy bajo)")
                estado["rsi_optimo"] = nuevo_rsi

    wr = analisis.get("wr", 50)
    # Si WR muy baja → ser más selectivo
    if wr < 35 and rsi_actual > 22:
        nuevo_rsi = rsi_actual - 2
        ajustes.append(f"RSI {rsi_actual} → {nuevo_rsi} (WR baja: {wr:.1f}%)")
        estado["rsi_optimo"] = nuevo_rsi

    # Si WR muy alta con pocos trades → relajar un poco para más señales
    if wr > 75 and analisis.get("total", 0) < 10 and rsi_actual < 35:
        nuevo_rsi = rsi_actual + 1
        ajustes.append(f"RSI {rsi_actual} → {nuevo_rsi} (WR alta, pocas señales)")
        estado["rsi_optimo"] = nuevo_rsi

    return estado, ajustes


def _ajustar_sl_tp(estado: dict, analisis: dict) -> dict:
    """
    Si las pérdidas promedio son mucho mayores que las ganancias,
    ajustar el multiplicador ATR del SL.
    """
    ajustes = []
    avg_win  = analisis.get("avg_win", 0)
    avg_loss = analisis.get("avg_loss", 0)
    sl_actual = estado.get("sl_optimo", config.SL_ATR_MULT)
    tp_actual = estado.get("tp_optimo", config.TP_ATR_MULT)

    if avg_win > 0 and avg_loss > 0:
        rr_real = avg_win / avg_loss

        if rr_real < 1.0:
            # Losses más grandes que wins → SL más ajustado o TP más amplio
            nuevo_sl = max(1.0, sl_actual - 0.1)
            ajustes.append(f"SL {sl_actual:.1f} → {nuevo_sl:.1f} (R:R real {rr_real:.2f})")
            estado["sl_optimo"] = round(nuevo_sl, 1)

        elif rr_real > 3.0 and tp_actual < 4.0:
            # R:R excelente → ampliar TP para capturar más
            nuevo_tp = min(4.0, tp_actual + 0.2)
            ajustes.append(f"TP {tp_actual:.1f} → {nuevo_tp:.1f} (R:R excelente {rr_real:.2f})")
            estado["tp_optimo"] = round(nuevo_tp, 1)

    return estado, ajustes


def _detectar_horas_malas(estado: dict, analisis: dict) -> dict:
    """Detecta si hay horas del día con muchos losses consecutivos"""
    ajustes = []
    horas_loss = analisis.get("horas_loss", {})

    horas_malas = [h for h, count in horas_loss.items() if count >= 3]

    if horas_malas:
        estado["horas_malas"] = horas_malas
        ajustes.append(f"Horas con muchos losses detectadas: {horas_malas}")

    return estado, ajustes


def _penalizar_pares_malos(estado: dict, pares: list) -> tuple:
    """Penaliza pares con mal rendimiento reciente"""
    penalizados = []
    rehabilitados = []
    ahora = datetime.now()

    pares_pen = estado.get("pares_penalizados", {})

    # Rehabilitar pares cuya penalización expiró
    for par, hasta_str in list(pares_pen.items()):
        try:
            hasta = datetime.fromisoformat(hasta_str)
            if ahora >= hasta:
                del pares_pen[par]
                rehabilitados.append(par)
                notifier.learner_ajuste(par, "REHABILITAR", "penalizacion expirada")
        except:
            del pares_pen[par]

    # Evaluar cada par activo
    for par in pares:
        if par in pares_pen:
            continue

        metricas = database.get_metricas_par(par)
        if not metricas:
            continue

        total = metricas.get("total_trades", 0)
        if total < config.LEARNER_MIN_TRADES:
            continue

        wr = metricas.get("wr", 0)
        pf = metricas.get("pf", 0)

        motivo = None
        if wr < 30 and pf < 0.8:
            motivo = f"WR={wr:.0f}% PF={pf:.2f} (critico)"
        elif wr < config.LEARNER_MIN_WR and pf < config.LEARNER_MIN_PF:
            motivo = f"WR={wr:.0f}% PF={pf:.2f} bajo minimo"

        if motivo:
            hasta = (ahora + timedelta(hours=config.LEARNER_PENALIZACION_H)).isoformat()
            pares_pen[par] = hasta
            penalizados.append(par)
            database.penalizar_par(par, hasta, motivo)
            notifier.learner_ajuste(par, "PENALIZAR", motivo)
            print(f"  PENALIZADO {par}: {motivo}")
        else:
            if total >= config.LEARNER_MIN_TRADES:
                print(f"  OK {par}: WR={wr:.0f}% PF={pf:.2f}")

    estado["pares_penalizados"] = pares_pen
    return estado, penalizados, rehabilitados


def _aplicar_aprendizaje_a_config(estado: dict):
    """
    Actualiza config.py en memoria con los parámetros aprendidos.
    Los cambios se aplican en el ciclo actual SIN reiniciar el bot.
    """
    rsi_nuevo = estado.get("rsi_optimo", config.RSI_OVERSOLD)
    sl_nuevo  = estado.get("sl_optimo",  config.SL_ATR_MULT)
    tp_nuevo  = estado.get("tp_optimo",  config.TP_ATR_MULT)

    cambiado = []
    if rsi_nuevo != config.RSI_OVERSOLD:
        cambiado.append(f"RSI_OVERSOLD: {config.RSI_OVERSOLD} → {rsi_nuevo}")
        config.RSI_OVERSOLD = rsi_nuevo
    if sl_nuevo != config.SL_ATR_MULT:
        cambiado.append(f"SL_ATR_MULT: {config.SL_ATR_MULT} → {sl_nuevo}")
        config.SL_ATR_MULT = sl_nuevo
    if tp_nuevo != config.TP_ATR_MULT:
        cambiado.append(f"TP_ATR_MULT: {config.TP_ATR_MULT} → {tp_nuevo}")
        config.TP_ATR_MULT = tp_nuevo

    return cambiado


# ============================================================
# FUNCIÓN PRINCIPAL
# ============================================================

def evaluar_y_ajustar(pares_config: list) -> list:
    print(f"\n[LEARNER] Evaluando {len(pares_config)} pares...")
    ahora = datetime.now()

    estado = _cargar_estado()
    trades = database.get_ultimos_trades(100)
    analisis = _analizar_trades(trades)

    todos_ajustes = []

    # 1. Ajustar RSI según patrones de wins/losses
    if len(trades) >= 10:
        estado, aj = _ajustar_rsi(estado, analisis)
        todos_ajustes.extend(aj)

        # 2. Ajustar SL/TP
        estado, aj = _ajustar_sl_tp(estado, analisis)
        todos_ajustes.extend(aj)

        # 3. Detectar horas malas
        estado, aj = _detectar_horas_malas(estado, analisis)
        todos_ajustes.extend(aj)

    # 4. Penalizar/rehabilitar pares
    estado, penalizados, rehabilitados = _penalizar_pares_malos(estado, pares_config)

    # 5. Aplicar cambios a config en memoria
    cambios_config = _aplicar_aprendizaje_a_config(estado)
    todos_ajustes.extend(cambios_config)

    # 6. Registrar en historial
    if todos_ajustes:
        entrada = {
            "timestamp": ahora.isoformat(),
            "ajustes": todos_ajustes,
            "stats": {
                "total_trades": analisis.get("total", 0),
                "wr": round(analisis.get("wr", 0), 1),
                "rsi_optimo": estado.get("rsi_optimo"),
                "sl_optimo": estado.get("sl_optimo"),
                "tp_optimo": estado.get("tp_optimo"),
            }
        }
        historial = estado.get("historial_ajustes", [])
        historial.append(entrada)
        estado["historial_ajustes"] = historial[-20:]  # Guardar últimos 20

        print(f"[LEARNER] Ajustes aplicados:")
        for a in todos_ajustes:
            print(f"  → {a}")

        # Notificar en Telegram si hay cambios importantes
        if cambios_config:
            msg = "🧠 <b>LEARNER — Parámetros ajustados</b>\n" + "\n".join(f"• {c}" for c in cambios_config)
            _telegram(msg)
    else:
        print(f"[LEARNER] Sin ajustes necesarios")

    # Mostrar resumen
    print(f"[LEARNER] WR global: {analisis.get('wr', 0):.1f}% | "
          f"Trades: {analisis.get('total', 0)} | "
          f"RSI actual: {config.RSI_OVERSOLD} | "
          f"Penalizados: {len(penalizados)}")

    estado["ultima_evaluacion"] = ahora.isoformat()
    _guardar_estado(estado)

    # Retornar pares activos (sin penalizados)
    pares_pen = estado.get("pares_penalizados", {})
    pares_activos = [p for p in pares_config if p not in pares_pen]
    return pares_activos


def ajustar_parametros_globales():
    """Análisis adicional y sugerencias"""
    trades = database.get_ultimos_trades(50)
    analisis = _analizar_tracks(trades) if trades else {}

    estado = _cargar_estado()
    horas_malas = estado.get("horas_malas", [])
    if horas_malas:
        hora_actual = datetime.now().hour
        if hora_actual in horas_malas:
            print(f"[LEARNER] Hora actual ({hora_actual}h) marcada como problemática")


def _analizar_tracks(trades):
    return _analizar_trades(trades)


def _telegram(msg: str):
    """Envío directo a Telegram sin depender de notifier para evitar imports circulares"""
    import requests
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except:
        pass


def get_estado_actual() -> dict:
    """Retorna el estado actual del learner para diagnóstico"""
    estado = _cargar_estado()
    return {
        "rsi_optimo":        estado.get("rsi_optimo", config.RSI_OVERSOLD),
        "sl_optimo":         estado.get("sl_optimo",  config.SL_ATR_MULT),
        "tp_optimo":         estado.get("tp_optimo",  config.TP_ATR_MULT),
        "horas_malas":       estado.get("horas_malas", []),
        "pares_penalizados": list(estado.get("pares_penalizados", {}).keys()),
        "ultima_evaluacion": estado.get("ultima_evaluacion", "nunca"),
        "total_ajustes":     len(estado.get("historial_ajustes", []))
    }
