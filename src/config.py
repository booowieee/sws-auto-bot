import os
from pathlib import Path
from typing import Dict, Optional
import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from src.logger import logger
from src.models import SynonymEntry, UserProfile

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


class Config:
    PROJECT_ROOT: Path = PROJECT_ROOT
    CONFIG_DIR: Path = PROJECT_ROOT / "config"
    DATA_DIR: Path = PROJECT_ROOT / "data"
    LOGS_DIR: Path = PROJECT_ROOT / "logs"
    SCREENSHOTS_DIR: Path = PROJECT_ROOT / "screenshots"

    PROFILE_PATH: Path = CONFIG_DIR / "profile.yaml"
    PROFILE_EXAMPLE_PATH: Path = CONFIG_DIR / "profile.example.yaml"
    SYNONYMS_PATH: Path = CONFIG_DIR / "synonyms.yaml"

    CHROME_PROFILE_DIR: Path = Path(
        os.getenv("CHROME_PERSISTENT_PROFILE_DIR", str(DATA_DIR / "chrome_profile"))
    )
    if not CHROME_PROFILE_DIR.is_absolute():
        CHROME_PROFILE_DIR = PROJECT_ROOT / CHROME_PROFILE_DIR

    TELEGRAM_BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID: Optional[str] = os.getenv("TELEGRAM_CHAT_ID")

    HEADLESS: bool = os.getenv("HEADLESS", "true").lower() in ("true", "1", "yes")
    ACTION_TIMEOUT_MS: int = int(os.getenv("ACTION_TIMEOUT_MS", "10000"))
    NAVIGATION_TIMEOUT_MS: int = int(os.getenv("NAVIGATION_TIMEOUT_MS", "30000"))
    FUZZY_THRESHOLD: float = float(os.getenv("FUZZY_THRESHOLD", "75.0"))
    MAX_PAGES: int = int(os.getenv("MAX_PAGES", "15"))
    DELAY_BETWEEN_FORMS: float = float(os.getenv("DELAY_BETWEEN_FORMS", "1.5"))

    VIEWPORT_WIDTH: int = 1920
    VIEWPORT_HEIGHT: int = 1080

    TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Bucharest")
    USER_AGENT: str = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36",
    )

    # LLM Fallback (Tier 2) Settings
    LLM_FALLBACK_ENABLED: bool = os.getenv("LLM_FALLBACK_ENABLED", "false").lower() in ("true", "1", "yes")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
    LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "4.0"))

    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "") or LLM_API_KEY
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2:3b")


def ensure_directories() -> None:
    Config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    Config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    Config.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    Config.CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def load_profile(path: Optional[Path] = None) -> UserProfile:
    target_path = path or Config.PROFILE_PATH

    if not target_path.exists():
        if Config.PROFILE_EXAMPLE_PATH.exists():
            logger.warning(
                f"Profile file not found at {target_path}. Falling back to example profile at {Config.PROFILE_EXAMPLE_PATH}"
            )
            target_path = Config.PROFILE_EXAMPLE_PATH
        else:
            raise FileNotFoundError(f"Neither profile.yaml nor profile.example.yaml found in {Config.CONFIG_DIR}")

    with open(target_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping in {target_path}, got {type(data).__name__}")

    try:
        return UserProfile(**data)
    except ValidationError as e:
        logger.error(f"Invalid profile schema in {target_path}: {e}")
        raise


def load_synonyms(path: Optional[Path] = None) -> Dict[str, SynonymEntry]:
    target_path = path or Config.SYNONYMS_PATH
    if not target_path.exists():
        raise FileNotFoundError(f"Synonyms file not found at {target_path}")

    with open(target_path, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f) or {}

    synonyms = {}
    for key, item in raw_data.items():
        if isinstance(item, dict):
            synonyms[key] = SynonymEntry(
                keywords=item.get("keywords") or [],
                patterns=item.get("patterns") or [],
                profile_key=item.get("profile_key") or "",
            )
    return synonyms
