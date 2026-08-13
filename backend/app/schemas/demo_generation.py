"""Strict wire contracts for zero-LLM technical-demo generation."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TechnicalDemoCapabilityResponse(BaseModel):
    schema_version: Literal[1] = 1
    execution_mode: Literal["technical_demo"] = "technical_demo"
    fixture_version: Literal[1] = 1
    adapter_schema_version: Literal[1] = 1
    content_spec_version: Literal[1] = 1
    project_id: str = Field(min_length=32, max_length=32)
    planning_chapter_id: str = Field(min_length=32, max_length=32)
    run_id: str = Field(min_length=32, max_length=32)
    context_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixed_response: Literal[True] = True
    ai_invoked: Literal[False] = False
    billing_effect: Literal["none"] = "none"
    usage_status: Literal["not_applicable"] = "not_applicable"
    capability_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid")


class TechnicalDemoExecuteCommand(BaseModel):
    operation_key: str = Field(
        min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    expected_context_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_capability_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_version: Literal[1]
    confirm_technical_demo: Literal[True]

    model_config = ConfigDict(extra="forbid")


class TechnicalDemoExecutionResponse(BaseModel):
    schema_version: Literal[1] = 1
    execution_mode: Literal["technical_demo"] = "technical_demo"
    fixture_version: Literal[1]
    adapter_schema_version: Literal[1]
    content_spec_version: Literal[1]
    project_id: str = Field(min_length=32, max_length=32)
    planning_chapter_id: str = Field(min_length=32, max_length=32)
    run_id: str = Field(min_length=32, max_length=32)
    operation_key: str = Field(min_length=8, max_length=128)
    context_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_id: str = Field(min_length=32, max_length=32)
    candidate_id: str = Field(min_length=32, max_length=32)
    status: Literal["succeeded"]
    replayed: bool
    ai_invoked: Literal[False]
    billing_effect: Literal["none"]
    usage_status: Literal["not_applicable"]
    created_at: datetime
    completed_at: datetime

    model_config = ConfigDict(extra="forbid")


class TechnicalDemoCandidateResponse(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(min_length=32, max_length=32)
    project_id: str = Field(min_length=32, max_length=32)
    run_id: str = Field(min_length=32, max_length=32)
    planning_chapter_id: str = Field(min_length=32, max_length=32)
    source_technical_demo_execution_id: str = Field(min_length=32, max_length=32)
    parent_candidate_id: None = None
    version_no: int = Field(ge=1)
    origin_kind: Literal["technical_demo"]
    title: str
    content: str
    content_format: Literal["plain_text"]
    content_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_size_bytes: int = Field(ge=1, le=262_144)
    word_count: int = Field(ge=1)
    created_by: str = Field(min_length=32, max_length=32)
    ai_invoked: Literal[False]
    billing_effect: Literal["none"]
    usage_status: Literal["not_applicable"]
    created_at: datetime

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_content(self):
        if not self.content.strip():
            raise ValueError("技术模拟候选不能为空")
        return self
