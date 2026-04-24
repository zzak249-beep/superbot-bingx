"""
BingX Signal Bot v7 — PROFESSIONAL EDITION
==========================================
MEJORAS vs v6:
  ✅ Balance leído de FUTUROS (fix crítico del Balance 0)
  ✅ MIN_SIGNAL_SCORE coherente (60+, no 40)
  ✅ Apalancamiento dinámico por tipo de coin y volatilidad ATR
  ✅ Scanner multi-factor (11 señales de detección temprana)
  ✅ Signal engine con quality gates estrictos
  ✅ Trailing stop profesional con activación en ATR
  ✅ Dashboard v7 con métricas en tiempo real
  ✅ Cooldown inteligente (15min TP, 4h SL)
  ✅ Circuit breaker multi-nivel
  ✅ Reset diario de stats a medianoche UTC
"""

# ── Health server (arranca < 50ms) ──────────────────────────── #
import threading, os as _os
from http.server import HTTPServer, BaseHTTPRequestHandler

_PORT    = int(_os.getenv("PORT", "8080"))
_DRY_RUN = _os.getenv("DRY_RUN", "false").lower() == "true"

_STATE: dict = {"html": (
    "<html><body style='background:#0a0a0f;color:#00ff88;"
    "font-family:monospace;padding:40px'>"
    "<h2>⚡ BingX Bot v7 — Professional Edition</h2>"
    "<p>Iniciando sistema...</p></body></html>"
)}

class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        b = _STATE["html"].encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass

threading.Thread(
    target=HTTPServer(("0.0.0.0", _PORT), _H).serve_forever, daemon=True
).start()
# ─────────────────────────────────────────────────────────────── #

import asyncio, logging, signal, json
from datetime import datetime, timezone

from signal_engine  import SignalEngine
from market_scanner import MarketScanner
from bingx_client   import BingXClient
from trade_manager  import TradeManager
from order_book     import OrderBookAnalyzer

