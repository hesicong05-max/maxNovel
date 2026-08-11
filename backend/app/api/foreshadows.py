"""Durable foreshadow lifecycle API for chapter planning."""

from collections.abc import Awaitable
from typing import Annotated, Any, Literal, TypeVar
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import User, get_current_user, get_project_for_owner
from app.core.foreshadow_lifecycle import (
    ForeshadowWriteError,
    derived_state,
    execute_operation,
    find_operation,
    history_response,
    lifecycle_response,
    load_active_target,
    load_eligible_element,
    lock_lifecycle,
    replay_operation,
    require_active_lifecycle,
    require_lifecycle_version,
    require_structure_version,
    utcnow,
)
from app.database import get_db
from app.models.foreshadow import (
    ForeshadowFact,
    ForeshadowLifecycle,
    ForeshadowLifecycleEvent,
    ForeshadowPlanItem,
)
from app.models.planning import NovelPlan, PlanningChapter, PlanningPart
from app.schemas.foreshadow import (
    ForeshadowBindCommand,
    ForeshadowFactCreate,
    ForeshadowFactRetract,
    ForeshadowHistoryResponse,
    ForeshadowLifecycleCommand,
    ForeshadowLifecycleListResponse,
    ForeshadowLifecycleResponse,
    ForeshadowMutationReceipt,
    ForeshadowPlanCommand,
    ForeshadowPlanCreate,
    ForeshadowRestoreCommand,
    ForeshadowStateCounts,
)


router = APIRouter(
    prefix="/api/projects/{project_id}/planning/foreshadows",
    tags=["foreshadows"],
)
_ReadResult = TypeVar("_ReadResult")


async def _run_write(**kwargs: Any) -> dict[str, Any]:
    try:
        return await execute_operation(**kwargs)
    except ForeshadowWriteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def _run_read(operation: Awaitable[_ReadResult]) -> _ReadResult:
    try:
        return await operation
    except ForeshadowWriteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def _owned_lifecycle(
    db: AsyncSession, project_id: str, lifecycle_id: str
) -> ForeshadowLifecycle:
    lifecycle = await db.scalar(
        select(ForeshadowLifecycle).where(
            ForeshadowLifecycle.project_id == project_id,
            ForeshadowLifecycle.id == lifecycle_id,
        )
    )
    if lifecycle is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FORESHADOW_NOT_FOUND",
                "message": "伏笔不存在或不属于当前项目。",
                "retryable": False,
                "recommended_action": "refresh_foreshadows",
            },
        )
    return lifecycle


