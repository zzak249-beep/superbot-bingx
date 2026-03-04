"""
main.py — Loop principal del bot
Flujo: señal → validar → abrir → monitorear → cerrar → aprender
"""

import time
import traceback
from datetime import datetime, date

import config
import database
import exchange
import analizar
import learner
import notifier

# Estado de sesión en memoria
posiciones_abiertas = {}   # {par: trade_dict}
balance_inicio_dia  = 0.0
fecha_actual        = None


def inicializar():
    global balance_inicio_dia, fecha_actual

    print("=" * 60)
    print("  BOT22 — BB+RSI Elite v5")
    print(f"  Modo: {'DEMO 🔴' if config.MODO_DEMO else 'REAL 🟢'}")
    print("=" * 60)

    # Inicializar base de datos
    database.init_db()

    # Balance inicial
    balance_inicio_dia = exchange.get_balance()
    fecha_actual = date.today()

    print(f"[MAIN] Balance inicial: ${balance_inicio_dia:.2f}")
    print(f"[MAIN] Pares configurados: {len(config.PARES)}")
    print(f"[MAIN] Max posiciones: {config.MAX_POSICIONES}")
    print(f"[MAIN] Leverage: {config.LEVERAGE}x")
    print(f"[MAIN] Ciclo: {config.CICLO_SEGUNDOS}s")
    print()

    notifier.bot_iniciado(config.PARES, balance_inicio_dia)


def nuevo_dia():
    """Acciones al inicio de un nuevo día"""
    global balance_inicio_dia, fecha_actual

    balance_inicio_dia = exchange.get_balance()
    fecha_actual = date.today()

    # Resumen del día anterior
    trades_ayer = database.get_ultimos_trades(50)
    if trades_ayer:
        ayer_str = str(date.today())
        wins   = [t for t in trades_ayer if t.get("resultado") == "WIN"]
        losses = [t for t in trades_ayer if t.get("resultado") == "LOSS"]
        pnl    = sum(t.get("pnl_usd", 0) for t in trades_ayer)
        wr     = len(wins) / len(trades_ayer) * 100 if trades_ayer else 0
        pnl_gan= sum(t["pnl_usd"] for t in wins) if wins else 0
        pnl_per= sum(t["pnl_usd"] for t in losses) if losses else 0
        pf     = abs(pnl_gan / pnl_per) if pnl_per < 0 else 999

        notifier.resumen_diario({
            "total": len(trades_ayer),
            "wins": len(wins),
            "losses": len(losses),
            "pnl": pnl,
            "wr": wr,
            "pf": pf,
            "balance": balance_inicio_dia
        })

    print(f"[MAIN] Nuevo día: {fecha_actual} | Balance: ${balance_inicio_dia:.2f}")


def circuit_breaker_activo() -> tuple[bool, str]:
    """
    Verifica si alguna condición de circuit breaker está activa.
    Retorna (True, motivo) si hay que pausar.
    """
    balance_actual = exchange.get_balance()
    pnl_dia = balance_actual - balance_inicio_dia
    pnl_pct = pnl_dia / balance_inicio_dia if balance_inicio_dia > 0 else 0

    # Pérdida máxima del día
    if pnl_pct <= config.MAX_PNL_NEGATIVO_DIA:
        return True, f"PnL día {pnl_pct*100:.1f}% (límite {config.MAX_PNL_NEGATIVO_DIA*100:.0f}%)"

    # Racha de pérdidas
    racha = database.get_racha_perdidas_hoy()
    if racha >= config.MAX_PERDIDAS_SEGUIDAS:
        return True, f"{racha} pérdidas consecutivas"

    return False, ""


def pares_activos_para_operar() -> list:
    """
    Retorna la lista de pares que el bot puede operar en este ciclo,
    excluyendo los que ya tienen posición abierta.
    """
    pares_con_posicion = set(posiciones_abiertas.keys())
    return [p for p in config.PARES if p not in pares_con_posicion]


