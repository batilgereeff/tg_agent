from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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
import state as _st
from agent import run_agent
from tools import execute_tool, fmt_deadline, STATUS_RU

logger = logging.getLogger(__name__)
router = Router()

_histories: dict[int, list[dict]] = {}
_MAX_HISTORY = 20


async def _safe_edit(msg, text: str | None = None, markup=None) -> None:
    """Edit message text or markup, silently ignoring Telegram 'not modified' errors."""
    try:
        if text is not None:
            await msg.edit_text(text, reply_markup=markup)
        else:
            await msg.edit_reply_markup(reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning("Edit failed: %s", e)


# ── Persistent reply keyboards ─────────────────────────────────────────────────

_ADMIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Новая задача"),       KeyboardButton(text="Статистика")],
        [KeyboardButton(text="Список сотрудников"),  KeyboardButton(text="Мои задачи")],
    ],
    resize_keyboard=True,
)

_EMPLOYEE_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Мои задачи"), KeyboardButton(text="Помощь")]],
    resize_keyboard=True,
)

_REMOVE_KB = ReplyKeyboardRemove()


# ══════════════════════════════════════════════════════════════════════════════
# FSM — Task Creation Wizard
# ══════════════════════════════════════════════════════════════════════════════

class TaskWizard(StatesGroup):
    choose_employee      = State()
    enter_description    = State()
    choose_deadline      = State()
    enter_deadline_manual = State()
    choose_priority      = State()
    choose_category      = State()
    enter_comment        = State()
    confirm              = State()
    edit_field           = State()


PRIORITY_DISPLAY = {
    "low":      "Низкий",
    "normal":   "Обычный",
    "high":     "Высокий",
    "critical": "Срочный",
}

CATEGORY_DISPLAY = {
    "repair":   "Ремонт",
    "docs":     "Документы",
    "purchase": "Закупка",
    "call":     "Звонок",
    "other":    "Другое",
}


# ── Deadline helpers ───────────────────────────────────────────────────────────

def _deadline_from_preset(preset: str) -> tuple[str | None, str]:
    now = datetime.now()
    if preset == "1h":
        dt = now + timedelta(hours=1)
    elif preset == "3h":
        dt = now + timedelta(hours=3)
    elif preset == "today18":
        dt = now.replace(hour=18, minute=0, second=0, microsecond=0)
    elif preset == "tom12":
        dt = (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    elif preset == "tom18":
        dt = (now + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    else:
        return None, "без дедлайна"
    return dt.isoformat(), dt.strftime("%d.%m.%Y %H:%M")


def _parse_manual_deadline(text: str) -> tuple[str | None, str | None]:
    try:
        dt = datetime.strptime(text.strip(), "%d.%m.%Y %H:%M")
        return dt.isoformat(), None
    except ValueError:
        return None, "Неверный формат. Попробуйте ещё раз: 15.05.2026 18:00"


# ── Wizard keyboards ───────────────────────────────────────────────────────────

_CANCEL_ROW = [InlineKeyboardButton(text="❌ Отмена", callback_data="tw:cancel")]


def _wiz_employee_kb(employees: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=e["name"], callback_data=f"tw:emp:{e['id']}")]
        for e in employees
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows + [_CANCEL_ROW])


def _wiz_cancel_only_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[_CANCEL_ROW])


def _wiz_deadline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Через 1 час",    callback_data="tw:dl:1h"),
            InlineKeyboardButton(text="Через 3 часа",   callback_data="tw:dl:3h"),
        ],
        [
            InlineKeyboardButton(text="Сегодня 18:00",  callback_data="tw:dl:today18"),
            InlineKeyboardButton(text="Завтра 12:00",   callback_data="tw:dl:tom12"),
        ],
        [
            InlineKeyboardButton(text="Завтра 18:00",   callback_data="tw:dl:tom18"),
            InlineKeyboardButton(text="Ввести вручную", callback_data="tw:dl:manual"),
        ],
        _CANCEL_ROW,
    ])


def _wiz_priority_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Низкий",  callback_data="tw:pr:low"),
            InlineKeyboardButton(text="Обычный", callback_data="tw:pr:normal"),
        ],
        [
            InlineKeyboardButton(text="Высокий", callback_data="tw:pr:high"),
            InlineKeyboardButton(text="Срочный", callback_data="tw:pr:critical"),
        ],
        _CANCEL_ROW,
    ])


