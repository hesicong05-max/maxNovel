"""DEV-017B3a persistence contract tests for paid generation execution."""

import asyncio
from datetime import UTC, datetime
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import settings as app_settings
import app.core.generation_execution as generation_execution
from app.core.generation_execution import (
    GenerationExecutionError,
    SingleCallGenerationTransport,
    GenerationTransportResult,
    GenerationUsage,
    get_generation_transport,
)
from app.core.llm_client import LLMSingleCallError
from app.main import app
from app.models.generation import (
    ChapterGenerationAttempt,
    ChapterGenerationCandidate,
    ChapterGenerationRun,
)
from app.models.planning import NovelPlan, PlanningChapter, PlanningPart
from app.models.project import Project
from app.models.user import User
from app.schemas.generation import GenerationAttemptExecuteCommand
from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


TEST_CAPABILITY = {
    "schema_version": 1,
    "provider_name": "counting_fake",
    "model_name": "counting-fake-model",
    "max_output_tokens": 4096,
    "input_limit_availability": "unavailable",
    "max_input_tokens": None,
    "price_availability": "unavailable",
}
TEST_EXECUTION_CONFIG_DIGEST = "a" * 64
TEST_CAPABILITY_CHECKSUM = hashlib.sha256(
    json.dumps(
        {
            "capability": TEST_CAPABILITY,
            "execution_config_digest": TEST_EXECUTION_CONFIG_DIGEST,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
).hexdigest()


async def _seed_prepared_run(session, *, user=None):
    if user is None:
        user = User(
            id=_id(),
            email=f"{_id()}@example.test",
            username=f"user-{_id()}",
            hashed_password="not-used-in-persistence-test",
        )
    project = Project(
        id=_id(),
        title="B3a 持久化测试",
        genre="玄幻",
        owner_id=user.id,
        lore_storage_mode="relational",
    )
    plan = NovelPlan(
        id=_id(),
        project_id=project.id,
        status="active",
        structure_version=3,
        assignment_version=2,
    )
    part = PlanningPart(
        id=_id(),
        project_id=project.id,
        plan_id=plan.id,
        title="第一篇",
        description="",
        position=1,
        status="active",
        lock_version=1,
    )
    chapter = PlanningChapter(
        id=_id(),
        project_id=project.id,
        plan_id=plan.id,
        part_id=part.id,
        title="第一章",
        summary="角色进入星港。",
        target_word_count=1800,
        position=1,
        status="active",
        lock_version=1,
    )
    type_id = _id()
    element_id = _id()
    manifest = {
        "schema_version": 1,
        "project_id": project.id,
        "plan_id": plan.id,
        "versions": {"structure": 3, "assignment": 2, "chapter_lock": 1},
        "part": {
            "id": part.id,
            "title": part.title,
            "description": "",
            "position": 1,
            "lock_version": 1,
        },
        "chapter": {
            "id": chapter.id,
            "title": chapter.title,
            "summary": chapter.summary,
            "target_word_count": 1800,
            "position": 1,
            "lock_version": 1,
        },
        "elements": [
            {
                "element_id": element_id,
                "type": {
                    "id": type_id,
                    "key": "character",
                    "display_name": "角色",
                    "schema_revision": 1,
                },
                "version": {
                    "id": _id(),
                    "element_id": element_id,
                    "type_id": type_id,
                    "version_no": 1,
                    "name": "沈星",
                    "summary": "星港旅人。",
                    "payload": {},
                    "field_states": {},
                    "source_id": None,
                },
                "assignment_sources": [
                    {
                        "assignment_id": _id(),
                        "scope_type": "novel",
                        "scope_target_id": project.id,
                        "scope_title": "整部小说",
                        "assignment_lock_version": 1,
                        "assigned_at_content_version": 1,
                    }
                ],
            }
        ],
        "relations": [],
        "foreshadow_actions": {"supported": False, "items": []},
        "warnings": [],
        "counts": {"elements": 1, "relations": 0, "warnings": 0},
    }
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    run = ChapterGenerationRun(
        id=_id(),
        project_id=project.id,
        plan_id=plan.id,
        planning_chapter_id=chapter.id,
        requested_by=user.id,
        operation_key=f"prepare-{_id()}",
        request_fingerprint="1" * 64,
        status="prepared",
        execution_mode="preflight_only",
        ai_invoked=False,
        billing_effect="none",
        structure_version=3,
        assignment_version=2,
        chapter_lock_version=1,
        context_schema_version=1,
        context_manifest=manifest,
        context_checksum=hashlib.sha256(canonical).hexdigest(),
        context_size_bytes=len(canonical),
    )
    if user not in session:
        session.add(user)
    session.add(project)
    await session.flush()
    session.add(plan)
    await session.flush()
    session.add(part)
    await session.flush()
    session.add(chapter)
    await session.flush()
    session.add(run)
    await session.commit()
    return user, project, run


def _attempt(user, project, run, **overrides):
    values = {
        "id": _id(),
        "project_id": project.id,
        "run_id": run.id,
        "requested_by": user.id,
        "operation_key": f"execute-{_id()}",
        "request_fingerprint": "3" * 64,
        "status": "reserved",
        "execution_mode": "single_call",
        "billing_confirmed": True,
        "ai_invoked": False,
        "billing_effect": "none",
        "capability_schema_version": 1,
        "capability_snapshot": TEST_CAPABILITY,
        "capability_checksum": TEST_CAPABILITY_CHECKSUM,
        "execution_config_digest": TEST_EXECUTION_CONFIG_DIGEST,
        "provider_name": "counting_fake",
        "model_name": "counting-fake-model",
        "max_output_tokens": 4096,
        "input_limit_availability": "unavailable",
        "max_input_tokens": None,
        "price_availability": "unavailable",
        "prompt_schema_version": 1,
        "prompt_checksum": "4" * 64,
        "context_checksum": run.context_checksum,
        "usage_status": "unavailable",
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "lock_version": 1,
    }
    values.update(overrides)
    return ChapterGenerationAttempt(**values)


@pytest.mark.usefixtures("clean_db")
async def test_attempt_key_is_unique_and_cross_project_run_is_rejected():
    async with TestSessionLocal() as session:
        user_one, project_one, run_one = await _seed_prepared_run(session)
        user_two, project_two, run_two = await _seed_prepared_run(session)
        project_one_id = project_one.id
        user_one_id = user_one.id
        cross_project_attempt = _attempt(
            user_two,
            project_two,
            run_two,
            project_id=project_one_id,
            requested_by=user_one_id,
        )
        key = "execute-same-operation-key"
        session.add(_attempt(user_one, project_one, run_one, operation_key=key))
        await session.commit()

        session.add(_attempt(user_one, project_one, run_one, operation_key=key))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        session.add(cross_project_attempt)
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize(
    "invalid_state",
    [
        {"status": "reserved", "ai_invoked": True, "billing_effect": "possible"},
        {"status": "calling", "ai_invoked": True, "billing_effect": "possible"},
        {
            "status": "outcome_unknown",
            "ai_invoked": True,
            "billing_effect": "possible",
            "claimed_at": _now(),
            "completed_at": _now(),
        },
        {
            "status": "failed",
            "ai_invoked": True,
            "billing_effect": "possible",
            "completed_at": _now(),
            "error_code": "KNOWN_FAILURE",
        },
    ],
)
async def test_attempt_state_shape_fails_closed(invalid_state):
    async with TestSessionLocal() as session:
        user, project, run = await _seed_prepared_run(session)
        session.add(_attempt(user, project, run, **invalid_state))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.usefixtures("clean_db")
async def test_attempt_requires_full_request_fingerprint():
    async with TestSessionLocal() as session:
        user, project, run = await _seed_prepared_run(session)
        session.add(
            _attempt(user, project, run, request_fingerprint="too-short")
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.usefixtures("clean_db")
async def test_generated_and_manual_candidates_form_immutable_version_chain():
    async with TestSessionLocal() as session:
        user, project, run = await _seed_prepared_run(session)
        attempt = _attempt(user, project, run)
        session.add(attempt)
        await session.commit()

        content = "星港的门缓缓开启。"
        generated = ChapterGenerationCandidate(
            id=_id(),
            project_id=project.id,
            run_id=run.id,
            source_attempt_id=attempt.id,
            parent_candidate_id=None,
            version_no=1,
            origin_kind="generated",
            title="第一章",
            content=content,
            content_format="plain_text",
            content_checksum="5" * 64,
            content_size_bytes=len(content.encode("utf-8")),
            word_count=1,
            created_by=user.id,
        )
        session.add(generated)
        await session.commit()

        edited = ChapterGenerationCandidate(
            id=_id(),
            project_id=project.id,
            run_id=run.id,
            source_attempt_id=None,
            parent_candidate_id=generated.id,
            version_no=2,
            origin_kind="manual_edit",
            title="第一章（修订）",
            content=f"{content}\n角色走入星港。",
            content_format="plain_text",
            content_checksum="6" * 64,
            content_size_bytes=len(f"{content}\n角色走入星港。".encode("utf-8")),
            word_count=2,
            created_by=user.id,
        )
        session.add(edited)
        await session.commit()

        session.add(
            ChapterGenerationCandidate(
                id=_id(),
                project_id=project.id,
                run_id=run.id,
                source_attempt_id=attempt.id,
                parent_candidate_id=None,
                version_no=3,
                origin_kind="generated",
                title="重复结果",
                content="不应写入",
                content_format="plain_text",
                content_checksum="7" * 64,
                content_size_bytes=15,
                word_count=1,
                created_by=user.id,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


def test_execute_command_requires_explicit_confirmation_and_forbids_extra_fields():
    valid = {
        "operation_key": "execute-contract-0001",
        "expected_context_checksum": "a" * 64,
        "expected_capability_checksum": TEST_CAPABILITY_CHECKSUM,
        "confirm_model_call": True,
    }
    assert GenerationAttemptExecuteCommand.model_validate(valid).confirm_model_call is True

    with pytest.raises(ValidationError):
        GenerationAttemptExecuteCommand.model_validate(
            {**valid, "confirm_model_call": False}
        )
    with pytest.raises(ValidationError):
        GenerationAttemptExecuteCommand.model_validate({**valid, "unexpected": 1})


class CountingFakeTransport:
    model_name = "counting-fake-model"

    def __init__(
        self,
        *,
        result="星港的门缓缓开启。",
        error=None,
        blocking=False,
        usage=None,
    ):
        self.result = result
        self.error = error
        self.blocking = blocking
        self.usage = usage
        self.capability_snapshot = dict(TEST_CAPABILITY)
        self.capability_checksum = TEST_CAPABILITY_CHECKSUM
        self.execution_config_digest = TEST_EXECUTION_CONFIG_DIGEST
        self.capability_current = True
        self.call_count = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def ensure_ready(self):
        return None

    def verify_capability_current(self):
        if not self.capability_current:
            raise GenerationExecutionError(
                "LLM_CAPABILITY_CHANGED",
                "LLM 能力已变化，未发起本次调用。",
                recommended_action="refresh_generation_capability",
            )

    async def generate(self, messages):
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        self.call_count += 1
        self.started.set()
        if self.blocking:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        if self.usage is None:
            return self.result
        return GenerationTransportResult(content=self.result, usage=self.usage)


async def _authenticated_prepared_run(email="testuser@example.com"):
    async with TestSessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == email))
        assert user is not None
        return await _seed_prepared_run(session, user=user)


def _execute_path(project_id, run_id):
    return f"/api/projects/{project_id}/planning/generation-runs/{run_id}/attempts"


def _execute_body(run, operation_key="generation-execute-0001", **overrides):
    body = {
        "operation_key": operation_key,
        "expected_context_checksum": run.context_checksum,
        "expected_capability_checksum": TEST_CAPABILITY_CHECKSUM,
        "confirm_model_call": True,
    }
    body.update(overrides)
    return body


@pytest.mark.usefixtures("clean_db")
async def test_execute_same_key_replays_one_transport_call_and_one_candidate(
    client, auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport()
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        first = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run),
        )
        second = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run),
        )
        by_key = await client.get(
            f"/api/projects/{project.id}/planning/generation-attempts/by-key/"
            "generation-execute-0001",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert first.status_code == second.status_code == by_key.status_code == 200
    assert first.json()["status"] == "succeeded"
    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True
    assert by_key.json()["replayed"] is True
    assert first.json()["id"] == second.json()["id"] == by_key.json()["id"]
    assert first.json()["candidate_id"] == second.json()["candidate_id"]
    assert first.json()["capability"] == {
        **TEST_CAPABILITY,
        "capability_checksum": TEST_CAPABILITY_CHECKSUM,
    }
    assert first.json()["usage"] == {
        "status": "unavailable",
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }
    assert fake.call_count == 1
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationAttempt)
        ) == 1
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationCandidate)
        ) == 1


