"""PostgreSQL locking proof for the isolated Lore migration rehearsal."""

import asyncio

import pytest

from app.config import settings as app_settings
from app.core.lore_migration_rehearsal import (
    LoreMigrationRehearsalError,
    commit_rehearsal,
    compensating_rollback_rehearsal,
    validate_rehearsal,
)
from tests.test_lore_migration_rehearsal import GUARD, _fixture_project
from app.models.project import Worldview
from sqlalchemy import select


@pytest.mark.usefixtures("clean_db")
async def test_postgres_concurrent_same_anchor_creates_one_migration(monkeypatch):
    from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal

    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("PostgreSQL-only row locking proof")
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    _, anchor, _ = await _fixture_project(character_count=25)

    first, second = await asyncio.gather(
        commit_rehearsal(TestSessionLocal, anchor, GUARD),
        commit_rehearsal(TestSessionLocal, anchor, GUARD),
    )

    assert {first.replayed, second.replayed} == {False, True}
    assert first.migration_id == second.migration_id
    assert (await validate_rehearsal(TestSessionLocal, anchor, GUARD))["status"] == "passed"
    await compensating_rollback_rehearsal(TestSessionLocal, anchor, GUARD)


@pytest.mark.usefixtures("clean_db")
async def test_postgres_one_thousand_items_round_trip(monkeypatch):
    from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal

    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("PostgreSQL-only scale proof")
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    _, anchor, preview = await _fixture_project(character_count=1000)

    await commit_rehearsal(TestSessionLocal, anchor, GUARD)
    validation = await validate_rehearsal(TestSessionLocal, anchor, GUARD)
    assert validation["counts"]["elements"] == preview["counts"]["legacy_total"]
    await compensating_rollback_rehearsal(TestSessionLocal, anchor, GUARD)


@pytest.mark.usefixtures("clean_db")
async def test_postgres_worldview_update_waits_for_migration_lock(monkeypatch):
    from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal

    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("PostgreSQL-only Worldview lock proof")
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    project_id, anchor, _ = await _fixture_project(character_count=25)
    lock_acquired = asyncio.Event()
    release_migration = asyncio.Event()

    async def phase_hook(phase: str):
        if phase == "after_source_lock":
            lock_acquired.set()
            await release_migration.wait()

    commit_task = asyncio.create_task(
        commit_rehearsal(
            TestSessionLocal,
            anchor,
            GUARD,
            phase_hook=phase_hook,
        )
    )
    await lock_acquired.wait()

    async def update_worldview():
        async with TestSessionLocal() as session:
            worldview = await session.scalar(
                select(Worldview)
                .where(Worldview.project_id == project_id)
                .with_for_update()
            )
            worldview.characters = [{"name": "锁后修改", "personality": "变化"}]
            await session.commit()

    update_task = asyncio.create_task(update_worldview())
    await asyncio.sleep(0.1)
    assert update_task.done() is False
    release_migration.set()
    await commit_task
    await update_task

    with pytest.raises(LoreMigrationRehearsalError) as error:
        await validate_rehearsal(TestSessionLocal, anchor, GUARD)
    assert getattr(error.value, "code", None) == "REHEARSAL_SOURCE_CHANGED"
