"""HTTP request and response schemas for repositories."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RepositoryCreate(BaseModel):
    """Input required to register a repository."""

    name: str = Field(
        min_length=1,
        max_length=255,
    )

    local_path: str = Field(
        min_length=1,
        max_length=4096,
    )


class RepositoryResponse(BaseModel):
    """Public representation of a registered repository."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    local_path: str
    language: str | None
    build_system: str | None
    test_framework: str | None
    created_at: datetime


class ProjectDetectionResponse(BaseModel):
    """Detected language, tools, and supporting evidence."""

    model_config = ConfigDict(from_attributes=True)

    language: str | None
    build_system: str | None
    test_framework: str | None

    evidence: tuple[str, ...]
    warnings: tuple[str, ...]


class RepositoryDetectionResponse(BaseModel):
    """Response returned after repository detection."""

    model_config = ConfigDict(from_attributes=True)

    repository: RepositoryResponse
    detection: ProjectDetectionResponse


class GitMetadataResponse(BaseModel):
    """Current Git metadata for a registered repository."""

    model_config = ConfigDict(from_attributes=True)

    is_git_repository: bool
    repository_root: str | None
    branch: str | None
    commit_hash: str | None
    detached_head: bool
    is_repository_root: bool
    warnings: tuple[str, ...]
