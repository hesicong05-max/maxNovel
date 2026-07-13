"""Unit tests for pacing_planner — the progressive reveal engine."""

import pytest
from app.core.pacing_planner import PacingPlanner

planner = PacingPlanner()


# ─── Phase bounds ──────────────────────────────────────────────

class TestPhaseBounds:
    def test_10_chapters(self):
        bounds = planner._phase_bounds(10)
        assert bounds["introduction"][0] == 1
        assert bounds["deepening"][1] == 10

    def test_30_chapters(self):
        bounds = planner._phase_bounds(30)
        assert bounds["introduction"][1] == 3  # max(3, 30//10) = 3
        assert bounds["expansion"][1] == 15   # max(3+4, 30//2) = 15
        assert bounds["deepening"][1] == 30

    def test_50_chapters(self):
        bounds = planner._phase_bounds(50)
        assert bounds["introduction"][1] == 5  # max(3, 50//10) = 5
        assert bounds["expansion"][1] == 25   # max(5+4, 50//2) = 25

    def test_3_chapters_min(self):
        bounds = planner._phase_bounds(3)
        assert bounds["introduction"][1] == 3  # max(3, 3//10) = 3


# ─── Phase identification ─────────────────────────────────────

class TestPhaseFor:
    def test_chapter_1_is_introduction(self):
        assert planner._phase_for(1, 30) == "introduction"

    def test_mid_chapter_is_expansion(self):
        assert planner._phase_for(10, 30) == "expansion"

    def test_late_chapter_is_deepening(self):
        assert planner._phase_for(25, 30) == "deepening"

    def test_last_chapter_is_deepening(self):
        assert planner._phase_for(30, 30) == "deepening"


# ─── Phase reveal targets ─────────────────────────────────────

class TestPhaseRevealTarget:
    def test_introduction_15_percent(self):
        assert planner._phase_reveal_target("introduction") == 0.15

    def test_expansion_40_percent(self):
        assert planner._phase_reveal_target("expansion") == 0.40

    def test_deepening_100_percent(self):
        assert planner._phase_reveal_target("deepening") == 1.0


# ─── Max per chapter ──────────────────────────────────────────

class TestMaxPerChapter:
    def test_introduction_conservative(self):
        result = planner._phase_max_per_chapter("introduction", 20)
        assert result <= 3

    def test_expansion_moderate(self):
        result = planner._phase_max_per_chapter("expansion", 20)
        assert result <= 5

    def test_deepening_aggressive(self):
        result = planner._phase_max_per_chapter("deepening", 20)
        assert result <= 6

    def test_zero_elements(self):
        assert planner._phase_max_per_chapter("introduction", 0) == 2


# ─── Full plan ────────────────────────────────────────────────

class TestPlan:
    @pytest.fixture
    def sample_elements(self):
        return [
            {"id": "char_1", "category": "character", "name": "主角", "priority": "core"},
            {"id": "char_2", "category": "character", "name": "反派", "priority": "important"},
            {"id": "geo_1", "category": "geography", "name": "大陆", "priority": "core"},
            {"id": "fac_1", "category": "faction", "name": "宗门", "priority": "important"},
            {"id": "ps_1", "category": "power_system", "name": "修炼体系", "priority": "core"},
            {"id": "hist_1", "category": "history", "name": "古战", "priority": "secondary"},
            {"id": "conf_1", "category": "conflict", "name": "血仇", "priority": "core"},
            {"id": "ss_1", "category": "special_setting", "name": "秘境", "priority": "secondary"},
        ]

    def test_all_elements_assigned(self, sample_elements):
        plan = planner.plan(sample_elements, 30, 2000)
        assigned_ids = set()
        for ch in plan:
            assigned_ids.update(ch["elements"])
        assert len(assigned_ids) == len(sample_elements)

    def test_all_chapters_present(self, sample_elements):
        plan = planner.plan(sample_elements, 30, 2000)
        assert len(plan) == 30
        for i, ch in enumerate(plan):
            assert ch["chapter"] == i + 1

    def test_last_chapter_catches_all_remaining(self, sample_elements):
        plan = planner.plan(sample_elements, 30, 2000)
        # Last chapter should have all remaining elements
        total_assigned = sum(len(ch["elements"]) for ch in plan)
        assert total_assigned == len(sample_elements)

    def test_empty_elements(self):
        plan = planner.plan([], 10, 2000)
        assert len(plan) == 10
        for ch in plan:
            assert ch["elements"] == []

    def test_priority_ordering(self, sample_elements):
        plan = planner.plan(sample_elements, 30, 2000)
        # First chapter should have core elements first
        first_chapter_elements = plan[0]["elements"]
        el_map = {e["id"]: e for e in sample_elements}
        priorities = [el_map[eid]["priority"] for eid in first_chapter_elements if eid in el_map]
        # Core should come before important
        if "important" in priorities and "core" in priorities:
            assert priorities.index("core") < priorities.index("important")


# ─── Reveal density validation ────────────────────────────────

class TestValidateRevealDensity:
    def test_normal_content(self):
        content = "主角走进了森林，看到一只小兔子。他微笑着继续前行。"
        result = planner.validate_reveal_density(content)
        assert result["exceeded"] is False
        assert result["threshold"] == 0.20

    def test_setting_heavy_content(self):
        content = "修炼体系分为九个境界等级。每个境界都有严格的规则。" * 20
        result = planner.validate_reveal_density(content)
        assert result["setting_density"] > 0

    def test_empty_content(self):
        result = planner.validate_reveal_density("")
        assert result["exceeded"] is False
        assert result["total_sentences"] == 0


# ─── Phase labels ─────────────────────────────────────────────

class TestPhaseLabel:
    def test_introduction_label(self):
        assert planner.get_phase_label("introduction") == "引入期"

    def test_expansion_label(self):
        assert planner.get_phase_label("expansion") == "展开期"

    def test_deepening_label(self):
        assert planner.get_phase_label("deepening") == "深入期"

    def test_unknown_phase(self):
        assert planner.get_phase_label("unknown") == "unknown"
