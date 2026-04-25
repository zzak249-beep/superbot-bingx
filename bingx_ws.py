"""
exchange/bingx_ws.py — WebSocket para datos en tiempo real de BingX.
Velocidad máxima: streaming de klines + trades en vez de polling REST.
- Reconexión automática con backoff exponencial
- BingX comprime mensajes con gzip
- Mantiene buffer de velas en memoria para los indicadores

CORRECCIONES v2.5:
  [1] ping_interval=None — se desactiva el ping binario de la librería
      websockets porque BingX usa ping/pong a nivel de APLICACIÓN (texto
      "Ping" / "Pong"). Tener ambos activos hace que el servidor devuelva
      HTTP 200 en lugar de 101 y rechace el handshake.
  [2] Heartbeat manual — tarea asyncio que envía {"ping": ts} cada 20 s
      y espera respuesta. Si no llega en 10 s cierra la conexión para
      forzar reconexión limpia.
  [3] open_timeout=15 — evita que el connect() quede colgado indefinidamente.
  [4] Parser de mensajes reforzado para los distintos formatos de pong
      que devuelve BingX swap-market.
"""

import asyncio
import gzip
import json
import logging
import time
from collections import deque
from typing import Callable, Dict, List, Optional, Set

import websockets
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from config import config

log = logging.getLogger("bingx_ws")

INTERVAL_SECS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400,
}

_HEARTBEAT_INTERVAL = 20   # segundos entre pings
_HEARTBEAT_TIMEOUT  = 10   # segundos para recibir pong antes de reconectar


class KlineBuffer:
    """
    Buffer circular de velas OHLCV en memoria.
    Actualiza la vela actual con cada tick; al cerrar, añade nueva.
    """

    def __init__(self, symbol: str, interval: str, maxlen: int = 200):
        self.symbol   = symbol
        self.interval = interval
        self.maxlen   = maxlen
        self._data: deque          = deque(maxlen=maxlen)
        self._current: Optional[Dict] = None
        self.last_close_ts: int    = 0

    def seed(self, klines: List):
        """Inicializa con datos históricos REST."""
        self._data.clear()
        for k in klines:
            self._data.append({
                "ts": int(k[0]),
                "o":  float(k[1]),
                "h":  float(k[2]),
                "l":  float(k[3]),
                "c":  float(k[4]),
                "v":  float(k[5]),
                "closed": True,
            })
        if self._data:
            self.last_close_ts = self._data[-1]["ts"]

    def update_tick(self, tick: Dict):
        """
        Recibe un tick de kline del WebSocket y actualiza el buffer.
        tick keys: ts, o, h, l, c, v, closed (bool)
        """
        if tick["closed"]:
            self._data.append({**tick, "closed": True})
            self.last_close_ts = tick["ts"]
            self._current = None
        else:
            self._current = tick

    def to_arrays(self):
        """
        Devuelve (opens, highs, lows, closes, volumes) como listas Python.
        Incluye la vela abierta actual (sin confirmar) al final si existe.
        """
        candles = list(self._data)
        if self._current:
            candles.append(self._current)
        if not candles:
            return [], [], [], [], []
        opens  = [c["o"] for c in candles]
        highs  = [c["h"] for c in candles]
        lows   = [c["l"] for c in candles]
        closes = [c["c"] for c in candles]
        vols   = [c["v"] for c in candles]
        return opens, highs, lows, closes, vols

    def latest_close(self) -> float:
        if self._current:
            return self._current["c"]
        if self._data:
            return self._data[-1]["c"]
        return 0.0

    def ready(self, min_candles: int = 60) -> bool:
        return len(self._data) >= min_candles


