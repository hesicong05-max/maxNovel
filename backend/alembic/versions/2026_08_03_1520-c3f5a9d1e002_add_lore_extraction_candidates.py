"""Add persistent lore extraction batches and review candidates.

Revision ID: c3f5a9d1e002
Revises: b2e4f8d0c001
Create Date: 2026-08-03 15:20:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3f5a9d1e002"
down_revision: Union[str, None] = "b2e4f8d0c001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lore_extraction_batches",
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
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("source_kind", sa.String(length=30), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=True),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("extractor_version", sa.String(length=40), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("llm_started_at", sa.DateTime(), nullable=True),
        sa.Column("llm_completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'outcome_unknown')",
            name="ck_lore_extraction_batch_status",
        ),
        sa.UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_lore_extraction_project_idempotency",
        ),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_lore_extraction_project_id_id",
        ),
    )
    op.create_index(
        "ix_lore_extraction_batches_project_id",
        "lore_extraction_batches",
        ["project_id"],
    )
    op.create_index(
        "ix_lore_extraction_batches_project_created",
        "lore_extraction_batches",
        ["project_id", "created_at", "id"],
    )

    op.create_table(
        "lore_extraction_candidates",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("batch_id", sa.String(length=32), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("deterministic_key", sa.String(length=64), nullable=False),
        sa.Column("type_key", sa.String(length=50), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("field_states", sa.JSON(), nullable=False),
        sa.Column("relation_suggestions", sa.JSON(), nullable=False),
        sa.Column("duplicate_conflict_suggestions", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("accepted_element_id", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "batch_id"],
            ["lore_extraction_batches.project_id", "lore_extraction_batches.id"],
            name="fk_lore_extraction_candidate_project_batch",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "accepted_element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_lore_extraction_candidate_project_element",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('pending_review', 'accepted', 'rejected', 'failed')",
            name="ck_lore_extraction_candidate_status",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "deterministic_key",
            name="uq_lore_extraction_candidate_key",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "ordinal",
            name="uq_lore_extraction_candidate_ordinal",
        ),
    )
    op.create_index(
        "ix_lore_extraction_candidates_project_id",
        "lore_extraction_candidates",
        ["project_id"],
    )
    op.create_index(
        "ix_lore_extraction_candidates_batch_id",
        "lore_extraction_candidates",
        ["batch_id"],
    )
    op.create_index(
        "ix_lore_extraction_candidates_batch_ordinal",
        "lore_extraction_candidates",
        ["batch_id", "ordinal"],
    )
    op.create_index(
        "ix_lore_extraction_candidates_project_status",
        "lore_extraction_candidates",
        ["project_id", "status", "updated_at"],
    )

    op.create_table(
        "lore_candidate_field_evidence",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.String(length=32),
            sa.ForeignKey("lore_extraction_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_key", sa.String(length=80), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("excerpt_hash", sa.String(length=64), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("is_name", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('provided', 'unknown', 'needs_confirmation')",
            name="ck_lore_candidate_evidence_state",
        ),
        sa.CheckConstraint(
            "char_start IS NULL OR char_start >= 0",
            name="ck_lore_candidate_evidence_start",
        ),
        sa.CheckConstraint(
            "char_end IS NULL OR char_end >= char_start",
            name="ck_lore_candidate_evidence_end",
        ),
        sa.UniqueConstraint(
            "candidate_id",
            "field_key",
            name="uq_lore_candidate_field_evidence",
        ),
    )
    op.create_index(
        "ix_lore_candidate_field_evidence_candidate_id",
        "lore_candidate_field_evidence",
        ["candidate_id"],
    )
    op.create_index(
        "ix_lore_candidate_evidence_candidate",
        "lore_candidate_field_evidence",
        ["candidate_id", "field_key"],
    )


def downgrade() -> None:
    op.drop_table("lore_candidate_field_evidence")
    op.drop_table("lore_extraction_candidates")
    op.drop_table("lore_extraction_batches")
