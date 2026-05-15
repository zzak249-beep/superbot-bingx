"""telegram_bot.py — Notificaciones Telegram para Sniper Bot V49"""
import logging
import asyncio
import aiohttp
from datetime import datetime
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, SYMBOL, MODE

log = logging.getLogger("telegram")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


async def _send(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram no configurado — omitiendo mensaje.")
        return False
    url     = f"{BASE_URL}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                return r.status == 200
    except Exception as e:
        log.error(f"Error enviando Telegram: {e}")
        return False


def send(text: str) -> None:
    """Wrapper sincrónico."""
    asyncio.run(_send(text))


# ── Plantillas de mensajes ─────────────────────────────────────

def msg_start(symbol: str, timeframe: str, mode: str) -> str:
    emoji = "🟡" if mode == "paper" else "🟢"
    return (
        f"{emoji} <b>Sniper Bot V49 Iniciado</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Par:       <code>{symbol}</code>\n"
        f"⏱ Timeframe: <code>{timeframe}</code>\n"
        f"🔧 Modo:      <code>{mode.upper()}</code>\n"
        f"🕐 Hora:      <code>{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Motor: Markov 200v + ADX Adaptativo + STC + POC"
    )


def msg_signal(direction: str, ind: dict, price: float,
               qty: float, tp: float, sl: float, balance: float) -> str:
    arrow   = "🟢 LONG" if direction == "long" else "🔴 SHORT"
    regime  = "TENDENCIA" if ind["is_trending"] else ("RANGO" if ind["is_ranging"] else "TRANSICIÓN")
    rr      = round(abs(tp - price) / abs(sl - price), 2) if abs(sl - price) > 0 else 0
    mode_lbl = "🟡 PAPER" if MODE == "paper" else "💰 LIVE"
    return (
        f"{arrow} <b>SEÑAL DETECTADA</b> {mode_lbl}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Par:        <code>{SYMBOL}</code>\n"
        f"💵 Precio:     <code>{price:.4f}</code>\n"
        f"📦 Cantidad:   <code>{qty:.4f}</code>\n"
        f"🎯 TP:         <code>{tp:.4f}</code>\n"
        f"🛡 SL:         <code>{sl:.4f}</code>\n"
        f"⚖️ R:R:        <code>1 : {rr}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 <b>Indicadores</b>\n"
        f"  Slope:       <code>{ind['slope']:.1f}%</code> (umbral {ind['adaptive_slope']:.1f}%)\n"
        f"  ADX:         <code>{ind['adx']:.1f}</code>  [{regime}]\n"
        f"  Prob Bull:   <code>{ind['prob_bull']:.1f}%</code>\n"
        f"  Prob Bear:   <code>{ind['prob_bear']:.1f}%</code>\n"
        f"  RVOL:        <code>{ind['rvol']:.2f}x</code>\n"
        f"  STC:         <code>{ind['stc']:.1f}</code>\n"
        f"  VWAP:        <code>{ind['vwap']:.4f}</code>\n"
        f"  POC:         <code>{ind['poc']:.4f}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 Balance:    <code>${balance:.2f} USDT</code>\n"
        f"🕐 <code>{datetime.utcnow().strftime('%H:%M:%S')} UTC</code>"
    )


def msg_close(direction: str, entry: float, exit_price: float,
              qty: float, reason: str, balance: float) -> str:
    pnl     = (exit_price - entry) * qty if direction == "long" else (entry - exit_price) * qty
    pct     = (pnl / (entry * qty)) * 100 if entry * qty > 0 else 0
    emoji   = "✅" if pnl >= 0 else "❌"
    return (
        f"{emoji} <b>POSICIÓN CERRADA</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Par:        <code>{SYMBOL}</code>\n"
        f"↗️  Dirección:  <code>{'LONG' if direction == 'long' else 'SHORT'}</code>\n"
        f"🔵 Entrada:    <code>{entry:.4f}</code>\n"
        f"🔴 Salida:     <code>{exit_price:.4f}</code>\n"
        f"📦 Cantidad:   <code>{qty:.4f}</code>\n"
        f"💰 PnL:        <code>{'+' if pnl >= 0 else ''}{pnl:.4f} USDT ({pct:+.2f}%)</code>\n"
        f"📋 Motivo:     <code>{reason}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 Balance:    <code>${balance:.2f} USDT</code>\n"
        f"🕐 <code>{datetime.utcnow().strftime('%H:%M:%S')} UTC</code>"
    )


def msg_error(context: str, error: str) -> str:
    return (
        f"⚠️ <b>ERROR — Sniper Bot V49</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Contexto: <code>{context}</code>\n"
        f"❗ Error:    <code>{error}</code>\n"
        f"🕐 <code>{datetime.utcnow().strftime('%H:%M:%S')} UTC</code>"
    )


def msg_heartbeat(ind: dict, balance: float, trades_today: int) -> str:
    regime = "📈 TENDENCIA" if ind["is_trending"] else ("📉 RANGO" if ind["is_ranging"] else "↔️ TRANSICIÓN")
    return (
        f"💓 <b>Heartbeat — Sniper V49</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 {SYMBOL}  |  <code>{datetime.utcnow().strftime('%H:%M')} UTC</code>\n"
        f"💵 Precio:   <code>{ind['close']:.4f}</code>\n"
        f"📊 Régimen:  {regime} (ADX {ind['adx']:.1f})\n"
        f"🎯 ProbBull: <code>{ind['prob_bull']:.1f}%</code>  "
        f"ProbBear: <code>{ind['prob_bear']:.1f}%</code>\n"
        f"📦 RVOL:     <code>{ind['rvol']:.2f}x</code>  "
        f"STC: <code>{ind['stc']:.1f}</code>\n"
        f"💼 Balance:  <code>${balance:.2f} USDT</code>\n"
        f"🔢 Trades hoy: <code>{trades_today}</code>"
    )
