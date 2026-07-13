"""Pacing planner — the core rhythm engine for progressive worldview reveal."""

from typing import Any


class PacingPlanner:
    """
    Three-phase reveal model:
      - Introduction (引入期): Ch.1 ~ 3, reveal ~15% of elements
      - Expansion  (展开期): Ch.4 ~ mid, reveal to ~40%
      - Deepening  (深入期): mid+ ~ end, reveal to 100%

    Constraints:
      - Max elements revealed per chapter <= threshold (anti info-dump)
      - Core elements need at least 3 chapters of foreshadowing
      - Foreshadow spacing >= 3 chapters
      - Setting description <= 20% of chapter word count
    """

    # Phase boundaries based on total_chapters
    def _phase_bounds(self, total: int) -> dict[str, tuple[int, int]]:
        intro_end = max(3, total // 10)
        exp_end = max(intro_end + 4, total // 2)
        return {
            "introduction": (1, intro_end),
            "expansion": (intro_end + 1, exp_end),
            "deepening": (exp_end + 1, total),
        }

    def _phase_reveal_target(self, phase: str) -> float:
        return {
            "introduction": 0.15,
            "expansion": 0.40,
            "deepening": 1.0,  # All elements should be revealed by end of story
        }.get(phase, 0.5)

    def _phase_max_per_chapter(self, phase: str, total_elements: int) -> int:
        """How many new elements can be introduced per chapter in each phase."""
        if total_elements == 0:
            return 2
        base = max(2, total_elements // 15)
        return {
            "introduction": min(base, 3),  # Be conservative early
            "expansion": min(base + 1, 5),
            "deepening": min(base + 2, 6),
        }.get(phase, 3)

    def plan(
        self,
        elements: list[dict[str, Any]],
        total_chapters: int,
        chapter_word_count: int,
    ) -> list[dict[str, Any]]:
        """
        Build a chapter-by-chapter reveal plan.

        Returns a list of dicts:
          [{chapter, phase, elements: [element_id...], summary}]
        """
        if not elements:
            return [
                {"chapter": i + 1, "phase": self._phase_for(i + 1, total_chapters),
                 "elements": [], "summary": ""}
                for i in range(total_chapters)
            ]

        bounds = self._phase_bounds(total_chapters)
        plan: list[dict[str, Any]] = []

        # Sort elements by priority: core first, then important, then secondary, then background
        priority_order = {"core": 0, "important": 1, "secondary": 2, "background": 3}
        sorted_elements = sorted(elements, key=lambda e: priority_order.get(e["priority"], 99))

        # Distribute elements across chapters
        element_idx = 0
        total_elements = len(sorted_elements)

        for ch in range(1, total_chapters + 1):
            phase = self._phase_for(ch, total_chapters)
            target = self._phase_reveal_target(phase)
            max_per_ch = self._phase_max_per_chapter(phase, total_elements)

            # How many elements should be revealed by end of this chapter
            if ch == total_chapters:
                # Last chapter: ensure all remaining elements are assigned
                to_reveal_now = total_elements - element_idx
            else:
                target_count = int(total_elements * target * (ch / bounds[phase][1]))
                current_revealed = element_idx  # Count of elements already assigned to prior chapters
                to_reveal_now = max(0, min(max_per_ch, target_count - current_revealed))

            chapter_elements = []
            for _ in range(to_reveal_now):
                if element_idx >= total_elements:
                    break
                el = sorted_elements[element_idx]
                chapter_elements.append(el["id"])
                element_idx += 1

            plan.append({
                "chapter": ch,
                "phase": phase,
                "elements": chapter_elements,
                "summary": self._phase_summary(phase, chapter_elements, elements),
            })

        return plan

    def _phase_for(self, chapter: int, total: int) -> str:
        bounds = self._phase_bounds(total)
        for phase, (start, end) in bounds.items():
            if start <= chapter <= end:
                return phase
        return "deepening"

    def _phase_summary(self, phase: str, element_ids: list[str], all_elements: list[dict]) -> str:
        if not element_ids:
            return ""
        phase_names = {
            "introduction": "引入期 — 建立基调，适度提示",
            "expansion": "展开期 — 核心体系揭露，势力浮现",
            "deepening": "深入期 — 深层设定，伏笔回收",
        }
        names = []
        el_map = {e["id"]: e for e in all_elements}
        for eid in element_ids:
            if e := el_map.get(eid):
                names.append(f"{e['category']}:{e['name']}")
        return f"{phase_names.get(phase, '')} | 揭示: {', '.join(names)}"

    def get_phase_label(self, phase: str) -> str:
        return {
            "introduction": "引入期",
            "expansion": "展开期",
            "deepening": "深入期",
        }.get(phase, phase)

    def validate_reveal_density(self, chapter_content: str) -> dict[str, Any]:
        """
        Check if a chapter has too much setting description (anti info-dump check).
        This is a heuristic — in production, an LLM call would do this more accurately.
        """
        setting_keywords = ["体系", "等级", "规则", "历史", "势力", "大陆", "宗门", "修炼",
                           "境界", "传承", "血脉", "天赋", "法则"]
        setting_sentences = 0
        total_sentences = 0
        setting_chars = 0

        for line in chapter_content.split("\n"):
            line = line.strip()
            if not line or len(line) < 5:
                continue
            total_sentences += 1
            if any(kw in line for kw in setting_keywords):
                setting_sentences += 1
                setting_chars += len(line)

        total_chars = max(len(chapter_content), 1)
        word_density = setting_chars / total_chars

        return {
            "setting_density": round(word_density, 3),
            "threshold": 0.20,
            "exceeded": word_density > 0.20,
            "setting_sentences": setting_sentences,
            "total_sentences": total_sentences,
        }


pacing_planner = PacingPlanner()
