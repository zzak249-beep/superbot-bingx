"""
Signal Projection Explorer Bot v3 — BingX Auto Trader
======================================================
FIX DEFINITIVO HEALTHCHECK RAILWAY:
  Un HTTPServer de stdlib arranca en un hilo daemon en las primeras
  líneas del módulo, antes de cualquier import pesado.
  Railway ve el puerto respondiendo en < 100 ms.

  El servidor sirve el dashboard HTML completo (sin aiohttp).
"""

# ============================================================
# ⚡ HEALTH SERVER — PRIMERA COSA QUE SE EJECUTA
# Usa solo stdlib: no hay dependencias que puedan fallar.
# ============================================================
import threading
import os as _os
from http.server import HTTPServer, BaseHTTPRequestHandler

_PORT    = int(_os.getenv("PORT", "8080"))
_DRY_RUN = _os.getenv("DRY_RUN", "false").lower() == "true"

_STATE = {
    "html": (
        "<html><body style='background:#080b10;color:#00ff88;"
        "font-family:monospace;padding:40px'>"
        "<h2>⚡ BingX Bot</h2><p>Iniciando...</p></body></html>"
    ),
    "cycle": 0,
}

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = _STATE["html"].encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # silenciar logs del servidor stdlib

_srv = HTTPServer(("0.0.0.0", _PORT), _Handler)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
# ============================================================

# Ahora importamos el resto
import asyncio
import logging
import signal
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

