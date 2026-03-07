"""
main.py — BingX RSI+BB Bot v7.0
MEJORAS vs v6.x:
  - Partial TP: 50% en TP1 (1.5×ATR), SL → breakeven, resto a TP2
  - Time-based exit: si posición > TIME_EXIT_HORAS sin resolver → cerrar
  - Notificación de divergencia y StochRSI en Telegram
  - EMA200 desactivado (backtest confirma)
  - PARES_BLOQUEADOS / PARES_PRIORITARIOS por backtest
  - PnL correcto (sin doble leverage)
  - Margen dinámico 8% del balance
"""

import sys, os, time, traceback
from datetime import datetime, date, timezone, timedelta

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    stream=sys.stdout, force=True,
)
log = logging.getLogger("main")
log.info("=== ARRANQUE BOT BINGX RSI+BB v7.0 ===")

try:
    import config, exchange, analizar, notifier, memoria, learner
except Exception as e:
    log.error(f"ERROR importando módulos: {e}")
    log.error(traceback.format_exc())
    sys.exit(1)

try:
    from config_pares import PARES as PARES_FIJOS
except Exception:
    PARES_FIJOS = []

log.info(f"Módulos OK | {config.VERSION}")


# ═══════════════════════════════════════════════════════
# ESTADO
# ═══════════════════════════════════════════════════════

class Estado:
    def __init__(self):
        self.posiciones    = {}   # par → dict
        self.pnl_hoy       = 0.0
        self.perdidas_cons = 0
        self.cb_activo     = False
        self.dia_actual    = str(date.today())
        self.wins = self.losses = 0

    def reset_diario(self):
        hoy = str(date.today())
        if hoy != self.dia_actual:
            self.dia_actual    = hoy
            self.pnl_hoy       = 0.0
            self.perdidas_cons = 0
            self.cb_activo     = False
            log.info(f"Reset diario — nuevo día: {hoy}")

    def check_circuit_breaker(self, balance):
        if self.cb_activo:
            return True
        if self.pnl_hoy <= -(balance * config.CB_MAX_DAILY_LOSS_PCT):
            log.warning(f"CB activado — pérdida diaria: ${self.pnl_hoy:.2f}")
            self.cb_activo = True
            return True
        if self.perdidas_cons >= config.CB_MAX_CONSECUTIVE_LOSS:
            log.warning(f"CB activado — {self.perdidas_cons} pérdidas seguidas")
            self.cb_activo = True
            return True
        return False

    def registrar_cierre(self, pnl):
        self.pnl_hoy += pnl
        if pnl > 0:
            self.wins += 1
            self.perdidas_cons = 0
        else:
            self.losses += 1
            self.perdidas_cons += 1

estado = Estado()


# ═══════════════════════════════════════════════════════
# PARES — PRIORIDAD + BLOQUEOS
# ═══════════════════════════════════════════════════════

def preparar_lista_pares(pares_raw: list) -> list:
    bloqueados   = set(getattr(config, "PARES_BLOQUEADOS",   []))
    prioritarios = getattr(config, "PARES_PRIORITARIOS", [])
    pares_limpios = [p for p in pares_raw if p not in bloqueados]
    pares_top  = [p for p in prioritarios if p in set(pares_limpios)]
    pares_rest = [p for p in pares_limpios if p not in set(pares_top)]
    resultado  = pares_top + pares_rest
    log.info(f"Pares: {len(pares_raw)} → {len(bloqueados)} bloqueados → "
             f"{len(resultado)} activos ({len(pares_top)} prioritarios)")
    return resultado


# ═══════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════

def _notif(msg):
    try:
        import requests
        tok = config.TELEGRAM_TOKEN.strip()
        cid = config.TELEGRAM_CHAT_ID.strip()
        if not tok or not cid:
            return
        requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": cid, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        log.error(f"Telegram: {e}")


