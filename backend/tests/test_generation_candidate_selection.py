"""Chapter-level immutable candidate selection tests."""

import asyncio
import os
import sqlite3
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import func, select

from app.api import projects as projects_api
from app.config import settings as app_settings
from app.core import generation_candidate_selection as selection_core
from app.core import demo_fixture
from app.core.demo_generation import get_technical_demo_adapter
from app.core.generation_candidate_selection import (
    candidate_selection_id,
    candidate_selection_operation_id,
    candidate_selection_request_fingerprint,
)
from app.core.generation_execution import get_generation_transport
from app.core.maintenance import ProjectWriteFrozenError
from app.main import app
from app.models.generation import (
    ChapterGenerationAttempt,
    ChapterGenerationCandidate,
    ChapterGenerationCandidateSelection,
    ChapterGenerationCandidateSelectionOperation,
    ChapterGenerationRun,
    ChapterTechnicalDemoExecution,
)
from app.models.foreshadow import ForeshadowFact
from app.models.lore import SettingElement
from app.models.planning import PlanningChapter
from app.models.project import Chapter, Project, StoryMemory, _utcnow
from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal
from tests.test_demo_generation import (
    CountingTechnicalAdapter,
    _execute_once as _execute_technical_once,
    _prepare as _prepare_technical_demo,
)
from tests.test_generation_execution import (
    CountingFakeTransport,
    _authenticated_prepared_run,
    _execute_body,
    _execute_path,
)


def _current_path(project_id: str, chapter_id: str) -> str:
    return (
        f"/api/projects/{project_id}/planning/chapters/{chapter_id}/"
        "candidate-selection"
    )


def _select_path(project_id: str, chapter_id: str) -> str:
    return (
        f"/api/projects/{project_id}/planning/chapters/{chapter_id}/"
        "candidate-selection-operations"
    )


def _by_key_path(project_id: str, chapter_id: str, operation_key: str) -> str:
    return f"{_select_path(project_id, chapter_id)}/by-key/{operation_key}"


async def _count(model) -> int:
    async with TestSessionLocal() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


def _select_body(run, candidate, *, operation_key: str, expected_version: int):
    return {
        "operation_key": operation_key,
        "expected_selection_version": expected_version,
        "target_run_id": run.id,
        "target_candidate_id": candidate.id,
        "expected_candidate_version_no": candidate.version_no,
        "expected_candidate_checksum": candidate.content_checksum,
        "expected_context_checksum": run.context_checksum,
    }


async def _generated_candidate(client, auth_headers):
    user, project, run = await _authenticated_prepared_run()
    candidate = await _execute_generated_candidate(client, auth_headers, run)
    return user, project, run, candidate


async def _execute_generated_candidate(client, auth_headers, run):
    fake = CountingFakeTransport()
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        response = await client.post(
            _execute_path(run.project_id, run.id),
            headers=auth_headers,
            json=_execute_body(
                run,
                operation_key=f"selection-root-{uuid.uuid4().hex}",
            ),
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)
    assert response.status_code == 200
    assert fake.call_count == 1
    async with TestSessionLocal() as session:
        candidate = await session.get(
            ChapterGenerationCandidate, response.json()["candidate_id"]
        )
        assert candidate is not None
    return candidate


async def _manual_candidate(client, auth_headers, project, run, parent, content):
    response = await client.post(
        f"/api/projects/{project.id}/planning/generation-runs/{run.id}/"
        "candidate-manual-edits",
        headers=auth_headers,
        json={
            "operation_key": f"selection-manual-{uuid.uuid4().hex}",
            "parent_candidate_id": parent.id,
            "expected_parent_version_no": parent.version_no,
            "expected_parent_checksum": parent.content_checksum,
            "expected_context_checksum": run.context_checksum,
            "content": content,
        },
    )
    assert response.status_code == 200, response.text
    async with TestSessionLocal() as session:
        candidate = await session.get(
            ChapterGenerationCandidate, response.json()["candidate"]["id"]
        )
        assert candidate is not None
    return candidate


async def _clone_run_for_same_chapter(user, project, run):
    cloned = ChapterGenerationRun(
        id=uuid.uuid4().hex,
        project_id=project.id,
        plan_id=run.plan_id,
        planning_chapter_id=run.planning_chapter_id,
        requested_by=user.id,
        operation_key=f"selection-run-{uuid.uuid4().hex}",
        request_fingerprint="9" * 64,
        status="prepared",
        execution_mode="preflight_only",
        ai_invoked=False,
        billing_effect="none",
        structure_version=run.structure_version,
        assignment_version=run.assignment_version,
        chapter_lock_version=run.chapter_lock_version,
        context_schema_version=run.context_schema_version,
        context_manifest=run.context_manifest,
        context_checksum=run.context_checksum,
        context_size_bytes=run.context_size_bytes,
    )
    async with TestSessionLocal() as session:
        session.add(cloned)
        await session.commit()
    return cloned


