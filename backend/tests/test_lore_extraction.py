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
    LoreExtractionBatch,
    LoreExtractionCandidate,
)
from app.models.lore import SettingElement


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
