"""DEV-013A tests for reviewable, source-grounded lore extraction."""

import asyncio
import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.core.llm_client import LLMSingleCallError, llm_client
from app.models.extraction import (
    LoreCandidateFieldEvidence,
    LoreCandidateRevision,
    LoreExtractionBatch,
    LoreExtractionCandidate,
)
from app.models.lore import (
    ElementSource,
    ElementStateEvent,
    ElementVersion,
    SettingElement,
)
from app.models.project import Project


SOURCE = "林远性格坚韧，目标是守护故乡。苏瑶性格冷静。天玄宗是正道宗门。"


async def _create_project(client, headers, title="候选提取测试"):
    response = await client.post(
        "/api/projects",
        headers=headers,
        json={
            "title": title,
            "genre": "玄幻",
            "total_chapters": 10,
            "chapter_word_count": 1000,
            "style_intensity": "standard",
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


def _response_json(candidates):
    return json.dumps({"candidates": candidates}, ensure_ascii=False)


def _valid_candidates():
    return [
        {
            "type_key": "character",
            "name": "林远",
            "fields": [
                {
                    "field_key": "personality",
                    "value": "坚韧",
                    "state": "provided",
                    "excerpt": "林远性格坚韧",
                },
                {
                    "field_key": "goals",
                    "value": "守护故乡",
                    "state": "provided",
                    "excerpt": "目标是守护故乡",
                },
            ],
            "relation_suggestions": [],
        },
        {
            "type_key": "character",
            "name": "苏瑶",
            "fields": [
                {
                    "field_key": "personality",
                    "value": "冷静",
                    "state": "provided",
                    "excerpt": "苏瑶性格冷静",
                }
            ],
            "relation_suggestions": [],
        },
        {
            "type_key": "faction",
            "name": "天玄宗",
            "fields": [
                {
                    "field_key": "stance",
                    "value": "正道宗门",
                    "state": "provided",
                    "excerpt": "天玄宗是正道宗门",
                }
            ],
            "relation_suggestions": [],
        },
    ]


async def _create_batch(client, headers, project_id, key="extract-case-001"):
    return await client.post(
        f"/api/projects/{project_id}/lore/extractions",
        headers=headers,
        json={
            "idempotency_key": key,
            "document_text": SOURCE,
            "source_kind": "manual_text",
            "source_ref": "upload:worldview-1",
        },
    )


async def _make_relational(project_id):
    from sqlalchemy import update
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as session:
        await session.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(lore_storage_mode="relational")
        )
        await session.commit()


async def _first_candidate(client, headers, project_id, batch_id):
    response = await client.get(
        f"/api/projects/{project_id}/lore/extractions/{batch_id}/candidates",
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()["items"][0]


@pytest.mark.usefixtures("clean_db")
async def test_multiple_objects_are_separate_review_candidates_with_evidence(
    client, auth_headers, monkeypatch
):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    call = AsyncMock(return_value=_response_json(_valid_candidates()))
    monkeypatch.setattr(llm_client, "chat_once", call)

    response = await _create_batch(client, auth_headers, project_id)
    assert response.status_code == 201, response.text
    batch = response.json()
    assert batch["status"] == "completed"
    assert batch["candidate_count"] == 3
    assert batch["pending_review_count"] == 3
    assert batch["source_preserved"] is True
    call.assert_awaited_once()

    listed = await client.get(
        f"/api/projects/{project_id}/lore/extractions/{batch['id']}/candidates",
        headers=auth_headers,
    )
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert [item["name"] for item in items] == ["林远", "苏瑶", "天玄宗"]
    assert [item["type_key"] for item in items] == [
        "character",
        "character",
        "faction",
    ]
    assert len({item["id"] for item in items}) == 3
    assert items[0]["payload"]["personality"] == "坚韧"
    assert items[0]["payload"]["appearance"] is None
    assert items[0]["field_states"]["appearance"] == "unknown"
    assert items[2]["payload"]["stance"] == "正道宗门"
    assert "personality" not in items[2]["payload"]

    personality = next(
        evidence
        for evidence in items[0]["evidence"]
        if evidence["field_key"] == "personality"
    )
    start = SOURCE.index("林远性格坚韧")
    assert personality["state"] == "provided"
    assert personality["label"] == "性格"
    assert personality["locator"] == {
        "char_start": start,
        "char_end": start + len("林远性格坚韧"),
        "complete": True,
    }
    assert personality["excerpt_hash"] == hashlib.sha256(
        "林远性格坚韧".encode("utf-8")
    ).hexdigest()

    async with TestSessionLocal() as session:
        stored = await session.scalar(
            select(LoreExtractionBatch).where(LoreExtractionBatch.id == batch["id"])
        )
        formal_count = await session.scalar(
            select(func.count()).select_from(SettingElement)
        )
        assert stored.source_text == SOURCE
        assert formal_count == 0


@pytest.mark.usefixtures("clean_db")
async def test_idempotency_reuses_batch_without_second_llm_call(
    client, auth_headers, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    call = AsyncMock(return_value=_response_json(_valid_candidates()))
    monkeypatch.setattr(llm_client, "chat_once", call)

    first = await _create_batch(client, auth_headers, project_id)
    second = await _create_batch(client, auth_headers, project_id)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    call.assert_awaited_once()

    conflict = await client.post(
        f"/api/projects/{project_id}/lore/extractions",
        headers=auth_headers,
        json={
            "idempotency_key": "extract-case-001",
            "document_text": SOURCE + "新增不同内容。",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "EXTRACTION_IDEMPOTENCY_CONFLICT"
    call.assert_awaited_once()


@pytest.mark.usefixtures("clean_db")
async def test_postgres_concurrent_idempotency_allows_only_one_llm_call(
    client, auth_headers, monkeypatch
):
    from tests.conftest import TEST_DATABASE_BACKEND

    if TEST_DATABASE_BACKEND == "sqlite":
        pytest.skip("Concurrent uniqueness is exercised by the PostgreSQL CI job")

    project_id = await _create_project(client, auth_headers)
    call = AsyncMock(return_value=_response_json(_valid_candidates()))
    monkeypatch.setattr(llm_client, "chat_once", call)
    first, second = await asyncio.gather(
        _create_batch(client, auth_headers, project_id, key="concurrent-key-001"),
        _create_batch(client, auth_headers, project_id, key="concurrent-key-001"),
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    call.assert_awaited_once()


@pytest.mark.usefixtures("clean_db")
async def test_invalid_contract_rolls_back_all_candidates(
    client, auth_headers, monkeypatch
):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    invalid = _valid_candidates()
    invalid[1]["unexpected"] = "must fail closed"
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json(invalid)),
    )

    response = await _create_batch(client, auth_headers, project_id)
    assert response.status_code == 201
    batch = response.json()
    assert batch["status"] == "failed"
    assert batch["error_code"] == "EXTRACTION_RESPONSE_INVALID"
    assert batch["candidate_count"] == 0

    async with TestSessionLocal() as session:
        candidate_count = await session.scalar(
            select(func.count()).select_from(LoreExtractionCandidate)
        )
        evidence_count = await session.scalar(
            select(func.count()).select_from(LoreCandidateFieldEvidence)
        )
        assert candidate_count == 0
        assert evidence_count == 0


@pytest.mark.usefixtures("clean_db")
async def test_unsupported_claim_is_not_saved_as_fact(
    client, auth_headers, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    candidate = {
        "type_key": "character",
        "name": "林远",
        "fields": [
            {
                "field_key": "abilities",
                "value": "掌握时间法则",
                "state": "provided",
                "excerpt": "原文并没有这句",
            },
            {
                "field_key": "personality",
                "value": "坚强",
                "state": "provided",
                "excerpt": "林远性格坚韧",
            },
        ],
        "relation_suggestions": [],
    }
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json([candidate])),
    )

    response = await _create_batch(client, auth_headers, project_id)
    batch_id = response.json()["id"]
    listed = await client.get(
        f"/api/projects/{project_id}/lore/extractions/{batch_id}/candidates",
        headers=auth_headers,
    )
    item = listed.json()["items"][0]
    assert item["payload"]["abilities"] is None
    assert item["field_states"]["abilities"] == "unknown"
    assert item["payload"]["personality"] == "坚强"
    assert item["field_states"]["personality"] == "needs_confirmation"
    assert item["can_accept"] is False
    assert "fields_need_confirmation" in item["disabled_reasons"]


@pytest.mark.usefixtures("clean_db")
async def test_same_batch_duplicates_are_flagged_but_not_merged(
    client, auth_headers, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    duplicate = _valid_candidates()[0]
    duplicate["fields"] = [
        {
            "field_key": "personality",
            "value": "坚韧",
            "state": "provided",
            "excerpt": "林远性格坚韧",
        }
    ]
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(
            return_value=_response_json([_valid_candidates()[0], duplicate])
        ),
    )

    created = await _create_batch(client, auth_headers, project_id)
    listed = await client.get(
        f"/api/projects/{project_id}/lore/extractions/"
        f"{created.json()['id']}/candidates",
        headers=auth_headers,
    )
    items = listed.json()["items"]
    assert len(items) == 2
    assert items[0]["id"] != items[1]["id"]
    for item in items:
        suggestion = item["duplicate_conflict_suggestions"][0]
        assert suggestion["kind"] == "possible_duplicate"
        assert suggestion["resolution_status"] == "unresolved"
        assert suggestion["target_candidate_ordinal"] != item["ordinal"]


@pytest.mark.usefixtures("clean_db")
async def test_timeout_is_outcome_unknown_and_never_auto_retried(
    client, auth_headers, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    call = AsyncMock(
        side_effect=LLMSingleCallError(
            "LLM_OUTCOME_UNKNOWN",
            "LLM 请求超时，结果状态无法确认",
            outcome_unknown=True,
        )
    )
    monkeypatch.setattr(llm_client, "chat_once", call)

    response = await _create_batch(client, auth_headers, project_id)
    assert response.status_code == 201
    assert response.json()["status"] == "outcome_unknown"
    assert response.json()["retryable"] is False
    call.assert_awaited_once()

    repeated = await _create_batch(client, auth_headers, project_id)
    assert repeated.json()["id"] == response.json()["id"]
    call.assert_awaited_once()


@pytest.mark.usefixtures("clean_db")
async def test_overlong_source_is_rejected_before_llm_or_batch_creation(
    client, auth_headers, monkeypatch
):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    call = AsyncMock(return_value=_response_json([]))
    monkeypatch.setattr(llm_client, "chat_once", call)
    response = await client.post(
        f"/api/projects/{project_id}/lore/extractions",
        headers=auth_headers,
        json={
            "idempotency_key": "extract-too-long-001",
            "document_text": "设" * 20_001,
        },
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "EXTRACTION_SOURCE_TOO_LONG"
    call.assert_not_awaited()
    async with TestSessionLocal() as session:
        batch_count = await session.scalar(
            select(func.count()).select_from(LoreExtractionBatch)
        )
        assert batch_count == 0


@pytest.mark.usefixtures("clean_db")
async def test_extraction_requires_owner_for_create_and_read(
    client, auth_headers, second_auth_headers, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json([])),
    )
    forbidden = await _create_batch(client, second_auth_headers, project_id)
    assert forbidden.status_code == 403

    created = await _create_batch(client, auth_headers, project_id)
    batch_id = created.json()["id"]
    read = await client.get(
        f"/api/projects/{project_id}/lore/extractions/{batch_id}",
        headers=second_auth_headers,
    )
    assert read.status_code == 403


@pytest.mark.usefixtures("clean_db")
async def test_maintenance_freeze_prevents_batch_and_llm_call(
    client, auth_headers, monkeypatch
):
    from app.config import settings as app_settings
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    call = AsyncMock(return_value=_response_json([]))
    monkeypatch.setattr(llm_client, "chat_once", call)
    original = app_settings.LEGACY_JSON_WRITES_FROZEN
    app_settings.LEGACY_JSON_WRITES_FROZEN = True
    try:
        response = await _create_batch(client, auth_headers, project_id)
    finally:
        app_settings.LEGACY_JSON_WRITES_FROZEN = original

    assert response.status_code == 503
    assert response.json()["code"] == "PROJECT_WRITE_FROZEN"
    call.assert_not_awaited()
    async with TestSessionLocal() as session:
        batch_count = await session.scalar(
            select(func.count()).select_from(LoreExtractionBatch)
        )
        assert batch_count == 0


@pytest.mark.usefixtures("clean_db")
async def test_freeze_after_llm_rolls_back_candidates_and_returns_503(
    client, auth_headers, monkeypatch
):
    from app.config import settings as app_settings
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)

    async def freeze_after_call(*_args, **_kwargs):
        monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
        return _response_json(_valid_candidates())

    monkeypatch.setattr(llm_client, "chat_once", freeze_after_call)
    try:
        response = await _create_batch(
            client,
            auth_headers,
            project_id,
            key="freeze-after-llm-001",
        )
    finally:
        monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", False)

    assert response.status_code == 503
    assert response.json()["code"] == "PROJECT_WRITE_FROZEN"
    async with TestSessionLocal() as session:
        batch = await session.scalar(
            select(LoreExtractionBatch).where(
                LoreExtractionBatch.idempotency_key == "freeze-after-llm-001"
            )
        )
        candidate_count = await session.scalar(
            select(func.count()).select_from(LoreExtractionCandidate)
        )
        evidence_count = await session.scalar(
            select(func.count()).select_from(LoreCandidateFieldEvidence)
        )
        assert batch.status == "failed"
        assert batch.error_code == "PROJECT_WRITE_FROZEN"
        assert candidate_count == 0
        assert evidence_count == 0


@pytest.mark.usefixtures("clean_db")
async def test_failure_after_first_candidate_flush_rolls_back_every_candidate(
    client, auth_headers, monkeypatch
):
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json(_valid_candidates())),
    )
    original_flush = AsyncSession.flush
    flush_calls = 0

    async def fail_second_flush(session, *args, **kwargs):
        nonlocal flush_calls
        flush_calls += 1
        if flush_calls == 2:
            raise RuntimeError("injected candidate flush failure")
        return await original_flush(session, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "flush", fail_second_flush)
    response = await _create_batch(
        client,
        auth_headers,
        project_id,
        key="flush-failure-001",
    )
    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "EXTRACTION_SAVE_FAILED"
    async with TestSessionLocal() as session:
        candidate_count = await session.scalar(
            select(func.count()).select_from(LoreExtractionCandidate)
        )
        evidence_count = await session.scalar(
            select(func.count()).select_from(LoreCandidateFieldEvidence)
        )
        assert candidate_count == 0
        assert evidence_count == 0


@pytest.mark.usefixtures("clean_db")
async def test_final_commit_failure_rolls_back_and_marks_batch_failed(
    client, auth_headers, monkeypatch
):
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json(_valid_candidates())),
    )
    original_commit = AsyncSession.commit
    commit_calls = 0

    async def fail_final_commit(session, *args, **kwargs):
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise RuntimeError("injected final commit failure")
        return await original_commit(session, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "commit", fail_final_commit)
    response = await _create_batch(
        client,
        auth_headers,
        project_id,
        key="commit-failure-001",
    )
    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "EXTRACTION_SAVE_FAILED"
    async with TestSessionLocal() as session:
        candidate_count = await session.scalar(
            select(func.count()).select_from(LoreExtractionCandidate)
        )
        evidence_count = await session.scalar(
            select(func.count()).select_from(LoreCandidateFieldEvidence)
        )
        assert candidate_count == 0
        assert evidence_count == 0


