"""LLM settings storage — persists user-configured API key and model info.

Security improvements:
- File permissions set to 0600 (owner read/write only)
- Environment variable LLM_API_KEY takes priority over file
- GET endpoint never returns the full key (handled in api/settings.py)
"""

import json
import logging
import os
from pathlib import Path

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

SETTINGS_FILE = DATA_DIR / "llm_settings.json"

DEFAULT_SETTINGS = {
    "api_key": "",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o",
    "temperature": 0.8,
    "max_tokens": 4096,
}


def _set_file_permissions(path: Path) -> None:
    """Set file permissions to owner read/write only (0600)."""
    try:
        os.chmod(path, 0o600)
    except OSError as e:
        logger.warning("Failed to set file permissions for %s: %s", path, e)


def load_settings() -> dict:
    """Load LLM settings.

    Priority: environment variable > file > defaults.
    """
    # Check environment variable first (highest priority)
    env_api_key = os.getenv("LLM_API_KEY", "")
    env_base_url = os.getenv("LLM_BASE_URL", "")
    env_model = os.getenv("LLM_MODEL", "")

    if env_api_key:
        logger.debug("Using LLM settings from environment variables")
        return {
            "api_key": env_api_key,
            "base_url": env_base_url or DEFAULT_SETTINGS["base_url"],
            "model": env_model or DEFAULT_SETTINGS["model"],
            "temperature": DEFAULT_SETTINGS["temperature"],
            "max_tokens": DEFAULT_SETTINGS["max_tokens"],
        }

    # Fall back to file
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                merged = {**DEFAULT_SETTINGS, **data}
                return merged
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Failed to load settings file: %s", e)

    # Final fallback to defaults (env-based from config.py)
    from app.config import settings as env_settings

    return {
        "api_key": env_settings.LLM_API_KEY,
        "base_url": env_settings.LLM_BASE_URL,
        "model": env_settings.LLM_MODEL,
        "temperature": env_settings.LLM_TEMPERATURE,
        "max_tokens": env_settings.LLM_MAX_TOKENS,
    }


def save_settings(data: dict) -> dict:
    """Save LLM settings to file with restricted permissions."""
    merged = {**DEFAULT_SETTINGS, **data}

    # Write file
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    # Set file permissions to owner-only
    _set_file_permissions(SETTINGS_FILE)

    logger.info("LLM settings saved to %s", SETTINGS_FILE)
    return merged


def get_api_key() -> str:
    """Convenience: get the current API key."""
    return load_settings().get("api_key", "")
