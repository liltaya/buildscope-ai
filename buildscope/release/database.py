"""SQLite integrity checks, verified backups and restore rehearsals."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from buildscope.database.migrate import head_revision


class DatabaseCheckError(RuntimeError):
    """A database failed an integrity, schema or checksum check."""


@dataclass(frozen=True, slots=True)
class DatabaseReport:
    path: Path
    revision: str | None
    tables: dict[str, int]


@dataclass(frozen=True, slots=True)
class BackupResult:
    path: Path
    checksum_path: Path
    sha256: str
    report: DatabaseReport


@dataclass(frozen=True, slots=True)
class RestoreResult:
    source: DatabaseReport
    restored: DatabaseReport


@contextmanager
def _read_only(path: Path) -> Iterator[sqlite3.Connection]:
    """Open an existing database without creating or modifying it."""
    resolved = path.expanduser().resolve(strict=True)

    if not resolved.is_file():
        raise DatabaseCheckError(f"Not a database file: {resolved}")

    with closing(
        sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        yield connection


def _user_tables(connection: sqlite3.Connection) -> list[str]:
    return [
        name
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _schema(connection: sqlite3.Connection) -> list[tuple[str, str, str | None]]:
    return list(
        connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        )
    )


def _inspect(connection: sqlite3.Connection, path: Path) -> DatabaseReport:
    integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]

    if integrity != ["ok"]:
        raise DatabaseCheckError(
            f"integrity_check failed for {path}: {'; '.join(integrity[:5])}"
        )

    violations = connection.execute("PRAGMA foreign_key_check").fetchall()

    if violations:
        tables = sorted({row[0] for row in violations})
        raise DatabaseCheckError(
            f"foreign_key_check found {len(violations)} orphan rows in {tables}"
        )

    tables = _user_tables(connection)

    revision = None

    if "alembic_version" in tables:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        revision = row[0] if row else None

    counts = {
        # Table names come from sqlite_master, not from user input.
        name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        for name in tables
    }

    return DatabaseReport(path=path, revision=revision, tables=counts)


def check_database(path: Path, *, require_head: bool = True) -> DatabaseReport:
    """Run integrity and foreign-key checks, and confirm the schema revision."""
    with _read_only(path) as connection:
        report = _inspect(connection, path)

    if require_head and report.revision != head_revision():
        raise DatabaseCheckError(
            f"Schema revision {report.revision!r} is not {head_revision()!r}"
        )

    return report


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


def backup_database(source: Path, destination: Path) -> BackupResult:
    """Copy a live database with the SQLite backup API and verify the copy."""
    source = source.expanduser().resolve(strict=True)
    destination = destination.expanduser().resolve()
    checksum_path = destination.with_name(destination.name + ".sha256")

    if destination == source:
        raise DatabaseCheckError("Backup destination must differ from the source")

    for existing in (destination, checksum_path):
        if existing.exists():
            raise FileExistsError(f"Refusing to overwrite {existing}")

    destination.parent.mkdir(parents=True, exist_ok=True)

    # Write beside the destination and link into place only after the
    # copy verifies, so a failed backup never leaves a plausible file.
    descriptor, staging_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    os.close(descriptor)
    staging = Path(staging_name)
    staging.chmod(0o600)

    try:
        with (
            _read_only(source) as reader,
            closing(sqlite3.connect(staging)) as writer,
        ):
            # backup() takes a consistent snapshot even while workers write.
            reader.backup(writer)

        report = check_database(staging, require_head=False)
        digest = file_sha256(staging)

        os.link(staging, destination)

    finally:
        staging.unlink(missing_ok=True)

    checksum_path.write_text(f"{digest}  {destination.name}\n")
    checksum_path.chmod(0o600)

    return BackupResult(
        path=destination,
        checksum_path=checksum_path,
        sha256=digest,
        report=DatabaseReport(
            path=destination,
            revision=report.revision,
            tables=report.tables,
        ),
    )


def verify_checksum(backup: Path) -> str:
    """Compare a backup against the sha256 file written beside it."""
    checksum_path = backup.with_name(backup.name + ".sha256")

    try:
        expected, name = checksum_path.read_text().split()
    except (OSError, ValueError) as exc:
        raise DatabaseCheckError(f"Unreadable checksum file {checksum_path}") from exc

    actual = file_sha256(backup)

    if name != backup.name or actual != expected:
        raise DatabaseCheckError(f"Checksum mismatch for {backup}")

    return actual


def restore_test(source: Path, backup: Path) -> RestoreResult:
    """Restore a backup into a scratch file and compare it with the source.

    Nothing is written next to the source, so this is safe to run while
    the application is using it.
    """
    verify_checksum(backup)

    with tempfile.TemporaryDirectory(prefix="buildscope-restore-") as scratch:
        restored_path = Path(scratch) / "restored.db"

        with (
            _read_only(backup) as reader,
            closing(sqlite3.connect(restored_path)) as writer,
        ):
            reader.backup(writer)

        with (
            _read_only(source) as original,
            _read_only(restored_path) as restored,
        ):
            source_report = _inspect(original, source)
            restored_report = _inspect(restored, restored_path)
            source_schema = _schema(original)
            restored_schema = _schema(restored)

    if restored_schema != source_schema:
        raise DatabaseCheckError("Restored schema differs from the source")

    if restored_report.tables != source_report.tables:
        changed = sorted(
            name
            for name in source_report.tables.keys() | restored_report.tables.keys()
            if source_report.tables.get(name) != restored_report.tables.get(name)
        )
        raise DatabaseCheckError(f"Row counts differ for tables: {changed}")

    return RestoreResult(source=source_report, restored=restored_report)
