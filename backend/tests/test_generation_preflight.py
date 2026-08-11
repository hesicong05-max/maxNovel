"""DEV-017B1a durable zero-LLM generation preparation tests."""

import asyncio
import hashlib
import json
import uuid

import pytest
from sqlalchemy import delete, func, select, update

from app.core.maintenance import ProjectWriteFrozenError
from app.models.generation import ChapterGenerationRun
from app.models.lore import (
    ElementRelation,
    ElementRelationVersion,
    ElementVersion,
    SettingElement,
    SettingType,
)
from app.models.planning import NovelPlan, PlanningChapter, PlanningLoreAssignment
from app.models.project import Chapter, Outline, Project, StoryMemory
from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal


PROJECT_PAYLOAD = {
    "title": "生成准备测试",
    "genre": "玄幻",
    "total_chapters": 12,
    "chapter_word_count": 1800,
    "style_intensity": "standard",
}


async def _project_with_chapter(client, headers):
    created = await client.post("/api/projects", headers=headers, json=PROJECT_PAYLOAD)
    assert created.status_code == 200, created.text
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
    assert initialized.status_code == 200, initialized.text
    part = await client.post(
        f"/api/projects/{project_id}/planning/parts",
        headers=headers,
        json={
            "operation_key": "generation-part-create-0001",
            "expected_structure_version": 1,
            "title": "第一篇",
            "description": "主角进入星港。",
        },
    )
    assert part.status_code == 200, part.text
    part_id = part.json()["affected_node"]["id"]
    chapter = await client.post(
        f"/api/projects/{project_id}/planning/parts/{part_id}/chapters",
        headers=headers,
        json={
            "operation_key": "generation-chapter-create-0001",
            "expected_structure_version": 2,
            "title": "抵达星港",
            "summary": "沈星抵达星港并遇到守卫。",
            "target_word_count": 1800,
        },
    )
    assert chapter.status_code == 200, chapter.text
    return project_id, part_id, chapter.json()["affected_node"]["id"]