async def _second_chapter(project, run):
    async with TestSessionLocal() as session:
        source = await session.get(PlanningChapter, run.planning_chapter_id)
        assert source is not None
        chapter = PlanningChapter(
            id=uuid.uuid4().hex,
            project_id=project.id,
            plan_id=source.plan_id,
            part_id=source.part_id,
            title="第二章",
            summary="跨章采用操作隔离测试。",
            target_word_count=source.target_word_count,
            position=source.position + 1,
            status="active",
            lock_version=1,
        )
        session.add(chapter)
        await session.commit()
    return chapter


async def _seed_selection(user, project, run, candidate, *, operation_key: str):
    selected_at = _utcnow()
    operation_id = candidate_selection_operation_id(
        user_id=user.id,
        project_id=project.id,
        operation_key=operation_key,
    )
    fingerprint = candidate_selection_request_fingerprint(
        project_id=project.id,
        user_id=user.id,
        planning_chapter_id=run.planning_chapter_id,
        operation_key=operation_key,
        expected_selection_version=0,
        target_run_id=run.id,
        target_candidate_id=candidate.id,
        expected_candidate_version_no=candidate.version_no,
        expected_candidate_checksum=candidate.content_checksum,
        expected_context_checksum=run.context_checksum,
    )
    operation = ChapterGenerationCandidateSelectionOperation(
        id=operation_id,
        project_id=project.id,
        planning_chapter_id=run.planning_chapter_id,
        requested_by=user.id,
        operation_key=operation_key,
        request_fingerprint=fingerprint,
        previous_selection_version=0,
        previous_run_id=None,
        previous_candidate_id=None,
        previous_candidate_version_no=None,
        previous_candidate_checksum=None,
        previous_context_checksum=None,
        result_selection_version=1,
        result_run_id=run.id,
        result_candidate_id=candidate.id,
        result_candidate_version_no=candidate.version_no,
        result_candidate_checksum=candidate.content_checksum,
        result_context_checksum=run.context_checksum,
        created_at=selected_at,
    )
    selection = ChapterGenerationCandidateSelection(
        id=candidate_selection_id(
            project_id=project.id,
            planning_chapter_id=run.planning_chapter_id,
        ),
        project_id=project.id,
        planning_chapter_id=run.planning_chapter_id,
        run_id=run.id,
        candidate_id=candidate.id,
        candidate_version_no=candidate.version_no,
        candidate_checksum=candidate.content_checksum,
        context_checksum=run.context_checksum,
        selection_version=1,
        changed_by=user.id,
        last_operation_id=operation_id,
        selected_at=selected_at,
        created_at=selected_at,
        updated_at=selected_at,
    )
    async with TestSessionLocal() as session:
        session.add(operation)
        await session.flush()
        session.add(selection)
        await session.commit()
    return operation_id


