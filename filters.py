"""
Correlation Filter
──────────────────
Problema: abrir DOGE y SHIB a la vez = mismo trade dos veces.
Si se liquida uno, se liquida el otro → riesgo doblado.

Solución: matriz de correlación dinámica.
Si dos monedas tienen correlación > 0.80 en las últimas 24h,
solo se permite una de ellas en cartera simultáneamente.
"""

import numpy as np
from loguru import logger


class CorrelationFilter:
    def __init__(
        self,
        max_correlation: float = 0.80,
        window_bars:     int   = 96,     # ~24h en 15m = 96 velas
    ):
        self.max_correlation = max_correlation
        self.window_bars     = window_bars
        self._price_cache:   dict[str, np.ndarray] = {}

    def update_prices(self, symbol: str, closes: np.ndarray):
        """Guarda los últimos N cierres para el símbolo"""
        self._price_cache[symbol] = closes[-self.window_bars:]

    def _pearson(self, a: np.ndarray, b: np.ndarray) -> float:
        """Correlación de Pearson sobre retornos log"""
        n = min(len(a), len(b))
        if n < 20:
            return 0.0
        ra = np.diff(np.log(a[-n:] + 1e-10))
        rb = np.diff(np.log(b[-n:] + 1e-10))
        if ra.std() < 1e-10 or rb.std() < 1e-10:
            return 0.0
        return float(np.corrcoef(ra, rb)[0, 1])

    def is_allowed(self, new_symbol: str, open_symbols: list[str]) -> tuple[bool, str]:
        """
        ¿Se puede abrir new_symbol dados los open_symbols actuales?
        """
        if new_symbol not in self._price_cache:
            return True, "No hay datos de correlación — se permite"

        for sym in open_symbols:
            if sym not in self._price_cache:
                continue
            corr = self._pearson(
                self._price_cache[new_symbol],
                self._price_cache[sym]
            )
            if abs(corr) >= self.max_correlation:
                return False, (
                    f"Correlación {new_symbol}/{sym} = {corr:.2f} "
                    f"≥ {self.max_correlation} → bloqueado"
                )
            logger.debug(f"Correlación {new_symbol}/{sym} = {corr:.2f} ✅")

        return True, "Correlación OK"


"""
Telegram Notifier
─────────────────
Envía alertas a tu Telegram cuando:
  • Se abre una posición
  • Se cierra con P&L
  • Se activa el stop diario
  • Señal bloqueada por filtros

Requiere: TELEGRAM_TOKEN y TELEGRAM_CHAT_ID en .env
"""

import httpx
import asyncio
import os
from datetime import datetime


class TelegramNotifier:
    BASE = "https://api.telegram.org/bot"

    def __init__(
        self,
        token:   str = "",
        chat_id: str = "",
        enabled: bool = True,
    ):
        self.token   = token   or os.getenv("TELEGRAM_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = enabled and bool(self.token) and bool(self.chat_id)
        self._client = httpx.AsyncClient(timeout=10)

        if not self.enabled:
            logger.info("Telegram: desactivado (configura TELEGRAM_TOKEN y TELEGRAM_CHAT_ID)")

    async def send(self, text: str, silent: bool = False):
        """Envía mensaje a Telegram (falla silenciosamente)"""
        if not self.enabled:
            return
        try:
            await self._client.post(
                f"{self.BASE}{self.token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_notification": silent,
                },
            )
        except Exception as e:
            logger.debug(f"Telegram error: {e}")

    async def trade_opened(
        self, symbol: str, side: str, qty: float,
        entry: float, sl: float, tp: float,
        score: float, method: str, confluence: float
    ):
        rr = abs(tp - entry) / (abs(sl - entry) + 1e-10)
        emoji = "🟢" if side == "LONG" else "🔴"
        msg = (
            f"{emoji} <b>ENTRADA {side} — {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💰 Entry: <code>{entry:.6f}</code>\n"
            f"🛑 SL:    <code>{sl:.6f}</code>\n"
            f"🎯 TP:    <code>{tp:.6f}</code>\n"
            f"📐 R:R    {rr:.2f}x\n"
            f"📊 Score: {score:.0f}/100  |  MTF: {confluence:+.0f}\n"
            f"📦 Qty:   {qty}  |  {method.upper()}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send(msg)

    async def trade_closed(
        self, symbol: str, side: str, entry: float,
        exit_price: float, pnl_pct: float, reason: str, daily_pnl: float
    ):
        emoji = "✅" if pnl_pct > 0 else "❌"
        msg = (
            f"{emoji} <b>CIERRE {side} — {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📥 Entry:  <code>{entry:.6f}</code>\n"
            f"📤 Exit:   <code>{exit_price:.6f}</code>\n"
            f"💹 P&amp;L:   <b>{pnl_pct:+.2f}%</b>\n"
            f"📋 Motivo: {reason}\n"
            f"📅 P&amp;L día: {daily_pnl:+.2f}%\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send(msg)

    async def daily_stop(self, daily_pnl: float):
        msg = (
            f"⛔ <b>STOP DIARIO ACTIVADO</b>\n"
            f"Pérdida acumulada: <b>{daily_pnl:.2f}%</b>\n"
            f"El bot no operará hasta mañana."
        )
        await self.send(msg)

    async def signal_blocked(self, symbol: str, side: str, reason: str):
        await self.send(
            f"⚠️ Señal bloqueada: <b>{symbol} {side}</b>\n"
            f"Motivo: {reason}",
            silent=True,
        )

    async def startup(self, demo: bool):
        mode = "⚠️ DEMO" if demo else "🔴 REAL"
        await self.send(
            f"🤖 <b>BingX Bot v2 arrancado</b> — modo {mode}\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    async def close(self):
        await self._client.aclose()
