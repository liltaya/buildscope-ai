"""Tests for SQLite engine configuration."""

import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

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

        with pytest.raises(IntegrityError), engine.begin() as connection:
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


def test_read_then_write_does_not_deadlock_with_another_writer(
    tmp_path: Path,
) -> None:
    """A transaction that reads before writing must wait, not fail.

    With deferred transactions, the reader holds a shared lock while a
    second connection takes the reserved lock; SQLite then refuses the
    reader's upgrade immediately instead of honouring busy_timeout.
    """
    database_path = tmp_path / "contention.db"
    first = create_sqlite_engine(database_path)
    second = create_sqlite_engine(database_path)

    try:
        with first.begin() as connection:
            connection.execute(text("CREATE TABLE counter (value INTEGER)"))
            connection.execute(text("INSERT INTO counter VALUES (0)"))

        reader_started = threading.Event()
        errors: list[OperationalError] = []

        def other_writer() -> None:
            reader_started.wait(5)

            try:
                with second.begin() as connection:
                    connection.execute(text("UPDATE counter SET value = value + 10"))
            except OperationalError as exc:
                errors.append(exc)

        thread = threading.Thread(target=other_writer)
        thread.start()

        with first.begin() as connection:
            connection.execute(text("SELECT value FROM counter")).scalar_one()
            reader_started.set()
            time.sleep(0.3)
            connection.execute(text("UPDATE counter SET value = value + 1"))

        thread.join(10)

        assert not errors

        with first.connect() as connection:
            total = connection.execute(text("SELECT value FROM counter")).scalar_one()

        assert total == 11

    finally:
        first.dispose()
        second.dispose()
