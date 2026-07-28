import os
import warnings
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App
    APP_NAME: str = "小说世界观续写 Agent"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/novel_agent.db"

    # LLM API — default to DeepSeek (OpenAI-compatible endpoint)
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.deepseek.com/v1"
    LLM_MODEL: str = "deepseek-chat"
    LLM_MAX_TOKENS: int = 4096
    LLM_TEMPERATURE: float = 0.8

    # CORS — comma-separated origins (stored as string, parsed via property)
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173"

    # File upload
    MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024  # 10MB

    # Rate limiting
    RATE_LIMIT_DEFAULT: str = "60/minute"
    RATE_LIMIT_LLM: str = "10/minute"
    RATE_LIMIT_STORAGE_URI: str = "memory://"

    # Auth / JWT
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_DAYS: int = 7

    # Sentry — error monitoring (empty = disabled)
    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1
    SENTRY_SEND_PII: bool = False

    # Logging
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS_ORIGINS string into a list."""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def validate_security(self) -> None:
        """Validate security-critical settings at startup."""
        if not self.JWT_SECRET:
            if not self.DEBUG:
                raise RuntimeError(
                    "JWT_SECRET must be set in production! "
                    "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
                )
            warnings.warn(
                "JWT_SECRET not set — using insecure default for development only. "
                "DO NOT use in production!",
                RuntimeWarning,
                stacklevel=2,
            )
            self.JWT_SECRET = "dev-only-insecure-secret-change-in-production"


settings = Settings()
settings.validate_security()

# Ensure data directory exists
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
