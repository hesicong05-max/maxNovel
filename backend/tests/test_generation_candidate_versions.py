"""Immutable generation candidate version workspace tests."""

import asyncio
import hashlib
import json
import re
import uuid

import pytest
from sqlalchemy import func, select, update

from app.api import projects as projects_api
from app.config import settings as app_settings
from app.core import generation_candidates
from app.core.generation_execution import get_generation_transport
from app.core.generation_candidates import (
    manual_edit_candidate_id,
    manual_edit_request_fingerprint,
)
from app.config import settings
from app.core import demo_fixture
from app.main import app
from app.models.generation import (
    ChapterGenerationAttempt,
    ChapterGenerationCandidate,
    ChapterTechnicalDemoExecution,
)
from app.models.foreshadow import ForeshadowFact
from app.models.lore import SettingElement
from app.models.project import Chapter, Project, StoryMemory
from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal
from tests.test_generation_execution import (
    CountingFakeTransport,
    _authenticated_prepared_run,
    _execute_body,
    _execute_path,
)
from tests.test_demo_generation import _execute_once as _execute_technical_once
from tests.test_demo_generation import _prepare as _prepare_demo


_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]")


def _candidate_path(project_id: str, run_id: str, candidate_id: str) -> str:
    return (
        f"/api/projects/{project_id}/planning/generation-runs/{run_id}/"
        f"candidate-versions/{candidate_id}"
    )


def _candidate_list_path(project_id: str, run_id: str) -> str:
    return (
        f"/api/projects/{project_id}/planning/generation-runs/{run_id}/"
        "candidate-versions"
    )


def _manual_edit_path(project_id: str, run_id: str) -> str:
    return (
        f"/api/projects/{project_id}/planning/generation-runs/{run_id}/"
        "candidate-manual-edits"
    )


def _manual_edit_body(root, run, *, operation_key: str, content: str) -> dict:
    return {
        "operation_key": operation_key,
        "parent_candidate_id": root.id,
        "expected_parent_version_no": root.version_no,
        "expected_parent_checksum": root.content_checksum,
        "expected_context_checksum": run.context_checksum,
        "content": content,
    }


async def _create_generated_candidate(client, auth_headers):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport()
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        response = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key=f"version-root-{uuid.uuid4().hex}"),
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)
    assert response.status_code == 200
    return project, run, response.json()["candidate_id"]


async def _insert_manual_candidate(
    *,
    project_id: str,
    run_id: str,
    parent_id: str,
    user_id: str,
    version_no: int,
    content: str,
) -> ChapterGenerationCandidate:
    content_bytes = content.encode("utf-8")
    candidate = ChapterGenerationCandidate(
        id=uuid.uuid4().hex,
        project_id=project_id,
        run_id=run_id,
        source_attempt_id=None,
        source_technical_demo_execution_id=None,
        parent_candidate_id=parent_id,
        version_no=version_no,
        origin_kind="manual_edit",
        title="第一章",
        content=content,
        content_format="plain_text",
        content_checksum=hashlib.sha256(content_bytes).hexdigest(),
        content_size_bytes=len(content_bytes),
        word_count=len(_WORD_PATTERN.findall(content)),
        created_by=user_id,
    )
    async with TestSessionLocal() as session:
        session.add(candidate)
        await session.commit()
    return candidate


