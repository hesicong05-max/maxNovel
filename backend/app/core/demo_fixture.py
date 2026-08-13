"""Transactional, owner-scoped bootstrap for the non-production demo fixture."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR, settings
from app.core.demo_fixture_store import (
    DemoFixtureDivergedError,
    DemoFixtureIds,
    add_fixture_rows,
    fixture_ids,
    load_fixture_rows,
    validate_fixture_rows,
)
from app.core.maintenance import ensure_project_writes_available
from app.core.settings_store import load_settings
from app.schemas.demo import (
    DemoFixtureBootstrapResponse,
    DemoFixtureCounts,
    DemoFixtureCurrentResponse,
)


class DemoFixtureUnavailableError(Exception):
    """The server is not an explicitly isolated demo/test environment."""


def _active_database_url(db: AsyncSession) -> str:
    bind = db.get_bind()
    return str(bind.url)


def ensure_demo_fixture_environment(db: AsyncSession) -> None:
    """Fail closed unless the active database and server are explicitly isolated."""

    if (
        not settings.DEMO_FIXTURE_ENABLED
        or settings.APP_ENVIRONMENT not in {"demo", "test"}
        or not settings.DEBUG
        or bool(load_settings().get("api_key"))
    ):
        raise DemoFixtureUnavailableError

    url = make_url(_active_database_url(db))
    if url.get_backend_name() != "sqlite":
        raise DemoFixtureUnavailableError

    database = url.database
    if settings.APP_ENVIRONMENT == "test":
        # Test mode is intentionally narrower than demo mode: only the
        # disposable in-memory database is accepted.
        if database in {None, "", ":memory:"}:
            return
        raise DemoFixtureUnavailableError

    if not database or database == ":memory:":
        raise DemoFixtureUnavailableError

    candidate = Path(database)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    resolved_candidate = candidate.resolve(strict=False)
    resolved_demo_root = (DATA_DIR / "demo").resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_demo_root)
    except ValueError as exc:
        raise DemoFixtureUnavailableError from exc


def _response(ids: DemoFixtureIds, replayed: bool) -> DemoFixtureBootstrapResponse:
    return DemoFixtureBootstrapResponse(
        replayed=replayed,
        project_id=ids.project,
        plan_id=ids.plan,
        part_id=ids.part,
        chapter_id=ids.chapter,
        element_id=ids.element,
        assignment_id=ids.assignment,
        next_path=f"/project/{ids.project}/lore",
    )


async def get_demo_fixture_current(
    db: AsyncSession, user_id: str
) -> DemoFixtureCurrentResponse:
    """Describe the current owner-scoped fixture without creating or repairing it."""

    ids = fixture_ids(user_id)
    rows = await load_fixture_rows(db, user_id)
    if all(row is None for row in rows.values()):
        return DemoFixtureCurrentResponse(
            state="missing",
            can_bootstrap=True,
            preserved=False,
            recommended_action="bootstrap_fixture",
        )

    try:
        counts = await validate_fixture_rows(db, user_id)
    except DemoFixtureDivergedError:
        project = rows["project"]
        preserved_project_id = (
            ids.project if project is not None and project.owner_id == user_id else None
        )
        return DemoFixtureCurrentResponse(
            state="diverged",
            can_bootstrap=False,
            preserved=True,
            project_id=preserved_project_id,
            recommended_action="preserve_existing_fixture",
        )

    return DemoFixtureCurrentResponse(
        state="ready",
        can_bootstrap=False,
        preserved=False,
        project_id=ids.project,
        plan_id=ids.plan,
        part_id=ids.part,
        chapter_id=ids.chapter,
        element_id=ids.element,
        assignment_id=ids.assignment,
        second_chapter_id=ids.second_chapter,
        foreshadow_element_id=ids.foreshadow_element,
        foreshadow_lifecycle_id=ids.foreshadow_lifecycle,
        counts=DemoFixtureCounts(**counts),
        next_path=f"/project/{ids.project}/lore",
        recommended_action="open_fixture",
    )


async def bootstrap_demo_fixture(
    db: AsyncSession, user_id: str
) -> DemoFixtureBootstrapResponse:
    """Create or strictly replay fixture v1 without repairing divergent data."""

    ensure_project_writes_available()
    ids = fixture_ids(user_id)
    existing = await load_fixture_rows(db, user_id)
    if existing["project"] is not None:
        await validate_fixture_rows(db, user_id)
        return _response(ids, replayed=True)
    if any(row is not None for row in existing.values()):
        raise DemoFixtureDivergedError

    try:
        await add_fixture_rows(db, user_id)
        await db.flush()
        ensure_project_writes_available()
        await db.commit()
    except IntegrityError:
        await db.rollback()
        await validate_fixture_rows(db, user_id)
        return _response(ids, replayed=True)
    except Exception:
        await db.rollback()
        raise

    return _response(ids, replayed=False)
