"""SQLAlchemy engine creation for BuildScope"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import URL, Connection, Engine, create_engine, event
from sqlalchemy.pool import ConnectionPoolEntry


def _configure_connection(
    dbapi_connection: sqlite3.Connection,
    _connection_record: ConnectionPoolEntry,
) -> None:
    """Enable foreign keys and lock waiting for one database connection"""

    cursor = dbapi_connection.cursor()

    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
    finally:
        cursor.close()


def _begin_immediate(connection: Connection) -> None:
    """Take the write lock when a transaction starts.

    A deferred transaction that reads and then writes must upgrade its
    lock; if another connection already holds the write lock, SQLite
    fails the upgrade at once rather than waiting, to avoid deadlock.
    Locking up front turns that case into an ordinary busy_timeout wait.
    """
    connection.exec_driver_sql("BEGIN IMMEDIATE")


def create_sqlite_engine(
    database_path: Path,
    *,
    echo: bool = False,
) -> Engine:
    """Create a configured SQLAlchemy SQLite engine"""

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
            # Let SQLAlchemy, not the driver, issue BEGIN so that every
            # transaction can use BEGIN IMMEDIATE.
            "isolation_level": None,
            "timeout": 30.0,
        },
    )

    event.listen(
        engine,
        "connect",
        _configure_connection,
    )

    event.listen(
        engine,
        "begin",
        _begin_immediate,
    )

    return engine