def _wiz_category_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Ремонт",    callback_data="tw:cat:repair"),
            InlineKeyboardButton(text="Документы", callback_data="tw:cat:docs"),
            InlineKeyboardButton(text="Закупка",   callback_data="tw:cat:purchase"),
        ],
        [
            InlineKeyboardButton(text="Звонок",    callback_data="tw:cat:call"),
            InlineKeyboardButton(text="Другое",    callback_data="tw:cat:other"),
        ],
        _CANCEL_ROW,
    ])


def _wiz_comment_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="tw:skip_comment")],
        _CANCEL_ROW,
    ])


def _wiz_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Подтвердить ✓", callback_data="tw:confirm"),
        InlineKeyboardButton(text="Изменить ✎",    callback_data="tw:edit"),
        InlineKeyboardButton(text="Отмена ✗",      callback_data="tw:cancel"),
    ]])


def _wiz_edit_field_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сотрудник",   callback_data="tw:ef:employee")],
        [InlineKeyboardButton(text="Задача",       callback_data="tw:ef:description")],
        [InlineKeyboardButton(text="Дедлайн",     callback_data="tw:ef:deadline")],
        [InlineKeyboardButton(text="Приоритет",   callback_data="tw:ef:priority")],
        [InlineKeyboardButton(text="Категория",   callback_data="tw:ef:category")],
        [InlineKeyboardButton(text="Комментарий", callback_data="tw:ef:comment")],
        _CANCEL_ROW,
    ])


def _format_task_card(data: dict) -> str:
    deadline_str = data.get("deadline_display") or "без дедлайна"
    priority_str = PRIORITY_DISPLAY.get(data.get("priority", "normal"), "Обычный")
    category_str = CATEGORY_DISPLAY.get(data.get("category", ""), "—")
    comment_str  = data.get("comment") or "—"
    return (
        "Проверьте задачу:\n\n"
        f"Сотрудник: {data.get('employee_name', '—')}\n"
        f"Задача: {data.get('description', '—')}\n"
        f"Дедлайн: {deadline_str}\n"
        f"Приоритет: {priority_str}\n"
        f"Категория: {category_str}\n"
        f"Комментарий: {comment_str}\n\n"
        "Всё верно?"
    )


# ── Agent-flow legacy keyboards ────────────────────────────────────────────────

def _confirm_kb(admin_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да ✓",       callback_data=f"tc:yes:{admin_id}"),
        InlineKeyboardButton(text="Изменить ✎", callback_data=f"tc:edit:{admin_id}"),
        InlineKeyboardButton(text="Отмена ✗",   callback_data=f"tc:cancel:{admin_id}"),
    ]])


def _review_kb(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Подтвердить ✓",       callback_data=f"tr:ok:{task_id}"),
        InlineKeyboardButton(text="Вернуть в работу ↩",  callback_data=f"tr:reject:{task_id}"),
    ]])


# ── Misc helpers ───────────────────────────────────────────────────────────────

def _get_history(uid: int) -> list:
    return _histories.get(uid, [])


def _save_history(uid: int, user_msg: str, bot_msg: str) -> None:
    h = _histories.get(uid, [])
    h += [{"role": "user", "content": user_msg}, {"role": "assistant", "content": bot_msg}]
    _histories[uid] = h[-_MAX_HISTORY:]


def _role_kb(is_admin: bool) -> ReplyKeyboardMarkup:
    return _ADMIN_KB if is_admin else _EMPLOYEE_KB


# ── Task creation: agent flow ──────────────────────────────────────────────────

async def _do_create_task(uid: int, pending: dict, bot: Bot) -> str:
    _st.clear_pending(uid)
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


# ── Task creation: FSM wizard flow ─────────────────────────────────────────────

async def _do_create_task_fsm(data: dict, bot: Bot) -> str:
    task = await db.create_task(
        employee_id=data["employee_id"],
        title=data.get("title"),
        description=data["description"],
        deadline=data.get("deadline"),
        priority=data.get("priority", "normal"),
        category=data.get("category"),
        comment=data.get("comment"),
    )
    notified = False
    emp_tid = data.get("employee_telegram_id")
    if emp_tid:
        dl = fmt_deadline(data.get("deadline"))
        cat = CATEGORY_DISPLAY.get(data.get("category", ""), "")
        title_line = f"Название: {data['title']}\n" if data.get("title") else ""
        lines = [f"Вам назначена задача #{task['id']}\n{title_line}", data["description"]]
        if dl:                  lines.append(f"Дедлайн: {dl}")
        if cat:                 lines.append(f"Категория: {cat}")
        if data.get("comment"): lines.append(f"Комментарий: {data['comment']}")
        try:
            await bot.send_message(emp_tid, "\n".join(lines))
            notified = True
        except Exception as e:
            logger.warning("Notify employee failed: %s", e)

    title_part = f"Название: {data['title']}\n" if data.get("title") else ""
    return (
        f"Задача #{task['id']} создана.\n\n"
        f"{title_part}"
        f"Сотрудник: {data['employee_name']}\n"
        f"Задача: {data['description']}\n"
        + ("Уведомление отправлено сотруднику." if notified
           else "Сотрудник ещё не зарегистрирован, уведомление не отправлено.")
    )


