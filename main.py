#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║         SUPERBOT BINGX v5 - INSTITUTIONAL GRADE                 ║
║                                                                  ║
║  Estrategias combinadas:                                         ║
║  1. EMA Institutional Hunter (9/21/50/120/200)                  ║
║  2. Saty Phase Oscillator (zonas acumulacion/distribucion)       ║
║  3. Saty Pivot Ribbon (conviction EMA 13/48)                     ║
║  4. VWAP Semanal como POC institucional                          ║
║  5. Compresion de Bollinger (detecta explosiones de precio)      ║
║  6. DCA Safety Orders (estilo 3Commas)                           ║
║  7. RSI + MACD + Breakout                                        ║
╚══════════════════════════════════════════════════════════════════╝
"""

import json
import time
import hmac
import hashlib
import urllib.request
import urllib.parse
import os
from datetime import datetime

# ══════════════════════════════════════════════════════════════════
#  CONFIGURACION — lee desde variables de entorno de Railway
#  Si no hay variable de entorno usa el valor por defecto
# ══════════════════════════════════════════════════════════════════
CONFIG = {
    'API_KEY':    os.environ.get('BINGX_API_KEY',    'TU_API_KEY_AQUI'),
    'API_SECRET': os.environ.get('BINGX_API_SECRET', 'TU_API_SECRET_AQUI'),
    'TELEGRAM_TOKEN':   os.environ.get('TELEGRAM_TOKEN',   ''),
    'TELEGRAM_CHAT_ID': os.environ.get('TELEGRAM_CHAT_ID', ''),

    'MODE': os.environ.get('BOT_MODE', 'paper'),   # 'paper' o 'real'

    'LEVERAGE':           5,
    'USDT_PER_TRADE':     10,
    'STOP_LOSS_PCT':      1.5,
    'TAKE_PROFIT_PCT':    3.0,

    # RSI
    'RSI_OVERSOLD':   30,
    'RSI_OVERBOUGHT': 70,
    'RSI_PERIOD':     14,

    # MACD
    'MACD_FAST':   12,
    'MACD_SLOW':   26,
    'MACD_SIGNAL': 9,

    # Bollinger
    'BB_PERIOD': 20,
    'BB_STD':    2.0,

    # EMA Institucionales (del EMA Institutional Hunter)
    'EMA_FAST':      9,
    'EMA_MID':       21,
    'EMA_TREND':     50,
    'EMA_STRUCT':    120,
    'EMA_MACRO':     200,

    # Saty Pivot Ribbon conviction
    'EMA_CONVICTION_FAST': 13,
    'EMA_CONVICTION_SLOW': 48,

    # Saty Phase Oscillator zonas
    'PHASE_ACCUMULATION':   -61.8,  # zona de acumulacion
    'PHASE_DISTRIBUTION':    61.8,  # zona de distribucion
    'PHASE_EXTREME_DOWN':   -100.0,
    'PHASE_EXTREME_UP':      100.0,

    # DCA Safety Orders
    'DCA_ENABLED':      True,
    'DCA_STEP_PCT':     1.5,
    'DCA_MAX_ORDERS':   2,
    'DCA_MULTIPLIER':   1.5,

    # Control
    'SCAN_INTERVAL_SEC':   60,
    'MAX_OPEN_POSITIONS':  10,
    'MAX_TRADE_HOURS':     6,
    'BREAKEVEN_TRIGGER':   1.0,

    # Minimo de confirmaciones para abrir trade (de 9 posibles)
    'MIN_CONFIRMACIONES':  3,

    # No operar si el mercado esta en compresion (rango lateral)
    'SKIP_COMPRESSION':    True,
}

BASE_URL = 'https://open-api.bingx.com'


# ══════════════════════════════════════════════════════════════════
#  AUTH + REQUEST
# ══════════════════════════════════════════════════════════════════
def _sign(params):
    query = '&'.join(str(k) + '=' + str(v) for k, v in sorted(params.items()))
    return hmac.new(CONFIG['API_SECRET'].encode(), query.encode(), hashlib.sha256).hexdigest()

def _request(method, path, params=None, signed=False):
    if params is None:
        params = {}
    if signed:
        params['timestamp'] = int(time.time() * 1000)
        params['signature'] = _sign(params)
    url = BASE_URL + path
    headers = {'X-BX-APIKEY': CONFIG['API_KEY'], 'Content-Type': 'application/json'}
    try:
        if method == 'GET':
            url += '?' + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers=headers)
        else:
            data = json.dumps(params).encode()
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print('  [ERR] ' + str(e)[:80])
        return None


# ══════════════════════════════════════════════════════════════════
#  PARES
# ══════════════════════════════════════════════════════════════════
def get_all_pairs():
    try:
        resp = _request('GET', '/openApi/swap/v2/quote/contracts')
        if resp and resp.get('code') == 0:
            pares = [c['symbol'] for c in resp['data'] if 'USDT' in c['symbol']]
            print('  Pares cargados: ' + str(len(pares)))
            return pares
    except Exception as e:
        print('  Error: ' + str(e))
    return ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'XRP-USDT', 'DOGE-USDT']

def get_klines(symbol, interval='1h', limit=220):
    resp = _request('GET', '/openApi/swap/v2/quote/klines', {
        'symbol': symbol, 'interval': interval, 'limit': limit
    })
    if not resp or resp.get('code') != 0:
        return None
    return resp.get('data', [])

def get_price(symbol):
    resp = _request('GET', '/openApi/swap/v2/quote/price', {'symbol': symbol})
    if resp and resp.get('code') == 0:
        return float(resp['data']['price'])
    return None


# ══════════════════════════════════════════════════════════════════
#  INDICADORES TECNICOS
# ══════════════════════════════════════════════════════════════════
def calcular_ema(closes, period):
    if len(closes) < period:
        return None
    k   = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for p in closes[period:]:
        ema = p * k + ema * (1 - k)
    return ema

def calcular_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return round(100 - (100 / (1 + ag / al)), 2)

def calcular_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i-1]),
                 abs(lows[i]  - closes[i-1]))
        trs.append(tr)
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr

def calcular_bollinger(closes, period=20, std_mult=2.0):
    if len(closes) < period:
        return None, None, None
    sl     = closes[-period:]
    middle = sum(sl) / period
    std    = (sum((x - middle)**2 for x in sl) / period) ** 0.5
    return round(middle + std_mult * std, 8), round(middle, 8), round(middle - std_mult * std, 8)

def calcular_vwap(closes, volumes, period=20):
    if len(closes) < period:
        return None
    pv  = sum(closes[-period+i] * volumes[-period+i] for i in range(period))
    vol = sum(volumes[-period:])
    return round(pv / vol, 8) if vol > 0 else None

def calcular_macd(closes):
    if len(closes) < CONFIG['MACD_SLOW'] + CONFIG['MACD_SIGNAL']:
        return None, None, None
    ema_fast = calcular_ema(closes, CONFIG['MACD_FAST'])
    ema_slow = calcular_ema(closes, CONFIG['MACD_SLOW'])
    if not ema_fast or not ema_slow:
        return None, None, None
    macd_line = ema_fast - ema_slow
    # Serie MACD para calcular signal
    macd_series = []
    for i in range(CONFIG['MACD_SLOW'], len(closes)):
        ef = calcular_ema(closes[:i+1], CONFIG['MACD_FAST'])
        es = calcular_ema(closes[:i+1], CONFIG['MACD_SLOW'])
        if ef and es:
            macd_series.append(ef - es)
    signal = calcular_ema(macd_series, CONFIG['MACD_SIGNAL'])
    hist   = macd_line - signal if signal else None
    return macd_line, signal, hist

def calcular_stdev(closes, period=21):
    if len(closes) < period:
        return None
    sl   = closes[-period:]
    mean = sum(sl) / period
    return (sum((x - mean)**2 for x in sl) / period) ** 0.5


# ══════════════════════════════════════════════════════════════════
#  SATY PHASE OSCILLATOR
#  Formula: ((precio - EMA21) / (3 * ATR)) * 100
#  Zonas: >61.8 distribucion, <-61.8 acumulacion, >100 extremo
# ══════════════════════════════════════════════════════════════════
def calcular_phase_oscillator(closes, highs, lows, period_ema=21, period_atr=14):
    pivot = calcular_ema(closes, period_ema)
    atr   = calcular_atr(highs, lows, closes, period_atr)
    if not pivot or not atr or atr == 0:
        return None
    raw = ((closes[-1] - pivot) / (3.0 * atr)) * 100
    return round(raw, 2)

def detectar_compresion(closes, highs, lows, period=21, atr_period=14):
    """
    Saty compression: BB estrecho vs ATR
    Si BB < ATR*2 = mercado comprimido, explosion inminente
    """
    bb_up, bb_mid, bb_low = calcular_bollinger(closes, period, 2.0)
    atr = calcular_atr(highs, lows, closes, atr_period)
    if not bb_up or not atr or not bb_mid:
        return False
    above_pivot = closes[-1] >= bb_mid
    if above_pivot:
        compression = (bb_up - (bb_mid + 2.0 * atr))
    else:
        compression = ((bb_mid - 2.0 * atr) - bb_low)
    return compression <= 0


# ══════════════════════════════════════════════════════════════════
#  EMA INSTITUTIONAL HUNTER
#  Condicion LONG: EMA50>EMA120>EMA200 + precio>EMA120 + pullback EMA21/50 + cruce EMA9>EMA21
#  Condicion SHORT: EMA50<EMA120<EMA200 + precio<EMA120 + pullback + cruce EMA9<EMA21
# ══════════════════════════════════════════════════════════════════
def evaluar_institutional_hunter(closes, highs, lows):
    if len(closes) < 210:
        return None, []
    ema9   = calcular_ema(closes, 9)
    ema21  = calcular_ema(closes, 21)
    ema50  = calcular_ema(closes, 50)
    ema120 = calcular_ema(closes, 120)
    ema200 = calcular_ema(closes, 200)

    if not all([ema9, ema21, ema50, ema120, ema200]):
        return None, []

    precio = closes[-1]
    low    = lows[-1]
    high   = highs[-1]

    # Tendencia institucional
    bull_trend = ema50 > ema120 > ema200
    bear_trend = ema50 < ema120 < ema200

    # Precio sobre/bajo estructura
    above_struct = precio > ema120 and precio > ema200
    below_struct = precio < ema120 and precio < ema200

    # Pullback a EMA21 o EMA50
    pullback_long  = low <= ema21 or low <= ema50
    pullback_short = high >= ema21 or high >= ema50

    # Cruce micro EMA9/EMA21 (necesitamos vela anterior)
    ema9_prev  = calcular_ema(closes[:-1], 9)
    ema21_prev = calcular_ema(closes[:-1], 21)
    micro_cross_long  = ema9_prev < ema21_prev and ema9 > ema21 if ema9_prev and ema21_prev else False
    micro_cross_short = ema9_prev > ema21_prev and ema9 < ema21 if ema9_prev and ema21_prev else False

    razones = []
    if bull_trend and above_struct and pullback_long:
        razones.append('Hunter LONG')
        if micro_cross_long:
            razones.append('MicroCruce 9/21')
        return 'LONG', razones
    elif bear_trend and below_struct and pullback_short:
        razones.append('Hunter SHORT')
        if micro_cross_short:
            razones.append('MicroCruce 9/21')
        return 'SHORT', razones

    return None, []


# ══════════════════════════════════════════════════════════════════
#  SATY PIVOT RIBBON - CONVICTION
#  EMA13 cruza EMA48 = señal de conviction
# ══════════════════════════════════════════════════════════════════
def evaluar_conviction(closes):
    ema13      = calcular_ema(closes, 13)
    ema48      = calcular_ema(closes, 48)
    ema13_prev = calcular_ema(closes[:-1], 13)
    ema48_prev = calcular_ema(closes[:-1], 48)

    if not all([ema13, ema48, ema13_prev, ema48_prev]):
        return None

    bull_conviction = ema13_prev < ema48_prev and ema13 >= ema48
    bear_conviction = ema13_prev > ema48_prev and ema13 <= ema48

    if bull_conviction:
        return 'LONG'
    elif bear_conviction:
        return 'SHORT'
    return None


# ══════════════════════════════════════════════════════════════════
#  MOTOR DE SEÑAL UNIFICADO
# ══════════════════════════════════════════════════════════════════
def analizar_par(symbol):
    # Usamos 1h para tener suficientes datos para EMA200
    klines = get_klines(symbol, interval='1h', limit=220)
    if not klines or len(klines) < 210:
        return None

    try:
        if isinstance(klines[0], dict):
            closes  = [float(k.get('close',  k.get('c', 0))) for k in klines]
            highs   = [float(k.get('high',   k.get('h', 0))) for k in klines]
            lows    = [float(k.get('low',    k.get('l', 0))) for k in klines]
            volumes = [float(k.get('volume', k.get('v', 0))) for k in klines]
        else:
            closes  = [float(k[4]) for k in klines]
            highs   = [float(k[2]) for k in klines]
            lows    = [float(k[3]) for k in klines]
            volumes = [float(k[5]) for k in klines]
    except Exception:
        return None

    precio = closes[-1]

    # ── Compresion: si el mercado esta lateral no operamos ──
    en_compresion = detectar_compresion(closes, highs, lows)
    if CONFIG['SKIP_COMPRESSION'] and en_compresion:
        return None

    # ── Calcular todos los indicadores ──
    rsi             = calcular_rsi(closes, CONFIG['RSI_PERIOD'])
    macd, sig, hist = calcular_macd(closes)
    bb_up, bb_mid, bb_low = calcular_bollinger(closes, 20, 2.0)
    vwap            = calcular_vwap(closes, volumes, 20)
    phase           = calcular_phase_oscillator(closes, highs, lows)
    ema20           = calcular_ema(closes, 20)
    ema50           = calcular_ema(closes, 50)

    # ── Estrategias especificas ──
    hunter_lado, hunter_razones = evaluar_institutional_hunter(closes, highs, lows)
    conviction_lado = evaluar_conviction(closes)

    # ══ SISTEMA DE VOTOS ══
    votos_long  = 0
    votos_short = 0
    razones     = []

    # 1. EMA Institutional Hunter (vale 2 votos por ser institucional)
    if hunter_lado == 'LONG':
        votos_long  += 2
        razones += hunter_razones
    elif hunter_lado == 'SHORT':
        votos_short += 2
        razones += hunter_razones

    # 2. Saty Conviction Ribbon (EMA 13/48)
    if conviction_lado == 'LONG':
        votos_long  += 2
        razones.append('Conviction LONG')
    elif conviction_lado == 'SHORT':
        votos_short += 2
        razones.append('Conviction SHORT')

    # 3. Saty Phase Oscillator
    if phase is not None:
        if phase <= CONFIG['PHASE_ACCUMULATION']:
            votos_long  += 1
            razones.append('Fase Acumulacion (' + str(phase) + ')')
        elif phase >= CONFIG['PHASE_DISTRIBUTION']:
            votos_short += 1
            razones.append('Fase Distribucion (' + str(phase) + ')')
        if phase <= CONFIG['PHASE_EXTREME_DOWN']:
            votos_long  += 1
            razones.append('Extremo DOWN')
        elif phase >= CONFIG['PHASE_EXTREME_UP']:
            votos_short += 1
            razones.append('Extremo UP')

    # 4. RSI
    if rsi is not None:
        if rsi < CONFIG['RSI_OVERSOLD']:
            votos_long  += 1
            razones.append('RSI ' + str(rsi))
        elif rsi > CONFIG['RSI_OVERBOUGHT']:
            votos_short += 1
            razones.append('RSI ' + str(rsi))

    # 5. VWAP
    if vwap:
        if precio < vwap * 0.995:
            votos_long  += 1
            razones.append('Bajo VWAP')
        elif precio > vwap * 1.005:
            votos_short += 1
            razones.append('Sobre VWAP')

    # 6. Bollinger Bands
    if bb_low and bb_up:
        if precio <= bb_low:
            votos_long  += 1
            razones.append('BB inferior')
        elif precio >= bb_up:
            votos_short += 1
            razones.append('BB superior')

    # 7. MACD
    if hist is not None:
        if hist > 0 and macd and macd > 0:
            votos_long  += 1
            razones.append('MACD +')
        elif hist < 0 and macd and macd < 0:
            votos_short += 1
            razones.append('MACD -')

    # 8. EMA 20/50 tendencia general
    if ema20 and ema50:
        if ema20 > ema50:
            votos_long  += 1
            razones.append('EMA alcista')
        else:
            votos_short += 1
            razones.append('EMA bajista')

    # ══ DECISION ══
    lado = None
    votos_totales = 0
    if votos_long >= CONFIG['MIN_CONFIRMACIONES'] and votos_long > votos_short:
        lado = 'LONG'
        votos_totales = votos_long
    elif votos_short >= CONFIG['MIN_CONFIRMACIONES'] and votos_short > votos_long:
        lado = 'SHORT'
        votos_totales = votos_short

    if lado is None:
        return None

    # SL dinamico basado en ATR
    atr = calcular_atr(highs, lows, closes, 14)
    if atr:
        sl_dist = max(atr * 1.5, precio * CONFIG['STOP_LOSS_PCT'] / 100)
        tp_dist = sl_dist * (CONFIG['TAKE_PROFIT_PCT'] / CONFIG['STOP_LOSS_PCT'])
    else:
        sl_dist = precio * CONFIG['STOP_LOSS_PCT'] / 100
        tp_dist = precio * CONFIG['TAKE_PROFIT_PCT'] / 100

    if lado == 'LONG':
        sl = round(precio - sl_dist, 8)
        tp = round(precio + tp_dist, 8)
    else:
        sl = round(precio + sl_dist, 8)
        tp = round(precio - tp_dist, 8)

    return {
        'symbol':      symbol,
        'lado':        lado,
        'precio':      precio,
        'sl':          sl,
        'tp':          tp,
        'rsi':         rsi,
        'phase':       phase,
        'votos':       votos_totales,
        'compresion':  en_compresion,
        'razones':     ' | '.join(razones[:5]),
    }


# ══════════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════════
def telegram(titulo, mensaje):
    token = CONFIG.get('TELEGRAM_TOKEN', '')
    chat  = CONFIG.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat:
        return
    try:
        texto = '<b>' + titulo + '</b>\n\n' + mensaje
        url   = 'https://api.telegram.org/bot' + token + '/sendMessage'
        data  = json.dumps({'chat_id': chat, 'text': texto, 'parse_mode': 'HTML'}).encode()
        req   = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
#  PAPER TRADING ENGINE
# ══════════════════════════════════════════════════════════════════
class PaperTrading:
    def __init__(self):
        self.posiciones      = {}
        self.historial       = []
        self.capital_inicial = 1000.0
        self.capital_virtual = 1000.0

    def abrir(self, senal):
        sym = senal['symbol']
        if sym in self.posiciones:
            return False
        self.posiciones[sym] = {
            'lado':        senal['lado'],
            'entrada':     senal['precio'],
            'entrada_avg': senal['precio'],
            'sl':          senal['sl'],
            'tp':          senal['tp'],
            'usdt':        CONFIG['USDT_PER_TRADE'],
            'usdt_total':  CONFIG['USDT_PER_TRADE'],
            'leverage':    CONFIG['LEVERAGE'],
            'abierta':     datetime.now(),
            'breakeven':   False,
            'dca_orders':  0,
            'votos':       senal['votos'],
            'phase':       senal['phase'],
        }
        print('  [OPEN] ' + sym + ' ' + senal['lado'] +
              ' @ ' + str(senal['precio']) +
              ' | Votos:' + str(senal['votos']) +
              ' | Phase:' + str(senal['phase']) +
              ' | ' + senal['razones'])
        return True

    def monitorear(self):
        cerradas = []
        ahora    = datetime.now()

        for sym, pos in list(self.posiciones.items()):
            precio = get_price(sym)
            if precio is None:
                continue

            # DCA Safety Orders
            if CONFIG['DCA_ENABLED'] and pos['dca_orders'] < CONFIG['DCA_MAX_ORDERS']:
                step = CONFIG['DCA_STEP_PCT'] / 100
                trigger_long  = pos['entrada_avg'] * (1 - step * (pos['dca_orders'] + 1))
                trigger_short = pos['entrada_avg'] * (1 + step * (pos['dca_orders'] + 1))
                dca_hit = (pos['lado'] == 'LONG'  and precio <= trigger_long) or \
                          (pos['lado'] == 'SHORT' and precio >= trigger_short)
                if dca_hit:
                    usdt_extra   = round(pos['usdt'] * CONFIG['DCA_MULTIPLIER'], 2)
                    total_usdt   = pos['usdt_total'] + usdt_extra
                    avg          = (pos['entrada_avg'] * pos['usdt_total'] + precio * usdt_extra) / total_usdt
                    pos['entrada_avg'] = round(avg, 8)
                    pos['usdt_total']  = total_usdt
                    pos['dca_orders'] += 1
                    sl_d = avg * CONFIG['STOP_LOSS_PCT'] / 100
                    tp_d = avg * CONFIG['TAKE_PROFIT_PCT'] / 100
                    pos['sl'] = round(avg - sl_d, 8) if pos['lado'] == 'LONG' else round(avg + sl_d, 8)
                    pos['tp'] = round(avg + tp_d, 8) if pos['lado'] == 'LONG' else round(avg - tp_d, 8)
                    print('  [DCA#' + str(pos['dca_orders']) + '] ' + sym +
                          ' @ ' + str(precio) + ' avg:' + str(pos['entrada_avg']))

            # Breakeven
            if not pos['breakeven']:
                be = CONFIG['BREAKEVEN_TRIGGER'] / 100
                if (pos['lado'] == 'LONG'  and precio >= pos['entrada_avg'] * (1 + be)) or \
                   (pos['lado'] == 'SHORT' and precio <= pos['entrada_avg'] * (1 - be)):
                    pos['sl']        = pos['entrada_avg']
                    pos['breakeven'] = True
                    print('  [BE] ' + sym + ' SL -> ' + str(pos['entrada_avg']))

            # Resultado
            horas    = (ahora - pos['abierta']).total_seconds() / 3600
            resultado = razon_cierre = None

            if horas >= CONFIG['MAX_TRADE_HOURS']:
                resultado    = 'TIMEOUT'
                razon_cierre = 'Timeout ' + str(CONFIG['MAX_TRADE_HOURS']) + 'h'
            elif pos['lado'] == 'LONG':
                if precio <= pos['sl']:
                    resultado    = 'BREAKEVEN' if pos['breakeven'] else 'LOSS'
                    razon_cierre = 'SL'
                elif precio >= pos['tp']:
                    resultado    = 'WIN'
                    razon_cierre = 'TP'
            else:
                if precio >= pos['sl']:
                    resultado    = 'BREAKEVEN' if pos['breakeven'] else 'LOSS'
                    razon_cierre = 'SL'
                elif precio <= pos['tp']:
                    resultado    = 'WIN'
                    razon_cierre = 'TP'

            if resultado:
                if resultado == 'WIN':
                    pnl_pct = CONFIG['TAKE_PROFIT_PCT']
                elif resultado == 'BREAKEVEN':
                    pnl_pct = 0.0
                elif resultado == 'TIMEOUT':
                    if pos['lado'] == 'LONG':
                        pnl_pct = round((precio - pos['entrada_avg']) / pos['entrada_avg'] * 100, 2)
                    else:
                        pnl_pct = round((pos['entrada_avg'] - precio) / pos['entrada_avg'] * 100, 2)
                else:
                    pnl_pct = -CONFIG['STOP_LOSS_PCT']

                pnl_usdt = round(pos['usdt_total'] * pos['leverage'] * pnl_pct / 100, 2)
                self.capital_virtual += pnl_usdt

                cerrada = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in pos.items()}
                cerrada.update({
                    'symbol':        sym,
                    'resultado':     resultado,
                    'razon_cierre':  razon_cierre,
                    'pnl_pct':       pnl_pct,
                    'pnl_usdt':      pnl_usdt,
                    'precio_cierre': precio,
                    'horas':         round(horas, 1),
                })
                self.historial.append(cerrada)
                cerradas.append(sym)

                tag = {'WIN': 'WIN', 'LOSS': 'LOSS', 'BREAKEVEN': 'BE', 'TIMEOUT': 'TO'}.get(resultado, resultado)
                dca_s = ' DCA:' + str(pos['dca_orders']) if pos['dca_orders'] > 0 else ''
                print('  [' + tag + '] ' + sym + ' ' + pos['lado'] +
                      ' ' + razon_cierre + dca_s +
                      ' | ' + str(pnl_pct) + '% ($' + str(pnl_usdt) + ')' +
                      ' | ' + str(round(horas, 1)) + 'h' +
                      ' | Cap: $' + str(round(self.capital_virtual, 2)))

                telegram('[PAPER] ' + sym + ' -> ' + resultado,
                    'Par: <b>' + sym + '</b>\nLado: ' + pos['lado'] + '\n' +
                    'Entrada: $' + str(pos['entrada_avg']) + '\nCierre: $' + str(precio) + '\n' +
                    'Razon: ' + razon_cierre + dca_s + '\nDuracion: ' + str(round(horas, 1)) + 'h\n' +
                    'PnL: ' + str(pnl_pct) + '% ($' + str(pnl_usdt) + ')\n' +
                    'Capital: $' + str(round(self.capital_virtual, 2))
                )

        for sym in cerradas:
            del self.posiciones[sym]

    def stats(self):
        total = len(self.historial)
        if total == 0:
            print('  Sin trades cerrados aun.')
            return
        wins = len([h for h in self.historial if h['resultado'] == 'WIN'])
        loss = len([h for h in self.historial if h['resultado'] == 'LOSS'])
        bes  = len([h for h in self.historial if h['resultado'] == 'BREAKEVEN'])
        tos  = len([h for h in self.historial if h['resultado'] == 'TIMEOUT'])
        wr   = round(wins / total * 100, 1)
        pnl  = round(sum(h['pnl_usdt'] for h in self.historial), 2)
        roi  = round((self.capital_virtual - self.capital_inicial) / self.capital_inicial * 100, 2)
        dca  = sum(h.get('dca_orders', 0) for h in self.historial)
        print('')
        print('  STATS v5 | ' + str(total) + ' trades | W:' + str(wins) +
              ' L:' + str(loss) + ' BE:' + str(bes) + ' TO:' + str(tos) +
              ' | WR:' + str(wr) + '%' +
              ' | PnL:$' + str(pnl) +
              ' | Cap:$' + str(round(self.capital_virtual, 2)) +
              ' (' + str(roi) + '% ROI)' +
              ' | DCA:' + str(dca))
        print('')
        telegram('STATS SUPERBOT v5',
            str(total) + ' trades | WR: ' + str(wr) + '%\n' +
            'W:' + str(wins) + ' L:' + str(loss) + ' BE:' + str(bes) + ' TO:' + str(tos) + '\n' +
            'DCA: ' + str(dca) + '\n' +
            'PnL: $' + str(pnl) + '\n' +
            'Capital: $' + str(round(self.capital_virtual, 2)) + ' (' + str(roi) + '% ROI)'
        )


# ══════════════════════════════════════════════════════════════════
#  BOT PRINCIPAL
# ══════════════════════════════════════════════════════════════════
class Bot:
    def __init__(self):
        self.ronda = 0
        self.paper = PaperTrading()
        self.pairs = get_all_pairs()

    def run(self):
        modo = CONFIG['MODE'].upper()
        print('')
        print('=' * 72)
        print('  SUPERBOT BINGX v5 - INSTITUTIONAL GRADE - ' + modo)
        print('=' * 72)
        print('  Pares:           ' + str(len(self.pairs)))
        print('  Apal.:           ' + str(CONFIG['LEVERAGE']) + 'x')
        print('  USDT/trade:      ' + str(CONFIG['USDT_PER_TRADE']))
        print('  SL / TP:         ' + str(CONFIG['STOP_LOSS_PCT']) + '% / ' + str(CONFIG['TAKE_PROFIT_PCT']) + '% (ATR dinamico)')
        print('  Min votos:       ' + str(CONFIG['MIN_CONFIRMACIONES']))
        print('  Max posiciones:  ' + str(CONFIG['MAX_OPEN_POSITIONS']))
        print('  Timeout:         ' + str(CONFIG['MAX_TRADE_HOURS']) + 'h')
        print('  DCA:             ON (max ' + str(CONFIG['DCA_MAX_ORDERS']) + ')')
        print('  Skip compresion: ' + str(CONFIG['SKIP_COMPRESSION']))
        print('  Capital:         $' + str(self.paper.capital_virtual))
        print('  Timeframe:       1H (institucional)')
        print('  Estrategias:     Hunter EMA + Phase Osc + Conviction + VWAP + BB + MACD + RSI')
        print('=' * 72)
        print('')

        telegram('SUPERBOT BINGX v5 (' + modo + ')',
            'Pares: ' + str(len(self.pairs)) + '\n' +
            'Estrategias: Hunter + Phase + Conviction + VWAP + BB + MACD + RSI\n' +
            'Min votos: ' + str(CONFIG['MIN_CONFIRMACIONES']) + '\n' +
            'DCA: ON | Timeout: ' + str(CONFIG['MAX_TRADE_HOURS']) + 'h\n' +
            'Skip compresion: ' + str(CONFIG['SKIP_COMPRESSION'])
        )

        try:
            while True:
                self.ronda += 1
                self._ciclo()
                time.sleep(CONFIG['SCAN_INTERVAL_SEC'])
        except KeyboardInterrupt:
            self._shutdown()

    def _ciclo(self):
        hora     = datetime.now().strftime('%H:%M:%S')
        abiertas = len(self.paper.posiciones)
        print('[RONDA ' + str(self.ronda) + '] ' + hora +
              ' | Pos: ' + str(abiertas) + '/' + str(CONFIG['MAX_OPEN_POSITIONS']) +
              ' | ' + str(len(self.pairs)) + ' pares')
        print('-' * 72)

        self.paper.monitorear()

        senales = 0
        if abiertas < CONFIG['MAX_OPEN_POSITIONS']:
            for sym in self.pairs:
                if len(self.paper.posiciones) >= CONFIG['MAX_OPEN_POSITIONS']:
                    break
                if sym in self.paper.posiciones:
                    continue
                senal = analizar_par(sym)
                if senal:
                    if self.paper.abrir(senal):
                        telegram('[PAPER] SENAL ' + senal['symbol'],
                            'Par: <b>' + senal['symbol'] + '</b>\n' +
                            'Lado: <b>' + senal['lado'] + '</b>\n' +
                            'Precio: $' + str(senal['precio']) + '\n' +
                            'SL: $' + str(senal['sl']) + '\n' +
                            'TP: $' + str(senal['tp']) + '\n' +
                            'Votos: ' + str(senal['votos']) + '\n' +
                            'Phase: ' + str(senal['phase']) + '\n' +
                            'Razones: ' + senal['razones']
                        )
                        senales += 1
                    time.sleep(0.3)

        if senales == 0 and abiertas < CONFIG['MAX_OPEN_POSITIONS']:
            print('  Sin senales (mercado sin setup claro).')

        if self.ronda % 10 == 0:
            self.paper.stats()

    def _shutdown(self):
        print('')
        print('SUPERBOT DETENIDO')
        self.paper.stats()
        total = len(self.paper.historial)
        if total > 0:
            wins = len([h for h in self.paper.historial if h['resultado'] == 'WIN'])
            wr   = round(wins / total * 100, 1)
            pnl  = round(sum(h['pnl_usdt'] for h in self.paper.historial), 2)
            roi  = round((self.paper.capital_virtual - self.paper.capital_inicial) / self.paper.capital_inicial * 100, 2)
            status = 'RENTABLE' if wr > 55 and pnl > 0 else 'MARGINAL' if pnl > 0 else 'NO RENTABLE'
            telegram('SUPERBOT DETENIDO',
                'Rondas: ' + str(self.ronda) + '\n' +
                'Trades: ' + str(total) + '\n' +
                'WR: ' + str(wr) + '%\n' +
                'PnL: $' + str(pnl) + '\n' +
                'Capital: $' + str(round(self.paper.capital_virtual, 2)) +
                ' (' + str(roi) + '% ROI)\n\n' + status
            )
        print('')

if __name__ == '__main__':
    bot = Bot()
    bot.run()
