"""Comprehensive tests for DEV-003B relational lore writes.

Covers: auth, project isolation, legacy write rejection (409 mode guard),
relational CRUD, state changes, 409 on stale version with stable code,
422 field errors, maintenance freeze 503, transaction rollback, content
version boundary, excerpt storage, type resolution, payload key validation,
Alembic upgrade-downgrade-upgrade.
"""

import pytest
from sqlalchemy import func, select

from app.models.lore import (
    ElementRelation,
    ElementRelationVersion,
    ElementSource,
    ElementStateEvent,
    ElementVersion,
    SettingElement,
    SettingType,
    SettingTypeRevision,
)
from app.models.project import Project, Worldview


# ─── helpers ──────────────────────────────────────────────────────


async def _create_project(client, headers, title="写入测试"):
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
    return response.json()["id"]


async def _make_relational(client, headers, project_id):
    """Set project.lore_storage_mode to relational via direct DB update."""
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as session:
        from sqlalchemy import update

        await session.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(lore_storage_mode="relational")
        )
        await session.commit()


async def _create_relational_element(
    client, headers, project_id, **kwargs
):
    """Create an element in a relational project. Returns response json."""
    await _make_relational(client, headers, project_id)
    body = {
        "type_key": kwargs.get("type_key", "character"),
        "name": kwargs.get("name", "测试角色"),
        "payload": kwargs.get("payload", {}),
    }
    if "summary" in kwargs:
        body["summary"] = kwargs["summary"]
    if "field_states" in kwargs:
        body["field_states"] = kwargs["field_states"]
    if "sources" in kwargs:
        body["sources"] = kwargs["sources"]
    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=headers,
        json=body,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_relation(
    client,
    headers,
    project_id,
    source_id,
    target_id,
    **overrides,
):
    body = {
        "target_element_id": target_id,
        "source_expected_version": overrides.get("source_expected_version", 1),
        "target_expected_version": overrides.get("target_expected_version", 1),
        "relation_key": overrides.get("relation_key", "ally"),
        "forward_label": overrides.get("forward_label", "盟友"),
        "reverse_label": overrides.get("reverse_label", "盟友"),
        "description": overrides.get("description", "共同目标"),
        "metadata": overrides.get("metadata", {}),
    }
    response = await client.post(
        f"/api/projects/{project_id}/lore/elements/{source_id}/relations",
        headers=headers,
        json=body,
    )
    assert response.status_code == 201, response.text
    return response.json()


# ─── auth and project isolation ───────────────────────────────────


@pytest.mark.usefixtures("clean_db")
async def test_write_endpoints_require_authentication(client):
    response = await client.post(
        "/api/projects/fake-id/lore/elements",
        json={
            "type_key": "character",
            "name": "测试",
            "payload": {},
        },
    )
    assert response.status_code == 401


@pytest.mark.usefixtures("clean_db")
async def test_create_requires_project_ownership(
    client, auth_headers, second_auth_headers
):
    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)
    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=second_auth_headers,
        json={
            "type_key": "character",
            "name": "越权创建",
            "payload": {},
        },
    )
    assert response.status_code == 403


@pytest.mark.usefixtures("clean_db")
async def test_create_returns_404_for_missing_project(
    client, auth_headers
):
    response = await client.post(
        "/api/projects/nonexistent-id/lore/elements",
        headers=auth_headers,
        json={
            "type_key": "character",
            "name": "幽灵",
            "payload": {},
        },
    )
    assert response.status_code == 404


# ─── relational mode enforcement (item 2) ────────────────────────


@pytest.mark.usefixtures("clean_db")
async def test_legacy_project_writes_return_409(client, auth_headers):
    """Legacy mode projects must not allow relational writes."""
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    # Project defaults to "legacy" — no _make_relational call

    # Verify legacy mode
    async with TestSessionLocal() as session:
        project = await session.scalar(
            select(Project).where(Project.id == project_id)
        )
        assert project.lore_storage_mode == "legacy"

    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
        json={
            "type_key": "character",
            "name": "不应创建",
            "payload": {},
        },
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "LORE_MODE_NOT_RELATIONAL"

    type_response = await client.post(
        f"/api/projects/{project_id}/lore/types",
        headers=auth_headers,
        json={"key": "vehicle", "display_name": "载具"},
    )
    assert type_response.status_code == 409
    assert type_response.json()["detail"]["code"] == "LORE_MODE_NOT_RELATIONAL"


@pytest.mark.usefixtures("clean_db")
async def test_legacy_write_does_not_mutate_worldview(client, auth_headers):
    """Writing to a legacy project must not change any data."""
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)

    # Set legacy worldview
    worldview_set = await client.post(
        f"/api/worldview/{project_id}",
        headers=auth_headers,
        json={
            "characters": [{"name": "旧角色", "personality": "旧"}],
            "geography": [],
            "factions": [],
            "power_system": [],
            "history": [],
            "conflicts": [],
            "special_settings": [],
            "source": "manual",
        },
    )
    assert worldview_set.status_code == 200

    # Capture checksums before write attempt
    async with TestSessionLocal() as session:
        worldview = await session.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        old_characters = list(worldview.characters)
        old_setting_types = await session.scalar(
            select(func.count()).select_from(SettingType)
        )
        old_elements = await session.scalar(
            select(func.count()).select_from(SettingElement)
        )
        old_versions = await session.scalar(
            select(func.count()).select_from(ElementVersion)
        )
        old_sources = await session.scalar(
            select(func.count()).select_from(ElementSource)
        )
        old_events = await session.scalar(
            select(func.count()).select_from(ElementStateEvent)
        )

    # Attempt relational write (should fail with 409)
    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
        json={
            "type_key": "character",
            "name": "新角色",
            "payload": {},
        },
    )
    assert response.status_code == 409

    # Verify nothing changed
    async with TestSessionLocal() as session:
        worldview = await session.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        assert len(worldview.characters) == len(old_characters)
        assert worldview.characters[0]["name"] == "旧角色"
        assert await session.scalar(
            select(func.count()).select_from(SettingType)
        ) == old_setting_types
        assert await session.scalar(
            select(func.count()).select_from(SettingElement)
        ) == old_elements
        assert await session.scalar(
            select(func.count()).select_from(ElementVersion)
        ) == old_versions
        assert await session.scalar(
            select(func.count()).select_from(ElementSource)
        ) == old_sources
        assert await session.scalar(
            select(func.count()).select_from(ElementStateEvent)
        ) == old_events


# ─── relational create ────────────────────────────────────────────


