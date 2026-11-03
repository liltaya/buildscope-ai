"""Read a safe, non-executing Git snapshot for one repository"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class GitMetadataError(RuntimeError):
    """Git is unavailable or its metadata could not be read."""


@dataclass(frozen=True, slots=True)
class GitSnapshot:
    """Identity of a checkout before a pipeline starts."""

    commit_sha: str
    branch: str | None
    is_dirty: bool
    commit_at: datetime
    subject: str


def _git(
    root: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    """Run a read-only Git command without invoking a shell."""
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_TERMINAL_PROMPT"] = "0"

    try:
        return subprocess.run(
            ("git", "-C", str(root), *arguments),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=10,
            check=False,
            env=environment,
        )
    except (
        OSError,
        subprocess.TimeoutExpired,
    ) as exc:
        raise GitMetadataError(f"Could not inspect Git repository: {exc}") from exc


def _require_success(
    result: subprocess.CompletedProcess[str],
    action: str,
) -> str:
    """Return command output or raise a Git metadata error."""
    if result.returncode != 0:
        raise GitMetadataError(f"Cannot {action}: {result.stderr.strip() or 'Git command failed'}")

    return result.stdout.strip()


def capture_git_snapshot(
    repository_root: Path,
) -> GitSnapshot | None:
    """Capture HEAD, branch and local changes before execution.

    Non-Git directories return None.
    Git repositories without commits return None.
    Dirty snapshots cannot establish clean-commit regressions.
    """
    root = repository_root.expanduser().resolve(strict=True)

    if not root.is_dir():
        raise GitMetadataError("Repository root is not a directory")

    probe = _git(
        root,
        "rev-parse",
        "--is-inside-work-tree",
    )

    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return None

    # An initialized Git repository may not have any commits.
    # An unborn HEAD does not identify a valid commit.
    if (
        _git(
            root,
            "rev-parse",
            "--verify",
            "HEAD",
        ).returncode
        != 0
    ):
        return None

    summary = _require_success(
        _git(
            root,
            "log",
            "-1",
            "--format=%H%x00%cI%x00%s",
        ),
        "read HEAD commit",
    )

    parts = summary.split("\x00", 2)

    if len(parts) != 3:
        raise GitMetadataError("Unexpected Git commit metadata format")

    sha, timestamp, subject = parts

    if (
        re.fullmatch(
            r"(?:[0-9a-f]{40}|[0-9a-f]{64})",
            sha,
        )
        is None
    ):
        raise GitMetadataError("Invalid Git commit identifier")

    try:
        commit_at = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise GitMetadataError("Invalid Git commit timestamp") from exc

    if commit_at.tzinfo is None:
        raise GitMetadataError("Git commit timestamp has no timezone")

    branch_result = _git(
        root,
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
    )

    if branch_result.returncode == 0:
        branch: str | None = branch_result.stdout.strip()

    elif branch_result.returncode == 1:
        # Detached HEAD.
        branch = None

    else:
        raise GitMetadataError("Could not read Git branch")

    dirty_output = _require_success(
        _git(
            root,
            "status",
            "--porcelain=v1",
            "--untracked-files=normal",
        ),
        "inspect working tree",
    )

    return GitSnapshot(
        commit_sha=sha,
        branch=branch,
        is_dirty=bool(dirty_output),
        commit_at=commit_at,
        subject=subject[:512],
    )
