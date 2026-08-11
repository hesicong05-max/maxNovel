"""Add durable relational foreshadow lifecycle records.

Revision ID: e7b9d1f3a016
Revises: d6a8c0e2f015
Create Date: 2026-08-11 15:35:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7b9d1f3a016"
down_revision: Union[str, None] = "d6a8c0e2f015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "foreshadow_lifecycles",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("plan_id", sa.String(length=32), nullable=False),
        sa.Column("element_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("updated_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_foreshadow_lifecycle_status",
        ),
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_foreshadow_lifecycle_lock_version"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "plan_id"],
            ["novel_plans.project_id", "novel_plans.id"],
            name="fk_foreshadow_lifecycle_plan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_foreshadow_lifecycle_element",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "project_id", "element_id", name="uq_foreshadow_lifecycle_element"
        ),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_foreshadow_lifecycle_project_id_id"
        ),
    )
    op.create_index(
        "ix_foreshadow_lifecycles_project_status_updated",
        "foreshadow_lifecycles",
        ["project_id", "status", "updated_at", "id"],
    )

    op.create_table(
        "foreshadow_plan_items",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("plan_id", sa.String(length=32), nullable=False),
        sa.Column("lifecycle_id", sa.String(length=32), nullable=False),
        sa.Column("action_kind", sa.String(length=20), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.String(length=32), nullable=False),
        sa.Column("part_id", sa.String(length=32), nullable=True),
        sa.Column("chapter_id", sa.String(length=32), nullable=True),
        sa.Column("condition_text", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("updated_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "action_kind IN ('plant', 'resolve')",
            name="ck_foreshadow_plan_item_action",
        ),
        sa.CheckConstraint(
            "target_type IN ('part', 'chapter')",
            name="ck_foreshadow_plan_item_target_type",
        ),
        sa.CheckConstraint(
            "(target_type = 'part' AND target_id = part_id AND part_id IS NOT NULL "
            "AND chapter_id IS NULL) OR "
            "(target_type = 'chapter' AND target_id = chapter_id AND part_id IS NULL "
            "AND chapter_id IS NOT NULL)",
            name="ck_foreshadow_plan_item_target",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'cancelled')",
            name="ck_foreshadow_plan_item_status",
        ),
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_foreshadow_plan_item_lock_version"
        ),
        sa.CheckConstraint(
            "action_kind = 'plant' OR length(trim(condition_text)) > 0",
            name="ck_foreshadow_resolve_condition",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "plan_id"],
            ["novel_plans.project_id", "novel_plans.id"],
            name="fk_foreshadow_plan_item_plan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "lifecycle_id"],
            ["foreshadow_lifecycles.project_id", "foreshadow_lifecycles.id"],
            name="fk_foreshadow_plan_item_lifecycle",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "part_id"],
            ["planning_parts.project_id", "planning_parts.id"],
            name="fk_foreshadow_plan_item_part",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "chapter_id"],
            ["planning_chapters.project_id", "planning_chapters.id"],
            name="fk_foreshadow_plan_item_chapter",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_foreshadow_plan_item_project_id_id"
        ),
    )
    op.create_index(
        "uq_foreshadow_plan_items_active_kind",
        "foreshadow_plan_items",
        ["lifecycle_id", "action_kind"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_foreshadow_plan_items_target_status",
        "foreshadow_plan_items",
        ["target_type", "target_id", "status", "id"],
    )

    op.create_table(
        "foreshadow_facts",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("plan_id", sa.String(length=32), nullable=False),
        sa.Column("lifecycle_id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("fact_kind", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("recorded_by", sa.String(length=32), nullable=False),
        sa.Column("retracted_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("retracted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "fact_kind IN ('planted', 'resolved')", name="ck_foreshadow_fact_kind"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'retracted')", name="ck_foreshadow_fact_status"
        ),
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_foreshadow_fact_lock_version"
        ),
        sa.CheckConstraint(
            "(status = 'active' AND retracted_by IS NULL AND retracted_at IS NULL) OR "
            "(status = 'retracted' AND retracted_by IS NOT NULL AND retracted_at IS NOT NULL)",
            name="ck_foreshadow_fact_retraction",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "plan_id"],
            ["novel_plans.project_id", "novel_plans.id"],
            name="fk_foreshadow_fact_plan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "lifecycle_id"],
            ["foreshadow_lifecycles.project_id", "foreshadow_lifecycles.id"],
            name="fk_foreshadow_fact_lifecycle",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "chapter_id"],
            ["planning_chapters.project_id", "planning_chapters.id"],
            name="fk_foreshadow_fact_chapter",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["recorded_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["retracted_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_foreshadow_fact_project_id_id"
        ),
    )
    op.create_index(
        "uq_foreshadow_facts_active_kind",
        "foreshadow_facts",
        ["lifecycle_id", "fact_kind"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_foreshadow_facts_chapter_status",
        "foreshadow_facts",
        ["chapter_id", "status", "id"],
    )

    op.create_table(
        "foreshadow_lifecycle_events",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("lifecycle_id", sa.String(length=32), nullable=False),
        sa.Column("performed_by", sa.String(length=32), nullable=False),
        sa.Column("event_kind", sa.String(length=30), nullable=False),
        sa.Column("plan_item_id", sa.String(length=32), nullable=True),
        sa.Column("fact_id", sa.String(length=32), nullable=True),
        sa.Column("previous_lifecycle_version", sa.Integer(), nullable=False),
        sa.Column("new_lifecycle_version", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "event_kind IN ('create', 'archive', 'restore', 'plan_create', "
            "'plan_cancel', 'plan_restore', 'fact_record', 'fact_retract')",
            name="ck_foreshadow_event_kind",
        ),
        sa.CheckConstraint(
            "previous_lifecycle_version >= 0 AND new_lifecycle_version >= 1",
            name="ck_foreshadow_event_versions",
        ),
        sa.CheckConstraint(
            "(event_kind IN ('create', 'archive', 'restore') AND plan_item_id IS NULL "
            "AND fact_id IS NULL) OR "
            "(event_kind IN ('plan_create', 'plan_cancel', 'plan_restore') "
            "AND plan_item_id IS NOT NULL AND fact_id IS NULL) OR "
            "(event_kind IN ('fact_record', 'fact_retract') AND fact_id IS NOT NULL "
            "AND plan_item_id IS NULL)",
            name="ck_foreshadow_event_target",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "lifecycle_id"],
            ["foreshadow_lifecycles.project_id", "foreshadow_lifecycles.id"],
            name="fk_foreshadow_event_lifecycle",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "plan_item_id"],
            ["foreshadow_plan_items.project_id", "foreshadow_plan_items.id"],
            name="fk_foreshadow_event_plan_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "fact_id"],
            ["foreshadow_facts.project_id", "foreshadow_facts.id"],
            name="fk_foreshadow_event_fact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["performed_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_foreshadow_event_project_id_id"
        ),
        sa.UniqueConstraint(
            "project_id",
            "lifecycle_id",
            "id",
            name="uq_foreshadow_event_project_lifecycle_id",
        ),
    )
    op.create_index(
        "ix_foreshadow_events_lifecycle_created",
        "foreshadow_lifecycle_events",
        ["lifecycle_id", "created_at", "id"],
    )

    op.create_table(
        "foreshadow_operations",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=32), nullable=False),
        sa.Column("operation_key", sa.String(length=128), nullable=False),
        sa.Column("operation_type", sa.String(length=50), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("lifecycle_id", sa.String(length=32), nullable=False),
        sa.Column("event_id", sa.String(length=32), nullable=False),
        sa.Column("result_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["project_id", "lifecycle_id"],
            ["foreshadow_lifecycles.project_id", "foreshadow_lifecycles.id"],
            name="fk_foreshadow_operation_lifecycle",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "lifecycle_id", "event_id"],
            [
                "foreshadow_lifecycle_events.project_id",
                "foreshadow_lifecycle_events.lifecycle_id",
                "foreshadow_lifecycle_events.id",
            ],
            name="fk_foreshadow_operation_event",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_foreshadow_operation_key",
        ),
    )
    op.create_index(
        "ix_foreshadow_operations_project_created",
        "foreshadow_operations",
        ["project_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("foreshadow_operations")
    op.drop_table("foreshadow_lifecycle_events")
    op.drop_table("foreshadow_facts")
    op.drop_table("foreshadow_plan_items")
    op.drop_table("foreshadow_lifecycles")
