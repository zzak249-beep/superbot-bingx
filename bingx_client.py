"""
Cliente BingX API - Futuros Perpetuos (VERSIÓN ARREGLADA)
Autenticación HMAC, órdenes, posiciones
"""

import os
import time
import hmac
import hashlib
import requests
import logging
import asyncio
from typing import Optional, List, Dict
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


class BingXClient:
    """Cliente para BingX API"""
    
    BASE_URL = "https://open-api.bingx.com"
    
    def __init__(self):
        """Inicializar con credenciales"""
        self.api_key = os.getenv('BINGX_API_KEY', '')
        self.api_secret = os.getenv('BINGX_API_SECRET', '')
        
        if not self.api_key or not self.api_secret:
            logger.warning("⚠️ Credenciales BingX no configuradas")
        
        self.session = requests.Session()
        self.session.headers.update({
            'X-BX-APIKEY': str(self.api_key),
            'Content-Type': 'application/json'
        })
    
    def _generate_signature(self, params: dict) -> str:
        """Generar firma HMAC"""
        try:
            if not self.api_secret:
                return ""
            
            # Convertir a string si no lo es
            secret = str(self.api_secret)
            
            # Crear query string
            query_string = urlencode(sorted(params.items()))
            
            # Generar signature
            signature = hmac.new(
                secret.encode('utf-8'),
                query_string.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            return signature
            
        except Exception as e:
            logger.error(f"Error generando signature: {e}")
            return ""
    
    async def get_klines(self, symbol: str, interval: str, limit: int = 100) -> List[Dict]:
        """Obtener velas históricas"""
        try:
            endpoint = "/openApi/swap/v2/quote/klines"
            
            params = {
                'symbol': symbol,
                'interval': interval,
                'limit': str(limit),
                'timestamp': str(int(time.time() * 1000))
            }
            
            params['signature'] = self._generate_signature(params)
            
            response = self.session.get(
                f"{self.BASE_URL}{endpoint}",
                params=params,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('code') == 0:
                    klines = data.get('data', [])
                    
                    candles = []
                    for k in klines:
                        try:
                            candles.append({
                                'timestamp': int(k[0]),
                                'open': float(k[1]),
                                'high': float(k[2]),
                                'low': float(k[3]),
                                'close': float(k[4]),
                                'volume': float(k[5])
                            })
                        except (ValueError, TypeError, IndexError):
                            continue
                    
                    return candles
                else:
                    logger.error(f"API Error: {data.get('msg')}")
                    return []
            else:
                logger.error(f"HTTP Error {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"Error obteniendo velas: {str(e)}")
            return []
    
    async def get_position(self, symbol: str) -> Optional[Dict]:
        """Obtener posición actual"""
        try:
            endpoint = "/openApi/swap/v2/user/positions"
            
            params = {
                'symbol': symbol,
                'timestamp': str(int(time.time() * 1000))
            }
            
            params['signature'] = self._generate_signature(params)
            
            response = self.session.get(
                f"{self.BASE_URL}{endpoint}",
                params=params,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('code') == 0:
                    positions = data.get('data', [])
                    
                    for pos in positions:
                        try:
                            amount = float(pos.get('positionAmt', 0))
                            if amount != 0:
                                return {
                                    'symbol': pos.get('symbol', symbol),
                                    'side': 'LONG' if amount > 0 else 'SHORT',
                                    'size': abs(amount),
                                    'entry_price': float(pos.get('avgPrice', 0)),
                                    'unrealized_pnl': float(pos.get('unrealizedProfit', 0))
                                }
                        except (ValueError, TypeError, KeyError):
                            continue
                    
                    return None
                else:
                    logger.error(f"API Error: {data.get('msg')}")
                    return None
            else:
                logger.error(f"HTTP Error {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error obteniendo posición: {str(e)}")
            return None
    
    async def place_order(self,
                         symbol: str,
                         side: str,
                         quantity: float,
                         price: Optional[float] = None,
                         stop_loss: Optional[float] = None,
                         take_profit: Optional[float] = None) -> Optional[Dict]:
        """Colocar orden con SL/TP"""
        try:
            endpoint = "/openApi/swap/v2/trade/order"
            
            params = {
                'symbol': symbol,
                'side': side,
                'type': 'MARKET' if price is None else 'LIMIT',
                'quantity': str(quantity),
                'timestamp': str(int(time.time() * 1000))
            }
            
            if price:
                params['price'] = str(price)
            
            params['signature'] = self._generate_signature(params)
            
            response = self.session.post(
                f"{self.BASE_URL}{endpoint}",
                params=params,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('code') == 0:
                    order_id = data.get('data', {}).get('orderId', 'unknown')
                    
                    if stop_loss:
                        await self._place_stop_loss(symbol, side, quantity, stop_loss)
                    
                    if take_profit:
                        await self._place_take_profit(symbol, side, quantity, take_profit)
                    
                    return {
                        'order_id': order_id,
                        'symbol': symbol,
                        'side': side,
                        'quantity': quantity
                    }
                else:
                    logger.error(f"API Error: {data.get('msg')}")
                    return None
            else:
                logger.error(f"HTTP Error {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error colocando orden: {str(e)}")
            return None
    
    async def _place_stop_loss(self, symbol: str, side: str, quantity: float, stop_price: float):
        """Colocar SL"""
        try:
            endpoint = "/openApi/swap/v2/trade/order"
            sl_side = 'SELL' if side == 'BUY' else 'BUY'
            
            params = {
                'symbol': symbol,
                'side': sl_side,
                'type': 'STOP_MARKET',
                'quantity': str(quantity),
                'stopPrice': str(stop_price),
                'timestamp': str(int(time.time() * 1000))
            }
            
            params['signature'] = self._generate_signature(params)
            
            response = self.session.post(
                f"{self.BASE_URL}{endpoint}",
                params=params,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    logger.info(f"✅ SL en {stop_price}")
                else:
                    logger.error(f"Error SL: {data.get('msg')}")
                    
        except Exception as e:
            logger.error(f"Error SL: {str(e)}")
    
    async def _place_take_profit(self, symbol: str, side: str, quantity: float, tp_price: float):
        """Colocar TP"""
        try:
            endpoint = "/openApi/swap/v2/trade/order"
            tp_side = 'SELL' if side == 'BUY' else 'BUY'
            
            params = {
                'symbol': symbol,
                'side': tp_side,
                'type': 'TAKE_PROFIT_MARKET',
                'quantity': str(quantity),
                'stopPrice': str(tp_price),
                'timestamp': str(int(time.time() * 1000))
            }
            
            params['signature'] = self._generate_signature(params)
            
            response = self.session.post(
                f"{self.BASE_URL}{endpoint}",
                params=params,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    logger.info(f"✅ TP en {tp_price}")
                else:
                    logger.error(f"Error TP: {data.get('msg')}")
                    
        except Exception as e:
            logger.error(f"Error TP: {str(e)}")
    
    async def close_position(self, symbol: str, side: str) -> Optional[Dict]:
        """Cerrar posición"""
        try:
            position = await self.get_position(symbol)
            
            if not position:
                logger.warning("Sin posición abierta")
                return None
            
            close_side = 'SELL' if side == 'LONG' else 'BUY'
            quantity = position['size']
            
            endpoint = "/openApi/swap/v2/trade/order"
            
            params = {
                'symbol': symbol,
                'side': close_side,
                'type': 'MARKET',
                'quantity': str(quantity),
                'timestamp': str(int(time.time() * 1000))
            }
            
            params['signature'] = self._generate_signature(params)
            
            response = self.session.post(
                f"{self.BASE_URL}{endpoint}",
                params=params,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('code') == 0:
                    return {
                        'closed': True,
                        'pnl': position.get('unrealized_pnl', 0)
                    }
                else:
                    logger.error(f"API Error: {data.get('msg')}")
                    return None
            else:
                logger.error(f"HTTP Error {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error cerrando: {str(e)}")
            return None
