"""DEV-003C2 isolated commit, validation and compensating rollback proofs."""

import json
import secrets
from dataclasses import replace

import pytest
from sqlalchemy import func, select, text

from app.config import settings as app_settings
from app.core.lore_migration_preview import build_migration_preview
from app.core.lore_migration import project_legacy_worldview
from app.core.lore_migration_rehearsal import (
    LoreMigrationRehearsalError,
    RehearsalGuard,
    build_rehearsal_anchor,
    build_sanitized_rehearsal_report,
    commit_rehearsal,
    compensating_rollback_rehearsal,
    validate_rehearsal,
    validate_rehearsal_baseline,
)
from app.models.lore import (
    ElementSource,
    ElementStateEvent,
    ElementVersion,
    LegacyElementMap,
    ProjectLoreMigration,
    SettingElement,
    SettingType,
)
from app.models.project import Project, Worldview
from app.models.user import User


_ISOLATION_NONCE = secrets.token_urlsafe(32)
GUARD = RehearsalGuard(
    environment_kind="test",
    synthetic_fixture=True,
    isolated_database=True,
    all_application_instances_frozen=True,
    isolation_nonce=_ISOLATION_NONCE,
)


async def _fixture_project(*, character_count: int = 1):
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as session:
        await session.execute(text(
            "CREATE TABLE IF NOT EXISTS lore_rehearsal_sentinel "
            "(nonce VARCHAR(128) PRIMARY KEY)"
        ))
        existing_nonce = await session.scalar(text(
            "SELECT nonce FROM lore_rehearsal_sentinel WHERE nonce = :nonce"
        ), {"nonce": _ISOLATION_NONCE})
        if existing_nonce is None:
            await session.execute(text(
                "INSERT INTO lore_rehearsal_sentinel (nonce) VALUES (:nonce)"
            ), {"nonce": _ISOLATION_NONCE})
        user = User(
            email="rehearsal@example.test",
            username="rehearsal-owner",
            hashed_password="not-a-real-password",
        )
        session.add(user)
        await session.flush()
        project = Project(
            title="隔离迁移演练",
            genre="玄幻",
            owner_id=user.id,
            lore_storage_mode="legacy",
        )
        session.add(project)
        await session.flush()
        worldview = Worldview(
            project_id=project.id,
            characters=[
                {
                    "name": f"角色{i}",
                    "personality": "沉稳",
                    "background": "来自云港",
                    "motivation": "寻找真相",
                    "ability": "观星",
                }
                for i in range(character_count)
            ],
            geography=[{
                "name": "云港",
                "description": "浮空港口",
                "significance": "故事起点",
            }],
            factions=[],
            power_system=[{
                "name": "灵阶",
                "levels": "九阶",
                "rules": "逐级修炼",
                "limitations": "不可越阶",
            }],
            history=[{
                "event": "星落之夜",
                "time": "百年前",
                "description": "群星坠落",
                "impact": "旧王朝终结",
            }],
            conflicts=[],
            special_settings=[],
            parsed_elements=[],
            raw_text=None,
            source="manual",
        )
        session.add(worldview)
        await session.commit()
        project_id = project.id

    async with TestSessionLocal() as session:
        worldview = await session.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        preview = build_migration_preview(project_id, "legacy", worldview)
    assert preview["overall_status"] == "ready", preview["issues"]
    return project_id, build_rehearsal_anchor(preview), preview


async def _project_mode(project_id: str):
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as session:
        return (await session.execute(
            select(Project.lore_storage_mode, Project.lore_migration_version)
            .where(Project.id == project_id)
        )).one()


async def _business_counts(project_id: str) -> dict[str, int]:
    from tests.conftest import TestSessionLocal

    models = {
        "types": SettingType,
        "elements": SettingElement,
        "sources": ElementSource,
        "maps": LegacyElementMap,
        "migrations": ProjectLoreMigration,
    }
    async with TestSessionLocal() as session:
        result = {}
        for name, model in models.items():
            result[name] = int(await session.scalar(
                select(func.count()).select_from(model).where(model.project_id == project_id)
            ) or 0)
        element_ids = select(SettingElement.id).where(SettingElement.project_id == project_id)
        result["versions"] = int(await session.scalar(
            select(func.count()).select_from(ElementVersion).where(
                ElementVersion.element_id.in_(element_ids)
            )
        ) or 0)
        result["events"] = int(await session.scalar(
            select(func.count()).select_from(ElementStateEvent).where(
                ElementStateEvent.element_id.in_(element_ids)
            )
        ) or 0)
        return result


