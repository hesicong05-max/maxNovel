"""Auditable, fail-closed author resolutions for legacy migration issues."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.core.lore_migration import BUILTIN_TYPE_KEYS
from app.core.lore_migration_preview import (
    MAPPING_VERSION,
    PREVIEW_SCHEMA_VERSION,
    RESOLVABLE_REASON_CODES,
    build_migration_preview,
    migration_preview_source_checksum,
)
from app.models.lore import (
    LegacyElementMap,
    LegacyLoreResolution,
    LegacyLoreResolutionEvent,
    ProjectLoreMigration,
    SettingElement,
)
from app.models.project import Project, Worldview
from app.schemas.lore import (
    LegacyLoreResolutionInput,
    LegacyLoreResolutionResponse,
    LegacyLoreResolutionRevokeInput,
    LegacyLoreResolutionSummary,
)


_FINGERPRINT_VERSION = "legacy-lore-resolution:v1"
_SOURCE_KINDS = frozenset({"manual", "imported", "hybrid"})
_DUPLICATE_REASONS = frozenset({
    "duplicate_name_same_type", "duplicate_name_cross_type"
})


class LegacyLoreResolutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.detail = {"code": code, "message": message, "retryable": False}


def _error(code: str, message: str, *, status_code: int = 409) -> None:
    raise LegacyLoreResolutionError(code, message, status_code=status_code)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _request_fingerprint(
    project_id: str,
    user_id: str,
    action: str,
    payload: Mapping[str, Any],
) -> str:
    value = {
        "project_id": project_id,
        "performed_by": user_id,
        "action": action,
        "payload": dict(payload),
    }
    return hashlib.sha256(
        f"{_FINGERPRINT_VERSION}\n{_canonical_json(value)}".encode("utf-8")
    ).hexdigest()


def _summary(
    resolution: LegacyLoreResolution,
    *,
    current_source_checksum: str | None = None,
    status_override: str | None = None,
    applies: bool = False,
) -> LegacyLoreResolutionSummary:
    status = status_override or resolution.status
    if (
        current_source_checksum is not None
        and resolution.source_checksum != current_source_checksum
    ):
        status = "expired"
    return LegacyLoreResolutionSummary(
        id=resolution.id,
        legacy_category=resolution.legacy_category,
        legacy_index=resolution.legacy_index,
        reason_code=resolution.reason_code,
        decision_code=resolution.decision_code,
        decision_payload=dict(resolution.decision_payload or {}),
        status=status,
        lock_version=resolution.lock_version,
        created_at=resolution.created_at,
        updated_at=resolution.updated_at,
        applies=applies,
    )


def _ensure_resolution_writes_available() -> None:
    if app_settings.LEGACY_JSON_WRITES_FROZEN:
        _error(
            "LORE_MIGRATION_RESOLUTION_MAINTENANCE",
            "设定仓库正在维护，没有保存迁移决定。",
            status_code=503,
        )


def resolution_response(
    resolution: LegacyLoreResolution,
    event: LegacyLoreResolutionEvent,
    *,
    replayed: bool,
) -> LegacyLoreResolutionResponse:
    return LegacyLoreResolutionResponse(
        resolution=_summary(resolution),
        operation_key=event.operation_key,
        replayed=replayed,
    )


async def _locked_project_worldview(
    db: AsyncSession,
    project_id: str,
    user_id: str,
) -> tuple[Project, Worldview]:
    project = await db.scalar(
        select(Project).where(Project.id == project_id).with_for_update()
    )
    if project is None:
        _error("LORE_MIGRATION_PROJECT_MISSING", "项目不存在。", status_code=404)
    if project.owner_id != user_id:
        _error("LORE_MIGRATION_RESOLUTION_FORBIDDEN", "无权操作此项目。", status_code=403)
    if project.lore_storage_mode != "legacy" or project.lore_migration_version is not None:
        _error(
            "LORE_MIGRATION_RESOLUTION_PROJECT_NOT_LEGACY",
            "项目已进入升级流程，不能再修改迁移决定。",
        )
    worldview = await db.scalar(
        select(Worldview)
        .where(Worldview.project_id == project_id)
        .with_for_update()
    )
    if worldview is None:
        _error("LORE_MIGRATION_WORLDVIEW_MISSING", "项目没有可检查的旧世界观资料。")
    return project, worldview


async def _preview(
    db: AsyncSession,
    project: Project,
    worldview: Worldview,
) -> tuple[dict[str, Any], list[LegacyLoreResolution]]:
    project_id = project.id
    resolutions = list((await db.scalars(
        select(LegacyLoreResolution)
        .where(LegacyLoreResolution.project_id == project_id)
        .order_by(LegacyLoreResolution.created_at, LegacyLoreResolution.id)
    )).all())
    existing_elements = list((await db.scalars(
        select(SettingElement).where(SettingElement.project_id == project_id)
    )).all())
    map_count = int(await db.scalar(
        select(func.count()).select_from(LegacyElementMap).where(
            LegacyElementMap.project_id == project_id
        )
    ) or 0)
    migration_count = int(await db.scalar(
        select(func.count()).select_from(ProjectLoreMigration).where(
            ProjectLoreMigration.project_id == project_id
        )
    ) or 0)
    return build_migration_preview(
        project_id,
        project.lore_storage_mode or "legacy",
        worldview,
        existing_elements=existing_elements,
        existing_legacy_map_count=map_count,
        existing_migration_count=migration_count,
        resolutions=resolutions,
    ), resolutions


def _find_item(preview: Mapping[str, Any], body: LegacyLoreResolutionInput) -> dict[str, Any]:
    for item in preview["items"]:
        if (
            item["legacy_category"] == body.legacy_category
            and item["legacy_index"] == body.legacy_index
            and item["item_fingerprint"] == body.item_fingerprint
        ):
            return item
    _error(
        "LORE_MIGRATION_RESOLUTION_TARGET_STALE",
        "旧资料条目已经变化，请重新检查后再决定。",
    )


def _validate_preview(
    project_id: str,
    preview: Mapping[str, Any],
    body: LegacyLoreResolutionInput,
) -> dict[str, Any]:
    if (
        body.preview_schema_version != PREVIEW_SCHEMA_VERSION
        or body.mapping_version != MAPPING_VERSION
    ):
        _error(
            "LORE_MIGRATION_RESOLUTION_VERSION_STALE",
            "预检版本已经更新，请重新检查后再决定。",
        )
    if (
        preview["project_id"] != project_id
        or preview["source_checksum"] != body.expected_source_checksum
        or preview["semantic_result_checksum"]
        != body.expected_semantic_result_checksum
    ):
        _error(
            "LORE_MIGRATION_RESOLUTION_PREVIEW_STALE",
            "旧资料或已有决定已经变化，请重新检查后再决定。",
        )
    item = _find_item(preview, body)
    if body.reason_code not in item["effective_reason_codes"]:
        _error(
            "LORE_MIGRATION_RESOLUTION_REASON_STALE",
            "该问题已不存在或已变化，请重新检查。",
        )
    if body.reason_code not in RESOLVABLE_REASON_CODES:
        _error(
            "LORE_MIGRATION_RESOLUTION_REASON_NOT_ALLOWED",
            "该问题不能通过人工决定绕过，请按建议修正原资料。",
            status_code=422,
        )
    return item


def _validate_decision(
    preview: Mapping[str, Any],
    item: Mapping[str, Any],
    body: LegacyLoreResolutionInput,
) -> None:
    reason = body.reason_code
    payload = body.decision_payload
    if reason == "type_confirmation_required":
        valid = (
            body.decision_code == "confirm_type"
            and set(payload) == {"type_key"}
            and payload.get("type_key") in BUILTIN_TYPE_KEYS
        )
    elif reason == "source_missing":
        valid = (
            body.decision_code == "confirm_source"
            and set(payload) == {"source_kind"}
            and payload.get("source_kind") in _SOURCE_KINDS
        )
    elif reason == "raw_text_excerpt_unverified":
        valid = (
            body.decision_code == "accept_unlocated_source"
            and payload == {
                "confirmed_by_author": True,
                "exact_excerpt_available": False,
            }
        )
    elif reason == "unmapped_fields":
        valid = (
            body.decision_code == "preserve_unmapped_fields"
            and set(payload) == {"field_names"}
            and sorted(payload.get("field_names") or [])
            == item["effective_unmapped_fields"]
        )
    elif reason in _DUPLICATE_REASONS:
        members = sorted(
            candidate["item_fingerprint"]
            for candidate in preview["items"]
            if candidate.get("group_fingerprint") == item.get("group_fingerprint")
        )
        valid = (
            body.decision_code == "confirm_distinct_same_name"
            and body.group_fingerprint == item.get("group_fingerprint")
            and set(payload) == {"member_fingerprints"}
            and sorted(payload.get("member_fingerprints") or []) == members
            and len(members) > 1
        )
    else:
        valid = False
    if not valid:
        _error(
            "LORE_MIGRATION_RESOLUTION_DECISION_INVALID",
            "决定内容与当前问题不匹配，未保存任何内容。",
            status_code=422,
        )


async def _event_by_key(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    operation_key: str,
) -> LegacyLoreResolutionEvent | None:
    return await db.scalar(select(LegacyLoreResolutionEvent).where(
        LegacyLoreResolutionEvent.project_id == project_id,
        LegacyLoreResolutionEvent.performed_by == user_id,
        LegacyLoreResolutionEvent.operation_key == operation_key,
    ))


async def _replay(
    db: AsyncSession,
    event: LegacyLoreResolutionEvent,
    fingerprint: str,
) -> LegacyLoreResolutionResponse:
    if event.request_fingerprint != fingerprint:
        _error(
            "LORE_MIGRATION_RESOLUTION_OPERATION_CONFLICT",
            "此操作标识已用于不同请求，请停止并核对。",
        )
    resolution = await db.scalar(select(LegacyLoreResolution).where(
        LegacyLoreResolution.project_id == event.project_id,
        LegacyLoreResolution.id == event.resolution_id,
    ))
    if resolution is None:
        _error(
            "LORE_MIGRATION_RESOLUTION_RECEIPT_INVALID",
            "决定记录不完整，请停止操作并联系管理员。",
            status_code=500,
        )
    return resolution_response(resolution, event, replayed=True)


async def decide_legacy_lore_resolution(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    body: LegacyLoreResolutionInput,
) -> LegacyLoreResolutionResponse:
    request_payload = body.model_dump(exclude={"operation_key"})
    fingerprint = _request_fingerprint(project_id, user_id, "decide", request_payload)
    replay = await _event_by_key(db, project_id, user_id, body.operation_key)
    if replay is not None:
        return await _replay(db, replay, fingerprint)
    try:
        project, worldview = await _locked_project_worldview(db, project_id, user_id)
        preview, _ = await _preview(db, project, worldview)
        item = _validate_preview(project_id, preview, body)
        _validate_decision(preview, item, body)
        replay = await _event_by_key(db, project_id, user_id, body.operation_key)
        if replay is not None:
            return await _replay(db, replay, fingerprint)
        resolution = await db.scalar(
            select(LegacyLoreResolution).where(
                LegacyLoreResolution.project_id == project_id,
                LegacyLoreResolution.preview_schema_version == body.preview_schema_version,
                LegacyLoreResolution.mapping_version == body.mapping_version,
                LegacyLoreResolution.source_checksum == body.expected_source_checksum,
                LegacyLoreResolution.item_fingerprint == body.item_fingerprint,
                LegacyLoreResolution.reason_code == body.reason_code,
            ).with_for_update()
        )
        if resolution is None:
            if body.expected_resolution_version is not None:
                _error(
                    "LORE_MIGRATION_RESOLUTION_STALE",
                    "决定版本已经变化，请重新检查。",
                )
            resolution = LegacyLoreResolution(
                project_id=project_id,
                source_worldview_id=worldview.id,
                preview_schema_version=body.preview_schema_version,
                mapping_version=body.mapping_version,
                source_checksum=body.expected_source_checksum,
                semantic_result_checksum=body.expected_semantic_result_checksum,
                item_fingerprint=body.item_fingerprint,
                group_fingerprint=body.group_fingerprint,
                legacy_category=body.legacy_category,
                legacy_index=body.legacy_index,
                reason_code=body.reason_code,
                decision_code=body.decision_code,
                decision_payload=dict(body.decision_payload),
                status="active",
                lock_version=1,
                created_by=user_id,
                updated_by=user_id,
            )
            db.add(resolution)
            await db.flush()
            action = "decide"
            previous_status = None
            previous_decision: dict[str, Any] = {}
            previous_version = 0
        else:
            if body.expected_resolution_version != resolution.lock_version:
                _error(
                    "LORE_MIGRATION_RESOLUTION_STALE",
                    "决定版本已经变化，请重新检查。",
                )
            action = "replace"
            previous_status = resolution.status
            previous_decision = {
                "decision_code": resolution.decision_code,
                "decision_payload": dict(resolution.decision_payload or {}),
            }
            previous_version = resolution.lock_version
            resolution.semantic_result_checksum = body.expected_semantic_result_checksum
            resolution.group_fingerprint = body.group_fingerprint
            resolution.decision_code = body.decision_code
            resolution.decision_payload = dict(body.decision_payload)
            resolution.status = "active"
            resolution.lock_version += 1
            resolution.updated_by = user_id
        event = LegacyLoreResolutionEvent(
            project_id=project_id,
            resolution_id=resolution.id,
            performed_by=user_id,
            operation_key=body.operation_key,
            request_fingerprint=fingerprint,
            action=action,
            previous_status=previous_status,
            new_status="active",
            previous_decision=previous_decision,
            new_decision={
                "decision_code": body.decision_code,
                "decision_payload": dict(body.decision_payload),
            },
            previous_lock_version=previous_version,
            new_lock_version=resolution.lock_version,
        )
        db.add(event)
        _ensure_resolution_writes_available()
        await db.commit()
        return resolution_response(resolution, event, replayed=False)
    except IntegrityError as exc:
        await db.rollback()
        replay = await _event_by_key(db, project_id, user_id, body.operation_key)
        if replay is not None:
            return await _replay(db, replay, fingerprint)
        raise LegacyLoreResolutionError(
            "LORE_MIGRATION_RESOLUTION_CONCURRENT_CONFLICT",
            "并发决定发生冲突，请重新检查最新状态。",
        ) from exc
    except Exception:
        if db.in_transaction():
            await db.rollback()
        raise


async def revoke_legacy_lore_resolution(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    resolution_id: str,
    body: LegacyLoreResolutionRevokeInput,
) -> LegacyLoreResolutionResponse:
    request_payload = {**body.model_dump(exclude={"operation_key"}), "resolution_id": resolution_id}
    fingerprint = _request_fingerprint(project_id, user_id, "revoke", request_payload)
    replay = await _event_by_key(db, project_id, user_id, body.operation_key)
    if replay is not None:
        return await _replay(db, replay, fingerprint)
    try:
        _, worldview = await _locked_project_worldview(db, project_id, user_id)
        if migration_preview_source_checksum(worldview) != body.expected_source_checksum:
            _error(
                "LORE_MIGRATION_RESOLUTION_SOURCE_STALE",
                "旧资料已经变化，请重新检查后再撤销。",
            )
        resolution = await db.scalar(
            select(LegacyLoreResolution).where(
                LegacyLoreResolution.project_id == project_id,
                LegacyLoreResolution.id == resolution_id,
            ).with_for_update()
        )
        if resolution is None:
            _error("LORE_MIGRATION_RESOLUTION_NOT_FOUND", "决定不存在。", status_code=404)
        if (
            resolution.source_checksum != body.expected_source_checksum
            or resolution.lock_version != body.expected_resolution_version
            or resolution.status != "active"
        ):
            _error(
                "LORE_MIGRATION_RESOLUTION_STALE",
                "决定版本已经变化，请重新检查。",
            )
        previous_version = resolution.lock_version
        previous_decision = {
            "decision_code": resolution.decision_code,
            "decision_payload": dict(resolution.decision_payload or {}),
        }
        resolution.status = "revoked"
        resolution.lock_version += 1
        resolution.updated_by = user_id
        event = LegacyLoreResolutionEvent(
            project_id=project_id,
            resolution_id=resolution.id,
            performed_by=user_id,
            operation_key=body.operation_key,
            request_fingerprint=fingerprint,
            action="revoke",
            previous_status="active",
            new_status="revoked",
            previous_decision=previous_decision,
            new_decision=previous_decision,
            previous_lock_version=previous_version,
            new_lock_version=resolution.lock_version,
        )
        db.add(event)
        _ensure_resolution_writes_available()
        await db.commit()
        return resolution_response(resolution, event, replayed=False)
    except IntegrityError as exc:
        await db.rollback()
        replay = await _event_by_key(db, project_id, user_id, body.operation_key)
        if replay is not None:
            return await _replay(db, replay, fingerprint)
        raise LegacyLoreResolutionError(
            "LORE_MIGRATION_RESOLUTION_CONCURRENT_CONFLICT",
            "并发撤销发生冲突，请重新检查最新状态。",
        ) from exc
    except Exception:
        if db.in_transaction():
            await db.rollback()
        raise


async def list_legacy_lore_resolutions(
    db: AsyncSession,
    project_id: str,
    user_id: str,
) -> list[LegacyLoreResolutionSummary]:
    project = await db.scalar(select(Project).where(Project.id == project_id))
    if project is None:
        _error("LORE_MIGRATION_PROJECT_MISSING", "项目不存在。", status_code=404)
    if project.owner_id != user_id:
        _error("LORE_MIGRATION_RESOLUTION_FORBIDDEN", "无权查看此项目。", status_code=403)
    worldview = await db.scalar(select(Worldview).where(Worldview.project_id == project_id))
    rows = list((await db.scalars(
        select(LegacyLoreResolution)
        .where(LegacyLoreResolution.project_id == project_id)
        .order_by(LegacyLoreResolution.updated_at.desc(), LegacyLoreResolution.id)
    )).all())
    if worldview is None:
        return [_summary(row, status_override="expired") for row in rows]
    preview, _ = await _preview(db, project, worldview)
    preview_states = {
        state["id"]: state
        for item in preview["items"]
        for state in item.get("resolution_states", [])
    }
    summaries: list[LegacyLoreResolutionSummary] = []
    for row in rows:
        state = preview_states.get(row.id)
        if state is None:
            status_override = "expired" if row.status == "active" else row.status
            summaries.append(_summary(row, status_override=status_override))
        else:
            summaries.append(_summary(
                row,
                status_override=str(state["status"]),
                applies=bool(state.get("applies")),
            ))
    return summaries
