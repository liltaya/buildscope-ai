"""FastAPI application entry point for BuildScope AI."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.routes.health import router as health_router
from buildscope import version
from buildscope.logging import configure_logging


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Manage application startup and shutdown."""
    logger = configure_logging()

    logger.info("BuildScope API starting")

    yield

    logger.info("BuildScope API stopping")


def create_app() -> FastAPI:
    """Create and configure the BuildScope FastAPI application."""
    application = FastAPI(
        title="BuildScope AI",
        description=("Local software delivery and reliability intelligence platform."),
        version=version(),
        lifespan=lifespan,
    )

    application.include_router(
        health_router,
    )

    return application


app = create_app()