def _notif_senal(r, balance, ejecutado):
    lado   = "🟢 LONG" if r["lado"] == "LONG" else "🔴 SHORT"
    ex_txt = "✅ *Ejecutado*" if ejecutado else "⚠️ *No ejecutado*"
    adx    = r.get("adx", 0)
    regimen = "📊 Lateral" if adx < 20 else ("⚠️ Transición" if adx < 25 else "🌊 Tendencia")
    star   = "⭐ " if r["par"] in getattr(config, "PARES_PRIORITARIOS", []) else ""

    # Badges de calidad
    badges = []
    if r.get("divergencia") not in ("none", None, ""):
        badges.append(f"🔀 Divergencia {r['divergencia']}")
    stoch_k = r.get("stoch_k", 50)
    if stoch_k < 20:
        badges.append(f"⚡ StochRSI OS ({stoch_k:.0f})")
    elif stoch_k > 80:
        badges.append(f"⚡ StochRSI OB ({stoch_k:.0f})")
    if r.get("squeeze"):
        badges.append("🗜 BB Squeeze")
    badge_txt = "\n".join(badges) + "\n" if badges else ""

    _notif(
        f"{lado} — {star}`{r['par']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{badge_txt}"
        f"🎯 Entrada : `{r['precio']:.6f}`\n"
        f"🔶 TP1     : `{r['tp1']:.6f}` (50%)\n"
        f"✅ TP2     : `{r['tp']:.6f}` (50%)\n"
        f"🛑 SL      : `{r['sl']:.6f}`\n"
        f"📐 R:R     : `{r['rr']:.2f}x`\n"
        f"🏅 Score   : `{r['score']}/100`\n"
        f"📉 RSI     : `{r['rsi']:.1f}` | StochRSI: `{stoch_k:.0f}`\n"
        f"📊 ADX     : `{adx:.1f}` {regimen}\n"
        f"📦 Vol     : `{r.get('vol_rel',1):.1f}x` media\n"
        f"💰 Balance : `${balance:.2f} USDT`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{ex_txt}"
    )


def _notif_cierre(par, lado, entrada, salida, pnl, razon=""):
    ico   = "✅" if pnl >= 0 else "❌"
    r_txt = f" ({razon})" if razon else ""
    _notif(
        f"{ico} *CIERRE {lado}{r_txt}* — `{par}`\n"
        f"Entrada → Salida: `{entrada:.6f}` → `{salida:.6f}`\n"
        f"PnL: `${pnl:+.2f} USDT`"
    )


# ═══════════════════════════════════════════════════════
# TRAILING STOP
# ═══════════════════════════════════════════════════════

def actualizar_trailing_stop(par, pos, precio):
    if not getattr(config, "TRAILING_ACTIVO", True):
        return
    lado, atr = pos["lado"], pos.get("atr", 0)
    if atr <= 0:
        return
    activar   = getattr(config, "TRAILING_ACTIVAR",   1.5)
    distancia = getattr(config, "TRAILING_DISTANCIA", 1.0)
    entrada   = pos["entrada"]

    if lado == "LONG":
        if precio - entrada < atr * activar:
            return
        nuevo_sl  = precio - atr * distancia
        sl_actual = pos.get("sl_trailing", pos["sl"])
        if nuevo_sl > sl_actual:
            pos["sl_trailing"] = nuevo_sl
    else:
        if entrada - precio < atr * activar:
            return
        nuevo_sl  = precio + atr * distancia
        sl_actual = pos.get("sl_trailing", pos["sl"])
        if nuevo_sl < sl_actual:
            pos["sl_trailing"] = nuevo_sl


# ═══════════════════════════════════════════════════════
# PARTIAL TP — LÓGICA CENTRAL
# ═══════════════════════════════════════════════════════