@pytest.mark.usefixtures("clean_db")
async def test_candidate_edit_preserves_evidence_and_creates_revision(
    client, auth_headers, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json([_valid_candidates()[0]])),
    )
    batch = (await _create_batch(client, auth_headers, project_id)).json()
    candidate = await _first_candidate(
        client, auth_headers, project_id, batch["id"]
    )
    response = await client.patch(
        f"/api/projects/{project_id}/lore/extractions/{batch['id']}/"
        f"candidates/{candidate['id']}",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "type_key": "character",
            "name": "林远",
            "summary": "用户确认后的摘要",
            "payload": {"personality": "勇敢"},
            "field_states": {"personality": "provided"},
        },
    )
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["revision"] == 2
    assert edited["payload"]["personality"] == "勇敢"
    evidence = next(
        item for item in edited["evidence"] if item["field_key"] == "personality"
    )
    assert evidence["extracted_value"] == "坚韧"
    assert evidence["current_value"] == "勇敢"
    assert evidence["value_origin"] == "user_override"
    assert evidence["excerpt"] == "林远性格坚韧"

    revisions = await client.get(
        f"/api/projects/{project_id}/lore/extractions/{batch['id']}/"
        f"candidates/{candidate['id']}/revisions",
        headers=auth_headers,
    )
    assert [item["change_kind"] for item in revisions.json()["items"]] == [
        "extracted",
        "edited",
    ]
    stale = await client.patch(
        f"/api/projects/{project_id}/lore/extractions/{batch['id']}/"
        f"candidates/{candidate['id']}",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "type_key": "character",
            "name": "过期覆盖",
            "payload": {},
            "field_states": {},
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "LORE_CANDIDATE_VERSION_CONFLICT"
    assert stale.json()["detail"]["latest_revision"] == 2


@pytest.mark.usefixtures("clean_db")
async def test_accept_requires_relational_mode(client, auth_headers, monkeypatch):
    project_id = await _create_project(client, auth_headers)
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json([_valid_candidates()[0]])),
    )
    batch = (await _create_batch(client, auth_headers, project_id)).json()
    candidate = await _first_candidate(
        client, auth_headers, project_id, batch["id"]
    )
    response = await client.post(
        f"/api/projects/{project_id}/lore/extractions/{batch['id']}/"
        f"candidates/{candidate['id']}/accept",
        headers=auth_headers,
        json={"expected_version": 1},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "LORE_MODE_NOT_RELATIONAL"


@pytest.mark.usefixtures("clean_db")
async def test_accept_is_atomic_and_idempotent(client, auth_headers, monkeypatch):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _make_relational(project_id)
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json([_valid_candidates()[0]])),
    )
    batch = (await _create_batch(client, auth_headers, project_id)).json()
    candidate = await _first_candidate(
        client, auth_headers, project_id, batch["id"]
    )
    endpoint = (
        f"/api/projects/{project_id}/lore/extractions/{batch['id']}/"
        f"candidates/{candidate['id']}/accept"
    )
    accepted = await client.post(
        endpoint,
        headers=auth_headers,
        json={"expected_version": 1},
    )
    assert accepted.status_code == 200, accepted.text
    data = accepted.json()
    assert data["action_result"] == "accepted"
    assert data["replayed"] is False
    element_id = data["accepted_element_id"]
    assert element_id

    async def counts():
        async with TestSessionLocal() as session:
            return (
                await session.scalar(select(func.count()).select_from(SettingElement)),
                await session.scalar(select(func.count()).select_from(ElementVersion)),
                await session.scalar(select(func.count()).select_from(ElementSource)),
                await session.scalar(
                    select(func.count()).select_from(ElementStateEvent)
                ),
                await session.scalar(
                    select(func.count()).select_from(LoreCandidateRevision)
                ),
            )

    assert await counts() == (1, 1, 1, 1, 2)
    async with TestSessionLocal() as session:
        version = await session.scalar(select(ElementVersion))
        source = await session.scalar(select(ElementSource))
        assert version.source_id == source.id
        assert source.source_kind == "system_extract"
        assert source.locator["candidate_id"] == candidate["id"]

    replay = await client.post(
        endpoint,
        headers=auth_headers,
        json={"expected_version": 1},
    )
    assert replay.status_code == 200
    assert replay.json()["action_result"] == "already_accepted"
    assert replay.json()["replayed"] is True
    assert replay.json()["accepted_element_id"] == element_id
    assert await counts() == (1, 1, 1, 1, 2)


