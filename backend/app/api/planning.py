"""Safe initialization and read API for the relational chapter planning layer."""

from typing import Annotated, Any
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import User, get_current_user, get_project_for_owner
from app.core.legacy_json import read_legacy_json
from app.core.maintenance import (
    ensure_project_writes_available,
    require_project_writes_available,
)
from app.core.planning_write import (
    PlanningWriteError,
    execute_operation,
    find_operation,
    next_active_position,
    node_snapshot,
    reorder_active_structure,
    replay_operation,
)
from app.core.planning_assignment import (
    assignment_snapshot,
    execute_assignment_operation,
    ineligible_reasons,
    load_element,
    require_active_scope,
    require_eligible,
    resolve_scope,
)
from app.database import get_db
from app.models.foreshadow import (
    ForeshadowFact,
    ForeshadowLifecycle,
    ForeshadowPlanItem,
)
from app.models.lore import SettingElement, SettingType
from app.models.planning import (
    NovelPlan,
    PlanningChapter,
    PlanningLoreAssignment,
    PlanningLoreAssignmentEvent,
    PlanningPart,
)
from app.models.project import Chapter, Outline, Project, StoryMemory
from app.schemas.planning import (
    NovelPlanResponse,
    PlanningAssignmentCommand,
    PlanningAssignmentCreate,
    PlanningAssignmentHistoryResponse,
    PlanningAssignmentMutationReceipt,
    PlanningAssignmentScopeResponse,
    PlanningChapterCreate,
    PlanningChapterResponse,
    PlanningChapterUpdate,
    PlanningMutationCommand,
    PlanningMutationReceipt,
    PlanningPartCreate,
    PlanningPartResponse,
    PlanningPartUpdate,
    PlanningStructureReorder,
)


router = APIRouter(prefix="/api/projects/{project_id}/planning", tags=["planning"])

_PLANNING_NOT_INITIALIZED = "PLANNING_NOT_INITIALIZED"
_PLANNING_LEGACY_IMPORT_REQUIRED = "PLANNING_LEGACY_IMPORT_REQUIRED"
_PLANNING_LORE_MIGRATION_REQUIRED = "PLANNING_LORE_MIGRATION_REQUIRED"


def _error(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "message": message, "retryable": retryable}


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


async def _response(db: AsyncSession, plan: NovelPlan) -> NovelPlanResponse:
    parts = list((await db.scalars(
        select(PlanningPart)
        .where(PlanningPart.plan_id == plan.id)
        .order_by(PlanningPart.position, PlanningPart.id)
    )).all())
    chapters = list((await db.scalars(
        select(PlanningChapter)
        .where(PlanningChapter.plan_id == plan.id)
        .order_by(PlanningChapter.part_id, PlanningChapter.position, PlanningChapter.id)
    )).all())
    chapters_by_part: dict[str, list[PlanningChapterResponse]] = {}
    for chapter in chapters:
        chapters_by_part.setdefault(chapter.part_id, []).append(
            PlanningChapterResponse.model_validate(chapter)
        )
    return NovelPlanResponse(
        id=plan.id,
        project_id=plan.project_id,
        status=plan.status,
        structure_version=plan.structure_version,
        assignment_version=plan.assignment_version,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        parts=[
            PlanningPartResponse(
                **PlanningPartResponse.model_validate(part).model_dump(
                    exclude={"chapters"}
                ),
                chapters=chapters_by_part.get(part.id, []),
            )
            for part in parts
        ],
    )


async def _run_write(**kwargs: Any) -> dict[str, Any]:
    try:
        return await execute_operation(**kwargs)
    except PlanningWriteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _node_not_found(node_type: str) -> PlanningWriteError:
    return PlanningWriteError(
        "PLANNING_NODE_NOT_FOUND",
        f"{node_type}不存在或不属于当前项目。",
        status_code=404,
        recommended_action="refresh_planning",
    )


def _node_version_conflict(node: PlanningPart | PlanningChapter) -> PlanningWriteError:
    return PlanningWriteError(
        "PLANNING_NODE_VERSION_CONFLICT",
        "该节点已被其他操作更新，请核对最新内容。",
        recommended_action="review_current_node",
        extra={"current_node": node_snapshot(node)},
    )


@router.get("", response_model=NovelPlanResponse)
async def get_planning(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    plan = await db.scalar(select(NovelPlan).where(NovelPlan.project_id == project_id))
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail=_error(
                _PLANNING_NOT_INITIALIZED,
                "章节规划尚未创建。",
            )
            | {"recommended_action": "initialize_planning"},
        )
    return await _response(db, plan)


