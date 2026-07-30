"""Add normalized lore tables without changing the legacy fact source.

Revision ID: a1d3c7e9f002
Revises: 8b87ca11f912
Create Date: 2026-07-30 14:50:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1d3c7e9f002"
down_revision: Union[str, None] = "8b87ca11f912"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "lore_storage_mode",
            sa.String(length=20),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.add_column(
        "projects",
        sa.Column("lore_migration_version", sa.Integer(), nullable=True),
    )

    op.create_table(
        "setting_types",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=32),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("schema_revision", sa.Integer(), nullable=False),
        sa.Column("field_schema", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_setting_type_status",
        ),
        sa.UniqueConstraint(
            "project_id",
            "key",
            name="uq_setting_type_project_key",
        ),
    )
    op.create_index(
        "ix_setting_types_project_id",
        "setting_types",
        ["project_id"],
    )

    op.create_table(
        "setting_type_revisions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "type_id",
            sa.String(length=32),
            sa.ForeignKey("setting_types.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("field_schema", sa.JSON(), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "type_id",
            "revision",
            name="uq_setting_type_revision",
        ),
    )
    op.create_index(
        "ix_setting_type_revisions_type_id",
        "setting_type_revisions",
        ["type_id"],
    )

    op.create_table(
        "setting_elements",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=32),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "type_id",
            sa.String(length=32),
            sa.ForeignKey("setting_types.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_schema_revision", sa.Integer(), nullable=False),
        sa.Column("confirmation_status", sa.String(length=20), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=20), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "confirmation_status IN ('candidate', 'confirmed', 'rejected')",
            name="ck_setting_element_confirmation",
        ),
        sa.CheckConstraint(
            "lifecycle_status IN ('active', 'archived', 'merged')",
            name="ck_setting_element_lifecycle",
        ),
    )
    op.create_index(
        "ix_setting_elements_project_id",
        "setting_elements",
        ["project_id"],
    )
    op.create_index(
        "ix_setting_elements_type_id",
        "setting_elements",
        ["type_id"],
    )
    op.create_index(
        "ix_setting_elements_project_status_updated",
        "setting_elements",
        ["project_id", "lifecycle_status", "updated_at", "id"],
    )
    op.create_index(
        "ix_setting_elements_project_type_updated",
        "setting_elements",
        ["project_id", "type_id", "updated_at", "id"],
    )
    op.create_index(
        "ix_setting_elements_project_confirmation_updated",
        "setting_elements",
        ["project_id", "confirmation_status", "updated_at", "id"],
    )
    op.create_index(
        "ix_setting_elements_project_name",
        "setting_elements",
        ["project_id", "normalized_name", "id"],
    )

    op.create_table(
        "element_sources",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=32),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "element_id",
            sa.String(length=32),
            sa.ForeignKey("setting_elements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_kind", sa.String(length=30), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=True),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column("excerpt_hash", sa.String(length=64), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_element_sources_project_id", "element_sources", ["project_id"])
    op.create_index("ix_element_sources_element_id", "element_sources", ["element_id"])
    op.create_index(
        "ix_element_sources_project_kind_ref",
        "element_sources",
        ["project_id", "source_kind", "source_ref"],
    )

    op.create_table(
        "element_versions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "element_id",
            sa.String(length=32),
            sa.ForeignKey("setting_elements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column(
            "type_id",
            sa.String(length=32),
            sa.ForeignKey("setting_types.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("type_schema_revision", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("change_reason", sa.String(length=100), nullable=True),
        sa.Column(
            "source_id",
            sa.String(length=32),
            sa.ForeignKey("element_sources.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by",
            sa.String(length=32),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "element_id",
            "version_no",
            name="uq_element_version",
        ),
    )
    op.create_index("ix_element_versions_element_id", "element_versions", ["element_id"])

    op.create_table(
        "project_lore_migrations",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=32),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("migration_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_checksum", sa.String(length=64), nullable=False),
        sa.Column("result_checksum", sa.String(length=64), nullable=True),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('preparing', 'validating', 'ready', 'failed', 'stale')",
            name="ck_project_lore_migration_status",
        ),
        sa.UniqueConstraint(
            "project_id",
            "migration_version",
            "source_checksum",
            name="uq_project_lore_migration_source",
        ),
    )
    op.create_index(
        "ix_project_lore_migrations_project_id",
        "project_lore_migrations",
        ["project_id"],
    )

    op.create_table(
        "legacy_element_maps",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=32),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("legacy_category", sa.String(length=50), nullable=False),
        sa.Column("legacy_index", sa.Integer(), nullable=False),
        sa.Column("legacy_id", sa.String(length=100), nullable=True),
        sa.Column(
            "element_id",
            sa.String(length=32),
            sa.ForeignKey("setting_elements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_checksum", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "legacy_category",
            "legacy_index",
            name="uq_legacy_element_position",
        ),
        sa.UniqueConstraint(
            "project_id",
            "element_id",
            name="uq_legacy_element_target",
        ),
    )
    op.create_index(
        "ix_legacy_element_maps_project_id",
        "legacy_element_maps",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_table("legacy_element_maps")
    op.drop_table("project_lore_migrations")
    op.drop_table("element_versions")
    op.drop_table("element_sources")
    op.drop_table("setting_elements")
    op.drop_table("setting_type_revisions")
    op.drop_table("setting_types")

    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("lore_migration_version")
        batch_op.drop_column("lore_storage_mode")
