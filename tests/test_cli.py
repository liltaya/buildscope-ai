"""Tests for the buildscope command-line entry point."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from buildscope.__main__ import main


def test_help_lists_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "serve" in output
    assert "db" in output


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--version"])

    assert capsys.readouterr().out.strip() == "buildscope 0.1.0"


def test_db_upgrade_creates_schema(tmp_path: Path) -> None:
    database = tmp_path / "cli.db"

    assert main(["db", "upgrade", "--database", str(database)]) == 0

    with sqlite3.connect(database) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()

    assert revision == ("0010_benchmarks",)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com"])
def test_serve_refuses_non_loopback_host(host: str) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["serve", "--host", host])

    assert exit_info.value.code == 2
