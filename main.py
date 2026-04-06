#!/usr/bin/env python3
"""
BOT LONGS v5.3 — TPs Escalonados + EMA25 Trailing + Aprendizaje Profundo
══════════════════════════════════════════════════════════════════════════
Inspirado en el chart de Aurolo: múltiples TPs, EMA como trailing dinámico
y sistema de aprendizaje que mejora continuamente con cada trade.

NUEVO en v5.3:
  v5.3-A  TPs ESCALONADOS: cierra 40% en TP1, 35% en TP2, deja correr el 25%
  v5.3-B  EMA25 TRAILING: el 25% restante corre hasta que EMA25 lo cierra
  v5.3-C  SL sube a break-even automáticamente al alcanzar TP1
  v5.3-D  APRENDIZAJE PROFUNDO: aprende de cada motivo de cierre, por hora,
          por tipo de entrada (A/B), por condición de mercado BTC
  v5.3-E  MEJORA CONTINUA: ajusta pesos del score según qué factores
          realmente predicen wins vs losses en el histórico real
  v5.3-F  REPORTE DE MEJORA: cada 10 trades, envía por TG qué está aprendiendo

HEREDADO de v5.2:
  Estrategia VWAP Breakout + Retesteo (Whale Analytics)
  SL bajo el VWAP (nivel institucional)
  Regla del 1% para tamaño de posición
  Filtro 1H alcista obligatorio
  Entradas Tipo A (Ruptura) y Tipo B (Retesteo)

HEREDADO de v5.1:
  FIX-HEDGE-01/02/03/04: Sin doble dirección por símbolo
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re, json
from datetime import datetime, timedelta
from urllib.parse import urlencode
from collections import defaultdict

# ============================================================================
# CONFIG
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

API_KEY    = os.getenv('BINGX_API_KEY',    '').strip().strip('"').strip("'")
API_SECRET = os.getenv('BINGX_API_SECRET', '').strip().strip('"').strip("'")
TG_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '')
TG_CHAT    = os.getenv('TELEGRAM_CHAT_ID',   '')

# ── Capital ────────────────────────────────────────────────────────────────
AUTO           = clean('AUTO_TRADING_ENABLED', 'true',  'bool')
POS_SIZE       = clean('MAX_POSITION_SIZE',    '10',    'float')
MIN_TRADE      = clean('MIN_TRADE_USDT',       '10',    'float')
_lev           = clean('LEVERAGE',             '2',     'int')
LEVERAGE       = min(_lev, 3)
MAX_TRADES     = clean('MAX_OPEN_TRADES',      '3',     'int')
RISK_PCT       = clean('RISK_PCT',             '1.0',   'float')
ACCOUNT_EQUITY = clean('ACCOUNT_EQUITY',       '100',   'float')

# ── TPs Escalonados (v5.3-A) ──────────────────────────────────────────────
# Porcentaje de la posición que se cierra en cada TP
TP1_PCT   = clean('TP1_PCT',   '40',  'float')   # 40% de la qty en TP1
TP2_PCT   = clean('TP2_PCT',   '35',  'float')   # 35% en TP2
# El 25% restante lo gestiona la EMA25 (v5.3-B)
TP1_RATIO = clean('TP1_RATIO', '1.0', 'float')   # TP1 = 1× el SL (1:1 RR)
TP2_RATIO = clean('TP2_RATIO', '2.0', 'float')   # TP2 = 2× el SL (2:1 RR)
# TP3 (runner 25%) lo cierra la EMA25 sin límite de objetivo

# ── TP/SL base ─────────────────────────────────────────────────────────────
TP_MIN         = clean('TAKE_PROFIT_PCT',  '2.0',  'float')
SL_VWAP_MARGIN = clean('SL_VWAP_MARGIN',  '0.15', 'float')
ATR_TP_M       = clean('ATR_TP_MULT',     '2.5',  'float')
MIN_RR         = clean('MIN_RR',          '2.0',  'float')

# ── Filtros ────────────────────────────────────────────────────────────────
MIN_VOL    = clean('MIN_VOLUME_24H',      '500000', 'float')
MAX_SYMS   = clean('MAX_SYMBOLS',         '60',     'int')
MIN_SCORE  = clean('MIN_SCORE',           '50',     'float')
BTC_BLOCK  = clean('BTC_BEAR_BLOCK_PCT',  '2.0',    'float')

# ── VWAP ───────────────────────────────────────────────────────────────────
VWAP_FLAT_PCT      = clean('VWAP_FLAT_PCT',      '0.15', 'float')
VWAP_BREAK_MIN_PCT = clean('VWAP_BREAK_MIN_PCT', '0.10', 'float')
VWAP_RETEST_PCT    = clean('VWAP_RETEST_PCT',    '0.30', 'float')
VWAP_CANDLES       = clean('VWAP_CANDLES',        '50',   'int')
VWAP_SLOPE_CANDLES = clean('VWAP_SLOPE_CANDLES',  '20',   'int')

# ── Circuit breaker ────────────────────────────────────────────────────────
CB_USDT    = clean('CIRCUIT_BREAKER_USDT', '3.0', 'float')
CB_HOURS   = clean('CB_PAUSE_HOURS',       '2',   'int')
MAX_STREAK = clean('MAX_LOSING_STREAK',    '4',   'int')

# ── Cooldowns ─────────────────────────────────────────────────────────────
CD_TP  = clean('COOLDOWN_TP_MIN', '5',  'int')
CD_SL  = clean('COOLDOWN_SL_MIN', '60', 'int')

# ── Misc ───────────────────────────────────────────────────────────────────
INTERVAL   = clean('CHECK_INTERVAL', '120', 'int')
LTV_WARN   = clean('LTV_WARNING_PCT', '80', 'float')
SKIP_HOURS = {2, 3}
BASE_URL   = "https://open-api.bingx.com"
FEE        = 0.0002
TP_MIN_FEE = round((2 * FEE * LEVERAGE + 0.003) * 100, 3)

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

# ============================================================================
# SETUP VWAP
# ============================================================================

def analizar_setup_vwap(closes, highs, lows, volumes, opens):
    result = {'tipo': None, 'vwap': 0, 'sl_price': 0,
              'calidad': 0, 'slope': 0, 'vol_ratio': 1, 'descripcion': ''}

    if len(closes) < VWAP_CANDLES + 15: return result

    vwap_val = calc_vwap(closes, highs, lows, volumes, VWAP_CANDLES)
    slope    = vwap_slope_pct(closes, highs, lows, volumes, VWAP_SLOPE_CANDLES)
    price    = closes[-1]
    result['vwap'] = vwap_val; result['slope'] = slope

    if abs(slope) < VWAP_FLAT_PCT:
        result['descripcion'] = f'VWAP plano (slope={slope:.3f}%) — manos quietas'
        return result
    if slope <= 0:
        result['descripcion'] = f'VWAP bajista — no operar longs'
        return result

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
                       'descripcion': f'RUPTURA | slope={slope:.2f}% | vol={vol_ratio:.1f}x'})
        return result

    # TIPO B: Retesteo (3 fases)
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
        if slope > 0.3:       calidad += 10
        if pct_sobre_vwap < 0.5: calidad += 10
        if vol_ratio >= 1.3:  calidad += 5
        if slope > 0.6:       calidad += 5
        result.update({'tipo': 'B', 'calidad': min(calidad, 100),
                       'descripcion': f'RETESTEO | slope={slope:.2f}% | vol={vol_ratio:.1f}x'})
        return result

    result['descripcion'] = 'Sin setup VWAP'
    return result


def calcular_qty_por_riesgo(precio_entrada, sl_price, capital_usdt, riesgo_pct):
    if sl_price >= precio_entrada: return 0, 0
    riesgo_usdt   = capital_usdt * (riesgo_pct / 100)
    distancia_pct = (precio_entrada - sl_price) / precio_entrada * 100
    qty_usdt      = riesgo_usdt / (distancia_pct / 100)
    return qty_usdt, distancia_pct


# ============================================================================
# APRENDIZAJE PROFUNDO v5.3
# ============================================================================

class Learning:
    """
    Sistema de mejora continua que aprende de cada trade real:

    - Por símbolo: win rate, PnL acumulado → blacklist si consistentemente malo
    - Por hora UTC: ¿a qué horas funciona mejor la estrategia?
    - Por tipo entrada (A/B): ¿ruptura o retesteo genera más profit?
    - Por contexto BTC: ¿funciona mejor con BTC alcista/bajista/neutral?
    - Por motivo de cierre: TP1/TP2/EMA25/SL → ¿cuál maximiza PnL?
    - Pesos del score: qué factores (vwap_quality, vol, rsi_1h…)
      realmente correlacionan con ganar → ajusta el score mínimo
    - Cada 10 trades: reporte Telegram de lo que aprendió
    """

    def __init__(self):
        self.history   = []          # Todos los trades
        self.sym_stats = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0,'n':0})
        self.opt_score = MIN_SCORE
        self.blacklist = set()
        self.streak    = 0
        self.last10    = []

        # Análisis por dimensiones (v5.3-D)
        self.by_hour    = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0})
        self.by_tipo    = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0})  # A / B
        self.by_btc     = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0})  # up/flat/down
        self.by_reason  = defaultdict(lambda: {'n':0,'pnl':0.0})        # TP1/TP2/EMA25/SL

        # Pesos de factores del score (v5.3-E)
        # Se ajustan si un factor aparece más en wins o en losses
        self.factor_wins   = defaultdict(int)  # factor → nº veces en wins
        self.factor_losses = defaultdict(int)  # factor → nº veces en losses
        self.score_boost   = {}                # factor → bonus/malus aprendido

    # ── Registrar un trade cerrado ────────────────────────────────────────

    def record(self, symbol, score, pnl, win, hora_utc=None,
               tipo_entrada='?', btc_dir='flat', reason='?', factors=None):
        rec = {
            'ts':    datetime.now().isoformat(),
            'sym':   symbol,
            'score': score,
            'pnl':   pnl,
            'win':   win,
            'hora':  hora_utc or datetime.utcnow().hour,
            'tipo':  tipo_entrada,
            'btc':   btc_dir,
            'reason': reason,
            'factors': factors or [],
        }
        self.history.append(rec)
        self.last10.append(rec)
        if len(self.last10) > 10: self.last10.pop(0)

        # Stats por símbolo
        s = self.sym_stats[symbol]
        s['n'] += 1; s['pnl'] += pnl
        if win: s['w'] += 1; self.streak = 0
        else:   s['l'] += 1; self.streak += 1

        # Stats por dimensión
        self.by_hour[rec['hora']]['pnl']  += pnl
        self.by_tipo[tipo_entrada]['pnl'] += pnl
        self.by_btc[btc_dir]['pnl']       += pnl
        self.by_reason[reason]['n']       += 1
        self.by_reason[reason]['pnl']     += pnl

        k = 'w' if win else 'l'
        self.by_hour[rec['hora']][k]  += 1
        self.by_tipo[tipo_entrada][k] += 1
        self.by_btc[btc_dir][k]       += 1

        # Factores del score
        for f in (factors or []):
            if win: self.factor_wins[f]   += 1
            else:   self.factor_losses[f] += 1

        self._adjust()

        # Reporte de mejora cada 10 trades (v5.3-F)
        if len(self.history) % 10 == 0:
            self._reporte_aprendizaje()

    # ── Ajuste automático (v5.3-E) ────────────────────────────────────────

    def _adjust(self):
        n = len(self.history)

        # Score mínimo según WR reciente
        if n >= 10:
            wr = sum(1 for t in self.last10 if t['win']) / len(self.last10)
            if wr < 0.35:
                self.opt_score = min(self.opt_score + 5, 85)
                log.warning(f"  [LEARN] WR muy bajo {wr:.0%} → score +5 → {self.opt_score}")
            elif wr < 0.45:
                self.opt_score = min(self.opt_score + 2, 85)
            elif wr > 0.65:
                self.opt_score = max(self.opt_score - 2, MIN_SCORE)
            elif wr > 0.75:
                self.opt_score = max(self.opt_score - 4, MIN_SCORE)

        # Blacklist por símbolo
        for sym, s in self.sym_stats.items():
            tot = s['w'] + s['l']
            if tot >= 5 and s['pnl'] < -2.0 and s['w'] / tot < 0.20:
                if sym not in self.blacklist:
                    self.blacklist.add(sym)
                    log.warning(f"  [LEARN] 🚫 {sym} → blacklist (WR:{s['w']/tot:.0%} PnL:${s['pnl']:.2f})")

        # Pesos de factores: si un factor aparece 3+ veces más en losses → malus
        if n >= 20:
            for factor in set(list(self.factor_wins.keys()) + list(self.factor_losses.keys())):
                w = self.factor_wins.get(factor, 0)
                l = self.factor_losses.get(factor, 0)
                total_f = w + l
                if total_f < 5: continue
                wr_f = w / total_f
                if wr_f < 0.30:
                    self.score_boost[factor] = -8   # Factor malo → penalizar
                elif wr_f > 0.70:
                    self.score_boost[factor] = +5   # Factor bueno → bonificar
                else:
                    self.score_boost.pop(factor, None)

    # ── Bloquear horas malas ──────────────────────────────────────────────

    def hora_ok(self, hora_utc) -> tuple:
        """Retorna (ok, motivo). Bloquea horas con >5 trades y WR<25%."""
        h = self.by_hour.get(hora_utc)
        if not h: return True, "ok"
        tot = h['w'] + h['l']
        if tot < 5: return True, "ok"
        wr = h['w'] / tot
        if wr < 0.25:
            return False, f"hora {hora_utc}h WR={wr:.0%} ({tot} trades)"
        return True, "ok"

    # ── Bonus de tipo de entrada aprendido ────────────────────────────────

    def bonus_tipo(self, tipo) -> int:
        """Retorna bonus/malus según rendimiento histórico del tipo A o B."""
        h = self.by_tipo.get(tipo)
        if not h: return 0
        tot = h['w'] + h['l']
        if tot < 5: return 0
        wr = h['w'] / tot
        if wr > 0.65:  return +10
        if wr < 0.35:  return -10
        return 0

    def ok(self, sym, score):
        if sym in self.blacklist:     return False, "blacklist"
        if score < self.opt_score:    return False, f"score {score:.0f}<{self.opt_score:.0f}"
        if self.streak >= MAX_STREAK: return False, f"streak -{self.streak}"
        return True, "ok"

    def factor_score_adj(self, factors: list) -> int:
        """Suma los ajustes aprendidos para los factores de este setup."""
        return sum(self.score_boost.get(f, 0) for f in factors)

    # ── Reporte de aprendizaje (v5.3-F) ──────────────────────────────────

    def _reporte_aprendizaje(self):
        n = len(self.history)
        total = sum(1 for t in self.history if t['win'])
        wr    = total / n * 100 if n else 0
        pnl   = sum(t['pnl'] for t in self.history)

        # Mejor y peor hora
        horas_ok = [(h, d) for h, d in self.by_hour.items()
                    if d['w']+d['l'] >= 3]
        mejor_h = max(horas_ok, key=lambda x: x[1]['pnl'], default=(None, {}))
        peor_h  = min(horas_ok, key=lambda x: x[1]['pnl'], default=(None, {}))

        # Tipo A vs B
        tipo_txt = ""
        for t, d in self.by_tipo.items():
            tot = d['w'] + d['l']
            if tot > 0:
                tipo_txt += f"  Tipo {t}: WR={d['w']/tot:.0%} | PnL:${d['pnl']:.2f} ({tot} trades)\n"

        # Motivos de cierre
        reason_txt = ""
        for r, d in sorted(self.by_reason.items(), key=lambda x: x[1]['pnl'], reverse=True):
            reason_txt += f"  {r}: ${d['pnl']:+.2f} ({d['n']} veces)\n"

        # Factores aprendidos
        boost_txt = ""
        for f, b in sorted(self.score_boost.items(), key=lambda x: x[1]):
            boost_txt += f"  {f}: {b:+d}pts\n"

        msg = (
            f"<b>🧠 APRENDIZAJE — {n} trades</b>\n"
            f"WR: {wr:.0f}% | PnL total: ${pnl:+.4f}\n"
            f"Score mín actual: {self.opt_score:.0f}\n"
            f"Blacklist: {len(self.blacklist)} símbolos\n\n"
            f"<b>📊 Por tipo de entrada:</b>\n{tipo_txt or '  Sin datos\n'}"
            f"<b>🕐 Mejor hora:</b> {mejor_h[0]}h UTC (${mejor_h[1].get('pnl',0):+.2f})\n"
            f"<b>🕐 Peor hora:</b> {peor_h[0]}h UTC (${peor_h[1].get('pnl',0):+.2f})\n\n"
            f"<b>🚪 Cierres más rentables:</b>\n{reason_txt or '  Sin datos\n'}"
            + (f"<b>⚖️ Ajustes de factores:</b>\n{boost_txt}" if boost_txt else "")
        )
        log.info(f"\n[LEARN] Reporte #{n//10}:\n{msg.replace('<b>','').replace('</b>','')}")
        try:
            if TG_TOKEN and TG_CHAT:
                requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                    timeout=6)
        except: pass

    def save(self, fp='/tmp/bot_learn_v53.json'):
        try:
            json.dump({
                'history':      self.history[-200:],
                'sym_stats':    dict(self.sym_stats),
                'opt_score':    self.opt_score,
                'blacklist':    list(self.blacklist),
                'by_hour':      dict(self.by_hour),
                'by_tipo':      dict(self.by_tipo),
                'by_btc':       dict(self.by_btc),
                'by_reason':    dict(self.by_reason),
                'factor_wins':  dict(self.factor_wins),
                'factor_losses':dict(self.factor_losses),
                'score_boost':  self.score_boost,
            }, open(fp, 'w'), indent=2)
        except: pass

    def load(self, fp='/tmp/bot_learn_v53.json'):
        try:
            if not os.path.exists(fp):
                # Intentar migrar desde v5.2
                fp_old = '/tmp/bot_learn.json'
                if os.path.exists(fp_old):
                    fp = fp_old
                    log.info("  [LEARN] Migrando historial desde v5.2")
            if os.path.exists(fp):
                d = json.load(open(fp))
                self.history    = d.get('history', [])
                self.sym_stats  = defaultdict(
                    lambda: {'w':0,'l':0,'pnl':0.0,'n':0}, d.get('sym_stats', {}))
                self.opt_score  = d.get('opt_score', MIN_SCORE)
                self.blacklist  = set(d.get('blacklist', []))
                self.by_hour    = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0},
                                              d.get('by_hour', {}))
                self.by_tipo    = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0},
                                              d.get('by_tipo', {}))
                self.by_btc     = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0},
                                              d.get('by_btc', {}))
                self.by_reason  = defaultdict(lambda: {'n':0,'pnl':0.0},
                                              d.get('by_reason', {}))
                self.factor_wins   = defaultdict(int, d.get('factor_wins', {}))
                self.factor_losses = defaultdict(int, d.get('factor_losses', {}))
                self.score_boost   = d.get('score_boost', {})
                log.info(f"  [LEARN] {len(self.history)} trades cargados | "
                         f"Score mín: {self.opt_score:.0f} | "
                         f"Blacklist: {len(self.blacklist)}")
        except Exception as e:
            log.warning(f"  [LEARN] No se pudo cargar historial: {e}")


# ============================================================================
# BOT PRINCIPAL v5.3
# ============================================================================

class LongBot:
    _opening = False

    def __init__(self):
        log.info("=" * 72)
        log.info("  BOT LONGS v5.3 — TPs Escalonados + EMA25 Trailing + Deep Learning")
        log.info(f"  Capital: ${POS_SIZE} | Riesgo: {RISK_PCT}%/trade | {LEVERAGE}x")
        log.info(f"  TPs: {TP1_PCT:.0f}% en TP1 ({TP1_RATIO:.1f}×SL) | "
                 f"{TP2_PCT:.0f}% en TP2 ({TP2_RATIO:.1f}×SL) | "
                 f"{100-TP1_PCT-TP2_PCT:.0f}% runner EMA25")
        log.info(f"  VWAP plano: <{VWAP_FLAT_PCT}% | Break: {VWAP_BREAK_MIN_PCT}%")
        log.info(f"  Aprendizaje: por hora, tipo, BTC, motivo de cierre ✅")
        log.info("=" * 72)

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
            f"<b>🤖 Bot LONGS v5.3</b>\n"
            f"TPs: {TP1_PCT:.0f}%→TP1 | {TP2_PCT:.0f}%→TP2 | {100-TP1_PCT-TP2_PCT:.0f}%→EMA25\n"
            f"Riesgo: {RISK_PCT}% cuenta | {LEVERAGE}x | VWAP Strategy\n"
            f"🧠 Aprendizaje profundo activado\n"
            f"Posiciones recuperadas: {len(self.trades)}"
        )

    # ════════════════════════════════════════════════════════════════
    # SETUP
    # ════════════════════════════════════════════════════════════════

    def _connect(self) -> bool:
        global AUTO, ACCOUNT_EQUITY
        if not AUTO: return True
        if not API_KEY or not API_SECRET:
            log.error("❌ API keys no configuradas"); AUTO = False; return False
        d = api('GET', '/openApi/swap/v2/user/balance')
        if d.get('code') == 0:
            b  = d.get('data', {})
            eq = float(b.get('equity', b.get('balance', 0)) or 0)
            if eq > 0: ACCOUNT_EQUITY = eq
            log.info(f"✅ BingX conectado | ${ACCOUNT_EQUITY:.2f} USDT")
            return True
        log.error(f"❌ [{d.get('code')}]: {d.get('msg')}")
        AUTO = False; return False

    def _detect_mode(self):
        try:
            d = api('GET', '/openApi/swap/v2/user/positions', {'symbol': 'BTC-USDT'})
            for p in (d.get('data') or []):
                side = str(p.get('positionSide', '')).upper()
                if side in ('LONG', 'SHORT'): self._mode = 'hedge'; log.info("  Modo: HEDGE"); return
                if side == 'BOTH': self._mode = 'oneway'; log.info("  Modo: ONE-WAY"); return
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
            log.info(f"  Contratos: {len(self._contracts)}")

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
        log.info(f"  Símbolos: {len(self.symbols)}")

    # ════════════════════════════════════════════════════════════════
    # FIX-HEDGE (v5.1 heredado)
    # ════════════════════════════════════════════════════════════════

    def _get_exchange_positions(self, symbol=None):
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
                if side == 'LONG'  or (side == 'BOTH' and amt > 0): result[sym]['long']  = abs(amt)
                elif side == 'SHORT' or (side == 'BOTH' and amt < 0): result[sym]['short'] = abs(amt)
            except: continue
        return result

    def _has_any_position(self, symbol) -> bool:
        pos = self._get_exchange_positions(symbol)
        return pos[symbol]['long'] > 0 or pos[symbol]['short'] > 0

    def _order_close_short(self, sym, qty):
        params = {'symbol': sym, 'side': 'BUY', 'type': 'MARKET', 'quantity': str(qty)}
        if self._mode == 'hedge': params['positionSide'] = 'SHORT'
        else: params['reduceOnly'] = 'true'
        return api('POST', '/openApi/swap/v2/trade/order', params)

    def _recover(self):
        if not AUTO: return
        all_pos = self._get_exchange_positions()
        n_rec = 0; n_short = 0
        for sym, sides in all_pos.items():
            if sides['short'] > 0:
                log.warning(f"  ⚠️  SHORT huérfano: {sym} → cerrando")
                if self._order_close_short(sym, sides['short']).get('code') == 0:
                    n_short += 1; self._tg(f"<b>🔧 SHORT huérfano cerrado</b>\n{sym}")
                time.sleep(0.5)
            if sides['long'] > 0 and sym not in self.trades:
                d2 = api('GET', '/openApi/swap/v2/user/positions', {'symbol': sym})
                entry = 0.0
                for p in (d2.get('data') or []):
                    side = str(p.get('positionSide','')).upper()
                    amt  = float(p.get('positionAmt',0) or 0)
                    if (side=='LONG' and abs(amt)>0) or (side=='BOTH' and amt>0):
                        entry = float(p.get('avgPrice') or p.get('entryPrice') or 0); break
                if entry <= 0: continue
                qty_total = sides['long']
                # Al recuperar, asumimos que todos los TPs están pendientes
                self.trades[sym] = self._nuevo_trade(sym, entry, entry, qty_total, qty_total,
                                                     entry*(1-0.3/100), entry*(1+TP_MIN/100),
                                                     TP_MIN, 0.3, 0, '?', entry)
                n_rec += 1; log.info(f"  ♻️  LONG recuperado: {sym} @ ${entry:.6f}")
        log.info(f"  Recuperadas: {n_rec} | SHORTs cerrados: {n_short}")

    def _scan_orphan_shorts(self):
        if not AUTO: return
        for sym, sides in self._get_exchange_positions().items():
            if sides['short'] > 0:
                self._order_close_short(sym, sides['short'])
                time.sleep(0.3)

    def _nuevo_trade(self, sym, fill_price, sl, tp1, tp2_tp3_qty, qty_total,
                     tp1_price, tp2_price, tp_pct, sl_pct, score, tipo, ema25_val):
        """Estructura de trade con soporte de TPs escalonados."""
        return {
            'entry':        fill_price,
            'qty_total':    qty_total,             # Qty original completa
            'qty_runner':   qty_total,              # Qty restante (se reduce en TPs parciales)
            'qty_tp1':      round(qty_total * TP1_PCT / 100, 6),
            'qty_tp2':      round(qty_total * TP2_PCT / 100, 6),
            'tp1_hit':      False,                  # ¿Ya cerró el 40%?
            'tp2_hit':      False,                  # ¿Ya cerró el 35%?
            'tp1_price':    tp1_price,
            'tp2_price':    tp2_price,
            'sl':           sl,
            'sl_vwap':      sl,
            'tp_pct':       tp_pct,
            'sl_pct':       sl_pct,
            'highest':      fill_price,
            'opened':       datetime.now(),
            'score':        score,
            'ema25':        ema25_val,
            'vwap':         sl / (1 - SL_VWAP_MARGIN / 100),
            'entrada_tipo': tipo,
            'usdt':         POS_SIZE,
            'pnl_parcial':  0.0,    # PnL ya realizado en TP1/TP2
        }

    # ════════════════════════════════════════════════════════════════
    # MERCADO
    # ════════════════════════════════════════════════════════════════

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

    def _btc_dir(self) -> str:
        if self._btc_1h > 0.5:   return 'up'
        if self._btc_1h < -0.5:  return 'down'
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
                self._tg(f"<b>⚠️ LTV ALTO</b> — cerrando posiciones")
                for sym in list(self.trades.keys()):
                    tk = self._ticker(sym)
                    if tk: self._close_all(sym, tk['price'], "LTV EMERGENCIA")
        except: pass

    # ════════════════════════════════════════════════════════════════
    # ANÁLISIS
    # ════════════════════════════════════════════════════════════════

    def analyze(self, symbol):
        if symbol in self.trades: return None
        if not self._cd_ok(symbol): return None
        hora = datetime.utcnow().hour
        if hora in SKIP_HOURS: return None
        if not self._btc_ok: return None
        if self._cb_active: return None

        # Bloquear horas malas aprendidas
        hora_ok, motivo_h = self.learn.hora_ok(hora)
        if not hora_ok:
            log.debug(f"  ⏰ {symbol} bloqueado: {motivo_h}")
            return None

        c5, h5, l5, v5, o5 = self._klines(symbol, '5m', 120)
        if not c5 or len(c5) < 70: return None

        c1h, h1h, l1h, v1h, _ = self._klines(symbol, '1h', 50)
        tk = self._ticker(symbol)
        if not tk or tk['price'] <= 0: return None
        price = tk['price']; change_24 = tk['change']

        # 1H: filtro obligatorio
        trend_1h = 0; rsi_1h = 50.0
        if c1h and len(c1h) >= 25:
            e9_1h  = ema(c1h, 9); e21_1h = ema(c1h, 21)
            rsi_1h = rsi(c1h, 14)
            vwap_1h = calc_vwap(c1h, h1h, l1h, v1h, 30)
            if e9_1h > e21_1h and c1h[-1] > vwap_1h:   trend_1h = 1
            elif e9_1h < e21_1h and c1h[-1] < vwap_1h: trend_1h = -1
        if trend_1h == -1: return None

        e25   = ema(c5, 25); e9_5m = ema(c5, 9)
        rsi_v = rsi(c5, 14)
        atr_v = atr_calc(h5, l5, c5, 14)
        atr_pct = atr_v / price * 100 if price > 0 else 0

        setup = analizar_setup_vwap(c5, h5, l5, v5, o5)
        if not setup['tipo']: return None

        vwap_val = setup['vwap']
        sl_price = setup['sl_price']
        sl_pct   = (price - sl_price) / price * 100

        # TPs escalonados basados en la distancia al SL
        tp1_price = price * (1 + sl_pct * TP1_RATIO / 100)
        tp2_price = price * (1 + sl_pct * TP2_RATIO / 100)
        # TP runner: sin objetivo fijo, lo cierra la EMA25

        tp_pct_ref = sl_pct * MIN_RR
        tp_pct_ref = max(tp_pct_ref, TP_MIN, TP_MIN_FEE, atr_pct * ATR_TP_M)
        rr         = tp_pct_ref / sl_pct if sl_pct > 0 else 0

        if rr < MIN_RR * 0.8:       return None
        if atr_pct < 0.15:          return None
        if rsi_v > 75:              return None
        if change_24 > 20.0:        return None
        if price < e25 * 0.99:      return None

        # ── SCORING con factores etiquetados (para aprendizaje) ──────
        score = 0; reasons = []; factors = []

        cal = setup['calidad']
        score += int(cal * 0.5)
        tipo_label = "RUPTURA" if setup['tipo'] == 'A' else "RETESTEO"
        reasons.append(f"{tipo_label}({cal:.0f}%)"); factors.append(f"vwap_{setup['tipo']}")

        # Bonus aprendido por tipo
        bonus_t = self.learn.bonus_tipo(setup['tipo'])
        if bonus_t != 0:
            score += bonus_t
            reasons.append(f"Learn{setup['tipo']}({bonus_t:+d})")
            factors.append(f"learn_tipo_{setup['tipo']}")

        if trend_1h == 1:
            score += 20; reasons.append("1H↑(20)"); factors.append("trend_1h_up")

        if price > vwap_val and price > e25:
            score += 15; reasons.append("PrecioOK(15)"); factors.append("precio_ok")
        elif price > vwap_val:
            score += 8;  reasons.append("OverVWAP(8)"); factors.append("over_vwap")

        if e9_5m > e25:
            score += 12; reasons.append("EMA↑(12)"); factors.append("ema_alineada")

        if 40 <= rsi_v <= 60:
            score += 15; reasons.append(f"RSI{rsi_v:.0f}(15)"); factors.append("rsi_neutral")
        elif rsi_v < 40:
            score += 10; reasons.append(f"RSIsob{rsi_v:.0f}(10)"); factors.append("rsi_oversold")
        elif rsi_v < 70:
            score += 5;  reasons.append(f"RSI{rsi_v:.0f}(5)"); factors.append("rsi_ok")

        vr = setup['vol_ratio']
        if vr >= 2.0:   score += 12; reasons.append(f"Vol{vr:.1f}x(12)"); factors.append("vol_fuerte")
        elif vr >= 1.4: score += 7;  reasons.append(f"Vol{vr:.1f}x(7)");  factors.append("vol_medio")

        if self._btc_1h > 1.0:   score += 8; reasons.append(f"BTC↑(8)"); factors.append("btc_up")
        elif self._btc_1h > 0.3: score += 4; reasons.append(f"BTC~(4)"); factors.append("btc_neutral")

        if rsi_1h < 40:   score += 10; reasons.append(f"RSI1H{rsi_1h:.0f}(10)"); factors.append("rsi_1h_oversold")
        elif rsi_1h < 55: score += 5;  reasons.append(f"RSI1H{rsi_1h:.0f}(5)");  factors.append("rsi_1h_ok")

        if setup['tipo'] == 'B':
            score += 10; reasons.append("Retest+10"); factors.append("retest_bonus")

        # Ajuste por factores aprendidos
        adj = self.learn.factor_score_adj(factors)
        if adj != 0:
            score += adj
            reasons.append(f"FactorAdj({adj:+d})")

        ok, reason = self.learn.ok(symbol, score)
        if not ok: return None

        if score >= self.learn.opt_score:
            return {
                'price':       price,
                'change':      change_24,
                'score':       score,
                'score_min':   self.learn.opt_score,
                'rsi':         rsi_v,
                'rsi_1h':      rsi_1h,
                'vol_ratio':   vr,
                'atr_pct':     atr_pct,
                'tp1_price':   round(tp1_price, 8),
                'tp2_price':   round(tp2_price, 8),
                'tp_pct':      round(tp_pct_ref, 2),
                'sl_pct':      round(sl_pct, 2),
                'sl_price':    round(sl_price, 8),
                'rr':          round(rr, 2),
                'vwap':        vwap_val,
                'slope':       setup['slope'],
                'entrada_tipo': setup['tipo'],
                'calidad':     cal,
                'ema25':       e25,
                'trend_1h':    trend_1h,
                'reasons':     ' | '.join(reasons),
                'factors':     factors,
                'hora_utc':    hora,
                'btc_dir':     self._btc_dir(),
            }
        return None

    # ════════════════════════════════════════════════════════════════
    # GESTIÓN POSICIONES
    # ════════════════════════════════════════════════════════════════

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
        """FIX-HEDGE-04: SELL siempre positionSide=LONG (cierra LONG, no abre SHORT)."""
        params = {'symbol': sym, 'side': side.upper(),
                  'type': otype, 'quantity': str(qty)}
        if self._mode == 'hedge':
            params['positionSide'] = 'LONG'
        else:
            if side.upper() == 'SELL': params['reduceOnly'] = 'true'
        if price:       params['price']     = str(round(price, 8)); params['timeInForce'] = 'GTC'
        if stop_price:  params['stopPrice'] = str(round(stop_price, 8))
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
        """Coloca solo el SL (los TPs los gestiona el monitor por escalonado)."""
        d = self._order(sym, 'SELL', qty, 'STOP_MARKET', stop_price=sl_price)
        ok = d.get('code') == 0
        if not ok:
            d = self._order(sym, 'SELL', qty, 'STOP',
                            price=sl_price*0.999, stop_price=sl_price)
            ok = d.get('code') == 0
        log.info(f"  {'✅' if ok else '❌'} SL @ ${sl_price:.6f} (bajo VWAP)")
        return ok

    def open_trade(self, sym, sig):
        if not AUTO or sym in self.trades: return False
        if LongBot._opening or len(self.trades) >= MAX_TRADES: return False
        if self._has_any_position(sym):
            log.warning(f"  ⛔ {sym} ya tiene posición → omitiendo"); return False
        LongBot._opening = True
        try:
            return self._open(sym, sig)
        finally:
            LongBot._opening = False

    def _open(self, sym, sig):
        price    = sig['price']
        sl_price = sig['sl_price']
        tipo_txt = "🔴 RUPTURA" if sig['entrada_tipo'] == 'A' else "🟢 RETESTEO"

        log.info(f"\n  🎯 LONG {sym} [{tipo_txt}] | Score:{sig['score']:.0f} | RR:{sig['rr']:.2f}:1")
        log.info(f"  TPs: TP1=${sig['tp1_price']:.4f} | TP2=${sig['tp2_price']:.4f} | Runner→EMA25")

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
            log.warning("  ⚠️  LIMIT sin fill → MARKET")
            self._cancel_open(sym); time.sleep(0.5)
            d = self._order(sym, 'BUY', qty, 'MARKET')
            if d.get('code') != 0:
                log.error(f"  ❌ MARKET: {d.get('msg')}"); return False
            filled_qty, fill_price = self._confirm_pos(sym, 12)
            if not filled_qty: return False

        # Recalcular TPs desde precio real de fill
        sl_pct_real = (fill_price - sl_price) / fill_price * 100
        tp1_price   = fill_price * (1 + sl_pct_real * TP1_RATIO / 100)
        tp2_price   = fill_price * (1 + sl_pct_real * TP2_RATIO / 100)

        # Cantidades por tramo
        qty_tp1    = round(filled_qty * TP1_PCT / 100, 6)
        qty_tp2    = round(filled_qty * TP2_PCT / 100, 6)
        qty_runner = filled_qty - qty_tp1 - qty_tp2

        # Colocar solo el SL en el exchange (los TPs los gestiona el monitor)
        sl_ok = self._place_sl(sym, filled_qty, sl_price)
        if not sl_ok:
            time.sleep(2); sl_ok = self._place_sl(sym, filled_qty, sl_price)
        if not sl_ok:
            log.error("  ❌ SL crítico — cerrando")
            self._order(sym, 'SELL', filled_qty, 'MARKET'); return False

        trade = {
            'entry':        fill_price,
            'qty_total':    filled_qty,
            'qty_runner':   filled_qty,  # Decrece con cada TP parcial
            'qty_tp1':      qty_tp1,
            'qty_tp2':      qty_tp2,
            'qty_runner_f': qty_runner,  # Runner final (el 25%)
            'tp1_hit':      False,
            'tp2_hit':      False,
            'tp1_price':    tp1_price,
            'tp2_price':    tp2_price,
            'sl':           sl_price,
            'sl_vwap':      sig['vwap'],
            'tp_pct':       sig['tp_pct'],
            'sl_pct':       sl_pct_real,
            'highest':      fill_price,
            'opened':       datetime.now(),
            'score':        sig['score'],
            'ema25':        sig['ema25'],
            'vwap':         sig['vwap'],
            'entrada_tipo': sig['entrada_tipo'],
            'usdt':         POS_SIZE,
            'pnl_parcial':  0.0,
            'factors':      sig['factors'],
            'hora_utc':     sig['hora_utc'],
            'btc_dir':      sig['btc_dir'],
        }
        self.trades[sym] = trade
        self.stats['exec']  += 1
        self.stats['fees']  += notional * FEE

        self._tg(
            f"<b>🟢 LONG {tipo_txt}</b> — <b>{sym}</b>\n"
            f"Score: {sig['score']:.0f} | Calidad: {sig['calidad']:.0f}% | RR: {sig['rr']:.2f}:1\n"
            f"📍 Entrada: ${fill_price:.6f}\n"
            f"📊 VWAP:    ${sig['vwap']:.6f} (slope: {sig['slope']:+.2f}%)\n"
            f"🎯 TP1 ({TP1_PCT:.0f}%): ${tp1_price:.6f} (+{sl_pct_real*TP1_RATIO:.2f}%)\n"
            f"🎯 TP2 ({TP2_PCT:.0f}%): ${tp2_price:.6f} (+{sl_pct_real*TP2_RATIO:.2f}%)\n"
            f"🏃 Runner ({100-TP1_PCT-TP2_PCT:.0f}%): EMA25 trailing\n"
            f"🛑 SL (VWAP): ${sl_price:.6f} (-{sl_pct_real:.2f}%)\n"
            f"1H: {'🟢' if sig['trend_1h']==1 else '⚪'} | BTC: {self._btc_1h:+.2f}%"
        )
        return True

    # ════════════════════════════════════════════════════════════════
    # CIERRE PARCIAL — TPs escalonados (v5.3-A)
    # ════════════════════════════════════════════════════════════════

    def _close_partial(self, sym, qty, exit_price, label):
        """Cierra una fracción de la posición y actualiza stats."""
        if qty <= 0: return 0
        d = self._order(sym, 'SELL', qty, 'MARKET')
        if d.get('code') != 0:
            log.error(f"  ❌ Cierre parcial {label} {sym}: {d.get('msg')}")
            return 0

        t   = self.trades[sym]
        chg = (exit_price - t['entry']) / t['entry']
        # PnL proporcional a la fracción cerrada
        frac  = qty / t['qty_total']
        gross = POS_SIZE * LEVERAGE * chg * frac
        fees  = POS_SIZE * LEVERAGE * FEE * 2 * frac
        net   = gross - fees

        t['pnl_parcial']  += net
        t['qty_runner']   -= qty
        self.stats['fees'] += fees
        self._daily_pnl   += net
        self.stats['pnl'] += net

        pct_trade = net / (POS_SIZE * frac) * 100
        log.info(f"  💰 {label} {sym}: ${net:+.4f} ({pct_trade:+.1f}%) | Resta:{t['qty_runner']:.4f}")
        self._tg(
            f"<b>💰 {label} cerrado — {sym}</b>\n"
            f"${exit_price:.6f} (+{chg*100:.2f}%)\n"
            f"PnL parcial: ${net:+.4f}\n"
            f"Posición restante: {t['qty_runner']:.4f} unidades"
        )
        return net

    def _close_all(self, sym, exit_price, reason):
        """Cierra toda la posición restante y registra el trade completo."""
        if sym not in self.trades: return False
        t = self.trades[sym]
        qty_rem = t['qty_runner']
        if qty_rem > 0:
            self._order(sym, 'SELL', qty_rem, 'MARKET')

        # PnL total = parcial ya realizado + runner
        chg_runner = (exit_price - t['entry']) / t['entry']
        frac_runner = qty_rem / t['qty_total'] if t['qty_total'] > 0 else 0
        gross_runner = POS_SIZE * LEVERAGE * chg_runner * frac_runner
        fees_runner  = POS_SIZE * LEVERAGE * FEE * 2 * frac_runner
        net_runner   = gross_runner - fees_runner

        net_total = t['pnl_parcial'] + net_runner
        win = net_total > 0

        self.stats['closed'] += 1
        self.stats['pnl']    += net_runner
        self.stats['fees']   += fees_runner
        self._daily_pnl      += net_runner
        if win: self.stats['wins']   += 1
        else:   self.stats['losses'] += 1

        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        mins  = int((datetime.now() - t['opened']).total_seconds() / 60)
        emoji = "✅" if win else "❌"
        pct   = net_total / POS_SIZE * 100

        log.info(f"  {emoji} {reason} | ${net_total:+.4f} ({pct:+.1f}%) | {mins}min | WR:{wr:.0f}%")

        # Registrar en aprendizaje con contexto completo
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
            f"<b>{emoji} CERRADO — {reason}</b>\n"
            f"<b>{sym}</b> | {'Ruptura' if t['entrada_tipo']=='A' else 'Retesteo'} | {mins}min\n"
            f"Entrada: ${t['entry']:.6f} → ${exit_price:.6f}\n"
            f"PnL parciales: ${t['pnl_parcial']:+.4f} | Runner: ${net_runner:+.4f}\n"
            f"<b>PnL total: ${net_total:+.4f} ({pct:+.1f}%)</b>\n"
            f"<b>Acumulado: ${self.stats['pnl']:+.4f} | WR: {wr:.0f}%</b>"
        )
        if self.stats['closed'] % 3 == 0: self.learn.save()
        del self.trades[sym]
        return True

    # ════════════════════════════════════════════════════════════════
    # MONITOR v5.3 — TPs escalonados + EMA25 trailing
    # ════════════════════════════════════════════════════════════════

    async def monitor(self):
        for sym in list(self.trades.keys()):
            try:
                t  = self.trades[sym]
                tk = self._ticker(sym)
                if not tk: continue
                cur = tk['price']
                pct = (cur - t['entry']) / t['entry'] * 100

                # Actualizar EMA25 dinámicamente
                c5, *_ = self._klines(sym, '5m', 35)
                if c5: t['ema25'] = ema(c5, 25)

                # Actualizar máximo histórico
                if cur > t['highest']: t['highest'] = cur

                # ── TP1: Cierra el 40% al 1×SL ──────────────────────
                if not t['tp1_hit'] and cur >= t['tp1_price']:
                    self._close_partial(sym, t['qty_tp1'], cur, f"TP1({TP1_PCT:.0f}%)")
                    t['tp1_hit'] = True
                    # SL sube a break-even (v5.3-C)
                    be = t['entry'] * 1.001   # Break-even + 0.1% fees
                    if be > t['sl']:
                        t['sl'] = be
                        log.info(f"  🔒 {sym} SL → break-even ${be:.6f}")
                    continue

                # ── TP2: Cierra el 35% al 2×SL ──────────────────────
                if t['tp1_hit'] and not t['tp2_hit'] and cur >= t['tp2_price']:
                    self._close_partial(sym, t['qty_tp2'], cur, f"TP2({TP2_PCT:.0f}%)")
                    t['tp2_hit'] = True
                    # SL sube al 50% de la ganancia actual
                    locked = t['entry'] + (cur - t['entry']) * 0.5
                    if locked > t['sl']:
                        t['sl'] = locked
                        log.info(f"  🔒 {sym} SL → ${locked:.6f} (50% ganancia)")
                    continue

                # ── RUNNER (25%): EMA25 trailing (v5.3-B) ─────────────
                # Después de TP2: solo cierra por EMA25 cruce bajista
                if t['tp2_hit']:
                    if cur < t['ema25']:
                        if c5 and len(c5) >= 2 and c5[-1] < t['ema25'] and c5[-2] < t['ema25']:
                            self._close_all(sym, cur, "EMA25 RUNNER")
                            continue
                    # Trailing: SL sube con EMA25 (no baja nunca)
                    if t['ema25'] > t['sl']:
                        t['sl'] = t['ema25']
                    continue

                # ── Antes de TP2: EMA25 como salida de emergencia ──────
                if t['tp1_hit'] and not t['tp2_hit']:
                    if cur < t['ema25'] and c5 and c5[-1] < t['ema25'] and c5[-2] < t['ema25']:
                        self._close_all(sym, cur, "EMA25 PRE-TP2")
                        continue

                # ── Sin TPs: EMA25 si hay ganancia mínima ─────────────
                if not t['tp1_hit']:
                    if pct > 0.3 and cur < t['ema25']:
                        if c5 and c5[-1] < t['ema25'] and c5[-2] < t['ema25']:
                            self._close_all(sym, cur, "EMA25 EARLY")
                            continue

                # ── SL (bajo el VWAP, luego break-even, luego trailing) ─
                if cur <= t['sl']:
                    self._close_all(sym, cur, "STOP LOSS")

            except Exception as e:
                log.debug(f"monitor {sym}: {e}")

    # ════════════════════════════════════════════════════════════════
    # UTILIDADES
    # ════════════════════════════════════════════════════════════════

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
            self._daily_pnl  = 0.0
            self._daily_date = today
            self._cb_active  = False
            self._cb_until   = None
            self.learn.streak = 0
            self._update_equity()
            log.info("📅 Nuevo día")

    def _circuit_check(self) -> bool:
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
            self._tg(
                f"<b>🔒 CIRCUIT BREAKER</b>\n"
                f"Pérdida: ${self._daily_pnl:.3f} | Pausa {CB_HOURS}h"
            )
        return self._cb_active

    def _report(self):
        if datetime.now() - self._last_report < timedelta(hours=2): return
        self._last_report = datetime.now()
        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        pos   = ""
        for sym, t in self.trades.items():
            tk  = self._ticker(sym)
            cur = tk['price'] if tk else t['entry']
            pct = (cur - t['entry']) / t['entry'] * 100
            tp_estado = ("TP1✅TP2✅" if t['tp2_hit'] else
                         "TP1✅" if t['tp1_hit'] else "→TP1")
            pos += f"  📌 {sym}: {pct:+.2f}% | {tp_estado} | EMA25:${t['ema25']:.4f}\n"
        self._tg(
            f"<b>📊 Reporte LONGS v5.3</b>\n"
            f"PnL: ${self.stats['pnl']:+.4f} | WR: {wr:.0f}% | {total} trades\n"
            f"Día: ${self._daily_pnl:+.4f} (límite -${CB_USDT})\n"
            f"Equity: ${ACCOUNT_EQUITY:.2f} | Score mín: {self.learn.opt_score:.0f}\n"
            f"Circuit: {'🔒' if self._cb_active else '🔓'} | BTC: {self._btc_1h:+.2f}%\n"
            + (pos if pos else "  Sin posiciones\n")
        )

    def _tg(self, msg):
        try:
            if TG_TOKEN and TG_CHAT:
                requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                    timeout=6)
        except: pass

    # ════════════════════════════════════════════════════════════════
    # LOOP PRINCIPAL
    # ════════════════════════════════════════════════════════════════

    async def run(self):
        log.info("\n🚀 Bot LONGS v5.3 — TPs Escalonados + EMA25 + Deep Learning\n")
        iteration = 0
        last_sym_refr = last_ltv = last_hedge = last_equity = 0

        while True:
            try:
                iteration += 1
                self._daily_reset()

                if time.time() - last_sym_refr > 600:
                    self._refresh_symbols(); last_sym_refr = time.time()
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

                log.info(f"\n{'='*72}")
                log.info(f"  #{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                         f"Abiertos:{len(self.trades)}/{MAX_TRADES} | "
                         f"PnL:${self.stats['pnl']:+.4f} | WR:{wr:.0f}%")
                log.info(f"  BTC:{self._btc_1h:+.2f}% | Equity:${ACCOUNT_EQUITY:.2f} | "
                         f"Score mín:{self.learn.opt_score:.0f} | "
                         f"Blacklist:{len(self.learn.blacklist)}")
                log.info(f"{'='*72}\n")

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
                            tipo_icon = "🔴" if sig['entrada_tipo'] == 'A' else "🟢"
                            log.info(
                                f"  💡 {tipo_icon} {sym} | "
                                f"Score:{sig['score']:.0f} | RR:{sig['rr']:.2f}:1 | "
                                f"TP1:+{sig['sl_pct']*TP1_RATIO:.1f}% "
                                f"TP2:+{sig['sl_pct']*TP2_RATIO:.1f}% Runner→EMA25"
                            )
                            if self.open_trade(sym, sig):
                                await asyncio.sleep(3)
                        if (i+1) % 15 == 0:
                            log.info(f"  ...{i+1}/{len(self.symbols)}")
                        await asyncio.sleep(0.12)
                    log.info(f"  ✅ Scan: {found} señales")
                else:
                    log.info("  ⏸️  Max trades — monitoreando")

                await asyncio.sleep(INTERVAL)

            except KeyboardInterrupt:
                log.info("⏹️  Detenido"); break
            except Exception as e:
                log.error(f"❌ Error #{iteration}: {e}", exc_info=True)
                await asyncio.sleep(20)

        self.learn.save()


# ============================================================================
# ENTRY POINT
# ============================================================================

async def main():
    bot = LongBot()
    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("👋 Bot terminado")
