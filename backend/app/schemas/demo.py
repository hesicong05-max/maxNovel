"""Strict contracts for the non-production technical-demo fixture."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DemoFixtureBootstrapCommand(BaseModel):
    fixture_version: Literal[1]
    operation_key: Literal["demo:v1:bootstrap"]

    model_config = ConfigDict(extra="forbid")


class DemoFixtureBootstrapResponse(BaseModel):
    schema_version: Literal[1] = 1
    fixture_version: Literal[1] = 1
    mode: Literal["technical_demo_fixture"] = "technical_demo_fixture"
    environment: Literal["non_production"] = "non_production"
    state: Literal["ready"] = "ready"
    replayed: bool
    project_id: str = Field(min_length=32, max_length=32)
    plan_id: str = Field(min_length=32, max_length=32)
    part_id: str = Field(min_length=32, max_length=32)
    chapter_id: str = Field(min_length=32, max_length=32)
    element_id: str = Field(min_length=32, max_length=32)
    assignment_id: str = Field(min_length=32, max_length=32)
    next_path: str

    model_config = ConfigDict(extra="forbid")


class DemoFixtureCurrentResponse(BaseModel):
    schema_version: Literal[1] = 1
    fixture_version: Literal[1] = 1
    mode: Literal["technical_demo_fixture"] = "technical_demo_fixture"
    environment: Literal["non_production"] = "non_production"
    state: Literal["missing", "ready", "diverged"]
    can_bootstrap: bool
    preserved: bool
    project_id: str | None = Field(default=None, min_length=32, max_length=32)
    plan_id: str | None = Field(default=None, min_length=32, max_length=32)
    part_id: str | None = Field(default=None, min_length=32, max_length=32)
    chapter_id: str | None = Field(default=None, min_length=32, max_length=32)
    element_id: str | None = Field(default=None, min_length=32, max_length=32)
    assignment_id: str | None = Field(default=None, min_length=32, max_length=32)
    next_path: str | None = None
    recommended_action: Literal[
        "bootstrap_fixture",
        "open_fixture",
        "preserve_existing_fixture",
    ]

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_state_shape(self):
        anchors = (
            self.project_id,
            self.plan_id,
            self.part_id,
            self.chapter_id,
            self.element_id,
            self.assignment_id,
        )
        if self.state == "missing":
            if (
                not self.can_bootstrap
                or self.preserved
                or any(value is not None for value in anchors)
                or self.next_path is not None
                or self.recommended_action != "bootstrap_fixture"
            ):
                raise ValueError("invalid missing fixture shape")
        elif self.state == "ready":
            if (
                self.can_bootstrap
                or self.preserved
                or any(value is None for value in anchors)
                or self.next_path is None
                or self.recommended_action != "open_fixture"
            ):
                raise ValueError("invalid ready fixture shape")
        elif (
            self.can_bootstrap
            or not self.preserved
            or any(value is not None for value in anchors[1:])
            or self.next_path is not None
            or self.recommended_action != "preserve_existing_fixture"
        ):
            raise ValueError("invalid diverged fixture shape")
        return self