# ══════════════════════════════════════════════════════════════════════════════
# Command handlers
# ══════════════════════════════════════════════════════════════════════════════

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
        await message.answer(f"С возвращением, {employee['name']}.", reply_markup=_EMPLOYEE_KB)
    else:
        await message.answer(
            f"Ваш Telegram ID: {user.id}\n\n"
            "Для работы нужен код регистрации от администратора.\n"
            "Введите: /start КОД"
        )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await _send_help(message, message.from_user.id == config.ADMIN_ID)


async def _send_help(message: Message, is_admin: bool) -> None:
    if is_admin:
        await message.answer(
            "Управление сотрудниками:\n"
            "- Добавь сотрудника Иванов — регистрация\n"
            "- Список сотрудников — все зарегистрированные\n\n"
            "Задачи:\n"
            "- Новая задача — пошаговый мастер создания\n"
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


@router.message(Command("clear"))
async def cmd_clear(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    _histories.pop(uid, None)
    _st.clear_pending(uid)
    _st.end_new_task_mode(uid)
    await state.clear()
    await message.answer("История диалога очищена.", reply_markup=_role_kb(uid == config.ADMIN_ID))


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: {message.from_user.id}")


# ══════════════════════════════════════════════════════════════════════════════
# FSM Wizard handlers  (registered BEFORE the catch-all handle_message)
# ══════════════════════════════════════════════════════════════════════════════

async def _wiz_launch(message: Message, state: FSMContext) -> None:
    """Core wizard launch logic, callable from multiple entry points."""
    await state.clear()
    try:
        employees = await db.list_employees()
    except Exception as exc:
        logger.error("wiz_start db error: %s", exc)
        await message.answer("Ошибка базы данных. Попробуйте ещё раз.", reply_markup=_ADMIN_KB)
        return
    if not employees:
        await message.answer(
            "Нет сотрудников. Сначала добавьте командой:\nДобавь сотрудника Иванов",
            reply_markup=_ADMIN_KB,
        )
        return
    await state.set_state(TaskWizard.choose_employee)
    await message.answer("Шаг 1/6 — Кому назначить?", reply_markup=_wiz_employee_kb(employees))


# ── Start: "Новая задача" button ───────────────────────────────────────────────

@router.message(F.text == "Новая задача")
async def wiz_start(message: Message, state: FSMContext) -> None:
    if message.from_user.id != config.ADMIN_ID:
        return
    await _wiz_launch(message, state)


# ── Universal cancel (inline button) ──────────────────────────────────────────

@router.callback_query(F.data == "tw:cancel")
async def wiz_cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _safe_edit(callback.message, markup=None)
    await callback.message.answer("Создание задачи отменено.", reply_markup=_ADMIN_KB)
    await callback.answer()


# ── Step 1 → 2: employee selected ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("tw:emp:"), StateFilter(TaskWizard.choose_employee))
async def wiz_cb_employee(callback: CallbackQuery, state: FSMContext) -> None:
    emp_id = int(callback.data.split(":")[-1])
    emp = await db.get_employee_by_id(emp_id)
    if not emp:
        await callback.answer("Сотрудник не найден.", show_alert=True)
        return
    await state.update_data(
        employee_id=emp["id"],
        employee_name=emp["name"],
        employee_telegram_id=emp.get("telegram_id"),
    )
    await _safe_edit(callback.message, markup=None)
    if (await state.get_data()).get("editing"):
        await state.set_state(TaskWizard.confirm)
        await callback.message.answer(_format_task_card(await state.get_data()), reply_markup=_wiz_confirm_kb())
    else:
        await state.set_state(TaskWizard.enter_description)
        await callback.message.answer(
            f"Сотрудник: {emp['name']}\n\nШаг 2/6 — Опишите задачу:",
            reply_markup=_wiz_cancel_only_kb(),
        )
    await callback.answer()


# ── Step 2: description text ───────────────────────────────────────────────────

@router.message(TaskWizard.enter_description)
async def wiz_description(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    await state.update_data(description=text)
    if (await state.get_data()).get("editing"):
        await state.set_state(TaskWizard.confirm)
        await message.answer(_format_task_card(await state.get_data()), reply_markup=_wiz_confirm_kb())
    else:
        await state.set_state(TaskWizard.choose_deadline)
        await message.answer(f"Задача: {text}\n\nШаг 3/6 — Дедлайн?", reply_markup=_wiz_deadline_kb())


# ── Step 3: deadline preset ────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("tw:dl:"), StateFilter(TaskWizard.choose_deadline))
async def wiz_cb_deadline(callback: CallbackQuery, state: FSMContext) -> None:
    preset = callback.data.split(":")[-1]
    await _safe_edit(callback.message, markup=None)

    if preset == "manual":
        await state.set_state(TaskWizard.enter_deadline_manual)
        await callback.message.answer(
            "Введите дедлайн в формате: 15.05.2026 18:00",
            reply_markup=_wiz_cancel_only_kb(),
        )
        await callback.answer()
        return

    iso, display = _deadline_from_preset(preset)
    await state.update_data(deadline=iso, deadline_display=display)
    if (await state.get_data()).get("editing"):
        await state.set_state(TaskWizard.confirm)
        await callback.message.answer(_format_task_card(await state.get_data()), reply_markup=_wiz_confirm_kb())
    else:
        await state.set_state(TaskWizard.choose_priority)
        await callback.message.answer(
            f"Дедлайн: {display}\n\nШаг 4/6 — Приоритет?", reply_markup=_wiz_priority_kb()
        )
    await callback.answer()


# ── Step 4b: manual deadline input ────────────────────────────────────────────

@router.message(TaskWizard.enter_deadline_manual)
async def wiz_deadline_manual(message: Message, state: FSMContext) -> None:
    iso, err = _parse_manual_deadline(message.text or "")
    if err:
        await message.answer(err, reply_markup=_wiz_cancel_only_kb())
        return
    display = datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M")
    await state.update_data(deadline=iso, deadline_display=display)
    if (await state.get_data()).get("editing"):
        await state.set_state(TaskWizard.confirm)
        await message.answer(_format_task_card(await state.get_data()), reply_markup=_wiz_confirm_kb())
    else:
        await state.set_state(TaskWizard.choose_priority)
        await message.answer(f"Дедлайн: {display}\n\nШаг 4/6 — Приоритет?", reply_markup=_wiz_priority_kb())


# ── Step 4: priority ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("tw:pr:"), StateFilter(TaskWizard.choose_priority))
async def wiz_cb_priority(callback: CallbackQuery, state: FSMContext) -> None:
    priority = callback.data.split(":")[-1]
    await state.update_data(priority=priority)
    await _safe_edit(callback.message, markup=None)
    if (await state.get_data()).get("editing"):
        await state.set_state(TaskWizard.confirm)
        await callback.message.answer(_format_task_card(await state.get_data()), reply_markup=_wiz_confirm_kb())
    else:
        await state.set_state(TaskWizard.choose_category)
        await callback.message.answer(
            f"Приоритет: {PRIORITY_DISPLAY.get(priority, priority)}\n\nШаг 5/6 — Категория?",
            reply_markup=_wiz_category_kb(),
        )
    await callback.answer()


# ── Step 5: category ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("tw:cat:"), StateFilter(TaskWizard.choose_category))
async def wiz_cb_category(callback: CallbackQuery, state: FSMContext) -> None:
    category = callback.data.split(":")[-1]
    await state.update_data(category=category)
    await _safe_edit(callback.message, markup=None)
    if (await state.get_data()).get("editing"):
        await state.set_state(TaskWizard.confirm)
        await callback.message.answer(_format_task_card(await state.get_data()), reply_markup=_wiz_confirm_kb())
    else:
        await state.set_state(TaskWizard.enter_comment)
        await callback.message.answer(
            f"Категория: {CATEGORY_DISPLAY.get(category, category)}\n\n"
            "Шаг 6/6 — Комментарий?\nВведите текст или нажмите Пропустить:",
            reply_markup=_wiz_comment_kb(),
        )
    await callback.answer()


# ── Step 6: comment text ───────────────────────────────────────────────────────

@router.message(TaskWizard.enter_comment)
async def wiz_comment(message: Message, state: FSMContext) -> None:
    await state.update_data(comment=(message.text or "").strip() or None)
    await state.set_state(TaskWizard.confirm)
    await message.answer(_format_task_card(await state.get_data()), reply_markup=_wiz_confirm_kb())


@router.callback_query(F.data == "tw:skip_comment", StateFilter(TaskWizard.enter_comment))
async def wiz_cb_skip_comment(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(comment=None)
    await state.set_state(TaskWizard.confirm)
    await _safe_edit(callback.message, markup=None)
    await callback.message.answer(_format_task_card(await state.get_data()), reply_markup=_wiz_confirm_kb())
    await callback.answer()


# ── Step 7: confirm / edit / cancel ───────────────────────────────────────────

@router.callback_query(F.data == "tw:confirm", StateFilter(TaskWizard.confirm))
async def wiz_cb_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    await _safe_edit(callback.message, markup=None)
    reply = await _do_create_task_fsm(data, bot)
    await callback.message.answer(reply, reply_markup=_ADMIN_KB)
    await callback.answer()


@router.callback_query(F.data == "tw:edit", StateFilter(TaskWizard.confirm))
async def wiz_cb_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TaskWizard.edit_field)
    await _safe_edit(callback.message, markup=None)
    await callback.message.answer("Что изменить?", reply_markup=_wiz_edit_field_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("tw:ef:"), StateFilter(TaskWizard.edit_field))
async def wiz_cb_edit_field(callback: CallbackQuery, state: FSMContext) -> None:
    field = callback.data.split(":")[-1]
    await state.update_data(editing=True)
    await _safe_edit(callback.message, markup=None)

    if field == "employee":
        employees = await db.list_employees()
        await state.set_state(TaskWizard.choose_employee)
        await callback.message.answer("Кому назначить?", reply_markup=_wiz_employee_kb(employees))
    elif field == "description":
        await state.set_state(TaskWizard.enter_description)
        await callback.message.answer("Опишите задачу текстом:", reply_markup=_wiz_cancel_only_kb())
    elif field == "deadline":
        await state.set_state(TaskWizard.choose_deadline)
        await callback.message.answer("Выберите дедлайн:", reply_markup=_wiz_deadline_kb())
    elif field == "priority":
        await state.set_state(TaskWizard.choose_priority)
        await callback.message.answer("Выберите приоритет:", reply_markup=_wiz_priority_kb())
    elif field == "category":
        await state.set_state(TaskWizard.choose_category)
        await callback.message.answer("Выберите категорию:", reply_markup=_wiz_category_kb())
    elif field == "comment":
        await state.set_state(TaskWizard.enter_comment)
        await callback.message.answer(
            "Введите комментарий или нажмите Пропустить:",
            reply_markup=_wiz_comment_kb(),
        )
    await callback.answer()


# ── Catch-all for unexpected text during wizard (shows hint) ──────────────────

@router.message(StateFilter(TaskWizard))
async def wiz_unexpected(message: Message, state: FSMContext) -> None:
    if message.text and message.text.lower() in {"отмена", "cancel", "отменить"}:
        await state.clear()
        await message.answer("Создание задачи отменено.", reply_markup=_ADMIN_KB)
    else:
        await message.answer("Используйте кнопки для навигации или нажмите ❌ Отмена.")


# ══════════════════════════════════════════════════════════════════════════════
# Employee Management
# ══════════════════════════════════════════════════════════════════════════════

class EmpWizard(StatesGroup):
    rename = State()


# ── Keyboards ──────────────────────────────────────────────────────────────────

def _emp_list_kb(employees: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=e["name"], callback_data=f"em:card:{e['id']}")]
        for e in employees
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _emp_card_kb(emp_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Задачи",        callback_data=f"em:tasks:{emp_id}"),
            InlineKeyboardButton(text="Переименовать", callback_data=f"em:rename:{emp_id}"),
        ],
        [
            InlineKeyboardButton(text="🗑 Удалить",    callback_data=f"em:del:{emp_id}"),
            InlineKeyboardButton(text="🔑 Новый код",  callback_data=f"em:newcode:{emp_id}"),
        ],
        [InlineKeyboardButton(text="← Назад",          callback_data="em:list")],
    ])


