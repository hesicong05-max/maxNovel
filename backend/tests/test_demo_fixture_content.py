"""Final v1 content graph tests for the ordinary technical demo."""

from __future__ import annotations

import pytest
from app.config import settings
from app.core import demo_fixture
from app.core.demo_fixture_content import RELATION_SPECS, TYPE_SPECS
from app.core.demo_fixture_store import fixture_ids
from app.core.lore_migration import TYPE_FIELD_SCHEMAS
from app.core.lore_relation_types import resolve_relation_type
from app.models.foreshadow import (
    ForeshadowFact,
    ForeshadowLifecycle,
    ForeshadowLifecycleEvent,
    ForeshadowOperation,
    ForeshadowPlanItem,
)
from app.models.generation import (
    ChapterGenerationAttempt,
    ChapterGenerationCandidate,
    ChapterGenerationRun,
)
from app.models.lore import (
    ElementRelation,
    ElementRelationVersion,
    ElementSource,
    ElementStateEvent,
    ElementVersion,
    LoreRelationCreateOperation,
    SettingElement,
    SettingType,
    SettingTypeRevision,
)
from app.models.planning import (
    NovelPlan,
    PlanningChapter,
    PlanningLoreAssignment,
    PlanningLoreAssignmentEvent,
    PlanningMutationOperation,
    PlanningPart,
)
from app.models.project import Chapter, Outline, Project, Worldview
from sqlalchemy import func, select, update

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


