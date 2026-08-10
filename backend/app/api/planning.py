"""Safe initialization and read API for the relational chapter planning layer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import User, get_current_user, get_project_for_owner
from app.core.legacy_json import read_legacy_json
from app.core.maintenance import (
    ensure_project_writes_available,
    require_project_writes_available,
)
from app.database import get_db
from app.models.planning import NovelPlan, PlanningChapter, PlanningPart
from app.models.project import Chapter, Outline, Project, StoryMemory
from app.schemas.planning import (
    NovelPlanResponse,
    PlanningChapterResponse,
    PlanningPartResponse,
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
            ),
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
            ),
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
