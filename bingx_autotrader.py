#!/usr/bin/env python3
"""
BingX API Client - Ejecutor de Trades Automático
Conecta con BingX y ejecuta LONG/SHORT automáticamente
"""

import os
import hmac
import hashlib
import requests
import time
import json
import logging
from datetime import datetime
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()


class BingXAutoTrader:
    """Cliente para ejecutar trades en BingX automáticamente"""
    
    def __init__(self):
        """Inicializar cliente BingX"""
        self.api_key = os.getenv('BINGX_API_KEY', '')
        self.api_secret = os.getenv('BINGX_API_SECRET', '')
        self.base_url = "https://open-api.bingx.com"
        
        self.position_size = float(os.getenv('MAX_POSITION_SIZE', '100'))
        self.leverage = int(os.getenv('LEVERAGE', '2'))  # 2x apalancamiento
        self.stop_loss_pct = float(os.getenv('STOP_LOSS_PCT', '1.0'))
        
        self.open_positions = {}
        
        logger.info("="*80)
        logger.info("🤖 BingX Auto Trader")
        logger.info(f"📊 Position Size: ${self.position_size}")
        logger.info(f"⚡ Leverage: {self.leverage}x")
        logger.info(f"🛑 Stop Loss: {self.stop_loss_pct}%")
        logger.info("="*80)
    
    def _get_timestamp(self):
        """Obtener timestamp en milisegundos"""
        return str(int(time.time() * 1000))
    
    def _sign_request(self, payload):
        """Firmar request con HMAC SHA256"""
        if not self.api_secret:
            logger.warning("⚠️ Sin API Secret - operaciones en modo lectura")
            return None
        
        message = json.dumps(payload)
        signature = hmac.new(
            self.api_secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _make_request(self, method, endpoint, payload=None):
        """Hacer request a BingX"""
        try:
            url = f"{self.base_url}{endpoint}"
            headers = {
                'Content-Type': 'application/json',
                'X-BX-APIKEY': self.api_key
            }
            
            if payload:
                payload['timestamp'] = self._get_timestamp()
                signature = self._sign_request(payload)
                if signature:
                    headers['X-BX-SIGN'] = signature
            
            if method == 'GET':
                response = requests.get(url, headers=headers, json=payload, timeout=10)
            elif method == 'POST':
                response = requests.post(url, headers=headers, json=payload, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"❌ Error BingX: {response.status_code} - {response.text}")
                return None
        
        except Exception as e:
            logger.error(f"❌ Error en request: {e}")
            return None
    
    def get_balance(self):
        """Obtener balance disponible"""
        payload = {'email': os.getenv('BINGX_EMAIL', '')}
        response = self._make_request('GET', '/openApi/swap/v2/user/balance', payload)
        
        if response and response.get('code') == 0:
            balances = response.get('data', {})
            usdt_balance = balances.get('USDT', 0)
            logger.info(f"💰 Balance USDT: ${usdt_balance:.2f}")
            return usdt_balance
        
        logger.warning("⚠️ No se pudo obtener balance")
        return 0
    
    def open_long(self, symbol, entry_price, tp1, tp2, sl):
        """Abrir posición LONG"""
        try:
            # Calcular cantidad
            quantity = self.position_size / entry_price
            
            payload = {
                'symbol': symbol,
                'side': 'BUY',
                'positionSide': 'LONG',
                'type': 'LIMIT',
                'price': entry_price,
                'quantity': quantity,
                'leverage': self.leverage
            }
            
            response = self._make_request('POST', '/openApi/swap/v2/trade/order', payload)
            
            if response and response.get('code') == 0:
                order_id = response.get('data', {}).get('orderId')
                logger.info(f"🟢 LONG ABIERTO: {symbol} @ ${entry_price:.4f}")
                logger.info(f"   Cantidad: {quantity:.4f}")
                logger.info(f"   Order ID: {order_id}")
                logger.info(f"   TP1: ${tp1:.4f} | TP2: ${tp2:.4f}")
                logger.info(f"   SL: ${sl:.4f}")
                
                # Registrar posición
                self.open_positions[symbol] = {
                    'direction': 'LONG',
                    'entry': entry_price,
                    'tp1': tp1,
                    'tp2': tp2,
                    'sl': sl,
                    'quantity': quantity,
                    'order_id': order_id,
                    'timestamp': datetime.now().isoformat()
                }
                
                return True
            else:
                logger.error(f"❌ Error abriendo LONG: {response}")
                return False
        
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return False
    
    def open_short(self, symbol, entry_price, tp1, tp2, sl):
        """Abrir posición SHORT"""
        try:
            quantity = self.position_size / entry_price
            
            payload = {
                'symbol': symbol,
                'side': 'SELL',
                'positionSide': 'SHORT',
                'type': 'LIMIT',
                'price': entry_price,
                'quantity': quantity,
                'leverage': self.leverage
            }
            
            response = self._make_request('POST', '/openApi/swap/v2/trade/order', payload)
            
            if response and response.get('code') == 0:
                order_id = response.get('data', {}).get('orderId')
                logger.info(f"🔴 SHORT ABIERTO: {symbol} @ ${entry_price:.4f}")
                logger.info(f"   Cantidad: {quantity:.4f}")
                logger.info(f"   Order ID: {order_id}")
                logger.info(f"   TP1: ${tp1:.4f} | TP2: ${tp2:.4f}")
                logger.info(f"   SL: ${sl:.4f}")
                
                self.open_positions[symbol] = {
                    'direction': 'SHORT',
                    'entry': entry_price,
                    'tp1': tp1,
                    'tp2': tp2,
                    'sl': sl,
                    'quantity': quantity,
                    'order_id': order_id,
                    'timestamp': datetime.now().isoformat()
                }
                
                return True
            else:
                logger.error(f"❌ Error abriendo SHORT: {response}")
                return False
        
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return False
    
    def set_take_profit(self, symbol, order_id, tp_price):
        """Establecer take profit"""
        try:
            payload = {
                'orderId': order_id,
                'takeProfitPrice': tp_price
            }
            
            response = self._make_request('POST', '/openApi/swap/v2/trade/setTakeProfit', payload)
            
            if response and response.get('code') == 0:
                logger.info(f"✅ TP establecido en ${tp_price:.4f}")
                return True
            
            return False
        except:
            return False
    
    def set_stop_loss(self, symbol, order_id, sl_price):
        """Establecer stop loss"""
        try:
            payload = {
                'orderId': order_id,
                'stopLossPrice': sl_price
            }
            
            response = self._make_request('POST', '/openApi/swap/v2/trade/setStopLoss', payload)
            
            if response and response.get('code') == 0:
                logger.info(f"✅ SL establecido en ${sl_price:.4f}")
                return True
            
            return False
        except:
            return False
    
    def close_position(self, symbol, direction):
        """Cerrar posición"""
        try:
            side = 'SELL' if direction == 'LONG' else 'BUY'
            
            if symbol in self.open_positions:
                pos = self.open_positions[symbol]
                quantity = pos['quantity']
            else:
                logger.warning(f"⚠️ Posición no registrada: {symbol}")
                return False
            
            payload = {
                'symbol': symbol,
                'side': side,
                'type': 'MARKET',
                'quantity': quantity,
                'positionSide': direction
            }
            
            response = self._make_request('POST', '/openApi/swap/v2/trade/order', payload)
            
            if response and response.get('code') == 0:
                logger.info(f"🔄 Posición cerrada: {symbol}")
                del self.open_positions[symbol]
                return True
            
            return False
        
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return False
    
    def get_open_positions(self):
        """Obtener posiciones abiertas"""
        return self.open_positions
    
    def execute_trade(self, symbol, direction, entry_price, tp1, tp2, sl):
        """Ejecutar trade completo (LONG o SHORT)"""
        
        logger.info(f"\n{'='*80}")
        logger.info(f"📊 EJECUTANDO TRADE: {symbol}")
        logger.info(f"{'='*80}")
        
        if direction == 'LONG':
            success = self.open_long(symbol, entry_price, tp1, tp2, sl)
        else:
            success = self.open_short(symbol, entry_price, tp1, tp2, sl)
        
        if success and symbol in self.open_positions:
            pos = self.open_positions[symbol]
            
            # Establecer TP y SL
            self.set_take_profit(symbol, pos['order_id'], tp1)
            self.set_stop_loss(symbol, pos['order_id'], sl)
            
            return True
        
        return False


# Ejemplo de uso
if __name__ == "__main__":
    trader = BingXAutoTrader()
    
    # Verificar balance
    balance = trader.get_balance()
    
    # Ejemplo: Abrir LONG en BTC
    # trader.execute_trade('BTC-USDT', 'LONG', 45000, 45500, 46000, 44500)
    
    # Ejemplo: Abrir SHORT en ETH
    # trader.execute_trade('ETH-USDT', 'SHORT', 2500, 2475, 2450, 2525)
    
    logger.info("✅ BingX Auto Trader listo")
