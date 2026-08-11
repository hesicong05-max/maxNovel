"""Memory store — persistent story memory for cross-chapter consistency."""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.maintenance import ensure_project_writes_available
from app.core.legacy_json import read_legacy_json, read_legacy_object_list
from app.models.project import StoryMemory

logger = logging.getLogger(__name__)


class InvalidLegacyStoryMemoryError(RuntimeError):
    """Raised before generation could overwrite malformed historical memory."""


def _invalid_memory(memory: StoryMemory, field: str, category: str) -> None:
    logger.warning(
        "Invalid legacy story memory project=%s field=%s category=%s",
        memory.project_id,
        field,
        category,
    )
    raise InvalidLegacyStoryMemoryError(field)


def _object_list(memory: StoryMemory, field: str) -> list[dict[str, Any]]:
    result = read_legacy_object_list(getattr(memory, field, None))
    if not result.valid:
        _invalid_memory(memory, field, result.error_category or "invalid")
    return result.items


def _string_list(memory: StoryMemory, field: str) -> list[str]:
    result = read_legacy_json(getattr(memory, field, None))
    value = result.value
    if value is None and result.valid:
        return []
    if not result.valid:
        _invalid_memory(memory, field, result.error_category or "invalid")
    if not isinstance(value, list):
        _invalid_memory(memory, field, "not_a_list")
    if any(not isinstance(item, str) for item in value):
        _invalid_memory(memory, field, "item_not_a_string")
    return value


def _object(memory: StoryMemory, field: str) -> dict[str, Any]:
    result = read_legacy_json(getattr(memory, field, None))
    value = result.value
    if value is None and result.valid:
        return {}
    if not result.valid:
        _invalid_memory(memory, field, result.error_category or "invalid")
    if not isinstance(value, dict):
        _invalid_memory(memory, field, "not_an_object")
    return value


class MemoryStore:
    """
    Manages the persistent story memory:
      - Track revealed worldview elements
      - Track character states (location, status, relationships)
      - Track foreshadowing queue (planted → resolved)
      - Track timeline of events
      - Maintain chapter summaries for long-context management
    """

    async def get_or_create(self, db: AsyncSession, project_id: str) -> StoryMemory:
        stmt = select(StoryMemory).where(StoryMemory.project_id == project_id)
        result = await db.execute(stmt)
        memory = result.scalar_one_or_none()
        if memory:
            return memory

        ensure_project_writes_available()
        memory = StoryMemory(
            project_id=project_id,
            revealed_elements=[],
            character_states={},
            foreshadows=[],
            timeline=[],
            chapter_summaries=[],
        )
        db.add(memory)
        await (
            db.flush()
        )  # flush to get ID, but don't commit — let caller manage transaction
        return memory

    async def mark_revealed(
        self,
        db: AsyncSession,
        memory: StoryMemory,
        element_ids: list[str],
        chapter_num: int,
    ):
        """Mark elements as revealed in a specific chapter. Does NOT commit — caller is responsible."""
        ensure_project_writes_available()
        revealed = set(_string_list(memory, "revealed_elements"))
        for eid in element_ids:
            revealed.add(eid)
        memory.revealed_elements = list(revealed)

    async def update_character_state(
        self,
        db: AsyncSession,
        memory: StoryMemory,
        char_name: str,
        state: dict[str, Any],
    ):
        """Update a character's current state. Does NOT commit — caller is responsible."""
        ensure_project_writes_available()
        states = _object(memory, "character_states")
        states[char_name] = state
        memory.character_states = states

    async def plant_foreshadow(
        self,
        db: AsyncSession,
        memory: StoryMemory,
        description: str,
        planted_chapter: int,
        resolve_by: int | None = None,
    ):
        """Plant a new foreshadow. Does NOT commit — caller is responsible."""
        ensure_project_writes_available()
        foreshadows = _object_list(memory, "foreshadows")
        fs_id = f"fs_{len(foreshadows) + 1}_{planted_chapter}"
        foreshadows.append(
            {
                "id": fs_id,
                "description": description,
                "planted_chapter": planted_chapter,
                "status": "planted",  # planted / strengthened / resolved
                "resolve_by": resolve_by,
            }
        )
        memory.foreshadows = foreshadows

    async def resolve_foreshadow(
        self, db: AsyncSession, memory: StoryMemory, fs_id: str, chapter_num: int
    ):
        """Mark a foreshadow as resolved. Does NOT commit — caller is responsible."""
        ensure_project_writes_available()
        foreshadows = _object_list(memory, "foreshadows")
        for fs in foreshadows:
            if fs["id"] == fs_id:
                fs["status"] = "resolved"
                fs["resolved_chapter"] = chapter_num
                break
        memory.foreshadows = foreshadows

    async def add_timeline_event(
        self,
        db: AsyncSession,
        memory: StoryMemory,
        chapter: int,
        event: str,
        description: str,
    ):
        """Add or replace a chapter event. Does NOT commit — caller is responsible."""
        ensure_project_writes_available()
        timeline = _object_list(memory, "timeline")
        timeline = [
            item
            for item in timeline
            if not (item.get("chapter") == chapter and item.get("event") == event)
        ]
        timeline.append(
            {"chapter": chapter, "event": event, "description": description}
        )
        memory.timeline = timeline

    async def add_chapter_summary(
        self, db: AsyncSession, memory: StoryMemory, chapter_num: int, summary: str
    ):
        """Add or update a chapter summary. Does NOT commit — caller is responsible."""
        ensure_project_writes_available()
        summaries = _object_list(memory, "chapter_summaries")
        # Replace if already exists
        summaries = [s for s in summaries if s.get("chapter_num") != chapter_num]
        summaries.append({"chapter_num": chapter_num, "summary": summary})
        summaries.sort(key=lambda s: s["chapter_num"])
        memory.chapter_summaries = summaries

    async def get_context_for_chapter(
        self, memory: StoryMemory, chapter_num: int, max_summaries: int = 5
    ) -> dict[str, Any]:
        """
        Build a context summary for generating a new chapter.
        Includes recent summaries (not all, to fit context window),
        current character states, and pending foreshadows.
        """
        summaries = _object_list(memory, "chapter_summaries")
        # Get the most recent N summaries before current chapter
        relevant = [s for s in summaries if s["chapter_num"] < chapter_num][
            -max_summaries:
        ]

        # Pending foreshadows that should be resolved or strengthened
        pending_fs = [
            fs
            for fs in _object_list(memory, "foreshadows")
            if fs["status"] in ("planted", "strengthened")
            and (fs.get("resolve_by") is None or fs["resolve_by"] >= chapter_num)
        ]

        return {
            "recent_summaries": relevant,
            "character_states": _object(memory, "character_states"),
            "pending_foreshadows": pending_fs,
            "revealed_elements": _string_list(memory, "revealed_elements"),
            "timeline": _object_list(memory, "timeline")[-10:],
        }


memory_store = MemoryStore()
