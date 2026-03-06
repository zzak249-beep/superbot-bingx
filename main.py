"""
main.py — BingX RSI+BB Bot v4.0
CAMBIOS v4:
  - Ejecuta tanto LONG (RSI sobrevendido + BB inf) como SHORT (RSI sobrecomprado + BB sup)
  - Solo notifica y ejecuta señales con score >= SCORE_MIN (75)
  - Usa exchange en modo ONE-WAY (sin positionSide) → corrige code=109400
  - Gestión de posiciones LONG y SHORT independiente
  - Circuit breaker por pérdida diaria y pérdidas consecutivas
"""

import sys
import os
import time
import traceback
from datetime import datetime, date, timezone

# ── Logging ──────────────────────────────────────────
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("main")
log.info("=== ARRANQUE BOT BINGX RSI+BB ===")

# ── Imports ───────────────────────────────────────────
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
log.info(f"SCORE_MIN={config.SCORE_MIN} | LEVERAGE={config.LEVERAGE}x | "
         f"SL={config.SL_ATR_MULT}×ATR | TP={config.TP_ATR_MULT}×ATR | "
         f"MODO_DEMO={config.MODO_DEMO}")


# ═══════════════════════════════════════════════════════
# ESTADO
# ═══════════════════════════════════════════════════════

class Estado:
    def __init__(self):
        self.posiciones:      dict  = {}   # {par: {lado, entrada, qty, sl, tp, ts}}
        self.operaciones_hoy: list  = []
        self.pnl_hoy:         float = 0.0
        self.perdidas_cons:   int   = 0
        self.cb_activo:       bool  = False
        self.dia_actual:      str   = str(date.today())
        self.wins:            int   = 0
        self.losses:          int   = 0

    def reset_diario(self):
        hoy = str(date.today())
        if hoy != self.dia_actual:
            self.dia_actual      = hoy
            self.pnl_hoy         = 0.0
            self.perdidas_cons   = 0
            self.cb_activo       = False
            self.operaciones_hoy = []
            log.info(f"Reset diario — nuevo día: {hoy}")

    def check_circuit_breaker(self, balance: float) -> bool:
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

    def registrar_cierre(self, pnl: float):
        self.pnl_hoy += pnl
        if pnl > 0:
            self.wins         += 1
            self.perdidas_cons = 0
        else:
            self.losses       += 1
            self.perdidas_cons += 1


estado = Estado()


# ═══════════════════════════════════════════════════════
# NOTIFIER — helpers simples
# ═══════════════════════════════════════════════════════

def _notif(msg: str):
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


def _notif_senal(r: dict, balance: float, ejecutado: bool):
    lado   = "🟢 LONG" if r["lado"] == "LONG" else "🔴 SHORT"
    ex_txt = "✅ *Ejecutado*" if ejecutado else "⚠️ *No ejecutado*"
    _notif(
        f"{lado} — `{r['par']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Entrada : `{r['precio']:.6f}`\n"
        f"🛑 SL      : `{r['sl']:.6f}`\n"
        f"✅ TP      : `{r['tp']:.6f}`\n"
        f"📊 R:R     : `{r['rr']:.2f}x`\n"
        f"🏅 Score   : `{r['score']}/100`\n"
        f"📉 RSI     : `{r['rsi']:.1f}`\n"
        f"💰 Balance : `${balance:.2f} USDT`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{ex_txt}"
    )


def _notif_cierre(par: str, lado: str, entrada: float, salida: float, pnl: float):
    ico = "✅" if pnl >= 0 else "❌"
    _notif(
        f"{ico} *CIERRE {lado}* — `{par}`\n"
        f"Entrada → Salida: `{entrada:.6f}` → `{salida:.6f}`\n"
        f"PnL estimado: `${pnl:+.2f} USDT`"
    )


# ═══════════════════════════════════════════════════════
# GESTIÓN DE POSICIONES ABIERTAS
# ═══════════════════════════════════════════════════════

