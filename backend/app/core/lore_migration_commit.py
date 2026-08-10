"""Two-phase, exactly-once legacy Lore upgrade service.

The service is intentionally not a bulk migrator.  One owner-confirmed project
is materialized while legacy writes are globally frozen, verified from a fresh
session, and only then switched to relational reads and writes.  The legacy
Worldview row is retained as the immutable source record.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings as app_settings
from app.core.lore_migration import (
    TYPE_DISPLAY_NAMES,
    TYPE_FIELD_SCHEMAS,
    deterministic_type_id,
    legacy_structured_payload,
    normalize_lore_name,
    structured_payload_checksum,
)
from app.core.lore_migration_preview import (
    MAPPING_VERSION,
    PREVIEW_SCHEMA_VERSION,
    build_migration_preview,
    migration_preview_source_checksum,
)
from app.models.lore import (
    ElementRelation,
    ElementSource,
    ElementStateEvent,
    ElementVersion,
    LegacyElementMap,
    LegacyLoreResolution,
    ProjectLoreMigration,
    ProjectLoreMigrationOperation,
    SettingElement,
    SettingType,
    SettingTypeRevision,
)
from app.models.project import Project, Worldview
from app.schemas.lore import (
    LoreMigrationCommitInput,
    LoreMigrationOperationResponse,
)


MIGRATION_COMMIT_CONTRACT_VERSION = 1
_REQUEST_FINGERPRINT_VERSION = "lore-migration-commit:v1"


class LoreMigrationCommitError(RuntimeError):
    """Stable public failure for a migration operation."""

    def __init__(
        self,
        detail: dict[str, Any],
        *,
        status_code: int = 409,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(str(detail.get("code") or "LORE_MIGRATION_FAILED"))
        self.detail = detail
        self.status_code = status_code
        self.outcome_unknown = outcome_unknown


def _error(
    code: str,
    message: str,
    *,
    status_code: int = 409,
    retryable: bool = False,
    outcome_unknown: bool = False,
) -> LoreMigrationCommitError:
    return LoreMigrationCommitError(
        {
            "code": code,
            "message": message,
            "retryable": retryable,
            "outcome_unknown": outcome_unknown,
        },
        status_code=status_code,
        outcome_unknown=outcome_unknown,
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _stable_id(project_id: str, operation_key: str, kind: str) -> str:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"lore-migration:{project_id}:{operation_key}:{kind}",
    ).hex


def _chunks(values: list[str], size: int = 500) -> list[list[str]]:
    """Keep IN clauses below conservative SQLite/driver parameter limits."""
    return [values[index:index + size] for index in range(0, len(values), size)]


def migration_request_fingerprint(
    project_id: str,
    user_id: str,
    worldview_id: str,
    body: LoreMigrationCommitInput,
) -> str:
    payload = {
        "project_id": project_id,
        "requested_by": user_id,
        "source_worldview_id": worldview_id,
        "preview_schema_version": body.preview_schema_version,
        "mapping_version": body.mapping_version,
        "source_checksum": body.expected_source_checksum,
        "semantic_result_checksum": body.expected_semantic_result_checksum,
        "confirmed": body.confirm_legacy_retained_no_automatic_rollback,
    }
    return hashlib.sha256(
        f"{_REQUEST_FINGERPRINT_VERSION}\n{_canonical_json(payload)}".encode("utf-8")
    ).hexdigest()


def _field_schema(type_key: str) -> list[dict[str, Any]]:
    return [dict(field) for field in TYPE_FIELD_SCHEMAS[type_key]]


def _item_type_key(item: Mapping[str, Any]) -> str:
    return str(item.get("effective_proposed_type_key") or item.get("proposed_type_key"))


def _item_source_kind(item: Mapping[str, Any]) -> str:
    return str(item.get("effective_source_kind") or item.get("source_kind") or "manual")


def _item_locator(item: Mapping[str, Any], preview: Mapping[str, Any]) -> dict[str, Any]:
    author_confirmed_unlocated = any(
        state.get("reason_code") == "raw_text_excerpt_unverified"
        and state.get("applies") is True
        for state in item.get("resolution_states", [])
    )
    locator = {
        "legacy_category": item["legacy_category"],
        "legacy_index": item["legacy_index"],
        "source_checksum": preview["source_checksum"],
    }
    resolution_ids = list(item.get("applied_resolution_ids", []))
    if resolution_ids:
        locator["resolution_ids"] = resolution_ids
    if author_confirmed_unlocated:
        locator["exact_excerpt_available"] = False
        locator["author_confirmed_unlocated"] = True
    return locator


def _field_states(type_key: str, payload: Mapping[str, Any]) -> dict[str, str]:
    definitions = {field["key"]: field for field in TYPE_FIELD_SCHEMAS[type_key]}
    if set(payload) - set(definitions):
        raise _error(
            "LORE_MIGRATION_PREVIEW_CONTENT_INVALID",
            "预检包含无法安全写入的字段，升级未开始。",
            status_code=422,
        )
    for key, value in payload.items():
        value_type = definitions[key].get("value_type", "string")
        if (
            value not in (None, "")
            and value_type in {"string", "text", "reference"}
            and not isinstance(value, str)
        ):
            raise _error(
                "LORE_MIGRATION_PREVIEW_CONTENT_INVALID",
                "预检字段类型不符合设定结构，升级未开始。",
                status_code=422,
            )
    return {
        field["key"]: (
            "provided"
            if payload.get(field["key"]) not in (None, "", [], {})
            else "unknown"
        )
        for field in TYPE_FIELD_SCHEMAS[type_key]
    }


def _manifest(
    project_id: str,
    operation_key: str,
    preview: Mapping[str, Any],
) -> dict[str, Any]:
    type_keys = sorted({_item_type_key(item) for item in preview["items"]})
    items: list[dict[str, Any]] = []
    for item in preview["items"]:
        element_id = str(item["planned_element_id"])
        items.append({
            "element_id": element_id,
            "type_key": _item_type_key(item),
            "legacy_category": str(item["legacy_category"]),
            "legacy_index": int(item["legacy_index"]),
            "legacy_id": item.get("legacy_id"),
            "source_id": _stable_id(project_id, operation_key, f"source:{element_id}"),
            "version_id": _stable_id(project_id, operation_key, f"version:{element_id}"),
            "map_id": _stable_id(project_id, operation_key, f"map:{element_id}"),
            "event_id": _stable_id(project_id, operation_key, f"event:{element_id}"),
        })
    return {
        "type_keys": type_keys,
        "type_ids": [deterministic_type_id(project_id, key) for key in type_keys],
        "type_revision_ids": [
            _stable_id(project_id, operation_key, f"type-revision:{key}")
            for key in type_keys
        ],
        "items": items,
    }


async def _preview_for_operation(
    session: AsyncSession,
    project_id: str,
    worldview: Worldview,
    *,
    lock_resolutions: bool = False,
) -> dict[str, Any]:
    statement = (
        select(LegacyLoreResolution)
        .where(LegacyLoreResolution.project_id == project_id)
        .order_by(LegacyLoreResolution.created_at, LegacyLoreResolution.id)
    )
    if lock_resolutions:
        statement = statement.with_for_update()
    resolutions = list((await session.scalars(statement)).all())
    return build_migration_preview(
        project_id, "legacy", worldview, resolutions=resolutions
    )


def _validate_frozen_preview(
    project_id: str,
    body: LoreMigrationCommitInput,
    preview: Mapping[str, Any],
) -> None:
    if (
        body.preview_schema_version != PREVIEW_SCHEMA_VERSION
        or body.mapping_version != MAPPING_VERSION
    ):
        raise _error(
            "LORE_MIGRATION_PREVIEW_VERSION_MISMATCH",
            "预检版本已经更新，请重新检查旧资料。",
        )
    if preview.get("overall_status") != "ready" or not preview.get("items"):
        raise _error(
            "LORE_MIGRATION_PREVIEW_NOT_READY",
            "旧资料仍有待确认、冲突或阻塞项，当前不能升级。",
        )
    if (
        preview.get("project_id") != project_id
        or preview.get("source_checksum") != body.expected_source_checksum
        or preview.get("semantic_result_checksum")
        != body.expected_semantic_result_checksum
    ):
        raise _error(
            "LORE_MIGRATION_PREVIEW_STALE",
            "旧资料或预检结果已经变化，请重新检查后再确认。",
            retryable=True,
        )


async def find_migration_operation(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    operation_key: str,
    lock: bool = False,
) -> ProjectLoreMigrationOperation | None:
    statement = select(ProjectLoreMigrationOperation).where(
        ProjectLoreMigrationOperation.project_id == project_id,
        ProjectLoreMigrationOperation.requested_by == user_id,
        ProjectLoreMigrationOperation.operation_key == operation_key,
    )
    if lock:
        statement = statement.with_for_update()
    return await db.scalar(statement)


def build_migration_operation_response(
    operation: ProjectLoreMigrationOperation,
    *,
    replayed: bool,
) -> LoreMigrationOperationResponse:
    return LoreMigrationOperationResponse(
        id=operation.id,
        project_id=operation.project_id,
        operation_key=operation.operation_key,
        status=operation.status,
        source_checksum=operation.source_checksum,
        preview_schema_version=operation.preview_schema_version,
        mapping_version=operation.mapping_version,
        semantic_result_checksum=operation.semantic_result_checksum,
        result_checksum=operation.result_checksum,
        migration_id=operation.migration_id,
        error_code=operation.error_code,
        counts=dict(operation.counts or {}),
        started_at=operation.created_at,
        updated_at=operation.updated_at,
        completed_at=operation.completed_at,
        replayed=replayed,
    )


async def _locked_project_worldview(
    session: AsyncSession,
    project_id: str,
    user_id: str,
) -> tuple[Project, Worldview]:
    project = await session.scalar(
        select(Project).where(Project.id == project_id).with_for_update()
    )
    if project is None:
        raise _error("LORE_MIGRATION_PROJECT_MISSING", "项目不存在。", status_code=404)
    if project.owner_id is None or project.owner_id != user_id:
        raise _error("LORE_MIGRATION_FORBIDDEN", "无权操作此项目。", status_code=403)
    worldview = await session.scalar(
        select(Worldview)
        .where(Worldview.project_id == project_id)
        .with_for_update()
    )
    if worldview is None:
        raise _error(
            "LORE_MIGRATION_WORLDVIEW_MISSING",
            "项目没有可升级的旧世界观资料。",
        )
    return project, worldview


async def _existing_business_state(
    session: AsyncSession,
    project_id: str,
) -> tuple[list[SettingElement], int, int, int]:
    elements = list((await session.scalars(
        select(SettingElement).where(SettingElement.project_id == project_id)
    )).all())
    maps = int(await session.scalar(
        select(func.count()).select_from(LegacyElementMap).where(
            LegacyElementMap.project_id == project_id
        )
    ) or 0)
    migrations = int(await session.scalar(
        select(func.count()).select_from(ProjectLoreMigration).where(
            ProjectLoreMigration.project_id == project_id
        )
    ) or 0)
    types = int(await session.scalar(
        select(func.count()).select_from(SettingType).where(
            SettingType.project_id == project_id
        )
    ) or 0)
    return elements, maps, migrations, types


async def _materialize(
    session: AsyncSession,
    project: Project,
    worldview: Worldview,
    operation: ProjectLoreMigrationOperation,
    preview: Mapping[str, Any],
    *,
    fault_at: str | None = None,
) -> None:
    manifest = _manifest(project.id, operation.operation_key, preview)
    type_by_key: dict[str, SettingType] = {}
    for index, type_key in enumerate(manifest["type_keys"]):
        type_id = deterministic_type_id(project.id, type_key)
        setting_type = SettingType(
            id=type_id,
            project_id=project.id,
            key=type_key,
            display_name=TYPE_DISPLAY_NAMES[type_key],
            description=f"内置{TYPE_DISPLAY_NAMES[type_key]}类型",
            is_builtin=True,
            schema_revision=1,
            field_schema=_field_schema(type_key),
            status="active",
        )
        session.add(setting_type)
        type_by_key[type_key] = setting_type
        session.add(SettingTypeRevision(
            id=manifest["type_revision_ids"][index],
            type_id=type_id,
            revision=1,
            display_name=TYPE_DISPLAY_NAMES[type_key],
            field_schema=_field_schema(type_key),
            change_summary="旧资料安全升级初始化",
        ))
    await session.flush()
    if fault_at == "after_types":
        raise _error("LORE_MIGRATION_FAULT_INJECTED", "升级测试故障。", status_code=500)

    manifest_by_element = {row["element_id"]: row for row in manifest["items"]}
    prepared: list[dict[str, Any]] = []
    for item in preview["items"]:
        ids = manifest_by_element[item["planned_element_id"]]
        type_key = _item_type_key(item)
        payload = dict(item.get("effective_mapped_fields", item["mapped_fields"]))
        states = _field_states(type_key, payload)
        session.add(SettingElement(
            id=item["planned_element_id"],
            project_id=project.id,
            type_id=type_by_key[type_key].id,
            name=item["name"],
            normalized_name=normalize_lore_name(item["name"]),
            summary="",
            payload=payload,
            payload_schema_revision=1,
            field_states=states,
            confirmation_status="confirmed",
            lifecycle_status="active",
            enabled=True,
            content_version=1,
            lock_version=1,
        ))
        prepared.append({
            "item": item,
            "ids": ids,
            "type_id": type_by_key[type_key].id,
            "payload": payload,
            "states": states,
            "excerpt": _canonical_json(item["original_value"]),
        })
    await session.flush()
    if fault_at == "after_elements":
        raise _error("LORE_MIGRATION_FAULT_INJECTED", "升级测试故障。", status_code=500)

    for row in prepared:
        item = row["item"]
        session.add(ElementSource(
            id=row["ids"]["source_id"],
            project_id=project.id,
            element_id=item["planned_element_id"],
            source_kind=_item_source_kind(item),
            source_ref=f"worldviews:{worldview.id}",
            locator=_item_locator(item, preview),
            excerpt=row["excerpt"],
            excerpt_hash=hashlib.sha256(row["excerpt"].encode()).hexdigest(),
            confirmation_status="provided",
            is_primary=True,
        ))
    await session.flush()

    for row in prepared:
        item = row["item"]
        session.add(ElementVersion(
            id=row["ids"]["version_id"],
            element_id=item["planned_element_id"],
            version_no=1,
            type_id=row["type_id"],
            type_schema_revision=1,
            name=item["name"],
            summary="",
            payload=row["payload"],
            field_states=row["states"],
            change_reason="旧资料安全升级",
            source_id=row["ids"]["source_id"],
            created_by=project.owner_id,
        ))
    await session.flush()

    for row in prepared:
        item = row["item"]
        session.add(LegacyElementMap(
            id=row["ids"]["map_id"],
            project_id=project.id,
            legacy_category=item["legacy_category"],
            legacy_index=item["legacy_index"],
            legacy_id=item.get("legacy_id"),
            element_id=item["planned_element_id"],
            source_checksum=preview["source_checksum"],
        ))
        session.add(ElementStateEvent(
            id=row["ids"]["event_id"],
            element_id=item["planned_element_id"],
            event_kind="create",
            previous_lock_version=0,
            new_lock_version=1,
            performed_by=project.owner_id,
            metadata_={
                "origin": "legacy_lore_migration",
                "operation_id": operation.id,
            },
        ))
    await session.flush()
    if fault_at == "after_materialization":
        raise _error("LORE_MIGRATION_FAULT_INJECTED", "升级测试故障。", status_code=500)


async def _authoritative_snapshot(
    session: AsyncSession,
    project: Project,
    worldview: Worldview,
    operation: ProjectLoreMigrationOperation,
    preview: Mapping[str, Any],
) -> dict[str, Any]:
    manifest = _manifest(project.id, operation.operation_key, preview)
    types = list((await session.scalars(
        select(SettingType)
        .where(SettingType.project_id == project.id)
        .order_by(SettingType.key)
    )).all())
    revisions = list((await session.scalars(
        select(SettingTypeRevision)
        .join(SettingType, SettingType.id == SettingTypeRevision.type_id)
        .where(SettingType.project_id == project.id)
        .order_by(SettingTypeRevision.id)
    )).all())
    elements = list((await session.scalars(
        select(SettingElement)
        .where(SettingElement.project_id == project.id)
        .order_by(SettingElement.id)
    )).all())
    sources = list((await session.scalars(
        select(ElementSource)
        .where(ElementSource.project_id == project.id)
        .order_by(ElementSource.element_id)
    )).all())
    versions = list((await session.scalars(
        select(ElementVersion)
        .join(SettingElement, SettingElement.id == ElementVersion.element_id)
        .where(SettingElement.project_id == project.id)
        .order_by(ElementVersion.element_id)
    )).all())
    maps = list((await session.scalars(
        select(LegacyElementMap)
        .where(LegacyElementMap.project_id == project.id)
        .order_by(LegacyElementMap.legacy_category, LegacyElementMap.legacy_index)
    )).all())
    events = list((await session.scalars(
        select(ElementStateEvent)
        .join(SettingElement, SettingElement.id == ElementStateEvent.element_id)
        .where(SettingElement.project_id == project.id)
        .order_by(ElementStateEvent.element_id)
    )).all())
    relation_count = int(await session.scalar(
        select(func.count()).select_from(ElementRelation).where(
            ElementRelation.project_id == project.id
        )
    ) or 0)

    expected_count = len(preview["items"])
    expected_type_count = len(manifest["type_keys"])
    if (
        len(types) != expected_type_count
        or len(revisions) != expected_type_count
        or any(len(rows) != expected_count for rows in (elements, sources, versions, maps, events))
        or relation_count != 0
    ):
        raise _error(
            "LORE_MIGRATION_AUTHORITATIVE_COUNT_MISMATCH",
            "升级结果数量校验失败，项目尚未切换。",
            status_code=500,
            outcome_unknown=True,
        )

    type_by_key = {row.key: row for row in types}
    revision_by_id = {row.id: row for row in revisions}
    element_by_id = {row.id: row for row in elements}
    source_by_element = {row.element_id: row for row in sources}
    version_by_element = {row.element_id: row for row in versions}
    map_by_position = {(row.legacy_category, row.legacy_index): row for row in maps}
    event_by_element = {row.element_id: row for row in events}
    manifest_by_element = {row["element_id"]: row for row in manifest["items"]}
    revision_id_by_key = dict(zip(
        manifest["type_keys"], manifest["type_revision_ids"], strict=True
    ))
    reconstructed = {key: [] for key in legacy_structured_payload(None)}

    for item in preview["items"]:
        element_id = item["planned_element_id"]
        type_key = _item_type_key(item)
        setting_type = type_by_key.get(type_key)
        ids = manifest_by_element[element_id]
        revision = revision_by_id.get(revision_id_by_key[type_key])
        element = element_by_id.get(element_id)
        source = source_by_element.get(element_id)
        version = version_by_element.get(element_id)
        mapping = map_by_position.get((item["legacy_category"], item["legacy_index"]))
        event = event_by_element.get(element_id)
        payload = dict(item.get("effective_mapped_fields", item["mapped_fields"]))
        states = _field_states(type_key, payload)
        excerpt = _canonical_json(item["original_value"])
        if (
            setting_type is None
            or setting_type.id != deterministic_type_id(project.id, type_key)
            or setting_type.display_name != TYPE_DISPLAY_NAMES[type_key]
            or setting_type.field_schema != _field_schema(type_key)
            or setting_type.is_builtin is not True
            or setting_type.status != "active"
            or revision is None
            or revision.type_id != setting_type.id
            or revision.revision != 1
            or revision.field_schema != _field_schema(type_key)
            or element is None
            or element.type_id != setting_type.id
            or element.name != item["name"]
            or element.normalized_name != normalize_lore_name(item["name"])
            or element.payload != payload
            or element.field_states != states
            or element.confirmation_status != "confirmed"
            or element.lifecycle_status != "active"
            or element.enabled is not True
            or (element.content_version, element.lock_version) != (1, 1)
            or source is None
            or source.id != ids["source_id"]
            or source.source_ref != f"worldviews:{worldview.id}"
            or source.source_kind != _item_source_kind(item)
            or source.excerpt != excerpt
            or source.excerpt_hash != hashlib.sha256(excerpt.encode()).hexdigest()
            or source.confirmation_status != "provided"
            or source.is_primary is not True
            or source.locator != _item_locator(item, preview)
            or version is None
            or version.id != ids["version_id"]
            or version.version_no != 1
            or version.type_id != setting_type.id
            or version.name != item["name"]
            or version.payload != payload
            or version.field_states != states
            or version.source_id != source.id
            or version.change_reason != "旧资料安全升级"
            or version.created_by != project.owner_id
            or mapping is None
            or mapping.id != ids["map_id"]
            or mapping.element_id != element_id
            or mapping.legacy_id != item.get("legacy_id")
            or mapping.source_checksum != preview["source_checksum"]
            or event is None
            or event.id != ids["event_id"]
            or event.event_kind != "create"
            or event.previous_lock_version != 0
            or event.new_lock_version != 1
            or event.performed_by != project.owner_id
            or event.metadata_ != {
                "origin": "legacy_lore_migration",
                "operation_id": operation.id,
            }
        ):
            raise _error(
                "LORE_MIGRATION_AUTHORITATIVE_CONTENT_MISMATCH",
                "升级结果内容校验失败，项目尚未切换。",
                status_code=500,
                outcome_unknown=True,
            )
        reconstructed[item["legacy_category"]].append(json.loads(excerpt))

    if structured_payload_checksum(reconstructed) != structured_payload_checksum(
        legacy_structured_payload(worldview)
    ):
        raise _error(
            "LORE_MIGRATION_LEGACY_ROUNDTRIP_MISMATCH",
            "旧资料往返校验失败，项目尚未切换。",
            status_code=500,
            outcome_unknown=True,
        )

    snapshot = {
        "types": [{
            "id": row.id,
            "key": row.key,
            "field_schema": row.field_schema,
        } for row in types],
        "elements": [{
            "id": row.id,
            "type_id": row.type_id,
            "name": row.name,
            "payload": row.payload,
            "field_states": row.field_states,
        } for row in elements],
        "sources": [{
            "id": row.id,
            "element_id": row.element_id,
            "source_ref": row.source_ref,
            "locator": row.locator,
            "excerpt_hash": row.excerpt_hash,
        } for row in sources],
        "maps": [{
            "id": row.id,
            "element_id": row.element_id,
            "legacy_category": row.legacy_category,
            "legacy_index": row.legacy_index,
        } for row in maps],
    }
    snapshot["checksum"] = _sha256_json(snapshot)
    return snapshot


async def _compensate_exact_materialization(
    session_factory: async_sessionmaker[AsyncSession],
    project_id: str,
    user_id: str,
    operation_key: str,
    error_code: str,
) -> ProjectLoreMigrationOperation | None:
    """Remove only an intact, not-yet-exposed materialization; retain its receipt."""
    async with session_factory() as session:
        try:
            project, worldview = await _locked_project_worldview(
                session, project_id, user_id
            )
            operation = await find_migration_operation(
                session,
                project_id=project_id,
                user_id=user_id,
                operation_key=operation_key,
                lock=True,
            )
            if (
                operation is None
                or operation.status != "validating"
                or project.lore_storage_mode != "migrating"
                or operation.source_worldview_id != worldview.id
                or migration_preview_source_checksum(worldview)
                != operation.source_checksum
            ):
                return None
            preview = await _preview_for_operation(
                session, project_id, worldview, lock_resolutions=True
            )
            body = LoreMigrationCommitInput(
                operation_key=operation.operation_key,
                preview_schema_version=operation.preview_schema_version,
                mapping_version=operation.mapping_version,
                expected_source_checksum=operation.source_checksum,
                expected_semantic_result_checksum=operation.semantic_result_checksum,
                confirm_legacy_retained_no_automatic_rollback=True,
            )
            _validate_frozen_preview(project_id, body, preview)
            snapshot = await _authoritative_snapshot(
                session, project, worldview, operation, preview
            )
            if operation.result_checksum and snapshot["checksum"] != operation.result_checksum:
                return None
            manifest = _manifest(project_id, operation.operation_key, preview)
            element_ids = [row["element_id"] for row in manifest["items"]]
            for element_id_chunk in _chunks(element_ids):
                await session.execute(delete(ElementStateEvent).where(
                    ElementStateEvent.element_id.in_(element_id_chunk)
                ))
                await session.execute(delete(ElementVersion).where(
                    ElementVersion.element_id.in_(element_id_chunk)
                ))
                await session.execute(delete(ElementSource).where(
                    ElementSource.element_id.in_(element_id_chunk)
                ))
            await session.execute(delete(LegacyElementMap).where(
                LegacyElementMap.project_id == project_id
            ))
            for element_id_chunk in _chunks(element_ids):
                await session.execute(delete(SettingElement).where(
                    SettingElement.id.in_(element_id_chunk)
                ))
            await session.execute(delete(SettingTypeRevision).where(
                SettingTypeRevision.id.in_(manifest["type_revision_ids"])
            ))
            await session.execute(delete(SettingType).where(
                SettingType.id.in_(manifest["type_ids"])
            ))
            project.lore_storage_mode = "legacy"
            project.lore_migration_version = None
            operation.status = "failed"
            operation.error_code = error_code
            operation.completed_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()
            return operation
        except Exception:
            if session.in_transaction():
                await session.rollback()
            return None


async def _prepare_or_resume(
    session_factory: async_sessionmaker[AsyncSession],
    project_id: str,
    user_id: str,
    body: LoreMigrationCommitInput,
    *,
    fault_at: str | None,
) -> tuple[str, bool, LoreMigrationOperationResponse | None]:
    async with session_factory() as session:
        try:
            project, worldview = await _locked_project_worldview(
                session, project_id, user_id
            )
            existing = await find_migration_operation(
                session,
                project_id=project_id,
                user_id=user_id,
                operation_key=body.operation_key,
                lock=True,
            )
            if existing is not None:
                fingerprint = migration_request_fingerprint(
                    project_id, user_id, existing.source_worldview_id, body
                )
                if existing.request_fingerprint != fingerprint:
                    raise _error(
                        "LORE_MIGRATION_OPERATION_KEY_CONFLICT",
                        "此操作标识已用于不同的升级请求，请停止并核对。",
                    )
                terminal_response = (
                    build_migration_operation_response(existing, replayed=True)
                    if existing.status in {"ready", "failed"}
                    else None
                )
                return existing.status, True, terminal_response

            if not app_settings.LEGACY_JSON_WRITES_FROZEN:
                raise _error(
                    "LORE_MIGRATION_REQUIRES_WRITE_FREEZE",
                    "项目写入尚未进入安全升级窗口，升级未开始。",
                    status_code=503,
                    retryable=True,
                )
            if project.lore_storage_mode != "legacy" or project.lore_migration_version is not None:
                raise _error(
                    "LORE_MIGRATION_PROJECT_NOT_LEGACY",
                    "项目当前状态不能启动旧资料升级。",
                )
            active = await session.scalar(select(ProjectLoreMigrationOperation).where(
                ProjectLoreMigrationOperation.project_id == project_id,
                ProjectLoreMigrationOperation.status == "validating",
            ))
            if active is not None:
                raise _error(
                    "LORE_MIGRATION_ANOTHER_OPERATION_ACTIVE",
                    "此项目已有升级操作正在核对，请先查询原操作结果。",
                )
            elements, map_count, migration_count, type_count = (
                await _existing_business_state(session, project_id)
            )
            resolutions = list((await session.scalars(
                select(LegacyLoreResolution)
                .where(LegacyLoreResolution.project_id == project_id)
                .order_by(LegacyLoreResolution.created_at, LegacyLoreResolution.id)
                .with_for_update()
            )).all())
            preview = build_migration_preview(
                project_id,
                "legacy",
                worldview,
                existing_elements=elements,
                existing_legacy_map_count=map_count,
                existing_migration_count=migration_count,
                resolutions=resolutions,
            )
            if type_count:
                raise _error(
                    "LORE_MIGRATION_EXISTING_RELATIONAL_STATE",
                    "检测到既有正式设定结构，升级未开始。",
                )
            _validate_frozen_preview(project_id, body, preview)
            operation = ProjectLoreMigrationOperation(
                id=_stable_id(project_id, body.operation_key, "operation"),
                project_id=project_id,
                requested_by=user_id,
                operation_key=body.operation_key,
                request_fingerprint=migration_request_fingerprint(
                    project_id, user_id, worldview.id, body
                ),
                status="validating",
                source_worldview_id=worldview.id,
                source_checksum=body.expected_source_checksum,
                preview_schema_version=body.preview_schema_version,
                mapping_version=body.mapping_version,
                semantic_result_checksum=body.expected_semantic_result_checksum,
                counts={
                    "contract_version": MIGRATION_COMMIT_CONTRACT_VERSION,
                    "legacy_total": len(preview["items"]),
                    "types": len({_item_type_key(item) for item in preview["items"]}),
                    "elements": len(preview["items"]),
                    "sources": len(preview["items"]),
                    "legacy_rows_deleted": 0,
                },
            )
            session.add(operation)
            await session.flush()
            await _materialize(
                session,
                project,
                worldview,
                operation,
                preview,
                fault_at=fault_at,
            )
            snapshot = await _authoritative_snapshot(
                session, project, worldview, operation, preview
            )
            operation.result_checksum = snapshot["checksum"]
            project.lore_storage_mode = "migrating"
            project.lore_migration_version = body.mapping_version
            if not app_settings.LEGACY_JSON_WRITES_FROZEN:
                raise _error(
                    "LORE_MIGRATION_WRITE_FREEZE_LOST",
                    "安全升级窗口已结束，升级未提交。",
                    status_code=503,
                    retryable=True,
                )
            if fault_at == "before_materialization_commit":
                raise _error("LORE_MIGRATION_FAULT_INJECTED", "升级测试故障。", status_code=500)
            try:
                await session.commit()
            except Exception as exc:
                raise _error(
                    "LORE_MIGRATION_OUTCOME_UNKNOWN",
                    "升级提交结果暂时无法确认，请使用原操作标识查询。",
                    status_code=503,
                    retryable=True,
                    outcome_unknown=True,
                ) from exc
            return "validating", False, None
        except IntegrityError as exc:
            await session.rollback()
            raise _error(
                "LORE_MIGRATION_CONCURRENT_CONFLICT",
                "并发升级请求发生冲突，请按原操作标识查询结果。",
                retryable=True,
            ) from exc
        except Exception:
            if session.in_transaction():
                await session.rollback()
            raise


async def _verify_and_finalize(
    session_factory: async_sessionmaker[AsyncSession],
    project_id: str,
    user_id: str,
    body: LoreMigrationCommitInput,
    *,
    fault_at: str | None,
) -> ProjectLoreMigrationOperation:
    if not app_settings.LEGACY_JSON_WRITES_FROZEN:
        raise _error(
            "LORE_MIGRATION_REQUIRES_WRITE_FREEZE",
            "安全升级窗口当前不可用；已保存的操作可稍后按原标识继续核对。",
            status_code=503,
            retryable=True,
            outcome_unknown=True,
        )

    async with session_factory() as verify_session:
        project = await verify_session.scalar(select(Project).where(Project.id == project_id))
        worldview = await verify_session.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        operation = await find_migration_operation(
            verify_session,
            project_id=project_id,
            user_id=user_id,
            operation_key=body.operation_key,
        )
        if project is None or worldview is None or operation is None:
            raise _error(
                "LORE_MIGRATION_RECEIPT_MISSING",
                "无法核对升级收据，请停止重试并联系维护人员。",
                status_code=500,
                outcome_unknown=True,
            )
        if operation.status != "validating":
            return operation
        if (
            project.lore_storage_mode != "migrating"
            or operation.source_worldview_id != worldview.id
            or migration_preview_source_checksum(worldview) != operation.source_checksum
        ):
            raise _error(
                "LORE_MIGRATION_INTERMEDIATE_STATE_MISMATCH",
                "升级中间状态无法安全确认，请停止重试并联系维护人员。",
                status_code=500,
                outcome_unknown=True,
            )
        preview = await _preview_for_operation(
            verify_session, project_id, worldview
        )
        _validate_frozen_preview(project_id, body, preview)
        snapshot = await _authoritative_snapshot(
            verify_session, project, worldview, operation, preview
        )
        if snapshot["checksum"] != operation.result_checksum:
            raise _error(
                "LORE_MIGRATION_RESULT_CHECKSUM_MISMATCH",
                "升级结果校验失败，项目尚未切换。",
                status_code=500,
                outcome_unknown=True,
            )
        if fault_at == "after_materialization_commit_unknown":
            raise _error(
                "LORE_MIGRATION_OUTCOME_UNKNOWN",
                "升级结果暂时无法确认，请使用原操作标识查询。",
                status_code=503,
                retryable=True,
                outcome_unknown=True,
            )
        if fault_at == "during_fresh_validation":
            raise _error("LORE_MIGRATION_VALIDATION_FAILED", "升级校验失败。", status_code=500)

    async with session_factory() as final_session:
        try:
            project, worldview = await _locked_project_worldview(
                final_session, project_id, user_id
            )
            operation = await find_migration_operation(
                final_session,
                project_id=project_id,
                user_id=user_id,
                operation_key=body.operation_key,
                lock=True,
            )
            if operation is None:
                raise _error(
                    "LORE_MIGRATION_RECEIPT_MISSING",
                    "升级收据不存在。",
                    status_code=500,
                    outcome_unknown=True,
                )
            if operation.status != "validating":
                return operation
            if not app_settings.LEGACY_JSON_WRITES_FROZEN:
                raise _error(
                    "LORE_MIGRATION_WRITE_FREEZE_LOST",
                    "安全升级窗口已结束，项目尚未切换。",
                    status_code=503,
                    retryable=True,
                    outcome_unknown=True,
                )
            if (
                project.lore_storage_mode != "migrating"
                or operation.source_worldview_id != worldview.id
                or migration_preview_source_checksum(worldview)
                != operation.source_checksum
            ):
                raise _error(
                    "LORE_MIGRATION_FINAL_STATE_MISMATCH",
                    "最终切换条件已经变化，项目尚未切换。",
                    status_code=500,
                    outcome_unknown=True,
                )
            preview = await _preview_for_operation(
                final_session, project_id, worldview, lock_resolutions=True
            )
            _validate_frozen_preview(project_id, body, preview)
            snapshot = await _authoritative_snapshot(
                final_session, project, worldview, operation, preview
            )
            if snapshot["checksum"] != operation.result_checksum:
                raise _error(
                    "LORE_MIGRATION_RESULT_CHECKSUM_MISMATCH",
                    "最终结果校验失败，项目尚未切换。",
                    status_code=500,
                    outcome_unknown=True,
                )
            migration = ProjectLoreMigration(
                id=_stable_id(project_id, body.operation_key, "migration"),
                project_id=project_id,
                migration_version=body.mapping_version,
                status="ready",
                source_checksum=body.expected_source_checksum,
                result_checksum=operation.result_checksum,
                counts=dict(operation.counts or {}),
                validation_errors=[],
                completed_at=datetime.now(UTC).replace(tzinfo=None),
            )
            final_session.add(migration)
            await final_session.flush()
            operation.status = "ready"
            operation.migration_id = migration.id
            operation.error_code = None
            operation.completed_at = datetime.now(UTC).replace(tzinfo=None)
            project.lore_storage_mode = "relational"
            project.lore_migration_version = body.mapping_version
            if fault_at == "before_final_commit":
                raise _error("LORE_MIGRATION_FAULT_INJECTED", "升级测试故障。", status_code=500)
            try:
                await final_session.commit()
            except Exception as exc:
                raise _error(
                    "LORE_MIGRATION_OUTCOME_UNKNOWN",
                    "升级最终结果暂时无法确认，请使用原操作标识查询。",
                    status_code=503,
                    retryable=True,
                    outcome_unknown=True,
                ) from exc
        except Exception:
            if final_session.in_transaction():
                await final_session.rollback()
            raise

    if fault_at == "after_final_commit_unknown":
        raise _error(
            "LORE_MIGRATION_OUTCOME_UNKNOWN",
            "升级结果暂时无法确认，请使用原操作标识查询。",
            status_code=503,
            retryable=True,
            outcome_unknown=True,
        )
    return operation


async def commit_lore_migration(
    session_factory: async_sessionmaker[AsyncSession],
    project_id: str,
    user_id: str,
    body: LoreMigrationCommitInput,
    *,
    fault_at: str | None = None,
) -> LoreMigrationOperationResponse:
    """Create or resume one frozen migration operation and return its receipt."""
    operation_status, replayed, terminal_response = await _prepare_or_resume(
        session_factory,
        project_id,
        user_id,
        body,
        fault_at=fault_at,
    )
    if operation_status in {"ready", "failed"}:
        if terminal_response is None:
            raise _error(
                "LORE_MIGRATION_RECEIPT_MISSING",
                "升级收据无法读取。",
                status_code=500,
                outcome_unknown=True,
            )
        return terminal_response
    try:
        operation = await _verify_and_finalize(
            session_factory,
            project_id,
            user_id,
            body,
            fault_at=fault_at,
        )
    except LoreMigrationCommitError as exc:
        if exc.outcome_unknown:
            raise
        compensated = await _compensate_exact_materialization(
            session_factory,
            project_id,
            user_id,
            body.operation_key,
            str(exc.detail.get("code") or "LORE_MIGRATION_VALIDATION_FAILED"),
        )
        if compensated is not None:
            return build_migration_operation_response(compensated, replayed=replayed)
        raise _error(
            "LORE_MIGRATION_OUTCOME_UNKNOWN",
            "升级结果暂时无法安全确认，请按原操作标识查询并联系维护人员。",
            status_code=500,
            outcome_unknown=True,
        ) from exc
    return build_migration_operation_response(operation, replayed=replayed)
