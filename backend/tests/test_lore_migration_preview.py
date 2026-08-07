from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.lore_migration_preview import build_migration_preview
from app.models.lore import (
    ElementSource,
    ElementVersion,
    LegacyElementMap,
    ProjectLoreMigration,
    SettingElement,
    SettingType,
    SettingTypeRevision,
)


def _worldview(**overrides):
    values = {
        "characters": [{"name": "林岚", "personality": "沉稳"}],
        "geography": [{"name": "云港", "description": "浮空港口"}],
        "factions": [],
        "power_system": [{"name": "灵阶", "levels": "九阶", "rules": "逐级修炼"}],
        "history": [{"event": "天裂", "description": "天空裂开"}],
        "conflicts": [],
        "special_settings": [{"name": "夜禁", "description": "夜间不得飞行"}],
        "parsed_elements": [],
        "raw_text": None,
        "source": "manual",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_preview_is_stable_zero_write_plan_with_dedicated_type_mapping():
    worldview = _worldview()
    before = deepcopy(worldview.__dict__)

    first = build_migration_preview("project-a", "legacy", worldview)
    second = build_migration_preview("project-a", "legacy", worldview)

    assert worldview.__dict__ == before
    assert first["dry_run"] is True
    assert first["writes_performed"] == 0
    assert first["commit_available"] is False
    assert first["source_checksum"] == second["source_checksum"]
    assert first["semantic_result_checksum"] == second["semantic_result_checksum"]
    assert [item["planned_element_id"] for item in first["items"]] == [
        item["planned_element_id"] for item in second["items"]
    ]
    type_by_category = {
        item["legacy_category"]: item["proposed_type_key"]
        for item in first["items"]
    }
    assert type_by_category["power_system"] == "ability_system"
    assert type_by_category["history"] == "historical_event"
    special = next(item for item in first["items"] if item["legacy_category"] == "special_settings")
    assert special["proposed_type_key"] is None
    assert special["classification"] == "review_required"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"characters": [{"personality": "沉稳"}]}, "missing_name"),
        ({"characters": [7]}, "non_object_entry"),
        ({"source": "external_guess"}, "source_unknown"),
        ({"characters": {"name": "错误结构"}}, "invalid_collection"),
    ],
)
def test_preview_fails_closed_for_unsafe_legacy_shapes(overrides, reason):
    preview = build_migration_preview("project-a", "legacy", _worldview(**overrides))

    assert preview["overall_status"] == "blocked"
    assert reason in {issue["reason_code"] for issue in preview["issues"]}


def test_preview_marks_duplicate_names_as_possible_conflicts():
    preview = build_migration_preview(
        "project-a",
        "legacy",
        _worldview(characters=[{"name": "林岚"}, {"name": " 林岚 "}]),
    )

    duplicates = [item for item in preview["items"] if item["legacy_category"] == "characters"]
    assert {item["classification"] for item in duplicates} == {"possible_conflict"}
    assert all("duplicate_name_same_type" in item["reason_codes"] for item in duplicates)


async def _create_project(client, headers):
    response = await client.post(
        "/api/projects",
        headers=headers,
        json={
            "title": "旧资料预检",
            "genre": "玄幻",
            "total_chapters": 10,
            "chapter_word_count": 1000,
            "style_intensity": "standard",
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


async def _set_worldview(client, headers, project_id):
    response = await client.post(
        f"/api/worldview/{project_id}",
        headers=headers,
        json={
            "characters": [{
                "name": "林岚",
                "personality": "沉稳",
                "background": "",
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
        },
    )
    assert response.status_code == 200


@pytest.mark.usefixtures("clean_db")
async def test_preview_api_is_owned_stable_and_does_not_write(
    client, auth_headers
):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _set_worldview(client, auth_headers, project_id)
    models = [
        SettingType,
        SettingTypeRevision,
        SettingElement,
        ElementSource,
        ElementVersion,
        ProjectLoreMigration,
        LegacyElementMap,
    ]

    async def counts():
        async with TestSessionLocal() as session:
            return [
                int(await session.scalar(select(func.count()).select_from(model)) or 0)
                for model in models
            ]

    before = await counts()
    unauthorized = await client.get(
        f"/api/projects/{project_id}/lore/migration-preview"
    )
    first = await client.get(
        f"/api/projects/{project_id}/lore/migration-preview",
        headers=auth_headers,
    )
    second = await client.get(
        f"/api/projects/{project_id}/lore/migration-preview",
        headers=auth_headers,
    )

    assert unauthorized.status_code == 401
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["semantic_result_checksum"] == second.json()["semantic_result_checksum"]
    assert first.json()["counts"]["legacy_total"] == 3
    assert first.json()["dry_run"] is True
    assert first.json()["writes_performed"] == 0
    assert await counts() == before == [0] * len(models)


@pytest.mark.usefixtures("clean_db")
async def test_preview_rejects_a_source_changed_during_check(
    client, auth_headers
):
    project_id = await _create_project(client, auth_headers)
    await _set_worldview(client, auth_headers, project_id)

    with patch("app.api.lore.project_legacy_worldview") as projection:
        projection.return_value = SimpleNamespace(checksum="f" * 64)
        response = await client.get(
            f"/api/projects/{project_id}/lore/migration-preview",
            headers=auth_headers,
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "LORE_MIGRATION_PREVIEW_STALE"


async def test_postgres_read_only_snapshot_rejects_dml():
    from tests.conftest import TEST_DATABASE_BACKEND, TestSessionLocal

    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("PostgreSQL-only read-only transaction proof")
    async with TestSessionLocal() as session:
        await session.execute(text(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        ))
        with pytest.raises(DBAPIError):
            await session.execute(text("UPDATE projects SET title = title"))
        await session.rollback()