def gestionar_posiciones(balance: float):
    for par, pos in list(estado.posiciones.items()):
        try:
            precio = exchange.get_precio(par)
            if precio <= 0:
                continue

            lado   = pos["lado"]
            entrada = pos["entrada"]
            sl      = pos["sl"]
            tp      = pos["tp"]
            qty     = pos["qty"]

            # Verificar SL/TP
            if lado == "LONG":
                sl_hit = precio <= sl
                tp_hit = precio >= tp
            else:  # SHORT
                sl_hit = precio >= sl
                tp_hit = precio <= tp

            razon = None
            salida = precio
            if sl_hit:
                razon  = "SL"
                salida = sl
            elif tp_hit:
                razon  = "TP"
                salida = tp

            if razon:
                res = exchange.cerrar_posicion(par, qty, lado)
                salida_real = res.get("precio_salida", salida) or salida

                if lado == "LONG":
                    pnl = qty * (salida_real - entrada) * config.LEVERAGE
                else:
                    pnl = qty * (entrada - salida_real) * config.LEVERAGE

                estado.registrar_cierre(pnl)
                memoria.registrar_resultado(par, pnl, lado)
                del estado.posiciones[par]

                log.info(f"CIERRE {lado} {par} @ {salida_real:.6f} PnL={pnl:+.4f} ({razon})")
                _notif_cierre(par, lado, entrada, salida_real, pnl)

        except Exception as e:
            log.error(f"gestionar {par}: {e}")
        time.sleep(0.5)


# ═══════════════════════════════════════════════════════
# EJECUTAR SEÑAL
# ═══════════════════════════════════════════════════════

def ejecutar_senal(r: dict, balance: float) -> bool:
    par    = r["par"]
    lado   = r["lado"]
    precio = r["precio"]
    sl     = r["sl"]
    tp     = r["tp"]

    if par in estado.posiciones:
        log.debug(f"Ya hay posición abierta en {par}")
        return False

    # Verificar blacklist de memoria
    if memoria.esta_bloqueado(par):
        log.info(f"[MEMORIA] {par} bloqueado — saltando")
        return False

    if len(estado.posiciones) >= config.MAX_POSICIONES:
        log.info(f"MAX_POSICIONES ({config.MAX_POSICIONES}) alcanzado")
        return False

    if balance < 4.0 and not config.MODO_DEMO:
        log.warning(f"Balance insuficiente: ${balance:.2f}")
        return False

    qty = exchange.calcular_cantidad(par, balance, precio)
    if qty <= 0:
        log.warning(f"qty=0 para {par} (balance=${balance:.2f} precio={precio:.6f})")
        return False

    if lado == "LONG":
        res = exchange.abrir_long(par, qty, precio, sl, tp)
    else:
        res = exchange.abrir_short(par, qty, precio, sl, tp)

    if not res or "error" in res:
        err = res.get("error", "respuesta vacía") if res else "respuesta vacía"
        log.error(f"Orden fallida {lado} {par}: {err}")
        memoria.registrar_error_api(par, 109400)
        _notif(
            f"🚨 *Orden fallida — {lado} `{par}`*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"❌ `{err}`\n"
            f"qty:`{qty}` precio:`{precio:.6f}`\n"
            f"💡 _Verifica permisos API (Trade) y modo posición en BingX_"
        )
        return False

    estado.posiciones[par] = {
        "lado":   lado,
        "entrada": precio,
        "qty":    qty,
        "sl":     sl,
        "tp":     tp,
        "ts":     datetime.now(timezone.utc).isoformat(),
    }

    log.info(
        f"✅ {lado} {par} qty:{qty} e:{precio:.6f} "
        f"SL:{sl:.6f} TP:{tp:.6f} R:R:{r['rr']:.2f} score:{r['score']}"
    )
    return True


# ═══════════════════════════════════════════════════════
# REPORTE DE STATUS
# ═══════════════════════════════════════════════════════

