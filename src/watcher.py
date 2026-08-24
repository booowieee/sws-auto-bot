import asyncio
import signal
import time
from typing import Optional

import aiohttp

from src.config import Config
from src.logger import logger


# Multilingual closed-form markers (borrowed from sws_monitor_bot detection logic)
CLOSED_MARKERS = [
    "closedform",
    "nu mai acceptă răspunsuri",
    "nu se mai acceptă răspunsuri",
    "formularul nu mai acceptă",
    "no longer accepting responses",
    "is no longer accepting",
    "больше не принимает ответы",
    "форма закрыта",
    "больше не принимает ответов",
]

# Active form indicators in HTML
OPEN_INDICATORS = [
    'role="listitem"',
    'type="text"',
    "<textarea",
    'role="radiogroup"',
    'data-params',
]


class FormWatcher:
    """Polls a Google Form URL and triggers autofill when the form opens."""

    def __init__(
        self,
        url: str,
        poll_interval: int = 30,
        max_hours: float = 72,
        is_test: bool = False,
        headless: Optional[bool] = None,
    ):
        self.url = url
        self.poll_interval = poll_interval
        self.max_duration = max_hours * 3600
        self.is_test = is_test
        self.headless = headless
        self._stop_event = asyncio.Event()

    def _setup_signal_handlers(self):
        """Register OS signal handlers for graceful shutdown."""
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop = asyncio.get_running_loop()
                loop.add_signal_handler(sig, self.stop)
            except (RuntimeError, NotImplementedError):
                # Windows doesn't support add_signal_handler; use signal.signal fallback
                signal.signal(sig, lambda s, f: self.stop())

    def stop(self):
        """Signal the watch loop to stop."""
        logger.info("Received shutdown signal. Stopping watcher...")
        self._stop_event.set()

    async def watch(self) -> int:
        """Main watch loop. Polls the form, triggers autofill when open. Returns exit code."""
        self._setup_signal_handlers()
        start = time.time()
        check_count = 0

        logger.info(
            f"Watcher started: {self.url} "
            f"(interval={self.poll_interval}s, max_hours={self.max_duration / 3600:.0f}h, "
            f"test_mode={self.is_test})"
        )
        await self._notify(
            f"<b>[SWS Watcher] Started</b>\n\n"
            f"<b>URL:</b> <code>{self.url}</code>\n"
            f"<b>Interval:</b> {self.poll_interval}s\n"
            f"<b>Mode:</b> {'Test (dry-run)' if self.is_test else 'LIVE (auto-submit)'}"
        )

        while not self._stop_event.is_set():
            elapsed = time.time() - start
            if elapsed > self.max_duration:
                logger.info("Max watch duration reached. Exiting.")
                await self._notify("<b>[SWS Watcher]</b> Max watch duration reached. Stopping.")
                return 1

            check_count += 1
            is_open = await self._check_form_open()

            hours_running = elapsed / 3600
            if is_open:
                logger.info(f"Form is OPEN after {check_count} checks ({hours_running:.1f}h). Launching autofill...")
                await self._notify(
                    f"<b>[SWS Watcher] FORM OPEN</b>\n\n"
                    f"<b>URL:</b> <code>{self.url}</code>\n"
                    f"<b>Checks:</b> {check_count}\n"
                    f"<b>Waited:</b> {hours_running:.1f}h\n\n"
                    f"Launching autofill..."
                )

                exit_code = await self._run_autofill()

                status = "SUCCESS" if exit_code == 0 else "FAILED"
                logger.info(f"Autofill completed: {status}")
                return exit_code

            # Log progress every 100 checks (silent otherwise)
            if check_count % 100 == 0:
                logger.info(f"Still watching... ({check_count} checks, {hours_running:.1f}h elapsed)")

            # Interruptible sleep
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval)
                break  # stop_event was set
            except asyncio.TimeoutError:
                pass  # Normal timeout, continue polling

        logger.info("Watcher stopped.")
        return 0

    async def _check_form_open(self) -> bool:
        """Lightweight HTTP check for form status (no Playwright needed)."""
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            headers = {
                "User-Agent": Config.USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9,ro;q=0.8,ru;q=0.7",
            }

            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(self.url, allow_redirects=True) as resp:
                    if resp.status != 200:
                        logger.debug(f"Form check HTTP {resp.status}")
                        return False

                    html = await resp.text()
                    html_lower = html.lower()
                    final_url = str(resp.url).lower()

                    # Closed: URL contains closedform
                    if "closedform" in final_url:
                        logger.debug("Form closed (URL indicator)")
                        return False

                    # Closed: known closure text markers
                    for marker in CLOSED_MARKERS:
                        if marker in html_lower:
                            logger.debug(f"Form closed (text marker: '{marker}')")
                            return False

                    # Open: active form elements found
                    has_inputs = any(ind in html_lower for ind in OPEN_INDICATORS)
                    if has_inputs:
                        return True

                    logger.debug("Form status undetermined or closed (no active input indicators found)")
                    return False

        except aiohttp.ClientError as e:
            logger.warning(f"Form check network error: {e}")
            return False
        except asyncio.TimeoutError:
            logger.warning("Form check timed out")
            return False
        except Exception:
            logger.exception("Unexpected error during form check")
            return False

    async def _run_autofill(self) -> int:
        """Trigger the full autofill pipeline."""
        # Import here to avoid circular imports
        from src.__main__ import run_autofill
        return await run_autofill(url=self.url, is_test=self.is_test, headless=self.headless)

    async def _notify(self, text: str) -> None:
        """Send a Telegram notification (text-only, no screenshots)."""
        token = Config.TELEGRAM_BOT_TOKEN
        chat_id = Config.TELEGRAM_CHAT_ID

        if not token or not chat_id:
            return

        api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"Telegram watcher notification failed: {body}")
        except Exception:
            logger.exception("Failed to send watcher Telegram notification")