def _event(
    *,
    lifecycle: ForeshadowLifecycle,
    user_id: str,
    event_kind: str,
    previous_version: int,
    plan_item_id: str | None = None,
    fact_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ForeshadowLifecycleEvent:
    return ForeshadowLifecycleEvent(
        id=uuid.uuid4().hex,
        project_id=lifecycle.project_id,
        lifecycle_id=lifecycle.id,
        performed_by=user_id,
        event_kind=event_kind,
        plan_item_id=plan_item_id,
        fact_id=fact_id,
        previous_lifecycle_version=previous_version,
        new_lifecycle_version=lifecycle.lock_version,
        metadata_json=metadata or {},
        created_at=utcnow(),
    )


async def _target_order(
    db: AsyncSession,
    target: PlanningPart | PlanningChapter,
) -> tuple[int, int]:
    if isinstance(target, PlanningPart):
        return target.position, 0
    part_position = await db.scalar(
        select(PlanningPart.position).where(PlanningPart.id == target.part_id)
    )
    if part_position is None:
        raise ForeshadowWriteError(
            "FORESHADOW_TARGET_MISSING",
            "章节所属篇章不存在，系统已停止写入。",
            recommended_action="contact_support",
        )
    return int(part_position), target.position


async def _plan_target(
    db: AsyncSession, item: ForeshadowPlanItem
) -> PlanningPart | PlanningChapter:
    model = PlanningPart if item.target_type == "part" else PlanningChapter
    target = await db.scalar(select(model).where(model.id == item.target_id))
    if target is None:
        raise ForeshadowWriteError(
            "FORESHADOW_TARGET_MISSING",
            "已有伏笔计划引用的目标不存在，系统已停止写入。",
            recommended_action="contact_support",
        )
    return target


async def _active_plan(
    db: AsyncSession, lifecycle_id: str, action_kind: str
) -> ForeshadowPlanItem | None:
    return await db.scalar(
        select(ForeshadowPlanItem).where(
            ForeshadowPlanItem.lifecycle_id == lifecycle_id,
            ForeshadowPlanItem.action_kind == action_kind,
            ForeshadowPlanItem.status == "active",
        )
    )


async def _validate_plan_order(
    db: AsyncSession,
    lifecycle_id: str,
    action_kind: str,
    target: PlanningPart | PlanningChapter,
) -> None:
    other_kind = "resolve" if action_kind == "plant" else "plant"
    other = await _active_plan(db, lifecycle_id, other_kind)
    if other is None:
        return
    current_order = await _target_order(db, target)
    other_order = await _target_order(db, await _plan_target(db, other))
    plant_order, resolve_order = (
        (current_order, other_order)
        if action_kind == "plant"
        else (other_order, current_order)
    )
    if plant_order >= resolve_order:
        raise ForeshadowWriteError(
            "FORESHADOW_PLAN_ORDER_INVALID",
            "计划回收位置必须晚于埋入位置。",
            recommended_action="select_later_resolve_target",
        )


async def _active_fact(
    db: AsyncSession, lifecycle_id: str, fact_kind: str
) -> ForeshadowFact | None:
    return await db.scalar(
        select(ForeshadowFact).where(
            ForeshadowFact.lifecycle_id == lifecycle_id,
            ForeshadowFact.fact_kind == fact_kind,
            ForeshadowFact.status == "active",
        )
    )


async def _validate_resolve_target_after_planted_fact(
    db: AsyncSession,
    lifecycle_id: str,
    resolve_target: PlanningPart | PlanningChapter,
) -> None:
    planted = await _active_fact(db, lifecycle_id, "planted")
    if planted is None:
        return
    planted_chapter = await db.scalar(
        select(PlanningChapter).where(PlanningChapter.id == planted.chapter_id)
    )
    if planted_chapter is None:
        raise ForeshadowWriteError(
            "FORESHADOW_CHAPTER_MISSING",
            "埋入事实引用的章节不存在，系统已停止写入。",
            recommended_action="contact_support",
        )
    if await _chapter_order(db, planted_chapter) >= await _target_order(
        db, resolve_target
    ):
        raise ForeshadowWriteError(
            "FORESHADOW_PLAN_ORDER_INVALID",
            "计划回收位置必须晚于实际埋入章节。",
            recommended_action="select_later_resolve_target",
        )


async def _validate_lifecycle_restore(
    db: AsyncSession, lifecycle: ForeshadowLifecycle
) -> None:
    plans = list(
        (
            await db.scalars(
                select(ForeshadowPlanItem).where(
                    ForeshadowPlanItem.lifecycle_id == lifecycle.id,
                    ForeshadowPlanItem.status == "active",
                )
            )
        ).all()
    )
    facts = list(
        (
            await db.scalars(
                select(ForeshadowFact).where(
                    ForeshadowFact.lifecycle_id == lifecycle.id,
                    ForeshadowFact.status == "active",
                )
            )
        ).all()
    )
    plan_targets: dict[str, PlanningPart | PlanningChapter] = {}
    for item in plans:
        target = await _plan_target(db, item)
        if target.status != "active":
            raise ForeshadowWriteError(
                "FORESHADOW_TARGET_ARCHIVED",
                "已有伏笔计划的目标已归档，不能恢复伏笔。",
                recommended_action="restore_or_replace_target",
                extra={"target_id": item.target_id},
            )
        plan_targets[item.action_kind] = target
    if set(plan_targets) == {"plant", "resolve"} and await _target_order(
        db, plan_targets["plant"]
    ) >= await _target_order(db, plan_targets["resolve"]):
        raise ForeshadowWriteError(
            "FORESHADOW_PLAN_ORDER_INVALID",
            "已有回收计划不晚于埋入计划，不能恢复伏笔。",
            recommended_action="adjust_foreshadow_plan",
        )
    if "resolve" in plan_targets:
        await _validate_resolve_target_after_planted_fact(
            db, lifecycle.id, plan_targets["resolve"]
        )
    fact_chapters: dict[str, PlanningChapter] = {}
    for item in facts:
        chapter = await db.scalar(
            select(PlanningChapter).where(PlanningChapter.id == item.chapter_id)
        )
        if chapter is None or chapter.status != "active":
            raise ForeshadowWriteError(
                "FORESHADOW_TARGET_ARCHIVED",
                "已有作者确认事实的章节已归档，不能恢复伏笔。",
                recommended_action="restore_target_chapter",
                extra={"chapter_id": item.chapter_id},
            )
        fact_chapters[item.fact_kind] = chapter
    if "resolved" in fact_chapters and "planted" not in fact_chapters:
        raise ForeshadowWriteError(
            "FORESHADOW_FACT_SEQUENCE_INVALID",
            "回收事实缺少有效的埋入事实，不能恢复伏笔。",
            recommended_action="review_foreshadow_history",
        )
    if set(fact_chapters) == {"planted", "resolved"} and await _chapter_order(
        db, fact_chapters["planted"]
    ) >= await _chapter_order(db, fact_chapters["resolved"]):
        raise ForeshadowWriteError(
            "FORESHADOW_FACT_ORDER_INVALID",
            "回收事实不晚于埋入事实，不能恢复伏笔。",
            recommended_action="review_foreshadow_history",
        )


async def _chapter_order(
    db: AsyncSession, chapter: PlanningChapter
) -> tuple[int, int]:
    return await _target_order(db, chapter)


@router.get("", response_model=ForeshadowLifecycleListResponse)
async def list_foreshadows(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    status: Literal["active", "archived"] = "active",
    state: Literal[
        "unplanted", "planted", "pending_resolution", "resolved"
    ] | None = None,
    after: str | None = Query(default=None, min_length=32, max_length=32),
    limit: int = Query(default=50, ge=1, le=100),
):
    await get_project_for_owner(project_id, current_user, db)
    all_items = list(
        (
            await db.scalars(
                select(ForeshadowLifecycle)
                .where(
                    ForeshadowLifecycle.project_id == project_id,
                    ForeshadowLifecycle.status == status,
                )
                .order_by(ForeshadowLifecycle.id)
            )
        ).all()
    )
    responses = [
        await _run_read(lifecycle_response(db, item)) for item in all_items
    ]
    counts_raw = {
        "unplanted": 0,
        "planted": 0,
        "pending_resolution": 0,
        "resolved": 0,
    }
    for item in responses:
        counts_raw[item.state] += 1
    filtered = [item for item in responses if state is None or item.state == state]
    if after is not None:
        filtered = [item for item in filtered if item.id > after]
    page = filtered[: limit + 1]
    next_cursor = page[limit - 1].id if len(page) > limit else None
    return ForeshadowLifecycleListResponse(
        items=page[:limit],
        counts=ForeshadowStateCounts(**counts_raw),
        next_cursor=next_cursor,
    )


@router.get("/operations/by-key/{operation_key}", response_model=ForeshadowMutationReceipt)
async def recover_operation(
    project_id: str,
    operation_key: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    operation = await find_operation(db, project_id, current_user.id, operation_key)
    if operation is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FORESHADOW_OPERATION_NOT_FOUND",
                "message": "没有找到该伏笔操作记录。",
                "retryable": True,
                "recommended_action": "retry_original_operation",
            },
        )
    try:
        return replay_operation(operation)
    except ForeshadowWriteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/{lifecycle_id}", response_model=ForeshadowLifecycleResponse)
