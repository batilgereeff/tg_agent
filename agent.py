import logging
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, MAX_TOOL_ITERATIONS
from tools import TOOL_SCHEMAS, execute_tool

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Вы — умный бизнес-ассистент с доступом к CRM, трекеру заявок и системе рассылок.

Возможности:
1. CRM — управление контактами (добавление, поиск) и сделками (создание, обновление статусов)
2. Заявки — создание тикетов, назначение исполнителей, изменение статусов и приоритетов
3. Рассылки — управление группами пользователей и отправка массовых сообщений в Telegram

Правила работы:
- Отвечайте на том же языке, на котором пишет пользователь
- Используйте инструменты проактивно: если задача требует нескольких шагов — выполняйте их последовательно
- После выполнения операции кратко подтверждайте результат с ключевыми данными (ID, название, статус)
- При выводе списков форматируйте их читаемо
- Статусы сделок: new (новая), in_progress (в работе), won (выиграна), lost (проиграна)
- Статусы заявок: open (открыта), in_progress (в работе), resolved (решена), closed (закрыта)
- Приоритеты заявок: low (низкий), medium (средний), high (высокий), critical (критический)"""


async def run_agent(history: list, user_message: str, bot=None) -> str:
    """
    Run a Claude agent with tool use loop (up to MAX_TOOL_ITERATIONS iterations).

    Args:
        history: Conversation history as list of {"role": ..., "content": ...} dicts.
                 Contains only plain text exchanges (no tool blocks).
        user_message: The new user message to process.
        bot: Optional aiogram Bot instance for send_broadcast tool.

    Returns:
        Final text response from the agent.
    """
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    # Build messages: history (text only) + new user message
    messages = list(history)
    messages.append({"role": "user", "content": user_message})

    last_response = None

    for iteration in range(MAX_TOOL_ITERATIONS):
        logger.debug("Agent iteration %d/%d", iteration + 1, MAX_TOOL_ITERATIONS)

        last_response = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )

        logger.debug("Stop reason: %s", last_response.stop_reason)

        # No more tool calls — we have the final answer
        if last_response.stop_reason != "tool_use":
            break

        # Collect all tool_use blocks from the response
        tool_uses = [b for b in last_response.content if b.type == "tool_use"]
        if not tool_uses:
            break

        # Append assistant message (may contain text + tool_use blocks)
        messages.append({"role": "assistant", "content": last_response.content})

        # Execute each tool and collect results
        tool_results = []
        for tu in tool_uses:
            logger.info("Calling tool '%s' with input: %s", tu.name, tu.input)
            result_str = await execute_tool(tu.name, tu.input, bot=bot)
            logger.debug("Tool '%s' result: %s", tu.name, result_str)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result_str,
            })

        # Feed results back to Claude
        messages.append({"role": "user", "content": tool_results})

    if last_response is None:
        return "Произошла ошибка при обработке запроса."

    # Extract plain text from the final response
    text_parts = [b.text for b in last_response.content if hasattr(b, "text") and b.text]
    return "\n".join(text_parts) or "Готово."