@router.post("", response_model=NovelPlanResponse)
async def initialize_planning(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    _write_gate: Annotated[None, Depends(require_project_writes_available)],
):
    await get_project_for_owner(project_id, current_user, db)
    project = await db.scalar(
        select(Project).where(Project.id == project_id).with_for_update()
    )
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    existing = await db.scalar(
        select(NovelPlan)
        .where(NovelPlan.project_id == project_id)
        .with_for_update()
    )
    if existing is not None:
        return await _response(db, existing)

    if (project.lore_storage_mode or "legacy") != "relational":
        raise HTTPException(
            status_code=409,
            detail=_error(
                _PLANNING_LORE_MIGRATION_REQUIRED,
                "请先将旧世界观安全升级为设定仓库，再创建章节规划。",
            )
            | {"recommended_action": "open_lore_repository"},
        )

    outline = await db.scalar(select(Outline.id).where(Outline.project_id == project_id))
    chapter = await db.scalar(select(Chapter.id).where(Chapter.project_id == project_id))
    memory = await db.scalar(
        select(StoryMemory).where(StoryMemory.project_id == project_id)
    )
    legacy_reasons = []
    if outline is not None:
        legacy_reasons.append("outline")
    if chapter is not None:
        legacy_reasons.append("chapter_content")
    if _memory_has_content(memory):
        legacy_reasons.append("story_memory")
    if legacy_reasons:
        raise HTTPException(
            status_code=409,
            detail={
                **_error(
                    _PLANNING_LEGACY_IMPORT_REQUIRED,
                    "检测到历史章节资料；当前不会自动迁移或覆盖。"
                    "章节规划暂不可用，请返回项目查看现有资料。",
                ),
                "recommended_action": "return_to_project",
                "reasons": legacy_reasons,
            },
        )

    ensure_project_writes_available()
    plan = NovelPlan(project_id=project_id)
    db.add(plan)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(
            select(NovelPlan).where(NovelPlan.project_id == project_id)
        )
        if existing is None:
            raise
        plan = existing
    else:
        await db.refresh(plan)
    return await _response(db, plan)


@router.get(
    "/operations/by-key/{operation_key}",
    response_model=PlanningMutationReceipt | PlanningAssignmentMutationReceipt,
)
async def get_planning_operation(
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
                **_error(
                    "PLANNING_OPERATION_NOT_FOUND",
                    "未找到该操作结果。",
                ),
                "recommended_action": "retry_original_request",
            },
        )
    try:
        return replay_operation(operation)
    except PlanningWriteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/parts", response_model=PlanningMutationReceipt)
async def create_planning_part(
    project_id: str,
    body: PlanningPartCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)

    async def mutate(plan: NovelPlan) -> dict[str, Any]:
        position = await next_active_position(db, PlanningPart, plan_id=plan.id)
        part = PlanningPart(
            project_id=project_id,
            plan_id=plan.id,
            title=body.title,
            description=body.description,
            position=position,
        )
        db.add(part)
        await db.flush()
        return {
            "affected_node": node_snapshot(part),
            "placement": {"current_position": position},
        }

    return await _run_write(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        operation_key=body.operation_key,
        operation_type="part_create",
        target_id=None,
        expected_structure_version=body.expected_structure_version,
        fingerprint_payload=body.model_dump(mode="json", exclude={"operation_key"}),
        mutate=mutate,
    )


@router.patch("/parts/{part_id}", response_model=PlanningMutationReceipt)
async def update_planning_part(
    project_id: str,
    part_id: str,
    body: PlanningPartUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)

    async def mutate(plan: NovelPlan) -> dict[str, Any]:
        part = await db.scalar(
            select(PlanningPart)
            .where(
                PlanningPart.project_id == project_id,
                PlanningPart.plan_id == plan.id,
                PlanningPart.id == part_id,
            )
            .with_for_update()
        )
        if part is None:
            raise _node_not_found("篇章")
        if part.lock_version != body.expected_lock_version:
            raise _node_version_conflict(part)
        description = part.description if body.description is None else body.description
        changed = part.title != body.title or part.description != description
        if changed:
            part.title = body.title
            part.description = description
            part.lock_version += 1
        return {"changed": changed, "affected_node": node_snapshot(part)}

    return await _run_write(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        operation_key=body.operation_key,
        operation_type="part_update",
        target_id=part_id,
        expected_structure_version=body.expected_structure_version,
        fingerprint_payload=body.model_dump(mode="json", exclude={"operation_key"}),
        mutate=mutate,
    )


