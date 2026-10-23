"""SQLAlchemy session creation for BuildScope."""

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

SessionFactory = sessionmaker[Session]


def create_session_factory(
    engine: Engine,
) -> SessionFactory:
    """Create a SQLAlchemy Session factory bound to an engine."""
    return sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )
