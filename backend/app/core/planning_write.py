"""Exactly-once transactional writes for the relational planning structure."""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import hashlib
import json
from typing import Any
import uuid

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.legacy_json import read_legacy_json
from app.core.maintenance import ensure_project_writes_available
from app.models.planning import (
    NovelPlan,
    PlanningChapter,
    PlanningMutationOperation,
    PlanningPart,
)
from app.models.foreshadow import (
    ForeshadowFact,
    ForeshadowLifecycle,
    ForeshadowPlanItem,
)
from app.models.project import Chapter, Outline, Project, StoryMemory
from app.schemas.planning import (
    PlanningAssignmentMutationReceipt,
    PlanningMutationReceipt,
)


_FINGERPRINT_VERSION = "planning-structure-v1"
_MAX_POSITION = 2_147_483_647


class PlanningWriteError(Exception):
    """A stable, user-actionable planning write failure."""

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


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _memory_has_content(memory: StoryMemory | None) -> bool:
    if memory is None:
        return False
    for field_name in (
        "revealed_elements",
        "character_states",
        "foreshadows",
        "timeline",
        "chapter_summaries",
    ):
        result = read_legacy_json(getattr(memory, field_name, None))
        if not result.valid:
            return True
        if isinstance(result.value, (list, dict)):
            if result.value:
                return True
        elif result.value not in (None, ""):
            return True
    return False


