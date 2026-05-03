import json
from typing import Optional
import database as db

TOOL_SCHEMAS = [
    # ── CRM: Contacts ──────────────────────────────────────────────────────────
    {
        "name": "add_contact",
        "description": "Добавить новый контакт в CRM",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":    {"type": "string", "description": "Полное имя контакта"},
                "phone":   {"type": "string", "description": "Номер телефона"},
                "email":   {"type": "string", "description": "Email адрес"},
                "company": {"type": "string", "description": "Название компании"},
                "notes":   {"type": "string", "description": "Заметки"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "search_contacts",
        "description": "Поиск контактов по имени, телефону, email или компании",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_contact",
        "description": "Получить детальную информацию о контакте вместе с его сделками",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "ID контакта"},
            },
            "required": ["contact_id"],
        },
    },
    # ── CRM: Deals ─────────────────────────────────────────────────────────────
    {
        "name": "create_deal",
        "description": "Создать новую сделку для контакта",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "ID контакта"},
                "title":      {"type": "string",  "description": "Название сделки"},
                "amount":     {"type": "number",  "description": "Сумма сделки"},
                "status":     {
                    "type": "string",
                    "description": "Статус сделки",
                    "enum": ["new", "in_progress", "won", "lost"],
                },
                "notes": {"type": "string", "description": "Заметки к сделке"},
            },
            "required": ["contact_id", "title"],
        },
    },
    {
        "name": "update_deal",
        "description": "Обновить статус, сумму или заметки сделки",
        "input_schema": {
            "type": "object",
            "properties": {
                "deal_id": {"type": "integer", "description": "ID сделки"},
                "status":  {
                    "type": "string",
                    "description": "Новый статус",
                    "enum": ["new", "in_progress", "won", "lost"],
                },
                "amount": {"type": "number", "description": "Новая сумма"},
                "notes":  {"type": "string", "description": "Обновлённые заметки"},
            },
            "required": ["deal_id"],
        },
    },
    {
        "name": "list_deals",
        "description": "Получить список сделок с фильтрацией по контакту или статусу",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "Фильтр по ID контакта"},
                "status":     {"type": "string",  "description": "Фильтр по статусу: new, in_progress, won, lost"},
            },
        },
    },
    # ── Tickets ────────────────────────────────────────────────────────────────
    {
        "name": "create_ticket",
        "description": "Создать новую заявку",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":       {"type": "string", "description": "Название заявки"},
                "description": {"type": "string", "description": "Подробное описание"},
                "assignee":    {"type": "string", "description": "Исполнитель (имя или username)"},
                "priority":    {
                    "type": "string",
                    "description": "Приоритет",
                    "enum": ["low", "medium", "high", "critical"],
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_ticket",
        "description": "Обновить статус, исполнителя или приоритет заявки",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer", "description": "ID заявки"},
                "status":    {
                    "type": "string",
                    "description": "Новый статус",
                    "enum": ["open", "in_progress", "resolved", "closed"],
                },
                "assignee": {"type": "string", "description": "Новый исполнитель"},
                "priority": {
                    "type": "string",
                    "description": "Новый приоритет",
                    "enum": ["low", "medium", "high", "critical"],
                },
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "list_tickets",
        "description": "Получить список заявок с фильтрацией",
        "input_schema": {
            "type": "object",
            "properties": {
                "status":   {"type": "string", "description": "Фильтр по статусу: open, in_progress, resolved, closed"},
                "assignee": {"type": "string", "description": "Фильтр по исполнителю"},
                "priority": {"type": "string", "description": "Фильтр по приоритету: low, medium, high, critical"},
            },
        },
    },
    # ── Broadcasts ─────────────────────────────────────────────────────────────
    {
        "name": "create_broadcast_group",
        "description": "Создать новую группу для рассылки",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Название группы"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_broadcast_groups",
        "description": "Получить список всех групп рассылки с количеством участников",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_member_to_group",
        "description": "Добавить пользователя Telegram в группу рассылки",
        "input_schema": {
            "type": "object",
            "properties": {
                "group_name":       {"type": "string",  "description": "Название группы"},
                "telegram_user_id": {"type": "integer", "description": "Telegram ID пользователя"},
                "username":         {"type": "string",  "description": "Username пользователя (опционально)"},
            },
            "required": ["group_name", "telegram_user_id"],
        },
    },
    {
        "name": "remove_member_from_group",
        "description": "Удалить пользователя из группы рассылки",
        "input_schema": {
            "type": "object",
            "properties": {
                "group_name":       {"type": "string",  "description": "Название группы"},
                "telegram_user_id": {"type": "integer", "description": "Telegram ID пользователя"},
            },
            "required": ["group_name", "telegram_user_id"],
        },
    },
    {
        "name": "send_broadcast",
        "description": "Отправить сообщение всем участникам группы рассылки через Telegram",
        "input_schema": {
            "type": "object",
            "properties": {
                "group_name": {"type": "string", "description": "Название группы"},
                "message":    {"type": "string", "description": "Текст сообщения"},
            },
            "required": ["group_name", "message"],
        },
    },
]


