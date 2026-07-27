"""Unit tests for prompt templates — verify worldview info is fully injected and LLM-driven structure."""

import pytest
from app.prompts.templates import (
    build_outline_prompt,
    build_chapter_prompt,
    _format_elements,
    _format_reveal_plan,
    _format_reveal_elements,
)
from app.models.project import NovelGenre


# ─── _format_reveal_elements (chapter prompt) ─────────────────

class TestFormatRevealElements:
    def test_empty_elements(self):
        result = _format_reveal_elements([])
        assert "无需揭示" in result

    def test_character_meta_with_chinese_labels(self):
        """Chapter prompt should show character meta with Chinese labels (not English keys)."""
        elements = [
            {
                "id": "c1",
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
                    "relations": [{"name": "苏婉", "relation": "青梅竹马"}],
                },
            },
        ]
        result = _format_reveal_elements(elements)
        assert "林天" in result
        assert "角色" in result  # Chinese category label
        assert "坚毅果敢" in result  # personality
        assert "家族被灭" in result  # background
        assert "复仇" in result  # motivation
        assert "剑灵体质" in result  # ability
        assert "苏婉" in result  # relations
        assert "青梅竹马" in result  # relation type
        # Should NOT have English keys
        assert "personality:" not in result
        assert "background:" not in result

    def test_geography_includes_significance(self):
        """Previously missing: significance field for geography."""
        elements = [
            {
                "id": "g1",
                "category": "geography",
                "name": "苍澜大陆",
                "description": "主大陆",
                "priority": "core",
                "meta": {
                    "name": "苍澜大陆",
                    "description": "主大陆",
                    "significance": "主要故事发生地",
                },
            },
        ]
        result = _format_reveal_elements(elements)
        assert "重要性" in result
        assert "主要故事发生地" in result

    def test_faction_includes_power_level(self):
        """Previously missing: power_level field for factions."""
        elements = [
            {
                "id": "f1",
                "category": "faction",
                "name": "天玄宗",
                "description": "正道领袖",
                "priority": "important",
                "meta": {
                    "name": "天玄宗",
                    "stance": "正道领袖",
                    "power_level": "顶级",
                },
            },
        ]
        result = _format_reveal_elements(elements)
        assert "立场" in result
        assert "正道领袖" in result
        assert "实力等级" in result
        assert "顶级" in result

    def test_history_includes_time_and_impact(self):
        """Previously missing: time and impact fields for history."""
        elements = [
            {
                "id": "h1",
                "category": "history",
                "name": "远古大战",
                "description": "上古大战",
                "priority": "important",
                "meta": {
                    "name": "远古大战",
                    "event": "远古大战",
                    "time": "万年前",
                    "description": "上古大战",
                    "impact": "传承失传",
                },
            },
        ]
        result = _format_reveal_elements(elements)
        assert "时间" in result
        assert "万年前" in result
        assert "影响" in result
        assert "传承失传" in result

    def test_conflict_includes_all_fields(self):
        """Previously missing: type and resolution_hint for conflicts."""
        elements = [
            {
                "id": "cf1",
                "category": "conflict",
                "name": "正邪之争",
                "description": "大陆控制权",
                "priority": "core",
                "meta": {
                    "name": "正邪之争",
                    "type": "阵营冲突",
                    "parties": "正道 vs 魔道",
                    "stakes": "大陆控制权",
                    "resolution_hint": "第三条道路",
                },
            },
        ]
        result = _format_reveal_elements(elements)
        assert "类型" in result
        assert "阵营冲突" in result
        assert "涉及方" in result
        assert "正道 vs 魔道" in result
        assert "利害关系" in result
        assert "大陆控制权" in result
        assert "解决线索" in result
        assert "第三条道路" in result

    def test_special_setting_includes_rules(self):
        elements = [
            {
                "id": "s1",
                "category": "special_setting",
                "name": "灵根天赋",
                "description": "不同灵根属性",
                "priority": "important",
                "meta": {
                    "name": "灵根天赋",
                    "description": "不同灵根属性",
                    "rules": "金木水火土五行",
                },
            },
        ]
        result = _format_reveal_elements(elements)
        assert "规则" in result
        assert "金木水火土五行" in result

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
        result = _format_reveal_elements(elements)
        assert "无meta角色" in result


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


