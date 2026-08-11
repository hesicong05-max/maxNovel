"""Exactly-once writes and strict reads for relational foreshadow lifecycles."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import hashlib
import json
import logging
from typing import Any
import uuid

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.maintenance import ensure_project_writes_available
from app.models.foreshadow import (
    ForeshadowFact,
    ForeshadowLifecycle,
    ForeshadowLifecycleEvent,
    ForeshadowOperation,
    ForeshadowPlanItem,
)
from app.models.lore import SettingElement, SettingType
from app.models.planning import NovelPlan, PlanningChapter, PlanningPart
from app.models.project import Project
from app.schemas.foreshadow import (
    ForeshadowElementSnapshot,
    ForeshadowEventResponse,
    ForeshadowFactResponse,
    ForeshadowLifecycleResponse,
    ForeshadowMutationReceipt,
    ForeshadowPlanItemResponse,
    ForeshadowTargetSnapshot,
)


_FINGERPRINT_VERSION = "foreshadow-lifecycle-v1"


class ForeshadowWriteError(Exception):
    """Stable API error that never leaks database details."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        retryable: bool = False,
        recommended_action: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = {
            "code": code,
            "message": message,
            "retryable": retryable,
            "recommended_action": recommended_action,
            **(extra or {}),
        }


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def operation_fingerprint(
    project_id: str,
    operation_type: str,
    lifecycle_id: str | None,
    payload: dict[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "version": _FINGERPRINT_VERSION,
            "project_id": project_id,
            "operation_type": operation_type,
            "lifecycle_id": lifecycle_id,
            "payload": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def find_operation(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    operation_key: str,
) -> ForeshadowOperation | None:
    return await db.scalar(
        select(ForeshadowOperation).where(
            ForeshadowOperation.project_id == project_id,
            ForeshadowOperation.requested_by == user_id,
            ForeshadowOperation.operation_key == operation_key,
        )
    )


def replay_operation(
    operation: ForeshadowOperation,
    request_fingerprint: str | None = None,
) -> dict[str, Any]:
    if request_fingerprint and operation.request_fingerprint != request_fingerprint:
        raise ForeshadowWriteError(
            "FORESHADOW_OPERATION_KEY_REUSED",
            "该操作编号已用于不同请求，系统没有重复写入。",
            recommended_action="retry_with_new_operation_key",
        )
    try:
        receipt = ForeshadowMutationReceipt.model_validate(operation.result_snapshot)
    except ValidationError as exc:
        raise ForeshadowWriteError(
            "FORESHADOW_OPERATION_CORRUPT",
            "伏笔操作记录不完整，系统已停止自动处理。",
            recommended_action="contact_support",
        ) from exc
    if (
        receipt.receipt_id != operation.id
        or receipt.project_id != operation.project_id
        or receipt.lifecycle_id != operation.lifecycle_id
        or receipt.event_id != operation.event_id
        or receipt.operation_key != operation.operation_key
        or receipt.operation_type != operation.operation_type
        or receipt.lifecycle.id != receipt.lifecycle_id
        or receipt.lifecycle.project_id != receipt.project_id
        or receipt.lifecycle.lock_version != receipt.new_lifecycle_version
    ):
        raise ForeshadowWriteError(
            "FORESHADOW_OPERATION_CORRUPT",
            "伏笔操作记录与收据不一致，系统已停止自动处理。",
            recommended_action="contact_support",
        )
    snapshot = receipt.model_dump(mode="json")
    snapshot["replayed"] = True
    return snapshot


async def lock_context(
    db: AsyncSession,
    project_id: str,
    user_id: str,
) -> tuple[Project, NovelPlan]:
    project = await db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update(read=True, key_share=True)
    )
    if project is None:
        raise ForeshadowWriteError(
            "FORESHADOW_PROJECT_NOT_FOUND",
            "项目不存在。",
            status_code=404,
            recommended_action="return_to_projects",
        )
    if project.owner_id != user_id:
        raise ForeshadowWriteError(
            "FORESHADOW_PROJECT_FORBIDDEN",
            "无权操作此项目。",
            status_code=403,
            recommended_action="return_to_projects",
        )
    plan = await db.scalar(
        select(NovelPlan)
        .where(NovelPlan.project_id == project_id)
        .with_for_update()
    )
    if plan is None:
        raise ForeshadowWriteError(
            "FORESHADOW_PLANNING_NOT_INITIALIZED",
            "章节规划尚未创建。",
            status_code=404,
            recommended_action="initialize_planning",
        )
    if plan.status != "active":
        raise ForeshadowWriteError(
            "FORESHADOW_PLAN_ARCHIVED",
            "章节规划当前不可编辑。",
            recommended_action="return_to_project",
        )
    if project.lore_storage_mode != "relational":
        raise ForeshadowWriteError(
            "FORESHADOW_LORE_MIGRATION_REQUIRED",
            "请先将旧世界观安全升级为设定仓库。",
            recommended_action="open_lore_repository",
        )
    return project, plan


async def lock_lifecycle(
    db: AsyncSession,
    project_id: str,
    lifecycle_id: str,
) -> ForeshadowLifecycle:
    lifecycle = await db.scalar(
        select(ForeshadowLifecycle)
        .where(
            ForeshadowLifecycle.project_id == project_id,
            ForeshadowLifecycle.id == lifecycle_id,
        )
        .with_for_update()
    )
    if lifecycle is None:
        raise ForeshadowWriteError(
            "FORESHADOW_NOT_FOUND",
            "伏笔不存在或不属于当前项目。",
            status_code=404,
            recommended_action="refresh_foreshadows",
        )
    return lifecycle


def require_lifecycle_version(
    lifecycle: ForeshadowLifecycle, expected_version: int
) -> None:
    if lifecycle.lock_version != expected_version:
        raise ForeshadowWriteError(
            "FORESHADOW_VERSION_CONFLICT",
            "伏笔已被其他操作更新，请刷新后核对。",
            recommended_action="refresh_foreshadow",
            extra={"current_lifecycle_version": lifecycle.lock_version},
        )


def require_active_lifecycle(lifecycle: ForeshadowLifecycle) -> None:
    if lifecycle.status != "active":
        raise ForeshadowWriteError(
            "FORESHADOW_ARCHIVED",
            "伏笔已归档，不能新增或修改计划与事实。",
            recommended_action="restore_foreshadow",
        )


def require_structure_version(plan: NovelPlan, expected_version: int) -> None:
    if plan.structure_version != expected_version:
        raise ForeshadowWriteError(
            "FORESHADOW_STRUCTURE_VERSION_CONFLICT",
            "章节结构已变化，请刷新后核对目标。",
            recommended_action="refresh_planning",
            extra={"current_structure_version": plan.structure_version},
        )


async def load_eligible_element(
    db: AsyncSession,
    project_id: str,
    element_id: str,
    expected_lock_version: int | None,
    *,
    for_update: bool = True,
) -> SettingElement:
    query = select(SettingElement).where(
        SettingElement.project_id == project_id,
        SettingElement.id == element_id,
    )
    if for_update:
        query = query.with_for_update()
    element = await db.scalar(query)
    if element is None:
        raise ForeshadowWriteError(
            "FORESHADOW_ELEMENT_NOT_FOUND",
            "伏笔设定不存在或不属于当前项目。",
            status_code=404,
            recommended_action="open_lore_repository",
        )
    if (
        expected_lock_version is not None
        and element.lock_version != expected_lock_version
    ):
        raise ForeshadowWriteError(
            "FORESHADOW_ELEMENT_VERSION_CONFLICT",
            "伏笔设定已变化，请刷新后核对。",
            recommended_action="refresh_lore_element",
            extra={"current_element_lock_version": element.lock_version},
        )
    setting_type = await db.scalar(
        select(SettingType).where(
            SettingType.project_id == project_id,
            SettingType.id == element.type_id,
        )
    )
    if setting_type is None or setting_type.key != "foreshadow":
        raise ForeshadowWriteError(
            "FORESHADOW_ELEMENT_TYPE_INVALID",
            "只能跟踪伏笔类型的设定模块。",
            recommended_action="select_foreshadow_element",
        )
    reasons: list[str] = []
    if setting_type.status != "active":
        reasons.append("type_archived")
    if element.confirmation_status != "confirmed":
        reasons.append("element_not_confirmed")
    if element.lifecycle_status != "active":
        reasons.append("element_not_active")
    if not element.enabled:
        reasons.append("element_disabled")
    if reasons:
        raise ForeshadowWriteError(
            "FORESHADOW_ELEMENT_INELIGIBLE",
            "该设定当前不能用于伏笔规划。",
            recommended_action="review_lore_element",
            extra={"reasons": reasons},
        )
    return element


async def load_active_target(
    db: AsyncSession,
    project_id: str,
    plan_id: str,
    target_type: str,
    target_id: str,
    expected_lock_version: int,
) -> PlanningPart | PlanningChapter:
    model = PlanningPart if target_type == "part" else PlanningChapter
    target = await db.scalar(
        select(model).where(
            model.project_id == project_id,
            model.plan_id == plan_id,
            model.id == target_id,
        )
    )
    if target is None:
        raise ForeshadowWriteError(
            "FORESHADOW_TARGET_NOT_FOUND",
            "计划目标不存在或不属于当前项目。",
            status_code=404,
            recommended_action="refresh_planning",
        )
    if target.lock_version != expected_lock_version:
        raise ForeshadowWriteError(
            "FORESHADOW_TARGET_VERSION_CONFLICT",
            "计划目标已变化，请刷新后核对。",
            recommended_action="refresh_planning",
            extra={"current_target_lock_version": target.lock_version},
        )
    if target.status != "active":
        raise ForeshadowWriteError(
            "FORESHADOW_TARGET_ARCHIVED",
            "计划目标已归档。",
            recommended_action="select_active_target",
        )
    return target


def derived_state(
    plans: list[ForeshadowPlanItem], facts: list[ForeshadowFact]
) -> str:
    active_facts = {item.fact_kind for item in facts if item.status == "active"}
    if "resolved" in active_facts:
        return "resolved"
    if "planted" not in active_facts:
        return "unplanted"
    if any(
        item.status == "active" and item.action_kind == "resolve" for item in plans
    ):
        return "pending_resolution"
    return "planted"


def _element_snapshot(element: SettingElement) -> ForeshadowElementSnapshot:
    return ForeshadowElementSnapshot(
        id=element.id,
        name=element.name,
        summary=element.summary or "",
        confirmation_status=element.confirmation_status,
        lifecycle_status=element.lifecycle_status,
        enabled=element.enabled,
        content_version=element.content_version,
        lock_version=element.lock_version,
    )


def _target_snapshot(
    target: PlanningPart | PlanningChapter,
) -> ForeshadowTargetSnapshot:
    return ForeshadowTargetSnapshot(
        target_type="part" if isinstance(target, PlanningPart) else "chapter",
        target_id=target.id,
        title=target.title,
        status=target.status,
        part_id=getattr(target, "part_id", None),
        position=target.position,
    )


async def lifecycle_response(
    db: AsyncSession, lifecycle: ForeshadowLifecycle
) -> ForeshadowLifecycleResponse:
    element = await db.scalar(
        select(SettingElement).where(SettingElement.id == lifecycle.element_id)
    )
    if element is None:
        raise ForeshadowWriteError(
            "FORESHADOW_ELEMENT_MISSING",
            "伏笔引用的设定不存在，系统已停止处理。",
            recommended_action="contact_support",
        )
    plans = list(
        (
            await db.scalars(
                select(ForeshadowPlanItem)
                .where(ForeshadowPlanItem.lifecycle_id == lifecycle.id)
                .order_by(ForeshadowPlanItem.created_at, ForeshadowPlanItem.id)
            )
        ).all()
    )
    facts = list(
        (
            await db.scalars(
                select(ForeshadowFact)
                .where(ForeshadowFact.lifecycle_id == lifecycle.id)
                .order_by(ForeshadowFact.created_at, ForeshadowFact.id)
            )
        ).all()
    )
    part_ids = {item.part_id for item in plans if item.part_id}
    chapter_ids = {item.chapter_id for item in plans if item.chapter_id} | {
        item.chapter_id for item in facts
    }
    parts = {
        item.id: item
        for item in (
            await db.scalars(select(PlanningPart).where(PlanningPart.id.in_(part_ids)))
            if part_ids
            else []
        )
    }
    chapters = {
        item.id: item
        for item in (
            await db.scalars(
                select(PlanningChapter).where(PlanningChapter.id.in_(chapter_ids))
            )
            if chapter_ids
            else []
        )
    }
    plan_models: list[ForeshadowPlanItemResponse] = []
    for item in plans:
        target = parts.get(item.part_id) if item.part_id else chapters.get(item.chapter_id)
        if target is None:
            raise ForeshadowWriteError(
                "FORESHADOW_TARGET_MISSING",
                "伏笔计划引用的章节结构不存在，系统已停止处理。",
                recommended_action="contact_support",
            )
        plan_models.append(
            ForeshadowPlanItemResponse(
                id=item.id,
                action_kind=item.action_kind,
                target=_target_snapshot(target),
                condition_text=item.condition_text,
                note=item.note,
                status=item.status,
                lock_version=item.lock_version,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
        )
    fact_models: list[ForeshadowFactResponse] = []
    for item in facts:
        chapter = chapters.get(item.chapter_id)
        if chapter is None:
            raise ForeshadowWriteError(
                "FORESHADOW_CHAPTER_MISSING",
                "伏笔事实引用的章节不存在，系统已停止处理。",
                recommended_action="contact_support",
            )
        fact_models.append(
            ForeshadowFactResponse(
                id=item.id,
                fact_kind=item.fact_kind,
                chapter=_target_snapshot(chapter),
                note=item.note,
                status=item.status,
                lock_version=item.lock_version,
                created_at=item.created_at,
                retracted_at=item.retracted_at,
            )
        )
    return ForeshadowLifecycleResponse(
        id=lifecycle.id,
        project_id=lifecycle.project_id,
        plan_id=lifecycle.plan_id,
        status=lifecycle.status,
        state=derived_state(plans, facts),
        lock_version=lifecycle.lock_version,
        element=_element_snapshot(element),
        plans=plan_models,
        facts=fact_models,
        created_at=lifecycle.created_at,
        updated_at=lifecycle.updated_at,
    )


async def history_response(
    db: AsyncSession, lifecycle_id: str
) -> list[ForeshadowEventResponse]:
    events = list(
        (
            await db.scalars(
                select(ForeshadowLifecycleEvent)
                .where(ForeshadowLifecycleEvent.lifecycle_id == lifecycle_id)
                .order_by(
                    ForeshadowLifecycleEvent.created_at,
                    ForeshadowLifecycleEvent.id,
                )
            )
        ).all()
    )
    return [
        ForeshadowEventResponse(
            id=item.id,
            event_kind=item.event_kind,
            plan_item_id=item.plan_item_id,
            fact_id=item.fact_id,
            previous_lifecycle_version=item.previous_lifecycle_version,
            new_lifecycle_version=item.new_lifecycle_version,
            metadata=item.metadata_json or {},
            created_at=item.created_at,
        )
        for item in events
    ]


Mutation = Callable[
    [Project, NovelPlan],
    Awaitable[tuple[ForeshadowLifecycle, int, ForeshadowLifecycleEvent]],
]


async def execute_operation(
    *,
    db: AsyncSession,
    project_id: str,
    user_id: str,
    operation_key: str,
    operation_type: str,
    lifecycle_id: str | None,
    fingerprint_payload: dict[str, Any],
    mutate: Mutation,
) -> dict[str, Any]:
    fingerprint = operation_fingerprint(
        project_id, operation_type, lifecycle_id, fingerprint_payload
    )
    existing = await find_operation(db, project_id, user_id, operation_key)
    if existing is not None:
        return replay_operation(existing, fingerprint)
    try:
        ensure_project_writes_available()
        project, plan = await lock_context(db, project_id, user_id)
        existing = await find_operation(db, project_id, user_id, operation_key)
        if existing is not None:
            replayed = replay_operation(existing, fingerprint)
            await db.rollback()
            return replayed
        lifecycle, previous_version, event = await mutate(project, plan)
        await db.flush()
        response = await lifecycle_response(db, lifecycle)
        operation = ForeshadowOperation(
            id=uuid.uuid4().hex,
            project_id=project_id,
            requested_by=user_id,
            operation_key=operation_key,
            operation_type=operation_type,
            request_fingerprint=fingerprint,
            lifecycle_id=lifecycle.id,
            event_id=event.id,
            result_snapshot={},
            created_at=utcnow(),
        )
        receipt = ForeshadowMutationReceipt(
            receipt_id=operation.id,
            operation_key=operation_key,
            operation_type=operation_type,
            replayed=False,
            project_id=project_id,
            lifecycle_id=lifecycle.id,
            previous_lifecycle_version=previous_version,
            new_lifecycle_version=lifecycle.lock_version,
            event_id=event.id,
            lifecycle=response,
            created_at=operation.created_at,
        )
        operation.result_snapshot = receipt.model_dump(mode="json")
        db.add(operation)
        await db.flush()
        ensure_project_writes_available()
        await db.commit()
        return operation.result_snapshot
    except ForeshadowWriteError:
        await db.rollback()
        raise
    except IntegrityError as exc:
        logging.getLogger(__name__).debug(
            "Foreshadow write constraint conflict for %s: %s",
            operation_type,
            exc.orig,
        )
        await db.rollback()
        existing = await find_operation(db, project_id, user_id, operation_key)
        if existing is not None:
            return replay_operation(existing, fingerprint)
        raise ForeshadowWriteError(
            "FORESHADOW_WRITE_CONFLICT",
            "伏笔发生并发冲突，请刷新后安全重试。",
            retryable=True,
            recommended_action="refresh_foreshadow",
        ) from exc
    except Exception:
        await db.rollback()
        raise
