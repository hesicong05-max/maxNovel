"""Add audit structures for non-destructive lore merge preview.

Revision ID: d0a2b6c8e009
Revises: c9f1a5b7d008
Create Date: 2026-08-06 17:37:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d0a2b6c8e009"
down_revision: Union[str, None] = "c9f1a5b7d008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("setting_elements") as batch_op:
        batch_op.add_column(
            sa.Column("merged_into_element_id", sa.String(length=32), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_setting_element_merged_into",
            "setting_elements",
            ["project_id", "merged_into_element_id"],
            ["project_id", "id"],
            deferrable=True,
            initially="DEFERRED",
        )
        batch_op.create_index(
            "ix_setting_elements_merged_into_element_id",
            ["merged_into_element_id"],
        )

    op.create_table(
        "lore_merge_operations",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "project_id", sa.String(length=32),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "performed_by", sa.String(length=32),
            sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("operation_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("suggestion_project_id", sa.String(length=32), nullable=True),
        sa.Column("suggestion_id", sa.String(length=32), nullable=True),
        sa.Column("evidence_revision", sa.Integer(), nullable=False),
        sa.Column("survivor_element_id", sa.String(length=32), nullable=False),
        sa.Column("merged_element_id", sa.String(length=32), nullable=False),
        sa.Column("survivor_before_content_version", sa.Integer(), nullable=False),
        sa.Column("survivor_before_lock_version", sa.Integer(), nullable=False),
        sa.Column("merged_before_content_version", sa.Integer(), nullable=False),
        sa.Column("merged_before_lock_version", sa.Integer(), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("relation_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("selection_snapshot", sa.JSON(), nullable=False),
        sa.Column("plan_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("impact_summary", sa.JSON(), nullable=False),
        sa.Column("survivor_after_content_version", sa.Integer(), nullable=False),
        sa.Column("survivor_after_lock_version", sa.Integer(), nullable=False),
        sa.Column("merged_after_lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["suggestion_project_id", "suggestion_id"],
            ["lore_review_suggestions.project_id", "lore_review_suggestions.id"],
            name="fk_lore_merge_operation_suggestion", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "survivor_element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_lore_merge_operation_survivor", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "merged_element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_lore_merge_operation_merged", ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id", "performed_by", "operation_key",
            name="uq_lore_merge_operation_key",
        ),
        sa.UniqueConstraint(
            "project_id", "merged_element_id",
            name="uq_lore_merge_operation_merged_element",
        ),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_lore_merge_operation_project_id_id",
        ),
        sa.CheckConstraint(
            "survivor_element_id <> merged_element_id",
            name="ck_lore_merge_operation_distinct_elements",
        ),
        sa.CheckConstraint(
            "(suggestion_project_id IS NULL AND suggestion_id IS NULL) OR "
            "(suggestion_project_id IS NOT NULL AND suggestion_id IS NOT NULL "
            "AND suggestion_project_id = project_id)",
            name="ck_lore_merge_operation_suggestion_scope",
        ),
    )
    op.create_index(
        "ix_lore_merge_operations_project_id",
        "lore_merge_operations", ["project_id"],
    )
    op.create_index(
        "ix_lore_merge_operations_performed_by",
        "lore_merge_operations", ["performed_by"],
    )
    op.create_index(
        "ix_lore_merge_operations_suggestion_id",
        "lore_merge_operations", ["suggestion_id"],
    )
    op.create_index(
        "ix_lore_merge_operations_survivor_element_id",
        "lore_merge_operations", ["survivor_element_id"],
    )
    op.create_index(
        "ix_lore_merge_operations_merged_element_id",
        "lore_merge_operations", ["merged_element_id"],
    )
    op.create_index(
        "ix_lore_merge_operations_project_created",
        "lore_merge_operations", ["project_id", "created_at", "id"],
    )

    op.create_table(
        "lore_merge_relation_actions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "project_id", sa.String(length=32),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("merge_operation_id", sa.String(length=32), nullable=False),
        sa.Column("relation_project_id", sa.String(length=32), nullable=True),
        sa.Column("relation_id", sa.String(length=32), nullable=True),
        sa.Column(
            "retained_relation_project_id", sa.String(length=32), nullable=True,
        ),
        sa.Column("retained_relation_id", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=False),
        sa.Column("after_snapshot", sa.JSON(), nullable=False),
        sa.Column("previous_lock_version", sa.Integer(), nullable=False),
        sa.Column("new_lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "merge_operation_id"],
            ["lore_merge_operations.project_id", "lore_merge_operations.id"],
            name="fk_lore_merge_relation_action_operation", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["relation_project_id", "relation_id"],
            ["element_relations.project_id", "element_relations.id"],
            name="fk_lore_merge_relation_action_relation", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["retained_relation_project_id", "retained_relation_id"],
            ["element_relations.project_id", "element_relations.id"],
            name="fk_lore_merge_relation_action_retained_relation",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "action IN ('rewired', 'exact_duplicate_archived', "
            "'self_loop_archived')",
            name="ck_lore_merge_relation_action",
        ),
        sa.CheckConstraint(
            "(relation_project_id IS NULL AND relation_id IS NULL) OR "
            "(relation_project_id IS NOT NULL AND relation_id IS NOT NULL "
            "AND relation_project_id = project_id)",
            name="ck_lore_merge_relation_action_relation_scope",
        ),
        sa.CheckConstraint(
            "(retained_relation_project_id IS NULL AND "
            "retained_relation_id IS NULL) OR "
            "(retained_relation_project_id IS NOT NULL AND "
            "retained_relation_id IS NOT NULL AND "
            "retained_relation_project_id = project_id)",
            name="ck_lore_merge_relation_action_retained_scope",
        ),
        sa.UniqueConstraint(
            "merge_operation_id", "relation_id",
            name="uq_lore_merge_relation_action_relation",
        ),
    )
    op.create_index(
        "ix_lore_merge_relation_actions_project_id",
        "lore_merge_relation_actions", ["project_id"],
    )
    op.create_index(
        "ix_lore_merge_relation_actions_merge_operation_id",
        "lore_merge_relation_actions", ["merge_operation_id"],
    )
    op.create_index(
        "ix_lore_merge_relation_actions_relation_id",
        "lore_merge_relation_actions", ["relation_id"],
    )
    op.create_index(
        "ix_lore_merge_relation_actions_operation",
        "lore_merge_relation_actions", ["merge_operation_id", "id"],
    )


def downgrade() -> None:
    op.drop_table("lore_merge_relation_actions")
    op.drop_table("lore_merge_operations")
    with op.batch_alter_table("setting_elements") as batch_op:
        batch_op.drop_index("ix_setting_elements_merged_into_element_id")
        batch_op.drop_constraint(
            "fk_setting_element_merged_into", type_="foreignkey"
        )
        batch_op.drop_column("merged_into_element_id")
