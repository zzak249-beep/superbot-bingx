"""
main.py — APEX Bot v7.0 [INSTITUCIONAL]
========================================
Loop principal 24/7 en Railway.
Gestiona entradas, posiciones, trailing, TP parcial, aprendizaje IA.
"""

import sys, os, time, traceback
from datetime import datetime, date, timezone
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    stream=sys.stdout, force=True,
)
if os.getenv("LOG_LEVEL", "").upper() == "DEBUG":
    logging.getLogger().setLevel(logging.DEBUG)

log = logging.getLogger("main")
log.info("=== ARRANQUE APEX BOT v7.0 ===")

try:
    import config, exchange, analizar, memoria, scanner_pares, metaclaw
    from config_pares import PARES as PARES_FIJOS
except Exception as e:
    log.error(f"Import error: {e}\n{traceback.format_exc()}")
    sys.exit(1)

try:
    import optimizador
    _opt_ok = True
except Exception:
    _opt_ok = False

errores_cfg = config.validar()
for e in errores_cfg:
    log.warning(f"⚠️ CONFIG: {e}")

log.info(f"✅ Módulos OK | {config.VERSION}")


# ═══════════════════════════════════════════════════════
# CORRELACIÓN AMPLIADA
# ═══════════════════════════════════════════════════════

GRUPOS_CORR = [
    {"BTC-USDT", "ETH-USDT"},
    {"SOL-USDT", "SUI-USDT"},
    {"ARB-USDT", "OP-USDT"},
    {"DOGE-USDT", "SHIB-USDT", "PEPE-USDT", "WIF-USDT", "BONK-USDT", "FLOKI-USDT"},
    {"ORDI-USDT", "STX-USDT"},
    {"AAVE-USDT", "UNI-USDT", "MKR-USDT"},
    {"BNB-USDT", "TRX-USDT"},
    {"AXS-USDT", "SAND-USDT", "MANA-USDT", "IMX-USDT"},
    {"INJ-USDT", "TIA-USDT"},
]


def hay_correlacion(par, lado, posiciones):
    if not config.CORRELACION_ACTIVO:
        return False
    for g in GRUPOS_CORR:
        if par not in g:
            continue
        for p, pos in posiciones.items():
            if p in g and p != par and pos["lado"] == lado:
                log.info(f"[CORR] {par} bloqueado — {p} ya abierto {lado}")
                return True
    return False


# ═══════════════════════════════════════════════════════
# ESTADO
# ═══════════════════════════════════════════════════════

class Estado:
    def __init__(self):
        self.posiciones = {}
        self.pnl_hoy    = 0.0
        self.dia_actual = str(date.today())
        self.wins = self.losses = 0

    def reset_diario(self):
        hoy = str(date.today())
        if hoy != self.dia_actual:
            self.dia_actual = hoy
            self.pnl_hoy    = 0.0
            log.info(f"[RESET] Nuevo día {hoy}")

    def registrar_cierre(self, pnl):
        self.pnl_hoy += pnl
        if pnl > 0: self.wins += 1
        else:       self.losses += 1

    def max_perdida(self):
        return config.MAX_PERDIDA_DIA > 0 and self.pnl_hoy <= -config.MAX_PERDIDA_DIA


estado = Estado()


# ═══════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════

