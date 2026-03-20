#!/usr/bin/env python3
"""
BOT DE TRADING PROFESIONAL v3.0.2 OPTIMIZADO
Estrategia: EMA + RSI + MACD + Bollinger + Volumen + Trailing Stop

MEJORAS v3.0.2:
- PnL REAL: considera leverage + comisiones BingX (0.1%)
- Capital FORZADO: 7 USDT por trade (validación estricta)
- Solo UNA dirección por moneda (evita hedge accidental)
- Win rate REAL basado en PnL corregido
- SOLO LONGS: SHORTS deshabilitados (perdían 90% de trades)
- Filtro mejorado: NUNCA opera índices (DowJones, SP500, etc)
- Score más estricto: 75 puntos mínimo
- Penalizaciones: RSI alto, volumen bajo, tendencia contraria
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math
from datetime import datetime
from urllib.parse import urlencode

# ============================================================================
# CONFIGURACION
# ============================================================================

def clean(key, default, typ='str'):
    v = os.getenv(key, str(default)).strip().strip('"').strip("'").strip()
    if typ == 'int':   return int(v)
    if typ == 'float': return float(v)
    if typ == 'bool':  return v.lower() == 'true'
    return v

BINGX_API_KEY    = os.getenv('BINGX_API_KEY',    '').strip().strip('"').strip("'")
BINGX_API_SECRET = os.getenv('BINGX_API_SECRET', '').strip().strip('"').strip("'")
TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT    = os.getenv('TELEGRAM_CHAT_ID',   '')

# CORREGIDO: AUTO_TRADING true por defecto, capital 7 USDT
AUTO_TRADING  = clean('AUTO_TRADING_ENABLED',  'true',  'bool')
POSITION_SIZE = clean('MAX_POSITION_SIZE',       '7',   'float')  # <-- 7 USDT
MIN_TRADE     = clean('MIN_TRADE_USDT',          '5',   'float')  # <-- min 5 USDT
LEVERAGE      = clean('LEVERAGE',                '3',   'int')    # <-- 3x leverage
TP_PCT        = clean('TAKE_PROFIT_PCT',         '3.5', 'float')  # <-- mejorado
SL_PCT        = clean('STOP_LOSS_PCT',           '1.0', 'float')  # <-- mejorado
MAX_TRADES    = clean('MAX_OPEN_TRADES',         '2',   'int')    # <-- más selectivo
INTERVAL      = clean('CHECK_INTERVAL',          '60',   'int')
MIN_VOLUME    = clean('MIN_VOLUME_24H',      '500000',   'float')
MAX_SYMBOLS   = clean('MAX_SYMBOLS_TO_ANALYZE',  '80',   'int')
MIN_SCORE     = clean('MIN_SCORE',               '75',   'float')  # <-- más estricto
TRAILING      = clean('TRAILING_STOP_ENABLED',  'true',  'bool')

# TEMPORAL: Deshabilitar SHORTS (pierden 90% de las veces)
ENABLE_SHORTS = clean('ENABLE_SHORT_TRADES',    'false', 'bool')  # <-- SHORTS OFF

BASE_URL = "https://open-api.bingx.com"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ============================================================================
# FIRMA BINGX
# ============================================================================

def bingx_request(method, endpoint, params):
    params['timestamp'] = int(time.time() * 1000)
    sp  = sorted(params.items())
    qs  = urlencode(sp)
    sig = hmac.new(BINGX_API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE_URL}{endpoint}?{qs}&signature={sig}"
    hdr = {'X-BX-APIKEY': BINGX_API_KEY, 'Content-Type': 'application/x-www-form-urlencoded'}
    if method == 'GET':
        return requests.get(url, headers=hdr, timeout=10)
    return requests.post(url, headers=hdr, timeout=10)

# ============================================================================
# INDICADORES TECNICOS
# ============================================================================

def calc_ema(prices, period):
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0
    k = 2 / (period + 1)
    e = prices[0]
    for p in prices[1:]:
        e = p * k + e * (1 - k)
    return e

def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains  = [max(0,  prices[i] - prices[i-1]) for i in range(1, len(prices))]
    losses = [max(0, prices[i-1] - prices[i])  for i in range(1, len(prices))]
    ag = sum(gains[-period:])  / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return 100 - (100 / (1 + ag / al))

def calc_macd(prices):
    if len(prices) < 26:
        return 0, 0, 0
    fast = calc_ema(prices, 12)
    slow = calc_ema(prices, 26)
    ml   = fast - slow
    sig  = ml * 0.9
    return ml, sig, ml - sig

def calc_bollinger(prices, period=20):
    if len(prices) < period:
        m = sum(prices) / len(prices)
        return m, m, m
    w   = prices[-period:]
    mid = sum(w) / period
    std = (sum((p - mid)**2 for p in w) / period) ** 0.5
    return mid + 2*std, mid, mid - 2*std

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < 2:
        return 0
    trs = []
    for i in range(1, min(len(closes), period+1)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1])
        ))
    return sum(trs) / len(trs) if trs else 0

def vol_spike(volumes):
    if len(volumes) < 5:
        return 1.0
    avg = sum(volumes[:-1]) / len(volumes[:-1])
    return (volumes[-1] / avg) if avg > 0 else 1.0

# ============================================================================
# BOT
# ============================================================================

class TradingBot:

    def __init__(self):
        log.info("=" * 70)
        log.info("BOT TRADING PROFESIONAL v3.0.2 OPTIMIZADO")
        log.info("SOLO LONGS | PnL REAL | Capital 7 USDT")
        log.info("=" * 70)
        log.info(f"AUTO-TRADING:   {'ON - EJECUTANDO TRADES REALES' if AUTO_TRADING else 'OFF'}")
        log.info(f"Capital:        ${POSITION_SIZE} USDT (min ${MIN_TRADE})")
        log.info(f"  ⚠️ CRÍTICO: Si ves $100 USDT, cambia MAX_POSITION_SIZE=7 en Railway")
        log.info(f"Leverage:       {LEVERAGE}x => posicion ${POSITION_SIZE * LEVERAGE}")
        log.info(f"TP/SL:          {TP_PCT}% / {SL_PCT}%  (RR {TP_PCT/SL_PCT:.1f}:1)")
        log.info(f"Max trades:     {MAX_TRADES}")
        log.info(f"Score minimo:   {MIN_SCORE}/100")
        log.info(f"Volumen min:    ${MIN_VOLUME/1e6:.1f}M")
        log.info(f"Trailing stop:  {'ON' if TRAILING else 'OFF'} (activa +0.5%)")
        log.info(f"Direcciones:    SOLO LONGS ({'SHORTS OFF - perdían 90%' if not ENABLE_SHORTS else 'SHORTS ON'})")
        log.info(f"Proteccion:     1 direccion/moneda | Sin índices bursátiles")
        log.info("=" * 70)

        self.symbols         = []
        self.open_trades     = {}
        self._contracts      = {}
        self.stats = {'exec':0,'closed':0,'wins':0,'losses':0,'pnl':0.0}

        self._verify()
        self._load_contracts()
        self._get_symbols()

        self._tg(
            f"<b>Bot v3.0.2 OPTIMIZADO iniciado</b>\n"
            f"{'AUTO ON - trades reales' if AUTO_TRADING else 'Solo señales'}\n"
            f"Capital: ${POSITION_SIZE} x{LEVERAGE} | TP:{TP_PCT}% SL:{SL_PCT}% RR:{TP_PCT/SL_PCT:.1f}\n"
            f"Score min:{MIN_SCORE} | MaxTrades:{MAX_TRADES} | Trailing:0.5%\n"
            f"🔴 SOLO LONGS (SHORTS OFF - perdían 90%)\n"
            f"PnL REAL (leverage + comisiones) | Solo 1 dir/moneda\n"
            f"Filtros: Sin índices bursátiles (DowJones, etc)"
        )

    def _verify(self):
        global AUTO_TRADING
        if not AUTO_TRADING:
            log.info("Modo SEÑALES - trades desactivados")
            return
        if not BINGX_API_KEY or not BINGX_API_SECRET:
            log.error("Credenciales faltantes")
            AUTO_TRADING = False
            return
        log.info(f"API Key: {BINGX_API_KEY[:12]}...")
        try:
            r = bingx_request('GET', '/openApi/swap/v2/user/balance', {})
            d = r.json()
            if d.get('code') == 0:
                bal = d.get('data', {})
                eq  = bal.get('equity', bal.get('balance', '?'))
                log.info(f"BingX OK | Balance: ${eq} USDT")
            else:
                log.error(f"Error BingX [{d.get('code')}]: {d.get('msg')}")
                AUTO_TRADING = False
        except Exception as e:
            log.error(f"Error: {e}")
            AUTO_TRADING = False

    def _load_contracts(self):
        try:
            r = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/contracts", timeout=15)
            d = r.json()
            if d.get('code') == 0:
                for c in d.get('data', []):
                    self._contracts[c.get('symbol','')] = {
                        'step': float(c.get('tradeMinQuantity', 1)),
                        'prec': int(c.get('quantityPrecision', 2)),
                    }
                log.info(f"Contratos cargados: {len(self._contracts)}")
        except Exception as e:
            log.warning(f"Error contratos: {e}")

    def _get_symbols(self):
        # CRÍTICO: Excluir TODO lo que NO sea criptomoneda pura
        excl = {
            # Metales y commodities
            'GOLD','SILVER','XAG','XAU','PAXG','XAUT','OIL','BRENT','WTI','CRUDE',
            'PLATINUM','PALLADIUM','COPPER','NICKEL',
            # Acciones
            'TSLA','AAPL','MSFT','GOOGL','AMZN','META','NVDA','COIN','MSTR',
            'TESLA','APPLE','MICROSOFT','GOOGLE','AMAZON','FACEBOOK',
            # Índices bursátiles (CRÍTICO)
            'SP500','SPX','SPY','QQQ','NASDAQ','DOW','DOWJONES','DJI','RUSSELL',
            'DAX','FTSE','CAC','NIKKEI','HANG','HSI','BOVESPA','IBEX',
            'DOW30','DJIA','US30','NAS100','US500',
            # Forex
            'EUR','GBP','JPY','CHF','AUD','CAD','NZD','100','1000',
            'EURUSD','GBPUSD','USDJPY','AUDUSD','NZDUSD',
            # Otros
            'WHEAT','CORN','SUGAR','COFFEE','COTTON','LUMBER',
        }
        
        # Lista blanca de criptos conocidas (opcional pero más seguro)
        cripto_whitelist = {
            'BTC','ETH','SOL','BNB','XRP','DOGE','ADA','AVAX','LINK','DOT',
            'MATIC','UNI','ATOM','LTC','BCH','NEAR','FIL','APT','ARB','OP',
            'INJ','SUI','SEI','TIA','JUP','PEPE','SHIB','FLOKI','BONK',
        }
        
        try:
            r = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker", timeout=15)
            d = r.json()
            if d.get('code') == 0:
                items = []
                excluded_symbols = []
                for t in d.get('data', []):
                    sym = t.get('symbol','')
                    if not sym.endswith('-USDT'):
                        continue
                    
                    base = sym.replace('-USDT','').upper()
                    
                    # FILTRO 1: Lista negra (índices, acciones, commodities)
                    if any(k in base for k in excl):
                        excluded_symbols.append(base)
                        continue
                    
                    # FILTRO 2: Contiene palabras de índices/acciones
                    if any(word in base for word in ['DOW','JONES','NASDAQ','INDEX','SP500','STOCK']):
                        excluded_symbols.append(base)
                        continue
                    
                    # FILTRO 3: Whitelist (opcional - comentar si da problemas)
                    # if base not in cripto_whitelist:
                    #     continue
                    
                    try:
                        vol = float(t.get('volume',0)) * float(t.get('lastPrice',0))
                        if vol < MIN_VOLUME or float(t.get('lastPrice',0)) < 0.0001:
                            continue
                        items.append({'symbol':sym, 'vol':vol})
                    except:
                        continue
                
                items.sort(key=lambda x: x['vol'], reverse=True)
                self.symbols = [x['symbol'] for x in items[:MAX_SYMBOLS]]
                
                if excluded_symbols:
                    log.info(f"Excluidos {len(excluded_symbols)} no-cripto: {', '.join(excluded_symbols[:10])}")
                log.info(f"{len(self.symbols)} criptomonedas puras (vol>${MIN_VOLUME/1e6:.1f}M)")
                return
        except Exception as e:
            log.warning(f"Error simbolos: {e}")
        
        # Fallback solo criptos TOP conocidas
        self.symbols = ['BTC-USDT','ETH-USDT','SOL-USDT','BNB-USDT','XRP-USDT',
                        'DOGE-USDT','ADA-USDT','AVAX-USDT','LINK-USDT','MATIC-USDT']

    # ---------------------------------------------------------------- DATOS

    def _klines(self, symbol, interval='5m', limit=50):
        try:
            r = requests.get(
                f"{BASE_URL}/openApi/swap/v3/quote/klines",
                params={'symbol':symbol,'interval':interval,'limit':limit},
                timeout=10
            )
            d = r.json()
            if d.get('code') == 0 and d.get('data'):
                k = d['data']
                return (
                    [float(x['close'])  for x in k],
                    [float(x['high'])   for x in k],
                    [float(x['low'])    for x in k],
                    [float(x['volume']) for x in k],
                )
        except:
            pass
        return None, None, None, None

    def _ticker(self, symbol):
        try:
            r = requests.get(
                f"{BASE_URL}/openApi/swap/v2/quote/ticker",
                params={'symbol':symbol}, timeout=8
            )
            d = r.json()
            if d.get('code') == 0 and d.get('data'):
                t = d['data']
                return {
                    'price':  float(t.get('lastPrice',0)),
                    'change': float(t.get('priceChangePercent',0)),
                }
        except:
            pass
        return None

    # ---------------------------------------------------------------- SEÑAL

    def analyze(self, symbol):
        if symbol in self.open_trades:
            return None

        closes, highs, lows, volumes = self._klines(symbol, '5m', 50)
        if not closes or len(closes) < 20:
            return None

        ticker = self._ticker(symbol)
        if not ticker or ticker['price'] <= 0:
            return None

        price  = ticker['price']
        change = ticker['change']

        # --- Indicadores ---
        ema9  = calc_ema(closes, 9)
        ema21 = calc_ema(closes, 21)
        ema50 = calc_ema(closes, min(50, len(closes)))
        rsi_v = calc_rsi(closes, 14)
        ml, sg, hist = calc_macd(closes)
        bb_u, bb_m, bb_l = calc_bollinger(closes, 20)
        atr_v = calc_atr(highs, lows, closes, 14)
        vspike = vol_spike(volumes)

        ema_bull = ema9 > ema21 > ema50
        ema_bear = ema9 < ema21 < ema50
        ema_gap  = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0

        short_trend = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0

        # --- Score LONG (más estricto para mejor win rate) ---
        ls, lr = 0, []
        
        # EMA alineación alcista (MUY IMPORTANTE - 30 pts)
        if ema_bull:
            if ema_gap > 1.0:
                p = 30 + min(15, ema_gap*10); ls += p; lr.append(f"EMA++({p:.0f})")
            else:
                p = 25 + min(10, ema_gap*8); ls += p; lr.append(f"EMA+({p:.0f})")
        else:
            # Penalizar si NO hay alineación alcista
            ls -= 10; lr.append("SinEMA(-10)")
        
        # RSI oversold (fuerte señal de compra)
        if rsi_v < 25:
            ls += 35; lr.append("RSI<25(35)")
        elif rsi_v < 35:
            ls += 25; lr.append("RSI<35(25)")
        elif rsi_v < 45:
            ls += 15; lr.append("RSI<45(15)")
        elif rsi_v > 65:
            # Penalizar comprar con RSI alto
            ls -= 15; lr.append("RSI>65(-15)")
        
        # MACD confirmación
        if ml > sg and hist > 0:
            if hist > abs(ml) * 0.3:  # Histograma fuerte
                ls += 25; lr.append("MACD++(25)")
            else:
                ls += 18; lr.append("MACD+(18)")
        
        # Bollinger Bands (precio bajo)
        if price <= bb_l * 1.002:
            ls += 22; lr.append("BB-low(22)")
        elif price <= bb_m * 0.98:
            ls += 12; lr.append("BB-mid(12)")
        elif price > bb_u:
            # Penalizar comprar en banda superior
            ls -= 12; lr.append("BB-high(-12)")
        
        # Volumen spike (confirmación fuerte)
        if vspike >= 2.5:
            p = min(25, vspike*9); ls += p; lr.append(f"Vol{vspike:.1f}x({p:.0f})")
        elif vspike >= 1.8:
            p = min(18, vspike*8); ls += p; lr.append(f"Vol{vspike:.1f}x({p:.0f})")
        elif vspike < 1.2:
            # Penalizar bajo volumen
            ls -= 8; lr.append("VolBajo(-8)")
        
        # Tendencia corto plazo
        if short_trend > 1.0:
            ls += 18; lr.append("trend++(18)")
        elif short_trend > 0.4:
            ls += 10; lr.append("trend+(10)")
        elif short_trend < -0.5:
            # Penalizar tendencia bajista
            ls -= 10; lr.append("trend-(-10)")
        
        # Cambio 24h
        if change > 3.0:
            p = min(15, change * 3); ls += p; lr.append(f"24h+{change:.1f}%({p:.0f})")
        elif change > 1.5:
            p = min(10, change * 2); ls += p; lr.append(f"24h+{change:.1f}%({p:.0f})")
        elif change < -2.0:
            # Penalizar caída fuerte 24h
            ls -= 10; lr.append(f"24h{change:.1f}%(-10)")

        # --- Score SHORT ---
        ss, sr = 0, []
        if ema_bear:
            p = 25 + min(15, ema_gap*8); ss += p; sr.append(f"EMA-({p:.0f})")
        if rsi_v > 70:
            ss += 30; sr.append("RSI>70(30)")
        elif rsi_v > 60:
            ss += 18; sr.append("RSI>60(18)")
        if ml < sg and hist < 0:
            ss += 20; sr.append("MACD-(20)")
        if price >= bb_u * 0.995:
            ss += 20; sr.append("BB-high(20)")
        if vspike >= 1.5:
            p = min(20, vspike*8); ss += p; sr.append(f"Vol{vspike:.1f}x({p:.0f})")
        if short_trend < -0.3:
            ss += 10; sr.append("trend-(10)")
        if change < -1.0:
            p = min(12, abs(change)*3); ss += p; sr.append(f"24h{change:.1f}%({p:.0f})")

        # TP dinamico basado en ATR
        atr_pct = (atr_v / price * 100) if price > 0 else 0
        tp_dyn  = max(TP_PCT, min(TP_PCT * 2.5, atr_pct * 2.0))

        # --- Decision ---
        if ls > ss and ls >= MIN_SCORE and rsi_v <= 70:
            return {'signal':'LONG',  'price':price,'change':change,'score':ls,
                    'reasons':' | '.join(lr),'rsi':rsi_v,'vol':vspike,'tp_pct':tp_dyn,'sl_pct':SL_PCT}

        # SHORTS: solo si están habilitados
        if ENABLE_SHORTS:
            if ss > ls and ss >= MIN_SCORE and rsi_v >= 30:
                return {'signal':'SHORT', 'price':price,'change':change,'score':ss,
                        'reasons':' | '.join(sr),'rsi':rsi_v,'vol':vspike,'tp_pct':tp_dyn,'sl_pct':SL_PCT}

        return None

    # ---------------------------------------------------------------- CANTIDAD

    def _qty(self, symbol, price):
        """Calcular cantidad respetando POSITION_SIZE de 7 USDT (min 5 USDT)"""
        info  = self._contracts.get(symbol, {'step':1.0,'prec':2})
        step  = info['step']
        prec  = info['prec']
        
        # FORZAR: usar exactamente POSITION_SIZE (7 USDT)
        capital = POSITION_SIZE
        if capital < MIN_TRADE:
            capital = MIN_TRADE
        
        log.info(f"  Calculando cantidad para ${capital:.2f} USDT a precio ${price:.6f}")
        
        raw   = capital / price
        stepped = math.ceil(raw / step) * step if step > 0 else raw
        qty   = round(stepped, prec)
        val   = qty * price
        
        # Ajustar si quedo por debajo del minimo
        i = 0
        while val < MIN_TRADE and step > 0 and i < 1000:
            qty += step
            qty = round(qty, prec)
            val = qty * price
            i += 1
        
        # VALIDACIÓN FINAL: nunca más de 10 USDT
        if val > 10:
            log.warning(f"  ADVERTENCIA: Capital ${val:.2f} > 10 USDT, ajustando...")
            qty = (POSITION_SIZE / price)
            qty = round(math.floor(qty / step) * step, prec)
            val = qty * price
        
        log.info(f"  Cantidad final: {qty} (${val:.2f} USDT)")
        return qty, round(val, 4)

    # ---------------------------------------------------------------- VERIFICAR POSICION

    def _tiene_posicion_bingx(self, symbol):
        """Verificar si hay posicion en BingX y su dirección"""
        try:
            r = bingx_request('GET', '/openApi/swap/v2/user/positions', {'symbol': symbol})
            d = r.json()
            if d.get('code') == 0:
                for p in (d.get('data') or []):
                    amt = float(p.get('positionAmt', 0) or 0)
                    if abs(amt) > 0:
                        direccion = 'LONG' if amt > 0 else 'SHORT'
                        return True, direccion
        except Exception as e:
            log.debug(f"  _tiene_posicion_bingx {symbol}: {e}")
        return False, None

    # ---------------------------------------------------------------- TRADING

    def open_trade(self, symbol, sig):
        if not AUTO_TRADING:
            log.info(f"  SEÑAL {sig['signal']} {symbol} score:{sig['score']:.0f} [AUTO-TRADING OFF]")
            return False

        direction = sig['signal']
        price = sig['price']

        # BLOQUEO 1: ya registrado localmente
        if symbol in self.open_trades:
            log.info(f"  {symbol} ya en open_trades locales - saltando")
            return False

        # BLOQUEO 2: verificar en BingX - solo una direccion por moneda
        if AUTO_TRADING:
            tiene_pos, pos_dir = self._tiene_posicion_bingx(symbol)
            if tiene_pos:
                if pos_dir == direction:
                    # Misma direccion - registrar y saltar
                    log.info(f"  {symbol} ya tiene {direction} en BingX - saltando")
                    self.open_trades[symbol] = {
                        'direction': direction, 'entry': price,
                        'qty': 0, 'val': 0, 'tp': 0, 'sl': 0,
                        'tp_pct': sig['tp_pct'], 'sl_pct': sig['sl_pct'],
                        'highest': price, 'lowest': price,
                        'order_id': 'EXISTENTE', 'tp_ok': False, 'sl_ok': False,
                        'opened_at': datetime.now(), 'score': sig['score'],
                    }
                    return False
                else:
                    # Direccion contraria - RECHAZAR
                    log.warning(f"  ⚠️ {symbol} RECHAZADO: ya existe {pos_dir}, queria {direction}")
                    log.warning(f"  REGLA: Solo una direccion por moneda")
                    return False

        qty, val = self._qty(symbol, price)
        
        # VALIDACIÓN CRÍTICA: rechazar si capital > 10 USDT
        if val > 10:
            log.error(f"  {symbol} RECHAZADO: capital ${val:.2f} > 10 USDT")
            return False

        if val < MIN_TRADE:
            log.warning(f"  {symbol} rechazado ${val:.2f} < ${MIN_TRADE}")
            return False
        
        log.info(f"  ✓ Capital validado: ${val:.2f} USDT")

        tp_pct = sig['tp_pct']
        sl_pct = sig['sl_pct']
        tp = price*(1+tp_pct/100) if direction=='LONG' else price*(1-tp_pct/100)
        sl = price*(1-sl_pct/100) if direction=='LONG' else price*(1+sl_pct/100)

        log.info(f"\n  Abriendo {direction} {symbol}")
        log.info(f"  Score:{sig['score']:.0f} RSI:{sig['rsi']:.1f} Vol:{sig['vol']:.1f}x")
        log.info(f"  {sig['reasons']}")
        log.info(f"  Entry:${price:.6f} Qty:{qty} (${val:.2f}) TP:{tp_pct:.1f}% SL:{sl_pct:.1f}%")

        # Orden mercado
        r = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol': symbol,
            'side':   'BUY' if direction=='LONG' else 'SELL',
            'positionSide': direction,
            'type':   'MARKET',
            'quantity': str(qty),
        })
        d = r.json()
        if d.get('code') != 0:
            log.error(f"  Error [{d.get('code')}]: {d.get('msg')}")
            return False

        oid = d.get('data',{}).get('orderId','N/A')
        log.info(f"  Abierto ID:{oid}")

        time.sleep(0.4)
        tp_ok = self._cond_order(symbol, direction, qty, tp, 'TAKE_PROFIT_MARKET')
        time.sleep(0.4)
        sl_ok = self._cond_order(symbol, direction, qty, sl, 'STOP_MARKET')

        self.open_trades[symbol] = {
            'direction': direction, 'entry': price, 'qty': qty, 'val': val,
            'tp': tp, 'sl': sl, 'tp_pct': tp_pct, 'sl_pct': sl_pct,
            'highest': price, 'lowest': price,
            'order_id': oid, 'tp_ok': tp_ok, 'sl_ok': sl_ok,
            'opened_at': datetime.now(), 'score': sig['score'],
        }
        self.stats['exec'] += 1

        self._tg(
            f"<b>TRADE ABIERTO</b>\n"
            f"{direction} {symbol} | Score:{sig['score']:.0f}/100\n"
            f"Entry: ${price:.4f}\n"
            f"{'OK' if tp_ok else 'ERR'} TP: ${tp:.4f} (+{tp_pct:.1f}%)\n"
            f"{'OK' if sl_ok else 'ERR'} SL: ${sl:.4f} (-{sl_pct:.1f}%)\n"
            f"Capital: ${val:.2f} USDT\n"
            f"Leverage: {LEVERAGE}x → Posicion: ${val * LEVERAGE:.2f}\n"
            f"Cantidad: {qty}\n"
            f"{sig['reasons']}"
        )
        return True

    def _cond_order(self, symbol, direction, qty, stop_price, otype):
        try:
            r = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol': symbol,
                'side':   'SELL' if direction=='LONG' else 'BUY',
                'positionSide': direction,
                'type':   otype,
                'quantity': str(qty),
                'stopPrice': str(round(stop_price, 6)),
            })
            d = r.json()
            ok = d.get('code') == 0
            lbl = "TP" if "TAKE" in otype else "SL"
            if ok:
                log.info(f"  {lbl} OK @ ${stop_price:.6f}")
            else:
                log.warning(f"  {lbl} ERR [{d.get('code')}]: {d.get('msg')}")
            return ok
        except Exception as e:
            log.warning(f"  {otype} exc: {e}")
            return False

    def close_trade(self, symbol, cur_price, reason):
        """Cerrar posicion con PnL CORREGIDO (leverage + comisiones)"""
        if symbol not in self.open_trades:
            return False
        t = self.open_trades[symbol]
        try:
            r = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol': symbol,
                'side':   'SELL' if t['direction']=='LONG' else 'BUY',
                'positionSide': t['direction'],
                'type':   'MARKET',
                'quantity': str(t['qty']),
            })
            d = r.json()
            if d.get('code') == 0:
                # PnL CORREGIDO: considerar leverage y comisiones
                COMISION_TOTAL = 0.001  # 0.1% total
                
                if t['direction'] == 'LONG':
                    cambio_pct = (cur_price - t['entry']) / t['entry']
                    pnl = (t['val'] * LEVERAGE * cambio_pct) - (t['val'] * LEVERAGE * COMISION_TOTAL)
                else:
                    cambio_pct = (t['entry'] - cur_price) / t['entry']
                    pnl = (t['val'] * LEVERAGE * cambio_pct) - (t['val'] * LEVERAGE * COMISION_TOTAL)
                
                pnl_pct = (pnl / t['val']) * 100
                
                self.stats['closed'] += 1
                self.stats['pnl']    += pnl
                if pnl > 0: self.stats['wins']   += 1
                else:        self.stats['losses'] += 1
                
                total = self.stats['wins'] + self.stats['losses']
                wr = self.stats['wins'] / total * 100 if total else 0
                mins = int((datetime.now() - t['opened_at']).total_seconds() / 60)
                
                log.info(f"  CERRADO({reason}) {symbol} PnL:${pnl:+.2f}({pnl_pct:+.1f}%) {mins}min")
                self._tg(
                    f"<b>CERRADO - {reason}</b>\n"
                    f"{symbol}\n"
                    f"PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
                    f"Entry: ${t['entry']:.4f} → Exit: ${cur_price:.4f}\n"
                    f"Capital: ${t['val']:.2f} x{LEVERAGE}\n"
                    f"Duracion: {mins} min\n"
                    f"Total PnL: ${self.stats['pnl']:+.2f}\n"
                    f"WR: {wr:.1f}% ({self.stats['wins']}W/{self.stats['losses']}L)"
                )
                del self.open_trades[symbol]
                return True
        except Exception as e:
            log.error(f"  Error cerrando {symbol}: {e}")
        return False

    # ---------------------------------------------------------------- MONITOR

    async def _sync_con_bingx(self):
        """Sincronizar con BingX - PnL CORREGIDO"""
        if not self.open_trades or not AUTO_TRADING:
            return
        try:
            r = bingx_request('GET', '/openApi/swap/v2/user/positions', {})
            d = r.json()
            if d.get('code') != 0:
                return
            
            posiciones_bingx = {}
            for p in (d.get('data') or []):
                sym = p.get('symbol','')
                amt = float(p.get('positionAmt', 0) or 0)
                if abs(amt) > 0:
                    posiciones_bingx[sym] = amt
            
            for symbol in list(self.open_trades.keys()):
                if symbol not in posiciones_bingx:
                    t = self.open_trades[symbol]
                    if t.get('order_id') not in ('EXTERNO', 'EXISTENTE', ''):
                        tk = self._ticker(symbol)
                        cur = tk['price'] if tk else t['entry']
                        
                        # PnL CORREGIDO
                        COMISION_TOTAL = 0.001
                        
                        if t['direction'] == 'LONG':
                            cambio_pct = (cur - t['entry']) / t['entry']
                            pnl = (t['val'] * LEVERAGE * cambio_pct) - (t['val'] * LEVERAGE * COMISION_TOTAL)
                        else:
                            cambio_pct = (t['entry'] - cur) / t['entry']
                            pnl = (t['val'] * LEVERAGE * cambio_pct) - (t['val'] * LEVERAGE * COMISION_TOTAL)
                        
                        pnl_pct = (pnl / t['val']) * 100
                        
                        self.stats['closed'] += 1
                        self.stats['pnl']    += pnl
                        if pnl >= 0: self.stats['wins']   += 1
                        else:        self.stats['losses'] += 1
                        
                        total = self.stats['wins'] + self.stats['losses']
                        wr = self.stats['wins'] / total * 100 if total else 0
                        mins = int((datetime.now() - t['opened_at']).total_seconds() / 60)
                        
                        log.info(f"  SYNC: {symbol} cerrado por BingX PnL=${pnl:+.2f} ({pnl_pct:+.1f}%)")
                        self._tg(
                            f"<b>CERRADO por BingX (TP/SL)</b>\n"
                            f"{symbol}\n"
                            f"PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
                            f"Entry: ${t['entry']:.4f} → Exit: ${cur:.4f}\n"
                            f"Capital: ${t['val']:.2f} x{LEVERAGE}\n"
                            f"Duracion: {mins} min\n"
                            f"Total PnL: ${self.stats['pnl']:+.2f}\n"
                            f"WR: {wr:.1f}% ({self.stats['wins']}W/{self.stats['losses']}L)"
                        )
                    del self.open_trades[symbol]
        except Exception as e:
            log.debug(f"sync_bingx: {e}")

    async def monitor_trades(self):
        """Monitor con trailing stop mejorado (activa +0.5%)"""
        await self._sync_con_bingx()
        
        for symbol in list(self.open_trades.keys()):
            try:
                t  = self.open_trades[symbol]
                tk = self._ticker(symbol)
                if not tk: continue
                cur = tk['price']

                if t['direction'] == 'LONG':
                    pnl_pct = (cur - t['entry']) / t['entry'] * 100
                    hit_tp  = cur >= t['tp']
                    hit_sl  = cur <= t['sl']
                    
                    # Trailing MEJORADO: activar con 0.5% ganancia
                    if TRAILING and cur > t['highest']:
                        t['highest'] = cur
                        if pnl_pct >= 0.5:
                            profit = cur - t['entry']
                            new_sl = t['entry'] + (profit * 0.6)
                            if new_sl > t['sl']:
                                t['sl'] = new_sl
                                log.info(f"  Trailing SL {symbol} -> ${new_sl:.4f} (protege {pnl_pct*0.6:.1f}%)")
                else:
                    pnl_pct = (t['entry'] - cur) / t['entry'] * 100
                    hit_tp  = cur <= t['tp']
                    hit_sl  = cur >= t['sl']
                    
                    if TRAILING and cur < t['lowest']:
                        t['lowest'] = cur
                        if pnl_pct >= 0.5:
                            profit = t['entry'] - cur
                            new_sl = t['entry'] - (profit * 0.6)
                            if new_sl < t['sl']:
                                t['sl'] = new_sl
                                log.info(f"  Trailing SL {symbol} -> ${new_sl:.4f} (protege {pnl_pct*0.6:.1f}%)")

                if abs(pnl_pct) > 0.4:
                    log.info(f"  {symbol} {t['direction']} PnL:{pnl_pct:+.1f}% ${cur:.4f} SL:${t['sl']:.4f}")

                if hit_tp:   self.close_trade(symbol, cur, "TAKE PROFIT")
                elif hit_sl: self.close_trade(symbol, cur, "STOP LOSS")

            except Exception as e:
                log.debug(f"Monitor {symbol}: {e}")

    # ---------------------------------------------------------------- TELEGRAM

    def _tg(self, msg):
        try:
            if TELEGRAM_TOKEN and TELEGRAM_CHAT:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={'chat_id': TELEGRAM_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                    timeout=5
                )
        except: pass

    # ---------------------------------------------------------------- LOOP

    async def run(self):
        log.info("\nBot arrancado - trading automatico\n")
        iteration = 0
        last_refresh = 0

        while True:
            try:
                iteration += 1
                now = time.time()

                if now - last_refresh > 600:
                    log.info("Actualizando monedas...")
                    self._get_symbols()
                    last_refresh = now

                total = self.stats['wins'] + self.stats['losses']
                wr    = self.stats['wins'] / total * 100 if total else 0

                log.info(f"\n{'='*70}")
                log.info(
                    f"#{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                    f"Trades:{len(self.open_trades)}/{MAX_TRADES} | "
                    f"PnL:${self.stats['pnl']:+.2f} | "
                    f"WR:{wr:.1f}%({self.stats['wins']}W/{self.stats['losses']}L)"
                )
                log.info(f"AUTO-TRADING: {'ON' if AUTO_TRADING else 'OFF'}")
                log.info(f"{'='*70}\n")

                await self.monitor_trades()

                if len(self.open_trades) < MAX_TRADES:
                    found = 0
                    for i, sym in enumerate(self.symbols):
                        if len(self.open_trades) >= MAX_TRADES:
                            break
                        sig = self.analyze(sym)
                        if sig:
                            found += 1
                            log.info(
                                f"  SEÑAL {sig['signal']} {sym} "
                                f"score:{sig['score']:.0f} RSI:{sig['rsi']:.0f} Vol:{sig['vol']:.1f}x"
                            )
                            self.open_trade(sym, sig)
                        await asyncio.sleep(0.15)
                        if (i+1) % 20 == 0:
                            log.info(f"  {i+1}/{len(self.symbols)} analizadas")

                    log.info(f"\n  {len(self.symbols)} monedas | {found} señales")
                else:
                    log.info(f"  Max trades ({MAX_TRADES}) - esperando")

                log.info(f"\n  Proxima en {INTERVAL}s\n")
                await asyncio.sleep(INTERVAL)

            except KeyboardInterrupt:
                log.info("Detenido"); break
            except Exception as e:
                log.error(f"Error loop: {e}")
                await asyncio.sleep(15)

# ============================================================================
# MAIN
# ============================================================================

async def main():
    try:
        await TradingBot().run()
    except Exception as e:
        log.error(f"Error fatal: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Terminado")
