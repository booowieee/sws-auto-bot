import pytest
from src.models import FieldMatch, FieldType, FormField, MatchMethod


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


def test_match_extended_fields(matcher):
    cases = [
        ("Mărimea încălțămintei de protecție (Safety shoe size)", "shoe_size", matcher.profile.ppe.shoe_size),
        ("Shoe size (EU/UK)", "shoe_size", matcher.profile.ppe.shoe_size),
        ("Размер спецобуви / ботинок", "shoe_size", matcher.profile.ppe.shoe_size),
        ("Aveți alergii cunoscute? (Allergies)", "allergies", matcher.profile.health.allergies),
        ("Any known food or medication allergies?", "allergies", matcher.profile.health.allergies),
        ("Наличие аллергии на укусы насекомых или препараты", "allergies", matcher.profile.health.allergies),
        ("Dețineți permis de conducere tractor?", "tractor_license", matcher.profile.work.tractor_license),
        ("Do you hold a valid tractor driving license?", "tractor_license", matcher.profile.work.tractor_license),
        ("Удостоверение тракториста-машиниста", "tractor_license", matcher.profile.work.tractor_license),
        ("Aeroportul preferat de plecare", "preferred_airport", matcher.profile.logistics.preferred_airport),
        ("Preferred airport of departure", "preferred_airport", matcher.profile.logistics.preferred_airport),
        ("Предпочитаемый аэропорт вылета", "preferred_airport", matcher.profile.logistics.preferred_airport),
        ("[DOB-VERIFY] Exact date you were born (Calendar selection)", "date_of_birth", matcher.profile.personal.date_of_birth),
        ("I confirm I have not paid any work-finding fees to third parties (GLAA Anti-Slavery Declaration)", "no_recruitment_fees", matcher.profile.compliance.truthful_declaration),
        ("Cetățenia deținută", "nationality", matcher.profile.personal.nationality),
        ("Relația de rudenie cu persoana de contact (Emergency Relation)", "emergency_relationship", matcher.profile.contacts.emergency_contact.relationship),
        ("Are you biological MALE or FEMALE?", "sex", matcher.profile.personal.sex),
        ("Ați avut vreodată refuz de viză pentru Marea Britanie sau altă țară?", "visa_refusal", matcher.profile.work.visa_refusal),
        ("Suferiți de afecțiuni medicale sau boli cronice care vă împiedică munca fizică?", "medical_conditions", matcher.profile.health.medical_conditions),
        ("Sunteți de acord cu cazarea oferită la fermă în rulote tip caravană?", "caravan_acceptance", matcher.profile.work.caravan_acceptance),
        ("Adresa completă de domiciliu din buletin / pașaport", "address_full", matcher.profile.contacts.address_full),
        ("1.1 SURNAME / LAST NAME ONLY (Family name)", "last_name", matcher.profile.personal.last_name),
        ("1.2 GIVEN / FIRST NAMES ONLY (Do not include surname)", "first_name", matcher.profile.personal.first_name),
    ]
    for label, expected_key, expected_val in cases:
        field = FormField(index=1, label=label, field_type=FieldType.TEXT, required=True)
        match = matcher.match_field(field)
        assert match.matched_key == expected_key, f"Failed on label: {label}"
        assert match.resolved_value == expected_val

