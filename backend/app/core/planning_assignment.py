"""Transactional writes and read helpers for planning Lore assignments."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal
import uuid

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.maintenance import ensure_project_writes_available
from app.core.planning_write import (
    PlanningWriteError,
    find_operation,
    lock_planning_context,
    operation_fingerprint,
    replay_operation,
)
from app.models.lore import SettingElement, SettingType
from app.models.planning import (
    NovelPlan,
    PlanningChapter,
    PlanningLoreAssignment,
    PlanningMutationOperation,
    PlanningPart,
)
from app.schemas.planning import PlanningAssignmentMutationReceipt


ScopeType = Literal["novel", "part", "chapter"]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def ineligible_reasons(
    element: SettingElement,
    setting_type: SettingType,
) -> list[str]:
    reasons: list[str] = []
    if element.confirmation_status == "candidate":
        reasons.append("element_candidate")
    elif element.confirmation_status == "rejected":
        reasons.append("element_rejected")
    if element.lifecycle_status == "archived":
        reasons.append("element_archived")
    elif element.lifecycle_status == "merged":
        reasons.append("element_merged")
    if not element.enabled:
        reasons.append("element_disabled")
    if "needs_confirmation" in (element.field_states or {}).values():
        reasons.append("fields_need_confirmation")
    if setting_type.status != "active":
        reasons.append("type_archived")
    return reasons


async def load_element(
    db: AsyncSession,
    project_id: str,
    element_id: str,
    *,
    lock: bool,
) -> tuple[SettingElement, SettingType]:
    statement = select(SettingElement).where(
        SettingElement.project_id == project_id,
        SettingElement.id == element_id,
    )
    if lock:
        statement = statement.with_for_update()
    element = await db.scalar(statement)
    if element is None:
        raise PlanningWriteError(
            "PLANNING_ELEMENT_NOT_FOUND",
            "设定不存在或不属于当前项目。",
            status_code=404,
            recommended_action="refresh_lore_repository",
        )
    setting_type = await db.scalar(
        select(SettingType).where(
            SettingType.project_id == project_id,
            SettingType.id == element.type_id,
        )
    )
    if setting_type is None:
        raise PlanningWriteError(
            "PLANNING_ELEMENT_NOT_FOUND",
            "设定类型不完整，系统已停止自动处理。",
            recommended_action="refresh_lore_repository",
        )
    return element, setting_type


async def resolve_scope(
    db: AsyncSession,
    plan: NovelPlan,
    scope_type: ScopeType,
    scope_target_id: str,
    *,
    lock: bool,
) -> tuple[dict[str, Any], PlanningPart | None, PlanningChapter | None]:
    if scope_type == "novel":
        if scope_target_id != plan.project_id:
            raise _scope_not_found()
        return (
            {
                "scope_type": "novel",
                "scope_target_id": plan.project_id,
                "title": "整部小说",
                "status": plan.status,
                "part_id": None,
            },
            None,
            None,
        )

    if scope_type == "part":
        statement = select(PlanningPart).where(
            PlanningPart.project_id == plan.project_id,
            PlanningPart.plan_id == plan.id,
            PlanningPart.id == scope_target_id,
        )
        if lock:
            statement = statement.with_for_update()
        part = await db.scalar(statement)
        if part is None:
            raise _scope_not_found()
        return (
            {
                "scope_type": "part",
                "scope_target_id": part.id,
                "title": part.title,
                "status": part.status,
                "part_id": part.id,
            },
            part,
            None,
        )

    parent_id = await db.scalar(
        select(PlanningChapter.part_id).where(
            PlanningChapter.project_id == plan.project_id,
            PlanningChapter.plan_id == plan.id,
            PlanningChapter.id == scope_target_id,
        )
    )
    if parent_id is None:
        raise _scope_not_found()
    part_statement = select(PlanningPart).where(
        PlanningPart.project_id == plan.project_id,
        PlanningPart.plan_id == plan.id,
        PlanningPart.id == parent_id,
    )
    if lock:
        part_statement = part_statement.with_for_update()
    part = await db.scalar(part_statement)
    chapter_statement = select(PlanningChapter).where(
        PlanningChapter.project_id == plan.project_id,
        PlanningChapter.plan_id == plan.id,
        PlanningChapter.id == scope_target_id,
        PlanningChapter.part_id == parent_id,
    )
    if lock:
        chapter_statement = chapter_statement.with_for_update()
    chapter = await db.scalar(chapter_statement)
    if part is None or chapter is None:
        raise _scope_not_found()
    status = (
        "active"
        if part.status == "active" and chapter.status == "active"
        else "archived"
    )
    return (
        {
            "scope_type": "chapter",
            "scope_target_id": chapter.id,
            "title": chapter.title,
            "status": status,
            "part_id": part.id,
        },
        part,
        chapter,
    )


def _scope_not_found() -> PlanningWriteError:
    return PlanningWriteError(
        "PLANNING_SCOPE_NOT_FOUND",
        "作用范围不存在或不属于当前项目。",
        status_code=404,
        recommended_action="refresh_planning",
    )


def require_active_scope(scope: dict[str, Any]) -> None:
    if scope["status"] != "active":
        raise PlanningWriteError(
            "PLANNING_SCOPE_ARCHIVED",
            "该作用范围已归档，暂时不能新增或恢复分配。",
            recommended_action="restore_scope",
        )


def require_eligible(element: SettingElement, setting_type: SettingType) -> None:
    reasons = ineligible_reasons(element, setting_type)
    if reasons:
        raise PlanningWriteError(
            "PLANNING_ELEMENT_INELIGIBLE",
            "该设定当前不可用于生成，系统没有创建分配。",
            recommended_action="review_lore_element",
            extra={"ineligible_reasons": reasons},
        )


def assignment_snapshot(
    assignment: PlanningLoreAssignment,
    element: SettingElement,
    setting_type: SettingType,
    scope: dict[str, Any],
) -> dict[str, Any]:
    reasons = ineligible_reasons(element, setting_type)
    if assignment.status == "removed":
        reasons.append("assignment_removed")
    return {
        "id": assignment.id,
        "element_id": element.id,
        "scope": scope,
        "status": assignment.status,
        "lock_version": assignment.lock_version,
        "assigned_at_content_version": assignment.element_content_version,
        "current_content_version": element.content_version,
        "content_changed_since_assignment": (
            assignment.element_content_version != element.content_version
        ),
        "element": {
            "id": element.id,
            "name": element.name,
            "summary": element.summary or "",
            "type": {
                "id": setting_type.id,
                "key": setting_type.key,
                "display_name": setting_type.display_name,
                "status": setting_type.status,
            },
            "confirmation_status": element.confirmation_status,
            "lifecycle_status": element.lifecycle_status,
            "enabled": element.enabled,
            "merged_into_element_id": element.merged_into_element_id,
        },
        "generation_eligible": not reasons,
        "ineligible_reasons": reasons,
        "created_at": assignment.created_at.isoformat(),
        "updated_at": assignment.updated_at.isoformat(),
    }


AssignmentMutation = Callable[[NovelPlan], Awaitable[dict[str, Any]]]


async def execute_assignment_operation(
    *,
    db: AsyncSession,
    project_id: str,
    user_id: str,
    operation_key: str,
    operation_type: str,
    target_id: str | None,
    expected_assignment_version: int,
    fingerprint_payload: dict[str, Any],
    mutate: AssignmentMutation,
) -> dict[str, Any]:
    request_fingerprint = operation_fingerprint(
        project_id, operation_type, target_id, fingerprint_payload
    )
    existing = await find_operation(db, project_id, user_id, operation_key)
    if existing is not None:
        return replay_operation(existing, request_fingerprint)
    try:
        ensure_project_writes_available()
        _, plan = await lock_planning_context(db, project_id, user_id)
        existing = await find_operation(db, project_id, user_id, operation_key)
        if existing is not None:
            replayed = replay_operation(existing, request_fingerprint)
            await db.rollback()
            return replayed
        if plan.assignment_version != expected_assignment_version:
            raise PlanningWriteError(
                "PLANNING_ASSIGNMENT_VERSION_CONFLICT",
                "设定分配已被其他操作更新，请刷新后核对。",
                recommended_action="refresh_assignments",
                extra={"current_assignment_version": plan.assignment_version},
            )
        previous_version = plan.assignment_version
        result = await mutate(plan)
        plan.assignment_version = previous_version + 1
        plan.updated_at = _utcnow()
        await db.flush()
        operation = PlanningMutationOperation(
            id=uuid.uuid4().hex,
            project_id=project_id,
            requested_by=user_id,
            operation_key=operation_key,
            operation_type=operation_type,
            request_fingerprint=request_fingerprint,
            result_snapshot={},
            created_at=_utcnow(),
        )
        snapshot = PlanningAssignmentMutationReceipt(
            receipt_id=operation.id,
            operation_key=operation_key,
            operation_type=operation_type,
            replayed=False,
            changed=result.get("changed", True),
            project_id=project_id,
            plan_id=plan.id,
            previous_assignment_version=previous_version,
            new_assignment_version=plan.assignment_version,
            assignment=result["assignment"],
            event_id=result["event_id"],
            created_at=operation.created_at,
        ).model_dump(mode="json")
        operation.result_snapshot = snapshot
        db.add(operation)
        await db.flush()
        ensure_project_writes_available()
        await db.commit()
        return snapshot
    except PlanningWriteError:
        await db.rollback()
        raise
    except (IntegrityError, ValidationError) as exc:
        await db.rollback()
        existing = await find_operation(db, project_id, user_id, operation_key)
        if existing is not None:
            return replay_operation(existing, request_fingerprint)
        raise PlanningWriteError(
            "PLANNING_ASSIGNMENT_CONFLICT",
            "设定分配发生并发冲突，请刷新后重试。",
            retryable=True,
            recommended_action="refresh_assignments",
        ) from exc
    except Exception:
        await db.rollback()
        raise
