"""Unit tests for chapter prompt templates and worldview grounding."""

from app.prompts.templates import (
    build_chapter_prompt,
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


# ─── build_chapter_prompt ──────────────────────────────────────

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