def abrir_posicion(señal: dict) -> bool:
    """Intenta abrir una posición para la señal dada"""
    par    = señal["par"]
    precio = señal["precio"]
    sl     = señal["sl"]
    tp     = señal["tp"]
    rsi    = señal["rsi"]
    atr    = señal["atr"]

    # Verificar límite de posiciones
    if len(posiciones_abiertas) >= config.MAX_POSICIONES:
        return False

    # No abrir si ya hay posición en este par
    if par in posiciones_abiertas:
        return False

    balance = exchange.get_balance()

    # Calcular tamaño con compound (usa balance real actual)
    cantidad = exchange.calcular_cantidad(par, balance, precio)
    if cantidad <= 0:
        print(f"[MAIN] {par}: cantidad calculada = 0, skip")
        return False

    # Configurar apalancamiento
    exchange.set_leverage(par, config.LEVERAGE)

    # Abrir orden
    trade = exchange.abrir_long(par, cantidad, precio, sl, tp)
    if not trade:
        print(f"[MAIN] {par}: error abriendo orden")
        return False

    # Enriquecer trade con datos de análisis
    trade["rsi"]    = rsi
    trade["atr"]    = atr
    trade["bb"]     = señal.get("bb", {})
    trade["rr"]     = señal["rr"]
    trade["balance_antes"] = balance

    # Guardar en memoria
    posiciones_abiertas[par] = trade

    print(f"[MAIN] ✅ ABIERTO {par} | entrada:{precio:.4f} SL:{sl:.4f} TP:{tp:.4f} qty:{cantidad}")
    notifier.trade_abierto(trade)

    return True


def monitorear_posiciones():
    """
    Revisa posiciones abiertas y cierra las que llegaron a SL o TP.
    BingX gestiona SL/TP automáticamente, pero aquí verificamos
    si la posición ya fue cerrada por el exchange.
    """
    if not posiciones_abiertas:
        return

    pares_cerrados = []

    for par, trade in list(posiciones_abiertas.items()):
        try:
            # Verificar si la posición sigue abierta en el exchange
            posicion = exchange.get_posicion(par)

            precio_actual = exchange.get_precio(par)
            precio_entrada = trade.get("precio_entrada", 0)
            sl = trade.get("sl", 0)
            tp = trade.get("tp", 0)

            posicion_cerrada = False
            motivo_cierre    = ""
            precio_salida    = precio_actual

            if config.MODO_DEMO:
                # En demo, verificar manualmente si tocó SL o TP
                if precio_actual <= sl:
                    posicion_cerrada = True
                    motivo_cierre    = "SL"
                    precio_salida    = sl
                elif precio_actual >= tp:
                    posicion_cerrada = True
                    motivo_cierre    = "TP"
                    precio_salida    = tp
            else:
                # En real, si la posición no está en el exchange, fue cerrada
                if not posicion:
                    posicion_cerrada = True
                    motivo_cierre    = "SL/TP (exchange)"

            if posicion_cerrada:
                pares_cerrados.append((par, precio_salida, motivo_cierre))

        except Exception as e:
            print(f"[MAIN] Error monitoreando {par}: {e}")

    # Procesar cierres
    for par, precio_salida, motivo in pares_cerrados:
        cerrar_y_registrar(par, precio_salida, motivo)


def cerrar_y_registrar(par: str, precio_salida: float, motivo: str):
    """Cierra posición y guarda en DB"""
    trade = posiciones_abiertas.pop(par, None)
    if not trade:
        return

    precio_entrada = trade.get("precio_entrada", 0)
    cantidad       = trade.get("cantidad", 0)

    # Calcular PnL
    pnl_pct = ((precio_salida - precio_entrada) / precio_entrada) * config.LEVERAGE if precio_entrada > 0 else 0
    pnl_usd = pnl_pct * trade.get("balance_antes", 0) * config.RIESGO_POR_TRADE

    resultado = "WIN" if pnl_usd > 0 else ("LOSS" if pnl_usd < 0 else "BE")

    balance_actual = exchange.get_balance()

    # Actualizar balance en demo
    if config.MODO_DEMO:
        exchange.demo_actualizar_balance(pnl_usd)
        balance_actual = exchange.get_balance()

    # Guardar en DB
    database.guardar_trade({
        "par":              par,
        "lado":             "LONG",
        "precio_entrada":   precio_entrada,
        "precio_salida":    precio_salida,
        "cantidad":         cantidad,
        "pnl_usd":          pnl_usd,
        "pnl_pct":          pnl_pct * 100,
        "rsi_entrada":      trade.get("rsi", 0),
        "bb_posicion":      trade.get("bb", {}).get("posicion", 0),
        "atr_entrada":      trade.get("atr", 0),
        "sl_precio":        trade.get("sl", 0),
        "tp_precio":        trade.get("tp", 0),
        "resultado":        resultado,
        "motivo_cierre":    motivo,
        "balance_antes":    trade.get("balance_antes", 0),
        "balance_despues":  balance_actual,
        "timestamp_entrada": trade.get("timestamp", ""),
        "timestamp_salida":  datetime.now().isoformat(),
        "order_id_entrada":  trade.get("order_id", ""),
        "order_id_salida":   ""
    })

    trade["precio_salida"] = precio_salida

    print(f"[MAIN] {'✅' if resultado == 'WIN' else '❌'} CERRADO {par} | "
          f"entrada:{precio_entrada:.4f} → salida:{precio_salida:.4f} | "
          f"PnL: ${pnl_usd:+.4f} | {motivo}")

    notifier.trade_cerrado(trade, pnl_usd, motivo, balance_actual)
    database.guardar_balance(balance_actual, balance_actual, database.get_pnl_hoy())


