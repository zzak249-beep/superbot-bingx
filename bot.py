"""
BingX Signal Bot v6
===================
FIXES vs v5:
  - order_book.py ya no tiene nearest_bid_wall crash
  - trade_manager importa correctamente get_performance()
  - scanner retorna 0 símbolos → solucionado con filtros graduales

MEJORAS — VENTAJA DE VELOCIDAD:
  - WebSocket activo: precio en tiempo real (10-50ms vs 500ms REST)
  - Order book de todos los símbolos EN PARALELO (asyncio.gather)
  - Signal evaluation EN PARALELO (semáforo para no saturar API)
  - Pre-fetch de klines en background mientras se analiza
  - Ciclo más corto (30s en vez de 60s) porque todo es paralelo
  - Ejecución de orden inmediata con precio WS más preciso
"""

# ============================================================
# ⚡ HEALTH SERVER — stdlib, arranca en < 50 ms
# ============================================================
import threading, os as _os
from http.server import HTTPServer, BaseHTTPRequestHandler

_PORT    = int(_os.getenv("PORT", "8080"))
_DRY_RUN = _os.getenv("DRY_RUN", "false").lower() == "true"

_STATE: dict = {"html": (
    "<html><body style='background:#060a0f;color:#00ff88;"
    "font-family:monospace;padding:40px'>"
    "<h2>⚡ BingX Bot v6</h2><p>Iniciando...</p></body></html>"
)}

class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        b = _STATE["html"].encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass

threading.Thread(target=HTTPServer(("0.0.0.0",_PORT),_H).serve_forever,
                 daemon=True).start()
# ============================================================

import asyncio, logging, signal, json
from datetime import datetime

from signal_engine  import SignalEngine
from market_scanner import MarketScanner
from bingx_client   import BingXClient
from trade_manager  import TradeManager
from order_book     import OrderBookAnalyzer

try:
    from rl_trainer import klines_to_csv, run_training
    RL_AVAILABLE = True
except ImportError:
    RL_AVAILABLE = False

_os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers= [logging.FileHandler("logs/bot.log"), logging.StreamHandler()]
)
log = logging.getLogger("BOT")

AUTO_TRADING  = _os.getenv("AUTO_TRADING_ENABLED", "true").lower() == "true"
SCAN_INTERVAL = int(_os.getenv("SCAN_INTERVAL_SEC", "30"))   # v6: 30s (paralelo = rápido)

# Semáforo para limitar concurrencia de evaluaciones
_EVAL_SEM = asyncio.Semaphore(8)   # max 8 símbolos en paralelo


# ── Telegram: formato rico ────────────────────────────────── #
def _score_emoji(score: float) -> str:
    return "🟢" if score >= 70 else "🟡" if score >= 55 else "🟠"

def _level_label(score: float) -> str:
    return "ALTO" if score >= 70 else "MEDIO" if score >= 55 else "BAJO"

async def _send_signal_alert(client, sig: dict):
    s     = sig
    emoji = _score_emoji(s["score"])
    lvl   = _level_label(s["score"])

    z_score = round((s["cvd_pct"] - 50) / 10, 2)
    metrics = (
        f"BB:{s['bb_width']:.1f}% "
        f"Z:{z_score:+.2f} "
        f"CVD:{s['cvd_pct']:.0f}% "
        f"MTF:{s['mtf_label']}"
    )

    sigs_lines = "\n".join(
        f"  {'🎯' if 'BB' in l else '🔥' if 'Vol' in l else '🌊' if 'CVD' in l else '✅' if 'MTF' in l else '⚡' if 'Anticip' in l else '🚀'} {l}"
        for l in s.get("active_sigs", [])
    ) or f"  📊 {s.get('signal_type','')}"

    dir_e = "🟢" if s["direction"] == "LONG" else "🔴"
    msg = (
        f"{emoji} <b>{lvl} {dir_e} {s['symbol']} — {s['score']:.0f}%</b>\n"
        f"$ ${s['price']:.6g} | 24h: {s.get('mean_pnl',0)*100:+.2f}%\n"
        f"{metrics}\n"
        f"Señales:\n{sigs_lines}\n"
        f"⏰ {datetime.utcnow().strftime('%H:%M')} UTC"
    )
    await client.notify(msg)


