import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.config import Config, load_profile
from src.llm import LLMFallbackClient, LLMFieldMatchResult
from src.models import FieldType, FormField


class MockAsyncContextManager:
    def __init__(self, return_value):
        self.return_value = return_value

    async def __aenter__(self):
        return self.return_value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.fixture
def sample_profile():
    return load_profile(Config.PROFILE_EXAMPLE_PATH)


def test_llm_client_availability():
    client_no_key = LLMFallbackClient(api_key="")
    assert not client_no_key.is_available

    with patch.object(Config, "LLM_FALLBACK_ENABLED", True):
        client_with_key = LLMFallbackClient(api_key="test-key")
        assert client_with_key.is_available


def test_llm_client_match_batch_disabled(sample_profile):
    client = LLMFallbackClient(api_key="")
    field = FormField(index=1, label="Custom Question", field_type=FieldType.TEXT, required=True)
    results = asyncio.run(client.match_batch([field], sample_profile))
    assert results == []


def test_llm_client_match_batch_success(sample_profile):
    client = LLMFallbackClient(api_key="sk-test", model="test-model")
    fields = [
        FormField(index=1, label="Do you have any dietary restrictions?", field_type=FieldType.RADIO, options=["No restrictions", "Vegetarian", "Halal"], required=True),
        FormField(index=2, label="Your emergency contact relation", field_type=FieldType.TEXT, required=True),
    ]

    mock_llm_json = [
        {
            "index": 1,
            "matched_key": "health.dietary_requirements",
            "resolved_value": "",
            "selected_option": "No restrictions",
        },
        {
            "index": 2,
            "matched_key": "contacts.emergency_contact.relationship",
            "resolved_value": "Mother",
            "selected_option": None,
        },
    ]

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={
        "choices": [
            {"message": {"content": json.dumps(mock_llm_json)}}
        ]
    })

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=MockAsyncContextManager(mock_resp))

    with patch.object(Config, "LLM_FALLBACK_ENABLED", True):
        with patch("aiohttp.ClientSession", return_value=MockAsyncContextManager(mock_session)):
            results = asyncio.run(client.match_batch(fields, sample_profile))
            assert len(results) == 2
            assert results[0].index == 1
            assert results[0].selected_option == "No restrictions"
            assert results[1].index == 2
            assert results[1].resolved_value == "Mother"


def test_llm_client_parse_markdown_fences(sample_profile):
    client = LLMFallbackClient(api_key="sk-test")
    fields = [FormField(index=1, label="Test Q", field_type=FieldType.TEXT, required=True)]
    
    raw_markdown = "```json\n[\n  {\"index\": 1, \"matched_key\": \"personal.country\", \"resolved_value\": \"Moldova\", \"selected_option\": null}\n]\n```"
    results = client._parse_llm_response(raw_markdown, fields)
    assert len(results) == 1
    assert results[0].index == 1
    assert results[0].resolved_value == "Moldova"


def test_llm_client_timeout_handling(sample_profile):
    client = LLMFallbackClient(api_key="sk-test", timeout_seconds=0.1)
    fields = [FormField(index=1, label="Test Q", field_type=FieldType.TEXT, required=True)]

    mock_session = MagicMock()
    mock_session.post.side_effect = asyncio.TimeoutError()

    with patch.object(Config, "LLM_FALLBACK_ENABLED", True):
        with patch("aiohttp.ClientSession", return_value=MockAsyncContextManager(mock_session)):
            results = asyncio.run(client.match_batch(fields, sample_profile))
            assert results == []
