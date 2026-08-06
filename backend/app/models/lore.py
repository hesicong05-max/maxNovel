"""Normalized lore models introduced alongside the legacy worldview storage."""

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


class SettingType(Base):
    __tablename__ = "setting_types"
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_setting_type_project_key"),
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_setting_type_project_id_id",
        ),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_setting_type_status",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(
        String(32),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key = Column(String(50), nullable=False)
    display_name = Column(String(100), nullable=False)
    description = Column(Text, default="")
    is_builtin = Column(Boolean, nullable=False, default=False)
    schema_revision = Column(Integer, nullable=False, default=1)
    field_schema = Column(JSON, nullable=False, default=dict)
    status = Column(String(20), nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class SettingTypeRevision(Base):
    __tablename__ = "setting_type_revisions"
    __table_args__ = (
        UniqueConstraint("type_id", "revision", name="uq_setting_type_revision"),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    type_id = Column(
        String(32),
        ForeignKey("setting_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision = Column(Integer, nullable=False)
    display_name = Column(String(100), nullable=False)
    field_schema = Column(JSON, nullable=False, default=dict)
    change_summary = Column(Text, default="")
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class SettingElement(Base):
    __tablename__ = "setting_elements"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_setting_element_project_id_id",
        ),
        ForeignKeyConstraint(
            ["project_id", "type_id"],
            ["setting_types.project_id", "setting_types.id"],
            name="fk_setting_element_project_type",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "merged_into_element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_setting_element_merged_into",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "confirmation_status IN ('candidate', 'confirmed', 'rejected')",
            name="ck_setting_element_confirmation",
        ),
        CheckConstraint(
            "lifecycle_status IN ('active', 'archived', 'merged')",
            name="ck_setting_element_lifecycle",
        ),
        Index(
            "ix_setting_elements_project_status_updated",
            "project_id",
            "lifecycle_status",
            "updated_at",
            "id",
        ),
        Index(
            "ix_setting_elements_project_type_updated",
            "project_id",
            "type_id",
            "updated_at",
            "id",
        ),
        Index(
            "ix_setting_elements_project_confirmation_updated",
            "project_id",
            "confirmation_status",
            "updated_at",
            "id",
        ),
        Index(
            "ix_setting_elements_project_name",
            "project_id",
            "normalized_name",
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
    type_id = Column(
        String(32),
        ForeignKey("setting_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(200), nullable=False)
    normalized_name = Column(String(200), nullable=False, default="")
    summary = Column(Text, default="")
    payload = Column(JSON, nullable=False, default=dict)
    payload_schema_revision = Column(Integer, nullable=False, default=1)
    field_states = Column(JSON, nullable=False, default=dict)
    confirmation_status = Column(String(20), nullable=False, default="confirmed")
    lifecycle_status = Column(String(20), nullable=False, default="active")
    merged_into_element_id = Column(String(32), nullable=True, index=True)
    enabled = Column(Boolean, nullable=False, default=True)
    content_version = Column(Integer, nullable=False, default=1)
    lock_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class LoreElementCreateOperation(Base):
    """Durable exactly-once receipt for a manual lore element creation."""

    __tablename__ = "lore_element_create_operations"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_lore_element_create_operation_key",
        ),
        UniqueConstraint(
            "project_id",
            "element_id",
            name="uq_lore_element_create_operation_element",
        ),
        ForeignKeyConstraint(
            ["project_id", "element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_lore_element_create_operation_element",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_lore_element_create_operations_project_created",
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
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    operation_key = Column(String(128), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    element_id = Column(
        String(32),
        ForeignKey("setting_elements.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class ElementSource(Base):
    __tablename__ = "element_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_element_source_project_element",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "confirmation_status IN ('provided', 'needs_confirmation')",
            name="ck_element_source_confirmation",
        ),
        Index(
            "ix_element_sources_project_kind_ref",
            "project_id",
            "source_kind",
            "source_ref",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(
        String(32),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    element_id = Column(
        String(32),
        ForeignKey("setting_elements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_kind = Column(String(30), nullable=False)
    source_ref = Column(String(200), nullable=True)
    locator = Column(JSON, nullable=False, default=dict)
    excerpt = Column(Text, nullable=True)
    excerpt_hash = Column(String(64), nullable=True)
    confirmation_status = Column(String(20), nullable=False, default="provided")
    is_primary = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class ElementVersion(Base):
    __tablename__ = "element_versions"
    __table_args__ = (
        UniqueConstraint("element_id", "version_no", name="uq_element_version"),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    element_id = Column(
        String(32),
        ForeignKey("setting_elements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_no = Column(Integer, nullable=False)
    type_id = Column(
        String(32),
        ForeignKey(
            "setting_types.id",
            name="fk_element_version_type",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    type_schema_revision = Column(Integer, nullable=False, default=1)
    name = Column(String(200), nullable=False)
    summary = Column(Text, default="")
    payload = Column(JSON, nullable=False, default=dict)
    field_states = Column(JSON, nullable=False, default=dict)
    change_reason = Column(String(100), default="")
    source_id = Column(
        String(32),
        ForeignKey("element_sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by = Column(
        String(32),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class ProjectLoreMigration(Base):
    __tablename__ = "project_lore_migrations"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "migration_version",
            "source_checksum",
            name="uq_project_lore_migration_source",
        ),
        CheckConstraint(
            "status IN ('preparing', 'validating', 'ready', 'failed', 'stale')",
            name="ck_project_lore_migration_status",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(
        String(32),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    migration_version = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default="preparing")
    source_checksum = Column(String(64), nullable=False)
    result_checksum = Column(String(64), nullable=True)
    counts = Column(JSON, nullable=False, default=dict)
    validation_errors = Column(JSON, nullable=False, default=list)
    started_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    completed_at = Column(DateTime, nullable=True)


class LegacyElementMap(Base):
    __tablename__ = "legacy_element_maps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_legacy_element_map_project_element",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "project_id",
            "legacy_category",
            "legacy_index",
            name="uq_legacy_element_position",
        ),
        UniqueConstraint(
            "project_id",
            "element_id",
            name="uq_legacy_element_target",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(
        String(32),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    legacy_category = Column(String(50), nullable=False)
    legacy_index = Column(Integer, nullable=False)
    legacy_id = Column(String(100), nullable=True)
    element_id = Column(
        String(32),
        ForeignKey("setting_elements.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_checksum = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class ElementStateEvent(Base):
    __tablename__ = "element_state_events"
    __table_args__ = (
        CheckConstraint(
            "event_kind IN ('create', 'confirm', 'reject', 'enable', "
            "'disable', 'archive', 'restore_archive', 'merge')",
            name="ck_element_state_event_kind",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    element_id = Column(
        String(32),
        ForeignKey("setting_elements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_kind = Column(String(20), nullable=False)
    previous_lock_version = Column(Integer, nullable=False)
    new_lock_version = Column(Integer, nullable=False)
    performed_by = Column(
        String(32),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    metadata_ = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class ElementRelation(Base):
    __tablename__ = "element_relations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "source_element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_element_relation_project_source",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "target_element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_element_relation_project_target",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_element_relation_status",
        ),
        UniqueConstraint(
            "project_id",
            "source_element_id",
            "target_element_id",
            "relation_key",
            name="uq_element_relation_key",
        ),
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_element_relation_project_id_id",
        ),
        Index(
            "ix_element_relations_project_source_status_key",
            "project_id",
            "source_element_id",
            "status",
            "relation_key",
        ),
        Index(
            "ix_element_relations_project_target_status_key",
            "project_id",
            "target_element_id",
            "status",
            "relation_key",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(
        String(32),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_element_id = Column(
        String(32),
        nullable=False,
        index=True,
    )
    target_element_id = Column(
        String(32),
        nullable=False,
    )
    relation_key = Column(String(50), nullable=False)
    forward_label = Column(String(100), nullable=False, default="")
    reverse_label = Column(String(100), nullable=False, default="")
    description = Column(Text, default="")
    metadata_ = Column("metadata", JSON, nullable=False, default=dict)
    status = Column(
        String(20), nullable=False, default="active",
    )
    version_no = Column(Integer, nullable=False, default=1)
    lock_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class LoreRelationCreateOperation(Base):
    """Durable exactly-once receipt for relation creation."""

    __tablename__ = "lore_relation_create_operations"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "requested_by",
            "operation_key",
            name="uq_lore_relation_create_operation_key",
        ),
        UniqueConstraint(
            "project_id",
            "relation_id",
            name="uq_lore_relation_create_operation_relation",
        ),
        ForeignKeyConstraint(
            ["project_id", "relation_id"],
            ["element_relations.project_id", "element_relations.id"],
            name="fk_lore_relation_create_operation_relation",
            ondelete="CASCADE",
        ),
        Index(
            "ix_lore_relation_create_operations_project_created",
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
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    operation_key = Column(String(128), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    relation_id = Column(String(32), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class ElementRelationVersion(Base):
    __tablename__ = "element_relation_versions"
    __table_args__ = (
        UniqueConstraint(
            "relation_id",
            "version_no",
            name="uq_element_relation_version",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    relation_id = Column(
        String(32),
        ForeignKey("element_relations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_no = Column(Integer, nullable=False)
    source_element_id = Column(String(32), nullable=False)
    target_element_id = Column(String(32), nullable=False)
    relation_key = Column(String(50), nullable=False)
    forward_label = Column(String(100), nullable=False)
    reverse_label = Column(String(100), nullable=False)
    description = Column(Text, default="")
    metadata_ = Column("metadata", JSON, nullable=False, default=dict)
    status = Column(String(20), nullable=False)
    change_reason = Column(String(100), default="")
    created_by = Column(
        String(32),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class LoreReviewSuggestion(Base):
    """Reviewable, non-destructive duplicate/conflict clue for formal lore."""

    __tablename__ = "lore_review_suggestions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "left_element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_lore_review_suggestion_left",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "right_element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_lore_review_suggestion_right",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "project_id", "left_element_id", "right_element_id", "rule_key",
            name="uq_lore_review_suggestion_pair_rule",
        ),
        UniqueConstraint(
            "project_id", "id", name="uq_lore_review_suggestion_project_id_id",
        ),
        CheckConstraint(
            "left_element_id <> right_element_id",
            name="ck_lore_review_suggestion_distinct_elements",
        ),
        CheckConstraint(
            "kind IN ('possible_duplicate', 'possible_conflict')",
            name="ck_lore_review_suggestion_kind",
        ),
        CheckConstraint(
            "detection_state IN ('active', 'stale')",
            name="ck_lore_review_suggestion_detection_state",
        ),
        CheckConstraint(
            "review_status IN ('pending', 'deferred', 'confirmed_duplicate', "
            "'confirmed_conflict', 'not_an_issue')",
            name="ck_lore_review_suggestion_review_status",
        ),
        Index(
            "ix_lore_review_suggestions_project_status_updated",
            "project_id", "detection_state", "review_status", "updated_at", "id",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    left_element_id = Column(String(32), nullable=False, index=True)
    right_element_id = Column(String(32), nullable=False, index=True)
    rule_key = Column(String(80), nullable=False)
    rule_version = Column(Integer, nullable=False, default=1)
    kind = Column(String(30), nullable=False)
    detection_state = Column(String(20), nullable=False, default="active")
    review_status = Column(String(30), nullable=False, default="pending")
    left_content_version = Column(Integer, nullable=False)
    right_content_version = Column(Integer, nullable=False)
    evidence = Column(JSON, nullable=False, default=list)
    evidence_fingerprint = Column(String(64), nullable=False)
    evidence_revision = Column(Integer, nullable=False, default=1)
    decided_evidence_revision = Column(Integer, nullable=True)
    lock_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class LoreReviewSuggestionEvent(Base):
    """Decision audit record and durable idempotency receipt."""

    __tablename__ = "lore_review_suggestion_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "suggestion_id"],
            ["lore_review_suggestions.project_id", "lore_review_suggestions.id"],
            name="fk_lore_review_event_suggestion", ondelete="CASCADE",
        ),
        UniqueConstraint(
            "project_id", "performed_by", "operation_key",
            name="uq_lore_review_event_operation",
        ),
        Index(
            "ix_lore_review_events_suggestion_created",
            "suggestion_id", "created_at", "id",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    suggestion_id = Column(String(32), nullable=False, index=True)
    performed_by = Column(
        String(32), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    operation_key = Column(String(128), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    previous_status = Column(String(30), nullable=False)
    new_status = Column(String(30), nullable=False)
    evidence_revision = Column(Integer, nullable=False)
    previous_lock_version = Column(Integer, nullable=False)
    new_lock_version = Column(Integer, nullable=False)
    note = Column(Text, nullable=False, default="")
    applied = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class LoreMergeOperation(Base):
    """Completed non-destructive merge audit and durable idempotency receipt."""

    __tablename__ = "lore_merge_operations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["suggestion_project_id", "suggestion_id"],
            ["lore_review_suggestions.project_id", "lore_review_suggestions.id"],
            name="fk_lore_merge_operation_suggestion",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["project_id", "survivor_element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_lore_merge_operation_survivor",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "merged_element_id"],
            ["setting_elements.project_id", "setting_elements.id"],
            name="fk_lore_merge_operation_merged",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "project_id", "performed_by", "operation_key",
            name="uq_lore_merge_operation_key",
        ),
        UniqueConstraint(
            "project_id", "merged_element_id",
            name="uq_lore_merge_operation_merged_element",
        ),
        UniqueConstraint(
            "project_id", "id", name="uq_lore_merge_operation_project_id_id",
        ),
        CheckConstraint(
            "survivor_element_id <> merged_element_id",
            name="ck_lore_merge_operation_distinct_elements",
        ),
        CheckConstraint(
            "(suggestion_project_id IS NULL AND suggestion_id IS NULL) OR "
            "(suggestion_project_id IS NOT NULL AND suggestion_id IS NOT NULL "
            "AND suggestion_project_id = project_id)",
            name="ck_lore_merge_operation_suggestion_scope",
        ),
        Index(
            "ix_lore_merge_operations_project_created",
            "project_id", "created_at", "id",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    performed_by = Column(
        String(32), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    operation_key = Column(String(128), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    suggestion_project_id = Column(String(32), nullable=True)
    suggestion_id = Column(String(32), nullable=True, index=True)
    evidence_revision = Column(Integer, nullable=False)
    survivor_element_id = Column(String(32), nullable=False, index=True)
    merged_element_id = Column(String(32), nullable=False, index=True)
    survivor_before_content_version = Column(Integer, nullable=False)
    survivor_before_lock_version = Column(Integer, nullable=False)
    merged_before_content_version = Column(Integer, nullable=False)
    merged_before_lock_version = Column(Integer, nullable=False)
    source_fingerprint = Column(String(64), nullable=False)
    relation_fingerprint = Column(String(64), nullable=False)
    selection_snapshot = Column(JSON, nullable=False, default=dict)
    plan_fingerprint = Column(String(64), nullable=False)
    impact_summary = Column(JSON, nullable=False, default=dict)
    survivor_after_content_version = Column(Integer, nullable=False)
    survivor_after_lock_version = Column(Integer, nullable=False)
    merged_after_lock_version = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class LoreMergeRelationAction(Base):
    """Per-relation before/after audit for a completed merge operation."""

    __tablename__ = "lore_merge_relation_actions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "merge_operation_id"],
            ["lore_merge_operations.project_id", "lore_merge_operations.id"],
            name="fk_lore_merge_relation_action_operation",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["relation_project_id", "relation_id"],
            ["element_relations.project_id", "element_relations.id"],
            name="fk_lore_merge_relation_action_relation",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["retained_relation_project_id", "retained_relation_id"],
            ["element_relations.project_id", "element_relations.id"],
            name="fk_lore_merge_relation_action_retained_relation",
            ondelete="SET NULL",
        ),
        CheckConstraint(
            "action IN ('rewired', 'exact_duplicate_archived', "
            "'self_loop_archived')",
            name="ck_lore_merge_relation_action",
        ),
        CheckConstraint(
            "(relation_project_id IS NULL AND relation_id IS NULL) OR "
            "(relation_project_id IS NOT NULL AND relation_id IS NOT NULL "
            "AND relation_project_id = project_id)",
            name="ck_lore_merge_relation_action_relation_scope",
        ),
        CheckConstraint(
            "(retained_relation_project_id IS NULL AND "
            "retained_relation_id IS NULL) OR "
            "(retained_relation_project_id IS NOT NULL AND "
            "retained_relation_id IS NOT NULL AND "
            "retained_relation_project_id = project_id)",
            name="ck_lore_merge_relation_action_retained_scope",
        ),
        UniqueConstraint(
            "merge_operation_id", "relation_id",
            name="uq_lore_merge_relation_action_relation",
        ),
        Index(
            "ix_lore_merge_relation_actions_operation",
            "merge_operation_id", "id",
        ),
    )

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    merge_operation_id = Column(String(32), nullable=False, index=True)
    relation_project_id = Column(String(32), nullable=True)
    relation_id = Column(String(32), nullable=True, index=True)
    retained_relation_project_id = Column(String(32), nullable=True)
    retained_relation_id = Column(String(32), nullable=True)
    action = Column(String(40), nullable=False)
    before_snapshot = Column(JSON, nullable=False, default=dict)
    after_snapshot = Column(JSON, nullable=False, default=dict)
    previous_lock_version = Column(Integer, nullable=False)
    new_lock_version = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
