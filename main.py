"""
main.py — BingX RSI+BB Bot v5.2
Correcciones sobre v5.1:
  - FIX CRÍTICO: cargar_posiciones_desde_bingx() al arranque
    → evita abrir LONG/SHORT sobre posición ya abierta tras reinicio
  - FIX: precio en notificación nunca muestra 0.000000
  - FIX: R:R correcto (dependía del precio 0)
"""

import sys, os, time, traceback
from datetime import datetime, date, timezone

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    stream=sys.stdout, force=True,
)
log = logging.getLogger("main")
log.info("=== ARRANQUE BOT v5.2 ===")

try:
    import config, exchange, analizar, notifier, memoria
except Exception as e:
    log.error(f"ERROR importando módulos: {e}")
    log.error(traceback.format_exc())
    sys.exit(1)

try:
    from config_pares import PARES as PARES_FIJOS
except Exception:
    PARES_FIJOS = []

log.info(f"Módulos OK | {config.VERSION}")
log.info(f"SCORE≥{config.SCORE_MIN} | LEV:{config.LEVERAGE}x | "
         f"SL:{config.SL_ATR_MULT}×ATR | TP:{config.TP_ATR_MULT}×ATR | "
         f"PARTIAL_TP:{getattr(config,'PARTIAL_TP_ACTIVO',True)} | "
         f"TIME_EXIT:{getattr(config,'TIME_EXIT_HORAS',8)}h")


# ═══════════════════════════════════════════════════════
# ESTADO
# ═══════════════════════════════════════════════════════

class Estado:
    def __init__(self):
        self.posiciones    = {}
        self.pnl_hoy       = 0.0
        self.dia_actual    = str(date.today())
        self.wins = self.losses = 0

    def reset_diario(self):
        hoy = str(date.today())
        if hoy != self.dia_actual:
            self.dia_actual = hoy
            self.pnl_hoy    = 0.0
            log.info(f"Reset diario — {hoy}")

    def registrar_cierre(self, pnl):
        self.pnl_hoy += pnl
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1

estado = Estado()


# ═══════════════════════════════════════════════════════
# PARES
# ═══════════════════════════════════════════════════════

def preparar_pares(pares_raw):
    bloqueados   = set(getattr(config, "PARES_BLOQUEADOS", []))
    prioritarios = getattr(config, "PARES_PRIORITARIOS", [])
    limpios      = [p for p in pares_raw if p not in bloqueados]
    top          = [p for p in prioritarios if p in set(limpios)]
    resto        = [p for p in limpios if p not in set(top)]
    log.info(f"Pares: {len(pares_raw)} brutos → {len(bloqueados)} bloqueados → "
             f"{len(top)} prioritarios + {len(resto)} resto = {len(top+resto)} activos")
    return top + resto


# ═══════════════════════════════════════════════════════
# ▶▶ FIX CRÍTICO: CARGAR POSICIONES AL ARRANQUE ◀◀
# ═══════════════════════════════════════════════════════

