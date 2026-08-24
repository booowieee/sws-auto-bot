import asyncio
import random
import re
from datetime import datetime
from typing import List, Optional, Tuple
from playwright.async_api import Page, Locator

from src.analyzer import FormAnalyzer
from src.llm import LLMFallbackClient
from src.logger import logger
from src.matcher import FieldMatcher
from src.models import (
    FieldMatch,
    FieldType,
    FormField,
    MatchMethod,
)

SUBMIT_BUTTON_TEXTS = [
    "trimite",
    "submit",
    "отправить",
    "send",
    "înregistrează",
    "inregistreaza",
]

NEXT_BUTTON_TEXTS = [
    "înainte",
    "inainte",
    "următor",
    "urmator",
    "next",
    "далее",
    "следующая",
]

CLOSED_TEXTS = [
    "nu mai acceptă răspunsuri",
    "nu se mai acceptă răspunsuri",
    "formularul nu mai acceptă",
    "no longer accepting responses",
    "is no longer accepting",
    "больше не принимает ответы",
    "форма закрыта",
]

REQUIRED_ERROR_TEXTS = [
    "acesta este un câmp obligatoriu",
    "this is a required question",
    "это обязательный вопрос",
    "обязательный вопрос",
    "este un câmp obligatoriu",
]

SUCCESS_CONFIRMATION_TEXTS = [
    "răspunsul dumneavoastră a fost înregistrat",
    "raspunsul dvs. a fost inregistrat",
    "raspunsul a fost inregistrat",
    "your response has been recorded",
    "ответ записан",
    "formular trimis",
]

VALIDATION_ERROR_TEXTS = [
    "răspuns obligatoriu",
    "raspuns obligatoriu",
    "this is a required question",
    "обязательный вопрос",
    "este un câmp obligatoriu",
]


