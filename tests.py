"""Comprehensive tests for telegram-agent bot."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta

# Must set env vars before importing config
os.environ["TELEGRAM_TOKEN"] = "test:token"
os.environ["ANTHROPIC_API_KEY"] = "test_key"
os.environ["ADMIN_ID"] = "42"

import config


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _tmp_db():
    """Return a fresh temp-file path for an isolated DB."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


# ══════════════════════════════════════════════════════════════════════════════
# bot.py — deadline helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestDeadlineFromPreset(unittest.TestCase):
    def _call(self, preset):
        from bot import _deadline_from_preset
        return _deadline_from_preset(preset)

    def test_1h_returns_roughly_now_plus_1h(self):
        iso, display = self._call("1h")
        self.assertIsNotNone(iso)
        dt = datetime.fromisoformat(iso)
        self.assertAlmostEqual((dt - datetime.now()).total_seconds(), 3600, delta=5)
        self.assertRegex(display, r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}")

    def test_3h(self):
        iso, _ = self._call("3h")
        dt = datetime.fromisoformat(iso)
        self.assertAlmostEqual((dt - datetime.now()).total_seconds(), 10800, delta=5)

    def test_today18_hour_and_date(self):
        iso, _ = self._call("today18")
        dt = datetime.fromisoformat(iso)
        self.assertEqual(dt.hour, 18)
        self.assertEqual(dt.date(), datetime.now().date())

    def test_tom12(self):
        iso, _ = self._call("tom12")
        dt = datetime.fromisoformat(iso)
        self.assertEqual(dt.hour, 12)
        self.assertEqual(dt.date(), (datetime.now() + timedelta(days=1)).date())

    def test_tom18(self):
        iso, _ = self._call("tom18")
        dt = datetime.fromisoformat(iso)
        self.assertEqual(dt.hour, 18)
        self.assertEqual(dt.date(), (datetime.now() + timedelta(days=1)).date())

    def test_unknown_preset_returns_none(self):
        iso, display = self._call("bogus")
        self.assertIsNone(iso)
        self.assertEqual(display, "без дедлайна")

    def test_all_presets_produce_valid_iso(self):
        from bot import _deadline_from_preset
        for p in ("1h", "3h", "today18", "tom12", "tom18"):
            iso, _ = _deadline_from_preset(p)
            self.assertIsNotNone(iso, f"preset {p!r} returned None")
            datetime.fromisoformat(iso)  # must not raise


# ══════════════════════════════════════════════════════════════════════════════
# bot.py — manual deadline parser
# ══════════════════════════════════════════════════════════════════════════════

class TestParseManualDeadline(unittest.TestCase):
    def _call(self, text):
        from bot import _parse_manual_deadline
        return _parse_manual_deadline(text)

    def test_valid_format(self):
        iso, err = self._call("15.05.2026 18:00")
        self.assertIsNone(err)
        dt = datetime.fromisoformat(iso)
        self.assertEqual((dt.day, dt.month, dt.year, dt.hour, dt.minute), (15, 5, 2026, 18, 0))

    def test_strips_whitespace(self):
        iso, err = self._call("  01.01.2027 09:30  ")
        self.assertIsNone(err)
        self.assertIsNotNone(iso)

    def test_wrong_separator_returns_error(self):
        iso, err = self._call("2026-05-15 18:00")
        self.assertIsNone(iso)
        self.assertIsNotNone(err)

    def test_empty_returns_error(self):
        iso, err = self._call("")
        self.assertIsNone(iso)
        self.assertIsNotNone(err)

    def test_garbage_returns_error(self):
        iso, err = self._call("завтра")
        self.assertIsNone(iso)
        self.assertIsNotNone(err)

    def test_date_only_no_time_returns_error(self):
        iso, err = self._call("15.05.2026")
        self.assertIsNone(iso)
        self.assertIsNotNone(err)


# ══════════════════════════════════════════════════════════════════════════════
# bot.py — task card formatter
# ══════════════════════════════════════════════════════════════════════════════

