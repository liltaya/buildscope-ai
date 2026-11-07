"""Integrity checks, verified backups and restore rehearsals"""

from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest

from buildscope.database import create_session_factory, create_sqlite_engine
from buildscope.database.migrate import head_revision, upgrade_database
from buildscope.pipelines.service import PipelineService
from buildscope.release.__main__ import main
from buildscope.release.database import (
    DatabaseCheckError,
    backup_database,
    check_database,
    file_sha256,
    restore_test,
)
from buildscope.repositories.models import Repository


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "live.db"
    upgrade_database(path)
    engine = create_sqlite_engine(path)

    try:
        factory = create_session_factory(engine)

        for name in ("alpha", "beta"):
            workspace = tmp_path / name
            workspace.mkdir()

            with factory.begin() as session:
                repository = Repository(name=name, local_path=str(workspace))
                session.add(repository)
                session.flush()
                repository_id = repository.id

            PipelineService(factory).create_version(
                repository_id,
                "name: ci\nstages:\n  - name: test\n    command: [pytest]\n",
            )
    finally:
        engine.dispose()

    return path


def test_db_check_reports_revision_and_counts(database: Path) -> None:
    report = check_database(database)

    assert report.revision == head_revision()
    assert report.tables["repositories"] == 2
    assert report.tables["pipeline_definitions"] == 2


def test_db_check_does_not_create_missing_database(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"

    with pytest.raises(FileNotFoundError):
        check_database(missing)

    assert not missing.exists()


def test_db_check_detects_orphan_foreign_keys(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        # Foreign keys are off by default in a raw connection, which is
        # exactly how orphans get written by tools outside BuildScope.
        connection.execute(
            "UPDATE pipeline_definitions SET repository_id = ? "
            "WHERE rowid = (SELECT MIN(rowid) FROM pipeline_definitions)",
            ("0" * 32,),
        )

    with pytest.raises(DatabaseCheckError, match="foreign_key_check"):
        check_database(database)


def test_db_check_detects_page_corruption(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        root_page = connection.execute(
            "SELECT rootpage FROM sqlite_master WHERE name = 'pipeline_definitions'"
        ).fetchone()[0]

    raw = bytearray(database.read_bytes())
    # Damage the table's root b-tree page after its 8-byte header. The
    # file header is untouched, so SQLite opens the file normally and
    # integrity_check has to find the damage itself.
    start = (root_page - 1) * page_size + 8
    raw[start : start + 64] = b"\xff" * 64
    database.write_bytes(bytes(raw))

    with pytest.raises((DatabaseCheckError, sqlite3.DatabaseError)):
        check_database(database)


def test_db_check_rejects_old_revision(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE alembic_version SET version_num = '0009'")

    with pytest.raises(DatabaseCheckError, match="revision"):
        check_database(database)


def test_backup_writes_verified_copy_and_checksum(
    database: Path,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "backups" / "buildscope.db"

    result = backup_database(database, destination)

    assert result.path == destination
    assert result.sha256 == file_sha256(destination)
    assert result.checksum_path.read_text() == (
        f"{result.sha256}  {destination.name}\n"
    )
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert result.report.tables == check_database(database).tables
    assert check_database(destination).revision == head_revision()
    assert sorted(path.name for path in destination.parent.iterdir()) == [
        "buildscope.db",
        "buildscope.db.sha256",
    ]


def test_backup_is_consistent_while_a_writer_is_open(
    database: Path,
    tmp_path: Path,
) -> None:
    writer = sqlite3.connect(database, isolation_level=None)

    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE repositories SET name = 'uncommitted'")

        result = backup_database(database, tmp_path / "snapshot.db")
    finally:
        writer.execute("ROLLBACK")
        writer.close()

    with sqlite3.connect(result.path) as copy:
        names = {row[0] for row in copy.execute("SELECT name FROM repositories")}

    assert names == {"alpha", "beta"}


def test_backup_refuses_to_overwrite(database: Path, tmp_path: Path) -> None:
    destination = tmp_path / "once.db"
    backup_database(database, destination)
    before = destination.read_bytes()

    with pytest.raises(FileExistsError):
        backup_database(database, destination)

    assert destination.read_bytes() == before


def test_backup_refuses_source_as_destination(database: Path) -> None:
    with pytest.raises(DatabaseCheckError, match="differ"):
        backup_database(database, database)


def test_restore_test_matches_source(database: Path, tmp_path: Path) -> None:
    backup = backup_database(database, tmp_path / "backup.db").path

    result = restore_test(database, backup)

    assert result.restored.tables == result.source.tables
    assert result.restored.revision == head_revision()


def test_restore_test_rejects_tampered_backup(database: Path, tmp_path: Path) -> None:
    backup = backup_database(database, tmp_path / "backup.db").path
    backup.chmod(0o600)

    with sqlite3.connect(backup) as connection:
        connection.execute("DELETE FROM pipeline_definitions")

    with pytest.raises(DatabaseCheckError, match="Checksum mismatch"):
        restore_test(database, backup)


def test_restore_test_reports_rows_written_after_backup(
    database: Path,
    tmp_path: Path,
) -> None:
    backup = backup_database(database, tmp_path / "backup.db").path

    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM pipeline_definitions")

    with pytest.raises(DatabaseCheckError, match="pipeline_definitions"):
        restore_test(database, backup)


def test_release_cli_round_trip(
    database: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backup = tmp_path / "cli-backup.db"

    assert main(["db-check", "--database", str(database)]) == 0
    assert main(["backup", "--database", str(database), "--output", str(backup)]) == 0
    assert (
        main(["restore-test", "--database", str(database), "--backup", str(backup)])
        == 0
    )

    output = capsys.readouterr().out
    assert "integrity_check: ok" in output
    assert "sha256:" in output
    assert "restore matches source" in output

    assert main(["backup", "--database", str(database), "--output", str(backup)]) == 1
