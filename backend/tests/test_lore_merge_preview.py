"""DEV-014C5A read-only formal-lore merge preview tests."""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.lore_merge_preview import decode_merge_preview_token
from app.core.lore_write import LoreWriteError
from app.models.lore import (
    ElementRelation,
    ElementSource,
    LoreMergeOperation,
    LoreMergeRelationAction,
    SettingElement,
)
from app.models.project import Project
from tests.conftest import TestSessionLocal
from tests.test_lore_writes import (
    _create_project,
    _create_relation,
    _create_relational_element,
)


async def _confirmed_duplicate(client, headers, project_id):
    left = await _create_relational_element(
        client,
        headers,
        project_id,
        name="Ａlice",
        summary="主角",
        payload={"personality": "谨慎"},
        field_states={"personality": "provided"},
        sources=[{
            "kind": "manual",
            "reference": "author-note",
            "excerpt": "Alice 是一名谨慎的主角。",
            "is_primary": True,
        }],
    )
    right = await _create_relational_element(
        client,
        headers,
        project_id,
        name="alice",
        summary="主角",
        payload={"personality": "谨慎"},
        field_states={"personality": "provided"},
        sources=[{
            "kind": "document_import",
            "reference": "chapter-1",
            "excerpt": "alice 在开篇表现得很谨慎。",
            "is_primary": True,
        }],
    )
    scan = await client.post(
        f"/api/projects/{project_id}/lore/reviews/scan", headers=headers
    )
    assert scan.status_code == 200, scan.text
    listing = await client.get(
        f"/api/projects/{project_id}/lore/reviews", headers=headers
    )
    suggestion = listing.json()["items"][0]
    assert suggestion["kind"] == "possible_duplicate"
    decided = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/decide",
        headers=headers,
        json={
            "operation_key": uuid.uuid4().hex,
            "expected_version": suggestion["lock_version"],
            "expected_evidence_revision": suggestion["evidence_revision"],
            "decision": "confirmed_duplicate",
            "note": "作者确认为同一设定",
        },
    )
    assert decided.status_code == 200, decided.text
    return left, right, decided.json()["suggestion"]


async def _preview_body(client, headers, project_id, left, right, suggestion):
    detail = await client.get(
        f"/api/projects/{project_id}/lore/elements/{left['id']}", headers=headers
    )
    assert detail.status_code == 200, detail.text
    fields = [field["key"] for field in detail.json()["field_definitions"]]
    return {
        "suggestion_expected_version": suggestion["lock_version"],
        "expected_evidence_revision": suggestion["evidence_revision"],
        "survivor_element_id": left["id"],
        "merged_element_id": right["id"],
        "survivor_expected_lock_version": left["lock_version"],
        "survivor_expected_content_version": left["content_version"],
        "merged_expected_lock_version": right["lock_version"],
        "merged_expected_content_version": right["content_version"],
        "name_choice": "survivor",
        "summary_choice": "survivor",
        "field_choices": {key: "survivor" for key in fields},
        "final_name": left["name"],
        "final_summary": left["summary"],
        "final_payload": left["payload"],
        "final_field_states": left["field_states"],
    }


