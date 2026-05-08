import json
import logging
from datetime import datetime
from typing import Optional

import database as db
import state
from config import ADMIN_ID

logger = logging.getLogger(__name__)

# ── Helpers ────────────────────────────────────────────────────────────────────

STATUS_RU = {
    "new":         "новая",
    "in_progress": "в работе",
    "review":      "на проверке",
    "done":        "выполнена",
    "overdue":     "просрочена",
    "cancelled":   "отменена",
}

PRIORITY_RU = {
    "low": "низкий",
    "normal": "обычный",
    "high": "высокий",
    "critical": "критический",
}


def fmt_deadline(deadline: Optional[str]) -> str:
    if not deadline:
        return ""
    try:
        return datetime.fromisoformat(deadline).strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return deadline


def _fmt_task(t: dict) -> dict:
    return {
        "id": t["id"],
        "employee": t.get("employee_name", ""),
        "description": t["description"],
        "deadline": fmt_deadline(t.get("deadline")) or "без дедлайна",
        "priority": PRIORITY_RU.get(t.get("priority", "normal"), "обычный"),
        "status": STATUS_RU.get(t["status"], t["status"]),
        "created_at": t.get("created_at", ""),
        "completed_at": t.get("completed_at", ""),
    }


# ── Tool schemas ───────────────────────────────────────────────────────────────

ADMIN_TOOL_SCHEMAS = [
    {
        "name": "add_employee",
        "description": "Добавить нового сотрудника. Генерирует 6-значный код регистрации.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Имя или фамилия сотрудника"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_employees",
        "description": "Список всех сотрудников с их статусом регистрации.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "propose_task",
        "description": (
            "Сформировать карточку задачи для подтверждения. "
            "Задача НЕ создаётся — только показывается карточка. "
            "После показа нужно дождаться ответа: Да / Нет / Изменить."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_name": {"type": "string", "description": "Имя сотрудника"},
                "description":   {"type": "string", "description": "Описание задачи"},
                "deadline": {
                    "type": "string",
                    "description": "Дедлайн в формате ISO 8601 (YYYY-MM-DDTHH:MM:SS)",
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high", "critical"],
                    "description": "Приоритет задачи",
                },
            },
            "required": ["employee_name", "description"],
        },
    },
    {
        "name": "get_team_stats",
        "description": (
            "Сводная статистика: итого по статусам, разбивка по сотрудникам, "
            "список просроченных задач. Вызывать при словах: таблица, статистика, сводка."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_tasks",
        "description": "Список задач с фильтрацией по сотруднику и/или статусу.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_name": {"type": "string", "description": "Имя сотрудника"},
                "status": {
                    "type": "string",
                    "description": "Статус: new, in_progress, review, done, overdue",
                },
            },
        },
    },
    {
        "name": "get_task",
        "description": "Детальная информация о задаче по ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID задачи"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "send_reminder",
        "description": "Вручную отправить сотруднику напоминание о задаче.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID задачи"},
                "custom_message": {"type": "string", "description": "Кастомный текст (необязательно)"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "confirm_task",
        "description": "Подтвердить выполнение задачи. Статус становится 'выполнена'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID задачи"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "reject_task",
        "description": "Отклонить задачу, вернуть в работу. Сотрудник получит уведомление.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID задачи"},
                "comment": {"type": "string", "description": "Причина отклонения"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "rename_employee",
        "description": "Переименовать сотрудника. Имя обновляется во всех связанных задачах.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_name": {"type": "string", "description": "Текущее имя сотрудника"},
                "new_name":      {"type": "string", "description": "Новое имя"},
            },
            "required": ["employee_name", "new_name"],
        },
    },
    {
        "name": "delete_employee",
        "description": "Удалить сотрудника. Все активные задачи будут отменены.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_name": {"type": "string", "description": "Имя сотрудника"},
            },
            "required": ["employee_name"],
        },
    },
]

EMPLOYEE_TOOL_SCHEMAS = [
    {
        "name": "my_tasks",
        "description": "Список моих задач с фильтром по статусу.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Статус: new, in_progress, review, done, overdue",
                },
            },
        },
    },
    {
        "name": "get_task",
        "description": "Детальная информация о задаче по ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID задачи"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "start_task",
        "description": "Взять задачу в работу (статус → в работе).",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID задачи"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "submit_task",
        "description": "Отправить задачу на проверку администратору (статус → на проверке).",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID задачи"},
            },
            "required": ["task_id"],
        },
    },
]


# ── Tool executor ──────────────────────────────────────────────────────────────

