"""Command-line entry point for BuildScope AI."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from ipaddress import ip_address
from pathlib import Path

from buildscope import version
from buildscope.settings import DATABASE_PATH_VARIABLE, database_path

LOOPBACK_NAMES = frozenset({"localhost"})


class CommandError(Exception):
    """Raised when a command cannot run with the given arguments."""


def _is_loopback(host: str) -> bool:
    """Return whether host names only the local machine."""
    if host in LOOPBACK_NAMES:
        return True

    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _serve(arguments: argparse.Namespace) -> int:
    """Run the API and dashboard on a loopback interface."""
    if not _is_loopback(arguments.host):
        # The API has no authentication; binding elsewhere would expose
        # repository registration to the network.
        raise CommandError(f"Refusing to bind to non-loopback host {arguments.host}")

    import uvicorn

    os.environ[DATABASE_PATH_VARIABLE] = str(arguments.database)
    os.environ.setdefault("BUILDSCOPE_ENABLE_LOCAL_DASHBOARD", "1")

    uvicorn.run(
        "apps.api.main:app",
        host=arguments.host,
        port=arguments.port,
    )

    return 0


def _upgrade(arguments: argparse.Namespace) -> int:
    """Apply all pending migrations to a database."""
    from buildscope.database.migrate import upgrade_database

    upgrade_database(arguments.database)
    print(f"Database at head: {arguments.database}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="buildscope",
        description=(
            "Local pipeline runner with regression, flaky-test and benchmark analysis."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {version()}",
    )

    commands = parser.add_subparsers(
        dest="command",
        metavar="COMMAND",
    )

    serve = commands.add_parser(
        "serve",
        help="run the API and dashboard on localhost",
    )
    serve.add_argument(
        "--host",
        default="127.0.0.1",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=8000,
    )
    serve.add_argument(
        "--database",
        type=Path,
        default=database_path(),
    )
    serve.set_defaults(handler=_serve)

    database = commands.add_parser(
        "db",
        help="manage the SQLite database",
    )
    database_commands = database.add_subparsers(
        dest="database_command",
        metavar="ACTION",
        required=True,
    )
    upgrade = database_commands.add_parser(
        "upgrade",
        help="apply pending schema migrations",
    )
    upgrade.add_argument(
        "--database",
        type=Path,
        default=database_path(),
    )
    upgrade.set_defaults(handler=_upgrade)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the BuildScope command-line entry point."""
    parser = build_parser()
    arguments = parser.parse_args(argv)

    if arguments.command is None:
        parser.print_help()
        return 0

    try:
        result: int = arguments.handler(arguments)
    except CommandError as exc:
        parser.error(str(exc))

    return result


if __name__ == "__main__":
    raise SystemExit(main())