@pytest.mark.usefixtures("clean_db")
async def test_execute_reused_key_with_different_payload_is_409_without_recall(
    client, auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport()
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        first = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run),
        )
        conflict = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, expected_context_checksum="f" * 64),
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "GENERATION_OPERATION_KEY_REUSED"
    assert fake.call_count == 1


@pytest.mark.usefixtures("clean_db")
async def test_capability_endpoint_is_non_sensitive_and_execute_binds_checksum(
    client, auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport()
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        capability = await client.get(
            f"/api/projects/{project.id}/planning/generation-capabilities/current",
            headers=auth_headers,
        )
        mismatch = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(
                run,
                operation_key="generation-capability-mismatch",
                expected_capability_checksum="f" * 64,
            ),
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert capability.status_code == 200
    assert capability.json() == {
        **TEST_CAPABILITY,
        "capability_checksum": TEST_CAPABILITY_CHECKSUM,
    }
    assert "api_key" not in capability.text
    assert "base_url" not in capability.text
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "LLM_CAPABILITY_CHANGED"
    assert fake.call_count == 0
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationAttempt)
        ) == 0


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("api_key", "sk-second-secret"),
        ("base_url", "https://api.openai.com/tenant/v1"),
        ("temperature", 0.35),
        ("model", "gpt-new-model"),
        ("max_tokens", 8192),
    ],
)
@pytest.mark.usefixtures("clean_db")
async def test_each_frozen_execution_setting_changes_checksum_and_stops_execute(
    client,
    auth_headers,
    monkeypatch,
    changed_field,
    changed_value,
):
    initial = {
        "api_key": "sk-first-secret",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-test-model",
        "max_tokens": 4096,
        "temperature": 0.8,
    }
    current = {**initial, changed_field: changed_value}
    monkeypatch.setattr(generation_execution, "load_settings", lambda: initial)
    frozen = SingleCallGenerationTransport()
    monkeypatch.setattr(generation_execution, "load_settings", lambda: current)
    changed = SingleCallGenerationTransport()
    assert changed.capability_checksum != frozen.capability_checksum

    calls = 0

    async def forbidden_provider_call(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("provider must not be called after capability drift")

    monkeypatch.setattr(
        generation_execution.llm_client,
        "chat_once_frozen",
        forbidden_provider_call,
    )
    _, project, run = await _authenticated_prepared_run()
    app.dependency_overrides[get_generation_transport] = lambda: frozen
    try:
        response = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(
                run,
                operation_key=f"generation-drift-{changed_field}",
                expected_capability_checksum=frozen.capability_checksum,
            ),
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error"]["code"] == "LLM_CONFIGURATION_CHANGED"
    assert response.json()["ai_invoked"] is False
    assert calls == 0


def test_real_capability_response_uses_only_opaque_non_sensitive_fields(monkeypatch):
    secret = "sk-do-not-disclose-this-value"
    base_url = "https://api.openai.com/private/tenant/v1/"
    monkeypatch.setattr(
        generation_execution,
        "load_settings",
        lambda: {
            "api_key": secret,
            "base_url": base_url,
            "model": "gpt-test-model",
            "max_tokens": 4096,
            "temperature": 0.8,
        },
    )
    transport = SingleCallGenerationTransport()
    response = generation_execution.generation_capability_response(transport)
    serialized = json.dumps(response, ensure_ascii=False)

    assert response["input_limit_availability"] == "unavailable"
    assert response["max_input_tokens"] is None
    assert response["price_availability"] == "unavailable"
    assert secret not in serialized
    assert base_url not in serialized
    assert "execution_config_digest" not in serialized


@pytest.mark.usefixtures("clean_db")
async def test_capability_change_after_reservation_is_terminal_without_call(
    client, auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport()
    fake.capability_current = False
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        response = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-capability-drift"),
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error"]["code"] == "LLM_CAPABILITY_CHANGED"
    assert response.json()["ai_invoked"] is False
    assert response.json()["usage"]["status"] == "unavailable"
    assert fake.call_count == 0


@pytest.mark.usefixtures("clean_db")
async def test_same_key_with_different_capability_checksum_is_409(client, auth_headers):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport()
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        first = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-capability-key"),
        )
        conflict = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(
                run,
                operation_key="generation-capability-key",
                expected_capability_checksum="e" * 64,
            ),
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "GENERATION_OPERATION_KEY_REUSED"
    assert fake.call_count == 1


@pytest.mark.usefixtures("clean_db")
async def test_outcome_unknown_is_terminal_and_same_key_never_recalls(
    client, auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport(
        error=LLMSingleCallError(
            "LLM_OUTCOME_UNKNOWN",
            "LLM 请求超时，结果状态无法确认",
            outcome_unknown=True,
        )
    )
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        first = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run),
        )
        replay = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run),
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert first.status_code == replay.status_code == 200
    assert first.json()["status"] == replay.json()["status"] == "outcome_unknown"
    assert first.json()["error"]["recommended_action"] == "keep_unknown_result"
    assert first.json()["usage"] == {
        "status": "unknown",
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }
    assert replay.json()["replayed"] is True
    assert fake.call_count == 1
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationCandidate)
        ) == 0


