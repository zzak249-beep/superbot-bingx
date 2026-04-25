"""
notifications/telegram_notifier.py — Cliente Telegram para notificaciones del bot.
Envía mensajes HTML usando la Bot API de Telegram.
"""

import asyncio
import logging
from typing import List, Optional

import aiohttp

from config import config

log = logging.getLogger("telegram")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None

    def start(self):
        """Arranca el worker de envío en background."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._worker())
            log.info("Telegram notifier iniciado.")

    async def close(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    # ── API de bajo nivel ─────────────────────────────────────────────────────

    async def send(self, text: str):
        """Encola un mensaje para enviar."""
        await self._queue.put(text)

    async def _send_now(self, text: str):
        """Envío inmediato, sin cola."""
        if not config.TG_TOKEN or not config.TG_CHAT_ID:
            log.debug(f"[Telegram desactivado] {text[:80]}")
            return
        url = TELEGRAM_API.format(token=config.TG_TOKEN)
        payload = {
            "chat_id": config.TG_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            session = await self._get_session()
            async with session.post(url, json=payload) as r:
                if r.status != 200:
                    body = await r.text()
                    log.warning(f"Telegram error {r.status}: {body[:200]}")
        except Exception as e:
            log.warning(f"Telegram send failed: {e}")

    async def _worker(self):
        """Procesa la cola de mensajes uno a uno."""
        while True:
            try:
                text = await self._queue.get()
                await self._send_now(text)
                self._queue.task_done()
                await asyncio.sleep(0.3)  # evitar flood
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Telegram worker error: {e}")

    # ── Métodos de alto nivel ─────────────────────────────────────────────────

    async def notify_start(self, symbols: List[str]):
        mode = "📄 PAPER" if config.PAPER else "💰 REAL"
        syms_str = ", ".join(symbols[:10])
        extra = f" +{len(symbols)-10} más" if len(symbols) > 10 else ""
        await self.send(
            f"🤖 <b>Bot iniciado</b> — {mode}\n"
            f"Símbolos: <code>{syms_str}{extra}</code>\n"
            f"HTF={config.HTF} MTF={config.MTF} LTF={config.LTF}\n"
            f"Leverage: {config.LEVERAGE}x | MaxPos: {config.MAX_POSITIONS}"
        )

    async def notify_signal(self, summary: str):
        await self.send(f"🔔 <b>Señal detectada</b>\n{summary}")

    async def notify_order(self, symbol: str, direction: str, entry: float,
                            sl: float, tp: float, size_usdt: float, order_id: str):
        emoji = "🟢" if direction == "LONG" else "🔴"
        await self.send(
            f"{emoji} <b>ORDEN ABIERTA</b> — {symbol}\n"
            f"Dirección : <b>{direction}</b>\n"
            f"Entry     : <code>{entry:.4f}</code>\n"
            f"SL        : <code>{sl:.4f}</code>\n"
            f"TP        : <code>{tp:.4f}</code>\n"
            f"Tamaño    : <code>${size_usdt:.2f}</code>\n"
            f"Order ID  : <code>{order_id}</code>"
        )

    async def notify_close(self, symbol: str, pnl: float, reason: str):
        emoji = "✅" if pnl >= 0 else "❌"
        sign  = "+" if pnl >= 0 else ""
        await self.send(
            f"{emoji} <b>POSICIÓN CERRADA</b> — {symbol}\n"
            f"Motivo : {reason}\n"
            f"PnL    : <code>{sign}{pnl:.2f} USDT</code>"
        )

    async def notify_error(self, message: str):
        await self.send(f"⚠️ <b>ERROR</b>\n<code>{message[:400]}</code>")


# Instancia global
telegram = TelegramNotifier()
