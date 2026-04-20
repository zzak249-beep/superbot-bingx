#!/usr/bin/env python3
import asyncio
from bot_v2 import TradingBotV2

if __name__ == "__main__":
    bot = TradingBotV2()
    asyncio.run(bot.run())