def operation_fingerprint(
    project_id: str,
    operation_type: str,
    target_id: str | None,
    payload: dict[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "version": _FINGERPRINT_VERSION,
            "project_id": project_id,
            "operation_type": operation_type,
            "target_id": target_id,
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
) -> PlanningMutationOperation | None:
    return await db.scalar(
        select(PlanningMutationOperation).where(
            PlanningMutationOperation.project_id == project_id,
            PlanningMutationOperation.requested_by == user_id,
            PlanningMutationOperation.operation_key == operation_key,
        )
    )


def replay_operation(
    operation: PlanningMutationOperation,
    request_fingerprint: str | None = None,
) -> dict[str, Any]:
    if (
        request_fingerprint is not None
        and operation.request_fingerprint != request_fingerprint
    ):
        raise PlanningWriteError(
            "PLANNING_OPERATION_KEY_REUSED",
            "该操作编号已用于不同请求，系统没有重复写入。",
            recommended_action="retry_with_new_operation_key",
        )
    try:
        receipt_model = (
            PlanningAssignmentMutationReceipt
            if operation.operation_type.startswith("assignment_")
            else PlanningMutationReceipt
        )
        receipt = receipt_model.model_validate(operation.result_snapshot)
    except ValidationError as exc:
        raise PlanningWriteError(
            "PLANNING_OPERATION_CORRUPT",
            "操作记录不完整，系统已停止自动处理。",
            recommended_action="contact_support",
        ) from exc
    if (
        receipt.receipt_id != operation.id
        or receipt.project_id != operation.project_id
        or receipt.operation_key != operation.operation_key
        or receipt.operation_type != operation.operation_type
    ):
        raise PlanningWriteError(
            "PLANNING_OPERATION_CORRUPT",
            "操作记录与收据不一致，系统已停止自动处理。",
            recommended_action="contact_support",
        )
    snapshot = receipt.model_dump(mode="json")
    snapshot["replayed"] = True
    return snapshot


async def _assert_compatible_project(
    db: AsyncSession,
    project: Project,
) -> None:
    if project.lore_storage_mode != "relational":
        raise PlanningWriteError(
            "PLANNING_LORE_MIGRATION_REQUIRED",
            "请先将旧世界观安全升级为设定仓库。",
            recommended_action="open_lore_repository",
        )
    outline_id = await db.scalar(
        select(Outline.id).where(Outline.project_id == project.id)
    )
    chapter_id = await db.scalar(
        select(Chapter.id).where(Chapter.project_id == project.id)
    )
    memory = await db.scalar(
        select(StoryMemory).where(StoryMemory.project_id == project.id)
    )
    reasons: list[str] = []
    if outline_id is not None:
        reasons.append("outline")
    if chapter_id is not None:
        reasons.append("chapter_content")
    if _memory_has_content(memory):
        reasons.append("story_memory")
    if reasons:
        raise PlanningWriteError(
            "PLANNING_LEGACY_IMPORT_REQUIRED",
            "检测到历史章节资料；当前不会自动迁移或覆盖。",
            recommended_action="return_to_project",
            extra={"reasons": reasons},
        )


async def lock_planning_context(
    db: AsyncSession,
    project_id: str,
    user_id: str,
) -> tuple[Project, NovelPlan]:
    project = await db.scalar(
        select(Project).where(Project.id == project_id).with_for_update()
    )
    if project is None:
        raise PlanningWriteError(
            "PLANNING_PROJECT_NOT_FOUND",
            "项目不存在。",
            status_code=404,
            recommended_action="return_to_projects",
        )
    if project.owner_id != user_id:
        raise PlanningWriteError(
            "PLANNING_PROJECT_FORBIDDEN",
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
        raise PlanningWriteError(
            "PLANNING_NOT_INITIALIZED",
            "章节规划尚未创建。",
            status_code=404,
            recommended_action="initialize_planning",
        )
    if plan.status != "active":
        raise PlanningWriteError(
            "PLANNING_PLAN_ARCHIVED",
            "章节规划当前不可编辑。",
            recommended_action="return_to_project",
        )
    await _assert_compatible_project(db, project)
    return project, plan


Mutation = Callable[[NovelPlan], Awaitable[dict[str, Any]]]


async def execute_operation(
    *,
    db: AsyncSession,
    project_id: str,
    user_id: str,
    operation_key: str,
    operation_type: str,
    target_id: str | None,
    expected_structure_version: int,
    fingerprint_payload: dict[str, Any],
    mutate: Mutation,
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
        if plan.structure_version != expected_structure_version:
            raise PlanningWriteError(
                "PLANNING_STRUCTURE_VERSION_CONFLICT",
                "章节规划已被其他操作更新，请刷新后核对。",
                recommended_action="refresh_planning",
                extra={"current_structure_version": plan.structure_version},
            )

        previous_version = plan.structure_version
        result = await mutate(plan)
        plan.structure_version = previous_version + 1
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
        snapshot = {
            "receipt_id": operation.id,
            "operation_key": operation_key,
            "operation_type": operation_type,
            "replayed": False,
            "changed": bool(result.get("changed", True)),
            "project_id": project_id,
            "plan_id": plan.id,
            "previous_structure_version": previous_version,
            "new_structure_version": plan.structure_version,
            "affected_node": result.get("affected_node"),
            "placement": result.get("placement"),
            "structure": result.get("structure"),
            "created_at": operation.created_at.isoformat(),
        }
        snapshot = PlanningMutationReceipt.model_validate(snapshot).model_dump(
            mode="json"
        )
        operation.result_snapshot = snapshot
        db.add(operation)
        await db.flush()
        ensure_project_writes_available()
        await db.commit()
        return snapshot
    except PlanningWriteError:
        await db.rollback()
        raise
    except IntegrityError as exc:
        await db.rollback()
        existing = await find_operation(db, project_id, user_id, operation_key)
        if existing is not None:
            return replay_operation(existing, request_fingerprint)
        raise PlanningWriteError(
            "PLANNING_WRITE_CONFLICT",
            "章节规划发生并发冲突，请刷新后安全重试。",
            retryable=True,
            recommended_action="refresh_planning",
        ) from exc
    except Exception:
        await db.rollback()
        raise


def node_snapshot(node: PlanningPart | PlanningChapter) -> dict[str, Any]:
    return {
        "id": node.id,
        "node_type": "part" if isinstance(node, PlanningPart) else "chapter",
        "status": node.status,
        "position": node.position,
        "part_id": getattr(node, "part_id", None),
        "lock_version": node.lock_version,
    }


async def next_active_position(
    db: AsyncSession,
    model: type[PlanningPart] | type[PlanningChapter],
    *,
    plan_id: str | None = None,
    part_id: str | None = None,
) -> int:
    conditions = [model.status == "active"]
    if plan_id is not None:
        conditions.append(model.plan_id == plan_id)
    if part_id is not None:
        conditions.append(model.part_id == part_id)
    maximum = await db.scalar(select(func.max(model.position)).where(*conditions))
    if maximum is not None and int(maximum) >= _MAX_POSITION:
        raise PlanningWriteError(
            "PLANNING_POSITION_EXHAUSTED",
            "章节位置编号已达安全上限，系统没有写入。",
            recommended_action="contact_support",
        )
    return int(maximum or 0) + 1


async def reorder_active_structure(
    db: AsyncSession,
    plan: NovelPlan,
    requested_parts: list[dict[str, Any]],
) -> dict[str, Any]:
    parts = list(
        (
            await db.scalars(
                select(PlanningPart)
                .where(
                    PlanningPart.plan_id == plan.id,
                    PlanningPart.status == "active",
                )
                .order_by(PlanningPart.id)
                .with_for_update()
            )
        ).all()
    )
    chapters = list(
        (
            await db.scalars(
                select(PlanningChapter)
                .where(
                    PlanningChapter.plan_id == plan.id,
                    PlanningChapter.status == "active",
                )
                .order_by(PlanningChapter.id)
                .with_for_update()
            )
        ).all()
    )
    part_by_id = {part.id: part for part in parts}
    chapter_by_id = {chapter.id: chapter for chapter in chapters}
    requested_part_ids = [item["part_id"] for item in requested_parts]
    requested_chapter_ids = [
        chapter_id
        for item in requested_parts
        for chapter_id in item["chapter_ids"]
    ]
    issues: list[dict[str, Any]] = []
    duplicate_parts = sorted(
        node_id for node_id, count in Counter(requested_part_ids).items() if count > 1
    )
    if duplicate_parts:
        issues.append({"kind": "duplicate_part", "node_ids": duplicate_parts})
    requested_part_set = set(requested_part_ids)
    stored_part_set = set(part_by_id)
    if requested_part_set != stored_part_set:
        issues.append(
            {
                "kind": "part_coverage_mismatch",
                "missing_ids": sorted(stored_part_set - requested_part_set),
                "unknown_ids": sorted(requested_part_set - stored_part_set),
            }
        )
    duplicate_chapters = sorted(
        node_id
        for node_id, count in Counter(requested_chapter_ids).items()
        if count > 1
    )
    if duplicate_chapters:
        issues.append(
            {"kind": "duplicate_chapter", "node_ids": duplicate_chapters}
        )
    requested_chapter_set = set(requested_chapter_ids)
    stored_chapter_set = set(chapter_by_id)
    if requested_chapter_set != stored_chapter_set:
        issues.append(
            {
                "kind": "chapter_coverage_mismatch",
                "missing_ids": sorted(stored_chapter_set - requested_chapter_set),
                "unknown_ids": sorted(requested_chapter_set - stored_chapter_set),
            }
        )
    inactive_parent_chapters = sorted(
        chapter.id for chapter in chapters if chapter.part_id not in part_by_id
    )
    if inactive_parent_chapters:
        issues.append(
            {
                "kind": "inactive_parent",
                "chapter_ids": inactive_parent_chapters,
            }
        )
    if issues:
        raise PlanningWriteError(
            "PLANNING_STRUCTURE_INVALID",
            "章节结构不完整或包含无效节点，系统没有写入。",
            recommended_action="correct_structure",
            extra={"issues": issues},
        )

    desired_parts = {
        part_id: position
        for position, part_id in enumerate(requested_part_ids, start=1)
    }
    desired_chapters: dict[str, tuple[str, int]] = {}
    for item in requested_parts:
        desired_chapters.update(
            {
                chapter_id: (item["part_id"], position)
                for position, chapter_id in enumerate(item["chapter_ids"], start=1)
            }
        )

    def desired_order(target_type: str, target_id: str) -> tuple[int, int]:
        if target_type == "part":
            return desired_parts[target_id], 0
        part_id, position = desired_chapters[target_id]
        return desired_parts[part_id], position

    active_plan_items = list(
        (
            await db.scalars(
                select(ForeshadowPlanItem).where(
                    ForeshadowPlanItem.plan_id == plan.id,
                    ForeshadowPlanItem.status == "active",
                    ForeshadowPlanItem.lifecycle_id.in_(
                        select(ForeshadowLifecycle.id).where(
                            ForeshadowLifecycle.status == "active"
                        )
                    ),
                )
            )
        ).all()
    )
    active_facts = list(
        (
            await db.scalars(
                select(ForeshadowFact).where(
                    ForeshadowFact.plan_id == plan.id,
                    ForeshadowFact.status == "active",
                    ForeshadowFact.lifecycle_id.in_(
                        select(ForeshadowLifecycle.id).where(
                            ForeshadowLifecycle.status == "active"
                        )
                    ),
                )
            )
        ).all()
    )
    plan_pairs: dict[str, dict[str, ForeshadowPlanItem]] = {}
    for item in active_plan_items:
        plan_pairs.setdefault(item.lifecycle_id, {})[item.action_kind] = item
    fact_pairs: dict[str, dict[str, ForeshadowFact]] = {}
    for item in active_facts:
        fact_pairs.setdefault(item.lifecycle_id, {})[item.fact_kind] = item
    invalid_lifecycle_ids: set[str] = set()
    for lifecycle_id, pair in plan_pairs.items():
        plant = pair.get("plant")
        resolve = pair.get("resolve")
        if plant and resolve and desired_order(
            plant.target_type, plant.target_id
        ) >= desired_order(resolve.target_type, resolve.target_id):
            invalid_lifecycle_ids.add(lifecycle_id)
    for lifecycle_id, pair in fact_pairs.items():
        planted = pair.get("planted")
        resolved = pair.get("resolved")
        if planted and resolved and desired_order(
            "chapter", planted.chapter_id
        ) >= desired_order("chapter", resolved.chapter_id):
            invalid_lifecycle_ids.add(lifecycle_id)
    if invalid_lifecycle_ids:
        raise PlanningWriteError(
            "PLANNING_FORESHADOW_ORDER_INVALID",
            "此次重排会让伏笔回收早于或等于埋入位置，系统没有写入。",
            recommended_action="adjust_foreshadow_or_structure",
            extra={"lifecycle_ids": sorted(invalid_lifecycle_ids)},
        )

    changed_parts = [
        part for part in parts if part.position != desired_parts[part.id]
    ]
    changed_chapters = [
        chapter
        for chapter in chapters
        if (chapter.part_id, chapter.position) != desired_chapters[chapter.id]
    ]
    max_position = max(
        [part.position for part in parts]
        + [chapter.position for chapter in chapters]
        + [0]
    )
    temporary_count = len(changed_parts) + len(changed_chapters)
    if max_position + temporary_count + 1 > _MAX_POSITION:
        raise PlanningWriteError(
            "PLANNING_POSITION_EXHAUSTED",
            "章节位置编号已达安全上限，系统没有写入。",
            recommended_action="contact_support",
        )
    temporary = max_position + 1
    for part in changed_parts:
        part.position = temporary
        temporary += 1
    for chapter in changed_chapters:
        chapter.position = temporary
        temporary += 1
    if changed_parts or changed_chapters:
        await db.flush()

    for part in changed_parts:
        part.position = desired_parts[part.id]
        part.lock_version += 1
        part.updated_at = _utcnow()
    for chapter in changed_chapters:
        chapter.part_id, chapter.position = desired_chapters[chapter.id]
        chapter.lock_version += 1
        chapter.updated_at = _utcnow()
    if changed_parts or changed_chapters:
        await db.flush()

    canonical = json.dumps(
        requested_parts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "changed": bool(changed_parts or changed_chapters),
        "structure": {
            "digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "part_count": len(parts),
            "chapter_count": len(chapters),
            "changed_part_count": len(changed_parts),
            "changed_chapter_count": len(changed_chapters),
        },
    }
