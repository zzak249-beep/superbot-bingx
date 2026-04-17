#!/usr/bin/env python3
"""
SuperBot v5.0 — Simons Quant Edition
Entry point para Railway / Docker
"""

import asyncio
import logging
from bot import SuperBot

log = logging.getLogger('main')


async def main():
    bot = SuperBot()
    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("👋 SuperBot v5.0 terminado")