def cargar_posiciones_desde_bingx():
    """
    Lee las posiciones abiertas reales en BingX y las registra
    en estado.posiciones para que el bot NO vuelva a entrar
    en el mismo par tras un reinicio.
    """
    if config.MODO_DEMO:
        log.info("[ARRANQUE] DEMO — no se cargan posiciones de BingX")
        return

    try:
        pos_reales = exchange.get_posiciones_abiertas()
        if not pos_reales:
            log.info("[ARRANQUE] Sin posiciones abiertas en BingX")
            return

        cargadas = 0
        for p in pos_reales:
            symbol = p.get("symbol", "")
            # Normalizar símbolo → formato "XXX-USDT"
            par = symbol if "-" in symbol else symbol.replace("USDT", "-USDT")

            # Evitar duplicados
            if par in estado.posiciones:
                continue

            # Determinar lado desde BingX (positionSide o qty positivo/negativo)
            pos_side = p.get("positionSide", "").upper()
            amt      = float(p.get("positionAmt", p.get("qty", 0)) or 0)

            if pos_side == "LONG" or amt > 0:
                lado = "LONG"
            elif pos_side == "SHORT" or amt < 0:
                lado = "SHORT"
            else:
                log.warning(f"[ARRANQUE] {par} — no se pudo determinar lado, ignorado")
                continue

            entrada = float(p.get("entryPrice", p.get("entrada", 0)) or 0)
            qty     = abs(amt)

            if entrada <= 0 or qty <= 0:
                log.warning(f"[ARRANQUE] {par} — entrada/qty inválido, ignorado")
                continue

            # Reconstruir SL/TP mínimos basados en ATR 0 (se actualizarán en el ciclo)
            # Lo importante es bloquear la entrada en este par
            estado.posiciones[par] = {
                "lado":        lado,
                "entrada":     entrada,
                "qty":         qty,
                "sl":          float(p.get("sl", 0) or 0),
                "tp":          float(p.get("tp", 0) or 0),
                "tp1":         0.0,
                "atr":         0.0,
                "sl_trailing": float(p.get("sl", 0) or 0),
                "tp1_hit":     False,
                "ts":          datetime.now(timezone.utc).isoformat(),
                "recuperada":  True,   # marcar para saber que viene del arranque
            }
            cargadas += 1
            log.info(f"[ARRANQUE] Posición recuperada: {lado} {par} "
                     f"entrada={entrada} qty={qty}")

        if cargadas:
            log.warning(f"[ARRANQUE] ⚠️ {cargadas} posición(es) cargadas desde BingX "
                        f"— el bot NO abrirá nuevas órdenes en esos pares")
            _notif(
                f"♻️ *Bot reiniciado — posiciones recuperadas*\n"
                + "\n".join(
                    f"  {'🟢' if v['lado']=='LONG' else '🔴'} `{k}` "
                    f"{v['lado']} @ `{v['entrada']:.6f}`"
                    for k, v in estado.posiciones.items()
                    if v.get("recuperada")
                )
            )
        else:
            log.info("[ARRANQUE] Sin posiciones activas recuperadas")

    except Exception as e:
        log.error(f"[ARRANQUE] Error cargando posiciones: {e}")
        log.error(traceback.format_exc())


# ═══════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════

def _notif(msg):
    try:
        import requests
        tok = config.TELEGRAM_TOKEN.strip()
        cid = config.TELEGRAM_CHAT_ID.strip()
        if not tok or not cid: return
        requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": cid, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        log.error(f"Telegram: {e}")


def _notif_senal(r, balance, ejecutado):
    lado  = "🟢 LONG" if r.get("lado") == "LONG" else "🔴 SHORT"
    ex    = "✅ *Ejecutado*" if ejecutado else "⚠️ *No ejecutado*"
    prior = getattr(config, "PARES_PRIORITARIOS", [])
    star  = "⭐ " if r.get("par") in prior else ""
    tp1   = r.get("tp1", 0)
    tp1_txt = f"🔶 TP1     : `{tp1:.6f}` (50%)\n" if tp1 > 0 else ""

    # ── FIX: precio nunca debe ser 0 en la notificación ──
    precio = r.get("precio", 0)
    if precio <= 0:
        precio = exchange.get_precio(r.get("par", ""))

    tp     = r.get("tp", 0)
    sl     = r.get("sl", 0)
    score  = r.get("score", 0)
    rsi    = r.get("rsi", 0)
    par    = r.get("par", "?")

    # ── FIX: R:R calculado aquí, no depende del campo r["rr"] ──
    if precio > 0 and tp > 0 and sl > 0:
        if r.get("lado") == "LONG":
            rr = (tp - precio) / (precio - sl) if precio > sl else 0
        else:
            rr = (precio - tp) / (sl - precio) if sl > precio else 0
    else:
        rr = r.get("rr", 0)

    _notif(
        f"{lado} — {star}`{par}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Entrada : `{precio:.6f}`\n"
        f"{tp1_txt}"
        f"✅ TP2     : `{tp:.6f}`\n"
        f"🛑 SL      : `{sl:.6f}`\n"
        f"📊 R:R     : `{rr:.2f}x`\n"
        f"🏅 Score   : `{score}/100`\n"
        f"📉 RSI     : `{rsi:.1f}`\n"
        f"💰 Balance : `${balance:.2f} USDT`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{ex}"
    )