def _emp_delete_kb(emp_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да, удалить ✓", callback_data=f"em:delok:{emp_id}"),
        InlineKeyboardButton(text="Нет ✗",         callback_data=f"em:card:{emp_id}"),
    ]])


def _emp_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="em:cancel")]
    ])


# ── Card formatter ─────────────────────────────────────────────────────────────

def _format_emp_card(emp: dict, stats: dict) -> str:
    tg_status = "подключён" if emp.get("telegram_id") else "не подключён"
    date_str = "—"
    ts = emp.get("created_at")
    if ts:
        try:
            date_str = datetime.fromisoformat(str(ts)).strftime("%d.%m.%Y")
        except (ValueError, TypeError):
            date_str = str(ts)[:10]
    return (
        f"Сотрудник: {emp['name']}\n"
        f"Зарегистрирован: {date_str}\n"
        f"Telegram: {tg_status}\n"
        f"Активных задач: {stats['active']}\n"
        f"Выполненных: {stats['done']}"
    )


# ── "Статистика" reply-keyboard button ────────────────────────────────────────

@router.message(F.text == "Статистика", StateFilter(None))
async def handle_stats_btn(message: Message) -> None:
    if message.from_user.id != config.ADMIN_ID:
        return
    stats = await db.get_team_stats()
    t = stats["totals"]
    lines = [
        "Статистика команды",
        "",
        f"Всего задач:   {t['total']}",
        f"Новых:         {t['new']}",
        f"В работе:      {t['in_progress']}",
        f"На проверке:   {t['review']}",
        f"Выполнено:     {t['done']}",
        f"Просрочено:    {t['overdue']}",
    ]
    if stats["by_employee"]:
        lines += ["", "По сотрудникам:"]
        for e in stats["by_employee"]:
            lines.append(
                f"  {e['name']}: всего {e['total']} | "
                f"новых {e['new_count']} | "
                f"в работе {e['in_progress_count']} | "
                f"готово {e['done_count']}"
            )
    if stats["overdue_tasks"]:
        lines += ["", "Просроченные:"]
        for task in stats["overdue_tasks"]:
            dl = fmt_deadline(task.get("deadline")) or "—"
            desc = task["description"][:50] + ("…" if len(task["description"]) > 50 else "")
            lines.append(f"  #{task['id']} {task['employee_name']} — {desc} (дедлайн {dl})")
    await message.answer("\n".join(lines), reply_markup=_ADMIN_KB)


