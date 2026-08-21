import asyncio
import os
import shutil
from pathlib import Path
from typing import Optional, Tuple
from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from src.config import Config, ensure_directories
from src.logger import logger

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--window-size=1920,1080",
    "--lang=en-US,en;q=0.9,ro;q=0.8,ru;q=0.7",
]


def clean_profile_locks(profile_dir: Path) -> None:
    """Removes stale Chromium lock files to prevent startup crashes."""
    lock_names = ["SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"]
    for lock_name in lock_names:
        lock_path = profile_dir / lock_name
        if lock_path.exists():
            try:
                if lock_path.is_dir():
                    shutil.rmtree(lock_path, ignore_errors=True)
                else:
                    lock_path.unlink(missing_ok=True)
                logger.debug(f"Removed stale lock: {lock_path}")
            except Exception as e:
                logger.warning(f"Could not remove lock file {lock_path}: {e}")


class BrowserManager:
    """Manages Playwright lifecycle, persistent context and anti-bot evasions."""

    def __init__(self, headless: Optional[bool] = None, user_data_dir: Optional[Path] = None):
        self.headless = Config.HEADLESS if headless is None else headless
        self.user_data_dir = user_data_dir or Config.CHROME_PROFILE_DIR
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None

    async def __aenter__(self) -> Tuple[BrowserContext, Page]:
        ensure_directories()
        clean_profile_locks(self.user_data_dir)

        self._playwright = await async_playwright().start()

        logger.info(
            f"Launching Chromium (headless={self.headless}, profile_dir={self.user_data_dir})"
        )

        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=self.headless,
            args=CHROMIUM_ARGS,
            user_agent=DEFAULT_USER_AGENT,
            viewport={"width": Config.VIEWPORT_WIDTH, "height": Config.VIEWPORT_HEIGHT},
            locale="en-US",
            timezone_id="Europe/Bucharest",
            permissions=["geolocation"],
            ignore_https_errors=True,
        )

        page = self._context.pages[0] if self._context.pages else await self._context.new_page()

        # Anti-detection stealth script injection
        await page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            window.chrome = {
                runtime: {}
            };
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en', 'ro', 'ru']
            });
            """
        )

        page.set_default_timeout(Config.ACTION_TIMEOUT_MS)
        page.set_default_navigation_timeout(Config.NAVIGATION_TIMEOUT_MS)

        return self._context, page

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._context:
            await self._context.close()
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Browser closed cleanly.")

    @staticmethod
    async def check_google_session(page: Page) -> bool:
        """
        Navigates to myaccount.google.com to verify if user is actively authenticated.
        Returns True if authenticated, False if login is required.
        """
        try:
            logger.info("Checking Google Account authentication status...")
            await page.goto("https://myaccount.google.com/", wait_until="domcontentloaded")
            await asyncio.sleep(2)

            current_url = page.url
            if "accounts.google.com/signin" in current_url or "ServiceLogin" in current_url:
                logger.warning("Google session is expired or not signed in.")
                return False

            # Look for account header or sign in button
            sign_in_buttons = await page.query_selector_all("a[href*='accounts.google.com/signin']")
            if sign_in_buttons:
                for btn in sign_in_buttons:
                    if await btn.is_visible():
                        logger.warning("Sign-in button found on Google Account page.")
                        return False

            logger.info("Google Account is actively authenticated.")
            return True
        except Exception as e:
            logger.error(f"Error checking Google session: {e}")
            return False
