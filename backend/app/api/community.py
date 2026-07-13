"""Community API — shared novels, tags, and co-creation settings."""

import random
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.community import CommunityNovel, CommunityTag
from app.models.project import Chapter, Project
from app.schemas.models import (
    CommunityNovelBrief,
    CommunityNovelCreate,
    CommunityNovelResponse,
    CommunityNovelUpdate,
    CommunityTagResponse,
)

router = APIRouter(prefix="/api/community", tags=["community"])


# ── Helpers ──────────────────────────────────────────────

async def _get_or_create_tag(db: AsyncSession, name: str) -> CommunityTag:
    """Find an existing tag by name (case-insensitive) or create a new one."""
    result = await db.execute(
        select(CommunityTag).where(func.lower(CommunityTag.name) == func.lower(name.strip()))
    )
    tag = result.scalar_one_or_none()
    if not tag:
        tag = CommunityTag(name=name.strip())
        db.add(tag)
        await db.flush()
    return tag


async def _sync_tags(db: AsyncSession, novel: CommunityNovel, tag_names: list[str], is_new: bool = False) -> None:
    """Replace the novel's tag set with the given list.

    Args:
        is_new: If True, the novel was just created and has no existing tags,
                so we skip the clear-old-tags step.
    """
    # Always eagerly load the tags relationship to avoid lazy-load in async context
    await db.refresh(novel, ["tags"])

    if not is_new:
        # Decrement usage_count for old tags
        for old_tag in list(novel.tags):
            old_tag.usage_count = max(0, (old_tag.usage_count or 0) - 1)
        novel.tags.clear()
        await db.flush()

    # Add new tags (deduplicate by name, case-insensitive)
    seen = set()
    for name in tag_names:
        name = name.strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        tag = await _get_or_create_tag(db, name)
        novel.tags.append(tag)
        tag.usage_count = (tag.usage_count or 0) + 1


async def _novel_to_brief(novel: CommunityNovel) -> CommunityNovelBrief:
    return CommunityNovelBrief(
        id=novel.id,
        title=novel.title,
        author_name=novel.author_name,
        genre=novel.genre,
        synopsis=novel.synopsis or "",
        allow_cocreation=novel.allow_cocreation,
        view_count=novel.view_count or 0,
        like_count=novel.like_count or 0,
        total_chapters=novel.total_chapters or 0,
        total_words=novel.total_words or 0,
        tags=[t.name for t in novel.tags],
        created_at=novel.created_at,
    )


async def _novel_to_response(novel: CommunityNovel) -> CommunityNovelResponse:
    return CommunityNovelResponse(
        id=novel.id,
        title=novel.title,
        author_name=novel.author_name,
        genre=novel.genre,
        synopsis=novel.synopsis or "",
        story_outline=novel.story_outline or "",
        chapter_notes=novel.chapter_notes or "",
        allow_cocreation=novel.allow_cocreation,
        view_count=novel.view_count or 0,
        like_count=novel.like_count or 0,
        total_chapters=novel.total_chapters or 0,
        total_words=novel.total_words or 0,
        tags=[t.name for t in novel.tags],
        project_id=novel.project_id,
        created_at=novel.created_at,
        updated_at=novel.updated_at,
    )


# ── Novel CRUD ───────────────────────────────────────────

@router.get("/novels", response_model=list[CommunityNovelBrief])
async def list_novels(
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: int = Query(0, ge=0),
    limit: int = Query(12, ge=1, le=50),
    tag: str | None = Query(None),
    sort: str = Query("latest", pattern="^(latest|popular|random)$"),
):
    """List community novels with pagination, optional tag filter, and sort order."""
    query = select(CommunityNovel).options(selectinload(CommunityNovel.tags))

    if tag:
        query = query.join(CommunityNovel.tags).where(
            func.lower(CommunityTag.name) == func.lower(tag)
        )

    if sort == "popular":
        query = query.order_by(CommunityNovel.like_count.desc(), CommunityNovel.view_count.desc())
    elif sort == "random":
        query = query.order_by(func.random())
    else:
        query = query.order_by(CommunityNovel.created_at.desc())

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    novels = result.scalars().unique().all()

    return [await _novel_to_brief(n) for n in novels]


@router.get("/novels/random", response_model=list[CommunityNovelBrief])
async def get_random_novels(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(6, ge=1, le=30),
    exclude: str | None = Query(None, description="Comma-separated novel IDs to exclude"),
):
    """Return random novels for infinite scroll refresh, excluding already-loaded IDs."""
    exclude_ids = set()
    if exclude:
        exclude_ids = {eid.strip() for eid in exclude.split(",") if eid.strip()}

    # Get all IDs first, then random sample
    id_query = select(CommunityNovel.id)
    if exclude_ids:
        id_query = id_query.where(~CommunityNovel.id.in_(exclude_ids))
    id_result = await db.execute(id_query)
    all_ids = [row[0] for row in id_result.all()]

    if not all_ids:
        return []

    sample_size = min(limit, len(all_ids))
    sampled_ids = random.sample(all_ids, sample_size)

    result = await db.execute(
        select(CommunityNovel)
        .options(selectinload(CommunityNovel.tags))
        .where(CommunityNovel.id.in_(sampled_ids))
    )
    novels = result.scalars().unique().all()

    # Shuffle to avoid always returning in the same DB order
    random.shuffle(novels)
    return [await _novel_to_brief(n) for n in novels]


