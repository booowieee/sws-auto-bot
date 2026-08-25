import asyncio
from pathlib import Path
import pytest
from src.bot.db import BotDatabase
from src.bot.keyboards import (
    get_form_trigger_keyboard,
    get_status_dashboard_keyboard,
    get_whitelist_approval_keyboard,
)


@pytest.fixture
def test_db(tmp_path: Path):
    db_file = tmp_path / "test_bot.db"
    db = BotDatabase(db_path=db_file)
    asyncio.run(db.init_db())
    return db


def test_bot_database_init(test_db):
    assert test_db.db_path.exists()


def test_user_whitelist_operations(test_db):
    # 1. Initially unauthorized
    assert asyncio.run(test_db.is_authorized(12345)) is False
    assert asyncio.run(test_db.get_user_role(12345)) is None

    # 2. Add pending user
    asyncio.run(test_db.add_or_update_user(12345, "john_doe", "John Doe", role="pending"))
    assert asyncio.run(test_db.is_authorized(12345)) is False
    assert asyncio.run(test_db.get_user_role(12345)) == "pending"

    # 3. Promote to operator
    asyncio.run(test_db.add_or_update_user(12345, "john_doe", "John Doe", role="operator"))
    assert asyncio.run(test_db.is_authorized(12345)) is True
    assert asyncio.run(test_db.get_user_role(12345)) == "operator"

    # 4. Get all users
    users = asyncio.run(test_db.get_all_users())
    assert len(users) == 1
    assert users[0]["user_id"] == 12345

    # 5. Delete user
    deleted = asyncio.run(test_db.delete_user(12345))
    assert deleted is True
    assert asyncio.run(test_db.is_authorized(12345)) is False


def test_watch_tasks_operations(test_db):
    url = "https://docs.google.com/forms/d/e/123/viewform"

    # 1. Add watch task
    task_id = asyncio.run(test_db.add_watch_task(url=url, title="Concordia Form", poll_interval=15, is_test=True))
    assert task_id > 0

    # 2. Get active tasks
    tasks = asyncio.run(test_db.get_active_watch_tasks())
    assert len(tasks) == 1
    assert tasks[0]["url"] == url
    assert tasks[0]["is_test"] == 1

    # 3. Update status
    asyncio.run(test_db.update_watch_status(url, "open"))

    # 4. Deactivate
    deactivated = asyncio.run(test_db.deactivate_watch_task(url))
    assert deactivated is True
    assert len(asyncio.run(test_db.get_active_watch_tasks())) == 0


def test_execution_history_logging(test_db):
    asyncio.run(
        test_db.log_execution(
            url="https://forms.gle/xyz",
            status="success",
            duration_sec=3.4,
            total_fields=12,
            filled_fields_count=12,
        )
    )

    history = asyncio.run(test_db.get_recent_executions(limit=5))
    assert len(history) == 1
    assert history[0]["url"] == "https://forms.gle/xyz"
    assert history[0]["status"] == "success"
    assert history[0]["duration_sec"] == 3.4


def test_semantic_cache_operations(test_db):
    key = BotDatabase.generate_cache_key("Mărimea pantofilor", "radio", ["42", "43", "44"])
    assert isinstance(key, str)
    assert len(key) == 64

    # 1. Cache miss
    cached = asyncio.run(test_db.get_cached_field(key))
    assert cached is None

    # 2. Set cache
    asyncio.run(
        test_db.set_cached_field(
            cache_key=key,
            label="Mărimea pantofilor",
            matched_key="ppe.shoe_size",
            resolved_value="43",
            selected_option="43 EU",
            confidence=95.0,
        )
    )

    # 3. Cache hit
    cached = asyncio.run(test_db.get_cached_field(key))
    assert cached is not None
    assert cached["matched_key"] == "ppe.shoe_size"
    assert cached["resolved_value"] == "43"
    assert cached["selected_option"] == "43 EU"


def test_keyboards_structure():
    kb_trigger = get_form_trigger_keyboard(task_id=1, url="https://forms.gle/test")
    assert len(kb_trigger.inline_keyboard) == 2
    assert "form_submit:1" in kb_trigger.inline_keyboard[0][0].callback_data

    kb_wl = get_whitelist_approval_keyboard(user_id=99999)
    assert len(kb_wl.inline_keyboard) == 1
    assert "wl_approve:99999" in kb_wl.inline_keyboard[0][0].callback_data

    kb_dash = get_status_dashboard_keyboard()
    assert len(kb_dash.inline_keyboard) == 2
