"""
notifier.py — Notificaciones Telegram para SuperBot v5
Envía alertas de: inicio, trades abiertos, TP, SL, resumen diario.

Configuración en .env:
    TELEGRAM_BOT_TOKEN=xxx
    TELEGRAM_CHAT_ID=xxx

Si no está configurado, las notificaciones se loguean pero no se envían.
"""
import logging
import os
import requests
from datetime import datetime

log = logging.getLogger("Notifier")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
ENABLED   = bool(BOT_TOKEN and CHAT_ID)


def _send(text: str):
    if not ENABLED:
        log.info(f"[NOTIFY] {text[:120]}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception as e:
        log.warning(f"Telegram error: {e}")


def notify_startup(balance: float, dry_run: bool):
    mode = "🔵 PAPER TRADING" if dry_run else "🟢 LIVE TRADING"
    _send(
        f"🤖 <b>SuperBot v5 iniciado</b>\n"
        f"Modo: {mode}\n"
        f"Balance: <b>${balance:.2f} USDT</b>\n"
        f"Hora: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
    )


def notify_trade_opened(symbol: str, direction: str, qty: float,
                         entry: float, sl: float, tp1: float, tp2: float,
                         notional: float, dry_run: bool = False):
    emoji = "📈" if direction == "LONG" else "📉"
    mode  = "[DRY] " if dry_run else ""
    _send(
        f"{emoji} <b>{mode}Trade Abierto</b>\n"
        f"Par: <b>{symbol}</b> {direction}\n"
        f"Qty: {qty} | Notional: ${notional:.2f}\n"
        f"Entry: {entry} | SL: {sl}\n"
        f"TP1: {tp1} | TP2: {tp2}"
    )


def notify_tp_hit(symbol: str, tp_num: int, price: float,
                   qty_closed: float, pnl: float):
    _send(
        f"💰 <b>TP{tp_num} Alcanzado</b> — {symbol}\n"
        f"Precio: {price} | Qty cerrada: {qty_closed}\n"
        f"PnL estimado: <b>${pnl:+.2f}</b>"
    )


def notify_sl_hit(symbol: str, sl_price: float, loss: float):
    _send(
        f"🛑 <b>SL Tocado</b> — {symbol}\n"
        f"Precio SL: {sl_price}\n"
        f"Pérdida est.: <b>${loss:.2f}</b>"
    )


def notify_circuit_breaker(loss_pct: float, daily_pnl: float):
    _send(
        f"⚡ <b>CIRCUIT BREAKER ACTIVADO</b>\n"
        f"Pérdida diaria: <b>{loss_pct:.1%}</b>\n"
        f"PnL del día: ${daily_pnl:.2f}\n"
        f"Bot pausado hasta mañana."
    )


def notify_daily_summary(balance: float, daily_pnl: float,
                          trades: int, wins: int, fees: float):
    wr = (wins / trades * 100) if trades > 0 else 0
    emoji = "📈" if daily_pnl >= 0 else "📉"
    _send(
        f"{emoji} <b>Resumen Diario</b>\n"
        f"Balance: ${balance:.2f}\n"
        f"PnL: <b>${daily_pnl:+.2f}</b>\n"
        f"Trades: {trades} | Wins: {wins} ({wr:.0f}%)\n"
        f"Fees: ${fees:.2f}"
    )


def notify_error(message: str):
    _send(f"❌ <b>Error Bot</b>\n{message[:400]}")
