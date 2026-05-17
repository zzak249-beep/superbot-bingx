"""
telegram_notifier.py — Notificaciones Telegram con HTML enriquecido.
Cubre: startup, señales, cierres, escaneo, reporte diario y errores.
"""
import logging
import requests
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

BAR = "━━━━━━━━━━━━━━━━"


class TelegramNotifier:
    def __init__(self):
        self.token   = TELEGRAM_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self._url    = f"https://api.telegram.org/bot{self.token}/sendMessage"

    # ──────────────────────────────────────────────────────
    # Core
    # ──────────────────────────────────────────────────────
    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.token or not self.chat_id:
            logger.warning("Telegram no configurado — omitiendo notificación")
            return False
        try:
            resp = requests.post(
                self._url,
                json={"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode},
                timeout=10,
            )
            ok = resp.json().get("ok", False)
            if not ok:
                logger.error(f"Telegram error: {resp.text}")
            return ok
        except Exception as e:
            logger.error(f"Telegram send exception: {e}")
            return False

    # ──────────────────────────────────────────────────────
    # Mensajes estructurados
    # ──────────────────────────────────────────────────────
    def notify_startup(self, balance: float, symbol_count: int, dry_run: bool = False):
        mode = "⚠️ <b>DRY RUN — SIN DINERO REAL</b>" if dry_run else "✅ <b>MODO REAL</b>"
        self.send(
            f"🚀 <b>SNIPER BOT V35 INICIADO</b>\n{BAR}\n"
            f"{mode}\n"
            f"💰 Balance: <b>${balance:,.2f} USDT</b>\n"
            f"🔍 Pares monitoreados: <b>{symbol_count}</b>\n"
            f"⚙️ Estrategia: Golden Equilibrium\n"
            f"📊 Filtros: Vol {1.5}x | ADX >20 | Time-Stop 45min\n"
            f"🧠 Motor de aprendizaje: <b>ACTIVO</b>"
        )

    def notify_trade_open(self, symbol: str, signal: dict, trade: dict):
        direction = "🟢 LONG" if signal["signal"] == "LONG" else "🔴 SHORT"
        rr = abs(signal["tp"] - signal["entry"]) / abs(signal["entry"] - signal["sl"])
        self.send(
            f"⚡ <b>NUEVA OPERACIÓN V35</b>\n{BAR}\n"
            f"📌 Par: <b>{symbol}</b>\n"
            f"📊 Dirección: <b>{direction}</b>\n"
            f"💰 Entrada: <b>${signal['entry']:.6f}</b>\n"
            f"🛡️ Stop Loss: <b>${signal['sl']:.6f}</b>\n"
            f"🎯 Take Profit: <b>${signal['tp']:.6f}</b>\n"
            f"📐 R:R aprox: <b>1:{rr:.1f}</b>\n"
            f"{BAR}\n"
            f"📈 ADX: {signal['adx']}  |  Fuerza: {signal['strength']}%\n"
            f"📊 Vol ratio: {signal.get('vol_ratio', '?')}x\n"
            f"💵 Capital usado: ${trade.get('position_usdt', 0):.2f} USDT\n"
            f"🔧 Apalancamiento: {trade.get('leverage', 5)}x\n"
            f"⏰ Time-Stop: 45 min"
        )

    def notify_trade_close(self, symbol: str, result: dict):
        pnl  = result.get("pnl", 0)
        emoji = "✅" if pnl > 0 else "❌"
        sign  = "+" if pnl > 0 else ""
        self.send(
            f"{emoji} <b>OPERACIÓN CERRADA</b>\n{BAR}\n"
            f"📌 Par: <b>{symbol}</b>\n"
            f"💸 PnL: <b>{sign}{pnl:.4f} USDT</b>\n"
            f"📋 Razón: {result.get('reason', 'TP/SL')}\n"
            f"⏱️ Duración: {result.get('duration_min', 0):.0f} min"
        )

    def notify_scan_results(self, top_symbols: list, scanner):
        summary = scanner.summary_text(top_symbols, n=5)
        self.send(
            f"🔍 <b>ESCANEO DE MERCADO V35</b>\n{BAR}\n"
            f"Top 5 de {len(top_symbols)} pares:\n"
            f"{summary}\n{BAR}\n"
            f"🤖 Bot activo y escaneando cada 3 min..."
        )

    def notify_daily_report(self, stats: dict):
        wr    = stats.get("winrate", 0)
        emoji = "🏆" if wr >= 55 else ("⚠️" if wr >= 40 else "🚨")
        sign  = "+" if stats.get("total_pnl", 0) >= 0 else ""
        self.send(
            f"{emoji} <b>REPORTE DIARIO V35</b>\n{BAR}\n"
            f"📊 Operaciones: {stats.get('total', 0)}\n"
            f"✅ Ganadoras: {stats.get('wins', 0)}\n"
            f"❌ Perdedoras: {stats.get('losses', 0)}\n"
            f"📈 Winrate: <b>{wr:.1f}%</b>\n"
            f"💰 PnL Total: <b>{sign}{stats.get('total_pnl', 0):.4f} USDT</b>\n"
            f"{BAR}\n"
            f"🧠 Aprendizaje activo:\n"
            f"   {stats.get('learning_notes', 'Analizando...')}"
        )

    def notify_learning_update(self, old_params: dict, new_params: dict, reason: str):
        self.send(
            f"🧠 <b>AJUSTE AUTOMÁTICO V35</b>\n{BAR}\n"
            f"Razón: {reason}\n"
            f"ADX: {old_params.get('adx_min')} → <b>{new_params.get('adx_min')}</b>\n"
            f"Fuerza mín: {old_params.get('min_strength')} → <b>{new_params.get('min_strength')}</b>"
        )

    def notify_blacklist(self, symbol: str, winrate: float, count: int):
        self.send(
            f"🚫 <b>PAR BLOQUEADO</b>\n{BAR}\n"
            f"Símbolo: <b>{symbol}</b>\n"
            f"WR: {winrate:.0f}% en {count} trades\n"
            f"El bot evitará este par temporalmente."
        )

    def notify_error(self, error: str):
        self.send(f"⚠️ <b>ERROR BOT V35</b>\n{BAR}\n<code>{error[:400]}</code>")
