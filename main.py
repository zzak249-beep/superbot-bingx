"""
main.py — Loop principal del bot
- Envía señales a Telegram aunque no haya fondos (para operar manual)
- Si hay fondos, abre órdenes automáticamente
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

posiciones_abiertas = {}
balance_inicio_dia  = 0.0
fecha_actual        = None


def inicializar():
    global balance_inicio_dia, fecha_actual

    print("=" * 60)
    print("  BOT22 — BB+RSI Elite v5")
    print(f"  Modo: {'DEMO' if config.MODO_DEMO else 'REAL'}")
    print("=" * 60)

    database.init_db()

    # ── DEBUG: verifica variables de entorno ──────────────────
    api_key    = config.BINGX_API_KEY.strip()
    secret_key = config.BINGX_SECRET_KEY.strip()

    print(f"[CONFIG] BINGX_API_KEY    : {'✓ SET (' + api_key[:6] + '...)' if api_key else '✗ VACÍA — añadir en Railway Variables'}")
    print(f"[CONFIG] BINGX_SECRET_KEY : {'✓ SET (' + secret_key[:4] + '...)' if secret_key else '✗ VACÍA — añadir en Railway Variables'}")
    print(f"[CONFIG] TELEGRAM_TOKEN   : {'✓ SET' if config.TELEGRAM_TOKEN.strip() else '✗ VACÍA'}")
    print(f"[CONFIG] TELEGRAM_CHAT_ID : {'✓ SET' if config.TELEGRAM_CHAT_ID.strip() else '✗ VACÍA'}")

    if api_key and secret_key:
        # Test rápido de firma
        import requests as _req, hmac as _hmac, hashlib as _hash, time as _time
        ts  = int(_time.time() * 1000)
        qs  = f"currency=USDT&recvWindow=10000&timestamp={ts}"
        sig = _hmac.new(secret_key.encode(), qs.encode(), _hash.sha256).hexdigest()
        url = f"https://open-api.bingx.com/openApi/swap/v2/user/balance?{qs}&signature={sig}"
        try:
            r = _req.get(url, headers={"X-BX-APIKEY": api_key}, timeout=10)
            j = r.json()
            print(f"[BALANCE_DEBUG] HTTP={r.status_code} | code={j.get('code')} | {str(j.get('data',''))[:150]}")
            if j.get("code") == 100001:
                print("[BALANCE_DEBUG] ✗ Signature mismatch — comprueba que BINGX_SECRET_KEY sea la Secret Key (NO la API Key)")
            elif j.get("code") == 0:
                print("[BALANCE_DEBUG] ✓ Autenticación correcta")
        except Exception as e:
            print(f"[BALANCE_DEBUG] ERROR={e}")
    # ── FIN DEBUG ──────────────────────────────────────────────

    balance_inicio_dia = exchange.get_balance()
    fecha_actual = date.today()

    print(f"[MAIN] Balance: ${balance_inicio_dia:.2f}")
    print(f"[MAIN] Pares: {len(config.PARES)}")

    notifier.bot_iniciado(config.PARES, balance_inicio_dia)


def nuevo_dia():
    global balance_inicio_dia, fecha_actual
    balance_inicio_dia = exchange.get_balance()
    fecha_actual = date.today()
    print(f"[MAIN] Nuevo dia: {fecha_actual} | Balance: ${balance_inicio_dia:.2f}")


def circuit_breaker_activo():
    balance_actual = exchange.get_balance()
    if balance_actual <= 0:
        return False, ""  # Sin fondos no activamos CB
    pnl_dia = balance_actual - balance_inicio_dia
    pnl_pct = pnl_dia / balance_inicio_dia if balance_inicio_dia > 0 else 0
    if pnl_pct <= config.MAX_PNL_NEGATIVO_DIA:
        return True, f"PnL dia {pnl_pct*100:.1f}%"
    racha = database.get_racha_perdidas_hoy()
    if racha >= config.MAX_PERDIDAS_SEGUIDAS:
        return True, f"{racha} perdidas consecutivas"
    return False, ""


def enviar_senal_telegram(senal: dict, balance: float):
    """Envía señal a Telegram para ejecución manual O automática"""
    par     = senal["par"]
    precio  = senal["precio"]
    sl      = senal["sl"]
    tp      = senal["tp"]
    rsi     = senal["rsi"]
    rr      = senal["rr"]
    score   = senal["score"]

    modo = "🤖 AUTO" if balance > 1 else "👤 MANUAL"

    import requests as req
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        return

    msg = (
        f"📈 <b>SEÑAL LONG — {modo}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 Par: <b>{par}</b>\n"
        f"💵 Entrada: <b>${precio:.6f}</b>\n"
        f"🔴 SL: ${sl:.6f}\n"
        f"🟢 TP: ${tp:.6f}\n"
        f"📐 R:R: {rr:.2f} | RSI: {rsi:.1f} | Score: {score}\n"
        f"🏦 Balance: ${balance:.2f}\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )

    try:
        req.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        print(f"[TELEGRAM] Error: {e}")


def abrir_posicion(senal: dict, balance: float) -> bool:
    par    = senal["par"]
    precio = senal["precio"]
    sl     = senal["sl"]
    tp     = senal["tp"]

    if par in posiciones_abiertas:
        return False
    if len(posiciones_abiertas) >= config.MAX_POSICIONES:
        return False

    cantidad = exchange.calcular_cantidad(par, balance, precio)
    if cantidad <= 0:
        print(f"[MAIN] {par}: cantidad = 0, skip")
        return False

    exchange.set_leverage(par, config.LEVERAGE)
    trade = exchange.abrir_long(par, cantidad, precio, sl, tp)
    if not trade:
        return False

    trade["rsi"]          = senal["rsi"]
    trade["atr"]          = senal["atr"]
    trade["bb"]           = senal.get("bb", {})
    trade["rr"]           = senal["rr"]
    trade["balance_antes"]= balance

    posiciones_abiertas[par] = trade
    print(f"[MAIN] ABIERTO {par} | entrada:{precio:.6f} SL:{sl:.6f} TP:{tp:.6f} qty:{cantidad}")
    notifier.trade_abierto(trade)
    return True


def monitorear_posiciones():
    if not posiciones_abiertas:
        return

    for par, trade in list(posiciones_abiertas.items()):
        try:
            precio_actual  = exchange.get_precio(par)
            precio_entrada = trade.get("precio_entrada", 0)
            sl = trade.get("sl", 0)
            tp = trade.get("tp", 0)

            cerrada = False
            motivo  = ""
            precio_salida = precio_actual

            if config.MODO_DEMO:
                if precio_actual <= sl:
                    cerrada = True; motivo = "SL"; precio_salida = sl
                elif precio_actual >= tp:
                    cerrada = True; motivo = "TP"; precio_salida = tp
            else:
                posicion = exchange.get_posicion(par)
                if not posicion:
                    cerrada = True; motivo = "SL/TP (exchange)"

            if cerrada:
                _cerrar_y_registrar(par, precio_salida, motivo)

        except Exception as e:
            print(f"[MAIN] Error monitoreando {par}: {e}")


def _cerrar_y_registrar(par: str, precio_salida: float, motivo: str):
    trade = posiciones_abiertas.pop(par, None)
    if not trade:
        return

    precio_entrada = trade.get("precio_entrada", 0)
    cantidad       = trade.get("cantidad", 0)
    balance_antes  = trade.get("balance_antes", 0)

    pnl_pct = ((precio_salida - precio_entrada) / precio_entrada) * config.LEVERAGE if precio_entrada > 0 else 0
    pnl_usd = pnl_pct * balance_antes * config.RIESGO_POR_TRADE
    resultado = "WIN" if pnl_usd > 0 else ("LOSS" if pnl_usd < 0 else "BE")

    balance_actual = exchange.get_balance()
    if config.MODO_DEMO:
        exchange.demo_actualizar_balance(pnl_usd)
        balance_actual = exchange.get_balance()

    database.guardar_trade({
        "par": par, "lado": "LONG",
        "precio_entrada": precio_entrada, "precio_salida": precio_salida,
        "cantidad": cantidad, "pnl_usd": pnl_usd, "pnl_pct": pnl_pct * 100,
        "rsi_entrada": trade.get("rsi", 0),
        "bb_posicion": trade.get("bb", {}).get("posicion", 0),
        "atr_entrada": trade.get("atr", 0),
        "sl_precio": trade.get("sl", 0), "tp_precio": trade.get("tp", 0),
        "resultado": resultado, "motivo_cierre": motivo,
        "balance_antes": balance_antes, "balance_despues": balance_actual,
        "timestamp_entrada": trade.get("timestamp", ""),
        "timestamp_salida": datetime.now().isoformat(),
        "order_id_entrada": trade.get("order_id", ""), "order_id_salida": ""
    })

    trade["precio_salida"] = precio_salida
    emoji = "WIN" if resultado == "WIN" else "LOSS"
    print(f"[MAIN] {emoji} {par} | {precio_entrada:.6f} -> {precio_salida:.6f} | PnL: ${pnl_usd:+.4f} | {motivo}")
    notifier.trade_cerrado(trade, pnl_usd, motivo, balance_actual)


def ciclo_principal():
    global fecha_actual

    if date.today() != fecha_actual:
        nuevo_dia()

    pausado, motivo_pausa = circuit_breaker_activo()
    if pausado:
        print(f"[MAIN] CIRCUIT BREAKER: {motivo_pausa}")
        notifier.circuit_breaker(motivo_pausa, exchange.get_balance())
        time.sleep(3600)
        return

    if learner.necesita_evaluacion():
        pares_validos = learner.evaluar_y_ajustar(config.PARES)
        learner.ajustar_parametros_globales()
    else:
        pares_validos = config.PARES

    monitorear_posiciones()

    balance = exchange.get_balance()
    pares_libres = [p for p in pares_validos if p not in posiciones_abiertas]

    if pares_libres:
        print(f"\n[MAIN] Analizando {len(pares_libres)} pares | Balance: ${balance:.2f}")
        senales = analizar.analizar_todos(pares_libres)

        if senales:
            print(f"[MAIN] {len(senales)} senales encontradas")
            for senal in senales:
                # SIEMPRE enviar señal a Telegram (para operar manual si no hay fondos)
                enviar_senal_telegram(senal, balance)

                # Solo abrir orden automática si hay fondos suficientes
                if balance > 1 and len(posiciones_abiertas) < config.MAX_POSICIONES:
                    abrir_posicion(senal, balance)
        else:
            print(f"[MAIN] Sin senales validas")

    pnl_dia = database.get_pnl_hoy()
    print(f"[MAIN] Balance: ${balance:.2f} | PnL hoy: ${pnl_dia:+.4f} | Pos: {len(posiciones_abiertas)}/{config.MAX_POSICIONES}")
    print(f"[MAIN] Proximo ciclo en {config.CICLO_SEGUNDOS}s — {datetime.now().strftime('%H:%M:%S')}")
    print("-" * 60)


def run():
    inicializar()
    ciclos = 0
    while True:
        try:
            ciclos += 1
            print(f"\n[MAIN] CICLO #{ciclos} — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            ciclo_principal()
            time.sleep(config.CICLO_SEGUNDOS)
        except KeyboardInterrupt:
            print("\n[MAIN] Bot detenido")
            break
        except Exception as e:
            print(f"\n[MAIN] ERROR: {e}")
            if config.MODO_DEBUG:
                traceback.print_exc()
            notifier.error_critico(str(e))
            time.sleep(60)


if __name__ == "__main__":
    run()