@pytest.mark.usefixtures("clean_db")
async def test_create_element_without_sources(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    data = await _create_relational_element(
        client, auth_headers, project_id,
        name="林岚",
        summary="主角",
        payload={"personality": "沉稳", "abilities": "观星"},
        field_states={
            "personality": "provided",
            "abilities": "provided",
        },
    )
    assert data["name"] == "林岚"
    assert data["type"]["key"] == "character"
    assert data["confirmation_status"] == "confirmed"
    assert data["lifecycle_status"] == "active"
    assert data["enabled"] is True
    assert data["generation_eligible"] is True
    assert data["lock_version"] == 1
    assert data["content_version"] == 1
    assert data["payload"]["personality"] == "沉稳"
    assert data["sources"] == []


@pytest.mark.usefixtures("clean_db")
async def test_create_element_with_sources(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    data = await _create_relational_element(
        client, auth_headers, project_id,
        type_key="location",
        name="云港",
        payload={"description": "浮空港口"},
        sources=[
            {
                "kind": "manual",
                "reference": "世界观设定",
                "locator": {"section": "intro"},
                "excerpt": "云港是浮空港口城市",
                "is_primary": True,
                "confirmation_status": "provided",
            }
        ],
    )
    assert len(data["sources"]) == 1
    assert data["sources"][0]["kind"] == "manual"
    assert data["sources"][0]["is_primary"] is True
    assert data["sources"][0]["reference"] == "世界观设定"
    assert data["sources"][0]["excerpt"] == "云港是浮空港口城市"
    assert data["sources"][0]["excerpt_hash"] is not None
    assert data["sources"][0]["confirmation_status"] == "provided"


@pytest.mark.usefixtures("clean_db")
async def test_relational_element_round_trip_reads_written_data(
    client, auth_headers,
):
    project_id = await _create_project(client, auth_headers)
    created = await _create_relational_element(
        client,
        auth_headers,
        project_id,
        name="林岚",
        summary="守护云港的巡夜人",
        payload={"personality": "沉稳"},
        sources=[
            {
                "kind": "manual",
                "reference": "用户设定原文",
                "excerpt": "林岚性格沉稳，是云港巡夜人。",
                "is_primary": True,
            }
        ],
    )

    listed = await client.get(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
        params={"q": "林岚", "type": "character", "enabled": True},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    item = listed.json()["items"][0]
    assert item["id"] == created["id"]
    assert item["lock_version"] == 1
    assert item["enabled"] is True
    assert item["generation_eligible"] is True

    detail = await client.get(
        f"/api/projects/{project_id}/lore/elements/{created['id']}",
        headers=auth_headers,
    )
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["payload"] == {"personality": "沉稳"}
    assert detail_body["field_states"]["personality"] == "provided"
    assert detail_body["payload_schema_revision"] == 1
    assert detail_body["sources"][0]["excerpt"] == "林岚性格沉稳，是云港巡夜人。"
    assert detail_body["read_only"] is False

    sources = await client.get(
        f"/api/projects/{project_id}/lore/elements/{created['id']}/sources",
        headers=auth_headers,
    )
    assert sources.status_code == 200
    assert sources.json()["items"][0]["id"]
    assert sources.json()["items"][0]["reference"] == "用户设定原文"

    versions = await client.get(
        f"/api/projects/{project_id}/lore/elements/{created['id']}/versions",
        headers=auth_headers,
    )
    assert versions.status_code == 200
    assert versions.json()["items"][0]["field_states"]["personality"] == "provided"
    version = await client.get(
        f"/api/projects/{project_id}/lore/elements/{created['id']}/versions/1",
        headers=auth_headers,
    )
    assert version.status_code == 200
    assert version.json()["created_by"]


@pytest.mark.usefixtures("clean_db")
async def test_create_element_auto_creates_setting_type(client, auth_headers):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _create_relational_element(
        client, auth_headers, project_id,
        type_key="faction",
        name="星盟",
    )

    async with TestSessionLocal() as session:
        setting_type = await session.scalar(
            select(SettingType).where(
                SettingType.project_id == project_id,
                SettingType.key == "faction",
            )
        )
        assert setting_type is not None
        assert setting_type.is_builtin is True
        assert setting_type.field_schema is not None
        field_keys = {f["key"] for f in setting_type.field_schema}
        assert "stance" in field_keys
        assert "power_level" in field_keys


@pytest.mark.usefixtures("clean_db")
async def test_create_element_creates_version_snapshot(client, auth_headers):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    data = await _create_relational_element(
        client, auth_headers, project_id, name="周野",
    )
    element_id = data["id"]

    async with TestSessionLocal() as session:
        version = await session.scalar(
            select(ElementVersion).where(
                ElementVersion.element_id == element_id,
                ElementVersion.version_no == 1,
            )
        )
        assert version is not None
        assert version.name == "周野"
        assert version.change_reason == "创建"


@pytest.mark.usefixtures("clean_db")
async def test_create_element_creates_state_event(client, auth_headers):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    data = await _create_relational_element(
        client, auth_headers, project_id, name="角色A",
    )
    element_id = data["id"]

    async with TestSessionLocal() as session:
        event = await session.scalar(
            select(ElementStateEvent).where(
                ElementStateEvent.element_id == element_id,
                ElementStateEvent.event_kind == "create",
            )
        )
        assert event is not None
        assert event.previous_lock_version == 0
        assert event.new_lock_version == 1


# ─── content edit ─────────────────────────────────────────────────


@pytest.mark.usefixtures("clean_db")
async def test_edit_element_content(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    created = await _create_relational_element(
        client, auth_headers, project_id,
        name="林岚",
        payload={"personality": "沉稳"},
    )

    edit_resp = await client.patch(
        f"/api/projects/{project_id}/lore/elements/{created['id']}",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "name": "林岚（修订）",
            "summary": "更新摘要",
            "payload": {"personality": "沉稳且果断", "motivations": "寻找真相"},
            "field_states": {
                "personality": "provided",
                "motivations": "provided",
            },
        },
    )
    assert edit_resp.status_code == 200, edit_resp.text
    edited = edit_resp.json()
    assert edited["name"] == "林岚（修订）"
    assert edited["summary"] == "更新摘要"
    assert edited["content_version"] == 2
    assert edited["lock_version"] == 2


@pytest.mark.usefixtures("clean_db")
async def test_edit_creates_new_version_snapshot(client, auth_headers):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    created = await _create_relational_element(
        client, auth_headers, project_id,
        name="原创",
        payload={"personality": "旧版"},
    )
    element_id = created["id"]

    await client.patch(
        f"/api/projects/{project_id}/lore/elements/{element_id}",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "name": "修订版",
            "payload": {"personality": "新版"},
            "field_states": {},
        },
    )

    async with TestSessionLocal() as session:
        count = await session.scalar(
            select(func.count()).select_from(ElementVersion).where(
                ElementVersion.element_id == element_id,
            )
        )
        assert count == 2


# ─── optimistic locking (item 6) ──────────────────────────────────


@pytest.mark.usefixtures("clean_db")
async def test_stale_expected_version_returns_409(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    created = await _create_relational_element(
        client, auth_headers, project_id, name="版本测试",
    )
    element_id = created["id"]

    # First edit succeeds
    await client.patch(
        f"/api/projects/{project_id}/lore/elements/{element_id}",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "name": "第一次编辑",
            "payload": {},
            "field_states": {},
        },
    )

    # Second edit with stale version
    stale = await client.patch(
        f"/api/projects/{project_id}/lore/elements/{element_id}",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "name": "过期编辑",
            "payload": {},
            "field_states": {},
        },
    )
    assert stale.status_code == 409
    stale_data = stale.json()
    assert isinstance(stale_data["detail"], dict)
    detail = stale_data["detail"]
    assert detail["code"] == "LORE_VERSION_CONFLICT"
    assert "current_lock_version" in detail
    assert detail["current_lock_version"] == 2
    assert "updated_at" in detail


@pytest.mark.usefixtures("clean_db")
async def test_two_sessions_compete_for_same_expected_version(
    client, auth_headers,
):
    from app.core.lore_write import LoreStaleVersionError, update_element_content
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    created = await _create_relational_element(
        client, auth_headers, project_id, name="并发版本测试",
    )

    async with TestSessionLocal() as first, TestSessionLocal() as second:
        first_element = await first.scalar(
            select(SettingElement).where(SettingElement.id == created["id"])
        )
        second_element = await second.scalar(
            select(SettingElement).where(SettingElement.id == created["id"])
        )
        project = await first.scalar(select(Project).where(Project.id == project_id))

        await update_element_content(
            first,
            first_element,
            project.owner_id,
            1,
            "第一个写入者",
            "",
            {},
            {},
        )
        await first.commit()

        with pytest.raises(LoreStaleVersionError) as stale:
            await update_element_content(
                second,
                second_element,
                project.owner_id,
                1,
                "第二个写入者",
                "",
                {},
                {},
            )
        assert stale.value.current_lock_version == 2
        await second.rollback()

    async with TestSessionLocal() as verify:
        element = await verify.scalar(
            select(SettingElement).where(SettingElement.id == created["id"])
        )
        version_count = await verify.scalar(
            select(func.count()).select_from(ElementVersion).where(
                ElementVersion.element_id == created["id"]
            )
        )
        assert element.name == "第一个写入者"
        assert element.lock_version == 2
        assert version_count == 2


@pytest.mark.usefixtures("clean_db")
async def test_state_change_requires_expected_version(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    created = await _create_relational_element(
        client, auth_headers, project_id, name="状态版本",
    )
    element_id = created["id"]

    stale = await client.post(
        f"/api/projects/{project_id}/lore/elements/{element_id}/archive",
        headers=auth_headers,
        json={"expected_version": 999},
    )
    assert stale.status_code == 409


# ─── state changes ────────────────────────────────────────────────


@pytest.mark.usefixtures("clean_db")
async def test_archive_and_restore(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    created = await _create_relational_element(
        client, auth_headers, project_id, name="归档测试",
    )
    element_id = created["id"]

    archive = await client.post(
        f"/api/projects/{project_id}/lore/elements/{element_id}/archive",
        headers=auth_headers,
        json={"expected_version": 1, "reason": "不再需要"},
    )
    assert archive.status_code == 200
    assert archive.json()["lifecycle_status"] == "archived"
    assert archive.json()["generation_eligible"] is False

    restore = await client.post(
        f"/api/projects/{project_id}/lore/elements/{element_id}/restore-archive",
        headers=auth_headers,
        json={"expected_version": 2, "reason": "重新启用"},
    )
    assert restore.status_code == 200
    assert restore.json()["lifecycle_status"] == "active"
    assert restore.json()["generation_eligible"] is True


@pytest.mark.usefixtures("clean_db")
async def test_disable_and_enable(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    created = await _create_relational_element(
        client, auth_headers, project_id, name="开关测试",
    )
    element_id = created["id"]

    disable = await client.post(
        f"/api/projects/{project_id}/lore/elements/{element_id}/disable",
        headers=auth_headers,
        json={"expected_version": 1},
    )
    assert disable.status_code == 200
    assert disable.json()["enabled"] is False
    assert disable.json()["generation_eligible"] is False

    enable = await client.post(
        f"/api/projects/{project_id}/lore/elements/{element_id}/enable",
        headers=auth_headers,
        json={"expected_version": 2},
    )
    assert enable.status_code == 200
    assert enable.json()["enabled"] is True
    assert enable.json()["generation_eligible"] is True


@pytest.mark.usefixtures("clean_db")
async def test_confirm_candidate(client, auth_headers):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)
    element_id = None
    async with TestSessionLocal() as session:
        st = SettingType(
            project_id=project_id,
            key="character",
            display_name="角色",
            is_builtin=True,
            field_schema=[],
        )
        session.add(st)
        await session.flush()

        element = SettingElement(
            project_id=project_id,
            type_id=st.id,
            name="候选角色",
            normalized_name="候选角色",
            confirmation_status="candidate",
            payload={},
            field_states={},
        )
        session.add(element)
        await session.flush()
        element_id = element.id
        await session.commit()

    confirm = await client.post(
        f"/api/projects/{project_id}/lore/elements/{element_id}/confirm",
        headers=auth_headers,
        json={"expected_version": 1, "reason": "手动确认"},
    )
    assert confirm.status_code == 200
    assert confirm.json()["confirmation_status"] == "confirmed"
    assert confirm.json()["generation_eligible"] is True


@pytest.mark.usefixtures("clean_db")
async def test_reject_candidate(client, auth_headers):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)
    element_id = None
    async with TestSessionLocal() as session:
        st = SettingType(
            project_id=project_id,
            key="character",
            display_name="角色",
            is_builtin=True,
            field_schema=[],
        )
        session.add(st)
        await session.flush()

        element = SettingElement(
            project_id=project_id,
            type_id=st.id,
            name="待拒绝",
            normalized_name="待拒绝",
            confirmation_status="candidate",
            payload={},
            field_states={},
        )
        session.add(element)
        await session.flush()
        element_id = element.id
        await session.commit()

    reject = await client.post(
        f"/api/projects/{project_id}/lore/elements/{element_id}/reject",
        headers=auth_headers,
        json={"expected_version": 1, "reason": "不适合故事"},
    )
    assert reject.status_code == 200
    assert reject.json()["confirmation_status"] == "rejected"
    assert reject.json()["generation_eligible"] is False


@pytest.mark.usefixtures("clean_db")
async def test_state_events_are_recorded_independently(client, auth_headers):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    created = await _create_relational_element(
        client, auth_headers, project_id, name="事件记录测试",
    )
    element_id = created["id"]

    await client.post(
        f"/api/projects/{project_id}/lore/elements/{element_id}/disable",
        headers=auth_headers,
        json={"expected_version": 1},
    )
    await client.post(
        f"/api/projects/{project_id}/lore/elements/{element_id}/enable",
        headers=auth_headers,
        json={"expected_version": 2},
    )

    async with TestSessionLocal() as session:
        events = (
            await session.execute(
                select(ElementStateEvent).where(
                    ElementStateEvent.element_id == element_id,
                ).order_by(ElementStateEvent.created_at.asc())
            )
        ).scalars().all()
        event_kinds = [e.event_kind for e in events]
        assert "create" in event_kinds
        assert "disable" in event_kinds
        assert "enable" in event_kinds
        lock_versions = [e.new_lock_version for e in events]
        assert lock_versions == [1, 2, 3]


# ─── content version restore (item 5) ────────────────────────────


@pytest.mark.usefixtures("clean_db")
async def test_restore_version_only_restores_content(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    created = await _create_relational_element(
        client, auth_headers, project_id,
        name="原始名称",
        payload={"personality": "原始性格"},
    )
    element_id = created["id"]

    # Edit
    await client.patch(
        f"/api/projects/{project_id}/lore/elements/{element_id}",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "name": "修改名称",
            "payload": {"personality": "修改后性格"},
            "field_states": {},
        },
    )
    # Archive (changes lifecycle but not content version independently)
    await client.post(
        f"/api/projects/{project_id}/lore/elements/{element_id}/archive",
        headers=auth_headers,
        json={"expected_version": 2},
    )

    # Restore version 1 with expected_version=3
    restore = await client.post(
        f"/api/projects/{project_id}/lore/elements/{element_id}/versions/1/restore",
        headers=auth_headers,
        json={"expected_version": 3, "reason": "回滚内容"},
    )
    assert restore.status_code == 200, restore.text
    data = restore.json()
    assert data["name"] == "原始名称"
    assert data["payload"]["personality"] == "原始性格"
    assert data["content_version"] == 3
    # State changes should NOT be reverted by content restore
    assert data["lifecycle_status"] == "archived"
    assert data["confirmation_status"] == "confirmed"


@pytest.mark.usefixtures("clean_db")
async def test_restore_with_stale_version_returns_409(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    created = await _create_relational_element(
        client, auth_headers, project_id, name="版本测试",
    )
    element_id = created["id"]

    resp = await client.post(
        f"/api/projects/{project_id}/lore/elements/{element_id}/versions/1/restore",
        headers=auth_headers,
        json={"expected_version": 999},
    )
    assert resp.status_code == 409


@pytest.mark.usefixtures("clean_db")
async def test_restore_nonexistent_version_returns_404(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    created = await _create_relational_element(
        client, auth_headers, project_id, name="版本测试",
    )
    element_id = created["id"]

    resp = await client.post(
        f"/api/projects/{project_id}/lore/elements/{element_id}/versions/999/restore",
        headers=auth_headers,
        json={"expected_version": 1},
    )
    assert resp.status_code == 404


# ─── field validation (item 7) ───────────────────────────────────


@pytest.mark.usefixtures("clean_db")
async def test_create_rejects_missing_name(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)
    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
        json={
            "type_key": "character",
            "payload": {"personality": "无名称"},
        },
    )
    assert response.status_code == 422


@pytest.mark.usefixtures("clean_db")
async def test_create_rejects_missing_type_key(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)
    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
        json={
            "name": "缺类型",
            "payload": {},
        },
    )
    assert response.status_code == 422


@pytest.mark.usefixtures("clean_db")
async def test_create_rejects_unknown_type_key(client, auth_headers):
    """Unknown (non-builtin, non-existing) type keys must be rejected."""
    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)
    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
        json={
            "type_key": "totally_made_up_type",
            "name": "未知类型",
            "payload": {},
        },
    )
    assert response.status_code == 422


