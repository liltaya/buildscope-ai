"""Verify the Git-history migration preserves existing runs"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_adds_nullable_git_columns_preserves_rows(
    tmp_path: Path,
) -> None:
    """Verify migration adds Git fields without losing existing runs."""

    engine = create_engine("sqlite:///" + str(tmp_path / "old.db"))

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE pipeline_runs "
                    "(id TEXT PRIMARY KEY, "
                    "repository_id TEXT NOT NULL, "
                    "status TEXT NOT NULL)"
                )
            )

            connection.execute(
                text(
                    "INSERT INTO pipeline_runs VALUES ('original', 'repo', 'SUCCEEDED')"
                )
            )

            context = MigrationContext.configure(connection)

            migration_path = (
                Path(__file__).resolve().parents[2]
                / "buildscope"
                / "database"
                / "migrations"
                / "versions"
                / "v0008_git_run_history.py"
            )

            spec = importlib.util.spec_from_file_location(
                "revision_0008",
                str(migration_path),
            )

            assert spec is not None
            assert spec.loader is not None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            module.op = Operations(context)  # type: ignore[attr-defined]

            module.upgrade()

            row = (
                connection.execute(text("SELECT * FROM pipeline_runs")).mappings().one()
            )

            assert row["id"] == "original"
            assert row["git_commit_sha"] is None

        columns = {
            column["name"] for column in inspect(engine).get_columns("pipeline_runs")
        }

        assert {
            "git_commit_sha",
            "git_branch",
            "git_is_dirty",
            "git_commit_at",
            "git_commit_subject",
        } <= columns

        indexes = {
            index["name"] for index in inspect(engine).get_indexes("pipeline_runs")
        }

        assert "ix_pipeline_runs_repo_branch_commit" in indexes

    finally:
        engine.dispose()
