"""Worldview parser — converts structured worldview into indexed elements with priorities."""

import hashlib
import json
import re
from typing import Any

from app.core.llm_client import llm_client
from app.prompts.templates import build_worldview_extraction_prompt


class WorldviewParser:
    """
    Takes structured worldview data (characters, geography, factions, etc.)
    and produces a flat list of elements with priority tags and reveal flags.

    Priority levels:
      - core:     Essential to the main plot, must be revealed early
      - important: Significant to story arcs, revealed in expansion phase
      - secondary: Enriches the world, revealed gradually
      - background: Flavor/detail, revealed opportunistically
    """

    def parse(self, worldview_data: dict[str, Any]) -> list[dict[str, Any]]:
        elements: list[dict[str, Any]] = []
        _counter = 0  # Ensures unique IDs even for same-name elements

        # Characters — main character(s) are core, others scale by importance
        for i, char in enumerate(worldview_data.get("characters", [])):
            priority = "core" if i == 0 else ("important" if i < 3 else "secondary")
            elements.append(self._make_element(
                category="character",
                name=char.get("name", f"角色{i+1}"),
                description=self._char_desc(char),
                priority=priority,
                meta=char,
                counter=_counter,
            ))
            _counter += 1

        # Geography — starting location is core
        for i, geo in enumerate(worldview_data.get("geography", [])):
            priority = "core" if i == 0 else ("important" if i < 2 else "secondary")
            elements.append(self._make_element(
                category="geography",
                name=geo.get("name", f"地点{i+1}"),
                description=geo.get("description", ""),
                priority=priority,
                meta=geo,
                counter=_counter,
            ))
            _counter += 1

        # Factions
        for i, fac in enumerate(worldview_data.get("factions", [])):
            priority = "important" if i < 2 else "secondary"
            elements.append(self._make_element(
                category="faction",
                name=fac.get("name", f"势力{i+1}"),
                description=fac.get("stance", ""),
                priority=priority,
                meta=fac,
                counter=_counter,
            ))
            _counter += 1

        # Power system — always core for xuanhuan/xianxia
        for i, ps in enumerate(worldview_data.get("power_system", [])):
            priority = "core" if i == 0 else "important"
            elements.append(self._make_element(
                category="power_system",
                name=ps.get("name", f"力量体系{i+1}"),
                description=ps.get("rules", ""),
                priority=priority,
                meta=ps,
                counter=_counter,
            ))
            _counter += 1

        # History events
        for i, hist in enumerate(worldview_data.get("history", [])):
            priority = "important" if i < 2 else ("secondary" if i < 4 else "background")
            elements.append(self._make_element(
                category="history",
                name=hist.get("event", f"历史事件{i+1}"),
                description=hist.get("description", ""),
                priority=priority,
                meta=hist,
                counter=_counter,
            ))
            _counter += 1

        # Conflicts — always core
        for i, conf in enumerate(worldview_data.get("conflicts", [])):
            priority = "core" if i == 0 else "important"
            elements.append(self._make_element(
                category="conflict",
                name=conf.get("name", f"矛盾{i+1}"),
                description=conf.get("stakes", ""),
                priority=priority,
                meta=conf,
                counter=_counter,
            ))
            _counter += 1

        # Special settings
        for i, ss in enumerate(worldview_data.get("special_settings", [])):
            priority = "important" if i < 2 else "secondary"
            elements.append(self._make_element(
                category="special_setting",
                name=ss.get("name", f"特殊设定{i+1}"),
                description=ss.get("description", ""),
                priority=priority,
                meta=ss,
                counter=_counter,
            ))
            _counter += 1

        return elements

    def _make_element(
        self,
        category: str,
        name: str,
        description: str,
        priority: str,
        meta: dict[str, Any],
        counter: int = 0,
    ) -> dict[str, Any]:
        eid = hashlib.md5(f"{category}_{name}_{counter}".encode()).hexdigest()[:12]
        return {
            "id": eid,
            "category": category,
            "name": name,
            "description": description,
            "priority": priority,
            "revealed": False,
            "reveal_chapter": None,
            "meta": meta,
        }

    def _char_desc(self, char: dict[str, Any]) -> str:
        parts = []
        if p := char.get("personality"):
            parts.append(f"性格: {p}")
        if b := char.get("background"):
            parts.append(f"背景: {b}")
        if m := char.get("motivation"):
            parts.append(f"动机: {m}")
        if a := char.get("ability"):
            parts.append(f"能力: {a}")
        return " | ".join(parts)

    def summary(self, elements: list[dict[str, Any]]) -> dict[str, Any]:
        """Return a summary of parsed elements for display."""
        by_category: dict[str, int] = {}
        by_priority: dict[str, int] = {}
        for e in elements:
            by_category[e["category"]] = by_category.get(e["category"], 0) + 1
            by_priority[e["priority"]] = by_priority.get(e["priority"], 0) + 1
        return {
            "total": len(elements),
            "by_category": by_category,
            "by_priority": by_priority,
        }

    async def parse_document(self, document_text: str, genre: str = "玄幻") -> dict[str, Any]:
        """
        Use LLM to extract structured worldview from a free-form document.
        Returns a dict matching the WorldviewCreate schema.
        """
        messages = build_worldview_extraction_prompt(document_text, genre)
        raw_response = await llm_client.chat(messages, temperature=0.3, max_tokens=4000)

        # Extract JSON from response (may be wrapped in ```json ... ```)
        json_str = self._extract_json(raw_response)
        if not json_str:
            raise ValueError("LLM 响应中未找到有效的 JSON 数据")

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM 响应的 JSON 格式无效: {e}")

        # Validate and normalize the structure
        return self._normalize_extracted(parsed)

    def _extract_json(self, text: str) -> str | None:
        """Extract JSON content from LLM response (may be wrapped in code fences)."""
        # Try to find ```json ... ``` block
        match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Try to find raw JSON object
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return match.group(0).strip()

        return None

    def _normalize_extracted(self, data: dict[str, Any]) -> dict[str, Any]:
        """Ensure all 7 categories exist and items have required fields."""
        result = {
            "characters": [],
            "geography": [],
            "factions": [],
            "power_system": [],
            "history": [],
            "conflicts": [],
            "special_settings": [],
            "raw_text": None,
        }

        # Characters
        for c in data.get("characters", []):
            if not c.get("name"):
                continue
            result["characters"].append({
                "name": c.get("name", ""),
                "personality": c.get("personality", ""),
                "background": c.get("background", ""),
                "motivation": c.get("motivation", ""),
                "ability": c.get("ability", ""),
                "relations": c.get("relations", []),
            })

        # Geography
        for g in data.get("geography", []):
            if not g.get("name"):
                continue
            result["geography"].append({
                "name": g.get("name", ""),
                "description": g.get("description", ""),
                "significance": g.get("significance", ""),
            })

        # Factions
        for f in data.get("factions", []):
            if not f.get("name"):
                continue
            result["factions"].append({
                "name": f.get("name", ""),
                "stance": f.get("stance", ""),
                "power_level": f.get("power_level", ""),
                "relations": f.get("relations", []),
            })

        # Power system
        for p in data.get("power_system", []):
            if not p.get("name"):
                continue
            result["power_system"].append({
                "name": p.get("name", ""),
                "levels": p.get("levels", ""),
                "rules": p.get("rules", ""),
                "limitations": p.get("limitations", ""),
            })

        # History
        for h in data.get("history", []):
            if not h.get("event"):
                continue
            result["history"].append({
                "event": h.get("event", ""),
                "time": h.get("time", ""),
                "description": h.get("description", ""),
                "impact": h.get("impact", ""),
            })

        # Conflicts
        for c in data.get("conflicts", []):
            if not c.get("name"):
                continue
            result["conflicts"].append({
                "name": c.get("name", ""),
                "type": c.get("type", ""),
                "parties": c.get("parties", ""),
                "stakes": c.get("stakes", ""),
                "resolution_hint": c.get("resolution_hint", ""),
            })

        # Special settings
        for s in data.get("special_settings", []):
            if not s.get("name"):
                continue
            result["special_settings"].append({
                "name": s.get("name", ""),
                "description": s.get("description", ""),
                "rules": s.get("rules", ""),
            })

        return result


worldview_parser = WorldviewParser()
