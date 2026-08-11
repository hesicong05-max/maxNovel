"""Read-only compatibility API for historical outline data.

Automatic outline generation and public outline writes were retired in DEV-003D1.
The legacy GET remains so existing projects can keep using their saved chapter
arrangements while the second-stage chapter planner is developed.
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import User, get_current_user, get_project_for_owner
from app.core.legacy_json import read_legacy_object_list
from app.database import get_db
from app.models.project import Outline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/outline", tags=["outline"])


def _read_outline_list(outline: Outline, field_name: str) -> list[dict[str, Any]]:
    """Read a historical Outline JSON list without mutating stored data."""
    result = read_legacy_object_list(getattr(outline, field_name, None))
    if not result.valid:
        logger.warning(
            "Invalid legacy outline list project=%s field=%s category=%s",
            outline.project_id,
            field_name,
            result.error_category,
        )
    return result.items


@router.get("/{project_id}")
async def get_outline(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Return the owner's historical chapter arrangement as read-only data."""
    await get_project_for_owner(project_id, current_user, db)

    result = await db.execute(select(Outline).where(Outline.project_id == project_id))
    outline = result.scalar_one_or_none()
    if not outline:
        raise HTTPException(
            status_code=404,
            detail="未找到历史章节安排；新章节规划将在第二阶段开放",
        )

    return {
        "id": outline.id,
        "project_id": project_id,
        "story_arc": outline.story_arc,
        "chapters": _read_outline_list(outline, "chapters"),
        "reveal_plan": _read_outline_list(outline, "reveal_plan"),
        "created_at": outline.created_at.isoformat() if outline.created_at else None,
        "updated_at": outline.updated_at.isoformat() if outline.updated_at else None,
    }
