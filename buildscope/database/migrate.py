"""Apply BuildScope's versioned database migrations."""

from pathlib import Path

from alembic.config import Config

from alembic import command


def upgrade_database(database_path: Path) -> None:
    """Upgrade one SQLite database to the latest migration."""
    config_path = Path(__file__).resolve().parents[2] / "alembic.ini"

    config = Config(str(config_path))

    config.set_main_option(
        "buildscope_database_path",
        str(database_path.expanduser().resolve()),
    )

    command.upgrade(config, "head")