@pytest.mark.usefixtures("clean_db")
async def test_current_selection_starts_none_and_archived_chapter_remains_readable(
    client, auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    async with TestSessionLocal() as session:
        chapter = await session.get(PlanningChapter, run.planning_chapter_id)
        assert chapter is not None
        chapter.status = "archived"
        await session.commit()

    response = await client.get(
        _current_path(project.id, run.planning_chapter_id), headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "project_id": project.id,
        "planning_chapter_id": run.planning_chapter_id,
        "state": "none",
        "selection_version": 0,
        "run_id": None,
        "context_checksum": None,
        "candidate": None,
        "selected_at": None,
        "changed_by": None,
    }


@pytest.mark.usefixtures("clean_db")
async def test_current_selection_returns_strict_generated_provenance(
    client, auth_headers
):
    user, project, run, candidate = await _generated_candidate(client, auth_headers)
    await _seed_selection(
        user,
        project,
        run,
        candidate,
        operation_key="candidate-selection-current-0001",
    )

    response = await client.get(
        _current_path(project.id, run.planning_chapter_id), headers=auth_headers
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "selected"
    assert payload["selection_version"] == 1
    assert payload["run_id"] == run.id
    assert payload["context_checksum"] == run.context_checksum
    assert payload["changed_by"] == user.id
    assert payload["candidate"]["id"] == candidate.id
    assert payload["candidate"]["version_no"] == candidate.version_no
    assert payload["candidate"]["origin_kind"] == "generated"
    assert payload["candidate"]["root_candidate_id"] == candidate.id
    assert payload["candidate"]["ai_invoked_for_this_version"] is True
    assert "content" not in payload["candidate"]


@pytest.mark.usefixtures("clean_db")
async def test_current_selection_fails_closed_when_last_receipt_is_corrupt(
    client, auth_headers
):
    user, project, run, candidate = await _generated_candidate(client, auth_headers)
    operation_id = await _seed_selection(
        user,
        project,
        run,
        candidate,
        operation_key="candidate-selection-corrupt-0001",
    )
    async with TestSessionLocal() as session:
        operation = await session.scalar(
            select(ChapterGenerationCandidateSelectionOperation).where(
                ChapterGenerationCandidateSelectionOperation.id == operation_id
            )
        )
        assert operation is not None
        operation.result_candidate_checksum = "f" * 64
        await session.commit()

    response = await client.get(
        _current_path(project.id, run.planning_chapter_id), headers=auth_headers
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "GENERATION_CANDIDATE_SELECTION_CORRUPT",
        "message": "章节采用版本记录不完整，已停止展示采用状态。",
        "retryable": False,
        "recommended_action": "reload_candidate_selection",
    }
    by_key = await client.get(
        _by_key_path(
            project.id,
            run.planning_chapter_id,
            "candidate-selection-corrupt-0001",
        ),
        headers=auth_headers,
    )
    assert by_key.status_code == 409
    assert by_key.json()["detail"] == response.json()["detail"]


@pytest.mark.usefixtures("clean_db")
async def test_selection_routes_hide_foreign_owner_and_missing_operation(
    client, auth_headers, second_auth_headers
):
    _, project, run, candidate = await _generated_candidate(client, auth_headers)
    operation_key = "candidate-selection-owner-0001"
    created = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=_select_body(
            run,
            candidate,
            operation_key=operation_key,
            expected_version=0,
        ),
    )
    assert created.status_code == 200

    foreign_responses = [
        await client.get(
            _current_path(project.id, run.planning_chapter_id),
            headers=second_auth_headers,
        ),
        await client.post(
            _select_path(project.id, run.planning_chapter_id),
            headers=second_auth_headers,
            json=_select_body(
                run,
                candidate,
                operation_key="candidate-selection-owner-foreign",
                expected_version=0,
            ),
        ),
        await client.get(
            _by_key_path(project.id, run.planning_chapter_id, operation_key),
            headers=second_auth_headers,
        ),
    ]
    assert [response.status_code for response in foreign_responses] == [403, 403, 403]
    serialized = " ".join(response.text for response in foreign_responses)
    assert candidate.id not in serialized
    assert operation_key not in serialized

    missing = await client.get(
        _by_key_path(
            project.id,
            run.planning_chapter_id,
            "candidate-selection-missing-0001",
        ),
        headers=auth_headers,
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == {
        "code": "GENERATION_CANDIDATE_SELECTION_OPERATION_NOT_FOUND",
        "message": "尚未找到该章节采用操作。",
        "retryable": True,
        "recommended_action": "retry_original_candidate_selection",
    }


@pytest.mark.usefixtures("clean_db")
async def test_by_key_hides_same_project_operation_from_another_chapter(
    client, auth_headers
):
    _, project, run, candidate = await _generated_candidate(client, auth_headers)
    other_chapter = await _second_chapter(project, run)
    operation_key = "candidate-selection-other-chapter"
    body = _select_body(
        run,
        candidate,
        operation_key=operation_key,
        expected_version=0,
    )
    created = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=body,
    )
    assert created.status_code == 200

    hidden = await client.get(
        _by_key_path(project.id, other_chapter.id, operation_key),
        headers=auth_headers,
    )
    conflict = await client.post(
        _select_path(project.id, other_chapter.id),
        headers=auth_headers,
        json=body,
    )

    assert hidden.status_code == 404
    assert hidden.json()["detail"] == {
        "code": "GENERATION_CANDIDATE_SELECTION_OPERATION_NOT_FOUND",
        "message": "尚未找到该章节采用操作。",
        "retryable": True,
        "recommended_action": "retry_original_candidate_selection",
    }
    assert operation_key not in hidden.text
    assert candidate.id not in hidden.text
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == (
        "GENERATION_CANDIDATE_SELECTION_OPERATION_CONFLICT"
    )
    assert conflict.json()["detail"]["retryable"] is False
    assert conflict.json()["detail"]["recommended_action"] == (
        "start_new_candidate_selection"
    )


@pytest.mark.usefixtures("clean_db")
async def test_selection_rejects_foreign_project_candidate_without_receipt(
    client, auth_headers, second_auth_headers
):
    _, project, run, _ = await _generated_candidate(client, auth_headers)
    _, foreign_project, foreign_run = await _authenticated_prepared_run(
        "user2@example.com"
    )
    foreign_candidate = await _execute_generated_candidate(
        client, second_auth_headers, foreign_run
    )

    response = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=_select_body(
            foreign_run,
            foreign_candidate,
            operation_key="candidate-selection-foreign-project",
            expected_version=0,
        ),
    )

    assert response.status_code == 404
    assert foreign_project.id not in response.text
    assert foreign_candidate.id not in response.text
    assert await _count(ChapterGenerationCandidateSelectionOperation) == 0


