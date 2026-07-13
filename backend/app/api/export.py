"""Export API — export novel as txt/docx/markdown."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import User, get_current_user, get_project_for_owner
from app.database import get_db
from app.models.project import Chapter

router = APIRouter(prefix="/api/export", tags=["export"])


def _content_disposition(filename: str) -> str:
    """Build a Content-Disposition header with proper UTF-8 encoding."""
    encoded = quote(filename, safe="")
    return f"attachment; filename*=UTF-8''{encoded}"


@router.get("/{project_id}/txt")
async def export_txt(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)

    result = await db.execute(
        select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.chapter_num)
    )
    chapters = result.scalars().all()

    lines = [project.title, "=" * 40, ""]
    for ch in chapters:
        if ch.content:
            lines.append(f"\n{ch.title or f'第{ch.chapter_num}章'}\n")
            lines.append(ch.content)
            lines.append("\n" + "-" * 40 + "\n")

    content = "\n".join(lines)
    filename = f"{project.title}.txt"

    return StreamingResponse(
        iter([content.encode("utf-8")]),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.get("/{project_id}/markdown")
async def export_markdown(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)

    result = await db.execute(
        select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.chapter_num)
    )
    chapters = result.scalars().all()

    lines = [f"# {project.title}", ""]
    for ch in chapters:
        if ch.content:
            lines.append(f"\n## {ch.title or f'第{ch.chapter_num}章'}\n")
            lines.append(ch.content)
            lines.append("")

    content = "\n".join(lines)
    filename = f"{project.title}.md"

    return StreamingResponse(
        iter([content.encode("utf-8")]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(filename)},
    )
