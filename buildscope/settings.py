"""Process-wide configuration read from the environment."""

from __future__ import annotations

import os
from pathlib import Path

DATABASE_PATH_VARIABLE = "BUILDSCOPE_DATABASE_PATH"

DEFAULT_DATABASE_PATH = Path("data") / "buildscope.db"


def database_path() -> Path:
    """Return the configured SQLite database path, relative to the cwd."""
    configured = os.environ.get(DATABASE_PATH_VARIABLE)

    return Path(configured) if configured else DEFAULT_DATABASE_PATH