async def _change_part_archive_state(
    *,
    project_id: str,
    part_id: str,
    body: PlanningMutationCommand,
    db: AsyncSession,
    current_user: User,
    restore: bool,
) -> dict[str, Any]:
    await get_project_for_owner(project_id, current_user, db)
    operation_type = "part_restore" if restore else "part_archive"

    async def mutate(plan: NovelPlan) -> dict[str, Any]:
        part = await db.scalar(
            select(PlanningPart)
            .where(
                PlanningPart.project_id == project_id,
                PlanningPart.plan_id == plan.id,
                PlanningPart.id == part_id,
            )
            .with_for_update()
        )
        if part is None:
            raise _node_not_found("篇章")
        previous_position = part.position
        target_status = "active" if restore else "archived"
        changed = part.status != target_status
        if changed and restore:
            part.position = await next_active_position(
                db, PlanningPart, plan_id=plan.id
            )
            part.status = "active"
            part.lock_version += 1
        elif changed:
            counts = dict(
                (
                    await db.execute(
                        select(PlanningChapter.status, func.count())
                        .where(PlanningChapter.part_id == part.id)
                        .group_by(PlanningChapter.status)
                    )
                ).all()
            )
            if sum(counts.values()):
                raise PlanningWriteError(
                    "PLANNING_PART_NOT_EMPTY",
                    "该篇章仍包含章节，请先移动后再归档。",
                    recommended_action="move_chapters_first",
                    extra={
                        "active_chapter_count": counts.get("active", 0),
                        "archived_chapter_count": counts.get("archived", 0),
                    },
                )
            active_assignment_count = await db.scalar(
                select(func.count())
                .select_from(PlanningLoreAssignment)
                .where(
                    PlanningLoreAssignment.project_id == project_id,
                    PlanningLoreAssignment.plan_id == plan.id,
                    PlanningLoreAssignment.scope_type == "part",
                    PlanningLoreAssignment.scope_target_id == part.id,
                    PlanningLoreAssignment.status == "active",
                )
            )
            if active_assignment_count:
                raise PlanningWriteError(
                    "PLANNING_SCOPE_HAS_ACTIVE_ASSIGNMENTS",
                    "该篇章仍有启用中的设定分配，请先移除分配。",
                    recommended_action="remove_assignments_first",
                    extra={"active_assignment_count": active_assignment_count},
                )
            active_foreshadow_count = await db.scalar(
                select(func.count())
                .select_from(ForeshadowPlanItem)
                .join(
                    ForeshadowLifecycle,
                    ForeshadowLifecycle.id == ForeshadowPlanItem.lifecycle_id,
                )
                .where(
                    ForeshadowPlanItem.project_id == project_id,
                    ForeshadowPlanItem.target_type == "part",
                    ForeshadowPlanItem.target_id == part.id,
                    ForeshadowPlanItem.status == "active",
                    ForeshadowLifecycle.status == "active",
                )
            )
            if active_foreshadow_count:
                raise PlanningWriteError(
                    "PLANNING_SCOPE_HAS_ACTIVE_FORESHADOWS",
                    "该篇章仍被伏笔计划使用，请先调整或取消伏笔计划。",
                    recommended_action="review_foreshadow_plans",
                    extra={"active_foreshadow_count": active_foreshadow_count},
                )
            part.status = "archived"
            part.lock_version += 1
        return {
            "changed": changed,
            "affected_node": node_snapshot(part),
            "placement": {
                "previous_position": previous_position,
                "current_position": part.position,
                "position_changed": previous_position != part.position,
            },
        }

    return await _run_write(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        operation_key=body.operation_key,
        operation_type=operation_type,
        target_id=part_id,
        expected_structure_version=body.expected_structure_version,
        fingerprint_payload=body.model_dump(mode="json", exclude={"operation_key"}),
        mutate=mutate,
    )