@pytest.mark.usefixtures("clean_db")
async def test_generated_and_manual_versions_have_strict_list_detail_and_audit(
    client, auth_headers
):
    project, run, root_id = await _create_generated_candidate(client, auth_headers)
    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, root_id)
        assert root is not None
        user_id = root.created_by
    edited = await _insert_manual_candidate(
        project_id=project.id,
        run_id=run.id,
        parent_id=root_id,
        user_id=user_id,
        version_no=2,
        content="星港的门缓缓开启，沈星走入光里。",
    )

    listing = await client.get(
        _candidate_list_path(project.id, run.id), headers=auth_headers
    )
    detail = await client.get(
        _candidate_path(project.id, run.id, edited.id), headers=auth_headers
    )
    audit = await client.get(
        f"/api/projects/{project.id}/planning/generation-candidates/"
        f"{edited.id}/audit",
        headers=auth_headers,
    )

    assert listing.status_code == detail.status_code == audit.status_code == 200
    assert [item["version_no"] for item in listing.json()["items"]] == [2, 1]
    assert "content" not in listing.json()["items"][0]
    assert listing.json()["items"][0]["origin_kind"] == "manual_edit"
    assert detail.json()["parent_candidate_id"] == root_id
    assert detail.json()["parent_version_no"] == 1
    assert detail.json()["root_candidate_id"] == root_id
    assert detail.json()["root_origin_kind"] == "generated"
    assert detail.json()["ai_invoked_for_this_version"] is False
    assert detail.json()["billing_effect_for_this_version"] == "none"
    assert detail.json()["usage_status_for_this_version"] == "not_applicable"
    assert audit.json()["candidate_id"] == edited.id
    assert audit.json()["candidate_version"] == 2

    first_page = await client.get(
        _candidate_list_path(project.id, run.id),
        headers=auth_headers,
        params={"limit": 1},
    )
    second_page = await client.get(
        _candidate_list_path(project.id, run.id),
        headers=auth_headers,
        params={"limit": 1, "before_version_no": 2},
    )
    assert first_page.json()["has_more"] is True
    assert first_page.json()["next_cursor"] == "2"
    assert [item["version_no"] for item in second_page.json()["items"]] == [1]
    assert second_page.json()["has_more"] is False


@pytest.mark.usefixtures("clean_db")
async def test_version_reads_fail_closed_when_any_ancestor_is_corrupt(
    client, auth_headers
):
    project, run, root_id = await _create_generated_candidate(client, auth_headers)
    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, root_id)
        assert root is not None
        user_id = root.created_by
    edited = await _insert_manual_candidate(
        project_id=project.id,
        run_id=run.id,
        parent_id=root_id,
        user_id=user_id,
        version_no=2,
        content="星港的门已经开启。",
    )
    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, root_id)
        assert root is not None
        root.title = "串章标题"
        await session.commit()

    detail = await client.get(
        _candidate_path(project.id, run.id, edited.id), headers=auth_headers
    )
    listing = await client.get(
        _candidate_list_path(project.id, run.id), headers=auth_headers
    )
    audit = await client.get(
        f"/api/projects/{project.id}/planning/generation-candidates/"
        f"{edited.id}/audit",
        headers=auth_headers,
    )

    assert detail.status_code == listing.status_code == audit.status_code == 409
    assert detail.json()["detail"]["code"] == "GENERATION_CANDIDATE_CORRUPT"


@pytest.mark.usefixtures("clean_db")
async def test_version_reads_enforce_owner_project_run_and_cursor(
    client, auth_headers, second_auth_headers
):
    project, run, root_id = await _create_generated_candidate(client, auth_headers)
    _, other_project, other_run = await _authenticated_prepared_run()

    wrong_owner = await client.get(
        _candidate_path(project.id, run.id, root_id), headers=second_auth_headers
    )
    wrong_project = await client.get(
        _candidate_path(other_project.id, run.id, root_id), headers=auth_headers
    )
    wrong_run = await client.get(
        _candidate_path(project.id, other_run.id, root_id), headers=auth_headers
    )
    empty_page = await client.get(
        _candidate_list_path(project.id, run.id),
        headers=auth_headers,
        params={"before_version_no": 1},
    )

    assert wrong_owner.status_code == 403
    assert wrong_project.status_code == 404
    assert wrong_run.status_code == 404
    assert empty_page.status_code == 200
    assert empty_page.json()["items"] == []


