"""Deterministic, non-destructive review clues for formal lore."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import defaultdict
from itertools import combinations
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.lore_write import check_writes_available, field_schema_for_type
from app.models.lore import LoreReviewSuggestion, SettingElement, SettingType
from app.models.project import Project, _utcnow


REVIEW_RULE_KEY = "same_normalized_name_same_type"
REVIEW_RULE_VERSION = 1
MAX_GROUP_SIZE = 40
MAX_SCAN_PAIRS = 1000


def normalize_review_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _provided_scalar(element: SettingElement, key: str) -> Any | None:
    if (element.field_states or {}).get(key) != "provided":
        return None
    value = (element.payload or {}).get(key)
    if isinstance(value, (str, int, float, bool)) and not isinstance(value, complex):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value
    return None


def _display_value(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _evidence(
    left: SettingElement,
    right: SettingElement,
    setting_type: SettingType,
) -> tuple[str, list[dict[str, Any]], str]:
    fields: list[dict[str, Any]] = []
    definitions = field_schema_for_type(setting_type)
    for definition in definitions:
        key = str(definition.get("key", ""))
        if not key:
            continue
        left_value = _provided_scalar(left, key)
        right_value = _provided_scalar(right, key)
        if left_value is None or right_value is None:
            continue
        if left_value == right_value:
            continue
        fields.append(
            {
                "field_key": key,
                "label": str(definition.get("label") or key),
                "comparison": "different",
                "left_value": _display_value(left_value)[:1000],
                "right_value": _display_value(right_value)[:1000],
            }
        )
    kind = "possible_conflict" if fields else "possible_duplicate"
    payload = {
        "rule_key": REVIEW_RULE_KEY,
        "rule_version": REVIEW_RULE_VERSION,
        "type_id": setting_type.id,
        "normalized_name": normalize_review_name(left.name),
        "kind": kind,
        "fields": fields,
        "left_content_version": left.content_version,
        "right_content_version": right.content_version,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return kind, fields, fingerprint


async def scan_lore_review_suggestions(
    db: AsyncSession,
    project_id: str,
) -> dict[str, int | bool]:
    """Converge v1 clues under a project lock without changing formal lore."""

    check_writes_available()
    await db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update()
    )
    rows = await db.execute(
        select(SettingElement, SettingType)
        .join(
            SettingType,
            (SettingType.id == SettingElement.type_id)
            & (SettingType.project_id == SettingElement.project_id),
        )
        .where(
            SettingElement.project_id == project_id,
            SettingElement.confirmation_status == "confirmed",
            SettingElement.lifecycle_status != "merged",
        )
        .order_by(SettingElement.id)
        .with_for_update()
    )
    elements = list(rows.all())
    groups: dict[tuple[str, str], list[tuple[SettingElement, SettingType]]] = (
        defaultdict(list)
    )
    for element, setting_type in elements:
        normalized = normalize_review_name(element.name)
        if normalized:
            groups[(setting_type.id, normalized)].append((element, setting_type))

    existing_rows = await db.execute(
        select(LoreReviewSuggestion).where(
            LoreReviewSuggestion.project_id == project_id,
            LoreReviewSuggestion.rule_key == REVIEW_RULE_KEY,
        )
    )
    existing = {
        (item.left_element_id, item.right_element_id): item
        for item in existing_rows.scalars().all()
    }

    created = updated = unchanged = marked_stale = pair_count = 0
    emitted: set[tuple[str, str]] = set()
    truncated = False
    now = _utcnow()
    for grouped in groups.values():
        if len(grouped) < 2:
            continue
        if len(grouped) > MAX_GROUP_SIZE:
            grouped = grouped[:MAX_GROUP_SIZE]
            truncated = True
        for (left, setting_type), (right, _) in combinations(grouped, 2):
            if pair_count >= MAX_SCAN_PAIRS:
                truncated = True
                break
            pair_count += 1
            if right.id < left.id:
                left, right = right, left
            key = (left.id, right.id)
            emitted.add(key)
            kind, evidence, fingerprint = _evidence(left, right, setting_type)
            suggestion = existing.get(key)
            if suggestion is None:
                db.add(
                    LoreReviewSuggestion(
                        project_id=project_id,
                        left_element_id=left.id,
                        right_element_id=right.id,
                        rule_key=REVIEW_RULE_KEY,
                        rule_version=REVIEW_RULE_VERSION,
                        kind=kind,
                        detection_state="active",
                        review_status="pending",
                        left_content_version=left.content_version,
                        right_content_version=right.content_version,
                        evidence=evidence,
                        evidence_fingerprint=fingerprint,
                        evidence_revision=1,
                        lock_version=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
                created += 1
            elif (
                suggestion.evidence_fingerprint != fingerprint
                or suggestion.detection_state != "active"
            ):
                suggestion.kind = kind
                suggestion.detection_state = "active"
                suggestion.left_content_version = left.content_version
                suggestion.right_content_version = right.content_version
                suggestion.evidence = evidence
                suggestion.evidence_fingerprint = fingerprint
                suggestion.evidence_revision += 1
                suggestion.lock_version += 1
                suggestion.updated_at = now
                updated += 1
            else:
                unchanged += 1
        if pair_count >= MAX_SCAN_PAIRS:
            break

    if not truncated:
        for key, suggestion in existing.items():
            if key not in emitted and suggestion.detection_state != "stale":
                suggestion.detection_state = "stale"
                suggestion.lock_version += 1
                suggestion.updated_at = now
                marked_stale += 1

    check_writes_available()
    await db.commit()
    active_total = await db.scalar(
        select(func.count()).select_from(LoreReviewSuggestion).where(
            LoreReviewSuggestion.project_id == project_id,
            LoreReviewSuggestion.detection_state == "active",
        )
    )
    pending_total = await db.scalar(
        select(func.count()).select_from(LoreReviewSuggestion).where(
            LoreReviewSuggestion.project_id == project_id,
            LoreReviewSuggestion.detection_state == "active",
            LoreReviewSuggestion.review_status.in_(("pending", "deferred")),
        )
    )
    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "marked_stale": marked_stale,
        "active_total": int(active_total or 0),
        "pending_total": int(pending_total or 0),
        "truncated": truncated,
        "rescan_required": truncated,
    }
