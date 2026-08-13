"""Safety and idempotency gates for the non-production demo fixture."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.engine import make_url

from app.config import settings
from app.core import demo_fixture
from app.core.demo_fixture import bootstrap_demo_fixture, fixture_ids
from app.models.generation import (
    ChapterGenerationAttempt,
    ChapterGenerationCandidate,
    ChapterGenerationRun,
)
from app.models.lore import ElementSource, ElementVersion, SettingElement, SettingType
from app.models.planning import (
    NovelPlan,
    PlanningChapter,
    PlanningLoreAssignment,
    PlanningPart,
)
from app.models.project import Project
from app.models.user import User
from tests.conftest import TestSessionLocal


pytestmark = pytest.mark.usefixtures("clean_db")

_COMMAND = {"fixture_version": 1, "operation_key": "demo:v1:bootstrap"}


@pytest.fixture(autouse=True)
def _enable_isolated_test_gate(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "test")
    monkeypatch.setattr(settings, "DEMO_FIXTURE_ENABLED", True)
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(demo_fixture, "load_settings", lambda: {"api_key": ""})


async def _count(session, model) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _user_id(email: str = "testuser@example.com") -> str:
    async with TestSessionLocal() as session:
        return str(await session.scalar(select(User.id).where(User.email == email)))


async def test_bootstrap_creates_real_fixture_once_and_replays(client, auth_headers):
    first = await client.post("/api/demo/v1/bootstrap", headers=auth_headers, json=_COMMAND)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body == {
        "schema_version": 1,
        "fixture_version": 1,
        "mode": "technical_demo_fixture",
        "environment": "non_production",
        "state": "ready",
        "replayed": False,
        "project_id": body["project_id"],
        "plan_id": body["plan_id"],
        "part_id": body["part_id"],
        "chapter_id": body["chapter_id"],
        "element_id": body["element_id"],
        "assignment_id": body["assignment_id"],
        "next_path": f'/project/{body["project_id"]}/lore',
    }
    assert all(len(body[key]) == 32 for key in (
        "project_id", "plan_id", "part_id", "chapter_id", "element_id", "assignment_id"
    ))

    async with TestSessionLocal() as session:
        before = {
            model: await _count(session, model)
            for model in (
                Project,
                SettingType,
                SettingElement,
                ElementSource,
                ElementVersion,
                NovelPlan,
                PlanningPart,
                PlanningChapter,
                PlanningLoreAssignment,
            )
        }
        assert before == {
            Project: 1,
            SettingType: 1,
            SettingElement: 1,
            ElementSource: 1,
            ElementVersion: 1,
            NovelPlan: 1,
            PlanningPart: 1,
            PlanningChapter: 1,
            PlanningLoreAssignment: 1,
        }
        assert await _count(session, ChapterGenerationAttempt) == 0
        assert await _count(session, ChapterGenerationCandidate) == 0

    second = await client.post("/api/demo/v1/bootstrap", headers=auth_headers, json=_COMMAND)
    assert second.status_code == 200, second.text
    assert second.json() == body | {"replayed": True}

    async with TestSessionLocal() as session:
        after = {model: await _count(session, model) for model in before}
    assert after == before

    current = await client.get("/api/demo/v1/fixture", headers=auth_headers)
    assert current.status_code == 200, current.text
    assert current.json() == {
        "schema_version": 1,
        "fixture_version": 1,
        "mode": "technical_demo_fixture",
        "environment": "non_production",
        "state": "ready",
        "can_bootstrap": False,
        "preserved": False,
        "project_id": body["project_id"],
        "plan_id": body["plan_id"],
        "part_id": body["part_id"],
        "chapter_id": body["chapter_id"],
        "element_id": body["element_id"],
        "assignment_id": body["assignment_id"],
        "next_path": body["next_path"],
        "recommended_action": "open_fixture",
    }


async def test_current_descriptor_reports_missing_without_writing(client, auth_headers):
    response = await client.get("/api/demo/v1/fixture", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "schema_version": 1,
        "fixture_version": 1,
        "mode": "technical_demo_fixture",
        "environment": "non_production",
        "state": "missing",
        "can_bootstrap": True,
        "preserved": False,
        "project_id": None,
        "plan_id": None,
        "part_id": None,
        "chapter_id": None,
        "element_id": None,
        "assignment_id": None,
        "next_path": None,
        "recommended_action": "bootstrap_fixture",
    }
    async with TestSessionLocal() as session:
        assert await _count(session, Project) == 0


async def test_fixture_is_readable_through_existing_lore_and_planning_apis(
    client, auth_headers
):
    created = await client.post(
        "/api/demo/v1/bootstrap", headers=auth_headers, json=_COMMAND
    )
    assert created.status_code == 200, created.text
    body = created.json()

    lore = await client.get(
        f'/api/projects/{body["project_id"]}/lore/elements', headers=auth_headers
    )
    assert lore.status_code == 200, lore.text
    assert [(item["id"], item["name"]) for item in lore.json()["items"]] == [
        (body["element_id"], "沈星")
    ]

    planning = await client.get(
        f'/api/projects/{body["project_id"]}/planning', headers=auth_headers
    )
    assert planning.status_code == 200, planning.text
    assert planning.json()["id"] == body["plan_id"]
    assert planning.json()["parts"][0]["id"] == body["part_id"]
    assert planning.json()["parts"][0]["chapters"][0]["id"] == body["chapter_id"]

    prepared = await client.post(
        f'/api/projects/{body["project_id"]}/planning/chapters/'
        f'{body["chapter_id"]}/generation-runs',
        headers=auth_headers,
        json={
            "operation_key": "demo-fixture-preflight-check-0001",
            "expected_structure_version": 1,
            "expected_assignment_version": 1,
            "expected_chapter_lock_version": 1,
        },
    )
    assert prepared.status_code == 200, prepared.text
    async with TestSessionLocal() as session:
        assert await _count(session, ChapterGenerationRun) == 1
        assert await _count(session, ChapterGenerationAttempt) == 0
        assert await _count(session, ChapterGenerationCandidate) == 0


async def test_gate_is_hidden_and_writes_nothing_when_disabled(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(settings, "DEMO_FIXTURE_ENABLED", False)
    response = await client.post(
        "/api/demo/v1/bootstrap", headers=auth_headers, json=_COMMAND
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "资源不存在"}
    async with TestSessionLocal() as session:
        assert await _count(session, Project) == 0
    current = await client.get("/api/demo/v1/fixture", headers=auth_headers)
    assert current.status_code == 404


async def test_bootstrap_requires_authentication(client):
    response = await client.post("/api/demo/v1/bootstrap", json=_COMMAND)
    assert response.status_code == 401
    async with TestSessionLocal() as session:
        assert await _count(session, Project) == 0


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("APP_ENVIRONMENT", "development"),
        ("APP_ENVIRONMENT", "production"),
        ("DEBUG", False),
    ],
)
async def test_wrong_environment_fails_closed(
    client, auth_headers, monkeypatch, attribute, value
):
    monkeypatch.setattr(settings, attribute, value)
    response = await client.post(
        "/api/demo/v1/bootstrap", headers=auth_headers, json=_COMMAND
    )
    assert response.status_code == 404
    async with TestSessionLocal() as session:
        assert await _count(session, Project) == 0


async def test_configured_llm_key_fails_closed(client, auth_headers, monkeypatch):
    monkeypatch.setattr(demo_fixture, "load_settings", lambda: {"api_key": "secret"})
    response = await client.post(
        "/api/demo/v1/bootstrap", headers=auth_headers, json=_COMMAND
    )
    assert response.status_code == 404
    assert "secret" not in response.text
    async with TestSessionLocal() as session:
        assert await _count(session, Project) == 0


async def test_demo_environment_rejects_non_demo_and_non_sqlite_database(
    monkeypatch, tmp_path
):
    class FakeBind:
        def __init__(self, url):
            self.url = make_url(url)

    class FakeSession:
        def __init__(self, url):
            self._bind = FakeBind(url)

        def get_bind(self):
            return self._bind

    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "demo")
    isolated_data = tmp_path / "backend-data"
    isolated_demo = isolated_data / "demo"
    isolated_demo.mkdir(parents=True)
    monkeypatch.setattr(demo_fixture, "DATA_DIR", isolated_data)
    demo_fixture.ensure_demo_fixture_environment(
        FakeSession(f"sqlite+aiosqlite:///{isolated_demo / 'fixture.db'}")
    )
    with pytest.raises(demo_fixture.DemoFixtureUnavailableError):
        demo_fixture.ensure_demo_fixture_environment(
            FakeSession(f"sqlite+aiosqlite:///{tmp_path / 'outside.db'}")
        )
    outside_directory = tmp_path / "symlink-target"
    outside_directory.mkdir()
    (isolated_demo / "escape").symlink_to(outside_directory, target_is_directory=True)
    with pytest.raises(demo_fixture.DemoFixtureUnavailableError):
        demo_fixture.ensure_demo_fixture_environment(
            FakeSession(
                f"sqlite+aiosqlite:///{isolated_demo / 'escape' / 'fixture.db'}"
            )
        )
    with pytest.raises(demo_fixture.DemoFixtureUnavailableError):
        demo_fixture.ensure_demo_fixture_environment(
            FakeSession("postgresql+asyncpg://demo:demo@127.0.0.1/demo")
        )
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "test")
    with pytest.raises(demo_fixture.DemoFixtureUnavailableError):
        demo_fixture.ensure_demo_fixture_environment(
            FakeSession(f"sqlite+aiosqlite:///{tmp_path / 'persistent-test.db'}")
        )


async def test_existing_unrelated_project_is_not_changed(client, auth_headers):
    unrelated = await client.post(
        "/api/projects",
        headers=auth_headers,
        json={
            "title": "作者自己的项目",
            "genre": "悬疑",
            "total_chapters": 18,
            "chapter_word_count": 2200,
            "style_intensity": "mild",
        },
    )
    assert unrelated.status_code == 200, unrelated.text
    unrelated_before = unrelated.json()
    fixture = await client.post(
        "/api/demo/v1/bootstrap", headers=auth_headers, json=_COMMAND
    )
    assert fixture.status_code == 200, fixture.text
    reread = await client.get(
        f'/api/projects/{unrelated_before["id"]}', headers=auth_headers
    )
    assert reread.status_code == 200
    for key in (
        "id",
        "title",
        "genre",
        "total_chapters",
        "chapter_word_count",
        "style_intensity",
    ):
        assert reread.json()[key] == unrelated_before[key]


async def test_contract_rejects_unknown_operation_or_extra_fields(client, auth_headers):
    wrong_key = await client.post(
        "/api/demo/v1/bootstrap",
        headers=auth_headers,
        json={"fixture_version": 1, "operation_key": "different"},
    )
    assert wrong_key.status_code == 422
    extra = await client.post(
        "/api/demo/v1/bootstrap",
        headers=auth_headers,
        json=_COMMAND | {"reset": True},
    )
    assert extra.status_code == 422
    async with TestSessionLocal() as session:
        assert await _count(session, Project) == 0


async def test_changed_fixture_is_reported_without_overwrite(client, auth_headers):
    created = await client.post(
        "/api/demo/v1/bootstrap", headers=auth_headers, json=_COMMAND
    )
    assert created.status_code == 200
    project_id = created.json()["project_id"]
    async with TestSessionLocal() as session:
        await session.execute(
            update(Project).where(Project.id == project_id).values(title="作者修改后的标题")
        )
        await session.commit()

    response = await client.post(
        "/api/demo/v1/bootstrap", headers=auth_headers, json=_COMMAND
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DEMO_FIXTURE_DIVERGED"
    assert (
        response.json()["detail"]["recommended_action"]
        == "preserve_existing_fixture"
    )
    current = await client.get("/api/demo/v1/fixture", headers=auth_headers)
    assert current.status_code == 200
    assert current.json()["state"] == "diverged"
    assert current.json()["project_id"] == project_id
    assert current.json()["plan_id"] is None
    assert current.json()["preserved"] is True
    assert current.json()["can_bootstrap"] is False
    async with TestSessionLocal() as session:
        project = await session.get(Project, project_id)
        assert project.title == "作者修改后的标题"
        assert await _count(session, Project) == 1


async def test_two_users_receive_separate_owner_scoped_fixtures(client, auth_headers):
    first = await client.post(
        "/api/demo/v1/bootstrap", headers=auth_headers, json=_COMMAND
    )
    registered = await client.post(
        "/api/auth/register",
        json={
            "email": "second@example.com",
            "username": "second-user",
            "password": "testpass123",
        },
    )
    second_headers = {"Authorization": f'Bearer {registered.json()["token"]}'}
    second = await client.post(
        "/api/demo/v1/bootstrap", headers=second_headers, json=_COMMAND
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["project_id"] != second.json()["project_id"]
    forbidden = await client.get(
        f'/api/projects/{first.json()["project_id"]}', headers=second_headers
    )
    assert forbidden.status_code == 403
    async with TestSessionLocal() as session:
        assert await _count(session, Project) == 2


async def test_partial_deterministic_identity_fails_closed(client, auth_headers):
    user_id = await _user_id()
    ids = fixture_ids(user_id)
    async with TestSessionLocal() as session:
        session.add(
            Project(
                id=ids.project,
                title="占用但不完整",
                genre="科幻",
                owner_id=user_id,
                lore_storage_mode="relational",
            )
        )
        await session.commit()
    response = await client.post(
        "/api/demo/v1/bootstrap", headers=auth_headers, json=_COMMAND
    )
    assert response.status_code == 409
    async with TestSessionLocal() as session:
        project = await session.get(Project, ids.project)
        assert project.title == "占用但不完整"
        assert await _count(session, SettingElement) == 0


async def test_exception_rolls_back_every_fixture_row(auth_headers, monkeypatch):
    del auth_headers  # registration only; the service receives the resolved owner id below
    user_id = await _user_id()
    original = demo_fixture._add_fixture_rows

    async def add_then_fail(db, owner_id, ids):
        await original(db, owner_id, ids)
        raise RuntimeError("injected failure")

    monkeypatch.setattr(demo_fixture, "_add_fixture_rows", add_then_fail)
    async with TestSessionLocal() as session:
        with pytest.raises(RuntimeError, match="injected failure"):
            await bootstrap_demo_fixture(session, user_id)
    async with TestSessionLocal() as session:
        for model in (
            Project,
            SettingType,
            SettingElement,
            ElementSource,
            ElementVersion,
            NovelPlan,
            PlanningPart,
            PlanningChapter,
            PlanningLoreAssignment,
        ):
            assert await _count(session, model) == 0
