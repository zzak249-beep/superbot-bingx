"""
main.py — BingX RSI+BB Bot v5.0
MEJORAS v5:
  - Sincronización real de posiciones con BingX cada ciclo
  - Filtro de tendencia EMA200 (solo LONG si precio > EMA200, SHORT si < EMA200)
  - Balance mínimo $8 (margen fijo)
  - MAX_POSICIONES respetado estrictamente
  - Memoria integrada (aprende de errores y pérdidas)
"""

import sys
import os
import time
import traceback
from datetime import datetime, date, timezone

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("main")
log.info("=== ARRANQUE BOT BINGX RSI+BB v5.0 ===")

try:
    import config
    import exchange
    import analizar
    import notifier  # noqa
    import memoria
except Exception as e:
    log.error(f"ERROR importando módulos: {e}")
    log.error(traceback.format_exc())
    sys.exit(1)

try:
    from config_pares import PARES as PARES_FIJOS
except Exception:
    PARES_FIJOS = []

log.info(f"Módulos OK | Versión: {config.VERSION}")
log.info(f"SCORE_MIN={config.SCORE_MIN} | LEVERAGE={config.LEVERAGE}x | MODO_DEMO={config.MODO_DEMO}")


# ═══════════════════════════════════════════════════════
# ESTADO
# ═══════════════════════════════════════════════════════

class Estado:
    def __init__(self):
        self.posiciones      = {}
        self.operaciones_hoy = []
        self.pnl_hoy         = 0.0
        self.perdidas_cons   = 0
        self.cb_activo       = False
        self.dia_actual      = str(date.today())
        self.wins            = 0
        self.losses          = 0

    def reset_diario(self):
        hoy = str(date.today())
        if hoy != self.dia_actual:
            self.dia_actual      = hoy
            self.pnl_hoy         = 0.0
            self.perdidas_cons   = 0
            self.cb_activo       = False
            self.operaciones_hoy = []
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
# NOTIFIER
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
    tend   = r.get("tendencia", "")
    t_txt  = f"\n📈 EMA200: `{tend}`" if tend else ""
    _notif(
        f"{lado} — `{r['par']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Entrada : `{r['precio']:.6f}`\n"
        f"🛑 SL      : `{r['sl']:.6f}`\n"
        f"✅ TP      : `{r['tp']:.6f}`\n"
        f"📊 R:R     : `{r['rr']:.2f}x`\n"
        f"🏅 Score   : `{r['score']}/100`\n"
        f"📉 RSI     : `{r['rsi']:.1f}`"
        f"{t_txt}\n"
        f"💰 Balance : `${balance:.2f} USDT`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{ex_txt}"
    )


def _notif_cierre(par, lado, entrada, salida, pnl, razon=""):
    ico = "✅" if pnl >= 0 else "❌"
    r_txt = f" ({razon})" if razon else ""
    _notif(
        f"{ico} *CIERRE {lado}{r_txt}* — `{par}`\n"
        f"Entrada → Salida: `{entrada:.6f}` → `{salida:.6f}`\n"
        f"PnL estimado: `${pnl:+.2f} USDT`"
    )


# ═══════════════════════════════════════════════════════
# MEJORA 1 — SINCRONIZACIÓN CON BINGX
# Detecta posiciones cerradas por SL/TP automático de BingX
# ═══════════════════════════════════════════════════════

