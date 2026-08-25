import asyncio
import json
import re
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional
import aiohttp
from pydantic import BaseModel, Field, ValidationError, field_validator

from src.bot.db import BotDatabase
from src.config import Config
from src.llm import LLMFieldMatchResult, SYSTEM_PROMPT
from src.logger import logger
from src.models import FormField, UserProfile


class LLMProviderConfig(BaseModel):
    name: str
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 3.0
    is_active: bool = True


class LLMRouter:
    """Multi-provider LLM failover router with semantic caching and structured output validation."""

    def __init__(self, db: Optional[BotDatabase] = None):
        self.db = db or BotDatabase()
        self.providers: List[LLMProviderConfig] = self._init_providers()

    def _init_providers(self) -> List[LLMProviderConfig]:
        """Initializes providers from configuration and environment variables."""
        providers = []

        # 1. Google Gemini (Primary Tier)
        gemini_key = Config.GEMINI_API_KEY
        if gemini_key:
            providers.append(
                LLMProviderConfig(
                    name="Google Gemini",
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                    api_key=gemini_key,
                    model=Config.GEMINI_MODEL or "gemini-flash-lite-latest",
                    timeout_seconds=5.0,
                )
            )

        # 2. Groq (Secondary Ultra-Fast Tier ~200ms)
        groq_key = Config.GROQ_API_KEY
        if groq_key:
            providers.append(
                LLMProviderConfig(
                    name="Groq",
                    base_url="https://api.groq.com/openai/v1",
                    api_key=groq_key,
                    model="llama-3.3-70b-versatile",
                    timeout_seconds=2.5,
                )
            )

        # 3. Standard OpenAI / OpenRouter (Third Tier)
        openai_key = Config.OPENAI_API_KEY
        if openai_key:
            providers.append(
                LLMProviderConfig(
                    name="OpenAI",
                    base_url=Config.LLM_BASE_URL or "https://api.openai.com/v1",
                    api_key=openai_key,
                    model=Config.LLM_MODEL or "gpt-4o-mini",
                    timeout_seconds=3.5,
                )
            )

        # 4. Local Ollama (Offline Local Fallback)
        ollama_url = Config.OLLAMA_BASE_URL
        if ollama_url:
            providers.append(
                LLMProviderConfig(
                    name="Local Ollama",
                    base_url=ollama_url,
                    api_key="ollama",
                    model=Config.OLLAMA_MODEL or "llama3.2:3b",
                    timeout_seconds=4.0,
                )
            )

        return providers

    @property
    def is_available(self) -> bool:
        return bool(self.providers) and Config.LLM_FALLBACK_ENABLED

    async def resolve_batch(
        self, fields: List[FormField], profile: UserProfile
    ) -> List[LLMFieldMatchResult]:
        """
        Resolves unmapped fields with Semantic Cache check first,
        then cascade across LLM providers with zero-downtime failover.
        """
        if not fields:
            return []

        resolved_results: List[LLMFieldMatchResult] = []
        pending_fields: List[FormField] = []

        # Step 1: Query Local Semantic Cache
        for f in fields:
            cache_key = BotDatabase.generate_cache_key(f.label, f.field_type.value, f.options)
            cached = await self.db.get_cached_field(cache_key)
            if cached:
                logger.info(f"Semantic Cache HIT for field [{f.index}] '{f.label}' -> '{cached['resolved_value']}'")
                resolved_results.append(
                    LLMFieldMatchResult(
                        index=f.index,
                        matched_key=cached["matched_key"],
                        resolved_value=cached["resolved_value"],
                        selected_option=cached["selected_option"],
                        confidence=cached.get("confidence", 95.0),
                    )
                )
            else:
                pending_fields.append(f)

        if not pending_fields:
            return resolved_results

        if not self.is_available:
            logger.debug("LLMRouter is disabled or no providers configured. Skipping LLM query.")
            return resolved_results

        # Step 2: Query Providers in Priority Order (Failover Cascade)
        llm_batch_results = await self._query_providers_with_failover(pending_fields, profile)

        # Step 3: Cache newly resolved results and merge
        for res in llm_batch_results:
            orig_field = next((f for f in pending_fields if f.index == res.index), None)
            if orig_field:
                cache_key = BotDatabase.generate_cache_key(
                    orig_field.label, orig_field.field_type.value, orig_field.options
                )
                await self.db.set_cached_field(
                    cache_key=cache_key,
                    label=orig_field.label,
                    matched_key=res.matched_key,
                    resolved_value=res.resolved_value,
                    selected_option=res.selected_option,
                    confidence=res.confidence,
                )
            resolved_results.append(res)

        return resolved_results

    async def _query_providers_with_failover(
        self, fields: List[FormField], profile: UserProfile
    ) -> List[LLMFieldMatchResult]:
        """Tries each provider in order until one succeeds."""
        payload = self._build_prompt_payload(fields, profile)

        for provider in self.providers:
            if not provider.is_active:
                continue

            try:
                logger.info(f"LLMRouter: Attempting resolution with provider '{provider.name}' ({provider.model})...")
                results = await self._call_provider(provider, payload, fields)
                if results:
                    logger.info(f"LLMRouter: Successfully resolved {len(results)} field(s) with '{provider.name}'.")
                    return results
            except Exception as e:
                logger.warning(f"LLMRouter: Provider '{provider.name}' failed: {e}. Falling over to next provider...")

        logger.error("LLMRouter: All configured LLM providers failed or returned empty.")
        return []

    async def _call_provider(
        self, provider: LLMProviderConfig, payload: Dict[str, Any], fields: List[FormField]
    ) -> List[LLMFieldMatchResult]:
        """Makes an async POST request to the provider's chat completions endpoint."""
        url = f"{provider.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": provider.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }

        timeout = aiohttp.ClientTimeout(total=provider.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=body) as resp:
                if resp.status != 200:
                    err_body = await resp.text()
                    raise RuntimeError(f"HTTP {resp.status}: {err_body[:200]}")

                data = await resp.json()
                content = data["choices"][0]["message"]["content"]
                return self._parse_llm_response(content, fields)

    def _build_prompt_payload(self, fields: List[FormField], profile: UserProfile) -> Dict[str, Any]:
        return {
            "candidate_profile": profile.model_dump(),
            "questions_to_match": [
                {
                    "index": f.index,
                    "label": f.label,
                    "type": f.field_type.value,
                    "options": f.options,
                    "required": f.required,
                }
                for f in fields
            ],
        }

    def _parse_llm_response(self, raw_content: str, fields: List[FormField]) -> List[LLMFieldMatchResult]:
        content = raw_content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
            content = content.strip()

        try:
            data = json.loads(content)
            if isinstance(data, dict):
                # Unpack if nested under key like 'matches' or 'questions'
                for v in data.values():
                    if isinstance(v, list):
                        data = v
                        break
        except json.JSONDecodeError:
            match = re.search(r"\[\s*\{.*\}\s*\]", content, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                except json.JSONDecodeError:
                    return []
            else:
                return []

        if not isinstance(data, list):
            return []

        results: List[LLMFieldMatchResult] = []
        valid_indices = {f.index for f in fields}

        for item in data:
            if isinstance(item, dict):
                try:
                    res = LLMFieldMatchResult(**item)
                    if res.index in valid_indices:
                        results.append(res)
                except ValidationError:
                    pass

        return results
