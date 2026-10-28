"""FastAPI application factory and lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from apps.api.routes.health import router as health_router
from apps.api.routes.pipelines import router as pipeline_router
from apps.api.routes.repositories import router as repository_router
from buildscope import version
from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.database.migrate import upgrade_database
from buildscope.logging import configure_logging
from buildscope.pipelines.service import PipelineService
from buildscope.repositories.service import RepositoryService


@asynccontextmanager
async def lifespan(
    application: FastAPI,
) -> AsyncIterator[None]:
    """Upgrade and initialize application database resources."""
    logger = configure_logging()
    logger.info("BuildScope API starting")

    database_path: Path = application.state.database_path

    # Upgrade existing database tables before accepting requests.
    upgrade_database(database_path)

    engine = create_sqlite_engine(database_path)

    try:
        session_factory = create_session_factory(engine)

        application.state.repository_service = RepositoryService(session_factory)

        application.state.pipeline_service = PipelineService(session_factory)

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
    application.include_router(pipeline_router)

    return application


app = create_app()
