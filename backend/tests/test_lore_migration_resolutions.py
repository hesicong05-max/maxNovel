"""DEV-016A3b auditable legacy migration resolution tests."""

import asyncio

import pytest
from sqlalchemy import select, update

from app.config import settings as app_settings
from app.models.lore import (
    ElementSource,
    LegacyLoreResolution,
    LegacyLoreResolutionEvent,
)
from app.models.project import Project, Worldview


async def _project(client, headers) -> str:
    response = await client.post(
        "/api/projects",
        headers=headers,
        json={
            "title": "旧资料人工决定",
            "genre": "玄幻",
            "total_chapters": 10,
            "chapter_word_count": 1000,
            "style_intensity": "standard",
        },
    )
    assert response.status_code == 200
    project_id = response.json()["id"]
    from tests.conftest import TestSessionLocal
    async with TestSessionLocal() as session:
        await session.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(lore_storage_mode="legacy")
        )
        await session.commit()
    return project_id


async def _worldview(
    client,
    headers,
    project_id: str,
    *,
    duplicate: bool = False,
    character_name: str = "林岚",
    special_name: str = "夜禁",
):
    characters = [{
        "name": character_name,
        "personality": "沉稳",
        "background": "",
        "motivation": "寻找真相",
        "ability": "观星",
        "relations": [],
    }]
    if duplicate:
        characters.extend([{
            "name": " 林岚 ",
            "personality": "果断",
            "background": "",
            "motivation": "守护故乡",
            "ability": "剑术",
            "relations": [],
        }, {
            "name": "林岚",
            "personality": "谨慎",
            "background": "",
            "motivation": "记录历史",
            "ability": "速记",
            "relations": [],
        }])
    response = await client.post(
        f"/api/worldview/{project_id}",
        headers=headers,
        json={
            "characters": characters,
            "geography": [],
            "factions": [],
            "power_system": [],
            "history": [],
            "conflicts": [],
            "special_settings": [{"name": special_name, "description": "夜间不得飞行"}],
            "raw_text": None,
            "source": "manual",
        },
    )
    assert response.status_code == 200


