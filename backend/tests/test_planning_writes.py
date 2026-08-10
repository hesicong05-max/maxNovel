"""DEV-017A2 safe planning structure mutation tests."""

import asyncio
from copy import deepcopy

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.legacy_json import read_legacy_json
from app.core.maintenance import ProjectWriteFrozenError
from app.models.planning import (
    NovelPlan,
    PlanningChapter,
    PlanningMutationOperation,
    PlanningPart,
)
from app.models.project import Chapter, Outline, Project, StoryMemory
from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal


PROJECT_PAYLOAD = {
    "title": "A2 结构写入测试",
    "genre": "玄幻",
    "total_chapters": 20,
    "chapter_word_count": 1800,
    "style_intensity": "standard",
}


async def _initialized_project(client, headers) -> tuple[str, dict]:
    created = await client.post("/api/projects", headers=headers, json=PROJECT_PAYLOAD)
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


def _command(key: str, version: int) -> dict:
    return {"operation_key": key, "expected_structure_version": version}


async def _create_part(client, headers, project_id, version, key, title):
    return await client.post(
        f"/api/projects/{project_id}/planning/parts",
        headers=headers,
        json={**_command(key, version), "title": title},
    )


async def _create_chapter(
    client, headers, project_id, part_id, version, key, title
):
    return await client.post(
        f"/api/projects/{project_id}/planning/parts/{part_id}/chapters",
        headers=headers,
        json={**_command(key, version), "title": title},
    )


@pytest.mark.usefixtures("clean_db")
async def test_create_replay_recovery_and_key_conflict(client, auth_headers):
    project_id, plan = await _initialized_project(client, auth_headers)
    payload = {**_command("part-create-0001", 1), "title": "第一篇"}

    first = await client.post(
        f"/api/projects/{project_id}/planning/parts",
        headers=auth_headers,
        json=payload,
    )
    replay = await client.post(
        f"/api/projects/{project_id}/planning/parts",
        headers=auth_headers,
        json=payload,
    )
    recovery = await client.get(
        f"/api/projects/{project_id}/planning/operations/by-key/part-create-0001",
        headers=auth_headers,
    )
    conflict = await client.post(
        f"/api/projects/{project_id}/planning/parts",
        headers=auth_headers,
        json={**payload, "title": "不同篇章"},
    )

    assert first.status_code == replay.status_code == recovery.status_code == 200
    assert first.json()["replayed"] is False
    assert replay.json()["replayed"] is recovery.json()["replayed"] is True
    assert first.json()["receipt_id"] == replay.json()["receipt_id"]
    assert first.json()["new_structure_version"] == 2
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "PLANNING_OPERATION_KEY_REUSED"
    current = await client.get(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )
    assert current.json()["structure_version"] == 2
    assert [item["title"] for item in current.json()["parts"]] == ["第一篇"]
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(PlanningPart.id))) == 1
        assert (
            await session.scalar(select(func.count(PlanningMutationOperation.id)))
            == 1
        )


