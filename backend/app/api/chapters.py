"""Chapter generation and management API."""

import json
import logging
from typing import Annotated, Any, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.core.auth import User, get_current_user, get_project_for_owner
from app.core.llm_client import LLMResponseTruncatedError, llm_client
from app.core.legacy_json import LegacyObjectListResult, read_legacy_object_list
from app.core.maintenance import (
    PROJECT_WRITE_FROZEN_CODE,
    ProjectWriteFrozenError,
    ensure_project_writes_available,
    project_write_frozen_sse_event,
    require_project_writes_available,
)
from app.core.memory_store import InvalidLegacyStoryMemoryError, memory_store
from app.core.pacing_planner import pacing_planner
from app.core.rate_limiter import limiter
from app.core.worldview_parser import worldview_parser
from app.database import async_session, get_db
from app.models.project import (
    Chapter,
    Outline,
    Project,
    ProjectStatus,
    StoryMemory,
    Worldview,
)
from app.prompts.templates import build_chapter_prompt, build_summary_prompt
from app.schemas.models import (
    ChapterUpdate,
    ProgressResponse,
    WordCountConfigRequest,
    WordCountConfigResponse,
)

router = APIRouter(prefix="/api/chapters", tags=["chapters"])
logger = logging.getLogger(__name__)

# Word count validation constants
MIN_WORD_COUNT = 500
MAX_WORD_COUNT = 10000

_LEGACY_OUTLINE_CHAPTERS_INVALID = "LEGACY_OUTLINE_CHAPTERS_INVALID"


def _read_outline_object_list(
    outline: Outline | None,
    field_name: str,
) -> LegacyObjectListResult:
    if outline is None:
        return LegacyObjectListResult(items=[])
    result = read_legacy_object_list(getattr(outline, field_name, None))
    if not result.valid:
        logger.warning(
            "Invalid legacy outline list project=%s field=%s category=%s",
            outline.project_id,
            field_name,
            result.error_category,
        )
    return result

# Prevent overlapping single/batch writers from corrupting the same project's
# chapter and memory state within one application process. Database uniqueness
# constraints provide the final guard across multiple processes.
_active_generation_projects: set[str] = set()


def _fallback_summary(content: str, max_chars: int = 400) -> str:
    """Build a compact deterministic summary when no LLM summary is available."""
    normalized = " ".join(content.split())
    return normalized[:max_chars]


def _chapter_output_token_budget(
    target_word_count: int,
    configured_max_tokens: Any,
) -> int:
    """Calculate a bounded output budget that respects the configured maximum."""
    try:
        configured_max = int(configured_max_tokens)
    except (TypeError, ValueError):
        configured_max = 4096
    configured_max = min(max(configured_max, 2048), 32768)
    desired_tokens = max(target_word_count * 2 + 500, 2048)
    return min(desired_tokens, configured_max)


async def _refresh_project_completion_status(
    db: AsyncSession,
    project: Project,
) -> None:
    """Mark the project complete only when every planned chapter is usable."""
    result = await db.execute(
        select(func.count(Chapter.id)).where(
            Chapter.project_id == project.id,
            Chapter.status.in_(("generated", "edited")),
        )
    )
    completed_chapters = result.scalar_one()
    project.status = (
        ProjectStatus.COMPLETED
        if completed_chapters >= project.total_chapters
        else ProjectStatus.WRITING
    )


# ============================================================================
# Word Count Configuration API
# ============================================================================


