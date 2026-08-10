"""Safe initialization and read API for the relational chapter planning layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
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
from app.database import get_db
from app.models.planning import NovelPlan, PlanningChapter, PlanningPart
from app.models.project import Chapter, Outline, Project, StoryMemory
from app.schemas.planning import (
    NovelPlanResponse,
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
    response_model=PlanningMutationReceipt,
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