# ── "Список сотрудников" reply-keyboard button ─────────────────────────────────

@router.message(F.text == "Список сотрудников", F.from_user.id == config.ADMIN_ID, StateFilter(None))
async def handle_emp_list_btn(message: Message) -> None:
    employees = await db.list_employees()
    if not employees:
        await message.answer(
            "Нет сотрудников. Добавьте командой:\nДобавь сотрудника Иванов",
            reply_markup=_ADMIN_KB,
        )
        return
    await message.answer("Выберите сотрудника:", reply_markup=_emp_list_kb(employees))


# ── em:list — back to list ─────────────────────────────────────────────────────

@router.callback_query(F.data == "em:list")
async def em_cb_list(callback: CallbackQuery) -> None:
    employees = await db.list_employees()
    text = "Выберите сотрудника:" if employees else "Нет сотрудников."
    kb = _emp_list_kb(employees) if employees else None
    await _safe_edit(callback.message, text=text, markup=kb)
    await callback.answer()


# ── em:card:{id} — employee card ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("em:card:"))
async def em_cb_card(callback: CallbackQuery) -> None:
    emp_id = int(callback.data.split(":")[-1])
    emp = await db.get_employee_by_id(emp_id)
    if not emp:
        await callback.answer("Сотрудник не найден.", show_alert=True)
        return
    stats = await db.get_employee_stats(emp_id)
    await _safe_edit(callback.message, text=_format_emp_card(emp, stats), markup=_emp_card_kb(emp_id))
    await callback.answer()


