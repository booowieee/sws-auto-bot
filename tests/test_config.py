import pytest
from pathlib import Path
from src.config import Config, load_profile, load_synonyms
from src.models import UserProfile


def test_load_example_profile():
    profile = load_profile(Config.PROFILE_EXAMPLE_PATH)
    assert isinstance(profile, UserProfile)
    assert profile.personal.first_name != ""
    assert profile.contacts.email != ""
    assert profile.documents.passport_number != ""


def test_load_synonyms():
    synonyms = load_synonyms(Config.SYNONYMS_PATH)
    assert "full_name" in synonyms
    assert "email" in synonyms
    assert "phone" in synonyms
    assert "passport" in synonyms
    assert "sex" in synonyms

    full_name_entry = synonyms["full_name"]
    assert len(full_name_entry.keywords) > 0
    assert full_name_entry.profile_key == "personal.full_name"
