"""Safe, read-only Git repository inspection"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitInspectionError(Exception):
    """A Git inspection operation failed."""


class GitUnavailableError(GitInspectionError):
    """Git could not be started."""


class GitTimeoutError(GitInspectionError):
    """A Git operation took too long."""


@dataclass(frozen=True, slots=True)
class GitMetadata:
    is_git_repository: bool
    repository_root: str | None
    branch: str | None
    commit_hash: str | None
    detached_head: bool
    is_repository_root: bool
    warnings: tuple[str, ...]


def _run_git(
    directory: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    """Execute a fixed Git command without a shell."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

    try:
        return subprocess.run(
            ["git", "--no-optional-locks", *args],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=environment,
        )

    except FileNotFoundError as exc:
        raise GitUnavailableError("Git executable was not found.") from exc

    except subprocess.TimeoutExpired as exc:
        raise GitTimeoutError("Git inspection timed out.") from exc

    except OSError as exc:
        raise GitInspectionError(f"Failed to execute Git: {exc}") from exc


def inspect_git_repository(
    directory: Path,
) -> GitMetadata:
    """Read the current Git state of an existing directory."""
    try:
        path = directory.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("Repository path does not exist or cannot be resolved.") from exc

    if not path.is_dir():
        raise ValueError("Repository path must be a directory.")

    status = _run_git(
        path,
        "rev-parse",
        "--is-inside-work-tree",
    )

    if status.returncode != 0:
        if "not a git repository" not in status.stderr.lower():
            raise GitInspectionError(
                "Could not determine Git repository status: " + status.stderr.strip()[:300]
            )

    if status.returncode != 0 or status.stdout.strip() != "true":
        return GitMetadata(
            is_git_repository=False,
            repository_root=None,
            branch=None,
            commit_hash=None,
            detached_head=False,
            is_repository_root=False,
            warnings=("Directory is not inside a Git working tree.",),
        )

    root_result = _run_git(
        path,
        "rev-parse",
        "--show-toplevel",
    )

    if root_result.returncode != 0:
        raise GitInspectionError("Could not determine Git repository root.")

    root = Path(root_result.stdout.strip()).resolve()

    commit_result = _run_git(
        path,
        "rev-parse",
        "--verify",
        "--quiet",
        "HEAD",
    )

    if commit_result.returncode not in (0, 1):
        raise GitInspectionError("Could not determine current Git commit.")

    commit_hash = commit_result.stdout.strip() if commit_result.returncode == 0 else None

    branch_result = _run_git(
        path,
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
    )

    if branch_result.returncode not in (0, 1):
        raise GitInspectionError("Could not determine current Git branch.")

    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None

    detached = branch is None and commit_hash is not None

    warnings = ("Repository has no commits yet.",) if commit_hash is None else ()

    return GitMetadata(
        is_git_repository=True,
        repository_root=str(root),
        branch=branch,
        commit_hash=commit_hash,
        detached_head=detached,
        is_repository_root=(path == root),
        warnings=warnings,
    )