@pytest.mark.usefixtures("clean_db")
async def test_crud_versions_and_node_lock_conflict(client, auth_headers):
    project_id, _ = await _initialized_project(client, auth_headers)
    part_response = await _create_part(
        client, auth_headers, project_id, 1, "part-create-0002", "旧篇名"
    )
    part_id = part_response.json()["affected_node"]["id"]
    renamed = await client.patch(
        f"/api/projects/{project_id}/planning/parts/{part_id}",
        headers=auth_headers,
        json={
            **_command("part-update-0001", 2),
            "expected_lock_version": 1,
            "title": "新篇名",
        },
    )
    stale_node = await client.patch(
        f"/api/projects/{project_id}/planning/parts/{part_id}",
        headers=auth_headers,
        json={
            **_command("part-update-0002", 3),
            "expected_lock_version": 1,
            "title": "过期修改",
        },
    )
    assert renamed.status_code == 200
    assert renamed.json()["affected_node"]["lock_version"] == 2
    assert stale_node.status_code == 409
    assert stale_node.json()["detail"]["code"] == "PLANNING_NODE_VERSION_CONFLICT"

    chapter = await _create_chapter(
        client, auth_headers, project_id, part_id, 3, "chapter-create-0001", "第一章"
    )
    assert chapter.status_code == 200
    chapter_id = chapter.json()["affected_node"]["id"]
    updated = await client.patch(
        f"/api/projects/{project_id}/planning/chapters/{chapter_id}",
        headers=auth_headers,
        json={
            **_command("chapter-update-0001", 4),
            "expected_lock_version": 1,
            "title": "开幕",
            "target_word_count": 2200,
        },
    )
    assert updated.status_code == 200
    tree = await client.get(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )
    assert tree.json()["structure_version"] == 5
    assert tree.json()["parts"][0]["title"] == "新篇名"
    assert tree.json()["parts"][0]["chapters"][0]["title"] == "开幕"


