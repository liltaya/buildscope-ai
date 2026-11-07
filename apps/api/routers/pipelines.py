"""HTTP endpoints for versioned pipeline definitions"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from apps.api.dependencies import get_pipeline_service
from apps.api.schemas.pipelines import (
    PipelineCreateRequest,
    PipelineVersionResponse,
)
from buildscope.pipelines.loader import PipelineConfigError
from buildscope.pipelines.service import (
    PipelineNotFoundError,
    PipelineService,
    PipelineVersion,
    PipelineVersionConflictError,
)
from buildscope.repositories.errors import RepositoryNotFoundError

router = APIRouter(
    prefix="/repositories/{repository_id}/pipelines",
    tags=["pipelines"],
)

ServiceDependency = Annotated[
    PipelineService,
    Depends(get_pipeline_service),
]


@router.post(
    "",
    response_model=PipelineVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_pipeline_version(
    repository_id: UUID,
    payload: PipelineCreateRequest,
    service: ServiceDependency,
) -> PipelineVersion:
    """Validate and persist the next pipeline version."""
    try:
        return service.create_version(
            repository_id,
            payload.yaml,
        )

    except RepositoryNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except PipelineConfigError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except PipelineVersionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


@router.get(
    "/{pipeline_name}/versions",
    response_model=list[PipelineVersionResponse],
)
def list_pipeline_versions(
    repository_id: UUID,
    pipeline_name: str,
    service: ServiceDependency,
) -> list[PipelineVersion]:
    """List the versions of a named pipeline."""
    try:
        return service.list_versions(
            repository_id,
            pipeline_name,
        )

    except RepositoryNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc


@router.get(
    "/{pipeline_name}/versions/{version}",
    response_model=PipelineVersionResponse,
)
def get_pipeline_version(
    repository_id: UUID,
    pipeline_name: str,
    version: int,
    service: ServiceDependency,
) -> PipelineVersion:
    """Retrieve an exact historical version."""
    try:
        return service.get_version(
            repository_id,
            pipeline_name,
            version,
        )

    except (
        RepositoryNotFoundError,
        PipelineNotFoundError,
    ) as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc


@router.get(
    "/{pipeline_name}/latest",
    response_model=PipelineVersionResponse,
)
def get_latest_pipeline_version(
    repository_id: UUID,
    pipeline_name: str,
    service: ServiceDependency,
) -> PipelineVersion:
    """Retrieve the newest saved configuration."""
    try:
        return service.get_latest(
            repository_id,
            pipeline_name,
        )

    except (
        RepositoryNotFoundError,
        PipelineNotFoundError,
    ) as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
