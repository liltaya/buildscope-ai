"""Database-backed readiness endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)

router = APIRouter(tags=["health"])


@router.get("/ready")
def readiness(request: Request) -> dict[str, str]:
    """Verify that the application database is accessible."""
    database_path = request.app.state.database_path

    try:
        engine = create_sqlite_engine(database_path)

        try:
            session_factory = create_session_factory(engine)

            with session_factory() as session:
                session.execute(text("SELECT 1"))

        finally:
            engine.dispose()

    except (OSError, SQLAlchemyError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        ) from exc

    return {
        "status": "ready",
        "database": "connected",
    }