def _notif_cierre(par, lado, entrada, salida, pnl, razon=""):
    ico   = "✅" if pnl >= 0 else "❌"
    r_txt = f" ({razon})" if razon else ""
    _notif(
        f"{ico} *CIERRE {lado}{r_txt}* — `{par}`\n"
        f"`{entrada:.6f}` → `{salida:.6f}`\n"
        f"PnL: `${pnl:+.2f} USDT`"
    )


# ═══════════════════════════════════════════════════════
# TRAILING STOP
# ═══════════════════════════════════════════════════════

def actualizar_trailing(par, pos, precio):
    if not getattr(config, "TRAILING_ACTIVO", True):
        return
    lado      = pos["lado"]
    atr       = pos.get("atr", 0)
    activar   = getattr(config, "TRAILING_ACTIVAR",   1.5)
    distancia = getattr(config, "TRAILING_DISTANCIA", 1.0)
    entrada   = pos["entrada"]
    if atr <= 0:
        return
    if lado == "LONG":
        if precio - entrada < atr * activar:
            return
        nuevo = precio - atr * distancia
        if nuevo > pos.get("sl_trailing", pos["sl"]):
            pos["sl_trailing"] = nuevo
            log.debug(f"[TRAIL] {par} LONG → SL={nuevo:.6f}")
    else:
        if entrada - precio < atr * activar:
            return
        nuevo = precio + atr * distancia
        if nuevo < pos.get("sl_trailing", pos["sl"]):
            pos["sl_trailing"] = nuevo
            log.debug(f"[TRAIL] {par} SHORT → SL={nuevo:.6f}")


# ═══════════════════════════════════════════════════════
# PARTIAL TP
# ═══════════════════════════════════════════════════════

def gestionar_partial_tp(par, pos, precio):
    if not getattr(config, "PARTIAL_TP_ACTIVO", True):
        return
    if pos.get("tp1_hit"):
        return

    tp1  = pos.get("tp1", 0)
    lado = pos["lado"]
    if tp1 <= 0:
        return

    alcanzado = (precio >= tp1) if lado == "LONG" else (precio <= tp1)
    if not alcanzado:
        return

    qty     = pos["qty"]
    qty_tp1 = round(qty * 0.5, 8)

    if not config.MODO_DEMO:
        res         = exchange.cerrar_posicion(par, qty_tp1, lado)
        salida_real = (res or {}).get("precio_salida", precio) or precio
    else:
        salida_real = precio

    entrada = pos["entrada"]
    pnl_p   = qty_tp1 * ((salida_real - entrada) if lado == "LONG"
                          else (entrada - salida_real))
    estado.pnl_hoy += pnl_p

    be = entrada * 1.0005 if lado == "LONG" else entrada * 0.9995
    pos["sl"]          = be
    pos["sl_trailing"] = be
    pos["qty"]         = round(qty - qty_tp1, 8)
    pos["tp1_hit"]     = True

    log.info(f"[TP1] {par} 50% @ {salida_real:.6f} PnL_p={pnl_p:+.4f} SL→BE={be:.6f}")
    _notif(
        f"🔶 *TP1 ALCANZADO* — `{par}` {lado}\n"
        f"50% cerrado @ `{salida_real:.6f}`\n"
        f"PnL parcial: `${pnl_p:+.2f}` USDT\n"
        f"🔄 SL → breakeven `{be:.6f}`\n"
        f"▶️ Resto a TP2: `{pos['tp']:.6f}`"
    )


# ═══════════════════════════════════════════════════════
# TIME-BASED EXIT
# ═══════════════════════════════════════════════════════

def check_time_exit(par, pos):
    horas_max = getattr(config, "TIME_EXIT_HORAS", 8)
    ts_str    = pos.get("ts", "")
    if not ts_str:
        return False
    try:
        ts    = datetime.fromisoformat(ts_str)
        ahora = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        horas = (ahora - ts).total_seconds() / 3600
        if horas >= horas_max:
            log.warning(f"[TIME_EXIT] {par} lleva {horas:.1f}h — cerrando")
            return True
    except Exception:
        pass
    return False