# ── em:tasks:{id} — employee tasks ────────────────────────────────────────────

@router.callback_query(F.data.startswith("em:tasks:"))
async def em_cb_tasks(callback: CallbackQuery) -> None:
    emp_id = int(callback.data.split(":")[-1])
    emp = await db.get_employee_by_id(emp_id)
    if not emp:
        await callback.answer("Сотрудник не найден.", show_alert=True)
        return
    tasks = await db.list_tasks(employee_id=emp_id)
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← К сотруднику", callback_data=f"em:card:{emp_id}")]
    ])
    if not tasks:
        text = f"У {emp['name']} нет задач."
    else:
        lines = [f"Задачи {emp['name']}:\n"]
        for t in tasks:
            dl = fmt_deadline(t.get("deadline")) or "без дедлайна"
            status_label = STATUS_RU.get(t["status"], t["status"])
            desc = t["description"][:50] + ("…" if len(t["description"]) > 50 else "")
            lines.append(f"#{t['id']} | {status_label} | {desc} | {dl}")
        text = "\n".join(lines)[:4000]
    await _safe_edit(callback.message, text=text, markup=back_kb)
    await callback.answer()


# ── em:rename:{id} — prompt for new name ─────────────────────────────────────

@router.callback_query(F.data.startswith("em:rename:"))
async def em_cb_rename(callback: CallbackQuery, state: FSMContext) -> None:
    emp_id = int(callback.data.split(":")[-1])
    emp = await db.get_employee_by_id(emp_id)
    if not emp:
        await callback.answer("Сотрудник не найден.", show_alert=True)
        return
    await state.set_state(EmpWizard.rename)
    await state.update_data(emp_id=emp_id)
    await _safe_edit(
        callback.message,
        text=f"Введите новое имя для {emp['name']}:",
        markup=_emp_cancel_kb(),
    )
    await callback.answer()