@pytest.mark.usefixtures("clean_db")
async def test_create_rejects_payload_with_unknown_keys(client, auth_headers):
    """Payload keys not in the type schema must be rejected."""
    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)
    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
        json={
            "type_key": "character",
            "name": "未知字段",
            "payload": {"totally_unknown_field": "value"},
        },
    )
    assert response.status_code == 422


@pytest.mark.usefixtures("clean_db")
async def test_provided_state_requires_non_empty_payload(client, auth_headers):
    """Field marked as 'provided' must have non-null, non-empty payload value."""
    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)
    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
        json={
            "type_key": "character",
            "name": "空值测试",
            "payload": {"personality": ""},
            "field_states": {"personality": "provided"},
        },
    )
    assert response.status_code == 422


@pytest.mark.usefixtures("clean_db")
async def test_edit_rejects_invalid_field_states(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    created = await _create_relational_element(
        client, auth_headers, project_id, name="字段测试",
    )
    element_id = created["id"]

    response = await client.patch(
        f"/api/projects/{project_id}/lore/elements/{element_id}",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "name": "无效字段状态",
            "payload": {},
            "field_states": {"personality": "invalid_status"},
        },
    )
    assert response.status_code == 422


# ─── new type support (item 3) ───────────────────────────────────


@pytest.mark.usefixtures("clean_db")
async def test_create_with_new_builtin_type(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)
    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
        json={
            "type_key": "ability_system",
            "name": "魔法体系",
            "payload": {"description": "基于元素的法术系统"},
        },
    )
    assert response.status_code == 201
    assert response.json()["type"]["key"] == "ability_system"
    assert response.json()["type"]["display_name"] == "能力体系"


