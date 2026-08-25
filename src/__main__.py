import asyncio
import sys
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import List, Optional
import click

from src.analyzer import FormAnalyzer
from src.browser import BrowserManager
from src.config import Config, ensure_directories, load_profile, load_synonyms
from src.filler import FormFiller
from src.logger import logger
from src.matcher import FieldMatcher
from src.models import ExecutionReport, FormStatus
from src.reporter import ExecutionReporter


async def run_autofill(url: str, is_test: bool = False, headless: Optional[bool] = None) -> int:
    """Executes the full automated form filling pipeline."""
    start_time = time.time()
    ensure_directories()

    profile = load_profile()
    synonyms = load_synonyms()
    matcher = FieldMatcher(profile, synonyms)

    browser_mgr = BrowserManager(headless=headless)

    async with browser_mgr as (context, page):
        reporter = ExecutionReporter(page)
        report = ExecutionReport(
            url=url,
            timestamp=datetime.now(UTC).isoformat(),
            status=FormStatus.FAILED,
        )

        try:
            logger.info(f"Navigating to Google Form: {url}")
            await page.goto(url, wait_until="networkidle")

            # Check if form is closed
            is_closed, close_reason = await FormAnalyzer.is_form_closed(page)
            if is_closed:
                logger.error(f"Form is CLOSED: {close_reason}")
                report.status = FormStatus.CLOSED
                report.error_message = f"Form is closed: {close_reason}"
                report.duration_sec = time.time() - start_time
                reporter.save_json_log(report)
                await reporter.send_telegram_report(report)
                return 1

            filler = FormFiller(page, matcher)
            all_filled_records = []
            page_index = 1

            # Multi-page progression loop
            max_pages = Config.MAX_PAGES
            while page_index <= max_pages:
                logger.info(f"Processing form section #{page_index}...")
                matches, unmatched_req = await filler.fill_current_section()

                for m in matches:
                    all_filled_records.append({
                        "label": m.field.label,
                        "type": m.field.field_type.value,
                        "matched_key": m.matched_key,
                        "value": str(m.resolved_value or m.selected_option or ""),
                        "method": m.method.value,
                    })

                if unmatched_req:
                    unmatched_labels = [f.label for f in unmatched_req]
                    err_msg = f"Cannot proceed: {len(unmatched_req)} required field(s) unmatched: {unmatched_labels}"
                    logger.error(err_msg)
                    report.unmatched_required_fields = unmatched_labels
                    report.error_message = err_msg
                    await reporter.capture_milestone(f"section_{page_index:02d}_unmatched_error")
                    break

                # Capture screenshot of filled section
                await reporter.capture_milestone(f"section_{page_index:02d}_filled")

                nav_btn, nav_type = await filler.find_navigation_button()

                if nav_type == "next" and nav_btn:
                    logger.info(f"Navigating to next section from section #{page_index}...")
                    old_fields = [f.label for f in await FormAnalyzer.extract_fields(page)]
                    
                    navigated = False
                    for attempt in range(1, 4):
                        try:
                            await nav_btn.scroll_into_view_if_needed()
                            await nav_btn.click(force=True, no_wait_after=True, timeout=5000)
                        except Exception as e:
                            logger.warning(f"Click on Next button attempt {attempt} failed: {e}. Trying dispatch_event...")
                            try:
                                await nav_btn.dispatch_event("click")
                            except Exception:
                                pass

                        # Polling wait for DOM to transition to next section (up to 3.5s per click attempt)
                        for _ in range(12):
                            await asyncio.sleep(0.3)
                            new_fields = [f.label for f in await FormAnalyzer.extract_fields(page)]
                            if new_fields != old_fields:
                                navigated = True
                                break

                            # Check if validation error is explicitly visible
                            error_alerts = page.locator('[role="alert"]:visible, .v5Duua:visible, .R2oA3c:visible')
                            if await error_alerts.count() > 0:
                                break

                        if navigated:
                            break

                    if not navigated:
                        error_alerts = page.locator('[role="alert"], .v5Duua, .R2oA3c')
                        err_count = await error_alerts.count()
                        err_texts = []
                        for i in range(err_count):
                            elem = error_alerts.nth(i)
                            if await elem.is_visible():
                                txt = (await elem.inner_text()).strip()
                                if txt and txt not in err_texts:
                                    err_texts.append(txt)

                        err_detail = f": {err_texts}" if err_texts else ""
                        err_msg = f"Section #{page_index} failed to advance after clicking Next (validation error{err_detail})"
                        logger.error(err_msg)
                        report.error_message = err_msg
                        await reporter.capture_milestone(f"section_{page_index:02d}_nav_error")
                        break

                    page_index += 1
                    continue
                else:
                    break

            report.total_fields = len(all_filled_records)
            report.filled_fields = all_filled_records

            # If stopped due to errors or unmatched required fields, bail out before submit
            if report.unmatched_required_fields or report.error_message:
                report.status = FormStatus.FAILED
                report.duration_sec = time.time() - start_time
                reporter.save_json_log(report)
                await reporter.send_telegram_report(report)
                return 1

            if is_test:
                logger.info("Test mode enabled: skipping Submit button click.")
                report.status = FormStatus.DRY_RUN
                report.is_submitted = False
                report.duration_sec = time.time() - start_time
                reporter.save_json_log(report)
                await reporter.send_telegram_report(report)
                return 0

            # Submit form
            submit_ok = await filler.click_submit()
            if not submit_ok:
                report.status = FormStatus.FAILED
                report.error_message = "Failed to locate or click Submit button."
                await reporter.capture_milestone("03_submit_failed")
                report.duration_sec = time.time() - start_time
                reporter.save_json_log(report)
                await reporter.send_telegram_report(report)
                return 1

            # Verify submission
            is_success, status_msg = await filler.verify_submission_status()
            await reporter.capture_milestone("03_form_submitted")

            if is_success:
                logger.info(f"Form submission SUCCESS: {status_msg}")
                report.status = FormStatus.SUCCESS
                report.is_submitted = True
            else:
                logger.error(f"Form submission FAILED verification: {status_msg}")
                report.status = FormStatus.FAILED
                report.error_message = status_msg

            report.duration_sec = time.time() - start_time
            reporter.save_json_log(report)
            await reporter.send_telegram_report(report)

            return 0 if is_success else 1

        except Exception as e:
            logger.exception(f"Unhandled exception during autofill execution: {e}")
            await reporter.capture_milestone("error_unhandled")
            report.status = FormStatus.FAILED
            report.error_message = str(e)
            report.duration_sec = time.time() - start_time
            reporter.save_json_log(report)
            await reporter.send_telegram_report(report)
            return 1


