"""
Bot Principal — orquesta scanner + signal engine + trading en BingX
Loop: escanear → analizar → filtrar → entrar → gestionar → salir
"""

import asyncio
import numpy as np
from datetime import datetime
from loguru import logger

from config import cfg
from bingx_client import BingXClient
from scanner import MarketScanner, CoinScore
from signal_engine import SignalProjectionExplorer, SignalType, MarketRegime
from risk_manager import RiskManager, PositionInfo


# ─── Setup logging ─────────────────────────────────────────────────────────────

logger.add(
    "logs/bot_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="14 days",
    level="INFO",
    format="{time:HH:mm:ss} | {level} | {message}",
)


class TradingBot:
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
        self._scan_cache:     list[CoinScore] = []
        self._last_scan_time: datetime        = datetime.min
        self._loop_count:     int             = 0

    # ─── Scan ──────────────────────────────────────────────────────────────────

    async def _refresh_scan_if_needed(self):
        now = datetime.now()
        minutes_since = (now - self._last_scan_time).total_seconds() / 60
        if minutes_since >= cfg.SCAN_INTERVAL_MIN:
            self._scan_cache = await self.scanner.scan()
            self._last_scan_time = now

    # ─── Open trade ────────────────────────────────────────────────────────────

    async def _try_open_trade(self, coin: CoinScore):
        symbol = coin.symbol

        # Fetch candles for signal engine
        klines = await self.client.get_klines(symbol, cfg.TRADE_INTERVAL, cfg.CANDLES)
        if not klines or len(klines) < 210:
            return

        opens   = np.array([float(k[1]) for k in klines])
        highs   = np.array([float(k[2]) for k in klines])
        lows    = np.array([float(k[3]) for k in klines])
        closes  = np.array([float(k[4]) for k in klines])
        volumes = np.array([float(k[5]) for k in klines])

        result = self.engine.analyze(opens, highs, lows, closes, volumes)

        if result.signal == SignalType.NONE:
            return

        # Verificar si se puede abrir
        ok, reason = self.risk.can_open_trade(symbol, result.signal.value, result.score)
        if not ok:
            logger.debug(f"  ⏭  {symbol}: {reason}")
            return

        # Balance y tamaño
        balance_data = await self.client.get_balance()
        balance = float(balance_data.get("availableMargin", 0) or balance_data.get("equity", 0) or 0)
        if balance <= 5:
            logger.warning(f"Balance insuficiente: {balance} USDT")
            return

        entry_price = closes[-1]
        qty = self.risk.calc_position_size(balance, entry_price, result.projected_sl, symbol)
        if qty <= 0:
            return

        regime_str = result.regime.value.upper()
        logger.info(
            f"\n{'═'*55}\n"
            f"🚀 SEÑAL {result.signal.value} — {symbol}\n"
            f"   Score: {result.score}/100 | Regime: {regime_str}\n"
            f"   ADX: {result.adx:.1f} | ATR: {result.atr:.6f} | RSI: {result.rsi:.1f}\n"
            f"   Entry: {entry_price:.6f} | SL: {result.projected_sl:.6f} | TP: {result.projected_tp:.6f}\n"
            f"   Qty: {qty} | Balance: {balance:.2f} USDT\n"
            f"{'═'*55}"
        )

        # Proyección estadística
        if result.projection:
            last_bar = max(result.projection.keys())
            proj = result.projection[last_bar]
            logger.info(
                f"   📊 Proyección ({last_bar} velas): "
                f"Mejor:{proj['best']*100:+.2f}% | "
                f"P75:{proj['p75']*100:+.2f}% | "
                f"Media:{proj['mean']*100:+.2f}% | "
                f"P25:{proj['p25']*100:+.2f}% | "
                f"Peor:{proj['worst']*100:+.2f}%"
            )

        # Set leverage
        await self.client.set_leverage(symbol, cfg.LEVERAGE, result.signal.value)
        await asyncio.sleep(0.3)

        # Place order
        side = "BUY" if result.signal == SignalType.LONG else "SELL"
        resp = await self.client.place_order(
            symbol=symbol,
            side=side,
            position_side=result.signal.value,
            order_type="MARKET",
            quantity=qty,
            stop_loss=result.projected_sl,
            take_profit=result.projected_tp,
        )

        if resp.get("code") != 0:
            logger.error(f"❌ Error al abrir orden {symbol}: {resp}")
            return

        order_id = resp.get("data", {}).get("orderId", "?")
        logger.info(f"✅ Orden enviada | ID: {order_id}")

        self.risk.register_position(PositionInfo(
            symbol=symbol,
            side=result.signal.value,
            entry_price=entry_price,
            quantity=qty,
            stop_loss=result.projected_sl,
            take_profit=result.projected_tp,
            score=result.score,
        ))

    # ─── Manage open positions ─────────────────────────────────────────────────

    async def _manage_positions(self):
        if not self.risk.open_positions:
            return

        for symbol in list(self.risk.open_positions.keys()):
            try:
                klines = await self.client.get_klines(symbol, cfg.TRADE_INTERVAL, 20)
                if not klines:
                    continue

                current_price = float(klines[-1][4])
                highs   = np.array([float(k[2]) for k in klines])
                lows    = np.array([float(k[3]) for k in klines])
                closes  = np.array([float(k[4]) for k in klines])
                atr = float(np.maximum(highs - lows, 0).mean())

                # Trailing stop
                new_sl = self.risk.update_trailing_stop(symbol, current_price, atr)
                if new_sl:
                    logger.info(f"🔄 Trailing SL actualizado {symbol}: {new_sl:.6f}")

                # Check exit
                should_close, reason = self.risk.should_close_position(symbol, current_price)
                if should_close:
                    await self._close_position(symbol, current_price, reason)

            except Exception as e:
                logger.error(f"Error gestionando {symbol}: {e}")

    async def _close_position(self, symbol: str, current_price: float, reason: str):
        pos = self.risk.open_positions.get(symbol)
        if not pos:
            return

        resp = await self.client.close_position(symbol, pos.side, pos.quantity)
        if resp.get("code") == 0:
            self.risk.close_position(symbol, current_price)
            logger.info(f"🔒 Cerrada {symbol} | Motivo: {reason}")
        else:
            logger.error(f"Error cerrando {symbol}: {resp}")

    # ─── Sync with exchange ────────────────────────────────────────────────────

    async def _sync_positions_with_exchange(self):
        """Sincroniza posiciones locales con las reales en BingX"""
        try:
            exchange_positions = await self.client.get_positions()
            exchange_symbols = {
                p["symbol"] for p in exchange_positions
                if float(p.get("positionAmt", 0)) != 0
            }
            # Limpiar locales que ya no existen en el exchange
            for sym in list(self.risk.open_positions.keys()):
                if sym not in exchange_symbols:
                    logger.info(f"🔄 Sync: {sym} ya no existe en exchange, limpiando local")
                    self.risk.open_positions.pop(sym, None)
        except Exception as e:
            logger.debug(f"Sync positions error: {e}")

    # ─── Main loop ─────────────────────────────────────────────────────────────

    async def run(self):
        logger.info("=" * 60)
        logger.info("🤖 BingX Signal Projection Bot — Iniciando")
        logger.info(f"   Modo: {'⚠️  DEMO' if cfg.DEMO_MODE else '🔴 REAL'}")
        logger.info(f"   Leverage: {cfg.LEVERAGE}x | Max pos: {cfg.MAX_OPEN_POSITIONS}")
        logger.info(f"   Riesgo/trade: {cfg.MAX_RISK_PER_TRADE}% | Score mínimo: {cfg.MIN_SCORE}")
        logger.info(f"   Intervalo: {cfg.TRADE_INTERVAL} | Loop: {cfg.LOOP_SLEEP_SECONDS}s")
        logger.info("=" * 60)

        while True:
            try:
                self._loop_count += 1
                logger.info(f"\n─── Loop #{self._loop_count} — {datetime.now().strftime('%H:%M:%S')} ───")
                logger.info(self.risk.summary())

                # 1. Gestionar posiciones abiertas
                await self._sync_positions_with_exchange()
                await self._manage_positions()

                # 2. Escanear mercado
                await self._refresh_scan_if_needed()

                # 3. Buscar nuevas entradas
                if self._scan_cache:
                    logger.info(f"📡 Analizando {len(self._scan_cache)} candidatos...")
                    for coin in self._scan_cache:
                        if coin.symbol not in self.risk.open_positions:
                            await self._try_open_trade(coin)
                            await asyncio.sleep(0.5)  # rate limit

                logger.info(f"💤 Esperando {cfg.LOOP_SLEEP_SECONDS}s...")
                await asyncio.sleep(cfg.LOOP_SLEEP_SECONDS)

            except KeyboardInterrupt:
                logger.info("🛑 Bot detenido por el usuario")
                break
            except Exception as e:
                logger.error(f"Error en loop principal: {e}", exc_info=True)
                await asyncio.sleep(30)

        await self.client.close()


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot = TradingBot()
    asyncio.run(bot.run())
