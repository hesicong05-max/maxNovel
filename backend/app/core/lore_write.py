"""Transactional write service for relational lore elements.

All mutations enforce optimistic locking via expected_version with
row-level SELECT ... FOR UPDATE, check maintenance freeze before every
write, and scope everything to a single database transaction.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.lore_migration import (
    BUILTIN_TYPE_KEYS,
    TYPE_DISPLAY_NAMES,
    TYPE_FIELD_SCHEMAS,
)
from app.core.maintenance import ensure_project_writes_available
from app.models.lore import (
    ElementRelation,
    ElementRelationVersion,
    ElementSource,
    ElementStateEvent,
    ElementVersion,
    SettingElement,
    SettingType,
    SettingTypeRevision,
)

_LORE_VERSION_CONFLICT_CODE = "LORE_VERSION_CONFLICT"


class LoreWriteError(Exception):
    """Non-recoverable write precondition violated."""

    def __init__(self, detail: Any, status_code: int = 422) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class LoreStaleVersionError(Exception):
    """The provided expected_version does not match the current lock_version."""

    def __init__(self, current_lock_version: int, updated_at: Any = None) -> None:
        super().__init__()
        self.current_lock_version = current_lock_version
        self.updated_at = updated_at


def check_writes_available() -> None:
    """Use the shared maintenance exception and global public response contract."""
    ensure_project_writes_available()


def _excerpt_hash(excerpt: str | None) -> str | None:
    if not excerpt:
        return None
    return hashlib.sha256(excerpt.encode("utf-8")).hexdigest()


def field_schema_for_type(setting_type: SettingType) -> list[dict[str, Any]]:
    schema = setting_type.field_schema
    if isinstance(schema, list) and schema:
        return [dict(field) for field in schema]
    if setting_type.is_builtin:
        return [dict(field) for field in TYPE_FIELD_SCHEMAS.get(setting_type.key, [])]
    return []


def generation_eligible(element: SettingElement) -> bool:
    return (
        element.confirmation_status == "confirmed"
        and element.lifecycle_status == "active"
        and element.enabled
        and "needs_confirmation" not in (element.field_states or {}).values()
    )


async def _claim_lock_version(
    db: AsyncSession,
    model: Any,
    row: Any,
    expected_version: int,
) -> int:
    conditions = [
        model.id == row.id,
        model.lock_version == expected_version,
    ]
    if hasattr(model, "project_id") and hasattr(row, "project_id"):
        conditions.append(model.project_id == row.project_id)
    result = await db.execute(
        update(model)
        .where(*conditions)
        .values(lock_version=expected_version + 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        latest = await db.execute(
            select(model.lock_version, model.updated_at).where(model.id == row.id)
        )
        current = latest.one_or_none()
        raise LoreStaleVersionError(
            current[0] if current else row.lock_version,
            current[1] if current else row.updated_at,
        )
    await db.refresh(row)
    return row.lock_version


def _validate_payload_keys(
    payload: dict[str, Any],
    field_schema: list[dict[str, Any]],
) -> None:
    """Reject payload keys that are not in the type's field schema."""
    schema_keys = {field["key"] for field in field_schema}
    for key in payload:
        if key not in schema_keys:
            raise LoreWriteError(
                detail={
                    "code": "LORE_FIELD_INVALID",
                    "message": "设定字段校验失败",
                    "field_errors": [
                        {"field": key, "message": "字段不属于当前设定类型"}
                    ],
                },
                status_code=422,
            )


def _validate_payload_values(
    payload: dict[str, Any],
    field_schema: list[dict[str, Any]],
) -> None:
    definitions = {field["key"]: field for field in field_schema}
    errors: list[dict[str, str]] = []
    for key, value in payload.items():
        if value is None:
            continue
        value_type = definitions[key].get("value_type", "string")
        if value_type in ("string", "text", "reference") and not isinstance(
            value, str
        ):
            errors.append(
                {
                    "field": key,
                    "message": f"字段值必须是 {value_type} 字符串",
                }
            )
    if errors:
        raise LoreWriteError(
            detail={
                "code": "LORE_FIELD_INVALID",
                "message": "设定字段校验失败",
                "field_errors": errors,
            },
            status_code=422,
        )


