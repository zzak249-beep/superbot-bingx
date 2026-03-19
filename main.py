#!/usr/bin/env python3
"""
🚀 BOT DE TRADING PROFESIONAL - VERSIÓN TODO-EN-UNO
Optimizado para Railway - Con inversión fija $6 USDT
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

# INVERSIÓN FIJA - NUEVA VARIABLE
CAPITAL_PER_TRADE = get_env_value('CAPITAL_PER_TRADE', '6.0', 'float')  # $6 FIJO

LEVERAGE = get_env_value('LEVERAGE', '3', 'int')
TAKE_PROFIT_PCT = get_env_value('TAKE_PROFIT_PCT', '2.5', 'float')
STOP_LOSS_PCT = get_env_value('STOP_LOSS_PCT', '1.2', 'float')
MAX_OPEN_TRADES = get_env_value('MAX_OPEN_TRADES', '5', 'int')
CHECK_INTERVAL = get_env_value('CHECK_INTERVAL', '60', 'int')

# Umbrales de señal
MIN_CHANGE_PCT = get_env_value('MIN_CHANGE_PCT', '0.3', 'float')
MIN_CONFIDENCE = get_env_value('MIN_CONFIDENCE', '42', 'float')

# Market scanning
MIN_VOLUME_USD = get_env_value('MIN_VOLUME_24H', '500000', 'float')  # $500k mínimo
MAX_SYMBOLS_TO_ANALYZE = get_env_value('MAX_SYMBOLS_TO_ANALYZE', '50', 'int')  # Analizar hasta 50

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
    """Bot de Trading con Inversión Fija"""
    
    def __init__(self):
        logger.info("="*80)
        logger.info("🚀 BOT PROFESIONAL - SOLO CRIPTOMONEDAS")
        logger.info("="*80)
        logger.info(f"{'✅ AUTO-TRADING: ON' if AUTO_TRADING else '⏹️ AUTO-TRADING: OFF'}")
        logger.info(f"💰 Inversión por trade: ${CAPITAL_PER_TRADE} USDT")
        logger.info(f"⚡ Leverage: {LEVERAGE}x")
        logger.info(f"📊 Posición controlada: ${CAPITAL_PER_TRADE * LEVERAGE}")
        logger.info(f"🎯 TP: {TAKE_PROFIT_PCT}% | SL: {STOP_LOSS_PCT}%")
        logger.info(f"📈 Min Change: {MIN_CHANGE_PCT}% | Min Confidence: {MIN_CONFIDENCE}%")
        logger.info(f"📊 Max Trades: {MAX_OPEN_TRADES}")
        logger.info(f"🔍 Max Símbolos: {MAX_SYMBOLS_TO_ANALYZE}")
        logger.info(f"💵 Volumen mín: ${MIN_VOLUME_USD:,.0f}")
        logger.info(f"✅ SOLO CRIPTOMONEDAS (sin acciones/commodities/índices)")
        logger.info("="*80)
        
        self.symbols = []
        self.open_trades = {}
        self.price_history = {}
        self.stats = {
            'signals': 0,
            'trades_executed': 0,
            'trades_closed': 0,
            'total_pnl': 0.0,
            'wins': 0,
            'losses': 0
        }
        
        self._verify_credentials()
        self._get_top_symbols()
        self._send_telegram(
            f"🤖 <b>Bot iniciado</b>\n"
            f"💰 ${CAPITAL_PER_TRADE} por trade\n"
            f"⚡ Leverage: {LEVERAGE}x\n"
            f"🔍 Analizando {len(self.symbols)} CRIPTOMONEDAS\n"
            f"💵 Volumen mín: ${MIN_VOLUME_USD:,.0f}\n"
            f"✅ Solo criptomonedas (sin acciones/commodities)"
        )
    
    def _verify_credentials(self):
        """Verificar credenciales"""
        if AUTO_TRADING and (not BINGX_API_KEY or not BINGX_API_SECRET):
            logger.error("❌ Credenciales faltantes")
            logger.warning("⚠️ AUTO-TRADING DESACTIVADO")
            globals()['AUTO_TRADING'] = False
        else:
            logger.info("✅ Credenciales verificadas")
    
    def _get_top_symbols(self):
        """Obtener SOLO CRIPTOMONEDAS de BingX"""
        try:
            logger.info("🔍 Obteniendo SOLO criptomonedas...")
            
            # FILTROS - EXCLUIR TODO LO QUE NO SEA CRIPTO
            excluded_keywords = [
                # Commodities
                'GOLD', 'SILVER', 'XAG', 'XAU', 'PAXG', 'XAUT',
                'OIL', 'BRENT', 'WTI', 'GAS',
                # Acciones
                'TSLA', 'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA',
                'COIN', 'MSTR', 'GME', 'AMC',
                # Índices
                'NASDAQ', 'SPX', 'DJI', 'S&P', 'NDX',
                # Forex
                'EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD', 'NZD',
                # Otros
                '100', '1000'  # Índices tipo NASDAQ100
            ]
            
            url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"
            response = requests.get(url, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    tickers = data.get('data', [])
                    
                    logger.info(f"📊 Total de pares en BingX: {len(tickers)}")
                    
                    # Procesar pares
                    processed = []
                    excluded_count = 0
                    
                    for ticker in tickers:
                        symbol = ticker.get('symbol', '')
                        
                        # Solo pares USDT perpetuos
                        if not symbol.endswith('-USDT'):
                            continue
                        
                        # FILTRO: Excluir todo lo que NO sea cripto
                        is_excluded = False
                        symbol_upper = symbol.upper()
                        
                        for keyword in excluded_keywords:
                            if keyword in symbol_upper:
                                is_excluded = True
                                excluded_count += 1
                                logger.debug(f"   ❌ Excluido: {symbol} (contiene {keyword})")
                                break
                        
                        if is_excluded:
                            continue
                        
                        try:
                            volume = float(ticker.get('volume', 0))
                            price = float(ticker.get('lastPrice', 0))
                            volume_usd = volume * price
                            
                            # Filtros
                            if volume_usd < MIN_VOLUME_USD:
                                continue
                            
                            if price < 0.0001:
                                continue
                            
                            processed.append({
                                'symbol': symbol,
                                'volume_usd': volume_usd,
                                'price': price,
                                'change': float(ticker.get('priceChangePercent', 0))
                            })
                        
                        except (ValueError, TypeError):
                            continue
                    
                    # Ordenar por volumen
                    processed.sort(key=lambda x: x['volume_usd'], reverse=True)
                    
                    # Tomar límite
                    top_symbols = processed[:MAX_SYMBOLS_TO_ANALYZE]
                    self.symbols = [item['symbol'] for item in top_symbols]
                    
                    logger.info(f"✅ {len(self.symbols)} CRIPTOMONEDAS seleccionadas")
                    logger.info(f"❌ {excluded_count} NO-cripto excluidos (acciones, commodities, índices)")
                    
                    # Top 10
                    logger.info("\n📊 Top 10 CRIPTOMONEDAS por volumen:")
                    for i, item in enumerate(top_symbols[:10], 1):
                        logger.info(
                            f"   {i:2d}. {item['symbol']:15s} | "
                            f"Vol: ${item['volume_usd']:>15,.0f} | "
                            f"Change: {item['change']:>+7.2f}%"
                        )
                    
                    return
        
        except Exception as e:
            logger.warning(f"⚠️ Error obteniendo símbolos: {e}")
        
        # Fallback - SOLO CRIPTOMONEDAS
        logger.warning("⚠️ Usando lista estática de CRIPTOMONEDAS")
        self.symbols = [
            # Top criptos por capitalización
            'BTC-USDT', 'ETH-USDT', 'BNB-USDT', 'SOL-USDT', 'XRP-USDT',
            'ADA-USDT', 'DOGE-USDT', 'AVAX-USDT', 'DOT-USDT', 'MATIC-USDT',
            'LINK-USDT', 'UNI-USDT', 'ATOM-USDT', 'LTC-USDT', 'BCH-USDT',
            'NEAR-USDT', 'APT-USDT', 'ARB-USDT', 'OP-USDT', 'FTM-USDT',
            # Otras criptos populares
            'ALGO-USDT', 'VET-USDT', 'ICP-USDT', 'FIL-USDT', 'HBAR-USDT',
            'ETC-USDT', 'XLM-USDT', 'AAVE-USDT', 'EOS-USDT', 'XMR-USDT'
        ]
    
    def _sign_request(self, params):
        """Firmar request"""
        query_string = urlencode(sorted(params.items()))
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
                    price = float(ticker.get('lastPrice', 0))
                    
                    # Guardar historial
                    if symbol not in self.price_history:
                        self.price_history[symbol] = []
                    
                    self.price_history[symbol].append(price)
                    if len(self.price_history[symbol]) > 20:
                        self.price_history[symbol] = self.price_history[symbol][-20:]
                    
                    return {
                        'symbol': symbol,
                        'price': price,
                        'change': float(ticker.get('priceChangePercent', 0)),
                        'volume': float(ticker.get('volume', 0))
                    }
        except:
            pass
        return None
    
    def _calculate_rsi(self, prices):
        """RSI simple"""
        if len(prices) < 10:
            return 50
        
        gains = []
        losses = []
        
        for i in range(1, len(prices)):
            change = prices[i] - prices[i-1]
            gains.append(max(0, change))
            losses.append(max(0, -change))
        
        avg_gain = sum(gains[-9:]) / 9
        avg_loss = sum(losses[-9:]) / 9
        
        if avg_loss == 0:
            return 100
        
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    def analyze_signal(self, ticker):
        """Análisis de señal mejorado"""
        if not ticker:
            return None
        
        symbol = ticker['symbol']
        change = ticker['change']
        price = ticker['price']
        
        # No duplicar
        if symbol in self.open_trades:
            return None
        
        # Scoring
        score = 0
        
        # Momentum
        if abs(change) >= MIN_CHANGE_PCT:
            score += min(40, abs(change) * 15)
        
        # RSI
        if symbol in self.price_history:
            prices = self.price_history[symbol]
            rsi = self._calculate_rsi(prices)
            
            if rsi < 40:
                score += 30
            elif rsi > 60:
                score += 30
            elif 40 <= rsi <= 60:
                score += 15
        
        # Tendencia
        if symbol in self.price_history and len(self.price_history[symbol]) >= 5:
            prices = self.price_history[symbol]
            trend = (prices[-1] - prices[-5]) / prices[-5] * 100
            if abs(trend) > 0.2:
                score += 15
        
        # Decisión
        if score >= MIN_CONFIDENCE:
            if change > 0:
                return {'signal': 'LONG', 'price': price, 'change': change, 'score': score}
            elif change < 0:
                return {'signal': 'SHORT', 'price': price, 'change': change, 'score': score}
        
        return None
    
    def open_trade(self, symbol, direction, price):
        """Abrir trade con INVERSIÓN FIJA de $6 USDT"""
        if not AUTO_TRADING:
            logger.info(f"📊 SEÑAL: {direction} {symbol} @ ${price:.4f}")
            return False
        
        try:
            # CÁLCULO CON INVERSIÓN FIJA
            position_value = CAPITAL_PER_TRADE * LEVERAGE
            quantity = position_value / price
            
            # Redondeo inteligente
            if price > 1000:
                quantity = round(quantity, 6)
            elif price > 100:
                quantity = round(quantity, 5)
            elif price > 10:
                quantity = round(quantity, 4)
            elif price > 1:
                quantity = round(quantity, 3)
            else:
                quantity = round(quantity, 2)
            
            logger.info(f"\n💰 Calculando inversión:")
            logger.info(f"   Capital: ${CAPITAL_PER_TRADE}")
            logger.info(f"   Leverage: {LEVERAGE}x")
            logger.info(f"   Posición: ${position_value:.2f}")
            logger.info(f"   Precio: ${price:.4f}")
            logger.info(f"   Cantidad: {quantity}")
            
            # Calcular TP/SL
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
                    
                    # 2. COLOCAR TAKE PROFIT
                    time.sleep(0.5)
                    tp_placed = self._place_tp_sl_order(
                        symbol, direction, quantity, tp_price, 'TAKE_PROFIT_MARKET'
                    )
                    
                    # 3. COLOCAR STOP LOSS
                    time.sleep(0.5)
                    sl_placed = self._place_tp_sl_order(
                        symbol, direction, quantity, sl_price, 'STOP_MARKET'
                    )
                    
                    # Registrar
                    self.open_trades[symbol] = {
                        'direction': direction,
                        'entry_price': price,
                        'tp_price': tp_price,
                        'sl_price': sl_price,
                        'quantity': quantity,
                        'capital': CAPITAL_PER_TRADE,
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
                        f"✅ <b>TRADE ABIERTO</b>\n"
                        f"{direction} {symbol}\n"
                        f"💰 Inversión: ${CAPITAL_PER_TRADE} USDT\n"
                        f"📊 Posición: ${position_value:.2f}\n"
                        f"📈 Entry: ${price:.4f}\n"
                        f"{tp_status} TP: ${tp_price:.4f} (+{TAKE_PROFIT_PCT}%)\n"
                        f"{sl_status} SL: ${sl_price:.4f} (-{STOP_LOSS_PCT}%)\n"
                        f"⚡ Leverage: {LEVERAGE}x\n"
                        f"📦 Cantidad: {quantity}"
                    )
                    
                    return True
                else:
                    logger.error(f"❌ Error BingX: {data.get('msg')}")
            else:
                logger.error(f"❌ HTTP Error: {response.status_code}")
        
        except Exception as e:
            logger.error(f"❌ Error abriendo trade: {e}")
        
        return False
    
    def _place_tp_sl_order(self, symbol, direction, quantity, price, order_type):
        """Colocar orden de TP o SL en BingX"""
        try:
            timestamp = int(time.time() * 1000)
            
            if direction == 'LONG':
                side = 'SELL'
            else:
                side = 'BUY'
            
            params = {
                'symbol': symbol,
                'side': side,
                'positionSide': direction,
                'type': order_type,
                'quantity': str(quantity),
                'stopPrice': str(price),
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
            logger.warning(f"   ⚠️ Error {order_type}: {e}")
            return False
    
    def close_trade(self, symbol, current_price, reason):
        """Cerrar trade (backup - BingX cierra automáticamente)"""
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
                    # PnL
                    if trade['direction'] == 'LONG':
                        pnl = (current_price - trade['entry_price']) * trade['quantity']
                    else:
                        pnl = (trade['entry_price'] - current_price) * trade['quantity']
                    
                    pnl_pct = (pnl / (trade['entry_price'] * trade['quantity'])) * 100
                    
                    self.stats['trades_closed'] += 1
                    self.stats['total_pnl'] += pnl
                    
                    if pnl > 0:
                        self.stats['wins'] += 1
                    else:
                        self.stats['losses'] += 1
                    
                    logger.info(f"✅ CERRADO - {reason}: {symbol}")
                    logger.info(f"   PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
                    
                    win_rate = 0
                    if self.stats['wins'] + self.stats['losses'] > 0:
                        win_rate = self.stats['wins'] / (self.stats['wins'] + self.stats['losses']) * 100
                    
                    self._send_telegram(
                        f"✅ <b>CERRADO - {reason}</b>\n"
                        f"{symbol}\n"
                        f"📊 PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
                        f"💵 Total: ${self.stats['total_pnl']:+.2f}\n"
                        f"📈 Win Rate: {win_rate:.1f}% ({self.stats['wins']}W/{self.stats['losses']}L)"
                    )
                    
                    del self.open_trades[symbol]
                    return True
        except Exception as e:
            logger.error(f"❌ Error cerrando: {e}")
        
        return False
    
    async def monitor_trades(self):
        """Monitorear trades (BingX cierra automáticamente)"""
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
                
                # PnL actual
                if trade['direction'] == 'LONG':
                    pnl_pct = ((current_price - entry_price) / entry_price) * 100
                else:
                    pnl_pct = ((entry_price - current_price) / entry_price) * 100
                
                # Log estado
                if abs(pnl_pct) > 0.5:
                    logger.debug(
                        f"   {symbol}: {trade['direction']} | "
                        f"PnL: {pnl_pct:+.2f}% | "
                        f"Price: ${current_price:.4f}"
                    )
            
            except Exception as e:
                logger.debug(f"Error monitoreando {symbol}: {e}")
    
    def _send_telegram(self, message):
        """Telegram"""
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
        logger.info("\n🚀 Bot iniciado - Inversión fija $6 USDT\n")
        
        iteration = 0
        last_symbol_update = 0
        symbol_update_interval = 300  # 5 min
        
        while True:
            try:
                iteration += 1
                current_time = time.time()
                
                # Actualizar símbolos
                if current_time - last_symbol_update > symbol_update_interval:
                    logger.info("\n🔄 Actualizando monedas...")
                    self._get_top_symbols()
                    last_symbol_update = current_time
                
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 ITERACIÓN #{iteration} | {datetime.now().strftime('%H:%M:%S')}")
                logger.info(f"💰 Trades: {len(self.open_trades)}/{MAX_OPEN_TRADES}")
                logger.info(f"💵 Capital en uso: ${len(self.open_trades) * CAPITAL_PER_TRADE}")
                logger.info(f"📈 PnL Total: ${self.stats['total_pnl']:+.2f}")
                
                if self.stats['wins'] + self.stats['losses'] > 0:
                    wr = self.stats['wins'] / (self.stats['wins'] + self.stats['losses']) * 100
                    logger.info(f"🎯 Win Rate: {wr:.1f}% ({self.stats['wins']}W/{self.stats['losses']}L)")
                
                logger.info(f"🔍 Monedas: {len(self.symbols)}")
                logger.info(f"{'='*80}\n")
                
                # Monitorear
                await self.monitor_trades()
                
                # Buscar señales
                if len(self.open_trades) < MAX_OPEN_TRADES:
                    signals = 0
                    analyzed = 0
                    
                    for symbol in self.symbols:
                        analyzed += 1
                        ticker = self.get_ticker(symbol)
                        analysis = self.analyze_signal(ticker)
                        
                        if analysis:
                            signals += 1
                            self.stats['signals'] += 1
                            
                            logger.info(
                                f"   📊 {symbol}: {analysis['signal']} "
                                f"({analysis['change']:+.2f}% | Score: {analysis['score']:.0f}%) "
                                f"@ ${analysis['price']:.4f}"
                            )
                            
                            self.open_trade(
                                symbol,
                                analysis['signal'],
                                analysis['price']
                            )
                        
                        await asyncio.sleep(0.05)
                        
                        # Progreso
                        if analyzed % 10 == 0:
                            logger.info(
                                f"   ⏳ {analyzed}/{len(self.symbols)} "
                                f"({analyzed/len(self.symbols)*100:.0f}%)"
                            )
                    
                    logger.info(
                        f"\n✅ Análisis: {analyzed} monedas | "
                        f"{signals} señales"
                    )
                
                else:
                    logger.info(f"⏸️ Max trades: {MAX_OPEN_TRADES}")
                
                logger.info(f"\n⏱️ Próxima en {CHECK_INTERVAL}s...\n")
                await asyncio.sleep(CHECK_INTERVAL)
            
            except KeyboardInterrupt:
                logger.info("\n🛑 Detenido")
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
        logger.error(f"❌ Fatal: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Terminado")
