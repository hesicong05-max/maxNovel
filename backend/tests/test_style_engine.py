"""Unit tests for style_engine — genre-specific writing templates."""

import pytest
from app.core.style_engine import StyleEngine
from app.models.project import NovelGenre

engine = StyleEngine()


# ─── Template availability ────────────────────────────────────

class TestTemplates:
    @pytest.mark.parametrize("genre", [
        NovelGenre.XUANHUAN,
        NovelGenre.URBAN,
        NovelGenre.SCIFI,
        NovelGenre.WUXIA,
        NovelGenre.XIANXIA,
        NovelGenre.SUSPENSE,
        NovelGenre.ROMANCE,
    ])
    def test_template_exists_for_genre(self, genre):
        tmpl = engine.get_template(genre)
        assert tmpl is not None
        assert "name" in tmpl
        assert "perspective" in tmpl
        assert "dialogue_ratio" in tmpl
        assert "pacing" in tmpl
        assert "dialogue_style" in tmpl
        assert "excitement_design" in tmpl
        assert "common_tropes" in tmpl

    def test_unknown_genre_falls_back_to_xuanhuan(self):
        tmpl = engine.get_template("nonexistent")
        assert tmpl["name"] == "玄幻"


# ─── Template content ────────────────────────────────────────

class TestTemplateContent:
    def test_xuanhuan_has_face_slap(self):
        tmpl = engine.get_template(NovelGenre.XUANHUAN)
        assert "face_slap_rhythm" in tmpl["excitement_design"]

    def test_scifi_has_discovery_rhythm(self):
        tmpl = engine.get_template(NovelGenre.SCIFI)
        assert "discovery_rhythm" in tmpl["excitement_design"]

    def test_romance_has_sweet_rhythm(self):
        tmpl = engine.get_template(NovelGenre.ROMANCE)
        assert "sweet_rhythm" in tmpl["excitement_design"]

    def test_each_genre_has_at_least_3_tropes(self):
        for genre in NovelGenre:
            tmpl = engine.get_template(genre)
            assert len(tmpl["common_tropes"]) >= 3

    def test_each_genre_has_dialogue_roles(self):
        for genre in NovelGenre:
            tmpl = engine.get_template(genre)
            assert len(tmpl["dialogue_style"]) >= 2


# ─── Style prompt generation ──────────────────────────────────

class TestStylePrompt:
    def test_prompt_contains_genre_name(self):
        prompt = engine.get_style_prompt(NovelGenre.XUANHUAN)
        assert "玄幻" in prompt

    def test_prompt_contains_perspective(self):
        prompt = engine.get_style_prompt(NovelGenre.URBAN)
        assert "叙事视角" in prompt

    def test_prompt_contains_dialogue_ratio(self):
        prompt = engine.get_style_prompt(NovelGenre.SCIFI)
        assert "对话比例" in prompt

    def test_prompt_contains_intensity_standard(self):
        prompt = engine.get_style_prompt(NovelGenre.WUXIA, "standard")
        assert "标准节奏" in prompt

    def test_prompt_contains_intensity_mild(self):
        prompt = engine.get_style_prompt(NovelGenre.XIANXIA, "mild")
        assert "放缓" in prompt

    def test_prompt_contains_intensity_intense(self):
        prompt = engine.get_style_prompt(NovelGenre.SUSPENSE, "intense")
        assert "紧凑" in prompt or "密集" in prompt

    def test_prompt_contains_writing_guide_header(self):
        prompt = engine.get_style_prompt(NovelGenre.ROMANCE)
        assert "写作风格指导" in prompt

    def test_prompt_contains_dialogue_style_section(self):
        prompt = engine.get_style_prompt(NovelGenre.XUANHUAN)
        assert "对话风格参考" in prompt

    def test_prompt_contains_excitement_section(self):
        prompt = engine.get_style_prompt(NovelGenre.XUANHUAN)
        assert "爽点设计" in prompt

    def test_unknown_intensity_falls_back_to_standard(self):
        prompt = engine.get_style_prompt(NovelGenre.URBAN, "nonexistent")
        assert "标准节奏" in prompt
