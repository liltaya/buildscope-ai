"""Verify migration 0010 preserves existing execution attempts"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_migration_preserves_existing_attempt(
    tmp_path: Path,
) -> None:
    engine = create_engine("sqlite:///" + str(tmp_path / "old.db"))

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE stage_attempts (id TEXT PRIMARY KEY)"))

        connection.execute(text("INSERT INTO stage_attempts (id) VALUES ('existing')"))

        migration_path = (
            Path(__file__).resolve().parents[2]
            / "alembic"
            / "versions"
            / "0010_benchmarks.py"
        )

        spec = importlib.util.spec_from_file_location(
            "revision_0010",
            migration_path,
        )

        assert spec is not None
        assert spec.loader is not None

        module = importlib.util.module_from_spec(spec)

        spec.loader.exec_module(module)

        context = MigrationContext.configure(connection)

        vars(module)["op"] = Operations(context)
        module.upgrade()

        existing = connection.execute(
            text("SELECT id FROM stage_attempts")
        ).scalar_one()

        assert existing == "existing"

    tables = set(inspect(engine).get_table_names())

    assert {
        "stage_attempts",
        "benchmark_reports",
        "benchmark_samples",
    } <= tables

    engine.dispose()