@pytest.mark.usefixtures("clean_db")
async def test_reject_is_preserved_and_idempotent_in_legacy_mode(
    client, auth_headers, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json([_valid_candidates()[0]])),
    )
    batch = (await _create_batch(client, auth_headers, project_id)).json()
    candidate = await _first_candidate(
        client, auth_headers, project_id, batch["id"]
    )
    endpoint = (
        f"/api/projects/{project_id}/lore/extractions/{batch['id']}/"
        f"candidates/{candidate['id']}"
    )
    rejected = await client.post(
        endpoint + "/reject",
        headers=auth_headers,
        json={"expected_version": 1},
    )
    assert rejected.status_code == 200
    assert rejected.json()["action_result"] == "rejected"
    replay = await client.post(
        endpoint + "/reject",
        headers=auth_headers,
        json={"expected_version": 1},
    )
    assert replay.json()["action_result"] == "already_rejected"
    accept = await client.post(
        endpoint + "/accept",
        headers=auth_headers,
        json={"expected_version": 2},
    )
    assert accept.status_code == 409
    assert accept.json()["detail"]["code"] == "LORE_CANDIDATE_ALREADY_REJECTED"


@pytest.mark.usefixtures("clean_db")
async def test_unresolved_duplicate_blocks_accept_until_explicit_resolution(
    client, auth_headers, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    await _make_relational(project_id)
    duplicate = _valid_candidates()[0]
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json([_valid_candidates()[0], duplicate])),
    )
    batch = (await _create_batch(client, auth_headers, project_id)).json()
    candidate = await _first_candidate(
        client, auth_headers, project_id, batch["id"]
    )
    suggestion_id = candidate["duplicate_conflict_suggestions"][0]["suggestion_id"]
    endpoint = (
        f"/api/projects/{project_id}/lore/extractions/{batch['id']}/"
        f"candidates/{candidate['id']}/accept"
    )
    blocked = await client.post(
        endpoint,
        headers=auth_headers,
        json={"expected_version": 1},
    )
    assert blocked.status_code == 422
    assert (
        blocked.json()["detail"]["code"]
        == "LORE_CANDIDATE_SUGGESTIONS_UNRESOLVED"
    )
    accepted = await client.post(
        endpoint,
        headers=auth_headers,
        json={
            "expected_version": 1,
            "suggestion_resolutions": {suggestion_id: "accept_as_new"},
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["action_result"] == "accepted"


@pytest.mark.usefixtures("clean_db")
async def test_accept_failure_after_formal_rows_rolls_back_entire_transaction(
    client, auth_headers, monkeypatch
):
    from app.api import lore_extraction as extraction_api
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _make_relational(project_id)
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json([_valid_candidates()[0]])),
    )
    batch = (await _create_batch(client, auth_headers, project_id)).json()
    candidate = await _first_candidate(
        client, auth_headers, project_id, batch["id"]
    )
    original_create = extraction_api.create_element

    async def fail_after_formal_rows(**kwargs):
        await original_create(**kwargs)
        raise RuntimeError("injected after formal rows")

    monkeypatch.setattr(extraction_api, "create_element", fail_after_formal_rows)
    response = await client.post(
        f"/api/projects/{project_id}/lore/extractions/{batch['id']}/"
        f"candidates/{candidate['id']}/accept",
        headers=auth_headers,
        json={"expected_version": 1},
    )
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "LORE_CANDIDATE_ACCEPT_FAILED"
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(SettingElement)
        ) == 0
        assert await session.scalar(
            select(func.count()).select_from(ElementVersion)
        ) == 0
        stored = await session.scalar(
            select(LoreExtractionCandidate).where(
                LoreExtractionCandidate.id == candidate["id"]
            )
        )
        assert stored.status == "pending_review"
        assert stored.revision == 1


