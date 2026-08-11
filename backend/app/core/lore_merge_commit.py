"""Transactional, non-destructive commit and audit for confirmed lore merges."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.lore_merge_preview import (
    build_merge_preview,
    decode_merge_preview_token,
    stable_merge_claims,
)
from app.core.lore_write import LoreWriteError, check_writes_available
from app.models.lore import (
    ElementRelation,
    ElementRelationVersion,
    ElementSource,
    ElementStateEvent,
    ElementVersion,
    LoreMergeOperation,
    LoreMergeRelationAction,
    LoreReviewSuggestion,
    SettingElement,
    SettingType,
)
from app.models.foreshadow import ForeshadowLifecycle
from app.models.project import Project, gen_id
from app.schemas.lore import (
    LoreMergeCommitInput,
    LoreMergeOperationResponse,
    LoreMergeRelationActionSummary,
)

_REQUEST_FINGERPRINT_VERSION = "lore-merge-commit:v1"


def merge_request_fingerprint(
    suggestion_id: str,
    body: LoreMergeCommitInput,
) -> str:
    payload = {
        "suggestion_id": suggestion_id,
        "preview_token": body.preview_token,
        "preview": body.preview.model_dump(mode="json"),
    }
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LoreWriteError(
            {
                "code": "LORE_MERGE_REQUEST_INVALID",
                "message": "合并请求无法生成稳定指纹",
            },
            status_code=422,
        ) from exc
    return hashlib.sha256(
        f"{_REQUEST_FINGERPRINT_VERSION}\n{canonical}".encode("utf-8")
    ).hexdigest()


async def find_merge_operation(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    operation_key: str,
) -> LoreMergeOperation | None:
    return await db.scalar(
        select(LoreMergeOperation).where(
            LoreMergeOperation.project_id == project_id,
            LoreMergeOperation.performed_by == user_id,
            LoreMergeOperation.operation_key == operation_key,
        )
    )


async def build_merge_operation_response(
    db: AsyncSession,
    operation: LoreMergeOperation,
    *,
    replayed: bool,
) -> LoreMergeOperationResponse:
    rows = await db.execute(
        select(LoreMergeRelationAction)
        .where(LoreMergeRelationAction.merge_operation_id == operation.id)
        .order_by(LoreMergeRelationAction.id)
    )
    actions = list(rows.scalars().all())
    return LoreMergeOperationResponse(
        id=operation.id,
        project_id=operation.project_id,
        operation_key=operation.operation_key,
        suggestion_id=operation.suggestion_id,
        evidence_revision=operation.evidence_revision,
        survivor_element_id=operation.survivor_element_id,
        merged_element_id=operation.merged_element_id,
        survivor_before_content_version=operation.survivor_before_content_version,
        survivor_before_lock_version=operation.survivor_before_lock_version,
        survivor_after_content_version=operation.survivor_after_content_version,
        survivor_after_lock_version=operation.survivor_after_lock_version,
        merged_before_content_version=operation.merged_before_content_version,
        merged_before_lock_version=operation.merged_before_lock_version,
        merged_after_lock_version=operation.merged_after_lock_version,
        selection_snapshot=dict(operation.selection_snapshot or {}),
        impact_summary=dict(operation.impact_summary or {}),
        relation_actions=[
            LoreMergeRelationActionSummary(
                id=action.id,
                relation_id=action.relation_id,
                retained_relation_id=action.retained_relation_id,
                action=action.action,
                before_snapshot=dict(action.before_snapshot or {}),
                after_snapshot=dict(action.after_snapshot or {}),
                previous_lock_version=action.previous_lock_version,
                new_lock_version=action.new_lock_version,
            )
            for action in actions
        ],
        created_at=operation.created_at,
        replayed=replayed,
    )


def _relation_snapshot(relation: ElementRelation) -> dict[str, Any]:
    return {
        "id": relation.id,
        "source_element_id": relation.source_element_id,
        "target_element_id": relation.target_element_id,
        "relation_key": relation.relation_key,
        "forward_label": relation.forward_label or "",
        "reverse_label": relation.reverse_label or "",
        "description": relation.description or "",
        "metadata": dict(relation.metadata_ or {}),
        "status": relation.status,
        "version_no": relation.version_no,
        "lock_version": relation.lock_version,
    }


def _add_relation_version(
    db: AsyncSession,
    relation: ElementRelation,
    user_id: str,
    reason: str,
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
            change_reason=reason,
            created_by=user_id,
        )
    )


def _idempotency_conflict() -> LoreWriteError:
    return LoreWriteError(
        {
            "code": "LORE_MERGE_OPERATION_KEY_REUSED",
            "message": "该操作标识已用于不同的合并请求",
            "retryable": False,
        },
        status_code=409,
    )


async def replay_merge_operation(
    db: AsyncSession,
    operation: LoreMergeOperation,
    request_fingerprint: str,
) -> LoreMergeOperationResponse:
    if operation.request_fingerprint != request_fingerprint:
        raise _idempotency_conflict()
    return await build_merge_operation_response(db, operation, replayed=True)


async def commit_lore_merge(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    suggestion_id: str,
    body: LoreMergeCommitInput,
) -> LoreMergeOperationResponse:
    """Commit a C5A plan under a fixed lock order and one transaction."""
    request_fingerprint = merge_request_fingerprint(suggestion_id, body)
    existing = await find_merge_operation(
        db,
        project_id=project_id,
        user_id=user_id,
        operation_key=body.operation_key,
    )
    if existing is not None:
        return await replay_merge_operation(db, existing, request_fingerprint)

    check_writes_available()
    claims = decode_merge_preview_token(body.preview_token)
    if (
        claims.get("sub") != user_id
        or claims.get("project_id") != project_id
        or claims.get("suggestion_id") != suggestion_id
    ):
        raise LoreWriteError(
            {
                "code": "LORE_MERGE_PREVIEW_SCOPE_INVALID",
                "message": "合并预览不属于当前用户、项目或线索",
            },
            status_code=409,
        )

    project = await db.scalar(
        select(Project).where(Project.id == project_id).with_for_update()
    )
    if project is None or project.lore_storage_mode != "relational":
        raise LoreWriteError("当前项目不能提交设定合并", status_code=409)

    suggestion = await db.scalar(
        select(LoreReviewSuggestion)
        .where(
            LoreReviewSuggestion.project_id == project_id,
            LoreReviewSuggestion.id == suggestion_id,
        )
        .with_for_update()
    )
    if suggestion is None:
        raise LoreWriteError("设定线索不存在", status_code=404)

    existing = await find_merge_operation(
        db,
        project_id=project_id,
        user_id=user_id,
        operation_key=body.operation_key,
    )
    if existing is not None:
        return await replay_merge_operation(db, existing, request_fingerprint)

    endpoint_ids = sorted(
        {body.preview.survivor_element_id, body.preview.merged_element_id}
    )
    rows = await db.execute(
        select(SettingElement)
        .where(
            SettingElement.project_id == project_id,
            SettingElement.id.in_(endpoint_ids),
        )
        .order_by(SettingElement.id)
        .with_for_update()
    )
    elements = {element.id: element for element in rows.scalars().all()}
    survivor = elements.get(body.preview.survivor_element_id)
    merged = elements.get(body.preview.merged_element_id)
    if survivor is None or merged is None:
        raise LoreWriteError("合并对象已变化，请重新预览", status_code=409)

    tracked_ids = list(
        (
            await db.scalars(
                select(ForeshadowLifecycle.element_id).where(
                    ForeshadowLifecycle.project_id == project_id,
                    ForeshadowLifecycle.element_id.in_(endpoint_ids),
                    ForeshadowLifecycle.status == "active",
                )
            )
        ).all()
    )
    if tracked_ids:
        raise LoreWriteError(
            {
                "code": "LORE_MERGE_ACTIVE_FORESHADOW",
                "message": "合并对象仍有活动伏笔生命周期，请先归档对应伏笔。",
                "element_ids": sorted(tracked_ids),
            },
            status_code=409,
        )

    setting_type = await db.scalar(
        select(SettingType)
        .where(
            SettingType.project_id == project_id,
            SettingType.id == survivor.type_id,
        )
        .with_for_update()
    )
    if setting_type is None:
        raise LoreWriteError("设定类型已不可用", status_code=409)

    source_rows = await db.execute(
        select(ElementSource)
        .where(
            ElementSource.project_id == project_id,
            ElementSource.element_id.in_(endpoint_ids),
        )
        .order_by(ElementSource.id)
        .with_for_update()
    )
    sources = list(source_rows.scalars().all())

    relation_rows = await db.execute(
        select(ElementRelation)
        .where(
            ElementRelation.project_id == project_id,
            or_(
                ElementRelation.source_element_id.in_(endpoint_ids),
                ElementRelation.target_element_id.in_(endpoint_ids),
            ),
        )
        .order_by(ElementRelation.id)
        .with_for_update()
    )
    relations = list(relation_rows.scalars().all())

    incoming_aliases = list(
        (
            await db.execute(
                select(SettingElement.id)
                .where(
                    SettingElement.project_id == project_id,
                    SettingElement.merged_into_element_id == merged.id,
                )
                .order_by(SettingElement.id)
                .with_for_update()
            )
        ).scalars().all()
    )
    if incoming_aliases:
        raise LoreWriteError(
            {
                "code": "LORE_MERGE_CHAIN_UNSUPPORTED",
                "message": "被合并项已有历史别名指向，当前版本不会自动形成合并链",
                "alias_ids": incoming_aliases,
            },
            status_code=409,
        )

    fresh_preview = await build_merge_preview(
        db,
        project_id=project_id,
        user_id=user_id,
        suggestion_id=suggestion_id,
        body=body.preview,
    )
    fresh_claims = decode_merge_preview_token(fresh_preview.preview_token)
    if stable_merge_claims(claims) != stable_merge_claims(fresh_claims):
        raise LoreWriteError(
            {
                "code": "LORE_MERGE_PREVIEW_STALE",
                "message": "设定类型、内容、来源或关系已变化，请重新预览",
                "retryable": False,
            },
            status_code=409,
        )
    if fresh_preview.blockers:
        raise LoreWriteError(
            {
                "code": "LORE_MERGE_RELATION_BLOCKED",
                "message": "关系处置仍有待确认项，不能提交合并",
                "blockers": fresh_preview.blockers,
            },
            status_code=409,
        )

    operation_id = gen_id()
    survivor_before_content = survivor.content_version
    survivor_before_lock = survivor.lock_version
    merged_before_content = merged.content_version
    merged_before_lock = merged.lock_version

    survivor.content_version += 1
    survivor.lock_version += 1
    survivor.name = fresh_preview.final_name.strip()
    survivor.normalized_name = survivor.name.casefold()
    survivor.summary = fresh_preview.final_summary
    survivor.payload = dict(fresh_preview.final_payload)
    survivor.field_states = dict(fresh_preview.final_field_states)
    survivor.payload_schema_revision = setting_type.schema_revision
    db.add(
        ElementVersion(
            element_id=survivor.id,
            version_no=survivor.content_version,
            type_id=survivor.type_id,
            type_schema_revision=setting_type.schema_revision,
            name=survivor.name,
            summary=survivor.summary,
            payload=dict(survivor.payload or {}),
            field_states=dict(survivor.field_states or {}),
            change_reason=f"合并设定 {merged.id}",
            created_by=user_id,
        )
    )

    relation_by_id = {relation.id: relation for relation in relations}
    for plan in fresh_preview.relation_plan:
        relation = relation_by_id[plan.relation_id]
        before = _relation_snapshot(relation)
        previous_lock = relation.lock_version
        if plan.action == "rewire":
            relation.source_element_id = plan.planned_source_element_id
            relation.target_element_id = plan.planned_target_element_id
            action = "rewired"
            reason = "合并设定时改连关系"
        elif plan.action == "exact_duplicate_archive":
            relation.status = "archived"
            action = "exact_duplicate_archived"
            reason = "合并设定时归档语义完全相同的重复关系"
        elif plan.action == "self_loop_archive":
            relation.status = "archived"
            action = "self_loop_archived"
            reason = "合并设定时归档重定向后的自指关系"
        else:
            raise LoreWriteError(
                {
                    "code": "LORE_MERGE_RELATION_BLOCKED",
                    "message": "关系计划包含未解决项，不能提交合并",
                },
                status_code=409,
            )
        relation.lock_version += 1
        relation.version_no += 1
        _add_relation_version(db, relation, user_id, reason)
        after = _relation_snapshot(relation)
        db.add(
            LoreMergeRelationAction(
                project_id=project_id,
                merge_operation_id=operation_id,
                relation_project_id=project_id,
                relation_id=relation.id,
                retained_relation_project_id=(
                    project_id if plan.retained_relation_id else None
                ),
                retained_relation_id=plan.retained_relation_id,
                action=action,
                before_snapshot=before,
                after_snapshot=after,
                previous_lock_version=previous_lock,
                new_lock_version=relation.lock_version,
            )
        )

    merged.lock_version += 1
    merged.enabled = False
    merged.lifecycle_status = "merged"
    merged.merged_into_element_id = survivor.id
    db.add_all(
        [
            ElementStateEvent(
                element_id=survivor.id,
                event_kind="merge",
                previous_lock_version=survivor_before_lock,
                new_lock_version=survivor.lock_version,
                performed_by=user_id,
                metadata_={
                    "role": "survivor",
                    "other_element_id": merged.id,
                    "merge_operation_id": operation_id,
                },
            ),
            ElementStateEvent(
                element_id=merged.id,
                event_kind="merge",
                previous_lock_version=merged_before_lock,
                new_lock_version=merged.lock_version,
                performed_by=user_id,
                metadata_={
                    "role": "merged",
                    "other_element_id": survivor.id,
                    "merge_operation_id": operation_id,
                },
            ),
        ]
    )

    suggestion_before_lock = suggestion.lock_version
    suggestion.detection_state = "stale"
    suggestion.lock_version += 1

    source_ids = {
        element_id: sorted(
            source.id for source in sources if source.element_id == element_id
        )
        for element_id in endpoint_ids
    }
    impact_summary = {
        "element_names": {
            "survivor": fresh_preview.survivor.name,
            "merged": fresh_preview.merged.name,
        },
        "type_anchor": {
            "type_id": setting_type.id,
            "status": setting_type.status,
            "schema_revision": setting_type.schema_revision,
            "field_schema_fingerprint": fresh_claims["field_schema_fingerprint"],
            "survivor_payload_schema_revision": fresh_claims[
                "survivor_payload_schema_revision"
            ],
            "merged_payload_schema_revision": fresh_claims[
                "merged_payload_schema_revision"
            ],
        },
        "suggestion_before_lock_version": suggestion_before_lock,
        "suggestion_after_lock_version": suggestion.lock_version,
        "source_ids": source_ids,
        "source_impact": fresh_preview.source_impact.model_dump(mode="json"),
        "final_content": {
            "name": fresh_preview.final_name,
            "summary": fresh_preview.final_summary,
            "payload": fresh_preview.final_payload,
            "field_states": fresh_preview.final_field_states,
        },
        "would_be_generation_eligible": fresh_preview.would_be_generation_eligible,
        "relation_plan": [
            plan.model_dump(mode="json") for plan in fresh_preview.relation_plan
        ],
        "merged_after_content_version": merged.content_version,
        "physical_deletions": 0,
    }
    operation = LoreMergeOperation(
        id=operation_id,
        project_id=project_id,
        performed_by=user_id,
        operation_key=body.operation_key,
        request_fingerprint=request_fingerprint,
        suggestion_project_id=project_id,
        suggestion_id=suggestion.id,
        evidence_revision=suggestion.evidence_revision,
        survivor_element_id=survivor.id,
        merged_element_id=merged.id,
        survivor_before_content_version=survivor_before_content,
        survivor_before_lock_version=survivor_before_lock,
        merged_before_content_version=merged_before_content,
        merged_before_lock_version=merged_before_lock,
        source_fingerprint=fresh_claims["source_fingerprint"],
        relation_fingerprint=fresh_claims["relation_fingerprint"],
        selection_snapshot=dict(fresh_preview.selection_snapshot),
        plan_fingerprint=fresh_claims["plan_fingerprint"],
        impact_summary=impact_summary,
        survivor_after_content_version=survivor.content_version,
        survivor_after_lock_version=survivor.lock_version,
        merged_after_lock_version=merged.lock_version,
    )
    db.add(operation)
    await db.flush()
    check_writes_available()
    await db.commit()
    return await build_merge_operation_response(db, operation, replayed=False)


async def list_element_merge_history(
    db: AsyncSession,
    *,
    project_id: str,
    element_id: str,
) -> list[LoreMergeOperationResponse]:
    rows = await db.execute(
        select(LoreMergeOperation)
        .where(
            LoreMergeOperation.project_id == project_id,
            or_(
                LoreMergeOperation.survivor_element_id == element_id,
                LoreMergeOperation.merged_element_id == element_id,
            ),
        )
        .order_by(LoreMergeOperation.created_at.desc(), LoreMergeOperation.id.desc())
    )
    return [
        await build_merge_operation_response(db, operation, replayed=False)
        for operation in rows.scalars().all()
    ]
