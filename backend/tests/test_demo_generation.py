"""DEMO-001b1 zero-LLM execution, provenance, and recovery tests."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.api import projects as projects_api
from app.config import settings
from app.core import demo_fixture
from app.core import demo_generation
from app.core.demo_generation import get_technical_demo_adapter
from app.main import app
from app.models.foreshadow import ForeshadowFact
from app.models.generation import (
    ChapterGenerationAttempt,
    ChapterGenerationCandidate,
    ChapterGenerationRun,
    ChapterTechnicalDemoExecution,
)
from app.models.project import Chapter, Project
from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal

pytestmark = pytest.mark.usefixtures("clean_db")
_BOOTSTRAP = {"fixture_version": 1, "operation_key": "demo:v1:bootstrap"}


class CountingTechnicalAdapter:
    adapter_schema_version = 1
    content_spec_version = 1

    def __init__(self, *, fail: bool = False):
        self.call_count = 0
        self.fail = fail

    def render(self, manifest):
        self.call_count += 1
        if self.fail:
            raise RuntimeError("injected fixed-adapter failure")
        assert manifest["counts"] == {"elements": 7, "relations": 3, "warnings": 0}
        return "雾潮里，沈星在《星港》记录航标。《无名星门》只是待核对名称，并非已确认设定。"


@pytest.fixture(autouse=True)
def _enable_demo_gate(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "test")
    monkeypatch.setattr(settings, "DEMO_FIXTURE_ENABLED", True)
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(demo_fixture, "load_settings", lambda: {"api_key": ""})
    if TEST_DATABASE_BACKEND == "postgresql":
        # Production/demo mode remains SQLite-only. The PostgreSQL CI job uses
        # an ephemeral localhost service, so only this test's environment
        # probe is replaced; all application queries still hit real PG 16.4.
        monkeypatch.setattr(
            demo_fixture,
            "_active_database_url",
            lambda _db: "sqlite+aiosqlite:///:memory:",
        )


async def _count(model) -> int:
    async with TestSessionLocal() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _prepare(client, headers):
    fixture = await client.post(
        "/api/demo/v1/bootstrap", headers=headers, json=_BOOTSTRAP
    )
    assert fixture.status_code == 200, fixture.text
    ids = fixture.json()
    response = await client.post(
        f'/api/projects/{ids["project_id"]}/planning/chapters/'
        f'{ids["chapter_id"]}/generation-runs',
        headers=headers,
        json={
            "operation_key": "demo-preflight-v1",
            "expected_structure_version": 1,
            "expected_assignment_version": 1,
            "expected_chapter_lock_version": 1,
        },
    )
    assert response.status_code == 200, response.text
    return ids, response.json()


async def _execute_once(client, headers, ids, run, operation_key):
    capability = (
        await client.get(
            _capability_path(ids["project_id"], run["id"]), headers=headers
        )
    ).json()
    response = await client.post(
        _execute_path(ids["project_id"], run["id"]),
        headers=headers,
        json={
            "operation_key": operation_key,
            "expected_context_checksum": run["context_checksum"],
            "expected_capability_checksum": capability["capability_checksum"],
            "fixture_version": 1,
            "confirm_technical_demo": True,
        },
    )
    assert response.status_code == 200, response.text
    return response.json(), capability


def _capability_path(project_id, run_id):
    return (
        f"/api/demo/v1/projects/{project_id}/planning/generation-runs/{run_id}/"
        "technical-generation-capability"
    )


def _execute_path(project_id, run_id):
    return (
        f"/api/demo/v1/projects/{project_id}/planning/generation-runs/{run_id}/"
        "technical-demo-executions"
    )


async def test_technical_demo_same_key_replays_one_adapter_and_auditable_candidate(
    client, auth_headers
):
    ids, run = await _prepare(client, auth_headers)
    adapter = CountingTechnicalAdapter()
    app.dependency_overrides[get_technical_demo_adapter] = lambda: adapter
    try:
        capability = await client.get(
            _capability_path(ids["project_id"], run["id"]), headers=auth_headers
        )
        assert capability.status_code == 200, capability.text
        capability_body = capability.json()
        assert capability_body["ai_invoked"] is False
        assert capability_body["billing_effect"] == "none"
        assert capability_body["usage_status"] == "not_applicable"
        assert (
            not {
                "provider",
                "provider_name",
                "model",
                "model_name",
                "api_key",
                "base_url",
                "max_output_tokens",
            }
            & capability_body.keys()
        )
        payload = {
            "operation_key": "technical-demo-once-v1",
            "expected_context_checksum": run["context_checksum"],
            "expected_capability_checksum": capability_body["capability_checksum"],
            "fixture_version": 1,
            "confirm_technical_demo": True,
        }
        first = await client.post(
            _execute_path(ids["project_id"], run["id"]),
            headers=auth_headers,
            json=payload,
        )
        assert first.status_code == 200, first.text
        assert first.json()["replayed"] is False
        assert first.json()["ai_invoked"] is False
        assert first.json()["billing_effect"] == "none"
        assert first.json()["usage_status"] == "not_applicable"

        replay = await client.post(
            _execute_path(ids["project_id"], run["id"]),
            headers=auth_headers,
            json=payload,
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert replay.json()["execution_id"] == first.json()["execution_id"]
        assert replay.json()["candidate_id"] == first.json()["candidate_id"]
        assert adapter.call_count == 1

        by_key = await client.get(
            f'/api/demo/v1/projects/{ids["project_id"]}/planning/'
            f'technical-demo-executions/by-key/{payload["operation_key"]}',
            headers=auth_headers,
        )
        assert by_key.status_code == 200, by_key.text
        assert by_key.json()["candidate_id"] == first.json()["candidate_id"]

        candidate = await client.get(
            f'/api/demo/v1/projects/{ids["project_id"]}/planning/'
            f'technical-demo-candidates/{first.json()["candidate_id"]}',
            headers=auth_headers,
        )
        assert candidate.status_code == 200, candidate.text
        assert candidate.json()["origin_kind"] == "technical_demo"
        assert candidate.json()["ai_invoked"] is False

        audit = await client.get(
            f'/api/projects/{ids["project_id"]}/planning/generation-candidates/'
            f'{first.json()["candidate_id"]}/audit',
            headers=auth_headers,
        )
        assert audit.status_code == 200, audit.text
        assert audit.json()["status"] == "review"
        assert {
            item["term"]
            for item in audit.json()["unrecognized_explicit_terms"]["items"]
        } == {"无名星门"}
    finally:
        app.dependency_overrides.pop(get_technical_demo_adapter, None)

    assert await _count(ChapterTechnicalDemoExecution) == 1
    assert await _count(ChapterGenerationCandidate) == 1
    assert await _count(ChapterGenerationAttempt) == 0
    assert await _count(ForeshadowFact) == 0
    assert await _count(Chapter) == 0
    current = await client.get("/api/demo/v1/fixture", headers=auth_headers)
    assert current.status_code == 200
    assert current.json()["state"] == "ready"


async def test_same_key_different_payload_is_409_without_second_adapter_call(
    client, auth_headers
):
    ids, run = await _prepare(client, auth_headers)
    adapter = CountingTechnicalAdapter()
    app.dependency_overrides[get_technical_demo_adapter] = lambda: adapter
    try:
        capability = (
            await client.get(
                _capability_path(ids["project_id"], run["id"]),
                headers=auth_headers,
            )
        ).json()
        payload = {
            "operation_key": "technical-demo-conflict-v1",
            "expected_context_checksum": run["context_checksum"],
            "expected_capability_checksum": capability["capability_checksum"],
            "fixture_version": 1,
            "confirm_technical_demo": True,
        }
        first = await client.post(
            _execute_path(ids["project_id"], run["id"]),
            headers=auth_headers,
            json=payload,
        )
        assert first.status_code == 200
        conflict = await client.post(
            _execute_path(ids["project_id"], run["id"]),
            headers=auth_headers,
            json={**payload, "expected_context_checksum": "a" * 64},
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "TECHNICAL_DEMO_OPERATION_CONFLICT"
        assert adapter.call_count == 1
    finally:
        app.dependency_overrides.pop(get_technical_demo_adapter, None)


async def test_adapter_exception_rolls_back_execution_and_candidate(
    client, auth_headers
):
    ids, run = await _prepare(client, auth_headers)
    adapter = CountingTechnicalAdapter(fail=True)
    app.dependency_overrides[get_technical_demo_adapter] = lambda: adapter
    try:
        capability = (
            await client.get(
                _capability_path(ids["project_id"], run["id"]),
                headers=auth_headers,
            )
        ).json()
        response = await client.post(
            _execute_path(ids["project_id"], run["id"]),
            headers=auth_headers,
            json={
                "operation_key": "technical-demo-failure-v1",
                "expected_context_checksum": run["context_checksum"],
                "expected_capability_checksum": capability["capability_checksum"],
                "fixture_version": 1,
                "confirm_technical_demo": True,
            },
        )
        assert response.status_code == 503
        assert response.json()["detail"] == {
            "code": "TECHNICAL_DEMO_ADAPTER_UNAVAILABLE",
            "message": "固定技术模拟内容暂时无法生成，本次未保存执行或候选。",
            "retryable": True,
            "recommended_action": "start_new_confirmed_technical_demo",
        }
    finally:
        app.dependency_overrides.pop(get_technical_demo_adapter, None)
    assert adapter.call_count == 1
    assert await _count(ChapterTechnicalDemoExecution) == 0
    assert await _count(ChapterGenerationCandidate) == 0


async def test_disabled_gate_and_unknown_request_field_fail_closed(
    client, auth_headers, monkeypatch
):
    ids, run = await _prepare(client, auth_headers)
    adapter = CountingTechnicalAdapter()
    app.dependency_overrides[get_technical_demo_adapter] = lambda: adapter
    try:
        capability = (
            await client.get(
                _capability_path(ids["project_id"], run["id"]),
                headers=auth_headers,
            )
        ).json()
        response = await client.post(
            _execute_path(ids["project_id"], run["id"]),
            headers=auth_headers,
            json={
                "operation_key": "technical-demo-extra-field-v1",
                "expected_context_checksum": run["context_checksum"],
                "expected_capability_checksum": capability["capability_checksum"],
                "fixture_version": 1,
                "confirm_technical_demo": True,
                "failure_mode": "outcome_unknown",
            },
        )
        assert response.status_code == 422
        assert adapter.call_count == 0
        monkeypatch.setattr(settings, "DEMO_FIXTURE_ENABLED", False)
        hidden = await client.get(
            _capability_path(ids["project_id"], run["id"]), headers=auth_headers
        )
        assert hidden.status_code == 404
    finally:
        app.dependency_overrides.pop(get_technical_demo_adapter, None)


async def test_non_fixture_project_and_corrupt_candidate_fail_closed(
    client, auth_headers
):
    ids, run = await _prepare(client, auth_headers)
    wrong_project = await client.get(
        _capability_path("f" * 32, run["id"]), headers=auth_headers
    )
    assert wrong_project.status_code == 404

    adapter = CountingTechnicalAdapter()
    app.dependency_overrides[get_technical_demo_adapter] = lambda: adapter
    try:
        capability = (
            await client.get(
                _capability_path(ids["project_id"], run["id"]),
                headers=auth_headers,
            )
        ).json()
        created = await client.post(
            _execute_path(ids["project_id"], run["id"]),
            headers=auth_headers,
            json={
                "operation_key": "technical-demo-corrupt-candidate-v1",
                "expected_context_checksum": run["context_checksum"],
                "expected_capability_checksum": capability["capability_checksum"],
                "fixture_version": 1,
                "confirm_technical_demo": True,
            },
        )
        assert created.status_code == 200
    finally:
        app.dependency_overrides.pop(get_technical_demo_adapter, None)
    async with TestSessionLocal() as session:
        await session.execute(
            update(ChapterGenerationCandidate)
            .where(ChapterGenerationCandidate.id == created.json()["candidate_id"])
            .values(content_checksum="0" * 64)
        )
        await session.commit()
    candidate = await client.get(
        f'/api/demo/v1/projects/{ids["project_id"]}/planning/'
        f'technical-demo-candidates/{created.json()["candidate_id"]}',
        headers=auth_headers,
    )
    audit = await client.get(
        f'/api/projects/{ids["project_id"]}/planning/generation-candidates/'
        f'{created.json()["candidate_id"]}/audit',
        headers=auth_headers,
    )
    assert candidate.status_code == audit.status_code == 409


@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("execution", "capability_checksum", "f" * 64),
        ("execution", "request_fingerprint", "e" * 64),
        ("candidate", "content_checksum", "d" * 64),
        ("candidate", "content_size_bytes", 2),
        ("candidate", "word_count", 2),
        ("candidate", "title", "被篡改的章节标题"),
        ("run", "context_manifest", {}),
    ],
)
async def test_corrupt_technical_records_are_409_across_read_routes(
    client, auth_headers, target, field, value
):
    ids, run = await _prepare(client, auth_headers)
    created, _ = await _execute_once(
        client, auth_headers, ids, run, f"technical-demo-corrupt-{target}-{field}"
    )
    async with TestSessionLocal() as session:
        model_and_id = {
            "execution": (ChapterTechnicalDemoExecution, created["execution_id"]),
            "candidate": (ChapterGenerationCandidate, created["candidate_id"]),
            "run": (ChapterGenerationRun, run["id"]),
        }
        model, row_id = model_and_id[target]
        row = await session.get(model, row_id)
        setattr(row, field, value)
        await session.commit()

    by_key = await client.get(
        f'/api/demo/v1/projects/{ids["project_id"]}/planning/'
        f'technical-demo-executions/by-key/{created["operation_key"]}',
        headers=auth_headers,
    )
    candidate = await client.get(
        f'/api/demo/v1/projects/{ids["project_id"]}/planning/'
        f'technical-demo-candidates/{created["candidate_id"]}',
        headers=auth_headers,
    )
    audit = await client.get(
        f'/api/projects/{ids["project_id"]}/planning/generation-candidates/'
        f'{created["candidate_id"]}/audit',
        headers=auth_headers,
    )
    assert by_key.status_code == candidate.status_code == audit.status_code == 409


async def test_fixed_content_change_invalidates_old_confirmation_without_adapter_call(
    client, auth_headers, monkeypatch
):
    ids, run = await _prepare(client, auth_headers)
    capability = (
        await client.get(
            _capability_path(ids["project_id"], run["id"]), headers=auth_headers
        )
    ).json()
    monkeypatch.setattr(
        demo_generation,
        "TECHNICAL_DEMO_CONTENT",
        demo_generation.TECHNICAL_DEMO_CONTENT + "\n修订。",
    )
    adapter = CountingTechnicalAdapter()
    app.dependency_overrides[get_technical_demo_adapter] = lambda: adapter
    try:
        response = await client.post(
            _execute_path(ids["project_id"], run["id"]),
            headers=auth_headers,
            json={
                "operation_key": "technical-demo-stale-fixed-content-v1",
                "expected_context_checksum": run["context_checksum"],
                "expected_capability_checksum": capability["capability_checksum"],
                "fixture_version": 1,
                "confirm_technical_demo": True,
            },
        )
    finally:
        app.dependency_overrides.pop(get_technical_demo_adapter, None)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TECHNICAL_DEMO_CONFIRMATION_STALE"
    assert adapter.call_count == 0
    assert await _count(ChapterTechnicalDemoExecution) == 0
    assert await _count(ChapterGenerationCandidate) == 0


async def test_database_rejects_non_v1_technical_execution_versions(
    client, auth_headers
):
    ids, run = await _prepare(client, auth_headers)
    created, _ = await _execute_once(
        client, auth_headers, ids, run, "technical-demo-version-constraint-v1"
    )
    async with TestSessionLocal() as session:
        execution = await session.get(
            ChapterTechnicalDemoExecution, created["execution_id"]
        )
        execution.adapter_schema_version = 2
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_second_user_cannot_read_technical_demo_identity(
    client, auth_headers, second_auth_headers
):
    ids, run = await _prepare(client, auth_headers)
    created, _ = await _execute_once(
        client, auth_headers, ids, run, "technical-demo-owner-isolation-v1"
    )
    responses = [
        await client.get(
            _capability_path(ids["project_id"], run["id"]),
            headers=second_auth_headers,
        ),
        await client.get(
            f'/api/demo/v1/projects/{ids["project_id"]}/planning/'
            f'technical-demo-executions/by-key/{created["operation_key"]}',
            headers=second_auth_headers,
        ),
        await client.get(
            f'/api/demo/v1/projects/{ids["project_id"]}/planning/'
            f'technical-demo-candidates/{created["candidate_id"]}',
            headers=second_auth_headers,
        ),
        await client.get(
            f'/api/projects/{ids["project_id"]}/planning/generation-candidates/'
            f'{created["candidate_id"]}/audit',
            headers=second_auth_headers,
        ),
    ]
    assert [response.status_code for response in responses[:3]] == [404, 404, 404]
    assert responses[3].status_code in {403, 404}
    serialized = " ".join(response.text for response in responses)
    assert created["execution_id"] not in serialized
    assert created["candidate_id"] not in serialized


async def test_formal_project_delete_removes_complete_fixture_only(
    client, auth_headers, second_auth_headers, monkeypatch
):
    ids, run = await _prepare(client, auth_headers)
    created, _capability = await _execute_once(
        client, auth_headers, ids, run, "technical-demo-formal-delete-v1"
    )
    second_fixture = await client.post(
        "/api/demo/v1/bootstrap",
        headers=second_auth_headers,
        json=_BOOTSTRAP,
    )
    assert second_fixture.status_code == 200
    monkeypatch.setattr(projects_api, "archive_project_files", lambda _id: None)
    monkeypatch.setattr(
        projects_api, "finalize_project_file_delete", lambda _archive: None
    )

    deleted = await client.delete(
        f'/api/projects/{ids["project_id"]}', headers=auth_headers
    )

    assert deleted.status_code == 200
    assert deleted.json() == {"message": "项目已删除"}
    assert (
        await client.get(f'/api/projects/{ids["project_id"]}', headers=auth_headers)
    ).status_code == 404
    assert (
        await client.get(
            f'/api/projects/{second_fixture.json()["project_id"]}',
            headers=second_auth_headers,
        )
    ).status_code == 200
    assert await _count(Project) == 1
    assert await _count(ChapterTechnicalDemoExecution) == 0
    assert await _count(ChapterGenerationCandidate) == 0
    assert created["execution_id"] not in deleted.text


async def test_formal_project_delete_cleanup_failure_rolls_back_complete_fixture(
    client, auth_headers, monkeypatch
):
    ids, run = await _prepare(client, auth_headers)
    created, _capability = await _execute_once(
        client, auth_headers, ids, run, "technical-demo-delete-rollback-v1"
    )
    real_cleanup = projects_api.delete_project_relational_dependents

    async def fail_after_cleanup(db, project_id):
        await real_cleanup(db, project_id)
        raise RuntimeError("injected project dependent cleanup failure")

    monkeypatch.setattr(
        projects_api, "delete_project_relational_dependents", fail_after_cleanup
    )
    monkeypatch.setattr(projects_api, "archive_project_files", lambda _id: None)

    deleted = await client.delete(
        f'/api/projects/{ids["project_id"]}', headers=auth_headers
    )

    assert deleted.status_code == 500
    assert deleted.json()["detail"] == "删除未完成，项目仍保留，请重试"
    assert (
        await client.get(f'/api/projects/{ids["project_id"]}', headers=auth_headers)
    ).status_code == 200
    assert (
        await client.get(
            f'/api/demo/v1/projects/{ids["project_id"]}/planning/'
            f'technical-demo-executions/by-key/{created["operation_key"]}',
            headers=auth_headers,
        )
    ).status_code == 200
    assert (await client.get("/api/demo/v1/fixture", headers=auth_headers)).json()[
        "state"
    ] == "ready"


async def test_postgres_concurrent_same_key_runs_fixed_adapter_once(
    client, auth_headers
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("requires PostgreSQL row locks and independent connections")
    ids, run = await _prepare(client, auth_headers)
    adapter = CountingTechnicalAdapter()
    app.dependency_overrides[get_technical_demo_adapter] = lambda: adapter
    try:
        capability = (
            await client.get(
                _capability_path(ids["project_id"], run["id"]),
                headers=auth_headers,
            )
        ).json()
        payload = {
            "operation_key": "technical-demo-concurrent-v1",
            "expected_context_checksum": run["context_checksum"],
            "expected_capability_checksum": capability["capability_checksum"],
            "fixture_version": 1,
            "confirm_technical_demo": True,
        }
        first, second = await asyncio.wait_for(
            asyncio.gather(
                client.post(
                    _execute_path(ids["project_id"], run["id"]),
                    headers=auth_headers,
                    json=payload,
                ),
                client.post(
                    _execute_path(ids["project_id"], run["id"]),
                    headers=auth_headers,
                    json=payload,
                ),
            ),
            timeout=20,
        )
    finally:
        app.dependency_overrides.pop(get_technical_demo_adapter, None)
    assert first.status_code == second.status_code == 200
    assert first.json()["execution_id"] == second.json()["execution_id"]
    assert sorted((first.json()["replayed"], second.json()["replayed"])) == [
        False,
        True,
    ]
    assert adapter.call_count == 1
    assert await _count(ChapterTechnicalDemoExecution) == 1
    assert await _count(ChapterGenerationCandidate) == 1


async def test_postgres_delete_lock_first_prevents_technical_execution(
    client, auth_headers, monkeypatch
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("requires PostgreSQL project row locks")
    ids, run = await _prepare(client, auth_headers)
    adapter = CountingTechnicalAdapter()
    real_cleanup = projects_api.delete_project_relational_dependents
    lock_acquired = asyncio.Event()
    release_delete = asyncio.Event()
    delete_task = None
    execute_task = None

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
    app.dependency_overrides[get_technical_demo_adapter] = lambda: adapter
    try:
        capability = (
            await client.get(
                _capability_path(ids["project_id"], run["id"]),
                headers=auth_headers,
            )
        ).json()
        delete_task = asyncio.create_task(
            client.delete(
                f'/api/projects/{ids["project_id"]}', headers=auth_headers
            )
        )
        await asyncio.wait_for(lock_acquired.wait(), timeout=10)
        execute_task = asyncio.create_task(
            client.post(
                _execute_path(ids["project_id"], run["id"]),
                headers=auth_headers,
                json={
                    "operation_key": "technical-demo-delete-first-v1",
                    "expected_context_checksum": run["context_checksum"],
                    "expected_capability_checksum": capability[
                        "capability_checksum"
                    ],
                    "fixture_version": 1,
                    "confirm_technical_demo": True,
                },
            )
        )
        await asyncio.sleep(0.1)
        assert adapter.call_count == 0
        release_delete.set()
        deleted, response = await asyncio.wait_for(
            asyncio.gather(delete_task, execute_task), timeout=20
        )
    finally:
        release_delete.set()
        pending_tasks = [
            task for task in (delete_task, execute_task) if task and not task.done()
        ]
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        app.dependency_overrides.pop(get_technical_demo_adapter, None)
    assert deleted.status_code == 200
    assert response.status_code == 404
    assert adapter.call_count == 0
    assert await _count(Project) == 0
    assert await _count(ChapterTechnicalDemoExecution) == 0
    assert await _count(ChapterGenerationCandidate) == 0


def _run_alembic(backend_dir, database_url, *args):
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": database_url,
            "DEBUG": "true",
            "JWT_SECRET": "technical-demo-migration-test-secret",
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


def test_technical_demo_migration_empty_round_trip(tmp_path):
    backend_dir = os.path.dirname(os.path.dirname(__file__))
    database_path = tmp_path / "technical-demo.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    _run_alembic(backend_dir, database_url, "upgrade", "head")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        candidate_columns = {
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("chapter_generation_candidates")'
            )
        }
    assert "chapter_technical_demo_executions" in tables
    assert "source_technical_demo_execution_id" in candidate_columns
    _run_alembic(backend_dir, database_url, "downgrade", "f8c0e2a4b017")
    _run_alembic(backend_dir, database_url, "upgrade", "head")


def test_technical_demo_migration_refuses_nonempty_downgrade(tmp_path):
    backend_dir = os.path.dirname(os.path.dirname(__file__))
    database_path = tmp_path / "technical-demo-nonempty.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    _run_alembic(backend_dir, database_url, "upgrade", "head")
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO chapter_technical_demo_executions "
            "(id, project_id, run_id, requested_by, operation_key, "
            "request_fingerprint, status, execution_mode, ai_invoked, "
            "billing_effect, usage_status, fixture_version, "
            "adapter_schema_version, content_spec_version, context_checksum, "
            "capability_checksum, created_at, completed_at) VALUES "
            "(?, ?, ?, ?, ?, ?, 'succeeded', 'technical_demo', 0, 'none', "
            "'not_applicable', 1, 1, 1, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (
                "e" * 32,
                "p" * 32,
                "r" * 32,
                "u" * 32,
                "technical-demo-nonempty-v1",
                "1" * 64,
                "2" * 64,
                "3" * 64,
            ),
        )
        connection.commit()
    with pytest.raises(subprocess.CalledProcessError):
        _run_alembic(backend_dir, database_url, "downgrade", "f8c0e2a4b017")
    with sqlite3.connect(database_path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM chapter_technical_demo_executions"
            ).fetchone()[0]
            == 1
        )
