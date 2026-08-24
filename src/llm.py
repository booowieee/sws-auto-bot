import asyncio
import json
import re
from typing import Any, Dict, List, Optional
import aiohttp
from pydantic import BaseModel, Field, ValidationError

from src.config import Config
from src.logger import logger
from src.models import FormField, UserProfile


class LLMFieldMatchResult(BaseModel):
    index: int
    matched_key: str = ""
    resolved_value: str = ""
    selected_option: Optional[str] = None
    confidence: float = 85.0


SYSTEM_PROMPT = """You are an automated form filler assistant for UK Seasonal Worker Scheme (SWS) applications.
You match unknown form questions to candidate profile data.

Given:
1. Candidate profile (JSON)
2. List of unmapped form questions (with field type and options if applicable)

Instructions:
- Match each question to the most accurate candidate profile field.
- For single-choice or multi-choice fields (radio, dropdown, checkbox), pick the closest matching string from the provided 'options' list for 'selected_option'.
- For text fields, provide the appropriate candidate answer for 'resolved_value'.
- If the question cannot be answered with the given profile, set 'resolved_value': "" and 'selected_option': null.
- Output ONLY a valid JSON array of objects. Do not include markdown explanation.

Output JSON format:
[
  {
    "index": 1,
    "matched_key": "health.allergies",
    "resolved_value": "Nu",
    "selected_option": "Nu am alergii"
  }
]"""


class LLMFallbackClient:
    """Async client for Tier 2 LLM batch matching of unmapped form fields."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ):
        self.api_key = api_key if api_key is not None else Config.LLM_API_KEY
        self.base_url = (base_url if base_url is not None else Config.LLM_BASE_URL).rstrip("/")
        self.model = model if model is not None else Config.LLM_MODEL
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else Config.LLM_TIMEOUT_SECONDS

    @property
    def is_available(self) -> bool:
        return bool(Config.LLM_FALLBACK_ENABLED and self.api_key)

    async def match_batch(
        self,
        fields: List[FormField],
        profile: UserProfile,
    ) -> List[LLMFieldMatchResult]:
        """Resolves unmapped fields in a single async batch call to LLM API."""
        if not fields or not self.is_available:
            return []

        prompt_payload = self._build_prompt_payload(fields, profile)

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            body = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt_payload},
                ],
                "temperature": 0.0,
            }

            api_endpoint = f"{self.base_url}/chat/completions"
            logger.info(f"Dispatching LLM Fallback batch request ({len(fields)} fields) to {self.model}...")

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(api_endpoint, headers=headers, json=body) as resp:
                    if resp.status != 200:
                        error_body = await resp.text()
                        logger.warning(f"LLM API returned HTTP {resp.status}: {error_body[:200]}")
                        return []

                    resp_data = await resp.json()
                    raw_content = resp_data["choices"][0]["message"]["content"]
                    return self._parse_llm_response(raw_content, fields)

        except asyncio.TimeoutError:
            logger.warning(f"LLM Fallback timed out after {self.timeout_seconds}s. Continuing without LLM.")
            return []
        except aiohttp.ClientError as e:
            logger.warning(f"LLM Fallback network error: {e}")
            return []
        except Exception:
            logger.exception("Unexpected error in LLM Fallback resolution")
            return []

    def _build_prompt_payload(self, fields: List[FormField], profile: UserProfile) -> str:
        """Constructs prompt containing candidate profile and target form fields."""
        questions_data = []
        for f in fields:
            q_item: Dict[str, Any] = {
                "index": f.index,
                "label": f.label,
                "type": f.field_type.value,
                "required": f.required,
            }
            if f.options:
                q_item["options"] = f.options
            questions_data.append(q_item)

        payload = {
            "candidate_profile": profile.model_dump(),
            "unmapped_questions": questions_data,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _parse_llm_response(self, raw_content: str, fields: List[FormField]) -> List[LLMFieldMatchResult]:
        """Extracts and validates structured JSON array from LLM response."""
        content = raw_content.strip()

        # Strip markdown fences if present
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
            content = content.strip()

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Fallback: extract first JSON array using regex
            match = re.search(r"\[\s*\{.*\}\s*\]", content, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse JSON from LLM response: {raw_content[:200]}")
                    return []
            else:
                logger.warning(f"No JSON array found in LLM response: {raw_content[:200]}")
                return []

        if not isinstance(data, list):
            logger.warning(f"Expected JSON list from LLM, got {type(data).__name__}")
            return []

        results: List[LLMFieldMatchResult] = []
        valid_indices = {f.index for f in fields}

        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                result = LLMFieldMatchResult(**item)
                if result.index in valid_indices:
                    results.append(result)
                    logger.info(
                        f"LLM matched field [{result.index}] -> key='{result.matched_key}', "
                        f"value='{result.resolved_value}', option='{result.selected_option}'"
                    )
            except ValidationError as e:
                logger.debug(f"Skipping invalid LLM result item {item}: {e}")

        return results