@pytest.mark.usefixtures("clean_db")
async def test_version_list_hides_missing_foreign_project_and_run_identity(
    client, auth_headers, second_auth_headers
):
    project, run, _ = await _create_generated_candidate(client, auth_headers)
    _, other_project, _ = await _authenticated_prepared_run()
    paths = [
        (_candidate_list_path(project.id, run.id), second_auth_headers),
        (_candidate_list_path("f" * 32, run.id), auth_headers),
        (_candidate_list_path(other_project.id, run.id), auth_headers),
        (_candidate_list_path(project.id, "e" * 32), auth_headers),
    ]
    responses = [await client.get(path, headers=headers) for path, headers in paths]
    assert {response.status_code for response in responses} == {404}
    assert {response.json()["detail"]["code"] for response in responses} == {
        "GENERATION_RUN_NOT_FOUND"
    }
    assert len({response.json()["detail"]["message"] for response in responses}) == 1


@pytest.mark.usefixtures("clean_db")
async def test_manual_edit_post_replays_by_key_and_conflicts_on_changed_payload(
    client, auth_headers
):
    project, run, root_id = await _create_generated_candidate(client, auth_headers)
    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, root_id)
        assert root is not None
        original_content = root.content
        body = _manual_edit_body(
            root,
            run,
            operation_key="manual-edit-same-key-v1",
            content=f"{root.content}\n沈星记下了新的航标。",
        )

    first = await client.post(
        _manual_edit_path(project.id, run.id), headers=auth_headers, json=body
    )
    replay = await client.post(
        _manual_edit_path(project.id, run.id), headers=auth_headers, json=body
    )
    recovered = await client.get(
        f"{_manual_edit_path(project.id, run.id)}/by-key/" "manual-edit-same-key-v1",
        headers=auth_headers,
    )
    conflict = await client.post(
        _manual_edit_path(project.id, run.id),
        headers=auth_headers,
        json={**body, "content": f'{body["content"]}\n不同请求。'},
    )

    assert first.status_code == replay.status_code == recovered.status_code == 200
    assert first.json()["replayed"] is False
    assert replay.json()["replayed"] is recovered.json()["replayed"] is True
    assert first.json()["candidate"]["id"] == replay.json()["candidate"]["id"]
    assert first.json()["candidate"]["id"] == manual_edit_candidate_id(
        user_id=first.json()["candidate"]["created_by"],
        project_id=project.id,
        operation_key=body["operation_key"],
    )
    assert first.json()["ai_invoked"] is False
    assert first.json()["billing_effect"] == "none"
    assert first.json()["usage_status"] == "not_applicable"
    assert body["operation_key"] not in first.text
    assert conflict.status_code == 409
    assert (
        conflict.json()["detail"]["code"] == "GENERATION_CANDIDATE_OPERATION_CONFLICT"
    )

    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, root_id)
        assert root is not None and root.content == original_content
        assert (
            await session.scalar(
                select(func.count()).select_from(ChapterGenerationCandidate)
            )
            == 2
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(ChapterGenerationAttempt)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(ChapterTechnicalDemoExecution)
            )
            == 0
        )
        assert await session.scalar(select(func.count()).select_from(Chapter)) == 0
        assert await session.scalar(select(func.count()).select_from(StoryMemory)) == 0
        assert (
            await session.scalar(select(func.count()).select_from(SettingElement)) == 0
        )
        assert (
            await session.scalar(select(func.count()).select_from(ForeshadowFact)) == 0
        )


def test_manual_edit_fingerprint_binds_every_request_identity_field():
    payload = {
        "project_id": "1" * 32,
        "user_id": "2" * 32,
        "run_id": "3" * 32,
        "operation_key": "manual-fingerprint-v1",
        "parent_candidate_id": "4" * 32,
        "expected_parent_version_no": 2,
        "expected_parent_checksum": "5" * 64,
        "expected_context_checksum": "6" * 64,
        "content": "星港手工修订。",
    }
    original = manual_edit_request_fingerprint(**payload)
    variants = [
        {**payload, "project_id": "a" * 32},
        {**payload, "user_id": "b" * 32},
        {**payload, "run_id": "c" * 32},
        {**payload, "operation_key": "manual-fingerprint-v2"},
        {**payload, "parent_candidate_id": "d" * 32},
        {**payload, "expected_parent_version_no": 3},
        {**payload, "expected_parent_checksum": "e" * 64},
        {**payload, "expected_context_checksum": "f" * 64},
        {**payload, "content": "星港不同修订。"},
    ]
    assert all(
        manual_edit_request_fingerprint(**variant) != original for variant in variants
    )


