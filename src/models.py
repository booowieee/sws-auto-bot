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


class DocumentsInfo(BaseModel):
    passport_number: str = ""
    passport_issue_date: str = ""
    passport_expiry: str = ""
    passport_issuing_authority: str = ""
    id_card_number: str = ""


class EmergencyContact(BaseModel):
    name: str = ""
    relationship: str = ""
    phone: str = ""


class ContactsInfo(BaseModel):
    phone: str = ""
    whatsapp: str = ""
    email: str = ""
    address_street: str = ""
    address_city: str = ""
    address_region: str = ""
    address_country: str = ""
    postal_code: str = ""
    address_full: str = ""
    emergency_contact: EmergencyContact = Field(default_factory=EmergencyContact)


class WorkInfo(BaseModel):
    experience_agriculture: bool = False
    experience_agriculture_text: str = ""
    experience_crops: str = ""
    experience_uk: bool = False
    experience_uk_text: str = ""
    previous_employer: str = ""
    driving_license: str = ""
    physical_fitness: str = ""
    caravan_acceptance: str = "Da"
    available_from: str = ""
    duration_stay: str = ""
    apply_alone: bool = True
    apply_alone_text: str = ""
    preferred_location: str = ""
    english_level: str = ""
    visa_refusal: str = "Nu"
    criminal_record: str = "Nu"


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