@pytest.mark.usefixtures("clean_db")
async def test_preview_is_zero_write_and_token_contains_only_fingerprints(
    client, auth_headers
):
    project_id = await _create_project(client, auth_headers)
    left, right, suggestion = await _confirmed_duplicate(
        client, auth_headers, project_id
    )
    body = await _preview_body(
        client, auth_headers, project_id, left, right, suggestion
    )
    async with TestSessionLocal() as session:
        before_elements = [
            tuple(row)
            for row in (
                await session.execute(
                    select(
                        SettingElement.id,
                        SettingElement.content_version,
                        SettingElement.lock_version,
                        SettingElement.lifecycle_status,
                        SettingElement.merged_into_element_id,
                    ).where(SettingElement.project_id == project_id)
                )
            ).all()
        ]
        before_source_count = await session.scalar(
            select(func.count()).select_from(ElementSource).where(
                ElementSource.project_id == project_id
            )
        )

    response = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-preview",
        headers=auth_headers,
        json=body,
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["commit_available"] is False
    assert preview["blockers"] == []
    assert preview["source_impact"] == {
        "survivor_source_count": 1,
        "merged_source_count": 1,
        "preserved_total": 2,
        "exact_duplicate_pairs": 0,
        "strategy": "preserve_in_place",
    }
    claims = decode_merge_preview_token(preview["preview_token"])
    assert claims["v"] == 2
    assert claims["project_id"] == project_id
    assert claims["survivor_id"] == left["id"]
    assert isinstance(claims["type_id"], str)
    assert claims["type_id"]
    assert claims["type_schema_revision"] == 1
    assert claims["survivor_payload_schema_revision"] == 1
    assert claims["merged_payload_schema_revision"] == 1
    assert claims["field_schema_fingerprint"]
    assert "Alice 是一名谨慎的主角。" not in str(claims)
    assert "final_name" not in claims
    token = preview["preview_token"]
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    with pytest.raises(LoreWriteError):
        decode_merge_preview_token(tampered)
    with pytest.raises(LoreWriteError):
        decode_merge_preview_token(
            preview["preview_token"],
            now=datetime.fromisoformat(preview["expires_at"]),
        )

    async with TestSessionLocal() as session:
        after_elements = [
            tuple(row)
            for row in (
                await session.execute(
                    select(
                        SettingElement.id,
                        SettingElement.content_version,
                        SettingElement.lock_version,
                        SettingElement.lifecycle_status,
                        SettingElement.merged_into_element_id,
                    ).where(SettingElement.project_id == project_id)
                )
            ).all()
        ]
        assert after_elements == before_elements
        source_count = await session.scalar(
            select(func.count()).select_from(ElementSource).where(
                ElementSource.project_id == project_id
            )
        )
        assert source_count == before_source_count
        assert await session.scalar(select(func.count()).select_from(LoreMergeOperation)) == 0
        assert await session.scalar(select(func.count()).select_from(LoreMergeRelationAction)) == 0


@pytest.mark.usefixtures("clean_db")
async def test_preview_rejects_unconfirmed_and_stale_evidence(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    left = await _create_relational_element(client, auth_headers, project_id, name="Ａlice")
    right = await _create_relational_element(client, auth_headers, project_id, name="alice")
    await client.post(f"/api/projects/{project_id}/lore/reviews/scan", headers=auth_headers)
    suggestion = (
        await client.get(
            f"/api/projects/{project_id}/lore/reviews", headers=auth_headers
        )
    ).json()["items"][0]
    body = await _preview_body(client, auth_headers, project_id, left, right, suggestion)
    blocked = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-preview",
        headers=auth_headers,
        json=body,
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "LORE_MERGE_REVIEW_NOT_CONFIRMED"

    decided = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/decide",
        headers=auth_headers,
        json={
            "operation_key": uuid.uuid4().hex,
            "expected_version": suggestion["lock_version"],
            "expected_evidence_revision": suggestion["evidence_revision"],
            "decision": "confirmed_duplicate",
        },
    )
    confirmed = decided.json()["suggestion"]
    body = await _preview_body(client, auth_headers, project_id, left, right, confirmed)
    body["suggestion_expected_version"] -= 1
    stale = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-preview",
        headers=auth_headers,
        json=body,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "LORE_MERGE_REVIEW_STALE"


@pytest.mark.usefixtures("clean_db")
async def test_preview_requires_complete_and_truthful_field_selection(
    client, auth_headers
):
    project_id = await _create_project(client, auth_headers)
    left, right, suggestion = await _confirmed_duplicate(client, auth_headers, project_id)
    body = await _preview_body(client, auth_headers, project_id, left, right, suggestion)
    body["field_choices"].pop(next(iter(body["field_choices"])))
    incomplete = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-preview",
        headers=auth_headers,
        json=body,
    )
    assert incomplete.status_code == 422
    assert incomplete.json()["detail"]["code"] == "LORE_MERGE_SELECTION_INCOMPLETE"

    body = await _preview_body(client, auth_headers, project_id, left, right, suggestion)
    body["final_name"] = "未经选择的名称"
    mismatched = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-preview",
        headers=auth_headers,
        json=body,
    )
    assert mismatched.status_code == 422
    assert mismatched.json()["detail"]["code"] == "LORE_MERGE_SELECTION_INVALID"

    right_survives = await _preview_body(
        client, auth_headers, project_id, right, left, suggestion
    )
    reversed_preview = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-preview",
        headers=auth_headers,
        json=right_survives,
    )
    assert reversed_preview.status_code == 200, reversed_preview.text
    assert reversed_preview.json()["survivor"]["id"] == right["id"]
    assert reversed_preview.json()["final_name"] == right["name"]


