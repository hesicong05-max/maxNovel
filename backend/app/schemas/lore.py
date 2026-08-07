"""Lore API schemas — read and write."""

import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


STATUS_FIELD_MAP: dict[str, str] = {
    "name": "name",
    "type_key": "type_key",
    "summary": "summary",
}


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


class LoreMigrationPreviewCounts(BaseModel):
    legacy_total: int
    mappable: int
    review_required: int
    possible_conflict: int
    blocked: int


class LoreMigrationPreviewItem(BaseModel):
    legacy_category: str
    legacy_index: int
    legacy_id: str | None = None
    planned_element_id: str
    proposed_type_key: str | None = None
    name: str
    classification: Literal[
        "mappable", "review_required", "possible_conflict", "blocked"
    ]
    reason_codes: list[str] = Field(default_factory=list)
    source_locator: str
    source_kind: str | None = None
    source_label: str | None = None
    exact_excerpt_available: bool = False
    original_value: Any
    mapped_fields: dict[str, Any] = Field(default_factory=dict)
    unmapped_fields: list[str] = Field(default_factory=list)


class LoreMigrationPreviewIssue(BaseModel):
    case_id: str
    severity: Literal["review", "blocked"]
    reason_code: str
    legacy_category: str | None = None
    legacy_index: int | None = None
    message: str
    recommended_action: str


class LoreMigrationPreviewResponse(BaseModel):
    preview_schema_version: int
    mapping_version: int
    project_id: str
    storage_mode: str
    source_checksum: str
    semantic_result_checksum: str
    checked_at: datetime
    overall_status: Literal["ready", "review_required", "blocked"]
    dry_run: Literal[True]
    read_only: Literal[True]
    writes_performed: Literal[0]
    commit_available: Literal[False]
    counts: LoreMigrationPreviewCounts
    by_legacy_category: dict[str, int] = Field(default_factory=dict)
    by_target_type: dict[str, int] = Field(default_factory=dict)
    items: list[LoreMigrationPreviewItem] = Field(default_factory=list)
    issues: list[LoreMigrationPreviewIssue] = Field(default_factory=list)


class LoreElementListItem(BaseModel):
    id: str
    type: LoreTypeSummary
    name: str
    summary: str = ""
    confirmation_status: str
    lifecycle_status: str
    enabled: bool = True
    generation_eligible: bool = True
    source_summary: str
    current_version: int
    revision: int
    lock_version: int = 1
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
    lifecycle_statuses: list[LoreFacetCount] = Field(default_factory=list)
    enabled_statuses: list[LoreFacetCount] = Field(default_factory=list)
    relation_statuses: list[LoreFacetCount] = Field(default_factory=list)


class LoreListResponse(BaseModel):
    items: list[LoreElementListItem]
    next_cursor: str | None = None
    has_more: bool
    total: int
    facets: LoreFacets
    migration_status: LoreMigrationStatus


class LoreRepositoryCapabilities(BaseModel):
    candidate_review: bool = True
    candidate_accept: bool
    formal_create: bool = False
    formal_conflict_tracking: bool = False
    formal_merge_preview: bool = False
    formal_merge_commit: bool = False
    search_fields: list[str] = Field(default_factory=lambda: ["name", "summary"])


class LoreRepositoryOverview(BaseModel):
    formal_total: int
    confirmed_active: int
    pending_review: int
    needs_attention: int
    disabled: int
    archived: int
    review_pending: int = 0
    migration_status: LoreMigrationStatus
    capabilities: LoreRepositoryCapabilities
    count_definitions: dict[str, dict[str, Any]] = Field(default_factory=dict)


class LoreFieldDefinition(BaseModel):
    key: str
    label: str
    control: str = "text"
    value_type: str = "string"
    help: str = ""
    order: int = 0
    required: bool = False


class LoreFieldError(BaseModel):
    field: str
    message: str