@pytest.mark.usefixtures("clean_db")
async def test_reported_usage_is_persisted_without_fabrication(client, auth_headers):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport(
        usage=GenerationUsage(input_tokens=120, output_tokens=80, total_tokens=200)
    )
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        response = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-reported-usage"),
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert response.status_code == 200
    assert response.json()["usage"] == {
        "status": "reported",
        "input_tokens": 120,
        "output_tokens": 80,
        "total_tokens": 200,
    }
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationCandidate)
        ) == 1


@pytest.mark.usefixtures("clean_db")
async def test_attempt_to_candidate_detail_refresh_and_isolation(
    client, auth_headers, second_auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    _, other_project, _ = await _authenticated_prepared_run()
    fake = CountingFakeTransport()
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        attempt = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-candidate-refresh"),
        )
        candidate_id = attempt.json()["candidate_id"]
        recovered_attempt = await client.get(
            f"/api/projects/{project.id}/planning/generation-attempts/by-key/"
            "generation-candidate-refresh",
            headers=auth_headers,
        )
        candidate = await client.get(
            f"/api/projects/{project.id}/planning/generation-candidates/{candidate_id}",
            headers=auth_headers,
        )
        wrong_owner = await client.get(
            f"/api/projects/{project.id}/planning/generation-candidates/{candidate_id}",
            headers=second_auth_headers,
        )
        wrong_project = await client.get(
            f"/api/projects/{other_project.id}/planning/generation-candidates/{candidate_id}",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert attempt.status_code == recovered_attempt.status_code == candidate.status_code == 200
    assert recovered_attempt.json()["candidate_id"] == candidate_id
    assert candidate.json()["planning_chapter_id"] == run.planning_chapter_id
    assert candidate.json()["content"] == fake.result
    assert wrong_owner.status_code == 403
    assert wrong_project.status_code == 404
    assert wrong_project.json()["detail"]["code"] == "GENERATION_CANDIDATE_NOT_FOUND"


@pytest.mark.usefixtures("clean_db")
async def test_corrupt_candidate_and_capability_snapshot_fail_closed(
    client, auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport()
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        created = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-corrupt-receipt"),
        )
        candidate_id = created.json()["candidate_id"]
        async with TestSessionLocal() as session:
            candidate = await session.get(ChapterGenerationCandidate, candidate_id)
            assert candidate is not None
            candidate.content_size_bytes += 1
            await session.commit()
        corrupt_candidate = await client.get(
            f"/api/projects/{project.id}/planning/generation-candidates/{candidate_id}",
            headers=auth_headers,
        )
        async with TestSessionLocal() as session:
            attempt = await session.scalar(
                select(ChapterGenerationAttempt).where(
                    ChapterGenerationAttempt.operation_key
                    == "generation-corrupt-receipt"
                )
            )
            assert attempt is not None
            attempt.capability_snapshot = {"schema_version": 1, "corrupt": True}
            await session.commit()
        corrupt_attempt = await client.get(
            f"/api/projects/{project.id}/planning/generation-attempts/by-key/"
            "generation-corrupt-receipt",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert corrupt_candidate.status_code == 409
    assert corrupt_candidate.json()["detail"]["code"] == "GENERATION_ATTEMPT_CORRUPT"
    assert corrupt_attempt.status_code == 409
    assert corrupt_attempt.json()["detail"]["code"] == "GENERATION_ATTEMPT_CORRUPT"


@pytest.mark.usefixtures("clean_db")
async def test_calling_receipt_replays_without_second_transport_call(
    client, auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport(blocking=True)
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        first_task = asyncio.create_task(
            client.post(
                _execute_path(project.id, run.id),
                headers=auth_headers,
                json=_execute_body(run),
            )
        )
        await asyncio.wait_for(fake.started.wait(), timeout=5)
        replay = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run),
        )
        fake.release.set()
        first = await asyncio.wait_for(first_task, timeout=5)
    finally:
        fake.release.set()
        app.dependency_overrides.pop(get_generation_transport, None)

    assert replay.status_code == first.status_code == 200
    assert replay.json()["status"] == "calling"
    assert replay.json()["replayed"] is True
    assert first.json()["status"] == "succeeded"
    assert fake.call_count == 1


@pytest.mark.usefixtures("clean_db")
async def test_project_delete_is_blocked_before_file_archive_while_calling(
    client, auth_headers, monkeypatch
):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport(blocking=True)
    archive_calls = []

    def unexpected_archive(project_id):
        archive_calls.append(project_id)
        return None

    monkeypatch.setattr("app.api.projects.archive_project_files", unexpected_archive)
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        execute_task = asyncio.create_task(
            client.post(
                _execute_path(project.id, run.id),
                headers=auth_headers,
                json=_execute_body(run, operation_key="generation-delete-guard"),
            )
        )
        await asyncio.wait_for(fake.started.wait(), timeout=5)
        blocked = await client.delete(
            f"/api/projects/{project.id}", headers=auth_headers
        )
        assert archive_calls == []
        async with TestSessionLocal() as session:
            assert await session.get(Project, project.id) is not None
            assert await session.get(ChapterGenerationRun, run.id) is not None
            attempt = await session.scalar(
                select(ChapterGenerationAttempt).where(
                    ChapterGenerationAttempt.operation_key
                    == "generation-delete-guard"
                )
            )
            assert attempt is not None
            assert attempt.status == "calling"
        fake.release.set()
        completed = await asyncio.wait_for(execute_task, timeout=5)
        recovered = await client.get(
            f"/api/projects/{project.id}/planning/generation-attempts/by-key/"
            "generation-delete-guard",
            headers=auth_headers,
        )
    finally:
        fake.release.set()
        app.dependency_overrides.pop(get_generation_transport, None)

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "PROJECT_GENERATION_ACTIVE"
    assert completed.status_code == recovered.status_code == 200
    assert recovered.json()["status"] == "succeeded"
    assert recovered.json()["candidate_id"] is not None
    assert fake.call_count == 1


@pytest.mark.usefixtures("clean_db")
async def test_terminal_generation_allows_existing_project_delete_flow(
    client, auth_headers, monkeypatch
):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport()
    archive_marker = object()
    file_calls = []

    def fake_archive(project_id):
        file_calls.append(("archive", project_id))
        return archive_marker

    def fake_finalize(archive):
        file_calls.append(("finalize", archive))

    monkeypatch.setattr("app.api.projects.archive_project_files", fake_archive)
    monkeypatch.setattr(
        "app.api.projects.finalize_project_file_delete", fake_finalize
    )
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        completed = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-terminal-delete"),
        )
        deleted = await client.delete(
            f"/api/projects/{project.id}", headers=auth_headers
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded"
    assert deleted.status_code == 200
    assert file_calls == [
        ("archive", project.id),
        ("finalize", archive_marker),
    ]
    async with TestSessionLocal() as session:
        assert await session.get(Project, project.id) is None
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationRun)
        ) == 0
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationAttempt)
        ) == 0
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationCandidate)
        ) == 0


