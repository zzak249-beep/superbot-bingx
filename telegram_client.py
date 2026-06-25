"""
Telegram Client — Cascade Bot (standalone)
"""
import asyncio
import logging
import os

import aiohttp

log = logging.getLogger("telegram")

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

_session = None


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if not _session or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    return _session


async def send(text: str):
    if not TOKEN or not CHAT_ID:
        return
    try:
        s = await _get_session()
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        async with s.post(url, json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }) as r:
            if r.status != 200:
                log.debug("Telegram error %s", r.status)
    except Exception as e:
        log.debug("Telegram send error: %s", e)


async def notify_status(risk_status: dict, balance: float, n_symbols: int):
    text = (
        f"💥 *CASCADE BOT* — Iniciado\n"
        f"Balance: `{balance:.2f} USDT`\n"
        f"Posiciones: `{risk_status.get('open_trades', 0)}/{risk_status.get('max_open', 3)}`\n"
        f"Símbolos: `{n_symbols}`"
    )
    await send(text)


async def notify_error(context: str, message: str):
    await send(f"🚨 *ERROR* [{context}]\n`{message[:300]}`")


async def notify_trade_opened(sig, qty: float, order_id: str):
    direction = sig.direction if hasattr(sig, 'direction') else "?"
    symbol    = sig.symbol    if hasattr(sig, 'symbol')    else "?"
    entry     = sig.entry     if hasattr(sig, 'entry')     else 0.0
    sl        = sig.sl        if hasattr(sig, 'sl')        else 0.0
    tp1       = sig.tp1       if hasattr(sig, 'tp1')       else 0.0
    score     = sig.score     if hasattr(sig, 'score')     else 0.0
    tier      = sig.tier      if hasattr(sig, 'tier')      else "CASCADE"
    await send(
        f"💥 *CASCADE ABIERTO* — `{symbol}` {direction}\n"
        f"Entry: `{entry:.6f}` | Qty: `{qty:.6f}`\n"
        f"SL: `{sl:.6f}` | TP1: `{tp1:.6f}`\n"
        f"Score: `{score:.0f}` | Tier: `{tier}`\n"
        f"Order: `{order_id}`"
    )


async def notify_trade_closed(symbol: str, direction: str, entry: float,
                               close: float, qty: float, reason: str, pnl: float):
    emoji = "✅" if pnl >= 0 else "❌"
    await send(
        f"{emoji} *CASCADE CERRADO* — `{symbol}` {direction}\n"
        f"Entry: `{entry:.6f}` → Exit: `{close:.6f}`\n"
        f"PnL: `{pnl:+.4f} USDT` | Razón: `{reason}`"
    )


async def notify_circuit_breaker(symbol: str):
    await send(f"⚡ *CIRCUIT BREAKER* — `{symbol}` bloqueado 10min")


async def notify_time_stop(symbol: str, pnl: float, reason: str):
    await send(
        f"⏱️ *TIME STOP* — `{symbol}`\n"
        f"PnL: `{pnl:+.4f} USDT` | `{reason}`"
    )


async def notify_signal(sig):
    pass   # CASCADE bot no usa esto


async def notify_diagnostics(*args, **kwargs):
    pass


async def notify_journal_report(stats: dict):
    pass


async def notify_harvest_opportunity(*args, **kwargs):
    pass


async def notify_limit_filled(*args, **kwargs):
    pass
