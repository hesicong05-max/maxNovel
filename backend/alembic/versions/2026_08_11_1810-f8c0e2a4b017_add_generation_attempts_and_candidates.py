"""Add durable generation attempts and immutable candidates.

Revision ID: f8c0e2a4b017
Revises: e7b9d1f3a016
Create Date: 2026-08-11 18:10:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f8c0e2a4b017"
down_revision: Union[str, None] = "e7b9d1f3a016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chapter_generation_attempts",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=32), nullable=False),
        sa.Column("operation_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("execution_mode", sa.String(length=30), nullable=False),
        sa.Column("billing_confirmed", sa.Boolean(), nullable=False),
        sa.Column("ai_invoked", sa.Boolean(), nullable=False),
        sa.Column("billing_effect", sa.String(length=20), nullable=False),
        sa.Column("capability_schema_version", sa.Integer(), nullable=False),
        sa.Column("capability_snapshot", sa.JSON(), nullable=False),
        sa.Column("capability_checksum", sa.String(length=64), nullable=False),
        sa.Column("execution_config_digest", sa.String(length=64), nullable=False),
        sa.Column("provider_name", sa.String(length=80), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("input_limit_availability", sa.String(length=20), nullable=False),
        sa.Column("max_input_tokens", sa.Integer(), nullable=True),
        sa.Column("price_availability", sa.String(length=20), nullable=False),
        sa.Column("prompt_schema_version", sa.Integer(), nullable=False),
        sa.Column("prompt_checksum", sa.String(length=64), nullable=False),
        sa.Column("context_checksum", sa.String(length=64), nullable=False),
        sa.Column("usage_status", sa.String(length=20), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('reserved', 'calling', 'succeeded', 'failed', "
            "'outcome_unknown')",
            name="ck_generation_attempt_status",
        ),
        sa.CheckConstraint(
            "execution_mode = 'single_call' AND billing_confirmed IS TRUE",
            name="ck_generation_attempt_execution_mode",
        ),
        sa.CheckConstraint(
            "billing_effect IN ('none', 'possible')",
            name="ck_generation_attempt_billing_effect",
        ),
        sa.CheckConstraint(
            "prompt_schema_version >= 1 AND capability_schema_version >= 1 "
            "AND lock_version >= 1",
            name="ck_generation_attempt_versions",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64 AND "
            "length(prompt_checksum) = 64 AND length(context_checksum) = 64 "
            "AND length(capability_checksum) = 64 "
            "AND length(execution_config_digest) = 64",
            name="ck_generation_attempt_checksums",
        ),
        sa.CheckConstraint(
            "length(provider_name) BETWEEN 1 AND 80 "
            "AND length(model_name) BETWEEN 1 AND 200 "
            "AND max_output_tokens BETWEEN 1 AND 1000000 "
            "AND input_limit_availability = 'unavailable' "
            "AND max_input_tokens IS NULL "
            "AND price_availability = 'unavailable'",
            name="ck_generation_attempt_capability",
        ),
        sa.CheckConstraint(
            "usage_status IN ('reported', 'unavailable', 'unknown') AND ("
            "(usage_status = 'reported' AND input_tokens IS NOT NULL "
            "AND output_tokens IS NOT NULL AND total_tokens IS NOT NULL "
            "AND input_tokens >= 0 AND output_tokens >= 0 "
            "AND total_tokens = input_tokens + output_tokens) OR "
            "(usage_status IN ('unavailable', 'unknown') "
            "AND input_tokens IS NULL AND output_tokens IS NULL "
            "AND total_tokens IS NULL))",
            name="ck_generation_attempt_usage",
        ),
        sa.CheckConstraint(
            "(status = 'reserved' AND ai_invoked IS FALSE "
            "AND billing_effect = 'none' AND usage_status = 'unavailable' "
            "AND claimed_at IS NULL "
            "AND completed_at IS NULL AND error_code IS NULL) OR "
            "(status = 'calling' AND ai_invoked IS TRUE "
            "AND billing_effect = 'possible' AND usage_status = 'unknown' "
            "AND claimed_at IS NOT NULL "
            "AND completed_at IS NULL AND error_code IS NULL) OR "
            "(status = 'succeeded' AND ai_invoked IS TRUE "
            "AND billing_effect = 'possible' "
            "AND usage_status IN ('reported', 'unavailable') "
            "AND claimed_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND error_code IS NULL) OR "
            "(status = 'failed' AND usage_status = 'unavailable' "
            "AND completed_at IS NOT NULL "
            "AND error_code IS NOT NULL "
            "AND ((ai_invoked IS FALSE AND billing_effect = 'none' "
            "AND claimed_at IS NULL) OR "
            "(ai_invoked IS TRUE AND billing_effect = 'possible' "
            "AND claimed_at IS NOT NULL))) OR "
            "(status = 'outcome_unknown' AND ai_invoked IS TRUE "
            "AND billing_effect = 'possible' AND usage_status = 'unknown' "
            "AND claimed_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name="ck_generation_attempt_state_shape",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR length(error_code) BETWEEN 1 AND 80",
            name="ck_generation_attempt_error_code",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "run_id"],
            ["chapter_generation_runs.project_id", "chapter_generation_runs.id"],
            name="fk_generation_attempt_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_generation_attempt_operation_key",
        ),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_generation_attempt_project_id_id"
        ),
        sa.UniqueConstraint(
            "project_id",
            "id",
            "run_id",
            name="uq_generation_attempt_identity",
        ),
    )
    op.create_index(
        "ix_chapter_generation_attempts_project_id",
        "chapter_generation_attempts",
        ["project_id"],
    )
    op.create_index(
        "ix_chapter_generation_attempts_run_id",
        "chapter_generation_attempts",
        ["run_id"],
    )
    op.create_index(
        "ix_chapter_generation_attempts_requested_by",
        "chapter_generation_attempts",
        ["requested_by"],
    )
    op.create_index(
        "ix_generation_attempts_run_created",
        "chapter_generation_attempts",
        ["run_id", "created_at", "id"],
    )
    op.create_index(
        "ix_generation_attempts_user_created",
        "chapter_generation_attempts",
        ["project_id", "requested_by", "created_at", "id"],
    )

    op.create_table(
        "chapter_generation_candidates",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("source_attempt_id", sa.String(length=32), nullable=True),
        sa.Column("parent_candidate_id", sa.String(length=32), nullable=True),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("origin_kind", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_format", sa.String(length=20), nullable=False),
        sa.Column("content_checksum", sa.String(length=64), nullable=False),
        sa.Column("content_size_bytes", sa.Integer(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "origin_kind IN ('generated', 'manual_edit')",
            name="ck_generation_candidate_origin",
        ),
        sa.CheckConstraint(
            "(origin_kind = 'generated' AND source_attempt_id IS NOT NULL "
            "AND parent_candidate_id IS NULL) OR "
            "(origin_kind = 'manual_edit' AND source_attempt_id IS NULL "
            "AND parent_candidate_id IS NOT NULL)",
            name="ck_generation_candidate_origin_shape",
        ),
        sa.CheckConstraint(
            "content_format = 'plain_text'",
            name="ck_generation_candidate_content_format",
        ),
        sa.CheckConstraint(
            "version_no >= 1 AND word_count >= 1",
            name="ck_generation_candidate_versions",
        ),
        sa.CheckConstraint(
            "content_size_bytes >= 1 AND content_size_bytes <= 262144",
            name="ck_generation_candidate_content_size",
        ),
        sa.CheckConstraint(
            "length(content_checksum) = 64",
            name="ck_generation_candidate_checksum",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "run_id"],
            ["chapter_generation_runs.project_id", "chapter_generation_runs.id"],
            name="fk_generation_candidate_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            [
                "project_id",
                "source_attempt_id",
                "run_id",
            ],
            [
                "chapter_generation_attempts.project_id",
                "chapter_generation_attempts.id",
                "chapter_generation_attempts.run_id",
            ],
            name="fk_generation_candidate_attempt",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            [
                "project_id",
                "parent_candidate_id",
                "run_id",
            ],
            [
                "chapter_generation_candidates.project_id",
                "chapter_generation_candidates.id",
                "chapter_generation_candidates.run_id",
            ],
            name="fk_generation_candidate_parent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_generation_candidate_project_id_id"
        ),
        sa.UniqueConstraint(
            "project_id",
            "id",
            "run_id",
            name="uq_generation_candidate_identity",
        ),
        sa.UniqueConstraint(
            "project_id",
            "source_attempt_id",
            name="uq_generation_candidate_attempt",
        ),
        sa.UniqueConstraint(
            "project_id",
            "run_id",
            "version_no",
            name="uq_generation_candidate_run_version",
        ),
    )
    op.create_index(
        "ix_chapter_generation_candidates_project_id",
        "chapter_generation_candidates",
        ["project_id"],
    )
    op.create_index(
        "ix_chapter_generation_candidates_run_id",
        "chapter_generation_candidates",
        ["run_id"],
    )
    op.create_index(
        "ix_chapter_generation_candidates_source_attempt_id",
        "chapter_generation_candidates",
        ["source_attempt_id"],
    )
    op.create_index(
        "ix_chapter_generation_candidates_parent_candidate_id",
        "chapter_generation_candidates",
        ["parent_candidate_id"],
    )
    op.create_index(
        "ix_chapter_generation_candidates_created_by",
        "chapter_generation_candidates",
        ["created_by"],
    )
    op.create_index(
        "ix_generation_candidates_run_created",
        "chapter_generation_candidates",
        ["run_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("chapter_generation_candidates")
    op.drop_table("chapter_generation_attempts")
