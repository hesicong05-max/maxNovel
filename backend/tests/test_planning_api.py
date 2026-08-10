"""DEV-017A1 safe initialization and read tests for chapter planning."""

import asyncio
from copy import deepcopy

import pytest
from sqlalchemy import func, select, update

from app.config import settings as app_settings
from app.core.legacy_json import read_legacy_json
from app.core.maintenance import ProjectWriteFrozenError
from app.models.planning import NovelPlan, PlanningChapter, PlanningPart
from app.models.project import Chapter, Outline, Project, StoryMemory
from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal


PROJECT_PAYLOAD = {
    "title": "章节规划测试",
    "genre": "玄幻",
    "total_chapters": 10,
    "chapter_word_count": 1500,
    "style_intensity": "standard",
}


async def _create_project(client, headers, *, title="章节规划测试") -> str:
    response = await client.post(
        "/api/projects",
        headers=headers,
        json={**PROJECT_PAYLOAD, "title": title},
    )
    assert response.status_code == 200
    return response.json()["id"]


async def _set_lore_mode(project_id: str, mode: str) -> None:
    async with TestSessionLocal() as session:
        await session.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(lore_storage_mode=mode)
        )
        await session.commit()


async def _plan_count(project_id: str) -> int:
    async with TestSessionLocal() as session:
        return int(
            await session.scalar(
                select(func.count(NovelPlan.id)).where(
                    NovelPlan.project_id == project_id
                )
            )
            or 0
        )


def _assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert detail["retryable"] is False


@pytest.mark.usefixtures("clean_db")
async def test_get_is_read_only_until_explicit_initialization(client, auth_headers):
    project_id = await _create_project(client, auth_headers)

    response = await client.get(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )

    _assert_error(response, 404, "PLANNING_NOT_INITIALIZED")
    assert await _plan_count(project_id) == 0


@pytest.mark.usefixtures("clean_db")
async def test_initialize_is_empty_idempotent_and_readable(client, auth_headers):
    project_id = await _create_project(client, auth_headers)

    first = await client.post(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )
    second = await client.post(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )
    read = await client.get(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )

    assert first.status_code == second.status_code == read.status_code == 200
    assert first.json() == second.json() == read.json()
    assert first.json()["parts"] == []
    assert first.json()["structure_version"] == 1
    assert first.json()["assignment_version"] == 1
    assert await _plan_count(project_id) == 1


