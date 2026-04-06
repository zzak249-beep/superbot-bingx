#!/usr/bin/env python3
"""
BOT — Dynamic Swing Anchored VWAP (DSAVWAP)
═════════════════════════════════════════════
Estrategia basada en el indicador de Zeiierman:
  1. Detecta Swing Highs y Swing Lows (ventana configurable)
  2. Ancla el VWAP desde el último swing significativo
  3. Cuando el VWAP cambia de dirección (bear→bull) = señal LONG
  4. Filtros de contexto: ATR, BTC, hora, volumen

LÓGICA DE SEÑAL:
  ┌────────────────────────────────────────────────────┐
  │  DIR = 1  (BULLISH): último swing HIGH es más      │
  │           reciente que el último swing LOW         │
  │                                                     │
  │  DIR = -1 (BEARISH): último swing LOW es más       │
  │           reciente que el último swing HIGH        │
  │                                                     │
  │  ENTRADA LONG: dir cambia -1 → 1                   │
  │    + precio sobre VWAP anclado                     │
  │    + confirmación 1H alcista                       │
  │                                                     │
  │  SALIDA: dir cambia 1 → -1 (swing bearish)         │
  │          o SL / TP basado en ATR                   │
  └────────────────────────────────────────────────────┘

SL: mínimo del último swing LOW - ATR × multiplicador
TP: SL × ratio RR configurado
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re, json
from datetime import datetime, timedelta
from urllib.parse import urlencode
from collections import defaultdict

# ============================================================================
# HELPERS CONFIG
# ============================================================================

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

# ============================================================================
# CONFIG
# ============================================================================

API_KEY    = os.getenv('BINGX_API_KEY',    '').strip().strip('"').strip("'")
API_SECRET = os.getenv('BINGX_API_SECRET', '').strip().strip('"').strip("'")
TG_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '')
TG_CHAT    = os.getenv('TELEGRAM_CHAT_ID',   '')

# ── Capital ────────────────────────────────────────────────────────────────
AUTO           = clean('AUTO_TRADING_ENABLED', 'true',  'bool')
POS_SIZE       = clean('MAX_POSITION_SIZE',    '10',    'float')
MIN_TRADE      = clean('MIN_TRADE_USDT',       '10',    'float')
LEVERAGE       = min(clean('LEVERAGE',         '2',     'int'), 5)
MAX_TRADES     = clean('MAX_OPEN_TRADES',      '3',     'int')
RISK_PCT       = clean('RISK_PCT',             '1.0',   'float')
ACCOUNT_EQUITY = clean('ACCOUNT_EQUITY',       '100',   'float')

# ── DSAVWAP — Parámetros de la estrategia ─────────────────────────────────
SWING_PERIOD   = clean('SWING_PERIOD',       '50',   'int')    # Ventana detección swing
BASE_APT       = clean('BASE_APT',           '20',   'float')  # Adaptive Price Tracking
USE_ADAPT      = clean('USE_ADAPT_ATR',      'true', 'bool')   # Adaptar APT por ATR
VOL_BIAS       = clean('VOL_BIAS',           '10.0', 'float')  # Influencia volatilidad
ATR_LEN        = clean('ATR_LEN',            '50',   'int')    # Longitud ATR

# ── SL / TP ────────────────────────────────────────────────────────────────
SL_ATR_MULT    = clean('SL_ATR_MULT',        '1.5',  'float')  # SL = swing_low - ATR×mult
TP_RR          = clean('TP_RR',              '2.0',  'float')  # RR mínimo
SL_MAX_PCT     = clean('SL_MAX_PCT',         '4.0',  'float')  # SL máximo %
MIN_RR         = clean('MIN_RR',             '1.5',  'float')

# ── Filtros ────────────────────────────────────────────────────────────────
MIN_SCORE      = clean('MIN_SCORE',          '45',   'float')
MIN_VOL        = clean('MIN_VOLUME_24H',     '500000','float')
MAX_SYMS       = clean('MAX_SYMBOLS',        '60',   'int')
BTC_BLOCK      = clean('BTC_BEAR_BLOCK_PCT', '2.0',  'float')
MIN_ATR_PCT    = clean('MIN_ATR_PCT',        '0.15', 'float')

# ── Circuit breaker ────────────────────────────────────────────────────────
CB_USDT        = clean('CIRCUIT_BREAKER_USDT', '3.0', 'float')
CB_HOURS       = clean('CB_PAUSE_HOURS',       '2',   'int')
MAX_STREAK     = clean('MAX_LOSING_STREAK',    '4',   'int')

# ── Cooldowns ─────────────────────────────────────────────────────────────
CD_TP          = clean('COOLDOWN_TP_MIN',  '5',   'int')
CD_SL          = clean('COOLDOWN_SL_MIN',  '60',  'int')

# ── Misc ───────────────────────────────────────────────────────────────────
INTERVAL       = clean('CHECK_INTERVAL',  '120', 'int')
SKIP_HOURS     = {2, 3}
BASE_URL       = "https://open-api.bingx.com"
FEE            = 0.0002

EXCLUDE = {
    'DOW','SP500','GOLD','SILVER','XAU','OIL','BRENT','EUR','GBP','JPY',
    'TSLA','AAPL','MSFT','GOOGL','AMZN','META','NVDA','COIN','MSTR',
    'WHEAT','CORN','SUGAR','PAXG','XAUT',
}

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ============================================================================
# API
# ============================================================================

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

# ============================================================================
# INDICADORES
# ============================================================================

def ema(prices, n):
    if not prices: return 0
    if len(prices) < n: return sum(prices) / len(prices)
    k, e = 2 / (n + 1), prices[0]
    for p in prices[1:]: e = p * k + e * (1 - k)
    return e

def rma(data, n):
    """Wilder moving average (RMA)"""
    if len(data) < n: return sum(data) / max(len(data), 1)
    result = sum(data[:n]) / n
    for v in data[n:]: result = (result * (n - 1) + v) / n
    return result

def atr_series(highs, lows, closes, n=14):
    """Devuelve serie completa de ATR (Wilder)"""
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    if not trs: return [0.0]
    result = [sum(trs[:n]) / n] if len(trs) >= n else [sum(trs) / len(trs)]
    for tr in trs[n:]:
        result.append((result[-1] * (n-1) + tr) / n)
    # pad al inicio
    pad = len(closes) - len(result) - 1
    return [result[0]] * max(pad, 0) + result

def atr_single(highs, lows, closes, n=14):
    s = atr_series(highs, lows, closes, n)
    return s[-1] if s else 0

# ============================================================================
# MOTOR DSAVWAP
# ============================================================================

import math as _math

def _alpha_from_apt(apt):
    """Convierte APT a alpha EWMA (half-life decay)"""
    apt = max(1.0, apt)
    decay = _math.exp(-_math.log(2.0) / apt)
    return 1.0 - decay


def detect_swings(highs, lows, period=50):
    """
    Detecta swing highs y lows usando ventana deslizante.
    Retorna (ph, pl, ph_idx, pl_idx) — último swing high/low y sus índices.
    """
    n = len(highs)
    if n < period * 2: return None, None, 0, 0

    ph = ph_idx = None
    pl = pl_idx = None

    # Buscar el swing high más reciente: máximo en ventana [i-period, i]
    for i in range(n - 1, max(n - period * 3, period - 1), -1):
        window_h = highs[max(0, i-period):i+1]
        if highs[i] == max(window_h):
            if ph is None:
                ph = highs[i]; ph_idx = i
            break

    # Buscar el swing low más reciente: mínimo en ventana [i-period, i]
    for i in range(n - 1, max(n - period * 3, period - 1), -1):
        window_l = lows[max(0, i-period):i+1]
        if lows[i] == min(window_l):
            if pl is None:
                pl = lows[i]; pl_idx = i
            break

    return ph, pl, ph_idx or 0, pl_idx or 0


def calc_anchored_vwap(closes, highs, lows, volumes, anchor_idx,
                       apt=20, use_adapt=True, vol_bias=10.0, atr_len=50):
    """
    Calcula el VWAP anclado desde anchor_idx con suavizado adaptativo.
    Retorna el valor actual del anchored VWAP.
    """
    n = len(closes)
    if anchor_idx >= n or anchor_idx < 0: return closes[-1]

    # ATR series para adaptar APT
    atr_s = atr_series(highs, lows, closes, atr_len)
    atr_avg = rma(atr_s, atr_len)

    slice_c = closes[anchor_idx:]
    slice_h = highs[anchor_idx:]
    slice_l = lows[anchor_idx:]
    slice_v = volumes[anchor_idx:]
    slice_atr = atr_s[anchor_idx:] if anchor_idx < len(atr_s) else [atr_s[-1]] * len(slice_c)

    if not slice_c: return closes[-1]

    cum_pv = slice_c[0] * slice_v[0]
    cum_v  = slice_v[0]
    vwap_v = cum_pv / cum_v if cum_v > 0 else slice_c[0]

    for i in range(1, len(slice_c)):
        if use_adapt and atr_avg > 0:
            ratio = slice_atr[i] / atr_avg if i < len(slice_atr) else 1.0
            apt_i = max(5.0, min(300.0, apt / max(ratio ** vol_bias, 0.01)))
        else:
            apt_i = apt

        alpha = _alpha_from_apt(apt_i)
        hlc3  = (slice_h[i] + slice_l[i] + slice_c[i]) / 3
        pv    = hlc3 * slice_v[i]

        cum_pv = alpha * pv         + (1 - alpha) * cum_pv
        cum_v  = alpha * slice_v[i] + (1 - alpha) * cum_v
        vwap_v = cum_pv / cum_v if cum_v > 0 else hlc3

    return vwap_v


def dsavwap_signal(closes, highs, lows, volumes,
                   swing_period=50, base_apt=20,
                   use_adapt=True, vol_bias=10.0, atr_len=50):
    """
    Motor principal: Dynamic Swing Anchored VWAP

    Retorna dict:
    {
      'dir':         1 (bullish) | -1 (bearish),
      'dir_prev':    dirección anterior,
      'dir_changed': bool — cambio de dirección esta vela,
      'señal':       'LONG' | 'NO',
      'vwap':        float — VWAP anclado actual,
      'ph':          float — último swing high,
      'pl':          float — último swing low,
      'ph_idx':      int,
      'pl_idx':      int,
      'anchor_idx':  int — índice de anclaje del VWAP,
      'swing_label': str — 'HH'|'HL'|'LH'|'LL',
      'sl_price':    float,
      'sl_pct':      float,
      'atr':         float,
    }
    """
    result = {
        'dir': 0, 'dir_prev': 0, 'dir_changed': False,
        'señal': 'NO', 'vwap': closes[-1] if closes else 0,
        'ph': 0, 'pl': 0, 'ph_idx': 0, 'pl_idx': 0,
        'anchor_idx': 0, 'swing_label': '',
        'sl_price': 0, 'sl_pct': 0, 'atr': 0,
    }

    min_bars = swing_period * 3 + atr_len + 10
    if len(closes) < min_bars:
        result['señal'] = 'DATOS_INSUF'
        return result

    ph, pl, ph_idx, pl_idx = detect_swings(highs, lows, swing_period)
    if ph is None or pl is None: return result

    result['ph'] = ph; result['ph_idx'] = ph_idx
    result['pl'] = pl; result['pl_idx'] = pl_idx

    # Dirección actual: si el swing HIGH más reciente > swing LOW más reciente → bull
    dir_now  = 1 if ph_idx > pl_idx else -1

    # Dirección previa: calcula sobre datos sin la última vela
    if len(closes) >= min_bars + 1:
        ph_p, pl_p, ph_idx_p, pl_idx_p = detect_swings(highs[:-1], lows[:-1], swing_period)
        dir_prev = 1 if (ph_idx_p or 0) > (pl_idx_p or 0) else -1
    else:
        dir_prev = dir_now

    result['dir']       = dir_now
    result['dir_prev']  = dir_prev
    result['dir_changed'] = (dir_now != dir_prev)

    # Anclar VWAP desde el swing más reciente
    anchor_idx = max(ph_idx, pl_idx)
    result['anchor_idx'] = anchor_idx

    vwap_v = calc_anchored_vwap(
        closes, highs, lows, volumes, anchor_idx,
        apt=base_apt, use_adapt=use_adapt, vol_bias=vol_bias, atr_len=atr_len
    )
    result['vwap'] = vwap_v

    # Label del swing (HH/HL/LH/LL)
    # Comparar con el swing previo del mismo tipo
    label = ''
    if dir_now == 1:   # último pivot es HIGH → buscar si es HH o LH
        prev_ph = ph  # sin datos previos suficientes, skip label
        label = 'HL' if pl > 0 else 'HL'
    else:
        label = 'LH'
    result['swing_label'] = label

    # ATR actual
    atr_v = atr_single(highs, lows, closes, 14)
    result['atr'] = atr_v

    # SL: bajo el swing low más reciente - ATR × multiplicador
    price = closes[-1]
    sl_price = pl - atr_v * SL_ATR_MULT
    sl_max   = price * (1 - SL_MAX_PCT / 100)
    sl_price = max(sl_price, sl_max)
    sl_pct   = (price - sl_price) / price * 100 if price > 0 else 0

    result['sl_price'] = round(sl_price, 8)
    result['sl_pct']   = round(sl_pct, 3)

    # Señal LONG: dirección cambia a bullish + precio sobre VWAP
    price_over_vwap = closes[-1] > vwap_v
    if dir_now == 1 and dir_prev == -1 and price_over_vwap:
        result['señal'] = 'LONG'
    elif dir_now == 1 and result['dir_changed'] == False and price_over_vwap:
        # Continuación alcista (sin cambio de dir, ya estamos en bull)
        result['señal'] = 'LONG_CONT'
    else:
        result['señal'] = 'NO'

    return result

# ============================================================================
# APRENDIZAJE
# ============================================================================

class Learning:
    def __init__(self):
        self.history    = []
        self.sym_stats  = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0,'n':0})
        self.opt_score  = MIN_SCORE
        self.blacklist  = set()
        self.streak     = 0
        self.last10     = []
        self.by_hour    = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0})
        self.by_reason  = defaultdict(lambda: {'n':0,'pnl':0.0})
        self.score_boost = {}

    def record(self, symbol, score, pnl, win, hora_utc=None, reason='?'):
        rec = {
            'ts': datetime.now().isoformat(), 'sym': symbol,
            'score': score, 'pnl': pnl, 'win': win,
            'hora': hora_utc or datetime.utcnow().hour,
            'reason': reason,
        }
        self.history.append(rec); self.last10.append(rec)
        if len(self.last10) > 10: self.last10.pop(0)
        s = self.sym_stats[symbol]; s['n'] += 1; s['pnl'] += pnl
        k = 'w' if win else 'l'
        if win: s['w'] += 1; self.streak = 0
        else:   s['l'] += 1; self.streak += 1
        self.by_hour[rec['hora']][k] += 1
        self.by_hour[rec['hora']]['pnl'] += pnl
        self.by_reason[reason]['n']   += 1
        self.by_reason[reason]['pnl'] += pnl
        self._adjust()
        if len(self.history) % 10 == 0: self._reporte()

    def _adjust(self):
        n = len(self.history)
        if n >= 10:
            wr = sum(1 for t in self.last10 if t['win']) / len(self.last10)
            if wr < 0.35:   self.opt_score = min(self.opt_score + 5, 85)
            elif wr < 0.45: self.opt_score = min(self.opt_score + 2, 85)
            elif wr > 0.65: self.opt_score = max(self.opt_score - 2, MIN_SCORE)
            elif wr > 0.75: self.opt_score = max(self.opt_score - 4, MIN_SCORE)
        for sym, s in self.sym_stats.items():
            tot = s['w'] + s['l']
            if tot >= 5 and s['pnl'] < -2.0 and (s['w']/tot < 0.20):
                if sym not in self.blacklist:
                    self.blacklist.add(sym)
                    log.warning(f"  [LEARN] 🚫 {sym} → blacklist")

    def ok(self, sym, score):
        if sym in self.blacklist:     return False, "blacklist"
        if score < self.opt_score:    return False, f"score {score:.0f}<{self.opt_score:.0f}"
        if self.streak >= MAX_STREAK: return False, f"streak -{self.streak}"
        return True, "ok"

    def hora_ok(self, h):
        d = self.by_hour.get(h)
        if not d: return True, "ok"
        tot = d['w'] + d['l']
        if tot < 5: return True, "ok"
        if d['w']/tot < 0.25: return False, f"hora {h}h WR={d['w']/tot:.0%}"
        return True, "ok"

    def _reporte(self):
        n   = len(self.history)
        wr  = sum(1 for t in self.history if t['win'])/n*100 if n else 0
        pnl = sum(t['pnl'] for t in self.history)
        reas_txt = "".join(
            f"  {r}: ${d['pnl']:+.2f} ({d['n']}×)\n"
            for r,d in sorted(self.by_reason.items(), key=lambda x: x[1]['pnl'], reverse=True))
        msg = (
            f"<b>🧠 APRENDIZAJE — {n} trades</b>\n"
            f"WR: {wr:.0f}% | PnL: ${pnl:+.4f} | Score mín: {self.opt_score:.0f}\n"
            f"Blacklist: {len(self.blacklist)} símbolos\n\n"
            f"<b>🚪 Cierres:</b>\n{reas_txt or '  Sin datos\n'}"
        )
        log.info(f"[LEARN] #{n//10}: WR={wr:.0f}% PnL=${pnl:+.4f}")
        try:
            if TG_TOKEN and TG_CHAT:
                requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                              json={'chat_id':TG_CHAT,'text':msg,'parse_mode':'HTML'}, timeout=6)
        except: pass

    def save(self, fp='/tmp/bot_dsavwap.json'):
        try:
            json.dump({
                'history': self.history[-200:],
                'sym_stats': dict(self.sym_stats),
                'opt_score': self.opt_score,
                'blacklist': list(self.blacklist),
                'by_hour': dict(self.by_hour),
                'by_reason': dict(self.by_reason),
            }, open(fp,'w'), indent=2)
        except: pass

    def load(self, fp='/tmp/bot_dsavwap.json'):
        for path in [fp, '/tmp/bot_dsavwap_bak.json']:
            try:
                if not os.path.exists(path): continue
                d = json.load(open(path))
                self.history    = d.get('history', [])
                self.sym_stats  = defaultdict(lambda:{'w':0,'l':0,'pnl':0.0,'n':0}, d.get('sym_stats',{}))
                self.opt_score  = d.get('opt_score', MIN_SCORE)
                self.blacklist  = set(d.get('blacklist', []))
                self.by_hour    = defaultdict(lambda:{'w':0,'l':0,'pnl':0.0}, d.get('by_hour',{}))
                self.by_reason  = defaultdict(lambda:{'n':0,'pnl':0.0}, d.get('by_reason',{}))
                log.info(f"  [LEARN] {len(self.history)} trades | Score: {self.opt_score:.0f} | BL: {len(self.blacklist)}")
                return
            except: continue

# ============================================================================
# BOT PRINCIPAL — DSAVWAP
# ============================================================================

class DSAVWAPBot:
    _opening = False

    def __init__(self):
        log.info("=" * 70)
        log.info("  BOT — Dynamic Swing Anchored VWAP (Zeiierman)")
        log.info(f"  Capital: ${POS_SIZE} | Riesgo: {RISK_PCT}%/trade | {LEVERAGE}x")
        log.info(f"  Swing Period: {SWING_PERIOD} | APT: {BASE_APT} | Adapt ATR: {USE_ADAPT}")
        log.info(f"  SL: swing_low - ATR×{SL_ATR_MULT} | TP RR: {TP_RR}:1")
        log.info(f"  Vol Bias: {VOL_BIAS} | ATR Len: {ATR_LEN}")
        log.info("=" * 70)

        self.symbols      = []
        self.trades       = {}
        self._contracts   = {}
        self._cooldowns   = {}
        self._last_report = datetime.now() - timedelta(hours=3)
        self._btc_1h      = 0.0
        self._btc_ok      = True
        self._mode        = 'hedge'
        self._daily_pnl   = 0.0
        self._daily_date  = datetime.utcnow().date()
        self._cb_active   = False
        self._cb_until    = None
        self.learn        = Learning()
        self.learn.load()
        self.stats = {'exec':0,'closed':0,'wins':0,'losses':0,'pnl':0.0,'fees':0.0}

        if not self._connect():
            log.error("❌ Sin conexión BingX"); sys.exit(1)
        self._detect_mode()
        self._load_contracts()
        self._refresh_symbols()
        self._recover()

        self._tg(
            f"<b>🤖 Bot DSAVWAP — Dynamic Swing Anchored VWAP</b>\n"
            f"Swing:{SWING_PERIOD} | APT:{BASE_APT} | AdaptATR:{'✅' if USE_ADAPT else '❌'}\n"
            f"SL: swing_low - ATR×{SL_ATR_MULT} | TP: {TP_RR}:1\n"
            f"Posiciones recuperadas: {len(self.trades)}"
        )

    # ═══════════════════════════════════════════════════════════════
    # SETUP
    # ═══════════════════════════════════════════════════════════════

    def _connect(self) -> bool:
        global AUTO, ACCOUNT_EQUITY
        if not AUTO: return True
        if not API_KEY or not API_SECRET:
            log.error("❌ API keys no configuradas"); AUTO = False; return False
        d = api('GET', '/openApi/swap/v2/user/balance')
        if d.get('code') == 0:
            b = d.get('data', {}); eq = float(b.get('equity', b.get('balance', 0)) or 0)
            if eq > 0: ACCOUNT_EQUITY = eq
            log.info(f"✅ BingX conectado | ${ACCOUNT_EQUITY:.2f} USDT"); return True
        log.error(f"❌ [{d.get('code')}]: {d.get('msg')}"); AUTO = False; return False

    def _detect_mode(self):
        try:
            d = api('GET', '/openApi/swap/v2/user/positions', {'symbol':'BTC-USDT'})
            for p in (d.get('data') or []):
                s = str(p.get('positionSide','')).upper()
                if s in ('LONG','SHORT'): self._mode='hedge'; log.info("  Modo: HEDGE"); return
                if s == 'BOTH': self._mode='oneway'; log.info("  Modo: ONE-WAY"); return
        except: pass
        log.info("  Modo: HEDGE (default)")

    def _load_contracts(self):
        d = pub('/openApi/swap/v2/quote/contracts')
        if d.get('code') == 0:
            for c in d.get('data', []):
                s = c.get('symbol', '')
                if s: self._contracts[s] = {
                    'step': float(c.get('tradeMinQuantity', 1)),
                    'prec': int(c.get('quantityPrecision', 2)),
                    'ctval': float(c.get('contractSize', 1)),
                }
            log.info(f"  Contratos: {len(self._contracts)}")

    def _refresh_symbols(self):
        d = pub('/openApi/swap/v2/quote/ticker')
        if d.get('code') != 0:
            self.symbols = self.symbols or ['BTC-USDT','ETH-USDT','SOL-USDT']; return
        items = []
        for t in d.get('data', []):
            sym = t.get('symbol', '')
            if not sym.endswith('-USDT'): continue
            base = sym.replace('-USDT', '').upper()
            if any(ex in base for ex in EXCLUDE): continue
            try:
                price = float(t.get('lastPrice', 0))
                vol   = float(t.get('volume', 0)) * price
                if vol >= MIN_VOL and price > 0: items.append({'sym':sym,'vol':vol})
            except: continue
        items.sort(key=lambda x: x['vol'], reverse=True)
        self.symbols = [x['sym'] for x in items[:MAX_SYMS]]
        log.info(f"  Símbolos: {len(self.symbols)}")

    # ═══════════════════════════════════════════════════════════════
    # HEDGE FIX
    # ═══════════════════════════════════════════════════════════════

    def _get_exchange_positions(self, symbol=None):
        params = {}
        if symbol: params['symbol'] = symbol
        d = api('GET', '/openApi/swap/v2/user/positions', params)
        result = defaultdict(lambda: {'long':0.0,'short':0.0})
        for p in (d.get('data') or []):
            try:
                amt  = float(p.get('positionAmt', 0) or 0)
                sym  = p.get('symbol', '')
                side = str(p.get('positionSide', '')).upper()
                if not sym or abs(amt) == 0: continue
                if side == 'LONG' or (side == 'BOTH' and amt > 0):
                    result[sym]['long'] = abs(amt)
                elif side == 'SHORT' or (side == 'BOTH' and amt < 0):
                    result[sym]['short'] = abs(amt)
            except: continue
        return result

    def _has_any_position(self, symbol) -> bool:
        pos = self._get_exchange_positions(symbol)
        return pos[symbol]['long'] > 0 or pos[symbol]['short'] > 0

    def _order_close_short(self, sym, qty):
        params = {'symbol':sym,'side':'BUY','type':'MARKET','quantity':str(qty)}
        if self._mode == 'hedge': params['positionSide'] = 'SHORT'
        else: params['reduceOnly'] = 'true'
        return api('POST', '/openApi/swap/v2/trade/order', params)

    def _recover(self):
        if not AUTO: return
        all_pos = self._get_exchange_positions(); n_rec=0; n_sh=0
        for sym, sides in all_pos.items():
            if sides['short'] > 0:
                log.warning(f"  ⚠️ SHORT huérfano: {sym} → cerrando")
                if self._order_close_short(sym, sides['short']).get('code') == 0:
                    n_sh += 1
                time.sleep(0.5)
            if sides['long'] > 0 and sym not in self.trades:
                d2 = api('GET', '/openApi/swap/v2/user/positions', {'symbol':sym})
                entry = 0.0
                for p in (d2.get('data') or []):
                    s2  = str(p.get('positionSide','')).upper()
                    a2  = float(p.get('positionAmt', 0) or 0)
                    if (s2 == 'LONG' and abs(a2) > 0) or (s2 == 'BOTH' and a2 > 0):
                        entry = float(p.get('avgPrice') or p.get('entryPrice') or 0); break
                if entry <= 0: continue
                qty = sides['long']
                sl_rec = entry * (1 - (SL_MAX_PCT + 0.5) / 100)
                tp_rec = entry * (1 + SL_MAX_PCT * TP_RR / 100)
                self.trades[sym] = {
                    'entry': entry, 'qty': qty,
                    'sl': sl_rec, 'sl_orig': sl_rec,
                    'tp': tp_rec, 'highest': entry,
                    'opened': datetime.now(), 'score': 0,
                    'vwap': entry, 'swing_low': entry * 0.98,
                    'sl_pct': SL_MAX_PCT, 'atr': 0,
                    'hora_utc': datetime.utcnow().hour,
                }
                n_rec += 1
                log.info(f"  ♻️ LONG recuperado: {sym} @ ${entry:.6f}")
        log.info(f"  Recuperadas: {n_rec} | SHORTs cerrados: {n_sh}")

    def _scan_orphan_shorts(self):
        if not AUTO: return
        for sym, sides in self._get_exchange_positions().items():
            if sides['short'] > 0:
                self._order_close_short(sym, sides['short']); time.sleep(0.3)

    # ═══════════════════════════════════════════════════════════════
    # MERCADO
    # ═══════════════════════════════════════════════════════════════

    def _klines(self, symbol, interval='5m', limit=200):
        d = pub('/openApi/swap/v3/quote/klines',
                {'symbol':symbol,'interval':interval,'limit':limit})
        if d.get('code') == 0 and d.get('data'):
            kl = d['data']
            return ([float(k['close'])  for k in kl],
                    [float(k['high'])   for k in kl],
                    [float(k['low'])    for k in kl],
                    [float(k['volume']) for k in kl],
                    [float(k['open'])   for k in kl])
        return None, None, None, None, None

    def _ticker(self, sym):
        d = pub('/openApi/swap/v2/quote/ticker', {'symbol':sym})
        if d.get('code') == 0 and d.get('data'):
            t = d['data']
            return {'price':float(t.get('lastPrice',0)), 'change':float(t.get('priceChangePercent',0))}
        return None

    def _update_btc(self):
        c, *_ = self._klines('BTC-USDT', '1h', 4)
        if c and len(c) >= 2:
            self._btc_1h = (c[-1] - c[-2]) / c[-2] * 100
            self._btc_ok = self._btc_1h >= -BTC_BLOCK
        else: self._btc_ok = True

    def _update_equity(self):
        global ACCOUNT_EQUITY
        d = api('GET', '/openApi/swap/v2/user/balance')
        if d.get('code') == 0:
            b  = d.get('data', {}); eq = float(b.get('equity', 0) or b.get('balance', 0))
            if eq > 0: ACCOUNT_EQUITY = eq

    # ═══════════════════════════════════════════════════════════════
    # ANÁLISIS — Motor DSAVWAP
    # ═══════════════════════════════════════════════════════════════

    def analyze(self, symbol):
        if symbol in self.trades: return None
        if not self._cd_ok(symbol): return None
        hora = datetime.utcnow().hour
        if hora in SKIP_HOURS: return None
        if not self._btc_ok: return None
        if self._cb_active: return None

        hora_ok, _ = self.learn.hora_ok(hora)
        if not hora_ok: return None

        # ── Datos 5M ─────────────────────────────────────────────
        c5, h5, l5, v5, o5 = self._klines(symbol, '5m', 250)
        if not c5 or len(c5) < SWING_PERIOD * 3 + ATR_LEN + 20: return None

        tk = self._ticker(symbol)
        if not tk or tk['price'] <= 0: return None
        price = tk['price']; change_24 = tk['change']

        # ── Filtros básicos ───────────────────────────────────────
        atr_v   = atr_single(h5, l5, c5, 14)
        atr_pct = atr_v / price * 100 if price > 0 else 0
        if atr_pct < MIN_ATR_PCT: return None
        if change_24 >  20.0: return None
        if change_24 < -15.0: return None

        # ── Confirmación 1H ───────────────────────────────────────
        c1h, h1h, l1h, v1h, _ = self._klines(symbol, '1h', 120)
        trend_1h = 0
        sig_1h   = None
        if c1h and len(c1h) >= SWING_PERIOD * 2 + ATR_LEN:
            sig_1h = dsavwap_signal(
                c1h, h1h, l1h, v1h,
                swing_period=SWING_PERIOD, base_apt=BASE_APT,
                use_adapt=USE_ADAPT, vol_bias=VOL_BIAS, atr_len=ATR_LEN
            )
            trend_1h = sig_1h['dir']
        if trend_1h == -1: return None   # 1H bajista

        # ── MOTOR PRINCIPAL: DSAVWAP ──────────────────────────────
        sig = dsavwap_signal(
            c5, h5, l5, v5,
            swing_period=SWING_PERIOD, base_apt=BASE_APT,
            use_adapt=USE_ADAPT, vol_bias=VOL_BIAS, atr_len=ATR_LEN
        )

        # Solo operar en cambio de dirección a bullish (señal principal)
        if sig['señal'] not in ('LONG', 'LONG_CONT'): return None

        # Cambio de dirección fuerte = mejor señal
        is_reversal = sig['dir_changed'] and sig['dir'] == 1

        sl_price = sig['sl_price']
        sl_pct   = sig['sl_pct']
        if sl_pct <= 0 or sl_pct > SL_MAX_PCT * 1.1: return None

        tp_price = price * (1 + sl_pct * TP_RR / 100)
        rr       = sl_pct * TP_RR / sl_pct if sl_pct > 0 else 0

        if TP_RR < MIN_RR: return None

        # ── VWAP anclado como soporte ─────────────────────────────
        price_over_vwap = price > sig['vwap']
        vwap_dist_pct   = (price - sig['vwap']) / sig['vwap'] * 100 if sig['vwap'] > 0 else 0

        # ── EMA contexto ──────────────────────────────────────────
        e9  = ema(c5, 9)
        e21 = ema(c5, 21)
        e50 = ema(c5, 50)
        ema_alcista = e9 > e21 > e50

        # ── Volumen ───────────────────────────────────────────────
        vol_avg = sum(v5[-6:-1]) / 5 if len(v5) >= 6 else v5[-1]
        vol_ratio = v5[-1] / vol_avg if vol_avg > 0 else 1.0

        # ── SCORING ───────────────────────────────────────────────
        score = 0; reasons = []

        # Señal principal
        if is_reversal:
            score += 50; reasons.append("REVERSAL(50)")
        else:
            score += 25; reasons.append("CONT(25)")

        # Precio sobre VWAP
        if price_over_vwap:
            score += 10; reasons.append(f"VWAP↑(10)")
            if vwap_dist_pct > 0.3:
                score += 5; reasons.append("VWAP_dist(5)")

        # 1H confirmado
        if trend_1h == 1:
            score += 12; reasons.append("1H↑(12)")
        elif trend_1h == 0:
            score += 5; reasons.append("1H_neutral(5)")

        # 1H también en reversal
        if sig_1h and sig_1h['dir_changed'] and sig_1h['dir'] == 1:
            score += 10; reasons.append("1H_reversal(10)")

        # EMAs alineadas
        if ema_alcista:
            score += 8; reasons.append("EMA↑(8)")
        elif e9 > e21:
            score += 4; reasons.append("EMA9>21(4)")

        # Volumen en vela de señal
        if vol_ratio >= 2.0:   score += 10; reasons.append(f"Vol{vol_ratio:.1f}x(10)")
        elif vol_ratio >= 1.4: score += 5;  reasons.append(f"Vol{vol_ratio:.1f}x(5)")

        # BTC favorable
        if self._btc_1h > 1.0:   score += 8; reasons.append("BTC↑(8)")
        elif self._btc_1h > 0.3: score += 4; reasons.append("BTC~(4)")

        # ATR activo (volatilidad buena)
        if atr_pct > 0.3: score += 5; reasons.append(f"ATR{atr_pct:.2f}%(5)")

        # Aprendizaje
        ok, reason = self.learn.ok(symbol, score)
        if not ok: return None

        if score >= self.learn.opt_score:
            return {
                'price':      price,
                'change':     change_24,
                'score':      score,
                'señal':      sig['señal'],
                'is_reversal': is_reversal,
                'vwap':       sig['vwap'],
                'ph':         sig['ph'],
                'pl':         sig['pl'],
                'ph_idx':     sig['ph_idx'],
                'pl_idx':     sig['pl_idx'],
                'dir':        sig['dir'],
                'swing_label': sig['swing_label'],
                'sl_price':   sl_price,
                'sl_pct':     sl_pct,
                'tp_price':   round(tp_price, 8),
                'tp_pct':     round(sl_pct * TP_RR, 2),
                'rr':         TP_RR,
                'atr':        atr_v,
                'atr_pct':    atr_pct,
                'vol_ratio':  vol_ratio,
                'trend_1h':   trend_1h,
                'ema9':       e9, 'ema21': e21, 'ema50': e50,
                'reasons':    ' | '.join(reasons),
                'hora_utc':   hora,
            }
        return None

    # ═══════════════════════════════════════════════════════════════
    # GESTIÓN DE POSICIONES
    # ═══════════════════════════════════════════════════════════════

    def _set_lev(self, sym):
        for side in ('LONG', 'SHORT'):
            try: api('POST', '/openApi/swap/v2/trade/leverage',
                     {'symbol':sym,'side':side,'leverage':str(LEVERAGE)})
            except: pass

    def _calc_qty(self, sym, price, sl_price):
        info  = self._contracts.get(sym, {'step':1,'prec':2,'ctval':1})
        step  = max(float(info.get('step', 1)), 1e-6)
        prec  = int(info.get('prec', 2))
        ctval = max(float(info.get('ctval', 1)), 1e-9)
        ppc   = price * ctval
        if ppc <= 0: return None, 0
        if sl_price < price:
            dist_pct = (price - sl_price) / price * 100
            riesgo   = ACCOUNT_EQUITY * (RISK_PCT / 100)
            notional = min(riesgo / (dist_pct / 100), POS_SIZE * LEVERAGE)
            notional = max(notional, MIN_TRADE)
        else:
            notional = max(POS_SIZE * LEVERAGE, MIN_TRADE)
        qty = math.ceil((notional / ppc) / step) * step; qty = round(qty, prec)
        val = qty * ppc
        for _ in range(200):
            if val >= MIN_TRADE: break
            qty += step; qty = round(qty, prec); val = qty * ppc
        return (qty, round(val, 4)) if val >= MIN_TRADE else (None, 0)

    def _order(self, sym, side, qty, otype='MARKET', price=None, stop_price=None):
        params = {'symbol':sym,'side':side.upper(),'type':otype,'quantity':str(qty)}
        if self._mode == 'hedge': params['positionSide'] = 'LONG'
        else:
            if side.upper() == 'SELL': params['reduceOnly'] = 'true'
        if price:       params['price'] = str(round(price, 8)); params['timeInForce'] = 'GTC'
        if stop_price:  params['stopPrice'] = str(round(stop_price, 8))
        return api('POST', '/openApi/swap/v2/trade/order', params)

    def _wait_fill(self, sym, oid, timeout=35):
        for _ in range(timeout):
            d = api('GET', '/openApi/swap/v2/trade/order',
                    {'symbol':sym,'orderId':str(oid)})
            if d.get('code') == 0:
                o  = d.get('data', {}).get('order', {})
                st = o.get('status', '')
                if st == 'FILLED':
                    return float(o.get('executedQty', 0)), float(o.get('avgPrice', 0))
                if st in ('CANCELED','EXPIRED','REJECTED'): return None, None
            time.sleep(1)
        return None, None

    def _confirm_pos(self, sym, timeout=15):
        for _ in range(timeout):
            d = api('GET', '/openApi/swap/v2/user/positions', {'symbol':sym})
            for p in (d.get('data') or []):
                amt  = float(p.get('positionAmt', 0) or 0)
                side = str(p.get('positionSide', '')).upper()
                if (side == 'LONG' and abs(amt) > 0) or (side == 'BOTH' and amt > 0):
                    return abs(amt), float(p.get('avgPrice') or p.get('entryPrice') or 0)
            time.sleep(1)
        return None, None

    def _cancel_open(self, sym):
        d = api('GET', '/openApi/swap/v2/trade/openOrders', {'symbol':sym})
        for o in (d.get('data', {}).get('orders') or []):
            oid = o.get('orderId')
            if oid: api('DELETE', '/openApi/swap/v2/trade/order',
                        {'symbol':sym,'orderId':str(oid)})

    def _place_sl(self, sym, qty, sl_price):
        d  = self._order(sym, 'SELL', qty, 'STOP_MARKET', stop_price=sl_price)
        ok = d.get('code') == 0
        if not ok:
            d  = self._order(sym, 'SELL', qty, 'STOP',
                             price=sl_price*0.999, stop_price=sl_price)
            ok = d.get('code') == 0
        log.info(f"  {'✅' if ok else '❌'} SL @ ${sl_price:.6f}")
        return ok

    def open_trade(self, sym, sig):
        if not AUTO or sym in self.trades: return False
        if DSAVWAPBot._opening or len(self.trades) >= MAX_TRADES: return False
        if self._has_any_position(sym):
            log.warning(f"  ⛔ {sym} ya tiene posición → omitiendo"); return False
        DSAVWAPBot._opening = True
        try: return self._open(sym, sig)
        finally: DSAVWAPBot._opening = False

    def _open(self, sym, sig):
        price    = sig['price']
        sl_price = sig['sl_price']
        label    = "REVERSAL" if sig['is_reversal'] else "CONT"

        log.info(f"\n  🎯 LONG [{label}] {sym} | Score:{sig['score']:.0f} | RR:{sig['rr']:.1f}:1")
        log.info(f"  VWAP:{sig['vwap']:.4f} | PH:{sig['ph']:.4f} | PL:{sig['pl']:.4f}")

        self._set_lev(sym); time.sleep(0.2)

        qty, notional = self._calc_qty(sym, price, sl_price)
        if not qty: return False

        limit_p = round(price * (1 - 0.05/100), 8)
        d = self._order(sym, 'BUY', qty, 'LIMIT', price=limit_p)
        if d.get('code') != 0:
            log.error(f"  ❌ LIMIT: {d.get('msg')}"); return False

        oid = d.get('data', {}).get('orderId')
        filled_qty, fill_price = self._wait_fill(sym, oid, 30)

        if not filled_qty:
            log.warning("  ⚠️ LIMIT sin fill → MARKET")
            self._cancel_open(sym); time.sleep(0.5)
            d = self._order(sym, 'BUY', qty, 'MARKET')
            if d.get('code') != 0: log.error(f"  ❌ MARKET: {d.get('msg')}"); return False
            filled_qty, fill_price = self._confirm_pos(sym, 12)
            if not filled_qty: return False

        sl_pct_real = (fill_price - sl_price) / fill_price * 100
        tp_price    = fill_price * (1 + sl_pct_real * TP_RR / 100)

        sl_ok = self._place_sl(sym, filled_qty, sl_price)
        if not sl_ok:
            time.sleep(2); sl_ok = self._place_sl(sym, filled_qty, sl_price)
        if not sl_ok:
            log.error("  ❌ SL crítico — cerrando")
            self._order(sym, 'SELL', filled_qty, 'MARKET'); return False

        self.trades[sym] = {
            'entry':     fill_price,    'qty':       filled_qty,
            'sl':        sl_price,      'sl_orig':   sl_price,
            'tp':        tp_price,      'highest':   fill_price,
            'opened':    datetime.now(),'score':     sig['score'],
            'vwap':      sig['vwap'],   'swing_low': sig['pl'],
            'sl_pct':    sl_pct_real,   'atr':       sig['atr'],
            'hora_utc':  sig['hora_utc'],
            'label':     label,
            'reasons':   sig['reasons'],
        }
        self.stats['exec']  += 1
        self.stats['fees']  += notional * FEE

        self._tg(
            f"<b>🟢 LONG [{label}]</b> — <b>{sym}</b>\n"
            f"Score: {sig['score']:.0f} | RR: {sig['rr']:.1f}:1\n\n"
            f"<b>📊 DSAVWAP:</b>\n"
            f"  VWAP Anclado: ${sig['vwap']:.6f}\n"
            f"  Swing High:   ${sig['ph']:.6f} (bar -{sig['ph_idx']})\n"
            f"  Swing Low:    ${sig['pl']:.6f} (bar -{sig['pl_idx']})\n"
            f"  {'🔄 Cambio dirección bear→bull' if sig['is_reversal'] else '▶️ Continuación alcista'}\n\n"
            f"📍 Entrada:  ${fill_price:.6f}\n"
            f"🎯 TP:       ${tp_price:.6f} (+{sl_pct_real*TP_RR:.2f}%)\n"
            f"🛑 SL:       ${sl_price:.6f} (-{sl_pct_real:.2f}%)\n"
            f"1H: {'🟢' if sig['trend_1h']==1 else '⚪'} | BTC: {self._btc_1h:+.2f}%\n"
            f"📝 {sig['reasons']}"
        )
        return True

    # ═══════════════════════════════════════════════════════════════
    # MONITOR
    # ═══════════════════════════════════════════════════════════════

    async def monitor(self):
        for sym in list(self.trades.keys()):
            try:
                t  = self.trades[sym]
                tk = self._ticker(sym)
                if not tk: continue
                cur = tk['price']
                pct = (cur - t['entry']) / t['entry'] * 100

                if cur > t['highest']: t['highest'] = cur

                # ── Actualizar VWAP y verificar dirección ─────────
                c5, h5, l5, v5, _ = self._klines(sym, '5m', 250)
                if c5 and len(c5) >= SWING_PERIOD * 3:
                    sig_live = dsavwap_signal(
                        c5, h5, l5, v5,
                        swing_period=SWING_PERIOD, base_apt=BASE_APT,
                        use_adapt=USE_ADAPT, vol_bias=VOL_BIAS, atr_len=ATR_LEN
                    )
                    t['vwap'] = sig_live['vwap']

                    # SALIDA: la dirección cambió a bearish = swing inverso
                    if sig_live['dir'] == -1 and sig_live['dir_changed']:
                        reason = "SWING_REVERSAL_BEAR"
                        log.warning(f"  🔄 {sym} swing invertido a BEAR | {pct:+.2f}%")
                        self._close(sym, cur, reason); continue

                    # Trailing SL: mover al nuevo swing low si es más alto
                    new_sl_candidate = sig_live['pl'] - sig_live['atr'] * SL_ATR_MULT
                    if new_sl_candidate > t['sl'] and new_sl_candidate < cur:
                        old_sl = t['sl']
                        t['sl'] = new_sl_candidate
                        log.info(f"  🔒 {sym} SL trailing: ${old_sl:.6f} → ${new_sl_candidate:.6f}")

                # ── TP ────────────────────────────────────────────
                if cur >= t['tp']:
                    self._close(sym, cur, "TAKE PROFIT"); continue

                # ── Break-even al 50% camino al TP ───────────────
                half_tp = t['entry'] + (t['tp'] - t['entry']) * 0.5
                if cur >= half_tp and t['sl'] < t['entry']:
                    be = t['entry'] * 1.001
                    t['sl'] = be
                    log.info(f"  🔒 {sym} SL → break-even ${be:.6f}")

                # ── SL ────────────────────────────────────────────
                if cur <= t['sl']:
                    self._close(sym, cur, "STOP LOSS")

            except Exception as e:
                log.debug(f"monitor {sym}: {e}")

    def _close(self, sym, exit_price, reason):
        if sym not in self.trades: return False
        t   = self.trades[sym]
        qty = t['qty']
        if qty > 0: self._order(sym, 'SELL', qty, 'MARKET')

        chg       = (exit_price - t['entry']) / t['entry']
        net       = POS_SIZE * LEVERAGE * chg - POS_SIZE * LEVERAGE * FEE * 2
        win       = net > 0
        self.stats['closed'] += 1
        self.stats['pnl']    += net
        self.stats['fees']   += POS_SIZE * LEVERAGE * FEE * 2
        self._daily_pnl      += net
        if win: self.stats['wins']   += 1
        else:   self.stats['losses'] += 1

        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        mins  = int((datetime.now() - t['opened']).total_seconds() / 60)
        emoji = "✅" if win else "❌"
        pct   = net / POS_SIZE * 100

        log.info(f"  {emoji} {reason} | {sym} | ${net:+.4f} ({pct:+.1f}%) | {mins}min | WR:{wr:.0f}%")
        self.learn.record(
            symbol=sym, score=t['score'], pnl=net, win=win,
            hora_utc=t.get('hora_utc', datetime.utcnow().hour), reason=reason
        )
        self._set_cd(sym, 'TP' if 'PROFIT' in reason or 'TP' in reason else 'SL')
        self._tg(
            f"<b>{emoji} CERRADO — {reason}</b>\n"
            f"<b>{sym}</b> | [{t.get('label','?')}] | {mins}min\n"
            f"${t['entry']:.6f} → ${exit_price:.6f}\n"
            f"<b>PnL: ${net:+.4f} ({pct:+.1f}%) | WR: {wr:.0f}%</b>"
        )
        if self.stats['closed'] % 3 == 0: self.learn.save()
        del self.trades[sym]
        return True

    # ═══════════════════════════════════════════════════════════════
    # UTILIDADES
    # ═══════════════════════════════════════════════════════════════

    def _cd_ok(self, sym):
        ts = self._cooldowns.get(sym)
        if not ts: return True
        resume, _ = ts if isinstance(ts, tuple) else (ts, 'TP')
        if time.time() >= resume: del self._cooldowns[sym]; return True
        return False

    def _set_cd(self, sym, reason='TP'):
        mins = CD_TP if reason == 'TP' else CD_SL
        self._cooldowns[sym] = (time.time() + mins * 60, reason)

    def _daily_reset(self):
        today = datetime.utcnow().date()
        if today != self._daily_date:
            self._daily_pnl  = 0.0; self._daily_date = today
            self._cb_active  = False; self._cb_until  = None
            self.learn.streak = 0; self._update_equity()
            log.info("📅 Nuevo día")

    def _circuit_check(self):
        self._daily_reset()
        if self._cb_active:
            if self._cb_until and datetime.utcnow() > self._cb_until:
                self._cb_active = False; self._daily_pnl = 0.0
                log.info("  🔓 Circuit breaker OFF")
                self._tg("<b>🔓 Circuit breaker OFF</b>")
            return self._cb_active
        if self._daily_pnl < -CB_USDT:
            self._cb_active = True
            self._cb_until  = datetime.utcnow() + timedelta(hours=CB_HOURS)
            log.warning(f"  🔒 CIRCUIT BREAKER | ${self._daily_pnl:.3f}")
            self._tg(f"<b>🔒 CIRCUIT BREAKER</b>\nPérdida: ${self._daily_pnl:.3f} | Pausa {CB_HOURS}h")
        return self._cb_active

    def _report(self):
        if datetime.now() - self._last_report < timedelta(hours=2): return
        self._last_report = datetime.now()
        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        pos   = ""
        for sym, t in self.trades.items():
            tk  = self._ticker(sym); cur = tk['price'] if tk else t['entry']
            pct = (cur - t['entry']) / t['entry'] * 100
            pos += f"  📌 {sym}[{t.get('label','?')}]: {pct:+.2f}%\n"
        self._tg(
            f"<b>📊 Reporte DSAVWAP</b>\n"
            f"PnL: ${self.stats['pnl']:+.4f} | WR: {wr:.0f}% | {total}t\n"
            f"Día: ${self._daily_pnl:+.4f} | Equity: ${ACCOUNT_EQUITY:.2f}\n"
            f"Score mín: {self.learn.opt_score:.0f} | BTC: {self._btc_1h:+.2f}%\n"
            + (pos if pos else "  Sin posiciones\n")
        )

    def _tg(self, msg):
        try:
            if TG_TOKEN and TG_CHAT:
                requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                              json={'chat_id':TG_CHAT,'text':msg,'parse_mode':'HTML'}, timeout=6)
        except: pass

    # ═══════════════════════════════════════════════════════════════
    # LOOP PRINCIPAL
    # ═══════════════════════════════════════════════════════════════

    async def run(self):
        log.info("\n🚀 Bot DSAVWAP — Dynamic Swing Anchored VWAP\n")
        iteration = 0
        last_sym = last_ltv = last_hedge = last_eq = 0

        while True:
            try:
                iteration += 1; self._daily_reset()
                if time.time() - last_sym   > 600:  self._refresh_symbols();    last_sym   = time.time()
                if time.time() - last_hedge > 600:  self._scan_orphan_shorts(); last_hedge = time.time()
                if time.time() - last_eq    > 1800: self._update_equity();      last_eq    = time.time()

                self._update_btc()
                if self._circuit_check():
                    await asyncio.sleep(INTERVAL); continue

                total = self.stats['wins'] + self.stats['losses']
                wr    = self.stats['wins'] / total * 100 if total else 0
                log.info(f"\n{'='*70}")
                log.info(f"  #{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                         f"Abiertos:{len(self.trades)}/{MAX_TRADES} | "
                         f"PnL:${self.stats['pnl']:+.4f} | WR:{wr:.0f}%")
                log.info(f"  BTC:{self._btc_1h:+.2f}% | Equity:${ACCOUNT_EQUITY:.2f} | "
                         f"Score mín:{self.learn.opt_score:.0f}")
                log.info(f"{'='*70}\n")

                await self.monitor()
                self._report()

                if len(self.trades) < MAX_TRADES:
                    log.info(f"  Escaneando {len(self.symbols)} símbolos...")
                    found = 0
                    for i, sym in enumerate(self.symbols):
                        if len(self.trades) >= MAX_TRADES: break
                        sig = self.analyze(sym)
                        if sig:
                            found += 1
                            log.info(
                                f"  💡 {sym} [{sig['señal']}] | "
                                f"Score:{sig['score']:.0f} | RR:{sig['rr']:.1f}:1 | "
                                f"VWAP:{sig['vwap']:.4f}"
                            )
                            if self.open_trade(sym, sig):
                                await asyncio.sleep(3)
                        if (i + 1) % 15 == 0:
                            log.info(f"  ...{i+1}/{len(self.symbols)}")
                        await asyncio.sleep(0.12)
                    log.info(f"  ✅ Scan: {found} señales")
                else:
                    log.info("  ⏸️ Max trades — monitoreando")

                await asyncio.sleep(INTERVAL)

            except KeyboardInterrupt: log.info("⏹️ Detenido"); break
            except Exception as e:
                log.error(f"❌ Error #{iteration}: {e}", exc_info=True)
                await asyncio.sleep(20)

        self.learn.save()


# ============================================================================
# ENTRY POINT
# ============================================================================

async def main():
    bot = DSAVWAPBot()
    await bot.run()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: log.info("👋 Bot terminado")
