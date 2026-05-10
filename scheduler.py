import logging
from datetime import datetime, timedelta

import database as db
from config import ADMIN_ID
from tools import fmt_deadline, STATUS_RU

logger = logging.getLogger(__name__)


# ── Deadline check — every 5 minutes ──────────────────────────────────────────

async def check_deadlines(bot) -> None:
    logger.debug("Deadline check running")
    now = datetime.now()

    for task in await db.get_tasks_for_deadline_check():
        if not task.get("deadline"):
            continue
        try:
            deadline = datetime.fromisoformat(task["deadline"])
        except (ValueError, TypeError):
            continue

        time_left = deadline - now
        tid = task["id"]
        desc = task["description"]
        emp_tg = task.get("employee_telegram_id")
        emp_name = task.get("employee_name", "")
        dl_str = deadline.strftime("%d.%m.%Y %H:%M")

        # 1-hour reminder
        if timedelta(0) < time_left <= timedelta(hours=1) and not task["deadline_reminded"]:
            mins = max(1, int(time_left.total_seconds() / 60))
            if emp_tg:
                try:
                    await bot.send_message(
                        emp_tg,
                        f"Напоминание о задаче #{tid}\n\n"
                        f"{desc}\n\n"
                        f"Дедлайн через {mins} мин ({dl_str}).\n"
                        f"Когда будет готово — напишите: Задача #{tid} готова",
                    )
                except Exception as e:
                    logger.error("Reminder failed user %s: %s", emp_tg, e)
            await db.mark_deadline_reminded(tid)

        # Overdue
        if time_left < timedelta(0) and not task["overdue_notified"]:
            if emp_tg:
                try:
                    await bot.send_message(
                        emp_tg,
                        f"Задача #{tid} просрочена.\n\n"
                        f"{desc}\n\n"
                        f"Дедлайн был: {dl_str}\n"
                        f"Отправьте на проверку: Задача #{tid} готова",
                    )
                except Exception as e:
                    logger.error("Overdue notify failed user %s: %s", emp_tg, e)
            if ADMIN_ID:
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        f"Просрочена задача #{tid}\n\n"
                        f"Сотрудник: {emp_name}\n"
                        f"Задача: {desc}\n"
                        f"Дедлайн был: {dl_str}",
                    )
                except Exception as e:
                    logger.error("Admin overdue notify failed: %s", e)
            await db.mark_overdue_notified(tid)


# ── Morning digest — 09:00 ─────────────────────────────────────────────────────

async def morning_digest(bot) -> None:
    logger.info("Morning digest running")
    for emp in await db.list_employees():
        tg_id = emp.get("telegram_id")
        if not tg_id:
            continue
        tasks = await db.list_tasks(employee_id=emp["id"])
        active = [t for t in tasks if t["status"] not in ("done", "cancelled")]
        if not active:
            continue

        overdue = [t for t in active if t["status"] == "overdue"]
        regular = [t for t in active if t["status"] != "overdue"]

        lines = [f"Доброе утро, {emp['name']}!\n"]

        if overdue:
            lines.append("ПРОСРОЧЕННЫЕ ЗАДАЧИ:")
            for t in overdue:
                dl = fmt_deadline(t.get("deadline")) or "—"
                desc = t["description"][:60] + ("..." if len(t["description"]) > 60 else "")
                lines.append(f"  ❗ #{t['id']} — {desc}\n     Дедлайн был: {dl}")
            lines.append("")

        if regular:
            lines.append("Активные задачи:")
            for t in regular:
                dl = fmt_deadline(t.get("deadline")) or "без дедлайна"
                status = STATUS_RU.get(t["status"], t["status"])
                desc = t["description"][:60] + ("..." if len(t["description"]) > 60 else "")
                lines.append(f"  #{t['id']} | {status} | {desc} | {dl}")

        try:
            await bot.send_message(tg_id, "\n".join(lines))
        except Exception as e:
            logger.error("Morning digest failed %s: %s", emp["name"], e)


# ── Evening summary — 18:00 ────────────────────────────────────────────────────

async def evening_summary(bot) -> None:
    if not ADMIN_ID:
        return
    logger.info("Evening summary running")

    summary = await db.get_daily_summary()
    done = summary["done_today"]
    overdue = summary["overdue_today"]

    # Count in-progress tasks
    all_tasks = await db.list_tasks()
    in_progress = [t for t in all_tasks if t["status"] == "in_progress"]

    today = datetime.now().strftime("%d.%m.%Y")
    lines = [
        f"Итоги дня {today}:\n",
        f"Выполнено: {len(done)}",
        f"Просрочено: {len(overdue)}",
        f"В работе: {len(in_progress)}",
    ]

    if done:
        lines.append("\nВыполненные задачи:")
        for t in done:
            desc = t["description"][:50] + ("..." if len(t["description"]) > 50 else "")
            lines.append(f"  #{t['id']} {t.get('employee_name', '')} — {desc}")

    if overdue:
        lines.append("\nПросрочены сегодня:")
        for t in overdue:
            desc = t["description"][:50] + ("..." if len(t["description"]) > 50 else "")
            dl = fmt_deadline(t.get("deadline"))
            lines.append(f"  #{t['id']} {t.get('employee_name', '')} — {desc} (дедлайн {dl})")

    if not done and not overdue:
        lines.append("\nНет изменений за день.")

    try:
        await bot.send_message(ADMIN_ID, "\n".join(lines))
    except Exception as e:
        logger.error("Evening summary failed: %s", e)