def sincronizar_posiciones():
    if not estado.posiciones or config.MODO_DEMO:
        return
    try:
        pos_reales = exchange.get_posiciones_abiertas()
        # Normalizar símbolos (BTC-USDT y BTCUSDT son lo mismo)
        simbolos_reales = set()
        for p in pos_reales:
            s = p.get("symbol", "")
            simbolos_reales.add(s)
            simbolos_reales.add(s.replace("-", ""))
            if "USDT" in s and "-" not in s:
                simbolos_reales.add(s.replace("USDT", "-USDT"))

        cerradas = []
        for par in list(estado.posiciones.keys()):
            par_sin_guion = par.replace("-", "")
            if par not in simbolos_reales and par_sin_guion not in simbolos_reales:
                cerradas.append(par)

        for par in cerradas:
            pos     = estado.posiciones[par]
            lado    = pos["lado"]
            entrada = pos["entrada"]
            qty     = pos["qty"]
            sl      = pos["sl"]
            tp      = pos["tp"]
            precio_actual = exchange.get_precio(par)

            # Estimar si fue SL o TP según precio actual
            if lado == "LONG":
                if precio_actual >= tp * 0.98:
                    salida, razon = tp, "TP"
                    pnl = qty * (tp - entrada)
                else:
                    salida, razon = sl, "SL"
                    pnl = qty * (sl - entrada)
            else:
                if precio_actual <= tp * 1.02:
                    salida, razon = tp, "TP"
                    pnl = qty * (entrada - tp)
                else:
                    salida, razon = sl, "SL"
                    pnl = qty * (entrada - sl)

            estado.registrar_cierre(pnl)
            memoria.registrar_resultado(par, pnl, lado)
            del estado.posiciones[par]

            log.info(f"[SYNC] {par} cerrado por BingX ({razon}) PnL≈{pnl:+.4f}")
            _notif_cierre(par, lado, entrada, salida, pnl, f"BingX-{razon}")

        if cerradas:
            log.info(f"[SYNC] {len(cerradas)} posición(es) detectadas como cerradas por BingX")

    except Exception as e:
        log.error(f"[SYNC] Error: {e}")


# ═══════════════════════════════════════════════════════
# MEJORA 2 — FILTRO EMA200
# Solo operar a favor de la tendencia en 1h
# ═══════════════════════════════════════════════════════

_ema_cache = {}
EMA_TTL    = 300  # 5 minutos


def _ema(closes, periodo):
    if len(closes) < periodo:
        return 0.0
    k   = 2.0 / (periodo + 1)
    ema = sum(closes[:periodo]) / periodo
    for c in closes[periodo:]:
        ema = c * k + ema * (1 - k)
    return ema


def get_ema200(par):
    ahora = time.time()
    if par in _ema_cache:
        ts, val = _ema_cache[par]
        if ahora - ts < EMA_TTL:
            return val
    try:
        klines = exchange.get_klines(par, "1h", 220)
        datos  = exchange.parsear_klines(klines)
        closes = datos.get("closes", [])
        val    = _ema(closes, 200) if len(closes) >= 200 else 0.0
        _ema_cache[par] = (ahora, val)
        return val
    except Exception:
        return 0.0


def filtro_tendencia(par, lado):
    """Retorna (pasa: bool, descripcion: str)"""
    ema200 = get_ema200(par)
    if ema200 <= 0:
        return True, "N/D"
    precio = exchange.get_precio(par)
    if precio <= 0:
        return True, "N/D"
    pct = ((precio - ema200) / ema200) * 100
    if lado == "LONG":
        pasa = precio > ema200
        desc = f"{'OK' if pasa else 'BLOQ'} p={precio:.5f} {'>' if pasa else '<'} EMA200={ema200:.5f} ({pct:+.2f}%)"
    else:
        pasa = precio < ema200
        desc = f"{'OK' if pasa else 'BLOQ'} p={precio:.5f} {'<' if pasa else '>'} EMA200={ema200:.5f} ({pct:+.2f}%)"
    return pasa, desc


# ═══════════════════════════════════════════════════════
# GESTIÓN DE POSICIONES
# ═══════════════════════════════════════════════════════

def gestionar_posiciones(balance):
    for par, pos in list(estado.posiciones.items()):
        try:
            precio = exchange.get_precio(par)
            if precio <= 0:
                continue
            lado    = pos["lado"]
            entrada = pos["entrada"]
            sl      = pos["sl"]
            tp      = pos["tp"]
            qty     = pos["qty"]

            if lado == "LONG":
                sl_hit = precio <= sl
                tp_hit = precio >= tp
            else:
                sl_hit = precio >= sl
                tp_hit = precio <= tp

            razon = None
            salida = precio
            if sl_hit:
                razon, salida = "SL", sl
            elif tp_hit:
                razon, salida = "TP", tp

            if razon:
                res         = exchange.cerrar_posicion(par, qty, lado)
                salida_real = res.get("precio_salida", salida) or salida
                pnl = qty * (salida_real - entrada if lado == "LONG" else entrada - salida_real)

                estado.registrar_cierre(pnl)
                memoria.registrar_resultado(par, pnl, lado)
                del estado.posiciones[par]

                log.info(f"CIERRE {lado} {par} @ {salida_real:.6f} PnL={pnl:+.4f} ({razon})")
                _notif_cierre(par, lado, entrada, salida_real, pnl, razon)

        except Exception as e:
            log.error(f"gestionar {par}: {e}")
        time.sleep(0.5)


