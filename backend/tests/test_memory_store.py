"""Unit tests for memory_store — story memory management."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.memory_store import MemoryStore
from app.models.project import StoryMemory


def _make_memory(**kwargs) -> StoryMemory:
    """Create a StoryMemory instance with defaults."""
    return StoryMemory(
        project_id="test-project",
        revealed_elements=kwargs.get("revealed_elements", []),
        character_states=kwargs.get("character_states", {}),
        foreshadows=kwargs.get("foreshadows", []),
        timeline=kwargs.get("timeline", []),
        chapter_summaries=kwargs.get("chapter_summaries", []),
    )


# ─── get_or_create tests ─────────────────────────────────────


class TestGetOrCreate:
    @pytest.mark.asyncio
    async def test_get_existing_memory(self):
        """Should return existing memory without creating new one."""
        mock_db = AsyncMock()
        existing = _make_memory()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        mock_db.execute.return_value = mock_result

        store = MemoryStore()
        result = await store.get_or_create(mock_db, "test-project")
        assert result is existing
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_new_memory(self):
        """Should create new memory when none exists."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        store = MemoryStore()
        result = await store.get_or_create(mock_db, "test-project")
        assert isinstance(result, StoryMemory)
        assert result.project_id == "test-project"
        assert result.revealed_elements == []
        assert result.character_states == {}
        assert result.foreshadows == []
        assert result.timeline == []
        assert result.chapter_summaries == []
        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()


# ─── mark_revealed tests ──────────────────────────────────────


class TestMarkRevealed:
    @pytest.mark.asyncio
    async def test_mark_single_element(self):
        mock_db = AsyncMock()
        memory = _make_memory(revealed_elements=[])
        store = MemoryStore()
        await store.mark_revealed(mock_db, memory, ["el_1"], chapter_num=1)
        assert "el_1" in memory.revealed_elements

    @pytest.mark.asyncio
    async def test_mark_multiple_elements(self):
        mock_db = AsyncMock()
        memory = _make_memory(revealed_elements=[])
        store = MemoryStore()
        await store.mark_revealed(
            mock_db, memory, ["el_1", "el_2", "el_3"], chapter_num=2
        )
        assert len(memory.revealed_elements) == 3

    @pytest.mark.asyncio
    async def test_mark_already_revealed_no_duplicate(self):
        """Marking an already-revealed element should not create duplicates."""
        mock_db = AsyncMock()
        memory = _make_memory(revealed_elements=["el_1"])
        store = MemoryStore()
        await store.mark_revealed(mock_db, memory, ["el_1", "el_2"], chapter_num=3)
        assert memory.revealed_elements.count("el_1") == 1
        assert "el_2" in memory.revealed_elements

    @pytest.mark.asyncio
    async def test_mark_empty_list_no_change(self):
        mock_db = AsyncMock()
        memory = _make_memory(revealed_elements=["el_1"])
        store = MemoryStore()
        await store.mark_revealed(mock_db, memory, [], chapter_num=2)
        assert memory.revealed_elements == ["el_1"]

    @pytest.mark.asyncio
    async def test_mark_on_null_revealed_elements(self):
        """Should handle None revealed_elements gracefully."""
        mock_db = AsyncMock()
        memory = _make_memory()
        memory.revealed_elements = None
        store = MemoryStore()
        await store.mark_revealed(mock_db, memory, ["el_1"], chapter_num=1)
        assert "el_1" in memory.revealed_elements


# ─── update_character_state tests ────────────────────────────