@router.get("/{project_id}/word-counts")
async def get_word_counts(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Get word count configuration for all chapters."""
    project = await get_project_for_owner(project_id, current_user, db)

    # Load outline to get chapter entries with target_word_count
    ol_result = await db.execute(
        select(Outline).where(Outline.project_id == project_id)
    )
    outline = ol_result.scalar_one_or_none()

    outline_chapters = _read_outline_object_list(outline, "chapters").items
    total_word_count = None

    # Check if total_word_count is stored in outline metadata
    for ch in outline_chapters:
        if ch.get("chapter_num") == -1:  # metadata entry
            total_word_count = ch.get("total_word_count")
            break

    # Build per-chapter word count info
    chapters_info = []
    for i in range(1, project.total_chapters + 1):
        # Find chapter entry in outline
        entry = next((c for c in outline_chapters if c.get("chapter_num") == i), {})

        # target_word_count from outline entry (user override)
        target_wc = entry.get("target_word_count")

        # Effective word count: override > auto-distributed > project default
        if total_word_count and total_word_count > 0:
            auto_wc = total_word_count // project.total_chapters
        else:
            auto_wc = project.chapter_word_count

        effective_wc = target_wc if target_wc else auto_wc

        chapters_info.append(
            {
                "chapter_num": i,
                "target_word_count": target_wc,
                "effective_word_count": effective_wc,
                "title": entry.get("title", f"第{i}章"),
            }
        )

    return WordCountConfigResponse(
        total_word_count=total_word_count,
        project_default=project.chapter_word_count,
        chapters=chapters_info,
    )


@router.put("/{project_id}/word-counts")
async def update_word_counts(
    project_id: str,
    data: WordCountConfigRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    _write_gate: Annotated[None, Depends(require_project_writes_available)],
):
    """Save word count configuration."""
    project = await get_project_for_owner(project_id, current_user, db)

    # Validate total_word_count
    if data.total_word_count is not None:
        min_total = MIN_WORD_COUNT * project.total_chapters
        max_total = MAX_WORD_COUNT * project.total_chapters
        if data.total_word_count < min_total:
            raise HTTPException(
                status_code=422,
                detail=f"总字数不能少于 {min_total}（每章最少 {MIN_WORD_COUNT} 字 × {project.total_chapters} 章）",
            )
        if data.total_word_count > max_total:
            raise HTTPException(
                status_code=422,
                detail=f"总字数不能超过 {max_total}（每章最多 {MAX_WORD_COUNT} 字 × {project.total_chapters} 章）",
            )

    # Validate per-chapter overrides
    overrides = {
        c.chapter_num: c.target_word_count
        for c in data.chapters
        if c.target_word_count is not None
    }
    for ch_num, wc in overrides.items():
        if wc < MIN_WORD_COUNT:
            raise HTTPException(
                status_code=422,
                detail=f"第{ch_num}章字数不能少于 {MIN_WORD_COUNT} 字",
            )
        if wc > MAX_WORD_COUNT:
            raise HTTPException(
                status_code=422,
                detail=f"第{ch_num}章字数不能超过 {MAX_WORD_COUNT} 字",
            )
        if ch_num < 1 or ch_num > project.total_chapters:
            raise HTTPException(
                status_code=422,
                detail=f"章节号 {ch_num} 超出范围（1-{project.total_chapters}）",
            )

    # Load outline
    ol_result = await db.execute(
        select(Outline).where(Outline.project_id == project_id)
    )
    outline = ol_result.scalar_one_or_none()
    if not outline:
        raise HTTPException(
            status_code=400,
            detail="当前项目没有可用的历史章节规划；新章节规划将在第二阶段开放",
        )

    ensure_project_writes_available()

    # Update chapter entries with target_word_count
    chapters_result = _read_outline_object_list(outline, "chapters")
    if not chapters_result.valid:
        raise HTTPException(
            status_code=409,
            detail={
                "code": _LEGACY_OUTLINE_CHAPTERS_INVALID,
                "message": "大纲章节配置无法读取，请重新保存大纲后重试",
            },
        )
    chapters_data = chapters_result.items
    for ch in chapters_data:
        ch_num = ch.get("chapter_num")
        if ch_num and ch_num in overrides:
            ch["target_word_count"] = overrides[ch_num]
        elif ch_num and ch_num > 0:
            # Clear override if not in the new config
            ch.pop("target_word_count", None)

    # Store total_word_count in a metadata entry
    # Remove existing metadata entry
    chapters_data = [c for c in chapters_data if c.get("chapter_num") != -1]
    # Add new metadata entry
    chapters_data.insert(
        0, {"chapter_num": -1, "total_word_count": data.total_word_count}
    )

    outline.chapters = chapters_data
    ensure_project_writes_available()
    await db.commit()

    # Return updated config
    return await get_word_counts(project_id, db, current_user)


# ============================================================================
# Chapter CRUD API
# ============================================================================


@router.get("/{project_id}")
async def list_chapters(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)

    result = await db.execute(
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_num)
    )
    chapters = result.scalars().all()
    return [
        {
            "id": c.id,
            "chapter_num": c.chapter_num,
            "title": c.title,
            "status": c.status,
            "word_count": c.word_count,
            "revealed_elements": c.revealed_elements or [],
        }
        for c in chapters
    ]


@router.get("/{project_id}/progress")
async def get_progress(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Get worldview reveal progress and story state."""
    project = await get_project_for_owner(project_id, current_user, db)

    # Query worldview directly
    wv_result = await db.execute(
        select(Worldview).where(Worldview.project_id == project_id)
    )
    worldview = wv_result.scalar_one_or_none()

    if not worldview:
        return ProgressResponse(
            total_elements=0,
            revealed_elements=0,
            reveal_percentage=0.0,
            current_phase="draft",
            current_chapter=0,
            total_chapters=project.total_chapters,
            pending_foreshadows=0,
            character_states={},
        )

    elements = worldview_parser.normalize_elements(worldview.parsed_elements)

    # Query memory directly
    mem_result = await db.execute(
        select(StoryMemory).where(StoryMemory.project_id == project_id)
    )
    memory = mem_result.scalar_one_or_none()

    revealed = set(memory.revealed_elements) if memory else set()
    total = len(elements)

    # Count chapters
    ch_result = await db.execute(
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_num)
    )
    chapters = ch_result.scalars().all()
    chapter_count = len(chapters)
    current_chapter = (
        chapter_count + 1 if project.status == ProjectStatus.WRITING else chapter_count
    )

    # Determine phase from outline's LLM-generated reveal_plan
    ol_result = await db.execute(
        select(Outline).where(Outline.project_id == project_id)
    )
    outline = ol_result.scalar_one_or_none()
    phase = ""
    reveal_plan = _read_outline_object_list(outline, "reveal_plan").items
    if reveal_plan:
        for entry in reveal_plan:
            if isinstance(entry, dict) and entry.get("chapter") == (
                current_chapter or 1
            ):
                phase = entry.get("phase", "")
                break
    # Fallback to pacing_planner if no LLM-generated plan
    if not phase:
        phase = pacing_planner._phase_for(current_chapter or 1, project.total_chapters)
        phase = pacing_planner.get_phase_label(phase)

    pending_fs = (
        [f for f in (memory.foreshadows or []) if f["status"] != "resolved"]
        if memory
        else []
    )

    return ProgressResponse(
        total_elements=total,
        revealed_elements=len(revealed),
        reveal_percentage=round(len(revealed) / max(total, 1) * 100, 1),
        current_phase=phase,
        current_chapter=current_chapter,
        total_chapters=project.total_chapters,
        pending_foreshadows=len(pending_fs),
        character_states=memory.character_states if memory else {},
    )