@pytest.mark.usefixtures("clean_db")
async def test_concurrent_initialization_creates_exactly_one_plan(
    client, auth_headers
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("row-lock concurrency is verified on PostgreSQL")
    project_id = await _create_project(client, auth_headers)

    first, second = await asyncio.gather(
        client.post(f"/api/projects/{project_id}/planning", headers=auth_headers),
        client.post(f"/api/projects/{project_id}/planning", headers=auth_headers),
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert await _plan_count(project_id) == 1


@pytest.mark.usefixtures("clean_db")
async def test_planning_is_owner_isolated(
    client, auth_headers, second_auth_headers
):
    project_id = await _create_project(client, auth_headers)

    response = await client.post(
        f"/api/projects/{project_id}/planning", headers=second_auth_headers
    )

    assert response.status_code == 403
    assert await _plan_count(project_id) == 0


@pytest.mark.usefixtures("clean_db")
async def test_legacy_lore_project_must_upgrade_before_planning(
    client, auth_headers
):
    project_id = await _create_project(client, auth_headers)
    await _set_lore_mode(project_id, "legacy")

    response = await client.post(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )

    _assert_error(response, 409, "PLANNING_LORE_MIGRATION_REQUIRED")
    assert await _plan_count(project_id) == 0


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize("legacy_kind", ["outline", "chapter", "story_memory"])
async def test_effective_legacy_chapter_data_is_never_auto_migrated(
    client, auth_headers, legacy_kind
):
    project_id = await _create_project(client, auth_headers)
    memory_before = None
    async with TestSessionLocal() as session:
        if legacy_kind == "outline":
            session.add(
                Outline(
                    project_id=project_id,
                    story_arc="原有故事弧",
                    reveal_plan=[],
                    chapters=[],
                )
            )
        elif legacy_kind == "chapter":
            session.add(
                Chapter(
                    project_id=project_id,
                    chapter_num=1,
                    title="原有章节",
                    content="原有正文不得被迁移或覆盖。",
                )
            )
        else:
            memory = StoryMemory(
                project_id=project_id,
                chapter_summaries=[{"chapter_num": 1, "summary": "原有摘要"}],
            )
            session.add(memory)
        await session.commit()
        if legacy_kind == "story_memory":
            await session.refresh(memory)
            memory_before = deepcopy(memory.chapter_summaries)

    response = await client.post(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )

    _assert_error(response, 409, "PLANNING_LEGACY_IMPORT_REQUIRED")
    assert response.json()["detail"]["recommended_action"] == "return_to_project"
    assert "继续使用原章节流程" not in response.text
    assert response.json()["detail"]["reasons"] == [
        {
            "outline": "outline",
            "chapter": "chapter_content",
            "story_memory": "story_memory",
        }[legacy_kind]
    ]
    assert await _plan_count(project_id) == 0
    async with TestSessionLocal() as session:
        if legacy_kind == "outline":
            stored = await session.scalar(
                select(Outline).where(Outline.project_id == project_id)
            )
            assert stored.story_arc == "原有故事弧"
        elif legacy_kind == "chapter":
            stored = await session.scalar(
                select(Chapter).where(Chapter.project_id == project_id)
            )
            assert stored.content == "原有正文不得被迁移或覆盖。"
        else:
            stored = await session.scalar(
                select(StoryMemory).where(StoryMemory.project_id == project_id)
            )
            assert stored.chapter_summaries == memory_before
            decoded = read_legacy_json(stored.chapter_summaries)
            assert decoded.valid is True
            assert decoded.value == [
                {"chapter_num": 1, "summary": "原有摘要"}
            ]


@pytest.mark.usefixtures("clean_db")
async def test_empty_story_memory_does_not_block_new_plan(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    async with TestSessionLocal() as session:
        session.add(StoryMemory(project_id=project_id))
        await session.commit()

    response = await client.post(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["parts"] == []


@pytest.mark.usefixtures("clean_db")
async def test_malformed_legacy_memory_fails_closed_without_disclosure(
    client, auth_headers
):
    project_id = await _create_project(client, auth_headers)
    async with TestSessionLocal() as session:
        session.add(
            StoryMemory(project_id=project_id, timeline="not-json-private-timeline")
        )
        await session.commit()

    response = await client.post(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )

    _assert_error(response, 409, "PLANNING_LEGACY_IMPORT_REQUIRED")
    assert "not-json-private-timeline" not in response.text
    assert await _plan_count(project_id) == 0


@pytest.mark.usefixtures("clean_db")
async def test_initialization_obeys_maintenance_freeze(
    client, auth_headers, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)

    response = await client.post(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )

    assert response.status_code == 503
    assert response.json()["code"] == "PROJECT_WRITE_FROZEN"
    assert await _plan_count(project_id) == 0


@pytest.mark.usefixtures("clean_db")
async def test_maintenance_flip_before_commit_leaves_no_plan(
    client, auth_headers, monkeypatch
):
    import app.api.planning as planning_api

    project_id = await _create_project(client, auth_headers)

    def freeze_before_commit() -> None:
        raise ProjectWriteFrozenError("PROJECT_WRITE_FROZEN")

    monkeypatch.setattr(
        planning_api,
        "ensure_project_writes_available",
        freeze_before_commit,
    )

    response = await client.post(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )

    assert response.status_code == 503
    assert response.json()["code"] == "PROJECT_WRITE_FROZEN"
    assert await _plan_count(project_id) == 0


@pytest.mark.usefixtures("clean_db")
async def test_read_returns_stable_part_and_chapter_order_including_archives(
    client, auth_headers
):
    project_id = await _create_project(client, auth_headers)
    initialized = await client.post(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )
    plan_id = initialized.json()["id"]
    async with TestSessionLocal() as session:
        later = PlanningPart(
            project_id=project_id,
            plan_id=plan_id,
            title="第二篇",
            position=2,
        )
        earlier = PlanningPart(
            project_id=project_id,
            plan_id=plan_id,
            title="第一篇（已归档）",
            position=1,
            status="archived",
        )
        session.add_all([later, earlier])
        await session.flush()
        session.add_all(
            [
                PlanningChapter(
                    project_id=project_id,
                    plan_id=plan_id,
                    part_id=later.id,
                    title="第二章",
                    position=2,
                ),
                PlanningChapter(
                    project_id=project_id,
                    plan_id=plan_id,
                    part_id=later.id,
                    title="第一章",
                    position=1,
                ),
            ]
        )
        await session.commit()

    response = await client.get(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )

    assert response.status_code == 200
    parts = response.json()["parts"]
    assert [part["title"] for part in parts] == ["第一篇（已归档）", "第二篇"]
    assert parts[0]["status"] == "archived"
    assert [chapter["title"] for chapter in parts[1]["chapters"]] == [
        "第一章",
        "第二章",
    ]