@pytest.mark.usefixtures("clean_db")
async def test_maintenance_freeze_after_accept_claim_returns_503_and_rolls_back(
    client, auth_headers, monkeypatch
):
    from app.api import lore_extraction as extraction_api
    from app.core.maintenance import ProjectWriteFrozenError
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _make_relational(project_id)
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json([_valid_candidates()[0]])),
    )
    batch = (await _create_batch(client, auth_headers, project_id)).json()
    candidate = await _first_candidate(
        client, auth_headers, project_id, batch["id"]
    )
    checks = 0

    def freeze_before_commit():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ProjectWriteFrozenError("PROJECT_WRITE_FROZEN")

    monkeypatch.setattr(
        extraction_api,
        "ensure_project_writes_available",
        freeze_before_commit,
    )
    response = await client.post(
        f"/api/projects/{project_id}/lore/extractions/{batch['id']}/"
        f"candidates/{candidate['id']}/accept",
        headers=auth_headers,
        json={"expected_version": 1},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "PROJECT_WRITE_FROZEN"
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(SettingElement)
        ) == 0
        stored = await session.scalar(
            select(LoreExtractionCandidate).where(
                LoreExtractionCandidate.id == candidate["id"]
            )
        )
        assert stored.status == "pending_review"
        assert stored.revision == 1


