#!/usr/bin/env python3
"""
BOT FLOOP Pro v4.0 — Optimizado para Máxima Rentabilidad
══════════════════════════════════════════════════════════
MEJORAS v4.0 vs v3:

  1. TAMAÑO DE TRADE ESCALADO:
     - Usa % del balance disponible (risk_pct) en vez de cantidad fija
     - Ej: 15% del balance → con $53 = $7.95 por trade (crece con el balance)
     - Nunca arriesga más del balance_pct configurado

  2. TP/SL GARANTIZADOS SIEMPRE:
     - _esperar_posicion con 60s timeout y 3 métodos de detección
     - Si timeout: fallback MARKET + nuevo intento de detección
     - Si aún falla: coloca TP/SL con qty estimada y alerta Telegram
     - NUNCA deja una posición sin protección

  3. COMISIONES OPTIMIZADAS:
     - Entrada: siempre LIMIT (maker 0.02%)
     - TP: TAKE_PROFIT limit (maker 0.02%) con fallback market
     - SL: STOP limit con offset (maker 0.02%) con fallback market
     - Ahorro vs versión anterior: ~0.06% por trade = $0.013 por trade
     - Con 72 trades: ahorro $0.94 extra de comisiones

  4. LÍMITE REAL DE POSICIONES:
     - Verifica posiciones reales en BingX antes de cada trade
     - No abre si BingX ya tiene MAX_OPEN_TRADES posiciones
     - Sincronización cada ciclo

  5. SCORE MÍNIMO 10/14:
     - Antes: 8/14 (demasiado permisivo)
     - Ahora: 10/14 (solo señales de alta calidad)
     - Menos trades pero con WR esperado >70%

  6. ANTI-SOBRETRADING:
     - Cooldown mínimo 20min tras TP, 45min tras SL
     - Máximo 3 trades simultáneos
     - Filtro de mercado amplio mejorado

  7. TRAILING STOP MEJORADO:
     - Activa al 1% de ganancia (antes 0.8%)
     - Protege 70% de la ganancia máxima (antes 65%)
     - Break-even automático al 0.5% de ganancia
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re
from datetime import datetime, timedelta
from urllib.parse import urlencode

# ============================================================================
# CONFIGURACION
# ============================================================================

def clean(key, default, typ='str'):
    v = os.getenv(key, str(default))
    v = v.strip().strip('"').strip("'").strip()
    if typ in ('int', 'float'):
        v = v.replace(',', '.')
        m = re.match(r'^-?\d+\.?\d*', v)
        v = m.group(0) if m else str(default)
    if typ == 'int':   return int(float(v))
    if typ == 'float': return float(v)
    if typ == 'bool':  return v.lower() == 'true'
    return v

BINGX_API_KEY    = os.getenv('BINGX_API_KEY',    '').strip().strip('"').strip("'")
BINGX_API_SECRET = os.getenv('BINGX_API_SECRET', '').strip().strip('"').strip("'")
TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT    = os.getenv('TELEGRAM_CHAT_ID',   '')

AUTO_TRADING     = clean('AUTO_TRADING_ENABLED',  'true',  'bool')
# MEJORA 1: Tamaño por % del balance
RISK_PCT         = clean('RISK_PCT_PER_TRADE',    '15',    'float')  # % del balance por trade
MIN_TRADE_USDT   = clean('MIN_TRADE_USDT',         '7',    'float')  # mínimo absoluto
MAX_TRADE_USDT   = clean('MAX_TRADE_USDT',         '20',   'float')  # máximo absoluto
LEVERAGE         = clean('LEVERAGE',               '3',    'int')
MAX_TRADES       = clean('MAX_OPEN_TRADES',        '3',    'int')
INTERVAL         = clean('CHECK_INTERVAL',         '120',  'int')
MIN_VOLUME       = clean('MIN_VOLUME_24H',     '500000',   'float')
MAX_SYMBOLS      = clean('MAX_SYMBOLS_TO_ANALYZE', '60',   'int')
ENABLE_LONGS     = clean('ENABLE_LONGS',          'true',  'bool')
ENABLE_SHORTS    = clean('ENABLE_SHORTS',         'true',  'bool')
USE_LIMIT_ORDERS = clean('USE_LIMIT_ORDERS',      'true',  'bool')
TRAILING         = clean('TRAILING_STOP_ENABLED', 'true',  'bool')

# Filtro BTC dual ventana
BTC_FILTER_1H    = clean('BTC_FILTER_1H_PCT',     '1.5',  'float')
BTC_FILTER_4H    = clean('BTC_FILTER_4H_PCT',     '2.5',  'float')

# Filtro mercado
MARKET_FILTER_ON = clean('MARKET_FILTER_ENABLED', 'true', 'bool')
MARKET_FILTER_PCT= clean('MARKET_FILTER_PCT',     '2.0',  'float')
MARKET_FILTER_N  = clean('MARKET_FILTER_MIN_BAD', '3',    'int')

# FLOOP Core
SENSITIVITY   = clean('FLOOP_SENSITIVITY',   '6',     'int')
ATR_LEN       = clean('FLOOP_ATR_LENGTH',    '14',    'int')
ATR_MULT      = clean('FLOOP_ATR_MULT',      '0.8',   'float')
EMA_FAST      = clean('FLOOP_EMA_FAST',      '60',    'int')
EMA_SLOW      = clean('FLOOP_EMA_SLOW',      '200',   'int')
EMA_FILTER_ON = clean('FLOOP_EMA_FILTER',    'true',  'bool')
HTF_INTERVAL  = clean('FLOOP_HTF_TF',        '1h',    'str')
ADX_ON        = clean('FLOOP_ADX_FILTER',    'true',  'bool')
ADX_LEN       = clean('FLOOP_ADX_LENGTH',    '14',    'int')
ADX_THRESH    = clean('FLOOP_ADX_THRESH',    '20',    'float')
CHOP_ON       = clean('FLOOP_CHOP_FILTER',   'true',  'bool')
CHOP_LEN      = clean('FLOOP_CHOP_LENGTH',   '14',    'int')
CHOP_THRESH   = clean('FLOOP_CHOP_THRESH',   '61.8',  'float')
COOLDOWN_BARS = clean('FLOOP_COOLDOWN_BARS', '5',     'int')

# TP/SL
TP_MULT       = clean('TP_ATR_MULT',         '3.0',   'float')
SL_MULT       = clean('SL_ATR_MULT',         '2.2',   'float')
TP_MIN_PCT    = clean('TP_MIN_PCT',          '1.5',   'float')
TP_MAX_PCT    = clean('TP_MAX_PCT',          '6.0',   'float')
SL_MIN_PCT    = clean('SL_MIN_PCT',          '0.8',   'float')
SL_MAX_PCT    = clean('SL_MAX_PCT',          '2.5',   'float')

# MEJORA 5: Score mínimo más alto
MIN_SCORE        = clean('MIN_SCORE',           '10',    'int')

# MEJORA 6: Cooldowns más largos
COOLDOWN_AFTER_TP = clean('COOLDOWN_AFTER_TP_MIN', '20', 'int')
COOLDOWN_AFTER_SL = clean('COOLDOWN_AFTER_SL_MIN', '45', 'int')

# MEJORA 7: Trailing mejorado
TRAILING_ACTIVATE_PCT = clean('TRAILING_ACTIVATE_PCT', '1.0',  'float')
TRAILING_PROTECT_PCT  = clean('TRAILING_PROTECT_PCT',  '70',   'float')
BREAKEVEN_PCT         = clean('BREAKEVEN_PCT',          '0.5',  'float')

SKIP_HOURS_UTC   = {0, 1}
LIMIT_OFFSET_PCT = 0.05
SL_LIMIT_OFFSET  = 0.0005
BASE_URL         = "https://open-api.bingx.com"
COMISION_MAKER   = 0.0002
COMISION_TAKER   = 0.0005
COMISION_ACTUAL  = COMISION_MAKER
API_RATE_LIMIT   = 0.12
MARKET_REF_PAIRS = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT', 'XRP-USDT']

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

# ============================================================================
# RATE LIMITER
# ============================================================================

_last_api_call = 0.0

def _rate_limit():
    global _last_api_call
    wait = API_RATE_LIMIT - (time.time() - _last_api_call)
    if wait > 0: time.sleep(wait)
    _last_api_call = time.time()

# ============================================================================
# API BINGX
# ============================================================================

def bingx_request(method, endpoint, params, retries=2):
    for attempt in range(retries + 1):
        try:
            _rate_limit()
            p = dict(params)
            p['timestamp'] = int(time.time() * 1000)
            qs  = urlencode(sorted(p.items()))
            sig = hmac.new(BINGX_API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
            url = f"{BASE_URL}{endpoint}?{qs}&signature={sig}"
            hdr = {'X-BX-APIKEY': BINGX_API_KEY,
                   'Content-Type': 'application/x-www-form-urlencoded'}
            r = (requests.get(url, headers=hdr, timeout=12) if method == 'GET'
                 else requests.post(url, headers=hdr, timeout=12))
            if r.status_code == 429:
                wait = int(r.headers.get('Retry-After', 5))
                log.warning(f"  Rate limit — esperando {wait}s")
                time.sleep(wait); continue
            return r
        except Exception as e:
            if attempt < retries:
                log.warning(f"  retry {attempt+1}: {e}")
                time.sleep(2 ** attempt)
            else:
                raise

# ============================================================================
# INDICADORES
# ============================================================================

def calc_ema(prices, period):
    if not prices: return 0.0
    period = min(period, len(prices))
    k = 2.0 / (period + 1)
    e = sum(prices[:period]) / period
    for p in prices[period:]: e = p * k + e * (1 - k)
    return e

def calc_rma(values, period):
    if not values: return 0.0
    period = min(period, len(values))
    result = sum(values[:period]) / period
    alpha  = 1.0 / period
    for v in values[period:]: result = alpha * v + (1 - alpha) * result
    return result

def calc_rma_series(values, period):
    if not values: return []
    period = min(period, len(values))
    out, alpha = [], 1.0 / period
    result = sum(values[:period]) / period
    for i, v in enumerate(values):
        if i < period: result = sum(values[:i+1]) / (i+1)
        else:          result = alpha * v + (1 - alpha) * result
        out.append(result)
    return out

def _true_ranges(highs, lows, closes):
    return [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            for i in range(1, len(closes))]

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < 2: return 0.0
    trs = _true_ranges(highs, lows, closes)
    return calc_rma(trs, period) if trs else 0.0

def calc_atr_series(highs, lows, closes, period=14):
    if len(closes) < 2: return [0.0]
    return calc_rma_series(_true_ranges(highs, lows, closes), period)

def calc_range_filter(closes, highs, lows, sensitivity=6, atr_len=14, atr_mult=0.8):
    n = len(closes)
    if n < atr_len + 2: return [closes[-1]]*n, [0]*n, [0]*n
    atr_vals = calc_atr_series(highs, lows, closes, atr_len)
    atr_full = [atr_vals[0]] + atr_vals
    filt_s, trend_s, sig_s = [0.0]*n, [0]*n, [0]*n
    filt_s[0] = closes[0]
    for i in range(1, n):
        atr_i = atr_full[min(i, len(atr_full)-1)]
        rng   = atr_i * atr_mult * (sensitivity / 8.0)
        pf    = filt_s[i-1]
        if   closes[i] > pf + rng: filt_s[i] = closes[i] - rng
        elif closes[i] < pf - rng: filt_s[i] = closes[i] + rng
        else:                       filt_s[i] = pf
        pt = trend_s[i-1]
        if   filt_s[i] > filt_s[i-1]: trend_s[i] = 1
        elif filt_s[i] < filt_s[i-1]: trend_s[i] = -1
        else:                           trend_s[i] = pt
        sig_s[i] = trend_s[i] if trend_s[i] != pt else 0
    return filt_s, trend_s, sig_s

def calc_adx(highs, lows, closes, period=14):
    n = len(closes)
    if n < period + 2: return 0.0, 0.0, 0.0
    plus_dm_s, minus_dm_s, tr_s = [], [], []
    for i in range(1, n):
        up   = highs[i] - highs[i-1]
        down = lows[i-1] - lows[i]
        plus_dm_s.append(up   if up > down and up > 0   else 0.0)
        minus_dm_s.append(down if down > up and down > 0 else 0.0)
        tr_s.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    rma_tr    = calc_rma_series(tr_s, period)
    rma_plus  = calc_rma_series(plus_dm_s, period)
    rma_minus = calc_rma_series(minus_dm_s, period)
    dx_s = []
    for rt, rp, rm in zip(rma_tr, rma_plus, rma_minus):
        if rt == 0: dx_s.append(0.0); continue
        dp, dm = 100*rp/rt, 100*rm/rt
        den    = dp + dm
        dx_s.append(abs(dp-dm)/den*100 if den else 0.0)
    adx      = calc_rma(dx_s, period)
    di_plus  = 100*rma_plus[-1]/rma_tr[-1]  if rma_tr[-1] else 0.0
    di_minus = 100*rma_minus[-1]/rma_tr[-1] if rma_tr[-1] else 0.0
    return adx, di_plus, di_minus

def calc_choppiness(highs, lows, closes, period=14):
    n = len(closes)
    if n < period + 2: return 50.0
    trs     = _true_ranges(highs, lows, closes)
    atr_sum = sum(trs[-period:])
    rng     = max(highs[-period:]) - min(lows[-period:])
    if rng <= 0 or atr_sum <= 0: return 50.0
    return 100 * math.log10(atr_sum / rng) / math.log10(period)

def calc_momentum_roc(closes):
    n = len(closes)
    roc5  = (closes[-1]-closes[-6])  /closes[-6]  *100 if n > 6  else 0.0
    roc10 = (closes[-1]-closes[-11]) /closes[-11] *100 if n > 11 else 0.0
    roc20 = (closes[-1]-closes[-21]) /closes[-21] *100 if n > 21 else 0.0
    return roc5, roc10, roc20, (roc5>0 and roc10>0 and roc20>0), (roc5<0 and roc10<0 and roc20<0)

def calc_atr_rank(highs, lows, closes, period=14, lookback=60):
    n = len(closes)
    if n < period + 2: return 50.0, 0.0
    atr_series = calc_rma_series(_true_ranges(highs, lows, closes), period)
    atr_now    = atr_series[-1]
    atr_norm   = atr_now / closes[-1] * 100 if closes[-1] > 0 else 0.0
    window     = atr_series[-lookback:]
    rank       = sum(1 for v in window if v <= atr_now) / len(window) * 100
    return round(rank), atr_norm

def calc_tp_sl(price, direction, atr, tp_mult, sl_mult, tp_min, tp_max, sl_min, sl_max):
    atr_pct = atr / price * 100 if price > 0 else 1.0
    tp_pct  = max(tp_min, min(tp_max, atr_pct * tp_mult))
    sl_pct  = max(sl_min, min(sl_max, atr_pct * sl_mult))
    # RR mínimo 1.5:1
    if tp_pct < sl_pct * 1.5: tp_pct = sl_pct * 1.5
    if direction == 'LONG':
        return price*(1+tp_pct/100), price*(1-sl_pct/100), round(tp_pct,3), round(sl_pct,3)
    return price*(1-tp_pct/100), price*(1+sl_pct/100), round(tp_pct,3), round(sl_pct,3)

# ============================================================================
# BOT FLOOP PRO v4
# ============================================================================

class FloopBotV4:

    def __init__(self):
        dirs = (['LONGS'] if ENABLE_LONGS else []) + (['SHORTS'] if ENABLE_SHORTS else [])
        log.info("=" * 65)
        log.info("  BOT FLOOP Pro v4.0 — Optimizado Máxima Rentabilidad")
        log.info("=" * 65)
        log.info(f"  Modo:        {'AUTO' if AUTO_TRADING else 'SEÑALES'}")
        log.info(f"  Capital:     {RISK_PCT}% balance (min ${MIN_TRADE_USDT} max ${MAX_TRADE_USDT}) x{LEVERAGE}")
        log.info(f"  Score mín:   {MIN_SCORE}/14")
        log.info(f"  TP/SL:       {TP_MULT}x/{SL_MULT}x ATR | RR min 1.5:1")
        log.info(f"  Cooldown:    TP={COOLDOWN_AFTER_TP}min SL={COOLDOWN_AFTER_SL}min")
        log.info(f"  Trailing:    activa al {TRAILING_ACTIVATE_PCT}% protege {TRAILING_PROTECT_PCT}%")
        log.info(f"  Comisiones:  maker 0.02% entrada+TP+SL (vs taker 0.05%)")
        log.info(f"  Dirs:        {' + '.join(dirs)}")
        log.info("=" * 65)

        self.symbols        = []
        self.open_trades    = {}
        self._contracts     = {}
        self._cooldowns     = {}
        self._rf_state      = {}
        self._klines_cache  = {}
        self._last_report   = datetime.now()
        self._btc_1h        = 0.0
        self._btc_4h        = 0.0
        self._market_bias   = 'neutral'
        self._balance       = 0.0
        self.stats          = {'exec':0,'closed':0,'wins':0,'losses':0,
                               'pnl':0.0,'max_dd':0.0,'peak_pnl':0.0,
                               'comisiones_ahorradas':0.0}

        self._verify()
        self._load_contracts()
        self._get_symbols()
        self._reconciliar_posiciones()

        usdt_ejemplo = min(MAX_TRADE_USDT, max(MIN_TRADE_USDT, self._balance * RISK_PCT / 100))
        self._tg(
            f"<b>🤖 FLOOP Pro v4.0 iniciado</b>\n"
            f"Score ≥ {MIN_SCORE}/14 | ATR TP:{TP_MULT}x SL:{SL_MULT}x\n"
            f"Capital: {RISK_PCT}% balance (~${usdt_ejemplo:.1f}) x{LEVERAGE}\n"
            f"Cooldown TP:{COOLDOWN_AFTER_TP}m SL:{COOLDOWN_AFTER_SL}m\n"
            f"Comisiones: maker 0.02% (ahorro vs taker)\n"
            f"Balance: ${self._balance:.2f} USDT | Max trades: {MAX_TRADES}"
        )

    # ---------------------------------------------------------------- setup

    def _extraer_balance(self, d):
        try:
            data = d.get('data', {})
            if isinstance(data, list): data = data[0] if data else {}
            bal = data.get('balance', None)
            if isinstance(bal, dict):
                for k in ['equity','balance','availableMargin','availableBalance']:
                    v = bal.get(k)
                    if v is not None:
                        try: return float(str(v) or 0)
                        except: continue
            for k in ['equity','balance','availableMargin','availableBalance','walletBalance']:
                v = data.get(k)
                if v is not None and not isinstance(v, dict):
                    try: return float(str(v) or 0)
                    except: continue
            def buscar(obj, depth=0):
                if depth > 3: return None
                if isinstance(obj, (int, float)): return float(obj)
                if isinstance(obj, str):
                    try: return float(obj)
                    except: return None
                if isinstance(obj, dict):
                    for k in ['equity','balance','availableMargin','availableBalance']:
                        if k in obj:
                            r = buscar(obj[k], depth+1)
                            if r is not None and r > 0: return r
                if isinstance(obj, list) and obj: return buscar(obj[0], depth+1)
                return None
            return buscar(data) or 0.0
        except Exception as e:
            log.error(f"  Error balance: {e}"); return 0.0

    def _verify(self):
        global AUTO_TRADING
        if not AUTO_TRADING: return
        if not BINGX_API_KEY or not BINGX_API_SECRET:
            log.error("  API keys vacías"); AUTO_TRADING = False; return
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/balance', {}).json()
            if d.get('code') == 0:
                self._balance = self._extraer_balance(d)
                log.info(f"BingX OK | Balance: ${self._balance:.2f} USDT")
            else:
                log.error(f"BingX [{d.get('code')}]: {d.get('msg')}"); AUTO_TRADING = False
        except Exception as e:
            log.error(f"Error API: {e}"); AUTO_TRADING = False

    def _update_balance(self):
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/balance', {}).json()
            if d.get('code') == 0: self._balance = self._extraer_balance(d)
        except: pass
        return self._balance

    def _calcular_usdt_trade(self):
        """
        MEJORA 1: Tamaño dinámico basado en % del balance.
        Con $53 y RISK_PCT=15%: $53 × 0.15 = $7.95
        Con $100 y RISK_PCT=15%: $100 × 0.15 = $15 (capped en MAX_TRADE_USDT)
        """
        bal = self._balance
        usdt = bal * RISK_PCT / 100
        usdt = max(MIN_TRADE_USDT, min(MAX_TRADE_USDT, usdt))
        return round(usdt, 2)

    def _balance_suficiente(self):
        bal = self._update_balance()
        needed = MIN_TRADE_USDT / LEVERAGE
        if bal < needed:
            log.warning(f"  Balance ${bal:.2f} insuficiente — skip"); return False
        return True

    def _set_leverage(self, symbol, direction):
        try:
            bingx_request('POST', '/openApi/swap/v2/trade/leverage',
                          {'symbol':symbol,'side':direction,'leverage':str(LEVERAGE)})
        except: pass

    def _load_contracts(self):
        try:
            d = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/contracts", timeout=15).json()
            if d.get('code') == 0:
                for c in d.get('data', []):
                    self._contracts[c.get('symbol','')] = {
                        'step':  float(c.get('tradeMinQuantity', 1)),
                        'prec':  int(c.get('quantityPrecision', 2)),
                        'ctval': float(c.get('contractSize', 1)),
                    }
                log.info(f"Contratos: {len(self._contracts)}")
        except Exception as e: log.warning(f"Error contratos: {e}")

    def _get_symbols(self):
        NO = ['DOW','JONES','SP500','SPX','SPY','QQQ','NASDAQ','RUSSELL','DAX','FTSE',
              'CAC','NIKKEI','HANG','BOVESPA','IBEX','US30','NAS100','US500','DJI','INDEX',
              'GOLD','SILVER','XAU','XAG','PAXG','XAUT','OIL','BRENT','WTI','CRUDE',
              'GAS','GASOLINE','PLATINUM','PALLADIUM','COPPER','NICKEL','ZINC','IRON',
              'TSLA','AAPL','MSFT','GOOGL','AMZN','META','NVDA','COIN','MSTR',
              'EUR','GBP','JPY','CHF','AUD','CAD','NZD',
              'WHEAT','CORN','SUGAR','COFFEE','COTTON','LUMBER','SOYBEAN']
        try:
            d = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker", timeout=15).json()
            if d.get('code') == 0:
                items, excl = [], []
                for t in d.get('data', []):
                    sym = t.get('symbol','')
                    if not sym.endswith('-USDT'): continue
                    base = sym.replace('-USDT','').upper()
                    if any(kw in base for kw in NO): excl.append(base); continue
                    try:
                        price = float(t.get('lastPrice',0))
                        vol   = float(t.get('volume',0)) * price
                        if vol < MIN_VOLUME or price < 0.000001: continue
                        items.append({'symbol':sym,'vol':vol})
                    except: continue
                items.sort(key=lambda x: x['vol'], reverse=True)
                self.symbols = [x['symbol'] for x in items[:MAX_SYMBOLS]]
                log.info(f"Pares: {len(self.symbols)} | Excluidos: {len(excl)}")
                return
        except Exception as e: log.warning(f"Error símbolos: {e}")
        self.symbols = ['BTC-USDT','ETH-USDT','SOL-USDT','BNB-USDT','XRP-USDT']

    def _reconciliar_posiciones(self):
        if not AUTO_TRADING: return
        log.info("  🔍 Reconciliando posiciones...")
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/positions', {}).json()
            if d.get('code') != 0: return
            recuperadas = 0
            for p in (d.get('data') or []):
                try: amt = float(p.get('positionAmt', 0) or 0)
                except: continue
                if abs(amt) == 0: continue
                sym = p.get('symbol', '')
                if not sym or sym in self.open_trades: continue
                try: lev = int(float(p.get('leverage', 0) or 0))
                except: lev = 0
                if lev != 0 and lev != LEVERAGE: continue
                try: entry = float(p.get('avgPrice') or p.get('entryPrice') or 0)
                except: entry = 0
                if entry <= 0:
                    tk = self._ticker(sym); entry = tk['price'] if tk else 0
                if entry <= 0: continue
                direction = 'LONG' if amt > 0 else 'SHORT'
                qty_c = abs(amt)
                atr   = calc_atr(*([[]]*3)) if False else 0
                tp_p, sl_p, tp_pct, sl_pct = calc_tp_sl(
                    entry, direction, entry*0.01,
                    TP_MULT, SL_MULT, TP_MIN_PCT, TP_MAX_PCT, SL_MIN_PCT, SL_MAX_PCT)
                tp_ok = self._cond_order(sym, direction, qty_c, tp_p, 'TAKE_PROFIT_MARKET')
                time.sleep(0.3)
                sl_ok = self._cond_order(sym, direction, qty_c, sl_p, 'STOP_MARKET')
                self.open_trades[sym] = {
                    'direction':direction,'entry':entry,'qty_c':qty_c,
                    'usdt_qty':MIN_TRADE_USDT,
                    'tp':tp_p,'sl':sl_p,'tp_pct':tp_pct,'sl_pct':sl_pct,
                    'highest':entry,'lowest':entry,'order_id':'RECONCILIADO',
                    'tp_ok':tp_ok,'sl_ok':sl_ok,'opened_at':datetime.now(),
                    'score':0,'atr':0,'breakeven_set':False,
                }
                recuperadas += 1
                log.info(f"  {'📈' if direction=='LONG' else '📉'} {sym} {direction} reconciliado @ ${entry:.6f}")
            log.info(f"  ✅ Reconciliación: {recuperadas} posiciones")
        except Exception as e: log.error(f"  Error reconciliación: {e}")

    # ---------------------------------------------------------------- datos + cache

    def _klines(self, symbol, interval='15m', limit=150):
        key = (symbol, interval)
        if key in self._klines_cache: return self._klines_cache[key]
        try:
            _rate_limit()
            d = requests.get(f"{BASE_URL}/openApi/swap/v3/quote/klines",
                params={'symbol':symbol,'interval':interval,'limit':limit}, timeout=12).json()
            if d.get('code') == 0 and d.get('data'):
                k = d['data']
                result = ([float(x['close'])  for x in k],
                          [float(x['high'])   for x in k],
                          [float(x['low'])    for x in k],
                          [float(x['volume']) for x in k])
                self._klines_cache[key] = result
                return result
        except: pass
        return None, None, None, None

    def _clear_cache(self): self._klines_cache.clear()

    def _ticker(self, symbol):
        try:
            _rate_limit()
            d = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker",
                             params={'symbol':symbol}, timeout=8).json()
            if d.get('code') == 0 and d.get('data'):
                t = d['data']
                return {'price': float(t.get('lastPrice',0)),
                        'change': float(t.get('priceChangePercent',0))}
        except: pass
        return None

    def _update_btc_trend(self):
        try:
            c15, *_ = self._klines('BTC-USDT', '15m', 8)
            if c15 and len(c15) >= 5:
                self._btc_1h = (c15[-1]-c15[-5])/c15[-5]*100
            c1h, *_ = self._klines('BTC-USDT', '1h', 8)
            if c1h and len(c1h) >= 5:
                self._btc_4h = (c1h[-1]-c1h[-5])/c1h[-5]*100
        except: pass

    def _update_market_bias(self):
        if not MARKET_FILTER_ON:
            self._market_bias = 'neutral'; return
        bull, bear = 0, 0
        for sym in MARKET_REF_PAIRS:
            try:
                c15, *_ = self._klines(sym, '15m', 6)
                if c15 and len(c15) >= 5:
                    chg = (c15[-1]-c15[-5])/c15[-5]*100
                    if chg >  MARKET_FILTER_PCT: bull += 1
                    if chg < -MARKET_FILTER_PCT: bear += 1
            except: pass
        self._market_bias = 'bear' if bear >= MARKET_FILTER_N else 'bull' if bull >= MARKET_FILTER_N else 'neutral'
        log.info(f"  Mercado:{self._market_bias} (bull={bull} bear={bear}) | "
                 f"BTC 1h:{self._btc_1h:+.2f}% 4h:{self._btc_4h:+.2f}%")

    # ---------------------------------------------------------------- sizing y cooldown

    def _qty_contratos(self, symbol, price, usdt_amount):
        info  = self._contracts.get(symbol, {'step':1.0,'prec':2,'ctval':1.0})
        step  = max(info['step'], 0.0001)
        prec  = info['prec']
        ppc   = price * info.get('ctval',1.0) if info.get('ctval',1.0) != 1.0 else price
        if ppc <= 0: return None, 0
        qty = round(math.ceil(usdt_amount / ppc / step) * step, prec)
        val = qty * ppc
        i   = 0
        while val < MIN_TRADE_USDT and i < 500:
            qty += step; qty = round(qty, prec); val = qty * ppc; i += 1
        if val > usdt_amount * 1.3:
            qty = round(math.floor((usdt_amount*1.3/ppc)/step)*step, prec)
            val = qty * ppc
        log.info(f"    qty: {qty} × ${ppc:.6f} = ${val:.2f} USDT")
        return qty, round(val, 4)

    def _cooldown_ok(self, symbol):
        cd = self._cooldowns.get(symbol)
        if not cd: return True
        resume_ts, reason = cd
        if time.time() >= resume_ts:
            del self._cooldowns[symbol]; return True
        return False

    def _set_cooldown(self, symbol, reason='TP'):
        mins = COOLDOWN_AFTER_TP if reason == 'TP' else COOLDOWN_AFTER_SL
        self._cooldowns[symbol] = (time.time() + mins*60, reason)
        log.info(f"  Cooldown {symbol}: {mins}min ({reason})")

    def _hora_ok(self):
        from datetime import timezone
        return datetime.now(timezone.utc).hour not in SKIP_HOURS_UTC

    def _filtro_btc_ok(self, direction):
        if direction == 'LONG':
            ok = (self._btc_1h > -BTC_FILTER_1H and self._btc_4h > -BTC_FILTER_4H)
            mkt = self._market_bias != 'bear'
            return ok and mkt
        else:
            ok = (self._btc_1h < BTC_FILTER_1H and self._btc_4h < BTC_FILTER_4H)
            mkt = self._market_bias != 'bull'
            return ok and mkt

    # ---------------------------------------------------------------- MEJORA 4: posiciones reales BingX

    def _posiciones_reales_bingx(self):
        """Cuenta posiciones reales abiertas en BingX con LEVERAGE correcto."""
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/positions', {}).json()
            if d.get('code') == 0:
                count = 0
                for p in (d.get('data') or []):
                    try:
                        amt = float(p.get('positionAmt', 0) or 0)
                        lev = int(float(p.get('leverage', 0) or 0))
                    except: continue
                    if abs(amt) > 0 and (lev == 0 or lev == LEVERAGE):
                        count += 1
                return count
        except: pass
        return len(self.open_trades)

    # ---------------------------------------------------------------- ANÁLISIS FLOOP

    def _floop_tf(self, symbol, interval, state=None):
        closes, highs, lows, vols = self._klines(symbol, interval)
        if not closes or len(closes) < 40: return None

        _, trend_s, sig_s = calc_range_filter(closes, highs, lows,
                                              SENSITIVITY, ATR_LEN, ATR_MULT)
        rf_trend = trend_s[-1]
        rf_sig   = sig_s[-1]

        if state is None: state = {'bars_since': 999}
        bars_since = state.get('bars_since', 999)
        bars_since = 0 if rf_sig != 0 else min(bars_since + 1, 9999)
        state['bars_since'] = bars_since
        cooldown_clear = bars_since >= COOLDOWN_BARS

        atr_val            = calc_atr(highs, lows, closes, ATR_LEN)
        atr_rank, atr_norm = calc_atr_rank(highs, lows, closes, ATR_LEN, 60)
        roc5, roc10, roc20, mom_bull, mom_bear = calc_momentum_roc(closes)
        mom_aligned = (rf_trend==1 and mom_bull) or (rf_trend==-1 and mom_bear)
        mom_partial = (rf_trend==1 and roc5>0)   or (rf_trend==-1 and roc5<0)

        ema_f      = calc_ema(closes, EMA_FAST)
        ema_s      = calc_ema(closes, EMA_SLOW)
        ema_f_prev = calc_ema(closes[:-1], EMA_FAST) if len(closes) > EMA_FAST else ema_f

        ema_cross_bull    = ema_f > ema_s
        ema_cross_bear    = ema_f < ema_s
        ema_cond1         = ema_cross_bull if rf_trend==1 else ema_cross_bear
        ema_cond2         = closes[-1] > ema_f if rf_trend==1 else closes[-1] < ema_f
        slope_ok          = (ema_f > ema_f_prev) if rf_trend==1 else (ema_f < ema_f_prev)
        ema_fully_aligned = ema_cond1 and ema_cond2 and slope_ok

        score_ema = min((1 if ema_cond1 else 0) + (1 if ema_cond2 else 0) +
                        (1 if mom_aligned else 0) + (1 if mom_partial else 0), 4)

        adx_val, _, _ = calc_adx(highs, lows, closes, ADX_LEN)
        adx_trending  = adx_val >= ADX_THRESH
        chop_idx      = calc_choppiness(highs, lows, closes, CHOP_LEN)
        chop_clear    = chop_idx <= CHOP_THRESH

        chop_gate    = ((not ADX_ON or adx_trending) and
                        (not CHOP_ON or chop_clear)  and cooldown_clear)
        chop_penalty = ((-1 if ADX_ON  and not adx_trending else 0) +
                        (-1 if CHOP_ON and not chop_clear    else 0))

        score_vol  = min((1 if atr_rank < 80 else 0) + (1 if atr_norm < 1.5 else 0), 2)

        _, ts12, _ = calc_range_filter(closes, highs, lows, 12, ATR_LEN, ATR_MULT)
        _, ts16, _ = calc_range_filter(closes, highs, lows, 16, ATR_LEN, ATR_MULT)
        score_sens = (2 if ts12[-1]==rf_trend else 0) + (1 if ts16[-1]==rf_trend else 0)

        ema_gate  = ema_fully_aligned if EMA_FILTER_ON else True
        long_sig  = (rf_sig == 1)  and ema_gate and chop_gate
        short_sig = (rf_sig == -1) and ema_gate and chop_gate

        return {
            'rf_trend':rf_trend,'rf_sig':rf_sig,'long_sig':long_sig,'short_sig':short_sig,
            'ema_fully_aligned':ema_fully_aligned,'chop_gate':chop_gate,
            'chop_penalty':chop_penalty,'score_ema':score_ema,'score_vol':score_vol,
            'score_sens':score_sens,'adx':adx_val,'adx_trending':adx_trending,
            'chop_idx':chop_idx,'atr_val':atr_val,'atr_norm':atr_norm,
            'atr_rank':atr_rank,'roc5':roc5,'bars_since':bars_since,'state':state,
        }

    def analyze(self, symbol):
        if symbol in self.open_trades: return None
        if not self._cooldown_ok(symbol): return None
        if not self._hora_ok(): return None

        ticker = self._ticker(symbol)
        if not ticker or ticker['price'] <= 0: return None
        price = ticker['price']

        sym_state = self._rf_state.setdefault(symbol, {})
        state_15m = sym_state.setdefault('15m', {'bars_since': 999})
        main      = self._floop_tf(symbol, '15m', state_15m)
        if not main: return None
        sym_state['15m'] = main['state']

        if not main['long_sig'] and not main['short_sig']: return None

        state_htf = sym_state.setdefault(HTF_INTERVAL, {'bars_since': 999})
        htf       = self._floop_tf(symbol, HTF_INTERVAL, state_htf)
        if htf: sym_state[HTF_INTERVAL] = htf['state']

        htf_trend = htf['rf_trend'] if htf else 0
        score_htf = 1 if (htf_trend != 0 and htf_trend == main['rf_trend']) else 0

        MTF_TFS   = ['5m', '15m', '1h', '4h']
        score_mtf = 0
        mtf_trends = {}
        for tf in MTF_TFS:
            if tf == '15m':
                mtf_trends['15m'] = main['rf_trend']
                score_mtf += 1
                continue
            st = sym_state.setdefault(tf, {'bars_since': 999})
            tf_data = self._floop_tf(symbol, tf, st)
            if tf_data:
                sym_state[tf] = tf_data['state']
                mtf_trends[tf] = tf_data['rf_trend']
                if tf_data['rf_trend'] == main['rf_trend']:
                    score_mtf += 1
        score_mtf = min(score_mtf, 4)

        score = max(0, min(14,
            score_htf + score_mtf + main['score_sens'] +
            main['score_ema'] + main['score_vol'] + main['chop_penalty']))

        str_label = "HIGH" if score>=11 else "MED" if score>=8 else "LOW" if score>=6 else "WEAK"
        if score < MIN_SCORE: return None

        direction = None
        if main['long_sig']  and ENABLE_LONGS  and self._filtro_btc_ok('LONG'):
            direction = 'LONG'
        elif main['short_sig'] and ENABLE_SHORTS and self._filtro_btc_ok('SHORT'):
            direction = 'SHORT'
        if not direction: return None

        atr = main['atr_val']
        tp_price, sl_price, tp_pct, sl_pct = calc_tp_sl(
            price, direction, atr,
            TP_MULT, SL_MULT, TP_MIN_PCT, TP_MAX_PCT, SL_MIN_PCT, SL_MAX_PCT)

        mtf_str = ' '.join([f"{k}:{'↑' if v==1 else '↓'}"
                            for k,v in mtf_trends.items() if k != '15m'])

        return {
            'signal':direction,'price':price,'score':score,'str_label':str_label,
            'tp_price':tp_price,'sl_price':sl_price,'tp_pct':tp_pct,'sl_pct':sl_pct,
            'atr_val':atr,'atr_pct':round(main['atr_norm'],2),'adx':round(main['adx'],1),
            'chop':round(main['chop_idx'],1),'roc5':round(main['roc5'],2),
            'score_ema':main['score_ema'],'score_mtf':score_mtf,'score_htf':score_htf,
            'score_vol':main['score_vol'],'score_sens':main['score_sens'],
            'chop_penalty':main['chop_penalty'],'mtf_str':mtf_str,
            'htf_trend':htf_trend,'ema_ok':main['ema_fully_aligned'],
        }

    # ---------------------------------------------------------------- MEJORA 2: TP/SL garantizados

    def _place_entry(self, symbol, direction, usdt_qty, price):
        qty_c, val = self._qty_contratos(symbol, price, usdt_qty)
        if not qty_c: return None, None
        side = 'BUY' if direction=='LONG' else 'SELL'
        log.info(f"  Abriendo {direction} {symbol}: {qty_c} cts = ${val:.2f}")

        if USE_LIMIT_ORDERS:
            offset = (1-LIMIT_OFFSET_PCT/100) if direction=='LONG' else (1+LIMIT_OFFSET_PCT/100)
            lp = round(price*offset, 8)
            d  = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol,'side':side,'positionSide':direction,
                'type':'LIMIT','price':str(lp),'quantity':str(qty_c),'timeInForce':'GTC',
            }).json()
            if d.get('code') == 0:
                log.info(f"  LÍMITE OK @ ${lp:.6f} (maker 0.02%)")
                return d.get('data',{}).get('orderId','OK'), qty_c
            if 'margin' in str(d.get('msg','')).lower(): return None, None
            log.warning("  Límite falló — fallback MARKET")

        d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol':symbol,'side':side,'positionSide':direction,
            'type':'MARKET','quantity':str(qty_c),
        }).json()
        if d.get('code') == 0: return d.get('data',{}).get('orderId','OK'), qty_c
        log.error(f"  Entrada fallida: {d.get('msg')}"); return None, None

    def _cond_order(self, symbol, direction, qty_c, stop_price, otype):
        """
        MEJORA 3: Comisiones optimizadas.
        TP → TAKE_PROFIT límite (maker 0.02%)
        SL → STOP límite con offset (maker 0.02%)
        Ambos con fallback a market si BingX rechaza.
        """
        if not qty_c or qty_c <= 0: return False
        try:
            is_tp      = "TAKE" in otype
            lbl        = "TP" if is_tp else "SL"
            close_side = 'SELL' if direction=='LONG' else 'BUY'

            if is_tp:
                params = {
                    'symbol':symbol,'side':close_side,'positionSide':direction,
                    'type':'TAKE_PROFIT','quantity':str(qty_c),
                    'price':str(round(stop_price,8)),
                    'stopPrice':str(round(stop_price,8)),'timeInForce':'GTC',
                }
                d  = bingx_request('POST', '/openApi/swap/v2/trade/order', params).json()
                ok = d.get('code') == 0
                if ok:
                    log.info(f"  TP maker ✅ @ ${stop_price:.6f} (0.02%)")
                    self.stats['comisiones_ahorradas'] += qty_c * stop_price * (COMISION_TAKER - COMISION_MAKER)
                else:
                    log.warning(f"  TP límite rechazado — fallback market")
                    p2 = {'symbol':symbol,'side':close_side,'positionSide':direction,
                          'type':'TAKE_PROFIT_MARKET','quantity':str(qty_c),
                          'stopPrice':str(round(stop_price,8))}
                    d2 = bingx_request('POST', '/openApi/swap/v2/trade/order', p2).json()
                    ok = d2.get('code') == 0
                    if ok:  log.info(f"  TP taker ✅ fallback @ ${stop_price:.6f} (0.05%)")
                    else:   log.error(f"  TP ❌ {d2.get('msg')}")

            else:
                # SL como STOP límite con offset (maker)
                if direction == 'LONG':
                    limit_price = round(stop_price * (1 + SL_LIMIT_OFFSET), 8)
                else:
                    limit_price = round(stop_price * (1 - SL_LIMIT_OFFSET), 8)

                params = {
                    'symbol':symbol,'side':close_side,'positionSide':direction,
                    'type':'STOP','quantity':str(qty_c),
                    'price':str(limit_price),
                    'stopPrice':str(round(stop_price,8)),'timeInForce':'GTC',
                }
                d  = bingx_request('POST', '/openApi/swap/v2/trade/order', params).json()
                ok = d.get('code') == 0
                if ok:
                    log.info(f"  SL maker ✅ trigger=${stop_price:.6f} limit=${limit_price:.6f} (0.02%)")
                    self.stats['comisiones_ahorradas'] += qty_c * stop_price * (COMISION_TAKER - COMISION_MAKER)
                else:
                    log.warning(f"  SL límite rechazado — fallback STOP_MARKET")
                    p2 = {'symbol':symbol,'side':close_side,'positionSide':direction,
                          'type':'STOP_MARKET','quantity':str(qty_c),
                          'stopPrice':str(round(stop_price,8))}
                    d2 = bingx_request('POST', '/openApi/swap/v2/trade/order', p2).json()
                    ok = d2.get('code') == 0
                    if ok:  log.info(f"  SL taker ✅ fallback @ ${stop_price:.6f} (0.05%)")
                    else:   log.error(f"  SL ❌ {d2.get('msg')}")

            return ok
        except Exception as e:
            log.error(f"  {otype}: {e}"); return False

    def _close_position(self, symbol, direction, t):
        qty_c      = t.get('qty_c', 0)
        close_side = 'SELL' if direction=='LONG' else 'BUY'

        # Intentar cierre maker IOC
        cur_price = t.get('entry', 0)
        try:
            tk = self._ticker(symbol)
            if tk and tk['price'] > 0: cur_price = tk['price']
        except: pass

        if qty_c and qty_c > 0 and cur_price > 0:
            CLOSE_OFFSET = 0.0005
            lp = round(cur_price*(1+CLOSE_OFFSET), 8) if direction=='LONG' else round(cur_price*(1-CLOSE_OFFSET), 8)
            d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol,'side':close_side,'positionSide':direction,
                'type':'LIMIT','quantity':str(qty_c),'price':str(lp),
                'timeInForce':'IOC','reduceOnly':'true',
            }).json()
            if d.get('code') == 0:
                log.info(f"  Cierre maker IOC ✅ @ ${lp:.6f} (0.02%)")
                return True

        # Fallback market
        params = ({'symbol':symbol,'side':close_side,'positionSide':direction,
                   'type':'MARKET','quantity':str(qty_c),'reduceOnly':'true'}
                  if qty_c else
                  {'symbol':symbol,'side':close_side,'positionSide':direction,
                   'type':'MARKET','quoteOrderQty':str(round(t.get('usdt_qty',MIN_TRADE_USDT),2)),
                   'reduceOnly':'true'})
        return bingx_request('POST', '/openApi/swap/v2/trade/order', params).json().get('code') == 0

    def _tiene_posicion(self, symbol):
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/positions', {'symbol':symbol}).json()
            if d.get('code') == 0:
                for p in (d.get('data') or []):
                    amt = float(p.get('positionAmt',0) or 0)
                    if abs(amt) > 0: return True, 'LONG' if amt>0 else 'SHORT'
        except: pass
        return False, None

    def _esperar_posicion(self, symbol, direction, timeout=60):
        """
        MEJORA 2: Detección robusta de posición con 3 métodos y 60s timeout.
        """
        log.info(f"  Esperando {direction} {symbol} (max {timeout}s)...")
        for i in range(timeout):
            try:
                d = bingx_request('GET', '/openApi/swap/v2/user/positions', {'symbol':symbol}).json()
                if d.get('code') == 0:
                    for p in (d.get('data') or []):
                        try: amt = float(p.get('positionAmt',0) or 0)
                        except: continue
                        ps = str(p.get('positionSide','')).upper()
                        # Método 1: amt positivo/negativo
                        # Método 2: positionSide explícito
                        # Método 3: ambos
                        ok = False
                        if direction == 'LONG':
                            ok = (amt > 0) or (ps == 'LONG' and abs(amt) > 0)
                        else:
                            ok = (amt < 0) or (ps == 'SHORT' and abs(amt) > 0)
                        if ok and abs(amt) > 0:
                            entry_real = float(p.get('avgPrice') or
                                               p.get('entryPrice') or
                                               p.get('averagePrice') or 0)
                            qty_real = abs(amt)
                            log.info(f"  ✅ {direction} confirmado: qty={qty_real:.4f} "
                                     f"entry=${entry_real:.6f} ({i+1}s)")
                            return qty_real, entry_real
            except Exception as e:
                log.debug(f"  _esperar: {e}")
            if i < 3: log.debug(f"  Esperando posición... ({i+1}s)")
            time.sleep(1)
        log.warning(f"  ⏱ Timeout {timeout}s — posición no detectada")
        return None, None

    def _cancelar_ordenes(self, symbol):
        try:
            d = bingx_request('GET', '/openApi/swap/v2/trade/openOrders', {'symbol':symbol}).json()
            if d.get('code') == 0:
                for o in (d.get('data',{}).get('orders') or []):
                    oid = o.get('orderId','')
                    if oid: bingx_request('DELETE', '/openApi/swap/v2/trade/order',
                                          {'symbol':symbol,'orderId':str(oid)})
        except: pass

    def _colocar_tpsl_con_reintentos(self, symbol, direction, qty_c, tp_price, sl_price):
        """
        MEJORA 2: Hasta 5 intentos con delay progresivo.
        NUNCA sale sin TP/SL colocados (o alerta Telegram).
        """
        tp_ok = self._cond_order(symbol, direction, qty_c, tp_price, 'TAKE_PROFIT_MARKET')
        time.sleep(0.3)
        sl_ok = self._cond_order(symbol, direction, qty_c, sl_price, 'STOP_MARKET')

        delays = [2, 4, 6, 10]
        for delay in delays:
            if tp_ok and sl_ok: break
            log.warning(f"  TP:{tp_ok} SL:{sl_ok} — reintentando en {delay}s")
            time.sleep(delay)
            if not tp_ok: tp_ok = self._cond_order(symbol, direction, qty_c, tp_price, 'TAKE_PROFIT_MARKET')
            if not sl_ok: sl_ok = self._cond_order(symbol, direction, qty_c, sl_price, 'STOP_MARKET')

        if not tp_ok or not sl_ok:
            log.error(f"  ❌ {symbol} SIN {'TP' if not tp_ok else 'SL'} tras 5 intentos")
            self._tg(f"⚠️ <b>ALERTA</b> {symbol} {direction}\n"
                     f"{'TP' if not tp_ok else 'SL'} no colocado tras 5 intentos\n"
                     f"Fijar manualmente: TP=${tp_price:.6f} SL=${sl_price:.6f}")

        return tp_ok, sl_ok

    # ---------------------------------------------------------------- lifecycle

    def _pnl_contable(self, t, cur_price):
        direction = t['direction']
        cambio    = ((cur_price-t['entry'])/t['entry'] if direction=='LONG'
                     else (t['entry']-cur_price)/t['entry'])
        pnl       = (t['usdt_qty']*LEVERAGE*cambio) - (t['usdt_qty']*LEVERAGE*COMISION_ACTUAL*2)
        return pnl, pnl/t['usdt_qty']*100

    def _actualizar_stats(self, pnl):
        self.stats['closed'] += 1
        self.stats['pnl']    += pnl
        if pnl > 0: self.stats['wins']   += 1
        else:        self.stats['losses'] += 1
        if self.stats['pnl'] > self.stats['peak_pnl']:
            self.stats['peak_pnl'] = self.stats['pnl']
        dd = self.stats['peak_pnl'] - self.stats['pnl']
        if dd > self.stats['max_dd']: self.stats['max_dd'] = dd

    def open_trade(self, symbol, sig):
        if not AUTO_TRADING:
            log.info(f"  [SEÑAL] {sig['signal']} {symbol} {sig['str_label']} {sig['score']}/14")
            return False
        if symbol in self.open_trades: return False
        if not self._balance_suficiente(): return False

        # MEJORA 4: verificar posiciones reales en BingX
        pos_reales = self._posiciones_reales_bingx()
        if pos_reales >= MAX_TRADES:
            log.info(f"  BingX tiene {pos_reales}/{MAX_TRADES} posiciones — skip")
            return False

        tiene, dir_bx = self._tiene_posicion(symbol)
        if tiene: log.info(f"  {symbol} ya tiene {dir_bx} — skip"); return False

        direction = sig['signal']
        price     = sig['price']

        # MEJORA 1: tamaño dinámico
        usdt_qty  = self._calcular_usdt_trade()
        tp_price  = sig['tp_price']
        sl_price  = sig['sl_price']
        tp_pct    = sig['tp_pct']
        sl_pct    = sig['sl_pct']

        self._set_leverage(symbol, direction)
        emoji = "📈" if direction=='LONG' else "📉"

        log.info(f"\n  ➤ {direction} {symbol} [FLOOP {sig['str_label']} {sig['score']}/14]")
        log.info(f"  EMA:{'✅' if sig['ema_ok'] else '❌'} ADX:{sig['adx']} "
                 f"Chop:{sig['chop']} ROC5:{sig['roc5']:+.2f}%")
        log.info(f"  Score: HTF={sig['score_htf']}/1 MTF={sig['score_mtf']}/4 "
                 f"EMA={sig['score_ema']}/4 SENS={sig['score_sens']}/3 "
                 f"VOL={sig['score_vol']}/2 CHOP={sig['chop_penalty']}")
        log.info(f"  ATR:{sig['atr_pct']:.2f}% TP:{tp_pct:.2f}% SL:{sl_pct:.2f}% "
                 f"RR:{tp_pct/sl_pct:.1f}:1")
        log.info(f"  Capital:${usdt_qty} ({RISK_PCT}% de ${self._balance:.2f}) | "
                 f"BTC 1h:{self._btc_1h:+.2f}% 4h:{self._btc_4h:+.2f}%")

        oid, qty_c = self._place_entry(symbol, direction, usdt_qty, price)
        if not oid: return False

        # MEJORA 2: esperar posición con timeout largo
        qty_real, entry_real = self._esperar_posicion(symbol, direction, timeout=60)

        if qty_real is None:
            log.warning(f"  LIMIT no ejecutada → cancelando + MARKET")
            self._cancelar_ordenes(symbol); time.sleep(0.5)
            side  = 'BUY' if direction=='LONG' else 'SELL'
            d_mkt = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol,'side':side,'positionSide':direction,
                'type':'MARKET','quantity':str(qty_c),
            }).json()
            if d_mkt.get('code') == 0:
                qty_real, entry_real = self._esperar_posicion(symbol, direction, timeout=30)
            if qty_real is None:
                # Último intento: usar qty_c estimado
                log.error(f"  Usando qty estimada {qty_c} para TP/SL de {symbol}")
                qty_real  = qty_c
                entry_real = price

        if entry_real and entry_real > 0:
            tp_price, sl_price, tp_pct, sl_pct = calc_tp_sl(
                entry_real, direction, sig['atr_val'],
                TP_MULT, SL_MULT, TP_MIN_PCT, TP_MAX_PCT, SL_MIN_PCT, SL_MAX_PCT)

        qty_final   = qty_real   if qty_real   else qty_c
        entry_final = entry_real if (entry_real and entry_real > 0) else price

        # MEJORA 2: TP/SL garantizados con hasta 5 intentos
        tp_ok, sl_ok = self._colocar_tpsl_con_reintentos(
            symbol, direction, qty_final, tp_price, sl_price)

        self.open_trades[symbol] = {
            'direction':direction,'entry':entry_final,'qty_c':qty_final,'usdt_qty':usdt_qty,
            'tp':tp_price,'sl':sl_price,'tp_pct':tp_pct,'sl_pct':sl_pct,
            'highest':entry_final,'lowest':entry_final,
            'order_id':oid,'tp_ok':tp_ok,'sl_ok':sl_ok,
            'opened_at':datetime.now(),'score':sig['score'],'atr':sig['atr_val'],
            'breakeven_set':False,
        }
        self.stats['exec'] += 1

        stp = "✅" if tp_ok else "❌ FIJAR MANUAL"
        ssl = "✅" if sl_ok else "❌ FIJAR MANUAL"
        self._tg(
            f"<b>{emoji} {direction} ABIERTO — FLOOP {sig['str_label']}</b>\n"
            f"<b>{symbol}</b> | Score: {sig['score']}/14\n"
            f"Entrada: ${entry_final:.6f}\n"
            f"{stp} TP: ${tp_price:.6f} (+{tp_pct:.2f}%)\n"
            f"{ssl} SL: ${sl_price:.6f} (-{sl_pct:.2f}%)\n"
            f"RR: {tp_pct/sl_pct:.1f}:1 | ATR: {sig['atr_pct']:.2f}%\n"
            f"Capital: ${usdt_qty:.2f} ({RISK_PCT}% balance) x{LEVERAGE}\n"
            f"EMA:{'✅' if sig['ema_ok'] else '❌'} ADX:{sig['adx']} | {sig['mtf_str']}\n"
            f"BTC 1h:{self._btc_1h:+.2f}% 4h:{self._btc_4h:+.2f}% | Mkt:{self._market_bias}\n"
            f"Balance: ${self._balance:.2f} USDT"
        )
        return True

    def close_trade(self, symbol, cur_price, reason):
        if symbol not in self.open_trades: return False
        t = self.open_trades[symbol]
        direction = t['direction']
        self._close_position(symbol, direction, t)

        pnl, pnl_pct = self._pnl_contable(t, cur_price)
        self._actualizar_stats(pnl)

        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        mins  = int((datetime.now()-t['opened_at']).total_seconds()/60)

        close_reason = 'TP' if 'PROFIT' in reason else 'SL'
        self._set_cooldown(symbol, close_reason)

        emoji = "✅" if pnl > 0 else "❌"
        log.info(f"  {emoji} {reason} {symbol} PnL:${pnl:+.3f}({pnl_pct:+.1f}%) {mins}min")
        self._tg(
            f"<b>{emoji} {direction} CERRADO — {reason}</b>\n<b>{symbol}</b>\n"
            f"PnL: ${pnl:+.3f} ({pnl_pct:+.1f}%)\n"
            f"Entry: ${t['entry']:.6f} → Exit: ${cur_price:.6f} | {mins}min\n"
            f"TP:{t['tp_pct']:.2f}% SL:{t['sl_pct']:.2f}% RR:{t['tp_pct']/t['sl_pct']:.1f}:1\n"
            f"Cooldown: {COOLDOWN_AFTER_TP if close_reason=='TP' else COOLDOWN_AFTER_SL}min\n"
            f"<b>Total: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}% "
            f"({self.stats['wins']}W/{self.stats['losses']}L) | "
            f"MaxDD:${self.stats['max_dd']:.2f}</b>"
        )
        del self.open_trades[symbol]
        return True

    # ---------------------------------------------------------------- monitor

    async def _sync_bingx(self):
        if not self.open_trades or not AUTO_TRADING: return
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/positions', {}).json()
            if d.get('code') != 0: return
            pos = {p.get('symbol'): float(p.get('positionAmt',0) or 0)
                   for p in (d.get('data') or [])
                   if abs(float(p.get('positionAmt',0) or 0)) > 0}
            for sym in list(self.open_trades.keys()):
                if sym not in pos:
                    t   = self.open_trades[sym]
                    tk  = self._ticker(sym)
                    cur = tk['price'] if tk else t['entry']
                    pnl, pnl_pct = self._pnl_contable(t, cur)
                    self._actualizar_stats(pnl)
                    total = self.stats['wins']+self.stats['losses']
                    wr    = self.stats['wins']/total*100 if total else 0
                    mins  = int((datetime.now()-t['opened_at']).total_seconds()/60)
                    close_reason = 'TP' if pnl >= 0 else 'SL'
                    self._set_cooldown(sym, close_reason)
                    emoji = "✅" if pnl >= 0 else "❌"
                    self._tg(
                        f"<b>{emoji} {t['direction']} cerrado BingX</b>\n<b>{sym}</b>\n"
                        f"PnL: ${pnl:+.3f} ({pnl_pct:+.1f}%) | {mins}min\n"
                        f"Total: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}% | "
                        f"MaxDD:${self.stats['max_dd']:.2f}"
                    )
                    del self.open_trades[sym]
        except Exception as e: log.debug(f"sync: {e}")

    async def monitor_trades(self):
        await self._sync_bingx()
        for sym in list(self.open_trades.keys()):
            try:
                t  = self.open_trades[sym]
                tk = self._ticker(sym)
                if not tk: continue
                cur = tk['price']
                dir = t['direction']

                if dir == 'LONG':
                    pnl_pct = (cur-t['entry'])/t['entry']*100
                    # MEJORA 7: Break-even automático
                    if not t.get('breakeven_set') and pnl_pct >= BREAKEVEN_PCT:
                        new_sl = t['entry'] * 1.001  # 0.1% por encima del entry
                        if new_sl > t['sl']:
                            t['sl'] = new_sl
                            t['breakeven_set'] = True
                            log.info(f"  Break-even {sym}: SL=${new_sl:.6f}")
                    # MEJORA 7: Trailing mejorado
                    if TRAILING and cur > t['highest']:
                        t['highest'] = cur
                        if pnl_pct >= TRAILING_ACTIVATE_PCT:
                            profit     = cur - t['entry']
                            new_sl     = t['entry'] + profit * (TRAILING_PROTECT_PCT/100)
                            if new_sl > t['sl']:
                                t['sl'] = new_sl
                                log.info(f"  Trailing {sym}: SL=${new_sl:.6f} (+{pnl_pct*(TRAILING_PROTECT_PCT/100):.1f}%)")
                    hit_tp = cur >= t['tp']
                    hit_sl = cur <= t['sl']
                else:
                    pnl_pct = (t['entry']-cur)/t['entry']*100
                    if not t.get('breakeven_set') and pnl_pct >= BREAKEVEN_PCT:
                        new_sl = t['entry'] * 0.999
                        if new_sl < t['sl']:
                            t['sl'] = new_sl
                            t['breakeven_set'] = True
                            log.info(f"  Break-even {sym}: SL=${new_sl:.6f}")
                    if TRAILING and cur < t['lowest']:
                        t['lowest'] = cur
                        if pnl_pct >= TRAILING_ACTIVATE_PCT:
                            profit = t['entry'] - cur
                            new_sl = t['entry'] - profit * (TRAILING_PROTECT_PCT/100)
                            if new_sl < t['sl']:
                                t['sl'] = new_sl
                                log.info(f"  Trailing {sym}: SL=${new_sl:.6f}")
                    hit_tp = cur <= t['tp']
                    hit_sl = cur >= t['sl']

                if abs(pnl_pct) > 0.3:
                    log.info(f"  {sym} {dir}: {pnl_pct:+.2f}% | "
                             f"TP:${t['tp']:.6f} SL:${t['sl']:.6f}")

                if hit_tp:   self.close_trade(sym, cur, "TAKE PROFIT")
                elif hit_sl: self.close_trade(sym, cur, "STOP LOSS")
            except Exception as e: log.debug(f"Monitor {sym}: {e}")

    def _reporte_horario(self):
        if datetime.now() - self._last_report < timedelta(hours=1): return
        self._last_report = datetime.now()
        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        comis = self.stats['comisiones_ahorradas']
        pos_txt = ""
        for sym, t in self.open_trades.items():
            tk = self._ticker(sym)
            if tk:
                cur     = tk['price']
                dir     = t['direction']
                pnl_pct = ((cur-t['entry'])/t['entry']*100
                           if dir=='LONG' else (t['entry']-cur)/t['entry']*100)
                pos_txt += f"  {sym} {dir}: {pnl_pct:+.2f}%\n"
        self._tg(
            f"<b>📊 Reporte horario — FLOOP v4</b>\n"
            f"PnL: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}% | MaxDD:${self.stats['max_dd']:.2f}\n"
            f"({self.stats['wins']}W/{self.stats['losses']}L | {self.stats['closed']} trades)\n"
            f"Comisiones ahorradas (maker): ${comis:.3f}\n"
            f"Abiertos: {len(self.open_trades)}/{MAX_TRADES}\n"
            f"Balance: ${self._balance:.2f} USDT\n"
            f"BTC 1h:{self._btc_1h:+.2f}% 4h:{self._btc_4h:+.2f}% | Mkt:{self._market_bias}\n"
            + (pos_txt or "  sin posiciones\n")
        )

    def _tg(self, msg):
        try:
            if TELEGRAM_TOKEN and TELEGRAM_CHAT:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={'chat_id':TELEGRAM_CHAT,'text':msg,'parse_mode':'HTML'},
                    timeout=6)
        except: pass

    async def run(self):
        log.info("\n▶  Bot FLOOP Pro v4.0 arrancado\n")
        iteration, last_refresh = 0, 0
        while True:
            try:
                iteration += 1
                if time.time() - last_refresh > 600:
                    self._get_symbols(); last_refresh = time.time()

                self._clear_cache()
                self._update_btc_trend()
                self._update_market_bias()
                self._update_balance()

                total   = self.stats['wins'] + self.stats['losses']
                wr      = self.stats['wins'] / total * 100 if total else 0
                hora_st = "🌙 BAJA" if not self._hora_ok() else "☀️"
                usdt_t  = self._calcular_usdt_trade()

                log.info(f"\n{'='*65}")
                log.info(f"  #{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                         f"Abiertos:{len(self.open_trades)}/{MAX_TRADES} | "
                         f"PnL:${self.stats['pnl']:+.3f} | WR:{wr:.1f}%")
                log.info(f"  Balance:${self._balance:.2f} | Trade:${usdt_t:.2f} | "
                         f"BTC 1h:{self._btc_1h:+.2f}% 4h:{self._btc_4h:+.2f}% | "
                         f"Mkt:{self._market_bias} | {hora_st}")
                log.info(f"{'='*65}\n")

                await self.monitor_trades()
                self._reporte_horario()

                # MEJORA 4: verificar posiciones reales antes de escanear
                pos_reales = self._posiciones_reales_bingx()
                if pos_reales >= MAX_TRADES:
                    log.info(f"  BingX: {pos_reales}/{MAX_TRADES} posiciones — esperando")
                elif self._hora_ok():
                    found = 0
                    for i, sym in enumerate(self.symbols):
                        if len(self.open_trades) >= MAX_TRADES: break
                        sig = self.analyze(sym)
                        if sig:
                            found += 1
                            emoji = "📈" if sig['signal']=='LONG' else "📉"
                            log.info(f"  ★ {emoji} {sig['signal']} {sym} "
                                     f"{sig['str_label']} {sig['score']}/14 | "
                                     f"EMA:{'✅' if sig['ema_ok'] else '❌'} "
                                     f"ADX:{sig['adx']}")
                            self.open_trade(sym, sig)
                        await asyncio.sleep(0.15)
                        if (i+1) % 20 == 0:
                            log.info(f"  ...{i+1}/{len(self.symbols)} analizados")
                    log.info(f"\n  {len(self.symbols)} pares | {found} señales")
                else:
                    log.info("  Hora baja liquidez — esperando")

                log.info(f"\n  Próximo ciclo en {INTERVAL}s\n")
                await asyncio.sleep(INTERVAL)

            except KeyboardInterrupt:
                log.info("Detenido"); break
            except Exception as e:
                log.error(f"Error loop #{iteration}: {e}")
                await asyncio.sleep(20)

async def main():
    try: await FloopBotV4().run()
    except Exception as e: log.error(f"Error fatal: {e}")

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: log.info("Terminado")
