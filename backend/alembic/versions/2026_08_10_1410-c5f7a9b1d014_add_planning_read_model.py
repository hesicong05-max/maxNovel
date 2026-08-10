"""Add the isolated relational read model for chapter planning.

Revision ID: c5f7a9b1d014
Revises: b4e6f8a0c013
Create Date: 2026-08-10 14:10:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5f7a9b1d014"
down_revision: Union[str, None] = "b4e6f8a0c013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "novel_plans",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("structure_version", sa.Integer(), nullable=False),
        sa.Column("assignment_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name="ck_novel_plan_status"
        ),
        sa.CheckConstraint(
            "structure_version >= 1 AND assignment_version >= 1",
            name="ck_novel_plan_versions",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", name="uq_novel_plan_project"),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_novel_plan_project_id_id"
        ),
    )
    op.create_index("ix_novel_plans_project_id", "novel_plans", ["project_id"])

    op.create_table(
        "planning_parts",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("plan_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("position >= 1", name="ck_planning_part_position"),
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_planning_part_lock_version"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name="ck_planning_part_status"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "plan_id"],
            ["novel_plans.project_id", "novel_plans.id"],
            name="fk_planning_part_plan",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_planning_part_project_id_id"
        ),
    )
    op.create_index("ix_planning_parts_project_id", "planning_parts", ["project_id"])
    op.create_index("ix_planning_parts_plan_id", "planning_parts", ["plan_id"])
    op.create_index(
        "ix_planning_parts_plan_status_position",
        "planning_parts",
        ["plan_id", "status", "position", "id"],
    )
    op.create_index(
        "uq_planning_parts_active_position",
        "planning_parts",
        ["plan_id", "position"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "planning_chapters",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("plan_id", sa.String(length=32), nullable=False),
        sa.Column("part_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("target_word_count", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("position >= 1", name="ck_planning_chapter_position"),
        sa.CheckConstraint(
            "target_word_count IS NULL OR "
            "(target_word_count >= 500 AND target_word_count <= 10000)",
            name="ck_planning_chapter_target_words",
        ),
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_planning_chapter_lock_version"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name="ck_planning_chapter_status"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "plan_id"],
            ["novel_plans.project_id", "novel_plans.id"],
            name="fk_planning_chapter_plan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "part_id"],
            ["planning_parts.project_id", "planning_parts.id"],
            name="fk_planning_chapter_part",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_planning_chapter_project_id_id"
        ),
    )
    for column in ("project_id", "plan_id", "part_id"):
        op.create_index(
            f"ix_planning_chapters_{column}", "planning_chapters", [column]
        )
    op.create_index(
        "ix_planning_chapters_part_status_position",
        "planning_chapters",
        ["part_id", "status", "position", "id"],
    )
    op.create_index(
        "uq_planning_chapters_active_position",
        "planning_chapters",
        ["part_id", "position"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "planning_lore_assignments",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("plan_id", sa.String(length=32), nullable=False),
        sa.Column("element_id", sa.String(length=32), nullable=False),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("scope_target_id", sa.String(length=32), nullable=False),
        sa.Column("part_id", sa.String(length=32), nullable=True),
        sa.Column("chapter_id", sa.String(length=32), nullable=True),
        sa.Column("element_content_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("updated_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "scope_type IN ('novel', 'part', 'chapter')",
            name="ck_planning_lore_assignment_scope",
        ),
        sa.CheckConstraint(
            "(scope_type = 'novel' AND scope_target_id = project_id "
            "AND part_id IS NULL AND chapter_id IS NULL) OR "
            "(scope_type = 'part' AND scope_target_id = part_id "
            "AND part_id IS NOT NULL AND chapter_id IS NULL) OR "
            "(scope_type = 'chapter' AND scope_target_id = chapter_id "
            "AND part_id IS NULL AND chapter_id IS NOT NULL)",
            name="ck_planning_lore_assignment_target",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'removed')",
            name="ck_planning_lore_assignment_status",
        ),
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_planning_lore_assignment_lock_version"
        ),
        sa.CheckConstraint(
            "element_content_version >= 1",
            name="ck_planning_lore_assignment_element_version",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "plan_id"],
            ["novel_plans.project_id", "novel_plans.id"],
            name="fk_planning_lore_assignment_plan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_planning_lore_assignment_element",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "part_id"],
            ["planning_parts.project_id", "planning_parts.id"],
            name="fk_planning_lore_assignment_part",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "chapter_id"],
            ["planning_chapters.project_id", "planning_chapters.id"],
            name="fk_planning_lore_assignment_chapter",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "project_id",
            "element_id",
            "scope_type",
            "scope_target_id",
            name="uq_planning_lore_assignment_target_element",
        ),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_planning_lore_assignment_project_id_id"
        ),
    )
    for column in ("project_id", "plan_id", "element_id"):
        op.create_index(
            f"ix_planning_lore_assignments_{column}",
            "planning_lore_assignments",
            [column],
        )
    op.create_index(
        "ix_planning_lore_assignments_plan_scope_status",
        "planning_lore_assignments",
        ["plan_id", "scope_type", "status", "scope_target_id", "id"],
    )
    op.create_index(
        "ix_planning_lore_assignments_element_status",
        "planning_lore_assignments",
        ["element_id", "status", "id"],
    )

    op.create_table(
        "planning_lore_assignment_events",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("assignment_id", sa.String(length=32), nullable=False),
        sa.Column("performed_by", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("previous_status", sa.String(length=20), nullable=True),
        sa.Column("new_status", sa.String(length=20), nullable=False),
        sa.Column("previous_lock_version", sa.Integer(), nullable=False),
        sa.Column("new_lock_version", sa.Integer(), nullable=False),
        sa.Column("element_content_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "action IN ('assign', 'remove', 'restore')",
            name="ck_planning_lore_assignment_event_action",
        ),
        sa.CheckConstraint(
            "previous_lock_version >= 0 AND new_lock_version >= 1",
            name="ck_planning_lore_assignment_event_versions",
        ),
        sa.CheckConstraint(
            "element_content_version >= 1",
            name="ck_planning_lore_assignment_event_element_version",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "assignment_id"],
            ["planning_lore_assignments.project_id", "planning_lore_assignments.id"],
            name="fk_planning_lore_assignment_event_assignment",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["performed_by"], ["users.id"], ondelete="RESTRICT"),
    )
    for column in ("project_id", "assignment_id"):
        op.create_index(
            f"ix_planning_lore_assignment_events_{column}",
            "planning_lore_assignment_events",
            [column],
        )
    op.create_index(
        "ix_planning_lore_assignment_events_assignment_created",
        "planning_lore_assignment_events",
        ["assignment_id", "created_at", "id"],
    )

    op.create_table(
        "planning_mutation_operations",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=32), nullable=False),
        sa.Column("operation_key", sa.String(length=128), nullable=False),
        sa.Column("operation_type", sa.String(length=50), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("result_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_planning_mutation_operation_key",
        ),
    )
    op.create_index(
        "ix_planning_mutation_operations_project_id",
        "planning_mutation_operations",
        ["project_id"],
    )
    op.create_index(
        "ix_planning_mutation_operations_project_created",
        "planning_mutation_operations",
        ["project_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("planning_mutation_operations")
    op.drop_table("planning_lore_assignment_events")
    op.drop_table("planning_lore_assignments")
    op.drop_table("planning_chapters")
    op.drop_table("planning_parts")
    op.drop_table("novel_plans")
