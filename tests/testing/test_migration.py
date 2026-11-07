"""Verify migration 0009 preserves earlier execution history"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_migration_preserves_existing_attempt(
    tmp_path: Path,
) -> None:
    """Verify test-report migration preserves existing attempts."""

    engine = create_engine("sqlite:///" + str(tmp_path / "before.db"))

    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE stage_attempts (id TEXT PRIMARY KEY)")
            )

            connection.execute(
                text("INSERT INTO stage_attempts (id) VALUES ('existing')")
            )

            migration_path = (
                Path(__file__).resolve().parents[2]
                / "buildscope"
                / "database"
                / "migrations"
                / "versions"
                / "v0009_test_reports.py"
            )

            spec = importlib.util.spec_from_file_location(
                "revision_0009",
                str(migration_path),
            )

            assert spec is not None
            assert spec.loader is not None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            context = MigrationContext.configure(connection)
            module.op = Operations(context)  # type: ignore[attr-defined]

            module.upgrade()

            existing_id = connection.execute(
                text("SELECT id FROM stage_attempts")
            ).scalar_one()

            assert existing_id == "existing"

        tables = set(inspect(engine).get_table_names())

        assert {
            "stage_attempts",
            "test_reports",
            "test_cases",
        } <= tables

    finally:
        engine.dispose()