class TestFormatTaskCard(unittest.TestCase):
    def _call(self, data):
        from bot import _format_task_card
        return _format_task_card(data)

    def _full(self):
        return {
            "employee_name": "Иванов",
            "description": "Подготовить отчёт",
            "deadline_display": "15.05.2026 18:00",
            "priority": "high",
            "category": "docs",
            "comment": "нужны данные за Q1",
        }

    def test_all_fields_present(self):
        card = self._call(self._full())
        for fragment in ("Иванов", "Подготовить отчёт", "15.05.2026 18:00",
                         "Высокий", "Документы", "нужны данные за Q1", "Всё верно?"):
            self.assertIn(fragment, card, f"Missing: {fragment!r}")

    def test_no_deadline_shows_fallback(self):
        data = self._full()
        del data["deadline_display"]
        self.assertIn("без дедлайна", self._call(data))

    def test_none_comment_shows_dash(self):
        data = self._full()
        data["comment"] = None
        self.assertIn("Комментарий: —", self._call(data))

    def test_empty_category_shows_dash(self):
        data = self._full()
        data["category"] = ""
        self.assertIn("Категория: —", self._call(data))

    def test_unknown_priority_defaults_to_normal(self):
        data = self._full()
        data["priority"] = "mystery"
        self.assertIn("Обычный", self._call(data))

    def test_empty_data_still_returns_card(self):
        card = self._call({})
        self.assertIn("Всё верно?", card)

    def test_all_priorities(self):
        from bot import PRIORITY_DISPLAY
        for key, label in PRIORITY_DISPLAY.items():
            data = self._full()
            data["priority"] = key
            self.assertIn(label, self._call(data))

    def test_all_categories(self):
        from bot import CATEGORY_DISPLAY
        for key, label in CATEGORY_DISPLAY.items():
            data = self._full()
            data["category"] = key
            self.assertIn(label, self._call(data))


# ══════════════════════════════════════════════════════════════════════════════
# bot.py — display mappings
# ══════════════════════════════════════════════════════════════════════════════

class TestDisplayMaps(unittest.TestCase):
    def test_priority_keys(self):
        from bot import PRIORITY_DISPLAY
        self.assertEqual(set(PRIORITY_DISPLAY), {"low", "normal", "high", "critical"})

    def test_category_keys(self):
        from bot import CATEGORY_DISPLAY
        self.assertEqual(set(CATEGORY_DISPLAY), {"repair", "docs", "purchase", "call", "other"})

    def test_no_empty_display_values(self):
        from bot import PRIORITY_DISPLAY, CATEGORY_DISPLAY
        for v in {**PRIORITY_DISPLAY, **CATEGORY_DISPLAY}.values():
            self.assertTrue(v.strip(), f"Empty display value found: {v!r}")


# ══════════════════════════════════════════════════════════════════════════════
# bot.py — keyboard builders
# ══════════════════════════════════════════════════════════════════════════════

class TestKeyboardBuilders(unittest.TestCase):
    def _all_cb(self, kb):
        return [btn.callback_data for row in kb.inline_keyboard for btn in row]

    def test_employee_kb(self):
        from bot import _wiz_employee_kb
        employees = [{"id": 1, "name": "Иванов"}, {"id": 2, "name": "Петров"}]
        cb = self._all_cb(_wiz_employee_kb(employees))
        self.assertIn("tw:cancel", cb)
        self.assertIn("tw:emp:1", cb)
        self.assertIn("tw:emp:2", cb)

    def test_employee_kb_empty_list_still_has_cancel(self):
        from bot import _wiz_employee_kb
        cb = self._all_cb(_wiz_employee_kb([]))
        self.assertIn("tw:cancel", cb)

    def test_deadline_kb_all_presets(self):
        from bot import _wiz_deadline_kb
        cb = self._all_cb(_wiz_deadline_kb())
        for p in ("tw:dl:1h", "tw:dl:3h", "tw:dl:today18",
                  "tw:dl:tom12", "tw:dl:tom18", "tw:dl:manual"):
            self.assertIn(p, cb)
        self.assertIn("tw:cancel", cb)

    def test_priority_kb_all_options(self):
        from bot import _wiz_priority_kb
        cb = self._all_cb(_wiz_priority_kb())
        for p in ("tw:pr:low", "tw:pr:normal", "tw:pr:high", "tw:pr:critical"):
            self.assertIn(p, cb)
        self.assertIn("tw:cancel", cb)

    def test_category_kb_all_options(self):
        from bot import _wiz_category_kb
        cb = self._all_cb(_wiz_category_kb())
        for c in ("tw:cat:repair", "tw:cat:docs", "tw:cat:purchase",
                  "tw:cat:call", "tw:cat:other"):
            self.assertIn(c, cb)
        self.assertIn("tw:cancel", cb)

    def test_comment_kb(self):
        from bot import _wiz_comment_kb
        cb = self._all_cb(_wiz_comment_kb())
        self.assertIn("tw:skip_comment", cb)
        self.assertIn("tw:cancel", cb)

    def test_confirm_kb_three_actions(self):
        from bot import _wiz_confirm_kb
        cb = self._all_cb(_wiz_confirm_kb())
        self.assertIn("tw:confirm", cb)
        self.assertIn("tw:edit", cb)
        self.assertIn("tw:cancel", cb)

    def test_edit_field_kb_all_fields_and_cancel(self):
        from bot import _wiz_edit_field_kb
        cb = self._all_cb(_wiz_edit_field_kb())
        for f in ("tw:ef:employee", "tw:ef:description", "tw:ef:deadline",
                  "tw:ef:priority", "tw:ef:category", "tw:ef:comment"):
            self.assertIn(f, cb)
        self.assertIn("tw:cancel", cb)

    def test_cancel_only_kb_single_button(self):
        from bot import _wiz_cancel_only_kb
        cb = self._all_cb(_wiz_cancel_only_kb())
        self.assertEqual(cb, ["tw:cancel"])

    def test_builders_return_independent_objects(self):
        from bot import _wiz_deadline_kb
        kb1, kb2 = _wiz_deadline_kb(), _wiz_deadline_kb()
        self.assertIsNot(kb1, kb2)
        self.assertIsNot(kb1.inline_keyboard, kb2.inline_keyboard)