# ═══════════════════════════════════════════════════════
# SINCRONIZACIÓN CON BINGX
# ═══════════════════════════════════════════════════════

def sincronizar_posiciones():
    if not estado.posiciones or config.MODO_DEMO:
        return
    try:
        pos_reales = exchange.get_posiciones_abiertas()
        reales     = set()
        for p in pos_reales:
            s = p.get("symbol", "")
            reales.add(s)
            reales.add(s.replace("-", ""))
            if "USDT" in s and "-" not in s:
                reales.add(s.replace("USDT", "-USDT"))

        cerradas = [
            par for par in estado.posiciones
            if par not in reales and par.replace("-", "") not in reales
        ]

        for par in cerradas:
            pos     = estado.posiciones[par]
            lado    = pos["lado"]
            entrada = pos["entrada"]
            qty     = pos["qty"]
            sl_ef   = pos.get("sl_trailing", pos["sl"])
            tp      = pos["tp"]
            precio  = exchange.get_precio(par)

            if lado == "LONG":
                salida, razon = (tp, "TP") if precio >= tp * 0.98 else (sl_ef, "SL")
                pnl = qty * (salida - entrada)
            else:
                salida, razon = (tp, "TP") if precio <= tp * 1.02 else (sl_ef, "SL")
                pnl = qty * (entrada - salida)

            estado.registrar_cierre(pnl)
            memoria.registrar_resultado(par, pnl, lado)
            del estado.posiciones[par]
            log.info(f"[SYNC] {par} cerrado por BingX ({razon}) PnL≈{pnl:+.4f}")
            _notif_cierre(par, lado, entrada, salida, pnl, f"BingX-{razon}")

        if cerradas:
            log.info(f"[SYNC] {len(cerradas)} posición(es) sincronizadas")
    except Exception as e:
        log.error(f"[SYNC] {e}")


# ═══════════════════════════════════════════════════════
# GESTIONAR POSICIONES ABIERTAS
# ═══════════════════════════════════════════════════════

def gestionar_posiciones(balance):
    for par, pos in list(estado.posiciones.items()):
        try:
            precio = exchange.get_precio(par)
            if precio <= 0:
                continue
            lado = pos["lado"]
            qty  = pos["qty"]

            # Posiciones recuperadas sin SL/TP → no gestionar hasta que se actualicen
            if pos.get("recuperada") and pos.get("sl", 0) <= 0:
                log.info(f"[RECUPERADA] {par} sin SL/TP — esperando datos del analizar")
                continue

            # 1. Partial TP
            gestionar_partial_tp(par, pos, precio)

            # 2. Time exit
            if check_time_exit(par, pos):
                res         = exchange.cerrar_posicion(par, qty, lado)
                salida_real = (res or {}).get("precio_salida", precio) or precio
                pnl = qty * ((salida_real - pos["entrada"]) if lado == "LONG"
                             else (pos["entrada"] - salida_real))
                estado.registrar_cierre(pnl)
                memoria.registrar_resultado(par, pnl, lado)
                del estado.posiciones[par]
                _notif_cierre(par, lado, pos["entrada"], salida_real, pnl, "TIME")
                continue

            # 3. Trailing
            actualizar_trailing(par, pos, precio)
            sl_ef = pos.get("sl_trailing", pos["sl"])
            tp    = pos["tp"]

            sl_hit = (precio <= sl_ef) if lado == "LONG" else (precio >= sl_ef)
            tp_hit = (precio >= tp)    if lado == "LONG" else (precio <= tp)

            razon = salida = None
            if sl_hit:
                razon  = "TRAIL" if pos.get("tp1_hit") else "SL"
                salida = sl_ef
            elif tp_hit:
                razon, salida = "TP2", tp

            if razon:
                res         = exchange.cerrar_posicion(par, qty, lado)
                salida_real = (res or {}).get("precio_salida", salida) or salida
                pnl = qty * ((salida_real - pos["entrada"]) if lado == "LONG"
                             else (pos["entrada"] - salida_real))
                estado.registrar_cierre(pnl)
                memoria.registrar_resultado(par, pnl, lado)
                del estado.posiciones[par]
                log.info(f"CIERRE {lado} {par} @ {salida_real:.6f} PnL={pnl:+.4f} ({razon})")
                _notif_cierre(par, lado, pos["entrada"], salida_real, pnl, razon)

        except Exception as e:
            log.error(f"gestionar {par}: {e}")
        time.sleep(0.3)