@pytest.mark.usefixtures("clean_db")
async def test_preview_obeys_maintenance_and_project_ownership(
    client, auth_headers, second_auth_headers, monkeypatch
):
    from app.config import settings as app_settings
    from app.core import lore_merge_preview
    from app.core.maintenance import ProjectWriteFrozenError

    project_id = await _create_project(client, auth_headers)
    left, right, suggestion = await _confirmed_duplicate(
        client, auth_headers, project_id
    )
    body = await _preview_body(
        client, auth_headers, project_id, left, right, suggestion
    )
    forbidden = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-preview",
        headers=second_auth_headers,
        json=body,
    )
    assert forbidden.status_code == 403
    legacy_project_id = await _create_project(
        client, auth_headers, title="legacy preview gate"
    )
    legacy = await client.post(
        f"/api/projects/{legacy_project_id}/lore/reviews/{suggestion['id']}/merge-preview",
        headers=auth_headers,
        json=body,
    )
    assert legacy.status_code == 409
    assert legacy.json()["detail"]["code"] == "LORE_MODE_NOT_RELATIONAL"

    original = app_settings.LEGACY_JSON_WRITES_FROZEN
    app_settings.LEGACY_JSON_WRITES_FROZEN = True
    try:
        blocked = await client.post(
            f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-preview",
            headers=auth_headers,
            json=body,
        )
    finally:
        app_settings.LEGACY_JSON_WRITES_FROZEN = original
    assert blocked.status_code == 503
    assert blocked.json()["code"] == "PROJECT_WRITE_FROZEN"

    checks = 0

    def freeze_after_read():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ProjectWriteFrozenError("PROJECT_WRITE_FROZEN")

    monkeypatch.setattr(
        lore_merge_preview, "check_writes_available", freeze_after_read
    )
    frozen_after_read = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-preview",
        headers=auth_headers,
        json=body,
    )
    assert frozen_after_read.status_code == 503
    assert frozen_after_read.json()["code"] == "PROJECT_WRITE_FROZEN"
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(LoreMergeOperation)
        ) == 0
        assert await session.scalar(
            select(func.count()).select_from(LoreMergeRelationAction)
        ) == 0


@pytest.mark.usefixtures("clean_db")
async def test_relation_plan_covers_rewire_self_loop_exact_duplicate_and_blocker(
    client, auth_headers
):
    project_id = await _create_project(client, auth_headers)
    left, right, suggestion = await _confirmed_duplicate(client, auth_headers, project_id)
    target = await _create_relational_element(
        client, auth_headers, project_id, name="外部角色"
    )
    await _create_relation(
        client, auth_headers, project_id, right["id"], left["id"],
        relation_type="enemy", description="旧冲突",
    )
    await _create_relation(
        client, auth_headers, project_id, right["id"], target["id"],
        relation_type="member_of", description="指导",
    )
    retained = await _create_relation(
        client, auth_headers, project_id, left["id"], target["id"],
        relation_type="ally", description="同盟",
    )
    # Simulate a pre-C3 symmetric row whose endpoints were not canonicalized.
    async with TestSessionLocal() as session:
        await session.execute(
            update(ElementRelation)
            .where(ElementRelation.id == retained["id"])
            .values(
                source_element_id=target["id"],
                target_element_id=left["id"],
            )
        )
        session.add(
            ElementRelation(
                project_id=project_id,
                source_element_id=left["id"],
                target_element_id=target["id"],
                relation_key="ally",
                forward_label="盟友",
                reverse_label="盟友",
                description="历史反向行与正向行的语义不同",
                metadata_={},
                status="active",
                version_no=1,
                lock_version=1,
            )
        )
        await session.commit()
    await _create_relation(
        client, auth_headers, project_id, right["id"], target["id"],
        relation_type="ally", description="同盟",
    )
    exact_retained = await _create_relation(
        client, auth_headers, project_id, left["id"], target["id"],
        relation_type="affects", description="影响",
    )
    await _create_relation(
        client, auth_headers, project_id, right["id"], target["id"],
        relation_type="affects", description="影响",
    )
    await _create_relation(
        client, auth_headers, project_id, left["id"], target["id"],
        relation_type="related_to", description="旧识",
    )
    await _create_relation(
        client, auth_headers, project_id, right["id"], target["id"],
        relation_type="related_to", description="家族",
    )

    body = await _preview_body(client, auth_headers, project_id, left, right, suggestion)
    response = await client.post(
        f"/api/projects/{project_id}/lore/reviews/{suggestion['id']}/merge-preview",
        headers=auth_headers,
        json=body,
    )
    assert response.status_code == 200, response.text
    plans = response.json()["relation_plan"]
    actions = {plan["action"] for plan in plans}
    assert {
        "rewire", "self_loop_archive", "exact_duplicate_archive", "blocker"
    }.issubset(actions), plans
    exact = next(plan for plan in plans if plan["action"] == "exact_duplicate_archive")
    assert exact["retained_relation_id"] == exact_retained["id"]
    ally_plan = next(plan for plan in plans if plan["relation_key"] == "ally")
    assert ally_plan["action"] == "blocker"
    assert retained["id"] in ally_plan["reason"]
    assert response.json()["blockers"]


