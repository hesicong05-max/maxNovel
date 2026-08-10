"""Pure, versioned planning for a zero-write legacy Lore migration preview."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any, Iterable

from app.core.legacy_json import read_legacy_json, read_legacy_object_list
from app.core.lore_migration import (
    BUILTIN_TYPE_KEYS,
    PARSED_CATEGORY_MAP,
    TYPE_FIELD_SCHEMAS,
    deterministic_element_id,
    legacy_worldview_checksum,
    normalize_lore_name,
)


PREVIEW_SCHEMA_VERSION = 1
MAPPING_VERSION = 1
PREVIEW_TYPE_MAP: dict[str, str | None] = {
    "characters": "character",
    "geography": "location",
    "factions": "faction",
    "power_system": "ability_system",
    "history": "historical_event",
    "conflicts": "conflict",
    "special_settings": None,
}
_SOURCE_LABELS = {
    "manual": "手动创建",
    "imported": "文档导入",
    "hybrid": "文档导入与手动补充",
}
_FIELD_ALIASES: dict[str, dict[str, str]] = {
    "characters": {
        "motivation": "motivations",
        "ability": "abilities",
    },
}
_STATUS_ORDER = {
    "mappable": 0,
    "review_required": 1,
    "possible_conflict": 2,
    "blocked": 3,
}
RESOLVABLE_REASON_CODES = frozenset({
    "type_confirmation_required",
    "source_missing",
    "raw_text_excerpt_unverified",
    "unmapped_fields",
    "duplicate_name_same_type",
    "duplicate_name_cross_type",
})
_HARD_BLOCK_REASONS = frozenset({
    "non_object_entry",
    "missing_name",
    "source_unknown",
    "duplicate_legacy_id",
    "existing_element_name_collision",
})
_DUPLICATE_REASONS = frozenset({
    "duplicate_name_same_type", "duplicate_name_cross_type"
})


def _legacy_value(value: Any) -> Any:
    result = read_legacy_json(value)
    if result.valid:
        return result.value
    return {"__legacy_json_error__": result.error_category}


def migration_preview_source_checksum(worldview: Any | None) -> str:
    """Fingerprint every legacy input that can change preview classification."""
    payload = {
        "legacy_checksum": legacy_worldview_checksum(worldview),
        "raw_text": getattr(worldview, "raw_text", None) if worldview is not None else None,
    }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _item_name(category: str, raw: dict[str, Any]) -> str:
    value = raw.get("event") if category == "history" else raw.get("name")
    return str(value).strip() if value is not None else ""


def migration_preview_item_fingerprint(
    legacy_category: str,
    legacy_index: int,
    legacy_id: str | None,
    original_value: Any,
) -> str:
    """Bind a preview row to its exact position and canonical legacy value."""
    payload = [legacy_category, legacy_index, legacy_id, _json_value(original_value)]
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def migration_preview_group_fingerprint(
    items: Iterable[dict[str, Any]],
    *,
    type_field: str = "proposed_type_key",
) -> str:
    payload = sorted(
        (str(item["item_fingerprint"]), str(item.get(type_field) or ""))
        for item in items
    )
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode()).hexdigest()


def _issue(
    reason_code: str,
    severity: str,
    legacy_category: str | None,
    legacy_index: int | None,
    message: str,
    recommended_action: str,
) -> dict[str, Any]:
    identity = json.dumps(
        [reason_code, legacy_category, legacy_index],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "case_id": hashlib.sha256(identity.encode()).hexdigest()[:16],
        "severity": severity,
        "reason_code": reason_code,
        "legacy_category": legacy_category,
        "legacy_index": legacy_index,
        "message": message,
        "recommended_action": recommended_action,
    }


def _promote(item: dict[str, Any], classification: str, reason_code: str) -> None:
    if _STATUS_ORDER[classification] > _STATUS_ORDER[item["classification"]]:
        item["classification"] = classification
    if reason_code not in item["reason_codes"]:
        item["reason_codes"].append(reason_code)


def _parsed_by_category(
    worldview: Any,
) -> tuple[dict[str, list[dict[str, Any]]], str | None]:
    parsed_result = read_legacy_object_list(
        getattr(worldview, "parsed_elements", None)
    )
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not parsed_result.valid:
        return result, parsed_result.error_category
    for value in parsed_result.items:
        result[str(value.get("category") or "")].append(value)
    return result, None


def project_legacy_item_fields(
    legacy_category: str,
    original_value: Any,
    target_type_key: str | None,
) -> tuple[dict[str, Any], list[str]]:
    """Project one legacy object into one selected type without mutating input."""
    raw = original_value if isinstance(original_value, dict) else {}
    schema_keys = {
        field["key"] for field in TYPE_FIELD_SCHEMAS.get(target_type_key or "", [])
    }
    consumed_fields = {"name", "event"}
    mapped_fields: dict[str, Any] = {}
    aliases = _FIELD_ALIASES.get(legacy_category, {})
    for key in sorted(raw):
        target_key = aliases.get(key, key)
        if target_key in schema_keys:
            mapped_fields[target_key] = raw[key]
            consumed_fields.add(key)
    unmapped_fields = sorted(
        key for key in set(raw) - consumed_fields
        if raw[key] not in (None, "", [], {})
    )
    return mapped_fields, unmapped_fields


def build_migration_preview(
    project_id: str,
    storage_mode: str,
    worldview: Any | None,
    *,
    existing_elements: Iterable[Any] = (),
    existing_legacy_map_count: int = 0,
    existing_migration_count: int = 0,
    commit_enabled: bool = False,
    resolutions: Iterable[Any] = (),
) -> dict[str, Any]:
    """Return a deterministic plan without constructing or mutating ORM rows."""
    source_checksum = migration_preview_source_checksum(worldview)
    issues: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []

    if storage_mode != "legacy":
        issues.append(_issue(
            "project_not_legacy", "blocked", None, None,
            "当前项目不是兼容资料模式，不能生成旧资料迁移预检。",
            "返回设定仓库并使用当前正式仓库流程。",
        ))
    if worldview is None:
        issues.append(_issue(
            "worldview_missing", "blocked", None, None,
            "项目没有可检查的旧世界观记录。",
            "先保存世界观资料，再重新检查。",
        ))
    if existing_legacy_map_count:
        issues.append(_issue(
            "existing_legacy_map", "blocked", None, None,
            "检测到既有旧资料映射，当前状态不能按全新迁移计划处理。",
            "由开发者核对既有映射完整性后再继续。",
        ))
    if existing_migration_count:
        issues.append(_issue(
            "existing_migration_state", "blocked", None, None,
            "检测到既有迁移状态记录，必须先核对其终态。",
            "由开发者审查迁移历史，禁止重复启动。",
        ))

    existing = list(existing_elements)
    if existing:
        issues.append(_issue(
            "existing_formal_elements", "blocked", None, None,
            "兼容资料项目中已存在正式设定，自动规划可能覆盖或碰撞现有事实。",
            "先核对正式设定与旧资料的对应关系。",
        ))
    existing_names = {
        (str(getattr(element, "type_key", "")), normalize_lore_name(str(getattr(element, "name", ""))))
        for element in existing
    }

    if worldview is not None:
        source = str(getattr(worldview, "source", "") or "").strip()
        parsed, parsed_error = _parsed_by_category(worldview)
        if parsed_error is not None:
            issues.append(_issue(
                "invalid_collection", "blocked", None, None,
                "旧资料的解析索引无法安全读取。",
                "修正 parsed_elements 的数据结构后重新检查。",
            ))
        seen_legacy_ids: Counter[str] = Counter()
        for values in parsed.values():
            for parsed_item in values:
                legacy_id = str(parsed_item.get("id") or "").strip()
                if legacy_id:
                    seen_legacy_ids[legacy_id] += 1

        for category, target_type in PREVIEW_TYPE_MAP.items():
            values = _legacy_value(getattr(worldview, category, None))
            if values is None:
                values = []
            if not isinstance(values, list):
                issues.append(_issue(
                    "invalid_collection", "blocked", category, None,
                    "旧资料集合不是可安全读取的列表。",
                    "修正该分类的数据结构后重新检查。",
                ))
                continue
            parsed_values = parsed.get(PARSED_CATEGORY_MAP[category], [])
            for index, value in enumerate(values):
                raw = _json_value(value)
                raw_dict = raw if isinstance(raw, dict) else {}
                name = _item_name(category, raw_dict)
                parsed_item = parsed_values[index] if index < len(parsed_values) else {}
                parsed_name = str(parsed_item.get("name") or "").strip()
                aligned = bool(name and parsed_name and normalize_lore_name(name) == normalize_lore_name(parsed_name))
                legacy_id = str(parsed_item.get("id") or "").strip() if aligned else ""
                item = {
                    "legacy_category": category,
                    "legacy_index": index,
                    "legacy_id": legacy_id or None,
                    "item_fingerprint": migration_preview_item_fingerprint(
                        category, index, legacy_id or None, raw
                    ),
                    "planned_element_id": deterministic_element_id(project_id, category, index, legacy_id or None),
                    "proposed_type_key": target_type,
                    "name": name,
                    "classification": "mappable",
                    "reason_codes": [],
                    "source_locator": f"worldviews:{project_id}:{category}:{index}",
                    "source_kind": source or None,
                    "source_label": _SOURCE_LABELS.get(source),
                    "exact_excerpt_available": False,
                    "original_value": raw,
                    "mapped_fields": {},
                    "unmapped_fields": [],
                    "original_group_fingerprint": None,
                    "group_fingerprint": None,
                    "effective_classification": "mappable",
                    "effective_proposed_type_key": target_type,
                    "effective_source_kind": source or None,
                    "effective_mapped_fields": {},
                    "effective_unmapped_fields": [],
                    "effective_reason_codes": [],
                    "applied_resolution_ids": [],
                    "resolution_states": [],
                }
                if not isinstance(raw, dict):
                    _promote(item, "blocked", "non_object_entry")
                if not name:
                    _promote(item, "blocked", "missing_name")
                if not source:
                    _promote(item, "review_required", "source_missing")
                elif source not in _SOURCE_LABELS:
                    _promote(item, "blocked", "source_unknown")
                if target_type is None:
                    _promote(item, "review_required", "type_confirmation_required")
                if parsed_name and not aligned:
                    _promote(item, "review_required", "parsed_name_mismatch")
                if legacy_id and seen_legacy_ids[legacy_id] > 1:
                    _promote(item, "blocked", "duplicate_legacy_id")
                if source in {"imported", "hybrid"} and getattr(worldview, "raw_text", None):
                    _promote(item, "review_required", "raw_text_excerpt_unverified")

                mapped_fields, unmapped_fields = project_legacy_item_fields(
                    category, raw, target_type
                )
                item["mapped_fields"] = mapped_fields
                item["unmapped_fields"] = unmapped_fields
                item["effective_mapped_fields"] = dict(mapped_fields)
                item["effective_unmapped_fields"] = list(unmapped_fields)
                if item["unmapped_fields"]:
                    _promote(item, "review_required", "unmapped_fields")
                if target_type and (target_type, normalize_lore_name(name)) in existing_names:
                    _promote(item, "blocked", "existing_element_name_collision")
                items.append(item)

    grouped_names: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if item["name"]:
            grouped_names[normalize_lore_name(item["name"])].append(item)
    for group in grouped_names.values():
        if len(group) < 2:
            continue
        group_fingerprint = migration_preview_group_fingerprint(group)
        target_types = {item["proposed_type_key"] for item in group}
        reason = "duplicate_name_same_type" if len(target_types) == 1 else "duplicate_name_cross_type"
        for item in group:
            item["original_group_fingerprint"] = group_fingerprint
            item["group_fingerprint"] = group_fingerprint
            _promote(item, "possible_conflict", reason)

    resolution_rows = list(resolutions)
    effective_reasons: dict[str, list[str]] = {}

    # Type and source decisions must apply before field projection and grouping.
    for item in items:
        for row in resolution_rows:
            reason = str(getattr(row, "reason_code", ""))
            item_match = (
                str(getattr(row, "legacy_category", "")) == item["legacy_category"]
                and int(getattr(row, "legacy_index", -1)) == item["legacy_index"]
            )
            if not item_match:
                continue
            base_exact = (
                int(getattr(row, "preview_schema_version", 0)) == PREVIEW_SCHEMA_VERSION
                and int(getattr(row, "mapping_version", 0)) == MAPPING_VERSION
                and str(getattr(row, "source_checksum", "")) == source_checksum
                and str(getattr(row, "item_fingerprint", ""))
                == item["item_fingerprint"]
            )
            row_status = str(getattr(row, "status", ""))
            state = {
                "id": str(getattr(row, "id")),
                "legacy_category": item["legacy_category"],
                "legacy_index": item["legacy_index"],
                "reason_code": str(getattr(row, "reason_code")),
                "decision_code": str(getattr(row, "decision_code")),
                "decision_payload": dict(getattr(row, "decision_payload", None) or {}),
                "status": "expired" if not base_exact else row_status,
                "lock_version": int(getattr(row, "lock_version", 1)),
                "created_at": getattr(row, "created_at").isoformat(),
                "updated_at": getattr(row, "updated_at").isoformat(),
                "applies": False,
            }
            item["resolution_states"].append(state)
            if not base_exact or row_status != "active":
                continue
            decision = str(getattr(row, "decision_code"))
            payload = dict(getattr(row, "decision_payload", None) or {})
            if reason == "type_confirmation_required":
                type_key = str(payload.get("type_key") or "")
                valid = (
                    reason in item["reason_codes"]
                    and decision == "confirm_type"
                    and type_key in BUILTIN_TYPE_KEYS
                )
                if valid:
                    item["effective_proposed_type_key"] = type_key
            elif reason == "source_missing":
                source_kind = str(payload.get("source_kind") or "")
                valid = (
                    reason in item["reason_codes"]
                    and decision == "confirm_source"
                    and source_kind in _SOURCE_LABELS
                )
                if valid:
                    item["effective_source_kind"] = source_kind
            else:
                continue
            if valid:
                state["applies"] = True
                item["applied_resolution_ids"].append(str(getattr(row, "id")))
            else:
                state["status"] = "expired"

        mapped_fields, unmapped_fields = project_legacy_item_fields(
            item["legacy_category"],
            item["original_value"],
            item["effective_proposed_type_key"],
        )
        item["effective_mapped_fields"] = mapped_fields
        item["effective_unmapped_fields"] = unmapped_fields
        reasons = [
            reason for reason in item["reason_codes"]
            if reason not in _DUPLICATE_REASONS
            and reason not in {"unmapped_fields", "existing_element_name_collision"}
        ]
        preliminarily_resolved = {
            state["reason_code"] for state in item["resolution_states"]
            if state.get("applies")
        }
        reasons = [reason for reason in reasons if reason not in preliminarily_resolved]
        if (
            item["effective_source_kind"] in {"imported", "hybrid"}
            and getattr(worldview, "raw_text", None)
            and "raw_text_excerpt_unverified" not in reasons
        ):
            reasons.append("raw_text_excerpt_unverified")
        if unmapped_fields:
            reasons.append("unmapped_fields")
        effective_type = item["effective_proposed_type_key"]
        if (
            effective_type
            and (effective_type, normalize_lore_name(item["name"])) in existing_names
        ):
            reasons.append("existing_element_name_collision")
        effective_reasons[item["item_fingerprint"]] = list(dict.fromkeys(reasons))

    effective_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        item["group_fingerprint"] = None
        if item["name"]:
            effective_groups[normalize_lore_name(item["name"])].append(item)
    duplicate_group_members: dict[str, set[str]] = defaultdict(set)
    for group in effective_groups.values():
        if len(group) < 2:
            continue
        group_fingerprint = migration_preview_group_fingerprint(
            group, type_field="effective_proposed_type_key"
        )
        target_types = {item["effective_proposed_type_key"] for item in group}
        reason = (
            "duplicate_name_same_type"
            if len(target_types) == 1
            else "duplicate_name_cross_type"
        )
        for item in group:
            item["group_fingerprint"] = group_fingerprint
            duplicate_group_members[group_fingerprint].add(item["item_fingerprint"])
            effective_reasons[item["item_fingerprint"]].append(reason)

    # Remaining decisions are evaluated against the effective projection and group.
    for item in items:
        reasons = list(dict.fromkeys(effective_reasons[item["item_fingerprint"]]))
        states_by_id = {
            state["id"]: state for state in item["resolution_states"]
        }
        for row in resolution_rows:
            row_id = str(getattr(row, "id"))
            state = states_by_id.get(row_id)
            if state is None or state["status"] != "active" or state.get("applies"):
                continue
            reason = str(getattr(row, "reason_code", ""))
            decision = str(getattr(row, "decision_code", ""))
            payload = dict(getattr(row, "decision_payload", None) or {})
            if reason == "raw_text_excerpt_unverified":
                valid = reason in reasons and decision == "accept_unlocated_source" and payload == {
                    "confirmed_by_author": True,
                    "exact_excerpt_available": False,
                }
            elif reason == "unmapped_fields":
                valid = (
                    reason in reasons
                    and decision == "preserve_unmapped_fields"
                    and sorted(payload.get("field_names") or [])
                    == item["effective_unmapped_fields"]
                )
            elif reason in _DUPLICATE_REASONS:
                group_fingerprint = item.get("group_fingerprint")
                valid = (
                    reason in reasons
                    and group_fingerprint is not None
                    and decision == "confirm_distinct_same_name"
                    and str(getattr(row, "group_fingerprint", ""))
                    == group_fingerprint
                    and sorted(payload.get("member_fingerprints") or [])
                    == sorted(duplicate_group_members[group_fingerprint])
                )
            else:
                valid = False
            if valid:
                state["applies"] = True
                item["applied_resolution_ids"].append(row_id)
            elif reason in RESOLVABLE_REASON_CODES:
                state["status"] = "expired"

        resolved_reasons = {
            state["reason_code"]
            for state in item["resolution_states"]
            if state.get("applies")
        }
        unresolved = [
            reason for reason in reasons if reason not in resolved_reasons
        ]
        item["effective_reason_codes"] = unresolved
        if any(reason in _HARD_BLOCK_REASONS for reason in unresolved):
            item["effective_classification"] = "blocked"
        elif any(reason in _DUPLICATE_REASONS for reason in unresolved):
            item["effective_classification"] = "possible_conflict"
        elif unresolved:
            item["effective_classification"] = "review_required"
        else:
            item["effective_classification"] = "mappable"

    applied_resolution_ids = sorted({
        resolution_id
        for item in items
        for resolution_id in item["applied_resolution_ids"]
    })

    for item in items:
        for reason in item["effective_reason_codes"]:
            severity = "blocked" if item["effective_classification"] == "blocked" else "review"
            issues.append(_issue(
                reason,
                severity,
                item["legacy_category"],
                item["legacy_index"],
                f"旧资料第 {item['legacy_index'] + 1} 项需要处理：{reason}。",
                "查看原始值和建议映射，确认或修正后重新检查。",
            ))

    items.sort(key=lambda item: (item["legacy_category"], item["legacy_index"], item["planned_element_id"]))
    issues.sort(key=lambda item: (item["legacy_category"] or "", item["legacy_index"] if item["legacy_index"] is not None else -1, item["reason_code"]))
    counts = Counter(item["effective_classification"] for item in items)
    has_blocking_issue = any(issue["severity"] == "blocked" for issue in issues)
    if has_blocking_issue or counts["blocked"]:
        overall_status = "blocked"
    elif counts["possible_conflict"] or counts["review_required"]:
        overall_status = "review_required"
    else:
        overall_status = "ready"

    semantic = {
        "preview_schema_version": PREVIEW_SCHEMA_VERSION,
        "mapping_version": MAPPING_VERSION,
        "project_id": project_id,
        "storage_mode": storage_mode,
        "source_checksum": source_checksum,
        "overall_status": overall_status,
        "items": items,
        "issues": issues,
        "applied_resolution_ids": sorted(set(applied_resolution_ids)),
    }
    semantic_result_checksum = hashlib.sha256(json.dumps(
        semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    return {
        **semantic,
        "semantic_result_checksum": semantic_result_checksum,
        "checked_at": datetime.now(UTC),
        "dry_run": True,
        "read_only": True,
        "writes_performed": 0,
        "commit_available": bool(
            commit_enabled
            and storage_mode == "legacy"
            and overall_status == "ready"
            and items
        ),
        "counts": {
            "legacy_total": len(items),
            "mappable": counts["mappable"],
            "review_required": counts["review_required"],
            "possible_conflict": counts["possible_conflict"],
            "blocked": counts["blocked"],
        },
        "by_legacy_category": dict(sorted(Counter(item["legacy_category"] for item in items).items())),
        "by_target_type": dict(sorted(Counter(
            item["effective_proposed_type_key"]
            for item in items
            if item["effective_proposed_type_key"]
        ).items())),
    }
