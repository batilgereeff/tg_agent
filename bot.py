import json
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.enums import ChatAction

import config
import database as db
import state
from agent import run_agent
from tools import execute_tool, fmt_deadline

logger = logging.getLogger(__name__)
router = Router()

# Per-user conversation history
_histories: dict[int, list[dict]] = {}
_MAX_HISTORY = 20

# ── Persistent reply keyboards ─────────────────────────────────────────────────

_ADMIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Новая задача"),      KeyboardButton(text="Статистика")],
        [KeyboardButton(text="Список сотрудников"), KeyboardButton(text="Мои задачи")],
    ],
    resize_keyboard=True,
)

_EMPLOYEE_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Мои задачи"), KeyboardButton(text="Помощь")]],
    resize_keyboard=True,
)

_REMOVE_KB = ReplyKeyboardRemove()

# ── Inline keyboard builders ───────────────────────────────────────────────────

def _confirm_kb(admin_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да ✓",       callback_data=f"tc:yes:{admin_id}"),
        InlineKeyboardButton(text="Изменить ✎", callback_data=f"tc:edit:{admin_id}"),
        InlineKeyboardButton(text="Отмена ✗",   callback_data=f"tc:cancel:{admin_id}"),
    ]])


def _review_kb(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Подтвердить ✓",    callback_data=f"tr:ok:{task_id}"),
        InlineKeyboardButton(text="Вернуть в работу ↩", callback_data=f"tr:reject:{task_id}"),
    ]])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_history(uid: int) -> list:
    return _histories.get(uid, [])


def _save_history(uid: int, user_msg: str, bot_msg: str) -> None:
    h = _histories.get(uid, [])
    h += [{"role": "user", "content": user_msg}, {"role": "assistant", "content": bot_msg}]
    _histories[uid] = h[-_MAX_HISTORY:]


def _role_kb(is_admin: bool) -> ReplyKeyboardMarkup:
    return _ADMIN_KB if is_admin else _EMPLOYEE_KB


# ── Task creation helpers ──────────────────────────────────────────────────────

async def _do_create_task(uid: int, pending: dict, bot: Bot) -> str:
    """Create confirmed task in DB, notify employee, return result text."""
    state.clear_pending(uid)
    task = await db.create_task(
        employee_id=pending["employee_id"],
        description=pending["description"],
        deadline=pending.get("deadline"),
        priority=pending.get("priority", "normal"),
    )
    notified = False
    if pending.get("employee_telegram_id"):
        dl = fmt_deadline(pending.get("deadline"))
        try:
            await bot.send_message(
                pending["employee_telegram_id"],
                f"Вам назначена задача #{task['id']}\n\n"
                f"{pending['description']}"
                + (f"\nДедлайн: {dl}" if dl else ""),
            )
            notified = True
        except Exception as e:
            logger.warning("Notify employee failed: %s", e)

    return (
        f"Задача #{task['id']} создана.\n\n"
        f"Сотрудник: {pending['employee_name']}\n"
        f"Задача: {pending['description']}\n"
        + ("Уведомление отправлено сотруднику." if notified
           else "Сотрудник ещё не зарегистрирован, уведомление не отправлено.")
    )


# ── /start ─────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    args = message.text.split(maxsplit=1)
    reg_code = args[1].strip() if len(args) > 1 else None
    user = message.from_user

    if user.id == config.ADMIN_ID:
        await message.answer(
            f"Добро пожаловать, администратор.\nВаш ID: {user.id}",
            reply_markup=_ADMIN_KB,
        )
        return

    if reg_code:
        result = await db.register_employee(reg_code, user.id)
        if result["ok"]:
            _histories.pop(user.id, None)
            await message.answer(
                f"Вы зарегистрированы как {result['name']}.\n\n"
                "Напишите Мои задачи чтобы увидеть свои задания.",
                reply_markup=_EMPLOYEE_KB,
            )
        elif result["reason"] == "not_found":
            await message.answer("Код не найден. Уточните у администратора.")
        elif result["reason"] == "already_registered":
            await message.answer(
                f"Аккаунт {result['name']} уже зарегистрирован.",
                reply_markup=_EMPLOYEE_KB,
            )
        return

    employee = await db.get_employee_by_telegram_id(user.id)
    if employee:
        await message.answer(
            f"С возвращением, {employee['name']}.",
            reply_markup=_EMPLOYEE_KB,
        )
    else:
        await message.answer(
            f"Ваш Telegram ID: {user.id}\n\n"
            "Для работы нужен код регистрации от администратора.\n"
            "Введите: /start КОД"
        )