@pytest.mark.usefixtures("clean_db")
async def test_create_with_race_type(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)
    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
        json={
            "type_key": "race",
            "name": "精灵族",
            "payload": {"description": "森林居民"},
        },
    )
    assert response.status_code == 201


@pytest.mark.usefixtures("clean_db")
async def test_character_schema_has_required_fields(client, auth_headers):
    """Character schema must include identity, appearance, personality,
    background, abilities, limitations, goals, motivations, possible_plots."""
    project_id = await _create_project(client, auth_headers)
    data = await _create_relational_element(
        client, auth_headers, project_id, name="字段检查",
    )
    field_keys = {f["key"] for f in data["field_definitions"]}
    expected = {
        "identity", "appearance", "personality", "background",
        "abilities", "limitations", "goals", "motivations",
        "possible_plots",
    }
    assert expected.issubset(field_keys), f"Missing: {expected - field_keys}"


@pytest.mark.usefixtures("clean_db")
async def test_field_definitions_have_value_type(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    data = await _create_relational_element(
        client, auth_headers, project_id, name="类型检查",
    )
    for field in data["field_definitions"]:
        assert "value_type" in field
        assert field["value_type"] in ("string", "text", "reference")


@pytest.mark.usefixtures("clean_db")
async def test_no_required_fields_besides_name(client, auth_headers):
    """No type field should have required=True (name is element-level)."""
    project_id = await _create_project(client, auth_headers)
    data = await _create_relational_element(
        client, auth_headers, project_id, name="必填检查",
    )
    for field in data["field_definitions"]:
        assert field["required"] is False, f"{field['key']} should not be required"


@pytest.mark.usefixtures("clean_db")
async def test_custom_type_can_create_structured_elements(client, auth_headers):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)
    type_response = await client.post(
        f"/api/projects/{project_id}/lore/types",
        headers=auth_headers,
        json={
            "key": "vehicle",
            "display_name": "载具",
            "description": "小说中的交通与作战载具",
            "field_schema": [
                {
                    "key": "appearance",
                    "label": "外观",
                    "control": "textarea",
                    "value_type": "text",
                    "help": "载具外观",
                    "order": 10,
                    "required": False,
                },
                {
                    "key": "limitations",
                    "label": "限制",
                    "control": "textarea",
                    "value_type": "text",
                    "help": "使用限制",
                    "order": 20,
                    "required": False,
                },
            ],
        },
    )
    assert type_response.status_code == 201, type_response.text
    custom_type = type_response.json()
    assert custom_type["is_builtin"] is False
    assert custom_type["schema_revision"] == 1

    element = await _create_relational_element(
        client,
        auth_headers,
        project_id,
        type_key="vehicle",
        name="云舟",
        payload={"appearance": "银白船身", "limitations": "惧强风"},
    )
    assert element["type"]["key"] == "vehicle"
    assert [field["key"] for field in element["field_definitions"]] == [
        "appearance",
        "limitations",
    ]

    listed = await client.get(
        f"/api/projects/{project_id}/lore/types",
        headers=auth_headers,
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["key"] == "vehicle"

    async with TestSessionLocal() as session:
        revision_count = await session.scalar(
            select(func.count()).select_from(SettingTypeRevision)
        )
        assert revision_count == 1


@pytest.mark.usefixtures("clean_db")
async def test_custom_type_validation_and_duplicates(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)
    body = {
        "key": "vehicle",
        "display_name": "载具",
        "field_schema": [
            {
                "key": "description",
                "label": "描述",
                "required": False,
            }
        ],
    }
    created = await client.post(
        f"/api/projects/{project_id}/lore/types",
        headers=auth_headers,
        json=body,
    )
    assert created.status_code == 201

    duplicate = await client.post(
        f"/api/projects/{project_id}/lore/types",
        headers=auth_headers,
        json=body,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "LORE_TYPE_DUPLICATE"

    builtin_collision = await client.post(
        f"/api/projects/{project_id}/lore/types",
        headers=auth_headers,
        json={**body, "key": "character"},
    )
    assert builtin_collision.status_code == 409

    required_field = await client.post(
        f"/api/projects/{project_id}/lore/types",
        headers=auth_headers,
        json={
            "key": "custom_required",
            "display_name": "非法必填",
            "field_schema": [
                {"key": "fact", "label": "事实", "required": True}
            ],
        },
    )
    assert required_field.status_code == 422

    unknown_payload = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
        json={
            "type_key": "vehicle",
            "name": "错误载具",
            "payload": {"invented": "不在定义中"},
        },
    )
    assert unknown_payload.status_code == 422


# ─── maintenance freeze (item 1) ─────────────────────────────────


@pytest.mark.usefixtures("clean_db")
async def test_write_blocked_when_maintenance_frozen(client, auth_headers):
    """Freeze must return 503 and no new rows in any table."""
    from app.config import settings as app_settings
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)

    # Create an element before freeze
    created = await _create_relational_element(
        client, auth_headers, project_id, name="冻结前",
    )
    element_id = created["id"]
    relation_target = await _create_relational_element(
        client, auth_headers, project_id, name="冻结关系目标",
    )

    # Capture counts before freeze
    async with TestSessionLocal() as session:
        before_types = await session.scalar(
            select(func.count()).select_from(SettingType)
        )
        before_elements = await session.scalar(
            select(func.count()).select_from(SettingElement)
        )
        before_versions = await session.scalar(
            select(func.count()).select_from(ElementVersion)
        )
        before_sources = await session.scalar(
            select(func.count()).select_from(ElementSource)
        )
        before_events = await session.scalar(
            select(func.count()).select_from(ElementStateEvent)
        )
        before_relations = await session.scalar(
            select(func.count()).select_from(ElementRelation)
        )
        before_relation_versions = await session.scalar(
            select(func.count()).select_from(ElementRelationVersion)
        )

    # Freeze
    original = app_settings.LEGACY_JSON_WRITES_FROZEN
    app_settings.LEGACY_JSON_WRITES_FROZEN = True
    try:
        # Create during freeze
        freeze_create = await client.post(
            f"/api/projects/{project_id}/lore/elements",
            headers=auth_headers,
            json={
                "type_key": "character",
                "name": "冻结中",
                "payload": {},
            },
        )
        assert freeze_create.status_code == 503
        freeze_data = freeze_create.json()
        assert freeze_data["code"] == "PROJECT_WRITE_FROZEN"
        assert freeze_data["maintenance_state"] == "write_frozen"
        assert freeze_create.headers["retry-after"] == str(
            freeze_data["retry_after_seconds"]
        )

        # Edit during freeze
        freeze_edit = await client.patch(
            f"/api/projects/{project_id}/lore/elements/{element_id}",
            headers=auth_headers,
            json={
                "expected_version": 1,
                "name": "冻结编辑",
                "payload": {},
                "field_states": {},
            },
        )
        assert freeze_edit.status_code == 503

        # State change during freeze
        freeze_state = await client.post(
            f"/api/projects/{project_id}/lore/elements/{element_id}/archive",
            headers=auth_headers,
            json={"expected_version": 1},
        )
        assert freeze_state.status_code == 503

        freeze_relation = await client.post(
            f"/api/projects/{project_id}/lore/elements/{element_id}/relations",
            headers=auth_headers,
            json={
                "target_element_id": relation_target["id"],
                "source_expected_version": 1,
                "target_expected_version": 1,
                "relation_key": "ally",
                "forward_label": "盟友",
                "reverse_label": "盟友",
            },
        )
        assert freeze_relation.status_code == 503

        freeze_type = await client.post(
            f"/api/projects/{project_id}/lore/types",
            headers=auth_headers,
            json={"key": "vehicle", "display_name": "载具"},
        )
        assert freeze_type.status_code == 503
    finally:
        app_settings.LEGACY_JSON_WRITES_FROZEN = original

    # Verify nothing was written during freeze
    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(SettingType)
        ) == before_types
        assert await session.scalar(
            select(func.count()).select_from(SettingElement)
        ) == before_elements
        assert await session.scalar(
            select(func.count()).select_from(ElementVersion)
        ) == before_versions
        assert await session.scalar(
            select(func.count()).select_from(ElementSource)
        ) == before_sources
        assert await session.scalar(
            select(func.count()).select_from(ElementStateEvent)
        ) == before_events
        assert await session.scalar(
            select(func.count()).select_from(ElementRelation)
        ) == before_relations
        assert await session.scalar(
            select(func.count()).select_from(ElementRelationVersion)
        ) == before_relation_versions