def enviar_reporte(balance: float):
    pos_txt = ""
    for par, pos in estado.posiciones.items():
        precio_actual = exchange.get_precio(par)
        if pos["lado"] == "LONG":
            pnl_est = pos["qty"] * (precio_actual - pos["entrada"]) * config.LEVERAGE
        else:
            pnl_est = pos["qty"] * (pos["entrada"] - precio_actual) * config.LEVERAGE
        ico = "🟢" if pos["lado"] == "LONG" else "🔴"
        pos_txt += (f"  {ico} `{par}` {pos['lado']} "
                    f"e:`{pos['entrada']:.4f}` → `{precio_actual:.4f}` "
                    f"est:${pnl_est:+.2f}\n")

    if not pos_txt:
        pos_txt = "  _(sin posiciones)_\n"

    w  = estado.wins
    l  = estado.losses
    wr = f"{w/(w+l)*100:.1f}%" if (w+l) > 0 else "N/A"

    _notif(
        f"📊 *Reporte — {config.VERSION}*\n"
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
    log.info(f"{config.VERSION}")
    log.info(f"Score mín: {config.SCORE_MIN}/100 | Solo ejecuta alta convicción")
    log.info(f"LONG:  RSI < {config.RSI_OVERSOLD}  + precio en BB inferior")
    log.info(f"SHORT: RSI > {config.RSI_OVERBOUGHT} + precio en BB superior")
    log.info("=" * 55)

    balance = exchange.get_balance()
    log.info(f"Balance: ${balance:.2f} USDT | MODO_DEMO={config.MODO_DEMO}")

    if balance <= 0 and not config.MODO_DEMO:
        log.error("Balance = 0 — verifica BINGX_API_KEY y BINGX_SECRET_KEY en Railway")
        _notif(
            "🚨 *Balance = $0.00*\n"
            "Verifica `BINGX_API_KEY` y `BINGX_SECRET_KEY` en Railway Variables.\n"
            "Bot en espera — reintentando cada 5 min."
        )

    # Pares a escanear
    if PARES_FIJOS:
        pares = PARES_FIJOS[:100]
        log.info(f"Usando {len(pares)} pares de config_pares.py")
    else:
        pares = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT",
                 "DOGE-USDT", "BNB-USDT", "AVAX-USDT", "LINK-USDT",
                 "ADA-USDT", "DOT-USDT", "MATIC-USDT", "UNI-USDT",
                 "ATOM-USDT", "LTC-USDT", "OP-USDT", "ARB-USDT"]
        log.info(f"Usando {len(pares)} pares por defecto")

    _notif(
        f"🤖 *{config.VERSION}* arrancado\n"
        f"💰 Balance: `${balance:.2f} USDT`\n"
        f"📊 Pares: `{len(pares)}`\n"
        f"🏅 Score mín: `{config.SCORE_MIN}/100`\n"
        f"🟢 LONG: RSI<{config.RSI_OVERSOLD} + BB inf\n"
        f"🔴 SHORT: RSI>{config.RSI_OVERBOUGHT} + BB sup\n"
        f"⚙️ Lev:`{config.LEVERAGE}x` Riesgo:`{config.RISK_PCT*100:.0f}%`\n"
        f"{'🔇 *DEMO (sin trades reales)*' if config.MODO_DEMO else '🟢 *LIVE — DINERO REAL*'}"
    ) if hasattr(config, "RISK_PCT") else _notif(
        f"🤖 *{config.VERSION}* arrancado\n"
        f"💰 Balance: `${balance:.2f} USDT` | Score mín: `{config.SCORE_MIN}/100`\n"
        f"🟢 LONG: RSI<{config.RSI_OVERSOLD} | 🔴 SHORT: RSI>{config.RSI_OVERBOUGHT}\n"
        f"{'🔇 DEMO' if config.MODO_DEMO else '🟢 LIVE'}"
    )

    ciclo        = 0
    last_reporte = time.time()

    while True:
        try:
            ciclo += 1
            estado.reset_diario()
            balance = exchange.get_balance()

            log.info(
                f"Ciclo {ciclo} | "
                f"{datetime.now(timezone.utc).strftime('%H:%M UTC')} | "
                f"Bal:${balance:.2f} | Pos:{len(estado.posiciones)} | "
                f"PnL hoy:${estado.pnl_hoy:+.2f}"
            )

            # Circuit breaker
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

            # Gestionar posiciones abiertas
            if estado.posiciones:
                gestionar_posiciones(balance)
                balance = exchange.get_balance()

            # Escaneo de señales (LONG + SHORT)
            if len(estado.posiciones) < config.MAX_POSICIONES:
                log.info(f"Escaneando {len(pares)} pares (score≥{config.SCORE_MIN})...")
                señales = analizar.analizar_todos(pares)

                if señales:
                    log.info(f"✓ {len(señales)} señal(es) encontrada(s):")
                    for s in señales:
                        log.info(
                            f"  {s['lado']:5s} {s['par']:20s} "
                            f"score={s['score']:3d} RSI={s['rsi']:.1f} R:R={s['rr']:.2f}"
                        )
                else:
                    log.info("Sin señales con score suficiente en este ciclo")

                for s in señales:
                    if len(estado.posiciones) >= config.MAX_POSICIONES:
                        break
                    if s["par"] in estado.posiciones:
                        continue

                    # Ajustar score según historial del par
                    s["score"] = memoria.ajustar_score(s["par"], s["score"])
                    if s["score"] < config.SCORE_MIN:
                        log.info(f"[MEMORIA] {s['par']} score ajustado a {s['score']} < {config.SCORE_MIN} — saltando")
                        continue

                    ejecutado = ejecutar_senal(s, balance)
                    _notif_senal(s, balance, ejecutado)

                    if ejecutado:
                        balance = exchange.get_balance()
                        time.sleep(2)

            # Reporte horario
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