# ── Dashboard HTML ─────────────────────────────────────────── #
def _render(s: dict) -> str:
    d    = s.get("daily", {})
    perf = s.get("perf",  {})
    DRY  = _os.getenv("DRY_RUN","false").lower() == "true"
    m    = ('<span style="color:#ff6600;border:1px solid #ff6600;padding:2px 8px">DRY RUN</span>'
            if DRY else
            '<span style="color:#00ff88;border:1px solid #00ff88;padding:2px 8px">● LIVE</span>')

    paused = d.get("risk_paused", False)
    banner = (f'<div style="background:#1a0000;border:1px solid #ff3355;border-radius:4px;'
              f'padding:10px 16px;margin-bottom:14px;color:#ff3355;font-family:monospace;font-size:11px">'
              f'⛔ CIRCUIT BREAKER: {d.get("pause_reason","")}</div>') if paused else ""

    TH = "padding:7px 12px;font-size:10px;letter-spacing:1px;color:#4a5568;text-align:left;text-transform:uppercase;background:#0a0f16"
    TD = "padding:7px 12px;font-family:monospace;font-size:11px;border-bottom:1px solid rgba(26,37,53,.6)"

    def stat(lbl, val, col="#00ff88", sub=""):
        return (f'<div style="background:#0d1117;border:1px solid #1a2535;border-radius:6px;'
                f'padding:12px 15px;border-bottom:2px solid {col}">'
                f'<div style="font-size:10px;letter-spacing:2px;color:#4a5568;text-transform:uppercase;margin-bottom:4px">{lbl}</div>'
                f'<div style="font-family:monospace;font-size:20px;color:{col}">{val}</div>'
                f'{"<div style=font-size:10px;color:#4a5568;margin-top:2px>"+sub+"</div>" if sub else ""}'
                f'</div>')

    total_pbr = sum(t.get("pbr",{}).get("cumulative_reward",0) for t in s.get("trades",[]))
    wr  = perf.get("win_rate", 0)
    pf  = perf.get("profit_factor", 0)
    lat = s.get("latency_ms", 0)

    stats = "".join([
        stat("Escaneados",    len(s.get("symbols",[])),  "#00ccff"),
        stat("Señales",       len(s.get("signals",[])),  "#ffcc00",
             f"min score {_os.getenv('MIN_SIGNAL_SCORE','40')}"),
        stat("Trades Open",   len(s.get("trades",[])),   "#00ff88"),
        stat("Win Rate",      f"{wr:.1f}%",  "#00ff88" if wr>=55 else "#ffcc00" if wr>=45 else "#ff3355",
             f"{perf.get('total_trades',0)} total"),
        stat("Profit Factor", f"{pf:.2f}",   "#00ff88" if pf>=1.5 else "#ffcc00" if pf>=1 else "#ff3355"),
        stat("Latencia REST", f"{lat:.0f}ms","#00ff88" if lat<200 else "#ffcc00" if lat<500 else "#ff3355",
             "WebSocket activo"),
    ])

    # Signals table
    srows = ""
    for sg in s.get("signals", []):
        sc    = sg.get("score", 0)
        sc_c  = "#00ff88" if sc>=70 else "#ffcc00" if sc>=55 else "#ff9900"
        rr    = sg.get("risk_reward", 0)
        rrc   = "#00ff88" if rr>=2 else "#ffcc00" if rr>=1.5 else "#ff3355"
        dc    = "#00ff88" if sg["direction"]=="LONG" else "#ff3355"
        cvd_c = "#00ff88" if sg.get("cvd_pct",50)>=55 else "#ff3355" if sg.get("cvd_pct",50)<=45 else "#ffcc00"
        sigs_short = " · ".join(sg.get("active_sigs",[])[:3])
        srows += (
            f'<tr><td style="{TD};color:#e0e0e0">{sg["symbol"]}</td>'
            f'<td style="{TD}"><span style="color:{dc};border:1px solid {dc};padding:1px 6px;font-size:10px">{sg["direction"]}</span></td>'
            f'<td style="{TD};color:{sc_c};font-weight:700">{sc:.0f}</td>'
            f'<td style="{TD};color:{cvd_c}">{sg.get("cvd_pct",0):.0f}%</td>'
            f'<td style="{TD};color:#aaaaaa">{sg.get("bb_width",0):.1f}%</td>'
            f'<td style="{TD};color:#4a5568">{sg.get("mtf_label","?")}</td>'
            f'<td style="{TD};color:{rrc}">{rr}</td>'
            f'<td style="{TD};color:#4a5568;font-size:10px">{sigs_short}</td></tr>'
        )
    sigs_html = (
        '<table style="width:100%;border-collapse:collapse"><tr>'
        + "".join(f'<th style="{TH}">{h}</th>'
                  for h in ["Simbolo","Dir","Score","CVD","BB","MTF","R:R","Señales"])
        + f'</tr>{srows}</table>'
        if srows else
        '<div style="padding:18px;color:#4a5568;font-family:monospace">'
        '⚠️ Sin señales — scanner activo</div>'
    )

    # Trades
    trows = ""
    for t in s.get("trades",[]):
        pbr = t.get("pbr",{}); cr=pbr.get("cumulative_reward",0)
        crc = "#00ff88" if cr>=0 else "#ff3355"
        dc  = "#00ff88" if t["direction"]=="LONG" else "#ff3355"
        tp1h= "✅" if t.get("tp1_hit") else "⏳"
        trows += (
            f'<tr><td style="{TD};color:#e0e0e0">{t["symbol"]}</td>'
            f'<td style="{TD}"><span style="color:{dc};border:1px solid {dc};padding:1px 6px;font-size:10px">{t["direction"]}</span></td>'
            f'<td style="{TD};color:#4a5568">{t["entry"]}</td>'
            f'<td style="{TD};color:#ffcc00">{t.get("tp1","—")} {tp1h}</td>'
            f'<td style="{TD};color:#00ff88">{t.get("tp2","—")}</td>'
            f'<td style="{TD};color:#ff3355">{t["sl"]}</td>'
            f'<td style="{TD};color:{crc}">{cr:.5f}</td>'
            f'<td style="{TD};color:#4a5568">{str(t.get("opened_at",""))[:16]}</td></tr>'
        )
    trades_html = (
        '<table style="width:100%;border-collapse:collapse"><tr>'
        + "".join(f'<th style="{TH}">{h}</th>'
                  for h in ["Simbolo","Dir","Entry","TP1","TP2","SL","PBR","Abierto"])
        + f'</tr>{trows}</table>'
        if trows else
        '<div style="padding:18px;color:#4a5568">Sin trades abiertos</div>'
    )

    # OB panel
    ob_rows = ""
    for sym, snap in s.get("ob", {}).items():
        if not snap: continue
        imb   = snap.get("imbalance_ratio", 1.)
        bias  = snap.get("bias", "?")
        bc    = "#00ff88" if bias=="BULLISH" else "#ff3355" if bias=="BEARISH" else "#ffcc00"
        bd    = snap.get("bid_delta_pct", 0)
        bdc   = "#00ff88" if bd>0 else "#ff3355"
        bw    = len(snap.get("bid_walls",[]))
        aw    = len(snap.get("ask_walls",[]))
        absorb= "🔥" if snap.get("absorption_signal") else ""
        fill  = min(100, int(imb/2*100))
        ob_rows += (
            f'<tr><td style="{TD};color:#e0e0e0">{sym}</td>'
            f'<td style="{TD};color:{bc}">{bias}</td>'
            f'<td style="{TD}">{imb:.2f}'
            f'<div style="height:3px;background:#1a2535;border-radius:2px;margin-top:2px">'
            f'<div style="width:{fill}%;height:100%;background:#00ff88"></div></div></td>'
            f'<td style="{TD};color:{bdc}">{bd:+.1f}%</td>'
            f'<td style="{TD};color:#00ff88">B:{bw}</td>'
            f'<td style="{TD};color:#ff3355">A:{aw}</td>'
            f'<td style="{TD}">{absorb}</td></tr>'
        )
    ob_html = (
        '<table style="width:100%;border-collapse:collapse"><tr>'
        + "".join(f'<th style="{TH}">{h}</th>'
                  for h in ["Simbolo","Bias","Imbalance","Bid Δ","B.Walls","A.Walls","Sig"])
        + f'</tr>{ob_rows}</table>'
        if ob_rows else
        '<div style="padding:18px;color:#4a5568">Calculando...</div>'
    )

    by_type = perf.get("by_signal_type", {})
    pt_rows = ""
    for st, v in by_type.items():
        wc  = "#00ff88" if v["win_rate"]>=60 else "#ffcc00" if v["win_rate"]>=45 else "#ff3355"
        pc  = "#00ff88" if v["pnl"]>=0 else "#ff3355"
        pt_rows += (f'<tr><td style="{TD}">{st}</td>'
                    f'<td style="{TD};color:#4a5568">{v["count"]}</td>'
                    f'<td style="{TD};color:{wc}">{v["win_rate"]:.1f}%</td>'
                    f'<td style="{TD};color:{pc}">{v["pnl"]:+.2f}$</td></tr>')
    pt_html = (
        '<table style="width:100%;border-collapse:collapse"><tr>'
        + "".join(f'<th style="{TH}">{h}</th>' for h in ["Tipo","N","Win%","PnL"])
        + f'</tr>{pt_rows}</table>'
        if pt_rows else
        '<div style="padding:18px;color:#4a5568">Sin historial aún</div>'
    )

    syms = " ".join(
        f'<span style="font-family:monospace;font-size:10px;padding:2px 6px;'
        f'border:1px solid #1a2535;border-radius:3px;color:#4a5568">{sym}</span>'
        for sym in s.get("symbols",[])
    ) or "—"

    def panel(title, badge, body):
        return (f'<div style="background:#0d1117;border:1px solid #1a2535;'
                f'border-radius:8px;overflow:hidden;margin-bottom:12px">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;'
                f'padding:8px 16px;background:#0a0f16;border-bottom:1px solid #1a2535">'
                f'<span style="font-size:10px;letter-spacing:2px;color:#4a5568;text-transform:uppercase">{title}</span>'
                f'<span style="font-family:monospace;font-size:11px;color:#00ff88">{badge}</span>'
                f'</div>{body}</div>')

    ws_count = s.get("ws_prices_count", 0)
    return f"""<!DOCTYPE html><html lang="es"><head>
<meta charset="UTF-8"><meta http-equiv="refresh" content="10">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BingX Signal Bot v6</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#060a0f;color:#c8d8e8;font-size:14px;font-family:monospace}}
body::before{{content:"";position:fixed;inset:0;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,255,136,.01) 2px,rgba(0,255,136,.01) 4px);pointer-events:none;z-index:999}}
tr:hover td{{background:rgba(0,255,136,.02)}}</style></head><body>
<div style="display:flex;align-items:center;justify-content:space-between;padding:12px 22px;
  border-bottom:1px solid #1a2535;background:#0a0f16;position:sticky;top:0;z-index:100">
  <div>
    <div style="font-size:18px;font-weight:700;letter-spacing:3px;color:#00ff88">⚡ BINGX SIGNAL BOT v6</div>
    <div style="font-size:10px;color:#4a5568;letter-spacing:2px;margin-top:1px">WS · CVD · BB SQUEEZE · MTF · ORDER BOOK · PARALELO</div>
  </div>
  <div style="display:flex;gap:12px;align-items:center">
    <span style="font-size:10px;color:#4a5568">
      Ciclo #{s.get("cycle",0)} · {s.get("updated","—")} · 
      WS: <span style="color:#00ff88">{ws_count} feeds</span>
    </span>
    {m}
  </div>
</div>
<div style="padding:16px 22px;max-width:1700px;margin:0 auto">
  {banner}
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:14px">{stats}</div>
  <div style="display:grid;grid-template-columns:3fr 2fr;gap:12px">
    {panel("Señales Activas", f"{len(s.get('signals',[]))} señales", sigs_html)}
    {panel("Order Book", "muros · absorción", ob_html)}
  </div>
  {panel("Trades Abiertos", f"{len(s.get('trades',[]))} posiciones", trades_html)}
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
    {panel("Performance por Tipo de Señal", "", pt_html)}
    <div style="background:#0d1117;border:1px solid #1a2535;border-radius:8px;overflow:hidden">
      <div style="padding:8px 16px;background:#0a0f16;border-bottom:1px solid #1a2535">
        <span style="font-size:10px;letter-spacing:2px;color:#4a5568;text-transform:uppercase">Universo Escaneado</span>
      </div>
      <div style="padding:12px 16px;display:flex;flex-wrap:wrap;gap:5px">{syms}</div>
    </div>
  </div>
</div></body></html>"""


