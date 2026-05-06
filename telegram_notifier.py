"""
Telegram Notifier — Conflux 4 Bot
Notificaciones ricas: apertura, cierre, P&L, dashboard
"""
import httpx
from loguru import logger
from datetime import datetime


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self._base   = f"https://api.telegram.org/bot{token}"
        self._client = httpx.Client(timeout=12)

    def _send(self, text: str) -> bool:
        if not self.token or not self.chat_id:
            return False
        # Intentar con HTML primero, luego sin formato
        for parse_mode in ("HTML", None):
            try:
                payload = {"chat_id": self.chat_id, "text": text,
                           "disable_web_page_preview": True}
                if parse_mode:
                    payload["parse_mode"] = parse_mode
                r = self._client.post(f"{self._base}/sendMessage", json=payload)
                if r.status_code == 200:
                    return True
                if r.status_code == 400 and parse_mode:
                    continue   # reintentar sin formato
                logger.warning(f"Telegram {r.status_code}: {r.text[:120]}")
                return False
            except Exception as e:
                logger.error(f"Telegram send: {e}")
                return False
        return False

    def get_bot_info(self) -> dict:
        try:
            r = self._client.get(f"{self._base}/getMe")
            data = r.json()
            if data.get("ok"):
                bot = data["result"]
                logger.info(f"Bot conectado: @{bot.get('username')}")
                return bot
            logger.error(f"Token inválido: {data}")
        except Exception as e:
            logger.error(f"get_bot_info: {e}")
        return {}

    def delete_webhook(self) -> bool:
        try:
            r = self._client.post(f"{self._base}/deleteWebhook",
                                  json={"drop_pending_updates": True})
            return r.json().get("ok", False)
        except Exception:
            return False

    def startup(self, symbols: list, interval: str, preset: str,
                balance: float, dynamic_scan: bool, top_n: int):
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        mode = f"TOP {top_n} dinámico" if dynamic_scan else f"{len(symbols)} fijos"
        self._send(
            f"🤖 <b>Conflux 4 Bot — Iniciado</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: <code>${balance:,.2f} USDT</code>\n"
            f"⏱ Timeframe: <code>{interval}</code> | Preset: <code>{preset}</code>\n"
            f"🔍 Pares: <code>{mode}</code>\n"
            f"🕐 {now}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>Trading automático ACTIVO</b>"
        )

    # ── Señal detectada (antes de ejecutar) ───────────────────────────────────

    def signal(self, result, symbol: str, interval: str, preset: str,
               risk_dec, quality: int):
        """Solo se llama cuando hay señal aprobada Y se va a tradear."""
        direction = "LONG 📈" if result.signal == "BULL" else "SHORT 📉"
        emoji     = "🟢" if result.signal == "BULL" else "🔴"
        stars     = "⭐" * min(quality, 5)
        sl_pct    = abs(result.entry - result.stop)  / result.entry * 100
        tp_pct    = abs(result.tp2  - result.entry)  / result.entry * 100
        self._send(
            f"{emoji} <b>SEÑAL — {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 {direction} | {interval} | {preset}\n"
            f"⭐ Calidad: {quality}/10  {stars}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Entrada: <code>${result.entry:,.4f}</code>\n"
            f"🛑 SL: <code>${result.stop:,.4f}</code> (-{sl_pct:.2f}%)\n"
            f"🎯 TP: <code>${result.tp2:,.4f}</code> (+{tp_pct:.2f}%)\n"
            f"📊 R:R: <code>1:{tp_pct/sl_pct:.1f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 RSI: {result.rsi_val:.0f} | ADX: {result.adx_val:.0f} | ATR: {result.atr_pct:.2f}%"
        )

    # ── Trade ABIERTO (orden ejecutada en BingX) ──────────────────────────────

    def trade_opened(self, symbol: str, direction: str, entry: float,
                     stop: float, tp2: float, quantity: float,
                     position_usdt: float, leverage: int, quality: int):
        """Notificación confirmada cuando la orden se ejecutó en BingX."""
        emoji    = "🟢" if direction == "BULL" else "🔴"
        side_txt = "LONG 📈" if direction == "BULL" else "SHORT 📉"
        sl_pct   = abs(entry - stop) / entry * 100
        tp_pct   = abs(tp2  - entry) / entry * 100
        rr       = tp_pct / sl_pct if sl_pct > 0 else 0
        exp_gain = position_usdt * leverage * (tp_pct / 100)
        exp_loss = position_usdt * leverage * (sl_pct / 100)
        self._send(
            f"{emoji} <b>✅ TRADE ABIERTO — {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 {side_txt}  |  Leverage: {leverage}x\n"
            f"⭐ Calidad: {quality}/10\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 <b>Entrada:</b> <code>${entry:,.4f}</code>\n"
            f"📊 Cantidad: <code>{quantity}</code>\n"
            f"💰 Posición: <code>${position_usdt * leverage:,.0f} USDT</code> ({position_usdt:.0f} margin)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🛑 <b>Stop Loss:</b> <code>${stop:,.4f}</code>  (-{sl_pct:.2f}%)\n"
            f"🎯 <b>Take Profit:</b> <code>${tp2:,.4f}</code>  (+{tp_pct:.2f}%)\n"
            f"📊 <b>R:R:</b> 1:{rr:.1f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💸 Ganancia potencial: <code>+${exp_gain:.2f} USDT</code>\n"
            f"⚠️ Riesgo máximo: <code>-${exp_loss:.2f} USDT</code>\n"
            f"🕐 {datetime.utcnow().strftime('%H:%M UTC')}"
        )

    # ── Trade CERRADO (TP / SL / trailing) ───────────────────────────────────

    def trade_closed(self, symbol: str, direction: str, entry: float,
                     exit_price: float, pnl_usdt: float, reason: str):
        won   = pnl_usdt >= 0
        emoji = "✅" if won else "❌"
        side  = "LONG" if direction == "BULL" else "SHORT"
        pct   = (exit_price - entry) / entry * 100 if direction == "BULL" \
                else (entry - exit_price) / entry * 100
        result_txt = "GANANCIA 🎉" if won else "PÉRDIDA 😔"
        self._send(
            f"{emoji} <b>TRADE CERRADO — {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 {side} | Razón: <b>{reason}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📥 Entrada: <code>${entry:,.4f}</code>\n"
            f"📤 Salida:  <code>${exit_price:,.4f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>P&L: <code>{pnl_usdt:+.2f} USDT</code>  ({pct:+.2f}%)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{result_txt}\n"
            f"🕐 {datetime.utcnow().strftime('%H:%M UTC')}"
        )

    # ── Updates / dashboard ───────────────────────────────────────────────────

    def trade_update(self, symbol: str, events: list, price: float, pnl_approx: float):
        if not events:
            return
        ev_txt = " | ".join(events)
        emoji  = "🟢" if pnl_approx >= 0 else "🔴"
        self._send(
            f"{emoji} <b>UPDATE — {symbol}</b>\n"
            f"Evento: {ev_txt}\n"
            f"Precio: <code>${price:,.4f}</code>\n"
            f"P&L aprox: <code>{pnl_approx:+.2f} USDT</code>"
        )

    def performance_dashboard(self, summary: dict, last_results: dict):
        wr    = summary.get("winrate_pct", 50.0)
        bal   = summary.get("balance", 0)
        pnl_d = summary.get("today_pnl", 0)
        pnl_t = summary.get("total_pnl", 0)
        dd    = summary.get("drawdown_pct", 0)
        wins  = summary.get("today_wins", 0)
        losses= summary.get("today_losses", 0)
        open_ = summary.get("open_positions", 0)

        # Top señales del último scan
        signals = [(s, r) for s, r in last_results.items() if r.signal]
        top = ""
        for sym, r in signals[:5]:
            d = "📈" if r.signal == "BULL" else "📉"
            top += f"  {d} <code>{sym}</code> RSI {r.rsi_val:.0f} ADX {r.adx_val:.0f} Q{r.quality}\n"

        bar_wr = "█" * int(wr / 10) + "░" * (10 - int(wr / 10))
        self._send(
            f"📊 <b>DASHBOARD — Conflux 4</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: <code>${bal:,.2f} USDT</code>\n"
            f"📈 P&L hoy: <code>{pnl_d:+.2f} USDT</code>\n"
            f"📊 P&L total: <code>{pnl_t:+.2f} USDT</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 Win rate: {wr:.0f}%  [{bar_wr}]\n"
            f"✅ Wins: {wins}  ❌ Losses: {losses}\n"
            f"📉 Drawdown: {dd:.1f}%\n"
            f"🔓 Posiciones abiertas: {open_}\n"
            + (f"━━━━━━━━━━━━━━━━━━━━\n<b>Señales activas:</b>\n{top}" if top else "") +
            f"🕐 {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
        )

    def symbols_updated(self, symbols: list, min_vol: float, top_n: int):
        self._send(
            f"🔄 <b>Pares actualizados</b>\n"
            f"Top {top_n} pares (vol ≥ ${min_vol/1e6:.0f}M)\n"
            f"Total: <code>{len(symbols)}</code> pares activos"
        )

    def risk_alert(self, title: str, detail: str):
        self._send(f"⚠️ <b>ALERTA — {title}</b>\n{detail}")

    def error(self, msg: str):
        self._send(f"🚨 <b>ERROR</b>\n<code>{msg[:300]}</code>")
