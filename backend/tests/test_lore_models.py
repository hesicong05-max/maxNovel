import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from app.models.lore import (
    ElementRelation,
    ElementSource,
    LegacyElementMap,
    SettingElement,
    SettingType,
    SettingTypeRevision,
)
from app.models.project import Project


def test_postgres_test_engine_does_not_reuse_connections_across_event_loops():
    from tests.conftest import TEST_DATABASE_BACKEND, test_engine

    if TEST_DATABASE_BACKEND == "sqlite":
        assert not isinstance(test_engine.sync_engine.pool, NullPool)
    else:
        assert isinstance(test_engine.sync_engine.pool, NullPool)


@pytest.mark.usefixtures("clean_db")
async def test_setting_type_keys_and_revisions_are_project_scoped():
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as session:
        project = Project(title="模型约束", genre="玄幻")
        session.add(project)
        await session.flush()
        setting_type = SettingType(
            project_id=project.id,
            key="character",
            display_name="角色",
            is_builtin=True,
            field_schema={},
        )
        session.add(setting_type)
        await session.flush()
        session.add(
            SettingTypeRevision(
                type_id=setting_type.id,
                revision=1,
                display_name="角色",
                field_schema={},
            )
        )
        await session.commit()

        session.add(
            SettingType(
                project_id=project.id,
                key="character",
                display_name="重复角色",
                is_builtin=False,
                field_schema={},
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.usefixtures("clean_db")
async def test_project_delete_cascades_read_model_rows():
    from sqlalchemy import func, select
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as session:
        project = Project(title="级联删除", genre="玄幻")
        session.add(project)
        await session.flush()
        session.add(
            SettingType(
                project_id=project.id,
                key="location",
                display_name="地点",
                is_builtin=True,
                field_schema={},
            )
        )
        await session.commit()
        await session.delete(project)
        await session.commit()
        count = await session.scalar(select(func.count()).select_from(SettingType))
        assert count == 0


async def _two_projects_with_type_and_element(session):
    project_a = Project(title="项目 A", genre="玄幻")
    project_b = Project(title="项目 B", genre="科幻")
    session.add_all([project_a, project_b])
    await session.flush()
    type_a = SettingType(
        project_id=project_a.id,
        key="character",
        display_name="角色",
        is_builtin=True,
        field_schema={},
    )
    type_b = SettingType(
        project_id=project_b.id,
        key="character",
        display_name="角色",
        is_builtin=True,
        field_schema={},
    )
    session.add_all([type_a, type_b])
    await session.flush()
    element_a = SettingElement(
        project_id=project_a.id,
        type_id=type_a.id,
        name="角色 A",
        normalized_name="角色 a",
        payload={},
    )
    session.add(element_a)
    await session.flush()
    return project_a, project_b, type_a, type_b, element_a


@pytest.mark.usefixtures("clean_db")
async def test_setting_element_rejects_cross_project_type_reference():
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as session:
        _project_a, project_b, type_a, _type_b, _element_a = (
            await _two_projects_with_type_and_element(session)
        )
        session.add(
            SettingElement(
                project_id=project_b.id,
                type_id=type_a.id,
                name="越权类型",
                normalized_name="越权类型",
                payload={},
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.usefixtures("clean_db")
async def test_element_source_rejects_cross_project_element_reference():
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as session:
        _project_a, project_b, _type_a, _type_b, element_a = (
            await _two_projects_with_type_and_element(session)
        )
        session.add(
            ElementSource(
                project_id=project_b.id,
                element_id=element_a.id,
                source_kind="manual",
                locator={},
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.usefixtures("clean_db")
async def test_legacy_map_rejects_cross_project_element_reference():
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as session:
        _project_a, project_b, _type_a, _type_b, element_a = (
            await _two_projects_with_type_and_element(session)
        )
        session.add(
            LegacyElementMap(
                project_id=project_b.id,
                legacy_category="characters",
                legacy_index=0,
                element_id=element_a.id,
                source_checksum="0" * 64,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.usefixtures("clean_db")
async def test_relation_rejects_cross_project_endpoints_at_database_boundary():
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as session:
        _project_a, project_b, _type_a, _type_b, element_a = (
            await _two_projects_with_type_and_element(session)
        )
        session.add(
            ElementRelation(
                project_id=project_b.id,
                source_element_id=element_a.id,
                target_element_id=element_a.id,
                relation_key="ally",
                forward_label="盟友",
                reverse_label="盟友",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
