from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FieldType(str, Enum):
    TEXT = "text"
    TEXTAREA = "textarea"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    DROPDOWN = "dropdown"
    DATE = "date"
    FILE_UPLOAD = "file_upload"
    UNKNOWN = "unknown"


class FormField(BaseModel):
    index: int
    label: str
    field_type: FieldType
    entry_id: Optional[str] = None
    required: bool = False
    options: List[str] = Field(default_factory=list)
    element_id: Optional[str] = None
    raw_html: Optional[str] = None


class MatchMethod(str, Enum):
    EXACT_KEYWORD = "exact_keyword"
    REGEX_PATTERN = "regex_pattern"
    FUZZY = "fuzzy"
    FALLBACK = "fallback"
    UNMATCHED = "unmatched"


class FieldMatch(BaseModel):
    field: FormField
    matched_key: Optional[str] = None
    profile_key: Optional[str] = None
    resolved_value: Optional[Any] = None
    selected_option: Optional[str] = None
    method: MatchMethod = MatchMethod.UNMATCHED
    confidence: float = 0.0


class PersonalInfo(BaseModel):
    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    patronymic: str = ""
    date_of_birth: str = ""
    date_of_birth_parts: Dict[str, str] = Field(default_factory=dict)
    place_of_birth: str = ""
    place_of_birth_country: str = ""
    sex: str = ""
    nationality: str = ""
    marital_status: str = ""
    city: str = ""
    country: str = ""
    height: str = ""
    weight: str = ""
    tattoos_marks: str = ""
    age: str = ""
    age_confirmation: str = ""
    languages_spoken: str = ""
    today_date: str = ""
    signature: str = ""


class DocumentsInfo(BaseModel):
    passport_number: str = ""
    passport_issue_date: str = ""
    passport_expiry: str = ""
    passport_issuing_authority: str = ""
    id_card_number: str = ""
    brp_number: str = ""
    nino: str = ""
    has_nino: str = "Nu"
    cos_reference: str = ""
    share_code: str = ""
    tb_certificate: str = ""


class EmergencyContact(BaseModel):
    name: str = ""
    relationship: str = ""
    phone: str = ""
    email: str = ""


class ContactsInfo(BaseModel):
    phone: str = ""
    whatsapp: str = ""
    viber: str = ""
    telegram_contact: str = ""
    social_media: str = ""
    email: str = ""
    address_street: str = ""
    address_city: str = ""
    address_region: str = ""
    address_country: str = ""
    postal_code: str = ""
    address_full: str = ""
    emergency_contact: EmergencyContact = Field(default_factory=EmergencyContact)


class HealthInfo(BaseModel):
    medical_conditions: str = ""
    allergies: str = ""
    dietary_requirements: str = ""
    blood_type: str = ""
    covid_vaccination: str = ""
    smoking: str = ""
    swimming: str = ""


class PPEInfo(BaseModel):
    shoe_size: str = "42"
    tshirt_size: str = "L"
    trouser_size: str = "M"
    glove_size: str = "M"


class WorkInfo(BaseModel):
    experience_agriculture: bool = False
    experience_agriculture_text: str = ""
    experience_crops: str = ""
    experience_uk: bool = False
    experience_uk_text: str = ""
    previous_employer: str = ""
    previous_uk_employer: str = ""
    previous_sws_operator: str = ""
    driving_license: str = ""
    tractor_license: str = ""
    forklift_cert: str = ""
    first_aid_cert: str = ""
    food_hygiene_cert: str = ""
    heights_training: str = ""
    physical_fitness: str = ""
    caravan_acceptance: str = ""
    available_from: str = ""
    duration_stay: str = ""
    apply_alone: bool = True
    apply_alone_text: str = ""
    applying_with: str = ""
    preferred_location: str = ""
    english_level: str = ""
    visa_refusal: str = "Nu"
    valid_uk_visa: str = "Nu"
    deportation_history: str = "Nu"
    criminal_record: str = "Nu"
    weekend_availability: str = ""
    salary_expectations: str = ""
    references: str = ""
    travel_history: str = ""
    driving_license_categories: str = "Category B (Car)"
    can_ride_bicycle: str = "Da"
    can_swim: str = "Da"
    willing_overtime: str = "Da"
    overtime_acceptance: str = "Da"


class LogisticsInfo(BaseModel):
    preferred_airport: str = ""
    accommodation_pref: str = ""
    room_sharing: str = "Da"
    return_commitment: str = ""
    bank_iban: str = ""
    bank_swift: str = ""
    bank_account_name: str = ""
    has_uk_bank_account: str = "Nu"


class ComplianceInfo(BaseModel):
    truthful_declaration: str = "Da"
    gdpr_consent: str = "Da"
    consent_contact: str = "Da"
    terms_agreement: str = "Da"
    false_info_warning: str = "Da"
    signature: str = ""


class AboutInfo(BaseModel):
    ro: str = ""
    en: str = ""
    ru: str = ""
    short_ro: str = ""
    short_en: str = ""
    short_ru: str = ""


class UserProfile(BaseModel):
    personal: PersonalInfo = Field(default_factory=PersonalInfo)
    documents: DocumentsInfo = Field(default_factory=DocumentsInfo)
    contacts: ContactsInfo = Field(default_factory=ContactsInfo)
    work: WorkInfo = Field(default_factory=WorkInfo)
    health: HealthInfo = Field(default_factory=HealthInfo)
    ppe: PPEInfo = Field(default_factory=PPEInfo)
    logistics: LogisticsInfo = Field(default_factory=LogisticsInfo)
    compliance: ComplianceInfo = Field(default_factory=ComplianceInfo)
    about: AboutInfo = Field(default_factory=AboutInfo)


class SynonymEntry(BaseModel):
    keywords: List[str] = Field(default_factory=list)
    patterns: List[str] = Field(default_factory=list)
    profile_key: str


class FormStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CLOSED = "closed"
    DRY_RUN = "dry_run"


class ExecutionReport(BaseModel):
    url: str
    timestamp: str
    status: FormStatus
    duration_sec: float = 0.0
    total_fields: int = 0
    filled_fields: List[Dict[str, Any]] = Field(default_factory=list)
    unmatched_required_fields: List[str] = Field(default_factory=list)
    error_message: Optional[str] = None
    screenshots: List[str] = Field(default_factory=list)
    is_submitted: bool = False