class TestUpdateCharacterState:
    @pytest.mark.asyncio
    async def test_add_new_character(self):
        mock_db = AsyncMock()
        memory = _make_memory()
        store = MemoryStore()
        await store.update_character_state(
            mock_db, memory, "林远", {"location": "青云镇", "mood": "平静"}
        )
        assert "林远" in memory.character_states
        assert memory.character_states["林远"]["location"] == "青云镇"

    @pytest.mark.asyncio
    async def test_update_existing_character(self):
        mock_db = AsyncMock()
        memory = _make_memory(character_states={"林远": {"location": "青云镇"}})
        store = MemoryStore()
        await store.update_character_state(
            mock_db, memory, "林远", {"location": "天玄宗", "mood": "紧张"}
        )
        assert memory.character_states["林远"]["location"] == "天玄宗"
        assert memory.character_states["林远"]["mood"] == "紧张"

    @pytest.mark.asyncio
    async def test_multiple_characters(self):
        mock_db = AsyncMock()
        memory = _make_memory()
        store = MemoryStore()
        await store.update_character_state(
            mock_db, memory, "林远", {"location": "镇上"}
        )
        await store.update_character_state(
            mock_db, memory, "苏瑶", {"location": "宗门"}
        )
        assert len(memory.character_states) == 2

    @pytest.mark.asyncio
    async def test_update_on_null_states(self):
        mock_db = AsyncMock()
        memory = _make_memory()
        memory.character_states = None
        store = MemoryStore()
        await store.update_character_state(
            mock_db, memory, "林远", {"location": "镇上"}
        )
        assert "林远" in memory.character_states


# ─── foreshadow tests ────────────────────────────────────────


class TestForeshadow:
    @pytest.mark.asyncio
    async def test_plant_foreshadow(self):
        mock_db = AsyncMock()
        memory = _make_memory()
        store = MemoryStore()
        await store.plant_foreshadow(
            mock_db, memory, "神秘玉佩的秘密", planted_chapter=1
        )
        assert len(memory.foreshadows) == 1
        fs = memory.foreshadows[0]
        assert fs["description"] == "神秘玉佩的秘密"
        assert fs["planted_chapter"] == 1
        assert fs["status"] == "planted"
        assert "id" in fs

    @pytest.mark.asyncio
    async def test_plant_foreshadow_with_resolve_by(self):
        mock_db = AsyncMock()
        memory = _make_memory()
        store = MemoryStore()
        await store.plant_foreshadow(
            mock_db, memory, "伏笔", planted_chapter=1, resolve_by=10
        )
        assert memory.foreshadows[0]["resolve_by"] == 10

    @pytest.mark.asyncio
    async def test_plant_multiple_foreshadows(self):
        mock_db = AsyncMock()
        memory = _make_memory()
        store = MemoryStore()
        await store.plant_foreshadow(mock_db, memory, "伏笔1", planted_chapter=1)
        await store.plant_foreshadow(mock_db, memory, "伏笔2", planted_chapter=3)
        assert len(memory.foreshadows) == 2

    @pytest.mark.asyncio
    async def test_resolve_foreshadow(self):
        mock_db = AsyncMock()
        memory = _make_memory()
        store = MemoryStore()
        await store.plant_foreshadow(mock_db, memory, "伏笔1", planted_chapter=1)
        fs_id = memory.foreshadows[0]["id"]
        await store.resolve_foreshadow(mock_db, memory, fs_id, chapter_num=10)
        assert memory.foreshadows[0]["status"] == "resolved"
        assert memory.foreshadows[0]["resolved_chapter"] == 10

    @pytest.mark.asyncio
    async def test_resolve_nonexistent_foreshadow(self):
        """Resolving a non-existent foreshadow should not raise."""
        mock_db = AsyncMock()
        memory = _make_memory(
            foreshadows=[{"id": "fs_1_1", "status": "planted", "planted_chapter": 1}]
        )
        store = MemoryStore()
        await store.resolve_foreshadow(
            mock_db, memory, "nonexistent_id", chapter_num=10
        )
        # Original foreshadow should be unchanged
        assert memory.foreshadows[0]["status"] == "planted"

    @pytest.mark.asyncio
    async def test_plant_on_null_foreshadows(self):
        mock_db = AsyncMock()
        memory = _make_memory()
        memory.foreshadows = None
        store = MemoryStore()
        await store.plant_foreshadow(mock_db, memory, "伏笔", planted_chapter=1)
        assert len(memory.foreshadows) == 1


# ─── timeline tests ─────────────────────────────────────────


