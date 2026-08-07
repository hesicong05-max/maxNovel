"""DEV-016A1 safe legacy project upgrade behavior."""

import asyncio
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.core.lore_migration_commit import (
    LoreMigrationCommitError,
    commit_lore_migration,
)
from app.core.lore_migration_preview import migration_preview_source_checksum
from app.models.lore import (
    ElementSource,
    LegacyElementMap,
    ProjectLoreMigration,
    ProjectLoreMigrationOperation,
    SettingElement,
    SettingType,
)
from app.models.project import Project, Worldview
from app.schemas.lore import LoreMigrationCommitInput


async def _legacy_project(client, headers) -> tuple[str, str]:
    response = await client.post(
        "/api/projects",
        headers=headers,
        json={
            "title": "旧项目安全升级",
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
        project = await session.scalar(select(Project).where(Project.id == project_id))
        user_id = project.owner_id
        await session.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(lore_storage_mode="legacy", lore_migration_version=None)
        )
        await session.commit()
    return project_id, user_id


def _worldview_payload(name: str = "林岚") -> dict:
    return {
        "characters": [{
            "name": name,
            "personality": "沉稳",
            "background": "来自云港",
            "motivation": "寻找真相",
            "ability": "观星",
            "relations": [],
        }],
        "geography": [{
            "name": "云港",
            "description": "浮空港口",
            "significance": "故事起点",
        }],
        "factions": [],
        "power_system": [{
            "name": "灵阶",
            "levels": "九阶",
            "rules": "逐级修炼",
            "limitations": "不可越阶",
        }],
        "history": [],
        "conflicts": [],
        "special_settings": [],
        "raw_text": None,
        "source": "manual",
    }


async def _save_worldview(
    client,
    headers,
    project_id: str,
    *,
    name: str = "林岚",
    payload: dict | None = None,
):
    current = await client.get(f"/api/worldview/{project_id}", headers=headers)
    expected_source_checksum = (
        current.json()["source_checksum"] if current.status_code == 200 else None
    )
    request_payload = dict(payload or _worldview_payload(name))
    request_payload["expected_source_checksum"] = expected_source_checksum
    response = await client.post(
        f"/api/worldview/{project_id}",
        headers=headers,
        json=request_payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _preview_and_body(client, headers, project_id: str, operation_key: str):
    response = await client.get(
        f"/api/projects/{project_id}/lore/migration-preview",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["overall_status"] == "ready", preview["issues"]
    body = {
        "operation_key": operation_key,
        "preview_schema_version": preview["preview_schema_version"],
        "mapping_version": preview["mapping_version"],
        "expected_source_checksum": preview["source_checksum"],
        "expected_semantic_result_checksum": preview["semantic_result_checksum"],
        "confirm_legacy_retained_no_automatic_rollback": True,
    }
    return preview, body


async def _business_counts(project_id: str) -> dict[str, int]:
    from tests.conftest import TestSessionLocal

    result = {}
    async with TestSessionLocal() as session:
        for name, model in (
            ("types", SettingType),
            ("elements", SettingElement),
            ("sources", ElementSource),
            ("maps", LegacyElementMap),
            ("migrations", ProjectLoreMigration),
            ("operations", ProjectLoreMigrationOperation),
        ):
            result[name] = int(await session.scalar(
                select(func.count()).select_from(model).where(model.project_id == project_id)
            ) or 0)
    return result


@pytest.mark.usefixtures("clean_db")
async def test_legacy_worldview_updates_in_place_before_migration(
    client, auth_headers
):
    project_id, _ = await _legacy_project(client, auth_headers)
    first = await _save_worldview(client, auth_headers, project_id, name="林岚")
    second = await _save_worldview(client, auth_headers, project_id, name="林岚·修订")

    assert second["id"] == first["id"]
    assert second["characters"][0]["name"] == "林岚·修订"


@pytest.mark.usefixtures("clean_db")
async def test_api_commit_is_ready_idempotent_and_preserves_legacy_source(
    client, auth_headers, monkeypatch
):
    from tests.conftest import TestSessionLocal

    project_id, _ = await _legacy_project(client, auth_headers)
    saved = await _save_worldview(client, auth_headers, project_id)
    preview, body = await _preview_and_body(
        client, auth_headers, project_id, "migration-operation-0001"
    )
    async with TestSessionLocal() as session:
        worldview = await session.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        source_id = worldview.id
        source_checksum = migration_preview_source_checksum(worldview)

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    response = await client.post(
        f"/api/projects/{project_id}/lore/migration-operations",
        headers=auth_headers,
        json=body,
    )
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["status"] == "ready"
    assert receipt["replayed"] is False
    assert receipt["counts"]["elements"] == preview["counts"]["legacy_total"]
    assert receipt["counts"]["legacy_rows_deleted"] == 0

    replay = await client.post(
        f"/api/projects/{project_id}/lore/migration-operations",
        headers=auth_headers,
        json=body,
    )
    lookup = await client.get(
        f"/api/projects/{project_id}/lore/migration-operations/by-key/"
        f"{body['operation_key']}",
        headers=auth_headers,
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert lookup.status_code == 200
    assert lookup.json()["id"] == receipt["id"]

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", False)
    replay_without_freeze = await client.post(
        f"/api/projects/{project_id}/lore/migration-operations",
        headers=auth_headers,
        json=body,
    )
    assert replay_without_freeze.status_code == 200
    assert replay_without_freeze.json()["replayed"] is True

    async with TestSessionLocal() as session:
        project = await session.scalar(select(Project).where(Project.id == project_id))
        worldview = await session.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        assert project.lore_storage_mode == "relational"
        assert worldview.id == source_id == saved["id"]
        assert migration_preview_source_checksum(worldview) == source_checksum
    counts = await _business_counts(project_id)
    assert counts["operations"] == 1
    assert counts["migrations"] == 1
    assert counts["elements"] == preview["counts"]["legacy_total"]

    rejected_save = await client.post(
        f"/api/worldview/{project_id}",
        headers=auth_headers,
        json=_worldview_payload("被拒绝的覆盖"),
    )
    assert rejected_save.status_code == 409
    assert rejected_save.json()["detail"]["code"] == "WORLDVIEW_SOURCE_READ_ONLY"


@pytest.mark.usefixtures("clean_db")
async def test_commit_requires_freeze_and_rejects_stale_or_reused_key(
    client, auth_headers, monkeypatch
):
    project_id, _ = await _legacy_project(client, auth_headers)
    await _save_worldview(client, auth_headers, project_id)
    _, body = await _preview_and_body(
        client, auth_headers, project_id, "migration-operation-0002"
    )

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", False)
    no_freeze = await client.post(
        f"/api/projects/{project_id}/lore/migration-operations",
        headers=auth_headers,
        json=body,
    )
    assert no_freeze.status_code == 503
    assert (await _business_counts(project_id))["operations"] == 0

    stale = dict(body)
    stale["expected_source_checksum"] = "0" * 64
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    stale_response = await client.post(
        f"/api/projects/{project_id}/lore/migration-operations",
        headers=auth_headers,
        json=stale,
    )
    assert stale_response.status_code == 409
    assert stale_response.json()["detail"]["code"] == "LORE_MIGRATION_PREVIEW_STALE"

    success = await client.post(
        f"/api/projects/{project_id}/lore/migration-operations",
        headers=auth_headers,
        json=body,
    )
    assert success.status_code == 200
    reused = dict(body)
    reused["expected_semantic_result_checksum"] = "1" * 64
    conflict = await client.post(
        f"/api/projects/{project_id}/lore/migration-operations",
        headers=auth_headers,
        json=reused,
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "LORE_MIGRATION_OPERATION_KEY_CONFLICT"


@pytest.mark.usefixtures("clean_db")
async def test_migration_operation_is_owner_scoped(
    client, auth_headers, second_auth_headers, monkeypatch
):
    project_id, _ = await _legacy_project(client, auth_headers)
    await _save_worldview(client, auth_headers, project_id)
    _, body = await _preview_and_body(
        client, auth_headers, project_id, "migration-owner-scope-0001"
    )
    missing = await client.get(
        f"/api/projects/{project_id}/lore/migration-operations/by-key/"
        "migration-missing-key-0001",
        headers=auth_headers,
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "LORE_MIGRATION_OPERATION_NOT_FOUND"
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    created = await client.post(
        f"/api/projects/{project_id}/lore/migration-operations",
        headers=auth_headers,
        json=body,
    )
    assert created.status_code == 200

    forbidden_create = await client.post(
        f"/api/projects/{project_id}/lore/migration-operations",
        headers=second_auth_headers,
        json=body,
    )
    forbidden_read = await client.get(
        f"/api/projects/{project_id}/lore/migration-operations/by-key/"
        f"{body['operation_key']}",
        headers=second_auth_headers,
    )
    assert forbidden_create.status_code == 403
    assert forbidden_read.status_code == 403


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize(
    "fault_at",
    [
        "after_types",
        "after_elements",
        "after_materialization",
        "before_materialization_commit",
    ],
)
async def test_precommit_faults_leave_no_receipt_or_relational_rows(
    client, auth_headers, monkeypatch, fault_at
):
    from tests.conftest import TestSessionLocal

    project_id, user_id = await _legacy_project(client, auth_headers)
    await _save_worldview(client, auth_headers, project_id)
    _, body_data = await _preview_and_body(
        client, auth_headers, project_id, f"migration-precommit-{fault_at}"
    )
    body = LoreMigrationCommitInput(**body_data)
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)

    with pytest.raises(LoreMigrationCommitError):
        await commit_lore_migration(
            TestSessionLocal, project_id, user_id, body, fault_at=fault_at
        )

    counts = await _business_counts(project_id)
    assert not any(counts.values())
    async with TestSessionLocal() as session:
        mode = await session.scalar(
            select(Project.lore_storage_mode).where(Project.id == project_id)
        )
        assert mode == "legacy"


@pytest.mark.usefixtures("clean_db")
async def test_unknown_after_materialization_resumes_same_operation(
    client, auth_headers, monkeypatch
):
    from tests.conftest import TestSessionLocal

    project_id, user_id = await _legacy_project(client, auth_headers)
    await _save_worldview(client, auth_headers, project_id)
    _, body_data = await _preview_and_body(
        client, auth_headers, project_id, "migration-operation-unknown-1"
    )
    body = LoreMigrationCommitInput(**body_data)
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)

    with pytest.raises(LoreMigrationCommitError) as caught:
        await commit_lore_migration(
            TestSessionLocal,
            project_id,
            user_id,
            body,
            fault_at="after_materialization_commit_unknown",
        )
    assert caught.value.outcome_unknown is True
    async with TestSessionLocal() as session:
        project = await session.scalar(select(Project).where(Project.id == project_id))
        operation = await session.scalar(select(ProjectLoreMigrationOperation).where(
            ProjectLoreMigrationOperation.project_id == project_id
        ))
        assert project.lore_storage_mode == "migrating"
        assert operation.status == "validating"

    overview = await client.get(
        f"/api/projects/{project_id}/lore/overview",
        headers=auth_headers,
    )
    listing = await client.get(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
    )
    assert overview.status_code == 200
    assert overview.json()["migration_status"] == {
        "storage_mode": "migrating",
        "state": "validating",
        "read_only": True,
        "processed_count": 3,
        "total_count": 3,
        "started_at": None,
        "updated_at": None,
        "error_category": None,
        "can_retry": True,
    }
    assert listing.status_code == 200
    assert listing.json()["migration_status"]["state"] == "validating"

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", False)
    with pytest.raises(LoreMigrationCommitError) as freeze_lost:
        await commit_lore_migration(TestSessionLocal, project_id, user_id, body)
    assert freeze_lost.value.outcome_unknown is True
    async with TestSessionLocal() as session:
        project = await session.scalar(select(Project).where(Project.id == project_id))
        operation = await session.scalar(select(ProjectLoreMigrationOperation).where(
            ProjectLoreMigrationOperation.project_id == project_id
        ))
        assert project.lore_storage_mode == "migrating"
        assert operation.status == "validating"

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    resumed = await commit_lore_migration(
        TestSessionLocal, project_id, user_id, body
    )
    assert resumed.status == "ready"
    assert resumed.replayed is True
    assert (await _business_counts(project_id))["operations"] == 1


@pytest.mark.usefixtures("clean_db")
async def test_unknown_after_final_commit_is_resolved_by_receipt(
    client, auth_headers, monkeypatch
):
    from tests.conftest import TestSessionLocal

    project_id, user_id = await _legacy_project(client, auth_headers)
    await _save_worldview(client, auth_headers, project_id)
    _, body_data = await _preview_and_body(
        client, auth_headers, project_id, "migration-operation-unknown-final"
    )
    body = LoreMigrationCommitInput(**body_data)
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)

    with pytest.raises(LoreMigrationCommitError) as caught:
        await commit_lore_migration(
            TestSessionLocal,
            project_id,
            user_id,
            body,
            fault_at="after_final_commit_unknown",
        )
    assert caught.value.outcome_unknown is True

    replay = await commit_lore_migration(
        TestSessionLocal, project_id, user_id, body
    )
    assert replay.status == "ready"
    assert replay.replayed is True
    counts = await _business_counts(project_id)
    assert counts["operations"] == counts["migrations"] == 1


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize(
    ("raise_on_commit", "expected_intermediate_mode"),
    [(1, "migrating"), (2, "relational")],
)
async def test_real_commit_disconnect_is_structured_and_same_key_resolves(
    client,
    auth_headers,
    monkeypatch,
    raise_on_commit,
    expected_intermediate_mode,
):
    from tests.conftest import TestSessionLocal

    project_id, user_id = await _legacy_project(client, auth_headers)
    await _save_worldview(client, auth_headers, project_id)
    _, body_data = await _preview_and_body(
        client,
        auth_headers,
        project_id,
        f"migration-real-commit-unknown-{raise_on_commit}",
    )
    body = LoreMigrationCommitInput(**body_data)
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    original_commit = AsyncSession.commit
    commit_count = 0

    async def commit_then_disconnect(session):
        nonlocal commit_count
        await original_commit(session)
        commit_count += 1
        if commit_count == raise_on_commit:
            raise ConnectionError("simulated disconnect after database commit")

    monkeypatch.setattr(AsyncSession, "commit", commit_then_disconnect)
    with pytest.raises(LoreMigrationCommitError) as caught:
        await commit_lore_migration(TestSessionLocal, project_id, user_id, body)
    assert caught.value.detail == {
        "code": "LORE_MIGRATION_OUTCOME_UNKNOWN",
        "message": (
            "升级提交结果暂时无法确认，请使用原操作标识查询。"
            if raise_on_commit == 1
            else "升级最终结果暂时无法确认，请使用原操作标识查询。"
        ),
        "retryable": True,
        "outcome_unknown": True,
    }
    async with TestSessionLocal() as session:
        mode = await session.scalar(
            select(Project.lore_storage_mode).where(Project.id == project_id)
        )
        assert mode == expected_intermediate_mode

    monkeypatch.setattr(AsyncSession, "commit", original_commit)
    replay = await commit_lore_migration(
        TestSessionLocal, project_id, user_id, body
    )
    assert replay.status == "ready"
    assert replay.replayed is True
    counts = await _business_counts(project_id)
    assert counts["operations"] == counts["migrations"] == 1


@pytest.mark.usefixtures("clean_db")
async def test_fresh_validation_failure_compensates_only_new_rows(
    client, auth_headers, monkeypatch
):
    from tests.conftest import TestSessionLocal

    project_id, user_id = await _legacy_project(client, auth_headers)
    saved = await _save_worldview(client, auth_headers, project_id)
    _, body_data = await _preview_and_body(
        client, auth_headers, project_id, "migration-operation-failed-1"
    )
    body = LoreMigrationCommitInput(**body_data)
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)

    receipt = await commit_lore_migration(
        TestSessionLocal,
        project_id,
        user_id,
        body,
        fault_at="during_fresh_validation",
    )
    assert receipt.status == "failed"
    assert receipt.error_code == "LORE_MIGRATION_VALIDATION_FAILED"
    counts = await _business_counts(project_id)
    assert counts == {
        "types": 0,
        "elements": 0,
        "sources": 0,
        "maps": 0,
        "migrations": 0,
        "operations": 1,
    }
    async with TestSessionLocal() as session:
        project = await session.scalar(select(Project).where(Project.id == project_id))
        worldview = await session.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        assert project.lore_storage_mode == "legacy"
        assert worldview.id == saved["id"]


@pytest.mark.usefixtures("clean_db")
async def test_unexpected_relational_row_blocks_compensation_without_deleting_it(
    client, auth_headers, monkeypatch
):
    from tests.conftest import TestSessionLocal

    project_id, user_id = await _legacy_project(client, auth_headers)
    await _save_worldview(client, auth_headers, project_id)
    preview, body_data = await _preview_and_body(
        client, auth_headers, project_id, "migration-unexpected-row-0001"
    )
    body = LoreMigrationCommitInput(**body_data)
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    with pytest.raises(LoreMigrationCommitError):
        await commit_lore_migration(
            TestSessionLocal,
            project_id,
            user_id,
            body,
            fault_at="after_materialization_commit_unknown",
        )

    foreign_element_id = "f" * 32
    async with TestSessionLocal() as session:
        setting_type = await session.scalar(
            select(SettingType)
            .where(SettingType.project_id == project_id)
            .order_by(SettingType.key)
        )
        session.add(SettingElement(
            id=foreign_element_id,
            project_id=project_id,
            type_id=setting_type.id,
            name="非本次操作创建的资料",
            normalized_name="非本次操作创建的资料",
            payload={},
            field_states={},
            confirmation_status="confirmed",
            lifecycle_status="active",
            enabled=True,
            content_version=1,
            lock_version=1,
        ))
        await session.commit()

    with pytest.raises(LoreMigrationCommitError) as caught:
        await commit_lore_migration(TestSessionLocal, project_id, user_id, body)
    assert caught.value.outcome_unknown is True
    async with TestSessionLocal() as session:
        project = await session.scalar(select(Project).where(Project.id == project_id))
        operation = await session.scalar(select(ProjectLoreMigrationOperation).where(
            ProjectLoreMigrationOperation.project_id == project_id
        ))
        foreign = await session.scalar(select(SettingElement).where(
            SettingElement.id == foreign_element_id
        ))
        element_count = int(await session.scalar(
            select(func.count()).select_from(SettingElement).where(
                SettingElement.project_id == project_id
            )
        ) or 0)
        assert project.lore_storage_mode == "migrating"
        assert operation.status == "validating"
        assert foreign is not None
        assert element_count == preview["counts"]["legacy_total"] + 1


@pytest.mark.usefixtures("clean_db")
async def test_postgres_same_key_concurrency_is_exactly_once(
    client, auth_headers, monkeypatch
):
    from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal

    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("PostgreSQL row-lock behavior")
    project_id, user_id = await _legacy_project(client, auth_headers)
    await _save_worldview(client, auth_headers, project_id)
    _, body_data = await _preview_and_body(
        client, auth_headers, project_id, "migration-concurrent-same-key"
    )
    body = LoreMigrationCommitInput(**body_data)
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)

    first, second = await asyncio.gather(
        commit_lore_migration(TestSessionLocal, project_id, user_id, body),
        commit_lore_migration(TestSessionLocal, project_id, user_id, body),
    )

    assert {first.status, second.status} == {"ready"}
    assert sorted([first.replayed, second.replayed]) == [False, True]
    counts = await _business_counts(project_id)
    assert counts["operations"] == counts["migrations"] == 1


@pytest.mark.usefixtures("clean_db")
async def test_one_thousand_items_succeed_and_compensate_without_parameter_overflow(
    client, auth_headers, monkeypatch
):
    from tests.conftest import TestSessionLocal

    payload = _worldview_payload()
    payload["characters"] = [{
        "name": f"角色{i:04d}",
        "personality": "沉稳",
        "background": "来自云港",
        "motivation": "寻找真相",
        "ability": "观星",
        "relations": [],
    } for i in range(1000)]
    payload["geography"] = []
    payload["power_system"] = []
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", False)

    success_project, success_user = await _legacy_project(client, auth_headers)
    await _save_worldview(
        client, auth_headers, success_project, payload=payload
    )
    success_preview, success_body_data = await _preview_and_body(
        client, auth_headers, success_project, "migration-one-thousand-success"
    )
    assert success_preview["counts"]["legacy_total"] == 1000
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    success = await commit_lore_migration(
        TestSessionLocal,
        success_project,
        success_user,
        LoreMigrationCommitInput(**success_body_data),
    )
    assert success.status == "ready"
    assert (await _business_counts(success_project))["elements"] == 1000

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", False)
    failed_project, failed_user = await _legacy_project(client, auth_headers)
    await _save_worldview(client, auth_headers, failed_project, payload=payload)
    failed_preview, failed_body_data = await _preview_and_body(
        client, auth_headers, failed_project, "migration-one-thousand-compensation"
    )
    assert failed_preview["counts"]["legacy_total"] == 1000
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    failed = await commit_lore_migration(
        TestSessionLocal,
        failed_project,
        failed_user,
        LoreMigrationCommitInput(**failed_body_data),
        fault_at="during_fresh_validation",
    )
    assert failed.status == "failed"
    assert await _business_counts(failed_project) == {
        "types": 0,
        "elements": 0,
        "sources": 0,
        "maps": 0,
        "migrations": 0,
        "operations": 1,
    }
