import re
from typing import List, Tuple
from playwright.async_api import Page, ElementHandle

from src.logger import logger
from src.models import FieldType, FormField

CLOSED_TEXT_MARKERS = [
    "nu mai acceptă răspunsuri",
    "nu mai accepta raspunsuri",
    "no longer accepting responses",
    "the form is closed",
    "не принимает ответы",
    "форма закрыта",
    "formularul este închis",
    "formularul este inchis",
]


class FormAnalyzer:
    """Parses Google Forms DOM to extract structured question items and metadata."""

    @staticmethod
    async def is_form_closed(page: Page) -> Tuple[bool, str]:
        """Checks if the form is currently closed for submissions."""
        current_url = page.url.lower()
        if "closedform" in current_url:
            return True, f"Closed URL detected: {current_url}"

        body_text = (await page.inner_text("body")).lower()
        for marker in CLOSED_TEXT_MARKERS:
            if marker in body_text:
                return True, f"Closed marker found in text: '{marker}'"

        return False, ""

    @classmethod
    async def extract_fields(cls, page: Page) -> List[FormField]:
        """Scans the visible page for Google Form questions."""
        fields: List[FormField] = []

        # Find all question containers
        containers = await page.query_selector_all('[role="listitem"]')
        if not containers:
            containers = await page.query_selector_all('[data-params]')

        logger.info(f"Found {len(containers)} question containers on current page.")

        for idx, container in enumerate(containers):
            field = await cls._parse_container(container, idx + 1)
            if field and field.field_type != FieldType.UNKNOWN:
                fields.append(field)

        return fields

    @classmethod
    async def _parse_container(cls, container: ElementHandle, index: int) -> FormField:
        # Extract label
        label_elem = await container.query_selector('[role="heading"]')
        if not label_elem:
            label_elem = await container.query_selector('.M7eMe, [data-params] span')

        raw_label = (await label_elem.inner_text()).strip() if label_elem else ""

        # Clean label from asterisks or required notices
        clean_label = re.sub(r"[\*\n\r\t]+", " ", raw_label).strip()
        if not clean_label:
            clean_label = f"Question_{index}"

        # Detect required status
        is_required = False
        required_marker = await container.query_selector('.v5Duua, [aria-label*="required"], [aria-label*="obligatoriu"]')
        if required_marker or "*" in raw_label:
            is_required = True

        # Extract entry ID
        entry_id = await cls._extract_entry_id(container)

        # Detect field type and options
        field_type, options = await cls._detect_type_and_options(container, clean_label)

        return FormField(
            index=index,
            label=clean_label,
            field_type=field_type,
            entry_id=entry_id,
            required=is_required,
            options=options,
        )

    @staticmethod
    async def _extract_entry_id(container: ElementHandle) -> str:
        # Try input name attribute
        input_elem = await container.query_selector('input[name^="entry."], textarea[name^="entry."]')
        if input_elem:
            name_attr = await input_elem.get_attribute("name")
            if name_attr:
                return name_attr

        # Try data-params JSON blob
        data_params = await container.get_attribute("data-params")
        if data_params:
            match = re.search(r"\[\[(\d{6,})", data_params)
            if match:
                return f"entry.{match.group(1)}"

        return ""

    @staticmethod
    async def _detect_type_and_options(container: ElementHandle, label: str) -> Tuple[FieldType, List[str]]:
        label_lower = label.lower()

        # 1. Radio buttons
        radios = await container.query_selector_all('[role="radio"], input[type="radio"]')
        if radios:
            options = []
            for r in radios:
                # Option label can be on the element aria-label or child span
                opt_text = await r.get_attribute("data-value") or await r.get_attribute("aria-label")
                if not opt_text:
                    opt_text = (await r.inner_text()).strip()
                if not opt_text:
                    parent_label = await r.query_selector("xpath=ancestor::label")
                    if parent_label:
                        opt_text = (await parent_label.inner_text()).strip()
                if opt_text and opt_text not in options:
                    options.append(opt_text.strip())
            return FieldType.RADIO, options

        # 2. Checkboxes
        checkboxes = await container.query_selector_all('[role="checkbox"], input[type="checkbox"]')
        if checkboxes:
            options = []
            for c in checkboxes:
                opt_text = await c.get_attribute("data-value") or await c.get_attribute("aria-label")
                if not opt_text:
                    opt_text = (await c.inner_text()).strip()
                if not opt_text:
                    parent_label = await c.query_selector("xpath=ancestor::label")
                    if parent_label:
                        opt_text = (await parent_label.inner_text()).strip()
                if opt_text and opt_text not in options:
                    options.append(opt_text.strip())
            return FieldType.CHECKBOX, options

        # 3. Dropdown (custom listbox)
        dropdown = await container.query_selector('[role="listbox"]')
        if dropdown:
            # Dropdown options might not be rendered in DOM until clicked,
            # or stored in data-params / aria
            options = []
            opt_elems = await dropdown.query_selector_all('[role="option"]')
            for o in opt_elems:
                text = (await o.inner_text()).strip()
                if text and text not in options and text.lower() not in ("choose", "alege", "выберите"):
                    options.append(text)
            return FieldType.DROPDOWN, options

        # 4. Textarea
        textarea = await container.query_selector("textarea")
        if textarea:
            return FieldType.TEXTAREA, []

        # 5. Date inputs
        date_input = await container.query_selector('input[type="date"]')
        if date_input or any(w in label_lower for w in ["data nasterii", "date of birth", "дата рождения"]):
            return FieldType.DATE, []

        # 6. File upload
        file_input = await container.query_selector('input[type="file"], [data-file-upload]')
        if file_input:
            return FieldType.FILE_UPLOAD, []

        # 7. Text input
        text_input = await container.query_selector('input[type="text"], input:not([type])')
        if text_input:
            return FieldType.TEXT, []

        return FieldType.UNKNOWN, []
