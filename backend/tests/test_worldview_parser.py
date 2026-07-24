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


# ─── Document truncation ──────────────────────────────────────

class TestDocumentTruncation:
    def test_long_document_truncated_in_prompt(self):
        from app.prompts.templates import build_worldview_extraction_prompt

        long_text = "这是一个测试文档。" * 3000  # ~18k chars
        messages = build_worldview_extraction_prompt(long_text, genre="玄幻")
        user_msg = messages[1]["content"]
        # Should contain truncation notice and not exceed a reasonable length
        assert "以上为前 6000 字符" in user_msg
        assert len(user_msg) < len(long_text) + 200

    def test_short_document_not_truncated(self):
        from app.prompts.templates import build_worldview_extraction_prompt

        short_text = "这是一个短文档。"
        messages = build_worldview_extraction_prompt(short_text, genre="玄幻")
        user_msg = messages[1]["content"]
        assert "以上为前 6000 字符" not in user_msg
        assert short_text in user_msg


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

    def test_relations_string_list_converted_to_dict(self):
        """LLM may return relations as list[str] like '程子霄（父亲）'."""
        data = {
            "characters": [
                {"name": "方天时", "relations": ["程子霄（父亲）", "姥姥", "姥爷"]},
            ],
            "factions": [
                {"name": "天剑宗", "relations": ["魔教（敌对）", "青云宗:同盟"]},
            ],
        }
        result = parser._normalize_extracted(data)
        char_rels = result["characters"][0]["relations"]
        assert char_rels == [
            {"name": "程子霄", "relation": "父亲"},
            {"name": "姥姥", "relation": ""},
            {"name": "姥爷", "relation": ""},
        ]
        fac_rels = result["factions"][0]["relations"]
        assert fac_rels == [
            {"name": "魔教", "relation": "敌对"},
            {"name": "青云宗", "relation": "同盟"},
        ]

    def test_relations_dict_list_preserved(self):
        """Already-correct dict list should pass through unchanged."""
        data = {
            "characters": [
                {"name": "林远", "relations": [{"name": "苏瑶", "relation": "战友"}]},
            ],
        }
        result = parser._normalize_extracted(data)
        assert result["characters"][0]["relations"] == [{"name": "苏瑶", "relation": "战友"}]

    def test_relations_invalid_values_normalized(self):
        """None or non-list relations become empty list."""
        data = {
            "characters": [
                {"name": "A", "relations": None},
                {"name": "B", "relations": "not-a-list"},
            ],
        }
        result = parser._normalize_extracted(data)
        assert result["characters"][0]["relations"] == []
        assert result["characters"][1]["relations"] == []

    def test_conflicts_parties_list_converted_to_string(self):
        """LLM may return parties as list[str]; schema expects str."""
        data = {
            "conflicts": [
                {
                    "name": "家族矛盾",
                    "type": "家族恩怨",
                    "parties": ["方天时", "程子霄", "方天时的姥爷"],
                    "stakes": "家族传承",
                    "resolution_hint": ["和解", "决裂"],
                },
            ],
        }
        result = parser._normalize_extracted(data)
        conflict = result["conflicts"][0]
        assert conflict["parties"] == "方天时、程子霄、方天时的姥爷"
        assert conflict["stakes"] == "家族传承"
        assert conflict["resolution_hint"] == "和解、决裂"
        assert conflict["type"] == "家族恩怨"

    def test_conflicts_parties_string_preserved(self):
        """String parties should pass through unchanged."""
        data = {
            "conflicts": [
                {
                    "name": "正邪之战",
                    "parties": "正派联盟 vs 魔教",
                    "stakes": "天下苍生",
                },
            ],
        }
        result = parser._normalize_extracted(data)
        assert result["conflicts"][0]["parties"] == "正派联盟 vs 魔教"

    def test_conflicts_none_fields_become_empty_string(self):
        """Missing or None conflict fields become ''."""
        data = {"conflicts": [{"name": "测试矛盾"}]}
        result = parser._normalize_extracted(data)
        conflict = result["conflicts"][0]
        assert conflict["type"] == ""
        assert conflict["parties"] == ""
        assert conflict["stakes"] == ""
        assert conflict["resolution_hint"] == ""

    def test_validates_against_import_schema(self):
        """Normalized output must be accepted by WorldviewImportResponse."""
        from app.schemas.models import WorldviewImportResponse

        data = {
            "characters": [
                {"name": "程子霄", "relations": ["程子霄（父亲）", "姥姥"]},
                {"name": "方天时", "relations": [{"name": "程子霄", "relation": "父子"}]},
            ],
            "conflicts": [
                {"name": "家族矛盾", "parties": ["方天时", "程子霄"]},
            ],
        }
        normalized = parser._normalize_extracted(data)
        response = WorldviewImportResponse(**normalized)
        assert len(response.characters) == 2
        assert len(response.conflicts) == 1
        assert response.conflicts[0].parties == "方天时、程子霄"
