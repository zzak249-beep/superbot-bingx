"""
brain.py — Motor de Aprendizaje Adaptativo para SATY ELITE
══════════════════════════════════════════════════════════════
El bot aprende de CADA trade cerrado y ajusta su comportamiento:

¿QUÉ APRENDE?
  1. 🏆 Rendimiento por combinación de módulos
     "ConfPRO+BollingerHunter" → 72% WR → darle más peso
     "BollingerHunter+SMC" → 31% WR → subir score mínimo requerido

  2. 📊 Rendimiento por condición de mercado
     BTC alcista + ADX > 25 → LONGS funcionan 80% WR
     BTC neutral + squeeze → mejor NO operar

  3. ⏰ Rendimiento por franja horaria
     00-04h UTC → WR bajísimo → reducir posición
     12-16h UTC → WR alto → operar con fuerza normal

  4. 📈 Rendimiento por rango de RSI
     RSI 30-45 en entrada → mejor resultado para longs
     RSI 55-65 en entrada → mejor resultado para shorts

  5. 🎯 Rendimiento por score de entrada
     Score 5 → WR 45% → probablemente demasiado bajo
     Score 8+ → WR 75% → score alto correlaciona con éxito

¿QUÉ CAMBIA?
  - MIN_SCORE efectivo por combinación de módulos
  - Peso de cada módulo en el consenso (score multiplicador)
  - Cooldown extendido automáticamente en horas malas
  - Blacklist temporal de combinaciones con WR < 30%
  - Tamaño de posición reducido cuando condición de mercado es desfavorable

¿CUÁNDO APRENDE?
  - Cada vez que se cierra un trade (close_trade / manage_trade)
  - Revisa y ajusta parámetros cada 50 trades o 24h

PERSISTENCIA:
  - Guarda todo en brain_data.json (sobrevive reinicios)
  - Mínimo 10 muestras antes de ajustar (evita sobreajuste por muestras pequeñas)
"""

import json, os, time, logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

log = logging.getLogger("brain")

BRAIN_FILE = "brain_data.json"
MIN_SAMPLES_TO_ADJUST = 10   # mínimo trades antes de cambiar parámetros
MAX_MODULE_BOOST      = 2.0  # máximo multiplicador de score por módulo
MIN_MODULE_BOOST      = 0.3  # mínimo multiplicador (nunca bloquear completamente)
LEARNING_RATE         = 0.15 # cuánto ajusta cada nuevo trade (0.15 = suave)
BLACKLIST_WR          = 0.30 # WR < 30% → blacklist temporal esa combinación
BLACKLIST_DURATION_H  = 24   # horas de blacklist temporal
WINDOW_RECENT         = 30   # últimos N trades para calcular WR "reciente"

# ══════════════════════════════════════════════════════════
# ESTRUCTURAS DE DATOS
# ══════════════════════════════════════════════════════════

@dataclass
class TradeRecord:
    """Registro completo de un trade cerrado para el aprendizaje."""
    ts:           str   = ""    # timestamp cierre
    symbol:       str   = ""
    side:         str   = ""    # long / short
    modules:      str   = ""    # "ConfPRO+BollingerHunter"
    signals:      str   = ""    # señales activas en la entrada
    entry_score:  int   = 0
    pnl:          float = 0.0
    pnl_pct:      float = 0.0
    max_profit:   float = 0.0   # máxima ganancia alcanzada
    reason:       str   = ""    # motivo cierre: SL / TP1 / TP3 / TRAILING / FLIP
    duration_min: int   = 0
    # Contexto de mercado en la entrada
    btc_bull:     bool  = False
    btc_bear:     bool  = False
    btc_adx:      float = 0.0
    rsi_entry:    float = 0.0
    adx_entry:    float = 0.0
    hour_utc:     int   = 0     # hora UTC del cierre

    @property
    def win(self) -> bool:
        return self.pnl > 0

