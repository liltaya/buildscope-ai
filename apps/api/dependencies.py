"""Dependencies provided to HTTP routes"""

from typing import cast

from fastapi import Request

from buildscope.pipelines.service import PipelineService
from buildscope.repositories.service import RepositoryService


def get_pipeline_service(request: Request) -> PipelineService:
    """Return the application's configured pipeline service."""
    return cast(
        PipelineService,
        request.app.state.pipeline_service,
    )


def get_repository_service(request: Request) -> RepositoryService:
    """Return the app-owned service for the current request."""
    return cast(
        RepositoryService,
        request.app.state.repository_service,
    )
