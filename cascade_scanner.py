"""
Liquidation Cascade Bot v1.0 — Scanner
══════════════════════════════════════════════════════════════════════════════
BOT DEDICADO: usa la cuenta BingX de renewed-love (400 USDT independientes).
NO comparte cuenta con joyful-art ni zesty → sin conflictos de riesgo.

Estrategia: Detectar y explotar cascadas de liquidación forzada.

EDGE REAL:
  Los traders sobre-apalancados son predecibles. Cuando el OI acumula
  posiciones en una dirección + el FR es extremo + el precio está cerca
  de un nivel técnico → una pequeña vela contraria desencadena la cascade.
  El bot entra ANTES de que empiece la cascade (no persigue el movimiento).

SEÑAL CORTA (cascade bajista):
  OI subió >20% en 4H + FR extremo positivo + precio cerca de resistencia
  → Abrir SHORT → SL por encima de la resistencia → TP 5-10%

SEÑAL LARGA (cascade alcista):
  OI subió >20% en 4H + FR extremo negativo + precio cerca de soporte
  → Abrir LONG → SL por debajo del soporte → TP 5-10%

DIFERENCIAS vs joyful-art:
  - Timeframe de análisis: 1H (no 3m)
  - Señal principal: OI + FR (no scoring de indicadores)
  - Hold: 2-12 horas (los cascades se completan en ese ventana)
  - Max posiciones: 3 (alta convicción, no cantidad)
  - MIN_SCORE_CASCADE: 70+ (solo señales fuertes)
  - Símbolo favorito: tokens con OI/Mcap alto (más explosivos)
══════════════════════════════════════════════════════════════════════════════
"""
import asyncio
import logging
import time
from collections import Counter

import config as C
from bingx_client import BingXClient
from risk_manager import RiskManager
from position_manager import PositionManager, OpenTrade
from oi_cascade_signal import oi_cascade_engine, CascadeSignal
import telegram_client as tg

log = logging.getLogger("cascade_scanner")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ema(values: list, period: int) -> list:
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def _rma(values: list, period: int) -> list:
    n = len(values)
    out = [0.0] * n
    alpha = 1.0 / period
    for i in range(n):
        out[i] = (sum(values[:i+1]) / (i+1)) if i < period else \
                 (out[i-1] + alpha * (values[i] - out[i-1]))
    return out


def _atr(klines: list, period: int = 14) -> float:
    if len(klines) < period + 1:
        return (klines[-1][2] - klines[-1][3]) if klines else 0.0
    tr = []
    for i in range(1, len(klines)):
        h, l, pc = klines[i][2], klines[i][3], klines[i-1][4]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return _rma(tr, period)[-1]


def _recent_high(klines: list, lookback: int = 20) -> float:
    return max(k[2] for k in klines[-lookback:]) if klines else 0.0


def _recent_low(klines: list, lookback: int = 20) -> float:
    return min(k[3] for k in klines[-lookback:]) if klines else 0.0


def _near_level(price: float, level: float, atr: float, mult: float = 1.5) -> bool:
    """True si el precio está dentro de `mult` × ATR del nivel."""
    return abs(price - level) <= atr * mult


# ── Evaluación de símbolo ─────────────────────────────────────────────────────

