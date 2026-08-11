"""Strict API contracts for durable foreshadow planning and facts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ForeshadowState = Literal["unplanted", "planted", "pending_resolution", "resolved"]


class ForeshadowOperationCommand(BaseModel):
    operation_key: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )

    model_config = ConfigDict(extra="forbid")


class ForeshadowBindCommand(ForeshadowOperationCommand):
    element_id: str = Field(min_length=32, max_length=32)
    expected_structure_version: int = Field(ge=1)
    expected_element_lock_version: int = Field(ge=1)


class ForeshadowLifecycleCommand(ForeshadowOperationCommand):
    expected_lifecycle_version: int = Field(ge=1)


class ForeshadowRestoreCommand(ForeshadowLifecycleCommand):
    expected_structure_version: int = Field(ge=1)
    expected_element_lock_version: int = Field(ge=1)


class ForeshadowPlanCreate(ForeshadowLifecycleCommand):
    expected_structure_version: int = Field(ge=1)
    action_kind: Literal["plant", "resolve"]
    target_type: Literal["part", "chapter"]
    target_id: str = Field(min_length=32, max_length=32)
    expected_target_lock_version: int = Field(ge=1)
    condition_text: str = Field(default="", max_length=2_000)
    note: str = Field(default="", max_length=2_000)

    @field_validator("condition_text", "note")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class ForeshadowPlanCommand(ForeshadowLifecycleCommand):
    expected_structure_version: int = Field(ge=1)
    expected_item_lock_version: int = Field(ge=1)


class ForeshadowFactCreate(ForeshadowLifecycleCommand):
    expected_structure_version: int = Field(ge=1)
    fact_kind: Literal["planted", "resolved"]
    chapter_id: str = Field(min_length=32, max_length=32)
    expected_chapter_lock_version: int = Field(ge=1)
    note: str = Field(default="", max_length=2_000)

    @field_validator("note")
    @classmethod
    def strip_note(cls, value: str) -> str:
        return value.strip()


class ForeshadowFactRetract(ForeshadowLifecycleCommand):
    expected_fact_lock_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        if not (clean := value.strip()):
            raise ValueError("撤回原因不能为空")
        return clean


class ForeshadowElementSnapshot(BaseModel):
    id: str
    name: str
    summary: str
    confirmation_status: Literal["candidate", "confirmed", "rejected"]
    lifecycle_status: Literal["active", "archived", "merged"]
    enabled: bool
    content_version: int = Field(ge=1)
    lock_version: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid")


class ForeshadowTargetSnapshot(BaseModel):
    target_type: Literal["part", "chapter"]
    target_id: str
    title: str
    status: Literal["active", "archived"]
    part_id: str | None = None
    position: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid")


class ForeshadowPlanItemResponse(BaseModel):
    id: str
    action_kind: Literal["plant", "resolve"]
    target: ForeshadowTargetSnapshot
    condition_text: str
    note: str
    status: Literal["active", "cancelled"]
    lock_version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(extra="forbid")


class ForeshadowFactResponse(BaseModel):
    id: str
    fact_kind: Literal["planted", "resolved"]
    chapter: ForeshadowTargetSnapshot
    note: str
    status: Literal["active", "retracted"]
    lock_version: int = Field(ge=1)
    created_at: datetime
    retracted_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")


class ForeshadowLifecycleResponse(BaseModel):
    id: str
    project_id: str
    plan_id: str
    status: Literal["active", "archived"]
    state: ForeshadowState
    lock_version: int = Field(ge=1)
    element: ForeshadowElementSnapshot
    plans: list[ForeshadowPlanItemResponse] = Field(default_factory=list)
    facts: list[ForeshadowFactResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(extra="forbid")


class ForeshadowStateCounts(BaseModel):
    unplanted: int = Field(ge=0)
    planted: int = Field(ge=0)
    pending_resolution: int = Field(ge=0)
    resolved: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


class ForeshadowLifecycleListResponse(BaseModel):
    items: list[ForeshadowLifecycleResponse]
    counts: ForeshadowStateCounts
    next_cursor: str | None = None

    model_config = ConfigDict(extra="forbid")


class ForeshadowEventResponse(BaseModel):
    id: str
    event_kind: Literal[
        "create",
        "archive",
        "restore",
        "plan_create",
        "plan_cancel",
        "plan_restore",
        "fact_record",
        "fact_retract",
    ]
    plan_item_id: str | None = None
    fact_id: str | None = None
    previous_lifecycle_version: int = Field(ge=0)
    new_lifecycle_version: int = Field(ge=1)
    metadata: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(extra="forbid")


class ForeshadowHistoryResponse(BaseModel):
    lifecycle_id: str
    items: list[ForeshadowEventResponse]

    model_config = ConfigDict(extra="forbid")


class ForeshadowMutationReceipt(BaseModel):
    receipt_id: str
    operation_key: str
    operation_type: Literal[
        "foreshadow_bind",
        "foreshadow_archive",
        "foreshadow_restore",
        "foreshadow_plan_create",
        "foreshadow_plan_cancel",
        "foreshadow_plan_restore",
        "foreshadow_fact_record",
        "foreshadow_fact_retract",
    ]
    replayed: bool
    project_id: str
    lifecycle_id: str
    previous_lifecycle_version: int = Field(ge=0)
    new_lifecycle_version: int = Field(ge=1)
    event_id: str
    lifecycle: ForeshadowLifecycleResponse
    created_at: datetime

    model_config = ConfigDict(extra="forbid")
