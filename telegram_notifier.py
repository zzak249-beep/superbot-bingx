"""
telegram_notifier.py — Notificaciones Telegram con formato HTML
"""
import logging
from datetime import datetime, timezone

import requests

import config as C
from strategy import Signal

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self):
        self.token   = C.TELEGRAM_TOKEN
        self.chat_id = C.TELEGRAM_CHAT_ID
        self._url    = f"https://api.telegram.org/bot{self.token}/sendMessage"

    def _send(self, text: str):
        if not self.token or not self.chat_id:
            log.warning("Telegram no configurado")
            return
        try:
            requests.post(self._url, json={
                "chat_id":    self.chat_id,
                "text":       text,
                "parse_mode": "HTML",
            }, timeout=10)
        except Exception as e:
            log.error(f"Telegram error: {e}")

    # ── Nivel de señal ─────────────────────────────────────────

    @staticmethod
    def _level_emoji(level: str) -> str:
        return {"SUP": "⭐", "FUEL": "🔥", "STD": "▲"}.get(level, "")

    @staticmethod
    def _dir_emoji(direction: str) -> str:
        return "🟢" if direction == "LONG" else "🔴"

    # ── Mensajes ───────────────────────────────────────────────

    def send_entry(self, symbol: str, sig: Signal, qty: float, balance: float):
        e  = self._level_emoji(sig.level)
        d  = self._dir_emoji(sig.direction)
        rr = abs(sig.tp - sig.entry) / max(abs(sig.entry - sig.sl), 1e-9)

        reasons_text = "\n".join(f"  {r}" for r in sig.reasons) if sig.reasons else "  —"

        text = (
            f"{e} {d} <b>{sig.direction} {sig.level}</b> — {symbol}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 Entrada:    <code>{sig.entry:.4f}</code>\n"
            f"🛑 Stop Loss:  <code>{sig.sl:.4f}</code>\n"
            f"🎯 Take Profit:<code>{sig.tp:.4f}</code>\n"
            f"📐 RR:         <b>{rr:.2f}:1</b>\n"
            f"🎲 Cantidad:   <code>{qty:.6f}</code>\n"
            f"💎 Convicción: <b>{sig.conviction}/10</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>📊 Indicadores</b>\n"
            f"  Score:  {sig.score:+.3f}  |  CVD: {'↑' if sig.cvd_rising else '↓'}\n"
            f"  Squeeze: {'ON 🔲' if sig.sq_on else 'off'}  |  Sesión: {sig.ses_label}\n"
            f"  Funding: {sig.funding*100:.4f}%  |  Spread: {sig.bp_drain:.4f}bp\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>✅ Confluencias</b>\n{reasons_text}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: <code>{balance:.2f} USDT</code>\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC"
        )
        self._send(text)

    def send_close(self, symbol: str, direction: str, entry: float,
                   close: float, pnl: float, reason: str, qty: float):
        icon = "✅" if pnl >= 0 else "❌"
        d    = self._dir_emoji(direction)
        pct  = (pnl / (entry * qty)) * 100 if entry * qty > 0 else 0

        text = (
            f"{icon} <b>CIERRE {reason}</b> — {symbol}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{d} Dirección: <b>{direction}</b>\n"
            f"📍 Entrada:  <code>{entry:.4f}</code>\n"
            f"🏁 Salida:   <code>{close:.4f}</code>\n"
            f"💵 PnL:      <b>{'+'if pnl>=0 else ''}{pnl:.2f} USDT ({pct:+.2f}%)</b>\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC"
        )
        self._send(text)

    def send_scan_result(self, results: list):
        """Resumen del scanner multi-par"""
        if not results:
            return
        lines = [f"🔍 <b>SCAN 3min</b> — {len(results)} señales\n"]
        for r in results[:5]:
            e = self._level_emoji(r["level"])
            d = self._dir_emoji(r["direction"])
            lines.append(
                f"{e}{d} <b>{r['symbol']}</b> {r['direction']} {r['level']} "
                f"conv={r['conviction']}/10"
            )
        self._send("\n".join(lines))

    def send_daily_summary(self, pnl: float, n_trades: int, balance: float):
        icon = "📈" if pnl >= 0 else "📉"
        text = (
            f"{icon} <b>Resumen diario</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 PnL hoy:   <b>{'+'if pnl>=0 else ''}{pnl:.2f} USDT</b>\n"
            f"🔢 Trades:    {n_trades}\n"
            f"💰 Balance:   <code>{balance:.2f} USDT</code>\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d')} UTC"
        )
        self._send(text)

    def send_error(self, msg: str):
        self._send(f"⚠️ <b>ERROR</b>\n{msg}")

    def send_startup(self, symbol: str, balance: float, multi: bool):
        mode = f"Multi-par TOP {C.TOP_PAIRS}" if multi else f"Single: {symbol}"
        text = (
            f"🚀 <b>QF×JP Bot v3 iniciado</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚙️  Modo:       {mode}\n"
            f"⏱️  TF:         3min\n"
            f"🔑  Apal.:      {C.LEVERAGE}x\n"
            f"⚠️  Riesgo/op:  {C.RISK_PER_TRADE*100:.1f}%\n"
            f"🛑  Límite día: {C.DAILY_LOSS_LIMIT*100:.0f}%\n"
            f"💰  Balance:    <code>{balance:.2f} USDT</code>\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC"
        )
        self._send(text)

    def send_blocked(self, reason: str):
        self._send(f"⛔ <b>Operación bloqueada</b>\n{reason}")
