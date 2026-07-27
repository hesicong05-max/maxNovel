"""Unit tests for prompt templates — verify worldview info is fully injected."""

import pytest
from app.prompts.templates import (
    build_outline_prompt,
    _format_elements,
    _format_reveal_plan,
    _format_reveal_elements,
)
from app.models.project import NovelGenre


# ─── _format_elements ──────────────────────────────────────────

class TestFormatElements:
    def test_empty_elements(self):
        result = _format_elements([])
        assert "无世界观要素" in result

    def test_character_meta_fields_included(self):
        elements = [
            {
                "id": "char1",
                "category": "character",
                "name": "林天",
                "description": "少年剑修",
                "priority": "core",
                "meta": {
                    "name": "林天",
                    "personality": "坚毅果敢",
                    "background": "家族被灭",
                    "motivation": "复仇",
                    "ability": "剑灵体质",
                    "relations": [
                        {"name": "苏婉", "relation": "青梅竹马"},
                    ],
                },
            },
        ]
        result = _format_elements(elements)
        assert "林天" in result
        assert "坚毅果敢" in result  # personality
        assert "家族被灭" in result  # background
        assert "复仇" in result  # motivation
        assert "剑灵体质" in result  # ability
        assert "苏婉" in result  # relations
        assert "青梅竹马" in result  # relation type

    def test_power_system_meta_fields_included(self):
        elements = [
            {
                "id": "ps1",
                "category": "power_system",
                "name": "灵气修炼体系",
                "description": "规则说明",
                "priority": "core",
                "meta": {
                    "name": "灵气修炼体系",
                    "levels": "练气-筑基-金丹-元婴",
                    "rules": "需要灵石",
                    "limitations": "每境界瓶颈递增",
                },
            },
        ]
        result = _format_elements(elements)
        assert "灵气修炼体系" in result
        assert "练气-筑基-金丹-元婴" in result  # levels
        assert "需要灵石" in result  # rules
        assert "每境界瓶颈递增" in result  # limitations

    def test_conflict_meta_fields_included(self):
        elements = [
            {
                "id": "conf1",
                "category": "conflict",
                "name": "正邪之战",
                "description": "利害关系说明",
                "priority": "core",
                "meta": {
                    "name": "正邪之战",
                    "type": "阵营冲突",
                    "parties": "正道联盟 vs 魔教",
                    "stakes": "天下苍生",
                    "resolution_hint": "需找到上古神器",
                },
            },
        ]
        result = _format_elements(elements)
        assert "正邪之战" in result
        assert "阵营冲突" in result  # type
        assert "正道联盟" in result  # parties
        assert "天下苍生" in result  # stakes
        assert "上古神器" in result  # resolution_hint

    def test_multiple_categories_grouped(self):
        elements = [
            {
                "id": "c1",
                "category": "character",
                "name": "张三",
                "description": "主角",
                "priority": "core",
                "meta": {"name": "张三"},
            },
            {
                "id": "g1",
                "category": "geography",
                "name": "云州",
                "description": "起始之地",
                "priority": "core",
                "meta": {"name": "云州", "significance": "灵气最浓郁"},
            },
        ]
        result = _format_elements(elements)
        assert "[角色]" in result
        assert "[地理]" in result
        assert "张三" in result
        assert "云州" in result
        assert "灵气最浓郁" in result  # significance

    def test_no_meta_does_not_crash(self):
        elements = [
            {
                "id": "e1",
                "category": "character",
                "name": "无meta角色",
                "description": "测试",
                "priority": "secondary",
            },
        ]
        result = _format_elements(elements)
        assert "无meta角色" in result
        assert "测试" in result

    def test_string_relations_handled(self):
        elements = [
            {
                "id": "c1",
                "category": "character",
                "name": "李四",
                "description": "配角",
                "priority": "important",
                "meta": {
                    "name": "李四",
                    "relations": ["王五(师父)", "赵六(师弟)"],
                },
            },
        ]
        result = _format_elements(elements)
        assert "王五" in result
        assert "师父" in result
        assert "赵六" in result
        assert "师弟" in result


# ─── _format_reveal_plan ───────────────────────────────────────

