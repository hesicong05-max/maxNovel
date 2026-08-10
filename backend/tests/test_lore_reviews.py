"""DEV-014C4 deterministic formal-lore review queue tests."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, update

from app.models.lore import (
    LoreReviewSuggestion,
    LoreReviewSuggestionCreateOperation,
    LoreReviewSuggestionEvent,
    SettingElement,
    SettingType,
)
from tests.test_lore_writes import (
    _create_project,
    _create_relational_element,
    _make_relational,
    _make_legacy,
)


async def _same_name_pair(client, headers, project_id):
    left = await _create_relational_element(
        client,
        headers,
        project_id,
        name="Ａlice",
        payload={"personality": "谨慎"},
        field_states={"personality": "provided"},
        sources=[{
            "kind": "manual",
            "excerpt": "Alice 做事谨慎。",
            "is_primary": True,
        }],
    )
    right = await _create_relational_element(
        client,
        headers,
        project_id,
        name="alice",
        payload={"personality": "冲动"},
        field_states={"personality": "provided"},
        sources=[{
            "kind": "document_import",
            "reference": "chapter-1",
            "excerpt": "alice 行事冲动。",
            "is_primary": True,
        }],
    )
    return left, right


@pytest.mark.usefixtures("clean_db")
async def test_scan_is_deterministic_and_review_decision_is_idempotent(
    client, auth_headers
):
    project_id = await _create_project(client, auth_headers)
    await _same_name_pair(client, auth_headers, project_id)

    first_scan = await client.post(
        f"/api/projects/{project_id}/lore/reviews/scan",
        headers=auth_headers,
    )
    assert first_scan.status_code == 200, first_scan.text
    assert first_scan.json()["created"] == 1
    assert first_scan.json()["pending_total"] == 1

    second_scan = await client.post(
        f"/api/projects/{project_id}/lore/reviews/scan",
        headers=auth_headers,
    )
    assert second_scan.status_code == 200
    assert second_scan.json()["created"] == 0
    assert second_scan.json()["unchanged"] == 1

    listing = await client.get(
        f"/api/projects/{project_id}/lore/reviews",
        headers=auth_headers,
    )
    assert listing.status_code == 200
    item = listing.json()["items"][0]
    assert item["kind"] == "possible_conflict"
    assert "性格" in item["primary_reason"]
    assert item["stale"] is False

    detail = await client.get(
        f"/api/projects/{project_id}/lore/reviews/{item['id']}",
        headers=auth_headers,
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["left_snapshot"]["sources"][0]["excerpt"]
    assert "chapter-1" in {
        source["reference"]
        for endpoint in (body["left_snapshot"], body["right_snapshot"])
        for source in endpoint["sources"]
    }
    assert body["evidence"][0]["comparison"] == "different"

    operation_key = "review-decision-idempotent-0001"
    decision_input = {
        "operation_key": operation_key,
        "expected_version": body["lock_version"],
        "expected_evidence_revision": body["evidence_revision"],
        "decision": "confirmed_conflict",
        "note": "作者确认需要后续统一。",
    }
    decided = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{item['id']}/decide",
        headers=auth_headers,
        json=decision_input,
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["replayed"] is False
    assert decided.json()["suggestion"]["review_status"] == "confirmed_conflict"
    assert len(decided.json()["suggestion"]["history"]) == 1

    replay = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{item['id']}/decide",
        headers=auth_headers,
        json=decision_input,
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert len(replay.json()["suggestion"]["history"]) == 1

    conflict = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{item['id']}/decide",
        headers=auth_headers,
        json={**decision_input, "decision": "not_an_issue"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == (
        "LORE_REVIEW_DECISION_IDEMPOTENCY_CONFLICT"
    )

    no_op = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{item['id']}/decide",
        headers=auth_headers,
        json={
            **decision_input,
            "operation_key": "review-decision-noop-0001",
            "expected_version": decided.json()["suggestion"]["lock_version"],
        },
    )
    assert no_op.status_code == 200
    assert no_op.json()["applied"] is False
    assert no_op.json()["suggestion"]["lock_version"] == (
        decided.json()["suggestion"]["lock_version"]
    )


@pytest.mark.usefixtures("clean_db")
async def test_changed_endpoint_marks_detail_stale_and_blocks_decision(
    client, auth_headers
):
    project_id = await _create_project(client, auth_headers)
    left, _ = await _same_name_pair(client, auth_headers, project_id)
    assert (await client.post(
        f"/api/projects/{project_id}/lore/reviews/scan", headers=auth_headers
    )).status_code == 200
    item = (await client.get(
        f"/api/projects/{project_id}/lore/reviews", headers=auth_headers
    )).json()["items"][0]

    edited = await client.patch(
        f"/api/projects/{project_id}/lore/elements/{left['id']}",
        headers=auth_headers,
        json={
            "expected_version": left["lock_version"],
            "name": left["name"],
            "summary": "已更新",
            "payload": left["payload"],
            "field_states": left["field_states"],
        },
    )
    assert edited.status_code == 200, edited.text
    detail = (await client.get(
        f"/api/projects/{project_id}/lore/reviews/{item['id']}",
        headers=auth_headers,
    )).json()
    assert detail["stale"] is True

    blocked = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{item['id']}/decide",
        headers=auth_headers,
        json={
            "operation_key": uuid.uuid4().hex,
            "expected_version": detail["lock_version"],
            "expected_evidence_revision": detail["evidence_revision"],
            "decision": "confirmed_duplicate",
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "LORE_REVIEW_EVIDENCE_STALE"

    rescanned = await client.post(
        f"/api/projects/{project_id}/lore/reviews/scan", headers=auth_headers
    )
    assert rescanned.status_code == 200
    assert rescanned.json()["updated"] == 1
    refreshed = (await client.get(
        f"/api/projects/{project_id}/lore/reviews/{item['id']}",
        headers=auth_headers,
    )).json()
    assert refreshed["stale"] is False
    assert refreshed["evidence_revision"] == detail["evidence_revision"] + 1


@pytest.mark.usefixtures("clean_db")
async def test_merge_access_tracks_current_evidence_and_endpoint_state(
    client, auth_headers
):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    left, _ = await _same_name_pair(client, auth_headers, project_id)
    assert (await client.post(
        f"/api/projects/{project_id}/lore/reviews/scan", headers=auth_headers
    )).status_code == 200
    item = (await client.get(
        f"/api/projects/{project_id}/lore/reviews", headers=auth_headers
    )).json()["items"][0]
    decided = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{item['id']}/decide",
        headers=auth_headers,
        json={
            "operation_key": "review-current-evidence-0001",
            "expected_version": item["lock_version"],
            "expected_evidence_revision": item["evidence_revision"],
            "decision": "confirmed_duplicate",
        },
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["suggestion"]["merge_allowed"] is True

    async with TestSessionLocal() as session:
        element = await session.scalar(
            select(SettingElement).where(SettingElement.id == left["id"])
        )
        type_id = element.type_id
        await session.execute(
            update(SettingElement)
            .where(SettingElement.id == left["id"])
            .values(lifecycle_status="archived")
        )
        await session.commit()
    archived = (await client.get(
        f"/api/projects/{project_id}/lore/reviews/{item['id']}",
        headers=auth_headers,
    )).json()
    assert archived["stale"] is False
    assert archived["merge_allowed"] is False
    assert "未归档" in archived["merge_block_reason"]

    async with TestSessionLocal() as session:
        await session.execute(
            update(SettingElement)
            .where(SettingElement.id == left["id"])
            .values(lifecycle_status="active", confirmation_status="candidate")
        )
        await session.commit()
    unconfirmed = (await client.get(
        f"/api/projects/{project_id}/lore/reviews/{item['id']}",
        headers=auth_headers,
    )).json()
    assert unconfirmed["merge_allowed"] is False
    assert "已确认" in unconfirmed["merge_block_reason"]

    async with TestSessionLocal() as session:
        await session.execute(
            update(SettingElement)
            .where(SettingElement.id == left["id"])
            .values(confirmation_status="confirmed")
        )
        await session.execute(
            update(SettingType)
            .where(SettingType.id == type_id)
            .values(status="archived")
        )
        await session.commit()
    inactive_type = (await client.get(
        f"/api/projects/{project_id}/lore/reviews/{item['id']}",
        headers=auth_headers,
    )).json()
    assert inactive_type["merge_allowed"] is False
    assert "类型已停用" in inactive_type["merge_block_reason"]

    async with TestSessionLocal() as session:
        await session.execute(
            update(SettingType)
            .where(SettingType.id == type_id)
            .values(status="active")
        )
        await session.execute(
            update(LoreReviewSuggestion)
            .where(LoreReviewSuggestion.id == item["id"])
            .values(evidence_revision=item["evidence_revision"] + 1)
        )
        await session.commit()
    changed_evidence = (await client.get(
        f"/api/projects/{project_id}/lore/reviews/{item['id']}",
        headers=auth_headers,
    )).json()
    assert changed_evidence["stale"] is False
    assert changed_evidence["merge_allowed"] is False
    assert "当前依据" in changed_evidence["merge_block_reason"]


@pytest.mark.usefixtures("clean_db")
async def test_review_queue_isolated_and_legacy_scan_fails_closed(
    client, auth_headers, second_auth_headers
):
    project_id = await _create_project(client, auth_headers)
    await _same_name_pair(client, auth_headers, project_id)
    await client.post(
        f"/api/projects/{project_id}/lore/reviews/scan", headers=auth_headers
    )
    denied = await client.get(
        f"/api/projects/{project_id}/lore/reviews", headers=second_auth_headers
    )
    assert denied.status_code == 403

    legacy_id = await _create_project(client, auth_headers, title="兼容项目")
    await _make_legacy(legacy_id)
    legacy = await client.post(
        f"/api/projects/{legacy_id}/lore/reviews/scan", headers=auth_headers
    )
    assert legacy.status_code == 409
    assert legacy.json()["detail"]["code"] == "LORE_MODE_NOT_RELATIONAL"


@pytest.mark.usefixtures("clean_db")
async def test_review_maintenance_blocks_new_writes_but_allows_replay(
    client, auth_headers
):
    from app.config import settings as app_settings
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _same_name_pair(client, auth_headers, project_id)
    await client.post(
        f"/api/projects/{project_id}/lore/reviews/scan", headers=auth_headers
    )
    item = (await client.get(
        f"/api/projects/{project_id}/lore/reviews", headers=auth_headers
    )).json()["items"][0]
    operation_key = "review-before-maintenance-0001"
    payload = {
        "operation_key": operation_key,
        "expected_version": item["lock_version"],
        "expected_evidence_revision": item["evidence_revision"],
        "decision": "deferred",
    }
    assert (await client.post(
        f"/api/projects/{project_id}/lore/reviews/{item['id']}/decide",
        headers=auth_headers,
        json=payload,
    )).status_code == 200
    async with TestSessionLocal() as session:
        before_suggestions = await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestion)
        )
        before_events = await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestionEvent)
        )

    original = app_settings.LEGACY_JSON_WRITES_FROZEN
    app_settings.LEGACY_JSON_WRITES_FROZEN = True
    try:
        blocked_scan = await client.post(
            f"/api/projects/{project_id}/lore/reviews/scan", headers=auth_headers
        )
        assert blocked_scan.status_code == 503
        blocked_decision = await client.post(
            f"/api/projects/{project_id}/lore/reviews/{item['id']}/decide",
            headers=auth_headers,
            json={**payload, "operation_key": "review-maintenance-new-0001"},
        )
        assert blocked_decision.status_code == 503
        replay = await client.post(
            f"/api/projects/{project_id}/lore/reviews/{item['id']}/decide",
            headers=auth_headers,
            json=payload,
        )
        assert replay.status_code == 200
        assert replay.json()["replayed"] is True
    finally:
        app_settings.LEGACY_JSON_WRITES_FROZEN = original

    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestion)
        ) == before_suggestions
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestionEvent)
        ) == before_events


@pytest.mark.usefixtures("clean_db")
async def test_maintenance_freeze_before_scan_commit_rolls_back(
    client, auth_headers, monkeypatch
):
    from app.core import lore_review
    from app.core.maintenance import ProjectWriteFrozenError
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _same_name_pair(client, auth_headers, project_id)
    checks = 0

    def freeze_before_commit():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ProjectWriteFrozenError("PROJECT_WRITE_FROZEN")

    monkeypatch.setattr(lore_review, "check_writes_available", freeze_before_commit)
    response = await client.post(
        f"/api/projects/{project_id}/lore/reviews/scan",
        headers=auth_headers,
    )
    assert response.status_code == 503
    assert response.json()["code"] == "PROJECT_WRITE_FROZEN"
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestion)
        ) == 0


@pytest.mark.usefixtures("clean_db")
async def test_maintenance_freeze_before_decision_commit_rolls_back(
    client, auth_headers, monkeypatch
):
    from app.api import lore as lore_api
    from app.core.maintenance import ProjectWriteFrozenError
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _same_name_pair(client, auth_headers, project_id)
    await client.post(
        f"/api/projects/{project_id}/lore/reviews/scan", headers=auth_headers
    )
    item = (await client.get(
        f"/api/projects/{project_id}/lore/reviews", headers=auth_headers
    )).json()["items"][0]
    checks = 0

    def freeze_before_commit():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ProjectWriteFrozenError("PROJECT_WRITE_FROZEN")

    monkeypatch.setattr(lore_api, "check_writes_available", freeze_before_commit)
    response = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{item['id']}/decide",
        headers=auth_headers,
        json={
            "operation_key": "review-freeze-before-commit-0001",
            "expected_version": item["lock_version"],
            "expected_evidence_revision": item["evidence_revision"],
            "decision": "confirmed_duplicate",
        },
    )
    assert response.status_code == 503
    assert response.json()["code"] == "PROJECT_WRITE_FROZEN"
    async with TestSessionLocal() as session:
        stored = await session.scalar(
            select(LoreReviewSuggestion).where(
                LoreReviewSuggestion.project_id == project_id
            )
        )
        assert stored.review_status == "pending"
        assert stored.lock_version == item["lock_version"]
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestionEvent)
        ) == 0


@pytest.mark.usefixtures("clean_db")
async def test_postgres_concurrent_scan_and_same_decision_converge(
    client, auth_headers
):
    from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal

    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("PostgreSQL row-lock behavior is covered in the CI service")
    project_id = await _create_project(client, auth_headers)
    await _same_name_pair(client, auth_headers, project_id)
    scans = await asyncio.gather(*[
        client.post(
            f"/api/projects/{project_id}/lore/reviews/scan",
            headers=auth_headers,
        )
        for _ in range(2)
    ])
    assert [response.status_code for response in scans] == [200, 200]
    assert sorted(response.json()["created"] for response in scans) == [0, 1]
    item = (await client.get(
        f"/api/projects/{project_id}/lore/reviews", headers=auth_headers
    )).json()["items"][0]
    payload = {
        "operation_key": "postgres-review-decision-0001",
        "expected_version": item["lock_version"],
        "expected_evidence_revision": item["evidence_revision"],
        "decision": "confirmed_duplicate",
        "note": "并发相同请求",
    }
    decisions = await asyncio.gather(*[
        client.post(
            f"/api/projects/{project_id}/lore/reviews/{item['id']}/decide",
            headers=auth_headers,
            json=payload,
        )
        for _ in range(2)
    ])
    assert [response.status_code for response in decisions] == [200, 200]
    assert sorted(response.json()["replayed"] for response in decisions) == [False, True]
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestion)
        ) == 1
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestionEvent)
        ) == 1


@pytest.mark.usefixtures("clean_db")
async def test_project_delete_cascades_reviews_and_preserves_other_project(
    client, auth_headers
):
    from tests.conftest import TestSessionLocal

    first_id = await _create_project(client, auth_headers, title="待删除审查项目")
    await _same_name_pair(client, auth_headers, first_id)
    await client.post(
        f"/api/projects/{first_id}/lore/reviews/scan", headers=auth_headers
    )
    first_item = (await client.get(
        f"/api/projects/{first_id}/lore/reviews", headers=auth_headers
    )).json()["items"][0]
    await client.post(
        f"/api/projects/{first_id}/lore/reviews/{first_item['id']}/decide",
        headers=auth_headers,
        json={
            "operation_key": "review-delete-cascade-0001",
            "expected_version": first_item["lock_version"],
            "expected_evidence_revision": first_item["evidence_revision"],
            "decision": "deferred",
        },
    )

    second_id = await _create_project(client, auth_headers, title="保留审查项目")
    await _same_name_pair(client, auth_headers, second_id)
    await client.post(
        f"/api/projects/{second_id}/lore/reviews/scan", headers=auth_headers
    )
    deleted = await client.delete(f"/api/projects/{first_id}", headers=auth_headers)
    assert deleted.status_code == 200, deleted.text
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestion).where(
                LoreReviewSuggestion.project_id == first_id
            )
        ) == 0
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestionEvent).where(
                LoreReviewSuggestionEvent.project_id == first_id
            )
        ) == 0
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestion).where(
                LoreReviewSuggestion.project_id == second_id
            )
        ) == 1


@pytest.mark.usefixtures("clean_db")
async def test_author_can_create_replay_and_review_manual_clue_without_auto_merge(
    client, auth_headers
):
    from tests.test_lore_merge_preview import _preview_body

    project_id = await _create_project(client, auth_headers)
    left = await _create_relational_element(
        client, auth_headers, project_id,
        name="林岚",
        payload={"personality": "谨慎"},
        field_states={"personality": "provided"},
    )
    right = await _create_relational_element(
        client, auth_headers, project_id,
        name="林岚·化名",
        payload={"personality": "谨慎"},
        field_states={"personality": "provided"},
    )
    payload = {
        "operation_key": "manual-review-create-0001",
        "kind": "possible_conflict",
        "left_element_id": left["id"],
        "right_element_id": right["id"],
        "left_expected_lock_version": left["lock_version"],
        "right_expected_lock_version": right["lock_version"],
        "note": "作者怀疑这是同一人的化名，请复核来源。",
    }
    self_pair = await client.post(
        f"/api/projects/{project_id}/lore/reviews/manual",
        headers=auth_headers,
        json={
            **payload,
            "operation_key": "manual-review-self-pair-0001",
            "right_element_id": left["id"],
            "right_expected_lock_version": left["lock_version"],
        },
    )
    assert self_pair.status_code == 422
    stale = await client.post(
        f"/api/projects/{project_id}/lore/reviews/manual",
        headers=auth_headers,
        json={
            **payload,
            "operation_key": "manual-review-stale-pair-0001",
            "left_expected_lock_version": left["lock_version"] + 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "LORE_MANUAL_REVIEW_ENDPOINT_STALE"
    created = await client.post(
        f"/api/projects/{project_id}/lore/reviews/manual",
        headers=auth_headers,
        json=payload,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["created"] is True
    assert body["replayed"] is False
    assert body["suggestion"]["origin"] == "author_report"
    assert body["suggestion"]["merge_allowed"] is False
    assert body["suggestion"]["evidence"][0]["statement"] == payload["note"]

    replay = await client.post(
        f"/api/projects/{project_id}/lore/reviews/manual",
        headers=auth_headers,
        json=payload,
    )
    assert replay.status_code == 201
    assert replay.json()["replayed"] is True
    conflict = await client.post(
        f"/api/projects/{project_id}/lore/reviews/manual",
        headers=auth_headers,
        json={**payload, "note": "不同的说明"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "LORE_MANUAL_REVIEW_IDEMPOTENCY_CONFLICT"
    pair_conflict = await client.post(
        f"/api/projects/{project_id}/lore/reviews/manual",
        headers=auth_headers,
        json={
            **payload,
            "operation_key": "manual-review-pair-conflict-0001",
            "note": "这两项存在另一种不同说明。",
        },
    )
    assert pair_conflict.status_code == 409
    assert pair_conflict.json()["detail"]["code"] == "LORE_MANUAL_REVIEW_PAIR_CONFLICT"

    scan = await client.post(
        f"/api/projects/{project_id}/lore/reviews/scan", headers=auth_headers
    )
    assert scan.status_code == 200
    latest = await client.get(
        f"/api/projects/{project_id}/lore/reviews/{body['suggestion']['id']}",
        headers=auth_headers,
    )
    assert latest.json()["origin"] == "author_report"
    assert latest.json()["stale"] is False

    decided = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{body['suggestion']['id']}/decide",
        headers=auth_headers,
        json={
            "operation_key": "manual-review-decision-0001",
            "expected_version": latest.json()["lock_version"],
            "expected_evidence_revision": latest.json()["evidence_revision"],
            "decision": "confirmed_duplicate",
            "note": "已核对，但人工线索不可直接合并",
        },
    )
    assert decided.status_code == 200, decided.text
    suggestion = decided.json()["suggestion"]
    assert suggestion["merge_allowed"] is False
    assert "用户创建" in suggestion["merge_block_reason"]
    preview_body = await _preview_body(
        client, auth_headers, project_id, left, right, suggestion
    )
    preview = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-preview",
        headers=auth_headers,
        json=preview_body,
    )
    assert preview.status_code == 409
    assert preview.json()["detail"]["code"] == "LORE_MERGE_REVIEW_NOT_CONFIRMED"


@pytest.mark.usefixtures("clean_db")
async def test_manual_clue_cross_type_and_maintenance_are_fail_closed(
    client, auth_headers
):
    from app.config import settings as app_settings
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    left = await _create_relational_element(
        client, auth_headers, project_id, name="林岚", type_key="character"
    )
    right = await _create_relational_element(
        client, auth_headers, project_id, name="北境", type_key="location"
    )
    payload = {
        "operation_key": "manual-review-cross-type-0001",
        "kind": "possible_conflict",
        "left_element_id": left["id"],
        "right_element_id": right["id"],
        "left_expected_lock_version": left["lock_version"],
        "right_expected_lock_version": right["lock_version"],
        "note": "角色的出生地与地点设定可能冲突。",
    }
    other_project_id = await _create_project(
        client, auth_headers, title="另一个项目"
    )
    foreign = await _create_relational_element(
        client, auth_headers, other_project_id, name="项目外地点", type_key="location"
    )
    cross_project = await client.post(
        f"/api/projects/{project_id}/lore/reviews/manual",
        headers=auth_headers,
        json={
            **payload,
            "operation_key": "manual-review-cross-project-0001",
            "right_element_id": foreign["id"],
            "right_expected_lock_version": foreign["lock_version"],
        },
    )
    assert cross_project.status_code == 404

    merged_endpoint = await _create_relational_element(
        client, auth_headers, project_id, name="已合并地点", type_key="location"
    )
    async with TestSessionLocal() as session:
        await session.execute(
            update(SettingElement)
            .where(SettingElement.id == merged_endpoint["id"])
            .values(
                lifecycle_status="merged",
                merged_into_element_id=left["id"],
                enabled=False,
            )
        )
        await session.commit()
    merged = await client.post(
        f"/api/projects/{project_id}/lore/reviews/manual",
        headers=auth_headers,
        json={
            **payload,
            "operation_key": "manual-review-merged-endpoint-0001",
            "right_element_id": merged_endpoint["id"],
            "right_expected_lock_version": merged_endpoint["lock_version"],
        },
    )
    assert merged.status_code == 409
    assert merged.json()["detail"]["code"] == "LORE_MANUAL_REVIEW_ENDPOINT_MERGED"
    original = app_settings.LEGACY_JSON_WRITES_FROZEN
    app_settings.LEGACY_JSON_WRITES_FROZEN = True
    try:
        blocked = await client.post(
            f"/api/projects/{project_id}/lore/reviews/manual",
            headers=auth_headers,
            json=payload,
        )
        assert blocked.status_code == 503
    finally:
        app_settings.LEGACY_JSON_WRITES_FROZEN = original
    created = await client.post(
        f"/api/projects/{project_id}/lore/reviews/manual",
        headers=auth_headers,
        json=payload,
    )
    assert created.status_code == 201, created.text
    suggestion = created.json()["suggestion"]
    assert suggestion["merge_allowed"] is False
    assert "类型不同" in suggestion["merge_block_reason"]
    app_settings.LEGACY_JSON_WRITES_FROZEN = True
    try:
        replay = await client.post(
            f"/api/projects/{project_id}/lore/reviews/manual",
            headers=auth_headers,
            json=payload,
        )
        assert replay.status_code == 201
        assert replay.json()["replayed"] is True
    finally:
        app_settings.LEGACY_JSON_WRITES_FROZEN = original
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestionCreateOperation)
        ) == 1


@pytest.mark.usefixtures("clean_db")
async def test_manual_clue_receipts_follow_project_cascade_and_isolation(
    client, auth_headers
):
    from tests.conftest import TestSessionLocal

    project_ids = []
    for index in range(2):
        project_id = await _create_project(
            client, auth_headers, title=f"人工线索项目 {index + 1}"
        )
        project_ids.append(project_id)
        left = await _create_relational_element(
            client, auth_headers, project_id, name=f"角色 {index + 1}A"
        )
        right = await _create_relational_element(
            client, auth_headers, project_id, name=f"角色 {index + 1}B"
        )
        response = await client.post(
            f"/api/projects/{project_id}/lore/reviews/manual",
            headers=auth_headers,
            json={
                "operation_key": f"manual-review-cascade-{index + 1:04d}",
                "kind": "possible_duplicate",
                "left_element_id": left["id"],
                "right_element_id": right["id"],
                "left_expected_lock_version": left["lock_version"],
                "right_expected_lock_version": right["lock_version"],
                "note": "验证项目级联删与隔离。",
            },
        )
        assert response.status_code == 201, response.text

    deleted = await client.delete(
        f"/api/projects/{project_ids[0]}", headers=auth_headers
    )
    assert deleted.status_code == 200, deleted.text
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestionCreateOperation).where(
                LoreReviewSuggestionCreateOperation.project_id == project_ids[0]
            )
        ) == 0
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestionCreateOperation).where(
                LoreReviewSuggestionCreateOperation.project_id == project_ids[1]
            )
        ) == 1
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestion).where(
                LoreReviewSuggestion.project_id == project_ids[1]
            )
        ) == 1


@pytest.mark.usefixtures("clean_db")
async def test_manual_clue_freeze_before_commit_rolls_back(
    client, auth_headers, monkeypatch
):
    from app.api import lore as lore_api
    from app.core.maintenance import ProjectWriteFrozenError
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    left = await _create_relational_element(
        client, auth_headers, project_id, name="林岚"
    )
    right = await _create_relational_element(
        client, auth_headers, project_id, name="林岚的化名"
    )
    checks = 0

    def freeze_before_commit():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ProjectWriteFrozenError("PROJECT_WRITE_FROZEN")

    monkeypatch.setattr(lore_api, "check_writes_available", freeze_before_commit)
    response = await client.post(
        f"/api/projects/{project_id}/lore/reviews/manual",
        headers=auth_headers,
        json={
            "operation_key": "manual-review-freeze-0001",
            "kind": "possible_duplicate",
            "left_element_id": left["id"],
            "right_element_id": right["id"],
            "left_expected_lock_version": left["lock_version"],
            "right_expected_lock_version": right["lock_version"],
            "note": "请复核化名是否指向同一角色。",
        },
    )
    assert response.status_code == 503
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestion).where(
                LoreReviewSuggestion.project_id == project_id
            )
        ) == 0
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestionCreateOperation).where(
                LoreReviewSuggestionCreateOperation.project_id == project_id
            )
        ) == 0


@pytest.mark.usefixtures("clean_db")
async def test_postgres_concurrent_manual_clue_creation_converges(
    client, auth_headers
):
    from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal

    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("PostgreSQL row-lock behavior is covered in the CI service")
    project_id = await _create_project(client, auth_headers)
    left = await _create_relational_element(
        client, auth_headers, project_id, name="林岚"
    )
    right = await _create_relational_element(
        client, auth_headers, project_id, name="林岚化名"
    )
    payload = {
        "operation_key": "manual-review-postgres-concurrent-0001",
        "kind": "possible_duplicate",
        "left_element_id": left["id"],
        "right_element_id": right["id"],
        "left_expected_lock_version": left["lock_version"],
        "right_expected_lock_version": right["lock_version"],
        "note": "并发创建应只保留一条线索。",
    }
    responses = await asyncio.gather(*[
        client.post(
            f"/api/projects/{project_id}/lore/reviews/manual",
            headers=auth_headers,
            json=payload,
        )
        for _ in range(2)
    ])
    assert [response.status_code for response in responses] == [201, 201]
    assert sorted(response.json()["replayed"] for response in responses) == [False, True]
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestion).where(
                LoreReviewSuggestion.project_id == project_id,
                LoreReviewSuggestion.rule_key == "manual_pair_review",
            )
        ) == 1
        assert await session.scalar(
            select(func.count()).select_from(LoreReviewSuggestionCreateOperation).where(
                LoreReviewSuggestionCreateOperation.project_id == project_id
            )
        ) == 1
