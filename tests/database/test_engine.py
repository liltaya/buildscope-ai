"""Tests for SQLite engine configuration."""

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from buildscope.database import create_sqlite_engine


def test_engine_connects_to_sqlite(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "database" / "buildscope.db"

    engine = create_sqlite_engine(database_path)

    try:
        with engine.connect() as connection:
            result = connection.scalar(text("SELECT 1"))

        assert result == 1
        assert database_path.exists()
    finally:
        engine.dispose()


def test_engine_creates_parent_directory(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "nested" / "directory" / "buildscope.db"

    assert not database_path.parent.exists()

    engine = create_sqlite_engine(database_path)

    try:
        with engine.connect():
            pass

        assert database_path.parent.exists()
        assert database_path.exists()
    finally:
        engine.dispose()


def test_foreign_keys_are_enabled(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "buildscope.db"

    engine = create_sqlite_engine(database_path)

    try:
        with engine.connect() as connection:
            foreign_keys_enabled = connection.scalar(text("PRAGMA foreign_keys"))

        assert foreign_keys_enabled == 1
    finally:
        engine.dispose()


def test_foreign_key_constraints_are_enforced(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "buildscope.db"

    engine = create_sqlite_engine(database_path)

    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE parent (
                    id INTEGER PRIMARY KEY
                )
                """
            )

            connection.exec_driver_sql(
                """
                CREATE TABLE child (
                    id INTEGER PRIMARY KEY,
                    parent_id INTEGER NOT NULL,
                    FOREIGN KEY(parent_id)
                        REFERENCES parent(id)
                )
                """
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    """
                    INSERT INTO child (
                        id,
                        parent_id
                    )
                    VALUES (1, 999)
                    """
                )
    finally:
        engine.dispose()
