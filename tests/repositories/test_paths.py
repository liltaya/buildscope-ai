"""Tests for repository path utilities"""

from pathlib import Path

from buildscope.repositories import normalize_local_path


def test_normalize_local_path_returns_absolute_path(
    tmp_path: Path,
) -> None:
    repository_path = tmp_path / "project"

    normalized = normalize_local_path(repository_path)

    assert normalized == str(repository_path.resolve())


def test_normalize_local_path_resolves_parent_segments(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "project"

    input_path = tmp_path / "other" / ".." / "project"

    normalized = normalize_local_path(input_path)

    assert normalized == str(project_path.resolve())


def test_normalize_local_path_accepts_string(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "project"

    normalized = normalize_local_path(str(project_path))

    assert normalized == str(project_path.resolve())
