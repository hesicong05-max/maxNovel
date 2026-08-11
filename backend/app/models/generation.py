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
        UniqueConstraint(
            "project_id", "id", name="uq_generation_run_project_id_id"
        ),
        CheckConstraint(
            "status IN ('prepared')", name="ck_generation_run_status"
        ),
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
