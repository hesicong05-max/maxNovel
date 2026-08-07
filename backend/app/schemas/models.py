from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.project import NovelGenre, ProjectStatus


class WorldviewElementBase(BaseModel):
    name: str = ""
    description: str = ""


class CharacterSchema(BaseModel):
    name: str
    personality: str = ""
    background: str = ""
    motivation: str = ""
    ability: str = ""
    relations: list[dict[str, Any]] = Field(default_factory=list)


class GeographySchema(BaseModel):
    name: str
    description: str = ""
    significance: str = ""


class FactionSchema(BaseModel):
    name: str
    stance: str = ""
    power_level: str = ""
    relations: list[dict[str, Any]] = Field(default_factory=list)


class PowerSystemSchema(BaseModel):
    name: str
    levels: str = ""
    rules: str = ""
    limitations: str = ""


class HistorySchema(BaseModel):
    event: str
    time: str = ""
    description: str = ""
    impact: str = ""


class ConflictSchema(BaseModel):
    name: str
    type: str = ""
    parties: str = ""
    stakes: str = ""
    resolution_hint: str = ""


class SpecialSettingSchema(BaseModel):
    name: str
    description: str = ""
    rules: str = ""


class WorldviewImportRequest(BaseModel):
    """Request body for importing worldview from a document."""

    document_text: str = Field(min_length=10, max_length=200_000)
    genre: str = "玄幻"


class WorldviewImportResponse(BaseModel):
    """Response after LLM extracts worldview from a document."""

    characters: list[CharacterSchema] = Field(default_factory=list)
    geography: list[GeographySchema] = Field(default_factory=list)
    factions: list[FactionSchema] = Field(default_factory=list)
    power_system: list[PowerSystemSchema] = Field(default_factory=list)
    history: list[HistorySchema] = Field(default_factory=list)
    conflicts: list[ConflictSchema] = Field(default_factory=list)
    special_settings: list[SpecialSettingSchema] = Field(default_factory=list)
    raw_text: str | None = None
    source: str = "imported"
    element_count: int = 0


class WorldviewCreate(BaseModel):
    characters: list[CharacterSchema] = Field(default_factory=list)
    geography: list[GeographySchema] = Field(default_factory=list)
    factions: list[FactionSchema] = Field(default_factory=list)
    power_system: list[PowerSystemSchema] = Field(default_factory=list)
    history: list[HistorySchema] = Field(default_factory=list)
    conflicts: list[ConflictSchema] = Field(default_factory=list)
    special_settings: list[SpecialSettingSchema] = Field(default_factory=list)
    raw_text: str | None = Field(default=None, max_length=200_000)
    source: str = "manual"  # manual / imported / hybrid


class WorldviewResponse(WorldviewCreate):
    id: str
    project_id: str
    parsed_elements: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    genre: NovelGenre = NovelGenre.XUANHUAN
    total_chapters: int = Field(default=30, ge=1, le=50)
    chapter_word_count: int = Field(default=3000, ge=500, le=10000)
    style_intensity: Literal["mild", "standard", "intense"] = "standard"


class ProjectResponse(BaseModel):
    id: str
    title: str
    genre: NovelGenre
    status: ProjectStatus
    total_chapters: int
    chapter_word_count: int
    style_intensity: str
    created_at: datetime
    updated_at: datetime
    has_worldview: bool = False
    has_outline: bool = False
    chapter_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class ChapterResponse(BaseModel):
    id: str
    project_id: str
    chapter_num: int
    title: str
    content: str
    word_count: int
    summary: str
    status: str
    revealed_elements: list[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChapterUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=200_000)


class ChapterWordCountEntry(BaseModel):
    """Per-chapter word count override."""

    chapter_num: int
    target_word_count: int | None = None  # None = use auto-distributed value


class WordCountConfigRequest(BaseModel):
    """Request body for saving word count configuration."""

    total_word_count: int | None = None  # If set, auto-distribute across chapters
    chapters: list[ChapterWordCountEntry] = Field(default_factory=list, max_length=50)


class WordCountConfigResponse(BaseModel):
    """Response with effective word count per chapter."""

    total_word_count: int | None = None
    project_default: int  # project.chapter_word_count
    chapters: list[dict[str, Any]] = Field(
        default_factory=list
    )  # [{chapter_num, target_word_count, effective_word_count}]


class ProgressResponse(BaseModel):
    total_elements: int
    revealed_elements: int
    reveal_percentage: float
    current_phase: str
    current_chapter: int
    total_chapters: int
    pending_foreshadows: int
    character_states: dict[str, Any]


# ═══ Community Module Schemas ═══


class CommunityNovelCreate(BaseModel):
    """Upload a novel to the community."""

    title: str = Field(min_length=1, max_length=200)
    author_name: str = Field(default="匿名作者", min_length=1, max_length=100)
    genre: str = Field(default="玄幻", min_length=1, max_length=50)
    project_id: str | None = Field(default=None, max_length=32)
    synopsis: str = Field(default="", max_length=20_000)
    story_outline: str = Field(default="", max_length=100_000)
    chapter_notes: str = Field(default="", max_length=100_000)
    allow_cocreation: bool = False
    tags: list[Annotated[str, Field(min_length=1, max_length=50)]] = Field(
        default_factory=list,
        max_length=20,
    )
    total_chapters: int = Field(default=0, ge=0, le=50)
    total_words: int = Field(default=0, ge=0, le=10_000_000)


class CommunityNovelUpdate(BaseModel):
    """Edit an existing community novel."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    author_name: str | None = Field(default=None, min_length=1, max_length=100)
    genre: str | None = Field(default=None, min_length=1, max_length=50)
    synopsis: str | None = Field(default=None, max_length=20_000)
    story_outline: str | None = Field(default=None, max_length=100_000)
    chapter_notes: str | None = Field(default=None, max_length=100_000)
    allow_cocreation: bool | None = None
    tags: list[Annotated[str, Field(min_length=1, max_length=50)]] | None = Field(
        default=None,
        max_length=20,
    )


class CommunityNovelResponse(BaseModel):
    """Novel card data for community listing."""

    id: str
    title: str
    author_name: str
    genre: str
    synopsis: str
    story_outline: str
    chapter_notes: str
    allow_cocreation: bool
    view_count: int
    like_count: int
    total_chapters: int
    total_words: int
    tags: list[str] = Field(default_factory=list)
    project_id: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CommunityNovelBrief(BaseModel):
    """Lightweight novel data for card display in infinite scroll."""

    id: str
    title: str
    author_name: str
    genre: str
    synopsis: str
    allow_cocreation: bool
    view_count: int
    like_count: int
    total_chapters: int
    total_words: int
    tags: list[str] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CommunityTagResponse(BaseModel):
    id: str
    name: str
    usage_count: int

    model_config = ConfigDict(from_attributes=True)
