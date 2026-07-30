"""Safe maintenance gate for writes that depend on legacy JSON fields."""

from typing import Any

from app.config import settings as app_settings

PROJECT_WRITE_FROZEN_CODE = "PROJECT_WRITE_FROZEN"
PROJECT_WRITE_FROZEN_MESSAGE = "项目资料正在升级，暂时无法保存，请稍后重试。"
PROJECT_WRITE_FROZEN_STATE = "write_frozen"


class ProjectWriteFrozenError(RuntimeError):
    """Raised before a protected project write while maintenance is active."""


def project_write_frozen_payload() -> dict[str, Any]:
    """Return the single public contract used by HTTP and SSE responses."""
    return {
        "detail": PROJECT_WRITE_FROZEN_MESSAGE,
        "code": PROJECT_WRITE_FROZEN_CODE,
        "maintenance_state": PROJECT_WRITE_FROZEN_STATE,
        "retryable": True,
        "retry_after_seconds": app_settings.LEGACY_JSON_MAINTENANCE_RETRY_AFTER,
        "event_id": app_settings.LEGACY_JSON_MAINTENANCE_EVENT_ID,
    }


def ensure_project_writes_available() -> None:
    """Fail closed before a protected legacy-JSON write."""
    if app_settings.LEGACY_JSON_WRITES_FROZEN:
        raise ProjectWriteFrozenError(PROJECT_WRITE_FROZEN_CODE)


def require_project_writes_available() -> None:
    """FastAPI dependency for protected project write routes."""
    ensure_project_writes_available()


def project_write_frozen_sse_event() -> dict[str, Any]:
    """Return the SSE form without exposing exception or persistence details."""
    return {
        "type": "error",
        "error": project_write_frozen_payload(),
    }