async def run_login_flow() -> None:
    """Launches headed browser for one-time manual Google authentication."""
    ensure_directories()
    browser_mgr = BrowserManager(headless=False)

    logger.info("Opening browser for manual Google sign-in...")
    logger.info("Log in to your Google Account. Once done, close the browser window.")

    async with browser_mgr as (context, page):
        await page.goto("https://accounts.google.com/", wait_until="domcontentloaded")
        try:
            while not page.is_closed():
                await asyncio.sleep(1)
        except Exception:
            logger.debug("Browser window closed during login flow.")

    logger.info("Google authentication session saved to persistent profile.")


async def run_session_check() -> int:
    """Verifies whether the persistent Google profile is currently signed in."""
    ensure_directories()
    browser_mgr = BrowserManager(headless=True)

    async with browser_mgr as (context, page):
        is_active = await BrowserManager.check_google_session(page)
        if is_active:
            logger.info("Active Google session confirmed.")
            return 0
        else:
            logger.warning("No active Google session found. Please run with --login.")
            return 1


from src.batch_runner import BatchRunner


@click.command()
@click.option("--url", "-u", type=str, help="Target Google Form URL to fill and submit.")
@click.option("--batch", "-b", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Path to text/CSV file with Google Form URLs for mass testing.")
@click.option("--watch", "-w", type=str, help="Watch a Google Form URL and auto-fill when it opens.")
@click.option("--interval", type=int, default=30, show_default=True, help="Polling interval in seconds for --watch mode.")
@click.option("--max-hours", type=float, default=72, show_default=True, help="Max hours to watch before timeout.")
@click.option("--test", is_flag=True, help="Test mode: fills form, captures screenshots, but does not submit.")
@click.option("--login", is_flag=True, help="One-time manual Google authentication in headed browser.")
@click.option("--check-session", is_flag=True, help="Checks whether persistent Google session is active.")
@click.option("--headed", is_flag=True, help="Run browser in visible (headed) mode for debugging.")
@click.option("--bot", is_flag=True, help="Run interactive Telegram Bot Control Plane.")
def main(
    url: Optional[str],
    batch: Optional[Path],
    watch: Optional[str],
    interval: int,
    max_hours: float,
    test: bool,
    login: bool,
    check_session: bool,
    headed: bool,
    bot: bool,
):
    """SWS Auto-Fill Bot: Automated Google Forms submission tool."""
    if bot:
        from src.bot.bot import run_bot_service
        asyncio.run(run_bot_service())
        return

    if login:
        asyncio.run(run_login_flow())
        return

    if check_session:
        exit_code = asyncio.run(run_session_check())
        sys.exit(exit_code)

    headless_mode = False if headed else None

    if watch:
        from src.watcher import FormWatcher
        watcher = FormWatcher(
            url=watch,
            poll_interval=interval,
            max_hours=max_hours,
            is_test=test,
            headless=headless_mode,
        )
        exit_code = asyncio.run(watcher.watch())
        sys.exit(exit_code)

    if batch:
        items = BatchRunner.load_urls_from_file(batch)
        if not items:
            click.echo(f"Error: No valid Google Form URLs found in {batch}")
            sys.exit(1)

        runner = BatchRunner(is_test=test, headless=headless_mode)
        summary = asyncio.run(runner.run_batch(items))
        exit_code = 0 if summary["failed"] == 0 else 1
        sys.exit(exit_code)

    if not url:
        click.echo("Error: Please provide a Google Form URL using --url, a batch file with --batch, --watch for autopilot, --bot for Telegram control plane, or --login / --check-session")
        sys.exit(1)

    exit_code = asyncio.run(run_autofill(url=url, is_test=test, headless=headless_mode))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