@pytest.mark.usefixtures("clean_db")
async def test_archive_restore_is_non_cascading_and_append_only(client, auth_headers):
    project_id, _ = await _initialized_project(client, auth_headers)
    part = await _create_part(
        client, auth_headers, project_id, 1, "part-create-archive", "待归档篇"
    )
    part_id = part.json()["affected_node"]["id"]
    chapter = await _create_chapter(
        client,
        auth_headers,
        project_id,
        part_id,
        2,
        "chapter-create-archive",
        "待归档章",
    )
    chapter_id = chapter.json()["affected_node"]["id"]

    blocked = await client.post(
        f"/api/projects/{project_id}/planning/parts/{part_id}/archive",
        headers=auth_headers,
        json=_command("part-archive-blocked", 3),
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == {
        "code": "PLANNING_PART_NOT_EMPTY",
        "message": "该篇章仍包含章节，请先移动后再归档。",
        "retryable": False,
        "recommended_action": "move_chapters_first",
        "active_chapter_count": 1,
        "archived_chapter_count": 0,
    }
    archived = await client.post(
        f"/api/projects/{project_id}/planning/chapters/{chapter_id}/archive",
        headers=auth_headers,
        json=_command("chapter-archive-0001", 3),
    )
    assert archived.status_code == 200
    still_blocked = await client.post(
        f"/api/projects/{project_id}/planning/parts/{part_id}/archive",
        headers=auth_headers,
        json=_command("part-archive-blocked2", 4),
    )
    assert still_blocked.status_code == 409
    assert still_blocked.json()["detail"]["archived_chapter_count"] == 1

    restored = await client.post(
        f"/api/projects/{project_id}/planning/chapters/{chapter_id}/restore",
        headers=auth_headers,
        json=_command("chapter-restore-0001", 4),
    )
    replay_state = await client.post(
        f"/api/projects/{project_id}/planning/chapters/{chapter_id}/restore",
        headers=auth_headers,
        json=_command("chapter-restore-noop", 5),
    )
    assert restored.status_code == replay_state.status_code == 200
    assert restored.json()["changed"] is True
    assert replay_state.json()["changed"] is False
    assert replay_state.json()["new_structure_version"] == 6


@pytest.mark.usefixtures("clean_db")
async def test_atomic_full_reorder_moves_chapter_and_preserves_archived(client, auth_headers):
    project_id, _ = await _initialized_project(client, auth_headers)
    first = await _create_part(
        client, auth_headers, project_id, 1, "part-create-r1", "篇一"
    )
    second = await _create_part(
        client, auth_headers, project_id, 2, "part-create-r2", "篇二"
    )
    first_id = first.json()["affected_node"]["id"]
    second_id = second.json()["affected_node"]["id"]
    chapter_a = await _create_chapter(
        client, auth_headers, project_id, first_id, 3, "chapter-create-r1", "A"
    )
    chapter_b = await _create_chapter(
        client, auth_headers, project_id, first_id, 4, "chapter-create-r2", "B"
    )
    chapter_c = await _create_chapter(
        client, auth_headers, project_id, second_id, 5, "chapter-create-r3", "C"
    )
    a_id = chapter_a.json()["affected_node"]["id"]
    b_id = chapter_b.json()["affected_node"]["id"]
    c_id = chapter_c.json()["affected_node"]["id"]
    archived = await client.post(
        f"/api/projects/{project_id}/planning/chapters/{b_id}/archive",
        headers=auth_headers,
        json=_command("chapter-archive-reorder", 6),
    )
    assert archived.status_code == 200

    reorder = await client.post(
        f"/api/projects/{project_id}/planning/structure/reorder",
        headers=auth_headers,
        json={
            **_command("structure-reorder-0001", 7),
            "parts": [
                {"part_id": second_id, "chapter_ids": [c_id, a_id]},
                {"part_id": first_id, "chapter_ids": []},
            ],
        },
    )
    assert reorder.status_code == 200, reorder.text
    assert reorder.json()["structure"]["chapter_count"] == 2
    assert reorder.json()["structure"]["changed_chapter_count"] >= 1
    tree = await client.get(
        f"/api/projects/{project_id}/planning", headers=auth_headers
    )
    assert [part["id"] for part in tree.json()["parts"][:2]] == [second_id, first_id]
    second_part = next(part for part in tree.json()["parts"] if part["id"] == second_id)
    assert [chapter["id"] for chapter in second_part["chapters"] if chapter["status"] == "active"] == [c_id, a_id]
    archived_row = next(
        chapter
        for part in tree.json()["parts"]
        for chapter in part["chapters"]
        if chapter["id"] == b_id
    )
    assert archived_row["status"] == "archived"
    assert archived_row["part_id"] == first_id


@pytest.mark.usefixtures("clean_db")
async def test_invalid_or_stale_reorder_is_zero_write(client, auth_headers):
    project_id, _ = await _initialized_project(client, auth_headers)
    part = await _create_part(
        client, auth_headers, project_id, 1, "part-create-invalid", "唯一篇"
    )
    part_id = part.json()["affected_node"]["id"]
    invalid = await client.post(
        f"/api/projects/{project_id}/planning/structure/reorder",
        headers=auth_headers,
        json={**_command("reorder-invalid-0001", 2), "parts": []},
    )
    stale = await _create_part(
        client, auth_headers, project_id, 1, "part-create-stale", "过期"
    )
    assert invalid.status_code == stale.status_code == 409
    assert invalid.json()["detail"]["code"] == "PLANNING_STRUCTURE_INVALID"
    coverage_issue = invalid.json()["detail"]["issues"][0]
    assert coverage_issue["kind"] == "part_coverage_mismatch"
    assert coverage_issue["missing_ids"] == [part_id]
    assert coverage_issue["unknown_ids"] == []
    assert stale.json()["detail"]["code"] == "PLANNING_STRUCTURE_VERSION_CONFLICT"
    async with TestSessionLocal() as session:
        plan = await session.scalar(
            select(NovelPlan).where(NovelPlan.project_id == project_id)
        )
        assert plan.structure_version == 2
        assert await session.scalar(select(func.count(PlanningPart.id))) == 1
        assert await session.scalar(select(func.count(PlanningMutationOperation.id))) == 1
        stored = await session.get(PlanningPart, part_id)
        assert stored.title == "唯一篇"


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize(
    "legacy_kind", ["outline", "chapter", "memory", "malformed_memory"]
)
async def test_new_write_fails_closed_if_legacy_data_appears(
    client, auth_headers, legacy_kind
):
    project_id, _ = await _initialized_project(client, auth_headers)
    stored_before = None
    async with TestSessionLocal() as session:
        if legacy_kind == "outline":
            session.add(
                Outline(
                    project_id=project_id,
                    story_arc="旧大纲",
                    reveal_plan=[],
                    chapters=[],
                )
            )
        elif legacy_kind == "chapter":
            session.add(
                Chapter(
                    project_id=project_id,
                    chapter_num=1,
                    title="旧正文",
                    content="不得覆盖",
                )
            )
        elif legacy_kind == "memory":
            session.add(
                StoryMemory(
                    project_id=project_id,
                    chapter_summaries=[{"chapter_num": 1, "summary": "旧摘要"}],
                )
            )
        else:
            session.add(
                StoryMemory(project_id=project_id, revealed_elements="{broken-json")
            )
        await session.commit()
        if legacy_kind == "outline":
            row = await session.scalar(
                select(Outline).where(Outline.project_id == project_id)
            )
            stored_before = row.story_arc
        elif legacy_kind == "chapter":
            row = await session.scalar(
                select(Chapter).where(Chapter.project_id == project_id)
            )
            stored_before = row.content
        else:
            row = await session.scalar(
                select(StoryMemory).where(StoryMemory.project_id == project_id)
            )
            stored_before = deepcopy(
                row.chapter_summaries
                if legacy_kind == "memory"
                else row.revealed_elements
            )

    response = await _create_part(
        client, auth_headers, project_id, 1, "part-create-legacy", "不应写入"
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLANNING_LEGACY_IMPORT_REQUIRED"
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(PlanningPart.id))) == 0
        plan = await session.scalar(select(NovelPlan).where(NovelPlan.project_id == project_id))
        assert plan.structure_version == 1
        if legacy_kind == "outline":
            row = await session.scalar(
                select(Outline).where(Outline.project_id == project_id)
            )
            assert row.story_arc == stored_before
        elif legacy_kind == "chapter":
            row = await session.scalar(
                select(Chapter).where(Chapter.project_id == project_id)
            )
            assert row.content == stored_before
        else:
            row = await session.scalar(
                select(StoryMemory).where(StoryMemory.project_id == project_id)
            )
            stored_after = (
                row.chapter_summaries
                if legacy_kind == "memory"
                else row.revealed_elements
            )
            assert stored_after == stored_before
            decoded = read_legacy_json(stored_after)
            assert decoded.valid is (legacy_kind == "memory")


