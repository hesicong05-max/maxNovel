"""Comprehensive tests for outline generation — parsing, normalization, and API integration.

Covers:
- Normal flow (mock LLM returns valid JSON)
- Edge cases (missing fields, type mismatches, wrong chapter count)
- Exception input (non-JSON response, empty response, garbage)
- Parity with worldview import bug patterns (list instead of str, etc.)
"""

import json
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy import select

from app.api.outline import (
    OUTLINE_MAX_TOKENS,
    _extract_json,
    _normalize_chapter,
    _normalize_outline_data,
    _normalize_reveal_plan,
    _derive_reveal_plan_from_chapters,
    _parse_outline_response,
    _to_int,
    _to_list,
    _to_str,
)
from app.models.project import Outline


# ════════════════════════════════════════════════════════════
# Unit tests: helper functions
# ════════════════════════════════════════════════════════════


class TestToStr:
    def test_string_passthrough(self):
        assert _to_str("hello") == "hello"

    def test_list_joined(self):
        assert _to_str(["a", "b", "c"]) == "a、b、c"

    def test_list_with_none_items(self):
        assert _to_str(["a", None, "c"]) == "a、c"

    def test_none_returns_empty(self):
        assert _to_str(None) == ""

    def test_int_to_string(self):
        assert _to_str(42) == "42"

    def test_custom_separator(self):
        assert _to_str(["a", "b"], separator=", ") == "a, b"


class TestToList:
    def test_list_passthrough(self):
        assert _to_list(["a", "b"]) == ["a", "b"]

    def test_string_split_comma(self):
        assert _to_list("事件1，事件2，事件3") == ["事件1", "事件2", "事件3"]

    def test_string_split_newline(self):
        assert _to_list("line1\nline2") == ["line1", "line2"]

    def test_string_split_mixed(self):
        assert _to_list("a, b、c；d") == ["a", "b", "c", "d"]

    def test_none_returns_empty(self):
        assert _to_list(None) == []

    def test_single_string_no_delimiter(self):
        assert _to_list("single") == ["single"]

    def test_empty_string_returns_empty(self):
        assert _to_list("") == []

    def test_int_to_list(self):
        assert _to_list(42) == ["42"]


class TestToInt:
    def test_int_passthrough(self):
        assert _to_int(5) == 5

    def test_string_to_int(self):
        assert _to_int("7") == 7

    def test_float_to_int(self):
        assert _to_int(3.9) == 3

    def test_string_with_extracted_number(self):
        assert _to_int("第3章") == 3

    def test_none_returns_default(self):
        assert _to_int(None, default=99) == 99

    def test_garbage_returns_default(self):
        assert _to_int("abc", default=99) == 99

    def test_string_float(self):
        assert _to_int("3.9") == 3


class TestExtractJson:
    def test_plain_json(self):
        assert _extract_json('{"key": "value"}') is not None

    def test_code_fenced_json(self):
        text = '```json\n{"key": "value"}\n```'
        assert _extract_json(text) is not None

    def test_json_in_text(self):
        text = 'Here is the data: {"name": "test"} and some text'
        result = _extract_json(text)
        assert result is not None
        assert '"name"' in result

    def test_no_json(self):
        assert _extract_json("No JSON here at all") is None

    def test_empty_string(self):
        assert _extract_json("") is None


# ════════════════════════════════════════════════════════════
# Unit tests: element normalization (worldview_parser.normalize_elements)
# ════════════════════════════════════════════════════════════

from app.core.worldview_parser import worldview_parser


class TestNormalizeElements:
    def test_none_returns_empty(self):
        assert worldview_parser.normalize_elements(None) == []

    def test_empty_list_returns_empty(self):
        assert worldview_parser.normalize_elements([]) == []

    def test_dict_raw_worldview_gets_reparsed(self):
        """A raw worldview dict {characters: [...], ...} should be re-parsed into element list."""
        raw = {
            "characters": [{"name": "林远", "personality": "冷静"}],
            "geography": [{"name": "天玄大陆", "description": "东方大陆"}],
            "power_system": [{"name": "灵气体系", "rules": "分九境"}],
        }
        result = worldview_parser.normalize_elements(raw)
        assert isinstance(result, list)
        assert len(result) >= 3  # at least 1 char + 1 geo + 1 power_system
        # All items should be dicts with required keys
        for e in result:
            assert "id" in e
            assert "priority" in e
            assert "category" in e
            assert "name" in e

    def test_list_of_dicts_passthrough(self):
        """A list of properly formatted dicts should pass through with missing fields filled."""
        raw = [
            {"id": "el1", "category": "character", "name": "林远", "priority": "core"},
            {"id": "el2", "category": "geography", "name": "天玄大陆", "priority": "important"},
        ]
        result = worldview_parser.normalize_elements(raw)
        assert len(result) == 2
        assert result[0]["id"] == "el1"
        assert result[0]["priority"] == "core"

    def test_list_of_dicts_fills_missing_fields(self):
        """Dicts missing required fields should get defaults."""
        raw = [{"name": "测试要素"}]
        result = worldview_parser.normalize_elements(raw)
        assert len(result) == 1
        assert "id" in result[0]
        assert result[0]["priority"] == "secondary"
        assert result[0]["category"] == "unknown"

    def test_list_of_strings_converted_to_dicts(self):
        """A list of strings should be converted to basic element dicts."""
        raw = ["角色名", "地点名", "体系名"]
        result = worldview_parser.normalize_elements(raw)
        assert len(result) == 3
        for i, e in enumerate(result):
            assert e["name"] == raw[i]
            assert e["priority"] == "secondary"
            assert "id" in e
            assert "category" in e

    def test_unknown_type_returns_empty(self):
        """Non-dict, non-list types should return empty list."""
        assert worldview_parser.normalize_elements(42) == []
        assert worldview_parser.normalize_elements("string") == []


# ════════════════════════════════════════════════════════════
# Unit tests: chapter normalization
# ════════════════════════════════════════════════════════════


