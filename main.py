import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN
from db.database import init_db
from handlers.commands import router as commands_router
from handlers.errors import router as errors_router
from services.tracker import tracker_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set in .env")
        return

    await init_db()
    logger.info("Database initialized")

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher()

    dp.include_router(commands_router)
    dp.include_router(errors_router)

    tracker_task = asyncio.create_task(tracker_loop(bot))

    try:
        logger.info("Starting polling")
        await dp.start_polling(bot, skip_updates=True)
    finally:
        tracker_task.cancel()
        await tracker_task
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
