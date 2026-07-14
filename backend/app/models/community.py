"""Community module models — shared novels, tags, and co-creation settings."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.project import gen_id

# Association table for many-to-many: novels <-> tags
novel_tag_association = Table(
    "novel_tag_association",
    Base.metadata,
    Column("novel_id", String(32), ForeignKey("community_novels.id"), primary_key=True),
    Column("tag_id", String(32), ForeignKey("community_tags.id"), primary_key=True),
)


class CommunityNovel(Base):
    """A novel published to the community by a user."""

    __tablename__ = "community_novels"

    id = Column(String(32), primary_key=True, default=gen_id)
    title = Column(String(200), nullable=False)
    author_name = Column(String(100), nullable=False, default="匿名作者")
    genre = Column(String(50), nullable=False, default="玄幻")
    project_id = Column(String(32), ForeignKey("projects.id"), nullable=True)
    synopsis = Column(Text, default="")
    story_outline = Column(Text, default="")
    chapter_notes = Column(Text, default="")
    allow_cocreation = Column(Boolean, default=False)
    view_count = Column(Integer, default=0)
    like_count = Column(Integer, default=0)
    total_chapters = Column(Integer, default=0)
    total_words = Column(Integer, default=0)
    # Track which users/IPs have liked this novel for dedup
    liked_by = Column(JSON, default=list)  # ["user_id_or_ip_hash", ...]
    owner_id = Column(String(32), ForeignKey("users.id"), nullable=True)  # nullable for backward compat
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    tags = relationship("CommunityTag", secondary=novel_tag_association, back_populates="novels")


class CommunityTag(Base):
    """A user-created tag for categorising community novels."""

    __tablename__ = "community_tags"

    id = Column(String(32), primary_key=True, default=gen_id)
    name = Column(String(50), nullable=False, unique=True, index=True)
    usage_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    novels = relationship("CommunityNovel", secondary=novel_tag_association, back_populates="tags")