@pytest.mark.usefixtures("clean_db")
async def test_user_override_accept_uses_manual_review_primary_source(
    client, auth_headers, monkeypatch
):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _make_relational(project_id)
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json([_valid_candidates()[0]])),
    )
    batch = (await _create_batch(client, auth_headers, project_id)).json()
    candidate = await _first_candidate(
        client, auth_headers, project_id, batch["id"]
    )
    edited = await client.patch(
        f"/api/projects/{project_id}/lore/extractions/{batch['id']}/"
        f"candidates/{candidate['id']}",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "type_key": "character",
            "name": "林远",
            "summary": "用户修订",
            "payload": {"personality": "勇敢"},
            "field_states": {"personality": "provided"},
        },
    )
    assert edited.status_code == 200
    accepted = await client.post(
        f"/api/projects/{project_id}/lore/extractions/{batch['id']}/"
        f"candidates/{candidate['id']}/accept",
        headers=auth_headers,
        json={"expected_version": 2},
    )
    assert accepted.status_code == 200, accepted.text
    async with TestSessionLocal() as session:
        element = await session.scalar(select(SettingElement))
        sources = list(
            (
                await session.execute(
                    select(ElementSource).order_by(ElementSource.source_kind)
                )
            )
            .scalars()
            .all()
        )
        version = await session.scalar(select(ElementVersion))
        assert element.payload["personality"] == "勇敢"
        assert {source.source_kind for source in sources} == {
            "manual_review",
            "system_extract",
        }
        manual = next(s for s in sources if s.source_kind == "manual_review")
        extracted = next(s for s in sources if s.source_kind == "system_extract")
        assert manual.is_primary is True
        assert extracted.is_primary is False
        assert version.source_id == manual.id
        assert extracted.excerpt is not None


