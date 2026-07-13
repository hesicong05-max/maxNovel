"""LLM settings storage — persists user-configured API key and model info."""

import json
from pathlib import Path

from app.config import DATA_DIR

SETTINGS_FILE = DATA_DIR / "llm_settings.json"

DEFAULT_SETTINGS = {
    "api_key": "",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o",
    "temperature": 0.8,
    "max_tokens": 4096,
}


def load_settings() -> dict:
    """Load LLM settings from file, falling back to env-based defaults."""
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Merge with defaults to ensure all keys exist
                merged = {**DEFAULT_SETTINGS, **data}
                return merged
        except (json.JSONDecodeError, IOError):
            pass

    # Fall back to environment-based settings from config.py
    from app.config import settings as env_settings

    return {
        "api_key": env_settings.LLM_API_KEY,
        "base_url": env_settings.LLM_BASE_URL,
        "model": env_settings.LLM_MODEL,
        "temperature": env_settings.LLM_TEMPERATURE,
        "max_tokens": env_settings.LLM_MAX_TOKENS,
    }


def save_settings(data: dict) -> dict:
    """Save LLM settings to file."""
    merged = {**DEFAULT_SETTINGS, **data}
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return merged


def get_api_key() -> str:
    """Convenience: get the current API key."""
    return load_settings().get("api_key", "")
