"""Public contracts for reviewable lore extraction batches."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


MAX_EXTRACTION_SOURCE_CHARS = 20_000


class LoreExtractionCreate(BaseModel):
    idempotency_key: str = Field(
        ...,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    document_text: str = Field(
        ...,
        min_length=10,
        max_length=200_000,
    )
    source_kind: str = Field(
        default="manual_text",
        min_length=1,
        max_length=30,
        pattern=r"^[a-z][a-z0-9_:-]*$",
    )
    source_ref: str | None = Field(default=None, max_length=200)

    @field_validator("document_text")
    @classmethod
    def _meaningful_text(cls, value: str) -> str:
        if len(value.strip()) < 10:
            raise ValueError("文档内容过短，至少需要 10 个字符")
        return value


class LoreCandidateEvidenceResponse(BaseModel):
    id: str
    field_key: str
    label: str
    value: str | None
    state: Literal["provided", "unknown", "needs_confirmation"]
    excerpt: str | None
    locator: dict[str, Any]
    excerpt_hash: str | None
    source_hash: str
    is_name: bool


class LoreExtractionCandidateResponse(BaseModel):
    id: str
    batch_id: str
    ordinal: int
    type_key: str | None
    type_display_name: str | None
    name: str | None
    summary: str
    payload: dict[str, Any]
    field_states: dict[str, str]
    relation_suggestions: list[dict[str, Any]]
    duplicate_conflict_suggestions: list[dict[str, Any]]
    status: Literal["pending_review", "accepted", "rejected", "failed"]
    revision: int
    accepted_element_id: str | None
    error_code: str | None
    evidence: list[LoreCandidateEvidenceResponse]
    can_accept: bool
    disabled_reasons: list[str]
    created_at: datetime
    updated_at: datetime


class LoreExtractionBatchResponse(BaseModel):
    id: str
    project_id: str
    status: Literal["running", "completed", "failed", "outcome_unknown"]
    source_kind: str
    source_ref: str | None
    source_hash: str
    source_preserved: bool = True
    extractor_version: str
    model_name: str | None
    candidate_count: int
    pending_review_count: int
    accepted_count: int
    rejected_count: int
    failed_count: int
    retryable: bool
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class LoreExtractionCandidatesResponse(BaseModel):
    items: list[LoreExtractionCandidateResponse]
    total: int
