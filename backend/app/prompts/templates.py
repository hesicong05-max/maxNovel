"""Prompt templates for chapter writing and worldview extraction."""

import json
from typing import Any

from app.core.style_engine import style_engine
from app.models.project import NovelGenre


def _normalize_genre(genre: Any) -> NovelGenre:
    """Convert genre to NovelGenre enum, handling string inputs from DB.

    In PostgreSQL, the Enum column may return a string instead of an
    enum instance in certain edge cases (e.g. after alembic migration).
    """
    if isinstance(genre, NovelGenre):
        return genre
    if isinstance(genre, str):
        for g in NovelGenre:
            if g.value == genre or g.name == genre:
                return g
        # Default to xuanhuan if unknown
        return NovelGenre.XUANHUAN
    return NovelGenre.XUANHUAN


def build_chapter_prompt(
    genre: NovelGenre,
    chapter_num: int,
    chapter_title: str,
    chapter_summary: str,
    key_events: list[str],
    elements_to_reveal: list[dict[str, Any]],
    style_intensity: str,
    context: dict[str, Any],
    chapter_word_count: int,
    total_chapters: int,
    phase: str = "",
    phase_guidance: str = "",
    story_arc: str = "",
    all_element_names: list[str] | None = None,
) -> list[dict[str, str]]:
    """Build the system + user prompt for generating a single chapter.

    The ``phase`` and ``phase_guidance`` come from the outline's LLM-generated
    reveal_plan. If empty, they are derived from position as a fallback.

    ``story_arc`` is the overall story outline summary from the Outline model,
    giving the LLM context about the big-picture direction.

    ``all_element_names`` is a compact list of ALL worldview element names,
    so the LLM knows which names are valid to reference (not just the ones
    scheduled for reveal in this chapter).
    """

    genre = _normalize_genre(genre)

    style_text = style_engine.get_style_prompt(genre, style_intensity)

    # Format elements to reveal
    reveal_text = _format_reveal_elements(elements_to_reveal)

    # Format context from memory
    context_text = _format_context(context)

    # Use outline-derived phase, fall back to position-based heuristic
    if not phase:
        phase_ratio = chapter_num / max(total_chapters, 1)
        if phase_ratio < 0.15:
            phase = "引入期"
        elif phase_ratio < 0.5:
            phase = "展开期"
        else:
            phase = "深入期"
    if not phase_guidance:
        phase_guidance = "根据故事节奏自然推进，通过剧情和对话带出设定信息"

    # Build the valid-names reference string
    valid_names_str = "、".join(all_element_names) if all_element_names else "（无世界观要素）"

    system_prompt = f"""你是一位经验丰富的网文作者，正在创作一部连载小说。
当前是第{chapter_num}章（共{total_chapters}章），当前叙事阶段：{phase}。

【最高优先级原则：世界观即一切】
故事中的所有角色名称、地名、势力名称、力量体系、历史事件、矛盾冲突，
必须且只能来自用户提供的世界观数据。
禁止凭空创造世界观中不存在的任何角色、势力、地名或体系。
以下是世界观中所有合法的要素名称，写作时只能使用这些名称：
{valid_names_str}

【故事大纲方向】
{story_arc or "（未提供大纲综述，请根据章节信息推进剧情）"}

【以下写作风格指导仅用于叙事技巧参考，故事内容必须来自世界观设定】
{style_text}
以上指导仅涉及叙事技巧，不涉及具体内容。角色、地名、体系等必须来自世界观数据。

【当前阶段写作指引】
{phase_guidance}

【渐进式揭示原则】
- 本章需要自然地融入以下世界观要素，但不要用说明文段落直接解释
- 通过角色对话、事件发展、环境描写自然带出设定信息
- 设定描述不超过本章总字数的20%
- 已在之前章节揭示过的要素不要重复解释

【写作要求】
- 本章目标字数：约{chapter_word_count}字
- 保持叙事节奏，对话与叙述交替
- 章节末尾留下钩子（悬念/期待），吸引读者继续
- 直接输出纯文本正文，不要使用 Markdown 格式（#标题、**加粗**等）

直接输出章节正文，不要加任何说明性文字或元标注。"""

    user_prompt = f"""请写作第{chapter_num}章。

【章节信息】
标题：{chapter_title}
内容概述：{chapter_summary}
关键事件：{', '.join(key_events) if key_events else '无'}

【本章需要揭示的世界观要素】
{reveal_text}

【故事上下文】
{context_text}

请直接输出章节正文。"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_summary_prompt(chapter_content: str, chapter_num: int) -> list[dict[str, str]]:
    """Build prompt for generating a chapter summary (for memory)."""
    return [
        {
            "role": "system",
            "content": f"""请用2-3句话概括以下章节的核心内容，包括：
1. 主要发生了什么
2. 揭示了哪些新信息
3. 角色状态有什么变化
只输出概括，不要加任何说明。""",
        },
        {
            "role": "user",
            "content": f"第{chapter_num}章内容：\n\n{chapter_content[:3000]}",
        },
    ]


def build_worldview_extraction_prompt(document_text: str, genre: str = "玄幻") -> list[dict[str, str]]:
    """Build prompt for extracting structured worldview from a free-form document."""

    system_prompt = f"""你是一位专业的小说世界观分析师。
你的任务是从用户提供的文档中提取结构化的世界观设定要素。