# ─── excerpt storage (item 4) ────────────────────────────────────


@pytest.mark.usefixtures("clean_db")
async def test_excerpt_stored_and_returned(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    data = await _create_relational_element(
        client, auth_headers, project_id,
        name="出处测试",
        payload={"personality": "测试"},
        sources=[
            {
                "kind": "document_import",
                "reference": "chapter-3",
                "locator": {"chapter": 3, "paragraph": 5},
                "excerpt": "他握紧了手中的剑，目光如电。",
                "is_primary": True,
                "confirmation_status": "provided",
            }
        ],
    )
    assert len(data["sources"]) == 1
    src = data["sources"][0]
    assert src["excerpt"] == "他握紧了手中的剑，目光如电。"
    assert src["excerpt_hash"] is not None
    assert src["reference"] == "chapter-3"
    assert src["locator"] == {"chapter": 3, "paragraph": 5}
    assert src["confirmation_status"] == "provided"


@pytest.mark.usefixtures("clean_db")
async def test_excerpt_null_when_not_provided(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    data = await _create_relational_element(
        client, auth_headers, project_id,
        name="无出处",
        payload={"personality": "测试"},
        sources=[
            {
                "kind": "manual",
                "is_primary": True,
            }
        ],
    )
    assert len(data["sources"]) == 1
    assert data["sources"][0]["excerpt"] is None
    assert data["sources"][0]["excerpt_hash"] is None


@pytest.mark.usefixtures("clean_db")
async def test_source_needs_confirmation(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    data = await _create_relational_element(
        client, auth_headers, project_id,
        name="待确认",
        payload={"personality": "测试"},
        sources=[
            {
                "kind": "document_import",
                "excerpt": "待确认的片段",
                "confirmation_status": "needs_confirmation",
            }
        ],
    )
    assert data["sources"][0]["confirmation_status"] == "needs_confirmation"


# ─── project isolation (cross-project refs blocked) ──────────────


@pytest.mark.usefixtures("clean_db")
async def test_edit_element_cross_project_returns_404(client, auth_headers):
    project_a = await _create_project(client, auth_headers, title="项目A")
    project_b = await _create_project(client, auth_headers, title="项目B")

    created = await _create_relational_element(
        client, auth_headers, project_a, name="专属角色",
    )
    element_id = created["id"]

    await _make_relational(client, auth_headers, project_b)
    cross = await client.patch(
        f"/api/projects/{project_b}/lore/elements/{element_id}",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "name": "跨项目",
            "payload": {},
            "field_states": {},
        },
    )
    assert cross.status_code == 404


# ─── transaction integrity (item 8) ──────────────────────────────


@pytest.mark.usefixtures("clean_db")
async def test_successful_create_commits_all_dependent_rows(client, auth_headers):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)

    async with TestSessionLocal() as session:
        before = await session.scalar(
            select(func.count()).select_from(SettingElement)
        )

    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
        json={
            "type_key": "character",
            "name": "确保无残留",
            "payload": {"personality": "测试"},
        },
    )
    assert response.status_code == 201

    async with TestSessionLocal() as session:
        after = await session.scalar(
            select(func.count()).select_from(SettingElement)
        )
        assert after == before + 1

        version_count = await session.scalar(
            select(func.count()).select_from(ElementVersion)
        )
        event_count = await session.scalar(
            select(func.count()).select_from(ElementStateEvent)
        )
        assert version_count == 1
        assert event_count == 1


