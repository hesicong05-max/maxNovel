"""Persistent, reviewable lore extraction jobs and candidates."""

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


class LoreExtractionBatch(Base):
    __tablename__ = "lore_extraction_batches"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_lore_extraction_project_idempotency",
        ),
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_lore_extraction_project_id_id",
        ),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'outcome_unknown')",
            name="ck_lore_extraction_batch_status",
        ),
        Index(
            "ix_lore_extraction_batches_project_created",
            "project_id",
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
    requested_by = Column(
        String(32),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key = Column(String(128), nullable=False)
    source_kind = Column(String(30), nullable=False, default="manual_text")
    source_ref = Column(String(200), nullable=True)
    source_text = Column(Text, nullable=False)
    source_hash = Column(String(64), nullable=False)
    extractor_version = Column(String(40), nullable=False)
    model_name = Column(String(200), nullable=True)
    status = Column(String(20), nullable=False, default="running")
    raw_response = Column(Text, nullable=True)
    error_code = Column(String(80), nullable=True)
    error_message = Column(String(500), nullable=True)
    retryable = Column(Boolean, nullable=False, default=False)
    candidate_count = Column(Integer, nullable=False, default=0)
    lock_version = Column(Integer, nullable=False, default=1)
    llm_started_at = Column(DateTime, nullable=True)
    llm_completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class LoreExtractionCandidate(Base):
    __tablename__ = "lore_extraction_candidates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "batch_id"],
            ["lore_extraction_batches.project_id", "lore_extraction_batches.id"],
            name="fk_lore_extraction_candidate_project_batch",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "accepted_element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_lore_extraction_candidate_project_element",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "batch_id",
            "deterministic_key",
            name="uq_lore_extraction_candidate_key",
        ),
        UniqueConstraint(
            "batch_id",
            "ordinal",
            name="uq_lore_extraction_candidate_ordinal",
        ),
        UniqueConstraint(
            "project_id",
            "accepted_element_id",
            name="uq_lore_extraction_candidate_accepted_element",
        ),
        CheckConstraint(
            "status IN ('pending_review', 'accepted', 'rejected', 'failed')",
            name="ck_lore_extraction_candidate_status",
        ),
        Index(
            "ix_lore_extraction_candidates_batch_ordinal",
            "batch_id",
            "ordinal",
        ),
        Index(
            "ix_lore_extraction_candidates_project_status",
            "project_id",
            "status",
            "updated_at",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(String(32), nullable=False, index=True)
    batch_id = Column(String(32), nullable=False, index=True)
    ordinal = Column(Integer, nullable=False)
    deterministic_key = Column(String(64), nullable=False)
    type_key = Column(String(50), nullable=True)
    name = Column(String(200), nullable=True)
    summary = Column(Text, nullable=False, default="")
    payload = Column(JSON, nullable=False, default=dict)
    field_states = Column(JSON, nullable=False, default=dict)
    relation_suggestions = Column(JSON, nullable=False, default=list)
    duplicate_conflict_suggestions = Column(JSON, nullable=False, default=list)
    suggestion_resolutions = Column(JSON, nullable=False, default=dict)
    user_overrides = Column(JSON, nullable=False, default=dict)
    status = Column(String(20), nullable=False, default="pending_review")
    revision = Column(Integer, nullable=False, default=1)
    accepted_element_id = Column(String(32), nullable=True)
    error_code = Column(String(80), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class LoreCandidateRevision(Base):
    __tablename__ = "lore_candidate_revisions"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "revision",
            name="uq_lore_candidate_revision",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    candidate_id = Column(
        String(32),
        ForeignKey("lore_extraction_candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision = Column(Integer, nullable=False)
    type_key = Column(String(50), nullable=True)
    name = Column(String(200), nullable=True)
    summary = Column(Text, nullable=False, default="")
    payload = Column(JSON, nullable=False, default=dict)
    field_states = Column(JSON, nullable=False, default=dict)
    suggestion_resolutions = Column(JSON, nullable=False, default=dict)
    user_overrides = Column(JSON, nullable=False, default=dict)
    change_kind = Column(String(30), nullable=False)
    created_by = Column(
        String(32),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class LoreCandidateFieldEvidence(Base):
    __tablename__ = "lore_candidate_field_evidence"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "field_key",
            name="uq_lore_candidate_field_evidence",
        ),
        CheckConstraint(
            "state IN ('provided', 'unknown', 'needs_confirmation')",
            name="ck_lore_candidate_evidence_state",
        ),
        CheckConstraint(
            "char_start IS NULL OR char_start >= 0",
            name="ck_lore_candidate_evidence_start",
        ),
        CheckConstraint(
            "char_end IS NULL OR char_end >= char_start",
            name="ck_lore_candidate_evidence_end",
        ),
        Index(
            "ix_lore_candidate_evidence_candidate",
            "candidate_id",
            "field_key",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    candidate_id = Column(
        String(32),
        ForeignKey("lore_extraction_candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field_key = Column(String(80), nullable=False)
    value = Column(Text, nullable=True)
    state = Column(String(30), nullable=False)
    excerpt = Column(Text, nullable=True)
    char_start = Column(Integer, nullable=True)
    char_end = Column(Integer, nullable=True)
    excerpt_hash = Column(String(64), nullable=True)
    source_hash = Column(String(64), nullable=False)
    is_name = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
