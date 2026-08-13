"""Durable preparation records for the relational chapter generation flow."""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.database import Base
from app.models.project import _utcnow, gen_id


class ChapterGenerationRun(Base):
    """Immutable context receipt created before any paid model call.

    DEV-017B1a only permits ``prepared``. Later slices may add state transitions,
    attempts, and versioned chapter drafts without changing this receipt's
    confirmed context snapshot.
    """

    __tablename__ = "chapter_generation_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "plan_id"],
            ["novel_plans.project_id", "novel_plans.id"],
            name="fk_generation_run_plan",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "planning_chapter_id"],
            ["planning_chapters.project_id", "planning_chapters.id"],
            name="fk_generation_run_chapter",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_generation_run_operation_key",
        ),
        UniqueConstraint("project_id", "id", name="uq_generation_run_project_id_id"),
        CheckConstraint("status IN ('prepared')", name="ck_generation_run_status"),
        CheckConstraint(
            "execution_mode = 'preflight_only' AND ai_invoked IS FALSE "
            "AND billing_effect = 'none'",
            name="ck_generation_run_preflight_only",
        ),
        CheckConstraint(
            "structure_version >= 1 AND assignment_version >= 1 "
            "AND chapter_lock_version >= 1",
            name="ck_generation_run_versions",
        ),
        CheckConstraint(
            "context_schema_version >= 1",
            name="ck_generation_run_context_schema_version",
        ),
        CheckConstraint(
            "context_size_bytes >= 0 AND context_size_bytes <= 65536",
            name="ck_generation_run_context_size",
        ),
        Index(
            "ix_generation_runs_chapter_created",
            "planning_chapter_id",
            "created_at",
            "id",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(
        String(32),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_id = Column(String(32), nullable=False, index=True)
    planning_chapter_id = Column(String(32), nullable=False, index=True)
    requested_by = Column(
        String(32),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    operation_key = Column(String(128), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="prepared")
    execution_mode = Column(String(30), nullable=False, default="preflight_only")
    ai_invoked = Column(Boolean, nullable=False, default=False)
    billing_effect = Column(String(20), nullable=False, default="none")
    structure_version = Column(Integer, nullable=False)
    assignment_version = Column(Integer, nullable=False)
    chapter_lock_version = Column(Integer, nullable=False)
    context_schema_version = Column(Integer, nullable=False, default=1)
    context_manifest = Column(JSON, nullable=False)
    context_checksum = Column(String(64), nullable=False)
    context_size_bytes = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class ChapterGenerationAttempt(Base):
    """Durable reservation for one application-side model invocation.

    The operation key is the paid-call idempotency boundary. A claimed attempt is
    never reset to ``reserved``: an ambiguous provider outcome is terminal and
    must be exposed as ``outcome_unknown`` instead of triggering another call.
    """

    __tablename__ = "chapter_generation_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "run_id"],
            ["chapter_generation_runs.project_id", "chapter_generation_runs.id"],
            name="fk_generation_attempt_run",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_generation_attempt_operation_key",
        ),
        UniqueConstraint(
            "project_id", "id", name="uq_generation_attempt_project_id_id"
        ),
        UniqueConstraint(
            "project_id",
            "id",
            "run_id",
            name="uq_generation_attempt_identity",
        ),
        CheckConstraint(
            "status IN ('reserved', 'calling', 'succeeded', 'failed', "
            "'outcome_unknown')",
            name="ck_generation_attempt_status",
        ),
        CheckConstraint(
            "execution_mode = 'single_call' AND billing_confirmed IS TRUE",
            name="ck_generation_attempt_execution_mode",
        ),
        CheckConstraint(
            "billing_effect IN ('none', 'possible')",
            name="ck_generation_attempt_billing_effect",
        ),
        CheckConstraint(
            "prompt_schema_version >= 1 AND capability_schema_version >= 1 "
            "AND lock_version >= 1",
            name="ck_generation_attempt_versions",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64 AND "
            "length(prompt_checksum) = 64 AND length(context_checksum) = 64 "
            "AND length(capability_checksum) = 64 "
            "AND length(execution_config_digest) = 64",
            name="ck_generation_attempt_checksums",
        ),
        CheckConstraint(
            "length(provider_name) BETWEEN 1 AND 80 "
            "AND length(model_name) BETWEEN 1 AND 200 "
            "AND max_output_tokens BETWEEN 1 AND 1000000 "
            "AND input_limit_availability = 'unavailable' "
            "AND max_input_tokens IS NULL "
            "AND price_availability = 'unavailable'",
            name="ck_generation_attempt_capability",
        ),
        CheckConstraint(
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
        CheckConstraint(
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
        CheckConstraint(
            "error_code IS NULL OR length(error_code) BETWEEN 1 AND 80",
            name="ck_generation_attempt_error_code",
        ),
        Index(
            "ix_generation_attempts_run_created",
            "run_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_generation_attempts_user_created",
            "project_id",
            "requested_by",
            "created_at",
            "id",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(
        String(32),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id = Column(String(32), nullable=False, index=True)
    requested_by = Column(
        String(32),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    operation_key = Column(String(128), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="reserved")
    execution_mode = Column(String(30), nullable=False, default="single_call")
    billing_confirmed = Column(Boolean, nullable=False)
    ai_invoked = Column(Boolean, nullable=False, default=False)
    billing_effect = Column(String(20), nullable=False, default="none")
    capability_schema_version = Column(Integer, nullable=False, default=1)
    capability_snapshot = Column(JSON, nullable=False)
    capability_checksum = Column(String(64), nullable=False)
    execution_config_digest = Column(String(64), nullable=False)
    provider_name = Column(String(80), nullable=False)
    model_name = Column(String(200), nullable=False)
    max_output_tokens = Column(Integer, nullable=False)
    input_limit_availability = Column(String(20), nullable=False)
    max_input_tokens = Column(Integer, nullable=True)
    price_availability = Column(String(20), nullable=False)
    prompt_schema_version = Column(Integer, nullable=False, default=1)
    prompt_checksum = Column(String(64), nullable=False)
    context_checksum = Column(String(64), nullable=False)
    usage_status = Column(String(20), nullable=False, default="unavailable")
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    lock_version = Column(Integer, nullable=False, default=1)
    claimed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_code = Column(String(80), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class ChapterTechnicalDemoExecution(Base):
    """Terminal, zero-LLM receipt for one fixed technical-demo execution."""

    __tablename__ = "chapter_technical_demo_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "run_id"],
            ["chapter_generation_runs.project_id", "chapter_generation_runs.id"],
            name="fk_technical_demo_execution_run",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_technical_demo_execution_operation_key",
        ),
        UniqueConstraint(
            "project_id", "id", name="uq_technical_demo_execution_project_id_id"
        ),
        UniqueConstraint(
            "project_id",
            "id",
            "run_id",
            name="uq_technical_demo_execution_identity",
        ),
        CheckConstraint(
            "status = 'succeeded' AND execution_mode = 'technical_demo' "
            "AND ai_invoked IS FALSE AND billing_effect = 'none' "
            "AND usage_status = 'not_applicable'",
            name="ck_technical_demo_execution_shape",
        ),
        CheckConstraint(
            "fixture_version = 1 AND adapter_schema_version = 1 "
            "AND content_spec_version = 1",
            name="ck_technical_demo_execution_versions",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64 "
            "AND length(context_checksum) = 64 "
            "AND length(capability_checksum) = 64",
            name="ck_technical_demo_execution_checksums",
        ),
        Index(
            "ix_technical_demo_executions_run_created",
            "run_id",
            "created_at",
            "id",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(
        String(32),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id = Column(String(32), nullable=False, index=True)
    requested_by = Column(
        String(32),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    operation_key = Column(String(128), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="succeeded")
    execution_mode = Column(String(30), nullable=False, default="technical_demo")
    ai_invoked = Column(Boolean, nullable=False, default=False)
    billing_effect = Column(String(20), nullable=False, default="none")
    usage_status = Column(String(20), nullable=False, default="not_applicable")
    fixture_version = Column(Integer, nullable=False, default=1)
    adapter_schema_version = Column(Integer, nullable=False, default=1)
    content_spec_version = Column(Integer, nullable=False, default=1)
    context_checksum = Column(String(64), nullable=False)
    capability_checksum = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    completed_at = Column(DateTime, nullable=False, default=_utcnow)


class ChapterGenerationCandidate(Base):
    """Immutable generated or manually-derived planning chapter draft."""

    __tablename__ = "chapter_generation_candidates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "run_id"],
            ["chapter_generation_runs.project_id", "chapter_generation_runs.id"],
            name="fk_generation_candidate_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "project_id",
                "source_technical_demo_execution_id",
                "run_id",
            ],
            [
                "chapter_technical_demo_executions.project_id",
                "chapter_technical_demo_executions.id",
                "chapter_technical_demo_executions.run_id",
            ],
            name="fk_generation_candidate_technical_demo_execution",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
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
        ForeignKeyConstraint(
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
        UniqueConstraint(
            "project_id", "id", name="uq_generation_candidate_project_id_id"
        ),
        UniqueConstraint(
            "project_id",
            "id",
            "run_id",
            name="uq_generation_candidate_identity",
        ),
        UniqueConstraint(
            "project_id",
            "source_attempt_id",
            name="uq_generation_candidate_attempt",
        ),
        UniqueConstraint(
            "project_id",
            "source_technical_demo_execution_id",
            name="uq_generation_candidate_technical_demo_execution",
        ),
        UniqueConstraint(
            "project_id",
            "run_id",
            "version_no",
            name="uq_generation_candidate_run_version",
        ),
        CheckConstraint(
            "origin_kind IN ('generated', 'manual_edit', 'technical_demo')",
            name="ck_generation_candidate_origin",
        ),
        CheckConstraint(
            "(origin_kind = 'generated' AND source_attempt_id IS NOT NULL "
            "AND source_technical_demo_execution_id IS NULL "
            "AND parent_candidate_id IS NULL) OR "
            "(origin_kind = 'technical_demo' AND source_attempt_id IS NULL "
            "AND source_technical_demo_execution_id IS NOT NULL "
            "AND parent_candidate_id IS NULL) OR "
            "(origin_kind = 'manual_edit' AND source_attempt_id IS NULL "
            "AND source_technical_demo_execution_id IS NULL "
            "AND parent_candidate_id IS NOT NULL)",
            name="ck_generation_candidate_origin_shape",
        ),
        CheckConstraint(
            "content_format = 'plain_text'",
            name="ck_generation_candidate_content_format",
        ),
        CheckConstraint(
            "version_no >= 1 AND word_count >= 1",
            name="ck_generation_candidate_versions",
        ),
        CheckConstraint(
            "content_size_bytes >= 1 AND content_size_bytes <= 262144",
            name="ck_generation_candidate_content_size",
        ),
        CheckConstraint(
            "length(content_checksum) = 64",
            name="ck_generation_candidate_checksum",
        ),
        Index(
            "ix_generation_candidates_run_created",
            "run_id",
            "created_at",
            "id",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(
        String(32),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id = Column(String(32), nullable=False, index=True)
    source_attempt_id = Column(String(32), nullable=True, index=True)
    source_technical_demo_execution_id = Column(String(32), nullable=True, index=True)
    parent_candidate_id = Column(String(32), nullable=True, index=True)
    version_no = Column(Integer, nullable=False)
    origin_kind = Column(String(20), nullable=False)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    content_format = Column(String(20), nullable=False, default="plain_text")
    content_checksum = Column(String(64), nullable=False)
    content_size_bytes = Column(Integer, nullable=False)
    word_count = Column(Integer, nullable=False)
    created_by = Column(
        String(32),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at = Column(DateTime, nullable=False, default=_utcnow)
