"""Add durable receipts for legacy Lore upgrade operations.

Revision ID: a3d5e7f9b012
Revises: f2c4d8e0a011
Create Date: 2026-08-07 18:40:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3d5e7f9b012"
down_revision: Union[str, None] = "f2c4d8e0a011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_lore_migration_operations",
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
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "source_worldview_id",
            sa.String(length=32),
            sa.ForeignKey("worldviews.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_checksum", sa.String(length=64), nullable=False),
        sa.Column("preview_schema_version", sa.Integer(), nullable=False),
        sa.Column("mapping_version", sa.Integer(), nullable=False),
        sa.Column("semantic_result_checksum", sa.String(length=64), nullable=False),
        sa.Column("result_checksum", sa.String(length=64), nullable=True),
        sa.Column(
            "migration_id",
            sa.String(length=32),
            sa.ForeignKey("project_lore_migrations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('validating', 'ready', 'failed')",
            name="ck_project_lore_migration_operation_status",
        ),
        sa.UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_project_lore_migration_operation_key",
        ),
    )
    op.create_index(
        "ix_project_lore_migration_operations_project_id",
        "project_lore_migration_operations",
        ["project_id"],
    )
    op.create_index(
        "ix_project_lore_migration_operations_requested_by",
        "project_lore_migration_operations",
        ["requested_by"],
    )
    op.create_index(
        "ix_project_lore_migration_operations_source_worldview_id",
        "project_lore_migration_operations",
        ["source_worldview_id"],
    )
    op.create_index(
        "ix_project_lore_migration_operations_migration_id",
        "project_lore_migration_operations",
        ["migration_id"],
    )
    op.create_index(
        "ix_project_lore_migration_operations_project_created",
        "project_lore_migration_operations",
        ["project_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("project_lore_migration_operations")