@pytest.mark.usefixtures("clean_db")
async def test_state_confirmation_and_repeated_edits_preserve_manual_origin(
    client, auth_headers, monkeypatch
):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _make_relational(project_id)
    candidate_data = {
        "type_key": "character",
        "name": "林远",
        "fields": [
            {
                "field_key": "personality",
                "value": "坚强",
                "state": "provided",
                "excerpt": "林远性格坚韧",
            }
        ],
        "relation_suggestions": [],
    }
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json([candidate_data])),
    )
    batch = (await _create_batch(client, auth_headers, project_id)).json()
    candidate = await _first_candidate(
        client, auth_headers, project_id, batch["id"]
    )
    assert candidate["payload"]["personality"] == "坚强"
    assert candidate["field_states"]["personality"] == "needs_confirmation"

    endpoint = (
        f"/api/projects/{project_id}/lore/extractions/{batch['id']}/"
        f"candidates/{candidate['id']}"
    )
    confirmed = await client.patch(
        endpoint,
        headers=auth_headers,
        json={
            "expected_version": 1,
            "type_key": "character",
            "name": "林远",
            "summary": "第一次人工确认",
            "payload": {"personality": "坚强"},
            "field_states": {"personality": "provided"},
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["user_overrides"]["personality"]["state"] == "provided"
    assert (
        next(
            item
            for item in confirmed.json()["evidence"]
            if item["field_key"] == "personality"
        )["value_origin"]
        == "user_override"
    )

    edited_again = await client.patch(
        endpoint,
        headers=auth_headers,
        json={
            "expected_version": 2,
            "type_key": "character",
            "name": "林远",
            "summary": "第二次人工确认",
            "payload": {"personality": "坚强"},
            "field_states": {"personality": "provided"},
        },
    )
    assert edited_again.status_code == 200, edited_again.text
    assert "personality" in edited_again.json()["user_overrides"]
    assert edited_again.json()["user_overrides"]["summary"]["value"] == "第二次人工确认"

    accepted = await client.post(
        endpoint + "/accept",
        headers=auth_headers,
        json={"expected_version": 3},
    )
    assert accepted.status_code == 200, accepted.text
    async with TestSessionLocal() as session:
        sources = list((await session.execute(select(ElementSource))).scalars().all())
        assert len(sources) == 2
        assert next(source for source in sources if source.is_primary).source_kind == (
            "manual_review"
        )


@pytest.mark.usefixtures("clean_db")
async def test_needs_confirmation_blocks_accept_without_formal_rows(
    client, auth_headers, monkeypatch
):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _make_relational(project_id)
    candidate_data = {
        "type_key": "character",
        "name": "林远",
        "fields": [
            {
                "field_key": "personality",
                "value": "坚强",
                "state": "provided",
                "excerpt": "林远性格坚韧",
            }
        ],
        "relation_suggestions": [],
    }
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json([candidate_data])),
    )
    batch = (await _create_batch(client, auth_headers, project_id)).json()
    candidate = await _first_candidate(
        client, auth_headers, project_id, batch["id"]
    )
    assert candidate["needs_attention"] is True
    overview = await client.get(
        f"/api/projects/{project_id}/lore/overview",
        headers=auth_headers,
    )
    assert overview.status_code == 200
    assert overview.json()["pending_review"] == 1
    assert overview.json()["needs_attention"] == 1
    response = await client.post(
        f"/api/projects/{project_id}/lore/extractions/{batch['id']}/"
        f"candidates/{candidate['id']}/accept",
        headers=auth_headers,
        json={"expected_version": 1},
    )
    assert response.status_code == 422
    assert (
        response.json()["detail"]["code"]
        == "LORE_CANDIDATE_FIELDS_NEED_CONFIRMATION"
    )
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(SettingElement)
        ) == 0