class TestNormalizeChapter:
    def test_valid_chapter(self):
        ch = {
            "chapter_num": 1,
            "title": "觉醒",
            "summary": "主角觉醒",
            "key_events": ["事件1", "事件2"],
            "reveal_elements": ["el_1", "el_2"],
        }
        result = _normalize_chapter(ch, 1)
        assert result["chapter_num"] == 1
        assert result["title"] == "觉醒"
        assert result["summary"] == "主角觉醒"
        assert result["key_events"] == ["事件1", "事件2"]
        assert result["reveal_elements"] == ["el_1", "el_2"]

    def test_missing_fields_filled(self):
        ch = {"chapter_num": 1}
        result = _normalize_chapter(ch, 1)
        assert result["title"] == "第1章"
        assert result["summary"] == ""
        assert result["key_events"] == []
        assert result["reveal_elements"] == []

    def test_chapter_num_string_converted(self):
        """LLM may return chapter_num as string."""
        ch = {"chapter_num": "3", "title": "测试"}
        result = _normalize_chapter(ch, 3)
        assert result["chapter_num"] == 3
        assert isinstance(result["chapter_num"], int)

    def test_chapter_num_missing_uses_expected(self):
        ch = {"title": "无编号"}
        result = _normalize_chapter(ch, 5)
        assert result["chapter_num"] == 5

    def test_title_list_converted_to_string(self):
        """LLM may return title as a list — same bug pattern as worldview."""
        ch = {"chapter_num": 1, "title": ["觉醒", "初章"]}
        result = _normalize_chapter(ch, 1)
        assert result["title"] == "觉醒、初章"
        assert isinstance(result["title"], str)

    def test_summary_list_converted_to_string(self):
        ch = {"chapter_num": 1, "summary": ["第一段", "第二段"]}
        result = _normalize_chapter(ch, 1)
        assert result["summary"] == "第一段、第二段"
        assert isinstance(result["summary"], str)

    def test_key_events_string_split_to_list(self):
        """LLM may return key_events as a single string instead of a list."""
        ch = {"chapter_num": 1, "key_events": "事件1，事件2，事件3"}
        result = _normalize_chapter(ch, 1)
        assert result["key_events"] == ["事件1", "事件2", "事件3"]
        assert isinstance(result["key_events"], list)

    def test_reveal_elements_string_split_to_list(self):
        ch = {"chapter_num": 1, "reveal_elements": "el_1, el_2, el_3"}
        result = _normalize_chapter(ch, 1)
        assert result["reveal_elements"] == ["el_1", "el_2", "el_3"]
        assert isinstance(result["reveal_elements"], list)

    def test_none_fields_become_empty(self):
        ch = {"chapter_num": 1, "title": None, "summary": None,
              "key_events": None, "reveal_elements": None}
        result = _normalize_chapter(ch, 1)
        assert result["title"] == "第1章"
        assert result["summary"] == ""
        assert result["key_events"] == []
        assert result["reveal_elements"] == []

    def test_non_dict_chapter(self):
        """If LLM returns a non-dict value for a chapter, use defaults."""
        result = _normalize_chapter("garbage", 3)
        assert result["chapter_num"] == 3
        assert result["title"] == "第3章"
        assert result["summary"] == ""

    def test_title_empty_string_uses_default(self):
        ch = {"chapter_num": 1, "title": ""}
        result = _normalize_chapter(ch, 1)
        assert result["title"] == "第1章"


# ════════════════════════════════════════════════════════════
# Unit tests: full outline data normalization
# ════════════════════════════════════════════════════════════


class TestNormalizeOutlineData:
    def test_valid_data_passthrough(self):
        data = {
            "story_arc": "英雄成长弧",
            "chapters": [
                {"chapter_num": 1, "title": "第一章", "summary": "测试",
                 "key_events": ["e1"], "reveal_elements": ["r1"]},
                {"chapter_num": 2, "title": "第二章", "summary": "测试2",
                 "key_events": ["e2"], "reveal_elements": ["r2"]},
            ],
        }
        result = _normalize_outline_data(data, 2)
        assert result["story_arc"] == "英雄成长弧"
        assert len(result["chapters"]) == 2
        assert result["chapters"][0]["title"] == "第一章"

    def test_story_arc_list_converted_to_string(self):
        """LLM may return story_arc as a list — same pattern as worldview rules."""
        data = {"story_arc": ["弧线1", "弧线2"], "chapters": []}
        result = _normalize_outline_data(data, 5)
        assert result["story_arc"] == "弧线1、弧线2"
        assert isinstance(result["story_arc"], str)

    def test_story_arc_none_becomes_empty(self):
        data = {"chapters": []}
        result = _normalize_outline_data(data, 3)
        assert result["story_arc"] == ""

    def test_chapters_padded_to_expected_count(self):
        """LLM returns fewer chapters than expected."""
        data = {
            "story_arc": "弧线",
            "chapters": [
                {"chapter_num": 1, "title": "第一章"},
            ],
        }
        result = _normalize_outline_data(data, 5)
        assert len(result["chapters"]) == 5
        assert result["chapters"][0]["title"] == "第一章"
        assert result["chapters"][1]["title"] == "第2章"
        assert result["chapters"][4]["title"] == "第5章"
        assert result["chapters"][4]["summary"] == "待填充"

    def test_chapters_truncated_to_expected_count(self):
        """LLM returns more chapters than expected."""
        data = {
            "story_arc": "弧线",
            "chapters": [
                {"chapter_num": i, "title": f"第{i}章"} for i in range(1, 11)
            ],
        }
        result = _normalize_outline_data(data, 5)
        assert len(result["chapters"]) == 5
        assert result["chapters"][4]["chapter_num"] == 5

    def test_chapter_nums_renumbered_sequentially(self):
        """Chapters with non-sequential nums get remapped to 1, 2, 3..."""
        data = {
            "story_arc": "",
            "chapters": [
                {"chapter_num": 10, "title": "A"},
                {"chapter_num": 20, "title": "B"},
            ],
        }
        result = _normalize_outline_data(data, 5)
        assert len(result["chapters"]) == 5
        # Chapter 1 gets the first LLM chapter content but with num=1
        assert result["chapters"][0]["chapter_num"] == 1
        assert result["chapters"][0]["title"] == "A"
        # Chapter 2 gets the second LLM chapter content but with num=2
        assert result["chapters"][1]["chapter_num"] == 2
        assert result["chapters"][1]["title"] == "B"
        # Chapters 3-5 are padded defaults
        assert result["chapters"][2]["chapter_num"] == 3
        assert result["chapters"][2]["title"] == "第3章"

    def test_duplicate_chapter_nums_deduped(self):
        """LLM returns duplicate chapter_num — only first kept."""
        data = {
            "story_arc": "",
            "chapters": [
                {"chapter_num": 1, "title": "第一"},
                {"chapter_num": 1, "title": "重复"},
                {"chapter_num": 2, "title": "第二"},
            ],
        }
        result = _normalize_outline_data(data, 2)
        assert len(result["chapters"]) == 2
        assert result["chapters"][0]["title"] == "第一"
        assert result["chapters"][1]["title"] == "第二"

    def test_chapters_not_list_becomes_empty(self):
        """LLM returns chapters as a non-list value."""
        data = {"story_arc": "arc", "chapters": "not a list"}
        result = _normalize_outline_data(data, 3)
        assert len(result["chapters"]) == 3
        assert all(c["summary"] == "待填充" for c in result["chapters"])

    def test_chapters_missing_key(self):
        data = {"story_arc": "arc"}
        result = _normalize_outline_data(data, 2)
        assert len(result["chapters"]) == 2

    def test_all_fields_list_types_normalized(self):
        """Comprehensive: all fields that LLM might return as lists."""
        data = {
            "story_arc": ["弧线A", "弧线B"],
            "chapters": [
                {
                    "chapter_num": "1",
                    "title": ["标题1", "标题2"],
                    "summary": ["段1", "段2"],
                    "key_events": "事件1，事件2",
                    "reveal_elements": "el_1, el_2",
                },
            ],
        }
        result = _normalize_outline_data(data, 1)
        ch = result["chapters"][0]
        assert isinstance(result["story_arc"], str)
        assert isinstance(ch["chapter_num"], int)
        assert isinstance(ch["title"], str)
        assert isinstance(ch["summary"], str)
        assert isinstance(ch["key_events"], list)
        assert isinstance(ch["reveal_elements"], list)
        assert ch["chapter_num"] == 1
        assert ch["title"] == "标题1、标题2"
        assert ch["summary"] == "段1、段2"
        assert ch["key_events"] == ["事件1", "事件2"]
        assert ch["reveal_elements"] == ["el_1", "el_2"]


