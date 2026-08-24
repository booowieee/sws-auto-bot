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
    assert match.method == MatchMethod.REGEX_PATTERN
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


def test_match_colloquial_options_sex(matcher):
    field = FormField(
        index=1,
        label="Sunteți bărbat sau femeie?",
        field_type=FieldType.RADIO,
        options=["Bărbat", "Femeie"],
        required=True,
    )
    match = matcher.match_field(field)
    assert match.matched_key == "sex"
    assert match.selected_option == "Bărbat"


def test_match_passport_moldovan(matcher):
    field = FormField(
        index=1,
        label="Seria pașaportului moldovenesc",
        field_type=FieldType.TEXT,
        required=True,
    )
    match = matcher.match_field(field)
    assert match.matched_key == "passport"
    assert match.resolved_value == matcher.profile.documents.passport_number


def test_unmatched_unknown_field(matcher):
    field = FormField(
        index=1,
        label="Favorite color in winter",
        field_type=FieldType.TEXT,
        required=False,
    )
    match = matcher.match_field(field)
    assert match.method == MatchMethod.UNMATCHED


def test_match_pure_english_fields(matcher):
    en_cases = [
        ("Full Legal Name (as printed on passport)", "full_name", matcher.profile.personal.full_name),
        ("Place of Birth (Town and Country)", "place_of_birth", matcher.profile.personal.place_of_birth),
        ("Biometric Passport Number", "passport", matcher.profile.documents.passport_number),
        ("Passport Expiry Date", "passport_expiry", matcher.profile.documents.passport_expiry),
        ("Next of Kin Full Name (Emergency Contact)", "emergency_name", matcher.profile.contacts.emergency_contact.name),
        ("Next of Kin Phone Number", "emergency_phone", matcher.profile.contacts.emergency_contact.phone),
        ("Full Residential Address", "address_full", matcher.profile.contacts.address_full),
        ("Do you have commercial agricultural picking experience?", "experience_agriculture", matcher.profile.work.experience_agriculture_text),
    ]
    for label, expected_key, expected_val in en_cases:
        field = FormField(index=1, label=label, field_type=FieldType.TEXT, required=True)
        match = matcher.match_field(field)
        assert match.matched_key == expected_key, f"Failed on label: {label}"
        assert match.resolved_value == expected_val


def test_match_pure_russian_fields(matcher):
    ru_cases = [
        ("ФИО соискателя (полностью на латинице)", "full_name", matcher.profile.personal.full_name),
        ("Число, месяц и год рождения", "date_of_birth", matcher.profile.personal.date_of_birth),
        ("Место рождения (город и страна)", "place_of_birth", matcher.profile.personal.place_of_birth),
        ("Номер и серия загранпаспорта", "passport", matcher.profile.documents.passport_number),
        ("Дата окончания срока действия паспорта", "passport_expiry", matcher.profile.documents.passport_expiry),
        ("ФИО контактного лица на случай ЧС (родственник)", "emergency_name", matcher.profile.contacts.emergency_contact.name),
        ("Телефон контактного лица", "emergency_phone", matcher.profile.contacts.emergency_contact.phone),
        ("Фактический адрес проживания (полный)", "address_full", matcher.profile.contacts.address_full),
        ("Имеете ли практический опыт полевых работ на сборе урожая?", "experience_agriculture", matcher.profile.work.experience_agriculture_text),
    ]
    for label, expected_key, expected_val in ru_cases:
        field = FormField(index=1, label=label, field_type=FieldType.TEXT, required=True)
        match = matcher.match_field(field)
        assert match.matched_key == expected_key, f"Failed on label: {label}"
        assert match.resolved_value == expected_val


def test_match_english_russian_choice_options(matcher):
    # Gender in English and Russian
    f_en_sex = FormField(index=1, label="Gender", field_type=FieldType.RADIO, options=["Male", "Female"], required=True)
    assert matcher.match_field(f_en_sex).selected_option == "Male"

    f_ru_sex = FormField(index=1, label="Ваш пол", field_type=FieldType.RADIO, options=["Мужской", "Женский"], required=True)
    assert matcher.match_field(f_ru_sex).selected_option == "Мужской"

    # Marital status in English and Russian
    f_en_ms = FormField(index=1, label="Marital Status", field_type=FieldType.DROPDOWN, options=["Single", "Married", "Divorced"], required=False)
    assert matcher.match_field(f_en_ms).selected_option == "Single"

    f_ru_ms = FormField(index=1, label="Семейное положение", field_type=FieldType.DROPDOWN, options=["Холост / Не замужем", "В браке", "Разведен(а)"], required=False)
    assert matcher.match_field(f_ru_ms).selected_option == "Холост / Не замужем"

    # Agreements in English and Russian
    f_en_gdpr = FormField(index=1, label="Agree to privacy policy and GDPR terms:", field_type=FieldType.RADIO, options=["Yes, agree", "No"], required=True)
    assert matcher.match_field(f_en_gdpr).selected_option == "Yes, agree"

    f_ru_gdpr = FormField(index=1, label="Согласие на обработку персональных данных:", field_type=FieldType.RADIO, options=["Да, согласен", "Нет"], required=True)
    assert matcher.match_field(f_ru_gdpr).selected_option == "Да, согласен"
