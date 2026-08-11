"""DEV-017B2a durable foreshadow lifecycle API tests."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, update

from app.core.maintenance import ProjectWriteFrozenError
from app.core.foreshadow_lifecycle import ForeshadowWriteError
from app.models.foreshadow import (
    ForeshadowFact,
    ForeshadowLifecycle,
    ForeshadowLifecycleEvent,
    ForeshadowOperation,
    ForeshadowPlanItem,
)
from app.models.lore import SettingElement, SettingType
from app.models.planning import PlanningChapter, PlanningPart
from app.models.project import Project, StoryMemory
from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal


PROJECT_PAYLOAD = {
    "title": "伏笔生命周期测试",
    "genre": "悬疑",
    "total_chapters": 20,
    "chapter_word_count": 1800,
    "style_intensity": "standard",
}


async def _fixture(client, headers) -> dict:
    created = await client.post("/api/projects", headers=headers, json=PROJECT_PAYLOAD)
    assert created.status_code == 200
    project_id = created.json()["id"]
    type_id = uuid.uuid4().hex
    element_id = uuid.uuid4().hex
    async with TestSessionLocal() as session:
        project = await session.scalar(select(Project).where(Project.id == project_id))
        await session.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(lore_storage_mode="relational")
        )
        session.add(
            SettingType(
                id=type_id,
                project_id=project_id,
                key="foreshadow",
                display_name="伏笔",
                is_builtin=True,
                schema_revision=1,
                field_schema={},
                status="active",
            )
        )
        await session.flush()
        session.add(
            SettingElement(
                id=element_id,
                project_id=project_id,
                type_id=type_id,
                name="断裂的铜铃",
                normalized_name="断裂的铜铃",
                summary="雨夜遗留的铜铃",
                payload={"description": "雨夜遗留的铜铃"},
                field_states={"description": "confirmed"},
                confirmation_status="confirmed",
                lifecycle_status="active",
                enabled=True,
                content_version=1,
                lock_version=1,
            )
        )
        assert project is not None
        await session.commit()
    initialized = await client.post(
        f"/api/projects/{project_id}/planning", headers=headers
    )
    assert initialized.status_code == 200
    version = initialized.json()["structure_version"]
    nodes: dict[str, dict] = {}
    for key, title in (("part-a", "上篇"), ("part-b", "下篇")):
        response = await client.post(
            f"/api/projects/{project_id}/planning/parts",
            headers=headers,
            json={
                "operation_key": f"fixture-{key}",
                "expected_structure_version": version,
                "title": title,
            },
        )
        assert response.status_code == 200
        nodes[key] = response.json()["affected_node"]
        version = response.json()["new_structure_version"]
    for key, part_key, title in (
        ("chapter-a1", "part-a", "第一章"),
        ("chapter-a2", "part-a", "第二章"),
        ("chapter-b1", "part-b", "第三章"),
    ):
        response = await client.post(
            f"/api/projects/{project_id}/planning/parts/{nodes[part_key]['id']}/chapters",
            headers=headers,
            json={
                "operation_key": f"fixture-{key}",
                "expected_structure_version": version,
                "title": title,
            },
        )
        assert response.status_code == 200
        nodes[key] = response.json()["affected_node"]
        version = response.json()["new_structure_version"]
    return {
        "project_id": project_id,
        "element_id": element_id,
        "structure_version": version,
        **nodes,
    }


async def _bind(client, headers, fixture, key="foreshadow-bind-0001"):
    return await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows",
        headers=headers,
        json={
            "operation_key": key,
            "element_id": fixture["element_id"],
            "expected_structure_version": fixture["structure_version"],
            "expected_element_lock_version": 1,
        },
    )


def _plan_payload(fixture, lifecycle_version, *, key, action, target, condition=""):
    node = fixture[target]
    return {
        "operation_key": key,
        "expected_lifecycle_version": lifecycle_version,
        "expected_structure_version": fixture["structure_version"],
        "action_kind": action,
        "target_type": "part" if target.startswith("part") else "chapter",
        "target_id": node["id"],
        "expected_target_lock_version": node["lock_version"],
        "condition_text": condition,
        "note": "作者计划",
    }


def _fact_payload(fixture, lifecycle_version, *, key, kind, chapter):
    node = fixture[chapter]
    return {
        "operation_key": key,
        "expected_lifecycle_version": lifecycle_version,
        "expected_structure_version": fixture["structure_version"],
        "fact_kind": kind,
        "chapter_id": node["id"],
        "expected_chapter_lock_version": node["lock_version"],
        "note": "作者确认",
    }


@pytest.mark.usefixtures("clean_db")
async def test_bind_replay_recovery_list_and_owner_isolation(
    client, auth_headers, second_auth_headers
):
    fixture = await _fixture(client, auth_headers)
    unknown_field = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows",
        headers=auth_headers,
        json={
            "operation_key": "unknown-field-bind",
            "element_id": fixture["element_id"],
            "expected_structure_version": fixture["structure_version"],
            "expected_element_lock_version": 1,
            "unexpected": "must fail closed",
        },
    )
    first = await _bind(client, auth_headers, fixture)
    replay = await _bind(client, auth_headers, fixture)
    recovery = await client.get(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/operations/by-key/foreshadow-bind-0001",
        headers=auth_headers,
    )
    conflict = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows",
        headers=auth_headers,
        json={
            "operation_key": "foreshadow-bind-0001",
            "element_id": "f" * 32,
            "expected_structure_version": fixture["structure_version"],
            "expected_element_lock_version": 1,
        },
    )
    listed = await client.get(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows",
        headers=auth_headers,
    )
    forbidden = await client.get(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows",
        headers=second_auth_headers,
    )

    assert unknown_field.status_code == 422
    assert first.status_code == replay.status_code == recovery.status_code == 200
    assert first.json()["replayed"] is False
    assert replay.json()["replayed"] is recovery.json()["replayed"] is True
    assert first.json()["receipt_id"] == replay.json()["receipt_id"]
    assert first.json()["lifecycle"]["state"] == "unplanted"
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "FORESHADOW_OPERATION_KEY_REUSED"
    assert listed.json()["counts"]["unplanted"] == 1
    assert len(listed.json()["items"]) == 1
    assert forbidden.status_code == 403
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(ForeshadowLifecycle.id))) == 1
        assert await session.scalar(select(func.count(ForeshadowOperation.id))) == 1


@pytest.mark.usefixtures("clean_db")
async def test_corrupt_operation_event_identity_never_replays(client, auth_headers):
    fixture = await _fixture(client, auth_headers)
    first = await _bind(client, auth_headers, fixture, "corrupt-receipt-key")
    assert first.status_code == 200
    async with TestSessionLocal() as session:
        operation = await session.scalar(
            select(ForeshadowOperation).where(
                ForeshadowOperation.operation_key == "corrupt-receipt-key"
            )
        )
        assert operation is not None
        snapshot = dict(operation.result_snapshot)
        snapshot["event_id"] = "f" * 32
        operation.result_snapshot = snapshot
        await session.commit()
    recovery = await client.get(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/operations/by-key/corrupt-receipt-key",
        headers=auth_headers,
    )
    assert recovery.status_code == 409
    assert recovery.json()["detail"]["code"] == "FORESHADOW_OPERATION_CORRUPT"


@pytest.mark.usefixtures("clean_db")
async def test_corrupt_nested_lifecycle_snapshot_never_replays(client, auth_headers):
    fixture = await _fixture(client, auth_headers)
    first = await _bind(client, auth_headers, fixture, "corrupt-nested-key")
    assert first.status_code == 200
    async with TestSessionLocal() as session:
        operation = await session.scalar(
            select(ForeshadowOperation).where(
                ForeshadowOperation.operation_key == "corrupt-nested-key"
            )
        )
        assert operation is not None
        snapshot = dict(operation.result_snapshot)
        lifecycle = dict(snapshot["lifecycle"])
        lifecycle["lock_version"] = snapshot["new_lifecycle_version"] + 1
        snapshot["lifecycle"] = lifecycle
        operation.result_snapshot = snapshot
        await session.commit()
    recovery = await client.get(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/operations/by-key/corrupt-nested-key",
        headers=auth_headers,
    )
    assert recovery.status_code == 409
    assert recovery.json()["detail"]["code"] == "FORESHADOW_OPERATION_CORRUPT"


@pytest.mark.usefixtures("clean_db")
async def test_plan_and_confirmed_fact_states_are_separate_and_ordered(
    client, auth_headers
):
    fixture = await _fixture(client, auth_headers)
    bound = await _bind(client, auth_headers, fixture)
    lifecycle_id = bound.json()["lifecycle_id"]
    plant_plan = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/plans",
        headers=auth_headers,
        json=_plan_payload(
            fixture,
            1,
            key="plant-plan-0001",
            action="plant",
            target="chapter-a1",
        ),
    )
    missing_condition = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/plans",
        headers=auth_headers,
        json=_plan_payload(
            fixture,
            2,
            key="resolve-plan-bad",
            action="resolve",
            target="chapter-a2",
        ),
    )
    invalid_order = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/plans",
        headers=auth_headers,
        json=_plan_payload(
            fixture,
            2,
            key="resolve-plan-order-bad",
            action="resolve",
            target="chapter-a1",
            condition="同章回收不合法",
        ),
    )
    resolve_plan = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/plans",
        headers=auth_headers,
        json=_plan_payload(
            fixture,
            2,
            key="resolve-plan-0001",
            action="resolve",
            target="chapter-b1",
            condition="主角确认铜铃属于失踪者",
        ),
    )
    assert plant_plan.status_code == 200, plant_plan.json()
    assert resolve_plan.status_code == 200, resolve_plan.json()
    assert missing_condition.status_code == 422
    assert missing_condition.json()["detail"]["code"] == "FORESHADOW_RESOLVE_CONDITION_REQUIRED"
    assert invalid_order.status_code == 409
    assert invalid_order.json()["detail"]["code"] == "FORESHADOW_PLAN_ORDER_INVALID"
    assert resolve_plan.json()["lifecycle"]["state"] == "unplanted"

    too_early = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/facts",
        headers=auth_headers,
        json=_fact_payload(
            fixture,
            3,
            key="resolve-fact-early",
            kind="resolved",
            chapter="chapter-a2",
        ),
    )
    planted = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/facts",
        headers=auth_headers,
        json=_fact_payload(
            fixture,
            3,
            key="plant-fact-0001",
            kind="planted",
            chapter="chapter-a1",
        ),
    )
    resolved = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/facts",
        headers=auth_headers,
        json=_fact_payload(
            fixture,
            4,
            key="resolve-fact-0001",
            kind="resolved",
            chapter="chapter-b1",
        ),
    )
    assert too_early.status_code == 409
    assert too_early.json()["detail"]["code"] == "FORESHADOW_NOT_PLANTED"
    assert planted.json()["lifecycle"]["state"] == "pending_resolution"
    assert resolved.json()["lifecycle"]["state"] == "resolved"
    assert len(resolved.json()["lifecycle"]["plans"]) == 2
    assert len(resolved.json()["lifecycle"]["facts"]) == 2


@pytest.mark.usefixtures("clean_db")
async def test_actual_plant_cannot_fall_after_active_resolve_plan(client, auth_headers):
    fixture = await _fixture(client, auth_headers)
    bound = await _bind(client, auth_headers, fixture)
    lifecycle_id = bound.json()["lifecycle_id"]
    resolve_plan = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/plans",
        headers=auth_headers,
        json=_plan_payload(
            fixture,
            1,
            key="cross-order-resolve-plan",
            action="resolve",
            target="chapter-a2",
            condition="在第二章回收",
        ),
    )
    invalid_fact = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/facts",
        headers=auth_headers,
        json=_fact_payload(
            fixture,
            2,
            key="cross-order-plant-fact",
            kind="planted",
            chapter="chapter-b1",
        ),
    )
    assert resolve_plan.status_code == 200
    assert invalid_fact.status_code == 409
    assert invalid_fact.json()["detail"]["code"] == "FORESHADOW_PLAN_ORDER_INVALID"


@pytest.mark.usefixtures("clean_db")
async def test_fact_retraction_is_additive_and_preserves_audit_history(
    client, auth_headers
):
    fixture = await _fixture(client, auth_headers)
    bound = await _bind(client, auth_headers, fixture)
    lifecycle_id = bound.json()["lifecycle_id"]
    planted = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/facts",
        headers=auth_headers,
        json=_fact_payload(
            fixture, 1, key="plant-fact-0002", kind="planted", chapter="chapter-a1"
        ),
    )
    resolved = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/facts",
        headers=auth_headers,
        json=_fact_payload(
            fixture, 2, key="resolve-fact-0002", kind="resolved", chapter="chapter-b1"
        ),
    )
    planted_id = planted.json()["lifecycle"]["facts"][0]["id"]
    resolved_id = next(
        item["id"]
        for item in resolved.json()["lifecycle"]["facts"]
        if item["fact_kind"] == "resolved"
    )
    wrong_order = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/facts/{planted_id}/retract",
        headers=auth_headers,
        json={
            "operation_key": "retract-plant-bad",
            "expected_lifecycle_version": 3,
            "expected_fact_lock_version": 1,
            "reason": "章节记录有误",
        },
    )
    retract_resolved = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/facts/{resolved_id}/retract",
        headers=auth_headers,
        json={
            "operation_key": "retract-resolve-1",
            "expected_lifecycle_version": 3,
            "expected_fact_lock_version": 1,
            "reason": "作者撤销回收认定",
        },
    )
    retract_planted = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/facts/{planted_id}/retract",
        headers=auth_headers,
        json={
            "operation_key": "retract-plant-1",
            "expected_lifecycle_version": 4,
            "expected_fact_lock_version": 1,
            "reason": "作者撤销埋入认定",
        },
    )
    history = await client.get(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/history",
        headers=auth_headers,
    )
    assert wrong_order.status_code == 409
    assert wrong_order.json()["detail"]["code"] == "FORESHADOW_RETRACT_ORDER_INVALID"
    assert retract_resolved.json()["lifecycle"]["state"] == "planted"
    assert retract_planted.json()["lifecycle"]["state"] == "unplanted"
    assert len(retract_planted.json()["lifecycle"]["facts"]) == 2
    assert [item["event_kind"] for item in history.json()["items"]] == [
        "create",
        "fact_record",
        "fact_record",
        "fact_retract",
        "fact_retract",
    ]
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(ForeshadowFact.id))) == 2
        assert await session.scalar(select(func.count(ForeshadowLifecycleEvent.id))) == 5


@pytest.mark.usefixtures("clean_db")
async def test_archive_blocks_new_writes_and_restore_revalidates_lore(
    client, auth_headers
):
    fixture = await _fixture(client, auth_headers)
    bound = await _bind(client, auth_headers, fixture)
    lifecycle_id = bound.json()["lifecycle_id"]
    archived = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/archive",
        headers=auth_headers,
        json={
            "operation_key": "archive-foreshadow-1",
            "expected_lifecycle_version": 1,
        },
    )
    archived_replay = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/archive",
        headers=auth_headers,
        json={
            "operation_key": "archive-foreshadow-1",
            "expected_lifecycle_version": 1,
        },
    )
    blocked = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/facts",
        headers=auth_headers,
        json=_fact_payload(
            fixture, 2, key="fact-while-archived", kind="planted", chapter="chapter-a1"
        ),
    )
    async with TestSessionLocal() as session:
        await session.execute(
            update(SettingElement)
            .where(SettingElement.id == fixture["element_id"])
            .values(enabled=False, lock_version=2)
        )
        await session.commit()
    ineligible = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/restore",
        headers=auth_headers,
        json={
            "operation_key": "restore-foreshadow-bad",
            "expected_lifecycle_version": 2,
            "expected_structure_version": fixture["structure_version"],
            "expected_element_lock_version": 2,
        },
    )
    assert archived.status_code == 200
    assert archived_replay.status_code == 200
    assert archived_replay.json()["replayed"] is True
    assert archived_replay.json()["receipt_id"] == archived.json()["receipt_id"]
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "FORESHADOW_ARCHIVED"
    assert ineligible.status_code == 409
    assert ineligible.json()["detail"]["code"] == "FORESHADOW_ELEMENT_INELIGIBLE"


@pytest.mark.usefixtures("clean_db")
async def test_active_foreshadow_guards_lore_archive_chapter_archive_and_reorder(
    client, auth_headers
):
    fixture = await _fixture(client, auth_headers)
    bound = await _bind(client, auth_headers, fixture)
    lifecycle_id = bound.json()["lifecycle_id"]
    plant = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/plans",
        headers=auth_headers,
        json=_plan_payload(
            fixture,
            1,
            key="guard-plant-plan",
            action="plant",
            target="chapter-a1",
        ),
    )
    resolve = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/plans",
        headers=auth_headers,
        json=_plan_payload(
            fixture,
            2,
            key="guard-resolve-plan",
            action="resolve",
            target="chapter-b1",
            condition="确认铜铃来历",
        ),
    )
    assert plant.status_code == resolve.status_code == 200

    lore_archive = await client.post(
        f"/api/projects/{fixture['project_id']}/lore/elements/{fixture['element_id']}/archive",
        headers=auth_headers,
        json={"expected_version": 1, "reason": "尝试归档"},
    )
    chapter_archive = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/chapters/{fixture['chapter-a1']['id']}/archive",
        headers=auth_headers,
        json={
            "operation_key": "guard-chapter-archive",
            "expected_structure_version": fixture["structure_version"],
        },
    )
    invalid_reorder = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/structure/reorder",
        headers=auth_headers,
        json={
            "operation_key": "guard-reorder-invalid",
            "expected_structure_version": fixture["structure_version"],
            "parts": [
                {
                    "part_id": fixture["part-b"]["id"],
                    "chapter_ids": [fixture["chapter-b1"]["id"]],
                },
                {
                    "part_id": fixture["part-a"]["id"],
                    "chapter_ids": [
                        fixture["chapter-a1"]["id"],
                        fixture["chapter-a2"]["id"],
                    ],
                },
            ],
        },
    )
    assert lore_archive.status_code == 409
    assert lore_archive.json()["detail"]["code"] == "LORE_ELEMENT_ACTIVE_FORESHADOW"
    assert chapter_archive.status_code == 409
    assert (
        chapter_archive.json()["detail"]["code"]
        == "PLANNING_SCOPE_HAS_ACTIVE_FORESHADOWS"
    )
    assert invalid_reorder.status_code == 409
    assert (
        invalid_reorder.json()["detail"]["code"]
        == "PLANNING_FORESHADOW_ORDER_INVALID"
    )
    planning = await client.get(
        f"/api/projects/{fixture['project_id']}/planning", headers=auth_headers
    )
    assert planning.json()["structure_version"] == fixture["structure_version"]
    assert [part["title"] for part in planning.json()["parts"]] == ["上篇", "下篇"]


@pytest.mark.usefixtures("clean_db")
async def test_archived_lifecycle_releases_structure_but_restore_revalidates_targets(
    client, auth_headers
):
    fixture = await _fixture(client, auth_headers)
    bound = await _bind(client, auth_headers, fixture)
    lifecycle_id = bound.json()["lifecycle_id"]
    plan = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/plans",
        headers=auth_headers,
        json=_plan_payload(
            fixture,
            1,
            key="archive-release-plan",
            action="plant",
            target="chapter-a1",
        ),
    )
    archived = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/archive",
        headers=auth_headers,
        json={
            "operation_key": "archive-release-lifecycle",
            "expected_lifecycle_version": 2,
        },
    )
    chapter_archive = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/chapters/{fixture['chapter-a1']['id']}/archive",
        headers=auth_headers,
        json={
            "operation_key": "archive-released-chapter",
            "expected_structure_version": fixture["structure_version"],
        },
    )
    restore = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/restore",
        headers=auth_headers,
        json={
            "operation_key": "restore-invalid-target",
            "expected_lifecycle_version": 3,
            "expected_structure_version": fixture["structure_version"] + 1,
            "expected_element_lock_version": 1,
        },
    )
    assert plan.status_code == archived.status_code == chapter_archive.status_code == 200
    assert restore.status_code == 409
    assert restore.json()["detail"]["code"] == "FORESHADOW_TARGET_ARCHIVED"


@pytest.mark.usefixtures("clean_db")
async def test_archived_lifecycle_allows_correction_but_not_restoring_confirmed_plan(
    client, auth_headers
):
    fixture = await _fixture(client, auth_headers)
    bound = await _bind(client, auth_headers, fixture)
    lifecycle_id = bound.json()["lifecycle_id"]
    plan = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/plans",
        headers=auth_headers,
        json=_plan_payload(
            fixture,
            1,
            key="correction-plan",
            action="plant",
            target="chapter-a1",
        ),
    )
    item_id = plan.json()["lifecycle"]["plans"][0]["id"]
    archived = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/archive",
        headers=auth_headers,
        json={
            "operation_key": "correction-archive",
            "expected_lifecycle_version": 2,
        },
    )
    cancelled = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/plans/{item_id}/cancel",
        headers=auth_headers,
        json={
            "operation_key": "correction-cancel",
            "expected_lifecycle_version": 3,
            "expected_structure_version": fixture["structure_version"],
            "expected_item_lock_version": 1,
        },
    )
    restored = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/restore",
        headers=auth_headers,
        json={
            "operation_key": "correction-restore-life",
            "expected_lifecycle_version": 4,
            "expected_structure_version": fixture["structure_version"],
            "expected_element_lock_version": 1,
        },
    )
    planted = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/facts",
        headers=auth_headers,
        json=_fact_payload(
            fixture,
            5,
            key="correction-plant-fact",
            kind="planted",
            chapter="chapter-a1",
        ),
    )
    blocked_restore = await client.post(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{lifecycle_id}/plans/{item_id}/restore",
        headers=auth_headers,
        json={
            "operation_key": "correction-restore-plan",
            "expected_lifecycle_version": 6,
            "expected_structure_version": fixture["structure_version"],
            "expected_item_lock_version": 2,
        },
    )
    assert plan.status_code == archived.status_code == cancelled.status_code == 200
    assert restored.status_code == planted.status_code == 200
    assert blocked_restore.status_code == 409
    assert (
        blocked_restore.json()["detail"]["code"]
        == "FORESHADOW_ACTION_ALREADY_CONFIRMED"
    )


@pytest.mark.usefixtures("clean_db")
async def test_maintenance_flip_rolls_back_every_foreshadow_write(
    client, auth_headers, monkeypatch
):
    fixture = await _fixture(client, auth_headers)
    calls = 0

    def flip_on_precommit():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ProjectWriteFrozenError("PROJECT_WRITE_FROZEN")

    monkeypatch.setattr(
        "app.core.foreshadow_lifecycle.ensure_project_writes_available",
        flip_on_precommit,
    )
    response = await _bind(client, auth_headers, fixture, "maintenance-bind-1")
    assert response.status_code == 503
    assert response.json()["code"] == "PROJECT_WRITE_FROZEN"
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(ForeshadowLifecycle.id))) == 0
        assert await session.scalar(select(func.count(ForeshadowOperation.id))) == 0
        assert await session.scalar(select(func.count(ForeshadowLifecycleEvent.id))) == 0
        memory = await session.scalar(
            select(StoryMemory).where(StoryMemory.project_id == fixture["project_id"])
        )
        assert memory is None


@pytest.mark.usefixtures("clean_db")
async def test_read_corruption_keeps_stable_actionable_error(
    client, auth_headers, monkeypatch
):
    fixture = await _fixture(client, auth_headers)
    bound = await _bind(client, auth_headers, fixture)

    async def corrupt_response(_db, _lifecycle):
        raise ForeshadowWriteError(
            "FORESHADOW_TARGET_MISSING",
            "伏笔计划引用的章节结构不存在，系统已停止处理。",
            recommended_action="contact_support",
        )

    monkeypatch.setattr(
        "app.api.foreshadows.lifecycle_response", corrupt_response
    )
    listed = await client.get(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows",
        headers=auth_headers,
    )
    detail = await client.get(
        f"/api/projects/{fixture['project_id']}/planning/foreshadows/{bound.json()['lifecycle_id']}",
        headers=auth_headers,
    )
    assert listed.status_code == detail.status_code == 409
    assert listed.json()["detail"] == detail.json()["detail"]
    assert listed.json()["detail"]["code"] == "FORESHADOW_TARGET_MISSING"
    assert listed.json()["detail"]["recommended_action"] == "contact_support"


@pytest.mark.usefixtures("clean_db")
async def test_project_delete_cascades_full_foreshadow_graph_only_for_target_project(
    client, auth_headers
):
    target = await _fixture(client, auth_headers)
    survivor = await _fixture(client, auth_headers)
    target_bind = await _bind(client, auth_headers, target, "delete-target-bind")
    survivor_bind = await _bind(client, auth_headers, survivor, "delete-survivor-bind")
    lifecycle_id = target_bind.json()["lifecycle_id"]
    assert survivor_bind.status_code == 200
    plant_plan = await client.post(
        f"/api/projects/{target['project_id']}/planning/foreshadows/{lifecycle_id}/plans",
        headers=auth_headers,
        json=_plan_payload(
            target,
            1,
            key="delete-plant-plan",
            action="plant",
            target="chapter-a1",
        ),
    )
    resolve_plan = await client.post(
        f"/api/projects/{target['project_id']}/planning/foreshadows/{lifecycle_id}/plans",
        headers=auth_headers,
        json=_plan_payload(
            target,
            2,
            key="delete-resolve-plan",
            action="resolve",
            target="chapter-b1",
            condition="项目删除前的回收条件",
        ),
    )
    planted = await client.post(
        f"/api/projects/{target['project_id']}/planning/foreshadows/{lifecycle_id}/facts",
        headers=auth_headers,
        json=_fact_payload(
            target,
            3,
            key="delete-planted-fact",
            kind="planted",
            chapter="chapter-a1",
        ),
    )
    resolved = await client.post(
        f"/api/projects/{target['project_id']}/planning/foreshadows/{lifecycle_id}/facts",
        headers=auth_headers,
        json=_fact_payload(
            target,
            4,
            key="delete-resolved-fact",
            kind="resolved",
            chapter="chapter-b1",
        ),
    )
    resolved_fact_id = next(
        item["id"]
        for item in resolved.json()["lifecycle"]["facts"]
        if item["fact_kind"] == "resolved"
    )
    retracted = await client.post(
        f"/api/projects/{target['project_id']}/planning/foreshadows/{lifecycle_id}/facts/{resolved_fact_id}/retract",
        headers=auth_headers,
        json={
            "operation_key": "delete-retract-fact",
            "expected_lifecycle_version": 5,
            "expected_fact_lock_version": 1,
            "reason": "验证撤回历史也能级联清理",
        },
    )
    assert all(
        response.status_code == 200
        for response in (plant_plan, resolve_plan, planted, resolved, retracted)
    )
    async with TestSessionLocal() as session:
        project = await session.scalar(
            select(Project).where(Project.id == target["project_id"])
        )
        assert project is not None
        await session.delete(project)
        await session.commit()
    async with TestSessionLocal() as session:
        for model in (
            ForeshadowLifecycle,
            ForeshadowPlanItem,
            ForeshadowFact,
            ForeshadowLifecycleEvent,
            ForeshadowOperation,
        ):
            assert (
                await session.scalar(
                    select(func.count(model.id)).where(
                        model.project_id == target["project_id"]
                    )
                )
                == 0
            )
        assert (
            await session.scalar(
                select(func.count(ForeshadowLifecycle.id)).where(
                    ForeshadowLifecycle.project_id == survivor["project_id"]
                )
            )
            == 1
        )


@pytest.mark.usefixtures("clean_db")
async def test_concurrent_same_operation_is_exactly_once(client, auth_headers):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("row-lock concurrency is verified on PostgreSQL")
    fixture = await _fixture(client, auth_headers)
    first, second = await asyncio.wait_for(
        asyncio.gather(
            _bind(client, auth_headers, fixture, "concurrent-bind-1"),
            _bind(client, auth_headers, fixture, "concurrent-bind-1"),
        ),
        timeout=20,
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["receipt_id"] == second.json()["receipt_id"]
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(ForeshadowLifecycle.id))) == 1
        assert await session.scalar(select(func.count(ForeshadowOperation.id))) == 1