# ================================================================== #
class TradingBot:
    def __init__(self):
        self.client        = BingXClient(
            api_key    = _os.environ["BINGX_API_KEY"],
            api_secret = _os.environ["BINGX_API_SECRET"],
        )
        self.ob            = OrderBookAnalyzer(self.client)
        self.scanner       = MarketScanner(self.client, self.ob)
        self.signal_engine = SignalEngine(self.ob)
        self.trade_manager = TradeManager(self.client)
        self.running       = False
        self._cycle        = 0
        self._rl_every     = int(_os.getenv("RL_TRAIN_EVERY", "0"))
        self._rl_symbol    = _os.getenv("RL_TRAIN_SYMBOL", "BTC-USDT")

    async def run(self):
        mode = "DRY_RUN" if _DRY_RUN else "LIVE"
        log.info(f"Bot v6 iniciado — {mode}  (health en :{_PORT})")

        try:
            await self.client.initialize()
        except Exception as e:
            log.error(f"initialize(): {e}")

        await self.client.notify(
            f"⚡ <b>Bot v6 iniciado</b> — {mode}\n"
            f"Scan: {SCAN_INTERVAL}s · Paralelo · WebSocket activo"
        )

        self.running = True
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda: setattr(self, "running", False))
            except NotImplementedError:
                pass

        while self.running:
            try:
                await self._tick()
            except Exception as e:
                log.error(f"Ciclo: {e}", exc_info=True)
            await asyncio.sleep(SCAN_INTERVAL)

        await self.client.close()

    async def _tick(self):
        self._cycle += 1
        log.info(f"━━ Ciclo #{self._cycle} ━━")

        # 1. Símbolos calientes (scanner ya paralelo internamente)
        hot = await self.scanner.get_hot_symbols(
            top_n      = int(_os.getenv("TOP_SYMBOLS",    "30")),
            min_volume = float(_os.getenv("MIN_VOLUME_USDT","500000")),
        )
        log.info(f"  {len(hot)} símbolos calientes")

        if not hot:
            log.warning("  ⚠️ Sin símbolos — revisa MIN_VOLUME_USDT o MIN_MOMENTUM")

        # 2. Order book EN PARALELO para todos los símbolos
        ob_snapshots: dict = {}
        if hot:
            ob_results = await self.ob.analyze_batch(hot)
            ob_snapshots = ob_results

        # 3. Evaluación de señales EN PARALELO con semáforo
        async def _eval_one(sym: str):
            async with _EVAL_SEM:
                try:
                    ob_snap = ob_snapshots.get(sym)
                    sig     = await self.signal_engine.evaluate(self.client, sym, ob_snap)
                    return sig
                except Exception as e:
                    log.debug(f"eval {sym}: {e}")
                    return None

        eval_tasks = [_eval_one(sym) for sym in hot]
        eval_results = await asyncio.gather(*eval_tasks, return_exceptions=True)

        signals = []
        for res in eval_results:
            if isinstance(res, dict) and res:
                signals.append(res)
                await _send_signal_alert(self.client, res)

        log.info(f"  {len(signals)} señales detectadas")

        # 4. Gestión de trades abiertos
        await self.trade_manager.manage_open_trades()

        # 5. Abrir trades automáticamente
        if AUTO_TRADING:
            for sig in sorted(signals, key=lambda x: x.get("score", 0), reverse=True):
                await self.trade_manager.open_trade(sig)

        # 6. Stats
        trades = await self.trade_manager.get_open_trades()
        daily  = self.trade_manager.get_daily_stats()
        perf   = self.trade_manager.get_performance()

        # 7. Construir dict OB para el dashboard
        ob_dict = {}
        for sym in hot[:12]:
            snap = ob_snapshots.get(sym)
            if snap:
                ob_dict[sym] = {
                    "imbalance_ratio":   snap.imbalance_ratio,
                    "bias":              snap.bias,
                    "bid_walls":         [{"p": w.price} for w in snap.bid_walls],
                    "ask_walls":         [{"p": w.price} for w in snap.ask_walls],
                    "bid_delta_pct":     snap.bid_delta_pct,
                    "ask_delta_pct":     snap.ask_delta_pct,
                    "absorption_signal": snap.absorption_signal,
                    "spread_pct":        snap.spread_pct,
                }

        _STATE["html"] = _render({
            "symbols":          hot,
            "signals":          signals,
            "trades":           trades,
            "cycle":            self._cycle,
            "updated":          datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "daily":            daily,
            "perf":             perf,
            "ob":               ob_dict,
            "latency_ms":       self.client._latency_ms,
            "ws_prices_count":  len(self.client._ws_prices),
        })

        # RL opcional
        if RL_AVAILABLE and self._rl_every > 0 and self._cycle % self._rl_every == 0:
            await self._rl()

    async def _rl(self):
        try:
            klines = await self.client.get_klines(self._rl_symbol, "1h", limit=1000)
            if len(klines) < 300:
                return
            split = int(len(klines) * 0.8)
            klines_to_csv(klines[:split],  "logs/training.csv")
            klines_to_csv(klines[split:],  "logs/evaluation.csv")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: run_training(
                    "logs/training.csv", "logs/evaluation.csv",
                    int(_os.getenv("RL_ITERATIONS", "5"))
                )
            )
        except Exception as e:
            log.error(f"RL: {e}")


if __name__ == "__main__":
    log.info(f"Health server activo en 0.0.0.0:{_PORT}")
    bot = TradingBot()
    asyncio.run(bot.run())