@pytest.mark.usefixtures("clean_db")
async def test_commit_validate_replay_and_compensating_rollback(monkeypatch):
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    project_id, anchor, preview = await _fixture_project()
    source_before = anchor.expected_source_checksum

    receipt = await commit_rehearsal(TestSessionLocal, anchor, GUARD)
    validation = await validate_rehearsal(TestSessionLocal, anchor, GUARD)
    replay = await commit_rehearsal(TestSessionLocal, anchor, GUARD)

    assert receipt.replayed is False
    assert replay.replayed is True
    assert replay.migration_id == receipt.migration_id
    assert validation["status"] == "passed"
    assert validation["counts"]["elements"] == preview["counts"]["legacy_total"]
    assert validation["legacy_rows_deleted"] == 0
    assert await _project_mode(project_id) == ("relational", anchor.mapping_version)

    await compensating_rollback_rehearsal(TestSessionLocal, anchor, GUARD)
    baseline = await validate_rehearsal_baseline(TestSessionLocal, anchor, GUARD)

    assert baseline["status"] == "passed"
    assert baseline["source_checksum"] == source_before
    assert await _project_mode(project_id) == ("legacy", None)
    assert not any((await _business_counts(project_id)).values())

    second_receipt = await commit_rehearsal(TestSessionLocal, anchor, GUARD)
    assert second_receipt.replayed is False
    await validate_rehearsal(TestSessionLocal, anchor, GUARD)
    await compensating_rollback_rehearsal(TestSessionLocal, anchor, GUARD)


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize(
    "fault_at",
    [
        "after_types",
        "after_first_element",
        "after_first_source",
        "after_first_version",
        "after_first_map",
        "after_audit",
        "after_mode_switch",
        "before_commit",
    ],
)
async def test_commit_faults_are_atomic(monkeypatch, fault_at):
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    project_id, anchor, _ = await _fixture_project()

    with pytest.raises(LoreMigrationRehearsalError) as error:
        await commit_rehearsal(TestSessionLocal, anchor, GUARD, fault_at=fault_at)

    assert error.value.code == "REHEARSAL_FAULT_INJECTED"
    assert await _project_mode(project_id) == ("legacy", None)
    assert not any((await _business_counts(project_id)).values())


@pytest.mark.usefixtures("clean_db")
async def test_unknown_commit_result_is_resolved_by_fresh_validation(monkeypatch):
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    project_id, anchor, _ = await _fixture_project()

    with pytest.raises(LoreMigrationRehearsalError) as error:
        await commit_rehearsal(
            TestSessionLocal, anchor, GUARD, fault_at="after_commit_unknown"
        )

    assert error.value.outcome_unknown is True
    assert await _project_mode(project_id) == ("relational", anchor.mapping_version)
    assert (await validate_rehearsal(TestSessionLocal, anchor, GUARD))["status"] == "passed"
    replay = await commit_rehearsal(TestSessionLocal, anchor, GUARD)
    assert replay.replayed is True
    await compensating_rollback_rehearsal(TestSessionLocal, anchor, GUARD)


@pytest.mark.usefixtures("clean_db")
async def test_same_operation_key_with_different_semantic_checksum_conflicts(monkeypatch):
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    _, anchor, _ = await _fixture_project()
    await commit_rehearsal(TestSessionLocal, anchor, GUARD)
    conflicting = replace(
        anchor,
        expected_semantic_result_checksum="f" * 64,
    )

    with pytest.raises(LoreMigrationRehearsalError) as error:
        await commit_rehearsal(TestSessionLocal, conflicting, GUARD)

    assert error.value.code == "REHEARSAL_IDEMPOTENCY_CONFLICT"
    await compensating_rollback_rehearsal(TestSessionLocal, anchor, GUARD)