# ── /help ──────────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    is_admin = message.from_user.id == config.ADMIN_ID
    await _send_help(message, is_admin)


async def _send_help(message: Message, is_admin: bool) -> None:
    if is_admin:
        await message.answer(
            "Управление сотрудниками:\n"
            "- Добавь сотрудника Иванов — регистрация\n"
            "- Список сотрудников — все зарегистрированные\n\n"
            "Задачи:\n"
            "- Поставь Иванову задачу: описание, дедлайн завтра 18:00\n"
            "- Задачи Иванова — задачи конкретного сотрудника\n"
            "- Напомни Иванову про задачу #3\n"
            "- Подтверждаю задачу #3 — закрыть задачу\n\n"
            "Сводка:\n"
            "- Таблица — все задачи\n"
            "- Статистика — сводка по команде\n\n"
            "Автоматически:\n"
            "- 09:00 — задачи на сегодня каждому сотруднику\n"
            "- 18:00 — сводка дня тебе\n"
            "- За 1 час до дедлайна — напоминание\n"
            "- При просрочке — уведомление"
        )
    else:
        await message.answer(
            "Мои задачи — список твоих задач\n"
            "Задача #3 готова — отметить выполнение\n"
            "/id — узнать свой ID"
        )


# ── /clear ─────────────────────────────────────────────────────────────────────

@router.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    uid = message.from_user.id
    _histories.pop(uid, None)
    state.clear_pending(uid)
    state.end_new_task_mode(uid)
    is_admin = uid == config.ADMIN_ID
    await message.answer("История диалога очищена.", reply_markup=_role_kb(is_admin))


# ── /id ────────────────────────────────────────────────────────────────────────

@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: {message.from_user.id}")


# ── Callback: task confirmation (inline buttons on card) ───────────────────────

@router.callback_query(F.data.startswith("tc:"))
async def cb_task_confirm(callback: CallbackQuery, bot: Bot) -> None:
    _, action, raw_id = callback.data.split(":")
    admin_id = int(raw_id)

    if callback.from_user.id != admin_id:
        await callback.answer("Это не ваш запрос.", show_alert=True)
        return

    pending = state.get_pending(admin_id)

    if action == "yes":
        if not pending:
            await callback.answer("Запрос устарел.", show_alert=True)
            await callback.message.edit_reply_markup(reply_markup=None)
            return
        reply = await _do_create_task(admin_id, pending, bot)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(reply, reply_markup=_ADMIN_KB)
        _save_history(admin_id, "Да", reply)

    elif action == "edit":
        state.clear_pending(admin_id)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Что хотите изменить? Напишите и я пересоздам карточку.",
            reply_markup=_ADMIN_KB,
        )

    elif action == "cancel":
        state.clear_pending(admin_id)
        await callback.message.edit_reply_markup(reply_markup=None)
        reply = "Создание задачи отменено."
        await callback.message.answer(reply, reply_markup=_ADMIN_KB)
        _save_history(admin_id, "Отмена", reply)

    await callback.answer()


# ── Callback: task review (inline buttons on admin notification) ───────────────

