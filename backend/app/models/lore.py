"""Normalized lore models introduced alongside the legacy worldview storage."""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
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
        CheckConstraint(
            "confirmation_status IN ('candidate', 'confirmed', 'rejected')",
            name="ck_setting_element_confirmation",
        ),
        CheckConstraint(
            "lifecycle_status IN ('active', 'archived', 'merged')",
            name="ck_setting_element_lifecycle",
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
    confirmation_status = Column(String(20), nullable=False, default="confirmed")
    lifecycle_status = Column(String(20), nullable=False, default="active")
    content_version = Column(Integer, nullable=False, default=1)
    lock_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class ElementSource(Base):
    __tablename__ = "element_sources"

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
    excerpt_hash = Column(String(64), nullable=True)
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
        ForeignKey("setting_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    type_schema_revision = Column(Integer, nullable=False, default=1)
    name = Column(String(200), nullable=False)
    summary = Column(Text, default="")
    payload = Column(JSON, nullable=False, default=dict)
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
