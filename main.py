"""Punto de entrada único para Railway. TASK=examen (backtest, una vez) · TASK=bot (bucle diario)."""
import os

task = os.getenv("TASK", "examen").strip().strip('"').strip("'").lower()
if task in ("examen", "backtest", "test"):
    import backtest
    backtest.main([])
else:
    import bot
    bot.main()
