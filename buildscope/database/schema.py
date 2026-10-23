"""Database schema initialization."""

from sqlalchemy.engine import Engine

from buildscope.database.base import Base


def create_schema(engine: Engine) -> None:
    """Create all currently registered SQLAlchemy tables."""
    Base.metadata.create_all(engine)