async def _evaluate_symbol(
    symbol: str,
    client: BingXClient,
    pos_mgr: PositionManager,
    diag: dict,
) -> tuple:
    """
    Evalúa un símbolo y retorna (CascadeSignal, setup_dict) si hay setup,
    o (None, reason) si no.
    """
    if pos_mgr.is_trading(symbol):
        diag["counts"]["already_trading"] += 1
        return None, "already_trading"

    # Fetchear datos en paralelo
    try:
        results = await asyncio.gather(
            client.get_klines(symbol, "1h", 50),   # 1H para estructura
            client.get_klines(symbol, "4h", 30),   # 4H para tendencia macro
            client.get_funding_rate(symbol),
            client.get_open_interest(symbol),
            return_exceptions=True,
        )
    except Exception as e:
        diag["counts"]["fetch_error"] += 1
        return None, f"fetch_error: {e}"

    k1h = results[0] if isinstance(results[0], list) else []
    k4h = results[1] if isinstance(results[1], list) else []
    fr  = results[2] if isinstance(results[2], float) else 0.0
    oi  = results[3] if isinstance(results[3], float) else 0.0

    if len(k1h) < 20:
        diag["counts"]["insufficient_data"] += 1
        return None, "insufficient_data"

    close = k1h[-1][4]
    atr   = _atr(k1h, 14)

    # ── Actualizar motor de cascade con datos frescos ─────────────────────────
    cascade = oi_cascade_engine.update(symbol, oi, fr, close, atr)

    min_score = getattr(C, 'CASCADE_MIN_SCORE', 60.0)
    if abs(cascade.score) < min_score:
        diag["counts"]["cascade_score_bajo"] += 1
        return None, f"cascade_score_bajo({cascade.score:.0f})"

    # ── Verificar proximidad a nivel técnico ──────────────────────────────────
    r20 = _recent_high(k1h, 20)   # resistencia reciente
    s20 = _recent_low(k1h, 20)    # soporte reciente
    r5  = _recent_high(k1h, 5)    # resistencia inmediata
    s5  = _recent_low(k1h, 5)     # soporte inmediato

    if cascade.direction == "SHORT_CASCADE":
        # Para cascade bajista: queremos precio cerca de resistencia
        near_resistance = _near_level(close, r20, atr, mult=2.0) or \
                          _near_level(close, r5, atr, mult=1.0)
        if not near_resistance:
            diag["counts"]["no_near_level"] += 1
            # No bloquear — el cascade puede ocurrir aunque no esté en resistencia
            # pero reducir el score
            cascade.boost *= 0.5

        direction = "SHORT"
        sl_price  = r20 + atr * getattr(C, 'CASCADE_SL_ATR', 1.5)
        tp1_price = close - (close - s5) * 0.5   # 50% del camino al soporte inmediato
        tp2_price = s20                             # soporte de 20 barras

    else:  # LONG_CASCADE
        # Para cascade alcista: precio cerca de soporte
        near_support = _near_level(close, s20, atr, mult=2.0) or \
                       _near_level(close, s5, atr, mult=1.0)
        if not near_support:
            diag["counts"]["no_near_level"] += 1
            cascade.boost *= 0.5

        direction = "LONG"
        sl_price  = s20 - atr * getattr(C, 'CASCADE_SL_ATR', 1.5)
        tp1_price = close + (r5 - close) * 0.5
        tp2_price = r20

    # Validar que SL/TP son lógicos
    if direction == "SHORT":
        if sl_price <= close or tp1_price >= close:
            diag["counts"]["invalid_levels"] += 1
            return None, "invalid_sl_tp"
    else:
        if sl_price >= close or tp1_price <= close:
            diag["counts"]["invalid_levels"] += 1
            return None, "invalid_sl_tp"

    # ── RR mínimo ─────────────────────────────────────────────────────────────
    risk   = abs(close - sl_price)
    reward = abs(tp1_price - close)
    rr     = reward / risk if risk > 0 else 0
    if rr < getattr(C, 'CASCADE_MIN_RR', 1.5):
        diag["counts"]["rr_bajo"] += 1
        return None, f"rr_bajo({rr:.1f})"

    setup = {
        "direction":   direction,
        "entry":       close,
        "sl":          round(sl_price, 8),
        "tp1":         round(tp1_price, 8),
        "tp2":         round(tp2_price, 8),
        "atr":         atr,
        "rr":          round(rr, 2),
        "cascade":     cascade,
        "resistance":  round(r20, 8),
        "support":     round(s20, 8),
    }

    log.info(
        "[%s] 💥 CASCADE SETUP %s | score=%+.0f FR=%.3f%% OI_4H=%+.1f%% "
        "RR=%.1f SL=%.6f TP1=%.6f",
        symbol, direction, cascade.score, fr*100,
        cascade.oi_delta_4h*100, rr, sl_price, tp1_price,
    )

    return cascade, setup


# ── Loop principal ────────────────────────────────────────────────────────────

