"""Tests for audit-fix features: ComplianceInfo, today_date, _is_type_compatible DROPDOWN guard."""
import re
from datetime import datetime

import pytest

from src.matcher import FieldMatcher
from src.models import (
    ComplianceInfo,
    FieldType,
    FormField,
    MatchMethod,
    UserProfile,
)


# ---------------------------------------------------------------------------
# _is_type_compatible — DROPDOWN guard (C6 fix)
# ---------------------------------------------------------------------------

class TestIsTypeCompatible:
    """Verifies that text-only synonym keys are blocked from radio, checkbox AND dropdown."""

    TEXT_ONLY_KEYS = (
        "email", "phone", "whatsapp", "viber", "first_name", "last_name",
        "patronymic", "full_name", "address_full", "address_street",
        "postal_code", "passport_number", "id_card_number",
        "emergency_phone", "emergency_email", "emergency_name",
        "telegram_contact", "social_media",
        "date_of_birth", "passport_issue_date", "passport_expiry",
    )

    @pytest.mark.parametrize("syn_key", TEXT_ONLY_KEYS)
    def test_text_keys_blocked_for_radio(self, syn_key):
        assert not FieldMatcher._is_type_compatible(syn_key, FieldType.RADIO)

    @pytest.mark.parametrize("syn_key", TEXT_ONLY_KEYS)
    def test_text_keys_blocked_for_checkbox(self, syn_key):
        assert not FieldMatcher._is_type_compatible(syn_key, FieldType.CHECKBOX)

    @pytest.mark.parametrize("syn_key", TEXT_ONLY_KEYS)
    def test_text_keys_blocked_for_dropdown(self, syn_key):
        assert not FieldMatcher._is_type_compatible(syn_key, FieldType.DROPDOWN)

    @pytest.mark.parametrize("syn_key", TEXT_ONLY_KEYS)
    def test_text_keys_allowed_for_text(self, syn_key):
        assert FieldMatcher._is_type_compatible(syn_key, FieldType.TEXT)

    @pytest.mark.parametrize("field_type", [FieldType.RADIO, FieldType.CHECKBOX, FieldType.DROPDOWN])
    def test_non_text_keys_allowed(self, field_type):
        """Synonym keys like 'sex', 'experience_agriculture' should still match radio/checkbox/dropdown."""
        assert FieldMatcher._is_type_compatible("sex", field_type)
        assert FieldMatcher._is_type_compatible("experience_agriculture", field_type)
        assert FieldMatcher._is_type_compatible("caravan_acceptance", field_type)


# ---------------------------------------------------------------------------
# ComplianceInfo model defaults
# ---------------------------------------------------------------------------

class TestComplianceInfo:
    """Verifies ComplianceInfo default values used for agreement checkboxes."""

    def test_defaults(self):
        info = ComplianceInfo()
        assert info.truthful_declaration == "Da"
        assert info.gdpr_consent == "Da"
        assert info.consent_contact == "Da"
        assert info.terms_agreement == "Da"
        assert info.false_info_warning == "Da"
        assert info.signature == ""

    def test_profile_includes_compliance(self):
        profile = UserProfile()
        assert hasattr(profile, "compliance")
        assert isinstance(profile.compliance, ComplianceInfo)
        assert profile.compliance.truthful_declaration == "Da"


# ---------------------------------------------------------------------------
# today_date resolution in matcher
# ---------------------------------------------------------------------------

class TestTodayDateResolution:
    """Verifies that personal.today_date always resolves to current date."""

    def test_today_date_resolves_to_current(self, matcher):
        field = FormField(
            index=1,
            label="Today's Date (Data de azi)",
            field_type=FieldType.TEXT,
            required=True,
        )
        match = matcher.match_field(field)
        assert match.matched_key == "today_date"
        # Should resolve to today's date in DD/MM/YYYY format
        expected = datetime.now().strftime("%d/%m/%Y")
        assert match.resolved_value == expected

    def test_today_date_not_confused_with_deadline(self, matcher):
        """'Data cea mai târzie de finalizare' should NOT match today_date at priority confidence."""
        field = FormField(
            index=1,
            label="Data cea mai târzie de finalizare a contractului",
            field_type=FieldType.TEXT,
            required=False,
        )
        match = matcher.match_field(field)
        # Even if fuzzy matches today_date, it should be low-confidence fuzzy, not priority/regex
        if match.matched_key == "today_date":
            assert match.method == MatchMethod.FUZZY
            assert match.confidence < 85.0


# ---------------------------------------------------------------------------
# Synonym mapping correctness (C3 fix)
# ---------------------------------------------------------------------------

class TestSynonymMappingFixes:
    """Verifies corrected synonym profile_key mappings."""

    def test_no_recruitment_fees_maps_to_compliance(self, matcher):
        field = FormField(
            index=1,
            label="I confirm I have not paid any work-finding fees (GLAA Anti-Slavery Declaration)",
            field_type=FieldType.TEXT,
            required=True,
        )
        match = matcher.match_field(field)
        assert match.matched_key == "no_recruitment_fees"
        assert match.profile_key == "compliance.truthful_declaration"
        assert match.resolved_value == "Da"

    def test_uk_labour_laws_maps_to_compliance(self, matcher):
        field = FormField(
            index=1,
            label="Do you understand UK labour laws and worker rights?",
            field_type=FieldType.TEXT,
            required=True,
        )
        match = matcher.match_field(field)
        assert match.matched_key == "uk_labour_laws"
        assert match.profile_key == "compliance.truthful_declaration"
        assert match.resolved_value == "Da"

    def test_preferred_contact_method_maps_to_phone(self, synonyms):
        """preferred_contact_method should map to contacts.phone, not contacts.email."""
        entry = synonyms.get("preferred_contact_method")
        assert entry is not None
        assert entry.profile_key == "contacts.phone"


