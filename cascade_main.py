"""
Liquidation Cascade Bot v1.0 — Main
══════════════════════════════════════════════════════════════════════════════
Bot independiente en la cuenta BingX de renewed-love (400 USDT).
NO comparte cuenta con joyful-art ni zesty.

Deploy: Railway independiente, repo independiente.
Config:  cascade_config.py → renombrar a config.py en el repo del bot.

Diferencias vs main.py de joyful-art:
  - Importa cascade_scanner.scan_loop en vez de scanner.scan_loop
  - Banner muestra parámetros específicos de cascade
  - Sin complement_engine ni copier_client (bot autónomo)
  - Sin harvest scan (innecesario — el bot YA ES una estrategia de FR)
══════════════════════════════════════════════════════════════════════════════
"""
import asyncio
import logging
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

import config as C
from bingx_client import BingXClient
from risk_manager import RiskManager
from position_manager import PositionManager
from cascade_scanner import scan_loop
import telegram_client as tg
from trade_journal import TradeJournal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-16s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("cascade_main")

client:  BingXClient     = None
risk:    RiskManager     = None
pos_mgr: PositionManager = None
journal: TradeJournal    = None


async def _run_scanner():
    try:
        await scan_loop(client, risk, pos_mgr, journal=journal)
    except Exception as e:
        log.critical("Cascade scanner crash: %s", e, exc_info=True)
        await tg.notify_error("cascade_scanner_crash", str(e))


async def _run_monitor():
    if C.MODE == "LIVE":
        try:
            await pos_mgr.monitor_loop()
        except Exception as e:
            log.critical("Cascade monitor crash: %s", e, exc_info=True)
            await tg.notify_error("cascade_monitor_crash", str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, risk, pos_mgr, journal

    log.info("═" * 60)
    log.info("  💥 LIQUIDATION CASCADE BOT v1.0")
    log.info("  Modo: %s | Capital: %.0f USDT", C.MODE, C.CAPITAL)
    log.info("  Leverage: %dx | Max posiciones: %d",
             C.LEVERAGE, C.MAX_OPEN_TRADES)
    log.info("  CASCADE_MIN_SCORE: %.0f | CASCADE_MIN_RR: %.1f",
             getattr(C, 'CASCADE_MIN_SCORE', 60),
             getattr(C, 'CASCADE_MIN_RR', 1.5))
    log.info("  CASCADE_SL_ATR: %.1f | Scan interval: %ds",
             getattr(C, 'CASCADE_SL_ATR', 1.5),
             getattr(C, 'CASCADE_SCAN_INTERVAL', 60))
    log.info("  Universo: %d símbolos top volumen",
             getattr(C, 'CASCADE_UNIVERSE', 100))
    log.info("═" * 60)

    journal  = TradeJournal()
    client   = BingXClient()
    risk     = RiskManager()
    pos_mgr  = PositionManager(client, risk, journal=journal)

    if not C.BINGX_API_KEY or not C.BINGX_SECRET_KEY:
        log.error("BINGX_API_KEY / BINGX_SECRET_KEY no configurados")
    if not C.TELEGRAM_TOKEN or not C.TELEGRAM_CHAT_ID:
        log.warning("Telegram no configurado")

    try:
        balance = await client.get_balance()
        log.info("Balance cascade account: %.4f USDT", balance)
    except Exception as e:
        log.warning("Balance no disponible: %s", e)
        balance = 0.0

    if C.MODE == "LIVE":
        try:
            await pos_mgr.reconcile_on_startup()
        except Exception as e:
            log.warning("reconcile error: %s", e)

    await tg.notify_status(risk.status(), balance, 0)

    scanner_task = asyncio.create_task(_run_scanner())
    monitor_task = asyncio.create_task(_run_monitor())
    log.info("💥 Cascade Bot iniciado (scanner + monitor)")

    yield

    scanner_task.cancel()
    monitor_task.cancel()
    if client:
        await client.close()
    log.info("Cascade Bot detenido.")


app = FastAPI(
    title="Liquidation Cascade Bot v1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "cascade_1.0", "mode": C.MODE}


@app.get("/status")
async def status():
    if risk is None:
        return JSONResponse({"error": "not_ready"}, status_code=503)
    try:
        balance = await client.get_balance()
    except Exception:
        balance = -1.0
    try:
        unrealized = await pos_mgr.get_unrealized_pnl() if pos_mgr else 0.0
    except Exception:
        unrealized = 0.0

    # Top cascades detectadas actualmente
    from oi_cascade_signal import oi_cascade_engine
    top = oi_cascade_engine.get_top_cascades(10)
    cascade_signals = {
        sym: {"score": sig.score, "direction": sig.direction,
              "fr": round(sig.fr_value * 100, 4),
              "oi_4h_pct": round(sig.oi_delta_4h * 100, 2)}
        for sym, sig in top
    }

    tracked = pos_mgr.get_tracked() if pos_mgr else {}
    return {
        "version": "cascade_1.0",
        "mode": C.MODE,
        "balance": round(balance, 4),
        "unrealized": round(unrealized, 4),
        "risk": risk.status(unrealized_pnl=unrealized),
        "open_positions": len(tracked),
        "cascade_signals": cascade_signals,
        "trades": {
            sym: {
                "direction": t.direction,
                "entry": t.entry,
                "sl": t.sl,
                "tp1": t.tp1,
                "trailing_active": t.trailing_active,
            }
            for sym, t in tracked.items()
        },
    }


@app.get("/cascades")
async def cascades():
    """Ver top cascadas detectadas actualmente."""
    from oi_cascade_signal import oi_cascade_engine
    top = oi_cascade_engine.get_top_cascades(20)
    return {
        "count": len(top),
        "signals": [
            {
                "symbol": sym,
                "score": sig.score,
                "direction": sig.direction,
                "fr_pct": round(sig.fr_value * 100, 4),
                "oi_4h_pct": round(sig.oi_delta_4h * 100, 2),
                "reason": sig.reason,
            }
            for sym, sig in top
        ],
    }


@app.get("/journal")
async def journal_stats():
    if journal is None:
        return JSONResponse({"error": "not_ready"}, status_code=503)
    return journal.stats()


@app.post("/close/{symbol}")
async def close_symbol(symbol: str):
    if C.MODE != "LIVE":
        raise HTTPException(400, "Solo en modo LIVE")
    symbol = symbol.upper()
    if not pos_mgr.is_trading(symbol):
        raise HTTPException(404, f"{symbol} sin posición")
    await pos_mgr.close_position_emergency(symbol, reason="manual_close")
    return {"status": "ok", "symbol": symbol}


if __name__ == "__main__":
    uvicorn.run("cascade_main:app", host="0.0.0.0", port=C.PORT,
                log_level="info", access_log=False)
