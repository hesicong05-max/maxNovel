"""Add non-destructive formal lore review suggestions.

Revision ID: c9f1a5b7d008
Revises: b8e0f4a6c007
Create Date: 2026-08-06 16:40:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9f1a5b7d008"
down_revision: Union[str, None] = "b8e0f4a6c007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lore_review_suggestions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "project_id", sa.String(length=32),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("left_element_id", sa.String(length=32), nullable=False),
        sa.Column("right_element_id", sa.String(length=32), nullable=False),
        sa.Column("rule_key", sa.String(length=80), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column(
            "detection_state", sa.String(length=20),
            nullable=False, server_default="active",
        ),
        sa.Column(
            "review_status", sa.String(length=30),
            nullable=False, server_default="pending",
        ),
        sa.Column("left_content_version", sa.Integer(), nullable=False),
        sa.Column("right_content_version", sa.Integer(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("evidence_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decided_evidence_revision", sa.Integer(), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "left_element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_lore_review_suggestion_left", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "right_element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_lore_review_suggestion_right", ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id", "left_element_id", "right_element_id", "rule_key",
            name="uq_lore_review_suggestion_pair_rule",
        ),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_lore_review_suggestion_project_id_id",
        ),
        sa.CheckConstraint(
            "left_element_id <> right_element_id",
            name="ck_lore_review_suggestion_distinct_elements",
        ),
        sa.CheckConstraint(
            "kind IN ('possible_duplicate', 'possible_conflict')",
            name="ck_lore_review_suggestion_kind",
        ),
        sa.CheckConstraint(
            "detection_state IN ('active', 'stale')",
            name="ck_lore_review_suggestion_detection_state",
        ),
        sa.CheckConstraint(
            "review_status IN ('pending', 'deferred', 'confirmed_duplicate', "
            "'confirmed_conflict', 'not_an_issue')",
            name="ck_lore_review_suggestion_review_status",
        ),
    )
    op.create_index(
        "ix_lore_review_suggestions_project_id",
        "lore_review_suggestions", ["project_id"],
    )
    op.create_index(
        "ix_lore_review_suggestions_left_element_id",
        "lore_review_suggestions", ["left_element_id"],
    )
    op.create_index(
        "ix_lore_review_suggestions_right_element_id",
        "lore_review_suggestions", ["right_element_id"],
    )
    op.create_index(
        "ix_lore_review_suggestions_project_status_updated",
        "lore_review_suggestions",
        ["project_id", "detection_state", "review_status", "updated_at", "id"],
    )

    op.create_table(
        "lore_review_suggestion_events",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "project_id", sa.String(length=32),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("suggestion_id", sa.String(length=32), nullable=False),
        sa.Column(
            "performed_by", sa.String(length=32),
            sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("operation_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("previous_status", sa.String(length=30), nullable=False),
        sa.Column("new_status", sa.String(length=30), nullable=False),
        sa.Column("evidence_revision", sa.Integer(), nullable=False),
        sa.Column("previous_lock_version", sa.Integer(), nullable=False),
        sa.Column("new_lock_version", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "suggestion_id"],
            ["lore_review_suggestions.project_id", "lore_review_suggestions.id"],
            name="fk_lore_review_event_suggestion", ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id", "performed_by", "operation_key",
            name="uq_lore_review_event_operation",
        ),
    )
    op.create_index(
        "ix_lore_review_suggestion_events_project_id",
        "lore_review_suggestion_events", ["project_id"],
    )
    op.create_index(
        "ix_lore_review_suggestion_events_suggestion_id",
        "lore_review_suggestion_events", ["suggestion_id"],
    )
    op.create_index(
        "ix_lore_review_suggestion_events_performed_by",
        "lore_review_suggestion_events", ["performed_by"],
    )
    op.create_index(
        "ix_lore_review_events_suggestion_created",
        "lore_review_suggestion_events", ["suggestion_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("lore_review_suggestion_events")
    op.drop_table("lore_review_suggestions")
