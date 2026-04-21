"""
Dashboard Web — servidor aiohttp en :8080
Muestra estado en tiempo real: señales, trades abiertos, métricas
"""

import asyncio
import json
import logging
import os
from datetime import datetime

from aiohttp import web

log = logging.getLogger("DASH")


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BingX Signal Bot</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;600;700&display=swap');
  :root{--green:#00e676;--red:#ff5252;--amber:#ffb74d;--blue:#4fc3f7;--purple:#ab47bc;--bg:#0d0f14;--card:#161820;--border:#1e2230}
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--bg);color:#e0e0e0;font-family:'Rajdhani',sans-serif;font-size:15px;padding:20px}
  h1{font-size:28px;font-weight:700;color:var(--green);letter-spacing:3px;margin-bottom:4px}
  .sub{color:#555;font-family:'Share Tech Mono',monospace;font-size:12px;margin-bottom:24px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-bottom:24px}
  .card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px}
  .card h2{font-size:13px;letter-spacing:2px;color:#555;margin-bottom:12px;text-transform:uppercase}
  table{width:100%;border-collapse:collapse}
  th{text-align:left;font-size:11px;letter-spacing:1px;color:#444;padding:4px 8px;border-bottom:1px solid var(--border)}
  td{padding:5px 8px;font-family:'Share Tech Mono',monospace;font-size:12px;border-bottom:1px solid #111}
  .long{color:var(--green)} .short{color:var(--red)}
  .pos{color:var(--green)} .neg{color:var(--red)}
  .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700}
  .badge-long{background:#003d1a;color:var(--green)} .badge-short{background:#3d0a00;color:var(--red)}
  .stat{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid #111}
  .stat:last-child{border:none}
  .stat-val{font-family:'Share Tech Mono',monospace;font-size:13px}
</style>
</head>
<body>
<h1>⚡ BINGX SIGNAL BOT</h1>
<div class="sub">Actualizado: {updated} &nbsp;|&nbsp; Ciclo #{cycle} &nbsp;|&nbsp; Auto-refresh 30s</div>

<div class="grid">
  <div class="card">
    <h2>📊 Estado General</h2>
    {stats_html}
  </div>
  <div class="card">
    <h2>🔥 Señales Activas</h2>
    {signals_html}
  </div>
</div>

<div class="card" style="margin-bottom:16px">
  <h2>💼 Trades Abiertos</h2>
  {trades_html}
</div>

<div class="card">
  <h2>📡 Símbolos Escaneados</h2>
  <div style="font-family:'Share Tech Mono',monospace;font-size:12px;color:#444;line-height:1.8">
    {symbols_html}
  </div>
</div>
</body></html>"""


class Dashboard:
    def __init__(self):
        self.state = {
            "symbols": [],
            "signals": [],
            "trades":  [],
            "cycle":   0,
            "updated": "—",
        }
        self._app    = web.Application()
        self._runner = None

    async def start(self):
        self._app.router.add_get("/",        self._handle_index)
        self._app.router.add_get("/api/state", self._handle_api)
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
        await site.start()
        log.info("Dashboard en http://0.0.0.0:8080")

    async def update(self, symbols: list, signals: list, trades: list):
        self.state["symbols"] = symbols
        self.state["signals"] = signals
        self.state["trades"]  = trades
        self.state["cycle"]  += 1
        self.state["updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    # ------------------------------------------------------------------ #
    async def _handle_api(self, request):
        return web.json_response(self.state)

    async def _handle_index(self, request):
        s = self.state

        # Stats
        stats = [
            ("Símbolos escaneados", len(s["symbols"])),
            ("Señales encontradas", len(s["signals"])),
            ("Trades abiertos",     len(s["trades"])),
            ("Ciclos completados",  s["cycle"]),
        ]
        stats_html = "".join(
            f'<div class="stat"><span>{k}</span><span class="stat-val">{v}</span></div>'
            for k, v in stats
        )

        # Signals table
        if s["signals"]:
            rows = "".join(
                f'<tr><td>{sg["symbol"]}</td>'
                f'<td><span class="badge badge-{sg["direction"].lower()}">{sg["direction"]}</span></td>'
                f'<td class="pos">{sg["mean_pnl"]*100:.2f}%</td>'
                f'<td class="neg">{sg["worst_pnl"]*100:.2f}%</td>'
                f'<td>{sg["signal_count"]}</td></tr>'
                for sg in s["signals"]
            )
            signals_html = f'<table><tr><th>Símbolo</th><th>Dir</th><th>Mean</th><th>Worst</th><th>Señales</th></tr>{rows}</table>'
        else:
            signals_html = '<div style="color:#444;padding:12px">Sin señales en este ciclo</div>'

        # Trades table
        if s["trades"]:
            rows = "".join(
                f'<tr><td>{t["symbol"]}</td>'
                f'<td><span class="badge badge-{t["direction"].lower()}">{t["direction"]}</span></td>'
                f'<td>{t["entry"]}</td>'
                f'<td class="pos">{t["tp"]}</td>'
                f'<td class="neg">{t["sl"]}</td>'
                f'<td>{t["opened_at"][:16]}</td>'
                f'<td class="{"pos" if t.get("pbr",{}).get("cumulative_reward",0)>=0 else "neg"}">'
                f'{t.get("pbr",{}).get("cumulative_reward",0):.5f}</td>'
                f'<td>{t.get("pbr",{}).get("sharpe",0):.3f}</td></tr>'
                for t in s["trades"]
            )
            trades_html = (
                f'<table><tr><th>Símbolo</th><th>Dir</th><th>Entry</th>'
                f'<th>TP</th><th>SL</th><th>Abierto</th>'
                f'<th>PBR Reward</th><th>Sharpe</th></tr>{rows}</table>'
            )
        else:
            trades_html = '<div style="color:#444;padding:12px">Sin trades abiertos</div>'

        # Symbols cloud
        symbols_html = " &nbsp;·&nbsp; ".join(s["symbols"]) if s["symbols"] else "—"

        html = HTML_TEMPLATE.format(
            updated      = s["updated"],
            cycle        = s["cycle"],
            stats_html   = stats_html,
            signals_html = signals_html,
            trades_html  = trades_html,
            symbols_html = symbols_html,
        )
        return web.Response(text=html, content_type="text/html")
