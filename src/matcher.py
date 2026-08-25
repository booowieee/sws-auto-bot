import re
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional, Tuple
from rapidfuzz import fuzz

from src.config import Config
from src.logger import logger
from src.models import (
    FieldMatch,
    FieldType,
    FormField,
    MatchMethod,
    SynonymEntry,
    UserProfile,
)
from src.text_utils import normalize_text, strip_diacritics

# Confidence scores for each matching tier
CONFIDENCE_PRIORITY = 100.0
CONFIDENCE_REGEX = 95.0
CONFIDENCE_KEYWORD = 90.0
CONFIDENCE_FUZZY_MIN = 60.0

# Semantic choice mappings for radio/dropdown/checkbox option resolution (RO, EN, RU)
SEMANTIC_OPTION_MAP = {
    "masculin": ["masculin", "male", "bărbat", "barbat", "мужской", "мужчина", "m", "man", "парень", "муж."],
    "feminin": ["feminin", "female", "femeie", "женский", "женщина", "f", "woman", "девушка", "жен."],
    "da": [
        "da", "yes", "true", "daa", "да", "am", "prezintă", "prezinta", "accept", "confirm",
        "valabil", "oricare", "any", "согласен", "принимаю", "подтверждаю", "имею", "есть"
    ],
    "nu": [
        "nu", "no", "false", "nuu", "нет", "lipsă", "lipsa", "niciodată", "niciodata",
        "fără", "fara", "не имею", "нет опыта", "не был", "отсутствует", "чисто"
    ],
    "singur": ["singur", "alone", "solo", "individual", "de unul singur", "один", "одиночный", "индивидуально", "сам"],
    "cuplu": ["cuplu", "couple", "în doi", "in doi", "însoțit", "insotit", "пара", "в паре", "вдвоем", "с супругом", "с супругой"],
    "necasatorit": ["necasatorit", "single", "холост", "не замужем", "не состоял", "celibatar", "unmarried"],
    "casatorit": ["casatorit", "married", "женат", "замужем", "в браке"],
    "incepator": ["incepator", "beginner", "basic", "a1", "a2", "elementary", "nu vorbesc", "базовый", "начальный", "не говорю", "слабый"],
    "mediu": ["mediu", "intermediate", "b1", "b2", "conversational", "средний", "разговорный"],
    "avansat": ["avansat", "advanced", "c1", "c2", "fluent", "свободный", "высокий"],
    "imediat": ["imediat", "immediately", "urgent", "oricand", "anytime", "asap", "сейчас", "немедленно", "в любое время", "готов к выезду"],
    "moldoveneasca": ["moldoveneasca", "moldova", "republica moldova", "moldovean", "chisinau", "молдова", "молдавское", "молдаванин"],
    "romana": ["romana", "romania", "roman", "румыния", "румынское", "румын"],
    "ucraineana": ["ucraineana", "ucraina", "ucrainean", "украина", "украинское", "украинец"],
    "oricare": ["oricare", "any location", "any", "любая", "любой регион", "все регионы", "kent", "herefordshire", "scotland"],
    "permis": ["da", "yes", "permis", "category b", "categoria b", "водительские права", "есть права", "категория б", "права"],
    "email": ["email", "e-mail", "e-mailul", "posta", "почта", "электронная почта", "mail"],
    "telefon": ["telefon", "phone", "apel", "телефон", "звонок", "mobile", "sms", "whatsapp"],
    "whatsapp": ["whatsapp", "wapp", "ватсап", "what's app"],
    "telegram": ["telegram", "телеграм", "tg", "тг"],
}