# ═══════════════════════════════════════════════════════
# EJECUTAR SEÑAL
# ═══════════════════════════════════════════════════════

def ejecutar_senal(r, balance):
    par   = r["par"]
    lado  = r["lado"]
    precio = r["precio"]
    sl    = r["sl"]
    tp    = r["tp"]

    if par in estado.posiciones:
        return False

    if memoria.esta_bloqueado(par):
        log.info(f"[MEMORIA] {par} bloqueado")
        return False

    if len(estado.posiciones) >= config.MAX_POSICIONES:
        log.info(f"MAX_POSICIONES ({config.MAX_POSICIONES}) alcanzado")
        return False

    if balance < 8.0 and not config.MODO_DEMO:
        log.warning(f"Balance insuficiente: ${balance:.2f} < $8.00")
        return False

    # ── Filtro EMA200 ──
    pasa, desc = filtro_tendencia(par, lado)
    r["tendencia"] = desc
    log.info(f"[EMA200] {par} {lado}: {desc}")
    if not pasa:
        return False

    qty = exchange.calcular_cantidad(par, balance, precio)
    if qty <= 0:
        log.warning(f"qty=0 para {par}")
        return False

    res = exchange.abrir_long(par, qty, precio, sl, tp) if lado == "LONG" \
          else exchange.abrir_short(par, qty, precio, sl, tp)

    if not res or "error" in res:
        err = res.get("error", "respuesta vacía") if res else "respuesta vacía"
        log.error(f"Orden fallida {lado} {par}: {err}")
        memoria.registrar_error_api(par, 109400)
        _notif(
            f"🚨 *Orden fallida — {lado} `{par}`*\n"
            f"❌ `{err}`\n"
            f"qty:`{qty}` precio:`{precio:.6f}`\n"
            f"💡 _Verifica permisos API (Trade) y modo posición en BingX_"
        )
        return False

    estado.posiciones[par] = {
        "lado": lado, "entrada": precio, "qty": qty,
        "sl": sl, "tp": tp, "ts": datetime.now(timezone.utc).isoformat(),
    }
    log.info(f"✅ {lado} {par} qty:{qty} e:{precio:.6f} SL:{sl:.6f} TP:{tp:.6f} score:{r['score']}")
    return True


# ═══════════════════════════════════════════════════════
# REPORTE
# ═══════════════════════════════════════════════════════

def enviar_reporte(balance):
    pos_txt = ""
    for par, pos in estado.posiciones.items():
        p_actual = exchange.get_precio(par)
        pnl_est  = pos["qty"] * (
            (p_actual - pos["entrada"]) if pos["lado"] == "LONG"
            else (pos["entrada"] - p_actual)
        )
        ico = "🟢" if pos["lado"] == "LONG" else "🔴"
        pos_txt += f"  {ico} `{par}` e:`{pos['entrada']:.4f}` → `{p_actual:.4f}` est:${pnl_est:+.2f}\n"

    if not pos_txt:
        pos_txt = "  _(sin posiciones)_\n"

    w, l = estado.wins, estado.losses
    wr   = f"{w/(w+l)*100:.1f}%" if (w+l) > 0 else "N/A"

    _notif(
        f"📊 *Reporte — {config.VERSION} v5.0*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: `${balance:.2f} USDT`\n"
        f"📈 Hoy: `{w}W/{l}L` | WR: `{wr}`\n"
        f"PnL hoy: `${estado.pnl_hoy:+.2f}` USDT\n"
        f"🏅 Score mín: `{config.SCORE_MIN}/100`\n"
        f"📋 Posiciones:\n{pos_txt}"
        f"{'⚠️ *CIRCUIT BREAKER ACTIVO*' if estado.cb_activo else ''}"
    )


# ═══════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════