async def execute_tool(name: str, input_data: dict, bot=None) -> str:
    try:
        if name == "add_contact":
            result = await db.add_contact(
                name=input_data["name"],
                phone=input_data.get("phone", ""),
                email=input_data.get("email", ""),
                company=input_data.get("company", ""),
                notes=input_data.get("notes", ""),
            )
            return json.dumps(result, ensure_ascii=False)

        elif name == "search_contacts":
            result = await db.search_contacts(input_data["query"])
            return json.dumps(result, ensure_ascii=False)

        elif name == "get_contact":
            contact = await db.get_contact(input_data["contact_id"])
            if not contact:
                return json.dumps({"error": "Контакт не найден"})
            deals = await db.list_deals(contact_id=input_data["contact_id"])
            return json.dumps({**contact, "deals": deals}, ensure_ascii=False)

        elif name == "create_deal":
            result = await db.create_deal(
                contact_id=input_data["contact_id"],
                title=input_data["title"],
                amount=input_data.get("amount", 0),
                status=input_data.get("status", "new"),
                notes=input_data.get("notes", ""),
            )
            return json.dumps(result, ensure_ascii=False)

        elif name == "update_deal":
            result = await db.update_deal(
                deal_id=input_data["deal_id"],
                status=input_data.get("status"),
                amount=input_data.get("amount"),
                notes=input_data.get("notes"),
            )
            if not result:
                return json.dumps({"error": "Сделка не найдена"})
            return json.dumps(result, ensure_ascii=False)

        elif name == "list_deals":
            result = await db.list_deals(
                contact_id=input_data.get("contact_id"),
                status=input_data.get("status"),
            )
            return json.dumps(result, ensure_ascii=False)

        elif name == "create_ticket":
            result = await db.create_ticket(
                title=input_data["title"],
                description=input_data.get("description", ""),
                assignee=input_data.get("assignee", ""),
                priority=input_data.get("priority", "medium"),
            )
            return json.dumps(result, ensure_ascii=False)

        elif name == "update_ticket":
            result = await db.update_ticket(
                ticket_id=input_data["ticket_id"],
                status=input_data.get("status"),
                assignee=input_data.get("assignee"),
                priority=input_data.get("priority"),
            )
            if not result:
                return json.dumps({"error": "Заявка не найдена"})
            return json.dumps(result, ensure_ascii=False)

        elif name == "list_tickets":
            result = await db.list_tickets(
                status=input_data.get("status"),
                assignee=input_data.get("assignee"),
                priority=input_data.get("priority"),
            )
            return json.dumps(result, ensure_ascii=False)

        elif name == "create_broadcast_group":
            result = await db.create_broadcast_group(input_data["name"])
            return json.dumps(result, ensure_ascii=False)

        elif name == "list_broadcast_groups":
            result = await db.list_broadcast_groups()
            return json.dumps(result, ensure_ascii=False)

        elif name == "add_member_to_group":
            result = await db.add_member_to_group(
                group_name=input_data["group_name"],
                telegram_user_id=input_data["telegram_user_id"],
                username=input_data.get("username", ""),
            )
            return json.dumps(result, ensure_ascii=False)

        elif name == "remove_member_from_group":
            result = await db.remove_member_from_group(
                group_name=input_data["group_name"],
                telegram_user_id=input_data["telegram_user_id"],
            )
            return json.dumps(result, ensure_ascii=False)

        elif name == "send_broadcast":
            if bot is None:
                return json.dumps({"error": "Bot instance не доступен"})
            members = await db.get_group_members(input_data["group_name"])
            if not members:
                return json.dumps({"error": f"Группа '{input_data['group_name']}' не найдена или пуста"})
            sent, failed = 0, 0
            for member in members:
                try:
                    await bot.send_message(member["telegram_user_id"], input_data["message"])
                    sent += 1
                except Exception:
                    failed += 1
            return json.dumps({"sent": sent, "failed": failed, "total": len(members), "group": input_data["group_name"]}, ensure_ascii=False)

        else:
            return json.dumps({"error": f"Неизвестный инструмент: {name}"})

    except Exception as exc:
        return json.dumps({"error": str(exc)})