@router.get("/{project_id}/{chapter_num}")
async def get_chapter(
    project_id: str,
    chapter_num: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)

    result = await db.execute(
        select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.chapter_num == chapter_num,
        )
    )
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    return {
        "id": chapter.id,
        "project_id": chapter.project_id,
        "chapter_num": chapter.chapter_num,
        "title": chapter.title,
        "content": chapter.content,
        "word_count": chapter.word_count,
        "summary": chapter.summary,
        "status": chapter.status,
        "revealed_elements": chapter.revealed_elements or [],
    }


@router.put("/{project_id}/{chapter_num}")
async def update_chapter(
    project_id: str,
    chapter_num: int,
    data: ChapterUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    _write_gate: Annotated[None, Depends(require_project_writes_available)],
):
    """Edit a generated chapter."""
    project = await get_project_for_owner(project_id, current_user, db)

    result = await db.execute(
        select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.chapter_num == chapter_num,
        )
    )
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    ensure_project_writes_available()

    if data.title is not None:
        chapter.title = data.title
    if data.content is not None:
        chapter.content = data.content
        chapter.word_count = len(data.content)
        chapter.summary = _fallback_summary(data.content)

        # Keep the continuity memory aligned with the edited source of truth.
        memory = await memory_store.get_or_create(db, project_id)
        await memory_store.add_chapter_summary(db, memory, chapter_num, chapter.summary)
        await memory_store.add_timeline_event(
            db, memory, chapter_num, "章节生成", chapter.summary
        )
    chapter.status = "edited"
    await _refresh_project_completion_status(db, project)

    ensure_project_writes_available()
    await db.commit()
    await db.refresh(chapter)

    return {
        "id": chapter.id,
        "chapter_num": chapter.chapter_num,
        "title": chapter.title,
        "content": chapter.content,
        "word_count": chapter.word_count,
        "status": chapter.status,
    }


