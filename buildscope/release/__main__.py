"""Command line for release checks: python -m buildscope.release."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from buildscope.release.database import (
    DatabaseCheckError,
    DatabaseReport,
    backup_database,
    check_database,
    restore_test,
)
from buildscope.release.preflight import (
    changelog_section,
    project_version,
    run_preflight,
)


def _print_report(report: DatabaseReport) -> None:
    print(f"revision: {report.revision}")

    for name, count in sorted(report.tables.items()):
        print(f"  {name:<24} {count:>8}")


def _preflight(arguments: argparse.Namespace) -> int:
    findings = run_preflight(
        arguments.root.resolve(),
        tag=arguments.tag,
        wheel=arguments.wheel,
    )

    for finding in findings:
        status = "PASS" if finding.ok else "FAIL"
        print(f"{status}  {finding.name}: {finding.detail}")

    return 0 if all(finding.ok for finding in findings) else 1


def _db_check(arguments: argparse.Namespace) -> int:
    report = check_database(arguments.database)
    print(f"integrity_check: ok\nforeign_key_check: ok\npath: {report.path}")
    _print_report(report)

    return 0


def _backup(arguments: argparse.Namespace) -> int:
    result = backup_database(arguments.database, arguments.output)
    print(f"backup: {result.path}\nsha256: {result.sha256}")
    print(f"checksum file: {result.checksum_path}")
    _print_report(result.report)

    return 0


def _restore_test(arguments: argparse.Namespace) -> int:
    result = restore_test(arguments.database, arguments.backup)
    print("restore matches source: schema and row counts")
    _print_report(result.restored)

    return 0


def _notes(arguments: argparse.Namespace) -> int:
    root = arguments.root.resolve()
    version = arguments.version or project_version(root)
    section = changelog_section((root / "CHANGELOG.md").read_text(), version)

    if not section:
        print(f"FAIL  no changelog section for {version}")
        return 1

    print(section)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m buildscope.release",
        description="Release preflight and SQLite backup verification.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    preflight = commands.add_parser(
        "preflight",
        help="check the source tree and wheel before tagging",
    )
    preflight.add_argument("--root", type=Path, default=Path.cwd())
    preflight.add_argument(
        "--tag",
        help="release tag that must equal v<project.version>",
    )
    preflight.add_argument(
        "--wheel",
        type=Path,
        help="inspect this wheel instead of building one",
    )
    preflight.set_defaults(handler=_preflight)

    db_check = commands.add_parser(
        "db-check",
        help="run integrity_check and foreign_key_check",
    )
    db_check.add_argument("--database", type=Path, required=True)
    db_check.set_defaults(handler=_db_check)

    backup = commands.add_parser(
        "backup",
        help="copy a database with the backup API and verify the copy",
    )
    backup.add_argument("--database", type=Path, required=True)
    backup.add_argument("--output", type=Path, required=True)
    backup.set_defaults(handler=_backup)

    restore = commands.add_parser(
        "restore-test",
        help="restore a backup to a scratch file and compare it with the source",
    )
    restore.add_argument("--database", type=Path, required=True)
    restore.add_argument("--backup", type=Path, required=True)
    restore.set_defaults(handler=_restore_test)

    notes = commands.add_parser(
        "notes",
        help="print the changelog section used as release notes",
    )
    notes.add_argument("--root", type=Path, default=Path.cwd())
    notes.add_argument("--version", help="defaults to project.version")
    notes.set_defaults(handler=_notes)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    try:
        result: int = arguments.handler(arguments)
    except (DatabaseCheckError, FileExistsError, FileNotFoundError) as exc:
        print(f"FAIL  {exc}")
        return 1

    return result


if __name__ == "__main__":
    raise SystemExit(main())