# ══════════════════════════════════════════════════════════════════════════════
# bot.py — FSM states
# ══════════════════════════════════════════════════════════════════════════════

class TestFSMStates(unittest.TestCase):
    def test_nine_states_defined(self):
        from bot import TaskWizard
        self.assertEqual(len(TaskWizard.__states__), 9)

    def test_all_expected_states_present(self):
        from bot import TaskWizard
        names = {s.state.split(":")[-1] for s in TaskWizard.__states__}
        expected = {
            "choose_employee", "enter_description", "choose_deadline",
            "enter_deadline_manual", "choose_priority", "choose_category",
            "enter_comment", "confirm", "edit_field",
        }
        self.assertEqual(names, expected)


# ══════════════════════════════════════════════════════════════════════════════
# state.py
# ══════════════════════════════════════════════════════════════════════════════

class TestStateModule(unittest.TestCase):
    def setUp(self):
        import state as st
        st._pending.clear()
        st._new_task_mode.clear()

    def test_set_get_pending(self):
        import state as st
        st.set_pending(1, {"k": "v"})
        self.assertEqual(st.get_pending(1), {"k": "v"})

    def test_clear_pending(self):
        import state as st
        st.set_pending(1, {"x": 1})
        st.clear_pending(1)
        self.assertIsNone(st.get_pending(1))

    def test_clear_nonexistent_is_safe(self):
        import state as st
        st.clear_pending(9999)  # must not raise

    def test_new_task_mode_lifecycle(self):
        import state as st
        self.assertFalse(st.in_new_task_mode(1))
        st.start_new_task_mode(1)
        self.assertTrue(st.in_new_task_mode(1))
        st.end_new_task_mode(1)
        self.assertFalse(st.in_new_task_mode(1))

    def test_multiple_users_isolated(self):
        import state as st
        st.set_pending(1, {"a": 1})
        st.set_pending(2, {"b": 2})
        self.assertEqual(st.get_pending(1)["a"], 1)
        self.assertEqual(st.get_pending(2)["b"], 2)
        st.clear_pending(1)
        self.assertIsNone(st.get_pending(1))
        self.assertIsNotNone(st.get_pending(2))


# ══════════════════════════════════════════════════════════════════════════════
# tools.py — fmt_deadline
# ══════════════════════════════════════════════════════════════════════════════

class TestFmtDeadline(unittest.TestCase):
    def test_iso_roundtrip(self):
        from tools import fmt_deadline
        self.assertEqual(fmt_deadline("2026-05-15T18:00:00"), "15.05.2026 18:00")

    def test_none_returns_empty(self):
        from tools import fmt_deadline
        self.assertEqual(fmt_deadline(None), "")

    def test_empty_string_returns_empty(self):
        from tools import fmt_deadline
        self.assertEqual(fmt_deadline(""), "")

    def test_invalid_iso_returns_original(self):
        from tools import fmt_deadline
        self.assertEqual(fmt_deadline("not-a-date"), "not-a-date")


