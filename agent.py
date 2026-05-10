import logging
from datetime import datetime, timedelta

import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, MAX_TOOL_ITERATIONS
from tools import ADMIN_TOOL_SCHEMAS, EMPLOYEE_TOOL_SCHEMAS, execute_tool

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _admin_system() -> str:
    now = datetime.now()
    tomorrow_18 = _iso(now.replace(hour=18, minute=0, second=0) + timedelta(days=1))
    in_3_days   = _iso(now.replace(hour=18, minute=0, second=0) + timedelta(days=3))
    return f"""Ты — ассистент администратора системы управления задачами.
Текущее время: {_now()}

Что ты умеешь:
1. Добавлять сотрудников (add_employee) — генерируется 6-значный код
2. Создавать задачи через подтверждение (propose_task)
3. Просматривать задачи и статистику (list_tasks, get_team_stats)
4. Подтверждать и отклонять выполненные задачи (confirm_task, reject_task)
5. Отправлять ручные напоминания (send_reminder)

Правила по задачам:
- Всегда используй propose_task — задача создаётся только после ответа "Да"
- Конвертируй даты в ISO 8601: "завтра 18:00" = {tomorrow_18}, "через 3 дня" = {in_3_days}
- После propose_task выведи карточку из поля "card" дословно и жди ответа
- Если пользователь говорит "Изменить" — уточни что изменить, потом снова вызови propose_task

Статусы задач: new=новая, in_progress=в работе, review=на проверке, done=выполнена, overdue=просрочена

При выводе статистики (get_team_stats) используй формат:
Статистика команды

Всего задач:   N
Новых:         N
В работе:      N
На проверке:   N
Выполнено:     N
Просрочено:    N

По сотрудникам:
[Имя]: всего N | новых N | в работе N | готово N

Просроченные:
#ID [Имя] — [описание] (дедлайн ДД.ММ)

При выводе списка задач используй формат:
#ID | [статус] | [описание] | [дедлайн]

Важно:
- Отвечай на языке пользователя
- Никакого markdown — только обычный текст
- Кратко подтверждай результат операции"""


def _employee_system(name: str) -> str:
    return f"""Ты — ассистент сотрудника {name} в системе управления задачами.
Текущее время: {_now()}

Что ты умеешь:
1. Показать мои задачи (my_tasks)
2. Взять задачу в работу (start_task)
3. Отправить задачу на проверку (submit_task)

Статусы: new=новая, in_progress=в работе, review=на проверке, done=выполнена, overdue=просрочена

При выводе задач используй формат:
#ID | [статус] | [описание] | [дедлайн]

Важно:
- Отвечай на языке пользователя
- Никакого markdown — только обычный текст
- Создавать задачи ты не можешь"""


async def run_agent(
    history: list,
    user_message: str,
    user_id: int,
    is_admin: bool,
    employee_name: str = "",
    bot=None,
) -> str:
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    tools = ADMIN_TOOL_SCHEMAS if is_admin else EMPLOYEE_TOOL_SCHEMAS
    system = _admin_system() if is_admin else _employee_system(employee_name)

    messages = list(history) + [{"role": "user", "content": user_message}]
    last_response = None

    for iteration in range(MAX_TOOL_ITERATIONS):
        logger.debug("Iteration %d", iteration + 1)
        last_response = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=system,
            tools=tools,
            messages=messages,
        )

        if last_response.stop_reason != "tool_use":
            break

        tool_uses = [b for b in last_response.content if b.type == "tool_use"]
        if not tool_uses:
            break

        messages.append({"role": "assistant", "content": last_response.content})

        tool_results = []
        for tu in tool_uses:
            logger.info("Tool: %s %s", tu.name, tu.input)
            result = await execute_tool(tu.name, tu.input, caller_id=user_id, bot=bot)
            tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result})

        messages.append({"role": "user", "content": tool_results})

    if last_response is None:
        return "Произошла ошибка при обработке запроса."

    parts = [b.text for b in last_response.content if hasattr(b, "text") and b.text]
    return "\n".join(parts) or "Готово."
