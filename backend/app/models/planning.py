"""Relational planning read model for parts, chapters, and Lore scope usage."""

from datetime import UTC, datetime
import uuid

from sqlalchemy import (
    JSON,
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
    text,
)

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _id() -> str:
    return uuid.uuid4().hex


class NovelPlan(Base):
    __tablename__ = "novel_plans"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_novel_plan_project"),
        UniqueConstraint("project_id", "id", name="uq_novel_plan_project_id_id"),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_novel_plan_status",
        ),
        CheckConstraint(
            "structure_version >= 1 AND assignment_version >= 1",
            name="ck_novel_plan_versions",
        ),
    )

    id = Column(String(32), primary_key=True, default=_id)
    project_id = Column(
        String(32),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String(20), nullable=False, default="active")
    structure_version = Column(Integer, nullable=False, default=1)
    assignment_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class PlanningPart(Base):
    __tablename__ = "planning_parts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "plan_id"],
            ["novel_plans.project_id", "novel_plans.id"],
            name="fk_planning_part_plan",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "project_id", "id", name="uq_planning_part_project_id_id"
        ),
        CheckConstraint("position >= 1", name="ck_planning_part_position"),
        CheckConstraint("lock_version >= 1", name="ck_planning_part_lock_version"),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_planning_part_status",
        ),
        Index(
            "ix_planning_parts_plan_status_position",
            "plan_id",
            "status",
            "position",
            "id",
        ),
        Index(
            "uq_planning_parts_active_position",
            "plan_id",
            "position",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id = Column(String(32), primary_key=True, default=_id)
    project_id = Column(String(32), nullable=False, index=True)
    plan_id = Column(String(32), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default="")
    position = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="active")
    lock_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class PlanningChapter(Base):
    __tablename__ = "planning_chapters"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "plan_id"],
            ["novel_plans.project_id", "novel_plans.id"],
            name="fk_planning_chapter_plan",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "part_id"],
            ["planning_parts.project_id", "planning_parts.id"],
            name="fk_planning_chapter_part",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "project_id", "id", name="uq_planning_chapter_project_id_id"
        ),
        CheckConstraint("position >= 1", name="ck_planning_chapter_position"),
        CheckConstraint(
            "target_word_count IS NULL OR "
            "(target_word_count >= 500 AND target_word_count <= 10000)",
            name="ck_planning_chapter_target_words",
        ),
        CheckConstraint(
            "lock_version >= 1", name="ck_planning_chapter_lock_version"
        ),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_planning_chapter_status",
        ),
        Index(
            "ix_planning_chapters_part_status_position",
            "part_id",
            "status",
            "position",
            "id",
        ),
        Index(
            "uq_planning_chapters_active_position",
            "part_id",
            "position",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id = Column(String(32), primary_key=True, default=_id)
    project_id = Column(String(32), nullable=False, index=True)
    plan_id = Column(String(32), nullable=False, index=True)
    part_id = Column(String(32), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    summary = Column(Text, nullable=False, default="")
    target_word_count = Column(Integer, nullable=True)
    position = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="active")
    lock_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class PlanningLoreAssignment(Base):
    __tablename__ = "planning_lore_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "plan_id"],
            ["novel_plans.project_id", "novel_plans.id"],
            name="fk_planning_lore_assignment_plan",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_planning_lore_assignment_element",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "part_id"],
            ["planning_parts.project_id", "planning_parts.id"],
            name="fk_planning_lore_assignment_part",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "chapter_id"],
            ["planning_chapters.project_id", "planning_chapters.id"],
            name="fk_planning_lore_assignment_chapter",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "project_id",
            "element_id",
            "scope_type",
            "scope_target_id",
            name="uq_planning_lore_assignment_target_element",
        ),
        UniqueConstraint(
            "project_id", "id", name="uq_planning_lore_assignment_project_id_id"
        ),
        CheckConstraint(
            "scope_type IN ('novel', 'part', 'chapter')",
            name="ck_planning_lore_assignment_scope",
        ),
        CheckConstraint(
            "(scope_type = 'novel' AND scope_target_id = project_id "
            "AND part_id IS NULL AND chapter_id IS NULL) OR "
            "(scope_type = 'part' AND scope_target_id = part_id "
            "AND part_id IS NOT NULL AND chapter_id IS NULL) OR "
            "(scope_type = 'chapter' AND scope_target_id = chapter_id "
            "AND part_id IS NULL AND chapter_id IS NOT NULL)",
            name="ck_planning_lore_assignment_target",
        ),
        CheckConstraint(
            "status IN ('active', 'removed')",
            name="ck_planning_lore_assignment_status",
        ),
        CheckConstraint(
            "lock_version >= 1", name="ck_planning_lore_assignment_lock_version"
        ),
        CheckConstraint(
            "element_content_version >= 1",
            name="ck_planning_lore_assignment_element_version",
        ),
        Index(
            "ix_planning_lore_assignments_plan_scope_status",
            "plan_id",
            "scope_type",
            "status",
            "scope_target_id",
            "id",
        ),
        Index(
            "ix_planning_lore_assignments_element_status",
            "element_id",
            "status",
            "id",
        ),
    )

    id = Column(String(32), primary_key=True, default=_id)
    project_id = Column(String(32), nullable=False, index=True)
    plan_id = Column(String(32), nullable=False, index=True)
    element_id = Column(String(32), nullable=False, index=True)
    scope_type = Column(String(20), nullable=False)
    scope_target_id = Column(String(32), nullable=False)
    part_id = Column(String(32), nullable=True)
    chapter_id = Column(String(32), nullable=True)
    element_content_version = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="active")
    lock_version = Column(Integer, nullable=False, default=1)
    created_by = Column(
        String(32), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by = Column(
        String(32), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class PlanningLoreAssignmentEvent(Base):
    __tablename__ = "planning_lore_assignment_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "assignment_id"],
            ["planning_lore_assignments.project_id", "planning_lore_assignments.id"],
            name="fk_planning_lore_assignment_event_assignment",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "action IN ('assign', 'remove', 'restore')",
            name="ck_planning_lore_assignment_event_action",
        ),
        CheckConstraint(
            "previous_lock_version >= 0 AND new_lock_version >= 1",
            name="ck_planning_lore_assignment_event_versions",
        ),
        CheckConstraint(
            "element_content_version >= 1",
            name="ck_planning_lore_assignment_event_element_version",
        ),
        Index(
            "ix_planning_lore_assignment_events_assignment_created",
            "assignment_id",
            "created_at",
            "id",
        ),
    )

    id = Column(String(32), primary_key=True, default=_id)
    project_id = Column(String(32), nullable=False, index=True)
    assignment_id = Column(String(32), nullable=False, index=True)
    performed_by = Column(
        String(32), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    action = Column(String(20), nullable=False)
    previous_status = Column(String(20), nullable=True)
    new_status = Column(String(20), nullable=False)
    previous_lock_version = Column(Integer, nullable=False)
    new_lock_version = Column(Integer, nullable=False)
    element_content_version = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class PlanningMutationOperation(Base):
    __tablename__ = "planning_mutation_operations"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_planning_mutation_operation_key",
        ),
        Index(
            "ix_planning_mutation_operations_project_created",
            "project_id",
            "created_at",
            "id",
        ),
    )

    id = Column(String(32), primary_key=True, default=_id)
    project_id = Column(
        String(32),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_by = Column(
        String(32), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    operation_key = Column(String(128), nullable=False)
    operation_type = Column(String(50), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    result_snapshot = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