_os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers = [
        logging.FileHandler("logs/bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("BOT")

AUTO_TRADING  = _os.getenv("AUTO_TRADING_ENABLED", "true").lower() == "true"
SCAN_INTERVAL = int(_os.getenv("SCAN_INTERVAL_SEC", "30"))
TOP_N         = int(_os.getenv("TOP_SYMBOLS",        "40"))
MIN_VOL       = float(_os.getenv("MIN_VOLUME_USDT",  "500000"))
MIN_SCORE     = float(_os.getenv("MIN_SCORE",         "60"))

_EVAL_SEM = asyncio.Semaphore(10)


# ── Telegram alert ─────────────────────────────────────────── #

async def _send_signal_alert(client, sig: dict):
    score = sig["score"]
    emoji = "🟢" if score >= 75 else "🟡" if score >= 60 else "🟠"
    lvl   = "ALTO" if score >= 75 else "MEDIO"
    dir_e = "🟢" if sig["direction"] == "LONG" else "🔴"

    sigs_text = "\n".join(
        f"  {'🎯' if 'BB' in l else '🔥' if 'Vol' in l else '🌊' if 'CVD' in l else '✅' if 'MTF' in l else '⚡' if 'Mom' in l else '📊'} {l}"
        for l in sig.get("active_sigs", [])
    ) or "  📊 Señal combinada"

    lev_info = ""
    if sig.get("atr", 0) > 0:
        lev_info = f"ATR: {sig['atr']:.6g} | "

    msg = (
        f"{emoji} <b>{lvl} {dir_e} {sig['symbol']} — {score:.0f}%</b>\n"
        f"💲 ${sig['price']:.6g}\n"
        f"BB:{sig['bb_width']:.1f}% CVD:{sig['cvd_pct']:.0f}% MTF:{sig['mtf_label']}\n"
        f"{lev_info}R:R {sig['risk_reward']:.1f}x\n"
        f"<b>Señales activas:</b>\n{sigs_text}\n"
        f"SL: ${sig['sl']:.6g} | TP1: ${sig['tp1']:.6g} | TP2: ${sig['tp2']:.6g}\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M')} UTC"
    )
    await client.notify(msg)


# ── Dashboard HTML profesional ─────────────────────────────── #

def _render(s: dict) -> str:
    d    = s.get("daily", {})
    perf = s.get("perf", {})
    DRY  = _DRY_RUN
    mode_badge = (
        '<span class="badge badge-dry">DRY RUN</span>' if DRY else
        '<span class="badge badge-live">● LIVE</span>'
    )
    paused     = d.get("risk_paused", False)
    banner     = ""
    if paused:
        banner = (f'<div class="alert-banner">'
                  f'⛔ CIRCUIT BREAKER: {d.get("pause_reason","")}</div>')

    wr  = perf.get("win_rate", 0)
    pf  = perf.get("profit_factor", 0)
    lat = s.get("latency_ms", 0)
    net = perf.get("net_pnl", 0)
    eq  = d.get("equity", 100)

    def stat_card(label, value, color, sub=""):
        return (f'<div class="stat-card" style="--accent:{color}">'
                f'<div class="stat-label">{label}</div>'
                f'<div class="stat-value" style="color:{color}">{value}</div>'
                f'{"<div class=stat-sub>" + sub + "</div>" if sub else ""}'
                f'</div>')

    stats = "".join([
        stat_card("Escaneados",    len(s.get("symbols",[])), "#38bdf8", f"TOP_{TOP_N}"),
        stat_card("Señales",       len(s.get("signals",[])), "#fbbf24", f"min {MIN_SCORE:.0f}pts"),
        stat_card("Posiciones",    len(s.get("trades",[])),  "#34d399", f"max 3"),
        stat_card("Win Rate",      f"{wr:.1f}%",
                  "#34d399" if wr>=55 else "#fbbf24" if wr>=45 else "#f87171",
                  f"{perf.get('total_trades',0)} trades"),
        stat_card("Profit Factor", f"{pf:.2f}",
                  "#34d399" if pf>=1.5 else "#fbbf24" if pf>=1 else "#f87171"),
        stat_card("PnL Neto",      f"${net:+.2f}",
                  "#34d399" if net>=0 else "#f87171", f"equity ${eq:.0f}"),
        stat_card("Latencia",      f"{lat:.0f}ms",
                  "#34d399" if lat<200 else "#fbbf24" if lat<500 else "#f87171",
                  "WebSocket activo"),
        stat_card("Daily PnL",     f"${d.get('daily_pnl',0):+.2f}",
                  "#34d399" if d.get("daily_pnl",0)>=0 else "#f87171",
                  f"{d.get('daily_trades',0)} hoy"),
    ])

    # Signals table
    srows = ""
    for sg in sorted(s.get("signals", []), key=lambda x: x.get("score", 0), reverse=True):
        sc   = sg.get("score", 0)
        sc_c = "#34d399" if sc>=75 else "#fbbf24" if sc>=60 else "#fb923c"
        rr   = sg.get("risk_reward", 0)
        rrc  = "#34d399" if rr>=2 else "#fbbf24" if rr>=1.5 else "#f87171"
        sigs_short = " · ".join(sg.get("active_sigs", [])[:3])
        lev  = sg.get("leverage", "?")
        srows += (
            f'<tr>'
            f'<td class="sym">{sg["symbol"]}</td>'
            f'<td><span class="dir-long">LONG</span></td>'
            f'<td style="color:{sc_c};font-weight:700">{sc:.0f}</td>'
            f'<td class="num">{sg.get("bb_width",0):.1f}%</td>'
            f'<td class="num" style="color:{"#34d399" if sg.get("cvd_pct",50)>=58 else "#f87171" if sg.get("cvd_pct",50)<=42 else "#fbbf24"}">'
            f'{sg.get("cvd_pct",0):.0f}%</td>'
            f'<td class="num">{sg.get("mtf_label","?")}</td>'
            f'<td class="num" style="color:{rrc}">{rr}x</td>'
            f'<td class="dim">{sigs_short}</td>'
            f'</tr>'
        )
    sigs_html = (
        f'<table><thead><tr>'
        + "".join(f"<th>{h}</th>" for h in ["Símbolo","Dir","Score","BB","CVD","MTF","R:R","Señales"])
        + f'</tr></thead><tbody>{srows}</tbody></table>'
        if srows else '<div class="empty">Sin señales — scanner activo</div>'
    )

    # Trades table
    trows = ""
    for t in s.get("trades", []):
        pnl  = t.get("pnl_usdt", 0)
        pc   = "#34d399" if pnl >= 0 else "#f87171"
        tp1h = "✅" if t.get("tp1_hit") else "⏳"
        trows += (
            f'<tr>'
            f'<td class="sym">{t["symbol"]}</td>'
            f'<td><span class="dir-long">LONG</span></td>'
            f'<td class="num">${t["entry"]:.6g}</td>'
            f'<td class="num warn">${t.get("tp1","—"):.6g} {tp1h}</td>'
            f'<td class="num good">${t.get("tp2","—"):.6g}</td>'
            f'<td class="num bad">${t["sl"]:.6g}</td>'
            f'<td class="num" style="color:{pc}">${pnl:+.2f}</td>'
            f'<td class="num">{t.get("leverage","?")}x</td>'
            f'<td class="dim">{str(t.get("opened_at",""))[-5:]}</td>'
            f'</tr>'
        )
    trades_html = (
        f'<table><thead><tr>'
        + "".join(f"<th>{h}</th>" for h in ["Símbolo","Dir","Entry","TP1","TP2","SL","PnL","Lev","Hora"])
        + f'</tr></thead><tbody>{trows}</tbody></table>'
        if trows else '<div class="empty">Sin posiciones abiertas</div>'
    )

    # OB panel
    ob_rows = ""
    for sym, snap in s.get("ob", {}).items():
        if not snap: continue
        imb  = snap.get("imbalance_ratio", 1.)
        bias = snap.get("bias", "?")
        bc   = "#34d399" if bias == "BULLISH" else "#f87171" if bias == "BEARISH" else "#94a3b8"
        bd   = snap.get("bid_delta_pct", 0)
        bdc  = "#34d399" if bd > 0 else "#f87171"
        bw   = len(snap.get("bid_walls", []))
        aw   = len(snap.get("ask_walls", []))
        ab   = "🔥" if snap.get("absorption_signal") else ""
        fill = min(100, int(imb / 2 * 100))
        ob_rows += (
            f'<tr>'
            f'<td class="sym">{sym}</td>'
            f'<td style="color:{bc};font-weight:600">{bias}</td>'
            f'<td class="num"><span>{imb:.2f}</span>'
            f'<div class="imb-bar"><div class="imb-fill" style="width:{fill}%"></div></div></td>'
            f'<td class="num" style="color:{bdc}">{bd:+.1f}%</td>'
            f'<td class="num good">B:{bw}</td>'
            f'<td class="num bad">A:{aw}</td>'
            f'<td>{ab}</td>'
            f'</tr>'
        )
    ob_html = (
        f'<table><thead><tr>'
        + "".join(f"<th>{h}</th>" for h in ["Símbolo","Bias","Imbalance","Bid Δ","B.Walls","A.Walls","Absorb"])
        + f'</tr></thead><tbody>{ob_rows}</tbody></table>'
        if ob_rows else '<div class="empty">Calculando order books...</div>'
    )

    # Performance por tipo
    by_type = perf.get("by_signal_type", {})
    pt_rows = ""
    for st, v in sorted(by_type.items(), key=lambda x: x[1]["pnl"], reverse=True):
        wc = "#34d399" if v["win_rate"] >= 60 else "#fbbf24" if v["win_rate"] >= 45 else "#f87171"
        pc = "#34d399" if v["pnl"] >= 0 else "#f87171"
        pt_rows += (f'<tr><td>{st}</td>'
                    f'<td class="num">{v["count"]}</td>'
                    f'<td class="num" style="color:{wc}">{v["win_rate"]:.1f}%</td>'
                    f'<td class="num" style="color:{pc}">${v["pnl"]:+.2f}</td></tr>')
    pt_html = (
        f'<table><thead><tr>'
        + "".join(f"<th>{h}</th>" for h in ["Tipo","N","Win%","PnL"])
        + f'</tr></thead><tbody>{pt_rows}</tbody></table>'
        if pt_rows else '<div class="empty">Sin historial de trades</div>'
    )

    ws_count = s.get("ws_prices_count", 0)
    cycle    = s.get("cycle", 0)
    updated  = s.get("updated", "—")

    def panel(title, badge, body, col="#38bdf8"):
        return (f'<div class="panel">'
                f'<div class="panel-header">'
                f'<span class="panel-title">{title}</span>'
                f'<span class="panel-badge" style="color:{col}">{badge}</span>'
                f'</div><div class="panel-body">{body}</div></div>')

    syms = " ".join(
        f'<span class="sym-tag">{sym}</span>'
        for sym in s.get("symbols", [])
    ) or '—'

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="15">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BingX Bot v7 — Professional</title>
<style>
  :root {{
    --bg0: #070b12; --bg1: #0d1420; --bg2: #111a2a;
    --bg3: #162033; --border: rgba(56,189,248,.12);
    --text: #e2e8f0; --dim: #4a5568; --accent: #38bdf8;
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--bg0); color:var(--text); font-family:'Courier New',monospace; font-size:13px; }}
  
  /* Scanline overlay */
  body::after {{
    content:""; position:fixed; inset:0; pointer-events:none; z-index:999;
    background:repeating-linear-gradient(0deg,transparent,transparent 3px,rgba(0,0,0,.08) 3px,rgba(0,0,0,.08) 4px);
  }}

  /* Header */
  .hdr {{
    display:flex; align-items:center; justify-content:space-between;
    padding:14px 24px; background:var(--bg1);
    border-bottom:1px solid var(--border); position:sticky; top:0; z-index:100;
  }}
  .hdr-brand {{ display:flex; flex-direction:column; gap:2px; }}
  .hdr-title {{ font-size:20px; font-weight:700; letter-spacing:4px; color:#38bdf8; }}
  .hdr-sub {{ font-size:9px; letter-spacing:3px; color:var(--dim); }}
  .hdr-right {{ display:flex; gap:14px; align-items:center; }}
  .hdr-meta {{ font-size:10px; color:var(--dim); }}
  .badge {{ font-size:10px; padding:3px 10px; border-radius:3px; font-weight:700; letter-spacing:1px; }}
  .badge-live {{ color:#34d399; border:1px solid #34d399; }}
  .badge-dry  {{ color:#fb923c; border:1px solid #fb923c; }}

  /* Alert */
  .alert-banner {{
    background:rgba(248,113,113,.08); border:1px solid rgba(248,113,113,.3);
    border-radius:6px; padding:10px 16px; margin-bottom:12px;
    color:#f87171; font-size:11px; letter-spacing:1px;
  }}

  /* Stat cards */
  .stats-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:8px; margin-bottom:14px; }}
  .stat-card {{
    background:var(--bg1); border:1px solid var(--border);
    border-bottom:2px solid var(--accent,#38bdf8);
    border-radius:6px; padding:12px 14px;
  }}
  .stat-label {{ font-size:9px; letter-spacing:2px; color:var(--dim); text-transform:uppercase; margin-bottom:6px; }}
  .stat-value {{ font-size:22px; font-weight:700; letter-spacing:1px; }}
  .stat-sub {{ font-size:9px; color:var(--dim); margin-top:3px; }}

  /* Main grid */
  .main-wrap {{ padding:16px 24px; max-width:1800px; margin:0 auto; }}
  .grid-2 {{ display:grid; grid-template-columns:3fr 2fr; gap:12px; margin-bottom:12px; }}
  .grid-1-1 {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}

  /* Panel */
  .panel {{
    background:var(--bg1); border:1px solid var(--border);
    border-radius:8px; overflow:hidden; margin-bottom:12px;
  }}
  .panel-header {{
    display:flex; justify-content:space-between; align-items:center;
    padding:8px 16px; background:var(--bg2);
    border-bottom:1px solid var(--border);
  }}
  .panel-title {{ font-size:9px; letter-spacing:2px; color:var(--dim); text-transform:uppercase; }}
  .panel-badge {{ font-size:11px; font-weight:600; }}
  .panel-body {{ overflow-x:auto; }}

  /* Tables */
  table {{ width:100%; border-collapse:collapse; }}
  th {{
    padding:6px 12px; font-size:9px; letter-spacing:1.5px;
    color:var(--dim); text-align:left; text-transform:uppercase;
    background:var(--bg2); border-bottom:1px solid var(--border);
  }}
  td {{ padding:7px 12px; border-bottom:1px solid rgba(56,189,248,.06); }}
  tr:last-child td {{ border-bottom:none; }}
  tr:hover td {{ background:rgba(56,189,248,.03); }}
  .sym {{ color:#e2e8f0; font-weight:600; }}
  .num {{ font-family:'Courier New',monospace; }}
  .dim {{ color:var(--dim); font-size:11px; }}
  .good {{ color:#34d399; }}
  .bad  {{ color:#f87171; }}
  .warn {{ color:#fbbf24; }}
  .dir-long {{
    color:#34d399; border:1px solid rgba(52,211,153,.4);
    padding:1px 6px; font-size:10px; border-radius:2px;
  }}

  /* Imbalance bar */
  .imb-bar {{ height:3px; background:rgba(255,255,255,.08); border-radius:2px; margin-top:3px; }}
  .imb-fill {{ height:100%; background:#34d399; border-radius:2px; }}

  /* Symbol tags */
  .sym-tags {{ padding:12px 16px; display:flex; flex-wrap:wrap; gap:5px; }}
  .sym-tag {{
    font-size:10px; padding:2px 7px;
    border:1px solid rgba(56,189,248,.2); border-radius:2px;
    color:var(--dim); font-family:'Courier New',monospace;
  }}

  .empty {{ padding:20px 16px; color:var(--dim); font-size:12px; }}

  /* Pulse dot */
  @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.4}} }}
  .pulse {{ display:inline-block; width:6px; height:6px; background:#34d399;
            border-radius:50%; animation:pulse 2s ease-in-out infinite; margin-right:4px; }}
</style>
</head>
<body>

<div class="hdr">
  <div class="hdr-brand">
    <div class="hdr-title">⚡ BINGX SIGNAL BOT v7</div>
    <div class="hdr-sub">PROFESSIONAL EDITION · WS · CVD · BB SQUEEZE · MTF · ORDER BOOK · PARALELO</div>
  </div>
  <div class="hdr-right">
    <div class="hdr-meta">
      <span class="pulse"></span>
      Ciclo #{cycle} · {updated} · WS: <span style="color:#38bdf8">{ws_count} feeds</span>
    </div>
    {mode_badge}
  </div>
</div>

<div class="main-wrap">
  {banner}
  <div class="stats-grid">{stats}</div>

  <div class="grid-2">
    {panel("Señales de Alta Probabilidad", f"{len(s.get('signals',[]))} activas", sigs_html, "#fbbf24")}
    {panel("Order Book Depth", "muros · absorción", ob_html, "#f472b6")}
  </div>

  {panel("Posiciones Abiertas", f"{len(s.get('trades',[]))} abiertas · trailing stop activo", trades_html, "#34d399")}

  <div class="grid-1-1">
    {panel("Performance por Tipo de Señal", "", pt_html, "#a78bfa")}
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Universo Escaneado</span>
        <span class="panel-badge" style="color:#38bdf8">{len(s.get('symbols',[]))} coins</span>
      </div>
      <div class="sym-tags">{syms}</div>
    </div>
  </div>
</div>
</body>
</html>"""


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
        self._last_reset   = datetime.now(timezone.utc).date()

    async def run(self):
        mode = "DRY_RUN" if _DRY_RUN else "LIVE"
        log.info(f"Bot v7 PROFESSIONAL iniciado — {mode} (health :{_PORT})")

        try:
            await self.client.initialize()
            log.info("✅ Conexión BingX verificada — Balance FUTUROS OK")
        except Exception as e:
            log.error(f"❌ Fallo inicialización: {e}")
            await self.client.notify(f"❌ <b>Error inicio Bot v7</b>\n{e}")
            return

        await self.client.notify(
            f"⚡ <b>Bot v7 Professional iniciado</b> — {mode}\n"
            f"Scan: {SCAN_INTERVAL}s | Score mínimo: {MIN_SCORE}\n"
            f"Max trades: 3 | Leverage dinámico ✅\n"
            f"Balance futuros verificado ✅"
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
                log.error(f"Ciclo #{self._cycle}: {e}", exc_info=True)
            await asyncio.sleep(SCAN_INTERVAL)

        await self.client.close()

    async def _tick(self):
        self._cycle += 1

        # Reset diario a medianoche UTC
        today = datetime.now(timezone.utc).date()
        if today != self._last_reset:
            self.trade_manager.reset_daily()
            self._last_reset = today
            log.info("📅 Reset diario de stats")

        log.info(f"━━ Ciclo #{self._cycle} ━━")

        # 1. Escanear mercado
        hot = await self.scanner.get_hot_symbols(top_n=TOP_N, min_volume=MIN_VOL)

        if not hot:
            log.warning("  ⚠️ Sin símbolos calientes — ajusta MIN_VOLUME_USDT")
            _STATE["html"] = _render({
                "symbols": [], "signals": [], "trades": [],
                "cycle": self._cycle, "updated": _ts(),
                "daily": self.trade_manager.get_daily_stats(),
                "perf":  self.trade_manager.get_performance(),
                "ob": {}, "latency_ms": self.client._latency_ms,
                "ws_prices_count": len(self.client._ws_prices),
            })
            return

        # 2. Order books en paralelo
        ob_snapshots = {}
        if hot:
            ob_snapshots = await self.ob.analyze_batch(hot)

        # 3. Evaluar señales en paralelo
        async def _eval_one(sym: str):
            async with _EVAL_SEM:
                try:
                    ob_snap = ob_snapshots.get(sym)
                    sig     = await self.signal_engine.evaluate(self.client, sym, ob_snap)
                    if sig and sig["score"] >= MIN_SCORE:
                        return sig
                except Exception as e:
                    log.debug(f"eval {sym}: {e}")
                return None

        eval_results = await asyncio.gather(*[_eval_one(s) for s in hot], return_exceptions=True)
        signals = [r for r in eval_results if isinstance(r, dict) and r]

        # Ordenar por score
        signals.sort(key=lambda x: x.get("score", 0), reverse=True)
        log.info(f"  {len(signals)} señales ≥{MIN_SCORE}pts detectadas")

        # Notificar señales TOP
        for sig in signals[:3]:
            await _send_signal_alert(self.client, sig)

        # 4. Gestión de trades abiertos
        await self.trade_manager.manage_open_trades()

        # 5. Abrir nuevos trades
        if AUTO_TRADING:
            for sig in signals:
                opened = await self.trade_manager.open_trade(sig)
                if opened:
                    log.info(f"  ✅ Trade abierto: {sig['symbol']} score={sig['score']:.0f}")

        # 6. Recopilar stats
        trades = await self.trade_manager.get_open_trades()
        daily  = self.trade_manager.get_daily_stats()
        perf   = self.trade_manager.get_performance()

        # 7. OB dict para dashboard
        ob_dict = {}
        for sym in hot[:15]:
            snap = ob_snapshots.get(sym)
            if snap:
                ob_dict[sym] = {
                    "imbalance_ratio":   snap.imbalance_ratio,
                    "bias":              snap.bias,
                    "bid_walls":         [{"p": w.price, "q": w.qty} for w in snap.bid_walls],
                    "ask_walls":         [{"p": w.price, "q": w.qty} for w in snap.ask_walls],
                    "bid_delta_pct":     snap.bid_delta_pct,
                    "ask_delta_pct":     snap.ask_delta_pct,
                    "absorption_signal": snap.absorption_signal,
                    "spread_pct":        snap.spread_pct,
                }

        # 8. Actualizar dashboard
        _STATE["html"] = _render({
            "symbols":         hot,
            "signals":         signals,
            "trades":          trades,
            "cycle":           self._cycle,
            "updated":         _ts(),
            "daily":           daily,
            "perf":            perf,
            "ob":              ob_dict,
            "latency_ms":      self.client._latency_ms,
            "ws_prices_count": len(self.client._ws_prices),
        })


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


if __name__ == "__main__":
    log.info(f"Health server en 0.0.0.0:{_PORT}")
    bot = TradingBot()
    asyncio.run(bot.run())