class LoreSourceSummary(BaseModel):
    id: str | None = None
    kind: str
    label: str
    is_primary: bool = True
    created_at: datetime
    reference: str | None = None
    locator: dict[str, Any] = Field(default_factory=dict)
    excerpt: str | None = None
    excerpt_hash: str | None = None
    confirmation_status: str = "provided"


class LoreSourceInput(BaseModel):
    kind: str = Field(
        ...,
        min_length=1,
        max_length=30,
        pattern=r"^[a-z][a-z0-9_:-]*$",
    )
    reference: str | None = Field(default=None, max_length=200)
    locator: dict[str, Any] = Field(default_factory=dict)
    excerpt: str | None = Field(default=None, max_length=2000)
    is_primary: bool = False
    confirmation_status: str = Field(default="provided")

    @field_validator("confirmation_status")
    @classmethod
    def _valid_confirmation(cls, value: str) -> str:
        if value not in ("provided", "needs_confirmation"):
            raise ValueError(
                "confirmation_status must be provided or needs_confirmation"
            )
        return value

    @field_validator("locator")
    @classmethod
    def _bounded_locator(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("locator must be JSON serializable") from exc
        if len(encoded.encode("utf-8")) > 8192:
            raise ValueError("locator must not exceed 8192 bytes")
        return value


class LoreElementCreate(BaseModel):
    operation_key: str = Field(
        ...,
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    type_key: str = Field(..., max_length=50)
    name: str = Field(..., min_length=1, max_length=200)
    summary: str = Field(default="", max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    field_states: dict[str, str] = Field(default_factory=dict)
    sources: list[LoreSourceInput] = Field(default_factory=list)

    @field_validator("field_states")
    @classmethod
    def _valid_states(cls, value: dict[str, str]) -> dict[str, str]:
        for key, state in value.items():
            if state not in ("provided", "unknown", "needs_confirmation"):
                raise ValueError(
                    f"field {key}: state must be provided/unknown/needs_confirmation"
                )
        return value

    @field_validator("sources")
    @classmethod
    def _single_primary_source(
        cls,
        value: list[LoreSourceInput],
    ) -> list[LoreSourceInput]:
        if sum(1 for source in value if source.is_primary) > 1:
            raise ValueError("only one source may be primary")
        return value


class LoreTypeCreate(BaseModel):
    key: str = Field(
        ...,
        min_length=2,
        max_length=50,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    display_name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    field_schema: list[LoreFieldDefinition] = Field(
        default_factory=list,
        max_length=100,
    )

    @field_validator("field_schema")
    @classmethod
    def _valid_field_schema(
        cls,
        value: list[LoreFieldDefinition],
    ) -> list[LoreFieldDefinition]:
        keys: set[str] = set()
        for field in value:
            if re.fullmatch(r"[a-z][a-z0-9_]*", field.key) is None:
                raise ValueError("field key must contain letters, numbers, or underscores")
            if field.key in keys:
                raise ValueError(f"duplicate field key: {field.key}")
            if not field.label or len(field.label) > 100:
                raise ValueError(f"field {field.key}: label length must be 1-100")
            if len(field.help) > 500:
                raise ValueError(f"field {field.key}: help is too long")
            if len(field.control) > 30 or len(field.value_type) > 30:
                raise ValueError(f"field {field.key}: control or value_type is too long")
            if field.control not in ("text", "textarea"):
                raise ValueError(f"field {field.key}: unsupported control")
            if field.value_type not in ("string", "text", "reference"):
                raise ValueError(f"field {field.key}: unsupported value_type")
            if field.required:
                raise ValueError("custom lore fields cannot be required in M1")
            keys.add(field.key)
        return value


class LoreTypeResponse(BaseModel):
    id: str
    key: str
    display_name: str
    description: str = ""
    is_builtin: bool
    schema_revision: int
    field_schema: list[LoreFieldDefinition]
    status: Literal["active", "archived"]
    created_at: datetime
    updated_at: datetime


class LoreTypesResponse(BaseModel):
    items: list[LoreTypeResponse]
    total: int


class LoreElementUpdate(BaseModel):
    expected_version: int = Field(..., ge=1)
    name: str = Field(..., min_length=1, max_length=200)
    summary: str = Field(default="", max_length=2000)
    payload: dict[str, Any]
    field_states: dict[str, str] = Field(default_factory=dict)

    @field_validator("field_states")
    @classmethod
    def _valid_states(cls, value: dict[str, str]) -> dict[str, str]:
        for key, state in value.items():
            if state not in ("provided", "unknown", "needs_confirmation"):
                raise ValueError(
                    f"field {key}: state must be provided/unknown/needs_confirmation"
                )
        return value


class LoreElementStateInput(BaseModel):
    expected_version: int = Field(..., ge=1)
    reason: str = Field(default="", max_length=200)


class LoreVersionConflictDetail(BaseModel):
    code: str = "LORE_VERSION_CONFLICT"
    message: str = "设定已被其他操作更新，请重新加载后重试"
    current_lock_version: int
    updated_at: datetime


class LoreElementResponse(BaseModel):
    id: str
    type: LoreTypeSummary
    name: str
    summary: str = ""
    confirmation_status: str
    lifecycle_status: str
    enabled: bool
    generation_eligible: bool
    lock_version: int
    content_version: int
    payload_schema_revision: int
    payload: dict[str, Any]
    field_states: dict[str, str]
    field_definitions: list[LoreFieldDefinition]
    sources: list[LoreSourceSummary]
    relation_count: int = 0
    binding_count: int = 0
    created_at: datetime
    updated_at: datetime


class LoreElementCreateResponse(LoreElementResponse):
    replayed: bool = False


class LoreFieldValidationError(BaseModel):
    detail: str
    field_errors: list[LoreFieldError] = Field(default_factory=list)


class LoreElementDetail(LoreElementListItem):
    payload: dict[str, Any]
    field_states: dict[str, str] = Field(default_factory=dict)
    payload_schema_revision: int = 1
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
    field_states: dict[str, str] = Field(default_factory=dict)
    type_schema_revision: int = 1
    type: LoreTypeSummary
    created_at: datetime
    change_reason: str
    created_by: str | None = None
    read_only: bool = True


class LoreVersionsResponse(BaseModel):
    items: list[LoreVersionSummary]
    total: int
    read_only: bool = True


class LoreSourcesResponse(BaseModel):
    items: list[LoreSourceSummary]
    total: int
    read_only: bool = True


class LoreRelationCreate(BaseModel):
    operation_key: str = Field(
        ...,
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    target_element_id: str = Field(..., min_length=1, max_length=32)
    source_expected_version: int = Field(..., ge=1)
    target_expected_version: int = Field(..., ge=1)
    relation_type: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^[a-z][a-z0-9_:-]*$",
    )
    custom_forward_label: str | None = Field(default=None, max_length=100)
    custom_reverse_label: str | None = Field(default=None, max_length=100)
    description: str = Field(default="", max_length=2000)


class LoreRelationUpdate(BaseModel):
    expected_version: int = Field(..., ge=1)
    forward_label: str = Field(..., min_length=1, max_length=100)
    reverse_label: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LoreRelationStateInput(BaseModel):
    expected_version: int = Field(..., ge=1)
    reason: str = Field(default="", max_length=200)


class LoreRelationEndpoint(BaseModel):
    id: str
    name: str
    type: LoreTypeSummary
    summary: str = ""
    lifecycle_status: str
    enabled: bool


class LoreRelationResponse(BaseModel):
    id: str
    source: LoreRelationEndpoint
    target: LoreRelationEndpoint
    relation_key: str
    forward_label: str
    reverse_label: str
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: Literal["active", "archived"]
    version_no: int
    lock_version: int
    created_at: datetime
    updated_at: datetime


class LoreRelationCreateResponse(LoreRelationResponse):
    replayed: bool = False


class LoreRelationTypeResponse(BaseModel):
    key: str
    display_name: str
    forward_label: str
    reverse_label: str
    symmetric: bool


class LoreRelationTypesResponse(BaseModel):
    items: list[LoreRelationTypeResponse]


class LoreRelationListResponse(BaseModel):
    items: list[LoreRelationResponse]
    next_cursor: str | None = None
    has_more: bool
    total: int


class LoreRelationVersionSummary(BaseModel):
    version_no: int
    source_element_id: str
    target_element_id: str
    relation_key: str
    forward_label: str
    reverse_label: str
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: Literal["active", "archived"]
    change_reason: str = ""
    created_at: datetime


class LoreRelationVersionsResponse(BaseModel):
    items: list[LoreRelationVersionSummary]
    total: int


class LoreReviewEndpoint(BaseModel):
    id: str
    name: str
    type: LoreTypeSummary
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    field_states: dict[str, str] = Field(default_factory=dict)
    content_version: int
    lifecycle_status: str
    enabled: bool
    sources: list[LoreSourceSummary] = Field(default_factory=list)


class LoreReviewEvidence(BaseModel):
    field_key: str
    label: str
    comparison: Literal[
        "same", "different", "left_empty", "right_empty", "author_report"
    ]
    left_value: str | None = None
    right_value: str | None = None
    statement: str | None = None


class LoreReviewDecisionEvent(BaseModel):
    id: str
    previous_status: str
    new_status: str
    evidence_revision: int
    note: str = ""
    applied: bool
    performed_by: str | None = None
    created_at: datetime


class LoreReviewSuggestionListItem(BaseModel):
    id: str
    kind: Literal["possible_duplicate", "possible_conflict"]
    origin: Literal["system_scan", "author_report"]
    detection_state: Literal["active", "stale"]
    review_status: str
    needs_review: bool
    lock_version: int
    evidence_revision: int
    left: LoreRelationEndpoint
    right: LoreRelationEndpoint
    primary_reason: str
    stale: bool
    merge_allowed: bool
    merge_block_reason: str | None = None
    updated_at: datetime


class LoreReviewSuggestionDetail(LoreReviewSuggestionListItem):
    rule_key: str
    rule_version: int
    left_snapshot: LoreReviewEndpoint
    right_snapshot: LoreReviewEndpoint
    evidence: list[LoreReviewEvidence]
    decided_evidence_revision: int | None = None
    history: list[LoreReviewDecisionEvent] = Field(default_factory=list)


class LoreReviewSuggestionsResponse(BaseModel):
    items: list[LoreReviewSuggestionListItem]
    next_cursor: str | None = None
    has_more: bool
    total: int


class LoreReviewScanResponse(BaseModel):
    created: int
    updated: int
    unchanged: int
    marked_stale: int
    active_total: int
    pending_total: int
    truncated: bool = False
    rescan_required: bool = False


class LoreManualReviewCreateInput(BaseModel):
    operation_key: str = Field(
        ...,
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    kind: Literal["possible_duplicate", "possible_conflict"]
    left_element_id: str = Field(..., min_length=1, max_length=32)
    right_element_id: str = Field(..., min_length=1, max_length=32)
    left_expected_lock_version: int = Field(..., ge=1)
    right_expected_lock_version: int = Field(..., ge=1)
    note: str = Field(..., min_length=1, max_length=500)

    @field_validator("note")
    @classmethod
    def _non_empty_note(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请填写需要复核的具体说明")
        return value.strip()


class LoreManualReviewCreateResponse(BaseModel):
    suggestion: LoreReviewSuggestionDetail
    replayed: bool = False
    created: bool = False
    reused: bool = False


class LoreReviewDecisionInput(BaseModel):
    operation_key: str = Field(
        ...,
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    expected_version: int = Field(..., ge=1)
    expected_evidence_revision: int = Field(..., ge=1)
    decision: Literal[
        "deferred",
        "confirmed_duplicate",
        "confirmed_conflict",
        "not_an_issue",
    ]
    note: str = Field(default="", max_length=500)


class LoreReviewDecisionResponse(BaseModel):
    suggestion: LoreReviewSuggestionDetail
    replayed: bool = False
    applied: bool = True
    next_pending_id: str | None = None


LoreMergeChoice = Literal["survivor", "merged", "manual"]


class LoreMergePreviewInput(BaseModel):
    suggestion_expected_version: int = Field(..., ge=1)
    expected_evidence_revision: int = Field(..., ge=1)
    survivor_element_id: str = Field(..., min_length=1, max_length=32)
    merged_element_id: str = Field(..., min_length=1, max_length=32)
    survivor_expected_lock_version: int = Field(..., ge=1)
    survivor_expected_content_version: int = Field(..., ge=1)
    merged_expected_lock_version: int = Field(..., ge=1)
    merged_expected_content_version: int = Field(..., ge=1)
    name_choice: LoreMergeChoice
    summary_choice: LoreMergeChoice
    field_choices: dict[str, LoreMergeChoice] = Field(default_factory=dict)
    final_name: str = Field(..., min_length=1, max_length=200)
    final_summary: str = Field(default="", max_length=2000)
    final_payload: dict[str, Any] = Field(default_factory=dict)
    final_field_states: dict[str, str] = Field(default_factory=dict)

    @field_validator("final_field_states")
    @classmethod
    def _valid_merge_states(cls, value: dict[str, str]) -> dict[str, str]:
        for key, state in value.items():
            if state not in ("provided", "unknown", "needs_confirmation"):
                raise ValueError(
                    f"field {key}: state must be provided/unknown/needs_confirmation"
                )
        return value


class LoreMergeRelationPlan(BaseModel):
    relation_id: str
    action: Literal[
        "rewire",
        "exact_duplicate_archive",
        "self_loop_archive",
        "blocker",
    ]
    current_source_element_id: str
    current_target_element_id: str
    planned_source_element_id: str
    planned_target_element_id: str
    relation_key: str
    retained_relation_id: str | None = None
    reason: str


class LoreMergeSourceImpact(BaseModel):
    survivor_source_count: int
    merged_source_count: int
    preserved_total: int
    exact_duplicate_pairs: int
    strategy: Literal["preserve_in_place"] = "preserve_in_place"


class LoreMergePreviewResponse(BaseModel):
    suggestion_id: str
    survivor: LoreReviewEndpoint
    merged: LoreReviewEndpoint
    final_name: str
    final_summary: str
    final_payload: dict[str, Any]
    final_field_states: dict[str, str]
    selection_snapshot: dict[str, Any]
    source_impact: LoreMergeSourceImpact
    relation_plan: list[LoreMergeRelationPlan]
    blockers: list[str] = Field(default_factory=list)
    would_be_generation_eligible: bool
    preview_token: str
    expires_at: datetime
    commit_available: bool = False


class LoreMergeCommitInput(BaseModel):
    operation_key: str = Field(
        ...,
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    preview_token: str = Field(..., min_length=32, max_length=4096)
    preview: LoreMergePreviewInput


class LoreMergeRelationActionSummary(BaseModel):
    id: str
    relation_id: str | None
    retained_relation_id: str | None
    action: Literal["rewired", "exact_duplicate_archived", "self_loop_archived"]
    before_snapshot: dict[str, Any]
    after_snapshot: dict[str, Any]
    previous_lock_version: int
    new_lock_version: int


class LoreMergeOperationResponse(BaseModel):
    id: str
    project_id: str
    operation_key: str
    suggestion_id: str | None
    evidence_revision: int
    survivor_element_id: str
    merged_element_id: str
    survivor_before_content_version: int
    survivor_before_lock_version: int
    survivor_after_content_version: int
    survivor_after_lock_version: int
    merged_before_content_version: int
    merged_before_lock_version: int
    merged_after_lock_version: int
    selection_snapshot: dict[str, Any]
    impact_summary: dict[str, Any]
    relation_actions: list[LoreMergeRelationActionSummary] = Field(default_factory=list)
    created_at: datetime
    replayed: bool = False


class LoreMergeOperationsResponse(BaseModel):
    items: list[LoreMergeOperationResponse]
