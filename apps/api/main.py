"""FastAPI application factory and lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from apps.api.routes.health import router as health_router
from apps.api.routes.repositories import (
    router as repository_router,
)
from buildscope import version
from buildscope.database import (
    create_schema,
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.logging import configure_logging
from buildscope.repositories.service import RepositoryService


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Initialize database resources and clean them up."""
    logger = configure_logging()

    logger.info("BuildScope API starting")

    database_path: Path = application.state.database_path

    engine = create_sqlite_engine(database_path)

    try:
        create_schema(engine)

        application.state.repository_service = RepositoryService(create_session_factory(engine))

        yield

    finally:
        engine.dispose()

        logger.info("BuildScope API stopping")


def create_app(
    *,
    database_path: Path | None = None,
) -> FastAPI:
    """Create the local BuildScope API."""
    application = FastAPI(
        title="BuildScope AI",
        description=("Local software delivery and reliability intelligence platform."),
        version=version(),
        lifespan=lifespan,
    )

    application.state.database_path = (
        database_path if database_path is not None else Path.cwd() / "data" / "buildscope.db"
    )

    application.include_router(health_router)

    application.include_router(repository_router)

    return application


app = create_app()