def _validate_field_states(
    field_states: dict[str, str],
    payload: dict[str, Any],
    field_schema: list[dict[str, Any]],
) -> None:
    """Validate field_states against schema and payload."""
    schema_keys = {field["key"] for field in field_schema}
    for key, state in field_states.items():
        if key not in schema_keys:
            raise LoreWriteError(
                detail={
                    "code": "LORE_FIELD_STATE_INVALID",
                    "message": "字段状态校验失败",
                    "field_errors": [
                        {"field": key, "message": "状态字段不属于当前设定类型"}
                    ],
                },
                status_code=422,
            )
        if state not in ("provided", "unknown", "needs_confirmation"):
            raise LoreWriteError(
                detail={
                    "code": "LORE_FIELD_STATE_INVALID",
                    "message": "字段状态校验失败",
                    "field_errors": [
                        {"field": key, "message": f"状态无效: {state}"}
                    ],
                },
                status_code=422,
            )
        if state == "provided":
            value = payload.get(key)
            if value is None or value == "":
                raise LoreWriteError(
                    detail={
                        "code": "LORE_FIELD_STATE_INVALID",
                        "message": "字段状态校验失败",
                        "field_errors": [
                            {
                                "field": key,
                                "message": "标记为 provided 时字段值不能为空",
                            }
                        ],
                    },
                    status_code=422,
                )
        elif state == "unknown":
            value = payload.get(key)
            if value is not None and value != "":
                raise LoreWriteError(
                    detail={
                        "code": "LORE_FIELD_STATE_INVALID",
                        "message": "字段状态校验失败",
                        "field_errors": [
                            {
                                "field": key,
                                "message": "标记为 unknown 时字段值必须为空",
                            }
                        ],
                    },
                    status_code=422,
                )


def _derive_field_states(
    payload: dict[str, Any],
    field_schema: list[dict[str, Any]],
    user_states: dict[str, str] | None = None,
) -> dict[str, str]:
    """Derive field_states from payload presence and user-provided overrides.

    Fields present in payload with non-null values default to "provided".
    Fields absent or null default to "unknown".
    User-supplied overrides in user_states take precedence.
    """
    user_states = user_states or {}
    schema_keys = {field["key"] for field in field_schema}
    result: dict[str, str] = {}
    for key in schema_keys:
        if key in user_states:
            result[key] = user_states[key]
        elif key in payload and payload[key] is not None and payload[key] != "":
            result[key] = "provided"
        else:
            result[key] = "unknown"
    return result


async def _resolve_type(
    db: AsyncSession,
    project_id: str,
    type_key: str,
) -> SettingType:
    """Resolve or initialise a SettingType for the given project and key.

    Only registered built-in type keys may be auto-created with
    is_builtin=True.  Custom type keys must already exist in the project
    and be active; otherwise a 422 is raised.
    """
    result = await db.execute(
        select(SettingType).where(
            SettingType.project_id == project_id,
            SettingType.key == type_key,
        )
    )
    setting_type = result.scalar_one_or_none()
    if setting_type:
        if setting_type.status != "active":
            raise LoreWriteError(
                detail=f"类型 {type_key} 已被停用",
                status_code=422,
            )
        return setting_type

    if type_key not in BUILTIN_TYPE_KEYS:
        raise LoreWriteError(
            detail=f"未知类型: {type_key}",
            status_code=422,
        )

    display_name = TYPE_DISPLAY_NAMES[type_key]
    field_schema = [
        {
            "key": f["key"],
            "label": f["label"],
            "control": f["control"],
            "value_type": f.get("value_type", "string"),
            "help": f["help"],
            "order": f["order"],
            "required": f.get("required", False),
        }
        for f in TYPE_FIELD_SCHEMAS.get(type_key, [])
    ]
    new_type = SettingType(
        project_id=project_id,
        key=type_key,
        display_name=display_name,
        description=f"内置{display_name}类型",
        is_builtin=True,
        schema_revision=1,
        field_schema=field_schema,
        status="active",
    )
    db.add(new_type)
    await db.flush()
    db.add(
        SettingTypeRevision(
            type_id=new_type.id,
            revision=1,
            display_name=new_type.display_name,
            field_schema=field_schema,
            change_summary="初始化内置类型",
        )
    )
    return new_type