@pytest.mark.usefixtures("clean_db")
async def test_new_selection_target_corruption_has_distinct_error_and_zero_writes(
    client, auth_headers
):
    _, project, run, candidate = await _generated_candidate(client, auth_headers)
    missing_body = _select_body(
        run,
        candidate,
        operation_key="candidate-selection-target-missing",
        expected_version=0,
    )
    missing_body["target_candidate_id"] = "f" * 32
    missing = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=missing_body,
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == {
        "code": "GENERATION_CANDIDATE_VERSION_NOT_FOUND",
        "message": "未找到要采用的章节候选版本。",
        "retryable": False,
        "recommended_action": "reload_generation_candidate_versions",
    }

    async with TestSessionLocal() as session:
        stored = await session.get(ChapterGenerationCandidate, candidate.id)
        assert stored is not None
        stored.content = f"{stored.content}\n未重算校验和。"
        await session.commit()

    response = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=_select_body(
            run,
            candidate,
            operation_key="candidate-selection-target-corrupt",
            expected_version=0,
        ),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "GENERATION_CANDIDATE_SELECTION_TARGET_CORRUPT",
        "message": "要采用的候选版本记录不完整，已停止本次采用。",
        "retryable": False,
        "recommended_action": "reload_generation_candidate_versions",
    }
    assert await _count(ChapterGenerationCandidateSelectionOperation) == 0
    assert await _count(ChapterGenerationCandidateSelection) == 0


@pytest.mark.usefixtures("clean_db")
async def test_selection_candidate_or_current_drift_fails_closed_without_writes(
    client, auth_headers
):
    _, project, run, candidate = await _generated_candidate(client, auth_headers)
    operation_key = "candidate-selection-drift-0001"
    created = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=_select_body(
            run,
            candidate,
            operation_key=operation_key,
            expected_version=0,
        ),
    )
    assert created.status_code == 200

    async with TestSessionLocal() as session:
        stored_candidate = await session.get(ChapterGenerationCandidate, candidate.id)
        assert stored_candidate is not None
        original_content = stored_candidate.content
        stored_candidate.content = f"{original_content}\n未更新校验和的损坏内容。"
        await session.commit()

    corrupt_candidate_responses = [
        await client.get(
            _current_path(project.id, run.planning_chapter_id),
            headers=auth_headers,
        ),
        await client.get(
            _by_key_path(project.id, run.planning_chapter_id, operation_key),
            headers=auth_headers,
        ),
    ]
    assert [response.status_code for response in corrupt_candidate_responses] == [
        409,
        409,
    ]

    async with TestSessionLocal() as session:
        stored_candidate = await session.get(ChapterGenerationCandidate, candidate.id)
        selection = await session.scalar(
            select(ChapterGenerationCandidateSelection).where(
                ChapterGenerationCandidateSelection.project_id == project.id
            )
        )
        assert stored_candidate is not None and selection is not None
        stored_candidate.content = original_content
        selection.context_checksum = "d" * 64
        await session.commit()

    corrupt_current = await client.get(
        _current_path(project.id, run.planning_chapter_id), headers=auth_headers
    )
    assert corrupt_current.status_code == 409
    assert corrupt_current.json()["detail"]["code"] == (
        "GENERATION_CANDIDATE_SELECTION_CORRUPT"
    )
    assert await _count(ChapterGenerationCandidateSelection) == 1
    assert await _count(ChapterGenerationCandidateSelectionOperation) == 1


