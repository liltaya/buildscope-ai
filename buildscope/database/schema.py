"""Legacy schema creation for isolated repository tests.

Production database upgrades are managed by Alembic.
"""

from sqlalchemy import Table
from sqlalchemy.engine import Engine

from buildscope.database.base import Base


def create_schema(engine: Engine) -> None:
    """Create only the original repository table for legacy tests."""
    from buildscope.repositories.models import Repository

    repository_table = Base.metadata.tables[Repository.__tablename__]

    assert isinstance(repository_table, Table)

    repository_table.create(
        bind=engine,
        checkfirst=True,
    )