async def _element(client, headers, project_id, name, *, type_key="character"):
    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=headers,
        json={
            "operation_key": f"generation-element-{uuid.uuid4().hex}",
            "type_key": type_key,
            "name": name,
            "summary": f"{name}的已确认摘要",
            "payload": {"identity": name} if type_key == "character" else {},
            "sources": [
                {
                    "kind": "manual",
                    "reference": "世界观原稿",
                    "excerpt": f"{name}存在于原稿中。",
                    "is_primary": True,
                }
            ],
        },
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
):
    response = await client.post(
        f"/api/projects/{project_id}/planning/lore-assignments",
        headers=headers,
        json={
            "operation_key": f"generation-assignment-{uuid.uuid4().hex}",
            "expected_assignment_version": version,
            "element_id": element["id"],
            "expected_element_content_version": element["content_version"],
            "scope_type": scope_type,
            "scope_target_id": scope_target_id,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _prepare(
    client,
    headers,
    project_id,
    chapter_id,
    *,
    operation_key="generation-prepare-0001",
    structure_version=3,
    assignment_version=2,
    chapter_lock_version=1,
):
    return await client.post(
        f"/api/projects/{project_id}/planning/chapters/{chapter_id}/generation-runs",
        headers=headers,
        json={
            "operation_key": operation_key,
            "expected_structure_version": structure_version,
            "expected_assignment_version": assignment_version,
            "expected_chapter_lock_version": chapter_lock_version,
        },
    )


async def _seed_compact_assigned_elements(
    client, headers, project_id: str, count: int
) -> tuple[list[str], int]:
    """Seed the exact count boundary without paying API/idempotency overhead per row."""
    assert count >= 1
    first = await _element(client, headers, project_id, "E000")
    async with TestSessionLocal() as session:
        owner_id = await session.scalar(
            select(Project.owner_id).where(Project.id == project_id)
        )
        plan = await session.scalar(
            select(NovelPlan).where(NovelPlan.project_id == project_id)
        )
        setting_type = await session.scalar(
            select(SettingType)
            .join(SettingElement, SettingElement.type_id == SettingType.id)
            .where(SettingElement.id == first["id"])
        )
        assert owner_id and plan and setting_type
        element_ids = [first["id"]]
        session.add(
            PlanningLoreAssignment(
                project_id=project_id,
                plan_id=plan.id,
                element_id=first["id"],
                scope_type="novel",
                scope_target_id=project_id,
                element_content_version=1,
                status="active",
                lock_version=1,
                created_by=owner_id,
                updated_by=owner_id,
            )
        )
        for index in range(1, count):
            element_id = uuid.uuid4().hex
            name = f"E{index:03d}"
            element_ids.append(element_id)
            session.add(
                SettingElement(
                    id=element_id,
                    project_id=project_id,
                    type_id=setting_type.id,
                    name=name,
                    normalized_name=name.lower(),
                    summary="",
                    payload={},
                    payload_schema_revision=setting_type.schema_revision,
                    field_states={},
                    confirmation_status="confirmed",
                    lifecycle_status="active",
                    enabled=True,
                    content_version=1,
                    lock_version=1,
                )
            )
            await session.flush()
            session.add(
                ElementVersion(
                    element_id=element_id,
                    version_no=1,
                    type_id=setting_type.id,
                    type_schema_revision=setting_type.schema_revision,
                    name=name,
                    summary="",
                    payload={},
                    field_states={},
                    change_reason="boundary fixture",
                    created_by=owner_id,
                )
            )
            session.add(
                PlanningLoreAssignment(
                    project_id=project_id,
                    plan_id=plan.id,
                    element_id=element_id,
                    scope_type="novel",
                    scope_target_id=project_id,
                    element_content_version=1,
                    status="active",
                    lock_version=1,
                    created_by=owner_id,
                    updated_by=owner_id,
                )
            )
        plan.assignment_version = count + 1
        await session.commit()
    return element_ids, count + 1


async def _seed_relations(
    project_id: str, element_ids: list[str], count: int, *, start: int = 0
) -> None:
    pairs = [
        (source_id, target_id)
        for source_id in element_ids
        for target_id in element_ids
        if source_id != target_id
    ]
    assert len(pairs) >= start + count
    async with TestSessionLocal() as session:
        owner_id = await session.scalar(
            select(Project.owner_id).where(Project.id == project_id)
        )
        assert owner_id
        for index, (source_id, target_id) in enumerate(
            pairs[start : start + count], start=start
        ):
            relation_id = uuid.uuid4().hex
            session.add(
                ElementRelation(
                    id=relation_id,
                    project_id=project_id,
                    source_element_id=source_id,
                    target_element_id=target_id,
                    relation_key="ally",
                    forward_label="",
                    reverse_label="",
                    description="",
                    metadata_={"i": index},
                    status="active",
                    version_no=1,
                    lock_version=1,
                )
            )
            await session.flush()
            session.add(
                ElementRelationVersion(
                    relation_id=relation_id,
                    version_no=1,
                    source_element_id=source_id,
                    target_element_id=target_id,
                    relation_key="ally",
                    forward_label="",
                    reverse_label="",
                    description="",
                    metadata_={"i": index},
                    status="active",
                    change_reason="boundary fixture",
                    created_by=owner_id,
                )
            )
        await session.commit()


@pytest.mark.usefixtures("clean_db")
async def test_prepare_persists_authoritative_manifest_without_llm_or_legacy_writes(
    client, auth_headers, monkeypatch
):
    project_id, part_id, chapter_id = await _project_with_chapter(
        client, auth_headers
    )
    character = await _element(client, auth_headers, project_id, "沈星")
    location = await _element(
        client, auth_headers, project_id, "星港", type_key="location"
    )
    await _assign(
        client,
        auth_headers,
        project_id,
        character,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    await _assign(
        client,
        auth_headers,
        project_id,
        location,
        version=2,
        scope_type="chapter",
        scope_target_id=chapter_id,
    )
    relation = await client.post(
        f"/api/projects/{project_id}/lore/elements/{character['id']}/relations",
        headers=auth_headers,
        json={
            "operation_key": "generation-relation-create-0001",
            "target_element_id": location["id"],
            "source_expected_version": character["lock_version"],
            "target_expected_version": location["lock_version"],
            "relation_type": "ally",
            "description": "沈星当前位于星港。",
        },
    )
    assert relation.status_code == 201, relation.text

    def forbidden(*_args, **_kwargs):
        raise AssertionError("generation preparation must not call an LLM")

    from app.core.llm_client import llm_client

    monkeypatch.setattr(llm_client, "chat", forbidden)
    monkeypatch.setattr(llm_client, "chat_once", forbidden)
    monkeypatch.setattr(llm_client, "chat_stream", forbidden)

    before = {}
    async with TestSessionLocal() as session:
        for model in (Chapter, Outline, StoryMemory):
            before[model.__tablename__] = await session.scalar(
                select(func.count()).select_from(model).where(
                    model.project_id == project_id
                )
            )

    response = await _prepare(
        client,
        auth_headers,
        project_id,
        chapter_id,
        assignment_version=3,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "prepared"
    assert body["execution_mode"] == "preflight_only"
    assert body["ai_invoked"] is False
    assert body["billing_effect"] == "none"
    assert body["replayed"] is False
    assert body["planning_chapter_id"] == chapter_id
    assert body["structure_version"] == 3
    assert body["assignment_version"] == 3
    manifest = body["context_manifest"]
    assert manifest["part"]["id"] == part_id
    assert manifest["chapter"]["id"] == chapter_id
    assert manifest["counts"] == {"elements": 2, "relations": 1, "warnings": 0}
    assert manifest["foreshadow_actions"] == {"supported": False, "items": []}
    assert {item["version"]["name"] for item in manifest["elements"]} == {
        "沈星",
        "星港",
    }
    sources = {
        item["version"]["name"]: item["assignment_sources"]
        for item in manifest["elements"]
    }
    assert sources["沈星"][0]["scope_type"] == "novel"
    assert sources["星港"][0]["scope_type"] == "chapter"
    assert manifest["relations"][0]["relation_id"] == relation.json()["id"]
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert len(canonical) == body["context_size_bytes"]
    assert hashlib.sha256(canonical).hexdigest() == body["context_checksum"]

    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationRun).where(
                ChapterGenerationRun.project_id == project_id
            )
        ) == 1
        for model in (Chapter, Outline, StoryMemory):
            assert await session.scalar(
                select(func.count()).select_from(model).where(
                    model.project_id == project_id
                )
            ) == before[model.__tablename__]


@pytest.mark.usefixtures("clean_db")
async def test_prepare_replays_same_key_and_by_key_without_duplicate_rows(
    client, auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    element = await _element(client, auth_headers, project_id, "沈星")
    await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    first = await _prepare(client, auth_headers, project_id, chapter_id)
    replay = await _prepare(client, auth_headers, project_id, chapter_id)
    by_key = await client.get(
        f"/api/projects/{project_id}/planning/generation-runs/by-key/generation-prepare-0001",
        headers=auth_headers,
    )
    by_id = await client.get(
        f"/api/projects/{project_id}/planning/generation-runs/{first.json()['id']}",
        headers=auth_headers,
    )
    assert first.status_code == replay.status_code == by_key.status_code == by_id.status_code == 200
    assert first.json()["replayed"] is False
    assert replay.json()["replayed"] is True
    assert by_key.json()["replayed"] is True
    assert by_id.json()["replayed"] is True
    returned_ids = {
        first.json()["id"],
        replay.json()["id"],
        by_key.json()["id"],
        by_id.json()["id"],
    }
    assert returned_ids == {first.json()["id"]}
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationRun)
        ) == 1


@pytest.mark.usefixtures("clean_db")
async def test_missing_by_key_allows_only_original_prepare_retry(
    client, auth_headers
):
    project_id, _, _ = await _project_with_chapter(client, auth_headers)
    response = await client.get(
        f"/api/projects/{project_id}/planning/generation-runs/by-key/generation-missing-0001",
        headers=auth_headers,
    )
    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "GENERATION_RUN_NOT_FOUND",
        "message": "尚未找到该生成准备记录，请使用原请求安全重试。",
        "retryable": True,
        "recommended_action": "retry_original_prepare",
    }


