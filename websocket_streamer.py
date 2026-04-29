"""
ULTRA-FAST WEBSOCKET STREAMER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Ventajas vs REST polling:
  ✓ Latencia: 10-30ms (vs 200-500ms REST)
  ✓ Sin rate limits (stream continuo)
  ✓ Updates en tiempo real (no esperar cada 60s)
  ✓ Menor carga CPU (push vs poll)
  ✓ Detecta señales ANTES (no espera close de vela)

Streams disponibles:
  • Kline (OHLCV en tiempo real)
  • Trade (cada operación ejecutada)
  • Ticker (precio 24h stats)
  • Book Depth (orderbook updates)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import asyncio
import json
import time
from typing import Dict, Callable, Optional, List
from collections import deque
import websockets
from loguru import logger


class BingXWebSocketStreamer:
    """
    WebSocket streamer para BingX.
    Mantiene conexiones persistentes y distribuye datos a callbacks.
    """
    
    def __init__(self, testnet: bool = False):
        self.testnet = testnet
        self.ws_url = "wss://open-api.bingx.com/market" if not testnet else \
                      "wss://open-api-vst.bingx.com/market"
        
        # Conexiones activas por tipo de stream
        self.connections: Dict[str, websockets.WebSocketClientProtocol] = {}
        
        # Callbacks registrados
        self.callbacks: Dict[str, List[Callable]] = {
            "kline": [],
            "trade": [],
            "ticker": [],
            "depth": [],
        }
        
        # Buffer de últimos datos (para acceso sincrónico)
        self.last_klines: Dict[str, dict] = {}  # symbol_interval -> kline
        self.last_prices: Dict[str, float] = {}  # symbol -> price
        
        # Stats
        self.messages_received = 0
        self.reconnect_count = 0
        self.start_time = time.time()
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SUBSCRIPTION MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def subscribe_klines(self, symbols: List[str], interval: str = "15m"):
        """
        Suscribe a klines (OHLCV) en tiempo real.
        
        Args:
            symbols: Lista de símbolos (ej: ["BTC-USDT", "ETH-USDT"])
            interval: Timeframe (1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d)
        """
        stream_name = f"kline_{interval}"
        
        # Construir mensaje de suscripción
        topics = [f"{symbol.replace('-', '')}@kline_{interval}" for symbol in symbols]
        
        subscribe_msg = {
            "id": f"sub_{stream_name}_{int(time.time())}",
            "reqType": "sub",
            "dataType": topics,
        }
        
        await self._connect_and_subscribe(stream_name, subscribe_msg, self._handle_kline)
    
    async def subscribe_trades(self, symbols: List[str]):
        """Suscribe a stream de trades (cada operación ejecutada)."""
        topics = [f"{symbol.replace('-', '')}@trade" for symbol in symbols]
        
        subscribe_msg = {
            "id": f"sub_trade_{int(time.time())}",
            "reqType": "sub",
            "dataType": topics,
        }
        
        await self._connect_and_subscribe("trade", subscribe_msg, self._handle_trade)
    
    async def subscribe_tickers(self, symbols: List[str]):
        """Suscribe a ticker 24h (precio + volumen + cambio 24h)."""
        topics = [f"{symbol.replace('-', '')}@ticker" for symbol in symbols]
        
        subscribe_msg = {
            "id": f"sub_ticker_{int(time.time())}",
            "reqType": "sub",
            "dataType": topics,
        }
        
        await self._connect_and_subscribe("ticker", subscribe_msg, self._handle_ticker)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CONNECTION HANDLING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def _connect_and_subscribe(self, stream_name: str, subscribe_msg: dict,
                                     handler: Callable):
        """Conecta al WebSocket y suscribe."""
        try:
            ws = await websockets.connect(self.ws_url, ping_interval=20, ping_timeout=10)
            self.connections[stream_name] = ws
            
            # Enviar mensaje de suscripción
            await ws.send(json.dumps(subscribe_msg))
            logger.success(f"✓ Suscrito a {stream_name}")
            
            # Iniciar loop de recepción
            asyncio.create_task(self._receive_loop(stream_name, ws, handler))
            
        except Exception as e:
            logger.error(f"Error conectando WebSocket {stream_name}: {e}")
    
    async def _receive_loop(self, stream_name: str, ws, handler: Callable):
        """Loop principal de recepción de mensajes."""
        try:
            async for message in ws:
                try:
                    data = json.loads(message)
                    self.messages_received += 1
                    
                    # Llamar al handler correspondiente
                    await handler(data)
                    
                    # Ejecutar callbacks registrados
                    for callback in self.callbacks.get(stream_name.split('_')[0], []):
                        try:
                            if asyncio.iscoroutinefunction(callback):
                                await callback(data)
                            else:
                                callback(data)
                        except Exception as e:
                            logger.warning(f"Callback error: {e}")
                
                except json.JSONDecodeError:
                    logger.warning(f"JSON decode error: {message[:100]}")
                except Exception as e:
                    logger.error(f"Error procesando mensaje: {e}")
        
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"WebSocket {stream_name} cerrado, reconectando...")
            self.reconnect_count += 1
            await asyncio.sleep(5)
            # TODO: Implementar reconexión automática
        
        except Exception as e:
            logger.error(f"Error en receive loop {stream_name}: {e}")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DATA HANDLERS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def _handle_kline(self, data: dict):
        """Procesa mensaje de kline."""
        if "data" not in data:
            return
        
        kline = data["data"]
        dataType = data.get("dataType", "")
        
        # Extraer símbolo e interval del dataType
        # Formato: "BTCUSDT@kline_15m"
        if "@kline_" not in dataType:
            return
        
        parts = dataType.split("@kline_")
        symbol = parts[0]  # BTCUSDT
        interval = parts[1]  # 15m
        
        # Normalizar símbolo (BTC-USDT)
        if len(symbol) >= 6:
            symbol = f"{symbol[:-4]}-{symbol[-4:]}"
        
        key = f"{symbol}_{interval}"
        
        # Guardar en buffer
        self.last_klines[key] = {
            "symbol": symbol,
            "interval": interval,
            "open": float(kline.get("o", 0)),
            "high": float(kline.get("h", 0)),
            "low": float(kline.get("l", 0)),
            "close": float(kline.get("c", 0)),
            "volume": float(kline.get("v", 0)),
            "timestamp": int(kline.get("T", 0)),
            "is_closed": kline.get("x", False),
        }
        
        # Actualizar last price
        self.last_prices[symbol] = float(kline.get("c", 0))
    
    async def _handle_trade(self, data: dict):
        """Procesa mensaje de trade."""
        if "data" not in data:
            return
        
        trade = data["data"]
        symbol = trade.get("s", "").replace("USDT", "-USDT")
        price = float(trade.get("p", 0))
        
        # Actualizar last price
        self.last_prices[symbol] = price
    
    async def _handle_ticker(self, data: dict):
        """Procesa mensaje de ticker 24h."""
        if "data" not in data:
            return
        
        ticker = data["data"]
        symbol = ticker.get("s", "").replace("USDT", "-USDT")
        price = float(ticker.get("c", 0))
        
        self.last_prices[symbol] = price
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PUBLIC API
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_latest_kline(self, symbol: str, interval: str) -> Optional[dict]:
        """Obtiene la última vela recibida (sincrónico)."""
        key = f"{symbol}_{interval}"
        return self.last_klines.get(key)
    
    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Obtiene el último precio recibido (sincrónico)."""
        return self.last_prices.get(symbol)
    
    def register_callback(self, stream_type: str, callback: Callable):
        """
        Registra un callback para recibir datos.
        
        Args:
            stream_type: "kline", "trade", "ticker", "depth"
            callback: Función que recibe dict con los datos
        """
        if stream_type in self.callbacks:
            self.callbacks[stream_type].append(callback)
    
    def stats(self) -> dict:
        """Estadísticas de conexión."""
        uptime = time.time() - self.start_time
        return {
            "uptime_seconds": uptime,
            "messages_received": self.messages_received,
            "reconnections": self.reconnect_count,
            "msg_per_second": self.messages_received / uptime if uptime > 0 else 0,
            "active_connections": len(self.connections),
            "symbols_tracked": len(self.last_prices),
        }
    
    async def close_all(self):
        """Cierra todas las conexiones."""
        for name, ws in self.connections.items():
            try:
                await ws.close()
                logger.info(f"Cerrado WebSocket: {name}")
            except:
                pass
        self.connections.clear()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EJEMPLO DE USO
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def example_usage():
    """Ejemplo de uso del WebSocket streamer."""
    
    streamer = BingXWebSocketStreamer(testnet=False)
    
    # Callback personalizado
    def on_kline_update(data):
        kline = data.get("data", {})
        symbol = data.get("dataType", "").split("@")[0]
        close = float(kline.get("c", 0))
        is_closed = kline.get("x", False)
        
        if is_closed:
            logger.info(f"🕯️ {symbol} cerró en {close:.4f}")
    
    # Registrar callback
    streamer.register_callback("kline", on_kline_update)
    
    # Suscribirse a múltiples símbolos
    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    await streamer.subscribe_klines(symbols, interval="15m")
    
    # Mantener vivo
    try:
        while True:
            await asyncio.sleep(30)
            
            # Mostrar stats cada 30s
            stats = streamer.stats()
            logger.info(
                f"📊 WS Stats: {stats['messages_received']} msgs | "
                f"{stats['msg_per_second']:.1f} msg/s | "
                f"{stats['symbols_tracked']} símbolos"
            )
            
            # Ejemplo: obtener último precio
            for symbol in symbols:
                price = streamer.get_latest_price(symbol)
                if price:
                    logger.info(f"{symbol}: ${price:,.2f}")
    
    except KeyboardInterrupt:
        logger.info("Cerrando WebSocket...")
        await streamer.close_all()


if __name__ == "__main__":
    asyncio.run(example_usage())