# ════════════════════════════════════════════════════════════
# Unit tests: reveal_plan normalization (LLM-generated)
# ════════════════════════════════════════════════════════════


class TestNormalizeRevealPlan:
    def test_valid_reveal_plan_passthrough(self):
        """A well-formed LLM-generated reveal_plan passes through normalized."""
        raw = [
            {"chapter": 1, "phase": "起势", "elements": ["灵气体系"], "summary": "开场"},
            {"chapter": 2, "phase": "暗涌", "elements": ["主角身世"], "summary": "发现秘密"},
        ]
        result = _normalize_reveal_plan(raw, 2)
        assert len(result) == 2
        assert result[0]["chapter"] == 1
        assert result[0]["phase"] == "起势"
        assert result[0]["elements"] == ["灵气体系"]
        assert result[0]["summary"] == "开场"

    def test_missing_chapters_filled(self):
        """Missing chapter entries are filled with defaults."""
        raw = [
            {"chapter": 1, "phase": "起势", "elements": ["el1"], "summary": ""},
            {"chapter": 3, "phase": "爆发", "elements": ["el3"], "summary": ""},
        ]
        result = _normalize_reveal_plan(raw, 5)
        assert len(result) == 5
        assert result[0]["chapter"] == 1
        assert result[1]["chapter"] == 2
        assert result[1]["phase"] == "推进"  # default
        assert result[1]["elements"] == []
        assert result[2]["chapter"] == 3
        assert result[2]["phase"] == "爆发"
        assert result[3]["chapter"] == 4
        assert result[4]["chapter"] == 5

    def test_missing_fields_get_defaults(self):
        """Entries with missing fields get defaults."""
        raw = [{"chapter": 1}]  # Missing phase, elements, summary
        result = _normalize_reveal_plan(raw, 1)
        assert len(result) == 1
        assert result[0]["phase"] == "推进"
        assert result[0]["elements"] == []
        assert result[0]["summary"] == ""

    def test_chapter_num_as_string_converted(self):
        raw = [{"chapter": "3", "phase": "爆发", "elements": ["el1"], "summary": "test"}]
        result = _normalize_reveal_plan(raw, 5)
        assert len(result) == 5
        assert result[2]["chapter"] == 3
        assert isinstance(result[2]["chapter"], int)

    def test_elements_string_split_to_list(self):
        raw = [{"chapter": 1, "phase": "起势", "elements": "el1, el2, el3", "summary": ""}]
        result = _normalize_reveal_plan(raw, 1)
        assert result[0]["elements"] == ["el1", "el2", "el3"]

    def test_non_list_input_returns_empty(self):
        assert _normalize_reveal_plan("not a list", 5) == []
        assert _normalize_reveal_plan(None, 5) == []
        assert _normalize_reveal_plan({}, 5) == []

    def test_non_dict_entries_skipped(self):
        raw = ["garbage", {"chapter": 1, "phase": "test", "elements": [], "summary": ""}, 42]
        result = _normalize_reveal_plan(raw, 3)
        assert len(result) == 3
        assert result[0]["phase"] == "test"
        assert result[1]["phase"] == "推进"  # default

    def test_zero_or_negative_chapter_skipped(self):
        raw = [
            {"chapter": 0, "phase": "invalid", "elements": [], "summary": ""},
            {"chapter": -1, "phase": "invalid", "elements": [], "summary": ""},
            {"chapter": 1, "phase": "valid", "elements": ["el1"], "summary": ""},
        ]
        result = _normalize_reveal_plan(raw, 3)
        assert len(result) == 3
        assert result[0]["phase"] == "valid"
        assert result[1]["phase"] == "推进"  # default


class TestDeriveRevealPlanFromChapters:
    def test_derive_from_chapter_reveal_elements(self):
        """Derive reveal_plan from chapters' reveal_elements."""
        chapters = [
            {"chapter_num": 1, "reveal_elements": ["灵气体系", "主角身世"], "summary": "ch1"},
            {"chapter_num": 2, "reveal_elements": ["势力关系"], "summary": "ch2"},
            {"chapter_num": 3, "reveal_elements": [], "summary": "ch3"},
        ]
        result = _derive_reveal_plan_from_chapters(chapters)
        assert len(result) == 3
        assert result[0]["chapter"] == 1
        assert result[0]["elements"] == ["灵气体系", "主角身世"]
        assert result[0]["summary"] == "ch1"
        assert result[1]["chapter"] == 2
        assert result[1]["elements"] == ["势力关系"]
        assert result[2]["chapter"] == 3
        assert result[2]["elements"] == []

    def test_uses_chapter_phase_if_present(self):
        chapters = [
            {"chapter_num": 1, "phase": "起势", "reveal_elements": ["el1"], "summary": ""},
        ]
        result = _derive_reveal_plan_from_chapters(chapters)
        assert result[0]["phase"] == "起势"

    def test_default_phase_when_missing(self):
        chapters = [
            {"chapter_num": 1, "reveal_elements": [], "summary": ""},
        ]
        result = _derive_reveal_plan_from_chapters(chapters)
        assert result[0]["phase"] == "推进"

    def test_sorted_by_chapter_num(self):
        chapters = [
            {"chapter_num": 3, "reveal_elements": [], "summary": ""},
            {"chapter_num": 1, "reveal_elements": [], "summary": ""},
            {"chapter_num": 2, "reveal_elements": [], "summary": ""},
        ]
        result = _derive_reveal_plan_from_chapters(chapters)
        assert result[0]["chapter"] == 1
        assert result[1]["chapter"] == 2
        assert result[2]["chapter"] == 3

    def test_empty_chapters_returns_empty(self):
        assert _derive_reveal_plan_from_chapters([]) == []

    def test_non_list_returns_empty(self):
        assert _derive_reveal_plan_from_chapters("not a list") == []
        assert _derive_reveal_plan_from_chapters(None) == []

    def test_non_dict_chapters_skipped(self):
        chapters = [
            "garbage",
            {"chapter_num": 1, "reveal_elements": ["el1"], "summary": ""},
            42,
        ]
        result = _derive_reveal_plan_from_chapters(chapters)
        assert len(result) == 1
        assert result[0]["chapter"] == 1

    def test_reveal_elements_string_split(self):
        chapters = [
            {"chapter_num": 1, "reveal_elements": "el1, el2, el3", "summary": ""},
        ]
        result = _derive_reveal_plan_from_chapters(chapters)
        assert result[0]["elements"] == ["el1", "el2", "el3"]


