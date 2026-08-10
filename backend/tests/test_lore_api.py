import json
import time

import pytest
from sqlalchemy import func, select

from app.models.lore import (
    ElementSource,
    ElementVersion,
    LegacyElementMap,
    ProjectLoreMigration,
    SettingElement,
    SettingType,
    SettingTypeRevision,
)
from app.models.project import Worldview
from app.api.lore import _cursor_signature


async def _create_project(client, headers, title="设定库测试"):
    response = await client.post(
        "/api/projects",
        headers=headers,
        json={
            "title": title,
            "genre": "玄幻",
            "total_chapters": 10,
            "chapter_word_count": 1000,
            "style_intensity": "standard",
        },
    )
    assert response.status_code == 200
    project_id = response.json()["id"]
    # This file verifies the legacy projection explicitly. New product projects
    # are relational as of DEV-015A, so keep these fixtures intentional.
    from sqlalchemy import update
    from app.models.project import Project
    from tests.conftest import TestSessionLocal
    async with TestSessionLocal() as session:
        await session.execute(
            update(Project).where(Project.id == project_id).values(lore_storage_mode="legacy")
        )
        await session.commit()
    return project_id


async def _set_worldview(client, headers, project_id, extra_characters=None):
    characters = [
        {
            "name": "林岚",
            "personality": "沉稳",
            "background": "",
            "motivation": "寻找真相",
            "ability": "观星",
            "relations": [],
        },
        {
            "name": "周野",
            "personality": "直率",
            "background": "",
            "motivation": "守护云港",
            "ability": "",
            "relations": [],
        },
    ]
    characters.extend(extra_characters or [])
    current = await client.get(f"/api/worldview/{project_id}", headers=headers)
    expected_source_checksum = (
        current.json()["source_checksum"] if current.status_code == 200 else None
    )
    response = await client.post(
        f"/api/worldview/{project_id}",
        headers=headers,
        json={
            "characters": characters,
            "geography": [
                {
                    "name": "云港",
                    "description": "浮空港口",
                    "significance": "故事起点",
                }
            ],
            "factions": [],
            "power_system": [],
            "history": [],
            "conflicts": [],
            "special_settings": [],
            "raw_text": None,
            "source": "manual",
            "expected_source_checksum": expected_source_checksum,
        },
    )
    assert response.status_code == 200