class FieldMatcher:
    """Matches Google Form fields against user profile data."""

    # Synonym keys that get priority routing before the general matching loop
    PRIORITY_KEYS = (
        "consent_contact",
        "gdpr_consent",
        "truthful_declaration",
        "terms_agreement",
        "false_info_warning",
        "today_date",
        "signature",
        "emergency_email",
        "emergency_relationship",
        "emergency_phone",
        "emergency_name",
        "passport_issuing_authority",
        "passport_expiry",
        "passport_issue_date",
        "passport_number",
        "valid_uk_visa",
        "visa_refusal",
        "deportation",
        "criminal_record",
        "no_recruitment_fees",
        "caravan_acceptance",
        "room_sharing",
        "medical_conditions",
        "dietary_requirements",
        "shoe_size",
        "glove_size",
        "email",
        "phone",
        "preferred_contact_method",
        "idnp",
        "id_card_number",
        "address_full",
    )

    def __init__(self, profile: UserProfile, synonyms: Dict[str, SynonymEntry]):
        self.profile = profile
        self.synonyms = synonyms
        self.fuzzy_threshold = Config.FUZZY_THRESHOLD
        # Precompile keyword boundary patterns (both original and diacritic-stripped)
        self._compiled_keywords: Dict[str, List[Tuple[re.Pattern, str]]] = {}
        for syn_key, syn_entry in synonyms.items():
            compiled = []
            for kw in syn_entry.keywords:
                kw_clean = kw.lower().strip()
                kw_nd = strip_diacritics(kw_clean)
                for variant in set([kw_clean, kw_nd]):
                    try:
                        pat = re.compile(r"\b" + re.escape(variant) + r"\b")
                        compiled.append((pat, variant))
                    except re.error:
                        continue
            self._compiled_keywords[syn_key] = compiled

    @staticmethod
    def _is_type_compatible(syn_key: str, field_type: FieldType) -> bool:
        """Guards against matching raw text keys (email, phone) to radio/checkbox/dropdown questions."""
        if field_type in (FieldType.RADIO, FieldType.CHECKBOX, FieldType.DROPDOWN):
            if syn_key in (
                "email",
                "phone",
                "whatsapp",
                "viber",
                "first_name",
                "last_name",
                "patronymic",
                "full_name",
                "address_full",
                "address_street",
                "postal_code",
                "idnp",
                "passport_number",
                "id_card_number",
                "emergency_phone",
                "emergency_email",
                "emergency_name",
                "telegram_contact",
                "social_media",
                "date_of_birth",
                "passport_issue_date",
                "passport_expiry",
            ):
                return False
        return True

    def match_field(self, field: FormField) -> FieldMatch:
        """Matches a form field label to a profile attribute with universal diacritic tolerance."""
        # Normalize text: strip HTML, brackets, negative guidelines, delimiters
        label_clean = normalize_text(field.label)
        raw_clean = re.sub(r"\s+", " ", re.sub(r"[_\-:\*\.,\(\)\/\\]+", " ", field.label.lower())).strip()

        # Step 0: Priority routing for emergency/kin/compliance fields
        for pkey in self.PRIORITY_KEYS:
            if pkey in self.synonyms and self._is_type_compatible(pkey, field.field_type):
                entry = self.synonyms[pkey]
                if self._check_regex_patterns(label_clean, entry.patterns) or self._check_regex_patterns(raw_clean, entry.patterns):
                    return self._create_match(field, pkey, MatchMethod.REGEX_PATTERN, CONFIDENCE_PRIORITY)

        # Step 1: Compound name prioritization
        if "full_name" in self.synonyms and self._is_type_compatible("full_name", field.field_type):
            if self._check_regex_patterns(label_clean, self.synonyms["full_name"].patterns) or self._check_regex_patterns(raw_clean, self.synonyms["full_name"].patterns):
                return self._create_match(field, "full_name", MatchMethod.REGEX_PATTERN, CONFIDENCE_PRIORITY)

        # Step 2: Regex patterns across all synonym entries
        for syn_key, syn_entry in self.synonyms.items():
            if not self._is_type_compatible(syn_key, field.field_type):
                continue
            if self._check_regex_patterns(label_clean, syn_entry.patterns) or self._check_regex_patterns(raw_clean, syn_entry.patterns):
                return self._create_match(field, syn_key, MatchMethod.REGEX_PATTERN, CONFIDENCE_REGEX)

        # Step 3: Exact keyword boundary matching
        best_keyword_match: Optional[str] = None
        max_keyword_len = 0

        for syn_key, compiled_list in self._compiled_keywords.items():
            if not self._is_type_compatible(syn_key, field.field_type):
                continue
            for pat, variant in compiled_list:
                if pat.search(label_clean) or pat.search(raw_clean):
                    if len(variant) > max_keyword_len:
                        max_keyword_len = len(variant)
                        best_keyword_match = syn_key

        if best_keyword_match:
            return self._create_match(field, best_keyword_match, MatchMethod.EXACT_KEYWORD, CONFIDENCE_KEYWORD)

        # Step 4: Fuzzy matching with RapidFuzz (both diacritic-stripped)
        best_fuzzy_key: Optional[str] = None
        highest_score = 0.0

        for syn_key, syn_entry in self.synonyms.items():
            if not self._is_type_compatible(syn_key, field.field_type):
                continue
            for kw in syn_entry.keywords:
                kw_nd = strip_diacritics(kw.lower().strip())
                score = max(
                    fuzz.token_set_ratio(label_clean, kw_nd),
                    fuzz.token_set_ratio(raw_clean, kw.lower().strip()),
                )
                if score > highest_score and score >= self.fuzzy_threshold:
                    highest_score = score
                    best_fuzzy_key = syn_key

        if best_fuzzy_key:
            return self._create_match(field, best_fuzzy_key, MatchMethod.FUZZY, float(highest_score))

        # Step 5: No match found
        logger.warning(f"Unmatched field: '{field.label}' (Type: {field.field_type.value}, Required: {field.required})")
        return FieldMatch(field=field, method=MatchMethod.UNMATCHED, confidence=0.0)

    def match_all(self, fields: List[FormField]) -> List[FieldMatch]:
        return [self.match_field(f) for f in fields]

    def _check_regex_patterns(self, text: str, patterns: List[str]) -> bool:
        for pat in patterns:
            try:
                if re.search(pat, text, re.IGNORECASE):
                    return True
            except re.error:
                continue
        return False

    def _create_match(
        self, field: FormField, syn_key: str, method: MatchMethod, confidence: float
    ) -> FieldMatch:
        syn_entry = self.synonyms.get(syn_key)
        profile_key = syn_entry.profile_key if syn_entry else ""

        # For single-line text fields mapped to "about", use the short version
        if syn_key == "about" and field.field_type == FieldType.TEXT:
            short_val = self._resolve_profile_value("about.short_ro")
            raw_val = short_val if short_val else self._resolve_profile_value(profile_key)
        else:
            raw_val = self._resolve_profile_value(profile_key)

        # Fallback for Radio questions mapped to keys with empty string values
        if field.field_type == FieldType.RADIO and not raw_val:
            if profile_key in ("documents.nino", "documents.has_nino", "logistics.bank_iban", "logistics.has_uk_bank_account") or syn_key in ("nino", "has_nino", "bank_details", "has_uk_bank_account"):
                raw_val = "Nu"

        selected_option = None
        if field.field_type in (FieldType.RADIO, FieldType.DROPDOWN, FieldType.CHECKBOX) and field.options:
            selected_option = self._resolve_best_option(str(raw_val), field.options)

        return FieldMatch(
            field=field,
            matched_key=syn_key,
            profile_key=profile_key,
            resolved_value=raw_val,
            selected_option=selected_option,
            method=method,
            confidence=confidence,
        )

    def _resolve_profile_value(self, profile_key: str) -> Any:
        if not profile_key:
            return ""

        parts = profile_key.split(".")
        current: Any = self.profile

        for part in parts:
            if hasattr(current, part):
                current = getattr(current, part)
            elif isinstance(current, dict) and part in current:
                current = current[part]
            else:
                current = ""
                break

        # Fallback 1: Age requested but empty in profile -> auto-compute from date_of_birth
        if profile_key == "personal.age" and not current:
            dob = self._resolve_profile_value("personal.date_of_birth")
            if dob:
                for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d"):
                    try:
                        dt = datetime.strptime(str(dob).strip(), fmt)
                        now = datetime.now(UTC)
                        years = now.year - dt.year - ((now.month, now.day) < (dt.month, dt.day))
                        return str(years)
                    except ValueError:
                        continue

        # Fallback 2: Date of birth parts (day, month, year) requested but empty -> extract from date_of_birth
        if profile_key.startswith("personal.date_of_birth_parts.") and not current:
            part_name = profile_key.split(".")[-1]
            dob = self._resolve_profile_value("personal.date_of_birth")
            if dob:
                for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d"):
                    try:
                        dt = datetime.strptime(str(dob).strip(), fmt)
                        if part_name == "day":
                            return f"{dt.day:02d}"
                        elif part_name == "month":
                            return f"{dt.month:02d}"
                        elif part_name == "year":
                            return str(dt.year)
                    except ValueError:
                        continue

        # Fallback 3: Full name requested but empty -> combine first_name + last_name
        if profile_key == "personal.full_name" and not current:
            first = self._resolve_profile_value("personal.first_name")
            last = self._resolve_profile_value("personal.last_name")
            if first or last:
                return f"{first} {last}".strip()

        # Fallback 4: First/Last name requested but empty -> split from full_name
        if profile_key == "personal.first_name" and not current:
            full = self._resolve_profile_value("personal.full_name")
            if full:
                return str(full).split()[0]
        if profile_key == "personal.last_name" and not current:
            full = self._resolve_profile_value("personal.full_name")
            if full and len(str(full).split()) > 1:
                return " ".join(str(full).split()[1:])

        if profile_key == "contacts.emergency_contact.email" and not current:
            return self._resolve_profile_value("contacts.email") or "user@example.com"
        if profile_key == "ppe.shoe_size" and not current:
            return "42"
        if profile_key == "ppe.glove_size" and not current:
            return "M"
        if profile_key == "ppe.tshirt_size" and not current:
            return "L"
        if profile_key == "ppe.trouser_size" and not current:
            return "M"
        if profile_key == "health.dietary_requirements" and not current:
            return "Nu"
        if profile_key == "work.caravan_acceptance" and not current:
            return "Da"
        if profile_key in ("logistics.has_uk_bank_account", "documents.has_nino") and not current:
            return "Nu"
        if profile_key == "personal.today_date":
            return datetime.now().strftime("%d/%m/%Y")
        if profile_key in ("personal.signature", "compliance.signature") and not current:
            return self._resolve_profile_value("personal.full_name") or ""
        if profile_key.startswith("compliance.") and not current:
            return "Da"

        return current

    @classmethod
    def _resolve_best_option(cls, target_val: str, options: List[str]) -> Optional[str]:
        if not options:
            return None

        target_clean = target_val.lower().strip()
        target_nodiacritics = strip_diacritics(target_clean)

        # 1. Exact match (with and without diacritics) - Highest Priority
        for opt in options:
            opt_clean = opt.lower().strip()
            if opt_clean == target_clean or strip_diacritics(opt_clean) == target_nodiacritics:
                return opt

        # 2. Smart choice for contact method (email vs phone vs whatsapp)
        if "@" in target_clean:
            for opt in options:
                opt_nd = strip_diacritics(opt.lower().strip())
                if any(s in opt_nd for s in ("email", "e-mail", "e mail", "posta", "mail")):
                    return opt
        if target_clean.startswith("+") or target_clean.replace(" ", "").replace("-", "").isdigit():
            for opt in options:
                opt_nd = strip_diacritics(opt.lower().strip())
                if any(s in opt_nd for s in ("telefon", "phone", "apel", "mobil", "sms")):
                    return opt

        # 3. Handle numeric scale options (e.g. 1 to 5, shoe sizes 40 to 45)
        if all(opt.strip().isdigit() for opt in options):
            if target_clean.isdigit():
                target_num = int(target_clean)
                best_opt = options[0]
                min_diff = abs(int(best_opt.strip()) - target_num)
                for opt in options[1:]:
                    diff = abs(int(opt.strip()) - target_num)
                    if diff < min_diff:
                        min_diff = diff
                        best_opt = opt
                return best_opt
            if target_clean in ("incepator", "basic", "a1", "a2", "beginner", "slaba"):
                return next((opt for opt in ("2", "3", "1") if opt in options), options[0])
            elif target_clean in ("da", "yes", "avansat", "fluent", "excelenta", "5", "4"):
                return next((opt for opt in ("5", "4", "3") if opt in options), options[-1])

        # 4. Semantic synonym group match
        for key, syns in SEMANTIC_OPTION_MAP.items():
            key_clean = key.lower().strip()
            syns_clean = [s.lower().strip() for s in syns]
            syns_nodiacritics = [strip_diacritics(s) for s in syns_clean]

            is_match = (
                target_clean in syns_clean 
                or target_nodiacritics in syns_nodiacritics 
                or target_clean == key_clean
                or (key_clean == "da" and (target_clean.startswith("da") or target_clean.startswith("yes") or target_clean.startswith("да")))
                or (key_clean == "nu" and (target_clean.startswith("nu") or target_clean.startswith("no") or target_clean.startswith("нет")))
            )

            if is_match:
                for opt in options:
                    opt_lower = opt.lower().strip()
                    opt_nodiacritics = strip_diacritics(opt_lower)
                    if any(s in opt_lower for s in syns_clean) or any(s in opt_nodiacritics for s in syns_nodiacritics):
                        return opt

        # 3. Substring / contained match
        for opt in options:
            opt_lower = opt.lower().strip()
            opt_nodiacritics = strip_diacritics(opt_lower)
            if len(target_clean) >= 3 and len(opt_lower) >= 3:
                if target_clean in opt_lower or opt_lower in target_clean:
                    return opt
                if target_nodiacritics in opt_nodiacritics or opt_nodiacritics in target_nodiacritics:
                    return opt

        # 4. Fuzzy match
        best_opt = None
        max_score = 0.0
        for opt in options:
            score = fuzz.token_set_ratio(target_nodiacritics, strip_diacritics(opt.lower()))
            if score > max_score and score >= CONFIDENCE_FUZZY_MIN:
                max_score = score
                best_opt = opt

        return best_opt
