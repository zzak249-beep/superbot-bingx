#!/usr/bin/env python3
"""
🚀 BOT DE TRADING PROFESIONAL - VERSIÓN TODO-EN-UNO
Optimizado para Railway - Sin problemas de imports
"""

import os
import asyncio
import logging
import requests
import hmac
import hashlib
import time
import sys
from datetime import datetime
from urllib.parse import urlencode

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

def get_env_value(key, default, value_type='str'):
    """Obtener valor de entorno limpiando comillas"""
    value = os.getenv(key, str(default))
    # Limpiar comillas
    if isinstance(value, str):
        value = value.strip('"').strip("'")
    
    # Convertir tipo
    if value_type == 'int':
        return int(value)
    elif value_type == 'float':
        return float(value)
    elif value_type == 'bool':
        return value.lower() == 'true'
    return value


# Configuración
BINGX_API_KEY = os.getenv('BINGX_API_KEY', '')
BINGX_API_SECRET = os.getenv('BINGX_API_SECRET', '')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

AUTO_TRADING = get_env_value('AUTO_TRADING_ENABLED', 'false', 'bool')
MAX_POSITION_SIZE = get_env_value('MAX_POSITION_SIZE', '100', 'float')
LEVERAGE = get_env_value('LEVERAGE', '2', 'int')
TAKE_PROFIT_PCT = get_env_value('TAKE_PROFIT_PCT', '2.0', 'float')
STOP_LOSS_PCT = get_env_value('STOP_LOSS_PCT', '1.0', 'float')
MAX_OPEN_TRADES = get_env_value('MAX_OPEN_TRADES', '5', 'int')
CHECK_INTERVAL = get_env_value('CHECK_INTERVAL', '60', 'int')

BASE_URL = "https://open-api.bingx.com"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


# ============================================================================
# BOT PRINCIPAL
# ============================================================================