# ── em:del:{id} — delete confirmation card ────────────────────────────────────

@router.callback_query(F.data.startswith("em:del:"))
async def em_cb_delete(callback: CallbackQuery) -> None:
    emp_id = int(callback.data.split(":")[-1])
    emp = await db.get_employee_by_id(emp_id)
    if not emp:
        await callback.answer("Сотрудник не найден.", show_alert=True)
        return
    stats = await db.get_employee_stats(emp_id)
    active = stats["active"]
    if active:
        suffix = "а" if active == 1 else "и" if 2 <= active <= 4 else ""
        warning = f" У него {active} активных задач{suffix}. Они будут отменены."
    else:
        warning = ""
    await _safe_edit(
        callback.message,
        text=f"Удалить {emp['name']}?{warning}",
        markup=_emp_delete_kb(emp_id),
    )
    await callback.answer()


# ── em:delok:{id} — confirmed delete ──────────────────────────────────────────

@router.callback_query(F.data.startswith("em:delok:"))
async def em_cb_delete_ok(callback: CallbackQuery) -> None:
    emp_id = int(callback.data.split(":")[-1])
    emp = await db.get_employee_by_id(emp_id)
    if not emp:
        await _safe_edit(callback.message, markup=None)
        await callback.answer("Сотрудник уже удалён.", show_alert=True)
        return
    name = emp["name"]
    await db.delete_employee(emp_id)
    employees = await db.list_employees()
    if employees:
        await _safe_edit(
            callback.message,
            text=f"Сотрудник {name} удалён.\n\nВыберите сотрудника:",
            markup=_emp_list_kb(employees),
        )
    else:
        await _safe_edit(callback.message, text=f"Сотрудник {name} удалён. Список пуст.")
    await callback.answer(f"{name} удалён.")


# ── em:newcode:{id} — generate new registration code ─────────────────────────

@router.callback_query(F.data.startswith("em:newcode:"))
async def em_cb_newcode(callback: CallbackQuery) -> None:
    emp_id = int(callback.data.split(":")[-1])
    emp = await db.reset_employee_code(emp_id)
    if not emp:
        await callback.answer("Сотрудник не найден.", show_alert=True)
        return
    text = (
        f"Новый код для {emp['name']}: {emp['reg_code']}\n\n"
        f"Сотрудник должен написать боту:\n/start {emp['reg_code']}"
    )
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← К сотруднику", callback_data=f"em:card:{emp_id}")]
    ])
    await _safe_edit(callback.message, text=text, markup=back_kb)
    await callback.answer()


# ── em:cancel — cancel rename and return to card ──────────────────────────────

@router.callback_query(F.data == "em:cancel")
async def em_cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    emp_id = data.get("emp_id")
    await state.clear()
    if emp_id:
        emp = await db.get_employee_by_id(emp_id)
        if emp:
            stats = await db.get_employee_stats(emp_id)
            await _safe_edit(callback.message,
                             text=_format_emp_card(emp, stats), markup=_emp_card_kb(emp_id))
            await callback.answer()
            return
    employees = await db.list_employees()
    text = "Выберите сотрудника:" if employees else "Нет сотрудников."
    kb = _emp_list_kb(employees) if employees else None
    await _safe_edit(callback.message, text=text, markup=kb)
    await callback.answer()


# ── EmpWizard.rename — receive new name text ──────────────────────────────────

@router.message(EmpWizard.rename)
async def emp_rename_input(message: Message, state: FSMContext) -> None:
    new_name = (message.text or "").strip()
    if not new_name:
        await message.answer("Введите непустое имя:", reply_markup=_emp_cancel_kb())
        return
    data = await state.get_data()
    emp_id = data.get("emp_id")
    await state.clear()
    emp = await db.rename_employee(emp_id, new_name)
    if emp:
        stats = await db.get_employee_stats(emp_id)
        await message.answer(
            f"Имя изменено.\n\n{_format_emp_card(emp, stats)}",
            reply_markup=_emp_card_kb(emp_id),
        )
    else:
        await message.answer("Сотрудник не найден.", reply_markup=_ADMIN_KB)


