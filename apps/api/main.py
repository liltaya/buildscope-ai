"""FastAPI application factory and lifecycle"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from apps.api.routers.dashboard import router as dashboard_router
from apps.api.routers.health import router as health_router
from apps.api.routers.pipelines import router as pipeline_router
from apps.api.routers.predictions import router as prediction_router
from apps.api.routers.repositories import router as repository_router
from buildscope import version
from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)
from buildscope.database.migrate import upgrade_database
from buildscope.logging import configure_logging
from buildscope.pipelines.service import PipelineService
from buildscope.repositories.service import RepositoryService
from buildscope.settings import database_path as configured_database_path


@asynccontextmanager
async def lifespan(
    application: FastAPI,
) -> AsyncIterator[None]:
    """Upgrade and initialize application database resources."""
    logger = configure_logging()
    logger.info("BuildScope API starting")

    database_path: Path = application.state.database_path

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
        database_path if database_path is not None else configured_database_path()
    )

    application.include_router(health_router)
    application.include_router(repository_router)
    application.include_router(pipeline_router)
    application.include_router(prediction_router)
    application.include_router(dashboard_router)

    return application


app = create_app()
