"""Alembic environment for the local BuildScope SQLite database"""

from pathlib import Path

from alembic import context
from buildscope.database.base import Base
from buildscope.database.engine import create_sqlite_engine
from buildscope.pipelines.models import PipelineDefinitionRecord
from buildscope.pipelines.run_models import (
    PipelineRunRecord,
    StageRunRecord,
)
from buildscope.repositories.models import Repository

config = context.config

# Import all ORM models before accessing metadata.
_ = (
    Repository,
    PipelineDefinitionRecord,
    PipelineRunRecord,
    StageRunRecord,
)

target_metadata = Base.metadata


def run_migrations_online() -> None:
    """Apply migrations using the configured local SQLite engine."""
    database_path = Path(
        config.get_main_option(
            "buildscope_database_path",
            "data/buildscope.db",
        )
    )

    engine = create_sqlite_engine(database_path)

    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )

            with context.begin_transaction():
                context.run_migrations()

    finally:
        engine.dispose()


run_migrations_online()
