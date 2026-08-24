import re
import unicodedata


def strip_diacritics(text: str) -> str:
    """
    Universally removes diacritical marks and accents from any language
    (Romanian ă/â/î/ș/ț, Russian ё/е, Polish ą/ć/ę/ł/ń/ó/ś/ź/ż, French é/è/ê/ë, German ä/ö/ü, etc.).
    """
    if not text:
        return ""

    # Replace Russian ё/Ё and Romanian cedilla variants (U+015F/U+0163) before Unicode decomposition
    t = (
        text.replace("ё", "е")
        .replace("Ё", "Е")
        .replace("ş", "s")
        .replace("Ş", "S")
        .replace("ţ", "t")
        .replace("Ţ", "T")
    )
    # Decompose Unicode characters and filter out combining diacritical marks
    return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))


def normalize_text(text: str) -> str:
    """
    Cleans and normalizes text: strips HTML, punctuation, excessive whitespace,
    and removes all diacritics for robust matching.
    """
    if not text:
        return ""

    # Strip HTML tags
    clean = re.sub(r"<[^>]+>", " ", text.lower())
    # Strip bracketed metadata
    clean = re.sub(r"\[[^\]]+\]", " ", clean)
    # Strip negative parenthetical guidelines (e.g. "(do not include surname)")
    clean = re.sub(
        r"\((do\s*not|nu\s*include[tț]i|не\s*указывайте|без)[^\)]*\)", " ", clean
    )
    # Replace special delimiters with space
    clean = re.sub(r"[_\-:\*\.,\(\)\/\\]+", " ", clean)
    # Collapse multiple whitespaces
    clean = re.sub(r"\s+", " ", clean).strip()

    return strip_diacritics(clean)