@pytest.mark.usefixtures("clean_db")
async def test_maintenance_flip_before_commit_rolls_back_everything(
    client, auth_headers, monkeypatch
):
    project_id, _ = await _initialized_project(client, auth_headers)
    import app.core.planning_write as planning_write

    checks = 0

    def flip_on_precommit():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ProjectWriteFrozenError()

    monkeypatch.setattr(planning_write, "ensure_project_writes_available", flip_on_precommit)
    response = await _create_part(
        client, auth_headers, project_id, 1, "part-create-maint", "必须回滚"
    )
    assert response.status_code == 503
    assert response.json()["code"] == "PROJECT_WRITE_FROZEN"
    assert response.json()["recommended_action"] == "retry_later"
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(PlanningPart.id))) == 0
        assert await session.scalar(select(func.count(PlanningMutationOperation.id))) == 0
        plan = await session.scalar(select(NovelPlan).where(NovelPlan.project_id == project_id))
        assert plan.structure_version == 1


@pytest.mark.usefixtures("clean_db")
async def test_owner_isolation_covers_mutation_and_receipt_lookup(
    client, auth_headers, second_auth_headers
):
    project_id, _ = await _initialized_project(client, auth_headers)
    denied_write = await _create_part(
        client,
        second_auth_headers,
        project_id,
        1,
        "part-create-forbidden",
        "越权篇章",
    )
    assert denied_write.status_code == 403
    created = await _create_part(
        client, auth_headers, project_id, 1, "part-create-private", "私有篇章"
    )
    assert created.status_code == 200
    denied_receipt = await client.get(
        f"/api/projects/{project_id}/planning/operations/by-key/part-create-private",
        headers=second_auth_headers,
    )
    assert denied_receipt.status_code == 403
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(PlanningPart.id))) == 1
        assert await session.scalar(select(func.count(PlanningMutationOperation.id))) == 1


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize("mode", ["legacy", "migrating"])
async def test_new_write_rejects_non_relational_modes(
    client, auth_headers, mode
):
    project_id, _ = await _initialized_project(client, auth_headers)
    async with TestSessionLocal() as session:
        await session.execute(
            update(Project).where(Project.id == project_id).values(lore_storage_mode=mode)
        )
        await session.commit()
    response = await _create_part(
        client, auth_headers, project_id, 1, f"part-create-{mode}", "不应写入"
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLANNING_LORE_MIGRATION_REQUIRED"
    async with TestSessionLocal() as session:
        plan = await session.scalar(select(NovelPlan).where(NovelPlan.project_id == project_id))
        assert plan.structure_version == 1
        assert await session.scalar(select(func.count(PlanningPart.id))) == 0
        assert await session.scalar(select(func.count(PlanningMutationOperation.id))) == 0


@pytest.mark.usefixtures("clean_db")
async def test_same_part_swap_uses_temporary_positions(client, auth_headers):
    project_id, _ = await _initialized_project(client, auth_headers)
    part = await _create_part(
        client, auth_headers, project_id, 1, "part-create-swap", "互换篇"
    )
    part_id = part.json()["affected_node"]["id"]
    first = await _create_chapter(
        client, auth_headers, project_id, part_id, 2, "chapter-create-swap-a", "A"
    )
    second = await _create_chapter(
        client, auth_headers, project_id, part_id, 3, "chapter-create-swap-b", "B"
    )
    first_id = first.json()["affected_node"]["id"]
    second_id = second.json()["affected_node"]["id"]
    response = await client.post(
        f"/api/projects/{project_id}/planning/structure/reorder",
        headers=auth_headers,
        json={
            **_command("reorder-same-part-swap", 4),
            "parts": [{"part_id": part_id, "chapter_ids": [second_id, first_id]}],
        },
    )
    assert response.status_code == 200, response.text
    tree = await client.get(f"/api/projects/{project_id}/planning", headers=auth_headers)
    assert [chapter["id"] for chapter in tree.json()["parts"][0]["chapters"]] == [
        second_id,
        first_id,
    ]
    assert [chapter["lock_version"] for chapter in tree.json()["parts"][0]["chapters"]] == [
        2,
        2,
    ]


@pytest.mark.usefixtures("clean_db")
async def test_reorder_failure_after_temporary_flush_restores_exact_tree(
    client, auth_headers, monkeypatch
):
    project_id, _ = await _initialized_project(client, auth_headers)
    part = await _create_part(
        client, auth_headers, project_id, 1, "part-create-rollback", "回滚篇"
    )
    part_id = part.json()["affected_node"]["id"]
    first = await _create_chapter(
        client, auth_headers, project_id, part_id, 2, "chapter-create-rollback-a", "A"
    )
    second = await _create_chapter(
        client, auth_headers, project_id, part_id, 3, "chapter-create-rollback-b", "B"
    )
    first_id = first.json()["affected_node"]["id"]
    second_id = second.json()["affected_node"]["id"]
    before = (
        await client.get(f"/api/projects/{project_id}/planning", headers=auth_headers)
    ).json()
    original_flush = AsyncSession.flush
    flush_calls = 0

    async def fail_after_temporary_flush(self, *args, **kwargs):
        nonlocal flush_calls
        flush_calls += 1
        if flush_calls == 2:
            raise ProjectWriteFrozenError()
        return await original_flush(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "flush", fail_after_temporary_flush)
    response = await client.post(
        f"/api/projects/{project_id}/planning/structure/reorder",
        headers=auth_headers,
        json={
            **_command("reorder-rollback-after-temp", 4),
            "parts": [{"part_id": part_id, "chapter_ids": [second_id, first_id]}],
        },
    )
    assert response.status_code == 503
    after = (
        await client.get(f"/api/projects/{project_id}/planning", headers=auth_headers)
    ).json()
    assert after == before
    async with TestSessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count(PlanningMutationOperation.id)).where(
                    PlanningMutationOperation.operation_key
                    == "reorder-rollback-after-temp"
                )
            )
            == 0
        )