@pytest.mark.usefixtures("clean_db")
async def test_select_replays_by_key_and_rejects_key_payload_or_version_conflicts(
    client, auth_headers
):
    _, project, run, candidate = await _generated_candidate(client, auth_headers)
    protected_models = (
        ChapterGenerationAttempt,
        ChapterGenerationCandidate,
        ChapterTechnicalDemoExecution,
        Chapter,
        StoryMemory,
        SettingElement,
        ForeshadowFact,
    )
    protected_counts = {model: await _count(model) for model in protected_models}
    candidate_snapshot = (
        candidate.content,
        candidate.content_checksum,
        candidate.version_no,
        candidate.parent_candidate_id,
    )
    operation_key = "candidate-selection-api-0001"
    body = _select_body(run, candidate, operation_key=operation_key, expected_version=0)

    created = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=body,
    )
    replayed = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=body,
    )
    by_key = await client.get(
        _by_key_path(project.id, run.planning_chapter_id, operation_key),
        headers=auth_headers,
    )

    assert created.status_code == replayed.status_code == by_key.status_code == 200
    assert {
        created.json()["operation_key"],
        replayed.json()["operation_key"],
        by_key.json()["operation_key"],
    } == {operation_key}
    assert created.json()["replayed"] is False
    assert replayed.json()["replayed"] is True
    assert by_key.json()["replayed"] is True
    assert created.json()["previous"] == {
        "state": "none",
        "selection_version": 0,
        "run_id": None,
        "context_checksum": None,
        "candidate": None,
    }
    assert created.json()["result"]["state"] == "selected"
    assert created.json()["result"]["selection_version"] == 1
    assert created.json()["result"]["candidate"]["id"] == candidate.id
    assert created.json()["changed"] is True
    assert created.json()["ai_invoked"] is False
    assert created.json()["billing_effect"] == "none"
    assert created.json()["usage_status"] == "not_applicable"

    conflicting_body = dict(body)
    conflicting_body["expected_candidate_checksum"] = "e" * 64
    key_conflict = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=conflicting_body,
    )
    same_candidate = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=_select_body(
            run,
            candidate,
            operation_key="candidate-selection-api-0002",
            expected_version=1,
        ),
    )
    stale_version = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=_select_body(
            run,
            candidate,
            operation_key="candidate-selection-api-0003",
            expected_version=0,
        ),
    )
    assert key_conflict.status_code == 409
    assert key_conflict.json()["detail"] == {
        "code": "GENERATION_CANDIDATE_SELECTION_OPERATION_CONFLICT",
        "message": "该采用操作编号已用于其他请求。",
        "retryable": False,
        "recommended_action": "start_new_candidate_selection",
    }
    assert same_candidate.status_code == 409
    assert same_candidate.json()["detail"] == {
        "code": "GENERATION_CANDIDATE_ALREADY_SELECTED",
        "message": "该候选已经是章节采用版本。",
        "retryable": False,
        "recommended_action": "reload_candidate_selection",
    }
    assert stale_version.status_code == 409
    assert stale_version.json()["detail"] == {
        "code": "GENERATION_CANDIDATE_SELECTION_VERSION_CONFLICT",
        "message": "章节采用版本已变化，请重新读取后再确认。",
        "retryable": False,
        "recommended_action": "reload_candidate_selection",
    }

    async with TestSessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count(ChapterGenerationCandidateSelectionOperation.id))
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(ChapterGenerationCandidateSelection.id))
            )
            == 1
        )
        stored_candidate = await session.get(ChapterGenerationCandidate, candidate.id)
        assert stored_candidate is not None
        assert (
            stored_candidate.content,
            stored_candidate.content_checksum,
            stored_candidate.version_no,
            stored_candidate.parent_candidate_id,
        ) == candidate_snapshot
    assert {
        model: await _count(model) for model in protected_models
    } == protected_counts


@pytest.mark.usefixtures("clean_db")
async def test_select_is_blocked_for_archived_chapter_without_receipt(
    client, auth_headers
):
    _, project, run, candidate = await _generated_candidate(client, auth_headers)
    async with TestSessionLocal() as session:
        chapter = await session.get(PlanningChapter, run.planning_chapter_id)
        assert chapter is not None
        chapter.status = "archived"
        await session.commit()

    response = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=_select_body(
            run,
            candidate,
            operation_key="candidate-selection-archived-0001",
            expected_version=0,
        ),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "GENERATION_PLANNING_CHAPTER_ARCHIVED",
        "message": "归档章节不能修改采用版本，请先恢复章节。",
        "retryable": False,
        "recommended_action": "restore_planning_chapter",
    }
    async with TestSessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count(ChapterGenerationCandidateSelectionOperation.id))
            )
            == 0
        )


@pytest.mark.usefixtures("clean_db")
async def test_select_maintenance_first_and_final_gates_leave_zero_rows(
    client, auth_headers, monkeypatch
):
    _, project, run, candidate = await _generated_candidate(client, auth_headers)
    body = _select_body(
        run,
        candidate,
        operation_key="candidate-selection-maintenance-0001",
        expected_version=0,
    )
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    first = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=body,
    )
    assert first.status_code == 503
    assert first.json()["code"] == "PROJECT_WRITE_FROZEN"
    assert first.json()["retryable"] is True
    assert first.json()["recommended_action"] == "retry_later"

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", False)
    calls = 0

    def freeze_after_flush():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ProjectWriteFrozenError("PROJECT_WRITE_FROZEN")

    monkeypatch.setattr(
        selection_core, "ensure_project_writes_available", freeze_after_flush
    )
    final = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=body,
    )
    assert final.status_code == 503
    assert final.json()["code"] == "PROJECT_WRITE_FROZEN"
    assert final.json()["retryable"] is True
    assert final.json()["recommended_action"] == "retry_later"
    async with TestSessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count(ChapterGenerationCandidateSelectionOperation.id))
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count(ChapterGenerationCandidateSelection.id))
            )
            == 0
        )


@pytest.mark.usefixtures("clean_db")
async def test_reselects_manual_parent_and_strict_older_version(client, auth_headers):
    _, project, run, root = await _generated_candidate(client, auth_headers)
    manual = await _manual_candidate(
        client,
        auth_headers,
        project,
        run,
        root,
        "沈星在星港重新标注了潮汐门限。",
    )
    selected_manual = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=_select_body(
            run,
            manual,
            operation_key="candidate-selection-manual-0001",
            expected_version=0,
        ),
    )
    selected_older_root = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=_select_body(
            run,
            root,
            operation_key="candidate-selection-older-0001",
            expected_version=1,
        ),
    )

    assert selected_manual.status_code == selected_older_root.status_code == 200
    assert selected_manual.json()["result"]["candidate"]["origin_kind"] == (
        "manual_edit"
    )
    assert selected_older_root.json()["previous"]["candidate"]["id"] == manual.id
    assert selected_older_root.json()["result"]["candidate"]["id"] == root.id
    assert selected_older_root.json()["result"]["selection_version"] == 2
    current = await client.get(
        _current_path(project.id, run.planning_chapter_id), headers=auth_headers
    )
    assert current.status_code == 200
    assert current.json()["candidate"]["id"] == root.id
    assert current.json()["selection_version"] == 2


