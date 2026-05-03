import logging
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.enums import ChatAction, ParseMode

import database as db
from agent import run_agent

logger = logging.getLogger(__name__)

router = Router()

# In-memory per-user conversation history: {user_id: [{"role": ..., "content": str}]}
# Stores only plain text pairs (user + assistant) — no tool blocks.
_histories: dict[int, list[dict]] = {}
_MAX_HISTORY_MESSAGES = 20  # max stored messages per user (10 exchanges)

HELP_TEXT = """*Доступные команды:*

/start — Приветствие и ваш Telegram ID
/help  — Это сообщение
/clear — Очистить историю диалога
/id    — Показать ваш Telegram ID

*Что я умею:*
• *CRM* — добавлять и искать контакты, создавать и обновлять сделки
• *Заявки* — создавать тикеты, назначать исполнителей, менять статусы
• *Рассылки* — управлять группами и отправлять сообщения участникам

*Примеры запросов:*
— Добавь контакт Иван Петров, тел +7 999 123-45-67, компания ООО "Ромашка"
— Найди контакты из компании "Ромашка"
— Создай сделку "Поставка оборудования" на 500 000 руб для контакта #1
— Создай заявку: сервер не отвечает, приоритет критический, исполнитель @devops
— Покажи все открытые заявки
— Создай группу рассылки "vip_clients" и добавь в неё меня
— Отправь рассылку в группу "vip_clients": Акция только для вас!"""


def _get_history(user_id: int) -> list[dict]:
    return _histories.get(user_id, [])


def _save_history(user_id: int, user_msg: str, assistant_msg: str) -> None:
    history = _histories.get(user_id, [])
    history.extend([
        {"role": "user", "content": user_msg},
        {"role": "assistant", "content": assistant_msg},
    ])
    if len(history) > _MAX_HISTORY_MESSAGES:
        history = history[-_MAX_HISTORY_MESSAGES:]
    _histories[user_id] = history


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = message.from_user
    await db.register_user(user.id, user.username, user.first_name)
    name = user.first_name or user.username or "пользователь"
    await message.answer(
        f"Привет, *{name}*\\! Я ваш бизнес\\-ассистент с доступом к CRM, заявкам и рассылкам\\.\n\n"
        f"Ваш Telegram ID: `{user.id}`\n\n"
        "Просто напишите что вам нужно, или /help для списка возможностей\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


@router.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    _histories.pop(message.from_user.id, None)
    await message.answer("История диалога очищена.")


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(
        f"Ваш Telegram ID: `{message.from_user.id}`",
        parse_mode=ParseMode.MARKDOWN,
    )


@router.message()
async def handle_message(message: Message, bot: Bot) -> None:
    if not message.text:
        await message.answer("Пожалуйста, отправьте текстовое сообщение.")
        return

    user = message.from_user
    await db.register_user(user.id, user.username, user.first_name)

    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    history = _get_history(user.id)
    try:
        response = await run_agent(history, message.text, bot=bot)
    except Exception as exc:
        logger.error("Agent error for user %d: %s", user.id, exc, exc_info=True)
        await message.answer("Произошла ошибка при обработке запроса. Попробуйте ещё раз.")
        return

    _save_history(user.id, message.text, response)

    # Telegram message limit is 4096 chars
    for chunk_start in range(0, len(response), 4096):
        await message.answer(response[chunk_start:chunk_start + 4096])
