"""Outline generation and management API."""

import json
import re
from typing import Annotated, Any, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import User, get_current_user, get_project_for_owner
from app.core.llm_client import llm_client
from app.core.pacing_planner import pacing_planner
from app.core.memory_store import memory_store
from app.database import get_db, async_session
from app.models.project import Outline, Project, ProjectStatus, Worldview
from app.prompts.templates import build_outline_prompt
from app.schemas.models import OutlineCreate

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/outline", tags=["outline"])

# max_tokens for outline generation — must be large enough for full chapter list
# Each chapter needs ~100-125 tokens (title + summary + key_events + reveal_elements)
# 30 chapters ≈ 3700 tokens + story_arc + JSON overhead ≈ 4000-4500 tokens
# 8192 gives comfortable headroom for up to 50 chapters
OUTLINE_MAX_TOKENS = 8192


@router.post("/{project_id}/generate")
async def generate_outline(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Generate a story outline based on worldview and pacing plan (non-streaming fallback)."""
    project = await get_project_for_owner(project_id, current_user, db)

    # Query worldview directly (avoid lazy loading)
    wv_result = await db.execute(select(Worldview).where(Worldview.project_id == project_id))
    worldview = wv_result.scalar_one_or_none()
    if not worldview:
        raise HTTPException(status_code=400, detail="请先上传世界观")

    elements = worldview.parsed_elements or []

    # Build pacing plan
    reveal_plan = pacing_planner.plan(
        elements=elements,
        total_chapters=project.total_chapters,
        chapter_word_count=project.chapter_word_count,
    )

    # Generate outline via LLM
    messages = build_outline_prompt(
        genre=project.genre,
        worldview_elements=elements,
        total_chapters=project.total_chapters,
        chapter_word_count=project.chapter_word_count,
        reveal_plan=reveal_plan,
        style_intensity=project.style_intensity,
    )

    try:
        raw_response = await llm_client.chat(
            messages, temperature=0.7, max_tokens=OUTLINE_MAX_TOKENS
        )
        logger.info(
            "Outline LLM response for project %s: %d chars, %d elements, %d chapters",
            project_id, len(raw_response), len(elements), project.total_chapters,
        )
    except Exception as e:
        logger.error("Outline LLM call failed for project %s: %s", project_id, str(e))
        raise HTTPException(
            status_code=502,
            detail=f"大纲生成失败，LLM 服务异常: {str(e)}",
        )

    # Parse and normalize LLM response
    chapters_data = _parse_outline_response(raw_response, project.total_chapters)
    logger.info(
        "Outline parsed for project %s: story_arc=%d chars, %d chapters",
        project_id, len(chapters_data.get("story_arc", "")), len(chapters_data.get("chapters", [])),
    )

    # Delete existing outline if any (query directly)
    ol_result = await db.execute(select(Outline).where(Outline.project_id == project_id))
    existing_ol = ol_result.scalar_one_or_none()
    if existing_ol:
        await db.delete(existing_ol)
        await db.flush()

    # Create outline
    outline = Outline(
        project_id=project_id,
        story_arc=chapters_data.get("story_arc", ""),
        chapters=chapters_data.get("chapters", []),
        reveal_plan=reveal_plan,
    )
    db.add(outline)

    # Initialize story memory
    await memory_store.get_or_create(db, project_id)

    project.status = ProjectStatus.OUTLINE_PENDING
    await db.commit()
    await db.refresh(outline)

    return {
        "id": outline.id,
        "project_id": project_id,
        "story_arc": outline.story_arc,
        "chapters": outline.chapters,
        "reveal_plan": outline.reveal_plan,
    }


@router.post("/{project_id}/generate-stream")
async def generate_outline_stream(
    project_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Generate outline via SSE streaming — keeps connection alive, avoids proxy timeout.

    Events:
      data: {"type":"start","message":"..."}\n\n
      data: {"type":"chunk","content":"..."}\n\n
      data: {"type":"complete","outline":{...}}\n\n
      data: {"type":"error","message":"..."}\n\n
    """
    project = await get_project_for_owner(project_id, current_user, db)

    wv_result = await db.execute(select(Worldview).where(Worldview.project_id == project_id))
    worldview = wv_result.scalar_one_or_none()
    if not worldview:
        raise HTTPException(status_code=400, detail="请先上传世界观")

    elements = worldview.parsed_elements or []

    reveal_plan = pacing_planner.plan(
        elements=elements,
        total_chapters=project.total_chapters,
        chapter_word_count=project.chapter_word_count,
    )

    messages = build_outline_prompt(
        genre=project.genre,
        worldview_elements=elements,
        total_chapters=project.total_chapters,
        chapter_word_count=project.chapter_word_count,
        reveal_plan=reveal_plan,
        style_intensity=project.style_intensity,
    )

    project_id_val = project_id
    total_chapters_val = project.total_chapters
    reveal_plan_val = reveal_plan

    async def event_stream() -> AsyncGenerator[str, None]:
        # Send start event
        yield f'data: {json.dumps({"type": "start", "message": "正在生成大纲，请耐心等待...", "total_chapters": total_chapters_val}, ensure_ascii=False)}\n\n'

        full_response = ""
        chunk_count = 0

        try:
            async for chunk in llm_client.chat_stream(
                messages, temperature=0.7, max_tokens=OUTLINE_MAX_TOKENS
            ):
                full_response += chunk
                chunk_count += 1

                # Send chunk every 5 chunks to avoid flooding
                if chunk_count % 5 == 0:
                    yield f'data: {json.dumps({"type": "progress", "chunks": chunk_count, "chars": len(full_response)}, ensure_ascii=False)}\n\n'

            logger.info(
                "Outline stream complete for project %s: %d chunks, %d chars",
                project_id_val, chunk_count, len(full_response),
            )

            # Parse the complete response
            chapters_data = _parse_outline_response(full_response, total_chapters_val)
            logger.info(
                "Outline parsed for project %s: story_arc=%d chars, %d chapters",
                project_id_val, len(chapters_data.get("story_arc", "")),
                len(chapters_data.get("chapters", [])),
            )

            # Save to DB in a fresh session
            async with async_session() as save_db:
                # Delete existing outline
                ol_result = await save_db.execute(
                    select(Outline).where(Outline.project_id == project_id_val)
                )
                existing_ol = ol_result.scalar_one_or_none()
                if existing_ol:
                    await save_db.delete(existing_ol)
                    await save_db.flush()

                outline = Outline(
                    project_id=project_id_val,
                    story_arc=chapters_data.get("story_arc", ""),
                    chapters=chapters_data.get("chapters", []),
                    reveal_plan=reveal_plan_val,
                )
                save_db.add(outline)

                await memory_store.get_or_create(save_db, project_id_val)

                # Update project status
                proj_result = await save_db.execute(
                    select(Project).where(Project.id == project_id_val)
                )
                proj = proj_result.scalar_one_or_none()
                if proj:
                    proj.status = ProjectStatus.OUTLINE_PENDING

                await save_db.commit()
                await save_db.refresh(outline)

                result = {
                    "id": outline.id,
                    "project_id": project_id_val,
                    "story_arc": outline.story_arc,
                    "chapters": outline.chapters,
                    "reveal_plan": outline.reveal_plan,
                }

                yield f'data: {json.dumps({"type": "complete", "outline": result}, ensure_ascii=False)}\n\n'

        except Exception as e:
            logger.error("Outline stream failed for project %s: %s", project_id_val, str(e))
            yield f'data: {json.dumps({"type": "error", "message": f"大纲生成失败: {str(e)}"}, ensure_ascii=False)}\n\n'

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{project_id}")
async def get_outline(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)

    result = await db.execute(select(Outline).where(Outline.project_id == project_id))
    outline = result.scalar_one_or_none()
    if not outline:
        raise HTTPException(status_code=404, detail="大纲不存在，请先生成")

    return {
        "id": outline.id,
        "project_id": project_id,
        "story_arc": outline.story_arc,
        "chapters": outline.chapters,
        "reveal_plan": outline.reveal_plan,
        "created_at": outline.created_at.isoformat() if outline.created_at else None,
        "updated_at": outline.updated_at.isoformat() if outline.updated_at else None,
    }


@router.put("/{project_id}")
async def update_outline(
    project_id: str,
    data: OutlineCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Update outline (user-edited)."""
    project = await get_project_for_owner(project_id, current_user, db)

    result = await db.execute(select(Outline).where(Outline.project_id == project_id))
    outline = result.scalar_one_or_none()
    if not outline:
        raise HTTPException(status_code=404, detail="大纲不存在")

    outline.story_arc = data.story_arc
    outline.chapters = [c.model_dump() for c in data.chapters]

    # Rebuild reveal plan based on edited chapters
    wv_result = await db.execute(select(Worldview).where(Worldview.project_id == project_id))
    worldview = wv_result.scalar_one_or_none()
    if worldview:
        elements = worldview.parsed_elements or []
        outline.reveal_plan = pacing_planner.plan(
            elements=elements,
            total_chapters=project.total_chapters,
            chapter_word_count=project.chapter_word_count,
        )

    await db.commit()
    await db.refresh(outline)

    return {
        "id": outline.id,
        "project_id": project_id,
        "story_arc": outline.story_arc,
        "chapters": outline.chapters,
        "reveal_plan": outline.reveal_plan,
    }


@router.post("/{project_id}/confirm")
async def confirm_outline(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Confirm the outline and move to writing phase."""
    project = await get_project_for_owner(project_id, current_user, db)

    # Check outline exists (query directly)
    ol_result = await db.execute(select(Outline).where(Outline.project_id == project_id).limit(1))
    if not ol_result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="请先生成大纲")

    project.status = ProjectStatus.OUTLINE_CONFIRMED
    await db.commit()

    return {"message": "大纲已确认，可以开始逐章生成", "status": project.status.value}


def _parse_outline_response(raw: str, total_chapters: int) -> dict[str, Any]:
    """Parse LLM response into structured outline data.

    Handles:
    - JSON wrapped in ```json ... ``` code fences
    - JSON embedded in surrounding text
    - Missing/malformed fields → normalized to correct types
    - Fewer/more chapters than expected → padded/truncated
    """
    json_str = _extract_json(raw)

    if json_str:
        try:
            data = json.loads(json_str)
            if isinstance(data, dict):
                return _normalize_outline_data(data, total_chapters)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Outline JSON parse failed: %s", str(e))

    # Fallback: create a minimal outline
    logger.warning("Using fallback outline (LLM response could not be parsed)")
    return {
        "story_arc": "故事大纲生成中，请手动编辑完善",
        "chapters": [
            {
                "chapter_num": i + 1,
                "title": f"第{i+1}章",
                "summary": "待填充",
                "key_events": [],
                "reveal_elements": [],
            }
            for i in range(total_chapters)
        ],
    }


def _extract_json(text: str) -> str | None:
    """Extract JSON content from LLM response (may be wrapped in code fences or embedded in text)."""
    # Try to find ```json ... ``` block
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Try to find raw JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0).strip()

    return None


