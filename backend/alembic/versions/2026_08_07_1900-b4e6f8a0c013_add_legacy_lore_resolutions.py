"""Add auditable author resolutions for legacy Lore preview issues.

Revision ID: b4e6f8a0c013
Revises: a3d5e7f9b012
Create Date: 2026-08-07 19:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4e6f8a0c013"
down_revision: Union[str, None] = "a3d5e7f9b012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "legacy_lore_resolutions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("source_worldview_id", sa.String(length=32), nullable=False),
        sa.Column("preview_schema_version", sa.Integer(), nullable=False),
        sa.Column("mapping_version", sa.Integer(), nullable=False),
        sa.Column("source_checksum", sa.String(length=64), nullable=False),
        sa.Column("semantic_result_checksum", sa.String(length=64), nullable=False),
        sa.Column("item_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("group_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("legacy_category", sa.String(length=50), nullable=False),
        sa.Column("legacy_index", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column("decision_code", sa.String(length=80), nullable=False),
        sa.Column("decision_payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("updated_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_legacy_lore_resolution_status",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_worldview_id"], ["worldviews.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_legacy_lore_resolution_project_id_id"
        ),
        sa.UniqueConstraint(
            "project_id",
            "preview_schema_version",
            "mapping_version",
            "source_checksum",
            "item_fingerprint",
            "reason_code",
            name="uq_legacy_lore_resolution_issue",
        ),
    )
    for column in ("project_id", "source_worldview_id", "created_by", "updated_by"):
        op.create_index(
            f"ix_legacy_lore_resolutions_{column}",
            "legacy_lore_resolutions",
            [column],
        )
    op.create_index(
        "ix_legacy_lore_resolutions_project_updated",
        "legacy_lore_resolutions",
        ["project_id", "updated_at", "id"],
    )

    op.create_table(
        "legacy_lore_resolution_events",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("resolution_id", sa.String(length=32), nullable=False),
        sa.Column("performed_by", sa.String(length=32), nullable=False),
        sa.Column("operation_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("previous_status", sa.String(length=20), nullable=True),
        sa.Column("new_status", sa.String(length=20), nullable=False),
        sa.Column("previous_decision", sa.JSON(), nullable=False),
        sa.Column("new_decision", sa.JSON(), nullable=False),
        sa.Column("previous_lock_version", sa.Integer(), nullable=False),
        sa.Column("new_lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "action IN ('decide', 'replace', 'revoke')",
            name="ck_legacy_lore_resolution_event_action",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_id", "resolution_id"],
            ["legacy_lore_resolutions.project_id", "legacy_lore_resolutions.id"],
            name="fk_legacy_lore_resolution_event_resolution",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["performed_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "project_id", "performed_by", "operation_key",
            name="uq_legacy_lore_resolution_event_operation",
        ),
    )
    for column in ("project_id", "resolution_id", "performed_by"):
        op.create_index(
            f"ix_legacy_lore_resolution_events_{column}",
            "legacy_lore_resolution_events",
            [column],
        )
    op.create_index(
        "ix_legacy_lore_resolution_events_resolution_created",
        "legacy_lore_resolution_events",
        ["resolution_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("legacy_lore_resolution_events")
    op.drop_table("legacy_lore_resolutions")
