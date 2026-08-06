"""Cascade element versions when their setting type is removed.

Revision ID: a7d9e3f5b006
Revises: f6c8d2e4a005
Create Date: 2026-08-06 15:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7d9e3f5b006"
down_revision: Union[str, None] = "f6c8d2e4a005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_FK_NAME = "fk_element_version_type"
_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _reflected_type_fk_name() -> str:
    """Resolve the historical named/unnamed FK across PostgreSQL and SQLite."""
    foreign_keys = sa.inspect(op.get_bind()).get_foreign_keys("element_versions")
    matches = [
        foreign_key
        for foreign_key in foreign_keys
        if foreign_key.get("constrained_columns") == ["type_id"]
        and foreign_key.get("referred_table") == "setting_types"
        and foreign_key.get("referred_columns") == ["id"]
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one element_versions.type_id foreign key"
        )
    return matches[0].get("name") or (
        "fk_element_versions_type_id_setting_types"
    )


def _replace_type_fk(*, ondelete: str) -> None:
    existing_name = _reflected_type_fk_name()
    with op.batch_alter_table(
        "element_versions",
        naming_convention=_NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint(existing_name, type_="foreignkey")
        batch_op.create_foreign_key(
            _FK_NAME,
            "setting_types",
            ["type_id"],
            ["id"],
            ondelete=ondelete,
        )


def upgrade() -> None:
    _replace_type_fk(ondelete="CASCADE")


def downgrade() -> None:
    _replace_type_fk(ondelete="RESTRICT")
