"""Read-only Git metadata inspection for local working trees"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitInspectionError(Exception):
    """Git metadata could not be inspected reliably."""


class GitUnavailableError(GitInspectionError):
    """The Git executable is unavailable."""


class GitTimeoutError(GitInspectionError):
    """A Git metadata command exceeded its timeout."""


@dataclass(frozen=True, slots=True)
class GitMetadata:
    """Current Git information for a local directory."""

    is_git_repository: bool
    repository_root: str | None
    branch: str | None
    commit_hash: str | None
    detached_head: bool
    is_repository_root: bool
    warnings: tuple[str, ...]


def _git(
    directory: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    """Run a fixed Git inspection command without a shell."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

    try:
        return subprocess.run(
            ["git", "--no-optional-locks", *arguments],
            cwd=directory,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
            env=environment,
        )

    except FileNotFoundError as exc:
        raise GitUnavailableError("Git executable was not found in PATH.") from exc

    except subprocess.TimeoutExpired as exc:
        raise GitTimeoutError("Git inspection exceeded the five-second limit.") from exc

    except OSError as exc:
        raise GitInspectionError(f"Could not start Git: {exc}") from exc


def inspect_git_repository(directory: Path) -> GitMetadata:
    """Inspect a local Git working tree and its current HEAD."""
    try:
        root_directory = directory.expanduser().resolve(strict=True)

    except (OSError, RuntimeError) as exc:
        raise ValueError("Git inspection path must exist and be accessible.") from exc

    if not root_directory.is_dir():
        raise ValueError("Git inspection path must be a directory.")

    inside = _git(
        root_directory,
        "rev-parse",
        "--is-inside-work-tree",
    )

    if inside.returncode != 0:
        if "not a git repository" in inside.stderr.lower():
            return GitMetadata(
                is_git_repository=False,
                repository_root=None,
                branch=None,
                commit_hash=None,
                detached_head=False,
                is_repository_root=False,
                warnings=("Directory is not inside a Git working tree.",),
            )

        raise GitInspectionError(
            "Git could not determine repository status: " + inside.stderr.strip()[:300]
        )

    if inside.stdout.strip() != "true":
        return GitMetadata(
            is_git_repository=False,
            repository_root=None,
            branch=None,
            commit_hash=None,
            detached_head=False,
            is_repository_root=False,
            warnings=("Directory is not inside a Git working tree.",),
        )

    top_level = _git(
        root_directory,
        "rev-parse",
        "--show-toplevel",
    )

    if top_level.returncode != 0 or not top_level.stdout.strip():
        raise GitInspectionError("Git could not find the working-tree root.")

    git_root = Path(top_level.stdout.strip()).resolve()

    head = _git(
        root_directory,
        "rev-parse",
        "--verify",
        "--quiet",
        "HEAD",
    )

    if head.returncode not in (0, 1):
        raise GitInspectionError("Git could not read the current HEAD commit.")

    commit_hash = head.stdout.strip() if head.returncode == 0 else None

    symbolic = _git(
        root_directory,
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
    )

    if symbolic.returncode not in (0, 1):
        raise GitInspectionError("Git could not determine the current branch.")

    branch = symbolic.stdout.strip() if symbolic.returncode == 0 else None

    detached_head = branch is None and commit_hash is not None

    warnings = ("Repository has no commits yet.",) if commit_hash is None else ()

    return GitMetadata(
        is_git_repository=True,
        repository_root=str(git_root),
        branch=branch,
        commit_hash=commit_hash,
        detached_head=detached_head,
        is_repository_root=root_directory == git_root,
        warnings=warnings,
    )