def main():
    log.info("=" * 55)
    log.info(f"{config.VERSION} — v5.0")
    log.info(f"Score mín: {config.SCORE_MIN}/100")
    log.info(f"LONG:  RSI<{config.RSI_OVERSOLD}  + BB inf + precio>EMA200")
    log.info(f"SHORT: RSI>{config.RSI_OVERBOUGHT} + BB sup + precio<EMA200")
    log.info(f"MAX_POS: {config.MAX_POSICIONES} | LEV: {config.LEVERAGE}x | MARGEN: $8 fijo")
    log.info("=" * 55)

    balance = exchange.get_balance()
    log.info(f"Balance: ${balance:.2f} USDT | MODO_DEMO={config.MODO_DEMO}")

    if balance <= 0 and not config.MODO_DEMO:
        log.error("Balance = 0 — verifica API keys en Railway")
        _notif("🚨 *Balance = $0.00*\nVerifica `BINGX_API_KEY` y `BINGX_SECRET_KEY` en Railway.")

    if PARES_FIJOS:
        pares = PARES_FIJOS[:100]
        log.info(f"Usando {len(pares)} pares de config_pares.py")
    else:
        pares = ["BTC-USDT","ETH-USDT","SOL-USDT","XRP-USDT","DOGE-USDT",
                 "BNB-USDT","AVAX-USDT","LINK-USDT","ADA-USDT","DOT-USDT",
                 "UNI-USDT","ATOM-USDT","LTC-USDT","OP-USDT","ARB-USDT","INJ-USDT"]
        log.info(f"Usando {len(pares)} pares por defecto")

    _notif(
        f"🤖 *{config.VERSION} v5.0* arrancado\n"
        f"💰 Balance: `${balance:.2f} USDT`\n"
        f"📊 Pares: `{len(pares)}` | Max pos: `{config.MAX_POSICIONES}`\n"
        f"🏅 Score: `{config.SCORE_MIN}/100` | Lev: `{config.LEVERAGE}x`\n"
        f"📈 Filtro EMA200: *ACTIVO*\n"
        f"🧠 Memoria: *ACTIVA*\n"
        f"🔄 Sync BingX: *ACTIVO*\n"
        f"{'🔇 *DEMO*' if config.MODO_DEMO else '🟢 *LIVE — DINERO REAL*'}"
    )

    ciclo = 0
    last_reporte = time.time()

    while True:
        try:
            ciclo += 1
            estado.reset_diario()
            balance = exchange.get_balance()

            log.info(
                f"Ciclo {ciclo} | {datetime.now(timezone.utc).strftime('%H:%M UTC')} | "
                f"Bal:${balance:.2f} | Pos:{len(estado.posiciones)} | PnL:${estado.pnl_hoy:+.2f}"
            )

            # 1. Sincronizar posiciones reales con BingX
            sincronizar_posiciones()

            # 2. Circuit breaker
            if estado.check_circuit_breaker(balance):
                log.warning("⏸ Circuit breaker activo — esperando hasta mañana")
                _notif(
                    f"🚨 *Circuit Breaker*\n"
                    f"PnL hoy: `${estado.pnl_hoy:+.2f}`\n"
                    f"Pérdidas seguidas: `{estado.perdidas_cons}`\n"
                    f"Bot pausado hasta mañana."
                )
                time.sleep(3600)
                continue

            # 3. Gestionar posiciones abiertas (SL/TP software)
            if estado.posiciones:
                gestionar_posiciones(balance)
                balance = exchange.get_balance()

            # 4. Escanear señales nuevas
            if len(estado.posiciones) < config.MAX_POSICIONES:
                log.info(f"Escaneando {len(pares)} pares (score≥{config.SCORE_MIN})...")
                senales = analizar.analizar_todos(pares)

                if senales:
                    log.info(f"✓ {len(senales)} señal(es):")
                    for s in senales:
                        log.info(f"  {s['lado']:5s} {s['par']:20s} score={s['score']:3d} RSI={s['rsi']:.1f}")
                else:
                    log.info("Sin señales este ciclo")

                for s in senales:
                    if len(estado.posiciones) >= config.MAX_POSICIONES:
                        break
                    if s["par"] in estado.posiciones:
                        continue

                    # Ajustar score con memoria
                    s["score"] = memoria.ajustar_score(s["par"], s["score"])
                    if s["score"] < config.SCORE_MIN:
                        log.info(f"[MEMORIA] {s['par']} score={s['score']} < {config.SCORE_MIN} — skip")
                        continue

                    ejecutado = ejecutar_senal(s, balance)
                    _notif_senal(s, balance, ejecutado)

                    if ejecutado:
                        balance = exchange.get_balance()
                        time.sleep(2)

            # 5. Reporte horario
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
            except Exception:
                pass

        log.info(f"Próximo ciclo en {config.LOOP_SECONDS}s — {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
        log.info("-" * 55)
        time.sleep(config.LOOP_SECONDS)


if __name__ == "__main__":
    main()
