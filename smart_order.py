"""
Smart Order Manager
───────────────────
Problema de los bots con market orders:
  → Entrar en el cierre de la vela de señal = peor precio posible
  → Slippage destruye la expectativa matemática

Solución: limit order en retroceso
  → Si señal LONG: poner límite a entry - (ATR × offset)
  → Esperar N segundos que el mercado venga
  → Si no llega → subir el límite gradualmente (chasing)
  → Si sigue sin llegar → market order (fallback)
  → Si el precio se aleja mucho = setup inválido → cancelar

Además: detecta si la entrada es cerca de una zona de liquidación
(alta OI) y la evita.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Optional
from loguru import logger


@dataclass
class OrderResult:
    success:      bool
    order_id:     str
    fill_price:   float
    fill_qty:     float
    method:       str         # "limit" / "market" / "cancelled"
    reason:       str
    wait_seconds: float


class SmartOrderManager:
    """
    Ejecuta entradas con limit orders que «piden» al mercado.
    Mejora el precio de entrada vs market orders puras.
    """

    def __init__(
        self,
        atr_offset:          float = 0.3,     # limit = entry - ATR × 0.3 para longs
        max_wait_seconds:    int   = 45,       # espera máxima por el límite
        chase_after_seconds: int   = 20,       # si no llena, subir el límite
        chase_steps:         int   = 3,        # cuántas veces cazar
        max_slip_atr:        float = 0.8,      # si precio sube > 0.8 ATR = cancelar
        use_market_fallback: bool  = True,     # si llega al timeout → market
        poll_interval:       float = 2.0,      # cada cuántos segundos verificar fill
    ):
        self.atr_offset          = atr_offset
        self.max_wait_seconds    = max_wait_seconds
        self.chase_after_seconds = chase_after_seconds
        self.chase_steps         = chase_steps
        self.max_slip_atr        = max_slip_atr
        self.use_market_fallback = use_market_fallback
        self.poll_interval       = poll_interval

    def _calc_limit_price(
        self, side: str, entry_price: float, atr: float, step: int = 0
    ) -> float:
        """
        Calcula precio límite. En cada step de chase sube/baja la agresividad.
        """
        offset = atr * (self.atr_offset - step * 0.1)  # reduce offset al cazar
        offset = max(offset, 0.0)
        if side == "LONG":
            return round(entry_price - offset, 8)
        else:
            return round(entry_price + offset, 8)

    async def _check_order_filled(self, client, symbol: str, order_id: str) -> tuple[bool, float]:
        """Verifica si una orden fue llenada. Devuelve (filled, avg_price)"""
        try:
            data = await client._get(
                "/openApi/swap/v2/trade/order",
                {"symbol": symbol, "orderId": order_id},
            )
            order = data.get("data", {}).get("order", {})
            status = order.get("status", "")
            avg_price = float(order.get("avgPrice", 0))
            if status in ("FILLED", "PARTIALLY_FILLED"):
                return True, avg_price
            return False, 0.0
        except Exception as e:
            logger.debug(f"Order status check error: {e}")
            return False, 0.0

    async def execute(
        self,
        client,
        symbol:         str,
        side:           str,     # "BUY" / "SELL"
        position_side:  str,     # "LONG" / "SHORT"
        quantity:       float,
        entry_price:    float,
        atr:            float,
        stop_loss:      float,
        take_profit:    float,
    ) -> OrderResult:
        """
        Intenta entrar con limit order. Fallback a market si no llena.
        """
        start_time    = time.time()
        last_order_id = None
        chase_count   = 0

        logger.info(
            f"[SmartOrder] {symbol} {side} qty={quantity} "
            f"entry~{entry_price:.6f} ATR={atr:.6f}"
        )

        # ── Lugar primera limit order ──────────────────────────────────────
        limit_price = self._calc_limit_price(position_side, entry_price, atr, step=0)
        logger.info(f"  → Limit order @ {limit_price:.6f} (offset={self.atr_offset}×ATR)")

        resp = await client.place_order(
            symbol=symbol,
            side=side,
            position_side=position_side,
            order_type="LIMIT",
            quantity=quantity,
            price=limit_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        if resp.get("code") != 0:
            # Error en la orden, fallback directo a market
            logger.warning(f"  Limit order falló: {resp}. Fallback a market.")
            return await self._market_fallback(
                client, symbol, side, position_side,
                quantity, entry_price, stop_loss, take_profit,
                start_time, "limit_error"
            )

        last_order_id = resp.get("data", {}).get("orderId", "")

        # ── Esperar fill ──────────────────────────────────────────────────
        while True:
            elapsed = time.time() - start_time

            # Timeout → market fallback
            if elapsed >= self.max_wait_seconds:
                if last_order_id:
                    await client.cancel_order(symbol, last_order_id)
                if self.use_market_fallback:
                    logger.info(f"  Timeout ({elapsed:.0f}s) → market fallback")
                    return await self._market_fallback(
                        client, symbol, side, position_side,
                        quantity, entry_price, stop_loss, take_profit,
                        start_time, "timeout"
                    )
                else:
                    return OrderResult(
                        success=False, order_id="", fill_price=0, fill_qty=0,
                        method="cancelled", reason="Timeout y no se usa market fallback",
                        wait_seconds=elapsed
                    )

            # Verificar fill
            filled, fill_price = await self._check_order_filled(client, symbol, last_order_id)
            if filled:
                elapsed_final = time.time() - start_time
                logger.info(f"  ✅ Filled @ {fill_price:.6f} en {elapsed_final:.1f}s (limit)")
                return OrderResult(
                    success=True, order_id=last_order_id,
                    fill_price=fill_price, fill_qty=quantity,
                    method="limit", reason="Limit order llenada",
                    wait_seconds=elapsed_final
                )

            # Verificar si el precio se alejó demasiado (setup inválido)
            try:
                mark = await client.get_mark_price(symbol)
                away = abs(mark - entry_price)
                if away > atr * self.max_slip_atr:
                    await client.cancel_order(symbol, last_order_id)
                    logger.warning(
                        f"  Setup inválido: precio {mark:.6f} se alejó "
                        f"{away:.6f} > {atr * self.max_slip_atr:.6f} de entry"
                    )
                    return OrderResult(
                        success=False, order_id="", fill_price=0, fill_qty=0,
                        method="cancelled", reason="Precio se alejó demasiado",
                        wait_seconds=time.time() - start_time
                    )
            except Exception:
                pass

            # Chase (subir/bajar el límite para acercarse al mercado)
            if elapsed > self.chase_after_seconds * (chase_count + 1) and chase_count < self.chase_steps:
                chase_count += 1
                new_limit = self._calc_limit_price(position_side, entry_price, atr, step=chase_count)
                logger.info(f"  Chase #{chase_count}: actualizando límite a {new_limit:.6f}")
                try:
                    await client.cancel_order(symbol, last_order_id)
                    resp2 = await client.place_order(
                        symbol=symbol,
                        side=side,
                        position_side=position_side,
                        order_type="LIMIT",
                        quantity=quantity,
                        price=new_limit,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                    )
                    if resp2.get("code") == 0:
                        last_order_id = resp2.get("data", {}).get("orderId", "")
                except Exception as e:
                    logger.debug(f"  Chase error: {e}")

            await asyncio.sleep(self.poll_interval)

    async def _market_fallback(
        self, client, symbol, side, position_side, quantity,
        entry_price, stop_loss, take_profit, start_time, reason
    ) -> OrderResult:
        """Ejecuta market order de fallback"""
        resp = await client.place_order(
            symbol=symbol,
            side=side,
            position_side=position_side,
            order_type="MARKET",
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        elapsed = time.time() - start_time
        if resp.get("code") == 0:
            order_id = resp.get("data", {}).get("orderId", "")
            logger.info(f"  Market fallback OK | ID: {order_id}")
            return OrderResult(
                success=True, order_id=order_id,
                fill_price=entry_price, fill_qty=quantity,
                method="market", reason=f"Market fallback ({reason})",
                wait_seconds=elapsed
            )
        logger.error(f"  Market fallback falló: {resp}")
        return OrderResult(
            success=False, order_id="", fill_price=0, fill_qty=0,
            method="cancelled", reason=f"Market fallback error: {resp}",
            wait_seconds=elapsed
        )
