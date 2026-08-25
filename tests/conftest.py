"""Shared test fixtures for sws-auto-bot test suite."""
import pytest

from src.config import Config, load_profile, load_synonyms
from src.matcher import FieldMatcher


class MockAsyncContextManager:
    """Reusable mock for async context managers (aiohttp sessions, etc.)."""

    def __init__(self, return_value):
        self.return_value = return_value

    async def __aenter__(self):
        return self.return_value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.fixture
def sample_profile():
    """Loads the example profile for testing."""
    return load_profile(Config.PROFILE_EXAMPLE_PATH)


@pytest.fixture
def synonyms():
    """Loads the synonyms dictionary for testing."""
    return load_synonyms(Config.SYNONYMS_PATH)


@pytest.fixture
def matcher(sample_profile, synonyms):
    """Creates a FieldMatcher with example profile and synonyms."""
    return FieldMatcher(sample_profile, synonyms)