# ============================================================================
# Chapter Generation API (Single + Batch)
# ============================================================================


@router.post("/{project_id}/{chapter_num}/generate")
@limiter.limit(app_settings.RATE_LIMIT_LLM)
async def generate_chapter(
    request: Request,
    project_id: str,
    chapter_num: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    _write_gate: Annotated[None, Depends(require_project_writes_available)],
):
    """Generate a single chapter with streaming output."""
    # Verify ownership before streaming starts
    project = await get_project_for_owner(project_id, current_user, db)

    # Validate chapter number range
    if chapter_num < 1 or chapter_num > project.total_chapters:
        raise HTTPException(
            status_code=422,
            detail=f"章节号 {chapter_num} 超出范围（1-{project.total_chapters}）",
        )

    return StreamingResponse(
        _stream_single_chapter(project_id, chapter_num, current_user.id),
        media_type="text/event-stream",
    )


@router.post("/{project_id}/generate-all")
@limiter.limit(app_settings.RATE_LIMIT_LLM)
async def generate_all_chapters(
    request: Request,
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    _write_gate: Annotated[None, Depends(require_project_writes_available)],
    skip_existing: bool = True,
):
    """Batch generate all chapters with streaming progress via SSE."""
    # Verify ownership before streaming starts
    await get_project_for_owner(project_id, current_user, db)

    return StreamingResponse(
        _stream_batch_generate(
            project_id, current_user.id, skip_existing=skip_existing
        ),
        media_type="text/event-stream",
    )


async def _stream_single_chapter(project_id: str, chapter_num: int, user_id: str):
    """Stream single chapter generation via SSE.

    Re-verifies project ownership in the new session to prevent TOCTOU.
    """
    try:
        ensure_project_writes_available()
    except ProjectWriteFrozenError:
        yield _sse(project_write_frozen_sse_event())
        return

    if project_id in _active_generation_projects:
        yield _sse(
            {"type": "error", "error": "该项目已有生成任务正在运行，请等待完成后重试"}
        )
        return

    _active_generation_projects.add(project_id)
    try:
        async with async_session() as db:
            # Re-verify ownership in the new session
            result = await db.execute(select(Project).where(Project.id == project_id))
            project = result.scalar_one_or_none()
            if not project:
                yield _sse({"type": "error", "error": "项目不存在"})
                return
            if project.owner_id is None or project.owner_id != user_id:
                yield _sse({"type": "error", "error": "无权操作此项目"})
                return

            async for event in _generate_chapter_core(db, project_id, chapter_num):
                yield event
    finally:
        _active_generation_projects.discard(project_id)


