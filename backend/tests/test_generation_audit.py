"""DEV-017B3c1 deterministic, read-only candidate audit tests."""

import hashlib
import json

import pytest
from sqlalchemy import func, select

from app.core.generation_execution import get_generation_transport
from app.main import app
from app.models.generation import (
    ChapterGenerationAttempt,
    ChapterGenerationCandidate,
    ChapterGenerationRun,
)
from app.models.foreshadow import ForeshadowFact, ForeshadowLifecycle, ForeshadowPlanItem
from app.models.lore import ElementRelation, ElementVersion, SettingElement
from app.models.project import Chapter, StoryMemory
from tests.conftest import TestSessionLocal
from tests.test_generation_execution import (
    CountingFakeTransport,
    _authenticated_prepared_run,
    _execute_body,
    _execute_path,
)


def _audit_path(project_id: str, candidate_id: str) -> str:
    return (
        f"/api/projects/{project_id}/planning/generation-candidates/"
        f"{candidate_id}/audit"
    )


async def _replace_manifest(run_id: str, mutate) -> ChapterGenerationRun:
    async with TestSessionLocal() as session:
        run = await session.get(ChapterGenerationRun, run_id)
        assert run is not None
        manifest = json.loads(json.dumps(run.context_manifest, ensure_ascii=False))
        mutate(manifest)
        canonical = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        run.context_manifest = manifest
        run.context_checksum = hashlib.sha256(canonical).hexdigest()
        run.context_size_bytes = len(canonical)
        await session.commit()
        await session.refresh(run)
        return run


@pytest.mark.usefixtures("clean_db")
async def test_candidate_audit_is_stable_read_only_and_does_not_recall_model(
    client, auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport(result="星" * 1_300 + "《沈星》")
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        attempt = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-audit-stable"),
        )
        assert attempt.status_code == 200
        candidate_id = attempt.json()["candidate_id"]
        async with TestSessionLocal() as session:
            before = {
                "attempts": await session.scalar(
                    select(func.count()).select_from(ChapterGenerationAttempt)
                ),
                "candidates": await session.scalar(
                    select(func.count()).select_from(ChapterGenerationCandidate)
                ),
                "chapters": await session.scalar(
                    select(func.count()).select_from(Chapter)
                ),
                "memories": await session.scalar(
                    select(func.count()).select_from(StoryMemory)
                ),
                "lore_elements": await session.scalar(
                    select(func.count()).select_from(SettingElement)
                ),
                "lore_versions": await session.scalar(
                    select(func.count()).select_from(ElementVersion)
                ),
                "lore_relations": await session.scalar(
                    select(func.count()).select_from(ElementRelation)
                ),
                "foreshadow_lifecycles": await session.scalar(
                    select(func.count()).select_from(ForeshadowLifecycle)
                ),
                "foreshadow_plans": await session.scalar(
                    select(func.count()).select_from(ForeshadowPlanItem)
                ),
                "foreshadow_facts": await session.scalar(
                    select(func.count()).select_from(ForeshadowFact)
                ),
            }
        first = await client.get(
            _audit_path(project.id, candidate_id), headers=auth_headers
        )
        second = await client.get(
            _audit_path(project.id, candidate_id), headers=auth_headers
        )
        async with TestSessionLocal() as session:
            after = {
                "attempts": await session.scalar(
                    select(func.count()).select_from(ChapterGenerationAttempt)
                ),
                "candidates": await session.scalar(
                    select(func.count()).select_from(ChapterGenerationCandidate)
                ),
                "chapters": await session.scalar(
                    select(func.count()).select_from(Chapter)
                ),
                "memories": await session.scalar(
                    select(func.count()).select_from(StoryMemory)
                ),
                "lore_elements": await session.scalar(
                    select(func.count()).select_from(SettingElement)
                ),
                "lore_versions": await session.scalar(
                    select(func.count()).select_from(ElementVersion)
                ),
                "lore_relations": await session.scalar(
                    select(func.count()).select_from(ElementRelation)
                ),
                "foreshadow_lifecycles": await session.scalar(
                    select(func.count()).select_from(ForeshadowLifecycle)
                ),
                "foreshadow_plans": await session.scalar(
                    select(func.count()).select_from(ForeshadowPlanItem)
                ),
                "foreshadow_facts": await session.scalar(
                    select(func.count()).select_from(ForeshadowFact)
                ),
            }
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    report = first.json()
    assert report["status"] == "pass"
    assert report["ruleset_version"] == 1
    assert report["candidate_id"] == candidate_id
    assert report["target_length"]["status"] == "pass"
    assert report["preparation"] == {"status": "pass", "warnings": []}
    assert report["unrecognized_explicit_terms"] == {
        "status": "pass",
        "items": [],
        "truncated": False,
    }
    assert report["context_summary"]["elements"][0]["name"] == "沈星"
    assert fake.call_count == 1
    assert before == after


