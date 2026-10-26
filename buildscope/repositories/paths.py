"""Canonicalize and validate local repository paths."""

from pathlib import Path

from buildscope.repositories.errors import InvalidRepositoryError


def normalize_local_path(path: str | Path) -> str:
    """Normalize a path without requiring it to exist."""
    return str(Path(path).expanduser().resolve(strict=False))


def validate_repository_path(path: str | Path) -> str:
    """Require an existing directory and return its canonical path."""
    if not str(path).strip():
        raise InvalidRepositoryError("Repository path must not be blank.")

    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InvalidRepositoryError("Repository path must exist and be accessible.") from exc

    if not resolved.is_dir():
        raise InvalidRepositoryError("Repository path must be an existing directory.")

    return str(resolved)
