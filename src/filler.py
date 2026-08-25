import asyncio
import random
import re
from datetime import datetime
from typing import List, Optional, Tuple
from playwright.async_api import Page, Locator

from rapidfuzz import fuzz

from src.analyzer import FormAnalyzer
from src.llm_router import LLMRouter
from src.logger import logger
from src.matcher import FieldMatcher, SEMANTIC_OPTION_MAP
from src.models import (
    FieldMatch,
    FieldType,
    FormField,
    MatchMethod,
)
from src.text_utils import normalize_text, strip_diacritics

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
]

VALIDATION_ERROR_TEXTS = [
    "acesta este un câmp obligatoriu",
    "this is a required question",
    "это обязательный вопрос",
    "este un câmp obligatoriu",
]


class FormFiller:
    """Fills Google Forms fields and handles page navigation and submission."""

    def __init__(self, page: Page, matcher: FieldMatcher, llm_router: Optional[LLMRouter] = None):
        self.page = page
        self.matcher = matcher
        self.llm_router = llm_router or LLMRouter()

    async def fill_current_section(self) -> Tuple[List[FieldMatch], List[FormField]]:
        """Extracts visible fields, matches against profile, executes LLM fallback if needed, and fills them."""
        fields = await FormAnalyzer.extract_fields(self.page)
        matches = self.matcher.match_all(fields)

        # Tier 2: LLM Fallback with Semantic Caching for unmapped fields
        unmapped_fields = [m.field for m in matches if m.method == MatchMethod.UNMATCHED]
        if unmapped_fields and self.llm_router.is_available:
            logger.info(f"Tier 2 LLM Fallback triggered for {len(unmapped_fields)} unmapped field(s)...")
            llm_results = await self.llm_router.resolve_batch(unmapped_fields, self.matcher.profile)
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
            if field.required:
                logger.error(f"Error filling required field '{field.label}': {e}")
                raise
            else:
                logger.warning(f"Error filling optional field '{field.label}': {e}. Continuing.")

    async def _get_container(self, field: FormField) -> Locator:
        """Finds the question container by matching heading label, with 1-to-1 index fallback."""
        containers = self.page.locator('[role="listitem"]')
        count = await containers.count()
        if count == 0:
            containers = self.page.locator('[data-params]')
            count = await containers.count()

        if count == 0:
            return self.page.locator('body')

        if field.label:
            clean_target = re.sub(r"[\*\n\r\t]+", " ", field.label).strip().lower()
            clean_target_nd = strip_diacritics(clean_target)

            # Pass 1: Exact match on heading label (avoids "пол" matching "полное имя")
            for i in range(count):
                c = containers.nth(i)
                heading = c.locator('[role="heading"], .M7eMe').first
                if await heading.count() > 0:
                    htext = (await heading.inner_text()).strip().lower()
                    htext_clean = re.sub(r"[\*\n\r\t]+", " ", htext).strip()
                    htext_clean_nd = strip_diacritics(htext_clean)
                    if clean_target == htext_clean or clean_target_nd == htext_clean_nd:
                        return c

            # Pass 2: Word boundary regex match
            for i in range(count):
                c = containers.nth(i)
                heading = c.locator('[role="heading"], .M7eMe').first
                if await heading.count() > 0:
                    htext = (await heading.inner_text()).strip().lower()
                    htext_clean = re.sub(r"[\*\n\r\t]+", " ", htext).strip()
                    htext_clean_nd = strip_diacritics(htext_clean)
                    try:
                        if re.search(r'\b' + re.escape(clean_target_nd) + r'\b', htext_clean_nd):
                            return c
                    except Exception:
                        pass

            # Pass 3: Fuzzy token set match (handles variations like (caravane) vs tip caravana)
            best_c = None
            best_score = 0.0
            for i in range(count):
                c = containers.nth(i)
                heading = c.locator('[role="heading"], .M7eMe').first
                if await heading.count() > 0:
                    htext = (await heading.inner_text()).strip().lower()
                    htext_clean = re.sub(r"[\*\n\r\t]+", " ", htext).strip()
                    htext_clean_nd = strip_diacritics(htext_clean)
                    score = fuzz.token_set_ratio(clean_target_nd, htext_clean_nd)
                    if score > best_score and score >= 75.0:
                        best_score = score
                        best_c = c
            if best_c is not None:
                return best_c

        # Pass 4: Fallback to exact 1-to-1 index (aligned with FormAnalyzer.extract_fields)
        if 0 < field.index <= count:
            return containers.nth(field.index - 1)

        return containers.first

    async def _type_text(self, field: FormField, text: str) -> None:
        if text is None:
            text = ""

        container = await self._get_container(field)
        locator = None

        if field.entry_id:
            locator = self.page.locator(f'input[name="{field.entry_id}"], textarea[name="{field.entry_id}"]')

        if not locator or await locator.count() == 0:
            locator = container.locator('input[type="text"], input:not([type]), textarea')

        if locator and await locator.count() > 0:
            target_input = locator.first
            await target_input.scroll_into_view_if_needed()

            # Check if field is disabled or read-only (conditional questions in Google Forms)
            try:
                is_dis = await target_input.is_disabled()
                aria_dis = (await target_input.get_attribute("aria-disabled") or "").lower() == "true"
                attr_dis = await target_input.get_attribute("disabled") is not None
                if is_dis or aria_dis or attr_dis:
                    logger.info(f"Field [{field.index}] '{field.label}' is disabled/conditional. Skipping.")
                    return
            except Exception:
                pass

            try:
                await target_input.click(force=True, timeout=1500)
            except Exception:
                try:
                    await target_input.focus()
                except Exception:
                    pass

            try:
                await target_input.fill(text, timeout=3000)
            except Exception as e:
                logger.warning(f"Could not fill text into field [{field.index}] '{field.label}': {e}. Skipping.")
        else:
            logger.warning(f"Could not locate text input for field [{field.index}] '{field.label}'")

    async def _select_radio(self, field: FormField, option_text: str) -> None:
        if not option_text:
            return

        container = await self._get_container(field)
        target_clean = option_text.strip().lower()
        target_nd = strip_diacritics(target_clean)

        # Collect candidate synonym phrases for the option (RO / EN / RU)
        candidate_syns = [target_clean, target_nd]
        for syn_group in SEMANTIC_OPTION_MAP.values():
            syn_group_clean = [s.lower().strip() for s in syn_group]
            if target_clean in syn_group_clean or target_nd in syn_group_clean:
                candidate_syns.extend(syn_group_clean)
                candidate_syns.extend([strip_diacritics(s) for s in syn_group_clean])
        candidate_syns = list(set(candidate_syns))

        radios = container.locator('[role="radio"]')
        count = await radios.count()

        # If primary container has no radios, try index-based container fallback
        if count == 0 and 0 < field.index:
            containers = self.page.locator('[role="listitem"]')
            if await containers.count() >= field.index:
                container = containers.nth(field.index - 1)
                radios = container.locator('[role="radio"]')
                count = await radios.count()

        for i in range(count):
            radio = radios.nth(i)
            data_val = (await radio.get_attribute("data-value") or "").strip().lower()
            aria_label = (await radio.get_attribute("aria-label") or "").strip().lower()
            inner_txt = (await radio.inner_text()).strip().lower()

            parent_wrapper = radio.locator("xpath=ancestor::*[contains(@class, 'docssharedWizToggleLabeledContainer') or contains(@class, 'geS5nc') or self::label]").first
            parent_txt = (await parent_wrapper.inner_text()).strip().lower() if await parent_wrapper.count() > 0 else ""

            data_val_nd = strip_diacritics(data_val)
            aria_label_nd = strip_diacritics(aria_label)
            inner_txt_nd = strip_diacritics(inner_txt)
            parent_txt_nd = strip_diacritics(parent_txt)

            # Match against target and all semantic candidates
            is_matched = False
            for cand in candidate_syns:
                cand_nd = strip_diacritics(cand)
                if (
                    cand in (data_val, aria_label, inner_txt)
                    or cand_nd in (data_val_nd, aria_label_nd, inner_txt_nd)
                    or cand in parent_txt
                    or cand_nd in parent_txt_nd
                    or (len(cand) >= 3 and (cand in data_val or data_val in cand or cand_nd in data_val_nd or data_val_nd in cand_nd))
                ):
                    is_matched = True
                    break

            if is_matched:
                await radio.scroll_into_view_if_needed()
                try:
                    await radio.click(force=True, timeout=2000)
                except Exception:
                    pass

                checked = await radio.get_attribute("aria-checked")
                if checked != "true" and await parent_wrapper.count() > 0:
                    try:
                        await parent_wrapper.click(force=True, timeout=2000)
                    except Exception:
                        pass
                return

        # 2. Fallback: text search inside container
        for cand in candidate_syns:
            opt_label = container.get_by_text(cand, exact=False).first
            if await opt_label.count() > 0:
                await opt_label.scroll_into_view_if_needed()
                try:
                    await opt_label.click(force=True, timeout=2000)
                    return
                except Exception:
                    pass

        logger.warning(f"Could not locate radio option '{option_text}' in field '{field.label}'")

    async def _select_checkbox(self, field: FormField, option_text: str) -> None:
        if not option_text:
            return

        container = await self._get_container(field)
        target_clean = option_text.strip().lower()
        target_nd = strip_diacritics(target_clean)

        checkboxes = container.locator('[role="checkbox"]')
        count = await checkboxes.count()

        # If primary container has no checkboxes, try index-based container fallback
        if count == 0 and 0 < field.index:
            containers = self.page.locator('[role="listitem"]')
            if await containers.count() >= field.index:
                container = containers.nth(field.index - 1)
                checkboxes = container.locator('[role="checkbox"]')
                count = await checkboxes.count()

        # If target is negative (e.g. "Nu", "No", "None", "Nu detin")
        is_negative = target_clean in ("nu", "no", "нет", "none", "niciunul", "niciuna", "nu detin", "nu am")
        if is_negative:
            for i in range(count):
                cb = checkboxes.nth(i)
                txt = ((await cb.inner_text()) or (await cb.get_attribute("aria-label")) or "").lower()
                txt_nd = strip_diacritics(txt)
                if any(neg in txt_nd for neg in ("none", "niciun", "niciuna", "nu detin", "nu am", "fara", "нет", "отсутств")):
                    await cb.scroll_into_view_if_needed()
                    try:
                        await cb.click(force=True, timeout=2000)
                    except Exception:
                        pass
                    return
            # If no explicit "None" checkbox exists and field is optional, skip gracefully
            if not field.required:
                return

        # Split comma-separated items if multiple selections
        items_to_match = [s.strip() for s in target_clean.split(",") if s.strip()] if "," in target_clean else [target_clean]

        for item in items_to_match:
            item_nd = strip_diacritics(item)
            matched = False
            for i in range(count):
                cb = checkboxes.nth(i)
                data_val = (await cb.get_attribute("data-value") or "").strip().lower()
                aria_label = (await cb.get_attribute("aria-label") or "").strip().lower()
                inner_txt = (await cb.inner_text()).strip().lower()

                parent_wrapper = cb.locator("xpath=ancestor::*[contains(@class, 'docssharedWizToggleLabeledContainer') or contains(@class, 'geS5nc') or self::label]").first
                parent_txt = (await parent_wrapper.inner_text()).strip().lower() if await parent_wrapper.count() > 0 else ""

                data_val_nd = strip_diacritics(data_val)
                aria_label_nd = strip_diacritics(aria_label)
                inner_txt_nd = strip_diacritics(inner_txt)
                parent_txt_nd = strip_diacritics(parent_txt)

                if (
                    item in (data_val, aria_label, inner_txt)
                    or item_nd in (data_val_nd, aria_label_nd, inner_txt_nd)
                    or item in parent_txt
                    or item_nd in parent_txt_nd
                    or (len(item) >= 3 and (item in data_val or data_val in item or item_nd in data_val_nd or data_val_nd in item_nd or item_nd in parent_txt_nd))
                ):
                    await cb.scroll_into_view_if_needed()
                    try:
                        await cb.click(force=True, timeout=2000)
                    except Exception:
                        pass

                    checked = await cb.get_attribute("aria-checked")
                    if checked != "true" and await parent_wrapper.count() > 0:
                        try:
                            await parent_wrapper.click(force=True, timeout=2000)
                        except Exception:
                            pass
                    matched = True
                    break

            if not matched:
                opt_label = container.get_by_text(item, exact=False).first
                if await opt_label.count() > 0:
                    await opt_label.scroll_into_view_if_needed()
                    try:
                        await opt_label.click(force=True, timeout=2000)
                    except Exception:
                        pass

    async def _select_dropdown(self, field: FormField, option_text: str) -> None:
        if not option_text:
            return

        container = await self._get_container(field)
        target_clean = option_text.lower().strip()
        target_nd = strip_diacritics(target_clean)

        dropdown = container.locator('[role="listbox"], .quantumWizMenuPaperselectEl')
        if await dropdown.count() > 0:
            await dropdown.first.scroll_into_view_if_needed()
            await dropdown.first.click(force=True, timeout=3000)
            await asyncio.sleep(0.4)

            # Look for options in popup
            options = self.page.locator('[role="option"]:visible')
            count = await options.count()
            if count == 0:
                options = self.page.locator('[role="option"]')
                count = await options.count()

            # 1. Exact match pass (with and without diacritics)
            for i in range(count):
                opt = options.nth(i)
                txt = (await opt.inner_text()).lower().strip()
                txt_nd = strip_diacritics(txt)
                if txt == target_clean or txt_nd == target_nd:
                    try:
                        await opt.scroll_into_view_if_needed()
                        await opt.click(force=True, timeout=3000)
                        return
                    except Exception:
                        pass

            # 2. Semantic and keyword pass
            best_opt = None
            for i in range(count):
                opt = options.nth(i)
                txt = (await opt.inner_text()).lower().strip()
                txt_nd = strip_diacritics(txt)
                if txt in ("alege", "choose", "выбрать", "--", "", "select"):
                    continue

                # Nationality matches
                if ("moldov" in target_nd or "chisinau" in target_nd) and "moldov" in txt_nd:
                    best_opt = opt
                    break
                if "roman" in target_nd and "roman" in txt_nd:
                    best_opt = opt
                    break
                if "ucrain" in target_nd and "ucrain" in txt_nd:
                    best_opt = opt
                    break

                # Size matches (L, XL, XXL, M, S, 42, 43, 44)
                if len(target_clean) <= 4:
                    if txt.startswith(f"{target_clean} ") or txt.startswith(f"{target_clean}-") or f"({target_clean})" in txt or f" {target_clean} " in f" {txt} ":
                        best_opt = opt
                        break

                # English level matches
                if any(w in target_nd for w in ("incepator", "basic", "beginner", "a1", "a2", "elementar")):
                    if any(w in txt_nd for w in ("incepator", "basic", "beginner", "a1", "a2", "elementar", "начальн", "базов")):
                        best_opt = opt
                        break
                elif any(w in target_nd for w in ("mediu", "intermediate", "b1", "b2", "conversational")):
                    if any(w in txt_nd for w in ("mediu", "intermediate", "b1", "b2", "conversational", "средн")):
                        best_opt = opt
                        break
                elif any(w in target_nd for w in ("avansat", "advanced", "c1", "c2", "fluent")):
                    if any(w in txt_nd for w in ("avansat", "advanced", "c1", "c2", "fluent", "свободн", "продвинут")):
                        best_opt = opt
                        break

                # Driving license category matches
                if any(w in target_nd for w in ("categoria b", "category b", "cat b", "b", "car", "autoturism")):
                    if any(w in txt_nd for w in ("categoria b", "category b", "cat b", "(b)", "car", "autoturism", "легков")):
                        best_opt = opt
                        break

                # Emergency contact relationship matches
                if any(w in target_nd for w in ("mother", "mama", "мать", "мама")):
                    if any(w in txt_nd for w in ("mother", "mama", "мать", "мама", "parinte", "parent", "родител")):
                        best_opt = opt
                        break
                elif any(w in target_nd for w in ("father", "tata", "отец", "папа")):
                    if any(w in txt_nd for w in ("father", "tata", "отец", "папа", "parinte", "parent", "родител")):
                        best_opt = opt
                        break
                elif any(w in target_nd for w in ("spouse", "sot", "sotie", "супруг", "супруга", "муж", "жена")):
                    if any(w in txt_nd for w in ("spouse", "sot", "sotie", "супруг", "супруга", "муж", "жена")):
                        best_opt = opt
                        break

                # Physical condition rating matches
                if any(w in target_nd for w in ("excelenta", "excellent", "отличн", "forte buna", "good", "хорош")):
                    if any(w in txt_nd for w in ("excelent", "отличн", "buna", "good", "хорош", "5", "4")):
                        best_opt = opt
                        break

                # General substring match (longer strings only)
                if len(target_nd) > 3 and (target_nd in txt_nd or txt_nd in target_nd):
                    best_opt = opt
                    break

            if best_opt:
                try:
                    await best_opt.scroll_into_view_if_needed()
                    await best_opt.click(force=True, timeout=3000)
                    return
                except Exception:
                    pass

            # 3. Fallback for required dropdown: pick first non-placeholder option
            if field.required and count > 1:
                logger.warning(f"Selecting first valid option for required dropdown '{field.label}'")
                try:
                    await options.nth(1).scroll_into_view_if_needed()
                    await options.nth(1).click(force=True, timeout=3000)
                    return
                except Exception:
                    pass

        logger.warning(f"Could not select dropdown option '{option_text}' in field '{field.label}'")

    async def _fill_date(self, field: FormField, date_val: str) -> None:
        if not date_val:
            return

        container = await self._get_container(field)
        d_clean = date_val.strip()

        # Parse date parts
        day_str, month_str, year_str = "", "", ""
        for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(d_clean, fmt)
                day_str = f"{dt.day:02d}"
                month_str = f"{dt.month:02d}"
                year_str = str(dt.year)
                break
            except ValueError:
                continue

        # Check for multi-part sub-inputs (Google Forms separate Day, Month, Year widgets)
        inputs = container.locator('input[type="text"], input[type="number"], input:not([type])')
        input_count = await inputs.count()

        if input_count >= 3 and day_str and month_str and year_str:
            # Multi-input date field (Day, Month, Year)
            for i in range(input_count):
                inp = inputs.nth(i)
                aria = strip_diacritics((await inp.get_attribute("aria-label") or "").lower())
                await inp.scroll_into_view_if_needed()
                try:
                    await inp.click(force=True, timeout=1000)
                except Exception:
                    pass

                if any(w in aria for w in ("zi", "day", "день")):
                    await inp.fill(day_str)
                elif any(w in aria for w in ("luna", "month", "месяц")):
                    await inp.fill(month_str)
                elif any(w in aria for w in ("an", "year", "год")):
                    await inp.fill(year_str)
                elif i == 0:
                    await inp.fill(day_str)
                elif i == 1:
                    await inp.fill(month_str)
                elif i == 2:
                    await inp.fill(year_str)
            return

        # Single input field
        date_input = container.locator('input[type="date"], input[type="text"], input:not([type])')
        if await date_input.count() > 0:
            first_input = date_input.first
            input_type = await first_input.get_attribute("type")
            await first_input.scroll_into_view_if_needed()
            if input_type == "date":
                iso_val = f"{year_str}-{month_str}-{day_str}" if (year_str and month_str and day_str) else self._convert_date_to_iso(d_clean)
                try:
                    await first_input.fill(iso_val)
                    return
                except Exception:
                    pass
            try:
                await first_input.click(force=True, timeout=2000)
            except Exception:
                await first_input.focus()
            await first_input.fill(d_clean)
        else:
            await self._type_text(field, d_clean)

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
        """Detects whether current page has a 'Next' or 'Submit' button with universal diacritic tolerance."""
        buttons = self.page.locator('[role="button"]')
        button_count = await buttons.count()

        submit_btn = None
        next_btn = None

        for i in range(button_count):
            btn = buttons.nth(i)
            raw_text = (await btn.inner_text()).lower().strip()
            text_nd = strip_diacritics(raw_text)

            for s_kw in SUBMIT_BUTTON_TEXTS:
                s_kw_nd = strip_diacritics(s_kw.lower())
                if s_kw in raw_text or s_kw_nd in text_nd:
                    submit_btn = btn
                    break

            for n_kw in NEXT_BUTTON_TEXTS:
                n_kw_nd = strip_diacritics(n_kw.lower())
                if n_kw in raw_text or n_kw_nd in text_nd:
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
            try:
                await btn.scroll_into_view_if_needed()
                await btn.click(force=True, no_wait_after=True, timeout=5000)
            except Exception:
                try:
                    await btn.dispatch_event("click")
                except Exception:
                    pass
            return True

        # Fallback query for submit
        for s_kw in SUBMIT_BUTTON_TEXTS:
            b = self.page.get_by_role("button", name=s_kw, exact=False)
            if await b.count() > 0:
                logger.info(f"Clicking Submit button with text '{s_kw}'...")
                try:
                    await b.first.scroll_into_view_if_needed()
                    await b.first.click(force=True, no_wait_after=True, timeout=5000)
                except Exception:
                    try:
                        await b.first.dispatch_event("click")
                    except Exception:
                        pass
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
