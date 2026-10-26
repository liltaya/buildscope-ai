"""HTTP endpoints for registered repositories"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from apps.api.dependencies import get_repository_service
from apps.api.schemas.repositories import (
    RepositoryCreate,
    RepositoryResponse,
)
from buildscope.repositories.errors import (
    DuplicateRepositoryError,
    InvalidRepositoryError,
    RepositoryNotFoundError,
)
from buildscope.repositories.service import (
    RepositoryDetails,
    RepositoryService,
)

router = APIRouter(
    prefix="/repositories",
    tags=["repositories"],
)

ServiceDependency = Annotated[
    RepositoryService,
    Depends(get_repository_service),
]


@router.post(
    "",
    response_model=RepositoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_repository(
    payload: RepositoryCreate,
    service: ServiceDependency,
) -> RepositoryDetails:
    """Register an existing local directory."""
    try:
        return service.register_repository(
            name=payload.name,
            local_path=payload.local_path,
        )

    except InvalidRepositoryError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except DuplicateRepositoryError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


@router.get(
    "",
    response_model=list[RepositoryResponse],
)
def list_repositories(
    service: ServiceDependency,
) -> list[RepositoryDetails]:
    """Return all registered repositories."""
    return service.list_repositories()


@router.get(
    "/{repository_id}",
    response_model=RepositoryResponse,
)
def get_repository(
    repository_id: UUID,
    service: ServiceDependency,
) -> RepositoryDetails:
    """Return one repository by UUID."""
    try:
        return service.get_repository(repository_id)

    except RepositoryNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