@pytest.mark.usefixtures("clean_db")
async def test_validation_failure_never_auto_deletes_committed_rows(monkeypatch):
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    project_id, anchor, _ = await _fixture_project()
    await commit_rehearsal(TestSessionLocal, anchor, GUARD)

    with pytest.raises(LoreMigrationRehearsalError) as error:
        await validate_rehearsal(
            TestSessionLocal, anchor, GUARD, fault_at="post_commit_validation"
        )

    assert error.value.phase == "post_commit_validation"
    assert await _project_mode(project_id) == ("relational", anchor.mapping_version)
    assert (await _business_counts(project_id))["elements"] > 0
    await compensating_rollback_rehearsal(TestSessionLocal, anchor, GUARD)


@pytest.mark.usefixtures("clean_db")
async def test_compensating_rollback_failure_restores_relational_state(monkeypatch):
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    project_id, anchor, _ = await _fixture_project()
    await commit_rehearsal(TestSessionLocal, anchor, GUARD)
    counts_before = await _business_counts(project_id)

    with pytest.raises(LoreMigrationRehearsalError):
        await compensating_rollback_rehearsal(
            TestSessionLocal,
            anchor,
            GUARD,
            fault_at="during_compensating_cleanup",
        )

    assert await _project_mode(project_id) == ("relational", anchor.mapping_version)
    assert await _business_counts(project_id) == counts_before
    assert (await validate_rehearsal(TestSessionLocal, anchor, GUARD))["status"] == "passed"
    await compensating_rollback_rehearsal(TestSessionLocal, anchor, GUARD)


@pytest.mark.usefixtures("clean_db")
async def test_changed_relational_content_blocks_compensating_rollback(monkeypatch):
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    project_id, anchor, _ = await _fixture_project()
    await commit_rehearsal(TestSessionLocal, anchor, GUARD)
    async with TestSessionLocal() as session:
        element = await session.scalar(
            select(SettingElement).where(SettingElement.project_id == project_id)
        )
        element.name = "迁移后的编辑"
        element.content_version = 2
        await session.commit()

    with pytest.raises(LoreMigrationRehearsalError) as error:
        await compensating_rollback_rehearsal(TestSessionLocal, anchor, GUARD)

    assert error.value.code == "ROLLBACK_UNSAFE"
    assert await _project_mode(project_id) == ("relational", anchor.mapping_version)
    assert (await _business_counts(project_id))["elements"] > 0


@pytest.mark.usefixtures("clean_db")
async def test_additional_source_blocks_compensating_rollback(monkeypatch):
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    project_id, anchor, _ = await _fixture_project()
    await commit_rehearsal(TestSessionLocal, anchor, GUARD)
    async with TestSessionLocal() as session:
        element_id = await session.scalar(
            select(SettingElement.id).where(SettingElement.project_id == project_id)
        )
        session.add(ElementSource(
            project_id=project_id,
            element_id=element_id,
            source_kind="manual",
            source_ref="post-migration-edit",
            locator={},
            excerpt="额外来源",
            confirmation_status="provided",
            is_primary=False,
        ))
        await session.commit()

    with pytest.raises(LoreMigrationRehearsalError) as error:
        await compensating_rollback_rehearsal(TestSessionLocal, anchor, GUARD)

    assert error.value.code == "ROLLBACK_UNSAFE"


@pytest.mark.usefixtures("clean_db")
async def test_isolation_and_write_freeze_fail_closed(monkeypatch):
    from tests.conftest import TestSessionLocal

    _, anchor, _ = await _fixture_project()
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", False)
    with pytest.raises(LoreMigrationRehearsalError) as frozen:
        await commit_rehearsal(TestSessionLocal, anchor, GUARD)
    assert frozen.value.code == "MIGRATION_REQUIRES_WRITE_FREEZE"

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    unsafe_guard = RehearsalGuard(
        environment_kind="production",
        synthetic_fixture=False,
        isolated_database=False,
        all_application_instances_frozen=True,
        isolation_nonce="not-installed",
    )
    with pytest.raises(LoreMigrationRehearsalError) as unsafe:
        await commit_rehearsal(TestSessionLocal, anchor, unsafe_guard)
    assert unsafe.value.code == "REHEARSAL_ISOLATION_NOT_PROVEN"