# ════════════════════════════════════════════════════════════
# Unit tests: _parse_outline_response (full pipeline)
# ════════════════════════════════════════════════════════════


class TestParseOutlineResponse:
    def test_valid_json_with_code_fence(self):
        raw = '```json\n{"story_arc": "测试弧", "chapters": [{"chapter_num": 1, "title": "T1", "summary": "S1", "key_events": ["E1"], "reveal_elements": ["R1"]}]}\n```'
        result, warning = _parse_outline_response(raw, 1)
        assert result["story_arc"] == "测试弧"
        assert len(result["chapters"]) == 1
        assert result["chapters"][0]["title"] == "T1"
        assert warning is None

    def test_valid_raw_json(self):
        raw = '{"story_arc": "测试", "chapters": [{"chapter_num": 1, "title": "T", "summary": "S", "key_events": [], "reveal_elements": []}]}'
        result, warning = _parse_outline_response(raw, 1)
        assert result["story_arc"] == "测试"
        assert len(result["chapters"]) == 1
        assert warning is None

    def test_json_embedded_in_text(self):
        """LLM wraps JSON with explanatory text."""
        raw = 'Here is the outline:\n```json\n{"story_arc": "弧", "chapters": [{"chapter_num": 1, "title": "T", "summary": "S", "key_events": [], "reveal_elements": []}]}\n```\nLet me know!'
        result, warning = _parse_outline_response(raw, 1)
        assert result["story_arc"] == "弧"
        assert warning is None

    def test_garbage_response_uses_fallback(self):
        raw = "This is not JSON at all"
        result, warning = _parse_outline_response(raw, 5)
        assert "待填充" in result["story_arc"] or result["story_arc"] == "故事大纲生成中，请手动编辑完善"
        assert len(result["chapters"]) == 5
        assert all(c["key_events"] == [] for c in result["chapters"])
        assert warning is not None
        assert "无法解析" in warning

    def test_empty_response_uses_fallback(self):
        result, warning = _parse_outline_response("", 3)
        assert len(result["chapters"]) == 3
        assert warning is not None

    def test_malformed_json_uses_fallback(self):
        raw = '{"story_arc": "incomplete'
        result, warning = _parse_outline_response(raw, 3)
        assert len(result["chapters"]) == 3
        assert warning is not None

    def test_fewer_chapters_padded(self):
        raw = '```json\n{"story_arc": "弧", "chapters": [{"chapter_num": 1, "title": "T", "summary": "S", "key_events": [], "reveal_elements": []}]}\n```'
        result, warning = _parse_outline_response(raw, 5)
        assert len(result["chapters"]) == 5
        assert result["chapters"][0]["title"] == "T"
        assert result["chapters"][4]["title"] == "第5章"
        assert warning is not None
        assert "1 章" in warning

    def test_all_list_fields_handled(self):
        """All the bug patterns from worldview import should not crash here."""
        raw = '```json\n{"story_arc": ["弧A", "弧B"], "chapters": [{"chapter_num": "1", "title": ["T1", "T2"], "summary": ["S1"], "key_events": "E1，E2", "reveal_elements": "R1, R2"}]}\n```'
        result, warning = _parse_outline_response(raw, 1)
        assert isinstance(result["story_arc"], str)
        assert isinstance(result["chapters"][0]["chapter_num"], int)
        assert isinstance(result["chapters"][0]["title"], str)
        assert isinstance(result["chapters"][0]["summary"], str)
        assert isinstance(result["chapters"][0]["key_events"], list)
        assert isinstance(result["chapters"][0]["reveal_elements"], list)

    def test_zero_chapters(self):
        """Edge case: total_chapters = 0."""
        result, warning = _parse_outline_response('{"story_arc": "", "chapters": []}', 0)
        assert result["chapters"] == []
        assert result["story_arc"] == ""
        assert warning is None

    def test_truncated_json_repaired(self):
        """Truncated JSON (max_tokens hit mid-response) should be repaired, not discarded."""
        raw = '''```json
{
  "story_arc": "测试故事弧线",
  "chapters": [
    {"chapter_num": 1, "title": "觉醒", "summary": "主角觉醒", "key_events": ["事件1"], "reveal_elements": ["要素1"]},
    {"chapter_num": 2, "title": "初入", "summary": "主角入门", "key_events": ["事件2"], "reveal_elements": ["要素2"]},
    {"chapter_num": 3, "title": "暗流'''
        result, warning = _parse_outline_response(raw, 5)
        # Should have recovered at least 2 chapters from the truncated JSON
        assert len(result["chapters"]) == 5
        assert result["chapters"][0]["title"] == "觉醒"
        assert result["chapters"][1]["title"] == "初入"
        # Chapters 3-5 should be padded
        assert result["chapters"][2]["title"] == "第3章"
        # Warning should mention fewer chapters
        assert warning is not None

    def test_truncated_json_without_code_fence(self):
        """Truncated raw JSON (no code fence) should also be repaired."""
        raw = '{"story_arc": "弧线", "chapters": [{"chapter_num": 1, "title": "T1", "summary": "S1", "key_events": [], "reveal_elements": []}, {"chapter_num": 2, "title": "T2", "summary":'
        result, warning = _parse_outline_response(raw, 3)
        # Should recover chapter 1 at minimum
        assert len(result["chapters"]) == 3
        assert result["chapters"][0]["title"] == "T1"

    def test_json_with_curly_braces_in_text(self):
        """Text before JSON with curly braces should not break extraction."""
        raw = '这是一个{重要}的大纲：\n```json\n{"story_arc": "弧", "chapters": [{"chapter_num": 1, "title": "T", "summary": "S", "key_events": [], "reveal_elements": []}]}\n```'
        result, warning = _parse_outline_response(raw, 1)
        assert result["story_arc"] == "弧"
        assert len(result["chapters"]) == 1

    def test_parse_extracts_llm_generated_reveal_plan(self):
        """When LLM includes reveal_plan in its response, it should be extracted."""
        raw = '```json\n{"story_arc": "弧线", "reveal_plan": [{"chapter": 1, "phase": "起势", "elements": ["灵气体系"], "summary": "开场"}, {"chapter": 2, "phase": "暗涌", "elements": ["主角身世"], "summary": "发现秘密"}], "chapters": [{"chapter_num": 1, "title": "T1", "summary": "S1", "key_events": [], "reveal_elements": ["灵气体系"]}, {"chapter_num": 2, "title": "T2", "summary": "S2", "key_events": [], "reveal_elements": ["主角身世"]}]}\n```'
        result, warning = _parse_outline_response(raw, 2)
        assert "reveal_plan" in result
        assert len(result["reveal_plan"]) == 2
        assert result["reveal_plan"][0]["phase"] == "起势"
        assert result["reveal_plan"][0]["elements"] == ["灵气体系"]
        assert result["reveal_plan"][1]["phase"] == "暗涌"

    def test_parse_derives_reveal_plan_when_missing(self):
        """When LLM doesn't include reveal_plan, derive from chapters' reveal_elements."""
        raw = '```json\n{"story_arc": "弧线", "chapters": [{"chapter_num": 1, "title": "T1", "summary": "S1", "key_events": [], "reveal_elements": ["灵气体系"]}, {"chapter_num": 2, "title": "T2", "summary": "S2", "key_events": [], "reveal_elements": ["主角身世"]}]}\n```'
        result, warning = _parse_outline_response(raw, 2)
        assert "reveal_plan" in result
        assert len(result["reveal_plan"]) == 2
        assert result["reveal_plan"][0]["elements"] == ["灵气体系"]
        assert result["reveal_plan"][1]["elements"] == ["主角身世"]
        # Derived plan should have default phase
        assert result["reveal_plan"][0]["phase"] == "推进"

    def test_parse_fallback_includes_empty_reveal_plan(self):
        """Fallback outline should include empty reveal_plan."""
        result, warning = _parse_outline_response("garbage", 3)
        assert "reveal_plan" in result
        assert result["reveal_plan"] == []


