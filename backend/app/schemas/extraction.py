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
    extracted_value: str | None
    current_value: str | None
    current_state: Literal["provided", "unknown", "needs_confirmation"]
    value_origin: Literal["ai_extraction", "user_override", "user_cleared"]
    state: Literal["provided", "unknown", "needs_confirmation"]
    excerpt: str | None
    locator: dict[str, Any]
    excerpt_hash: str | None
    source_hash: str
    is_name: bool


class LoreCandidateActions(BaseModel):
    can_edit: bool
    can_accept: bool
    can_reject: bool
    can_open_element: bool
    disabled_reasons: dict[str, list[str]] = Field(default_factory=dict)


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
    suggestion_resolutions: dict[str, str]
    user_overrides: dict[str, Any]
    status: Literal["pending_review", "accepted", "rejected", "failed"]
    revision: int
    accepted_element_id: str | None
    error_code: str | None
    needs_attention: bool
    evidence: list[LoreCandidateEvidenceResponse]
    can_accept: bool
    disabled_reasons: list[str]
    actions: LoreCandidateActions
    created_at: datetime
    updated_at: datetime


class LoreCandidateInboxResponse(BaseModel):
    items: list[LoreExtractionCandidateResponse]
    next_cursor: str | None = None
    has_more: bool
    total: int
    applied_filters: dict[str, Any] = Field(default_factory=dict)
    query_signature: str


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


class LoreCandidateEdit(BaseModel):
    expected_version: int = Field(..., ge=1)
    type_key: str = Field(..., min_length=1, max_length=50)
    name: str | None = Field(default=None, max_length=200)
    summary: str = Field(default="", max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    field_states: dict[str, str] = Field(default_factory=dict)
    suggestion_resolutions: dict[
        str,
        Literal["accept_as_new", "dismissed", "deferred"],
    ] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("field_states")
    @classmethod
    def _valid_states(cls, value: dict[str, str]) -> dict[str, str]:
        for key, state in value.items():
            if state not in ("provided", "unknown", "needs_confirmation"):
                raise ValueError(f"field {key}: invalid state")
        return value


class LoreCandidateActionInput(BaseModel):
    expected_version: int = Field(..., ge=1)
    suggestion_resolutions: dict[
        str,
        Literal["accept_as_new", "dismissed", "deferred"],
    ] = Field(default_factory=dict)


class LoreCandidateActionResponse(BaseModel):
    candidate: LoreExtractionCandidateResponse
    action_result: Literal[
        "accepted",
        "already_accepted",
        "rejected",
        "already_rejected",
    ]
    replayed: bool
    accepted_element_id: str | None
    remaining_pending_count: int
    next_pending_candidate_id: str | None


class LoreCandidateRevisionResponse(BaseModel):
    revision: int
    type_key: str | None
    name: str | None
    summary: str
    payload: dict[str, Any]
    field_states: dict[str, str]
    suggestion_resolutions: dict[str, str]
    user_overrides: dict[str, Any]
    change_kind: str
    created_by: str | None
    created_at: datetime


class LoreCandidateRevisionsResponse(BaseModel):
    items: list[LoreCandidateRevisionResponse]
    total: int
