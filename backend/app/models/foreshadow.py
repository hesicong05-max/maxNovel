"""Durable plans and author-confirmed facts for relational foreshadows."""

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
from app.models.project import _utcnow, gen_id


class ForeshadowLifecycle(Base):
    __tablename__ = "foreshadow_lifecycles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "plan_id"],
            ["novel_plans.project_id", "novel_plans.id"],
            name="fk_foreshadow_lifecycle_plan",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_foreshadow_lifecycle_element",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "project_id", "element_id", name="uq_foreshadow_lifecycle_element"
        ),
        UniqueConstraint(
            "project_id", "id", name="uq_foreshadow_lifecycle_project_id_id"
        ),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_foreshadow_lifecycle_status",
        ),
        CheckConstraint(
            "lock_version >= 1", name="ck_foreshadow_lifecycle_lock_version"
        ),
        Index(
            "ix_foreshadow_lifecycles_project_status_updated",
            "project_id",
            "status",
            "updated_at",
            "id",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    plan_id = Column(String(32), nullable=False)
    element_id = Column(String(32), nullable=False)
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


class ForeshadowPlanItem(Base):
    __tablename__ = "foreshadow_plan_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "plan_id"],
            ["novel_plans.project_id", "novel_plans.id"],
            name="fk_foreshadow_plan_item_plan",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "lifecycle_id"],
            ["foreshadow_lifecycles.project_id", "foreshadow_lifecycles.id"],
            name="fk_foreshadow_plan_item_lifecycle",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "part_id"],
            ["planning_parts.project_id", "planning_parts.id"],
            name="fk_foreshadow_plan_item_part",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "chapter_id"],
            ["planning_chapters.project_id", "planning_chapters.id"],
            name="fk_foreshadow_plan_item_chapter",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "project_id", "id", name="uq_foreshadow_plan_item_project_id_id"
        ),
        CheckConstraint(
            "action_kind IN ('plant', 'resolve')",
            name="ck_foreshadow_plan_item_action",
        ),
        CheckConstraint(
            "target_type IN ('part', 'chapter')",
            name="ck_foreshadow_plan_item_target_type",
        ),
        CheckConstraint(
            "(target_type = 'part' AND target_id = part_id AND part_id IS NOT NULL "
            "AND chapter_id IS NULL) OR "
            "(target_type = 'chapter' AND target_id = chapter_id AND part_id IS NULL "
            "AND chapter_id IS NOT NULL)",
            name="ck_foreshadow_plan_item_target",
        ),
        CheckConstraint(
            "status IN ('active', 'cancelled')",
            name="ck_foreshadow_plan_item_status",
        ),
        CheckConstraint(
            "lock_version >= 1", name="ck_foreshadow_plan_item_lock_version"
        ),
        CheckConstraint(
            "action_kind = 'plant' OR length(trim(condition_text)) > 0",
            name="ck_foreshadow_resolve_condition",
        ),
        Index(
            "uq_foreshadow_plan_items_active_kind",
            "lifecycle_id",
            "action_kind",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_foreshadow_plan_items_target_status",
            "target_type",
            "target_id",
            "status",
            "id",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(String(32), nullable=False)
    plan_id = Column(String(32), nullable=False)
    lifecycle_id = Column(String(32), nullable=False)
    action_kind = Column(String(20), nullable=False)
    target_type = Column(String(20), nullable=False)
    target_id = Column(String(32), nullable=False)
    part_id = Column(String(32), nullable=True)
    chapter_id = Column(String(32), nullable=True)
    condition_text = Column(Text, nullable=False, default="")
    note = Column(Text, nullable=False, default="")
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


class ForeshadowFact(Base):
    __tablename__ = "foreshadow_facts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "plan_id"],
            ["novel_plans.project_id", "novel_plans.id"],
            name="fk_foreshadow_fact_plan",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "lifecycle_id"],
            ["foreshadow_lifecycles.project_id", "foreshadow_lifecycles.id"],
            name="fk_foreshadow_fact_lifecycle",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "chapter_id"],
            ["planning_chapters.project_id", "planning_chapters.id"],
            name="fk_foreshadow_fact_chapter",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "project_id", "id", name="uq_foreshadow_fact_project_id_id"
        ),
        CheckConstraint(
            "fact_kind IN ('planted', 'resolved')",
            name="ck_foreshadow_fact_kind",
        ),
        CheckConstraint(
            "status IN ('active', 'retracted')",
            name="ck_foreshadow_fact_status",
        ),
        CheckConstraint(
            "lock_version >= 1", name="ck_foreshadow_fact_lock_version"
        ),
        CheckConstraint(
            "(status = 'active' AND retracted_by IS NULL AND retracted_at IS NULL) OR "
            "(status = 'retracted' AND retracted_by IS NOT NULL AND retracted_at IS NOT NULL)",
            name="ck_foreshadow_fact_retraction",
        ),
        Index(
            "uq_foreshadow_facts_active_kind",
            "lifecycle_id",
            "fact_kind",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_foreshadow_facts_chapter_status",
            "chapter_id",
            "status",
            "id",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(String(32), nullable=False)
    plan_id = Column(String(32), nullable=False)
    lifecycle_id = Column(String(32), nullable=False)
    chapter_id = Column(String(32), nullable=False)
    fact_kind = Column(String(20), nullable=False)
    note = Column(Text, nullable=False, default="")
    status = Column(String(20), nullable=False, default="active")
    lock_version = Column(Integer, nullable=False, default=1)
    recorded_by = Column(
        String(32), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    retracted_by = Column(
        String(32), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    retracted_at = Column(DateTime, nullable=True)


class ForeshadowLifecycleEvent(Base):
    __tablename__ = "foreshadow_lifecycle_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "lifecycle_id"],
            ["foreshadow_lifecycles.project_id", "foreshadow_lifecycles.id"],
            name="fk_foreshadow_event_lifecycle",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "plan_item_id"],
            ["foreshadow_plan_items.project_id", "foreshadow_plan_items.id"],
            name="fk_foreshadow_event_plan_item",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "project_id", "id", name="uq_foreshadow_event_project_id_id"
        ),
        UniqueConstraint(
            "project_id",
            "lifecycle_id",
            "id",
            name="uq_foreshadow_event_project_lifecycle_id",
        ),
        ForeignKeyConstraint(
            ["project_id", "fact_id"],
            ["foreshadow_facts.project_id", "foreshadow_facts.id"],
            name="fk_foreshadow_event_fact",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "event_kind IN ('create', 'archive', 'restore', 'plan_create', "
            "'plan_cancel', 'plan_restore', 'fact_record', 'fact_retract')",
            name="ck_foreshadow_event_kind",
        ),
        CheckConstraint(
            "previous_lifecycle_version >= 0 AND new_lifecycle_version >= 1",
            name="ck_foreshadow_event_versions",
        ),
        CheckConstraint(
            "(event_kind IN ('create', 'archive', 'restore') AND plan_item_id IS NULL "
            "AND fact_id IS NULL) OR "
            "(event_kind IN ('plan_create', 'plan_cancel', 'plan_restore') "
            "AND plan_item_id IS NOT NULL AND fact_id IS NULL) OR "
            "(event_kind IN ('fact_record', 'fact_retract') AND fact_id IS NOT NULL "
            "AND plan_item_id IS NULL)",
            name="ck_foreshadow_event_target",
        ),
        Index(
            "ix_foreshadow_events_lifecycle_created",
            "lifecycle_id",
            "created_at",
            "id",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(String(32), nullable=False)
    lifecycle_id = Column(String(32), nullable=False)
    performed_by = Column(
        String(32), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    event_kind = Column(String(30), nullable=False)
    plan_item_id = Column(String(32), nullable=True)
    fact_id = Column(String(32), nullable=True)
    previous_lifecycle_version = Column(Integer, nullable=False)
    new_lifecycle_version = Column(Integer, nullable=False)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class ForeshadowOperation(Base):
    __tablename__ = "foreshadow_operations"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_foreshadow_operation_key",
        ),
        ForeignKeyConstraint(
            ["project_id", "lifecycle_id"],
            ["foreshadow_lifecycles.project_id", "foreshadow_lifecycles.id"],
            name="fk_foreshadow_operation_lifecycle",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "lifecycle_id", "event_id"],
            [
                "foreshadow_lifecycle_events.project_id",
                "foreshadow_lifecycle_events.lifecycle_id",
                "foreshadow_lifecycle_events.id",
            ],
            name="fk_foreshadow_operation_event",
            ondelete="CASCADE",
        ),
        Index(
            "ix_foreshadow_operations_project_created",
            "project_id",
            "created_at",
            "id",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    requested_by = Column(
        String(32), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    operation_key = Column(String(128), nullable=False)
    operation_type = Column(String(50), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    lifecycle_id = Column(String(32), nullable=False)
    event_id = Column(String(32), nullable=False)
    result_snapshot = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
