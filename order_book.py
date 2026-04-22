"""
Order Book Analyzer — Detección de Muros, CVD, Absorption
==========================================================
Analiza el libro de órdenes en tiempo real para detectar:
  - Bid/Ask walls (muros grandes)
  - Imbalance ratio (bid vs ask volume)
  - CVD (Cumulative Volume Delta)
  - Absorption signals (grandes órdenes absorbiendo flujo)
  - Bid/Ask delta percentages
"""

import logging
import numpy as np
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime, timedelta

log = logging.getLogger("ORDER_BOOK")


@dataclass
class Wall:
    """Representa un muro de órdenes (bid o ask)."""
    price: float
    volume: float
    distance_pct: float  # Distancia % del precio actual


@dataclass
class OrderBookSnapshot:
    """Snapshot del order book con análisis."""
    symbol: str
    timestamp: datetime
    
    # Precios
    best_bid: float
    best_ask: float
    mid_price: float
    spread_pct: float
    
    # Volúmenes
    total_bid_volume: float
    total_ask_volume: float
    imbalance_ratio: float  # bid_vol / ask_vol
    
    # Bias direccional
    bias: str  # "BULLISH", "BEARISH", "NEUTRAL"
    
    # Muros detectados
    bid_walls: List[Wall]
    ask_walls: List[Wall]
    
    # Delta percentages (cambio respecto al snapshot anterior)
    bid_delta_pct: float
    ask_delta_pct: float
    
    # CVD
    cvd: float
    cvd_pct: float  # CVD normalizado 0-100%
    
    # Absorption signal
    absorption_signal: bool
    absorption_side: Optional[str]  # "BID" o "ASK"
    
    # Metadata
    depth_levels: int