@pytest.mark.usefixtures("clean_db")
async def test_postgres_concurrent_accept_is_idempotent(
    client, auth_headers, monkeypatch
):
    from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal

    if TEST_DATABASE_BACKEND == "sqlite":
        pytest.skip("Concurrent candidate actions run in PostgreSQL CI")
    project_id = await _create_project(client, auth_headers)
    await _make_relational(project_id)
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json([_valid_candidates()[0]])),
    )
    batch = (await _create_batch(client, auth_headers, project_id)).json()
    candidate = await _first_candidate(
        client, auth_headers, project_id, batch["id"]
    )
    endpoint = (
        f"/api/projects/{project_id}/lore/extractions/{batch['id']}/"
        f"candidates/{candidate['id']}/accept"
    )
    first, second = await asyncio.gather(
        client.post(endpoint, headers=auth_headers, json={"expected_version": 1}),
        client.post(endpoint, headers=auth_headers, json={"expected_version": 1}),
    )
    assert first.status_code == second.status_code == 200
    assert {first.json()["action_result"], second.json()["action_result"]} == {
        "accepted",
        "already_accepted",
    }
    assert first.json()["accepted_element_id"] == second.json()[
        "accepted_element_id"
    ]
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(SettingElement)
        ) == 1
        assert await session.scalar(
            select(func.count()).select_from(ElementVersion)
        ) == 1


