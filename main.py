#!/usr/bin/env python3
"""
BOT LONGS v5.3 — TPs Escalonados + EMA25 Trailing + Aprendizaje Profundo
FIX-SYNTAX: Eliminados backslash dentro de expresiones f-string (Python 3.11 compatible)
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re, json
from datetime import datetime, timedelta
from urllib.parse import urlencode
from collections import defaultdict

def clean(key, default, typ='str'):
    v = os.getenv(key, str(default)).strip().strip('"').strip("'")
    if typ in ('int', 'float'):
        v = v.replace(',', '.')
        m = re.match(r'^-?\d+\.?\d*', v)
        v = m.group(0) if m else str(default)
    if typ == 'int':   return int(float(v))
    if typ == 'float': return float(v)
    if typ == 'bool':  return v.lower() == 'true'
    return v

API_KEY    = os.getenv('BINGX_API_KEY',    '').strip().strip('"').strip("'")
API_SECRET = os.getenv('BINGX_API_SECRET', '').strip().strip('"').strip("'")
TG_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '')
TG_CHAT    = os.getenv('TELEGRAM_CHAT_ID',   '')

AUTO           = clean('AUTO_TRADING_ENABLED', 'true',  'bool')
POS_SIZE       = clean('MAX_POSITION_SIZE',    '10',    'float')
MIN_TRADE      = clean('MIN_TRADE_USDT',       '10',    'float')
_lev           = clean('LEVERAGE',             '2',     'int')
LEVERAGE       = min(_lev, 3)
MAX_TRADES     = clean('MAX_OPEN_TRADES',      '3',     'int')
RISK_PCT       = clean('RISK_PCT',             '1.0',   'float')
ACCOUNT_EQUITY = clean('ACCOUNT_EQUITY',       '100',   'float')

TP1_PCT   = clean('TP1_PCT',   '40',  'float')
TP2_PCT   = clean('TP2_PCT',   '35',  'float')
TP1_RATIO = clean('TP1_RATIO', '1.0', 'float')
TP2_RATIO = clean('TP2_RATIO', '2.0', 'float')

TP_MIN         = clean('TAKE_PROFIT_PCT',  '2.0',  'float')
SL_VWAP_MARGIN = clean('SL_VWAP_MARGIN',  '0.15', 'float')
ATR_TP_M       = clean('ATR_TP_MULT',     '2.5',  'float')
MIN_RR         = clean('MIN_RR',          '2.0',  'float')

MIN_VOL    = clean('MIN_VOLUME_24H',      '500000', 'float')
MAX_SYMS   = clean('MAX_SYMBOLS',         '60',     'int')
MIN_SCORE  = clean('MIN_SCORE',           '50',     'float')
BTC_BLOCK  = clean('BTC_BEAR_BLOCK_PCT',  '2.0',    'float')

VWAP_FLAT_PCT      = clean('VWAP_FLAT_PCT',      '0.15', 'float')
VWAP_BREAK_MIN_PCT = clean('VWAP_BREAK_MIN_PCT', '0.10', 'float')
VWAP_RETEST_PCT    = clean('VWAP_RETEST_PCT',    '0.30', 'float')
VWAP_CANDLES       = clean('VWAP_CANDLES',        '50',   'int')
VWAP_SLOPE_CANDLES = clean('VWAP_SLOPE_CANDLES',  '20',   'int')

CB_USDT    = clean('CIRCUIT_BREAKER_USDT', '3.0', 'float')
CB_HOURS   = clean('CB_PAUSE_HOURS',       '2',   'int')
MAX_STREAK = clean('MAX_LOSING_STREAK',    '4',   'int')
CD_TP      = clean('COOLDOWN_TP_MIN',      '5',   'int')
CD_SL      = clean('COOLDOWN_SL_MIN',      '60',  'int')
INTERVAL   = clean('CHECK_INTERVAL',       '120', 'int')
LTV_WARN   = clean('LTV_WARNING_PCT',      '80',  'float')
SKIP_HOURS = {2, 3}
BASE_URL   = "https://open-api.bingx.com"
FEE        = 0.0002
TP_MIN_FEE = round((2 * FEE * LEVERAGE + 0.003) * 100, 3)

EXCLUDE = {
    'DOW','SP500','GOLD','SILVER','XAU','OIL','BRENT','EUR','GBP','JPY',
    'TSLA','AAPL','MSFT','GOOGL','AMZN','META','NVDA','COIN','MSTR',
    'WHEAT','CORN','SUGAR','PAXG','XAUT',
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ── API ───────────────────────────────────────────────────────────────────

def api(method, endpoint, params=None, retries=3):
    params = params or {}
    for attempt in range(retries + 1):
        try:
            p   = {**{k: str(v) for k, v in params.items()},
                   'timestamp': str(int(time.time() * 1000))}
            qs  = urlencode(sorted(p.items()))
            sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
            url = f"{BASE_URL}{endpoint}?{qs}&signature={sig}"
            hdr = {'X-BX-APIKEY': API_KEY,
                   'Content-Type': 'application/x-www-form-urlencoded'}
            r   = getattr(requests, method.lower())(url, headers=hdr, timeout=15)
            return r.json()
        except Exception as e:
            if attempt < retries: time.sleep(2 ** attempt)
            else: log.error(f"API {endpoint}: {e}"); return {}

def pub(path, params=None):
    try:
        return requests.get(f"{BASE_URL}{path}", params=params or {}, timeout=10).json()
    except:
        return {}

# ── INDICADORES ───────────────────────────────────────────────────────────

def ema(prices, n):
    if not prices: return 0
    if len(prices) < n: return sum(prices) / len(prices)
    k, e = 2 / (n + 1), prices[0]
    for p in prices[1:]:
        e = p * k + e * (1 - k)
    return e

def rsi(prices, n=14):
    if len(prices) < n + 1: return 50.0
    g = [max(prices[i] - prices[i-1], 0) for i in range(1, len(prices))]
    l = [max(prices[i-1] - prices[i], 0) for i in range(1, len(prices))]
    ag, al = sum(g[-n:]) / n, sum(l[-n:]) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)

def atr_calc(h, l, c, n=14):
    if len(c) < 2: return 0
    trs = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
           for i in range(1, min(len(c), n+1))]
    return sum(trs) / len(trs) if trs else 0

def calc_vwap(closes, highs, lows, volumes, n=None):
    n = n or len(closes)
    c = closes[-n:]; h = highs[-n:]; l = lows[-n:]; v = volumes[-n:]
    typical = [(h[i] + l[i] + c[i]) / 3 for i in range(len(c))]
    tp_vol  = sum(typical[i] * v[i] for i in range(len(c)))
    vol_sum = sum(v)
    return tp_vol / vol_sum if vol_sum > 0 else c[-1]

def vwap_slope_pct(closes, highs, lows, volumes, n=20):
    if len(closes) < n * 2: return 0.0
    vwap_now  = calc_vwap(closes,      highs,      lows,      volumes,      n)
    vwap_prev = calc_vwap(closes[:-5], highs[:-5], lows[:-5], volumes[:-5], n)
    return (vwap_now - vwap_prev) / vwap_prev * 100 if vwap_prev > 0 else 0.0

# ── SETUP VWAP ────────────────────────────────────────────────────────────

def analizar_setup_vwap(closes, highs, lows, volumes, opens):
    result = {'tipo': None, 'vwap': 0, 'sl_price': 0,
              'calidad': 0, 'slope': 0, 'vol_ratio': 1, 'descripcion': ''}
    if len(closes) < VWAP_CANDLES + 15: return result

    vwap_val = calc_vwap(closes, highs, lows, volumes, VWAP_CANDLES)
    slope    = vwap_slope_pct(closes, highs, lows, volumes, VWAP_SLOPE_CANDLES)
    price    = closes[-1]
    result['vwap'] = vwap_val; result['slope'] = slope

    if abs(slope) < VWAP_FLAT_PCT:
        result['descripcion'] = 'VWAP plano — manos quietas'; return result
    if slope <= 0:
        result['descripcion'] = 'VWAP bajista'; return result

    sl_price = vwap_val * (1 - SL_VWAP_MARGIN / 100)
    result['sl_price'] = sl_price

    vol_avg   = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else volumes[-1]
    vol_ratio = volumes[-1] / vol_avg if vol_avg > 0 else 1
    result['vol_ratio'] = vol_ratio

    pct_sobre_vwap = (price - vwap_val) / vwap_val * 100
    prev_close     = closes[-2] if len(closes) >= 2 else price

    # TIPO A: Ruptura
    ruptura_reciente = (prev_close <= vwap_val * 1.001) and \
                       (price > vwap_val * (1 + VWAP_BREAK_MIN_PCT / 100))
    if ruptura_reciente and 0 < pct_sobre_vwap < 1.5:
        calidad = 55
        if slope > 0.3:      calidad += 15
        if vol_ratio >= 1.5: calidad += 15
        if slope > 0.6:      calidad += 10
        if vol_ratio >= 2.0: calidad += 5
        result.update({'tipo': 'A', 'calidad': min(calidad, 100),
                       'descripcion': 'RUPTURA'})
        return result

    # TIPO B: Retesteo
    ventana = min(12, len(closes) - 5)
    fase_ruptura = fase_retest = fase_rebote = False
    for i in range(-ventana, -1):
        c_i = closes[i]; l_i = lows[i]; o_i = opens[i] if opens else c_i
        if not fase_ruptura:
            if c_i > vwap_val * (1 + VWAP_BREAK_MIN_PCT / 100): fase_ruptura = True
            continue
        if fase_ruptura and not fase_retest:
            if abs(l_i - vwap_val) / vwap_val * 100 < VWAP_RETEST_PCT: fase_retest = True
            continue
        if fase_ruptura and fase_retest:
            if c_i > o_i and c_i > vwap_val: fase_rebote = True; break

    if fase_ruptura and fase_retest and fase_rebote and price > vwap_val:
        calidad = 70
        if slope > 0.3:          calidad += 10
        if pct_sobre_vwap < 0.5: calidad += 10
        if vol_ratio >= 1.3:     calidad += 5
        if slope > 0.6:          calidad += 5
        result.update({'tipo': 'B', 'calidad': min(calidad, 100),
                       'descripcion': 'RETESTEO'})
        return result

    result['descripcion'] = 'Sin setup'; return result


def calcular_qty_por_riesgo(precio_entrada, sl_price, capital_usdt, riesgo_pct):
    if sl_price >= precio_entrada: return 0, 0
    riesgo_usdt   = capital_usdt * (riesgo_pct / 100)
    distancia_pct = (precio_entrada - sl_price) / precio_entrada * 100
    qty_usdt      = riesgo_usdt / (distancia_pct / 100)
    return qty_usdt, distancia_pct

# ── APRENDIZAJE PROFUNDO ──────────────────────────────────────────────────

class Learning:
    def __init__(self):
        self.history       = []
        self.sym_stats     = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0,'n':0})
        self.opt_score     = MIN_SCORE
        self.blacklist     = set()
        self.streak        = 0
        self.last10        = []
        self.by_hour       = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0})
        self.by_tipo       = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0})
        self.by_btc        = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0})
        self.by_reason     = defaultdict(lambda: {'n':0,'pnl':0.0})
        self.factor_wins   = defaultdict(int)
        self.factor_losses = defaultdict(int)
        self.score_boost   = {}

    def record(self, symbol, score, pnl, win, hora_utc=None,
               tipo_entrada='?', btc_dir='flat', reason='?', factors=None):
        hora = hora_utc or datetime.utcnow().hour
        rec = {'ts': datetime.now().isoformat(), 'sym': symbol,
               'score': score, 'pnl': pnl, 'win': win,
               'hora': hora, 'tipo': tipo_entrada,
               'btc': btc_dir, 'reason': reason, 'factors': factors or []}
        self.history.append(rec)
        self.last10.append(rec)
        if len(self.last10) > 10: self.last10.pop(0)

        s = self.sym_stats[symbol]
        s['n'] += 1; s['pnl'] += pnl
        if win: s['w'] += 1; self.streak = 0
        else:   s['l'] += 1; self.streak += 1

        k = 'w' if win else 'l'
        self.by_hour[hora][k] += 1;        self.by_hour[hora]['pnl'] += pnl
        self.by_tipo[tipo_entrada][k] += 1; self.by_tipo[tipo_entrada]['pnl'] += pnl
        self.by_btc[btc_dir][k] += 1;      self.by_btc[btc_dir]['pnl'] += pnl
        self.by_reason[reason]['n'] += 1;   self.by_reason[reason]['pnl'] += pnl

        for f in (factors or []):
            if win: self.factor_wins[f]   += 1
            else:   self.factor_losses[f] += 1

        self._adjust()
        if len(self.history) % 10 == 0:
            self._reporte_aprendizaje()

    def _adjust(self):
        n = len(self.history)
        if n >= 10:
            wr = sum(1 for t in self.last10 if t['win']) / len(self.last10)
            if wr < 0.35:   self.opt_score = min(self.opt_score + 5, 85)
            elif wr < 0.45: self.opt_score = min(self.opt_score + 2, 85)
            elif wr > 0.75: self.opt_score = max(self.opt_score - 4, MIN_SCORE)
            elif wr > 0.65: self.opt_score = max(self.opt_score - 2, MIN_SCORE)

        for sym, s in self.sym_stats.items():
            tot = s['w'] + s['l']
            if tot >= 5 and s['pnl'] < -2.0 and s['w'] / tot < 0.20:
                if sym not in self.blacklist:
                    self.blacklist.add(sym)
                    log.warning(f"  [LEARN] Blacklist: {sym}")

        if n >= 20:
            all_factors = set(list(self.factor_wins.keys()) + list(self.factor_losses.keys()))
            for factor in all_factors:
                w = self.factor_wins.get(factor, 0)
                l = self.factor_losses.get(factor, 0)
                if w + l < 5: continue
                wr_f = w / (w + l)
                if wr_f < 0.30:   self.score_boost[factor] = -8
                elif wr_f > 0.70: self.score_boost[factor] = +5
                else:             self.score_boost.pop(factor, None)

    def hora_ok(self, hora_utc):
        h = self.by_hour.get(hora_utc)
        if not h: return True, "ok"
        tot = h['w'] + h['l']
        if tot < 5: return True, "ok"
        wr = h['w'] / tot
        if wr < 0.25:
            return False, "hora {0}h WR={1:.0%}".format(hora_utc, wr)
        return True, "ok"

    def bonus_tipo(self, tipo):
        h = self.by_tipo.get(tipo)
        if not h: return 0
        tot = h['w'] + h['l']
        if tot < 5: return 0
        wr = h['w'] / tot
        if wr > 0.65: return +10
        if wr < 0.35: return -10
        return 0

    def ok(self, sym, score):
        if sym in self.blacklist:     return False, "blacklist"
        if score < self.opt_score:    return False, "score bajo"
        if self.streak >= MAX_STREAK: return False, "streak"
        return True, "ok"

    def factor_score_adj(self, factors):
        return sum(self.score_boost.get(f, 0) for f in factors)

    def _reporte_aprendizaje(self):
        # FIX-SYNTAX: variables pre-calculadas, sin \n dentro de expresiones f-string
        n    = len(self.history)
        total_wins = sum(1 for t in self.history if t['win'])
        wr   = total_wins / n * 100 if n else 0
        pnl  = sum(t['pnl'] for t in self.history)

        horas_ok = [(h, d) for h, d in self.by_hour.items() if d['w']+d['l'] >= 3]
        mejor_h  = max(horas_ok, key=lambda x: x[1]['pnl'], default=(None, {'pnl': 0}))
        peor_h   = min(horas_ok, key=lambda x: x[1]['pnl'], default=(None, {'pnl': 0}))

        # FIX: construir strings en variables separadas (sin \n en expresiones {})
        no_data = "  Sin datos\n"

        tipo_lines = []
        for t_key, d in self.by_tipo.items():
            tot = d['w'] + d['l']
            if tot > 0:
                tipo_lines.append("  Tipo {0}: WR={1:.0%} | PnL:${2:.2f} ({3} trades)".format(
                    t_key, d['w']/tot, d['pnl'], tot))
        tipo_txt = "\n".join(tipo_lines) + "\n" if tipo_lines else no_data

        reason_lines = []
        for r, d in sorted(self.by_reason.items(), key=lambda x: x[1]['pnl'], reverse=True):
            reason_lines.append("  {0}: ${1:+.2f} ({2} veces)".format(r, d['pnl'], d['n']))
        reason_txt = "\n".join(reason_lines) + "\n" if reason_lines else no_data

        boost_lines = []
        for f, b in sorted(self.score_boost.items(), key=lambda x: x[1]):
            boost_lines.append("  {0}: {1:+d}pts".format(f, b))
        boost_txt = "\n".join(boost_lines) if boost_lines else ""

        mejor_h_str = "{0}h UTC (${1:+.2f})".format(mejor_h[0], mejor_h[1].get('pnl', 0))
        peor_h_str  = "{0}h UTC (${1:+.2f})".format(peor_h[0],  peor_h[1].get('pnl', 0))

        msg = (
            "<b>Aprendizaje — {0} trades</b>\n"
            "WR: {1:.0f}% | PnL: ${2:+.4f}\n"
            "Score min: {3:.0f} | Blacklist: {4}\n\n"
            "<b>Por tipo de entrada:</b>\n{5}"
            "<b>Mejor hora:</b> {6}\n"
            "<b>Peor hora:</b> {7}\n\n"
            "<b>Cierres rentables:</b>\n{8}"
        ).format(n, wr, pnl, self.opt_score, len(self.blacklist),
                 tipo_txt, mejor_h_str, peor_h_str, reason_txt)

        if boost_txt:
            msg += "<b>Ajustes de factores:</b>\n" + boost_txt

        log.info("[LEARN] Reporte #{0}".format(n // 10))
        try:
            if TG_TOKEN and TG_CHAT:
                requests.post(
                    "https://api.telegram.org/bot{0}/sendMessage".format(TG_TOKEN),
                    json={'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                    timeout=6)
        except: pass

    def save(self, fp='/tmp/bot_learn_v53.json'):
        try:
            json.dump({
                'history':       self.history[-200:],
                'sym_stats':     dict(self.sym_stats),
                'opt_score':     self.opt_score,
                'blacklist':     list(self.blacklist),
                'by_hour':       dict(self.by_hour),
                'by_tipo':       dict(self.by_tipo),
                'by_btc':        dict(self.by_btc),
                'by_reason':     dict(self.by_reason),
                'factor_wins':   dict(self.factor_wins),
                'factor_losses': dict(self.factor_losses),
                'score_boost':   self.score_boost,
            }, open(fp, 'w'), indent=2)
        except: pass

    def load(self, fp='/tmp/bot_learn_v53.json'):
        try:
            fp_use = fp
            if not os.path.exists(fp):
                fp_old = '/tmp/bot_learn.json'
                if os.path.exists(fp_old):
                    fp_use = fp_old
            if os.path.exists(fp_use):
                d = json.load(open(fp_use))
                self.history       = d.get('history', [])
                self.sym_stats     = defaultdict(
                    lambda: {'w':0,'l':0,'pnl':0.0,'n':0}, d.get('sym_stats', {}))
                self.opt_score     = d.get('opt_score', MIN_SCORE)
                self.blacklist     = set(d.get('blacklist', []))
                self.by_hour       = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0}, d.get('by_hour', {}))
                self.by_tipo       = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0}, d.get('by_tipo', {}))
                self.by_btc        = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0}, d.get('by_btc', {}))
                self.by_reason     = defaultdict(lambda: {'n':0,'pnl':0.0}, d.get('by_reason', {}))
                self.factor_wins   = defaultdict(int, d.get('factor_wins', {}))
                self.factor_losses = defaultdict(int, d.get('factor_losses', {}))
                self.score_boost   = d.get('score_boost', {})
                log.info("  [LEARN] {0} trades | score min:{1:.0f} | blacklist:{2}".format(
                    len(self.history), self.opt_score, len(self.blacklist)))
        except Exception as e:
            log.warning("  [LEARN] No se pudo cargar: {0}".format(e))

# ── BOT PRINCIPAL ─────────────────────────────────────────────────────────

class LongBot:
    _opening = False

    def __init__(self):
        log.info("=" * 72)
        log.info("  BOT LONGS v5.3 — TPs Escalonados + EMA25 + Deep Learning")
        log.info("  FIX-SYNTAX: Compatible Python 3.11+")
        log.info("  Capital: ${0} | Riesgo: {1}%/trade | {2}x".format(POS_SIZE, RISK_PCT, LEVERAGE))
        log.info("  TPs: {0:.0f}% TP1 | {1:.0f}% TP2 | {2:.0f}% runner EMA25".format(
            TP1_PCT, TP2_PCT, 100-TP1_PCT-TP2_PCT))
        log.info("=" * 72)

        self.symbols       = []
        self.trades        = {}
        self._contracts    = {}
        self._cooldowns    = {}
        self._last_report  = datetime.now() - timedelta(hours=3)
        self._btc_1h       = 0.0
        self._btc_ok       = True
        self._mode         = 'hedge'
        self._daily_pnl    = 0.0
        self._daily_date   = datetime.utcnow().date()
        self._cb_active    = False
        self._cb_until     = None
        self.learn         = Learning()
        self.learn.load()
        self.stats = {'exec':0,'closed':0,'wins':0,'losses':0,'pnl':0.0,'fees':0.0}

        if not self._connect():
            log.error("Sin conexion BingX"); sys.exit(1)

        self._detect_mode()
        self._load_contracts()
        self._refresh_symbols()
        self._recover()

        self._tg(
            "<b>Bot LONGS v5.3</b>\n"
            "TPs: {0:.0f}% TP1 | {1:.0f}% TP2 | {2:.0f}% EMA25\n"
            "Riesgo: {3}% | {4}x leverage\n"
            "Aprendizaje profundo activado\n"
            "Posiciones recuperadas: {5}".format(
                TP1_PCT, TP2_PCT, 100-TP1_PCT-TP2_PCT,
                RISK_PCT, LEVERAGE, len(self.trades))
        )

    def _connect(self):
        global AUTO, ACCOUNT_EQUITY
        if not AUTO: return True
        if not API_KEY or not API_SECRET:
            log.error("API keys no configuradas"); AUTO = False; return False
        d = api('GET', '/openApi/swap/v2/user/balance')
        if d.get('code') == 0:
            b  = d.get('data', {})
            eq = float(b.get('equity', b.get('balance', 0)) or 0)
            if eq > 0: ACCOUNT_EQUITY = eq
            log.info("BingX conectado | ${0:.2f} USDT".format(ACCOUNT_EQUITY))
            return True
        log.error("[{0}]: {1}".format(d.get('code'), d.get('msg')))
        AUTO = False; return False

    def _detect_mode(self):
        try:
            d = api('GET', '/openApi/swap/v2/user/positions', {'symbol': 'BTC-USDT'})
            for p in (d.get('data') or []):
                side = str(p.get('positionSide', '')).upper()
                if side in ('LONG', 'SHORT'):
                    self._mode = 'hedge'; log.info("  Modo: HEDGE"); return
                if side == 'BOTH':
                    self._mode = 'oneway'; log.info("  Modo: ONE-WAY"); return
        except: pass
        log.info("  Modo: HEDGE (default)")

    def _load_contracts(self):
        d = pub('/openApi/swap/v2/quote/contracts')
        if d.get('code') == 0:
            for c in d.get('data', []):
                s = c.get('symbol', '')
                if s:
                    self._contracts[s] = {
                        'step':  float(c.get('tradeMinQuantity', 1)),
                        'prec':  int(c.get('quantityPrecision', 2)),
                        'ctval': float(c.get('contractSize', 1)),
                    }
            log.info("  Contratos: {0}".format(len(self._contracts)))

    def _refresh_symbols(self):
        d = pub('/openApi/swap/v2/quote/ticker')
        if d.get('code') != 0:
            self.symbols = self.symbols or ['BTC-USDT','ETH-USDT','SOL-USDT']; return
        items = []
        for t in d.get('data', []):
            sym = t.get('symbol', '')
            if not sym.endswith('-USDT'): continue
            base = sym.replace('-USDT','').upper()
            if any(ex in base for ex in EXCLUDE): continue
            try:
                price    = float(t.get('lastPrice', 0))
                vol_usdt = float(t.get('volume', 0)) * price
                if vol_usdt >= MIN_VOL and price > 0:
                    items.append({'sym': sym, 'vol': vol_usdt})
            except: continue
        items.sort(key=lambda x: x['vol'], reverse=True)
        self.symbols = [x['sym'] for x in items[:MAX_SYMS]]
        log.info("  Simbolos: {0}".format(len(self.symbols)))

    def _get_positions(self, symbol=None):
        params = {}
        if symbol: params['symbol'] = symbol
        d = api('GET', '/openApi/swap/v2/user/positions', params)
        result = defaultdict(lambda: {'long': 0.0, 'short': 0.0})
        for p in (d.get('data') or []):
            try:
                amt  = float(p.get('positionAmt', 0) or 0)
                sym  = p.get('symbol', '')
                side = str(p.get('positionSide', '')).upper()
                if not sym or abs(amt) == 0: continue
                if side == 'LONG'  or (side == 'BOTH' and amt > 0):  result[sym]['long']  = abs(amt)
                elif side == 'SHORT' or (side == 'BOTH' and amt < 0): result[sym]['short'] = abs(amt)
            except: continue
        return result

    def _close_short(self, sym, qty):
        params = {'symbol': sym, 'side': 'BUY', 'type': 'MARKET', 'quantity': str(qty)}
        if self._mode == 'hedge': params['positionSide'] = 'SHORT'
        else: params['reduceOnly'] = 'true'
        return api('POST', '/openApi/swap/v2/trade/order', params)

    def _recover(self):
        if not AUTO: return
        all_pos = self._get_positions()
        n_rec = 0; n_short = 0
        for sym, sides in all_pos.items():
            if sides['short'] > 0:
                log.warning("  SHORT huerfano: {0} -> cerrando".format(sym))
                if self._close_short(sym, sides['short']).get('code') == 0:
                    n_short += 1
                time.sleep(0.5)
            if sides['long'] > 0 and sym not in self.trades:
                d2 = api('GET', '/openApi/swap/v2/user/positions', {'symbol': sym})
                entry = 0.0
                for p in (d2.get('data') or []):
                    s2  = str(p.get('positionSide','')).upper()
                    amt = float(p.get('positionAmt',0) or 0)
                    if (s2=='LONG' and abs(amt)>0) or (s2=='BOTH' and amt>0):
                        entry = float(p.get('avgPrice') or p.get('entryPrice') or 0); break
                if entry <= 0: continue
                qty_total  = sides['long']
                qty_tp1    = round(qty_total * TP1_PCT / 100, 6)
                qty_tp2    = round(qty_total * TP2_PCT / 100, 6)
                sl_price   = entry * (1 - 0.3/100)
                tp1_price  = entry * (1 + TP_MIN/100)
                tp2_price  = entry * (1 + TP_MIN*2/100)
                self.trades[sym] = {
                    'entry': entry, 'qty_total': qty_total, 'qty_runner': qty_total,
                    'qty_tp1': qty_tp1, 'qty_tp2': qty_tp2,
                    'tp1_hit': False, 'tp2_hit': False,
                    'tp1_price': tp1_price, 'tp2_price': tp2_price,
                    'sl': sl_price, 'sl_vwap': sl_price,
                    'tp_pct': TP_MIN, 'sl_pct': 0.3,
                    'highest': entry, 'opened': datetime.now(),
                    'score': 0, 'ema25': entry, 'vwap': entry,
                    'entrada_tipo': '?', 'usdt': POS_SIZE, 'pnl_parcial': 0.0,
                    'factors': [], 'hora_utc': datetime.utcnow().hour, 'btc_dir': 'flat',
                }
                n_rec += 1
                log.info("  Recuperado: {0} @ ${1:.6f}".format(sym, entry))
        log.info("  Recuperadas: {0} | SHORTs cerrados: {1}".format(n_rec, n_short))

    def _klines(self, symbol, interval='5m', limit=120):
        d = pub('/openApi/swap/v3/quote/klines',
                {'symbol': symbol, 'interval': interval, 'limit': limit})
        if d.get('code') == 0 and d.get('data'):
            kl = d['data']
            return ([float(k['close'])  for k in kl],
                    [float(k['high'])   for k in kl],
                    [float(k['low'])    for k in kl],
                    [float(k['volume']) for k in kl],
                    [float(k['open'])   for k in kl])
        return None, None, None, None, None

    def _ticker(self, sym):
        d = pub('/openApi/swap/v2/quote/ticker', {'symbol': sym})
        if d.get('code') == 0 and d.get('data'):
            t = d['data']
            return {'price':  float(t.get('lastPrice', 0)),
                    'change': float(t.get('priceChangePercent', 0))}
        return None

    def _update_btc(self):
        c, *_ = self._klines('BTC-USDT', '1h', 4)
        if c and len(c) >= 2:
            self._btc_1h = (c[-1] - c[-2]) / c[-2] * 100
            self._btc_ok = self._btc_1h >= -BTC_BLOCK
        else:
            self._btc_ok = True

    def _btc_dir(self):
        if self._btc_1h > 0.5:  return 'up'
        if self._btc_1h < -0.5: return 'down'
        return 'flat'

    def _update_equity(self):
        global ACCOUNT_EQUITY
        d = api('GET', '/openApi/swap/v2/user/balance')
        if d.get('code') == 0:
            b  = d.get('data', {})
            eq = float(b.get('equity', 0) or b.get('balance', 0))
            if eq > 0: ACCOUNT_EQUITY = eq

    def _check_ltv(self):
        if not AUTO: return
        d = api('GET', '/openApi/swap/v2/user/balance')
        if d.get('code') != 0: return
        try:
            b      = d.get('data', {})
            equity = float(b.get('equity', 0) or b.get('balance', 0))
            margin = float(b.get('usedMargin', b.get('initialMargin', 0)) or 0)
            if equity <= 0: return
            if margin / equity * 100 >= LTV_WARN:
                self._tg("<b>LTV ALTO</b> — cerrando posiciones")
                for sym in list(self.trades.keys()):
                    tk = self._ticker(sym)
                    if tk: self._close_all(sym, tk['price'], "LTV EMERGENCIA")
        except: pass

    def analyze(self, symbol):
        if symbol in self.trades: return None
        if not self._cd_ok(symbol): return None
        hora = datetime.utcnow().hour
        if hora in SKIP_HOURS: return None
        if not self._btc_ok: return None
        if self._cb_active: return None

        hora_ok, _ = self.learn.hora_ok(hora)
        if not hora_ok: return None

        c5, h5, l5, v5, o5 = self._klines(symbol, '5m', 120)
        if not c5 or len(c5) < 70: return None

        c1h, h1h, l1h, v1h, _ = self._klines(symbol, '1h', 50)
        tk = self._ticker(symbol)
        if not tk or tk['price'] <= 0: return None
        price = tk['price']; change_24 = tk['change']

        trend_1h = 0; rsi_1h = 50.0
        if c1h and len(c1h) >= 25:
            e9_1h  = ema(c1h, 9); e21_1h = ema(c1h, 21)
            rsi_1h = rsi(c1h, 14)
            vwap_1h = calc_vwap(c1h, h1h, l1h, v1h, 30)
            if e9_1h > e21_1h and c1h[-1] > vwap_1h:   trend_1h = 1
            elif e9_1h < e21_1h and c1h[-1] < vwap_1h: trend_1h = -1
        if trend_1h == -1: return None

        e25    = ema(c5, 25); e9_5m = ema(c5, 9)
        rsi_v  = rsi(c5, 14)
        atr_v  = atr_calc(h5, l5, c5, 14)
        atr_pct = atr_v / price * 100 if price > 0 else 0

        setup = analizar_setup_vwap(c5, h5, l5, v5, o5)
        if not setup['tipo']: return None

        vwap_val = setup['vwap']
        sl_price = setup['sl_price']
        sl_pct   = (price - sl_price) / price * 100 if price > 0 else 2.0

        tp1_price   = price * (1 + sl_pct * TP1_RATIO / 100)
        tp2_price   = price * (1 + sl_pct * TP2_RATIO / 100)
        tp_pct_ref  = max(sl_pct * MIN_RR, TP_MIN, TP_MIN_FEE, atr_pct * ATR_TP_M)
        rr          = tp_pct_ref / sl_pct if sl_pct > 0 else 0

        if rr < MIN_RR * 0.8: return None
        if atr_pct < 0.15:    return None
        if rsi_v > 75:        return None
        if change_24 > 20.0:  return None
        if price < e25 * 0.99: return None

        score = 0; reasons = []; factors = []

        cal = setup['calidad']
        score += int(cal * 0.5)
        label = "RUPTURA" if setup['tipo'] == 'A' else "RETESTEO"
        reasons.append("{0}({1:.0f}%)".format(label, cal))
        factors.append("vwap_{0}".format(setup['tipo']))

        bonus_t = self.learn.bonus_tipo(setup['tipo'])
        if bonus_t != 0:
            score += bonus_t
            reasons.append("Learn{0}({1:+d})".format(setup['tipo'], bonus_t))

        if trend_1h == 1:
            score += 20; reasons.append("1H(20)"); factors.append("trend_1h_up")

        if price > vwap_val and price > e25:
            score += 15; reasons.append("PrecioOK(15)"); factors.append("precio_ok")
        elif price > vwap_val:
            score += 8;  reasons.append("OverVWAP(8)"); factors.append("over_vwap")

        if e9_5m > e25:
            score += 12; reasons.append("EMA(12)"); factors.append("ema_alineada")

        if 40 <= rsi_v <= 60:
            score += 15; reasons.append("RSI{0:.0f}(15)".format(rsi_v)); factors.append("rsi_neutral")
        elif rsi_v < 40:
            score += 10; reasons.append("RSIsob{0:.0f}(10)".format(rsi_v)); factors.append("rsi_oversold")
        elif rsi_v < 70:
            score += 5;  reasons.append("RSI{0:.0f}(5)".format(rsi_v)); factors.append("rsi_ok")

        vr = setup['vol_ratio']
        if vr >= 2.0:   score += 12; reasons.append("Vol{0:.1f}x(12)".format(vr)); factors.append("vol_fuerte")
        elif vr >= 1.4: score += 7;  reasons.append("Vol{0:.1f}x(7)".format(vr));  factors.append("vol_medio")

        if self._btc_1h > 1.0:   score += 8; reasons.append("BTC+(8)");  factors.append("btc_up")
        elif self._btc_1h > 0.3: score += 4; reasons.append("BTC~(4)"); factors.append("btc_neutral")

        if rsi_1h < 40:   score += 10; reasons.append("RSI1H{0:.0f}(10)".format(rsi_1h)); factors.append("rsi_1h_oversold")
        elif rsi_1h < 55: score += 5;  reasons.append("RSI1H{0:.0f}(5)".format(rsi_1h));  factors.append("rsi_1h_ok")

        if setup['tipo'] == 'B':
            score += 10; reasons.append("Retest+10"); factors.append("retest_bonus")

        adj = self.learn.factor_score_adj(factors)
        if adj != 0:
            score += adj; reasons.append("Adj({0:+d})".format(adj))

        ok, _ = self.learn.ok(symbol, score)
        if not ok: return None

        if score >= self.learn.opt_score:
            return {
                'price': price, 'change': change_24,
                'score': score, 'score_min': self.learn.opt_score,
                'rsi': rsi_v, 'rsi_1h': rsi_1h,
                'vol_ratio': vr, 'atr_pct': atr_pct,
                'tp1_price': round(tp1_price, 8),
                'tp2_price': round(tp2_price, 8),
                'tp_pct': round(tp_pct_ref, 2),
                'sl_pct': round(sl_pct, 2),
                'sl_price': round(sl_price, 8),
                'rr': round(rr, 2),
                'vwap': vwap_val, 'slope': setup['slope'],
                'entrada_tipo': setup['tipo'],
                'calidad': cal, 'ema25': e25,
                'trend_1h': trend_1h,
                'reasons': ' | '.join(reasons),
                'factors': factors,
                'hora_utc': hora,
                'btc_dir': self._btc_dir(),
            }
        return None

    def _set_lev(self, symbol):
        for side in ('LONG', 'SHORT'):
            try:
                api('POST', '/openApi/swap/v2/trade/leverage',
                    {'symbol': symbol, 'side': side, 'leverage': str(LEVERAGE)})
            except: pass

    def _calc_qty(self, symbol, price, sl_price=None):
        info  = self._contracts.get(symbol, {'step':1,'prec':2,'ctval':1})
        step  = max(float(info.get('step',1)), 1e-6)
        prec  = int(info.get('prec', 2))
        ctval = max(float(info.get('ctval',1)), 1e-9)
        ppc   = price * ctval
        if ppc <= 0: return None, 0
        if sl_price and sl_price < price:
            notional_usdt, _ = calcular_qty_por_riesgo(price, sl_price, ACCOUNT_EQUITY, RISK_PCT)
            notional_usdt = min(notional_usdt, POS_SIZE * LEVERAGE)
            notional_usdt = max(notional_usdt, MIN_TRADE)
        else:
            notional_usdt = max(POS_SIZE * LEVERAGE, MIN_TRADE)
        qty = math.ceil((notional_usdt / ppc) / step) * step
        qty = round(qty, prec)
        val = qty * ppc
        for _ in range(200):
            if val >= MIN_TRADE: break
            qty += step; qty = round(qty, prec); val = qty * ppc
        return (qty, round(val, 4)) if val >= MIN_TRADE else (None, 0)

    def _order(self, sym, side, qty, otype='MARKET', price=None, stop_price=None):
        params = {'symbol': sym, 'side': side.upper(),
                  'type': otype, 'quantity': str(qty)}
        if self._mode == 'hedge':
            params['positionSide'] = 'LONG'
        else:
            if side.upper() == 'SELL': params['reduceOnly'] = 'true'
        if price:
            params['price'] = str(round(price, 8)); params['timeInForce'] = 'GTC'
        if stop_price:
            params['stopPrice'] = str(round(stop_price, 8))
        return api('POST', '/openApi/swap/v2/trade/order', params)

    def _wait_fill(self, sym, oid, timeout=35):
        for _ in range(timeout):
            d = api('GET', '/openApi/swap/v2/trade/order',
                    {'symbol': sym, 'orderId': str(oid)})
            if d.get('code') == 0:
                o  = d.get('data', {}).get('order', {})
                st = o.get('status', '')
                if st == 'FILLED':
                    return float(o.get('executedQty',0)), float(o.get('avgPrice',0))
                if st in ('CANCELED','EXPIRED','REJECTED'): return None, None
            time.sleep(1)
        return None, None

    def _confirm_pos(self, sym, timeout=15):
        for _ in range(timeout):
            d = api('GET', '/openApi/swap/v2/user/positions', {'symbol': sym})
            for p in (d.get('data') or []):
                amt  = float(p.get('positionAmt',0) or 0)
                side = str(p.get('positionSide','')).upper()
                if (side=='LONG' and abs(amt)>0) or (side=='BOTH' and amt>0):
                    return abs(amt), float(p.get('avgPrice') or p.get('entryPrice') or 0)
            time.sleep(1)
        return None, None

    def _cancel_open(self, sym):
        d = api('GET', '/openApi/swap/v2/trade/openOrders', {'symbol': sym})
        for o in (d.get('data',{}).get('orders') or []):
            oid = o.get('orderId')
            if oid: api('DELETE', '/openApi/swap/v2/trade/order',
                        {'symbol': sym, 'orderId': str(oid)})

    def _place_sl(self, sym, qty, sl_price):
        d = self._order(sym, 'SELL', qty, 'STOP_MARKET', stop_price=sl_price)
        ok = d.get('code') == 0
        if not ok:
            d = self._order(sym, 'SELL', qty, 'STOP',
                            price=sl_price*0.999, stop_price=sl_price)
            ok = d.get('code') == 0
        log.info("  {0} SL @ ${1:.6f}".format("OK" if ok else "FAIL", sl_price))
        return ok

    def open_trade(self, sym, sig):
        if not AUTO or sym in self.trades: return False
        if LongBot._opening or len(self.trades) >= MAX_TRADES: return False
        pos = self._get_positions(sym)
        if pos[sym]['long'] > 0 or pos[sym]['short'] > 0:
            log.warning("  {0} ya tiene posicion — omitiendo".format(sym)); return False
        LongBot._opening = True
        try:
            return self._open(sym, sig)
        finally:
            LongBot._opening = False

    def _open(self, sym, sig):
        price    = sig['price']
        sl_price = sig['sl_price']
        tipo_txt = "RUPTURA" if sig['entrada_tipo'] == 'A' else "RETESTEO"

        log.info("\n  LONG {0} [{1}] | Score:{2:.0f} | RR:{3:.2f}:1".format(
            sym, tipo_txt, sig['score'], sig['rr']))
        log.info("  TP1:${0:.4f} | TP2:${1:.4f} | Runner->EMA25".format(
            sig['tp1_price'], sig['tp2_price']))

        self._set_lev(sym); time.sleep(0.2)

        qty, notional = self._calc_qty(sym, price, sl_price)
        if not qty: return False

        limit_p = round(price * (1 - 0.05/100), 8)
        d = self._order(sym, 'BUY', qty, 'LIMIT', price=limit_p)
        if d.get('code') != 0:
            log.error("  LIMIT: {0}".format(d.get('msg'))); return False

        oid = d.get('data', {}).get('orderId')
        filled_qty, fill_price = self._wait_fill(sym, oid, 30)

        if not filled_qty:
            log.warning("  LIMIT sin fill -> MARKET")
            self._cancel_open(sym); time.sleep(0.5)
            d = self._order(sym, 'BUY', qty, 'MARKET')
            if d.get('code') != 0:
                log.error("  MARKET: {0}".format(d.get('msg'))); return False
            filled_qty, fill_price = self._confirm_pos(sym, 12)
            if not filled_qty: return False

        sl_pct_real = (fill_price - sl_price) / fill_price * 100
        tp1_price   = fill_price * (1 + sl_pct_real * TP1_RATIO / 100)
        tp2_price   = fill_price * (1 + sl_pct_real * TP2_RATIO / 100)
        qty_tp1     = round(filled_qty * TP1_PCT / 100, 6)
        qty_tp2     = round(filled_qty * TP2_PCT / 100, 6)

        sl_ok = self._place_sl(sym, filled_qty, sl_price)
        if not sl_ok:
            time.sleep(2); sl_ok = self._place_sl(sym, filled_qty, sl_price)
        if not sl_ok:
            log.error("  SL critico — cerrando")
            self._order(sym, 'SELL', filled_qty, 'MARKET'); return False

        self.trades[sym] = {
            'entry': fill_price, 'qty_total': filled_qty, 'qty_runner': filled_qty,
            'qty_tp1': qty_tp1, 'qty_tp2': qty_tp2,
            'tp1_hit': False, 'tp2_hit': False,
            'tp1_price': tp1_price, 'tp2_price': tp2_price,
            'sl': sl_price, 'sl_vwap': sig['vwap'],
            'tp_pct': sig['tp_pct'], 'sl_pct': sl_pct_real,
            'highest': fill_price, 'opened': datetime.now(),
            'score': sig['score'], 'ema25': sig['ema25'],
            'vwap': sig['vwap'], 'entrada_tipo': sig['entrada_tipo'],
            'usdt': POS_SIZE, 'pnl_parcial': 0.0,
            'factors': sig['factors'],
            'hora_utc': sig['hora_utc'],
            'btc_dir': sig['btc_dir'],
        }
        self.stats['exec']  += 1
        self.stats['fees']  += notional * FEE

        tp_pct1 = sl_pct_real * TP1_RATIO
        tp_pct2 = sl_pct_real * TP2_RATIO
        self._tg(
            "<b>LONG {0}</b> — <b>{1}</b>\n"
            "Score: {2:.0f} | Calidad: {3:.0f}% | RR: {4:.2f}:1\n"
            "Entrada: ${5:.6f}\n"
            "VWAP: ${6:.6f} (slope: {7:+.2f}%)\n"
            "TP1 ({8:.0f}%): ${9:.6f} (+{10:.2f}%)\n"
            "TP2 ({11:.0f}%): ${12:.6f} (+{13:.2f}%)\n"
            "Runner ({14:.0f}%): EMA25 trailing\n"
            "SL (VWAP): ${15:.6f} (-{16:.2f}%)\n"
            "1H: {17} | BTC: {18:+.2f}%".format(
                tipo_txt, sym,
                sig['score'], sig['calidad'], sig['rr'],
                fill_price,
                sig['vwap'], sig['slope'],
                TP1_PCT, tp1_price, tp_pct1,
                TP2_PCT, tp2_price, tp_pct2,
                100-TP1_PCT-TP2_PCT,
                sl_price, sl_pct_real,
                "OK" if sig['trend_1h']==1 else "neutral",
                self._btc_1h)
        )
        return True

    def _close_partial(self, sym, qty, exit_price, label):
        if qty <= 0: return 0
        d = self._order(sym, 'SELL', qty, 'MARKET')
        if d.get('code') != 0:
            log.error("  Cierre parcial {0} {1}: {2}".format(label, sym, d.get('msg')))
            return 0
        t    = self.trades[sym]
        chg  = (exit_price - t['entry']) / t['entry']
        frac = qty / t['qty_total']
        gross = POS_SIZE * LEVERAGE * chg * frac
        fees  = POS_SIZE * LEVERAGE * FEE * 2 * frac
        net   = gross - fees
        t['pnl_parcial']  += net
        t['qty_runner']   -= qty
        self.stats['fees'] += fees
        self._daily_pnl   += net
        self.stats['pnl'] += net
        pct_trade = net / (POS_SIZE * frac) * 100 if frac > 0 else 0
        log.info("  {0} {1}: ${2:+.4f} ({3:+.1f}%) | Resta:{4:.4f}".format(
            label, sym, net, pct_trade, t['qty_runner']))
        self._tg(
            "<b>{0} cerrado — {1}</b>\n"
            "${2:.6f} (+{3:.2f}%)\n"
            "PnL: ${4:+.4f} | Resta: {5:.4f} unidades".format(
                label, sym, exit_price, chg*100, net, t['qty_runner'])
        )
        return net

    def _close_all(self, sym, exit_price, reason):
        if sym not in self.trades: return False
        t = self.trades[sym]
        qty_rem = t['qty_runner']
        if qty_rem > 0:
            self._order(sym, 'SELL', qty_rem, 'MARKET')

        chg_runner   = (exit_price - t['entry']) / t['entry']
        frac_runner  = qty_rem / t['qty_total'] if t['qty_total'] > 0 else 0
        gross_runner = POS_SIZE * LEVERAGE * chg_runner * frac_runner
        fees_runner  = POS_SIZE * LEVERAGE * FEE * 2 * frac_runner
        net_runner   = gross_runner - fees_runner
        net_total    = t['pnl_parcial'] + net_runner
        win          = net_total > 0

        self.stats['closed'] += 1
        self.stats['pnl']    += net_runner
        self.stats['fees']   += fees_runner
        self._daily_pnl      += net_runner
        if win: self.stats['wins']   += 1
        else:   self.stats['losses'] += 1

        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        mins  = int((datetime.now() - t['opened']).total_seconds() / 60)
        emoji = "OK" if win else "LOSS"
        pct   = net_total / POS_SIZE * 100

        log.info("  {0} {1} | ${2:+.4f} ({3:+.1f}%) | {4}min | WR:{5:.0f}%".format(
            emoji, reason, net_total, pct, mins, wr))

        self.learn.record(
            symbol=sym, score=t['score'], pnl=net_total, win=win,
            hora_utc=t.get('hora_utc', datetime.utcnow().hour),
            tipo_entrada=t.get('entrada_tipo', '?'),
            btc_dir=t.get('btc_dir', 'flat'),
            reason=reason,
            factors=t.get('factors', []),
        )

        self._set_cd(sym, 'TP' if any(k in reason for k in ['PROFIT','TP','EMA']) else 'SL')
        self._tg(
            "<b>{0} CERRADO — {1}</b>\n"
            "<b>{2}</b> | {3} | {4}min\n"
            "${5:.6f} -> ${6:.6f}\n"
            "Parciales: ${7:+.4f} | Runner: ${8:+.4f}\n"
            "<b>PnL total: ${9:+.4f} ({10:+.1f}%)</b>\n"
            "<b>Acum: ${11:+.4f} | WR: {12:.0f}%</b>".format(
                "WIN" if win else "LOSS",
                reason, sym,
                "Ruptura" if t['entrada_tipo']=='A' else "Retesteo",
                mins,
                t['entry'], exit_price,
                t['pnl_parcial'], net_runner,
                net_total, pct,
                self.stats['pnl'], wr)
        )
        if self.stats['closed'] % 3 == 0: self.learn.save()
        del self.trades[sym]
        return True

    async def monitor(self):
        for sym in list(self.trades.keys()):
            try:
                t  = self.trades[sym]
                tk = self._ticker(sym)
                if not tk: continue
                cur = tk['price']
                pct = (cur - t['entry']) / t['entry'] * 100

                c5, *_ = self._klines(sym, '5m', 35)
                if c5: t['ema25'] = ema(c5, 25)

                if cur > t['highest']: t['highest'] = cur

                if not t['tp1_hit'] and cur >= t['tp1_price']:
                    self._close_partial(sym, t['qty_tp1'], cur, "TP1({0:.0f}%)".format(TP1_PCT))
                    t['tp1_hit'] = True
                    be = t['entry'] * 1.001
                    if be > t['sl']:
                        t['sl'] = be
                        log.info("  {0} SL -> break-even ${1:.6f}".format(sym, be))
                    continue

                if t['tp1_hit'] and not t['tp2_hit'] and cur >= t['tp2_price']:
                    self._close_partial(sym, t['qty_tp2'], cur, "TP2({0:.0f}%)".format(TP2_PCT))
                    t['tp2_hit'] = True
                    locked = t['entry'] + (cur - t['entry']) * 0.5
                    if locked > t['sl']:
                        t['sl'] = locked
                        log.info("  {0} SL -> ${1:.6f} (50% ganancia)".format(sym, locked))
                    continue

                if t['tp2_hit']:
                    if cur < t['ema25']:
                        if c5 and len(c5) >= 2 and c5[-1] < t['ema25'] and c5[-2] < t['ema25']:
                            self._close_all(sym, cur, "EMA25 RUNNER")
                            continue
                    if t['ema25'] > t['sl']:
                        t['sl'] = t['ema25']
                    continue

                if t['tp1_hit'] and not t['tp2_hit']:
                    if cur < t['ema25'] and c5 and c5[-1] < t['ema25'] and c5[-2] < t['ema25']:
                        self._close_all(sym, cur, "EMA25 PRE-TP2")
                        continue

                if not t['tp1_hit']:
                    if pct > 0.3 and cur < t['ema25']:
                        if c5 and c5[-1] < t['ema25'] and c5[-2] < t['ema25']:
                            self._close_all(sym, cur, "EMA25 EARLY")
                            continue

                if cur <= t['sl']:
                    self._close_all(sym, cur, "STOP LOSS")

            except Exception as e:
                log.debug("monitor {0}: {1}".format(sym, e))

    def _cd_ok(self, sym):
        ts = self._cooldowns.get(sym)
        if not ts: return True
        resume, _ = ts if isinstance(ts, tuple) else (ts, 'TP')
        if time.time() >= resume:
            del self._cooldowns[sym]; return True
        return False

    def _set_cd(self, sym, reason='TP'):
        mins = CD_TP if reason == 'TP' else CD_SL
        self._cooldowns[sym] = (time.time() + mins*60, reason)

    def _daily_reset(self):
        today = datetime.utcnow().date()
        if today != self._daily_date:
            self._daily_pnl   = 0.0
            self._daily_date  = today
            self._cb_active   = False
            self._cb_until    = None
            self.learn.streak = 0
            self._update_equity()
            log.info("Nuevo dia")

    def _circuit_check(self):
        self._daily_reset()
        if self._cb_active:
            if self._cb_until and datetime.utcnow() > self._cb_until:
                self._cb_active = False; self._daily_pnl = 0.0
                log.info("  Circuit breaker OFF")
                self._tg("<b>Circuit breaker OFF</b>")
            return self._cb_active
        if self._daily_pnl < -CB_USDT:
            self._cb_active = True
            self._cb_until  = datetime.utcnow() + timedelta(hours=CB_HOURS)
            log.warning("  CIRCUIT BREAKER | ${0:.3f}".format(self._daily_pnl))
            self._tg(
                "<b>CIRCUIT BREAKER</b>\n"
                "Perdida: ${0:.3f} | Pausa {1}h\n"
                "Reanuda: {2} UTC".format(
                    self._daily_pnl, CB_HOURS,
                    self._cb_until.strftime('%H:%M'))
            )
        return self._cb_active

    def _report(self):
        if datetime.now() - self._last_report < timedelta(hours=2): return
        self._last_report = datetime.now()
        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        pos_lines = []
        for sym, t in self.trades.items():
            tk  = self._ticker(sym)
            cur = tk['price'] if tk else t['entry']
            pct = (cur - t['entry']) / t['entry'] * 100
            estado = "TP1+TP2" if t['tp2_hit'] else "TP1" if t['tp1_hit'] else "->TP1"
            pos_lines.append("  {0}: {1:+.2f}% | {2}".format(sym, pct, estado))
        pos_txt = "\n".join(pos_lines) if pos_lines else "  Sin posiciones"
        self._tg(
            "<b>Reporte LONGS v5.3</b>\n"
            "PnL: ${0:+.4f} | WR: {1:.0f}% | {2} trades\n"
            "Dia: ${3:+.4f} (limite -${4})\n"
            "Equity: ${5:.2f} | Score min: {6:.0f}\n"
            "Circuit: {7} | BTC: {8:+.2f}%\n"
            "{9}".format(
                self.stats['pnl'], wr, total,
                self._daily_pnl, CB_USDT,
                ACCOUNT_EQUITY, self.learn.opt_score,
                "ACTIVO" if self._cb_active else "OK",
                self._btc_1h, pos_txt)
        )

    def _tg(self, msg):
        try:
            if TG_TOKEN and TG_CHAT:
                requests.post(
                    "https://api.telegram.org/bot{0}/sendMessage".format(TG_TOKEN),
                    json={'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                    timeout=6)
        except: pass

    async def run(self):
        log.info("\n Bot LONGS v5.3 arrancado\n")
        iteration = 0
        last_sym = last_ltv = last_hedge = last_equity = 0

        while True:
            try:
                iteration += 1
                self._daily_reset()

                if time.time() - last_sym > 600:
                    self._refresh_symbols(); last_sym = time.time()
                if time.time() - last_ltv > 300:
                    self._check_ltv(); last_ltv = time.time()
                if time.time() - last_hedge > 600:
                    self._scan_orphan_shorts(); last_hedge = time.time()
                if time.time() - last_equity > 1800:
                    self._update_equity(); last_equity = time.time()

                self._update_btc()

                if self._circuit_check():
                    await asyncio.sleep(INTERVAL); continue

                total = self.stats['wins'] + self.stats['losses']
                wr    = self.stats['wins'] / total * 100 if total else 0

                log.info("\n" + "="*72)
                log.info("  #{0} {1} | Abiertos:{2}/{3} | PnL:${4:+.4f} | WR:{5:.0f}%".format(
                    iteration, datetime.now().strftime('%H:%M:%S'),
                    len(self.trades), MAX_TRADES, self.stats['pnl'], wr))
                log.info("  BTC:{0:+.2f}% | Equity:${1:.2f} | Score min:{2:.0f}".format(
                    self._btc_1h, ACCOUNT_EQUITY, self.learn.opt_score))
                log.info("="*72 + "\n")

                await self.monitor()
                self._report()

                if len(self.trades) < MAX_TRADES:
                    log.info("  Escaneando {0} simbolos...".format(len(self.symbols)))
                    found = 0
                    for i, sym in enumerate(self.symbols):
                        if len(self.trades) >= MAX_TRADES: break
                        sig = self.analyze(sym)
                        if sig:
                            found += 1
                            log.info("  {0} {1} | Score:{2:.0f} | RR:{3:.2f}:1".format(
                                sig['entrada_tipo'], sym, sig['score'], sig['rr']))
                            if self.open_trade(sym, sig):
                                await asyncio.sleep(3)
                        if (i+1) % 15 == 0:
                            log.info("  ...{0}/{1}".format(i+1, len(self.symbols)))
                        await asyncio.sleep(0.12)
                    log.info("  Scan: {0} senales".format(found))
                else:
                    log.info("  Max trades — monitoreando")

                await asyncio.sleep(INTERVAL)

            except KeyboardInterrupt:
                log.info("Detenido"); break
            except Exception as e:
                log.error("Error #{0}: {1}".format(iteration, e), exc_info=True)
                await asyncio.sleep(20)

        self.learn.save()

    def _scan_orphan_shorts(self):
        if not AUTO: return
        for sym, sides in self._get_positions().items():
            if sides['short'] > 0:
                self._close_short(sym, sides['short'])
                time.sleep(0.3)


async def main():
    bot = LongBot()
    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot terminado")
