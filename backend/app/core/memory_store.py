"""Memory store — persistent story memory for cross-chapter consistency."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import StoryMemory


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

        memory = StoryMemory(
            project_id=project_id,
            revealed_elements=[],
            character_states={},
            foreshadows=[],
            timeline=[],
            chapter_summaries=[],
        )
        db.add(memory)
        await db.commit()
        await db.refresh(memory)
        return memory

    async def mark_revealed(self, db: AsyncSession, memory: StoryMemory, element_ids: list[str], chapter_num: int):
        """Mark elements as revealed in a specific chapter. Does NOT commit — caller is responsible."""
        revealed = set(memory.revealed_elements or [])
        for eid in element_ids:
            revealed.add(eid)
        memory.revealed_elements = list(revealed)

    async def update_character_state(self, db: AsyncSession, memory: StoryMemory,
                                      char_name: str, state: dict[str, Any]):
        """Update a character's current state. Does NOT commit — caller is responsible."""
        states = dict(memory.character_states or {})
        states[char_name] = state
        memory.character_states = states

    async def plant_foreshadow(self, db: AsyncSession, memory: StoryMemory,
                               description: str, planted_chapter: int,
                               resolve_by: int | None = None):
        """Plant a new foreshadow. Does NOT commit — caller is responsible."""
        foreshadows = list(memory.foreshadows or [])
        fs_id = f"fs_{len(foreshadows) + 1}_{planted_chapter}"
        foreshadows.append({
            "id": fs_id,
            "description": description,
            "planted_chapter": planted_chapter,
            "status": "planted",  # planted / strengthened / resolved
            "resolve_by": resolve_by,
        })
        memory.foreshadows = foreshadows

    async def resolve_foreshadow(self, db: AsyncSession, memory: StoryMemory, fs_id: str, chapter_num: int):
        """Mark a foreshadow as resolved. Does NOT commit — caller is responsible."""
        foreshadows = list(memory.foreshadows or [])
        for fs in foreshadows:
            if fs["id"] == fs_id:
                fs["status"] = "resolved"
                fs["resolved_chapter"] = chapter_num
                break
        memory.foreshadows = foreshadows

    async def add_timeline_event(self, db: AsyncSession, memory: StoryMemory,
                                 chapter: int, event: str, description: str):
        """Add an event to the story timeline. Does NOT commit — caller is responsible."""
        timeline = list(memory.timeline or [])
        timeline.append({"chapter": chapter, "event": event, "description": description})
        memory.timeline = timeline

    async def add_chapter_summary(self, db: AsyncSession, memory: StoryMemory,
                                  chapter_num: int, summary: str):
        """Add or update a chapter summary. Does NOT commit — caller is responsible."""
        summaries = list(memory.chapter_summaries or [])
        # Replace if already exists
        summaries = [s for s in summaries if s.get("chapter_num") != chapter_num]
        summaries.append({"chapter_num": chapter_num, "summary": summary})
        summaries.sort(key=lambda s: s["chapter_num"])
        memory.chapter_summaries = summaries

    async def get_context_for_chapter(self, memory: StoryMemory, chapter_num: int,
                                      max_summaries: int = 5) -> dict[str, Any]:
        """
        Build a context summary for generating a new chapter.
        Includes recent summaries (not all, to fit context window),
        current character states, and pending foreshadows.
        """
        summaries = list(memory.chapter_summaries or [])
        # Get the most recent N summaries before current chapter
        relevant = [s for s in summaries if s["chapter_num"] < chapter_num][-max_summaries:]

        # Pending foreshadows that should be resolved or strengthened
        pending_fs = [
            fs for fs in (memory.foreshadows or [])
            if fs["status"] in ("planted", "strengthened")
            and (fs.get("resolve_by") is None or fs["resolve_by"] >= chapter_num)
        ]

        return {
            "recent_summaries": relevant,
            "character_states": memory.character_states or {},
            "pending_foreshadows": pending_fs,
            "revealed_elements": memory.revealed_elements or [],
            "timeline": (memory.timeline or [])[-10:],  # Last 10 events
        }


memory_store = MemoryStore()
