import asyncio
import csv
import json
import re
import sys
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.analyzer import FormAnalyzer
from src.browser import BrowserManager
from src.config import Config, ensure_directories, load_profile, load_synonyms
from src.filler import FormFiller
from src.logger import logger
from src.matcher import FieldMatcher
from src.models import ExecutionReport, FormStatus
from src.reporter import ExecutionReporter


class BatchRunner:
    """Runs tests across multiple Google Forms and generates QA benchmark reports."""

    def __init__(self, is_test: bool = True, headless: Optional[bool] = None, delay_between_forms: Optional[float] = None):
        self.is_test = is_test
        self.headless = Config.HEADLESS if headless is None else headless
        self.delay_between_forms = delay_between_forms if delay_between_forms is not None else Config.DELAY_BETWEEN_FORMS

    @staticmethod
    def load_urls_from_file(file_path: Path) -> List[Tuple[str, str]]:
        """
        Parses form URLs from text, CSV or TSV.
        Returns a list of tuples: (form_title, form_url).
        """
        if not file_path.exists():
            raise FileNotFoundError(f"URLs file not found: {file_path}")

        results: List[Tuple[str, str]] = []
        content = file_path.read_text(encoding="utf-8").strip()

        # Handle CSV / TSV format
        if file_path.suffix in (".csv", ".tsv") or "\t" in content or "," in content:
            delimiter = "\t" if "\t" in content else ","
            reader = csv.reader(content.splitlines(), delimiter=delimiter)
            for row in reader:
                if not row:
                    continue
                # Search for Google Forms URL in row items
                url = next((col.strip() for col in row if "docs.google.com/forms" in col or "forms.gle" in col), None)
                if url:
                    # Find a title if present
                    title = row[1].strip() if len(row) > 1 and row[1].strip() != url else (row[0].strip() if row[0].strip() != url else "Google Form")
                    results.append((title, url))
            if results:
                return results

        # Handle Plain text lines format
        lines = content.splitlines()
        current_title = "Google Form"

        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue

            # Check if line contains a URL
            url_match = re.search(r"https?://(?:docs\.google\.com/forms/d/e/[^\s/]+/viewform|forms\.gle/[^\s]+)", line_str)
            if url_match:
                url = url_match.group(0)
                # If the line had a prefix title (e.g. "QA-01: https://...")
                prefix = line_str[:url_match.start()].strip().rstrip(":- \t")
                title = prefix if prefix else current_title
                results.append((title, url))
                current_title = "Google Form"
            else:
                # Might be a title line above the URL
                current_title = line_str.rstrip(":")

        return results

    async def run_single_form(
        self,
        url: str,
        title: str,
        matcher: FieldMatcher,
        browser_mgr: BrowserManager,
    ) -> ExecutionReport:
        """Executes automated filling for a single form."""
        start_time = time.time()
        report = ExecutionReport(
            url=url,
            timestamp=datetime.now(UTC).isoformat(),
            status=FormStatus.FAILED,
        )

        async with browser_mgr as (context, page):
            reporter = ExecutionReporter(page)
            try:
                await page.goto(url, wait_until="networkidle")
                await reporter.capture_milestone("01_loaded")

                is_closed, close_reason = await FormAnalyzer.is_form_closed(page)
                if is_closed:
                    report.status = FormStatus.CLOSED
                    report.error_message = close_reason
                    report.duration_sec = time.time() - start_time
                    reporter.save_json_log(report)
                    return report

                filler = FormFiller(page, matcher)
                all_filled_records = []
                page_index = 1
                max_pages = Config.MAX_PAGES

                while page_index <= max_pages:
                    matches, unmatched_req = await filler.fill_current_section()
                    for m in matches:
                        all_filled_records.append({
                            "label": m.field.label,
                            "type": m.field.field_type.value,
                            "matched_key": m.matched_key,
                            "value": str(m.resolved_value or m.selected_option or ""),
                            "method": m.method.value,
                            "required": m.field.required,
                        })

                    if unmatched_req:
                        unmatched_labels = [f.label for f in unmatched_req]
                        err_msg = f"{len(unmatched_req)} required field(s) unmatched: {unmatched_labels}"
                        report.unmatched_required_fields = unmatched_labels
                        report.error_message = err_msg
                        await reporter.capture_milestone(f"section_{page_index}_unmatched")
                        break

                    nav_btn, nav_type = await filler.find_navigation_button()
                    if nav_type == "next" and nav_btn:
                        old_fields = [f.label for f in await FormAnalyzer.extract_fields(page)]
                        await nav_btn.click()
                        await asyncio.sleep(1.2)
                        await page.wait_for_load_state("networkidle")

                        new_fields = [f.label for f in await FormAnalyzer.extract_fields(page)]
                        if old_fields == new_fields:
                            err_msg = f"Section #{page_index} failed to advance after clicking Next (validation or required field error)"
                            logger.error(err_msg)
                            report.error_message = err_msg
                            report.status = FormStatus.FAILED
                            break

                        page_index += 1
                        continue
                    else:
                        break

                report.total_fields = len(all_filled_records)
                report.filled_fields = all_filled_records

                if report.unmatched_required_fields:
                    report.status = FormStatus.FAILED
                    report.duration_sec = time.time() - start_time
                    reporter.save_json_log(report)
                    return report

                await reporter.capture_milestone("02_filled")

                if self.is_test:
                    report.status = FormStatus.DRY_RUN
                    report.is_submitted = False
                    report.duration_sec = time.time() - start_time
                    reporter.save_json_log(report)
                    return report

                submit_ok = await filler.click_submit()
                if not submit_ok:
                    report.status = FormStatus.FAILED
                    report.error_message = "Submit button not found."
                    report.duration_sec = time.time() - start_time
                    reporter.save_json_log(report)
                    return report

                is_success, status_msg = await filler.verify_submission_status()
                await reporter.capture_milestone("03_submitted")

                report.status = FormStatus.SUCCESS if is_success else FormStatus.FAILED
                report.is_submitted = is_success
                report.error_message = None if is_success else status_msg
                report.duration_sec = time.time() - start_time
                reporter.save_json_log(report)
                return report

            except Exception as e:
                logger.error(f"Error processing form '{title}' ({url}): {e}")
                report.status = FormStatus.FAILED
                report.error_message = str(e)
                report.duration_sec = time.time() - start_time
                reporter.save_json_log(report)
                return report

    async def run_batch(self, items: List[Tuple[str, str]]) -> Dict[str, Any]:
        """Executes full test suite sequentially against all provided forms."""
        ensure_directories()
        profile = load_profile()
        synonyms = load_synonyms()
        matcher = FieldMatcher(profile, synonyms)

        total_forms = len(items)
        logger.info(f"Starting QA Batch Benchmark on {total_forms} forms (Dry-Run: {self.is_test})...")

        results: List[Dict[str, Any]] = []
        success_count = 0
        failed_count = 0
        closed_count = 0
        total_fields_matched = 0
        total_fields_found = 0

        print("\n" + "=" * 80)
        print(f" SWS AUTO-BOT QA BENCHMARK -- TESTING {total_forms} FORMS")
        print("=" * 80 + "\n")

        for idx, (title, url) in enumerate(items, 1):
            print(f"[{idx:02d}/{total_forms:02d}] Testing: {title} ...", end=" ", flush=True)

            browser_mgr = BrowserManager(headless=self.headless)
            report = await self.run_single_form(url=url, title=title, matcher=matcher, browser_mgr=browser_mgr)

            matched_in_form = sum(1 for f in report.filled_fields if f.get("matched_key"))
            total_in_form = report.total_fields
            total_fields_matched += matched_in_form
            total_fields_found += total_in_form

            status_str = report.status.value.upper()
            duration_str = f"{report.duration_sec:.1f}s"

            if report.status in (FormStatus.SUCCESS, FormStatus.DRY_RUN):
                success_count += 1
                print(f"PASSED ({status_str}) in {duration_str} [Fields: {matched_in_form}/{total_in_form}]")
            elif report.status == FormStatus.CLOSED:
                closed_count += 1
                print(f"CLOSED in {duration_str}")
            else:
                failed_count += 1
                print(f"FAILED ({report.error_message}) in {duration_str}")

            results.append({
                "index": idx,
                "title": title,
                "url": url,
                "status": report.status.value,
                "duration_sec": round(report.duration_sec, 2),
                "total_fields": total_in_form,
                "matched_fields": matched_in_form,
                "unmatched_required": report.unmatched_required_fields,
                "error": report.error_message,
            })

            if idx < total_forms:
                await asyncio.sleep(self.delay_between_forms)

        accuracy_pct = (total_fields_matched / total_fields_found * 100.0) if total_fields_found > 0 else 0.0
        success_rate_pct = (success_count / total_forms * 100.0) if total_forms > 0 else 0.0

        summary = {
            "timestamp": datetime.now(UTC).isoformat(),
            "total_tested": total_forms,
            "passed": success_count,
            "failed": failed_count,
            "closed": closed_count,
            "success_rate_pct": round(success_rate_pct, 1),
            "field_matching_accuracy_pct": round(accuracy_pct, 1),
            "results": results,
        }

        self._generate_reports(summary)
        self._print_summary_table(summary)

        return summary

    def _generate_reports(self, summary: Dict[str, Any]) -> None:
        """Saves detailed Markdown and JSON summary reports."""
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        json_path = Config.LOGS_DIR / f"benchmark_{timestamp}.json"
        md_path = Config.LOGS_DIR / f"benchmark_{timestamp}.md"

        # Save JSON
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        # Save Markdown Report
        md_lines = [
            f"# SWS Auto-Bot -- QA Benchmark Report",
            f"",
            f"- **Date / Time:** {summary['timestamp']}",
            f"- **Total Forms Tested:** {summary['total_tested']}",
            f"- **Passed (OK/Dry-Run):** {summary['passed']} ({summary['success_rate_pct']}%)",
            f"- **Failed:** {summary['failed']}",
            f"- **Field Matching Accuracy:** {summary['field_matching_accuracy_pct']}%",
            f"",
            f"---",
            f"",
            f"## Detailed Results Table",
            f"",
            f"| # | Form Title | Status | Duration | Fields Matched | Error / Unmatched Details |",
            f"|---|------------|--------|----------|----------------|---------------------------|",
        ]

        for r in summary["results"]:
            status_badge = "OK" if r["status"] in ("success", "dry_run") else r["status"].upper()
            err_text = r["error"] or (", ".join(r["unmatched_required"]) if r["unmatched_required"] else "-")
            md_lines.append(
                f"| {r['index']} | [{r['title']}]({r['url']}) | `{status_badge}` | {r['duration_sec']}s | {r['matched_fields']}/{r['total_fields']} | {err_text} |"
            )

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines) + "\n")

        logger.info(f"Saved benchmark report: {md_path}")

    def _print_summary_table(self, summary: Dict[str, Any]) -> None:
        print("\n" + "=" * 80)
        print(" BENCHMARK EXECUTION SUMMARY")
        print("=" * 80)
        print(f" Total Forms Tested:       {summary['total_tested']}")
        print(f" Passed (Success/Dry-Run): {summary['passed']} ({summary['success_rate_pct']}%)")
        print(f" Failed:                   {summary['failed']}")
        print(f" Field Matching Accuracy:  {summary['field_matching_accuracy_pct']}%")
        print("=" * 80 + "\n")
