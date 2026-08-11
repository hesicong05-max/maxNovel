"""Add durable receipts for author-created lore review clues.

Revision ID: f2c4d8e0a011
Revises: e1b3c7d9f010
Create Date: 2026-08-07 17:15:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2c4d8e0a011"
down_revision: Union[str, None] = "e1b3c7d9f010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lore_review_suggestion_create_operations",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=32),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by",
            sa.String(length=32),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("operation_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("suggestion_id", sa.String(length=32), nullable=False),
        sa.Column(
            "created_suggestion",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "suggestion_id"],
            ["lore_review_suggestions.project_id", "lore_review_suggestions.id"],
            name="fk_lore_review_create_operation_suggestion",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_lore_review_create_operation_key",
        ),
    )
    op.create_index(
        "ix_lore_review_suggestion_create_operations_project_id",
        "lore_review_suggestion_create_operations",
        ["project_id"],
    )
    op.create_index(
        "ix_lore_review_suggestion_create_operations_requested_by",
        "lore_review_suggestion_create_operations",
        ["requested_by"],
    )
    op.create_index(
        "ix_lore_review_suggestion_create_operations_suggestion_id",
        "lore_review_suggestion_create_operations",
        ["suggestion_id"],
    )
    op.create_index(
        "ix_lore_review_create_operations_project_created",
        "lore_review_suggestion_create_operations",
        ["project_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("lore_review_suggestion_create_operations")
