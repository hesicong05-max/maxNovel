"""Deterministic, read-only checks for an immutable generation candidate."""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.generation_execution import (
    MAX_CANDIDATE_BYTES,
    GenerationExecutionError,
    generation_candidate_response,
)
from app.core.generation_preflight import (
    GenerationPreparationError,
    generation_run_response,
)
from app.models.generation import ChapterGenerationCandidate, ChapterGenerationRun
from app.schemas.generation import GenerationCandidateAuditResponse


AUDIT_RULESET_VERSION = 1
TARGET_WORD_COUNT_MIN_PERCENT = 70
TARGET_WORD_COUNT_MAX_PERCENT = 130
MAX_UNRECOGNIZED_TERMS = 20
_EXPLICIT_TERM_PATTERN = re.compile(r"《([^《》\r\n]{1,80})》")


def _audit_corrupt() -> GenerationExecutionError:
    return GenerationExecutionError(
        "GENERATION_AUDIT_CORRUPT",
        "生成候选的确定性检查数据不完整，已停止展示检查结果。",
        status_code=409,
        recommended_action="reload_generation_candidate",
    )


def _normalized_term(value: str) -> str:
    return "".join(value.split()).casefold()


def _explicit_term_evidence(
    content: str,
    *,
    authorized_terms: set[str],
) -> tuple[list[dict[str, Any]], bool]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    truncated = False
    for match in _EXPLICIT_TERM_PATTERN.finditer(content):
        term = match.group(1).strip()
        normalized = _normalized_term(term)
        if not normalized or normalized in authorized_terms or normalized in seen:
            continue
        seen.add(normalized)
        if len(items) >= MAX_UNRECOGNIZED_TERMS:
            truncated = True
            break
        excerpt_start = max(0, match.start() - 24)
        excerpt_end = min(len(content), match.end() + 24)
        items.append(
            {
                "term": term,
                "excerpt": content[excerpt_start:excerpt_end],
                "start_offset": match.start(),
                "end_offset": match.end(),
            }
        )
    return items, truncated


async def generation_candidate_audit_response(
    db: AsyncSession,
    candidate: ChapterGenerationCandidate,
    *,
    user_id: str,
) -> dict[str, Any]:
    """Validate the authoritative candidate and derive a stable audit report."""

    candidate_snapshot = await generation_candidate_response(
        db, candidate, user_id=user_id
    )
    run = await db.scalar(
        select(ChapterGenerationRun).where(
            ChapterGenerationRun.project_id == candidate.project_id,
            ChapterGenerationRun.id == candidate.run_id,
            ChapterGenerationRun.requested_by == user_id,
        )
    )
    if run is None:
        raise _audit_corrupt()
    try:
        run_snapshot = generation_run_response(run, replayed=True)
    except GenerationPreparationError as exc:
        raise _audit_corrupt() from exc
    manifest = run_snapshot["context_manifest"]

    target = manifest["chapter"]["target_word_count"]
    actual = candidate_snapshot["word_count"]
    if target is None:
        target_length = {
            "status": "not_applicable",
            "actual_word_count": actual,
            "target_word_count": None,
            "minimum_word_count": None,
            "maximum_word_count": None,
        }
    else:
        minimum = max(1, target * TARGET_WORD_COUNT_MIN_PERCENT // 100)
        maximum = (target * TARGET_WORD_COUNT_MAX_PERCENT + 99) // 100
        target_length = {
            "status": "pass" if minimum <= actual <= maximum else "review",
            "actual_word_count": actual,
            "target_word_count": target,
            "minimum_word_count": minimum,
            "maximum_word_count": maximum,
        }

    preparation_warnings = list(manifest["warnings"])
    authorized_terms = {
        _normalized_term(manifest["part"]["title"]),
        _normalized_term(manifest["chapter"]["title"]),
        *(
            _normalized_term(element["version"]["name"])
            for element in manifest["elements"]
        ),
    }
    authorized_terms.discard("")
    term_items, terms_truncated = _explicit_term_evidence(
        candidate_snapshot["content"],
        authorized_terms=authorized_terms,
    )
    integrity_status = (
        "review"
        if candidate_snapshot["content_size_bytes"] == MAX_CANDIDATE_BYTES
        else "pass"
    )
    statuses = {
        integrity_status,
        target_length["status"],
        "review" if preparation_warnings else "pass",
        "review" if term_items else "pass",
    }

    snapshot = {
        "schema_version": 1,
        "ruleset_version": AUDIT_RULESET_VERSION,
        "project_id": candidate_snapshot["project_id"],
        "run_id": candidate_snapshot["run_id"],
        "planning_chapter_id": candidate_snapshot["planning_chapter_id"],
        "candidate_id": candidate_snapshot["id"],
        "candidate_version": candidate_snapshot["version_no"],
        "candidate_checksum": candidate_snapshot["content_checksum"],
        "context_checksum": run_snapshot["context_checksum"],
        "status": "review" if "review" in statuses else "pass",
        "integrity": {
            "status": integrity_status,
            "content_size_bytes": candidate_snapshot["content_size_bytes"],
            "word_count": actual,
            "storage_limit_bytes": MAX_CANDIDATE_BYTES,
            "storage_limit_reached": (
                candidate_snapshot["content_size_bytes"] == MAX_CANDIDATE_BYTES
            ),
        },
        "target_length": target_length,
        "preparation": {
            "status": "review" if preparation_warnings else "pass",
            "warnings": preparation_warnings,
        },
        "unrecognized_explicit_terms": {
            "status": "review" if term_items else "pass",
            "items": term_items,
            "truncated": terms_truncated,
        },
        "context_summary": {
            "element_count": manifest["counts"]["elements"],
            "relation_count": manifest["counts"]["relations"],
            "warning_count": manifest["counts"]["warnings"],
            "elements": [
                {
                    "element_id": element["element_id"],
                    "type_key": element["type"]["key"],
                    "type_display_name": element["type"]["display_name"],
                    "name": element["version"]["name"],
                    "version_no": element["version"]["version_no"],
                }
                for element in manifest["elements"]
            ],
            "foreshadow_actions_supported": False,
            "foreshadow_action_count": 0,
        },
    }
    try:
        return GenerationCandidateAuditResponse.model_validate(snapshot).model_dump(
            mode="json"
        )
    except ValidationError as exc:
        raise _audit_corrupt() from exc