@pytest.mark.usefixtures("clean_db")
async def test_manual_edit_rechecks_maintenance_gate_immediately_before_insert(
    client, auth_headers, monkeypatch
):
    project, run, root_id = await _create_generated_candidate(client, auth_headers)
    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, root_id)
        assert root is not None
        root_snapshot = (
            root.content,
            root.content_checksum,
            root.version_no,
            root.parent_candidate_id,
        )
        body = _manual_edit_body(
            root,
            run,
            operation_key="manual-maintenance-deferred-v1",
            content=f"{root.content}\n等待期间进入维护。",
        )
        before = {
            "candidates": await session.scalar(
                select(func.count()).select_from(ChapterGenerationCandidate)
            ),
            "attempts": await session.scalar(
                select(func.count()).select_from(ChapterGenerationAttempt)
            ),
            "technical": await session.scalar(
                select(func.count()).select_from(ChapterTechnicalDemoExecution)
            ),
            "chapters": await session.scalar(select(func.count()).select_from(Chapter)),
            "stories": await session.scalar(
                select(func.count()).select_from(StoryMemory)
            ),
            "lore": await session.scalar(
                select(func.count()).select_from(SettingElement)
            ),
            "facts": await session.scalar(
                select(func.count()).select_from(ForeshadowFact)
            ),
        }

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", False)
    real_gate = generation_candidates.ensure_project_writes_available
    gate_calls = 0

    def freeze_after_first_gate():
        nonlocal gate_calls
        gate_calls += 1
        real_gate()
        if gate_calls == 1:
            monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)

    monkeypatch.setattr(
        generation_candidates,
        "ensure_project_writes_available",
        freeze_after_first_gate,
    )
    response = await client.post(
        _manual_edit_path(project.id, run.id), headers=auth_headers, json=body
    )
    assert response.status_code == 503
    assert response.json()["code"] == "PROJECT_WRITE_FROZEN"
    assert gate_calls == 2

    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, root_id)
        assert root is not None
        assert (
            root.content,
            root.content_checksum,
            root.version_no,
            root.parent_candidate_id,
        ) == root_snapshot
        after = {
            "candidates": await session.scalar(
                select(func.count()).select_from(ChapterGenerationCandidate)
            ),
            "attempts": await session.scalar(
                select(func.count()).select_from(ChapterGenerationAttempt)
            ),
            "technical": await session.scalar(
                select(func.count()).select_from(ChapterTechnicalDemoExecution)
            ),
            "chapters": await session.scalar(select(func.count()).select_from(Chapter)),
            "stories": await session.scalar(
                select(func.count()).select_from(StoryMemory)
            ),
            "lore": await session.scalar(
                select(func.count()).select_from(SettingElement)
            ),
            "facts": await session.scalar(
                select(func.count()).select_from(ForeshadowFact)
            ),
        }
    assert after == before


@pytest.mark.usefixtures("clean_db")
async def test_manual_edit_accepts_a_manual_parent_and_allocates_next_version(
    client, auth_headers
):
    project, run, root_id = await _create_generated_candidate(client, auth_headers)
    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, root_id)
        assert root is not None
        first_body = _manual_edit_body(
            root,
            run,
            operation_key="manual-parent-first-v1",
            content=f"{root.content}\n第一次手工修订。",
        )
    first = await client.post(
        _manual_edit_path(project.id, run.id),
        headers=auth_headers,
        json=first_body,
    )
    assert first.status_code == 200, first.text
    first_candidate = first.json()["candidate"]
    second_body = {
        "operation_key": "manual-parent-second-v1",
        "parent_candidate_id": first_candidate["id"],
        "expected_parent_version_no": first_candidate["version_no"],
        "expected_parent_checksum": first_candidate["content_checksum"],
        "expected_context_checksum": run.context_checksum,
        "content": f'{first_candidate["content"]}\n第二次手工修订。',
    }
    second = await client.post(
        _manual_edit_path(project.id, run.id),
        headers=auth_headers,
        json=second_body,
    )

    assert second.status_code == 200, second.text
    assert second.json()["candidate"]["version_no"] == 3
    assert second.json()["candidate"]["parent_version_no"] == 2
    assert second.json()["candidate"]["root_candidate_id"] == root_id
    assert second.json()["candidate"]["root_origin_kind"] == "generated"


