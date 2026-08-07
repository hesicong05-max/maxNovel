"""Pure, versioned planning for a zero-write legacy Lore migration preview."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any, Iterable

from app.core.lore_migration import (
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


def _legacy_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return value
        return decoded
    return value


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


def _parsed_by_category(worldview: Any) -> dict[str, list[dict[str, Any]]]:
    parsed = _legacy_value(getattr(worldview, "parsed_elements", None))
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not isinstance(parsed, list):
        return result
    for value in parsed:
        if isinstance(value, dict):
            result[str(value.get("category") or "")].append(value)
    return result


def build_migration_preview(
    project_id: str,
    storage_mode: str,
    worldview: Any | None,
    *,
    existing_elements: Iterable[Any] = (),
    existing_legacy_map_count: int = 0,
    existing_migration_count: int = 0,
    commit_enabled: bool = False,
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
        parsed = _parsed_by_category(worldview)
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

                schema_keys = {
                    field["key"] for field in TYPE_FIELD_SCHEMAS.get(target_type or "", [])
                }
                name_keys = {"name", "event"}
                mapped_fields: dict[str, Any] = {}
                consumed_fields = set(name_keys)
                aliases = _FIELD_ALIASES.get(category, {})
                for key in sorted(raw_dict):
                    target_key = aliases.get(key, key)
                    if target_key in schema_keys:
                        mapped_fields[target_key] = raw_dict[key]
                        consumed_fields.add(key)
                item["mapped_fields"] = mapped_fields
                item["unmapped_fields"] = sorted(
                    key for key in set(raw_dict) - consumed_fields
                    if raw_dict[key] not in (None, "", [], {})
                )
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
        target_types = {item["proposed_type_key"] for item in group}
        reason = "duplicate_name_same_type" if len(target_types) == 1 else "duplicate_name_cross_type"
        for item in group:
            _promote(item, "possible_conflict", reason)

    for item in items:
        for reason in item["reason_codes"]:
            severity = "blocked" if item["classification"] == "blocked" else "review"
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
    counts = Counter(item["classification"] for item in items)
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
        "by_target_type": dict(sorted(Counter(item["proposed_type_key"] for item in items if item["proposed_type_key"]).items())),
    }
