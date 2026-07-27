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
from app.core.worldview_parser import worldview_parser
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

    elements = worldview_parser.normalize_elements(worldview.parsed_elements)

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
    chapters_data, warning = _parse_outline_response(raw_response, project.total_chapters)
    logger.info(
        "Outline parsed for project %s: story_arc=%d chars, %d chapters, warning=%s",
        project_id, len(chapters_data.get("story_arc", "")), len(chapters_data.get("chapters", [])),
        warning or "none",
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

    result = {
        "id": outline.id,
        "project_id": project_id,
        "story_arc": outline.story_arc,
        "chapters": outline.chapters,
        "reveal_plan": outline.reveal_plan,
    }
    if warning:
        result["warning"] = warning
    return result


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

    elements = worldview_parser.normalize_elements(worldview.parsed_elements)

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
            chapters_data, warning = _parse_outline_response(full_response, total_chapters_val)
            logger.info(
                "Outline parsed for project %s: story_arc=%d chars, %d chapters, warning=%s",
                project_id_val, len(chapters_data.get("story_arc", "")),
                len(chapters_data.get("chapters", [])), warning or "none",
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

                event_data = {"type": "complete", "outline": result}
                if warning:
                    event_data["warning"] = warning
                yield f'data: {json.dumps(event_data, ensure_ascii=False)}\n\n'

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
        elements = worldview_parser.normalize_elements(worldview.parsed_elements)
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


def _parse_outline_response(raw: str, total_chapters: int) -> tuple[dict[str, Any], str | None]:
    """Parse LLM response into structured outline data.

    Returns:
        Tuple of (outline_data, warning_message).
        warning_message is None on success, or describes what went wrong.

    Handles:
    - JSON wrapped in ```json ... ``` code fences
    - JSON embedded in surrounding text
    - Missing/malformed fields → normalized to correct types
    - Fewer/more chapters than expected → padded/truncated
    - Truncated JSON (max_tokens hit) → attempts repair
    """
    json_str = _extract_json(raw)

    if json_str:
        try:
            data = json.loads(json_str)
            if isinstance(data, dict):
                normalized = _normalize_outline_data(data, total_chapters)
                # Check if we got fewer chapters than expected from the LLM
                raw_chapters = data.get("chapters", [])
                llm_chapter_count = len(raw_chapters) if isinstance(raw_chapters, list) else 0
                if llm_chapter_count < total_chapters:
                    warning = f"LLM 返回了 {llm_chapter_count} 章（预期 {total_chapters} 章），已自动补齐剩余章节"
                    logger.warning("Outline has fewer chapters than expected: %d/%d", llm_chapter_count, total_chapters)
                    return normalized, warning
                return normalized, None
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Outline JSON parse failed: %s", str(e))

    # Fallback: create a minimal outline
    raw_preview = raw[:200].replace("\n", " ").replace("\r", "") if raw else "(empty)"
    warning = f"无法解析 LLM 返回的 JSON，已生成占位大纲。请重试或手动编辑。响应前200字: {raw_preview}"
    logger.warning("Using fallback outline. Raw response (first 500 chars): %s", raw[:500])

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
    }, warning


def _extract_json(text: str) -> str | None:
    """Extract JSON content from LLM response.

    Handles:
    - JSON wrapped in ```json ... ``` code fences
    - JSON embedded in surrounding text
    - Truncated JSON (attempts repair by closing open structures)
    """
    # 1. Try code fence first (most common format from ERNIE)
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            # Code fence content is malformed, try repair
            repaired = _repair_truncated_json(candidate)
            if repaired:
                logger.debug("Repaired JSON from code fence")
                return repaired

    # 2. Try balanced brace matching (find first valid JSON object)
    start = text.find("{")
    if start != -1:
        candidate = _extract_balanced_json(text, start)
        if candidate:
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass

        # 3. Truncated JSON — attempt repair
        repaired = _repair_truncated_json(text[start:])
        if repaired:
            logger.debug("Repaired truncated JSON (no closing braces)")
            return repaired

    return None


def _extract_balanced_json(text: str, start: int) -> str | None:
    """Extract a balanced JSON object starting at position ``start``.

    Uses brace counting with string awareness to handle nested objects correctly.
    Returns None if no matching close brace is found (truncated).
    """
    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return None  # No matching close brace (truncated)


def _repair_truncated_json(text: str) -> str | None:
    """Attempt to repair truncated JSON by closing open structures.

    Finds the last position where a complete JSON entry ends,
    then closes any remaining open arrays/objects.
    Handles trailing commas and incomplete string values.
    """
    depth_brace = 0
    depth_bracket = 0
    in_string = False
    escape = False
    candidates: list[tuple[int, int, int]] = []  # (position, brace_depth, bracket_depth)

    for i, c in enumerate(text):
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth_brace += 1
        elif c == "}":
            depth_brace -= 1
            if depth_brace >= 0:
                candidates.append((i + 1, depth_brace, depth_bracket))
        elif c == "[":
            depth_bracket += 1
        elif c == "]":
            depth_bracket -= 1

    # Try from latest (most data preserved) to earliest
    for pos, brace, bracket in reversed(candidates):
        repaired = text[:pos].rstrip()
        # Strip trailing comma
        if repaired.endswith(","):
            repaired = repaired[:-1].rstrip()
        # Close open structures
        repaired += "]" * max(0, bracket)
        repaired += "}" * max(0, brace)
        try:
            json.loads(repaired)
            return repaired
        except json.JSONDecodeError:
            continue

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