@pytest.mark.usefixtures("clean_db")
async def test_reselects_a_strict_candidate_from_an_older_run_of_same_chapter(
    client, auth_headers
):
    user, project, old_run, old_candidate = await _generated_candidate(
        client, auth_headers
    )
    newer_run = await _clone_run_for_same_chapter(user, project, old_run)
    newer_candidate = await _execute_generated_candidate(
        client, auth_headers, newer_run
    )
    selected_newer = await client.post(
        _select_path(project.id, old_run.planning_chapter_id),
        headers=auth_headers,
        json=_select_body(
            newer_run,
            newer_candidate,
            operation_key="candidate-selection-newer-run-0001",
            expected_version=0,
        ),
    )
    selected_older = await client.post(
        _select_path(project.id, old_run.planning_chapter_id),
        headers=auth_headers,
        json=_select_body(
            old_run,
            old_candidate,
            operation_key="candidate-selection-older-run-0001",
            expected_version=1,
        ),
    )

    assert selected_newer.status_code == selected_older.status_code == 200
    assert selected_newer.json()["result"]["run_id"] == newer_run.id
    assert selected_older.json()["previous"]["run_id"] == newer_run.id
    assert selected_older.json()["result"]["run_id"] == old_run.id
    assert selected_older.json()["result"]["candidate"]["id"] == old_candidate.id