async def execute_tool(name: str, input_data: dict, caller_id: int = 0, bot=None) -> str:
    try:
        # ── Admin tools ──────────────────────────────────────────────────────
        if name == "add_employee":
            emp = await db.create_employee(input_data["name"])
            return json.dumps({
                "id": emp["id"],
                "name": emp["name"],
                "reg_code": emp["reg_code"],
                "instruction": f"Сотрудник пишет боту: /start {emp['reg_code']}",
            }, ensure_ascii=False)

        elif name == "list_employees":
            rows = await db.list_employees()
            return json.dumps([
                {
                    "id": e["id"],
                    "name": e["name"],
                    "registered": bool(e["telegram_id"]),
                    "reg_code": e["reg_code"] if not e["telegram_id"] else None,
                }
                for e in rows
            ], ensure_ascii=False)

        elif name == "propose_task":
            matches = await db.find_employees_by_name(input_data["employee_name"])
            if not matches:
                return json.dumps({"error": f"Сотрудник '{input_data['employee_name']}' не найден"})
            if len(matches) > 1:
                return json.dumps({"error": f"Найдено несколько: {[e['name'] for e in matches]}. Уточните имя."})
            emp = matches[0]
            deadline = input_data.get("deadline")
            priority = input_data.get("priority", "normal")

            state.set_pending(caller_id, {
                "employee_id":         emp["id"],
                "employee_name":       emp["name"],
                "employee_telegram_id": emp["telegram_id"],
                "description":         input_data["description"],
                "deadline":            deadline,
                "priority":            priority,
            })

            card = (
                f"Проверьте задачу:\n\n"
                f"Сотрудник: {emp['name']}\n"
                f"Задача: {input_data['description']}\n"
                f"Дедлайн: {fmt_deadline(deadline) or 'не указан'}\n"
                f"Приоритет: {PRIORITY_RU.get(priority, priority)}\n\n"
                f"Все верно? Ответьте: Да, Нет или Изменить"
            )
            return json.dumps({"pending": True, "card": card}, ensure_ascii=False)

        elif name == "get_team_stats":
            stats = await db.get_team_stats()
            for t in stats["overdue_tasks"]:
                t["deadline"] = fmt_deadline(t.get("deadline")) or "без дедлайна"
            return json.dumps(stats, ensure_ascii=False)

        elif name == "list_tasks":
            employee_id = None
            if input_data.get("employee_name"):
                matches = await db.find_employees_by_name(input_data["employee_name"])
                if not matches:
                    return json.dumps({"error": "Сотрудник не найден"})
                employee_id = matches[0]["id"]
            tasks = await db.list_tasks(employee_id=employee_id, status=input_data.get("status"))
            return json.dumps([_fmt_task(t) for t in tasks], ensure_ascii=False)

        elif name == "get_task":
            task = await db.get_task(input_data["task_id"])
            if not task:
                return json.dumps({"error": f"Задача #{input_data['task_id']} не найдена"})
            return json.dumps(_fmt_task(task), ensure_ascii=False)

        elif name == "send_reminder":
            task = await db.get_task(input_data["task_id"])
            if not task:
                return json.dumps({"error": f"Задача #{input_data['task_id']} не найдена"})
            tg_id = task.get("employee_telegram_id")
            if not tg_id:
                return json.dumps({"error": "Сотрудник ещё не зарегистрирован в Telegram"})
            if not bot:
                return json.dumps({"error": "Bot недоступен"})
            custom = input_data.get("custom_message")
            if custom:
                text = custom
            else:
                deadline_str = fmt_deadline(task.get("deadline"))
                text = (
                    f"Напоминание от администратора\n\n"
                    f"Задача #{task['id']}: {task['description']}"
                    + (f"\nДедлайн: {deadline_str}" if deadline_str else "")
                    + f"\nСтатус: {STATUS_RU.get(task['status'], task['status'])}"
                )
            await bot.send_message(tg_id, text)
            return json.dumps({"sent": True, "to": task["employee_name"]}, ensure_ascii=False)

        elif name == "confirm_task":
            task = await db.get_task(input_data["task_id"])
            if not task:
                return json.dumps({"error": f"Задача #{input_data['task_id']} не найдена"})
            if task["status"] == "done":
                return json.dumps({"error": "Задача уже выполнена"})
            now = datetime.now().isoformat(timespec="seconds")
            task = await db.update_task_status(input_data["task_id"], "done", completed_at=now)
            if bot and task.get("employee_telegram_id"):
                try:
                    await bot.send_message(
                        task["employee_telegram_id"],
                        f"Задача #{task['id']} подтверждена администратором.\n\n{task['description']}",
                    )
                except Exception as e:
                    logger.warning("Notify employee failed: %s", e)
            return json.dumps({"ok": True, "task_id": task["id"], "completed_at": now}, ensure_ascii=False)

        elif name == "reject_task":
            task = await db.get_task(input_data["task_id"])
            if not task:
                return json.dumps({"error": f"Задача #{input_data['task_id']} не найдена"})
            task = await db.update_task_status(input_data["task_id"], "in_progress")
            comment = input_data.get("comment", "")
            if bot and task.get("employee_telegram_id"):
                try:
                    await bot.send_message(
                        task["employee_telegram_id"],
                        f"Задача #{task['id']} возвращена на доработку."
                        + (f"\n\nКомментарий: {comment}" if comment else ""),
                    )
                except Exception as e:
                    logger.warning("Notify employee failed: %s", e)
            return json.dumps({"ok": True, "task_id": task["id"], "status": "in_progress"}, ensure_ascii=False)

        # ── Employee tools ───────────────────────────────────────────────────
        elif name == "my_tasks":
            emp = await db.get_employee_by_telegram_id(caller_id)
            if not emp:
                return json.dumps({"error": "Вы не зарегистрированы"})
            tasks = await db.list_tasks(employee_id=emp["id"], status=input_data.get("status"))
            return json.dumps([_fmt_task(t) for t in tasks], ensure_ascii=False)

        elif name == "start_task":
            task = await db.get_task(input_data["task_id"])
            if not task:
                return json.dumps({"error": f"Задача #{input_data['task_id']} не найдена"})
            emp = await db.get_employee_by_telegram_id(caller_id)
            if not emp or task["employee_id"] != emp["id"]:
                return json.dumps({"error": "Это не ваша задача"})
            task = await db.update_task_status(input_data["task_id"], "in_progress")
            return json.dumps({"ok": True, "task_id": task["id"], "status": "in_progress"}, ensure_ascii=False)

        elif name == "submit_task":
            task = await db.get_task(input_data["task_id"])
            if not task:
                return json.dumps({"error": f"Задача #{input_data['task_id']} не найдена"})
            emp = await db.get_employee_by_telegram_id(caller_id)
            if not emp or task["employee_id"] != emp["id"]:
                return json.dumps({"error": "Это не ваша задача"})
            if task["status"] == "done":
                return json.dumps({"error": "Задача уже выполнена"})
            task = await db.update_task_status(input_data["task_id"], "review")
            if bot and ADMIN_ID:
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                review_kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="Подтвердить ✓",      callback_data=f"tr:ok:{task['id']}"),
                    InlineKeyboardButton(text="Вернуть в работу ↩", callback_data=f"tr:reject:{task['id']}"),
                ]])
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        f"{task['employee_name']} выполнил задачу #{task['id']}:\n\n"
                        f"{task['description']}",
                        reply_markup=review_kb,
                    )
                except Exception as e:
                    logger.warning("Notify admin failed: %s", e)
            return json.dumps({"ok": True, "task_id": task["id"], "status": "review"}, ensure_ascii=False)

        elif name == "rename_employee":
            matches = await db.find_employees_by_name(input_data["employee_name"])
            if not matches:
                return json.dumps({"error": f"Сотрудник '{input_data['employee_name']}' не найден"})
            if len(matches) > 1:
                return json.dumps({"error": f"Найдено несколько: {[e['name'] for e in matches]}. Уточните имя."})
            emp = matches[0]
            old_name = emp["name"]
            updated = await db.rename_employee(emp["id"], input_data["new_name"])
            return json.dumps({"ok": True, "old_name": old_name, "new_name": updated["name"]},
                              ensure_ascii=False)

        elif name == "delete_employee":
            matches = await db.find_employees_by_name(input_data["employee_name"])
            if not matches:
                return json.dumps({"error": f"Сотрудник '{input_data['employee_name']}' не найден"})
            if len(matches) > 1:
                return json.dumps({"error": f"Найдено несколько: {[e['name'] for e in matches]}. Уточните имя."})
            emp = matches[0]
            cancelled = await db.delete_employee(emp["id"])
            return json.dumps(
                {"ok": True, "deleted": emp["name"], "cancelled_tasks": cancelled},
                ensure_ascii=False,
            )

        else:
            return json.dumps({"error": f"Неизвестный инструмент: {name}"})

    except Exception as exc:
        logger.error("Tool '%s' error: %s", name, exc, exc_info=True)
        return json.dumps({"error": str(exc)})
