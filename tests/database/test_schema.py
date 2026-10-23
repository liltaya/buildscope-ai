"""Tests for database schema initialization."""

from pathlib import Path

from sqlalchemy import inspect

from buildscope.database import (
    create_schema,
    create_sqlite_engine,
)


def test_create_schema_succeeds_without_models(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "buildscope.db"

    engine = create_sqlite_engine(database_path)

    try:
        create_schema(engine)

        inspector = inspect(engine)

        assert inspector.get_table_names() == []
    finally:
        engine.dispose()
