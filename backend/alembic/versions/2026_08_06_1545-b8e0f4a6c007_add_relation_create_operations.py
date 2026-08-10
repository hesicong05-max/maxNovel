"""Add durable idempotency receipts for relation creation.

Revision ID: b8e0f4a6c007
Revises: a7d9e3f5b006
Create Date: 2026-08-06 15:45:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8e0f4a6c007"
down_revision: Union[str, None] = "a7d9e3f5b006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("element_relations") as batch_op:
        batch_op.create_unique_constraint(
            "uq_element_relation_project_id_id",
            ["project_id", "id"],
        )

    op.create_table(
        "lore_relation_create_operations",
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
        sa.Column("relation_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "relation_id"],
            ["element_relations.project_id", "element_relations.id"],
            name="fk_lore_relation_create_operation_relation",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_lore_relation_create_operation_key",
        ),
        sa.UniqueConstraint(
            "project_id",
            "relation_id",
            name="uq_lore_relation_create_operation_relation",
        ),
    )
    op.create_index(
        "ix_lore_relation_create_operations_project_id",
        "lore_relation_create_operations",
        ["project_id"],
    )
    op.create_index(
        "ix_lore_relation_create_operations_requested_by",
        "lore_relation_create_operations",
        ["requested_by"],
    )
    op.create_index(
        "ix_lore_relation_create_operations_relation_id",
        "lore_relation_create_operations",
        ["relation_id"],
    )
    op.create_index(
        "ix_lore_relation_create_operations_project_created",
        "lore_relation_create_operations",
        ["project_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("lore_relation_create_operations")
    with op.batch_alter_table("element_relations") as batch_op:
        batch_op.drop_constraint(
            "uq_element_relation_project_id_id",
            type_="unique",
        )
