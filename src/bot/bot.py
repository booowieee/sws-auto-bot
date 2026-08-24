import asyncio
import signal
import sys
from typing import Optional
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.bot.db import BotDatabase
from src.bot.handlers import router
from src.config import Config
from src.logger import logger
from src.watcher_manager import WatcherManager


class AutoBotTelegramService:
    """Manages the long-polling Telegram Bot and background watcher loop."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or Config.TELEGRAM_BOT_TOKEN
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not configured in .env or environment variables.")

        self.bot = Bot(
            token=self.token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.dp = Dispatcher()
        self.db = BotDatabase()
        self.watcher_mgr = WatcherManager(db=self.db)
        self._setup_routers()

    def _setup_routers(self) -> None:
        # Dependency injection of DB and WatcherManager into all handlers
        self.dp["db"] = self.db
        self.dp["watcher_mgr"] = self.watcher_mgr
        self.dp.include_router(router)

    async def start(self) -> None:
        """Initializes database, starts watcher manager and begins long-polling."""
        logger.info("Starting SWS Auto-Bot Telegram Control Plane...")
        await self.db.init_db()
        await self.watcher_mgr.initialize()

        # Check bot connection
        try:
            bot_info = await self.bot.get_me()
            logger.info(f"Telegram Bot connected: @{bot_info.username} (ID: {bot_info.id})")
        except Exception as e:
            logger.error(f"Failed to connect to Telegram Bot API: {e}")
            raise

        try:
            await self.dp.start_polling(self.bot, allowed_updates=["message", "callback_query"])
        finally:
            await self.watcher_mgr.stop_all()
            await self.bot.session.close()
            logger.info("Telegram Bot session closed.")


async def run_bot_service() -> None:
    """Entry point for running the interactive Telegram bot service."""
    service = AutoBotTelegramService()
    await service.start()