@pytest.mark.usefixtures("clean_db")
async def test_corrupt_operation_receipt_fails_closed(client, auth_headers):
    project_id, plan = await _initialized_project(client, auth_headers)
    async with TestSessionLocal() as session:
        project = await session.get(Project, project_id)
        session.add(
            PlanningMutationOperation(
                project_id=project_id,
                requested_by=project.owner_id,
                operation_key="corrupt-receipt-0001",
                operation_type="part_create",
                request_fingerprint="0" * 64,
                result_snapshot={"incomplete": True},
            )
        )
        await session.commit()
    response = await client.get(
        f"/api/projects/{project_id}/planning/operations/by-key/corrupt-receipt-0001",
        headers=auth_headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLANNING_OPERATION_CORRUPT"
    assert plan["structure_version"] == 1


@pytest.mark.usefixtures("clean_db")
async def test_append_position_overflow_is_zero_write(client, auth_headers):
    project_id, _ = await _initialized_project(client, auth_headers)
    created = await _create_part(
        client, auth_headers, project_id, 1, "part-create-max-position", "最大位置"
    )
    assert created.status_code == 200
    async with TestSessionLocal() as session:
        await session.execute(
            update(PlanningPart)
            .where(PlanningPart.project_id == project_id)
            .values(position=2_147_483_647)
        )
        await session.commit()
    response = await _create_part(
        client, auth_headers, project_id, 2, "part-create-overflow", "不应写入"
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLANNING_POSITION_EXHAUSTED"
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(PlanningPart.id))) == 1
        assert await session.scalar(select(func.count(PlanningMutationOperation.id))) == 1


