"""Test-only proof that a legacy Lore migration can be committed and reversed.

This module deliberately has no API or CLI entrypoint.  Its mutating functions
refuse databases that cannot be identified as isolated test/rehearsal targets,
and they require both the application write freeze and an explicit all-instance
freeze assertion.  A post-commit restore is a *compensating rollback*, not a
database transaction rollback.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from collections.abc import Awaitable, Callable
from typing import Any, Mapping

from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings as app_settings
from app.core.lore_migration import (
    TYPE_DISPLAY_NAMES,
    TYPE_FIELD_SCHEMAS,
    deterministic_type_id,
    legacy_structured_payload,
    legacy_worldview_checksum,
    normalize_lore_name,
    project_legacy_worldview,
    structured_payload_checksum,
    validate_projection,
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
    LoreElementCreateOperation,
    LoreMergeOperation,
    LoreRelationCreateOperation,
    LoreReviewSuggestion,
    ProjectLoreMigration,
    SettingElement,
    SettingType,
    SettingTypeRevision,
)
from app.models.project import Project, Worldview


REHEARSAL_CONTRACT_VERSION = 1
_SAFE_DATABASE_MARKERS = re.compile(r"(?:^|[_-])(test|testing|rehearsal)(?:$|[_-])", re.I)


class LoreMigrationRehearsalError(RuntimeError):
    """A stable, non-sensitive rehearsal failure."""

    def __init__(self, code: str, phase: str, *, outcome_unknown: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.phase = phase
        self.outcome_unknown = outcome_unknown


@dataclass(frozen=True)
class RehearsalGuard:
    """Assertions that must be supplied by the isolated test harness."""

    environment_kind: str
    synthetic_fixture: bool
    isolated_database: bool
    all_application_instances_frozen: bool
    isolation_nonce: str


@dataclass(frozen=True)
class RehearsalAnchor:
    project_id: str
    preview_schema_version: int
    mapping_version: int
    expected_source_checksum: str
    expected_semantic_result_checksum: str
    operation_key: str


@dataclass(frozen=True)
class RehearsalReceipt:
    project_id: str
    migration_id: str
    migration_version: int
    source_checksum: str
    semantic_result_checksum: str
    relational_checksum: str
    operation_key: str
    replayed: bool


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _stable_id(project_id: str, operation_key: str, kind: str) -> str:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"lore-rehearsal:{project_id}:{operation_key}:{kind}",
    ).hex


def build_rehearsal_anchor(preview: Mapping[str, Any]) -> RehearsalAnchor:
    """Bind an internal rehearsal to one immutable C1 preview result."""
    fields = {
        "project_id": str(preview.get("project_id") or ""),
        "preview_schema_version": int(preview.get("preview_schema_version") or 0),
        "mapping_version": int(preview.get("mapping_version") or 0),
        "expected_source_checksum": str(preview.get("source_checksum") or ""),
        "expected_semantic_result_checksum": str(
            preview.get("semantic_result_checksum") or ""
        ),
    }
    operation_key = _sha256_json(fields)
    return RehearsalAnchor(**fields, operation_key=operation_key)


def _database_identity(session: AsyncSession) -> tuple[str, str]:
    url = make_url(str(session.get_bind().url))
    backend = url.get_backend_name()
    database = str(url.database or "")
    return backend, database


async def _require_guard(
    session: AsyncSession,
    guard: RehearsalGuard,
    *,
    project_id: str | None = None,
) -> None:
    backend, database = _database_identity(session)
    database_is_safe = (
        backend == "sqlite" and (database in {"", ":memory:"} or bool(_SAFE_DATABASE_MARKERS.search(database)))
    ) or (
        backend != "sqlite" and bool(_SAFE_DATABASE_MARKERS.search(database))
    )
    if (
        guard.environment_kind != "test"
        or not guard.synthetic_fixture
        or not guard.isolated_database
        or not database_is_safe
    ):
        raise LoreMigrationRehearsalError(
            "REHEARSAL_ISOLATION_NOT_PROVEN", "preflight"
        )
    if not guard.isolation_nonce:
        raise LoreMigrationRehearsalError(
            "REHEARSAL_ISOLATION_NOT_PROVEN", "preflight"
        )
    try:
        stored_nonce = await session.scalar(text(
            "SELECT nonce FROM lore_rehearsal_sentinel WHERE nonce = :nonce"
        ), {"nonce": guard.isolation_nonce})
    except Exception as exc:
        raise LoreMigrationRehearsalError(
            "REHEARSAL_ISOLATION_NOT_PROVEN", "preflight"
        ) from exc
    if not isinstance(stored_nonce, str) or not hmac.compare_digest(
        stored_nonce, guard.isolation_nonce
    ):
        raise LoreMigrationRehearsalError(
            "REHEARSAL_ISOLATION_NOT_PROVEN", "preflight"
        )
    if project_id is not None:
        project_count = int(await session.scalar(
            select(func.count()).select_from(Project)
        ) or 0)
        foreign_project_count = int(await session.scalar(
            select(func.count()).select_from(Project).where(Project.id != project_id)
        ) or 0)
        if project_count != 1 or foreign_project_count:
            raise LoreMigrationRehearsalError(
                "REHEARSAL_DATABASE_NOT_DEDICATED", "preflight"
            )
    if (
        not app_settings.LEGACY_JSON_WRITES_FROZEN
        or not guard.all_application_instances_frozen
    ):
        raise LoreMigrationRehearsalError(
            "MIGRATION_REQUIRES_WRITE_FREEZE", "preflight"
        )


def _raise_if_fault(fault_at: str | None, phase: str) -> None:
    if fault_at == phase:
        raise LoreMigrationRehearsalError("REHEARSAL_FAULT_INJECTED", phase)


async def _locked_project_worldview(
    session: AsyncSession, project_id: str
) -> tuple[Project, Worldview]:
    project = await session.scalar(
        select(Project).where(Project.id == project_id).with_for_update()
    )
    if project is None:
        raise LoreMigrationRehearsalError("REHEARSAL_PROJECT_MISSING", "preflight")
    worldview = await session.scalar(
        select(Worldview)
        .where(Worldview.project_id == project_id)
        .with_for_update()
    )
    if worldview is None:
        raise LoreMigrationRehearsalError("REHEARSAL_WORLDVIEW_MISSING", "preflight")
    return project, worldview


async def _existing_state(session: AsyncSession, project_id: str) -> dict[str, list[Any]]:
    return {
        "types": list((await session.scalars(
            select(SettingType).where(SettingType.project_id == project_id)
        )).all()),
        "elements": list((await session.scalars(
            select(SettingElement).where(SettingElement.project_id == project_id)
        )).all()),
        "maps": list((await session.scalars(
            select(LegacyElementMap).where(LegacyElementMap.project_id == project_id)
        )).all()),
        "migrations": list((await session.scalars(
            select(ProjectLoreMigration).where(ProjectLoreMigration.project_id == project_id)
        )).all()),
    }


def _validate_anchor(anchor: RehearsalAnchor, preview: Mapping[str, Any]) -> None:
    if anchor.preview_schema_version != PREVIEW_SCHEMA_VERSION:
        raise LoreMigrationRehearsalError("REHEARSAL_PREVIEW_VERSION_MISMATCH", "preflight")
    if anchor.mapping_version != MAPPING_VERSION:
        raise LoreMigrationRehearsalError("REHEARSAL_MAPPING_VERSION_MISMATCH", "preflight")
    if preview.get("overall_status") != "ready" or not preview.get("items"):
        raise LoreMigrationRehearsalError("REHEARSAL_PREVIEW_NOT_READY", "preflight")
    if (
        preview.get("project_id") != anchor.project_id
        or preview.get("source_checksum") != anchor.expected_source_checksum
        or preview.get("semantic_result_checksum")
        != anchor.expected_semantic_result_checksum
    ):
        raise LoreMigrationRehearsalError("REHEARSAL_PREVIEW_STALE", "preflight")
    if build_rehearsal_anchor(preview).operation_key != anchor.operation_key:
        raise LoreMigrationRehearsalError("REHEARSAL_ANCHOR_INVALID", "preflight")


def _field_schema(type_key: str) -> list[dict[str, Any]]:
    return [dict(field) for field in TYPE_FIELD_SCHEMAS[type_key]]


def _field_states(type_key: str, payload: Mapping[str, Any]) -> dict[str, str]:
    definitions = {field["key"]: field for field in TYPE_FIELD_SCHEMAS[type_key]}
    if set(payload) - set(definitions):
        raise LoreMigrationRehearsalError(
            "REHEARSAL_PREVIEW_CONTENT_INVALID", "preflight"
        )
    for key, value in payload.items():
        value_type = definitions[key].get("value_type", "string")
        if value not in (None, "") and value_type in {"string", "text", "reference"} and not isinstance(value, str):
            raise LoreMigrationRehearsalError(
                "REHEARSAL_PREVIEW_CONTENT_INVALID", "preflight"
            )
    return {
        field["key"]: (
            "provided" if payload.get(field["key"]) not in (None, "", [], {}) else "unknown"
        )
        for field in TYPE_FIELD_SCHEMAS[type_key]
    }


def _excerpt(item: Mapping[str, Any]) -> str:
    return _canonical_json(item["original_value"])


def _planned_manifest(
    project_id: str, operation_key: str, preview: Mapping[str, Any]
) -> dict[str, Any]:
    type_keys = sorted({str(item["proposed_type_key"]) for item in preview["items"]})
    items = []
    for item in preview["items"]:
        element_id = str(item["planned_element_id"])
        items.append({
            "element_id": element_id,
            "type_key": str(item["proposed_type_key"]),
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


async def _authoritative_snapshot(
    session: AsyncSession,
    project_id: str,
    anchor: RehearsalAnchor,
    preview: Mapping[str, Any],
) -> dict[str, Any]:
    manifest = _planned_manifest(project_id, anchor.operation_key, preview)
    type_rows = list((await session.scalars(
        select(SettingType)
        .where(SettingType.id.in_(manifest["type_ids"]))
        .order_by(SettingType.key)
    )).all())
    revision_rows = list((await session.scalars(
        select(SettingTypeRevision)
        .where(SettingTypeRevision.id.in_(manifest["type_revision_ids"]))
        .order_by(SettingTypeRevision.type_id)
    )).all())
    element_ids = [item["element_id"] for item in manifest["items"]]
    elements = list((await session.scalars(
        select(SettingElement)
        .where(SettingElement.id.in_(element_ids))
        .order_by(SettingElement.id)
    )).all())
    sources = list((await session.scalars(
        select(ElementSource)
        .where(ElementSource.element_id.in_(element_ids))
        .order_by(ElementSource.element_id)
    )).all())
    versions = list((await session.scalars(
        select(ElementVersion)
        .where(ElementVersion.element_id.in_(element_ids))
        .order_by(ElementVersion.element_id)
    )).all())
    maps = list((await session.scalars(
        select(LegacyElementMap)
        .where(LegacyElementMap.project_id == project_id)
        .order_by(LegacyElementMap.legacy_category, LegacyElementMap.legacy_index)
    )).all())
    events = list((await session.scalars(
        select(ElementStateEvent)
        .where(ElementStateEvent.element_id.in_(element_ids))
        .order_by(ElementStateEvent.element_id)
    )).all())
    unexpected_counts = {
        "types": int(await session.scalar(
            select(func.count()).select_from(SettingType).where(
                SettingType.project_id == project_id,
                SettingType.id.not_in(manifest["type_ids"]),
            )
        ) or 0),
        "elements": int(await session.scalar(
            select(func.count()).select_from(SettingElement).where(
                SettingElement.project_id == project_id,
                SettingElement.id.not_in(element_ids),
            )
        ) or 0),
        "relations": int(await session.scalar(
            select(func.count()).select_from(ElementRelation).where(
                ElementRelation.project_id == project_id
            )
        ) or 0),
        "element_create_operations": int(await session.scalar(
            select(func.count()).select_from(LoreElementCreateOperation).where(
                LoreElementCreateOperation.project_id == project_id
            )
        ) or 0),
        "relation_create_operations": int(await session.scalar(
            select(func.count()).select_from(LoreRelationCreateOperation).where(
                LoreRelationCreateOperation.project_id == project_id
            )
        ) or 0),
        "review_suggestions": int(await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestion).where(
                LoreReviewSuggestion.project_id == project_id
            )
        ) or 0),
        "merge_operations": int(await session.scalar(
            select(func.count()).select_from(LoreMergeOperation).where(
                LoreMergeOperation.project_id == project_id
            )
        ) or 0),
    }
    payload = {
        "types": [{
            "id": row.id,
            "key": row.key,
            "display_name": row.display_name,
            "is_builtin": row.is_builtin,
            "schema_revision": row.schema_revision,
            "field_schema": row.field_schema,
            "status": row.status,
        } for row in type_rows],
        "revisions": [{
            "id": row.id,
            "type_id": row.type_id,
            "revision": row.revision,
            "display_name": row.display_name,
            "field_schema": row.field_schema,
        } for row in revision_rows],
        "elements": [{
            "id": row.id,
            "project_id": row.project_id,
            "type_id": row.type_id,
            "name": row.name,
            "normalized_name": row.normalized_name,
            "payload": row.payload,
            "field_states": row.field_states,
            "confirmation_status": row.confirmation_status,
            "lifecycle_status": row.lifecycle_status,
            "enabled": row.enabled,
            "content_version": row.content_version,
            "lock_version": row.lock_version,
        } for row in elements],
        "sources": [{
            "id": row.id,
            "element_id": row.element_id,
            "source_kind": row.source_kind,
            "source_ref": row.source_ref,
            "locator": row.locator,
            "excerpt": row.excerpt,
            "excerpt_hash": row.excerpt_hash,
            "confirmation_status": row.confirmation_status,
            "is_primary": row.is_primary,
        } for row in sources],
        "versions": [{
            "id": row.id,
            "element_id": row.element_id,
            "version_no": row.version_no,
            "type_id": row.type_id,
            "type_schema_revision": row.type_schema_revision,
            "name": row.name,
            "payload": row.payload,
            "field_states": row.field_states,
            "source_id": row.source_id,
            "change_reason": row.change_reason,
            "created_by": row.created_by,
        } for row in versions],
        "maps": [{
            "id": row.id,
            "legacy_category": row.legacy_category,
            "legacy_index": row.legacy_index,
            "legacy_id": row.legacy_id,
            "element_id": row.element_id,
            "source_checksum": row.source_checksum,
        } for row in maps],
        "events": [{
            "id": row.id,
            "element_id": row.element_id,
            "event_kind": row.event_kind,
            "previous_lock_version": row.previous_lock_version,
            "new_lock_version": row.new_lock_version,
            "metadata": row.metadata_,
            "performed_by": row.performed_by,
        } for row in events],
        "unexpected_counts": unexpected_counts,
    }
    payload["checksum"] = _sha256_json(payload)
    return payload


def _assert_authoritative_snapshot(
    snapshot: Mapping[str, Any],
    preview: Mapping[str, Any],
    project_id: str,
    legacy_payload: Mapping[str, Any],
    *,
    worldview_id: str,
    owner_id: str | None,
    operation_key: str,
) -> None:
    expected_count = len(preview["items"])
    expected_type_count = len({item["proposed_type_key"] for item in preview["items"]})
    if any(len(snapshot[key]) != expected_count for key in ("elements", "sources", "versions", "maps", "events")):
        raise LoreMigrationRehearsalError("REHEARSAL_AUTHORITATIVE_COUNT_MISMATCH", "validation")
    if len(snapshot["types"]) != expected_type_count or len(snapshot["revisions"]) != expected_type_count:
        raise LoreMigrationRehearsalError("REHEARSAL_TYPE_COUNT_MISMATCH", "validation")
    if any(snapshot["unexpected_counts"].values()):
        raise LoreMigrationRehearsalError(
            "REHEARSAL_UNEXPECTED_RELATIONAL_STATE", "validation"
        )

    elements = {row["id"]: row for row in snapshot["elements"]}
    sources = {row["element_id"]: row for row in snapshot["sources"]}
    versions = {row["element_id"]: row for row in snapshot["versions"]}
    maps = {(row["legacy_category"], row["legacy_index"]): row for row in snapshot["maps"]}
    events = {row["element_id"]: row for row in snapshot["events"]}
    type_by_key = {row["key"]: row for row in snapshot["types"]}
    revision_by_type = {row["type_id"]: row for row in snapshot["revisions"]}
    manifest = _planned_manifest(project_id, operation_key, preview)
    expected_rows = {item["element_id"]: item for item in manifest["items"]}
    expected_revision_ids = {
        key: revision_id
        for key, revision_id in zip(
            manifest["type_keys"], manifest["type_revision_ids"], strict=True
        )
    }

    reconstructed: dict[str, list[Any]] = {
        key: [] for key in legacy_structured_payload(None)
    }
    for item in preview["items"]:
        element_id = item["planned_element_id"]
        type_key = item["proposed_type_key"]
        type_row = type_by_key.get(type_key)
        element = elements.get(element_id)
        source = sources.get(element_id)
        version = versions.get(element_id)
        map_row = maps.get((item["legacy_category"], item["legacy_index"]))
        event = events.get(element_id)
        expected_payload = item.get("effective_mapped_fields", item["mapped_fields"])
        expected_states = _field_states(type_key, expected_payload)
        expected_ids = expected_rows[element_id]
        if (
            type_row is None
            or type_row["id"] != deterministic_type_id(project_id, type_key)
            or type_row["display_name"] != TYPE_DISPLAY_NAMES[type_key]
            or type_row["schema_revision"] != 1
            or type_row["status"] != "active"
            or type_row["is_builtin"] is not True
            or type_row["field_schema"] != _field_schema(type_key)
            or revision_by_type.get(type_row["id"], {}).get("revision") != 1
            or revision_by_type.get(type_row["id"], {}).get("id")
            != expected_revision_ids[type_key]
            or revision_by_type.get(type_row["id"], {}).get("display_name")
            != TYPE_DISPLAY_NAMES[type_key]
            or revision_by_type.get(type_row["id"], {}).get("field_schema")
            != _field_schema(type_key)
            or element is None
            or element["project_id"] != project_id
            or element["type_id"] != type_row["id"]
            or element["name"] != item["name"]
            or element["normalized_name"] != normalize_lore_name(item["name"])
            or element["payload"] != expected_payload
            or element["field_states"] != expected_states
            or element["confirmation_status"] != "confirmed"
            or element["lifecycle_status"] != "active"
            or element["enabled"] is not True
            or (element["content_version"], element["lock_version"]) != (1, 1)
            or source is None
            or source["id"] != expected_ids["source_id"]
            or source["source_kind"] != item["source_kind"]
            or source["source_ref"] != f"worldviews:{worldview_id}"
            or source["excerpt"] != _excerpt(item)
            or source["excerpt_hash"] != hashlib.sha256(_excerpt(item).encode()).hexdigest()
            or source["excerpt_hash"]
            != hashlib.sha256(str(source["excerpt"]).encode()).hexdigest()
            or source["confirmation_status"] != "provided"
            or source["is_primary"] is not True
            or source["locator"] != {
                "legacy_category": item["legacy_category"],
                "legacy_index": item["legacy_index"],
                "source_checksum": preview["source_checksum"],
            }
            or version is None
            or version["id"] != expected_ids["version_id"]
            or version["version_no"] != 1
            or version["type_id"] != type_row["id"]
            or version["type_schema_revision"] != 1
            or version["name"] != item["name"]
            or version["payload"] != expected_payload
            or version["field_states"] != expected_states
            or version["source_id"] != source["id"]
            or version["change_reason"] != "隔离迁移演练"
            or version["created_by"] != owner_id
            or map_row is None
            or map_row["id"] != expected_ids["map_id"]
            or map_row["element_id"] != element_id
            or map_row["source_checksum"] != preview["source_checksum"]
            or map_row["legacy_id"] != item.get("legacy_id")
            or event is None
            or event["id"] != expected_ids["event_id"]
            or event["event_kind"] != "create"
            or event["previous_lock_version"] != 0
            or event["new_lock_version"] != 1
            or event["metadata"] != {"origin": "isolated_migration_rehearsal"}
            or event["performed_by"] != owner_id
        ):
            raise LoreMigrationRehearsalError("REHEARSAL_AUTHORITATIVE_CONTENT_MISMATCH", "validation")
        try:
            reconstructed[item["legacy_category"]].append(json.loads(source["excerpt"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise LoreMigrationRehearsalError(
                "REHEARSAL_COMPATIBILITY_CHECKSUM_MISMATCH", "validation"
            ) from exc

    if structured_payload_checksum(reconstructed) != structured_payload_checksum(dict(legacy_payload)):
        raise LoreMigrationRehearsalError("REHEARSAL_COMPATIBILITY_CHECKSUM_MISMATCH", "validation")


async def commit_rehearsal(
    session_factory: async_sessionmaker[AsyncSession],
    anchor: RehearsalAnchor,
    guard: RehearsalGuard,
    *,
    fault_at: str | None = None,
    phase_hook: Callable[[str], Awaitable[None]] | None = None,
) -> RehearsalReceipt:
    """Commit one synthetic legacy-to-relational rehearsal transaction."""
    async with session_factory() as session:
        try:
            await _require_guard(session, guard, project_id=anchor.project_id)
            project, worldview = await _locked_project_worldview(session, anchor.project_id)
            if phase_hook is not None:
                await phase_hook("after_source_lock")
            existing = await _existing_state(session, anchor.project_id)
            matching = next((row for row in existing["migrations"] if row.source_checksum == anchor.expected_source_checksum), None)
            if matching is not None:
                counts = matching.counts or {}
                if (
                    counts.get("operation_key") != anchor.operation_key
                    or counts.get("semantic_result_checksum")
                    != anchor.expected_semantic_result_checksum
                ):
                    raise LoreMigrationRehearsalError("REHEARSAL_IDEMPOTENCY_CONFLICT", "preflight")
                if matching.status != "ready" or project.lore_storage_mode != "relational":
                    raise LoreMigrationRehearsalError("REHEARSAL_NONTERMINAL_RECEIPT", "preflight")
                await session.commit()
                receipt = RehearsalReceipt(
                    project_id=anchor.project_id,
                    migration_id=matching.id,
                    migration_version=matching.migration_version,
                    source_checksum=matching.source_checksum,
                    semantic_result_checksum=counts["semantic_result_checksum"],
                    relational_checksum=str(matching.result_checksum or ""),
                    operation_key=anchor.operation_key,
                    replayed=True,
                )
                await validate_rehearsal(session_factory, anchor, guard)
                return receipt

            if project.lore_storage_mode != "legacy" or project.lore_migration_version is not None:
                raise LoreMigrationRehearsalError("REHEARSAL_PROJECT_NOT_LEGACY", "preflight")
            if existing["types"]:
                raise LoreMigrationRehearsalError("REHEARSAL_EXISTING_TYPES", "preflight")
            preview = build_migration_preview(
                anchor.project_id,
                "legacy",
                worldview,
                existing_elements=existing["elements"],
                existing_legacy_map_count=len(existing["maps"]),
                existing_migration_count=len(existing["migrations"]),
            )
            _validate_anchor(anchor, preview)
            await _require_guard(session, guard, project_id=anchor.project_id)
            if migration_preview_source_checksum(worldview) != anchor.expected_source_checksum:
                raise LoreMigrationRehearsalError("REHEARSAL_SOURCE_CHANGED", "preflight")

            manifest = _planned_manifest(anchor.project_id, anchor.operation_key, preview)
            types: dict[str, SettingType] = {}
            for index, type_key in enumerate(manifest["type_keys"]):
                type_id = deterministic_type_id(anchor.project_id, type_key)
                setting_type = SettingType(
                    id=type_id,
                    project_id=anchor.project_id,
                    key=type_key,
                    display_name=TYPE_DISPLAY_NAMES[type_key],
                    description=f"内置{TYPE_DISPLAY_NAMES[type_key]}类型",
                    is_builtin=True,
                    schema_revision=1,
                    field_schema=_field_schema(type_key),
                    status="active",
                )
                session.add(setting_type)
                types[type_key] = setting_type
                session.add(SettingTypeRevision(
                    id=manifest["type_revision_ids"][index],
                    type_id=type_id,
                    revision=1,
                    display_name=TYPE_DISPLAY_NAMES[type_key],
                    field_schema=_field_schema(type_key),
                    change_summary="隔离迁移演练初始化",
                ))
            await session.flush()
            _raise_if_fault(fault_at, "after_types")

            row_manifest = {item["element_id"]: item for item in manifest["items"]}
            planned_rows: list[dict[str, Any]] = []
            for item in preview["items"]:
                ids = row_manifest[item["planned_element_id"]]
                type_key = item["proposed_type_key"]
                type_id = types[type_key].id
                payload = dict(item.get("effective_mapped_fields", item["mapped_fields"]))
                field_states = _field_states(type_key, payload)
                element = SettingElement(
                    id=item["planned_element_id"],
                    project_id=anchor.project_id,
                    type_id=type_id,
                    name=item["name"],
                    normalized_name=normalize_lore_name(item["name"]),
                    summary="",
                    payload=payload,
                    payload_schema_revision=1,
                    field_states=field_states,
                    confirmation_status="confirmed",
                    lifecycle_status="active",
                    enabled=True,
                    content_version=1,
                    lock_version=1,
                )
                session.add(element)
                excerpt = _excerpt(item)
                planned_rows.append({
                    "item": item,
                    "ids": ids,
                    "type_id": type_id,
                    "payload": payload,
                    "field_states": field_states,
                    "excerpt": excerpt,
                })
            await session.flush()
            _raise_if_fault(fault_at, "after_first_element")

            for row in planned_rows:
                item = row["item"]
                ids = row["ids"]
                excerpt = row["excerpt"]
                session.add(ElementSource(
                    id=ids["source_id"],
                    project_id=anchor.project_id,
                    element_id=item["planned_element_id"],
                    source_kind=item["source_kind"],
                    source_ref=f"worldviews:{worldview.id}",
                    locator={
                        "legacy_category": item["legacy_category"],
                        "legacy_index": item["legacy_index"],
                        "source_checksum": preview["source_checksum"],
                    },
                    excerpt=excerpt,
                    excerpt_hash=hashlib.sha256(excerpt.encode()).hexdigest(),
                    confirmation_status="provided",
                    is_primary=True,
                ))
            await session.flush()
            _raise_if_fault(fault_at, "after_first_source")

            for row in planned_rows:
                item = row["item"]
                ids = row["ids"]
                session.add(ElementVersion(
                    id=ids["version_id"],
                    element_id=item["planned_element_id"],
                    version_no=1,
                    type_id=row["type_id"],
                    type_schema_revision=1,
                    name=item["name"],
                    summary="",
                    payload=row["payload"],
                    field_states=row["field_states"],
                    change_reason="隔离迁移演练",
                    source_id=ids["source_id"],
                    created_by=project.owner_id,
                ))
            await session.flush()
            _raise_if_fault(fault_at, "after_first_version")

            for row in planned_rows:
                item = row["item"]
                ids = row["ids"]
                session.add(LegacyElementMap(
                    id=ids["map_id"],
                    project_id=anchor.project_id,
                    legacy_category=item["legacy_category"],
                    legacy_index=item["legacy_index"],
                    legacy_id=item.get("legacy_id"),
                    element_id=item["planned_element_id"],
                    source_checksum=preview["source_checksum"],
                ))
                session.add(ElementStateEvent(
                    id=ids["event_id"],
                    element_id=item["planned_element_id"],
                    event_kind="create",
                    previous_lock_version=0,
                    new_lock_version=1,
                    performed_by=project.owner_id,
                    metadata_={"origin": "isolated_migration_rehearsal"},
                ))
            await session.flush()
            _raise_if_fault(fault_at, "after_first_map")
            snapshot = await _authoritative_snapshot(session, anchor.project_id, anchor, preview)
            _assert_authoritative_snapshot(
                snapshot,
                preview,
                anchor.project_id,
                legacy_structured_payload(worldview),
                worldview_id=worldview.id,
                owner_id=project.owner_id,
                operation_key=anchor.operation_key,
            )
            migration_id = _stable_id(anchor.project_id, anchor.operation_key, "migration")
            migration = ProjectLoreMigration(
                id=migration_id,
                project_id=anchor.project_id,
                migration_version=anchor.mapping_version,
                status="ready",
                source_checksum=anchor.expected_source_checksum,
                result_checksum=snapshot["checksum"],
                counts={
                    "contract_version": REHEARSAL_CONTRACT_VERSION,
                    "operation_key": anchor.operation_key,
                    "preview_schema_version": anchor.preview_schema_version,
                    "semantic_result_checksum": anchor.expected_semantic_result_checksum,
                    "pre_storage_mode": "legacy",
                    "pre_migration_version": None,
                    "type_count": len(manifest["type_keys"]),
                    "element_count": len(manifest["items"]),
                },
                validation_errors=[],
                completed_at=datetime.now(UTC).replace(tzinfo=None),
            )
            session.add(migration)
            await session.flush()
            _raise_if_fault(fault_at, "after_audit")

            project.lore_storage_mode = "relational"
            project.lore_migration_version = anchor.mapping_version
            await session.flush()
            _raise_if_fault(fault_at, "after_mode_switch")
            await _require_guard(session, guard, project_id=anchor.project_id)
            if migration_preview_source_checksum(worldview) != anchor.expected_source_checksum:
                raise LoreMigrationRehearsalError("REHEARSAL_SOURCE_CHANGED", "commit")
            _raise_if_fault(fault_at, "before_commit")
            await session.commit()
        except Exception:
            if session.in_transaction():
                await session.rollback()
            raise

    if fault_at == "after_commit_unknown":
        raise LoreMigrationRehearsalError(
            "REHEARSAL_COMMIT_OUTCOME_UNKNOWN", "commit", outcome_unknown=True
        )
    return RehearsalReceipt(
        project_id=anchor.project_id,
        migration_id=migration_id,
        migration_version=anchor.mapping_version,
        source_checksum=anchor.expected_source_checksum,
        semantic_result_checksum=anchor.expected_semantic_result_checksum,
        relational_checksum=snapshot["checksum"],
        operation_key=anchor.operation_key,
        replayed=False,
    )


async def validate_rehearsal(
    session_factory: async_sessionmaker[AsyncSession],
    anchor: RehearsalAnchor,
    guard: RehearsalGuard,
    *,
    fault_at: str | None = None,
) -> dict[str, Any]:
    """Validate a committed rehearsal using a fresh authoritative session."""
    async with session_factory() as session:
        await _require_guard(session, guard, project_id=anchor.project_id)
        project = await session.scalar(select(Project).where(Project.id == anchor.project_id))
        worldview = await session.scalar(select(Worldview).where(Worldview.project_id == anchor.project_id))
        migration = await session.scalar(select(ProjectLoreMigration).where(
            ProjectLoreMigration.project_id == anchor.project_id,
            ProjectLoreMigration.migration_version == anchor.mapping_version,
            ProjectLoreMigration.source_checksum == anchor.expected_source_checksum,
        ))
        if project is None or worldview is None or migration is None:
            raise LoreMigrationRehearsalError("REHEARSAL_RECEIPT_MISSING", "validation")
        if project.lore_storage_mode != "relational" or project.lore_migration_version != anchor.mapping_version:
            raise LoreMigrationRehearsalError("REHEARSAL_MODE_MISMATCH", "validation")
        if migration.status != "ready" or (migration.counts or {}).get("operation_key") != anchor.operation_key:
            raise LoreMigrationRehearsalError("REHEARSAL_RECEIPT_MISMATCH", "validation")
        if migration_preview_source_checksum(worldview) != anchor.expected_source_checksum:
            raise LoreMigrationRehearsalError("REHEARSAL_SOURCE_CHANGED", "validation")
        preview = build_migration_preview(anchor.project_id, "legacy", worldview)
        _validate_anchor(anchor, preview)
        snapshot = await _authoritative_snapshot(session, anchor.project_id, anchor, preview)
        _assert_authoritative_snapshot(
            snapshot,
            preview,
            anchor.project_id,
            legacy_structured_payload(worldview),
            worldview_id=worldview.id,
            owner_id=project.owner_id,
            operation_key=anchor.operation_key,
        )
        if snapshot["checksum"] != migration.result_checksum:
            raise LoreMigrationRehearsalError("REHEARSAL_RESULT_CHECKSUM_MISMATCH", "validation")
        _raise_if_fault(fault_at, "post_commit_validation")
        return {
            "status": "passed",
            "source_checksum": anchor.expected_source_checksum,
            "relational_checksum": snapshot["checksum"],
            "legacy_rows_deleted": 0,
            "counts": {
                "types": len(snapshot["types"]),
                "elements": len(snapshot["elements"]),
                "versions": len(snapshot["versions"]),
                "sources": len(snapshot["sources"]),
                "legacy_maps": len(snapshot["maps"]),
                "state_events": len(snapshot["events"]),
                "migration_records": 1,
            },
        }


async def compensating_rollback_rehearsal(
    session_factory: async_sessionmaker[AsyncSession],
    anchor: RehearsalAnchor,
    guard: RehearsalGuard,
    *,
    fault_at: str | None = None,
) -> None:
    """Remove only unchanged rows created by this isolated rehearsal."""
    async with session_factory() as session:
        try:
            await _require_guard(session, guard, project_id=anchor.project_id)
            project, worldview = await _locked_project_worldview(session, anchor.project_id)
            migration = await session.scalar(
                select(ProjectLoreMigration).where(
                    ProjectLoreMigration.project_id == anchor.project_id,
                    ProjectLoreMigration.migration_version == anchor.mapping_version,
                    ProjectLoreMigration.source_checksum == anchor.expected_source_checksum,
                ).with_for_update()
            )
            if migration is None or migration.status != "ready":
                raise LoreMigrationRehearsalError("ROLLBACK_RECEIPT_MISSING", "rollback")
            if project.lore_storage_mode != "relational" or project.lore_migration_version != anchor.mapping_version:
                raise LoreMigrationRehearsalError("ROLLBACK_MODE_MISMATCH", "rollback")
            if migration_preview_source_checksum(worldview) != anchor.expected_source_checksum:
                raise LoreMigrationRehearsalError("ROLLBACK_UNSAFE", "rollback")
            preview = build_migration_preview(anchor.project_id, "legacy", worldview)
            _validate_anchor(anchor, preview)
            try:
                snapshot = await _authoritative_snapshot(session, anchor.project_id, anchor, preview)
                _assert_authoritative_snapshot(
                    snapshot,
                    preview,
                    anchor.project_id,
                    legacy_structured_payload(worldview),
                    worldview_id=worldview.id,
                    owner_id=project.owner_id,
                    operation_key=anchor.operation_key,
                )
            except LoreMigrationRehearsalError as exc:
                raise LoreMigrationRehearsalError("ROLLBACK_UNSAFE", "rollback") from exc
            if snapshot["checksum"] != migration.result_checksum:
                raise LoreMigrationRehearsalError("ROLLBACK_UNSAFE", "rollback")

            manifest = _planned_manifest(anchor.project_id, anchor.operation_key, preview)
            element_ids = [item["element_id"] for item in manifest["items"]]
            await session.execute(delete(ElementStateEvent).where(ElementStateEvent.element_id.in_(element_ids)))
            await session.execute(delete(ElementVersion).where(ElementVersion.element_id.in_(element_ids)))
            await session.execute(delete(ElementSource).where(ElementSource.element_id.in_(element_ids)))
            await session.execute(delete(LegacyElementMap).where(LegacyElementMap.project_id == anchor.project_id))
            _raise_if_fault(fault_at, "during_compensating_cleanup")
            await session.execute(delete(SettingElement).where(SettingElement.id.in_(element_ids)))
            await session.execute(delete(SettingTypeRevision).where(SettingTypeRevision.id.in_(manifest["type_revision_ids"])))
            await session.execute(delete(SettingType).where(SettingType.id.in_(manifest["type_ids"])))
            await session.execute(delete(ProjectLoreMigration).where(ProjectLoreMigration.id == migration.id))
            project.lore_storage_mode = "legacy"
            project.lore_migration_version = None
            await _require_guard(session, guard, project_id=anchor.project_id)
            await session.commit()
        except Exception:
            if session.in_transaction():
                await session.rollback()
            raise


async def validate_rehearsal_baseline(
    session_factory: async_sessionmaker[AsyncSession],
    anchor: RehearsalAnchor,
    guard: RehearsalGuard,
) -> dict[str, Any]:
    """Prove a compensating rollback restored the synthetic legacy baseline."""
    async with session_factory() as session:
        await _require_guard(session, guard, project_id=anchor.project_id)
        project = await session.scalar(select(Project).where(Project.id == anchor.project_id))
        worldview = await session.scalar(select(Worldview).where(Worldview.project_id == anchor.project_id))
        if project is None or worldview is None:
            raise LoreMigrationRehearsalError("REHEARSAL_BASELINE_MISSING", "post_rollback_validation")
        if project.lore_storage_mode != "legacy" or project.lore_migration_version is not None:
            raise LoreMigrationRehearsalError("REHEARSAL_BASELINE_MODE_MISMATCH", "post_rollback_validation")
        if migration_preview_source_checksum(worldview) != anchor.expected_source_checksum:
            raise LoreMigrationRehearsalError("REHEARSAL_BASELINE_SOURCE_MISMATCH", "post_rollback_validation")
        preview = build_migration_preview(anchor.project_id, "legacy", worldview)
        _validate_anchor(anchor, preview)
        manifest = _planned_manifest(anchor.project_id, anchor.operation_key, preview)
        legacy_projection = project_legacy_worldview(anchor.project_id, worldview)
        legacy_validation = validate_projection(legacy_projection)
        if (
            not legacy_validation["valid"]
            or legacy_projection.checksum != legacy_worldview_checksum(worldview)
        ):
            raise LoreMigrationRehearsalError(
                "REHEARSAL_BASELINE_PROJECTION_MISMATCH",
                "post_rollback_validation",
            )
        counts = {}
        for name, model in (
            ("types", SettingType),
            ("elements", SettingElement),
            ("versions", ElementVersion),
            ("sources", ElementSource),
            ("legacy_maps", LegacyElementMap),
            ("state_events", ElementStateEvent),
            ("migration_records", ProjectLoreMigration),
        ):
            statement = select(func.count()).select_from(model)
            if hasattr(model, "project_id"):
                statement = statement.where(model.project_id == anchor.project_id)
            elif model in {ElementVersion, ElementStateEvent}:
                statement = statement.where(model.element_id.in_(
                    [item["planned_element_id"] for item in build_migration_preview(
                        anchor.project_id, "legacy", worldview
                    )["items"]]
                ))
            counts[name] = int(await session.scalar(statement) or 0)
        counts["type_revisions"] = int(await session.scalar(
            select(func.count()).select_from(SettingTypeRevision).where(
                SettingTypeRevision.id.in_(manifest["type_revision_ids"])
            )
        ) or 0)
        if any(counts.values()):
            raise LoreMigrationRehearsalError("REHEARSAL_BASELINE_RESIDUE", "post_rollback_validation")
        return {
            "status": "passed",
            "final_mode": "legacy",
            "source_checksum": anchor.expected_source_checksum,
            "semantic_result_checksum": preview["semantic_result_checksum"],
            "legacy_projection_checksum": legacy_projection.checksum,
            "legacy_projection_ids": [element.id for element in legacy_projection.elements],
            "counts": counts,
            "legacy_rows_deleted": 0,
        }


def build_sanitized_rehearsal_report(
    *,
    anchor: RehearsalAnchor,
    guard: RehearsalGuard,
    database_backend: str,
    phases: Mapping[str, str],
    validation: Mapping[str, Any],
    baseline_validation: Mapping[str, Any],
    report_hmac_key: bytes,
    error_category: str | None = None,
) -> dict[str, Any]:
    """Build a report without database identity, user content, SQL, or traceback."""
    if len(report_hmac_key) < 32:
        raise ValueError("report_hmac_key must contain at least 32 bytes")
    if (
        guard.environment_kind != "test"
        or not guard.synthetic_fixture
        or not guard.isolated_database
        or not guard.all_application_instances_frozen
        or not guard.isolation_nonce
    ):
        raise ValueError("verified isolated rehearsal guard is required")

    def digest(value: str) -> str:
        return hmac.new(report_hmac_key, value.encode(), hashlib.sha256).hexdigest()

    allowed_phase_names = {
        "preview",
        "commit",
        "post_commit_validation",
        "compensating_rollback",
        "post_rollback_validation",
    }
    allowed_phase_states = {"not_run", "passed", "failed", "blocked", "unknown"}
    phase_order = (
        "preview",
        "commit",
        "post_commit_validation",
        "compensating_rollback",
        "post_rollback_validation",
    )
    safe_phases = {
        name: (
            phases.get(name, "not_run")
            if phases.get(name, "not_run") in allowed_phase_states
            else "unknown"
        )
        for name in phase_order
    }
    allowed_count_names = {
        "types",
        "type_revisions",
        "elements",
        "versions",
        "sources",
        "legacy_maps",
        "state_events",
        "migration_records",
    }
    required_baseline_count_names = set(allowed_count_names)
    safe_counts = {
        name: max(0, int(value))
        for name, value in (validation.get("counts") or {}).items()
        if name in allowed_count_names
    }
    safe_baseline_counts = {
        name: max(0, int(value))
        for name, value in (baseline_validation.get("counts") or {}).items()
        if name in allowed_count_names
    }
    safe_backend = database_backend if database_backend in {"sqlite", "postgresql"} else "other"
    validation_ok = (
        validation.get("status") == "passed"
        and validation.get("legacy_rows_deleted") == 0
    )
    baseline_ok = (
        baseline_validation.get("status") == "passed"
        and baseline_validation.get("legacy_rows_deleted") == 0
        and baseline_validation.get("final_mode") == "legacy"
        and set((baseline_validation.get("counts") or {}).keys())
        >= required_baseline_count_names
        and not any(safe_baseline_counts.values())
        and baseline_validation.get("source_checksum")
        == anchor.expected_source_checksum
        and baseline_validation.get("semantic_result_checksum")
        == anchor.expected_semantic_result_checksum
        and bool(baseline_validation.get("legacy_projection_checksum"))
        and bool(baseline_validation.get("legacy_projection_ids"))
    )
    if safe_phases["post_commit_validation"] == "passed" and not validation_ok:
        safe_phases["post_commit_validation"] = "failed"
    if safe_phases["post_rollback_validation"] == "passed" and not baseline_ok:
        safe_phases["post_rollback_validation"] = "failed"
    rollback_attempted = safe_phases["compensating_rollback"] != "not_run"
    rollback_succeeded = (
        safe_phases["compensating_rollback"] == "passed"
        and safe_phases["post_rollback_validation"] == "passed"
    )
    evidence_passed = (
        all(state == "passed" for state in safe_phases.values())
        and validation_ok
        and baseline_ok
    )
    if evidence_passed:
        safe_overall_status = "passed"
    elif "blocked" in safe_phases.values():
        safe_overall_status = "blocked"
    elif "failed" in safe_phases.values():
        safe_overall_status = "failed"
    else:
        safe_overall_status = "unknown"
    safe_failed_phase = next(
        (name for name in phase_order if safe_phases[name] != "passed"),
        None,
    )
    safe_error_category = (
        error_category
        if error_category is not None
        and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error_category)
        else None
    )
    source_checksum = str(validation.get("source_checksum") or "")
    relational_checksum = str(validation.get("relational_checksum") or "")
    if source_checksum != anchor.expected_source_checksum:
        raise ValueError("validation source does not match rehearsal anchor")

    return {
        "purpose": "isolated_test_database_migration_and_rollback_rehearsal",
        "production_migration_authorized": False,
        "real_user_data_accessed": False,
        "deployment_conclusion": False,
        "disclaimer": (
            "演练通过仅证明当前隔离样本可完成写入、校验和补偿回滚；"
            "不代表真实项目已迁移、生产数据兼容或获得上线授权。"
        ),
        "contract_version": REHEARSAL_CONTRACT_VERSION,
        "checked_at": datetime.now(UTC).isoformat(),
        "environment_kind": "test",
        "database_backend": safe_backend,
        "sample_kind": "synthetic_isolated_fixture",
        "digest_scope": "rehearsal_report",
        "operation_digest": digest(anchor.operation_key),
        "source_digest": digest(source_checksum),
        "relational_digest": digest(relational_checksum) if relational_checksum else None,
        "phases": safe_phases,
        "counts": safe_counts,
        "post_rollback_counts": safe_baseline_counts,
        "legacy_rows_deleted": validation.get("legacy_rows_deleted"),
        "rollback_attempted": rollback_attempted,
        "rollback_succeeded": rollback_succeeded,
        "overall_status": safe_overall_status,
        "failed_phase": safe_failed_phase,
        "error_category": safe_error_category,
        "final_mode": (
            "legacy"
            if baseline_validation.get("final_mode") == "legacy" and rollback_succeeded
            else "unknown"
        ),
        "next_action": "review_report_only_no_production_action",
    }