async def _stream_batch_generate(
    project_id: str,
    user_id: str,
    skip_existing: bool = True,
):
    """Stream batch generation of all chapters via SSE.

    Re-verifies project ownership in the new session to prevent TOCTOU.
    """
    try:
        ensure_project_writes_available()
    except ProjectWriteFrozenError:
        yield _sse(project_write_frozen_sse_event())
        return

    if project_id in _active_generation_projects:
        yield _sse(
            {"type": "error", "error": "该项目已有生成任务正在运行，请等待完成后重试"}
        )
        return

    _active_generation_projects.add(project_id)
    try:
        async with async_session() as db:
            # Load project and re-verify ownership
            result = await db.execute(select(Project).where(Project.id == project_id))
            project = result.scalar_one_or_none()
            if not project:
                yield _sse({"type": "error", "error": "项目不存在"})
                return
            if project.owner_id is None or project.owner_id != user_id:
                yield _sse({"type": "error", "error": "无权操作此项目"})
                return

            # Load outline
            ol_result = await db.execute(
                select(Outline).where(Outline.project_id == project_id)
            )
            outline = ol_result.scalar_one_or_none()
            if not outline:
                yield _sse({"type": "error", "error": "大纲不存在，请先生成并确认大纲"})
                return

            # Load existing chapters to know which to skip
            ch_result = await db.execute(
                select(Chapter).where(Chapter.project_id == project_id)
            )
            existing_chapters = {c.chapter_num: c for c in ch_result.scalars().all()}

            # Determine which chapters to generate
            chapters_to_generate = []
            for i in range(1, project.total_chapters + 1):
                ch = existing_chapters.get(i)
                if skip_existing and ch and ch.status in ("generated", "edited"):
                    continue
                chapters_to_generate.append(i)

            total_to_generate = len(chapters_to_generate)
            if total_to_generate == 0:
                yield _sse(
                    {
                        "type": "batch_complete",
                        "total_generated": 0,
                        "message": "所有章节已生成",
                    }
                )
                return

            # Send batch start
            yield _sse(
                {
                    "type": "batch_start",
                    "total_chapters": project.total_chapters,
                    "chapters_to_generate": chapters_to_generate,
                    "total_to_generate": total_to_generate,
                }
            )

            # Do not downgrade a completed project merely because a regeneration
            # attempt starts; successful saves recompute the final status.
            try:
                ensure_project_writes_available()
            except ProjectWriteFrozenError:
                await db.rollback()
                yield _sse(project_write_frozen_sse_event())
                return
            if project.status != ProjectStatus.COMPLETED:
                project.status = ProjectStatus.WRITING
            await db.commit()

            generated_count = 0
            total_words = 0
            failed_chapters = []

            for idx, chapter_num in enumerate(chapters_to_generate):
                progress = idx + 1
                yield _sse(
                    {
                        "type": "batch_progress",
                        "current": progress,
                        "total": total_to_generate,
                        "chapter_num": chapter_num,
                    }
                )

                # Generate the chapter
                chapter_success = False
                async for event in _generate_chapter_core(
                    db, project_id, chapter_num, batch_mode=True
                ):
                    # Parse the event to track success
                    maintenance_frozen = False
                    try:
                        event_data = json.loads(event.replace("data: ", "").strip())
                        if event_data.get("type") == "complete":
                            chapter_success = True
                        error = event_data.get("error")
                        maintenance_frozen = (
                            isinstance(error, dict)
                            and error.get("code") == PROJECT_WRITE_FROZEN_CODE
                        )
                    except (json.JSONDecodeError, ValueError):
                        pass
                    yield event
                    if maintenance_frozen:
                        await db.rollback()
                        return

                if chapter_success:
                    generated_count += 1
                    # Accumulate word count from the generated chapter
                    ch_result = await db.execute(
                        select(Chapter).where(
                            Chapter.project_id == project_id,
                            Chapter.chapter_num == chapter_num,
                        )
                    )
                    ch = ch_result.scalar_one_or_none()
                    if ch:
                        total_words += ch.word_count or 0
                else:
                    failed_chapters.append(chapter_num)

            # Send batch complete
            yield _sse(
                {
                    "type": "batch_complete",
                    "total_generated": generated_count,
                    "total_words": total_words,
                    "failed_chapters": failed_chapters,
                    "total_chapters": project.total_chapters,
                }
            )
    finally:
        _active_generation_projects.discard(project_id)


