"""
notifier.py — Notificaciones por Telegram
"""

import requests
import config
from datetime import datetime


def _enviar(mensaje: str) -> bool:
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM] (sin configurar) {mensaje}")
        return False

    try:
        url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(url, json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": mensaje,
            "parse_mode": "HTML"
        }, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[TELEGRAM] Error: {e}")
        return False


def bot_iniciado(pares: list, balance: float):
    modo = "🔴 DEMO" if config.MODO_DEMO else "🟢 REAL"
    msg = (
        f"🤖 <b>BOT22 Arrancado</b> {modo}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{len(pares)} pares activos</b>\n"
        f"💰 Balance: <b>${balance:.2f}</b>\n"
        f"⚙️ RSI<{config.RSI_OVERSOLD} | BB σ{config.BB_STD} | "
        f"SL:{config.SL_ATR_MULT}×ATR | Lev:{config.LEVERAGE}x\n"
        f"🔄 Ciclo: cada {config.CICLO_SEGUNDOS}s | Max pos: {config.MAX_POSICIONES}\n"
        f"🛡️ Circuit Breaker: {config.MAX_PNL_NEGATIVO_DIA*100:.0f}% día | "
        f"{config.MAX_PERDIDAS_SEGUIDAS} pérdidas seguidas\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    _enviar(msg)


def trade_abierto(trade: dict):
    rr = trade.get("rr", 0)
    msg = (
        f"📈 <b>LONG ABIERTO</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 Par: <b>{trade['par']}</b>\n"
        f"💵 Entrada: ${trade['precio_entrada']:.4f}\n"
        f"🔴 SL: ${trade['sl']:.4f}\n"
        f"🟢 TP: ${trade['tp']:.4f}\n"
        f"📐 R:R: {rr:.2f}\n"
        f"📊 RSI: {trade.get('rsi', 0):.1f}\n"
        f"📦 Qty: {trade['cantidad']}\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )
    _enviar(msg)


def trade_cerrado(trade: dict, pnl: float, motivo: str, balance: float):
    emoji_resultado = "✅" if pnl > 0 else "❌"
    resultado = "WIN" if pnl > 0 else "LOSS"
    msg = (
        f"{emoji_resultado} <b>TRADE CERRADO — {resultado}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 Par: <b>{trade['par']}</b>\n"
        f"💵 Entrada: ${trade.get('precio_entrada', 0):.4f}\n"
        f"💵 Salida:  ${trade.get('precio_salida', 0):.4f}\n"
        f"💰 PnL: <b>${pnl:+.4f}</b>\n"
        f"📌 Motivo: {motivo}\n"
        f"🏦 Balance: ${balance:.2f}\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )
    _enviar(msg)


def circuit_breaker(motivo: str, balance: float):
    msg = (
        f"⛔ <b>CIRCUIT BREAKER ACTIVADO</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔎 Motivo: {motivo}\n"
        f"🏦 Balance actual: ${balance:.2f}\n"
        f"💤 Bot pausado hasta mañana o 1 hora\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )
    _enviar(msg)


def learner_ajuste(par: str, accion: str, motivo: str):
    emoji = "⚠️" if accion == "PENALIZAR" else "♻️"
    msg = (
        f"{emoji} <b>LEARNER — {accion}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 Par: {par}\n"
        f"📝 Motivo: {motivo}\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )
    _enviar(msg)


def resumen_diario(stats: dict):
    msg = (
        f"📊 <b>RESUMEN DIARIO</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔢 Trades: {stats.get('total', 0)} "
        f"(✅{stats.get('wins', 0)} / ❌{stats.get('losses', 0)})\n"
        f"💰 PnL: <b>${stats.get('pnl', 0):+.4f}</b>\n"
        f"📈 WR: {stats.get('wr', 0):.1f}%\n"
        f"⚖️ PF: {stats.get('pf', 0):.2f}\n"
        f"🏦 Balance: ${stats.get('balance', 0):.2f}\n"
        f"📅 {datetime.now().strftime('%Y-%m-%d')}"
    )
    _enviar(msg)


def error_critico(mensaje: str):
    msg = f"🚨 <b>ERROR CRÍTICO</b>\n{mensaje}\n🕐 {datetime.now().strftime('%H:%M:%S')}"
    _enviar(msg)