# ══════════════════════════════════════════════════════════════════════════════
# database.py  (each test class uses its own temp file)
# ══════════════════════════════════════════════════════════════════════════════

class TestDatabase(unittest.TestCase):
    def setUp(self):
        self._db_path = _tmp_db()
        config.DATABASE_PATH = self._db_path
        import database as db
        # Reload the module-level DATABASE_PATH reference inside database.py
        import importlib
        importlib.reload(db)
        run(db.init_db())

    def tearDown(self):
        try:
            os.unlink(self._db_path)
        except OSError:
            pass

    def test_init_db_idempotent(self):
        import database as db
        run(db.init_db())  # second call must not raise

    def test_category_and_comment_columns_exist(self):
        import database as db, aiosqlite

        async def cols():
            async with aiosqlite.connect(self._db_path) as conn:
                cur = await conn.execute("PRAGMA table_info(tasks)")
                return {row[1] for row in await cur.fetchall()}

        c = run(cols())
        self.assertIn("category", c)
        self.assertIn("comment", c)

    def test_create_employee_generates_code(self):
        import database as db
        emp = run(db.create_employee("Иванов"))
        self.assertEqual(emp["name"], "Иванов")
        self.assertTrue(emp["reg_code"].isdigit())
        self.assertEqual(len(emp["reg_code"]), 6)

    def test_list_employees(self):
        import database as db
        run(db.create_employee("А"))
        run(db.create_employee("Б"))
        self.assertEqual(len(run(db.list_employees())), 2)

    def test_register_employee_success(self):
        import database as db
        emp = run(db.create_employee("Козлов"))
        res = run(db.register_employee(emp["reg_code"], telegram_id=777))
        self.assertTrue(res["ok"])

    def test_register_invalid_code(self):
        import database as db
        res = run(db.register_employee("000000", telegram_id=1))
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "not_found")

    def test_register_already_registered(self):
        import database as db
        emp = run(db.create_employee("Волков"))
        run(db.register_employee(emp["reg_code"], telegram_id=100))
        res = run(db.register_employee(emp["reg_code"], telegram_id=200))
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "already_registered")

    def test_get_employee_by_telegram_id(self):
        import database as db
        emp = run(db.create_employee("Новиков"))
        run(db.register_employee(emp["reg_code"], telegram_id=555))
        found = run(db.get_employee_by_telegram_id(555))
        self.assertIsNotNone(found)
        self.assertEqual(found["name"], "Новиков")

    def test_get_employee_by_telegram_id_not_found(self):
        import database as db
        self.assertIsNone(run(db.get_employee_by_telegram_id(9999)))

    def test_get_employee_by_id(self):
        import database as db
        emp = run(db.create_employee("Фёдоров"))
        found = run(db.get_employee_by_id(emp["id"]))
        self.assertEqual(found["name"], "Фёдоров")

    def test_find_employees_by_partial_name(self):
        import database as db
        run(db.create_employee("Александров"))
        run(db.create_employee("Александрова"))
        run(db.create_employee("Иванов"))
        res = run(db.find_employees_by_name("Александр"))
        self.assertEqual(len(res), 2)

    def test_create_task_minimal(self):
        import database as db
        emp = run(db.create_employee("Минимум"))
        task = run(db.create_task(employee_id=emp["id"], description="Сделать"))
        self.assertEqual(task["description"], "Сделать")
        self.assertEqual(task["status"], "new")
        self.assertEqual(task["priority"], "normal")
        self.assertIsNone(task["category"])
        self.assertIsNone(task["comment"])

    def test_create_task_all_fields(self):
        import database as db
        emp = run(db.create_employee("Полный"))
        task = run(db.create_task(
            employee_id=emp["id"],
            description="Все поля",
            deadline="2026-05-15T18:00:00",
            priority="high",
            category="docs",
            comment="важно",
        ))
        self.assertEqual(task["priority"], "high")
        self.assertEqual(task["category"], "docs")
        self.assertEqual(task["comment"], "важно")
        self.assertEqual(task["deadline"], "2026-05-15T18:00:00")

    def test_get_task_includes_employee_name(self):
        import database as db
        emp = run(db.create_employee("Тестовый"))
        task = run(db.create_task(employee_id=emp["id"], description="Найти"))
        fetched = run(db.get_task(task["id"]))
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["employee_name"], "Тестовый")

    def test_get_task_not_found(self):
        import database as db
        self.assertIsNone(run(db.get_task(9999)))

    def test_update_task_status(self):
        import database as db
        emp = run(db.create_employee("Статус"))
        task = run(db.create_task(employee_id=emp["id"], description="Поменять"))
        updated = run(db.update_task_status(task["id"], "in_progress"))
        self.assertEqual(updated["status"], "in_progress")

    def test_update_task_status_done_sets_completed_at(self):
        import database as db
        emp = run(db.create_employee("Готово"))
        task = run(db.create_task(employee_id=emp["id"], description="Завершить"))
        now = datetime.now().isoformat(timespec="seconds")
        updated = run(db.update_task_status(task["id"], "done", completed_at=now))
        self.assertEqual(updated["status"], "done")
        self.assertEqual(updated["completed_at"], now)

    def test_list_tasks_by_employee(self):
        import database as db
        e1 = run(db.create_employee("Один"))
        e2 = run(db.create_employee("Два"))
        run(db.create_task(employee_id=e1["id"], description="T1"))
        run(db.create_task(employee_id=e1["id"], description="T2"))
        run(db.create_task(employee_id=e2["id"], description="T3"))
        self.assertEqual(len(run(db.list_tasks(employee_id=e1["id"]))), 2)
        self.assertEqual(len(run(db.list_tasks(employee_id=e2["id"]))), 1)

    def test_list_tasks_by_status(self):
        import database as db
        emp = run(db.create_employee("Статусник"))
        t1 = run(db.create_task(employee_id=emp["id"], description="Новая"))
        run(db.create_task(employee_id=emp["id"], description="Тоже новая"))
        run(db.update_task_status(t1["id"], "done"))
        self.assertEqual(len(run(db.list_tasks(status="new"))), 1)
        self.assertEqual(len(run(db.list_tasks(status="done"))), 1)

    def test_get_team_stats_structure(self):
        import database as db
        emp = run(db.create_employee("Аналитик"))
        run(db.create_task(employee_id=emp["id"], description="Задача"))
        stats = run(db.get_team_stats())
        self.assertIn("totals", stats)
        self.assertIn("by_employee", stats)
        self.assertIn("overdue_tasks", stats)
        self.assertEqual(stats["totals"]["total"], 1)
        self.assertEqual(stats["totals"]["new"], 1)

    def test_mark_deadline_reminded(self):
        import database as db
        emp = run(db.create_employee("Напомнить"))
        task = run(db.create_task(employee_id=emp["id"], description="Дедлайн",
                                  deadline="2030-01-01T09:00:00"))
        run(db.mark_deadline_reminded(task["id"]))
        self.assertEqual(run(db.get_task(task["id"]))["deadline_reminded"], 1)

    def test_mark_overdue_notified_sets_status(self):
        import database as db
        emp = run(db.create_employee("Просрочил"))
        task = run(db.create_task(employee_id=emp["id"], description="Просрочена",
                                  deadline="2020-01-01T09:00:00"))
        run(db.mark_overdue_notified(task["id"]))
        fetched = run(db.get_task(task["id"]))
        self.assertEqual(fetched["overdue_notified"], 1)
        self.assertEqual(fetched["status"], "overdue")

    def test_get_tasks_for_deadline_check(self):
        import database as db
        emp = run(db.create_employee("Дедлайн-чек"))
        run(db.create_task(employee_id=emp["id"], description="С дедлайном",
                           deadline="2030-01-01T09:00:00"))
        run(db.create_task(employee_id=emp["id"], description="Без дедлайна"))
        tasks = run(db.get_tasks_for_deadline_check())
        descs = {t["description"] for t in tasks}
        self.assertIn("С дедлайном", descs)
        self.assertNotIn("Без дедлайна", descs)

    def test_get_tasks_for_deadline_check_excludes_cancelled(self):
        import database as db
        emp = run(db.create_employee("Отменённый"))
        task = run(db.create_task(employee_id=emp["id"], description="Отменённая",
                                  deadline="2030-01-01T09:00:00"))
        run(db.update_task_status(task["id"], "cancelled"))
        tasks = run(db.get_tasks_for_deadline_check())
        descs = {t["description"] for t in tasks}
        self.assertNotIn("Отменённая", descs)

    def test_get_employee_stats_empty(self):
        import database as db
        emp = run(db.create_employee("Без задач"))
        stats = run(db.get_employee_stats(emp["id"]))
        self.assertEqual(stats["active"], 0)
        self.assertEqual(stats["done"], 0)

    def test_get_employee_stats_counts(self):
        import database as db
        emp = run(db.create_employee("Со задачами"))
        t1 = run(db.create_task(employee_id=emp["id"], description="Активная 1"))
        t2 = run(db.create_task(employee_id=emp["id"], description="Активная 2"))
        t3 = run(db.create_task(employee_id=emp["id"], description="Выполненная"))
        run(db.update_task_status(t3["id"], "done"))
        stats = run(db.get_employee_stats(emp["id"]))
        self.assertEqual(stats["active"], 2)
        self.assertEqual(stats["done"], 1)

    def test_rename_employee(self):
        import database as db
        emp = run(db.create_employee("Старое имя"))
        updated = run(db.rename_employee(emp["id"], "Новое имя"))
        self.assertEqual(updated["name"], "Новое имя")
        # Verify persisted
        fetched = run(db.get_employee_by_id(emp["id"]))
        self.assertEqual(fetched["name"], "Новое имя")

    def test_rename_reflects_in_tasks(self):
        import database as db
        emp = run(db.create_employee("Исходный"))
        task = run(db.create_task(employee_id=emp["id"], description="Задача"))
        run(db.rename_employee(emp["id"], "Переименованный"))
        fetched_task = run(db.get_task(task["id"]))
        self.assertEqual(fetched_task["employee_name"], "Переименованный")

    def test_delete_employee_removes_record(self):
        import database as db
        emp = run(db.create_employee("Удалить меня"))
        run(db.delete_employee(emp["id"]))
        self.assertIsNone(run(db.get_employee_by_id(emp["id"])))

    def test_delete_employee_cancels_active_tasks(self):
        import database as db
        import aiosqlite

        emp = run(db.create_employee("С задачами"))
        t1 = run(db.create_task(employee_id=emp["id"], description="Новая"))
        t2 = run(db.create_task(employee_id=emp["id"], description="В работе"))
        t3 = run(db.create_task(employee_id=emp["id"], description="Готова"))
        run(db.update_task_status(t2["id"], "in_progress"))
        run(db.update_task_status(t3["id"], "done"))

        cancelled_count = run(db.delete_employee(emp["id"]))
        self.assertEqual(cancelled_count, 2)  # t1 and t2 are active

        async def statuses():
            async with aiosqlite.connect(self._db_path) as conn:
                conn.row_factory = aiosqlite.Row
                cur = await conn.execute("SELECT id, status FROM tasks WHERE id IN (?,?,?)",
                                         (t1["id"], t2["id"], t3["id"]))
                return {row["id"]: row["status"] for row in await cur.fetchall()}

        st = run(statuses())
        self.assertEqual(st[t1["id"]], "cancelled")
        self.assertEqual(st[t2["id"]], "cancelled")
        self.assertEqual(st[t3["id"]], "done")  # done stays done

    def test_delete_employee_returns_cancelled_count(self):
        import database as db
        emp = run(db.create_employee("Считаем"))
        run(db.create_task(employee_id=emp["id"], description="T1"))
        run(db.create_task(employee_id=emp["id"], description="T2"))
        count = run(db.delete_employee(emp["id"]))
        self.assertEqual(count, 2)

    def test_reset_employee_code_generates_new_code(self):
        import database as db
        emp = run(db.create_employee("Сброс кода"))
        old_code = emp["reg_code"]
        updated = run(db.reset_employee_code(emp["id"]))
        self.assertNotEqual(updated["reg_code"], old_code)
        self.assertTrue(updated["reg_code"].isdigit())
        self.assertEqual(len(updated["reg_code"]), 6)

    def test_reset_employee_code_clears_telegram(self):
        import database as db
        emp = run(db.create_employee("Потерял Telegram"))
        run(db.register_employee(emp["reg_code"], telegram_id=123))
        linked = run(db.get_employee_by_id(emp["id"]))
        self.assertIsNotNone(linked["telegram_id"])

        run(db.reset_employee_code(emp["id"]))
        reset = run(db.get_employee_by_id(emp["id"]))
        self.assertIsNone(reset["telegram_id"])


