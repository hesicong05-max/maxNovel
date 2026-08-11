"""DEV-017A3 safe Lore assignment, inheritance, and audit tests."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, update

from app.core.maintenance import ProjectWriteFrozenError
from app.models.lore import SettingElement, SettingType
from app.models.planning import (
    PlanningChapter,
    NovelPlan,
    PlanningLoreAssignment,
    PlanningLoreAssignmentEvent,
    PlanningMutationOperation,
)
from app.models.project import Chapter, Outline, Project, StoryMemory
from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal


PROJECT_PAYLOAD = {
    "title": "A3 设定分配测试",
    "genre": "玄幻",
    "total_chapters": 12,
    "chapter_word_count": 1800,
    "style_intensity": "standard",
}


async def _initialized_project(client, headers, *, title="A3 设定分配测试"):
    created = await client.post(
        "/api/projects", headers=headers, json={**PROJECT_PAYLOAD, "title": title}
    )
    assert created.status_code == 200
    project_id = created.json()["id"]
    async with TestSessionLocal() as session:
        await session.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(lore_storage_mode="relational")
        )
        await session.commit()
    initialized = await client.post(
        f"/api/projects/{project_id}/planning", headers=headers
    )
    assert initialized.status_code == 200
    return project_id, initialized.json()


async def _create_structure(client, headers, project_id):
    part = await client.post(
        f"/api/projects/{project_id}/planning/parts",
        headers=headers,
        json={
            "operation_key": "a3-part-create-0001",
            "expected_structure_version": 1,
            "title": "第一篇",
        },
    )
    assert part.status_code == 200, part.text
    part_id = part.json()["affected_node"]["id"]
    chapter = await client.post(
        f"/api/projects/{project_id}/planning/parts/{part_id}/chapters",
        headers=headers,
        json={
            "operation_key": "a3-chapter-create-0001",
            "expected_structure_version": 2,
            "title": "第一章",
        },
    )
    assert chapter.status_code == 200, chapter.text
    return part_id, chapter.json()["affected_node"]["id"]


async def _create_element(client, headers, project_id, *, name="沈星", field_states=None):
    body = {
        "operation_key": f"a3-element-{uuid.uuid4().hex}",
        "type_key": "character",
        "name": name,
        "summary": "星港守卫队长",
        "payload": {"identity": "队长"},
        "sources": [
            {
                "kind": "manual",
                "reference": "世界观原稿",
                "excerpt": f"{name}是星港守卫队长。",
                "is_primary": True,
            }
        ],
    }
    if field_states is not None:
        body["field_states"] = field_states
    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=headers,
        json=body,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _assign(
    client,
    headers,
    project_id,
    element,
    *,
    version,
    scope_type,
    scope_target_id,
    key,
):
    return await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments",
        headers=headers,
        json={
            "operation_key": key,
            "expected_assignment_version": version,
            "element_id": element["id"],
            "expected_element_content_version": element["content_version"],
            "scope_type": scope_type,
            "scope_target_id": scope_target_id,
        },
    )


@pytest.mark.usefixtures("clean_db")
async def test_assignments_are_additive_deduplicated_and_source_preserving(
    client, auth_headers
):
    project_id, _ = await _initialized_project(client, auth_headers)
    part_id, chapter_id = await _create_structure(client, auth_headers, project_id)
    element = await _create_element(client, auth_headers, project_id)

    novel = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
        key="a3-assign-novel-0001",
    )
    part = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=2,
        scope_type="part",
        scope_target_id=part_id,
        key="a3-assign-part-0001",
    )
    chapter = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=3,
        scope_type="chapter",
        scope_target_id=chapter_id,
        key="a3-assign-chapter-0001",
    )

    assert novel.status_code == part.status_code == chapter.status_code == 200
    assert chapter.json()["new_assignment_version"] == 4
    tree = await client.get(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )
    assert tree.json()["structure_version"] == 3
    assert tree.json()["assignment_version"] == 4

    response = await client.get(
        f"/api/projects/{project_id}/planning/lore-assignments",
        headers=auth_headers,
        params={"scope_type": "chapter", "scope_target_id": chapter_id},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["counts"] == {
        "direct": 1,
        "direct_active": 1,
        "direct_removed": 0,
        "effective": 1,
        "generation_eligible": 1,
        "ineligible": 0,
    }
    effective = body["effective_elements"][0]
    assert effective["element_id"] == element["id"]
    assert len(effective["all_sources"]) == 3
    assert len(effective["direct_assignments"]) == 1
    assert len(effective["inherited_from"]) == 2


@pytest.mark.usefixtures("clean_db")
async def test_moving_chapter_recomputes_part_inheritance_without_rewriting_assignments(
    client, auth_headers
):
    project_id, _ = await _initialized_project(client, auth_headers)
    old_part_id, chapter_id = await _create_structure(
        client, auth_headers, project_id
    )
    new_part = await client.post(
        f"/api/projects/{project_id}/planning/parts",
        headers=auth_headers,
        json={
            "operation_key": "a5-part-create-target",
            "expected_structure_version": 3,
            "title": "第二篇",
        },
    )
    assert new_part.status_code == 200, new_part.text
    new_part_id = new_part.json()["affected_node"]["id"]

    novel_element = await _create_element(
        client, auth_headers, project_id, name="整书法则"
    )
    old_part_element = await _create_element(
        client, auth_headers, project_id, name="旧篇阵营"
    )
    new_part_element = await _create_element(
        client, auth_headers, project_id, name="新篇地点"
    )
    chapter_element = await _create_element(
        client, auth_headers, project_id, name="章节角色"
    )

    novel_assignment = await _assign(
        client,
        auth_headers,
        project_id,
        novel_element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
        key="a5-assign-novel",
    )
    old_part_assignment = await _assign(
        client,
        auth_headers,
        project_id,
        old_part_element,
        version=2,
        scope_type="part",
        scope_target_id=old_part_id,
        key="a5-assign-old-part",
    )
    new_part_assignment = await _assign(
        client,
        auth_headers,
        project_id,
        new_part_element,
        version=3,
        scope_type="part",
        scope_target_id=new_part_id,
        key="a5-assign-new-part",
    )
    chapter_assignment = await _assign(
        client,
        auth_headers,
        project_id,
        chapter_element,
        version=4,
        scope_type="chapter",
        scope_target_id=chapter_id,
        key="a5-assign-chapter",
    )
    assert {
        novel_assignment.status_code,
        old_part_assignment.status_code,
        new_part_assignment.status_code,
        chapter_assignment.status_code,
    } == {200}
    novel_assignment_id = novel_assignment.json()["assignment"]["id"]
    old_part_assignment_id = old_part_assignment.json()["assignment"]["id"]
    new_part_assignment_id = new_part_assignment.json()["assignment"]["id"]
    chapter_assignment_id = chapter_assignment.json()["assignment"]["id"]

    before = await client.get(
        f"/api/projects/{project_id}/planning/lore-assignments",
        headers=auth_headers,
        params={"scope_type": "chapter", "scope_target_id": chapter_id},
    )
    assert before.status_code == 200, before.text
    before_ids = {
        item["element_id"] for item in before.json()["effective_elements"]
    }
    assert before_ids == {
        novel_element["id"],
        old_part_element["id"],
        chapter_element["id"],
    }
    before_by_element = {
        item["element_id"]: item for item in before.json()["effective_elements"]
    }
    assert before_by_element[novel_element["id"]]["inherited_from"] == [
        {
            "assignment_id": novel_assignment_id,
            "scope": {
                "scope_type": "novel",
                "scope_target_id": project_id,
                "title": "整部小说",
                "status": "active",
                "part_id": None,
            },
            "lock_version": 1,
            "assigned_at_content_version": novel_element["content_version"],
        }
    ]
    assert before_by_element[old_part_element["id"]]["inherited_from"][0][
        "assignment_id"
    ] == old_part_assignment_id
    assert before_by_element[old_part_element["id"]]["inherited_from"][0][
        "scope"
    ]["scope_target_id"] == old_part_id

    moved = await client.post(
        f"/api/projects/{project_id}/planning/structure/reorder",
        headers=auth_headers,
        json={
            "operation_key": "a5-move-chapter-between-parts",
            "expected_structure_version": 4,
            "parts": [
                {"part_id": old_part_id, "chapter_ids": []},
                {"part_id": new_part_id, "chapter_ids": [chapter_id]},
            ],
        },
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["new_structure_version"] == 5

    tree = await client.get(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )
    assert tree.status_code == 200
    assert tree.json()["structure_version"] == 5
    assert tree.json()["assignment_version"] == 5
    moved_chapter = next(
        chapter
        for part in tree.json()["parts"]
        for chapter in part["chapters"]
        if chapter["id"] == chapter_id
    )
    assert moved_chapter["part_id"] == new_part_id

    after = await client.get(
        f"/api/projects/{project_id}/planning/lore-assignments",
        headers=auth_headers,
        params={"scope_type": "chapter", "scope_target_id": chapter_id},
    )
    assert after.status_code == 200, after.text
    after_body = after.json()
    assert {
        item["element_id"] for item in after_body["effective_elements"]
    } == {
        novel_element["id"],
        new_part_element["id"],
        chapter_element["id"],
    }
    chapter_effective = next(
        item
        for item in after_body["effective_elements"]
        if item["element_id"] == chapter_element["id"]
    )
    assert chapter_effective["direct_assignments"][0]["assignment_id"] == (
        chapter_assignment_id
    )
    assert chapter_effective["inherited_from"] == []
    novel_effective = next(
        item
        for item in after_body["effective_elements"]
        if item["element_id"] == novel_element["id"]
    )
    new_part_effective = next(
        item
        for item in after_body["effective_elements"]
        if item["element_id"] == new_part_element["id"]
    )
    assert novel_effective["inherited_from"][0]["assignment_id"] == (
        novel_assignment_id
    )
    assert novel_effective["inherited_from"][0]["scope"]["scope_type"] == "novel"
    assert new_part_effective["inherited_from"][0]["assignment_id"] == (
        new_part_assignment_id
    )
    assert new_part_effective["inherited_from"][0]["scope"][
        "scope_target_id"
    ] == new_part_id

    history = await client.get(
        f"/api/projects/{project_id}/planning/lore-assignments/history",
        headers=auth_headers,
        params={"element_id": chapter_element["id"]},
    )
    assert history.status_code == 200
    assert history.json()["assignments"][0]["id"] == chapter_assignment_id
    assert [
        event["action"]
        for event in history.json()["assignments"][0]["events"]
    ] == ["assign"]


@pytest.mark.usefixtures("clean_db")
async def test_archive_remove_restore_chain_preserves_inheritance_and_history(
    client, auth_headers
):
    project_id, _ = await _initialized_project(client, auth_headers)
    _, chapter_id = await _create_structure(client, auth_headers, project_id)
    inherited_element = await _create_element(
        client, auth_headers, project_id, name="整书约束"
    )
    chapter_element = await _create_element(
        client, auth_headers, project_id, name="本章道具"
    )
    inherited = await _assign(
        client,
        auth_headers,
        project_id,
        inherited_element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
        key="a5-archive-inherited",
    )
    direct = await _assign(
        client,
        auth_headers,
        project_id,
        chapter_element,
        version=2,
        scope_type="chapter",
        scope_target_id=chapter_id,
        key="a5-archive-direct",
    )
    assert inherited.status_code == direct.status_code == 200
    assignment_id = direct.json()["assignment"]["id"]

    blocked = await client.post(
        f"/api/projects/{project_id}/planning/chapters/{chapter_id}/archive",
        headers=auth_headers,
        json={
            "operation_key": "a5-archive-blocked",
            "expected_structure_version": 3,
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == (
        "PLANNING_SCOPE_HAS_ACTIVE_ASSIGNMENTS"
    )
    assert blocked.json()["detail"]["active_assignment_count"] == 1

    removed = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments/{assignment_id}/remove",
        headers=auth_headers,
        json={
            "operation_key": "a5-remove-before-archive",
            "expected_assignment_version": 3,
            "expected_lock_version": 1,
            "scope_type": "chapter",
            "scope_target_id": chapter_id,
        },
    )
    assert removed.status_code == 200, removed.text
    inherited_only = await client.get(
        f"/api/projects/{project_id}/planning/lore-assignments",
        headers=auth_headers,
        params={"scope_type": "chapter", "scope_target_id": chapter_id},
    )
    assert inherited_only.status_code == 200
    assert {
        item["element_id"]
        for item in inherited_only.json()["effective_elements"]
    } == {inherited_element["id"]}

    archived = await client.post(
        f"/api/projects/{project_id}/planning/chapters/{chapter_id}/archive",
        headers=auth_headers,
        json={
            "operation_key": "a5-archive-after-remove",
            "expected_structure_version": 3,
        },
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["new_structure_version"] == 4
    archived_scope = await client.get(
        f"/api/projects/{project_id}/planning/lore-assignments",
        headers=auth_headers,
        params={"scope_type": "chapter", "scope_target_id": chapter_id},
    )
    assert archived_scope.status_code == 200
    assert archived_scope.json()["scope"]["status"] == "archived"
    assert archived_scope.json()["direct_assignments"][0]["status"] == "removed"

    blocked_restore = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments/{assignment_id}/restore",
        headers=auth_headers,
        json={
            "operation_key": "a5-restore-on-archived-scope",
            "expected_assignment_version": 4,
            "expected_lock_version": 2,
            "scope_type": "chapter",
            "scope_target_id": chapter_id,
        },
    )
    assert blocked_restore.status_code == 409
    assert blocked_restore.json()["detail"]["code"] == "PLANNING_SCOPE_ARCHIVED"

    restored_scope = await client.post(
        f"/api/projects/{project_id}/planning/chapters/{chapter_id}/restore",
        headers=auth_headers,
        json={
            "operation_key": "a5-restore-chapter-scope",
            "expected_structure_version": 4,
        },
    )
    assert restored_scope.status_code == 200, restored_scope.text
    restored_assignment = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments/{assignment_id}/restore",
        headers=auth_headers,
        json={
            "operation_key": "a5-restore-assignment",
            "expected_assignment_version": 4,
            "expected_lock_version": 2,
            "scope_type": "chapter",
            "scope_target_id": chapter_id,
        },
    )
    assert restored_assignment.status_code == 200, restored_assignment.text
    assert restored_assignment.json()["assignment"]["id"] == assignment_id
    assert restored_assignment.json()["assignment"]["lock_version"] == 3

    disabled = await client.post(
        f"/api/projects/{project_id}/lore/elements/{chapter_element['id']}/disable",
        headers=auth_headers,
        json={"expected_version": chapter_element["lock_version"]},
    )
    assert disabled.status_code == 200, disabled.text
    current = await client.get(
        f"/api/projects/{project_id}/planning/lore-assignments",
        headers=auth_headers,
        params={"scope_type": "chapter", "scope_target_id": chapter_id},
    )
    assert current.status_code == 200
    assert current.json()["assignment_version"] == 5
    disabled_effective = next(
        item
        for item in current.json()["effective_elements"]
        if item["element_id"] == chapter_element["id"]
    )
    assert disabled_effective["generation_eligible"] is False
    assert disabled_effective["ineligible_reasons"] == ["element_disabled"]

    history = await client.get(
        f"/api/projects/{project_id}/planning/lore-assignments/history",
        headers=auth_headers,
        params={"element_id": chapter_element["id"]},
    )
    assert history.status_code == 200
    assert len(history.json()["assignments"]) == 1
    assert history.json()["assignments"][0]["id"] == assignment_id
    assert [
        event["action"]
        for event in history.json()["assignments"][0]["events"]
    ] == ["assign", "remove", "restore"]


@pytest.mark.usefixtures("clean_db")
async def test_remove_restore_reuses_row_and_appends_history(client, auth_headers):
    project_id, _ = await _initialized_project(client, auth_headers)
    element = await _create_element(client, auth_headers, project_id)
    assigned = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
        key="a3-history-assign-0001",
    )
    assignment_id = assigned.json()["assignment"]["id"]
    removed = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments/{assignment_id}/remove",
        headers=auth_headers,
        json={
            "operation_key": "a3-history-remove-0001",
            "expected_assignment_version": 2,
            "expected_lock_version": 1,
            "scope_type": "novel",
            "scope_target_id": project_id,
        },
    )
    restored = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments/{assignment_id}/restore",
        headers=auth_headers,
        json={
            "operation_key": "a3-history-restore-0001",
            "expected_assignment_version": 3,
            "expected_lock_version": 2,
            "scope_type": "novel",
            "scope_target_id": project_id,
        },
    )
    assert removed.status_code == restored.status_code == 200
    assert restored.json()["assignment"]["id"] == assignment_id
    assert restored.json()["assignment"]["lock_version"] == 3
    history = await client.get(
        f"/api/projects/{project_id}/planning/lore-assignments/history",
        headers=auth_headers,
        params={"element_id": element["id"]},
    )
    assert history.status_code == 200
    assert len(history.json()["assignments"]) == 1
    assert history.json()["assignments"][0]["scope"]["title"] == "整部小说"
    assert [event["action"] for event in history.json()["assignments"][0]["events"]] == [
        "assign",
        "remove",
        "restore",
    ]
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(PlanningLoreAssignment.id))) == 1
        assert await session.scalar(select(func.count(PlanningLoreAssignmentEvent.id))) == 3


@pytest.mark.usefixtures("clean_db")
async def test_removed_direct_assignment_does_not_cancel_inheritance(client, auth_headers):
    project_id, _ = await _initialized_project(client, auth_headers)
    part_id, chapter_id = await _create_structure(client, auth_headers, project_id)
    element = await _create_element(client, auth_headers, project_id)
    inherited = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
        key="a3-inherit-novel-0001",
    )
    inherited_id = inherited.json()["assignment"]["id"]
    direct = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=2,
        scope_type="chapter",
        scope_target_id=chapter_id,
        key="a3-inherit-chapter-0001",
    )
    assignment_id = direct.json()["assignment"]["id"]
    inherited_remove = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments/{inherited_id}/remove",
        headers=auth_headers,
        json={
            "operation_key": "a3-inherit-readonly-0001",
            "expected_assignment_version": 3,
            "expected_lock_version": 1,
            "scope_type": "chapter",
            "scope_target_id": chapter_id,
        },
    )
    assert inherited_remove.status_code == 409
    assert inherited_remove.json()["detail"]["code"] == (
        "PLANNING_ASSIGNMENT_INHERITED_READ_ONLY"
    )
    assert inherited_remove.json()["detail"]["source_scope"]["title"] == "整部小说"
    removed = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments/{assignment_id}/remove",
        headers=auth_headers,
        json={
            "operation_key": "a3-inherit-remove-0001",
            "expected_assignment_version": 3,
            "expected_lock_version": 1,
            "scope_type": "chapter",
            "scope_target_id": chapter_id,
        },
    )
    assert removed.status_code == 200
    result = await client.get(
        f"/api/projects/{project_id}/planning/lore-assignments",
        headers=auth_headers,
        params={"scope_type": "chapter", "scope_target_id": chapter_id},
    )
    assert result.status_code == 200
    body = result.json()
    assert body["counts"]["direct_removed"] == 1
    assert body["counts"]["effective"] == 1
    effective = body["effective_elements"][0]
    assert effective["direct_assignments"] == []
    assert len(effective["inherited_from"]) == 1
    assert effective["inherited_from"][0]["scope"]["scope_type"] == "novel"


@pytest.mark.usefixtures("clean_db")
async def test_later_ineligible_assignment_remains_visible_but_not_generatable(
    client, auth_headers
):
    project_id, _ = await _initialized_project(client, auth_headers)
    element = await _create_element(client, auth_headers, project_id)
    assigned = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
        key="a3-ineligible-assign-0001",
    )
    assignment_id = assigned.json()["assignment"]["id"]
    async with TestSessionLocal() as session:
        await session.execute(
            update(SettingElement)
            .where(SettingElement.id == element["id"])
            .values(enabled=False, content_version=2, lock_version=2)
        )
        await session.commit()

    result = await client.get(
        f"/api/projects/{project_id}/planning/lore-assignments",
        headers=auth_headers,
        params={"scope_type": "novel", "scope_target_id": project_id},
    )
    current = result.json()["effective_elements"][0]
    assert current["generation_eligible"] is False
    assert current["ineligible_reasons"] == ["element_disabled"]
    assert current["content_changed_since_any_assignment"] is True
    assert current["current_content_version"] == 2

    removed = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments/{assignment_id}/remove",
        headers=auth_headers,
        json={
            "operation_key": "a3-ineligible-remove-0001",
            "expected_assignment_version": 2,
            "expected_lock_version": 1,
            "scope_type": "novel",
            "scope_target_id": project_id,
        },
    )
    assert removed.status_code == 200
    blocked_restore = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments/{assignment_id}/restore",
        headers=auth_headers,
        json={
            "operation_key": "a3-ineligible-restore-0001",
            "expected_assignment_version": 3,
            "expected_lock_version": 2,
            "scope_type": "novel",
            "scope_target_id": project_id,
        },
    )
    assert blocked_restore.status_code == 409
    assert blocked_restore.json()["detail"] == {
        "code": "PLANNING_ELEMENT_INELIGIBLE",
        "message": "该设定当前不可用于生成，系统没有创建分配。",
        "retryable": False,
        "recommended_action": "review_lore_element",
        "ineligible_reasons": ["element_disabled"],
    }
    pending = await _create_element(
        client,
        auth_headers,
        project_id,
        name="待确认角色",
        field_states={"identity": "needs_confirmation"},
    )
    blocked_create = await _assign(
        client,
        auth_headers,
        project_id,
        pending,
        version=3,
        scope_type="novel",
        scope_target_id=project_id,
        key="a3-ineligible-create-0001",
    )
    assert blocked_create.status_code == 409
    assert blocked_create.json()["detail"]["ineligible_reasons"] == [
        "fields_need_confirmation"
    ]


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("candidate", "element_candidate"),
        ("rejected", "element_rejected"),
        ("archived", "element_archived"),
        ("merged", "element_merged"),
        ("type_archived", "type_archived"),
    ],
)
async def test_all_later_ineligible_states_remain_visible(
    client, auth_headers, state, reason
):
    project_id, _ = await _initialized_project(client, auth_headers)
    element = await _create_element(client, auth_headers, project_id)
    assigned = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
        key=f"a3-later-state-{state}",
    )
    assert assigned.status_code == 200
    survivor = None
    if state == "merged":
        survivor = await _create_element(client, auth_headers, project_id, name="保留角色")
    async with TestSessionLocal() as session:
        if state in {"candidate", "rejected"}:
            await session.execute(
                update(SettingElement)
                .where(SettingElement.id == element["id"])
                .values(confirmation_status=state)
            )
        elif state == "archived":
            await session.execute(
                update(SettingElement)
                .where(SettingElement.id == element["id"])
                .values(lifecycle_status="archived")
            )
        elif state == "merged":
            await session.execute(
                update(SettingElement)
                .where(SettingElement.id == element["id"])
                .values(
                    lifecycle_status="merged",
                    enabled=False,
                    merged_into_element_id=survivor["id"],
                )
            )
        else:
            stored_element = await session.scalar(
                select(SettingElement).where(SettingElement.id == element["id"])
            )
            await session.execute(
                update(SettingType)
                .where(SettingType.id == stored_element.type_id)
                .values(status="archived")
            )
        await session.commit()
    response = await client.get(
        f"/api/projects/{project_id}/planning/lore-assignments",
        headers=auth_headers,
        params={"scope_type": "novel", "scope_target_id": project_id},
    )
    assert response.status_code == 200
    effective = response.json()["effective_elements"][0]
    assert effective["generation_eligible"] is False
    assert reason in effective["ineligible_reasons"]
    if state == "merged":
        assert effective["element"]["merged_into_element_id"] == survivor["id"]


@pytest.mark.usefixtures("clean_db")
async def test_assignment_idempotency_version_and_operation_recovery(client, auth_headers):
    project_id, _ = await _initialized_project(client, auth_headers)
    element = await _create_element(client, auth_headers, project_id)
    payload = {
        "operation_key": "a3-idempotent-assign-0001",
        "expected_assignment_version": 1,
        "element_id": element["id"],
        "expected_element_content_version": 1,
        "scope_type": "novel",
        "scope_target_id": project_id,
    }
    first = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments",
        headers=auth_headers,
        json=payload,
    )
    replay = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments",
        headers=auth_headers,
        json=payload,
    )
    recovered = await client.get(
        f"/api/projects/{project_id}/planning/operations/by-key/{payload['operation_key']}",
        headers=auth_headers,
    )
    conflict = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments",
        headers=auth_headers,
        json={**payload, "scope_target_id": uuid.uuid4().hex},
    )
    assert first.status_code == replay.status_code == recovered.status_code == 200
    assert first.json()["receipt_kind"] == "assignment"
    assert first.json()["replayed"] is False
    assert replay.json()["replayed"] is recovered.json()["replayed"] is True
    assert first.json()["receipt_id"] == replay.json()["receipt_id"]
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "PLANNING_OPERATION_KEY_REUSED"
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(PlanningLoreAssignment.id))) == 1
        assert await session.scalar(select(func.count(PlanningMutationOperation.id))) == 1


@pytest.mark.usefixtures("clean_db")
async def test_corrupt_nested_assignment_receipt_fails_closed(client, auth_headers):
    project_id, _ = await _initialized_project(client, auth_headers)
    element = await _create_element(client, auth_headers, project_id)
    created = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
        key="a3-corrupt-receipt-0001",
    )
    assert created.status_code == 200
    async with TestSessionLocal() as session:
        operation = await session.scalar(
            select(PlanningMutationOperation).where(
                PlanningMutationOperation.project_id == project_id,
                PlanningMutationOperation.operation_key == "a3-corrupt-receipt-0001",
            )
        )
        snapshot = dict(operation.result_snapshot)
        snapshot["assignment"] = {"id": "incomplete"}
        operation.result_snapshot = snapshot
        await session.commit()
    recovered = await client.get(
        f"/api/projects/{project_id}/planning/operations/by-key/a3-corrupt-receipt-0001",
        headers=auth_headers,
    )
    assert recovered.status_code == 409
    assert recovered.json()["detail"]["code"] == "PLANNING_OPERATION_CORRUPT"


@pytest.mark.usefixtures("clean_db")
async def test_assignment_and_element_versions_fail_closed(client, auth_headers):
    project_id, _ = await _initialized_project(client, auth_headers)
    element = await _create_element(client, auth_headers, project_id)
    other = await _create_element(client, auth_headers, project_id, name="其他角色")
    assigned = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
        key="a3-version-assign-0001",
    )
    assignment_id = assigned.json()["assignment"]["id"]
    stale_root = await _assign(
        client,
        auth_headers,
        project_id,
        other,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
        key="a3-version-stale-root",
    )
    stale_row = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments/{assignment_id}/remove",
        headers=auth_headers,
        json={
            "operation_key": "a3-version-stale-row",
            "expected_assignment_version": 2,
            "expected_lock_version": 99,
            "scope_type": "novel",
            "scope_target_id": project_id,
        },
    )
    duplicate = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=2,
        scope_type="novel",
        scope_target_id=project_id,
        key="a3-version-duplicate",
    )
    async with TestSessionLocal() as session:
        await session.execute(
            update(SettingElement)
            .where(SettingElement.id == other["id"])
            .values(content_version=2, lock_version=2)
        )
        await session.commit()
    stale_element = await _assign(
        client,
        auth_headers,
        project_id,
        other,
        version=2,
        scope_type="novel",
        scope_target_id=project_id,
        key="a3-version-stale-element",
    )
    assert stale_root.status_code == stale_row.status_code == 409
    assert duplicate.status_code == stale_element.status_code == 409
    assert stale_root.json()["detail"]["code"] == "PLANNING_ASSIGNMENT_VERSION_CONFLICT"
    assert stale_row.json()["detail"]["code"] == "PLANNING_ASSIGNMENT_LOCK_CONFLICT"
    assert duplicate.json()["detail"]["code"] == "PLANNING_ASSIGNMENT_EXISTS"
    assert stale_element.json()["detail"]["code"] == "PLANNING_ELEMENT_VERSION_CONFLICT"
    async with TestSessionLocal() as session:
        plan = await session.scalar(select(NovelPlan).where(NovelPlan.project_id == project_id))
        assert plan.assignment_version == 2
        assert await session.scalar(select(func.count(PlanningLoreAssignment.id))) == 1
        assert await session.scalar(select(func.count(PlanningMutationOperation.id))) == 1


@pytest.mark.usefixtures("clean_db")
async def test_active_assignment_blocks_scope_archive_and_binding_count_is_live(
    client, auth_headers
):
    project_id, _ = await _initialized_project(client, auth_headers)
    part_id, chapter_id = await _create_structure(client, auth_headers, project_id)
    element = await _create_element(client, auth_headers, project_id)
    assigned = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="chapter",
        scope_target_id=chapter_id,
        key="a3-archive-assign-0001",
    )
    assignment_id = assigned.json()["assignment"]["id"]
    blocked = await client.post(
        f"/api/projects/{project_id}/planning/chapters/{chapter_id}/archive",
        headers=auth_headers,
        json={
            "operation_key": "a3-archive-chapter-blocked",
            "expected_structure_version": 3,
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "PLANNING_SCOPE_HAS_ACTIVE_ASSIGNMENTS"

    detail = await client.get(
        f"/api/projects/{project_id}/lore/elements/{element['id']}",
        headers=auth_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["binding_count"] == 1
    listing = await client.get(
        f"/api/projects/{project_id}/lore/elements", headers=auth_headers
    )
    listed = next(item for item in listing.json()["items"] if item["id"] == element["id"])
    assert listed["binding_count"] == 1

    removed = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments/{assignment_id}/remove",
        headers=auth_headers,
        json={
            "operation_key": "a3-archive-remove-0001",
            "expected_assignment_version": 2,
            "expected_lock_version": 1,
            "scope_type": "chapter",
            "scope_target_id": chapter_id,
        },
    )
    assert removed.status_code == 200
    archived = await client.post(
        f"/api/projects/{project_id}/planning/chapters/{chapter_id}/archive",
        headers=auth_headers,
        json={
            "operation_key": "a3-archive-chapter-ok",
            "expected_structure_version": 3,
        },
    )
    assert archived.status_code == 200
    archived_scope = await client.get(
        f"/api/projects/{project_id}/planning/lore-assignments",
        headers=auth_headers,
        params={"scope_type": "chapter", "scope_target_id": chapter_id},
    )
    assert archived_scope.status_code == 200
    assert archived_scope.json()["scope"]["status"] == "archived"
    assert archived_scope.json()["direct_assignments"][0]["generation_eligible"] is False
    assert archived_scope.json()["direct_assignments"][0]["ineligible_reasons"] == [
        "assignment_removed",
        "scope_archived",
    ]
    other = await _create_element(client, auth_headers, project_id, name="宁海")
    blocked_add = await _assign(
        client,
        auth_headers,
        project_id,
        other,
        version=3,
        scope_type="chapter",
        scope_target_id=chapter_id,
        key="a3-archive-add-blocked",
    )
    blocked_restore = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments/{assignment_id}/restore",
        headers=auth_headers,
        json={
            "operation_key": "a3-archive-restore-blocked",
            "expected_assignment_version": 3,
            "expected_lock_version": 2,
            "scope_type": "chapter",
            "scope_target_id": chapter_id,
        },
    )
    assert blocked_add.status_code == blocked_restore.status_code == 409
    assert blocked_add.json()["detail"]["code"] == "PLANNING_SCOPE_ARCHIVED"
    assert blocked_restore.json()["detail"]["code"] == "PLANNING_SCOPE_ARCHIVED"


@pytest.mark.usefixtures("clean_db")
async def test_active_part_assignment_blocks_part_archive(client, auth_headers):
    project_id, _ = await _initialized_project(client, auth_headers)
    part = await client.post(
        f"/api/projects/{project_id}/planning/parts",
        headers=auth_headers,
        json={
            "operation_key": "a3-part-only-create",
            "expected_structure_version": 1,
            "title": "无章节篇章",
        },
    )
    part_id = part.json()["affected_node"]["id"]
    element = await _create_element(client, auth_headers, project_id)
    assigned = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="part",
        scope_target_id=part_id,
        key="a3-part-archive-assign",
    )
    assert assigned.status_code == 200
    blocked = await client.post(
        f"/api/projects/{project_id}/planning/parts/{part_id}/archive",
        headers=auth_headers,
        json={
            "operation_key": "a3-part-archive-blocked",
            "expected_structure_version": 2,
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "PLANNING_SCOPE_HAS_ACTIVE_ASSIGNMENTS"
    assert blocked.json()["detail"]["active_assignment_count"] == 1


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize("mode", ["legacy", "migrating"])
async def test_assignment_write_fails_closed_for_mode_change_and_other_owner(
    client, auth_headers, second_auth_headers, mode
):
    project_id, _ = await _initialized_project(client, auth_headers)
    element = await _create_element(client, auth_headers, project_id)
    forbidden = await _assign(
        client,
        second_auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
        key="a3-owner-forbidden",
    )
    assert forbidden.status_code == 403
    async with TestSessionLocal() as session:
        await session.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(lore_storage_mode=mode)
        )
        await session.commit()
    blocked = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
        key="a3-mode-fail-closed",
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "PLANNING_LORE_MIGRATION_REQUIRED"
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(PlanningLoreAssignment.id))) == 0


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize(
    "legacy_kind", ["outline", "chapter", "story_memory", "malformed_memory"]
)
async def test_late_legacy_data_blocks_assignment_without_mutation(
    client, auth_headers, legacy_kind
):
    project_id, _ = await _initialized_project(client, auth_headers)
    element = await _create_element(client, auth_headers, project_id)
    async with TestSessionLocal() as session:
        if legacy_kind == "outline":
            session.add(
                Outline(
                    project_id=project_id,
                    story_arc="不得覆盖的旧故事弧",
                    reveal_plan=[],
                    chapters=[],
                )
            )
        elif legacy_kind == "chapter":
            session.add(
                Chapter(
                    project_id=project_id,
                    chapter_num=1,
                    title="旧章节",
                    content="不得覆盖的旧正文",
                )
            )
        elif legacy_kind == "story_memory":
            session.add(
                StoryMemory(
                    project_id=project_id,
                    chapter_summaries=[{"chapter_num": 1, "summary": "旧摘要"}],
                )
            )
        else:
            session.add(
                StoryMemory(project_id=project_id, timeline="private-malformed-json")
            )
        await session.commit()

    response = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
        key=f"a3-late-legacy-{legacy_kind}",
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLANNING_LEGACY_IMPORT_REQUIRED"
    assert "private-malformed-json" not in response.text
    async with TestSessionLocal() as session:
        plan = await session.scalar(select(NovelPlan).where(NovelPlan.project_id == project_id))
        assert plan.assignment_version == 1
        assert await session.scalar(select(func.count(PlanningLoreAssignment.id))) == 0
        assert await session.scalar(select(func.count(PlanningLoreAssignmentEvent.id))) == 0
        assert await session.scalar(select(func.count(PlanningMutationOperation.id))) == 0
        if legacy_kind == "outline":
            stored = await session.scalar(select(Outline).where(Outline.project_id == project_id))
            assert stored.story_arc == "不得覆盖的旧故事弧"
        elif legacy_kind == "chapter":
            stored = await session.scalar(select(Chapter).where(Chapter.project_id == project_id))
            assert stored.content == "不得覆盖的旧正文"


@pytest.mark.usefixtures("clean_db")
async def test_assignment_write_rolls_back_on_maintenance_flip(
    client, auth_headers, monkeypatch
):
    project_id, _ = await _initialized_project(client, auth_headers)
    element = await _create_element(client, auth_headers, project_id)
    calls = 0

    def flip_after_mutation():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ProjectWriteFrozenError("maintenance")

    monkeypatch.setattr(
        "app.core.planning_assignment.ensure_project_writes_available",
        flip_after_mutation,
    )
    response = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
        key="a3-maintenance-assign-0001",
    )
    assert response.status_code == 503
    async with TestSessionLocal() as session:
        plan = await session.scalar(select(NovelPlan).where(NovelPlan.project_id == project_id))
        assert plan.assignment_version == 1
        assert await session.scalar(select(func.count(PlanningLoreAssignment.id))) == 0
        assert await session.scalar(select(func.count(PlanningLoreAssignmentEvent.id))) == 0
        assert await session.scalar(select(func.count(PlanningMutationOperation.id))) == 0


@pytest.mark.usefixtures("clean_db")
async def test_same_owner_cross_project_ids_are_not_assignable(client, auth_headers):
    first_id, _ = await _initialized_project(client, auth_headers, title="项目甲")
    second_id, _ = await _initialized_project(client, auth_headers, title="项目乙")
    first_element = await _create_element(client, auth_headers, first_id, name="甲角色")
    second_element = await _create_element(client, auth_headers, second_id, name="乙角色")
    second_part, _ = await _create_structure(client, auth_headers, second_id)

    foreign_element = await _assign(
        client,
        auth_headers,
        first_id,
        second_element,
        version=1,
        scope_type="novel",
        scope_target_id=first_id,
        key="a3-cross-project-element",
    )
    foreign_scope = await _assign(
        client,
        auth_headers,
        first_id,
        first_element,
        version=1,
        scope_type="part",
        scope_target_id=second_part,
        key="a3-cross-project-scope",
    )
    assert foreign_element.status_code == foreign_scope.status_code == 404
    assert foreign_element.json()["detail"]["code"] == "PLANNING_ELEMENT_NOT_FOUND"
    assert foreign_scope.json()["detail"]["code"] == "PLANNING_SCOPE_NOT_FOUND"
    second_assignment = await _assign(
        client,
        auth_headers,
        second_id,
        second_element,
        version=1,
        scope_type="novel",
        scope_target_id=second_id,
        key="a3-cross-project-source-assignment",
    )
    second_assignment_id = second_assignment.json()["assignment"]["id"]
    foreign_assignment = await client.post(
        f"/api/projects/{first_id}/planning/lore-assignments/{second_assignment_id}/remove",
        headers=auth_headers,
        json={
            "operation_key": "a3-cross-project-assignment",
            "expected_assignment_version": 1,
            "expected_lock_version": 1,
            "scope_type": "novel",
            "scope_target_id": first_id,
        },
    )
    assert foreign_assignment.status_code == 404
    assert foreign_assignment.json()["detail"]["code"] == "PLANNING_ASSIGNMENT_NOT_FOUND"
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count(PlanningLoreAssignment.id)).where(
                PlanningLoreAssignment.project_id == first_id
            )
        ) == 0
        assert await session.scalar(
            select(func.count(PlanningLoreAssignment.id)).where(
                PlanningLoreAssignment.project_id == second_id
            )
        ) == 1


@pytest.mark.usefixtures("clean_db")
async def test_postgresql_assignment_concurrency_has_one_winner(client, auth_headers):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("row-lock concurrency is verified on PostgreSQL")
    project_id, _ = await _initialized_project(client, auth_headers)
    first_element = await _create_element(client, auth_headers, project_id, name="甲")
    second_element = await _create_element(client, auth_headers, project_id, name="乙")
    first, second = await asyncio.gather(
        _assign(
            client,
            auth_headers,
            project_id,
            first_element,
            version=1,
            scope_type="novel",
            scope_target_id=project_id,
            key="a3-concurrent-assign-a",
        ),
        _assign(
            client,
            auth_headers,
            project_id,
            second_element,
            version=1,
            scope_type="novel",
            scope_target_id=project_id,
            key="a3-concurrent-assign-b",
        ),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    loser = first if first.status_code == 409 else second
    assert loser.json()["detail"]["code"] == "PLANNING_ASSIGNMENT_VERSION_CONFLICT"
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(PlanningLoreAssignment.id))) == 1
        assert await session.scalar(select(func.count(PlanningMutationOperation.id))) == 1


@pytest.mark.usefixtures("clean_db")
async def test_postgresql_same_assignment_key_replays_one_receipt(client, auth_headers):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("row-lock concurrency is verified on PostgreSQL")
    project_id, _ = await _initialized_project(client, auth_headers)
    element = await _create_element(client, auth_headers, project_id)
    first, second = await asyncio.gather(
        _assign(
            client,
            auth_headers,
            project_id,
            element,
            version=1,
            scope_type="novel",
            scope_target_id=project_id,
            key="a3-same-key-concurrent",
        ),
        _assign(
            client,
            auth_headers,
            project_id,
            element,
            version=1,
            scope_type="novel",
            scope_target_id=project_id,
            key="a3-same-key-concurrent",
        ),
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["receipt_id"] == second.json()["receipt_id"]
    assert sorted([first.json()["replayed"], second.json()["replayed"]]) == [
        False,
        True,
    ]
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(PlanningLoreAssignment.id))) == 1
        assert await session.scalar(select(func.count(PlanningLoreAssignmentEvent.id))) == 1
        assert await session.scalar(select(func.count(PlanningMutationOperation.id))) == 1


@pytest.mark.usefixtures("clean_db")
async def test_postgresql_assignment_and_archive_are_serialized(client, auth_headers):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("row-lock concurrency is verified on PostgreSQL")
    project_id, _ = await _initialized_project(client, auth_headers)
    _, chapter_id = await _create_structure(client, auth_headers, project_id)
    element = await _create_element(client, auth_headers, project_id)
    assignment, archive = await asyncio.gather(
        _assign(
            client,
            auth_headers,
            project_id,
            element,
            version=1,
            scope_type="chapter",
            scope_target_id=chapter_id,
            key="a3-archive-race-assign",
        ),
        client.post(
            f"/api/projects/{project_id}/planning/chapters/{chapter_id}/archive",
            headers=auth_headers,
            json={
                "operation_key": "a3-archive-race-archive",
                "expected_structure_version": 3,
            },
        ),
    )
    assert sorted([assignment.status_code, archive.status_code]) == [200, 409]
    loser = assignment if assignment.status_code == 409 else archive
    assert loser.json()["detail"]["code"] in {
        "PLANNING_SCOPE_ARCHIVED",
        "PLANNING_SCOPE_HAS_ACTIVE_ASSIGNMENTS",
    }
    async with TestSessionLocal() as session:
        chapter = await session.scalar(
            select(PlanningChapter).where(PlanningChapter.id == chapter_id)
        )
        assignment_count = await session.scalar(
            select(func.count(PlanningLoreAssignment.id))
        )
        assert (chapter.status, assignment_count) in {
            ("active", 1),
            ("archived", 0),
        }


@pytest.mark.usefixtures("clean_db")
async def test_postgresql_restore_race_has_one_winner(client, auth_headers):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("row-lock concurrency is verified on PostgreSQL")
    project_id, _ = await _initialized_project(client, auth_headers)
    element = await _create_element(client, auth_headers, project_id)
    assigned = await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
        key="a3-restore-race-assign",
    )
    assignment_id = assigned.json()["assignment"]["id"]
    removed = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments/{assignment_id}/remove",
        headers=auth_headers,
        json={
            "operation_key": "a3-restore-race-remove",
            "expected_assignment_version": 2,
            "expected_lock_version": 1,
            "scope_type": "novel",
            "scope_target_id": project_id,
        },
    )
    assert removed.status_code == 200

    async def restore(key):
        return await client.post(
            f"/api/projects/{project_id}/planning/lore-assignments/{assignment_id}/restore",
            headers=auth_headers,
            json={
                "operation_key": key,
                "expected_assignment_version": 3,
                "expected_lock_version": 2,
                "scope_type": "novel",
                "scope_target_id": project_id,
            },
        )

    first, second = await asyncio.gather(
        restore("a3-restore-race-a"), restore("a3-restore-race-b")
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    loser = first if first.status_code == 409 else second
    assert loser.json()["detail"]["code"] == "PLANNING_ASSIGNMENT_VERSION_CONFLICT"
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(PlanningLoreAssignment.id))) == 1
        assert await session.scalar(select(func.count(PlanningLoreAssignmentEvent.id))) == 3
        assert await session.scalar(select(func.count(PlanningMutationOperation.id))) == 3
