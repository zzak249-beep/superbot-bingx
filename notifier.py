"""
notifier.py — Notificaciones Telegram Elite v4
Incluye: señales manuales, score, estado del bot, errores.
"""
import logging
import requests
import config as cfg

log = logging.getLogger("notifier")

def _send(text: str, parse_mode="Markdown"):
    """Funcion base para enviar mensajes."""
    if not cfg.TELEGRAM_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        log.warning("Telegram no configurado (TG_TOKEN / TG_CHAT_ID)")
        return
    try:
        url = f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id":    cfg.TELEGRAM_CHAT_ID,
            "text":       text,
            "parse_mode": parse_mode,
        }, timeout=10)
        if not resp.ok:
            log.warning(f"Telegram error {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        log.error(f"Error enviando Telegram: {e}")


def send_raw(message: str):
    """Mensaje libre en Markdown."""
    _send(message)


def send_startup(symbol_stats: str):
    _send(
        f"🤖 *BB+RSI Bot Elite v4 arrancado*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {symbol_stats}\n"
        f"⚙️ RSI_OB: `{cfg.RSI_OB}` | BB_σ: `{cfg.BB_SIGMA}` | "
        f"SL: `{cfg.SL_ATR}x ATR` | Lev: `{cfg.LEVERAGE}x`\n"
        f"🔁 Ciclo: cada `{cfg.LOOP_SECONDS}s` | "
        f"Max pos: `{cfg.MAX_POSITIONS}`\n"
        f"🛡️ Circuit Breaker: `-{cfg.CB_MAX_DAILY_LOSS_PCT*100:.0f}%` dia | "
        f"`{cfg.CB_MAX_CONSECUTIVE_LOSS}` perdidas"
    )


def send_buy_signal(symbol: str, sig: dict, balance: float, executed: bool):
    side = "🟢 LONG" if sig["action"] == "buy" else "🔴 SHORT"
    ex   = "✅ Ejecutado" if executed else "⚠️ No ejecutado"
    score_line = f"Score   : `{sig.get('score','N/A')}/100`\n" if sig.get("score") else ""
    _send(
        f"{side} — `{symbol}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Entrada : `{sig['entry']}`\n"
        f"🛑 SL   : `{sig['sl']}`\n"
        f"🎯 TP   : `{sig['tp']}`\n"
        f"TP 50%  : `{sig.get('tp_partial','N/A')}`\n"
        f"RSI     : `{sig.get('rsi','N/A')}`\n"
        f"{score_line}"
        f"4h      : `{sig.get('trend_4h','N/A')}`\n"
        f"Balance : `${balance:.2f}`\n"
        f"Razon   : _{sig.get('reason','')}_\n"
        f"{ex}"
    )


def send_close_signal(symbol: str, entry: float, exit_price: float,
                      pnl: float, reason: str, executed: bool):
    emoji  = "✅" if pnl >= 0 else "❌"
    ex_txt = "Ejecutado" if executed else "Simulado"
    _send(
        f"{emoji} *CIERRE* — `{symbol}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Entrada : `{entry}`\n"
        f"Salida  : `{exit_price}`\n"
        f"PnL est : `${pnl:+.2f}`\n"
        f"Razon   : `{reason}`\n"
        f"Estado  : {ex_txt}"
    )


def send_status(positions: list, balance: float, stats: dict, perf: str):
    pos_lines = ""
    for p in positions:
        side_e = "🟢" if p.get("side") == "long" else "🔴"
        diff   = p.get("current", p["entry"]) - p["entry"]
        pos_lines += f"  {side_e} `{p['symbol']}` e:{p['entry']} c:{p.get('current',p['entry'])} ({diff:+.4f})\n"
    if not pos_lines:
        pos_lines = "  _(sin posiciones abiertas)_\n"

    wins   = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    total  = wins + losses
    wr     = f"{wins/total*100:.1f}%" if total > 0 else "N/A"

    pnl_total = stats.get("pnl_total", None)
    roi_total = stats.get("roi_total", None)
    ttrades   = stats.get("total_trades", "")
    total_line = (
        f"📊 Total: `{ttrades}tr` | PnL: `${pnl_total:+.2f}` | ROI: `{roi_total:+.1f}%`\n"
        if pnl_total is not None else ""
    )
    _send(
        f"📊 *Reporte horario*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: `${balance:.2f} USDT`\n"
        f"📈 Hoy: `{wins}W / {losses}L` | WR: `{wr}` | PnL hoy: `${stats.get('pnl_today',0):+.2f}`\n"
        f"{total_line}"
        f"📋 Posiciones abiertas:\n{pos_lines}"
        f"🤖 Learner: _{perf}_"
    )


def send_no_funds(symbol: str, sig: dict, balance: float):
    """Deprecado — ahora se usa send_raw con señal completa desde main.py"""
    pass


def send_error(msg: str):
    _send(f"🚨 *ERROR BOT*\n`{msg}`")


def send_param_update(updates: dict, perf: str):
    lines = "\n".join([f"  `{k}`: {v}" for k, v in updates.items()])
    _send(
        f"🔧 *Learner — Ajuste de parametros*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{lines}\n"
        f"📊 {perf}"
    )


def send_symbols_update(symbols: list):
    _send(
        f"🔄 *Pares actualizados*\n"
        f"Total: `{len(symbols)}`\n"
        f"Top 5: `{', '.join(symbols[:5])}`"
    )