class OrderBookAnalyzer:
    """Analizador de order book en tiempo real."""
    
    def __init__(self, client, depth_levels: int = 20):
        self.client = client
        self.depth_levels = depth_levels
        
        # Cache de snapshots (último por símbolo)
        self._cache: dict[str, OrderBookSnapshot] = {}
        
        # Historial de CVD (para calcular delta)
        self._cvd_history: dict[str, List[tuple[datetime, float]]] = {}
        
        # Configuración
        self.wall_threshold_pct = 2.0  # Muro = orden 2x más grande que promedio
        self.absorption_threshold = 3.0  # Absorción = 3x volumen normal
        self.imbalance_strong = 1.5  # Ratio > 1.5 = fuerte imbalance
    
    # ------------------------------------------------------------------ #
    #  API Pública
    # ------------------------------------------------------------------ #
    async def analyze(self, symbol: str) -> Optional[OrderBookSnapshot]:
        """
        Analiza el order book del símbolo y devuelve snapshot.
        """
        try:
            # Obtener order book depth del exchange
            ob_data = await self._fetch_order_book(symbol)
            if not ob_data:
                return None
            
            # Parsear bids y asks
            bids = ob_data.get("bids", [])
            asks = ob_data.get("asks", [])
            
            if not bids or not asks:
                return None
            
            # Calcular métricas básicas
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            mid_price = (best_bid + best_ask) / 2
            spread_pct = ((best_ask - best_bid) / mid_price) * 100
            
            # Volúmenes totales
            total_bid_vol = sum(float(b[1]) for b in bids)
            total_ask_vol = sum(float(a[1]) for a in asks)
            
            # Imbalance ratio
            imbalance = total_bid_vol / total_ask_vol if total_ask_vol > 0 else 1.0
            
            # Bias direccional
            if imbalance >= self.imbalance_strong:
                bias = "BULLISH"
            elif imbalance <= (1 / self.imbalance_strong):
                bias = "BEARISH"
            else:
                bias = "NEUTRAL"
            
            # Detectar muros
            bid_walls = self._detect_walls(bids, mid_price, "BID")
            ask_walls = self._detect_walls(asks, mid_price, "ASK")
            
            # Calcular delta (cambio respecto al snapshot anterior)
            bid_delta_pct, ask_delta_pct = self._calculate_deltas(
                symbol, total_bid_vol, total_ask_vol
            )
            
            # CVD
            cvd, cvd_pct = self._calculate_cvd(symbol, total_bid_vol, total_ask_vol)
            
            # Absorption signal
            absorption_signal, absorption_side = self._detect_absorption(
                symbol, bid_walls, ask_walls, total_bid_vol, total_ask_vol
            )
            
            # Crear snapshot
            snapshot = OrderBookSnapshot(
                symbol=symbol,
                timestamp=datetime.utcnow(),
                best_bid=best_bid,
                best_ask=best_ask,
                mid_price=mid_price,
                spread_pct=spread_pct,
                total_bid_volume=total_bid_vol,
                total_ask_volume=total_ask_vol,
                imbalance_ratio=imbalance,
                bias=bias,
                bid_walls=bid_walls,
                ask_walls=ask_walls,
                bid_delta_pct=bid_delta_pct,
                ask_delta_pct=ask_delta_pct,
                cvd=cvd,
                cvd_pct=cvd_pct,
                absorption_signal=absorption_signal,
                absorption_side=absorption_side,
                depth_levels=len(bids),
            )
            
            # Guardar en cache
            self._cache[symbol] = snapshot
            
            log.debug(f"{symbol}: bias={bias}  imb={imbalance:.2f}  "
                     f"cvd={cvd_pct:.0f}%  walls=B{len(bid_walls)}/A{len(ask_walls)}")
            
            return snapshot
            
        except Exception as e:
            log.warning(f"Error analizando OB {symbol}: {e}")
            return None
    
    def get_cached(self, symbol: str) -> Optional[OrderBookSnapshot]:
        """Devuelve el último snapshot cacheado (sin consultar API)."""
        return self._cache.get(symbol)
    
    # ------------------------------------------------------------------ #
    #  Métodos Internos
    # ------------------------------------------------------------------ #
    async def _fetch_order_book(self, symbol: str) -> Optional[dict]:
        """Obtiene order book del exchange."""
        try:
            # BingX Perpetual Futures API
            # Endpoint: /openApi/swap/v2/quote/depth
            data = await self.client._get(
                "/openApi/swap/v2/quote/depth",
                {"symbol": symbol, "limit": self.depth_levels}
            )
            
            if data.get("code") != 0:
                return None
            
            return data.get("data", {})
            
        except Exception as e:
            log.warning(f"Fetch OB {symbol}: {e}")
            return None
    
    def _detect_walls(
        self, 
        orders: List[List], 
        ref_price: float, 
        side: str
    ) -> List[Wall]:
        """
        Detecta muros (órdenes anormalmente grandes).
        
        Un muro es una orden que es wall_threshold_pct veces más grande
        que el volumen promedio en ese nivel.
        """
        if not orders:
            return []
        
        volumes = [float(o[1]) for o in orders]
        avg_volume = np.mean(volumes)
        
        walls = []
        for price_str, volume_str in orders:
            price = float(price_str)
            volume = float(volume_str)
            
            # Si el volumen es significativamente mayor al promedio
            if volume >= avg_volume * self.wall_threshold_pct:
                distance_pct = abs((price - ref_price) / ref_price) * 100
                walls.append(Wall(
                    price=price,
                    volume=volume,
                    distance_pct=distance_pct
                ))
        
        # Ordenar por volumen descendente
        walls.sort(key=lambda w: w.volume, reverse=True)
        
        # Retornar solo los top 5
        return walls[:5]
    
    def _calculate_deltas(
        self, 
        symbol: str, 
        current_bid_vol: float, 
        current_ask_vol: float
    ) -> tuple[float, float]:
        """
        Calcula el cambio porcentual en bid/ask volume respecto al
        snapshot anterior.
        """
        prev_snapshot = self._cache.get(symbol)
        
        if not prev_snapshot:
            return 0.0, 0.0
        
        # Delta bid
        if prev_snapshot.total_bid_volume > 0:
            bid_delta = ((current_bid_vol - prev_snapshot.total_bid_volume) / 
                        prev_snapshot.total_bid_volume) * 100
        else:
            bid_delta = 0.0
        
        # Delta ask
        if prev_snapshot.total_ask_volume > 0:
            ask_delta = ((current_ask_vol - prev_snapshot.total_ask_volume) / 
                        prev_snapshot.total_ask_volume) * 100
        else:
            ask_delta = 0.0
        
        return bid_delta, ask_delta
    
    def _calculate_cvd(
        self, 
        symbol: str, 
        bid_vol: float, 
        ask_vol: float
    ) -> tuple[float, float]:
        """
        Calcula CVD (Cumulative Volume Delta).
        
        CVD = suma acumulada de (bid_volume - ask_volume)
        CVD_pct = CVD normalizado a escala 0-100%
        """
        # Obtener historial
        if symbol not in self._cvd_history:
            self._cvd_history[symbol] = []
        
        history = self._cvd_history[symbol]
        
        # Delta actual
        delta = bid_vol - ask_vol
        
        # CVD acumulado
        if history:
            last_cvd = history[-1][1]
            cvd = last_cvd + delta
        else:
            cvd = delta
        
        # Añadir al historial
        now = datetime.utcnow()
        history.append((now, cvd))
        
        # Limpiar historial antiguo (mantener últimas 24h)
        cutoff = now - timedelta(hours=24)
        history[:] = [(t, v) for t, v in history if t >= cutoff]
        
        # Normalizar CVD a porcentaje (0-100%)
        # Usando min-max scaling sobre la ventana de 24h
        if len(history) > 1:
            cvd_values = [v for _, v in history]
            cvd_min = min(cvd_values)
            cvd_max = max(cvd_values)
            
            if cvd_max > cvd_min:
                cvd_pct = ((cvd - cvd_min) / (cvd_max - cvd_min)) * 100
            else:
                cvd_pct = 50.0
        else:
            cvd_pct = 50.0
        
        return cvd, cvd_pct
    
    def _detect_absorption(
        self,
        symbol: str,
        bid_walls: List[Wall],
        ask_walls: List[Wall],
        total_bid_vol: float,
        total_ask_vol: float
    ) -> tuple[bool, Optional[str]]:
        """
        Detecta señales de absorción.
        
        Absorción = muros grandes que "absorben" el flujo de órdenes
        del lado opuesto, indicando acumulación/distribución.
        """
        # Calcular volumen promedio de los muros
        if bid_walls:
            max_bid_wall = max(w.volume for w in bid_walls)
        else:
            max_bid_wall = 0
        
        if ask_walls:
            max_ask_wall = max(w.volume for w in ask_walls)
        else:
            max_ask_wall = 0
        
        # Absorción en bid (acumulación alcista)
        if max_bid_wall > total_ask_vol * self.absorption_threshold:
            return True, "BID"
        
        # Absorción en ask (distribución bajista)
        if max_ask_wall > total_bid_vol * self.absorption_threshold:
            return True, "ASK"
        
        return False, None