async def _bootstrap(client, headers) -> dict:
    response = await client.post(
        "/api/demo/v1/bootstrap", headers=headers, json=_COMMAND
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_frozen_builtin_schemas_and_relations_match_public_contracts():
    for spec in TYPE_SPECS:
        assert list(spec.fields) == TYPE_FIELD_SCHEMAS[spec.key]
    for spec in RELATION_SPECS:
        if spec.relation_key.startswith("custom:"):
            resolved = resolve_relation_type(
                "custom", spec.forward_label, spec.reverse_label
            )
        else:
            resolved = resolve_relation_type(spec.relation_key, None, None)
        assert resolved[:3] == (
            spec.relation_key,
            spec.forward_label,
            spec.reverse_label,
        )


async def test_final_fixture_graph_has_exact_content_and_zero_generation(
    client, auth_headers
):
    body = await _bootstrap(client, auth_headers)
    current = await client.get("/api/demo/v1/fixture", headers=auth_headers)
    assert current.status_code == 200
    assert current.json()["state"] == "ready"
    assert current.json()["counts"] == {
        "setting_type_count": 6,
        "element_count": 7,
        "source_count": 7,
        "relation_count": 3,
        "part_count": 1,
        "chapter_count": 2,
        "assignment_count": 7,
        "foreshadow_lifecycle_count": 1,
        "foreshadow_plan_count": 2,
        "foreshadow_fact_count": 0,
    }
    assert current.json()["second_chapter_id"] != body["chapter_id"]
    assert current.json()["foreshadow_element_id"] != body["element_id"]

    exact = {
        Project: 1,
        SettingType: 6,
        SettingTypeRevision: 6,
        SettingElement: 7,
        ElementSource: 7,
        ElementVersion: 7,
        ElementStateEvent: 7,
        NovelPlan: 1,
        PlanningPart: 1,
        PlanningChapter: 2,
        PlanningLoreAssignment: 7,
        PlanningLoreAssignmentEvent: 7,
        ElementRelation: 3,
        ElementRelationVersion: 3,
        ForeshadowLifecycle: 1,
        ForeshadowPlanItem: 2,
        ForeshadowLifecycleEvent: 3,
    }
    zero = (
        ForeshadowFact,
        ForeshadowOperation,
        LoreRelationCreateOperation,
        PlanningMutationOperation,
        ChapterGenerationRun,
        ChapterGenerationAttempt,
        ChapterGenerationCandidate,
        Chapter,
        Worldview,
        Outline,
    )
    async with TestSessionLocal() as session:
        for model, expected in exact.items():
            assert await _count(session, model) == expected
        for model in zero:
            assert await _count(session, model) == 0


async def test_existing_apis_expose_types_relations_assignments_and_future_plans(
    client, auth_headers
):
    body = await _bootstrap(client, auth_headers)
    current = (await client.get("/api/demo/v1/fixture", headers=auth_headers)).json()
    project_id = body["project_id"]

    lore = await client.get(
        f"/api/projects/{project_id}/lore/elements?limit=100", headers=auth_headers
    )
    assert lore.status_code == 200, lore.text
    assert {item["name"] for item in lore.json()["items"]} == {
        "沈星",
        "星港",
        "守夜司",
        "星钥",
        "潮汐门限",
        "禁航令",
        "褪色航标",
    }
    assert {item["type"]["key"] for item in lore.json()["items"]} == {
        "character",
        "location",
        "faction",
        "item",
        "rule",
        "foreshadow",
    }
    detail = await client.get(
        f'/api/projects/{project_id}/lore/elements/{body["element_id"]}',
        headers=auth_headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["name"] == "沈星"
    assert detail.json()["sources"][0]["reference"] == (
        "《雾港回声》非生产固定样例原稿 v1"
    )

    planning = await client.get(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )
    assert planning.status_code == 200, planning.text
    chapters = planning.json()["parts"][0]["chapters"]
    assert [(item["title"], item["position"]) for item in chapters] == [
        ("停摆的星港", 1),
        ("潮线之外", 2),
    ]

    chapter_assignments = await client.get(
        f"/api/projects/{project_id}/planning/lore-assignments",
        headers=auth_headers,
        params={"scope_type": "chapter", "scope_target_id": body["chapter_id"]},
    )
    assert chapter_assignments.status_code == 200, chapter_assignments.text
    assignment_counts = chapter_assignments.json()["counts"]
    assert assignment_counts["direct"] == 2
    assert assignment_counts["effective"] == 7
    assert assignment_counts["generation_eligible"] == 7

    foreshadows = await client.get(
        f"/api/projects/{project_id}/planning/foreshadows", headers=auth_headers
    )
    assert foreshadows.status_code == 200, foreshadows.text
    assert foreshadows.json()["counts"] == {
        "unplanted": 1,
        "planted": 0,
        "pending_resolution": 0,
        "resolved": 0,
    }
    item = foreshadows.json()["items"][0]
    assert item["id"] == current["foreshadow_lifecycle_id"]
    assert item["element"]["id"] == current["foreshadow_element_id"]
    assert item["state"] == "unplanted"
    assert item["facts"] == []
    assert [
        (plan["action_kind"], plan["target"]["target_id"]) for plan in item["plans"]
    ] == [
        ("plant", body["chapter_id"]),
        ("resolve", current["second_chapter_id"]),
    ]
    history = await client.get(
        f'/api/projects/{project_id}/planning/foreshadows/{item["id"]}/history',
        headers=auth_headers,
    )
    assert history.status_code == 200, history.text
    assert [event["event_kind"] for event in history.json()["items"]] == [
        "create",
        "plan_create",
        "plan_create",
    ]
    assert [
        (event["previous_lifecycle_version"], event["new_lifecycle_version"])
        for event in history.json()["items"]
    ] == [(0, 1), (1, 2), (2, 3)]


async def test_first_chapter_preflight_freezes_all_elements_relations_but_no_facts(
    client, auth_headers
):
    body = await _bootstrap(client, auth_headers)
    response = await client.post(
        f'/api/projects/{body["project_id"]}/planning/chapters/'
        f'{body["chapter_id"]}/generation-runs',
        headers=auth_headers,
        json={
            "operation_key": "demo-final-content-preflight-0001",
            "expected_structure_version": 1,
            "expected_assignment_version": 1,
            "expected_chapter_lock_version": 1,
        },
    )
    assert response.status_code == 200, response.text
    manifest = response.json()["context_manifest"]
    assert len(manifest["elements"]) == 7
    assert len(manifest["relations"]) == 3
    assert manifest["foreshadow_actions"]["supported"] is False
    async with TestSessionLocal() as session:
        assert await _count(session, ChapterGenerationRun) == 1
        assert await _count(session, ChapterGenerationAttempt) == 0
        assert await _count(session, ChapterGenerationCandidate) == 0
        assert await _count(session, ForeshadowFact) == 0


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        (PlanningChapter, "title", "被修改的第二章"),
        (ElementRelation, "forward_label", "错误关系"),
        (ForeshadowLifecycle, "lock_version", 2),
        (ForeshadowPlanItem, "condition_text", "错误条件"),
    ],
)
async def test_any_final_graph_drift_is_preserved_and_reported(
    client, auth_headers, model, field, value
):
    body = await _bootstrap(client, auth_headers)
    user_id = await _owner_id(body["project_id"])
    ids = fixture_ids(user_id)
    target_ids = {
        PlanningChapter: ids.second_chapter,
        ElementRelation: ids.relation_id("shen_xing_serves_night_watch"),
        ForeshadowLifecycle: ids.foreshadow_lifecycle,
        ForeshadowPlanItem: ids.foreshadow_plan_id("resolve"),
    }
    async with TestSessionLocal() as session:
        await session.execute(
            update(model).where(model.id == target_ids[model]).values({field: value})
        )
        await session.commit()
    replay = await client.post(
        "/api/demo/v1/bootstrap", headers=auth_headers, json=_COMMAND
    )
    assert replay.status_code == 409
    current = await client.get("/api/demo/v1/fixture", headers=auth_headers)
    assert current.status_code == 200
    assert current.json()["state"] == "diverged"
    assert current.json()["preserved"] is True
    assert current.json()["counts"] is None
    async with TestSessionLocal() as session:
        row = await session.get(model, target_ids[model])
        assert getattr(row, field) == value