def gestionar_partial_tp(par, pos, precio):
    """
    Fase 1 (tp1_hit=False):
      → precio llega a TP1: cerrar 50%, mover SL a breakeven
    Fase 2 (tp1_hit=True):
      → trailing hasta TP2
    """
    if pos.get("tp1_hit"):
        return False   # Ya en fase 2, el trailing lo gestiona

    tp1    = pos.get("tp1", 0)
    lado   = pos["lado"]
    if tp1 <= 0:
        return False

    tp1_alcanzado = (precio >= tp1) if lado == "LONG" else (precio <= tp1)
    if not tp1_alcanzado:
        return False

    qty     = pos["qty"]
    qty_tp1 = round(qty * 0.5, 8)

    if not config.MODO_DEMO:
        res = exchange.cerrar_posicion_parcial(par, qty_tp1, lado) if hasattr(exchange, "cerrar_posicion_parcial") \
              else exchange.cerrar_posicion(par, qty_tp1, lado)
        salida_real = (res or {}).get("precio_salida", precio) or precio
    else:
        salida_real = precio

    entrada = pos["entrada"]
    pnl_parcial = qty_tp1 * (
        (salida_real - entrada) if lado == "LONG"
        else (entrada - salida_real)
    )
    estado.pnl_hoy += pnl_parcial

    # Mover SL a breakeven (entrada + un tick)
    pos["sl"]          = entrada * 1.0005 if lado == "LONG" else entrada * 0.9995
    pos["sl_trailing"] = pos["sl"]
    pos["qty"]         = round(qty - qty_tp1, 8)
    pos["tp1_hit"]     = True

    log.info(f"[TP1] {par} {lado} 50% cerrado @ {salida_real:.6f} "
             f"PnL_parcial={pnl_parcial:+.4f} | SL→BE | Resto qty={pos['qty']}")
    _notif(
        f"🔶 *TP1 ALCANZADO* — `{par}` {lado}\n"
        f"50% cerrado @ `{salida_real:.6f}`\n"
        f"PnL parcial: `${pnl_parcial:+.2f}` USDT\n"
        f"🔄 SL movido a breakeven\n"
        f"▶️ Resto corriendo a TP2: `{pos['tp']:.6f}`"
    )
    return True


# ═══════════════════════════════════════════════════════
# TIME-BASED EXIT
# ═══════════════════════════════════════════════════════