@pytest.mark.usefixtures("clean_db")
async def test_stale_anchor_and_idempotency_conflict_fail_closed(monkeypatch):
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    project_id, anchor, _ = await _fixture_project()
    async with TestSessionLocal() as session:
        worldview = await session.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        worldview.characters = [{"name": "来源已变化", "personality": "未知"}]
        await session.commit()

    with pytest.raises(LoreMigrationRehearsalError) as stale:
        await commit_rehearsal(TestSessionLocal, anchor, GUARD)
    assert stale.value.code == "REHEARSAL_PREVIEW_STALE"
    assert not any((await _business_counts(project_id)).values())


def _report_evidence():
    validation = {
        "status": "passed",
        "source_checksum": "a" * 64,
        "relational_checksum": "c" * 64,
        "legacy_rows_deleted": 0,
        "counts": {"elements": 4},
    }
    baseline = {
        "status": "passed",
        "final_mode": "legacy",
        "source_checksum": "a" * 64,
        "counts": {
            "types": 0,
            "type_revisions": 0,
            "elements": 0,
            "versions": 0,
            "sources": 0,
            "legacy_maps": 0,
            "state_events": 0,
            "migration_records": 0,
        },
        "legacy_rows_deleted": 0,
        "semantic_result_checksum": "b" * 64,
        "legacy_projection_checksum": "d" * 64,
        "legacy_projection_ids": ["synthetic-element"],
    }
    return validation, baseline


def test_sanitized_report_has_fixed_disclaimer_and_no_sensitive_values():
    preview = {
        "project_id": "project-secret-id",
        "preview_schema_version": 1,
        "mapping_version": 1,
        "source_checksum": "a" * 64,
        "semantic_result_checksum": "b" * 64,
    }
    anchor = build_rehearsal_anchor(preview)
    validation, baseline = _report_evidence()
    report = build_sanitized_rehearsal_report(
        anchor=anchor,
        guard=GUARD,
        database_backend="postgresql",
        phases={
            "preview": "passed",
            "commit": "passed",
            "post_commit_validation": "passed",
            "compensating_rollback": "passed",
            "post_rollback_validation": "passed",
        },
        validation=validation,
        baseline_validation=baseline,
        report_hmac_key=b"x" * 32,
    )

    encoded = json.dumps(report, ensure_ascii=False)
    assert report["production_migration_authorized"] is False
    assert report["real_user_data_accessed"] is False
    assert report["deployment_conclusion"] is False
    assert report["overall_status"] == "passed"
    assert report["rollback_attempted"] is True
    assert report["rollback_succeeded"] is True
    assert report["final_mode"] == "legacy"
    assert "不代表真实项目" in report["disclaimer"]
    assert "project-secret-id" not in encoded
    assert "a" * 64 not in encoded
    assert "c" * 64 not in encoded
    assert "host" not in encoded.casefold()
    assert "select " not in encoded.casefold()
    assert "insert " not in encoded.casefold()
    assert "traceback" not in encoded.casefold()


def test_report_cannot_pass_without_successful_rollback_evidence():
    validation, baseline = _report_evidence()
    preview = {
        "project_id": "project-a",
        "preview_schema_version": 1,
        "mapping_version": 1,
        "source_checksum": validation["source_checksum"],
        "semantic_result_checksum": "b" * 64,
    }
    report = build_sanitized_rehearsal_report(
        anchor=build_rehearsal_anchor(preview),
        guard=GUARD,
        database_backend="sqlite",
        phases={
            "preview": "passed",
            "commit": "passed",
            "post_commit_validation": "passed",
        },
        validation=validation,
        baseline_validation=baseline,
        report_hmac_key=b"x" * 32,
    )

    assert report["overall_status"] != "passed"
    assert report["rollback_attempted"] is False
    assert report["rollback_succeeded"] is False
    assert report["failed_phase"] == "compensating_rollback"


