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
