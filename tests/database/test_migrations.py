"""Alembic migrations build the schema the ORM models describe"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from buildscope.database import create_sqlite_engine
from buildscope.database.migrate import head_revision, upgrade_database
from buildscope.database.models import MODELS, full_metadata

ROOT = Path(__file__).resolve().parents[2]


def test_upgrade_empty_database_reaches_head(tmp_path: Path) -> None:
    database = tmp_path / "empty.db"

    upgrade_database(database)

    with sqlite3.connect(database) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchall()

    assert revision == [(head_revision(),)]


def test_every_model_table_exists_after_upgrade(tmp_path: Path) -> None:
    database = tmp_path / "tables.db"
    upgrade_database(database)
    engine = create_sqlite_engine(database)

    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    expected = {model.__tablename__ for model in MODELS}

    assert expected <= tables
    assert tables - expected == {"alembic_version"}


def test_models_and_migrations_do_not_drift(tmp_path: Path) -> None:
    database = tmp_path / "drift.db"
    upgrade_database(database)
    engine = create_sqlite_engine(database)

    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True},
            )
            differences = compare_metadata(context, full_metadata())
    finally:
        engine.dispose()

    assert differences == []


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "twice.db"

    upgrade_database(database)
    upgrade_database(database)

    with sqlite3.connect(database) as connection:
        count = connection.execute("SELECT COUNT(*) FROM alembic_version").fetchone()

    assert count == (1,)


def test_alembic_cli_upgrade_from_repository_root(tmp_path: Path) -> None:
    # The checked-in alembic.ini must keep working for contributors who
    # run Alembic directly rather than through the application.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ROOT / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "data" / "buildscope.db").is_file()


def test_foreign_keys_enforced_on_every_connection(tmp_path: Path) -> None:
    database = tmp_path / "fk.db"
    upgrade_database(database)
    engine = create_sqlite_engine(database)

    try:
        for _ in range(3):
            with engine.connect() as connection:
                enabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
                assert enabled == 1
    finally:
        engine.dispose()