def test_report_rejects_non_isolated_guard_and_nonzero_residue():
    validation, baseline = _report_evidence()
    preview = {
        "project_id": "project-a",
        "preview_schema_version": 1,
        "mapping_version": 1,
        "source_checksum": validation["source_checksum"],
        "semantic_result_checksum": "b" * 64,
    }
    anchor = build_rehearsal_anchor(preview)
    phases = {
        "preview": "passed",
        "commit": "passed",
        "post_commit_validation": "passed",
        "compensating_rollback": "passed",
        "post_rollback_validation": "passed",
    }
    unsafe_guard = RehearsalGuard(
        "production", False, False, True, "not-installed"
    )
    with pytest.raises(ValueError):
        build_sanitized_rehearsal_report(
            anchor=anchor,
            guard=unsafe_guard,
            database_backend="postgresql",
            phases=phases,
            validation=validation,
            baseline_validation=baseline,
            report_hmac_key=b"x" * 32,
        )

    baseline["counts"]["elements"] = 1
    report = build_sanitized_rehearsal_report(
        anchor=anchor,
        guard=GUARD,
        database_backend="postgresql",
        phases=phases,
        validation=validation,
        baseline_validation=baseline,
        report_hmac_key=b"x" * 32,
    )
    assert report["overall_status"] != "passed"
    assert report["post_rollback_counts"]["elements"] == 1
    assert report["failed_phase"] == "post_rollback_validation"


def test_report_cannot_pass_with_missing_baseline_evidence():
    validation, baseline = _report_evidence()
    preview = {
        "project_id": "project-a",
        "preview_schema_version": 1,
        "mapping_version": 1,
        "source_checksum": validation["source_checksum"],
        "semantic_result_checksum": "b" * 64,
    }
    phases = {
        "preview": "passed",
        "commit": "passed",
        "post_commit_validation": "passed",
        "compensating_rollback": "passed",
        "post_rollback_validation": "passed",
    }
    baseline["counts"] = {}
    baseline.pop("source_checksum")
    baseline.pop("semantic_result_checksum")

    report = build_sanitized_rehearsal_report(
        anchor=build_rehearsal_anchor(preview),
        guard=GUARD,
        database_backend="sqlite",
        phases=phases,
        validation=validation,
        baseline_validation=baseline,
        report_hmac_key=b"x" * 32,
    )

    assert report["overall_status"] == "failed"
    assert report["phases"]["post_rollback_validation"] == "failed"
    assert report["failed_phase"] == "post_rollback_validation"


@pytest.mark.usefixtures("clean_db")
async def test_guard_rejects_database_with_an_unrelated_project(monkeypatch):
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    _, anchor, _ = await _fixture_project()
    async with TestSessionLocal() as session:
        session.add(Project(title="非演练项目", genre="玄幻", lore_storage_mode="legacy"))
        await session.commit()

    with pytest.raises(LoreMigrationRehearsalError) as error:
        await commit_rehearsal(TestSessionLocal, anchor, GUARD)

    assert error.value.code == "REHEARSAL_DATABASE_NOT_DEDICATED"


@pytest.mark.usefixtures("clean_db")
async def test_end_to_end_execution_builds_a_passed_report(monkeypatch):
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    project_id, anchor, _ = await _fixture_project()
    async with TestSessionLocal() as session:
        worldview = await session.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        legacy_before = project_legacy_worldview(project_id, worldview)
    await commit_rehearsal(TestSessionLocal, anchor, GUARD)
    validation = await validate_rehearsal(TestSessionLocal, anchor, GUARD)
    await compensating_rollback_rehearsal(TestSessionLocal, anchor, GUARD)
    baseline = await validate_rehearsal_baseline(TestSessionLocal, anchor, GUARD)

    report = build_sanitized_rehearsal_report(
        anchor=anchor,
        guard=GUARD,
        database_backend="sqlite",
        phases={
            "preview": "passed",
            "commit": "passed",
            "post_commit_validation": "passed",
            "compensating_rollback": "passed",
            "post_rollback_validation": "passed",
        },
        validation=validation,
        baseline_validation=baseline,
        report_hmac_key=b"isolated-environment-report-key!",
    )

    assert report["overall_status"] == "passed"
    assert report["final_mode"] == "legacy"
    assert report["legacy_rows_deleted"] == 0
    assert not any(report["post_rollback_counts"].values())
    assert baseline["legacy_projection_checksum"] == legacy_before.checksum
    assert baseline["legacy_projection_ids"] == [
        element.id for element in legacy_before.elements
    ]
