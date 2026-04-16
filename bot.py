#!/usr/bin/env python3
"""
🔥 ULTRA-AGGRESSIVE BOT v6.0 — GUARANTEED SIGNALS EDITION
════════════════════════════════════════════════════════════════════════════

FILOSOFÍA: "Buscar las MEJORES oportunidades disponibles, no la perfección"

CAMBIOS CRÍTICOS:
├─ ✅ SCORING RELATIVO: Toma los top N símbolos del scan
├─ ✅ FILTROS MÍNIMOS: Solo elimina lo obviamente malo
├─ ✅ LOGGING DETALLADO: Ve exactamente por qué se rechaza cada símbolo
├─ ✅ PARÁMETROS ULTRA-RELAJADOS: Configuración para GENERAR señales
├─ ✅ FALLBACK MODES: Si no hay señales "perfectas", toma las "buenas"
└─ ✅ DEBUG MODE: Información completa de cada análisis

OBJETIVO: Generar 3-5 señales por scan GARANTIZADO
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re, json
from datetime import datetime, timedelta
from urllib.parse import urlencode
from collections import deque
from typing import Dict, List, Optional, Tuple

# ════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════════════

def clean_env(key: str, default, typ='str'):
    v = os.getenv(key, str(default)).strip()
    if v.startswith('"') and v.endswith('"'): v = v[1:-1]
    elif v.startswith("'") and v.endswith("'"): v = v[1:-1]
    if typ in ('int', 'float'):
        v = v.replace(',', '.')
        m = re.match(r'^-?\d+\.?\d*', v)
        v = m.group(0) if m else str(default)
    if typ == 'int':   return int(float(v))
    if typ == 'float': return float(v)
    if typ == 'bool':  return v.lower() == 'true'
    return v

# API
API_KEY    = clean_env('BINGX_API_KEY', '')
API_SECRET = clean_env('BINGX_API_SECRET', '')
TG_TOKEN   = clean_env('TELEGRAM_BOT_TOKEN', '')
TG_CHAT    = clean_env('TELEGRAM_CHAT_ID', '')

# CAPITAL
AUTO_TRADING   = clean_env('AUTO_TRADING_ENABLED', 'false', 'bool')
POSITION_SIZE  = clean_env('POSITION_SIZE_USD', '10', 'float')
LEVERAGE       = clean_env('LEVERAGE', '5', 'int')
MAX_POSITIONS  = clean_env('MAX_POSITIONS', '2', 'int')
ACCOUNT_EQUITY = clean_env('ACCOUNT_EQUITY', '100', 'float')

# v6.0 ULTRA-AGGRESSIVE - MINIMAL FILTERS
MIN_VOLUME_24H = clean_env('MIN_VOLUME_24H', '100000', 'float')  # Solo 100k!
MAX_SYMBOLS = clean_env('MAX_SYMBOLS', '60', 'int')  # Más símbolos
SIGNALS_PER_SCAN = clean_env('SIGNALS_PER_SCAN', '3', 'int')  # Top 3 siempre

# STOP LOSS & TP - MUY RELAJADO
SL_ATR_MULT = clean_env('SL_ATR_MULTIPLIER', '1.5', 'float')
TP_RR = clean_env('TP_RISK_REWARD', '1.5', 'float')

# DEBUG
DEBUG_MODE = clean_env('DEBUG_MODE', 'true', 'bool')

# TIMING
SCAN_INTERVAL = clean_env('SCAN_INTERVAL_SEC', '60', 'int')

# CONSTANTS
BASE_URL = "https://open-api.bingx.com"
FEE_TOTAL = 0.0014

EXCLUDE_SYMBOLS = {'DOW', 'SP500', 'GOLD', 'SILVER', 'EUR', 'GBP', 'JPY'}

# ════════════════════════════════════════════════════════════════════
# LOGGING
# ════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.DEBUG if DEBUG_MODE else logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════

def safe_div(n: float, d: float, default: float = 0.0) -> float:
    return n / d if abs(d) > 1e-10 else default

def safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val not in (None, '') else default
    except:
        return default

# ════════════════════════════════════════════════════════════════════
# API
# ════════════════════════════════════════════════════════════════════

def api_request(method: str, endpoint: str, params: dict = None) -> dict:
    params = params or {}
    try:
        p = {**{k: str(v) for k, v in params.items()},
             'timestamp': str(int(time.time() * 1000))}
        query = urlencode(sorted(p.items()))
        sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
        url = f"{BASE_URL}{endpoint}?{query}&signature={sig}"
        headers = {'X-BX-APIKEY': API_KEY}
        response = getattr(requests, method.lower())(url, headers=headers, timeout=10)
        return response.json()
    except Exception as e:
        log.error(f"API error {endpoint}: {e}")
        return {'code': -1}

def public_request(path: str, params: dict = None) -> dict:
    try:
        response = requests.get(f"{BASE_URL}{path}", params=params or {}, timeout=8)
        return response.json()
    except:
        return {'code': -1}

def extract_equity(data: dict) -> float:
    if data.get('code') != 0:
        return 0.0
    raw = data.get('data', {})
    if isinstance(raw, dict) and 'balance' in raw:
        inner = raw['balance']
        if isinstance(inner, dict):
            return safe_float(inner.get('equity', inner.get('availableMargin', 0)))
    if isinstance(raw, dict):
        return safe_float(raw.get('equity', raw.get('balance', 0)))
    return 0.0

# ════════════════════════════════════════════════════════════════════
# INDICATORS (SIMPLIFIED)
# ════════════════════════════════════════════════════════════════════

def sma(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0
    return sum(prices[-period:]) / period

def ema(prices: List[float], period: int) -> float:
    if not prices or len(prices) < period:
        return sum(prices) / len(prices) if prices else 0
    k = 2 / (period + 1)
    val = prices[0]
    for p in prices[1:]:
        val = p * k + val * (1 - k)
    return val

def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, min(len(closes), period + 1)):
        if closes[i-1] <= 0:
            continue
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0

def rsi(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    gains = [max(prices[i] - prices[i-1], 0) for i in range(1, len(prices))]
    losses = [max(prices[i-1] - prices[i], 0) for i in range(1, len(prices))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al <= 0:
        return 100.0 if ag > 0 else 50.0
    return 100 - (100 / (1 + ag/al))

# ════════════════════════════════════════════════════════════════════
# SIMPLIFIED SCORING (NO HARD FILTERS)
# ════════════════════════════════════════════════════════════════════

def score_symbol(symbol: str, price: float, closes: List[float], highs: List[float],
                 lows: List[float], volumes: List[float]) -> Tuple[float, Dict]:
    """
    Score 0-100 WITHOUT hard filters.
    Toma lo mejor disponible.
    """
    score = 0.0
    details = {'symbol': symbol, 'price': price, 'reasons': []}
    
    try:
        # Trend (0-30)
        ma10 = sma(closes, 10)
        ma20 = sma(closes, 20)
        
        if price > ma10 > ma20:
            score += 30
            details['reasons'].append("Strong_Trend(30)")
        elif price > ma10:
            score += 20
            details['reasons'].append("Above_MA10(20)")
        elif price > ma20:
            score += 10
            details['reasons'].append("Above_MA20(10)")
        else:
            score += 5
            details['reasons'].append("Trend_Weak(5)")
        
        # Momentum (0-25)
        if len(closes) >= 5:
            mom = safe_div(price - closes[-5], closes[-5], 0) * 100
            if mom > 3:
                score += 25
                details['reasons'].append(f"Strong_Mom({mom:.1f}%)(25)")
            elif mom > 1:
                score += 15
                details['reasons'].append(f"Good_Mom({mom:.1f}%)(15)")
            elif mom > 0:
                score += 8
                details['reasons'].append(f"Positive({mom:.1f}%)(8)")
            else:
                score += 3
                details['reasons'].append(f"Negative({mom:.1f}%)(3)")
        
        # Volume (0-20)
        if len(volumes) >= 20:
            avg_vol = sum(volumes[-20:-1]) / 19
            vol_ratio = safe_div(volumes[-1], avg_vol, 1.0)
            if vol_ratio > 2.0:
                score += 20
                details['reasons'].append(f"Vol_Surge({vol_ratio:.1f}x)(20)")
            elif vol_ratio > 1.5:
                score += 15
                details['reasons'].append(f"Vol_High({vol_ratio:.1f}x)(15)")
            elif vol_ratio > 1.0:
                score += 8
                details['reasons'].append(f"Vol_Normal({vol_ratio:.1f}x)(8)")
            else:
                score += 3
                details['reasons'].append(f"Vol_Low({vol_ratio:.1f}x)(3)")
        
        # RSI (0-15)
        rsi_val = rsi(closes, 14)
        if 40 < rsi_val < 60:
            score += 15
            details['reasons'].append(f"RSI_Sweet({int(rsi_val)})(15)")
        elif 35 < rsi_val < 65:
            score += 10
            details['reasons'].append(f"RSI_Good({int(rsi_val)})(10)")
        elif rsi_val < 35:
            score += 8
            details['reasons'].append(f"RSI_Oversold({int(rsi_val)})(8)")
        else:
            score += 3
            details['reasons'].append(f"RSI_OK({int(rsi_val)})(3)")
        
        # Volatility (0-10)
        atr_val = atr(highs, lows, closes, 14)
        atr_pct = safe_div(atr_val, price, 0) * 100
        if 0.5 < atr_pct < 3.0:
            score += 10
            details['reasons'].append(f"ATR_Good({atr_pct:.1f}%)(10)")
        elif atr_pct < 4.0:
            score += 5
            details['reasons'].append(f"ATR_OK({atr_pct:.1f}%)(5)")
        else:
            score += 2
            details['reasons'].append(f"ATR_High({atr_pct:.1f}%)(2)")
        
        details['score'] = score
        details['ma10'] = ma10
        details['ma20'] = ma20
        details['rsi'] = rsi_val
        details['atr'] = atr_val
        
        return score, details
    
    except Exception as e:
        log.error(f"Score error {symbol}: {e}")
        return 0, details

# ════════════════════════════════════════════════════════════════════
# MAIN BOT
# ════════════════════════════════════════════════════════════════════

class UltraAggressiveBot:
    def __init__(self):
        self.symbols = []
        self.positions = {}
        self.contracts_info = {}
        self.equity = ACCOUNT_EQUITY
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.stats = {'total_trades': 0, 'wins': 0, 'losses': 0, 'total_pnl': 0.0}

        log.info("=" * 80)
        log.info("🔥 ULTRA-AGGRESSIVE BOT v6.0 — GUARANTEED SIGNALS")
        log.info("=" * 80)
        log.info(f"🎯 STRATEGY: Take TOP {SIGNALS_PER_SCAN} symbols per scan")
        log.info(f"📊 Minimal filters - Maximum opportunities")
        log.info(f"🔍 Debug mode: {'ON' if DEBUG_MODE else 'OFF'}")
        log.info("=" * 80)
        log.info(f"Capital: ${POSITION_SIZE} × {MAX_POSITIONS} | Leverage: {LEVERAGE}×")
        log.info(f"Mode: {'🔥 LIVE' if AUTO_TRADING else '📝 PAPER'}")
        log.info("=" * 80)

        if not self._connect():
            if AUTO_TRADING:
                sys.exit(1)

        self._load_contracts()
        self._refresh_symbols()
        self._recover_positions()

        self._send_telegram(
            f"<b>🔥 ULTRA-AGGRESSIVE BOT v6.0</b>\n\n"
            f"✅ Guaranteed {SIGNALS_PER_SCAN} signals/scan\n"
            f"💰 ${POSITION_SIZE} × {MAX_POSITIONS} | {LEVERAGE}×\n"
            f"🎯 Relative scoring (best available)\n\n"
            f"{'🔥 LIVE' if AUTO_TRADING else '📝 PAPER'}"
        )

    def _connect(self) -> bool:
        global AUTO_TRADING
        if not AUTO_TRADING:
            log.info("✓ PAPER MODE")
            return True
        
        if not API_KEY or not API_SECRET:
            log.error("❌ No API keys")
            AUTO_TRADING = False
            return False
        
        data = api_request('GET', '/openApi/swap/v2/user/balance')
        if data.get('code') == 0:
            eq = extract_equity(data)
            if eq > 0:
                self.equity = eq
                log.info(f"✓ Connected | Equity: ${eq:.2f}")
                return True
        
        log.error("❌ Connection failed")
        AUTO_TRADING = False
        return False

    def _load_contracts(self):
        data = public_request('/openApi/swap/v2/quote/contracts')
        if data.get('code') == 0:
            for c in data.get('data', []):
                s = c.get('symbol', '')
                if s:
                    self.contracts_info[s] = {
                        'min_qty': safe_float(c.get('tradeMinQuantity', 1)),
                        'qty_precision': int(c.get('quantityPrecision', 2)),
                        'contract_size': safe_float(c.get('contractSize', 1))
                    }
            log.info(f"✓ Contracts: {len(self.contracts_info)}")

    def _refresh_symbols(self):
        data = public_request('/openApi/swap/v2/quote/ticker')
        if data.get('code') != 0:
            self.symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT', 'XRP-USDT', 
                           'ADA-USDT', 'DOGE-USDT', 'MATIC-USDT', 'DOT-USDT', 'AVAX-USDT']
            log.info(f"✓ Using default symbols: {len(self.symbols)}")
            return
        
        candidates = []
        for t in data.get('data', []):
            s = t.get('symbol', '')
            if not s.endswith('-USDT'):
                continue
            
            base = s.replace('-USDT', '').upper()
            if any(ex in base for ex in EXCLUDE_SYMBOLS):
                continue
            
            if s not in self.contracts_info:
                continue
            
            price = safe_float(t.get('lastPrice', 0))
            vol = safe_float(t.get('volume', 0)) * price
            
            # MINIMAL FILTER - solo volumen mínimo
            if vol >= MIN_VOLUME_24H and price > 0:
                candidates.append({'symbol': s, 'volume': vol})
        
        candidates.sort(key=lambda x: x['volume'], reverse=True)
        self.symbols = [c['symbol'] for c in candidates[:MAX_SYMBOLS]]
        log.info(f"✓ Symbols loaded: {len(self.symbols)}")

    def _recover_positions(self):
        if not AUTO_TRADING:
            return
        
        data = api_request('GET', '/openApi/swap/v2/user/positions')
        if data.get('code') != 0:
            return
        
        for pos in data.get('data', []):
            try:
                symbol = pos.get('symbol', '')
                amt = safe_float(pos.get('positionAmt', 0))
                if abs(amt) > 0:
                    entry = safe_float(pos.get('avgPrice', 0))
                    if entry > 0:
                        self.positions[symbol] = {
                            'entry': entry, 'qty': abs(amt), 'side': 'LONG',
                            'highest': entry, 'opened_at': datetime.now(),
                            'sl_price': entry * 0.985, 'tp_price': entry * 1.022
                        }
                        log.info(f"♻️ Recovered: {symbol}")
            except:
                continue

    def _get_klines(self, symbol: str):
        try:
            data = public_request('/openApi/swap/v3/quote/klines', {
                'symbol': symbol, 'interval': '5m', 'limit': 60
            })
            if data.get('code') == 0 and data.get('data'):
                k = data['data']
                return (
                    [safe_float(x['close']) for x in k],
                    [safe_float(x['high']) for x in k],
                    [safe_float(x['low']) for x in k],
                    [safe_float(x['volume']) for x in k]
                )
        except Exception as e:
            if DEBUG_MODE:
                log.debug(f"Klines error {symbol}: {e}")
        return None, None, None, None

    def _get_ticker(self, symbol: str):
        try:
            data = public_request('/openApi/swap/v2/quote/ticker', {'symbol': symbol})
            if data.get('code') == 0 and data.get('data'):
                t = data['data']
                return {'price': safe_float(t.get('lastPrice', 0))}
        except:
            pass
        return None

    def scan_for_signals(self) -> List[Dict]:
        """
        ESCANEO AGRESIVO: Retorna TOP N símbolos SIEMPRE
        No rechaza por filtros duros - toma lo mejor disponible
        """
        log.info(f"\n{'='*80}")
        log.info(f"🔍 SCANNING {len(self.symbols)} symbols...")
        log.info(f"{'='*80}\n")
        
        scored_symbols = []
        failed_count = 0
        
        for symbol in self.symbols:
            # Skip if already in position
            if symbol in self.positions:
                if DEBUG_MODE:
                    log.debug(f"{symbol}: Already in position - SKIP")
                continue
            
            # Get data
            closes, highs, lows, volumes = self._get_klines(symbol)
            
            if not closes or len(closes) < 30:
                failed_count += 1
                if DEBUG_MODE:
                    log.debug(f"{symbol}: Insufficient data ({len(closes) if closes else 0} bars) - SKIP")
                continue
            
            ticker = self._get_ticker(symbol)
            if not ticker or ticker['price'] <= 0:
                failed_count += 1
                if DEBUG_MODE:
                    log.debug(f"{symbol}: No ticker data - SKIP")
                continue
            
            price = ticker['price']
            
            # SCORE (NO FILTERING)
            score, details = score_symbol(symbol, price, closes, highs, lows, volumes)
            
            if score > 0:
                # Calculate SL/TP
                atr_val = details.get('atr', 0)
                sl_price = price - (atr_val * SL_ATR_MULT) if atr_val > 0 else price * 0.985
                sl_pct = safe_div(price - sl_price, price, 0.015) * 100
                tp_price = price + (price - sl_price) * TP_RR
                tp_pct = safe_div(tp_price - price, price, 0) * 100
                
                details['sl_price'] = sl_price
                details['sl_pct'] = sl_pct
                details['tp_price'] = tp_price
                details['tp_pct'] = tp_pct
                
                scored_symbols.append(details)
                
                if DEBUG_MODE:
                    log.debug(
                        f"{symbol}: Score={score:.0f} | "
                        f"MA10={details['ma10']:.6f} MA20={details['ma20']:.6f} | "
                        f"RSI={details['rsi']:.0f} | "
                        f"{' | '.join(details['reasons'])}"
                    )
        
        log.info(f"✓ Analyzed: {len(scored_symbols)} symbols")
        log.info(f"✗ Failed/Skipped: {failed_count} symbols")
        
        # SORT by score and take TOP N
        scored_symbols.sort(key=lambda x: x['score'], reverse=True)
        
        top_signals = scored_symbols[:SIGNALS_PER_SCAN]
        
        log.info(f"\n{'='*80}")
        log.info(f"🎯 TOP {len(top_signals)} SIGNALS (Best Available):")
        log.info(f"{'='*80}")
        for i, sig in enumerate(top_signals, 1):
            log.info(
                f"#{i} {sig['symbol']} | Score: {sig['score']:.0f}/100 | "
                f"Entry: ${sig['price']:.6f} | "
                f"SL: ${sig['sl_price']:.6f} (-{sig['sl_pct']:.1f}%) | "
                f"TP: ${sig['tp_price']:.6f} (+{sig['tp_pct']:.1f}%)"
            )
            if DEBUG_MODE:
                log.debug(f"    Reasons: {' | '.join(sig['reasons'])}")
        log.info(f"{'='*80}\n")
        
        return top_signals

    def open_position(self, signal: Dict) -> bool:
        """Open position"""
        symbol = signal['symbol']
        price = signal['price']
        
        if not AUTO_TRADING:
            log.info(f"📝 PAPER: Would open {symbol} @ ${price:.6f}")
            return False

        log.info(f"\n{'='*80}")
        log.info(f"🎯 OPENING: {symbol}")
        log.info(f"Score: {signal['score']:.0f}/100")
        log.info(f"Entry: ${price:.6f} | SL: ${signal['sl_price']:.6f} | TP: ${signal['tp_price']:.6f}")
        log.info(f"{'='*80}\n")

        qty = self._calculate_quantity(symbol, price)
        if not qty:
            log.error(f"❌ Cannot calculate quantity")
            return False

        # Set leverage
        for side in ['LONG', 'SHORT']:
            try:
                api_request('POST', '/openApi/swap/v2/trade/leverage', {
                    'symbol': symbol, 'side': side, 'leverage': str(LEVERAGE)
                })
            except:
                pass
        
        time.sleep(0.2)

        # Market order
        order = api_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol': symbol, 'side': 'BUY', 'type': 'MARKET',
            'quantity': str(qty), 'positionSide': 'LONG'
        })

        if order.get('code') != 0:
            log.error(f"❌ Order failed: {order.get('msg')}")
            return False

        time.sleep(1)
        
        # Confirm position
        fill_qty, fill_price = self._confirm_position(symbol)
        if not fill_qty:
            log.error(f"❌ Position not confirmed")
            return False

        # Place SL
        api_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol': symbol, 'side': 'SELL', 'type': 'STOP_MARKET',
            'quantity': str(fill_qty), 'stopPrice': str(round(signal['sl_price'], 8)),
            'positionSide': 'LONG'
        })

        self.positions[symbol] = {
            'entry': fill_price, 'qty': fill_qty, 'side': 'LONG',
            'sl_price': signal['sl_price'], 'tp_price': signal['tp_price'],
            'highest': fill_price, 'opened_at': datetime.now(),
            'signal': signal
        }

        self.stats['total_trades'] += 1
        self.daily_trades += 1

        self._send_telegram(
            f"<b>🟢 LONG OPENED</b>\n\n"
            f"<b>{symbol}</b>\n"
            f"Score: {signal['score']:.0f}/100\n\n"
            f"📍 Entry: ${fill_price:.6f}\n"
            f"🎯 TP: ${signal['tp_price']:.6f} (+{signal['tp_pct']:.1f}%)\n"
            f"🛑 SL: ${signal['sl_price']:.6f} (-{signal['sl_pct']:.1f}%)"
        )

        log.info(f"✅ Position opened: {symbol} @ ${fill_price:.6f}")
        return True

    def _calculate_quantity(self, symbol: str, price: float) -> Optional[float]:
        contract = self.contracts_info.get(symbol, {})
        min_qty = contract.get('min_qty', 1)
        precision = contract.get('qty_precision', 2)
        contract_size = contract.get('contract_size', 1)
        
        notional = POSITION_SIZE * LEVERAGE
        qty = safe_div(notional, price * contract_size, 0)
        qty = math.ceil(qty / min_qty) * min_qty
        qty = round(qty, precision)
        
        return qty if qty >= min_qty else None

    def _confirm_position(self, symbol: str, timeout: int = 10) -> Tuple[Optional[float], Optional[float]]:
        for _ in range(timeout):
            try:
                data = api_request('GET', '/openApi/swap/v2/user/positions', {'symbol': symbol})
                for pos in data.get('data', []):
                    amt = safe_float(pos.get('positionAmt', 0))
                    if abs(amt) > 0:
                        entry = safe_float(pos.get('avgPrice', 0))
                        return abs(amt), entry
            except:
                pass
            time.sleep(1)
        return None, None

    async def monitor_positions(self):
        """Monitor positions"""
        for symbol in list(self.positions.keys()):
            try:
                pos = self.positions[symbol]
                ticker = self._get_ticker(symbol)
                if not ticker:
                    continue

                current_price = ticker['price']
                
                if current_price > pos.get('highest', pos['entry']):
                    pos['highest'] = current_price

                # TP
                if current_price >= pos.get('tp_price', float('inf')):
                    self._close_position(symbol, current_price, "TP")
                # SL
                elif current_price <= pos.get('sl_price', 0):
                    self._close_position(symbol, current_price, "SL")

            except Exception as e:
                log.error(f"Monitor error {symbol}: {e}")

    def _close_position(self, symbol: str, price: float, reason: str):
        if symbol not in self.positions:
            return
        
        pos = self.positions[symbol]
        qty = pos['qty']
        
        if qty > 0 and AUTO_TRADING:
            api_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol': symbol, 'side': 'SELL', 'type': 'MARKET',
                'quantity': str(qty), 'positionSide': 'LONG'
            })
        
        # PnL
        pnl_pct = safe_div(price - pos['entry'], pos['entry'], 0) * 100
        
        win = pnl_pct > 0
        if win:
            self.stats['wins'] += 1
        else:
            self.stats['losses'] += 1
        
        total = self.stats['wins'] + self.stats['losses']
        wr = safe_div(self.stats['wins'], total, 0) * 100
        
        log.info(f"{'✅' if win else '❌'} {reason} {symbol} | {pnl_pct:+.2f}% | WR:{wr:.0f}%")
        
        self._send_telegram(
            f"<b>{'✅ WIN' if win else '❌ LOSS'}</b>\n\n"
            f"{symbol} — {reason}\n"
            f"PnL: <b>{pnl_pct:+.2f}%</b>\n"
            f"WR: {wr:.0f}% ({self.stats['wins']}/{total})"
        )
        
        del self.positions[symbol]

    def _send_telegram(self, msg: str):
        if not TG_TOKEN or not TG_CHAT:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                timeout=5
            )
        except:
            pass

    async def run(self):
        """Main loop"""
        log.info("\n🔥 Ultra-Aggressive Bot v6.0 RUNNING\n")
        iteration = 0

        while True:
            try:
                iteration += 1

                if iteration % 10 == 0:
                    self._refresh_symbols()
                    if AUTO_TRADING:
                        data = api_request('GET', '/openApi/swap/v2/user/balance')
                        if data.get('code') == 0:
                            eq = extract_equity(data)
                            if eq > 0:
                                self.equity = eq

                total = self.stats['wins'] + self.stats['losses']
                wr = safe_div(self.stats['wins'], total, 0) * 100

                log.info(f"\n{'='*80}")
                log.info(f"SCAN #{iteration} | Positions: {len(self.positions)}/{MAX_POSITIONS}")
                log.info(f"Stats: {self.stats['wins']}W / {self.stats['losses']}L | WR: {wr:.0f}%")
                log.info(f"{'='*80}")

                # Monitor existing
                await self.monitor_positions()

                # Scan for new
                if len(self.positions) < MAX_POSITIONS:
                    signals = self.scan_for_signals()
                    
                    for signal in signals:
                        if len(self.positions) >= MAX_POSITIONS:
                            break
                        
                        if self.open_position(signal):
                            await asyncio.sleep(2)

                await asyncio.sleep(SCAN_INTERVAL)

            except KeyboardInterrupt:
                log.info("⏹️ Bot stopped")
                break
            except Exception as e:
                log.error(f"Loop error: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(30)

async def main():
    bot = UltraAggressiveBot()
    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("👋 Bot v6.0 terminated")
