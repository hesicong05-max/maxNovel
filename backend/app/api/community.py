"""Community API — shared novels, tags, and co-creation settings."""

import hashlib
import logging
import random
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import (
    User,
    get_current_user,
    get_optional_user,
    get_project_for_owner,
)
from app.core.rate_limiter import limiter
from app.database import get_db
from app.models.community import CommunityNovel, CommunityTag
from app.models.project import Chapter
from app.schemas.models import (
    CommunityNovelBrief,
    CommunityNovelCreate,
    CommunityNovelResponse,
    CommunityNovelUpdate,
    CommunityTagResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/community", tags=["community"])

# In-memory view dedup cache: {(ip_hash, novel_id): timestamp}
# TTL: 1 hour. For multi-process deployments, use Redis.
_VIEW_DEDUP_TTL = 3600  # 1 hour
_view_cache: dict[tuple[str, str], float] = {}


def _ip_hash(request: Request) -> str:
    """Hash the client IP for anonymous dedup."""
    client_ip = request.client.host if request.client else "unknown"
    return hashlib.sha256(client_ip.encode()).hexdigest()[:16]


def _should_count_view(ip_hash: str, novel_id: str) -> bool:
    """Check if this view should be counted (dedup within TTL)."""
    key = (ip_hash, novel_id)
    now = time.time()
    # Clean expired entries periodically
    if len(_view_cache) > 10000:
        _view_cache.clear()
    if key in _view_cache and now - _view_cache[key] < _VIEW_DEDUP_TTL:
        return False
    _view_cache[key] = now
    return True


# ── Helpers ──────────────────────────────────────────────


async def _get_or_create_tag(db: AsyncSession, name: str) -> CommunityTag:
    """Find an existing tag by name (case-insensitive) or create a new one."""
    result = await db.execute(
        select(CommunityTag).where(
            func.lower(CommunityTag.name) == func.lower(name.strip())
        )
    )
    tag = result.scalar_one_or_none()
    if not tag:
        tag = CommunityTag(name=name.strip())
        db.add(tag)
        await db.flush()
    return tag


async def _sync_tags(
    db: AsyncSession, novel: CommunityNovel, tag_names: list[str], is_new: bool = False
) -> None:
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


def _check_novel_ownership(novel: CommunityNovel, user: User) -> None:
    """Verify that the user owns the novel. Raises 403 if not."""
    if novel.owner_id is None or novel.owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权操作此小说")


# ── Novel CRUD ───────────────────────────────────────────


@router.get("/novels", response_model=list[CommunityNovelBrief])
async def list_novels(
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: int = Query(0, ge=0),
    limit: int = Query(12, ge=1, le=50),
    tag: str | None = Query(None, max_length=50),
    sort: str = Query("latest", pattern="^(latest|popular|random)$"),
):
    """List community novels with pagination, optional tag filter, and sort order. Public."""
    query = select(CommunityNovel).options(selectinload(CommunityNovel.tags))

    if tag:
        query = query.join(CommunityNovel.tags).where(
            func.lower(CommunityTag.name) == func.lower(tag)
        )

    if sort == "popular":
        query = query.order_by(
            CommunityNovel.like_count.desc(), CommunityNovel.view_count.desc()
        )
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
    exclude: str | None = Query(
        None,
        max_length=5000,
        description="Comma-separated novel IDs to exclude",
    ),
):
    """Return random novels for infinite scroll refresh. Public."""
    exclude_ids = set()
    if exclude:
        exclude_ids = {
            eid.strip()
            for eid in exclude.split(",")[:100]
            if 0 < len(eid.strip()) <= 32
        }

    # Sample directly in the database instead of loading every novel ID into
    # application memory as the community grows.
    query = select(CommunityNovel).options(selectinload(CommunityNovel.tags))
    if exclude_ids:
        query = query.where(~CommunityNovel.id.in_(exclude_ids))
    result = await db.execute(query.order_by(func.random()).limit(limit))
    novels = result.scalars().unique().all()

    # Shuffle to avoid always returning in the same DB order
    random.shuffle(novels)
    return [await _novel_to_brief(n) for n in novels]


