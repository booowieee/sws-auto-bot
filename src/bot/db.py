import hashlib
import json
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import aiosqlite

from src.config import Config
from src.logger import logger


class BotDatabase:
    """Manages SQLite storage for whitelist users, watch tasks, execution logs, and LLM cache."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (Config.DATA_DIR / "bot.db")

    async def init_db(self) -> None:
        """Initializes database schema if tables do not exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL;")
            await db.execute("PRAGMA foreign_keys = ON;")

            # 1. Users & Whitelist Table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    role TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                );
            """)

            # 2. Watch Tasks Table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS watch_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    poll_interval INTEGER NOT NULL DEFAULT 30,
                    max_hours REAL NOT NULL DEFAULT 72.0,
                    is_test BOOLEAN NOT NULL DEFAULT 0,
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_by INTEGER,
                    created_at TEXT NOT NULL,
                    last_checked_at TEXT,
                    status TEXT NOT NULL DEFAULT 'watching'
                );
            """)

            # 3. Execution History Table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS execution_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_sec REAL NOT NULL,
                    total_fields INTEGER NOT NULL DEFAULT 0,
                    filled_fields_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    created_at TEXT NOT NULL
                );
            """)

            # 4. Semantic Cache Table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS semantic_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_key TEXT UNIQUE NOT NULL,
                    field_label TEXT NOT NULL,
                    matched_key TEXT NOT NULL,
                    resolved_value TEXT,
                    selected_option TEXT,
                    confidence REAL NOT NULL DEFAULT 85.0,
                    created_at TEXT NOT NULL
                );
            """)

            await db.commit()
            logger.info(f"Bot database initialized at {self.db_path}")

    # ==================== User & Whitelist Operations ====================

    async def get_user_role(self, user_id: int) -> Optional[str]:
        """Returns the role of a user ('admin', 'operator', 'pending', or None)."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT role FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def is_authorized(self, user_id: int) -> bool:
        """Returns True if user has 'admin' or 'operator' role."""
        role = await self.get_user_role(user_id)
        return role in ("admin", "operator")

    async def add_or_update_user(
        self, user_id: int, username: Optional[str], full_name: Optional[str], role: str = "pending"
    ) -> None:
        """Inserts or updates user access role."""
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, username, full_name, role, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name,
                    role = excluded.role;
                """,
                (user_id, username or "", full_name or "", role, now),
            )
            await db.commit()

    async def get_all_users(self) -> List[Dict[str, Any]]:
        """Returns list of all registered users."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users ORDER BY created_at DESC") as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def delete_user(self, user_id: int) -> bool:
        """Removes user from database."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            await db.commit()
            return cursor.rowcount > 0

    # ==================== Watch Tasks Operations ====================

    async def add_watch_task(
        self,
        url: str,
        title: str,
        poll_interval: int = 30,
        max_hours: float = 72.0,
        is_test: bool = False,
        created_by: Optional[int] = None,
    ) -> int:
        """Adds or reactivates a watch task."""
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO watch_tasks (url, title, poll_interval, max_hours, is_test, is_active, created_by, created_at, status)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, 'watching')
                ON CONFLICT(url) DO UPDATE SET
                    title = excluded.title,
                    poll_interval = excluded.poll_interval,
                    max_hours = excluded.max_hours,
                    is_test = excluded.is_test,
                    is_active = 1,
                    status = 'watching';
                """,
                (url, title, poll_interval, max_hours, 1 if is_test else 0, created_by, now),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def get_active_watch_tasks(self) -> List[Dict[str, Any]]:
        """Returns all currently active watch tasks."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM watch_tasks WHERE is_active = 1") as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def deactivate_watch_task(self, url: str) -> bool:
        """Deactivates a watch task by URL."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE watch_tasks SET is_active = 0, status = 'cancelled' WHERE url = ?", (url,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def update_watch_status(self, url: str, status: str) -> None:
        """Updates status and timestamp for a task."""
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE watch_tasks SET status = ?, last_checked_at = ? WHERE url = ?", (status, now, url)
            )
            await db.commit()

    # ==================== Execution History Operations ====================

    async def log_execution(
        self,
        url: str,
        status: str,
        duration_sec: float,
        total_fields: int = 0,
        filled_fields_count: int = 0,
        error_message: Optional[str] = None,
    ) -> None:
        """Logs a completed form execution."""
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO execution_history (url, status, duration_sec, total_fields, filled_fields_count, error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (url, status, duration_sec, total_fields, filled_fields_count, error_message, now),
            )
            await db.commit()

    async def get_recent_executions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Returns recent form execution records."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM execution_history ORDER BY id DESC LIMIT ?", (limit,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    # ==================== Semantic Cache Operations ====================

    @staticmethod
    def generate_cache_key(label: str, field_type: str, options: Optional[List[str]] = None) -> str:
        """Generates deterministic sha256 hash for field semantics."""
        raw = f"{label.strip().lower()}|{field_type.strip().lower()}|{sorted(options or [])}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def get_cached_field(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Retrieves cached resolution if present."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT matched_key, resolved_value, selected_option, confidence FROM semantic_cache WHERE cache_key = ?",
                (cache_key,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def set_cached_field(
        self,
        cache_key: str,
        label: str,
        matched_key: str,
        resolved_value: Optional[str] = "",
        selected_option: Optional[str] = None,
        confidence: float = 85.0,
    ) -> None:
        """Caches an LLM resolution."""
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO semantic_cache (cache_key, field_label, matched_key, resolved_value, selected_option, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    matched_key = excluded.matched_key,
                    resolved_value = excluded.resolved_value,
                    selected_option = excluded.selected_option,
                    confidence = excluded.confidence;
                """,
                (cache_key, label, matched_key, resolved_value or "", selected_option, confidence, now),
            )
            await db.commit()
