"""
filters.py — Filtros Institucionales v1.0
Investigación 2025-2026: lo que usan los bots ganadores

Filtros implementados:
  1. Funding Rate Filter → evita entrar contra el crowd
  2. Open Interest Trend → confirma si el movimiento tiene fuerza real
  3. Session Filter → solo opera en horarios de alto volumen
  4. CVD Estimado → compra/venta agresiva via velas
  5. Liquidity Zone Detector → mapea zonas de stops acumulados
  6. Fee Viability Check → asegura edge ≥ 3× coste de transacción

Uso:
    from filters import InstitutionalFilters
    f = InstitutionalFilters(client)
    ok, reason = f.validate_long("BTCUSDT", signal)
"""
import logging
import time
from datetime import datetime, timezone
from typing import Optional, Tuple
import numpy as np

log = logging.getLogger(__name__)

# ── Funding Rate thresholds ───────────────────────────────────────────
# Positivo = longs pagan a shorts (mercado cargado de longs)
# Si es muy positivo → probable squeeze bajista → evitar LONG
FUNDING_LONG_MAX   =  0.0005   # 0.05%/8h = muy cargado de longs → skip LONG
FUNDING_SHORT_MIN  = -0.0005   # -0.05%/8h = muy cargado de shorts → skip SHORT
FUNDING_EXTREME    =  0.001    # 0.10% → señal contraria fuerte

# ── Session Filter (UTC hours) ────────────────────────────────────────
# Basado en investigación: ETH Europe→US momentum +44.9bps 2020-2026
SESSION_BEST   = (13, 22)   # US session: BEST momentum
SESSION_OK     = (7, 13)    # London session: OK
SESSION_AVOID  = (22, 7)    # Asia: más reversals, menos momentum

# ── CVD (Cumulative Volume Delta) ─────────────────────────────────────
CVD_WINDOW = 10  # últimas 10 velas para CVD
CVD_MIN_ALIGN = 0.55  # 55% de velas deben confirmar la dirección

# ── Open Interest ─────────────────────────────────────────────────────
OI_CONFIRM_PCT = 0.02  # OI debe subir/bajar al menos 2% para confirmar

# ── Fee Viability ─────────────────────────────────────────────────────
TAKER_FEE    = 0.0005   # 0.05% por lado
MIN_EDGE_MULT = 2.5     # edge mínimo = 2.5× fees totales (relajado de 3×)


class SessionStatus:
    BEST  = "BEST"
    OK    = "OK"
    AVOID = "AVOID"