async def _preview(client, headers, project_id: str) -> dict:
    response = await client.get(
        f"/api/projects/{project_id}/lore/migration-preview",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _decision_body(
    preview: dict,
    item: dict,
    *,
    operation_key: str,
    reason_code: str,
    decision_code: str,
    decision_payload: dict,
    expected_resolution_version: int | None = None,
) -> dict:
    return {
        "operation_key": operation_key,
        "preview_schema_version": preview["preview_schema_version"],
        "mapping_version": preview["mapping_version"],
        "expected_source_checksum": preview["source_checksum"],
        "expected_semantic_result_checksum": preview["semantic_result_checksum"],
        "item_fingerprint": item["item_fingerprint"],
        "group_fingerprint": item.get("group_fingerprint"),
        "legacy_category": item["legacy_category"],
        "legacy_index": item["legacy_index"],
        "reason_code": reason_code,
        "decision_code": decision_code,
        "decision_payload": decision_payload,
        "expected_resolution_version": expected_resolution_version,
    }


@pytest.mark.usefixtures("clean_db")
async def test_type_resolution_is_audited_idempotent_and_changes_only_effective_preview(
    client, auth_headers
):
    from tests.conftest import TestSessionLocal

    project_id = await _project(client, auth_headers)
    await _worldview(client, auth_headers, project_id)
    before = await _preview(client, auth_headers, project_id)
    item = next(
        row for row in before["items"] if row["legacy_category"] == "special_settings"
    )
    body = _decision_body(
        before,
        item,
        operation_key="resolution-type-0001",
        reason_code="type_confirmation_required",
        decision_code="confirm_type",
        decision_payload={"type_key": "rule"},
    )

    created = await client.post(
        f"/api/projects/{project_id}/lore/migration-resolutions",
        headers=auth_headers,
        json=body,
    )
    replay = await client.post(
        f"/api/projects/{project_id}/lore/migration-resolutions",
        headers=auth_headers,
        json=body,
    )
    after = await _preview(client, auth_headers, project_id)
    after_item = next(
        row for row in after["items"] if row["legacy_category"] == "special_settings"
    )

    assert created.status_code == 200, created.text
    assert created.json()["replayed"] is False
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert after_item["proposed_type_key"] is None
    assert after_item["effective_proposed_type_key"] == "rule"
    assert after_item["mapped_fields"] == {}
    assert after_item["effective_mapped_fields"]["description"] == "夜间不得飞行"
    assert after_item["effective_unmapped_fields"] == []
    assert after["by_target_type"]["rule"] == 1
    assert "type_confirmation_required" in after_item["reason_codes"]
    assert "type_confirmation_required" not in after_item["effective_reason_codes"]
    assert after["semantic_result_checksum"] != before["semantic_result_checksum"]

    async with TestSessionLocal() as session:
        assert len((await session.scalars(select(LegacyLoreResolution))).all()) == 1
        assert len((await session.scalars(select(LegacyLoreResolutionEvent))).all()) == 1
        worldview = await session.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        assert worldview.special_settings[0]["description"] == "夜间不得飞行"


@pytest.mark.usefixtures("clean_db")
async def test_commit_records_only_each_items_applied_resolution_ids(
    client, auth_headers, monkeypatch
):
    from tests.conftest import TestSessionLocal

    project_id = await _project(client, auth_headers)
    await _worldview(client, auth_headers, project_id)
    preview = await _preview(client, auth_headers, project_id)
    special = next(
        item for item in preview["items"] if item["legacy_category"] == "special_settings"
    )
    decision = await client.post(
        f"/api/projects/{project_id}/lore/migration-resolutions",
        headers=auth_headers,
        json=_decision_body(
            preview,
            special,
            operation_key="resolution-source-tracking-0001",
            reason_code="type_confirmation_required",
            decision_code="confirm_type",
            decision_payload={"type_key": "rule"},
        ),
    )
    assert decision.status_code == 200, decision.text
    resolution_id = decision.json()["resolution"]["id"]
    ready = await _preview(client, auth_headers, project_id)
    assert ready["overall_status"] == "ready", ready["issues"]
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    committed = await client.post(
        f"/api/projects/{project_id}/lore/migration-operations",
        headers=auth_headers,
        json={
            "operation_key": "resolution-source-commit-0001",
            "preview_schema_version": ready["preview_schema_version"],
            "mapping_version": ready["mapping_version"],
            "expected_source_checksum": ready["source_checksum"],
            "expected_semantic_result_checksum": ready["semantic_result_checksum"],
            "confirm_legacy_retained_no_automatic_rollback": True,
        },
    )
    assert committed.status_code == 200, committed.text

    async with TestSessionLocal() as session:
        sources = list((await session.scalars(
            select(ElementSource).where(ElementSource.project_id == project_id)
        )).all())
        by_category = {
            source.locator["legacy_category"]: source.locator for source in sources
        }
        assert by_category["special_settings"]["resolution_ids"] == [resolution_id]
        assert "resolution_ids" not in by_category["characters"]


@pytest.mark.usefixtures("clean_db")
async def test_type_change_expires_old_duplicate_group_resolution(
    client, auth_headers
):
    project_id = await _project(client, auth_headers)
    await _worldview(
        client,
        auth_headers,
        project_id,
        character_name="夜禁",
        special_name="夜禁",
    )
    preview = await _preview(client, auth_headers, project_id)
    character = next(
        item for item in preview["items"] if item["legacy_category"] == "characters"
    )
    group = [
        item for item in preview["items"]
        if item["group_fingerprint"] == character["group_fingerprint"]
    ]
    duplicate = await client.post(
        f"/api/projects/{project_id}/lore/migration-resolutions",
        headers=auth_headers,
        json=_decision_body(
            preview,
            character,
            operation_key="resolution-cross-group-0001",
            reason_code="duplicate_name_cross_type",
            decision_code="confirm_distinct_same_name",
            decision_payload={
                "member_fingerprints": sorted(
                    item["item_fingerprint"] for item in group
                )
            },
        ),
    )
    assert duplicate.status_code == 200, duplicate.text
    after_duplicate = await _preview(client, auth_headers, project_id)
    special = next(
        item for item in after_duplicate["items"]
        if item["legacy_category"] == "special_settings"
    )
    type_decision = await client.post(
        f"/api/projects/{project_id}/lore/migration-resolutions",
        headers=auth_headers,
        json=_decision_body(
            after_duplicate,
            special,
            operation_key="resolution-regroup-type-0001",
            reason_code="type_confirmation_required",
            decision_code="confirm_type",
            decision_payload={"type_key": "character"},
        ),
    )
    assert type_decision.status_code == 200, type_decision.text
    regrouped = await _preview(client, auth_headers, project_id)
    regrouped_character = next(
        item for item in regrouped["items"]
        if item["legacy_category"] == "characters"
    )
    assert "duplicate_name_same_type" in regrouped_character["effective_reason_codes"]
    duplicate_state = next(
        state for state in regrouped_character["resolution_states"]
        if state["id"] == duplicate.json()["resolution"]["id"]
    )
    assert duplicate_state["status"] == "expired"
    listed = await client.get(
        f"/api/projects/{project_id}/lore/migration-resolutions",
        headers=auth_headers,
    )
    listed_duplicate = next(
        row for row in listed.json()["items"]
        if row["id"] == duplicate.json()["resolution"]["id"]
    )
    assert listed_duplicate["status"] == "expired"


@pytest.mark.usefixtures("clean_db")
async def test_operation_key_conflict_and_non_whitelisted_reason_fail_closed(
    client, auth_headers
):
    project_id = await _project(client, auth_headers)
    await _worldview(client, auth_headers, project_id)
    preview = await _preview(client, auth_headers, project_id)
    item = next(
        row for row in preview["items"] if row["legacy_category"] == "special_settings"
    )
    body = _decision_body(
        preview,
        item,
        operation_key="resolution-conflict-0001",
        reason_code="type_confirmation_required",
        decision_code="confirm_type",
        decision_payload={"type_key": "rule"},
    )
    first = await client.post(
        f"/api/projects/{project_id}/lore/migration-resolutions",
        headers=auth_headers,
        json=body,
    )
    changed = await client.post(
        f"/api/projects/{project_id}/lore/migration-resolutions",
        headers=auth_headers,
        json={**body, "decision_payload": {"type_key": "other"}},
    )
    blocked = await client.post(
        f"/api/projects/{project_id}/lore/migration-resolutions",
        headers=auth_headers,
        json={
            **body,
            "operation_key": "resolution-blocked-0001",
            "reason_code": "missing_name",
        },
    )

    assert first.status_code == 200
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "LORE_MIGRATION_RESOLUTION_OPERATION_CONFLICT"
    assert blocked.status_code in {409, 422}


@pytest.mark.usefixtures("clean_db")
async def test_duplicate_name_resolution_is_bound_to_each_group_member(
    client, auth_headers
):
    project_id = await _project(client, auth_headers)
    await _worldview(client, auth_headers, project_id, duplicate=True)
    preview = await _preview(client, auth_headers, project_id)
    group = [
        item for item in preview["items"]
        if item["legacy_category"] == "characters"
    ]
    assert len({item["group_fingerprint"] for item in group}) == 1
    first_body = _decision_body(
        preview,
        group[0],
        operation_key="resolution-duplicate-group-0001",
        reason_code="duplicate_name_same_type",
        decision_code="confirm_distinct_same_name",
        decision_payload={
            "member_fingerprints": sorted(item["item_fingerprint"] for item in group)
        },
    )
    response = await client.post(
        f"/api/projects/{project_id}/lore/migration-resolutions",
        headers=auth_headers,
        json=first_body,
    )
    after = await _preview(client, auth_headers, project_id)
    after_group = [
        item for item in after["items"]
        if item["legacy_category"] == "characters"
    ]

    assert response.status_code == 200, response.text
    assert "duplicate_name_same_type" not in after_group[0]["effective_reason_codes"]
    assert all(
        "duplicate_name_same_type" in item["effective_reason_codes"]
        for item in after_group[1:]
    )

    for index, fingerprint in enumerate(
        [item["item_fingerprint"] for item in after_group[1:]], start=2
    ):
        current = await _preview(client, auth_headers, project_id)
        current_group = [
            item for item in current["items"]
            if item["legacy_category"] == "characters"
        ]
        current_item = next(
            item for item in current_group if item["item_fingerprint"] == fingerprint
        )
        decided = await client.post(
            f"/api/projects/{project_id}/lore/migration-resolutions",
            headers=auth_headers,
            json=_decision_body(
                current,
                current_item,
                operation_key=f"resolution-duplicate-member-000{index}",
                reason_code="duplicate_name_same_type",
                decision_code="confirm_distinct_same_name",
                decision_payload={
                    "member_fingerprints": sorted(
                        item["item_fingerprint"] for item in current_group
                    )
                },
            ),
        )
        assert decided.status_code == 200, decided.text

    final = await _preview(client, auth_headers, project_id)
    final_group = [
        item for item in final["items"] if item["legacy_category"] == "characters"
    ]
    assert all(
        "duplicate_name_same_type" not in item["effective_reason_codes"]
        for item in final_group
    )
    assert all(item["effective_classification"] == "mappable" for item in final_group)
    assert len({
        item["applied_resolution_ids"][0] for item in final_group
    }) == 3


@pytest.mark.usefixtures("clean_db")
async def test_source_change_expires_resolution_and_revoke_is_version_safe(
    client, auth_headers
):
    project_id = await _project(client, auth_headers)
    await _worldview(client, auth_headers, project_id)
    preview = await _preview(client, auth_headers, project_id)
    item = next(
        row for row in preview["items"] if row["legacy_category"] == "special_settings"
    )
    created = await client.post(
        f"/api/projects/{project_id}/lore/migration-resolutions",
        headers=auth_headers,
        json=_decision_body(
            preview,
            item,
            operation_key="resolution-expiry-0001",
            reason_code="type_confirmation_required",
            decision_code="confirm_type",
            decision_payload={"type_key": "rule"},
        ),
    )
    assert created.status_code == 200
    resolution = created.json()["resolution"]
    current = await client.get(
        f"/api/worldview/{project_id}", headers=auth_headers
    )
    updated_payload = current.json()
    updated_payload["special_settings"][0]["description"] = "夜间不得离城"
    updated_payload["expected_source_checksum"] = updated_payload["source_checksum"]
    for key in ("id", "project_id", "created_at", "updated_at", "source_checksum"):
        updated_payload.pop(key, None)
    updated = await client.post(
        f"/api/worldview/{project_id}", headers=auth_headers, json=updated_payload
    )
    assert updated.status_code == 200, updated.text

    listed = await client.get(
        f"/api/projects/{project_id}/lore/migration-resolutions",
        headers=auth_headers,
    )
    stale_revoke = await client.post(
        f"/api/projects/{project_id}/lore/migration-resolutions/{resolution['id']}/revoke",
        headers=auth_headers,
        json={
            "operation_key": "resolution-revoke-stale-0001",
            "expected_source_checksum": preview["source_checksum"],
            "expected_resolution_version": resolution["lock_version"],
        },
    )
    assert listed.status_code == 200
    assert listed.json()["items"][0]["status"] == "expired"
    assert stale_revoke.status_code == 409


@pytest.mark.usefixtures("clean_db")
async def test_resolution_write_is_blocked_during_maintenance(
    client, auth_headers, monkeypatch
):
    project_id = await _project(client, auth_headers)
    await _worldview(client, auth_headers, project_id)
    preview = await _preview(client, auth_headers, project_id)
    item = next(
        row for row in preview["items"] if row["legacy_category"] == "special_settings"
    )
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    response = await client.post(
        f"/api/projects/{project_id}/lore/migration-resolutions",
        headers=auth_headers,
        json=_decision_body(
            preview,
            item,
            operation_key="resolution-maintenance-0001",
            reason_code="type_confirmation_required",
            decision_code="confirm_type",
            decision_payload={"type_key": "rule"},
        ),
    )
    assert response.status_code == 503


@pytest.mark.usefixtures("clean_db")
async def test_maintenance_flip_before_commit_rolls_back_resolution_and_event(
    client, auth_headers, monkeypatch
):
    from app.core import lore_migration_resolution as resolution_module
    from tests.conftest import TestSessionLocal

    project_id = await _project(client, auth_headers)
    await _worldview(client, auth_headers, project_id)
    preview = await _preview(client, auth_headers, project_id)
    item = next(
        row for row in preview["items"] if row["legacy_category"] == "special_settings"
    )
    original_gate = resolution_module._ensure_resolution_writes_available

    def freeze_at_commit_gate():
        monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
        original_gate()

    monkeypatch.setattr(
        resolution_module,
        "_ensure_resolution_writes_available",
        freeze_at_commit_gate,
    )
    response = await client.post(
        f"/api/projects/{project_id}/lore/migration-resolutions",
        headers=auth_headers,
        json=_decision_body(
            preview,
            item,
            operation_key="resolution-maintenance-race-0001",
            reason_code="type_confirmation_required",
            decision_code="confirm_type",
            decision_payload={"type_key": "rule"},
        ),
    )
    assert response.status_code == 503
    async with TestSessionLocal() as session:
        assert list((await session.scalars(select(LegacyLoreResolution))).all()) == []
        assert list((await session.scalars(select(LegacyLoreResolutionEvent))).all()) == []


@pytest.mark.usefixtures("clean_db")
async def test_postgres_concurrent_same_version_resolution_has_one_winner(
    client, auth_headers
):
    from tests.conftest import TEST_DATABASE_BACKEND

    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("PostgreSQL row-lock behavior")
    project_id = await _project(client, auth_headers)
    await _worldview(client, auth_headers, project_id)
    preview = await _preview(client, auth_headers, project_id)
    item = next(
        row for row in preview["items"] if row["legacy_category"] == "special_settings"
    )
    base = _decision_body(
        preview,
        item,
        operation_key="resolution-concurrent-a-0001",
        reason_code="type_confirmation_required",
        decision_code="confirm_type",
        decision_payload={"type_key": "rule"},
    )
    first, second = await asyncio.gather(
        client.post(
            f"/api/projects/{project_id}/lore/migration-resolutions",
            headers=auth_headers,
            json=base,
        ),
        client.post(
            f"/api/projects/{project_id}/lore/migration-resolutions",
            headers=auth_headers,
            json={
                **base,
                "operation_key": "resolution-concurrent-b-0001",
                "decision_payload": {"type_key": "other"},
            },
        ),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
