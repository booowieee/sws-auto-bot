import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.bot.db import BotDatabase
from src.config import Config
from src.llm import LLMFieldMatchResult
from src.llm_router import LLMProviderConfig, LLMRouter
from src.models import FieldType, FormField
from tests.conftest import MockAsyncContextManager


@pytest.fixture
def test_db(tmp_path: Path):
    db_file = tmp_path / "test_llm_router.db"
    db = BotDatabase(db_path=db_file)
    asyncio.run(db.init_db())
    return db


def test_llm_router_init(test_db):
    router = LLMRouter(db=test_db)
    assert isinstance(router.providers, list)


def test_llm_router_cache_hit(test_db, sample_profile):
    async def _test():
        router = LLMRouter(db=test_db)
        field = FormField(index=1, label="Mărimea pantofilor", field_type=FieldType.TEXT, required=True)

        cache_key = BotDatabase.generate_cache_key(field.label, field.field_type.value, field.options)
        await test_db.set_cached_field(
            cache_key=cache_key,
            label=field.label,
            matched_key="ppe.shoe_size",
            resolved_value="43",
            confidence=95.0,
        )

        results = await router.resolve_batch([field], sample_profile)
        assert len(results) == 1
        assert results[0].index == 1
        assert results[0].matched_key == "ppe.shoe_size"
        assert results[0].resolved_value == "43"

    asyncio.run(_test())


def test_llm_router_failover_success(test_db, sample_profile):
    async def _test():
        router = LLMRouter(db=test_db)
        router.providers = [
            LLMProviderConfig(name="Primary-Failing", base_url="https://api.fail.com", api_key="k1", model="m1"),
            LLMProviderConfig(name="Secondary-Success", base_url="https://api.ok.com", api_key="k2", model="m2"),
        ]

        field = FormField(index=1, label="Allergies question", field_type=FieldType.RADIO, options=["Da", "Nu"], required=True)

        mock_call = AsyncMock()
        mock_call.side_effect = [
            RuntimeError("Primary provider 500 error"),
            [LLMFieldMatchResult(index=1, matched_key="health.allergies", resolved_value="Nu", selected_option="Nu")],
        ]

        router._call_provider = mock_call

        with patch.object(Config, "LLM_FALLBACK_ENABLED", True):
            results = await router.resolve_batch([field], sample_profile)
            assert len(results) == 1
            assert results[0].matched_key == "health.allergies"
            assert results[0].resolved_value == "Nu"
            assert mock_call.call_count == 2

    asyncio.run(_test())