def check_time_exit(par, pos, precio):
    """
    Si la posición lleva más de TIME_EXIT_HORAS sin resolver,
    cerrar al precio actual. Libera capital para nuevas oportunidades.
    """
    horas_max = getattr(config, "TIME_EXIT_HORAS", 8)
    ts_str    = pos.get("ts", "")
    if not ts_str:
        return False
    try:
        ts   = datetime.fromisoformat(ts_str)
        ahora = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        horas = (ahora - ts).total_seconds() / 3600
        if horas >= horas_max:
            log.warning(f"[TIME_EXIT] {par} lleva {horas:.1f}h — cerrando")
            return True
    except:
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
        simbolos_reales = set()
        for p in pos_reales:
            s = p.get("symbol", "")
            simbolos_reales.add(s)
            simbolos_reales.add(s.replace("-", ""))
            if "USDT" in s and "-" not in s:
                simbolos_reales.add(s.replace("USDT", "-USDT"))

        cerradas = [
            par for par in estado.posiciones
            if par not in simbolos_reales and par.replace("-", "") not in simbolos_reales
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
            learner.registrar_trade_con_adx(par, pos.get("adx", 0),
                                            "WIN" if pnl > 0 else "LOSS", pnl)
            del estado.posiciones[par]
            log.info(f"[SYNC] {par} cerrado ({razon}) PnL≈{pnl:+.4f}")
            _notif_cierre(par, lado, entrada, salida, pnl, f"BingX-{razon}")

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
            lado  = pos["lado"]
            qty   = pos["qty"]
            tp    = pos["tp"]

            # 1. Partial TP
            gestionar_partial_tp(par, pos, precio)

            # 2. Time-based exit
            if check_time_exit(par, pos, precio):
                res         = exchange.cerrar_posicion(par, qty, lado)
                salida_real = (res or {}).get("precio_salida", precio) or precio
                pnl = qty * ((salida_real - pos["entrada"]) if lado == "LONG"
                             else (pos["entrada"] - salida_real))
                estado.registrar_cierre(pnl)
                memoria.registrar_resultado(par, pnl, lado)
                learner.registrar_trade_con_adx(par, pos.get("adx", 0),
                                                "WIN" if pnl > 0 else "LOSS", pnl)
                del estado.posiciones[par]
                log.info(f"[TIME_EXIT] {par} cerrado PnL={pnl:+.4f}")
                _notif_cierre(par, lado, pos["entrada"], salida_real, pnl, "TIME")
                continue

            # 3. Trailing stop
            actualizar_trailing_stop(par, pos, precio)
            sl_ef = pos.get("sl_trailing", pos["sl"])

            # 4. SL / TP2
            sl_hit = (precio <= sl_ef) if lado == "LONG" else (precio >= sl_ef)
            tp_hit = (precio >= tp)    if lado == "LONG" else (precio <= tp)
            razon  = None
            salida = precio

            if sl_hit:
                razon  = "TRAIL" if sl_ef != pos["sl"] else "SL"
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
                learner.registrar_trade_con_adx(par, pos.get("adx", 0),
                                                "WIN" if pnl > 0 else "LOSS", pnl)
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
    par    = r["par"]
    lado   = r["lado"]
    precio = r["precio"]

    if par in estado.posiciones:
        return False
    if memoria.esta_bloqueado(par):
        log.info(f"[MEMORIA] {par} bloqueado")
        return False
    if len(estado.posiciones) >= config.MAX_POSICIONES:
        return False
    if balance < 3.0 and not config.MODO_DEMO:
        return False

    qty = exchange.calcular_cantidad(par, balance, precio)
    if qty <= 0:
        return False

    res = exchange.abrir_long(par, qty, precio, r["sl"], r["tp"]) if lado == "LONG" \
          else exchange.abrir_short(par, qty, precio, r["sl"], r["tp"])

    if not res or "error" in res:
        err = (res or {}).get("error", "vacío")
        log.error(f"Orden fallida {lado} {par}: {err}")
        memoria.registrar_error_api(par, 109400)
        _notif(f"🚨 *Orden fallida — {lado} `{par}`*\n❌ `{err}`")
        return False

    # ════════════════════════════════════════════════════════
    # FIX CRÍTICO: usar fill_price (precio real de ejecución)
    # "precio" es del análisis — puede ser MUY diferente al fill real
    # ════════════════════════════════════════════════════════
    entrada_real = float(res.get("fill_price", 0) or 0)
    if entrada_real <= 0:
        entrada_real = exchange.get_precio(par)
    if entrada_real <= 0:
        entrada_real = precio  # último recurso

    slippage_pct = abs(entrada_real - precio) / precio * 100 if precio > 0 else 0

    # Recalcular SL/TP basados en la entrada REAL
    atr = r.get("atr", 0)
    if atr > 0:
        if lado == "LONG":
            sl_real  = entrada_real - atr * config.SL_ATR_MULT
            tp_real  = entrada_real + atr * config.TP_ATR_MULT
            tp1_real = entrada_real + atr * getattr(config, "PARTIAL_TP1_MULT", 1.5)
        else:
            sl_real  = entrada_real + atr * config.SL_ATR_MULT
            tp_real  = entrada_real - atr * config.TP_ATR_MULT
            tp1_real = entrada_real - atr * getattr(config, "PARTIAL_TP1_MULT", 1.5)
    else:
        # Ajustar proporcionalmente si no hay ATR
        ratio    = entrada_real / precio if precio > 0 else 1.0
        sl_real  = r["sl"]  * ratio
        tp_real  = r["tp"]  * ratio
        tp1_real = r.get("tp1", tp_real) * ratio

    qty_real = float(res.get("executedQty", qty) or qty)

    estado.posiciones[par] = {
        "lado":         lado,
        "entrada":      entrada_real,   # ← PRECIO REAL DE EJECUCIÓN
        "qty":          qty_real,
        "sl":           sl_real,        # ← SL desde entrada real
        "tp":           tp_real,        # ← TP desde entrada real
        "tp1":          tp1_real,
        "atr":          atr,
        "adx":          r.get("adx", 0),
        "sl_trailing":  sl_real,
        "tp1_hit":      False,
        "ts":           datetime.now(timezone.utc).isoformat(),
        "precio_senal": precio,         # solo para diagnóstico
        "divergencia":  r.get("divergencia", "none"),
    }

    div_tag  = f" [DIV:{r.get('divergencia','')}]" if r.get("divergencia") not in ("none", None, "") else ""
    slip_tag = f" ⚠️SLIP:{slippage_pct:.1f}%" if slippage_pct > 0.5 else ""
    log.info(f"✅ {lado} {par} | señal:{precio:.6f}→fill:{entrada_real:.6f}{slip_tag} | "
             f"qty:{qty_real} SL:{sl_real:.6f} TP1:{tp1_real:.6f} TP2:{tp_real:.6f} | "
             f"ADX:{r.get('adx',0):.1f} score:{r['score']}{div_tag}")
    return True


# ═══════════════════════════════════════════════════════
# REPORTE HORARIO
# ═══════════════════════════════════════════════════════

def enviar_reporte(balance):
    pos_txt = ""
    prior   = set(getattr(config, "PARES_PRIORITARIOS", []))
    for par, pos in estado.posiciones.items():
        p_actual = exchange.get_precio(par)
        pnl_est  = pos["qty"] * (
            (p_actual - pos["entrada"]) if pos["lado"] == "LONG"
            else (pos["entrada"] - p_actual)
        )
        sl_ef    = pos.get("sl_trailing", pos["sl"])
        fase     = "🔶TP1✓→TP2" if pos.get("tp1_hit") else "▶️TP1"
        ico      = "🟢" if pos["lado"] == "LONG" else "🔴"
        star     = "⭐" if par in prior else ""
        div_tag  = f" DIV" if pos.get("divergencia") not in ("none", None, "") else ""
        ts_str   = pos.get("ts", "")
        horas    = ""
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
                horas = f" {h:.1f}h"
            except:
                pass
        pos_txt += (f"  {ico}{star} `{par}` e:`{pos['entrada']:.4f}` "
                    f"est:${pnl_est:+.2f} {fase}{div_tag}{horas}\n")

    if not pos_txt:
        pos_txt = "  _(sin posiciones)_\n"

    w, l = estado.wins, estado.losses
    wr   = f"{w/(w+l)*100:.1f}%" if (w+l) > 0 else "N/A"
    le   = learner.get_estado_actual()

    _notif(
        f"📊 *Reporte — {config.VERSION}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance  : `${balance:.2f} USDT`\n"
        f"📈 Sesión   : `{w}W/{l}L` WR:`{wr}`\n"
        f"PnL hoy     : `${estado.pnl_hoy:+.2f}` USDT\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏅 Score≥`{config.SCORE_MIN}` | ADX<`{getattr(config,'ADX_MAX',25)}`\n"
        f"⚡ StochRSI + 🔀 Divergencias activos\n"
        f"🔶 Partial TP | ⏱ Time exit {getattr(config,'TIME_EXIT_HORAS',8)}h\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Posiciones:\n{pos_txt}"
        f"{'⚠️ *CIRCUIT BREAKER ACTIVO*' if estado.cb_activo else ''}"
    )


# ═══════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════

def main():
    log.info("=" * 60)
    log.info(f"{config.VERSION}")
    log.info(f"SCORE≥{config.SCORE_MIN} | ADX<{getattr(config,'ADX_MAX',25)} | "
             f"LEV:{config.LEVERAGE}x | STOCHRSI+DIV+SQUEEZE | "
             f"PARTIAL_TP | TIME_EXIT:{getattr(config,'TIME_EXIT_HORAS',8)}h")
    log.info("=" * 60)

    memoria.inicializar()
    balance = exchange.get_balance()
    log.info(f"Balance: ${balance:.2f} USDT | DEMO={config.MODO_DEMO}")

    if balance <= 0 and not config.MODO_DEMO:
        log.error("Balance = 0")
        _notif("🚨 *Balance = $0.00*\nVerifica las variables en Railway → Variables.")

    pares_raw = PARES_FIJOS or [
        "BERA-USDT","PI-USDT","OP-USDT","NEAR-USDT","ARB-USDT",
        "GRASS-USDT","KAITO-USDT","MYX-USDT","LINK-USDT","ONDO-USDT",
        "POPCAT-USDT","INJ-USDT","AVAX-USDT","LTC-USDT","SOL-USDT",
        "DOT-USDT","SUI-USDT","TIA-USDT","RUNE-USDT"
    ]
    pares    = preparar_lista_pares(pares_raw)
    prior    = getattr(config, "PARES_PRIORITARIOS", [])
    bloq     = getattr(config, "PARES_BLOQUEADOS",   [])
    le       = learner.get_estado_actual()

    _notif(
        f"🤖 *{config.VERSION}* arrancado\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance    : `${balance:.2f} USDT`\n"
        f"📊 Pares      : `{len(pares)}` activos\n"
        f"⭐ Prioritarios: `{len(prior)}`\n"
        f"🚫 Bloqueados  : `{len(bloq)}` (backtest)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏅 Score≥`{config.SCORE_MIN}` | ADX<`{getattr(config,'ADX_MAX',25)}`\n"
        f"⚡ StochRSI timing\n"
        f"🔀 RSI Divergencia (+18 score)\n"
        f"🗜 BB Squeeze detector\n"
        f"🔶 Partial TP (50%@TP1 + SL→BE)\n"
        f"⏱ Time exit: `{getattr(config,'TIME_EXIT_HORAS',8)}h`\n"
        f"📐 Lev:`{config.LEVERAGE}x` | Margen:`{getattr(config, 'RIESGO_MARGEN_PCT', 0.08)*100:.0f}%`\n"
        f"{'🔇 *DEMO*' if config.MODO_DEMO else '🟢 *LIVE — DINERO REAL*'}"
    )

    ciclo        = 0
    last_reporte = time.time()
    last_learner = time.time()

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

            sincronizar_posiciones()

            if estado.check_circuit_breaker(balance):
                _notif(f"🚨 *Circuit Breaker*\nPnL hoy: `${estado.pnl_hoy:+.2f}`\nPausado hasta mañana.")
                time.sleep(3600)
                continue

            if estado.posiciones:
                gestionar_posiciones(balance)
                balance = exchange.get_balance()

            if time.time() - last_learner >= 21600:
                pares_activos = learner.evaluar_y_ajustar(pares)
                pares = preparar_lista_pares(pares_activos)
                last_learner = time.time()

            if len(estado.posiciones) < config.MAX_POSICIONES:
                log.info(f"Escaneando {len(pares)} pares (score≥{config.SCORE_MIN})...")
                senales = analizar.analizar_todos(pares)

                if senales:
                    log.info(f"✓ {len(senales)} señal(es):")
                    for s in senales:
                        star  = "⭐" if s["par"] in prior else " "
                        div_t = f" DIV:{s.get('divergencia','')}" if s.get("divergencia") not in ("none","",None) else ""
                        sq_t  = " 🗜SQUEEZE" if s.get("squeeze") else ""
                        log.info(f"  {star}{s['lado']:5s} {s['par']:20s} "
                                 f"score={s['score']} RSI={s['rsi']:.1f} "
                                 f"ADX={s['adx']:.1f} StochRSI={s.get('stoch_k',50):.0f}{div_t}{sq_t}")
                else:
                    log.info("Sin señales este ciclo")

                for s in senales:
                    if len(estado.posiciones) >= config.MAX_POSICIONES:
                        break
                    if s["par"] in estado.posiciones:
                        continue
                    s["score"] = memoria.ajustar_score(s["par"], s["score"])
                    if s["score"] < config.SCORE_MIN:
                        continue
                    ejecutado = ejecutar_senal(s, balance)
                    _notif_senal(s, balance, ejecutado)
                    if ejecutado:
                        balance = exchange.get_balance()
                        time.sleep(2)

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
            try:
                _notif(f"🚨 *Error ciclo {ciclo}*\n`{str(e)[:200]}`")
            except:
                pass

        log.info(f"Próximo ciclo en {config.LOOP_SECONDS}s")
        log.info("-" * 55)
        time.sleep(config.LOOP_SECONDS)


if __name__ == "__main__":
    main()
