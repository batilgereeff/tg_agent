import secrets
import aiosqlite
from typing import Optional
from config import DATABASE_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    telegram_id   INTEGER UNIQUE,
    reg_code      TEXT UNIQUE NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    registered_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id       INTEGER NOT NULL REFERENCES employees(id),
    title             TEXT,
    description       TEXT NOT NULL,
    deadline          TEXT,
    priority          TEXT DEFAULT 'normal',
    category          TEXT,
    comment           TEXT,
    status            TEXT DEFAULT 'new',
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at      TEXT,
    deadline_reminded INTEGER DEFAULT 0,
    overdue_notified  INTEGER DEFAULT 0
);
"""

_MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN priority TEXT DEFAULT 'normal'",
    "ALTER TABLE tasks ADD COLUMN category TEXT",
    "ALTER TABLE tasks ADD COLUMN comment  TEXT",
    "ALTER TABLE tasks ADD COLUMN title    TEXT",
]


async def init_db() -> None:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        await conn.executescript(_SCHEMA)
        for sql in _MIGRATIONS:
            try:
                await conn.execute(sql)
                await conn.commit()
            except aiosqlite.OperationalError:
                pass


# ── Employees ──────────────────────────────────────────────────────────────────

def _gen_code() -> str:
    """6-digit numeric registration code, e.g. '847291'."""
    return str(secrets.randbelow(900_000) + 100_000)


async def create_employee(name: str) -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        code = _gen_code()
        while True:
            cur = await conn.execute("SELECT id FROM employees WHERE reg_code = ?", (code,))
            if not await cur.fetchone():
                break
            code = _gen_code()
        cur = await conn.execute(
            "INSERT INTO employees (name, reg_code) VALUES (?, ?)", (name, code)
        )
        await conn.commit()
        return {"id": cur.lastrowid, "name": name, "reg_code": code}


async def register_employee(reg_code: str, telegram_id: int) -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM employees WHERE reg_code = ?", (reg_code,))
        emp = await cur.fetchone()
        if not emp:
            return {"ok": False, "reason": "not_found"}
        if emp["telegram_id"]:
            return {"ok": False, "reason": "already_registered", "name": emp["name"]}
        await conn.execute(
            "UPDATE employees SET telegram_id = ?, registered_at = CURRENT_TIMESTAMP WHERE reg_code = ?",
            (telegram_id, reg_code),
        )
        await conn.commit()
        cur = await conn.execute("SELECT * FROM employees WHERE reg_code = ?", (reg_code,))
        return {"ok": True, **dict(await cur.fetchone())}


async def get_employee_by_telegram_id(telegram_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM employees WHERE telegram_id = ?", (telegram_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_employee_by_id(employee_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM employees WHERE id = ?", (employee_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def find_employees_by_name(name: str) -> list:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM employees WHERE name LIKE ? ORDER BY name", (f"%{name}%",)
        )
        return [dict(r) for r in await cur.fetchall()]


async def list_employees() -> list:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM employees ORDER BY name")
        return [dict(r) for r in await cur.fetchall()]


# ── Tasks ──────────────────────────────────────────────────────────────────────

_TASK_SELECT = """
    SELECT t.*, e.name AS employee_name, e.telegram_id AS employee_telegram_id
    FROM tasks t JOIN employees e ON t.employee_id = e.id