@pytest.mark.usefixtures("clean_db")
async def test_postgres_delete_lock_first_prevents_reservation_and_transport(
    client, auth_headers
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("requires PostgreSQL project row locks")
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport()
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        async with TestSessionLocal() as delete_session:
            locked_project = await delete_session.scalar(
                select(Project)
                .where(Project.id == project.id)
                .with_for_update()
            )
            assert locked_project is not None
            execute_task = asyncio.create_task(
                client.post(
                    _execute_path(project.id, run.id),
                    headers=auth_headers,
                    json=_execute_body(
                        run, operation_key="generation-delete-lock-first"
                    ),
                )
            )
            await asyncio.sleep(0.1)
            assert fake.call_count == 0
            await delete_session.delete(locked_project)
            await delete_session.commit()
        response = await asyncio.wait_for(execute_task, timeout=20)
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert response.status_code == 404
    assert fake.call_count == 0
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationAttempt)
        ) == 0


@pytest.mark.usefixtures("clean_db")
async def test_postgres_concurrent_same_key_claims_one_transport_call(
    client, auth_headers
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("requires PostgreSQL row locking and independent connections")
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport()
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        first, second = await asyncio.wait_for(
            asyncio.gather(
                client.post(
                    _execute_path(project.id, run.id),
                    headers=auth_headers,
                    json=_execute_body(run),
                ),
                client.post(
                    _execute_path(project.id, run.id),
                    headers=auth_headers,
                    json=_execute_body(run),
                ),
            ),
            timeout=20,
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert sorted([first.json()["replayed"], second.json()["replayed"]]) == [
        False,
        True,
    ]
    assert fake.call_count == 1
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationAttempt)
        ) == 1
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationCandidate)
        ) == 1