async def scan_loop(client: BingXClient, risk: RiskManager,
                    pos_mgr: PositionManager, complement=None, journal=None):
    """Drop-in para cascade_main.py."""

    scan_interval = getattr(C, 'CASCADE_SCAN_INTERVAL', 60)
    log.info(
        "Cascade Bot v1.0 | Capital=%.0f USDT | MaxPos=%d | "
        "MinScore=%.0f | Interval=%ds",
        C.CAPITAL, C.MAX_OPEN_TRADES,
        getattr(C, 'CASCADE_MIN_SCORE', 60), scan_interval,
    )

    iteration = 0
    symbols_cache: list = []

    while True:
        start = time.time()
        iteration += 1
        diag = {"counts": Counter(), "setups": 0, "opened": 0}

        # Refrescar universo de símbolos cada 10 iteraciones
        if iteration == 1 or iteration % 10 == 0:
            try:
                all_syms = await client.get_all_symbols()
                # Priorizar tokens con alto volumen relativo (más OI potential)
                symbols_cache = all_syms[:getattr(C, 'CASCADE_UNIVERSE', 100)]
                log.info("Universo cascade: %d símbolos", len(symbols_cache))
            except Exception as e:
                log.error("get_all_symbols error: %s", e)
                if not symbols_cache:
                    await asyncio.sleep(30)
                    continue

        # ── Fase 1: Actualizar OI de todos los símbolos (pre-scan) ───────────
        # Hacer primero un pass de OI sin abrir trades — esto construye historia
        if iteration <= 3:
            log.info("Iter %d: construyendo historia OI (mín 2 iteraciones para señales)...",
                     iteration)
            for sym in symbols_cache[:50]:
                try:
                    oi = await client.get_open_interest(sym)
                    fr = await client.get_funding_rate(sym)
                    k  = await client.get_klines(sym, "1h", 5)
                    if k and oi:
                        oi_cascade_engine.update(sym, oi, fr, k[-1][4])
                    await asyncio.sleep(0.1)
                except Exception:
                    pass
            await asyncio.sleep(max(0, scan_interval - (time.time()-start)))
            continue

        # ── Fase 2: Evaluar símbolos con mayor potencial de cascade ──────────
        # Procesar en batches pequeños (cascade es menos frecuente que scalp)
        BATCH = 10
        for i in range(0, min(len(symbols_cache), 100), BATCH):
            batch = symbols_cache[i:i+BATCH]
            for sym in batch:
                try:
                    cascade, setup = await _evaluate_symbol(sym, client, pos_mgr, diag)
                except Exception as e:
                    log.debug("[%s] eval error: %s", sym, e)
                    continue

                if cascade is None:
                    continue

                diag["setups"] += 1

                if C.MODE == "SIGNAL":
                    c = setup["cascade"]
                    await tg.send(
                        f"💥 *CASCADE* — `{sym}` {setup['direction']}\n"
                        f"Score: `{c.score:+.0f}` | FR: `{c.fr_value*100:.3f}%`\n"
                        f"OI 4H: `{c.oi_delta_4h*100:+.1f}%`\n"
                        f"Entry: `{setup['entry']:.6f}` | RR: `{setup['rr']:.1f}`\n"
                        f"SL: `{setup['sl']:.6f}` | TP1: `{setup['tp1']:.6f}`\n"
                        f"_{c.reason}_"
                    )
                    continue

                # ── LIVE: verificar riesgo y abrir ────────────────────────────
                unrealized = await pos_mgr.get_unrealized_pnl()
                can, reason = await risk.can_trade(unrealized_pnl=unrealized)
                if not can:
                    diag["counts"]["risk_blocked"] += 1
                    continue

                trade_confirmed = False
                try:
                    sym_ok, _ = risk.symbol_allowed(sym)
                    if not sym_ok:
                        await risk.release_reservation()
                        continue

                    balance = await client.get_balance()
                    if balance < 5.0:
                        balance = C.CAPITAL

                    qty = risk.kelly_position_size(
                        balance, setup["entry"], setup["sl"],
                        score=min(abs(cascade.score), 90),
                        tier="STD", symbol=sym,
                    )
                    if qty <= 0:
                        await risk.release_reservation()
                        continue

                    results = await client.open_trade(
                        symbol=sym,
                        direction=setup["direction"],
                        quantity=qty,
                        sl_price=setup["sl"],
                        tp1_price=setup["tp1"],
                        tp2_price=setup["tp2"],
                    )
                    entry_resp = results.get("entry", {})
                    if entry_resp.get("code", -1) != 0:
                        log.error("[%s] Cascade entrada rechazada: %s", sym, entry_resp)
                        await risk.release_reservation()
                        continue

                    order_id = str(
                        entry_resp.get("data", {}).get("order", {}).get("orderId", "cascade")
                        or "cascade"
                    )
                    trade = OpenTrade(
                        symbol=sym, direction=setup["direction"],
                        entry=setup["entry"], sl=setup["sl"],
                        tp1=setup["tp1"], tp2=setup["tp2"],
                        qty=qty, atr=setup["atr"], order_id=order_id,
                    )
                    await pos_mgr.register_trade(trade)
                    await tg.notify_trade_opened(
                        type("S", (), {
                            "symbol": sym, "direction": setup["direction"],
                            "entry": setup["entry"], "sl": setup["sl"],
                            "tp1": setup["tp1"], "tp2": setup["tp2"],
                            "score": abs(cascade.score), "tier": "CASCADE",
                        })(), qty, order_id,
                    )
                    trade_confirmed = True
                    diag["opened"] += 1

                    if journal:
                        journal.on_open(
                            symbol=sym, direction=setup["direction"],
                            tier="CASCADE", score=abs(cascade.score),
                            filter_tags={"cascade_score": str(cascade.score),
                                         "fr": f"{cascade.fr_value*100:.3f}%",
                                         "oi_4h": f"{cascade.oi_delta_4h*100:+.1f}%"},
                        )

                except Exception as e:
                    log.error("[%s] cascade open error: %s", sym, e)
                finally:
                    if not trade_confirmed:
                        try:
                            await risk.release_reservation()
                        except Exception:
                            pass

            await asyncio.sleep(0.5)

        # ── Diagnóstico ───────────────────────────────────────────────────────
        elapsed = time.time() - start
        top5 = diag["counts"].most_common(5)

        # Mostrar top cascades detectadas
        top_cascades = oi_cascade_engine.get_top_cascades(3)
        cascade_str = " | ".join(
            f"{sym}={sig.score:+.0f}({sig.direction[:5]})"
            for sym, sig in top_cascades
        ) if top_cascades else "—"

        log.info(
            "Cascade iter %d | %d symbols | %d setups | %d opened | %.1fs | "
            "top_cascades: %s | %s",
            iteration, len(symbols_cache[:100]), diag["setups"], diag["opened"],
            elapsed, cascade_str,
            " | ".join(f"{k}={v}" for k, v in top5) if top5 else "—",
        )

        await asyncio.sleep(max(0.0, scan_interval - elapsed))
