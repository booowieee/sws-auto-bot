import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
from src.bot.db import BotDatabase
from src.watcher import FormWatcher
from src.watcher_manager import WatcherManager


@pytest.fixture
def manager(tmp_path: Path):
    db_file = tmp_path / "test_wm.db"
    db = BotDatabase(db_path=db_file)
    asyncio.run(db.init_db())
    return WatcherManager(db=db)


def test_watcher_manager_add_and_stop(manager):
    async def _test():
        url = "https://docs.google.com/forms/d/e/sample/viewform"

        with patch.object(FormWatcher, "_check_form_open", AsyncMock(return_value=False)):
            # Start watching
            started = await manager.start_watching(
                url=url,
                title="Sample Form",
                poll_interval=10,
                is_test=True,
                save_to_db=True,
            )
            assert started == 1
            assert url in manager._tasks

            # Duplicate start check
            started_dup = await manager.start_watching(url=url)
            assert started_dup == 0

            # Check stats
            stats = manager.get_task_stats()
            assert len(stats) == 1
            assert stats[0]["url"] == url
            assert stats[0]["status"] == "watching"

            # Stop watching
            stopped = await manager.stop_watching(url)
            assert stopped is True
            assert url not in manager._tasks

    asyncio.run(_test())


def test_watcher_manager_stop_all(manager):
    async def _test():
        urls = [
            "https://docs.google.com/forms/d/e/1/viewform",
            "https://docs.google.com/forms/d/e/2/viewform",
        ]
        with patch.object(FormWatcher, "_check_form_open", AsyncMock(return_value=False)):
            for u in urls:
                await manager.start_watching(url=u, poll_interval=10, is_test=True)

            assert len(manager._tasks) == 2
            await manager.stop_all(deactivate_db=False)
            assert len(manager._tasks) == 0

            # Verify tasks are preserved in DB
            active_tasks = await manager.db.get_active_watch_tasks()
            assert len(active_tasks) == 2

            # Verify initialize resumes them
            new_manager = WatcherManager(db=manager.db)
            await new_manager.initialize()
            assert len(new_manager._tasks) == 2

            # Cleanup
            await new_manager.stop_all(deactivate_db=True)

    asyncio.run(_test())