# ═══════════════════════════════════════════════════════
# EJECUTAR SEÑAL
# ═══════════════════════════════════════════════════════

def ejecutar_senal(r, balance):
    par    = r.get("par")
    lado   = r.get("lado")
    precio = r.get("precio", 0)

    if not par or not lado:
        log.warning(f"Señal incompleta (sin par/lado): {r}")
        return False

    # ── FIX: precio real antes de todo ──
    if precio <= 0:
        precio = exchange.get_precio(par)
    if precio <= 0:
        log.warning(f"[{par}] precio no disponible — señal descartada")
        return False
    r["precio"] = precio   # guardar precio real en la señal para la notificación

    # ── FIX CRÍTICO: verificar par en estado.posiciones (cargado desde BingX) ──
    if par in estado.posiciones:
        pos_existente = estado.posiciones[par]
        log.warning(f"[BLOQUEO] {par} ya tiene posición {pos_existente['lado']} abierta "
                    f"— señal {lado} descartada")
        return False

    if memoria.esta_bloqueado(par):
        log.info(f"[MEMORIA] {par} bloqueado"); return False
    if len(estado.posiciones) >= config.MAX_POSICIONES:
        return False
    if balance < 8.0 and not config.MODO_DEMO:
        log.warning(f"Balance insuficiente: ${balance:.2f}"); return False

    qty = exchange.calcular_cantidad(par, balance, precio)
    if qty <= 0:
        return False

    res = exchange.abrir_long(par, qty, precio, r["sl"], r["tp"])  if lado == "LONG" \
          else exchange.abrir_short(par, qty, precio, r["sl"], r["tp"])

    if not res or "error" in res:
        err = (res or {}).get("error", "vacío")
        log.error(f"Orden fallida {lado} {par}: {err}")
        memoria.registrar_error_api(par, 109400)
        _notif(f"🚨 *Orden fallida {lado} `{par}`*\n❌ `{err}`")
        return False

    entrada_real = float(res.get("fill_price", 0) or 0)
    if entrada_real <= 0:
        entrada_real = exchange.get_precio(par)
    if entrada_real <= 0:
        entrada_real = precio

    atr = r.get("atr", 0)
    if atr > 0:
        sl_r  = (entrada_real - atr * config.SL_ATR_MULT) if lado == "LONG" \
                else (entrada_real + atr * config.SL_ATR_MULT)
        tp_r  = (entrada_real + atr * config.TP_ATR_MULT) if lado == "LONG" \
                else (entrada_real - atr * config.TP_ATR_MULT)
        tp1_r = (entrada_real + atr * getattr(config, "PARTIAL_TP1_MULT", 1.5)) if lado == "LONG" \
                else (entrada_real - atr * getattr(config, "PARTIAL_TP1_MULT", 1.5))
    else:
        ratio = entrada_real / precio if precio > 0 else 1.0
        sl_r  = r["sl"]  * ratio
        tp_r  = r["tp"]  * ratio
        tp1_r = r.get("tp1", tp_r) * ratio

    qty_real = float(res.get("executedQty", qty) or qty)

    estado.posiciones[par] = {
        "lado":        lado,
        "entrada":     entrada_real,
        "qty":         qty_real,
        "sl":          sl_r,
        "tp":          tp_r,
        "tp1":         tp1_r,
        "atr":         atr,
        "sl_trailing": sl_r,
        "tp1_hit":     False,
        "ts":          datetime.now(timezone.utc).isoformat(),
        "recuperada":  False,
    }

    slip = abs(entrada_real - precio) / precio * 100 if precio > 0 else 0
    slip_tag = f" ⚠️SLIP:{slip:.1f}%" if slip > 0.5 else ""
    log.info(f"✅ {lado} {par} fill:{entrada_real:.6f}{slip_tag} "
             f"SL:{sl_r:.6f} TP1:{tp1_r:.6f} TP2:{tp_r:.6f} score:{r['score']}")
    return True


