import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.sqlite import JSON

from app.database import Base


def gen_id() -> str:
    return uuid.uuid4().hex


class NovelGenre(str, PyEnum):
    XUANHUAN = "玄幻"
    URBAN = "都市"
    SCIFI = "科幻"
    WUXIA = "武侠"
    XIANXIA = "仙侠"
    SUSPENSE = "悬疑"
    ROMANCE = "言情"


class ProjectStatus(str, PyEnum):
    DRAFT = "draft"
    WORLDVIEW_SET = "worldview_set"
    OUTLINE_PENDING = "outline_pending"
    OUTLINE_CONFIRMED = "outline_confirmed"
    WRITING = "writing"
    COMPLETED = "completed"


class Project(Base):
    __tablename__ = "projects"

    id = Column(String(32), primary_key=True, default=gen_id)
    title = Column(String(200), nullable=False)
    genre = Column(Enum(NovelGenre), nullable=False, default=NovelGenre.XUANHUAN)
    status = Column(Enum(ProjectStatus), nullable=False, default=ProjectStatus.DRAFT)
    total_chapters = Column(Integer, default=30)
    chapter_word_count = Column(Integer, default=3000)
    style_intensity = Column(String(20), default="standard")  # mild / standard / intense
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    worldview = relationship("Worldview", back_populates="project", uselist=False, cascade="all, delete-orphan")
    outline = relationship("Outline", back_populates="project", uselist=False, cascade="all, delete-orphan")
    chapters = relationship("Chapter", back_populates="project", cascade="all, delete-orphan")
    memory = relationship("StoryMemory", back_populates="project", uselist=False, cascade="all, delete-orphan")


class Worldview(Base):
    __tablename__ = "worldviews"

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(String(32), ForeignKey("projects.id"), nullable=False)
    # Structured worldview data stored as JSON
    characters = Column(JSON, default=list)       # [{name, personality, background, motivation, ability, relations}]
    geography = Column(JSON, default=list)         # [{name, description, significance}]
    factions = Column(JSON, default=list)         # [{name, stance, power_level, relations}]
    power_system = Column(JSON, default=list)     # [{name, levels, rules, limitations}]
    history = Column(JSON, default=list)          # [{event, time, description, impact}]
    conflicts = Column(JSON, default=list)         # [{name, type, parties, stakes, resolution_hint}]
    special_settings = Column(JSON, default=list)  # [{name, description, rules}]
    raw_text = Column(Text, nullable=True)         # Original uploaded text (if any)
    source = Column(String(20), default="manual")  # manual / imported / hybrid
    # Parsed elements with priority tags
    parsed_elements = Column(JSON, default=list)    # [{id, category, name, priority, revealed, reveal_chapter}]
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="worldview")


class Outline(Base):
    __tablename__ = "outlines"

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(String(32), ForeignKey("projects.id"), nullable=False)
    # The reveal plan: which elements get revealed in which chapter
    reveal_plan = Column(JSON, default=list)  # [{chapter, phase, elements: [element_id...], summary}]
    # Chapter-level outline entries
    chapters = Column(JSON, default=list)     # [{chapter_num, title, summary, key_events, reveal_elements}]
    # Story arc description
    story_arc = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="outline")


class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(String(32), ForeignKey("projects.id"), nullable=False)
    chapter_num = Column(Integer, nullable=False)
    title = Column(String(200), default="")
    content = Column(Text, default="")
    word_count = Column(Integer, default=0)
    summary = Column(Text, default="")  # Auto-generated summary for memory
    status = Column(String(20), default="pending")  # pending / generating / generated / edited
    revealed_elements = Column(JSON, default=list)  # Element IDs revealed in this chapter
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="chapters")


class StoryMemory(Base):
    """Persistent memory store for cross-chapter consistency."""
    __tablename__ = "story_memories"

    id = Column(String(32), primary_key=True, default=gen_id)
    project_id = Column(String(32), ForeignKey("projects.id"), nullable=False)
    # Track which worldview elements have been revealed
    revealed_elements = Column(JSON, default=list)    # [element_id...]
    # Current state of each character (location, status, relationships)
    character_states = Column(JSON, default=dict)     # {char_name: {location, status, mood, ...}}
    # Foreshadowing queue: planted but not yet resolved
    foreshadows = Column(JSON, default=list)          # [{id, description, planted_chapter, status, resolve_by}]
    # Timeline of events
    timeline = Column(JSON, default=list)             # [{chapter, event, description}]
    # Running chapter summaries (for long-context management)
    chapter_summaries = Column(JSON, default=list)    # [{chapter_num, summary}]
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="memory")