@router.post("/novels", response_model=CommunityNovelResponse)
@limiter.limit("20/minute")
async def upload_novel(
    request: Request,
    data: CommunityNovelCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Publish a novel to the community. Requires authentication."""
    logger.info("Uploading novel: %s by %s", data.title, data.author_name)

    # If project_id is provided, verify ownership
    if data.project_id:
        await get_project_for_owner(data.project_id, current_user, db)

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
        owner_id=current_user.id,
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
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a single novel's full details. Increments view count (IP-deduped). Public."""
    result = await db.execute(
        select(CommunityNovel)
        .options(selectinload(CommunityNovel.tags))
        .where(CommunityNovel.id == novel_id)
    )
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    # Dedup view count by IP within a 1-hour window
    ip_h = _ip_hash(request)
    if _should_count_view(ip_h, novel_id):
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
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Edit a community novel's details. Requires authentication + ownership."""
    result = await db.execute(
        select(CommunityNovel)
        .options(selectinload(CommunityNovel.tags))
        .where(CommunityNovel.id == novel_id)
    )
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    _check_novel_ownership(novel, current_user)

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
@limiter.limit("20/minute")
async def delete_novel(
    request: Request,
    novel_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Remove a novel from the community. Requires authentication + ownership."""
    result = await db.execute(
        select(CommunityNovel)
        .options(selectinload(CommunityNovel.tags))
        .where(CommunityNovel.id == novel_id)
    )
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    _check_novel_ownership(novel, current_user)

    # Decrement tag usage counts
    for tag in list(novel.tags):
        tag.usage_count = max(0, (tag.usage_count or 0) - 1)

    await db.delete(novel)
    await db.commit()
    return {"message": "小说已从社区移除"}


@router.post("/novels/{novel_id}/like", response_model=dict)
@limiter.limit("30/minute")
async def like_novel(
    request: Request,
    novel_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User | None, Depends(get_optional_user)] = None,
):
    """Like a novel (increments like_count). Dedup by user or IP.

    - Authenticated users: one like per user per novel (stored in liked_by)
    - Anonymous: one like per IP per novel (IP hash stored in liked_by, 24h TTL via rate limit)
    """
    result = await db.execute(
        select(CommunityNovel).where(CommunityNovel.id == novel_id)
    )
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    # Determine liker identifier
    if current_user:
        liker_id = current_user.id
    else:
        liker_id = f"ip:{_ip_hash(request)}"

    # Check if already liked
    liked_by = novel.liked_by or []
    if liker_id in liked_by:
        # Already liked — return current count without incrementing
        return {"like_count": novel.like_count, "already_liked": True}

    # Increment and record
    liked_by.append(liker_id)
    novel.liked_by = liked_by
    novel.like_count = (novel.like_count or 0) + 1
    await db.commit()
    return {"like_count": novel.like_count, "already_liked": False}


# ── Tags ──────────────────────────────────────────────────


@router.get("/tags", response_model=list[CommunityTagResponse])
async def list_tags(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, ge=1, le=200),
):
    """List all tags sorted by usage count descending. Public."""
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
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Get chapter count and total word count for a project. Requires auth + ownership."""
    project = await get_project_for_owner(project_id, current_user, db)

    ch_result = await db.execute(
        select(
            func.count(Chapter.id), func.coalesce(func.sum(Chapter.word_count), 0)
        ).where(Chapter.project_id == project_id)
    )
    ch_count, total_words = ch_result.one()

    return {
        "title": project.title,
        "genre": project.genre.value
        if hasattr(project.genre, "value")
        else str(project.genre),
        "total_chapters": project.total_chapters,
        "chapter_count": ch_count,
        "total_words": total_words,
    }