def ciclo_principal():
    """Un ciclo completo del bot"""
    global fecha_actual

    # Nuevo día
    hoy = date.today()
    if hoy != fecha_actual:
        nuevo_dia()

    # Circuit breaker
    pausado, motivo_pausa = circuit_breaker_activo()
    if pausado:
        print(f"[MAIN] ⛔ CIRCUIT BREAKER: {motivo_pausa}")
        notifier.circuit_breaker(motivo_pausa, exchange.get_balance())
        time.sleep(3600)  # Pausa 1 hora
        return

    # Learner — evaluar pares periódicamente
    if learner.necesita_evaluacion():
        pares_validos = learner.evaluar_y_ajustar(config.PARES)
        learner.ajustar_parametros_globales()
    else:
        pares_validos = config.PARES

    # Monitorear posiciones existentes
    monitorear_posiciones()

    # Buscar nuevas señales
    pares_disponibles = pares_activos_para_operar()

    if pares_disponibles and len(posiciones_abiertas) < config.MAX_POSICIONES:
        print(f"\n[MAIN] Analizando {len(pares_disponibles)} pares...")

        # Intersección con pares validados por learner
        pares_a_analizar = [p for p in pares_disponibles if p in pares_validos]

        señales = analizar.analizar_todos(pares_a_analizar)

        nuevas = 0
        for señal in señales:
            if len(posiciones_abiertas) >= config.MAX_POSICIONES:
                break
            if abrir_posicion(señal):
                nuevas += 1

        if nuevas > 0:
            print(f"[MAIN] {nuevas} nuevas posiciones abiertas")
        else:
            print(f"[MAIN] Sin señales válidas en este ciclo")
    else:
        if not pares_disponibles:
            print(f"[MAIN] Todas las posiciones están ocupadas ({len(posiciones_abiertas)}/{config.MAX_POSICIONES})")

    # Estado del ciclo
    balance = exchange.get_balance()
    pnl_dia = database.get_pnl_hoy()
    print(f"\n[MAIN] 💰 Balance: ${balance:.2f} | PnL hoy: ${pnl_dia:+.4f} | "
          f"Posiciones: {len(posiciones_abiertas)}/{config.MAX_POSICIONES}")
    print(f"[MAIN] Próximo ciclo en {config.CICLO_SEGUNDOS}s — {datetime.now().strftime('%H:%M:%S')}")
    print("-" * 60)


def run():
    """Punto de entrada principal"""
    inicializar()

    ciclos = 0
    while True:
        try:
            ciclos += 1
            print(f"\n[MAIN] ═══ CICLO #{ciclos} — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ═══")
            ciclo_principal()
            time.sleep(config.CICLO_SEGUNDOS)

        except KeyboardInterrupt:
            print("\n[MAIN] Bot detenido por usuario")
            balance = exchange.get_balance()
            print(f"[MAIN] Balance final: ${balance:.2f}")
            break
        except Exception as e:
            print(f"\n[MAIN] ❌ ERROR en ciclo: {e}")
            if config.MODO_DEBUG:
                traceback.print_exc()
            notifier.error_critico(str(e))
            print("[MAIN] Reintentando en 60s...")
            time.sleep(60)


if __name__ == "__main__":
    run()
