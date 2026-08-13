"""Authenticated, non-production demo fixture API."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import User, get_current_user
from app.core.demo_fixture import (
    DemoFixtureDivergedError,
    DemoFixtureUnavailableError,
    bootstrap_demo_fixture,
    ensure_demo_fixture_environment,
    get_demo_fixture_current,
)
from app.core.demo_fixture_store import fixture_ids
from app.core.demo_generation import (
    TechnicalDemoAdapter,
    TechnicalDemoError,
    execute_technical_demo,
    find_technical_demo_execution_by_key,
    get_technical_demo_adapter,
    technical_demo_candidate_response,
    technical_demo_capability_response,
    technical_demo_execution_response,
)
from app.database import get_db
from app.models.generation import ChapterGenerationCandidate
from app.schemas.demo import (
    DemoFixtureBootstrapCommand,
    DemoFixtureBootstrapResponse,
    DemoFixtureCurrentResponse,
)
from app.schemas.demo_generation import (
    TechnicalDemoCandidateResponse,
    TechnicalDemoCapabilityResponse,
    TechnicalDemoExecuteCommand,
    TechnicalDemoExecutionResponse,
)

router = APIRouter(prefix="/api/demo/v1", tags=["demo"])


def _ensure_available(db: AsyncSession) -> None:
    try:
        ensure_demo_fixture_environment(db)
    except DemoFixtureUnavailableError as exc:
        raise HTTPException(status_code=404, detail="资源不存在") from exc


def _raise_technical(error: TechnicalDemoError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get("/fixture", response_model=DemoFixtureCurrentResponse)
async def current_fixture(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _ensure_available(db)
    return await get_demo_fixture_current(db, current_user.id)


@router.post("/bootstrap", response_model=DemoFixtureBootstrapResponse)
async def bootstrap(
    _command: DemoFixtureBootstrapCommand,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _ensure_available(db)

    try:
        return await bootstrap_demo_fixture(db, current_user.id)
    except DemoFixtureDivergedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEMO_FIXTURE_DIVERGED",
                "message": "样例数据已发生变化，系统不会覆盖或自动修复。",
                "retryable": False,
                "recommended_action": "preserve_existing_fixture",
            },
        ) from exc


@router.get(
    "/projects/{project_id}/planning/generation-runs/{run_id}/"
    "technical-generation-capability",
    response_model=TechnicalDemoCapabilityResponse,
)
async def technical_generation_capability(
    project_id: str,
    run_id: Annotated[str, Path(min_length=32, max_length=32)],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _ensure_available(db)
    try:
        return await technical_demo_capability_response(
            db, project_id, current_user.id, run_id
        )
    except TechnicalDemoError as exc:
        _raise_technical(exc)


@router.post(
    "/projects/{project_id}/planning/generation-runs/{run_id}/"
    "technical-demo-executions",
    response_model=TechnicalDemoExecutionResponse,
)
async def run_technical_demo(
    project_id: str,
    run_id: Annotated[str, Path(min_length=32, max_length=32)],
    body: TechnicalDemoExecuteCommand,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    adapter: Annotated[TechnicalDemoAdapter, Depends(get_technical_demo_adapter)],
):
    _ensure_available(db)
    try:
        return await execute_technical_demo(
            db=db,
            project_id=project_id,
            user_id=current_user.id,
            run_id=run_id,
            operation_key=body.operation_key,
            expected_context_checksum=body.expected_context_checksum,
            expected_capability_checksum=body.expected_capability_checksum,
            adapter=adapter,
        )
    except TechnicalDemoError as exc:
        _raise_technical(exc)


@router.get(
    "/projects/{project_id}/planning/technical-demo-executions/"
    "by-key/{operation_key}",
    response_model=TechnicalDemoExecutionResponse,
)
async def get_technical_demo_by_key(
    project_id: str,
    operation_key: Annotated[
        str,
        Path(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _ensure_available(db)
    execution = await find_technical_demo_execution_by_key(
        db, project_id, current_user.id, operation_key
    )
    if execution is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "TECHNICAL_DEMO_EXECUTION_NOT_FOUND",
                "message": "尚未找到该技术模拟记录。",
                "retryable": True,
                "recommended_action": "retry_original_technical_demo",
            },
        )
    try:
        return await technical_demo_execution_response(db, execution, replayed=True)
    except TechnicalDemoError as exc:
        _raise_technical(exc)


@router.get(
    "/projects/{project_id}/planning/technical-demo-candidates/{candidate_id}",
    response_model=TechnicalDemoCandidateResponse,
)
async def get_technical_demo_candidate(
    project_id: str,
    candidate_id: Annotated[str, Path(min_length=32, max_length=32)],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _ensure_available(db)
    if fixture_ids(current_user.id).project != project_id:
        raise HTTPException(status_code=404, detail="资源不存在")
    candidate = await db.scalar(
        select(ChapterGenerationCandidate).where(
            ChapterGenerationCandidate.project_id == project_id,
            ChapterGenerationCandidate.id == candidate_id,
            ChapterGenerationCandidate.origin_kind == "technical_demo",
        )
    )
    if candidate is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "TECHNICAL_DEMO_CANDIDATE_NOT_FOUND",
                "message": "未找到该技术模拟候选。",
                "retryable": False,
                "recommended_action": "check_technical_demo_by_key",
            },
        )
    try:
        return await technical_demo_candidate_response(
            db, candidate, user_id=current_user.id
        )
    except TechnicalDemoError as exc:
        _raise_technical(exc)
