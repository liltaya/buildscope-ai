"""Dependencies provided to HTTP routes"""

from typing import cast

from fastapi import Request

from buildscope.repositories.service import RepositoryService


def get_repository_service(request: Request) -> RepositoryService:
    """Return the app-owned service for the current request."""
    return cast(
        RepositoryService,
        request.app.state.repository_service,
    )
