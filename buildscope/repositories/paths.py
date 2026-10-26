"""Filesystem path utilities for registered repositories."""

from pathlib import Path


def normalize_local_path(
    path: str | Path,
) -> str:
    """Return a canonical absolute representation of a local path."""
    return str(Path(path).expanduser().resolve(strict=False))
