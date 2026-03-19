#!/usr/bin/env python3
"""
🚀 BOT DE TRADING PROFESIONAL - VERSIÓN TODO-EN-UNO
Optimizado para Railway - Firma BingX CORREGIDA
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
    """Obtener valor de entorno limpiando comillas y espacios"""
    value = os.getenv(key, str(default))
    if isinstance(value, str):
        value = value.strip().strip('"').strip("'").strip()
    if value_type == 'int':
        return int(value)
    elif value_type == 'float':
        return float(value)
    elif value_type == 'bool':
        return value.lower() == 'true'
    return value


BINGX_API_KEY    = os.getenv('BINGX_API_KEY', '').strip().strip('"').strip("'")
BINGX_API_SECRET = os.getenv('BINGX_API_SECRET', '').strip().strip('"').strip("'")
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID', '')

AUTO_TRADING         = get_env_value('AUTO_TRADING_ENABLED',   'false', 'bool')
MAX_POSITION_SIZE    = get_env_value('MAX_POSITION_SIZE',       '100',  'float')
LEVERAGE             = get_env_value('LEVERAGE',                '2',    'int')
TAKE_PROFIT_PCT      = get_env_value('TAKE_PROFIT_PCT',         '2.0',  'float')
STOP_LOSS_PCT        = get_env_value('STOP_LOSS_PCT',           '1.0',  'float')
MAX_OPEN_TRADES      = get_env_value('MAX_OPEN_TRADES',         '5',    'int')
CHECK_INTERVAL       = get_env_value('CHECK_INTERVAL',          '60',   'int')
MIN_VOLUME_USD       = get_env_value('MIN_VOLUME_24H',          '500000','float')
MAX_SYMBOLS_TO_ANALYZE = get_env_value('MAX_SYMBOLS_TO_ANALYZE','100',  'int')
MIN_TRADE_USDT       = get_env_value('MIN_TRADE_USDT',          '7',    'float')  # Mínimo $7 USDT por trade

BASE_URL = "https://open-api.bingx.com"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


# ============================================================================
# FIRMA BINGX - MÉTODO CORRECTO SEGÚN DOCUMENTACIÓN OFICIAL
# ============================================================================

def bingx_sign(secret: str, params: dict) -> str:
    """
    Firma HMAC-SHA256 correcta para BingX.
    Los parámetros se ordenan alfabéticamente y se concatenan como query string.
    El 'timestamp' DEBE estar dentro del dict ANTES de firmar.
    La 'signature' NO se incluye en el mensaje a firmar.
    """
    # Ordenar parámetros alfabéticamente (requerido por BingX)
    sorted_params = sorted(params.items())
    query_string = urlencode(sorted_params)
    
    signature = hmac.new(
        secret.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return signature


def bingx_request(method: str, endpoint: str, params: dict) -> dict:
    """
    Ejecuta una request autenticada a BingX correctamente.
    Construye la URL con todos los parámetros + signature en la query string.
    """
    # Añadir timestamp
    params['timestamp'] = int(time.time() * 1000)
    
    # Generar firma (SIN incluir 'signature' en los params todavía)
    signature = bingx_sign(BINGX_API_SECRET, params)
    
    # Construir query string final con signature al FINAL
    sorted_params = sorted(params.items())
    query_string = urlencode(sorted_params) + f"&signature={signature}"
    
    url = f"{BASE_URL}{endpoint}?{query_string}"
    
    headers = {
        'X-BX-APIKEY': BINGX_API_KEY,
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    if method == 'GET':
        response = requests.get(f"{BASE_URL}{endpoint}", params=dict(sorted_params + [('signature', signature)]), headers=headers, timeout=10)
    else:
        response = requests.post(url, headers=headers, timeout=10)
    
    return response


# ============================================================================
# BOT PRINCIPAL
# ============================================================================

class TradingBot:
    """Bot de Trading con firma BingX corregida"""
    
    def __init__(self):
        logger.info("=" * 80)
        logger.info("🚀 BOT DE TRADING PROFESIONAL - FIRMA CORREGIDA")
        logger.info("=" * 80)
        logger.info(f"{'✅ AUTO-TRADING: ON' if AUTO_TRADING else '⏹️ AUTO-TRADING: OFF'}")
        logger.info(f"💰 Position Size: ${MAX_POSITION_SIZE}")
        logger.info(f"⚡ Leverage: {LEVERAGE}x")
        logger.info(f"🎯 TP: {TAKE_PROFIT_PCT}% | SL: {STOP_LOSS_PCT}%")
        logger.info(f"📊 Max Trades: {MAX_OPEN_TRADES}")
        logger.info(f"🔍 Max Símbolos: {MAX_SYMBOLS_TO_ANALYZE}")
        logger.info(f"💵 Volumen mín: ${MIN_VOLUME_USD:,.0f}")
        logger.info(f"💲 Mínimo por trade: ${MIN_TRADE_USDT} USDT  ← FORZADO")
        logger.info("=" * 80)
        
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
            f"🤖 <b>Bot iniciado (firma corregida)</b>\n"
            f"{'✅ AUTO-TRADING: ON' if AUTO_TRADING else '⏹️ Solo señales'}\n"
            f"💰 ${MAX_POSITION_SIZE} por trade | {LEVERAGE}x leverage\n"
            f"🔍 Analizando hasta {MAX_SYMBOLS_TO_ANALYZE} monedas\n"
            f"💵 Volumen mín: ${MIN_VOLUME_USD:,.0f}"
        )
    
    # -------------------------------------------------------------------------
    # CREDENCIALES Y VERIFICACIÓN
    # -------------------------------------------------------------------------
    
    def _verify_credentials(self):
        """Verificar y testear credenciales con un endpoint real"""
        global AUTO_TRADING
        
        if not AUTO_TRADING:
            logger.info("⚪ Modo solo señales - No se requieren credenciales")
            return
        
        if not BINGX_API_KEY or not BINGX_API_SECRET:
            logger.error("❌ Credenciales faltantes - AUTO-TRADING DESACTIVADO")
            AUTO_TRADING = False
            return
        
        logger.info(f"✅ API Key cargada: {BINGX_API_KEY[:10]}...")
        logger.info(f"✅ Secret cargado: {len(BINGX_API_SECRET)} chars")
        
        # Test real: obtener balance
        try:
            response = bingx_request('GET', '/openApi/swap/v2/user/balance', {})
            data = response.json()
            
            if data.get('code') == 0:
                balance_data = data.get('data', {})
                usdt = balance_data.get('balance', {})
                # BingX devuelve balance en distintos formatos según versión API
                if isinstance(usdt, dict):
                    equity = usdt.get('equity', usdt.get('balance', '?'))
                else:
                    equity = usdt
                logger.info(f"✅ Conexión BingX OK | Balance: ${equity} USDT")
            else:
                code = data.get('code')
                msg  = data.get('msg', '')
                logger.error(f"❌ Error BingX: [{code}] {msg}")
                
                if code == 100001:
                    logger.error("   → Firma inválida. Verifica API_SECRET sin espacios")
                elif code == 100403:
                    logger.error("   → IP no autorizada en BingX")
                elif code == 100412:
                    logger.error("   → API Key inválida o sin permisos de Futures")
                
                logger.warning("⚠️ AUTO-TRADING DESACTIVADO por error de credenciales")
                AUTO_TRADING = False
        
        except Exception as e:
            logger.error(f"❌ Error verificando credenciales: {e}")
            AUTO_TRADING = False
    
    # -------------------------------------------------------------------------
    # SÍMBOLOS
    # -------------------------------------------------------------------------
    
    def _get_top_symbols(self):
        """Obtener las monedas más activas de BingX (solo cripto USDT)"""
        # Excluir commodities, acciones e índices
        excluded = {
            'GOLD','SILVER','XAG','XAU','PAXG','XAUT',
            'OIL','BRENT','WTI','GAS',
            'TSLA','AAPL','MSFT','GOOGL','AMZN','META','NVDA','COIN','MSTR',
            'EUR','GBP','JPY','CHF','AUD','CAD','NZD',
            '100','1000'
        }
        
        try:
            logger.info("🔍 Obteniendo monedas disponibles...")
            url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"
            response = requests.get(url, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    tickers = data.get('data', [])
                    logger.info(f"📊 Total pares BingX: {len(tickers)}")
                    
                    processed = []
                    for ticker in tickers:
                        symbol = ticker.get('symbol', '')
                        if not symbol.endswith('-USDT'):
                            continue
                        
                        base = symbol.replace('-USDT', '').upper()
                        if any(kw in base for kw in excluded):
                            continue
                        
                        try:
                            volume    = float(ticker.get('volume', 0))
                            price     = float(ticker.get('lastPrice', 0))
                            vol_usd   = volume * price
                            
                            if vol_usd < MIN_VOLUME_USD or price < 0.0001:
                                continue
                            
                            processed.append({
                                'symbol':     symbol,
                                'volume_usd': vol_usd,
                                'price':      price,
                                'change':     float(ticker.get('priceChangePercent', 0))
                            })
                        except (ValueError, TypeError):
                            continue
                    
                    processed.sort(key=lambda x: x['volume_usd'], reverse=True)
                    top = processed[:MAX_SYMBOLS_TO_ANALYZE]
                    self.symbols = [item['symbol'] for item in top]
                    
                    logger.info(f"✅ {len(self.symbols)} monedas seleccionadas")
                    logger.info("📊 Top 10 por volumen:")
                    for i, item in enumerate(top[:10], 1):
                        logger.info(
                            f"   {i:2d}. {item['symbol']:15s} | "
                            f"Vol: ${item['volume_usd']:>15,.0f} | "
                            f"{item['change']:>+7.2f}%"
                        )
                    return
        
        except Exception as e:
            logger.warning(f"⚠️ Error obteniendo símbolos: {e}")
        
        logger.warning("⚠️ Usando lista estática de respaldo")
        self.symbols = [
            'BTC-USDT','ETH-USDT','SOL-USDT','BNB-USDT','XRP-USDT',
            'DOGE-USDT','ADA-USDT','AVAX-USDT','DOT-USDT','MATIC-USDT',
            'LINK-USDT','UNI-USDT','ATOM-USDT','LTC-USDT','BCH-USDT',
            'NEAR-USDT','FIL-USDT','APT-USDT','ARB-USDT','OP-USDT'
        ]
    
    # -------------------------------------------------------------------------
    # DATOS DE MERCADO
    # -------------------------------------------------------------------------
    
    def get_ticker(self, symbol):
        """Obtener precio actual de un símbolo"""
        try:
            url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"
            response = requests.get(url, params={'symbol': symbol}, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0 and data.get('data'):
                    ticker = data['data']
                    price = float(ticker.get('lastPrice', 0))
                    
                    # Guardar historial para RSI
                    if symbol not in self.price_history:
                        self.price_history[symbol] = []
                    self.price_history[symbol].append(price)
                    if len(self.price_history[symbol]) > 20:
                        self.price_history[symbol] = self.price_history[symbol][-20:]
                    
                    return {
                        'symbol': symbol,
                        'price':  price,
                        'change': float(ticker.get('priceChangePercent', 0))
                    }
        except Exception:
            pass
        return None
    
    # -------------------------------------------------------------------------
    # ANÁLISIS DE SEÑALES
    # -------------------------------------------------------------------------
    
    def _calculate_rsi(self, prices):
        """RSI simplificado"""
        if len(prices) < 10:
            return 50
        gains  = [max(0,  prices[i] - prices[i-1]) for i in range(1, len(prices))]
        losses = [max(0, prices[i-1] - prices[i])  for i in range(1, len(prices))]
        avg_gain = sum(gains[-9:])  / 9
        avg_loss = sum(losses[-9:]) / 9
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    def analyze_signal(self, ticker):
        """Análisis con momentum + RSI"""
        if not ticker:
            return None
        
        symbol = ticker['symbol']
        change = ticker['change']
        price  = ticker['price']
        
        if symbol in self.open_trades:
            return None
        
        score = 0
        
        # Momentum
        if abs(change) >= 0.8:
            score += min(40, abs(change) * 15)
        
        # RSI
        if symbol in self.price_history:
            rsi = self._calculate_rsi(self.price_history[symbol])
            if rsi < 35 and change > 0:   # Oversold + rebote
                score += 35
            elif rsi > 65 and change < 0:  # Overbought + caída
                score += 35
            elif 40 <= rsi <= 60:
                score += 15
        
        # Tendencia reciente
        if symbol in self.price_history and len(self.price_history[symbol]) >= 5:
            prices = self.price_history[symbol]
            trend = (prices[-1] - prices[-5]) / prices[-5] * 100
            if abs(trend) > 0.2:
                score += 15
        
        if score >= 42:
            if change > 0:
                return {'signal': 'LONG',  'price': price, 'change': change, 'score': score}
            elif change < 0:
                return {'signal': 'SHORT', 'price': price, 'change': change, 'score': score}
        
        return None
    
    # -------------------------------------------------------------------------
    # TRADING
    # -------------------------------------------------------------------------
    
    # Cache de precisión de símbolos para no llamar a BingX cada vez
    _contract_info_cache = {}

    def _get_contract_info(self, symbol):
        """Obtener precisión mínima del contrato desde BingX (con cache)"""
        if symbol in self._contract_info_cache:
            return self._contract_info_cache[symbol]
        try:
            url = f"{BASE_URL}/openApi/swap/v2/quote/contracts"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    for contract in data.get('data', []):
                        sym = contract.get('symbol', '')
                        info = {
                            'qty_step':  float(contract.get('tradeMinQuantity', 1)),
                            'qty_prec':  int(contract.get('quantityPrecision', 2)),
                        }
                        self._contract_info_cache[sym] = info
        except Exception as e:
            logger.debug(f"Error obteniendo contratos: {e}")

        # Si no encontró el símbolo, devolver valores por defecto seguros
        default = {'qty_step': 1.0, 'qty_prec': 2}
        self._contract_info_cache[symbol] = default
        return default

    def _calc_quantity(self, symbol, price):
        """
        Calcular cantidad correcta garantizando mínimo MIN_TRADE_USDT.
        Respeta el step size y precisión del contrato.
        """
        info     = self._get_contract_info(symbol)
        step     = info['qty_step']   # unidades mínimas por orden
        prec     = info['qty_prec']   # decimales permitidos

        # Capital objetivo: el mayor entre lo configurado y el mínimo
        capital  = max(MAX_POSITION_SIZE, MIN_TRADE_USDT)

        # Cantidad bruta
        raw_qty  = capital / price

        # Ajustar al step size (redondear HACIA ARRIBA al step más cercano)
        import math
        stepped  = math.ceil(raw_qty / step) * step

        # Redondear a la precisión permitida
        quantity = round(stepped, prec)

        # Verificar valor resultante
        value    = quantity * price

        # Si el valor sigue siendo menor al mínimo, subir un step más
        while value < MIN_TRADE_USDT and step > 0:
            quantity += step
            quantity  = round(quantity, prec)
            value     = quantity * price

        return quantity, value

    def open_trade(self, symbol, direction, price):
        """Abrir posición en BingX con TP y SL - mínimo MIN_TRADE_USDT garantizado"""
        if not AUTO_TRADING:
            logger.info(f"📊 SEÑAL ({direction}): {symbol} @ ${price:.4f} [auto-trading OFF]")
            return False

        try:
            quantity, trade_value = self._calc_quantity(symbol, price)

            # Rechazo explícito si no llega al mínimo
            if trade_value < MIN_TRADE_USDT:
                logger.warning(f"   ❌ {symbol} RECHAZADO: ${trade_value:.4f} < mínimo ${MIN_TRADE_USDT} USDT")
                return False

            tp_price = price * (1 + TAKE_PROFIT_PCT / 100) if direction == 'LONG' else price * (1 - TAKE_PROFIT_PCT / 100)
            sl_price = price * (1 - STOP_LOSS_PCT  / 100) if direction == 'LONG' else price * (1 + STOP_LOSS_PCT  / 100)

            logger.info(f"\n🔄 Abriendo {direction} {symbol}:")
            logger.info(f"   Precio:    ${price:.6f}")
            logger.info(f"   Cantidad:  {quantity} (${trade_value:.2f} USDT)  ← mín ${MIN_TRADE_USDT}")
            logger.info(f"   TP:        ${tp_price:.6f} (+{TAKE_PROFIT_PCT}%)")
            logger.info(f"   SL:        ${sl_price:.6f} (-{STOP_LOSS_PCT}%)")
            
            # ── 1. Orden de mercado principal ──────────────────────────────
            params = {
                'symbol':       symbol,
                'side':         'BUY' if direction == 'LONG' else 'SELL',
                'positionSide': direction,
                'type':         'MARKET',
                'quantity':     str(quantity),
            }
            
            response = bingx_request('POST', '/openApi/swap/v2/trade/order', params)
            data = response.json()
            
            if data.get('code') != 0:
                logger.error(f"❌ Error abriendo posición: [{data.get('code')}] {data.get('msg')}")
                return False
            
            order_id = data.get('data', {}).get('orderId', 'N/A')
            logger.info(f"✅ Posición abierta | OrderID: {order_id}")
            
            # ── 2. Take Profit ─────────────────────────────────────────────
            time.sleep(0.5)
            tp_ok = self._place_conditional_order(symbol, direction, quantity, tp_price, 'TAKE_PROFIT_MARKET')
            
            # ── 3. Stop Loss ───────────────────────────────────────────────
            time.sleep(0.5)
            sl_ok = self._place_conditional_order(symbol, direction, quantity, sl_price, 'STOP_MARKET')
            
            # Registrar
            self.open_trades[symbol] = {
                'direction':   direction,
                'entry_price': price,
                'tp_price':    tp_price,
                'sl_price':    sl_price,
                'quantity':    quantity,
                'order_id':    order_id,
                'tp_ok':       tp_ok,
                'sl_ok':       sl_ok,
                'timestamp':   datetime.now()
            }
            self.stats['trades_executed'] += 1
            
            tp_icon = "✅" if tp_ok else "⚠️"
            sl_icon = "✅" if sl_ok else "⚠️"
            logger.info(f"   {tp_icon} TP colocado: ${tp_price:.6f}")
            logger.info(f"   {sl_icon} SL colocado: ${sl_price:.6f}")
            
            self._send_telegram(
                f"✅ <b>TRADE ABIERTO</b>\n"
                f"{direction} {symbol}\n"
                f"💰 Entry:  ${price:.4f}\n"
                f"{tp_icon} TP: ${tp_price:.4f} (+{TAKE_PROFIT_PCT}%)\n"
                f"{sl_icon} SL: ${sl_price:.4f} (-{STOP_LOSS_PCT}%)\n"
                f"📦 Qty: {quantity} (${trade_value:.2f} USDT) | {LEVERAGE}x"
            )
            return True
        
        except Exception as e:
            logger.error(f"❌ Excepción abriendo trade {symbol}: {e}")
            return False
    
    def _place_conditional_order(self, symbol, direction, quantity, stop_price, order_type):
        """Colocar orden TP o SL condicional"""
        try:
            close_side = 'SELL' if direction == 'LONG' else 'BUY'
            
            params = {
                'symbol':       symbol,
                'side':         close_side,
                'positionSide': direction,
                'type':         order_type,
                'quantity':     str(quantity),
                'stopPrice':    str(round(stop_price, 6)),
            }
            
            response = bingx_request('POST', '/openApi/swap/v2/trade/order', params)
            data = response.json()
            
            if data.get('code') == 0:
                return True
            else:
                logger.warning(f"   ⚠️ {order_type} error: [{data.get('code')}] {data.get('msg')}")
                return False
        
        except Exception as e:
            logger.warning(f"   ⚠️ Excepción {order_type}: {e}")
            return False
    
    def close_trade(self, symbol, current_price, reason):
        """Cerrar posición manualmente (backup)"""
        if symbol not in self.open_trades:
            return False
        
        trade = self.open_trades[symbol]
        
        try:
            params = {
                'symbol':       symbol,
                'side':         'SELL' if trade['direction'] == 'LONG' else 'BUY',
                'positionSide': trade['direction'],
                'type':         'MARKET',
                'quantity':     str(trade['quantity']),
            }
            
            response = bingx_request('POST', '/openApi/swap/v2/trade/order', params)
            data = response.json()
            
            if data.get('code') == 0:
                if trade['direction'] == 'LONG':
                    pnl = (current_price - trade['entry_price']) * trade['quantity']
                else:
                    pnl = (trade['entry_price'] - current_price) * trade['quantity']
                
                pnl_pct = pnl / (trade['entry_price'] * trade['quantity']) * 100
                self.stats['trades_closed'] += 1
                self.stats['total_pnl']     += pnl
                
                if pnl > 0:
                    self.stats['wins'] += 1
                else:
                    self.stats['losses'] += 1
                
                wr = 0
                total = self.stats['wins'] + self.stats['losses']
                if total > 0:
                    wr = self.stats['wins'] / total * 100
                
                logger.info(f"✅ CERRADO ({reason}): {symbol} | PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
                self._send_telegram(
                    f"✅ <b>CERRADO - {reason}</b>\n"
                    f"{symbol}\n"
                    f"📊 PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
                    f"💵 Total: ${self.stats['total_pnl']:+.2f}\n"
                    f"🎯 Win Rate: {wr:.1f}% ({self.stats['wins']}W/{self.stats['losses']}L)"
                )
                del self.open_trades[symbol]
                return True
        
        except Exception as e:
            logger.error(f"❌ Error cerrando {symbol}: {e}")
        
        return False
    
    # -------------------------------------------------------------------------
    # MONITOREO
    # -------------------------------------------------------------------------
    
    async def monitor_trades(self):
        """Monitorear trades abiertos y cerrar si TP/SL no fue ejecutado por BingX"""
        for symbol in list(self.open_trades.keys()):
            try:
                trade  = self.open_trades[symbol]
                ticker = self.get_ticker(symbol)
                if not ticker:
                    continue
                
                current = ticker['price']
                entry   = trade['entry_price']
                
                if trade['direction'] == 'LONG':
                    pnl_pct = (current - entry) / entry * 100
                    hit_tp  = current >= trade['tp_price']
                    hit_sl  = current <= trade['sl_price']
                else:
                    pnl_pct = (entry - current) / entry * 100
                    hit_tp  = current <= trade['tp_price']
                    hit_sl  = current >= trade['sl_price']
                
                # Log estado si hay movimiento
                if abs(pnl_pct) > 0.3:
                    logger.info(
                        f"   👁 {symbol}: {trade['direction']} | "
                        f"PnL: {pnl_pct:+.2f}% | ${current:.4f}"
                    )
                
                # Cierre de seguridad (por si BingX no lo cerró)
                if hit_tp:
                    self.close_trade(symbol, current, "TAKE PROFIT")
                elif hit_sl:
                    self.close_trade(symbol, current, "STOP LOSS")
            
            except Exception as e:
                logger.debug(f"Error monitoreando {symbol}: {e}")
    
    # -------------------------------------------------------------------------
    # TELEGRAM
    # -------------------------------------------------------------------------
    
    def _send_telegram(self, message):
        try:
            if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                data = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'HTML'}
                requests.post(url, json=data, timeout=5)
        except Exception:
            pass
    
    # -------------------------------------------------------------------------
    # LOOP PRINCIPAL
    # -------------------------------------------------------------------------
    
    async def run(self):
        logger.info("\n🚀 Bot iniciado - Loop principal\n")
        
        iteration          = 0
        last_symbol_update = 0
        
        while True:
            try:
                iteration   += 1
                current_time = time.time()
                
                # Actualizar lista de símbolos cada 5 min
                if current_time - last_symbol_update > 300:
                    logger.info("\n🔄 Actualizando lista de monedas...")
                    self._get_top_symbols()
                    last_symbol_update = current_time
                
                # Encabezado iteración
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 ITERACIÓN #{iteration} | {datetime.now().strftime('%H:%M:%S')}")
                logger.info(f"💰 Trades abiertos: {len(self.open_trades)}/{MAX_OPEN_TRADES}")
                logger.info(f"📈 Total PnL: ${self.stats['total_pnl']:+.2f}")
                if self.stats['wins'] + self.stats['losses'] > 0:
                    wr = self.stats['wins'] / (self.stats['wins'] + self.stats['losses']) * 100
                    logger.info(f"🎯 Win Rate: {wr:.1f}% ({self.stats['wins']}W/{self.stats['losses']}L)")
                logger.info(f"🔍 Monedas: {len(self.symbols)}")
                logger.info(f"{'='*80}\n")
                
                # Monitorear posiciones abiertas
                await self.monitor_trades()
                
                # Buscar nuevas oportunidades
                if len(self.open_trades) < MAX_OPEN_TRADES:
                    signals_found  = 0
                    analyzed_count = 0
                    
                    logger.info(f"🔍 Analizando {len(self.symbols)} monedas...\n")
                    
                    for symbol in self.symbols:
                        if len(self.open_trades) >= MAX_OPEN_TRADES:
                            break
                        
                        analyzed_count += 1
                        ticker   = self.get_ticker(symbol)
                        analysis = self.analyze_signal(ticker)
                        
                        if analysis:
                            signals_found += 1
                            self.stats['signals'] += 1
                            logger.info(
                                f"   📊 {symbol}: {analysis['signal']} "
                                f"({analysis['change']:+.2f}% | score:{analysis['score']:.0f}) "
                                f"@ ${analysis['price']:.4f}"
                            )
                            self.open_trade(symbol, analysis['signal'], analysis['price'])
                        
                        await asyncio.sleep(0.05)
                        
                        if analyzed_count % 20 == 0:
                            logger.info(
                                f"   ⏳ {analyzed_count}/{len(self.symbols)} "
                                f"({analyzed_count/len(self.symbols)*100:.0f}%)"
                            )
                    
                    logger.info(
                        f"\n✅ Análisis: {analyzed_count} monedas | "
                        f"{signals_found} señales encontradas"
                    )
                else:
                    logger.info(f"⏸️ Max trades alcanzado ({MAX_OPEN_TRADES})")
                
                logger.info(f"\n⏱️ Próxima en {CHECK_INTERVAL}s...\n")
                await asyncio.sleep(CHECK_INTERVAL)
            
            except KeyboardInterrupt:
                logger.info("\n🛑 Bot detenido")
                break
            except Exception as e:
                logger.error(f"\n❌ Error en loop: {e}")
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