@router.post("/parts/{part_id}/archive", response_model=PlanningMutationReceipt)
async def archive_planning_part(
    project_id: str,
    part_id: str,
    body: PlanningMutationCommand,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    return await _change_part_archive_state(
        project_id=project_id,
        part_id=part_id,
        body=body,
        db=db,
        current_user=current_user,
        restore=False,
    )


@router.post("/parts/{part_id}/restore", response_model=PlanningMutationReceipt)
async def restore_planning_part(
    project_id: str,
    part_id: str,
    body: PlanningMutationCommand,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    return await _change_part_archive_state(
        project_id=project_id,
        part_id=part_id,
        body=body,
        db=db,
        current_user=current_user,
        restore=True,
    )


@router.post("/parts/{part_id}/chapters", response_model=PlanningMutationReceipt)
async def create_planning_chapter(
    project_id: str,
    part_id: str,
    body: PlanningChapterCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)

    async def mutate(plan: NovelPlan) -> dict[str, Any]:
        part = await db.scalar(
            select(PlanningPart)
            .where(
                PlanningPart.project_id == project_id,
                PlanningPart.plan_id == plan.id,
                PlanningPart.id == part_id,
            )
            .with_for_update()
        )
        if part is None:
            raise _node_not_found("篇章")
        if part.status != "active":
            raise PlanningWriteError(
                "PLANNING_PARENT_ARCHIVED",
                "该篇章已归档，暂时不能新增章节。",
                recommended_action="restore_parent",
            )
        position = await next_active_position(db, PlanningChapter, part_id=part.id)
        chapter = PlanningChapter(
            project_id=project_id,
            plan_id=plan.id,
            part_id=part.id,
            title=body.title,
            summary=body.summary,
            target_word_count=body.target_word_count,
            position=position,
        )
        db.add(chapter)
        await db.flush()
        return {
            "affected_node": node_snapshot(chapter),
            "placement": {
                "current_part_id": part.id,
                "current_position": position,
            },
        }

    return await _run_write(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        operation_key=body.operation_key,
        operation_type="chapter_create",
        target_id=part_id,
        expected_structure_version=body.expected_structure_version,
        fingerprint_payload=body.model_dump(mode="json", exclude={"operation_key"}),
        mutate=mutate,
    )


@router.patch("/chapters/{chapter_id}", response_model=PlanningMutationReceipt)
async def update_planning_chapter(
    project_id: str,
    chapter_id: str,
    body: PlanningChapterUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)

    async def mutate(plan: NovelPlan) -> dict[str, Any]:
        chapter = await db.scalar(
            select(PlanningChapter)
            .where(
                PlanningChapter.project_id == project_id,
                PlanningChapter.plan_id == plan.id,
                PlanningChapter.id == chapter_id,
            )
            .with_for_update()
        )
        if chapter is None:
            raise _node_not_found("章节")
        if chapter.lock_version != body.expected_lock_version:
            raise _node_version_conflict(chapter)
        title = chapter.title if body.title is None else body.title
        summary = chapter.summary if body.summary is None else body.summary
        target_words = (
            None
            if body.clear_target_word_count
            else chapter.target_word_count
            if body.target_word_count is None
            else body.target_word_count
        )
        changed = (
            chapter.title != title
            or chapter.summary != summary
            or chapter.target_word_count != target_words
        )
        if changed:
            chapter.title = title
            chapter.summary = summary
            chapter.target_word_count = target_words
            chapter.lock_version += 1
        return {"changed": changed, "affected_node": node_snapshot(chapter)}

    return await _run_write(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        operation_key=body.operation_key,
        operation_type="chapter_update",
        target_id=chapter_id,
        expected_structure_version=body.expected_structure_version,
        fingerprint_payload=body.model_dump(mode="json", exclude={"operation_key"}),
        mutate=mutate,
    )


async def _change_chapter_archive_state(
    *,
    project_id: str,
    chapter_id: str,
    body: PlanningMutationCommand,
    db: AsyncSession,
    current_user: User,
    restore: bool,
) -> dict[str, Any]:
    await get_project_for_owner(project_id, current_user, db)
    operation_type = "chapter_restore" if restore else "chapter_archive"

    async def mutate(plan: NovelPlan) -> dict[str, Any]:
        parent_id: str | None = None
        part: PlanningPart | None = None
        if restore:
            parent_id = await db.scalar(
                select(PlanningChapter.part_id).where(
                    PlanningChapter.project_id == project_id,
                    PlanningChapter.plan_id == plan.id,
                    PlanningChapter.id == chapter_id,
                )
            )
            if parent_id is None:
                raise _node_not_found("章节")
            part = await db.scalar(
                select(PlanningPart)
                .where(
                    PlanningPart.project_id == project_id,
                    PlanningPart.plan_id == plan.id,
                    PlanningPart.id == parent_id,
                )
                .with_for_update()
            )
            if part is None:
                raise _node_not_found("篇章")
        chapter = await db.scalar(
            select(PlanningChapter)
            .where(
                PlanningChapter.project_id == project_id,
                PlanningChapter.plan_id == plan.id,
                PlanningChapter.id == chapter_id,
            )
            .with_for_update()
        )
        if chapter is None or (restore and chapter.part_id != parent_id):
            raise _node_not_found("章节")
        previous_position = chapter.position
        target_status = "active" if restore else "archived"
        changed = chapter.status != target_status
        if changed and restore:
            assert part is not None
            if part.status != "active":
                raise PlanningWriteError(
                    "PLANNING_PARENT_ARCHIVED",
                    "原篇章已归档，请先恢复篇章。",
                    recommended_action="restore_parent",
                )
            chapter.position = await next_active_position(
                db, PlanningChapter, part_id=part.id
            )
            chapter.status = "active"
            chapter.lock_version += 1
        elif changed:
            active_assignment_count = await db.scalar(
                select(func.count())
                .select_from(PlanningLoreAssignment)
                .where(
                    PlanningLoreAssignment.project_id == project_id,
                    PlanningLoreAssignment.plan_id == plan.id,
                    PlanningLoreAssignment.scope_type == "chapter",
                    PlanningLoreAssignment.scope_target_id == chapter.id,
                    PlanningLoreAssignment.status == "active",
                )
            )
            if active_assignment_count:
                raise PlanningWriteError(
                    "PLANNING_SCOPE_HAS_ACTIVE_ASSIGNMENTS",
                    "该章节仍有启用中的设定分配，请先移除分配。",
                    recommended_action="remove_assignments_first",
                    extra={"active_assignment_count": active_assignment_count},
                )
            active_plan_count = await db.scalar(
                select(func.count())
                .select_from(ForeshadowPlanItem)
                .join(
                    ForeshadowLifecycle,
                    ForeshadowLifecycle.id == ForeshadowPlanItem.lifecycle_id,
                )
                .where(
                    ForeshadowPlanItem.project_id == project_id,
                    ForeshadowPlanItem.target_type == "chapter",
                    ForeshadowPlanItem.target_id == chapter.id,
                    ForeshadowPlanItem.status == "active",
                    ForeshadowLifecycle.status == "active",
                )
            )
            active_fact_count = await db.scalar(
                select(func.count())
                .select_from(ForeshadowFact)
                .join(
                    ForeshadowLifecycle,
                    ForeshadowLifecycle.id == ForeshadowFact.lifecycle_id,
                )
                .where(
                    ForeshadowFact.project_id == project_id,
                    ForeshadowFact.chapter_id == chapter.id,
                    ForeshadowFact.status == "active",
                    ForeshadowLifecycle.status == "active",
                )
            )
            if active_plan_count or active_fact_count:
                raise PlanningWriteError(
                    "PLANNING_SCOPE_HAS_ACTIVE_FORESHADOWS",
                    "该章节仍被伏笔计划或作者确认事实使用，不能归档。",
                    recommended_action="review_foreshadow_plans",
                    extra={
                        "active_plan_count": active_plan_count or 0,
                        "active_fact_count": active_fact_count or 0,
                    },
                )
            chapter.status = "archived"
            chapter.lock_version += 1
        return {
            "changed": changed,
            "affected_node": node_snapshot(chapter),
            "placement": {
                "previous_part_id": chapter.part_id,
                "current_part_id": chapter.part_id,
                "previous_position": previous_position,
                "current_position": chapter.position,
                "position_changed": previous_position != chapter.position,
            },
        }

    return await _run_write(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        operation_key=body.operation_key,
        operation_type=operation_type,
        target_id=chapter_id,
        expected_structure_version=body.expected_structure_version,
        fingerprint_payload=body.model_dump(mode="json", exclude={"operation_key"}),
        mutate=mutate,
    )


@router.post("/chapters/{chapter_id}/archive", response_model=PlanningMutationReceipt)
async def archive_planning_chapter(
    project_id: str,
    chapter_id: str,
    body: PlanningMutationCommand,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    return await _change_chapter_archive_state(
        project_id=project_id,
        chapter_id=chapter_id,
        body=body,
        db=db,
        current_user=current_user,
        restore=False,
    )


@router.post("/chapters/{chapter_id}/restore", response_model=PlanningMutationReceipt)
async def restore_planning_chapter(
    project_id: str,
    chapter_id: str,
    body: PlanningMutationCommand,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    return await _change_chapter_archive_state(
        project_id=project_id,
        chapter_id=chapter_id,
        body=body,
        db=db,
        current_user=current_user,
        restore=True,
    )


@router.post("/structure/reorder", response_model=PlanningMutationReceipt)
async def reorder_planning_structure(
    project_id: str,
    body: PlanningStructureReorder,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    requested_parts = [part.model_dump(mode="json") for part in body.parts]

    async def mutate(plan: NovelPlan) -> dict[str, Any]:
        return await reorder_active_structure(db, plan, requested_parts)

    return await _run_write(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        operation_key=body.operation_key,
        operation_type="structure_reorder",
        target_id=None,
        expected_structure_version=body.expected_structure_version,
        fingerprint_payload=body.model_dump(mode="json", exclude={"operation_key"}),
        mutate=mutate,
    )


async def _assignment_scope_response(
    db: AsyncSession,
    plan: NovelPlan,
    scope_type: str,
    scope_target_id: str,
) -> dict[str, Any]:
    scope, part, _ = await resolve_scope(
        db, plan, scope_type, scope_target_id, lock=False
    )
    source_filters = [
        (
            PlanningLoreAssignment.scope_type == "novel",
            PlanningLoreAssignment.scope_target_id == plan.project_id,
        )
    ]
    if scope_type in {"part", "chapter"}:
        assert part is not None
        source_filters.append(
            (
                PlanningLoreAssignment.scope_type == "part",
                PlanningLoreAssignment.scope_target_id == part.id,
            )
        )
    if scope_type == "chapter":
        source_filters.append(
            (
                PlanningLoreAssignment.scope_type == "chapter",
                PlanningLoreAssignment.scope_target_id == scope_target_id,
            )
        )
    source_condition = or_(
        *[
            (scope_column & target_column)
            for scope_column, target_column in source_filters
        ]
    )
    direct = list(
        (
            await db.scalars(
                select(PlanningLoreAssignment)
                .where(
                    PlanningLoreAssignment.project_id == plan.project_id,
                    PlanningLoreAssignment.plan_id == plan.id,
                    PlanningLoreAssignment.scope_type == scope_type,
                    PlanningLoreAssignment.scope_target_id == scope_target_id,
                )
                .order_by(PlanningLoreAssignment.created_at, PlanningLoreAssignment.id)
            )
        ).all()
    )
    active_sources = list(
        (
            await db.scalars(
                select(PlanningLoreAssignment)
                .where(
                    PlanningLoreAssignment.project_id == plan.project_id,
                    PlanningLoreAssignment.plan_id == plan.id,
                    PlanningLoreAssignment.status == "active",
                    source_condition,
                )
                .order_by(
                    PlanningLoreAssignment.element_id,
                    PlanningLoreAssignment.scope_type,
                    PlanningLoreAssignment.created_at,
                    PlanningLoreAssignment.id,
                )
            )
        ).all()
    )
    all_assignments = {item.id: item for item in [*direct, *active_sources]}
    element_ids = sorted({item.element_id for item in all_assignments.values()})
    elements = list(
        (
            await db.scalars(
                select(SettingElement).where(
                    SettingElement.project_id == plan.project_id,
                    SettingElement.id.in_(element_ids),
                )
            )
        ).all()
    ) if element_ids else []
    element_by_id = {element.id: element for element in elements}
    type_ids = sorted({element.type_id for element in elements})
    setting_types = list(
        (
            await db.scalars(
                select(SettingType).where(
                    SettingType.project_id == plan.project_id,
                    SettingType.id.in_(type_ids),
                )
            )
        ).all()
    ) if type_ids else []
    type_by_id = {setting_type.id: setting_type for setting_type in setting_types}

    scope_refs = await _load_assignment_scope_refs(
        db, plan, list(all_assignments.values())
    )

    def snapshot(item: PlanningLoreAssignment) -> dict[str, Any]:
        element = element_by_id.get(item.element_id)
        setting_type = type_by_id.get(element.type_id) if element else None
        if element is None or setting_type is None:
            raise PlanningWriteError(
                "PLANNING_ASSIGNMENT_CORRUPT",
                "分配记录与设定不一致，系统已停止自动处理。",
                recommended_action="contact_support",
            )
        return assignment_snapshot(
            item,
            element,
            setting_type,
            scope_refs[(item.scope_type, item.scope_target_id)],
        )

    direct_snapshots = [snapshot(item) for item in direct]
    if scope["status"] != "active":
        for item in direct_snapshots:
            if "scope_archived" not in item["ineligible_reasons"]:
                item["ineligible_reasons"].append("scope_archived")
            item["generation_eligible"] = False
    grouped: dict[str, list[PlanningLoreAssignment]] = {}
    for item in active_sources:
        grouped.setdefault(item.element_id, []).append(item)
    effective: list[dict[str, Any]] = []
    for element_id in sorted(grouped):
        sources = grouped[element_id]
        first = snapshot(sources[0])
        source_order = {"novel": 0, "part": 1, "chapter": 2}
        sources = sorted(
            sources,
            key=lambda item: (
                source_order[item.scope_type],
                item.created_at,
                item.id,
            ),
        )
        source_snapshots = [
            {
                "assignment_id": item.id,
                "scope": scope_refs[(item.scope_type, item.scope_target_id)],
                "lock_version": item.lock_version,
                "assigned_at_content_version": item.element_content_version,
            }
            for item in sources
        ]
        reasons = list(first["ineligible_reasons"])
        if scope["status"] != "active" and "scope_archived" not in reasons:
            reasons.append("scope_archived")
        current_sources = [
            item
            for item in source_snapshots
            if item["scope"]["scope_type"] == scope_type
            and item["scope"]["scope_target_id"] == scope_target_id
        ]
        effective.append(
            {
                "element_id": first["element_id"],
                "current_content_version": first["current_content_version"],
                "content_changed_since_any_assignment": any(
                    item.element_content_version
                    != first["current_content_version"]
                    for item in sources
                ),
                "element": first["element"],
                "direct_assignments": current_sources,
                "inherited_from": [
                    item for item in source_snapshots if item not in current_sources
                ],
                "all_sources": source_snapshots,
                "generation_eligible": not reasons,
                "ineligible_reasons": reasons,
            }
        )
    return {
        "scope": scope,
        "assignment_version": plan.assignment_version,
        "direct_assignments": direct_snapshots,
        "effective_elements": effective,
        "counts": {
            "direct": len(direct_snapshots),
            "direct_active": sum(item["status"] == "active" for item in direct_snapshots),
            "direct_removed": sum(item["status"] == "removed" for item in direct_snapshots),
            "effective": len(effective),
            "generation_eligible": sum(
                item["generation_eligible"] for item in effective
            ),
            "ineligible": sum(not item["generation_eligible"] for item in effective),
        },
    }


async def _load_assignment_scope_refs(
    db: AsyncSession,
    plan: NovelPlan,
    assignments: list[PlanningLoreAssignment],
) -> dict[tuple[str, str], dict[str, Any]]:
    refs: dict[tuple[str, str], dict[str, Any]] = {
        ("novel", plan.project_id): {
            "scope_type": "novel",
            "scope_target_id": plan.project_id,
            "title": "整部小说",
            "status": plan.status,
            "part_id": None,
        }
    }
    part_ids = {
        item.scope_target_id for item in assignments if item.scope_type == "part"
    }
    chapter_ids = {
        item.scope_target_id for item in assignments if item.scope_type == "chapter"
    }
    chapters = list(
        (
            await db.scalars(
                select(PlanningChapter).where(
                    PlanningChapter.project_id == plan.project_id,
                    PlanningChapter.plan_id == plan.id,
                    PlanningChapter.id.in_(chapter_ids),
                )
            )
        ).all()
    ) if chapter_ids else []
    part_ids.update(chapter.part_id for chapter in chapters)
    parts = list(
        (
            await db.scalars(
                select(PlanningPart).where(
                    PlanningPart.project_id == plan.project_id,
                    PlanningPart.plan_id == plan.id,
                    PlanningPart.id.in_(part_ids),
                )
            )
        ).all()
    ) if part_ids else []
    part_by_id = {part.id: part for part in parts}
    for part in parts:
        refs[("part", part.id)] = {
            "scope_type": "part",
            "scope_target_id": part.id,
            "title": part.title,
            "status": part.status,
            "part_id": part.id,
        }
    for chapter in chapters:
        part = part_by_id.get(chapter.part_id)
        if part is None:
            raise PlanningWriteError(
                "PLANNING_ASSIGNMENT_CORRUPT",
                "分配记录的章节归属不完整，系统已停止自动处理。",
                recommended_action="contact_support",
            )
        refs[("chapter", chapter.id)] = {
            "scope_type": "chapter",
            "scope_target_id": chapter.id,
            "title": chapter.title,
            "status": (
                "active"
                if part.status == "active" and chapter.status == "active"
                else "archived"
            ),
            "part_id": part.id,
        }
    expected = {(item.scope_type, item.scope_target_id) for item in assignments}
    if not expected.issubset(refs):
        raise PlanningWriteError(
            "PLANNING_ASSIGNMENT_CORRUPT",
            "分配记录的作用范围不完整，系统已停止自动处理。",
            recommended_action="contact_support",
        )
    return refs


@router.get(
    "/lore-assignments",
    response_model=PlanningAssignmentScopeResponse,
)
async def get_lore_assignments(
    project_id: str,
    scope_type: str,
    scope_target_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    plan = await db.scalar(select(NovelPlan).where(NovelPlan.project_id == project_id))
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail={
                **_error(_PLANNING_NOT_INITIALIZED, "章节规划尚未创建。"),
                "recommended_action": "initialize_planning",
            },
        )
    if scope_type not in {"novel", "part", "chapter"}:
        raise HTTPException(status_code=422, detail="scope_type 无效")
    try:
        return await _assignment_scope_response(
            db, plan, scope_type, scope_target_id
        )
    except PlanningWriteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post(
    "/lore-assignments",
    response_model=PlanningAssignmentMutationReceipt,
)
async def create_lore_assignment(
    project_id: str,
    body: PlanningAssignmentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)

    async def mutate(plan: NovelPlan) -> dict[str, Any]:
        scope, part, chapter = await resolve_scope(
            db,
            plan,
            body.scope_type,
            body.scope_target_id,
            lock=True,
        )
        require_active_scope(scope)
        element, setting_type = await load_element(
            db, project_id, body.element_id, lock=True
        )
        if element.content_version != body.expected_element_content_version:
            raise PlanningWriteError(
                "PLANNING_ELEMENT_VERSION_CONFLICT",
                "设定内容已更新，请核对后重试。",
                recommended_action="review_lore_element",
                extra={"current_element_content_version": element.content_version},
            )
        require_eligible(element, setting_type)
        existing = await db.scalar(
            select(PlanningLoreAssignment)
            .where(
                PlanningLoreAssignment.project_id == project_id,
                PlanningLoreAssignment.element_id == element.id,
                PlanningLoreAssignment.scope_type == body.scope_type,
                PlanningLoreAssignment.scope_target_id == body.scope_target_id,
            )
            .with_for_update()
        )
        if existing is not None:
            code = (
                "PLANNING_ASSIGNMENT_REMOVED"
                if existing.status == "removed"
                else "PLANNING_ASSIGNMENT_EXISTS"
            )
            action = (
                "restore_assignment"
                if existing.status == "removed"
                else "review_current_assignment"
            )
            raise PlanningWriteError(
                code,
                "该设定在当前范围已有分配记录。",
                recommended_action=action,
                extra={"assignment_id": existing.id},
            )
        assignment = PlanningLoreAssignment(
            project_id=project_id,
            plan_id=plan.id,
            element_id=element.id,
            scope_type=body.scope_type,
            scope_target_id=body.scope_target_id,
            part_id=part.id if body.scope_type == "part" and part else None,
            chapter_id=chapter.id if chapter else None,
            element_content_version=element.content_version,
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        db.add(assignment)
        await db.flush()
        event = PlanningLoreAssignmentEvent(
            id=uuid.uuid4().hex,
            project_id=project_id,
            assignment_id=assignment.id,
            performed_by=current_user.id,
            action="assign",
            previous_status=None,
            new_status="active",
            previous_lock_version=0,
            new_lock_version=1,
            element_content_version=element.content_version,
        )
        db.add(event)
        await db.flush()
        return {
            "assignment": assignment_snapshot(
                assignment, element, setting_type, scope
            ),
            "event_id": event.id,
        }

    return await _run_assignment_write(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        operation_key=body.operation_key,
        operation_type="assignment_create",
        target_id=body.scope_target_id,
        expected_assignment_version=body.expected_assignment_version,
        fingerprint_payload=body.model_dump(mode="json", exclude={"operation_key"}),
        mutate=mutate,
    )


async def _run_assignment_write(**kwargs: Any) -> dict[str, Any]:
    try:
        return await execute_assignment_operation(**kwargs)
    except PlanningWriteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def _change_assignment_state(
    *,
    project_id: str,
    assignment_id: str,
    body: PlanningAssignmentCommand,
    db: AsyncSession,
    current_user: User,
    restore: bool,
) -> dict[str, Any]:
    await get_project_for_owner(project_id, current_user, db)
    operation_type = "assignment_restore" if restore else "assignment_remove"

    async def mutate(plan: NovelPlan) -> dict[str, Any]:
        identity = await db.scalar(
            select(PlanningLoreAssignment).where(
                PlanningLoreAssignment.project_id == project_id,
                PlanningLoreAssignment.plan_id == plan.id,
                PlanningLoreAssignment.id == assignment_id,
            )
        )
        if identity is None:
            raise PlanningWriteError(
                "PLANNING_ASSIGNMENT_NOT_FOUND",
                "分配记录不存在。",
                status_code=404,
                recommended_action="refresh_assignments",
            )
        scope, _, _ = await resolve_scope(
            db,
            plan,
            identity.scope_type,
            identity.scope_target_id,
            lock=True,
        )
        if (
            identity.scope_type != body.scope_type
            or identity.scope_target_id != body.scope_target_id
        ):
            raise PlanningWriteError(
                "PLANNING_ASSIGNMENT_INHERITED_READ_ONLY",
                "该设定来自上级作用范围，请前往来源处修改。",
                recommended_action="open_source_scope",
                extra={"source_scope": scope},
            )
        element, setting_type = await load_element(
            db, project_id, identity.element_id, lock=True
        )
        assignment = await db.scalar(
            select(PlanningLoreAssignment)
            .where(
                PlanningLoreAssignment.project_id == project_id,
                PlanningLoreAssignment.id == assignment_id,
                PlanningLoreAssignment.scope_type == identity.scope_type,
                PlanningLoreAssignment.scope_target_id == identity.scope_target_id,
                PlanningLoreAssignment.element_id == identity.element_id,
            )
            .with_for_update()
        )
        if assignment is None:
            raise PlanningWriteError(
                "PLANNING_ASSIGNMENT_NOT_FOUND",
                "分配记录已变更，请刷新。",
                status_code=404,
                recommended_action="refresh_assignments",
            )
        if assignment.lock_version != body.expected_lock_version:
            raise PlanningWriteError(
                "PLANNING_ASSIGNMENT_LOCK_CONFLICT",
                "分配记录已被更新，请核对。",
                recommended_action="review_current_assignment",
                extra={
                    "current_assignment": assignment_snapshot(
                        assignment, element, setting_type, scope
                    )
                },
            )
        if restore:
            require_active_scope(scope)
            require_eligible(element, setting_type)
            if assignment.status == "active":
                raise PlanningWriteError(
                    "PLANNING_ASSIGNMENT_ACTIVE",
                    "该分配当前已启用。",
                    recommended_action="review_current_assignment",
                )
            action = "restore"
            new_status = "active"
        else:
            if assignment.status == "removed":
                raise PlanningWriteError(
                    "PLANNING_ASSIGNMENT_REMOVED",
                    "该分配已移除。",
                    recommended_action="restore_assignment",
                )
            action = "remove"
            new_status = "removed"
        previous_status = assignment.status
        previous_lock = assignment.lock_version
        assignment.status = new_status
        assignment.lock_version += 1
        assignment.updated_by = current_user.id
        if restore:
            assignment.element_content_version = element.content_version
        event = PlanningLoreAssignmentEvent(
            id=uuid.uuid4().hex,
            project_id=project_id,
            assignment_id=assignment.id,
            performed_by=current_user.id,
            action=action,
            previous_status=previous_status,
            new_status=new_status,
            previous_lock_version=previous_lock,
            new_lock_version=assignment.lock_version,
            element_content_version=element.content_version,
        )
        db.add(event)
        await db.flush()
        return {
            "assignment": assignment_snapshot(
                assignment, element, setting_type, scope
            ),
            "event_id": event.id,
        }

    return await _run_assignment_write(
        db=db,
        project_id=project_id,
        user_id=current_user.id,
        operation_key=body.operation_key,
        operation_type=operation_type,
        target_id=assignment_id,
        expected_assignment_version=body.expected_assignment_version,
        fingerprint_payload=body.model_dump(mode="json", exclude={"operation_key"}),
        mutate=mutate,
    )


@router.post(
    "/lore-assignments/{assignment_id}/remove",
    response_model=PlanningAssignmentMutationReceipt,
)
async def remove_lore_assignment(
    project_id: str,
    assignment_id: str,
    body: PlanningAssignmentCommand,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    return await _change_assignment_state(
        project_id=project_id,
        assignment_id=assignment_id,
        body=body,
        db=db,
        current_user=current_user,
        restore=False,
    )


@router.post(
    "/lore-assignments/{assignment_id}/restore",
    response_model=PlanningAssignmentMutationReceipt,
)
async def restore_lore_assignment(
    project_id: str,
    assignment_id: str,
    body: PlanningAssignmentCommand,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    return await _change_assignment_state(
        project_id=project_id,
        assignment_id=assignment_id,
        body=body,
        db=db,
        current_user=current_user,
        restore=True,
    )


@router.get(
    "/lore-assignments/history",
    response_model=PlanningAssignmentHistoryResponse,
)
async def get_lore_assignment_history(
    project_id: str,
    element_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    element = await db.scalar(
        select(SettingElement).where(
            SettingElement.project_id == project_id,
            SettingElement.id == element_id,
        )
    )
    if element is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "PLANNING_ELEMENT_NOT_FOUND",
                "message": "设定不存在或不属于当前项目。",
                "retryable": False,
                "recommended_action": "refresh_lore_repository",
            },
        )
    plan = await db.scalar(select(NovelPlan).where(NovelPlan.project_id == project_id))
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail={
                **_error(_PLANNING_NOT_INITIALIZED, "章节规划尚未创建。"),
                "recommended_action": "initialize_planning",
            },
        )
    assignments = list(
        (
            await db.scalars(
                select(PlanningLoreAssignment)
                .where(
                    PlanningLoreAssignment.project_id == project_id,
                    PlanningLoreAssignment.element_id == element_id,
                )
                .order_by(PlanningLoreAssignment.created_at, PlanningLoreAssignment.id)
            )
        ).all()
    )
    assignment_ids = [item.id for item in assignments]
    try:
        scope_refs = await _load_assignment_scope_refs(db, plan, assignments)
    except PlanningWriteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    events = list(
        (
            await db.scalars(
                select(PlanningLoreAssignmentEvent)
                .where(
                    PlanningLoreAssignmentEvent.project_id == project_id,
                    PlanningLoreAssignmentEvent.assignment_id.in_(assignment_ids),
                )
                .order_by(
                    PlanningLoreAssignmentEvent.created_at,
                    PlanningLoreAssignmentEvent.id,
                )
            )
        ).all()
    ) if assignment_ids else []
    events_by_assignment: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        events_by_assignment.setdefault(event.assignment_id, []).append(
            {
                "id": event.id,
                "action": event.action,
                "previous_status": event.previous_status,
                "new_status": event.new_status,
                "previous_lock_version": event.previous_lock_version,
                "new_lock_version": event.new_lock_version,
                "element_content_version": event.element_content_version,
                "performed_by": event.performed_by,
                "created_at": event.created_at.isoformat(),
            }
        )
    return {
        "element_id": element_id,
        "assignments": [
            {
                "id": item.id,
                "scope": scope_refs[(item.scope_type, item.scope_target_id)],
                "status": item.status,
                "lock_version": item.lock_version,
                "events": events_by_assignment.get(item.id, []),
            }
            for item in assignments
        ],
    }