async def create_custom_type(
    db: AsyncSession,
    project_id: str,
    key: str,
    display_name: str,
    description: str,
    field_schema: list[dict[str, Any]],
) -> SettingType:
    check_writes_available()
    if key in BUILTIN_TYPE_KEYS:
        raise LoreWriteError("内置类型键不能用于自定义类型", status_code=409)
    existing = await db.scalar(
        select(SettingType).where(
            SettingType.project_id == project_id,
            SettingType.key == key,
        )
    )
    if existing is not None:
        raise LoreWriteError(
            {
                "code": "LORE_TYPE_DUPLICATE",
                "message": "项目中已存在相同类型键",
                "type_id": existing.id,
            },
            status_code=409,
        )
    setting_type = SettingType(
        project_id=project_id,
        key=key,
        display_name=display_name,
        description=description,
        is_builtin=False,
        schema_revision=1,
        field_schema=field_schema,
        status="active",
    )
    db.add(setting_type)
    await db.flush()
    db.add(
        SettingTypeRevision(
            type_id=setting_type.id,
            revision=1,
            display_name=display_name,
            field_schema=field_schema,
            change_summary="创建自定义类型",
        )
    )
    return setting_type


async def create_element(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    type_key: str,
    name: str,
    summary: str,
    payload: dict[str, Any],
    field_states: dict[str, str] | None,
    sources_input: list[dict[str, Any]],
) -> SettingElement:
    check_writes_available()

    setting_type = await _resolve_type(db, project_id, type_key)
    field_schema = field_schema_for_type(setting_type)
    _validate_payload_keys(payload, field_schema)
    _validate_payload_values(payload, field_schema)
    if field_states:
        _validate_field_states(field_states, payload, field_schema)

    type_id = setting_type.id
    normalized_name = name.strip().casefold()

    derived_states = _derive_field_states(payload, field_schema, field_states)
    element = SettingElement(
        project_id=project_id,
        type_id=type_id,
        name=name,
        normalized_name=normalized_name,
        summary=summary,
        payload=payload,
        payload_schema_revision=setting_type.schema_revision,
        field_states=derived_states,
        confirmation_status="confirmed",
        lifecycle_status="active",
        enabled=True,
        content_version=1,
        lock_version=1,
    )
    db.add(element)
    await db.flush()

    version = ElementVersion(
        element_id=element.id,
        version_no=1,
        type_id=type_id,
        type_schema_revision=setting_type.schema_revision,
        name=name,
        summary=summary,
        payload=payload,
        field_states=derived_states,
        change_reason="创建",
        created_by=user_id,
    )
    db.add(version)

    primary_source: ElementSource | None = None
    for source in (sources_input or []):
        excerpt = source.get("excerpt")
        confirmation = source.get("confirmation_status", "provided")
        if confirmation not in ("provided", "needs_confirmation"):
            confirmation = "provided"
        element_source = ElementSource(
            project_id=project_id,
            element_id=element.id,
            source_kind=source.get("kind", "manual"),
            source_ref=source.get("reference"),
            locator=source.get("locator", {}) or {},
            excerpt=excerpt,
            excerpt_hash=_excerpt_hash(excerpt),
            confirmation_status=confirmation,
            is_primary=source.get("is_primary", False),
        )
        db.add(element_source)
        if element_source.is_primary:
            primary_source = element_source

    if primary_source is not None:
        await db.flush()
        version.source_id = primary_source.id

    db.add(
        ElementStateEvent(
            element_id=element.id,
            event_kind="create",
            previous_lock_version=0,
            new_lock_version=1,
            performed_by=user_id,
        )
    )

    return element


