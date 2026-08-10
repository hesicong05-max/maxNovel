"""API schemas for the second-stage relational planning layer."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PlanningMutationCommand(BaseModel):
    operation_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
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
