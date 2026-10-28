"""HTTP schemas for versioned pipeline definitions"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from buildscope.pipelines.schema import PipelineDefinition


class PipelineCreateRequest(BaseModel):
    """YAML input for creating a new pipeline version."""

    yaml: str = Field(
        min_length=1,
        max_length=256 * 1024,
    )


class PipelineVersionResponse(BaseModel):
    """A saved and validated pipeline version."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    repository_id: UUID
    name: str
    version: int
    definition: PipelineDefinition
    created_at: datetime
