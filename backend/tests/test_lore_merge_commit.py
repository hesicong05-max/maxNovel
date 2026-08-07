"""DEV-014C5B transactional formal-lore merge commit tests."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.models.lore import (
    ElementRelation,
    ElementSource,
    ElementVersion,
    LoreMergeOperation,
    LoreMergeRelationAction,
    SettingElement,
    SettingType,
)
from tests.conftest import TestSessionLocal
from tests.test_lore_merge_preview import _confirmed_duplicate, _preview_body
from tests.test_lore_writes import (
    _create_project,
    _create_relation,
    _create_relational_element,
)


async def _preview(client, headers, project_id, left, right, suggestion):
    body = await _preview_body(
        client, headers, project_id, left, right, suggestion
    )
    response = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-preview",
        headers=headers,
        json=body,
    )
    assert response.status_code == 200, response.text
    return body, response.json()


async def _commit(
    client,
    headers,
    project_id,
    suggestion_id,
    body,
    preview,
    *,
    operation_key=None,
):
    key = operation_key or uuid.uuid4().hex
    response = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{suggestion_id}/merge-commit",
        headers=headers,
        json={
            "operation_key": key,
            "preview_token": preview["preview_token"],
            "preview": body,
        },
    )
    return key, response


@pytest.mark.usefixtures("clean_db")
async def test_commit_is_non_destructive_audited_and_replayable(
    client, auth_headers
):
    project_id = await _create_project(client, auth_headers)
    left, right, suggestion = await _confirmed_duplicate(
        client, auth_headers, project_id
    )
    target = await _create_relational_element(
        client, auth_headers, project_id, name="星盟"
    )
    await _create_relation(
        client,
        auth_headers,
        project_id,
        right["id"],
        left["id"],
        relation_type="enemy",
        description="会形成自指",
    )
    await _create_relation(
        client,
        auth_headers,
        project_id,
        right["id"],
        target["id"],
        relation_type="member_of",
        description="需要改连",
    )
    body, preview = await _preview(
        client, auth_headers, project_id, left, right, suggestion
    )
    before_source_ids = {}
    async with TestSessionLocal() as session:
        rows = await session.execute(
            select(ElementSource.id, ElementSource.element_id)
            .where(ElementSource.project_id == project_id)
            .order_by(ElementSource.id)
        )
        before_source_ids = dict(rows.all())

    operation_key, response = await _commit(
        client,
        auth_headers,
        project_id,
        suggestion["id"],
        body,
        preview,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["replayed"] is False
    assert result["survivor_element_id"] == left["id"]
    assert result["merged_element_id"] == right["id"]
    assert result["impact_summary"]["physical_deletions"] == 0
    assert result["impact_summary"]["element_names"] == {
        "survivor": left["name"],
        "merged": right["name"],
    }
    assert {action["action"] for action in result["relation_actions"]} == {
        "rewired",
        "self_loop_archived",
    }

    replay = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-commit",
        headers=auth_headers,
        json={
            "operation_key": operation_key,
            "preview_token": preview["preview_token"],
            "preview": body,
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["id"] == result["id"]

    by_key = await client.get(
        f"/api/projects/{project_id}/lore/merge-operations/by-key/{operation_key}",
        headers=auth_headers,
    )
    assert by_key.status_code == 200, by_key.text
    assert by_key.json()["id"] == result["id"]

    for element_id in (left["id"], right["id"]):
        history = await client.get(
            f"/api/projects/{project_id}/lore/elements/{element_id}/merge-history",
            headers=auth_headers,
        )
        assert history.status_code == 200, history.text
        assert [item["id"] for item in history.json()["items"]] == [result["id"]]

    async with TestSessionLocal() as session:
        survivor = await session.get(SettingElement, left["id"])
        merged = await session.get(SettingElement, right["id"])
        assert survivor.content_version == left["content_version"] + 1
        assert survivor.lock_version == left["lock_version"] + 1
        assert merged.lifecycle_status == "merged"
        assert merged.enabled is False
        assert merged.merged_into_element_id == survivor.id
        assert merged.content_version == right["content_version"]
        source_rows = await session.execute(
            select(ElementSource.id, ElementSource.element_id)
            .where(ElementSource.project_id == project_id)
            .order_by(ElementSource.id)
        )
        assert dict(source_rows.all()) == before_source_ids
        active_to_loser = await session.scalar(
            select(func.count()).select_from(ElementRelation).where(
                ElementRelation.project_id == project_id,
                ElementRelation.status == "active",
                or_(
                    ElementRelation.source_element_id == merged.id,
                    ElementRelation.target_element_id == merged.id,
                ),
            )
        )
        assert active_to_loser == 0
        assert await session.scalar(
            select(func.count()).select_from(ElementVersion).where(
                ElementVersion.element_id == survivor.id
            )
        ) == 2
        assert await session.scalar(
            select(func.count()).select_from(LoreMergeOperation)
        ) == 1
        assert await session.scalar(
            select(func.count()).select_from(LoreMergeRelationAction)
        ) == 2

    blocked_edit = await client.patch(
        f"/api/projects/{project_id}/lore/elements/{right['id']}",
        headers=auth_headers,
        json={
            "expected_version": right["lock_version"] + 1,
            "name": right["name"],
            "summary": right["summary"],
            "payload": right["payload"],
            "field_states": right["field_states"],
        },
    )
    assert blocked_edit.status_code == 409
    assert blocked_edit.json()["detail"]["code"] == "LORE_ELEMENT_ALREADY_MERGED"


@pytest.mark.usefixtures("clean_db")
async def test_commit_rejects_stale_schema_chain_and_reused_key(
    client, auth_headers
):
    project_id = await _create_project(client, auth_headers)
    left, right, suggestion = await _confirmed_duplicate(
        client, auth_headers, project_id
    )
    body, preview = await _preview(
        client, auth_headers, project_id, left, right, suggestion
    )

    async with TestSessionLocal() as session:
        await session.execute(
            update(SettingType)
            .where(SettingType.project_id == project_id)
            .values(schema_revision=SettingType.schema_revision + 1)
        )
        await session.commit()
    _, stale = await _commit(
        client,
        auth_headers,
        project_id,
        suggestion["id"],
        body,
        preview,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "LORE_MERGE_PREVIEW_STALE"
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(LoreMergeOperation)
        ) == 0

    # Restore the type anchor, create an inbound historical alias, and re-preview.
    async with TestSessionLocal() as session:
        await session.execute(
            update(SettingType)
            .where(SettingType.project_id == project_id)
            .values(schema_revision=SettingType.schema_revision - 1)
        )
        alias = SettingElement(
            project_id=project_id,
            type_id=(
                await session.scalar(
                    select(SettingElement.type_id).where(
                        SettingElement.id == left["id"]
                    )
                )
            ),
            name="历史别名",
            normalized_name="历史别名",
            summary="",
            payload={},
            payload_schema_revision=1,
            field_states={},
            confirmation_status="confirmed",
            lifecycle_status="merged",
            merged_into_element_id=right["id"],
            enabled=False,
            content_version=1,
            lock_version=1,
        )
        session.add(alias)
        await session.commit()
    body, preview = await _preview(
        client, auth_headers, project_id, left, right, suggestion
    )
    operation_key, chained = await _commit(
        client,
        auth_headers,
        project_id,
        suggestion["id"],
        body,
        preview,
    )
    assert chained.status_code == 409
    assert chained.json()["detail"]["code"] == "LORE_MERGE_CHAIN_UNSUPPORTED"

    # The same key with different semantics is rejected by the durable receipt.
    async with TestSessionLocal() as session:
        await session.execute(
            update(SettingElement)
            .where(SettingElement.merged_into_element_id == right["id"])
            .values(merged_into_element_id=left["id"])
        )
        await session.commit()
    body, preview = await _preview(
        client, auth_headers, project_id, left, right, suggestion
    )
    operation_key, committed = await _commit(
        client,
        auth_headers,
        project_id,
        suggestion["id"],
        body,
        preview,
        operation_key=operation_key,
    )
    assert committed.status_code == 200, committed.text
    changed = dict(body)
    changed["final_name"] = "不同请求"
    conflict = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-commit",
        headers=auth_headers,
        json={
            "operation_key": operation_key,
            "preview_token": preview["preview_token"],
            "preview": changed,
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "LORE_MERGE_OPERATION_KEY_REUSED"


@pytest.mark.usefixtures("clean_db")
async def test_merge_state_constraints_fail_closed(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    left = await _create_relational_element(
        client, auth_headers, project_id, name="A"
    )
    right = await _create_relational_element(
        client, auth_headers, project_id, name="B"
    )
    async with TestSessionLocal() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                update(SettingElement)
                .where(SettingElement.id == left["id"])
                .values(merged_into_element_id=left["id"])
            )
        await session.rollback()

        with pytest.raises(IntegrityError):
            await session.execute(
                update(SettingElement)
                .where(SettingElement.id == left["id"])
                .values(
                    lifecycle_status="merged",
                    enabled=True,
                    merged_into_element_id=right["id"],
                )
            )
        await session.rollback()


@pytest.mark.usefixtures("clean_db")
async def test_commit_rolls_back_on_maintenance_recheck_and_replay_bypasses_freeze(
    client, auth_headers, monkeypatch
):
    from app.config import settings as app_settings
    from app.core import lore_merge_commit
    from app.core.maintenance import ProjectWriteFrozenError

    project_id = await _create_project(client, auth_headers)
    left, right, suggestion = await _confirmed_duplicate(
        client, auth_headers, project_id
    )
    body, preview = await _preview(
        client, auth_headers, project_id, left, right, suggestion
    )
    checks = 0

    def freeze_after_flush():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ProjectWriteFrozenError("PROJECT_WRITE_FROZEN")

    # The preview module retains its own checks; this hook covers commit start/final.
    monkeypatch.setattr(
        lore_merge_commit, "check_writes_available", freeze_after_flush
    )
    operation_key, frozen = await _commit(
        client,
        auth_headers,
        project_id,
        suggestion["id"],
        body,
        preview,
    )
    assert frozen.status_code == 503, frozen.text
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(LoreMergeOperation)
        ) == 0
        survivor = await session.get(SettingElement, left["id"])
        merged = await session.get(SettingElement, right["id"])
        assert survivor.content_version == left["content_version"]
        assert survivor.lock_version == left["lock_version"]
        assert merged.lifecycle_status == "active"
        assert merged.merged_into_element_id is None

    monkeypatch.undo()
    body, preview = await _preview(
        client, auth_headers, project_id, left, right, suggestion
    )
    operation_key, committed = await _commit(
        client,
        auth_headers,
        project_id,
        suggestion["id"],
        body,
        preview,
        operation_key=operation_key,
    )
    assert committed.status_code == 200, committed.text

    original = app_settings.LEGACY_JSON_WRITES_FROZEN
    app_settings.LEGACY_JSON_WRITES_FROZEN = True
    try:
        replay = await client.post(
            f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-commit",
            headers=auth_headers,
            json={
                "operation_key": operation_key,
                "preview_token": preview["preview_token"],
                "preview": body,
            },
        )
        by_key = await client.get(
            f"/api/projects/{project_id}/lore/merge-operations/by-key/{operation_key}",
            headers=auth_headers,
        )
    finally:
        app_settings.LEGACY_JSON_WRITES_FROZEN = original
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert by_key.status_code == 200, by_key.text


@pytest.mark.usefixtures("clean_db")
async def test_postgres_concurrent_merge_requests_converge(client, auth_headers):
    from tests.conftest import TEST_DATABASE_BACKEND

    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("PostgreSQL row-lock convergence is covered in CI")
    project_id = await _create_project(client, auth_headers)
    left, right, suggestion = await _confirmed_duplicate(
        client, auth_headers, project_id
    )
    body, preview = await _preview(
        client, auth_headers, project_id, left, right, suggestion
    )
    operation_key = "postgres-merge-concurrent-0001"
    payload = {
        "operation_key": operation_key,
        "preview_token": preview["preview_token"],
        "preview": body,
    }
    first, second = await asyncio.gather(
        client.post(
            f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-commit",
            headers=auth_headers,
            json=payload,
        ),
        client.post(
            f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-commit",
            headers=auth_headers,
            json=payload,
        ),
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert sorted([first.json()["replayed"], second.json()["replayed"]]) == [
        False,
        True,
    ]
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(LoreMergeOperation)
        ) == 1