class TestFormatRevealPlan:
    def test_empty_plan(self):
        result = _format_reveal_plan([])
        assert "无揭示计划" in result

    def test_element_ids_converted_to_names(self):
        elements = [
            {"id": "abc123", "name": "灵气修炼体系"},
            {"id": "def456", "name": "正邪之战"},
        ]
        plan = [
            {"chapter": 1, "phase": "introduction", "elements": ["abc123"], "summary": ""},
            {"chapter": 5, "phase": "expansion", "elements": ["def456"], "summary": ""},
        ]
        result = _format_reveal_plan(plan, elements)
        assert "灵气修炼体系" in result
        assert "正邪之战" in result
        assert "引入期" in result
        assert "展开期" in result
        # Should not contain raw IDs
        assert "abc123" not in result
        assert "def456" not in result

    def test_chapter_with_no_elements(self):
        plan = [
            {"chapter": 3, "phase": "introduction", "elements": [], "summary": ""},
        ]
        result = _format_reveal_plan(plan, [])
        assert "无新要素" in result

    def test_without_elements_param_uses_ids(self):
        plan = [
            {"chapter": 1, "phase": "introduction", "elements": ["unknown_id"], "summary": ""},
        ]
        result = _format_reveal_plan(plan)
        # Without elements mapping, falls back to showing the ID
        assert "引入期" in result
        assert "unknown_id" in result

    def test_phase_labels_in_chinese(self):
        plan = [
            {"chapter": 1, "phase": "introduction", "elements": [], "summary": ""},
            {"chapter": 5, "phase": "expansion", "elements": [], "summary": ""},
            {"chapter": 15, "phase": "deepening", "elements": [], "summary": ""},
        ]
        result = _format_reveal_plan(plan)
        assert "引入期" in result
        assert "展开期" in result
        assert "深入期" in result


# ─── build_outline_prompt integration ─────────────────────────

class TestBuildOutlinePrompt:
    def test_prompt_contains_worldview_details(self):
        elements = [
            {
                "id": "c1",
                "category": "character",
                "name": "叶辰",
                "description": "废柴逆袭",
                "priority": "core",
                "meta": {
                    "name": "叶辰",
                    "personality": "隐忍",
                    "background": "叶家旁系",
                    "motivation": "重振家族",
                    "ability": "吞噬火焰",
                },
            },
            {
                "id": "ps1",
                "category": "power_system",
                "name": "斗气体系",
                "description": "规则",
                "priority": "core",
                "meta": {
                    "name": "斗气体系",
                    "levels": "斗者-斗师-大斗师",
                    "rules": "需吸收天地灵气",
                    "limitations": "每阶瓶颈三年",
                },
            },
        ]
        messages = build_outline_prompt(
            genre=NovelGenre.XUANHUAN,
            worldview_elements=elements,
            total_chapters=10,
            chapter_word_count=3000,
            reveal_plan=[],
            style_intensity="standard",
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

        user_content = messages[1]["content"]
        # Worldview elements with meta should be in the prompt
        assert "叶辰" in user_content
        assert "隐忍" in user_content  # personality
        assert "吞噬火焰" in user_content  # ability
        assert "斗气体系" in user_content
        assert "斗者-斗师-大斗师" in user_content  # levels
        assert "每阶瓶颈三年" in user_content  # limitations

    def test_prompt_requires_reveal_elements_consistency(self):
        elements = [
            {"id": "e1", "name": "主角身世", "category": "character", "priority": "core"},
            {"id": "e2", "name": "力量体系", "category": "power_system", "priority": "core"},
        ]
        reveal_plan = [
            {"chapter": 1, "phase": "introduction", "elements": ["e1"], "summary": ""},
            {"chapter": 3, "phase": "expansion", "elements": ["e2"], "summary": ""},
        ]
        messages = build_outline_prompt(
            genre=NovelGenre.XUANHUAN,
            worldview_elements=elements,
            total_chapters=5,
            chapter_word_count=3000,
            reveal_plan=reveal_plan,
            style_intensity="standard",
        )
        user_content = messages[1]["content"]
        # Reveal plan should show element names, not IDs
        assert "主角身世" in user_content
        assert "力量体系" in user_content
        assert "e1" not in user_content.replace("elements", "")  # avoid matching key names
        # Should ask for consistency
        assert "reveal_elements" in messages[0]["content"] or "reveal_elements" in user_content

    def test_prompt_includes_genre_style(self):
        messages = build_outline_prompt(
            genre=NovelGenre.URBAN,
            worldview_elements=[],
            total_chapters=5,
            chapter_word_count=2000,
            reveal_plan=[],
            style_intensity="intense",
        )
        system_content = messages[0]["content"]
        assert "都市" in system_content
        assert "intense" in system_content.lower() or "紧凑" in system_content
