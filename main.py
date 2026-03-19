#!/usr/bin/env python3
"""
BOT DE TRADING PROFESIONAL v3.0
Estrategia: EMA + RSI + MACD + Bollinger + Volumen + Trailing Stop
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

AUTO_TRADING  = clean('AUTO_TRADING_ENABLED',  'false', 'bool')
POSITION_SIZE = clean('MAX_POSITION_SIZE',      '100',  'float')
MIN_TRADE     = clean('MIN_TRADE_USDT',           '7',  'float')
LEVERAGE      = clean('LEVERAGE',                  '2',  'int')
TP_PCT        = clean('TAKE_PROFIT_PCT',          '2.5', 'float')
SL_PCT        = clean('STOP_LOSS_PCT',            '1.2', 'float')
MAX_TRADES    = clean('MAX_OPEN_TRADES',            '3', 'int')
INTERVAL      = clean('CHECK_INTERVAL',            '60', 'int')
MIN_VOLUME    = clean('MIN_VOLUME_24H',        '1000000','float')
MAX_SYMBOLS   = clean('MAX_SYMBOLS_TO_ANALYZE',    '80', 'int')
MIN_SCORE     = clean('MIN_SCORE',                 '65', 'float')
TRAILING      = clean('TRAILING_STOP_ENABLED',   'true', 'bool')

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
        log.info("BOT TRADING PROFESIONAL v3.0 - EMA+RSI+MACD+BB+Trailing")
        log.info("=" * 70)
        log.info(f"AUTO-TRADING:   {'ON' if AUTO_TRADING else 'OFF'}")
        log.info(f"Capital:        ${POSITION_SIZE} USDT (min ${MIN_TRADE})")
        log.info(f"Leverage:       {LEVERAGE}x")
        log.info(f"TP/SL:          {TP_PCT}% / {SL_PCT}%  (RR {TP_PCT/SL_PCT:.1f}:1)")
        log.info(f"Max trades:     {MAX_TRADES}")
        log.info(f"Score minimo:   {MIN_SCORE}/100")
        log.info(f"Volumen min:    ${MIN_VOLUME/1e6:.1f}M")
        log.info(f"Trailing stop:  {'ON' if TRAILING else 'OFF'}")
        log.info("=" * 70)

        self.symbols         = []
        self.open_trades     = {}
        self._contracts      = {}
        self.stats = {'exec':0,'closed':0,'wins':0,'losses':0,'pnl':0.0}

        self._verify()
        self._load_contracts()
        self._get_symbols()

        self._tg(
            f"<b>Bot v3.0 iniciado</b>\n"
            f"{'AUTO ON' if AUTO_TRADING else 'Solo señales'}\n"
            f"Capital: ${POSITION_SIZE} | TP:{TP_PCT}% SL:{SL_PCT}% RR:{TP_PCT/SL_PCT:.1f}\n"
            f"Score min:{MIN_SCORE} | MaxTrades:{MAX_TRADES} | Trailing:{'ON' if TRAILING else 'OFF'}"
        )

    def _verify(self):
        global AUTO_TRADING
        if not AUTO_TRADING:
            return
        if not BINGX_API_KEY or not BINGX_API_SECRET:
            log.error("Credenciales faltantes")
            AUTO_TRADING = False
            return
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
        excl = {
            'GOLD','SILVER','XAG','XAU','PAXG','XAUT','OIL','BRENT','WTI',
            'TSLA','AAPL','MSFT','GOOGL','AMZN','META','NVDA','COIN','MSTR',
            'EUR','GBP','JPY','CHF','AUD','CAD','NZD','100','1000'
        }
        try:
            r = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker", timeout=15)
            d = r.json()
            if d.get('code') == 0:
                items = []
                for t in d.get('data', []):
                    sym = t.get('symbol','')
                    if not sym.endswith('-USDT'):
                        continue
                    if any(k in sym.replace('-USDT','').upper() for k in excl):
                        continue
                    try:
                        vol = float(t.get('volume',0)) * float(t.get('lastPrice',0))
                        if vol < MIN_VOLUME or float(t.get('lastPrice',0)) < 0.0001:
                            continue
                        items.append({'symbol':sym, 'vol':vol})
                    except:
                        continue
                items.sort(key=lambda x: x['vol'], reverse=True)
                self.symbols = [x['symbol'] for x in items[:MAX_SYMBOLS]]
                log.info(f"{len(self.symbols)} monedas (vol>${MIN_VOLUME/1e6:.1f}M)")
                return
        except Exception as e:
            log.warning(f"Error simbolos: {e}")
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
        ema_gap  = abs(ema9 - ema21) / ema21 * 100

        short_trend = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0

        # --- Score LONG ---
        ls, lr = 0, []
        if ema_bull:
            p = 20 + min(10, ema_gap*5); ls += p; lr.append(f"EMA+({p:.0f})")
        if rsi_v < 30:
            ls += 25; lr.append("RSI<30(25)")
        elif rsi_v < 40:
            ls += 15; lr.append("RSI<40(15)")
        if ml > sg and hist > 0:
            ls += 20; lr.append("MACD+(20)")
        if price <= bb_l * 1.005:
            ls += 15; lr.append("BB-low(15)")
        if vspike >= 1.3:
            p = min(15, vspike*5); ls += p; lr.append(f"Vol{vspike:.1f}x({p:.0f})")
        if short_trend > 0.3:
            ls += 10; lr.append("trend+(10)")
        if change > 1.0:
            p = min(10, change*2); ls += p; lr.append(f"24h+{change:.1f}%({p:.0f})")

        # --- Score SHORT ---
        ss, sr = 0, []
        if ema_bear:
            p = 20 + min(10, ema_gap*5); ss += p; sr.append(f"EMA-({p:.0f})")
        if rsi_v > 70:
            ss += 25; sr.append("RSI>70(25)")
        elif rsi_v > 60:
            ss += 15; sr.append("RSI>60(15)")
        if ml < sg and hist < 0:
            ss += 20; sr.append("MACD-(20)")
        if price >= bb_u * 0.995:
            ss += 15; sr.append("BB-high(15)")
        if vspike >= 1.3:
            p = min(15, vspike*5); ss += p; sr.append(f"Vol{vspike:.1f}x({p:.0f})")
        if short_trend < -0.3:
            ss += 10; sr.append("trend-(10)")
        if change < -1.0:
            p = min(10, abs(change)*2); ss += p; sr.append(f"24h{change:.1f}%({p:.0f})")

        # TP dinamico basado en ATR (entre TP_PCT y TP_PCT*2)
        atr_pct = (atr_v / price * 100) if price > 0 else 0
        tp_dyn  = max(TP_PCT, min(TP_PCT * 2, atr_pct * 1.5))

        # --- Decision ---
        if ls > ss and ls >= MIN_SCORE and rsi_v <= 70:
            return {'signal':'LONG',  'price':price,'change':change,'score':ls,
                    'reasons':' | '.join(lr),'rsi':rsi_v,'vol':vspike,'tp_pct':tp_dyn,'sl_pct':SL_PCT}

        if ss > ls and ss >= MIN_SCORE and rsi_v >= 30:
            return {'signal':'SHORT', 'price':price,'change':change,'score':ss,
                    'reasons':' | '.join(sr),'rsi':rsi_v,'vol':vspike,'tp_pct':tp_dyn,'sl_pct':SL_PCT}

        return None

    # ---------------------------------------------------------------- CANTIDAD

    def _qty(self, symbol, price):
        info  = self._contracts.get(symbol, {'step':1.0,'prec':2})
        step  = info['step']
        prec  = info['prec']
        cap   = max(POSITION_SIZE, MIN_TRADE)
        raw   = cap / price
        stepped = math.ceil(raw / step) * step if step > 0 else raw
        qty   = round(stepped, prec)
        val   = qty * price
        i = 0
        while val < MIN_TRADE and step > 0 and i < 1000:
            qty += step; qty = round(qty, prec); val = qty * price; i += 1
        return qty, round(val, 4)

    # ---------------------------------------------------------------- TRADING

    def open_trade(self, symbol, sig):
        if not AUTO_TRADING:
            log.info(f"  SEÑAL {sig['signal']} {symbol} score:{sig['score']:.0f} [OFF]")
            return False

        # Bloquear si ya hay posicion local
        if symbol in self.open_trades:
            return False

        # Bloquear si BingX ya tiene posicion (proteccion hedge mode)
        try:
            r = bingx_request('GET', '/openApi/swap/v2/user/positions', {'symbol': symbol})
            d = r.json()
            if d.get('code') == 0:
                for p in (d.get('data') or []):
                    if abs(float(p.get('positionAmt', 0) or 0)) > 0:
                        lado = 'LONG' if float(p.get('positionAmt',0)) > 0 else 'SHORT'
                        log.info(f"  {symbol} ya tiene {lado} en BingX - bloqueado")
                        self.open_trades[symbol] = {
                            'direction': sig['signal'], 'entry': sig['price'],
                            'qty': 0, 'val': 0, 'tp': 0, 'sl': 0,
                            'tp_pct': sig['tp_pct'], 'sl_pct': sig['sl_pct'],
                            'highest': sig['price'], 'lowest': sig['price'],
                            'order_id': 'EXTERNO', 'tp_ok': False, 'sl_ok': False,
                            'opened_at': datetime.now(), 'score': 0,
                        }
                        return False
        except Exception as e:
            log.debug(f"  check posicion {symbol}: {e}")

        price = sig['price']
        qty, val = self._qty(symbol, price)

        if val < MIN_TRADE:
            log.warning(f"  {symbol} rechazado ${val:.2f} < ${MIN_TRADE}")
            return False

        direction = sig['signal']
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
            f"Capital: ${val:.2f} x{LEVERAGE}\n"
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
                pnl = (cur_price - t['entry']) * t['qty'] if t['direction']=='LONG' \
                      else (t['entry'] - cur_price) * t['qty']
                pnl_pct = pnl / t['val'] * 100
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
        """Eliminar del dict posiciones ya cerradas por BingX (TP/SL ejecutado)"""
        if not self.open_trades or not AUTO_TRADING:
            return
        try:
            r = bingx_request('GET', '/openApi/swap/v2/user/positions', {})
            d = r.json()
            if d.get('code') != 0:
                return
            activas = set()
            for p in (d.get('data') or []):
                if abs(float(p.get('positionAmt', 0) or 0)) > 0:
                    activas.add(p.get('symbol',''))
            for sym in list(self.open_trades.keys()):
                if sym not in activas:
                    t = self.open_trades[sym]
                    if t.get('order_id') not in ('EXTERNO', ''):
                        tk = self._ticker(sym)
                        cur = tk['price'] if tk else t['entry']
                        pnl = (cur - t['entry']) * t['qty'] if t['direction'] == 'LONG'                               else (t['entry'] - cur) * t['qty']
                        self.stats['closed'] += 1
                        self.stats['pnl']    += pnl
                        if pnl >= 0: self.stats['wins']   += 1
                        else:        self.stats['losses'] += 1
                        log.info(f"  SYNC: {sym} cerrado por BingX PnL~${pnl:+.2f}")
                    del self.open_trades[sym]
        except Exception as e:
            log.debug(f"sync_bingx: {e}")

    async def monitor_trades(self):
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
                    # Trailing: cuando ganancia >=1%, mover SL a breakeven + SL_PCT/2
                    if TRAILING and cur > t['highest']:
                        t['highest'] = cur
                        if pnl_pct >= 1.0:
                            new_sl = cur * (1 - t['sl_pct']/100)
                            if new_sl > t['sl']:
                                t['sl'] = new_sl
                                log.info(f"  Trailing SL LONG {symbol} -> ${new_sl:.4f}")
                else:
                    pnl_pct = (t['entry'] - cur) / t['entry'] * 100
                    hit_tp  = cur <= t['tp']
                    hit_sl  = cur >= t['sl']
                    if TRAILING and cur < t['lowest']:
                        t['lowest'] = cur
                        if pnl_pct >= 1.0:
                            new_sl = cur * (1 + t['sl_pct']/100)
                            if new_sl < t['sl']:
                                t['sl'] = new_sl
                                log.info(f"  Trailing SL SHORT {symbol} -> ${new_sl:.4f}")

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
        log.info("\nBot arrancado\n")
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
                log.info(f"{'='*70}\n")

                await self._sync_con_bingx()
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
