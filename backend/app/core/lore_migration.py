"""Pure, read-only projection of legacy worldview JSON into normalized lore."""

from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.legacy_json import read_legacy_json, read_legacy_object_list


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
    "ability_system": "能力体系",
    "race": "种族",
    "historical_event": "历史事件",
    "social_institution": "社会制度",
    "other": "其他",
}

BUILTIN_TYPE_KEYS = frozenset(TYPE_DISPLAY_NAMES.keys())

TYPE_FIELD_SCHEMAS: dict[str, list[dict[str, Any]]] = {
    "world": [
        {"key": "description", "label": "描述", "control": "textarea", "value_type": "text", "help": "世界观的核心描述", "order": 10, "required": False},
        {"key": "rules", "label": "规则", "control": "textarea", "value_type": "text", "help": "世界运行的规则", "order": 20, "required": False},
        {"key": "history", "label": "历史", "control": "textarea", "value_type": "text", "help": "世界的历史背景", "order": 30, "required": False},
    ],
    "character": [
        {"key": "identity", "label": "身份", "control": "text", "value_type": "string", "help": "角色的身份标识", "order": 5, "required": False},
        {"key": "appearance", "label": "外貌", "control": "textarea", "value_type": "text", "help": "角色的外貌描述", "order": 10, "required": False},
        {"key": "personality", "label": "性格", "control": "textarea", "value_type": "text", "help": "角色的性格特点", "order": 20, "required": False},
        {"key": "background", "label": "背景", "control": "textarea", "value_type": "text", "help": "角色的背景故事", "order": 30, "required": False},
        {"key": "abilities", "label": "能力", "control": "textarea", "value_type": "text", "help": "角色的能力和特长", "order": 40, "required": False},
        {"key": "limitations", "label": "限制", "control": "textarea", "value_type": "text", "help": "角色的弱点和限制", "order": 50, "required": False},
        {"key": "goals", "label": "目标", "control": "textarea", "value_type": "text", "help": "角色的目标", "order": 60, "required": False},
        {"key": "motivations", "label": "动机", "control": "textarea", "value_type": "text", "help": "角色的内在动机", "order": 70, "required": False},
        {"key": "possible_plots", "label": "可能剧情", "control": "textarea", "value_type": "text", "help": "角色可能参与的剧情线索", "order": 80, "required": False},
    ],
    "location": [
        {"key": "description", "label": "描述", "control": "textarea", "value_type": "text", "help": "地点的具体描述", "order": 10, "required": False},
        {"key": "significance", "label": "重要性", "control": "textarea", "value_type": "text", "help": "地点在故事中的重要性", "order": 20, "required": False},
        {"key": "geography", "label": "地理特征", "control": "textarea", "value_type": "text", "help": "地点的地理特征和环境", "order": 30, "required": False},
    ],
    "scene": [
        {"key": "time", "label": "故事时间", "control": "text", "value_type": "string", "help": "场景发生的故事时间", "order": 10, "required": False},
        {"key": "purpose", "label": "目的", "control": "textarea", "value_type": "text", "help": "场景在叙事中的目的", "order": 40, "required": False},
        {"key": "description", "label": "描述", "control": "textarea", "value_type": "text", "help": "场景的具体描述", "order": 50, "required": False},
    ],
    "faction": [
        {"key": "stance", "label": "立场", "control": "textarea", "value_type": "text", "help": "阵营的立场和理念", "order": 10, "required": False},
        {"key": "power_level", "label": "实力", "control": "text", "value_type": "string", "help": "阵营的实力等级", "order": 20, "required": False},
        {"key": "goal", "label": "目标", "control": "textarea", "value_type": "text", "help": "阵营的目标", "order": 30, "required": False},
        {"key": "structure", "label": "组织结构", "control": "textarea", "value_type": "text", "help": "阵营的组织结构", "order": 40, "required": False},
    ],
    "item": [
        {"key": "description", "label": "描述", "control": "textarea", "value_type": "text", "help": "物品的具体描述", "order": 10, "required": False},
        {"key": "origin", "label": "来源", "control": "textarea", "value_type": "text", "help": "物品的来源与创造", "order": 20, "required": False},
        {"key": "power", "label": "能力/效果", "control": "textarea", "value_type": "text", "help": "物品的能力或特殊效果", "order": 30, "required": False},
        {"key": "limitations", "label": "限制", "control": "textarea", "value_type": "text", "help": "物品的使用限制", "order": 40, "required": False},
    ],
    "conflict": [
        {"key": "type", "label": "类型", "control": "text", "value_type": "string", "help": "冲突的类型", "order": 10, "required": False},
        {"key": "parties", "label": "参与方", "control": "textarea", "value_type": "text", "help": "冲突的参与方", "order": 20, "required": False},
        {"key": "stakes", "label": "赌注", "control": "textarea", "value_type": "text", "help": "冲突的赌注", "order": 30, "required": False},
        {"key": "resolution_hint", "label": "解决线索", "control": "textarea", "value_type": "text", "help": "解决冲突的线索", "order": 40, "required": False},
        {"key": "status", "label": "当前状态", "control": "text", "value_type": "string", "help": "冲突的当前状态", "order": 50, "required": False},
    ],
    "event": [
        {"key": "time", "label": "故事时间", "control": "text", "value_type": "string", "help": "事件发生的故事时间", "order": 10, "required": False},
        {"key": "description", "label": "描述", "control": "textarea", "value_type": "text", "help": "事件的核心描述", "order": 20, "required": False},
        {"key": "impact", "label": "影响", "control": "textarea", "value_type": "text", "help": "事件对故事的影响", "order": 30, "required": False},
    ],
    "foreshadow": [
        {"key": "description", "label": "描述", "control": "textarea", "value_type": "text", "help": "伏笔的具体描述", "order": 10, "required": False},
        {"key": "hint", "label": "线索提示", "control": "textarea", "value_type": "text", "help": "埋入章节中的线索提示", "order": 50, "required": False},
    ],
    "rule": [
        {"key": "levels", "label": "层级", "control": "textarea", "value_type": "text", "help": "规则体系的层级", "order": 10, "required": False},
        {"key": "rules", "label": "规则", "control": "textarea", "value_type": "text", "help": "具体的规则内容", "order": 20, "required": False},
        {"key": "limitations", "label": "限制", "control": "textarea", "value_type": "text", "help": "规则的限制条件", "order": 30, "required": False},
        {"key": "description", "label": "描述", "control": "textarea", "value_type": "text", "help": "规则的详细描述", "order": 40, "required": False},
        {"key": "scope", "label": "适用范围", "control": "textarea", "value_type": "text", "help": "规则适用的范围", "order": 50, "required": False},
    ],
    "ability_system": [
        {"key": "description", "label": "描述", "control": "textarea", "value_type": "text", "help": "能力体系的核心描述", "order": 10, "required": False},
        {"key": "levels", "label": "等级划分", "control": "textarea", "value_type": "text", "help": "能力等级划分", "order": 20, "required": False},
        {"key": "rules", "label": "规则", "control": "textarea", "value_type": "text", "help": "能力体系的规则", "order": 30, "required": False},
        {"key": "limitations", "label": "限制", "control": "textarea", "value_type": "text", "help": "能力体系的限制", "order": 40, "required": False},
    ],
    "race": [
        {"key": "description", "label": "描述", "control": "textarea", "value_type": "text", "help": "种族的核心描述", "order": 10, "required": False},
        {"key": "traits", "label": "特征", "control": "textarea", "value_type": "text", "help": "种族的生理和文化特征", "order": 20, "required": False},
        {"key": "habitat", "label": "栖息地", "control": "textarea", "value_type": "text", "help": "种族的栖息地", "order": 30, "required": False},
    ],
    "historical_event": [
        {"key": "time", "label": "时间", "control": "text", "value_type": "string", "help": "历史事件发生的时间", "order": 10, "required": False},
        {"key": "description", "label": "描述", "control": "textarea", "value_type": "text", "help": "历史事件的核心描述", "order": 20, "required": False},
        {"key": "impact", "label": "影响", "control": "textarea", "value_type": "text", "help": "历史事件对世界的影响", "order": 30, "required": False},
        {"key": "key_figures", "label": "关键人物", "control": "textarea", "value_type": "text", "help": "历史事件中的关键人物", "order": 40, "required": False},
    ],
    "social_institution": [
        {"key": "description", "label": "描述", "control": "textarea", "value_type": "text", "help": "社会制度的核心描述", "order": 10, "required": False},
        {"key": "structure", "label": "组织结构", "control": "textarea", "value_type": "text", "help": "社会制度的组织结构", "order": 20, "required": False},
        {"key": "function", "label": "职能", "control": "textarea", "value_type": "text", "help": "社会制度的职能", "order": 30, "required": False},
        {"key": "scope", "label": "适用范围", "control": "textarea", "value_type": "text", "help": "社会制度的适用范围", "order": 40, "required": False},
    ],
    "other": [
        {"key": "description", "label": "描述", "control": "textarea", "value_type": "text", "help": "自定义设定的描述", "order": 10, "required": False},
        {"key": "details", "label": "详细信息", "control": "textarea", "value_type": "text", "help": "其他补充信息", "order": 20, "required": False},
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


def _legacy_collection_value(value: Any) -> Any:
    """Normalize a legacy collection stored as JSON or historical Text."""
    if value is None:
        return []
    result = read_legacy_json(value)
    if not result.valid:
        return {"__legacy_json_error__": result.error_category}
    return _json_value(result.value)


def legacy_worldview_checksum(worldview: Any | None) -> str:
    """Return a stable checksum of the legacy fields that affect lore projection."""
    if worldview is None:
        payload: dict[str, Any] = {}
    else:
        payload = {
            category: _legacy_collection_value(
                getattr(worldview, category, None)
            )
            for category in LEGACY_CATEGORY_MAP
        }
        payload["parsed_elements"] = _legacy_collection_value(
            getattr(worldview, "parsed_elements", None)
        )
        payload["source"] = getattr(worldview, "source", None) or "manual"
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def legacy_structured_payload(worldview: Any | None) -> dict[str, Any]:
    """Return only the seven arrays used for lossless compatibility checks."""
    if worldview is None:
        return {category: [] for category in LEGACY_CATEGORY_MAP}
    return {
        category: _legacy_collection_value(
            getattr(worldview, category, None)
        )
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

    parsed_result = read_legacy_object_list(
        getattr(worldview, "parsed_elements", None)
    )
    parsed_by_category: dict[str, list[dict[str, Any]]] = {}
    warnings: list[str] = []
    if not parsed_result.valid:
        warnings.append("parsed_elements:invalid_collection")
    else:
        for raw in parsed_result.items:
            parsed_by_category.setdefault(str(raw.get("category", "")), []).append(raw)

    source_kind, source_label = _source_info(
        str(getattr(worldview, "source", None) or "manual")
    )
    created_at = getattr(worldview, "created_at", None) or datetime(1970, 1, 1)
    elements: list[ProjectedLoreElement] = []

    for legacy_category, (type_key, type_display_name) in LEGACY_CATEGORY_MAP.items():
        values = _legacy_collection_value(
            getattr(worldview, legacy_category, None)
        )
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
