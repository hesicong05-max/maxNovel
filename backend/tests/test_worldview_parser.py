"""Unit tests for worldview_parser — structured extraction with priorities."""

import pytest
from app.core.worldview_parser import WorldviewParser

parser = WorldviewParser()


@pytest.fixture
def sample_worldview():
    return {
        "characters": [
            {"name": "林枫", "personality": "沉稳内敛", "background": "没落家族", "motivation": "复兴家族", "ability": "剑灵体"},
            {"name": "苏瑶", "personality": "聪明伶俐", "background": "丹道世家", "motivation": "寻找失踪的父亲", "ability": "灵眼"},
            {"name": "黑魔", "personality": "阴狠狡诈", "background": "魔族", "motivation": "统治大陆", "ability": "暗影术"},
            {"name": "配角甲", "personality": "胆小", "background": "村民", "motivation": "生存", "ability": "无"},
        ],
        "geography": [
            {"name": "天玄大陆", "description": "故事发生的主要大陆"},
            {"name": "幽冥深渊", "description": "魔族封印之地"},
        ],
        "factions": [
            {"name": "天剑宗", "stance": "正派领袖", "power_level": "顶级"},
            {"name": "魔教", "stance": "反派势力", "power_level": "顶级"},
        ],
        "power_system": [
            {"name": "灵力修炼体系", "levels": "练气、筑基、金丹、元婴、化神", "rules": "需灵根才能修炼"},
        ],
        "history": [
            {"event": "封魔之战", "time": "三千年前", "description": "人族联合封印魔族", "impact": "千年和平"},
        ],
        "conflicts": [
            {"name": "人魔之争", "type": "种族冲突", "parties": "人族 vs 魔族", "stakes": "大陆存亡"},
        ],
        "special_settings": [
            {"name": "天劫系统", "description": "突破大境界需渡劫", "rules": "天劫强度随修为递增"},
        ],
    }


# ─── Parse ────────────────────────────────────────────────────

class TestParse:
    def test_returns_all_categories(self, sample_worldview):
        elements = parser.parse(sample_worldview)
        categories = {e["category"] for e in elements}
        assert "character" in categories
        assert "geography" in categories
        assert "faction" in categories
        assert "power_system" in categories
        assert "history" in categories
        assert "conflict" in categories
        assert "special_setting" in categories

    def test_first_character_is_core(self, sample_worldview):
        elements = parser.parse(sample_worldview)
        chars = [e for e in elements if e["category"] == "character"]
        assert chars[0]["priority"] == "core"
        assert chars[0]["name"] == "林枫"

    def test_first_geography_is_core(self, sample_worldview):
        elements = parser.parse(sample_worldview)
        geos = [e for e in elements if e["category"] == "geography"]
        assert geos[0]["priority"] == "core"

    def test_first_power_system_is_core(self, sample_worldview):
        elements = parser.parse(sample_worldview)
        pss = [e for e in elements if e["category"] == "power_system"]
        assert pss[0]["priority"] == "core"

    def test_first_conflict_is_core(self, sample_worldview):
        elements = parser.parse(sample_worldview)
        confs = [e for e in elements if e["category"] == "conflict"]
        assert confs[0]["priority"] == "core"

    def test_all_elements_have_required_fields(self, sample_worldview):
        elements = parser.parse(sample_worldview)
        for e in elements:
            assert "id" in e
            assert "category" in e
            assert "name" in e
            assert "description" in e
            assert "priority" in e
            assert "revealed" in e
            assert e["revealed"] is False

    def test_empty_worldview(self):
        elements = parser.parse({})
        assert elements == []


# ─── Element ID uniqueness ────────────────────────────────────

class TestElementIDs:
    def test_unique_ids_for_different_elements(self, sample_worldview):
        elements = parser.parse(sample_worldview)
        ids = [e["id"] for e in elements]
        assert len(ids) == len(set(ids)), "All element IDs must be unique"

    def test_same_name_different_category_has_different_id(self):
        data = {
            "characters": [{"name": "test"}],
            "geography": [{"name": "test"}],
        }
        elements = parser.parse(data)
        assert elements[0]["id"] != elements[1]["id"]


# ─── Summary ──────────────────────────────────────────────────

class TestSummary:
    def test_summary_counts(self, sample_worldview):
        elements = parser.parse(sample_worldview)
        summary = parser.summary(elements)
        assert summary["total"] == len(elements)
        assert "character" in summary["by_category"]
        assert "core" in summary["by_priority"]

    def test_empty_summary(self):
        summary = parser.summary([])
        assert summary["total"] == 0
        assert summary["by_category"] == {}


# ─── JSON extraction ──────────────────────────────────────────

class TestExtractJson:
    def test_plain_json(self):
        text = '{"key": "value"}'
        result = parser._extract_json(text)
        assert result is not None

    def test_code_fenced_json(self):
        text = '```json\n{"key": "value"}\n```'
        result = parser._extract_json(text)
        assert result is not None

    def test_json_in_text(self):
        text = 'Here is the data: {"name": "test"} and some text'
        result = parser._extract_json(text)
        assert result is not None

    def test_no_json(self):
        text = "No JSON here at all"
        result = parser._extract_json(text)
        assert result is None


# ─── Normalize extracted ───────────────────────────────────────

class TestNormalizeExtracted:
    def test_ensures_all_categories(self):
        data = {"characters": [{"name": "test"}]}
        result = parser._normalize_extracted(data)
        for key in ["characters", "geography", "factions", "power_system", "history", "conflicts", "special_settings"]:
            assert key in result

    def test_filters_invalid_items(self):
        data = {
            "characters": [{"name": "valid"}, {"no_name": "invalid"}],
            "geography": [{"name": "loc"}, {}],
        }
        result = parser._normalize_extracted(data)
        assert len(result["characters"]) == 1
        assert len(result["geography"]) == 1

    def test_fills_missing_fields(self):
        data = {"characters": [{"name": "test"}]}
        result = parser._normalize_extracted(data)
        char = result["characters"][0]
        assert char["name"] == "test"
        assert char["personality"] == ""
        assert char["background"] == ""
        assert char["relations"] == []
