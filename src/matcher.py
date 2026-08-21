import re
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


class FieldMatcher:
    """Matches Google Form fields against user profile using multi-tier heuristics."""

    def __init__(self, profile: UserProfile, synonyms: Dict[str, SynonymEntry]):
        self.profile = profile
        self.synonyms = synonyms
        self.fuzzy_threshold = Config.FUZZY_THRESHOLD

    def match_field(self, field: FormField) -> FieldMatch:
        """Determines which profile attribute corresponds to a given form field."""
        # Normalize labels: remove HTML tags, brackets, underscores, and multiple spaces
        clean_text = re.sub(r"<[^>]+>", " ", field.label.lower())
        clean_text = re.sub(r"\[[^\]]+\]", " ", clean_text)
        clean_text = re.sub(r"[_\-:\*]+", " ", clean_text)
        label_clean = re.sub(r"\s+", " ", clean_text).strip()

        # Step 0: Emergency contact & Next of Kin prioritization (prevents matching to candidate full_name)
        if any(w in label_clean for w in ("emergency", "kin", "urgen", "контактн", "чс")):
            if "emergency_relationship" in self.synonyms and self._check_regex_patterns(
                label_clean, self.synonyms["emergency_relationship"].patterns
            ):
                return self._create_match(field, "emergency_relationship", MatchMethod.REGEX_PATTERN, 100.0)
            if "emergency_phone" in self.synonyms and self._check_regex_patterns(
                label_clean, self.synonyms["emergency_phone"].patterns
            ):
                return self._create_match(field, "emergency_phone", MatchMethod.REGEX_PATTERN, 100.0)
            if "emergency_name" in self.synonyms and self._check_regex_patterns(
                label_clean, self.synonyms["emergency_name"].patterns
            ):
                return self._create_match(field, "emergency_name", MatchMethod.REGEX_PATTERN, 100.0)

        # Step 1: Candidate Compound name prioritization
        if "full_name" in self.synonyms:
            if self._check_regex_patterns(label_clean, self.synonyms["full_name"].patterns):
                return self._create_match(field, "full_name", MatchMethod.REGEX_PATTERN, 100.0)

        # Step 2: Regex patterns across all synonym entries
        for syn_key, syn_entry in self.synonyms.items():
            if self._check_regex_patterns(label_clean, syn_entry.patterns):
                return self._create_match(field, syn_key, MatchMethod.REGEX_PATTERN, 95.0)

        # Step 3: Exact keywords and word boundary checks
        best_keyword_match: Optional[str] = None
        max_keyword_len = 0

        for syn_key, syn_entry in self.synonyms.items():
            for kw in syn_entry.keywords:
                kw_lower = kw.lower().strip()
                # Check for whole word match
                if re.search(r"\b" + re.escape(kw_lower) + r"\b", label_clean):
                    if len(kw_lower) > max_keyword_len:
                        max_keyword_len = len(kw_lower)
                        best_keyword_match = syn_key

        if best_keyword_match:
            return self._create_match(field, best_keyword_match, MatchMethod.EXACT_KEYWORD, 90.0)

        # Step 4: Fuzzy matching with RapidFuzz
        best_fuzzy_key: Optional[str] = None
        highest_score = 0.0

        for syn_key, syn_entry in self.synonyms.items():
            for kw in syn_entry.keywords:
                score = fuzz.token_set_ratio(label_clean, kw.lower())
                if score > highest_score and score >= self.fuzzy_threshold:
                    highest_score = score
                    best_fuzzy_key = syn_key

        if best_fuzzy_key:
            return self._create_match(field, best_fuzzy_key, MatchMethod.FUZZY, float(highest_score))

        # Step 5: Unmatched field
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

        # Smart fallback to short text version if field is a single-line text input
        if syn_key == "about" and field.field_type == FieldType.TEXT:
            short_val = self._resolve_profile_value("about.short_ro")
            raw_val = short_val if short_val else self._resolve_profile_value(profile_key)
        else:
            raw_val = self._resolve_profile_value(profile_key)

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
                return ""

        return current

    @staticmethod
    def _resolve_best_option(target_val: str, options: List[str]) -> Optional[str]:
        if not options:
            return None

        target_clean = target_val.lower().strip()

        # Handle numeric scale options (e.g. 1 to 5)
        if all(opt.isdigit() for opt in options):
            if target_clean in ("incepator", "basic", "a1", "a2", "beginner", "slaba"):
                return next((opt for opt in ("2", "3", "1") if opt in options), options[0])
            elif target_clean in ("da", "yes", "avansat", "fluent", "excelenta", "5", "4"):
                return next((opt for opt in ("5", "4", "3") if opt in options), options[-1])
            return options[-1]

        # Semantic choice synonym mappings (RO, EN, RU parity)
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
        }

        # 1. Exact match
        for opt in options:
            if opt.lower().strip() == target_clean:
                return opt

        # 2. Semantic synonym group match
        for key, syns in SEMANTIC_OPTION_MAP.items():
            if target_clean in syns:
                for opt in options:
                    opt_lower = opt.lower().strip()
                    if any(s in opt_lower for s in syns):
                        return opt

        # 3. Substring / contained match
        for opt in options:
            opt_lower = opt.lower().strip()
            if target_clean in opt_lower or opt_lower in target_clean:
                return opt

        # 4. Fuzzy match
        best_opt = None
        max_score = 0.0
        for opt in options:
            score = fuzz.token_set_ratio(target_clean, opt.lower())
            if score > max_score and score >= 60.0:
                max_score = score
                best_opt = opt

        return best_opt
