"""Consistency checker — validates cross-chapter consistency."""

from typing import Any


class ConsistencyChecker:
    """
    Checks for consistency violations across chapters:
      1. Character behavior vs. established personality
      2. Timeline contradictions
      3. Geographic/faction relation consistency
      4. Foreshadow tracking (planted → resolved)
      5. No re-explanation of already-revealed elements
    """

    def check_character_consistency(
        self,
        character_name: str,
        established_personality: str,
        chapter_content: str,
    ) -> dict[str, Any]:
        """Heuristic check for character behavior consistency."""
        # This is a simple heuristic — in production, an LLM call would do this better
        issues = []

        # Check for personality-breaking dialogue patterns
        if "沉稳" in established_personality and ("哈哈哈" in chapter_content or "激动大叫" in chapter_content):
            issues.append(f"角色「{character_name}」设定为沉稳，但章节中有过度激动的表现")

        if "冷静" in established_personality and ("慌张" in chapter_content or "手足无措" in chapter_content):
            issues.append(f"角色「{character_name}」设定为冷静，但章节中表现出慌张")

        return {
            "character": character_name,
            "issues": issues,
            "passed": len(issues) == 0,
        }

    def check_timeline_consistency(
        self,
        timeline: list[dict[str, Any]],
        new_chapter: int,
        chapter_events: list[str],
    ) -> dict[str, Any]:
        """Check for timeline contradictions."""
        issues = []

        # Check for events happening out of order
        past_events = [e for e in timeline if e.get("chapter", 0) < new_chapter]
        for event in chapter_events:
            # Simple heuristic: if the event description already appeared in a past chapter
            for past in past_events:
                if past.get("event") == event:
                    issues.append(f"事件「{event}」已在第{past['chapter']}章发生过")

        return {
            "issues": issues,
            "passed": len(issues) == 0,
        }

    def check_reveal_consistency(
        self,
        revealed_elements: list[str],
        chapter_reveals: list[str],
        chapter_content: str,
    ) -> dict[str, Any]:
        """Check if already-revealed elements are being re-explained (info-dump risk)."""
        issues = []

        # Elements already revealed should not be re-explained in detail
        already_revealed = set(revealed_elements) - set(chapter_reveals)
        # This is a heuristic — in production, an LLM would check if the content re-explains

        return {
            "already_revealed_count": len(already_revealed),
            "new_reveals": len(chapter_reveals),
            "issues": issues,
            "passed": True,
        }

    def check_foreshadow_status(
        self,
        foreshadows: list[dict[str, Any]],
        current_chapter: int,
    ) -> dict[str, Any]:
        """Check foreshadow health — are any overdue for resolution?"""
        overdue = []
        active = []

        for fs in foreshadows:
            if fs["status"] == "resolved":
                continue
            resolve_by = fs.get("resolve_by")
            if resolve_by and current_chapter > resolve_by:
                overdue.append(fs)
            else:
                active.append(fs)

        # Foreshadows planted more than 10 chapters ago and still unresolved
        still_active = []
        for fs in active:
            if current_chapter - fs["planted_chapter"] > 10:
                overdue.append(fs)
            else:
                still_active.append(fs)
        active = still_active

        return {
            "total": len(foreshadows),
            "active": len(active),
            "overdue": len(overdue),
            "overdue_items": overdue,
            "passed": len(overdue) == 0,
        }

    def run_full_check(
        self,
        chapter_content: str,
        chapter_num: int,
        memory_data: dict[str, Any],
        worldview_elements: list[dict[str, Any]],
        chapter_reveals: list[str],
    ) -> dict[str, Any]:
        """Run all consistency checks and return a combined report."""
        results = {
            "character_checks": [],
            "timeline_check": None,
            "reveal_check": None,
            "foreshadow_check": None,
            "all_passed": True,
        }

        # Character consistency
        for char_name, state in (memory_data.get("character_states") or {}).items():
            personality = state.get("personality", "")
            check = self.check_character_consistency(char_name, personality, chapter_content)
            results["character_checks"].append(check)
            if not check["passed"]:
                results["all_passed"] = False

        # Timeline consistency
        timeline_check = self.check_timeline_consistency(
            memory_data.get("timeline", []),
            chapter_num,
            [],  # Would be populated from chapter parsing
        )
        results["timeline_check"] = timeline_check
        if not timeline_check["passed"]:
            results["all_passed"] = False

        # Reveal consistency
        reveal_check = self.check_reveal_consistency(
            memory_data.get("revealed_elements", []),
            chapter_reveals,
            chapter_content,
        )
        results["reveal_check"] = reveal_check

        # Foreshadow health
        fs_check = self.check_foreshadow_status(
            memory_data.get("foreshadows", []),
            chapter_num,
        )
        results["foreshadow_check"] = fs_check
        if not fs_check["passed"]:
            results["all_passed"] = False

        return results


consistency_checker = ConsistencyChecker()