class BingXWebSocket:
    """
    Gestiona una conexión WebSocket a BingX Swap (perpetual futures).
    Suscribe a klines de múltiples símbolos × timeframes.
    Llama a callbacks cuando una vela cierra.
    """

    def __init__(self):
        self.buffers: Dict[str, KlineBuffer]  = {}
        self._on_close_callbacks: List[Callable] = []
        self._subscriptions: Set[str]         = set()
        self._ws                              = None
        self._running                         = False
        self._pong_received                   = asyncio.Event()

    def add_buffer(self, symbol: str, interval: str,
                   maxlen: int = 200) -> KlineBuffer:
        key = f"{symbol}@kline_{interval}"
        if key not in self.buffers:
            self.buffers[key] = KlineBuffer(symbol, interval, maxlen)
        return self.buffers[key]

    def get_buffer(self, symbol: str, interval: str) -> Optional[KlineBuffer]:
        return self.buffers.get(f"{symbol}@kline_{interval}")

    def on_candle_close(self, callback: Callable):
        """Registra un callback async: callback(symbol, interval, buffer)."""
        self._on_close_callbacks.append(callback)

    # ── Envío ────────────────────────────────────────────────────────────────

    async def _send(self, msg):
        if self._ws:
            payload = json.dumps(msg) if isinstance(msg, dict) else msg
            try:
                await self._ws.send(payload)
            except Exception as e:
                log.warning(f"WS send error: {e}")

    async def _subscribe_all(self):
        for key in self.buffers:
            await self._send({
                "id":       str(int(time.time() * 1000)),
                "reqType":  "sub",
                "dataType": key,
            })
            log.debug(f"Subscribed: {key}")
            await asyncio.sleep(0.05)

    # ── Heartbeat manual ─────────────────────────────────────────────────────

    async def _heartbeat(self):
        """
        Envía un ping de aplicación cada _HEARTBEAT_INTERVAL segundos.
        BingX responde con {"pong": <ts>} o el texto "Pong".
        Si no responde en _HEARTBEAT_TIMEOUT segundos, cierra el socket
        para provocar una reconexión limpia.
        """
        await asyncio.sleep(_HEARTBEAT_INTERVAL)  # espera inicial
        while self._running and self._ws:
            ts = int(time.time() * 1000)
            self._pong_received.clear()
            await self._send({"ping": ts})
            log.debug(f"Ping enviado ts={ts}")

            try:
                await asyncio.wait_for(
                    self._pong_received.wait(),
                    timeout=_HEARTBEAT_TIMEOUT,
                )
                log.debug("Pong recibido ✓")
            except asyncio.TimeoutError:
                log.warning("Pong no recibido — cerrando WS para reconectar")
                try:
                    await self._ws.close()
                except Exception:
                    pass
                break

            await asyncio.sleep(_HEARTBEAT_INTERVAL)

    # ── Parser de kline ───────────────────────────────────────────────────────

    def _parse_kline_tick(self, data) -> Optional[Dict]:
        """
        Normaliza el payload de kline del WebSocket swap-market de BingX.
        Soporta tanto el formato dict directo como el anidado en 'c'.
        """
        if not data:
            return None

        # El swap-market a veces anida la vela bajo la clave 'c'
        k = data
        if isinstance(data, dict) and "c" in data and isinstance(data["c"], dict):
            k = data["c"]

        try:
            return {
                "ts":    int(k.get("t", k.get("T", k.get("startTime", 0)))),
                "o":     float(k.get("o", k.get("open",  0))),
                "h":     float(k.get("h", k.get("high",  0))),
                "l":     float(k.get("l", k.get("low",   0))),
                "c":     float(k.get("c", k.get("close", 0))),
                "v":     float(k.get("v", k.get("volume", 0))),
                # 'x'/'X' = vela cerrada; algunos streams usan 'confirm'
                "closed": bool(
                    k.get("x", k.get("X", k.get("confirm", False)))
                ),
            }
        except (TypeError, ValueError):
            return None

    # ── Handler de mensajes ───────────────────────────────────────────────────

    async def _handle_message(self, raw: bytes):
        try:
            # BingX comprime con gzip
            try:
                text = gzip.decompress(raw).decode("utf-8")
            except Exception:
                text = raw.decode("utf-8") if isinstance(raw, bytes) else raw

            msg = json.loads(text)

            # ── Ping / Pong ───────────────────────────────────────────────
            # BingX puede mandar ping de varias formas; respondemos a todas
            if msg == "Ping":
                await self._send("Pong")
                return
            if isinstance(msg, dict):
                # Pong que BingX devuelve a nuestro ping
                if "pong" in msg or msg.get("e") == "pong" or msg.get("type") == "pong":
                    self._pong_received.set()
                    return
                # Ping que BingX nos envía
                if "ping" in msg:
                    await self._send({"pong": msg["ping"]})
                    return
                if msg.get("e") == "ping" or msg.get("type") == "ping":
                    await self._send({"pong": msg.get("ts", "")})
                    return

            # ── Kline ─────────────────────────────────────────────────────
            data_type = msg.get("dataType", msg.get("e", ""))
            data      = msg.get("data", msg.get("k", {}))

            if "@kline_" not in str(data_type):
                return

            buf = self.buffers.get(data_type)
            if not buf:
                return

            tick = self._parse_kline_tick(data)
            if tick is None:
                log.warning(f"No se pudo parsear tick para {data_type}: {data}")
                return

            buf.update_tick(tick)

            if tick["closed"]:
                parts  = data_type.split("@kline_")
                symbol, interval = parts[0], parts[1]
                for cb in self._on_close_callbacks:
                    asyncio.create_task(cb(symbol, interval, buf))

        except Exception as e:
            log.error(f"WS message error: {e}", exc_info=True)

    # ── Loop principal ────────────────────────────────────────────────────────

    async def run(self):
        """Loop principal de reconexión con backoff exponencial."""
        self._running = True
        backoff = 1
        while self._running:
            hb_task = None
            try:
                log.info(f"Conectando WebSocket BingX: {config.WS_URL}")
                async with websockets.connect(
                    config.WS_URL,
                    # ── CORRECCIÓN PRINCIPAL ──────────────────────────────
                    # ping_interval=None desactiva los pings binarios del
                    # protocolo WS que genera la librería. BingX espera pings
                    # de APLICACIÓN (JSON {"ping": ts}), no frames binarios.
                    # Tener ambos activos provoca el rechazo HTTP 200.
                    ping_interval=None,
                    # ─────────────────────────────────────────────────────
                    open_timeout=15,
                    max_size=2 ** 20,
                ) as ws:
                    self._ws  = ws
                    backoff   = 1  # reset al conectar con éxito

                    await self._subscribe_all()
                    log.info("✅ WebSocket conectado y suscrito.")

                    # Arranca el heartbeat en paralelo
                    hb_task = asyncio.create_task(self._heartbeat())

                    async for msg in ws:
                        await self._handle_message(msg)

            except (ConnectionClosedOK, ConnectionClosedError) as e:
                log.warning(f"WS cerrado: {e}. Reconectando en {backoff}s...")
            except asyncio.TimeoutError:
                log.warning(f"WS timeout al conectar. Reconectando en {backoff}s...")
            except Exception as e:
                log.error(f"WS error: {e}. Reconectando en {backoff}s...")
            finally:
                if hb_task and not hb_task.done():
                    hb_task.cancel()
                    try:
                        await hb_task
                    except asyncio.CancelledError:
                        pass
                self._ws = None

            if self._running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()


# Instancia global
ws_client = BingXWebSocket()