@pytest.mark.usefixtures("clean_db")
async def test_project_candidate_inbox_filters_pages_and_reports_overview(
    client, auth_headers, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    first = _valid_candidates()[0]
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json([first, first, _valid_candidates()[1]])),
    )
    batch = (await _create_batch(client, auth_headers, project_id)).json()
    inbox_url = f"/api/projects/{project_id}/lore/extractions/candidates"
    first_page = await client.get(
        inbox_url,
        headers=auth_headers,
        params={"limit": 1},
    )
    assert first_page.status_code == 200, first_page.text
    data = first_page.json()
    assert data["total"] == 3
    assert data["has_more"] is True
    assert data["next_cursor"]
    assert data["applied_filters"]["status"] == "pending_review"
    assert data["query_signature"]

    second_page = await client.get(
        inbox_url,
        headers=auth_headers,
        params={"limit": 1, "cursor": data["next_cursor"]},
    )
    assert second_page.status_code == 200
    assert second_page.json()["items"][0]["id"] != data["items"][0]["id"]

    searched = await client.get(
        inbox_url,
        headers=auth_headers,
        params={"q": "苏瑶"},
    )
    assert searched.status_code == 200
    assert searched.json()["total"] == 1
    assert searched.json()["items"][0]["name"] == "苏瑶"

    attention = await client.get(
        inbox_url,
        headers=auth_headers,
        params={"needs_attention": True},
    )
    assert attention.status_code == 200
    assert attention.json()["total"] >= 1
    assert all(
        item["duplicate_conflict_suggestions"]
        for item in attention.json()["items"]
    )

    overview = await client.get(
        f"/api/projects/{project_id}/lore/overview",
        headers=auth_headers,
    )
    assert overview.status_code == 200, overview.text
    assert overview.json()["formal_total"] == 0
    assert overview.json()["confirmed_active"] == 0
    assert overview.json()["pending_review"] == 3
    assert overview.json()["needs_attention"] == attention.json()["total"]
    assert overview.json()["capabilities"]["candidate_accept"] is False
    assert overview.json()["capabilities"]["formal_create"] is False
    assert overview.json()["capabilities"]["formal_conflict_tracking"] is False

    other_project = await _create_project(client, auth_headers, title="其他候选项目")
    mismatched = await client.get(
        f"/api/projects/{other_project}/lore/extractions/candidates",
        headers=auth_headers,
        params={"limit": 1, "cursor": data["next_cursor"]},
    )
    assert mismatched.status_code == 400

    batch_filtered = await client.get(
        inbox_url,
        headers=auth_headers,
        params={"batch_id": batch["id"], "type": "character"},
    )
    assert batch_filtered.status_code == 200
    assert batch_filtered.json()["total"] == 3


@pytest.mark.usefixtures("clean_db")
async def test_candidate_without_name_is_indexed_as_needing_attention(
    client, auth_headers, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    unnamed = {
        "type_key": "character",
        "name": None,
        "fields": [
            {
                "field_key": "personality",
                "value": "坚韧",
                "state": "provided",
                "excerpt": "性格坚韧",
            }
        ],
        "relation_suggestions": [],
    }
    monkeypatch.setattr(
        llm_client,
        "chat_once",
        AsyncMock(return_value=_response_json([unnamed])),
    )
    await _create_batch(client, auth_headers, project_id, key="unnamed-001")
    inbox = await client.get(
        f"/api/projects/{project_id}/lore/extractions/candidates",
        headers=auth_headers,
        params={"needs_attention": True},
    )
    assert inbox.status_code == 200
    assert inbox.json()["total"] == 1
    assert inbox.json()["items"][0]["name"] is None
    assert "name_missing" in inbox.json()["items"][0]["disabled_reasons"]
    overview = await client.get(
        f"/api/projects/{project_id}/lore/overview",
        headers=auth_headers,
    )
    assert overview.json()["needs_attention"] == 1
