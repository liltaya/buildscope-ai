"""Database schema initialization."""

from sqlalchemy.engine import Engine

from buildscope.database.base import Base


def _load_models() -> None:
    """Import ORM models so they register with SQLAlchemy metadata."""
    from buildscope.repositories.models import Repository

    _ = Repository


def create_schema(
    engine: Engine,
) -> None:
    """Create all currently registered SQLAlchemy tables."""
    _load_models()

    Base.metadata.create_all(engine)
