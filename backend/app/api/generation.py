"""Durable, zero-LLM preparation endpoints for relational chapters."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import User, get_current_user, get_project_for_owner
from app.core.generation_preflight import (
    GenerationPreparationError,
    find_generation_run_by_key,
    generation_run_response,
    prepare_generation_run,
)
from app.database import get_db
from app.models.generation import ChapterGenerationRun
from app.schemas.generation import GenerationRunPrepareCommand, GenerationRunResponse


router = APIRouter(
    prefix="/api/projects/{project_id}/planning",
    tags=["generation-preflight"],
)


def _raise(error: GenerationPreparationError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/chapters/{chapter_id}/generation-runs",
    response_model=GenerationRunResponse,
)
async def prepare_chapter_generation(
    project_id: str,
    chapter_id: Annotated[str, Path(min_length=32, max_length=32)],
    body: GenerationRunPrepareCommand,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    try:
        return await prepare_generation_run(
            db=db,
            project_id=project_id,
            user_id=current_user.id,
            chapter_id=chapter_id,
            operation_key=body.operation_key,
            expected_structure_version=body.expected_structure_version,
            expected_assignment_version=body.expected_assignment_version,
            expected_chapter_lock_version=body.expected_chapter_lock_version,
        )
    except GenerationPreparationError as exc:
        _raise(exc)


@router.get(
    "/generation-runs/by-key/{operation_key}",
    response_model=GenerationRunResponse,
)
async def get_generation_run_by_key(
    project_id: str,
    operation_key: Annotated[
        str,
        Path(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    run = await find_generation_run_by_key(
        db, project_id, current_user.id, operation_key
    )
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "GENERATION_RUN_NOT_FOUND",
                "message": "尚未找到该生成准备记录，请使用原请求安全重试。",
                "retryable": True,
                "recommended_action": "retry_original_prepare",
            },
        )
    try:
        return generation_run_response(run, replayed=True)
    except GenerationPreparationError as exc:
        _raise(exc)


@router.get(
    "/generation-runs/{run_id}",
    response_model=GenerationRunResponse,
)
async def get_generation_run(
    project_id: str,
    run_id: Annotated[str, Path(min_length=32, max_length=32)],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    run = await db.scalar(
        select(ChapterGenerationRun).where(
            ChapterGenerationRun.project_id == project_id,
            ChapterGenerationRun.requested_by == current_user.id,
            ChapterGenerationRun.id == run_id,
        )
    )
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "GENERATION_RUN_NOT_FOUND",
                "message": "未找到该生成准备记录。",
                "retryable": False,
                "recommended_action": "return_to_chapter_planning",
            },
        )
    try:
        return generation_run_response(run, replayed=True)
    except GenerationPreparationError as exc:
        _raise(exc)