@pytest.mark.usefixtures("clean_db")
async def test_candidate_audit_reviews_length_preflight_and_explicit_unknown_term(
    client, auth_headers
):
    _, project, run = await _authenticated_prepared_run()

    def add_warning(manifest):
        manifest["warnings"] = [
            {"code": "CHAPTER_SUMMARY_EMPTY", "element_id": None}
        ]
        manifest["counts"]["warnings"] = 1

    run = await _replace_manifest(run.id, add_warning)
    fake = CountingFakeTransport(result="短章提到了《无名星门》。")
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        attempt = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-audit-review"),
        )
        assert attempt.status_code == 200
        report_response = await client.get(
            _audit_path(project.id, attempt.json()["candidate_id"]),
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert report_response.status_code == 200
    report = report_response.json()
    assert report["status"] == "review"
    assert report["target_length"]["status"] == "review"
    assert report["target_length"]["minimum_word_count"] == 1_260
    assert report["target_length"]["maximum_word_count"] == 2_340
    assert report["preparation"]["status"] == "review"
    assert report["preparation"]["warnings"] == [
        {"code": "CHAPTER_SUMMARY_EMPTY", "element_id": None}
    ]
    terms = report["unrecognized_explicit_terms"]
    assert terms["status"] == "review"
    assert terms["items"][0]["term"] == "无名星门"
    assert "《无名星门》" in terms["items"][0]["excerpt"]
    assert terms["items"][0]["end_offset"] > terms["items"][0]["start_offset"]
    assert fake.call_count == 1


@pytest.mark.usefixtures("clean_db")
async def test_candidate_audit_uses_unicode_code_point_offsets_and_caps_terms(
    client, auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    terms = "《 𠮷星门 》" + "".join(f"《专名{index}》" for index in range(21))
    fake = CountingFakeTransport(result=f"🌌{terms}")
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        attempt = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-audit-unicode"),
        )
        report = await client.get(
            _audit_path(project.id, attempt.json()["candidate_id"]),
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert report.status_code == 200
    items = report.json()["unrecognized_explicit_terms"]
    assert len(items["items"]) == 20
    assert items["truncated"] is True
    first = items["items"][0]
    assert first["start_offset"] == 1
    assert first["term"] == "𠮷星门"
    assert fake.result[first["start_offset"] : first["end_offset"]] == "《 𠮷星门 》"


@pytest.mark.usefixtures("clean_db")
async def test_candidate_audit_marks_missing_target_as_not_applicable(
    client, auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    run = await _replace_manifest(
        run.id,
        lambda manifest: manifest["chapter"].update(target_word_count=None),
    )
    fake = CountingFakeTransport(result="星港的门缓缓开启。")
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        attempt = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-audit-no-target"),
        )
        report = await client.get(
            _audit_path(project.id, attempt.json()["candidate_id"]),
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert report.status_code == 200
    assert report.json()["target_length"] == {
        "status": "not_applicable",
        "actual_word_count": 8,
        "target_word_count": None,
        "minimum_word_count": None,
        "maximum_word_count": None,
    }


@pytest.mark.usefixtures("clean_db")
async def test_candidate_audit_applies_inclusive_target_length_boundaries(
    client, auth_headers
):
    _, project, run = await _authenticated_prepared_run()
    fake = CountingFakeTransport(result="星")
    app.dependency_overrides[get_generation_transport] = lambda: fake
    try:
        attempt = await client.post(
            _execute_path(project.id, run.id),
            headers=auth_headers,
            json=_execute_body(run, operation_key="generation-audit-boundaries"),
        )
        candidate_id = attempt.json()["candidate_id"]
        for word_count, expected_status in (
            (1_259, "review"),
            (1_260, "pass"),
            (2_340, "pass"),
            (2_341, "review"),
        ):
            content = "星" * word_count
            async with TestSessionLocal() as session:
                candidate = await session.get(
                    ChapterGenerationCandidate, candidate_id
                )
                assert candidate is not None
                candidate.content = content
                candidate.content_checksum = hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest()
                candidate.content_size_bytes = len(content.encode("utf-8"))
                candidate.word_count = word_count
                await session.commit()
            report = await client.get(
                _audit_path(project.id, candidate_id), headers=auth_headers
            )
            assert report.status_code == 200
            target = report.json()["target_length"]
            assert target["minimum_word_count"] == 1_260
            assert target["maximum_word_count"] == 2_340
            assert target["status"] == expected_status
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert fake.call_count == 1


@pytest.mark.usefixtures("clean_db")
async def test_candidate_audit_is_owner_project_isolated_and_corruption_fails_closed(
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
            json=_execute_body(run, operation_key="generation-audit-isolation"),
        )
        candidate_id = attempt.json()["candidate_id"]
        wrong_owner = await client.get(
            _audit_path(project.id, candidate_id), headers=second_auth_headers
        )
        wrong_project = await client.get(
            _audit_path(other_project.id, candidate_id), headers=auth_headers
        )
        async with TestSessionLocal() as session:
            candidate = await session.get(ChapterGenerationCandidate, candidate_id)
            assert candidate is not None
            candidate.content_size_bytes += 1
            await session.commit()
        corrupt = await client.get(
            _audit_path(project.id, candidate_id), headers=auth_headers
        )
    finally:
        app.dependency_overrides.pop(get_generation_transport, None)

    assert wrong_owner.status_code == 403
    assert wrong_project.status_code == 404
    assert wrong_project.json()["detail"]["code"] == "GENERATION_CANDIDATE_NOT_FOUND"
    assert corrupt.status_code == 409
    assert corrupt.json()["detail"]["code"] == "GENERATION_ATTEMPT_CORRUPT"
    assert fake.call_count == 1
