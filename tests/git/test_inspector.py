"""Behavioral tests for local Git metadata inspection"""

import re
import subprocess
from pathlib import Path

import pytest

from buildscope.git import (
    GitTimeoutError,
    GitUnavailableError,
    inspect_git_repository,
)


def git(directory: Path, *args: str) -> str:
    """Run a Git command in an isolated test repository."""
    result = subprocess.run(
        ["git", *args],
        cwd=directory,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Create a Git repository without any commits."""
    root = tmp_path / "project"
    root.mkdir()

    git(root, "init", "-q")
    git(root, "symbolic-ref", "HEAD", "refs/heads/main")

    return root


@pytest.fixture
def committed_repo(repo: Path) -> Path:
    """Create a repository containing one commit."""
    (repo / "README.md").write_text("Example\n")

    git(repo, "add", "README.md")

    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.com",
        "commit",
        "-q",
        "-m",
        "initial",
    )

    return repo


def test_branch_and_commit(committed_repo: Path) -> None:
    result = inspect_git_repository(committed_repo)

    assert result.is_git_repository
    assert result.repository_root == str(committed_repo.resolve())
    assert result.branch == "main"

    assert result.commit_hash == git(
        committed_repo,
        "rev-parse",
        "HEAD",
    )

    assert re.fullmatch(
        r"[0-9a-f]{40}|[0-9a-f]{64}",
        result.commit_hash or "",
    )

    assert result.is_repository_root
    assert not result.detached_head
    assert not result.warnings


def test_nested_path(committed_repo: Path) -> None:
    nested = committed_repo / "src" / "module"
    nested.mkdir(parents=True)

    result = inspect_git_repository(nested)

    assert result.is_git_repository
    assert not result.is_repository_root
    assert result.repository_root == str(committed_repo)


def test_unborn_branch(repo: Path) -> None:
    result = inspect_git_repository(repo)

    assert result.is_git_repository
    assert result.branch == "main"
    assert result.commit_hash is None
    assert not result.detached_head
    assert "no commits" in result.warnings[0]


def test_detached_head(committed_repo: Path) -> None:
    git(
        committed_repo,
        "checkout",
        "--detach",
        "HEAD",
    )

    result = inspect_git_repository(committed_repo)

    assert result.is_git_repository
    assert result.branch is None
    assert result.detached_head
    assert result.commit_hash is not None


def test_not_a_repo(tmp_path: Path) -> None:
    result = inspect_git_repository(tmp_path)

    assert not result.is_git_repository
    assert result.repository_root is None
    assert result.commit_hash is None
    assert result.warnings


def test_read_only(committed_repo: Path) -> None:
    before = git(
        committed_repo,
        "rev-parse",
        "HEAD",
    )

    inspect_git_repository(committed_repo)

    after = git(
        committed_repo,
        "rev-parse",
        "HEAD",
    )

    assert before == after


def test_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        inspect_git_repository(tmp_path / "gone")


def test_reject_file(tmp_path: Path) -> None:
    file = tmp_path / "a.txt"
    file.write_text("hello")

    with pytest.raises(ValueError):
        inspect_git_repository(file)


def test_git_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git missing")

    monkeypatch.setattr(
        "buildscope.git.inspector.subprocess.run",
        missing,
    )

    with pytest.raises(GitUnavailableError):
        inspect_git_repository(tmp_path)


def test_git_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(
            ["git"],
            5,
        )

    monkeypatch.setattr(
        "buildscope.git.inspector.subprocess.run",
        timeout,
    )

    with pytest.raises(GitTimeoutError):
        inspect_git_repository(tmp_path)
