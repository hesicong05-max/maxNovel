"""Pure, read-only projection of legacy worldview JSON into normalized lore."""

from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any


LEGACY_CATEGORY_MAP = {
    "characters": ("character", "角色"),
    "geography": ("location", "地点"),
    "factions": ("faction", "阵营"),
    "power_system": ("rule", "规则与限制"),
    "history": ("event", "事件"),
    "conflicts": ("conflict", "冲突"),
    "special_settings": ("rule", "规则与限制"),
}

PARSED_CATEGORY_MAP = {
    "characters": "character",
    "geography": "geography",
    "factions": "faction",
    "power_system": "power_system",
    "history": "history",
    "conflicts": "conflict",
    "special_settings": "special_setting",
}

TYPE_DISPLAY_NAMES = {
    "world": "世界观",
    "character": "角色",
    "location": "地点",
    "scene": "场景",
    "faction": "阵营",
    "item": "物品",
    "conflict": "冲突",
    "event": "事件",
    "foreshadow": "伏笔",
    "rule": "规则与限制",
}

TYPE_FIELD_SCHEMAS: dict[str, list[dict[str, Any]]] = {
    "character": [
        {"key": "personality", "label": "性格", "control": "textarea", "order": 10},
        {"key": "background", "label": "背景", "control": "textarea", "order": 20},
        {"key": "motivation", "label": "目标与动机", "control": "textarea", "order": 30},
        {"key": "ability", "label": "能力", "control": "textarea", "order": 40},
    ],
    "location": [
        {"key": "description", "label": "描述", "control": "textarea", "order": 10},
        {"key": "significance", "label": "重要性", "control": "textarea", "order": 20},
    ],
    "faction": [
        {"key": "stance", "label": "立场", "control": "textarea", "order": 10},
        {"key": "power_level", "label": "实力", "control": "text", "order": 20},
    ],
    "rule": [
        {"key": "levels", "label": "层级", "control": "textarea", "order": 10},
        {"key": "rules", "label": "规则", "control": "textarea", "order": 20},
        {"key": "limitations", "label": "限制", "control": "textarea", "order": 30},
        {"key": "description", "label": "描述", "control": "textarea", "order": 40},
    ],
    "event": [
        {"key": "time", "label": "故事时间", "control": "text", "order": 10},
        {"key": "description", "label": "描述", "control": "textarea", "order": 20},
        {"key": "impact", "label": "影响", "control": "textarea", "order": 30},
    ],
    "conflict": [
        {"key": "type", "label": "类型", "control": "text", "order": 10},
        {"key": "parties", "label": "参与方", "control": "textarea", "order": 20},
        {"key": "stakes", "label": "赌注", "control": "textarea", "order": 30},
        {"key": "resolution_hint", "label": "解决线索", "control": "textarea", "order": 40},
    ],
}


@dataclass(frozen=True)
class ProjectedLoreElement:
    id: str
    type_key: str
    type_display_name: str
    name: str
    summary: str
    payload: dict[str, Any]
    legacy_raw: Any
    source_kind: str
    source_label: str
    legacy_category: str
    legacy_index: int
    legacy_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class LoreProjection:
    elements: list[ProjectedLoreElement]
    checksum: str
    warnings: list[str]


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def legacy_worldview_checksum(worldview: Any | None) -> str:
    """Return a stable checksum of the legacy fields that affect lore projection."""
    if worldview is None:
        payload: dict[str, Any] = {}
    else:
        payload = {
            category: _json_value(getattr(worldview, category, None) or [])
            for category in LEGACY_CATEGORY_MAP
        }
        payload["parsed_elements"] = _json_value(
            getattr(worldview, "parsed_elements", None) or []
        )
        payload["source"] = getattr(worldview, "source", None) or "manual"
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def legacy_structured_payload(worldview: Any | None) -> dict[str, list[Any]]:
    """Return only the seven arrays used for lossless compatibility checks."""
    if worldview is None:
        return {category: [] for category in LEGACY_CATEGORY_MAP}
    return {
        category: _json_value(getattr(worldview, category, None) or [])
        for category in LEGACY_CATEGORY_MAP
    }


def structured_payload_checksum(payload: dict[str, Any]) -> str:
    normalized = {
        category: _json_value(payload.get(category, []) or [])
        for category in LEGACY_CATEGORY_MAP
    }
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_lore_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def deterministic_element_id(
    project_id: str,
    legacy_category: str,
    legacy_index: int,
    legacy_id: str | None,
) -> str:
    """Namespace legacy IDs by project to prevent cross-project collisions."""
    identity = (
        f"lore:{project_id}:{legacy_category}:{legacy_index}:{legacy_id or '-'}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, identity).hex


def deterministic_type_id(project_id: str, type_key: str) -> str:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"lore-type:{project_id}:{type_key}",
    ).hex


def _as_dict(value: Any, default_name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, str):
        return {"name": value}
    return {"name": default_name}


def _name_for(category: str, item: dict[str, Any], index: int) -> str:
    if category == "history":
        return str(item.get("event") or f"历史事件{index + 1}")
    defaults = {
        "characters": "角色",
        "geography": "地点",
        "factions": "阵营",
        "power_system": "规则",
        "conflicts": "冲突",
        "special_settings": "特殊设定",
    }
    return str(item.get("name") or f"{defaults.get(category, '设定')}{index + 1}")