class FormFiller:
    """Fills Google Forms fields and handles page navigation and submission."""

    def __init__(self, page: Page, matcher: FieldMatcher, llm_client: Optional[LLMFallbackClient] = None):
        self.page = page
        self.matcher = matcher
        self.llm_client = llm_client or LLMFallbackClient()

    async def fill_current_section(self) -> Tuple[List[FieldMatch], List[FormField]]:
        """Extracts visible fields, matches against profile, executes LLM fallback if needed, and fills them."""
        fields = await FormAnalyzer.extract_fields(self.page)
        matches = self.matcher.match_all(fields)

        # Tier 2: LLM Fallback for unmapped fields
        unmapped_fields = [m.field for m in matches if m.method == MatchMethod.UNMATCHED]
        if unmapped_fields and self.llm_client.is_available:
            logger.info(f"Tier 2 LLM Fallback triggered for {len(unmapped_fields)} unmapped field(s)...")
            llm_results = await self.llm_client.match_batch(unmapped_fields, self.matcher.profile)
            if llm_results:
                result_map = {r.index: r for r in llm_results}
                merged_matches: List[FieldMatch] = []
                for m in matches:
                    if m.method == MatchMethod.UNMATCHED and m.field.index in result_map:
                        res = result_map[m.field.index]
                        merged_matches.append(
                            FieldMatch(
                                field=m.field,
                                matched_key=res.matched_key,
                                profile_key=res.matched_key,
                                resolved_value=res.resolved_value,
                                selected_option=res.selected_option,
                                method=MatchMethod.FALLBACK,
                                confidence=res.confidence,
                            )
                        )
                    else:
                        merged_matches.append(m)
                matches = merged_matches

        unmatched_required: List[FormField] = []

        for match in matches:
            if match.method == MatchMethod.UNMATCHED:
                if match.field.required:
                    unmatched_required.append(match.field)
                continue

            await self._fill_field(match)
            # Randomized pause between fields
            await asyncio.sleep(random.uniform(0.25, 0.65))

        return matches, unmatched_required

    async def _fill_field(self, match: FieldMatch) -> None:
        field = match.field
        value = str(match.resolved_value or "")

        logger.info(
            f"Filling field [{field.index}] '{field.label}' (Type: {field.field_type.value}) -> '{value}'"
        )

        try:
            if field.field_type in (FieldType.TEXT, FieldType.TEXTAREA):
                await self._type_text(field, value)
            elif field.field_type == FieldType.RADIO:
                await self._select_radio(field, match.selected_option or value)
            elif field.field_type == FieldType.CHECKBOX:
                await self._select_checkbox(field, match.selected_option or value)
            elif field.field_type == FieldType.DROPDOWN:
                await self._select_dropdown(field, match.selected_option or value)
            elif field.field_type == FieldType.DATE:
                await self._fill_date(field, value)
            else:
                logger.warning(f"Unsupported field type {field.field_type.value} for '{field.label}'")
        except Exception as e:
            logger.error(f"Error filling field '{field.label}': {e}")
            raise

    async def _get_container(self, field: FormField) -> Locator:
        """Finds the question container by label text with fallback to index."""
        if field.label:
            # 1. Try finding container containing the exact label text
            by_text = self.page.locator('[role="listitem"]').filter(has_text=field.label)
            if await by_text.count() > 0:
                return by_text.first

            # 2. Try with main part before parenthesis or newline
            clean_l = field.label.split("(")[0].split("\n")[0].strip()
            if clean_l and len(clean_l) >= 3:
                by_prefix = self.page.locator('[role="listitem"]').filter(has_text=clean_l)
                if await by_prefix.count() > 0:
                    return by_prefix.first

        # 3. Fallback to question index
        containers = self.page.locator('[role="listitem"]')
        count = await containers.count()
        if 0 < field.index <= count:
            return containers.nth(field.index - 1)

        return containers.first if count > 0 else self.page.locator('[role="listitem"]')

    async def _type_text(self, field: FormField, text: str) -> None:
        locator = None
        if field.entry_id:
            locator = self.page.locator(f'input[name="{field.entry_id}"], textarea[name="{field.entry_id}"]')

        if not locator or await locator.count() == 0:
            container = await self._get_container(field)
            if await container.count() > 0:
                locator = container.locator('input[type="text"], input:not([type]), textarea')

        if not locator or await locator.count() == 0:
            locator = self.page.locator('input[type="text"], input:not([type]), textarea')

        if await locator.count() > 0:
            try:
                await locator.first.click(force=True, timeout=2000)
            except Exception:
                await locator.first.focus()

            await locator.first.fill("")  # Clear existing content

            # Human-like typing with random jitter
            for char in text:
                await locator.first.press_sequentially(char, delay=random.uniform(15, 55))
        else:
            logger.warning(f"Could not locate text input for '{field.label}'")

    async def _select_radio(self, field: FormField, option_text: str) -> None:
        container = await self._get_container(field)

        # Try finding radio by exact option label
        radio = container.locator(f'[role="radio"][data-value="{option_text}"], [role="radio"][aria-label="{option_text}"]')
        if await radio.count() > 0:
            await radio.first.click(force=True, timeout=3000)
            return

        # Try matching radio containing the option text
        radio_text = container.locator('[role="radio"]').filter(has_text=option_text)
        if await radio_text.count() > 0:
            await radio_text.first.click(force=True, timeout=3000)
            return

        # Fallback: click text label directly inside container
        opt_label = container.get_by_text(option_text, exact=False)
        if await opt_label.count() > 0:
            await opt_label.first.click(force=True, timeout=3000)
            return

        logger.warning(f"Could not locate radio option '{option_text}' in field '{field.label}'")

    async def _select_checkbox(self, field: FormField, option_text: str) -> None:
        container = await self._get_container(field)

        cb = container.locator(f'[role="checkbox"][data-value="{option_text}"], [role="checkbox"][aria-label="{option_text}"]')
        if await cb.count() > 0:
            await cb.first.click(force=True, timeout=3000)
            return

        cb_text = container.locator('[role="checkbox"]').filter(has_text=option_text)
        if await cb_text.count() > 0:
            await cb_text.first.click(force=True, timeout=3000)
            return

        opt_label = container.get_by_text(option_text, exact=False)
        if await opt_label.count() > 0:
            await opt_label.first.click(force=True, timeout=3000)
            return

        logger.warning(f"Could not locate checkbox option '{option_text}' in field '{field.label}'")

    async def _select_dropdown(self, field: FormField, option_text: str) -> None:
        container = await self._get_container(field)

        dropdown = container.locator('[role="listbox"]')
        if await dropdown.count() > 0:
            await dropdown.first.click(force=True, timeout=3000)
            await asyncio.sleep(0.4)

            # Look for options in popup
            options = self.page.locator('[role="option"]:visible')
            count = await options.count()
            if count == 0:
                options = self.page.locator('[role="option"]')
                count = await options.count()

            # 1. Exact match pass (highest precision)
            for i in range(count):
                opt = options.nth(i)
                txt = (await opt.inner_text()).lower().strip()
                if txt == target_clean:
                    try:
                        await opt.click(force=True, timeout=3000)
                        return
                    except Exception:
                        pass

            # 2. Semantic and keyword pass
            best_opt = None
            for i in range(count):
                opt = options.nth(i)
                txt = (await opt.inner_text()).lower().strip()
                if txt in ("alege", "choose", "выбрать", "--", "", "select"):
                    continue

                # Nationality matches
                if ("moldov" in target_clean or "chisinau" in target_clean) and "moldov" in txt:
                    best_opt = opt
                    break
                if "roman" in target_clean and "roman" in txt:
                    best_opt = opt
                    break
                if "ucrain" in target_clean and "ucrain" in txt:
                    best_opt = opt
                    break

                # Size matches (L, XL, XXL, M, S, 42, 43, 44)
                if len(target_clean) <= 4:
                    if txt.startswith(f"{target_clean} ") or txt.startswith(f"{target_clean}-") or f"({target_clean})" in txt or f" {target_clean} " in f" {txt} ":
                        best_opt = opt
                        break

                # English level matches
                if any(w in target_clean for w in ("incepator", "basic", "beginner", "a1", "a2", "elementar")):
                    if any(w in txt for w in ("incepator", "începător", "basic", "beginner", "a1", "a2", "elementar", "начальн", "базов")):
                        best_opt = opt
                        break
                elif any(w in target_clean for w in ("mediu", "intermediate", "b1", "b2", "conversational")):
                    if any(w in txt for w in ("mediu", "intermediate", "b1", "b2", "conversational", "средн")):
                        best_opt = opt
                        break
                elif any(w in target_clean for w in ("avansat", "advanced", "c1", "c2", "fluent")):
                    if any(w in txt for w in ("avansat", "advanced", "c1", "c2", "fluent", "свободн", "продвинут")):
                        best_opt = opt
                        break

                # General substring match (longer strings only)
                if len(target_clean) > 3 and (target_clean in txt or txt in target_clean):
                    best_opt = opt
                    break

            if best_opt:
                try:
                    await best_opt.click(force=True, timeout=3000)
                    return
                except Exception:
                    pass

            # 3. Fallback for required dropdown: pick first non-placeholder option
            if field.required and count > 1:
                logger.warning(f"Selecting first valid option for required dropdown '{field.label}'")
                try:
                    await options.nth(1).click(force=True, timeout=3000)
                    return
                except Exception:
                    pass

        logger.warning(f"Could not select dropdown option '{option_text}' in field '{field.label}'")

    async def _fill_date(self, field: FormField, date_val: str) -> None:
        container = await self._get_container(field)

        date_input = container.locator('input[type="date"], input[type="text"], input:not([type])')
        if await date_input.count() > 0:
            first_input = date_input.first
            input_type = await first_input.get_attribute("type")
            if input_type == "date":
                iso_val = self._convert_date_to_iso(date_val)
                try:
                    await first_input.fill(iso_val)
                    return
                except Exception:
                    pass
            try:
                await first_input.click(force=True, timeout=2000)
            except Exception:
                await first_input.focus()
            await first_input.fill(date_val)
        else:
            await self._type_text(field, date_val)

    @staticmethod
    def _convert_date_to_iso(val: str) -> str:
        val_clean = val.strip()
        for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(val_clean, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return val_clean

    async def find_navigation_button(self) -> Tuple[Optional[Locator], str]:
        """Detects whether current page has a 'Next' or 'Submit' button."""
        buttons = self.page.locator('[role="button"]')
        button_count = await buttons.count()

        submit_btn = None
        next_btn = None

        for i in range(button_count):
            btn = buttons.nth(i)
            text = (await btn.inner_text()).lower().strip()

            for s_kw in SUBMIT_BUTTON_TEXTS:
                if s_kw in text:
                    submit_btn = btn
                    break

            for n_kw in NEXT_BUTTON_TEXTS:
                if n_kw in text:
                    next_btn = btn
                    break

        if submit_btn:
            return submit_btn, "submit"
        if next_btn:
            return next_btn, "next"

        return None, "none"

    async def click_submit(self) -> bool:
        """Finds and clicks the submission button."""
        btn, btn_type = await self.find_navigation_button()
        if btn and btn_type == "submit":
            logger.info("Clicking Submit button...")
            await btn.click()
            return True

        # Fallback query for submit
        for s_kw in SUBMIT_BUTTON_TEXTS:
            b = self.page.get_by_role("button", name=s_kw, exact=False)
            if await b.count() > 0:
                logger.info(f"Clicking Submit button with text '{s_kw}'...")
                await b.first.click()
                return True

        logger.error("Submit button not found on the page.")
        return False

    async def verify_submission_status(self) -> Tuple[bool, str]:
        """
        Verifies whether form was successfully recorded or validation errors occurred.
        Returns (is_success, detail_message).
        """
        await asyncio.sleep(2.5)

        # Check for validation errors
        for err_kw in VALIDATION_ERROR_TEXTS:
            err_elem = self.page.get_by_text(err_kw, exact=False)
            if await err_elem.count() > 0:
                for i in range(await err_elem.count()):
                    if await err_elem.nth(i).is_visible():
                        return False, f"Validation error visible on page: '{err_kw}'"

        # Check URL change
        current_url = self.page.url.lower()
        if "formresponse" in current_url:
            return True, f"Redirected to formResponse: {current_url}"

        # Check confirmation message
        body_text = (await self.page.inner_text("body")).lower()
        for succ_kw in SUCCESS_CONFIRMATION_TEXTS:
            if succ_kw in body_text:
                return True, f"Confirmation text detected: '{succ_kw}'"

        return False, "Could not confirm submission status (unknown state)."
