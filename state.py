"""Per-user bot state. Key = telegram_id."""
from typing import Optional

# Tasks awaiting admin confirmation
_pending: dict[int, dict] = {}

# Admins waiting to type an employee name for a new task
_new_task_mode: set[int] = set()


# ── Pending task ───────────────────────────────────────────────────────────────

def set_pending(admin_id: int, task: dict) -> None:
    _pending[admin_id] = task


def get_pending(admin_id: int) -> Optional[dict]:
    return _pending.get(admin_id)


def clear_pending(admin_id: int) -> None:
    _pending.pop(admin_id, None)


# ── New-task wizard ────────────────────────────────────────────────────────────

def start_new_task_mode(admin_id: int) -> None:
    _new_task_mode.add(admin_id)


def in_new_task_mode(admin_id: int) -> bool:
    return admin_id in _new_task_mode


def end_new_task_mode(admin_id: int) -> None:
    _new_task_mode.discard(admin_id)