@pytest.mark.usefixtures("clean_db")
async def test_prepare_rejects_reused_key_with_different_request(
    client, auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    element = await _element(client, auth_headers, project_id, "沈星")
    await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    first = await _prepare(client, auth_headers, project_id, chapter_id)
    conflict = await _prepare(
        client,
        auth_headers,
        project_id,
        chapter_id,
        chapter_lock_version=2,
    )
    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "GENERATION_OPERATION_KEY_REUSED"


@pytest.mark.usefixtures("clean_db")
async def test_prepare_fails_closed_for_empty_context_and_stale_versions(
    client, auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    empty = await _prepare(
        client, auth_headers, project_id, chapter_id, assignment_version=1
    )
    stale_structure = await _prepare(
        client,
        auth_headers,
        project_id,
        chapter_id,
        operation_key="generation-prepare-stale-structure",
        structure_version=2,
        assignment_version=1,
    )
    assert empty.status_code == 422
    assert empty.json()["detail"]["code"] == "GENERATION_CONTEXT_EMPTY"
    assert stale_structure.status_code == 409
    assert stale_structure.json()["detail"]["code"] == "GENERATION_STRUCTURE_VERSION_CONFLICT"
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationRun)
        ) == 0


@pytest.mark.usefixtures("clean_db")
async def test_prepare_fails_closed_when_assigned_lore_becomes_ineligible(
    client, auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    element = await _element(client, auth_headers, project_id, "沈星")
    await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    async with TestSessionLocal() as session:
        await session.execute(
            update(SettingElement)
            .where(SettingElement.id == element["id"])
            .values(enabled=False)
        )
        await session.commit()
    response = await _prepare(client, auth_headers, project_id, chapter_id)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "GENERATION_LORE_INELIGIBLE"
    assert detail["ineligible_elements"] == [
        {"element_id": element["id"], "reasons": ["element_disabled"]}
    ]


@pytest.mark.usefixtures("clean_db")
async def test_generation_runs_are_project_and_user_isolated(
    client, auth_headers, second_auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    element = await _element(client, auth_headers, project_id, "沈星")
    await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    first = await _prepare(client, auth_headers, project_id, chapter_id)
    assert first.status_code == 200
    forbidden_by_key = await client.get(
        f"/api/projects/{project_id}/planning/generation-runs/by-key/generation-prepare-0001",
        headers=second_auth_headers,
    )
    forbidden_by_id = await client.get(
        f"/api/projects/{project_id}/planning/generation-runs/{first.json()['id']}",
        headers=second_auth_headers,
    )
    assert forbidden_by_key.status_code == 403
    assert forbidden_by_id.status_code == 403


@pytest.mark.usefixtures("clean_db")
async def test_prepare_rejects_archived_chapter_without_writing_run(
    client, auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    element = await _element(client, auth_headers, project_id, "沈星")
    await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    async with TestSessionLocal() as session:
        await session.execute(
            update(PlanningChapter)
            .where(PlanningChapter.id == chapter_id)
            .values(status="archived")
        )
        await session.commit()
    response = await _prepare(client, auth_headers, project_id, chapter_id)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "GENERATION_SCOPE_ARCHIVED"
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationRun)
        ) == 0


@pytest.mark.usefixtures("clean_db")
async def test_prepare_rejects_missing_current_element_version(
    client, auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    element = await _element(client, auth_headers, project_id, "沈星")
    await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    async with TestSessionLocal() as session:
        await session.execute(
            delete(ElementVersion).where(ElementVersion.element_id == element["id"])
        )
        await session.commit()
    response = await _prepare(client, auth_headers, project_id, chapter_id)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "GENERATION_CONTEXT_INCOMPLETE"


@pytest.mark.usefixtures("clean_db")
async def test_prepare_rejects_oversized_context_without_truncation(
    client, auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    element = await _element(client, auth_headers, project_id, "沈星")
    await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    oversized = "界" * 70_000
    async with TestSessionLocal() as session:
        await session.execute(
            update(SettingElement)
            .where(SettingElement.id == element["id"])
            .values(summary=oversized)
        )
        await session.execute(
            update(ElementVersion)
            .where(
                ElementVersion.element_id == element["id"],
                ElementVersion.version_no == 1,
            )
            .values(summary=oversized)
        )
        await session.commit()
    response = await _prepare(client, auth_headers, project_id, chapter_id)
    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail["code"] == "GENERATION_CONTEXT_TOO_LARGE"
    assert detail["context_size_bytes"] > detail["limits"]["context_size_bytes"]
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationRun)
        ) == 0


@pytest.mark.usefixtures("clean_db")
async def test_precommit_maintenance_flip_rolls_back_prepared_run(
    client, auth_headers, monkeypatch
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    element = await _element(client, auth_headers, project_id, "沈星")
    await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    import app.core.generation_preflight as generation_preflight

    checks = 0

    def flip_on_precommit():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ProjectWriteFrozenError()

    monkeypatch.setattr(
        generation_preflight, "ensure_project_writes_available", flip_on_precommit
    )
    response = await _prepare(client, auth_headers, project_id, chapter_id)
    assert response.status_code == 503
    assert response.json()["code"] == "PROJECT_WRITE_FROZEN"
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationRun)
        ) == 0


