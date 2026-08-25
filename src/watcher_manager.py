import asyncio
import random
import re
import time
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional
import aiohttp

from src.bot.db import BotDatabase
from src.config import Config
from src.logger import logger
from src.watcher import FormWatcher, CLOSED_MARKERS, OPEN_INDICATORS

# Module-level lock to prevent concurrent Playwright browser sessions
_browser_lock = asyncio.Lock()


class WatcherManager:
    """Orchestrates concurrent 24/7 background form watchers with persistence and adaptive polling."""

    def __init__(self, db: Optional[BotDatabase] = None):
        self.db = db or BotDatabase()
        self._tasks: Dict[str, asyncio.Task] = {}
        self._watchers: Dict[str, FormWatcher] = {}
        self._stats: Dict[str, Dict[str, Any]] = {}
        self._is_running = True

    async def initialize(self) -> None:
        """Initializes database and auto-resumes active watch tasks from SQLite."""
        await self.db.init_db()
        self._is_running = True

        active_tasks = await self.db.get_active_watch_tasks()
        logger.info(f"WatcherManager: Loaded {len(active_tasks)} active watch tasks from database.")

        for task_info in active_tasks:
            url = task_info["url"]
            interval = task_info.get("poll_interval", 30)
            max_hours = task_info.get("max_hours", 72.0)
            is_test = bool(task_info.get("is_test", 0))
            title = task_info.get("title", "Google Form")

            await self.start_watching(
                url=url,
                title=title,
                poll_interval=interval,
                max_hours=max_hours,
                is_test=is_test,
                save_to_db=False,
            )

    async def start_watching(
        self,
        url: str,
        title: str = "Google Form",
        poll_interval: int = 30,
        max_hours: float = 72.0,
        is_test: bool = False,
        created_by: Optional[int] = None,
        save_to_db: bool = True,
    ) -> int:
        """Starts watching a single form URL in an async background task."""
        # Clean up finished tasks for this URL
        if url in self._tasks:
            if not self._tasks[url].done():
                logger.info(f"Watcher already running for URL: {url}")
                return 0
            # Previous task finished — remove stale references
            del self._tasks[url]
            self._watchers.pop(url, None)

        if save_to_db:
            await self.db.add_watch_task(
                url=url,
                title=title,
                poll_interval=poll_interval,
                max_hours=max_hours,
                is_test=is_test,
                created_by=created_by,
            )

        watcher = FormWatcher(
            url=url,
            poll_interval=poll_interval,
            max_hours=max_hours,
            is_test=is_test,
        )
        self._watchers[url] = watcher
        self._stats[url] = {
            "title": title,
            "url": url,
            "started_at": datetime.now(UTC).isoformat(),
            "check_count": 0,
            "last_checked_at": None,
            "status": "watching",
            "is_test": is_test,
            "poll_interval": poll_interval,
        }

        # Spawn task
        task = asyncio.create_task(self._run_watcher_task(url, watcher))
        self._tasks[url] = task
        logger.info(f"WatcherManager: Started background watching for '{title}' ({url})")
        return 1

    async def stop_watching(self, url: str) -> bool:
        """Stops watching a form URL and updates database."""
        if url in self._watchers:
            self._watchers[url].stop()

        if url in self._tasks:
            task = self._tasks.pop(url)
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if url in self._watchers:
            del self._watchers[url]

        if url in self._stats:
            self._stats[url]["status"] = "cancelled"

        await self.db.deactivate_watch_task(url)
        logger.info(f"WatcherManager: Stopped watching URL: {url}")
        return True

    async def stop_all(self) -> None:
        """Gracefully cancels all running watch tasks."""
        self._is_running = False
        urls = list(self._tasks.keys())
        for url in urls:
            await self.stop_watching(url)
        self._tasks.clear()
        logger.info("WatcherManager: All watch tasks stopped.")

    def get_task_stats(self) -> List[Dict[str, Any]]:
        """Returns live metrics for all registered tasks."""
        return list(self._stats.values())

    async def _run_watcher_task(self, url: str, watcher: FormWatcher) -> None:
        """Internal worker executing adaptive polling loop for a single form."""
        start_time = time.time()
        backoff_delay = 0.0

        try:
            while not watcher._stop_event.is_set() and self._is_running:
                now_iso = datetime.now(UTC).isoformat()
                self._stats[url]["check_count"] += 1
                self._stats[url]["last_checked_at"] = now_iso

                # Check status
                is_open = await watcher._check_form_open()

                # Update status in DB every 10 checks
                if self._stats[url]["check_count"] % 10 == 0:
                    await self.db.update_watch_status(url, "watching")

                if is_open:
                    logger.info(f"WatcherManager: Form is OPEN! Triggering autofill: {url}")
                    self._stats[url]["status"] = "open"
                    await self.db.update_watch_status(url, "open")

                    # Dispatch alert
                    await watcher._notify(
                        f"<b>🚨 [SWS Watcher] FORM OPENED!</b>\n\n"
                        f"<b>URL:</b> <code>{url}</code>\n"
                        f"<b>Checks:</b> {self._stats[url]['check_count']}\n"
                        f"Launching automated form filler..."
                    )

                    # Trigger autofill pipeline (locked to prevent concurrent browser sessions)
                    from src.__main__ import run_autofill
                    async with _browser_lock:
                        exit_code = await run_autofill(url=url, is_test=watcher.is_test, headless=True)
                    status_str = "success" if exit_code == 0 else "failed"
                    self._stats[url]["status"] = status_str
                    await self.db.update_watch_status(url, status_str)

                    if exit_code == 0:
                        await self.db.deactivate_watch_task(url)
                        break

                    logger.warning(f"WatcherManager: Autofill failed for {url}. Retrying watch loop.")
                    await asyncio.sleep(max(10, watcher.poll_interval))
                    continue

                # Sleep with randomized jitter (e.g. ±15% of interval) to avoid robotic cadence
                jitter = random.uniform(-0.15, 0.15) * watcher.poll_interval
                actual_sleep = max(3.0, watcher.poll_interval + jitter + backoff_delay)

                try:
                    await asyncio.wait_for(watcher._stop_event.wait(), timeout=actual_sleep)
                    break
                except asyncio.TimeoutError:
                    pass

        except asyncio.CancelledError:
            logger.debug(f"Watcher task cancelled for {url}")
        except Exception as e:
            logger.exception(f"Unhandled error in watcher task for {url}: {e}")
            if url in self._stats:
                self._stats[url]["status"] = "error"
        finally:
            if url in self._tasks:
                del self._tasks[url]
