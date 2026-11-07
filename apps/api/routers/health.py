"""Health and readiness API routes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from apps.api.security import require_local_request
from buildscope import version
from buildscope.database import create_sqlite_engine
from buildscope.database.migrate import head_revision


class HealthResponse(BaseModel):
    """Response returned by the API health endpoint."""

    status: Literal["ok"]
    service: str
    version: str


class ReadinessResponse(BaseModel):
    """Response returned once the database is usable."""

    status: Literal["ready"]


router = APIRouter(
    tags=["system"],
    dependencies=[Depends(require_local_request)],
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


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
)
def ready(request: Request) -> ReadinessResponse:
    """Report ready only when the database is at the packaged head revision."""
    engine = create_sqlite_engine(request.app.state.database_path)

    try:
        with engine.connect() as connection:
            current = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()

    except SQLAlchemyError as exc:
        # Details stay in the server log; the endpoint may be polled
        # by tooling that should not see filesystem paths.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from exc

    finally:
        engine.dispose()

    if current != head_revision():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database schema is not at the expected revision",
        )

    return ReadinessResponse(status="ready")
