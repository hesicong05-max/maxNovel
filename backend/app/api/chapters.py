"""Chapter generation and management API."""

import json
from typing import Annotated, Any, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.core.auth import User, get_current_user, get_project_for_owner
from app.core.llm_client import llm_client
from app.core.memory_store import memory_store
from app.core.pacing_planner import pacing_planner
from app.core.rate_limiter import limiter
from app.core.worldview_parser import worldview_parser
from app.database import get_db, async_session
from app.models.project import Chapter, Outline, Project, ProjectStatus, StoryMemory, Worldview
from app.prompts.templates import build_chapter_prompt, build_summary_prompt
from app.schemas.models import (
    ChapterUpdate,
    ProgressResponse,
    WordCountConfigRequest,
    WordCountConfigResponse,
)

router = APIRouter(prefix="/api/chapters", tags=["chapters"])

# Word count validation constants
MIN_WORD_COUNT = 500
MAX_WORD_COUNT = 10000


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
    ol_result = await db.execute(select(Outline).where(Outline.project_id == project_id))
    outline = ol_result.scalar_one_or_none()

    outline_chapters = (outline.chapters if outline else []) or []
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

        chapters_info.append({
            "chapter_num": i,
            "target_word_count": target_wc,
            "effective_word_count": effective_wc,
            "title": entry.get("title", f"第{i}章"),
        })

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
    overrides = {c.chapter_num: c.target_word_count for c in data.chapters if c.target_word_count is not None}
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
    ol_result = await db.execute(select(Outline).where(Outline.project_id == project_id))
    outline = ol_result.scalar_one_or_none()
    if not outline:
        raise HTTPException(status_code=400, detail="大纲不存在，请先生成大纲")

    # Update chapter entries with target_word_count
    chapters_data = list(outline.chapters or [])
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
    chapters_data.insert(0, {"chapter_num": -1, "total_word_count": data.total_word_count})

    outline.chapters = chapters_data
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
        select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.chapter_num)
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
    wv_result = await db.execute(select(Worldview).where(Worldview.project_id == project_id))
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
    mem_result = await db.execute(select(StoryMemory).where(StoryMemory.project_id == project_id))
    memory = mem_result.scalar_one_or_none()

    revealed = set(memory.revealed_elements) if memory else set()
    total = len(elements)

    # Count chapters
    ch_result = await db.execute(
        select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.chapter_num)
    )
    chapters = ch_result.scalars().all()
    chapter_count = len(chapters)
    current_chapter = chapter_count + 1 if project.status == ProjectStatus.WRITING else chapter_count

    # Determine phase from outline's LLM-generated reveal_plan
    ol_result = await db.execute(select(Outline).where(Outline.project_id == project_id))
    outline = ol_result.scalar_one_or_none()
    phase = ""
    if outline and outline.reveal_plan:
        for entry in outline.reveal_plan:
            if isinstance(entry, dict) and entry.get("chapter") == (current_chapter or 1):
                phase = entry.get("phase", "")
                break
    # Fallback to pacing_planner if no LLM-generated plan
    if not phase:
        phase = pacing_planner._phase_for(current_chapter or 1, project.total_chapters)
        phase = pacing_planner.get_phase_label(phase)

    pending_fs = [f for f in (memory.foreshadows or []) if f["status"] != "resolved"] if memory else []

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
):
    """Edit a generated chapter."""
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

    if data.title is not None:
        chapter.title = data.title
    if data.content is not None:
        chapter.content = data.content
        chapter.word_count = len(data.content)
    chapter.status = "edited"

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
):
    """Generate a single chapter with streaming output."""
    # Verify ownership before streaming starts
    await get_project_for_owner(project_id, current_user, db)

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
    skip_existing: bool = True,
):
    """Batch generate all chapters with streaming progress via SSE."""
    # Verify ownership before streaming starts
    await get_project_for_owner(project_id, current_user, db)

    return StreamingResponse(
        _stream_batch_generate(project_id, current_user.id, skip_existing=skip_existing),
        media_type="text/event-stream",
    )


async def _stream_single_chapter(project_id: str, chapter_num: int, user_id: str):
    """Stream single chapter generation via SSE.

    Re-verifies project ownership in the new session to prevent TOCTOU.
    """
    async with async_session() as db:
        # Re-verify ownership in the new session
        result = await db.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            yield _sse({"type": "error", "error": "项目不存在"})
            return
        if project.owner_id is not None and project.owner_id != user_id:
            yield _sse({"type": "error", "error": "无权操作此项目"})
            return

        async for event in _generate_chapter_core(db, project_id, chapter_num):
            yield event