# ════════════════════════════════════════════════════════════
# API integration tests
# ════════════════════════════════════════════════════════════

# Database, client, auth fixtures are shared from conftest.py


async def _create_project_with_worldview(client, auth_headers, total_chapters=5):
    """Helper: create a project and set its worldview."""
    proj = await client.post("/api/projects", json={
        "title": "大纲测试",
        "genre": "玄幻",
        "total_chapters": total_chapters,
        "chapter_word_count": 2000,
        "style_intensity": "standard",
    }, headers=auth_headers)
    pid = proj.json()["id"]
    # Set worldview
    await client.post(f"/api/worldview/{pid}", json={
        "characters": [{"name": "林远", "personality": "坚韧", "background": "",
                       "motivation": "", "ability": "", "relations": []}],
        "geography": [{"name": "大陆", "description": "", "significance": ""}],
        "factions": [], "power_system": [], "history": [],
        "conflicts": [], "special_settings": [], "source": "manual",
    }, headers=auth_headers)
    return pid


class TestRetiredOutlinePublicAPI:
    @pytest.mark.usefixtures("clean_db")
    async def test_only_legacy_read_route_remains_in_openapi(self, client):
        paths = (await client.get("/openapi.json")).json()["paths"]

        assert "/api/outline/{project_id}" in paths
        assert set(paths["/api/outline/{project_id}"]) == {"get"}
        assert "/api/outline/{project_id}/generate" not in paths
        assert "/api/outline/{project_id}/generate-stream" not in paths
        assert "/api/outline/{project_id}/diagnose" not in paths
        assert "/api/outline/{project_id}/confirm" not in paths

    @pytest.mark.usefixtures("clean_db")
    async def test_retired_routes_do_not_call_llm_or_write_outline(
        self, client, auth_headers, db_session
    ):
        pid = await _create_project_with_worldview(client, auth_headers)
        payload = {
            "story_arc": "不应写入",
            "chapters": [],
        }

        with (
            patch("app.api.outline.llm_client.chat", new_callable=AsyncMock) as chat,
            patch("app.api.outline.llm_client.chat_stream") as chat_stream,
        ):
            responses = [
                await client.post(f"/api/outline/{pid}/generate", headers=auth_headers),
                await client.post(f"/api/outline/{pid}/generate-stream", headers=auth_headers),
                await client.get(f"/api/outline/{pid}/diagnose", headers=auth_headers),
                await client.put(f"/api/outline/{pid}", json=payload, headers=auth_headers),
                await client.post(f"/api/outline/{pid}/confirm", headers=auth_headers),
            ]

        assert [response.status_code for response in responses] == [404, 404, 404, 405, 404]
        chat.assert_not_awaited()
        chat_stream.assert_not_called()
        result = await db_session.execute(select(Outline).where(Outline.project_id == pid))
        assert result.scalar_one_or_none() is None

    @pytest.mark.usefixtures("clean_db")
    async def test_legacy_outline_remains_readable(
        self, client, auth_headers, db_session
    ):
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=1)
        db_session.add(Outline(
            project_id=pid,
            story_arc="旧规划原地保留",
            chapters=[{
                "chapter_num": 1,
                "title": "旧章节",
                "summary": "旧摘要",
                "key_events": [],
                "reveal_elements": [],
            }],
            reveal_plan=[],
        ))
        await db_session.commit()

        response = await client.get(f"/api/outline/{pid}", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["story_arc"] == "旧规划原地保留"
        assert response.json()["chapters"][0]["title"] == "旧章节"


@pytest.mark.skip(reason="DEV-003D1 retired public outline generation and write APIs")
class TestOutlineAPI:
    @pytest.mark.usefixtures("clean_db")
    async def test_generate_outline_mock_llm(self, client, auth_headers):
        """Generate outline with mock LLM (no API key configured)."""
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=5)
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
            }
            resp = await client.post(f"/api/outline/{pid}/generate", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "story_arc" in data
        assert "chapters" in data
        assert "reveal_plan" in data
        assert len(data["chapters"]) == 5
        # Check each chapter has required fields with correct types
        for ch in data["chapters"]:
            assert isinstance(ch.get("chapter_num"), int)
            assert isinstance(ch.get("title"), str)
            assert isinstance(ch.get("summary"), str)
            assert isinstance(ch.get("key_events"), list)
            assert isinstance(ch.get("reveal_elements"), list)

    @pytest.mark.usefixtures("clean_db")
    async def test_generate_outline_without_worldview_400(self, client, auth_headers):
        """Cannot generate outline without worldview."""
        proj = await client.post("/api/projects", json={
            "title": "无世界观", "genre": "玄幻", "total_chapters": 5,
            "chapter_word_count": 2000, "style_intensity": "standard",
        }, headers=auth_headers)
        pid = proj.json()["id"]
        resp = await client.post(f"/api/outline/{pid}/generate", headers=auth_headers)
        assert resp.status_code == 400
        assert "世界观" in resp.json()["detail"]

    @pytest.mark.usefixtures("clean_db")
    async def test_generate_outline_without_auth_401(self, client, auth_headers):
        pid = await _create_project_with_worldview(client, auth_headers)
        resp = await client.post(f"/api/outline/{pid}/generate")
        assert resp.status_code == 401

    @pytest.mark.usefixtures("clean_db")
    async def test_generate_outline_other_user_403(self, client, auth_headers, second_auth_headers):
        """User B cannot generate outline for User A's project."""
        pid = await _create_project_with_worldview(client, auth_headers)
        resp = await client.post(f"/api/outline/{pid}/generate", headers=second_auth_headers)
        assert resp.status_code == 403

    @pytest.mark.usefixtures("clean_db")
    async def test_get_outline(self, client, auth_headers):
        """Generate outline then get it back."""
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=3)
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
            }
            gen_resp = await client.post(f"/api/outline/{pid}/generate", headers=auth_headers)
            assert gen_resp.status_code == 200
        # Get the outline
        resp = await client.get(f"/api/outline/{pid}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == pid
        assert len(data["chapters"]) == 3

    @pytest.mark.usefixtures("clean_db")
    async def test_get_outline_not_found_404(self, client, auth_headers):
        proj = await client.post("/api/projects", json={
            "title": "无大纲", "genre": "玄幻", "total_chapters": 5,
            "chapter_word_count": 2000, "style_intensity": "standard",
        }, headers=auth_headers)
        pid = proj.json()["id"]
        resp = await client.get(f"/api/outline/{pid}", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.usefixtures("clean_db")
    async def test_confirm_outline(self, client, auth_headers):
        """Generate then confirm outline."""
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=3)
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
            }
            await client.post(f"/api/outline/{pid}/generate", headers=auth_headers)
        resp = await client.post(f"/api/outline/{pid}/confirm", headers=auth_headers)
        assert resp.status_code == 200
        assert "已确认" in resp.json()["message"]

    @pytest.mark.usefixtures("clean_db")
    async def test_confirm_outline_not_found_400(self, client, auth_headers):
        proj = await client.post("/api/projects", json={
            "title": "测试", "genre": "玄幻", "total_chapters": 5,
            "chapter_word_count": 2000, "style_intensity": "standard",
        }, headers=auth_headers)
        pid = proj.json()["id"]
        resp = await client.post(f"/api/outline/{pid}/confirm", headers=auth_headers)
        assert resp.status_code == 400

    @pytest.mark.usefixtures("clean_db")
    async def test_update_outline(self, client, auth_headers):
        """Generate outline then update it."""
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=3)
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
            }
            await client.post(f"/api/outline/{pid}/generate", headers=auth_headers)
        # Update outline
        new_chapters = [
            {"chapter_num": 1, "title": "修改第一章", "summary": "修改摘要",
             "key_events": ["新事件"], "reveal_elements": ["el_new"]},
            {"chapter_num": 2, "title": "修改第二章", "summary": "修改2",
             "key_events": [], "reveal_elements": []},
            {"chapter_num": 3, "title": "修改第三章", "summary": "修改3",
             "key_events": [], "reveal_elements": []},
        ]
        resp = await client.put(f"/api/outline/{pid}", json={
            "story_arc": "修改后的弧线",
            "chapters": new_chapters,
        }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["story_arc"] == "修改后的弧线"
        assert data["chapters"][0]["title"] == "修改第一章"

    @pytest.mark.usefixtures("clean_db")
    async def test_generate_outline_llm_error_502(self, client, auth_headers):
        """When LLM fails, should return 502 with user-friendly message."""
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=3)
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "fake-key", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
            }
            with patch("app.api.outline.llm_client") as mock_llm:
                mock_llm.chat = AsyncMock(side_effect=RuntimeError("LLM 服务不可用"))
                resp = await client.post(f"/api/outline/{pid}/generate", headers=auth_headers)
        assert resp.status_code == 502
        assert "LLM" in resp.json()["detail"] or "大纲" in resp.json()["detail"]

    @pytest.mark.usefixtures("clean_db")
    async def test_generate_outline_nonexistent_project_404(self, client, auth_headers):
        resp = await client.post("/api/outline/nonexistent-id/generate", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.usefixtures("clean_db")
    async def test_regenerate_outline_overwrites(self, client, auth_headers):
        """Regenerating outline should overwrite the previous one."""
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=3)
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
            }
            # First generation
            resp1 = await client.post(f"/api/outline/{pid}/generate", headers=auth_headers)
            assert resp1.status_code == 200
            outline_id_1 = resp1.json()["id"]
            # Second generation (should overwrite)
            resp2 = await client.post(f"/api/outline/{pid}/generate", headers=auth_headers)
            assert resp2.status_code == 200
            # Should be a new outline (old one deleted)
            assert resp2.json()["id"] != outline_id_1

    @pytest.mark.usefixtures("clean_db")
    async def test_outline_chapter_count_matches_project(self, client, auth_headers):
        """Outline chapter count must match project.total_chapters."""
        for total in [1, 5, 10, 20]:
            pid = await _create_project_with_worldview(client, auth_headers, total_chapters=total)
            with patch("app.core.llm_client.load_settings") as mock_load:
                mock_load.return_value = {
                    "api_key": "", "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
                }
                resp = await client.post(f"/api/outline/{pid}/generate", headers=auth_headers)
                assert resp.status_code == 200
                data = resp.json()
                assert len(data["chapters"]) == total, \
                    f"Expected {total} chapters, got {len(data['chapters'])}"

    @pytest.mark.usefixtures("clean_db")
    async def test_outline_mock_llm_normalizes_list_fields(self, client, auth_headers):
        """Mock LLM returning list-type fields — must be normalized to correct types."""
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=2)
        mock_response = '```json\n{"story_arc": ["弧线A", "弧线B"], "chapters": [{"chapter_num": "1", "title": ["T1", "T2"], "summary": ["S1"], "key_events": "E1，E2", "reveal_elements": "R1, R2"}, {"chapter_num": "2", "title": "T2", "summary": "S2", "key_events": [], "reveal_elements": []}]}\n```'
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "fake-key", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
            }
            with patch("app.api.outline.llm_client") as mock_llm:
                mock_llm.chat = AsyncMock(return_value=mock_response)
                resp = await client.post(f"/api/outline/{pid}/generate", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["story_arc"], str)
        assert isinstance(data["chapters"][0]["chapter_num"], int)
        assert isinstance(data["chapters"][0]["title"], str)
        assert isinstance(data["chapters"][0]["summary"], str)
        assert isinstance(data["chapters"][0]["key_events"], list)
        assert isinstance(data["chapters"][0]["reveal_elements"], list)

    @pytest.mark.usefixtures("clean_db")
    async def test_outline_mock_llm_garbage_response(self, client, auth_headers):
        """Non-JSON output must not be saved as a fake successful outline."""
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=3)
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "fake-key", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
            }
            with patch("app.api.outline.llm_client") as mock_llm:
                mock_llm.chat = AsyncMock(return_value="This is not JSON at all!")
                resp = await client.post(f"/api/outline/{pid}/generate", headers=auth_headers)
        assert resp.status_code == 502
        assert "不完整或格式无效" in resp.json()["detail"]
        get_resp = await client.get(f"/api/outline/{pid}", headers=auth_headers)
        assert get_resp.status_code == 404

    @pytest.mark.usefixtures("clean_db")
    async def test_outline_mock_llm_fewer_chapters_padded(self, client, auth_headers):
        """An incomplete chapter list must be rejected instead of padded and saved."""
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=5)
        mock_response = '```json\n{"story_arc": "弧线", "chapters": [{"chapter_num": 1, "title": "唯一章节", "summary": "S", "key_events": [], "reveal_elements": []}]}\n```'
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "fake-key", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
            }
            with patch("app.api.outline.llm_client") as mock_llm:
                mock_llm.chat = AsyncMock(return_value=mock_response)
                resp = await client.post(f"/api/outline/{pid}/generate", headers=auth_headers)
        assert resp.status_code == 502
        assert "不完整或格式无效" in resp.json()["detail"]

    @pytest.mark.usefixtures("clean_db")
    async def test_outline_mock_llm_more_chapters_truncated(self, client, auth_headers):
        """LLM returns 10 chapters but project expects 3 — should truncate to 3."""
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=3)
        mock_response = '```json\n{"story_arc": "弧线", "chapters": [' + \
            ', '.join([f'{{"chapter_num": {i}, "title": "第{i}章", "summary": "S", "key_events": [], "reveal_elements": []}}' for i in range(1, 11)]) + \
            ']}\n```'
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "fake-key", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
            }
            with patch("app.api.outline.llm_client") as mock_llm:
                mock_llm.chat = AsyncMock(return_value=mock_response)
                resp = await client.post(f"/api/outline/{pid}/generate", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["chapters"]) == 3