class TestTimeline:
    @pytest.mark.asyncio
    async def test_add_timeline_event(self):
        mock_db = AsyncMock()
        memory = _make_memory()
        store = MemoryStore()
        await store.add_timeline_event(
            mock_db, memory, chapter=1, event="主角觉醒", description="林远获得了传承"
        )
        assert len(memory.timeline) == 1
        event = memory.timeline[0]
        assert event["chapter"] == 1
        assert event["event"] == "主角觉醒"
        assert event["description"] == "林远获得了传承"

    @pytest.mark.asyncio
    async def test_add_multiple_timeline_events(self):
        mock_db = AsyncMock()
        memory = _make_memory()
        store = MemoryStore()
        for i in range(5):
            await store.add_timeline_event(
                mock_db,
                memory,
                chapter=i + 1,
                event=f"事件{i + 1}",
                description=f"描述{i + 1}",
            )
        assert len(memory.timeline) == 5

    @pytest.mark.asyncio
    async def test_same_chapter_event_is_replaced(self):
        mock_db = AsyncMock()
        memory = _make_memory()
        store = MemoryStore()
        await store.add_timeline_event(
            mock_db, memory, chapter=1, event="章节生成", description="旧摘要"
        )
        await store.add_timeline_event(
            mock_db, memory, chapter=1, event="章节生成", description="新摘要"
        )
        assert memory.timeline == [
            {
                "chapter": 1,
                "event": "章节生成",
                "description": "新摘要",
            }
        ]

    @pytest.mark.asyncio
    async def test_add_on_null_timeline(self):
        mock_db = AsyncMock()
        memory = _make_memory()
        memory.timeline = None
        store = MemoryStore()
        await store.add_timeline_event(
            mock_db, memory, chapter=1, event="事件", description="描述"
        )
        assert len(memory.timeline) == 1


# ─── chapter summary tests ───────────────────────────────────


class TestChapterSummary:
    @pytest.mark.asyncio
    async def test_add_new_summary(self):
        mock_db = AsyncMock()
        memory = _make_memory()
        store = MemoryStore()
        await store.add_chapter_summary(
            mock_db, memory, chapter_num=1, summary="第一章摘要"
        )
        assert len(memory.chapter_summaries) == 1
        assert memory.chapter_summaries[0]["chapter_num"] == 1
        assert memory.chapter_summaries[0]["summary"] == "第一章摘要"

    @pytest.mark.asyncio
    async def test_update_existing_summary(self):
        """Adding summary for same chapter should replace, not duplicate."""
        mock_db = AsyncMock()
        memory = _make_memory()
        store = MemoryStore()
        await store.add_chapter_summary(
            mock_db, memory, chapter_num=1, summary="旧摘要"
        )
        await store.add_chapter_summary(
            mock_db, memory, chapter_num=1, summary="新摘要"
        )
        assert len(memory.chapter_summaries) == 1
        assert memory.chapter_summaries[0]["summary"] == "新摘要"

    @pytest.mark.asyncio
    async def test_summaries_sorted_by_chapter(self):
        """Summaries should be sorted by chapter number."""
        mock_db = AsyncMock()
        memory = _make_memory()
        store = MemoryStore()
        await store.add_chapter_summary(mock_db, memory, chapter_num=3, summary="三")
        await store.add_chapter_summary(mock_db, memory, chapter_num=1, summary="一")
        await store.add_chapter_summary(mock_db, memory, chapter_num=2, summary="二")
        chapters = [s["chapter_num"] for s in memory.chapter_summaries]
        assert chapters == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_add_on_null_summaries(self):
        mock_db = AsyncMock()
        memory = _make_memory()
        memory.chapter_summaries = None
        store = MemoryStore()
        await store.add_chapter_summary(mock_db, memory, chapter_num=1, summary="摘要")
        assert len(memory.chapter_summaries) == 1


# ─── get_context_for_chapter tests ───────────────────────────