async def _generate_chapter_core(
    db: AsyncSession,
    project_id: str,
    chapter_num: int,
    batch_mode: bool = False,
) -> AsyncGenerator[str, None]:
    """
    Core chapter generation logic. Yields SSE events.
    Used by both single-chapter and batch generation.
    """
    try:
        ensure_project_writes_available()
    except ProjectWriteFrozenError:
        await db.rollback()
        yield _sse(project_write_frozen_sse_event())
        return

    # Load project
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        yield _sse({"type": "error", "error": "项目不存在"})
        return

    # Validate chapter number range (safety net for batch generation)
    if chapter_num < 1 or chapter_num > project.total_chapters:
        yield _sse(
            {
                "type": "error",
                "error": f"章节号 {chapter_num} 超出范围（1-{project.total_chapters}）",
            }
        )
        return

    # Load outline
    ol_result = await db.execute(
        select(Outline).where(Outline.project_id == project_id)
    )
    outline = ol_result.scalar_one_or_none()
    if not outline:
        yield _sse({"type": "error", "error": "大纲不存在，请先生成并确认大纲"})
        return

    # Load worldview
    wv_result = await db.execute(
        select(Worldview).where(Worldview.project_id == project_id)
    )
    worldview = wv_result.scalar_one_or_none()
    if not worldview:
        yield _sse({"type": "error", "error": "世界观不存在"})
        return

    chapters_result = _read_outline_object_list(outline, "chapters")
    if not chapters_result.valid:
        yield _sse(
            {
                "type": "error",
                "error": "大纲章节配置无法读取，请重新保存大纲后重试",
                "code": _LEGACY_OUTLINE_CHAPTERS_INVALID,
            }
        )
        return
    outline_chapters = chapters_result.items
    outline_reveal_plan = _read_outline_object_list(
        outline, "reveal_plan"
    ).items

    # Find the chapter entry in outline
    chapter_entry = None
    total_word_count_target = None
    for ch in outline_chapters:
        if ch.get("chapter_num") == -1:
            total_word_count_target = ch.get("total_word_count")
        elif ch.get("chapter_num") == chapter_num:
            chapter_entry = ch

    if not chapter_entry:
        chapter_entry = {
            "chapter_num": chapter_num,
            "title": f"第{chapter_num}章",
            "summary": "",
            "key_events": [],
            "reveal_elements": [],
        }

    # Determine effective word count
    target_wc = chapter_entry.get("target_word_count")
    if target_wc:
        effective_wc = target_wc
    elif total_word_count_target and total_word_count_target > 0:
        effective_wc = total_word_count_target // project.total_chapters
    else:
        effective_wc = project.chapter_word_count

    # Get elements to reveal
    # Elements in both chapter_entry.reveal_elements and reveal_plan.elements are
    # element NAMES (the LLM is instructed to output names, not IDs).
    # Match by name first, then fall back to ID for backward compatibility.
    all_elements = worldview_parser.normalize_elements(worldview.parsed_elements)
    elements_to_reveal: list[dict[str, Any]] = []
    added_ids: set[str] = set()

    # Round 1: match from chapter_entry.reveal_elements (by name or ID)
    reveal_names_set = set(chapter_entry.get("reveal_elements", []))
    for e in all_elements:
        if e["name"] in reveal_names_set or e["id"] in reveal_names_set:
            elements_to_reveal.append(e)
            added_ids.add(e["id"])

    # Round 2: also check outline.reveal_plan for this chapter
    # (reveal_plan.elements contains names, same as chapter.reveal_elements)
    for entry in outline_reveal_plan:
        if not isinstance(entry, dict):
            continue
        if entry.get("chapter") != chapter_num:
            continue
        for ename in entry.get("elements", []):
            # Skip if already found in Round 1 (check by both name and ID)
            already_added = any(
                e["name"] == ename or e["id"] == ename for e in elements_to_reveal
            )
            if already_added:
                continue
            # Match by name first, then by ID
            for e in all_elements:
                if e["name"] == ename or e["id"] == ename:
                    elements_to_reveal.append(e)
                    added_ids.add(e["id"])
                    break

    # Get story context from memory
    try:
        ensure_project_writes_available()
        memory = await memory_store.get_or_create(db, project_id)
    except ProjectWriteFrozenError:
        await db.rollback()
        yield _sse(project_write_frozen_sse_event())
        return
    try:
        context = await memory_store.get_context_for_chapter(memory, chapter_num)
    except InvalidLegacyStoryMemoryError:
        await db.rollback()
        yield _sse(
            {
                "type": "error",
                "error": "故事记忆配置无法读取，请检查数据后重试",
                "code": "LEGACY_STORY_MEMORY_INVALID",
            }
        )
        return

    # Persist newly-created memory before the long-running stream. Keep a
    # completed project completed while a regeneration attempt is in flight;
    # a failed retry must not downgrade already-valid data.
    try:
        ensure_project_writes_available()
    except ProjectWriteFrozenError:
        await db.rollback()
        yield _sse(project_write_frozen_sse_event())
        return
    if project.status != ProjectStatus.COMPLETED:
        project.status = ProjectStatus.WRITING
    await db.commit()

    # Determine phase from outline's LLM-generated reveal_plan
    phase = ""
    phase_guidance = ""
    for entry in outline_reveal_plan:
        if isinstance(entry, dict) and entry.get("chapter") == chapter_num:
            phase = entry.get("phase", "")
            phase_guidance = entry.get("summary", "")
            break
    # Fallback to pacing_planner if no LLM-generated plan exists
    if not phase:
        phase = pacing_planner._phase_for(chapter_num, project.total_chapters)
    phase_label = phase if phase else "推进"

    # Send metadata
    yield _sse(
        {
            "type": "metadata",
            "chapter_num": chapter_num,
            "title": chapter_entry.get("title", f"第{chapter_num}章"),
            "elements_to_reveal": [e["name"] for e in elements_to_reveal],
            "phase": phase,
            "phase_label": phase_label,
            "target_word_count": effective_wc,
        }
    )

    # Build prompt
    messages = build_chapter_prompt(
        genre=project.genre,
        chapter_num=chapter_num,
        chapter_title=chapter_entry.get("title", f"第{chapter_num}章"),
        chapter_summary=chapter_entry.get("summary", ""),
        key_events=chapter_entry.get("key_events", []),
        elements_to_reveal=elements_to_reveal,
        style_intensity=project.style_intensity,
        context=context,
        chapter_word_count=effective_wc,
        total_chapters=project.total_chapters,
        phase=phase,
        phase_guidance=phase_guidance,
        story_arc=outline.story_arc or "",
        all_element_names=[e["name"] for e in all_elements if e.get("name")],
    )

    # Stream content — use user-configured temperature from settings
    from app.core.settings_store import load_settings as load_llm_settings

    llm_s = load_llm_settings()
    stream_temperature = llm_s.get("temperature", 0.8)

    # Calculate the desired output budget from the target length, while
    # respecting the administrator's configured maximum.
    stream_max_tokens = _chapter_output_token_budget(
        effective_wc,
        llm_s.get("max_tokens", 4096),
    )

    full_content = ""
    try:
        async for chunk in llm_client.chat_stream(
            messages, temperature=stream_temperature, max_tokens=stream_max_tokens
        ):
            full_content += chunk
            yield _sse({"type": "content", "text": chunk, "chapter_num": chapter_num})
    except Exception as e:
        logger.exception(
            "Chapter generation failed for project=%s chapter=%d: %s",
            project_id,
            chapter_num,
            e,
        )
        if isinstance(e, LLMResponseTruncatedError):
            yield _sse(
                {
                    "type": "error",
                    "error": (
                        f"第{chapter_num}章达到最大输出长度，未保存不完整内容；"
                        "请提高最大输出 token 或降低本章目标字数后重试"
                    ),
                    "chapter_num": chapter_num,
                }
            )
        elif full_content:
            yield _sse(
                {
                    "type": "error",
                    "error": f"第{chapter_num}章生成中断，未保存不完整内容，请重试",
                    "chapter_num": chapter_num,
                }
            )
        else:
            yield _sse(
                {
                    "type": "error",
                    "error": f"第{chapter_num}章生成失败，请稍后重试",
                    "chapter_num": chapter_num,
                }
            )
        return

    if not full_content.strip():
        yield _sse(
            {
                "type": "error",
                "error": f"第{chapter_num}章未生成有效内容，未保存，请重试",
                "chapter_num": chapter_num,
            }
        )
        return

    # Generate summary for memory (non-fatal if this fails)
    summary = ""
    try:
        summary_messages = build_summary_prompt(full_content, chapter_num)
        summary = await llm_client.chat(
            summary_messages, temperature=0.3, max_tokens=200
        )
    except Exception:
        summary = (
            _fallback_summary(full_content) if full_content else "（摘要生成失败）"
        )

    # Save chapter to database
    try:
        ensure_project_writes_available()
    except ProjectWriteFrozenError:
        await db.rollback()
        yield _sse(project_write_frozen_sse_event())
        return
    result = await db.execute(
        select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.chapter_num == chapter_num,
        )
    )
    chapter = result.scalar_one_or_none()
    if chapter:
        chapter.title = chapter_entry.get("title", f"第{chapter_num}章")
        chapter.content = full_content
        chapter.word_count = len(full_content)
        chapter.summary = summary
        chapter.status = "generated"
        chapter.revealed_elements = [e["id"] for e in elements_to_reveal]
    else:
        chapter = Chapter(
            project_id=project_id,
            chapter_num=chapter_num,
            title=chapter_entry.get("title", f"第{chapter_num}章"),
            content=full_content,
            word_count=len(full_content),
            summary=summary,
            status="generated",
            revealed_elements=[e["id"] for e in elements_to_reveal],
        )
        db.add(chapter)

    # Update memory
    try:
        await memory_store.mark_revealed(
            db, memory, [e["id"] for e in elements_to_reveal], chapter_num
        )
        await memory_store.add_chapter_summary(db, memory, chapter_num, summary)
        await memory_store.add_timeline_event(
            db, memory, chapter_num, "章节生成", summary
        )
    except ProjectWriteFrozenError:
        await db.rollback()
        yield _sse(project_write_frozen_sse_event())
        return
    await _refresh_project_completion_status(db, project)

    try:
        ensure_project_writes_available()
        await db.commit()
    except ProjectWriteFrozenError:
        await db.rollback()
        yield _sse(project_write_frozen_sse_event())
        return
    except Exception as e:
        await db.rollback()
        logger.exception(
            "Chapter save failed for project=%s chapter=%d: %s",
            project_id,
            chapter_num,
            e,
        )
        yield _sse(
            {
                "type": "error",
                "error": f"第{chapter_num}章保存失败，请稍后重试",
                "chapter_num": chapter_num,
            }
        )
        return

    # Send completion
    yield _sse(
        {
            "type": "complete",
            "chapter_num": chapter_num,
            "word_count": len(full_content),
            "summary": summary,
        }
    )


def _sse(data: dict[str, Any]) -> str:
    """Format as Server-Sent Event."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
