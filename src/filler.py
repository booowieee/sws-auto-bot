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
    "trimiteți",
    "trimiteti",
    "trimitere",
    "submit",
    "отправить",
    "отправка",
    "готово",
    "send",
    "înregistrează",
    "inregistreaza",
    "înregistrare",
    "inregistrare",
    "finalizare",
    "finalizează",
    "finalizeaza",
    "termină",
    "termina",
    "completează",
    "completeaza",
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
    "raspunsul dumneavoastra a fost inregistrat",
    "răspunsul dvs. a fost înregistrat",
    "raspunsul dvs. a fost inregistrat",
    "răspunsul tău a fost înregistrat",
    "raspunsul tau a fost inregistrat",
    "raspunsul a fost inregistrat",
    "your response has been recorded",
    "ответ записан",
    "ваш ответ записан",
    "форма отправлена",
    "trimite un alt răspuns",
    "trimite un alt raspuns",
    "submit another response",
    "отправить еще один ответ",
    "отправить ещё один ответ",
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
        # Brief wait for Google Forms SPA transition to settle (section slide animation ~300ms)
        await asyncio.sleep(0.5)

        # Auto-check Google Account email recording checkbox if present on page
        await self._handle_google_account_email_checkbox()

        fields = await FormAnalyzer.extract_fields(self.page)

        # If no fields found, wait a bit longer and retry (slow DOM transitions)
        if not fields:
            await asyncio.sleep(1.0)
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

    async def _handle_google_account_email_checkbox(self) -> None:
        """
        When logged into a Google Account, Google Forms injects an email recording checkbox:
        'Record user@gmail.com as the email to be included with my response' /
        'Указать в моем ответе адрес электронной почты...' /
        'Înregistrează ... ca adresă de e-mail care va fi inclusă în răspuns'.
        This method ensures it is always checked.
        """
        try:
            email_cbs = self.page.locator(
                '[role="checkbox"][aria-label*="mail" i], '
                '[role="checkbox"][aria-label*="почт" i], '
                '[role="checkbox"][aria-label*="record" i], '
                '[role="checkbox"][aria-label*="указать" i], '
                '[role="checkbox"][aria-label*="înregistrează" i], '
                '[role="checkbox"][aria-label*="inregistreaza" i]'
            )
            count = await email_cbs.count()
            for i in range(count):
                cb = email_cbs.nth(i)
                if await cb.is_visible():
                    aria_checked = (await cb.get_attribute("aria-checked") or "").lower()
                    if aria_checked != "true":
                        logger.info("Found Google Account email recording checkbox. Auto-checking...")
                        await cb.scroll_into_view_if_needed()
                        await cb.click(force=True, timeout=2000)
                        logger.info("Google Account email recording checkbox checked.")
        except Exception as e:
            logger.debug(f"Google Account email checkbox check notice: {e}")

    async def _fill_field(self, match: FieldMatch) -> None:
        field = match.field
        value = str(match.resolved_value or "")

        try:
            logger.info(f"Filling field [{field.index}] '{field.label}' (Type: {field.field_type.value}) -> '{value}'")

            if field.field_type in (FieldType.TEXT, FieldType.TEXTAREA):
                await self._type_text(field, value)
            elif field.field_type == FieldType.RADIO:
                await self._select_radio(field, value)
            elif field.field_type == FieldType.CHECKBOX:
                await self._select_checkbox(field, value)
            elif field.field_type == FieldType.DROPDOWN:
                await self._select_dropdown(field, value)
            elif field.field_type == FieldType.DATE:
                await self._fill_date(field, value)
            else:
                logger.warning(f"Unsupported field type {field.field_type.value} for '{field.label}'")
        except Exception as e:
            # Log and continue instead of crashing the entire run.
            # Google Forms validation will catch unfilled required fields at submit time.
            if field.required:
                logger.error(f"Error filling required field '{field.label}': {e}. Will continue with remaining fields.")
            else:
                logger.warning(f"Error filling optional field '{field.label}': {e}. Continuing.")

    async def _get_container(self, field: FormField) -> Locator:
        """Finds the question container by matching heading label, with 1-to-1 index fallback.
        
        Only queries VISIBLE containers to avoid matching stale elements from
        previous sections in Google Forms' SPA-style DOM transitions.
        """
        containers = self.page.locator('[role="listitem"]:visible')
        count = await containers.count()
        if count == 0:
            containers = self.page.locator('[data-params]:visible')
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

            # Pass 2: Word boundary regex / substring match (only if label is distinctive, >= 4 chars)
            if len(clean_target_nd) >= 4:
                for i in range(count):
                    c = containers.nth(i)
                    heading = c.locator('[role="heading"], .M7eMe').first
                    if await heading.count() > 0:
                        htext = (await heading.inner_text()).strip().lower()
                        htext_clean_nd = strip_diacritics(re.sub(r"[\*\n\r\t]+", " ", htext).strip())
                        if clean_target_nd in htext_clean_nd or htext_clean_nd in clean_target_nd:
                            return c

        # Pass 3: 1-to-1 index fallback within visible containers
        if 0 < field.index <= count:
            return containers.nth(field.index - 1)

        return containers.first

    async def _type_text(self, field: FormField, text: str) -> None:
        if not text:
            return

        lbl_lower = field.label.lower()
        lbl_lower_nd = strip_diacritics(lbl_lower)

        # Today-date keywords with word boundaries to avoid "azi" matching "finalizare"
        today_patterns = (
            r"\btoday\b", r"\bazi\b", r"\bastazi\b", r"\bсегодня\b",
            r"\bdata\s+completarii\b", r"\bдата\s+заполнения\b",
            r"\btoday'?s?\s+date\b", r"\bdata\s+de\s+azi\b",
        )
        if any(re.search(p, lbl_lower_nd) for p in today_patterns):
            if not text or text == "None":
                text = datetime.now().strftime("%d/%m/%Y")
        elif any(d_fmt in lbl_lower for d_fmt in ("(dd/mm/yyyy)", "(zz/ll/aaaa)", "(дд/мм/гггг)", "dd/mm/yyyy", "zz/ll/aaaa")):
            if not text or not any(char.isdigit() for char in text):
                # Dynamic fallback: 90 days from now
                from datetime import timedelta
                text = (datetime.now() + timedelta(days=90)).strftime("%d/%m/%Y")

        container = await self._get_container(field)
        target_input = None

        # 1. Locate the VISIBLE interactive text input/textarea strictly inside the container
        for sel in (
            'input.whsOnd',
            'textarea.KHxj8b',
            'input[type="text"]',
            'input[type="email"]',
            'input[type="tel"]',
            'input[type="number"]',
            'textarea',
            'input:not([type]):not([type="hidden"])',
        ):
            loc = container.locator(sel)
            count = await loc.count()
            for i in range(count):
                el = loc.nth(i)
                try:
                    if await el.is_visible():
                        target_input = el
                        break
                except Exception:
                    continue
            if target_input:
                break

        # 2. Fallback: if not found by class/type, check entry_id inside container
        if not target_input and field.entry_id:
            loc = container.locator(f'input[name="{field.entry_id}"], textarea[name="{field.entry_id}"]')
            count = await loc.count()
            for i in range(count):
                el = loc.nth(i)
                try:
                    if await el.is_visible():
                        target_input = el
                        break
                except Exception:
                    continue

        if target_input:
            try:
                await target_input.scroll_into_view_if_needed(timeout=3000)
            except Exception as scroll_err:
                logger.warning(
                    f"Field [{field.index}] '{field.label}': scroll_into_view failed ({scroll_err}). "
                    "Attempting direct interaction."
                )

            # Check if element itself has the 'disabled' attribute directly.
            # NOTE: Do NOT use Playwright's is_disabled() here — it walks ancestor
            # elements checking for aria-disabled="true" and <fieldset disabled>,
            # which Google Forms sets on question wrappers even when the input is
            # fully interactive.  We only care about the element's own attribute.
            try:
                has_disabled_attr = await target_input.get_attribute("disabled")
                if has_disabled_attr is not None:
                    logger.info(f"Field [{field.index}] '{field.label}' has disabled attribute. Skipping.")
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
        if target_clean.startswith("da") or target_clean.startswith("yes") or target_clean.startswith("да") or target_clean in ("imediat", "immediately", "urgent", "срочно", "готов", "ready", "accept", "de acord"):
            candidate_syns.extend([s.lower().strip() for s in SEMANTIC_OPTION_MAP.get("da", [])])
        elif target_clean.startswith("nu") or target_clean.startswith("no") or target_clean.startswith("нет"):
            candidate_syns.extend([s.lower().strip() for s in SEMANTIC_OPTION_MAP.get("nu", [])])

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

        # 3. Fallback for binary / required Yes-No questions
        if count >= 2:
            if target_clean in ("imediat", "immediately", "urgent", "срочно", "готов", "ready", "accept", "de acord") or target_clean.startswith("da") or target_clean.startswith("yes") or target_clean.startswith("да"):
                first_r = radios.first
                await first_r.scroll_into_view_if_needed()
                try:
                    await first_r.click(force=True, timeout=2000)
                    return
                except Exception:
                    pass
            elif target_clean.startswith("nu") or target_clean.startswith("no") or target_clean.startswith("нет"):
                last_r = radios.nth(1)
                await last_r.scroll_into_view_if_needed()
                try:
                    await last_r.click(force=True, timeout=2000)
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
        # If this is an agreement / terms / declarations checkbox container or target is affirmative, select all
        is_agreement_label = any(kw in (field.label.lower() if field.label else "") for kw in ("agree", "read", "declar", "termeni", "conditii", "terms", "gdpr", "consent", "acord", "соглас", "подтвержд"))
        if is_agreement_label or target_clean in ("da", "yes", "true", "all", "agree", "de acord", "согласен", "все", "toate"):
            for i in range(count):
                cb = checkboxes.nth(i)
                checked = await cb.get_attribute("aria-checked")
                if checked != "true":
                    await cb.scroll_into_view_if_needed()
                    try:
                        await cb.click(force=True, timeout=2000)
                    except Exception:
                        pass
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

                # Duration of stay matches (6 months / full season)
                if any(w in target_nd for w in ("6 luni", "6 months", "6 мес", "tot sezonul", "full season", "весь сезон", "imediat")):
                    if any(w in txt_nd for w in ("6", "full season", "tot sezonul", "весь сезон", "maxim", "luni", "months", "месяц")):
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

            # Skip back / clear buttons
            if any(b_kw in text_nd for b_kw in ("inapoi", "back", "назад", "clear", "sterge", "очистить")):
                continue

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

        # Footer fallback: check buttons inside Google Forms footer container .lRwqEb
        footer_buttons = self.page.locator('.lRwqEb [role="button"], .ThqF7b [role="button"]')
        f_count = await footer_buttons.count()
        if f_count > 0:
            last_btn = footer_buttons.last
            b_txt = strip_diacritics((await last_btn.inner_text()).lower().strip())
            if not any(b_kw in b_txt for b_kw in ("inapoi", "back", "назад", "clear", "sterge", "очистить")):
                return last_btn, "submit"

        return None, "none"

    async def click_submit(self) -> bool:
        """Finds and clicks the submission button."""
        btn, btn_type = await self.find_navigation_button()
        if btn:
            logger.info(f"Clicking Submit button ({btn_type})...")
            try:
                await btn.scroll_into_view_if_needed()
                await btn.click(force=True, no_wait_after=True, timeout=5000)
                return True
            except Exception:
                try:
                    await btn.dispatch_event("click")
                    return True
                except Exception:
                    pass

        # Fallback query for submit text in page
        for s_kw in SUBMIT_BUTTON_TEXTS:
            b = self.page.get_by_role("button", name=s_kw, exact=False)
            if await b.count() > 0:
                logger.info(f"Clicking Submit button with text '{s_kw}'...")
                try:
                    await b.first.scroll_into_view_if_needed()
                    await b.first.click(force=True, no_wait_after=True, timeout=5000)
                    return True
                except Exception:
                    try:
                        await b.first.dispatch_event("click")
                        return True
                    except Exception:
                        pass

        # Universal footer action fallback: last button in page that is not Back
        buttons = self.page.locator('[role="button"]:visible')
        b_count = await buttons.count()
        for i in reversed(range(b_count)):
            b = buttons.nth(i)
            txt = strip_diacritics((await b.inner_text()).lower().strip())
            if not any(b_kw in txt for b_kw in ("inapoi", "back", "назад", "clear", "sterge", "очистить")):
                logger.info(f"Clicking last available footer action button (text: '{txt}')...")
                try:
                    await b.scroll_into_view_if_needed()
                    await b.click(force=True, no_wait_after=True, timeout=5000)
                    return True
                except Exception:
                    try:
                        await b.dispatch_event("click")
                        return True
                    except Exception:
                        pass

        logger.error("Submit button not found on the page.")
        return False

    async def verify_submission_status(self) -> Tuple[bool, str]:
        """
        Verifies whether form was successfully recorded or validation errors occurred.
        Returns (is_success, detail_message).
        """
        await asyncio.sleep(2.5)

        # 1. Check for any visible validation errors or required question alerts
        error_locators = self.page.locator('[role="alert"]:visible, .v5Duua:visible, .R2oA3c:visible, div[jsname="B34EJ"]:visible')
        err_count = await error_locators.count()
        if err_count > 0:
            err_texts = []
            for i in range(err_count):
                txt = (await error_locators.nth(i).inner_text()).strip()
                if txt and txt not in err_texts:
                    err_texts.append(txt)
            if err_texts:
                return False, f"Validation errors visible after submit: {err_texts}"

        for err_kw in VALIDATION_ERROR_TEXTS:
            err_elem = self.page.get_by_text(err_kw, exact=False)
            if await err_elem.count() > 0:
                for i in range(await err_elem.count()):
                    if await err_elem.nth(i).is_visible():
                        return False, f"Validation error visible on page: '{err_kw}'"

        # 2. Check confirmation message in page body
        body_text = (await self.page.inner_text("body")).lower()
        body_text_nd = strip_diacritics(body_text)
        for succ_kw in SUCCESS_CONFIRMATION_TEXTS:
            succ_kw_nd = strip_diacritics(succ_kw.lower())
            if succ_kw in body_text or succ_kw_nd in body_text_nd:
                return True, f"Confirmation text detected: '{succ_kw}'"

        # 3. Check URL change ONLY IF no question containers remain on the page
        current_url = self.page.url.lower()
        containers = self.page.locator('[role="listitem"]:visible')
        container_count = await containers.count()
        if "formresponse" in current_url and container_count == 0:
            return True, f"Redirected to confirmation page: {current_url}"

        # 4. If question containers are still visible, submission definitely did not complete!
        if container_count > 0:
            return False, f"Form still displaying {container_count} question(s) after submit (submission rejected by form)."

        return False, "Could not confirm submission status (unknown state)."
