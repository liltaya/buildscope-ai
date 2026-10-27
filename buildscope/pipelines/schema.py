"""Pydantic models for declarative pipeline configuration"""

from __future__ import annotations

from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class StageDefinition(BaseModel):
    """A named stage and its execution configuration."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    name: str = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$",
    )

    command: tuple[str, ...] = Field(
        min_length=1,
        max_length=64,
    )

    depends_on: tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )

    timeout_seconds: int = Field(
        default=300,
        ge=1,
        le=3600,
        strict=True,
    )

    retry_count: int = Field(
        default=0,
        ge=0,
        le=10,
        strict=True,
    )

    @field_validator("command")
    @classmethod
    def check_command_parts(
        cls,
        parts: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not part.strip() or "\x00" in part for part in parts):
            raise ValueError("command arguments must be nonblank and contain no NUL bytes")

        return parts

    @field_validator("depends_on")
    @classmethod
    def check_dependencies(
        cls,
        names: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(names) != len(set(names)):
            raise ValueError("depends_on contains duplicate names")

        if any(not name.strip() for name in names):
            raise ValueError("depends_on cannot contain blank names")

        return names


class PipelineDefinition(BaseModel):
    """A pipeline containing ordered stage declarations."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    name: str = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$",
    )

    stages: tuple[StageDefinition, ...] = Field(
        min_length=1,
        max_length=100,
    )

    @model_validator(mode="after")
    def check_unique_stage_names(self) -> Self:
        names = [stage.name for stage in self.stages]

        if len(names) != len(set(names)):
            raise ValueError("stage names must be unique")

        return self