def _summary_for(category: str, item: dict[str, Any]) -> str:
    fields = {
        "characters": ("personality", "motivation", "background"),
        "geography": ("description", "significance"),
        "factions": ("stance", "power_level"),
        "power_system": ("rules", "limitations", "levels"),
        "history": ("description", "impact", "time"),
        "conflicts": ("stakes", "parties", "resolution_hint"),
        "special_settings": ("description", "rules"),
    }
    for field in fields.get(category, ()):
        value = item.get(field)
        if value:
            return str(value)[:500]
    return ""


def _source_info(source: str) -> tuple[str, str]:
    if source == "imported":
        return "document_import", "文档导入并由用户保存"
    if source == "hybrid":
        return "document_import", "文档导入与手动补充"
    return "manual", "手动创建"


def project_legacy_worldview(
    project_id: str,
    worldview: Any | None,
) -> LoreProjection:
    """Project legacy arrays without writing to the database or project files."""
    checksum = legacy_worldview_checksum(worldview)
    if worldview is None:
        return LoreProjection([], checksum, [])

    parsed = getattr(worldview, "parsed_elements", None) or []
    parsed_by_category: dict[str, list[dict[str, Any]]] = {}
    if isinstance(parsed, list):
        for raw in parsed:
            if not isinstance(raw, dict):
                continue
            parsed_by_category.setdefault(str(raw.get("category", "")), []).append(raw)

    source_kind, source_label = _source_info(
        str(getattr(worldview, "source", None) or "manual")
    )
    created_at = getattr(worldview, "created_at", None) or datetime(1970, 1, 1)
    elements: list[ProjectedLoreElement] = []
    warnings: list[str] = []

    for legacy_category, (type_key, type_display_name) in LEGACY_CATEGORY_MAP.items():
        values = getattr(worldview, legacy_category, None) or []
        if not isinstance(values, list):
            warnings.append(f"{legacy_category}:invalid_collection")
            continue

        parsed_category = PARSED_CATEGORY_MAP[legacy_category]
        parsed_values = parsed_by_category.get(parsed_category, [])
        for index, raw in enumerate(values):
            legacy_raw = _json_value(raw)
            item = _as_dict(legacy_raw, f"{type_display_name}{index + 1}")
            name = _name_for(legacy_category, item, index)
            parsed_item = parsed_values[index] if index < len(parsed_values) else {}
            parsed_name = str(parsed_item.get("name") or "")
            if parsed_name and normalize_lore_name(parsed_name) != normalize_lore_name(name):
                warnings.append(f"{legacy_category}:{index}:parsed_name_mismatch")
                parsed_item = {}

            legacy_id_value = parsed_item.get("id")
            legacy_id = str(legacy_id_value) if legacy_id_value else None
            payload = dict(item)
            if legacy_category in {"power_system", "special_settings"}:
                payload["legacy_category"] = legacy_category
            elements.append(
                ProjectedLoreElement(
                    id=deterministic_element_id(
                        project_id,
                        legacy_category,
                        index,
                        legacy_id,
                    ),
                    type_key=type_key,
                    type_display_name=type_display_name,
                    name=name,
                    summary=_summary_for(legacy_category, item),
                    payload=payload,
                    legacy_raw=legacy_raw,
                    source_kind=source_kind,
                    source_label=source_label,
                    legacy_category=legacy_category,
                    legacy_index=index,
                    legacy_id=legacy_id,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )

    return LoreProjection(elements, checksum, warnings)


def type_field_definitions(type_key: str) -> list[dict[str, Any]]:
    return [dict(field) for field in TYPE_FIELD_SCHEMAS.get(type_key, [])]


def build_legacy_compatibility_projection(
    projection: LoreProjection,
) -> dict[str, list[Any]]:
    """Rebuild the seven legacy arrays as a pure compatibility projection."""
    result: dict[str, list[Any]] = {
        category: [] for category in LEGACY_CATEGORY_MAP
    }
    for element in sorted(
        projection.elements,
        key=lambda item: (item.legacy_category, item.legacy_index),
    ):
        if element.legacy_category in result:
            result[element.legacy_category].append(_json_value(element.legacy_raw))
    return result


def compare_legacy_file_payload(
    worldview: Any | None,
    file_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compare a project JSON document with the DB fact source without writing."""
    database_payload = legacy_structured_payload(worldview)
    file_payload = file_payload or {}
    database_checksum = structured_payload_checksum(database_payload)
    file_checksum = structured_payload_checksum(file_payload)
    return {
        "matches": database_checksum == file_checksum,
        "database_checksum": database_checksum,
        "file_checksum": file_checksum,
    }


def validate_projection(projection: LoreProjection) -> dict[str, Any]:
    ids = [element.id for element in projection.elements]
    return {
        "valid": len(ids) == len(set(ids)) and not any(
            warning.endswith("invalid_collection") for warning in projection.warnings
        ),
        "total": len(projection.elements),
        "by_type": {
            type_key: sum(
                element.type_key == type_key for element in projection.elements
            )
            for type_key in sorted({element.type_key for element in projection.elements})
        },
        "warnings": list(projection.warnings),
        "checksum": projection.checksum,
    }