async def get_foreshadow(
    project_id: str,
    lifecycle_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    return await _run_read(
        lifecycle_response(
            db, await _owned_lifecycle(db, project_id, lifecycle_id)
        )
    )


@router.get("/{lifecycle_id}/history", response_model=ForeshadowHistoryResponse)
async def get_foreshadow_history(
    project_id: str,
    lifecycle_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    await _owned_lifecycle(db, project_id, lifecycle_id)
    return ForeshadowHistoryResponse(
        lifecycle_id=lifecycle_id,
        items=await _run_read(history_response(db, lifecycle_id)),
    )


@router.post("", response_model=ForeshadowMutationReceipt)
async def bind_foreshadow(
    project_id: str,
    body: ForeshadowBindCommand,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    async def mutate(_project, plan):
        require_structure_version(plan, body.expected_structure_version)
        element = await load_eligible_element(
            db,
            project_id,
            body.element_id,
            body.expected_element_lock_version,
        )
        existing = await db.scalar(
            select(ForeshadowLifecycle).where(
                ForeshadowLifecycle.project_id == project_id,
                ForeshadowLifecycle.element_id == body.element_id,
            )
        )
        if existing is not None:
            raise ForeshadowWriteError(
                "FORESHADOW_ALREADY_TRACKED",
                "该伏笔设定已经加入管理。",
                recommended_action=(
                    "restore_foreshadow"
                    if existing.status == "archived"
                    else "open_foreshadow"
                ),
                extra={"lifecycle_id": existing.id},
            )
        lifecycle = ForeshadowLifecycle(
            id=uuid.uuid4().hex,
            project_id=project_id,
            plan_id=plan.id,
            element_id=element.id,
            status="active",
            lock_version=1,
            created_by=current_user.id,
            updated_by=current_user.id,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(lifecycle)
        await db.flush()
        event = _event(
            lifecycle=lifecycle,
            user_id=current_user.id,
            event_kind="create",
            previous_version=0,
            metadata={"element_id": element.id},
        )
        db.add(event)
        return lifecycle, 0, event

    return await _run_write(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        operation_key=body.operation_key,
        operation_type="foreshadow_bind",
        lifecycle_id=None,
        fingerprint_payload=body.model_dump(mode="json"),
        mutate=mutate,
    )


@router.post("/{lifecycle_id}/archive", response_model=ForeshadowMutationReceipt)
async def archive_foreshadow(
    project_id: str,
    lifecycle_id: str,
    body: ForeshadowLifecycleCommand,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    async def mutate(_project, _plan):
        lifecycle = await lock_lifecycle(db, project_id, lifecycle_id)
        require_lifecycle_version(lifecycle, body.expected_lifecycle_version)
        require_active_lifecycle(lifecycle)
        previous = lifecycle.lock_version
        lifecycle.status = "archived"
        lifecycle.lock_version += 1
        lifecycle.updated_by = current_user.id
        lifecycle.updated_at = utcnow()
        event = _event(
            lifecycle=lifecycle,
            user_id=current_user.id,
            event_kind="archive",
            previous_version=previous,
        )
        db.add(event)
        return lifecycle, previous, event

    return await _run_write(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        operation_key=body.operation_key,
        operation_type="foreshadow_archive",
        lifecycle_id=lifecycle_id,
        fingerprint_payload=body.model_dump(mode="json"),
        mutate=mutate,
    )


@router.post("/{lifecycle_id}/restore", response_model=ForeshadowMutationReceipt)
async def restore_foreshadow(
    project_id: str,
    lifecycle_id: str,
    body: ForeshadowRestoreCommand,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    async def mutate(_project, plan):
        require_structure_version(plan, body.expected_structure_version)
        lifecycle = await lock_lifecycle(db, project_id, lifecycle_id)
        require_lifecycle_version(lifecycle, body.expected_lifecycle_version)
        if lifecycle.status != "archived":
            raise ForeshadowWriteError(
                "FORESHADOW_NOT_ARCHIVED",
                "伏笔当前未归档。",
                recommended_action="refresh_foreshadow",
            )
        await load_eligible_element(
            db,
            project_id,
            lifecycle.element_id,
            body.expected_element_lock_version,
        )
        await _validate_lifecycle_restore(db, lifecycle)
        previous = lifecycle.lock_version
        lifecycle.status = "active"
        lifecycle.lock_version += 1
        lifecycle.updated_by = current_user.id
        lifecycle.updated_at = utcnow()
        event = _event(
            lifecycle=lifecycle,
            user_id=current_user.id,
            event_kind="restore",
            previous_version=previous,
        )
        db.add(event)
        return lifecycle, previous, event

    return await _run_write(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        operation_key=body.operation_key,
        operation_type="foreshadow_restore",
        lifecycle_id=lifecycle_id,
        fingerprint_payload=body.model_dump(mode="json"),
        mutate=mutate,
    )


@router.post("/{lifecycle_id}/plans", response_model=ForeshadowMutationReceipt)
async def create_plan_item(
    project_id: str,
    lifecycle_id: str,
    body: ForeshadowPlanCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    async def mutate(_project, plan):
        require_structure_version(plan, body.expected_structure_version)
        lifecycle = await lock_lifecycle(db, project_id, lifecycle_id)
        require_lifecycle_version(lifecycle, body.expected_lifecycle_version)
        require_active_lifecycle(lifecycle)
        if body.action_kind == "resolve" and not body.condition_text:
            raise ForeshadowWriteError(
                "FORESHADOW_RESOLVE_CONDITION_REQUIRED",
                "计划回收伏笔时必须填写回收条件。",
                status_code=422,
                recommended_action="add_resolve_condition",
            )
        await load_eligible_element(
            db,
            project_id,
            lifecycle.element_id,
            expected_lock_version=None,
        )
        if await _active_plan(db, lifecycle.id, body.action_kind):
            raise ForeshadowWriteError(
                "FORESHADOW_ACTIVE_PLAN_EXISTS",
                "该伏笔已有同类型的活动计划。",
                recommended_action="cancel_existing_plan",
            )
        if await _active_fact(db, lifecycle.id, "planted" if body.action_kind == "plant" else "resolved"):
            raise ForeshadowWriteError(
                "FORESHADOW_ACTION_ALREADY_CONFIRMED",
                "该动作已记录为作者确认事实，无需再创建计划。",
                recommended_action="review_foreshadow_history",
            )
        target = await load_active_target(
            db,
            project_id,
            plan.id,
            body.target_type,
            body.target_id,
            body.expected_target_lock_version,
        )
        await _validate_plan_order(db, lifecycle.id, body.action_kind, target)
        if body.action_kind == "resolve":
            await _validate_resolve_target_after_planted_fact(
                db, lifecycle.id, target
            )
        previous = lifecycle.lock_version
        item = ForeshadowPlanItem(
            id=uuid.uuid4().hex,
            project_id=project_id,
            plan_id=plan.id,
            lifecycle_id=lifecycle.id,
            action_kind=body.action_kind,
            target_type=body.target_type,
            target_id=body.target_id,
            part_id=body.target_id if body.target_type == "part" else None,
            chapter_id=body.target_id if body.target_type == "chapter" else None,
            condition_text=body.condition_text,
            note=body.note,
            status="active",
            lock_version=1,
            created_by=current_user.id,
            updated_by=current_user.id,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        lifecycle.lock_version += 1
        lifecycle.updated_by = current_user.id
        lifecycle.updated_at = utcnow()
        db.add(item)
        await db.flush()
        event = _event(
            lifecycle=lifecycle,
            user_id=current_user.id,
            event_kind="plan_create",
            previous_version=previous,
            plan_item_id=item.id,
            metadata={"action_kind": body.action_kind, "target_id": body.target_id},
        )
        db.add(event)
        return lifecycle, previous, event

    return await _run_write(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        operation_key=body.operation_key,
        operation_type="foreshadow_plan_create",
        lifecycle_id=lifecycle_id,
        fingerprint_payload=body.model_dump(mode="json"),
        mutate=mutate,
    )


async def _change_plan_item(
    *,
    db: AsyncSession,
    project_id: str,
    lifecycle_id: str,
    item_id: str,
    body: ForeshadowPlanCommand,
    user_id: str,
    restore: bool,
) -> dict[str, Any]:
    operation_type = "foreshadow_plan_restore" if restore else "foreshadow_plan_cancel"

    async def mutate(_project, plan):
        require_structure_version(plan, body.expected_structure_version)
        lifecycle = await lock_lifecycle(db, project_id, lifecycle_id)
        require_lifecycle_version(lifecycle, body.expected_lifecycle_version)
        if restore:
            require_active_lifecycle(lifecycle)
        item = await db.scalar(
            select(ForeshadowPlanItem)
            .where(
                ForeshadowPlanItem.project_id == project_id,
                ForeshadowPlanItem.lifecycle_id == lifecycle_id,
                ForeshadowPlanItem.id == item_id,
            )
            .with_for_update()
        )
        if item is None:
            raise ForeshadowWriteError(
                "FORESHADOW_PLAN_NOT_FOUND",
                "伏笔计划不存在。",
                status_code=404,
                recommended_action="refresh_foreshadow",
            )
        if item.lock_version != body.expected_item_lock_version:
            raise ForeshadowWriteError(
                "FORESHADOW_PLAN_VERSION_CONFLICT",
                "伏笔计划已变化，请刷新后核对。",
                recommended_action="refresh_foreshadow",
                extra={"current_item_lock_version": item.lock_version},
            )
        expected_status = "cancelled" if restore else "active"
        if item.status != expected_status:
            raise ForeshadowWriteError(
                "FORESHADOW_PLAN_STATE_CONFLICT",
                "伏笔计划状态已变化。",
                recommended_action="refresh_foreshadow",
            )
        if restore:
            await load_eligible_element(
                db,
                project_id,
                lifecycle.element_id,
                expected_lock_version=None,
            )
            fact_kind = "planted" if item.action_kind == "plant" else "resolved"
            if await _active_fact(db, lifecycle.id, fact_kind):
                raise ForeshadowWriteError(
                    "FORESHADOW_ACTION_ALREADY_CONFIRMED",
                    "该动作已记录为作者确认事实，不能恢复对应计划。",
                    recommended_action="review_foreshadow_history",
                )
            if await _active_plan(db, lifecycle.id, item.action_kind):
                raise ForeshadowWriteError(
                    "FORESHADOW_ACTIVE_PLAN_EXISTS",
                    "已有同类型活动计划，不能恢复此计划。",
                    recommended_action="cancel_existing_plan",
                )
            target = await _plan_target(db, item)
            if target.status != "active":
                raise ForeshadowWriteError(
                    "FORESHADOW_TARGET_ARCHIVED",
                    "原计划目标已归档，不能恢复。",
                    recommended_action="select_active_target",
                )
            await _validate_plan_order(db, lifecycle.id, item.action_kind, target)
            if item.action_kind == "resolve":
                await _validate_resolve_target_after_planted_fact(
                    db, lifecycle.id, target
                )
            item.status = "active"
        else:
            item.status = "cancelled"
        previous = lifecycle.lock_version
        item.lock_version += 1
        item.updated_by = user_id
        item.updated_at = utcnow()
        lifecycle.lock_version += 1
        lifecycle.updated_by = user_id
        lifecycle.updated_at = utcnow()
        event = _event(
            lifecycle=lifecycle,
            user_id=user_id,
            event_kind="plan_restore" if restore else "plan_cancel",
            previous_version=previous,
            plan_item_id=item.id,
        )
        db.add(event)
        return lifecycle, previous, event

    return await _run_write(
        db=db,
        project_id=project_id,
        user_id=user_id,
        operation_key=body.operation_key,
        operation_type=operation_type,
        lifecycle_id=lifecycle_id,
        fingerprint_payload={**body.model_dump(mode="json"), "item_id": item_id},
        mutate=mutate,
    )


@router.post("/{lifecycle_id}/plans/{item_id}/cancel", response_model=ForeshadowMutationReceipt)
async def cancel_plan_item(
    project_id: str,
    lifecycle_id: str,
    item_id: str,
    body: ForeshadowPlanCommand,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    return await _change_plan_item(
        db=db,
        project_id=project_id,
        lifecycle_id=lifecycle_id,
        item_id=item_id,
        body=body,
        user_id=current_user.id,
        restore=False,
    )


@router.post("/{lifecycle_id}/plans/{item_id}/restore", response_model=ForeshadowMutationReceipt)
async def restore_plan_item(
    project_id: str,
    lifecycle_id: str,
    item_id: str,
    body: ForeshadowPlanCommand,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    return await _change_plan_item(
        db=db,
        project_id=project_id,
        lifecycle_id=lifecycle_id,
        item_id=item_id,
        body=body,
        user_id=current_user.id,
        restore=True,
    )


@router.post("/{lifecycle_id}/facts", response_model=ForeshadowMutationReceipt)
async def record_fact(
    project_id: str,
    lifecycle_id: str,
    body: ForeshadowFactCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    async def mutate(_project, plan):
        require_structure_version(plan, body.expected_structure_version)
        lifecycle = await lock_lifecycle(db, project_id, lifecycle_id)
        require_lifecycle_version(lifecycle, body.expected_lifecycle_version)
        require_active_lifecycle(lifecycle)
        await load_eligible_element(
            db,
            project_id,
            lifecycle.element_id,
            expected_lock_version=None,
        )
        if await _active_fact(db, lifecycle.id, body.fact_kind):
            raise ForeshadowWriteError(
                "FORESHADOW_ACTIVE_FACT_EXISTS",
                "该伏笔已有同类型的作者确认事实。",
                recommended_action="review_foreshadow_history",
            )
        chapter = await load_active_target(
            db,
            project_id,
            plan.id,
            "chapter",
            body.chapter_id,
            body.expected_chapter_lock_version,
        )
        if not isinstance(chapter, PlanningChapter):
            raise AssertionError("chapter target expected")
        if body.fact_kind == "resolved":
            planted = await _active_fact(db, lifecycle.id, "planted")
            if planted is None:
                raise ForeshadowWriteError(
                    "FORESHADOW_NOT_PLANTED",
                    "伏笔尚未埋入，不能记录回收事实。",
                    recommended_action="record_planted_fact",
                )
            planted_chapter = await db.scalar(
                select(PlanningChapter).where(PlanningChapter.id == planted.chapter_id)
            )
            if planted_chapter is None:
                raise ForeshadowWriteError(
                    "FORESHADOW_CHAPTER_MISSING",
                    "埋入事实引用的章节不存在，系统已停止写入。",
                    recommended_action="contact_support",
                )
            if await _chapter_order(db, planted_chapter) >= await _chapter_order(db, chapter):
                raise ForeshadowWriteError(
                    "FORESHADOW_FACT_ORDER_INVALID",
                    "伏笔回收章节必须晚于实际埋入章节。",
                    recommended_action="select_later_chapter",
                )
        elif await _active_fact(db, lifecycle.id, "resolved"):
            raise ForeshadowWriteError(
                "FORESHADOW_ALREADY_RESOLVED",
                "伏笔已有回收事实，不能另行记录埋入事实。",
                recommended_action="review_foreshadow_history",
            )
        if body.fact_kind == "planted":
            resolve_plan = await _active_plan(db, lifecycle.id, "resolve")
            if resolve_plan is not None:
                resolve_target = await _plan_target(db, resolve_plan)
                if await _chapter_order(db, chapter) >= await _target_order(
                    db, resolve_target
                ):
                    raise ForeshadowWriteError(
                        "FORESHADOW_PLAN_ORDER_INVALID",
                        "已有计划回收位置必须晚于实际埋入章节。",
                        recommended_action="adjust_resolve_plan",
                    )
        previous = lifecycle.lock_version
        fact = ForeshadowFact(
            id=uuid.uuid4().hex,
            project_id=project_id,
            plan_id=plan.id,
            lifecycle_id=lifecycle.id,
            chapter_id=chapter.id,
            fact_kind=body.fact_kind,
            note=body.note,
            status="active",
            lock_version=1,
            recorded_by=current_user.id,
            created_at=utcnow(),
        )
        lifecycle.lock_version += 1
        lifecycle.updated_by = current_user.id
        lifecycle.updated_at = utcnow()
        db.add(fact)
        await db.flush()
        event = _event(
            lifecycle=lifecycle,
            user_id=current_user.id,
            event_kind="fact_record",
            previous_version=previous,
            fact_id=fact.id,
            metadata={"fact_kind": body.fact_kind, "chapter_id": chapter.id},
        )
        db.add(event)
        return lifecycle, previous, event

    return await _run_write(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        operation_key=body.operation_key,
        operation_type="foreshadow_fact_record",
        lifecycle_id=lifecycle_id,
        fingerprint_payload=body.model_dump(mode="json"),
        mutate=mutate,
    )


@router.post("/{lifecycle_id}/facts/{fact_id}/retract", response_model=ForeshadowMutationReceipt)
async def retract_fact(
    project_id: str,
    lifecycle_id: str,
    fact_id: str,
    body: ForeshadowFactRetract,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    async def mutate(_project, _plan):
        lifecycle = await lock_lifecycle(db, project_id, lifecycle_id)
        require_lifecycle_version(lifecycle, body.expected_lifecycle_version)
        fact = await db.scalar(
            select(ForeshadowFact)
            .where(
                ForeshadowFact.project_id == project_id,
                ForeshadowFact.lifecycle_id == lifecycle_id,
                ForeshadowFact.id == fact_id,
            )
            .with_for_update()
        )
        if fact is None:
            raise ForeshadowWriteError(
                "FORESHADOW_FACT_NOT_FOUND",
                "伏笔事实不存在。",
                status_code=404,
                recommended_action="refresh_foreshadow",
            )
        if fact.lock_version != body.expected_fact_lock_version:
            raise ForeshadowWriteError(
                "FORESHADOW_FACT_VERSION_CONFLICT",
                "伏笔事实已变化，请刷新后核对。",
                recommended_action="refresh_foreshadow",
                extra={"current_fact_lock_version": fact.lock_version},
            )
        if fact.status != "active":
            raise ForeshadowWriteError(
                "FORESHADOW_FACT_ALREADY_RETRACTED",
                "该事实已经撤回。",
                recommended_action="refresh_foreshadow",
            )
        if fact.fact_kind == "planted" and await _active_fact(db, lifecycle.id, "resolved"):
            raise ForeshadowWriteError(
                "FORESHADOW_RETRACT_ORDER_INVALID",
                "请先撤回回收事实，再撤回埋入事实。",
                recommended_action="retract_resolved_fact_first",
            )
        previous = lifecycle.lock_version
        fact.status = "retracted"
        fact.lock_version += 1
        fact.retracted_by = current_user.id
        fact.retracted_at = utcnow()
        lifecycle.lock_version += 1
        lifecycle.updated_by = current_user.id
        lifecycle.updated_at = utcnow()
        event = _event(
            lifecycle=lifecycle,
            user_id=current_user.id,
            event_kind="fact_retract",
            previous_version=previous,
            fact_id=fact.id,
            metadata={"reason": body.reason},
        )
        db.add(event)
        return lifecycle, previous, event

    return await _run_write(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        operation_key=body.operation_key,
        operation_type="foreshadow_fact_retract",
        lifecycle_id=lifecycle_id,
        fingerprint_payload={**body.model_dump(mode="json"), "fact_id": fact_id},
        mutate=mutate,
    )
