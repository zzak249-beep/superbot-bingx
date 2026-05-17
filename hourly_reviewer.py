"""
hourly_reviewer.py — Revisión de rentabilidad cada hora.
Analiza los trades de la sesión actual, detecta patrones de pérdida,
y envía un informe estructurado a Telegram con recomendaciones.
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict

logger = logging.getLogger(__name__)


class HourlyReviewer:
    def __init__(self, learning_engine, telegram, client):
        self.learning  = learning_engine
        self.telegram  = telegram
        self.client    = client
        self._session_start = datetime.now(timezone.utc)

    # ──────────────────────────────────────────────────────
    # Informe horario principal
    # ──────────────────────────────────────────────────────
    def run(self, active_trades: dict):
        """Genera y envía el informe horario completo."""
        now      = datetime.now(timezone.utc)
        trades   = self.learning.trades
        balance  = self.client.get_balance()

        # Trades de la última hora
        cutoff_1h = now - timedelta(hours=1)
        last_hour = [
            t for t in trades
            if self._parse_ts(t["ts"]) >= cutoff_1h
        ]

        # Trades del día
        today     = now.date().isoformat()
        today_trades = [t for t in trades if t["ts"].startswith(today)]

        # Métricas
        m1h   = self._metrics(last_hour,    "1h")
        m1d   = self._metrics(today_trades, "hoy")
        m_all = self._metrics(trades,       "total")

        # Análisis cualitativo
        alert    = self._alert_level(m1h, m1d)
        advice   = self._build_advice(m1h, m1d, m_all)
        open_txt = self._open_positions_text(active_trades)

        msg = self._format_message(
            now, balance, m1h, m1d, m_all,
            alert, advice, open_txt,
        )
        self.telegram.send(msg)
        logger.info(f"Informe horario enviado. WR1h={m1h['wr']:.0f}% PnL1h={m1h['pnl']:+.4f}")

    # ──────────────────────────────────────────────────────
    # Métricas
    # ──────────────────────────────────────────────────────
    def _metrics(self, trades: List[Dict], label: str) -> dict:
        if not trades:
            return {
                "label": label, "total": 0, "wins": 0, "losses": 0,
                "wr": 0.0, "pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "best": 0.0, "worst": 0.0, "avg_dur": 0.0,
                "by_direction": {}, "time_stop_count": 0,
            }

        wins   = [t for t in trades if t["won"]]
        losses = [t for t in trades if not t["won"]]
        pnls   = [t.get("pnl", 0) for t in trades]

        # Por dirección
        longs  = [t for t in trades if t.get("direction") == "LONG"]
        shorts = [t for t in trades if t.get("direction") == "SHORT"]
        by_dir = {
            "LONG":  self._mini(longs),
            "SHORT": self._mini(shorts),
        }

        # Time-stops
        ts_count = sum(1 for t in trades if t.get("reason") == "TIME_STOP")

        return {
            "label":           label,
            "total":           len(trades),
            "wins":            len(wins),
            "losses":          len(losses),
            "wr":              len(wins) / len(trades) * 100 if trades else 0,
            "pnl":             sum(pnls),
            "avg_win":         sum(t.get("pnl",0) for t in wins)  / len(wins)  if wins   else 0,
            "avg_loss":        sum(t.get("pnl",0) for t in losses) / len(losses)if losses else 0,
            "best":            max(pnls) if pnls else 0,
            "worst":           min(pnls) if pnls else 0,
            "avg_dur":         sum(t.get("duration_min",0) for t in trades) / len(trades),
            "by_direction":    by_dir,
            "time_stop_count": ts_count,
        }

    @staticmethod
    def _mini(trades: List[Dict]) -> dict:
        if not trades:
            return {"total": 0, "wins": 0, "wr": 0.0, "pnl": 0.0}
        wins = [t for t in trades if t["won"]]
        return {
            "total": len(trades),
            "wins":  len(wins),
            "wr":    len(wins) / len(trades) * 100,
            "pnl":   sum(t.get("pnl", 0) for t in trades),
        }

    # ──────────────────────────────────────────────────────
    # Nivel de alerta
    # ──────────────────────────────────────────────────────
    def _alert_level(self, m1h: dict, m1d: dict) -> str:
        """🟢 BIEN | 🟡 ATENCIÓN | 🔴 ALERTA"""
        if m1h["total"] == 0:
            return "⚪ SIN ACTIVIDAD"
        if m1h["wr"] >= 55 and m1h["pnl"] > 0:
            return "🟢 BIEN"
        if m1h["wr"] >= 40 or (m1h["total"] < 3 and m1h["pnl"] >= 0):
            return "🟡 ATENCIÓN"
        return "🔴 ALERTA"

    # ──────────────────────────────────────────────────────
    # Consejos automáticos
    # ──────────────────────────────────────────────────────
    def _build_advice(self, m1h, m1d, m_all) -> List[str]:
        tips = []

        # Demasiados time-stops → mercado lateral
        if m1h["time_stop_count"] >= 2:
            tips.append("⏱️ Muchos TIME-STOP: mercado lateral. Bot ajustará filtros.")

        # WR bajo en la última hora
        if m1h["total"] >= 3 and m1h["wr"] < 35:
            tips.append("⚠️ WR <35% última hora → motor de aprendizaje subiendo ADX.")

        # Dirección con peor rendimiento
        for direction, dm in m1h["by_direction"].items():
            if dm["total"] >= 2 and dm["wr"] < 30:
                tips.append(f"📉 {direction}s fallando ({dm['wr']:.0f}% WR). Revisar tendencia macro.")

        # PnL negativo en el día pero positivo en la hora
        if m1d["pnl"] < 0 and m1h["pnl"] > 0:
            tips.append("🔄 Recuperación en curso. Mantener parámetros actuales.")

        # Racha perdedora seguida
        recent = self.learning.trades[-5:] if self.learning.trades else []
        consecutive_losses = 0
        for t in reversed(recent):
            if not t["won"]:
                consecutive_losses += 1
            else:
                break
        if consecutive_losses >= 3:
            tips.append(f"🚨 {consecutive_losses} pérdidas seguidas → motor de aprendizaje activado.")

        # Sin señales en la última hora
        if m1h["total"] == 0 and m1d["total"] > 0:
            tips.append("😴 Sin operaciones en la última hora. Mercado sin impulso.")

        if not tips:
            tips.append("✅ Comportamiento normal. Sin anomalías detectadas.")

        return tips

    # ──────────────────────────────────────────────────────
    # Posiciones abiertas
    # ──────────────────────────────────────────────────────
    def _open_positions_text(self, active_trades: dict) -> str:
        if not active_trades:
            return "  Sin posiciones abiertas"
        lines = []
        for sym, t in active_trades.items():
            sig  = t["signal"]
            dur  = (datetime.now(timezone.utc) - t["open_time"]).total_seconds() / 60
            sign = "🟢" if sig["signal"] == "LONG" else "🔴"
            lines.append(
                f"  {sign} <b>{sym}</b> | entrada ${sig['entry']:.5f} | "
                f"{dur:.0f}min | vela {t['candle_count']}/15"
            )
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────
    # Formato del mensaje
    # ──────────────────────────────────────────────────────
    def _format_message(
        self, now, balance,
        m1h, m1d, m_all,
        alert, advice, open_txt,
    ) -> str:
        BAR  = "━━━━━━━━━━━━━━━━"
        pnl_sign = lambda x: f"+{x:.4f}" if x >= 0 else f"{x:.4f}"
        wr_color = lambda w: "🟢" if w >= 55 else ("🟡" if w >= 40 else "🔴")

        adv_text = "\n".join(f"  {a}" for a in advice)

        # Dirección breakdown (última hora)
        lm = m1h["by_direction"].get("LONG",  {"total":0,"wr":0,"pnl":0})
        sm = m1h["by_direction"].get("SHORT", {"total":0,"wr":0,"pnl":0})

        return (
            f"📊 <b>REVISIÓN HORARIA V35</b>  {alert}\n"
            f"{now.strftime('%H:%M UTC')} — {BAR}\n\n"

            f"💰 <b>Balance actual:</b> ${balance:,.4f} USDT\n\n"

            f"⏱️ <b>ÚLTIMA HORA</b>\n"
            f"  Trades: {m1h['total']}  |  "
            f"✅{m1h['wins']} ❌{m1h['losses']}\n"
            f"  WR: {wr_color(m1h['wr'])} <b>{m1h['wr']:.0f}%</b>  |  "
            f"PnL: <b>{pnl_sign(m1h['pnl'])} USDT</b>\n"
            f"  Avg ganadora: +{m1h['avg_win']:.4f}  |  "
            f"Avg perdedora: {m1h['avg_loss']:.4f}\n"
            f"  Long  {lm['total']}ops {lm['wr']:.0f}%WR  |  "
            f"Short {sm['total']}ops {sm['wr']:.0f}%WR\n"
            f"  Time-Stops: {m1h['time_stop_count']}\n\n"

            f"📅 <b>HOY</b>\n"
            f"  Trades: {m1d['total']}  |  WR: {m1d['wr']:.0f}%  |  "
            f"PnL: <b>{pnl_sign(m1d['pnl'])} USDT</b>\n"
            f"  Mejor: +{m1d['best']:.4f}  |  Peor: {m1d['worst']:.4f}\n"
            f"  Dur. media: {m1d['avg_dur']:.0f} min\n\n"

            f"📈 <b>HISTÓRICO TOTAL</b>\n"
            f"  {m_all['total']} trades  |  WR: {m_all['wr']:.0f}%  |  "
            f"PnL: <b>{pnl_sign(m_all['pnl'])} USDT</b>\n\n"

            f"🤖 <b>PARÁMETROS ACTIVOS</b>\n"
            f"  ADX≥{self.learning.params['adx_min']:.0f}  |  "
            f"Fuerza≥{self.learning.params['min_strength']:.0f}%  |  "
            f"Ajustes: {len(self.learning.adjustments)}\n\n"

            f"📌 <b>POSICIONES ABIERTAS</b>\n{open_txt}\n\n"

            f"🧠 <b>DIAGNÓSTICO</b>\n{adv_text}"
        )

    # ──────────────────────────────────────────────────────
    # Util
    # ──────────────────────────────────────────────────────
    @staticmethod
    def _parse_ts(ts_str: str) -> datetime:
        try:
            return datetime.fromisoformat(ts_str)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)
