"""Prompt templates for outline generation and chapter writing."""

import json
from typing import Any

from app.core.style_engine import style_engine
from app.models.project import NovelGenre


def build_outline_prompt(
    genre: NovelGenre,
    worldview_elements: list[dict[str, Any]],
    total_chapters: int,
    chapter_word_count: int,
    reveal_plan: list[dict[str, Any]],
    style_intensity: str = "standard",
) -> list[dict[str, str]]:
    """Build the system + user prompt for generating story outline."""

    style_text = style_engine.get_style_prompt(genre, style_intensity)

    # Format worldview elements for the prompt (includes meta details)
    elements_text = _format_elements(worldview_elements)

    # Format reveal plan (convert element IDs to names for LLM readability)
    plan_text = _format_reveal_plan(reveal_plan, worldview_elements)

    system_prompt = f"""你是一位专业的网文创作顾问，擅长{genre.value}类型的网络小说。
你的任务是根据用户提供的世界观设定，生成一个完整的故事大纲。

{style_text}

【世界观渐进式揭示原则】
1. 世界观信息不要在开头集中倾倒，而是根据章节节奏逐步展开
2. 引入期（前10%）只揭示主角处境和核心矛盾雏形
3. 展开期（10%-50%）逐步揭露核心体系、势力关系、关键配角
4. 深入期（50%+）揭示深层设定、回收费伏笔、爆发世界观冲突
5. 每章设定描述不超过总字数的20%
6. 设定通过剧情和对话自然带出，不用说明文段落

【关键要求】
1. 大纲必须严格基于【世界观数据】中的设定进行构建，不得凭空创造与世界观无关的角色、势力或体系
2. 每章的 reveal_elements 必须从【揭示节奏计划】中该章对应的要素名称中选取，保持一致
3. story_arc 需要整合世界观中的核心矛盾和角色动机
4. 章节标题应体现世界观特色（如涉及力量体系、势力名称等）

【输出格式要求】
请输出一个JSON对象，格式如下：
{{
  "story_arc": "整个故事的弧线描述（2-3句话）",
  "chapters": [
    {{
      "chapter_num": 1,
      "title": "章节标题",
      "summary": "本章内容概述（1-2句话）",
      "key_events": ["关键事件1", "关键事件2"],
      "reveal_elements": ["要素名称1", "要素名称2"]
    }}
  ]
}}

请为全部{total_chapters}章生成大纲。每章约{chapter_word_count}字。"""

    user_prompt = f"""请根据以下信息生成故事大纲：

【世界观数据】
{elements_text}

【揭示节奏计划】
{plan_text}

请确保大纲中每章的 reveal_elements 与揭示计划中该章要揭示的要素名称完全一致。"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


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
) -> list[dict[str, str]]:
    """Build the system + user prompt for generating a single chapter."""

    style_text = style_engine.get_style_prompt(genre, style_intensity)

    # Format elements to reveal
    reveal_text = _format_reveal_elements(elements_to_reveal)

    # Format context from memory
    context_text = _format_context(context)

    # Determine phase
    phase_ratio = chapter_num / max(total_chapters, 1)
    if phase_ratio < 0.15:
        phase_name = "引入期"
        phase_guidance = "保持神秘感，只揭示必要的初始信息，着重建立角色和氛围"
    elif phase_ratio < 0.5:
        phase_name = "展开期"
        phase_guidance = "逐步展开核心设定，通过冲突和事件自然带出世界观要素"
    else:
        phase_name = "深入期"
        phase_guidance = "深入世界观核心，回收伏笔，制造世界观层面的冲突"

    system_prompt = f"""你是一位经验丰富的{genre.value}网文作者，正在创作一部连载小说。
当前是第{chapter_num}章（共{total_chapters}章），处于{phase_name}。

{style_text}

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


def _format_elements(elements: list[dict[str, Any]]) -> str:
    if not elements:
        return "（无世界观要素，请确保大纲具有完整的独立剧情结构）"

    lines = []
    by_category: dict[str, list] = {}
    for e in elements:
        by_category.setdefault(e["category"], []).append(e)

    category_names = {
        "character": "角色",
        "geography": "地理",
        "faction": "势力",
        "power_system": "力量体系",
        "history": "历史事件",
        "conflict": "矛盾",
        "special_setting": "特殊设定",
    }

    # Fields to extract from meta per category (key → label)
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

    for cat, items in by_category.items():
        cat_name = category_names.get(cat, cat)
        lines.append(f"\n[{cat_name}]")
        for item in items:
            priority_tag = f"（{item['priority']}）" if item.get("priority") else ""
            lines.append(f"  - {item['name']}{priority_tag}: {item.get('description', '')}")
            # Include meta fields with detailed info
            if meta := item.get("meta"):
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


def _format_reveal_plan(
    plan: list[dict[str, Any]],
    elements: list[dict[str, Any]] | None = None,
) -> str:
    if not plan:
        return "（无揭示计划）"

    # Build element_id → name mapping for readable output
    el_map: dict[str, str] = {}
    if elements:
        for e in elements:
            el_map[e.get("id", "")] = e.get("name", "")

    phase_labels = {
        "introduction": "引入期",
        "expansion": "展开期",
        "deepening": "深入期",
    }

    lines = []
    for entry in plan:
        ch = entry.get("chapter", "?")
        phase = entry.get("phase", "")
        element_ids = entry.get("elements", [])
        phase_label = phase_labels.get(phase, phase)

        # Convert element IDs to readable names
        element_names = []
        for eid in element_ids:
            name = el_map.get(eid, "")
            if name:
                element_names.append(name)
            elif eid:
                element_names.append(eid)

        if element_names:
            lines.append(f"  第{ch}章 [{phase_label}]: 揭示 → {', '.join(element_names)}")
        else:
            lines.append(f"  第{ch}章 [{phase_label}]: （无新要素，剧情推进）")

    return "\n".join(lines)


def _format_reveal_elements(elements: list[dict[str, Any]]) -> str:
    if not elements:
        return "（本章无需揭示新要素，着重剧情推进）"

    lines = []
    for e in elements:
        lines.append(f"  - {e['name']}（{e['category']}）: {e.get('description', '')}")
        if meta := e.get("meta"):
            # Include relevant meta info
            for key in ["personality", "background", "motivation", "ability",
                       "levels", "rules", "limitations", "stance", "parties", "stakes"]:
                if val := meta.get(key):
                    lines.append(f"    · {key}: {val}")

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