@pytest.mark.usefixtures("clean_db")
async def test_invalid_transport_response_fails_without_candidate_or_recall(
    client, auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport(result="   ")
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        first = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run),
        )
        replay = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run),
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert first.status_code == replay.status_code == 200
    assert first.json()["status"] == replay.json()["status"] == "failed"
    assert first.json()["error"]["code"] == "GENERATION_RESPONSE_INVALID"
    assert replay.json()["replayed"] is True
    assert fake.call_count == 1
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationCandidate)
        ) == 0


@pytest.mark.usefixtures("clean_db")
async def test_maintenance_blocks_new_reservation_but_allows_completed_replay(
    client, auth_headers, monkeypatch
):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport()
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        completed = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-before-maintenance"),
        )
        monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
        replay = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-before-maintenance"),
        )
        blocked = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-during-maintenance"),
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert completed.status_code == replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert blocked.status_code == 503
    assert blocked.json()["code"] == "PROJECT_WRITE_FROZEN"
    assert fake.call_count == 1
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationAttempt)
        ) == 1


@pytest.mark.usefixtures("clean_db")
async def test_by_key_is_isolated_by_owner_and_project(
    client, auth_headers, second_auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    _, other_project, _ = await _authenticated_prepared_run()
    fake = CountingFakeTransport()
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        created = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-isolation-key"),
        )
        wrong_owner = await client.get(
            f"/api/projects/{project.id}/planning/generation-attempts/by-key/"
            "generation-isolation-key",
            headers=second_auth_headers,
        )
        wrong_project = await client.get(
            f"/api/projects/{other_project.id}/planning/generation-attempts/by-key/"
            "generation-isolation-key",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert created.status_code == 200
    assert wrong_owner.status_code == 403
    assert wrong_project.status_code == 404
    assert wrong_project.json()["detail"]["code"] == "GENERATION_ATTEMPT_NOT_FOUND"
    assert fake.call_count == 1


@pytest.mark.usefixtures("clean_db")
async def test_corrupt_preflight_fails_before_reservation_or_transport(
    client, auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    async with TestSessionLocal() as session:
        stored = await session.get(ChapterGenerationRun, run.id)
        assert stored is not None
        stored.context_manifest = {"schema_version": 1, "corrupt": True}
        await session.commit()
    fake = CountingFakeTransport()
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        response = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-corrupt-run"),
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "GENERATION_RUN_CORRUPT"
    assert fake.call_count == 0
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationAttempt)
        ) == 0