async def update_element_content(
    db: AsyncSession,
    element: SettingElement,
    user_id: str,
    expected_version: int,
    name: str,
    summary: str,
    payload: dict[str, Any],
    field_states: dict[str, str] | None,
) -> SettingElement:
    check_writes_available()

    await _claim_lock_version(db, SettingElement, element, expected_version)

    setting_type = await db.scalar(
        select(SettingType).where(
            SettingType.id == element.type_id,
            SettingType.project_id == element.project_id,
        )
    )
    if setting_type is None or setting_type.status != "active":
        raise LoreWriteError("设定类型不存在或已停用", status_code=409)
    field_schema = field_schema_for_type(setting_type)

    _validate_payload_keys(payload, field_schema)
    _validate_payload_values(payload, field_schema)
    if field_states:
        _validate_field_states(field_states, payload, field_schema)

    derived_states = _derive_field_states(payload, field_schema, field_states)

    new_content_version = element.content_version + 1
    element.name = name
    element.normalized_name = name.strip().casefold()
    element.summary = summary
    element.payload = payload
    element.field_states = derived_states
    element.payload_schema_revision = setting_type.schema_revision
    element.content_version = new_content_version

    version = ElementVersion(
        element_id=element.id,
        version_no=new_content_version,
        type_id=element.type_id,
        type_schema_revision=setting_type.schema_revision,
        name=name,
        summary=summary,
        payload=payload,
        field_states=derived_states,
        change_reason="编辑",
        created_by=user_id,
    )
    db.add(version)

    return element


async def change_element_state(
    db: AsyncSession,
    element: SettingElement,
    user_id: str,
    expected_version: int,
    event_kind: str,
    reason: str,
) -> SettingElement:
    check_writes_available()

    previous_lock = expected_version
    new_lock = await _claim_lock_version(
        db, SettingElement, element, expected_version
    )

    if event_kind == "confirm":
        pending_fields = sorted(
            key
            for key, state in (element.field_states or {}).items()
            if state == "needs_confirmation"
        )
        if pending_fields:
            raise LoreWriteError(
                {
                    "code": "LORE_FIELDS_NEED_CONFIRMATION",
                    "message": "仍有字段需要确认，暂不能确认整个设定",
                    "fields": pending_fields,
                },
                status_code=422,
            )
        element.confirmation_status = "confirmed"
    elif event_kind == "reject":
        element.confirmation_status = "rejected"
    elif event_kind == "enable":
        element.enabled = True
    elif event_kind == "disable":
        element.enabled = False
    elif event_kind == "archive":
        element.lifecycle_status = "archived"
    elif event_kind == "restore_archive":
        element.lifecycle_status = "active"
    else:
        raise LoreWriteError(f"未知事件类型: {event_kind}")

    db.add(
        ElementStateEvent(
            element_id=element.id,
            event_kind=event_kind,
            previous_lock_version=previous_lock,
            new_lock_version=new_lock,
            performed_by=user_id,
            metadata_={"reason": reason} if reason else {},
        )
    )

    return element


async def restore_element_version_content(
    db: AsyncSession,
    element: SettingElement,
    user_id: str,
    target_version: ElementVersion,
    expected_version: int,
    reason: str,
) -> SettingElement:
    """Restore content from a historical version snapshot.

    Only content fields are restored: type_id, name, summary, payload,
    field_states, payload_schema_revision. Status and lifecycle fields are
    left untouched, and relationships are not restored.
    """
    check_writes_available()

    target_type = await db.scalar(
        select(SettingType).where(
            SettingType.id == target_version.type_id,
            SettingType.project_id == element.project_id,
        )
    )
    if target_type is None:
        raise LoreWriteError(
            {
                "code": "LORE_VERSION_TYPE_INVALID",
                "message": "历史版本引用的设定类型不属于当前项目",
            },
            status_code=409,
        )

    await _claim_lock_version(db, SettingElement, element, expected_version)

    new_content_version = element.content_version + 1

    element.type_id = target_version.type_id
    element.payload_schema_revision = target_version.type_schema_revision
    element.name = target_version.name
    element.normalized_name = target_version.name.strip().casefold()
    element.summary = target_version.summary
    element.payload = target_version.payload
    element.field_states = dict(target_version.field_states or {})
    element.content_version = new_content_version

    new_version = ElementVersion(
        element_id=element.id,
        version_no=new_content_version,
        type_id=target_version.type_id,
        type_schema_revision=target_version.type_schema_revision,
        name=target_version.name,
        summary=target_version.summary,
        payload=target_version.payload,
        field_states=dict(target_version.field_states or {}),
        change_reason=f"恢复版本 {target_version.version_no}",
        created_by=user_id,
    )
    db.add(new_version)

    return element


