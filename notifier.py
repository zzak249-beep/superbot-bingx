"""
notifier.py — Telegram notifications for SuperBot v4

Functions used by bot.py:
  notify_startup(balance, dry_run)
  notify_trade_opened(symbol, direction, qty, entry, sl, tp1, tp2, notional, dry_run)
  notify_sl_hit(symbol, sl_price, loss)
  notify_tp_hit(symbol, tp_num, price, qty, pnl)
"""

import os
import logging
import requests

log = logging.getLogger("notifier")

TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def _send(msg: str):
    """Send message to Telegram. Silently skips if not configured."""
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=8,
        )
    except Exception as e:
        log.debug(f"Telegram send error: {e}")


# ── Public API ────────────────────────────────────────────────────────────────

def notify_startup(balance: float, dry_run: bool):
    mode = "🔵 DRY RUN" if dry_run else "🟢 LIVE"
    msg  = (
        f"🤖 <b>SuperBot v4 iniciado</b>\n"
        f"Modo: {mode}\n"
        f"💼 Balance: ${balance:.2f}"
    )
    log.info(f"[notifier] startup balance=${balance:.2f} dry={dry_run}")
    _send(msg)


def notify_trade_opened(
    symbol: str,
    direction: str,
    qty: float,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    notional: float,
    dry_run: bool = False,
):
    emoji = "🟢" if direction == "LONG" else "🔴"
    tag   = " [DRY]" if dry_run else ""
    msg   = (
        f"{emoji} <b>ENTRADA{tag} {direction}</b> — {symbol}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💰 Qty: {qty}  |  Entry: {entry}\n"
        f"🛑 SL: {sl}\n"
        f"✅ TP1: {tp1}  |  TP2: {tp2}\n"
        f"📦 Notional: ${notional:.2f}"
    )
    log.info(f"[notifier] trade_opened {symbol} {direction} qty={qty} entry={entry}")
    _send(msg)


def notify_sl_hit(symbol: str, sl_price: float, loss: float):
    msg = (
        f"🔴 <b>SL HIT</b> — {symbol}\n"
        f"Precio SL: {sl_price}\n"
        f"Pérdida est.: ${loss:.2f}"
    )
    log.info(f"[notifier] sl_hit {symbol} sl={sl_price} loss={loss:.2f}")
    _send(msg)


def notify_tp_hit(symbol: str, tp_num: int, price: float, qty: float, pnl: float):
    emoji = "💰" if tp_num == 1 else "🎯"
    msg   = (
        f"{emoji} <b>TP{tp_num} HIT</b> — {symbol}\n"
        f"Precio: {price}  |  Qty: {qty}\n"
        f"PnL neto: ${pnl:.2f}"
    )
    log.info(f"[notifier] tp{tp_num}_hit {symbol} price={price} pnl={pnl:.2f}")
    _send(msg)


# ── Legacy helpers (for old scanner/pos_manager code) ─────────────────────────

def send(msg: str):
    _send(msg)