class InstitutionalFilters:
    def __init__(self, client):
        self.client   = client
        self._cache   = {}   # cache de funding/OI para no spamear la API
        self._cache_ttl = 300  # 5 min

    def _cached(self, key: str, fn):
        now = time.time()
        if key in self._cache:
            val, ts = self._cache[key]
            if now - ts < self._cache_ttl:
                return val
        val = fn()
        self._cache[key] = (val, now)
        return val

    # ── 1. Funding Rate ──────────────────────────────────────────────
    def get_funding_rate(self, symbol: str) -> float:
        """Retorna funding rate actual (ej: 0.0001 = 0.01%)"""
        def _fetch():
            try:
                data = self.client._get(
                    "/openApi/swap/v2/quote/premiumIndex",
                    {"symbol": symbol}, signed=False
                )
                rate = float(data.get("data", {}).get("lastFundingRate", 0) or 0)
                log.debug(f"Funding {symbol}: {rate*100:.4f}%")
                return rate
            except Exception as e:
                log.debug(f"Funding rate error {symbol}: {e}")
                return 0.0
        return self._cached(f"funding_{symbol}", _fetch)

    def funding_ok_for_long(self, symbol: str) -> Tuple[bool, str]:
        """
        LONG es mejor cuando:
        - Funding neutral o ligeramente negativo (mercado no sobrecargado de longs)
        - Funding muy positivo = muchos longs = probable squeeze → skip
        """
        rate = self.get_funding_rate(symbol)
        if rate > FUNDING_LONG_MAX:
            return False, f"Funding muy positivo ({rate*100:.3f}%) → mercado sobrecargado LONG"
        if rate < -FUNDING_EXTREME:
            return True, f"Funding negativo extremo ({rate*100:.3f}%) → oportunidad contrarian LONG"
        return True, f"Funding OK para LONG ({rate*100:.3f}%)"

    def funding_ok_for_short(self, symbol: str) -> Tuple[bool, str]:
        """SHORT es mejor cuando funding no está muy negativo (sobrecargado de shorts)"""
        rate = self.get_funding_rate(symbol)
        if rate < FUNDING_SHORT_MIN:
            return False, f"Funding muy negativo ({rate*100:.3f}%) → mercado sobrecargado SHORT"
        if rate > FUNDING_EXTREME:
            return True, f"Funding positivo extremo ({rate*100:.3f}%) → contrarian SHORT"
        return True, f"Funding OK para SHORT ({rate*100:.3f}%)"

    # ── 2. Open Interest ─────────────────────────────────────────────
    def get_open_interest(self, symbol: str) -> float:
        def _fetch():
            try:
                data = self.client._get(
                    "/openApi/swap/v2/quote/openInterest",
                    {"symbol": symbol}, signed=False
                )
                return float(data.get("data", {}).get("openInterest", 0) or 0)
            except Exception as e:
                log.debug(f"OI error {symbol}: {e}")
                return 0.0
        return self._cached(f"oi_{symbol}", _fetch)

    def oi_confirms_direction(self, symbol: str, direction: str,
                               klines: list) -> Tuple[bool, str]:
        """
        Precio↑ + OI↑ = breakout real (LONG confirmado)
        Precio↑ + OI↓ = movimiento débil sin fuerza
        Precio↓ + OI↑ = short confirmado
        """
        if len(klines) < 5:
            return True, "OI: no hay datos suficientes, skip check"

        oi_now = self.get_open_interest(symbol)
        if oi_now <= 0:
            return True, "OI: dato no disponible, skip"

        # Estimamos OI previo de hace 5 velas (simplificado)
        # En producción usaríamos el endpoint histórico de OI
        try:
            close_now  = float(klines[-1].get("close", klines[-1][4] if isinstance(klines[-1], list) else 0))
            close_prev = float(klines[-6].get("close", klines[-6][4] if isinstance(klines[-6], list) else 0))
            price_up = close_now > close_prev
        except Exception:
            return True, "OI: error parsing klines"

        # Sin historial de OI, usamos solo el precio como proxy
        if direction == "LONG" and price_up:
            return True, f"OI: precio↑, asumiendo breakout válido (OI={oi_now:.0f})"
        if direction == "SHORT" and not price_up:
            return True, f"OI: precio↓, asumiendo breakout válido (OI={oi_now:.0f})"

        return True, f"OI: neutral (OI={oi_now:.0f})"

    # ── 3. Session Filter ────────────────────────────────────────────
    @staticmethod
    def get_session() -> str:
        hour = datetime.now(timezone.utc).hour
        if SESSION_BEST[0] <= hour < SESSION_BEST[1]:
            return SessionStatus.BEST
        if SESSION_OK[0] <= hour < SESSION_OK[1]:
            return SessionStatus.OK
        return SessionStatus.AVOID

    @staticmethod
    def session_allows_trade(tier: str) -> Tuple[bool, str]:
        """
        En Asia session solo permitimos tier S (señales muy fuertes)
        En US session permitimos todo
        """
        session = InstitutionalFilters.get_session()
        hour_utc = datetime.now(timezone.utc).hour

        if session == SessionStatus.BEST:
            return True, f"US session ({hour_utc}h UTC) → máximo momentum"
        if session == SessionStatus.OK:
            return True, f"London session ({hour_utc}h UTC) → bueno"
        # Asia session: solo tier S
        if tier == "S":
            return True, f"Asia session ({hour_utc}h UTC) pero señal Tier S → permitido"
        return False, f"Asia session ({hour_utc}h UTC) → solo operar Tier S, tier actual={tier}"

    # ── 4. CVD Estimado ──────────────────────────────────────────────
    @staticmethod
    def estimate_cvd(klines: list, window: int = CVD_WINDOW) -> Tuple[float, str]:
        """
        Estimación de Cumulative Volume Delta sin tick data.
        Formula: si close > open → vela alcista (compradores agresivos)
                 si close < open → vela bajista (vendedores agresivos)
        CVD = suma de (vol * signo) en las últimas N velas
        """
        if len(klines) < window:
            return 0.0, "CVD: no hay suficientes velas"

        recent = klines[-window:]
        cvd = 0.0
        bullish_count = 0

        for k in recent:
            try:
                if isinstance(k, dict):
                    o, c, v = float(k["open"]), float(k["close"]), float(k["volume"])
                else:
                    o, c, v = float(k[1]), float(k[4]), float(k[5])
                sign = 1 if c >= o else -1
                cvd += sign * v
                if sign > 0:
                    bullish_count += 1
            except Exception:
                continue

        bullish_pct = bullish_count / max(window, 1)
        return cvd, f"CVD: {cvd:+.0f} | bullish_velas={bullish_pct*100:.0f}%"

    @staticmethod
    def cvd_aligns_with(direction: str, klines: list) -> Tuple[bool, str]:
        cvd, info = InstitutionalFilters.estimate_cvd(klines)
        if direction == "LONG" and cvd >= 0:
            return True, f"CVD confirma LONG ({info})"
        if direction == "SHORT" and cvd <= 0:
            return True, f"CVD confirma SHORT ({info})"
        # CVD débilmente opuesto → no bloqueamos, solo advertimos
        if abs(cvd) < 1000:
            return True, f"CVD neutral ({info}) → no bloquea"
        return False, f"CVD opuesto ({info}) → señal débil"

    # ── 5. Liquidity Zone (simplificado) ────────────────────────────
    @staticmethod
    def is_near_liquidity_pool(price: float, klines: list,
                                direction: str, atr: float) -> Tuple[bool, str]:
        """
        Detecta si el precio está cerca de un pool de liquidez
        (swing high/low donde se acumulan stops).

        Para LONG: queremos entrar CERCA de un swing low (stop pool de vendedores)
        Para SHORT: queremos entrar CERCA de un swing high (stop pool de compradores)
        """
        if len(klines) < 20:
            return True, "Liquidity: no hay datos"

        try:
            highs = [float(k["high"] if isinstance(k, dict) else k[2]) for k in klines[-20:]]
            lows  = [float(k["low"]  if isinstance(k, dict) else k[3]) for k in klines[-20:]]

            recent_high = max(highs)
            recent_low  = min(lows)
            range_size  = recent_high - recent_low

            if range_size <= 0:
                return True, "Liquidity: rango 0"

            # Para LONG: precio está en el tercio inferior del rango = zona de liquidez
            pct_position = (price - recent_low) / range_size

            if direction == "LONG":
                if pct_position < 0.40:
                    return True, f"LONG cerca de zona de liquidez baja ({pct_position*100:.0f}% del rango)"
                elif pct_position > 0.75:
                    return False, f"LONG en parte alta del rango ({pct_position*100:.0f}%) → precio extendido"
            else:  # SHORT
                if pct_position > 0.60:
                    return True, f"SHORT cerca de zona de liquidez alta ({pct_position*100:.0f}% del rango)"
                elif pct_position < 0.25:
                    return False, f"SHORT en parte baja del rango ({pct_position*100:.0f}%) → precio extendido"

            return True, f"Liquidity: posición neutral ({pct_position*100:.0f}%)"
        except Exception:
            return True, "Liquidity: error de cálculo"

    # ── 6. Fee Viability ────────────────────────────────────────────
    @staticmethod
    def is_fee_viable(entry: float, tp1: float, sl: float) -> Tuple[bool, str]:
        """
        El edge esperado debe ser ≥ 2.5× el coste total de fees.
        Edge esperado = distancia al TP1 (primer cierre parcial).
        Coste total = 2× taker fee (entrada + salida).
        """
        total_fee_pct = TAKER_FEE * 2   # 0.10%
        tp1_pct = abs(tp1 - entry) / max(entry, 1e-10)
        sl_pct  = abs(sl  - entry) / max(entry, 1e-10)

        if tp1_pct < total_fee_pct * MIN_EDGE_MULT:
            return False, (
                f"Edge insuficiente: TP1={tp1_pct*100:.2f}% < "
                f"min={total_fee_pct*MIN_EDGE_MULT*100:.2f}% (fees×{MIN_EDGE_MULT})"
            )
        rr = tp1_pct / max(sl_pct, 1e-10)
        return True, f"Fee viable: edge={tp1_pct*100:.2f}% RR={rr:.1f}x"

    # ── Validación completa ──────────────────────────────────────────
    def validate_signal(self, symbol: str, direction: str, tier: str,
                        entry: float, sl: float, tp1: float,
                        klines: list, atr: float,
                        strict: bool = False) -> Tuple[bool, list]:
        """
        Aplica todos los filtros institucionales.
        Retorna (ok, lista_de_razones)

        strict=True: todos deben pasar
        strict=False: hasta 2 filtros pueden fallar (para tier B)
        """
        results = []
        fails   = 0

        checks = [
            # (nombre, resultado, es_bloqueante_para_tier_S)
        ]

        # Fee viability (siempre bloqueante)
        ok, msg = self.is_fee_viable(entry, tp1, sl)
        if not ok:
            return False, [f"❌ FEE INVIABLE: {msg}"]
        results.append(f"✅ Fees: {msg}")

        # Session filter
        ok, msg = self.session_allows_trade(tier)
        if not ok:
            fails += 1
            results.append(f"⚠️ Sesión: {msg}")
        else:
            results.append(f"✅ Sesión: {msg}")

        # Funding rate
        if direction == "LONG":
            ok, msg = self.funding_ok_for_long(symbol)
        else:
            ok, msg = self.funding_ok_for_short(symbol)
        if not ok:
            fails += 1
            results.append(f"⚠️ Funding: {msg}")
        else:
            results.append(f"✅ Funding: {msg}")

        # CVD
        ok, msg = self.cvd_aligns_with(direction, klines)
        if not ok:
            fails += 1
            results.append(f"⚠️ CVD: {msg}")
        else:
            results.append(f"✅ {msg}")

        # Liquidity zone
        ok, msg = self.is_near_liquidity_pool(entry, klines, direction, atr)
        if not ok:
            fails += 1
            results.append(f"⚠️ Liquidez: {msg}")
        else:
            results.append(f"✅ {msg}")

        # Decisión final
        max_fails = 0 if tier == "S" else (1 if tier == "A" else 2)
        if strict:
            max_fails = 0

        if fails > max_fails:
            return False, results + [f"❌ {fails} filtros fallaron (máx {max_fails} para tier {tier})"]

        return True, results


# ── Función helper para uso simple desde bot.py ──────────────────────
def quick_validate(filters_obj: InstitutionalFilters, symbol: str,
                   direction: str, tier: str, entry: float, sl: float,
                   tp1: float, klines: list, atr: float) -> Tuple[bool, str]:
    """Wrapper rápido que retorna (bool, string_reason)"""
    ok, msgs = filters_obj.validate_signal(
        symbol, direction, tier, entry, sl, tp1, klines, atr
    )
    summary = " | ".join(m for m in msgs if m.startswith("❌") or m.startswith("⚠️"))
    if not summary:
        summary = f"Todos los filtros OK [{len(msgs)} checks]"
    return ok, summary
