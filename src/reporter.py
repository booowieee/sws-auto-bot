import asyncio
import json
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiohttp
from playwright.async_api import Page

from src.config import Config
from src.logger import logger
from src.models import ExecutionReport, FormStatus


class ExecutionReporter:
    """Manages screenshot lifecycle, structured JSON audit logs, and Telegram dispatches."""

    def __init__(self, page: Page):
        self.page = page
        self.screenshots: List[Path] = []

    async def capture_milestone(self, name: str) -> Optional[Path]:
        """Captures a full-page screenshot for audit."""
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        file_name = f"{timestamp}_{name}.png"
        file_path = Config.SCREENSHOTS_DIR / file_name

        try:
            await self.page.screenshot(path=str(file_path), full_page=True)
            self.screenshots.append(file_path)
            logger.info(f"Captured screenshot: {file_name}")
            return file_path
        except Exception as e:
            logger.error(f"Failed to capture screenshot {name}: {e}")
            return None

    def save_json_log(self, report: ExecutionReport) -> Path:
        """Saves execution details into a structured JSON log."""
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        log_file = Config.LOGS_DIR / f"fill_{timestamp}.json"

        try:
            with open(log_file, "w", encoding="utf-8") as f:
                json.dump(report.model_dump(), f, ensure_ascii=False, indent=2)
            logger.info(f"Execution report saved: {log_file}")
            return log_file
        except Exception as e:
            logger.error(f"Failed to save JSON log: {e}")
            return log_file

    async def send_telegram_report(self, report: ExecutionReport) -> None:
        """
        Dispatches report with captured screenshots to Telegram,
        then immediately unlinks screenshots to preserve disk storage.
        """
        token = Config.TELEGRAM_BOT_TOKEN
        chat_id = Config.TELEGRAM_CHAT_ID

        if not token or not chat_id:
            logger.info("Telegram not configured (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing). Skipping notification.")
            self._cleanup_local_screenshots()
            return

        status_text = {
            FormStatus.SUCCESS: "SUCCESS",
            FormStatus.DRY_RUN: "DRY-RUN (TEST OK)",
            FormStatus.CLOSED: "CLOSED",
            FormStatus.FAILED: "FAILED",
        }.get(report.status, report.status.value.upper())

        caption = (
            f"<b>[SWS Auto-Bot] Отчет выполнения: {status_text}</b>\n\n"
            f"<b>URL:</b> <code>{report.url}</code>\n"
            f"<b>Статус:</b> {report.status.value}\n"
            f"<b>Время выполнения:</b> {report.duration_sec:.1f} сек.\n"
            f"<b>Всего полей:</b> {report.total_fields}\n"
            f"<b>Заполнено:</b> {len(report.filled_fields)}\n"
        )

        if report.unmatched_required_fields:
            caption += f"\n<b>Нераспознанные обязательные поля:</b>\n"
            for f in report.unmatched_required_fields[:5]:
                caption += f"• <code>{f}</code>\n"

        if report.error_message:
            caption += f"\n<b>Ошибка:</b> <i>{report.error_message}</i>\n"

        api_url = f"https://api.telegram.org/bot{token}"

        try:
            async with aiohttp.ClientSession() as session:
                if self.screenshots:
                    for idx, screen_path in enumerate(self.screenshots):
                        if not screen_path.exists():
                            continue

                        current_caption = caption if idx == 0 else f"Скриншот этапа: {screen_path.name}"
                        form_data = aiohttp.FormData()
                        form_data.add_field("chat_id", str(chat_id))
                        form_data.add_field("caption", current_caption[:1024])
                        form_data.add_field("parse_mode", "HTML")

                        photo_bytes = await asyncio.to_thread(screen_path.read_bytes)
                        form_data.add_field("photo", photo_bytes, filename=screen_path.name, content_type="image/png")

                        async with session.post(f"{api_url}/sendPhoto", data=form_data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                            if resp.status != 200:
                                resp_text = await resp.text()
                                logger.error(f"Telegram sendPhoto failed: {resp_text}")
                else:
                    payload = {
                        "chat_id": chat_id,
                        "text": caption,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    }
                    async with session.post(f"{api_url}/sendMessage", json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status != 200:
                            logger.error(f"Telegram sendMessage failed: {await resp.text()}")

            logger.info("Telegram notification sent successfully.")
        except Exception as e:
            logger.error(f"Failed to send Telegram report: {e}")
        finally:
            # Crucial: Immediately unlink local screenshots to prevent disk overflow
            self._cleanup_local_screenshots()

    def _cleanup_local_screenshots(self) -> None:
        """Deletes all captured screenshot files from local disk."""
        for screen_path in self.screenshots:
            try:
                if screen_path.exists():
                    screen_path.unlink(missing_ok=True)
                    logger.debug(f"Removed screenshot from disk: {screen_path.name}")
            except Exception as e:
                logger.warning(f"Could not delete screenshot {screen_path}: {e}")
        self.screenshots.clear()