@router.callback_query(F.data.startswith("tr:"))
async def cb_task_review(callback: CallbackQuery, bot: Bot) -> None:
    _, action, raw_id = callback.data.split(":")
    task_id = int(raw_id)

    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("Только администратор.", show_alert=True)
        return

    result_json = await execute_tool(
        "confirm_task" if action == "ok" else "reject_task",
        {"task_id": task_id},
        caller_id=config.ADMIN_ID,
        bot=bot,
    )
    data = json.loads(result_json)
    await callback.message.edit_reply_markup(reply_markup=None)

    if data.get("ok"):
        if action == "ok":
            reply = f"Задача #{task_id} подтверждена и закрыта."
        else:
            reply = f"Задача #{task_id} возвращена в работу."
    else:
        reply = data.get("error", "Ошибка при обработке.")

    await callback.message.answer(reply, reply_markup=_ADMIN_KB)
    await callback.answer()


# ── Main message handler ───────────────────────────────────────────────────────

@router.message()
async def handle_message(message: Message, bot: Bot) -> None:
    if not message.text:
        await message.answer("Отправьте текстовое сообщение.")
        return

    uid = message.from_user.id
    is_admin = uid == config.ADMIN_ID
    text = message.text.strip()

    # ── Role check ─────────────────────────────────────────────────────────────
    if not is_admin:
        emp = await db.get_employee_by_telegram_id(uid)
        if not emp:
            await message.answer(
                "Вы не зарегистрированы.\nПолучите код у администратора: /start КОД"
            )
            return
        employee_name = emp["name"]

        # Employee button: "Помощь"
        if text == "Помощь":
            await _send_help(message, is_admin=False)
            return
    else:
        employee_name = "Администратор"

    # ── Admin-only button: "Новая задача" ──────────────────────────────────────
    if is_admin and text == "Новая задача":
        state.start_new_task_mode(uid)
        await message.answer(
            "Кому назначить? Напиши имя сотрудника.",
            reply_markup=_ADMIN_KB,
        )
        return

    # ── Admin new-task wizard: receive employee name ───────────────────────────
    if is_admin and state.in_new_task_mode(uid):
        state.end_new_task_mode(uid)
        # Reframe as natural language for Claude so it can ask description/deadline
        text = f"Создай задачу для сотрудника {text!r}. Спроси у меня описание и дедлайн, потом покажи карточку."

    # ── Intercept pending confirmation (text fallback for Да/Нет/Изменить) ─────
    if is_admin:
        pending = state.get_pending(uid)
        if pending:
            token = text.lower()
            if token in {"да", "yes", "ок", "ok", "верно", "подтвердить"}:
                reply = await _do_create_task(uid, pending, bot)
                _save_history(uid, "Да", reply)
                await message.answer(reply, reply_markup=_ADMIN_KB)
                return
            if token in {"нет", "no", "отмена", "отменить", "cancel"}:
                state.clear_pending(uid)
                reply = "Создание задачи отменено."
                _save_history(uid, "Нет", reply)
                await message.answer(reply, reply_markup=_ADMIN_KB)
                return
            if token in {"изменить", "edit", "поправить"}:
                state.clear_pending(uid)
                # fall through to Claude

    # ── Claude ─────────────────────────────────────────────────────────────────
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    history = _get_history(uid)
    try:
        response = await run_agent(
            history=history,
            user_message=text,
            user_id=uid,
            is_admin=is_admin,
            employee_name=employee_name,
            bot=bot,
        )
    except Exception as exc:
        logger.error("Agent error for user %d: %s", uid, exc, exc_info=True)
        await message.answer("Произошла ошибка. Попробуйте ещё раз.", reply_markup=_role_kb(is_admin))
        return

    _save_history(uid, message.text.strip(), response)

    # If propose_task was called → show inline confirm keyboard
    has_pending = is_admin and state.get_pending(uid) is not None
    reply_kb = _confirm_kb(uid) if has_pending else _role_kb(is_admin)

    for i, start in enumerate(range(0, len(response), 4096)):
        await message.answer(
            response[start:start + 4096],
            reply_markup=reply_kb if i == 0 else None,
        )