# ════════════════════════════════════════════════════════════
# SSE streaming endpoint tests
# ════════════════════════════════════════════════════════════


@pytest.mark.skip(reason="DEV-003D1 retired public outline streaming API")
class TestOutlineStreamingAPI:
    """Tests for the SSE streaming outline generation endpoint."""

    @pytest.mark.usefixtures("clean_db")
    async def test_stream_outline_mock_llm(self, client, auth_headers):
        """SSE streaming with mock LLM returns complete outline."""
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=3)
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
            }
            resp = await client.post(
                f"/api/outline/{pid}/generate-stream",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

        # Parse SSE events
        events = []
        for line in resp.text.split("\n"):
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass

        # Should have at least start + complete events
        types = [e.get("type") for e in events]
        assert "start" in types
        assert "complete" in types

        # Find the complete event
        complete_evt = next(e for e in events if e["type"] == "complete")
        outline = complete_evt["outline"]
        assert "story_arc" in outline
        assert len(outline["chapters"]) == 3
        assert "reveal_plan" in outline

    @pytest.mark.usefixtures("clean_db")
    async def test_stream_outline_without_worldview_400(self, client, auth_headers):
        """Cannot stream outline without worldview."""
        proj = await client.post("/api/projects", json={
            "title": "无世界观", "genre": "玄幻", "total_chapters": 5,
            "chapter_word_count": 2000, "style_intensity": "standard",
        }, headers=auth_headers)
        pid = proj.json()["id"]
        resp = await client.post(f"/api/outline/{pid}/generate-stream", headers=auth_headers)
        assert resp.status_code == 400

    @pytest.mark.usefixtures("clean_db")
    async def test_stream_outline_without_auth_401(self, client, auth_headers):
        pid = await _create_project_with_worldview(client, auth_headers)
        resp = await client.post(f"/api/outline/{pid}/generate-stream")
        assert resp.status_code == 401

    @pytest.mark.usefixtures("clean_db")
    async def test_stream_outline_other_user_403(self, client, auth_headers, second_auth_headers):
        """User B cannot stream outline for User A's project."""
        pid = await _create_project_with_worldview(client, auth_headers)
        resp = await client.post(
            f"/api/outline/{pid}/generate-stream", headers=second_auth_headers
        )
        assert resp.status_code == 403

    @pytest.mark.usefixtures("clean_db")
    async def test_stream_outline_llm_error_sends_error_event(self, client, auth_headers):
        """When LLM fails during streaming, an error event is sent."""
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=3)
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "fake-key", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
            }
            with patch("app.api.outline.llm_client") as mock_llm:
                async def fake_stream(*args, **kwargs):
                    raise RuntimeError("LLM 连接失败")
                    yield  # never reached
                mock_llm.chat_stream = fake_stream
                resp = await client.post(
                    f"/api/outline/{pid}/generate-stream",
                    headers=auth_headers,
                )
        assert resp.status_code == 200  # SSE always returns 200

        events = []
        for line in resp.text.split("\n"):
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass

        types = [e.get("type") for e in events]
        assert "start" in types
        assert "error" in types

        error_evt = next(e for e in events if e["type"] == "error")
        assert "失败" in error_evt["message"]

    @pytest.mark.usefixtures("clean_db")
    async def test_stream_outline_persists_to_db(self, client, auth_headers):
        """After streaming completes, the outline should be in the DB."""
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=3)
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
            }
            await client.post(
                f"/api/outline/{pid}/generate-stream",
                headers=auth_headers,
            )

        # Verify outline was saved by fetching it via GET endpoint
        get_resp = await client.get(f"/api/outline/{pid}", headers=auth_headers)
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert len(data["chapters"]) == 3

    @pytest.mark.usefixtures("clean_db")
    async def test_stream_outline_overwrites_previous(self, client, auth_headers):
        """Re-streaming should overwrite previous outline."""
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=3)
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
            }
            # First generation
            resp1 = await client.post(
                f"/api/outline/{pid}/generate-stream", headers=auth_headers
            )
            events1 = [json.loads(l[6:]) for l in resp1.text.split("\n") if l.startswith("data: ")]
            complete1 = next(e for e in events1 if e["type"] == "complete")
            id1 = complete1["outline"]["id"]

            # Second generation
            resp2 = await client.post(
                f"/api/outline/{pid}/generate-stream", headers=auth_headers
            )
            events2 = [json.loads(l[6:]) for l in resp2.text.split("\n") if l.startswith("data: ")]
            complete2 = next(e for e in events2 if e["type"] == "complete")
            id2 = complete2["outline"]["id"]

            assert id1 != id2

    @pytest.mark.usefixtures("clean_db")
    async def test_stream_outline_mock_llm_normalizes_list_fields(self, client, auth_headers):
        """SSE streaming also normalizes list-type fields from LLM."""
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=2)
        mock_response = '```json\n{"story_arc": ["弧A", "弧B"], "chapters": [{"chapter_num": "1", "title": ["T1"], "summary": ["S1"], "key_events": "E1，E2", "reveal_elements": "R1, R2"}, {"chapter_num": "2", "title": "T2", "summary": "S2", "key_events": [], "reveal_elements": []}]}\n```'
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
            }
            with patch("app.api.outline.llm_client") as mock_llm:
                async def fake_stream(*args, **kwargs):
                    chunk_size = 20
                    for i in range(0, len(mock_response), chunk_size):
                        yield mock_response[i:i + chunk_size]
                mock_llm.chat_stream = fake_stream
                resp = await client.post(
                    f"/api/outline/{pid}/generate-stream", headers=auth_headers
                )
        events = [json.loads(l[6:]) for l in resp.text.split("\n") if l.startswith("data: ")]
        complete = next(e for e in events if e["type"] == "complete")
        outline = complete["outline"]
        assert isinstance(outline["story_arc"], str)
        assert isinstance(outline["chapters"][0]["chapter_num"], int)
        assert isinstance(outline["chapters"][0]["title"], str)
        assert isinstance(outline["chapters"][0]["key_events"], list)