# ─── build_outline_prompt integration (LLM-driven, no pre-computed reveal_plan) ──

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

    def test_prompt_asks_llm_to_generate_reveal_plan(self):
        """The prompt should ask the LLM to generate its own reveal_plan."""
        elements = [
            {"id": "e1", "name": "主角身世", "category": "character", "priority": "core"},
            {"id": "e2", "name": "力量体系", "category": "power_system", "priority": "core"},
        ]
        messages = build_outline_prompt(
            genre=NovelGenre.XUANHUAN,
            worldview_elements=elements,
            total_chapters=5,
            chapter_word_count=3000,
            style_intensity="standard",
        )
        system_content = messages[0]["content"]
        # Should ask LLM to output reveal_plan
        assert "reveal_plan" in system_content
        # Should ask LLM to name phases itself
        assert "你" in system_content and "命名" in system_content or "phase" in system_content

    def test_prompt_does_not_inject_precomputed_reveal_plan(self):
        """The prompt should NOT contain a pre-computed reveal plan section."""
        elements = [
            {"id": "e1", "name": "主角身世", "category": "character", "priority": "core"},
        ]
        messages = build_outline_prompt(
            genre=NovelGenre.XUANHUAN,
            worldview_elements=elements,
            total_chapters=5,
            chapter_word_count=3000,
            style_intensity="standard",
        )
        user_content = messages[1]["content"]
        # Should NOT have a "揭示节奏计划" section (pre-computed plan removed)
        assert "揭示节奏计划" not in user_content

    def test_prompt_no_longer_uses_fixed_three_phase_model(self):
        """The prompt should NOT hardcode '前10%' / '10%-50%' / '50%+' percentages."""
        elements = [
            {"id": "e1", "name": "主角", "category": "character", "priority": "core"},
        ]
        messages = build_outline_prompt(
            genre=NovelGenre.XUANHUAN,
            worldview_elements=elements,
            total_chapters=10,
            chapter_word_count=3000,
            style_intensity="standard",
        )
        system_content = messages[0]["content"]
        # Should NOT have fixed percentage-based phase rules
        assert "前10%" not in system_content
        assert "10%-50%" not in system_content
        assert "50%+" not in system_content

    def test_prompt_includes_element_name_list(self):
        """The prompt should list element names for the LLM to reference."""
        elements = [
            {"id": "e1", "name": "灵气体系", "category": "power_system", "priority": "core"},
            {"id": "e2", "name": "正邪之战", "category": "conflict", "priority": "core"},
        ]
        messages = build_outline_prompt(
            genre=NovelGenre.XUANHUAN,
            worldview_elements=elements,
            total_chapters=5,
            chapter_word_count=3000,
            style_intensity="standard",
        )
        system_content = messages[0]["content"]
        # Element names should be listed for LLM reference
        assert "灵气体系" in system_content
        assert "正邪之战" in system_content

    def test_prompt_includes_genre_style_as_advisory(self):
        messages = build_outline_prompt(
            genre=NovelGenre.URBAN,
            worldview_elements=[],
            total_chapters=5,
            chapter_word_count=2000,
            style_intensity="intense",
        )
        system_content = messages[0]["content"]
        assert "都市" in system_content
        # Style should be marked as advisory
        assert "参考" in system_content


# ─── build_chapter_prompt with LLM-derived phase ──────────────

class TestBuildChapterPrompt:
    def test_uses_outline_derived_phase(self):
        """When phase is provided from the outline, it should be used."""
        messages = build_chapter_prompt(
            genre=NovelGenre.XUANHUAN,
            chapter_num=3,
            chapter_title="暗涌",
            chapter_summary="主角发现暗流",
            key_events=["事件A"],
            elements_to_reveal=[],
            style_intensity="standard",
            context={},
            chapter_word_count=3000,
            total_chapters=10,
            phase="暗涌",
            phase_guidance="秘密逐渐浮出水面",
        )
        system_content = messages[0]["content"]
        assert "暗涌" in system_content
        assert "秘密逐渐浮出水面" in system_content

    def test_fallback_phase_when_not_provided(self):
        """When phase is empty, should fall back to position-based heuristic."""
        messages = build_chapter_prompt(
            genre=NovelGenre.XUANHUAN,
            chapter_num=1,
            chapter_title="开始",
            chapter_summary="开始",
            key_events=[],
            elements_to_reveal=[],
            style_intensity="standard",
            context={},
            chapter_word_count=3000,
            total_chapters=10,
        )
        system_content = messages[0]["content"]
        # Should have some phase name
        assert "引入期" in system_content or "展开期" in system_content or "深入期" in system_content

    def test_style_marked_as_advisory(self):
        """The chapter prompt should mark style guidance as advisory."""
        messages = build_chapter_prompt(
            genre=NovelGenre.URBAN,
            chapter_num=1,
            chapter_title="第一章",
            chapter_summary="测试",
            key_events=[],
            elements_to_reveal=[],
            style_intensity="standard",
            context={},
            chapter_word_count=2000,
            total_chapters=10,
        )
        system_content = messages[0]["content"]
        assert "参考" in system_content