class TestGetContextForChapter:
    @pytest.mark.asyncio
    async def test_empty_memory_context(self):
        memory = _make_memory()
        store = MemoryStore()
        ctx = await store.get_context_for_chapter(memory, chapter_num=1)
        assert ctx["recent_summaries"] == []
        assert ctx["character_states"] == {}
        assert ctx["pending_foreshadows"] == []
        assert ctx["revealed_elements"] == []
        assert ctx["timeline"] == []

    @pytest.mark.asyncio
    async def test_returns_recent_summaries(self):
        memory = _make_memory(
            chapter_summaries=[
                {"chapter_num": 1, "summary": "一"},
                {"chapter_num": 2, "summary": "二"},
                {"chapter_num": 3, "summary": "三"},
            ]
        )
        store = MemoryStore()
        ctx = await store.get_context_for_chapter(memory, chapter_num=5)
        assert len(ctx["recent_summaries"]) == 3

    @pytest.mark.asyncio
    async def test_filters_future_summaries(self):
        """Should only return summaries before the current chapter."""
        memory = _make_memory(
            chapter_summaries=[
                {"chapter_num": 1, "summary": "一"},
                {"chapter_num": 5, "summary": "五"},
                {"chapter_num": 10, "summary": "十"},
            ]
        )
        store = MemoryStore()
        ctx = await store.get_context_for_chapter(memory, chapter_num=5)
        chapters = [s["chapter_num"] for s in ctx["recent_summaries"]]
        assert 1 in chapters
        assert 10 not in chapters  # chapter 10 is after chapter 5

    @pytest.mark.asyncio
    async def test_max_summaries_limit(self):
        """Should respect max_summaries parameter."""
        memory = _make_memory(
            chapter_summaries=[
                {"chapter_num": i, "summary": f"ch{i}"} for i in range(1, 11)
            ]
        )
        store = MemoryStore()
        ctx = await store.get_context_for_chapter(
            memory, chapter_num=15, max_summaries=3
        )
        assert len(ctx["recent_summaries"]) == 3

    @pytest.mark.asyncio
    async def test_pending_foreshadows(self):
        """Should return foreshadows that are planted/strengthened and not overdue."""
        memory = _make_memory(
            foreshadows=[
                {
                    "id": "fs_1",
                    "status": "planted",
                    "planted_chapter": 1,
                    "resolve_by": 10,
                },
                {
                    "id": "fs_2",
                    "status": "resolved",
                    "planted_chapter": 2,
                    "resolve_by": 5,
                },
                {
                    "id": "fs_3",
                    "status": "strengthened",
                    "planted_chapter": 3,
                    "resolve_by": None,
                },
            ]
        )
        store = MemoryStore()
        ctx = await store.get_context_for_chapter(memory, chapter_num=5)
        pending_ids = [fs["id"] for fs in ctx["pending_foreshadows"]]
        assert "fs_1" in pending_ids
        assert "fs_3" in pending_ids
        assert "fs_2" not in pending_ids  # resolved

    @pytest.mark.asyncio
    async def test_foreshadow_overdue_filtered(self):
        """Foreshadows past their resolve_by should not appear."""
        memory = _make_memory(
            foreshadows=[
                {
                    "id": "fs_1",
                    "status": "planted",
                    "planted_chapter": 1,
                    "resolve_by": 5,
                },
            ]
        )
        store = MemoryStore()
        ctx = await store.get_context_for_chapter(memory, chapter_num=10)
        # fs_1 has resolve_by=5, current chapter=10, so it should be filtered
        assert len(ctx["pending_foreshadows"]) == 0

    @pytest.mark.asyncio
    async def test_timeline_last_10(self):
        """Should return only last 10 timeline events."""
        memory = _make_memory(
            timeline=[
                {"chapter": i, "event": f"事件{i}", "description": ""}
                for i in range(1, 21)
            ]
        )
        store = MemoryStore()
        ctx = await store.get_context_for_chapter(memory, chapter_num=25)
        assert len(ctx["timeline"]) == 10
        assert ctx["timeline"][-1]["chapter"] == 20

    @pytest.mark.asyncio
    async def test_full_context(self):
        """Integration: all context components present together."""
        memory = _make_memory(
            revealed_elements=["el_1", "el_2"],
            character_states={"林远": {"location": "宗门"}},
            foreshadows=[
                {
                    "id": "fs_1",
                    "status": "planted",
                    "planted_chapter": 1,
                    "resolve_by": None,
                }
            ],
            timeline=[{"chapter": 1, "event": "出发", "description": ""}],
            chapter_summaries=[{"chapter_num": 1, "summary": "第一章"}],
        )
        store = MemoryStore()
        ctx = await store.get_context_for_chapter(memory, chapter_num=3)
        assert len(ctx["recent_summaries"]) == 1
        assert "林远" in ctx["character_states"]
        assert len(ctx["pending_foreshadows"]) == 1
        assert len(ctx["revealed_elements"]) == 2
        assert len(ctx["timeline"]) == 1