@pytest.mark.usefixtures("clean_db")
async def test_lore_list_is_authenticated_read_only_projection(
    client, auth_headers
):
    project_id = await _create_project(client, auth_headers)
    await _set_worldview(client, auth_headers, project_id)

    unauthorized = await client.get(f"/api/projects/{project_id}/lore/elements")
    assert unauthorized.status_code == 401

    response = await client.get(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["migration_status"]["storage_mode"] == "legacy"
    assert data["migration_status"]["state"] == "ready"
    assert data["migration_status"]["read_only"] is True
    assert {item["type"]["key"] for item in data["items"]} == {
        "character",
        "location",
    }
    assert all("payload" not in item for item in data["items"])
    assert all(item["confirmation_status"] == "confirmed" for item in data["items"])
    assert data["facets"]["lifecycle_statuses"] == [
        {"key": "active", "label": "活动", "count": 3}
    ]
    assert data["facets"]["enabled_statuses"] == [
        {"key": "enabled", "label": "已启用", "count": 3}
    ]
    assert data["facets"]["relation_statuses"] == [
        {"key": "without_relations", "label": "无关联", "count": 3}
    ]
    overview = await client.get(
        f"/api/projects/{project_id}/lore/overview",
        headers=auth_headers,
    )
    assert overview.status_code == 200
    assert overview.json()["formal_total"] == 3
    assert overview.json()["confirmed_active"] == 3
    assert overview.json()["count_definitions"]["formal_total"] == {
        "entity": "formal_lore"
    }
    assert overview.json()["capabilities"]["formal_create"] is False


@pytest.mark.usefixtures("clean_db")
async def test_lore_get_requests_do_not_write_staging_tables(
    client, auth_headers
):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _set_worldview(client, auth_headers, project_id)
    model_classes = [
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
                await session.scalar(select(func.count()).select_from(model))
                for model in model_classes
            ]

    before = await counts()
    response = await client.get(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
    )
    assert response.status_code == 200
    element_id = response.json()["items"][0]["id"]
    for suffix in ("", "/sources", "/versions"):
        detail = await client.get(
            f"/api/projects/{project_id}/lore/elements/{element_id}{suffix}",
            headers=auth_headers,
        )
        assert detail.status_code == 200
    assert await counts() == before == [0] * len(model_classes)


@pytest.mark.usefixtures("clean_db")
async def test_lore_cursor_pagination_and_filter_binding(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    await _set_worldview(client, auth_headers, project_id)

    first = await client.get(
        f"/api/projects/{project_id}/lore/elements?limit=2",
        headers=auth_headers,
    )
    assert first.status_code == 200
    first_data = first.json()
    assert len(first_data["items"]) == 2
    assert first_data["has_more"] is True
    cursor = first_data["next_cursor"]

    second = await client.get(
        f"/api/projects/{project_id}/lore/elements",
        params={"limit": 2, "cursor": cursor},
        headers=auth_headers,
    )
    assert second.status_code == 200
    assert len(second.json()["items"]) == 1
    assert {
        item["id"] for item in first_data["items"]
    }.isdisjoint({item["id"] for item in second.json()["items"]})

    mismatched = await client.get(
        f"/api/projects/{project_id}/lore/elements",
        params={"limit": 2, "cursor": cursor, "type": "character"},
        headers=auth_headers,
    )
    assert mismatched.status_code == 400


@pytest.mark.usefixtures("clean_db")
async def test_lore_cursor_rejects_changed_legacy_source(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    await _set_worldview(client, auth_headers, project_id)
    first = await client.get(
        f"/api/projects/{project_id}/lore/elements?limit=1",
        headers=auth_headers,
    )
    cursor = first.json()["next_cursor"]

    await _set_worldview(
        client,
        auth_headers,
        project_id,
        extra_characters=[
            {
                "name": "新角色",
                "personality": "",
                "background": "",
                "motivation": "",
                "ability": "",
                "relations": [],
            }
        ],
    )
    stale = await client.get(
        f"/api/projects/{project_id}/lore/elements",
        params={"limit": 1, "cursor": cursor},
        headers=auth_headers,
    )
    assert stale.status_code == 409


@pytest.mark.usefixtures("clean_db")
async def test_lore_cursor_is_bound_to_project(client, auth_headers):
    first_project = await _create_project(client, auth_headers, title="项目一")
    second_project = await _create_project(client, auth_headers, title="项目二")
    await _set_worldview(client, auth_headers, first_project)
    await _set_worldview(client, auth_headers, second_project)

    first = await client.get(
        f"/api/projects/{first_project}/lore/elements?limit=1",
        headers=auth_headers,
    )
    cursor = first.json()["next_cursor"]

    cross_project = await client.get(
        f"/api/projects/{second_project}/lore/elements",
        params={"limit": 1, "cursor": cursor},
        headers=auth_headers,
    )
    assert cross_project.status_code == 400
    assert cross_project.json()["detail"] == "分页游标不属于当前项目"


@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize(
    "cursor",
    [
        "a.invalid",
        "%%%%.invalid",
        "W10.invalid",
        (
            "_w."
            + _cursor_signature(b"\xff")
        ),
    ],
)
async def test_lore_cursor_rejects_malformed_values_as_bad_request(
    client, auth_headers, cursor
):
    project_id = await _create_project(client, auth_headers)
    await _set_worldview(client, auth_headers, project_id)

    response = await client.get(
        f"/api/projects/{project_id}/lore/elements",
        params={"cursor": cursor},
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "分页游标无效"


@pytest.mark.usefixtures("clean_db")
async def test_lore_detail_sources_and_versions(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    await _set_worldview(client, auth_headers, project_id)
    listing = await client.get(
        f"/api/projects/{project_id}/lore/elements",
        params={"type": "location"},
        headers=auth_headers,
    )
    element_id = listing.json()["items"][0]["id"]

    detail = await client.get(
        f"/api/projects/{project_id}/lore/elements/{element_id}",
        headers=auth_headers,
    )
    assert detail.status_code == 200
    data = detail.json()
    assert data["type"]["key"] == "location"
    assert data["payload"]["name"] == "云港"
    assert data["read_only"] is True
    assert data["merged_to"] is None
    assert data["field_definitions"][0]["key"] == "description"

    sources = await client.get(
        f"/api/projects/{project_id}/lore/elements/{element_id}/sources",
        headers=auth_headers,
    )
    assert sources.status_code == 200
    assert sources.json()["items"][0]["kind"] == "manual"
    assert "source_ref" not in json.dumps(sources.json())

    versions = await client.get(
        f"/api/projects/{project_id}/lore/elements/{element_id}/versions",
        headers=auth_headers,
    )
    assert versions.status_code == 200
    assert versions.json()["items"][0]["version_no"] == 1


@pytest.mark.usefixtures("clean_db")
async def test_lore_project_isolation_and_missing_element(
    client, auth_headers, second_auth_headers
):
    project_id = await _create_project(client, auth_headers)
    await _set_worldview(client, auth_headers, project_id)

    forbidden = await client.get(
        f"/api/projects/{project_id}/lore/elements",
        headers=second_auth_headers,
    )
    assert forbidden.status_code == 403

    missing = await client.get(
        f"/api/projects/{project_id}/lore/elements/not-an-element",
        headers=auth_headers,
    )
    assert missing.status_code == 404


@pytest.mark.usefixtures("clean_db")
async def test_empty_project_has_not_started_status(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    response = await client.get(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["migration_status"]["state"] == "not_started"


@pytest.mark.usefixtures("clean_db")
async def test_lore_list_bounds_payload_for_one_thousand_elements(
    client, auth_headers
):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers, title="千条设定")
    async with TestSessionLocal() as session:
        session.add(
            Worldview(
                project_id=project_id,
                characters=[
                    {
                        "name": f"角色{i:04d}",
                        "personality": "稳定",
                        "background": "不应出现在列表响应",
                    }
                    for i in range(1000)
                ],
                geography=[],
                factions=[],
                power_system=[],
                history=[],
                conflicts=[],
                special_settings=[],
                parsed_elements=[],
                source="manual",
            )
        )
        await session.commit()

    started = time.perf_counter()
    response = await client.get(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
    )
    duration = time.perf_counter() - started

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1000
    assert len(data["items"]) == 30
    assert data["has_more"] is True
    assert all("payload" not in item for item in data["items"])
    assert duration < 1.5