@router.post("/novels", response_model=CommunityNovelResponse)
async def upload_novel(
    data: CommunityNovelCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Publish a novel to the community."""
    novel = CommunityNovel(
        title=data.title,
        author_name=data.author_name,
        genre=data.genre,
        project_id=data.project_id,
        synopsis=data.synopsis,
        story_outline=data.story_outline,
        chapter_notes=data.chapter_notes,
        allow_cocreation=data.allow_cocreation,
        total_chapters=data.total_chapters,
        total_words=data.total_words,
    )
    db.add(novel)
    await db.flush()

    # Attach tags
    if data.tags:
        await _sync_tags(db, novel, data.tags, is_new=True)

    await db.commit()
    await db.refresh(novel)

    # Reload with tags eagerly loaded
    result = await db.execute(
        select(CommunityNovel)
        .options(selectinload(CommunityNovel.tags))
        .where(CommunityNovel.id == novel.id)
    )
    novel = result.scalar_one()
    return await _novel_to_response(novel)


@router.get("/novels/{novel_id}", response_model=CommunityNovelResponse)
async def get_novel(
    novel_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a single novel's full details. Increments view count."""
    result = await db.execute(
        select(CommunityNovel)
        .options(selectinload(CommunityNovel.tags))
        .where(CommunityNovel.id == novel_id)
    )
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    novel.view_count = (novel.view_count or 0) + 1
    await db.commit()

    # Re-query with tags eagerly loaded (avoid lazy-load after commit)
    result = await db.execute(
        select(CommunityNovel)
        .options(selectinload(CommunityNovel.tags))
        .where(CommunityNovel.id == novel_id)
    )
    novel = result.scalar_one()
    return await _novel_to_response(novel)


@router.put("/novels/{novel_id}", response_model=CommunityNovelResponse)
async def update_novel(
    novel_id: str,
    data: CommunityNovelUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Edit a community novel's details."""
    result = await db.execute(
        select(CommunityNovel)
        .options(selectinload(CommunityNovel.tags))
        .where(CommunityNovel.id == novel_id)
    )
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    if data.title is not None:
        novel.title = data.title
    if data.author_name is not None:
        novel.author_name = data.author_name
    if data.genre is not None:
        novel.genre = data.genre
    if data.synopsis is not None:
        novel.synopsis = data.synopsis
    if data.story_outline is not None:
        novel.story_outline = data.story_outline
    if data.chapter_notes is not None:
        novel.chapter_notes = data.chapter_notes
    if data.allow_cocreation is not None:
        novel.allow_cocreation = data.allow_cocreation

    if data.tags is not None:
        await _sync_tags(db, novel, data.tags)

    await db.commit()
    await db.refresh(novel)

    # Reload with tags
    result = await db.execute(
        select(CommunityNovel)
        .options(selectinload(CommunityNovel.tags))
        .where(CommunityNovel.id == novel.id)
    )
    novel = result.scalar_one()
    return await _novel_to_response(novel)


@router.delete("/novels/{novel_id}")
async def delete_novel(
    novel_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Remove a novel from the community."""
    result = await db.execute(
        select(CommunityNovel)
        .options(selectinload(CommunityNovel.tags))
        .where(CommunityNovel.id == novel_id)
    )
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    # Decrement tag usage counts
    for tag in list(novel.tags):
        tag.usage_count = max(0, (tag.usage_count or 0) - 1)

    await db.delete(novel)
    await db.commit()
    return {"message": "小说已从社区移除"}


@router.post("/novels/{novel_id}/like", response_model=dict)
async def like_novel(
    novel_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Like a novel (increments like_count)."""
    result = await db.execute(select(CommunityNovel).where(CommunityNovel.id == novel_id))
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    novel.like_count = (novel.like_count or 0) + 1
    await db.commit()
    return {"like_count": novel.like_count}


# ── Tags ──────────────────────────────────────────────────

@router.get("/tags", response_model=list[CommunityTagResponse])
async def list_tags(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, ge=1, le=200),
):
    """List all tags sorted by usage count descending."""
    result = await db.execute(
        select(CommunityTag)
        .where(CommunityTag.usage_count > 0)
        .order_by(CommunityTag.usage_count.desc())
        .limit(limit)
    )
    return result.scalars().all()


# ── Helper: gather project stats for upload ───────────────

@router.get("/projects/{project_id}/stats")
async def get_project_stats(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get chapter count and total word count for a project, used during upload."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    ch_result = await db.execute(
        select(func.count(Chapter.id), func.coalesce(func.sum(Chapter.word_count), 0))
        .where(Chapter.project_id == project_id)
    )
    ch_count, total_words = ch_result.one()

    return {
        "title": project.title,
        "genre": project.genre.value if hasattr(project.genre, "value") else str(project.genre),
        "total_chapters": project.total_chapters,
        "chapter_count": ch_count,
        "total_words": total_words,
    }
