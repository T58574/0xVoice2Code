"""Entry point: python -m app"""

import asyncio
import logging

from aiogram import Bot, Dispatcher

from .config import TELEGRAM_BOT_TOKEN, GROQ_API_KEY, TELEGRAM_USER_ID
from . import db
from . import scheduler
from .handlers import router

log = logging.getLogger(__name__)


async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN is not set in .env")
        return
    if not GROQ_API_KEY:
        log.error("GROQ_API_KEY is not set in .env")
        return

    await db.init_db()

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    if TELEGRAM_USER_ID:
        await scheduler.start_scheduler(bot, TELEGRAM_USER_ID)

    log.info("Bot started. Listening for voice messages...")
    try:
        await dp.start_polling(bot)
    finally:
        await scheduler.stop_scheduler()
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