def _to_str(value: Any, separator: str = "、") -> str:
    """Normalize a value that may be a string, list, or None into a single string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return separator.join(str(item) for item in value if item is not None)
    return str(value)


def _to_list(value: Any) -> list[str]:
    """Normalize a value that may be a string, list, or None into a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, str):
        # Split on common delimiters
        parts = re.split(r"[，,；;、\n]", value)
        return [p.strip() for p in parts if p.strip()]
    return [str(value)]


def _to_int(value: Any, default: int = 0) -> int:
    """Normalize a value that may be a string or float into an int."""
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            # Try to extract first number from string
            match = re.search(r"\d+", value)
            if match:
                return int(match.group())
            return default
    return default


def _normalize_chapter(ch: Any, expected_num: int) -> dict[str, Any]:
    """Normalize a single chapter entry from LLM output."""
    if not isinstance(ch, dict):
        return {
            "chapter_num": expected_num,
            "title": f"第{expected_num}章",
            "summary": "",
            "key_events": [],
            "reveal_elements": [],
        }

    return {
        "chapter_num": _to_int(ch.get("chapter_num"), expected_num) or expected_num,
        "title": _to_str(ch.get("title")) or f"第{expected_num}章",
        "summary": _to_str(ch.get("summary")),
        "key_events": _to_list(ch.get("key_events")),
        "reveal_elements": _to_list(ch.get("reveal_elements")),
    }


