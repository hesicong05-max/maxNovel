"""API schemas for the second-stage relational planning layer."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
