"""Add durable zero-LLM chapter generation preparation records.

Revision ID: d6a8c0e2f015
Revises: c5f7a9b1d014
Create Date: 2026-08-11 14:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d6a8c0e2f015"
down_revision: Union[str, None] = "c5f7a9b1d014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chapter_generation_runs",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("plan_id", sa.String(length=32), nullable=False),
        sa.Column("planning_chapter_id", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=32), nullable=False),
        sa.Column("operation_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("execution_mode", sa.String(length=30), nullable=False),
        sa.Column("ai_invoked", sa.Boolean(), nullable=False),
        sa.Column("billing_effect", sa.String(length=20), nullable=False),
        sa.Column("structure_version", sa.Integer(), nullable=False),
        sa.Column("assignment_version", sa.Integer(), nullable=False),
        sa.Column("chapter_lock_version", sa.Integer(), nullable=False),
        sa.Column("context_schema_version", sa.Integer(), nullable=False),
        sa.Column("context_manifest", sa.JSON(), nullable=False),
        sa.Column("context_checksum", sa.String(length=64), nullable=False),
        sa.Column("context_size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('prepared')", name="ck_generation_run_status"
        ),
        sa.CheckConstraint(
            "execution_mode = 'preflight_only' AND ai_invoked IS FALSE "
            "AND billing_effect = 'none'",
            name="ck_generation_run_preflight_only",
        ),
        sa.CheckConstraint(
            "structure_version >= 1 AND assignment_version >= 1 "
            "AND chapter_lock_version >= 1",
            name="ck_generation_run_versions",
        ),
        sa.CheckConstraint(
            "context_schema_version >= 1",
            name="ck_generation_run_context_schema_version",
        ),
        sa.CheckConstraint(
            "context_size_bytes >= 0 AND context_size_bytes <= 65536",
            name="ck_generation_run_context_size",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "plan_id"],
            ["novel_plans.project_id", "novel_plans.id"],
            name="fk_generation_run_plan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "planning_chapter_id"],
            ["planning_chapters.project_id", "planning_chapters.id"],
            name="fk_generation_run_chapter",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_generation_run_operation_key",
        ),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_generation_run_project_id_id"
        ),
    )
    for column in ("project_id", "plan_id", "planning_chapter_id", "requested_by"):
        op.create_index(
            f"ix_chapter_generation_runs_{column}",
            "chapter_generation_runs",
            [column],
        )
    op.create_index(
        "ix_generation_runs_chapter_created",
        "chapter_generation_runs",
        ["planning_chapter_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("chapter_generation_runs")