def _normalize_outline_data(data: dict[str, Any], total_chapters: int) -> dict[str, Any]:
    """Normalize the full outline data from LLM output.

    Ensures:
    - story_arc is a string
    - chapters is a list with exactly total_chapters entries
    - Each chapter has correct field types
    - chapter_num values are sequential integers
    """
    story_arc = _to_str(data.get("story_arc"))

    raw_chapters = data.get("chapters", [])
    if not isinstance(raw_chapters, list):
        raw_chapters = []

    # Normalize each chapter
    normalized = []
    chapter_by_num: dict[int, dict[str, Any]] = {}

    for i, ch in enumerate(raw_chapters):
        nc = _normalize_chapter(ch, i + 1)
        num = nc["chapter_num"]
        # Only keep first occurrence of each chapter_num
        if num not in chapter_by_num:
            chapter_by_num[num] = nc
            normalized.append(nc)

    # Ensure we have exactly total_chapters entries
    result_chapters = []
    for i in range(1, total_chapters + 1):
        if i in chapter_by_num:
            result_chapters.append(chapter_by_num[i])
        elif i <= len(normalized):
            # Use the i-th normalized chapter with corrected num
            ch = dict(normalized[i - 1])
            ch["chapter_num"] = i
            result_chapters.append(ch)
        else:
            # Pad with default
            result_chapters.append({
                "chapter_num": i,
                "title": f"第{i}章",
                "summary": "待填充",
                "key_events": [],
                "reveal_elements": [],
            })

    return {
        "story_arc": story_arc,
        "chapters": result_chapters,
    }