# ---------------------------------------------------------------------------
# Today-date word boundary detection (C4 fix)
# ---------------------------------------------------------------------------

class TestTodayDateWordBoundary:
    """Verifies word-boundary regex prevents false positives."""

    TODAY_PATTERNS = (
        r"\btoday\b", r"\bazi\b", r"\bastazi\b", r"\bсегодня\b",
        r"\bdata\s+completarii\b", r"\bдата\s+заполнения\b",
        r"\btoday'?s?\s+date\b", r"\bdata\s+de\s+azi\b",
    )

    @pytest.mark.parametrize("label", [
        "today's date",
        "data de azi",
        "data completarii formularului",
        "дата заполнения",
        "azi",
    ])
    def test_true_positives(self, label):
        """These labels SHOULD match today-date patterns."""
        assert any(re.search(p, label.lower()) for p in self.TODAY_PATTERNS)

    @pytest.mark.parametrize("label", [
        "Data cea mai tarzie de finalizare",
        "realizarea obiectivelor",
        "magazine",
        "Organization",
        "yesterday",
    ])
    def test_false_positives_prevented(self, label):
        """These labels should NOT match today-date patterns."""
        assert not any(re.search(p, label.lower()) for p in self.TODAY_PATTERNS)


# ---------------------------------------------------------------------------
# Signature fallback (H11 fix)
# ---------------------------------------------------------------------------

class TestSignatureFallback:
    """Verifies signature resolves to full_name, not 'JOHN DOE'."""

    def test_signature_uses_full_name(self, matcher):
        field = FormField(
            index=1,
            label="Digital Signature (Semnătura digitală)",
            field_type=FieldType.TEXT,
            required=True,
        )
        match = matcher.match_field(field)
        assert match.matched_key == "signature"
        # Signature should resolve to full_name from profile (not a hardcoded default)
        full_name = matcher.profile.personal.full_name
        first_name = matcher.profile.personal.first_name
        last_name = matcher.profile.personal.last_name
        expected = full_name or f"{first_name} {last_name}".strip()
        assert match.resolved_value == expected

    def test_signature_empty_when_no_name(self):
        """With a blank profile, signature should be empty (not 'JOHN DOE')."""
        from src.config import load_synonyms, Config
        profile = UserProfile()  # All fields empty
        synonyms = load_synonyms(Config.SYNONYMS_PATH)
        m = FieldMatcher(profile, synonyms)
        field = FormField(
            index=1,
            label="Digital Signature (Semnătura digitală)",
            field_type=FieldType.TEXT,
            required=True,
        )
        match = m.match_field(field)
        assert match.matched_key == "signature"
        # Empty profile should produce empty string, NOT "JOHN DOE"
        assert match.resolved_value == ""


# ---------------------------------------------------------------------------
# Motivation vs Experience UK Disambiguation
# ---------------------------------------------------------------------------

class TestMotivationVsExperienceUK:
    """Verifies that 'Why work in UK' motivation questions don't falsely match experience_uk ('Nu')."""

    def test_motivation_ro_maps_to_about_ro(self, matcher):
        field = FormField(
            index=1,
            label="De ce doriți să lucrați în Marea Britanie ca muncitor sezonier?",
            field_type=FieldType.TEXTAREA,
            required=False,
        )
        match = matcher.match_field(field)
        assert match.matched_key == "why_uk_ro"
        assert match.profile_key == "about.ro"
        assert match.resolved_value == matcher.profile.about.ro

    def test_motivation_ru_maps_to_about_ru(self, matcher):
        field = FormField(
            index=1,
            label="Почему вы хотите работать в Великобритании как сезонный работник?",
            field_type=FieldType.TEXTAREA,
            required=False,
        )
        match = matcher.match_field(field)
        assert match.matched_key == "why_uk_ru"
        assert match.profile_key == "about.ru"
        assert match.resolved_value == matcher.profile.about.ru

    def test_motivation_en_maps_to_about_en(self, matcher):
        field = FormField(
            index=1,
            label="Why do you want to work in the UK as a Seasonal Worker?",
            field_type=FieldType.TEXTAREA,
            required=False,
        )
        match = matcher.match_field(field)
        assert match.matched_key == "why_uk_en"
        assert match.profile_key == "about.en"
        assert match.resolved_value == matcher.profile.about.en

    def test_previous_uk_experience_still_matches(self, matcher):
        field = FormField(
            index=1,
            label="Ați mai lucrat în UK anterior?",
            field_type=FieldType.RADIO,
            required=True,
            options=["Da", "Nu"],
        )
        match = matcher.match_field(field)
        assert match.matched_key == "experience_uk"
        assert match.resolved_value == "Nu"
