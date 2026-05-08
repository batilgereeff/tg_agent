import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import database as db
from bot import router
from scheduler import check_deadlines, morning_digest, evening_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    if not config.TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN не задан в .env"); sys.exit(1)
    if not config.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY не задан в .env"); sys.exit(1)
    if not config.ADMIN_ID:
        logger.error("ADMIN_ID не задан в .env"); sys.exit(1)

    await db.init_db()
    logger.info("БД: %s", config.DATABASE_PATH)

    bot = Bot(token=config.TELEGRAM_TOKEN, default=DefaultBotProperties())

    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_deadlines, "interval", minutes=5, args=[bot],
                      id="deadline_check", max_instances=1)
    scheduler.add_job(morning_digest, CronTrigger(hour=9, minute=0), args=[bot],
                      id="morning_digest", max_instances=1)
    scheduler.add_job(evening_summary, CronTrigger(hour=18, minute=0), args=[bot],
                      id="evening_summary", max_instances=1)
    scheduler.start()
    logger.info("Scheduler: deadline check 5 мин | утром 09:00 | вечером 18:00")

    dp = Dispatcher()
    dp.include_router(router)

    me = await bot.get_me()
    logger.info("Бот: @%s | Модель: %s | Админ: %d", me.username, config.CLAUDE_MODEL, config.ADMIN_ID)

    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