"""


async def create_task(
    employee_id: int,
    description: str,
    deadline: Optional[str] = None,
    priority: str = "normal",
    category: Optional[str] = None,
    comment: Optional[str] = None,
    title: Optional[str] = None,
) -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO tasks (employee_id, title, description, deadline, priority, category, comment)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (employee_id, title, description, deadline, priority, category, comment),
        )
        await conn.commit()
        return {
            "id": cur.lastrowid, "employee_id": employee_id,
            "title": title, "description": description, "deadline": deadline,
            "priority": priority, "category": category,
            "comment": comment, "status": "new",
        }


async def get_task(task_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(f"{_TASK_SELECT} WHERE t.id = ?", (task_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def update_task_status(
    task_id: int, status: str, completed_at: Optional[str] = None
) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        if completed_at:
            await conn.execute(
                "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?",
                (status, completed_at, task_id),
            )
        else:
            await conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
        await conn.commit()
    return await get_task(task_id)


async def list_tasks(employee_id: Optional[int] = None, status: Optional[str] = None) -> list:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        conditions, params = [], []
        if employee_id is not None:
            conditions.append("t.employee_id = ?"); params.append(employee_id)
        if status:
            conditions.append("t.status = ?"); params.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cur = await conn.execute(f"{_TASK_SELECT} {where} ORDER BY t.created_at DESC", params)
        return [dict(r) for r in await cur.fetchall()]


async def get_team_stats() -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("""
            SELECT e.id, e.name,
                COUNT(t.id)                                                AS total,
                SUM(CASE WHEN t.status='new'         THEN 1 ELSE 0 END)   AS new_count,
                SUM(CASE WHEN t.status='in_progress' THEN 1 ELSE 0 END)   AS in_progress_count,
                SUM(CASE WHEN t.status='review'      THEN 1 ELSE 0 END)   AS review_count,
                SUM(CASE WHEN t.status='done'        THEN 1 ELSE 0 END)   AS done_count,
                SUM(CASE WHEN t.status='overdue'     THEN 1 ELSE 0 END)   AS overdue_count
            FROM employees e LEFT JOIN tasks t ON t.employee_id = e.id
            GROUP BY e.id, e.name ORDER BY e.name
        """)
        by_employee = [dict(r) for r in await cur.fetchall()]
        cur2 = await conn.execute("""
            SELECT t.id, e.name AS employee_name, t.description, t.deadline
            FROM tasks t JOIN employees e ON t.employee_id = e.id
            WHERE t.status = 'overdue' ORDER BY t.deadline
        """)
        overdue_tasks = [dict(r) for r in await cur2.fetchall()]
        totals = {
            "total":       sum(e["total"]             for e in by_employee),
            "new":         sum(e["new_count"]          for e in by_employee),
            "in_progress": sum(e["in_progress_count"]  for e in by_employee),
            "review":      sum(e["review_count"]       for e in by_employee),
            "done":        sum(e["done_count"]          for e in by_employee),
            "overdue":     sum(e["overdue_count"]       for e in by_employee),
        }
        return {"totals": totals, "by_employee": by_employee, "overdue_tasks": overdue_tasks}


async def get_daily_summary() -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("""
            SELECT t.*, e.name AS employee_name FROM tasks t
            JOIN employees e ON t.employee_id = e.id
            WHERE DATE(t.completed_at) = DATE('now','localtime') AND t.status = 'done'
            ORDER BY t.completed_at
        """)
        done_today = [dict(r) for r in await cur.fetchall()]
        cur2 = await conn.execute("""
            SELECT t.*, e.name AS employee_name FROM tasks t
            JOIN employees e ON t.employee_id = e.id
            WHERE DATE(t.deadline) = DATE('now','localtime') AND t.status = 'overdue'
            ORDER BY t.deadline
        """)
        overdue_today = [dict(r) for r in await cur2.fetchall()]
        return {"done_today": done_today, "overdue_today": overdue_today}


async def get_tasks_for_deadline_check() -> list:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(f"""
            {_TASK_SELECT}
            WHERE t.status NOT IN ('done', 'cancelled') AND t.deadline IS NOT NULL
        """)
        return [dict(r) for r in await cur.fetchall()]


async def get_employee_stats(employee_id: int) -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        cur = await conn.execute("""
            SELECT
                SUM(CASE WHEN status NOT IN ('done', 'cancelled') THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN status = 'done'                     THEN 1 ELSE 0 END) AS done_count
            FROM tasks WHERE employee_id = ?
        """, (employee_id,))
        row = await cur.fetchone()
        return {"active": row[0] or 0, "done": row[1] or 0}


async def rename_employee(employee_id: int, new_name: str) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        await conn.execute("UPDATE employees SET name = ? WHERE id = ?", (new_name, employee_id))
        await conn.commit()
    return await get_employee_by_id(employee_id)


async def delete_employee(employee_id: int) -> int:
    """Cancel active tasks then delete employee. Returns number of cancelled tasks."""
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        cur = await conn.execute(
            "UPDATE tasks SET status = 'cancelled'"
            " WHERE employee_id = ? AND status NOT IN ('done', 'cancelled')",
            (employee_id,),
        )
        cancelled = cur.rowcount
        await conn.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
        await conn.commit()
    return cancelled


async def reset_employee_code(employee_id: int) -> Optional[dict]:
    """Generate a new reg code and unlink the Telegram account."""
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        new_code = _gen_code()
        while True:
            cur = await conn.execute(
                "SELECT id FROM employees WHERE reg_code = ? AND id != ?",
                (new_code, employee_id),
            )
            if not await cur.fetchone():
                break
            new_code = _gen_code()
        await conn.execute(
            "UPDATE employees SET reg_code = ?, telegram_id = NULL, registered_at = NULL"
            " WHERE id = ?",
            (new_code, employee_id),
        )
        await conn.commit()
    return await get_employee_by_id(employee_id)


async def mark_deadline_reminded(task_id: int) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        await conn.execute("UPDATE tasks SET deadline_reminded = 1 WHERE id = ?", (task_id,))
        await conn.commit()


async def mark_overdue_notified(task_id: int) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        await conn.execute(
            "UPDATE tasks SET overdue_notified = 1, status = 'overdue' WHERE id = ?", (task_id,)
        )
        await conn.commit()