def _add_relation_snapshot(
    db: AsyncSession,
    relation: ElementRelation,
    user_id: str,
    change_reason: str,
) -> None:
    db.add(
        ElementRelationVersion(
            relation_id=relation.id,
            version_no=relation.version_no,
            source_element_id=relation.source_element_id,
            target_element_id=relation.target_element_id,
            relation_key=relation.relation_key,
            forward_label=relation.forward_label,
            reverse_label=relation.reverse_label,
            description=relation.description or "",
            metadata_=dict(relation.metadata_ or {}),
            status=relation.status,
            change_reason=change_reason,
            created_by=user_id,
        )
    )


async def create_relation(
    db: AsyncSession,
    project_id: str,
    source: SettingElement,
    target: SettingElement,
    user_id: str,
    relation_key: str,
    forward_label: str,
    reverse_label: str,
    description: str,
    metadata: dict[str, Any],
) -> ElementRelation:
    """Create a relation or restore an archived relation with the same identity."""
    check_writes_available()

    if source.id == target.id:
        raise LoreWriteError(
            {
                "code": "LORE_RELATION_SELF_LOOP",
                "message": "默认不允许设定与自身建立关系",
            },
            status_code=422,
        )
    if source.lifecycle_status != "active" or target.lifecycle_status != "active":
        raise LoreWriteError(
            {
                "code": "LORE_RELATION_ENDPOINT_ARCHIVED",
                "message": "归档或合并设定不能用于新关系",
            },
            status_code=409,
        )

    result = await db.execute(
        select(ElementRelation)
        .where(
            ElementRelation.project_id == project_id,
            ElementRelation.source_element_id == source.id,
            ElementRelation.target_element_id == target.id,
            ElementRelation.relation_key == relation_key,
        )
        .with_for_update()
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise LoreWriteError(
            {
                "code": "LORE_RELATION_DUPLICATE",
                "message": "相同关系已经存在，请打开原关系处理",
                "relation_id": existing.id,
                "relation_status": existing.status,
                "current_lock_version": existing.lock_version,
            },
            status_code=409,
        )

    relation = ElementRelation(
        project_id=project_id,
        source_element_id=source.id,
        target_element_id=target.id,
        relation_key=relation_key,
        forward_label=forward_label,
        reverse_label=reverse_label,
        description=description,
        metadata_=dict(metadata or {}),
        status="active",
        version_no=1,
        lock_version=1,
    )
    db.add(relation)
    await db.flush()
    _add_relation_snapshot(db, relation, user_id, "创建关系")
    return relation


async def update_relation(
    db: AsyncSession,
    relation: ElementRelation,
    user_id: str,
    expected_version: int,
    forward_label: str,
    reverse_label: str,
    description: str,
    metadata: dict[str, Any],
) -> ElementRelation:
    check_writes_available()
    if relation.status != "active":
        raise LoreWriteError("已归档关系需恢复后才能编辑", status_code=409)

    await _claim_lock_version(db, ElementRelation, relation, expected_version)

    relation.forward_label = forward_label
    relation.reverse_label = reverse_label
    relation.description = description
    relation.metadata_ = dict(metadata or {})
    relation.version_no += 1
    _add_relation_snapshot(db, relation, user_id, "编辑关系")
    return relation


async def change_relation_state(
    db: AsyncSession,
    relation: ElementRelation,
    user_id: str,
    expected_version: int,
    status: str,
    reason: str,
) -> ElementRelation:
    check_writes_available()
    if status not in ("active", "archived"):
        raise LoreWriteError("关系状态无效")
    if relation.lock_version != expected_version:
        raise LoreStaleVersionError(relation.lock_version, relation.updated_at)
    if relation.status == status:
        return relation
    if status == "active":
        result = await db.execute(
            select(SettingElement.id).where(
                SettingElement.project_id == relation.project_id,
                SettingElement.id.in_(
                    [relation.source_element_id, relation.target_element_id]
                ),
                SettingElement.lifecycle_status == "active",
            )
        )
        if len(set(result.scalars().all())) != 2:
            raise LoreWriteError(
                {
                    "code": "LORE_RELATION_ENDPOINT_ARCHIVED",
                    "message": "关系两端均恢复后才能恢复关系",
                },
                status_code=409,
            )

    await _claim_lock_version(db, ElementRelation, relation, expected_version)

    relation.status = status
    relation.version_no += 1
    action = "恢复关系" if status == "active" else "归档关系"
    _add_relation_snapshot(db, relation, user_id, reason or action)
    return relation
