"""Add durable idempotency receipts for manual lore creation.

Revision ID: f6c8d2e4a005
Revises: e5b7c1f3a004
Create Date: 2026-08-06 13:20:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6c8d2e4a005"
down_revision: Union[str, None] = "e5b7c1f3a004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lore_element_create_operations",
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
        sa.Column(
            "element_id",
            sa.String(length=32),
            sa.ForeignKey("setting_elements.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_lore_element_create_operation_element",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_lore_element_create_operation_key",
        ),
        sa.UniqueConstraint(
            "project_id",
            "element_id",
            name="uq_lore_element_create_operation_element",
        ),
    )
    op.create_index(
        "ix_lore_element_create_operations_project_id",
        "lore_element_create_operations",
        ["project_id"],
    )
    op.create_index(
        "ix_lore_element_create_operations_requested_by",
        "lore_element_create_operations",
        ["requested_by"],
    )
    op.create_index(
        "ix_lore_element_create_operations_element_id",
        "lore_element_create_operations",
        ["element_id"],
    )
    op.create_index(
        "ix_lore_element_create_operations_project_created",
        "lore_element_create_operations",
        ["project_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("lore_element_create_operations")
