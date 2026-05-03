import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import config
import database as db
from bot import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    if not config.TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN не задан в .env")
        sys.exit(1)
    if not config.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY не задан в .env")
        sys.exit(1)

    await db.init_db()
    logger.info("База данных инициализирована: %s", config.DATABASE_PATH)

    bot = Bot(
        token=config.TELEGRAM_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    me = await bot.get_me()
    logger.info("Бот запущен: @%s (id=%d)", me.username, me.id)
    logger.info("Модель Claude: %s | Макс. итераций tool use: %d", config.CLAUDE_MODEL, config.MAX_TOOL_ITERATIONS)

    try:
        await dp.start_polling(bot, allowed_updates=["message"])
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
