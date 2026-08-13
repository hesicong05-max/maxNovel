"""Strict API schemas for durable chapter-generation preparation."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GenerationRunPrepareCommand(BaseModel):
    operation_key: str = Field(
        min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    expected_structure_version: int = Field(ge=1)
    expected_assignment_version: int = Field(ge=1)
    expected_chapter_lock_version: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid")


class GenerationContextVersions(BaseModel):
    structure: int = Field(ge=1)
    assignment: int = Field(ge=1)
    chapter_lock: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid")


class GenerationContextPart(BaseModel):
    id: str = Field(min_length=32, max_length=32)
    title: str
    description: str
    position: int = Field(ge=1)
    lock_version: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid")


class GenerationContextChapter(BaseModel):
    id: str = Field(min_length=32, max_length=32)
    title: str
    summary: str
    target_word_count: int | None = Field(default=None, ge=500, le=10_000)
    position: int = Field(ge=1)
    lock_version: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid")


class GenerationContextType(BaseModel):
    id: str = Field(min_length=32, max_length=32)
    key: str
    display_name: str
    schema_revision: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid")


class GenerationContextElementVersion(BaseModel):
    id: str = Field(min_length=32, max_length=32)
    element_id: str = Field(min_length=32, max_length=32)
    type_id: str = Field(min_length=32, max_length=32)
    version_no: int = Field(ge=1)
    name: str
    summary: str
    payload: dict[str, Any]
    field_states: dict[str, Any]
    source_id: str | None = None

    model_config = ConfigDict(extra="forbid")


class GenerationContextAssignmentSource(BaseModel):
    assignment_id: str = Field(min_length=32, max_length=32)
    scope_type: Literal["novel", "part", "chapter"]
    scope_target_id: str = Field(min_length=32, max_length=32)
    scope_title: str
    assignment_lock_version: int = Field(ge=1)
    assigned_at_content_version: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid")


class GenerationContextElement(BaseModel):
    element_id: str = Field(min_length=32, max_length=32)
    type: GenerationContextType
    version: GenerationContextElementVersion
    assignment_sources: list[GenerationContextAssignmentSource] = Field(
        min_length=1
    )

    model_config = ConfigDict(extra="forbid")


class GenerationContextRelationVersion(BaseModel):
    id: str = Field(min_length=32, max_length=32)
    relation_id: str = Field(min_length=32, max_length=32)
    version_no: int = Field(ge=1)
    source_element_id: str = Field(min_length=32, max_length=32)
    target_element_id: str = Field(min_length=32, max_length=32)
    relation_key: str
    forward_label: str
    reverse_label: str
    description: str
    metadata: dict[str, Any]
    status: Literal["active"]

    model_config = ConfigDict(extra="forbid")


class GenerationContextRelation(BaseModel):
    relation_id: str = Field(min_length=32, max_length=32)
    version: GenerationContextRelationVersion

    model_config = ConfigDict(extra="forbid")


class GenerationContextWarning(BaseModel):
    code: Literal["CHAPTER_SUMMARY_EMPTY", "LORE_CHANGED_SINCE_ASSIGNMENT"]
    element_id: str | None = Field(default=None, min_length=32, max_length=32)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_element_reference(self):
        if self.code == "LORE_CHANGED_SINCE_ASSIGNMENT" and self.element_id is None:
            raise ValueError("设定版本变化提示必须引用设定")
        if self.code == "CHAPTER_SUMMARY_EMPTY" and self.element_id is not None:
            raise ValueError("章节摘要提示不能引用设定")
        return self


class GenerationContextForeshadowActions(BaseModel):
    supported: Literal[False]
    items: list[dict[str, Any]] = Field(max_length=0)

    model_config = ConfigDict(extra="forbid")


class GenerationContextCounts(BaseModel):
    elements: int = Field(ge=1, le=100)
    relations: int = Field(ge=0, le=300)
    warnings: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


class GenerationContextManifest(BaseModel):
    schema_version: Literal[1]
    project_id: str = Field(min_length=32, max_length=32)
    plan_id: str = Field(min_length=32, max_length=32)
    versions: GenerationContextVersions
    part: GenerationContextPart
    chapter: GenerationContextChapter
    elements: list[GenerationContextElement] = Field(min_length=1, max_length=100)
    relations: list[GenerationContextRelation] = Field(max_length=300)
    foreshadow_actions: GenerationContextForeshadowActions
    warnings: list[GenerationContextWarning]
    counts: GenerationContextCounts

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_internal_references(self):
        if self.versions.chapter_lock != self.chapter.lock_version:
            raise ValueError("章节版本与上下文版本不一致")
        element_ids = [item.element_id for item in self.elements]
        if len(set(element_ids)) != len(element_ids):
            raise ValueError("生成上下文包含重复设定")
        element_id_set = set(element_ids)
        relation_ids = [item.relation_id for item in self.relations]
        if len(set(relation_ids)) != len(relation_ids):
            raise ValueError("生成上下文包含重复关系")
        if self.counts.model_dump() != {
            "elements": len(self.elements),
            "relations": len(self.relations),
            "warnings": len(self.warnings),
        }:
            raise ValueError("生成上下文计数不一致")
        scope_targets = {
            "novel": self.project_id,
            "part": self.part.id,
            "chapter": self.chapter.id,
        }
        for element in self.elements:
            if element.version.element_id != element.element_id:
                raise ValueError("设定版本身份与设定不一致")
            if element.version.type_id != element.type.id:
                raise ValueError("设定版本类型与设定不一致")
            for source in element.assignment_sources:
                if source.scope_target_id != scope_targets[source.scope_type]:
                    raise ValueError("设定分配来源不属于当前章节上下文")
        for relation in self.relations:
            version = relation.version
            if version.relation_id != relation.relation_id:
                raise ValueError("关系版本身份与关系不一致")
            if (
                version.source_element_id not in element_id_set
                or version.target_element_id not in element_id_set
            ):
                raise ValueError("关系端点不属于当前章节上下文")
        for warning in self.warnings:
            if warning.element_id is not None and warning.element_id not in element_id_set:
                raise ValueError("提示引用了上下文外的设定")
        return self


class GenerationRunResponse(BaseModel):
    id: str
    project_id: str
    plan_id: str
    planning_chapter_id: str
    operation_key: str
    replayed: bool
    status: Literal["prepared"]
    execution_mode: Literal["preflight_only"]
    ai_invoked: Literal[False]
    billing_effect: Literal["none"]
    structure_version: int = Field(ge=1)
    assignment_version: int = Field(ge=1)
    chapter_lock_version: int = Field(ge=1)
    context_schema_version: int = Field(ge=1)
    context_manifest: GenerationContextManifest
    context_checksum: str = Field(min_length=64, max_length=64)
    context_size_bytes: int = Field(ge=0, le=65_536)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(extra="forbid")


class GenerationAttemptExecuteCommand(BaseModel):
    operation_key: str = Field(
        min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    expected_context_checksum: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    expected_capability_checksum: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    confirm_model_call: Literal[True]

    model_config = ConfigDict(extra="forbid")


class GenerationAttemptError(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    message: str
    retryable: Literal[False]
    recommended_action: Literal[
        "inspect_failure", "keep_unknown_result", "start_new_confirmed_attempt"
    ]

    model_config = ConfigDict(extra="forbid")


class GenerationCapabilitySnapshot(BaseModel):
    schema_version: Literal[1]
    provider_name: str = Field(min_length=1, max_length=80)
    model_name: str = Field(min_length=1, max_length=200)
    max_output_tokens: int = Field(ge=1, le=1_000_000)
    input_limit_availability: Literal["unavailable"]
    max_input_tokens: None = None
    price_availability: Literal["unavailable"]

    model_config = ConfigDict(extra="forbid")


class GenerationCapabilityResponse(GenerationCapabilitySnapshot):
    capability_checksum: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )


class GenerationAttemptUsage(BaseModel):
    status: Literal["reported", "unavailable", "unknown"]
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_shape(self):
        values = (self.input_tokens, self.output_tokens, self.total_tokens)
        if self.status == "reported":
            if any(value is None for value in values):
                raise ValueError("供应商已报告用量时 token 字段必须完整")
            if self.total_tokens != self.input_tokens + self.output_tokens:
                raise ValueError("token 总量与输入输出之和不一致")
        elif any(value is not None for value in values):
            raise ValueError("未报告或未知用量不能伪造 token 数")
        return self


class GenerationAttemptResponse(BaseModel):
    id: str = Field(min_length=32, max_length=32)
    project_id: str = Field(min_length=32, max_length=32)
    run_id: str = Field(min_length=32, max_length=32)
    planning_chapter_id: str = Field(min_length=32, max_length=32)
    operation_key: str
    replayed: bool
    status: Literal[
        "reserved", "calling", "succeeded", "failed", "outcome_unknown"
    ]
    execution_mode: Literal["single_call"]
    billing_confirmed: Literal[True]
    ai_invoked: bool
    billing_effect: Literal["none", "possible"]
    capability: GenerationCapabilityResponse
    model_name: str
    prompt_schema_version: int = Field(ge=1)
    prompt_checksum: str = Field(min_length=64, max_length=64)
    context_checksum: str = Field(min_length=64, max_length=64)
    lock_version: int = Field(ge=1)
    usage: GenerationAttemptUsage
    candidate_id: str | None = Field(default=None, min_length=32, max_length=32)
    error: GenerationAttemptError | None = None
    claimed_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(extra="forbid")


class GenerationCandidateResponse(BaseModel):
    id: str = Field(min_length=32, max_length=32)
    project_id: str = Field(min_length=32, max_length=32)
    run_id: str = Field(min_length=32, max_length=32)
    planning_chapter_id: str = Field(min_length=32, max_length=32)
    source_attempt_id: str | None = Field(default=None, min_length=32, max_length=32)
    parent_candidate_id: str | None = Field(
        default=None, min_length=32, max_length=32
    )
    version_no: int = Field(ge=1)
    origin_kind: Literal["generated", "manual_edit"]
    title: str
    content: str
    content_format: Literal["plain_text"]
    content_checksum: str = Field(min_length=64, max_length=64)
    content_size_bytes: int = Field(ge=1, le=262_144)
    word_count: int = Field(ge=1)
    created_by: str = Field(min_length=32, max_length=32)
    created_at: datetime

    model_config = ConfigDict(extra="forbid")
