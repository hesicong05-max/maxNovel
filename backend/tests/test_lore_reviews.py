"""DEV-014C4 deterministic formal-lore review queue tests."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.models.lore import LoreReviewSuggestion, LoreReviewSuggestionEvent
from tests.test_lore_writes import (
    _create_project,
    _create_relational_element,
    _make_relational,
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
