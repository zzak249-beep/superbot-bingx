#!/usr/bin/env python3
"""
Bot con AUTO-TRADING COMPLETO
- Abre trades automáticamente
- Coloca TP y SL
- Cierra automáticamente cuando toca TP o SL
- Gestión completa del ciclo de vida del trade
"""

import os
import asyncio
import logging
import requests
import hmac
import hashlib
import time
from datetime import datetime
from urllib.parse import urlencode
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
load_dotenv()


class AutoTraderCompleto:
    """Auto-Trader con gestión COMPLETA de trades"""
    
    def __init__(self):
        """Inicializar"""
        self.symbols = [
            'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'MATIC-USDT', 'AVAX-USDT',
            'DOGE-USDT', 'ADA-USDT', 'XRP-USDT', 'DOT-USDT', 'LINK-USDT'
        ]
        
        # TELEGRAM
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        
        # BINGX API
        self.bingx_api_key = os.getenv('BINGX_API_KEY', '')
        self.bingx_api_secret = os.getenv('BINGX_API_SECRET', '')
        self.base_url = "https://open-api.bingx.com"
        
        # PARAMETROS DE TRADING
        self.position_size = float(os.getenv('MAX_POSITION_SIZE', '100'))
        self.leverage = int(os.getenv('LEVERAGE', '2'))
        self.interval = int(os.getenv('CHECK_INTERVAL', '180'))
        self.buy_threshold = 0.8
        
        # TAKE PROFIT Y STOP LOSS
        self.take_profit_pct = float(os.getenv('TAKE_PROFIT_PCT', '2.0'))  # 2%
        self.stop_loss_pct = float(os.getenv('STOP_LOSS_PCT', '1.0'))      # 1%
        
        # AUTO-TRADING ACTIVADO?
        self.auto_trading = os.getenv('AUTO_TRADING_ENABLED', 'false').lower() == 'true'
        
        self.stats = {
            'signals_generated': 0,
            'trades_executed': 0,
            'trades_success': 0,
            'trades_failed': 0,
            'trades_closed': 0,
            'total_pnl': 0.0
        }
        
        # Registro de trades abiertos
        self.open_trades = {}
        
        logger.info("="*80)
        logger.info("🤖 BOT CON AUTO-TRADING COMPLETO")
        logger.info("✅ Abre + TP/SL + Cierre Automático")
        if self.auto_trading:
            logger.info("✅ AUTO-TRADING ACTIVADO")
        else:
            logger.info("⏹️ AUTO-TRADING DESACTIVADO")
        logger.info(f"📊 Pares: {len(self.symbols)}")
        logger.info(f"💰 Position Size: ${self.position_size}")
        logger.info(f"⚡ Leverage: {self.leverage}x")
        logger.info(f"🎯 Take Profit: {self.take_profit_pct}%")
        logger.info(f"🛑 Stop Loss: {self.stop_loss_pct}%")
        logger.info(f"⏱️ Intervalo: {self.interval}s")
        logger.info("="*80)
        
        self._verify_credentials()
        self._check_balance()
        
        self._notify("🤖 Bot con Auto-Trading Completo iniciado\n" + 
                    ("✅ Gestión automática de TP/SL activada" if self.auto_trading else "⏹️ Solo señales"))
    
    def _verify_credentials(self):
        """Verificar credenciales"""
        logger.info("\n🔐 VERIFICANDO CREDENCIALES:")
        
        if not self.bingx_api_key:
            logger.error("❌ BINGX_API_KEY no configurada")
        else:
            logger.info(f"✅ API Key: {self.bingx_api_key[:10]}...***")
        
        if not self.bingx_api_secret:
            logger.error("❌ BINGX_API_SECRET no configurada")
        else:
            logger.info(f"✅ API Secret: ***configurada***")
        
        if self.auto_trading and (not self.bingx_api_key or not self.bingx_api_secret):
            logger.error("\n⚠️ AUTO-TRADING DESACTIVADO - Faltan credenciales")
            self.auto_trading = False
    
    def _check_balance(self):
        """Verificar balance en BingX"""
        if not self.auto_trading:
            return
        
        try:
            timestamp = int(time.time() * 1000)
            params = {'timestamp': timestamp}
            
            query_string = urlencode(params)
            signature = hmac.new(
                self.bingx_api_secret.encode(),
                query_string.encode(),
                hashlib.sha256
            ).hexdigest()
            
            params['signature'] = signature
            
            url = f"{self.base_url}/openApi/swap/v2/user/balance"
            headers = {'X-BX-APIKEY': self.bingx_api_key}
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    balance_data = data.get('data', {}).get('balance', {})
                    usdt_balance = balance_data.get('balance', 0) if isinstance(balance_data, dict) else 0
                    logger.info(f"💰 Balance USDT: ${usdt_balance:.2f}")
                else:
                    logger.warning(f"⚠️ Error: {data.get('msg')}")
        
        except Exception as e:
            logger.error(f"❌ Error: {e}")
    
    def _get_current_price(self, symbol):
        """Obtener precio actual de un símbolo"""
        try:
            url = f"{self.base_url}/openApi/swap/v2/quote/ticker"
            params = {'symbol': symbol}
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    return float(data['data']['lastPrice'])
            return None
        except:
            return None
    
    def _execute_trade(self, symbol, direction, price, quantity):
        """Abrir trade con TP y SL automáticos"""
        if not self.auto_trading or not self.bingx_api_key:
            logger.warning(f"⚠️ Auto-trading desactivado")
            return False
        
        try:
            # Calcular TP y SL
            if direction == 'LONG':
                tp_price = price * (1 + self.take_profit_pct / 100)
                sl_price = price * (1 - self.stop_loss_pct / 100)
            else:  # SHORT
                tp_price = price * (1 - self.take_profit_pct / 100)
                sl_price = price * (1 + self.stop_loss_pct / 100)
            
            # 1. Abrir posición
            timestamp = int(time.time() * 1000)
            
            params = {
                'symbol': symbol,
                'side': 'BUY' if direction == 'LONG' else 'SELL',
                'positionSide': direction,
                'type': 'MARKET',
                'quantity': str(quantity),
                'timestamp': timestamp
            }
            
            query_string = urlencode(params)
            signature = hmac.new(
                self.bingx_api_secret.encode(),
                query_string.encode(),
                hashlib.sha256
            ).hexdigest()
            
            params['signature'] = signature
            
            url = f"{self.base_url}/openApi/swap/v2/trade/order"
            headers = {
                'X-BX-APIKEY': self.bingx_api_key,
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            response = requests.post(url, params=params, headers=headers, timeout=10)
            
            logger.info(f"→ Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    order_data = data.get('data', {}).get('order', {})
                    order_id = order_data.get('orderId', 'N/A')
                    
                    logger.info(f"✅ TRADE ABIERTO")
                    logger.info(f"   {direction}: {symbol} @ ${price:.4f}")
                    logger.info(f"   Cantidad: {quantity:.4f}")
                    logger.info(f"   🎯 TP: ${tp_price:.4f} (+{self.take_profit_pct}%)")
                    logger.info(f"   🛑 SL: ${sl_price:.4f} (-{self.stop_loss_pct}%)")
                    logger.info(f"   Order ID: {order_id}")
                    
                    self.stats['trades_executed'] += 1
                    self.stats['trades_success'] += 1
                    
                    # Registrar trade
                    self.open_trades[symbol] = {
                        'direction': direction,
                        'entry_price': price,
                        'tp_price': tp_price,
                        'sl_price': sl_price,
                        'quantity': quantity,
                        'order_id': order_id,
                        'timestamp': datetime.now().isoformat()
                    }
                    
                    msg = f"✅ <b>TRADE ABIERTO</b>\n"
                    msg += f"{direction} {symbol}\n"
                    msg += f"💰 Entry: ${price:.4f}\n"
                    msg += f"🎯 TP: ${tp_price:.4f} (+{self.take_profit_pct}%)\n"
                    msg += f"🛑 SL: ${sl_price:.4f} (-{self.stop_loss_pct}%)\n"
                    msg += f"📊 Cantidad: {quantity:.4f}"
                    self._notify(msg)
                    
                    return True
                else:
                    logger.error(f"❌ Error: {data.get('msg')}")
                    self.stats['trades_failed'] += 1
                    return False
            else:
                logger.error(f"❌ Error HTTP: {response.status_code}")
                self.stats['trades_failed'] += 1
                return False
        
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            self.stats['trades_failed'] += 1
            return False
    
    def _close_trade(self, symbol, reason="MANUAL"):
        """Cerrar trade abierto"""
        if symbol not in self.open_trades:
            return False
        
        try:
            trade = self.open_trades[symbol]
            
            timestamp = int(time.time() * 1000)
            
            params = {
                'symbol': symbol,
                'side': 'SELL' if trade['direction'] == 'LONG' else 'BUY',
                'positionSide': trade['direction'],
                'type': 'MARKET',
                'quantity': str(trade['quantity']),
                'timestamp': timestamp
            }
            
            query_string = urlencode(params)
            signature = hmac.new(
                self.bingx_api_secret.encode(),
                query_string.encode(),
                hashlib.sha256
            ).hexdigest()
            
            params['signature'] = signature
            
            url = f"{self.base_url}/openApi/swap/v2/trade/order"
            headers = {
                'X-BX-APIKEY': self.bingx_api_key,
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            response = requests.post(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    # Calcular PnL
                    current_price = self._get_current_price(symbol)
                    if current_price:
                        if trade['direction'] == 'LONG':
                            pnl = (current_price - trade['entry_price']) * trade['quantity']
                        else:
                            pnl = (trade['entry_price'] - current_price) * trade['quantity']
                        
                        pnl_pct = (pnl / (trade['entry_price'] * trade['quantity'])) * 100
                        
                        logger.info(f"✅ TRADE CERRADO - {reason}")
                        logger.info(f"   {symbol}: ${current_price:.4f}")
                        logger.info(f"   PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
                        
                        self.stats['trades_closed'] += 1
                        self.stats['total_pnl'] += pnl
                        
                        msg = f"✅ <b>TRADE CERRADO - {reason}</b>\n"
                        msg += f"{symbol}\n"
                        msg += f"💰 Entry: ${trade['entry_price']:.4f}\n"
                        msg += f"💰 Exit: ${current_price:.4f}\n"
                        msg += f"📊 PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
                        msg += f"💵 Total PnL: ${self.stats['total_pnl']:+.2f}"
                        self._notify(msg)
                    
                    del self.open_trades[symbol]
                    return True
            
            return False
        
        except Exception as e:
            logger.error(f"❌ Error cerrando: {e}")
            return False
    
    async def monitor_trades(self):
        """Monitorear trades abiertos y cerrar si tocan TP o SL"""
        if not self.auto_trading:
            return
        
        for symbol in list(self.open_trades.keys()):
            try:
                trade = self.open_trades[symbol]
                current_price = self._get_current_price(symbol)
                
                if not current_price:
                    continue
                
                # Verificar TP
                if trade['direction'] == 'LONG':
                    if current_price >= trade['tp_price']:
                        logger.info(f"🎯 TP ALCANZADO: {symbol} @ ${current_price:.4f}")
                        self._close_trade(symbol, "TAKE PROFIT")
                    elif current_price <= trade['sl_price']:
                        logger.info(f"🛑 SL ALCANZADO: {symbol} @ ${current_price:.4f}")
                        self._close_trade(symbol, "STOP LOSS")
                else:  # SHORT
                    if current_price <= trade['tp_price']:
                        logger.info(f"🎯 TP ALCANZADO: {symbol} @ ${current_price:.4f}")
                        self._close_trade(symbol, "TAKE PROFIT")
                    elif current_price >= trade['sl_price']:
                        logger.info(f"🛑 SL ALCANZADO: {symbol} @ ${current_price:.4f}")
                        self._close_trade(symbol, "STOP LOSS")
            
            except Exception as e:
                logger.debug(f"Error monitoreando {symbol}: {e}")
    
    def _notify(self, msg: str):
        """Enviar Telegram"""
        try:
            if not self.telegram_token or not self.chat_id:
                return
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = {'chat_id': self.chat_id, 'text': msg, 'parse_mode': 'HTML'}
            requests.post(url, json=data, timeout=5)
        except:
            pass
    
    def get_price_data(self, symbol: str) -> dict:
        """Obtener datos de precio"""
        try:
            url = f"{self.base_url}/openApi/swap/v2/quote/ticker"
            params = {'symbol': symbol}
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0 and data.get('data'):
                    ticker = data['data']
                    price = float(ticker.get('lastPrice', 0))
                    
                    if price > 0:
                        return {
                            'symbol': symbol,
                            'price': price,
                            'change': float(ticker.get('priceChangePercent', 0)),
                            'status': 'OK'
                        }
            
            return {'symbol': symbol, 'status': 'ERROR'}
        except:
            return {'symbol': symbol, 'status': 'ERROR'}
    
    def analyze_signal(self, symbol: str, data: dict) -> dict:
        """Analizar señal"""
        try:
            change = data.get('change', 0)
            price = data.get('price', 0)
            
            # No abrir nuevo trade si ya hay uno abierto en este símbolo
            if symbol in self.open_trades:
                return {'direction': 'NEUTRAL', 'score': 0, 'change': change, 'price': price}
            
            if change >= self.buy_threshold:
                score = min(95, 50 + (change * 10))
                return {'direction': 'LONG', 'score': score, 'change': change, 'price': price}
            
            elif change <= -self.buy_threshold:
                score = min(95, 50 + (abs(change) * 10))
                return {'direction': 'SHORT', 'score': score, 'change': change, 'price': price}
            
            else:
                return {'direction': 'NEUTRAL', 'score': 0, 'change': change, 'price': price}
        except:
            return {'direction': 'NEUTRAL', 'score': 0, 'change': 0, 'price': 0}
    
    async def run(self):
        """Loop principal"""
        logger.info("\n🚀 Bot iniciado...\n")
        iteration = 0
        
        while True:
            try:
                iteration += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"⏱️ ITERACIÓN #{iteration} | {datetime.now().strftime('%H:%M:%S')}")
                if self.auto_trading:
                    logger.info(f"🤖 AUTO-TRADING: ACTIVADO ✅")
                logger.info(f"📊 Trades abiertos: {len(self.open_trades)}")
                logger.info(f"{'='*80}\n")
                
                # MONITOREAR TRADES ABIERTOS PRIMERO
                await self.monitor_trades()
                
                long_count = 0
                short_count = 0
                analyzed = 0
                
                # BUSCAR NUEVAS SEÑALES
                for i, symbol in enumerate(self.symbols, 1):
                    try:
                        data = self.get_price_data(symbol)
                        
                        if data['status'] != 'OK':
                            continue
                        
                        analyzed += 1
                        signal = self.analyze_signal(symbol, data)
                        
                        if signal['direction'] == 'LONG':
                            long_count += 1
                            self.stats['signals_generated'] += 1
                            
                            logger.info(f"{i:2d}. 🟢 {symbol}: ${data['price']:.4f} | {signal['change']:+.2f}% | LONG")
                            
                            if self.auto_trading:
                                quantity = self.position_size / data['price']
                                self._execute_trade(symbol, 'LONG', data['price'], quantity)
                        
                        elif signal['direction'] == 'SHORT':
                            short_count += 1
                            self.stats['signals_generated'] += 1
                            
                            logger.info(f"{i:2d}. 🔴 {symbol}: ${data['price']:.4f} | {signal['change']:+.2f}% | SHORT")
                            
                            if self.auto_trading:
                                quantity = self.position_size / data['price']
                                self._execute_trade(symbol, 'SHORT', data['price'], quantity)
                        
                        else:
                            logger.info(f"{i:2d}. ⚪ {symbol}: ${data['price']:.4f} | {signal['change']:+.2f}% | NEUTRAL")
                    
                    except Exception as e:
                        logger.debug(f"Error {symbol}: {str(e)[:40]}")
                    
                    await asyncio.sleep(0.05)
                
                # RESUMEN
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 RESUMEN #{iteration}:")
                logger.info(f"   ✅ Analizados: {analyzed}/10")
                logger.info(f"   🟢 LONG: {long_count}")
                logger.info(f"   🔴 SHORT: {short_count}")
                logger.info(f"   📈 Signals: {self.stats['signals_generated']} total")
                
                if self.auto_trading:
                    logger.info(f"   🤖 Trades ejecutados: {self.stats['trades_executed']}")
                    logger.info(f"   💰 Trades abiertos: {len(self.open_trades)}")
                    logger.info(f"   ✅ Trades cerrados: {self.stats['trades_closed']}")
                    logger.info(f"   💵 PnL Total: ${self.stats['total_pnl']:+.2f}")
                
                logger.info(f"{'='*80}")
                
                logger.info(f"\n⏱️ Próximo en {self.interval}s...\n")
                await asyncio.sleep(self.interval)
            
            except KeyboardInterrupt:
                logger.info("\n🛑 Bot detenido")
                break
            except Exception as e:
                logger.error(f"\n❌ Error: {e}")
                await asyncio.sleep(10)


async def main():
    try:
        bot = AutoTraderCompleto()
        await bot.run()
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot terminado")