# ══════════════════════════════════════════════════════════════════════════════
# tools.py — execute_tool (with real temp DB)
# ══════════════════════════════════════════════════════════════════════════════

class TestExecuteTool(unittest.TestCase):
    def setUp(self):
        import importlib
        self._db_path = _tmp_db()
        config.DATABASE_PATH = self._db_path
        import database as db
        importlib.reload(db)
        run(db.init_db())
        import tools
        importlib.reload(tools)

    def tearDown(self):
        try:
            os.unlink(self._db_path)
        except OSError:
            pass

    def _tool(self, name, inp=None, caller=42):
        import tools
        return run(tools.execute_tool(name, inp or {}, caller_id=caller))

    def _json(self, name, inp=None, caller=42):
        import json
        return json.loads(self._tool(name, inp, caller))

    def test_add_employee(self):
        d = self._json("add_employee", {"name": "Тест"})
        self.assertEqual(d["name"], "Тест")
        self.assertIn("reg_code", d)
        self.assertIn("instruction", d)

    def test_list_employees_empty(self):
        d = self._json("list_employees")
        self.assertEqual(d, [])

    def test_list_employees_after_add(self):
        self._json("add_employee", {"name": "Сотрудник"})
        d = self._json("list_employees")
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["name"], "Сотрудник")

    def test_propose_task_unknown_employee(self):
        d = self._json("propose_task", {"employee_name": "Никто", "description": "X"})
        self.assertIn("error", d)

    def test_propose_task_creates_pending(self):
        import state as st
        st._pending.clear()
        self._json("add_employee", {"name": "Карточкин"})
        d = self._json("propose_task", {
            "employee_name": "Карточкин",
            "description": "Выполнить задание",
        })
        self.assertTrue(d.get("pending"))
        self.assertIn("card", d)
        pending = st.get_pending(42)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["description"], "Выполнить задание")

    def test_get_task_not_found(self):
        d = self._json("get_task", {"task_id": 9999})
        self.assertIn("error", d)

    def test_get_team_stats(self):
        d = self._json("get_team_stats")
        self.assertIn("totals", d)

    def test_list_tasks_empty(self):
        d = self._json("list_tasks")
        self.assertEqual(d, [])

    def test_unknown_tool_returns_error(self):
        d = self._json("no_such_tool")
        self.assertIn("error", d)

    def test_confirm_task_not_found(self):
        d = self._json("confirm_task", {"task_id": 9999})
        self.assertIn("error", d)

    def test_confirm_task_already_done(self):
        import database as db
        emp = run(db.create_employee("Завершил"))
        task = run(db.create_task(employee_id=emp["id"], description="Готово"))
        run(db.update_task_status(task["id"], "done"))
        d = self._json("confirm_task", {"task_id": task["id"]})
        self.assertIn("error", d)

    def test_full_lifecycle(self):
        import database as db, state as st
        st._pending.clear()

        # add employee
        self._json("add_employee", {"name": "Жизненный"})
        emp = run(db.list_employees())[0]

        # create task directly
        task = run(db.create_task(employee_id=emp["id"], description="Цикл"))

        # confirm
        d = self._json("confirm_task", {"task_id": task["id"]})
        self.assertTrue(d["ok"])

        # re-confirm should error
        d2 = self._json("confirm_task", {"task_id": task["id"]})
        self.assertIn("error", d2)

    def test_reject_task(self):
        import database as db
        emp = run(db.create_employee("Отклонил"))
        task = run(db.create_task(employee_id=emp["id"], description="Вернуть"))
        d = self._json("reject_task", {"task_id": task["id"]})
        self.assertTrue(d["ok"])
        self.assertEqual(d["status"], "in_progress")

    def test_my_tasks_not_registered(self):
        d = self._json("my_tasks", caller=9999)
        self.assertIn("error", d)

    def test_start_task_not_yours(self):
        import database as db
        emp = run(db.create_employee("Другой"))
        task = run(db.create_task(employee_id=emp["id"], description="Чужая"))
        d = self._json("start_task", {"task_id": task["id"]}, caller=9999)
        self.assertIn("error", d)

    def test_rename_employee_tool(self):
        self._json("add_employee", {"name": "Иванов"})
        d = self._json("rename_employee", {"employee_name": "Иванов", "new_name": "Иванова"})
        self.assertTrue(d["ok"])
        self.assertEqual(d["old_name"], "Иванов")
        self.assertEqual(d["new_name"], "Иванова")

    def test_rename_employee_not_found(self):
        d = self._json("rename_employee", {"employee_name": "Никто", "new_name": "Кто-то"})
        self.assertIn("error", d)

    def test_rename_employee_ambiguous(self):
        self._json("add_employee", {"name": "Иванов Иван"})
        self._json("add_employee", {"name": "Иванов Пётр"})
        d = self._json("rename_employee", {"employee_name": "Иванов", "new_name": "X"})
        self.assertIn("error", d)

    def test_delete_employee_tool(self):
        self._json("add_employee", {"name": "УдалитьМеня"})
        d = self._json("delete_employee", {"employee_name": "УдалитьМеня"})
        self.assertTrue(d["ok"])
        self.assertEqual(d["deleted"], "УдалитьМеня")

    def test_delete_employee_tool_not_found(self):
        d = self._json("delete_employee", {"employee_name": "Призрак"})
        self.assertIn("error", d)

    def test_delete_employee_tool_cancels_tasks(self):
        import database as db
        self._json("add_employee", {"name": "СоЗадачами"})
        emp = run(db.list_employees())[0]
        run(db.create_task(employee_id=emp["id"], description="Активная"))
        d = self._json("delete_employee", {"employee_name": "СоЗадачами"})
        self.assertTrue(d["ok"])
        self.assertEqual(d["cancelled_tasks"], 1)

    def test_status_ru_has_cancelled(self):
        from tools import STATUS_RU
        self.assertIn("cancelled", STATUS_RU)
        self.assertEqual(STATUS_RU["cancelled"], "отменена")


