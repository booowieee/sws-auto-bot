import pytest
from src.config import Config, load_profile, load_synonyms
from src.matcher import FieldMatcher
from src.models import FieldMatch, FieldType, FormField, MatchMethod


@pytest.fixture
def matcher():
    profile = load_profile(Config.PROFILE_EXAMPLE_PATH)
    synonyms = load_synonyms(Config.SYNONYMS_PATH)
    return FieldMatcher(profile, synonyms)


def test_match_full_name_compound(matcher):
    field = FormField(
        index=1,
        label="Nume și Prenume (Full Name)",
        field_type=FieldType.TEXT,
        required=True,
    )
    match = matcher.match_field(field)
    assert match.method in (MatchMethod.REGEX_PATTERN, MatchMethod.EXACT_KEYWORD, MatchMethod.FUZZY)
    assert match.matched_key == "full_name"
    assert match.resolved_value == matcher.profile.personal.full_name


def test_match_first_name_only(matcher):
    field = FormField(
        index=2,
        label="Prenumele dumneavoastră",
        field_type=FieldType.TEXT,
        required=True,
    )
    match = matcher.match_field(field)
    assert match.matched_key == "first_name"
    assert match.resolved_value == matcher.profile.personal.first_name


def test_match_email_variations(matcher):
    variations = [
        "Adresă de e-mail",
        "Your Email Address",
        "Электронная почта",
    ]
    for label in variations:
        field = FormField(index=1, label=label, field_type=FieldType.TEXT, required=True)
        match = matcher.match_field(field)
        assert match.matched_key == "email"
        assert match.resolved_value == matcher.profile.contacts.email


def test_match_phone_variations(matcher):
    variations = [
        "Număr de telefon / WhatsApp",
        "Phone number",
        "Контактный номер телефона",
    ]
    for label in variations:
        field = FormField(index=1, label=label, field_type=FieldType.TEXT, required=True)
        match = matcher.match_field(field)
        assert match.matched_key == "phone"
        assert match.resolved_value == matcher.profile.contacts.phone


def test_match_radio_options_sex(matcher):
    field = FormField(
        index=1,
        label="Sex / Gen",
        field_type=FieldType.RADIO,
        options=["Masculin", "Feminin"],
        required=True,
    )
    match = matcher.match_field(field)
    assert match.matched_key == "sex"
    assert match.selected_option == "Masculin"


def test_match_radio_options_experience(matcher):
    field = FormField(
        index=1,
        label="Ai experiență în agricultură?",
        field_type=FieldType.RADIO,
        options=["Da, am experiență", "Nu am experiență"],
        required=True,
    )
    match = matcher.match_field(field)
    assert match.matched_key == "experience_agriculture"
    assert match.selected_option == "Da, am experiență"


def test_unmatched_unknown_field(matcher):
    field = FormField(
        index=1,
        label="Favorite color in winter",
        field_type=FieldType.TEXT,
        required=False,
    )
    match = matcher.match_field(field)
    assert match.method == MatchMethod.UNMATCHED