# ═══════════════════════════════════════════════════════
# REPORTE
# ═══════════════════════════════════════════════════════

def enviar_reporte(balance):
    prior   = set(getattr(config, "PARES_PRIORITARIOS", []))
    pos_txt = ""
    for par, pos in estado.posiciones.items():
        p_actual = exchange.get_precio(par)
        pnl_est  = pos["qty"] * (
            (p_actual - pos["entrada"]) if pos["lado"] == "LONG"
            else (pos["entrada"] - p_actual)
        )
        sl_ef  = pos.get("sl_trailing", pos["sl"])
        fase   = "🔶→TP2" if pos.get("tp1_hit") else "▶️TP1"
        ico    = "🟢" if pos["lado"] == "LONG" else "🔴"
        star   = "⭐" if par in prior else ""
        rec    = "♻️" if pos.get("recuperada") else ""
        ts_str = pos.get("ts", "")
        horas  = ""
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
                h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
                horas = f" {h:.1f}h"
            except Exception:
                pass
        pos_txt += (f"  {ico}{star}{rec} `{par}` e:`{pos['entrada']:.4f}` "
                    f"est:${pnl_est:+.2f} {fase}{horas}\n")

    if not pos_txt:
        pos_txt = "  _(sin posiciones)_\n"

    w, l = estado.wins, estado.losses
    wr   = f"{w/(w+l)*100:.1f}%" if (w+l) > 0 else "N/A"

    _notif(
        f"📊 *Reporte — {config.VERSION}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance  : `${balance:.2f} USDT`\n"
        f"📈 Sesión   : `{w}W/{l}L` WR:`{wr}`\n"
        f"PnL hoy     : `${estado.pnl_hoy:+.2f}` USDT\n"
        f"🏅 Score≥`{config.SCORE_MIN}` | Lev:`{config.LEVERAGE}x`\n"
        f"🔶 Partial TP | ⏱ Time exit `{getattr(config,'TIME_EXIT_HORAS',8)}h`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Posiciones:\n{pos_txt}"
    )


# ═══════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════