请仔细阅读文档，识别并提取以下7类要素：
1. 角色（characters）：姓名、性格、背景、动机、能力/特长
2. 地理（geography）：地名、描述、重要性
3. 势力组织（factions）：名称、立场、实力等级
4. 力量体系（power_system）：体系名称、等级划分、规则、限制
5. 历史事件（history）：事件名、时间、描述、影响
6. 核心矛盾（conflicts）：矛盾名称、类型、涉及方、利害关系、解决线索
7. 特殊设定（special_settings）：名称、描述、规则

【注意事项】
- 只提取文档中明确提到的内容，不要编造
- 如果某类要素在文档中没有提及，返回空数组
- 角色信息尽量完整（至少有姓名+一个其他字段）
- 如果原文信息不完整，对应字段留空字符串

【输出格式要求】
请输出一个JSON对象，格式如下：
{{
  "characters": [
    {{"name": "角色名", "personality": "性格描述", "background": "背景", "motivation": "动机", "ability": "能力/特长", "relations": [{{"name": "关联角色名", "relation": "关系说明"}}]}}
  ],
  "geography": [
    {{"name": "地名", "description": "描述", "significance": "重要性"}}
  ],
  "factions": [
    {{"name": "势力名", "stance": "立场", "power_level": "实力等级", "relations": [{{"name": "关联势力名", "relation": "关系说明"}}]}}
  ],
  "power_system": [
    {{"name": "体系名", "levels": "等级划分", "rules": "规则", "limitations": "限制"}}
  ],
  "history": [
    {{"event": "事件名", "time": "时间", "description": "描述", "impact": "影响"}}
  ],
  "conflicts": [
    {{"name": "矛盾名", "type": "类型", "parties": "涉及方", "stakes": "利害关系", "resolution_hint": "解决线索"}}
  ],
  "special_settings": [
    {{"name": "设定名", "description": "描述", "rules": "规则"}}
  ]
}}

注意：relations 数组中每个元素必须是对象格式 {{"name": "名称", "relation": "关系"}}，不要使用字符串。

只输出JSON，不要加任何其他说明文字。"""

    # Truncate document to keep prompt within reasonable size and reduce LLM latency
    MAX_DOC_CHARS = 6000
    truncated_text = document_text[:MAX_DOC_CHARS]
    if len(document_text) > MAX_DOC_CHARS:
        truncated_text += "\n\n[文档内容较长，以上为前 6000 字符。如要素不全，请分多次导入或手动补充。]"

    user_prompt = f"""请从以下文档中提取结构化的世界观设定要素：

{truncated_text}"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _format_reveal_elements(elements: list[dict[str, Any]]) -> str:
    if not elements:
        return "（本章无需揭示新要素，着重剧情推进）"

    # Category → Chinese label
    category_names = {
        "character": "角色",
        "geography": "地理",
        "faction": "势力",
        "power_system": "力量体系",
        "history": "历史事件",
        "conflict": "矛盾",
        "special_setting": "特殊设定",
    }

    # Meta fields per category for consistent author-facing labels.
    meta_fields: dict[str, list[tuple[str, str]]] = {
        "character": [
            ("personality", "性格"), ("background", "背景"),
            ("motivation", "动机"), ("ability", "能力"),
        ],
        "geography": [("significance", "重要性")],
        "faction": [("stance", "立场"), ("power_level", "实力等级")],
        "power_system": [("levels", "等级划分"), ("rules", "规则"), ("limitations", "限制")],
        "history": [("time", "时间"), ("impact", "影响")],
        "conflict": [("type", "类型"), ("parties", "涉及方"), ("stakes", "利害关系"), ("resolution_hint", "解决线索")],
        "special_setting": [("rules", "规则")],
    }

    lines = []
    for e in elements:
        cat = e.get("category", "unknown")
        cat_label = category_names.get(cat, cat)
        lines.append(f"  - {e['name']}（{cat_label}）: {e.get('description', '')}")
        if meta := e.get("meta"):
            fields = meta_fields.get(cat, [])
            for key, label in fields:
                if val := meta.get(key):
                    lines.append(f"    · {label}: {val}")
            # Include relations for characters and factions
            if relations := meta.get("relations"):
                if isinstance(relations, list) and relations:
                    rel_strs = []
                    for rel in relations:
                        if isinstance(rel, dict):
                            rel_strs.append(f"{rel.get('name', '')}({rel.get('relation', '')})")
                        elif isinstance(rel, str):
                            rel_strs.append(rel)
                    if rel_strs:
                        lines.append(f"    · 关系: {', '.join(rel_strs)}")

    return "\n".join(lines)


def _format_context(context: dict[str, Any]) -> str:
    lines = []

    # Recent chapter summaries
    summaries = context.get("recent_summaries", [])
    if summaries:
        lines.append("【前情回顾】")
        for s in summaries:
            lines.append(f"  第{s['chapter_num']}章: {s['summary']}")
    else:
        lines.append("【前情回顾】\n  （本章为第一章）")

    # Character states
    states = context.get("character_states", {})
    if states:
        lines.append("\n【角色当前状态】")
        for name, state in states.items():
            lines.append(f"  {name}: {json.dumps(state, ensure_ascii=False)}")

    # Pending foreshadows
    foreshadows = context.get("pending_foreshadows", [])
    if foreshadows:
        lines.append("\n【待回收伏笔】")
        for fs in foreshadows:
            lines.append(f"  - {fs['description']}（第{fs['planted_chapter']}章铺设）")

    # Recent timeline
    timeline = context.get("timeline", [])
    if timeline:
        lines.append("\n【近期事件】")
        for t in timeline:
            lines.append(f"  第{t['chapter']}章: {t['event']} — {t['description']}")

    return "\n".join(lines) if lines else "（无上下文信息）"
