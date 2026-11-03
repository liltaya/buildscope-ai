"""Database utilities for BuildScope AI."""

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from buildscope.database.base import Base
from buildscope.database.engine import create_sqlite_engine


def create_session_factory(
    engine: Engine,
) -> sessionmaker[Session]:
    """Create a SQLAlchemy session factory."""
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )


def create_schema(engine: Engine) -> None:
    """Create the legacy repository schema."""
    from buildscope.database.schema import (
        create_schema as _create_schema,
    )

    _create_schema(engine)


__all__ = [
    "Base",
    "create_sqlite_engine",
    "create_session_factory",
    "create_schema",
]