@pytest.mark.usefixtures("clean_db")
async def test_reorder_rejects_more_than_global_node_limit(client, auth_headers):
    project_id, _ = await _initialized_project(client, auth_headers)
    response = await client.post(
        f"/api/projects/{project_id}/planning/structure/reorder",
        headers=auth_headers,
        json={
            **_command("reorder-too-large", 1),
            "parts": [
                {"part_id": "a", "chapter_ids": ["a"] * 501},
                {"part_id": "b", "chapter_ids": ["b"] * 500},
            ],
        },
    )
    assert response.status_code == 422
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(PlanningMutationOperation.id))) == 0


@pytest.mark.usefixtures("clean_db")
async def test_postgresql_same_version_concurrency_has_one_winner(client, auth_headers):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("row-lock concurrency is verified on PostgreSQL")
    project_id, _ = await _initialized_project(client, auth_headers)
    first, second = await asyncio.gather(
        _create_part(
            client, auth_headers, project_id, 1, "part-create-concurrent-a", "A"
        ),
        _create_part(
            client, auth_headers, project_id, 1, "part-create-concurrent-b", "B"
        ),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    loser = first if first.status_code == 409 else second
    assert loser.json()["detail"]["code"] == "PLANNING_STRUCTURE_VERSION_CONFLICT"
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(PlanningPart.id))) == 1
        assert await session.scalar(select(func.count(PlanningMutationOperation.id))) == 1


