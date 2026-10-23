"""SQLAlchemy engine creation for BuildScope."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.pool import ConnectionPoolEntry


def _enable_sqlite_foreign_keys(
    dbapi_connection: sqlite3.Connection,
    _connection_record: ConnectionPoolEntry,
) -> None:
    """Enable SQLite foreign-key enforcement for one database connection."""
    previous_autocommit = dbapi_connection.autocommit

    dbapi_connection.autocommit = True

    try:
        cursor = dbapi_connection.cursor()

        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()
    finally:
        dbapi_connection.autocommit = previous_autocommit


def create_sqlite_engine(
    database_path: Path,
    *,
    echo: bool = False,
) -> Engine:
    """Create a configured SQLAlchemy SQLite engine."""
    resolved_path = database_path.expanduser().resolve()

    resolved_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    database_url = URL.create(
        drivername="sqlite+pysqlite",
        database=str(resolved_path),
    )

    engine = create_engine(
        database_url,
        echo=echo,
        connect_args={
            "autocommit": False,
        },
    )

    event.listen(
        engine,
        "connect",
        _enable_sqlite_foreign_keys,
    )

    return engine
