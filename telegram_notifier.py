"""
Telegram Notifier v3 — Conflux 4 Bot
Mejoras v3:
  - Señales incluyen MACD, BB, CVD, HMA y régimen de mercado
  - Dashboard muestra top señales del scan
  - Notificación al rotar lista de símbolos
"""

import httpx
from loguru import logger


class TelegramNotifier:
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

    # ── Señal principal v3 ────────────────────────────────────────────────
    def signal(self, result, symbol: str, interval: str, preset: str,
               risk_dec=None, quality: int = 0) -> str:
        sig = result.signal
        is_bull = sig == "BULL"
        emoji = "🟢" if is_bull else "🔴"
        stars = "⭐" * min(quality, 5) + "☆" * max(0, 5 - quality)

        def fmt(v):
            if v is None: return "—"
            return f"{v:,.2f}" if v >= 1000 else f"{v:.5f}"

        risk_txt = ""
        if risk_dec:
            risk_txt = (
                f"\n━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 <b>Gestión de riesgo</b>\n"
                f"  Posición: <code>{risk_dec.position_usdt:.1f} USDT</code>\n"
                f"  Riesgo: <code>{risk_dec.risk_pct:.2f}%</code> del capital\n"
                f"  Kelly: <code>{risk_dec.Kelly_fraction*100:.2f}%</code>"
            )

        sl_dist = abs(result.entry - result.stop)
        rr_tp2 = abs(result.tp2 - result.entry) / sl_dist if sl_dist > 0 else 0
        rr_tp4 = abs(result.tp4 - result.entry) / sl_dist if sl_dist > 0 else 0

        # v3: indicadores adicionales
        macd_e  = "✅" if result.macd_bull else "❌"
        cvd_e   = "✅" if result.cvd_bull else "❌"
        hma_e   = "✅" if result.hma_bull else "❌"
        regime_e = "📈 Trend" if result.regime_trend else "↔️ Range"
        bb_txt  = {"low": "🔵 zona baja", "mid": "⚪ zona media", "high": "🔴 zona alta"}.get(result.bb_position, "")

        msg = (
            f"{emoji}<b>CONFLUX 4 — {sig} SIGNAL</b>{emoji}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>{symbol}</b>  |  {interval}  |  {preset}\n"
            f"📌 {'LONG 📈' if is_bull else 'SHORT 📉'}  |  Calidad: {stars} ({quality}/10)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Entrada:         <code>{fmt(result.entry)}</code>\n"
            f"🛑 Stop Loss:       <code>{fmt(result.stop)}</code> ({sl_dist/result.entry*100:.2f}%)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 TP1 — 25%: <code>{fmt(result.tp1)}</code>\n"
            f"🎯 TP2 — 25%: <code>{fmt(result.tp2)}</code> (RR {rr_tp2:.1f}×)\n"
            f"🎯 TP3 — 25%: <code>{fmt(result.tp3)}</code>\n"
            f"🎯 TP4 — 25%: <code>{fmt(result.tp4)}</code> (RR {rr_tp4:.1f}×)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 RSI: {result.rsi_val:.1f}  ADX: {result.adx_val:.1f}  Conf: {result.confluence}/4\n"
            f"📊 MACD: {macd_e}  CVD: {cvd_e}  HMA: {hma_e}\n"
            f"🎯 BB: {bb_txt}  |  Régimen: {regime_e}\n"
            f"📊 Vol%ile: {result.volume_pct:.0f}  "
            f"MTF: {'✅' if result.mtf_ok else '❌'}  "
            f"Funding: {'✅' if result.funding_ok else '❌'}"
            f"{risk_txt}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ <i>No es consejo financiero. Opera bajo tu propio riesgo.</i>"
        )
        self.send(msg)
        return msg

    # ── Notificación de rotación de símbolos ──────────────────────────────
    def symbols_updated(self, symbols: list, min_vol: float, top_n: int):
        syms_txt = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(symbols[:15]))
        extra = f"\n  ... y {len(symbols)-15} más" if len(symbols) > 15 else ""
        msg = (
            f"🔄 <b>Lista de pares actualizada</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📡 Escaneando top {top_n} pares por volumen\n"
            f"💧 Vol mínimo: {min_vol/1e6:.1f}M USDT/24h\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{syms_txt}{extra}"
        )
        self.send(msg)

    # ── Actualización de trade activo ─────────────────────────────────────
    def trade_update(self, symbol: str, events: list, price: float, pnl_approx: float = None):
        if not events:
            return
        ev_txt = "\n".join(f"  {e}" for e in events)
        pnl_txt = f"\n💹 P&amp;L aprox: <code>{pnl_approx:+.2f} USDT</code>" if pnl_approx is not None else ""
        msg = (
            f"📍 <b>Trade Update — {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{ev_txt}\n"
            f"💵 Precio actual: <code>{price:.4f}</code>"
            f"{pnl_txt}"
        )
        self.send(msg)

    # ── Cierre de posición ────────────────────────────────────────────────
    def trade_closed(self, symbol: str, direction: str, entry: float,
                     exit_price: float, pnl_usdt: float, reason: str):
        won = pnl_usdt > 0
        emoji = "✅" if won else "❌"
        pct = (exit_price - entry) / entry * 100
        pct = pct if direction == "BULL" else -pct
        msg = (
            f"{emoji} <b>TRADE CERRADO — {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 {direction}  |  Razón: {reason}\n"
            f"📥 Entrada: <code>{entry:.4f}</code>\n"
            f"📤 Salida:  <code>{exit_price:.4f}</code>  ({pct:+.2f}%)\n"
            f"💰 P&amp;L: <code>{pnl_usdt:+.2f} USDT</code>"
        )
        self.send(msg)

    # ── Alerta de riesgo ──────────────────────────────────────────────────
    def risk_alert(self, title: str, detail: str):
        msg = f"⚠️ <b>ALERTA DE RIESGO — {title}</b>\n{detail}"
        self.send(msg)

    # ── Dashboard de performance ──────────────────────────────────────────
    def performance_dashboard(self, risk_summary: dict, symbol_results: dict):
        s = risk_summary
        wr_pct = s.get("winrate", 0) * 100

        def bars(v, mx):
            if mx <= 0: return "░" * 10
            filled = int(v / mx * 10)
            return "█" * filled + "░" * (10 - filled)

        # Top señales: ordenar por calidad desc
        sorted_results = sorted(
            symbol_results.items(),
            key=lambda x: x[1].quality,
            reverse=True
        )

        pairs_txt = ""
        for sym, res in sorted_results[:10]:  # máx 10 en dashboard
            trend_e = {"BULL": "🟢", "BEAR": "🔴", "NEUTRAL": "⚪"}.get(res.trend, "⚪")
            sig_e = f" ⚡Q{res.quality}" if res.signal else ""
            pairs_txt += f"\n  {trend_e} <b>{sym}</b>: {res.trend} | RSI {res.rsi_val:.0f} | ADX {res.adx_val:.0f}{sig_e}"

        total_scanned = len(symbol_results)
        msg = (
            f"📊 <b>CONFLUX 4 — Performance Report</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: <code>{s['balance']:.2f} USDT</code>\n"
            f"📈 P&amp;L Total: <code>{s['total_pnl']:+.2f} USDT</code>\n"
            f"📅 Hoy: <code>{s['today_pnl']:+.2f} USDT</code>\n"
            f"📆 Semana: <code>{s['week_pnl']:+.2f} USDT</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 Winrate: <code>{wr_pct:.1f}%</code>  [{bars(wr_pct, 100)}]\n"
            f"📉 Drawdown: <code>{s['drawdown_pct']:.2f}%</code>\n"
            f"🔢 Trades totales: {s['all_trades']}\n"
            f"🔓 Posiciones abiertas: {s['open_positions']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Top mercados ({total_scanned} pares escaneados):</b>{pairs_txt}"
        )
        self.send(msg)

    def signal_rejected(self, symbol: str, reason: str):
        msg = f"🚫 <b>Señal rechazada [{symbol}]</b>\n<i>{reason}</i>"
        self.send(msg)

    def startup(self, symbols: list, interval: str, preset: str, balance: float,
                dynamic_scan: bool = True, top_n: int = 50):
        mode_txt = (
            f"🔍 Modo: <b>Scanner dinámico</b> (top {top_n} pares por volumen)"
            if dynamic_scan else
            f"📌 Modo: <b>Lista fija</b> ({len(symbols)} pares)"
        )
        syms_preview = "  " + "\n  ".join(f"• {s}" for s in symbols[:10])
        extra = f"\n  ... y {len(symbols)-10} más" if len(symbols) > 10 else ""
        msg = (
            f"🚀 <b>Conflux 4 Bot v3 — Iniciado</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: <code>{balance:.2f} USDT</code>\n"
            f"⏱ Intervalo: {interval}  |  Preset: {preset}\n"
            f"{mode_txt}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Pares iniciales:</b>\n{syms_preview}{extra}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Motor Conflux 4 v3 activo\n"
            f"🛡 Risk Manager activo\n"
            f"📊 Multi-timeframe: activado\n"
            f"💸 Funding rate filter: activado\n"
            f"📈 MACD + BB + CVD + HMA: activado\n"
            f"🌐 Régimen de mercado: activado"
        )
        self.send(msg)

    def error(self, msg: str):
        self.send(f"🚨 <b>ERROR</b>\n<code>{msg[:300]}</code>")

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
            else:
                logger.warning(f"deleteWebhook response: {data}")
                return False
        except Exception as e:
            logger.warning(f"No se pudo eliminar webhook: {e}")
            return False

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