@pytest.mark.usefixtures("clean_db")
async def test_corrupt_manifest_is_not_replayed_or_returned(
    client, auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    element = await _element(client, auth_headers, project_id, "沈星")
    await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    created = await _prepare(client, auth_headers, project_id, chapter_id)
    assert created.status_code == 200
    async with TestSessionLocal() as session:
        await session.execute(
            update(ChapterGenerationRun)
            .where(ChapterGenerationRun.id == created.json()["id"])
            .values(context_manifest={"schema_version": 1})
        )
        await session.commit()
    by_key = await client.get(
        f"/api/projects/{project_id}/planning/generation-runs/by-key/generation-prepare-0001",
        headers=auth_headers,
    )
    replay = await _prepare(client, auth_headers, project_id, chapter_id)
    assert by_key.status_code == replay.status_code == 409
    assert by_key.json()["detail"]["code"] == "GENERATION_RUN_CORRUPT"
    assert replay.json()["detail"]["code"] == "GENERATION_RUN_CORRUPT"


@pytest.mark.usefixtures("clean_db")
async def test_outer_run_version_tampering_is_not_returned(
    client, auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    element = await _element(client, auth_headers, project_id, "沈星")
    await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    created = await _prepare(client, auth_headers, project_id, chapter_id)
    assert created.status_code == 200
    async with TestSessionLocal() as session:
        await session.execute(
            update(ChapterGenerationRun)
            .where(ChapterGenerationRun.id == created.json()["id"])
            .values(assignment_version=999)
        )
        await session.commit()
    response = await client.get(
        f"/api/projects/{project_id}/planning/generation-runs/{created.json()['id']}",
        headers=auth_headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "GENERATION_RUN_CORRUPT"


@pytest.mark.usefixtures("clean_db")
async def test_outer_run_chapter_identity_tampering_is_not_returned(
    client, auth_headers
):
    project_id, part_id, chapter_id = await _project_with_chapter(
        client, auth_headers
    )
    second = await client.post(
        f"/api/projects/{project_id}/planning/parts/{part_id}/chapters",
        headers=auth_headers,
        json={
            "operation_key": "generation-second-chapter-0001",
            "expected_structure_version": 3,
            "title": "第二章",
            "summary": "第二章摘要",
        },
    )
    assert second.status_code == 200, second.text
    second_id = second.json()["affected_node"]["id"]
    element = await _element(client, auth_headers, project_id, "沈星")
    await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    created = await _prepare(
        client,
        auth_headers,
        project_id,
        chapter_id,
        structure_version=4,
    )
    assert created.status_code == 200, created.text
    async with TestSessionLocal() as session:
        await session.execute(
            update(ChapterGenerationRun)
            .where(ChapterGenerationRun.id == created.json()["id"])
            .values(planning_chapter_id=second_id)
        )
        await session.commit()
    response = await client.get(
        f"/api/projects/{project_id}/planning/generation-runs/{created.json()['id']}",
        headers=auth_headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "GENERATION_RUN_CORRUPT"


@pytest.mark.usefixtures("clean_db")
async def test_prepare_reports_content_drift_without_using_stale_version(
    client, auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    element = await _element(client, auth_headers, project_id, "沈星")
    await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    async with TestSessionLocal() as session:
        stored = await session.scalar(
            select(SettingElement).where(SettingElement.id == element["id"])
        )
        current = await session.scalar(
            select(ElementVersion).where(
                ElementVersion.element_id == element["id"],
                ElementVersion.version_no == 1,
            )
        )
        stored.content_version = 2
        stored.summary = "更新后的摘要"
        session.add(
            ElementVersion(
                element_id=stored.id,
                version_no=2,
                type_id=current.type_id,
                type_schema_revision=current.type_schema_revision,
                name=current.name,
                summary="更新后的摘要",
                payload=current.payload,
                field_states=current.field_states,
                change_reason="测试更新",
            )
        )
        await session.commit()
    response = await _prepare(client, auth_headers, project_id, chapter_id)
    assert response.status_code == 200, response.text
    manifest = response.json()["context_manifest"]
    assert manifest["elements"][0]["version"]["version_no"] == 2
    assert manifest["warnings"] == [
        {"code": "LORE_CHANGED_SINCE_ASSIGNMENT", "element_id": element["id"]}
    ]


@pytest.mark.usefixtures("clean_db")
async def test_prepare_rejects_element_version_type_identity_mismatch(
    client, auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    character = await _element(client, auth_headers, project_id, "沈星")
    await _element(client, auth_headers, project_id, "星港", type_key="location")
    await _assign(
        client,
        auth_headers,
        project_id,
        character,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    async with TestSessionLocal() as session:
        location_type_id = await session.scalar(
            select(SettingType.id).where(
                SettingType.project_id == project_id,
                SettingType.key == "location",
            )
        )
        await session.execute(
            update(ElementVersion)
            .where(
                ElementVersion.element_id == character["id"],
                ElementVersion.version_no == 1,
            )
            .values(type_id=location_type_id)
        )
        await session.commit()
    response = await _prepare(client, auth_headers, project_id, chapter_id)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "GENERATION_CONTEXT_INCOMPLETE"


@pytest.mark.usefixtures("clean_db")
async def test_prepare_rejects_relation_version_status_mismatch(
    client, auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    left = await _element(client, auth_headers, project_id, "沈星")
    right = await _element(client, auth_headers, project_id, "林舟")
    await _assign(
        client,
        auth_headers,
        project_id,
        left,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    await _assign(
        client,
        auth_headers,
        project_id,
        right,
        version=2,
        scope_type="novel",
        scope_target_id=project_id,
    )
    relation = await client.post(
        f"/api/projects/{project_id}/lore/elements/{left['id']}/relations",
        headers=auth_headers,
        json={
            "operation_key": "generation-mismatched-relation",
            "target_element_id": right["id"],
            "source_expected_version": left["lock_version"],
            "target_expected_version": right["lock_version"],
            "relation_type": "ally",
            "description": "两人暂时结盟。",
        },
    )
    assert relation.status_code == 201, relation.text
    async with TestSessionLocal() as session:
        await session.execute(
            update(ElementRelationVersion)
            .where(ElementRelationVersion.relation_id == relation.json()["id"])
            .values(status="archived")
        )
        await session.commit()
    response = await _prepare(
        client,
        auth_headers,
        project_id,
        chapter_id,
        assignment_version=3,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "GENERATION_CONTEXT_INCOMPLETE"


@pytest.mark.usefixtures("clean_db")
async def test_prepare_distinguishes_assignment_and_chapter_version_conflicts(
    client, auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    element = await _element(client, auth_headers, project_id, "沈星")
    await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    assignment = await _prepare(
        client,
        auth_headers,
        project_id,
        chapter_id,
        operation_key="generation-stale-assignment",
        assignment_version=1,
    )
    chapter = await _prepare(
        client,
        auth_headers,
        project_id,
        chapter_id,
        operation_key="generation-stale-chapter",
        chapter_lock_version=2,
    )
    assert assignment.status_code == chapter.status_code == 409
    assert assignment.json()["detail"]["code"] == "GENERATION_ASSIGNMENT_VERSION_CONFLICT"
    assert chapter.json()["detail"]["code"] == "GENERATION_CHAPTER_VERSION_CONFLICT"


@pytest.mark.usefixtures("clean_db")
async def test_element_count_boundary_accepts_100_and_rejects_101(
    client, auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    _, assignment_version = await _seed_compact_assigned_elements(
        client, auth_headers, project_id, 100
    )
    at_limit = await _prepare(
        client,
        auth_headers,
        project_id,
        chapter_id,
        operation_key="generation-elements-100",
        assignment_version=assignment_version,
    )
    assert at_limit.status_code in {200, 413}, at_limit.text
    if at_limit.status_code == 200:
        assert at_limit.json()["context_manifest"]["counts"]["elements"] == 100
    else:
        detail = at_limit.json()["detail"]
        assert detail["code"] == "GENERATION_CONTEXT_TOO_LARGE"
        assert detail["counts"]["elements"] == 100
        assert "context_size_bytes" in detail

    async with TestSessionLocal() as session:
        owner_id = await session.scalar(
            select(Project.owner_id).where(Project.id == project_id)
        )
        plan = await session.scalar(
            select(NovelPlan).where(NovelPlan.project_id == project_id)
        )
        setting_type = await session.scalar(
            select(SettingType).where(
                SettingType.project_id == project_id,
                SettingType.key == "character",
            )
        )
        assert owner_id and plan and setting_type
        element_id = uuid.uuid4().hex
        session.add(
            SettingElement(
                id=element_id,
                project_id=project_id,
                type_id=setting_type.id,
                name="E100",
                normalized_name="e100",
                summary="",
                payload={},
                payload_schema_revision=setting_type.schema_revision,
                field_states={},
                confirmation_status="confirmed",
                lifecycle_status="active",
                enabled=True,
                content_version=1,
                lock_version=1,
            )
        )
        await session.flush()
        session.add(
            ElementVersion(
                element_id=element_id,
                version_no=1,
                type_id=setting_type.id,
                type_schema_revision=setting_type.schema_revision,
                name="E100",
                summary="",
                payload={},
                field_states={},
                change_reason="boundary fixture",
                created_by=owner_id,
            )
        )
        session.add(
            PlanningLoreAssignment(
                project_id=project_id,
                plan_id=plan.id,
                element_id=element_id,
                scope_type="novel",
                scope_target_id=project_id,
                element_content_version=1,
                status="active",
                lock_version=1,
                created_by=owner_id,
                updated_by=owner_id,
            )
        )
        plan.assignment_version = assignment_version + 1
        await session.commit()
    over_limit = await _prepare(
        client,
        auth_headers,
        project_id,
        chapter_id,
        operation_key="generation-elements-101",
        assignment_version=assignment_version + 1,
    )
    assert over_limit.status_code == 413
    detail = over_limit.json()["detail"]
    assert detail["code"] == "GENERATION_CONTEXT_TOO_LARGE"
    assert detail["counts"] == {"elements": 101}
    assert detail["limits"] == {"elements": 100}
    assert "context_size_bytes" not in detail


@pytest.mark.usefixtures("clean_db")
async def test_relation_count_boundary_accepts_300_and_rejects_301(
    client, auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    element_ids, assignment_version = await _seed_compact_assigned_elements(
        client, auth_headers, project_id, 26
    )
    await _seed_relations(project_id, element_ids, 300)
    at_limit = await _prepare(
        client,
        auth_headers,
        project_id,
        chapter_id,
        operation_key="generation-relations-300",
        assignment_version=assignment_version,
    )
    assert at_limit.status_code in {200, 413}, at_limit.text
    if at_limit.status_code == 200:
        assert at_limit.json()["context_manifest"]["counts"]["relations"] == 300
    else:
        detail = at_limit.json()["detail"]
        assert detail["code"] == "GENERATION_CONTEXT_TOO_LARGE"
        assert detail["counts"]["relations"] == 300
        assert "context_size_bytes" in detail

    await _seed_relations(project_id, element_ids, 1, start=300)
    over_limit = await _prepare(
        client,
        auth_headers,
        project_id,
        chapter_id,
        operation_key="generation-relations-301",
        assignment_version=assignment_version,
    )
    assert over_limit.status_code == 413
    detail = over_limit.json()["detail"]
    assert detail["code"] == "GENERATION_CONTEXT_TOO_LARGE"
    assert detail["counts"] == {"elements": 26, "relations": 301}
    assert detail["limits"] == {"elements": 100, "relations": 300}
    assert "context_size_bytes" not in detail


@pytest.mark.usefixtures("clean_db")
async def test_postgres_concurrent_same_prepare_key_returns_one_durable_run(
    client, auth_headers
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("requires PostgreSQL row locking")
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    element = await _element(client, auth_headers, project_id, "沈星")
    await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    first, second = await asyncio.gather(
        _prepare(client, auth_headers, project_id, chapter_id),
        _prepare(client, auth_headers, project_id, chapter_id),
    )
    assert first.status_code == second.status_code == 200
    assert sorted([first.json()["replayed"], second.json()["replayed"]]) == [False, True]
    assert first.json()["id"] == second.json()["id"]
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationRun)
        ) == 1


@pytest.mark.usefixtures("clean_db")
async def test_prepare_recomputes_part_inheritance_after_chapter_move(
    client, auth_headers
):
    project_id, old_part_id, chapter_id = await _project_with_chapter(
        client, auth_headers
    )
    new_part = await client.post(
        f"/api/projects/{project_id}/planning/parts",
        headers=auth_headers,
        json={
            "operation_key": "generation-part-create-target",
            "expected_structure_version": 3,
            "title": "第二篇",
            "description": "星港之后的旅程。",
        },
    )
    assert new_part.status_code == 200, new_part.text
    new_part_id = new_part.json()["affected_node"]["id"]
    old_element = await _element(client, auth_headers, project_id, "旧篇法则")
    new_element = await _element(client, auth_headers, project_id, "新篇法则")
    await _assign(
        client,
        auth_headers,
        project_id,
        old_element,
        version=1,
        scope_type="part",
        scope_target_id=old_part_id,
    )
    await _assign(
        client,
        auth_headers,
        project_id,
        new_element,
        version=2,
        scope_type="part",
        scope_target_id=new_part_id,
    )
    before = await _prepare(
        client,
        auth_headers,
        project_id,
        chapter_id,
        operation_key="generation-prepare-before-move",
        structure_version=4,
        assignment_version=3,
    )
    assert before.status_code == 200, before.text
    assert [
        item["version"]["name"]
        for item in before.json()["context_manifest"]["elements"]
    ] == ["旧篇法则"]

    moved = await client.post(
        f"/api/projects/{project_id}/planning/structure/reorder",
        headers=auth_headers,
        json={
            "operation_key": "generation-move-chapter-0001",
            "expected_structure_version": 4,
            "parts": [
                {"part_id": old_part_id, "chapter_ids": []},
                {"part_id": new_part_id, "chapter_ids": [chapter_id]},
            ],
        },
    )
    assert moved.status_code == 200, moved.text
    after = await _prepare(
        client,
        auth_headers,
        project_id,
        chapter_id,
        operation_key="generation-prepare-after-move",
        structure_version=5,
        assignment_version=3,
        chapter_lock_version=2,
    )
    assert after.status_code == 200, after.text
    assert after.json()["context_manifest"]["part"]["id"] == new_part_id
    assert [
        item["version"]["name"]
        for item in after.json()["context_manifest"]["elements"]
    ] == ["新篇法则"]


@pytest.mark.usefixtures("clean_db")
async def test_physical_chapter_cleanup_cascades_generation_receipts(
    client, auth_headers
):
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    element = await _element(client, auth_headers, project_id, "沈星")
    await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    prepared = await _prepare(client, auth_headers, project_id, chapter_id)
    assert prepared.status_code == 200
    async with TestSessionLocal() as session:
        await session.execute(
            delete(PlanningLoreAssignment).where(
                PlanningLoreAssignment.project_id == project_id
            )
        )
        await session.execute(
            delete(PlanningChapter).where(PlanningChapter.id == chapter_id)
        )
        await session.commit()
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationRun).where(
                ChapterGenerationRun.project_id == project_id
            )
        ) == 0


@pytest.mark.usefixtures("clean_db")
async def test_postgres_prepare_and_assignment_race_is_old_snapshot_or_conflict(
    client, auth_headers
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("requires PostgreSQL row locking")
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    first = await _element(client, auth_headers, project_id, "甲")
    second = await _element(client, auth_headers, project_id, "乙")
    await _assign(
        client,
        auth_headers,
        project_id,
        first,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    prepare, assignment = await asyncio.gather(
        _prepare(
            client,
            auth_headers,
            project_id,
            chapter_id,
            operation_key="generation-race-assignment",
            assignment_version=2,
        ),
        client.post(
            f"/api/projects/{project_id}/planning/lore-assignments",
            headers=auth_headers,
            json={
                "operation_key": "generation-race-assignment-write",
                "expected_assignment_version": 2,
                "element_id": second["id"],
                "expected_element_content_version": second["content_version"],
                "scope_type": "chapter",
                "scope_target_id": chapter_id,
            },
        ),
    )
    assert assignment.status_code == 200, assignment.text
    if prepare.status_code == 200:
        assert [
            item["version"]["name"]
            for item in prepare.json()["context_manifest"]["elements"]
        ] == ["甲"]
        assert prepare.json()["assignment_version"] == 2
    else:
        assert prepare.status_code == 409, prepare.text
        assert (
            prepare.json()["detail"]["code"]
            == "GENERATION_ASSIGNMENT_VERSION_CONFLICT"
        )


@pytest.mark.usefixtures("clean_db")
async def test_postgres_prepare_and_lore_edit_never_mix_versions(
    client, auth_headers
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("requires PostgreSQL row locking")
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    element = await _element(client, auth_headers, project_id, "沈星")
    await _assign(
        client,
        auth_headers,
        project_id,
        element,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    prepare, edit = await asyncio.gather(
        _prepare(
            client,
            auth_headers,
            project_id,
            chapter_id,
            operation_key="generation-race-lore-edit",
        ),
        client.patch(
            f"/api/projects/{project_id}/lore/elements/{element['id']}",
            headers=auth_headers,
            json={
                "expected_version": 1,
                "name": "沈星",
                "summary": "沈星的第二版摘要",
                "payload": {"identity": "沈星"},
                "field_states": {},
            },
        ),
    )
    assert edit.status_code == 200, edit.text
    assert prepare.status_code == 200, prepare.text
    snapshot = prepare.json()["context_manifest"]["elements"][0]["version"]
    assert (snapshot["version_no"], snapshot["summary"]) in {
        (1, "沈星的已确认摘要"),
        (2, "沈星的第二版摘要"),
    }


@pytest.mark.usefixtures("clean_db")
async def test_postgres_prepare_and_relation_edit_never_mix_versions(
    client, auth_headers
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("requires PostgreSQL row locking")
    project_id, _, chapter_id = await _project_with_chapter(client, auth_headers)
    source = await _element(client, auth_headers, project_id, "甲")
    target = await _element(client, auth_headers, project_id, "乙")
    await _assign(
        client,
        auth_headers,
        project_id,
        source,
        version=1,
        scope_type="novel",
        scope_target_id=project_id,
    )
    await _assign(
        client,
        auth_headers,
        project_id,
        target,
        version=2,
        scope_type="novel",
        scope_target_id=project_id,
    )
    relation = await client.post(
        f"/api/projects/{project_id}/lore/elements/{source['id']}/relations",
        headers=auth_headers,
        json={
            "operation_key": "generation-race-relation-create",
            "target_element_id": target["id"],
            "source_expected_version": source["lock_version"],
            "target_expected_version": target["lock_version"],
            "relation_type": "ally",
            "description": "旧关系摘要",
        },
    )
    assert relation.status_code == 201, relation.text
    prepare, edit = await asyncio.gather(
        _prepare(
            client,
            auth_headers,
            project_id,
            chapter_id,
            operation_key="generation-race-relation-edit",
            assignment_version=3,
        ),
        client.patch(
            f"/api/projects/{project_id}/lore/relations/{relation.json()['id']}",
            headers=auth_headers,
            json={
                "expected_version": relation.json()["lock_version"],
                "forward_label": "并肩",
                "reverse_label": "并肩",
                "description": "新关系摘要",
                "metadata": {"chapter": 1},
            },
        ),
    )
    assert edit.status_code == 200, edit.text
    assert prepare.status_code == 200, prepare.text
    snapshot = prepare.json()["context_manifest"]["relations"][0]["version"]
    assert (snapshot["version_no"], snapshot["description"]) in {
        (1, "旧关系摘要"),
        (2, "新关系摘要"),
    }
