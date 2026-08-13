"""Durable, zero-LLM preparation endpoints for relational chapters."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import User, get_current_user, get_project_for_owner
from app.core.generation_execution import (
    GenerationExecutionError,
    GenerationTransport,
    execute_generation_attempt,
    find_generation_attempt_by_key,
    generation_candidate_response,
    generation_capability_response,
    generation_attempt_response,
    get_generation_transport,
)
from app.core.generation_candidates import (
    create_manual_edit_candidate,
    find_manual_edit_by_key,
    get_candidate_version_detail,
    list_candidate_versions,
    manual_edit_response,
)
from app.core.generation_audit import generation_candidate_audit_response
from app.core.generation_preflight import (
    GenerationPreparationError,
    find_generation_run_by_key,
    generation_run_response,
    prepare_generation_run,
)
from app.database import get_db
from app.models.generation import ChapterGenerationCandidate, ChapterGenerationRun
from app.schemas.generation import (
    GenerationAttemptExecuteCommand,
    GenerationAttemptResponse,
    GenerationCandidateResponse,
    GenerationCandidateAuditResponse,
    GenerationCandidateManualEditCommand,
    GenerationCandidateManualEditResponse,
    GenerationCandidateVersionDetail,
    GenerationCandidateVersionListResponse,
    GenerationCapabilityResponse,
    GenerationRunPrepareCommand,
    GenerationRunResponse,
)


router = APIRouter(
    prefix="/api/projects/{project_id}/planning",
    tags=["generation-preflight"],
)


def _raise(error: GenerationPreparationError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.detail) from error


def _raise_execution(error: GenerationExecutionError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "/generation-capabilities/current",
    response_model=GenerationCapabilityResponse,
)
async def get_current_generation_capability(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    transport: Annotated[GenerationTransport, Depends(get_generation_transport)],
):
    await get_project_for_owner(project_id, current_user, db)
    try:
        return generation_capability_response(transport)
    except GenerationExecutionError as exc:
        _raise_execution(exc)


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


@router.post(
    "/generation-runs/{run_id}/attempts",
    response_model=GenerationAttemptResponse,
)
async def execute_prepared_generation(
    project_id: str,
    run_id: Annotated[str, Path(min_length=32, max_length=32)],
    body: GenerationAttemptExecuteCommand,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    transport: Annotated[GenerationTransport, Depends(get_generation_transport)],
):
    await get_project_for_owner(project_id, current_user, db)
    try:
        return await execute_generation_attempt(
            db=db,
            project_id=project_id,
            user_id=current_user.id,
            run_id=run_id,
            operation_key=body.operation_key,
            expected_context_checksum=body.expected_context_checksum,
            expected_capability_checksum=body.expected_capability_checksum,
            transport=transport,
        )
    except GenerationExecutionError as exc:
        _raise_execution(exc)


@router.get(
    "/generation-candidates/{candidate_id}",
    response_model=GenerationCandidateResponse,
)
async def get_generation_candidate(
    project_id: str,
    candidate_id: Annotated[str, Path(min_length=32, max_length=32)],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    candidate = await db.scalar(
        select(ChapterGenerationCandidate).where(
            ChapterGenerationCandidate.project_id == project_id,
            ChapterGenerationCandidate.id == candidate_id,
        )
    )
    if candidate is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "GENERATION_CANDIDATE_NOT_FOUND",
                "message": "未找到该章节候选。",
                "retryable": False,
                "recommended_action": "check_execution_by_key",
            },
        )
    try:
        return await generation_candidate_response(
            db, candidate, user_id=current_user.id
        )
    except GenerationExecutionError as exc:
        _raise_execution(exc)


@router.get(
    "/generation-runs/{run_id}/candidate-versions",
    response_model=GenerationCandidateVersionListResponse,
)
async def get_generation_candidate_versions(
    project_id: str,
    run_id: Annotated[str, Path(min_length=32, max_length=32)],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    before_version_no: Annotated[int | None, Query(ge=1)] = None,
):
    try:
        return await list_candidate_versions(
            db,
            project_id=project_id,
            run_id=run_id,
            user_id=current_user.id,
            limit=limit,
            before_version_no=before_version_no,
        )
    except GenerationExecutionError as exc:
        _raise_execution(exc)


@router.get(
    "/generation-runs/{run_id}/candidate-versions/{candidate_id}",
    response_model=GenerationCandidateVersionDetail,
)
async def get_generation_candidate_version(
    project_id: str,
    run_id: Annotated[str, Path(min_length=32, max_length=32)],
    candidate_id: Annotated[str, Path(min_length=32, max_length=32)],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    try:
        candidate = await get_candidate_version_detail(
            db,
            project_id=project_id,
            run_id=run_id,
            candidate_id=candidate_id,
            user_id=current_user.id,
        )
    except GenerationExecutionError as exc:
        _raise_execution(exc)
    if candidate is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "GENERATION_CANDIDATE_VERSION_NOT_FOUND",
                "message": "未找到该章节候选版本。",
                "retryable": False,
                "recommended_action": "reload_generation_candidate_versions",
            },
        )
    return candidate


@router.post(
    "/generation-runs/{run_id}/candidate-manual-edits",
    response_model=GenerationCandidateManualEditResponse,
)
async def create_generation_candidate_manual_edit(
    project_id: str,
    run_id: Annotated[str, Path(min_length=32, max_length=32)],
    body: GenerationCandidateManualEditCommand,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    try:
        return await create_manual_edit_candidate(
            db=db,
            project_id=project_id,
            run_id=run_id,
            user_id=current_user.id,
            operation_key=body.operation_key,
            parent_candidate_id=body.parent_candidate_id,
            expected_parent_version_no=body.expected_parent_version_no,
            expected_parent_checksum=body.expected_parent_checksum,
            expected_context_checksum=body.expected_context_checksum,
            content=body.content,
        )
    except GenerationExecutionError as exc:
        _raise_execution(exc)


@router.get(
    "/generation-runs/{run_id}/candidate-manual-edits/by-key/{operation_key}",
    response_model=GenerationCandidateManualEditResponse,
)
async def get_generation_candidate_manual_edit_by_key(
    project_id: str,
    run_id: Annotated[str, Path(min_length=32, max_length=32)],
    operation_key: Annotated[
        str,
        Path(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    candidate = await find_manual_edit_by_key(
        db,
        project_id=project_id,
        run_id=run_id,
        user_id=current_user.id,
        operation_key=operation_key,
    )
    if candidate is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "GENERATION_CANDIDATE_MANUAL_EDIT_NOT_FOUND",
                "message": "尚未找到该手工另存记录。",
                "retryable": True,
                "recommended_action": "retry_original_candidate_manual_edit",
            },
        )
    try:
        return await manual_edit_response(
            db, candidate, user_id=current_user.id, replayed=True
        )
    except GenerationExecutionError as exc:
        _raise_execution(exc)


@router.get(
    "/generation-candidates/{candidate_id}/audit",
    response_model=GenerationCandidateAuditResponse,
)
async def get_generation_candidate_audit(
    project_id: str,
    candidate_id: Annotated[str, Path(min_length=32, max_length=32)],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    candidate = await db.scalar(
        select(ChapterGenerationCandidate).where(
            ChapterGenerationCandidate.project_id == project_id,
            ChapterGenerationCandidate.id == candidate_id,
        )
    )
    if candidate is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "GENERATION_CANDIDATE_NOT_FOUND",
                "message": "未找到可检查的章节候选。",
                "retryable": False,
                "recommended_action": "check_execution_by_key",
            },
        )
    try:
        return await generation_candidate_audit_response(
            db, candidate, user_id=current_user.id
        )
    except GenerationExecutionError as exc:
        _raise_execution(exc)


@router.get(
    "/generation-attempts/by-key/{operation_key}",
    response_model=GenerationAttemptResponse,
)
async def get_generation_attempt_by_key(
    project_id: str,
    operation_key: Annotated[
        str,
        Path(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    attempt = await find_generation_attempt_by_key(
        db, project_id, current_user.id, operation_key
    )
    if attempt is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "GENERATION_ATTEMPT_NOT_FOUND",
                "message": "尚未找到该生成执行记录。",
                "retryable": True,
                "recommended_action": "retry_original_execute",
            },
        )
    try:
        return await generation_attempt_response(db, attempt, replayed=True)
    except GenerationExecutionError as exc:
        _raise_execution(exc)