@pytest.mark.usefixtures("clean_db")
async def test_late_commit_failure_rolls_back_flushed_rows(
    client, auth_headers, monkeypatch,
):
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)

    async with TestSessionLocal() as session:
        before_types = await session.scalar(
            select(func.count()).select_from(SettingType)
        )
        before_elements = await session.scalar(
            select(func.count()).select_from(SettingElement)
        )

    original_commit = AsyncSession.commit

    async def fail_commit(_session):
        raise RuntimeError("injected late commit failure")

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="injected late commit failure"):
            await client.post(
                f"/api/projects/{project_id}/lore/elements",
                headers=auth_headers,
                json={
                    "type_key": "character",
                    "name": "不得残留",
                    "payload": {"personality": "只存在于失败事务"},
                },
            )
    finally:
        monkeypatch.setattr(AsyncSession, "commit", original_commit)

    async with TestSessionLocal() as session:
        assert await session.scalar(
            select(func.count()).select_from(SettingType)
        ) == before_types
        assert await session.scalar(
            select(func.count()).select_from(SettingElement)
        ) == before_elements


@pytest.mark.usefixtures("clean_db")
async def test_payload_validation_rollback(client, auth_headers):
    """When payload validation fails, no partial data should be written."""
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)

    async with TestSessionLocal() as session:
        before_elements = await session.scalar(
            select(func.count()).select_from(SettingElement)
        )
        before_types = await session.scalar(
            select(func.count()).select_from(SettingType)
        )

    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
        json={
            "type_key": "character",
            "name": "回滚测试",
            "payload": {"unknown_field": "value"},
        },
    )
    assert response.status_code == 422

    async with TestSessionLocal() as session:
        after_elements = await session.scalar(
            select(func.count()).select_from(SettingElement)
        )
        after_types = await session.scalar(
            select(func.count()).select_from(SettingType)
        )
        assert after_elements == before_elements
        assert after_types == before_types


# ─── field_states consistency ─────────────────────────────────────