@pytest.mark.usefixtures("clean_db")
async def test_merge_audit_rejects_cross_project_references(client, auth_headers):
    project_a = await _create_project(client, auth_headers, title="项目 A")
    left_a, right_a, suggestion = await _confirmed_duplicate(
        client, auth_headers, project_a
    )
    relation_a = await _create_relation(
        client,
        auth_headers,
        project_a,
        left_a["id"],
        right_a["id"],
        relation_type="enemy",
    )
    project_b = await _create_project(client, auth_headers, title="项目 B")
    left_b = await _create_relational_element(
        client, auth_headers, project_b, name="B1"
    )
    right_b = await _create_relational_element(
        client, auth_headers, project_b, name="B2"
    )

    def operation(**overrides):
        values = {
            "project_id": project_b,
            "performed_by": owner_id,
            "operation_key": uuid.uuid4().hex,
            "request_fingerprint": "a" * 64,
            "suggestion_project_id": None,
            "suggestion_id": None,
            "evidence_revision": suggestion["evidence_revision"],
            "survivor_element_id": left_b["id"],
            "merged_element_id": right_b["id"],
            "survivor_before_content_version": 1,
            "survivor_before_lock_version": 1,
            "merged_before_content_version": 1,
            "merged_before_lock_version": 1,
            "source_fingerprint": "b" * 64,
            "relation_fingerprint": "c" * 64,
            "selection_snapshot": {},
            "plan_fingerprint": "d" * 64,
            "impact_summary": {},
            "survivor_after_content_version": 2,
            "survivor_after_lock_version": 2,
            "merged_after_lock_version": 2,
        }
        values.update(overrides)
        return LoreMergeOperation(**values)

    async with TestSessionLocal() as session:
        owner_id = await session.scalar(
            select(Project.owner_id).where(Project.id == project_b)
        )
        invalid_suggestion_refs = [
            {
                "suggestion_project_id": None,
                "suggestion_id": suggestion["id"],
            },
            {
                "suggestion_project_id": project_a,
                "suggestion_id": None,
            },
            {
                "suggestion_project_id": project_a,
                "suggestion_id": suggestion["id"],
            },
            {
                "suggestion_project_id": project_b,
                "suggestion_id": suggestion["id"],
            },
        ]
        for invalid_ref in invalid_suggestion_refs:
            session.add(operation(**invalid_ref))
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()

        valid_operation = operation()
        session.add(valid_operation)
        await session.commit()
        operation_id = valid_operation.id

        def relation_action(**overrides):
            values = {
                "project_id": project_b,
                "merge_operation_id": operation_id,
                "action": "rewired",
                "before_snapshot": {},
                "after_snapshot": {},
                "previous_lock_version": 1,
                "new_lock_version": 2,
            }
            values.update(overrides)
            return LoreMergeRelationAction(**values)

        invalid_relation_refs = [
            {"relation_project_id": None, "relation_id": relation_a["id"]},
            {"relation_project_id": project_a, "relation_id": None},
            {
                "relation_project_id": project_a,
                "relation_id": relation_a["id"],
            },
            {
                "relation_project_id": project_b,
                "relation_id": relation_a["id"],
            },
            {
                "retained_relation_project_id": None,
                "retained_relation_id": relation_a["id"],
            },
            {
                "retained_relation_project_id": project_a,
                "retained_relation_id": None,
            },
            {
                "retained_relation_project_id": project_a,
                "retained_relation_id": relation_a["id"],
            },
            {
                "retained_relation_project_id": project_b,
                "retained_relation_id": relation_a["id"],
            },
        ]
        for invalid_ref in invalid_relation_refs:
            session.add(relation_action(**invalid_ref))
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()


def test_preview_token_detects_tampering():
    with pytest.raises(LoreWriteError):
        decode_merge_preview_token("invalid.token")
