"""Tests for SQLAlchemy Session configuration."""

from pathlib import Path

from sqlalchemy import text

from buildscope.database import (
    create_session_factory,
    create_sqlite_engine,
)


def test_session_can_execute_query(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "buildscope.db"

    engine = create_sqlite_engine(database_path)

    session_factory = create_session_factory(engine)

    try:
        with session_factory() as session:
            result = session.scalar(text("SELECT 42"))

        assert result == 42
    finally:
        engine.dispose()


def test_session_rollback_discards_changes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "buildscope.db"

    engine = create_sqlite_engine(database_path)

    session_factory = create_session_factory(engine)

    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE example (
                    id INTEGER PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

        with session_factory() as session:
            session.execute(
                text(
                    """
                    INSERT INTO example (
                        id,
                        value
                    )
                    VALUES (
                        1,
                        'temporary'
                    )
                    """
                )
            )

            session.rollback()

        with session_factory() as session:
            count = session.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM example
                    """
                )
            )

        assert count == 0
    finally:
        engine.dispose()
