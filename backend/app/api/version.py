"""Version endpoint for deployment verification."""

import os
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api/version", tags=["version"])


def _read_commit() -> str:
    """Read the deployed git commit hash written during Docker build."""
    commit_file = Path("/app/COMMIT")
    if commit_file.exists():
        return commit_file.read_text().strip() or "unknown"
    # Fallback for local development
    return os.environ.get("GIT_COMMIT", "unknown")


@router.get("")
async def get_version() -> dict[str, str]:
    """Return current application version and git commit."""
    return {
        "version": "0.1.0",
        "commit": _read_commit(),
    }
