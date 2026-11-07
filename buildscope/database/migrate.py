"""Apply BuildScope's versioned database migrations."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

MIGRATIONS_PATH = Path(__file__).resolve().parent / "migrations"


def migration_config(database_path: Path) -> Config:
    """Build an Alembic config that works from a source tree or a wheel."""
    # Installed wheels carry no alembic.ini, so the script location is
    # resolved from the package rather than the working directory.
    config = Config()

    config.set_main_option(
        "script_location",
        str(MIGRATIONS_PATH),
    )
    config.set_main_option(
        "path_separator",
        "os",
    )
    config.set_main_option(
        "buildscope_database_path",
        str(database_path.expanduser().resolve()),
    )

    return config


def head_revision() -> str:
    """Return the newest migration revision shipped with this package."""
    script = ScriptDirectory.from_config(migration_config(Path("unused.db")))
    head = script.get_current_head()

    if head is None:
        raise RuntimeError("No migration revisions are available")

    return head


def upgrade_database(database_path: Path) -> None:
    """Upgrade one SQLite database to the latest migration."""
    command.upgrade(
        migration_config(database_path),
        "head",
    )