@pytest.mark.usefixtures("clean_db")
async def test_success_persistence_failure_never_exposes_succeeded(
    client, auth_headers, monkeypatch
):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport()

    async def fail_persistence(*_args, **_kwargs):
        raise RuntimeError("injected persistence failure")

    monkeypatch.setattr(generation_execution, "_persist_success", fail_persistence)
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        response = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-save-failure"),
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert response.status_code == 200
    assert response.json()["status"] == "outcome_unknown"
    assert response.json()["candidate_id"] is None
    assert fake.call_count == 1
    async with TestSessionLocal() as session:
        statuses = list(
            (
                await session.scalars(
                    select(ChapterGenerationAttempt.status)
                )
            ).all()
        )
        assert statuses == ["outcome_unknown"]
        assert await session.scalar(
            select(func.count()).select_from(ChapterGenerationCandidate)
        ) == 0


@pytest.mark.usefixtures("clean_db")
async def test_execute_api_forbids_unknown_fields_before_transport(
    client, auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport()
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        response = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json={**_execute_body(run), "unexpected": True},
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert response.status_code == 422
    assert fake.call_count == 0


def _run_alembic(backend_dir, database_url, *args):
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": database_url,
            "DEBUG": "true",
            "JWT_SECRET": "generation-execution-migration-test-secret",
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


def _tables(database_path):
    with sqlite3.connect(database_path) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }


