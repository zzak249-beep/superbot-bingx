"""
Bot v2 — Orquestador Principal con todas las mejoras
─────────────────────────────────────────────────────
FIXES v2.1:
  - Logging diagnóstico en cada etapa del filtrado
  - _last_scan_time inicia en datetime.min (fuerza scan al arrancar)  ← ya estaba
  - Log explícito cuando scan_cache está vacío
  - Log de cuántos candidatos pasan cada filtro
  - Timeout en funding/mtf para no bloquear el loop
  - Fallback si balance_data tiene estructura inesperada
"""

import asyncio
import numpy as np
from datetime import datetime, time as dtime
from loguru import logger

from config import cfg
from bingx_client import BingXClient
from scanner import MarketScanner, CoinScore
from signal_engine import SignalProjectionExplorer, SignalType
from risk_manager import RiskManager, PositionInfo
from funding_oi import FundingOIFilter
from mtf_analyzer import MTFAnalyzer
from smart_order import SmartOrderManager
from filters import CorrelationFilter, TelegramNotifier


logger.add(
    "logs/botv2_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="30 days",
    level="DEBUG",          # ← DEBUG para ver todo durante diagnóstico
    format="{time:HH:mm:ss} | {level:<8} | {message}",
)


class TradingBotV2:

    def __init__(self):
        cfg.validate()

        self.client = BingXClient(
            api_key=cfg.BINGX_API_KEY,
            secret_key=cfg.BINGX_SECRET_KEY,
            demo=cfg.DEMO_MODE,
        )
        self.scanner = MarketScanner(
            client=self.client,
            min_volume_24h=cfg.MIN_VOLUME_24H,
            max_coins=cfg.MAX_COINS_SCAN,
            vol_spike_min=cfg.VOL_SPIKE_MIN,
            min_change_abs=cfg.MIN_CHANGE_ABS,
            interval=cfg.TRADE_INTERVAL,
            candles=cfg.CANDLES,
        )
        self.engine = SignalProjectionExplorer(
            fast_len=cfg.FAST_MA,
            slow_len=cfg.SLOW_MA,
            proj_length=cfg.PROJ_LENGTH,
            adx_len=cfg.ADX_LEN,
            adx_min=cfg.ADX_MIN,
            atr_len=cfg.ATR_LEN,
            atr_ma_len=cfg.ATR_MA_LEN,
            atr_mult=cfg.ATR_MULT,
            rsi_len=cfg.RSI_LEN,
            rsi_long_min=cfg.RSI_LONG_MIN,
            rsi_short_max=cfg.RSI_SHORT_MAX,
            ema_trend_len=cfg.EMA_TREND,
        )
        self.risk = RiskManager(
            max_risk_per_trade=cfg.MAX_RISK_PER_TRADE,
            max_open_positions=cfg.MAX_OPEN_POSITIONS,
            max_daily_loss_pct=cfg.MAX_DAILY_LOSS_PCT,
            min_score=cfg.MIN_SCORE,
            leverage=cfg.LEVERAGE,
        )

        self.funding_filter = FundingOIFilter(
            funding_long_block  = float(getattr(cfg, "FUNDING_BLOCK", 0.0005)),
            funding_short_block = float(getattr(cfg, "FUNDING_BLOCK_SHORT", -0.0005)),
            require_oi_confirm  = getattr(cfg, "REQUIRE_OI_CONFIRM", True),
        )
        self.mtf = MTFAnalyzer(
            min_confluence = float(getattr(cfg, "MTF_MIN_CONFLUENCE", 25.0)),
            check_1h       = getattr(cfg, "MTF_CHECK_1H", True),
            check_4h       = getattr(cfg, "MTF_CHECK_4H", True),
        )
        self.smart_order = SmartOrderManager(
            atr_offset          = float(getattr(cfg, "LIMIT_ATR_OFFSET", 0.3)),
            max_wait_seconds    = int(getattr(cfg, "LIMIT_WAIT_SECONDS", 45)),
            use_market_fallback = True,
        )
        self.corr_filter = CorrelationFilter(
            max_correlation = float(getattr(cfg, "MAX_CORRELATION", 0.80)),
        )
        self.notifier = TelegramNotifier()

        self._scan_cache:     list[CoinScore] = []
        self._last_scan_time: datetime        = datetime.min
        self._loop_count:     int             = 0
        self._blocked_log:    dict            = {}

    # ─── Helpers ───────────────────────────────────────────────────────────

    def _calc_dynamic_leverage(self, atr_pct: float) -> int:
        base = cfg.LEVERAGE
        if atr_pct < 1.0:
            return base
        elif atr_pct < 2.0:
            return max(3, base - 2)
        elif atr_pct < 3.0:
            return max(2, base - 3)
        else:
            return 2

    def _effective_min_score(self) -> float:
        h = datetime.utcnow().hour
        if 1 <= h <= 7 and getattr(cfg, "SESSION_FILTER", False):
            return cfg.MIN_SCORE + 10
        return cfg.MIN_SCORE

    # ─── Scan ──────────────────────────────────────────────────────────────

    async def _refresh_scan_if_needed(self):
        now = datetime.now()
        minutes_since = (now - self._last_scan_time).total_seconds() / 60
        needs_scan = minutes_since >= cfg.SCAN_INTERVAL_MIN

        logger.debug(
            f"Scan check: {minutes_since:.1f}min desde último scan "
            f"(intervalo={cfg.SCAN_INTERVAL_MIN}min) → {'EJECUTANDO' if needs_scan else 'skip'}"
        )

        if needs_scan:
            logger.info("🔍 Ejecutando scan de mercado...")
            self._scan_cache = await self.scanner.scan()
            self._last_scan_time = now

            if not self._scan_cache:
                logger.warning(
                    "⚠️  Scanner devolvió 0 candidatos. Verifica: "
                    f"MIN_VOLUME_24H={cfg.MIN_VOLUME_24H}, "
                    f"VOL_SPIKE_MIN={cfg.VOL_SPIKE_MIN}, "
                    f"MIN_CHANGE_ABS={cfg.MIN_CHANGE_ABS}"
                )
            else:
                logger.info(
                    f"✅ Scanner encontró {len(self._scan_cache)} candidatos: "
                    + ", ".join(c.symbol for c in self._scan_cache[:10])
                )

    # ─── Main entry logic ──────────────────────────────────────────────────

    async def _try_open_trade(self, coin: CoinScore):
        symbol = coin.symbol

        # ── Fetch candles ─────────────────────────────────────────────────
        try:
            klines = await asyncio.wait_for(
                self.client.get_klines(symbol, cfg.TRADE_INTERVAL, cfg.CANDLES),
                timeout=15,
            )
        except asyncio.TimeoutError:
            logger.warning(f"  ⏱ Timeout obteniendo klines para {symbol}")
            return

        if not klines or len(klines) < 210:
            logger.debug(f"  {symbol}: klines insuficientes ({len(klines) if klines else 0})")
            return

        opens   = np.array([float(k[1]) for k in klines])
        highs   = np.array([float(k[2]) for k in klines])
        lows    = np.array([float(k[3]) for k in klines])
        closes  = np.array([float(k[4]) for k in klines])
        volumes = np.array([float(k[5]) for k in klines])

        # ── Signal Engine ─────────────────────────────────────────────────
        result = self.engine.analyze(opens, highs, lows, closes, volumes)
        if result.signal == SignalType.NONE:
            logger.debug(f"  {symbol}: sin señal del motor")
            return

        side_name = result.signal.value
        min_score = self._effective_min_score()

        logger.info(
            f"  📈 {symbol}: señal {side_name} | score={result.score} "
            f"(mín={min_score}) | ADX={result.adx:.1f} | RSI={result.rsi:.1f}"
        )

        # ── Score filter ──────────────────────────────────────────────────
        if result.score < min_score:
            logger.info(f"  ⏭  {symbol}: score {result.score} < mín {min_score} → descartado")
            return

        # ── Risk pre-check ────────────────────────────────────────────────
        ok, reason = self.risk.can_open_trade(symbol, side_name, result.score)
        if not ok:
            logger.info(f"  🚫 {symbol}: risk check falló → {reason}")
            return

        logger.info(f"\n{'─'*50}")
        logger.info(f"🔎 Analizando {symbol} {side_name} | Score: {result.score}/100")

        # ── Filter 1: Correlation ─────────────────────────────────────────
        self.corr_filter.update_prices(symbol, closes)
        corr_ok, corr_reason = self.corr_filter.is_allowed(
            symbol, list(self.risk.open_positions.keys())
        )
        if not corr_ok:
            logger.info(f"  🔗 {corr_reason}")
            await self.notifier.signal_blocked(symbol, side_name, corr_reason)
            return

        # ── Filter 2: Funding + OI ────────────────────────────────────────
        cur_price  = float(closes[-1])
        prev_price = float(closes[-2])
        try:
            funding_result = await asyncio.wait_for(
                self.funding_filter.analyze(self.client, symbol, cur_price, prev_price),
                timeout=10,
            )
        except asyncio.TimeoutError:
            logger.warning(f"  ⏱ {symbol}: timeout en funding filter, continuando sin él")
            # Crear resultado neutro para no bloquear por timeout
            from funding_oi import FundingOIResult  # import local para evitar circular
            funding_result = FundingOIResult(funding_pct=0.0, oi_trend="neutral")

        fund_ok, fund_reason = self.funding_filter.passes(funding_result, side_name)
        if not fund_ok:
            logger.info(f"  💸 Funding bloqueado: {fund_reason}")
            await self.notifier.signal_blocked(symbol, side_name, fund_reason)
            return

        # ── Filter 3: MTF confluence ──────────────────────────────────────
        try:
            mtf_result = await asyncio.wait_for(
                self.mtf.analyze(self.client, symbol, side_name, closes),
                timeout=15,
            )
        except asyncio.TimeoutError:
            logger.warning(f"  ⏱ {symbol}: timeout en MTF filter, continuando sin él")
            # Aprobar MTF si hay timeout para no bloquear innecesariamente
            class _FakeMTF:
                confluence_score = 0.0
                long_confirmed   = True
                short_confirmed  = True
                reason           = "timeout (aprobado por fallback)"
            mtf_result = _FakeMTF()

        confluence = mtf_result.confluence_score
        logger.info(f"  📊 MTF confluence: {confluence:+.0f} | {mtf_result.reason}")

        if side_name == "LONG"  and not mtf_result.long_confirmed:
            logger.info(f"  📊 MTF bloqueó LONG: {mtf_result.reason}")
            await self.notifier.signal_blocked(symbol, side_name, f"MTF: {mtf_result.reason}")
            return
        if side_name == "SHORT" and not mtf_result.short_confirmed:
            logger.info(f"  📊 MTF bloqueó SHORT: {mtf_result.reason}")
            await self.notifier.signal_blocked(symbol, side_name, f"MTF: {mtf_result.reason}")
            return

        # ── Dynamic leverage ──────────────────────────────────────────────
        atr_pct = result.atr / cur_price * 100
        lev     = self._calc_dynamic_leverage(atr_pct)
        if lev != cfg.LEVERAGE:
            logger.info(f"  ⚡ Leverage dinámico: {cfg.LEVERAGE}x → {lev}x (ATR {atr_pct:.2f}%)")

        # ── Balance & size ────────────────────────────────────────────────
        try:
            balance_data = await asyncio.wait_for(self.client.get_balance(), timeout=10)
        except asyncio.TimeoutError:
            logger.warning(f"  ⏱ Timeout obteniendo balance para {symbol}")
            return

        # FIX: probar múltiples campos posibles en la respuesta de BingX
        balance = 0.0
        for field in ("availableMargin", "equity", "availableBalance", "balance"):
            val = balance_data.get(field)
            if val is not None:
                try:
                    balance = float(val)
                    if balance > 0:
                        break
                except (ValueError, TypeError):
                    continue

        logger.info(f"  💰 Balance disponible: {balance:.2f} USDT")

        if balance <= 5:
            logger.warning(f"  ⚠️  Balance insuficiente: {balance:.2f} USDT (mín 5)")
            return

        qty = self.risk.calc_position_size(balance, cur_price, result.projected_sl, symbol)
        if qty <= 0:
            logger.warning(f"  ⚠️  Qty calculada = 0 para {symbol} | balance={balance} price={cur_price}")
            return

        # ── Log del setup completo ────────────────────────────────────────
        rr = abs(result.projected_tp - cur_price) / (abs(result.projected_sl - cur_price) + 1e-10)
        logger.info(
            f"\n{'═'*55}\n"
            f"🚀 {side_name} — {symbol}\n"
            f"   Score: {result.score}/100 | MTF: {confluence:+.0f} | Regime: {result.regime.value}\n"
            f"   Funding: {funding_result.funding_pct:+.4f}% | OI: {funding_result.oi_trend}\n"
            f"   ADX: {result.adx:.1f} | ATR: {result.atr:.6f} ({atr_pct:.2f}%) | RSI: {result.rsi:.1f}\n"
            f"   Entry: {cur_price:.6f} | SL: {result.projected_sl:.6f} | TP: {result.projected_tp:.6f}\n"
            f"   R:R = {rr:.2f}x | Qty: {qty} | Leverage: {lev}x\n"
            f"{'═'*55}"
        )

        if result.projection:
            last_bar = max(result.projection.keys())
            proj = result.projection[last_bar]
            logger.info(
                f"   📊 Proyección ({last_bar}v): "
                f"Best:{proj['best']*100:+.2f}% | "
                f"P75:{proj['p75']*100:+.2f}% | "
                f"Mean:{proj['mean']*100:+.2f}% | "
                f"P25:{proj['p25']*100:+.2f}%"
            )

        # ── Set leverage ──────────────────────────────────────────────────
        await self.client.set_leverage(symbol, lev, side_name)
        await asyncio.sleep(0.2)

        # ── Smart limit order ─────────────────────────────────────────────
        bx_side = "BUY" if side_name == "LONG" else "SELL"
        order_result = await self.smart_order.execute(
            client=self.client,
            symbol=symbol,
            side=bx_side,
            position_side=side_name,
            quantity=qty,
            entry_price=cur_price,
            atr=result.atr,
            stop_loss=result.projected_sl,
            take_profit=result.projected_tp,
        )

        if not order_result.success:
            logger.warning(f"  ❌ Orden no ejecutada: {order_result.reason}")
            return

        fill_price = order_result.fill_price or cur_price
        logger.info(
            f"  ✅ Entrada OK | método: {order_result.method} | "
            f"fill: {fill_price:.6f} | espera: {order_result.wait_seconds:.1f}s"
        )

        self.risk.register_position(PositionInfo(
            symbol=symbol,
            side=side_name,
            entry_price=fill_price,
            quantity=qty,
            stop_loss=result.projected_sl,
            take_profit=result.projected_tp,
            score=result.score,
        ))

        await self.notifier.trade_opened(
            symbol=symbol, side=side_name, qty=qty,
            entry=fill_price, sl=result.projected_sl, tp=result.projected_tp,
            score=result.score, method=order_result.method, confluence=confluence,
        )

    # ─── Manage positions ──────────────────────────────────────────────────

    async def _manage_positions(self):
        for symbol in list(self.risk.open_positions.keys()):
            try:
                klines = await self.client.get_klines(symbol, cfg.TRADE_INTERVAL, 20)
                if not klines:
                    continue

                prices  = np.array([float(k[4]) for k in klines])
                highs   = np.array([float(k[2]) for k in klines])
                lows    = np.array([float(k[3]) for k in klines])
                cur     = prices[-1]
                atr_now = float(np.maximum(highs - lows, 0).mean())

                self.risk.update_trailing_stop(symbol, cur, atr_now)

                should_close, reason = self.risk.should_close_position(symbol, cur)
                if should_close:
                    await self._close_position(symbol, cur, reason)

            except Exception as e:
                logger.error(f"Error gestionando {symbol}: {e}")

    async def _close_position(self, symbol: str, current_price: float, reason: str):
        pos = self.risk.open_positions.get(symbol)
        if not pos:
            return
        resp = await self.client.close_position(symbol, pos.side, pos.quantity)
        if resp.get("code") == 0:
            closed = self.risk.close_position(symbol, current_price)
            pnl = closed.pnl_pct if closed else 0.0
            await self.notifier.trade_closed(
                symbol=symbol, side=pos.side,
                entry=pos.entry_price, exit_price=current_price,
                pnl_pct=pnl * cfg.LEVERAGE,
                reason=reason, daily_pnl=self.risk.daily_pnl_pct
            )
            if self.risk.daily_pnl_pct <= -cfg.MAX_DAILY_LOSS_PCT:
                await self.notifier.daily_stop(self.risk.daily_pnl_pct)
        else:
            logger.error(f"Error cerrando {symbol}: {resp}")

    async def _sync_positions(self):
        try:
            ex_pos = await self.client.get_positions()
            ex_sym = {
                p["symbol"] for p in ex_pos
                if float(p.get("positionAmt", 0)) != 0
            }
            for sym in list(self.risk.open_positions.keys()):
                if sym not in ex_sym:
                    logger.info(f"  Sync: {sym} ya no existe en exchange")
                    self.risk.open_positions.pop(sym, None)
        except Exception as e:
            logger.debug(f"Sync error: {e}")

    # ─── Main loop ─────────────────────────────────────────────────────────

    async def run(self):
        logger.info("=" * 60)
        logger.info("🤖 BingX Signal Projection Bot v2.1")
        logger.info(f"   Modo: {'⚠️  DEMO' if cfg.DEMO_MODE else '🔴 REAL'}")
        logger.info(f"   Filtros: Funding+OI | MTF | Correlación | Sesión")
        logger.info(f"   Entradas: Smart Limit | Dynamic Leverage | Telegram")
        logger.info(f"   Config: MIN_SCORE={cfg.MIN_SCORE} | LEVERAGE={cfg.LEVERAGE}x")
        logger.info(f"           SCAN_INTERVAL={cfg.SCAN_INTERVAL_MIN}min | LOOP_SLEEP={cfg.LOOP_SLEEP_SECONDS}s")
        logger.info(f"           MAX_COINS_SCAN={cfg.MAX_COINS_SCAN} | MIN_VOLUME={cfg.MIN_VOLUME_24H}")
        logger.info("=" * 60)

        await self.notifier.startup(cfg.DEMO_MODE)

        while True:
            try:
                self._loop_count += 1
                logger.info(
                    f"\n─── Loop #{self._loop_count} — "
                    f"{datetime.now().strftime('%H:%M:%S')} UTC{datetime.utcnow().hour:+d}h ───"
                )
                logger.info(self.risk.summary())

                # 1. Sync + gestión
                await self._sync_positions()
                await self._manage_positions()

                # 2. Scan
                await self._refresh_scan_if_needed()

                # 3. Nuevas entradas
                if not self._scan_cache:
                    logger.info("📭 Sin candidatos en cache — esperando próximo scan")
                else:
                    open_count = len(self.risk.open_positions)
                    max_pos    = cfg.MAX_OPEN_POSITIONS
                    logger.info(
                        f"📡 {len(self._scan_cache)} candidatos | "
                        f"{open_count}/{max_pos} posiciones abiertas"
                    )

                    if open_count >= max_pos:
                        logger.info(f"  ⚠️  Máximo de posiciones alcanzado ({max_pos})")
                    else:
                        processed = 0
                        for coin in self._scan_cache:
                            if coin.symbol not in self.risk.open_positions:
                                await self._try_open_trade(coin)
                                processed += 1
                                await asyncio.sleep(0.5)
                        logger.info(f"  → {processed} candidatos procesados")

                logger.info(f"💤 {cfg.LOOP_SLEEP_SECONDS}s...")
                await asyncio.sleep(cfg.LOOP_SLEEP_SECONDS)

            except KeyboardInterrupt:
                logger.info("🛑 Parado por el usuario")
                break
            except Exception as e:
                logger.error(f"Error en loop: {e}", exc_info=True)
                await asyncio.sleep(30)

        await self.client.close()
        await self.notifier.close()


if __name__ == "__main__":
    bot = TradingBotV2()
    asyncio.run(bot.run())
