"""
ENTRY POINT — Railway
SATY Elite v11 — BingX + Telegram
"""
import os, sys, time, logging
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RAILWAY] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("railway")

def sync_and_validate():
    """
    Sincroniza variables de entorno ANTES de importar el bot.
    Railway puede tener los nombres con distinto prefijo.
    """
    # Sincronizar nombres alternativos
    mappings = [
        ("BINGX_API_SECRET",   "BINGX_SECRET_KEY"),
        ("TELEGRAM_BOT_TOKEN", "TG_TOKEN"),
        ("TELEGRAM_CHAT_ID",   "TG_CHAT_ID"),
        ("TG_TOKEN",           "TELEGRAM_BOT_TOKEN"),
        ("TG_CHAT_ID",         "TELEGRAM_CHAT_ID"),
    ]
    for src, dst in mappings:
        val = os.environ.get(src, "")
        if val and not os.environ.get(dst):
            os.environ[dst] = val
            log.info(f"Sincronizado: {src} → {dst}")

    log.info("─── Configuración ───────────────────────────────────")
    vars_check = [
        ("BINGX_API_KEY",      True),
        ("BINGX_API_SECRET",   True),
        ("TELEGRAM_BOT_TOKEN", False),
        ("TELEGRAM_CHAT_ID",   False),
        ("FIXED_USDT",         False),
        ("LEVERAGE",           False),
        ("MAX_OPEN_TRADES",    False),
        ("MIN_SCORE",          False),
        ("MAX_DRAWDOWN",       False),
        ("DAILY_LOSS_LIMIT",   False),
        ("MIN_VOLUME_USDT",    False),
        ("TOP_N_SYMBOLS",      False),
        ("BTC_FILTER",         False),
    ]
    errors = []
    for var, required in vars_check:
        val = os.environ.get(var, "")
        if val:
            hide    = any(k in var for k in ["KEY", "SECRET", "TOKEN"])
            display = f"***{val[-4:]}" if hide else val
            log.info(f"  ✅ {var:<22} = {display}")
        else:
            if required:
                log.error(f"  ❌ {var:<22} = NO CONFIGURADA (obligatoria)")
                errors.append(var)
            else:
                log.info(f"  ⚪ {var:<22} = (usando valor por defecto)")

    # Test Telegram
    tg_token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if tg_token and tg_chat_id:
        log.info(f"  📱 Telegram: token OK, chat_id OK")
    else:
        log.warning(f"  ⚠️  Telegram: token={'OK' if tg_token else 'FALTA'}, chat_id={'OK' if tg_chat_id else 'FALTA'}")
        log.warning("     Sin Telegram no llegarán alertas. Configura TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en Railway Variables.")

    if errors:
        log.error(f"Faltan variables obligatorias: {errors}")
        log.error("Railway → tu proyecto → Variables → Add Variable")
        sys.exit(1)

    log.info("─────────────────────────────────────────────────────")

if __name__ == "__main__":
    log.info("=" * 60)
    log.info("  SATY ELITE v11 — Railway + BingX + Telegram")
    log.info(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    log.info("=" * 60)

    # CRÍTICO: sincronizar ANTES de importar el bot
    sync_and_validate()

    log.info("Iniciando SATY Elite v11...")
    import saty_elite_v11
    saty_elite_v11.main()
