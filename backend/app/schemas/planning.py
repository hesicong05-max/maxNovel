"""API schemas for the second-stage relational planning layer."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PlanningOperationCommand(BaseModel):
    operation_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class PlanningMutationCommand(PlanningOperationCommand):
    expected_structure_version: int = Field(ge=1)


class PlanningPartCreate(PlanningMutationCommand):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        if not (clean := value.strip()):
            raise ValueError("篇章名称不能为空")
        return clean


class PlanningPartUpdate(PlanningMutationCommand):
    expected_lock_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        if not (clean := value.strip()):
            raise ValueError("篇章名称不能为空")
        return clean


class PlanningChapterCreate(PlanningMutationCommand):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=20_000)
    target_word_count: int | None = Field(default=None, ge=500, le=10_000)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        if not (clean := value.strip()):
            raise ValueError("章节名称不能为空")
        return clean


class PlanningChapterUpdate(PlanningMutationCommand):
    expected_lock_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=20_000)
    target_word_count: int | None = Field(default=None, ge=500, le=10_000)
    clear_target_word_count: bool = False

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not (clean := value.strip()):
            raise ValueError("章节名称不能为空")
        return clean

    @model_validator(mode="after")
    def require_update(self):
        if (
            self.title is None
            and self.summary is None
            and self.target_word_count is None
            and not self.clear_target_word_count
        ):
            raise ValueError("至少需要修改一个章节字段")
        if self.target_word_count is not None and self.clear_target_word_count:
            raise ValueError("不能同时设置并清空目标字数")
        return self


class PlanningReorderPart(BaseModel):
    part_id: str = Field(min_length=1, max_length=32)
    chapter_ids: list[str] = Field(default_factory=list, max_length=1000)


class PlanningStructureReorder(PlanningMutationCommand):
    parts: list[PlanningReorderPart] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def limit_total_nodes(self):
        if sum(len(part.chapter_ids) for part in self.parts) > 1000:
            raise ValueError("一次重排最多包含 1000 个活动章节")
        if any(len(chapter_id) > 32 for part in self.parts for chapter_id in part.chapter_ids):
            raise ValueError("章节 ID 格式无效")
        return self


class PlanningMutationReceipt(BaseModel):
    receipt_kind: Literal["structure"] = "structure"
    receipt_id: str
    operation_key: str
    operation_type: str
    replayed: bool
    changed: bool
    project_id: str
    plan_id: str
    previous_structure_version: int
    new_structure_version: int
    affected_node: dict[str, Any] | None = None
    placement: dict[str, Any] | None = None
    structure: dict[str, Any] | None = None
    created_at: datetime

    model_config = ConfigDict(extra="forbid")


class PlanningAssignmentCreate(PlanningOperationCommand):
    expected_assignment_version: int = Field(ge=1)
    element_id: str = Field(min_length=32, max_length=32)
    expected_element_content_version: int = Field(ge=1)
    scope_type: Literal["novel", "part", "chapter"]
    scope_target_id: str = Field(min_length=32, max_length=32)


class PlanningAssignmentCommand(PlanningOperationCommand):
    expected_assignment_version: int = Field(ge=1)
    expected_lock_version: int = Field(ge=1)
    scope_type: Literal["novel", "part", "chapter"]
    scope_target_id: str = Field(min_length=32, max_length=32)


class PlanningAssignmentTypeSnapshot(BaseModel):
    id: str
    key: str
    display_name: str
    status: Literal["active", "archived"]

    model_config = ConfigDict(extra="forbid")


class PlanningAssignedElementSnapshot(BaseModel):
    id: str
    name: str
    summary: str
    type: PlanningAssignmentTypeSnapshot
    confirmation_status: Literal["candidate", "confirmed", "rejected"]
    lifecycle_status: Literal["active", "archived", "merged"]
    enabled: bool
    merged_into_element_id: str | None = None

    model_config = ConfigDict(extra="forbid")


class PlanningAssignmentScopeSnapshot(BaseModel):
    scope_type: Literal["novel", "part", "chapter"]
    scope_target_id: str
    title: str
    status: Literal["active", "archived"]
    part_id: str | None = None

    model_config = ConfigDict(extra="forbid")


class PlanningAssignmentSnapshot(BaseModel):
    id: str
    element_id: str
    scope: PlanningAssignmentScopeSnapshot
    status: Literal["active", "removed"]
    lock_version: int = Field(ge=1)
    assigned_at_content_version: int = Field(ge=1)
    current_content_version: int = Field(ge=1)
    content_changed_since_assignment: bool
    element: PlanningAssignedElementSnapshot
    generation_eligible: bool
    ineligible_reasons: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(extra="forbid")


class PlanningAssignmentSource(BaseModel):
    assignment_id: str
    scope: PlanningAssignmentScopeSnapshot
    lock_version: int = Field(ge=1)
    assigned_at_content_version: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid")


class PlanningEffectiveElementResponse(BaseModel):
    element_id: str
    current_content_version: int = Field(ge=1)
    content_changed_since_any_assignment: bool
    element: PlanningAssignedElementSnapshot
    direct_assignments: list[PlanningAssignmentSource] = Field(default_factory=list)
    inherited_from: list[PlanningAssignmentSource] = Field(default_factory=list)
    all_sources: list[PlanningAssignmentSource] = Field(default_factory=list)
    generation_eligible: bool
    ineligible_reasons: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class PlanningAssignmentCounts(BaseModel):
    direct: int = Field(ge=0)
    direct_active: int = Field(ge=0)
    direct_removed: int = Field(ge=0)
    effective: int = Field(ge=0)
    generation_eligible: int = Field(ge=0)
    ineligible: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


class PlanningAssignmentMutationReceipt(BaseModel):
    receipt_kind: Literal["assignment"] = "assignment"
    receipt_id: str
    operation_key: str
    operation_type: Literal[
        "assignment_create", "assignment_remove", "assignment_restore"
    ]
    replayed: bool
    changed: bool
    project_id: str
    plan_id: str
    previous_assignment_version: int
    new_assignment_version: int
    assignment: PlanningAssignmentSnapshot
    event_id: str
    created_at: datetime

    model_config = ConfigDict(extra="forbid")


class PlanningAssignmentScopeResponse(BaseModel):
    scope: PlanningAssignmentScopeSnapshot
    assignment_version: int
    direct_assignments: list[PlanningAssignmentSnapshot] = Field(default_factory=list)
    effective_elements: list[PlanningEffectiveElementResponse] = Field(default_factory=list)
    counts: PlanningAssignmentCounts

    model_config = ConfigDict(extra="forbid")


class PlanningAssignmentEventResponse(BaseModel):
    id: str
    action: Literal["assign", "remove", "restore"]
    previous_status: Literal["active", "removed"] | None = None
    new_status: Literal["active", "removed"]
    previous_lock_version: int = Field(ge=0)
    new_lock_version: int = Field(ge=1)
    element_content_version: int = Field(ge=1)
    performed_by: str
    created_at: datetime

    model_config = ConfigDict(extra="forbid")


class PlanningAssignmentHistoryItem(BaseModel):
    id: str
    scope: PlanningAssignmentScopeSnapshot
    status: Literal["active", "removed"]
    lock_version: int = Field(ge=1)
    events: list[PlanningAssignmentEventResponse] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class PlanningAssignmentHistoryResponse(BaseModel):
    element_id: str
    assignments: list[PlanningAssignmentHistoryItem] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class PlanningChapterResponse(BaseModel):
    id: str
    project_id: str
    plan_id: str
    part_id: str
    title: str
    summary: str
    target_word_count: int | None
    position: int
    status: Literal["active", "archived"]
    lock_version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PlanningPartResponse(BaseModel):
    id: str
    project_id: str
    plan_id: str
    title: str
    description: str
    position: int
    status: Literal["active", "archived"]
    lock_version: int
    created_at: datetime
    updated_at: datetime
    chapters: list[PlanningChapterResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class NovelPlanResponse(BaseModel):
    id: str
    project_id: str
    status: Literal["active", "archived"]
    structure_version: int
    assignment_version: int
    created_at: datetime
    updated_at: datetime
    parts: list[PlanningPartResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