# ════════════════════════════════════════════════════════════
# Configuration tests
# ════════════════════════════════════════════════════════════


class TestOutlineMaxTokens:
    """Verify max_tokens is sufficient for large chapter counts."""

    def test_max_tokens_is_8192(self):
        """max_tokens must be 8192 (doubled from 4096 to prevent truncation)."""
        assert OUTLINE_MAX_TOKENS == 8192

    def test_max_tokens_sufficient_for_50_chapters(self):
        """50 chapters x ~125 tokens/chapter ~= 6250 < 8192."""
        per_chapter_tokens = 125
        overhead = 300
        for total_chapters in [1, 5, 10, 20, 30, 50]:
            estimated = total_chapters * per_chapter_tokens + overhead
            assert estimated < OUTLINE_MAX_TOKENS, \
                f"max_tokens={OUTLINE_MAX_TOKENS} insufficient for {total_chapters} chapters " \
                f"(est. {estimated} tokens)"


# ════════════════════════════════════════════════════════════
# Chapter generation: reveal element matching (name vs ID)
# ════════════════════════════════════════════════════════════

class TestRevealElementMatching:
    """Test that reveal_plan elements (which are NAMES) are correctly matched
    to worldview elements in chapter generation.

    Bug: _generate_chapter_core matched reveal_plan.elements by e["id"],
    but they actually contain element NAMES (as instructed by the prompt).
    Fix: Match by name first, then fall back to ID.
    """

    @staticmethod
    def _match_elements(
        all_elements: list,
        chapter_reveal_elements: list,
        reveal_plan_elements: list,
    ) -> list:
        """Replicate the matching logic from _generate_chapter_core (post-fix)."""
        elements_to_reveal = []
        added_ids: set = set()

        # Round 1: match from chapter_entry.reveal_elements (by name or ID)
        reveal_names_set = set(chapter_reveal_elements)
        for e in all_elements:
            if e["name"] in reveal_names_set or e["id"] in reveal_names_set:
                elements_to_reveal.append(e)
                added_ids.add(e["id"])

        # Round 2: match from reveal_plan.elements (by name or ID)
        for ename in reveal_plan_elements:
            already_added = any(
                e["name"] == ename or e["id"] == ename
                for e in elements_to_reveal
            )
            if already_added:
                continue
            for e in all_elements:
                if e["name"] == ename or e["id"] == ename:
                    elements_to_reveal.append(e)
                    added_ids.add(e["id"])
                    break

        return elements_to_reveal

    def test_match_by_name_from_chapter_reveal_elements(self):
        """Normal case: chapter.reveal_elements contains names → found in Round 1."""
        all_elements = [
            {"id": "abc", "name": "林远", "category": "character"},
            {"id": "def", "name": "苍澜大陆", "category": "geography"},
        ]
        result = self._match_elements(
            all_elements,
            chapter_reveal_elements=["林远", "苍澜大陆"],
            reveal_plan_elements=["林远", "苍澜大陆"],
        )
        assert len(result) == 2
        assert result[0]["name"] == "林远"
        assert result[1]["name"] == "苍澜大陆"

    def test_match_by_name_from_reveal_plan_only(self):
        """Bug scenario: chapter.reveal_elements is empty, rely on reveal_plan names."""
        all_elements = [
            {"id": "abc", "name": "林远", "category": "character"},
            {"id": "def", "name": "苍澜大陆", "category": "geography"},
        ]
        result = self._match_elements(
            all_elements,
            chapter_reveal_elements=[],
            reveal_plan_elements=["林远", "苍澜大陆"],
        )
        # Before fix: [] (0 elements found)
        # After fix: 2 elements found
        assert len(result) == 2
        names = [e["name"] for e in result]
        assert "林远" in names
        assert "苍澜大陆" in names

    def test_no_duplicates_when_in_both_sources(self):
        """Element in both chapter.reveal_elements and reveal_plan should not duplicate."""
        all_elements = [
            {"id": "abc", "name": "林远", "category": "character"},
        ]
        result = self._match_elements(
            all_elements,
            chapter_reveal_elements=["林远"],
            reveal_plan_elements=["林远"],
        )
        assert len(result) == 1  # No duplicate

    def test_reveal_plan_extras_merged(self):
        """reveal_plan has extra elements not in chapter.reveal_elements."""
        all_elements = [
            {"id": "abc", "name": "林远", "category": "character"},
            {"id": "def", "name": "苍澜大陆", "category": "geography"},
            {"id": "ghi", "name": "正邪之争", "category": "conflict"},
        ]
        result = self._match_elements(
            all_elements,
            chapter_reveal_elements=["林远"],
            reveal_plan_elements=["林远", "苍澜大陆", "正邪之争"],
        )
        assert len(result) == 3
        names = [e["name"] for e in result]
        assert "林远" in names
        assert "苍澜大陆" in names
        assert "正邪之争" in names

    def test_backward_compat_match_by_id(self):
        """Should still work if elements contain IDs (backward compatibility)."""
        all_elements = [
            {"id": "abc", "name": "林远", "category": "character"},
        ]
        result = self._match_elements(
            all_elements,
            chapter_reveal_elements=["abc"],  # ID instead of name
            reveal_plan_elements=[],
        )
        assert len(result) == 1
        assert result[0]["name"] == "林远"

    def test_unmatched_name_does_not_crash(self):
        """Non-existent element name should be silently ignored."""
        all_elements = [
            {"id": "abc", "name": "林远", "category": "character"},
        ]
        result = self._match_elements(
            all_elements,
            chapter_reveal_elements=["不存在的要素"],
            reveal_plan_elements=["另一个不存在的"],
        )
        assert len(result) == 0

    def test_empty_reveal_plan(self):
        """No reveal_plan → only chapter.reveal_elements used."""
        all_elements = [
            {"id": "abc", "name": "林远", "category": "character"},
        ]
        result = self._match_elements(
            all_elements,
            chapter_reveal_elements=["林远"],
            reveal_plan_elements=[],
        )
        assert len(result) == 1

    def test_end_to_end_with_worldview_parser(self):
        """End-to-end: parse worldview → normalize → match by LLM-output names."""
        from app.core.worldview_parser import worldview_parser

        worldview_data = {
            "characters": [
                {"name": "林远", "personality": "坚韧", "background": "小镇",
                 "motivation": "真相", "ability": "灵觉", "relations": []},
            ],
            "geography": [{"name": "苍澜大陆", "description": "主大陆", "significance": "主要发生地"}],
            "factions": [], "power_system": [], "history": [], "conflicts": [], "special_settings": [],
        }
        elements = worldview_parser.parse(worldview_data)
        all_elements = worldview_parser.normalize_elements(elements)

        # Simulate LLM output: chapter with reveal_elements as NAMES
        result = self._match_elements(
            all_elements,
            chapter_reveal_elements=[],  # Empty (simulating LLM only putting in reveal_plan)
            reveal_plan_elements=["林远", "苍澜大陆"],  # Names from LLM
        )

        # Before fix: [] (0 elements — BUG)
        # After fix: 2 elements correctly matched
        assert len(result) == 2
        names = {e["name"] for e in result}
        assert names == {"林远", "苍澜大陆"}
        # Verify meta is preserved
        for e in result:
            assert "meta" in e
            assert e["meta"]["name"] == e["name"]
