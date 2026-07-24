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

from app.api.outline import (
    _extract_json,
    _normalize_chapter,
    _normalize_outline_data,
    _parse_outline_response,
    _to_int,
    _to_list,
    _to_str,
)


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
# Unit tests: _parse_outline_response (full pipeline)
# ════════════════════════════════════════════════════════════


class TestParseOutlineResponse:
    def test_valid_json_with_code_fence(self):
        raw = '```json\n{"story_arc": "测试弧", "chapters": [{"chapter_num": 1, "title": "T1", "summary": "S1", "key_events": ["E1"], "reveal_elements": ["R1"]}]}\n```'
        result = _parse_outline_response(raw, 1)
        assert result["story_arc"] == "测试弧"
        assert len(result["chapters"]) == 1
        assert result["chapters"][0]["title"] == "T1"

    def test_valid_raw_json(self):
        raw = '{"story_arc": "测试", "chapters": [{"chapter_num": 1, "title": "T", "summary": "S", "key_events": [], "reveal_elements": []}]}'
        result = _parse_outline_response(raw, 1)
        assert result["story_arc"] == "测试"
        assert len(result["chapters"]) == 1

    def test_json_embedded_in_text(self):
        """LLM wraps JSON with explanatory text."""
        raw = 'Here is the outline:\n```json\n{"story_arc": "弧", "chapters": [{"chapter_num": 1, "title": "T", "summary": "S", "key_events": [], "reveal_elements": []}]}\n```\nLet me know!'
        result = _parse_outline_response(raw, 1)
        assert result["story_arc"] == "弧"

    def test_garbage_response_uses_fallback(self):
        raw = "This is not JSON at all"
        result = _parse_outline_response(raw, 5)
        assert "待填充" in result["story_arc"] or result["story_arc"] == "故事大纲生成中，请手动编辑完善"
        assert len(result["chapters"]) == 5
        assert all(c["key_events"] == [] for c in result["chapters"])

    def test_empty_response_uses_fallback(self):
        result = _parse_outline_response("", 3)
        assert len(result["chapters"]) == 3

    def test_malformed_json_uses_fallback(self):
        raw = '{"story_arc": "incomplete'
        result = _parse_outline_response(raw, 3)
        assert len(result["chapters"]) == 3

    def test_fewer_chapters_padded(self):
        raw = '```json\n{"story_arc": "弧", "chapters": [{"chapter_num": 1, "title": "T", "summary": "S", "key_events": [], "reveal_elements": []}]}\n```'
        result = _parse_outline_response(raw, 5)
        assert len(result["chapters"]) == 5
        assert result["chapters"][0]["title"] == "T"
        assert result["chapters"][4]["title"] == "第5章"

    def test_all_list_fields_handled(self):
        """All the bug patterns from worldview import should not crash here."""
        raw = '```json\n{"story_arc": ["弧A", "弧B"], "chapters": [{"chapter_num": "1", "title": ["T1", "T2"], "summary": ["S1"], "key_events": "E1，E2", "reveal_elements": "R1, R2"}]}\n```'
        result = _parse_outline_response(raw, 1)
        assert isinstance(result["story_arc"], str)
        assert isinstance(result["chapters"][0]["chapter_num"], int)
        assert isinstance(result["chapters"][0]["title"], str)
        assert isinstance(result["chapters"][0]["summary"], str)
        assert isinstance(result["chapters"][0]["key_events"], list)
        assert isinstance(result["chapters"][0]["reveal_elements"], list)

    def test_zero_chapters(self):
        """Edge case: total_chapters = 0."""
        result = _parse_outline_response('{"story_arc": "", "chapters": []}', 0)
        assert result["chapters"] == []
        assert result["story_arc"] == ""


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
        """When LLM returns non-JSON, outline should use fallback."""
        pid = await _create_project_with_worldview(client, auth_headers, total_chapters=3)
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "fake-key", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "temperature": 0.8, "max_tokens": 4096,
            }
            with patch("app.api.outline.llm_client") as mock_llm:
                mock_llm.chat = AsyncMock(return_value="This is not JSON at all!")
                resp = await client.post(f"/api/outline/{pid}/generate", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["chapters"]) == 3
        # Fallback chapters should have defaults
        assert all(c["key_events"] == [] for c in data["chapters"])

    @pytest.mark.usefixtures("clean_db")
    async def test_outline_mock_llm_fewer_chapters_padded(self, client, auth_headers):
        """LLM returns 1 chapter but project expects 5 — should pad to 5."""
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
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["chapters"]) == 5
        assert data["chapters"][0]["title"] == "唯一章节"
        assert data["chapters"][1]["title"] == "第2章"  # padded default

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