# ── EmpWizard catch-all ────────────────────────────────────────────────────────

@router.message(StateFilter(EmpWizard))
async def emp_unexpected(message: Message, state: FSMContext) -> None:
    if message.text and message.text.lower() in {"отмена", "cancel", "отменить"}:
        await state.clear()
        await message.answer("Отменено.", reply_markup=_ADMIN_KB)
    else:
        await message.answer("Введите новое имя или нажмите ❌ Отмена.", reply_markup=_emp_cancel_kb())


# ══════════════════════════════════════════════════════════════════════════════
# Agent-flow callbacks
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("tc:"))
async def cb_task_confirm(callback: CallbackQuery, bot: Bot) -> None:
    _, action, raw_id = callback.data.split(":")
    admin_id = int(raw_id)

    if callback.from_user.id != admin_id:
        await callback.answer("Это не ваш запрос.", show_alert=True)
        return

    pending = _st.get_pending(admin_id)

    if action == "yes":
        if not pending:
            await _safe_edit(callback.message, markup=None)
            await callback.answer("Запрос устарел.", show_alert=True)
            return
        reply = await _do_create_task(admin_id, pending, bot)
        await _safe_edit(callback.message, markup=None)
        await callback.message.answer(reply, reply_markup=_ADMIN_KB)
        _save_history(admin_id, "Да", reply)
    elif action == "edit":
        _st.clear_pending(admin_id)
        await _safe_edit(callback.message, markup=None)
        await callback.message.answer(
            "Что хотите изменить? Напишите и я пересоздам карточку.", reply_markup=_ADMIN_KB
        )
    elif action == "cancel":
        _st.clear_pending(admin_id)
        await _safe_edit(callback.message, markup=None)
        reply = "Создание задачи отменено."
        await callback.message.answer(reply, reply_markup=_ADMIN_KB)
        _save_history(admin_id, "Отмена", reply)

    await callback.answer()


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
    await _safe_edit(callback.message, markup=None)

    if data.get("ok"):
        reply = (f"Задача #{task_id} подтверждена и закрыта."
                 if action == "ok" else f"Задача #{task_id} возвращена в работу.")
    else:
        reply = data.get("error", "Ошибка при обработке.")

    await callback.message.answer(reply, reply_markup=_ADMIN_KB)
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════════
# Main message handler (non-FSM, passes to Claude)
# ══════════════════════════════════════════════════════════════════════════════

@router.message()
async def handle_message(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.text:
        await message.answer("Отправьте текстовое сообщение.")
        return

    uid = message.from_user.id
    is_admin = uid == config.ADMIN_ID
    text = message.text.strip()

    # Fallback: if wiz_start filter somehow missed, catch it here
    if is_admin and text == "Новая задача":
        await _wiz_launch(message, state)
        return

    if not is_admin:
        emp = await db.get_employee_by_telegram_id(uid)
        if not emp:
            await message.answer(
                "Вы не зарегистрированы.\nПолучите код у администратора: /start КОД"
            )
            return
        employee_name = emp["name"]
        if text == "Помощь":
            await _send_help(message, is_admin=False)
            return
    else:
        employee_name = "Администратор"

    # Legacy agent-based wizard (for backward compat)
    if is_admin and _st.in_new_task_mode(uid):
        _st.end_new_task_mode(uid)
        text = f"Создай задачу для сотрудника {text!r}. Спроси у меня описание и дедлайн, потом покажи карточку."

    # Intercept pending confirmation (text fallback)
    if is_admin:
        pending = _st.get_pending(uid)
        if pending:
            token = text.lower()
            if token in {"да", "yes", "ок", "ok", "верно", "подтвердить"}:
                reply = await _do_create_task(uid, pending, bot)
                _save_history(uid, "Да", reply)
                await message.answer(reply, reply_markup=_ADMIN_KB)
                return
            if token in {"нет", "no", "отмена", "отменить", "cancel"}:
                _st.clear_pending(uid)
                reply = "Создание задачи отменено."
                _save_history(uid, "Нет", reply)
                await message.answer(reply, reply_markup=_ADMIN_KB)
                return
            if token in {"изменить", "edit", "поправить"}:
                _st.clear_pending(uid)

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

    has_pending = is_admin and _st.get_pending(uid) is not None
    reply_kb = _confirm_kb(uid) if has_pending else _role_kb(is_admin)

    for i, start in enumerate(range(0, len(response), 4096)):
        await message.answer(response[start:start + 4096], reply_markup=reply_kb if i == 0 else None)