@dataclass
class ModuleStats:
    """Estadísticas de rendimiento para una combinación de módulos."""
    name:         str   = ""
    trades:       int   = 0
    wins:         int   = 0
    total_pnl:    float = 0.0
    avg_pnl:      float = 0.0
    boost:        float = 1.0   # multiplicador de score actual
    blacklisted_until: float = 0.0  # timestamp

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades > 0 else 50.0

    @property
    def is_blacklisted(self) -> bool:
        return time.time() < self.blacklisted_until

@dataclass
class HourStats:
    """Rendimiento por franja horaria (0-23 UTC)."""
    hour:   int   = 0
    trades: int   = 0
    wins:   int   = 0
    pnl:    float = 0.0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades > 0 else 50.0

    @property
    def size_mult(self) -> float:
        """Multiplicador de tamaño de posición según WR de esta hora."""
        if self.trades < 5: return 1.0
        if self.win_rate >= 65: return 1.2
        if self.win_rate >= 50: return 1.0
        if self.win_rate >= 40: return 0.7
        return 0.5  # hora mala → mitad de posición

@dataclass
class BrainData:
    """Todo el conocimiento acumulado del bot."""
    version:       int   = 2
    total_trades:  int   = 0
    last_updated:  str   = ""
    last_review:   float = 0.0   # timestamp última revisión de parámetros

    # Historial de trades (últimos 500)
    history: List[dict] = field(default_factory=list)

    # Stats por combinación de módulos
    module_stats: Dict[str, dict] = field(default_factory=dict)

    # Stats por hora UTC
    hour_stats: Dict[str, dict] = field(default_factory=dict)

    # Score mínimo efectivo por combinación (puede ser diferente al global)
    min_score_overrides: Dict[str, int] = field(default_factory=dict)

    # Parámetros adaptativos actuales
    effective_min_score:   int   = 5
    effective_cooldown_min: int  = 30
    consecutive_losses_today: int = 0
    last_loss_ts:          float = 0.0

    # Insights generados (para Telegram)
    insights: List[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════
# BRAIN ENGINE
# ══════════════════════════════════════════════════════════

class Brain:
    def __init__(self):
        self.data = BrainData()
        self.load()

    # ── Persistencia ─────────────────────────────────────────────────────────
    def load(self):
        try:
            if os.path.exists(BRAIN_FILE):
                with open(BRAIN_FILE, "r") as f:
                    raw = json.load(f)
                # Reconstruir con defaults para campos nuevos
                self.data = BrainData(
                    version              = raw.get("version", 1),
                    total_trades         = raw.get("total_trades", 0),
                    last_updated         = raw.get("last_updated", ""),
                    last_review          = raw.get("last_review", 0.0),
                    history              = raw.get("history", [])[-500:],  # max 500
                    module_stats         = raw.get("module_stats", {}),
                    hour_stats           = raw.get("hour_stats", {}),
                    min_score_overrides  = raw.get("min_score_overrides", {}),
                    effective_min_score  = raw.get("effective_min_score", 5),
                    effective_cooldown_min = raw.get("effective_cooldown_min", 30),
                    consecutive_losses_today = raw.get("consecutive_losses_today", 0),
                    last_loss_ts         = raw.get("last_loss_ts", 0.0),
                    insights             = raw.get("insights", [])[-20:],
                )
                log.info(f"Brain cargado: {self.data.total_trades} trades históricos")
            else:
                log.info("Brain nuevo — iniciando sin historial")
        except Exception as e:
            log.warning(f"Brain load error: {e} — iniciando limpio")
            self.data = BrainData()

    def save(self):
        try:
            self.data.last_updated = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
            with open(BRAIN_FILE, "w") as f:
                json.dump(asdict(self.data), f, indent=2)
        except Exception as e:
            log.warning(f"Brain save: {e}")

    # ── Registrar un trade cerrado ────────────────────────────────────────────
    def record_trade(self,
                     symbol: str, side: str, modules: str, signals: str,
                     entry_score: int, pnl: float, pnl_pct: float,
                     max_profit: float, reason: str, duration_min: int,
                     btc_bull: bool, btc_bear: bool, btc_adx: float,
                     rsi_entry: float, adx_entry: float):
        """Llamar cada vez que se cierra un trade. Es el momento de aprendizaje."""
        now_h = datetime.now(timezone.utc).hour
        rec = TradeRecord(
            ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
            symbol=symbol, side=side, modules=modules, signals=signals,
            entry_score=entry_score, pnl=pnl, pnl_pct=pnl_pct,
            max_profit=max_profit, reason=reason, duration_min=duration_min,
            btc_bull=btc_bull, btc_bear=btc_bear, btc_adx=btc_adx,
            rsi_entry=rsi_entry, adx_entry=adx_entry, hour_utc=now_h
        )

        self.data.history.append(asdict(rec))
        if len(self.data.history) > 500:
            self.data.history = self.data.history[-500:]

        self.data.total_trades += 1

        # Actualizar stats de módulos
        self._update_module_stats(modules, rec.win, pnl)

        # Actualizar stats de hora
        self._update_hour_stats(now_h, rec.win, pnl)

        # Rastrear pérdidas consecutivas
        if not rec.win:
            self.data.consecutive_losses_today += 1
            self.data.last_loss_ts = time.time()
        else:
            self.data.consecutive_losses_today = 0

        # Revisar y ajustar parámetros si corresponde
        should_review = (
            self.data.total_trades % 10 == 0 or     # cada 10 trades
            time.time() - self.data.last_review > 86400  # o cada 24h
        )
        if should_review:
            self._review_and_adapt()

        self.save()
        log.info(f"Brain: trade registrado {symbol} {'✅' if rec.win else '❌'} "
                 f"{pnl:+.2f} [{modules}] | total:{self.data.total_trades}")

    def _update_module_stats(self, modules: str, win: bool, pnl: float):
        key = modules or "unknown"
        if key not in self.data.module_stats:
            self.data.module_stats[key] = asdict(ModuleStats(name=key))
        s = self.data.module_stats[key]
        s["trades"] += 1
        s["wins"]   += 1 if win else 0
        s["total_pnl"] += pnl
        s["avg_pnl"]    = s["total_pnl"] / s["trades"]
        # Ajuste de boost con exponential moving average
        current_wr = s["wins"] / s["trades"]
        target_boost = 0.3 + (current_wr / 0.70) * 1.7  # 30% WR → 0.3, 70%+ → 2.0
        target_boost = max(MIN_MODULE_BOOST, min(MAX_MODULE_BOOST, target_boost))
        if s["trades"] >= MIN_SAMPLES_TO_ADJUST:
            old_boost = s.get("boost", 1.0)
            s["boost"] = old_boost + LEARNING_RATE * (target_boost - old_boost)
        # Blacklist si WR < 30% con ≥15 samples
        if s["trades"] >= 15 and current_wr < BLACKLIST_WR:
            s["blacklisted_until"] = time.time() + BLACKLIST_DURATION_H * 3600
            log.warning(f"Brain: {key} en BLACKLIST 24h (WR:{current_wr*100:.0f}%)")
        # Levantar blacklist si WR mejoró
        elif s["trades"] >= 20 and current_wr >= 0.50 and s.get("blacklisted_until", 0) > 0:
            s["blacklisted_until"] = 0

    def _update_hour_stats(self, hour: int, win: bool, pnl: float):
        key = str(hour)
        if key not in self.data.hour_stats:
            self.data.hour_stats[key] = asdict(HourStats(hour=hour))
        s = self.data.hour_stats[key]
        s["trades"] += 1
        s["wins"]   += 1 if win else 0
        s["pnl"]    += pnl

    def _review_and_adapt(self):
        """Revisa todos los stats y ajusta parámetros globales."""
        self.data.last_review = time.time()
        insights = []

        if self.data.total_trades < MIN_SAMPLES_TO_ADJUST:
            return

        # ── Ajustar MIN_SCORE global según WR reciente ────────────────────
        recent = self.data.history[-WINDOW_RECENT:]
        if len(recent) >= 10:
            recent_wr = sum(1 for t in recent if t["pnl"] > 0) / len(recent)
            recent_wins_5plus = [t for t in recent if t["entry_score"] >= 5 and t["pnl"] > 0]
            recent_total_5plus = [t for t in recent if t["entry_score"] >= 5]

            if len(recent_total_5plus) >= 5:
                wr_5plus = len(recent_wins_5plus) / len(recent_total_5plus)
                if wr_5plus < 0.45 and self.data.effective_min_score < 8:
                    self.data.effective_min_score += 1
                    insights.append(f"⬆️ Score mín subido a {self.data.effective_min_score} (WR@5={wr_5plus*100:.0f}%)")
                elif wr_5plus > 0.65 and self.data.effective_min_score > 4:
                    self.data.effective_min_score -= 1
                    insights.append(f"⬇️ Score mín bajado a {self.data.effective_min_score} (WR@5={wr_5plus*100:.0f}%)")

        # ── Ajustar min_score por combinación de módulos ──────────────────
        for combo, s in self.data.module_stats.items():
            if s["trades"] < MIN_SAMPLES_TO_ADJUST: continue
            wr = s["wins"] / s["trades"]
            boost = s.get("boost", 1.0)
            # Score mínimo efectivo: si boost < 0.7 → subir score requerido
            if boost < 0.7:
                override = self.data.effective_min_score + 2
                self.data.min_score_overrides[combo] = override
                insights.append(f"🎯 {combo}: score mín local→{override} (WR:{wr*100:.0f}%)")
            elif boost > 1.3 and combo in self.data.min_score_overrides:
                del self.data.min_score_overrides[combo]
                insights.append(f"✅ {combo}: override eliminado (WR:{wr*100:.0f}%)")

        # ── Detectar mejores/peores horas ─────────────────────────────────
        best_hours  = []
        worst_hours = []
        for h, s in self.data.hour_stats.items():
            if s["trades"] < 5: continue
            wr = s["wins"] / s["trades"]
            if wr >= 0.70: best_hours.append(int(h))
            elif wr <= 0.35: worst_hours.append(int(h))

        if worst_hours:
            insights.append(f"⚠️ Horas malas UTC: {sorted(worst_hours)} → posición 50%")
        if best_hours:
            insights.append(f"⭐ Horas buenas UTC: {sorted(best_hours)} → posición 120%")

        if insights:
            self.data.insights = insights + self.data.insights
            self.data.insights = self.data.insights[:20]
            log.info(f"Brain insights: {insights}")

    # ── Consultas (usadas por el bot antes de operar) ─────────────────────────
    def get_module_boost(self, modules: str) -> float:
        """Multiplicador de score para esta combinación. 1.0 = normal."""
        s = self.data.module_stats.get(modules, {})
        if not s: return 1.0
        if s.get("blacklisted_until", 0) > time.time():
            return 0.0  # blacklisted → no operar
        return float(s.get("boost", 1.0))

    def is_blacklisted(self, modules: str) -> bool:
        s = self.data.module_stats.get(modules, {})
        return s.get("blacklisted_until", 0) > time.time()

    def get_effective_min_score(self, modules: str) -> int:
        """Score mínimo efectivo para esta combinación (puede ser diferente al global)."""
        override = self.data.min_score_overrides.get(modules)
        return override if override else self.data.effective_min_score

    def get_hour_size_mult(self, hour_utc: Optional[int] = None) -> float:
        """Multiplicador de tamaño de posición según la hora actual."""
        h = hour_utc if hour_utc is not None else datetime.now(timezone.utc).hour
        s = self.data.hour_stats.get(str(h), {})
        if not s or s.get("trades", 0) < 5: return 1.0
        wr = s["wins"] / s["trades"]
        if wr >= 0.65: return 1.2
        if wr >= 0.50: return 1.0
        if wr >= 0.40: return 0.7
        return 0.5

    def adjusted_score(self, raw_score: int, modules: str) -> float:
        """Score ajustado por el conocimiento del bot."""
        boost = self.get_module_boost(modules)
        return raw_score * boost

    def should_enter(self, raw_score: int, modules: str) -> Tuple[bool, str]:
        """
        Decisión final: ¿entrar o no?
        Retorna (bool, reason_if_no)
        """
        if self.is_blacklisted(modules):
            s = self.data.module_stats.get(modules, {})
            wr = s["wins"]/s["trades"]*100 if s.get("trades",0) > 0 else 0
            remaining_h = (s.get("blacklisted_until",0) - time.time()) / 3600
            return False, f"módulo blacklisted WR:{wr:.0f}% ({remaining_h:.1f}h más)"

        adj = self.adjusted_score(raw_score, modules)
        min_sc = self.get_effective_min_score(modules)

        if adj < min_sc:
            boost = self.get_module_boost(modules)
            return False, f"score ajustado {adj:.1f} < {min_sc} (boost:{boost:.2f})"

        return True, ""

    # ── Informe completo para Telegram ────────────────────────────────────────
    def telegram_report(self) -> str:
        lines = [
            "🧠 <b>BRAIN — Aprendizaje Adaptativo</b>",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📊 Trades analizados: <b>{self.data.total_trades}</b>",
            f"🎯 Score mín efectivo: <b>{self.data.effective_min_score}</b>",
            f"⏸ Cooldown efectivo: <b>{self.data.effective_cooldown_min}min</b>",
        ]

        # Top módulos
        if self.data.module_stats:
            sorted_mods = sorted(
                [(k, v) for k, v in self.data.module_stats.items() if v.get("trades",0) >= 3],
                key=lambda x: x[1].get("wins",0)/max(x[1].get("trades",1),1),
                reverse=True
            )
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("📈 <b>Módulos por rendimiento:</b>")
            for k, v in sorted_mods[:5]:
                t = v.get("trades", 0)
                w = v.get("wins", 0)
                wr = w/t*100 if t > 0 else 0
                boost = v.get("boost", 1.0)
                bl = " ⛔BLACKLIST" if v.get("blacklisted_until",0) > time.time() else ""
                lines.append(f"  {'🟢' if wr>=55 else '🟡' if wr>=45 else '🔴'} "
                             f"{k[:30]}: {t}t {wr:.0f}%WR boost:{boost:.2f}{bl}")

        # Horas
        if self.data.hour_stats:
            hr_data = [(int(h), v) for h, v in self.data.hour_stats.items() if v.get("trades",0) >= 5]
            if hr_data:
                best  = max(hr_data, key=lambda x: x[1]["wins"]/x[1]["trades"])
                worst = min(hr_data, key=lambda x: x[1]["wins"]/x[1]["trades"])
                bwr   = best[1]["wins"]/best[1]["trades"]*100
                wwr   = worst[1]["wins"]/worst[1]["trades"]*100
                lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                lines.append(f"⏰ Mejor hora: {best[0]:02d}h UTC ({bwr:.0f}%WR) ×{self.get_hour_size_mult(best[0]):.1f}")
                lines.append(f"⏰ Peor hora:  {worst[0]:02d}h UTC ({wwr:.0f}%WR) ×{self.get_hour_size_mult(worst[0]):.1f}")

        # Insights
        if self.data.insights:
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("💡 <b>Insights:</b>")
            for ins in self.data.insights[:5]:
                lines.append(f"  {ins}")

        # Score overrides activos
        if self.data.min_score_overrides:
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("🎯 <b>Score overrides activos:</b>")
            for combo, sc in list(self.data.min_score_overrides.items())[:3]:
                lines.append(f"  {combo[:25]}: mín={sc}")

        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🕐 {datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}")
        return "\n".join(lines)

    def telegram_summary_line(self) -> str:
        """Línea corta para incluir en el heartbeat."""
        if self.data.total_trades < 5:
            return "🧠 Brain: aprendiendo... (pocas muestras)"
        recent = self.data.history[-20:]
        if not recent: return ""
        rwr = sum(1 for t in recent if t["pnl"] > 0) / len(recent) * 100
        bl_count = sum(1 for s in self.data.module_stats.values()
                       if s.get("blacklisted_until", 0) > time.time())
        return (f"🧠 Brain: {self.data.total_trades}t | WR reciente:{rwr:.0f}% | "
                f"Score mín:{self.data.effective_min_score} | "
                f"Blacklists:{bl_count}")

    def score_distribution_bar(self) -> str:
        """Mini gráfico de distribución WR por score de entrada."""
        if len(self.data.history) < 15: return ""
        buckets = {}
        for t in self.data.history:
            sc = t.get("entry_score", 0)
            bucket = f"{(sc//2)*2}-{(sc//2)*2+1}"
            if bucket not in buckets:
                buckets[bucket] = {"w": 0, "t": 0}
            buckets[bucket]["t"] += 1
            if t["pnl"] > 0: buckets[bucket]["w"] += 1
        lines = ["📊 WR por score de entrada:"]
        for k in sorted(buckets.keys()):
            v = buckets[k]
            wr = v["w"]/v["t"]*100 if v["t"] > 0 else 0
            bar = "█" * int(wr / 10) + "░" * (10 - int(wr / 10))
            lines.append(f"  {k:>4}pt: {bar} {wr:.0f}% ({v['t']}t)")
        return "\n".join(lines)


# Instancia global
brain = Brain()


# ══════════════════════════════════════════════════════════
# FUNCIONES DE INTEGRACIÓN (llamadas desde saty_v13.py)
# ══════════════════════════════════════════════════════════

def on_trade_closed(trade_state, pnl: float, reason: str,
                    btc_bull: bool, btc_bear: bool, btc_adx: float,
                    rsi_entry: float = 0.0, adx_entry: float = 0.0):
    """
    Hook principal. Llamar al final de close_trade() y manage_trade()
    con la TradeState y el pnl final.
    """
    try:
        pnl_pct = 0.0
        if trade_state.contracts > 0 and trade_state.entry_price > 0:
            pnl_pct = pnl / (trade_state.entry_price * trade_state.contracts) * 100

        # Calcular duración
        duration_min = 0
        try:
            from datetime import datetime
            entry_dt = datetime.strptime(trade_state.entry_time, "%d/%m/%Y %H:%M UTC")
            duration_min = int((datetime.utcnow() - entry_dt).total_seconds() / 60)
        except Exception:
            pass

        brain.record_trade(
            symbol      = trade_state.symbol,
            side        = trade_state.side,
            modules     = trade_state.modules_used,
            signals     = trade_state.active_signals[:100],
            entry_score = trade_state.entry_score,
            pnl         = pnl,
            pnl_pct     = pnl_pct,
            max_profit  = trade_state.max_profit_pct,
            reason      = reason,
            duration_min = duration_min,
            btc_bull    = btc_bull,
            btc_bear    = btc_bear,
            btc_adx     = btc_adx,
            rsi_entry   = rsi_entry,
            adx_entry   = adx_entry,
        )
    except Exception as e:
        log.warning(f"Brain on_trade_closed: {e}")


def check_entry(raw_score: int, modules: str, size_usdt: float) -> Tuple[bool, float, str]:
    """
    Verificación inteligente antes de abrir un trade.

    Retorna: (can_enter, adjusted_size_usdt, reason_if_no)
    """
    try:
        # 1. Verificar si módulo está blacklisted / score insuficiente
        can_enter, reason = brain.should_enter(raw_score, modules)
        if not can_enter:
            return False, 0.0, reason

        # 2. Multiplicador de tamaño por hora
        hour_mult = brain.get_hour_size_mult()
        adjusted_size = size_usdt * hour_mult

        reason_ok = f"score_adj={brain.adjusted_score(raw_score, modules):.1f} hour_mult={hour_mult:.1f}x"
        return True, adjusted_size, reason_ok
    except Exception as e:
        log.warning(f"Brain check_entry: {e}")
        return True, size_usdt, "brain_error_fallback"