async def _stream_batch_generate(
    project_id: str,
    user_id: str,
    skip_existing: bool = True,
):
    """Stream batch generation of all chapters via SSE.

    Re-verifies project ownership in the new session to prevent TOCTOU.
    """
    async with async_session() as db:
        # Load project and re-verify ownership
        result = await db.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            yield _sse({"type": "error", "error": "项目不存在"})
            return
        if project.owner_id is not None and project.owner_id != user_id:
            yield _sse({"type": "error", "error": "无权操作此项目"})
            return

        # Load outline
        ol_result = await db.execute(select(Outline).where(Outline.project_id == project_id))
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
            yield _sse({"type": "batch_complete", "total_generated": 0, "message": "所有章节已生成"})
            return

        # Send batch start
        yield _sse({
            "type": "batch_start",
            "total_chapters": project.total_chapters,
            "chapters_to_generate": chapters_to_generate,
            "total_to_generate": total_to_generate,
        })

        # Update project status
        project.status = ProjectStatus.WRITING
        await db.commit()

        generated_count = 0
        total_words = 0
        failed_chapters = []

        for idx, chapter_num in enumerate(chapters_to_generate):
            progress = idx + 1
            yield _sse({
                "type": "batch_progress",
                "current": progress,
                "total": total_to_generate,
                "chapter_num": chapter_num,
            })

            # Generate the chapter
            chapter_success = False
            async for event in _generate_chapter_core(
                db, project_id, chapter_num, batch_mode=True
            ):
                # Parse the event to track success
                if '"type": "complete"' in event or '"type":"complete"' in event:
                    chapter_success = True
                yield event

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
        yield _sse({
            "type": "batch_complete",
            "total_generated": generated_count,
            "total_words": total_words,
            "failed_chapters": failed_chapters,
            "total_chapters": project.total_chapters,
        })


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
    # Load project
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        yield _sse({"type": "error", "error": "项目不存在"})
        return

    # Load outline
    ol_result = await db.execute(select(Outline).where(Outline.project_id == project_id))
    outline = ol_result.scalar_one_or_none()
    if not outline:
        yield _sse({"type": "error", "error": "大纲不存在，请先生成并确认大纲"})
        return

    # Load worldview
    wv_result = await db.execute(select(Worldview).where(Worldview.project_id == project_id))
    worldview = wv_result.scalar_one_or_none()
    if not worldview:
        yield _sse({"type": "error", "error": "世界观不存在"})
        return

    # Find the chapter entry in outline
    chapter_entry = None
    total_word_count_target = None
    for ch in (outline.chapters or []):
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
    all_elements = worldview_parser.normalize_elements(worldview.parsed_elements)
    elements_to_reveal = []
    reveal_names_set = set(chapter_entry.get("reveal_elements", []))
    added_ids: set[str] = set()

    for e in all_elements:
        if e["name"] in reveal_names_set or e["id"] in reveal_names_set:
            elements_to_reveal.append(e)
            added_ids.add(e["id"])

    # Also check reveal plan
    for entry in (outline.reveal_plan or []):
        if entry.get("chapter") == chapter_num:
            for eid in entry.get("elements", []):
                if eid in added_ids:
                    continue
                for e in all_elements:
                    if e["id"] == eid:
                        elements_to_reveal.append(e)
                        added_ids.add(e["id"])
                        break

    # Get story context from memory
    memory = await memory_store.get_or_create(db, project_id)
    context = await memory_store.get_context_for_chapter(memory, chapter_num)

    # Update project status
    project.status = ProjectStatus.WRITING
    await db.commit()

    # Determine phase from outline's LLM-generated reveal_plan
    phase = ""
    phase_guidance = ""
    for entry in (outline.reveal_plan or []):
        if isinstance(entry, dict) and entry.get("chapter") == chapter_num:
            phase = entry.get("phase", "")
            phase_guidance = entry.get("summary", "")
            break
    # Fallback to pacing_planner if no LLM-generated plan exists
    if not phase:
        phase = pacing_planner._phase_for(chapter_num, project.total_chapters)
    phase_label = phase if phase else "推进"

    # Send metadata
    yield _sse({
        "type": "metadata",
        "chapter_num": chapter_num,
        "title": chapter_entry.get("title", f"第{chapter_num}章"),
        "elements_to_reveal": [e["name"] for e in elements_to_reveal],
        "phase": phase,
        "phase_label": phase_label,
        "target_word_count": effective_wc,
    })

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
    )

    # Stream content — use user-configured temperature from settings
    from app.core.settings_store import load_settings as load_llm_settings
    llm_s = load_llm_settings()
    stream_temperature = llm_s.get("temperature", 0.8)

    full_content = ""
    try:
        async for chunk in llm_client.chat_stream(messages, temperature=stream_temperature):
            full_content += chunk
            yield _sse({"type": "content", "text": chunk, "chapter_num": chapter_num})
    except Exception as e:
        if full_content:
            yield _sse({"type": "error", "error": f"第{chapter_num}章生成中断: {str(e)}，已保存部分内容", "chapter_num": chapter_num})
        else:
            yield _sse({"type": "error", "error": f"第{chapter_num}章生成失败: {str(e)}", "chapter_num": chapter_num})
            return

    # Generate summary for memory (non-fatal if this fails)
    summary = ""
    try:
        summary_messages = build_summary_prompt(full_content, chapter_num)
        summary = await llm_client.chat(summary_messages, temperature=0.3, max_tokens=200)
    except Exception:
        summary = full_content[:200] if full_content else "（摘要生成失败）"

    # Save chapter to database
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
    await memory_store.mark_revealed(db, memory, [e["id"] for e in elements_to_reveal], chapter_num)
    await memory_store.add_chapter_summary(db, memory, chapter_num, summary)
    await memory_store.add_timeline_event(db, memory, chapter_num, "章节生成", summary)

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        yield _sse({"type": "error", "error": f"第{chapter_num}章保存失败: {str(e)}", "chapter_num": chapter_num})
        return

    # Send completion
    yield _sse({
        "type": "complete",
        "chapter_num": chapter_num,
        "word_count": len(full_content),
        "summary": summary,
    })


def _sse(data: dict[str, Any]) -> str:
    """Format as Server-Sent Event."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