# ---- Logging -----------------------------------------------------------
_os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers = [
        logging.FileHandler("logs/bot.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("BOT")

AUTO_TRADING  = _os.getenv("AUTO_TRADING_ENABLED", "true").lower() == "true"
SCAN_INTERVAL = int(_os.getenv("SCAN_INTERVAL_SEC", "60"))


# ---- Dashboard HTML renderer ------------------------------------------- #
def _render(state: dict) -> str:
    s   = state
    d   = s.get("daily", {})
    DRY = _os.getenv("DRY_RUN", "false").lower() == "true"
    mode_badge = (
        '<span style="border:1px solid #ff6600;color:#ff6600;padding:2px 8px;'
        'font-size:11px">DRY RUN</span>'
        if DRY else
        '<span style="border:1px solid #00ff88;color:#00ff88;padding:2px 8px;'
        'font-size:11px">LIVE</span>'
    )

    total_pbr = sum(t.get("pbr", {}).get("cumulative_reward", 0)
                    for t in s.get("trades", []))
    pbr_col   = "#00ff88" if total_pbr >= 0 else "#ff3355"

    def stat(label, val, col="#00ff88"):
        return (
            f'<div style="background:#0d1117;border:1px solid #1a2535;'
            f'border-radius:6px;padding:14px 18px;border-bottom:2px solid {col}">'
            f'<div style="font-size:10px;letter-spacing:2px;color:#4a6070;'
            f'text-transform:uppercase;margin-bottom:6px">{label}</div>'
            f'<div style="font-family:monospace;font-size:22px;'
            f'font-weight:700;color:{col}">{val}</div>'
            f'</div>'
        )

    stats = (
        stat("Escaneados",    len(s.get("symbols", [])), "#00ccff") +
        stat("Senales",       len(s.get("signals", [])), "#ffcc00") +
        stat("Trades Abiertos", len(s.get("trades", [])), "#00ff88") +
        stat("Trades Hoy",    d.get("trades_opened", 0), "#cc44ff") +
        stat("PBR Acum.",     f"{total_pbr:+.5f}",       pbr_col)
    )

    TH = "padding:7px 12px;font-size:10px;letter-spacing:1px;color:#4a6070;text-align:left;text-transform:uppercase"
    TD = "padding:7px 12px;font-family:monospace;font-size:11px;border-bottom:1px solid rgba(26,37,53,.8)"

    # ---- Signals table ------------------------------------------------
    if s.get("signals"):
        rows = ""
        for sg in s["signals"]:
            rr    = sg.get("risk_reward", 0)
            rrc   = "#00ff88" if rr >= 2 else ("#ffcc00" if rr >= 1.5 else "#ff3355")
            ob    = sg.get("ob_bias", "?")
            obc   = "#00ff88" if ob == "BULLISH" else ("#ff3355" if ob == "BEARISH" else "#ffcc00")
            dirc  = "#00ff88" if sg["direction"] == "LONG" else "#ff3355"
            rows += (
                f'<tr>'
                f'<td style="{TD};color:#e0e0e0">{sg["symbol"]}</td>'
                f'<td style="{TD}"><span style="color:{dirc};border:1px solid {dirc};'
                f'padding:1px 6px;font-size:10px">{sg["direction"]}</span></td>'
                f'<td style="{TD};color:#00ff88">{sg["mean_pnl"]*100:+.2f}%</td>'
                f'<td style="{TD};color:#ff3355">{sg["worst_pnl"]*100:+.2f}%</td>'
                f'<td style="{TD};color:{rrc}">{rr:.2f}</td>'
                f'<td style="{TD};color:{obc}">{ob}</td>'
                f'<td style="{TD};color:#4a6070">{sg["signal_count"]}</td>'
                f'<td style="{TD};color:#4a6070">{sg.get("signal_type","?")}</td>'
                f'</tr>'
            )
        sigs_html = (
            '<table style="width:100%;border-collapse:collapse">'
            '<tr style="background:#131920">'
            + "".join(f'<th style="{TH}">{h}</th>'
                      for h in ["Simbolo","Dir","Mean","Worst","R:R","OB","N","Tipo"])
            + f'</tr>{rows}</table>'
        )
    else:
        sigs_html = '<div style="padding:20px;color:#4a6070">Sin senales en este ciclo</div>'

    # ---- Trades table -------------------------------------------------
    if s.get("trades"):
        rows = ""
        for t in s["trades"]:
            pbr  = t.get("pbr", {})
            cr   = pbr.get("cumulative_reward", 0)
            crc  = "#00ff88" if cr >= 0 else "#ff3355"
            sh   = pbr.get("sharpe", 0)
            tp1h = "✅" if t.get("tp1_hit") else "⏳"
            dirc = "#00ff88" if t["direction"] == "LONG" else "#ff3355"
            rows += (
                f'<tr>'
                f'<td style="{TD};color:#e0e0e0">{t["symbol"]}</td>'
                f'<td style="{TD}"><span style="color:{dirc};border:1px solid {dirc};'
                f'padding:1px 6px;font-size:10px">{t["direction"]}</span></td>'
                f'<td style="{TD};color:#4a6070">{t["entry"]}</td>'
                f'<td style="{TD};color:#ffcc00">{t.get("tp1","—")} {tp1h}</td>'
                f'<td style="{TD};color:#00ff88">{t.get("tp","—")}</td>'
                f'<td style="{TD};color:#ff3355">{t["sl"]}</td>'
                f'<td style="{TD};color:#4a6070">{str(t.get("opened_at",""))[:16]}</td>'
                f'<td style="{TD};color:{crc}">{cr:.5f}</td>'
                f'<td style="{TD};color:{"#00ff88" if sh>=0 else "#ff3355"}">{sh:.3f}</td>'
                f'</tr>'
            )
        trades_html = (
            '<table style="width:100%;border-collapse:collapse">'
            '<tr style="background:#131920">'
            + "".join(f'<th style="{TH}">{h}</th>'
                      for h in ["Simbolo","Dir","Entry","TP1","TP2","SL","Abierto","PBR Cum.","Sharpe"])
            + f'</tr>{rows}</table>'
        )
    else:
        trades_html = '<div style="padding:20px;color:#4a6070">Sin trades abiertos</div>'

    # ---- OB table -----------------------------------------------------
    ob_rows = ""
    for sym, snap in s.get("ob", {}).items():
        if not snap:
            continue
        imb  = snap.get("imbalance_ratio", 1.0)
        bias = snap.get("bias", "NEUTRAL")
        bw   = len(snap.get("bid_walls", []))
        aw   = len(snap.get("ask_walls", []))
        sp   = snap.get("spread_pct", 0)
        bc   = "#00ff88" if bias == "BULLISH" else ("#ff3355" if bias == "BEARISH" else "#ffcc00")
        fill = min(100, int(imb / 2.0 * 100))
        ob_rows += (
            f'<tr>'
            f'<td style="{TD};color:#e0e0e0">{sym}</td>'
            f'<td style="{TD};color:{bc}">{bias}</td>'
            f'<td style="{TD};color:#e0e0e0">{imb:.2f}'
            f'<div style="height:3px;background:#1a2535;border-radius:2px;margin-top:3px">'
            f'<div style="width:{fill}%;height:100%;background:#00ff88;border-radius:2px"></div>'
            f'</div></td>'
            f'<td style="{TD};color:#00ff88">B:{bw}</td>'
            f'<td style="{TD};color:#ff3355">A:{aw}</td>'
            f'<td style="{TD};color:#4a6070">{sp:.3f}%</td>'
            f'</tr>'
        )
    ob_html = (
        (
            '<table style="width:100%;border-collapse:collapse">'
            '<tr style="background:#131920">'
            + "".join(f'<th style="{TH}">{h}</th>'
                      for h in ["Simbolo","Bias","Imbalance","Bid W.","Ask W.","Spread"])
            + f'</tr>{ob_rows}</table>'
        ) if ob_rows else
        '<div style="padding:20px;color:#4a6070">Analizando order books...</div>'
    )

    syms = " ".join(
        f'<span style="font-family:monospace;font-size:10px;padding:2px 7px;'
        f'border:1px solid #1a2535;border-radius:3px;color:#4a6070">{sym}</span>'
        for sym in s.get("symbols", [])
    ) or "—"

    def panel(title, badge, content):
        return (
            f'<div style="background:#0d1117;border:1px solid #1a2535;'
            f'border-radius:8px;overflow:hidden;margin-bottom:16px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:9px 16px;background:#131920;border-bottom:1px solid #1a2535">'
            f'<span style="font-size:11px;letter-spacing:2px;color:#4a6070;'
            f'text-transform:uppercase">{title}</span>'
            f'<span style="font-family:monospace;font-size:11px;color:#00ff88">{badge}</span>'
            f'</div>{content}</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8"><meta http-equiv="refresh" content="20">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BingX Signal Bot</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#080b10;color:#c8d8e8;font-family:'Exo 2',monospace;font-size:14px}}
body::before{{content:"";position:fixed;inset:0;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,255,136,.012) 2px,rgba(0,255,136,.012) 4px);pointer-events:none;z-index:999}}
tr:hover td{{background:rgba(0,255,136,.025)}}
</style>
</head><body>
<div style="display:flex;align-items:center;justify-content:space-between;padding:14px 24px;border-bottom:1px solid #1a2535;background:#0d1117;position:sticky;top:0;z-index:100">
  <div>
    <div style="font-size:20px;font-weight:800;letter-spacing:4px;color:#00ff88;text-shadow:0 0 20px rgba(0,255,136,.4)">⚡ BINGX SIGNAL BOT</div>
    <div style="font-family:monospace;font-size:10px;color:#4a6070;letter-spacing:2px">SIGNAL PROJECTION EXPLORER v3</div>
  </div>
  <div style="display:flex;gap:14px;align-items:center">
    <span style="font-family:monospace;font-size:11px;color:#4a6070">Ciclo #{s.get("cycle",0)} · {s.get("updated","—")}</span>
    {mode_badge}
  </div>
</div>
<div style="padding:20px 24px;max-width:1600px;margin:0 auto">
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px">{stats}</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:0">
    {panel("Senales Activas", f"{len(s.get('signals',[]))} senales", sigs_html)}
    {panel("Order Book Institucional", "muros liquidez", ob_html)}
  </div>
  {panel("Trades Abiertos", f"{len(s.get('trades',[]))} posiciones", trades_html)}
  <div style="background:#0d1117;border:1px solid #1a2535;border-radius:8px;overflow:hidden">
    <div style="padding:9px 16px;background:#131920;border-bottom:1px solid #1a2535">
      <span style="font-size:11px;letter-spacing:2px;color:#4a6070;text-transform:uppercase">Universo Escaneado</span>
    </div>
    <div style="padding:12px 16px;display:flex;flex-wrap:wrap;gap:6px">{syms}</div>
  </div>
</div></body></html>"""


# ---- Bot principal ----------------------------------------------------- #
class TradingBot:
    def __init__(self):
        self.client        = BingXClient(
            api_key    = _os.environ["BINGX_API_KEY"],
            api_secret = _os.environ["BINGX_API_SECRET"],
        )
        self.ob_analyzer   = OrderBookAnalyzer(self.client)
        self.scanner       = MarketScanner(self.client, self.ob_analyzer)
        self.signal_engine = SignalEngine(self.ob_analyzer)
        self.trade_manager = TradeManager(self.client)
        self.running       = False
        self._cycle_count  = 0
        self._rl_train_every = int(_os.getenv("RL_TRAIN_EVERY", "0"))
        self._rl_symbol      = _os.getenv("RL_TRAIN_SYMBOL", "BTC-USDT")

    async def run(self):
        mode = "DRY_RUN" if _DRY_RUN else "LIVE"
        log.info(f"Bot iniciado — {mode}  (health server en :{_PORT})")

        try:
            await self.client.initialize()
            log.info("BingX inicializado")
        except Exception as e:
            log.error(f"Error initialize(): {e}")

        if AUTO_TRADING and not _DRY_RUN:
            await self.client.notify(f"Bot iniciado — {mode}\nScan: {SCAN_INTERVAL}s")

        self.running = True
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._shutdown)
            except NotImplementedError:
                pass

        while self.running:
            try:
                await self._cycle()
            except Exception as e:
                log.error(f"Error en ciclo: {e}", exc_info=True)
            await asyncio.sleep(SCAN_INTERVAL)

        await self.client.close()

    def _shutdown(self):
        log.info("Apagado…")
        self.running = False

    async def _cycle(self):
        self._cycle_count += 1
        log.info(f"Ciclo #{self._cycle_count}")

        hot_symbols = await self.scanner.get_hot_symbols(
            top_n      = int(_os.getenv("TOP_SYMBOLS",       "30")),
            min_volume = float(_os.getenv("MIN_VOLUME_USDT", "500000")),
        )

        signals = []
        for sym in hot_symbols:
            try:
                ob_snap = await self.ob_analyzer.analyze(sym)
                sig = await self.signal_engine.evaluate(self.client, sym, ob_snap)
                if sig:
                    signals.append(sig)
                    log.info(
                        f"SENAL {sig['direction']} {sym}  "
                        f"mean={sig['mean_pnl']:.2%}  R:R={sig.get('risk_reward',0):.2f}"
                    )
            except Exception as e:
                log.warning(f"Error {sym}: {e}")

        await self.trade_manager.manage_open_trades()

        if AUTO_TRADING:
            for sig in signals:
                await self.trade_manager.open_trade(sig)

        open_trades = await self.trade_manager.get_open_trades()
        daily_stats = self.trade_manager.get_daily_stats()

        # Serializar OB para el renderer
        ob_dict = {}
        for sym in hot_symbols[:10]:
            snap = self.ob_analyzer.get_cached(sym)
            if snap:
                ob_dict[sym] = {
                    "imbalance_ratio": snap.imbalance_ratio,
                    "bias":            snap.bias,
                    "bid_walls":       [{"price": w.price, "strength": w.strength}
                                        for w in snap.bid_walls],
                    "ask_walls":       [{"price": w.price, "strength": w.strength}
                                        for w in snap.ask_walls],
                    "spread_pct":      snap.spread_pct,
                }

        _STATE["cycle"] = self._cycle_count
        _STATE["html"]  = _render({
            "symbols": hot_symbols,
            "signals": signals,
            "trades":  open_trades,
            "cycle":   self._cycle_count,
            "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "daily":   daily_stats,
            "ob":      ob_dict,
        })

        if RL_AVAILABLE and self._rl_train_every > 0 \
                and self._cycle_count % self._rl_train_every == 0:
            await self._run_rl_training()

    async def _run_rl_training(self):
        try:
            klines = await self.client.get_klines(self._rl_symbol, "1h", limit=1000)
            if len(klines) < 300:
                return
            split = int(len(klines) * 0.8)
            klines_to_csv(klines[:split], "logs/training.csv")
            klines_to_csv(klines[split:], "logs/evaluation.csv")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: run_training("logs/training.csv", "logs/evaluation.csv",
                                     int(_os.getenv("RL_ITERATIONS", "5")))
            )
        except Exception as e:
            log.error(f"RL error: {e}", exc_info=True)


# ------------------------------------------------------------------ #
if __name__ == "__main__":
    log.info(f"Health server stdlib activo en 0.0.0.0:{_PORT}")
    bot = TradingBot()
    asyncio.run(bot.run())
