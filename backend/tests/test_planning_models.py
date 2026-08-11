"""Cross-database integrity checks for the DEV-017A1 planning model."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.lore import SettingElement, SettingType
from app.models.planning import (
    NovelPlan,
    PlanningChapter,
    PlanningLoreAssignment,
    PlanningLoreAssignmentEvent,
    PlanningMutationOperation,
    PlanningPart,
)
from app.models.project import Project
from tests.conftest import TestSessionLocal


PROJECT_PAYLOAD = {
    "title": "规划约束测试",
    "genre": "玄幻",
    "total_chapters": 10,
    "chapter_word_count": 1500,
    "style_intensity": "standard",
}


async def _create_project(client, headers, title: str) -> str:
    response = await client.post(
        "/api/projects",
        headers=headers,
        json={**PROJECT_PAYLOAD, "title": title},
    )
    assert response.status_code == 200
    return response.json()["id"]


async def _initialize(client, headers, project_id: str) -> str:
    response = await client.post(
        f"/api/projects/{project_id}/planning", headers=headers
    )
    assert response.status_code == 200
    return response.json()["id"]


async def _expect_integrity(instance) -> None:
    async with TestSessionLocal() as session:
        session.add(instance)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.usefixtures("clean_db")
async def test_planning_constraints_prevent_cross_project_and_ambiguous_rows(
    client, auth_headers
):
    project_a = await _create_project(client, auth_headers, "项目甲")
    project_b = await _create_project(client, auth_headers, "项目乙")
    plan_a = await _initialize(client, auth_headers, project_a)
    plan_b = await _initialize(client, auth_headers, project_b)

    async with TestSessionLocal() as session:
        owner_id = await session.scalar(
            select(Project.owner_id).where(Project.id == project_a)
        )
        type_a = SettingType(
            project_id=project_a,
            key="character",
            display_name="角色",
            is_builtin=True,
            field_schema={},
        )
        type_b = SettingType(
            project_id=project_b,
            key="character",
            display_name="角色",
            is_builtin=True,
            field_schema={},
        )
        session.add_all([type_a, type_b])
        await session.flush()
        element_a = SettingElement(
            project_id=project_a,
            type_id=type_a.id,
            name="甲角色",
            normalized_name="甲角色",
            payload={},
            field_states={},
        )
        element_b = SettingElement(
            project_id=project_b,
            type_id=type_b.id,
            name="乙角色",
            normalized_name="乙角色",
            payload={},
            field_states={},
        )
        part_a = PlanningPart(
            project_id=project_a,
            plan_id=plan_a,
            title="第一篇",
            position=1,
        )
        part_b = PlanningPart(
            project_id=project_b,
            plan_id=plan_b,
            title="第一篇",
            position=1,
        )
        session.add_all([element_a, element_b, part_a, part_b])
        await session.flush()
        chapter_a = PlanningChapter(
            project_id=project_a,
            plan_id=plan_a,
            part_id=part_a.id,
            title="第一章",
            position=1,
        )
        session.add(chapter_a)
        await session.commit()
        ids = {
            "owner": owner_id,
            "element_a": element_a.id,
            "element_b": element_b.id,
            "part_a": part_a.id,
            "part_b": part_b.id,
            "chapter_a": chapter_a.id,
        }

    await _expect_integrity(NovelPlan(project_id=project_a))
    await _expect_integrity(
        PlanningPart(
            project_id=project_a,
            plan_id=plan_b,
            title="跨项目篇章",
            position=2,
        )
    )
    await _expect_integrity(
        PlanningChapter(
            project_id=project_a,
            plan_id=plan_a,
            part_id=ids["part_b"],
            title="跨项目章节",
            position=2,
        )
    )
    await _expect_integrity(
        PlanningPart(
            project_id=project_a,
            plan_id=plan_a,
            title="重复活动位置",
            position=1,
        )
    )
    await _expect_integrity(
        PlanningChapter(
            project_id=project_a,
            plan_id=plan_a,
            part_id=ids["part_a"],
            title="重复活动章节位置",
            position=1,
        )
    )

    async with TestSessionLocal() as session:
        session.add_all(
            [
                PlanningPart(
                    project_id=project_a,
                    plan_id=plan_a,
                    title="归档位置允许保留",
                    position=1,
                    status="archived",
                ),
                PlanningChapter(
                    project_id=project_a,
                    plan_id=plan_a,
                    part_id=ids["part_a"],
                    title="归档章节位置允许保留",
                    position=1,
                    status="archived",
                ),
            ]
        )
        await session.commit()

    assignment_kwargs = {
        "project_id": project_a,
        "plan_id": plan_a,
        "scope_type": "novel",
        "scope_target_id": project_a,
        "element_content_version": 1,
        "created_by": ids["owner"],
        "updated_by": ids["owner"],
    }
    await _expect_integrity(
        PlanningLoreAssignment(
            **assignment_kwargs,
            element_id=ids["element_b"],
        )
    )
    await _expect_integrity(
        PlanningLoreAssignment(
            **{
                **assignment_kwargs,
                "scope_type": "part",
                "part_id": ids["part_a"],
                "scope_target_id": project_a,
            },
            element_id=ids["element_a"],
        )
    )
    await _expect_integrity(
        PlanningLoreAssignment(
            **{**assignment_kwargs, "element_content_version": 0},
            element_id=ids["element_a"],
        )
    )

    async with TestSessionLocal() as session:
        assignment = PlanningLoreAssignment(
            **assignment_kwargs,
            element_id=ids["element_a"],
        )
        session.add(assignment)
        await session.commit()
        assignment_id = assignment.id

    await _expect_integrity(
        PlanningLoreAssignment(
            **assignment_kwargs,
            element_id=ids["element_a"],
        )
    )
    await _expect_integrity(
        PlanningLoreAssignmentEvent(
            project_id=project_a,
            assignment_id=assignment_id,
            performed_by=ids["owner"],
            action="assign",
            previous_status=None,
            new_status="active",
            previous_lock_version=0,
            new_lock_version=1,
            element_content_version=0,
        )
    )

    operation_kwargs = {
        "project_id": project_a,
        "requested_by": ids["owner"],
        "operation_key": "planning-operation-0001",
        "operation_type": "create_part",
        "request_fingerprint": "a" * 64,
        "result_snapshot": {},
    }
    async with TestSessionLocal() as session:
        session.add(PlanningMutationOperation(**operation_kwargs))
        await session.commit()
    await _expect_integrity(PlanningMutationOperation(**operation_kwargs))
