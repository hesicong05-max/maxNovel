"""Add safe-write columns, state events, and relations tables.

Revision ID: b2e4f8d0c001
Revises: a1d3c7e9f002
Create Date: 2026-08-03 10:49:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2e4f8d0c001"
down_revision: Union[str, None] = "a1d3c7e9f002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("setting_types") as batch_op:
        batch_op.create_unique_constraint(
            "uq_setting_type_project_id_id",
            ["project_id", "id"],
        )

    op.add_column(
        "setting_elements",
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "setting_elements",
        sa.Column(
            "field_states",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "element_versions",
        sa.Column(
            "field_states",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "element_sources",
        sa.Column("excerpt", sa.Text(), nullable=True),
    )
    op.add_column(
        "element_sources",
        sa.Column(
            "confirmation_status",
            sa.String(length=20),
            nullable=False,
            server_default="provided",
        ),
    )
    with op.batch_alter_table("element_sources") as batch_op:
        batch_op.create_check_constraint(
            "ck_element_source_confirmation",
            "confirmation_status IN ('provided', 'needs_confirmation')",
        )

    with op.batch_alter_table("setting_elements") as batch_op:
        batch_op.create_unique_constraint(
            "uq_setting_element_project_id_id",
            ["project_id", "id"],
        )
        batch_op.create_foreign_key(
            "fk_setting_element_project_type",
            "setting_types",
            ["project_id", "type_id"],
            ["project_id", "id"],
            ondelete="CASCADE",
        )

    with op.batch_alter_table("element_sources") as batch_op:
        batch_op.create_foreign_key(
            "fk_element_source_project_element",
            "setting_elements",
            ["project_id", "element_id"],
            ["project_id", "id"],
            ondelete="CASCADE",
        )

    with op.batch_alter_table("legacy_element_maps") as batch_op:
        batch_op.create_foreign_key(
            "fk_legacy_element_map_project_element",
            "setting_elements",
            ["project_id", "element_id"],
            ["project_id", "id"],
            ondelete="CASCADE",
        )

    op.create_table(
        "element_state_events",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "element_id",
            sa.String(length=32),
            sa.ForeignKey("setting_elements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_kind", sa.String(length=20), nullable=False),
        sa.Column("previous_lock_version", sa.Integer(), nullable=False),
        sa.Column("new_lock_version", sa.Integer(), nullable=False),
        sa.Column(
            "performed_by",
            sa.String(length=32),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "event_kind IN ('create', 'confirm', 'reject', 'enable', "
            "'disable', 'archive', 'restore_archive', 'merge')",
            name="ck_element_state_event_kind",
        ),
    )
    op.create_index(
        "ix_element_state_events_element_id",
        "element_state_events",
        ["element_id"],
    )

    op.create_table(
        "element_relations",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=32),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_element_id",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "target_element_id",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("relation_key", sa.String(length=50), nullable=False),
        sa.Column("forward_label", sa.String(length=100), nullable=False),
        sa.Column("reverse_label", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "source_element_id",
            "target_element_id",
            "relation_key",
            name="uq_element_relation_key",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "source_element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_element_relation_project_source",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_element_relation_status",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "target_element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_element_relation_project_target",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_element_relations_project_id",
        "element_relations",
        ["project_id"],
    )
    op.create_index(
        "ix_element_relations_source_element_id",
        "element_relations",
        ["source_element_id"],
    )
    op.create_index(
        "ix_element_relations_project_source_status_key",
        "element_relations",
        ["project_id", "source_element_id", "status", "relation_key"],
    )
    op.create_index(
        "ix_element_relations_project_target_status_key",
        "element_relations",
        ["project_id", "target_element_id", "status", "relation_key"],
    )

    op.create_table(
        "element_relation_versions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "relation_id",
            sa.String(length=32),
            sa.ForeignKey("element_relations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("source_element_id", sa.String(length=32), nullable=False),
        sa.Column("target_element_id", sa.String(length=32), nullable=False),
        sa.Column("relation_key", sa.String(length=50), nullable=False),
        sa.Column("forward_label", sa.String(length=100), nullable=False),
        sa.Column("reverse_label", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("change_reason", sa.String(length=100), nullable=True),
        sa.Column(
            "created_by",
            sa.String(length=32),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "relation_id",
            "version_no",
            name="uq_element_relation_version",
        ),
    )
    op.create_index(
        "ix_element_relation_versions_relation_id",
        "element_relation_versions",
        ["relation_id"],
    )


def downgrade() -> None:
    op.drop_table("element_relation_versions")
    op.drop_table("element_relations")
    op.drop_table("element_state_events")

    with op.batch_alter_table("legacy_element_maps") as batch_op:
        batch_op.drop_constraint(
            "fk_legacy_element_map_project_element",
            type_="foreignkey",
        )

    with op.batch_alter_table("element_sources") as batch_op:
        batch_op.drop_constraint(
            "fk_element_source_project_element",
            type_="foreignkey",
        )

    with op.batch_alter_table("setting_elements") as batch_op:
        batch_op.drop_constraint(
            "fk_setting_element_project_type",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "uq_setting_element_project_id_id",
            type_="unique",
        )

    with op.batch_alter_table("element_sources") as batch_op:
        batch_op.drop_constraint("ck_element_source_confirmation", type_="check")
        batch_op.drop_column("confirmation_status")
        batch_op.drop_column("excerpt")

    with op.batch_alter_table("element_versions") as batch_op:
        batch_op.drop_column("field_states")

    with op.batch_alter_table("setting_elements") as batch_op:
        batch_op.drop_column("field_states")
        batch_op.drop_column("enabled")

    with op.batch_alter_table("setting_types") as batch_op:
        batch_op.drop_constraint(
            "uq_setting_type_project_id_id",
            type_="unique",
        )
