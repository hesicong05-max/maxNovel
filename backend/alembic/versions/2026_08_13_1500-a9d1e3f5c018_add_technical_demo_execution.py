"""Add isolated technical-demo executions and candidate provenance.

Revision ID: a9d1e3f5c018
Revises: f8c0e2a4b017
Create Date: 2026-08-13 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9d1e3f5c018"
down_revision: str | None = "f8c0e2a4b017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_ORIGIN = "origin_kind IN ('generated', 'manual_edit')"
_OLD_SHAPE = (
    "(origin_kind = 'generated' AND source_attempt_id IS NOT NULL "
    "AND parent_candidate_id IS NULL) OR "
    "(origin_kind = 'manual_edit' AND source_attempt_id IS NULL "
    "AND parent_candidate_id IS NOT NULL)"
)
_NEW_ORIGIN = "origin_kind IN ('generated', 'manual_edit', 'technical_demo')"
_NEW_SHAPE = (
    "(origin_kind = 'generated' AND source_attempt_id IS NOT NULL "
    "AND source_technical_demo_execution_id IS NULL "
    "AND parent_candidate_id IS NULL) OR "
    "(origin_kind = 'technical_demo' AND source_attempt_id IS NULL "
    "AND source_technical_demo_execution_id IS NOT NULL "
    "AND parent_candidate_id IS NULL) OR "
    "(origin_kind = 'manual_edit' AND source_attempt_id IS NULL "
    "AND source_technical_demo_execution_id IS NULL "
    "AND parent_candidate_id IS NOT NULL)"
)


def upgrade() -> None:
    op.create_table(
        "chapter_technical_demo_executions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=32), nullable=False),
        sa.Column("operation_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("execution_mode", sa.String(length=30), nullable=False),
        sa.Column("ai_invoked", sa.Boolean(), nullable=False),
        sa.Column("billing_effect", sa.String(length=20), nullable=False),
        sa.Column("usage_status", sa.String(length=20), nullable=False),
        sa.Column("fixture_version", sa.Integer(), nullable=False),
        sa.Column("adapter_schema_version", sa.Integer(), nullable=False),
        sa.Column("content_spec_version", sa.Integer(), nullable=False),
        sa.Column("context_checksum", sa.String(length=64), nullable=False),
        sa.Column("capability_checksum", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status = 'succeeded' AND execution_mode = 'technical_demo' "
            "AND ai_invoked IS FALSE AND billing_effect = 'none' "
            "AND usage_status = 'not_applicable'",
            name="ck_technical_demo_execution_shape",
        ),
        sa.CheckConstraint(
            "fixture_version = 1 AND adapter_schema_version = 1 "
            "AND content_spec_version = 1",
            name="ck_technical_demo_execution_versions",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64 "
            "AND length(context_checksum) = 64 "
            "AND length(capability_checksum) = 64",
            name="ck_technical_demo_execution_checksums",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_id", "run_id"],
            ["chapter_generation_runs.project_id", "chapter_generation_runs.id"],
            name="fk_technical_demo_execution_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_technical_demo_execution_operation_key",
        ),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_technical_demo_execution_project_id_id"
        ),
        sa.UniqueConstraint(
            "project_id",
            "id",
            "run_id",
            name="uq_technical_demo_execution_identity",
        ),
    )
    op.create_index(
        "ix_chapter_technical_demo_executions_project_id",
        "chapter_technical_demo_executions",
        ["project_id"],
    )
    op.create_index(
        "ix_chapter_technical_demo_executions_run_id",
        "chapter_technical_demo_executions",
        ["run_id"],
    )
    op.create_index(
        "ix_chapter_technical_demo_executions_requested_by",
        "chapter_technical_demo_executions",
        ["requested_by"],
    )
    op.create_index(
        "ix_technical_demo_executions_run_created",
        "chapter_technical_demo_executions",
        ["run_id", "created_at", "id"],
    )

    with op.batch_alter_table("chapter_generation_candidates") as batch_op:
        batch_op.add_column(
            sa.Column(
                "source_technical_demo_execution_id",
                sa.String(length=32),
                nullable=True,
            )
        )
        batch_op.drop_constraint("ck_generation_candidate_origin", type_="check")
        batch_op.drop_constraint("ck_generation_candidate_origin_shape", type_="check")
        batch_op.create_check_constraint("ck_generation_candidate_origin", _NEW_ORIGIN)
        batch_op.create_check_constraint(
            "ck_generation_candidate_origin_shape", _NEW_SHAPE
        )
        batch_op.create_foreign_key(
            "fk_generation_candidate_technical_demo_execution",
            "chapter_technical_demo_executions",
            ["project_id", "source_technical_demo_execution_id", "run_id"],
            ["project_id", "id", "run_id"],
            ondelete="CASCADE",
        )
        batch_op.create_unique_constraint(
            "uq_generation_candidate_technical_demo_execution",
            ["project_id", "source_technical_demo_execution_id"],
        )
        batch_op.create_index(
            "ix_chapter_generation_candidates_source_technical_demo_execution_id",
            ["source_technical_demo_execution_id"],
        )


def downgrade() -> None:
    connection = op.get_bind()
    demo_candidates = connection.scalar(
        sa.text(
            "SELECT count(*) FROM chapter_generation_candidates "
            "WHERE source_technical_demo_execution_id IS NOT NULL"
        )
    )
    demo_executions = connection.scalar(
        sa.text("SELECT count(*) FROM chapter_technical_demo_executions")
    )
    if int(demo_candidates or 0) or int(demo_executions or 0):
        raise RuntimeError(
            "refusing downgrade: technical-demo executions or candidates exist"
        )

    with op.batch_alter_table("chapter_generation_candidates") as batch_op:
        batch_op.drop_index(
            "ix_chapter_generation_candidates_source_technical_demo_execution_id"
        )
        batch_op.drop_constraint(
            "uq_generation_candidate_technical_demo_execution", type_="unique"
        )
        batch_op.drop_constraint(
            "fk_generation_candidate_technical_demo_execution", type_="foreignkey"
        )
        batch_op.drop_constraint("ck_generation_candidate_origin_shape", type_="check")
        batch_op.drop_constraint("ck_generation_candidate_origin", type_="check")
        batch_op.create_check_constraint("ck_generation_candidate_origin", _OLD_ORIGIN)
        batch_op.create_check_constraint(
            "ck_generation_candidate_origin_shape", _OLD_SHAPE
        )
        batch_op.drop_column("source_technical_demo_execution_id")

    op.drop_index(
        "ix_technical_demo_executions_run_created",
        table_name="chapter_technical_demo_executions",
    )
    op.drop_index(
        "ix_chapter_technical_demo_executions_requested_by",
        table_name="chapter_technical_demo_executions",
    )
    op.drop_index(
        "ix_chapter_technical_demo_executions_run_id",
        table_name="chapter_technical_demo_executions",
    )
    op.drop_index(
        "ix_chapter_technical_demo_executions_project_id",
        table_name="chapter_technical_demo_executions",
    )
    op.drop_table("chapter_technical_demo_executions")
