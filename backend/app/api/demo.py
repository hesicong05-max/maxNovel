"""Authenticated, non-production demo fixture API."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import User, get_current_user
from app.core.demo_fixture import (
    DemoFixtureDivergedError,
    DemoFixtureUnavailableError,
    bootstrap_demo_fixture,
    ensure_demo_fixture_environment,
    get_demo_fixture_current,
)
from app.database import get_db
from app.schemas.demo import (
    DemoFixtureBootstrapCommand,
    DemoFixtureBootstrapResponse,
    DemoFixtureCurrentResponse,
)

router = APIRouter(prefix="/api/demo/v1", tags=["demo"])


def _ensure_available(db: AsyncSession) -> None:
    try:
        ensure_demo_fixture_environment(db)
    except DemoFixtureUnavailableError as exc:
        raise HTTPException(status_code=404, detail="资源不存在") from exc


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