@pytest.mark.usefixtures("clean_db")
async def test_manual_edit_accepts_strict_technical_demo_parent(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "test")
    monkeypatch.setattr(settings, "DEMO_FIXTURE_ENABLED", True)
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(demo_fixture, "load_settings", lambda: {"api_key": ""})
    monkeypatch.setattr(
        demo_fixture,
        "_active_database_url",
        lambda _db: "sqlite+aiosqlite:///:memory:",
    )
    ids, run = await _prepare_demo(client, auth_headers)
    technical, _ = await _execute_technical_once(
        client, auth_headers, ids, run, "technical-parent-manual-edit-v1"
    )
    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, technical["candidate_id"])
        assert root is not None
        root_id = root.id
        body = _manual_edit_body(
            root,
            type("RunSnapshot", (), {"context_checksum": run["context_checksum"]})(),
            operation_key="manual-from-technical-v1",
            content=f"{root.content}\n手工补充了一句。",
        )

    response = await client.post(
        _manual_edit_path(ids["project_id"], run["id"]),
        headers=auth_headers,
        json=body,
    )
    assert response.status_code == 200, response.text
    assert response.json()["candidate"]["root_candidate_id"] == root_id
    assert response.json()["candidate"]["root_origin_kind"] == "technical_demo"
    assert response.json()["candidate"]["ai_invoked_for_this_version"] is False
    assert response.json()["candidate"]["billing_effect_for_this_version"] == "none"


@pytest.mark.usefixtures("clean_db")
async def test_manual_edit_rejects_unchanged_or_stale_parent_without_new_version(
    client, auth_headers
):
    project, run, root_id = await _create_generated_candidate(client, auth_headers)
    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, root_id)
        assert root is not None
        unchanged = _manual_edit_body(
            root,
            run,
            operation_key="manual-unchanged-v1",
            content=root.content,
        )
        stale = {
            **_manual_edit_body(
                root,
                run,
                operation_key="manual-stale-parent-v1",
                content=f"{root.content}\n尝试另存。",
            ),
            "expected_parent_checksum": "f" * 64,
        }

    unchanged_response = await client.post(
        _manual_edit_path(project.id, run.id),
        headers=auth_headers,
        json=unchanged,
    )
    stale_response = await client.post(
        _manual_edit_path(project.id, run.id), headers=auth_headers, json=stale
    )
    assert unchanged_response.status_code == stale_response.status_code == 409
    assert (
        unchanged_response.json()["detail"]["code"]
        == "GENERATION_CANDIDATE_CONTENT_UNCHANGED"
    )
    assert (
        stale_response.json()["detail"]["code"] == "GENERATION_CANDIDATE_PARENT_CHANGED"
    )
    async with TestSessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(ChapterGenerationCandidate)
            )
            == 1
        )


@pytest.mark.usefixtures("clean_db")
async def test_manual_edit_post_is_owner_project_and_run_scoped(
    client, auth_headers, second_auth_headers
):
    project, run, root_id = await _create_generated_candidate(client, auth_headers)
    _, other_project, other_run = await _authenticated_prepared_run()
    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, root_id)
        assert root is not None
        body = _manual_edit_body(
            root,
            run,
            operation_key="manual-scope-v1",
            content=f"{root.content}\n身份范围测试。",
        )
    wrong_owner = await client.post(
        _manual_edit_path(project.id, run.id),
        headers=second_auth_headers,
        json=body,
    )
    wrong_project = await client.post(
        _manual_edit_path(other_project.id, run.id),
        headers=auth_headers,
        json=body,
    )
    wrong_run = await client.post(
        _manual_edit_path(project.id, other_run.id),
        headers=auth_headers,
        json=body,
    )
    assert wrong_owner.status_code == 403
    assert wrong_project.status_code == 404
    assert wrong_run.status_code == 404
    async with TestSessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(ChapterGenerationCandidate)
            )
            == 1
        )