def _attempt_columns(database_path):
    with sqlite3.connect(database_path) as connection:
        return {
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("chapter_generation_attempts")'
            )
        }


def test_generation_execution_migration_round_trip(tmp_path):
    backend_dir = os.path.dirname(os.path.dirname(__file__))
    database_path = tmp_path / "generation-execution.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"

    _run_alembic(backend_dir, database_url, "upgrade", "head")
    assert {
        "chapter_generation_runs",
        "chapter_generation_attempts",
        "chapter_generation_candidates",
    }.issubset(_tables(database_path))
    assert {
        "capability_schema_version",
        "capability_snapshot",
        "capability_checksum",
        "execution_config_digest",
        "provider_name",
        "model_name",
        "max_output_tokens",
        "input_limit_availability",
        "max_input_tokens",
        "price_availability",
        "usage_status",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    }.issubset(_attempt_columns(database_path))

    _run_alembic(backend_dir, database_url, "downgrade", "e7b9d1f3a016")
    tables = _tables(database_path)
    assert "chapter_generation_runs" in tables
    assert "chapter_generation_attempts" not in tables
    assert "chapter_generation_candidates" not in tables

    _run_alembic(backend_dir, database_url, "upgrade", "head")
    assert {
        "chapter_generation_attempts",
        "chapter_generation_candidates",
    }.issubset(_tables(database_path))
    assert "capability_checksum" in _attempt_columns(database_path)