@pytest.mark.usefixtures("clean_db")
async def test_selects_technical_demo_candidate_without_paid_semantics(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(app_settings, "APP_ENVIRONMENT", "test")
    monkeypatch.setattr(app_settings, "DEMO_FIXTURE_ENABLED", True)
    monkeypatch.setattr(app_settings, "DEBUG", True)
    monkeypatch.setattr(demo_fixture, "load_settings", lambda: {"api_key": ""})
    if TEST_DATABASE_BACKEND == "postgresql":
        monkeypatch.setattr(
            demo_fixture,
            "_active_database_url",
            lambda _db: "sqlite+aiosqlite:///:memory:",
        )
    ids, run_payload = await _prepare_technical_demo(client, auth_headers)
    adapter = CountingTechnicalAdapter()
    app.dependency_overrides[get_technical_demo_adapter] = lambda: adapter
    try:
        created, _ = await _execute_technical_once(
            client,
            auth_headers,
            ids,
            run_payload,
            "candidate-selection-technical-root",
        )
    finally:
        app.dependency_overrides.pop(get_technical_demo_adapter, None)
    async with TestSessionLocal() as session:
        run = await session.get(ChapterGenerationRun, run_payload["id"])
        candidate = await session.get(
            ChapterGenerationCandidate, created["candidate_id"]
        )
        assert run is not None and candidate is not None

    response = await client.post(
        _select_path(ids["project_id"], ids["chapter_id"]),
        headers=auth_headers,
        json=_select_body(
            run,
            candidate,
            operation_key="candidate-selection-technical-0001",
            expected_version=0,
        ),
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["candidate"]["origin_kind"] == ("technical_demo")
    assert (
        response.json()["result"]["candidate"]["ai_invoked_for_this_version"] is False
    )
    assert response.json()["ai_invoked"] is False
    assert response.json()["billing_effect"] == "none"
    assert response.json()["usage_status"] == "not_applicable"
    assert adapter.call_count == 1


@pytest.mark.usefixtures("clean_db")
async def test_formal_project_delete_removes_selection_and_receipt_child_first(
    client, auth_headers, monkeypatch
):
    _, project, run, candidate = await _generated_candidate(client, auth_headers)
    selected = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=_select_body(
            run,
            candidate,
            operation_key="candidate-selection-formal-delete",
            expected_version=0,
        ),
    )
    assert selected.status_code == 200
    monkeypatch.setattr(projects_api, "archive_project_files", lambda _id: None)
    monkeypatch.setattr(
        projects_api, "finalize_project_file_delete", lambda _archive: None
    )

    deleted = await client.delete(f"/api/projects/{project.id}", headers=auth_headers)

    assert deleted.status_code == 200
    assert await _count(Project) == 0
    assert await _count(ChapterGenerationCandidateSelection) == 0
    assert await _count(ChapterGenerationCandidateSelectionOperation) == 0
    assert await _count(ChapterGenerationCandidate) == 0


@pytest.mark.usefixtures("clean_db")
async def test_formal_project_delete_failure_rolls_back_selection_cleanup(
    client, auth_headers, monkeypatch
):
    _, project, run, candidate = await _generated_candidate(client, auth_headers)
    selected = await client.post(
        _select_path(project.id, run.planning_chapter_id),
        headers=auth_headers,
        json=_select_body(
            run,
            candidate,
            operation_key="candidate-selection-delete-rollback",
            expected_version=0,
        ),
    )
    assert selected.status_code == 200
    real_cleanup = projects_api.delete_project_relational_dependents

    async def fail_after_cleanup(db, project_id):
        await real_cleanup(db, project_id)
        raise RuntimeError("injected selection cleanup failure")

    monkeypatch.setattr(
        projects_api, "delete_project_relational_dependents", fail_after_cleanup
    )
    monkeypatch.setattr(projects_api, "archive_project_files", lambda _id: None)

    deleted = await client.delete(f"/api/projects/{project.id}", headers=auth_headers)

    assert deleted.status_code == 500
    assert deleted.json()["detail"] == "删除未完成，项目仍保留，请重试"
    current = await client.get(
        _current_path(project.id, run.planning_chapter_id), headers=auth_headers
    )
    assert current.status_code == 200
    assert current.json()["candidate"]["id"] == candidate.id
    assert await _count(Project) == 1
    assert await _count(ChapterGenerationCandidateSelection) == 1
    assert await _count(ChapterGenerationCandidateSelectionOperation) == 1


@pytest.mark.usefixtures("clean_db")
async def test_postgres_concurrent_same_key_creates_one_selection_receipt(
    client, auth_headers
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("requires PostgreSQL row locks and independent connections")
    _, project, run, candidate = await _generated_candidate(client, auth_headers)
    body = _select_body(
        run,
        candidate,
        operation_key="candidate-selection-pg-same-key",
        expected_version=0,
    )

    first, second = await asyncio.wait_for(
        asyncio.gather(
            client.post(
                _select_path(project.id, run.planning_chapter_id),
                headers=auth_headers,
                json=body,
            ),
            client.post(
                _select_path(project.id, run.planning_chapter_id),
                headers=auth_headers,
                json=body,
            ),
        ),
        timeout=20,
    )

    assert first.status_code == second.status_code == 200
    assert sorted((first.json()["replayed"], second.json()["replayed"])) == [
        False,
        True,
    ]
    assert first.json()["result"] == second.json()["result"]
    assert await _count(ChapterGenerationCandidateSelectionOperation) == 1
    assert await _count(ChapterGenerationCandidateSelection) == 1


@pytest.mark.usefixtures("clean_db")
async def test_postgres_concurrent_different_keys_has_one_version_winner(
    client, auth_headers
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("requires PostgreSQL row locks and independent connections")
    _, project, run, candidate = await _generated_candidate(client, auth_headers)
    first_body = _select_body(
        run,
        candidate,
        operation_key="candidate-selection-pg-key-a",
        expected_version=0,
    )
    second_body = _select_body(
        run,
        candidate,
        operation_key="candidate-selection-pg-key-b",
        expected_version=0,
    )

    first, second = await asyncio.wait_for(
        asyncio.gather(
            client.post(
                _select_path(project.id, run.planning_chapter_id),
                headers=auth_headers,
                json=first_body,
            ),
            client.post(
                _select_path(project.id, run.planning_chapter_id),
                headers=auth_headers,
                json=second_body,
            ),
        ),
        timeout=20,
    )

    assert sorted((first.status_code, second.status_code)) == [200, 409]
    conflict = first if first.status_code == 409 else second
    assert conflict.json()["detail"]["code"] == (
        "GENERATION_CANDIDATE_SELECTION_VERSION_CONFLICT"
    )
    assert await _count(ChapterGenerationCandidateSelectionOperation) == 1
    assert await _count(ChapterGenerationCandidateSelection) == 1


@pytest.mark.usefixtures("clean_db")
async def test_postgres_delete_lock_first_prevents_selection(
    client, auth_headers, monkeypatch
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("requires PostgreSQL project row locks")
    _, project, run, candidate = await _generated_candidate(client, auth_headers)
    real_cleanup = projects_api.delete_project_relational_dependents
    lock_acquired = asyncio.Event()
    release_delete = asyncio.Event()
    delete_task = None
    selection_task = None

    async def paused_cleanup(db, project_id):
        lock_acquired.set()
        await release_delete.wait()
        await real_cleanup(db, project_id)

    monkeypatch.setattr(
        projects_api, "delete_project_relational_dependents", paused_cleanup
    )
    monkeypatch.setattr(projects_api, "archive_project_files", lambda _id: None)
    monkeypatch.setattr(
        projects_api, "finalize_project_file_delete", lambda _archive: None
    )
    try:
        delete_task = asyncio.create_task(
            client.delete(f"/api/projects/{project.id}", headers=auth_headers)
        )
        await asyncio.wait_for(lock_acquired.wait(), timeout=10)
        selection_task = asyncio.create_task(
            client.post(
                _select_path(project.id, run.planning_chapter_id),
                headers=auth_headers,
                json=_select_body(
                    run,
                    candidate,
                    operation_key="candidate-selection-pg-delete-first",
                    expected_version=0,
                ),
            )
        )
        await asyncio.sleep(0.1)
        assert not selection_task.done()
        release_delete.set()
        deleted, selected = await asyncio.wait_for(
            asyncio.gather(delete_task, selection_task), timeout=20
        )
    finally:
        release_delete.set()
        pending = [
            task for task in (delete_task, selection_task) if task and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    assert deleted.status_code == 200
    assert selected.status_code == 404
    assert await _count(Project) == 0
    assert await _count(ChapterGenerationCandidateSelectionOperation) == 0
    assert await _count(ChapterGenerationCandidateSelection) == 0


@pytest.mark.usefixtures("clean_db")
async def test_postgres_selection_lock_first_serializes_project_delete(
    client, auth_headers, monkeypatch
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("requires PostgreSQL project row locks")
    _, project, run, candidate = await _generated_candidate(client, auth_headers)
    original_response = selection_core._selection_operation_response
    selection_locked = asyncio.Event()
    release_selection = asyncio.Event()
    selection_task = None
    delete_task = None

    async def paused_response(db, operation, *, user_id, replayed):
        selection_locked.set()
        await release_selection.wait()
        return await original_response(
            db, operation, user_id=user_id, replayed=replayed
        )

    monkeypatch.setattr(
        selection_core, "_selection_operation_response", paused_response
    )
    monkeypatch.setattr(projects_api, "archive_project_files", lambda _id: None)
    monkeypatch.setattr(
        projects_api, "finalize_project_file_delete", lambda _archive: None
    )
    try:
        selection_task = asyncio.create_task(
            client.post(
                _select_path(project.id, run.planning_chapter_id),
                headers=auth_headers,
                json=_select_body(
                    run,
                    candidate,
                    operation_key="candidate-selection-pg-selection-first",
                    expected_version=0,
                ),
            )
        )
        await asyncio.wait_for(selection_locked.wait(), timeout=10)
        delete_task = asyncio.create_task(
            client.delete(f"/api/projects/{project.id}", headers=auth_headers)
        )
        await asyncio.sleep(0.1)
        assert not delete_task.done()
        release_selection.set()
        selected, deleted = await asyncio.wait_for(
            asyncio.gather(selection_task, delete_task), timeout=20
        )
    finally:
        release_selection.set()
        pending = [
            task for task in (selection_task, delete_task) if task and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    assert selected.status_code == 200
    assert deleted.status_code == 200
    assert await _count(Project) == 0
    assert await _count(ChapterGenerationCandidateSelectionOperation) == 0
    assert await _count(ChapterGenerationCandidateSelection) == 0


def _run_alembic(backend_dir, database_url, *args):
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": database_url,
            "DEBUG": "true",
            "JWT_SECRET": "candidate-selection-migration-test-secret",
        }
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=backend_dir,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_candidate_selection_migration_empty_round_trip(tmp_path):
    backend_dir = os.path.dirname(os.path.dirname(__file__))
    database_path = tmp_path / "candidate-selection.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    _run_alembic(backend_dir, database_url, "upgrade", "head")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "chapter_generation_candidate_selections" in tables
    assert "chapter_generation_candidate_selection_operations" in tables
    _run_alembic(backend_dir, database_url, "downgrade", "a9d1e3f5c018")
    _run_alembic(backend_dir, database_url, "upgrade", "head")


def test_candidate_selection_migration_refuses_nonempty_downgrade(tmp_path):
    backend_dir = os.path.dirname(os.path.dirname(__file__))
    database_path = tmp_path / "candidate-selection-nonempty.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    _run_alembic(backend_dir, database_url, "upgrade", "head")
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO chapter_generation_candidate_selection_operations "
            "(id, project_id, planning_chapter_id, requested_by, operation_key, "
            "request_fingerprint, previous_selection_version, previous_run_id, "
            "previous_candidate_id, previous_candidate_version_no, "
            "previous_candidate_checksum, previous_context_checksum, "
            "result_selection_version, result_run_id, result_candidate_id, "
            "result_candidate_version_no, result_candidate_checksum, "
            "result_context_checksum, created_at) VALUES "
            "(?, ?, ?, ?, ?, ?, 0, NULL, NULL, NULL, NULL, NULL, "
            "1, ?, ?, 1, ?, ?, CURRENT_TIMESTAMP)",
            (
                "o" * 32,
                "p" * 32,
                "h" * 32,
                "u" * 32,
                "candidate-selection-nonempty-v1",
                "1" * 64,
                "r" * 32,
                "c" * 32,
                "2" * 64,
                "3" * 64,
            ),
        )
        connection.commit()
    with pytest.raises(subprocess.CalledProcessError):
        _run_alembic(
            backend_dir,
            database_url,
            "downgrade",
            "a9d1e3f5c018",
        )
    with sqlite3.connect(database_path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM "
                "chapter_generation_candidate_selection_operations"
            ).fetchone()[0]
            == 1
        )