@pytest.mark.parametrize("extra_kind", ["element", "relation", "fact"])
async def test_extra_fixture_project_rows_are_preserved_and_marked_diverged(
    client, auth_headers, extra_kind
):
    body = await _bootstrap(client, auth_headers)
    user_id = await _owner_id(body["project_id"])
    ids = fixture_ids(user_id)
    extra_id = {
        "element": "e" * 32,
        "relation": "r" * 32,
        "fact": "f" * 32,
    }[extra_kind]
    async with TestSessionLocal() as session:
        if extra_kind == "element":
            session.add(
                SettingElement(
                    id=extra_id,
                    project_id=ids.project,
                    type_id=ids.type_id("character"),
                    name="用户新增角色",
                    normalized_name="用户新增角色",
                    summary="用于验证样例修改会被保留。",
                    payload={},
                    field_states={},
                    confirmation_status="confirmed",
                    lifecycle_status="active",
                    enabled=True,
                    content_version=1,
                    lock_version=1,
                )
            )
        elif extra_kind == "relation":
            session.add(
                ElementRelation(
                    id=extra_id,
                    project_id=ids.project,
                    source_element_id=ids.element_id("shen_xing"),
                    target_element_id=ids.element_id("star_harbor"),
                    relation_key="related_to",
                    forward_label="关联于",
                    reverse_label="关联于",
                    description="用户新增关系",
                    metadata_={},
                    status="active",
                    version_no=1,
                    lock_version=1,
                )
            )
        else:
            session.add(
                ForeshadowFact(
                    id=extra_id,
                    project_id=ids.project,
                    plan_id=ids.plan,
                    lifecycle_id=ids.foreshadow_lifecycle,
                    chapter_id=ids.chapter,
                    fact_kind="planted",
                    note="用户新增事实",
                    status="active",
                    lock_version=1,
                    recorded_by=user_id,
                )
            )
        await session.commit()

    current = await client.get("/api/demo/v1/fixture", headers=auth_headers)
    assert current.status_code == 200
    assert current.json()["state"] == "diverged"
    assert current.json()["counts"] is None
    replay = await client.post(
        "/api/demo/v1/bootstrap", headers=auth_headers, json=_COMMAND
    )
    assert replay.status_code == 409
    async with TestSessionLocal() as session:
        model = {
            "element": SettingElement,
            "relation": ElementRelation,
            "fact": ForeshadowFact,
        }[extra_kind]
        assert await session.get(model, extra_id) is not None


async def _owner_id(project_id: str) -> str:
    async with TestSessionLocal() as session:
        return str(
            await session.scalar(
                select(Project.owner_id).where(Project.id == project_id)
            )
        )
