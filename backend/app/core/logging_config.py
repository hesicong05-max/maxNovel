"""Centralized logging configuration.

Call `setup_logging()` once at startup (e.g. in lifespan or main module).
All other modules just do `logger = logging.getLogger(__name__)`.
"""

import logging
import sys
from datetime import datetime, timezone

from app.config import settings


class UTCFormatter(logging.Formatter):
    """Formatter with UTC timestamp and structured-ish output."""

    def format(self, record: logging.LogRecord) -> str:
        # Add UTC timestamp
        record.utc_time = datetime.now(timezone.utc).isoformat()
        return super().format(record)


# Color codes for console output
_COLORS = {
    "DEBUG": "\033[36m",    # cyan
    "INFO": "\033[32m",     # green
    "WARNING": "\033[33m",  # yellow
    "ERROR": "\033[31m",    # red
    "CRITICAL": "\033[35m", # magenta
}
_RESET = "\033[0m"


class ColorFormatter(logging.Formatter):
    """Console formatter with color-coded log levels."""

    def format(self, record: logging.LogRecord) -> str:
        color = _COLORS.get(record.levelname, "")
        record.levelname_colored = f"{color}{record.levelname}{_RESET}" if color else record.levelname
        record.utc_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return super().format(record)


def setup_logging() -> None:
    """Configure root logging with console handler.

    Call this once at application startup.
    """
    level_str = getattr(settings, "LOG_LEVEL", "INFO")
    # Handle both settings attribute and env var
    if not level_str or level_str == "NOT_SET":
        level_str = "INFO"
    level = getattr(logging, level_str.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers (uvicorn adds its own, but we want ours first)
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    # Console handler with color
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(
        ColorFormatter(
            fmt="%(utc_time)s %(levelname_colored)s [%(name)s] %(message)s",
        )
    )
    root.addHandler(console_handler)

    # Reduce noise from third-party libraries
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Log startup info
    logger = logging.getLogger(__name__)
    logger.info(
        "Logging initialized — level=%s, debug=%s",
        level_str.upper(),
        settings.DEBUG,
    )
