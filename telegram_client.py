"""
Telegram notification client.

FIX: entry() ahora acepta stop=None sin crashear. main.py llama a
tg.entry(..., None, equity) siempre — este bot no calcula un stop
explícito antes de notificar. Con f"{stop:.6g}" sin protección, esto
llevaba crasheando en cada apertura exitosa desde siempre — el trade
y el SL/TP ya se habían colocado antes de esta línea, así que no se
perdía el trade, pero nunca llegaba el aviso de entrada a Telegram.
"""
import logging
import requests

log = logging.getLogger("telegram")


class TelegramClient:
    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self._url    = f"https://api.telegram.org/bot{token}/sendMessage"

    def _send(self, text: str):
        if not self.token or not self.chat_id:
            return
        try:
            requests.post(
                self._url,
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=8,
            )
        except Exception as e:
            log.warning(f"Telegram error: {e}")

    # ── Templates ─────────────────────────────────────────────

    def startup(self, bot: str, symbol: str, tf: str, lev: int):
        self._send(
            f"🤖 <b>{bot}</b> iniciado\n"
            f"Symbol: <code>{symbol}</code>  TF: {tf}  Lev: {lev}x"
        )

    def entry(self, bot: str, symbol: str, side: str, price: float, qty: float,
              stop: float, equity: float):
        icon = "🟢" if side == "LONG" else "🔴"
        stop_str = f"{stop:.6g}" if stop is not None else "N/A"   # FIX
        self._send(
            f"{icon} <b>{bot}</b> — ENTRADA {side}\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Price:  {price:.6g}\n"
            f"Qty:    {qty}\n"
            f"Stop:   {stop_str}\n"
            f"Equity: {equity:.2f} USDT"
        )

    def exit_trade(self, bot: str, symbol: str, side: str, price: float,
                   reason: str, pnl: float):
        icon = "✅" if pnl >= 0 else "❌"
        self._send(
            f"{icon} <b>{bot}</b> — SALIDA {side}\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Price:  {price:.6g}\n"
            f"Razón:  {reason}\n"
            f"PnL:    {pnl:+.2f} USDT"
        )

    def trail_update(self, bot: str, symbol: str, side: str,
                     price: float, stop: float):
        self._send(
            f"🔄 <b>{bot}</b> Trail Stop\n"
            f"<code>{symbol}</code> {side}\n"
            f"Price: {price:.6g}  Stop: {stop:.6g}"
        )

    def blocked(self, bot: str, reason: str):
        self._send(f"⛔ <b>{bot}</b> BLOQUEADO\n{reason}")

    def error(self, bot: str, message: str):
        self._send(f"⚠️ <b>{bot}</b> ERROR\n{message}")

    def info(self, bot: str, message: str):
        self._send(f"ℹ️ <b>{bot}</b>\n{message}")
