import aiosqlite
from typing import Optional
from config import DATABASE_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    phone      TEXT DEFAULT '',
    email      TEXT DEFAULT '',
    company    TEXT DEFAULT '',
    notes      TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS deals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER REFERENCES contacts(id),
    title      TEXT NOT NULL,
    amount     REAL DEFAULT 0,
    status     TEXT DEFAULT 'new',
    notes      TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT DEFAULT '',
    assignee    TEXT DEFAULT '',
    status      TEXT DEFAULT 'open',
    priority    TEXT DEFAULT 'medium',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS broadcast_groups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS group_members (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id         INTEGER REFERENCES broadcast_groups(id),
    telegram_user_id INTEGER NOT NULL,
    username         TEXT DEFAULT '',
    UNIQUE(group_id, telegram_user_id)
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        await conn.executescript(_SCHEMA)
        await conn.commit()


# ── Users ──────────────────────────────────────────────────────────────────────

async def register_user(user_id: int, username: Optional[str], first_name: Optional[str]) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO users (id, username, first_name) VALUES (?, ?, ?)",
            (user_id, username or "", first_name or ""),
        )
        await conn.commit()


# ── Contacts ───────────────────────────────────────────────────────────────────

async def add_contact(
    name: str,
    phone: str = "",
    email: str = "",
    company: str = "",
    notes: str = "",
) -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO contacts (name, phone, email, company, notes) VALUES (?, ?, ?, ?, ?)",
            (name, phone, email, company, notes),
        )
        await conn.commit()
        return {"id": cur.lastrowid, "name": name, "phone": phone, "email": email, "company": company, "notes": notes}


async def search_contacts(query: str) -> list:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        like = f"%{query}%"
        cur = await conn.execute(
            "SELECT * FROM contacts WHERE name LIKE ? OR phone LIKE ? OR email LIKE ? OR company LIKE ? ORDER BY name",
            (like, like, like, like),
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_contact(contact_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


# ── Deals ──────────────────────────────────────────────────────────────────────

async def create_deal(
    contact_id: int,
    title: str,
    amount: float = 0,
    status: str = "new",
    notes: str = "",
) -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO deals (contact_id, title, amount, status, notes) VALUES (?, ?, ?, ?, ?)",
            (contact_id, title, amount, status, notes),
        )
        await conn.commit()
        return {"id": cur.lastrowid, "contact_id": contact_id, "title": title, "amount": amount, "status": status, "notes": notes}


async def update_deal(
    deal_id: int,
    status: Optional[str] = None,
    amount: Optional[float] = None,
    notes: Optional[str] = None,
) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM deals WHERE id = ?", (deal_id,))
        row = await cur.fetchone()
        if not row:
            return None
        deal = dict(row)
        new_status = status if status is not None else deal["status"]
        new_amount = amount if amount is not None else deal["amount"]
        new_notes = notes if notes is not None else deal["notes"]
        await conn.execute(
            "UPDATE deals SET status=?, amount=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_status, new_amount, new_notes, deal_id),
        )
        await conn.commit()
        return {**deal, "status": new_status, "amount": new_amount, "notes": new_notes}


async def list_deals(contact_id: Optional[int] = None, status: Optional[str] = None) -> list:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        conditions, params = [], []
        if contact_id is not None:
            conditions.append("contact_id = ?")
            params.append(contact_id)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cur = await conn.execute(f"SELECT * FROM deals {where} ORDER BY created_at DESC", params)
        return [dict(r) for r in await cur.fetchall()]


# ── Tickets ────────────────────────────────────────────────────────────────────

async def create_ticket(
    title: str,
    description: str = "",
    assignee: str = "",
    priority: str = "medium",
) -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO tickets (title, description, assignee, priority) VALUES (?, ?, ?, ?)",
            (title, description, assignee, priority),
        )
        await conn.commit()
        return {"id": cur.lastrowid, "title": title, "description": description,
                "assignee": assignee, "priority": priority, "status": "open"}


async def update_ticket(
    ticket_id: int,
    status: Optional[str] = None,
    assignee: Optional[str] = None,
    priority: Optional[str] = None,
) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        row = await cur.fetchone()
        if not row:
            return None
        ticket = dict(row)
        new_status = status if status is not None else ticket["status"]
        new_assignee = assignee if assignee is not None else ticket["assignee"]
        new_priority = priority if priority is not None else ticket["priority"]
        await conn.execute(
            "UPDATE tickets SET status=?, assignee=?, priority=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_status, new_assignee, new_priority, ticket_id),
        )
        await conn.commit()
        return {**ticket, "status": new_status, "assignee": new_assignee, "priority": new_priority}


async def list_tickets(
    status: Optional[str] = None,
    assignee: Optional[str] = None,
    priority: Optional[str] = None,
) -> list:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        conditions, params = [], []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if assignee:
            conditions.append("assignee = ?")
            params.append(assignee)
        if priority:
            conditions.append("priority = ?")
            params.append(priority)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cur = await conn.execute(f"SELECT * FROM tickets {where} ORDER BY created_at DESC", params)
        return [dict(r) for r in await cur.fetchall()]


# ── Broadcast Groups ───────────────────────────────────────────────────────────

async def create_broadcast_group(name: str) -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        try:
            cur = await conn.execute("INSERT INTO broadcast_groups (name) VALUES (?)", (name,))
            await conn.commit()
            return {"id": cur.lastrowid, "name": name, "created": True}
        except aiosqlite.IntegrityError:
            cur = await conn.execute("SELECT * FROM broadcast_groups WHERE name = ?", (name,))
            row = await cur.fetchone()
            return {**dict(row), "created": False}


async def list_broadcast_groups() -> list:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("""
            SELECT bg.id, bg.name, bg.created_at, COUNT(gm.id) AS member_count
            FROM broadcast_groups bg
            LEFT JOIN group_members gm ON bg.id = gm.group_id
            GROUP BY bg.id
            ORDER BY bg.name
        """)
        return [dict(r) for r in await cur.fetchall()]


async def add_member_to_group(group_name: str, telegram_user_id: int, username: str = "") -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT id FROM broadcast_groups WHERE name = ?", (group_name,))
        group = await cur.fetchone()
        if not group:
            return {"error": f"Группа '{group_name}' не найдена"}
        try:
            await conn.execute(
                "INSERT INTO group_members (group_id, telegram_user_id, username) VALUES (?, ?, ?)",
                (group["id"], telegram_user_id, username),
            )
            await conn.commit()
            return {"success": True, "group": group_name, "user_id": telegram_user_id}
        except aiosqlite.IntegrityError:
            return {"success": False, "message": "Пользователь уже в группе"}


async def remove_member_from_group(group_name: str, telegram_user_id: int) -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT id FROM broadcast_groups WHERE name = ?", (group_name,))
        group = await cur.fetchone()
        if not group:
            return {"error": f"Группа '{group_name}' не найдена"}
        await conn.execute(
            "DELETE FROM group_members WHERE group_id = ? AND telegram_user_id = ?",
            (group["id"], telegram_user_id),
        )
        await conn.commit()
        return {"success": True, "group": group_name, "user_id": telegram_user_id}


async def get_group_members(group_name: str) -> list:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("""
            SELECT gm.telegram_user_id, gm.username
            FROM group_members gm
            JOIN broadcast_groups bg ON gm.group_id = bg.id
            WHERE bg.name = ?
        """, (group_name,))
        return [dict(r) for r in await cur.fetchall()]