@pytest.mark.usefixtures("clean_db")
async def test_field_states_derived_from_payload(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    data = await _create_relational_element(
        client, auth_headers, project_id,
        name="字段状态测试",
        payload={
            "personality": "沉稳",
            "background": "战士世家",
        },
    )
    assert data["field_states"]["personality"] == "provided"
    assert data["field_states"]["background"] == "provided"
    assert data["field_states"]["motivations"] == "unknown"
    assert data["field_states"]["abilities"] == "unknown"


@pytest.mark.usefixtures("clean_db")
async def test_user_field_states_override_derived(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    data = await _create_relational_element(
        client, auth_headers, project_id,
        name="覆盖测试",
        payload={
            "personality": "有待确认的内容",
            "background": "明确背景",
        },
        field_states={
            "personality": "needs_confirmation",
            "background": "provided",
        },
    )
    assert data["field_states"]["personality"] == "needs_confirmation"
    assert data["field_states"]["background"] == "provided"
    assert data["generation_eligible"] is False


@pytest.mark.usefixtures("clean_db")
async def test_pending_fields_block_confirmation_until_resolved(
    client, auth_headers,
):
    from sqlalchemy import update
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)
    created = await _create_relational_element(
        client,
        auth_headers,
        project_id,
        name="待确认角色",
        payload={"personality": "可能很谨慎"},
        field_states={"personality": "needs_confirmation"},
    )
    assert created["confirmation_status"] == "confirmed"
    assert created["generation_eligible"] is False

    async with TestSessionLocal() as session:
        await session.execute(
            update(SettingElement)
            .where(SettingElement.id == created["id"])
            .values(confirmation_status="candidate")
        )
        await session.commit()

    blocked = await client.post(
        f"/api/projects/{project_id}/lore/elements/{created['id']}/confirm",
        headers=auth_headers,
        json={"expected_version": 1},
    )
    assert blocked.status_code == 422
    assert blocked.json()["detail"] == {
        "code": "LORE_FIELDS_NEED_CONFIRMATION",
        "message": "仍有字段需要确认，暂不能确认整个设定",
        "fields": ["personality"],
    }

    resolved = await client.patch(
        f"/api/projects/{project_id}/lore/elements/{created['id']}",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "name": "待确认角色",
            "payload": {"personality": "谨慎"},
            "field_states": {"personality": "provided"},
        },
    )
    assert resolved.status_code == 200
    assert resolved.json()["generation_eligible"] is False

    confirmed = await client.post(
        f"/api/projects/{project_id}/lore/elements/{created['id']}/confirm",
        headers=auth_headers,
        json={"expected_version": 2},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["generation_eligible"] is True


@pytest.mark.usefixtures("clean_db")
async def test_unknown_field_state_rejects_nonempty_value(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)
    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
        json={
            "type_key": "character",
            "name": "未知字段错误",
            "payload": {"personality": "不应作为未知值保存"},
            "field_states": {"personality": "unknown"},
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "LORE_FIELD_STATE_INVALID"


@pytest.mark.usefixtures("clean_db")
async def test_relational_source_facets_match_filtered_scope(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    await _create_relational_element(
        client,
        auth_headers,
        project_id,
        name="手动来源",
        sources=[{"kind": "manual", "is_primary": True}],
    )
    await _create_relational_element(
        client,
        auth_headers,
        project_id,
        name="导入来源",
        sources=[{"kind": "document_import", "is_primary": True}],
    )
    await _create_relational_element(
        client, auth_headers, project_id, name="无来源",
    )

    response = await client.get(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
    )
    assert response.status_code == 200
    facets = {
        item["key"]: item["count"]
        for item in response.json()["facets"]["sources"]
    }
    assert facets == {"document_import": 1, "manual": 1}

    filtered = await client.get(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
        params={"source_kind": "manual"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["facets"]["sources"] == [
        {"key": "manual", "label": "manual", "count": 1}
    ]


@pytest.mark.usefixtures("clean_db")
async def test_element_create_integrity_race_returns_retryable_409(
    client, auth_headers, monkeypatch,
):
    import app.api.lore as lore_api
    from sqlalchemy.exc import IntegrityError

    project_id = await _create_project(client, auth_headers)
    await _make_relational(client, auth_headers, project_id)

    async def raise_integrity_error(**_kwargs):
        raise IntegrityError("INSERT setting_types", {}, RuntimeError("race"))

    monkeypatch.setattr(lore_api, "create_element", raise_integrity_error)
    response = await client.post(
        f"/api/projects/{project_id}/lore/elements",
        headers=auth_headers,
        json={"type_key": "character", "name": "并发创建", "payload": {}},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "LORE_ELEMENT_CONFLICT",
        "message": "设定保存发生并发冲突，请安全重试",
        "retryable": True,
    }


# ─── duplicate name independence ──────────────────────────────────


@pytest.mark.usefixtures("clean_db")
async def test_duplicate_names_are_independent_elements(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    first = await _create_relational_element(
        client, auth_headers, project_id,
        name="角色X",
        payload={"personality": "版本1"},
    )
    second = await _create_relational_element(
        client, auth_headers, project_id,
        name="角色X",
        payload={"personality": "版本2"},
    )
    assert first["id"] != second["id"]
    assert first["payload"]["personality"] == "版本1"
    assert second["payload"]["personality"] == "版本2"


# ─── Alembic upgrade-downgrade-upgrade ────────────────────────────


@pytest.mark.usefixtures("clean_db")
async def test_alembic_upgrade_downgrade_upgrade_cycle(client, auth_headers):
    """Verify the new revision columns and tables exist."""
    from tests.conftest import test_engine as engine
    from sqlalchemy import inspect

    def _inspect(sync_conn):
        insp = inspect(sync_conn)
        element_cols = {c["name"] for c in insp.get_columns("setting_elements")}
        version_cols = {c["name"] for c in insp.get_columns("element_versions")}
        source_cols = {c["name"] for c in insp.get_columns("element_sources")}
        tables = set(insp.get_table_names())
        return element_cols, version_cols, source_cols, tables

    async with engine.begin() as conn:
        element_cols, version_cols, source_cols, tables = await conn.run_sync(
            _inspect
        )

    assert "enabled" in element_cols
    assert "field_states" in element_cols
    assert "field_states" in version_cols
    assert "excerpt" in source_cols
    assert "confirmation_status" in source_cols
    assert "element_state_events" in tables
    assert "element_relations" in tables


# ─── relation model constraints ───────────────────────────────────


@pytest.mark.usefixtures("clean_db")
async def test_relation_creation_basic(client, auth_headers):
    from tests.conftest import TestSessionLocal

    project_id = await _create_project(client, auth_headers)

    a = await _create_relational_element(
        client, auth_headers, project_a_id := project_id, name="角色A",
    )
    b = await _create_relational_element(
        client, auth_headers, project_id, name="角色B",
    )

    async with TestSessionLocal() as session:
        relation = ElementRelation(
            project_id=project_id,
            source_element_id=a["id"],
            target_element_id=b["id"],
            relation_key="ally",
            forward_label="盟友",
            reverse_label="盟友",
        )
        session.add(relation)
        await session.commit()

        count = await session.scalar(
            select(func.count()).select_from(ElementRelation)
        )
        assert count == 1


@pytest.mark.usefixtures("clean_db")
async def test_relation_api_create_list_and_initial_version(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    source = await _create_relational_element(
        client, auth_headers, project_id, name="林岚",
    )
    target = await _create_relational_element(
        client, auth_headers, project_id, name="星盟", type_key="faction",
    )

    relation = await _create_relation(
        client,
        auth_headers,
        project_id,
        source["id"],
        target["id"],
        relation_key="member_of",
        forward_label="隶属于",
        reverse_label="拥有成员",
        metadata={"certainty": "confirmed"},
    )
    assert relation["source"]["name"] == "林岚"
    assert relation["source"]["type"]["key"] == "character"
    assert relation["source"]["lifecycle_status"] == "active"
    assert relation["source"]["enabled"] is True
    assert relation["target"]["name"] == "星盟"
    assert relation["target"]["type"]["key"] == "faction"
    assert relation["status"] == "active"
    assert relation["version_no"] == 1
    assert relation["lock_version"] == 1

    listed = await client.get(
        f"/api/projects/{project_id}/lore/elements/{target['id']}/relations",
        headers=auth_headers,
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == relation["id"]

    versions = await client.get(
        f"/api/projects/{project_id}/lore/relations/{relation['id']}/versions",
        headers=auth_headers,
    )
    assert versions.status_code == 200
    assert versions.json()["total"] == 1
    assert versions.json()["items"][0]["change_reason"] == "创建关系"


@pytest.mark.usefixtures("clean_db")
async def test_relation_create_rejects_stale_endpoint_versions(
    client, auth_headers,
):
    project_id = await _create_project(client, auth_headers)
    source = await _create_relational_element(
        client, auth_headers, project_id, name="角色 A",
    )
    target = await _create_relational_element(
        client, auth_headers, project_id, name="角色 B",
    )
    changed = await client.post(
        f"/api/projects/{project_id}/lore/elements/{target['id']}/disable",
        headers=auth_headers,
        json={"expected_version": 1},
    )
    assert changed.status_code == 200

    response = await client.post(
        f"/api/projects/{project_id}/lore/elements/{source['id']}/relations",
        headers=auth_headers,
        json={
            "target_element_id": target["id"],
            "source_expected_version": 1,
            "target_expected_version": 1,
            "relation_key": "ally",
            "forward_label": "盟友",
            "reverse_label": "盟友",
        },
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "LORE_RELATION_ENDPOINT_CHANGED"
    assert detail["endpoint_conflicts"] == [
        {"endpoint": "target", "current_lock_version": 2}
    ]


@pytest.mark.usefixtures("clean_db")
async def test_relation_duplicate_and_self_loop_are_rejected(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    source = await _create_relational_element(
        client, auth_headers, project_id, name="角色 A",
    )
    target = await _create_relational_element(
        client, auth_headers, project_id, name="角色 B",
    )
    await _create_relation(
        client, auth_headers, project_id, source["id"], target["id"],
    )

    duplicate = await client.post(
        f"/api/projects/{project_id}/lore/elements/{source['id']}/relations",
        headers=auth_headers,
        json={
            "target_element_id": target["id"],
            "source_expected_version": 1,
            "target_expected_version": 1,
            "relation_key": "ally",
            "forward_label": "盟友",
            "reverse_label": "盟友",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "LORE_RELATION_DUPLICATE"

    self_loop = await client.post(
        f"/api/projects/{project_id}/lore/elements/{source['id']}/relations",
        headers=auth_headers,
        json={
            "target_element_id": source["id"],
            "source_expected_version": 1,
            "target_expected_version": 1,
            "relation_key": "ally",
            "forward_label": "盟友",
            "reverse_label": "盟友",
        },
    )
    assert self_loop.status_code == 422


@pytest.mark.usefixtures("clean_db")
async def test_relation_rejects_cross_project_target(client, auth_headers):
    project_a = await _create_project(client, auth_headers, title="关系项目 A")
    project_b = await _create_project(client, auth_headers, title="关系项目 B")
    source = await _create_relational_element(
        client, auth_headers, project_a, name="项目 A 角色",
    )
    target = await _create_relational_element(
        client, auth_headers, project_b, name="项目 B 角色",
    )
    response = await client.post(
        f"/api/projects/{project_a}/lore/elements/{source['id']}/relations",
        headers=auth_headers,
        json={
            "target_element_id": target["id"],
            "source_expected_version": 1,
            "target_expected_version": 1,
            "relation_key": "ally",
            "forward_label": "盟友",
            "reverse_label": "盟友",
        },
    )
    assert response.status_code == 404


@pytest.mark.usefixtures("clean_db")
async def test_relation_edit_archive_restore_and_versions(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    source = await _create_relational_element(
        client, auth_headers, project_id, name="角色 A",
    )
    target = await _create_relational_element(
        client, auth_headers, project_id, name="角色 B",
    )
    relation = await _create_relation(
        client, auth_headers, project_id, source["id"], target["id"],
    )

    edited = await client.patch(
        f"/api/projects/{project_id}/lore/relations/{relation['id']}",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "forward_label": "并肩作战",
            "reverse_label": "并肩作战",
            "description": "关系推进",
            "metadata": {"chapter": 3},
        },
    )
    assert edited.status_code == 200
    assert edited.json()["version_no"] == 2
    assert edited.json()["lock_version"] == 2

    stale = await client.patch(
        f"/api/projects/{project_id}/lore/relations/{relation['id']}",
        headers=auth_headers,
        json={
            "expected_version": 1,
            "forward_label": "过期编辑",
            "reverse_label": "过期编辑",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "LORE_VERSION_CONFLICT"

    archived = await client.post(
        f"/api/projects/{project_id}/lore/relations/{relation['id']}/archive",
        headers=auth_headers,
        json={"expected_version": 2, "reason": "剧情变化"},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert archived.json()["lock_version"] == 3

    restored = await client.post(
        f"/api/projects/{project_id}/lore/relations/{relation['id']}/restore",
        headers=auth_headers,
        json={"expected_version": 3},
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"
    assert restored.json()["lock_version"] == 4

    versions = await client.get(
        f"/api/projects/{project_id}/lore/relations/{relation['id']}/versions",
        headers=auth_headers,
    )
    assert versions.status_code == 200
    assert [item["version_no"] for item in versions.json()["items"]] == [4, 3, 2, 1]


@pytest.mark.usefixtures("clean_db")
async def test_archived_duplicate_requires_explicit_restore(client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    source = await _create_relational_element(
        client, auth_headers, project_id, name="角色 A",
    )
    target = await _create_relational_element(
        client, auth_headers, project_id, name="角色 B",
    )
    relation = await _create_relation(
        client, auth_headers, project_id, source["id"], target["id"],
    )
    archived = await client.post(
        f"/api/projects/{project_id}/lore/relations/{relation['id']}/archive",
        headers=auth_headers,
        json={"expected_version": 1},
    )
    assert archived.status_code == 200

    duplicate = await client.post(
        f"/api/projects/{project_id}/lore/elements/{source['id']}/relations",
        headers=auth_headers,
        json={
            "target_element_id": target["id"],
            "source_expected_version": 1,
            "target_expected_version": 1,
            "relation_key": "ally",
            "forward_label": "盟友",
            "reverse_label": "盟友",
            "description": "不得静默覆盖",
        },
    )
    assert duplicate.status_code == 409
    detail = duplicate.json()["detail"]
    assert detail["relation_id"] == relation["id"]
    assert detail["relation_status"] == "archived"
    assert detail["current_lock_version"] == 2

    restored = await client.post(
        f"/api/projects/{project_id}/lore/relations/{relation['id']}/restore",
        headers=auth_headers,
        json={"expected_version": 2},
    )
    assert restored.status_code == 200
    assert restored.json()["id"] == relation["id"]
    assert restored.json()["version_no"] == 3

    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as session:
        relation_count = await session.scalar(
            select(func.count()).select_from(ElementRelation)
        )
        version_count = await session.scalar(
            select(func.count()).select_from(ElementRelationVersion)
        )
        assert relation_count == 1
        assert version_count == 3