class TradingBot:
    """Bot de Trading Simplificado"""
    
    def __init__(self):
        logger.info("="*80)
        logger.info("🚀 BOT DE TRADING PROFESIONAL")
        logger.info("="*80)
        logger.info(f"{'✅ AUTO-TRADING: ON' if AUTO_TRADING else '⏹️ AUTO-TRADING: OFF'}")
        logger.info(f"💰 Position Size: ${MAX_POSITION_SIZE}")
        logger.info(f"⚡ Leverage: {LEVERAGE}x")
        logger.info(f"🎯 TP: {TAKE_PROFIT_PCT}% | SL: {STOP_LOSS_PCT}%")
        logger.info(f"📊 Max Trades: {MAX_OPEN_TRADES}")
        logger.info("="*80)
        
        self.symbols = []
        self.open_trades = {}
        self.stats = {
            'signals': 0,
            'trades_executed': 0,
            'trades_closed': 0,
            'total_pnl': 0.0
        }
        
        self._verify_credentials()
        self._get_top_symbols()
        self._send_telegram("🤖 Bot iniciado")
    
    def _verify_credentials(self):
        """Verificar credenciales"""
        if AUTO_TRADING and (not BINGX_API_KEY or not BINGX_API_SECRET):
            logger.error("❌ Credenciales faltantes")
            logger.warning("⚠️ AUTO-TRADING DESACTIVADO")
            globals()['AUTO_TRADING'] = False
        else:
            logger.info("✅ Credenciales verificadas")
    
    def _get_top_symbols(self):
        """Obtener símbolos más activos"""
        try:
            url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"
            response = requests.get(url, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    tickers = data.get('data', [])
                    
                    # Filtrar y ordenar
                    processed = []
                    for ticker in tickers:
                        symbol = ticker.get('symbol', '')
                        if not symbol.endswith('-USDT'):
                            continue
                        
                        try:
                            volume = float(ticker.get('volume', 0))
                            price = float(ticker.get('lastPrice', 0))
                            volume_usd = volume * price
                            
                            if volume_usd > 1000000 and price > 0.0001:
                                processed.append({
                                    'symbol': symbol,
                                    'volume': volume_usd
                                })
                        except:
                            continue
                    
                    # Top 30
                    processed.sort(key=lambda x: x['volume'], reverse=True)
                    self.symbols = [item['symbol'] for item in processed[:30]]
                    
                    logger.info(f"📊 {len(self.symbols)} símbolos activos")
        except Exception as e:
            logger.warning(f"⚠️ Error obteniendo símbolos: {e}")
            self.symbols = [
                'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT', 'XRP-USDT',
                'DOGE-USDT', 'ADA-USDT', 'AVAX-USDT', 'DOT-USDT', 'MATIC-USDT'
            ]
    
    def _sign_request(self, params):
        """Firmar request"""
        query_string = urlencode(params)
        signature = hmac.new(
            BINGX_API_SECRET.encode(),
            query_string.encode(),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def get_ticker(self, symbol):
        """Obtener datos de un símbolo"""
        try:
            url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"
            params = {'symbol': symbol}
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0 and data.get('data'):
                    ticker = data['data']
                    return {
                        'symbol': symbol,
                        'price': float(ticker.get('lastPrice', 0)),
                        'change': float(ticker.get('priceChangePercent', 0))
                    }
        except:
            pass
        return None
    
    def analyze_signal(self, ticker):
        """Análisis simple de señal"""
        if not ticker:
            return None
        
        symbol = ticker['symbol']
        change = ticker['change']
        price = ticker['price']
        
        # No abrir si ya hay trade
        if symbol in self.open_trades:
            return None
        
        # Señales básicas
        if change >= 0.8:
            return {'signal': 'LONG', 'price': price, 'change': change}
        elif change <= -0.8:
            return {'signal': 'SHORT', 'price': price, 'change': change}
        
        return None
    
    def open_trade(self, symbol, direction, price):
        """Abrir trade con TP/SL automáticos"""
        if not AUTO_TRADING:
            logger.info(f"📊 SEÑAL: {direction} {symbol} @ ${price:.4f}")
            return False
        
        try:
            quantity = MAX_POSITION_SIZE / price
            
            # Calcular TP/SL ANTES de abrir
            if direction == 'LONG':
                tp_price = price * (1 + TAKE_PROFIT_PCT / 100)
                sl_price = price * (1 - STOP_LOSS_PCT / 100)
            else:
                tp_price = price * (1 - TAKE_PROFIT_PCT / 100)
                sl_price = price * (1 + STOP_LOSS_PCT / 100)
            
            # 1. ABRIR POSICIÓN
            timestamp = int(time.time() * 1000)
            
            params = {
                'symbol': symbol,
                'side': 'BUY' if direction == 'LONG' else 'SELL',
                'positionSide': direction,
                'type': 'MARKET',
                'quantity': str(quantity),
                'timestamp': timestamp
            }
            
            params['signature'] = self._sign_request(params)
            
            url = f"{BASE_URL}/openApi/swap/v2/trade/order"
            headers = {'X-BX-APIKEY': BINGX_API_KEY}
            
            response = requests.post(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    logger.info(f"✅ Posición abierta: {direction} {symbol}")
                    
                    # 2. COLOCAR TAKE PROFIT (orden real en BingX)
                    time.sleep(0.5)  # Pequeña pausa
                    tp_placed = self._place_tp_sl_order(
                        symbol, direction, quantity, tp_price, 'TAKE_PROFIT_MARKET'
                    )
                    
                    # 3. COLOCAR STOP LOSS (orden real en BingX)
                    time.sleep(0.5)
                    sl_placed = self._place_tp_sl_order(
                        symbol, direction, quantity, sl_price, 'STOP_MARKET'
                    )
                    
                    # Registrar trade
                    self.open_trades[symbol] = {
                        'direction': direction,
                        'entry_price': price,
                        'tp_price': tp_price,
                        'sl_price': sl_price,
                        'quantity': quantity,
                        'tp_placed': tp_placed,
                        'sl_placed': sl_placed,
                        'timestamp': datetime.now()
                    }
                    
                    self.stats['trades_executed'] += 1
                    
                    tp_status = "✅" if tp_placed else "⚠️"
                    sl_status = "✅" if sl_placed else "⚠️"
                    
                    logger.info(f"✅ TRADE COMPLETO: {direction} {symbol} @ ${price:.4f}")
                    logger.info(f"   {tp_status} TP: ${tp_price:.4f} (+{TAKE_PROFIT_PCT}%)")
                    logger.info(f"   {sl_status} SL: ${sl_price:.4f} (-{STOP_LOSS_PCT}%)")
                    
                    self._send_telegram(
                        f"✅ <b>TRADE ABIERTO CON TP/SL</b>\n"
                        f"{direction} {symbol}\n"
                        f"💰 Entry: ${price:.4f}\n"
                        f"{tp_status} TP: ${tp_price:.4f} (+{TAKE_PROFIT_PCT}%)\n"
                        f"{sl_status} SL: ${sl_price:.4f} (-{STOP_LOSS_PCT}%)\n"
                        f"📊 Cantidad: {quantity:.6f}"
                    )
                    
                    return True
        except Exception as e:
            logger.error(f"❌ Error abriendo trade: {e}")
        
        return False
    
    def _place_tp_sl_order(self, symbol, direction, quantity, price, order_type):
        """Colocar orden de TP o SL en BingX"""
        try:
            timestamp = int(time.time() * 1000)
            
            # Determinar el lado opuesto para cerrar
            if direction == 'LONG':
                side = 'SELL'  # Para cerrar LONG
            else:
                side = 'BUY'   # Para cerrar SHORT
            
            params = {
                'symbol': symbol,
                'side': side,
                'positionSide': direction,
                'type': order_type,  # TAKE_PROFIT_MARKET o STOP_MARKET
                'quantity': str(quantity),
                'stopPrice': str(price),  # Precio de activación
                'timestamp': timestamp
            }
            
            params['signature'] = self._sign_request(params)
            
            url = f"{BASE_URL}/openApi/swap/v2/trade/order"
            headers = {'X-BX-APIKEY': BINGX_API_KEY}
            
            response = requests.post(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    order_name = "TP" if "TAKE_PROFIT" in order_type else "SL"
                    logger.info(f"   ✅ {order_name} colocado @ ${price:.4f}")
                    return True
                else:
                    logger.warning(f"   ⚠️ Error {order_type}: {data.get('msg')}")
            
            return False
        
        except Exception as e:
            logger.warning(f"   ⚠️ Error colocando {order_type}: {e}")
            return False
    
    def close_trade(self, symbol, current_price, reason):
        """Cerrar trade"""
        if symbol not in self.open_trades:
            return False
        
        trade = self.open_trades[symbol]
        
        try:
            timestamp = int(time.time() * 1000)
            
            params = {
                'symbol': symbol,
                'side': 'SELL' if trade['direction'] == 'LONG' else 'BUY',
                'positionSide': trade['direction'],
                'type': 'MARKET',
                'quantity': str(trade['quantity']),
                'timestamp': timestamp
            }
            
            params['signature'] = self._sign_request(params)
            
            url = f"{BASE_URL}/openApi/swap/v2/trade/order"
            headers = {'X-BX-APIKEY': BINGX_API_KEY}
            
            response = requests.post(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    # Calcular PnL
                    if trade['direction'] == 'LONG':
                        pnl = (current_price - trade['entry_price']) * trade['quantity']
                    else:
                        pnl = (trade['entry_price'] - current_price) * trade['quantity']
                    
                    pnl_pct = (pnl / (trade['entry_price'] * trade['quantity'])) * 100
                    
                    self.stats['trades_closed'] += 1
                    self.stats['total_pnl'] += pnl
                    
                    logger.info(f"✅ CERRADO - {reason}: {symbol}")
                    logger.info(f"   PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
                    
                    self._send_telegram(
                        f"✅ <b>TRADE CERRADO - {reason}</b>\n"
                        f"{symbol}\n"
                        f"📊 PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
                        f"💵 Total: ${self.stats['total_pnl']:+.2f}"
                    )
                    
                    del self.open_trades[symbol]
                    return True
        except Exception as e:
            logger.error(f"❌ Error cerrando: {e}")
        
        return False
    
    async def monitor_trades(self):
        """Monitorear trades abiertos (backup - BingX cierra automáticamente)"""
        if not self.open_trades:
            return
        
        for symbol in list(self.open_trades.keys()):
            try:
                trade = self.open_trades[symbol]
                ticker = self.get_ticker(symbol)
                
                if not ticker:
                    continue
                
                current_price = ticker['price']
                entry_price = trade['entry_price']
                
                # Calcular PnL actual
                if trade['direction'] == 'LONG':
                    pnl_pct = ((current_price - entry_price) / entry_price) * 100
                else:
                    pnl_pct = ((entry_price - current_price) / entry_price) * 100
                
                # Solo reportar estado (BingX cierra automáticamente con TP/SL)
                if abs(pnl_pct) > 0.5:  # Solo log si hay movimiento significativo
                    logger.debug(
                        f"   {symbol}: {trade['direction']} | "
                        f"PnL: {pnl_pct:+.2f}% | "
                        f"Price: ${current_price:.4f}"
                    )
                
                # BACKUP: Verificar si BingX ya cerró la posición
                # (opcional - verificar posiciones abiertas en BingX)
                
            except Exception as e:
                logger.debug(f"Error monitoreando {symbol}: {e}")
    
    def _send_telegram(self, message):
        """Enviar mensaje a Telegram"""
        try:
            if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                data = {
                    'chat_id': TELEGRAM_CHAT_ID,
                    'text': message,
                    'parse_mode': 'HTML'
                }
                requests.post(url, json=data, timeout=5)
        except:
            pass
    
    async def run(self):
        """Loop principal"""
        logger.info("\n🚀 Bot iniciado - Loop principal\n")
        
        iteration = 0
        
        while True:
            try:
                iteration += 1
                
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 ITERACIÓN #{iteration} | {datetime.now().strftime('%H:%M:%S')}")
                logger.info(f"💰 Trades abiertos: {len(self.open_trades)}/{MAX_OPEN_TRADES}")
                logger.info(f"📈 Total PnL: ${self.stats['total_pnl']:+.2f}")
                logger.info(f"{'='*80}\n")
                
                # Monitorear trades abiertos
                await self.monitor_trades()
                
                # Buscar nuevas oportunidades
                if len(self.open_trades) < MAX_OPEN_TRADES:
                    signals_found = 0
                    
                    for symbol in self.symbols:
                        ticker = self.get_ticker(symbol)
                        analysis = self.analyze_signal(ticker)
                        
                        if analysis:
                            signals_found += 1
                            self.stats['signals'] += 1
                            
                            logger.info(
                                f"   📊 {symbol}: {analysis['signal']} "
                                f"({analysis['change']:+.2f}%)"
                            )
                            
                            self.open_trade(
                                symbol,
                                analysis['signal'],
                                analysis['price']
                            )
                        
                        await asyncio.sleep(0.1)
                    
                    if signals_found > 0:
                        logger.info(f"\n✅ {signals_found} señales encontradas")
                
                logger.info(f"\n⏱️ Próxima iteración en {CHECK_INTERVAL}s...\n")
                await asyncio.sleep(CHECK_INTERVAL)
            
            except KeyboardInterrupt:
                logger.info("\n🛑 Bot detenido")
                break
            
            except Exception as e:
                logger.error(f"\n❌ Error: {e}")
                await asyncio.sleep(10)


# ============================================================================
# MAIN
# ============================================================================

async def main():
    try:
        bot = TradingBot()
        await bot.run()
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bot terminado")
