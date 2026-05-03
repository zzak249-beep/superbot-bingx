"""
TELEGRAM NOTIFIER ULTRA — Con tracking de trades reales
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NUEVAS FUNCIONES:
  ✓ Notificación de ENTRADA real con todos los detalles
  ✓ Notificación de SALIDA real con P&L exacto
  ✓ Updates en tiempo real de posiciones activas
  ✓ Resumen diario de performance
  ✓ Alertas de riesgo (drawdown, pérdidas)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import httpx
from loguru import logger
from datetime import datetime


class TelegramNotifierUltra:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base = f"https://api.telegram.org/bot{token}"

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        try:
            r = httpx.post(
                f"{self.base}/sendMessage",
                json={"chat_id": self.chat_id, "text": text,
                      "parse_mode": parse_mode, "disable_web_page_preview": True},
                timeout=12,
            )
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False

    # ═══════════════════════════════════════════════════════════════
    # NOTIFICACIONES DE TRADES REALES
    # ═══════════════════════════════════════════════════════════════
    
    def trade_opened_real(
        self,
        symbol: str,
        side: str,  # BUY | SELL
        entry_price: float,
        quantity: float,
        stop_loss: float,
        take_profit: float,
        usdt_used: float,
        leverage: int = 10,
        quality: int = 0,
    ):
        """Notifica ENTRADA real en BingX."""
        
        emoji = "🟢" if side == "BUY" else "🔴"
        direction = "LONG 📈" if side == "BUY" else "SHORT 📉"
        
        sl_dist_pct = abs(entry_price - stop_loss) / entry_price * 100
        tp_dist_pct = abs(take_profit - entry_price) / entry_price * 100
        rr_ratio = tp_dist_pct / sl_dist_pct if sl_dist_pct > 0 else 0
        
        position_value = usdt_used * leverage
        
        msg = (
            f"{emoji} <b>ENTRADA REAL — {symbol}</b> {emoji}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 {direction}  |  Leverage: {leverage}x\n"
            f"⭐ Calidad señal: {quality}/10\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 <b>ENTRADA:</b> <code>${entry_price:,.4f}</code>\n"
            f"📊 Cantidad: <code>{quantity:.3f}</code>\n"
            f"💰 Valor posición: <code>${position_value:,.2f}</code>\n"
            f"💸 USDT usado: <code>${usdt_used:,.2f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🛑 <b>STOP LOSS:</b> <code>${stop_loss:,.4f}</code>\n"
            f"   Distancia: {sl_dist_pct:.2f}%\n"
            f"🎯 <b>TAKE PROFIT:</b> <code>${take_profit:,.4f}</code>\n"
            f"   Distancia: {tp_dist_pct:.2f}%\n"
            f"📊 <b>R:R:</b> {rr_ratio:.1f}×\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>POSICIÓN ABIERTA EN BINGX</b>"
        )
        
        self.send(msg)
        logger.success(f"✅ Notificación ENTRADA enviada: {symbol} {direction}")

    def trade_closed_real(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
        pnl_usdt: float,
        pnl_pct: float,
        reason: str,  # TP | SL | Manual
        duration_hours: float = 0,
    ):
        """Notifica SALIDA real de BingX."""
        
        won = pnl_usdt > 0
        emoji = "✅" if won else "❌"
        
        msg = (
            f"{emoji} <b>SALIDA REAL — {symbol}</b> {emoji}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 {'LONG' if side == 'BUY' else 'SHORT'}  |  Razón: {reason}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📥 <b>ENTRADA:</b> <code>${entry_price:,.4f}</code>\n"
            f"📤 <b>SALIDA:</b> <code>${exit_price:,.4f}</code>\n"
            f"📊 Cantidad: <code>{quantity:.3f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>P&L REAL:</b> <code>{pnl_usdt:+,.2f} USDT</code>\n"
            f"📈 <b>Return:</b> <code>{pnl_pct:+.2f}%</code>\n"
            f"⏱ Duración: {duration_hours:.1f}h\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{'🎉 GANANCIA' if won else '😔 PÉRDIDA'}"
        )
        
        self.send(msg)
        logger.success(f"✅ Notificación SALIDA enviada: {symbol} P&L={pnl_usdt:+.2f}")

    def position_update_real(
        self,
        symbol: str,
        current_price: float,
        entry_price: float,
        quantity: float,
        unrealized_pnl: float,
        unrealized_pct: float,
    ):
        """Update de posición activa."""
        
        emoji = "🟢" if unrealized_pnl > 0 else "🔴"
        
        msg = (
            f"{emoji} <b>UPDATE — {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Precio actual: <code>${current_price:,.4f}</code>\n"
            f"📥 Entrada: <code>${entry_price:,.4f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 P&L no realizado: <code>{unrealized_pnl:+,.2f} USDT</code>\n"
            f"📊 Return: <code>{unrealized_pct:+.2f}%</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}"
        )
        
        self.send(msg)

    # ═══════════════════════════════════════════════════════════════
    # RESÚMENES Y ALERTAS
    # ═══════════════════════════════════════════════════════════════
    
    def daily_summary(
        self,
        total_trades: int,
        winners: int,
        losers: int,
        total_pnl: float,
        win_rate: float,
        balance: float,
        best_trade: float,
        worst_trade: float,
    ):
        """Resumen diario de performance."""
        
        msg = (
            f"📊 <b>RESUMEN DIARIO</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: <code>${balance:,.2f}</code>\n"
            f"📈 P&L del día: <code>{total_pnl:+,.2f} USDT</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 Trades: {total_trades}\n"
            f"   ✅ Ganados: {winners}\n"
            f"   ❌ Perdidos: {losers}\n"
            f"   📊 Win rate: {win_rate:.1f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏆 Mejor trade: <code>+${best_trade:,.2f}</code>\n"
            f"💔 Peor trade: <code>${worst_trade:,.2f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d')}"
        )
        
        self.send(msg)

    def risk_alert(self, title: str, detail: str):
        msg = f"⚠️ <b>ALERTA DE RIESGO — {title}</b>\n{detail}"
        self.send(msg)

    def error(self, msg: str):
        self.send(f"🚨 <b>ERROR</b>\n<code>{msg[:300]}</code>")

    def startup(self, balance: float, mode: str = "TESTNET"):
        msg = (
            f"🚀 <b>BOT ULTRA RENTABLE v4 — Iniciado</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: <code>${balance:,.2f}</code>\n"
            f"🔧 Modo: <b>{mode}</b>\n"
            f"📊 Leverage: 10x\n"
            f"🛡 SL/TP: Automático\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Sistema activo y monitoreando"
        )
        self.send(msg)

    def get_bot_info(self) -> dict:
        try:
            r = httpx.get(f"{self.base}/getMe", timeout=10)
            data = r.json()
            if data.get("ok"):
                bot = data["result"]
                logger.info(f"Bot conectado: @{bot.get('username')} (id={bot.get('id')})")
                return bot
            logger.error(f"Token inválido: {data}")
            return {}
        except Exception as e:
            logger.error(f"Error conectando a Telegram: {e}")
            return {}

    def delete_webhook(self) -> bool:
        try:
            r = httpx.post(
                f"{self.base}/deleteWebhook",
                json={"drop_pending_updates": True},
                timeout=10,
            )
            data = r.json()
            if data.get("ok"):
                logger.info("✅ Webhook eliminado correctamente")
                return True
            return False
        except Exception:
            return False
