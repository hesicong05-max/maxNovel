"""Unit tests for consistency_checker — cross-chapter validation."""

import pytest
from app.core.consistency_checker import ConsistencyChecker

checker = ConsistencyChecker()


# ─── Character consistency ────────────────────────────────────

class TestCharacterConsistency:
    def test_consistent_behavior_passes(self):
        result = checker.check_character_consistency(
            "林枫", "沉稳内敛", "林枫冷静地观察着四周，没有露出任何情绪。"
        )
        assert result["passed"] is True
        assert len(result["issues"]) == 0

    def test_沉稳_character_gets_excited_fails(self):
        result = checker.check_character_consistency(
            "林枫", "沉稳", "林枫哈哈大笑起来，激动大叫着冲了出去！"
        )
        assert result["passed"] is False
        assert len(result["issues"]) > 0

    def test_冷静_character_panics_fails(self):
        result = checker.check_character_consistency(
            "张三", "冷静", "张三慌张地四处张望，手足无措地站在原地。"
        )
        assert result["passed"] is False
        assert len(result["issues"]) > 0

    def test_different_personality_passes(self):
        result = checker.check_character_consistency(
            "李四", "热血冲动", "李四大笑着冲向敌人，毫不畏惧！"
        )
        assert result["passed"] is True


# ─── Timeline consistency ──────────────────────────────────────

class TestTimelineConsistency:
    def test_no_duplicates_passes(self):
        timeline = [{"chapter": 1, "event": "主角出发"}]
        result = checker.check_timeline_consistency(timeline, 2, ["主角到达城市"])
        assert result["passed"] is True

    def test_duplicate_event_fails(self):
        timeline = [{"chapter": 1, "event": "主角出发"}]
        result = checker.check_timeline_consistency(timeline, 2, ["主角出发"])
        assert result["passed"] is False
        assert len(result["issues"]) > 0

    def test_empty_timeline(self):
        result = checker.check_timeline_consistency([], 1, ["事件A"])
        assert result["passed"] is True


# ─── Reveal consistency ────────────────────────────────────────

class TestRevealConsistency:
    def test_no_issues_for_normal_reveal(self):
        result = checker.check_reveal_consistency(
            revealed_elements=["el_1", "el_2"],
            chapter_reveals=["el_3"],
            chapter_content="新内容",
        )
        assert result["passed"] is True
        assert result["new_reveals"] == 1

    def test_already_revealed_count(self):
        result = checker.check_reveal_consistency(
            revealed_elements=["el_1", "el_2", "el_3"],
            chapter_reveals=["el_3"],
            chapter_content="内容",
        )
        assert result["already_revealed_count"] == 2


# ─── Foreshadow status ────────────────────────────────────────

class TestForeshadowStatus:
    def test_all_resolved(self):
        foreshadows = [
            {"status": "resolved", "planted_chapter": 1, "resolve_by": 5},
            {"status": "resolved", "planted_chapter": 2, "resolve_by": 6},
        ]
        result = checker.check_foreshadow_status(foreshadows, 10)
        assert result["passed"] is True
        assert result["overdue"] == 0

    def test_overdue_by_resolve_by(self):
        foreshadows = [
            {"status": "active", "planted_chapter": 1, "resolve_by": 5},
        ]
        result = checker.check_foreshadow_status(foreshadows, 10)
        assert result["overdue"] >= 1

    def test_overdue_by_age(self):
        foreshadows = [
            {"status": "active", "planted_chapter": 1, "resolve_by": None},
        ]
        result = checker.check_foreshadow_status(foreshadows, 15)
        # Planted at chapter 1, now at 15, difference > 10
        assert result["overdue"] >= 1

    def test_active_not_overdue(self):
        foreshadows = [
            {"status": "active", "planted_chapter": 5, "resolve_by": 20},
        ]
        result = checker.check_foreshadow_status(foreshadows, 8)
        assert result["passed"] is True
        assert result["active"] == 1

    def test_empty_foreshadows(self):
        result = checker.check_foreshadow_status([], 1)
        assert result["passed"] is True
        assert result["total"] == 0


# ─── Full check ────────────────────────────────────────────────

class TestRunFullCheck:
    def test_all_passed_with_clean_data(self):
        result = checker.run_full_check(
            chapter_content="主角平静地走进了房间。",
            chapter_num=5,
            memory_data={
                "character_states": {"林枫": {"personality": "沉稳"}},
                "timeline": [],
                "revealed_elements": [],
                "foreshadows": [],
            },
            worldview_elements=[],
            chapter_reveals=[],
        )
        assert result["all_passed"] is True

    def test_fails_with_inconsistent_character(self):
        result = checker.run_full_check(
            chapter_content="林枫哈哈大笑，激动大叫！",
            chapter_num=5,
            memory_data={
                "character_states": {"林枫": {"personality": "沉稳"}},
                "timeline": [],
                "revealed_elements": [],
                "foreshadows": [],
            },
            worldview_elements=[],
            chapter_reveals=[],
        )
        assert result["all_passed"] is False

    def test_handles_empty_memory(self):
        result = checker.run_full_check(
            chapter_content="内容",
            chapter_num=1,
            memory_data={},
            worldview_elements=[],
            chapter_reveals=[],
        )
        assert result["all_passed"] is True