class TestEmployeeManagementKbs(unittest.TestCase):
    """Test employee management keyboard builders."""

    def _all_cb(self, kb):
        return [btn.callback_data for row in kb.inline_keyboard for btn in row]

    def test_emp_list_kb(self):
        from bot import _emp_list_kb
        employees = [{"id": 1, "name": "Иванов"}, {"id": 2, "name": "Петров"}]
        cb = self._all_cb(_emp_list_kb(employees))
        self.assertIn("em:card:1", cb)
        self.assertIn("em:card:2", cb)

    def test_emp_card_kb_all_actions(self):
        from bot import _emp_card_kb
        cb = self._all_cb(_emp_card_kb(7))
        self.assertIn("em:tasks:7", cb)
        self.assertIn("em:rename:7", cb)
        self.assertIn("em:del:7", cb)
        self.assertIn("em:newcode:7", cb)
        self.assertIn("em:list", cb)

    def test_emp_delete_kb(self):
        from bot import _emp_delete_kb
        cb = self._all_cb(_emp_delete_kb(3))
        self.assertIn("em:delok:3", cb)
        self.assertIn("em:card:3", cb)

    def test_emp_cancel_kb(self):
        from bot import _emp_cancel_kb
        cb = self._all_cb(_emp_cancel_kb())
        self.assertIn("em:cancel", cb)

    def test_format_emp_card_connected(self):
        from bot import _format_emp_card
        emp = {"name": "Иванов", "telegram_id": 123, "created_at": "2026-05-08 10:00:00"}
        stats = {"active": 3, "done": 7}
        card = _format_emp_card(emp, stats)
        self.assertIn("Иванов", card)
        self.assertIn("подключён", card)
        self.assertIn("08.05.2026", card)
        self.assertIn("Активных задач: 3", card)
        self.assertIn("Выполненных: 7", card)

    def test_format_emp_card_not_connected(self):
        from bot import _format_emp_card
        emp = {"name": "Петров", "telegram_id": None, "created_at": "2026-01-01 00:00:00"}
        stats = {"active": 0, "done": 0}
        card = _format_emp_card(emp, stats)
        self.assertIn("не подключён", card)

    def test_format_emp_card_no_date(self):
        from bot import _format_emp_card
        emp = {"name": "Без даты", "telegram_id": None, "created_at": None}
        stats = {"active": 0, "done": 0}
        card = _format_emp_card(emp, stats)
        self.assertIn("—", card)

    def test_emp_wizard_state_defined(self):
        from bot import EmpWizard
        state_names = {s.state.split(":")[-1] for s in EmpWizard.__states__}
        self.assertIn("rename", state_names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
