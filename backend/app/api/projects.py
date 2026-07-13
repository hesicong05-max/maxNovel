"""Project CRUD API."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.project import Chapter, Outline, Project, ProjectStatus, Worldview
from app.schemas.models import ProjectCreate, ProjectResponse

router = APIRouter(prefix="/api/projects", tags=["projects"])


async def _project_extras(db: AsyncSession, project_id: str) -> dict:
    """Query related counts directly to avoid lazy loading in async mode."""
    wv = await db.execute(select(Worldview.id).where(Worldview.project_id == project_id).limit(1))
    ol = await db.execute(select(Outline.id).where(Outline.project_id == project_id).limit(1))
    ch = await db.execute(select(func.count(Chapter.id)).where(Chapter.project_id == project_id))
    return {
        "has_worldview": wv.scalar_one_or_none() is not None,
        "has_outline": ol.scalar_one_or_none() is not None,
        "chapter_count": ch.scalar_one(),
    }


@router.get("", response_model=list[ProjectResponse])
async def list_projects(db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(Project).order_by(Project.updated_at.desc()))
    projects = result.scalars().all()

    responses = []
    for p in projects:
        resp = ProjectResponse.model_validate(p, from_attributes=True)
        extras = await _project_extras(db, p.id)
        resp.has_worldview = extras["has_worldview"]
        resp.has_outline = extras["has_outline"]
        resp.chapter_count = extras["chapter_count"]
        responses.append(resp)
    return responses


@router.post("", response_model=ProjectResponse)
async def create_project(data: ProjectCreate, db: Annotated[AsyncSession, Depends(get_db)]):
    project = Project(
        title=data.title,
        genre=data.genre,
        total_chapters=data.total_chapters,
        chapter_word_count=data.chapter_word_count,
        style_intensity=data.style_intensity,
        status=ProjectStatus.DRAFT,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    resp = ProjectResponse.model_validate(project, from_attributes=True)
    resp.has_worldview = False
    resp.has_outline = False
    resp.chapter_count = 0
    return resp


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    resp = ProjectResponse.model_validate(project, from_attributes=True)
    extras = await _project_extras(db, project.id)
    resp.has_worldview = extras["has_worldview"]
    resp.has_outline = extras["has_outline"]
    resp.chapter_count = extras["chapter_count"]
    return resp


@router.delete("/{project_id}")
async def delete_project(project_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    await db.delete(project)
    await db.commit()
    return {"message": "项目已删除"}


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    data: ProjectCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    project.title = data.title
    project.genre = data.genre
    project.total_chapters = data.total_chapters
    project.chapter_word_count = data.chapter_word_count
    project.style_intensity = data.style_intensity

    await db.commit()
    await db.refresh(project)

    resp = ProjectResponse.model_validate(project, from_attributes=True)
    extras = await _project_extras(db, project.id)
    resp.has_worldview = extras["has_worldview"]
    resp.has_outline = extras["has_outline"]
    resp.chapter_count = extras["chapter_count"]
    return resp
