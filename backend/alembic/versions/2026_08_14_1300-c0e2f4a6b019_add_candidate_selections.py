"""Add chapter candidate selections and immutable operation receipts.

Revision ID: c0e2f4a6b019
Revises: a9d1e3f5c018
Create Date: 2026-08-14 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c0e2f4a6b019"
down_revision: str | None = "a9d1e3f5c018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chapter_generation_candidate_selection_operations",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("planning_chapter_id", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=32), nullable=False),
        sa.Column("operation_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("previous_selection_version", sa.Integer(), nullable=False),
        sa.Column("previous_run_id", sa.String(length=32), nullable=True),
        sa.Column("previous_candidate_id", sa.String(length=32), nullable=True),
        sa.Column("previous_candidate_version_no", sa.Integer(), nullable=True),
        sa.Column("previous_candidate_checksum", sa.String(length=64), nullable=True),
        sa.Column("previous_context_checksum", sa.String(length=64), nullable=True),
        sa.Column("result_selection_version", sa.Integer(), nullable=False),
        sa.Column("result_run_id", sa.String(length=32), nullable=False),
        sa.Column("result_candidate_id", sa.String(length=32), nullable=False),
        sa.Column("result_candidate_version_no", sa.Integer(), nullable=False),
        sa.Column("result_candidate_checksum", sa.String(length=64), nullable=False),
        sa.Column("result_context_checksum", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_candidate_selection_operation_fingerprint",
        ),
        sa.CheckConstraint(
            "previous_selection_version >= 0 "
            "AND result_selection_version = previous_selection_version + 1",
            name="ck_candidate_selection_operation_versions",
        ),
        sa.CheckConstraint(
            "(previous_selection_version = 0 "
            "AND previous_run_id IS NULL "
            "AND previous_candidate_id IS NULL "
            "AND previous_candidate_version_no IS NULL "
            "AND previous_candidate_checksum IS NULL "
            "AND previous_context_checksum IS NULL) OR "
            "(previous_selection_version >= 1 "
            "AND previous_run_id IS NOT NULL "
            "AND previous_candidate_id IS NOT NULL "
            "AND previous_candidate_version_no >= 1 "
            "AND length(previous_candidate_checksum) = 64 "
            "AND length(previous_context_checksum) = 64)",
            name="ck_candidate_selection_operation_previous_shape",
        ),
        sa.CheckConstraint(
            "result_candidate_version_no >= 1 "
            "AND length(result_candidate_checksum) = 64 "
            "AND length(result_context_checksum) = 64",
            name="ck_candidate_selection_operation_result_shape",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_id", "planning_chapter_id"],
            ["planning_chapters.project_id", "planning_chapters.id"],
            name="fk_candidate_selection_operation_chapter",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["project_id", "previous_candidate_id", "previous_run_id"],
            [
                "chapter_generation_candidates.project_id",
                "chapter_generation_candidates.id",
                "chapter_generation_candidates.run_id",
            ],
            name="fk_candidate_selection_operation_previous_candidate",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "result_candidate_id", "result_run_id"],
            [
                "chapter_generation_candidates.project_id",
                "chapter_generation_candidates.id",
                "chapter_generation_candidates.run_id",
            ],
            name="fk_candidate_selection_operation_result_candidate",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_candidate_selection_operation_key",
        ),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_candidate_selection_operation_project_id_id",
        ),
        sa.UniqueConstraint(
            "project_id",
            "planning_chapter_id",
            "result_selection_version",
            name="uq_candidate_selection_operation_chapter_version",
        ),
    )
    op.create_index(
        "ix_candidate_selection_operations_project_id",
        "chapter_generation_candidate_selection_operations",
        ["project_id"],
    )
    op.create_index(
        "ix_candidate_selection_operations_chapter_id",
        "chapter_generation_candidate_selection_operations",
        ["planning_chapter_id"],
    )
    op.create_index(
        "ix_candidate_selection_operations_requested_by",
        "chapter_generation_candidate_selection_operations",
        ["requested_by"],
    )
    op.create_index(
        "ix_candidate_selection_operations_chapter_created",
        "chapter_generation_candidate_selection_operations",
        ["project_id", "planning_chapter_id", "created_at", "id"],
    )

    op.create_table(
        "chapter_generation_candidate_selections",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("planning_chapter_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("candidate_id", sa.String(length=32), nullable=False),
        sa.Column("candidate_version_no", sa.Integer(), nullable=False),
        sa.Column("candidate_checksum", sa.String(length=64), nullable=False),
        sa.Column("context_checksum", sa.String(length=64), nullable=False),
        sa.Column("selection_version", sa.Integer(), nullable=False),
        sa.Column("changed_by", sa.String(length=32), nullable=False),
        sa.Column("last_operation_id", sa.String(length=32), nullable=False),
        sa.Column("selected_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "selection_version >= 1 AND candidate_version_no >= 1",
            name="ck_candidate_selection_versions",
        ),
        sa.CheckConstraint(
            "length(candidate_checksum) = 64 " "AND length(context_checksum) = 64",
            name="ck_candidate_selection_checksums",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_id", "planning_chapter_id"],
            ["planning_chapters.project_id", "planning_chapters.id"],
            name="fk_candidate_selection_chapter",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "candidate_id", "run_id"],
            [
                "chapter_generation_candidates.project_id",
                "chapter_generation_candidates.id",
                "chapter_generation_candidates.run_id",
            ],
            name="fk_candidate_selection_candidate",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "last_operation_id"],
            [
                "chapter_generation_candidate_selection_operations.project_id",
                "chapter_generation_candidate_selection_operations.id",
            ],
            name="fk_candidate_selection_last_operation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "project_id",
            "planning_chapter_id",
            name="uq_candidate_selection_project_chapter",
        ),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_candidate_selection_project_id_id"
        ),
    )
    op.create_index(
        "ix_candidate_selections_project_id",
        "chapter_generation_candidate_selections",
        ["project_id"],
    )
    op.create_index(
        "ix_candidate_selections_chapter_id",
        "chapter_generation_candidate_selections",
        ["planning_chapter_id"],
    )
    op.create_index(
        "ix_candidate_selections_run_id",
        "chapter_generation_candidate_selections",
        ["run_id"],
    )
    op.create_index(
        "ix_candidate_selections_candidate_id",
        "chapter_generation_candidate_selections",
        ["candidate_id"],
    )
    op.create_index(
        "ix_candidate_selections_changed_by",
        "chapter_generation_candidate_selections",
        ["changed_by"],
    )
    op.create_index(
        "ix_candidate_selections_candidate",
        "chapter_generation_candidate_selections",
        ["project_id", "candidate_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    selections = connection.scalar(
        sa.text("SELECT count(*) FROM chapter_generation_candidate_selections")
    )
    operations = connection.scalar(
        sa.text(
            "SELECT count(*) " "FROM chapter_generation_candidate_selection_operations"
        )
    )
    if int(selections or 0) or int(operations or 0):
        raise RuntimeError(
            "refusing downgrade: candidate selections or operation receipts exist"
        )

    op.drop_index(
        "ix_candidate_selections_candidate",
        table_name="chapter_generation_candidate_selections",
    )
    op.drop_index(
        "ix_candidate_selections_changed_by",
        table_name="chapter_generation_candidate_selections",
    )
    op.drop_index(
        "ix_candidate_selections_candidate_id",
        table_name="chapter_generation_candidate_selections",
    )
    op.drop_index(
        "ix_candidate_selections_run_id",
        table_name="chapter_generation_candidate_selections",
    )
    op.drop_index(
        "ix_candidate_selections_chapter_id",
        table_name="chapter_generation_candidate_selections",
    )
    op.drop_index(
        "ix_candidate_selections_project_id",
        table_name="chapter_generation_candidate_selections",
    )
    op.drop_table("chapter_generation_candidate_selections")

    op.drop_index(
        "ix_candidate_selection_operations_chapter_created",
        table_name="chapter_generation_candidate_selection_operations",
    )
    op.drop_index(
        "ix_candidate_selection_operations_requested_by",
        table_name="chapter_generation_candidate_selection_operations",
    )
    op.drop_index(
        "ix_candidate_selection_operations_chapter_id",
        table_name="chapter_generation_candidate_selection_operations",
    )
    op.drop_index(
        "ix_candidate_selection_operations_project_id",
        table_name="chapter_generation_candidate_selection_operations",
    )
    op.drop_table("chapter_generation_candidate_selection_operations")