def _notif(msg: str):
    try:
        import requests as rq
        tok = config.TELEGRAM_TOKEN.strip()
        cid = config.TELEGRAM_CHAT_ID.strip()
        if not tok or not cid:
            return
        rq.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                json={"chat_id": cid, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        log.error(f"Telegram: {e}")


def _notif_entrada(s, trade_usdt, resultado):
    lado = "🟢 LONG" if s["lado"] == "LONG" else "🔴 SHORT"
    estado_str = "✅ *Ejecutado*" if resultado == "ok" else f"⚠️ *{resultado}*"
    mots = " + ".join(s.get("motivos", [])[:6])
    sweep_str = ""
    if s.get("sweep_bull") or s.get("sweep_bear"):
        sweep_str = f"💧 `SWEEP` fuerza={s.get('sweep_fuerza', 0)}\n"
    ob_str = ""
    if s.get("ob_fvg_bull") or s.get("ob_fvg_bear"):
        ob_str = f"🏆 `OB+FVG` quality={s.get('ob_quality', 0)}\n"
    elif s.get("ob_bull") or s.get("ob_bear"):
        ob_str = f"📦 `Order Block` quality={s.get('ob_quality', 0)}\n"
    choch_str = "🔄 `CHoCH/MSS`\n" if s.get("choch_bull") or s.get("choch_bear") else ""
    macro_str = f"₿ BTC macro: `{s.get('macro_btc_4h','?')}`\n" if s.get("macro_btc_4h") else ""

    _notif(
        f"{lado} — `{s['par']}` [{s.get('kz','')}]\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Entrada  : `{s['precio']:.6f}`\n"
        f"🔶 TP1(40%) : `{s['tp1']:.6f}`\n"
        f"✅ TP2      : `{s['tp']:.6f}`\n"
        f"🛑 SL       : `{s['sl']:.6f}`\n"
        f"📊 R:R      : `{s['rr']:.2f}x`\n"
        f"🏅 Score    : `{s['score']}/20`\n"
        f"📉 RSI      : `{s['rsi']:.1f}` | StochRSI:`{s.get('srsi_k',50):.0f}`\n"
        f"📈 HTF      : `{s.get('htf','?')}` / `{s.get('htf_4h','?')}`\n"
        f"🧩 Señales  : `{mots}`\n"
        f"{sweep_str}{ob_str}{choch_str}{macro_str}"
        f"💵 Trade    : `${trade_usdt:.2f}` × {config.LEVERAGE}x\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{estado_str}"
    )


def _notif_cierre(par, lado, entrada, salida, pnl, razon="", trade_usdt=0):
    ico  = "✅" if pnl >= 0 else "❌"
    comp = memoria._data["compounding"]
    _notif(
        f"{ico} *CIERRE {lado}* ({razon}) — `{par}`\n"
        f"`{entrada:.6f}` → `{salida:.6f}`\n"
        f"PnL: `${pnl:+.4f}`\n"
        f"💰 Pool: `${comp['ganancias']:.2f}` | Próx: `${memoria.get_trade_amount():.2f}`"
    )


def _mcl_aprender(pos, pnl):
    if not config.METACLAW_ACTIVO or not os.getenv("ANTHROPIC_API_KEY"):
        return
    try:
        metaclaw.aprender(pos, ganado=(pnl > 0), pnl=pnl)
    except Exception as e:
        log.debug(f"[MCL] aprender error: {e}")


# ═══════════════════════════════════════════════════════
# CARGAR POSICIONES AL ARRANQUE
# ═══════════════════════════════════════════════════════

def cargar_posiciones():
    if config.MODO_DEMO:
        return
    try:
        pos_reales = exchange.get_posiciones_abiertas()
        cargadas = 0
        for p in pos_reales:
            amt = float(p.get("positionAmt", 0) or 0)
            if amt == 0:
                continue
            sym = p.get("symbol", "")
            par = sym if "-" in sym else sym.replace("USDT", "-USDT")
            if par in estado.posiciones:
                continue
            lado  = "LONG" if amt > 0 else "SHORT"
            entry = float(p.get("entryPrice", 0) or 0)
            qty   = abs(amt)
            if entry <= 0 or qty <= 0:
                continue
            estado.posiciones[par] = {
                "lado": lado, "entrada": entry, "qty": qty,
                "sl": float(p.get("stopLoss", 0) or 0),
                "tp": float(p.get("takeProfit", 0) or 0),
                "tp1": 0.0, "atr": 0.0, "sl_trailing": float(p.get("stopLoss", 0) or 0),
                "tp1_hit": False, "ts": datetime.now(timezone.utc).isoformat(),
                "recuperada": True, "trade_usdt": config.TRADE_USDT_BASE,
                "score": 0, "motivos": [], "kz": "",
            }
            cargadas += 1
            log.info(f"[ARRANQUE] {lado} {par} @ {entry:.6f}")
        if cargadas:
            _notif(f"♻️ *APEX v7 — {cargadas} posición(es) recuperada(s)*")
    except Exception as e:
        log.error(f"[ARRANQUE] {e}")


# ═══════════════════════════════════════════════════════
# SINCRONIZACIÓN CON BINGX
# ═══════════════════════════════════════════════════════

def sincronizar():
    if not estado.posiciones or config.MODO_DEMO:
        return
    try:
        pos_reales = exchange.get_posiciones_abiertas()
        reales = set()
        for p in pos_reales:
            s = p.get("symbol", "")
            reales.add(s)
            reales.add(s.replace("-", ""))
            if "USDT" in s and "-" not in s:
                reales.add(s.replace("USDT", "-USDT"))

        cerradas = [par for par in estado.posiciones
                    if par not in reales and par.replace("-", "") not in reales]
        for par in cerradas:
            pos    = estado.posiciones[par]
            lado   = pos["lado"]
            entry  = pos["entrada"]
            qty    = pos["qty"]
            sl_ef  = pos.get("sl_trailing", pos["sl"])
            tp     = pos["tp"]
            precio = exchange.get_precio(par)

            if sl_ef > 0 and tp > 0:
                if lado == "LONG":
                    salida, razon = (tp, "TP") if precio >= tp * 0.98 else (sl_ef, "SL")
                    pnl = qty * (salida - entry)
                else:
                    salida, razon = (tp, "TP") if precio <= tp * 1.02 else (sl_ef, "SL")
                    pnl = qty * (entry - salida)
            else:
                salida, razon = precio, "BINGX"
                pnl = qty * ((precio - entry) if lado == "LONG" else (entry - precio))

            estado.registrar_cierre(pnl)
            memoria.registrar_resultado(par, pnl, lado,
                kz=pos.get("kz", ""), motivos=pos.get("motivos", []))
            _mcl_aprender(pos, pnl)
            try:
                analizar.registrar_trade_kz(pos.get("kz", "FUERA"), pnl > 0)
            except Exception:
                pass
            del estado.posiciones[par]
            _notif_cierre(par, lado, entry, salida, pnl, f"BingX-{razon}")
    except Exception as e:
        log.error(f"[SYNC] {e}")


# ═══════════════════════════════════════════════════════
# TRAILING STOP
# ═══════════════════════════════════════════════════════

def actualizar_trailing(par, pos, precio):
    if not config.TRAILING_ACTIVO:
        return
    atr  = pos.get("atr", 0)
    lado = pos["lado"]
    if atr <= 0:
        return
    act_dist   = atr * max(config.TRAILING_ACTIVAR, 1.0)
    trail_dist = atr * max(config.TRAILING_DISTANCIA, 0.8)

    if lado == "LONG":
        profit = precio - pos["entrada"]
        if profit < act_dist:
            return
        nuevo  = precio - trail_dist
        actual = pos.get("sl_trailing", pos["sl"])
        if nuevo > actual:
            pos["sl_trailing"] = nuevo
            exchange.actualizar_sl_bingx(par, nuevo, lado)
    else:
        profit = pos["entrada"] - precio
        if profit < act_dist:
            return
        nuevo  = precio + trail_dist
        actual = pos.get("sl_trailing", pos["sl"])
        if nuevo < actual:
            pos["sl_trailing"] = nuevo
            exchange.actualizar_sl_bingx(par, nuevo, lado)


# ═══════════════════════════════════════════════════════
# PARTIAL TP — TP1 al 40% cierra, SL a breakeven
# ═══════════════════════════════════════════════════════

def gestionar_partial_tp(par, pos, precio):
    if not config.PARTIAL_TP_ACTIVO or pos.get("tp1_hit"):
        return
    tp1  = pos.get("tp1", 0)
    lado = pos["lado"]
    if tp1 <= 0:
        return
    if not ((precio >= tp1) if lado == "LONG" else (precio <= tp1)):
        return

    # Cierre adaptativo según score
    score_pos = pos.get("score", 0)
    if   score_pos >= 16: pct, label = 0.25, "25%"
    elif score_pos >= 12: pct, label = 0.35, "35%"
    else:                 pct, label = 0.50, "50%"

    qty_tp1 = round(pos["qty"] * pct, 6)
    if not config.MODO_DEMO:
        res         = exchange.cerrar_posicion(par, qty_tp1, lado)
        salida_real = (res or {}).get("precio_salida", precio) or precio
    else:
        salida_real = precio

    entrada = pos["entrada"]
    pnl_p   = qty_tp1 * ((salida_real - entrada) if lado == "LONG" else (entrada - salida_real))
    estado.pnl_hoy += pnl_p
    memoria.registrar_ganancia_compounding(pnl_p)

    # SL a breakeven + pequeño buffer
    be = entrada * 1.001 if lado == "LONG" else entrada * 0.999
    pos["sl"] = pos["sl_trailing"] = be
    pos["qty"] -= qty_tp1
    pos["tp1_hit"] = True

    log.info(f"[TP1] {par} {label} @ {salida_real:.6f} PnL={pnl_p:+.4f} SL→BE={be:.6f}")
    _notif(
        f"🔶 *TP1* — `{par}` {lado}\n"
        f"`{label}` @ `{salida_real:.6f}` | PnL: `${pnl_p:+.4f}`\n"
        f"🔄 SL → BE `{be:.6f}` | Resto corre al TP2"
    )


# ═══════════════════════════════════════════════════════
# CIERRE POR TIEMPO / SIN MOVIMIENTO
# ═══════════════════════════════════════════════════════

def _check_time_exit(pos) -> bool:
    ts_str = pos.get("ts", "")
    if not ts_str:
        return False
    try:
        ts    = datetime.fromisoformat(ts_str)
        ahora = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (ahora - ts).total_seconds() / 3600 >= config.TIME_EXIT_HORAS
    except Exception:
        return False


def _check_sin_movimiento(par, pos, precio) -> bool:
    ts_str = pos.get("ts", "")
    if not ts_str or pos.get("tp1_hit"):
        return False
    try:
        ts    = datetime.fromisoformat(ts_str)
        ahora = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        horas     = (ahora - ts).total_seconds() / 3600
        if horas < 2.0:
            return False
        entrada   = pos["entrada"]
        movimiento = abs(precio - entrada) / entrada * 100
        if movimiento < 0.12:
            log.info(f"[SIN-MOV] {par} {horas:.1f}h mov={movimiento:.2f}% → cierre")
            return True
    except Exception:
        pass
    return False


# ═══════════════════════════════════════════════════════
# GESTIONAR POSICIONES ABIERTAS
# ═══════════════════════════════════════════════════════

def gestionar_posiciones():
    for par, pos in list(estado.posiciones.items()):
        try:
            precio = exchange.get_precio(par)
            if precio <= 0:
                continue
            lado = pos["lado"]
            qty  = pos["qty"]

            gestionar_partial_tp(par, pos, precio)

            # Cierre sin movimiento
            if _check_sin_movimiento(par, pos, precio):
                res         = exchange.cerrar_posicion(par, qty, lado)
                salida_real = (res or {}).get("precio_salida", precio) or precio
                pnl = qty * ((salida_real - pos["entrada"]) if lado == "LONG" else (pos["entrada"] - salida_real))
                estado.registrar_cierre(pnl)
                memoria.registrar_resultado(par, pnl, lado, kz=pos.get("kz",""), motivos=pos.get("motivos",[]))
                _mcl_aprender(pos, pnl)
                try: analizar.registrar_trade_kz(pos.get("kz","FUERA"), pnl > 0)
                except Exception: pass
                del estado.posiciones[par]
                _notif_cierre(par, lado, pos["entrada"], salida_real, pnl, "SIN-MOV")
                continue

            # Cierre por tiempo
            if _check_time_exit(pos):
                res         = exchange.cerrar_posicion(par, qty, lado)
                salida_real = (res or {}).get("precio_salida", precio) or precio
                pnl = qty * ((salida_real - pos["entrada"]) if lado == "LONG" else (pos["entrada"] - salida_real))
                estado.registrar_cierre(pnl)
                memoria.registrar_resultado(par, pnl, lado, kz=pos.get("kz",""), motivos=pos.get("motivos",[]))
                _mcl_aprender(pos, pnl)
                try: analizar.registrar_trade_kz(pos.get("kz","FUERA"), pnl > 0)
                except Exception: pass
                del estado.posiciones[par]
                _notif_cierre(par, lado, pos["entrada"], salida_real, pnl, "TIME")
                continue

            actualizar_trailing(par, pos, precio)

            sl_ef  = pos.get("sl_trailing", pos["sl"])
            tp     = pos["tp"]
            sl_hit = (precio <= sl_ef) if lado == "LONG" else (precio >= sl_ef)
            tp_hit = (precio >= tp)    if lado == "LONG" else (precio <= tp)

            razon = salida = None
            if sl_hit: razon = "TRAIL-SL" if pos.get("tp1_hit") else "SL"; salida = sl_ef
            elif tp_hit: razon = "TP2"; salida = tp

            if razon:
                res         = exchange.cerrar_posicion(par, qty, lado)
                salida_real = (res or {}).get("precio_salida", salida) or salida
                pnl = qty * ((salida_real - pos["entrada"]) if lado == "LONG" else (pos["entrada"] - salida_real))
                estado.registrar_cierre(pnl)
                memoria.registrar_resultado(par, pnl, lado, kz=pos.get("kz",""), motivos=pos.get("motivos",[]))
                _mcl_aprender(pos, pnl)
                try: analizar.registrar_trade_kz(pos.get("kz","FUERA"), pnl > 0)
                except Exception: pass
                del estado.posiciones[par]
                log.info(f"CIERRE {lado} {par} @ {salida_real:.6f} PnL={pnl:+.4f} ({razon})")
                _notif_cierre(par, lado, pos["entrada"], salida_real, pnl, razon,
                              pos.get("trade_usdt", config.TRADE_USDT_BASE))

        except Exception as e:
            log.error(f"gestionar {par}: {e}")
        time.sleep(0.3)


# ═══════════════════════════════════════════════════════
# EJECUTAR SEÑAL
# ═══════════════════════════════════════════════════════

def _pos_bot() -> int:
    return sum(1 for v in estado.posiciones.values() if not v.get("recuperada", False))


def _streak_mult() -> float:
    trades = memoria._data.get("trades", [])[-5:]
    if len(trades) < 3:
        return 1.0
    wins = sum(1 for t in trades if t.get("ganado"))
    losses = len(trades) - wins
    if wins >= 4:   return 1.4
    elif wins >= 3: return 1.2
    elif losses >= 4: return 0.6
    elif losses >= 3: return 0.8
    return 1.0


def ejecutar_senal(s: dict) -> str:
    par  = s["par"]
    lado = s["lado"]

    if par in estado.posiciones:
        return "skip"

    # Anti-hedge en BingX real
    if not config.MODO_DEMO:
        try:
            pos_reales = exchange.get_posiciones_abiertas()
            for p in pos_reales:
                sym = p.get("symbol", "")
                par_n = sym if "-" in sym else sym.replace("USDT", "-USDT")
                if par_n == par and float(p.get("positionAmt", 0) or 0) != 0:
                    return "skip"
        except Exception:
            pass

    if par in estado.posiciones and estado.posiciones[par]["lado"] != lado:
        return "bloq:anti-hedge"
    if hay_correlacion(par, lado, estado.posiciones):
        return "bloq:correlacion"
    if memoria.esta_bloqueado(par):
        return "bloq:par bloqueado"

    # MetaClaw validación IA
    if config.METACLAW_ACTIVO and os.getenv("ANTHROPIC_API_KEY"):
        try:
            mc = metaclaw.validar(s)
            if mc.get("metaclaw_activo"):
                log.info(f"[MCL] {par} {lado} → {'✅' if mc['aprobar'] else '❌'} conf={mc['confianza']} {mc['razon']}")
                if not mc["aprobar"] and mc["confianza"] >= config.METACLAW_VETO_MINIMO:
                    return f"bloq:MetaClaw (conf={mc['confianza']}) {mc['razon'][:40]}"
        except Exception as e:
            log.debug(f"[MCL] validar error: {e}")

    if _pos_bot() >= config.MAX_POSICIONES:
        return f"bloq:MAX_POSICIONES ({_pos_bot()}/{config.MAX_POSICIONES})"
    if estado.max_perdida():
        return f"bloq:circuit-breaker PnL={estado.pnl_hoy:.2f}"

    balance_total = exchange.get_balance()
    margen_libre  = exchange.get_available_margin()

    if balance_total > 0 and not config.MODO_DEMO:
        margen_usado_pct = (balance_total - margen_libre) / balance_total * 100
        if margen_usado_pct > 75:
            return f"bloq:exposición {margen_usado_pct:.0f}%"

    margen_min = max(config.TRADE_USDT_BASE / config.LEVERAGE * 1.3, 2.0)
    if margen_libre < margen_min and not config.MODO_DEMO:
        return f"bloq:margen libre ${margen_libre:.2f}"

    # Sizing dinámico
    trade_usdt = memoria.get_trade_amount()
    streak     = _streak_mult()
    if streak != 1.0:
        log.info(f"[STREAK] {par} mult={streak:.1f}x")

    if balance_total > config.TRADE_USDT_BASE * 2 and not config.MODO_DEMO:
        t_bal = balance_total * 0.12
        trade_usdt = min(max(trade_usdt, t_bal), config.TRADE_USDT_MAX)

    trade_usdt = round(min(trade_usdt * streak, config.TRADE_USDT_MAX), 2)

    # Bonus por score
    sc = s.get("score", 0)
    if   sc >= 16: mult = 2.0
    elif sc >= 13: mult = 1.5
    elif sc >= 11: mult = 1.25
    else:          mult = 1.0
    if mult > 1.0:
        trade_usdt = round(min(trade_usdt * mult, config.TRADE_USDT_MAX, balance_total * 0.15), 2)
        log.info(f"[SIZING] {par} score={sc} → ${trade_usdt:.2f}")

    qty = exchange.calcular_cantidad(par, trade_usdt, s["precio"])
    if qty <= 0:
        return f"bloq:qty=0"

    if balance_total < trade_usdt and not config.MODO_DEMO:
        t_red = balance_total * 0.80
        if t_red >= config.TRADE_USDT_BASE:
            trade_usdt = round(t_red, 2)
            qty = exchange.calcular_cantidad(par, trade_usdt, s["precio"])
            if qty <= 0:
                return "bloq:margen"
        else:
            return f"bloq:margen ${balance_total:.2f}"

    if lado == "LONG":
        res = exchange.abrir_long(par, qty, s["precio"], s["sl"], s["tp"])
    else:
        res = exchange.abrir_short(par, qty, s["precio"], s["sl"], s["tp"])

    if not res or "error" in res:
        err = (res or {}).get("error", "vacía")
        log.error(f"Orden fallida {lado} {par}: {err}")
        memoria.registrar_error_api(par)
        return f"error:{err[:80]}"

    fill  = float(res.get("fill_price", 0) or 0) or exchange.get_precio(par) or s["precio"]
    qty_r = float(res.get("executedQty", qty) or qty)
    atr   = s.get("atr", 0)

    if atr > 0:
        sl_r  = (fill - atr * config.SL_ATR_MULT)     if lado == "LONG" else (fill + atr * config.SL_ATR_MULT)
        tp_r  = (fill + atr * config.TP_ATR_MULT)     if lado == "LONG" else (fill - atr * config.TP_ATR_MULT)
        tp1_r = (fill + atr * config.PARTIAL_TP1_MULT) if lado == "LONG" else (fill - atr * config.PARTIAL_TP1_MULT)
    else:
        ratio = fill / s["precio"] if s["precio"] > 0 else 1.0
        sl_r  = s["sl"]  * ratio
        tp_r  = s["tp"]  * ratio
        tp1_r = s["tp1"] * ratio

    memoria.registrar_inversion(trade_usdt)

    estado.posiciones[par] = {
        "lado":        lado,
        "entrada":     fill,
        "qty":         qty_r,
        "sl":          sl_r,
        "tp":          tp_r,
        "tp1":         tp1_r,
        "atr":         atr,
        "sl_trailing": sl_r,
        "tp1_hit":     False,
        "ts":          datetime.now(timezone.utc).isoformat(),
        "recuperada":  False,
        "score":       s["score"],
        "motivos":     s.get("motivos", []),
        "kz":          s.get("kz", ""),
        "trade_usdt":  trade_usdt,
        "ob_fvg":      s.get("ob_fvg_bull") or s.get("ob_fvg_bear"),
        # Guardar señal completa para MetaClaw aprender
        **{k: s.get(k) for k in ("htf", "htf_4h", "sweep_bull", "sweep_bear", "sweep_fuerza",
                                  "choch_bull", "choch_bear", "ob_quality", "discount", "premium",
                                  "vol_delta", "macro_btc_4h", "srsi_k", "patron", "sobre_vwap",
                                  "ob_fvg_bull", "ob_fvg_bear")},
    }

    slip = abs(fill - s["precio"]) / s["precio"] * 100 if s["precio"] > 0 else 0
    log.info(
        f"✅ {lado} {par} fill:{fill:.6f} "
        f"{'⚠️SLIP:'+str(round(slip,1))+'%' if slip > 0.5 else ''} "
        f"${trade_usdt:.2f}×{config.LEVERAGE}x "
        f"SL:{sl_r:.6f} TP:{tp_r:.6f} score:{s['score']}/20"
    )
    return "ok"


# ═══════════════════════════════════════════════════════
# REPORTE HORARIO
# ═══════════════════════════════════════════════════════

def enviar_reporte(balance):
    pos_txt = ""
    for par, pos in estado.posiciones.items():
        p_a = exchange.get_precio(par)
        pnl = 0
        if p_a > 0:
            pnl = pos["qty"] * ((p_a - pos["entrada"]) if pos["lado"] == "LONG" else (pos["entrada"] - p_a))
        fase = "🔶→TP2" if pos.get("tp1_hit") else "▶→TP1"
        ico  = "🟢" if pos["lado"] == "LONG" else "🔴"
        pos_txt += f"  {ico} `{par}` PnL:${pnl:+.2f} {fase} [{pos.get('score','?')}/20]\n"
    if not pos_txt:
        pos_txt = "  _(sin posiciones)_\n"

    w, l = estado.wins, estado.losses
    wr   = f"{w/(w+l)*100:.1f}%" if (w+l) > 0 else "N/A"
    comp = memoria._data["compounding"]
    kz   = analizar.en_killzone()
    total_trades = len(memoria._data.get("trades", []))

    mc_txt = ""
    try:
        if config.METACLAW_ACTIVO:
            mc_txt = "\n" + metaclaw.get_resumen()
    except Exception:
        pass

    _notif(
        f"📊 *{config.VERSION}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance    : `${balance:.2f}`\n"
        f"📈 Sesión     : `{w}W/{l}L` WR:`{wr}`\n"
        f"PnL hoy       : `${estado.pnl_hoy:+.4f}`\n"
        f"🕐 KZ         : `{kz['nombre']}`\n"
        f"💵 Trade base : `${config.TRADE_USDT_BASE:.0f}` × {config.LEVERAGE}x\n"
        f"📊 Próx trade : `${memoria.get_trade_amount():.2f}`\n"
        f"💹 Pool       : `${comp['ganancias']:.2f}`\n"
        f"🏆 Trades tot.: `{total_trades}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Posiciones:\n{pos_txt}"
        f"{mc_txt}"
    )


# ═══════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════

def main():
    log.info("=" * 70)
    log.info(f"  {config.VERSION}")
    log.info(f"  LEVERAGE:{config.LEVERAGE}x | MAX_POS:{config.MAX_POSICIONES} | TF:{config.TIMEFRAME}/{config.MTF_TIMEFRAME}/4H")
    log.info(f"  SCORE≥{config.SCORE_MIN}/20 | MIN_RR:{config.MIN_RR} | SWEEP+MSS obligatorio para máx calidad")
    log.info(f"  MetaClaw:{'✅ ACTIVO' if config.METACLAW_ACTIVO else '❌'} | Premium/Discount | StochRSI | VolDelta")
    log.info(f"  SL estructural | TP hacia liquidez | Trailing activar={config.TRAILING_ACTIVAR}x")
    log.info("=" * 70)

    if config.MEMORY_DIR:
        import pathlib
        pathlib.Path(config.MEMORY_DIR).mkdir(parents=True, exist_ok=True)

    exchange.sync_server_time()
    exchange.diagnostico_balance()
    balance = exchange.get_balance()
    log.info(f"Balance inicial: ${balance:.2f} USDT")

    if balance <= 0 and not config.MODO_DEMO:
        time.sleep(3)
        balance = exchange.get_balance()
        if balance <= 0:
            _notif("🚨 *Balance = $0* — Verificar API keys")

    if _opt_ok:
        optimizador.iniciar()

    try:
        exchange._cargar_contratos()
        log.info(f"Contratos: {len(exchange._CONTRATOS_FUTURES)} pares")
    except Exception as e:
        log.warning(f"[STARTUP] contratos: {e}")

    try:
        cargar_posiciones()
    except Exception as e:
        log.warning(f"[STARTUP] posiciones: {e}")

    try:
        pares_raw = scanner_pares.get_pares_cached(config.VOLUMEN_MIN_24H)
    except Exception:
        from config_pares import PARES as pares_raw

    bloq_cfg    = set(config.PARES_BLOQUEADOS)
    fut_validos = exchange._CONTRATOS_FUTURES
    pares_raw   = [p for p in pares_raw if p not in bloq_cfg
                   and (not fut_validos or p in fut_validos)]

    prioritarios = [p for p in (PARES_FIJOS + config.PARES_PRIORITARIOS) if p in set(pares_raw)]
    top_mem      = [p for p in memoria.get_top_pares(10) if p in set(pares_raw)]
    resto        = [p for p in pares_raw if p not in set(prioritarios) and p not in set(top_mem)]
    pares        = prioritarios + top_mem + resto
    if config.MAX_PARES_SCAN > 0:
        pares = pares[:config.MAX_PARES_SCAN]

    log.info(f"Pares: {len(pares)} ({len(prioritarios)} prio + {len(top_mem)} top-mem)")

    _notif(
        f"🦅 *{config.VERSION}* arrancado\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance : `${balance:.2f}`\n"
        f"🔬 Motor   : Sweep+MSS+OB+FVG | StochRSI | VolDelta | 3xMTF\n"
        f"📊 Pares   : `{len(pares)}` (vol>${config.VOLUMEN_MIN_24H/1e6:.0f}M)\n"
        f"🏅 Score≥`{config.SCORE_MIN}/20` | R:R≥`{config.MIN_RR}x`\n"
        f"🔴 *LIVE — DINERO REAL — 24/7*"
    )

    ciclo           = 0
    last_reporte    = time.time()
    last_scan_pares = time.time()

    while True:
        try:
            ciclo += 1
            main._err_ciclo = 0
            estado.reset_diario()
            balance = exchange.get_balance()
            kz      = analizar.en_killzone()

            try:
                analizar.actualizar_macro_btc()
            except Exception:
                pass

            log.info(
                f"Ciclo {ciclo} | {datetime.now(timezone.utc).strftime('%H:%M UTC')} | "
                f"Bal:${balance:.2f} | Pos:{_pos_bot()}/{config.MAX_POSICIONES} | "
                f"PnL:${estado.pnl_hoy:+.4f} | KZ:{kz['nombre']}"
            )

            # Refrescar pares cada hora
            if time.time() - last_scan_pares > 3600:
                try:
                    nuevos = scanner_pares.get_pares_cached(config.VOLUMEN_MIN_24H)
                    fv     = exchange._CONTRATOS_FUTURES
                    nuevos = [p for p in nuevos if p not in bloq_cfg and (not fv or p in fv)]
                    bloq_m = set(memoria.get_pares_bloqueados())
                    top_m  = [p for p in memoria.get_top_pares(10) if p in set(nuevos)]
                    resto_n = [p for p in nuevos if p not in set(top_m) and p not in bloq_m]
                    pares   = prioritarios + top_m + resto_n
                    if config.MAX_PARES_SCAN > 0:
                        pares = pares[:config.MAX_PARES_SCAN]
                    log.info(f"Pares actualizados: {len(pares)}")
                    last_scan_pares = time.time()
                except Exception as e:
                    log.warning(f"[SCAN] {e}")

            # Circuit breaker
            if estado.max_perdida():
                log.warning(f"🛑 Máx pérdida (${estado.pnl_hoy:.2f}) — pausa 30min")
                _notif(f"🛑 *Máx pérdida diaria* `${estado.pnl_hoy:.2f}` — Pausa 30min")
                time.sleep(1800)
                continue

            sincronizar()

            if estado.posiciones:
                gestionar_posiciones()
                balance = exchange.get_balance()

            if _pos_bot() < config.MAX_POSICIONES:
                bloq_ahora = set(memoria.get_pares_bloqueados())
                pares_scan = [p for p in pares
                              if p not in estado.posiciones and p not in bloq_ahora]

                log.info(f"Escaneando {len(pares_scan)} pares | Score≥{config.SCORE_MIN}/20 | KZ:{kz['nombre']}")
                senales = analizar.analizar_todos(pares_scan, workers=config.ANALISIS_WORKERS)

                if senales:
                    log.info(f"✓ {len(senales)} señal(es):")
                    for s in senales[:5]:
                        sweep_tag = "🌊SWEEP" if (s.get("sweep_bull") or s.get("sweep_bear")) else ""
                        ob_tag    = "🏆OB+FVG" if (s.get("ob_fvg_bull") or s.get("ob_fvg_bear")) else ""
                        log.info(
                            f"  {s['lado']:5s} {s['par']:15s} "
                            f"score={s['score']}/20 RSI={s['rsi']:.0f} "
                            f"RR={s['rr']:.2f} KZ={s['kz']} HTF={s.get('htf','?')}/{s.get('htf_4h','?')} "
                            f"chop={s.get('chop',0):.0f} {sweep_tag}{ob_tag}"
                        )
                else:
                    log.info("Sin señales este ciclo")

                for s in senales:
                    if _pos_bot() >= config.MAX_POSICIONES:
                        break
                    if s["par"] in estado.posiciones:
                        continue

                    # Ajustar score por historial
                    s["score"] = memoria.ajustar_score(
                        s["par"], s["score"],
                        kz=s.get("kz", ""), motivos=s.get("motivos", []),
                    )
                    if s["score"] < config.SCORE_MIN:
                        log.info(f"[APRENDE] {s['par']} score={s['score']} < {config.SCORE_MIN}")
                        continue

                    if not exchange.par_es_soportado(s["par"]):
                        continue

                    resultado = ejecutar_senal(s)

                    if resultado == "skip":
                        continue

                    if resultado and resultado.startswith("error:"):
                        log.error(f"[API-ERR] {s['par']}: {resultado[6:]}")
                        if "margin" in resultado.lower() or "insufficient" in resultado.lower():
                            continue
                        if not hasattr(main, "_err_ciclo"):
                            main._err_ciclo = 0
                        if main._err_ciclo < 2:
                            _notif(f"🚨 *Error* `{s['par']}` `{resultado[6:80]}`")
                            main._err_ciclo += 1
                        continue

                    silenciosos = ("MAX_POSICIONES", "exposición", "margen libre", "correlacion")
                    if resultado and any(x in resultado for x in silenciosos):
                        log.info(f"[SKIP] {s['par']} — {resultado}")
                        continue

                    _notif_entrada(s, memoria.get_trade_amount(), resultado)
                    if resultado == "ok":
                        balance = exchange.get_balance()
                        time.sleep(2)

            if time.time() - last_reporte >= 3600:
                enviar_reporte(balance)
                _notif(memoria.resumen())
                last_reporte = time.time()

        except KeyboardInterrupt:
            log.info("Detenido manualmente")
            _notif("🛑 *APEX Bot detenido.*")
            break
        except Exception as e:
            log.error(f"ERROR CICLO {ciclo}: {e}\n{traceback.format_exc()}")
            try:
                _notif(f"🚨 *Error ciclo {ciclo}*\n`{str(e)[:200]}`")
            except Exception:
                pass

        log.info(f"Próximo ciclo en {config.LOOP_SECONDS}s")
        log.info("-" * 60)
        time.sleep(config.LOOP_SECONDS)


if __name__ == "__main__":
    main()