@pytest.mark.usefixtures("clean_db")
async def test_postgresql_same_key_concurrency_replays_one_receipt(
    client, auth_headers
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("row-lock concurrency is verified on PostgreSQL")
    project_id, _ = await _initialized_project(client, auth_headers)
    first, second = await asyncio.gather(
        _create_part(
            client, auth_headers, project_id, 1, "part-create-same-key", "唯一篇"
        ),
        _create_part(
            client, auth_headers, project_id, 1, "part-create-same-key", "唯一篇"
        ),
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["receipt_id"] == second.json()["receipt_id"]
    assert sorted([first.json()["replayed"], second.json()["replayed"]]) == [
        False,
        True,
    ]
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count(PlanningPart.id))) == 1
        assert await session.scalar(select(func.count(PlanningMutationOperation.id))) == 1


@pytest.mark.usefixtures("clean_db")
async def test_postgresql_reorder_and_archive_concurrency_has_one_winner(
    client, auth_headers
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("row-lock concurrency is verified on PostgreSQL")
    project_id, _ = await _initialized_project(client, auth_headers)
    first = await _create_part(
        client, auth_headers, project_id, 1, "part-create-race-a", "篇一"
    )
    second = await _create_part(
        client, auth_headers, project_id, 2, "part-create-race-b", "篇二"
    )
    first_id = first.json()["affected_node"]["id"]
    second_id = second.json()["affected_node"]["id"]
    chapter = await _create_chapter(
        client,
        auth_headers,
        project_id,
        first_id,
        3,
        "chapter-create-race",
        "开幕",
    )
    chapter_id = chapter.json()["affected_node"]["id"]
    reorder, archive = await asyncio.gather(
        client.post(
            f"/api/projects/{project_id}/planning/structure/reorder",
            headers=auth_headers,
            json={
                **_command("reorder-race", 4),
                "parts": [
                    {"part_id": second_id, "chapter_ids": [chapter_id]},
                    {"part_id": first_id, "chapter_ids": []},
                ],
            },
        ),
        client.post(
            f"/api/projects/{project_id}/planning/chapters/{chapter_id}/archive",
            headers=auth_headers,
            json=_command("archive-race", 4),
        ),
    )
    assert sorted([reorder.status_code, archive.status_code]) == [200, 409]
    loser = reorder if reorder.status_code == 409 else archive
    assert loser.json()["detail"]["code"] == "PLANNING_STRUCTURE_VERSION_CONFLICT"


@pytest.mark.usefixtures("clean_db")
async def test_postgresql_restore_and_reorder_concurrency_has_one_winner(
    client, auth_headers
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("row-lock concurrency is verified on PostgreSQL")
    project_id, _ = await _initialized_project(client, auth_headers)
    part = await _create_part(
        client, auth_headers, project_id, 1, "part-create-restore-race", "篇一"
    )
    part_id = part.json()["affected_node"]["id"]
    chapter = await _create_chapter(
        client,
        auth_headers,
        project_id,
        part_id,
        2,
        "chapter-create-restore-race",
        "开幕",
    )
    chapter_id = chapter.json()["affected_node"]["id"]
    archived = await client.post(
        f"/api/projects/{project_id}/planning/chapters/{chapter_id}/archive",
        headers=auth_headers,
        json=_command("chapter-archive-before-restore-race", 3),
    )
    assert archived.status_code == 200
    restore, reorder = await asyncio.gather(
        client.post(
            f"/api/projects/{project_id}/planning/chapters/{chapter_id}/restore",
            headers=auth_headers,
            json=_command("chapter-restore-race", 4),
        ),
        client.post(
            f"/api/projects/{project_id}/planning/structure/reorder",
            headers=auth_headers,
            json={
                **_command("reorder-during-restore-race", 4),
                "parts": [{"part_id": part_id, "chapter_ids": []}],
            },
        ),
    )
    assert sorted([restore.status_code, reorder.status_code]) == [200, 409]
    loser = restore if restore.status_code == 409 else reorder
    assert loser.json()["detail"]["code"] == "PLANNING_STRUCTURE_VERSION_CONFLICT"