@pytest.mark.usefixtures("clean_db")
async def test_manual_edit_lineage_cycle_fails_closed(client, auth_headers):
    project, run, root_id = await _create_generated_candidate(client, auth_headers)
    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, root_id)
        assert root is not None
        first_body = _manual_edit_body(
            root,
            run,
            operation_key="manual-cycle-first-v1",
            content=f"{root.content}\n第一个链节。",
        )
    first = await client.post(
        _manual_edit_path(project.id, run.id), headers=auth_headers, json=first_body
    )
    assert first.status_code == 200
    first_candidate = first.json()["candidate"]
    second = await client.post(
        _manual_edit_path(project.id, run.id),
        headers=auth_headers,
        json={
            "operation_key": "manual-cycle-second-v1",
            "parent_candidate_id": first_candidate["id"],
            "expected_parent_version_no": first_candidate["version_no"],
            "expected_parent_checksum": first_candidate["content_checksum"],
            "expected_context_checksum": run.context_checksum,
            "content": f'{first_candidate["content"]}\n第二个链节。',
        },
    )
    assert second.status_code == 200
    second_id = second.json()["candidate"]["id"]
    async with TestSessionLocal() as session:
        await session.execute(
            update(ChapterGenerationCandidate)
            .where(ChapterGenerationCandidate.id == first_candidate["id"])
            .values(parent_candidate_id=second_id)
        )
        await session.commit()

    response = await client.get(
        _candidate_path(project.id, run.id, second_id), headers=auth_headers
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "GENERATION_CANDIDATE_CORRUPT"


@pytest.mark.usefixtures("clean_db")
async def test_manual_edit_lineage_over_depth_limit_fails_closed_without_writes(
    client, auth_headers
):
    project, run, root_id = await _create_generated_candidate(client, auth_headers)
    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, root_id)
        assert root is not None
        user_id = root.created_by
    parent_id = root_id
    latest_id = root_id
    for version_no in range(2, 103):
        candidate = await _insert_manual_candidate(
            project_id=project.id,
            run_id=run.id,
            parent_id=parent_id,
            user_id=user_id,
            version_no=version_no,
            content=f"第 {version_no} 层手工候选。",
        )
        latest_id = candidate.id
        parent_id = candidate.id

    async with TestSessionLocal() as session:
        before_candidates = await session.scalar(
            select(func.count()).select_from(ChapterGenerationCandidate)
        )
        before_attempts = await session.scalar(
            select(func.count()).select_from(ChapterGenerationAttempt)
        )
    response = await client.get(
        _candidate_path(project.id, run.id, latest_id), headers=auth_headers
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "GENERATION_CANDIDATE_CORRUPT"
    async with TestSessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(ChapterGenerationCandidate)
            )
            == before_candidates
            == 102
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(ChapterGenerationAttempt)
            )
            == before_attempts
            == 1
        )


@pytest.mark.usefixtures("clean_db")
async def test_postgres_concurrent_same_key_creates_one_manual_version(
    client, auth_headers
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("requires PostgreSQL row locks and independent connections")
    project, run, root_id = await _create_generated_candidate(client, auth_headers)
    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, root_id)
        assert root is not None
        body = _manual_edit_body(
            root,
            run,
            operation_key="manual-concurrent-same-key-v1",
            content=f"{root.content}\n并发另存同一内容。",
        )
    first, second = await asyncio.wait_for(
        asyncio.gather(
            client.post(
                _manual_edit_path(project.id, run.id),
                headers=auth_headers,
                json=body,
            ),
            client.post(
                _manual_edit_path(project.id, run.id),
                headers=auth_headers,
                json=body,
            ),
        ),
        timeout=20,
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["candidate"]["id"] == second.json()["candidate"]["id"]
    assert sorted((first.json()["replayed"], second.json()["replayed"])) == [
        False,
        True,
    ]
    assert first.json()["candidate"]["id"] == manual_edit_candidate_id(
        user_id=first.json()["candidate"]["created_by"],
        project_id=project.id,
        operation_key=body["operation_key"],
    )
    async with TestSessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(ChapterGenerationCandidate)
            )
            == 2
        )


