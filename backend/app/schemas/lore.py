"""Read-only lore API schemas for the legacy projection phase."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class LoreTypeSummary(BaseModel):
    key: str
    display_name: str


class LoreMigrationStatus(BaseModel):
    storage_mode: str
    state: Literal["not_started", "preparing", "validating", "ready", "failed"]
    read_only: bool = True
    processed_count: int | None = None
    total_count: int | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    error_category: str | None = None
    can_retry: bool = False


class LoreElementListItem(BaseModel):
    id: str
    type: LoreTypeSummary
    name: str
    summary: str = ""
    confirmation_status: str
    lifecycle_status: str
    source_summary: str
    current_version: int
    revision: int
    updated_at: datetime
    relation_count: int = 0
    binding_count: int = 0


class LoreFacetCount(BaseModel):
    key: str
    label: str
    count: int


class LoreFacets(BaseModel):
    types: list[LoreFacetCount] = Field(default_factory=list)
    confirmation_statuses: list[LoreFacetCount] = Field(default_factory=list)
    sources: list[LoreFacetCount] = Field(default_factory=list)


class LoreListResponse(BaseModel):
    items: list[LoreElementListItem]
    next_cursor: str | None = None
    has_more: bool
    total: int
    facets: LoreFacets
    migration_status: LoreMigrationStatus


class LoreFieldDefinition(BaseModel):
    key: str
    label: str
    control: str = "text"
    help: str = ""
    order: int = 0


class LoreSourceSummary(BaseModel):
    kind: str
    label: str
    is_primary: bool = True
    created_at: datetime


class LoreElementDetail(LoreElementListItem):
    payload: dict[str, Any]
    field_definitions: list[LoreFieldDefinition]
    sources: list[LoreSourceSummary]
    created_at: datetime
    version_count: int = 1
    merged_to: str | None = None
    redirected_from: str | None = None
    read_only: bool = True
    migration_status: LoreMigrationStatus


class LoreVersionSummary(BaseModel):
    version_no: int
    name: str
    summary: str
    payload: dict[str, Any]
    type: LoreTypeSummary
    created_at: datetime
    change_reason: str
    read_only: bool = True


class LoreVersionsResponse(BaseModel):
    items: list[LoreVersionSummary]
    total: int
    read_only: bool = True


class LoreSourcesResponse(BaseModel):
    items: list[LoreSourceSummary]
    total: int
    read_only: bool = True
