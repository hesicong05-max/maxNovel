import pytest
from sqlalchemy.exc import IntegrityError

from app.models.lore import SettingType, SettingTypeRevision
from app.models.project import Project


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