@pytest.mark.usefixtures("clean_db")
async def test_postgres_concurrent_different_keys_allocate_unique_versions(
    client, auth_headers
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("requires PostgreSQL row locks and independent connections")
    project, run, root_id = await _create_generated_candidate(client, auth_headers)
    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, root_id)
        assert root is not None
        first_body = _manual_edit_body(
            root,
            run,
            operation_key="manual-concurrent-first-v1",
            content=f"{root.content}\n并发版本甲。",
        )
        second_body = _manual_edit_body(
            root,
            run,
            operation_key="manual-concurrent-second-v1",
            content=f"{root.content}\n并发版本乙。",
        )
    first, second = await asyncio.wait_for(
        asyncio.gather(
            client.post(
                _manual_edit_path(project.id, run.id),
                headers=auth_headers,
                json=first_body,
            ),
            client.post(
                _manual_edit_path(project.id, run.id),
                headers=auth_headers,
                json=second_body,
            ),
        ),
        timeout=20,
    )
    assert first.status_code == second.status_code == 200
    assert {
        first.json()["candidate"]["version_no"],
        second.json()["candidate"]["version_no"],
    } == {2, 3}
    assert first.json()["candidate"]["id"] != second.json()["candidate"]["id"]


@pytest.mark.usefixtures("clean_db")
async def test_postgres_delete_lock_first_prevents_manual_version(
    client, auth_headers, monkeypatch
):
    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("requires PostgreSQL project row locks")
    project, run, root_id = await _create_generated_candidate(client, auth_headers)
    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, root_id)
        assert root is not None
        body = _manual_edit_body(
            root,
            run,
            operation_key="manual-delete-first-v1",
            content=f"{root.content}\n删除竞态不应保存。",
        )
    real_cleanup = projects_api.delete_project_relational_dependents
    lock_acquired = asyncio.Event()
    release_delete = asyncio.Event()
    delete_task = None
    edit_task = None

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
        edit_task = asyncio.create_task(
            client.post(
                _manual_edit_path(project.id, run.id),
                headers=auth_headers,
                json=body,
            )
        )
        await asyncio.sleep(0.1)
        assert not edit_task.done()
        release_delete.set()
        deleted, response = await asyncio.wait_for(
            asyncio.gather(delete_task, edit_task), timeout=20
        )
    finally:
        release_delete.set()
        pending = [
            task for task in (delete_task, edit_task) if task and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    assert deleted.status_code == 200
    assert response.status_code == 404
    async with TestSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Project)) == 0
        assert (
            await session.scalar(
                select(func.count()).select_from(ChapterGenerationCandidate)
            )
            == 0
        )


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize(
    ("content", "status_code", "code"),
    [
        ("   \n", 409, "GENERATION_CANDIDATE_CONTENT_EMPTY"),
        ("a𠀀" * 70_000, 409, "GENERATION_CANDIDATE_CONTENT_TOO_LARGE"),
        ("a\ud800", 422, None),
    ],
)
async def test_manual_edit_rejects_invalid_unicode_content_without_writes(
    client, auth_headers, content, status_code, code
):
    project, run, root_id = await _create_generated_candidate(client, auth_headers)
    async with TestSessionLocal() as session:
        root = await session.get(ChapterGenerationCandidate, root_id)
        assert root is not None
        body = _manual_edit_body(
            root,
            run,
            operation_key="manual-edit-invalid-content",
            content=content,
        )

    response = await client.post(
        _manual_edit_path(project.id, run.id),
        headers={**auth_headers, "Content-Type": "application/json"},
        content=json.dumps(body, ensure_ascii=True).encode("utf-8"),
    )
    assert response.status_code == status_code
    if code is not None:
        assert response.json()["detail"]["code"] == code
    async with TestSessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(ChapterGenerationCandidate)
            )
            == 1
        )
