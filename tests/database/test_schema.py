"""Tests for database schema initialization."""

from pathlib import Path

from sqlalchemy import inspect

from buildscope.database import (
    create_schema,
    create_sqlite_engine,
)


def test_create_schema_creates_repositories_table(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "buildscope.db"

    engine = create_sqlite_engine(database_path)

    try:
        create_schema(engine)

        inspector = inspect(engine)

        assert inspector.get_table_names() == ["repositories"]
    finally:
        engine.dispose()


def test_repositories_table_has_expected_columns(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "buildscope.db"

    engine = create_sqlite_engine(database_path)

    try:
        create_schema(engine)

        inspector = inspect(engine)

        column_names = {column["name"] for column in inspector.get_columns("repositories")}

        assert column_names == {
            "id",
            "name",
            "local_path",
            "language",
            "build_system",
            "test_framework",
            "created_at",
        }
    finally:
        engine.dispose()


def test_repositories_local_path_is_unique(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "buildscope.db"

    engine = create_sqlite_engine(database_path)

    try:
        create_schema(engine)

        inspector = inspect(engine)

        constraints = inspector.get_unique_constraints("repositories")

        constrained_columns = {tuple(constraint["column_names"]) for constraint in constraints}

        assert ("local_path",) in constrained_columns
    finally:
        engine.dispose()