def main():
    log.info("=" * 60)
    log.info(f"{config.VERSION}")
    log.info(f"SCORE≥{config.SCORE_MIN} | LEV:{config.LEVERAGE}x | MAX_POS:{config.MAX_POSICIONES}")
    log.info(f"PARTIAL_TP:{getattr(config,'PARTIAL_TP_ACTIVO',True)} | "
             f"TRAILING:{getattr(config,'TRAILING_ACTIVO',True)} | "
             f"TIME_EXIT:{getattr(config,'TIME_EXIT_HORAS',8)}h")
    log.info("=" * 60)

    balance = exchange.get_balance()
    log.info(f"Balance: ${balance:.2f} USDT | DEMO={config.MODO_DEMO}")

    if balance <= 0 and not config.MODO_DEMO:
        log.error("Balance = 0")
        _notif("🚨 *Balance = $0.00*\nVerifica `BINGX_API_KEY` y `BINGX_SECRET_KEY` en Railway.")

    # ══ FIX CRÍTICO: cargar posiciones existentes ANTES del primer ciclo ══
    cargar_posiciones_desde_bingx()

    pares_raw = PARES_FIJOS or [
        "BERA-USDT","PI-USDT","OP-USDT","NEAR-USDT","ARB-USDT",
        "LINK-USDT","GRASS-USDT","MYX-USDT","KAITO-USDT","ONDO-USDT",
        "LTC-USDT","POPCAT-USDT","AVAX-USDT","INJ-USDT",
        "DOT-USDT","SUI-USDT","TIA-USDT","RUNE-USDT",
    ]
    pares = preparar_pares(pares_raw)
    prior = getattr(config, "PARES_PRIORITARIOS", [])
    bloq  = getattr(config, "PARES_BLOQUEADOS",   [])

    _notif(
        f"🤖 *{config.VERSION}* arrancado\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: `${balance:.2f} USDT`\n"
        f"📊 Pares: `{len(pares)}` activos\n"
        f"⭐ Prioritarios: `{len(prior)}` | 🚫 Bloqueados: `{len(bloq)}`\n"
        f"🏅 Score≥`{config.SCORE_MIN}` | Lev:`{config.LEVERAGE}x` | Max:`{config.MAX_POSICIONES}` pos\n"
        f"🔶 Partial TP (50%@TP1 + SL→BE)\n"
        f"🎯 Trailing stop activo\n"
        f"⏱ Time exit: `{getattr(config,'TIME_EXIT_HORAS',8)}h`\n"
        f"🔄 Sync BingX: activo\n"
        f"🧠 Memoria: activa\n"
        f"{'🔇 *DEMO*' if config.MODO_DEMO else '🟢 *LIVE — DINERO REAL*'}"
    )

    ciclo        = 0
    last_reporte = time.time()

    while True:
        try:
            ciclo += 1
            estado.reset_diario()
            balance = exchange.get_balance()

            log.info(
                f"Ciclo {ciclo} | {datetime.now(timezone.utc).strftime('%H:%M UTC')} | "
                f"Bal:${balance:.2f} | Pos:{len(estado.posiciones)} | "
                f"PnL:${estado.pnl_hoy:+.2f}"
            )

            # 1. Sincronizar con BingX
            sincronizar_posiciones()

            # 2. Gestionar posiciones abiertas
            if estado.posiciones:
                gestionar_posiciones(balance)
                balance = exchange.get_balance()

            # 3. Buscar señales nuevas
            if len(estado.posiciones) < config.MAX_POSICIONES:
                log.info(f"Escaneando {len(pares)} pares (score≥{config.SCORE_MIN})...")
                senales = analizar.analizar_todos(pares)

                if senales:
                    log.info(f"✓ {len(senales)} señal(es):")
                    for s in senales:
                        star  = "⭐" if s.get("par", "") in prior else " "
                        rr    = s.get("rr",    0)
                        rsi   = s.get("rsi",   0)
                        score = s.get("score", 0)
                        lado_ = s.get("lado",  "?")
                        par_  = s.get("par",   "?")
                        log.info(f"  {star}{lado_:5s} {par_:20s} "
                                 f"score={score} RSI={rsi:.1f} R:R={rr:.2f}")
                else:
                    log.info("Sin señales este ciclo")

                for s in senales:
                    if len(estado.posiciones) >= config.MAX_POSICIONES:
                        break
                    if s["par"] in estado.posiciones:
                        continue
                    s["score"] = memoria.ajustar_score(s["par"], s["score"])
                    if s["score"] < config.SCORE_MIN:
                        log.info(f"[MEMORIA] {s['par']} score={s['score']} < {config.SCORE_MIN}")
                        continue
                    ejecutado = ejecutar_senal(s, balance)
                    _notif_senal(s, balance, ejecutado)
                    if ejecutado:
                        balance = exchange.get_balance()
                        time.sleep(2)

            # 4. Reporte horario
            if time.time() - last_reporte >= 3600:
                enviar_reporte(balance)
                _notif(memoria.resumen())
                last_reporte = time.time()

        except KeyboardInterrupt:
            log.info("Detenido manualmente")
            _notif("🛑 *Bot detenido manualmente.*")
            break
        except Exception as e:
            log.error(f"ERROR CICLO {ciclo}: {e}")
            log.error(traceback.format_exc())
            try: _notif(f"🚨 *Error ciclo {ciclo}*\n`{str(e)[:200]}`")
            except Exception: pass

        log.info(f"Próximo ciclo en {config.LOOP_SECONDS}s — "
                 f"{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
        log.info("-" * 55)
        time.sleep(config.LOOP_SECONDS)


if __name__ == "__main__":
    main()
