"""Health-check API routes."""

from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel

from buildscope import version


class HealthResponse(BaseModel):
    """Response returned by the API health endpoint."""

    status: Literal["ok"]
    service: str
    version: str


router = APIRouter(
    tags=["system"],
)


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
)
def health() -> HealthResponse:
    """Return the current API health state."""
    return HealthResponse(
        status="ok",
        service="buildscope-api",
        version=version(),
    )
